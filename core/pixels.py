# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""像素层：宫格模板、边框复绘、单格裁切。只依赖 torch。

模型不保证把宫格线画直（它又不是矢量排版软件）。所以策略是"两段式锁定"：
- **前段**：模板 latent 初始化，让几何结构从第一步就存在；
- **后段**：解码后按 `Layout` 的坐标**重新画一遍线**（`composite_borders`），
  这样线宽、位置、直度 100% 确定，且不依赖模型发挥。
"""

import torch
import torch.nn.functional as F


def _border_mask(layout, thickness=None, device=None, dtype=torch.float32):
    """1 = 需要画线的像素（外框 + 内缝），0 = 单格内容区。

    `thickness` 为 None 时铺满整条缝和外边距；给具体像素时在缝里居中画那么宽。
    """
    mask = torch.zeros((layout.height, layout.width), dtype=dtype, device=device)
    t = layout.gutter if thickness is None else max(0, min(int(thickness), layout.gutter))
    # 外框：四条外边距（右/下含 16 对齐余量，与边距同色，肉眼不可见）
    mask[: layout.margin, :] = 1.0
    mask[layout.height - layout.margin - layout.pad_y:, :] = 1.0
    mask[:, : layout.margin] = 1.0
    mask[:, layout.width - layout.margin - layout.pad_x:] = 1.0
    # 内缝：竖缝 / 横缝
    for c in range(1, layout.cols):
        x0 = layout.margin + c * layout.panel_w + (c - 1) * layout.gutter
        off = (layout.gutter - t) // 2
        mask[:, x0 + off: x0 + off + t] = 1.0
    for r in range(1, layout.rows):
        y0 = layout.margin + r * layout.panel_h + (r - 1) * layout.gutter
        off = (layout.gutter - t) // 2
        mask[y0 + off: y0 + off + t, :] = 1.0
    return mask


def render_template(layout, cell_gray=25, invert=False):
    """宫格模板：单格填灰、缝与外框填白。返回 `[1, H, W, 3]` float32，CPU。

    这是给 img2img 用的**几何先验**，不是给人看的图：
    - 单格灰度取近黑（默认 25/255）比中灰好——中灰会被模型当成"要保留的底色"，
      导致成片整体发灰；近黑则天然被当成待绘制的空区。
    - 缝是纯白，任何 denoise 档位下都比周围更"硬"，最先被保住。
    """
    gray = max(0, min(255, int(cell_gray))) / 255.0
    plane = torch.full((layout.height, layout.width), 1.0, dtype=torch.float32)
    for cell in layout.cells:
        plane[cell.y: cell.y + cell.h, cell.x: cell.x + cell.w] = gray
    if invert:
        plane = 1.0 - plane
    return plane[None, :, :, None].repeat(1, 1, 1, 3).contiguous()


def composite_borders(image, layout, thickness=None, level=1.0):
    """在解码结果上按几何坐标复绘宫格线。支持任意 batch，自动跟随输入设备/精度。"""
    if image is None or image.ndim != 4:
        raise ValueError("image 必须是 [B, H, W, C]（收到 %r）" % (getattr(image, "shape", None),))
    b, h, w, c = image.shape
    if (h, w) != (layout.height, layout.width):
        raise ValueError("图像尺寸 %dx%d 与布局 %dx%d 不一致；请确认 layout 与采样画布同源"
                         % (w, h, layout.width, layout.height))
    mask = _border_mask(layout, thickness, device=image.device, dtype=image.dtype)[:, :, None]
    line = torch.full_like(image, level)
    return image * (1.0 - mask) + line * mask


def crop_panels(image, layout, shave=0, target_aspect=None, padding_mode="crop", resize_edge=0):
    """按 `Layout` 精确裁成 N 格，返回 `[N, h, w, C]`。

    - `shave`：每格四边各内缩的像素（默认 0；边框已被复绘在缝里，正常不需要）。
    - `target_aspect`：`None` 或 "keep" 表示保持单格原比例；给具体值（如 16/9）时，
      `padding_mode="crop"` 中心裁切 / `"pad"` 补边（补边色 = 0，即黑）。
    - `resize_edge`：>0 时把每格长边缩放到该值（默认 0 = 不缩放，避免无谓重采样）。
    """
    if image is None or image.ndim != 4:
        raise ValueError("image 必须是 [B, H, W, C]")
    b, h, w, c = image.shape
    if (h, w) != (layout.height, layout.width):
        raise ValueError("图像尺寸 %dx%d 与布局 %dx%d 不一致" % (w, h, layout.width, layout.height))

    out = []
    for bi in range(b):
        frame = image[bi]
        for cell in layout.cells:
            s = max(0, int(shave))
            x0, y0 = cell.x + s, cell.y + s
            x1, y1 = cell.x + cell.w - s, cell.y + cell.h - s
            if x1 - x0 < 16 or y1 - y0 < 16:
                raise ValueError("第 %d 格内缩 %dpx 后不足 16px；把 shave 调小" % (cell.index + 1, s))
            patch = frame[y0:y1, x0:x1, :]

            if target_aspect:
                ratio = float(target_aspect)
                ch, cw = patch.shape[0], patch.shape[1]
                if abs(cw / ch - ratio) > 1e-3:
                    if padding_mode == "pad":
                        if cw / ch > ratio:
                            th = int(round(cw / ratio))
                            patch = F.pad(patch.permute(2, 0, 1)[None], (0, 0, 0, th - ch))[0].permute(1, 2, 0)
                        else:
                            tw = int(round(ch * ratio))
                            patch = F.pad(patch.permute(2, 0, 1)[None], (0, tw - cw))[0].permute(1, 2, 0)
                    else:
                        if cw / ch > ratio:
                            nw = max(16, int(round(ratio * ch)))
                            x = (cw - nw) // 2
                            patch = patch[:, x:x + nw, :]
                        else:
                            nh = max(16, int(round(cw / ratio)))
                            y = (ch - nh) // 2
                            patch = patch[y:y + nh, :, :]

            if resize_edge and resize_edge > 0:
                ch, cw = patch.shape[0], patch.shape[1]
                if cw >= ch:
                    tw, th = int(resize_edge), max(16, int(round(resize_edge * ch / cw)))
                else:
                    th, tw = int(resize_edge), max(16, int(round(resize_edge * cw / ch)))
                if (tw, th) != (cw, ch):
                    patch = F.interpolate(patch.permute(2, 0, 1)[None], size=(th, tw),
                                          mode="bicubic", align_corners=False)[0].permute(1, 2, 0)
                    patch = torch.clamp(patch, 0.0, 1.0)

            out.append(patch.unsqueeze(0))

    sizes = {tuple(t.shape[1:]) for t in out}
    if len(sizes) > 1:
        raise RuntimeError("各格尺寸不一致 %s；这不该发生，请把 layout 与图像一起贴进 issue" % (sorted(sizes),))
    return torch.cat(out, dim=0)


def guide_preview(image, layout, thickness=None):
    """给 `MossStoryboardCrop` 用的校准预览：在原图上把裁切线画成纯红。

    与裁切共用同一份 `Layout`，所以"预览线位置 == 实际裁切位置"，
    不会出现旧版那种预览用未 clamp 坐标、裁切用已 clamp 坐标的分叉。
    """
    if image is None or image.ndim != 4:
        raise ValueError("image 必须是 [B, H, W, C]")
    b, h, w, c = image.shape
    if (h, w) != (layout.height, layout.width):
        raise ValueError("图像尺寸 %dx%d 与布局 %dx%d 不一致" % (w, h, layout.width, layout.height))
    mask = _border_mask(layout, thickness, device=image.device, dtype=image.dtype)[:, :, None]
    red = torch.zeros_like(image)
    red[..., 0] = 1.0
    return image * (1.0 - mask) + red * mask
