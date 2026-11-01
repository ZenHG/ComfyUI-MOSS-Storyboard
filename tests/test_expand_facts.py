# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/expand_facts.py` 的离线单测（零 GPU、零网络、零 llama-server）。

测三件事：
1. 桩的边界：只从 CURRENT DESCRIPTION 取正文（不把 `Aim for 150-300 words` 抄回去）；
2. 事实对账管线**非空转**：正常桩 lost=0；丢句桩会被判 lost；编造文字桩会被判 extra；
3. 统计口径：ok=false 单列且不进分母、运行异常单列、同输入同代码产物逐字节相同。

跑：python tests/test_expand_facts.py -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "expand_facts", ROOT / "tools" / "expand_facts.py")
ef = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ef)

FIXTURE = ROOT / "tests" / "fixtures" / "smith" / "hero-storyboard.ir.json"


def _ir_dict() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _unit(tmp: Path, arm: str, image_id: str, ok: bool = True,
          ir: dict | None = None) -> Path:
    d = tmp / arm
    d.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "g7-caption/1", "arm": arm, "image_id": image_id,
               "ok": ok, "ir": _ir_dict() if ir is None else ir}
    p = d / (image_id + ".json")
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


class TestStubBoundary(unittest.TestCase):
    def test_extract_current_does_not_bleed_length_or_tail_instructions(self):
        user = ("KEEP THESE EXACTLY (fixed facts):\n- a\n\nOPEN SLOTS (fill only these):\n- b\n"
                "\nCURRENT DESCRIPTION:\nA red fox in snow. The camera pushes in.\n\n"
                "Aim for 150-300 words total; do not pad with adjectives.\n\n"
                "Return the full description with only the OPEN slots filled.")
        got = ef._extract_current(user)
        self.assertEqual(got, "A red fox in snow. The camera pushes in.")
        self.assertNotIn("150-300", got)

    def test_stub_reorder_preserves_sentence_set(self):
        st = ef.StubLLM()
        out = st.chat("sys", "\nCURRENT DESCRIPTION:\nA red fox. Snow falls. Dawn breaks.\n\nReturn it.")
        self.assertEqual(sorted(s.strip() for s in out.split(".") if s.strip()),
                         sorted(["A red fox", "Snow falls", "Dawn breaks"]))
        self.assertEqual(len(st.calls), 1)


class TestRunUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="w2-ef-"))
        self.units = ef.load_units(self.tmp)
        self.reg = ef.Registry.load()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stub_run_preserves_facts_for_both_groups(self):
        _unit(self.tmp, "A-4B", "img01")
        units = ef.load_units(self.tmp)
        self.assertEqual(len(units), 1)
        for group in ("all", "rewrite"):
            rec = ef.run_unit(units[0], group, self.reg)
            self.assertEqual(rec["status"], "ok", rec)
            self.assertEqual(rec["lost_n"], 0, rec["lost"])
            self.assertEqual(rec["extra_n"], 0, rec["extra"])
            self.assertEqual(rec["llm"], ef.StubLLM.name)
            self.assertEqual(rec["band"], [150, 300])
            self.assertIn("prompt_sha256", rec)
            self.assertGreater(rec["words"], 0)
            self.assertGreater(rec["chars"], 0)
            self.assertGreater(rec["tokens"], 0)

    def test_drop_stub_is_caught_as_lost(self):
        _unit(self.tmp, "A-4B", "img01")
        unit = ef.load_units(self.tmp)[0]
        rec = ef.run_unit(unit, "rewrite", self.reg, llm_factory=ef._DropStub)
        self.assertEqual(rec["status"], "ok")
        self.assertGreater(rec["lost_n"], 0, "丢句桩必须被 roundtrip.diff 判出 lost")

    def test_invent_stub_is_caught_as_extra(self):
        _unit(self.tmp, "A-4B", "img01")
        unit = ef.load_units(self.tmp)[0]
        rec = ef.run_unit(unit, "rewrite", self.reg, llm_factory=ef._InventStub)
        self.assertEqual(rec["status"], "ok")
        self.assertGreater(rec["extra_n"], 0, "编造的文字必须被 roundtrip.diff 判出 extra")

    def test_ok_false_is_skipped(self):
        _unit(self.tmp, "A-4B", "img11", ok=False)
        unit = ef.load_units(self.tmp)[0]
        rec = ef.run_unit(unit, "all", self.reg)
        self.assertEqual(rec["status"], "skipped")
        self.assertIn("ok=false", rec["reason"])

    def test_bad_ir_is_failed_with_error(self):
        _unit(self.tmp, "B-9B", "img01", ir="not-a-dict")
        unit = ef.load_units(self.tmp)[0]
        rec = ef.run_unit(unit, "all", self.reg)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("error", rec)


    def test_fixed_guard_default_off_records_no_fallback(self):
        _unit(self.tmp, "A-4B", "img01")
        unit = ef.load_units(self.tmp)[0]
        rec = ef.run_unit(unit, "all", self.reg)
        self.assertEqual(rec["status"], "ok")
        self.assertFalse(rec["guard_fallback"])
        self.assertEqual(rec["pre_guard_lost"], [])
        self.assertEqual(rec["guard_reason"], "")

    def test_fixed_guard_falls_back_on_drop_stub(self):
        _unit(self.tmp, "A-4B", "img01")
        unit = ef.load_units(self.tmp)[0]
        rec = ef.run_unit(unit, "all", self.reg, llm_factory=ef._DropStub,
                          fixed_guard=True)
        self.assertEqual(rec["status"], "ok")
        self.assertTrue(rec["guard_fallback"], rec)
        self.assertTrue(rec["pre_guard_lost"], rec)
        self.assertIn("回退确定性草稿", rec["guard_reason"])
        self.assertEqual(rec["lost_n"], 0, rec["lost"])


class TestSummaryAndRepro(unittest.TestCase):
    def test_summary_denominators_exclude_skipped_and_failed(self):
        rows = [
            {"arm": "A-4B", "image_id": "img01", "unit": "A-4B/img01", "group": "all",
             "status": "ok", "lost": [], "extra": [], "lost_n": 0, "extra_n": 0,
             "lost0": True, "words": 100, "tokens": 90, "chars": 500,
             "under_band": True, "over_band": False},
            {"arm": "A-4B", "image_id": "img02", "unit": "A-4B/img02", "group": "all",
             "status": "ok", "lost": [["color", "blue"]], "extra": [["text", "X"]],
             "lost_n": 1, "extra_n": 1, "lost0": False, "words": 120, "tokens": 100,
             "chars": 600, "under_band": True, "over_band": False},
            {"arm": "A-4B", "image_id": "img11", "unit": "A-4B/img11", "group": "all",
             "status": "skipped", "reason": "ok=false"},
            {"arm": "B-9B", "image_id": "img01", "unit": "B-9B/img01", "group": "all",
             "status": "failed", "error": "boom"},
        ]
        s = ef.summarize(rows, ("all",))
        o = s["groups"]["all"]["overall"]
        self.assertEqual(o["denominator_ok"], 2)
        self.assertEqual(o["lost0"], 1)
        self.assertAlmostEqual(o["lost0_ratio"], 0.5)
        self.assertEqual(o["skipped_ok_false"], 1)
        self.assertEqual(o["failed_run"], 1)
        self.assertEqual(o["lost_kinds"], {"color": 1})
        self.assertEqual(o["extra_kinds"], {"text": 1})
        self.assertEqual(s["groups"]["all"]["per_arm"]["A-4B"]["denominator_ok"], 2)
        self.assertEqual(s["groups"]["all"]["per_arm"]["B-9B"]["denominator_ok"], 0)
        self.assertEqual(len(s["groups"]["all"]["extra_details"]), 1)

    def test_main_is_byte_reproducible_and_lists_failures(self):
        tmp = Path(tempfile.mkdtemp(prefix="w2-ef-root-"))
        try:
            _unit(tmp, "A-4B", "img01")
            _unit(tmp, "A-4B", "img11", ok=False)
            out1, out2 = tmp / "o1", tmp / "o2"
            argv = ["--json-root", str(tmp), "--out", str(out1), "--groups", "all",
                    "--quiet"]
            self.assertEqual(ef.main(argv), 0)
            self.assertEqual(ef.main(["--json-root", str(tmp), "--out", str(out2),
                                      "--groups", "all", "--quiet"]), 0)
            a = (out1 / "results.json").read_bytes()
            b = (out2 / "results.json").read_bytes()
            self.assertEqual(a, b, "同输入同代码 results.json 必须逐字节相同")
            summary = json.loads((out1 / "summary.json").read_text(encoding="utf-8"))
            o = summary["groups"]["all"]["overall"]
            self.assertEqual(o["denominator_ok"], 1)
            self.assertEqual(o["skipped_ok_false"], 1)
            failures = json.loads((out1 / "failures.json").read_text(encoding="utf-8"))
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["status"], "skipped")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_band_flags_follow_actual_words(self):
        tmp = Path(tempfile.mkdtemp(prefix="w2-ef-band-"))
        try:
            _unit(tmp, "A-4B", "img01")
            unit = ef.load_units(tmp)[0]
            rec = ef.run_unit(unit, "all", ef.Registry.load())
            band = rec["band"]
            self.assertEqual(band, [150, 300])
            self.assertEqual(rec["under_band"], rec["words"] < band[0])
            self.assertEqual(rec["over_band"], rec["words"] > band[1])
            self.assertGreater(rec["tokens"], 0)
            self.assertGreater(rec["chars"], 0)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestRealLLMAndReview(unittest.TestCase):
    """`--llm real` 是可选 spot-check 路径：默认必须仍是桩、不连网/不起服务。"""

    def test_default_llm_is_stub_and_does_not_start_server(self):
        import argparse
        args = argparse.Namespace(llm="stub")
        mode, factory, closer = ef.make_llm(args)
        self.assertEqual(mode, "stub")
        self.assertIsNone(closer)
        self.assertEqual(factory().name, ef.StubLLM.name)

    def test_real_llm_without_model_errors_before_any_server(self):
        import argparse
        with self.assertRaises(SystemExit) as ctx:
            ef.make_llm(argparse.Namespace(llm="real", model=""))
        self.assertIn("--model", str(ctx.exception))

    def test_review_md_has_fields_and_attribution(self):
        tmp = Path(tempfile.mkdtemp(prefix="w2-ef-review-"))
        try:
            _unit(tmp, "A-4B", "img01")
            unit = ef.load_units(tmp)[0]
            fake_summary = {"groups": {"all": {
                "extra_details": [{"unit": "A-4B/img01", "kind": "text",
                                   "value": '"INVENTED-TEXT"'}],
                "lost_details": [{"unit": "A-4B/img01",
                                  "lost": [["color", "purple"]]}],
            }}}
            out = tmp / "out"
            events = ef.build_review(fake_summary, [unit], ef.Registry.load(), out,
                                     ef.StubLLM, limit=5)
            self.assertEqual({e["event_kind"] for e in events}, {"extra", "lost"})
            self.assertIn("text", {e["kind"] for e in events if e["event_kind"] == "extra"})
            md = (out / "review.md").read_text(encoding="utf-8")
            self.assertIn("人工可审清单", md)
            self.assertIn("A-4B/img01", md)
            self.assertIn("归因猜测", md)
            self.assertIn("源 IR 字段", md)
            # 桩不可能新增文字 => extra 应被标「待人工」而不是断言模型编造
            self.assertTrue(any("待人工" in e["guess"] for e in events
                                if e["event_kind"] == "extra"))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
