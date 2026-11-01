# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""确定性草稿残差回归（W8）。

钉住三类真实 W5b 残差：
- descriptors.light / content / condition 里的颜色必须进入 deterministic prompt；
- 多行/内嵌引号的 text_content 只包一次引号，仍能通过 roundtrip.diff；
- B58 这类 capped/40 实体/重复 label 的输入属于「输入无效」，不是正常残差。

冻结基线更新：本文件最后一个测试直接比较 tools.freeze_smith_set.snapshot()
与 tests/fixtures/smith/expected.json；改渲染后必须重冻并说明原因。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.adapters import get_adapter          # noqa: E402
from moss_llm.smith.clauses import quote_text_block       # noqa: E402
from moss_llm.smith.expand import fixed_items             # noqa: E402
from moss_llm.smith.ir import IR                          # noqa: E402
from moss_llm.smith.registry import Registry              # noqa: E402
from moss_llm.smith.roundtrip import diff                 # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter        # noqa: E402

REG = Registry.load()


def _ir(entities: list[dict]) -> IR:
    return IR.from_dict({
        "canvas": {"background": "studio backdrop"},
        "style": {"medium": "photograph"},
        "entities": entities,
        "relations": [],
    })


def _render(entities: list[dict]) -> tuple[IR, str]:
    ir = _ir(entities)
    out = get_adapter("moss-image", REG, counter=HeuristicCounter()).realize(
        ir, mode="deterministic", use_layout=False)
    return ir, out.positive or ""


def _text(text: str, anchor: str = "top-left") -> dict:
    return {"id": "t1", "kind": "text", "label": "sign", "importance": 0.95,
            "text_content": text, "spatial": {"anchor": anchor}}


class TestLightContentColors(unittest.TestCase):
    def test_light_content_condition_colors_enter_prompt(self):
        """A08/A24/A35 的颜色藏在 light/content/condition，必须被渲染。"""
        ir, prompt = _render([
            {"id": "e1", "kind": "object", "label": "lamp", "importance": 0.9,
             "descriptors": {"light": "warm yellow glow"},
             "spatial": {"anchor": "left foreground"}},
            {"id": "e2", "kind": "object", "label": "glass", "importance": 0.8,
             "descriptors": {"content": "dark brown liquid, ice cubes"},
             "spatial": {"anchor": "center"}},
            {"id": "e3", "kind": "object", "label": "wall", "importance": 0.7,
             "descriptors": {"condition": "slightly textured, warm yellow tone"},
             "spatial": {"anchor": "background"}},
        ])
        low = prompt.lower()
        for color in ("yellow", "brown"):
            self.assertIn(color, low)
        d = diff(ir, prompt, target="moss-image")
        self.assertEqual(d.lost, [])
        colors = [f.value for f in fixed_items(ir) if f.kind == "color"]
        self.assertIn("yellow", colors)
        self.assertIn("brown", colors)


class TestTextBlocks(unittest.TestCase):
    def test_multiline_text_block_has_one_quote_pair(self):
        """A54/B60 的多行块：整块一只引号对，换行压空格，diff 无 lost/extra。"""
        raw = "ALPHA\nBETA\nGAMMA"
        ir, prompt = _render([_text(raw)])
        self.assertEqual(prompt.count('"'), 2, prompt)
        self.assertIn("ALPHA BETA GAMMA", prompt)
        d = diff(ir, prompt, target="moss-image")
        self.assertEqual(d.lost, [])
        self.assertEqual(d.extra, [])

    def test_inner_straight_quote_uses_curly_and_hits(self):
        """内容含直引号时改 curly 包裹，scan/hit 仍对上且不产生 extra。"""
        raw = 'He said "hello" loudly'
        ir, prompt = _render([_text(raw)])
        self.assertIn("“", prompt)
        self.assertIn("”", prompt)
        self.assertNotIn('"""', prompt)
        d = diff(ir, prompt, target="moss-image")
        self.assertEqual(d.lost, [])
        self.assertEqual(d.extra, [])

    def test_single_line_text_regression(self):
        """旧单行行为不变：仍是 the text "HELLO"。"""
        ir, prompt = _render([_text("HELLO")])
        self.assertIn('the text "hello"', prompt.lower())
        d = diff(ir, prompt, target="moss-image")
        self.assertEqual(d.lost, [])
        self.assertEqual(d.extra, [])

    def test_quote_helper_is_single_wrap(self):
        self.assertEqual(quote_text_block("A\nB"), '"A B"')
        self.assertEqual(quote_text_block('A "B" C'), "“A \"B\" C”")


class TestCappedInput(unittest.TestCase):
    def test_b58_capped_artifact_is_input_invalid(self):
        """B58 是 capped/40 实体/重复 label 的输入；它不是正常渲染残差。"""
        path = ROOT / "out/g7/round2/json60/B-9B/img58.json"
        if not path.is_file():
            self.skipTest("B58 artifact unavailable（out/ 不入库）")
        data = json.loads(path.read_text(encoding="utf-8"))
        report = data.get("report") or {}
        self.assertTrue(report.get("capped"), "B58 必须带 capped 告警")
        self.assertEqual(report.get("entity_count"), 40)
        labels = [e.get("label") for e in (data.get("ir") or {}).get("entities") or []]
        self.assertGreater(len(labels), len(set(labels)), "重复 label 是输入无效证据")


class TestGroupedEntityAnchor(unittest.TestCase):
    """分组实体（同 key）首实体 anchor 不能丢；无 anchor 时旧输出逐字节不变。"""

    def _two(self, first_anchor: str | None, second_anchor: str = "right"):
        first = {"id": "e1", "kind": "object", "label": "red wax seal",
                 "importance": 0.8}
        if first_anchor:
            first["spatial"] = {"anchor": first_anchor}
        return _render([
            first,
            {"id": "e2", "kind": "object", "label": "red wax seal",
             "importance": 0.7, "spatial": {"anchor": second_anchor}},
        ])

    def test_grouped_first_anchor_enters_subject(self):
        ir, prompt = self._two("bottom-right")
        low = prompt.lower()
        self.assertIn("red wax seal, in the bottom-right", low)
        self.assertIn("the same figure appears again in the right", low)
        d = diff(ir, prompt, target="moss-image")
        self.assertEqual(d.lost, [], d.lost)
        self.assertEqual(d.extra, [])

    def test_grouped_first_without_anchor_keeps_old_subject(self):
        """首实体无 anchor 时 subject 仍是 key（与旧版逐字节一致）。"""
        from moss_llm.smith.clauses import describe_entity
        ir, prompt = self._two(None)
        key = describe_entity(ir.entity("e1"), with_anchor=False)
        self.assertIn(key + ".", prompt.lower())
        self.assertIn("the same figure appears again in the right", prompt.lower())
        self.assertNotIn(key + ", in the", prompt.lower())

    def test_grouped_three_entities_keep_first_and_rest(self):
        ir, prompt = _render([
            {"id": "e1", "kind": "object", "label": "red wax seal",
             "importance": 0.8, "spatial": {"anchor": "bottom-right"}},
            {"id": "e2", "kind": "object", "label": "red wax seal",
             "importance": 0.7, "spatial": {"anchor": "right"}},
            {"id": "e3", "kind": "object", "label": "red wax seal",
             "importance": 0.6, "spatial": {"anchor": "left"}},
        ])
        low = prompt.lower()
        self.assertIn("red wax seal, in the bottom-right", low)
        self.assertIn("in the right and left", low)
        self.assertEqual(diff(ir, prompt, target="moss-image").lost, [])


class TestDetailsDescriptor(unittest.TestCase):
    """descriptors.details（复数）必须有渲染出口，否则 fixed_items 的颜色会假 lost。"""

    def test_details_string_enters_prompt(self):
        ir, prompt = _render([
            {"id": "e1", "kind": "object", "label": "patch", "importance": 0.9,
             "descriptors": {"details": "gold-colored metal button at top-left corner"},
             "spatial": {"anchor": "center"}},
        ])
        self.assertIn("gold-colored metal button", prompt.lower())
        colors = [f.value for f in fixed_items(ir) if f.kind == "color"]
        self.assertIn("gold", colors)
        self.assertEqual(diff(ir, prompt, target="moss-image").lost, [])

    def test_details_list_renders_each_item(self):
        items = ["gold-colored metal button at top-left corner",
                 "white embroidered five-pointed star",
                 "two parallel white vertical stripes"]
        ir, prompt = _render([
            {"id": "e1", "kind": "object", "label": "shoulder patch",
             "importance": 0.9, "descriptors": {"details": items},
             "spatial": {"anchor": "center"}},
        ])
        for item in items:
            self.assertIn(item.lower(), prompt.lower())
        self.assertEqual(diff(ir, prompt, target="moss-image").lost, [])

    def test_detail_and_details_do_not_double_render(self):
        ir, prompt = _render([
            {"id": "e1", "kind": "object", "label": "patch", "importance": 0.9,
             "descriptors": {"detail": "gold button",
                             "details": ["gold button", "velvet backing"]},
             "spatial": {"anchor": "center"}},
        ])
        low = prompt.lower()
        self.assertEqual(low.count("gold button"), 1, low)
        self.assertIn("velvet backing", low)


class TestFreezeBaseline(unittest.TestCase):
    def test_expected_baseline_matches_current_render(self):
        """渲染变更后 expected.json 必须重冻；这里直接对当前四 IR×五后端。"""
        from tools.freeze_smith_set import EXPECTED, snapshot
        got = snapshot()
        old = json.loads(EXPECTED.read_text(encoding="utf-8"))
        self.assertEqual(old, got, "渲染变了但 expected.json 未重冻")


if __name__ == "__main__":
    unittest.main(verbosity=2)
