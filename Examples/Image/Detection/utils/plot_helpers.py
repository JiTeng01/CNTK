# Copyright (c) Microsoft. All rights reserved.

# Licensed under the MIT license. See LICENSE.md file in the project root
# for full license information.
# ==============================================================================

from __future__ import print_function
from builtins import str
import sys, os, time
import numpy as np
from easydict import EasyDict
from builtins import range
import copy, textwrap
from PIL import Image, ImageFont, ImageDraw
from PIL.ExifTags import TAGS
from matplotlib.pyplot import imsave
import cntk
from cntk import input_variable, Axis
from utils.nms_wrapper import apply_nms_to_single_image_results
from utils.cntk_helpers import regress_rois
import cv2 # pip install opencv-python

available_font = "arial.ttf"
try:
    dummy = ImageFont.truetype(available_font, 16)
except:
    available_font = "FreeMono.ttf"


####################################
# Visualize results
####################################
def load_resize_and_pad(image_path, width, height, pad_value=114):
    if "@" in image_path:
        print("WARNING: zipped image archives are not supported for visualizing results.")
        exit(0)

    img = cv2.imread(image_path)
    img_width = len(img[0])
    img_height = len(img)
    scale_w = img_width > img_height
    target_w = width
    target_h = height

    if scale_w:
        target_h = int(np.round(img_height * float(width) / float(img_width)))
    else:
        target_w = int(np.round(img_width * float(height) / float(img_height)))

    resized = cv2.resize(img, (target_w, target_h), 0, 0, interpolation=cv2.INTER_NEAREST)

    top = int(max(0, np.round((height - target_h) / 2)))
    left = int(max(0, np.round((width - target_w) / 2)))
    bottom = height - top - target_h
    right = width - left - target_w
    resized_with_pad = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                          cv2.BORDER_CONSTANT, value=[pad_value, pad_value, pad_value])

    # transpose(2,0,1) converts the image to the HWC format which CNTK accepts
    model_arg_rep = np.ascontiguousarray(np.array(resized_with_pad, dtype=np.float32).transpose(2, 0, 1))

    dims = (width, height, target_w, target_h, img_width, img_height)
    return resized_with_pad, model_arg_rep, dims

def visualize_detections(img_path, roi_coords, roi_labels, roi_scores,
                         pad_width, pad_height, classes,
                         draw_negative_rois = False, decision_threshold = 0.0):
    # read and resize image
    imgWidth, imgHeight = imWidthHeight(img_path)
    scale = 800.0 / max(imgWidth, imgHeight)
    imgHeight = int(imgHeight * scale)
    imgWidth = int(imgWidth * scale)
    if imgWidth > imgHeight:
        h_border = 0
        v_border = int((imgWidth - imgHeight)/2)
    else:
        h_border = int((imgHeight - imgWidth)/2)
        v_border = 0

    PAD_COLOR = [103, 116, 123] # [114, 114, 114]
    cv_img = cv2.imread(img_path)
    rgb_img = cv2.cvtColor(cv_img,cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb_img, (imgWidth, imgHeight), interpolation=cv2.INTER_NEAREST)
    result_img = cv2.copyMakeBorder(resized,v_border,v_border,h_border,h_border,cv2.BORDER_CONSTANT,value=PAD_COLOR)
    rect_scale = 800 / pad_width

    assert(len(roi_labels) == len(roi_coords))
    if roi_scores is not None:
        assert(len(roi_labels) == len(roi_scores))
        minScore = min(roi_scores)
        if minScore > decision_threshold:
            decision_threshold = minScore * 0.5

    # draw multiple times to avoid occlusions
    for iter in range(0,3):
        for roiIndex in range(len(roi_coords)):
            label = roi_labels[roiIndex]
            if roi_scores is not None:
                score = roi_scores[roiIndex]
                if decision_threshold and score < decision_threshold:
                    label = 0

            # init drawing parameters
            thickness = 1
            if label == 0:
                color = (255, 0, 0)
            else:
                color = getColorsPalette()[label]

            rect = [(rect_scale * i) for i in roi_coords[roiIndex]]
            rect[0] = int(max(0, min(pad_width, rect[0])))
            rect[1] = int(max(0, min(pad_height, rect[1])))
            rect[2] = int(max(0, min(pad_width, rect[2])))
            rect[3] = int(max(0, min(pad_height, rect[3])))

            # draw in higher iterations only the detections
            if iter == 0 and draw_negative_rois:
                drawRectangles(result_img, [rect], color=color, thickness=thickness)
            elif iter==1 and label > 0:
                thickness = 4
                drawRectangles(result_img, [rect], color=color, thickness=thickness)
            elif iter == 2 and label > 0:
                font = ImageFont.truetype(available_font, 18)
                text = classes[label]
                if roi_scores is not None:
                    text += "(" + str(round(score, 2)) + ")"
                result_img = drawText(result_img, (rect[0],rect[1]), text, color = (255,255,255), font = font, colorBackground=color)
    return result_img

def eval_and_plot_faster_rcnn(eval_model, num_images_to_plot, results_base_path, cfg):
    test_map_file = cfg["CNTK"].TEST_MAP_FILE
    img_shape = (cfg["CNTK"].NUM_CHANNELS, cfg["CNTK"].IMAGE_HEIGHT, cfg["CNTK"].IMAGE_WIDTH)
    classes = cfg["CNTK"].CLASSES
    drawNegativeRois = cfg["CNTK"].DRAW_NEGATIVE_ROIS
    bgrPlotThreshold = cfg["CNTK"].RESULTS_BGR_PLOT_THRESHOLD

    # get image paths
    with open(test_map_file) as f:
        content = f.readlines()
    img_base_path = os.path.dirname(os.path.abspath(test_map_file))
    img_file_names = [os.path.join(img_base_path, x.split('\t')[1]) for x in content]

    # prepare model
    image_input = input_variable(img_shape, dynamic_axes=[Axis.default_batch_axis()], name=cfg["CNTK"].FEATURE_NODE_NAME)
    dims_input = input_variable((1,6), dynamic_axes=[Axis.default_batch_axis()], name='dims_input')
    frcn_eval = eval_model(image_input, dims_input)

    #dims_input_const = cntk.constant([image_width, image_height, image_width, image_height, image_width, image_height], (1, 6))
    print("Plotting results from Faster R-CNN model for %s images." % num_images_to_plot)
    for i in range(0, num_images_to_plot):
        imgPath = img_file_names[i]

        # evaluate single image
        _, cntk_img_input, dims = load_resize_and_pad(imgPath, img_shape[2], img_shape[1])

        dims_input = np.array(dims, dtype=np.float32)
        dims_input.shape = (1,) + dims_input.shape
        output = frcn_eval.eval({frcn_eval.arguments[0]: [cntk_img_input], frcn_eval.arguments[1]: dims_input})

        out_dict = dict([(k.name, k) for k in output])
        out_cls_pred = output[out_dict['cls_pred']][0]
        out_rpn_rois = output[out_dict['rpn_rois']][0]
        out_bbox_regr = output[out_dict['bbox_regr']][0]

        labels = out_cls_pred.argmax(axis=1)
        scores = out_cls_pred.max(axis=1)

        if cfg["CNTK"].DRAW_UNREGRESSED_ROIS:
            # plot results without final regression
            imgDebug = visualize_detections(imgPath, out_rpn_rois, labels, scores,
                                            img_shape[2], img_shape[1], classes,
                                            draw_negative_rois=drawNegativeRois,
                                            decision_threshold=bgrPlotThreshold)
            imsave("{}/{}_{}".format(results_base_path, i, os.path.basename(imgPath)), imgDebug)

        # apply regression and nms to bbox coordinates
        regressed_rois = regress_rois(out_rpn_rois, out_bbox_regr, labels, dims)

        nmsKeepIndices = apply_nms_to_single_image_results(regressed_rois, labels, scores,
                                                           use_gpu_nms=cfg.USE_GPU_NMS,
                                                           device_id=cfg.GPU_ID,
                                                           nms_threshold=cfg["CNTK"].RESULTS_NMS_THRESHOLD,
                                                           conf_threshold=cfg["CNTK"].RESULTS_NMS_CONF_THRESHOLD)

        filtered_bboxes = regressed_rois[nmsKeepIndices]
        filtered_labels = labels[nmsKeepIndices]
        filtered_scores = scores[nmsKeepIndices]

        img = visualize_detections(imgPath, filtered_bboxes, filtered_labels, filtered_scores,
                                   img_shape[2], img_shape[1], classes,
                                   draw_negative_rois=drawNegativeRois,
                                   decision_threshold=bgrPlotThreshold)
        imsave("{}/{}_regr_{}".format(results_base_path, i, os.path.basename(imgPath)), img)


####################################
# helper library
####################################

def imread(img_path, boThrowErrorIfExifRotationTagSet = True):
    if not os.path.exists(img_path):
        print("ERROR: image path does not exist.")
        error

    rotation = rotationFromExifTag(img_path)
    if boThrowErrorIfExifRotationTagSet and rotation != 0:
        print ("Error: exif roation tag set, image needs to be rotated by %d degrees." % rotation)
    img = cv2.imread(img_path)
    if img is None:
        print ("ERROR: cannot load image " + img_path)
        error
    if rotation != 0:
        img = imrotate(img, -90).copy()  # got this error occassionally without copy "TypeError: Layout of the output array img is incompatible with cv::Mat"
    return img

def rotationFromExifTag(img_path):
    TAGSinverted = {v: k for k, v in TAGS.items()}
    orientationExifId = TAGSinverted['Orientation']
    try:
        imageExifTags = Image.open(img_path)._getexif()
    except:
        imageExifTags = None

    # rotate the image if orientation exif tag is present
    rotation = 0
    if imageExifTags != None and orientationExifId != None and orientationExifId in imageExifTags:
        orientation = imageExifTags[orientationExifId]
        # print ("orientation = " + str(imageExifTags[orientationExifId]))
        if orientation == 1 or orientation == 0:
            rotation = 0 # no need to do anything
        elif orientation == 6:
            rotation = -90
        elif orientation == 8:
            rotation = 90
        else:
            print ("ERROR: orientation = " + str(orientation) + " not_supported!")
            error
    return rotation

def imwrite(img, img_path):
    cv2.imwrite(img_path, img)

def imresize(img, scale, interpolation = cv2.INTER_LINEAR):
    return cv2.resize(img, (0,0), fx=scale, fy=scale, interpolation=interpolation)

def imresizeMaxDim(img, maxDim, boUpscale = False, interpolation = cv2.INTER_LINEAR):
    scale = 1.0 * maxDim / max(img.shape[:2])
    if scale < 1  or boUpscale:
        img = imresize(img, scale, interpolation)
    else:
        scale = 1.0
    return img, scale

def imWidth(input):
    return imWidthHeight(input)[0]

def imHeight(input):
    return imWidthHeight(input)[1]

def imWidthHeight(input):
    width, height = Image.open(input).size #this does not load the full image
    return width,height

def imArrayWidth(input):
    return imArrayWidthHeight(input)[0]

def imArrayHeight(input):
    return imArrayWidthHeight(input)[1]
    
def imArrayWidthHeight(input):
    width =  input.shape[1]
    height = input.shape[0]
    return width,height
 
def imshow(img, waitDuration=0, maxDim = None, windowName = 'img'):
    if isinstance(img, str): #test if 'img' is a string
        img = cv2.imread(img)
    if maxDim is not None:
        scaleVal = 1.0 * maxDim / max(img.shape[:2])
        if scaleVal < 1:
            img = imresize(img, scaleVal)
    cv2.imshow(windowName, img)
    cv2.waitKey(waitDuration)

def drawRectangles(img, rects, color = (0, 255, 0), thickness = 2):
    for rect in rects:
        pt1 = tuple(ToIntegers(rect[0:2]))
        pt2 = tuple(ToIntegers(rect[2:]))
        try:
            cv2.rectangle(img, pt1, pt2, color, thickness)
        except:
            import pdb; pdb.set_trace()
            print("Unexpected error:", sys.exc_info()[0])

def drawCrossbar(img, pt):
    (x,y) = pt
    cv2.rectangle(img, (0, y), (x, y), (255, 255, 0), 1)
    cv2.rectangle(img, (x, 0), (x, y), (255, 255, 0), 1)
    cv2.rectangle(img, (img.shape[1],y), (x, y), (255, 255, 0), 1)
    cv2.rectangle(img, (x, img.shape[0]), (x, y), (255, 255, 0), 1)

def ptClip(pt, maxWidth, maxHeight):
    pt = list(pt)
    pt[0] = max(pt[0], 0)
    pt[1] = max(pt[1], 0)
    pt[0] = min(pt[0], maxWidth)
    pt[1] = min(pt[1], maxHeight)
    return pt

def drawText(img, pt, text, textWidth=None, color = (255,255,255), colorBackground = None, font = ImageFont.truetype("arial.ttf", 16)):
    pilImg = imconvertCv2Pil(img)
    pilImg = pilDrawText(pilImg,  pt, text, textWidth, color, colorBackground, font)
    return imconvertPil2Cv(pilImg)

def pilDrawText(pilImg, pt, text, textWidth=None, color = (255,255,255), colorBackground = None, font = ImageFont.truetype("arial.ttf", 16)):
    textY = pt[1]
    draw = ImageDraw.Draw(pilImg)
    if textWidth == None:
        lines = [text]
    else:
        lines = textwrap.wrap(text, width=textWidth)
    for line in lines:
        width, height = font.getsize(line)
        if colorBackground != None:
            draw.rectangle((pt[0], pt[1], pt[0] + width, pt[1] + height), fill=tuple(colorBackground[::-1]))
        draw.text(pt, line, fill = tuple(color), font = font)
        textY += height
    return pilImg

def getColorsPalette():
    colors = [[255,0,0], [0,255,0], [0,0,255], [255,255,0], [255,0,255]]
    for i in range(5):
        for dim in range(0,3):
            for s in (0.25, 0.5, 0.75):
                if colors[i][dim] != 0:
                    newColor = copy.deepcopy(colors[i])
                    newColor[dim] = int(round(newColor[dim] * s))
                    colors.append(newColor)
    return colors

def imconvertPil2Cv(pilImg):
    rgb = pilImg.convert('RGB')
    return np.array(rgb).copy()[:, :, ::-1]

def imconvertCv2Pil(img):
    cv2_im = cv2.cvtColor(img,cv2.COLOR_BGR2RGB)
    return Image.fromarray(cv2_im)

def ToIntegers(list1D):
    return [int(float(x)) for x in list1D]

def getDictionary(keys, values, boConvertValueToInt = True):
    dictionary = {}
    for key,value in zip(keys, values):
        if (boConvertValueToInt):
            value = int(value)
        dictionary[key] = value
    return dictionary

class Bbox:
    MAX_VALID_DIM = 100000
    left = top = right = bottom = None

    def __init__(self, left, top, right, bottom):
        self.left   = int(round(float(left)))
        self.top    = int(round(float(top)))
        self.right  = int(round(float(right)))
        self.bottom = int(round(float(bottom)))
        self.standardize()

    def __str__(self):
        return ("Bbox object: left = {0}, top = {1}, right = {2}, bottom = {3}".format(self.left, self.top, self.right, self.bottom))

    def __repr__(self):
        return str(self)

    def rect(self):
        return [self.left, self.top, self.right, self.bottom]

    def max(self):
        return max([self.left, self.top, self.right, self.bottom])

    def min(self):
        return min([self.left, self.top, self.right, self.bottom])

    def width(self):
        width  = self.right - self.left + 1
        assert(width>=0)
        return width

    def height(self):
        height = self.bottom - self.top + 1
        assert(height>=0)
        return height

    def surfaceArea(self):
        return self.width() * self.height()


