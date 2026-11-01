# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""H3 官方格式：IR 的第三种投影（时间线），不是「第五个 adapter」。

## 为什么单独一个模块

四个图像后端共享同一条流水线：把 IR 的七个维度摊成子句、按重要度装箱、渲染成
散文或 tag。它们之间只是方言差异，所以 `_render` 一行就能换掉。

H3 不一样。它的提示词**不是一张画面的属性表，而是一条时间线**：

    [Shot 1] ... [Shot 2] At 00:03.500, the camera cuts to ...

一旦有了 `[Shot N]` 和 `At MM:SS.mmm`，维度顺序、token 装箱、avoid→negative
这些概念全部失效——H3 没有负向通道，也不按维度读。硬套子句流水线只会产出
「一段很长的画面描述」，而 H3 要的是「哪一秒发生什么」。

所以这个模块自己负责：时间线规范化、渲染、反向解析、以及 28 条铁律里
**可机检的那一部分**。适配器（adapters/h3.py）只剩一层薄壳。

## 三个方向的接口

| 方向 | 函数 | 用途 |
|---|---|---|
| IR → prompt | `render(ir)` | 编译；`--target minimax-h3` |
| prompt → IR | `parse(text)` | 读别人的词 / 改词前先结构化成事实 |
| prompt → 判定 | `check(text)` | 提交前的机检闸（不需要 IR） |

## 判据的出处

格式真相源是 MiniMax-H3 官方提示词格式规范（基础模式：T2VA / I2VA / FL2VA /
L2VA），判定真相源是社区整理的「出词铁律」28 条。这个模块**只实现可机检的
子集**，每个 check 码都注明它守的是哪一条。凡是「要人看」的（表演、情绪、
构图好坏）这里一条都不做——那不是判据能管的事。

`check(music_policy=)` 把**内容策略**与**格式合法性**分开：默认 `any`（`N/A`
或配乐描述都合法，这是官方取值面），`na` 才是「产出端不配乐、后期铺 BGM」
那条产线的严格要求。默认值不该替使用者选产线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ir import IR
from .lint import Issue

# ─────────────────────────────────────────────────────────────
# 常量：全部来自官方格式规范，改这里等于改方言
# ─────────────────────────────────────────────────────────────

#: 三个字段，顺序固定。官方要求「Return only these three fields, in this exact order」。
FIELDS = ("integrated_multimodal_description", "overall_soundscape",
          "non_diegetic_music")

#: 四种任务模式。指令行是**终稿第一行**，后空一行再写三字段。
MODES = ("t2va", "i2va", "fl2va", "l2va")

#: 官方运镜词表 → 英文散文动词。渲染用它，解析也用它（同一份，不许分叉）。
CAMERA_VERBS = {
    "zoom in": "zooms in", "zoom out": "zooms out",
    "push in": "pushes in", "push_in": "pushes in", "dolly in": "pushes in",
    "pull out": "pulls out", "dolly out": "pulls out",
    "pan left": "pans left", "pan right": "pans right",
    "truck left": "trucks left", "truck right": "trucks right",
    "tilt up": "tilts up", "tilt down": "tilts down",
    "pedestal up": "pedestals up", "pedestal down": "pedestals down",
    "arc": "arcs around the subject", "arc shot": "arcs around the subject",
    "tracking": "tracks the moving subject", "tracking shot": "tracks the moving subject",
    "static": "holds a static shot", "static shot": "holds a static shot",
    "shake slightly": "shakes slightly", "shake strongly": "shakes strongly",
    "pov": "holds a point-of-view shot", "roll clockwise": "rolls clockwise",
    "roll counterclockwise": "rolls counterclockwise",
}

#: 幅度与速度。官方：「中等幅度与常速通常省略」——所以只有显式给了才写。
AMPLITUDES = ("small", "large")
SPEEDS = ("slow", "fast")

#: 切镜动词。普切只许这几个；叠化/淡入淡出仅用户明确要求时用。
CUT_VERBS = ("the camera cuts to", "the shot cuts to", "the shot transitions to",
             "the shot changes to", "the shot switches to")

#: `<d>` 外的合法内容不得出现的「关于说话」的元指令（铁律 7 末条，ot13 实测乱语主因）。
#: 身份类一行（音色/年龄/性别/是否在画）是官方 §5.4 授权的，**不在**此列。
SPEECH_META = (
    r"语速|口音|标准普通话|说慢一?点|说快一?点|放慢|加快|"
    r"一口气|不停顿|中途不|只(?:说|念)这(?:一)?句|只需(?:说|念)|"
    r"用(?:标准)?(?:中文|普通话|英文|英语)(?:开口|说|念)|"
    r"\bspeak(?:s)? slowly\b|\bspeak(?:s)? quickly\b|\bwithout pausing\b|"
    r"\bonly say(?:s)? this (?:one )?line\b|\bin (?:standard )?(?:mandarin|english)\b"
)

#: 数值/画幅/帧率这类**结构化参数**不许进正文（铁律 13）。真值在工作流字段里。
#: ⚠ 白名单不能收太窄：`2.35:1` / `4:5` / `16:10` / `fps 24` 都是真参数，必须报；
#: 但自然语言里的 "rhythmic steps"（脚步）、"two steps"、"footsteps" 不能报
#: （T12 真机实测的误报）。所以：steps 只认「名字紧跟数值」，比例用通用 `N:M`
#: 并在扫描前先由 `_blank_ts()` 挖掉 `At MM:SS[.mmm]` 时间戳（否则 `At 00:03`
#: 会被当成 `00:03` 画幅——那不是结构化参数，是切点时间）。
PARAM_WORDS = (
    r"\b\d+(?:\.\d+)?\s*fps\b|\bfps\s*[=:]?\s*\d+|\b\d+(?:\.\d+)?\s*帧\b|"
    r"\bframe[ _]?rate\b|"
    r"\b\d{3,4}\s*[x×]\s*\d{3,4}\b|"
    r"\b\d{1,3}\s*:\s*\d{1,3}\b|"                 # 2.35:1 / 4:5 / 16:10 / 5:4 …
    r"\b\d{1,2}\.\d{1,2}\s*:\s*1\b|"              # 2.35:1 / 1.85:1（显式小数比例）
    r"\bresolution\s*[=:]?\s*\d|\bsteps?\s*[=:]?\s*\d+|\bcfg\s*[=:]?\s*\d|"
    r"\bseed\s*[=:]?\s*\d+|\blength\s*[=:]\s*\d+|\b步数\s*[=:]?\s*\d+"
)

#: 地理方位。画面里没有东南西北，写了模型只能瞎猜（铁律 28）。
GEO = (r"东南|东北|西南|西北|东侧|西侧|南侧|北侧|东墙|西墙|南墙|北墙|"
       r"南端|北端|朝北|朝南|朝东|朝西|"
       r"\b(?:north|south|east|west)(?:ern)?\b|\b(?:north|south|east|west)[- ]"
       r"(?:east|west|north|south)\b")

#: 方位只说「一侧/另一端」也算——没说左右，等于没说（铁律 28 触发原话）。
VAGUE_SIDE = r"一侧|另一端|另一边|\bone side\b|\bthe other side\b|\bthe far end\b"

#: 数量词不可承诺（铁律 26）。H3 的数字遵从是随机的，任何具体计数都是一次犯错机会。
COUNT_EN = (r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
            r"dozens?|hundreds?)\s+(?![a-z]*-(?:year|shot|second)s?\b)"
            r"(?:[a-z]+\s+){0,2}[a-z]+")
COUNT_ZH = r"[一二两三四五六七八九十]+(?:只|把|份|个|名|位|张|件|杯|人|条|根|块)"

#: 否定式（铁律 1）。但铁律 14 **要求**道具写「无任何文字与符号」——
#: 那是官方口径的正面声明，不是否定式封锁。所以先剥掉这句再判。
NEGATION_CARVE_OUT = (
    r"无任何文字与符号|无任何文字和符号|无字|无印刷|no text or symbols|"
    r"free of any text and symbols|without any text or symbols"
)
NEGATION = (r"\b(?:no|not|never|without|none|nobody|nothing|nowhere|cannot|"
            r"can't|don't|doesn't|isn't|aren't|won't)\b|"
            r"没有|不是|不出现|不得|禁止|无人|没有任何|不存在|不许")

#: `<d>` 唯一的台词载荷边界。语言标签是载荷前缀。
D_RE = re.compile(r"<d>\s*(?:\[([A-Za-z]{2,3})\])?(.*?)</d>", re.S)
SPEAKER_RE = re.compile(r"\(\s*(S\d+(?:\s*,\s*S\d+)*)\s*\)")
TS_RE = re.compile(r"^At\s+(\d{2}):(\d{2})\.(\d{3})\s*,?\s*")
FIELD_RE = re.compile(r"^(%s)\s*:" % "|".join(FIELDS), re.M)
SHOT_RE = re.compile(r"\[Shot\s+(\d+)\]")
SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")

_CLAUSE_START = re.compile(
    r"^(?:the|a|an|she|he|they|it|his|her|their|its|there|we|i|this|that)\b", re.I)


# ─────────────────────────────────────────────────────────────
# 时间戳
# ─────────────────────────────────────────────────────────────

def format_ts(seconds: float) -> str:
    """秒 → `MM:SS.mmm`。官方唯一写法；`0:03.5` 与 `00:03` 都不认。"""
    s = max(0.0, float(seconds))
    m, rest = divmod(s, 60.0)
    return "%02d:%06.3f" % (int(m), rest)


def parse_ts(text: str) -> float | None:
    m = TS_RE.match(text.strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000.0


# ─────────────────────────────────────────────────────────────
# 时间线：规范化
# ─────────────────────────────────────────────────────────────

def _as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v if str(x).strip()]


def normalize_timeline(tl: dict) -> dict:
    """补默认值、夹紧范围、稳定排序。幂等。

    规范化的边界：**只做结构性的**（镜头号连续、one_take 清时间戳、字符串变数组）。
    语义问题（Shot 1 带时间戳、时间戳越界、镜头太稀）一律**不改**，留给 `check()`
    出声——静默改写数据比报错更糟，改完用户就不知道原稿长什么样了。
    """
    out = dict(tl or {})
    try:
        out["duration_s"] = round(max(0.1, float(out.get("duration_s") or 8.0)), 3)
    except (TypeError, ValueError):
        out["duration_s"] = 8.0
    mode = str(out.get("mode") or "t2va").lower()
    out["mode"] = mode if mode in MODES else "t2va"
    out["style"] = str(out.get("style") or "").strip().strip(",")
    out["one_take"] = bool(out.get("one_take"))

    shots = []
    for i, s in enumerate(out.get("shots") or [], 1):
        if not isinstance(s, dict):
            continue
        d = dict(s)
        d["index"] = i                       # 序号是结构，不是事实：连号
        ts = d.get("t_start")
        if out["one_take"] or ts in (None, "", 0, 0.0):
            # 一镜到底零时间戳（铁律 11 终裁：时间戳本身就会导致切镜）
            d.pop("t_start", None)
        else:
            try:
                d["t_start"] = round(max(0.0, float(ts)), 3)
            except (TypeError, ValueError):
                d.pop("t_start", None)
        for k in ("entities", "sound"):
            if d.get(k) is not None:
                d[k] = _as_list(d[k])
        if d.get("dialogue"):
            d["dialogue"] = [x if isinstance(x, dict) else {"text": str(x)}
                             for x in d["dialogue"]]
        if d.get("onscreen_text"):
            d["onscreen_text"] = [x if isinstance(x, dict) else {"text": str(x)}
                                  for x in d["onscreen_text"]]
        for k in ("action", "detail", "framing", "cut", "action_kind"):
            if d.get(k) is not None:
                d[k] = str(d[k]).strip()
        shots.append(d)
    # 有时间的按时间排，没时间的一律留在开头（Shot 1）
    shots.sort(key=lambda s: (s.get("t_start") is not None, s.get("t_start") or 0.0))
    for i, s in enumerate(shots, 1):
        s["index"] = i
    out["shots"] = shots
    out["soundscape"] = _as_list(out.get("soundscape"))
    out["music"] = _as_list(out.get("music"))
    return out


def timeline_of(ir: IR) -> dict:
    return ir.timeline or {}


# ─────────────────────────────────────────────────────────────
# 渲染
# ─────────────────────────────────────────────────────────────

@dataclass
class Take:
    """一个 `[Shot N]` 的只读视图。渲染时用，不在 IR 里存对象。"""
    index: int = 1
    t_start: float | None = None
    framing: str = ""
    action: str = ""
    #: `participle` 让动作并入 `a X shot frames <NP> <participle>`；
    #: `clause` 让它整句另起。空 = 让 `_is_clause` 猜。
    #: 为什么要有这个字段：一句 "The baker lifts the shutters" 单看分不出
    #: 是「名词短语 + 分词」还是「完整句」，而 `parse` 在剥 `frames` 时**知道**答案。
    #: 知道的事情不该让下游再猜一遍。
    action_kind: str = ""
    detail: str = ""
    cut: str = "the camera cuts to"
    camera: dict = field(default_factory=dict)
    entities: list = field(default_factory=list)
    dialogue: list = field(default_factory=list)
    onscreen_text: list = field(default_factory=list)
    sound: list = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "Take":
        return Take(
            index=int(d.get("index") or 1),
            t_start=d.get("t_start"),
            framing=str(d.get("framing") or ""),
            action=str(d.get("action") or ""),
            action_kind=str(d.get("action_kind") or "").strip().lower(),
            detail=str(d.get("detail") or ""),
            cut=str(d.get("cut") or "the camera cuts to"),
            camera=dict(d.get("camera") or {}),
            entities=_as_list(d.get("entities")),
            dialogue=list(d.get("dialogue") or []),
            onscreen_text=list(d.get("onscreen_text") or []),
            sound=_as_list(d.get("sound")),
        )


def takes(ir: IR) -> list:
    """取时间线里的镜头。没有时间线就**合成一个**，并让适配器去出声。

    为什么不在这抛错：一份人像 IR 也要能被 H3 编译（用户可能就想动一下）。
    但「从静态 IR 编出来的视频提示词」和「本来就是按时间线写的」不是一回事，
    这个差别由适配器的 warning 交代，不由这里假装没有。
    """
    tl = timeline_of(ir)
    if tl.get("shots"):
        return [Take.from_dict(s) for s in tl["shots"]]
    return [_synthesize(ir)]


def _synthesize(ir: IR) -> Take:
    """静态 IR → 单镜。没有时间信息时，只能把「有谁、在哪里、什么光」串起来。"""
    from .clauses import describe_entity

    leads = [e for e in ir.entities
             if e.kind in ("character", "object") and e.label][:3]
    np = ", ".join(describe_entity(e, with_anchor=False) for e in leads)
    setting = " ".join(ir.atmosphere[:2])
    action = np
    if setting:
        action = (action + " in " + setting) if action else setting
    return Take(index=1, framing=str(ir.composition.get("shot_size") or ""),
                action=action,
                camera=dict(ir.camera or {}),
                entities=[e.id for e in leads],
                dialogue=[], onscreen_text=[
                    {"text": e.text_content, "carrier": e.label or "text"}
                    for e in ir.text_entities])


def _cap(s: str) -> str:
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s


def _is_clause(s: str) -> bool:
    """这句是**完整句**还是**分词短语**。

    `action` 的契约是分词短语（`lifting the letter toward the window`），拼进
    `a medium-wide shot frames <subject> <action>`。但写的人常常直接给完整句
    （`She lifts the letter.`），硬拼会得到 "frames the woman She lifts the letter"。
    所以这里认两种，认错了也只是句子断开，不会丢内容。
    """
    s = (s or "").strip()
    if not s:
        return False
    if s.endswith((".", "。", "!", "！")):
        return True
    if _CLAUSE_START.match(s):
        return True
    # 第三人称单数限定动词：`lifts the shutters` / `slides a bag`。
    # 这类要**另起一句**——拼进 `frames <NP>` 后面会得到两个谓语
    # （"frames a man lifts the shutters"）。识别错也只是多断一句，不丢内容。
    first = s.split()[0].lower()
    return (len(first) > 3 and first.endswith("s")
            and not first.endswith(("ss", "us", "is", "ous")))


def _mid(s: str) -> str:
    """句中小写化首字母。拼接进去的名词短语来自用户，常常自带大写——
    留在句中会得到 `…cuts to a close-up, Steam rises`。全大写缩写（NPC）不动。"""
    s = (s or "").strip()
    if not s:
        return s
    first = s.split(" ")[0]
    if first.isupper() and len(first) > 1:
        return s
    return s[:1].lower() + s[1:]


def _prose_np(s: str) -> str:
    """名词短语 → 散文里能直接接谓语。

    `describe_entity` 是给图像后端用的**属性罗列**，各段之间统一用逗号：
    `a 45-year-old man, wearing a flour-dusted apron`。放进散文、
    后面再接动词就变成 "…, wearing an apron lifts the shutters"——逗号把
    谓语切成了插入语。这里只去掉穿着/手持前面那个逗号，其余一个不动。
    """
    for word in ("wearing ", "holding "):
        s = s.replace(", " + word, " " + word)
    return s


def _subject_of(ir: IR, t: Take) -> tuple:
    """(名词短语, Entity|None)。**只看本镜声明的实体**，不做全局兜底。

    兜底看着贴心，实际是错的：`entities: []` 的意思是「这一镜的主体不出场」
    （拍道具、拍空镜都这样）。偷偷拿 IR 里第一个角色顶上，等于给每一镜都塞人。
    合成单镜（静态 IR）走 `_synthesize`，那里会把实体显式写进 `entities`。
    """
    from .clauses import describe_entity

    for eid in t.entities:
        e = ir.entity(eid)
        if e is not None:
            return _prose_np(describe_entity(e, with_anchor=False, extras=False)), e
    return "", None


def _short_ref(e) -> str:
    """同一镜里的第二次提及用短称。全称重复两遍是噪声，而且会把句子拖长。

    「A 45-year-old man wearing a flour-dusted apron (S1) says: …」在前文已经
    交代过一次的情况下，改成「The baker (S1) says: …」不会丢任何事实对齐——
    `expand.fixed_items` 核的是年龄/穿着这些**词**在不在，短称不影响它。
    """
    if e is None:
        return "The speaker"
    label = str(e.label or "").strip()
    if label:
        return "The " + label
    return "The speaker"


def _pronoun(e) -> str:
    g = str((e.descriptors or {}).get("gender") or "").lower() if e else ""
    if g in ("man", "male", "boy", "男性", "男", "男人", "男子"):
        return "his"
    if g in ("woman", "female", "girl", "女性", "女", "女人", "女子"):
        return "her"
    return "their"


def _camera_sentence(cam: dict) -> str:
    """运镜：类型 + 幅度 + 速度。中等幅度与常速官方允许省略，所以只有给了才写。"""
    move = str(cam.get("move") or cam.get("type") or "").strip().lower()
    if not move:
        return ""
    verb = CAMERA_VERBS.get(move, "")
    if not verb:
        verb = move if " " not in move else move
    bits = ["The camera", verb]
    amp = str(cam.get("amplitude") or "").strip().lower()
    if amp in AMPLITUDES:
        bits.append("with %s amplitude" % amp)
    spd = str(cam.get("speed") or "").strip().lower()
    if spd in SPEEDS:
        bits.append("at %s speed" % spd)
    toward = str(cam.get("toward") or "").strip()
    if toward:
        bits.append("toward %s" % toward)
    return _cap(" ".join(bits)) + "."


def _dialogue_sentence(ir: IR, d: dict, fallback, lead_id: str = "") -> str:
    """`(Sx)` 在说话人之后、`<d>` 之前；语言只在 `<d>` 内。

    铁律 7 末条：`<d>` 外只留最小动作句，语速/口音/语言声明一律不写——
    那些会被读进语音轨，症状是杂字与碎句多包。
    """
    text = str(d.get("text") or "").strip()
    if not text:
        return ""
    subj = str(d.get("subject") or "").strip()
    e = ir.entity(subj) if subj else None
    if e is not None:
        if subj == lead_id:                 # 本镜刚用全称交代过，这里只留短称
            phrase = _short_ref(e)
        else:
            from .clauses import describe_entity

            phrase = describe_entity(e, with_anchor=False, extras=False)
    else:
        phrase, e = fallback

    who = str(d.get("speaker") or "").strip() or "S1"
    lang = str(d.get("lang") or "zh").strip() or "zh"
    carry = str(d.get("carry") or "").strip()
    body = "<d>[%s]%s</d>" % (lang, text)
    if carry in ("scenetrans", "cutoff"):
        body += "<%s>" % carry

    verb = str(d.get("verb") or "").strip()
    if not verb:
        verb = "says in an off-screen voiceover:" if d.get("offscreen") else "says:"
    elif not verb.endswith(":"):
        verb += ":"

    head = ("%s (%s) %s %s" % (phrase, who, verb, body)) if phrase else \
           ("(%s) %s %s" % (who, verb, body))
    if d.get("offscreen"):
        head += " while %s lips remain completely closed." % _pronoun(e)
    return _cap(head)


def _text_sentence(ir: IR, item: dict) -> str:
    """画面文字：原文照抄、不翻译、加英文双引号（官方 §4.5）。

    铁律 14 另有一条工程口径：小字要 10–20 步才成型，招牌字走后期叠加。
    这条在 `check()` 里出声，不在这里删——删掉用户写的字是改意图。
    """
    text = str(item.get("text") or "").strip()
    if not text:
        return ""
    carrier = str(item.get("carrier") or "").strip() or "A surface"
    note = str(item.get("note") or "").strip()
    sent = '%s reading "%s"' % (carrier, text)
    sent += (note or "is visible in the frame")
    return _cap(sent.rstrip(".")) + "."


def _gerund(s: str) -> bool:
    first = (s or "").strip().split(" ")[0].lower()
    return len(first) > 4 and first.endswith("ing")


def _shot_text(ir: IR, t: Take, style: str, one_take: bool) -> str:
    subj, ent = _subject_of(ir, t)
    act = (t.action or "").strip().rstrip(".")
    lead_id = t.entities[0] if t.entities else ""

    # 动作怎么接进句子，三条路：
    #   ① 分词短语 → 并入 `a X shot frames <NP> <participle>`（官方句式）
    #   ② 完整句   → 整句另起（塞进 frames 后面会变两个谓语）
    #   ③ 没主语   → 同上，只是没有 NP 可带
    participle = bool(act) and (t.action_kind == "participle"
                                or (t.action_kind != "clause" and not _is_clause(act)))
    opening, extra = subj, ""
    if act:
        if not subj:
            opening, extra = (act if participle else ""), ("" if participle else act)
        elif participle:
            opening = (subj + " " + act).strip()
        else:
            opening, extra = "", (subj + " " + act).strip()
    # 名词短语后面能接谓语吗：有主语当然能；没有主语时，只有分词能接在 frames 后面
    np_ok = bool(opening) and (bool(subj) or participle)

    head = "[Shot %d]" % t.index
    sentences: list = []

    if t.index == 1 or t.t_start is None:
        prefix = ", ".join(x for x in (style, t.framing) if x)
        if np_ok and t.framing:
            first = prefix + " frames " + _mid(opening)
        elif np_ok:
            first = (prefix + ", " + _mid(opening)) if prefix else opening
        else:
            first = prefix or "the shot opens"
            if opening:
                extra = opening
    else:
        first = "At %s, %s" % (format_ts(t.t_start), t.cut)
        if t.framing:
            first += " " + t.framing
        if np_ok:
            first += (" of " if _gerund(opening) else ", ") + _mid(opening)
        elif opening:
            extra = opening

    sentences.append(head + " " + _cap(first.rstrip(" .")) + ".")
    if extra:
        sentences.append(_cap(extra.rstrip(" .")) + ".")
    cam = _camera_sentence(t.camera)
    if cam:
        sentences.append(cam)
    for d in t.dialogue:
        s2 = _dialogue_sentence(ir, d, (subj, ent), lead_id)
        if s2:
            sentences.append(s2)
    for item in t.onscreen_text:
        s2 = _text_sentence(ir, item)
        if s2:
            sentences.append(s2)
    if t.detail:
        sentences.append(_cap(t.detail.rstrip(" .")) + ".")
    return " ".join(sentences)


def mode_instruction(mode: str, tl: dict) -> str:
    """模式指令。官方要求它是**终稿第一行**，后空一行再写三字段。

    T2VA 没有指令行——写一句反而是噪声。
    """
    mode = (mode or "t2va").lower()
    if mode == "t2va":
        return ""
    dur = float(tl.get("duration_s") or 8.0)
    shots = tl.get("shots") or []
    last = int(shots[-1].get("index") or len(shots) or 1) if shots else 1
    if mode == "i2va":
        return ("For the target video, at 0.00 seconds into the target video, "
                "<Picture 1> (from [Shot 1]) is fully referenced.")
    if mode == "fl2va":
        return ("How the reference pictures align with the target video — "
                "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the "
                "target video; Picture 2 (from Shot %d) aligns with the %.2f-second "
                "mark of the target video." % (last, dur))
    if mode == "l2va":
        return ("How the reference pictures align with the target video — "
                "<Picture 1> (from [Shot %d]) aligns with the %.2f-second mark of "
                "the target video." % (last, dur))
    return ""


def render(ir: IR, mode: str | None = None) -> str:
    """IR → H3 官方三字段终稿。确定性，零模型。"""
    tl = timeline_of(ir)
    m = (mode or tl.get("mode") or "t2va").lower()
    if m not in MODES:
        m = "t2va"
    style = str(tl.get("style") or ir.style.get("medium") or
                ir.style.get("look") or "").strip().strip(",")
    one_take = bool(tl.get("one_take"))
    shots = takes(ir)
    body = " ".join(_shot_text(ir, t, style if t.index == 1 else "", one_take)
                    for t in shots)
    sound = " ".join(x.strip() for x in tl.get("soundscape") or [] if x.strip())
    music = " ".join(x.strip() for x in tl.get("music") or [] if x.strip()) or "N/A"

    blocks = []
    instr = mode_instruction(m, tl)
    if instr:
        blocks.append(instr)
    blocks.append("%s: %s" % (FIELDS[0], body))
    blocks.append("%s: %s" % (FIELDS[1], sound or "N/A"))
    blocks.append("%s: %s" % (FIELDS[2], music))
    return "\n\n".join(blocks)


# ─────────────────────────────────────────────────────────────
# 反向解析：H3 终稿 → IR
# ─────────────────────────────────────────────────────────────

_SCENE_OPEN = re.compile(r"^(.*?)(?:\bframes\b|\breveals\b|\bshows\b)\s+", re.I)
#: 景别词。`a medium-wide shot` / `a close-up` / `an establishing view` 都算。
_FRAMING = re.compile(r"\b(?:shot|close-?up|angle|view|two-shot|establishing|"
                      r"wide|medium|extreme|full|long|over-the-shoulder|aerial)\b", re.I)


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def split_fields(text: str) -> tuple:
    """→ (指令行, {field: value})。字段缺失的键**不补**，让 check 去报。"""
    t = _strip_fence(text)
    hits = list(FIELD_RE.finditer(t))
    preamble = t[:hits[0].start()].strip() if hits else t
    out = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(t)
        out[m.group(1)] = t[m.end():end].strip()
    return preamble, out


def _bare_label(name) -> str:
    """`The baker` → `baker`。实体 label 是「是什么」，不该带冠词——
    带冠词进 `describe_entity` 会渲染成 `a The baker`。"""
    s = re.sub(r"^(?:the|a|an)\s+", "", str(name or "").strip(), flags=re.I)
    return s.strip(" ,，") or "person"


def looks_like(text: str) -> bool:
    """这段文本是不是 H3 三字段终稿。

    判据是**两个以上**字段名在行首出现——只认一个太松（正文里提一句字段名就误判），
    三个都认太紧（缺字段的稿子恰恰最需要被读出来判错）。
    """
    hits = {m.group(1) for m in FIELD_RE.finditer(_strip_fence(text or ""))}
    return len(hits) >= 2


def _detect_mode(preamble: str) -> str:
    if re.search(r"How the reference pictures align", preamble, re.I):
        return "fl2va" if re.search(r"Picture 2", preamble) else "l2va"
    if re.search(r"is fully referenced", preamble, re.I):
        return "i2va"
    return "t2va"


def _split_sentences(text: str) -> list:
    return [s.strip() for s in SENT_SPLIT.split(text or "") if s.strip()]


def _parse_shot_body(body: str) -> tuple:
    """一镜的正文 → (t_start, cut, camera, dialogue, texts, action, detail)。"""
    ts = None
    rest = body.strip()
    m = TS_RE.match(rest)
    if m:
        ts = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000.0
        rest = rest[m.end():].strip()
    cut = ""
    for verb in CUT_VERBS:
        if rest.lower().startswith(verb):
            cut = verb
            rest = rest[len(verb):].strip(" ,")
            break

    camera: dict = {}
    dialogue: list = []
    texts: list = []
    keep: list = []

    for sent in _split_sentences(rest):
        low = sent.lower()
        # ① 台词：整句拿走，但把 `(Sx)` 之前那截留下（可能是动作）
        ds = list(D_RE.finditer(sent))
        if ds:
            first = ds[0]
            prefix = sent[:first.start()]
            cut_at = max(prefix.rfind(", "), prefix.rfind("，"))
            if cut_at > 0:
                # `他放下杯子，张三 (S1) 说：` —— 逗号前是动作，留下
                keep.append(prefix[:cut_at].strip())
                prefix = prefix[cut_at + 1:]
            # 没有逗号时整段都是说话人指称（`The baker `），**不留**：
            # 它会被重新从实体生成，留下来就变成一句孤零零的 `The baker.`
            sp = SPEAKER_RE.search(prefix)
            speaker = sp.group(1).replace(" ", "") if sp else ""
            name = (prefix[:sp.start()] if sp else prefix).strip(" ,，")
            if name and cut_at > 0 and not re.search(
                    r"(says|shout|whisper|voiceover|开口|说)", name, re.I):
                keep.append(name)
            for d in ds:
                dialogue.append({
                    "speaker": speaker or "S1",
                    "lang": (d.group(1) or "zh"),
                    "text": d.group(2),
                    "offscreen": "offscreen" in low or "voiceover" in low,
                    "name": name.strip(" ,，"), "subject": "",
                })
            continue
        # ② 画面文字
        tm = re.search(r'(.*?)\breading\s+"([^"]+)"', sent)
        if tm:
            texts.append({"text": tm.group(2), "carrier": tm.group(1).strip()})
            continue
        # ③ 运镜
        cm = re.match(r"^(?:the camera|the shot)\s+(.*)", sent, re.I)
        if cm and not camera:
            tail = cm.group(1).rstrip(".")
            low = tail.lower()
            cam: dict = {}
            # 按整段动词短语匹配、最长优先。只比第一个词会把 "pans left"
            # 认成 "pan left"——方向反了，而且是静默的（实测踩过）。
            hit = next((k for k in sorted(CAMERA_VERBS, key=lambda x: -len(CAMERA_VERBS[x]))
                        if low.startswith(CAMERA_VERBS[k])), "")
            if hit:
                cam["move"] = hit
            else:
                mv = re.match(r"([a-z]+(?:\s[a-z]+)?)", low)
                if mv:
                    cam["move"] = mv.group(1)
            am = re.search(r"with\s+(small|large)\s+amplitude", tail, re.I)
            if am:
                cam["amplitude"] = am.group(1).lower()
            sm = re.search(r"at\s+(slow|fast)\s+speed", tail, re.I)
            if sm:
                cam["speed"] = sm.group(1).lower()
            tw = re.search(r"toward\s+(.+)$", tail, re.I)
            if tw:
                cam["toward"] = tw.group(1).rstrip(".")
            camera = cam
            continue
        keep.append(sent)

    action = ""
    detail = ""
    framing = ""
    style = ""
    kind = ""
    if keep:
        head = keep[0]
        # `… frames <NP> <动作>` 里，frames 之前是「风格 + 景别」，之后才是画面。
        # 不剥这一层，重编译会得到 "frames a man wearing an apron lifts the shutters"
        # ——两个谓语句。剥开的壳记进 framing/style，一个字不丢。
        rest = keep[1:]
        m2 = re.search(r"\b(?:frames|reveals|shows)\b\s+", head, re.I)
        kind = "participle" if m2 else "clause"
        if m2:
            lead = head[:m2.start()]
            head = head[m2.end():]
        else:
            lead = head
            head = ""
        segs = [s.strip() for s in lead.split(",") if s.strip()]
        # 景别段只认**短**段（≤4 词）。长段里可能把动作一起包进来了
        # （`a close-up of steam rising from the bread`），硬拆会丢内容。
        idx = next((i for i, s in enumerate(segs)
                    if _FRAMING.search(s) and len(s.split()) <= 4), -1)
        if idx >= 0:
            framing = segs[idx]
            style = ", ".join(segs[:idx])
            head = ", ".join(x for x in [", ".join(segs[idx + 1:]), head] if x)
        else:
            style = ", ".join(segs)
        if not head and rest:
            head, rest = rest[0], rest[1:]
        action = head.strip().rstrip(".")
        detail = " ".join(s.rstrip(".") for s in rest).strip()

    return ts, cut, camera, dialogue, texts, action, detail, framing, style, kind


def parse(text: str) -> dict:
    """H3 官方终稿 → IR dict。**不猜结构**：认不出的句子原样进 detail，不丢。

    为什么值得做：一份人给的 H3 词，只有先变成事实，才能被 `roundtrip` 查、
    被 `invariants` 体检、被改写成另一个后端。直接改字符串是没法验证的。
    """
    preamble, fields = split_fields(text)
    mode = _detect_mode(preamble)
    desc = fields.get(FIELDS[0], "")
    sound = fields.get(FIELDS[1], "")
    music = fields.get(FIELDS[2], "")

    shots = []
    marks = list(SHOT_RE.finditer(desc))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(desc)
        body = desc[m.end():end].strip()
        ts, cut, cam, dlg, txs, action, detail, framing, style, kind = \
            _parse_shot_body(body)
        shots.append({"index": int(m.group(1)), "t_start": ts, "cut": cut,
                      "camera": cam, "dialogue": dlg, "onscreen_text": txs,
                      "action": action, "action_kind": kind, "detail": detail,
                      "framing": framing, "style": style})

    tl = {
        "style": next((s["style"] for s in shots if s.get("style")), ""),
        "mode": mode,
        "shots": shots,
        "soundscape": [s for s in _split_sentences(sound) if s],
        "music": [] if music.strip().upper() == "N/A" else
                 [s for s in _split_sentences(music) if s],
        "duration_s": 8.0,
        "one_take": len(shots) <= 1,
    }
    # 时长：末镜时间戳是唯一能读到的下界，不能反推片长；只在有切点时给个保守值。
    ts_list = [s["t_start"] for s in shots if s.get("t_start")]
    if ts_list:
        tl["duration_s"] = round(max(ts_list) + 2.5, 3)

    # 说话人 → character 实体，这样再编译时 describe_entity 还有东西可写
    entities, seen = [], {}
    for s in shots:
        for d in s["dialogue"]:
            who = d.get("speaker") or "S1"
            if who in seen:
                continue
            eid = "s%s" % who.lstrip("Ss")
            seen[who] = eid
            # 只有在「本镜没有自带主语」时才把说话人挂进实体。
            # 动作已经自带名词短语（`A 45-year-old man wearing …`）时再挂一个，
            # 会渲染成「frames a baker a 45-year-old man …」——两个名词短语并排。
            if not s.get("entities") and not re.match(r"^(?:a|an|the)\b",
                                                     s.get("action") or "", re.I):
                s["entities"] = [eid]
            entities.append({"id": eid, "kind": "character",
                             "label": _bare_label(d.get("name")),
                             "importance": 0.9,
                             "descriptors": {"voice_id": who}})
            d["subject"] = eid

    return {
        "ir_version": "0.1",
        "task": mode,
        "canvas": {"aspect_ratio": "16:9"},
        "entities": entities,
        "timeline": tl,
        "provenance": {"source": "h3-parse", "preamble": preamble[:400]},
    }


# ─────────────────────────────────────────────────────────────
# 判定：28 条铁律里可机检的子集
# ─────────────────────────────────────────────────────────────

def _blank_d(text: str) -> str:
    """把 `<d>…</d>` 挖掉，只留结构。判定「标签外夹带」时用。"""
    return D_RE.sub("<d/>", text)


def _cjk_ratio(s: str) -> float:
    chars = [c for c in s if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if "\u3400" <= c <= "\u9fff") / float(len(chars))


def check(text: str, ir: IR | None = None, *, music_policy: str = "any",
          lang_check: bool = True) -> list:
    """H3 终稿的机检闸。零模型。

    `music_policy` 管的是**内容策略**，不是格式合法性：

      - `any`（默认）——`N/A` 或一段配乐描述都合法。这是官方格式的取值面：
        有画外配乐就写，没有就写 `N/A`。两者都要求填成一个模型读得懂的值，
        所以 `无。` / `none` / `-` 这类「看起来是空但又不是 N/A」的写法仍然报错。
      - `na`  —— 产出端不配乐、后期统一铺 BGM 的流水线用这条：必须**严格等于**
        `N/A`（铁律 8），写配乐描述会被拦。

    默认不选 `na`：那是某条产线的取舍，不是格式要求。要不要它由使用者决定。

    每条判据都标了它守哪条铁律。**没有判据的层不假装绿**：表演、情绪、
    构图好坏这里一条都不管，`check()` 全绿只代表「形式与自洽过关」。
    """
    issues: list = []

    def err(code: str, msg: str, fix: str = "") -> None:
        issues.append(Issue("error", code, msg, fix))

    def warn(code: str, msg: str, fix: str = "") -> None:
        issues.append(Issue("warn", code, msg, fix))

    raw = text or ""
    if "```" in raw:
        err("H3-FENCE", "prompt 里带 Markdown 代码围栏", "去掉 ``` 再提交；模型会把它们当正文")

    preamble, fields = split_fields(raw)
    missing = [f for f in FIELDS if f not in fields]
    if missing:
        err("H3-FIELD", "缺字段：%s" % "、".join(missing),
            "三字段必须齐全且顺序固定：%s" % " / ".join(FIELDS))
    order = [m.group(1) for m in FIELD_RE.finditer(raw)]
    if order != [f for f in FIELDS if f in fields]:
        err("H3-FIELD-ORDER", "字段顺序不是官方顺序（实际：%s）" % " → ".join(order),
            "顺序固定为 " + " → ".join(FIELDS))

    desc = fields.get(FIELDS[0], "")
    music = (fields.get(FIELDS[2]) or "").strip()
    sound = (fields.get(FIELDS[1]) or "").strip()

    # ── 铁律 8：non_diegetic_music 取值 ──────────────────────
    if FIELDS[2] in fields:
        bare = music.rstrip("。.！! ")
        if not music:
            err("H3-MUS", "non_diegetic_music 为空",
                "无画外配乐写 N/A；有就写一两句英文配乐描述")
        elif bare == "N/A" or bare.upper() in ("N/A", "NA"):
            pass                                  # 合法：全片无画外配乐
        elif music_policy == "na":
            err("H3-MUS", "non_diegetic_music = %r；`--music na` 要求严格写 N/A"
                % music[:24], "改成 N/A；人物能听见的音乐属画内声，写进正文或声景")
        elif len(re.findall(r"[A-Za-z]{2,}", music)) < 2:
            # 既不是 N/A，也不像一段配乐描述 —— `无。` / `none` / `-` 都属于这类。
            # 这条判的是「这个字段有没有填成可被模型读到的值」，与「填哪种值」无关；
            # 后者是产线取舍，交给 `--music na`。
            err("H3-MUS", "non_diegetic_music = %r 既不是 N/A，也不是配乐描述"
                % music[:24], "无画外配乐写 N/A；有就写一两句英文配乐描述")
    if FIELDS[1] in fields and sound.rstrip("。.").upper() == "N/A" and desc:
        warn("H3-SND", "overall_soundscape 是 N/A", "只有全片静音才允许 N/A")

    body = desc

    # ── 铁律 11 / 16：时间戳 ─────────────────────────────────
    marks = list(SHOT_RE.finditer(body))
    if not marks:
        err("H3-SHOT", "正文里没有 [Shot N] 标记", "至少要有 [Shot 1]")
    shots = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        seg = body[m.end():end]
        ts = parse_ts(seg.strip())
        shots.append((int(m.group(1)), ts, seg))

    one_take = len(shots) == 1 and bool(re.search(
        r"一镜到底|连续不切|one continuous take|single continuous", body, re.I))
    for idx, ts, _seg in shots:
        if idx == 1:
            if ts is not None and ts > 0:
                err("H3-SHOT1-TS", "Shot 1 带了时间戳 %s" % format_ts(ts),
                    "首镜不写时间戳；实测开场时间戳会把首帧锁成置景图（铁律 11）")
        else:
            if ts is None:
                err("H3-SHOTN-TS", "Shot %d 缺时间戳" % idx,
                    "Shot 2 起必须同行写 `At MM:SS.mmm`，否则模型在该区间没有新指令")
            elif one_take:
                err("H3-ONETAKE-TS", "声明了一镜到底却出现时间戳 %s" % format_ts(ts),
                    "一镜到底一律零时间戳：时间戳本身就会导致切镜")

    stamped = [(i, ts) for i, ts, _ in shots if ts is not None]
    for a, b in zip(stamped, stamped[1:], strict=False):
        if b[1] <= a[1]:
            err("H3-TS-ORDER", "时间戳没有严格递增：%s → %s"
                % (format_ts(a[1]), format_ts(b[1])), "切点必须严格递增且落在片长内")

    # ── 铁律 16：铺满三线（前不空窗 / 后不定格 / 中不断档）──
    if ir is not None:
        dur = float((ir.timeline or {}).get("duration_s") or 0) or None
        if dur and stamped:
            head = stamped[0][1]
            tail = dur - stamped[-1][1]
            if head > 3.0 + 1e-6:
                err("H3-TS-HEAD", "首个切点在 %.2fs，超过 3.0s" % head,
                    "首镜太长，开场空窗会被参考图填满")
            elif head > 1.0:
                warn("H3-TS-HEAD", "首个切点在 %.2fs，超过 1.0s" % head,
                     "官方口径 ≤1.0s；超过 3.0s 会判 error")
            if tail > 2.5 + 1e-6:
                err("H3-TS-TAIL", "末个切点距片尾 %.2fs，超过 2.5s" % tail,
                    "末段无新指令必定格（铁律 16 尾线）")
            for (ia, ta), (ib, tb) in zip(stamped, stamped[1:], strict=False):
                if tb - ta > 3.0 + 1e-6:
                    err("H3-TS-GAP", "Shot %d→%d 间隔 %.2fs，超过 3.0s"
                        % (ia, ib, tb - ta), "中段动作真空会僵住（铁律 16 间隔线）")

    # ── 铁律 7：`<d>` 是唯一台词载荷 ─────────────────────────
    for i, d in enumerate(D_RE.finditer(body), 1):
        if not d.group(1):
            err("H3-D-LANG", "第 %d 个 <d> 缺语言标签" % i,
                "写成 <d>[zh]台词</d>；语言标签是载荷前缀，缺了模型可能按正文语言读")
    outside = _blank_d(body)
    leak = re.findall(r"[「“]([^」”]{1,40})[」”]", outside)
    leak += re.findall(r'"([^"]{1,60})"', outside)
    leak = [x for x in leak if x.strip()]
    if leak:
        err("H3-D-OUT", "标签外夹带引号文本：%s" % "、".join(leak[:3]),
            "台词必须整体放进 <d>…</d>；标签外的导演说明会被读进语音轨（断言 A，永不摘除）")

    meta = re.findall(SPEECH_META, outside, re.I)
    if meta:
        err("H3-D-META", "<d> 外写了「关于说话」的元指令：%s" % "、".join(sorted(set(meta))[:3]),
            "语速/口音/语言声明/人声锁一律不写；要快慢只能改台词长度与逗号分句")

    # ── 铁律 1 / 26 / 28 / 13：正文层 ────────────────────────
    # 判定前先挖掉两类「不是正文」的东西，否则判据会自己踩自己：
    # 时间戳（`00:03.500` 长得像画幅）、`<d>` 里的台词（那是要念的字，不是描述）。
    no_ts = _blank_ts(body)
    probe = _blank_d(no_ts)
    carve = re.sub(NEGATION_CARVE_OUT, " ", probe, flags=re.I)
    neg = re.findall(NEGATION, carve, re.I)
    if neg:
        err("H3-NEG", "出现否定式：%s" % "、".join(sorted(set(neg))[:5]),
            "改成正向描述（否定句在 conditioning 里仍会激活被否定的概念）；"
            "「无任何文字与符号」是官方口径的正面声明，已豁免")

    counts = re.findall(COUNT_EN, carve, re.I) + re.findall(COUNT_ZH, carve)
    if counts:
        flat = [" ".join(c) if isinstance(c, tuple) else str(c) for c in counts]
        warn("H3-COUNT", "出现数量词：%s" % "、".join(flat[:4]),
             "改成 a few / 几只；H3 的数字遵从是随机的，确切数量走后期叠字（铁律 26）")

    geo = re.findall(GEO, carve, re.I) + re.findall(VAGUE_SIDE, carve, re.I)
    if geo:
        err("H3-GEO", "出现地理方位/模糊方位：%s" % "、".join(sorted(set(geo))[:4]),
            "画面里没有东南西北；改成参照物坐标（画面左墙 / 近端向纵深）（铁律 28）")

    params = re.findall(PARAM_WORDS, no_ts, re.I)
    if params:
        err("H3-PARAM", "正文写了结构化参数：%s" % "、".join(sorted(set(params))[:4]),
            "帧数/分辨率/帧率/步数是工作流字段，写进文本只会制造两套真值（铁律 13）")

    # ── 铁律 12·补：有台词段的正文语言 ───────────────────────
    if lang_check and D_RE.search(body):
        prose = D_RE.sub(" ", body)
        r = _cjk_ratio(prose)
        if r > 0.4:
            warn("H3-LANG", "有台词段正文的 CJK 占比 %.0f%%" % (r * 100),
                 "有台词段建议正文英译：中文正文 + 中文台词会让模型分不清"
                 "「描述」与「要念的字」，CER 实测差 11 倍（铁律 12·补）")

    # ── 铁律 14：画面文字 ────────────────────────────────────
    if ir is not None and ir.text_entities:
        quoted = set(re.findall(r'reading\s+"([^"]+)"', body))
        for e in ir.text_entities:
            if (e.text_content or "").strip() and e.text_content not in quoted:
                warn("H3-TEXT", "IR 里的图内文字没有进正文：%s" % e.text_content[:24],
                     "要字就写 reading \"…\" 并给大字；不要字就明写无任何文字与符号"
                     "——小字要 10–20 步才成型（铁律 14）")

    return issues


def _blank_ts(text: str) -> str:
    """把 `At MM:SS[.mmm]` 时间戳挖掉，免得被当成「画幅 N:M」判成结构化参数。

    两种写法都挖：官方推荐 `At 00:03.500`，但旧稿/模型输出常省略毫秒（`At 00:03`）。
    `At 00:03` 是切点时间，不是参数——通用 `N:M` 比例规则必须在挖掉之后才能跑。
    """
    return re.sub(r"At\s+\d{1,3}:\d{1,2}(?:\.\d{1,3})?", "At <ts>", text, flags=re.I)


def verdict(issues: list) -> str:
    if any(i.level == "error" for i in issues):
        return "fail"
    if any(i.level == "warn" for i in issues):
        return "warn"
    return "pass"


def report(issues: list) -> str:
    """给人看的一段。空清单也要有话说——「没报错」不等于「检查过」。"""
    if not issues:
        return "0 error / 0 warn（只覆盖可机检子集：格式/时间戳/台词边界/否定/数量/方位/参数）"
    lines = []
    for i in issues:
        lines.append("%s %s %s" % ("ERROR" if i.level == "error" else "WARN ",
                                   i.code, i.message))
        if i.fix:
            lines.append("      → " + i.fix)
    return "\n".join(lines)
