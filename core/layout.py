# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""宫格几何：本包唯一的坐标真相源（纯 Python，无 torch / 无 comfy 依赖）。

四条硬约束，每条都对应一个已发生的失败：

1. **画布与单格尺寸必须是 16 的倍数**。FLUX.2 latent 是 1/16 下采样
   （`comfy/latent_formats.py::Flux2.spacial_downscale_ratio = 16`），
   非 16 倍数的画布会被 VAE 静默截断，裁切框与实际内容错位。
2. **单格尺寸只能有一份**（`panel_w` / `panel_h`），不许每格各自 `round()`。
   旧版 `StoryboardPanelCrop` 按 `round(c*seg_w)` 逐格取整，2048 宽 3 列时得到
   683/682/683，`torch.cat` 当场抛 `Sizes of tensors must match`。
3. **余量只加在右/下外边距**。画布对齐 16 的差额（0–15px）塞进右边距与下边距，
   因为外边距与宫格线同色，肉眼不可见；单格尺寸因此永不被改动。
4. **必须有外框外边距**。旧版 `StoryboardGridGenerator` 把首/末行末列强制贴边，
   于是"边框"只存在于内部，裁切时最外圈切掉的是画面内容而不是线。
"""

from dataclasses import dataclass
import math
import re

SPEC_PREFIX = "MOSS-SB1"

#: 单格宽高比。键同时是画布选择器里的下拉项。
ASPECTS = {
    "16:9": 16.0 / 9.0,
    "3:2": 3.0 / 2.0,
    "4:3": 4.0 / 3.0,
    "1:1": 1.0,
    "4:5": 4.0 / 5.0,
    "3:4": 3.0 / 4.0,
    "2:3": 2.0 / 3.0,
    "9:16": 9.0 / 16.0,
    "21:9": 21.0 / 9.0,
    "2.39:1": 2.39,
}

#: 画布整体宽高比的目标档。`auto` = 按单格朝向取 16:9 或 9:16。
CANVAS_ASPECTS = ["auto", "16:9", "4:3", "1:1", "9:16", "21:9"]

#: 画布宽高比的容忍上限。故事板天然是长条：4×2 的 16:9 单格画布就是 ≈3.5:1，
#: 8 格竖排也有 ≈0.29:1。超过 4:1 时模型基本只在"一条"里画，故设 4.0。
MAX_CANVAS_RATIO = 4.0

SNAP = 16
MIN_PANEL = 64


def snap16(value):
    """取最近的 16 的倍数，下限 64。"""
    return max(MIN_PANEL, int(round(float(value) / SNAP)) * SNAP)


def panel_size(edge, ratio):
    """由长边像素与宽高比算单格尺寸（两边都对齐 16）。

    `edge` 指**长边**：横向画幅时是宽，纵向画幅时是高。
    """
    if ratio >= 1.0:
        return snap16(edge), snap16(round(edge / ratio))
    return snap16(round(edge * ratio)), snap16(edge)


def candidate_grids(panels):
    """所有 rows×cols == panels 的整数分解，从 (1, panels) 到 (panels, 1)。"""
    out = []
    for rows in range(1, panels + 1):
        if panels % rows == 0:
            out.append((rows, panels // rows))
    return out


def _canvas_ratio(grid, pw, ph, gutter, margin):
    rows, cols = grid
    w = cols * pw + (cols - 1) * gutter + 2 * margin
    h = rows * ph + (rows - 1) * gutter + 2 * margin
    return w / h


def pick_grid(panels, pw, ph, gutter, margin, canvas_aspect="auto"):
    """在整数分解里挑最接近目标画布比例的一档。

    返回 `(rows, cols, note)`；`note` 是给人看的一句话，写进 report。
    极端比例（>3:1）优先排除；若全部被排除则退回最接近的一档并出声。
    """
    grids = candidate_grids(panels)
    if len(grids) == 1:
        rows, cols = grids[0]
        return rows, cols, "唯一分解 %dx%d" % (cols, rows)

    if canvas_aspect in ASPECTS:
        target = ASPECTS[canvas_aspect]
        tname = canvas_aspect
    else:
        target = 16.0 / 9.0 if pw >= ph else 9.0 / 16.0
        tname = "16:9" if pw >= ph else "9:16"

    scored = []
    for rows, cols in grids:
        ar = _canvas_ratio((rows, cols), pw, ph, gutter, margin)
        scored.append((abs(math.log(ar / target)), ar, rows, cols))
    scored.sort()

    ok = [s for s in scored if (1.0 / MAX_CANVAS_RATIO) <= s[1] <= MAX_CANVAS_RATIO]
    pool = ok or scored
    _, ar, rows, cols = pool[0]
    note = "按画布比例 %s 选 %dx%d（实际 %.2f:1）" % (tname, cols, rows, ar)
    if not ok:
        note += "；⚠ 全部候选都超出 %.0f:1，已退让" % MAX_CANVAS_RATIO
    return rows, cols, note


@dataclass(frozen=True)
class Cell:
    """一个故事板格子。`x/y` 是左上角像素坐标，`w/h` 是内容区尺寸（不含缝）。"""

    index: int
    row: int
    col: int
    x: int
    y: int
    w: int
    h: int

    def bbox_px(self):
        return (self.y, self.x, self.y + self.h, self.x + self.w)


@dataclass(frozen=True)
class Layout:
    panels: int
    cols: int
    rows: int
    panel_w: int
    panel_h: int
    gutter: int
    margin: int
    width: int
    height: int
    pad_x: int
    pad_y: int
    note: str
    cells: tuple

    # ---- 序列化：单根线把所有几何一次性传给下游 ----
    def spec(self):
        return ("%s panels=%d w=%d h=%d cols=%d rows=%d pw=%d ph=%d gutter=%d margin=%d padx=%d pady=%d"
                % (SPEC_PREFIX, self.panels, self.width, self.height, self.cols, self.rows,
                   self.panel_w, self.panel_h, self.gutter, self.margin, self.pad_x, self.pad_y))

    @property
    def megapixels(self):
        return self.width * self.height / 1e6

    def cell(self, index):
        return self.cells[index]

    def boxes_abs_centered(self):
        """MOSS-Image 原生布局格式：`[y0, x0, y1, x1]`，原点在画布中心，像素单位。

        与 `comfy_extras/nodes_moss_image.py::MOSSImageLayoutPrompt` 的产出完全一致。
        """
        hx, hy = self.width // 2, self.height // 2
        return [[c.y - hy, c.x - hx, c.y + c.h - hy, c.x + c.w - hx] for c in self.cells]

    def boxes_1000(self):
        """0–1000 归一化的 `[y0, x0, y1, x1]`，供 `Create Bounding Boxes` 生态复用。"""
        out = []
        for c in self.cells:
            out.append([
                round(c.y * 1000.0 / self.height, 3),
                round(c.x * 1000.0 / self.width, 3),
                round((c.y + c.h) * 1000.0 / self.height, 3),
                round((c.x + c.w) * 1000.0 / self.width, 3),
            ])
        return out


_SPEC_RE = re.compile(r"([A-Za-z_]+)=(-?\d+)")


def parse_spec(text):
    """把 `spec()` 的字符串还原成 `Layout`；不合法就抛 ValueError（不静默兜底）。"""
    if not text or SPEC_PREFIX not in text:
        raise ValueError("spec 不是 %s 格式：%r" % (SPEC_PREFIX, (text or "")[:60]))
    kv = {k: int(v) for k, v in _SPEC_RE.findall(text)}
    need = ("panels", "w", "h", "cols", "rows", "pw", "ph", "gutter", "margin")
    missing = [k for k in need if k not in kv]
    if missing:
        raise ValueError("spec 缺字段：%s" % ", ".join(missing))
    return build_layout(
        panels=kv["panels"], cols=kv["cols"], rows=kv["rows"],
        panel_w=kv["pw"], panel_h=kv["ph"],
        gutter=kv["gutter"], margin=kv["margin"],
        pad_x=kv.get("padx", 0), pad_y=kv.get("pady", 0), note="来自 spec",
    )


def build_layout(panels, panel_w=None, panel_h=None, cols=0, rows=0, gutter=8, margin=8,
                 panel_edge=None, panel_aspect="16:9", canvas_aspect="auto",
                 pad_x=None, pad_y=None, note=""):
    """构造 `Layout`（唯一入口，下游全部从这里取坐标）。

    `cols`/`rows` 都为 0 时按 `canvas_aspect` 自动挑分解；只填一个时另一个由
    `panels` 反推。
    """
    panels = int(panels)
    if panels < 1:
        raise ValueError("panels 必须 >= 1（收到 %r）" % (panels,))
    gutter = max(0, int(gutter))
    margin = max(0, int(margin))

    if panel_w is None or panel_h is None:
        if panel_edge is None:
            raise ValueError("必须给 panel_w/panel_h 或 panel_edge")
        ratio = ASPECTS.get(panel_aspect)
        if ratio is None:
            raise ValueError("未知单格宽高比：%r（可选 %s）" % (panel_aspect, ", ".join(ASPECTS)))
        panel_w, panel_h = panel_size(panel_edge, ratio)

    cols, rows = int(cols), int(rows)
    if cols <= 0 and rows <= 0:
        rows, cols, note = pick_grid(panels, panel_w, panel_h, gutter, margin, canvas_aspect)
    elif cols <= 0:
        if panels % rows:
            raise ValueError("panels=%d 不能被 rows=%d 整除" % (panels, rows))
        cols = panels // rows
    elif rows <= 0:
        if panels % cols:
            raise ValueError("panels=%d 不能被 columns=%d 整除" % (panels, cols))
        rows = panels // cols
    if cols * rows != panels:
        raise ValueError("columns×rows=%d 与 panels=%d 不一致" % (cols * rows, panels))

    raw_w = cols * panel_w + (cols - 1) * gutter + 2 * margin
    raw_h = rows * panel_h + (rows - 1) * gutter + 2 * margin
    if pad_x is None:
        pad_x = (-raw_w) % SNAP
    if pad_y is None:
        pad_y = (-raw_h) % SNAP
    width = raw_w + pad_x
    height = raw_h + pad_y

    cells = []
    for r in range(rows):
        for c in range(cols):
            cells.append(Cell(index=r * cols + c, row=r, col=c,
                              x=margin + c * (panel_w + gutter),
                              y=margin + r * (panel_h + gutter),
                              w=panel_w, h=panel_h))

    return Layout(panels=panels, cols=cols, rows=rows, panel_w=panel_w, panel_h=panel_h,
                  gutter=gutter, margin=margin, width=width, height=height,
                  pad_x=pad_x, pad_y=pad_y, note=note, cells=tuple(cells))
