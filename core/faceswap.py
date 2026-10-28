# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
"""换脸的**像素与几何**部分：取目标格、抬到采样分辨率、缩回格子。

这里不含任何编码或采样逻辑（那是 `core.refimage` 与 `core.sampling` 的事）。
分工的理由：几何与像素是本包唯一有实测结论的领域（`spec` 一根线传完，
裁/贴逐像素对齐），换脸只是把「一格」当普通图处理，不该另起一套坐标。

**为什么要把单格抬到 `sample_edge` 再缩回。** 4 格 496 长边的画布里，一格只有
0.135 MP，脸大约 50×27 像素 —— 模型在那个尺度上认不出是谁。逐格模式早就有同一个
机制（每格按 `sample_edge` 采样再缩回格子），这里复用，不新增未验证的旋钮。
"""

from __future__ import annotations

#: 抬分辨率时的重采样核。放大用 `bicubic`（`area` 放大等于最近邻，会出块），
#: 缩回一律 `area`（面积平均，不会产生摩尔纹）。
UP_METHOD = "bicubic"
DOWN_METHOD = "area"

#: 抬到多少。与生成节点的 `sample_edge` 默认值一致：1024 是 MOSS 的训练尺度下限附近。
DEFAULT_SAMPLE_EDGE = 1024


def resolve_target(image, layout, index):
    """从整张宫格里取出第 `index` 格。返回 `(单格 [1,h,w,C], 说明)`。

    `layout` 为 `None` 时把 `image` 整体当成一格（用户只接了单格图，或本来就没有宫格）。
    """
    if image is None:
        raise ValueError("源图为空：请把要换脸的那张图接到 image")
    if image.ndim != 4:
        raise ValueError("源图必须是 [B, H, W, C]（收到 %d 维）" % image.ndim)
    if image.shape[0] != 1:
        raise ValueError(
            "源图批次应为 1，收到 %d。换脸一次只处理一张图——"
            "多格请用 index 逐格跑，或先接一个取单张的节点。" % image.shape[0])

    if layout is None:
        return image, "整图（未接 spec，按单格处理）"

    index = int(index)
    if index < 0 or index >= layout.panels:
        raise ValueError(
            "index=%d 越界：本布局共 %d 格（0 起）。想换整图请不接 spec。" % (index, layout.panels))
    if (int(image.shape[1]), int(image.shape[2])) != (layout.height, layout.width):
        raise ValueError(
            "源图 %d×%d 与 spec 画布 %d×%d 不一致。接了 spec 就必须给同一张宫格。"
            % (image.shape[2], image.shape[1], layout.width, layout.height))

    from . import pixels as P
    panel = P.crop_panels(image, layout, shave=0)[index:index + 1]
    return panel, "第 %d 格（spec 画布 %d×%d）" % (index, layout.width, layout.height)


def to_sampling(panel, sample_edge):
    """把单格抬到采样长边。返回 `(抬后张量, 报告)`。

    只升不降：单格本来就有 1024 以上时不再放大（放大不增加信息，只烧显存）。
    """
    import comfy.utils

    height, width = int(panel.shape[1]), int(panel.shape[2])
    long_edge = max(height, width)
    edge = int(sample_edge)
    report = {"cell": (width, height), "sampled": (width, height), "scale": 1.0}
    if edge <= 0 or long_edge >= edge:
        return panel, report

    scale = edge / float(long_edge)
    target_w = max(16, int(round(width * scale / 16.0)) * 16)
    target_h = max(16, int(round(height * scale / 16.0)) * 16)
    out = comfy.utils.common_upscale(
        panel.movedim(-1, 1), target_w, target_h, UP_METHOD, "disabled"
    ).movedim(1, -1)
    report.update({"sampled": (target_w, target_h), "scale": round(scale, 3)})
    return out, report


def to_cell(panel, cell_size):
    """把采样结果缩回格子精确尺寸。**必须精确**：差 1 像素贴回就错位。"""
    import comfy.utils

    width, height = int(cell_size[0]), int(cell_size[1])
    if (int(panel.shape[2]), int(panel.shape[1])) == (width, height):
        return panel
    return comfy.utils.common_upscale(
        panel.movedim(-1, 1), width, height, DOWN_METHOD, "disabled"
    ).movedim(1, -1)


def megapixels(image):
    """一张图的 MP，报告用。"""
    return round(int(image.shape[1]) * int(image.shape[2]) / 1_000_000, 4)

def crop_mask(mask, layout, index):
    """把整张宫格的蒙版裁成第 `index` 格。`mask` 是 `[B,H,W]` 或 `[H,W]`。

    与 `crop_panels` 用**同一套整数坐标**：蒙版和图像如果各裁各的，脸框就会偏，
    而这种偏不会报错，只会让「蒙版外不动」这句话变成假的。
    """
    if mask is None:
        return None
    if mask.ndim == 2:
        mask = mask.unsqueeze(0)
    if mask.ndim != 3:
        raise ValueError("mask 必须是 [B, H, W] 或 [H, W]（收到 %d 维）" % mask.ndim)
    if layout is None:
        return mask
    cell = layout.cell(int(index))
    if (int(mask.shape[1]), int(mask.shape[2])) != (layout.height, layout.width):
        raise ValueError(
            "蒙版 %d×%d 与 spec 画布 %d×%d 不一致；接了 spec 就必须给同一张宫格的蒙版。"
            % (mask.shape[2], mask.shape[1], layout.width, layout.height))
    return mask[:, cell.y:cell.y + cell.h, cell.x:cell.x + cell.w]


def fit_mask(mask, size_hw):
    """把蒙版缩放到 `(h, w)`。双线性，保留羽化。"""
    import torch

    if mask is None:
        return None
    if mask.ndim == 2:
        mask = mask.unsqueeze(0)
    h, w = int(size_hw[0]), int(size_hw[1])
    if (int(mask.shape[1]), int(mask.shape[2])) == (h, w):
        return mask
    out = torch.nn.functional.interpolate(
        mask.unsqueeze(1).float(), size=(h, w), mode="bilinear", align_corners=False)
    return out.squeeze(1)


def mask_for_latent(mask, latent):
    """把图像分辨率的蒙版压到 latent 空间：`[B,1,h,w]`。

    宿主 `SetLatentNoiseMask` 只做 reshape，缩放发生在采样器内部；这里自己压，
    因为我们要在**进采样器之前**就知道尺寸对不对（否则错了只会表现为「没效果」）。
    """
    import torch

    if mask is None:
        return None
    samples = latent.get("samples") if isinstance(latent, dict) else latent
    if samples is None:
        raise ValueError("latent 里没有 samples，无法对齐蒙版")
    h, w = int(samples.shape[-2]), int(samples.shape[-1])
    m = mask
    if m.ndim == 2:
        m = m.unsqueeze(0)
    m = torch.nn.functional.interpolate(
        m.unsqueeze(1).float(), size=(h, w), mode="bilinear", align_corners=False)
    # 尺寸必须与 latent 的 batch 对齐（蒙版是 1 张，latent 也是 1 张）
    if m.shape[0] != samples.shape[0]:
        m = m.expand(samples.shape[0], -1, -1, -1)
    return m
