# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""T8 `tools/wash_benchmark.py` 的离线单测：零 GPU、零网络。

钉住四件事：
1. 可用后端有三份可校验的 API 图；**缺权重的 flux3/flux1 如实记录为 skipped**，不假装；
2. 编译步骤对真实 IR 夹具能出 prompt（MOSS 会走 Layout JSON，其余走散文）；
3. 代理指标是确定性的、能区分「像」与「不像」，且**明确声明未校准、不作闸**；
4. 生成链的 dry/降级路径不联网也能走通（缺 caption、缺后端时给原因，不崩）。

跑：python tests\\test_wash_benchmark.py -v
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

_spec = importlib.util.spec_from_file_location(
    "wash_benchmark", ROOT / "tools" / "wash_benchmark.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)

FIXTURE_IR = ROOT / "tests" / "fixtures" / "smith" / "comic-4panel-lineart-sample.ir.json"


def _make_png(path: Path, mode: str = "gradient", size: tuple[int, int] = (640, 480)):
    from PIL import Image, ImageDraw
    if mode == "gradient":
        im = Image.new("RGB", size)
        px = im.load()
        for y in range(size[1]):
            for x in range(size[0]):
                px[x, y] = (x * 255 // size[0], y * 255 // size[1], 128)
    elif mode == "flat":
        im = Image.new("RGB", size, (128, 128, 128))
    elif mode == "inverse":
        im = Image.new("RGB", size)
        px = im.load()
        for y in range(size[1]):
            for x in range(size[0]):
                px[x, y] = (255 - x * 255 // size[0], 40, 200 - y * 200 // size[1])
    else:
        im = Image.new("RGB", size, (20, 120, 60))
        draw = ImageDraw.Draw(im)
        draw.ellipse((50, 50, 300, 200), fill=(250, 250, 250))
        draw.rectangle((400, 300, 600, 460), fill=(10, 10, 10))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


class TestDesignAndBackends(unittest.TestCase):
    def test_check_design_is_green(self):
        self.assertEqual(wb.check_design(), [])

    def test_three_backends_available(self):
        avail = [b for b, m in wb.BACKENDS.items() if m.get("available")]
        self.assertEqual(sorted(avail), ["krea2", "moss-image", "qwen-image-2_1"])

    def test_missing_backends_have_reason(self):
        for name in ("flux3", "flux1_dev"):
            meta = wb.BACKENDS[name]
            self.assertFalse(meta["available"])
            self.assertTrue(meta.get("reason"), "%s 缺 reason" % name)
            self.assertIsNone(meta.get("workflow"))

    def test_unavailable_backends_are_in_workflow_index(self):
        idx = wb.load_index()
        for name in ("flux3", "flux1_dev"):
            self.assertIn(name, idx.get("unavailable", {}))

    def test_planned_images_counts_only_available_and_existing(self):
        p = wb.plan()
        self.assertEqual(len(p["matrix"]), len(wb.BACKENDS) * len(wb.SOURCES))
        expect = sum(1 for r in p["matrix"] if r["available"] and r["exists"])
        self.assertEqual(p["planned_images"], expect)

    def test_metric_declares_uncalibrated_and_not_a_gate(self):
        self.assertFalse(wb.METRIC["calibrated"])
        self.assertTrue(wb.METRIC["not_for_gating"])
        self.assertAlmostEqual(sum(wb.METRIC["weights"].values()), 1.0, places=9)

    def test_caption_config_uses_non_conflicting_port(self):
        for key in ("model", "mmproj", "exe", "port"):
            self.assertIn(key, wb.CAPTION)
        # 8091 是 g7/T1 的常用口：T6/T8 不许跟 T1 抢同一个侧车口
        self.assertNotEqual(wb.CAPTION["port"], 8091)
        # 公开仓硬要求：模板里不许写死本机盘符/路径
        for key in ("model", "mmproj", "exe"):
            self.assertNotIn(":\\", wb.CAPTION[key])

    def test_resolve_caption_conf_requires_model(self):
        import os
        keys = ("MOSS_LLM_MODEL", "G7_MODEL", "MOSS_LLM_MMPROJ",
                "G7_MMPROJ", "MOSS_LLM_SERVER", "G7_SERVER")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            with self.assertRaises(RuntimeError):
                wb.resolve_caption_conf()
            os.environ["MOSS_LLM_MODEL"] = "some/model.gguf"
            os.environ["G7_SERVER"] = "some/llama-server.exe"
            conf = wb.resolve_caption_conf()
            self.assertEqual(conf["model"], "some/model.gguf")
            self.assertEqual(conf["exe"], "some/llama-server.exe")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestSizing(unittest.TestCase):
    def test_rounds_to_multiple_of_16(self):
        w, h = wb.size_for((1920, 1080))
        self.assertEqual(w % 16, 0)
        self.assertEqual(h % 16, 0)

    def test_keeps_aspect_approximately(self):
        w, h = wb.size_for((1600, 900))         # 16:9
        self.assertAlmostEqual(w / h, 16 / 9, delta=0.06)

    def test_bounds(self):
        for shape in [(4000, 100), (100, 4000), (1024, 1024)]:
            w, h = wb.size_for(shape)
            self.assertGreaterEqual(min(w, h), 512)
            self.assertLessEqual(max(w, h), 1536)

    def test_missing_shape_falls_back_to_square(self):
        self.assertEqual(wb.size_for(None), (1024, 1024))


class TestWorkflowBuilder(unittest.TestCase):
    def test_injects_prompt_seed_and_size(self):
        wf = wb.build_workflow("moss-image", "hello", 5, 832, 1216, "wash/x")
        nodes = wb.load_index()["backends"]["moss-image"]["nodes"]
        self.assertEqual(wf[nodes["positive"]]["inputs"]["text"], "hello")
        self.assertEqual(wf[nodes["sampler"]]["inputs"]["seed"], 5)
        self.assertEqual(wf[nodes["latent"]]["inputs"]["width"], 832)
        self.assertEqual(wf[nodes["latent"]]["inputs"]["height"], 1216)

    def test_all_links_point_to_existing_nodes(self):
        for backend in ("moss-image", "krea2", "qwen-image-2_1"):
            wf = wb.load_template(backend)
            for nid, node in wf.items():
                for name, val in node["inputs"].items():
                    if isinstance(val, list):
                        self.assertIn(str(val[0]), wf, "%s.%s.%s" % (backend, nid, name))

    def test_validator_rejects_missing_required(self):
        wf = wb.load_template("moss-image")
        del wf["1"]["inputs"]["unet_name"]
        infos = {"UNETLoader": {"input": {"required": {"unet_name": ["COMBO"]}},
                                "output": [["MODEL"]]}}
        problems = wb.validate_workflow(wf, infos)
        self.assertTrue(any("缺必填输入 unet_name" in p for p in problems))

    def test_seed_is_deterministic_and_backend_specific(self):
        a = wb.seed_for("moss-image", "ui-text")
        self.assertEqual(a, wb.seed_for("moss-image", "ui-text"))
        self.assertNotEqual(a, wb.seed_for("krea2", "ui-text"))
        self.assertIsInstance(a, int)
        self.assertLess(a, 2_000_000_000)


class TestCompileStep(unittest.TestCase):
    def test_each_available_backend_compiles_fixture(self):
        for backend in ("moss-image", "krea2", "qwen-image-2_1"):
            with self.subTest(backend=backend):
                r = wb.compile_for(FIXTURE_IR, backend)
                self.assertEqual(r["status"], "ok", r.get("error"))
                self.assertTrue(r["positive"].strip())

    def test_moss_layout_mode_is_detected(self):
        r = wb.compile_for(FIXTURE_IR, "moss-image")
        self.assertEqual(r["mode"], "layout_json")
        self.assertTrue(r["positive"].lstrip().startswith("{"))

    def test_krea2_stays_prose_and_has_512_limit(self):
        r = wb.compile_for(FIXTURE_IR, "krea2")
        self.assertEqual(r["mode"], "prose")
        self.assertEqual(r["token_limit"], 512)

    def test_bad_ir_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.ir.json"
            bad.write_text('{"task": "t2i", "entities": [', encoding="utf-8")
            r = wb.compile_for(bad, "krea2")
            self.assertEqual(r["status"], "error")
            self.assertIn("error", r)


class TestProxyMetrics(unittest.TestCase):
    def test_identical_images_score_near_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _make_png(Path(tmp) / "a.png", "photo")
            b = _make_png(Path(tmp) / "b.png", "photo")
            sc = wb.score_pair(a, b)
            for key, val in sc["scores"].items():
                self.assertGreaterEqual(val, 0.99, key)
            self.assertAlmostEqual(sc["triage_index"], 1.0, places=3)
            self.assertFalse(sc["degenerate"])

    def test_different_images_score_lower(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _make_png(Path(tmp) / "a.png", "photo")
            b = _make_png(Path(tmp) / "b.png", "inverse")
            sc = wb.score_pair(a, b)
            self.assertLess(sc["triage_index"], 0.99)
            self.assertGreaterEqual(sc["triage_index"], 0.0)

    def test_degenerate_generation_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _make_png(Path(tmp) / "a.png", "photo")
            b = _make_png(Path(tmp) / "b.png", "flat")
            sc = wb.score_pair(a, b)
            self.assertTrue(sc["degenerate"])

    def test_aspect_mismatch_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _make_png(Path(tmp) / "a.png", "photo", (640, 480))
            b = _make_png(Path(tmp) / "b.png", "photo", (640, 360))
            sc = wb.score_pair(a, b)
            self.assertLess(sc["aspect_match"], 0.9)

    def test_metrics_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _make_png(Path(tmp) / "a.png", "photo")
            b = _make_png(Path(tmp) / "b.png", "inverse")
            self.assertEqual(wb.score_pair(a, b), wb.score_pair(a, b))


class _TmpEnv:
    """临时 out/ + 一张源图 + 一份 IR 夹具。"""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.src = _make_png(self.out / "sources" / "ui-text.png", "photo", (640, 480))
        (self.out / "captions").mkdir(parents=True, exist_ok=True)
        (self.out / "captions" / "ui-text.ir.json").write_text(
            FIXTURE_IR.read_text(encoding="utf-8"), encoding="utf-8")

    def sources(self):
        return [{"id": "ui-text", "category": "含图内文字", "path": str(self.src),
                 "why": "tmp"}]

    def close(self):
        self._tmp.cleanup()


class _FakeLlama:
    """假侧车：够跑通 caption_sources 的两次带图调用，不占 GPU、不联网。"""

    calls = []

    def __init__(self, **kwargs):
        self.notes = ["fake"]
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def chat_vision(self, system, user, data_url, temperature=0.1, max_tokens=8192,
                    schema=None, name="scene_record"):
        _FakeLlama.calls.append(name)
        if name == "text_blocks":
            return '{"blocks": []}'
        return ('{"canvas": "single", "style": {"medium": "photograph"}, '
                '"entities": [{"id": "e1", "kind": "character", "label": "woman", '
                '"importance": 1.0, "age_years": 28, "spatial": {"anchor": "center"}, '
                '"evidence": ["visible"]}]}')


class TestCaptionOffline(unittest.TestCase):
    def setUp(self):
        import moss_llm.server as server_mod
        self._orig = server_mod.LlamaServer
        server_mod.LlamaServer = _FakeLlama
        _FakeLlama.calls = []

    def tearDown(self):
        import moss_llm.server as server_mod
        server_mod.LlamaServer = self._orig

    def test_caption_incremental_merge_and_only_source_filter(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            a = _make_png(out / "sources" / "a.png", "photo")
            b = _make_png(out / "sources" / "b.png", "inverse")
            src_a = [{"id": "a", "category": "x", "path": str(a)}]
            src_b = [{"id": "b", "category": "y", "path": str(b)}]
            fake_conf = {"model": str(out / "fake.gguf"), "mmproj": "", "exe": "",
                         "port": 8199, "ctx": 4096, "temperature": 0.1,
                         "max_tokens": 256, "max_side": 512,
                         "text_retry_max_tokens": 512}
            m1 = wb.caption_sources(sources=src_a, out=out, conf=fake_conf)
            self.assertEqual([s["id"] for s in m1["sources"]], ["a"])
            self.assertTrue((out / "captions" / "a.ir.json").is_file())
            n_first = len(_FakeLlama.calls)
            self.assertGreaterEqual(n_first, 1)
            # 第二次只跑 b：manifest 要合并（a 不能被清掉），且只调 b 的模型
            m2 = wb.caption_sources(sources=src_b, out=out, conf=fake_conf)
            self.assertEqual(sorted(s["id"] for s in m2["sources"]), ["a", "b"])
            self.assertEqual(len(_FakeLlama.calls) - n_first, n_first)  # a 没被重跑
            sources_doc = json.loads((out / "sources.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(s["id"] for s in sources_doc["sources"]), ["a", "b"])


class TestDegradationPaths(unittest.TestCase):
    def test_generate_dry_writes_workflow_without_network(self):
        env = _TmpEnv()
        try:
            run = wb.generate(sources=env.sources(), out=env.out, dry=True,
                              only=["moss-image"])
            self.assertIn("moss-image", run["backends"])
            imgs = run["backends"]["moss-image"]["images"]
            self.assertEqual(len(imgs), 1)
            self.assertEqual(imgs[0]["prompt_mode"], "layout_json")
            wf_path = ROOT / imgs[0]["workflow"]
            self.assertTrue(wf_path.is_file())
            wf = json.loads(wf_path.read_text(encoding="utf-8"))
            nodes = wb.load_index()["backends"]["moss-image"]["nodes"]
            self.assertTrue(wf[nodes["positive"]]["inputs"]["text"].lstrip().startswith("{"))
            self.assertTrue((env.out / "run.json").is_file())
        finally:
            env.close()

    def test_missing_caption_is_an_error_entry_not_a_crash(self):
        env = _TmpEnv()
        try:
            (env.out / "captions" / "ui-text.ir.json").unlink()
            run = wb.generate(sources=env.sources(), out=env.out, dry=True,
                              only=["moss-image"])
            errs = run["backends"]["moss-image"]["errors"]
            self.assertTrue(any("--caption" in e["error"] for e in errs), errs)
        finally:
            env.close()

    def test_unavailable_backend_is_skipped_with_reason(self):
        env = _TmpEnv()
        try:
            run = wb.generate(sources=env.sources(), out=env.out, dry=True,
                              only=["flux3"])
            self.assertIn("flux3", run["skipped"])
            self.assertTrue(run["skipped"]["flux3"]["reason"])
            self.assertNotIn("flux3", run["backends"])
        finally:
            env.close()

    def test_score_run_produces_ranking_and_missing_backends(self):
        env = _TmpEnv()
        try:
            gen = _make_png(env.out / "images" / "moss-image" / "ui-text.png",
                            "photo", (640, 480))
            run = {"backends": {"moss-image": {
                "images": [{"source": "ui-text", "category": "含图内文字",
                            "image": str(gen)}],
                "errors": []}}}
            (env.out / "run.json").write_text(json.dumps(run, ensure_ascii=False),
                                              encoding="utf-8")
            sc = wb.score_run(sources=env.sources(), out=env.out)
            self.assertEqual(sc["backends"]["moss-image"]["n"], 1)
            self.assertGreater(sc["backends"]["moss-image"]["mean_triage"], 0.9)
            self.assertEqual(sc["ranking"][0][0], "moss-image")
            self.assertIn("flux3", sc["missing_backends"])
            self.assertFalse(sc["metric"]["calibrated"])
            self.assertTrue((env.out / "score.json").is_file())
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
