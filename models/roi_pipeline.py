from .results import LandmarkDetectionResult, PredResult, RoiFeatureMapWithMask
from .utils import denormalize_bbox, normalize_bbox
from utils.yaml import load_yaml
import torch
import torch.nn as nn
import numpy as np


from torchvision.ops import roi_align
from torchvision.transforms.functional import crop

import matplotlib.pyplot as plt




# class PyramidROIAlign(nn.Module):
#     def __init__(self, pool_shape):
#         super(PyramidROIAlign, self).__init__()
#         self.pool_shape = pool_shape

#     def forward(self, inputs):
#         # Crop boxes [batch, num_boxes, (y1, x1, y2, x2)] in normalized coords
#         boxes = inputs[0]

#         # Feature Maps. List of feature maps from different level of the
#         # feature pyramid. Each is [batch, channels, height, width]
#         feature_maps = inputs[1:]

#         # Assign each ROI to a level in the pyramid based on the ROI area.
#         y1, x1, y2, x2 = torch.split(boxes, 1, dim=2)
#         h = y2 - y1
#         w = x2 - x1
#         # Use shape of first image. Images in a batch must have the same size.
#         image_shape = parse_image_meta_graph(image_meta)['image_shape'][0]
#         # Equation 1 in the Feature Pyramid Networks paper. Account for
#         # the fact that our coordinates are normalized here.
#         # e.g. a 224x224 ROI (in pixels) maps to P4
#         image_area = torch.tensor(image_shape[0] * image_shape[1], dtype=torch.float32)
#         roi_level = torch.log2(torch.sqrt(h * w) / (224.0 / torch.sqrt(image_area)))
#         roi_level = torch.clamp(torch.round(roi_level), min=2, max=5).to(torch.int32)
#         roi_level = roi_level.squeeze(2)

#         # Loop through levels and apply ROI pooling to each. P2 to P5.
#         pooled = []
#         box_to_level = []
#         for i, level in enumerate(range(2, 6)):
#             ix = torch.where(roi_level == level)
#             level_boxes = torch.gather(boxes, 1, ix.unsqueeze(2))

#             # Box indices for crop_and_resize.
#             box_indices = ix[:, 0].to(torch.int32)

#             # Keep track of which box is mapped to which level
#             box_to_level.append(ix)

#             # Stop gradient propogation to ROI proposals
#             level_boxes = level_boxes.detach()
#             box_indices = box_indices.detach()

#             # Here we use the simplified approach of a single value per bin,
#             # which is how it's done in F.interpolate()
#             # Result: [batch * num_boxes, pool_height, pool_width, channels]
#             pooled.append(F.interpolate(
#                 feature_maps[i], size=self.pool_shape, mode="bilinear", align_corners=False))

#         # Pack pooled features into one tensor
#         pooled = torch.cat(pooled, dim=0)

#         # Pack box_to_level mapping into one array and add another
#         # column representing the order of pooled boxes
#         box_to_level = torch.cat(box_to_level, dim=0)
#         box_range = torch.unsqueeze(torch.arange(box_to_level.size(0)), 1)
#         box_to_level = torch.cat([box_to_level.to(torch.int32), box_range], dim=1)

#         # Rearrange pooled features to match the order of the original boxes
#         # Sort box_to_level by batch then box index
#         # PyTorch doesn't have a way to sort by two columns, so merge them and sort.
#         sorting_tensor = box_to_level[:, 0] * 100000 + box_to_level[:, 1]
#         _, ix = torch.sort(sorting_tensor, descending=True)
#         ix = torch.gather(box_to_level[:, 2], 0, ix)
#         pooled = torch.gather(pooled, 0, ix.unsqueeze(1).expand(-1, self.pool_shape[0], self.pool_shape[1], -1))

#         # Re-add the batch dimension
#         shape = torch.cat([boxes.size(0), boxes.size(1), self.pool_shape, torch.tensor(feature_maps[0].size(3))], dim=0)
#         pooled = pooled.reshape(shape)
#         return pooled

#     def compute_output_shape(self, input_shape):
#         return (input_shape[0][0], input_shape[0][1]) + self.pool_shape + (input_shape[2][3],)


class CropLayer(nn.Module):
    def __init__(self, cfg, max_token_num = 784, pool_size = (24, 24)) -> None:
        super().__init__()
        self.max_token_num = max_token_num
        self.min_token_num = int(max_token_num / 4)

        self.pool_size = pool_size
        self.cfg = cfg

    @property
    def use_variable_tokennum(self):
        return self.cfg["use_variable_tokennum"]

    def _choose_crop_idx(self, bbox_areas:torch.Tensor):
        for i in range(bbox_areas.size()[0]):
            if bbox_areas[i] < self.max_token_num and bbox_areas[i] >= self.min_token_num :
                return i
        return -1
    
    def resize_crop_bynorm(self, P, ntop, nleft, nh, nw):
        H, W = P.shape[-2:]
        top     = int(ntop*H)
        left    = int(nleft*W)
        h       = int()
        w       = int()
    
    @staticmethod
    def crop(P:torch.Tensor, bbox_n:torch.Tensor):
        '''
        P: [b, C, H, W]
        bbox_n: [n, 4]
        '''
        # boxx1,y1向下取整，x2,y2向上取整, 转化为(x1, y1, w, h) in pixels
        height, width = P.shape[-2:]
        bbox = denormalize_bbox(bbox_n, (width, height))

        bbox[:, 0] = torch.floor(bbox[:, 0])  # x1
        bbox[:, 1] = torch.floor(bbox[:, 1])  # y1
        bbox[:, 2] = torch.ceil(bbox[:, 2])  # x2
        bbox[:, 3] = torch.ceil(bbox[:, 3])  # y2
        bbox = bbox.to(torch.int64)
        
        # 循环执行裁剪
        croped:list[torch.Tensor] = []
        for bx in bbox:
            t, l, h, w = bx[1], bx[0], bx[3] - bx[1], bx[2] - bx[0]
            croped.append(crop(P, t, l, h, w))

        # 重新归一化
        rounded_bbox_n = normalize_bbox(bbox, (width, height))

        return croped, rounded_bbox_n

    def roi_align(self, P:torch.Tensor, bbox_n:torch.Tensor):
        P = P.unsqueeze(0)
        box = denormalize_bbox(bbox_n, list(P.shape[-2:][::-1]))
        idx = torch.zeros(box.shape[0], 1).to("cuda")
        box = torch.cat((idx, box), dim=1)                        
        aligned = roi_align(P, box, self.pool_size, aligned=True)
        return aligned, bbox_n

    def forward(self, bboxes_list, pyramid_list):
        '''
        parameter
        -----
        bboxes_list: [bn, ?, 4] 归一化的bbox
        pyramid_list: [bn, (P,)]

        return
        -----
        all_croped list    :[bn, ?, H, W, C]
        '''
        bn = len(bboxes_list) # batch_num
        all_croped:list[list[torch.Tensor]] = []
        all_used_bbox_n:list[list[torch.Tensor]] = []
        for i in range(bn):
            feature_maps = pyramid_list[i]
            feature_maps_area = torch.Tensor([x.shape[-2]*x.shape[-1] for x in feature_maps]).to("cuda")
            bboxes      = bboxes_list[i]
            croped:list[torch.Tensor] = []
            used_bbox_n:list[torch.Tensor] = []
            if bboxes.shape[0] > 0:
                ### 判断图片大小，从哪一层crop
                FMmatch = {}
                for bi, bbox in enumerate(bboxes):
                    top = bbox[1]
                    left = bbox[0]
                    h = bbox[3] - top
                    w = bbox[2] - left
                    bbox_areas = h * w * feature_maps_area
                    choice_idx = self._choose_crop_idx(bbox_areas)
                    FMmatch.setdefault(choice_idx, []).append(bi) #添加

                idx_order = []
                for Pi, bis in FMmatch.items():
                    P:torch.Tensor = feature_maps[Pi] # 选取的特征图
                    idx_order.extend(bis)
                    bbox_n = [bboxes[bi] for bi in bis]
                    bbox_n = torch.stack(bbox_n).to("cuda")
                    # 转化为pixel
                    if self.use_variable_tokennum:
                        _croped, _used_bbox_n = self.crop(P, bbox_n)
                    else:
                        _croped, _used_bbox_n = self.roi_align(P, bbox_n)
                    used_bbox_n += _used_bbox_n
                    croped += _croped # [?, C, pool_size, pool_size]
                
                resort = np.argsort(idx_order)
                croped      = [croped[i] for i in resort]
                used_bbox_n = [used_bbox_n[i] for i in np.argsort(idx_order)]

            all_croped.append(croped)
            all_used_bbox_n.append(used_bbox_n)
        return all_croped, all_used_bbox_n

class FeatureMapDistribution(nn.Module):
    def __init__(self, cfg) -> None:
        super().__init__()
        self.cfg = cfg
        max_token_num  = self.cfg["max_token_num"]
        pool_size      = self.cfg["pool_size"]
        self.crop_layer = CropLayer(self.cfg, max_token_num, pool_size)

    @property
    def use_variable_tokennum(self):
        return self.cfg["use_variable_tokennum"]

    def distribute(self, class_ids, croped_feature_map:list[list[torch.Tensor]]) -> tuple[dict[int, RoiFeatureMapWithMask], dict[int, list[list[int]]]]:
        roi_feature_dict:dict[int, list[torch.Tensor]] = {}
        org_idx:dict[int, list[list[int]]] = {}
        for bn in range(len(class_ids)):
            for i, id_ in enumerate(class_ids[bn]):
                P = croped_feature_map[bn][i] #[C, H, W]
                roi_feature_dict.setdefault(int(id_), []).append(P)
                org_idx.setdefault(int(id_), []).append([bn, i])
        for id_, tensor_list in roi_feature_dict.items():
            tensor, mask = self.expand_and_merge(tensor_list)
            roi_feature_dict[id_] = RoiFeatureMapWithMask(feature_maps=tensor, masks=mask)
        return roi_feature_dict, org_idx

    @staticmethod
    def expand_and_merge(tensor_list:list[torch.Tensor]):
        '''
        将不同shape的feature_map按能包含他们的最小shape扩充并合并
        扩充的部分mask被赋值为True，原有部分为False
        mask: False 表示会被计算, True表示将被屏蔽
        parameter
        -----
        croped_feature_map:list[list[torch.Tensor]] 
        其中Tensor是3维的[C, H, W]，第一层list表示的是batch，第二层list表示每个batch的num_roi

        return
        -----
        list[tuple[(feat, mask)]]
        '''
        # 扩充并合并特征图
        max_size = torch.Tensor([list(img.shape) for img in tensor_list], 
                                ).to(torch.int64).max(dim=0)[0].tolist()
        # min_size = tuple(min(s) for s in zip(*[img.shape for img in tensor_list]))
        batch_shape = [len(tensor_list)] + max_size
        n, c, h, w = batch_shape
        dtype = tensor_list[0].dtype
        device = tensor_list[0].device
        tensor = torch.zeros(batch_shape, dtype=dtype, device=device)
        mask = torch.ones((n, h, w), dtype=torch.bool, device=device)
        for img, pad_img, m in zip(tensor_list, tensor, mask):
            pad_img[:img.shape[0], :img.shape[1], :img.shape[2]].copy_(img)
            m[:img.shape[1], :img.shape[2]] = False
        
        return tensor, mask

    def forward(self, class_ids, bboxes_n, feature_maps):
        '''
        parameter
        -----
        class_ids, bboxes, feature_maps = inputs
        class_ids: list[list[]] [bn, ?]
        bboxes:    list[list[]] [bn, ?, 4]
        feature_maps: list[]    [bn, (P3, P4, P5)]

        return
        -----
        roi_feature_dict    :{class_id: [?, H, W, C]} x branch_num
        '''
        # with torch.no_grad():
        croped_feature_map, used_bbox_n = \
            self.crop_layer(bboxes_n, feature_maps) #[bn, ?, H, W, C] list[list[Tensor]]
        # 按class_id分配
        roi_feature_dict, org_idx = self.distribute(class_ids, croped_feature_map)
        return roi_feature_dict, org_idx, used_bbox_n

###合并###
def gather_results(class_ids:list[torch.Tensor], 
            bboxes:list[torch.Tensor], 
            org_idx:dict[int, list[list[int]]], 
            landmark_dict:dict[int, PredResult],
            input_size_list: list[tuple]) -> list[list[LandmarkDetectionResult]]:
    # with torch.no_grad():
    BN = len(class_ids)
    detection_results = [[None for _ in range(class_ids[bn].shape[0])] for bn in range(BN)] # 用None占空
    for id_ in org_idx.keys():
        if id_ not in landmark_dict:
            continue
        else:
            coords  = landmark_dict[id_].pred_landmarks_coord # [num_landmark_group?, output_num, decoder_num, 2]
            probs   = landmark_dict[id_].pred_landmarks_probs # [num_landmark_group?, output_num, decoder_num, landmarknum + 1]
        oi = org_idx[id_] # 坐标对应的原位
        # 循环还原坐标到原位，通过LandmarkDetectionResult将每个roi的类别、bbox、landmarks绑定
        for ci, idx in enumerate(oi):
            c = coords[ci, ...] 
            p = probs[ci, ...] 
            bbox = bboxes[idx[0]][idx[1]]
            input_size = input_size_list[idx[0]]
            result = LandmarkDetectionResult(bbox, id_, c, p, input_size)
            detection_results[idx[0]][idx[1]] = result
    detection_results = [[item for item in sublist if item is not None] for sublist in detection_results]
    return detection_results
    
