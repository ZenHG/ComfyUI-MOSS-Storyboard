# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/g7_adjudicate.py` 单测：双盲判读协议工具（packet / merge / calibrate）。

全部离线、确定性、无 GPU/网络。覆盖 task-22 的四条硬要求：

- packet 不泄漏机检结论（机检数字进不了判读包）；
- 构造分歧必被发现（能定位到 图/模型/claim id）；
- 一致性过低必红（κ<0.6 → 进程非零 + 结论只能是「样本不足」）；
- 分母为 0 的比率不得报 1.0（accuracy/Wilson 为 null）；
- ρ 用已知序列对拍，ρ<0.5 必打「该分不应进 README」。

跑：python tests/test_g7_adjudicate.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import g7_adjudicate as A  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 构造数据集（不是真实 G7 产物；只用来驱动工具）
# ─────────────────────────────────────────────────────────────

def _entity(eid, kind="character", label="woman", age=28, **desc):
    d = {"gender": "woman", "hair": "black, long", "wardrobe": ["grey hoodie"]}
    d.update(desc)
    e = {"id": eid, "kind": kind, "label": label, "importance": 0.9,
         "descriptors": d, "spatial": {"anchor": "center"}}
    if age is not None:
        e["age_years"] = age
    return e


def _payload(model, img, entities, caption, ok=True, category="单人特写",
             sha=None):
    sha = sha or ("sha-%s-%s" % (model, img))
    return {"schema": "g7-caption/1", "model_key": model, "model_label": model,
            "image_id": img, "image_name": "%s.png" % img,
            "image_sha256": sha, "category": category, "ok": ok,
            "caption": caption,
            "ir": {"entities": entities, "style": {"medium": "photograph"},
                   "lighting": {"key": "soft side light"}, "canvas": {}}}


def make_dataset(tmp: Path, models=("A-4B", "B-9B"), imgs=("img01", "img02", "img03")):
    """造 2 模型 × N 图的最小数据集（含 images.json）。"""
    json_dir = tmp / "json"
    images = {"images": []}
    for i, img in enumerate(imgs):
        images["images"].append({"id": img, "category": "单人特写",
                                 "path": str(tmp / ("%s.png" % img)),
                                 "sha256": "sha-%s" % img})
        for model in models:
            if i == 2 and model == "B-9B":
                payload = _payload(model, img, [], "", ok=False)
                payload["error"] = "ReverseError: JSON 没有闭合"
            else:
                ents = [_entity("e1"), _entity("e2", kind="object", label="cup",
                                               age=None, material="ceramic", color="white")]
                payload = _payload(model, img, ents,
                                   "A 28-year-old woman wearing grey hoodie, "
                                   "a white ceramic cup, in the center.")
            p = json_dir / model / ("%s.json" % img)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (tmp / "images.json").write_text(json.dumps(images, ensure_ascii=False),
                                     encoding="utf-8")
    return json_dir, tmp / "images.json"


def run_cli(argv) -> tuple:
    """跑 CLI 主入口，返回 (rc, stdout, stderr)。SystemExit 也转成 rc/stderr。"""
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = A.main(argv)
        except SystemExit as exc:                      # 工具用 SystemExit 报参数错误
            rc = exc.code if isinstance(exc.code, int) else 2
            if str(exc):
                err.write(str(exc))
    return rc, out.getvalue(), err.getvalue()


def run_packet(tmp: Path, **kw) -> tuple:
    json_dir, images = make_dataset(tmp)
    out = tmp / "adjudication"
    argv = ["packet", "--json-dir", str(json_dir), "--images", str(images),
            "--out", str(out)]
    for key, val in kw.items():
        argv += ["--" + key, str(val)]
    rc, so, se = run_cli(argv)
    return rc, so, se, out


def load_judge_template(out: Path, name: str) -> dict:
    return json.loads((out / "templates" / ("%s.json" % name)).read_text(encoding="utf-8"))


def write_judge(out: Path, name: str, template: dict, fill) -> Path:
    """fill(unit) -> dict(claims={cid: verdict}, hallucination="absent"|None)。"""
    judge = json.loads(json.dumps(template))
    judge["judge_id"] = name
    for unit in judge["units"]:
        verdicts = fill(unit)
        for cid in unit["claims"]:
            unit["claims"][cid] = verdicts.get(cid)
        unit["hallucination"] = verdicts.get("_hallucination")
    path = out / ("%s.json" % name)
    path.write_text(json.dumps(judge, ensure_ascii=False), encoding="utf-8")
    return path


def all_correct(unit):
    return dict.fromkeys(unit["claims"], "correct") | {"_hallucination": "absent"}


def mixed_verdicts(unit):
    """按 **排序后的 claim_id** 分配 mixed 判定（与模板顺序无关，两个变体都对齐）。"""
    cycle = ["correct", "correct", "wrong", "unverifiable"]
    got = {cid: cycle[i % len(cycle)] for i, cid in enumerate(sorted(unit["claims"]))}
    got["_hallucination"] = "absent"
    return got


def flip_one(unit, cid):
    got = mixed_verdicts(unit)
    if cid in got:                       # 只在该 claim 所属单元翻一个
        got[cid] = "unverifiable" if got[cid] != "unverifiable" else "wrong"
    return got


def run_merge(out: Path, judges, extra=()) -> tuple:
    argv = ["merge", "--judges", ",".join(str(j) for j in judges),
            "--manifest", str(out / "manifest.json"), "--out", str(out)]
    argv += list(extra)
    return run_cli(argv)


# ─────────────────────────────────────────────────────────────
# 1) packet：不泄漏机检、双盲对齐、AI 标注、人类包
# ─────────────────────────────────────────────────────────────

class TestPacket(unittest.TestCase):
    def test_packet_has_caption_and_ir_but_no_machine_verdicts(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            rc, so, _se, out = run_packet(tmp)
            self.assertEqual(rc, 0, so)
            md = (out / "packets" / "judge_a" / "A-4B" / "img01.md").read_text(encoding="utf-8")
            self.assertIn("28-year-old woman", md)                 # caption 在包里
            self.assertIn("| e1 | character", md)                  # IR 实体表在包里
            self.assertIn("grey hoodie", md)
            self.assertNotIn("bound", md.lower())                  # 机检字段不进包
            self.assertNotIn("覆盖率", md)
            self.assertNotIn("机检通过", md)
            self.assertIn("不含机检结论", md)                       # 但要明确告知

    def test_packet_manifest_pins_caption_and_ir(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _rc, _so, _se, out = run_packet(tmp)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            unit = next(u for u in manifest["units"]
                        if u["model"] == "A-4B" and u["image_id"] == "img01")
            payload = json.loads((tmp / "json" / "A-4B" / "img01.json").read_text(encoding="utf-8"))
            self.assertEqual(unit["caption_sha256"], A._sha256(payload["caption"]))
            self.assertEqual(unit["ir_sha256"], A._sha256(A._canon(payload["ir"])))
            self.assertEqual(unit["image_sha256"], payload["image_sha256"])
            self.assertTrue(3 <= unit["claims_total"] <= 9,
                            "协议要求每图每模型 3–9 条可核事实: %d" % unit["claims_total"])
            self.assertNotIn("failed", json.dumps(manifest["units"]))   # 失败单元不进 unit 列表
            failed = manifest["source"]["failed_units"]
            self.assertEqual(len(failed), 1)                     # 构造数据里 B-9B img03 失败
            self.assertEqual(failed[0]["model"], "B-9B")

    def test_packet_variants_aligned_and_ai_labeled(self):
        with tempfile.TemporaryDirectory() as d:
            _rc, _so, _se, out = run_packet(Path(d))
            ja = load_judge_template(out, "judge_a")
            jb = load_judge_template(out, "judge_b")
            self.assertIn(A.AI_LABEL, ja["ai_label"])
            self.assertIn(A.AI_LABEL, jb["ai_label"])
            for ua, ub in zip(ja["units"], jb["units"], strict=True):
                self.assertEqual(set(ua["claims"]), set(ub["claims"]))   # claim id 对齐
                self.assertGreaterEqual(len(ua["claims"]), 3)
            # B 变体是逆序（至少一条单元顺序不同），但 id 集合相同
            rev = 0
            for ua, ub in zip(ja["units"], jb["units"], strict=True):
                if list(ua["claims"]) != list(ub["claims"]):
                    rev += 1
            self.assertGreater(rev, 0)

    def test_human_packet_is_blank_and_has_no_ai_results(self):
        with tempfile.TemporaryDirectory() as d:
            _rc, _so, _se, out = run_packet(Path(d))
            human = json.loads(
                (out / "human_packet" / "templates" / "human.json").read_text(encoding="utf-8"))
            self.assertEqual(human["judge_type"], "human")
            self.assertEqual(human["ai_label"], "")
            for unit in human["units"]:
                self.assertTrue(all(v is None for v in unit["claims"].values()))
                self.assertIsNone(unit["hallucination"])
            md = (out / "human_packet" / "A-4B" / "img01.md").read_text(encoding="utf-8")
            self.assertIn("人类", md)
            self.assertIn("不含 AI 判读结果", md)
            self.assertNotIn(A.AI_LABEL, md)          # 人类包里不能出现 AI 判读标签

    def test_packet_missing_dir_is_actionable_error(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _so, se = run_cli(["packet", "--json-dir", str(Path(d) / "nope"),
                                   "--out", str(Path(d) / "adj")])
            self.assertNotEqual(rc, 0)
            self.assertIn("找不到模型产目录", se)


# ─────────────────────────────────────────────────────────────
# 2) merge：一致性、分歧、仲裁、准确率、三档结论
# ─────────────────────────────────────────────────────────────

class TestMerge(unittest.TestCase):
    def _prepared(self, tmp: Path):
        _rc, _so, _se, out = run_packet(tmp)
        return out

    def test_perfect_agreement_kappa_one_and_green(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja, jb = (load_judge_template(out, n) for n in ("judge_a", "judge_b"))
            j1 = write_judge(out, "judge_a", ja, all_correct)
            j2 = write_judge(out, "judge_b", jb, all_correct)
            rc, so, _se = run_merge(out, [j1, j2], ["--min-n", "1"])
            self.assertEqual(rc, 0, so)
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["agreement"]["claims"]["kappa"], 1.0)
            self.assertTrue(merged["agreement"]["ok"])
            self.assertEqual(merged["disagreements"], [])
            self.assertIn(merged["conclusion"]["tier"], A.CONCLUSION_TIERS)
            self.assertNotIn("更好", json.dumps(merged, ensure_ascii=False))

    def test_constructed_disagreement_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja, jb = (load_judge_template(out, n) for n in ("judge_a", "judge_b"))
            target = next(u for u in ja["units"]
                          if u["model"] == "A-4B" and u["image_id"] == "img01")
            bad_cid = next(iter(target["claims"]))

            def fill_a(unit):
                return mixed_verdicts(unit)

            def fill_b(unit):
                return flip_one(unit, bad_cid)

            j1 = write_judge(out, "judge_a", ja, fill_a)
            j2 = write_judge(out, "judge_b", jb, fill_b)
            rc, _so, _se = run_merge(out, [j1, j2], ["--min-n", "1"])
            self.assertIn(rc, (0, 1))          # 闸可能因 κ 边缘而红；分歧必须被发现
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            found = [x for x in merged["disagreements"] if x["claim_id"] == bad_cid]
            self.assertEqual(len(found), 1, "构造的分歧必须被发现")
            self.assertEqual(found[0]["model"], "A-4B")
            self.assertEqual(found[0]["image_id"], "img01")
            self.assertIn("judge_a", found[0]["verdicts"])
            self.assertIn("judge_b", found[0]["verdicts"])
            self.assertIn(bad_cid, merged["unresolved"])      # 没有仲裁 → 未决

    def test_low_agreement_turns_gate_red(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja, jb = (load_judge_template(out, n) for n in ("judge_a", "judge_b"))

            def fill_a(unit):
                return all_correct(unit)

            def fill_b(unit):
                got = {}
                for i, cid in enumerate(sorted(unit["claims"])):
                    got[cid] = "correct" if i % 2 == 0 else "wrong"
                got["_hallucination"] = "present"
                return got

            j1 = write_judge(out, "judge_a", ja, fill_a)
            j2 = write_judge(out, "judge_b", jb, fill_b)
            rc, _so, se = run_merge(out, [j1, j2], ["--min-n", "1"])
            self.assertNotEqual(rc, 0, "一致性过低必须让进程非零（闸会红）")
            self.assertIn("判据", se)
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            self.assertFalse(merged["agreement"]["ok"])
            self.assertLess(merged["agreement"]["min_kappa"], A.KAPPA_THRESHOLD)
            self.assertEqual(merged["conclusion"]["tier"], "样本不足")

    def test_arbiter_resolves_disagreement(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja, jb, jarb = (load_judge_template(out, n)
                            for n in ("judge_a", "judge_b", "arbiter"))
            target = next(u for u in ja["units"]
                          if u["model"] == "A-4B" and u["image_id"] == "img01")
            bad_cid = next(iter(target["claims"]))

            j1 = write_judge(out, "judge_a", ja, mixed_verdicts)

            def fill_b(unit):
                got = mixed_verdicts(unit)
                if bad_cid in got:
                    got[bad_cid] = "wrong" if got[bad_cid] != "wrong" else "correct"
                return got

            j2 = write_judge(out, "judge_b", jb, fill_b)

            def fill_arb(unit):
                if unit["model"] == "A-4B" and unit["image_id"] == "img01":
                    return {bad_cid: "unverifiable"}
                return {}

            j3 = write_judge(out, "arbiter", jarb, fill_arb)
            rc, _so, _se = run_merge(out, [j1, j2], ["--arbiter", str(j3), "--min-n", "1"])
            self.assertIn(rc, (0, 1))
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            self.assertNotIn(bad_cid, merged["unresolved"])
            hit = [x for x in merged["disagreements"] if x["claim_id"] == bad_cid][0]
            self.assertEqual(hit["arbiter"], "unverifiable")
            # unverifiable 计入不可判、不计入准确率分母
            self.assertGreater(merged["accuracy"]["A-4B"]["unverifiable"], 0)

    def test_judge_without_ai_label_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja = load_judge_template(out, "judge_a")
            ja["ai_label"] = ""
            p = out / "bad.json"
            p.write_text(json.dumps(ja, ensure_ascii=False), encoding="utf-8")
            jb = write_judge(out, "judge_b", load_judge_template(out, "judge_b"),
                             all_correct)
            with self.assertRaises(SystemExit) as ctx:
                A.main(["merge", "--judges", str(p) + "," + str(jb), "--out", str(out)])
            self.assertIn("ai_label", str(ctx.exception))

    def test_invalid_verdict_is_actionable_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja = load_judge_template(out, "judge_a")
            for u in ja["units"]:
                for cid in u["claims"]:
                    u["claims"][cid] = "maybe"          # 非法 verdict
                    break
                break
            p = out / "bad.json"
            p.write_text(json.dumps(ja, ensure_ascii=False), encoding="utf-8")
            jb = write_judge(out, "judge_b", load_judge_template(out, "judge_b"),
                             all_correct)
            with self.assertRaises(SystemExit) as ctx:
                A.main(["merge", "--judges", str(p) + "," + str(jb), "--out", str(out)])
            msg = str(ctx.exception)
            self.assertIn("不合法", msg)
            self.assertIn("correct", msg)

    def test_single_judge_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            j1 = write_judge(out, "judge_a", load_judge_template(out, "judge_a"),
                             all_correct)
            with self.assertRaises(SystemExit) as ctx:
                A.main(["merge", "--judges", str(j1), "--out", str(out)])
            self.assertIn("≥2", str(ctx.exception))

    def test_accuracy_wilson_and_significance(self):
        """构造 A 明显优于 B：准确率、Wilson、Fisher、显著差/无显著差异都要对。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja, jb = (load_judge_template(out, n) for n in ("judge_a", "judge_b"))
            # 按 (model, image, 第 i 条) 决定 verdict
            plan = {
                ("A-4B", "img01"): ["correct"] * 6,
                ("A-4B", "img02"): ["correct"] * 3 + ["wrong"],
                ("B-9B", "img01"): ["wrong"] * 6,
                ("B-9B", "img02"): ["wrong"] * 3 + ["correct"],
            }

            def fill(unit):
                got = {}
                seq = plan.get((unit["model"], unit["image_id"]), [])
                for i, cid in enumerate(sorted(unit["claims"])):
                    got[cid] = seq[i % len(seq)] if seq else "correct"
                got["_hallucination"] = "absent"
                return got

            j1 = write_judge(out, "judge_a", ja, fill)
            j2 = write_judge(out, "judge_b", jb, fill)
            rc, so, _se = run_merge(out, [j1, j2], ["--min-n", "1"])
            self.assertEqual(rc, 0, so)
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            acc_a = merged["accuracy"]["A-4B"]
            acc_b = merged["accuracy"]["B-9B"]
            self.assertGreater(acc_a["accuracy"], acc_b["accuracy"])
            self.assertEqual(len(acc_a["wilson95"]), 2)
            self.assertEqual(merged["between_models"]["fisher_p"] is not None, True)
            self.assertIn(merged["conclusion"]["tier"], ("显著差", "无显著差异"))
            # 同样的构造，把 min_n 抬到不可能满足 → 只能报样本不足
            rc2, _so2, _se2 = run_merge(out, [j1, j2], ["--min-n", "100000"])
            merged2 = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            self.assertEqual(merged2["conclusion"]["tier"], "样本不足")
            self.assertIn(rc2, (0, 1))

    def test_zero_denominator_is_not_one(self):
        """全部 unverifiable：准确率/Wilson 必须是 null，不得报 1.0。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._prepared(tmp)
            ja, jb = (load_judge_template(out, n) for n in ("judge_a", "judge_b"))

            def fill(unit):
                got = dict.fromkeys(unit["claims"], "unverifiable")
                got["_hallucination"] = "absent"
                return got

            j1 = write_judge(out, "judge_a", ja, fill)
            j2 = write_judge(out, "judge_b", jb, fill)
            rc, _so, _se = run_merge(out, [j1, j2], ["--min-n", "1"])
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            for model in ("A-4B", "B-9B"):
                box = merged["accuracy"][model]
                self.assertIsNone(box["accuracy"])
                self.assertIsNone(box["wilson95"])
                self.assertEqual(box["resolved"], 0)
            self.assertEqual(merged["conclusion"]["tier"], "样本不足")
            self.assertIn(rc, (0, 1))


# ─────────────────────────────────────────────────────────────
# 3) calibrate：ρ 对拍、翻转率、ρ<0.5 必红
# ─────────────────────────────────────────────────────────────

class TestCalibrate(unittest.TestCase):
    def _merged(self, tmp: Path) -> Path:
        _rc, _so, _se, out = run_packet(tmp)
        ja, jb = (load_judge_template(out, n) for n in ("judge_a", "judge_b"))
        plan = {
            ("A-4B", "img01"): ["correct"] * 6,
            ("A-4B", "img02"): ["correct", "wrong", "unverifiable"],
            ("B-9B", "img01"): ["wrong"] * 6,
            ("B-9B", "img02"): ["wrong", "wrong", "correct"],
        }

        def fill(unit):
            got = {}
            seq = plan.get((unit["model"], unit["image_id"]),
                           ["correct", "wrong", "unverifiable"])
            for i, cid in enumerate(sorted(unit["claims"])):
                got[cid] = seq[i % len(seq)]
            got["_hallucination"] = "absent"
            return got

        j1 = write_judge(out, "judge_a", ja, fill)
        j2 = write_judge(out, "judge_b", jb, fill)
        rc, so, _se = run_merge(out, [j1, j2], ["--min-n", "1"])
        self.assertIn(rc, (0, 1), so)
        return out

    def test_spearman_known_sequences(self):
        self.assertEqual(A.spearman_rho([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertEqual(A.spearman_rho([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertIsNone(A.spearman_rho([1, 2], [1, 2]))       # n<3
        self.assertIsNone(A.spearman_rho([1, 1, 1], [1, 2, 3]))  # 方差 0
        # 并列用平均秩：已知 rho 数值
        self.assertAlmostEqual(A.spearman_rho([1, 2, 2, 3], [1, 2, 3, 4]),
                               0.9487, places=3)

    def test_calibrate_perfect_scores_pass(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._merged(tmp)
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            rows = [{"model": u["model"], "image_id": u["image_id"],
                     "score": u["accuracy"] if u["accuracy"] is not None else 0.0}
                    for u in merged["units"]]
            scores = tmp / "scores.json"
            scores.write_text(json.dumps({"rows": rows}), encoding="utf-8")
            rc, so, _se = run_cli(["calibrate", "--merged", str(out / "merged.json"),
                                   "--scores", str(scores), "--out", str(out)])
            self.assertEqual(rc, 0, so)
            cal = json.loads((out / "calibration.json").read_text(encoding="utf-8"))
            self.assertEqual(cal["rho"], 1.0)
            self.assertTrue(cal["readme_allowed"])

    def test_calibrate_low_rho_turns_red_with_readme_warning(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._merged(tmp)
            merged = json.loads((out / "merged.json").read_text(encoding="utf-8"))
            rows = []
            for u in merged["units"]:
                acc = u["accuracy"] if u["accuracy"] is not None else 0.0
                rows.append({"model": u["model"], "image_id": u["image_id"],
                             "score": 1.0 - acc})           # 故意反向排序
            scores = tmp / "scores.json"
            scores.write_text(json.dumps({"rows": rows}), encoding="utf-8")
            rc, _so, se = run_cli(["calibrate", "--merged", str(out / "merged.json"),
                                   "--scores", str(scores), "--out", str(out)])
            self.assertNotEqual(rc, 0, "ρ<0.5 必须让进程非零")
            self.assertIn("该分不应进 README", se)
            cal = json.loads((out / "calibration.json").read_text(encoding="utf-8"))
            self.assertFalse(cal["readme_allowed"])
            self.assertLess(cal["rho"], A.RHO_THRESHOLD)

    def test_calibrate_needs_three_units(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = self._merged(tmp)
            scores = tmp / "scores.json"
            scores.write_text(json.dumps({"rows": [
                {"model": "A-4B", "image_id": "img01", "score": 0.5}]}),
                encoding="utf-8")
            rc, _so, se = run_cli(["calibrate", "--merged", str(out / "merged.json"),
                                   "--scores", str(scores), "--out", str(out)])
            self.assertNotEqual(rc, 0)
            self.assertIn("<3", se)


# ─────────────────────────────────────────────────────────────
# calib-packet：从 merged 的「verdict 不一致 claim」生成预校准包
# ─────────────────────────────────────────────────────────────

def _first_claim_ids(packet_out: Path, per_unit: int = 2) -> dict:
    """从 packet manifest 取每单元前若干 claim id（确定性夹具用）。"""
    man = json.loads((packet_out / "manifest.json").read_text(encoding="utf-8"))
    out = {}
    for u in man["units"]:
        key = (u["model"], u["image_id"])
        out[key] = [c["id"] for c in u["claims"][:per_unit]]
    return out


def _packet_and_flips(tmp: Path) -> tuple:
    """先跑 packet，再按真实 claim id 构造「judge_b 改判」的 flips。

    必须先有 packet manifest 才知道 claim id 的真实集合；flips 只是夹具（两名判读者
    在这几条上不一致），用于驱动 calib-packet 的「只选不一致 claim」逻辑。
    """
    json_dir, images = make_dataset(tmp)
    rc, so, se, out = run_packet(tmp)
    assert rc == 0, (rc, so, se)
    ids = _first_claim_ids(out)
    flips = {}
    flips[ids[("A-4B", "img01")][0]] = "unverifiable"
    flips[ids[("A-4B", "img01")][1]] = "wrong"
    flips[ids[("A-4B", "img02")][0]] = "unverifiable"
    flips[ids[("B-9B", "img01")][0]] = "wrong"
    return out, json_dir, images, flips


def make_merged_with_flips(tmp: Path, flips: dict) -> tuple:
    """packet → 两名判读者（judge_b 在 flips 上改判）→ merge。

    返回 (packet_out, merged_path, json_dir, images_path)。
    """
    if not (tmp / "adjudication" / "manifest.json").is_file():
        _packet_and_flips(tmp)
    out = tmp / "adjudication"
    json_dir, images = tmp / "json", tmp / "images.json"

    def fill_all(unit):
        return dict.fromkeys(unit["claims"], "correct") | {"_hallucination": "absent"}

    def fill_b(unit):
        got = fill_all(unit)
        for cid in list(got):
            if cid in flips:
                got[cid] = flips[cid]
        return got

    ja = write_judge(out, "judge_a", load_judge_template(out, "judge_a"), fill_all)
    jb = write_judge(out, "judge_b", load_judge_template(out, "judge_b"), fill_b)
    rc, so, se = run_merge(out, [ja, jb])
    # 夹具只有少数几条分歧 → κ 必然低、merge 按设计返回非零（闸红）；
    # merged.json 仍然会落盘，这是 calib-packet 的输入，所以接受 0/1 两种退出码。
    assert rc in (0, 1) and (out / "merged.json").is_file(), (rc, so, se)
    return out, out / "merged.json", json_dir, images


def run_calib(tmp: Path, merged: Path, json_dir: Path, images: Path,
              extra=()) -> tuple:
    dest = tmp / "calib2"
    argv = ["calib-packet", "--merged", str(merged), "--json-dir", str(json_dir),
            "--images", str(images), "--out", str(dest)]
    argv += list(extra)
    rc, so, se = run_cli(argv)
    return rc, so, se, dest


class TestCalibPacket(unittest.TestCase):
    @staticmethod
    def _flips(tmp: Path) -> dict:
        _out, _json_dir, _images, flips = _packet_and_flips(tmp)
        return flips

    def _build(self, tmp: Path, max_claims=None):
        out, json_dir, images, flips = _packet_and_flips(tmp)
        merged = make_merged_with_flips(tmp, flips)[1]
        extra = () if max_claims is None else ("--max-claims", str(max_claims))
        rc, so, se, dest = run_calib(tmp, merged, json_dir, images, extra)
        return rc, so, se, dest, flips, merged, json_dir

    def test_selects_only_disputed_claims(self):
        with tempfile.TemporaryDirectory() as d:
            rc, so, se, dest, flips, merged, json_dir = self._build(Path(d))
            self.assertEqual(rc, 0, se)
            man = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            got = [c["id"] for u in man["units"] for c in u["claims"]]
            self.assertEqual(sorted(got), sorted(flips))
            sel = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(sel["source"]["claims"], len(flips))
            self.assertGreaterEqual(sel["source"]["units"], 1)

    def test_max_claims_caps_per_unit_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            flips = self._flips(tmp)
            out, merged, json_dir, images = make_merged_with_flips(tmp, flips)
            rc1, _so, se1, dest1 = run_calib(
                tmp, merged, json_dir, images, ("--max-claims", "1"))
            self.assertEqual(rc1, 0, se1)
            man1 = (dest1 / "manifest.json").read_text(encoding="utf-8")
            # 同一输入再跑一次 → 字节一致（确定性）
            rc2, _so, se2, dest2 = run_calib(
                Path(d), merged, json_dir, images, ("--max-claims", "1"))
            self.assertEqual(rc2, 0, se2)
            self.assertEqual(man1,
                             (dest2 / "manifest.json").read_text(encoding="utf-8"))
            man = json.loads(man1)
            for u in man["units"]:
                self.assertLessEqual(len(u["claims"]), 1)
            self.assertEqual(man["source"]["claims"],
                             len({cid.split("|")[0] + "|" + cid.split("|")[1]
                                  for cid in flips}))

    def test_manifest_pins_three_fingerprints_and_source_sha(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _so, se, dest, flips, merged, json_dir = self._build(Path(d))
            self.assertEqual(rc, 0, se)
            man = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(man["schema"], A.PACKET_SCHEMA)
            self.assertEqual(man["source"]["merged"], str(merged))
            self.assertEqual(man["source"]["merged_sha256"],
                             A._sha256_file(merged))
            for u in man["units"]:
                payload = json.loads((json_dir / u["model"] / (
                    u["image_id"] + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(u["caption_sha256"], A._sha256(payload["caption"]))
                self.assertEqual(u["ir_sha256"], A._sha256(A._canon(payload["ir"])))
                self.assertEqual(u["image_sha256"], payload["image_sha256"])

    def test_templates_are_blank_ai_labeled_and_variant_aligned(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _so, se, dest, flips, _merged, _json_dir = self._build(Path(d))
            self.assertEqual(rc, 0, se)
            man = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            man_order = {u["model"] + "|" + u["image_id"]:
                         [c["id"] for c in u["claims"]] for u in man["units"]}
            tpl_a = json.loads((dest / "judge_a" / "judge_a.json")
                               .read_text(encoding="utf-8"))
            tpl_b = json.loads((dest / "judge_b" / "judge_b.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual(tpl_a["variant"], "variant_a")
            self.assertEqual(tpl_b["variant"], "variant_b")
            for tpl in (tpl_a, tpl_b):
                self.assertEqual(tpl["schema"], A.JUDGE_SCHEMA)
                self.assertIn(A.AI_LABEL, tpl["ai_label"])
            for ua, ub in zip(tpl_a["units"], tpl_b["units"], strict=True):
                key = ua["model"] + "|" + ua["image_id"]
                self.assertTrue(all(v is None for v in ua["claims"].values()))
                self.assertTrue(all(v is None for v in ub["claims"].values()))
                self.assertEqual(set(ua["claims"]), set(man_order[key]))
                self.assertEqual(list(ua["claims"]), man_order[key])
                self.assertEqual(list(ub["claims"]), list(reversed(man_order[key])))

    def test_no_old_verdicts_or_dispute_words_leak(self):
        """包里不得出现旧判法值/「争议」字样；md 里的 verdict 词只允许在判读规则/填写说明。"""
        with tempfile.TemporaryDirectory() as d:
            rc, _so, se, dest, flips, _merged, _json_dir = self._build(Path(d))
            self.assertEqual(rc, 0, se)
            man_text = (dest / "manifest.json").read_text(encoding="utf-8")
            for token in ('"correct"', '"wrong"', '"unverifiable"', "争议",
                          "disagreement", "verdicts"):
                self.assertNotIn(token, man_text)
            selected = set(flips)
            for md in (dest / "judge_a" / "packets").rglob("*.md"):
                text = md.read_text(encoding="utf-8")
                for line in text.splitlines():
                    for token in ("correct", "wrong", "unverifiable"):
                        if token in line:
                            self.assertTrue(
                                line.startswith("- `")
                                or "逐条填 correct / wrong / unverifiable" in line,
                                "旧判法值/非法 verdict 出现在 md：%r" % line)
                for cid in selected:
                    if "`%s`" % cid in text:
                        row = next(ln for ln in text.splitlines()
                                   if "`%s`" % cid in ln)
                        self.assertTrue(row.rstrip().endswith("待填 |"),
                                        "claim 行带了非空 verdict：%r" % row)

    def test_no_dispute_is_actionable_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out, merged, json_dir, images = make_merged_with_flips(tmp, {})
            rc, _so, se, _dest = run_calib(tmp, merged, json_dir, images)
            self.assertNotEqual(rc, 0)
            self.assertIn("verdict 不一致", se)

    def test_bad_merged_structure_is_actionable_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            json_dir, images = make_dataset(tmp)
            bad = tmp / "bad-merged.json"
            bad.write_text('{"schema": "g7-adjudication-merged/1"}', encoding="utf-8")
            rc, _so, se, _dest = run_calib(tmp, bad, json_dir, images)
            self.assertNotEqual(rc, 0)
            self.assertIn("disagreements", se)

    def test_missing_record_is_actionable_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            flips = self._flips(tmp)
            _out, merged, json_dir, images = make_merged_with_flips(tmp, flips)
            (json_dir / "A-4B" / "img01.json").unlink()
            rc, _so, se, _dest = run_calib(tmp, merged, json_dir, images)
            self.assertNotEqual(rc, 0)
            self.assertIn("找不到对应产出", se)

    def test_merge_consumes_calib_output(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _so, se, dest, flips, _merged, _json_dir = self._build(Path(d))
            self.assertEqual(rc, 0, se)
            tpl_a = json.loads((dest / "judge_a" / "judge_a.json")
                               .read_text(encoding="utf-8"))
            tpl_b = json.loads((dest / "judge_b" / "judge_b.json")
                               .read_text(encoding="utf-8"))
            ja = write_judge(dest, "judge_a", tpl_a, all_correct)
            jb = write_judge(dest, "judge_b", tpl_b, all_correct)
            rc2, so2, se2 = run_cli(
                ["merge", "--judges", "%s,%s" % (ja, jb),
                 "--manifest", str(dest / "manifest.json"), "--out", str(dest)])
            self.assertEqual(rc2, 0, se2 + so2)
            merged = json.loads((dest / "merged.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["agreement"]["claims"]["n"], len(flips))
            self.assertEqual(merged["disagreements"], [])
            self.assertEqual(merged["agreement"]["claims"]["kappa"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
