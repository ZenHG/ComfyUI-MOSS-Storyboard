# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""FLUX 3 Image 后端。

方言要点（docs.bfl.ai/guides/prompting_unified_reference + flux3_image_bounding_boxes）：
- **短**。模型自己扩写（9B prompt upsampler），你只写「想控制的东西」：位置、外观、光、文字。
  写长 = 过度指定 = 构图僵化。这是与 Krea 2 / Qwen 2.1 完全相反的方向。
- 无 negative_prompt / seed / guidance 字段；未知字段直接 422。
- 布局是一等公民：caption 里用 `<silhouette_n>` 引用，后接 `{"id","bbox","desc"}` 行；
  bbox = `[top,left,bottom,right]`，**0–1000 网格**，与像素无关。
- 编辑：`Modify <ref_image_0> ... Keep the rest of the image exactly unchanged`；
  `tgt_bbox=null` 表示删除。
"""

from __future__ import annotations

import json

from ..clauses import Clause, capitalize_sentences, describe_entity, describe_text
from ..ir import IR
from .base import Adapter, Realized

#: 只保留「值得控制」的维度。相机/材质交给模型自己扩写。
FLUX3_DIMS = ("subject", "setting", "lighting", "text")


class Flux3Adapter(Adapter):
    name = "flux3"
    default_profile = "distilled"
    gen_max_tokens = 260           # 短就是目的，别让模型写长

    def realize(self, ir: IR, profile: str | None = None,
                mode: str = "deterministic") -> Realized:
        if ir.task in ("edit", "inpaint", "outpaint"):
            self._guard(ir)
            out = Realized(target=self.name, mode=mode)
            out.token_limit = self.token_limit(ir)
            out.positive = self._render_edit(ir)
            out.token_count = self._count(out.positive)
            out.negative = self._negative(ir)
            out.params = self.registry.sampling(self.name, profile or self.default_profile)
            return out

        out = super().realize(ir, profile=profile, mode=mode)
        if ir.needs_layout() and not out.positive.lstrip().startswith("{"):
            try:
                out.positive = self._with_bbox_table(ir, out.positive)
                out.token_count = self._count(out.positive)
                out.layout = {"_w": None, "_h": None,
                              "bbox_coord_space": "0-1000",
                              "bbox_format": "tlbr"}
                out.warnings.append("已追加 FLUX 3 bbox 表（0-1000 网格）")
            except ValueError as exc:
                out.warnings.append("bbox 表生成失败，退回纯散文：%s" % exc)
        if len(out.positive.split()) > 140:
            out.warnings.append(
                "FLUX 3 偏好短描述（模型自己扩写），当前 %d 词偏长；"
                "可降 importance 门槛或精简 IR" % len(out.positive.split()))
        return out

    # ── 散文 ────────────────────────────────────────────────
    def _render(self, ir: IR, clauses: list[Clause], mode: str) -> str:
        if mode == "llm" and self.llm is not None:
            return self._render_llm(ir, clauses)
        dims = FLUX3_DIMS if not ir.needs_layout() else tuple(d for d in FLUX3_DIMS if d != "text")
        keep = [c for c in clauses if c.dim in dims and c.importance >= 0.55]
        if not keep:                                   # 兜底：至少留主体
            keep = [c for c in clauses if c.dim == "subject"][:2]
        sentences = []
        for dim in dims:
            texts = [c.text for c in keep if c.dim == dim]
            if texts:
                sentences.append(", ".join(texts))
        text = ". ".join(s.strip().rstrip(".") for s in sentences if s.strip())
        return (capitalize_sentences(text) + ".") if text else ""

    def _with_bbox_table(self, ir: IR, caption: str) -> str:
        """caption + `<silhouette_n>` 引用 + 每项一行 JSON。

        两个坑，都踩过：
        ① id 必须**连续**且与引用一一对应。以前用 `enumerate(ir.entities)` 编号，
           中间夹一个没有 bbox 的实体，id 就变成 s1、s3，而 caption 仍然写
           `<silhouette_1>, <silhouette_2>`——引用了不存在的 id，FLUX 3 只会忽略它。
        ② 编号不能依赖实体数组的顺序。按 (top, left, desc) 排一遍再编号，
           同一份 IR 换数组顺序得到同一份 bbox 表。
        """
        cands = []
        for e in ir.entities:
            bb = e.spatial.get("bbox_norm")
            if not bb:
                continue
            x0, y0, x1, y1 = bb
            top, left = round(y0 * 1000), round(x0 * 1000)
            bottom, right = round(y1 * 1000), round(x1 * 1000)
            if not (0 <= top < bottom <= 1000 and 0 <= left < right <= 1000):
                raise ValueError("bbox_norm 非法：%r" % (bb,))
            cands.append((top, left, bottom, right, e))
        cands.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4].id))

        items, rows = [], []
        for i, (top, left, bottom, right, e) in enumerate(cands, start=1):
            sid = "s%d" % i
            item = {"id": sid, "bbox": [top, left, bottom, right]}
            if e.kind == "text":
                item["desc"] = describe_text(e, with_anchor=False)
                # 逐字文字单独成键：模型才知道要写什么，lint 也能逐字对账
                item["text"] = (e.text_content or "").replace("\n", " ")
            else:
                item["desc"] = describe_entity(e, with_anchor=False)
            items.append(item)
            rows.append((sid, item["desc"]))

        if not items:
            raise ValueError("没有带 bbox_norm 的实体")

        # caption 只引用 silhouette id，描述放在 JSON 行里（这是 FLUX 3 的协议形状）
        refs = ", ".join("<silhouette_%d>" % i for i in range(1, len(items) + 1))
        head = caption.rstrip(".")
        body = "%s. Elements: %s." % (head, refs) if head else "Elements: %s." % refs
        lines = [body] + [json.dumps(it, ensure_ascii=False, separators=(",", ":"))
                          for it in items]
        return "\n".join(lines)

    # ── 编辑 ────────────────────────────────────────────────
    def _render_edit(self, ir: IR) -> str:
        instruction = (ir.constraints.get("instruction")
                       or ir.provenance.get("notes") or "").strip()
        if not instruction:
            raise ValueError(
                "编辑任务必须在 constraints.instruction 里给出编辑意图")
        keep = [k for e in ir.entities for k in (e.must_keep or [])]
        seen, keeps = set(), []
        for k in keep:
            if k.lower() not in seen:
                seen.add(k.lower())
                keeps.append(k)
        refs = ir.provenance.get("source_images") or []
        tag = "<ref_image_0>" if refs else "the image"
        body = "Modify %s by %s." % (tag, instruction[:1].lower() + instruction[1:])
        if keeps:
            body += " Keep the rest of the image exactly unchanged, preserving the %s." % _join(keeps)
        else:
            body += " Keep the rest of the image exactly unchanged."
        return body

    def prompt_spec(self, ir: IR) -> str:
        if ir.task in ("edit", "inpaint", "outpaint"):
            return (
                "TARGET: FLUX 3 Image (edit).\n"
                "Form: one short imperative instruction, single paragraph.\n"
                "Rules: name the region to change and the change; end with 'Keep the rest of "
                "the image exactly unchanged'; do not describe what stays; no quality words."
            )
        return (
            "TARGET: FLUX 3 Image.\n"
            "Form: 2-4 short sentences, total %s. Each sentence under 22 words.\n"
            % self.length_hint("40-90 words") +
            "Only state what you want to control: subject, position of each element, lighting "
            "intent, and any visible text in quotes.\n"
            "Rules: the model expands the rest by itself, so do NOT add camera settings, lens "
            "numbers, material micro-detail, or atmosphere lists; do not turn the answer into a "
            "comma-separated inventory; no quality words; no negations; never describe what is "
            "absent or what you were told to avoid."
        )


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
