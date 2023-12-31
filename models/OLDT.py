from typing import Any
from .roi_pipeline import FeatureMapDistribution, gather_results, RoiFeatureMapWithMask
from .transformer import LandmarkBranch
from .utils import WeightLoader, normalize_bbox
from .results import PredResult
from . import yolo8_patch 
import ultralytics
from ultralytics import YOLO, yolo
from ultralytics.yolo.v8.detect import DetectionPredictor
from ultralytics.yolo.engine.results import Results
from ultralytics.nn.tasks import feature_visualization, DetectionModel, BaseModel
from utils.yaml import load_yaml

import torch
import torch.nn as nn

import torch
import torch.nn as nn
import torch.nn.functional as F

import numpy as np
import os
import time
import matplotlib.pyplot as plt

class yolo_detection_predict_once():
    def __init__(self, obj) -> None:
        self.feature_map = ()
        self.obj = obj

    def __call__(self, x, profile=False, visualize=False) -> Any:
        y, dt = [], []  # outputs
        for m in self.obj.model:
            if m.f != -1:  # if not from previous layer
                x = y[m.f] if isinstance(m.f, int) else [x if j == -1 else y[j] for j in m.f]  # from earlier layers
            if profile:
                self.obj._profile_one_layer(m, x, dt)
            x = m(x)  # run
            y.append(x if m.i in self.obj.save else None)  # save output
            if visualize:
                feature_visualization(x, m.type, m.i, save_dir=visualize)
        self.feature_map = (y[15], y[18], y[21]) 
        return x

class OLDT(nn.Module):
    '''
    Object Landmarks Detection Transformer
    -----
    主要分为4层：
    * backbone: yolo-v8 large 
    -----
                      from  n    params  module                                       arguments                \n
     0                  -1  1      1856  ultralytics.nn.modules.conv.Conv             [3, 64, 3, 2]            \n
     1                  -1  1     73984  ultralytics.nn.modules.conv.Conv             [64, 128, 3, 2]          \n
     2                  -1  3    279808  ultralytics.nn.modules.block.C2f             [128, 128, 3, True]      \n
     3                  -1  1    295424  ultralytics.nn.modules.conv.Conv             [128, 256, 3, 2]         \n
     4                  -1  6   2101248  ultralytics.nn.modules.block.C2f             [256, 256, 6, True]      \n
     5                  -1  1   1180672  ultralytics.nn.modules.conv.Conv             [256, 512, 3, 2]         \n
     6                  -1  6   8396800  ultralytics.nn.modules.block.C2f             [512, 512, 6, True]      \n
     7                  -1  1   2360320  ultralytics.nn.modules.conv.Conv             [512, 512, 3, 2]         \n
     8                  -1  3   4461568  ultralytics.nn.modules.block.C2f             [512, 512, 3, True]      \n
     9                  -1  1    656896  ultralytics.nn.modules.block.SPPF            [512, 512, 5]            \n
    10                  -1  1         0  torch.nn.modules.upsampling.Upsample         [None, 2, 'nearest']     \n
    11             [-1, 6]  1         0  ultralytics.nn.modules.conv.Concat           [1]                      \n
    12                  -1  3   4723712  ultralytics.nn.modules.block.C2f             [1024, 512, 3]           \n
    13                  -1  1         0  torch.nn.modules.upsampling.Upsample         [None, 2, 'nearest']     \n
    14             [-1, 4]  1         0  ultralytics.nn.modules.conv.Concat           [1]                      \n
    15                  -1  3   1247744  ultralytics.nn.modules.block.C2f             [768, 256, 3]            \n
    16                  -1  1    590336  ultralytics.nn.modules.conv.Conv             [256, 256, 3, 2]         \n
    17            [-1, 12]  1         0  ultralytics.nn.modules.conv.Concat           [1]                      \n
    18                  -1  3   4592640  ultralytics.nn.modules.block.C2f             [768, 512, 3]            \n
    19                  -1  1   2360320  ultralytics.nn.modules.conv.Conv             [512, 512, 3, 2]         \n
    20             [-1, 9]  1         0  ultralytics.nn.modules.conv.Concat           [1]                      \n
    21                  -1  3   4723712  ultralytics.nn.modules.block.C2f             [1024, 512, 3]           \n
    22        [15, 18, 21]  1   5644480  ultralytics.nn.modules.head.Detect           [80, [256, 512, 512]]    \n
    * feature_map_distribution
    -----
    * landmark transformer
    -----
    * gather_results
    -----
    
    '''

    MODULE_DETECTION    = 0
    MODULE_LDMK         = 1

    def __init__(self, yolo_weight_path, cfg_file, landmark_branch_classes:list[int] = [], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._backbone = YOLO(yolo_weight_path, "detect")
        self._backbone.to(f"cuda:{torch.cuda.current_device()}")
        self.cfg = load_yaml(cfg_file)
        self._backbone.overrides.update(self.cfg["yolo_override"])
        self._backbone.overrides.update({"device": self._backbone.device})
        self.yolo_detection:DetectionModel = self._backbone.model
        self.nc:int = self.yolo_detection.yaml['nc']

        # self.yolo_detection._predict_once = OLDT.decorator(self.yolo_detection, OLDT._predict_once) # 替换预测函数，将在model对象内添加一个属性feature_map
        self.get_feature_callback = yolo_detection_predict_once(self.yolo_detection)
        print("replaced")
        self.yolo_detection._predict_once = self.get_feature_callback

        self.feature_map_distribution = FeatureMapDistribution(self.cfg)
        self.if_gather = True
        self.locked_val = { self.MODULE_DETECTION: True,
                            self.MODULE_LDMK: False}

        self.landmark_branch_classes = landmark_branch_classes
        
        self.landmark_branches:dict[int, LandmarkBranch] = {}
        for branch_i in landmark_branch_classes:
            self.add_branch(branch_i)
        # self.transformer = Transformer()

        self.last_detect_rlt:list[Results] = []

        self.freeze_detection()  

    def add_branch(self, branch_i):
        assert isinstance(branch_i, int)
        ldmk_branch = LandmarkBranch(self.cfg).to("cuda")
        self.add_module(f"{LandmarkBranch.__name__}_{str(branch_i).rjust(2,'0')}", ldmk_branch)
        self.landmark_branches[branch_i] = ldmk_branch
        
    def parse_results(self, rlts:list[Results]):
        class_ids = [x.boxes.data[:, -1] for x in rlts]

        image_size = [x.orig_shape for x in rlts]
        normed_bboxes = []
        for i, size in enumerate(image_size):
            epd_size = torch.Tensor([size[1], size[0]]).to('cuda')
            nb = normalize_bbox(rlts[i].boxes.data[:, :4], epd_size)
            normed_bboxes.append(nb)
        return class_ids, normed_bboxes

    def reshape_feature_maps(self, feature_maps):
        feature_maps_by_batch = []
        bn = feature_maps[0].shape[0]
        for b in range(bn):
            feature_maps_by_batch.append([P[b] for P in feature_maps])
        return feature_maps_by_batch

    def forward(self, input, iftrain = True):
        detect_rlt:list[Results] = self._backbone.predict(input)
        self.last_detect_rlt = detect_rlt
        input_size = [x.orig_shape[::-1] for x in detect_rlt] #list[(w,h)]
        
        ### 整合特征图
        P3, P4, P5 = self.get_feature_callback.feature_map
        feature_map = self.reshape_feature_maps((P3,))
        class_ids, bboxes_n = self.parse_results(detect_rlt) #[bn, num_roi?] [bn, num_roi?, 4]
        distribution = self.feature_map_distribution(class_ids, bboxes_n, feature_map)
        roi_feature_dict:dict[int, RoiFeatureMapWithMask]    = distribution[0]
        org_idx:dict[int, list[list[int]]]          = distribution[1]
        bboxes_n:list[torch.Tensor]                 = distribution[2]

        ### LDT
        # landmark_dict:dict[int, dict[str, torch.Tensor]] = {}
        landmark_dict:dict[int, PredResult] = {}
        for class_id in self.landmark_branch_classes:
            try:
                rois:torch.Tensor = roi_feature_dict[class_id].rois_feature_map #[num_landmark_group?, C, H, W]
                masks:torch.Tensor = roi_feature_dict[class_id].masks #[num_landmark_group?, H, W]
            except:
                continue # 只选取关注的class
            landmark_coords, landmark_probs = self.forward_ldmk(class_id, rois, masks)

            pred_result = PredResult(
                pred_landmarks_coord    = landmark_coords, 
                pred_landmarks_probs     = landmark_probs)
            pred_result.pred_class  = pred_result.boardcast([class_id], torch.int32)
            landmark_dict[class_id] = pred_result
        
        if not self.if_gather:
            # 用于计算损失，
            for id_ in landmark_dict.keys():
                origin = org_idx[id_]
                bbox_n_list = []
                for idx in origin:
                    bbox_n_list.append(bboxes_n[idx[0]][idx[1]])
                bboxes_n:torch.Tensor = torch.stack(bbox_n_list)
                bn_list = torch.Tensor([x[0] for x in origin]).to(bboxes_n.device)
                input_size_list = torch.Tensor([input_size[x[0]] for x in origin]).to(bboxes_n.device)
                landmark_dict[id_].pred_bboxes_n = bboxes_n
                landmark_dict[id_].pred_batch_idx = bn_list
                landmark_dict[id_].input_size = landmark_dict[id_].boardcast(input_size_list)
            detection_results = landmark_dict
        else:
            # 重新汇聚
            detection_results =\
                  gather_results(class_ids, bboxes_n, org_idx, landmark_dict, input_size)

        return detection_results

    def forward_ldmk(self, class_id, rois, masks):
        branch = self.landmark_branches[class_id]
        landmark_coords, landmark_probs = branch(rois, masks) #[decoder_num, num_landmark_group?, landmarknum, 2]
        return landmark_coords, landmark_probs

    def set_mode(self, mode):
        '''
        mode: "train" / "val" / "predict"
        '''
        def lock():
            if self.locked_val[self.MODULE_DETECTION]:
                self.yolo_detection.eval()
            if self.locked_val[self.MODULE_LDMK]:
                for branch in self.landmark_branches.values():
                    branch.eval()
        assert mode == "train" or mode == "val" or mode == "predict" 
        if mode == "train":
            super().train(True)
            lock()
            self.if_gather = False
        elif mode == "val":
            super().eval()
            lock()
            self.if_gather = False
        elif mode == "predict":
            super().eval()
            lock()
            self.if_gather = True
        else:
            raise ValueError

    def freeze_detection(self):
        for p in self.yolo_detection.parameters():
            p.requires_grad = False

    def save_branch_weights(self, save_dir, prefix = ""):
        for key, value in self.landmark_branches.items():
            save_path = os.path.join(save_dir, prefix + "branch_ldt_"+str(key).rjust(2,"0") + ".pt")
            torch.save(value.state_dict(), save_path)

    def load_branch_weights(self, branch_i, ldt_weights_path):
        if ldt_weights_path != "":        
            try: branch = self.landmark_branches[branch_i]
            except KeyError: return
            pretrained = torch.load(ldt_weights_path, map_location='cuda')
            WeightLoader(branch).load_weights_to_layar(pretrained, WeightLoader.CORRISPONDING)
