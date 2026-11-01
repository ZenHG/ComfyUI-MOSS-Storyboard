# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""MiniMax-H3：音视频联合生成的目标方言。

与四个图像后端的根本差别：**它的提示词是一条时间线**。所以这个适配器不覆写
`_render`（那还是「把子句拼成一段话」的思路），而是整个换掉 `realize`。

## 两套骨架，一个后端

- 默认（`--target minimax-h3`）：官方基础模式的三字段时间线，见 `h3.py`。
- 方言档（`--profile six-section`）：全参考六段式，见 `h3_six.py`。

为什么六段式是 profile 而不是独立 target：新增 target 要在 `adapters/__init__.py`
的 `ADAPTERS` 表登记，而两套骨架共用同一套参考标签、时间线语义与机位词表；
做成 profile 让 `minimax-h3` 的默认行为**一个字节都不变**，同时 `--profile`
本来就是「同一个 target 下的方言变体」的现成入口。`generic` 走不了这条路——
它只会把 IR 摊成散文/tag，没有参考标签与六段顺序的概念。

## 为什么 llm 模式要照抄官方模板，而不是自己写一套

`lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA` 是**在固定输入格式上训的**：
system 一段固定说明，user 三行 `resolution/duration/original_prompt`。
换一个字都可能让 LoRA 的效果打折。所以这里的常量是**逐字**照抄
`prompt_template.py`，不「优化」措辞。

好处是：LoRA 有基座时走 LoRA，没基座时走同一个接口的普通模型——因为**接口相同**，
换权重不需要改代码。这也正是「并入提示词工匠」而不是「新做一个工具」的理由。
"""

from __future__ import annotations

from .. import h3, h3_six
from ..ir import IR
from .base import Adapter, Realized

#: 逐字照抄 `lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA/prompt_template.py`。
#: 不许「顺手改通顺」——LoRA 是在这段文字上训练的。
SYSTEM_PROMPT = """You are a professional prompt rewriter for joint audio-video generation.
Rewrite the user's original prompt into one coherent, production-ready multimodal description for the requested output aspect ratio and duration.

Return only these three fields, in this exact order:
integrated_multimodal_description: ...
overall_soundscape: ...
non_diegetic_music: ...

Requirements:
- Expand the visual narrative into clearly numbered shots such as [Shot 1], [Shot 2], and include timestamps for cuts after the first shot when useful.
- Make the number, timing, and pacing of shots appropriate for the requested duration.
- Compose the scene for the requested aspect ratio.
- Preserve the user's intent while adding concrete subjects, appearance, environment, lighting, composition, camera movement, physical motion, and temporal continuity.
- Keep characters, objects, wardrobe, locations, and spatial relationships consistent across shots.
- Describe synchronized diegetic audio in overall_soundscape and external score in non_diegetic_music.
- Do not add explanations, Markdown fences, safety commentary, or fields other than the three requested fields."""

#: 本地口径的补充（不是官方原文）。放在官方说明**之后**，不掺进去。
#: 这几条全部来自铁律里被判过「硬错」的项，模型每犯一条就是一次返工。
LOCAL_ADDENDUM = """
LOCAL RULES (from the production lead's iron rules, measured on real shots):
- Never use negation. Describe what is there, not what is absent.
- Never write camera azimuths (north/south/east/west) or vague "on one side". Use
  reference-object coordinates: "the wall on the frame-left", "the near end running
  into depth".
- Never commit to a count. Write "a few" / "several", not "three" or "four".
- Never write frame counts, fps, resolution or step counts in the prose. They are
  workflow fields, and repeating them creates a second source of truth.
- Dialogue goes entirely inside <d>...</d> with a language tag, e.g.
  <d>[zh]台词</d>. Outside the tag write at most one short physical action.
- Do not write speaking-rate, accent, pace or "say only this line" instructions.
  Control speed by shortening the line or splitting it with commas.
- Dialogue lines are Chinese and must be copied verbatim; the surrounding prose
  of any shot that carries dialogue should be in English."""


def build_messages(prompt: str, resolution: str = "16:9",
                   duration: int = 8) -> list:
    """官方 user 消息：三行，字段名与顺序都不能动。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": ("resolution: %s\n"
                                     "duration: %ds\n"
                                     "original_prompt: %s" % (resolution, duration,
                                                              prompt.strip()))},
    ]


#: 六段式方言档的 profile 名。registry 的 `minimax-h3.dialects.six-section`
#: 是文档侧镜像；这里是代码侧 selector 白名单，两者必须一致（单测钉住）。
SIX_SECTION_PROFILES = ("six-section", "six_section", "h3-six-section")

#: 六段式的 llm 改写契约。与三字段模板同一套思路：先钉字段名与顺序，
#: 再钉参考标签与「不许加字段」。不逐字照抄官方（官方只公开了基础模式模板），
#: 但字段顺序与参考标签口径来自官方全参考格式规范。
SIX_SYSTEM_PROMPT = """You are a professional prompt writer for joint audio-video generation with full reference control.
Rewrite the user's original prompt into one production-ready six-section manuscript.

Return only these six sections, in this exact order, each section starting at column 1:
subject_definitions: ...
summary: ...
retention_analysis: ...
detailed_description: ...
overall_soundscape: ...
non_diegetic_music: ...

Requirements:
- subject_definitions declares every referenced subject before it is used:
  one `<Subject N> <name>: <appearance>` line per subject, one `<Video N>` line for a
  continuation source, and `@图片N <description>` lines for still references.
- summary starts with a bracketed mode tag: `[video continuation]` or `[reference generation]`,
  and states the duration in seconds and the number of shots.
- retention_analysis has one line per referenced subject:
  `<Subject N> (appears in [Shot 1], [Shot 2]): fully_preserved - <what stays identical>`.
- detailed_description contains numbered shots: `[Shot 1] ...`, later shots may start with
  `At MM:SS.mmm,`. Dialogue goes entirely inside `<d>[zh]...</d>`. A project spec block may
  start with the marker line `【项目影像规格】`.
- overall_soundscape describes diegetic audio only; non_diegetic_music is either `N/A` or a
  short description of the external score.
- Keep Chinese content in Chinese; do not translate names, props or dialogue. Do not add
  explanations, Markdown fences, or sections other than the six requested."""


def is_six_section(profile: str | None) -> bool:
    """这个 profile 是不是六段式方言档。"""
    return str(profile or "").strip().lower() in SIX_SECTION_PROFILES


class H3Adapter(Adapter):
    name = "minimax-h3"
    gen_max_tokens = 2048
    gen_temperature = 0.7
    #: 官方 infer.py 的默认解码就是 temperature .7 / top_p .8 / top_k 20 / rep 1.05，
    #: 并且推荐 `--greedy` 做可复现对比。搜索在这里按 §扩写的做法保留，
    #: 因为「几分之三的镜头时间戳不单调」是间歇性失败，靠重采样比靠重写提示词便宜。
    gen_candidates = 3
    #: 不经过子句装箱：H3 没有维度顺序，也没有 token 预算的概念（它的输入是
    #: 一段 2000 token 的描述，真实上限远大于此）。所以 M4 对它没有意义。
    clause_pipeline = False

    # ── 主流程 ──────────────────────────────────────────────
    def realize(self, ir: IR, profile: str | None = None,
                mode: str = "deterministic") -> Realized:
        self._guard(ir)
        if is_six_section(profile):
            return self._realize_six(ir, mode=mode)
        out = Realized(target=self.name, mode=mode)

        if mode == "llm" and self.llm is not None:
            text, rep = self._rewrite(ir)
            out.positive = text
            out.expand = rep
        else:
            out.positive = h3.render(ir)

        out.token_count = self._count(out.positive)
        out.params = self.registry.sampling(self.name, profile or self.default_profile)

        if not (ir.timeline or {}).get("shots"):
            out.warnings.append(
                "这份 IR 没有 timeline：从静态 IR 编出的是**单镜**视频提示词。"
                "要多镜，请在 IR 里写 timeline.shots（见 examples/05-h3-t2va.json）")

        issues = h3.check(out.positive, ir)
        for i in issues:
            prefix = "[H3] ERROR " if i.level == "error" else "[H3] WARN  "
            out.warnings.append(prefix + i.code + " " + i.message)
        out.negative = None        # H3 没有负向通道
        return out

    # ── 六段式方言档（--profile six-section） ────────────────
    def _realize_six(self, ir: IR, mode: str = "deterministic") -> Realized:
        """全参考六段式：默认从 IR 渲染/逐字回放，llm 模式按六段契约改写。

        与基础模式的差别只有骨架与机检器；负向通道、token 预算、clause 装箱
        这些概念对 H3 **本来就不存在**，所以这里也不假装有。

        invariants 的说明：`INV.run()` 里 M6 走的是 `h3.parse`（三字段解析器），
        它验的是基础模式那条路径。六段式的往返由 `h3_six.parse/render` 的专用
        单测覆盖；这里在 warnings 里明说，避免把「invariants 全绿」读成
        「六段式也验过了」。
        """
        out = Realized(target=self.name, mode=mode)
        if mode == "llm" and self.llm is not None:
            text, rep = self._rewrite_six(ir)
            out.positive = text
            out.expand = rep
        else:
            out.positive = h3_six.render(ir)
        out.token_count = self._count(out.positive)

        params = dict(self.registry.sampling(self.name))
        params.update({"dialect": "h3-six-section",
                       "fields": list(h3_six.FIELDS)})
        out.params = params

        if not (ir.timeline or {}).get("shots"):
            out.warnings.append(
                "这份 IR 没有 timeline：六段式会按静态 IR 合成单镜段落。"
                "要多镜，请在 IR 里写 timeline.shots（见 examples/05-h3-t2va.json）")
        out.warnings.append(
            "六段式方言档：体检走 h3_six.check（六段齐序/参考标签/时间戳）；"
            "invariants 的 M6 仍按三字段解析器跑，不代表六段式已验")

        issues = h3_six.check(out.positive, ir)
        for i in issues:
            prefix = "[H3S] ERROR " if i.level == "error" else "[H3S] WARN  "
            out.warnings.append(prefix + i.code + " " + i.message)
        out.negative = None
        return out

    def _rewrite_six(self, ir: IR) -> tuple:
        """六段式的 llm 改写契约：同一套「采样 + 机检择优」策略，换骨架。"""
        tl = ir.timeline or {}
        resolution = str(ir.canvas.get("aspect_ratio") or "16:9")
        duration = int(round(float(tl.get("duration_s") or 8.0)))
        duration = max(4, min(15, duration))
        source = self._source_text(ir)

        pool: list = []
        seen: set = set()
        for _ in range(max(1, int(self.gen_candidates))):
            raw = self.llm.chat(
                SIX_SYSTEM_PROMPT,
                "resolution: %s\nduration: %ds\noriginal_prompt: %s"
                % (resolution, duration, source),
                temperature=self.gen_temperature, max_tokens=self.gen_max_tokens)
            text = self._clean_fields(raw)
            key = " ".join(text.lower().split())
            if not text or key in seen:
                continue
            seen.add(key)
            issues = h3_six.check(text, ir)
            pool.append((text, issues))
            if not any(i.level == "error" for i in issues):
                break
        if not pool:
            return "", {"verdict": "fail",
                        "issues": [{"code": "H3S-EMPTY",
                                    "message": "六段式改写没有拿到任何内容"}]}
        pool.sort(key=lambda p: (sum(1 for i in p[1] if i.level == "error"),
                                 sum(1 for i in p[1] if i.level == "warn")))
        best_text, best_issues = pool[0]
        rep = {"verdict": h3_six.verdict(best_issues), "candidates": len(pool),
               "issues": [i.__dict__ for i in best_issues]}
        return best_text, rep

    # ── llm：官方改写契约 ────────────────────────────────────
    def _rewrite(self, ir: IR) -> tuple:
        tl = ir.timeline or {}
        resolution = str(ir.canvas.get("aspect_ratio") or "16:9")
        duration = int(round(float(tl.get("duration_s") or 8.0)))
        duration = max(4, min(15, duration))          # 官方 choices=range(4,16)

        source = self._source_text(ir)
        system = SYSTEM_PROMPT + "\n" + LOCAL_ADDENDUM
        pool: list = []
        seen: set = set()
        for _ in range(max(1, int(self.gen_candidates))):
            raw = self.llm.chat(
                system,
                "resolution: %s\nduration: %ds\noriginal_prompt: %s"
                % (resolution, duration, source),
                temperature=self.gen_temperature, max_tokens=self.gen_max_tokens)
            text = self._clean_fields(raw)
            key = " ".join(text.lower().split())
            if not text or key in seen:
                continue
            seen.add(key)
            issues = h3.check(text, ir)
            pool.append((text, issues))
            if not any(i.level == "error" for i in issues):
                break

        if not pool:
            return "", {"verdict": "fail", "issues": [{"code": "H3-EMPTY",
                                                       "message": "改写没有拿到任何内容"}]}

        # 择优：先按 error 数，再按 warn 数。**不看长度**——长不等于好。
        pool.sort(key=lambda p: (sum(1 for i in p[1] if i.level == "error"),
                                 sum(1 for i in p[1] if i.level == "warn")))
        best_text, best_issues = pool[0]
        rep = {"verdict": h3.verdict(best_issues), "candidates": len(pool),
               "issues": [i.__dict__ for i in best_issues]}
        return best_text, rep

    def _source_text(self, ir: IR) -> str:
        """把 IR 压成一段「原始意图」喂给改写器。

        为什么不直接给 IR 的 JSON：官方模板要的是**一句话的点子**，不是结构化
        台账。给台账会让模型照着抄字段名，反而写不出时间线。
        """
        bits = []
        for t in h3.takes(ir):
            line = (t.action or t.detail or "").strip()
            if line:
                bits.append(line.rstrip("."))
        if ir.atmosphere:
            bits.append("; ".join(ir.atmosphere[:2]))
        if not bits:
            from ..clauses import describe_entity

            bits = [describe_entity(e, with_anchor=False)
                    for e in ir.entities[:3] if e.label]
        return ". ".join(b for b in bits if b).strip(" .") or "a short scene"

    def _clean_fields(self, raw: str) -> str:
        """只做结构清理：去掉围栏与对话前缀，**不改字段内容**。"""
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            text = text.rsplit("```", 1)[0]
        for prefix in ("Prompt:", "PROMPT:", "Rewritten prompt:"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        return text.strip()

    # ── 方言说明（给通用 LLM 扩写路径用） ────────────────────
    def prompt_spec(self, ir: IR) -> str:
        return ("Output exactly three fields in this order:\n"
                "integrated_multimodal_description: [Shot 1] ... [Shot 2] At MM:SS.mmm, ...\n"
                "overall_soundscape: ...\n"
                "non_diegetic_music: N/A\n"
                "First shot carries no timestamp. Every later shot starts with "
                "`At MM:SS.mmm,` on the same line as its [Shot N] tag. "
                "Keep timestamps strictly increasing and none within 2.5s of the end.")
