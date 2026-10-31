# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""扩写：把「事实」变成「一幅画的描述」，并且**能被验证**。

三条与「写得更长」无关的判断：

1. **扩写不是加词，是降低欠定槽位的熵。** 只补缺的（主体 / 动作 / 空间 / 镜头 / 光），
   不堆形容词。堆词只是把随机性从一个地方挪到另一个地方——「masterpiece, 8k」对
   输出分布的约束接近零，而「雨后的湿沥青把招牌倒映成竖条」约束很强。
2. **扩写不得改动用户固定的东西。** 数量、引号里的字、说过的颜色、说过的方位。
   这是唯一一条「改了就错」的硬约束，所以判 error，不是 warn。
3. **长度是方言参数，不是全局真理。** FLUX 3 要短（自带 upsampler，写长=过度指定），
   Krea 2 要长但受 512 token 硬截断，MOSS 要 150–300 词。同一个 IR 扩到不同目标，
   长度目标不同——写在 registry 里，不写在代码里。

两种失败模式，方向相反，必须分开判：
- **欠约束**：太短太泛，生成方差大，10 张图 10 个样子 → 长度带下界 + 信息密度。
- **过约束**：把用户没说的也写死了，模型没有自由度 → 只在 FIXED 之外乱加数/加引号文本。

本模块零模型、零网络，纯确定性，可单测。


## 与节点的 expand 通道不是同一件事（合流时确认过）

节点那条是「一句话想法 → 一整段描述」，模型在环，输出不可复现也不该可复现；
本模块是「已经有事实的 IR → 补齐欠定槽位」，零模型、可验证。
两者的判据也不同：节点只查 must_keep 逐字与段数，本层查长度带、固定项、凭空造的
数字与引号文本。**保留两份是有意的**，不是没收敛——删掉任何一个都是能力损失。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ir import IR

#: 引号里的内容：中英文引号都认。图内文字是扩写里唯一必须逐字的东西。
_QUOTE = re.compile(r'"([^"\n]{1,120})"|“([^”\n]{1,120})”|「([^」\n]{1,120})」')

#: 画面内容数字。带一个可选单位后缀，好在下一步区分「35mm 镜头」和「35 岁」。
_NUM = re.compile(r"(?<![\d.])(\d{1,4})(?![\d.])\s*([a-zA-Z°/]{0,3})")

#: 技术单位：出现在数字后面就是参数，不是画面内容。
_TECH_UNITS = {"mm", "cm", "px", "dpi", "fps", "kb", "mb", "k", "°", "f"}

#: 颜色词表。只收「说了就不能改、也不能换成别的」的高频词。
COLORS = {
    "red", "orange", "yellow", "green", "cyan", "blue", "purple", "violet",
    "magenta", "pink", "brown", "black", "white", "gray", "grey", "beige",
    "cream", "ivory", "gold", "golden", "silver", "bronze", "copper", "navy",
    "teal", "olive", "maroon", "turquoise", "amber", "crimson", "scarlet",
    "indigo", "lavender", "emerald", "ruby", "sapphire",
}

QUALITY_WORDS = re.compile(
    r"\b(masterpiece|best quality|high quality|ultra[-\s]?detailed|highly detailed|"
    r"award[-\s]?winning|8k|4k|16k|octane render|unreal engine|trending on artstation)\b",
    re.I)

NEGATION = re.compile(r"\b(no|not|without|never|none|nothing)\b", re.I)

#: 比例/分辨率不该进散文——它属于 wh_ratio 字段，写进句子等于污染语义。
RATIO_IN_PROSE = re.compile(r"\b\d{1,2}\s*:\s*\d{1,2}\b")

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*|[\u4e00-\u9fff]")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def words(text: str) -> int:
    """中英混排的词数：英文按词、中文按字。与 token 计数不是一回事，别混用。"""
    return len(_WORD.findall(text or ""))


@dataclass
class Fixed:
    """一条「说了就不能改」的事实。

    hard=True 的项改了就**错**（图里的字、年龄、用户明说的 must_keep）；
    hard=False 的项改得含糊只是**差**（方位、颜色——表达方式本来就自由）。
    把这两类混成一个「固定项命中率」会让报告失去判断力。
    """

    kind: str                 # text | age | color | anchor | keep | user
    value: str
    origin: str = ""          # 实体 id，便于回指
    hard: bool = True

    def label(self) -> str:
        return "[%s] %s" % (self.kind, self.value)


#: 只有这些种类是「改了就错」。
HARD_KINDS = ("text", "age", "keep", "user", "line", "beat")

#: 方位词。anchor 不要求整串命中——「upper right」写成「in the top-right corner」是对的。
DIRECTIONS = {"left", "right", "upper", "lower", "top", "bottom", "centre", "center",
              "middle", "foreground", "background", "third", "half", "corner", "edge"}


def hit(f: Fixed, flat: str) -> bool:
    """一条固定项在被小写化、空白折叠过的文本里是否命中。

    抽出来是因为有两处要问同一个问题：`verify()` 问「这段 prompt 有没有丢」，
    `decompose.unrendered()` 问「这份 IR 能不能编译出来」。两处若各写一份，
    迟早一个认「top-right」另一个不认，于是自检绿、体检红。

    两处都不要求整块文字一次命中：多行文字块按行核。
    「一杯温暖\\n一刻从容」被拆成两句、或被 JSON 转义成 `\\n` 两个字符，
    内容其实都在——按整块比会把这种情况报成「丢了」，那是最没用的那种红。
    """
    if f.kind == "anchor":
        need = [w for w in re.findall(r"[a-z]+", f.value.lower()) if w in DIRECTIONS]
        return all(w in flat for w in need) if need else True
    if f.kind == "text":
        lines = [ln for ln in f.value.splitlines() if ln.strip()]
        return all(_norm(ln) in flat for ln in lines) if lines else True
    return _norm(f.value) in flat


@dataclass
class Report:
    target: str = ""
    words: int = 0
    tokens: int = 0
    band: tuple | None = None
    fixed: list = field(default_factory=list)
    hits: list = field(default_factory=list)
    misses: list = field(default_factory=list)
    hard_misses: list = field(default_factory=list)
    extra_numbers: list = field(default_factory=list)
    extra_quotes: list = field(default_factory=list)
    quality: list = field(default_factory=list)
    negations: list = field(default_factory=list)
    ratio_in_prose: list = field(default_factory=list)
    density: float = 0.0
    verdict: str = "pass"
    errors: list = field(default_factory=list)
    warns: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    #: 这份报告是从几个**不同**的候选里选出来的（1 = 没做搜索）。
    candidates: int = 1

    @property
    def fixed_recall(self) -> float:
        if not self.fixed:
            return 1.0
        return len(self.hits) / float(len(self.fixed))

    def band_distance(self) -> int:
        """离长度带多远。带内为 0。没有带子（registry 没写）也按 0 算，不编。"""
        if not self.band:
            return 0
        lo, hi = self.band
        if self.words < lo:
            return lo - self.words
        if self.words > hi:
            return self.words - hi
        return 0

    def score(self) -> tuple:
        """排序键，越小越好。**不是总分**，是分层比较。

        顺序即优先级，每一层都是「这一层不同就没必要比下一层」：
        ① 硬错（改固定项 / 造数字 / 造文字）—— 错一次就废，量纲相同可以直接相加；
        ② 离长度带的距离 —— 欠约束与过约束都不好，谁离得近谁好；
        ③ 其余警告条数 —— 质量词、否定式、比例入文。

        故意**不加权求和**：这三层代价不同，加成一个数会让「3 条无关警告」
        看起来比「1 处造字」更糟。分层比较不做这种交换。
        """
        hard = len(self.hard_misses) + len(self.extra_numbers) + len(self.extra_quotes)
        return (hard, self.band_distance(), len(self.warns))

    def to_dict(self) -> dict:
        return {
            "target": self.target, "words": self.words, "tokens": self.tokens,
            "band": list(self.band) if self.band else None,
            "fixed_total": len(self.fixed), "fixed_hits": len(self.hits),
            "fixed_recall": round(self.fixed_recall, 3),
            "misses": self.misses, "hard_misses": self.hard_misses, "extra_numbers": self.extra_numbers,
            "extra_quotes": self.extra_quotes, "quality": self.quality,
            "negations": self.negations, "ratio_in_prose": self.ratio_in_prose,
            "density": round(self.density, 2), "verdict": self.verdict,
            "candidates": self.candidates, "band_distance": self.band_distance(),
            "issues": self.issues,
        }

    def feedback(self) -> str:
        """给模型的回炉指令：只说做错了什么，不重写整段。"""
        if not self.issues:
            return ""
        return ("YOUR LAST ANSWER FAILED THESE CHECKS. Fix only these, keep the rest:\n"
                + "\n".join("- " + i for i in self.issues))


# ── 固定项抽取 ────────────────────────────────────────────────

def fixed_items(ir: IR) -> list:
    """从 IR 里抽出「说了就不能改」的事实。

    不要求用户标注：这些项本来就在 IR 里，只是以前没人拿它们去校验。
    """
    out: list = []
    seen: set = set()

    def add(kind: str, value: str, origin: str) -> None:
        v = (value or "").strip()
        if not v:
            return
        key = (kind, _norm(v))
        if key in seen:
            return
        seen.add(key)
        out.append(Fixed(kind, v, origin, hard=kind in HARD_KINDS))

    for e in ir.entities:
        if e.kind == "text" and e.text_content:
            add("text", e.text_content, e.id)
        if e.age_years:
            add("age", str(e.age_years), e.id)
        anchor = (e.spatial or {}).get("anchor")
        if isinstance(anchor, str) and anchor.strip():
            add("anchor", anchor, e.id)
        for k in e.must_keep or []:
            add("keep", str(k), e.id)
        # 颜色藏在 label / descriptors 的任意文本里，扫出来即可
        blob = " ".join([e.label] + _flatten_text(e.descriptors))
        for w in re.findall(r"[A-Za-z]+", blob):
            if w.lower() in COLORS:
                add("color", w.lower(), e.id)

    # 时间线上的事实。图像后端没有这一维，循环自然空转，一行不用改。
    # 台词是**逐字**的：改一个字就是换了一句要念的话，比改年龄更严重。
    for shot in (ir.timeline or {}).get("shots") or []:
        idx = shot.get("index")
        for d in shot.get("dialogue") or []:
            add("line", str(d.get("text") or ""), "shot%s" % idx)
        for t in shot.get("onscreen_text") or []:
            add("text", str(t.get("text") or ""), "shot%s" % idx)
        if shot.get("t_start") is not None:
            add("beat", "At %s" % _ts(shot["t_start"]), "shot%s" % idx)

    for c in (ir.constraints or {}).get("fixed") or []:
        add("user", str(c), "constraints")
    return out


def _ts(seconds) -> str:
    """`MM:SS.mmm`。与 h3.format_ts 同一写法——两处各写一份迟早漂移。"""
    from .h3 import format_ts

    return format_ts(float(seconds))


def _flatten_text(obj, depth: int = 0) -> list:
    if depth > 4:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out += _flatten_text(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for v in obj:
            out += _flatten_text(v, depth + 1)
        return out
    return []


def _numbers_in(text: str) -> set:
    """一段文本里的「画面内容数字」。

    排除两类：比例/尺寸（3:2、1024x1024）与带技术单位的（35mm、24fps）。
    不排除的话，IR 里一个 `3:2` 会让「3 个杯子」这种编造永远抓不到。
    """
    clean = re.sub(r"\d+\s*[:x×]\s*\d+", " ", text or "")
    out = set()
    for m in _NUM.finditer(clean):
        if (m.group(2) or "").lower() in _TECH_UNITS:
            continue
        out.add(m.group(1))
    return out


#: 这些键里的数字是技术参数，不是画面内容（`lens_mm: 35` 不能用来洗白「35 岁」）。
_TECH_KEYS = re.compile(r"(mm|px|fps|dpi|kb|mb|_k|version|hash|seed|steps|cfg)$", re.I)


def _ir_numbers(ir: IR) -> set:
    """IR 里出现过的「画面内容数字」。

    只扫**值**，跳过 id 与版本号：实体 id 叫 `e1/e2/e3` 是索引，不是「图里有三个东西」。
    这条边界很重要——否则「3 个杯子」永远抓不到，因为 IR 里恰好有个 e3。
    """
    nums: set = set()

    def walk(obj, key: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("id", "ir_version") or _TECH_KEYS.search(k):
                    continue
                walk(v, k)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v, key)
        elif isinstance(obj, bool) or obj is None:
            return
        elif isinstance(obj, (int, float)):
            nums.update(_numbers_in(str(obj)))
        elif isinstance(obj, str):
            nums.update(_numbers_in(obj))

    walk(ir.to_dict())
    return nums


def _ir_quotes(ir: IR) -> set:
    return {_norm(e.text_content) for e in ir.entities
            if e.kind == "text" and e.text_content}


# ── 长度带 ────────────────────────────────────────────────────

def length_band(registry, target: str, length: str = "standard") -> tuple | None:
    """从 registry 读该目标的词数带。没写就返回 None（不判，不编）。"""
    if registry is None:
        return None
    try:
        policy = registry.prompt_policy(target) or {}
    except KeyError:
        return None
    table = policy.get("length") or {}
    band = table.get(length) or table.get("standard")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        try:
            return (int(band[0]), int(band[1]))
        except (TypeError, ValueError):
            return None
    return None


# ── 体检 ──────────────────────────────────────────────────────

def verify(ir: IR, text: str, target: str = "", registry=None,
           counter=None, band: tuple | None = None,
           length: str = "standard") -> Report:
    """对一段扩写结果做确定性体检。不需要模型，也不需要生成器。"""
    rep = Report(target=target, words=words(text))
    rep.band = band if band is not None else length_band(registry, target, length)

    if counter is None:
        from .tokens import HeuristicCounter
        counter = HeuristicCounter()
    rep.tokens = counter.count(text)

    low = _norm(text)
    flat = re.sub(r"\s+", " ", low)

    # ① 固定项：改了就错（hard），改得含糊只是差（soft）
    rep.fixed = fixed_items(ir)
    for f in rep.fixed:
        if hit(f, flat):
            rep.hits.append(f.label())
        else:
            rep.misses.append(f.label())
            if f.hard:
                rep.hard_misses.append(f.label())

    # ② 凭空造数：prompt 里的数字必须都能在 IR 里找到
    #
    # 时间线方言（H3）里 `At 00:03.500` 是**结构记号**，不是画面内容的数字。
    # 不挖掉会得到两条假错：「凭空出现数字 00」和「比例不该写进散文 00:03」——
    # 两条都是判据拿错了对象，不是 prompt 有问题。
    scan = text
    # Layout JSON 是合法交付形态：里面的数字是 bbox、引号是键名，
    # 不是「画面里编了细节」。拿散文的尺量 JSON 只会得到一屏假错，
    # 所以数字/引号/比例这三项对它一律不判——只保留固定项与长度带。
    is_json = text.lstrip().startswith("{")
    body = "" if is_json else text
    if is_json:
        scan = ""
    if registry is not None and target:
        caps = (registry.get(target) or {}).get("prompt") or {}
        if str(caps.get("mode") or "") == "timeline":
            scan = re.sub(r"At\s+\d{2}:\d{2}\.\d{3}", " At<ts> ", scan)
            scan = re.sub(r"\[Shot \d+\]", " ", scan)
    ir_nums = _ir_numbers(ir)
    for m in _NUM.finditer(scan):
        n, unit = m.group(1), (m.group(2) or "").lower()
        if unit in _TECH_UNITS:          # 35mm / 1024px / 24fps 是参数，不是画面内容
            continue
        if n not in ir_nums:
            rep.extra_numbers.append(n)
    rep.extra_numbers = sorted(set(rep.extra_numbers))

    # ③ 凭空造字：引号里的内容必须都是图里真有的
    ir_q = _ir_quotes(ir)
    for m in _QUOTE.finditer(body):
        q = _norm(next(g for g in m.groups() if g))
        if q and q not in ir_q:
            rep.extra_quotes.append(q)
    rep.extra_quotes = sorted(set(rep.extra_quotes))

    # ④ 风格污染
    rep.quality = sorted({m.group(0).lower() for m in QUALITY_WORDS.finditer(text)})
    rep.negations = sorted({m.group(0).lower() for m in NEGATION.finditer(text)})
    rep.ratio_in_prose = sorted({m.group(0) for m in RATIO_IN_PROSE.finditer(scan)})

    # ⑤ 信息密度：每条事实摊到多少 token。低密度=堆词。
    facts = len(ir.entities) + len(ir.relations) + len(ir.notes)
    rep.density = facts / (rep.tokens / 100.0) if rep.tokens else 0.0

    _judge(rep, ir)
    return rep


def _judge(rep: Report, ir: IR) -> None:
    """判死。error 只有三类：改固定项、造数字、造文字。"""
    errors, warns = [], []

    if rep.hard_misses:
        errors.append("丢失固定项 %d/%d：%s（图里的字/年龄/用户指定项，改了就是另一张图）"
                      % (len(rep.hard_misses), len(rep.fixed),
                         "；".join(rep.hard_misses[:4])))
    soft = [m for m in rep.misses if m not in rep.hard_misses]
    if soft:
        warns.append("方位/颜色没对上：%s（表达可以自由，但方向别丢）" % "；".join(soft[:4]))
    if rep.extra_numbers:
        errors.append("凭空出现数字：%s（IR 里没有，等于编了细节）"
                      % ", ".join(rep.extra_numbers))
    if rep.extra_quotes:
        errors.append("凭空出现引号文本：%s（图里没有这段字）"
                      % "；".join(rep.extra_quotes[:3]))

    if rep.band:
        lo, hi = rep.band
        if rep.words < lo:
            warns.append("只有 %d 词，低于该目标 %d–%d 的下界：欠约束，生成方差会大"
                         % (rep.words, lo, hi))
        elif rep.words > hi:
            warns.append("%d 词，超过该目标 %d–%d 的上界：过约束，模型自由度被压死"
                         % (rep.words, lo, hi))
    if rep.quality:
        warns.append("质量词对分布几乎无约束：%s" % ", ".join(rep.quality))
    if rep.negations:
        warns.append("否定式会反向激活：%s" % ", ".join(rep.negations))
    if rep.ratio_in_prose:
        warns.append("比例/分辨率不该写进散文：%s" % ", ".join(rep.ratio_in_prose))
    # 密度只报不判：多少算「低」没有标定过，判它等于编一个阈值。

    rep.errors = errors
    rep.warns = warns
    rep.issues = errors + warns
    rep.verdict = "fail" if errors else ("warn" if warns else "pass")


def rank(reports: list) -> int:
    """从 N 个候选里挑最好的那个，返回下标。并列时取**先采到的**（下标最小）。

    为什么搜索而不是重试：扩写的主要缺陷是**方差**——同一份 IR 跑十次，
    有时 100 分有时改掉了年龄。重试是在「已经错的那一份」上修，
    搜索是「在没出错的那些里挑」。前者要求模型自我纠错，后者只要求它偶尔写对。
    只要求「偶尔写对」是低得多的要求，也是唯一在 8B 本地模型上能兑现的要求。
    """
    if not reports:
        raise ValueError("rank() 需要至少一个候选")
    return min(range(len(reports)), key=lambda i: reports[i].score())


# ── 给模型的账本 ──────────────────────────────────────────────

EXPAND_SYSTEM = (
    "You write the description of a finished frame, as an observer looking at it.\n"
    "You never address the user, the renderer, or these rules.\n\n"
    "HARD RULES (any violation makes the output unusable):\n"
    "1. FIXED items are untouchable: copy quoted strings character for character and keep\n"
    "   their original script; keep exact counts; keep stated colors and left/right positions.\n"
    "2. Add no new number, no new quoted string, no new brand, no new named place or person.\n"
    "3. Never write what is absent ('no text', 'without', 'empty'). Never write quality words\n"
    "   (masterpiece, 8k, ultra detailed), weights, or sampling parameters.\n"
    "4. Do not write the aspect ratio or resolution into the prose.\n\n"
    "HOW TO EXPAND — this is the whole job:\n"
    "- Expanding means resolving underdetermined slots, not adding adjectives. Fill subject\n"
    "  and action, space and composition, one motivated key light, color hierarchy, material\n"
    "  behaviour, medium. If a slot is already stated, leave it alone.\n"
    "- Commit once per frame: one composition strategy, one key light direction, one medium,\n"
    "  one palette hierarchy. Never stack alternatives or contradictions.\n"
    "- Name the thing, its material, and how light behaves on it. Specificity over decoration.\n"
    "- Prefer observable surface: pose, gaze, hands, contact points, wardrobe layers.\n"
    "- Hedge real ambiguity ('appears to be'). Never invent identity, backstory, or off-frame\n"
    "  content.\n"
    "- If you run out of facts, stop. Do not pad to reach a word count.\n"
)

_DIM_ZH = {"subject": "主体与动作", "setting": "环境与空间", "camera": "镜头与构图",
           "lighting": "光", "material": "材质与色彩", "medium": "媒介与风格",
           "text": "画面文字", "atmosphere": "氛围"}


def ledger(ir: IR, clauses, band: tuple | None = None) -> str:
    """给模型的事实账本：FIXED 不动 / OPEN 自定 / FACTS 逐条 / LENGTH 目标。"""
    fixed = fixed_items(ir)
    lines = ["FIXED (do not change, do not paraphrase):"]
    lines += ["- " + f.label() for f in fixed] or ["- (none)"]

    have = {c.dim for c in clauses}
    missing = [d for d in ("subject", "setting", "camera", "lighting", "material", "medium")
               if d not in have]
    lines.append("\nOPEN SLOTS (nothing stated — you decide, and commit once):")
    lines += ["- %s（%s）" % (d, _DIM_ZH.get(d, d)) for d in missing] or ["- (none)"]

    lines.append("\nFACTS (every sentence must trace back to one of these):")
    lines += ["- [%s] %s" % (c.dim, c.text) for c in clauses] or ["- (none)"]

    if band:
        lines.append("\nLENGTH: %d–%d words." % band)
    return "\n".join(lines)


def ledger_user(ir: IR, clauses, band: tuple | None = None) -> str:
    return ledger(ir, clauses, band) + "\n\nWrite the description now."
