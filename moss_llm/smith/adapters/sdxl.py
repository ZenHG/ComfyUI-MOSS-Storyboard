# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""SDXL（legacy）后端：tag 方言 + 77 token **分块**。

方言要点（registry 的 `encoder.chunking: compel/BREAK`）：
- CLIP-L + CLIP-G，每段上限 **77 token**（含 BOS/EOS，实际可用 75）。
- 超了是**静默截断尾部**——不是报错，也不是拒绝生成。所以默认必须按块切。
- tag 形态：逗号分隔，不用句号、不写完整句。
- 有真负向通道（CFG），`constraints.avoid` 能落地。
- 权重语法（`(word:1.2)`）A1111/Comfy 都认，但**工具不发明权重**：给谁加、加多少是
  模型相关的经验值，瞎填只会让人以为调过参。用户自己写就原样透传。

为什么这是原生后端而不是走通用后端：
通用后端会把子句按重要度**砍到** 77 token 以内——不越界，但丢内容。
SDXL 的编码器支持分段，77 是**每块**的上限，不是整段的上限。多出来的该被切出去，
不该被砍掉。这条差异就是「原生」与「通用」的分界。
"""

from __future__ import annotations

from ..clauses import Clause, render_tags
from ..ir import IR
from .base import Adapter, Realized

#: CLIP 的硬上限与其中被起止符占掉的部分。
CLIP_TOKENS = 77
CLIP_OVERHEAD = 2
#: 一块实际能装多少 token。
CHUNK_BUDGET = CLIP_TOKENS - CLIP_OVERHEAD
#: A1111 / ComfyUI 都认的分段标记。
SEP = " BREAK "


class SdxlAdapter(Adapter):
    name = "sdxl"
    dim_order = ("subject", "setting", "camera", "lighting", "material", "medium", "text")
    #: `_render` 拿不到 Realized（基类签名固定），备注先攒这儿，realize 里一次交出。
    _notes: list = []

    # ── 预算：分块后端没有「整段上限」 ──────────────────────
    def token_limit(self, ir: IR) -> int | None:
        """返回 None，让基类**不要**按 77 装箱。

        基类的 `pack()` 是「装不下就丢」。对 SDXL 来说那是错的取舍：77 是每块的上限，
        丢掉的内容本来可以放进第二块。裁剪改由 `_chunk()` 负责（切，不是砍）。
        """
        return None

    # ── 切块 ────────────────────────────────────────────────
    def _chunk(self, clauses: list[Clause]) -> str:
        """按子句边界贪心装块，块间用 BREAK 连接。

        只在**子句边界**切：句子中间断开会让 CLIP 拿到两个半句，
        两个半句都不再是原来的意思——那比丢一条子句更糟。

        单条子句自己就超预算时，它独占一块并出声（我们改不了它，但用户要知道）。
        """
        chunks: list[list[Clause]] = [[]]
        used = 0
        for c in clauses:
            cost = self._count(c.text) + 2          # 逗号 + 空格
            if chunks[-1] and used + cost > CHUNK_BUDGET:
                chunks.append([c])
                used = cost
                continue
            chunks[-1].append(c)
            used += cost
        if not chunks[0]:
            chunks = chunks[1:]

        rendered = []
        for i, chunk in enumerate(chunks):
            text = render_tags(chunk, self.dim_order)
            if self._count(text) > CHUNK_BUDGET:
                self._notes.append(
                    "第 %d 块 %d token 超过 CLIP 的 %d：这条子句无法再切，"
                    "编码器会截断它的尾部" % (i + 1, self._count(text), CHUNK_BUDGET))
            rendered.append(text)
        if len(rendered) > 1:
            self._notes.append("已切成 %d 块（每块 ≤%d token，BREAK 分隔）："
                               "不切的话尾部会被静默截断" % (len(rendered), CHUNK_BUDGET))
        return SEP.join(rendered)

    # ── 渲染 ────────────────────────────────────────────────
    def _render(self, ir: IR, clauses: list[Clause], mode: str) -> str:
        if mode == "llm" and self.llm is not None:
            return self._render_llm(ir, clauses)
        return self._chunk(clauses)

    def realize(self, ir: IR, profile: str | None = None,
                mode: str = "deterministic") -> Realized:
        self._notes = []                # 与 qwen 那个 `_pending_warning` 同一个套路
        out = super().realize(ir, profile=profile, mode=mode)
        for w in self._notes:
            if w not in out.warnings:
                out.warnings.append(w)
        self._notes = []
        return out

    def prompt_spec(self, ir: IR) -> str:
        return (
            "TARGET: SDXL (legacy).\n"
            "Form: comma-separated tags, lowercase, no full sentences, no trailing period.\n"
            "Order: subject first, then setting, camera, light, material, medium, visible text.\n"
            "Hard limit: 75 usable CLIP tokens per segment. Segments are joined with BREAK; "
            "never put more than one subject per segment.\n"
            "Rules: no quality words (masterpiece, 8k, best quality) — they do not constrain "
            "the distribution; no weights unless the caller wrote them; quote visible text "
            "verbatim in its original script."
        )
