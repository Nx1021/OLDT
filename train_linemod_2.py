from __init__ import SCRIPT_DIR, DATASETS_DIR, CFG_DIR, WEIGHTS_DIR, LOGS_DIR, _get_sub_log_dir
from launcher.Predictor import OLDTPredictor, IntermediateManager
from launcher.Trainer import Trainer

from models.OLDT import OLDT
from launcher.OLDTDataset import OLDTDataset
from launcher.setup import setup

import platform
from torch.utils.data import Dataset, DataLoader
import torch
import numpy as np
from PIL import Image
from typing import Iterable
import os
import shutil
from utils.yaml import load_yaml, dump_yaml

from posture_6d.data.dataset_example import VocFormat_6dPosture

if __name__ == "__main__":
    # setup
    cfg_file = f"{CFG_DIR}/oldt_linemod_mix.yaml"

    weights = {
        0: "20240102071157branch_ldt_00.pt",
        1: "20230815074456branch_ldt_01.pt",
        2: "20230831080343branch_ldt_02.pt",
        3: "20230818011104branch_ldt_03.pt",
        4: "20230819081237branch_ldt_04.pt",
        5: "20230819081450branch_ldt_05.pt",
        6: "20230823005717branch_ldt_06.pt",
        7: "20230826185557branch_ldt_07.pt",
        8: "20230823010935branch_ldt_08.pt",
        9: "20230826200842branch_ldt_09.pt",
        10: "20230823011027branch_ldt_10.pt",
        11: "20230826191856branch_ldt_11.pt",
        12: "20230823011323branch_ldt_12.pt",
        13: "20230826165015branch_ldt_13.pt",
        14: "20230902185318branch_ldt_14.pt"
    }
    # torch.cuda.set_device("cuda:0")
    for i in [0, 1, 3, 4, 5, 7, 8, 9, 11, 12, 13, 14]:
        setup_paras = load_yaml(cfg_file)["setup"]

        sys = platform.system()
        if sys == "Windows":
            batch_size = 4
            # model = torch.nn.DataParallel(model)
        elif sys == "Linux":
            batch_size = 32 # * torch.cuda.device_count()
            # model = torch.nn.DataParallel(model)
        # setup_paras["ldt_branches"] = {i: f"linemod_mix/{weights[i]}"}
        setup_paras["ldt_branches"] = {i: ""}
        setup_paras["batch_size"] = batch_size
        setup_paras["sub_data_dir"] = f"linemod_mix/{str(i).rjust(6, '0')}"

        trainer = setup("train",
                        detection_base_weight=f"{WEIGHTS_DIR}/linemod_mix/{str(i).rjust(6, '0')}_best.pt" ,
                        **setup_paras)
        trainer.train_dataset.vocformat.spliter_group.split_mode = "aug_posture"
        trainer.train()

        trainer = None

    # for i in range(1, 15):
    #     print(i)
    #     for name, mapname in zip(["bbox_3ds", "depth_scale", "intr", "landmarks", "trans_vecs", "visib_fracts"], 
    #                              ["bbox_3d",  "depth_scale", "intr", "landmarks", "extr_vecs",  "visib_fracts"]):
    #         os.remove(f"/home/nerc-ningxiao/datasets/linemod_mix/{str(i).rjust(6, '0')}/{name}/{mapname}.datamap")
    #         shutil.copy(f"{DATASETS_DIR}/linemod_mix/{str(i).rjust(6, '0')}/{name}/{mapname}.datamap", 
    #                     f"/home/nerc-ningxiao/datasets/linemod_mix/{str(i).rjust(6, '0')}/{name}/{mapname}.datamap")
        # voc:VocFormat_6dPosture = VocFormat_6dPosture(f"{DATASETS_DIR}/linemod_mix/{str(i).rjust(6, '0')}")
        
        # voc_server:VocFormat_6dPosture = VocFormat_6dPosture(f"/home/nerc-ningxiao/datasets/linemod_mix/{str(i).rjust(6, '0')}")
        # voc_server.copy_from_simplified(voc, cover=True, force=True)