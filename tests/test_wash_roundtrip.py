# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/wash_roundtrip.py` 离线单测（W3 洗图 roundtrip 事实保全）。

全部离线、确定性、零 GPU/网络：

- 生成图清单：seed=42 过滤、run.json 映射优先、文件名 imgNN 兜底、id 冲突/缺源 IR 报错；
- 事实 diff：lost/extra 对称差、按类计数、文字实体单列、hard/soft 分开；
- 分母为 0 的率必须是 None（不写 0/1）——与 G7 口径一致；
- 失败图单列且不进分母；
- CLI（images → diff）端到端、确定性。

跑：python tests/test_wash_roundtrip.py
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


def _png(path: Path, data: bytes = b"\x89PNG-fake-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data + path.name.encode("utf-8"))


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


def _payload(img_id, entities, ok=True, category="单人特写") -> dict:
    return {
        "schema": "g7-caption/1", "model_key": "B-9B", "image_id": img_id,
        "category": category, "ok": ok,
        "error": None if ok else "ReverseError: JSON 没有闭合",
        "ir": ({"ir_version": "0.1", "task": "t2i", "canvas": {},
                "entities": entities} if ok else None),
    }


def source_entities() -> list:
    return [_entity("e1", age=28, color="red", anchor="center"),
            _entity("t1", kind="text", label="sign", age=None, color=None,
                    anchor=None, text="HELLO")]


def reverse_entities() -> list:
    return [_entity("e1", age=30, color="blue", anchor="center"),
            _entity("t1", kind="text", label="sign", age=None, color=None,
                    anchor=None, text="BYE")]


def make_env(tmp: Path, imgs=("img01", "img02")) -> dict:
    """G7 清单 + 源 IR + W1 生成图 + run.json（seed=42）。"""
    g7_entries = []
    for gid in imgs:
        src = tmp / "g7src" / ("%s.png" % gid)
        _png(src)
        g7_entries.append({"id": gid, "category": "单人特写",
                           "path": str(src), "sha256": W.sha256_file(src)})
    g7_images = tmp / "g7" / "images.json"
    _write_json(g7_images, {"images": g7_entries})

    src_root = tmp / "round2" / "B-9B"
    for gid in imgs:
        _write_json(src_root / ("%s.json" % gid), _payload(gid, source_entities()))

    images_dir = tmp / "wash" / "moss_q4_round1" / "images" / "moss-image-q4"
    _png(images_dir / "portrait_a__seed42.png")      # 无 imgNN → 依赖 run.json 映射
    for gid in imgs[1:]:
        _png(images_dir / ("%s__seed42.png" % gid))  # 文件名兜底
    _png(images_dir / "img01__seed7.png")            # 非 seed42 → 必须跳过
    run_json = tmp / "wash" / "moss_q4_round1" / "run.json"
    entries = [
        {"id": imgs[0], "seed": 42,
         "path": str(images_dir / "portrait_a__seed42.png"), "elapsed_s": 12.0},
        {"id": imgs[0], "seed": 7,
         "path": str(images_dir / "img01__seed7.png"), "elapsed_s": 99.0},
    ]
    for gid in imgs[1:]:
        entries.append({"id": gid, "seed": 42,
                        "path": str(images_dir / ("%s__seed42.png" % gid)),
                        "elapsed_s": 12.5})
    _write_json(run_json, {"plan": {"backend": "moss-image-q4"}, "images": entries})
    return {"tmp": tmp, "g7_images": g7_images, "source_root": src_root,
            "images_dir": images_dir, "run_json": run_json}


# ─────────────────────────────────────────────────────────────
# 1) 生成图清单
# ─────────────────────────────────────────────────────────────

class TestBuildImages(unittest.TestCase):
    def test_seed42_filter_run_json_mapping_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            env = make_env(Path(d))
            data = W.build_images(env["run_json"], env["images_dir"],
                                  env["g7_images"], env["source_root"])
            self.assertEqual(data["image_count"], 2)
            self.assertEqual([i["id"] for i in data["images"]], ["img01", "img02"])
            first, second = data["images"]
            self.assertEqual(first["name"], "portrait_a__seed42.png")  # run.json 映射
            self.assertEqual(second["name"], "img02__seed42.png")     # 文件名兜底
            self.assertEqual(first["seed"], 42)
            self.assertEqual(first["category"], "单人特写")
            self.assertEqual(first["sha256"],
                             W.sha256_file(env["images_dir"] / first["name"]))
            self.assertTrue(Path(first["source_image"]).is_file())
            self.assertTrue(Path(first["source_json"]).is_file())
            self.assertIn("img01__seed7.png", data["skipped_other_seed"])

    def test_unmapped_or_missing_source_is_actionable_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            env = make_env(tmp, imgs=("img01", "img02"))
            _png(env["images_dir"] / "mystery__seed42.png")   # 无 id 映射
            with self.assertRaises(SystemExit) as ctx:
                W.build_images(env["run_json"], env["images_dir"],
                               env["g7_images"], env["source_root"])
            self.assertIn("无法映射", str(ctx.exception))

            (env["images_dir"] / "mystery__seed42.png").unlink()
            (env["source_root"] / "img02.json").unlink()      # 缺源 IR
            with self.assertRaises(SystemExit) as ctx:
                W.build_images(env["run_json"], env["images_dir"],
                               env["g7_images"], env["source_root"])
            self.assertIn("缺源 IR", str(ctx.exception))

    def test_missing_w1_dir_is_actionable(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with self.assertRaises(SystemExit) as ctx:
                W.build_images(tmp / "nope.json", tmp / "nope-images",
                               tmp / "nope.json", tmp / "nope")
            self.assertIn("找不到 W1 生成图目录", str(ctx.exception))


# ─────────────────────────────────────────────────────────────
# 2) 事实 diff
# ─────────────────────────────────────────────────────────────

class TestCompare(unittest.TestCase):
    def _ir(self, entities):
        from moss_llm.smith.ir import IR
        return IR.from_dict({"ir_version": "0.1", "task": "t2i",
                             "canvas": {}, "entities": entities})

    def test_lost_extra_by_kind_and_text_separated(self):
        row = W.compare_irs(self._ir(source_entities()),
                            self._ir(reverse_entities()), image_id="img01")
        lost = {(f["kind"], f["value"]) for f in row["lost"]}
        extra = {(f["kind"], f["value"]) for f in row["extra"]}
        self.assertIn(("age", "28"), lost)
        self.assertIn(("color", "red"), lost)
        self.assertIn(("text", "HELLO"), lost)
        self.assertIn(("age", "30"), extra)
        self.assertIn(("color", "blue"), extra)
        self.assertIn(("text", "BYE"), extra)
        self.assertEqual(row["lost_count"], 3)
        self.assertEqual(row["extra_count"], 3)
        self.assertEqual(row["lost_by_kind"]["text"], 1)
        self.assertEqual(row["text_lost"], ["HELLO"])
        self.assertEqual(row["text_extra"], ["BYE"])
        self.assertEqual(row["source_facts"], 4)       # text/age/anchor/color
        self.assertEqual(row["lost_rate"], 0.75)       # 3/4，分母是源事实
        # text 与 age 是 hard：改了就错；anchor/color 是 soft
        self.assertEqual(row["hard_lost_count"], 2)
        self.assertFalse(row["ok"])

    def test_zero_denominator_is_none_not_zero(self):
        empty = self._ir([])
        row = W.compare_irs(empty, empty, image_id="img00")
        self.assertEqual(row["source_facts"], 0)
        self.assertIsNone(row["lost_rate"])
        self.assertIsNone(row["extra_rate"])
        self.assertTrue(row["ok"])


# ─────────────────────────────────────────────────────────────
# 3) 汇总（失败图单列、不进分母）
# ─────────────────────────────────────────────────────────────

class TestSummary(unittest.TestCase):
    def _images(self, tmp: Path, imgs) -> list:
        env = make_env(tmp, imgs=imgs)
        return W.build_images(env["run_json"], env["images_dir"],
                              env["g7_images"], env["source_root"])["images"]

    def test_failed_reverse_is_listed_and_excluded_from_denominator(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            images = self._images(tmp, ("img01", "img02", "img03"))
            rev_root = tmp / "reverse"
            _write_json(rev_root / "img01.json", _payload("img01", reverse_entities()))
            _write_json(rev_root / "img02.json", _payload("img02", [], ok=False))
            # img03 缺记录 → 必须单列
            summary = W.build_summary(images, tmp / "round2" / "B-9B", rev_root)
            self.assertEqual(summary["stage"]["images"], 3)
            self.assertEqual(summary["stage"]["diff_ok"], 1)
            self.assertEqual(summary["stage"]["reverse_ok"], 1)
            self.assertEqual(summary["stage"]["reverse_failed"], 2)
            self.assertEqual(len(summary["failed"]), 2)
            self.assertEqual({f["id"] for f in summary["failed"]}, {"img02", "img03"})
            # 分母只算成功再反推的 img01
            self.assertEqual(summary["totals"]["source_facts"], 4)
            self.assertEqual(summary["totals"]["lost"], 3)
            self.assertEqual(summary["totals"]["lost_rate"], 0.75)
            self.assertEqual(len(summary["typical_lost"]), 3)
            self.assertIn("失败图", W.format_report(summary))

    def test_zero_fact_summary_rate_is_none_and_reported_n_a(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            env = make_env(tmp, imgs=("img01",))
            images = W.build_images(env["run_json"], env["images_dir"],
                                    env["g7_images"], env["source_root"])["images"]
            _write_json(env["source_root"] / "img01.json", _payload("img01", []))
            rev_root = tmp / "reverse"
            _write_json(rev_root / "img01.json", _payload("img01", []))
            summary = W.build_summary(images, env["source_root"], rev_root)
            self.assertEqual(summary["totals"]["source_facts"], 0)
            self.assertIsNone(summary["totals"]["lost_rate"])
            self.assertIsNone(summary["totals"]["extra_rate"])
            self.assertIn("分母 0", W.format_report(summary))


# ─────────────────────────────────────────────────────────────
# 4) CLI 端到端 + 确定性
# ─────────────────────────────────────────────────────────────

class TestCli(unittest.TestCase):
    def test_images_then_diff_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            env = make_env(tmp)
            images_out = tmp / "out" / "images.json"
            reverse_root = tmp / "out" / "reverse" / "B-9B"
            summary_out = tmp / "out" / "summary.json"
            for gid in ("img01", "img02"):
                _write_json(reverse_root / ("%s.json" % gid),
                            _payload(gid, reverse_entities()))
            rc = W.main(["images", "--run-json", str(env["run_json"]),
                         "--images-dir", str(env["images_dir"]),
                         "--g7-images", str(env["g7_images"]),
                         "--source-json-root", str(env["source_root"]),
                         "--out", str(images_out)])
            self.assertEqual(rc, 0)
            first = images_out.read_bytes()
            rc = W.main(["images", "--run-json", str(env["run_json"]),
                         "--images-dir", str(env["images_dir"]),
                         "--g7-images", str(env["g7_images"]),
                         "--source-json-root", str(env["source_root"]),
                         "--out", str(images_out)])
            self.assertEqual(rc, 0)
            self.assertEqual(first, images_out.read_bytes())   # 确定性
            rc = W.main(["diff", "--images", str(images_out),
                         "--source-json-root", str(env["source_root"]),
                         "--reverse-json-root", str(reverse_root),
                         "--out", str(summary_out)])
            self.assertEqual(rc, 0)
            summary = json.loads(summary_out.read_text(encoding="utf-8"))
            self.assertEqual(summary["schema"], "wash-roundtrip-summary/1")
            self.assertEqual(summary["stage"]["diff_ok"], 2)
            self.assertEqual(len(summary["rows"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
