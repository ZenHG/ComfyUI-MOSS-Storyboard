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


_PROBED = {"ok": False}

#: 本包真正依赖的宿主符号。宿主升级若改了这些，报错必须说"改了什么"，
#: 而不是抛一个 AttributeError 让人自己猜（这是节点包最常见的跨版本死法）。
_HOST_API = (
    ("comfy.sample", "sample_custom"),
    ("comfy.sample", "prepare_noise"),
    ("comfy.sample", "fix_empty_latent_channels"),
    ("comfy.samplers", "KSampler"),
    ("comfy.samplers", "sampler_object"),
    ("comfy.model_management", "intermediate_device"),
)


def probe_host_api():
    """校验宿主公开内核是否齐全（幂等，只成功一次之后不再检查）。"""
    if _PROBED["ok"]:
        return
    missing = []
    for mod_name, attr in _HOST_API:
        try:
            mod = __import__(mod_name, fromlist=[attr])
        except Exception as exc:
            missing.append("%s（import 失败：%s）" % (mod_name, exc))
            continue
        if not hasattr(mod, attr):
            missing.append("%s.%s" % (mod_name, attr))
    if missing:
        raise RuntimeError(
            "宿主 ComfyUI 缺少本包依赖的内核：%s\n"
            "说明：这是宿主版本不匹配（本包要求 requires-comfyui >=0.42.0）。\n"
            "处理：升级 ComfyUI，或回退本包到与宿主匹配的版本。" % "、".join(missing))
    _PROBED["ok"] = True


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


def as_tensor(latent):
    """把 latent 统一成**张量**。

    🔴 这里是宿主 API 的一个反直觉点，踩过一次：`comfy.sample.prepare_noise()` 与
    `CFGGuider.sample()` 都直接对参数调 `latent.is_nested` / `.size()`，
    也就是它们要的是**张量**，不是 `{"samples": ...}` 这个字典。
    传字典的报错是 `'dict' object has no attribute 'is_nested'` —— 既没提 latent，
    也不提是哪个节点，很难反推。所以在边界处一次性摊平，并把错误提前成人话。
    """
    if isinstance(latent, dict):
        if "samples" not in latent:
            raise ValueError("latent 字典缺 'samples' 键（实际键：%s）" % ", ".join(latent))
        latent = latent["samples"]
    if not torch.is_tensor(latent):
        raise TypeError("latent 必须是张量或 {'samples': 张量}（收到 %s）" % type(latent).__name__)
    return latent


def effective_denoise(steps, denoise):
    """算出**真正生效**的降噪强度。

    宿主的 denoise 不是"按比例少走几步"，而是 `new_steps = int(steps/denoise)` 重算
    sigma 后再取末 `steps+1` 段。所以：

    - `int(steps/denoise) == steps` ⇒ **一段都没截**，等价 denoise=1.0；
    - 只有 `int(steps/denoise) > steps` 才是真 img2img。

    4 步时 `int(4/0.85)=4`、`int(4/0.9)=4`、`int(4/0.99)=4` —— 0.8 以上全部**静默无效**。
    用户以为在调"保留模板的程度"，实际什么都没发生。这个函数就是为了不再撒这个谎。
    """
    steps = int(steps)
    denoise = float(denoise)
    if denoise >= 1.0:
        return 1.0
    if steps <= 0:
        return denoise
    return denoise if int(steps / denoise) > steps else 1.0


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

    latent_t = as_tensor(latent)

    # 用宿主 KSampler 对象算 sigma：denoise<1 时它会先按 int(steps/denoise) 重算，
    # 再取末 steps+1 段 —— 这就是画布上 %降噪 的真实含义，别自己 slicing。
    ks = comfy.samplers.KSampler(model, steps=steps, device=model.load_device,
                                 sampler=sampler_name, scheduler=scheduler,
                                 denoise=denoise, model_options=model.model_options)
    sigmas = ks.sigmas
    if sigmas is None or len(sigmas) < 2:
        raise ValueError("denoise=%r 太小，sigma 序列不足两步；调高降噪或步数" % denoise)

    noise = comfy.sample.prepare_noise(latent_t, int(seed))
    sampler = comfy.samplers.sampler_object(sampler_name)

    cb = None
    if preview:
        try:
            import latent_preview
            cb = latent_preview.prepare_callback(model, max(1, sigmas.shape[-1] - 1))
        except Exception:
            cb = None  # 预览只是锦上添花，不许把采样搞挂

    samples = comfy.sample.sample_custom(
        model, noise, cfg, sampler, sigmas, positive, negative, latent_t,
        callback=cb, disable_pbar=not comfy.utils.PROGRESS_BAR_ENABLED, seed=int(seed),
    )
    return {"samples": samples}


def decode(vae, latent):
    return vae.decode(as_tensor(latent))


def latent_preview_shape(latent):
    """给 report 用的一行形状描述。"""
    s = latent.get("samples") if isinstance(latent, dict) else None
    return "x".join(str(v) for v in s.shape) if s is not None else "?"
