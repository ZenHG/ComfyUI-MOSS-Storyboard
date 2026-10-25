# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""elements-v1 互操作层：外部元素 JSON ⇄ state v3（单张模式）。

**不依赖模型、宿主、网络。** 纯函数：同一份输入永远得到同一份 state（含 id）。

用途：任何 planner / 脚本 / 别的工具只要产出「谁在哪个框里」，就能经这一层
变成编辑器能继续改、节点能确定性编译的 state。格式与取舍见 `docs/12-interop.md`。

硬规则：

- bbox 单位是 **0–1000**，不做 0–1 自动识别（`[0,0,1,1]` 有歧义）；换算只在导入 / 导出时发生。
- 元素 role 只认 `text / object / character`（别名 `obj`、`person`）；`type` 可当 `role` 用。
- `desc` 映射 `caption`；text 元素必须自带 `text`，非 text 元素带 `text` 直接报错（不静默丢）。
- grid 不支持导入：宫格的 item 是**格位**不是元素，硬映射会误导。
- 最终 state 一律再过 `editor.parse_state`：本层不另立一套契约。
- 未知字段不丢：元素级收进 `x_ext`，文档级收进 `state.x_ext`；导出时还原。
"""

import json
import math
import re

from . import editor as E

#: elements 文档版本闸。与 state 版本各自独立。
ELEMENTS_VERSION = 1

#: bbox 单位：0–1000（端点含 1000）。
UNITS = 1000

#: 元素上限与 state 一致，避免"导入成功但存不进 widget"。
MAX_ELEMENTS = E.MAX_ITEMS

#: 默认画布（= 前端 `DEFAULT_CANVAS` / `presets.DEFAULT_CANVAS`）。
DEFAULT_CANVAS = (1536, 864)

#: role 别名。`type` 字段可替代 `role`。
ROLE_ALIASES = {
    "text": "text",
    "object": "object",
    "obj": "object",
    "character": "character",
    "person": "character",
}

#: 元素已知字段。其余键 → `x_ext`。
_ELEMENT_KEYS = {"id", "role", "type", "text", "desc", "caption", "bbox", "palette", "z", "x_ext"}

_DOC_KEYS = {"version", "canvas", "style", "elements", "x_ext"}

#: 生成 id 的前缀（与前端 `makeItemId` 同形：t1 / o1 / c1）。
_ID_PREFIX = {"text": "t", "object": "o", "character": "c"}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _fail(msg):
    raise ValueError(msg)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _role(el, where):
    raw = el.get("role", el.get("type"))
    if raw is None:
        _fail("%s 缺 role（可选 text / object / character；也可用 type 字段）。" % where)
    if "role" in el and "type" in el and el["role"] != el["type"]:
        _fail("%s 的 role=%r 与 type=%r 冲突，只留一个。" % (where, el["role"], el["type"]))
    if not isinstance(raw, str) or raw not in ROLE_ALIASES:
        _fail("%s 的 role=%r 不认识，可选：%s（别名：obj、person）。"
              % (where, raw, "、".join(sorted(set(ROLE_ALIASES.values())))))
    return ROLE_ALIASES[raw]


def _desc(el, where):
    raw = el.get("desc", el.get("caption"))
    if not isinstance(raw, str) or not raw.strip():
        _fail("%s 缺 desc（这个元素长什么样，必填，不能为空）。" % where)
    return raw


def _bbox(el, where):
    raw = el.get("bbox")
    if not (isinstance(raw, (list, tuple)) and len(raw) == 4):
        _fail("%s 的 bbox 必须是 4 个数 [x0,y0,x1,y1]（单位 0–%d，收到 %r）。"
              % (where, UNITS, raw))
    if any(not _is_num(v) for v in raw):
        _fail("%s 的 bbox 必须全是数字，不收字符串/布尔（收到 %r）。" % (where, list(raw)))
    x0, y0, x1, y1 = (float(v) for v in raw)
    if not (0.0 <= x0 < x1 <= UNITS and 0.0 <= y0 < y1 <= UNITS):
        _fail("%s 的 bbox 越界或反向：需要 0 ≤ x0 < x1 ≤ %d、0 ≤ y0 < y1 ≤ %d"
              "（收到 %r）。单位是 0–%d，不是 0–1。" % (where, UNITS, UNITS, list(raw), UNITS))
    return [v / UNITS for v in (x0, y0, x1, y1)]


def _palette(el, where):
    pal = el["palette"]
    if not isinstance(pal, list) or len(pal) > 5:
        _fail("%s 的 palette 必须是数组，最多 5 个颜色（收到 %r）。" % (where, pal))
    for c in pal:
        if not (isinstance(c, str) and _HEX_RE.match(c)):
            _fail("%s 的 palette 颜色必须是 #rrggbb（收到 %r）。" % (where, c))
    return list(pal)


def _quantize(v):
    """0–1 → 0–1000，保留 3 位小数。与前端 `Math.round(v * 1e6) / 1000` 同式。"""
    return math.floor(v * UNITS * 1000 + 0.5) / 1000


def _gen_id(role, used):
    prefix = _ID_PREFIX[role]
    n = 1
    while "%s%d" % (prefix, n) in used:
        n += 1
    return "%s%d" % (prefix, n)


def _extras(el):
    """元素里不认识的键 + 自带 `x_ext`，合并成一个 dict。"""
    out = {}
    if "x_ext" in el:
        if not isinstance(el["x_ext"], dict):
            _fail("x_ext 必须是对象（收到 %s）。" % type(el["x_ext"]).__name__)
        out.update(el["x_ext"])
    for key, val in el.items():
        if key not in _ELEMENT_KEYS:
            out[key] = val
    return out


def to_state(doc, preset="free", style=None):
    """elements-v1 文档 → state v3（单张模式）。不合法就抛 ValueError。

    `preset` 只能用单张模式的预设（默认 `free`）；`style` 传入则覆盖文档里的 `style`。
    """
    if not isinstance(doc, dict):
        _fail("elements 文档必须是 JSON 对象，收到 %s。" % type(doc).__name__)
    ver = doc.get("version")
    if not isinstance(ver, int) or isinstance(ver, bool) or ver != ELEMENTS_VERSION:
        _fail("elements 版本是 %r，本版只认 %d。" % (ver, ELEMENTS_VERSION))
    if preset == "grid" or preset not in E.PRESETS:
        if preset == "grid":
            _fail("grid 不支持导入：宫格的 item 是格位，不是元素。请用单张模式（free 等）。")
        _fail("preset=%r 不认识，可选：%s。" % (preset, "、".join(p for p in E.PRESETS if p != "grid")))

    elements = doc.get("elements")
    if not isinstance(elements, list):
        _fail("文档缺 elements 数组（收到 %s）。" % type(elements).__name__)
    if len(elements) > MAX_ELEMENTS:
        _fail("elements 有 %d 个，超过上限 %d。请拆成多张画布。" % (len(elements), MAX_ELEMENTS))

    canvas = doc.get("canvas")
    if canvas is None:
        cw, ch = DEFAULT_CANVAS
    else:
        if not isinstance(canvas, dict):
            _fail("canvas 必须是对象 {width,height}（收到 %s）。" % type(canvas).__name__)
        cw, ch = canvas.get("width"), canvas.get("height")

    st_style = doc.get("style") if style is None else style
    if st_style is None:
        st_style = ""
    if not isinstance(st_style, str):
        _fail("style 必须是字符串（收到 %s）。" % type(st_style).__name__)

    used = set()
    items = []
    for i, el in enumerate(elements):
        where = "第 %d 个元素" % (i + 1)
        if not isinstance(el, dict):
            _fail("%s 必须是对象（收到 %s）。" % (where, type(el).__name__))
        role = _role(el, where)
        ident = el.get("id")
        if ident is None:
            ident = _gen_id(role, used)
        elif not isinstance(ident, str) or not ident:
            _fail("%s 的 id 必须是非空字符串（收到 %r）。" % (where, ident))
        elif ident in used:
            _fail("%s 的 id=%r 重复了；id 必须唯一。" % (where, ident))
        used.add(ident)
        item = {"id": ident, "label": role, "caption": _desc(el, where),
                "bbox": _bbox(el, where)}
        if role == "text":
            txt = el.get("text")
            if not isinstance(txt, str) or not txt.strip():
                _fail("%s 是 text 元素，必须填 text（实际渲染的字；desc 不能当 text 用）。" % where)
            item["text"] = txt
        elif "text" in el:
            _fail("%s 的 role=%s 不支持 text；要渲染文字请用 role=text。" % (where, role))
        if "palette" in el:
            item["palette"] = _palette(el, where)
        ext = _extras(el)
        if ext:
            item["x_ext"] = ext
        items.append((el.get("z", i), i, item))

    # 缺省 z = 数组下标；稳定排序，同 z 保持原顺序。
    for z, _, _ in items:
        if not _is_num(z):
            _fail("z 必须是数字（收到 %r）。" % (z,))
    items = [it for _, _, it in sorted(items, key=lambda t: (t[0], t[1]))]

    state = {
        "version": E.STATE_VERSION,
        "preset": preset,
        "style": st_style,
        "canvas": {"width": cw, "height": ch},
        "items": items,
    }
    doc_ext = doc.get("x_ext")
    if doc_ext is not None:
        if not isinstance(doc_ext, dict):
            _fail("文档级 x_ext 必须是对象（收到 %s）。" % type(doc_ext).__name__)
        state["x_ext"] = dict(doc_ext)
    for key, val in doc.items():
        if key not in _DOC_KEYS:
            state.setdefault("x_ext", {})[key] = val
    return E.parse_state(json.dumps(state, ensure_ascii=False))


def from_state(state):
    """state v3（单张模式）→ elements-v1 文档。

    bbox 乘 1000 后保留 3 位小数（约 0.001 精度）；element 顺序即 z 序，
    因此导出不再写 `z`。`x_ext` 原样带出，供再次导入时保留。
    """
    state = E.parse_state(state if isinstance(state, str)
                          else json.dumps(state, ensure_ascii=False))
    if E.is_grid(state):
        _fail("宫格 state 不能导出成 elements：每格是格位，不是元素。")
    doc = {
        "version": ELEMENTS_VERSION,
        "canvas": {"width": state["canvas"]["width"], "height": state["canvas"]["height"]},
        "style": state.get("style") or "",
        "elements": [],
    }
    for it in state["items"]:
        el = {"id": it["id"], "role": it["label"],
              "desc": it["caption"],
              "bbox": [_quantize(float(v)) for v in it["bbox"]]}
        if it["label"] == "text":
            el["text"] = it["text"]
        if it.get("palette"):
            el["palette"] = list(it["palette"])
        if isinstance(it.get("x_ext"), dict):
            el["x_ext"] = dict(it["x_ext"])
        doc["elements"].append(el)
    ext = state.get("x_ext")
    if isinstance(ext, dict):
        for key, val in ext.items():
            if key not in doc:
                doc[key] = val
            else:
                doc.setdefault("x_ext", {})[key] = val
    return doc


def sniff(obj):
    """判断一份 JSON 是 elements 文档还是 state。返回 `"elements"` / `"state"`。"""
    if not isinstance(obj, dict):
        return None
    if "elements" in obj:
        return "elements"
    if obj.get("version") == E.STATE_VERSION and "items" in obj:
        return "state"
    return None


def to_elements_json(doc_or_state):
    """state 或 elements 文档 → elements 文档（幂等）。"""
    kind = sniff(doc_or_state)
    if kind == "elements":
        return from_state(to_state(doc_or_state))
    if kind == "state":
        return from_state(doc_or_state)
    raise ValueError("既不是 elements 文档也不是 state v3：缺 elements / items。")
