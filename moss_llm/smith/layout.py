# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""MOSS-Image Layout：draft（abs/xyxy）→ compiled（abs_centralized/yxyx）。

严格复刻官方 `scripts/compile_prompt.py` 的语义：
- 浮点边先 floor（左/上）与 ceil（右/下），再减中心；
- 编译后丢弃 width/height；
- 输出紧凑 JSON（separators=(",", ":")），不转义非 ASCII；
- 未知字段拒绝，不静默裁剪。

不手算中心坐标是纪律，不是风格：floor/ceil 顺序写错一次，整张图错位。
"""

from __future__ import annotations

import json
import math

from .clauses import build_clauses, describe_entity, describe_text, render_prose
from .ir import IR

LABELS = ("object", "character", "text")


def build_draft(ir: IR, megapixels: float = 1.0) -> dict:
    w, h = ir.pixel_size(megapixels)
    items = []
    for e in ir.entities:
        if e.kind == "background" or not e.spatial.get("bbox_norm"):
            continue
        x0, y0, x1, y1 = e.spatial["bbox_norm"]
        bbox = [int(round(x0 * w)), int(round(y0 * h)),
                int(round(x1 * w)), int(round(y1 * h))]
        item = {
            "label": e.kind if e.kind in LABELS else "object",
            # 位置已由 bbox 编码，caption 里再写 "at the top-right" 是冗余，
            # 而且会和 bbox 冲突时把模型搞晕。
            "caption": (describe_text(e, with_anchor=False) if e.kind == "text"
                        else describe_entity(e, with_anchor=False)),
            "bbox": bbox,
        }
        if e.kind == "text" and e.text_content:
            item["text"] = e.text_content
        items.append(item)

    if not items:
        raise ValueError("IR 里没有任何带 bbox_norm 的实体，无法生成 layout draft")

    # 阅读顺序（上→下、左→右）。官方不要求顺序，但**输出不该依赖 IR 数组顺序**：
    # 同一张海报换个实体写法就得到不同 JSON，下游 diff 会全是噪声。
    # 顺带让 JSON 的阅读顺序和人看图的顺序一致，便于人工核对。
    items.sort(key=lambda it: (it["bbox"][1], it["bbox"][0],
                               it["bbox"][3], it["bbox"][2], it["label"], it["caption"]))

    caption_clauses = [c for c in build_clauses(ir) if c.dim != "text"]
    caption = render_prose(caption_clauses)
    summary = {
        "caption": caption[:1].upper() + caption[1:] if caption else "",
        "meta": list((ir.style.get("substyle") or [])[:3]) or ["image"],
        "canvas": ir.canvas.get("canvas_type") or "single",
    }
    return {"width": w, "height": h, "bbox_coord_space": "abs",
            "bbox_format": "xyxy", "summary": summary, "items": items}


def compile_draft(draft: dict) -> dict:
    """官方语义的编译。返回 compiled dict（含 `_w`/`_h` 供 lint 用，序列化时剔除）。"""
    w, h = int(draft["width"]), int(draft["height"])
    if w % 16 or h % 16:
        raise ValueError("width/height 必须是 16 的倍数，当前 %dx%d" % (w, h))
    if draft.get("bbox_coord_space") != "abs" or draft.get("bbox_format") != "xyxy":
        raise ValueError("draft 必须是 abs/xyxy")

    items = []
    for it in draft["items"]:
        if it["label"] not in LABELS:
            raise ValueError("label 必须是 object/character/text，收到 %r" % it["label"])
        x0, y0, x1, y1 = [float(v) for v in it["bbox"]]
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise ValueError("bbox 越界或反序：%r（画布 %dx%d）" % (it["bbox"], w, h))
        out = {
            "label": it["label"],
            "caption": it["caption"],
            "bbox": [math.floor(y0) - h // 2, math.floor(x0) - w // 2,
                     math.ceil(y1) - h // 2, math.ceil(x1) - w // 2],
        }
        if it["label"] == "text":
            if not it.get("text"):
                raise ValueError("text 项必须带逐字 text")
            out["text"] = it["text"]
        items.append(out)

    return {
        "summary": draft["summary"],
        "items": items,
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
        "_w": w, "_h": h,
    }


def dumps(compiled: dict) -> str:
    """紧凑单行 JSON，剔除内部字段。"""
    clean = {k: v for k, v in compiled.items() if not k.startswith("_")}
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
