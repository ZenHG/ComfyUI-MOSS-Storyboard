# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""对账器「图内文字」口径的单测：多行块 / 内嵌引号 / 相邻引号 / 真编造。

三个调用方必须同一套口径（否则一处绿一处红）：
- `expand.verify()` ③ `extra_quotes`
- `roundtrip.diff()` ② text extra
- `steps._fact_delta()`（经 verify 复用）

跑：python tests/test_text_accounting.py -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith import steps                         # noqa: E402
from moss_llm.smith.expand import (                      # noqa: E402
    fixed_items, hit, is_ir_text, ir_text_blocks, verify)
from moss_llm.smith.ir import IR                          # noqa: E402
from moss_llm.smith.roundtrip import diff                 # noqa: E402

_UI = json.loads((ROOT / "tests" / "fixtures" / "smith" / "ui-smoke.ir.json")
                 .read_text(encoding="utf-8"))


def _ir(text: str) -> IR:
    d = json.loads(json.dumps(_UI))
    d["entities"] = [{"id": "e1", "kind": "text", "text_content": text,
                      "spatial": {"anchor": "center"}}]
    return IR.from_dict(d)


def _text_extra(d) -> list:
    return [v for k, v in d.extra if k == "text"]


def _text_lost(d) -> list:
    return [v for k, v in d.lost if k == "text"]


class TestMultilineText(unittest.TestCase):
    def test_multiline_block_split_into_lines_is_not_extra(self):
        ir = _ir("Line one here\nLine two here")
        prompt = 'center of frame; the sign reads "Line one here" and "Line two here".'
        self.assertEqual(_text_extra(diff(ir, prompt)), [])
        self.assertEqual(_text_lost(diff(ir, prompt)), [])
        self.assertEqual(verify(ir, prompt).extra_quotes, [])

    def test_embedded_direct_quotes_wrapped_in_curly_is_not_extra(self):
        ir = _ir('"hello" loudly\nsecond line')
        prompt = 'center; the sign reads “"hello" loudly” and "second line".'
        d = diff(ir, prompt)
        self.assertEqual(_text_extra(d), [])
        self.assertEqual(_text_lost(d), [])
        self.assertEqual(verify(ir, prompt).extra_quotes, [])

    def test_missing_one_line_is_lost_not_extra(self):
        ir = _ir("Line one here\nLine two here")
        prompt = 'center; the sign reads "Line one here".'
        d = diff(ir, prompt)
        self.assertEqual(len(_text_lost(d)), 1, d.lost)
        self.assertEqual(_text_extra(d), [])
        rep = verify(ir, prompt)
        self.assertIn("[text] Line one here\nLine two here", rep.misses)
        self.assertEqual(rep.extra_quotes, [])

    def test_true_new_quoted_text_still_extra(self):
        ir = _ir("Line one here\nLine two here")
        prompt = 'center; "Line one here" "Line two here" "BRAND NEW TEXT".'
        d = diff(ir, prompt)
        self.assertIn("BRAND NEW TEXT", _text_extra(d))
        self.assertIn("brand new text", verify(ir, prompt).extra_quotes)

    def test_adjacent_quotes_merged_by_scan_is_not_extra(self):
        blocks = ir_text_blocks(_ir("Line one here\nLine two here"))
        # scan 可能把两个相邻引号之间的残片合成一条 q（含中间引号）
        self.assertTrue(is_ir_text('Line one here" "Line two here', blocks))
        prompt = 'center; "Line one here""Line two here".'
        self.assertEqual(_text_extra(diff(_ir("Line one here\nLine two here"), prompt)), [])

    def test_single_line_behavior_unchanged(self):
        ir = _ir("Hello world")
        f = fixed_items(ir)[0]
        flat = "center; the sign reads 'hello world'."
        self.assertTrue(hit(f, flat.lower()))
        self.assertEqual(_text_extra(diff(ir, 'center; "Hello world".')), [])
        self.assertEqual(_text_extra(diff(ir, 'center; "Other text".')), ["Other text"])
        self.assertEqual(verify(ir, 'center; "Other text".').extra_quotes, ["other text"])


class TestStepsDeltaUsesSameAccounting(unittest.TestCase):
    def test_fact_delta_new_quotes_respects_split_lines(self):
        ir = _ir("Line one here\nLine two here")
        source = 'center; "Line one here" "Line two here".'
        output = 'center; "Line one here" "Line two here".'
        before, after, new_hard, new_nums, new_quotes = steps._fact_delta(
            ir, source, output)
        self.assertEqual(new_quotes, [], before.extra_quotes)
        # 真正新增的引号文本仍要在 delta 里出现
        out2 = output + ' "INVENTED".'
        *_rest, new_quotes2 = steps._fact_delta(ir, source, out2)
        self.assertIn("invented", new_quotes2)


class TestStructuredStrict(unittest.TestCase):
    """结构化 JSON 的 `text` 是逐字契约：整块/整行可放行，任意子串必须报。"""

    def test_structured_substring_is_still_extra(self):
        ir = _ir("alpha beta gamma delta epsilon zeta eta theta")
        bad = verify(ir, '{"text": "alpha beta"}')
        self.assertIn("alpha beta", bad.extra_quotes)
        # 同一子串在 prose 路径仍可按「一行/连续子串」放行（口径分工，不是漏洞）
        self.assertEqual(verify(ir, 'the text "alpha beta" is printed.').extra_quotes, [])

    def test_structured_whole_block_ignores_quotes_and_newlines(self):
        ir = _ir('"alpha" beta\ngamma delta')
        rep = verify(ir, '{"text": "\\"alpha\\" beta gamma delta"}')
        self.assertEqual(rep.extra_quotes, [])

    def test_structured_line_equality_is_allowed(self):
        ir = _ir("alpha beta gamma\ndelta epsilon")
        rep = verify(ir, '{"text": "alpha beta gamma"}')
        self.assertEqual(rep.extra_quotes, [])
        bad = verify(ir, '{"text": "totally fabricated words"}')
        self.assertIn("totally fabricated words", bad.extra_quotes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
