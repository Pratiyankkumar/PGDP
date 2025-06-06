# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
# Set up custom environment before nearly anything else is imported
# NOTE: this should be the first import (no not reorder)
from geo_parse.utils.env import setup_environment  # noqa F401 isort:skip

import argparse
import os
import torch
from geo_parse.config import cfg
from geo_parse.data import make_data_loader
from geo_parse.engine.inference import inference
from geo_parse.modeling.detector import build_detection_model
from geo_parse.utils.checkpoint import DetectronCheckpointer
from geo_parse.utils.collect_env import collect_env_info
from geo_parse.utils.comm import synchronize, get_rank
from geo_parse.utils.logger import setup_logger
from geo_parse.utils.miscellaneous import mkdir


def main():
    parser = argparse.ArgumentParser(description="PyTorch PGDP Inference")
    parser.add_argument(
        "--config-file",
        default="/private/home/fmassa/github/detectron.pytorch_v2/configs/e2e_faster_rcnn_R_50_C4_1x_caffe2.yaml",
        metavar="FILE",
        help="path to config file",
    )
    parser.add_argument("--local_rank", type=int, default=0)
    parser.add_argument(
        "opts",
        help="Modify config options using the command-line",
        default=None,
        nargs=argparse.REMAINDER,
    )

    args = parser.parse_args()

    num_gpus = int(os.environ["WORLD_SIZE"]) if "WORLD_SIZE" in os.environ else 1
    distributed = num_gpus > 1

    if distributed:
        # FIX: Make CUDA calls conditional
        if torch.cuda.is_available():
            torch.cuda.set_device(args.local_rank)
            torch.distributed.init_process_group(
                backend="nccl", init_method="env://"
            )
        else:
            # Use CPU backend for distributed training
            torch.distributed.init_process_group(
                backend="gloo", init_method="env://"
            )
        synchronize()

    cfg.merge_from_file(args.config_file)
    cfg.merge_from_list(args.opts)
    cfg.freeze()

    save_dir = ""
    logger = setup_logger("geo_parse", save_dir, get_rank())
    
    # FIX: Update logging message for CPU compatibility
    if torch.cuda.is_available():
        logger.info("Using {} GPUs".format(num_gpus))
    else:
        logger.info("Using CPU (no GPUs available)")
    
    logger.info(cfg)

    logger.info("Collecting env info (might take some time)")
    logger.info("\n" + collect_env_info())

    model = build_detection_model(cfg)
    model.to(cfg.MODEL.DEVICE)

    output_dir = cfg.OUTPUT_DIR
    checkpointer = DetectronCheckpointer(cfg, model, save_dir=output_dir)
    _ = checkpointer.load(cfg.MODEL.WEIGHT)

    output_folders = [None] * len(cfg.DATASETS.TEST)
    dataset_names = cfg.DATASETS.TEST
    if cfg.OUTPUT_DIR:
        for idx, dataset_name in enumerate(dataset_names):
            output_folder = os.path.join(cfg.OUTPUT_DIR, "inference", dataset_name)
            mkdir(output_folder)
            output_folders[idx] = output_folder
    data_loaders_val = make_data_loader(cfg, is_train=False, is_distributed=distributed)
    for output_folder, dataset_name, data_loader_val in zip(output_folders, dataset_names, data_loaders_val):
        inference(
            cfg,
            model,
            data_loader_val,
            dataset_name=dataset_name,
            device=cfg.MODEL.DEVICE,
            output_folder=output_folder,
        )
        synchronize()


if __name__ == "__main__":
    main()