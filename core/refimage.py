# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
"""参考图进 vision tower：像素预算缩放 + `Picture N` 前缀 + 编码。

**为什么需要这一层。** 宿主 `TextEncodeMOSSImageEdit` 把每张参考图硬编码压到
384×384（0.147 MP）：

```python
scale = min(1.0, math.sqrt(384 * 384 / (image.shape[1] * image.shape[2])))
```

而 Qwen3.5 vision tower 的入口是 `max_pixels=12,845,056` —— 差 87 倍。
参考图里人脸只占画幅 1/10 时，进 vision 后约 10×10 像素：模型看得见「有个人」，
看不清「是谁」。实测该默认值下身份余弦 **0.008**（等于没给参考图）。

**本模块只做三件事**，全部走宿主公开 API（`clip.tokenize(images=...)` +
`node_helpers.conditioning_set_values`），不复制任何 GPL 源码：

1. 把参考图按像素预算缩放（**只降不升**，见 `scale_for`）；
2. 拼 `Picture N: <|vision_start|><|image_pad|><|vision_end|>` 前缀；
3. 编码成 `CONDITIONING`。

**不做 reference_latents。** 那条路（`edit_format` + `vae.encode` + ref 窗口）实测
在中间窗口全部更差，产线配方是把 ref_kv 关掉、只留 vision 通道
（`审计报告/无依赖换脸-结果-2026-10-28.md`）。本模块因此没有 ref 窗口参数。
"""

from __future__ import annotations

import math
import re

#: 像素预算的计量单位。**1e6，不是 1024²**：面板上的 1.05 要能读成「约 105 万像素」。
#: 用 1024² 会让同一个数字表示不同的像素量，而用户看到的旋钮值就是 1.05。
PIXELS_PER_MEGAPIXEL = 1_000_000

#: 宿主默认预算 = 384×384。**本包不把它当默认值**，只当「身份归零」的参照点。
STOCK_MEGAPIXELS = 384 * 384 / PIXELS_PER_MEGAPIXEL

#: 实测安全线：0.59 MP ≈ 768²。低于它身份明显掉。
SAFE_MEGAPIXELS = 0.59

#: 实测塌陷点：0.30 MP 会**变成另一个人**（余弦为负）。只用于告警文案。
TRAP_MEGAPIXELS = 0.30

#: 缩放后的最短边下限。太小会让 vision 侧 patch 数不足，直接报错。
MIN_SIDE = 32


def scale_for(width, height, megapixels):
    """算缩放系数。返回 `1.0` 表示不缩放。

    🔴 **只降不升**：`min(1.0, ...)`。参考图本身比预算小时放大没有任何好处
    （实测 1.05 / 1.5 / 2.0 三个预算在同一张 1024×576 参考图上结果完全相同），
    却会白烧一遍重采样。所以「调大预算但画面没变」是正常现象 —— 源图不够大。
    """
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("参考图尺寸非法：%dx%d" % (width, height))
    budget = float(megapixels)
    if not math.isfinite(budget):
        raise ValueError("vision_megapixels 必须是有限数，收到 %r" % (megapixels,))
    if budget <= 0.0:
        return 1.0                      # 0 = 原生分辨率，不缩放
    return min(1.0, math.sqrt(budget * PIXELS_PER_MEGAPIXEL / (width * height)))


def budget_notes(megapixels, report):
    """预算相关的告警行。**返回文案而不是自己打日志**：节点要把它们写进 `report`，
    用户看不到控制台。"""
    notes = []
    value = float(megapixels)
    if report["scale"] >= 1.0 and value > 0:
        notes.append(
            "参考图 %d×%d 比预算小，**没有缩放**（预算只降不升）——"
            "调大 vision_megapixels 在这张图上不会有任何变化。"
            % (report["native"][0], report["native"][1]))
    if 0.0 < value < SAFE_MEGAPIXELS - 1e-3:
        if abs(value - STOCK_MEGAPIXELS) < 1e-3:
            notes.append(
                "vision_megapixels=%.4f 是**宿主默认值**，此值下参考图几乎不起作用"
                "（实测身份余弦约 0.008）。换脸请显式调到 %.2f。"
                % (value, SAFE_MEGAPIXELS))
        elif value < TRAP_MEGAPIXELS:
            notes.append(
                "vision_megapixels=%.2f 低于实测安全线 %.2f，身份会明显掉。"
                % (value, SAFE_MEGAPIXELS))
        else:
            notes.append(
                "vision_megapixels=%.2f 落在**未验证的塌陷带**（实测 0.30 会变成另一个人）。"
                "要么 ≥%.2f，要么 0（原生）。" % (value, SAFE_MEGAPIXELS))
    return notes


def picture_prefix(count):
    """宿主同款前缀：`Picture 1: <|vision_start|><|image_pad|><|vision_end|>…`

    每张参考图对应一个 `<|image_pad|>`，tokenizer 按它把视觉 token 插进去。
    少一个 ⇒ 图被丢掉；多一个 ⇒ tokenize 报错。所以数量必须与 `images=` 严格一致。
    """
    count = int(count)
    if count < 0:
        raise ValueError("参考图数量不能为负（收到 %d）" % count)
    return "".join(
        "Picture {}: <|vision_start|><|image_pad|><|vision_end|>".format(i + 1)
        for i in range(count)
    )


def strip_prefix(text):
    """剥掉用户可能自己写的 `Picture N:` 前缀（含视觉 token）。返回 `(文本, 剥了几条)`。

    为什么必须剥：前缀里的 `<|image_pad|>` 是 tokenizer 插视觉 token 的锚点，
    数量必须与 `images=` 一致。用户照抄文档写一句 `Picture 1:`，不剥就变成两条，
    轻则 tokenizer 报错、重则图被丢进错误位置。
    """
    pattern = re.compile(
        r"^\s*(?:Picture\s+\d+\s*:\s*"
        r"(?:<\|vision_start\|><\|image_pad\|><\|vision_end\|>)?\s*)+")
    match = pattern.match(text)
    if not match:
        return text, 0
    return text[match.end():], text[:match.end()].count("Picture")


def fit_reference(image, megapixels):
    """把一张参考图缩到预算内。返回 `(张量, 报告)`。

    入参 `image` 是 ComfyUI 的 IMAGE：`[B, H, W, C]`，float 0–1。
    出参张量固定 `B=1`：换脸只该给**一张**参考图 —— 实测双视角比单张更差
    （0.384 vs 0.463，视觉 token 被摊薄）。
    """
    import comfy.utils

    if image is None:
        raise ValueError("参考图为空：请把身份参考图接到 ref")
    if image.ndim != 4:
        raise ValueError("参考图必须是 [B, H, W, C]（收到 %d 维）" % image.ndim)
    if image.shape[0] < 1:
        raise ValueError("参考图批次为空")
    dropped = int(image.shape[0])
    if dropped > 1:
        # 不抛错：取第一张并说明。换脸场景下多张一定是接错了批次，
        # 抛错会逼用户先去加一个「取第一张」的节点，成本更高。
        image = image[:1]

    height, width = int(image.shape[1]), int(image.shape[2])
    scale = scale_for(width, height, megapixels)
    target_w = max(MIN_SIDE, int(round(width * scale)))
    target_h = max(MIN_SIDE, int(round(height * scale)))
    if (target_w, target_h) != (width, height):
        image = comfy.utils.common_upscale(
            image.movedim(-1, 1), target_w, target_h, "area", "disabled"
        ).movedim(1, -1)

    report = {
        "native": (width, height),
        "native_megapixels": round(width * height / PIXELS_PER_MEGAPIXEL, 4),
        "target": (target_w, target_h),
        "target_megapixels": round(target_w * target_h / PIXELS_PER_MEGAPIXEL, 4),
        "scale": round(scale, 4),
        "batch_dropped": dropped,
    }
    return image, report


def encode(clip, prompt, reference, megapixels):
    """参考图进 vision，编码成 CONDITIONING。返回 `(conditioning, 报告)`。

    `prompt` 里**不需要**自己写 `Picture 1:` 前缀 —— 这里会拼。
    自己写了也不会变成两条：开头的 `Picture N:` 与视觉 token 会被**剥掉再统一拼一条**。
    理由：`<|image_pad|>` 的个数必须与 `images=` 严格一致 ——
    多一个 tokenizer 直接报错，少一个图被静默丢掉。
    """
    if clip is None:
        raise RuntimeError("clip 为 None：确认 CLIPLoader 的 type 选了 moss_image")

    ref, report = fit_reference(reference, megapixels)
    text, stripped = strip_prefix(str(prompt or ""))
    report["prefix_stripped"] = stripped
    tokens = clip.tokenize(picture_prefix(1) + text, images=[ref])
    conditioning = clip.encode_from_tokens_scheduled(tokens)
    report["notes"] = budget_notes(megapixels, report)
    return conditioning, report
