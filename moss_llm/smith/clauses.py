# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""IR → 子句计划（clause plan）。

这是编译器的中端：把原子事实拆成带维度标签、带重要度的子句。
后端只负责「排序 + 连接 + 改写」，不再碰事实。

为什么中间要这一层：
- 预算裁剪发生在子句粒度，不在句子粒度（Krea 2 的 512 截断砍句子会砍掉主体）。
- 同一份子句可以被不同后端用不同顺序重排，不需要重跑 LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ir import IR, Entity

#: 维度 → 在散文里的自然顺序（MOSS 七维）
DIM_ORDER = ("subject", "setting", "camera", "lighting", "material", "medium", "text")

#: IR.notes 的键 → 落到哪个散文维度
_NOTE_DIM = {"subject": "subject", "setting": "setting", "composition": "setting",
             "camera": "camera", "lighting": "lighting", "material": "material",
             "medium": "medium", "text": "text"}

#: 观察散文的默认重要度。一律低于同维度的结构化事实——
#: 事实是身份（丢了主体就画错人），散文是修饰（丢了只是不够润）。
_NOTE_IMP = {"subject": 0.87, "setting": 0.82, "camera": 0.77,
             "lighting": 0.82, "material": 0.47, "medium": 0.57, "text": 0.94}

_VOWELS = "aeiou"

#: 去重时不算内容的词
_STOP = set("""a an the of in on at to for with and or is are was were be been being
this that these those it its as by from into over under near behind front left right
top bottom center centre very more most some any all not no""".split())


@dataclass
class Clause:
    dim: str
    text: str
    importance: float
    entity_id: str | None = None
    #: "fact" = 结构化事实渲染；"note" = 反推时 VLM 写的观察散文。
    #: 预算裁剪时 fact 先留、note 先走：事实是身份，散文是修饰。
    source: str = "fact"

    def __post_init__(self):
        self.text = " ".join(self.text.split())


def _article(word: str) -> str:
    w = (word or "").strip().lower()
    return "an" if w[:1] in _VOWELS else "a"


def _age_phrase(e: Entity) -> str:
    """年龄硬闸的落点：每个 character 必须显式带岁数。

    gender 会先归一到英文名词：中文模型会写 "女性" / "男性"，
    直接拼进英文散文就成了 "a 28-year-old 女性"——两种语言混在一句里。

    gender 缺失时**退回 label**：真机实测 8B 会把性别写在 label 上
    （label="woman"、descriptors 留空），照原逻辑渲染成 "a 28-year-old person" ——
    源里明明写着 woman，输出却成了「一个人」。label 只在是短名词短语时才拿来兜底，
    长句 label（"handmade ceramic cup with…"）不用。
    """
    gender = (e.descriptors.get("gender") or "").strip()
    noun = {"woman": "woman", "man": "man", "girl": "girl", "boy": "boy",
            "female": "woman", "male": "man",
            "女性": "woman", "男性": "man", "女孩": "girl", "男孩": "boy",
            "女": "woman", "男": "man", "女子": "woman", "男子": "man",
            "女人": "woman", "男人": "man"}.get(gender, gender)
    if not noun:
        lab = (e.label or "").strip()
        noun = lab if 0 < len(lab.split()) <= 3 else ""
    noun = noun or "person"
    if e.age_years is None:
        return "%s %s" % (_article(noun), noun)
    age = int(e.age_years)
    # 英文冠词看读音：8 / 11 / 18 / 80-89 用 an
    art = "an" if (age in (8, 11, 18) or 80 <= age <= 89) else "a"
    return "%s %d-year-old %s" % (art, age, noun)


def describe_entity(e: Entity, with_anchor: bool = True, extras: bool = True) -> str:
    """把一个实体摊成一段名词短语。确定性，不含修辞。

    with_anchor=False 用于 layout：位置已由 bbox 编码，再写方位是冗余。

    extras=False 只留**名词短语**该有的部分（年龄/性别/外观/穿着/手持），
    丢掉表情、姿态、build、detail 这类**谓语性**描述。
    为什么需要这个开关：时间线体裁里主语后面要接动词（`the baker lifts the
    shutters`），把 `a calm, unhurried manner` 塞进名词短语就会得到
    「…a calm, unhurried manner lifts the shutters」——半截句。图像后端是
    属性罗列，无所谓；散文不行。

    `descriptors.color` 会被渲染（物体前置成形容词「yellow raincoat」，人物写成
    「in yellow」）。为什么非渲染不可：模型抽取时最爱把颜色填进这个字段，而
    `expand.fixed_items` 会把 descriptors 里的颜色词当**硬事实**核对——记着却渲染不出来，
    verify 就报一条永远修不好的假错（IR 在撒谎）。真机实测就是这么漏掉 yellow / white 的。
    """
    d = e.descriptors
    color = str(d.get("color") or "").strip()
    if e.kind == "character":
        head = _age_phrase(e)
        withs = [d[k] for k in ("hair", "skin", "face") if d.get(k)]
        if withs:
            head += " with " + _join_list(withs)
        parts = [head]
        if d.get("wardrobe"):
            parts.append("wearing %s" % _join_list(d["wardrobe"]))
        if d.get("props"):
            parts.append("holding %s" % _join_list(d["props"]))
        if extras and d.get("expression"):
            parts.append(d["expression"])
        if extras and d.get("pose"):
            parts.append(d["pose"])
        if color and color.lower() not in " ".join(parts).lower():
            parts.append("in %s" % color)
    else:
        head = e.label or e.kind
        if color and color.lower() not in head.lower():
            head = "%s %s" % (color, head)
        if d.get("material"):
            head += " with %s" % d["material"]
        parts = [head]
        if extras and d.get("build"):
            parts.append(d["build"])
        if d.get("wardrobe"):
            parts.append(_join_list(d["wardrobe"]))
        if extras and d.get("detail"):           # 审计补进来的整句描述
            parts.append(str(d["detail"]))

    # 固定项颜色不只藏在 color/material 里：模型还会把「暖黄光」「杯里棕/黄液体」
    # 写进 light/content/condition。fixed_items 会把 descriptors 的**任意文本**里出现的
    # 颜色当硬事实，不渲染就必然报假 lost。这里只补事实短语，不做风格改写。
    for key in ("light", "content", "condition"):
        value = str(d.get(key) or "").strip()
        if value and value.lower() not in " ".join(parts).lower():
            parts.append(value)

    # `details`（复数）是反推产物里的细节目录；`detail`（单数）是审计补充字段。
    # fixed_items 把 descriptors 任意文本里的颜色当硬事实，而旧渲染只认 detail 单数，
    # 于是 details 里的颜色必然假 lost（A56 的 gold 只在这里）。字符串出一条短语，
    # 列表逐项 join；已在 parts 里出现过的项跳过（与上面颜色去重同一口径）。
    details = d.get("details")
    if isinstance(details, str):
        details = [details]
    elif not isinstance(details, (list, tuple)):
        details = []
    seen = " ".join(parts).lower()
    extra_details = []
    for item in details:
        value = str(item).strip()
        if value and value.lower() not in seen:
            extra_details.append(value)
            seen += " " + value.lower()
    if extra_details:
        parts.append(_join_list(extra_details))

    anchor = (e.spatial or {}).get("anchor")
    if with_anchor and anchor:
        parts.append("in the %s" % anchor)
    return ", ".join(p for p in parts if p)


def _case_applies(e: Entity) -> bool:
    """大小写只对含拉丁字母的文字有意义。中文写 "upper case" 是纯噪声。"""
    return bool((e.typography or {}).get("case")) and bool(re.search(r"[A-Za-z]", e.text_content or ""))


def describe_text(e: Entity, with_anchor: bool = True) -> str:
    """text 项的 caption：只描述排版与表面处理，不复述文字内容。

    中文没有大小写，模型却爱填 case="upper"。照写会得到 "Chinese text in bold
    lettering, upper case" 这种无意义描述，所以只对含拉丁字母的文字保留 case。

    字号读 `typography.visual_weight`（normalize 时由 importance 派生并冻结），
    不再直接读 importance——否则「为了不被预算砍掉而提权」会顺手改字号，
    变了画面却说的是改了优先级。老 IR 没有这个字段时退回老算法，行为不变。
    """
    typ = e.typography or {}
    content = e.text_content or ""
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", content))
    weight = str(typ.get("visual_weight") or "").strip().lower()
    if weight in ("large", "small"):
        size = weight.capitalize()
    else:
        size = "Large" if e.importance >= 0.95 else "Small"
    lang = "Chinese" if has_cjk else ""
    bits = [size]
    if typ.get("color"):
        bits.append(typ["color"])
    if lang:
        bits.append(lang)
    bits.append("text")
    head = " ".join(bits)
    tails = []
    if typ.get("weight"):
        tails.append("in %s lettering" % typ["weight"])
    if _case_applies(e):
        tails.append("%s case" % typ["case"])
    if typ.get("align"):
        tails.append("%s aligned" % typ["align"])
    if typ.get("style_hint"):
        tails.append(typ["style_hint"])
    anchor = (e.spatial or {}).get("anchor")
    if with_anchor and anchor:
        tails.append("at the %s" % anchor)
    s = head + (", " + ", ".join(tails) if tails else "")
    return s[:1].upper() + s[1:] + "."


def quote_text_block(value: str) -> str:
    """把一块图内文字渲染成**恰好一对**引号。

    多行压成空格，避免 `_QUOTE` / 逐行扫描把整块误读；内容自带直引号时改用
    curly/「」包裹，避免内层引号提前闭合。调用方（clauses + moss_image）共用
    这一处，保证幂等、不逐行二次包装。
    """
    flat = " ".join((value or "").split())
    if not flat:
        return '""'
    if '"' not in flat:
        return '"%s"' % flat
    if "“" not in flat and "”" not in flat:
        return "“%s”" % flat
    return "「%s」" % flat


def _join_list(items: list) -> str:
    """列表连接。元素自身带逗号时改用分号——否则 "a, b, c" 会糊成一团读不出结构。"""
    items = [str(i).strip() for i in items if str(i).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if any(", " in i for i in items):
        return "; ".join(items[:-1]) + "; and " + items[-1]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _relation_text(ir: IR, rel: dict) -> str:
    """关系句用**短标签**，不用整段实体描述——否则散文会重复三遍外观。"""
    subj, obj = ir.entity(rel.get("subject", "")), ir.entity(rel.get("object", ""))
    if not subj or not obj:
        return ""
    verbs = {
        "holding": "holds", "touching": "touches", "facing": "faces",
        "left_of": "is to the left of", "right_of": "is to the right of",
        "above": "is above", "below": "is below", "behind": "is behind",
        "in_front_of": "is in front of", "occluding": "partly covers",
        # 中文模型会直接给中文谓词
        "拿着": "holds", "握着": "holds", "触碰": "touches", "面对": "faces",
        "朝向": "faces", "在左": "is to the left of", "在右": "is to the right of",
        "在上": "is above", "在下": "is below", "在后方": "is behind",
        "在前方": "is in front of", "遮挡": "partly covers",
    }
    verb = verbs.get(rel.get("predicate", ""), rel.get("predicate", "relates to"))
    return "the %s %s the %s" % (subj.label or subj.kind, verb, obj.label or obj.kind)


def _stem(w: str) -> str:
    """极轻量词干化。只为让 centered/center、standing/stand 能对上，不做真 NLP。"""
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _tokens(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOP and len(w) > 2}


def _dedupe_note(note: str, fact_text: str, threshold: float = 0.4) -> str:
    """丢掉 note 里与事实重合度高的**句子**，保留新增信息。

    整条 note 一起丢太粗暴：实测 note 常是「前半句复述 + 后半句新信息」的混合体，
    整条丢会把新信息一起扔掉；整条留则散文里出现两遍同一句话。

    判据要两个条件同时成立才丢：至少 2 个实词重合**且**重合率 ≥ 45%。
    只用重合率会误杀「a 7-foot bronze statue」这种短句（一个词重合就是 50%）。
    """
    fact = _tokens(fact_text)
    if not fact:
        return note
    kept = []
    for sent in re.split(r"(?<=[.!?])\s+", note.strip()):
        want = _tokens(sent)
        if not want:
            continue
        hit = want & fact
        if len(hit) >= 2 and len(hit) / float(len(want)) >= threshold:
            continue
        kept.append(sent.strip())
    return " ".join(kept)


def build_clauses(ir: IR) -> list[Clause]:
    """产出全部子句，已按重要度降序。"""
    out: list[Clause] = []

    # 1. 主体
    # 四格漫画里同一个人会在 4 个格子各出现一次，模型会给出 4 段**完全相同**的描述。
    # 照抄会得到四遍 "a 28-year-old woman with black, long, tied back..."，
    # 一句话把 prompt 撑到 200 词。按「去掉方位后的描述」归并，重复次数改成一句方位补充。
    groups: dict[str, list[Entity]] = {}
    order: list[str] = []
    for e in ir.entities:
        if e.kind == "text":
            continue
        key = describe_entity(e, with_anchor=False)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)

    for key in order:
        group = groups[key]
        if len(group) == 1:
            out.append(Clause("subject", describe_entity(group[0]),
                              group[0].importance, group[0].id))
            continue
        # 分组后 key 不含 anchor（with_anchor=False）；首实体的 anchor 必须补回来，
        # 否则同一 label 重复出现时（G7 反推 IR 很常见）首实体方位会**系统性**丢：
        # e5 bottom-right + e6 right 同 key → 旧输出只剩 "in the right"。
        # 措辞与 describe_entity 的 "in the {anchor}" 一致；首实体无 anchor 时
        # subject 仍是 key，输出与旧版逐字节相同。
        anchor0 = (group[0].spatial or {}).get("anchor")
        subject = ", ".join([key, "in the %s" % anchor0]) if anchor0 else key
        out.append(Clause("subject", subject, group[0].importance, group[0].id))
        rest = [(e.spatial or {}).get("anchor") for e in group[1:]]
        if all(rest):
            out.append(Clause("setting", "the same figure appears again in the %s"
                              % _join_list(rest), group[0].importance * 0.8))
        else:
            out.append(Clause("setting", "the same figure appears again elsewhere in the frame",
                              group[0].importance * 0.8))

    # 1.5 观察散文在最后统一追加（见步骤 10），因为要去重必须先有全部事实。

    # 2. 关系（重要度取两端较低者，避免抢主体预算）
    # 按 (主语, 谓词, 宾语) 排一遍：关系是一组事实，数组顺序没有语义，
    # 但**输出**不能依赖它——否则同一份 IR 换个写法就得到不同的 prompt，
    # 团队之间永远对不上同一张图。（实测：打乱 relations 后 6 次里有 3 次输出不同。）
    for rel in sorted(ir.relations, key=lambda r: (str(r.get("subject", "")),
                                                   str(r.get("predicate", "")),
                                                   str(r.get("object", "")))):
        text = _relation_text(ir, rel)
        if not text:
            continue
        imp = min(ir.entity(rel["subject"]).importance,
                  ir.entity(rel["object"]).importance)
        out.append(Clause("setting", text, imp, rel.get("subject")))

    # 3. 场景
    setting_parts = [ir.canvas.get("background") or ""]
    for e in ir.entities:
        if e.kind == "background":
            setting_parts.append(e.label)
    comp = ir.composition
    rules = [str(r).replace("_", " ") for r in (comp.get("rules") or [])]
    if len(rules) == 1:
        setting_parts.append("%s composition" % rules[0])      # "centered composition"
    elif rules:
        setting_parts.append("composition follows " + _join_list(rules))
    if comp.get("horizon"):
        setting_parts.append(comp["horizon"])
    if comp.get("negative_space"):
        setting_parts.append(comp["negative_space"])
    if any(setting_parts):
        out.append(Clause("setting", ", ".join(p for p in setting_parts if p), 0.9))

    # 4. 相机
    cam = ir.camera
    if cam:
        bits = []
        if cam.get("shot"):
            shot = str(cam["shot"]).replace("_", " ").strip()
            # "close up" 是英文里唯一必须带连字符的景别写法
            shot = re.sub(r"\b(close|extreme close|full|medium close)[ ]up\b", r"\1-up", shot, flags=re.I)
            # 模型常写 "medium shot"，再加 " shot" 会得到 "medium shot shot"
            bits.append(shot if shot.lower().endswith("shot") else shot + " shot")
        if cam.get("angle"):
            bits.append(str(cam["angle"]).replace("_", " ") + " angle")
        if cam.get("lens_mm"):
            bits.append("%dmm lens" % int(cam["lens_mm"]))
        if cam.get("dof"):
            bits.append("%s depth of field" % cam["dof"])
        if cam.get("focus"):
            bits.append("focused on %s" % cam["focus"])
        if cam.get("motion"):
            bits.append(cam["motion"])
        if bits:
            out.append(Clause("camera", ", ".join(bits), 0.8))

    # 5. 光照
    lit = ir.lighting
    if lit:
        bits = [lit.get("key") or "", lit.get("fill") or "", lit.get("rim") or ""]
        if lit.get("time_of_day"):
            bits.append(str(lit["time_of_day"]))
        if lit.get("mood"):
            bits.append(str(lit["mood"]) + " mood")
        text = ", ".join(b for b in bits if b)
        if text:
            out.append(Clause("lighting", text, 0.85))

    # 6. 材质
    # 只收「主体描述里没写过」的材质：物体描述已经带了 "with {material}"，
    # 再单独列一句 "Textures: metallic, black, fountain pen" 就是复述。
    mats = []
    for e in ir.entities:
        m = e.descriptors.get("material")
        if not m:
            continue
        if str(m).lower() in describe_entity(e).lower():
            continue
        mats.append(m)
    if mats:
        out.append(Clause("material", "textures: " + _join_list(mats), 0.5))

    # 7. 氛围
    if ir.atmosphere:
        out.append(Clause("lighting", _join_list(ir.atmosphere), 0.4))

    # 8. 媒介/风格
    st = ir.style
    if st:
        bits = []
        if st.get("medium"):
            bits.append(str(st["medium"]).replace("_", " "))
        if st.get("substyle"):
            bits.append(_join_list(st["substyle"]))
        refs = [r["name"] for r in (st.get("artist_refs") or [])
                if r.get("policy", "allowed") != "blocked"]
        if refs:
            bits.append("in the manner of " + _join_list(refs))
        if bits:
            out.append(Clause("medium", ", ".join(bits), 0.6))

    # 9. 图内文字（必须逐字，单独成句）
    for e in ir.text_entities:
        typ = e.typography or {}
        tail = []
        if typ.get("weight"):
            tail.append(typ["weight"])
        if _case_applies(e):
            tail.append("%s case" % typ["case"])
        if typ.get("color"):
            tail.append(typ["color"])
        if typ.get("align"):
            tail.append("%s aligned" % typ["align"])
        if typ.get("style_hint"):
            tail.append(typ["style_hint"])
        anchor = (e.spatial or {}).get("anchor")
        if anchor:
            tail.append("at the " + anchor)
        body = "the text %s" % quote_text_block(e.text_content or "")
        out.append(Clause("text", body + (", " + ", ".join(tail) if tail else ""),
                          max(0.95, e.importance), e.id))

    # 10. 观察散文（反推时 VLM 写入）
    # 放在最后追加，因为它要和**全部事实**比对去重。8B 模型写 notes 时几乎一定会
    # 复述已有字段（实测："Subject centered in frame" 复述了 centered 构图规则），
    # 逐句算重合度、只丢重复句，新增的句子（"eye movement is direct"）留下。
    fact_text = " ".join(c.text for c in out)
    for key in sorted(ir.notes or {}):        # 同上：字典顺序不是语义
        text = ir.notes[key]
        dim = _NOTE_DIM.get(key)
        if not dim or not str(text).strip():
            continue
        trimmed = _dedupe_note(str(text), fact_text)
        if trimmed:
            out.append(Clause(dim, trimmed, _NOTE_IMP.get(dim, 0.5), None, "note"))

    out.sort(key=lambda c: (-c.importance,
                            DIM_ORDER.index(c.dim) if c.dim in DIM_ORDER else 99,
                            0 if c.source == "fact" else 1))
    return out


def pack(clauses: list[Clause], limit_tokens: int | None, counter) -> tuple[list[Clause], list[Clause]]:
    """按重要度装箱。返回 (留下的, 被砍的)。

    不按位置截断：Krea 2 的 512 会砍掉句尾的媒介/光照/文字，那恰恰是决定画面质量的部分。
    """
    if not limit_tokens:
        return list(clauses), []
    kept, dropped, used = [], [], 0
    for c in clauses:
        cost = counter.count(c.text) + 2      # 分隔符开销
        if used + cost <= limit_tokens:
            kept.append(c)
            used += cost
        else:
            dropped.append(c)
    return kept, dropped


def capitalize_sentences(text: str) -> str:
    """每个句子首字母大写。caption 是散文，不是列表。"""
    if not text:
        return text
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(p[:1].upper() + p[1:] if p else p for p in parts)


def render_tags(clauses: list[Clause], dim_order: tuple[str, ...] = DIM_ORDER) -> str:
    """tag 方言（SDXL / Pony / Illustrious 这一类）：逗号分隔，不要句号。

    不发明权重。`(word:1.2)` 看起来专业，但权重该给谁、给多少，是模型相关的经验值，
    工具瞎填只会让用户以为调过参。真要权重就自己加，`_sanitize` 不会拦。
    """
    buckets: dict[str, list[Clause]] = {}
    for c in clauses:
        buckets.setdefault(c.dim, []).append(c)

    parts: list[str] = []
    seen = ""
    for dim in list(dim_order) + [d for d in buckets if d not in dim_order]:
        for c in buckets.get(dim, []):
            t = c.text.strip().rstrip(".").strip()
            if not t:
                continue
            # 同一句被拆成多条时只留一次（"a woman, a woman wearing..."）
            if t.lower() in seen:
                continue
            parts.append(t)
            seen += " " + t.lower()
    return ", ".join(parts)


def render_prose(clauses: list[Clause], dim_order: tuple[str, ...] = DIM_ORDER) -> str:
    """确定性散文渲染：同维度的**事实**用逗号连；**观察散文**自成一句。

    为什么 note 不参与逗号连接：note 是完整句子（"The subject is centered in the
    frame."），用 ", " 拼上去会得到 "Centered composition, The subject is..."——
    句中冒出大写字母，读起来像拼接故障。
    """
    buckets: dict[str, list[Clause]] = {}
    for c in clauses:
        buckets.setdefault(c.dim, []).append(c)

    sentences: list[str] = []
    order = list(dim_order) + [d for d in buckets if d not in dim_order]
    for dim in order:
        items = buckets.get(dim)
        if not items:
            continue
        buf: list[str] = []
        for c in items:
            if c.source == "note":
                if buf:
                    sentences.append(", ".join(buf))
                    buf = []
                sentences.append(c.text)
            else:
                buf.append(c.text)
        if buf:
            sentences.append(", ".join(buf))

    text = ". ".join(s.strip().rstrip(".") for s in sentences if s.strip())
    if not text:
        return ""
    return capitalize_sentences(text) + "."
