# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""不变式（metamorphic relations）：不需要 ground truth 的一层验证。

## 为什么需要这一层

验证一个提示词编译器，最直觉的办法是「看图好不好」。但那要生成器、要人、要钱，
而且**不可复现**。L2 保真（说的和图上一致）需要标注；L4 偏好需要多人打分。

还有一类性质**不需要标准答案**也能判真假：

> 我把输入做一个**语义上不该改变结果**的变换，输出就必须跟着不变（或按已知方向变）。

这就是 metamorphic testing。它的价值在于：**免费、确定性、毫秒级、可进 CI**。
它证明不了「prompt 写得好」，但它能证明「编译器没有偷偷依赖不该依赖的东西」——
而后者恰恰是最容易悄悄坏掉的部分。

## 五条关系

| 代号 | 关系 | 变换 | 预期 |
|---|---|---|---|
| M1 | 幂等 | 同一 IR 编译两次 | 逐字节相同 |
| M2 | 顺序无关 | 打乱 entities / relations / notes 数组 | 逐字节相同 |
| M3 | 规避隔离 | 追加 `constraints.avoid` | positive 不变，只动 negative |
| M4 | 预算只做减法 | 预算从低到高扫 | 不越界、用掉的 token 不减、子句不被改写 |
| M5 | 提权不丢 | 把某实体 importance 提到 1.0 | 原本出现过的描述不会消失 |

> M4 原本想写「集合单调：加预算不丢已保留的子句」。**它不成立**，反例见该函数
> 的注释——所以改成了三条真能保证的性质。测试发现关系本身是错的，这也是收获。

## 纪律：每条关系都要能「变红」

一条永远为真的测试等于没有测试。`tests/test_core.py` 里对每条关系都有一个
**故意破坏版**（把实现改坏、断言必须失败）。只有破坏版红了，正常版绿才有意义。

## 边界

- 只对 **deterministic 模式**成立。llm 模式本身就是随机的，这里不测。
- M4 测的是 `pack()`，不是最终字符串：字符串还会被方言过滤（FLUX 3 按维度裁剪），
  那不是预算的语义。
- 这些关系**不评价质量**。全绿只说明「没有不该有的依赖」，不说明「写得好」。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from .clauses import build_clauses, pack
from .ir import IR
from .tokens import HeuristicCounter

#: 预算扫描点（token）。从「装不下一条」到「装得下全部」。
#: 不用 0：`pack(clauses, 0)` 的语义是「不限预算」，0 在这里是哨兵不是数值。
BUDGET_SWEEP = (10, 20, 40, 60, 90, 120, 200, 400, 800)


@dataclass
class Result:
    code: str
    name: str
    ok: bool
    detail: str = ""
    #: 失败时的最小复现线索：哪两条不一样、差在哪
    evidence: list = field(default_factory=list)

    def __str__(self) -> str:
        tag = "PASS" if self.ok else "FAIL"
        line = "[%s] %s %s" % (tag, self.code, self.name)
        if self.detail:
            line += "  " + self.detail
        return line

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "ok": self.ok,
                "detail": self.detail, "evidence": self.evidence}


# ── 变换工具 ──────────────────────────────────────────────────

def _compile(adapter, ir: IR) -> tuple:
    out = adapter.realize(ir, mode="deterministic")
    return out.positive, out.negative or "", json.dumps(out.params, sort_keys=True)


def _first_diff(a: str, b: str, width: int = 60) -> list:
    """给出第一处差异的上下文，而不是把两整段 prompt 都打印出来。"""
    n = min(len(a), len(b))
    i = next((k for k in range(n) if a[k] != b[k]), n)
    lo = max(0, i - width // 2)
    return ["@%d" % i, "A: …%s…" % a[lo:i + width], "B: …%s…" % b[lo:i + width]]


# ── 五条关系 ──────────────────────────────────────────────────

def m1_idempotent(adapter, ir: IR) -> Result:
    """同一 IR 编译两次必须逐字节相同。有任何随机源/全局状态都会在这里露出来。"""
    a, b = _compile(adapter, ir), _compile(adapter, ir)
    ok = a == b
    return Result("M1", "幂等（同输入两次编译）", ok,
                  "" if ok else "两次编译结果不同",
                  [] if ok else _first_diff(a[0], b[0]))


def m2_order_free(adapter, ir: IR) -> Result:
    """打乱 entities / relations / notes 的数组顺序，输出必须不变。

    这三处的顺序在语义上都是集合。输出依赖它 = 两个人写同一份 IR 得到两张图，
    而且 diff 看不出原因（内容一模一样，只是数组换了个位置）。
    """
    base = _compile(adapter, ir)

    variants = {}
    variants["entities 倒序"] = IR.from_dict(_reorder(ir, entities=True))
    variants["relations 倒序"] = IR.from_dict(_reorder(ir, relations=True))
    variants["notes 倒序"] = IR.from_dict(_reorder(ir, notes=True))

    bad = []
    for label, variant in variants.items():
        if _compile(adapter, variant) != base:
            bad.append(label)
    return Result("M2", "顺序无关（数组顺序不改变输出）", not bad,
                  "" if not bad else "以下变换改变了输出：" + "、".join(bad),
                  [] if not bad else _first_diff(base[0], _compile(
                      adapter, variants[bad[0]])[0]))


def _reorder(ir: IR, entities=False, relations=False, notes=False) -> dict:
    d = copy.deepcopy(ir.to_dict())
    if entities:
        d["entities"] = list(reversed(d.get("entities") or []))
    if relations:
        d["relations"] = list(reversed(d.get("relations") or []))
    if notes:
        d["notes"] = dict(reversed(list((d.get("notes") or {}).items())))
    return d


def m3_avoid_isolated(adapter, ir: IR) -> Result:
    """追加 avoid 只能改 negative，不能改 positive。

    avoid 是「不要什么」，走负向通道。它一旦渗进正向散文，就变成
    「no text, no watermark」这种反向激活——正是扩写体检里判 warn 的那类错误。
    没有负向通道的后端（FLUX 3）会直接丢掉 avoid，同样不该动 positive。
    """
    base = _compile(adapter, ir)
    probe = IR.from_dict(ir.to_dict())
    probe.constraints["avoid"] = list(probe.constraints.get("avoid") or []) + \
        ["blurry", "watermark", "extra fingers"]
    got = _compile(adapter, probe)
    ok = got[0] == base[0]
    return Result("M3", "规避隔离（avoid 只进 negative）", ok,
                  "" if ok else "追加 avoid 后 positive 变了",
                  [] if ok else _first_diff(base[0], got[0]))


def m4_budget_honest(adapter, ir: IR) -> Result:
    """预算只做减法：不越界、不减用、不改写。

    这里**没有**断言「加预算 ⇒ 保留集合变大」，因为它对跳过式装箱根本不成立。
    实测反例（成本序列 4,3）：预算 3 时保留第 2 条，预算 4 时保留第 1 条。
    穷举成本 3–12、长度 ≤6 的序列，集合单调与条数单调都有反例。

    为什么不改成前缀式（装不下就停）来换取单调：实测 40 token 预算下
    前缀式**一条都装不下**（首条主体子句就 61 token），输出直接为空。
    利用率换单调性，这个交换不划算。

    所以只钉住三件真能保证、且坏了就一定是 bug 的事：
    ① 不越界：保留子句的总成本 ≤ 预算；
    ② 不减用：预算变大，用掉的 token 不会变少（穷举无反例）；
    ③ 不改写：保留的每条子句文本，与「不限预算」时的同一条逐字相同——
       防的是将来有人为了「省预算」去压缩子句内容。预算只能决定**删谁**，
       不能决定**怎么写**。这一条是三层里唯一会真正伤到事实的。
    """
    if not getattr(adapter, "clause_pipeline", True):
        # H3 不经过子句装箱：对它跑 M4 测的是 pack() 本身，与这个后端无关。
        # 报「不适用」而不是 PASS——报告里写「通过」就等于宣称验过了。
        return Result("M4", "预算只做减法（不越界/不减用/不改写）", True,
                      "不适用（%s 不走子句装箱）" % adapter.name)
    clauses = build_clauses(ir)
    counter = HeuristicCounter()
    full = {id(c): c.text for c in clauses}
    prev_used = -1
    for limit in BUDGET_SWEEP:
        kept, _ = pack(clauses, limit, counter)
        used = sum(counter.count(c.text) + 2 for c in kept)
        if used > limit:
            return Result("M4", "预算只做减法（不越界/不减用/不改写）", False,
                          "预算 %d 却用掉 %d token" % (limit, used))
        if used < prev_used:
            return Result("M4", "预算只做减法（不越界/不减用/不改写）", False,
                          "预算涨到 %d，用掉的 token 反而从 %d 降到 %d"
                          % (limit, prev_used, used))
        for c in kept:
            if full.get(id(c), c.text) != c.text:
                return Result("M4", "预算只做减法（不越界/不减用/不改写）", False,
                              "预算 %d 时子句文本被改写" % limit, [c.text[:60]])
        prev_used = used
    return Result("M4", "预算只做减法（不越界/不减用/不改写）", True,
                  "扫描 %d 个预算点" % len(BUDGET_SWEEP))


def m5_raise_keeps(adapter, ir: IR) -> Result:
    """把某个实体的 importance 提到 1.0，它原本出现过的描述不能消失。

    提权只该让它更靠前、更难被裁。如果提权反而让它消失，说明排序键写反了，
    或者提权触发了别的裁剪路径（例如分组去重按第一个实体的重要度算）。

    只拿**基准输出里确实出现过**的片段来比：没出现过的片段消失了不算违规，
    否则会把「本来就没渲染」误判成「提权后被删」。

    **比对不分大小写**。第一句会被 `capitalize_sentences` 改成大写，按原样比
    会让「恰好是首句」的实体全部漏检——实测 02 海报在 qwen/krea2 上是
    「检查了 0 个实体」，而那三条描述明明都在输出里。漏检比误报更危险：
    报告写的是「没出现在基准输出里」，读起来像结论，其实是没比。

    text 实体以前被排除（`describe_text` 用 importance 决定字号，提权会合法改写
    输出）；`importance` / `typography.visual_weight` 拆开之后这条前提消失了，
    所以现在一并检查。
    """
    from .clauses import describe_entity

    base = _compile(adapter, ir)[0].lower()
    clauses = build_clauses(ir)
    checked, skipped = 0, 0
    for e in ir.entities:
        cands = [c.text for c in clauses if c.entity_id == e.id]
        cands.append(describe_entity(e, with_anchor=False))
        present = [f for f in cands if f and f.lower() in base]
        if not present:
            skipped += 1
            continue
        probe = IR.from_dict(ir.to_dict())
        for pe in probe.entities:
            if pe.id == e.id:
                pe.importance = 1.0
        got = _compile(adapter, probe)[0].lower()
        checked += 1
        lost = [f for f in present if f.lower() not in got]
        if lost:
            return Result("M5", "提权不丢（importance→1.0 不丢描述）", False,
                          "实体 %s 提权后描述消失" % e.id, [f[:70] for f in lost])
    detail = "检查了 %d 个实体" % checked
    if skipped:
        detail += "，跳过 %d 个（描述未出现在基准输出里）" % skipped
    return Result("M5", "提权不丢（importance→1.0 不丢描述）", True, detail)


def m6_timeline_roundtrip(adapter, ir: IR) -> Result:
    """时间线往返：IR → 提示词 → 再解析回来，镜头/时间戳/台词必须一条不丢。

    这条关系只对**有时间线**的 IR 成立，图像 IR 报「不适用」。

    为什么它值得单独一条：`roundtrip.diff` 比的是「事实在不在文本里」，
    而这条比的是「**读回来还是不是同一件事**」。两者不等价——
    文本里出现了台词，不代表它落在正确的镜头里、带着正确的说话人。
    一个时间戳写错、台词被挪到相邻镜头，`diff` 全绿而这条会红。
    """
    if not (ir.timeline or {}).get("shots"):
        return Result("M6", "时间线往返（镜头/时间戳/台词不丢位）", True,
                      "不适用（这份 IR 没有 timeline）")
    from . import h3

    text = _compile(adapter, ir)[0]
    back = IR.from_dict(h3.parse(text))
    got = (back.timeline or {}).get("shots") or []
    want = ir.timeline["shots"]

    if len(got) != len(want):
        return Result("M6", "时间线往返（镜头/时间戳/台词不丢位）", False,
                      "镜头数 %d → %d" % (len(want), len(got)))

    problems = []
    for a, b in zip(want, got, strict=False):
        if a.get("t_start") is not None and b.get("t_start") is not None:
            if abs(float(a["t_start"]) - float(b["t_start"])) > 1e-6:
                problems.append("Shot %s 时间戳 %s → %s"
                                % (a.get("index"), h3.format_ts(a["t_start"]),
                                   h3.format_ts(b["t_start"])))
        elif (a.get("t_start") is None) != (b.get("t_start") is None):
            problems.append("Shot %s 时间戳有无不一致" % a.get("index"))
        want_lines = [str(d.get("text") or "") for d in a.get("dialogue") or []]
        got_lines = [str(d.get("text") or "") for d in b.get("dialogue") or []]
        if want_lines != got_lines:
            problems.append("Shot %s 台词 %r → %r"
                            % (a.get("index"), want_lines, got_lines))
        want_text = [str(t.get("text") or "") for t in a.get("onscreen_text") or []]
        got_text = [str(t.get("text") or "") for t in b.get("onscreen_text") or []]
        if want_text != got_text:
            problems.append("Shot %s 画面文字 %r → %r"
                            % (a.get("index"), want_text, got_text))
    if problems:
        return Result("M6", "时间线往返（镜头/时间戳/台词不丢位）", False,
                      "解析回来的时间线与原 IR 不一致", problems)
    return Result("M6", "时间线往返（镜头/时间戳/台词不丢位）", True,
                  "%d 个镜头全部对位" % len(want))


RELATIONS = (
    ("M1", "幂等（同输入两次编译）", m1_idempotent),
    ("M2", "顺序无关（数组顺序不改变输出）", m2_order_free),
    ("M3", "规避隔离（avoid 只进 negative）", m3_avoid_isolated),
    ("M4", "预算只做减法（不越界/不减用/不改写）", m4_budget_honest),
    ("M5", "提权不丢（importance→1.0 不丢描述）", m5_raise_keeps),
    ("M6", "时间线往返（镜头/时间戳/台词不丢位）", m6_timeline_roundtrip),
)


def run(adapter, ir: IR, only: tuple | None = None) -> list:
    """跑全部关系。`only=("M2","M4")` 可只跑子集（调试用）。

    **能力不匹配直接抛 ValueError**，不当作「5 条全失败」。拿一份 edit 的 IR 去跑
    没有编辑通道的后端（moss-image / krea2），跑出来的是 5 条同因 FAIL，
    读起来像「编译器坏了」——那是判据用错了地方，不是结论。
    """
    adapter._guard(ir)
    out = []
    for code, name, fn in RELATIONS:
        if only and code not in only:
            continue
        try:
            res = fn(adapter, ir)
        except Exception as exc:      # 关系自己炸了也算失败，不算「跳过」
            res = Result(code, name, False, "%s: %s" % (type(exc).__name__, exc))
        out.append(res)
    return out


def summarize(results: list) -> tuple:
    """(全部通过?, 一行摘要)。"""
    bad = [r for r in results if not r.ok]
    if not bad:
        return True, "%d/%d 条不变式通过" % (len(results), len(results))
    return False, "%d/%d 条不变式失败：%s" % (
        len(bad), len(results), "、".join(r.code for r in bad))
