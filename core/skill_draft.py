# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
"""官方 MOSS-Image Prompt Enhancer 草稿的编译与校验。

草稿格式来自 OpenMOSS/MOSS-Image 的
`skills/moss-image-prompt-enhancer`（Apache-2.0）：

- caption 草稿：`{"caption": "..."}`
- layout 草稿：`width` / `height` / `bbox_coord_space="abs"` /
  `bbox_format="xyxy"` / `summary` / `items`

本模块**独立实现**，未复制该 skill 的代码；规则与官方
`compile_prompt.py` 对齐（2026-10-26 核，官方 8 项测试全过）：

- 草稿 bbox 是**左上原点、绝对像素、xyxy**；
- 输出是 MOSS 原生布局：`abs_centralized` / `yxyx`，并去掉画布宽高；
- 小数边先 floor 左边/上边、ceil 右边/下边，再减画布中心；
- 未知字段、重复键、NaN/Infinity、越界、零面积一律拒绝。

比官方脚本多两条服务本包的约束：
- `MAX_ITEMS = 256`（与编辑器 state v3 上限一致）；
- `draft_to_state()` 把 layout 草稿转成 state v3，供布局编辑器导入。
  state v3 表达不了的字段（`summary.meta/canvas/palette`、item `tags`、
  非 `#rrggbb` 的 palette）会**明确报告丢弃**，不静默吞掉。
"""

import json
import math
import re

#: 与 `core.editor.MAX_ITEMS` 保持一致；防止一个草稿把编辑器/提示词撑爆。
MAX_ITEMS = 256
#: 草稿文本上限（字符）。官方脚本没有上限；节点输入必须有。
MAX_DRAFT_CHARS = 1_000_000

LABELS = ("object", "character", "text")
_ROOT_FIELDS = {"width", "height", "bbox_coord_space", "bbox_format", "summary", "items"}
_SUMMARY_FIELDS = {"caption", "meta", "canvas", "palette"}
_ITEM_FIELDS = {"label", "caption", "bbox", "text", "tags", "palette"}
_CANVAS_KINDS = ("single", "multi_panel", "collage")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
#: 与 `core.editor.is_blank` 同口径：零宽 / 方向控制字符渲染不出来，按空处理。
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff]")


def _fail(msg):
    raise ValueError(msg)


def _require(cond, msg):
    if not cond:
        _fail(msg)


def _is_num(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v))
    except (OverflowError, ValueError):
        return False


def _blank(v):
    return not _INVISIBLE_RE.sub("", v).strip()


def _text(v, where):
    _require(isinstance(v, str) and not _blank(v),
             "%s 必须是非空字符串（零宽 / 方向控制字符不算内容）。" % where)


def _strings(v, where):
    _require(isinstance(v, list), "%s 必须是数组。" % where)
    for i, item in enumerate(v):
        _text(item, "%s[%d]" % (where, i))


def _fields(value, allowed, required, where):
    _require(isinstance(value, dict), "%s 必须是 JSON 对象。" % where)
    unknown = set(value) - allowed
    missing = set(required) - set(value)
    if unknown:
        _fail("%s 有未知字段 %s；官方草稿只认 %s。"
              % (where, sorted(unknown), sorted(allowed)))
    if missing:
        _fail("%s 缺字段 %s。" % (where, sorted(missing)))


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        _require(key not in out, "JSON 里有重复键 %r；请删掉重复项。" % key)
        out[key] = value
    return out


def _reject_constant(name):
    _fail("JSON 里不允许 %s（不是合法 JSON）。" % name)


def loads(raw):
    """严格解析草稿文本（拒重复键 / NaN / Infinity / 超长）。"""
    text = "" if raw is None else str(raw)
    _require(len(text) <= MAX_DRAFT_CHARS,
             "草稿有 %d 个字符，超过上限 %d；请拆分后再试。" % (len(text), MAX_DRAFT_CHARS))
    try:
        draft = json.loads(text, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        _fail("草稿不是合法 JSON（%s）。" % exc)
    _require(isinstance(draft, dict), "草稿必须是 JSON 对象，收到 %s。" % type(draft).__name__)
    return draft


def _centered_box(box, width, height, where):
    _require(isinstance(box, list) and len(box) == 4,
             "%s.bbox 必须是 [x1,y1,x2,y2] 四个数。" % where)
    _require(all(_is_num(v) for v in box),
             "%s.bbox 必须全是有限数字，不收字符串/布尔/NaN（收到 %r）。" % (where, box))
    x1, y1, x2, y2 = box
    _require(x1 < x2 and y1 < y2,
             "%s.bbox 必须 x1<x2 且 y1<y2（收到 %r）。" % (where, box))
    _require(0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height,
             "%s.bbox 超出 %d×%d 画布（收到 %r）。" % (where, width, height, box))
    out = [
        math.floor(round(y1, 9)) - height // 2,
        math.floor(round(x1, 9)) - width // 2,
        math.ceil(round(y2, 9)) - height // 2,
        math.ceil(round(x2, 9)) - width // 2,
    ]
    _require(out[0] < out[2] and out[1] < out[3],
             "%s.bbox 在像素精度下会塌成零面积；请把框放大。" % where)
    return out


def _validate_layout(draft):
    """校验 layout 草稿，返回 `(width, height, summary, items)`（不修改原对象）。"""
    _fields(draft, _ROOT_FIELDS, _ROOT_FIELDS, "草稿")
    width, height = draft["width"], draft["height"]
    for name, value in (("width", width), ("height", height)):
        # 官方 schema 是"正整数"：与 core.editor 的整值浮点宽容不同，
        # 这里按官方 compile_prompt.py 的 `type(value) is int` 严格执行。
        _require(type(value) is int and value > 0 and value % 16 == 0,
                 "%s 必须是 16 的正整数倍（收到 %r）。" % (name, value))
    width, height = int(width), int(height)
    _require(draft["bbox_coord_space"] == "abs",
             "bbox_coord_space 必须是 \"abs\"（左上原点像素）；收到 %r。"
             % draft["bbox_coord_space"])
    _require(draft["bbox_format"] == "xyxy",
             "bbox_format 必须是 \"xyxy\"；收到 %r。" % draft["bbox_format"])

    summary = draft["summary"]
    _fields(summary, _SUMMARY_FIELDS, {"caption"}, "summary")
    _text(summary["caption"], "summary.caption")
    if "canvas" in summary:
        _require(summary["canvas"] in _CANVAS_KINDS,
                 "summary.canvas 必须是 %s 之一（收到 %r）。"
                 % ("/".join(_CANVAS_KINDS), summary["canvas"]))
    for key in ("meta", "palette"):
        if key in summary:
            _strings(summary[key], "summary.%s" % key)

    items = draft["items"]
    _require(isinstance(items, list) and bool(items), "items 必须是非空数组。")
    _require(len(items) <= MAX_ITEMS,
             "items 有 %d 个，超过上限 %d；请拆成多张画布。" % (len(items), MAX_ITEMS))

    checked = []
    for i, item in enumerate(items):
        where = "items[%d]" % i
        _fields(item, _ITEM_FIELDS, {"label", "caption", "bbox"}, where)
        label = item["label"]
        _require(label in LABELS,
                 "%s.label 必须是 %s 之一（收到 %r）。" % (where, "/".join(LABELS), label))
        _text(item["caption"], "%s.caption" % where)
        if label == "text":
            _require("text" in item, "%s 是文字元素，必须填 text（实际渲染的字）。" % where)
        if "text" in item:
            _text(item["text"], "%s.text" % where)
        for key in ("tags", "palette"):
            if key in item:
                _strings(item[key], "%s.%s" % (where, key))
        box = _centered_box(item["bbox"], width, height, where)
        checked.append((item, box))
    return width, height, summary, checked


def compile_draft(draft):
    """草稿对象 → `(prompt, report)`。

    - caption 草稿返回纯文本；
    - layout 草稿返回一行 MOSS 原生布局 JSON（abs_centralized / yxyx）。
    """
    _require(isinstance(draft, dict), "草稿必须是 JSON 对象。")
    if "caption" in draft:
        _fields(draft, {"caption"}, {"caption"}, "caption 草稿")
        _text(draft["caption"], "caption")
        return draft["caption"], {
            "kind": "caption",
            "items": 0,
            "notes": ["caption 草稿是纯文本，没有布局框。"],
        }

    width, height, summary, checked = _validate_layout(draft)
    items = []
    for item, box in checked:
        out = dict(item)
        out["bbox"] = box
        items.append(out)
    prompt = json.dumps(
        {"summary": summary, "items": items,
         "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    labels = {}
    for item, _ in checked:
        labels[item["label"]] = labels.get(item["label"], 0) + 1
    return prompt, {
        "kind": "layout",
        "width": width,
        "height": height,
        "items": len(items),
        "labels": labels,
        "notes": [],
    }


def _norm(v, total, where):
    n = round(float(v) / float(total), 6)
    _require(0.0 <= n <= 1.0, "%s 归一化后越界（%r）。" % (where, n))
    return n


def draft_to_state(draft):
    """layout 草稿 → `(state_v3, notes)`；caption 草稿不适用。

    state v3 的字段是 `version/preset/style/canvas/items`，所以：
    - `summary.caption` → `style`；`width/height` → `canvas`；
    - item 保留 `label/caption/text/bbox`；`bbox` 归一化到 0–1；
    - state 表达不了的 `summary.meta/canvas/palette`、item `tags`、
      非 `#rrggbb` palette **明确记入 notes 并丢弃**；
    - 合法 `#rrggbb` palette（≤5）保留。
    """
    _require(isinstance(draft, dict), "草稿必须是 JSON 对象。")
    _require("caption" not in draft,
             "caption 草稿没有布局，不能转 state；请用 compile 输出纯文本。")
    width, height, summary, checked = _validate_layout(draft)
    _require(64 <= width <= 4096 and 64 <= height <= 4096,
             "画布 %d×%d 超出布局编辑器范围 64..4096；compile 模式仍可输出提示词，"
             "但 state 模式需要 64–4096 的画布。" % (width, height))
    notes = []
    for key in ("meta", "canvas", "palette"):
        if key in summary:
            notes.append("summary.%s 在 state v3 里没有对应字段，已丢弃；compile 输出会保留。" % key)
    items = []
    for i, (item, _box) in enumerate(checked):
        where = "items[%d]" % i
        x1, y1, x2, y2 = item["bbox"]
        bbox = [_norm(x1, width, where), _norm(y1, height, where),
                _norm(x2, width, where), _norm(y2, height, where)]
        _require(bbox[0] < bbox[2] and bbox[1] < bbox[3],
                 "%s.bbox 归一化后塌成零面积；请把框放大。" % where)
        out = {
            "id": "s%d" % (i + 1),
            "label": item["label"],
            "caption": item["caption"],
            "bbox": bbox,
        }
        if "text" in item:
            out["text"] = item["text"]
        if "palette" in item:
            pal = item["palette"]
            good = [c for c in pal if isinstance(c, str) and _HEX_RE.match(c)][:5]
            if good:
                out["palette"] = good
            if len(good) != len(pal):
                notes.append("%s.palette 含非 #rrggbb 颜色或超过 5 个，state 里只保留 %s。"
                             % (where, good or "空"))
        if "tags" in item:
            notes.append("%s.tags 在 state v3 里没有对应字段，已丢弃；compile 输出会保留。" % where)
        items.append(out)
    state = {
        "version": 3,
        "preset": "free",
        "style": summary["caption"],
        "canvas": {"width": width, "height": height},
        "items": items,
    }
    return state, notes
