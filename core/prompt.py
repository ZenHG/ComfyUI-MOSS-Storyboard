# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""提示词层：把"[STYLE] + [PANEL n]"的故事板稿拆成 MOSS-Image 原生布局 JSON。

为什么不用 `MOSSImageLayoutPrompt` 节点：那个节点的 bbox 只能从
`Create Bounding Boxes` 的画布手绘来，故事板要 3/4/6/8/9 格就得手画 N 个框，
无法自动化。这里用 `Layout` 算出的框直接生成**同格式**的 JSON，
所以既可以直接喂 `CLIPTextEncode`，也可以和上游 `Create Bounding Boxes` 混用。
"""

import json
import re

#: `[PANEL 3]` / `[panel3]` / `[ 画面 3 ]` 都认。
PANEL_RE = re.compile(r"\[\s*(?:PANEL|画面|分镜)\s*(\d+)\s*\]", re.IGNORECASE)

#: 全局段的标记：`[STYLE]` / `[GLOBAL]` / `[风格]` / `[全局]`。
GLOBAL_RE = re.compile(r"\[\s*(?:STYLE|GLOBAL|风格|全局)\s*\]", re.IGNORECASE)

#: 布局 JSON 允许的 item 标签（宿主只认这三个）。
LABELS = ("object", "character", "text")

#: 标记后面常见的分隔符（中英冒号、逗号、顿号）。不剥掉会让 caption 以 ": " 开头，
#: 直接送去文本编码时会多一段无意义符号。
_SEP = ":：,，、;；"


def _clean(text):
    return (text or "").strip().lstrip(_SEP).strip()


def split_panels(text):
    """拆成 `(global_caption, [(panel_no, caption), ...])`。

    - 无 `[PANEL n]` 标记时返回 `(全文, [])`，调用方据此退回"纯文生图"。
    - 标记前的内容（去掉 `[STYLE]` 标签本身）当全局描述。
    - 标记顺序按编号升序，缺号不补。
    """
    text = (text or "").strip()
    if not text:
        return "", []
    marks = list(PANEL_RE.finditer(text))
    if not marks:
        return _clean(GLOBAL_RE.sub("", text)), []

    head = _clean(GLOBAL_RE.sub("", text[: marks[0].start()]))
    parts = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        cap = _clean(re.sub(r"\s*\n\s*", " ", text[m.end():end]))
        parts.append((int(m.group(1)), cap))
    parts.sort(key=lambda p: p[0])
    return head, parts


def pair_captions(captions, cells):
    """把 N 条 caption 与 M 个格子对齐，返回 `(pairs, notes)`。

    规则（宁可少画一格，也不许错位）：
    - 一一对应时直接用；
    - caption 比格子少 → 只约束前 K 格，其余格子不给 bbox（模型自由构图）；
    - caption 比格子多 → 丢尾部并出声。
    """
    notes = []
    n_cap, n_cell = len(captions), len(cells)
    if n_cap > n_cell:
        notes.append("提示词有 %d 段但只有 %d 格：尾部 %d 段被丢弃" % (n_cap, n_cell, n_cap - n_cell))
    if n_cap < n_cell:
        notes.append("提示词只有 %d 段但布局有 %d 格：后 %d 格不给框（模型自由构图）"
                     % (n_cap, n_cell, n_cell - n_cap))
    pairs = []
    for i in range(min(n_cap, n_cell)):
        pairs.append((captions[i][1], cells[i]))
    return pairs, notes


def layout_json_text(global_caption, pairs, boxes, label="object", char_boxes=""):
    """生成 MOSS-Image 布局提示词字符串。

    `boxes` = `Layout.boxes_abs_centered()`，长度必须 >= len(pairs)。

    无格子时返回**纯文本**（等价于普通文生图）：空 `items` 的 JSON 会让文本编码
    多出一段无用符号，而 `{}` 更会污染提示词。
    """
    if not pairs:
        return (global_caption or "").strip()
    if label not in LABELS:
        raise ValueError("label 只能是 %s（收到 %r）" % ("/".join(LABELS), label))
    if len(boxes) < len(pairs):
        raise ValueError("boxes(%d) 少于 caption 对(%d)" % (len(boxes), len(pairs)))

    chars = {int(i) for i in re.findall(r"\d+", char_boxes or "")}
    items = []
    for i, (caption, _cell) in enumerate(pairs):
        caption = (caption or "").strip()
        if not caption:
            continue
        items.append({
            "label": "character" if (i + 1) in chars else label,
            "caption": caption,
            "bbox": [int(v) for v in boxes[i]],
        })
    if not items:
        return (global_caption or "").strip()

    payload = {
        "summary": {"caption": (global_caption or "").strip()},
        "items": items,
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def describe(text):
    """一行摘要，写进 report，避免把整段提示词刷到日志里。"""
    if not text:
        return "空"
    head = text[:48].replace("\n", " ")
    return "%s…（%d 字）" % (head, len(text)) if len(text) > 48 else "%s（%d 字）" % (head, len(text))
