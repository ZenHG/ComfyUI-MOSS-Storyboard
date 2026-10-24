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


def assemble_grid(panels, layout, order=None, border=None, level=1.0, background=0.0):
    """把 N 张单格图按 `Layout` 坐标拼成整张宫格，返回 `[1, H, W, C]`。

    逐格生成的工作流用它做拼版：每格独立采样（各自足量分辨率、跨格角色一致），
    最后按几何坐标拼起来。

    - `panels`：`[N, h, w, C]` 的张量（或 IMAGE batch），顺序 = 格号。
    - `order`：可选，长度 N 的排列，指定"第 i 张图放进第 order[i] 格"。
      默认顺序放入（0→格0, 1→格1, …）。
    - `border`：>0 时在缝里画宽 `border` 像素的宫格线（`level` 控制亮度）。
    - `background`：没被填满的格的底色（默认 0 = 黑）。
    """
    if panels is None:
        raise ValueError("panels 为 None")
    if panels.ndim != 4:
        raise ValueError("panels 必须是 [N, h, w, C]（收到 %r）" % (getattr(panels, "shape", None),))
    n, ph, pw, pc = panels.shape
    if n != layout.panels:
        raise ValueError("格数不符：panels 有 %d 张，布局要 %d 格" % (n, layout.panels))

    order = list(range(n)) if order is None else list(order)
    if sorted(order) != list(range(n)):
        raise ValueError("order 必须是 0..%d 的排列（收到 %r）" % (n - 1, order))

    device, dtype = panels.device, panels.dtype
    out = torch.full((1, layout.height, layout.width, pc), float(background),
                     device=device, dtype=dtype)
    for src_idx, cell_idx in enumerate(order):
        cell = layout.cell(cell_idx)
        img = panels[src_idx]
        # 尺寸不符就中心裁切（不缩放，避免拉伸变形）
        ih, iw = img.shape[0], img.shape[1]
        if (ih, iw) != (cell.h, cell.w):
            ih2, iw2 = min(ih, cell.h), min(iw, cell.w)
            top = (ih - ih2) // 2
            left = (iw - iw2) // 2
            img = img[top:top + ih2, left:left + iw2]
            if ih2 != cell.h or iw2 != cell.w:
                pad = torch.full((cell.h, cell.w, pc), float(background),
                                 device=device, dtype=dtype)
                pad[:ih2, :iw2] = img
                img = pad
        out[0, cell.y:cell.y + cell.h, cell.x:cell.x + cell.w, :] = img

    if border and border > 0:
        out = composite_borders(out, layout, thickness=border, level=level)
    return out


def assemble_panels(panels, layout, reborder=True, border_px=0, shave=0):
    """逐格拼版 → 可选复绘宫格线 → 裁格。

    与整张生成共用同一套 `reborder` 语义：`False` 时一条线都不画；
    `True` 时按 `border_px`（0 = 铺满整条缝）复绘。
    """
    grid = assemble_grid(panels, layout)
    if reborder:
        grid = composite_borders(grid, layout, thickness=(border_px or None))
    return grid, crop_panels(grid, layout, shave=shave)


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


def paste_panel(grid, layout, index, panel, shave=0):
    """把一张改过的单格写回宫格的第 `index` 格，返回 `(新宫格, 信息行列表)`。

    `crop_panels` 的逆运算。关键性质：

    - **只动目标格的内容区**，缝与外边距一个字节都不碰 —— 宫格线是后画的，
      贴回不该把它擦掉，也不需要重画。
    - `shave` 必须与当初裁切时一致：裁切缩了 s 像素，贴回就写在缩过的矩形里，
      否则内容会整体偏移 s 像素，而且不报错。
    - 尺寸不符时**重采样到格子精确尺寸**并明确告知（ComfyUI 的 IMAGE 没有
      "差不多就行"这回事，差 1 像素下游拼接就错位）。
    - 宫格是 `[B, H, W, C]`；`panel` 是 `[1, h, w, C]` 或 `[h, w, C]`。
    """
    if grid is None or grid.ndim != 4:
        raise ValueError("grid 必须是 [B, H, W, C]")
    if panel is None or panel.ndim not in (3, 4):
        raise ValueError("panel 必须是 [h, w, C] 或 [1, h, w, C]")
    if int(index) < 0 or int(index) >= layout.panels:
        raise ValueError("index=%d 越界：本布局共 %d 格（0 起）" % (int(index), layout.panels))
    if panel.ndim == 3:
        panel = panel.unsqueeze(0)
    b, gh, gw, gc = grid.shape
    if (gh, gw) != (layout.height, layout.width):
        raise ValueError("宫格尺寸 %dx%d 与布局 %dx%d 不一致" % (gw, gh, layout.width, layout.height))
    if grid.shape[0] != 1:
        raise ValueError("宫格批次应为 1，收到 %d；请先取单张" % b)

    cell = layout.cell(int(index))
    s = max(0, int(shave))
    x0, y0 = cell.x + s, cell.y + s
    x1, y1 = cell.x + cell.w - s, cell.y + cell.h - s
    if x1 - x0 < 16 or y1 - y0 < 16:
        raise ValueError("第 %d 格内缩 %dpx 后不足 16px；把 shave 调小" % (cell.index + 1, s))

    notes = []
    if panel.shape[0] != 1:
        raise ValueError("panel 批次应为 1，收到 %d；一次只贴一格" % panel.shape[0])
    src = panel[0]
    if src.shape[-1] != gc:
        raise ValueError("panel 通道数 %d 与宫格 %d 不一致" % (src.shape[-1], gc))
    ph, pw = int(src.shape[0]), int(src.shape[1])
    if (ph, pw) != (y1 - y0, x1 - x0):
        # 重采样到格子的精确尺寸；bicubic 之后必须 clamp，否则会出现越界值
        src = F.interpolate(src.permute(2, 0, 1)[None], size=(y1 - y0, x1 - x0),
                            mode="bicubic", align_corners=False)[0].permute(1, 2, 0)
        src = torch.clamp(src, 0.0, 1.0)
        notes.append("⚠ 输入 %dx%d ≠ 格子 %dx%d，已重采样（bicubic）。"
                     "要逐像素一致：Crop 的 resize_edge=0、target_aspect=keep、shave 与本节点相同"
                     % (pw, ph, x1 - x0, y1 - y0))
    else:
        notes.append("输入 %dx%d = 格子 %dx%d，逐像素贴入，无重采样" % (pw, ph, x1 - x0, y1 - y0))

    out = grid.clone()
    out[0, y0:y1, x0:x1, :] = src.to(dtype=out.dtype)
    notes.append("贴回第 %d 格（0 起，行主序）= 第 %d 行第 %d 列，区域 x %d–%d / y %d–%d"
                 % (cell.index, cell.row + 1, cell.col + 1, x0, x1, y0, y1))
    notes.append("画布 %dx%d 未变；缝与外边距未触碰（宫格线保持原样）" % (gw, gh))
    return out, notes


def paste_marker(grid, layout, index, shave=0, thickness=4):
    """把第 `index` 格的区域框成纯红，用于目检"到底贴进了哪一格"。

    与 `paste_panel` 用同一套坐标，所以红线位置就是实际写入位置 —— 不会出现
    "预览画在 A、实际写在 B"的分叉。`show_guides=False` 时节点直接返回原图，
    不走这里。
    """
    if grid is None or grid.ndim != 4:
        raise ValueError("grid 必须是 [B, H, W, C]")
    cell = layout.cell(int(index))
    s = max(0, int(shave))
    x0, y0 = cell.x + s, cell.y + s
    x1, y1 = cell.x + cell.w - s, cell.y + cell.h - s
    t = max(1, int(thickness))
    out = grid.clone()
    x1c, y1c = min(x1, out.shape[2]), min(y1, out.shape[1])
    out[:, y0:min(y0 + t, y1c), x0:x1c, 0] = 1.0
    out[:, y0:min(y0 + t, y1c), x0:x1c, 1:] = 0.0
    out[:, max(y1c - t, y0):y1c, x0:x1c, 0] = 1.0
    out[:, max(y1c - t, y0):y1c, x0:x1c, 1:] = 0.0
    out[:, y0:y1c, x0:min(x0 + t, x1c), 0] = 1.0
    out[:, y0:y1c, x0:min(x0 + t, x1c), 1:] = 0.0
    out[:, y0:y1c, max(x1c - t, x0):x1c, 0] = 1.0
    out[:, y0:y1c, max(x1c - t, x0):x1c, 1:] = 0.0
    return out
