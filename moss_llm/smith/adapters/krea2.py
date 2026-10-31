# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Krea 2 后端。

方言要点（krea-ai/krea-2 docs/prompting.md + docs/expansion.txt）：
- 自然语言长句，**媒介词开头**（"long detailed prompts yield best results"）。
- 编码器 Qwen3-VL-4B，`max_length=512` 且 `truncation=True` → **超了静默截断**，不报错。
- 无 negative 参数（只有 CFG），无 layout，开源版不支持 i2i / edit。
- expansion.txt 明令 Avoid Over-Specification、Preserve User Medium、文字加引号。

本后端的核心价值不是「写得更长」，是**在 512 内按重要度取舍**。
按位置截断会砍掉句尾的媒介/光照/文字——恰恰是决定画面质量的部分。
"""

from __future__ import annotations

from ..clauses import Clause, render_prose
from ..ir import IR
from .base import Adapter, Realized

#: 媒介必须打头，其余按常规七维
KREA_ORDER = ("medium", "subject", "setting", "camera", "lighting", "material", "text")


class Krea2Adapter(Adapter):
    name = "krea2"
    dim_order = KREA_ORDER
    default_profile = "raw"          # 质量档；Turbo 需显式 --profile turbo

    def realize(self, ir: IR, profile: str | None = None,
                mode: str = "deterministic") -> Realized:
        out = super().realize(ir, profile=profile, mode=mode)
        limit = out.token_limit or 512
        if out.token_count > limit * 0.95:
            # 走到这里说明子句已经砍到不能再砍，但仍超预算：必须出声
            out.warnings.append(
                "prompt %d token 逼近/超过 Krea 2 的 %d 硬上限，编码器会**静默截断**；"
                "请降低 IR 细节或提高 importance 门槛" % (out.token_count, limit))
        return out

    def _render(self, ir: IR, clauses: list[Clause], mode: str) -> str:
        if mode == "llm" and self.llm is not None:
            return self._render_llm(ir, clauses)
        # 观察散文一律压到末尾：Krea 要「媒介词开头」，一句三行的散文打头会把
        # 媒介和主体挤到 512 token 之后，被静默截断。
        facts = [c for c in clauses if c.source == "fact"]
        notes = [c for c in clauses if c.source == "note"]
        head = render_prose(facts, KREA_ORDER)
        tail = render_prose(notes, KREA_ORDER)
        if head and tail:
            text = head.rstrip(".") + ". " + tail
        else:
            text = head or tail
        # expansion.txt 要求文字加引号；LLM 常忘，这里兜底。
        # 先看整块是否已经带引号：`the text "一杯温暖\n一刻从容"` 已经是带引号的，
        # 再按行补引号会得到 `""一杯温暖" "一刻从容""` —— 双引号把一块字拆成两块（实测踩过）。
        # 比对用空白折叠后的正文：Clause 会把换行压成空格，原文里的 \n 直接比会漏判。
        for e in ir.text_entities:
            body = e.text_content or ""
            if not body.strip() or ('"%s"' % " ".join(body.split())) in text:
                continue
            for line in body.split("\n"):
                line = line.strip()
                if line and line in text and ('"%s"' % line) not in text:
                    text = text.replace(line, '"%s"' % line, 1)
        return text

    def prompt_spec(self, ir: IR) -> str:
        return (
            "TARGET: Krea 2.\n"
            "Form: natural-language English prose, ONE paragraph, no headings, no bullets, "
            "no tags.\n"
            "Open with the medium (photograph, oil painting, anime screencap, 3D render), then "
            "subject and action, setting and spatial layout, light, material, lens and "
            "composition.\n"
            "Rules: preserve the medium the facts specify and never substitute another one; "
            "quote visible text exactly; avoid over-specification (do not invent micro-detail "
            "that the facts do not support); no quality words; no weights; no resolution or "
            "sampling numbers; never state what is absent.\n"
            "Hard limit: the encoder truncates at 512 tokens. Front-load what matters most; "
            "aim for %s." % self.length_hint("250-380 words")
        )
