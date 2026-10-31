# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""通用后端：完全由 registry 的声明驱动，不写死任何方言。

为什么需要它：
这是开源项目。用户想接 SDXL / Illustrious / Pony / 自家微调的模型时，
不该被迫读 Python 源码、继承一个类、再提 PR。registry 里已经声明了
`mode`（prose/tag）、`language`、`weight_syntax`、`negative`、token 上限，
这些信息足够渲染出一份能用的 prompt。

能力边界（要诚实）：
- 通用后端只做「散文 / tag」两种形态 + 负向 + 预算裁剪。
- 结构化输出（MOSS 的 Layout JSON）、编辑指令、专属采样档，仍然需要原生后端。
  这些不是「少写几行」的问题，是每个模型一套独立语法，猜不出来。
"""

from __future__ import annotations

from ..clauses import Clause, render_prose, render_tags
from ..ir import IR
from .base import Adapter


class GenericAdapter(Adapter):
    """按 registry 声明渲染。名字由构造时传入，不是类属性。"""

    def __init__(self, registry, name: str, counter=None, llm=None):
        self.name = name
        super().__init__(registry, counter=counter, llm=llm)

    # ── 策略 ────────────────────────────────────────────────
    def policy(self) -> dict:
        return self.registry.prompt_policy(self.name) or {}

    @property
    def form(self) -> str:
        return str(self.policy().get("mode") or "prose").lower()

    # ── 渲染 ────────────────────────────────────────────────
    def _render(self, ir: IR, clauses: list[Clause], mode: str) -> str:
        if mode == "llm" and self.llm is not None:
            return self._render_llm(ir, clauses)
        if self.form == "tag":
            return render_tags(clauses, self.dim_order)
        return render_prose(clauses, self.dim_order)

    def prompt_spec(self, ir: IR) -> str:
        p = self.policy()
        display = (self.registry.get(self.name) or {}).get("display") or self.name
        form = ("comma-separated tags, lowercase, no full sentences"
                if self.form == "tag" else "one paragraph of flowing prose")
        verbosity = p.get("verbosity")
        try:
            verbosity = float(verbosity)
        except (TypeError, ValueError):
            verbosity = 0.7
        target_words = int(40 + 210 * max(0.0, min(1.5, verbosity)))
        quality = ("no quality words (masterpiece, 8k, ultra detailed, best quality)"
                   if (p.get("quality_words") or "banned") == "banned" else "")
        weights = ("no weights, no brackets, no emphasis syntax"
                   if (p.get("weight_syntax") or "none") == "none"
                   else "weights allowed as (word:1.2)")
        lines = [
            "TARGET: %s." % display,
            "Form: %s." % form,
            "Order the facts: subject and action, setting, camera, lighting, "
            "materials, medium, then any visible text.",
            "Rules: every person carries an explicit age in years; visible text stays "
            "verbatim in its original language inside double quotes; never state what is "
            "absent (no 'no text', 'without', 'there is no').",
            "Language: English.",
        ]
        if quality:
            lines.append("Style: %s." % quality)
        lines.append("Syntax: %s." % weights)
        lines.append("Length: about %d words." % target_words)
        return "\n".join(lines)


def is_generic(name: str, registry) -> bool:
    """registry 里有、但没有原生后端 → 走通用后端。"""
    from . import ADAPTERS
    return name not in ADAPTERS and name in registry.names()
