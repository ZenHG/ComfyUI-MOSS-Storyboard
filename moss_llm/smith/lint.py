# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""15 条静态检查。零 LLM，全部确定性，可单测。

定位：在把 prompt 交给图模型之前，拦下「不报错但出烂图」的低级错误。
这不是审美判断，是语法与能力边界判断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ir import IR

QUALITY_WORDS = re.compile(
    r"\b(masterpiece|best quality|high quality|ultra[-\s]?detailed|highly detailed|"
    r"award[-\s]?winning|8k|4k|16k|octane render|unreal engine|trending on artstation)\b",
    re.I)

WEIGHT_SYNTAX = re.compile(r"\([^()]{1,80}:\s*[\d.]+\)|\(\(|\)\)|\[[^\[\]]{1,80}\]")

SAMPLING_LEAK = re.compile(
    r"\b\d{1,4}\s*steps?\b|\bcfg\b|\btrue[_\s]?cfg\b|--ar\b|\bseed\b|\bguidance\b|"
    r"\b\d{3,4}\s*[x×]\s*\d{3,4}\b|\b\d+(\.\d+)?\s*mp\b|\bdenois(e|ing)\b", re.I)

VAGUE_ADVERBS = re.compile(r"\b(very|really|extremely|highly|incredibly|super|truly)\b", re.I)

SYNONYM_GROUPS = [
    {"beautiful", "gorgeous", "stunning", "exquisite", "breathtaking"},
    {"big", "large", "huge", "massive"},
    {"detailed", "intricate", "elaborate"},
]

CAMERA_MODELS = re.compile(
    r"\b(hasselblad|contax|leica|polaroid|kodak portra|canon eos|nikon z\d?|sony a7|"
    r"iphone \d+|fujifilm pro)\b", re.I)
CAMERA_SUPPORT = re.compile(
    r"\b(shot on|photographed with|taken with|captured on|using a|with a)\b", re.I)

CONTRADICTIONS = [
    # (组 A, 组 B, 作用域)
    # global = 整幅画面只能有一种（白天 vs 夜晚）
    # entity = 允许不同主体各持一种（一个人坐着、另一个人站着是常态，不是矛盾）
    (("daylight", "sunny", "midday"), ("night", "midnight", "moonlit"), "global"),
    (("indoor", "interior"), ("outdoor", "exterior"), "global"),
    (("black hair", "dark hair"), ("blonde", "blond hair"), "entity"),
    (("sitting", "seated"), ("standing", "upright"), "entity"),
    (("smiling", "grinning"), ("frowning", "scowling"), "entity"),
]

IP_WORDS = re.compile(
    r"\b(pokemon|nintendo|naruto|genshin impact|marvel|disney|mickey mouse|"
    r"star wars|harry potter)\b", re.I)

#: 引号里的短文字（中文 1-20 字，或全大写英文 2-20 字）
QUOTED_TEXT = re.compile(r"[\"“]([\u3400-\u9fff]{1,20}|[A-Z][A-Z0-9 ’']{1,20})[\"”]")
AGE_PATTERN = re.compile(r"(\d{1,3})[- ]year[- ]old", re.I)
NEGATION = re.compile(r"\b(no text|no visible|there is no|without any|not present|"
                      r"none of|no other)\b", re.I)


@dataclass
class Issue:
    level: str          # error | warn
    code: str
    message: str
    fix: str = ""

    def __str__(self) -> str:
        tag = "ERROR" if self.level == "error" else "WARN "
        return "[%s] %s %s%s" % (tag, self.code, self.message,
                                 ("  → " + self.fix) if self.fix else "")


def _has_any(text: str, words) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def _flat(text: str) -> str:
    """空白归一化。渲染器会把多行块压成单行，比对前两边都得压。"""
    return " ".join((text or "").split())


def check(ir: IR, target: str, caps: dict, realized: dict) -> list[Issue]:
    """realized: {positive, negative, layout, params, token_count}"""
    pos = realized.get("positive") or ""
    issues: list[Issue] = []
    # Layout JSON 是合法交付形态：里面的方括号/引号是结构，不是权重语法或口语。
    is_json = (pos.lstrip().startswith("{")
               or any(ln.lstrip().startswith("{") for ln in pos.splitlines()))
    prompt_caps = caps.get("prompt") or {}
    # 时间线体裁（H3）：`[Shot N]` / `<d>` / `At MM:SS.mmm` 是**规定记号**，不是语法错误；
    # 年龄闸与「多主体要 anchor」也是图像后端的口径——H3 的空间关系写在散文里，
    # 由关系图或参考图决定。这三条对视频后端是错的判据，不是「更严」。
    timeline_mode = str(prompt_caps.get("mode") or "") == "timeline"
    neg_caps = caps.get("negative") or {}
    layout_caps = caps.get("layout") or {}
    edit_caps = caps.get("edit") or {}
    limit = realized.get("token_limit")

    # L001 超预算
    # 分两档：真超了才是 error；只是贴着上限是 warn。
    # 以前 >95% 就报 error 并说「超出」，可 pack() 本来就是**填满**预算，
    # 于是每个预算紧的后端都会稳定误报一条 error（sdxl 74/77 就是）。
    n = realized.get("token_count") or 0
    if limit and n > limit:
        issues.append(Issue("error", "L001",
                            "token %d 超出目标预算 %d（%s）" % (n, limit, target),
                            "按 importance 裁剪子句，或换更高预算的目标"))
    elif limit and n > limit * 0.95:
        issues.append(Issue("warn", "L001",
                            "token %d 已贴到预算上限 %d（%s），下游可能截断"
                            % (n, limit, target),
                            "想留余量就把 importance 低的子句删掉"))

    # L002 质量词
    hits = sorted({m.group(0).lower() for m in QUALITY_WORDS.finditer(pos)})
    if hits:
        issues.append(Issue("error", "L002", "质量词堆砌：%s" % ", ".join(hits),
                            "删除；具体性才是质量信号"))

    # L003 权重语法错配
    if not is_json and (prompt_caps.get("weight_syntax") or "none") == "none":
        scan = pos
        if timeline_mode:
            scan = re.sub(r"\[Shot \d+\]", " ", scan)      # 规定记号
            scan = re.sub(r"<d>.*?</d>", " ", scan, flags=re.S)
            scan = re.sub(r"</?(?:scenetrans|cutoff)>", " ", scan)
            # 六段式的 summary 首行是**体裁记号**，不是权重语法：
            # `[video continuation]`（续接）/ `[reference generation]`（全参考生成）。
            # 不剥掉会被 `\[[^\[\]]{1,80}\]` 匹配，报一条永远修不好的 L003 假错。
            scan = re.sub(r"\[(?:video continuation|reference generation)\]",
                          " ", scan, flags=re.I)
        m = WEIGHT_SYNTAX.search(scan)
        if m:
            issues.append(Issue("error", "L003",
                                "%s 不支持权重语法，出现 %r" % (target, m.group(0)),
                                "剥离括号/权重，改写成自然语言强调"))

    # L004 负向写进正向
    if not neg_caps.get("supported"):
        leaked = [w for w in (ir.constraints.get("avoid") or []) if w and w.lower() in pos.lower()]
        if leaked:
            issues.append(Issue("warn", "L004",
                                "%s 无负向通道，正向里出现规避词：%s" % (target, ", ".join(leaked)),
                                "改写成肯定式描述，或删除"))

    # L005 采样参数泄漏
    # 先把引号里的内容挖空再扫：图内文字是**逐字转录**的，一张 ComfyUI 截图里
    # 本来就会写着 "步数 / cfg"，那是画面内容，不是采样参数泄漏（实测误报）。
    m = SAMPLING_LEAK.search(re.sub(r'"[^"]*"', '""', pos))
    if m:
        issues.append(Issue("error", "L005", "采样参数/分辨率泄漏进 prompt：%r" % m.group(0),
                            "移到 params，prompt 只写画面"))

    # L006 人体无年龄
    missing = [] if timeline_mode else [
        e.id for e in ir.entities if e.kind == "character" and e.age_years is None]
    if missing:
        issues.append(Issue("error", "L006", "角色缺显式年龄：%s" % ", ".join(missing),
                            "补 age_years；<18 强制 SFW"))

    # L007 多主体未绑定
    subjects = [] if timeline_mode else [
        e for e in ir.entities if e.kind in ("character", "object")]
    if len(subjects) > 1 and not ir.relations and not any(e.spatial.get("anchor") for e in subjects):
        issues.append(Issue("warn", "L007",
                            "%d 个主体既无 relations 也无 spatial.anchor，属性会串味" % len(subjects),
                            "加 relations 或给每个主体写 anchor"))

    # L008 属性矛盾
    # 分作用域判：全画面属性（白天/夜晚）全局扫；主体属性（坐/站、发色、表情）
    # 只在**同一个实体**的描述里判。实测四格漫画里「女坐男站」被误报成矛盾。
    from .clauses import describe_entity
    for group_a, group_b, scope in CONTRADICTIONS:
        if scope == "global":
            if _has_any(pos, group_a) and _has_any(pos, group_b):
                issues.append(Issue("error", "L008",
                                    "属性矛盾：%s 与 %s 同时出现" % (group_a[0], group_b[0]),
                                    "二选一"))
        else:
            for e in ir.entities:
                desc = describe_entity(e)
                if _has_any(desc, group_a) and _has_any(desc, group_b):
                    issues.append(Issue("error", "L008",
                                        "实体 %s 自身矛盾：%s 与 %s" % (e.id, group_a[0], group_b[0]),
                                        "同一主体二选一"))
                    break

    # L009 画师/IP 违规
    blocked = [r["name"] for r in (ir.style.get("artist_refs") or [])
               if r.get("policy") == "blocked"]
    present = [b for b in blocked if b.lower() in pos.lower()]
    if present:
        issues.append(Issue("error", "L009", "策略禁止的画师引用仍在 prompt：%s" % ", ".join(present),
                            "删除；改描述可见的画风特征"))
    if ir.constraints.get("ip_policy") == "strict":
        m = IP_WORDS.search(pos)
        if m:
            issues.append(Issue("error", "L009", "ip_policy=strict 下出现 IP 词：%r" % m.group(0),
                                "删除版权大词"))

    # L010 图内文字未加引号
    # 比对要按**空白归一化**后的文本做：渲染器会把多行块压成单行（Clause 里
    # " ".join(text.split())），拿带 \n 的原文去比永远不中（实测误报）。
    flat_pos = _flat(pos)
    for e in ir.text_entities:
        lines = [ln for ln in (e.text_content or "").split("\n") if ln.strip()]
        if not all(_flat(ln) in flat_pos for ln in lines):
            issues.append(Issue("error", "L010",
                                "图内文字未逐字出现：%s" % (e.text_content or "")[:24],
                                "用直角/弯引号包裹，保留原语种"))
            break

    # L011 坐标非法
    if realized.get("layout"):
        lay = realized["layout"]
        w, h = lay.get("_w"), lay.get("_h")
        if layout_caps.get("require_multiple_of_16") and w and h:
            if w % 16 or h % 16:
                issues.append(Issue("error", "L011", "画布 %dx%d 非 16 的倍数" % (w, h),
                                    "取整到 16 的倍数"))

    # L012 相机型号孤立
    m = CAMERA_MODELS.search(pos)
    if m and not CAMERA_SUPPORT.search(pos):
        issues.append(Issue("warn", "L012",
                            "相机型号 %r 单独出现，模型多半不响应" % m.group(0),
                            "改写成整句词组：shot on X with Y"))

    # L013 空泛副词过密
    words = len(pos.split()) or 1
    adv = len(VAGUE_ADVERBS.findall(pos))
    if not is_json and adv / words > 0.03:
        issues.append(Issue("warn", "L013", "空泛副词 %d 个 / %d 词，密度过高" % (adv, words),
                            "换成可观察的具体描述"))

    # L014 同义堆叠
    low = pos.lower()
    for group in SYNONYM_GROUPS:
        hit = [w for w in group if w in low]
        if len(hit) >= 3:
            issues.append(Issue("warn", "L014", "同义词堆叠：%s" % ", ".join(sorted(hit)),
                                "留一个"))

    # L015 能力越界
    if ir.task in ("edit", "inpaint", "outpaint") and not edit_caps.get("supported"):
        issues.append(Issue("error", "L015", "%s 不支持 %s" % (target, ir.task),
                            "换支持编辑的目标，或退回 t2i"))
    if ir.task == "t2i" and ir.needs_layout() and not layout_caps.get("supported"):
        issues.append(Issue("warn", "L015",
                            "%s 不支持 bbox 布局，坐标已降级为方位短语" % target,
                            "如需精确布局，换 MOSS / FLUX 3"))

    # L016 凭空生成图内文字（实测复现：IR 无文字，模型却写出 "茶屋"）
    if not ir.text_entities:
        m = QUOTED_TEXT.search(pos)
        if m:
            issues.append(Issue("error", "L016",
                                "IR 里没有文字，prompt 却出现引号文字 %r" % m.group(0),
                                "删掉；或把该文字写进 IR 的 text 实体"))

    # L017 年龄被改写
    ir_ages = {e.age_years for e in ir.entities if e.age_years}
    said = {int(a) for a in AGE_PATTERN.findall(pos)}
    if said and ir_ages and not said <= ir_ages:
        issues.append(Issue("error", "L017",
                            "prompt 里的年龄 %s 与 IR %s 不符" % (sorted(said), sorted(ir_ages)),
                            "年龄是硬闸字段，不得由模型改写"))

    # L018 否定式（散文方言不要写「没有/不要」）
    m = NEGATION.search(pos)
    if m and not is_json:
        issues.append(Issue("warn", "L018", "散文里出现否定式：%r" % m.group(0),
                            "改写成肯定式描述；负向词走 negative 通道"))

    issues.sort(key=lambda i: (i.level != "error", i.code))
    return issues
