# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""扩写固定项护栏（opt-in `expand_fixed_guard`）的离线单测（零 GPU/网络）。

护栏契约：`mode="llm"` 选完候选后跑 `expand.verify`；只要 `report.misses` 非空
（固定文本/年龄/颜色/方位被改），就**回退到同一方言的确定性草稿**（不调模型），
并把 `Realized.expand["fixed_guard"] = {fallback, pre_guard_misses, reason}` 挂上，
同时写一条人话 warning。默认关闭 ⇒ 旧行为不变。

跑：python tests/test_expand_guard.py -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.adapters import get_adapter          # noqa: E402
from moss_llm.smith.clauses import build_clauses          # noqa: E402
from moss_llm.smith.ir import IR                          # noqa: E402
from moss_llm.smith.registry import Registry              # noqa: E402
from moss_llm.smith.roundtrip import diff                 # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter        # noqa: E402

REG = Registry.load()
FIXTURE = ROOT / "tests" / "fixtures" / "smith" / "hero-storyboard.ir.json"


def _ir() -> IR:
    return IR.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _draft() -> str:
    """同 IR/同 length 的确定性草稿（不含 sanitize 之外的处理）。"""
    ad = get_adapter("moss-image", REG, counter=HeuristicCounter())
    ad.length = "standard"
    return ad._render(_ir(), build_clauses(_ir()), "deterministic")


class _Fixed:
    """固定返回一段文本的桩（记录调用次数）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list = []

    def chat(self, system, user, temperature=0.4, max_tokens=1200):
        self.calls.append({"system_head": (system or "")[:60]})
        return self.text


class _Drop(_Fixed):
    """只回一句通用描述（丢固定年龄/颜色/方位）。"""

    def chat(self, system, user, temperature=0.4, max_tokens=1200):
        self.calls.append({})
        return "A woman stands in a quiet room."


class _InventOnly(_Fixed):
    """只回一句 IR 里没有的内容（全丢 + 编造）。"""

    def chat(self, system, user, temperature=0.4, max_tokens=1200):
        self.calls.append({})
        return "A purple elephant walks through the snow."


class _AdapterCase(unittest.TestCase):
    def make(self, stub_text: str = "", *, guard: bool, steps=None, stub_cls=_Fixed):
        ad = get_adapter("moss-image", REG, counter=HeuristicCounter())
        ad.length = "standard"
        ad.gen_candidates = 1
        ad.gen_retries = 0
        ad.expand_steps = steps
        ad.expand_fixed_guard = guard
        ad.llm = stub_cls(stub_text)
        return ad

    def run_case(self, stub_text="", *, guard, steps=None, stub_cls=_Fixed):
        ad = self.make(stub_text, guard=guard, steps=steps, stub_cls=stub_cls)
        out = ad.realize(_ir(), mode="llm", use_layout=False)
        return ad, out

    @staticmethod
    def guard_of(out) -> dict:
        return (out.expand or {}).get("fixed_guard") or {}

    @staticmethod
    def determinism(out) -> dict:
        det = get_adapter("moss-image", REG, counter=HeuristicCounter())
        det.length = "standard"
        r = det.realize(_ir(), mode="deterministic", use_layout=False)
        return {"text": r.positive}


class TestGuardDefaultOff(_AdapterCase):
    def test_default_off_keeps_old_behavior(self):
        ad, out = self.run_case(_draft(), guard=False, stub_cls=_Drop)
        self.assertNotIn("fixed_guard", out.expand or {})
        self.assertGreater(len(diff(_ir(), out.positive, target="moss-image").lost), 0)

    def test_guard_false_same_as_attribute_absent(self):
        text = _draft()
        ad1, out1 = self.run_case(text, guard=False)
        ad2, out2 = self.run_case(text, guard=False)
        out2.expand = None                       # 模拟旧版本没有 expand 字段
        self.assertEqual(out1.positive, out2.positive)
        self.assertNotEqual(ad1.name, "")


class TestGuardFallback(_AdapterCase):
    def test_drop_stub_falls_back_to_deterministic(self):
        ad, out = self.run_case(_draft(), guard=True, stub_cls=_Drop)
        g = self.guard_of(out)
        self.assertTrue(g.get("fallback"), g)
        self.assertTrue(g.get("pre_guard_misses"), g)
        self.assertIn("固定项护栏", "\n".join(out.warnings))
        d = diff(_ir(), out.positive, target="moss-image")
        self.assertEqual(d.lost, [], "回退后必须 lost=0（确定性草稿）")
        self.assertEqual(out.positive, self.determinism(out)["text"])

    def test_invent_only_falls_back(self):
        ad, out = self.run_case(_draft(), guard=True, stub_cls=_InventOnly)
        g = self.guard_of(out)
        self.assertTrue(g.get("fallback"), g)
        self.assertEqual(diff(_ir(), out.positive, target="moss-image").lost, [])

    def test_empty_model_output_falls_back(self):
        ad, out = self.run_case("", guard=True, stub_cls=_Fixed)
        self.assertTrue(self.guard_of(out).get("fallback"))
        self.assertTrue((out.positive or "").strip())

    def test_good_candidate_keeps_llm_output(self):
        draft = _draft()
        ad, out = self.run_case(draft, guard=True, stub_cls=_Fixed)
        g = self.guard_of(out)
        self.assertFalse(g.get("fallback"), g)
        self.assertEqual(g.get("pre_guard_misses"), [])
        self.assertEqual(out.positive, draft)

    def test_warning_lists_pre_guard_misses(self):
        ad, out = self.run_case(_draft(), guard=True, stub_cls=_Drop)
        g = self.guard_of(out)
        joined = "\n".join(out.warnings)
        self.assertTrue(g.get("pre_guard_misses"))
        kind = g["pre_guard_misses"][0]
        self.assertIn(kind, joined, "warning 要点名具体 pre_guard_misses")
        self.assertIn("回退确定性草稿", joined)


class TestGuardWithSteps(_AdapterCase):
    def test_steps_drop_falls_back_and_keeps_stage(self):
        ad, out = self.run_case(_draft(), guard=True, steps=("rewrite",),
                                stub_cls=_Drop)
        g = self.guard_of(out)
        self.assertTrue(g.get("fallback"), g)
        self.assertTrue(out.expand.get("stage"), "步级归因字段不能丢：%r" % (out.expand,))
        self.assertEqual(diff(_ir(), out.positive, target="moss-image").lost, [])

    def test_steps_good_candidate_no_fallback(self):
        ad, out = self.run_case(_draft(), guard=True, steps=("rewrite",))
        g = self.guard_of(out)
        self.assertFalse(g.get("fallback"), g)
        steps = (out.expand or {}).get("steps") or []
        self.assertTrue(any(s.get("step") == "rewrite" for s in steps),
                        "步级归因（steps）不能丢：%r" % (steps,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
