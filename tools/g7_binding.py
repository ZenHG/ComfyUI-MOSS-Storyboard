# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 · binding 机检（零模型）。

回答的不是「写得好不好看」，是：**模型在 IR 里记下的属性，有没有绑到同一个实体上、
并且原样活到了成品 caption 里。**

五个类别的判据（全部可离线单测，见 tests/test_g7_binding.py）：

| 类 | 分母（applicable） | bound 的判据 |
|---|---|---|
| 颜色 | 实体自己的描述字段里出现颜色词 | 该颜色词出现在**该实体自己的子句**内、距子句起点 ≤ N 词 |
| 材质 | 实体自己的字段里出现材质词 | 同上 |
| 方位 | 实体有 spatial.anchor | 方位词出现在该实体子句内；且**若 bbox 来自模型**，bbox 中心要落在 anchor 声明的象限里（两通道互证） |
| 图内文字 | kind=text 且 text_content 非空 | 每一行都**逐字出现在直双引号内** |
| 数量 | 同一 label 的实体成组且 n≥2（仅单图 canvas 计） | 组子句（或紧随的 "the same figure appears again" 子句）表达了数量 |

三个必须写清楚的边界（否则这张表会被误读成准确率）：

1. **分母来自模型自己的 IR，不是真值召回。** 所以「覆盖率 1.0」只说明「记下的都绑对了」，
   不说明「该记的都记了」。每个模型的**分母本身**也是结果的一部分（`applicable` 列），
   空 IR 不会拿到 1.0 —— 分母为 0 时覆盖率报 n/a，不报 1.0（记忆总账 §9.7 的坑）。
2. **不判事实对错**：颜色记成错的（图里红衣写成蓝衣）但只要绑在同一实体上，本判据算 bound。
   事实准确率与幻觉由 10 图代理目检单独判（`calibrate` 子命令对齐两者）。
3. **它是「账本 → 成品」的保真度**：bound 失败的第一含义是「模型记了、但没挂对该实体
   / 没活到 caption 里」，第二含义才是「模型没记」。两类在 instances 里分开标注。

用法::

  python tools/g7_binding.py measure            # → out/g7/binding.json + binding_table.md
  python tools/g7_binding.py calib-sheet --ids img01,...   # → out/g7/calib/machine_cells.json
  python tools/g7_binding.py calibrate          # 机检 vs 代理目检一致率
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "g7"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moss_llm.smith.clauses import (DIM_ORDER, build_clauses, describe_entity,  # noqa: E402
                                    render_prose)
from moss_llm.smith.expand import COLORS, DIRECTIONS                            # noqa: E402
from moss_llm.smith.ir import IR                                                # noqa: E402
from moss_llm.smith.reverse import _anchor_zone                                 # noqa: E402

#: 颜色词表：expand.COLORS（英文）之上再补中文颜色字与常见复合色。
#: 中文是必须的：A 臂是中文语料占比高的 4B，实测会给出纯中文描述。
COLOR_WORDS = set(COLORS) | {
    "红", "橙", "黄", "绿", "青", "蓝", "紫", "白", "黑", "灰", "金", "银", "褐",
    "粉", "棕", "米", "奶油", "琥珀", "墨", "朱", "绯", "翠", "碧", "赭",
    "vermilion", "ochre", "sepia", "charcoal", "off-white", "ivory", "ebony",
    "auburn", "blond", "blonde", "brunette", "ginger", "tan", "khaki", "plum",
}

#: 材质词表。收的是「表面行为/材料」而不是纹样；反光类的词也算（材质与反光同类）。
MATERIAL_WORDS = {
    "metal", "metallic", "steel", "stainless", "iron", "brass", "bronze", "copper",
    "chrome", "aluminium", "aluminum", "gold", "silver", "wood", "wooden", "timber",
    "glass", "ceramic", "porcelain", "clay", "terracotta", "leather", "suede", "denim",
    "cotton", "wool", "silk", "satin", "linen", "velvet", "lace", "knit", "knitted",
    "canvas", "plastic", "rubber", "paper", "cardboard", "stone", "marble", "granite",
    "concrete", "brick", "fabric", "textile", "fur", "feather", "hair", "matte",
    "glossy", "glazed", "brushed", "polished", "burnished", "patina", "patinated",
    "rust", "rusted", "wet", "damp", "condensation", "reflect", "reflective",
    "reflection", "specular", "shiny", "smooth", "rough", "textured", "woven",
    "embroidered", "engraved", "磨砂", "金属", "木", "玻璃", "陶瓷", "皮革", "布",
    "丝", "麻", "纸", "石", "反光", "光泽", "哑光", "湿", "水", "黄铜",
    "青铜", "不锈钢", "金", "银",
}

#: 数量词 → 数字。只有出现在「组子句 / same figure 子句」里才算绑定。
COUNT_WORDS = {
    "two": 2, "2": 2, "both": 2, "pair": 2, "couple": 2,
    "three": 3, "3": 3, "trio": 3,
    "four": 4, "4": 4, "five": 5, "5": 5, "six": 6, "6": 6,
    "两个": 2, "两位": 2, "三人": 3, "三个": 3, "四人": 4, "四个": 4,
}

#: 绑定窗口：预算子句内「属性词」距子句起点的最大词数。
#: 真正的判据是**同一子句**（确定性散文给每个实体一个逗号串子句）。
#: 这个数的实测用途只有一个：不让远端 note 里的颜色词冒充绑定。
#: 取值依据：gold set 实测最长实体子句 84 词（角色描述把头发/皮肤/穿着/姿势/方位
#: 全摊进一句），所以上限取 120；它事实上不会触发，单测里用 61 词的反例钉住语义。
DISTANCE_N = 120

_TOKEN = re.compile(r"[A-Za-z]+(?:['\-][A-Za-z]+)*|\d+|[\u3400-\u9fff]")


def _words(text: str) -> list[str]:
    return [w.lower() for w in _TOKEN.findall(text or "")]


def _norm_space(text: str) -> str:
    return " ".join((text or "").split())


def _flatten(value) -> str:
    """descriptors 里可能是 str / list / dict，统一摊成一段文本再取词。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(_flatten(v) for v in value if v is not None)
    if isinstance(value, dict):
        return "; ".join(_flatten(v) for v in value.values() if v is not None)
    return str(value)


def _tokens_in(text: str, vocab: set) -> list[str]:
    """按词表抽词，保持首次出现顺序、去重。

    三条实测踩过的规则：
    1. **连字符词拆分量**：`white-gold` 要拆出 `white` / `gold`（B · img14 实测
       把 typography.color="white-gold" 当整词漏检）；
    2. 中文词按子串匹配：`不锈钢` / `磨砂` 这种多字词不可能靠「逐词相等」命中；
    3. 顺序必须确定：英文按文本出现顺序，多字中文词按词表排序追加——否则同一份
       IR 两次算出不同顺序，binding.json 就不稳定。
    """
    out: list[str] = []
    for w in _words(text):
        for cand in [w] + w.split("-"):
            if cand in vocab and cand not in out:
                out.append(cand)
    for v in sorted(vocab):
        if (len(v) > 1 and any(ord(c) > 127 for c in v) and v in (text or "")
                and v not in out):
            out.append(v)
    return out


def _direction_tokens(anchor: str) -> list[str]:
    """方位串 → 方向词。用 expand.DIRECTIONS 的词表 + 归一化同义词。"""
    a = _norm_space(anchor).lower().replace("_", "-")
    hits: list[str] = []
    for w in re.findall(r"[a-z]+", a):
        if w in DIRECTIONS and w not in hits:
            hits.append(w)
    return hits


def _anchor_zone_ok(anchor: str, bbox: list[float] | None) -> bool | None:
    """bbox 中心是否落在 anchor 声明的区域里。无法判定返回 None（不做校验）。"""
    zone = _anchor_zone(anchor or "")
    if zone is None or not bbox or len(bbox) < 4:
        return None
    x0z, x1z, y0z, y1z = zone
    cx = (float(bbox[0]) + float(bbox[2])) / 2.0
    cy = (float(bbox[1]) + float(bbox[3])) / 2.0
    return (x0z <= cx <= x1z) and (y0z <= cy <= y1z)


# ─────────────────────────────────────────────────────────────
# caption 的可定位重建：子句 → 字符区间
# ─────────────────────────────────────────────────────────────

def render_with_spans(clauses) -> tuple[str, list[tuple[object, int, int]]]:
    """按 render_prose 的同一规则重建 caption，并记下每个子句的字符区间。

    为什么复制一遍渲染逻辑而不是在 caption 上做正则：绑定判据要的是「哪一段属于
    哪个实体」，正则切不出这个归属。复制有漂移风险，所以**强制断言**重建结果与
    `render_prose(clauses)` 逐字节相同；不同就 raise，绝不静默退化（否则判据会
    悄悄变宽松，而这正是 G4 那轮踩过的坑）。
    """
    buckets: dict[str, list] = {}
    for c in clauses:
        buckets.setdefault(c.dim, []).append(c)

    units: list[tuple[str, list]] = []          # (句子文本(未 strip), 组成它的子句)
    order = list(DIM_ORDER) + [d for d in buckets if d not in DIM_ORDER]
    for dim in order:
        items = buckets.get(dim)
        if not items:
            continue
        buf: list = []
        for c in items:
            if c.source == "note":
                if buf:
                    units.append((", ".join(x.text for x in buf), list(buf)))
                    buf = []
                units.append((c.text, [c]))
            else:
                buf.append(c)
        if buf:
            units.append((", ".join(x.text for x in buf), list(buf)))

    text_parts: list[str] = []
    spans: list[tuple[object, int, int]] = []
    cursor = 0
    for i, (raw, group) in enumerate(units):
        raw = raw.strip()
        if not raw:
            continue
        body = raw.rstrip(".")
        lead = len(body) - len(body.lstrip())
        start = cursor + lead
        text_parts.append(body)
        cursor = start + len(body)
        # 句子之间用 ". " 连接，最后统一补 "."
        sep = 0 if i == len(units) - 1 else 2
        # 子句在句子里的相对位置（忽略大小写：capitalize_sentences 会改句首字母）
        low_body, offset = body.lower(), 0
        for c in group:
            # 与 body 同口径：strip 后再去掉句末 "."（渲染时句子连接处不吃句号，
            # 统一在最后补）。note 子句自带句号，不这样归一就永远定位不到。
            needle = c.text.strip().rstrip(".").lower()
            if not needle:
                continue
            at = low_body.find(needle, offset)
            if at < 0:                      # 理论上不会发生（body 就是它们拼的）
                continue
            spans.append((c, start + at, start + at + len(needle)))
            offset = at + len(needle)
        cursor += sep

    text = ". ".join(p.strip().rstrip(".") for p in text_parts if p.strip())
    if not text:
        return "", []
    # capitalize_sentences：只改句首字母，不改变长度
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    final = " ".join(p[:1].upper() + p[1:] if p else p for p in parts) + "."
    expect = render_prose(clauses)
    if final != expect:
        raise RuntimeError(
            "render_with_spans 与 render_prose 不一致——判据的定位层已漂移。\n"
            "  重建：%r\n  实际：%r" % (final[:200], expect[:200]))
    return final, spans


def _entity_clause_map(ir: IR, clauses, spans) -> dict[str, tuple[int, int, str]]:
    """实体 id → (起, 止, 子句文本)。同描述被合并成一组时，组内实体共用组子句。

    这里必须**按维度挑**：同一个实体 id 会出现在两类子句里——subject（实体本身的
    描述）和 relation（"the man faces the grey wall"，entity_id 记的是主语）。
    取最后一个会把实体指到关系句上，颜色/材质就全都「不在自己子句里」了（实测踩过）。
    """
    subject_map = {c.entity_id: (s, e, c.text) for c, s, e in spans
                   if c.entity_id and c.dim == "subject"}
    text_map = {c.entity_id: (s, e, c.text) for c, s, e in spans
                if c.entity_id and c.dim == "text"}

    # 同组 key：与 clauses.build_clauses 的归并口径一致（去掉方位后的描述）
    group_ids: dict[str, list[str]] = {}
    for e in ir.entities:
        if e.kind == "text":
            continue
        group_ids.setdefault(describe_entity(e, with_anchor=False), []).append(e.id)

    out: dict[str, tuple[int, int, str]] = {}
    for e in ir.entities:
        if e.kind == "text" and e.id in text_map:
            out[e.id] = text_map[e.id]
        elif e.id in subject_map:
            out[e.id] = subject_map[e.id]
    for ids in group_ids.values():
        span = next((subject_map[i] for i in ids if i in subject_map), None)
        if span is None:
            continue
        for i in ids:
            out[i] = span
    return out


# ─────────────────────────────────────────────────────────────
# 五类判据
# ─────────────────────────────────────────────────────────────

def _own_descriptor_text(entity) -> str:
    """实体自己的描述文本。刻意**不含** notes / 全局 lighting —— 那些不是绑定证据。"""
    d = entity.descriptors or {}
    keys = ("color", "hair", "skin", "face", "wardrobe", "props", "material",
            "build", "detail", "tags", "expression", "pose")
    parts = [_flatten(d.get(k)) for k in keys if d.get(k)]
    parts.append(entity.label or "")
    typ = entity.typography or {}
    if typ.get("color"):
        parts.append(_flatten(typ["color"]))
    return " ; ".join(p for p in parts if p)


def _material_text(entity) -> str:
    """材质类只看「衣物/道具/材料/颜色」字段。

    人物不取 hair/skin/face：那几栏里全是 "smooth / glossy" 这类表面词，
    取进来会让材质类退化成「有没有写皮肤光泽」，与「材质绑到哪个实体」无关。
    """
    d = entity.descriptors or {}
    keys = ("wardrobe", "props", "material", "color")
    if entity.kind != "character":
        keys = keys + ("build", "detail", "tags")
        return " ; ".join([_flatten(d.get(k)) for k in keys if d.get(k)]
                          + [entity.label or ""])
    return " ; ".join(_flatten(d.get(k)) for k in keys if d.get(k))


def _distance_ok(caption: str, clause_span: tuple[int, int], token: str,
                 n: int = DISTANCE_N) -> tuple[bool, int]:
    """词是否出现在子句内、且距子句起点 ≤ n 词。返回 (命中, 词距)。

    英文与连字符词按「整词或分量」匹配：方位 "top-left" 在分词器眼里是一个词，
    但方向词表里是 top / left 两个——不拆分量的话一条正确的 "in the top-left"
    会被判成没绑（§5.2 修过）。中文词按子串定位，因为中文分词粒度与词表不一致。
    """
    start, end = clause_span
    clause = caption[start:end]
    if any(ord(c) > 127 for c in token):
        at = clause.find(token)
        if at < 0:
            return False, -1
        dist = len(_words(clause[:at]))
        return dist <= n, dist
    hi = _words(clause)
    pos = [i for i, w in enumerate(hi) if w == token or token in w.split("-")]
    if not pos:
        return False, -1
    return pos[0] <= n, pos[0]


def _measure_color(entity, caption, span) -> dict:
    expected = _tokens_in(_own_descriptor_text(entity), COLOR_WORDS)
    inst = {"entity_id": entity.id, "kind": entity.kind, "class": "color",
            "expected": expected, "bound": False, "missing": [],
            "details": [], "applicable": bool(expected)}
    if not expected:
        return inst
    ok_all = True
    for tok in expected:
        hit, dist = _distance_ok(caption, span, tok)
        inst["details"].append({"token": tok, "in_own_clause": hit, "distance": dist})
        if not hit:
            inst["missing"].append(tok)
            ok_all = False
    inst["bound"] = ok_all
    return inst


def _measure_material(entity, caption, span) -> dict:
    expected = _tokens_in(_material_text(entity), MATERIAL_WORDS)
    inst = {"entity_id": entity.id, "kind": entity.kind, "class": "material",
            "expected": expected, "bound": False, "missing": [], "details": [],
            "applicable": bool(expected)}
    if not expected:
        return inst
    ok_all = True
    for tok in expected:
        hit, dist = _distance_ok(caption, span, tok)
        inst["details"].append({"token": tok, "in_own_clause": hit, "distance": dist})
        if not hit:
            inst["missing"].append(tok)
            ok_all = False
    inst["bound"] = ok_all
    return inst


def _measure_anchor(entity, caption, span) -> dict:
    anchor = str((entity.spatial or {}).get("anchor") or "").strip()
    dirs = _direction_tokens(anchor)
    inst = {"entity_id": entity.id, "kind": entity.kind, "class": "spatial",
            "anchor": anchor, "expected": dirs, "bound": False, "missing": [],
            "details": [], "applicable": bool(anchor), "channel_conflict": None}
    if not anchor:
        return inst
    ok_all = True
    if not dirs:
        # 方位串里没有词表能认的方向词（如 "mid-air"）：退化成「原文是否出现在子句里」。
        start, end = span
        phrase = " ".join(anchor.lower().replace("_", "-").split())
        ok_all = phrase in " ".join(caption[start:end].lower().split())
        inst["details"].append({"token": phrase, "in_own_clause": ok_all, "distance": 0})
        if not ok_all:
            inst["missing"].append(phrase)
    for tok in dirs:
        hit, dist = _distance_ok(caption, span, tok)
        inst["details"].append({"token": tok, "in_own_clause": hit, "distance": dist})
        if not hit:
            inst["missing"].append(tok)
            ok_all = False
    # 两通道互证：bbox 是模型自己给的坐标时，中心必须落在 anchor 声明的区域。
    # bbox_source=="anchor" 的不判（那是用方位重建出来的，判了等于自己证自己）。
    src = (entity.spatial or {}).get("bbox_source")
    box = (entity.spatial or {}).get("bbox_norm")
    agree = _anchor_zone_ok(anchor, box) if src == "model" else None
    inst["channel_conflict"] = None if agree is None else (not agree)
    if agree is False:
        ok_all = False
        inst["missing"].append("bbox≠anchor 象限")
    inst["bound"] = ok_all
    return inst


_QUOTED = re.compile(r'"([^"]{1,600})"', re.DOTALL)


def _quoted_spans(caption: str) -> list[str]:
    return [m.group(1) for m in _QUOTED.finditer(caption or "")]


def _measure_text(entity, caption, span=None) -> dict:
    """图内文字类：只测**被标成 text** 的实体。

    为什么要求 kind=="text"：反推管线里文字专线会把文字实体造出来；如果模型把一段
    文字塞进 character/object 的 `text_content`，那是**类型错误**（归 §7.1 讨论），
    不是「图内文字绑定」的对象。放开这个条件会让分母混进「人物带一段描述」的噪声
    （实测 A 有 2 个 character 带 text_content，分母 28 vs 26）——判据文档写的是
    kind=text，实现就必须是同一条线。
    """
    lines = [ln.strip() for ln in (entity.text_content or "").splitlines() if ln.strip()]
    applicable = entity.kind == "text" and bool(lines)
    inst = {"entity_id": entity.id, "kind": entity.kind, "class": "text",
            "lines": lines, "bound": False, "missing": [], "applicable": applicable,
            "details": []}
    if not applicable:
        return inst
    quoted = _quoted_spans(caption)
    ok_all = True
    for ln in lines:
        # 逐字：该行必须是某一个引号块的子串。多行文字块在散文里仍是一个引号块
        # （"第一行\n第二行"），所以不能只查 `"整块"` 是否存在。
        hit = any(ln in q for q in quoted)
        inst["details"].append({"line": ln, "verbatim_in_quotes": hit})
        if not hit:
            inst["missing"].append(ln)
            ok_all = False
    inst["bound"] = ok_all
    return inst


def _measure_count(ir: IR, clauses, spans, canvas_type: str) -> dict:
    """数量绑定：同 label 实体成组，组子句要表达「有几个」。"""
    groups: dict[tuple, list[str]] = {}
    for e in ir.entities:
        if e.kind not in ("character", "object"):
            continue
        groups.setdefault((e.kind, (e.label or "").strip().lower()), []).append(e.id)

    # 「同一描述被合并」时 clauses 会补一句 "the same figure appears again ..."
    dup_texts = [c.text for c, _s, _e in spans
                 if "appears again" in (c.text or "")]

    instances, skipped = [], []
    for (kind, label), ids in groups.items():
        if len(ids) < 2:
            continue
        if canvas_type and canvas_type != "single":
            # 多格里同一个人的重复出现不是「数量」——那是分格画的语义，
            # 硬判会把「四格漫画里同一位主角」记成「四个人」。
            skipped.append({"class": "count", "label": label, "n": len(ids),
                            "reason": "canvas=%s，跨格重复不是数量" % canvas_type})
            continue
        texts = []
        # 组子句文本：从 duplicate/普通子句里按 entity_id 找
        for c, _s, _e in spans:
            if c.entity_id == ids[0] or c.entity_id in ids:
                texts.append(c.text)
        texts.extend(dup_texts)
        hay = " ".join(texts).lower()
        n = len(ids)
        hit_word = next((w for w, v in COUNT_WORDS.items() if v == n and w in hay), "")
        bound = bool(hit_word) or (n == 2 and "appears again" in hay)
        instances.append({"class": "count", "entity_ids": ids, "label": label,
                          "kind": kind, "n": n, "expected": n, "bound": bound,
                          "evidence": hit_word or ("appears again" if bound else ""),
                          "applicable": True})
    return {"instances": instances, "skipped": skipped}


_NORM_TEXT = re.compile(r"[^0-9a-z\u3400-\u9fff]+")


def _norm_text(s: str) -> str:
    return _NORM_TEXT.sub("", (s or "").lower())


def _text_cross_pass(payload: dict, ir: IR) -> dict:
    """文字两遍互证：取证遍读到的字，有没有被独立的转录遍证实。

    为什么这是一个真判据：取证遍与转录遍是**两次独立带图调用**（第二遍只问「把字抄下来」），
    两遍都读出同一串字，比任何单遍自证都强；只有取证遍读过、转录遍没读到的，
    更可能是模型在编（或转录遍漏了，所以只作辅助指标，不单独定罪）。
    """
    blocks: list[str] = []
    for call in payload.get("calls") or []:
        if call.get("name") != "text_blocks" or not call.get("content"):
            continue
        try:
            from moss_llm.smith.reverse import extract_json
            data = extract_json(call["content"])
        except Exception:
            continue
        for b in data.get("blocks") or []:
            if isinstance(b, dict) and str(b.get("text") or "").strip():
                blocks.append(str(b["text"]))
    norm_blocks = [_norm_text(b) for b in blocks]
    stats = {"blocks": len(norm_blocks), "from_evidence": 0, "corroborated": 0,
             "transcription_only": 0}
    for e in ir.entities:
        if e.kind != "text" or not (e.text_content or "").strip():
            continue
        n = _norm_text(e.text_content)
        if not n:
            continue
        from_tx = any("transcription pass" in (ev or "") for ev in (e.evidence or []))
        if from_tx:
            stats["transcription_only"] += 1
            continue
        stats["from_evidence"] += 1
        if any(b == n or (min(len(b), len(n)) >= 4 and (b in n or n in b))
               for b in norm_blocks):
            stats["corroborated"] += 1
    return stats


def record_ir(payload: dict) -> IR:
    """从记录里还原 IR，**保留落盘时的实体顺序**。

    为什么不能直接 `IR.from_dict`：`from_dict` 会 `normalize()`，而 normalize 会按
    importance 重排实体；但落盘 caption 是反推管线在 normalize **之后**追加文字专线
    实体的（那时没有再次排序）。同一份 IR 两种顺序 ⇒ 重建出的子句顺序不同 ⇒
    定位层错位、颜色/方位会被误判成「不在自己子句里」（实测踩过）。
    落盘顺序才是生成那份 caption 时的真实顺序，所以以它为准。
    """
    from moss_llm.smith.ir import Entity as _Entity
    d = payload["ir"]
    ir = IR.from_dict(d)
    ir.entities = [_Entity.from_dict(e) for e in d.get("entities") or []]
    return ir


def measure_record(payload: dict, meta: dict) -> dict:
    """一份 caption 记录 → 五类绑定结果。坏记录也返回带 error 的结构。"""
    out = {"model": payload.get("model_key"), "image_id": meta["id"],
           "category": meta["category"], "ok": bool(payload.get("ok")),
           "classes": {}, "warnings": []}
    if not payload.get("ok"):
        out["error"] = payload.get("error", "record not ok")
        return out
    ir = record_ir(payload)
    clauses = build_clauses(ir)
    caption = payload.get("caption") or ""
    rebuilt, spans = render_with_spans(clauses)
    if _norm_space(rebuilt) != _norm_space(caption):
        out["warnings"].append("落盘 caption 与 IR 重建结果不一致，判据按重建文本执行")
    entity_map = _entity_clause_map(ir, clauses, spans)

    per_class: dict[str, dict] = {k: {"applicable": 0, "bound": 0, "instances": [],
                                      "skipped": []}
                                  for k in ("color", "material", "spatial", "text", "count")}
    for e in ir.entities:
        span = entity_map.get(e.id)
        if span is None:
            # 没进散文的实体（预算/关系原因）：applicable 照算，bound 为假，
            # 但要把原因说清楚，不然读表的人以为模型根本没记。
            for fn, cls in ((_measure_color, "color"), (_measure_material, "material"),
                            (_measure_anchor, "spatial"), (_measure_text, "text")):
                inst = fn(e, "", (0, 0))
                if inst.get("applicable"):
                    inst["bound"] = False
                    inst["missing"] = list(inst.get("expected") or []) + ["[未进入 caption]"]
                    per_class[cls]["instances"].append(inst)
                    per_class[cls]["applicable"] += 1
            continue
        for fn, cls in ((_measure_color, "color"), (_measure_material, "material"),
                        (_measure_anchor, "spatial"), (_measure_text, "text")):
            inst = fn(e, caption, (span[0], span[1]))
            if not inst.get("applicable"):
                continue
            per_class[cls]["instances"].append(inst)
            per_class[cls]["applicable"] += 1
            if inst["bound"]:
                per_class[cls]["bound"] += 1

    count_res = _measure_count(ir, clauses, spans,
                               (ir.canvas or {}).get("canvas_type", "single"))
    for inst in count_res["instances"]:
        per_class["count"]["instances"].append(inst)
        per_class["count"]["applicable"] += 1
        if inst["bound"]:
            per_class["count"]["bound"] += 1
    per_class["count"]["skipped"] = count_res["skipped"]

    for box in per_class.values():
        box["coverage"] = (round(box["bound"] / box["applicable"], 4)
                           if box["applicable"] else None)
    out["classes"] = per_class
    out["aux"] = {
        "entities": len(ir.entities),
        "captions_words": len(_words(caption)),
        "text_entities": sum(1 for e in ir.entities if e.kind == "text"),
        "canvas_type": (ir.canvas or {}).get("canvas_type", ""),
        "text_cross_pass": _text_cross_pass(payload, ir),
    }
    return out


# ─────────────────────────────────────────────────────────────
# 汇总与报表
# ─────────────────────────────────────────────────────────────

def aggregate(records: list[dict], model_keys: list[str]) -> dict:
    classes = ("color", "material", "spatial", "text", "count")
    out: dict = {"models": {}, "classes": list(classes)}
    for model in model_keys:
        recs = [r for r in records if r["model"] == model]
        ok_recs = [r for r in recs if r.get("ok")]
        mbox = {"records": len(recs), "ok": len(ok_recs),
                "failed": len(recs) - len(ok_recs), "classes": {}}
        for cls in classes:
            applicable = sum(r["classes"][cls]["applicable"] for r in ok_recs)
            bound = sum(r["classes"][cls]["bound"] for r in ok_recs)
            per_image = {}
            for r in ok_recs:
                c = r["classes"][cls]
                per_image[r["image_id"]] = {"applicable": c["applicable"],
                                            "bound": c["bound"],
                                            "coverage": c["coverage"]}
            mbox["classes"][cls] = {
                "applicable": applicable, "bound": bound,
                "coverage": round(bound / applicable, 4) if applicable else None,
                "images_with_denominator": sum(1 for v in per_image.values()
                                               if v["applicable"]),
                "per_image": per_image,
            }
        aux = [r.get("aux") or {} for r in ok_recs]
        cross = {"blocks": 0, "from_evidence": 0, "corroborated": 0,
                 "transcription_only": 0}
        for a in aux:
            cp = a.get("text_cross_pass") or {}
            for k in cross:
                cross[k] += int(cp.get(k) or 0)
        cross["corroboration_rate"] = (round(cross["corroborated"]
                                             / cross["from_evidence"], 4)
                                       if cross["from_evidence"] else None)
        mbox["aux"] = {
            "entities_mean": round(sum(a.get("entities", 0) for a in aux) / len(aux), 2)
            if aux else None,
            "entities_total": sum(a.get("entities", 0) for a in aux),
            "caption_words_mean": round(sum(a.get("captions_words", 0) for a in aux)
                                        / len(aux), 1) if aux else None,
            "text_entities_total": sum(a.get("text_entities", 0) for a in aux),
            "canvas_multi_panel": sum(1 for a in aux
                                      if a.get("canvas_type") == "multi_panel"),
            "text_cross_pass": cross,
        }
        out["models"][model] = mbox
    return out


def format_table(summary: dict) -> str:
    """2 模型 × 5 类覆盖率表（附分母）。"""
    models = list(summary["models"])
    classes = summary["classes"]
    head = "| 类别 | " + " | ".join("%s 覆盖（bound/分母）" % m for m in models) + " |"
    line = "|---|" + "---|" * len(models)
    rows = [head, line]
    for cls in classes:
        cells = []
        for m in models:
            c = summary["models"][m]["classes"][cls]
            cov = "n/a" if c["coverage"] is None else "%.0f%%" % (c["coverage"] * 100)
            cells.append("%s（%d/%d）" % (cov, c["bound"], c["applicable"]))
        rows.append("| %s | %s |" % (cls, " | ".join(cells)))
    return "\n".join(rows)


def format_aux(summary: dict) -> str:
    rows = ["| 指标 | %s |" % " | ".join(summary["models"]), "|---|" + "---|" * len(summary["models"])]
    labels = [("records", "记录数"), ("ok", "成功"), ("failed", "失败")]
    for key, label in labels:
        rows.append("| %s | %s |" % (label, " | ".join(
            str(summary["models"][m][key]) for m in summary["models"])))
    aux_labels = [("entities_total", "IR 实体总数"), ("entities_mean", "IR 实体/图（均值）"),
                  ("caption_words_mean", "caption 词数（均值）"),
                  ("text_entities_total", "text 实体总数"),
                  ("canvas_multi_panel", "判为 multi_panel 的图数")]
    for key, label in aux_labels:
        rows.append("| %s | %s |" % (label, " | ".join(
            str(summary["models"][m]["aux"].get(key)) for m in summary["models"])))
    rows.append("| 文字两遍互证（取证遍被转录遍证实） | %s |" % " | ".join(
        _cross_cell(summary["models"][m]["aux"].get("text_cross_pass") or {})
        for m in summary["models"]))
    return "\n".join(rows)


def _cross_cell(cp: dict) -> str:
    if not cp.get("from_evidence"):
        return "n/a（无取证遍文字）"
    rate = cp.get("corroboration_rate")
    return "%d/%d%s" % (cp.get("corroborated", 0), cp.get("from_evidence", 0),
                        "" if rate is None else "（%.0f%%）" % (rate * 100))


# ─────────────────────────────────────────────────────────────
# 校准：机检 vs 代理目检
# ─────────────────────────────────────────────────────────────

def _load_image_meta(path: str | Path | None = None) -> dict:
    p = Path(path) if path else (OUT / "images.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    return {i["id"]: i for i in data["images"]}


def cmd_measure(args) -> int:
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    images = _load_image_meta(args.images)
    json_root = Path(args.json_root)
    records, missing = [], []
    for model in models:
        for img_id, meta in images.items():
            path = json_root / model / ("%s.json" % img_id)
            if not path.is_file():
                missing.append({"model": model, "image_id": img_id, "reason": "缺记录"})
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.append(measure_record(payload, meta))
    summary = aggregate(records, models)
    # 距离窗口的实测分布：用来证明 DISTANCE_N 没在卡正常描述（写进 docs 的证据）。
    dists = []
    for rec in records:
        for cls in ("color", "material", "spatial"):
            for inst in (rec.get("classes", {}).get(cls) or {}).get("instances") or []:
                for det in inst.get("details") or []:
                    if (det.get("distance") or -1) >= 0:
                        dists.append(det["distance"])
    dest = {"generated_by": "tools/g7_binding.py measure",
            "images": str(args.images), "json_root": str(json_root),
            "distance_n": DISTANCE_N,
            "distance_stats": {"max_observed": max(dists) if dists else None,
                               "tokens_measured": len(dists),
                               "over_n": sum(1 for d in dists if d > DISTANCE_N)},
            "definitions": {
                "color": "实体自有描述字段里的颜色词，出现在该实体子句内且距子句起点≤N词",
                "material": "同上，材质/表面词",
                "spatial": "方位词同上；bbox_source=model 时 bbox 中心须落在 anchor 象限",
                "text": "text_content 每行逐字出现在直双引号内",
                "count": "同 label 组 n≥2（仅单图 canvas）时，组子句/重复子句表达数量",
            },
            "summary": summary, "records": records, "missing": missing}
    out_json = Path(args.out)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(dest, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    table = format_table(summary)
    aux = format_aux(summary)
    out_table = Path(args.table)
    out_table.parent.mkdir(parents=True, exist_ok=True)
    out_table.write_text(
        "# G7 binding 覆盖率（机检）\n\n%s\n\n%s\n" % (table, aux), encoding="utf-8")
    print(table)
    print()
    print(aux)
    if missing:
        print("\n缺记录 %d 条：%s" % (len(missing), json.dumps(missing[:6], ensure_ascii=False)))
    return 0


def cmd_calib_sheet(args) -> int:
    """导出待目检的机检单元：一格 = 模型 × 图 × 类别。"""
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    images = _load_image_meta(args.images)
    json_root = Path(args.json_root)
    cells = []
    for model in models:
        for img_id in ids:
            path = json_root / model / ("%s.json" % img_id)
            if not path.is_file():
                continue
            rec = measure_record(json.loads(path.read_text(encoding="utf-8")),
                                 images[img_id])
            if not rec.get("ok"):
                continue
            for cls, box in rec["classes"].items():
                if not box["applicable"]:
                    continue
                cells.append({
                    "model": model, "image_id": img_id, "class": cls,
                    "applicable": box["applicable"], "bound": box["bound"],
                    "machine_pass": box["bound"] == box["applicable"],
                    "evidence": [{"entity_id": i.get("entity_id") or i.get("entity_ids"),
                                  "expected": i.get("expected"), "bound": i["bound"],
                                  "missing": i.get("missing")}
                                 for i in box["instances"]],
                })
    dest = Path(args.calib_dir) / "machine_cells.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"cells": cells, "images": str(args.images),
                                "json_root": str(json_root)},
                               ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print("已写 %s：%d 格（%d 模型 × %d 图 × 5 类中可判的部分）"
          % (dest, len(cells), len(models), len(ids)))
    return 0


def cmd_calibrate(args) -> int:
    """机检 vs 代理目检的一致率。agent 文件结构：

    {"cells": [{"model","image_id","class","agent_pass": true/false, "note": "..."}]}
    """
    calib_dir = Path(args.calib_dir)
    machine = json.loads((calib_dir / "machine_cells.json").read_text(encoding="utf-8"))
    agent_path = calib_dir / "agent_cells.json"
    if not agent_path.is_file():
        print("缺 %s：先按 machine_cells.json 逐格目检并填 agent_pass。" % agent_path,
              file=sys.stderr)
        return 2
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    key = lambda c: (c["model"], c["image_id"], c["class"])           # noqa: E731
    a = {key(c): c for c in agent["cells"]}
    total = agree = 0
    disagreements = []
    for mc in machine["cells"]:
        k = key(mc)
        if k not in a or "agent_pass" not in a[k]:
            continue
        total += 1
        if bool(a[k]["agent_pass"]) == bool(mc["machine_pass"]):
            agree += 1
        else:
            disagreements.append({
                "model": mc["model"], "image_id": mc["image_id"], "class": mc["class"],
                "machine_pass": mc["machine_pass"], "agent_pass": a[k]["agent_pass"],
                "machine": "%d/%d" % (mc["bound"], mc["applicable"]),
                "agent_note": a[k].get("note", ""),
                "machine_evidence": mc["evidence"],
            })
    rate = agree / total if total else 0.0
    payload = {"cells_compared": total, "agreement": round(rate, 4),
               "verdict": "pass" if rate >= args.threshold else "fail",
               "threshold": args.threshold, "disagreements": disagreements}
    (calib_dir / "calibration.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("机检 vs 目检：%d/%d 格一致 = %.1f%%（阈值 %.0f%%）"
          % (agree, total, rate * 100, args.threshold * 100))
    for d in disagreements[:20]:
        print("  ✗ %s %s %-8s 机检=%s 目检=%s  %s"
              % (d["model"], d["image_id"], d["class"], d["machine"],
                 "pass" if d["agent_pass"] else "fail", d["agent_note"][:60]))
    return 0 if rate >= args.threshold else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G7 binding 机检与校准")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--models", default="A-4B,B-9B")
    m.add_argument("--images", default=str(OUT / "images.json"),
                   help="gold set 清单（默认第一轮；60 图轮可指 images60.json）")
    m.add_argument("--json-root", dest="json_root", default=str(OUT / "json"),
                   help="单图 JSON 根目录（默认第一轮；round2 指到 out/g7/round2/json）")
    m.add_argument("--out", default=str(OUT / "binding.json"),
                   help="逐实例结果 JSON 输出（round2 用独立路径，不覆盖第一轮）")
    m.add_argument("--table", default=str(OUT / "binding_table.md"))
    m.set_defaults(func=cmd_measure)
    s = sub.add_parser("calib-sheet")
    s.add_argument("--models", default="A-4B,B-9B")
    s.add_argument("--ids", required=True)
    s.add_argument("--images", default=str(OUT / "images.json"))
    s.add_argument("--json-root", dest="json_root", default=str(OUT / "json"))
    s.add_argument("--calib-dir", dest="calib_dir", default=str(OUT / "calib"))
    s.set_defaults(func=cmd_calib_sheet)
    c = sub.add_parser("calibrate")
    c.add_argument("--threshold", type=float, default=0.80)
    c.add_argument("--calib-dir", dest="calib_dir", default=str(OUT / "calib"))
    c.set_defaults(func=cmd_calibrate)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
