# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""brief → elements-v1：确定性的版式模板（不接模型、不联网）。

一份 brief 只回答「这张图说什么」，版式由本模块决定；产出仍是 elements-v1，
后续走 `core.interop` 导入编辑器 / 转 state。**几何只来自 `core.presets`**，
本模块不自己发明坐标。

v1 只支持 `title-body`（标题 / 主体 / 底部文字）。其它版式先在编辑器里选预设——
把每套版式都塞进这里，会让「预设」出现第二个真相源。
"""

from . import interop as I
from . import presets as P

#: 与前端 `CANVAS_PRESETS` 逐项一致（跨语言测试盯着）。
CANVAS_PRESETS = {
    "16:9": (1536, 864),
    "9:16": (864, 1536),
    "1:1": (1024, 1024),
    "4:3": (1152, 864),
    "3:4": (864, 1152),
}

LAYOUTS = ("title-body",)


def _text(brief, key, required=False):
    if key not in brief or brief[key] is None:
        if required:
            raise ValueError("brief 缺 %s（必填）。" % key)
        return None
    val = brief[key]
    if not isinstance(val, str) or not val.strip():
        raise ValueError("brief.%s 必须是非空字符串（收到 %r）。" % (key, val))
    return val


def _canvas(brief):
    raw = brief.get("canvas")
    if raw is None:
        return CANVAS_PRESETS["16:9"]
    if isinstance(raw, str):
        if raw not in CANVAS_PRESETS:
            raise ValueError("canvas=%r 不认识，可选：%s。" % (raw, "、".join(CANVAS_PRESETS)))
        return CANVAS_PRESETS[raw]
    if isinstance(raw, dict):
        return raw.get("width"), raw.get("height")
    raise ValueError("canvas 必须是预设名（如 16:9）或 {width,height}。")


def brief_to_elements(brief):
    """brief → elements-v1 文档。不合法就抛 ValueError（文案要能指导修复）。"""
    if not isinstance(brief, dict):
        raise ValueError("brief 必须是 JSON 对象（收到 %s）。" % type(brief).__name__)
    layout = brief.get("layout", "title-body")
    if layout not in LAYOUTS:
        raise ValueError("layout=%r 不在 v1 支持范围（只有 %s）。其它版式请在编辑器里选预设。"
                         % (layout, "、".join(LAYOUTS)))
    title = _text(brief, "title", required=True)
    subtitle = _text(brief, "subtitle")
    body = _text(brief, "body")
    if subtitle and body:
        raise ValueError("subtitle 与 body 只能给一个：title-body 底部只有一个文字框。")
    subject = _text(brief, "subject")
    title_desc = _text(brief, "title_desc")
    bottom_desc = _text(brief, "bottom_desc")
    style = brief.get("style", "")
    if style is None:
        style = ""
    if not isinstance(style, str):
        raise ValueError("style 必须是字符串。")
    palette = brief.get("palette")
    if palette is not None and not isinstance(palette, list):
        raise ValueError("palette 必须是数组（≤5 个 #rrggbb）。")

    cw, ch = _canvas(brief)
    tpl = P.PRESET_ITEMS["title-body"]
    elements = [{
        "role": "text",
        "desc": title_desc or tpl[0]["caption"],
        "text": title,
        "bbox": list(tpl[0]["bbox"]),
        "palette": list(palette) if palette is not None else list(tpl[0]["palette"]),
    }]
    if subject:
        elements.append({"role": "character", "desc": subject,
                         "bbox": list(tpl[1]["bbox"])})
    bottom = subtitle or body
    if bottom:
        elements.append({
            "role": "text",
            "desc": bottom_desc or tpl[2]["caption"],
            "text": bottom,
            "bbox": list(tpl[2]["bbox"]),
            "palette": list(palette) if palette is not None else list(tpl[2]["palette"]),
        })
    doc = {"version": I.ELEMENTS_VERSION, "canvas": {"width": cw, "height": ch},
           "style": style, "elements": elements}
    # 提前过一遍契约：画布 / 调色板 / 文字框不合法在生成时就报，而不是等导入。
    I.to_state(doc)
    return doc


def brief_to_state(brief):
    """brief → state v3（单张 free）。等价于 `import` 一次模板产物。"""
    return I.to_state(brief_to_elements(brief))
