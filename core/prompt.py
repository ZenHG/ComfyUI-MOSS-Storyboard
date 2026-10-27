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
#: 直接送去文本编码时会多出一段无意义符号。
_SEP = ":：,，、;；"

#: 全局描述里的"多格"措辞。逐格生成时必须清掉，否则模型照样画多格
#: （2026-10-24 实测：给 1 个 bbox + 「恰好 4 个面板」⇒ 输出仍是 4 格）。
#: 只匹配明确的"分格"说法，不碰"画面/镜头/景别/全景"这类正常描述。
MULTI_PANEL_PATTERNS = (
    re.compile(r"[^，,。；;、]{0,24}?(?:恰好|正好|共|分为|切成|划分成?|分割成?|剪成)"
               r"[^，,。；;、]{0,12}?\d+\s*(?:个|幅|张)?\s*(?:面板|分镜|格子|画面|镜头|画格|小图)"),
    re.compile(r"(?:单一|一张|单张)\s*故事板(?:画布|图|格)"),
    re.compile(r"严格对称(?:网格|分格)"),
    re.compile(r"(?:细白|白色|细)\s*(?:边框|分割线|分隔线|线条)"),
    re.compile(r"(?:整齐)?分(?:隔|割)(?:成|为)\s*\d+\s*(?:个|幅|张)?\s*(?:面板|分镜|格子|画面)"),
    re.compile(r"(?:没有|无|不含?)\s*(?:粗框|黑边|跨(?:格|面板)(?:全景)?溢出)"),
    # 裸计数："4 个面板的故事板" 这类没有动词的说法也必须清掉
    # （英文侧 `\b\d+ panels\b` 早就会清；中文侧此前漏了，逐格时仍会画出多格）。
    re.compile(r"\d+\s*(?:个|幅|张|格)?\s*(?:面板|分镜|格子|画格)"),
    # 英文：storyboard canvas / split into N panels / panel borders
    re.compile(r"(?i)\b(?:single\s+)?storyboard\s+(?:canvas|image|grid|sheet)\b"),
    re.compile(r"(?i)\bsplit\s+into\s+\d+\s+(?:panels?|frames?|cells?|tiles?)\b"),
    re.compile(r"(?i)\b(?:thin\s+white\s+|white\s+)?(?:panel\s+)?borders?\b"),
    re.compile(r"(?i)\b\d+\s+panels?\b"),
)


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


#: 全局段里的显式格数。只认「恰好/正好/共 N 个面板（格子）」与英文「N panels」；
#: 不碰「第 8 格」这类格号，也不碰「4 个镜头」这类画面描述。
COUNT_RE = re.compile(
    r"(?:恰好|正好|共)\s*(\d+)\s*(?:个|幅|张)?\s*(?:面板|分镜|格子|画格|格)"
    r"|(?i:\b(\d+)\s+panels?\b)")


def board_mismatch(text, panels):
    """对账：稿子说的格数 vs 布局格数。一致返回 `None`，否则 `(问题描述, 稿子隐含格数)`。

    只查两处机器能确定的数字：全局段的显式格数、`[PANEL n]` 段数。
    不改写、不补段 —— 布局模式要求作者自己写满，调用方负责把出路写进报错。
    """
    head, caps = split_panels(text)
    stated = None
    m = COUNT_RE.search(head)
    if m:
        stated = int(m.group(1) or m.group(2))
    problems = []
    if stated is not None and stated != panels:
        problems.append("全局段写「%s」，但布局是 %d 格" % (m.group(0).strip(), panels))
    if caps and len(caps) != panels:
        problems.append("提示词有 %d 段 [PANEL]，但布局是 %d 格" % (len(caps), panels))
    if not problems:
        return None
    return "；".join(problems), (len(caps) if caps else stated)


def strip_multi_panel_words(text):
    """去掉全局描述里的"多格"措辞，只留风格/角色/环境。

    🔴 为什么要这一步（2026-10-24 实测）：MOSS 是靠**语义**画宫格的，不是靠 bbox。
    实测给 1 个 bbox、但 summary 里仍写着「恰好 4 个面板」⇒ 模型照样画出 4 格。
    逐格生成时若不清理，模型会继续画多格，逐格就失去意义。

    实现按**分句**判断：整句含多格词就整句删掉。
    早先用正则逐词替换，残留过"分割为""所有面板由""跨面板"这类碎片，
    反而给模型送了乱码般的提示 —— 宁可整句删干净。

    不动"画面/镜头/景别/全景"这类正常描述。
    """
    if not text:
        return ""
    # 先按中英文标点分句（保留分隔符，方便判断逗号级从句）
    parts = re.split(r"([，,。；;、\n])", text)
    # parts 是 [句, 分隔, 句, 分隔, ...]；每 2 个一组
    kept = []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if _is_multi_panel_clause(seg):
            continue
        kept.append(seg + sep)
    out = "".join(kept)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[，,、；;]\s*[，,、；;]", "", out)
    out = re.sub(r"^[，,、；;、\s]+", "", out)
    return out.strip()


def _is_multi_panel_clause(clause):
    """这一句/从句是否在描述"多格宫格"本身（而不是画面内容）。"""
    if not clause:
        return False
    for pat in MULTI_PANEL_PATTERNS:
        if pat.search(clause):
            return True
    # 「每个面板」「所有面板」「面板之间」「跨面板」这类纯分格指代
    if re.search(r"(?:每个|所有|各|相邻|跨|整个)\s*(?:面板|分镜|格子|画格)", clause):
        return True
    # 「每格」「逐格」「分 N 格」这类分格说法（数字与单位之间允许空格）
    if re.search(r"(?:每|逐)\s*(?:格|个面板|幅面板)", clause):
        return True
    if re.search(r"(?:分割|划分|切|分)(?:成|为)\s*\d+\s*(?:格|个面板|幅|张|画面|镜头)", clause):
        return True
    # 「分割为」「分成」后面没跟着具体内容，说明就是在讲分格结构
    if re.search(r"(?:分割|划分|切)(?:成|为)\s*$", clause):
        return True
    return False


def cell_bbox_to_centered(bbox, width, height):
    """格内归一化 `[u0, v0, u1, v1]` → 画布中心原点的像素 `[y0, x0, y1, x1]`。

    为什么是**格内归一化**而不是像素：单格的像素尺寸由 `panel_edge` 决定，
    用户改 `panel_edge` 时不该重画所有元素框。归一化后几何一变框自动跟着缩放。

    `width` / `height` 是**该格的采样画布**（逐格模式下的 `pw` / `ph`），
    不是整张宫格 —— 逐格采样时一张图就是一格。
    """
    u0, v0, u1, v1 = (float(v) for v in bbox)
    hx, hy = int(width) // 2, int(height) // 2
    return [round(v0 * height) - hy, round(u0 * width) - hx,
            round(v1 * height) - hy, round(u1 * width) - hx]


def single_panel_json(global_caption, caption, width, height, label="object", elements=None):
    """单格模式的布局提示词 JSON。

    与 `layout_json_text` 的差别有两处，都是实测逼出来的：
    1. `summary.caption` 先过 `strip_multi_panel_words` ⇒ 不再暗示"多格"；
    2. bbox **覆盖整幅画布** ⇒ 实测这决定构图粒度（只给 1/4 区域会让模型
       把主体塞进那一块、周围留白），整幅覆盖时模型按 caption 的景别自由构图。

    `elements` 非空时改为**元素分区**：每个元素一个 bbox，模型按区域摆放。
    此时 `summary.caption` = 全局段 + 该格 caption（整格描述进 summary），
    `items` = 元素列表。`elements` 为空 ⇒ 与旧行为逐字节相同。
    """
    if label not in LABELS:
        raise ValueError("label 只能是 %s（收到 %r）" % ("/".join(LABELS), label))
    cap = (caption or "").strip()
    g = strip_multi_panel_words(global_caption)

    if elements:
        items = []
        for i, el in enumerate(elements):
            el_label = el.get("label", "object")
            if el_label not in LABELS:
                raise ValueError("元素 %d 的 label 只能是 %s（收到 %r）"
                                 % (i + 1, "/".join(LABELS), el_label))
            el_cap = (el.get("caption") or "").strip()
            if not el_cap:
                raise ValueError("元素 %d 没有描述（caption 不能为空）" % (i + 1))
            item = {"label": el_label, "caption": el_cap,
                    "bbox": cell_bbox_to_centered(el["bbox"], width, height)}
            if el_label == "text":
                item["text"] = (el.get("text") or el_cap).strip()
            items.append(item)
        summary = (g.rstrip("。") + "。" + cap) if cap else g
        payload = {"summary": {"caption": summary}, "items": items,
                   "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    if not cap:
        return g
    hx, hy = int(width) // 2, int(height) // 2
    payload = {
        "summary": {"caption": g},
        "items": [{"label": label, "caption": cap,
                   "bbox": [-hy, -hx, int(height) - hy, int(width) - hx]}],
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def panel_prompt(global_caption, caption, width, height, init_mode="layout", label="object"):
    """逐格采样的单格提示词。

    `layout` = 布局 JSON（与整张模式的编码方式一致）；
    `plain` = 纯文本，但**必须带上全局段**并清掉多格措辞 —— 只发该格 caption
    会丢掉角色/风格锚点，逐格一致性就没了（此前正是这么丢的）。
    """
    if init_mode == "layout":
        return single_panel_json(global_caption, caption, width, height, label=label)
    cap = (caption or "").strip()
    head = strip_multi_panel_words(global_caption)
    return ("%s %s" % (head, cap)).strip()


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

