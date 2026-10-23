# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""采样层：MOSS-Image 的 latent 构造与单次采样。

全部调用宿主（ComfyUI）的公开内核，**不重写任何调度数学**：
`KSampler` 负责 denoise→sigma 的截断（含 `DISCARD_PENULTIMATE_SIGMA_SAMPLERS` 特例），
`sample_custom` 负责加噪与迭代。这样本节点的 denoise 语义与画布上的 KSampler
**逐位一致**，用户按老经验调 0.8 得到的就是 0.8。

MOSS-Image 事实（`comfy/latent_formats.py::Flux2`）：latent 128 通道、空间下采样 1/16，
所以画布宽高必须是 16 的倍数——这条已由 `core.layout` 强制。
"""

import torch


def empty_latent(model, width, height, batch=1):
    """空 latent：先造 4 通道再让宿主按模型 latent_format 扩到 128 通道。

    直接 `torch.zeros([B,128,...])` 会写死 MOSS，换成别家 16 通道模型就崩；
    走 `fix_empty_latent_channels` 才是与 `EmptyLatentImage` 同一条路径。
    """
    import comfy.model_management
    import comfy.sample

    if width % 16 or height % 16:
        raise ValueError("画布必须能被 16 整除（收到 %dx%d）" % (width, height))
    samples = torch.zeros([int(batch), 4, height // 16, width // 16],
                          device=comfy.model_management.intermediate_device())
    samples = comfy.sample.fix_empty_latent_channels(model, samples)
    return {"samples": samples}


def encode_image(vae, image):
    """把宫格模板编码成 latent（img2img 起点）。"""
    if image is None or image.ndim != 4:
        raise ValueError("image 必须是 [B, H, W, C]")
    return {"samples": vae.encode(image[:, :, :, :3])}


def encode_text(clip, text):
    """与 `CLIPTextEncode` 同一实现：tokenize → encode_from_tokens_scheduled。"""
    if clip is None:
        raise RuntimeError("clip 为 None：确认 CLIPLoader 的 type 选了 moss_image")
    return clip.encode_from_tokens_scheduled(clip.tokenize(text))


def sample(model, positive, negative, latent, steps, cfg, sampler_name, scheduler,
           denoise=1.0, seed=0, preview=True):
    """单次采样。返回 `{"samples": tensor}`。"""
    import comfy.samplers
    import comfy.sample
    import comfy.utils
    import comfy.model_management

    steps = int(steps)
    if steps < 1:
        raise ValueError("steps 必须 >= 1（收到 %d）" % steps)
    denoise = float(denoise)
    if not (0.0 < denoise <= 1.0):
        raise ValueError("denoise 必须在 (0, 1]（收到 %r）" % denoise)

    # 用宿主 KSampler 对象算 sigma：denoise<1 时它会先按 int(steps/denoise) 重算，
    # 再取末 steps+1 段 —— 这就是画布上 %降噪 的真实含义，别自己 slicing。
    ks = comfy.samplers.KSampler(model, steps=steps, device=model.load_device,
                                 sampler=sampler_name, scheduler=scheduler,
                                 denoise=denoise, model_options=model.model_options)
    sigmas = ks.sigmas
    if sigmas is None or len(sigmas) < 2:
        raise ValueError("denoise=%r 太小，sigma 序列不足两步；调高降噪或步数" % denoise)

    noise = comfy.sample.prepare_noise(latent, int(seed))
    sampler = comfy.samplers.sampler_object(sampler_name)

    cb = None
    if preview:
        try:
            import latent_preview
            cb = latent_preview.prepare_callback(model, max(1, sigmas.shape[-1] - 1))
        except Exception:
            cb = None  # 预览只是锦上添花，不许把采样搞挂

    samples = comfy.sample.sample_custom(
        model, noise, cfg, sampler, sigmas, positive, negative, latent,
        callback=cb, disable_pbar=not comfy.utils.PROGRESS_BAR_ENABLED, seed=int(seed),
    )
    return {"samples": samples}


def decode(vae, latent):
    return vae.decode(latent["samples"])


def latent_preview_shape(latent):
    """给 report 用的一行形状描述。"""
    s = latent.get("samples") if isinstance(latent, dict) else None
    return "x".join(str(v) for v in s.shape) if s is not None else "?"
