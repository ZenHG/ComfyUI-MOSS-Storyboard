# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""预设：一键生成初始元素框。

**唯一真相源是 `docs/11-editor-ui.md` §五**（前端线维护）。本文件必须与它逐项对齐；
要改先在那份文档里改，再同步到这里。

设计原则：
- 预设只是**起点**，不是约束。用户选完可以随便改框、改描述、删元素。
- `stack` 刻意重叠（数组顺序 = z 序），其余预设的元素框不重叠。
- 文字元素必须同时给 `caption`（长什么样）与 `text`（实际渲染的字），**不许互相兜底**。
"""

import math
import uuid

from . import editor as E

#: 边距常量（与 docs/11 一致）
M = 0.03

#: 预设 → 元素模板。`bbox` 是 `[x0, y0, x1, y1]`，归一化。
#: `text` 只对 label=text 的元素有意义；四个文字元素都带 `palette`（给个可见起点）。
#: 值逐项对齐 `web/moss_sb_editor.js::PRESET_SPECS`（docs/11 §五 的唯一真相源）；
#: 前端 caption 是中英双语，这里取 `zh`。
PRESET_ITEMS = {
    "split-v": [
        {"label": "object", "caption": "主体",
         "bbox": [M, M, 0.485, 1 - M]},
        {"label": "text", "caption": "粗体无衬线标题",
         "text": "标题文字", "palette": ["#c8a15a"],
         "bbox": [0.515, M, 1 - M, 1 - M]},
    ],
    "split-h": [
        {"label": "object", "caption": "主体",
         "bbox": [M, M, 1 - M, 0.485]},
        {"label": "text", "caption": "细体说明文字",
         "text": "正文文字", "palette": ["#ffffff"],
         "bbox": [M, 0.515, 1 - M, 1 - M]},
    ],
    "title-body": [
        {"label": "text", "caption": "粗体无衬线标题，浅金色",
         "text": "标题文字", "palette": ["#c8a15a"],
         # y0 = 0.10：实测文字框贴顶（0.05）会被画布裁掉（docs/09 §4.3）
         "bbox": [0.08, 0.10, 0.92, 0.19]},
        {"label": "character", "caption": "主角半身，侧对镜头",
         "bbox": [0.10, 0.24, 0.90, 0.80]},
        {"label": "text", "caption": "细体小字，白色",
         "text": "副标题文字", "palette": ["#ffffff"],
         "bbox": [0.08, 0.84, 0.92, 0.90]},
    ],
    "three-col": [
        {"label": "object", "caption": "主体（左）", "bbox": [M, M, 0.33, 1 - M]},
        {"label": "object", "caption": "主体（中）", "bbox": [0.35, M, 0.65, 1 - M]},
        {"label": "object", "caption": "主体（右）", "bbox": [0.67, M, 1 - M, 1 - M]},
    ],
    "center": [
        {"label": "character", "caption": "主体，居中，四周留白",
         "bbox": [0.30, 0.18, 0.70, 0.82]},
    ],
    "stack": [
        {"label": "object", "caption": "背景元素",
         "bbox": [0.05, 0.06, 0.95, 0.72]},
        {"label": "character", "caption": "前景主体",
         "bbox": [0.28, 0.34, 0.72, 0.95]},
    ],
    "free": [],
}

#: 宫格默认尺寸（= 前端 `DEFAULT_GRID`）。
DEFAULT_GRID = {"cols": 4, "rows": 2, "panel_aspect": "16:9",
                "panel_edge": 512, "gutter": 8, "margin": 8}

#: 单张默认画布（= 前端 `DEFAULT_CANVAS`）。
DEFAULT_CANVAS = (1536, 864)

#: n=1 用的景别下标：中景（前端 `presetItems` 里写死 3）。
_MID = E.SIZES.index("中景")


def _grid_size_index(i, n):
    """格 i 的景别下标：与前端 `presetItems` 逐位一致。

    n>1 时 `index = floor(i*(len-1)/(n-1) + 0.5)` —— 这是 JS `Math.round`
    对非负数的一半向上语义。**不能用 Python `round()`**：它是银行家舍入，
    恰好落在 .5 时结果会与前端差一档。
    """
    if n <= 1:
        return _MID
    return int(math.floor(i * (len(E.SIZES) - 1) / (n - 1) + 0.5))


def _new_id(prefix="e"):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:8])


def build(preset="free", canvas=DEFAULT_CANVAS, grid=None):
    """按预设生成一份完整 state。

    默认画布 1536×864（与前端一致）。`grid` 只在 `preset="grid"` 时使用，
    默认 4 列 × 2 行；可传
    `{"cols":4, "rows":2, "panel_aspect":"16:9", "panel_edge":512, "gutter":8, "margin":8}`。
    """
    if preset not in E.PRESETS:
        raise ValueError("未知预设 %r，可选：%s。" % (preset, "、".join(E.PRESETS)))

    state = {"version": E.STATE_VERSION, "preset": preset,
             "style": "", "items": []}

    if preset == "grid":
        g = dict(DEFAULT_GRID)
        g.update(grid or {})
        state["grid"] = g
        cols, rows = int(g["cols"]), int(g["rows"])
        n = cols * rows
        for i in range(n):
            c, r = i % cols, i // cols
            state["items"].append({
                "id": _new_id("s"),
                "label": "object",
                "no": i + 1,
                "size": E.SIZES[_grid_size_index(i, n)],
                "caption": "第 %d 格画面" % (i + 1),
                "bbox": [c / cols, r / rows, (c + 1) / cols, (r + 1) / rows],
            })
        return state

    w, h = canvas
    state["canvas"] = {"width": int(w), "height": int(h)}
    for tpl in PRESET_ITEMS.get(preset, []):
        item = dict(tpl)
        item["id"] = _new_id("e")
        state["items"].append(item)
    return state
