# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""图像预处理：把任意图片变成 VLM 吃得下、且 token 不爆的 data URL。

三条纪律（来自 slmonker media.py 的实测，照抄）：
1. 取 batch 第 0 帧——动图/多帧只反推第一帧，多帧会把 token 打满。
2. Lanczos 缩到最长边 1536。超过这个尺寸 VLM 看不出更多，只是烧 token。
3. JPEG quality=95、subsampling=0（4:4:4）。4:2:0 会把小字糊掉，而图内文字是反推的核心目标。

PIL 缺失时降级为原图直传，并在 notes 里**出声**，不静默。

## 为什么在父包

节点（`moss_llm/nodes.py`）与编译器（`moss_llm/smith/`）都要做「把图喂给 VLM」这件事。
以前是两份：节点把 ComfyUI 的 IMAGE 张量按**原始尺寸**编成 PNG（1536×1024 的图直接
一两千 token），编译器那边缩到 1536 再编 JPEG。同一件事两个尺度，报告里还看不出来。
现在只有这一份，`smith/vision.py` 转发到这里；张量入口是 `prepare_tensor`。
"""

import base64
import io
from pathlib import Path

#: 最长边上限。再大 VLM 也不会多看到细节。
MAX_SIDE = 1536

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".jfif": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp",
         ".gif": "image/gif", ".tif": "image/tiff", ".tiff": "image/tiff",
         ".avif": "image/avif", ".heic": "image/heic"}


class Prepared:
    """预处理结果。`notes` 会原样进 provenance，用户能看见降级了什么。"""

    def __init__(self, data_url, width, height, notes, source):
        self.data_url = data_url
        self.width = width
        self.height = height
        self.notes = notes
        self.source = source

    @property
    def aspect_ratio(self):
        if not self.width or not self.height:
            return None
        r = self.width / float(self.height)
        # 常见比例收敛。1536x914 直接约分会得到 "768:457"，那种串对模型毫无意义。
        for cand, (cw, ch) in {"1:1": (1, 1), "4:3": (4, 3), "3:2": (3, 2),
                               "16:9": (16, 9), "16:10": (16, 10), "5:3": (5, 3),
                               "2:1": (2, 1), "21:9": (21, 9),
                               "9:16": (9, 16), "2:3": (2, 3), "3:4": (3, 4),
                               "3:5": (3, 5), "4:5": (4, 5), "1:2": (1, 2)}.items():
            if abs(r - cw / ch) < 0.02:
                return cand
        # 落不进常见表时，用最接近的小分母分数——比 "15:9" 这种四舍五入可读得多
        from fractions import Fraction
        f = Fraction(self.width, self.height).limit_denominator(20)
        return "%d:%d" % (f.numerator, f.denominator)

    def __repr__(self):
        return "<Prepared %sx%s %s>" % (self.width, self.height, self.source)


def _flatten(im, notes):
    """把任意模式归到 8bit RGB，透明通道合成到**白底**。

    为什么不直接 `convert("RGB")`：RGBA→RGB 只是丢掉 alpha 通道，透明像素
    保留它原来的 RGB（通常是 0,0,0）。贴纸、logo、免抠素材这类图会变成
    「白底变黑底」，VLM 于是老老实实描述成「黑色背景」——错得很有说服力。
    """
    from PIL import Image  # type: ignore

    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")
    if im.mode in ("RGBA", "LA", "PA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im.convert("RGB"), mask=im.convert("RGBA").split()[-1])
        notes.append("透明通道已合成到白底（直接丢 alpha 会变成黑底）")
        return bg
    if im.mode != "RGB":
        if im.mode not in ("L",):            # 灰度转 RGB 是等价变换，不值得占一行备注
            notes.append("色彩模式 %s → RGB" % im.mode)
        return im.convert("RGB")
    return im


def _encode(im, notes, max_side, quality):
    """缩到最长边 max_side 再编 JPEG。返回 (data_url, w, h)。"""
    from PIL import Image  # type: ignore

    w0, h0 = im.size
    im = _flatten(im, notes)
    scale = min(1.0, max_side / float(max(w0, h0)))
    if scale < 1.0:
        im = im.resize((max(1, int(round(w0 * scale))),
                        max(1, int(round(h0 * scale)))), Image.LANCZOS)
        notes.append("已 Lanczos 缩放 %dx%d → %dx%d" % (w0, h0, im.size[0], im.size[1]))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, subsampling=0)   # 纪律 3
    w, h = im.size
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), w, h


def prepare(path, max_side=MAX_SIDE, quality=95):
    """文件 → data URL。读不到 PIL 就原图直传（并出声）。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("找不到图片：%s" % p)
    notes = []

    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        raw = p.read_bytes()
        mime = _MIME.get(p.suffix.lower(), "image/png")
        notes.append("未安装 Pillow：原图直传，未缩放也未重编码（可能烧 token）")
        return Prepared("data:%s;base64,%s" % (mime, base64.b64encode(raw).decode()),
                        None, None, notes, str(p))

    with Image.open(p) as im:
        try:
            im.seek(0)                       # 纪律 1：多帧只取第 0 帧
        except EOFError:
            pass
        # 手机照片的方向记在 EXIF 里，像素本身是躺着的。不转正，VLM 看到的是侧倒的图，
        # 而且宽高跟着错——竖图被当成横图，比例也就错了。
        try:
            size_before = im.size
            turned = ImageOps.exif_transpose(im)
            if turned is not None:
                im = turned
            if im.size != size_before:
                notes.append("已按 EXIF 方向转正 %dx%d → %dx%d"
                             % (size_before + im.size))
        except Exception:                    # 坏 EXIF 不该让整次反推失败
            pass
        data_url, w, h = _encode(im, notes, max_side, quality)
    return Prepared(data_url, w, h, notes, str(p))


def _to_uint8_array(image, index=0):
    """ComfyUI 的 IMAGE 张量 `(B,H,W,C)` float0..1 → `(H,W,C)` uint8。

    先认 torch 张量，再退 numpy。**不能只按「torch 装没装」分流**：装了 torch 但传进来
    是 numpy 数组时，会走到 `array.to(torch.uint8)` 上炸——而调用方看到的是
    「预处理失败」，与真实原因无关。
    """
    try:
        import torch
    except ImportError:
        torch = None
    if torch is not None and isinstance(image, torch.Tensor):
        t = image.detach().to("cpu").float().clamp(0.0, 1.0)
        if t.ndim == 4:
            t = t[index]
        return (t * 255.0).round().to(torch.uint8).numpy()
    import numpy as np
    a = np.asarray(image, dtype="float32")
    if a.ndim == 4:
        a = a[index]
    return np.clip(np.round(a * 255.0), 0, 255).astype("uint8")


def prepare_tensor(image, index=0, max_side=MAX_SIDE, quality=95):
    """ComfyUI IMAGE 张量 → data URL。

    与 `prepare()` 同一套纪律（缩放 + JPEG 444），只是入口是张量不是文件。
    以前节点直接把整张图编成 PNG 发出去，图内小字反而更容易被 4:2:0 糊掉——
    编码格式选错了方向。
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError as e:                 # pragma: no cover - ComfyUI 必带 Pillow
        raise RuntimeError("缺少 Pillow，无法把图像张量编码成 data URL：%s" % e) from e
    arr = _to_uint8_array(image, index)
    notes = []
    im = Image.fromarray(arr)
    data_url, w, h = _encode(im, notes, max_side, quality)
    return Prepared(data_url, w, h, notes, "tensor[%d]" % index)

