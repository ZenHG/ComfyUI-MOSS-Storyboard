# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""MOSS-Image 后端。

方言要点（来自官方 SKILL.md + MaxKruse preset）：
- 裸散文，一个段落，七维顺序：主体→场景→相机→光照→材质→媒介→文字。
- 首字符即交付物首字符：无前言、无 markdown、无 bullet。
- 禁质量词、禁权重语法、禁坐标、禁采样参数。
- 有放置结构时输出**一行 Layout JSON**，不是散文。
"""

from __future__ import annotations

from .. import layout as layout_mod
from ..clauses import Clause
from ..ir import IR
from .base import Adapter, Realized


class MossImageAdapter(Adapter):
    name = "moss-image"

    def realize(self, ir: IR, profile: str | None = None,
                mode: str = "deterministic", use_layout: bool | None = None) -> Realized:
        if use_layout is None:
            use_layout = ir.needs_layout()
        if use_layout:
            # Layout 是确定性产物：不调 LLM。多跑一次模型只会白烧 10 秒。
            out = Realized(target=self.name, mode=mode)
            out.token_limit = self.token_limit(ir)
            try:
                draft = layout_mod.build_draft(ir, megapixels=_mp(
                    self.registry.sampling(self.name, profile or self.default_profile)))
                compiled = layout_mod.compile_draft(draft)
                out.positive = layout_mod.dumps(compiled)
                out.layout = compiled
                out.token_count = self._count(out.positive)
                out.negative = self._negative(ir)
                out.params = self.registry.sampling(self.name, profile or self.default_profile)
                out.warnings.append("已切 Layout JSON 模式（%dx%d），未调用 LLM"
                                    % (compiled["_w"], compiled["_h"]))
                return out
            except ValueError as exc:
                layout_error = exc        # 退回散文，但把原因留在 warnings 里
        else:
            layout_error = None

        out = super().realize(ir, profile=profile, mode=mode)
        if layout_error is not None:
            out.warnings.append("layout 生成失败，退回散文：%s" % layout_error)
        elif out.mode != "llm" and len(out.positive.split()) < 120:
            lo = (self.band() or (150, 300))[0]
            out.warnings.append(
                "确定性模式只有 %d 词，低于 MOSS 的 %d 词下界；需 --mode llm 扩写"
                % (len(out.positive.split()), lo))
        return out

    def _render(self, ir: IR, clauses: list[Clause], mode: str) -> str:
        text = super()._render(ir, clauses, mode)
        # 官方要求：图内文字必须加引号；这里兜底一次（LLM 常忘）
        for e in ir.text_entities:
            for line in (e.text_content or "").split("\n"):
                line = line.strip()
                if line and line in text and ('"%s"' % line) not in text:
                    text = text.replace(line, '"%s"' % line, 1)
        return text

    def prompt_spec(self, ir: IR) -> str:
        return (
            "TARGET: MOSS-Image.\n"
            "Form: bare prose caption, ONE paragraph, no line breaks, no bullets, no headings.\n"
            "Order the facts: subject and action, then setting and spatial layout, then camera "
            "and framing, then lighting and atmosphere, then materials and texture, then medium "
            "and style, then any visible text.\n"
            "Rules: every person carries an explicit age in years; visible text stays verbatim "
            "in its original language inside double quotes; no quality words (masterpiece, 8k, "
            "ultra detailed); no weights or brackets; no resolution, steps or CFG; write in "
            "English; never state what is absent (no 'no text', 'without', 'there is no').\n"
            "Length: %s, dense and specific." % self.length_hint("150-300 words")
        )


def _mp(params: dict) -> float:
    mp = params.get("mp") or params.get("mp_range")
    if isinstance(mp, list):
        return float(mp[0])
    try:
        return float(mp)
    except (TypeError, ValueError):
        return 1.0
