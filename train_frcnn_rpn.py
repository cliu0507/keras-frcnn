from __future__ import division
import random
import pprint
import sys
import time
import numpy as np
from optparse import OptionParser
import pickle

from keras import backend as K
from keras.optimizers import Adam, SGD, RMSprop
from keras.layers import Input
from keras.models import Model
from keras_frcnn import config, data_generators
from keras_frcnn import losses as losses
import keras_frcnn.roi_helpers as roi_helpers
from keras.utils import generic_utils
from keras_frcnn.pascal_voc_parser_chang import get_data
from keras_frcnn import resnet as nn
import tensorflow as tf
'''
python train_frcnn_rpn.py  --path "/Users/cliu/Documents/Github/keras-frcnn-orginal/data" 

'''

import linecache
import sys
def PrintException():
	exc_type, exc_obj, tb = sys.exc_info()
	f = tb.tb_frame
	lineno = tb.tb_lineno
	filename = f.f_code.co_filename
	linecache.checkcache(filename)
	line = linecache.getline(filename, lineno, f.f_globals)
	print('EXCEPTION IN ({}, LINE {} "{}"): {}'.format(filename, lineno, line.strip(), exc_obj))



sys.setrecursionlimit(40000)

parser = OptionParser()

parser.add_option("-p", "--path", dest="train_path", help="Path to training data.")
parser.add_option("-n", "--num_rois", type="int", dest="num_rois", help="Number of RoIs to process at once.", default=32)
parser.add_option("--hf", dest="horizontal_flips", help="Augment with horizontal flips in training. (Default=false).", action="store_true", default=False)
parser.add_option("--vf", dest="vertical_flips", help="Augment with vertical flips in training. (Default=false).", action="store_true", default=False)
parser.add_option("--rot", "--rot_90", dest="rot_90", help="Augment with 90 degree rotations in training. (Default=false).",
				  action="store_true", default=False)
parser.add_option("--num_epochs", type="int", dest="num_epochs", help="Number of epochs.", default=2000)
parser.add_option("--config_filename", dest="config_filename", help=
				"Location to store all the metadata related to the training (to be used when testing).",
				default="config.pickle")
parser.add_option("--output_weight_path", dest="output_weight_path", help="Output path for weights.", default='./model_frcnn_rpn.hdf5')
parser.add_option("--input_weight_path", dest="input_weight_path", help="Input path for weights. If not specified, will try to load default weights provided by keras.")

(options, args) = parser.parse_args()

if not options.train_path:   # if filename is not given
	parser.error('Error: path to training data must be specified. Pass --path to command line')

# pass the settings from the command line, and persist them in the config object
C = config.Config()

C.use_horizontal_flips = bool(options.horizontal_flips)
C.use_vertical_flips = bool(options.vertical_flips)
C.rot_90 = bool(options.rot_90)
C.model_path = options.output_weight_path
C.num_rois = int(options.num_rois)
C.network = 'resnet50'

# check if weight path was passed via command line
if options.input_weight_path:
	C.base_net_weights = options.input_weight_path
else:
	# set the path to weights based on backend and model
	C.base_net_weights = nn.get_weight_path()

all_imgs, classes_count, class_mapping = get_data(options.train_path)
#print(all_imgs)
print(classes_count)
print(class_mapping)


if 'bg' not in classes_count:
	classes_count['bg'] = 0
	class_mapping['bg'] = len(class_mapping)

C.class_mapping = class_mapping

inv_map = {v: k for k, v in class_mapping.items()}

print('Training images per class:')
pprint.pprint(classes_count)
print('Num classes (including bg) = {}'.format(len(classes_count)))


config_output_filename = options.config_filename

with open(config_output_filename, 'wb') as config_f:
	pickle.dump(C,config_f)
	print('Config has been written to {}, and can be loaded when testing to ensure correct results'.format(config_output_filename))


random.shuffle(all_imgs)

#train_imgs = [s for s in all_imgs if s['imageset'] == 'trainval']
train_imgs = [s for s in all_imgs]

#val_imgs = [s for s in all_imgs if s['imageset'] == 'test']
val_imgs = []

print('Num train samples {}'.format(len(train_imgs)))
print('Num val samples {}'.format(len(val_imgs)))

data_gen_train = data_generators.get_anchor_gt(train_imgs, classes_count, C, nn.get_img_output_length, 'tf', mode='train')
data_gen_val = data_generators.get_anchor_gt(val_imgs, classes_count, C, nn.get_img_output_length,'tf', mode='val')

'''
next(data_gen_train) = np.copy(x_img), [np.copy(y_rpn_cls), np.copy(y_rpn_regr)], img_data_aug
np.copy(x_img)

img_data_aug -- raw image with aug (no change to width and height)
x_img -- resized image with aug (minimum side is 600 pixel)
y_rpn_cls -- [1,featuremap_height,featuremap_width, 2 * number of anchors] - include gt and rp anchor
y_rpn_regr -- [1,featuremap_height,featuremap_width, 2 * number of anchors * 4] - include gt and rp anchor
'''

'''
for item in data_gen_train:
	print("item length:",len(item))
	print("Next Image:")
'''

#Since we are using tensorflow, so channel last
img_input = Input(shape=(None,None,3)) # Note input doesn't need batchsize(3-D tensor), however, img_input will have batchsize in first dimension(4-D tensor)
roi_input = Input(shape=(None,4)) # shape=(num_rois,4)
#img_input.shape = [batch,resized_height,resized_width,3], minimum side to be 600 pixel
print("the shape of img_input INPUT tensor", tf.keras.backend.shape(img_input))

# define the base network (resnet here, can be VGG, Inception, etc)
shared_layers = nn.nn_base(img_input, trainable=True) # shared_layers.shape = [batch,featuremap_height,featuremap_weight,1024]

# define the RPN, built on the base layers
num_anchors = len(C.anchor_box_scales) * len(C.anchor_box_ratios)
rpn = nn.rpn(shared_layers, num_anchors)
# rpn = [x_class, x_regr, base_layers]
# x_class.shape = [batch, featuremap_height, featuremap_weight,num_anchor]
# x_regr.shape = [batch, featuremap_height, featuremap_weight,num_anchor * 4 ]
# base_layers = img_input ([batch,featuremap_height, featuremap_weight, 3])

classifier = nn.classifier(shared_layers, roi_input, C.num_rois, nb_classes=len(classes_count), trainable=True)
#classifier = [out_class, out_regr]
#out_class.shape = [batch, nb_classes]
#out_class.shape = [batch, 4 * (nb_classes-1)]

model_rpn = Model(img_input, rpn[:2])
model_classifier = Model([img_input, roi_input], classifier)

# this is a model that holds both the RPN and the classifier, used to load/save weights for the models
model_all = Model([img_input, roi_input], rpn[:2] + classifier)

try:
	print('loading weights from {}'.format(C.base_net_weights))
	model_rpn.load_weights(C.base_net_weights, by_name=True)
	model_classifier.load_weights(C.base_net_weights, by_name=True)
except:
	print('Could not load pretrained model weights. Weights can be found in the keras application folder \
		https://github.com/fchollet/keras/tree/master/keras/applications')

optimizer = Adam(lr=1e-5)
optimizer_classifier = Adam(lr=1e-5)
model_rpn.compile(optimizer=optimizer, loss=[losses.rpn_loss_cls(num_anchors), losses.rpn_loss_regr(num_anchors)])
model_classifier.compile(optimizer=optimizer_classifier,
						 loss=[losses.class_loss_cls, losses.class_loss_regr(len(classes_count) - 1)],
						 metrics={'dense_class_{}'.format(len(classes_count)): 'accuracy'})
model_all.compile(optimizer='sgd', loss='mae')

epoch_length = 500
num_epochs = int(options.num_epochs)
iter_num = 0

losses = np.zeros((epoch_length, 5))
rpn_accuracy_rpn_monitor = []
rpn_accuracy_for_epoch = []
start_time = time.time()

best_loss = np.Inf

class_mapping_inv = {v: k for k, v in class_mapping.items()}
print('Starting training')

vis = True

for epoch_num in range(num_epochs):
	
	progbar = generic_utils.Progbar(epoch_length)
	print('Epoch {}/{}'.format(epoch_num + 1, num_epochs))
	
	#only train rpn:
	losses = np.zeros((epoch_length, 2))
	while True:
		try:
			'''
			if len(rpn_accuracy_rpn_monitor) == epoch_length and C.verbose:
				mean_overlapping_bboxes = float(sum(rpn_accuracy_rpn_monitor)) / len(rpn_accuracy_rpn_monitor)
				rpn_accuracy_rpn_monitor = []
				print('Average number of overlapping bounding boxes from RPN = {} for {} previous iterations'.format(
					mean_overlapping_bboxes, epoch_length))
				if mean_overlapping_bboxes == 0:
					print(
						'RPN is not producing bounding boxes that overlap the ground truth boxes. Check RPN settings or keep training.')
			
			
			X.shape --- (1,new_height,new_width,3)
			Y[0] --- [1,featuremap_height,featuremap_width, 2 * number of anchors]  - include gt and rp anchor
			Y[1] --- [1,featuremap_height,featuremap_width, 2 * number of anchors * 4] include gt and rp anchor
			img_data --- raw image with aug (no change to width and height)

			'''
			X, Y, img_data = next(data_gen_train)
			
			loss_rpn = model_rpn.train_on_batch(X, Y)
			losses[iter_num, 0] = loss_rpn[1]
			losses[iter_num, 1] = loss_rpn[2]
			iter_num += 1
			#print(X.shape)
			progbar.update(iter_num, [('rpn_cls', np.mean(losses[:iter_num, 0])), ('rpn_regr', np.mean(losses[:iter_num, 1]))])
			#print("\n")
			
			if iter_num == epoch_length:
				iter_num = 0
				loss_rpn_cls = np.mean(losses[:, 0])
				loss_rpn_regr = np.mean(losses[:, 1])
				curr_loss = loss_rpn_cls + loss_rpn_regr
				if curr_loss < best_loss:
					print('Total loss decreased from {} to {}, saving weights'.format(best_loss, curr_loss))
					best_loss = curr_loss
					model_rpn.save_weights("./model_frcnn_rpn.hdf5")
				break

			#above debug only
			#something add to network
			'''
			#P_rpn = model_rpn.predict_on_batch(X)
			#P_rpn[0].shape= [batch, featuremap_height, featuremap_weight,num_anchor]
			#P_rpn[1].shape= [batch, featuremap_height, featuremap_weight,num_anchor * 4 ]

			R = roi_helpers.rpn_to_roi(P_rpn[0], P_rpn[1], C, K.image_dim_ordering(), use_regr=True, overlap_thresh=0.7,
									   max_boxes=300)
			X2, Y1, Y2, IouS = roi_helpers.calc_iou(R, img_data, C, class_mapping)
			
			neg_samples = np.where(Y1[0, :, -1] == 1)  # check if it is background, last element
			pos_samples = np.where(Y1[0, :, -1] == 0)
			'''



			'''
			P_rpn = model_rpn.predict_on_batch(X)
			#import pickle
			#pickle.dump(P_rpn,open("test.pk",w))
			#P_rpn[0].shape= [batch, featuremap_height, featuremap_weight,num_anchor]
			#P_rpn[1].shape= [batch, featuremap_height, featuremap_weight,num_anchor * 4 ]
			R = roi_helpers.rpn_to_roi(P_rpn[0], P_rpn[1], C, K.image_dim_ordering(), use_regr=True, overlap_thresh=0.7,
									   max_boxes=300)
			
			# R.shape = [num_rois, 4] but it is (x1,y1,x2,y2) in last dimension(need to use existing code of Non-Maximum Suppression)
			# note: calc_iou converts from (x1,y1,x2,y2) to (x,y,w,h) format
			X2, Y1, Y2, IouS = roi_helpers.calc_iou(R, img_data, C, class_mapping)
			#X2.shape = [batch,num_rois,4]
			#Y1.shape = [batch,num_rois,class_mapping]
			#Y2.shape = [num_rois,class_mapping * 8] -- regressor
			
			if X2 is None:
				rpn_accuracy_rpn_monitor.append(0)
				rpn_accuracy_for_epoch.append(0)
				continue
			
			neg_samples = np.where(Y1[0, :, -1] == 1) # check if it is background, last element
			pos_samples = np.where(Y1[0, :, -1] == 0)
			
			if len(neg_samples) > 0:
				neg_samples = neg_samples[0]
			else:
				neg_samples = []
			
			if len(pos_samples) > 0:
				pos_samples = pos_samples[0]
			else:
				pos_samples = []
			
			rpn_accuracy_rpn_monitor.append(len(pos_samples))
			rpn_accuracy_for_epoch.append((len(pos_samples)))
			
			if C.num_rois > 1:
				if len(pos_samples) < C.num_rois // 2:
					selected_pos_samples = pos_samples.tolist()
				else:
					selected_pos_samples = np.random.choice(pos_samples, C.num_rois // 2, replace=False).tolist()
				try:
					selected_neg_samples = np.random.choice(neg_samples, C.num_rois - len(selected_pos_samples),
															replace=False).tolist()
				except:
					selected_neg_samples = np.random.choice(neg_samples, C.num_rois - len(selected_pos_samples),
															replace=True).tolist()
				
				sel_samples = selected_pos_samples + selected_neg_samples
			else:
				# in the extreme case where num_rois = 1, we pick a random pos or neg sample
				selected_pos_samples = pos_samples.tolist()
				selected_neg_samples = neg_samples.tolist()
				if np.random.randint(0, 2):
					sel_samples = random.choice(neg_samples)
				else:
					sel_samples = random.choice(pos_samples)
			
			#sel_samples.shape = [C.num_rois] - usually 4 per image
			# X.shape = [batch,resized_height,resized_width,3]
			# X2.shape = [batch,num_rois,4]
			# Y1.shape = [batch,num_rois,class_mapping]
			# Y2.shape = [num_rois,class_mapping * 8]
			# X2[:, sel_samples, :] select only C.num_rois(4) rois
			loss_class = model_classifier.train_on_batch([X, X2[:, sel_samples, :]],
														 [Y1[:, sel_samples, :], Y2[:, sel_samples, :]])
			
			losses[iter_num, 0] = loss_rpn[1]
			losses[iter_num, 1] = loss_rpn[2]
			
			losses[iter_num, 2] = loss_class[1]
			losses[iter_num, 3] = loss_class[2]
			losses[iter_num, 4] = loss_class[3]
			
			iter_num += 1
			print(X.shape)
			progbar.update(iter_num,
						   [('rpn_cls', losses[iter_num-1, 0]), ('rpn_regr', losses[iter_num-1, 1]),
							('detector_cls', losses[iter_num-1, 2]),
							('detector_regr', losses[iter_num-1, 3])])
			print("\n")
			if iter_num == epoch_length:
				loss_rpn_cls = np.mean(losses[:, 0])
				loss_rpn_regr = np.mean(losses[:, 1])
				loss_class_cls = np.mean(losses[:, 2])
				loss_class_regr = np.mean(losses[:, 3])
				class_acc = np.mean(losses[:, 4])
				
				#Average valid overapping rp with groundtruth bbox
				mean_overlapping_bboxes = float(sum(rpn_accuracy_for_epoch)) / len(rpn_accuracy_for_epoch)
				rpn_accuracy_for_epoch = []
				
				if C.verbose:
					print('Mean number of bounding boxes from RPN overlapping ground truth boxes: {}'.format(
						mean_overlapping_bboxes))
					print('Classifier accuracy for bounding boxes from RPN: {}'.format(class_acc))
					print('Loss RPN classifier(average): {}'.format(loss_rpn_cls))
					print('Loss RPN regression(average): {}'.format(loss_rpn_regr))
					print('Loss Detector classifier(average): {}'.format(loss_class_cls))
					print('Loss Detector regression(average): {}'.format(loss_class_regr))


					print('Elapsed time: {}'.format(time.time() - start_time))
				
				curr_loss = loss_rpn_cls + loss_rpn_regr + loss_class_cls + loss_class_regr
				iter_num = 0
				start_time = time.time()
				
				if curr_loss < best_loss:
					if C.verbose:
						print('Total loss decreased from {} to {}, saving weights'.format(best_loss, curr_loss))
					best_loss = curr_loss
					model_all.save_weights(C.model_path)
				
				break
			'''
		except Exception as e:
			print(e)
			continue

print('Training complete, exiting.')