from typing import Any
from models.roi_handling import FeatureMapDistribution, gather_results, NestedTensor
from models.transformer import LandmarkBranch
from models.utils import WeightLoader, normalize_bbox

from ultralytics import YOLO, yolo
from ultralytics.yolo.v8.detect import DetectionPredictor
from ultralytics.yolo.engine.results import Results
from ultralytics.nn.tasks import feature_visualization, DetectionModel, BaseModel
from ultralytics.yolo.utils import ops, yaml_load

import torch
import torch.nn as nn

import torch
import torch.nn as nn
import torch.nn.functional as F

import numpy as np
import os

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
    def __init__(self, yolo_weight_path, cfg, landmark_branch_classes:list[int] = [], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._backbone = YOLO(yolo_weight_path, "detect")
        self.cfg = yaml_load(cfg)
        self._backbone.overrides.update(self.cfg["yolo_override"])
        self.yolo_detection:DetectionModel = self._backbone.model
        self.nc:int = self.yolo_detection.yaml['nc']

        # self.yolo_detection._predict_once = OLDT.decorator(self.yolo_detection, OLDT._predict_once) # 替换预测函数，将在model对象内添加一个属性feature_map
        self.get_feature_callback = yolo_detection_predict_once(self.yolo_detection)
        print("replaced")
        self.yolo_detection._predict_once = self.get_feature_callback

        self.feature_map_distribution = FeatureMapDistribution(cfg)

        self.landmark_branch_classes = landmark_branch_classes
        
        self.landmark_branches:dict[int, LandmarkBranch] = {}
        for branch_i in landmark_branch_classes:
            assert isinstance(branch_i, int)
            branch = LandmarkBranch(self.cfg["transformer_tgt_num"]).to("cuda")
            self.add_module(f"LandmarkBranch_{str(branch_i).rjust(2,'0')}", branch)
            self.landmark_branches[branch_i] = branch
            self.set_branch_trainable(branch_i)
        # self.transformer = Transformer()

        self.last_detect_rlt:list[Results] = []

        self.freeze_detection()

    @staticmethod
    def decorator(obj, func):
        def wrapper(*arg, **kw):
            return func(obj, *arg, **kw)
        return wrapper

    @staticmethod
    def _predict_once(obj, x, profile=False, visualize=False):
        y, dt = [], []  # outputs
        for m in obj.model:
            if m.f != -1:  # if not from previous layer
                x = y[m.f] if isinstance(m.f, int) else [x if j == -1 else y[j] for j in m.f]  # from earlier layers
            if profile:
                obj._profile_one_layer(m, x, dt)
            x = m(x)  # run
            y.append(x if m.i in obj.save else None)  # save output
            if visualize:
                feature_visualization(x, m.type, m.i, save_dir=visualize)
        obj.feature_map = (y[15], y[18], y[21]) 
        return x

    def parse_results(self, rlts:list[Results]):
        class_ids = [x.boxes.data[:, -1] for x in rlts]

        img_size = [x.orig_shape for x in rlts]
        normed_bboxes = []
        for i, size in enumerate(img_size):
            epd_size = torch.Tensor([size[1], size[0]]).to('cuda')
            nb = normalize_bbox(rlts[i].boxes.data[:, :4], epd_size)
            normed_bboxes.append(nb)
        return class_ids, normed_bboxes

    # def parse_results(self, rlts:list[Results]):
    #     class_ids = [x.boxes.data[:, -1] for x in rlts]

    #     img_size = [x.orig_shape for x in rlts]
    #     bboxes = []
    #     for i, size in enumerate(img_size):
    #         bboxes.append(rlts[i].boxes.data[:, :4])
    #     return class_ids, bboxes

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
        # P3, P4, P5 = self._backbone.model.feature_map
        P3, P4, P5 = self.get_feature_callback.feature_map
        # P3 = torch.rand(len(input), 256, 60, 80)
        feature_map = self.reshape_feature_maps((P3,))
        class_ids, bboxes_n = self.parse_results(detect_rlt) #[bn, num_roi?] [bn, num_roi?, 4]
        roi_feature_dict, org_idx, bboxes_n = self.feature_map_distribution(class_ids, bboxes_n, feature_map)
        
        landmark_dict = {}
        for class_id in self.landmark_branch_classes:
            try:
                rois:torch.Tensor = roi_feature_dict[class_id].tensor #[num_landmark_group?, C, H, W]
                masks:torch.Tensor = roi_feature_dict[class_id].mask #[num_landmark_group?, H, W]
            except:
                continue # 只选取关注的class
            branch = self.landmark_branches[class_id]
            landmark_coords, landmark_probs = branch(rois, masks) #[decoder_num, num_landmark_group?, landmarknum, 2]
            landmark_dict[class_id] = (landmark_coords, landmark_probs)

        # 重新汇聚
        detection_results = gather_results(class_ids, bboxes_n, org_idx, landmark_dict, input_size)

        return detection_results

    def train(self, mode: bool = True):
        self.yolo_detection.eval()
        for branch in self.landmark_branches.values():
            branch.train(mode)

    def eval(self):
        for branch in self.landmark_branches.values():
            branch.eval()

    def freeze_detection(self):
        for p in self.yolo_detection.parameters():
            p.requires_grad = False

    def set_branch_trainable(self, branch_i:int, trainable = True):
        try: branch = self.landmark_branches[branch_i]
        except KeyError:
            print(f"branch {branch_i} doesn't exist")
            return
        for p in branch.parameters():
            p.requires_grad = trainable

    def save_branch_weights(self, save_dir, prefix = ""):
        for key, value in self.landmark_branches.items():
            save_path = os.path.join(save_dir, prefix + "branch"+str(key).rjust(2,"0") + ".pt")
            torch.save(value.state_dict(), save_path)

    def load_branch_weights(self, branch_i, weights_path):
        try: branch = self.landmark_branches[branch_i]
        except KeyError: return
        pretrained = torch.load(weights_path)
        WeightLoader(branch).load_weights_to_layar(pretrained, WeightLoader.CORRISPONDING)

