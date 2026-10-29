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

#: 直调 API 的硬上限（防恶意/误用一次建百万格）。节点侧的 panels/columns/rows
#: 本来就只到 12；144 = 12×12，是「单轴 12 以内」能表达的最大格数。
MAX_PANELS = 144
MAX_AXIS = 12

#: spec 字符串与字段上限。本包自己生成的 spec 约 100 字符；超长或超大字段
#: 只可能来自手写/上游篡改，必须在分配任何张量之前拒绝。
MAX_SPEC_CHARS = 4096
MAX_PANEL_PX = 4096
MAX_GAP = 256

#: 整张画布的像素上限（模板图 / 空 latent 在分配前用它硬拦）。
#: 本包节点参数能产生的最大值约 29MP；64MP 留一倍余量，同时挡住
#: 12×12@4096（约 1368MP）这类会让宿主一次申请十几 GB 的极端 state。
MAX_CANVAS_PIXELS = 64_000_000


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

    # 🔴 近打平时按故事板惯例定序，而不是让 0.01 的差异决定排法。
    # 反例：单格长边 512 → 6 格选 2行3列（2.58:1）；长边改 496 → 变成 3行2列（1.21:1）。
    # 只因为两者对 16:9 的 log 距离是 0.37 vs 0.39 —— 用户只是把单格缩了 16px，
    # 排法却从"横向条带"翻成"竖块"。所以：在最优值 0.08 个 log 单位内视为打平，
    # 打平时横画幅优先"列更多"、竖画幅优先"行更多"。
    best = pool[0][0]
    near = [s for s in pool if s[0] <= best + 0.08]
    if pw >= ph:
        near.sort(key=lambda s: (-s[3], s[0]))
    else:
        near.sort(key=lambda s: (-s[2], s[0]))
    _, ar, rows, cols = near[0]
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

    @property
    def panel_megapixels(self):
        """单格有效像素（MP）。

        故事板的画质真因是**每格自身的像素量**，不是整图像素量：
        MOSS 在 1–2MP 训练时，每格只分到 `panel_megapixels`。
        9 格 1:1 时单格仅 0.26MP，远低于训练区间 ⇒ 每格细节明显糊。
        """
        return self.panel_w * self.panel_h / 1e6

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
    """把 `spec()` 的字符串还原成 `Layout`；不合法就抛 ValueError（不静默兜底）。

    spec 是**外部输入**（可来自任意上游节点），所以字段必须逐项限界：
    超长字符串、超大单格/缝/边距、单格与画布不是 16 的倍数、以及 `w/h` 与字段不自洽的 spec 都在这里拒绝，
    否则下游 `empty_latent` / `render_template` 会按伪造的尺寸申请 TB 级张量。
    """
    if not text or SPEC_PREFIX not in text:
        raise ValueError("spec 不是 %s 格式：%r" % (SPEC_PREFIX, (text or "")[:60]))
    if len(text) > MAX_SPEC_CHARS:
        raise ValueError("spec 有 %d 个字符，超过上限 %d；这不像本包生成的 spec。"
                         % (len(text), MAX_SPEC_CHARS))
    kv = {k: int(v) for k, v in _SPEC_RE.findall(text)}
    need = ("panels", "w", "h", "cols", "rows", "pw", "ph", "gutter", "margin")
    missing = [k for k in need if k not in kv]
    if missing:
        raise ValueError("spec 缺字段：%s" % ", ".join(missing))
    for key in ("pw", "ph"):
        if not MIN_PANEL <= kv[key] <= MAX_PANEL_PX:
            raise ValueError("spec.%s=%d 越界：必须在 %d..%d 像素（本包生成的 spec 不会超出）。"
                             % (key, kv[key], MIN_PANEL, MAX_PANEL_PX))
    # 16 的倍数：VAE 按 16 倍下采样，非整倍数会被静默截断（裁切框与内容错位）。
    # 节点自己算的几何一律 snap16，所以这条只在**手写 spec** 时才会触发 —— 那正是它要挡的。
    for key in ("pw", "ph", "w", "h"):
        if kv[key] % SNAP:
            raise ValueError(
                "spec.%s=%d 不是 %d 的倍数：MOSS 的 VAE 按 %d 倍下采样，非整倍数会被静默截断，"
                "裁切框与内容错位。请重新接一次上游节点的 spec，不要手写。"
                % (key, kv[key], SNAP, SNAP))
    for key in ("gutter", "margin"):
        if not 0 <= kv[key] <= MAX_GAP:
            raise ValueError("spec.%s=%d 越界：必须在 0..%d。" % (key, kv[key], MAX_GAP))
    for key in ("padx", "pady"):
        if not 0 <= kv.get(key, 0) < SNAP:
            raise ValueError("spec.%s=%d 越界：必须在 0..%d（对齐余量）。"
                             % (key, kv.get(key, 0), SNAP - 1))
    lay = build_layout(
        panels=kv["panels"], cols=kv["cols"], rows=kv["rows"],
        panel_w=kv["pw"], panel_h=kv["ph"],
        gutter=kv["gutter"], margin=kv["margin"],
        pad_x=kv.get("padx", 0), pad_y=kv.get("pady", 0), note="来自 spec",
    )
    if (lay.width, lay.height) != (kv["w"], kv["h"]):
        raise ValueError("spec 自相矛盾：字段算出的画布是 %d×%d，但 spec 写着 %d×%d。"
                         "请重新接一次上游节点的 spec。"
                         % (lay.width, lay.height, kv["w"], kv["h"]))
    return lay


def build_layout(panels, panel_w=None, panel_h=None, cols=0, rows=0, gutter=8, margin=8,
                 panel_edge=None, panel_aspect="16:9", canvas_aspect="auto",
                 pad_x=None, pad_y=None, note=""):
    """构造 `Layout`（唯一入口，下游全部从这里取坐标）。

    `cols`/`rows` 都为 0 时按 `canvas_aspect` 自动挑分解；只填一个时另一个由
    `panels` 反推。

    上限（直调 API 的防爆闸）：`panels <= 144`、`cols`/`rows <= 12`。常用
    3/4/6/8/9 不受影响；超过就明确报错，不分配。
    """
    panels = int(panels)
    if panels < 1:
        raise ValueError("panels 必须 >= 1（收到 %r）" % (panels,))
    if panels > MAX_PANELS:
        raise ValueError("panels=%d 超过上限 %d（单轴最多 %d 格）"
                         % (panels, MAX_PANELS, MAX_AXIS))
    cols, rows = int(cols), int(rows)
    if cols > MAX_AXIS or rows > MAX_AXIS:
        raise ValueError("cols/rows 不能超过 %d（收到 cols=%d rows=%d）"
                         % (MAX_AXIS, cols, rows))
    gutter = max(0, int(gutter))
    margin = max(0, int(margin))

    if panel_w is None or panel_h is None:
        if panel_edge is None:
            raise ValueError("必须给 panel_w/panel_h 或 panel_edge")
        ratio = ASPECTS.get(panel_aspect)
        if ratio is None:
            raise ValueError("未知单格宽高比：%r（可选 %s）" % (panel_aspect, ", ".join(ASPECTS)))
        panel_w, panel_h = panel_size(panel_edge, ratio)
    panel_w, panel_h = int(panel_w), int(panel_h)
    if panel_w < 1 or panel_h < 1:
        raise ValueError("单格尺寸必须为正（收到 %d×%d）" % (panel_w, panel_h))

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
    if cols > MAX_AXIS or rows > MAX_AXIS:
        raise ValueError("panels=%d 只能分解成 %d 列 × %d 行，超过单轴上限 %d；"
                         "请减少格数或显式指定 cols/rows"
                         % (panels, cols, rows, MAX_AXIS))
    if cols * rows != panels:
        raise ValueError("columns×rows=%d 与 panels=%d 不一致" % (cols * rows, panels))

    raw_w = cols * panel_w + (cols - 1) * gutter + 2 * margin
    raw_h = rows * panel_h + (rows - 1) * gutter + 2 * margin
    if pad_x is None:
        pad_x = (-raw_w) % SNAP
    if pad_y is None:
        pad_y = (-raw_h) % SNAP
    pad_x, pad_y = int(pad_x), int(pad_y)
    if pad_x < 0 or pad_y < 0:
        raise ValueError("对齐余量不能为负（收到 padx=%d pady=%d）" % (pad_x, pad_y))
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
