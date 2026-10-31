# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""忠实度审计：拿「图 + 编译出来的 prompt」回问一次，找出漏写与编造。

为什么必须做这一步：
反推的上游是 VLM，下游是另一套模型。VLM 漏掉的东西，下游永远不会补；
VLM 编出来的东西，下游会**照着画**。没有图像生成器时，唯一能闭环的办法是
把 prompt 念给同一双眼睛听，问它「这跟你看到的一样吗」。

补丁策略刻意保守：
- 漏写 → 加成低重要度实体（可被预算裁掉，不会污染主体）。
- 编造 → 只删 importance < 0.7 的实体，或删掉命中的那条 note。
  主角永远不因模型一句话被删——那是把判断权交给了可能看错的模型。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ir import Entity, IR
from .reverse import Report, _as_list, extract_json
from .vision import prepare

SYSTEM = """You are a fidelity auditor. You are given an image and a prompt that claims to \
describe it. You must find where the prompt and the image disagree.

Work in three steps, then output ONE JSON object. No markdown, no commentary.

Step 1 — extract up to 8 load-bearing claims from the prompt. A claim is a concrete \
assertion about what is in the picture: a subject, an object, a pose, a setting, a light \
direction, a visible word. Skip filler.
Step 2 — for each claim, look at the image and mark it present or absent. Be literal: if \
the prompt says "a man in a grey t-shirt" and the image shows a woman, the claim is absent.
Step 3 — list what a viewer would notice in the image that the prompt never mentions.

Output schema:

{
  "claims": [{"claim": "the prompt's own words, short", "present": true, "why": "what you see"}],
  "missing": ["concrete visible things the prompt never mentions"],
  "score": 0-100
}

Rules:
1. score = round(100 * present_claims / total_claims). If the prompt describes a different \
image, every claim is absent and the score is near 0.
2. An all-empty audit is invalid output. If the prompt is wrong, say which claims are wrong.
3. "missing" is for things a viewer would notice if removed. Ignore invisible trivia.
4. Quote the prompt's own words in "claim"; do not paraphrase.
5. Do not flag style or mood as wrong unless the image clearly contradicts it.
6. A prompt shorter than the image is not automatically wrong; only flag real omissions.
7. Output the JSON object only."""

USER = ("IMAGE and PROMPT are below. Audit the prompt against the image.\n\n"
        "PROMPT:\n%s")

#: 审计输出的结构约束。
#: `claims` 的 minItems=1 是刻意的：空审计（"什么都对"）是这个环节最危险的失败模式，
#: 用语法把「一条都没查」这条路堵掉。模型想交白卷就得先编一条 claim，
#: 而编出来的 claim 会被 present 判定和分数计算暴露。
AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "present": {"type": "boolean"},
                    "why": {"type": "string"},
                },
                "required": ["claim", "present"],
            },
        },
        "missing": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "integer"},
    },
    "required": ["claims", "missing"],
}


@dataclass
class Audit:
    score: int = 0
    missing: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    raw: str = ""
    raw_chars: int = 0

    def to_dict(self) -> dict:
        return {"score": self.score, "missing": self.missing,
                "wrong": self.wrong, "ok": self.ok, "claims": self.claims,
                "degraded": self.degraded}

    @property
    def degraded(self) -> bool:
        """空审计。模型什么都没查就交卷——这比不审更危险，必须显式标出。

        实测：给一个完全错配的 prompt，8B 直接返回
        {"score":0,"missing":[],"wrong":[],"ok":[]}，看着像「审完了没问题」。
        """
        return not (self.missing or self.wrong or self.ok or self.claims)

    @property
    def verdict(self) -> str:
        if self.degraded:
            return "degraded"
        if self.score >= 85:
            return "pass"
        return "repair" if self.score >= 55 else "fail"


#: 审计意见里混进来的「元评论」——它在评价 prompt，不是在描述画面。
_META = re.compile(
    r"\b(the prompt|prompt mentions|prompt says|prompt describes|"
    r"but the image (shows|has|does)|does not mention|is missing from the prompt|"
    r"not mentioned in the prompt)\b", re.I)


def is_meta_comment(claim: str) -> bool:
    """判定一条审计文字是不是在谈论 prompt 本身。

    实测：模型会把 "The prompt mentions 'top and bottom margins' but the image
    shows no visible..." 塞进 missing。这条被当成实体后，画面描述里就出现了
    一句评价提示词的话——比漏项本身更糟。
    """
    return bool(_META.search(claim or ""))


def score_audit(claims: list[dict], missing: list[str],
                model_score: int | None = None) -> int:
    """把「claims 命中率」和「漏项」合成一个分数。

    只算 claims 会自相矛盾：实测一张 UI 截图，模型列的 5 条 claims 全对，
    但它完全没提标题和标签——打分 100。漏项必须扣分。
    """
    if claims:
        base = 100.0 * sum(1 for c in claims if c.get("present")) / len(claims)
    else:
        base = 0.0
    score = int(round(base - min(40.0, 12.0 * len(missing))))
    if model_score is not None and not missing and claims:
        score = max(score, min(100, model_score))   # 无漏项时才允许模型微调
    return max(0, min(100, score))


def audit(image: str | Path, prompt: str, llm, temperature: float = 0.1,
          max_tokens: int = 1400, schema: bool = True) -> Audit:
    prepared = prepare(image)
    raw = llm.chat_vision(SYSTEM, USER % prompt.strip(), prepared.data_url,
                          temperature=temperature, max_tokens=max_tokens,
                          schema=AUDIT_SCHEMA if schema else None, name="audit")
    data = extract_json(raw)

    claims = []
    wrong = []
    for item in _as_list(data.get("claims")):
        if not isinstance(item, dict):
            continue
        text = str(item.get("claim") or "").strip()
        if not text:
            continue
        present = bool(item.get("present"))
        claims.append({"claim": text, "present": present,
                       "why": str(item.get("why") or "").strip()})
        if not present:
            wrong.append(text)

    # 模型给了 claims 却没给 score 时，用 claims 自己算，不信它的直觉分
    missing = [str(x) for x in _as_list(data.get("missing"))]
    model_score = None
    if "score" in data:
        try:
            model_score = int(round(float(data.get("score"))))
        except (TypeError, ValueError):
            model_score = None
    score = score_audit(claims, missing, model_score)

    return Audit(score=score,
                 missing=missing,
                 wrong=wrong,
                 ok=[str(x) for x in _as_list(data.get("ok"))],
                 claims=claims, raw=raw[:600], raw_chars=len(raw))


# ─────────────────────────────────────────────────────────────
# 补丁
# ─────────────────────────────────────────────────────────────

_STOP = set("""a an the of in on at to for with and or is are was were be been being
this that these those it its as by from into over under near behind front left right
top bottom center centre very more most some any all""".split())


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP}


def _covered(ir: IR, claim: str) -> bool:
    """claim 的关键词是否已被**将要写进 prompt 的文字**覆盖。

    判据不是 IR 字段，而是 build_clauses 的产物——那才是下游模型真正读到的东西。
    用字段判会漏掉渲染时补出来的冠词/岁数（"28-year-old" 并不存在于任何字段里）。
    """
    from .clauses import build_clauses

    want = _tokens(claim)
    if not want:
        return True
    hay: set[str] = set()
    for c in build_clauses(ir):
        hay |= _tokens(c.text)
    for e in ir.entities:
        hay |= _tokens(e.label)
        hay |= _tokens(str(e.text_content or ""))
    overlap = want & hay
    return len(overlap) / float(len(want)) >= 0.5


def _hit_entity(ir: IR, claim: str) -> Entity | None:
    """claim 命中的最重要实体。用 label 是否出现在 claim 里判定，保守。"""
    low = claim.lower()
    best, best_len = None, 0
    for e in ir.entities:
        lab = (e.label or "").strip().lower()
        if lab and len(lab) > 2 and lab in low and len(lab) > best_len:
            best, best_len = e, len(lab)
    return best


def patch(ir: IR, result: Audit, report: Report | None = None,
          max_add: int = 6) -> tuple[IR, list[str]]:
    """按审计结果补 IR。返回 (新 IR, 变更说明)。不修改入参。

    `max_add` 是硬上限：审计一次能报十几条漏项，全加进去会把 prompt 撑爆，
    而且第一轮加完分数不涨时再加也是白加。
    """
    r = report or Report()
    changes: list[str] = []
    data = json.loads(json.dumps(ir.to_dict(), ensure_ascii=False))   # 深拷贝，IR 无引用环
    new = IR.from_dict(data)

    # ① 漏写 → 补低重要度实体
    added = 0
    for claim in result.missing:
        if added >= max_add:
            r.warn("漏项超过 %d 条，本轮只补前 %d 条" % (max_add, max_add))
            break
        claim = claim.strip()
        if len(claim) < 3 or is_meta_comment(claim) or _covered(new, claim):
            continue
        eid = "fix%d" % (added + 1)
        while new.entity(eid):
            eid += "_"
        new.entities.append(Entity(
            id=eid, kind="object", label=_claim_label(claim), importance=0.45,
            evidence=["audit: missing"]))
        added += 1
        changes.append("补漏：%s" % claim[:80])
    if added:
        r.warn("审计补入 %d 条漏写" % added)

    # ② 编造 → 只删弱实体 / 命中 note；主角不动
    removed = 0
    for claim in result.wrong:
        claim = claim.strip()
        if not claim:
            continue
        matched = False
        for key in list((new.notes or {}).keys()):
            want = _tokens(claim)
            if want and len(want & _tokens(new.notes[key])) / float(len(want)) >= 0.6:
                new.notes.pop(key)
                changes.append("删 note[%s]：%s" % (key, claim[:60]))
                removed += 1
                matched = True
                break
        hit = _hit_entity(new, claim)
        if hit is not None:
            matched = True
            if hit.importance >= 0.7:
                changes.append("保留高重要度实体 %s（审计判错，但主角不删）" % hit.label)
            else:
                new.entities = [e for e in new.entities if e.id != hit.id]
                changes.append("删实体 %s：%s" % (hit.label, claim[:60]))
                removed += 1
        if not matched:
            changes.append("记录未匹配的编造：%s" % claim[:80])

    new.normalize()
    new.provenance["audit"] = {"score": result.score, "missing": result.missing,
                               "wrong": result.wrong, "changes": changes}
    if removed:
        r.warn("审计删除 %d 处编造" % removed)
    return new, changes


def _claim_label(claim: str) -> str:
    """把审计意见变成可直接朗读的名词短语。

    第一版取前 3 个词，产出 "four distinct panel" 这种残句，渲染后是
    "four distinct panel, The four distinct panel crops (close-ups) shown..."——
    标签和描述各说一遍。整句当标签反而通顺："The four distinct panel crops
    (close-ups) shown in the storyboard"。
    """
    text = " ".join(claim.split()).strip().rstrip(".")
    words = text.split()
    if len(words) > 16:
        text = " ".join(words[:16])
    return text[:1].lower() + text[1:] if text[:1].isupper() and \
        text.split()[0].lower() in _STOP else text


def reverse_with_audit(image: str | Path, llm, compile_fn, rounds: int = 2,
                       target: int = 85, schema: bool = True,
                       text_pass: bool = True, max_side: int = 1536
                       ) -> tuple[IR, Report, list[dict]]:
    """反推 → 编译 → 审计 → 补丁，最多 `rounds` 轮。

    `compile_fn(ir) -> str` 由调用方给（哪个后端都行）。
    返回 (最终 IR, 报告, 每轮审计记录)。
    """
    from .reverse import reverse as _reverse
    ir, report = _reverse(image, llm, schema=schema, text_pass=text_pass,
                          max_side=max_side)
    history: list[dict] = []
    prev = -1
    for i in range(rounds):
        prompt = compile_fn(ir)
        result = audit(image, prompt, llm, schema=schema)
        history.append({"round": i + 1, "score": result.score,
                        "verdict": result.verdict, "missing": result.missing,
                        "wrong": result.wrong, "claims": result.claims})
        if result.degraded:
            # 空审计不能当成「通过」。记下来，停止补丁，把原始响应留给排查。
            history[-1]["raw"] = result.raw
            break
        if result.score >= target:
            break
        if result.score <= prev:
            # 补过一轮分数没涨：继续补只会把 prompt 撑长，停。
            history[-1]["changes"] = ["分数未提升（%d→%d），停止补丁" % (prev, result.score)]
            break
        prev = result.score
        ir, changes = patch(ir, result, report)
        history[-1]["changes"] = changes
        if not changes:
            break
    return ir, report, history
