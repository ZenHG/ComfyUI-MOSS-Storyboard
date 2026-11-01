# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""往返校验：IR → prompt → 事实集合，两边相减。

这是**不需要图像生成器**的最便宜的一层验证（VISION §4 P1⑤）：
编译丢了一条事实，不需要看生成的图就能发现——因为那条事实本来就不在
交付物里。

两个方向的错，含义完全不同：

- **丢（lost）= 欠表达。** IR 里有，prompt 里没有。多半是预算砍掉了、
  渲染路径没接上、或方言把某个维度整个吞了。
- **多（extra）= 幻觉。** prompt 里有，IR 里没有。编译器的每一句话都该
  能追溯回一条事实；追不回去的，就是它自己编的。

判据：`len(lost) == 0`。多出来的按 kind 单独报——颜色和方位词在多语言
同义改写里最容易误报，所以 `extra` 只用来提示，不用来判死。

零模型、零网络、毫秒级。`diff()` 是纯函数，`roundtrip()` 只是「先编译再 diff」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decompose import Facts, scan
from .expand import _ir_numbers, _norm, fixed_items, hit, is_ir_text


@dataclass
class Diff:
    target: str = ""
    words: int = 0
    lost: list = field(default_factory=list)     # [(kind, value)] IR 有、prompt 没有
    extra: list = field(default_factory=list)    # [(kind, value)] prompt 有、IR 没有
    notes: list = field(default_factory=list)
    #: 这套核对对这次编译是否成立。edit 任务按方言规则「保持项只点名、不描述外观」，
    #: 年龄/颜色本来就不该出现在指令里——这时报「丢失」是判据错了，不是编译错了。
    applicable: bool = True

    @property
    def ok(self) -> bool:
        return (not self.lost) if self.applicable else True

    def to_dict(self) -> dict:
        return {"target": self.target, "words": self.words,
                "lost": [list(x) for x in self.lost],
                "extra": [list(x) for x in self.extra],
                "notes": self.notes, "applicable": self.applicable, "ok": self.ok}

    def lines(self) -> list:
        if not self.applicable:
            return ["不适用：" + (self.notes[0] if self.notes else "这套核对对这个任务不成立")]
        if self.ok and not self.extra:
            return ["往返一致：%d 词，零丢失零多余" % self.words]
        out = []
        for k, v in self.lost:
            out.append("丢失 [%s] %s" % (k, v))
        for k, v in self.extra:
            out.append("多出 [%s] %s（IR 里没有）" % (k, v))
        return out


def facts_of(ir) -> Facts:
    """IR 这一侧的事实集合。口径与 `expand.fixed_items` 完全一致——
    两套口径会让「verify 说没丢」和「roundtrip 说丢了」同时成立。"""
    f = Facts()
    for item in fixed_items(ir):
        if item.kind == "text":
            f.texts.append(item.value)
        elif item.kind == "age":
            try:
                f.ages.append(int(item.value))
                f.subjects.append("")
            except ValueError:
                pass
        elif item.kind == "color":
            f.colors.append(item.value)
        elif item.kind == "anchor":
            f.anchors.append(item.value)
    f.numbers = sorted(_ir_numbers(ir))
    return f


def diff(ir, text: str, target: str = "") -> Diff:
    """IR 与一段 prompt 的事实差。纯函数，不编译、不调模型。"""
    d = Diff(target=target, words=len((text or "").split()))
    flat = _norm(text)
    seen = scan(text)

    if seen.json:
        d.notes.append("JSON 方言：事实写在结构里，方位按坐标核对，不按词核对")
    if (getattr(ir, "task", "") or "") in ("edit", "inpaint", "outpaint"):
        d.applicable = False
        d.notes.append("edit 任务的方言规则是「保持项只点名、不描述外观」，"
                       "年龄/颜色本来就不该写进指令——事实集核对不适用")

    # ① 丢：IR 记着的每条事实，问一遍「这段 prompt 里有没有」
    for item in fixed_items(ir):
        if item.kind == "anchor" and seen.json and _boxed(ir, item.origin):
            continue          # 方位由坐标承载，词表里找不到是对的
        if not hit(item, flat):
            d.lost.append((item.kind, item.value))

    # ② 多：prompt 里的每条事实，问一遍「IR 里有没有」
    ir_f = facts_of(ir)
    # 文本比对用 expand.is_ir_text（与 verify ③ / steps._fact_delta 同一 helper）：
    # 多行块拆成单行引号、内嵌引号换弯引号、相邻两引号被 scan 合串，都不算 extra；
    # 真正 IR 里没有的新文本仍报 extra（helper 只放宽引用方式，不放宽内容）。
    for q in seen.texts:
        if not is_ir_text(q, ir_f.texts):
            d.extra.append(("text", q))
    ages = {str(a) for a in ir_f.ages}
    for a in seen.ages:
        if str(a) not in ages:
            d.extra.append(("age", str(a)))
    nums = set(ir_f.numbers)
    for n in seen.numbers:
        if n not in nums:
            d.extra.append(("number", n))
    # 颜色：IR 的**任何**字段里出现过就不算多（notes 也是 IR，散文里的颜色不是编的）
    blob = _ir_blob(ir)
    for c in seen.colors:
        if c not in blob:
            d.extra.append(("color", c))
    return d


def _boxed(ir, eid: str) -> bool:
    """这个实体有没有坐标。JSON 方言里坐标就是方位事实的落点。"""
    ent = ir.entity(eid) if eid else None
    if ent is None:
        return False
    sp = ent.spatial or {}
    return bool(sp.get("bbox_norm") or sp.get("bbox") or sp.get("bbox_source"))


def _ir_blob(ir) -> str:
    """IR 全文（小写）。用来判「这个词 IR 里到底有没有」，不区分它落在哪个字段。"""
    import json
    return json.dumps(ir.to_dict(), ensure_ascii=False).lower()


def roundtrip(ir, adapter, mode: str = "deterministic") -> tuple[Diff, object]:
    """IR → 该后端的 prompt → 事实差。返回 (diff, Realized)。"""
    out = adapter.realize(ir, mode=mode)
    d = diff(ir, out.positive, target=adapter.name)
    if out.dropped:
        d.notes.append("编译期按预算砍掉 %d 条子句（丢失可能来自这里）" % len(out.dropped))
    return d, out
