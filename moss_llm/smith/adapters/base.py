# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""后端基类：所有目标共用同一条流水线，差异只在 realize 里的方言规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..clauses import Clause, build_clauses, pack, render_prose
from ..ir import IR
from ..registry import Registry

#: `(word:1.2)` / `((word))` / `[word]` → `word`
_WEIGHT_SUB = re.compile(r"\(\(([^()]*)\)\)|\(([^():]*):\s*[\d.]+\)|\[([^\[\]]*)\]")
#: 否定式从句的起手词
_NEG_START = re.compile(r"^(no|not|without|never|none|nothing)\b", re.I)
#: 以逗号/分号引出的否定式从句
_NEG_CLAUSE = re.compile(r"[;,]\s*(?:no|not|without|never|none|nothing)\b[^;,.!?]*", re.I)
#: 模型把系统提示回写进正文（"without any text ... beyond what is specified"）
_META_TAIL = re.compile(
    r"[,\s]+(?:and\s+)?(?:without|with no|free of)\b[^;.!?]{0,160}?"
    r"(?:text|signage|objects|elements|specified|listed|additional)\b[^;.!?]*", re.I)


@dataclass
class Realized:
    target: str
    positive: str = ""
    negative: str | None = None
    layout: dict | None = None
    params: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    token_count: int = 0
    token_limit: int | None = None
    mode: str = "deterministic"
    dropped: list[str] = field(default_factory=list)
    #: 扩写体检报告（只有 llm 模式有）。固定项漏没漏、有没有凭空造细节，都在这。
    expand: dict | None = None

    def as_dict(self) -> dict:
        return {"target": self.target, "positive": self.positive,
                "negative": self.negative, "layout": self.layout,
                "params": self.params, "warnings": self.warnings,
                "token_count": self.token_count, "token_limit": self.token_limit,
                "mode": self.mode, "dropped": self.dropped, "expand": self.expand}


class Adapter:
    name = ""
    gen_max_tokens = 1200
    gen_temperature = 0.4
    #: 扩写采样数。搜索压的是**方差**，不是平均分——同一份 IR 跑十次，
    #: 有时对有时错，最直接的解法是采几个挑最好的，而不是让模型自我纠错。
    #: 命中「全项通过」就提前停，所以成本上界 4×、常见 1–2×。
    gen_candidates = 4
    #: 扩写回炉次数。1 次就够——第二遍还改不对，说明是事实层的问题，重跑模型不解决。
    gen_retries = 1
    dim_order = ("subject", "setting", "camera", "lighting", "material", "medium", "text")
    default_profile: str | None = None
    #: 是否走「子句装箱」这条主流水线。图像后端全是；H3 不是——它没有维度顺序，
    #: 也没有 token 预算的概念。置 False 后 M4（预算只做减法）对它不适用，
    #: 报「不适用」而不是假通过：假通过会让报告读起来像「这条也验过了」。
    clause_pipeline = True
    #: 长度档（brief / standard / detailed），由 CLI 传入；带子在 registry 里。
    length = "standard"

    def __init__(self, registry: Registry, counter=None, llm=None):
        self.registry = registry
        self.counter = counter
        self.llm = llm

    # ── 能力 ────────────────────────────────────────────────
    def caps(self) -> dict:
        return self.registry.get(self.name)

    def _count(self, text: str) -> int:
        if self.counter is None:
            from ..tokens import HeuristicCounter
            self.counter = HeuristicCounter()
        return self.counter.count(text)

    def token_limit(self, ir: IR) -> int | None:
        explicit = (ir.budget or {}).get("target_tokens")
        if explicit:
            return int(explicit)
        return self.registry.max_tokens(self.name)

    def band(self) -> tuple | None:
        """该目标在当前长度档下的词数带。带子在 registry，代码不硬编码。"""
        from ..expand import length_band
        return length_band(self.registry, self.name, self.length)

    def length_hint(self, fallback: str) -> str:
        """给模型看的长度指令。`--length` 必须真的改变这句，否则那个开关是假的。"""
        b = self.band()
        return "%d-%d words" % (b[0], b[1]) if b else fallback

    # ── 主流程 ──────────────────────────────────────────────
    def realize(self, ir: IR, profile: str | None = None,
                mode: str = "deterministic") -> Realized:
        self._guard(ir)
        out = Realized(target=self.name, mode=mode)

        clauses = build_clauses(ir)
        limit = self.token_limit(ir)
        out.token_limit = limit

        kept, dropped = pack(clauses, limit, self.counter or _lazy(self))
        if dropped:
            out.dropped = ["%s:%s" % (c.dim, c.text[:40]) for c in dropped]
            out.warnings.append(
                "预算 %s token，按重要度砍掉 %d 条低权子句" % (limit, len(dropped)))

        out.positive = self._sanitize(ir, self._render(ir, kept, mode), out,
                                      from_llm=(mode == "llm" and self.llm is not None))
        out.token_count = self._count(out.positive)
        rep = getattr(self, "_expand_report", None)
        if rep is not None:
            out.expand = rep.to_dict()
            self._expand_report = None
            out.warnings.extend(getattr(self, "_expand_notes", []) or [])
            self._expand_notes = []
            for issue in rep.issues:
                out.warnings.append("[扩写] " + issue)

        out.negative = self._negative(ir)
        out.params = self.registry.sampling(self.name, profile or self.default_profile)
        return out

    # ── 方言钩子（子类覆写） ────────────────────────────────
    def _sanitize(self, ir: IR, text: str, out: Realized, from_llm: bool = False) -> str:
        """编译器该做的清理，不留给用户。

        只做**结构性**清理（权重语法），不动语义（质量词留给 lint 判 error）。
        理由：删括号是修语法；删 "masterpiece" 是改意图，必须让人看见。

        否定式裁剪只在 LLM 产物上做：确定性渲染的词来自 IR，
        里面可能有合法的 "no backing panel" 这类排版描述，不能误删。
        """
        if text.lstrip().startswith("{"):
            return text
        caps = self.caps().get("prompt") or {}
        if (caps.get("weight_syntax") or "none") == "none":
            cleaned = _WEIGHT_SUB.sub(
                lambda m: m.group(1) or m.group(2) or m.group(3) or "", text)
            if cleaned != text:
                out.warnings.append("已剥离权重语法（%s 不支持）" % self.name)
                text = cleaned
        if from_llm:
            text = self._strip_negation_tail(text, out)
        return text

    def _guard(self, ir: IR) -> None:
        edit = self.caps().get("edit") or {}
        if ir.task in ("edit", "inpaint", "outpaint") and not edit.get("supported"):
            raise ValueError("%s 不支持 %s；换支持编辑的目标，或退回 t2i" % (self.name, ir.task))

    def _render(self, ir: IR, clauses: list[Clause], mode: str) -> str:
        if mode == "llm" and self.llm is not None:
            return self._render_llm(ir, clauses)
        return render_prose(clauses, self.dim_order)

    def _render_llm(self, ir: IR, clauses: list[Clause]) -> str:
        """扩写 = 账本 + **N 次采样** + 体检择优 + 一次回炉。

        为什么不是「把事实丢给模型让它写长一点」：
        - 模型不知道哪些是**不能动的**（数字、引号里的字、说过的颜色），必须写进账本；
        - 模型不知道这个目标要**多长**，长度是方言参数，从 registry 读；
        - 模型会为了凑字数造细节，所以写完必须**逐条对账**。

        为什么要采样多次：8B 本地模型的失败是**间歇性**的——同一份账本，
        这次改了年龄，下次没改。平均质量靠改 prompt，**方差只能靠搜索**。
        采到「全项通过」立刻停，所以多数情况只多花一次调用。

        回炉仍然只做一次：第二遍还错，说明是事实层（IR）的问题，重跑模型不解决。
        """
        from ..expand import EXPAND_SYSTEM, ledger_user, verify, length_band, rank

        band = length_band(self.registry, self.name, self.length)
        spec = self.prompt_spec(ir)
        system = EXPAND_SYSTEM + ("\nTARGET DIALECT:\n" + spec if spec else "")
        user = ledger_user(ir, clauses, band)

        pool: list = []          # [(text, Report)]
        seen: set = set()
        for _ in range(max(1, int(self.gen_candidates))):
            raw = self.llm.chat(system, user, temperature=self.gen_temperature,
                                max_tokens=self.gen_max_tokens)
            text = self._clean(raw)
            key = " ".join(text.lower().split())
            if not text or key in seen:
                continue
            seen.add(key)
            rep = verify(ir, text, self.name, self.registry,
                         counter=self.counter, band=band)
            pool.append((text, rep))
            # 硬错为零且落在长度带内，就没有更好的了，剩下的采样是纯浪费。
            # 不看软警告：一条「用了 very」不值得再多花三次调用。
            if rep.score()[0] == 0 and rep.score()[1] == 0:
                break

        if not pool:
            # 模型什么都没给：交空字符串，让上层按失败处理，而不是假装成功
            empty = verify(ir, "", self.name, self.registry,
                           counter=self.counter, band=band)
            self._expand_report = empty
            self._expand_notes = ["扩写没有拿到任何内容（模型返回空）"]
            return ""

        best_text, best_rep = pool[rank([r for _, r in pool])]

        for _ in range(max(0, self.gen_retries)):
            if best_rep.verdict != "fail":
                break
            raw = self.llm.chat(system, user + "\n\n" + best_rep.feedback(),
                                temperature=max(0.1, self.gen_temperature - 0.15),
                                max_tokens=self.gen_max_tokens)
            cand = self._clean(raw)
            if not cand:
                break
            cand_rep = verify(ir, cand, self.name, self.registry,
                              counter=self.counter, band=band)
            # 只在「分层键确实更小」时替换。不能只比 issue 条数：
            # 一条造字和三条无关警告不是同一个量级的错。
            if cand_rep.score() < best_rep.score():
                best_text, best_rep = cand, cand_rep
            if best_rep.verdict != "fail":
                break

        best_rep.candidates = len(pool)
        self._expand_report = best_rep
        notes = []
        if len(pool) > 1:
            notes.append("扩写采样 %d 份，取其中分层键 %s 最好的一份"
                         % (len(pool), best_rep.score()))
        elif self.gen_candidates > 1 and len(seen) == 1:
            notes.append("扩写采样 %d 次只得到同一份结果（温度太低或服务端缓存），"
                         "搜索没有收益" % self.gen_candidates)
        self._expand_notes = notes
        return best_text

    def _clean(self, raw: str) -> str:
        text = (raw or "").strip()
        for fence in ("```\n", "```"):
            if text.startswith(fence):
                text = text[len(fence):]
        text = text.strip().strip("`").strip()
        # 去掉模型爱加的前缀标签
        for prefix in ("Prompt:", "PROMPT:", "Caption:", "CAPTION:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        return " ".join(text.split())

    def _strip_negation_tail(self, text: str, out: "Realized") -> str:
        """裁掉散文里的否定式从句。

        实测：模型会在结尾或中段补一句 "no text visible" / "; no text is visible;"。
        这既违反「不写否定式」，又把不存在的东西写进了条件里。确定性裁掉，不重跑模型。
        """
        if not text or text.lstrip().startswith("{"):
            return text
        changed = False

        # ① 整句都是否定式 → 丢掉
        sentences, kept = re.split(r"(?<=[.!?])\s+", text), []
        for s in sentences:
            if _NEG_START.match(s.strip()) and len(s.split()) <= 12:
                changed = True
                continue
            kept.append(s)
        text = " ".join(kept)

        # ② 句中/句尾的否定式从句 → 删掉（只在短从句上动手，避免误删主句）
        def _repl(m: re.Match) -> str:
            nonlocal changed
            seg = m.group(0)
            if len(seg.split()) <= 14:
                changed = True
                return ""
            return seg

        text = _NEG_CLAUSE.sub(_repl, text)

        # ②b 模型把系统提示回写进正文（实测：Krea 2 结尾补 "without any text, signage,
        #     or background elements beyond what is specified"）
        def _repl_meta(m: re.Match) -> str:
            nonlocal changed
            seg = m.group(0)
            if len(seg.split()) <= 22:
                changed = True
                return ""
            return seg

        text = _META_TAIL.sub(_repl_meta, text)

        # ③ 修补标点
        text = re.sub(r"\s*[,;]\s*(?=[.;,])", "", text)
        text = re.sub(r"[;,]\s*\.", ".", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if changed:
            out.warnings.append("已裁掉否定式/指令回写从句（模型补的「没有 X」）")
        return text

    def _negative(self, ir: IR) -> str | None:
        policy = self.caps().get("negative") or {}
        avoid = [a for a in (ir.constraints.get("avoid") or []) if a]
        if not policy.get("supported"):
            return None
        if avoid:
            return ", ".join(avoid)
        return policy.get("default") or None

    def prompt_spec(self, ir: IR) -> str:
        """给 LLM 的目标方言说明。子类覆写。"""
        return ""


def _lazy(adapter: Adapter):
    from ..tokens import HeuristicCounter
    adapter.counter = HeuristicCounter()
    return adapter.counter
