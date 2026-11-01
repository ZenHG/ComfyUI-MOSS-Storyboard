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

import json
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
    # ── 拆步归因（steps.py 写入；不带这三个字段的旧报告照常工作） ──
    #: 问题来自哪一步：``completion`` / ``translation`` / ``rewrite``；
    #: ``single`` = 历史单次调用（无法归因）；``""`` = 未知（直接调 verify()）。
    stage: str = ""
    #: ``{步名: [问题]}``，只列有问题的步。未拆步为空。
    stage_issues: dict = field(default_factory=dict)
    #: 每步的执行摘要（``steps.StepResult.to_dict()``），含「关掉的步」。
    steps: list = field(default_factory=list)
    #: 为什么这样归因 / 为什么无法归因。人话，给报告读。
    stage_note: str = ""

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

    def step_error_count(self) -> int:
        """步级判据判死的错误条数（拆步模式）。未拆步恒为 0，排序行为不变。"""
        return sum(len(s.get("errors") or []) for s in (self.steps or []))

    def failing_stages(self) -> list:
        """有问题的步，按执行顺序（``stage_issues`` 的插入顺序就是步顺序）。"""
        return list(self.stage_issues or {})

    def score(self) -> tuple:
        """排序键，越小越好。**不是总分**，是分层比较。

        顺序即优先级，每一层都是「这一层不同就没必要比下一层」：
        ① 硬错（改固定项 / 造数字 / 造文字）—— 错一次就废，量纲相同可以直接相加；
        ② 离长度带的距离 —— 欠约束与过约束都不好，谁离得近谁好；
        ③ 其余警告条数 —— 质量词、否定式、比例入文。

        故意**不加权求和**：这三层代价不同，加成一个数会让「3 条无关警告」
        看起来比「1 处造字」更糟。分层比较不做这种交换。

        第 ① 层额外计入**步级判据的 error**（拆步模式才有；未拆步为 0）：
        「术语没翻译」不会让固定项命中变差，但它确实是一处硬失败，不能被长度带
        和软警告淹没。
        """
        hard = (len(self.hard_misses) + len(self.extra_numbers) + len(self.extra_quotes)
                + self.step_error_count())
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
            # 下面四个是新增键，旧键一个没动（向后兼容）。
            "stage": self.stage, "stage_issues": dict(self.stage_issues or {}),
            "steps": self.steps, "stage_note": self.stage_note,
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

def _split_structured(text: str) -> tuple:
    """把「散文 + JSON」拆成 (散文, [解析出来的对象])。

    两种形态都认：整段就是一个 JSON（Layout 方言），或一行提示 + 每行一个 JSON（bbox 方言）。
    解析不了的片段**原样留在散文里** —— 宁可多扫，不可静默跳过。
    """
    s = (text or "").strip()
    if s.startswith(("{", "[")):
        try:
            return "", [json.loads(s)]
        except Exception:
            pass
    prose, blocks = [], []
    for ln in (text or "").splitlines():
        st = ln.strip()
        if st.startswith(("{", "[")):
            try:
                blocks.append(json.loads(st))
                continue
            except Exception:
                pass
        prose.append(ln)
    return "\n".join(prose), blocks


def _string_values(blocks) -> list:
    """结构化块里的字符串值，**跳过结构字段**（键名、`id`、`text`、数值字段）。

    `id` 是把手（`s1`/`s3`），`text` 是图内文字（走逐字校验），两者都不参与数字扫描：
    把它们当「凭空造数」是判据拿错对象——实测 2 份夹具的 flux3 因此各报 3–4 条假错。
    """
    out = []
    skip = {"id", "text"}

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key not in skip:
            out.append(node)

    for b in blocks:
        walk(b)
    return out


def _text_values(blocks) -> list:
    """结构化块里 `text` 字段的值：那就是图内文字，必须逐字对得上 IR。"""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "text" and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for b in blocks:
        walk(b)
    return out


#: 六个欠定槽位：(名字, 判定, 可抄的例子)。
#:
#: 判定只看**渲染器真正读的字段**，不看"写了多少字"——IR 里塞了 atmosphere 却全在
#: 同一个意思上堆词，词数够了但槽位还是空的。这正是"写长"和"降低熵"的分界。
#: 文本维（text）不在这里：画面文字不是"没说的"，是必须逐字的。
SLOT_DIMS = ("subject", "setting", "camera", "lighting", "material", "medium")


def open_slot_dims(clauses) -> list:
    """clauses 里**没有**的维度：这些才是补全该填的 OPEN 槽位（按 SLOT_DIMS 顺序）。

    抽成公共函数是因为有两处要问同一个问题：``ledger`` 把 OPEN 写给模型，
    ``steps.check_completion`` 判「模型填的是不是 OPEN 槽位」。两处若各写一份口径，
    迟早一个认 camera 一个不认，于是账本说没空位、判据说填了不该填的。
    """
    have = {c.dim for c in clauses}
    return [d for d in SLOT_DIMS if d not in have]


_SLOTS = (
    ("主体", lambda ir: any(e.kind in ("character", "object") and e.label
                        for e in ir.entities),
     "a 28-year-old woman in a yellow raincoat"),
    ("动作", lambda ir: bool(ir.relations) or any(
        set(e.descriptors) & {"pose", "expression", "action", "gesture"} for e in ir.entities),
     "standing with her weight on one leg, looking up at the sky"),
    ("场景", lambda ir: bool((ir.canvas or {}).get("background")) or any(
        e.kind == "background" for e in ir.entities),
     "a narrow Kyoto backstreet after rain"),
    ("镜头", lambda ir: bool(ir.camera),
     "medium shot, low angle, 35mm lens, shallow depth of field"),
    ("光", lambda ir: bool(ir.lighting),
     "soft overcast daylight from the left, blue hour"),
    ("材质", lambda ir: bool(ir.atmosphere) or any(
        set(e.descriptors) & {"material", "surface", "texture"} for e in ir.entities),
     "wet asphalt mirroring the shop signs"),
)


def slot_gaps(ir: IR) -> list:
    """还空着的槽位：[(名字, 例子)]，按七维顺序。"""
    return [(name, example) for name, ok, example in _SLOTS if not ok(ir)]


def slot_advice(ir: IR, words: int = 0) -> str:
    """短了该补什么——具体到槽位，不是「再加点形容词」。

    只报数（`只有 93 词`）会把人推向堆词，而堆词不缩小出图分布。这里给的是
    「哪一维还空着 + 一句可以照抄的写法」，补一句就够，不必写满。
    """
    gaps = slot_gaps(ir)
    if not gaps:
        return ("槽位齐全（%d 词）：缺的不是类目，是每个槽位的**排他细节**——"
                "加形容词不缩小分布（见 docs/24 扩写判据）" % words)
    head = "缺槽位 %s" % " / ".join(n for n, _ in gaps[:4])
    sample = "；".join("%s→「%s」" % (n, ex) for n, ex in gaps[:2])
    return "%s。补一句就够：%s" % (head, sample)


def verify(ir: IR, text: str, target: str = "", registry=None,
           counter=None, band: tuple | None = None,
           length: str = "standard", stage: str = "") -> Report:
    """对一段扩写结果做确定性体检。不需要模型，也不需要生成器。

    ``stage`` 只是报告标签（由拆步管线写入「问题来自哪一步」），不参与判死；
    不传时报告里 stage 为空，含义是「来源未知」。
    """
    rep = Report(target=target, words=words(text), stage=stage)
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
    # Layout JSON / bbox 行都是合法交付形态：里面的数字是坐标、引号是键名，
    # 不是「画面里编了细节」。但**不能整段跳过**——flux3 的输出是「一行提示 + 每行
    # 一个 JSON」，原来只认「整段以 { 开头」，于是 4 份夹具 × 5 后端报出 78 条假错
    # （bbox 数字当凭空造数、键名当凭空造字）。判据拿错对象时，红不是结论。
    prose, blocks = _split_structured(text)
    structured = bool(blocks)
    # **先把换行压平再配对引号**：H3 把图内文字原样嵌进引号，字里带 `\n`
    # （`"0.20\n0.15\n0.95"is visible…`）。`_QUOTE` 的字符类排除了换行，于是配不上对，
    # 会把上一段的收尾引号和下一段的开头引号配成一条「凭空多出来的文字」——
    # 实测 ui-smoke 上就是这么产生 2 条假错的。压平后按空白无关比较，与 `_norm` 一致。
    flat = re.sub(r"\s+", " ", prose if structured else text)
    body = flat
    scan = flat
    if structured:
        # 结构化块里只把**字符串值**当正文再扫一遍数字（`"3 panels"` 里的 3 是真内容）；
        # 数值字段（bbox 坐标）与 `text` 字段一律不判：前者是几何，后者走逐字校验。
        scan = (scan + " " + " ".join(_string_values(blocks))).strip()
    else:
        # 引号里的字是**图内文字**，它的数字不是描写（`"2x2 · 4 panels"`）。
        # 它已由 ③ 逐字校验，这里从数字扫描里拿掉，否则会报「凭空出现 2」。
        scan = _QUOTE.sub(" ", scan)
    # 结构记号（配合方言的把手）不是画面内容：`<silhouette_3>` / `<Picture 1>` / `[Shot 2]`。
    scan = re.sub(r"<[^<>]{0,24}?\d+[^<>]{0,8}?>", " ", scan)
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
    if structured:
        # JSON 里的 `text` 字段就是图内文字，必须与 IR **逐字一致**（不是「像」就行）。
        # 结构化方言不能靠引号扫：那些字在值里，没有引号包着。
        for v in _text_values(blocks):
            q = _norm(v)
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

    missing = open_slot_dims(clauses)
    lines.append("\nOPEN SLOTS (nothing stated — you decide, and commit once):")
    lines += ["- %s（%s）" % (d, _DIM_ZH.get(d, d)) for d in missing] or ["- (none)"]

    lines.append("\nFACTS (every sentence must trace back to one of these):")
    lines += ["- [%s] %s" % (c.dim, c.text) for c in clauses] or ["- (none)"]

    if band:
        lines.append("\nLENGTH: %d–%d words." % band)
    return "\n".join(lines)


def ledger_user(ir: IR, clauses, band: tuple | None = None) -> str:
    return ledger(ir, clauses, band) + "\n\nWrite the description now."
