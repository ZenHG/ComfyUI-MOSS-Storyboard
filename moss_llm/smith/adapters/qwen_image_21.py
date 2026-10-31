# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Qwen-Image 2.1 后端。

方言要点（来自官方 PE-T2I / PE-I2I 的 system_prompt.txt）：
- 英文长散文，约 20 句 / 400-500 词，单段。
- 第三人称现在时观察者语气：不用 you，不用 create / make sure。
- 开篇一句 = 媒介 + 风格 + 主体 + 背景；中段 8-14 个方位短语；光照单独一句；结尾一句总括构图。
- 质量词被明令禁止；比例/分辨率不得写进正文。
- 编辑：指令式、动词开头、单段；保持项只按类型/位置点名，**不描述其外观**（越描述越漂移）。
- 多图强制 <image1>/<image2> 标签；单图禁用标签。
"""

from __future__ import annotations

from ..clauses import Clause
from ..ir import IR
from .base import Adapter, Realized


class QwenImage21Adapter(Adapter):
    name = "qwen-image-2_1"
    gen_max_tokens = 1600          # 400-500 词英文散文 ≈ 700-900 token，留足余量

    def _render(self, ir: IR, clauses: list[Clause], mode: str) -> str:
        if ir.task in ("edit", "inpaint", "outpaint"):
            return self._render_edit(ir)
        text = super()._render(ir, clauses, mode)
        if mode != "llm":
            words = len(text.split())
            if words < 300:
                # 不假装达标：确定性模式不扩写，明确告知需要 LLM
                lo = (self.band() or (150, 450))[0]
                self._pending_warning = (
                    "确定性模式只有 %d 词，低于 Qwen 2.1 的 %d 词下界；"
                    "需 --mode llm 扩写" % (words, lo))
        return text

    def realize(self, ir: IR, profile: str | None = None, mode: str = "deterministic") -> Realized:
        self._pending_warning = None
        out = super().realize(ir, profile=profile, mode=mode)
        if getattr(self, "_pending_warning", None):
            out.warnings.append(self._pending_warning)
        if ir.task in ("edit", "inpaint", "outpaint"):
            refs = (ir.provenance.get("source_images") or [])
            if len(refs) > 1 and "<image1>" not in out.positive:
                out.warnings.append("多图编辑必须用 <image1>/<image2> 标签，当前未标注")
            if len(refs) == 1 and "<image" in out.positive:
                out.warnings.append("单图编辑禁用 <imageN> 标签")
        return out

    def _render_edit(self, ir: IR) -> str:
        instruction = (ir.constraints.get("instruction")
                       or ir.provenance.get("notes") or "").strip()
        if not instruction:
            raise ValueError(
                "编辑任务必须在 constraints.instruction 里给出编辑意图（动词开头的一句话）")
        keep = [k for e in ir.entities for k in (e.must_keep or [])]
        # 去重保序
        seen, keeps = set(), []
        for k in keep:
            if k.lower() not in seen:
                seen.add(k.lower())
                keeps.append(k)

        parts = [instruction.rstrip(".") + "."]
        if keeps:
            parts.append("Keep the %s unchanged." % _join(keeps))
        if (ir.style.get("medium") or "").startswith("photograph"):
            parts.append("Preserve the original lighting direction and grain.")
        text = " ".join(parts)
        if "\n" in text:
            text = " ".join(text.split())
        return text

    def prompt_spec(self, ir: IR) -> str:
        if ir.task in ("edit", "inpaint", "outpaint"):
            return (
                "TARGET: Qwen-Image 2.1 (edit).\n"
                "Form: one imperative instruction, verb first, single paragraph, no line breaks, "
                "no ellipsis.\n"
                "Rules: state what to change; for what must stay, name it only by type or "
                "position and DO NOT describe its appearance (describing it makes it drift); "
                "no quality words; write in English."
            )
        return (
            "TARGET: Qwen-Image 2.1 (text-to-image).\n"
            "Form: English prose, ONE paragraph, about %d words. Never pad to hit a length: "
            "stop when the listed facts are fully described.\n"
            "Voice: third-person present-tense observer. Never use 'you'. Never use the words "
            "'create', 'make sure', 'generate'.\n"
            "Structure: the first sentence states medium, style, subject and background; the "
            "middle carries positional phrases (upper-left, across the top, in the lower "
            "third, behind, tucked into the corner); lighting gets its own sentence; the last "
            "sentence summarises the composition.\n"
            "Rules: visible text stays verbatim in its original language inside double quotes, "
            "with weight, colour and size; no quality words (masterpiece, 8K, highly detailed); "
            "no aspect ratio, resolution, steps or CFG."
            % self._target_words(ir)
        )

    def _target_words(self, ir: IR) -> int:
        """按 IR 的信息量在长度带内取位。空 IR 硬凑 450 词，只会逼出幻觉。

        带子（--length）给上下界，IR 的信息量给位置：事实少就落在下沿，
        事实多才往上走。两者缺一不可——只有带子会逼模型注水，只有信息量会无视方言。
        """
        from ..clauses import build_clauses
        lo, hi = self.band() or (150, 450)
        base = sum(len(c.text.split()) for c in build_clauses(ir))
        return max(lo, min(hi, int(base * 3)))


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
