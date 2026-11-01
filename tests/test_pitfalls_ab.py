# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""T6 `tools/pitfalls_ab.py` 的离线单测：零 GPU、零网络。

钉住三件事：
1. **实验设计合法**：断言逐字来自 registry；每对同 seed/尺寸/采样，A/B 只差被测变量；
2. **P3 的顺序对照成立**：A/B 同词同长，关键内容在 A 靠前、B 靠后（>512 token）；
3. **判定是机械的**：逐对三分类 + 2-of-3 汇总，支持/推翻/不显著三档都能造出来。

跑：python tests\\test_pitfalls_ab.py -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.registry import Registry               # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter        # noqa: E402

_spec = importlib.util.spec_from_file_location("pitfalls_ab", ROOT / "tools" / "pitfalls_ab.py")
pa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pa)

REG = Registry.load()
CNT = HeuristicCounter()


class TestDesignMatchesRegistry(unittest.TestCase):
    def test_three_experiments(self):
        self.assertEqual([e["id"] for e in pa.EXPERIMENTS],
                         ["P1-token-names", "P2-realword-suppression", "P3-krea2-truncation"])

    def test_pitfall_claim_is_still_traceable_in_registry(self):
        """预注册的原文必须仍能在 registry 里检索到（原文或关键词，注释也算）。

        2026-11-01 实验后 Lead 已按 docs/30 §5 代落：被推翻的条目被改写、原说法
        保留在 YAML 注释里 ⇒ 这里不能再要求「pitfalls 列表里逐字出现」。
        """
        raw = pa.REGISTRY_PATH.read_text(encoding="utf-8")
        for exp in pa.EXPERIMENTS:
            key = exp["pitfall_key"]
            with self.subTest(exp=exp["id"]):
                traceable = (pa.PITFALL_TEXT[key] in raw
                             or pa.PITFALL_KEYWORD[key] in raw)
                self.assertTrue(traceable, "registry 里找不到原说法/关键词")
                self.assertTrue(REG.get(exp["registry_target"]).get("pitfalls"))

    def test_tokenizer_conf_requires_model_and_never_hardcodes_paths(self):
        saved = {k: os.environ.get(k) for k in
                 ("MOSS_LLM_MODEL", "G7_MODEL", "MOSS_LLM_MMPROJ",
                  "G7_MMPROJ", "MOSS_LLM_SERVER", "G7_SERVER")}
        try:
            for k in saved:
                os.environ.pop(k, None)
            with self.assertRaises(RuntimeError):
                pa.resolve_tokenizer_conf()
            os.environ["MOSS_LLM_MODEL"] = "some/model.gguf"
            conf = pa.resolve_tokenizer_conf()
            self.assertEqual(conf["model"], "some/model.gguf")
            # 公开仓硬要求：模板里不许出现盘符/本机路径
            for key in ("model", "mmproj", "exe"):
                self.assertNotIn(":\\", pa.TOKENIZER[key])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_backends_have_api_templates(self):
        idx = pa.load_index()
        for exp in pa.EXPERIMENTS:
            self.assertIn(exp["backend"], idx["backends"])

    def test_flux_family_is_recorded_missing_not_faked(self):
        idx = pa.load_index()
        self.assertNotIn("flux3", idx["backends"])
        self.assertIn("flux3", idx["unavailable"])
        self.assertIn("flux1_dev", idx["unavailable"])

    def test_check_design_is_green(self):
        self.assertEqual(pa.check_design(), [])


class TestPairValidity(unittest.TestCase):
    def test_at_least_three_pairs_per_experiment(self):
        for exp in pa.EXPERIMENTS:
            with self.subTest(exp=exp["id"]):
                self.assertGreaterEqual(len(exp["pairs"]), 3)

    def test_pair_ids_unique(self):
        ids = [p["id"] for e in pa.EXPERIMENTS for p in e["pairs"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_same_seed_and_size_for_both_arms(self):
        for exp in pa.EXPERIMENTS:
            for pair in exp["pairs"]:
                with self.subTest(pair=pair["id"]):
                    self.assertIsInstance(pair["seed"], int)
                    self.assertEqual(pair["width"] % 16, 0)
                    self.assertEqual(pair["height"] % 16, 0)
                    # A/B 用同一份 pair，没有独立的 seed/size 字段 ⇒ 结构上不可能不一致；
                    # 这里再钉住 build_workflow 没有把 seed 随臂改动。
                    wf_a = pa.build_workflow(exp["backend"], "A", pair["seed"],
                                             pair["width"], pair["height"], "x/a")
                    wf_b = pa.build_workflow(exp["backend"], "B", pair["seed"],
                                             pair["width"], pair["height"], "x/b")
                    sa = wf_a[pa.load_index()["backends"][exp["backend"]]["nodes"]["sampler"]]["inputs"]
                    sb = wf_b[pa.load_index()["backends"][exp["backend"]]["nodes"]["sampler"]]["inputs"]
                    for key in ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"):
                        self.assertEqual(sa[key], sb[key], key)
                    la = wf_a[pa.load_index()["backends"][exp["backend"]]["nodes"]["latent"]]["inputs"]
                    lb = wf_b[pa.load_index()["backends"][exp["backend"]]["nodes"]["latent"]]["inputs"]
                    self.assertEqual(la["width"], lb["width"])
                    self.assertEqual(la["height"], lb["height"])

    def test_a_and_b_are_not_identical(self):
        for exp in pa.EXPERIMENTS:
            for pair in exp["pairs"]:
                a, b = pa.resolved_prompts(exp, pair)
                with self.subTest(pair=pair["id"]):
                    self.assertNotEqual(a, b)

    def test_only_the_tested_tokens_differ(self):
        """P1/P2：B 是 A 的前缀（A 只是在同一句后追加被测词），不是另写一段。"""
        for exp in pa.EXPERIMENTS:
            if exp["pair_rule"] == "tail_lost":
                continue
            for pair in exp["pairs"]:
                a, b = pa.resolved_prompts(exp, pair)
                stem = pair["base"].rstrip(".").strip()
                with self.subTest(pair=pair["id"]):
                    self.assertTrue(a.startswith(stem), "A 没从同一基础句起笔")
                    self.assertTrue(b.startswith(stem), "B 没从同一基础句起笔")


class TestTruncationPair(unittest.TestCase):
    def setUp(self):
        self.exp = [e for e in pa.EXPERIMENTS if e["pair_rule"] == "tail_lost"][0]

    def test_filler_is_deterministic_and_long_enough(self):
        self.assertEqual(pa.build_filler(), pa.build_filler())
        self.assertGreaterEqual(CNT.count(pa.build_filler()), 900)

    def test_ab_same_words_same_length_only_order_differs(self):
        for pair in self.exp["pairs"]:
            a, b = pa.resolved_prompts(self.exp, pair)
            with self.subTest(pair=pair["id"]):
                self.assertEqual(CNT.count(a), CNT.count(b))
                self.assertEqual(sorted(a.split()), sorted(b.split()))

    def test_cues_inside_first_512_in_A_and_after_in_B(self):
        for pair in self.exp["pairs"]:
            a, b = pa.resolved_prompts(self.exp, pair)
            cues = pair["A"][len(pair["base"]):].strip()
            head = pair["base"] + " " + cues
            with self.subTest(pair=pair["id"]):
                self.assertLessEqual(CNT.count(head), 512)      # A：靠前，不被截
                pos_in_b = b.find(cues)
                self.assertGreater(pos_in_b, 0)
                self.assertGreater(CNT.count(b[:pos_in_b]), 512)  # B：关键内容在截断点之后

    def test_control_must_be_the_bare_base(self):
        pair = dict(self.exp["pairs"][0])
        pair["B"] = pair["base"] + " extra words"
        with self.assertRaises(ValueError):
            pa.resolved_prompts(self.exp, pair)


class TestWorkflowBuilder(unittest.TestCase):
    def test_workflow_injects_prompt_seed_and_prefix(self):
        wf = pa.build_workflow("moss-image", "hello", 123, 768, 512, "pfx/x")
        nodes = pa.load_index()["backends"]["moss-image"]["nodes"]
        self.assertEqual(wf[nodes["positive"]]["inputs"]["text"], "hello")
        self.assertEqual(wf[nodes["sampler"]]["inputs"]["seed"], 123)
        self.assertEqual(wf[nodes["latent"]]["inputs"]["width"], 768)
        self.assertEqual(wf[nodes["latent"]]["inputs"]["height"], 512)
        self.assertEqual(wf[nodes["save"]]["inputs"]["filename_prefix"], "pfx/x")

    def test_qwen_builds_shift_node(self):
        wf = pa.build_workflow("qwen-image-2_1", "hi", 7, 1024, 1024, "p")
        self.assertIn("10", wf)
        self.assertEqual(wf["10"]["class_type"], "ModelSamplingAuraFlow")
        self.assertEqual(wf["10"]["inputs"]["model"], ["1", 0])

    def test_all_templates_links_reference_existing_nodes(self):
        for backend in pa.load_index()["backends"]:
            wf = pa.load_template(backend)
            with self.subTest(backend=backend):
                for nid, node in wf.items():
                    for name, val in node["inputs"].items():
                        if isinstance(val, list):
                            self.assertIn(str(val[0]), wf, "%s.%s" % (nid, name))
                            self.assertIsInstance(val[1], int)


class TestWorkflowValidator(unittest.TestCase):
    def _infos(self):
        """最小伪 /object_info：够跑校验器，不需要连宿主。"""
        return {
            "UNETLoader": {"input": {"required": {
                "unet_name": ["COMBO"], "weight_dtype": ["COMBO"]}},
                "output": [["MODEL"]]},
            "KSampler": {"input": {"required": {
                "model": ["MODEL"], "seed": ["INT"], "steps": ["INT"], "cfg": ["FLOAT"],
                "sampler_name": ["COMBO"], "scheduler": ["COMBO"],
                "positive": ["CONDITIONING"], "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"]}},
                "output": [["LATENT"]]},
        }

    def _mini_wf(self):
        return {
            "1": {"class_type": "UNETLoader",
                  "inputs": {"unet_name": "x.safetensors", "weight_dtype": "default"}},
            "5": {"class_type": "KSampler",
                  "inputs": {"model": ["1", 0], "seed": 1, "steps": 4, "cfg": 1.0,
                             "sampler_name": "euler", "scheduler": "simple",
                             "positive": ["1", 0], "negative": ["1", 0],
                             "latent_image": ["1", 0], "denoise": 1.0}},
        }

    def test_good_workflow_passes(self):
        wf = self._mini_wf()
        # 补上真实模板里必然存在的其余节点不参与本伪 schema 的校验
        self.assertEqual(pa.validate_workflow(wf, self._infos()), [])

    def test_missing_required_input_is_caught(self):
        wf = self._mini_wf()
        del wf["1"]["inputs"]["unet_name"]
        problems = pa.validate_workflow(wf, self._infos())
        self.assertTrue(any("缺必填输入 unet_name" in p for p in problems), problems)

    def test_dangling_link_is_caught(self):
        wf = self._mini_wf()
        wf["5"]["inputs"]["model"] = ["99", 0]
        problems = pa.validate_workflow(wf, self._infos())
        self.assertTrue(any("不存在的节点 99" in p for p in problems), problems)

    def test_unknown_node_type_is_caught(self):
        wf = self._mini_wf()
        wf["7"] = {"class_type": "NoSuchNode", "inputs": {}}
        problems = pa.validate_workflow(wf, self._infos())
        self.assertTrue(any("没有节点类型" in p for p in problems), problems)


class TestJudge(unittest.TestCase):
    def test_tokens_effective_three_classes(self):
        exp = pa.EXPERIMENTS[0]
        support = pa.classify_pair(exp, {"a_shows_effect": False, "b_shows_effect": False})
        refute = pa.classify_pair(exp, {"a_shows_effect": True, "b_shows_effect": False})
        inc = pa.classify_pair(exp, {"a_shows_effect": "unclear"})
        self.assertEqual(support[0], pa.SUPPORT)
        self.assertEqual(refute[0], pa.REFUTE)
        self.assertEqual(inc[0], pa.INCONCLUSIVE)

    def test_style_suppression_uses_rank_order(self):
        exp = pa.EXPERIMENTS[1]
        self.assertEqual(pa.classify_pair(exp, {"style_a": "photoreal",
                                                "style_b": "anime"})[0], pa.SUPPORT)
        self.assertEqual(pa.classify_pair(exp, {"style_a": "anime",
                                                "style_b": "photoreal"})[0], pa.REFUTE)
        self.assertEqual(pa.classify_pair(exp, {"style_a": "anime",
                                                "style_b": "anime"})[0], pa.REFUTE)
        self.assertEqual(pa.classify_pair(exp, {"style_a": "水墨",
                                                "style_b": "anime"})[0], pa.INCONCLUSIVE)

    def test_tail_lost_three_classes(self):
        exp = pa.EXPERIMENTS[2]
        support = pa.classify_pair(exp, {"a_cues": [True, True], "b_cues": [True, False]})
        refute = pa.classify_pair(exp, {"a_cues": [True, True], "b_cues": [True, True]})
        inc = pa.classify_pair(exp, {"a_cues": [True, False], "b_cues": [True, False]})
        bad = pa.classify_pair(exp, {"a_cues": [True], "b_cues": [True, True]})
        self.assertEqual(support[0], pa.SUPPORT)
        self.assertEqual(refute[0], pa.REFUTE)
        self.assertEqual(inc[0], pa.INCONCLUSIVE)
        self.assertEqual(bad[0], pa.INCONCLUSIVE)

    def test_aggregate_two_of_three(self):
        self.assertEqual(pa.aggregate([pa.SUPPORT, pa.SUPPORT, pa.REFUTE])[0], pa.SUPPORT)
        self.assertEqual(pa.aggregate([pa.REFUTE, pa.REFUTE, pa.SUPPORT])[0], pa.REFUTE)
        self.assertEqual(pa.aggregate([pa.SUPPORT, pa.REFUTE, pa.INCONCLUSIVE])[0],
                         pa.INCONCLUSIVE)
        self.assertEqual(pa.aggregate([pa.SUPPORT, pa.SUPPORT, pa.INCONCLUSIVE])[0],
                         pa.SUPPORT)

    def test_judge_all_three_verdicts_from_fake_observations(self):
        obs = pa.observation_template()
        exp1, exp2, exp3 = (e["id"] for e in pa.EXPERIMENTS)
        # P1：三对都「写了无效」⇒ 支持
        for pid in obs[exp1]:
            obs[exp1][pid]["a_shows_effect"] = False
            obs[exp1][pid]["b_shows_effect"] = False
        # P2：两对压制 + 一对不 ⇒ 支持
        pids = list(obs[exp2])
        obs[exp2][pids[0]].update(style_a="photoreal", style_b="anime")
        obs[exp2][pids[1]].update(style_a="hybrid", style_b="anime")
        obs[exp2][pids[2]].update(style_a="anime", style_b="anime")
        # P3：一对出现位置效应、两对没出现 ⇒ 推翻
        pids = list(obs[exp3])
        obs[exp3][pids[0]].update(a_cues=[True, True], b_cues=[True, False])
        obs[exp3][pids[1]].update(a_cues=[True, True], b_cues=[True, True])
        obs[exp3][pids[2]].update(a_cues=[True, True], b_cues=[True, True])
        verdicts = pa.judge(obs)
        self.assertEqual(verdicts["verdict"][exp1], pa.SUPPORT)
        self.assertEqual(verdicts["verdict"][exp2], pa.SUPPORT)
        self.assertEqual(verdicts["verdict"][exp3], pa.REFUTE)
        self.assertEqual(verdicts["experiments"][0]["verdict_zh"], "支持")
        md = pa.report_markdown(verdicts)
        self.assertIn("P1", md)
        self.assertIn("支持", md)
        self.assertIn("推翻", md)

    def test_observation_template_fields_follow_rule(self):
        tpl = pa.observation_template()
        for exp in pa.EXPERIMENTS:
            for pid in tpl[exp["id"]]:
                fields = set(tpl[exp["id"]][pid])
                if exp["pair_rule"] == "tokens_effective":
                    self.assertTrue({"a_shows_effect", "b_shows_effect"} <= fields)
                elif exp["pair_rule"] == "style_suppression":
                    self.assertTrue({"style_a", "style_b"} <= fields)
                else:
                    self.assertTrue({"a_cues", "b_cues"} <= fields)

    def test_observation_draft_fills_compare_from_manifest(self):
        manifest = {"experiments": [{
            "id": pa.EXPERIMENTS[0]["id"],
            "pairs": [{"id": pa.EXPERIMENTS[0]["pairs"][0]["id"],
                       "compare": "out/pitfalls_ab/x/compare.png"}],
        }]}
        draft = pa.observation_draft(manifest)
        slot = draft[pa.EXPERIMENTS[0]["id"]][pa.EXPERIMENTS[0]["pairs"][0]["id"]]
        self.assertEqual(slot["compare"], "out/pitfalls_ab/x/compare.png")
        self.assertIn("a_shows_effect", slot)

    def test_observation_draft_falls_back_to_arm_images(self):
        manifest = {"experiments": [{
            "id": pa.EXPERIMENTS[1]["id"],
            "pairs": [{"id": pa.EXPERIMENTS[1]["pairs"][0]["id"],
                       "arms": {"A": {"image": "a.png"}, "B": {"image": "b.png"}}}],
        }]}
        draft = pa.observation_draft(manifest)
        slot = draft[pa.EXPERIMENTS[1]["id"]][pa.EXPERIMENTS[1]["pairs"][0]["id"]]
        self.assertIn("a.png", slot["compare"])
        self.assertIn("b.png", slot["compare"])

    def test_pair_table_markdown_has_every_pair(self):
        obs = pa.observation_template()
        exp = pa.EXPERIMENTS[0]
        for pid in obs[exp["id"]]:
            obs[exp["id"]][pid].update(a_shows_effect=False, b_shows_effect=False)
        verdicts = pa.judge(obs)
        manifest = {"experiments": [{"id": exp["id"], "pairs": [
            {"id": p["id"], "compare": "out/x.png"} for p in exp["pairs"]]}]}
        md = pa.pair_table_markdown(verdicts, manifest)
        for p in exp["pairs"]:
            self.assertIn(p["id"], md)
        self.assertIn("支持", md)
        self.assertIn("compare", md)

    def test_tail_lost_low_real_tokens_is_not_a_valid_test(self):
        exp = pa.EXPERIMENTS[2]
        pair = exp["pairs"][0]
        obs = {"a_cues": [True, True], "b_cues": [True, True], "b_tokens": 480}
        cls, reason = pa.classify_pair(exp, obs)
        self.assertEqual(cls, pa.INCONCLUSIVE)
        self.assertIn("512", reason)
        obs["b_tokens"] = 900
        self.assertEqual(pa.classify_pair(exp, obs)[0], pa.REFUTE)
        self.assertIn(pair["id"], pa.observation_template()[exp["id"]])

    def test_inject_tokens_writes_real_counts_into_observations(self):
        obs = pa.observation_template()
        tok = {"pairs": {"%s/%s" % (pa.EXPERIMENTS[2]["id"],
                                    pa.EXPERIMENTS[2]["pairs"][0]["id"]):
                         {"A": 900, "B": 905}}}
        pa.inject_tokens(obs, tok)
        slot = obs[pa.EXPERIMENTS[2]["id"]][pa.EXPERIMENTS[2]["pairs"][0]["id"]]
        self.assertEqual(slot["a_tokens"], 900)
        self.assertEqual(slot["b_tokens"], 905)

    def test_validate_observations_flags_all_blank_and_accepts_filled(self):
        blank = pa.observation_template()
        problems = pa.validate_observations(blank)
        self.assertEqual(len(problems), sum(len(e["pairs"]) for e in pa.EXPERIMENTS))
        filled = pa.observation_template()
        for exp in pa.EXPERIMENTS:
            for pid in filled[exp["id"]]:
                slot = filled[exp["id"]][pid]
                if exp["pair_rule"] == "tokens_effective":
                    slot.update(a_shows_effect=False, b_shows_effect=False)
                elif exp["pair_rule"] == "style_suppression":
                    slot.update(style_a="anime", style_b="anime")
                else:
                    slot.update(a_cues=[True, True], b_cues=[True, True])
        self.assertEqual(pa.validate_observations(filled), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
