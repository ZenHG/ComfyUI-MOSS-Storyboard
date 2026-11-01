# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/g7_round2.py` 的离线单测（零模型、零网络、零 GPU）。

跑：
    python tests/test_g7_round2.py
    python -m unittest tests.test_g7_round2 -v

覆盖两轮对比器的四条纪律：
1. 四项指标（材质覆盖率 / 物体编年龄幻觉 / 跑批失败率 / 文字两遍互证）算得对，
   且带**方向+幅度+分母**；
2. **分母为 0 报 n/a，绝不报 1.0**（记忆总账 §9.7 的坑）；
3. 采样参数/提示词 sha 不一致要**出声**（不是静默对齐）；
4. 第一轮产物 manifest 的改动/缺失必须被发现。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.clauses import build_clauses, render_prose   # noqa: E402
from moss_llm.smith.ir import IR                                  # noqa: E402

from tools.g7_round2 import (build_round, collect_manifest, compare,  # noqa: E402
                             format_markdown, material_rows, verify_manifest,
                             write_manifest, _pp)

IMAGES = [
    {"id": "img01", "category": "材质与反光"},
    {"id": "img02", "category": "多人互动"},
]


def make_payload(entities, canvas="single", ok=True, calls=None,
                 model="A-4B", age_gate=None) -> dict:
    ir = IR.from_dict({"task": "t2i",
                       "canvas": {"aspect_ratio": "1:1", "canvas_type": canvas},
                       "entities": entities})
    payload = {
        "model_key": model, "ok": ok,
        "ir": ir.to_dict(),
        "caption": render_prose(build_clauses(ir)) if ok else None,
        "calls": calls or [{"name": "scene_record", "finish_reason": "stop"}],
        "report": {"age_gate": age_gate or []},
    }
    if not ok:
        payload["error"] = "ReverseError: JSON 没有闭合（缺右括号）"
        payload["calls"] = calls or [{"name": "scene_record", "finish_reason": "length",
                                      "max_tokens": 8192}]
    return payload


def write_round(root, payloads: dict) -> None:
    """payloads: {(model, img_id): payload}"""
    root = Path(root)
    for (model, img_id), payload in payloads.items():
        d = root / model
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % img_id)).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def object_entity(material="brushed steel", eid="e1") -> dict:
    return {"id": eid, "kind": "object", "label": "kettle", "importance": 0.8,
            "descriptors": {"material": material}, "spatial": {"anchor": "center"}}


def mis_kind_entity(material="wood", age=80, eid="e1") -> dict:
    """物体被标成 character：无人物字段、带物体字段（第一轮实测的主要缺陷形态）。"""
    return {"id": eid, "kind": "character", "label": "coffee grinder",
            "importance": 0.8, "age_years": age,
            "descriptors": {"material": material}, "spatial": {"anchor": "center"}}


def human_entity(eid="e2", age=28) -> dict:
    return {"id": eid, "kind": "character", "label": "woman", "importance": 0.8,
            "age_years": age,
            "descriptors": {"gender": "woman", "hair": "black"},
            "spatial": {"anchor": "left"}}


class TestMaterial(unittest.TestCase):
    def test_material_coverage_and_delta(self):
        """r1 物体被标 character（材质不渲染）→ r2 标 object（渲染）= 覆盖率 0%→100%。"""
        with tempfile.TemporaryDirectory() as td:
            r1, r2 = Path(td) / "r1", Path(td) / "r2"
            write_round(r1, {("A-4B", "img01"): make_payload([mis_kind_entity()])})
            write_round(r2, {("A-4B", "img01"): make_payload([object_entity()])})
            b1, b2 = build_round(r1, IMAGES, ["A-4B"]), build_round(r2, IMAGES, ["A-4B"])
            cmp = compare(b1, b2, IMAGES, ["A-4B"])
        mat = cmp["material"]["A-4B"]["__all__"]
        self.assertEqual((mat["r1"]["bound"], mat["r1"]["applicable"]), (0, 1))
        self.assertEqual((mat["r2"]["bound"], mat["r2"]["applicable"]), (1, 1))
        self.assertEqual(mat["direction"], "↑")
        self.assertEqual(mat["delta_pp"], 100.0)

    def test_material_by_category_denominators(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = Path(td) / "r1"
            write_round(r1, {("A-4B", "img01"): make_payload([object_entity()]),
                             ("A-4B", "img02"): make_payload([object_entity(eid="e9")])})
            build = build_round(r1, IMAGES, ["A-4B"])
            rows = material_rows(build, IMAGES, "A-4B")
        self.assertEqual(rows["材质与反光"]["applicable"], 1)
        self.assertEqual(rows["多人互动"]["applicable"], 1)
        self.assertEqual(rows["__all__"]["applicable"], 2)

    def test_zero_denominator_is_na_not_one(self):
        """没有材质属性时覆盖率必须是 None（n/a），不能写成 1.0。"""
        with tempfile.TemporaryDirectory() as td:
            r1, r2 = Path(td) / "r1", Path(td) / "r2"
            for d in (r1, r2):
                write_round(d, {("A-4B", "img01"): make_payload([human_entity()])})
            cmp = compare(build_round(r1, IMAGES, ["A-4B"]),
                          build_round(r2, IMAGES, ["A-4B"]), IMAGES, ["A-4B"])
            md = format_markdown(cmp)
        mat = cmp["material"]["A-4B"]["__all__"]
        self.assertIsNone(mat["r1"]["coverage"])
        self.assertIsNone(mat["r2"]["coverage"])
        self.assertEqual(mat["direction"], "n/a")
        self.assertIn("n/a（分母 0）", md)
        self.assertNotIn("100%（0/0）", md)


class TestObjectAge(unittest.TestCase):
    def test_object_age_hallucination_counted_only_for_mis_kind(self):
        """物体（无人物字段、有物体字段）带年龄 = 编年龄；真人带年龄不算。"""
        with tempfile.TemporaryDirectory() as td:
            r1 = Path(td) / "r1"
            write_round(r1, {("A-4B", "img01"): make_payload(
                [mis_kind_entity(age=80), human_entity()])})
            stats = build_round(r1, IMAGES, ["A-4B"])["stats"]["A-4B"]
        self.assertEqual(stats["object_like_characters"], 1)
        self.assertEqual(stats["object_age_hallucinations"], 1)
        self.assertEqual(stats["object_age_rate"], 1.0)

    def test_object_age_delta_direction(self):
        with tempfile.TemporaryDirectory() as td:
            r1, r2 = Path(td) / "r1", Path(td) / "r2"
            write_round(r1, {("A-4B", "img01"): make_payload([mis_kind_entity(age=80)])})
            write_round(r2, {("A-4B", "img01"): make_payload([object_entity()])})
            cmp = compare(build_round(r1, IMAGES, ["A-4B"]),
                          build_round(r2, IMAGES, ["A-4B"]), IMAGES, ["A-4B"])
        age = cmp["object_age"]["A-4B"]["with_age"]
        self.assertEqual((age["r1"], age["r2"], age["delta"]), (1, 0, -1))


class TestFailureRate(unittest.TestCase):
    def _round(self, td, name, payloads):
        root = Path(td) / name
        write_round(root, payloads)
        return root

    def test_failure_rate_counts_failed_and_missing(self):
        """失败 + 缺记录都算失败；分母是应有张数（不是落盘数）。"""
        with tempfile.TemporaryDirectory() as td:
            r1 = self._round(td, "r1", {("A-4B", "img01"): make_payload([human_entity()])})
            r2 = self._round(td, "r2", {
                ("A-4B", "img01"): make_payload([human_entity()], ok=False),
                ("A-4B", "img02"): make_payload([human_entity(eid="e3")]),
            })
            cmp = compare(build_round(r1, IMAGES, ["A-4B"]),
                          build_round(r2, IMAGES, ["A-4B"]), IMAGES, ["A-4B"])
        fail = cmp["failure"]["A-4B"]
        self.assertEqual(fail["expected"]["r1"], 2)
        self.assertEqual((fail["failed"]["r1"], fail["missing"]["r1"]), (0, 1))
        self.assertEqual(fail["rate"]["r1"], 0.5)
        self.assertEqual((fail["ok"]["r2"], fail["failed"]["r2"]), (1, 1))
        self.assertEqual(fail["rate"]["r2"], 0.5)
        self.assertEqual(fail["reasons"]["r2"].get("finish=length/JSON未闭合"), 1)

    def test_all_missing_round_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"          # 故意不创建
            cmp = compare(build_round(empty, IMAGES, ["A-4B"]),
                          build_round(empty, IMAGES, ["A-4B"]), IMAGES, ["A-4B"])
        fail = cmp["failure"]["A-4B"]
        self.assertEqual(fail["rate"]["r1"], 1.0)
        self.assertEqual(cmp["binding"]["A-4B"]["color"]["r1"]["coverage"], None)


class TestTextCrossPass(unittest.TestCase):
    def test_corroborated_text_rate_and_delta(self):
        ent = {"id": "t1", "kind": "text", "label": "headline", "importance": 0.9,
               "text_content": "开门", "spatial": {"anchor": "top-center"}}
        blocks = json.dumps({"blocks": [{"text": "开门", "where": "top-center"}]},
                            ensure_ascii=False)
        with_block = [
            {"name": "scene_record", "finish_reason": "stop"},
            {"name": "text_blocks", "finish_reason": "stop", "content": blocks},
        ]
        with tempfile.TemporaryDirectory() as td:
            r1 = Path(td) / "r1"
            r2 = Path(td) / "r2"
            write_round(r1, {("A-4B", "img01"): make_payload([ent])})
            write_round(r2, {("A-4B", "img01"): make_payload([ent], calls=with_block)})
            cmp = compare(build_round(r1, IMAGES, ["A-4B"]),
                          build_round(r2, IMAGES, ["A-4B"]), IMAGES, ["A-4B"])
        t = cmp["text_cross_pass"]["A-4B"]
        self.assertEqual((t["r1"]["corroborated"], t["r1"]["from_evidence"]), (0, 1))
        self.assertEqual((t["r2"]["corroborated"], t["r2"]["from_evidence"]), (1, 1))
        self.assertEqual(t["direction"], "↑")
        self.assertEqual(t["delta_pp"], 100.0)


class TestSettingsAndMarkdown(unittest.TestCase):
    def test_settings_mismatch_is_reported(self):
        s1 = {"settings": {"seed": 42}, "system_prompt_sha256": "a" * 64}
        s2 = {"settings": {"seed": 7}, "system_prompt_sha256": "b" * 64}
        with tempfile.TemporaryDirectory() as td:
            r1 = Path(td) / "r1"
            write_round(r1, {("A-4B", "img01"): make_payload([human_entity()])})
            cmp = compare(build_round(r1, IMAGES, ["A-4B"]),
                          build_round(r1, IMAGES, ["A-4B"]), IMAGES, ["A-4B"], s1, s2)
        joined = " ".join(cmp["warnings"])
        self.assertIn("采样参数不一致", joined)
        self.assertIn("system prompt sha", joined)

    def test_markdown_has_denominators_direction_and_pp(self):
        with tempfile.TemporaryDirectory() as td:
            r1, r2 = Path(td) / "r1", Path(td) / "r2"
            write_round(r1, {("A-4B", "img01"): make_payload([mis_kind_entity()]),
                             ("B-9B", "img01"): make_payload([mis_kind_entity()],
                                                             model="B-9B")})
            write_round(r2, {("A-4B", "img01"): make_payload([object_entity()]),
                             ("B-9B", "img01"): make_payload([object_entity()],
                                                             model="B-9B")})
            cmp = compare(build_round(r1, IMAGES, ["A-4B", "B-9B"]),
                          build_round(r2, IMAGES, ["A-4B", "B-9B"]),
                          IMAGES, ["A-4B", "B-9B"])
            md = format_markdown(cmp)
        self.assertIn("材质绑定覆盖率", md)
        self.assertIn("物体编年龄", md)
        self.assertIn("pp", md)
        self.assertIn("0/1", md)
        self.assertIn("1/1", md)

    def test_markdown_image_count_header_is_dynamic(self):
        """F2（lang-zh 复核）：表头「全部 N 图」必须跟实际 images 列表走。

        旧实现写死「全部 20 图」——用 60 图列表跑 compare 时表头会错。
        """
        images20 = list(IMAGES)
        images60 = [{"id": "img%02d" % i, "category": "单人特写"}
                    for i in range(1, 61)]
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            for images, expect in ((images20, "全部 2 图"), (images60, "全部 60 图")):
                cmp = compare(build_round(empty, images, ["A-4B"]),
                              build_round(empty, images, ["A-4B"]),
                              images, ["A-4B"])
                self.assertEqual(cmp["images"], len(images))
                self.assertIn(expect, format_markdown(cmp))

    def test_compare_has_all_four_metrics_for_both_models(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = Path(td) / "r1"
            write_round(r1, {("A-4B", "img01"): make_payload([human_entity()]),
                             ("B-9B", "img01"): make_payload([human_entity()],
                                                             model="B-9B")})
            cmp = compare(build_round(r1, IMAGES, ["A-4B", "B-9B"]),
                          build_round(r1, IMAGES, ["A-4B", "B-9B"]),
                          IMAGES, ["A-4B", "B-9B"])
        for model in ("A-4B", "B-9B"):
            for key in ("material", "object_age", "failure", "text_cross_pass"):
                self.assertIn(key, cmp)
                self.assertIn(model, cmp[key])


class TestManifest(unittest.TestCase):
    @staticmethod
    def _root(td: Path) -> Path:
        root = td / "g7"
        (root / "json" / "A-4B").mkdir(parents=True)
        (root / "images.json").write_text('{"images": []}', encoding="utf-8")
        (root / "json" / "A-4B" / "img01.json").write_text("{}", encoding="utf-8")
        return root

    @staticmethod
    def _snapshot(td: Path) -> tuple[Path, Path]:
        root = TestManifest._root(td)
        man = td / "manifest.sha256"
        write_manifest(collect_manifest(root=root), man)
        return root, man

    def test_manifest_roundtrip_ok(self):
        with tempfile.TemporaryDirectory() as td:
            _root, man = self._snapshot(Path(td))
            res = verify_manifest(man, root=Path(td) / "g7")
            files = collect_manifest(root=Path(td) / "g7")
        self.assertEqual(len(files), 2)
        self.assertTrue(res["ok"], res)

    def test_manifest_detects_changed_file(self):
        with tempfile.TemporaryDirectory() as td:
            root, man = self._snapshot(Path(td))
            (root / "json" / "A-4B" / "img01.json").write_text('{"x": 1}',
                                                              encoding="utf-8")
            res = verify_manifest(man, root=root)
        self.assertFalse(res["ok"])
        self.assertIn("json/A-4B/img01.json", res["changed"])

    def test_manifest_detects_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root, man = self._snapshot(Path(td))
            (root / "images.json").unlink()
            res = verify_manifest(man, root=root)
        self.assertFalse(res["ok"])
        self.assertIn("images.json", res["missing"])

    def test_verify_never_rewrites_manifest(self):
        """F1（lang-zh 复核）：verify 必须只读——篡改后连续两次 verify 都要报 changed。

        旧实现里 verify 会顺手把当前状态写回 manifest：第一次报 changed，第二次
        就「自愈」成 ok。证据链不能自己把自己治好。
        """
        with tempfile.TemporaryDirectory() as td:
            root, man = self._snapshot(Path(td))
            before = man.read_bytes()
            (root / "json" / "A-4B" / "img01.json").write_text('{"x": 1}',
                                                              encoding="utf-8")
            first = verify_manifest(man, root=root)
            mid = man.read_bytes()
            second = verify_manifest(man, root=root)
            after = man.read_bytes()
        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"], "第二次 verify 不应自愈")
        self.assertIn("json/A-4B/img01.json", second["changed"])
        self.assertEqual(before, mid, "verify 之后 manifest 被改写了（必须只读）")
        self.assertEqual(mid, after)


class TestPpHelper(unittest.TestCase):
    def test_directions(self):
        self.assertEqual(_pp(0.5, 1.0), ("↑", 50.0))
        self.assertEqual(_pp(1.0, 0.5), ("↓", -50.0))
        self.assertEqual(_pp(0.5, 0.5), ("=", 0.0))
        self.assertEqual(_pp(None, 0.5), ("n/a", None))
        self.assertEqual(_pp(0.5, None), ("n/a", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
