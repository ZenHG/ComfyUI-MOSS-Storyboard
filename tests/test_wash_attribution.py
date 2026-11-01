# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""W6 归因拆解离线单测（`tools/wash_roundtrip.py` 的 W6 扩展）。

全部离线、确定性、零 GPU/网络：

- `--models/--repeats` 解析与多臂计划（含 `B-9B:2,A-4B:1` 逐模型覆盖）；
- 三态归因：stable_lost / unstable_lost / cross_model_only_lost / never_lost；
- 失败图/臂单列、uncovered 图不进分母、分母 0 → null；
- `--retry-failed` 的重试计划与失败 id 收集；
- 旧默认行为（recaption 不给 --out-root、images/diff 默认值）不变。

跑：python tests/test_wash_attribution.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import wash_roundtrip as W  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 夹具
# ─────────────────────────────────────────────────────────────

def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _entity(eid, kind="character", label="man", age=28, color="red",
            anchor="center", text=None) -> dict:
    e = {"id": eid, "kind": kind, "label": label,
         "descriptors": ({"color": color} if color else {}),
         "spatial": ({"anchor": anchor} if anchor else {})}
    if age is not None:
        e["age_years"] = age
    if text is not None:
        e["text_content"] = text
    return e


def _payload(entities, ok=True, error=None) -> dict:
    return {"schema": "g7-caption/1", "model_key": "X", "image_id": "",
            "ok": ok, "error": error,
            "ir": ({"ir_version": "0.1", "task": "t2i", "canvas": {},
                    "entities": entities} if ok else None)}


def source_entities() -> list:
    return [_entity("e1", age=28, color="red", anchor="center"),
            _entity("t1", kind="text", label="sign", age=None, color=None,
                    anchor=None, text="HELLO")]


def b42_entities() -> list:
    """丢 age + text（其余保留）。"""
    return [_entity("e1", age=None, color="red", anchor="center"),
            _entity("t1", kind="text", label="sign", age=None, color=None,
                    anchor=None, text=None)]


def b43_entities() -> list:
    """只丢 age（text 保留）→ 同一事实在 B 两遍里一丢一留 = unstable。"""
    return [_entity("e1", age=None, color="red", anchor="center"),
            _entity("t1", kind="text", label="sign", age=None, color=None,
                    anchor=None, text="HELLO")]


def a42_entities() -> list:
    """丢 color（B 两遍都保留）→ cross_model_only。"""
    return [_entity("e1", age=28, color=None, anchor="center"),
            _entity("t1", kind="text", label="sign", age=None, color=None,
                    anchor=None, text="HELLO")]


def runs_fixture() -> dict:
    return {
        ("B-9B", 42): {"img01": _payload(b42_entities())},
        ("B-9B", 43): {"img01": _payload(b43_entities())},
        ("A-4B", 42): {"img01": _payload(a42_entities())},
    }


# ─────────────────────────────────────────────────────────────
# 1) 模型/重复解析 + 多臂计划
# ─────────────────────────────────────────────────────────────

class TestModelsAndPlan(unittest.TestCase):
    def test_parse_models_global_repeats_and_per_model_override(self):
        got = W.parse_models("A-4B,B-9B", 2)
        self.assertEqual([(m["model"], m["repeats"]) for m in got],
                         [("A-4B", 2), ("B-9B", 2)])
        got = W.parse_models("B-9B:2,A-4B:1", 1)
        self.assertEqual([(m["model"], m["repeats"]) for m in got],
                         [("B-9B", 2), ("A-4B", 1)])
        # 去重取最后一次
        got = W.parse_models("B-9B:1,B-9B:3", 1)
        self.assertEqual([(m["model"], m["repeats"]) for m in got], [("B-9B", 3)])

    def test_parse_models_errors_are_actionable(self):
        for spec in ("", "B-9B:x", "B-9B:0"):
            with self.assertRaises(SystemExit):
                W.parse_models(spec, 1)

    def test_plan_recaption_layout_and_order(self):
        arms = W.plan_recaption("B-9B:2,A-4B:1", 1, 42, "out/x")
        self.assertEqual([(a["model"], a["seed"]) for a in arms],
                         [("B-9B", 42), ("B-9B", 43), ("A-4B", 42)])
        self.assertEqual(str(arms[0]["json_root"]).replace("\\", "/"),
                         "out/x/B-9B/seed42")
        self.assertIn("run", arms[0]["run_dir"].parts)
        # 同输入 → 同计划（确定性）
        self.assertEqual(arms, W.plan_recaption("B-9B:2,A-4B:1", 1, 42, "out/x"))


# ─────────────────────────────────────────────────────────────
# 2) 三态归因
# ─────────────────────────────────────────────────────────────

class TestAttribution(unittest.TestCase):
    def test_three_states_by_kind_and_rates(self):
        images = [{"id": "img01", "category": "单人特写"}]
        src = {"img01": _payload(source_entities())}
        summary = W.attribute(src, runs_fixture(), images, b_model="B-9B",
                              a_models=("A-4B",))
        counts = summary["totals"]["class_counts"]
        self.assertEqual(counts["stable_lost"], 1)               # age
        self.assertEqual(counts["unstable_lost"], 1)             # text
        self.assertEqual(counts["cross_model_only_lost"], 1)     # color
        self.assertEqual(counts["never_lost"], 1)                # anchor
        self.assertEqual(summary["totals"]["facts"], 4)
        self.assertEqual(summary["totals"]["rates"]["stable_lost"], 0.25)
        # 按 kind：age=stable, text=unstable, color=cross-model, anchor=never
        self.assertEqual(summary["by_kind"]["age"]["stable_lost"], 1)
        self.assertEqual(summary["by_kind"]["text"]["unstable_lost"], 1)
        self.assertEqual(summary["by_kind"]["color"]["cross_model_only_lost"], 1)
        self.assertEqual(summary["by_kind"]["anchor"]["never_lost"], 1)
        # hard 事实：age+text 两条，stable 1 / unstable 1
        self.assertEqual(summary["totals"]["hard"]["facts"], 2)
        self.assertEqual(summary["totals"]["hard"]["class_counts"]["stable_lost"], 1)
        # 臂成功数
        self.assertEqual(summary["arms"]["ok"],
                         {"A-4B@42": 1, "B-9B@42": 1, "B-9B@43": 1})

    def test_failed_arm_is_listed_and_uncovered_excluded(self):
        images = [{"id": "img01"}, {"id": "img02"}]
        src = {"img01": _payload(source_entities()),
               "img02": _payload(source_entities())}
        runs = runs_fixture()
        runs[("B-9B", 42)]["img02"] = _payload([], ok=False, error="ReverseError: 重复循环")
        runs[("B-9B", 43)]["img02"] = _payload([], ok=False, error="ReverseError: 重复循环")
        summary = W.attribute(src, runs, images, b_model="B-9B",
                              a_models=("A-4B",))
        self.assertEqual(summary["images"]["covered"], 1)
        self.assertEqual(summary["images"]["total"], 2)
        self.assertEqual([u["id"] for u in summary["images"]["uncovered"]], ["img02"])
        self.assertIn("B-9B@42", summary["arms"]["failed"])
        self.assertIn("重复循环", summary["arms"]["failed"]["B-9B@42"][0]["error"])
        self.assertNotIn("img02", [r["id"] for r in summary["by_image"]])

    def test_zero_denominator_rates_are_none(self):
        images = [{"id": "img00"}]
        src = {"img00": _payload([])}                 # 源 IR 零事实
        summary = W.attribute(src, {("B-9B", 42): {"img00": _payload([])}},
                              images, b_model="B-9B", a_models=("A-4B",))
        self.assertEqual(summary["totals"]["facts"], 0)
        for k in ("stable_lost", "unstable_lost", "cross_model_only_lost", "never_lost"):
            self.assertIsNone(summary["totals"]["rates"][k])
        self.assertEqual(summary["images"]["covered"], 1)   # 图被覆盖但事实为 0


# ─────────────────────────────────────────────────────────────
# 3) 失败收集 / 重试计划 / 默认行为
# ─────────────────────────────────────────────────────────────

class TestRetryAndDefaults(unittest.TestCase):
    def test_collect_failed_ids_marks_missing_and_not_ok(self):
        runs = {("B-9B", 42): {"img01": _payload([]),
                               "img02": _payload([], ok=False, error="x")},
                ("B-9B", 43): {"img01": _payload([])}}
        got = W.collect_failed_ids(runs, ["img01", "img02", "img03"])
        self.assertEqual(got, ["img02", "img03"])

    def test_retry_plan_default_seeds_and_models(self):
        plan = W.retry_plan(["img16"], "B-9B,A-4B", base_seed=42, repeats=2,
                            out_root="out/attr")
        self.assertEqual(sorted({(p["model"], p["seed"]) for p in plan}),
                         [("A-4B", 44), ("A-4B", 45), ("B-9B", 44), ("B-9B", 45)])
        self.assertTrue(all(p["image_id"] == "img16" for p in plan))
        self.assertIn("retry", str(plan[0]["json_root"]))
        plan2 = W.retry_plan(["img16"], "B-9B", 42, 2, seeds=[50], out_root="out/attr")
        self.assertEqual([(p["model"], p["seed"]) for p in plan2], [("B-9B", 50)])

    def test_legacy_defaults_unchanged(self):
        ap = W.build_parser()
        # images/diff 默认路径保持 W3 原值
        a = ap.parse_args(["images"])
        self.assertEqual(a.out, str(W.DEFAULT_IMAGES_OUT))
        b = ap.parse_args(["diff"])
        self.assertEqual(b.out, str(W.DEFAULT_SUMMARY))
        self.assertEqual(b.reverse_json_root, str(W.DEFAULT_REVERSE_ROOT))
        # recaption 默认仍走旧单臂布局（out_root 空）
        d = ap.parse_args(["recaption"])
        self.assertEqual(d.out_root, "")
        self.assertEqual(d.repeats, 1)
        self.assertEqual(d.models, "")
        self.assertFalse(d.retry_failed)
        # attribution 默认
        e = ap.parse_args(["attribution"])
        self.assertEqual(e.b_model, W.MODEL_KEY)
        self.assertEqual(e.a_models, "A-4B")


# ─────────────────────────────────────────────────────────────
# 4) CLI 端到端（attribution 读写）
# ─────────────────────────────────────────────────────────────

class TestNestedLayout(unittest.TestCase):
    def test_scan_arm_payloads_supports_g7_caption_nested_model_dir(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            arm = W.plan_recaption("B-9B:1", 1, 42, tmp / "reverse")[0]
            # g7_caption 会在 --json-root 下再拼一层 <model>/
            _write_json(Path(arm["json_root"]) / "B-9B" / "img01.json",
                        _payload(b42_entities()))
            runs = W.scan_arm_payloads([arm])
            self.assertEqual(len(runs[("B-9B", 42)]), 1)
            self.assertTrue(runs[("B-9B", 42)]["img01"]["ok"])

    def test_discover_arms_finds_nested_and_skips_retry(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _write_json(tmp / "reverse" / "B-9B" / "seed42" / "B-9B" / "img01.json",
                        _payload(b42_entities()))
            _write_json(tmp / "reverse" / "retry" / "A-4B" / "seed44" / "A-4B" / "img02.json",
                        _payload(a42_entities()))
            arms = W.discover_arms(tmp / "reverse")
            self.assertEqual([(a["model"], a["seed"]) for a in arms], [("B-9B", 42)])
            runs = W.scan_arm_payloads(arms)
            self.assertEqual(len(runs[("B-9B", 42)]), 1)


class TestCliAttribution(unittest.TestCase):
    def test_cli_reads_multi_arm_layout_and_writes_summary(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            images = tmp / "images.json"
            _write_json(images, {"images": [{"id": "img01", "category": "单人特写"}]})
            src_root = tmp / "src"
            _write_json(src_root / "img01.json", _payload(source_entities()))
            rev_root = tmp / "reverse"
            _write_json(rev_root / "B-9B" / "seed42" / "img01.json",
                        _payload(b42_entities()))
            _write_json(rev_root / "B-9B" / "seed43" / "img01.json",
                        _payload(b43_entities()))
            _write_json(rev_root / "A-4B" / "seed42" / "img01.json",
                        _payload(a42_entities()))
            out = tmp / "attribution.json"
            rc = W.main(["attribution", "--images", str(images),
                         "--source-json-root", str(src_root),
                         "--reverse-root", str(rev_root), "--out", str(out)])
            self.assertEqual(rc, 0)
            summary = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(summary["schema"], "wash-roundtrip-attribution/1")
            self.assertEqual(summary["totals"]["facts"], 4)
            self.assertEqual(summary["totals"]["class_counts"]["stable_lost"], 1)
            self.assertEqual(summary["arms"]["ok"],
                             {"A-4B@42": 1, "B-9B@42": 1, "B-9B@43": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)

