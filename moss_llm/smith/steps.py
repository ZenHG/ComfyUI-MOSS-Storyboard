# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""扩写三拆：补全 / 翻译 / 改写，三步各自独立 prompt、独立开关、独立判据。

## 为什么拆

`--mode llm` 原来**一次调用同时做三件事**。出问题时无法归因：是补错了（把没说的
写死）、翻错了（术语没对上）、还是改丢了（重排时丢了事实）？docs/24 §1 把这三件事
定义为不同语义、需要不同资产、可验证性也不同；这里把它们拆开，每步只做一件事。

| 步 | 只做什么 | 判据 | 资产 |
|---|---|---|---|
| completion 补全 | 只填 OPEN 槽位 | 每项填充可追溯到「该风格的公认默认」；改固定项 / 造数 / 造引号文本 / 造专名判 error | ``DEFAULT_LIBRARY`` |
| translation 翻译 | 只把中文口语换成目标方言术语 | 术语表命中率（分母 = 源文本里出现的术语数） | ``GLOSSARY`` |
| rewrite 改写 | 只重排顺序、不改事实 | 事实集合不变（复用 roundtrip 的 scanner） | — |

## 开关与降级

每步都能单独关（``Adapter.expand_steps``）。**关掉任何一步产物仍合法**：管线起点是
该方言的**确定性草稿**（不调模型、必然合法），最后仍由 ``expand.verify`` 兜底；
三步全关 = 直接交付确定性草稿。默认 ``expand_steps=None``，即保持历史的单次调用，
确定性基线与历史 llm 调用次数都不变——拆步是 opt-in。

## 判据的边界（诚实清单，不是假通过）

机器判据只能判「可客观判」的部分，判不了「写得好」：

- 补全的默认值库是**闭集**：模型写出同义但不在库里的措辞会报 error。这是有意的
  保守设计（宁可报错给人看，也不静默通过），代价是可能误报；库当前只覆盖
  摄影 / 漫画 / 插画三类媒介的高频默认，未知媒介退回 ``generic``。
- 补全**不覆盖开放词汇的幻觉**（凭空加的情节、关系、氛围），只覆盖五类可判对象：
  新数字、新引号文本、新专名、槽位填充不可追溯、新颜色词（warn）。
  开放词汇只能靠人读或第二个模型交叉——这一点写在 docs/26 的「已知限制」里。
- 翻译的术语表同样是闭集：未收录的中文词判不了，只能报「仍有术语表未覆盖的中文字符」
  warn；命中率的分母是**源文本里真正出现的术语数**，为 0 时判「不适用」而不是
  按 100% 记——分母为 0 的比率没有意义。
- 改写只判字面事实集合（数字 / 年龄 / 引号文本 / 颜色 / 方位）与词袋保留率；
  「重排得好不好」判不了。
- 步级判据**不判长度带**：长度由最终 ``expand.verify`` 判一次，避免同一条警告被
  两个判据各算一遍、把报告灌满。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .expand import (_QUOTE, _WORD, Report, _norm, fixed_items, open_slot_dims,
                     verify, words)

STEP_COMPLETION = "completion"
STEP_TRANSLATION = "translation"
STEP_REWRITE = "rewrite"
STEP_ORDER = (STEP_COMPLETION, STEP_TRANSLATION, STEP_REWRITE)

STEP_LABELS = {STEP_COMPLETION: "补全", STEP_TRANSLATION: "翻译", STEP_REWRITE: "改写"}

#: 每步 system prompt 里的固定标记。测试与日志靠它识别「这是哪一步」，
#: 不依赖调用顺序——顺序改了也不会静默错配。
STEP_MARKERS = {
    STEP_COMPLETION: "COMPLETION STEP of a three-step prompt pipeline",
    STEP_TRANSLATION: "TRANSLATION STEP of a three-step prompt pipeline",
    STEP_REWRITE: "REWRITE STEP of a three-step prompt pipeline",
}

#: 可以由默认值库补的维度。**不含 subject / text**：
#: 主体缺了是 IR 的洞（编一个主体 = 换一个人），图内文字必须逐字（不是默认值）。
FILLABLE_DIMS = ("setting", "camera", "lighting", "material", "medium")

_CJK = re.compile(r"[\u4e00-\u9fff]+")

# ── 资产 1：补全的默认值库 ────────────────────────────────────

#: 媒介（``IR.style.medium``）→ 槽位 → 该风格的公认默认值。
#: 只收「高频、可复现、无争议」的条目；每条是闭集里的一个合法选项。
#: 判据只认**库里出现过的条目**，所以这份表既是生成约束也是核对清单。
DEFAULT_LIBRARY = {
    "generic": {
        "setting": ("a quiet interior", "a narrow street", "an open landscape",
                    "a plain studio backdrop", "a corner of a room", "an overcast sky",
                    "a backstreet after rain", "a sunlit room"),
        "camera": ("medium shot", "close-up", "wide shot", "eye level", "low angle",
                   "high angle", "35mm lens", "50mm lens", "85mm lens",
                   "shallow depth of field", "deep focus"),
        "lighting": ("soft overcast daylight", "natural light", "window light",
                     "soft directional light", "single key light", "diffused light",
                     "golden hour", "blue hour", "backlight", "rim light"),
        "material": ("wet asphalt", "brushed metal", "matte fabric", "polished wood",
                     "rough stone", "glass reflection", "film grain", "smooth skin",
                     "rain-soaked pavement"),
        "medium": ("photograph", "digital photograph", "35mm film", "oil painting",
                   "watercolor", "ink illustration", "3D render"),
    },
    "photograph": {
        "setting": ("a city street", "a backstreet after rain", "a studio interior",
                    "a sunlit room", "an outdoor market"),
        "lighting": ("soft overcast daylight", "natural window light", "golden hour",
                     "blue hour", "warm key light", "cool fill light", "backlight",
                     "hard directional sunlight", "diffused overcast light"),
        "material": ("wet asphalt", "rain-soaked pavement", "brushed metal",
                     "matte cotton", "polished wood", "glass reflection", "film grain"),
        "medium": ("photograph", "35mm film", "digital photograph"),
    },
    "manga": {
        "setting": ("a school hallway", "a rooftop", "a city street at dusk",
                    "an empty classroom"),
        "camera": ("close-up", "medium shot", "wide shot", "low angle", "high angle"),
        "lighting": ("high-contrast shadows", "flat even lighting",
                     "single hard light source", "dramatic backlight"),
        "material": ("clean ink lines", "cross-hatched shading", "halftone dots",
                     "solid black fills"),
        "medium": ("manga", "ink drawing", "comic lineart"),
    },
    "illustration": {
        "setting": ("a painted landscape", "a stylised interior", "a textured backdrop"),
        "camera": ("medium shot", "close-up", "wide shot", "eye level", "low angle"),
        "lighting": ("soft ambient light", "dramatic side light", "warm glow",
                     "cool shadow"),
        "material": ("visible brush strokes", "layered pigment", "grainy paper texture"),
        "medium": ("oil painting", "watercolor", "ink illustration", "digital painting"),
    },
}

#: ``style.medium`` 的自由文本 → 默认值库的键。按顺序匹配，先命中先用。
_STYLE_ALIASES = (
    ("photograph", "photograph"), ("photo", "photograph"),
    ("manga", "manga"), ("comic", "manga"), ("lineart", "manga"),
    ("line art", "manga"), ("anime", "manga"), ("ink drawing", "manga"),
    ("oil painting", "illustration"), ("painting", "illustration"),
    ("watercolor", "illustration"), ("illustration", "illustration"),
    ("render", "illustration"),
)


def style_key(ir) -> str:
    """IR 的媒介 → 默认值库的键。没有 medium 或认不出就走 ``generic``。"""
    style = getattr(ir, "style", None) or {}
    medium = str(style.get("medium") or "").strip().lower()
    if not medium:
        return "generic"
    for alias, key in _STYLE_ALIASES:
        if alias in medium:
            return key
    return "generic"


def library_entries(style: str, dim: str) -> tuple:
    """该媒介在某维度上的全部合法默认值（风格自己的 + 通用兜底，去重保序）。"""
    out, seen = [], set()
    for key in (style, "generic"):
        for entry in (DEFAULT_LIBRARY.get(key) or {}).get(dim) or ():
            if entry not in seen:
                seen.add(entry)
                out.append(entry)
    return tuple(out)


def traceable_entry(style: str, dim: str, text: str):
    """这段文本里有没有一条**库内**的默认值；返回命中的条目，没有则 None。"""
    low = _norm(text)
    for entry in library_entries(style, dim):
        if _term_in(low, entry):
            return entry
    return None


def library_block(style: str) -> str:
    """给模型看的默认值库（闭集）。模型只能从这里选，判据也只认这里。"""
    lines = ["STYLE DEFAULTS (closed set — fill only from these):"]
    for dim in FILLABLE_DIMS:
        entries = library_entries(style, dim)
        if entries:
            lines.append("- %s: %s" % (dim, "; ".join(entries)))
    return "\n".join(lines)


#: 维度线索词：出现这些词才认为模型**尝试**填了这个槽位（判据只判尝试过的填充）。
DIM_CUES = {
    "setting": ("background", "scene", "setting", "street", "room", "interior",
                "exterior", "city", "landscape", "environment", "surroundings",
                "alley", "forest", "wall", "sky", "backdrop", "behind", "rain",
                "market", "hallway", "rooftop", "classroom", "studio"),
    "camera": ("shot", "lens", "angle", "framing", "frame", "depth of field",
               "focus", "perspective", "close-up", "close up", "wide", "telephoto",
               "macro", "eye level"),
    "lighting": ("light", "lit", "lighting", "shadow", "shadows", "hour", "sun",
                 "sunlight", "backlight", "rim", "highlight", "glow", "overcast",
                 "daylight", "shadows"),
    "material": ("material", "texture", "surface", "fabric", "metal", "wood",
                 "stone", "glass", "asphalt", "grain", "skin", "reflection",
                 "reflective", "cotton", "ink"),
    "medium": ("photograph", "painting", "illustration", "render", "film", "manga",
               "anime", "watercolor", "ink", "digital"),
}

# ── 资产 2：翻译的术语表 ──────────────────────────────────────

#: 中文口语 → 目标方言术语（英文侧给多个可接受写法，任一对上就算命中）。
#: 只收「译法基本唯一」的词；多义词宁可留空（留空只报 warn，不会假判命中）。
GLOSSARY = {
    "雨后": {"dim": "setting", "targets": ("after rain", "after the rain")},
    "黄昏": {"dim": "lighting", "targets": ("dusk", "sunset light")},
    "清晨": {"dim": "lighting", "targets": ("early morning", "daybreak")},
    "夜晚": {"dim": "lighting", "targets": ("night",)},
    "霓虹": {"dim": "lighting", "targets": ("neon",)},
    "招牌": {"dim": "setting", "targets": ("sign", "signage", "storefront sign")},
    "湿沥青": {"dim": "material", "targets": ("wet asphalt", "wet pavement")},
    "水洼": {"dim": "material", "targets": ("puddle",)},
    "倒影": {"dim": "material", "targets": ("reflection", "reflected")},
    "薄雾": {"dim": "lighting", "targets": ("mist", "haze")},
    "小巷": {"dim": "setting", "targets": ("alley", "alleyway", "backstreet")},
    "地铁站": {"dim": "setting", "targets": ("subway station", "metro station")},
    "特写": {"dim": "camera", "targets": ("close-up", "close up", "closeup")},
    "近景": {"dim": "camera", "targets": ("medium shot", "medium close-up")},
    "远景": {"dim": "camera", "targets": ("wide shot", "long shot")},
    "全景": {"dim": "camera", "targets": ("full shot", "wide shot")},
    "仰拍": {"dim": "camera", "targets": ("low angle",)},
    "俯拍": {"dim": "camera", "targets": ("high angle", "overhead view", "top-down")},
    "逆光": {"dim": "lighting", "targets": ("backlight", "backlit")},
    "暖光": {"dim": "lighting", "targets": ("warm light", "warm glow")},
    "冷光": {"dim": "lighting", "targets": ("cool light", "cool shadow")},
    "胶片颗粒": {"dim": "material", "targets": ("film grain", "grain")},
    "黄雨衣": {"dim": "subject", "targets": ("yellow raincoat", "yellow rain jacket")},
    "雨衣": {"dim": "subject", "targets": ("raincoat", "rain jacket")},
    "油画": {"dim": "medium", "targets": ("oil painting",)},
    "水彩": {"dim": "medium", "targets": ("watercolor", "watercolour")},
    "水墨": {"dim": "medium", "targets": ("ink wash", "ink painting")},
    "黑白": {"dim": "material", "targets": ("black and white", "monochrome")},
}


def glossary_block(glossary=None) -> str:
    entries = GLOSSARY if glossary is None else glossary
    lines = ["GLOSSARY (Chinese colloquial → target dialect terms):"]
    for zh in sorted(entries):
        spec = entries[zh]
        lines.append("- %s → %s（%s）" % (zh, " / ".join(spec["targets"]), spec["dim"]))
    return "\n".join(lines)


def applicable_terms(source: str, glossary=None) -> list:
    """源文本里出现的术语（按出现顺序）。**引号内的图内文字不参与**——那是要逐字保留的。

    长词优先并记覆盖区间，避免「霓虹」把「霓虹灯」再算一次，把分母灌大。
    """
    entries = GLOSSARY if glossary is None else glossary
    masked = _mask_quotes(source)
    covered: list = []
    picked: list = []
    for zh in sorted(entries, key=len, reverse=True):
        start = 0
        found = False
        while True:
            i = masked.find(zh, start)
            if i < 0:
                break
            if not any(a <= i < b for a, b in covered):
                covered.append((i, i + len(zh)))
                found = True
            start = i + len(zh)
        if found:
            picked.append(zh)
    picked.sort(key=lambda z: masked.find(z))
    return picked


# ── 小工具 ────────────────────────────────────────────────────

def _term_in(text_low: str, term: str) -> bool:
    """词边界匹配：英文按字母/数字边界，中文按直接子串。"""
    t = _norm(term)
    if not t:
        return False
    if not re.search(r"[a-z0-9]", t):
        return t in text_low
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text_low) is not None


def _mask_quotes(text: str) -> str:
    return _QUOTE.sub(" ", text or "")


def _quoted_strings(text: str) -> list:
    out = []
    for m in _QUOTE.finditer(text or ""):
        q = next((g for g in m.groups() if g), "").strip()
        if q:
            out.append(q)
    return out


def _capitalized_tokens(text: str) -> list:
    """句首之外的大写词：洋文里的专名（人名 / 品牌 / 地名）几乎总这么出现。"""
    out = []
    for m in re.finditer(r"[A-Z][a-z]{2,}", text or ""):
        before = (text or "")[:m.start()].rstrip()
        if not before:
            continue
        if before[-1] in ".!?;:,\"'“”（(»•-–—\n\t":
            continue
        out.append(m.group(0))
    return out


def _has_cue(text: str, dim: str) -> bool:
    low = _norm(text)
    return any(_term_in(low, cue) for cue in DIM_CUES.get(dim, ()))


def _library_vocab(style: str) -> set:
    out = set()
    for key in (style, "generic"):
        for entries in (DEFAULT_LIBRARY.get(key) or {}).values():
            for entry in entries:
                out.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", entry))
    for spec in GLOSSARY.values():
        for t in spec["targets"]:
            out.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", t))
    return out


def _allowed_named(ir, source: str, style: str) -> set:
    """可接受的大写词：源文本里的、IR 里出现过的、默认值库/术语表用到的。"""
    allowed = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", source or "")}
    if ir is not None:
        try:
            blob = json.dumps(ir.to_dict(), ensure_ascii=False).lower()
            allowed |= set(re.findall(r"[a-z][a-z'\-]*", blob))
        except Exception:            # 判据不该因为 IR 脏就整条炸掉
            pass
    allowed |= _library_vocab(style)
    return allowed


def _fact_delta(ir, source: str, output: str, *, target: str = "", counter=None):
    """对比源文本与产物：「这一步**新引入**」的硬错才归给这一步。

    继承下来的错（源文本本来就有）不算这一步的问题——否则每一步都会重复报同一个
    根因，报告又会失去归因能力。
    """
    if ir is None:
        return None
    before = verify(ir, source, target, None, counter=counter)
    after = verify(ir, output, target, None, counter=counter)
    new_hard = [x for x in after.hard_misses if x not in before.hard_misses]
    new_nums = [x for x in after.extra_numbers if x not in before.extra_numbers]
    new_quotes = [x for x in after.extra_quotes if x not in before.extra_quotes]
    return before, after, new_hard, new_nums, new_quotes


def _delta_checks(rep, ir, source: str, output: str, *, target: str, counter, verb: str) -> None:
    delta = _fact_delta(ir, source, output, target=target, counter=counter)
    if not delta:
        return
    _, _, new_hard, new_nums, new_quotes = delta
    if new_hard:
        rep.errors.append("%s这一步改动了固定项：%s（图里的字/年龄/用户指定项，改了就错）"
                          % (verb, "；".join(new_hard[:4])))
    if new_nums:
        rep.errors.append("%s这一步凭空造数：%s" % (verb, ", ".join(new_nums)))
    if new_quotes:
        rep.errors.append("%s这一步凭空造引号文本：%s"
                          % (verb, "；".join(new_quotes[:3])))


def _finish(rep: "StepResult") -> "StepResult":
    if not rep.applicable:
        rep.verdict = "skipped"
    elif rep.errors:
        rep.verdict = "fail"
    elif rep.warns:
        rep.verdict = "warn"
    else:
        rep.verdict = "pass"
    return rep


# ── 判据结果 ──────────────────────────────────────────────────

@dataclass
class StepResult:
    """一步的判据结果。字段命名与 ``expand.Report`` 的 errors/warns 保持同构。"""

    step: str = ""
    enabled: bool = True
    #: 是否真的调了模型（关掉的步、预检就跳过的步都是 False）。
    ran: bool = False
    #: 这条判据对这次输入是否成立。为 False 时**不按通过记**（verdict=skipped），
    #: 并且 note 必须说清为什么——分母为 0 的比率不能算 100%。
    applicable: bool = True
    verdict: str = "pass"
    errors: list = field(default_factory=list)
    warns: list = field(default_factory=list)
    before_words: int = 0
    after_words: int = 0
    metrics: dict = field(default_factory=dict)
    note: str = ""

    @property
    def issues(self) -> list:
        return list(self.errors) + list(self.warns)

    def to_dict(self) -> dict:
        return {"step": self.step,
                "label": STEP_LABELS.get(self.step, self.step),
                "enabled": self.enabled, "ran": self.ran,
                "applicable": self.applicable, "verdict": self.verdict,
                "before_words": self.before_words, "after_words": self.after_words,
                "errors": list(self.errors), "warns": list(self.warns),
                "metrics": dict(self.metrics), "note": self.note}


# ── 三个判据（纯 Python，零模型） ─────────────────────────────

def check_completion(ir, source: str, output: str, *, style: str = "",
                     target: str = "", counter=None, open_dims=None) -> StepResult:
    """补全判据：只准填 OPEN 槽位，且填的每一项都能追溯到该风格的默认值库。"""
    rep = StepResult(step=STEP_COMPLETION, ran=True)
    src, out = source or "", output or ""
    rep.before_words, rep.after_words = words(src), words(out)
    if not out.strip():
        rep.applicable = False
        rep.note = "补全产物为空：保留上一步产物（降级但合法）"
        return _finish(rep)

    style = style or style_key(ir)
    rep.metrics["style"] = style
    if open_dims is None:
        from .clauses import build_clauses

        open_dims = open_slot_dims(build_clauses(ir))
    fillable = [d for d in open_dims if d in FILLABLE_DIMS]
    rep.metrics["open_slots"] = list(open_dims)
    rep.metrics["fillable_slots"] = list(fillable)

    _delta_checks(rep, ir, src, out, target=target, counter=counter, verb="补全")

    # 专名幻觉：名字/品牌/地名是「不可追溯的新细节」里最能客观判的一类
    allowed = _allowed_named(ir, src, style)
    invented = sorted({w for w in _capitalized_tokens(out) if w.lower() not in allowed})
    if invented:
        rep.errors.append("补全造了专名：%s（IR 里没有这个名字/品牌/地点）"
                          % "、".join(invented))
    rep.metrics["invented_names"] = invented

    # 槽位填充可追溯性：只判「真的尝试填了」的槽位（有线索词），没填不罚、只提示
    fills, traceable, untraceable = [], [], []
    for dim in fillable:
        if not _has_cue(out, dim):
            continue
        fills.append(dim)
        entry = traceable_entry(style, dim, out)
        if entry:
            traceable.append([dim, entry])
        else:
            untraceable.append(dim)
    if untraceable:
        options = {d: "；".join(library_entries(style, d)[:5]) for d in untraceable}
        rep.errors.append("补全填了默认值库里没有的内容（不可追溯）：%s。该风格可选：%s"
                          % ("、".join(untraceable),
                             " / ".join("%s→%s" % (d, options[d]) for d in untraceable)))
    unfilled = [d for d in fillable if d not in fills]
    if unfilled:
        rep.warns.append("补全后仍空着的槽位：%s（不算错——宁缺勿编）" % "、".join(unfilled))
    rep.metrics["fill_attempts"] = fills
    rep.metrics["traceable_fills"] = traceable
    rep.metrics["untraceable_fills"] = untraceable

    # 新颜色词：与 verify 对颜色的口径一致，判 warn 不判 error
    new_colors = _new_colors(src, out)
    if new_colors:
        rep.warns.append("补全新加的颜色词：%s（颜色在 verify 里是软项；确认不是凭空指定）"
                         % ", ".join(new_colors))
    rep.metrics["new_colors"] = new_colors
    return _finish(rep)


def check_translation(ir, source: str, output: str, *, glossary=None,
                      target: str = "", counter=None) -> StepResult:
    """翻译判据：术语表命中率 + 图内文字逐字 + 不得引入新事实。"""
    rep = StepResult(step=STEP_TRANSLATION, ran=True)
    src, out = source or "", output or ""
    rep.before_words, rep.after_words = words(src), words(out)
    if not out.strip():
        rep.applicable = False
        rep.note = "翻译产物为空：保留上一步产物（降级但合法）"
        return _finish(rep)

    entries = GLOSSARY if glossary is None else glossary
    terms = applicable_terms(src, entries)
    rep.metrics["applicable_terms"] = list(terms)
    if not terms:
        rep.applicable = False
        rep.note = ("源文本不含术语表里的中文口语术语：翻译步**不适用**"
                    "（分母为 0，不按 100% 命中记）")
        return _finish(rep)

    out_low = _norm(out)
    hits, misses = [], []
    for zh in terms:
        targets = entries[zh]["targets"]
        if any(_term_in(out_low, t) for t in targets):
            hits.append(zh)
        else:
            misses.append((zh, targets))
    rep.metrics["hits"] = hits
    rep.metrics["misses"] = [zh for zh, _ in misses]
    rep.metrics["hit_rate"] = round(len(hits) / float(len(terms)), 3)
    if misses:
        rep.errors.append("未翻译的中文术语：%s（应写成：%s）"
                          % ("、".join(zh for zh, _ in misses),
                             "；".join("%s→%s" % (zh, " / ".join(t)) for zh, t in misses[:4])))

    # 图内文字（引号里）必须逐字保留：翻译步最容易犯的典型错误就是把它也翻了
    changed_quotes = [q for q in _quoted_strings(src)
                      if not _term_in(_norm(out), q)]
    if changed_quotes:
        rep.errors.append("翻译改动了图内文字（引号内容必须逐字保留）：%s"
                          % "；".join(changed_quotes[:3]))

    _delta_checks(rep, ir, src, out, target=target, counter=counter, verb="翻译")

    missed = {zh for zh, _ in misses}
    residual = [s for s in sorted(set(_CJK.findall(_mask_quotes(out))))
                if not any(zh in s for zh in missed)]
    rep.metrics["residual_zh"] = residual
    if residual:
        rep.warns.append("译文里仍有术语表未覆盖的中文字符：%s（无法机检，需人读）"
                         % "、".join(residual[:6]))
    return _finish(rep)


def check_rewrite(ir, source: str, output: str, *, target: str = "",
                  counter=None) -> StepResult:
    """改写判据：事实集合不变 + 词袋基本不变（只许重排）。"""
    rep = StepResult(step=STEP_REWRITE, ran=True)
    src, out = source or "", output or ""
    rep.before_words, rep.after_words = words(src), words(out)
    if not out.strip():
        rep.applicable = False
        rep.note = "改写产物为空：保留上一步产物（降级但合法）"
        return _finish(rep)

    from .decompose import scan

    before, after = scan(src), scan(out)
    lost = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    if lost:
        rep.errors.append("改写在重排中丢了事实：%s" % _fmt_facts(lost))
    if added:
        rep.errors.append("改写在重排中多出了事实：%s" % _fmt_facts(added))
    _delta_checks(rep, ir, src, out, target=target, counter=counter, verb="改写")

    bw = _WORD.findall(src.lower())
    aw = _WORD.findall(out.lower())
    from collections import Counter

    kept = sum((Counter(bw) & Counter(aw)).values())
    retention = kept / float(len(bw)) if bw else 1.0
    rep.metrics["facts_before"] = len(before.keys())
    rep.metrics["facts_equal"] = not lost and not added
    rep.metrics["word_retention"] = round(retention, 3)
    rep.metrics["order_changed"] = bw != aw
    if retention < 0.9:
        rep.warns.append("改写换掉了 %.0f%% 的词（这一步只该重排；若是刻意改写，说明用错了步）"
                         % (100 * (1 - retention)))
    return _finish(rep)


def check(step: str, *, ir, source: str, output: str, target: str = "",
          counter=None, open_dims=None, style: str = "", glossary=None) -> StepResult:
    """按步名分派判据。未知步名报错，不静默走默认。"""
    if step == STEP_COMPLETION:
        return check_completion(ir, source, output, style=style, target=target,
                                counter=counter, open_dims=open_dims)
    if step == STEP_TRANSLATION:
        return check_translation(ir, source, output, glossary=glossary,
                                 target=target, counter=counter)
    if step == STEP_REWRITE:
        return check_rewrite(ir, source, output, target=target, counter=counter)
    raise ValueError("未知的扩写子步骤 %r；可用：%s" % (step, ", ".join(STEP_ORDER)))


def _fmt_facts(keys) -> str:
    return "；".join("[%s] %s" % (k, v) for k, v in keys[:6])


def _new_colors(source: str, output: str) -> list:
    from .decompose import scan

    try:
        return sorted(set(scan(output).colors) - set(scan(source).colors))
    except Exception:              # 颜色是软项，扫不出来不该把整条判据炸掉
        return []


# ── 给模型看的 prompt（每步独立） ─────────────────────────────

_SYSTEM = {
    STEP_COMPLETION: (
        "You are the " + STEP_MARKERS[STEP_COMPLETION] + ".\n"
        "The description you receive is already correct and already renders a valid prompt.\n"
        "Your ONLY job is to fill the slots that are still EMPTY (listed as OPEN SLOTS).\n"
        "Use ONLY the STYLE DEFAULTS listed below: they are the closed set of recognised\n"
        "defaults for this style. A fill that is not in that list will be rejected.\n\n"
        "HARD RULES (any violation makes the output unusable):\n"
        "1. Keep every fact of the current description: same person, same age, same quoted\n"
        "   on-screen text, same colours, same left/right positions, same wording of what is\n"
        "   already stated.\n"
        "2. Do not reorder or rewrite existing sentences; splice new clauses in for the OPEN\n"
        "   slots only.\n"
        "3. Add no new number, no new quoted string, no brand, no named place or person that\n"
        "   is not already in the description.\n"
        "4. Never write what is absent, no quality words, no aspect ratio, no sampling\n"
        "   parameters.\n"
        "5. Commit once per slot: one default per slot, never stack alternatives.\n"
        "6. If a slot has no honest default, leave it empty — an unfilled slot is better\n"
        "   than an invented one.\n"
        "Return only the extended description: no preface, no headings, no lists."
    ),
    STEP_TRANSLATION: (
        "You are the " + STEP_MARKERS[STEP_TRANSLATION] + ".\n"
        "You convert the Chinese colloquial parts of an existing description into the target\n"
        "dialect's terminology and structure. The facts are already correct and must not change.\n\n"
        "HARD RULES:\n"
        "1. Translate Chinese colloquial terms with the GLOSSARY below; never invent a new\n"
        "   term for a fact that is already stated.\n"
        "2. Text inside double quotes is on-screen text: copy it character for character, in\n"
        "   its original language. NEVER translate quoted text.\n"
        "3. Do not add or remove facts, numbers, colours or positions; do not resolve an\n"
        "   ambiguity by inventing detail.\n"
        "4. Keep the clause order of the description: this is not the rewrite step.\n"
        "5. Write the result in English, except for quoted on-screen text.\n"
        "6. Stop when every glossary term found in the draft has been replaced. Do not pad.\n"
        "Return only the converted description."
    ),
    STEP_REWRITE: (
        "You are the " + STEP_MARKERS[STEP_REWRITE] + ".\n"
        "You reorder an existing description so its clauses follow the target dialect's\n"
        "preferred order. You are NOT allowed to change the facts.\n\n"
        "HARD RULES:\n"
        "1. Keep every fact: same counts, same ages, same colours, same quoted on-screen\n"
        "   text, same left/right positions, same stated materials and light.\n"
        "2. Reorder clauses and sentences only. Keep the same words as closely as possible.\n"
        "3. Add no detail; remove no detail; do not merge two facts into a vaguer one.\n"
        "4. No quality words, no aspect ratio, no sampling parameters, no negations.\n"
        "Return only the reordered description."
    ),
}

_ORDER_HINT = {
    STEP_COMPLETION: "Return the full description with only the OPEN slots filled.",
    STEP_TRANSLATION: "Return the description with those Chinese terms replaced by target terms.",
    STEP_REWRITE: "Return the same description reordered to the preferred order.",
}


def build_system(step: str, *, spec: str = "", style: str = "generic",
                 band=None, glossary=None, dim_order=None) -> str:
    """一步的 system prompt。三步各自独立，互不共享正文。"""
    if step not in _SYSTEM:
        raise ValueError("未知的扩写子步骤 %r" % (step,))
    parts = [_SYSTEM[step]]
    if step == STEP_COMPLETION:
        parts.append("STYLE: %s" % style)
        parts.append(library_block(style))
    elif step == STEP_TRANSLATION:
        parts.append(glossary_block(glossary))
    elif step == STEP_REWRITE:
        if dim_order:
            parts.append("PREFERRED CLAUSE ORDER: %s" % " → ".join(dim_order))
    if band:
        parts.append("LENGTH TARGET: %d-%d words (the final dialect band)." % band)
    if spec:
        parts.append("TARGET DIALECT:\n" + spec)
    return "\n\n".join(p for p in parts if p)


def build_user(step: str, *, ir, text: str, open_dims=None, band=None,
               glossary=None, dim_order=None) -> str:
    """一步的 user prompt：只给它这一步需要的信息（账本片段 + 当前描述）。"""
    if step == STEP_COMPLETION:
        fixed = fixed_items(ir) if ir is not None else []
        lines = ["KEEP THESE EXACTLY (fixed facts):"]
        lines += ["- " + f.label() for f in fixed] or ["- (none)"]
        dims = [d for d in (open_dims or ()) if d in FILLABLE_DIMS]
        lines.append("\nOPEN SLOTS (fill only these):")
        lines += ["- %s" % d for d in dims] or ["- (none)"]
        lines.append("\nCURRENT DESCRIPTION:\n" + (text or ""))
        if band:
            lines.append("\nAim for %d-%d words total; do not pad with adjectives." % band)
        lines.append("\n" + _ORDER_HINT[step])
        return "\n".join(lines)
    if step == STEP_TRANSLATION:
        terms = applicable_terms(text, glossary)
        entries = GLOSSARY if glossary is None else glossary
        lines = ["GLOSSARY TERMS FOUND IN THIS DRAFT:"]
        if terms:
            lines += ["- %s → %s" % (zh, " / ".join(entries[zh]["targets"])) for zh in terms]
        else:
            lines.append("- (none — leave the wording as it is)")
        lines.append("\nCURRENT DESCRIPTION:\n" + (text or ""))
        lines.append("\n" + _ORDER_HINT[step])
        return "\n".join(lines)
    if step == STEP_REWRITE:
        lines = ["PREFERRED ORDER: %s" % " → ".join(dim_order or ()),
                 "\nCURRENT DESCRIPTION:\n" + (text or ""),
                 "\n" + _ORDER_HINT[step]]
        return "\n".join(lines)
    raise ValueError("未知的扩写子步骤 %r" % (step,))


# ── 编排：确定性草稿 → 三步 → 每步判据 ──────────────────────

def precheck(step: str, *, ir=None, clauses=None, open_dims=None, text: str = "",
             glossary=None) -> tuple:
    """调模型前的筛子：这一步对当前输入有没有活干。返回 (要不要调, 不调的理由)。

    用处不是省调用，而是**不让一步在没有对象时乱改**：没有 OPEN 槽位还硬扩写、
    源文本没有中文还硬翻译，都只会带来无谓的事实漂移。
    """
    if step == STEP_COMPLETION:
        dims = [d for d in (open_dims or ()) if d in FILLABLE_DIMS]
        if not dims:
            return False, ("没有可补的 OPEN 槽位（subject/text 不在可补维度里），"
                           "跳过调用")
        if not (text or "").strip():
            return False, "上一步产物为空，跳过调用"
    elif step == STEP_TRANSLATION:
        if not applicable_terms(text, glossary):
            return False, ("源文本不含术语表里的中文口语术语，跳过调用"
                           "（不烧一次模型调用，也避免无谓的事实漂移）")
    elif step == STEP_REWRITE:
        if not (text or "").strip():
            return False, "上一步产物为空，跳过调用"
    else:
        raise ValueError("未知的扩写子步骤 %r" % (step,))
    return True, ""


def run_steps(llm, *, ir, base_text: str, clauses=None, target: str = "",
              band=None, spec: str = "", dim_order=None, style: str = "",
              enabled=STEP_ORDER, temperature: float = 0.4, max_tokens: int = 1200,
              clean=None, counter=None, glossary=None, feedback: str = "",
              feedback_steps=(), open_dims=None) -> tuple:
    """跑一遍拆步管线：``确定性草稿 → 启用的各步（按固定顺序）``。

    返回 ``(最终文本, [StepResult, ...])``。``enabled`` 里没有的步也会有一条
    ``enabled=False / verdict="skipped"`` 的结果，这样报告能回答「这一步到底跑没跑」。

    ``feedback`` / ``feedback_steps`` 只用于一次回炉：把失败项回喂给**出问题的那一步**
    （归因的用处就在这里），其它步不受影响。
    """
    from .clauses import build_clauses

    if clauses is None:
        clauses = build_clauses(ir) if ir is not None else []
    if open_dims is None:
        open_dims = open_slot_dims(clauses)
    style = style or style_key(ir)
    enabled_set = set(enabled or ())
    text = base_text or ""
    results: list = []
    feedback_set = set(feedback_steps or ())

    for step in STEP_ORDER:
        if step not in enabled_set:
            results.append(StepResult(
                step=step, enabled=False, ran=False, applicable=False,
                verdict="skipped", note="已关闭：expand_steps 里没有这一步",
                before_words=words(text), after_words=words(text)))
            continue
        ok, why = precheck(step, ir=ir, clauses=clauses, open_dims=open_dims,
                           text=text, glossary=glossary)
        if not ok:
            results.append(StepResult(
                step=step, enabled=True, ran=False, applicable=False,
                verdict="skipped", note=why,
                before_words=words(text), after_words=words(text)))
            continue

        system = build_system(step, spec=spec, style=style, band=band,
                              glossary=glossary, dim_order=dim_order)
        user = build_user(step, ir=ir, text=text, open_dims=open_dims, band=band,
                          glossary=glossary, dim_order=dim_order)
        if feedback and (not feedback_set or step in feedback_set):
            user = user + "\n\n" + feedback
        raw = llm.chat(system, user, temperature=temperature, max_tokens=max_tokens)
        cand = clean(raw) if clean is not None else (raw or "").strip()
        if not cand:
            results.append(StepResult(
                step=step, enabled=True, ran=True, applicable=False,
                verdict="skipped", note="模型返回空：保留上一步产物（降级但合法）",
                before_words=words(text), after_words=words(text)))
            continue
        result = check(step, ir=ir, source=text, output=cand, target=target,
                       counter=counter, open_dims=open_dims, style=style,
                       glossary=glossary)
        result.ran = True
        results.append(result)
        text = cand

    return text, results


def summarize(results: list) -> str:
    """一行摘要：每步是 关 / 跳过 / pass / warn / fail。给 report 备注用。"""
    parts = []
    for r in results:
        label = STEP_LABELS.get(r.step, r.step)
        if not r.enabled:
            parts.append("%s=关" % label)
        elif r.verdict == "skipped":
            parts.append("%s=跳过" % label)
        else:
            parts.append("%s=%s" % (label, r.verdict))
    return "；".join(parts)


def attach_to_report(rep, results: list) -> "Report":
    """把三步结果写进 ``expand.Report``（新字段，旧字段一个不动）。

    写三样：
    - ``steps``：每步摘要（含「关掉的步」），回答「这一步跑没跑」；
    - ``stage``：**问题来自哪一步**（先看 error，再看 warn）；
    - ``stage_issues``：``{步名: [问题]}``。

    步级 error 会以 ``[补全] …`` 的形式升级进整体 errors 并把 verdict 判 fail：
    否则「术语没翻译但事实都在」会被最终 verify 判 pass，报告读起来像没问题。
    """
    rep.steps = [r.to_dict() for r in results]
    rep.stage_issues = {}
    for r in results:
        issues = list(r.errors) + list(r.warns)
        if issues:
            rep.stage_issues[r.step] = issues
    rep.stage = ""
    for r in results:
        if r.errors:
            rep.stage = r.step
            break
    if not rep.stage:
        for r in results:
            if r.warns:
                rep.stage = r.step
                break
    enabled = [STEP_LABELS.get(r.step, r.step) + ("✓" if r.enabled else "✗")
               for r in results]
    rep.stage_note = ("拆步模式：%s；问题按步归属（stage / stage_issues），"
                      "关掉的步不产生判据" % " ".join(enabled))

    for r in results:
        for e in r.errors:
            line = "[%s] %s" % (STEP_LABELS.get(r.step, r.step), e)
            if line not in rep.errors:
                rep.errors.append(line)
        for w in r.warns:
            line = "[%s] %s" % (STEP_LABELS.get(r.step, r.step), w)
            if line not in rep.warns:
                rep.warns.append(line)
    if any(r.errors for r in results):
        rep.verdict = "fail"
    rep.issues = list(rep.errors) + list(rep.warns)
    return rep


__all__ = [
    "DEFAULT_LIBRARY", "DIM_CUES", "FILLABLE_DIMS", "GLOSSARY", "STEP_COMPLETION",
    "STEP_LABELS", "STEP_MARKERS", "STEP_ORDER", "STEP_REWRITE", "STEP_TRANSLATION",
    "StepResult", "applicable_terms", "attach_to_report", "build_system", "build_user",
    "check", "check_completion", "check_rewrite", "check_translation", "library_entries",
    "precheck", "run_steps", "style_key", "summarize", "traceable_entry",
]
