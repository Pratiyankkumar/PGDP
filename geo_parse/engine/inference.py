# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
import logging
import time
import os

import torch
from tqdm import tqdm
from geo_parse.data.datasets.evaluation import evaluate
from ..utils.comm import is_main_process, get_world_size
from ..utils.comm import all_gather
from ..utils.comm import synchronize
from ..utils.timer import Timer, get_time_str
from .bbox_aug import im_detect_bbox_aug


def compute_on_dataset(cfg, model, data_loader, device, timer=None):
    
    model.eval()
    results_dict = {}

    for _, batch in enumerate(tqdm(data_loader)):
        images, _, _, _, image_ids = batch
        # images, targets_det, targets_seg, _, image_ids = batch
        with torch.no_grad():
            if timer:
                timer.tic()
            if cfg.TEST.BBOX_AUG.ENABLED:
                output = im_detect_bbox_aug(model, images, device)
            else:
                output = model(images.to(device))
                # use the GT of targets_det and targets_seg
                # targets_det = [target.to(device) for target in targets_det] 
                # output = model(images.to(device), targets_det, targets_seg)
            if timer:
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                timer.toc() 

        results_dict.update(
            {img_id: result for img_id, result in zip(image_ids, output)}
        )
    return results_dict

def _accumulate_predictions_from_multiple_gpus(predictions_per_gpu):

    all_predictions = all_gather(predictions_per_gpu)
    if not is_main_process():
        return
    # merge the list of dicts
    predictions = {}
    for p in all_predictions:
        predictions.update(p)
    # convert a dict where the key is the index in a list
    image_ids = list(sorted(predictions.keys()))
    if len(image_ids) != image_ids[-1] + 1:
        logger = logging.getLogger("geo_parse.inference")
        logger.warning(
            "Number of images that were gathered from multiple processes is not "
            "a contiguous set. Some images might be missing from the evaluation"
        )
    # convert to a list
    predictions = [predictions[i] for i in image_ids]
    return predictions

def inference(
        cfg,
        model,
        data_loader,
        dataset_name,
        device="cuda",
        output_folder=None,
):
    # convert to a torch.device for efficiency
    device = torch.device(device)
    num_devices = get_world_size()
    logger = logging.getLogger("geo_parse.inference")
    dataset = data_loader.dataset
    logger.info("Start evaluation on {} dataset({} images).".format(dataset_name, len(dataset)))
    total_timer = Timer()
    inference_timer = Timer()
    total_timer.tic()

    predictions = compute_on_dataset(cfg, model, data_loader, device, inference_timer)
    
    # wait for all processes to complete before measuring the time
    synchronize()
    total_time = total_timer.toc()
    total_time_str = get_time_str(total_time)
    logger.info(
        "Total run time: {} ({} s / img per device, on {} devices)".format(
            total_time_str, total_time * num_devices / len(dataset), num_devices
        )
    )
    total_infer_time = get_time_str(inference_timer.total_time)
    logger.info(
        "Model inference time: {} ({} s / img per device, on {} devices)".format(
            total_infer_time,
            inference_timer.total_time * num_devices / len(dataset),
            num_devices,
        )
    )

    predictions = _accumulate_predictions_from_multiple_gpus(predictions)

    if not is_main_process():
        return

    # if output_folder:
    #     torch.save(predictions, os.path.join(output_folder, "predictions.pth"))
    # predictions = torch.load('./inference/geo_test/predictions.pth')
    
    extra_args = dict(
      iou_thresh_det=cfg.TEST.IOU_DET_TH,
      iou_thresh_seg=cfg.TEST.IOU_SEG_TH,
      cfg = cfg
    )

    return evaluate(dataset=dataset,
                    predictions=predictions,
                    output_folder=output_folder,
                    **extra_args)
