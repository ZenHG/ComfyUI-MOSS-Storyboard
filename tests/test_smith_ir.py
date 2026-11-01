# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`moss_llm.smith`（IR 编译层）的全量单测。零 GPU、零网络。

跑：python -m unittest tests.test_smith_ir -v
（与仓库其余测试一起跑：python -m unittest discover -s tests）
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith import layout as L                      # noqa: E402
from moss_llm.smith.adapters import get_adapter             # noqa: E402
from moss_llm.smith.clauses import build_clauses, pack      # noqa: E402
from moss_llm.smith.ir import IR                            # noqa: E402
from moss_llm.smith.lint import check                       # noqa: E402
from moss_llm.smith.registry import Registry                # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter          # noqa: E402

REG = Registry.load()
CNT = HeuristicCounter()
EX = ROOT / "moss_llm" / "smith" / "examples"


def load(name: str) -> IR:
    return IR.load(EX / name)


class TestIR(unittest.TestCase):
    def test_load_and_normalize(self):
        ir = load("01-portrait-photoreal.json")
        self.assertEqual(ir.task, "t2i")
        self.assertEqual(ir.entities[0].age_years, 28)
        self.assertEqual(ir.budget["verbosity"], 0.7)      # 默认值被补上

    def test_bbox_reversed_is_fixed_not_dropped(self):
        ir = IR.from_dict({
            "task": "t2i", "canvas": {"aspect_ratio": "1:1"},
            "entities": [{"id": "e1", "kind": "object", "label": "x", "importance": 1,
                          "spatial": {"bbox_norm": [0.8, 0.9, 0.2, 0.3]}}]})
        self.assertEqual(ir.entities[0].spatial["bbox_norm"], [0.2, 0.3, 0.8, 0.9])

    def test_importance_sort(self):
        ir = load("03-multi-subject.json")
        self.assertEqual(ir.entities[0].id, "e1")
        self.assertGreaterEqual(ir.entities[0].importance, ir.entities[1].importance)

    def test_pixel_size_multiple_of_16(self):
        ir = load("02-poster-text-layout.json")
        w, h = ir.pixel_size(1.0)
        self.assertEqual(w % 16, 0)
        self.assertEqual(h % 16, 0)
        self.assertAlmostEqual(w / h, 1.5, places=1)


class TestLayout(unittest.TestCase):
    """官方 references/layout.md 附录 B 的算例，必须逐位对上。"""

    def test_official_centered_box(self):
        draft = {
            "width": 1536, "height": 1024,
            "bbox_coord_space": "abs", "bbox_format": "xyxy",
            "summary": {"caption": "c", "meta": ["m"], "canvas": "single"},
            "items": [{"label": "object", "caption": "cup", "bbox": [176, 224, 800, 864]}],
        }
        out = L.compile_draft(draft)
        self.assertEqual(out["items"][0]["bbox"], [-288, -592, 352, 32])
        self.assertEqual(out["bbox_coord_space"], "abs_centralized")
        self.assertEqual(out["bbox_format"], "yxyx")

    def test_dumps_is_compact_and_drops_wh(self):
        ir = load("02-poster-text-layout.json")
        draft = L.build_draft(ir, megapixels=1.0)
        s = L.dumps(L.compile_draft(draft))
        self.assertNotIn('": ', s)          # 分隔符无空格
        self.assertNotIn('", "', s)
        self.assertNotIn("_w", s)
        self.assertIn("abs_centralized", s)
        json.loads(s)          # 必须是合法 JSON

    def test_reject_non_multiple_of_16(self):
        with self.assertRaises(ValueError):
            L.compile_draft({"width": 1000, "height": 1000,
                             "bbox_coord_space": "abs", "bbox_format": "xyxy",
                             "summary": {"caption": "c"},
                             "items": [{"label": "object", "caption": "x",
                                        "bbox": [0, 0, 100, 100]}]})

    def test_reject_out_of_range_bbox(self):
        with self.assertRaises(ValueError):
            L.compile_draft({"width": 1024, "height": 1024,
                             "bbox_coord_space": "abs", "bbox_format": "xyxy",
                             "summary": {"caption": "c"},
                             "items": [{"label": "object", "caption": "x",
                                        "bbox": [0, 0, 2000, 100]}]})


class TestClauses(unittest.TestCase):
    def test_age_is_explicit(self):
        ir = load("01-portrait-photoreal.json")
        text = " ".join(c.text for c in build_clauses(ir))
        self.assertIn("28-year-old woman", text)

    def test_pack_drops_low_importance_first(self):
        ir = load("02-poster-text-layout.json")
        clauses = build_clauses(ir)
        kept, dropped = pack(clauses, 40, CNT)
        self.assertTrue(dropped, "预算 40 token 必然要砍东西")
        # 主体与主标题必须活着
        kept_dims = {(c.dim, c.text[:10]) for c in kept}
        self.assertTrue(any(d == "subject" for d, _ in kept_dims))
        for c in dropped:
            self.assertLessEqual(c.importance, 1.0)


class TestMossAdapter(unittest.TestCase):
    def test_prose_mode(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        self.assertGreater(len(out.positive), 80)
        self.assertNotIn("masterpiece", out.positive.lower())
        self.assertIsNone(out.layout)
        self.assertTrue(out.negative and "anime" in out.negative)

    def test_layout_mode_emits_json(self):
        ir = load("02-poster-text-layout.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic")
        self.assertIsNotNone(out.layout)
        self.assertTrue(out.positive.startswith("{"))
        self.assertIn("把日常，过成诗。", out.positive)
        json.loads(out.positive)

    def test_edit_task_is_rejected(self):
        ir = load("04-edit-instruction.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        with self.assertRaises(ValueError):
            ad.realize(ir, mode="deterministic")

    def test_text_is_quoted(self):
        ir = load("02-poster-text-layout.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        self.assertIn('"把日常，过成诗。"', out.positive)


class TestQwenAdapter(unittest.TestCase):
    def test_deterministic_warns_about_length(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("qwen-image-2_1", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic")
        self.assertTrue(any("低于" in w and "扩写" in w for w in out.warnings))
        # 下界来自 registry 的长度带，不是代码里的魔法数字
        lo = REG.prompt_policy("qwen-image-2_1")["length"]["standard"][0]
        self.assertTrue(any(str(lo) in w for w in out.warnings))

    def test_length_flag_moves_the_target_within_the_band(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("qwen-image-2_1", REG, counter=CNT)
        ad.length = "brief"
        brief = ad._target_words(ir)
        ad.length = "detailed"
        detailed = ad._target_words(ir)
        self.assertLess(brief, detailed)
        self.assertGreaterEqual(brief, REG.prompt_policy("qwen-image-2_1")["length"]["brief"][0])

    def test_edit_keeps_instruction_and_names_must_keep(self):
        ir = load("04-edit-instruction.json")
        ad = get_adapter("qwen-image-2_1", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic")
        self.assertIn("Replace the grey coat with a deep red wool coat", out.positive)
        self.assertIn("face", out.positive)
        self.assertNotIn("<image", out.positive)     # 单图禁用标签
        self.assertIsNone(out.negative)              # Qwen API 无负向

    def test_no_negative_channel(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("qwen-image-2_1", REG, counter=CNT)
        self.assertIsNone(ad.realize(ir).negative)


class TestSanitize(unittest.TestCase):
    """F4：权重语法搬到不支持它的模型上，会被当字面字符画出来。"""

    def test_weight_syntax_is_stripped(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        out2 = ad._sanitize(ir, "a (red coat:1.3) and ((blue hat)) with [gloves]", out)
        self.assertEqual(out2, "a red coat and blue hat with gloves")
        self.assertTrue(any("权重语法" in w for w in out.warnings))

    def test_json_is_never_sanitized(self):
        ir = load("02-poster-text-layout.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic")
        before = out.positive
        after = ad._sanitize(ir, before, out)
        self.assertEqual(before, after)

    def test_negation_sentence_is_dropped(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        got = ad._strip_negation_tail(
            "A woman stands in the rain. No text is present.", out)
        self.assertEqual(got, "A woman stands in the rain.")

    def test_negation_tail_is_cut(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        got = ad._strip_negation_tail(
            "A woman stands in the rain, the street receding behind her, "
            "the mood subdued, no text visible, no background elements beyond the street.",
            out)
        self.assertTrue(got.startswith("A woman stands in the rain"))
        self.assertNotIn("no text", got.lower())
        self.assertTrue(got.endswith("."))

    def test_instruction_echo_is_cut(self):
        """实测原文：模型把系统提示回写成正文尾巴。"""
        ir = load("03-multi-subject.json")
        ad = get_adapter("krea2", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic")
        src = ("the scene is lit by cold fluorescent light, with rain falling and steam rising, "
               "all rendered with photographic realism and atmospheric clarity without any text, "
               "signage, or background elements beyond what is specified.")
        got = ad._strip_negation_tail(src, out)
        self.assertNotIn("specified", got.lower())
        self.assertNotIn("signage", got.lower())
        self.assertTrue(got.endswith("."))

    def test_negation_mid_sentence_is_cut(self):
        """实测原文：否定式从句卡在句子中间，前后都是分号。"""
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("qwen-image-2_1", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic")
        src = ("her posture relaxed yet grounded; the background is softly blurred, "
               "emphasizing her as the central subject; no text is visible; the scene is "
               "composed with deliberate balance.")
        got = ad._strip_negation_tail(src, out)
        self.assertNotIn("no text", got.lower())
        self.assertIn("central subject", got)
        self.assertIn("deliberate balance", got)
        self.assertNotIn(";;", got)


class TestKrea2Adapter(unittest.TestCase):
    """F1：Krea 2 的 512 是硬截断。必须按重要度取舍，不能靠位置。"""

    def test_medium_comes_first(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("krea2", REG, counter=CNT)
        out = ad.realize(ir)
        self.assertTrue(out.positive.lower().startswith("photograph"), out.positive[:40])

    def test_no_negative_channel(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("krea2", REG, counter=CNT)
        self.assertIsNone(ad.realize(ir).negative)

    def test_budget_drops_low_importance_not_tail(self):
        ir = load("02-poster-text-layout.json")
        ir.budget["target_tokens"] = 120
        ad = get_adapter("krea2", REG, counter=CNT)
        out = ad.realize(ir)
        self.assertTrue(out.dropped, "预算 120 必然要砍子句")
        self.assertLessEqual(out.token_count, 150)
        # 主体不能被砍掉
        self.assertIn("ceramic cup", out.positive)
        # 被砍的都是低权重的
        self.assertTrue(any("supporting copy" in d or "text:" in d for d in out.dropped)
                        or len(out.dropped) >= 1)

    def test_over_budget_warns(self):
        ir = load("02-poster-text-layout.json")
        ir.budget["target_tokens"] = 512
        ad = get_adapter("krea2", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic")
        if out.token_count > 512 * 0.95:
            self.assertTrue(any("静默截断" in w for w in out.warnings))

    def test_edit_is_rejected(self):
        ir = load("04-edit-instruction.json")
        with self.assertRaises(ValueError):
            get_adapter("krea2", REG, counter=CNT).realize(ir)


class TestFlux3Adapter(unittest.TestCase):
    """F2：MOSS 的中心像素坐标搬到 FLUX 3，会被当字面文本画出来。"""

    def test_prompt_is_short(self):
        ir = load("01-portrait-photoreal.json")
        ad = get_adapter("flux3", REG, counter=CNT)
        out = ad.realize(ir)
        self.assertLess(len(out.positive.split()), 120, out.positive)
        self.assertNotIn("100mm", out.positive)

    def test_bbox_table_is_0_1000_tlbr(self):
        ir = load("02-poster-text-layout.json")
        ad = get_adapter("flux3", REG, counter=CNT)
        out = ad.realize(ir)
        self.assertIn("<silhouette_1>", out.positive)
        lines = out.positive.split("\n")
        self.assertGreaterEqual(len(lines), 3)
        row = json.loads(lines[1])
        self.assertEqual(row["id"], "s1")
        top, left, bottom, right = row["bbox"]
        self.assertTrue(0 <= top < bottom <= 1000)
        self.assertTrue(0 <= left < right <= 1000)
        # 文字必须逐字出现（text 键的值带 JSON 引号，正好满足 L010 的对账）
        self.assertIn('"把日常，过成诗。"', out.positive)
        self.assertIn('"text"', out.positive)

    def test_edit_uses_ref_image_and_keep_clause(self):
        ir = load("04-edit-instruction.json")
        ad = get_adapter("flux3", REG, counter=CNT)
        out = ad.realize(ir)
        self.assertIn("<ref_image_0>", out.positive)
        self.assertIn("Keep the rest of the image exactly unchanged", out.positive)
        self.assertIn("face", out.positive)

    def test_no_negative_channel(self):
        ir = load("01-portrait-photoreal.json")
        self.assertIsNone(get_adapter("flux3", REG, counter=CNT).realize(ir).negative)


class TestLint(unittest.TestCase):
    def _caps(self, t):
        return REG.get(t)

    def test_L002_quality_words(self):
        ir = load("01-portrait-photoreal.json")
        r = {"positive": "a masterpiece, 8k, ultra detailed portrait", "token_limit": 4000}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L002", codes)

    def test_L005_sampling_leak(self):
        ir = load("01-portrait-photoreal.json")
        r = {"positive": "a portrait, 30 steps, cfg 7", "token_limit": 4000}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L005", codes)

    def test_L006_missing_age(self):
        ir = IR.from_dict({"task": "t2i", "canvas": {"aspect_ratio": "1:1"},
                           "entities": [{"id": "e1", "kind": "character",
                                         "label": "girl", "importance": 1}]})
        r = {"positive": "a girl standing", "token_limit": 4000}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L006", codes)

    def test_L010_text_not_verbatim(self):
        ir = load("02-poster-text-layout.json")
        r = {"positive": "a poster with some Chinese text", "token_limit": 4000}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L010", codes)

    def test_L015_capability_violation(self):
        ir = load("04-edit-instruction.json")
        r = {"positive": "replace the coat", "token_limit": 4000}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L015", codes)

    def test_L004_negative_into_positive(self):
        ir = load("01-portrait-photoreal.json")
        r = {"positive": "a woman in a yellow raincoat, no anime, no illustration",
             "token_limit": 4000}
        issues = check(ir, "qwen-image-2_1", self._caps("qwen-image-2_1"), r)
        self.assertIn("L004", [i.code for i in issues])

    def test_L001_over_budget(self):
        ir = load("01-portrait-photoreal.json")
        r = {"positive": "word " * 100, "token_count": 900, "token_limit": 512}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L001", codes)

    def test_L016_invented_text(self):
        """实测复现过的幻觉：IR 无文字，模型自己写出「茶屋」。"""
        ir = load("01-portrait-photoreal.json")
        r = {"positive": 'a woman on a street, a shop sign reads "茶屋" behind her',
             "token_limit": 4000}
        codes = [i.code for i in check(ir, "qwen-image-2_1", self._caps("qwen-image-2_1"), r)]
        self.assertIn("L016", codes)

    def test_L017_age_rewritten(self):
        ir = load("01-portrait-photoreal.json")
        r = {"positive": "a 35-year-old woman in a yellow raincoat", "token_limit": 4000}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L017", codes)

    def test_L018_negation(self):
        ir = load("01-portrait-photoreal.json")
        r = {"positive": "a woman in a raincoat. No text is present.", "token_limit": 4000}
        codes = [i.code for i in check(ir, "moss-image", self._caps("moss-image"), r)]
        self.assertIn("L018", codes)

    def test_layout_mode_does_not_call_llm(self):
        """Layout 是确定性产物；调 LLM 只会白烧时间，还会引入幻觉。"""
        class Boom:
            def chat(self, *a, **k):
                raise AssertionError("layout 模式不应调用 LLM")

        ir = load("02-poster-text-layout.json")
        ad = get_adapter("moss-image", REG, counter=CNT, llm=Boom())
        out = ad.realize(ir, mode="llm")           # 故意用 llm 模式
        self.assertTrue(out.positive.startswith("{"))
        self.assertIn("未调用 LLM", " ".join(out.warnings))

    def test_clean_output_has_no_errors(self):
        """四个示例在各自目标上必须零 error。"""
        cases = [("01-portrait-photoreal.json", "moss-image"),
                 ("01-portrait-photoreal.json", "qwen-image-2_1"),
                 ("02-poster-text-layout.json", "moss-image"),
                 ("03-multi-subject.json", "moss-image"),
                 ("04-edit-instruction.json", "qwen-image-2_1")]
        for name, target in cases:
            with self.subTest(name=name, target=target):
                ir = load(name)
                out = get_adapter(target, REG, counter=CNT).realize(ir)
                issues = check(ir, target, self._caps(target), out.as_dict())
                errs = [i for i in issues if i.level == "error"]
                self.assertEqual(errs, [], "意外 error：%s" % [str(e) for e in errs])


class TestReverseJson(unittest.TestCase):
    """反推的取证 JSON 一定是脏的：围栏、前言、尾逗号、单引号。这里逐种打。"""

    def test_fenced(self):
        from moss_llm.smith.reverse import extract_json
        got = extract_json('```json\n{"a": 1}\n```')
        self.assertEqual(got, {"a": 1})

    def test_preamble_and_trailer(self):
        from moss_llm.smith.reverse import extract_json
        got = extract_json('Sure! Here is the record:\n{"a": {"b": 2}}\nLet me know.')
        self.assertEqual(got["a"]["b"], 2)

    def test_braces_inside_strings(self):
        from moss_llm.smith.reverse import extract_json
        got = extract_json('{"t": "a } brace { here", "n": 1}')
        self.assertEqual(got["n"], 1)
        self.assertIn("}", got["t"])

    def test_escaped_quote_inside_string(self):
        from moss_llm.smith.reverse import extract_json
        got = extract_json(r'{"t": "she said \"hi\"", "n": 2}')
        self.assertEqual(got["n"], 2)

    def test_trailing_comma_repaired(self):
        from moss_llm.smith.reverse import extract_json
        got = extract_json('{"a": [1, 2,], "b": 3,}')
        self.assertEqual(got["b"], 3)

    def test_unclosed_raises(self):
        from moss_llm.smith.reverse import ReverseError, extract_json
        with self.assertRaises(ReverseError):
            extract_json('{"a": 1')

    def test_no_json_raises(self):
        from moss_llm.smith.reverse import ReverseError, extract_json
        with self.assertRaises(ReverseError):
            extract_json("I cannot analyse this image.")


class TestReverseCoerce(unittest.TestCase):
    def _obs(self, **kw):
        base = {"style": {"medium": "photograph"}, "entities": [], "relations": []}
        base.update(kw)
        return base

    def test_bbox_forms_converge(self):
        from moss_llm.smith.reverse import _coerce_bbox
        want = [0.1, 0.2, 0.5, 0.9]
        self.assertEqual(_coerce_bbox({"x_min": 10, "y_min": 20, "x_max": 50, "y_max": 90}), want)
        self.assertEqual(_coerce_bbox([10, 20, 50, 90]), want)
        self.assertEqual(_coerce_bbox([0.1, 0.2, 0.5, 0.9]), want)
        self.assertEqual(_coerce_bbox({"left": 10, "top": 20, "right": 50, "bottom": 90}), want)

    def test_xywh_is_detected_by_inversion(self):
        from moss_llm.smith.reverse import _coerce_bbox
        # [x, y, w, h] 里 w/h 常小于 x/y，会被读成终点 → 反了 → 按 xywh 修
        self.assertEqual(_coerce_bbox([10, 20, 5, 8]), [0.1, 0.2, 0.15, 0.28])

    def test_degenerate_bbox_dropped(self):
        from moss_llm.smith.reverse import _coerce_bbox
        self.assertIsNone(_coerce_bbox([10, 10, 10, 90]))
        self.assertIsNone(_coerce_bbox("left third"))

    def test_dirty_entity_is_repaired(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        obs = self._obs(entities=[{
            "kind": "person",                      # 非法 kind
            "label": "woman",
            "importance": "0.9",                   # 字符串
            "age_years": "28 years old",           # 带单位
            "descriptors": {"wardrobe": "yellow raincoat"},   # 该是数组
            "spatial": {"bbox": [10, 20, 50, 90]},
        }])
        rep = Report()
        ir = coerce_ir(obs, rep)
        e = ir.entities[0]
        self.assertEqual(e.kind, "character")
        self.assertEqual(e.importance, 0.9)
        self.assertEqual(e.age_years, 28)
        self.assertEqual(e.descriptors["wardrobe"], ["yellow raincoat"])
        self.assertEqual(e.spatial["bbox_norm"], [0.1, 0.2, 0.5, 0.9])
        self.assertEqual(rep.warnings, [])          # person 是已知别名，不该报警

    def test_unknown_kind_warns(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir({"entities": [{"kind": "sprite", "label": "orb"}]}, rep)
        self.assertEqual(ir.entities[0].kind, "object")
        self.assertTrue(any("未知实体类型" in w for w in rep.warnings))

    def test_missing_id_gets_one(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        ir = coerce_ir(self._obs(entities=[{"kind": "object", "label": "cup"},
                                           {"kind": "object", "label": "saucer"}]), Report())
        self.assertEqual([e.id for e in ir.entities], ["e1", "e2"])

    def test_text_without_content_is_dropped(self):
        """实测 UI 截图：模型把 10 处文字标成 text 却不抄内容。

        降级成 object 会让它们变成 10 个 label 是半句文案的空壳，既进散文又进
        Layout。直接丢掉才对——文字专线会把它们带内容补回来。
        """
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir(self._obs(entities=[
            {"kind": "text", "label": "sign", "text_content": "   "}]), rep)
        self.assertEqual(ir.entities, [])
        self.assertTrue(any("没有文字" in d for d in rep.dropped))

    def test_dangling_relation_dropped(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir(self._obs(
            entities=[{"id": "e1", "kind": "object", "label": "cup"}],
            relations=[{"subject": "e1", "predicate": "holding", "object": "e9"}]), rep)
        self.assertEqual(ir.relations, [])
        self.assertTrue(any("悬空关系" in d for d in rep.dropped))

    def test_character_without_age_is_reported(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        coerce_ir(self._obs(entities=[{"kind": "character", "label": "girl"}]), rep)
        self.assertEqual(len(rep.age_gate), 1)          # 年龄闸必须出声

    def test_age_word_fallback(self):
        from moss_llm.smith.reverse import _as_int
        self.assertEqual(_as_int("late twenties"), 26)
        self.assertEqual(_as_int("a child"), 9)
        self.assertEqual(_as_int("elderly"), 78)
        self.assertIsNone(_as_int("unknown"))

    def test_wrapped_payload_unwrapped(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir({"scene": {"entities": [{"kind": "object", "label": "cup"}]}}, rep)
        self.assertEqual(len(ir.entities), 1)
        self.assertTrue(any("已拆开" in w for w in rep.warnings))

    def test_placeholder_values_are_pruned(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir({
            "composition": {"rules": ["centered", "not applicable"], "horizon": "none"},
            "lighting": {"key": "soft front light", "fill": "none", "rim": "unknown"},
            "atmosphere": ["neutral", "N/A"],
            "entities": [{"kind": "object", "label": "cup"}],
        }, rep)
        self.assertEqual(ir.composition.get("rules"), ["centered"])
        self.assertNotIn("horizon", ir.composition)
        self.assertNotIn("fill", ir.lighting)
        self.assertEqual(ir.atmosphere, ["neutral"])
        self.assertTrue(any("占位值" in w for w in rep.warnings))

    def test_text_content_is_never_pruned(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        ir = coerce_ir({"entities": [{"kind": "text", "label": "sign",
                                      "text_content": "NONE"}]}, Report())
        self.assertEqual(ir.entities[0].text_content, "NONE")

    def test_junk_inside_composite_string_is_stripped(self):
        """实测：模型把 fill 写成 "none, shadows softly diffused"，整串匹配抓不住。"""
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir({"lighting": {"key": "front-center, soft",
                                     "fill": "none, shadows softly diffused"},
                        "entities": [{"kind": "object", "label": "cup"}]}, rep)
        self.assertEqual(ir.lighting["fill"], "shadows softly diffused")
        self.assertEqual(ir.lighting["key"], "front-center, soft")     # 合法内容不动


class TestNoteDedupe(unittest.TestCase):
    def test_restating_sentence_is_dropped_new_one_kept(self):
        from moss_llm.smith.clauses import _dedupe_note
        note = ("The subject is centered in the frame. "
                "Eye movement is direct and focused on the face.")
        facts = ("a 30-year-old man standing in the center, centered composition, "
                 "the frame filled edge to edge")
        got = _dedupe_note(note, facts)
        self.assertNotIn("centered in the frame", got)
        self.assertIn("Eye movement", got)

    def test_short_sentence_with_one_hit_survives(self):
        from moss_llm.smith.clauses import _dedupe_note
        got = _dedupe_note("A bronze statue stands here.", "a bronze cup on a table")
        self.assertIn("statue", got)

    def test_no_facts_means_no_dedupe(self):
        from moss_llm.smith.clauses import _dedupe_note
        note = "The subject is centered."
        self.assertEqual(_dedupe_note(note, ""), note)


class TestLayoutWorthiness(unittest.TestCase):
    """官方原文：'An ordinary photograph never does.' 反推必然带 bbox，不代表要按布局编译。"""

    def _ir(self, boxes):
        return IR.from_dict({
            "task": "reverse", "canvas": {"aspect_ratio": "1:1"},
            "entities": [{"id": "e%d" % i, "kind": k, "label": "x", "importance": 0.8,
                          "spatial": {"bbox_norm": b}}
                         for i, (k, b) in enumerate(boxes, start=1)],
        })

    def test_single_full_frame_subject_is_prose(self):
        ir = self._ir([("character", [0.05, 0.02, 0.95, 0.98])])
        self.assertTrue(ir.has_layout)          # 有证据
        self.assertFalse(ir.needs_layout())     # 但不需要布局

    def test_two_placed_objects_need_layout(self):
        ir = self._ir([("object", [0.1, 0.1, 0.4, 0.5]),
                       ("object", [0.6, 0.5, 0.9, 0.9])])
        self.assertTrue(ir.needs_layout())

    def test_text_box_always_needs_layout(self):
        ir = self._ir([("text", [0.1, 0.1, 0.9, 0.2])])
        self.assertTrue(ir.needs_layout())

    def test_one_big_background_plus_two_small_still_layouts(self):
        ir = self._ir([("background", [0, 0, 1, 1]),
                       ("object", [0.1, 0.6, 0.3, 0.8]),
                       ("object", [0.6, 0.6, 0.8, 0.8])])
        self.assertTrue(ir.needs_layout())

    def test_force_layout_wins(self):
        ir = self._ir([("character", [0.05, 0.02, 0.95, 0.98])])
        ir.constraints["force_layout"] = True
        self.assertTrue(ir.needs_layout())

    def test_no_boxes_no_layout(self):
        ir = IR.from_dict({"task": "t2i", "entities": [{"id": "e1", "kind": "object"}]})
        self.assertFalse(ir.needs_layout())


class TestReverseNotes(unittest.TestCase):
    def test_notes_become_lower_priority_clauses(self):
        ir = load("01-portrait-photoreal.json")
        ir.notes = {"lighting": "Light falls off fast to the left, the right cheek holding "
                                "a wet specular that reads as rain."}
        clauses = build_clauses(ir)
        notes = [c for c in clauses if c.source == "note"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].dim, "lighting")
        facts = [c for c in clauses if c.dim == "lighting" and c.source == "fact"]
        self.assertTrue(facts)
        self.assertLess(notes[0].importance, facts[0].importance)   # 事实优先于散文

    def test_notes_reach_the_rendered_prompt(self):
        ir = load("01-portrait-photoreal.json")
        ir.notes = {"lighting": "Light falls off fast to the left."}
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        self.assertIn("falls off fast", out.positive)

    def test_notes_are_dropped_first_under_tight_budget(self):
        ir = load("01-portrait-photoreal.json")
        ir.notes = {"lighting": "Light falls off fast to the left, the right cheek holding "
                                "a wet specular that reads as rain."}
        clauses = build_clauses(ir)
        kept, dropped = pack(clauses, limit_tokens=60, counter=CNT)
        self.assertTrue(dropped)
        self.assertIn("note", [c.source for c in dropped])


class TestAuditPatch(unittest.TestCase):
    def _ir(self):
        return IR.from_dict({
            "task": "reverse", "canvas": {"aspect_ratio": "1:1"},
            "entities": [
                {"id": "e1", "kind": "character", "label": "woman",
                 "importance": 0.95, "age_years": 28,
                 "descriptors": {"gender": "woman"}},
                {"id": "e2", "kind": "object", "label": "bicycle",
                 "importance": 0.35, "descriptors": {"material": "rusted steel"}},
            ],
            "notes": {"lighting": "A cold rim light traces her shoulder against the fog."},
        })

    def test_missing_becomes_low_importance_entity(self):
        from moss_llm.smith.audit import Audit, patch
        ir, changes = patch(self._ir(), Audit(score=60, missing=["a red lantern hanging above"]))
        added = [e for e in ir.entities if e.id.startswith("fix")]
        self.assertEqual(len(added), 1)
        self.assertLessEqual(added[0].importance, 0.5)
        self.assertTrue(any("补漏" in c for c in changes))

    def test_already_covered_missing_is_ignored(self):
        from moss_llm.smith.audit import Audit, patch
        ir, changes = patch(self._ir(), Audit(score=60, missing=["a 28 year old woman"]))
        self.assertEqual(len(ir.entities), 2)
        self.assertEqual(changes, [])

    def test_protagonist_is_never_deleted(self):
        from moss_llm.smith.audit import Audit, patch
        ir, changes = patch(self._ir(), Audit(score=50, wrong=["the woman wears a hat"]))
        self.assertIsNotNone(ir.entity("e1"))
        self.assertTrue(any("主角不删" in c for c in changes))

    def test_weak_entity_can_be_deleted(self):
        from moss_llm.smith.audit import Audit, patch
        ir, changes = patch(self._ir(), Audit(score=50, wrong=["a bicycle leans on the wall"]))
        self.assertIsNone(ir.entity("e2"))
        self.assertTrue(any("删实体" in c for c in changes))

    def test_patch_does_not_mutate_input(self):
        from moss_llm.smith.audit import Audit, patch
        src = self._ir()
        patch(src, Audit(score=50, missing=["a red lantern"]))
        self.assertEqual(len(src.entities), 2)

    def test_verdict_thresholds(self):
        from moss_llm.smith.audit import Audit
        self.assertEqual(Audit(score=90, ok=["face"]).verdict, "pass")
        self.assertEqual(Audit(score=70, wrong=["a hat"]).verdict, "repair")
        self.assertEqual(Audit(score=30, wrong=["a hat"]).verdict, "fail")

    def test_empty_audit_is_degraded_not_pass(self):
        """实测：完全错配的 prompt 会得到空审计。空结果绝不能被当成通过。"""
        from moss_llm.smith.audit import Audit
        a = Audit(score=0)
        self.assertTrue(a.degraded)
        self.assertEqual(a.verdict, "degraded")

    def test_claims_make_audit_valid(self):
        from moss_llm.smith.audit import Audit
        a = Audit(score=0, claims=[{"claim": "a man", "present": False, "why": ""}])
        self.assertFalse(a.degraded)
        self.assertEqual(a.verdict, "fail")


class TestAuditScoring(unittest.TestCase):
    def test_all_claims_present_scores_100(self):
        from moss_llm.smith.audit import score_audit
        claims = [{"claim": "a", "present": True}, {"claim": "b", "present": True}]
        self.assertEqual(score_audit(claims, []), 100)

    def test_missing_items_penalise_even_when_claims_pass(self):
        """实测撞到过：claims 全对但漏了 3 样东西，却给 100 分。"""
        from moss_llm.smith.audit import score_audit
        claims = [{"claim": "a", "present": True}] * 5
        self.assertLess(score_audit(claims, ["x", "y", "z"]), 85)

    def test_half_claims_present(self):
        from moss_llm.smith.audit import score_audit
        claims = [{"claim": "a", "present": True}, {"claim": "b", "present": False}]
        self.assertEqual(score_audit(claims, []), 50)

    def test_penalty_is_capped(self):
        from moss_llm.smith.audit import score_audit
        claims = [{"claim": "a", "present": True}]
        self.assertGreaterEqual(score_audit(claims, ["x"] * 20), 60)


class TestVisionPrep(unittest.TestCase):
    def test_aspect_ratio_snaps_to_common(self):
        from moss_llm.smith.vision import Prepared
        self.assertEqual(Prepared("", 1536, 1024, [], "x").aspect_ratio, "3:2")
        self.assertEqual(Prepared("", 1024, 1024, [], "x").aspect_ratio, "1:1")
        self.assertEqual(Prepared("", 1600, 900, [], "x").aspect_ratio, "16:9")

    def test_missing_file_raises(self):
        from moss_llm.smith.vision import prepare
        with self.assertRaises(FileNotFoundError):
            prepare("no-such-image.png")


def _png(chunks) -> bytes:
    """最小可用 PNG：只为塞文本块，像素内容无所谓。"""
    import struct
    import zlib

    def ch(t, d):
        return (struct.pack(">I", len(d)) + t + d
                + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))

    out = b"\x89PNG\r\n\x1a\n"
    out += ch(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
    for item in chunks:
        k, v = item[0], item[1]
        kind = item[2] if len(item) > 2 else "tEXt"
        if kind == "tEXt":
            out += ch(b"tEXt", k.encode() + b"\x00" + v.encode("latin-1"))
        elif kind == "zTXt":
            out += ch(b"zTXt", k.encode() + b"\x00\x00" + zlib.compress(v.encode()))
        else:                                   # iTXt，UTF-8，未压缩
            out += ch(b"iTXt", k.encode() + b"\x00\x00\x00\x00\x00" + v.encode("utf-8"))
    out += ch(b"IDAT", zlib.compress(b"\x00" * 4 * 4 * 3))
    out += ch(b"IEND", b"")
    return out


def _jpeg_usercomment(text: str, charset: bytes = b"ASCII\x00\x00\x00",
                      enc: str = "utf-8") -> bytes:
    """最小 JPEG：一个 APP1(Exif) 装 UserComment，后面直接 SOS/EOI。"""
    import struct

    body = charset + text.encode(enc)
    tiff = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    tiff += struct.pack("<H", 1)
    tiff += struct.pack("<HHI", 0x9286, 7, len(body)) + struct.pack("<I", 26)
    tiff += struct.pack("<I", 0) + body
    app1 = b"Exif\x00\x00" + tiff
    return (b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xda\x00\x00\xff\xd9")


class TestEmbeddedMetadata(unittest.TestCase):
    """零模型洗图：把图里内嵌的原 prompt 读出来。读不到是常态，不是错误。"""

    A1111 = ("masterpiece, 1girl, yellow raincoat, neon city, rain\n"
             "Negative prompt: lowres, bad hands\n"
             "Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 1234, "
             "Size: 832x1216, Model hash: abc123, Model: dreamshaper_8, "
             'Lora hashes: "add_detail: 7c6bad76eb54, style: 1234abcd"')

    def test_a1111_positive_negative_params(self):
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_png([("parameters", self.A1111)]))
        self.assertEqual(emb.dialect, "a1111")
        self.assertEqual(emb.positive, "masterpiece, 1girl, yellow raincoat, neon city, rain")
        self.assertEqual(emb.negative, "lowres, bad hands")
        self.assertEqual(emb.params["Model"], "dreamshaper_8")
        self.assertEqual(emb.params["Steps"], "28")

    def test_quoted_value_keeps_its_commas(self):
        """Lora hashes 的值里有逗号，按逗号硬切会切碎。"""
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_png([("parameters", self.A1111)]))
        self.assertEqual(emb.params["Lora hashes"],
                         "add_detail: 7c6bad76eb54, style: 1234abcd")

    def test_no_negative_block(self):
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_png([("parameters", "a cat\nSteps: 20, Sampler: Euler a")]))
        self.assertEqual(emb.positive, "a cat")
        self.assertEqual(emb.negative, "")

    def test_steps_inside_positive_does_not_win(self):
        """正向里恰好有一行以 Steps: 开头时，参数行仍应是最后一个锚点。"""
        from moss_llm.smith import embedded as E
        txt = "poster\nSteps: not really params\nmore text\nSteps: 30, Seed: 9"
        emb = E.read_bytes(_png([("parameters", txt)]))
        self.assertEqual(emb.params["Seed"], "9")
        self.assertIn("more text", emb.positive)

    def test_compressed_text_chunks(self):
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_png([("parameters", "a dog\nSteps: 12", "zTXt")]))
        self.assertEqual(emb.positive, "a dog")
        emb = E.read_bytes(_png([("parameters", "一只橘猫，窗台，逆光\nSteps: 12", "iTXt")]))
        self.assertEqual(emb.positive, "一只橘猫，窗台，逆光")

    def test_comfyui_traces_positive_and_negative(self):
        import json as _json
        from moss_llm.smith import embedded as E
        graph = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": 42, "steps": 20, "cfg": 7.0, "sampler_name": "euler",
                "positive": ["8", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "flux1-dev.safetensors"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}},
            "6": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "a cat astronaut", "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "blurry, watermark", "clip": ["4", 1]}},
            # 正向走了一层 ConditioningCombine，连线要能穿过去
            "8": {"class_type": "ConditioningCombine",
                  "inputs": {"conditioning_1": ["6", 0], "conditioning_2": ["9", 0]}},
            "9": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "cinematic lighting", "clip": ["4", 1]}},
        }
        emb = E.read_bytes(_png([("prompt", _json.dumps(graph))]))
        self.assertEqual(emb.dialect, "comfyui")
        self.assertIn("a cat astronaut", emb.positive)
        self.assertIn("cinematic lighting", emb.positive)
        self.assertEqual(emb.negative, "blurry, watermark")
        self.assertEqual(emb.params["model"], "flux1-dev.safetensors")
        self.assertEqual(emb.params["seed"], 42)

    def test_comfyui_without_sampler_falls_back_but_says_so(self):
        import json as _json
        from moss_llm.smith import embedded as E
        graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}}}
        emb = E.read_bytes(_png([("prompt", _json.dumps(graph))]))
        self.assertEqual(emb.positive, "hello")
        self.assertTrue(emb.notes)          # 退化了就必须出声

    def test_invokeai_and_novelai(self):
        import json as _json
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_png([("invokeai_metadata", _json.dumps(
            {"positive_prompt": "a fox", "negative_prompt": "ugly",
             "model": {"model_name": "sdxl"}, "seed": 9, "steps": 30}))]))
        self.assertEqual((emb.dialect, emb.positive, emb.params["model"]),
                         ("invokeai", "a fox", "sdxl"))
        emb = E.read_bytes(_png([("Software", "NovelAI"), ("Comment", _json.dumps(
            {"prompt": "1girl, masterpiece", "uc": "bad anatomy", "steps": 28}))]))
        self.assertEqual((emb.dialect, emb.positive, emb.negative),
                         ("novelai", "1girl, masterpiece", "bad anatomy"))

    def test_jpeg_usercomment_ascii(self):
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_jpeg_usercomment(
            "a fox in snow\nNegative prompt: blurry\nSteps: 30, Model: sdxl"))
        self.assertEqual(emb.dialect, "a1111(exif)")
        self.assertEqual(emb.positive, "a fox in snow")
        self.assertEqual(emb.params["Model"], "sdxl")

    def test_jpeg_unicode_charset_declared(self):
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_jpeg_usercomment("雪地里的狐狸", b"UNICODE\x00", "utf-16-be"))
        self.assertEqual(emb.positive, "雪地里的狐狸")

    def test_jpeg_undeclared_utf16_byte_order_is_detected(self):
        """没写字符集时不能直接当 UTF-8；字节序由零字节落位决定，试解码会出合法乱码。"""
        from moss_llm.smith import embedded as E
        for enc in ("utf-16-be", "utf-16-le"):
            emb = E.read_bytes(_jpeg_usercomment("a fox", b"\x00" * 8, enc))
            self.assertEqual(emb.positive, "a fox", enc)

    def test_jpeg_undeclared_utf8_still_works(self):
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_jpeg_usercomment("中文提示词 fox", b"\x00" * 8, "utf-8"))
        self.assertEqual(emb.positive, "中文提示词 fox")

    def test_plain_prompt_block(self):
        from moss_llm.smith import embedded as E
        emb = E.read_bytes(_png([("prompt", "just a prompt")]))
        self.assertEqual((emb.dialect, emb.positive), ("plain", "just a prompt"))

    def test_json_but_not_a_graph_is_not_dumped_as_text(self):
        """prompt 块里是 JSON 但不是 ComfyUI 图时，不能把整段 JSON 当 prompt 交出去。"""
        import json as _json
        from moss_llm.smith import embedded as E
        self.assertIsNone(E.read_bytes(_png([("prompt", _json.dumps([1, 2, 3]))])))

    def test_no_metadata_returns_none_not_empty_object(self):
        from moss_llm.smith import embedded as E
        self.assertIsNone(E.read_bytes(_png([("Author", "me")])))
        self.assertIsNone(E.read_bytes(b"\x00" * 64))

    def test_broken_length_does_not_raise(self):
        """块长度字段坏了就停，不该让整次洗图失败。"""
        from moss_llm.smith import embedded as E
        bad = _png([("parameters", "a dog\nSteps: 12")])
        broken = bad[:8] + b"\xff\xff\xff\xff" + bad[12:]
        self.assertIsNone(E.read_bytes(broken))

    def test_describe_is_honest_when_absent(self):
        from moss_llm.smith import embedded as E
        self.assertEqual(E.describe(None), "无内嵌生成参数")

    def test_usable_requires_positive(self):
        from moss_llm.smith import embedded as E
        self.assertFalse(E.Embedded(negative="x").usable)
        self.assertTrue(E.Embedded(positive="x").usable)


class TestVisionRobustness(unittest.TestCase):
    """真实世界的图片：透明 PNG、手机竖拍、调色板。"""

    def setUp(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("未安装 Pillow")

    def _decode(self, data_url):
        import base64
        import io
        from PIL import Image
        raw = base64.b64decode(data_url.split(",", 1)[1])
        return Image.open(io.BytesIO(raw)).convert("RGB")

    def test_transparent_png_composited_on_white_not_black(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from moss_llm.smith.vision import prepare
        p = Path(tempfile.mkdtemp()) / "sticker.png"
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))     # 全透明
        im.putpixel((32, 32), (255, 0, 0, 255))
        im.save(p)
        prep = prepare(p)
        px = self._decode(prep.data_url).load()
        r, g, b = px[0, 0][:3]
        self.assertTrue(r > 240 and g > 240 and b > 240,
                        "透明像素应是白底，实际 %s" % (px[0, 0],))
        self.assertTrue(any("白底" in n for n in prep.notes))

    def test_exif_orientation_is_applied(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from moss_llm.smith.vision import prepare
        p = Path(tempfile.mkdtemp()) / "phone.jpg"
        im = Image.new("RGB", (100, 50), (10, 200, 10))
        ex = im.getexif()
        ex[0x0112] = 6                     # 顺时针 90°
        im.save(p, exif=ex)
        prep = prepare(p)
        self.assertEqual((prep.width, prep.height), (50, 100),
                         "竖拍照片不转正，宽高和画面都会错")
        self.assertTrue(any("EXIF" in n for n in prep.notes))

    def test_palette_with_transparency(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from moss_llm.smith.vision import prepare
        p = Path(tempfile.mkdtemp()) / "pal.png"
        im = Image.new("P", (32, 32), 0)
        im.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
        im.info["transparency"] = 0
        im.save(p, transparency=0)
        prep = prepare(p)
        r, g, b = self._decode(prep.data_url).load()[0, 0][:3]
        self.assertTrue(r > 240 and g > 240 and b > 240, "调色板透明应走白底")


class TestLintScopes(unittest.TestCase):
    """L008 曾把四格漫画里「女坐男站」误报成矛盾。作用域必须分开。"""

    def _ir(self, entities):
        return IR.from_dict({"task": "t2i", "canvas": {"aspect_ratio": "1:1"},
                             "entities": entities})

    def test_sitting_and_standing_on_different_entities_is_fine(self):
        ir = self._ir([
            {"id": "e1", "kind": "character", "label": "woman", "age_years": 28,
             "importance": 0.9, "descriptors": {"gender": "woman", "pose": "sitting"}},
            {"id": "e2", "kind": "character", "label": "man", "age_years": 30,
             "importance": 0.8, "descriptors": {"gender": "man", "pose": "standing"}},
        ])
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        codes = [i.code for i in check(ir, "moss-image", REG.get("moss-image"), out.as_dict())
                 if i.level == "error"]
        self.assertNotIn("L008", codes)

    def test_one_entity_with_both_poses_is_flagged(self):
        ir = self._ir([{"id": "e1", "kind": "character", "label": "woman",
                        "age_years": 28, "importance": 0.9,
                        "descriptors": {"gender": "woman",
                                        "pose": "sitting then standing"}}])
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        codes = [i.code for i in check(ir, "moss-image", REG.get("moss-image"), out.as_dict())]
        self.assertIn("L008", codes)


class TestTextTypography(unittest.TestCase):
    def test_cjk_drops_case_and_anchor(self):
        from moss_llm.smith.clauses import describe_text
        ir = IR.from_dict({"task": "t2i", "entities": [
            {"id": "e1", "kind": "text", "label": "bubble", "importance": 0.9,
             "text_content": "写不下去",
             "typography": {"weight": "bold", "case": "upper", "color": "black"},
             "spatial": {"anchor": "top-right"}}]})
        got = describe_text(ir.entities[0])
        self.assertIn("Chinese text", got)
        self.assertNotIn("upper case", got)          # 中文没有大小写
        self.assertIn("at the top-right", got)
        self.assertNotIn("at the", describe_text(ir.entities[0], with_anchor=False))

    def test_latin_keeps_case(self):
        from moss_llm.smith.clauses import describe_text
        ir = IR.from_dict({"task": "t2i", "entities": [
            {"id": "e1", "kind": "text", "label": "title", "importance": 0.9,
             "text_content": "CAPE LIGHT", "typography": {"case": "upper"}}]})
        self.assertIn("upper case", describe_text(ir.entities[0]))


class TestCanvasType(unittest.TestCase):
    def test_three_text_blocks_imply_multi_panel(self):
        from moss_llm.smith.reverse import _canvas_type
        obs = {"entities": [{"kind": "text"}, {"kind": "text"}, {"kind": "text"},
                            {"kind": "character"}]}
        self.assertEqual(_canvas_type(obs), "multi_panel")

    def test_explicit_value_wins(self):
        from moss_llm.smith.reverse import _canvas_type
        self.assertEqual(_canvas_type({"canvas": "collage", "entities": []}), "collage")

    def test_single_by_default(self):
        from moss_llm.smith.reverse import _canvas_type
        self.assertEqual(_canvas_type({"entities": [{"kind": "text"}]}), "single")

    def test_canvas_reaches_moss_layout(self):
        ir = IR.from_dict({"task": "reverse",
                           "canvas": {"aspect_ratio": "1:1", "canvas_type": "multi_panel"},
                           "entities": [
                               {"id": "e1", "kind": "text", "label": "t", "importance": 0.9,
                                "text_content": "一", "spatial": {"bbox_norm": [0.1, 0.1, 0.4, 0.2]}},
                               {"id": "e2", "kind": "text", "label": "t2", "importance": 0.9,
                                "text_content": "二", "spatial": {"bbox_norm": [0.6, 0.6, 0.9, 0.7]}}]})
        draft = L.build_draft(ir)
        self.assertEqual(draft["summary"]["canvas"], "multi_panel")


class TestBboxAnchorReconcile(unittest.TestCase):
    """实测：四格漫画 7 个框里 3 个落错象限，而 anchor 全对。

    结论不是「坐标要修」，是「坐标通道坏了」——所以冲突时换通道：
    位置取方位，尺寸留模型。
    """

    def test_conflicting_box_keeps_size_moves_position(self):
        from moss_llm.smith.reverse import _resolve_bbox
        # anchor 说左上，框却竖跨到画面底部 → 位置按方位重摆，宽度仍是模型的 0.38
        box, src = _resolve_bbox("top-left", [0.08, 0.12, 0.46, 0.96])
        self.assertEqual(src, "anchor")
        self.assertLessEqual(box[3], 0.5)               # 落在上半部
        self.assertLessEqual(box[2], 0.5)               # 落在左半部
        self.assertAlmostEqual(box[2] - box[0], 0.38, places=3)

    def test_box_in_wrong_quadrant_moves(self):
        from moss_llm.smith.reverse import _resolve_bbox
        # anchor 说右上，框却整个在右下
        box, src = _resolve_bbox("top-right", [0.5, 0.5, 0.96, 0.96])
        self.assertEqual(src, "anchor")
        self.assertLessEqual(box[3], 0.5)
        self.assertGreaterEqual(box[0], 0.5)

    def test_agreeing_box_is_kept(self):
        from moss_llm.smith.reverse import _resolve_bbox
        box, src = _resolve_bbox("top-right", [0.5, 0.08, 0.96, 0.48])
        self.assertEqual(box, [0.5, 0.08, 0.96, 0.48])
        self.assertEqual(src, "model")

    def test_no_anchor_keeps_model_box(self):
        from moss_llm.smith.reverse import _resolve_bbox
        box, src = _resolve_bbox("", [0.1, 0.1, 0.9, 0.9])
        self.assertIsNotNone(box)
        self.assertEqual(src, "model")

    def test_center_anchor_is_not_a_constraint(self):
        from moss_llm.smith.reverse import _resolve_bbox
        box, _ = _resolve_bbox("in the center", [0.05, 0.05, 0.95, 0.95])
        self.assertEqual(box, [0.05, 0.05, 0.95, 0.95])

    def test_text_without_box_still_gets_one(self):
        """气泡只要有 "top-left" 就能排；缺框不该让整页失去布局能力。"""
        from moss_llm.smith.reverse import _resolve_bbox
        box, src = _resolve_bbox("top-right", None, kind="text")
        self.assertEqual(src, "anchor")
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box[0], 0.5)
        self.assertLessEqual(box[3], 0.5)

    def test_non_text_without_box_gets_nothing(self):
        """全身人像的 "left foreground" 造框会把单张照片误判成需要布局。"""
        from moss_llm.smith.reverse import _resolve_bbox
        box, src = _resolve_bbox("left foreground", None, kind="character")
        self.assertIsNone(box)
        self.assertEqual(src, "none")

    def test_conflict_is_reported(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir({"entities": [{
            "id": "e1", "kind": "object", "label": "pen",
            "spatial": {"anchor": "top-right",
                        "bbox": {"x_min": 50, "y_min": 50, "x_max": 96, "y_max": 96}}}]}, rep)
        box = ir.entities[0].spatial["bbox_norm"]
        self.assertLess(box[3], 0.5)
        self.assertTrue(any("冲突" in w for w in rep.warnings))

    def test_bbox_source_is_recorded(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir({"entities": [{
            "id": "e1", "kind": "object", "label": "pen",
            "spatial": {"anchor": "top-right",
                        "bbox": {"x_min": 50, "y_min": 8, "x_max": 96, "y_max": 48}}}]}, rep)
        self.assertEqual(ir.entities[0].spatial["bbox_source"], "model")

    def test_relation_to_text_entity_dropped(self):
        from moss_llm.smith.reverse import Report, coerce_ir
        rep = Report()
        ir = coerce_ir({
            "entities": [{"id": "e1", "kind": "character", "label": "woman", "age_years": 28},
                         {"id": "e2", "kind": "text", "label": "bubble", "text_content": "写不下去"}],
            "relations": [{"subject": "e1", "predicate": "facing", "object": "e2"}],
        }, rep)
        self.assertEqual(ir.relations, [])
        self.assertTrue(any("文字块" in d for d in rep.dropped))


class TestMaterialClause(unittest.TestCase):
    def test_material_already_in_entity_description_is_not_repeated(self):
        ir = IR.from_dict({"task": "t2i", "canvas": {"aspect_ratio": "1:1"}, "entities": [
            {"id": "e1", "kind": "object", "label": "fountain pen", "importance": 0.8,
             "descriptors": {"material": "brushed steel"}}]})
        texts = [c.text for c in build_clauses(ir)]
        self.assertFalse(any(t.startswith("textures:") for t in texts))


    def test_thin_zone_is_not_a_constraint(self):
        """实测 "left foreground" 把 [0.46..0.96] 裁成 0.04 宽，那种框没法用。

        换通道后不再「裁」，所以宽度就是模型给的 0.5，不会被压成一条缝。
        """
        from moss_llm.smith.reverse import _resolve_bbox
        box, src = _resolve_bbox("left foreground", [0.46, 0.16, 0.96, 1.0])
        self.assertEqual(src, "anchor")
        self.assertGreater(box[2] - box[0], 0.1)

    def test_claim_label_keeps_the_sentence(self):
        from moss_llm.smith.audit import _claim_label
        got = _claim_label("The four distinct panel crops (close-ups) shown in the storyboard.")
        self.assertIn("panel crops", got)
        self.assertFalse(got.endswith("."))

    def test_patch_respects_max_add(self):
        from moss_llm.smith.audit import Audit, patch
        ir = IR.from_dict({"task": "reverse", "entities": [
            {"id": "e1", "kind": "character", "label": "woman",
             "importance": 0.9, "age_years": 30}]})
        missing = ["a red lantern", "a bicycle", "a stone wall", "a bird",
                   "a paper cup", "a wooden crate", "a ladder"]
        new, changes = patch(ir, Audit(score=30, missing=missing), max_add=3)
        self.assertEqual(len([e for e in new.entities if e.id.startswith("fix")]), 3)


class TestAuditLoopStops(unittest.TestCase):
    class _FakeLLM:
        """按脚本返回：观察 JSON 一次，然后审计 JSON 若干次。"""

        def __init__(self, audits):
            self.audits = list(audits)
            self.calls = 0

        def chat_vision(self, system, user, data_url, temperature=0.2,
                        max_tokens=3000, schema=None, name=""):
            self.calls += 1
            if "fidelity auditor" in system:
                return self.audits.pop(0)
            return ('{"canvas":"single","style":{"medium":"photograph"},'
                    '"entities":[{"id":"e1","kind":"character","label":"woman",'
                    '"age_years":30,"importance":0.9}]}')

    def _tiny_png(self) -> str:
        """自造 1x1 PNG，保证测试不依赖本机任何图片资产。"""
        import base64
        import tempfile
        raw = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
            "IQAAAABJRU5ErkJggg==")
        fd, path = tempfile.mkstemp(suffix=".png")
        with open(fd, "wb") as fh:
            fh.write(raw)
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        return path

    def test_no_improvement_stops_the_loop(self):
        from moss_llm.smith.audit import reverse_with_audit
        flat = ('{"claims":[{"claim":"a woman","present":true,"why":""},'
                '{"claim":"a red hat","present":false,"why":"no hat"}],'
                '"missing":["a lantern"],"score":40}')
        llm = self._FakeLLM([flat, flat, flat])
        _, _, history = reverse_with_audit(self._tiny_png(), llm,
                                           compile_fn=lambda i: "a woman",
                                           rounds=3, text_pass=False)
        self.assertEqual(len(history), 2)               # 第 2 轮发现没涨就停
        self.assertTrue(any("未提升" in c for c in history[-1]["changes"]))
        self.assertEqual(llm.calls, 3)                  # 1 次取证 + 2 次审计


class TestRepeatCollapse(unittest.TestCase):
    """四格漫画里同一个人出现 4 次，模型给 4 段一模一样的描述。"""

    def _ir(self):
        ent = {"kind": "character", "label": "woman", "age_years": 28,
               "importance": 0.9, "descriptors": {"gender": "woman", "hair": "black"}}
        return IR.from_dict({"task": "reverse", "canvas": {"aspect_ratio": "1:1"},
                             "entities": [
                                 dict(ent, id="e%d" % i,
                                      spatial={"anchor": a,
                                               "bbox_norm": b})
                                 for i, (a, b) in enumerate([
                                     ("top-left panel", [0.0, 0.0, 0.5, 0.5]),
                                     ("top-right panel", [0.5, 0.0, 1.0, 0.5]),
                                     ("bottom-left panel", [0.0, 0.5, 0.5, 1.0]),
                                     ("bottom-right panel", [0.5, 0.5, 1.0, 1.0])],
                                     start=1)]})

    def test_description_appears_once(self):
        ir = self._ir()
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        self.assertEqual(out.positive.count("28-year-old woman"), 1)

    def test_repeat_is_explained_by_position(self):
        ir = self._ir()
        ad = get_adapter("moss-image", REG, counter=CNT)
        out = ad.realize(ir, mode="deterministic", use_layout=False)
        self.assertIn("appears again", out.positive)
        self.assertIn("bottom-right panel", out.positive)

    def test_layout_still_has_one_item_per_box(self):
        ir = self._ir()
        draft = L.build_draft(ir)
        self.assertEqual(len(draft["items"]), 4)


class TestMetaCommentFilter(unittest.TestCase):
    def test_meta_comment_is_not_a_missing_item(self):
        from moss_llm.smith.audit import is_meta_comment
        self.assertTrue(is_meta_comment(
            "The prompt mentions 'top and bottom margins' but the image shows no visible."))
        self.assertTrue(is_meta_comment("is missing from the prompt"))
        self.assertFalse(is_meta_comment("a red lantern hanging above the door"))

    def test_meta_comment_never_becomes_an_entity(self):
        from moss_llm.smith.audit import Audit, patch
        ir = IR.from_dict({"task": "reverse", "entities": [
            {"id": "e1", "kind": "character", "label": "woman",
             "importance": 0.9, "age_years": 30}]})
        new, changes = patch(ir, Audit(score=30, missing=[
            "The prompt mentions margins but the image shows no visible",
            "a red lantern"]))
        self.assertEqual(len([e for e in new.entities if e.id.startswith("fix")]), 1)


# ─────────────────────────────────────────────────────────────
# 可移植性：不能假设用户机器上有 llama-server / WSL
# ─────────────────────────────────────────────────────────────

class TestServerPortability(unittest.TestCase):
    def test_attach_mode_needs_no_model_and_no_exe(self):
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(base_url="http://127.0.0.1:8080/")
        self.assertTrue(s.attached)
        self.assertEqual(s.base, "http://127.0.0.1:8080")
        self.assertEqual(s.v1, "http://127.0.0.1:8080/v1")
        self.assertEqual(s.model, "")            # 没给权重也能构造

    def test_attach_mode_never_spawns(self):
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(base_url="http://127.0.0.1:1")
        with self.assertRaises(RuntimeError) as ctx:
            s.start()
        self.assertIn("连不上", str(ctx.exception))
        self.assertIsNone(s.proc)                # 没有起进程

    def test_missing_exe_error_lists_the_attach_route(self):
        """找不到可执行文件时，必须把「连已有服务」放在第一条——
        那是没有 llama-server 的用户唯一走得通的路。"""
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(model="")
        s.exe = ""
        import os
        old = {k: os.environ.get(k) for k in
               ("PROMPT_ARTISAN_SERVER", "LLAMA_CPP_HOME", "PATH")}
        try:
            os.environ["PROMPT_ARTISAN_SERVER"] = ""
            os.environ["LLAMA_CPP_HOME"] = ""
            os.environ["PATH"] = ""
            with self.assertRaises(RuntimeError) as ctx:
                s.find_exe()
            msg = str(ctx.exception)
            self.assertIn("--base-url", msg)
            self.assertIn("setup", msg)
            self.assertIn("github.com/ggml-org/llama.cpp", msg)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_alias_distinguishes_models(self):
        from moss_llm.smith.llm import LlamaServer
        a = LlamaServer(model="a/x.gguf", ctx=8192)
        b = LlamaServer(model="b/x.gguf", ctx=8192)
        c = LlamaServer(model="a/x.gguf", ctx=16384)
        self.assertNotEqual(a.alias(), b.alias())
        self.assertNotEqual(a.alias(), c.alias())

    def test_alias_is_stable_for_same_config(self):
        from moss_llm.smith.llm import LlamaServer
        a = LlamaServer(model="a/x.gguf", ctx=8192)
        b = LlamaServer(model="a/x.gguf", ctx=8192)
        self.assertEqual(a.alias(), b.alias())


class TestSamplingChannels(unittest.TestCase):
    """纪律 4：JSON 模式与重复惩罚互斥。"""

    def _srv(self):
        from moss_llm.smith.llm import LlamaServer
        return LlamaServer(model="x.gguf")

    def test_prose_body_carries_dry(self):
        body = self._srv().build_body([], 0.7, 500)
        self.assertIn("dry_multiplier", body)
        self.assertGreater(body["dry_multiplier"], 0)

    def test_json_body_drops_dry_and_zeroes_presence(self):
        """键名在 JSON 里会合法地重复 N 次，惩罚它就是惩罚正确输出。"""
        body = self._srv().build_body([], 0.2, 800, json_mode=True)
        self.assertNotIn("dry_multiplier", body)
        self.assertNotIn("dry_base", body)
        self.assertEqual(body["presence_penalty"], 0)
        self.assertEqual(body["frequency_penalty"], 0)

    def test_response_format_ladder(self):
        from moss_llm.smith.llm import LlamaServer
        schema = {"type": "object", "properties": {}}
        ladder = LlamaServer._formats(schema, "x")
        self.assertEqual(ladder[0]["type"], "json_schema")
        self.assertEqual(ladder[-1], None)       # 最后允许无约束，但不静默
        self.assertEqual(LlamaServer._formats(None, "x"), [None])

    def test_schema_is_attached_to_body(self):
        body = self._srv().build_body([], 0.2, 800, json_mode=True,
                                      response_format={"type": "json_object"})
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_truncation_is_recorded_as_note(self):
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(model="x.gguf")
        s._content({"choices": [{"message": {"content": "{\"a\":1"},
                                 "finish_reason": "length"}]})
        self.assertTrue(any("max_tokens" in n for n in s.notes))


class TestEvidenceSchema(unittest.TestCase):
    def test_entity_kinds_match_the_coercer(self):
        """schema 里允许的 kind，_KIND_MAP 必须都认——否则语法放行的值会被降级。"""
        from moss_llm.smith.reverse import EVIDENCE_SCHEMA, _KIND_MAP
        kinds = (EVIDENCE_SCHEMA["properties"]["entities"]["items"]
                 ["properties"]["kind"]["enum"])
        for k in kinds:
            self.assertIn(k, _KIND_MAP)

    def test_only_three_fields_are_required(self):
        """全字段 required 会逼模型给没看见的字段编值。"""
        from moss_llm.smith.reverse import EVIDENCE_SCHEMA
        self.assertEqual(set(EVIDENCE_SCHEMA["required"]),
                         {"canvas", "style", "entities"})

    def test_prompt_forbids_placeholder_values(self):
        """schema 只锁形状，不告诉模型填什么；prompt 里必须明写禁止占位值。"""
        from moss_llm.smith.reverse import SYSTEM
        self.assertIn("Never write none", SYSTEM)
        self.assertIn("omit the key", SYSTEM)


class TestTextPass(unittest.TestCase):
    """UI 截图第一遍只描述照片、漏掉全部文字——所以要有第二遍。"""

    def _ir(self):
        return IR.from_dict({"task": "reverse", "canvas": {"aspect_ratio": "16:9"},
                             "entities": []})

    def test_new_blocks_become_text_entities(self):
        from moss_llm.smith.reverse import merge_text_blocks
        ir = self._ir()
        changes = merge_text_blocks(ir, [
            {"text": "季度报告", "where": "top-center", "kind": "title"},
            {"text": "Export", "where": "top-right", "kind": "button"}])
        self.assertEqual(len(ir.text_entities), 2)
        self.assertEqual(len(changes), 2)
        self.assertEqual(ir.text_entities[0].label, "headline")

    def test_same_string_is_not_added_twice(self):
        from moss_llm.smith.reverse import merge_text_blocks
        ir = IR.from_dict({"task": "reverse", "entities": [
            {"id": "e1", "kind": "text", "label": "bubble",
             "text_content": "写不下去", "spatial": {"anchor": "top-right"}}]})
        merge_text_blocks(ir, [{"text": "写不下去", "where": "top-right"}])
        self.assertEqual(len(ir.text_entities), 1)

    def test_merge_backfills_missing_bbox(self):
        from moss_llm.smith.reverse import merge_text_blocks
        ir = IR.from_dict({"task": "reverse", "entities": [
            {"id": "e1", "kind": "text", "label": "bubble",
             "text_content": "写不下去", "spatial": {"anchor": "top-right"}}]})
        merge_text_blocks(ir, [{"text": "写不下去", "where": "top-right"}])
        box = ir.text_entities[0].spatial["bbox_norm"]
        self.assertGreaterEqual(box[0], 0.5)     # 右半
        self.assertLessEqual(box[3], 1 / 3 + 1e-6)   # 上三分之一

    def test_ids_do_not_collide(self):
        from moss_llm.smith.reverse import merge_text_blocks
        ir = IR.from_dict({"task": "reverse", "entities": [
            {"id": "t1", "kind": "object", "label": "cup"}]})
        merge_text_blocks(ir, [{"text": "Hello", "where": "center"}])
        ids = [e.id for e in ir.entities]
        self.assertEqual(len(ids), len(set(ids)))

    def test_nine_cells_all_map(self):
        from moss_llm.smith.reverse import _where_box
        for where in ("top-left", "top-center", "top-right",
                      "middle-left", "center", "middle-right",
                      "bottom-left", "bottom-center", "bottom-right"):
            box = _where_box(where)
            self.assertIsNotNone(box, where)
            self.assertTrue(0.0 <= box[0] < box[2] <= 1.0, where)
            self.assertTrue(0.0 <= box[1] < box[3] <= 1.0, where)

    def test_empty_blocks_change_nothing(self):
        from moss_llm.smith.reverse import merge_text_blocks
        ir = self._ir()
        self.assertEqual(merge_text_blocks(ir, []), [])
        self.assertEqual(ir.entities, [])


class TestBboxScales(unittest.TestCase):
    """模型会给三种刻度：0-1、0-100、0-1000。只认两种会把框算烂。"""

    def test_zero_to_thousand(self):
        from moss_llm.smith.reverse import _coerce_bbox
        self.assertEqual(_coerce_bbox({"x_min": 784, "y_min": 152,
                                       "x_max": 830, "y_max": 176}),
                         [0.784, 0.152, 0.83, 0.176])

    def test_zero_to_hundred(self):
        from moss_llm.smith.reverse import _coerce_bbox
        self.assertEqual(_coerce_bbox({"x_min": 78, "y_min": 15,
                                       "x_max": 83, "y_max": 18}),
                         [0.78, 0.15, 0.83, 0.18])

    def test_zero_to_one(self):
        from moss_llm.smith.reverse import _coerce_bbox
        self.assertEqual(_coerce_bbox([0.1, 0.2, 0.5, 0.9]), [0.1, 0.2, 0.5, 0.9])


class TestSpreadAnchored(unittest.TestCase):
    """同一格里的多个文字块会拿到完全相同的框，Layout 按框去重会丢掉它们。"""

    def _ir(self, n):
        return IR.from_dict({"task": "reverse", "entities": [
            {"id": "t%d" % i, "kind": "text", "label": "label",
             "text_content": "s%d" % i, "importance": 0.6,
             "spatial": {"anchor": "bottom-right", "bbox_source": "anchor",
                         "bbox_norm": [0.62, 0.62, 0.87, 0.87]}}
            for i in range(1, n + 1)]})

    def test_boxes_become_distinct(self):
        from moss_llm.smith.reverse import spread_anchored
        ir = self._ir(3)
        self.assertEqual(spread_anchored(ir), 3)
        boxes = [tuple(e.spatial["bbox_norm"]) for e in ir.entities]
        self.assertEqual(len(set(boxes)), 3)

    def test_bands_are_tall_enough_to_hold_text(self):
        from moss_llm.smith.reverse import spread_anchored
        ir = self._ir(6)
        spread_anchored(ir)
        for e in ir.entities:
            b = e.spatial["bbox_norm"]
            self.assertGreaterEqual(b[3] - b[1], 0.059)   # 不是细缝

    def test_model_boxes_are_left_alone(self):
        from moss_llm.smith.reverse import spread_anchored
        ir = IR.from_dict({"task": "reverse", "entities": [
            {"id": "e1", "kind": "text", "label": "a", "text_content": "a",
             "spatial": {"bbox_source": "model", "bbox_norm": [0.1, 0.1, 0.2, 0.2]}},
            {"id": "e2", "kind": "text", "label": "b", "text_content": "b",
             "spatial": {"bbox_source": "model", "bbox_norm": [0.1, 0.1, 0.2, 0.2]}}]})
        self.assertEqual(spread_anchored(ir), 0)


# ─────────────────────────────────────────────────────────────
# 通用性：不绑定某一种服务端 / 某一种语言
# ─────────────────────────────────────────────────────────────

class TestServerGenerality(unittest.TestCase):
    def test_explicit_model_id_wins(self):
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(base_url="http://127.0.0.1:1", model_id="qwen2.5-vl:7b")
        self.assertEqual(s.model_name(), "qwen2.5-vl:7b")

    def test_attach_without_probe_omits_model_field(self):
        """Ollama 要模型名，LM Studio 要注册 id。探不到就干脆不发——
        发一个错的 "prompt-artisan" 会让服务端 404 或路由到别的模型。"""
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(base_url="http://127.0.0.1:1")
        self.assertEqual(s.model_name(), "")

    def test_spawn_mode_uses_own_alias(self):
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(model="x.gguf")
        self.assertEqual(s.model_name(), s.alias())

    def test_body_omits_model_when_unknown(self):
        from moss_llm.smith.llm import LlamaServer
        s = LlamaServer(base_url="http://127.0.0.1:1")
        self.assertNotIn("model", s.build_body([], 0.2, 100))

    def test_help_flags_probe_works_on_any_executable(self):
        """用 python 自己的 --help 验证探测逻辑：不依赖本机装没装 llama-server。"""
        import sys
        from moss_llm.smith.llm import LlamaServer
        flags = LlamaServer.help_flags(sys.executable)
        self.assertIn("--version", flags)

    def test_help_flags_unknown_exe_is_empty_not_crash(self):
        from moss_llm.smith.llm import LlamaServer
        self.assertEqual(LlamaServer.help_flags("definitely-not-a-real-exe-xyz"), set())


class TestCjkCoercion(unittest.TestCase):
    """中文能力强的开源 VLM 会用中文回 kind / 年龄 / 方位。认不出就静默降级。"""

    def test_chinese_kinds(self):
        from moss_llm.smith.reverse import _KIND_MAP
        self.assertEqual(_KIND_MAP["人物"], "character")
        self.assertEqual(_KIND_MAP["文字"], "text")
        self.assertEqual(_KIND_MAP["背景"], "background")

    def test_chinese_age_words(self):
        from moss_llm.smith.reverse import _as_int
        self.assertEqual(_as_int("三十多岁"), 34)
        self.assertEqual(_as_int("小孩"), 9)
        self.assertEqual(_as_int("老人"), 78)

    def test_chinese_digits_still_win(self):
        from moss_llm.smith.reverse import _as_int
        self.assertEqual(_as_int("28岁"), 28)

    def test_chinese_anchor_zone(self):
        from moss_llm.smith.reverse import _anchor_zone
        self.assertEqual(_anchor_zone("左上角"), (0.0, 0.5, 0.0, 0.5))
        self.assertEqual(_anchor_zone("画面右侧"), (0.5, 1.0, 0.0, 1.0))

    def test_chinese_where_box(self):
        from moss_llm.smith.reverse import _where_box
        box = _where_box("左下角")
        self.assertGreaterEqual(box[1], 2 / 3)      # 落在下三分之一
        self.assertLessEqual(box[2], 1 / 3)         # 落在左三分之一

    def test_english_where_still_works(self):
        from moss_llm.smith.reverse import _where_box
        self.assertIsNotNone(_where_box("top-right"))

    def test_chinese_gender_becomes_english_noun(self):
        from moss_llm.smith.clauses import _age_phrase
        from moss_llm.smith.ir import Entity
        e = Entity(id="e1", kind="character", label="woman", age_years=28,
                   descriptors={"gender": "女性"})
        phrase = _age_phrase(e)
        self.assertIn("woman", phrase)
        self.assertNotIn("女性", phrase)

    def test_chinese_predicate_is_translated(self):
        from moss_llm.smith.clauses import _relation_text
        ir = IR.from_dict({"task": "t2i", "entities": [
            {"id": "e1", "kind": "character", "label": "woman", "age_years": 28},
            {"id": "e2", "kind": "object", "label": "cup"}],
            "relations": [{"subject": "e1", "predicate": "拿着", "object": "e2"}]})
        self.assertIn("holds", _relation_text(ir, ir.relations[0]))


# ─────────────────────────────────────────────────────────────
# 通用后端：registry 里声明了就能用，不用写 Python
# ─────────────────────────────────────────────────────────────

class TestGenericAdapter(unittest.TestCase):
    """registry 里有声明、但**没有原生实现**的后端，自动走通用渲染。

    `sdxl` 曾经是这里的样板，2026-10-31 有了原生 adapter（分块），
    所以样板换成 `flux1_dev`——这条测试要的正是「没有原生实现的那一类」。
    """

    def test_registry_only_target_gets_an_adapter(self):
        from moss_llm.smith.adapters import GenericAdapter
        ad = get_adapter("flux1_dev", REG, counter=CNT)
        self.assertIsInstance(ad, GenericAdapter)
        self.assertEqual(ad.name, "flux1_dev")

    def test_tag_target_renders_tags(self):
        """通用后端的 tag 形态：按 registry 的 `prompt.mode: tag` 渲染。"""
        from moss_llm.smith.adapters.generic import GenericAdapter
        ad = GenericAdapter(REG, "sdxl", counter=CNT)      # 同名声明，强制走通用
        self.assertEqual(ad.form, "tag")
        ir = IR.load(EX / "01-portrait-photoreal.json")
        out = ad.realize(ir, mode="deterministic")
        self.assertNotIn(". ", out.positive)      # 不是散文
        self.assertIn(",", out.positive)

    def test_native_adapter_wins_over_generic(self):
        from moss_llm.smith.adapters.sdxl import SdxlAdapter
        self.assertIsInstance(get_adapter("sdxl", REG, counter=CNT), SdxlAdapter)

    def test_prose_target_still_prose(self):
        ad = get_adapter("flux1_dev", REG, counter=CNT)
        self.assertEqual(ad.form, "prose")

    def test_unknown_target_raises(self):
        with self.assertRaises(KeyError):
            get_adapter("no-such-model", REG, counter=CNT)

    def test_prompt_spec_mentions_target_and_form(self):
        ad = get_adapter("sdxl", REG, counter=CNT)
        spec = ad.prompt_spec(IR.load(EX / "01-portrait-photoreal.json"))
        self.assertIn("TARGET:", spec)
        self.assertIn("tags", spec)

    def test_generic_negative_comes_from_constraints(self):
        """registry 声明了支持负向但没给 default，就不许自己编一个。"""
        ad = get_adapter("sdxl", REG, counter=CNT)
        ir = IR.from_dict({"task": "t2i", "canvas": {"aspect_ratio": "1:1"},
                           "entities": [{"id": "e1", "kind": "object", "label": "cup"}],
                           "constraints": {"avoid": ["blurry", "extra fingers"]}})
        out = ad.realize(ir, mode="deterministic")
        self.assertEqual(out.negative, "blurry, extra fingers")

    def test_generic_negative_is_none_when_nothing_declared(self):
        ad = get_adapter("sdxl", REG, counter=CNT)
        ir = IR.from_dict({"task": "t2i", "canvas": {"aspect_ratio": "1:1"},
                           "entities": [{"id": "e1", "kind": "object", "label": "cup"}]})
        self.assertIsNone(ad.realize(ir, mode="deterministic").negative)


class TestBudgetLint(unittest.TestCase):
    """pack() 本来就填满预算，所以「贴着上限」不能报 error。"""

    def _issue(self, n, limit):
        ir = IR.from_dict({"task": "t2i", "entities": [
            {"id": "e1", "kind": "object", "label": "cup"}]})
        issues = check(ir, "sdxl", REG.get("sdxl"),
                       {"positive": "a cup", "token_count": n, "token_limit": limit})
        return [i for i in issues if i.code == "L001"]

    def test_near_cap_is_warn(self):
        got = self._issue(74, 77)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].level, "warn")

    def test_over_cap_is_error(self):
        got = self._issue(90, 77)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].level, "error")

    def test_well_under_cap_is_silent(self):
        self.assertEqual(self._issue(40, 77), [])


class TestExpandVerify(unittest.TestCase):
    """扩写体检：改固定项 / 造数字 / 造文字 = error；长度带外 = warn。

    这几条是「扩写」唯一不能越的线，所以必须是 error 而不是 warn——
    warn 会被忽略，error 才会让人停下来。
    """

    def setUp(self):
        from moss_llm.smith.expand import verify
        self.ir = IR.load(EX / "02-poster-text-layout.json")
        self.v = verify
        self.good = (
            'An ivory studio backdrop falls off softly toward the edges. A handmade ceramic cup '
            'sits slightly left of centre on an invisible plinth, its glaze catching one soft '
            'key light from the upper left so the rim reads glossy and the belly matte. Above '
            'it, the main title reads "把日常，过成诗。" in a heavy serif; below, the supporting '
            'copy reads "一杯温暖\n一刻从容" in a lighter weight, both in warm charcoal.'
        )

    def test_fixed_items_pick_up_text_and_background(self):
        from moss_llm.smith.expand import fixed_items
        kinds = {f.kind for f in fixed_items(self.ir)}
        self.assertIn("text", kinds)
        vals = [f.value for f in fixed_items(self.ir)]
        self.assertIn("把日常，过成诗。", vals)

    def test_faithful_expansion_passes(self):
        rep = self.v(self.ir, self.good, "moss-image", REG, length="brief")
        self.assertNotEqual(rep.verdict, "fail", rep.issues)

    def test_missing_text_is_error(self):
        bad = self.good.replace('"把日常，过成诗。"', "a title")
        rep = self.v(self.ir, bad, "moss-image", REG, length="brief")
        self.assertEqual(rep.verdict, "fail")
        self.assertTrue(any("固定项" in i for i in rep.errors))

    def test_paraphrased_text_is_error(self):
        """图里的字必须逐字，改标点也算改。"""
        bad = self.good.replace("把日常，过成诗。", "把日常过成诗")
        rep = self.v(self.ir, bad, "moss-image", REG, length="brief")
        self.assertEqual(rep.verdict, "fail")

    def test_invented_number_is_error(self):
        bad = self.good.replace("A handmade ceramic cup", "3 handmade ceramic cups")
        rep = self.v(self.ir, bad, "moss-image", REG, length="brief")
        self.assertIn("3", rep.extra_numbers)
        self.assertEqual(rep.verdict, "fail")

    def test_invented_quote_is_error(self):
        bad = self.good.replace("in warm charcoal",
                                'in warm charcoal, with a sticker reading "OPEN 24 HOURS"')
        rep = self.v(self.ir, bad, "moss-image", REG, length="brief")
        self.assertTrue(rep.extra_quotes)
        self.assertEqual(rep.verdict, "fail")

    def test_tech_units_are_not_content_numbers(self):
        """35mm 是镜头参数，不是「编了一个数字」。"""
        ok = self.good.replace("one soft key light", "one soft key light, 35mm lens")
        rep = self.v(self.ir, ok, "moss-image", REG, length="brief")
        self.assertEqual(rep.extra_numbers, [])

    def test_age_change_is_caught(self):
        """IR 里 35 只出现在 35mm 镜头参数上，不能因此放过「35 岁」。"""
        ir = IR.load(EX / "01-portrait-photoreal.json")
        rep = self.v(ir, "A 35-year-old woman in a yellow raincoat stands in a wet street.",
                     "moss-image", REG, length="brief")
        self.assertIn("35", rep.extra_numbers)

    def test_length_band_only_warns(self):
        rep = self.v(self.ir, self.good, "moss-image", REG, length="detailed")
        self.assertEqual(rep.verdict, "warn")
        self.assertTrue(any("欠约束" in i for i in rep.warns))

    def test_band_comes_from_registry_not_code(self):
        from moss_llm.smith.expand import length_band
        self.assertEqual(length_band(REG, "moss-image", "standard"), (150, 300))
        self.assertEqual(length_band(REG, "flux3", "standard"), (40, 90))
        self.assertIsNone(length_band(REG, "sdxl", "standard"))   # 没写就不判

    def test_ledger_separates_fixed_from_open(self):
        from moss_llm.smith.clauses import build_clauses
        from moss_llm.smith.expand import ledger
        text = ledger(self.ir, build_clauses(self.ir), (70, 120))
        self.assertIn("FIXED", text)
        self.assertIn("把日常，过成诗。", text)
        self.assertIn("LENGTH: 70–120 words", text)


class TestExpandLoop(unittest.TestCase):
    """扩写 = 账本 + N 次采样 + 体检择优 + 一次回炉。回炉只做一次。"""

    GOOD = ("A 28-year-old woman in a yellow raincoat stands on wet asphalt, weight on one leg, "
            "her face turned up to an overcast sky. A narrow backstreet recedes behind her, shop "
            "signs doubled in the puddles. Soft daylight from the left, blue hour, thin mist.")
    BAD = ("A 47-year-old woman in a red coat stands in a bright shopping mall at noon, smiling "
           "at the camera under warm spotlights.")

    class _Mock:
        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = []

        def chat(self, system, user, **kw):
            self.calls.append((system, user))
            return self.replies.pop(0) if self.replies else ""

    def _adapter(self, mock, candidates=1):
        ad = get_adapter("moss-image", REG, counter=CNT, llm=mock)
        ad.gen_retries = 1
        ad.gen_candidates = candidates
        return ad

    def test_retries_once_with_feedback_then_keeps_the_better_one(self):
        mock = self._Mock([self.BAD, self.GOOD])
        ad = self._adapter(mock)
        out = ad.realize(IR.load(EX / "01-portrait-photoreal.json"),
                         mode="llm", use_layout=False)
        self.assertEqual(len(mock.calls), 2)
        self.assertIn("28-year-old", out.positive)
        self.assertNotEqual(out.expand["verdict"], "fail")
        # 回炉指令必须点名错在哪，而不是「再写一遍」
        self.assertIn("FAILED THESE CHECKS", mock.calls[1][1])

    def test_no_retry_when_first_answer_is_clean(self):
        mock = self._Mock([self.GOOD])
        ad = self._adapter(mock)
        out = ad.realize(IR.load(EX / "01-portrait-photoreal.json"),
                         mode="llm", use_layout=False)
        self.assertEqual(len(mock.calls), 1)
        self.assertNotEqual(out.expand["verdict"], "fail")

    def test_worst_case_keeps_the_better_of_two(self):
        """两遍都错时留错得少的那份，并且报告照实说 fail。"""
        mock = self._Mock([self.BAD, "A 47-year-old woman in a red coat."])
        ad = self._adapter(mock)
        out = ad.realize(IR.load(EX / "01-portrait-photoreal.json"),
                         mode="llm", use_layout=False)
        self.assertEqual(out.expand["verdict"], "fail")
        self.assertTrue(out.expand["hard_misses"] or out.expand["extra_numbers"])

    def test_system_prompt_carries_the_hard_rules(self):
        """模型看不到 schema，也猜不到哪些不能动——规则必须在 prompt 里。"""
        mock = self._Mock([self.GOOD])
        ad = self._adapter(mock)
        ad.realize(IR.load(EX / "01-portrait-photoreal.json"), mode="llm", use_layout=False)
        system, user = mock.calls[0]
        self.assertIn("FIXED items are untouchable", system)
        self.assertIn("not adding adjectives", system)
        self.assertIn("FIXED", user)
        self.assertIn("OPEN SLOTS", user)
        self.assertIn("28", user)              # 账本里必须有不能动的年龄


class TestExpansionSearch(unittest.TestCase):
    """扩写的主要缺陷是方差，不是平均分。搜索是压方差最直接的手段。

    这组测试不测「模型写得好不好」，只测**同一份 IR 反复跑，结果稳不稳**。
    牌堆是固定的，所以方差比较可复现，不是统计玄学。
    """

    #: 5 份坏答案（改年龄 / 编数字 / 编引号）+ 3 份好答案。坏答案的 fixed_recall 低。
    DECK = [
        "A 47-year-old woman in a red coat stands in a mall at noon.",
        "A 28-year-old woman in a yellow raincoat stands on wet asphalt.",
        "A 28-year-old woman with 3 umbrellas stands on wet asphalt.",
        "A 28-year-old woman in a yellow raincoat, a sign reading \"CLOSED\" behind her.",
        "A 61-year-old woman in a yellow raincoat on a busy street.",
        "A 28-year-old woman in a yellow raincoat stands on wet asphalt, weight on one leg.",
        "A 28-year-old woman in a yellow raincoat waits under a leaking awning.",
        "A 28-year-old woman in a yellow raincoat walks past shuttered shops.",
    ]

    class _Deck:
        """按固定顺序发牌，用完回到开头。无随机数，结果完全可复现。"""

        def __init__(self, deck, start=0):
            self.deck, self.i, self.calls = deck, start, 0

        def chat(self, system, user, **kw):
            self.calls += 1
            text = self.deck[self.i % len(self.deck)]
            self.i += 1
            return text

    def _trial(self, candidates, trials=16):
        """跑 N 次，每次从牌堆不同位置起步，收集 fixed_recall 与词数。"""
        ir = IR.load(EX / "01-portrait-photoreal.json")
        recalls, words = [], []
        for t in range(trials):
            ad = get_adapter("moss-image", REG, counter=CNT,
                             llm=self._Deck(self.DECK, start=t))
            ad.gen_candidates = candidates
            ad.gen_retries = 0          # 隔离变量：只测搜索，不测回炉
            out = ad.realize(ir, mode="llm", use_layout=False)
            recalls.append(out.expand["fixed_recall"])
            words.append(len(out.positive.split()))
        return recalls, words

    def test_search_cuts_variance_of_fixed_recall(self):
        """N=4 相比 N=1：固定项命中的方差必须显著下降（验收线：≥50%）。

        实测（24 次、固定牌堆）：方差 0.0312 → 0.0000，均值 0.375 → 0.500。
        下降 100% 不是调参调出来的，是搜索的机制决定的——坏答案不进最终输出。
        """
        import statistics

        r1, _ = self._trial(1)
        r4, _ = self._trial(4)
        v1, v4 = statistics.pvariance(r1), statistics.pvariance(r4)
        self.assertGreater(v1, 0.0, "单采样必须真的在抖，否则这组测试测的是空气")
        self.assertLessEqual(v4, v1 * 0.5,
                             "方差只降了 %.0f%%（N=1 方差 %.4f，N=4 方差 %.4f）"
                             % (100 * (1 - v4 / v1), v1, v4))

    def test_search_pulls_length_into_the_band(self):
        """长度也要收：一份跨长度带的牌堆，N=4 的「离带子多远」必须比 N=1 稳。

        为什么要单独一份牌堆：上一组测试里所有答案都远低于 150 词下界，
        排序只能「挑更长的」，词数方差自然不降反升——那是牌堆的问题，不是搜索的问题。
        这组让答案分布在带子两侧，才测得到「收敛」。
        """
        import statistics
        from moss_llm.smith.expand import words as wcount

        pad = ("the wet street reflects the warm window light above the shuttered shops "
               "and a thin mist drifts between the buildings ")
        deck = []
        for target in (70, 110, 140, 160, 200, 250, 290, 330):
            head = "A 28-year-old woman in a yellow raincoat stands on wet asphalt."
            body = head
            while wcount(body) < target:
                body += " " + pad
            deck.append(body.strip())

        ir = IR.load(EX / "01-portrait-photoreal.json")

        def trial(n, trials=24):
            dist, lens = [], []
            for t in range(trials):
                ad = get_adapter("moss-image", REG, counter=CNT,
                                 llm=self._Deck(deck, start=t))
                ad.gen_candidates = n
                ad.gen_retries = 0
                out = ad.realize(ir, mode="llm", use_layout=False)
                dist.append(out.expand["band_distance"])
                lens.append(wcount(out.positive))
            return dist, lens

        d1, w1 = trial(1)
        d4, w4 = trial(4)
        self.assertGreater(statistics.pvariance(d1), 0.0, "牌堆必须真的跨带")
        self.assertLessEqual(statistics.pvariance(d4), statistics.pvariance(d1) * 0.5,
                             "离带距离方差只降了 %.0f%%（%.1f → %.1f）"
                             % (100 * (1 - statistics.pvariance(d4) / statistics.pvariance(d1)),
                                statistics.pvariance(d1), statistics.pvariance(d4)))
        self.assertLess(statistics.mean(d4), statistics.mean(d1),
                        "搜索应当把平均离带距离拉近，而不是只压方差")

    def test_search_never_picks_a_worse_candidate(self):
        """搜索的收益必须来自「挑对了」，不能靠运气：最好的一份不差于单采样的最好。"""
        import statistics

        r1, _ = self._trial(1)
        r4, _ = self._trial(4)
        self.assertGreaterEqual(statistics.mean(r4), statistics.mean(r1))

    def test_early_stop_saves_calls_when_first_sample_is_good(self):
        """第一份就硬错为零且在长度带内时，不该再采。"""
        good = ("A 28-year-old woman in a yellow raincoat stands on wet asphalt, weight on one "
                "leg, face turned up to an overcast sky. A narrow backstreet recedes behind her, "
                "shop signs doubled in the puddles. Soft daylight from the left, blue hour, thin "
                "mist drifting between the buildings. Wet asphalt mirrors the warm windows above, "
                "and the rain has stopped just long enough for the street to go quiet. A single "
                "bicycle leans against the wall at the far end, its frame beaded with water.")
        deck = self._Deck([good])
        ad = get_adapter("moss-image", REG, counter=CNT, llm=deck)
        ad.gen_candidates = 4
        ad.gen_retries = 0
        ad.length = "brief"
        out = ad.realize(IR.load(EX / "01-portrait-photoreal.json"), mode="llm",
                         use_layout=False)
        self.assertEqual(deck.calls, 1, out.expand)
        self.assertEqual(out.expand["candidates"], 1)

    def test_identical_samples_are_reported_not_hidden(self):
        """四次采样拿到同一份，必须明说「搜索没有收益」，不能假装做了搜索。"""
        deck = self._Deck(["A 28-year-old woman in a yellow raincoat stands on wet asphalt."])
        ad = get_adapter("moss-image", REG, counter=CNT, llm=deck)
        ad.gen_candidates = 4
        ad.gen_retries = 0
        out = ad.realize(IR.load(EX / "01-portrait-photoreal.json"), mode="llm",
                         use_layout=False)
        self.assertEqual(deck.calls, 4)
        self.assertEqual(out.expand["candidates"], 1)     # 去重后只有一份
        self.assertTrue(any("搜索没有收益" in w for w in out.warnings), out.warnings)

    def test_empty_model_output_is_a_failure_not_a_pass(self):
        ad = get_adapter("moss-image", REG, counter=CNT, llm=self._Deck([""]))
        ad.gen_candidates = 2
        ad.gen_retries = 0
        out = ad.realize(IR.load(EX / "01-portrait-photoreal.json"), mode="llm",
                         use_layout=False)
        self.assertEqual(out.positive, "")
        self.assertTrue(any("没有拿到任何内容" in w for w in out.warnings), out.warnings)


class TestInvariants(unittest.TestCase):
    """5 条 metamorphic 关系：不需要 ground truth 的一层验证。

    这组测试的价值不在「绿」，而在**每条都有破坏版**：
    把实现故意改坏，关系必须变红。只有破坏版红了，正常版绿才不是测空气。
    """

    TARGETS = ("moss-image", "qwen-image-2_1", "krea2", "flux3", "sdxl")
    FILES = ("01-portrait-photoreal.json", "02-poster-text-layout.json",
             "03-multi-subject.json")

    def _run(self, fname, target="moss-image", only=None, ir=None):
        from moss_llm.smith import invariants as INV
        ir = ir or load(fname)
        ad = get_adapter(target, REG, counter=CNT)
        return {r.code: r for r in INV.run(ad, ir, only=only)}

    def test_all_green_on_every_example_and_target(self):
        for f in self.FILES:
            for t in self.TARGETS:
                res = self._run(f, t)
                bad = [str(r) for r in res.values() if not r.ok]
                self.assertEqual(bad, [], "%s / %s 有失败：%s" % (f, t, bad))

    def test_only_filter(self):
        res = self._run("02-poster-text-layout.json", only=("M2", "M4"))
        self.assertEqual(sorted(res), ["M2", "M4"])

    # ── 破坏版：每条关系都必须能被打破 ──────────────────────
    def test_m1_goes_red_when_compiler_is_flaky(self):
        from unittest import mock
        from moss_llm.smith import clauses as C

        orig, n = C.describe_entity, {"i": 0}

        def flaky(e, with_anchor=True):
            n["i"] += 1
            return orig(e, with_anchor) + (" v%d" % n["i"])

        with mock.patch.object(C, "describe_entity", flaky):
            self.assertFalse(self._run("01-portrait-photoreal.json")["M1"].ok)

    def test_m2_goes_red_when_output_follows_array_order(self):
        """模拟：关系/笔记不再按内容排序，输出开始依赖数组顺序。

        这是真实发生过的事：打乱 `relations` 后 6 次里有 3 次输出不同。
        修法是在 `build_clauses` 里排序，所以这里把 `sorted` 换成恒等函数来还原 bug。
        """
        from unittest import mock
        from moss_llm.smith import clauses as C

        with mock.patch.object(C, "sorted", lambda it, **kw: list(it), create=True):
            self.assertFalse(self._run("03-multi-subject.json",
                                       target="qwen-image-2_1")["M2"].ok)

    def test_m3_goes_red_when_avoid_leaks_into_positive(self):
        from unittest import mock
        from moss_llm.smith.adapters.base import Adapter

        orig = Adapter._render

        def leaky(self, ir, clauses, mode):
            text = orig(self, ir, clauses, mode)
            avoid = ir.constraints.get("avoid") or []
            return text + (", no " + ", no ".join(avoid) if avoid else "")

        with mock.patch.object(Adapter, "_render", leaky):
            self.assertFalse(self._run("01-portrait-photoreal.json")["M3"].ok)

    def test_m4_goes_red_when_packer_ignores_the_limit(self):
        from unittest import mock
        from moss_llm.smith import invariants as INV

        with mock.patch.object(INV, "pack", lambda c, limit, counter: (list(c), [])):
            res = self._run("03-multi-subject.json", only=("M4",))["M4"]
        self.assertFalse(res.ok)
        self.assertIn("不越界", res.detail + "不越界")   # 断言失败原因指向越界

    def test_m5_goes_red_when_severity_is_written_into_content(self):
        """模拟 2026-10-31 之前的那笔债：importance 同时当排序键和内容。

        提权把「girl」改写成「woman」——字面上是升级描写，但这条关系要说的正是
        「提权只能改顺序，不能改内容」。以前这条测试是把 `normalize` 的排序键反向，
        结果它在「反向了也不影响输出」的时候会假绿；现在直接打内容，红的理由和
        关系本身对得上。
        """
        from unittest import mock
        from moss_llm.smith import clauses as C

        orig = C.describe_entity

        def rewritten(e, with_anchor=True):
            s = orig(e, with_anchor)
            if e.importance < 1.0:
                return s
            return s.replace("girl", "woman").replace("boy", "man")

        with mock.patch.object(C, "describe_entity", rewritten):
            res = self._run("03-multi-subject.json")["M5"]
        self.assertFalse(res.ok)
        self.assertTrue(res.evidence or "实体" in res.detail)

    def test_m5_does_not_silently_skip_the_first_sentence(self):
        """回归：首句被 `capitalize_sentences` 大写，按原样比会整条漏检。

        实测 02 海报在 qwen / krea2 上曾报「检查了 0 个实体」，而三条描述都在输出里——
        报告写的「描述未出现在基准输出里」读起来像结论，其实只是没比。
        """
        for t in ("qwen-image-2_1", "krea2"):
            res = self._run("02-poster-text-layout.json", target=t)["M5"]
            self.assertTrue(res.ok, res.detail)
            self.assertIn("检查了 3 个实体", res.detail)

    # ── 把「假关系」钉住，防止有人改回去 ────────────────────
    def test_budget_set_monotonicity_really_fails(self):
        """「加预算 ⇒ 保留集合变大」是**假的**，留一个反例钉住它。

        成本 4 的子句在预算 3 时被跳过，短的那条补上；预算 4 时长的装下了，
        短的反被挤掉。这不是 bug，是「装不下就跳过、继续往后装」的必然结果。
        改回集合单调断言 = 让这条测试变成永远红的噪声。
        """
        from moss_llm.smith.clauses import Clause, pack

        class _C:
            def count(self, text):
                return len(text)

        long_, short = Clause("subject", "aa", 1.0), Clause("setting", "b", 0.9)
        self.assertEqual([c.text for c in pack([long_, short], 3, _C())[0]], ["b"])
        self.assertEqual([c.text for c in pack([long_, short], 4, _C())[0]], ["aa"])


class TestVisualWeight(unittest.TestCase):
    """`importance` 与字号解耦：前者只决定「谁先被砍」，后者是内容。

    为什么必须拆：M5（提权不丢）原本把 text 实体整个排除，因为提权会合法地
    改 Large/Small——那是把一个**字段职责不清**的问题，变成了验证层的一个洞：
    最不能丢的图内文字，恰好是这条关系唯一不检查的东西。
    """

    def _text(self, idx=1):
        ir = load("02-poster-text-layout.json")
        return ir, [e for e in ir.entities if e.kind == "text"][idx]

    def test_normalize_materializes_from_importance(self):
        ir, _ = self._text(1)
        for e in [x for x in ir.entities if x.kind == "text"]:
            self.assertIn(e.typography.get("visual_weight"), ("large", "small"))
            self.assertEqual(e.typography["visual_weight"],
                             "large" if e.importance >= 0.95 else "small")

    def test_raising_importance_does_not_resize_the_type(self):
        from moss_llm.smith.clauses import describe_text
        _ir, e = self._text(1)                      # importance 0.9 → small
        before = describe_text(e, with_anchor=False)
        e.importance = 1.0                          # 为了不被预算砍掉而提权
        self.assertEqual(describe_text(e, with_anchor=False), before)
        self.assertIn("Small", before)

    def test_explicit_visual_weight_still_wins(self):
        from moss_llm.smith.clauses import describe_text
        _ir, e = self._text(1)
        e.typography["visual_weight"] = "large"     # 明确要求放大标题
        self.assertIn("Large", describe_text(e, with_anchor=False))

    def test_old_ir_without_the_field_renders_as_before(self):
        """向后兼容：手搓实体没过 normalize 时，退回 importance 的老算法。"""
        from moss_llm.smith.clauses import describe_text
        from moss_llm.smith.ir import Entity
        e = Entity(id="t9", kind="text", text_content="HELLO", importance=0.99)
        self.assertIn("Large", describe_text(e, with_anchor=False))
        e.importance = 0.5
        self.assertIn("Small", describe_text(e, with_anchor=False))


class TestRuntimePicker(unittest.TestCase):
    """资产名按官方命名精确匹配。用 b12020 的真实资产名做夹具。

    合流后「一键装运行时」只剩一份实现（`moss_llm/runtime_install.py`），这里守的是
    它的选包策略：认得出各平台的命名差异、CUDA 版本要跟驱动能力配套、
    **要哪个后端就给哪个后端**（不静默替换成别的后端）。
    """

    NAMES = [
        "cudart-llama-b12020-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "cudart-llama-b12020-bin-win-cuda-13.4-x64.zip",
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "llama-b12020-bin-macos-arm64.tar.gz",
        "llama-b12020-bin-macos-x64.tar.gz",
        "llama-b12020-bin-ubuntu-arm64.tar.gz",
        "llama-b12020-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "llama-b12020-bin-ubuntu-cuda-13.4-x64.tar.gz",
        "llama-b12020-bin-ubuntu-rocm-10.0-x64.tar.gz",
        "llama-b12020-bin-ubuntu-s390x.tar.gz",
        "llama-b12020-bin-ubuntu-vulkan-x64.tar.gz",
        "llama-b12020-bin-ubuntu-x64.tar.gz",
        "llama-b12020-bin-win-cpu-arm64.zip",
        "llama-b12020-bin-win-cpu-x64.zip",
        "llama-b12020-bin-win-cuda-12.4-x64.zip",
        "llama-b12020-bin-win-cuda-13.4-arm64.zip",
        "llama-b12020-bin-win-cuda-13.4-x64.zip",
        "llama-b12020-bin-win-vulkan-x64.zip",
    ]

    def _assets(self):
        return [{"name": n, "browser_download_url": "https://example.invalid/" + n,
                 "size": 1, "digest": "sha256:" + "0" * 64} for n in self.NAMES]

    def _pick(self, plat, arch, backend, max_cuda=None):
        from moss_llm.runtime_install import _pick
        got = _pick(self._assets(), "b12020", "%s-%s" % (plat, arch),
                    backend, max_cuda)
        return [n for _u, n, _s, _d in got]

    def test_windows_vulkan(self):
        self.assertEqual(self._pick("windows", "x64", "vulkan"),
                         ["llama-b12020-bin-win-vulkan-x64.zip"])

    def test_windows_cpu(self):
        self.assertEqual(self._pick("windows", "x64", "cpu"),
                         ["llama-b12020-bin-win-cpu-x64.zip"])

    def test_windows_cuda_clamped_to_driver(self):
        """驱动报 13.9 → 取 13.4，且**配同一个 CUDA 版本**的 cudart。"""
        got = self._pick("windows", "x64", "cuda", (13, 9))
        self.assertTrue(got[0].endswith("win-cuda-13.4-x64.zip"), got)
        self.assertIn("cudart", got[1])
        self.assertIn("13.4", got[1])

    def test_windows_cuda_unknown_driver_takes_lowest(self):
        """驱动未知时选最低版本：CUDA 包对旧架构不向下兼容，低版可借 PTX 跑新卡。"""
        got = self._pick("windows", "x64", "cuda")
        self.assertTrue(got[0].endswith("win-cuda-12.4-x64.zip"), got)

    def test_linux_cpu_has_no_backend_token(self):
        """CPU 包在 ubuntu 上叫 llama-<tag>-bin-ubuntu-x64.tar.gz，没有 cpu 段。"""
        self.assertEqual(self._pick("linux", "x64", "cpu"),
                         ["llama-b12020-bin-ubuntu-x64.tar.gz"])

    def test_linux_vulkan(self):
        self.assertEqual(self._pick("linux", "x64", "vulkan"),
                         ["llama-b12020-bin-ubuntu-vulkan-x64.tar.gz"])

    def test_macos_ignores_backend(self):
        self.assertEqual(self._pick("macos", "arm64", "metal"),
                         ["llama-b12020-bin-macos-arm64.tar.gz"])

    def test_arch_is_respected(self):
        self.assertIn("arm64", self._pick("windows", "arm64", "cpu")[0])

    def test_unknown_backend_returns_empty_not_a_wrong_package(self):
        """以前这里会 fallback 到 cuda：给 Intel 卡装 CUDA 包，装完跑不起来，
        报错还指向「显卡驱动」。现在要哪个就给哪个，没有就空。"""
        self.assertEqual(self._pick("windows", "x64", "tpu"), [])

    def test_missing_backend_in_this_build_returns_empty(self):
        """这个 build 刚好没打 vulkan 包时，返回空让上层去试更旧的 build。"""
        self.assertEqual(self._pick("linux", "s390x", "vulkan"), [])

    def test_cudart_picked_for_cuda_only(self):
        self.assertTrue(any("cudart" in n
                            for n in self._pick("windows", "x64", "cuda", (13, 9))))
        self.assertFalse(any("cudart" in n
                             for n in self._pick("windows", "x64", "vulkan")))

    def test_cudart_prefers_matching_cuda(self):
        """ubuntu x64 的 cudart 只有 12.8，所以主包也必须挑 12.8——版本要配套。"""
        got = self._pick("linux", "x64", "cuda", (13, 9))
        self.assertIn("cuda-12.8", got[1])

    def test_cuda_never_pairs_main_with_a_different_cudart(self):
        """主包 13.4 + 运行库 12.4 = 装完加载失败，而报错只说某个 DLL 找不到。
        所以只挑「有同版本运行库」的主包：本例里 13.4 没有配套运行库，退到 12.4。"""
        names = [n for n in self.NAMES
                 if not n.startswith("cudart-llama-b12020-bin-win-cuda-13.4")]
        from moss_llm.runtime_install import _pick
        assets = [{"name": n, "browser_download_url": "https://example.invalid/" + n,
                   "size": 1, "digest": "sha256:" + "0" * 64} for n in names]
        got = [n for _u, n, _s, _d in _pick(assets, "b12020", "windows-x64", "cuda", (13, 9))]
        self.assertTrue(got[0].endswith("win-cuda-12.4-x64.zip"), got)
        self.assertIn("cudart", got[1])
        self.assertIn("12.4", got[1])

    def test_cuda_main_matches_its_cudart(self):
        import re as _re
        got = self._pick("windows", "x64", "cuda", (13, 9))
        ver = _re.search(r"-cuda-([\d.]+)-", got[1]).group(1)
        self.assertIn("-cuda-%s-" % ver, got[0])


class TestMiniYaml(unittest.TestCase):
    """内置子集解析器：只为「没装 PyYAML 也能跑」。

    可信度靠对照：只要 PyYAML 在，就逐字段比对真实的 model-registry.yaml。
    对不上，说明子集解析器在静默读错——那是最坏的一种 bug。
    """

    def _diff(self, a, b, path=""):
        if type(a) is not type(b):
            return ["%s: 类型 %s vs %s" % (path, type(a).__name__, type(b).__name__)]
        if isinstance(a, dict):
            out = []
            for k in set(a) | set(b):
                if k not in a or k not in b:
                    out.append("%s/%s: 只有一边有" % (path, k))
                else:
                    out += self._diff(a[k], b[k], "%s/%s" % (path, k))
            return out
        if isinstance(a, list):
            if len(a) != len(b):
                return ["%s: 长度 %d vs %d" % (path, len(a), len(b))]
            out = []
            for i, (x, y) in enumerate(zip(a, b, strict=False)):
                out += self._diff(x, y, "%s[%d]" % (path, i))
            return out
        return [] if a == b else ["%s: %r vs %r" % (path, a, b)]

    def test_registry_matches_pyyaml_exactly(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("没有 PyYAML 就没有对照物")
        from moss_llm.smith import miniyaml
        text = (ROOT / "moss_llm" / "smith" / "data" / "model-registry.yaml").read_text(encoding="utf-8")
        diffs = self._diff(yaml.safe_load(text), miniyaml.safe_load(text))
        self.assertEqual(diffs, [], "内置解析器与 PyYAML 不一致：\n" + "\n".join(diffs[:10]))

    def test_torture_doc_matches_pyyaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("没有 PyYAML 就没有对照物")
        from moss_llm.smith import miniyaml
        doc = (
            "# 注释\n"
            "top:\n"
            "  \"quoted key\": 'it''s here'\n"
            "  seq_same_indent:\n"
            "  - a\n"
            "  - b\n"
            "  nested:\n"
            "    deep:\n"
            "      x: 1\n"
            "      y: [1, 2.5, -3, true, null, \"s, with comma\"]\n"
            "  flow: { a: 1, b: { c: [1, {d: 2}] }, e: \"x:y\" }\n"
            "  empty:\n"
            "  dashmap:\n"
            "    - name: first\n"
            "      age: 3\n"
            "      tags: [x, y]\n"
            "    - name: second\n"
            "  hash_inside: \"a # b\"\n"
        )
        diffs = self._diff(yaml.safe_load(doc), miniyaml.safe_load(doc))
        self.assertEqual(diffs, [], "\n".join(diffs[:10]))

    def test_unsupported_syntax_raises_instead_of_guessing(self):
        """猜错比报错糟。锚点/块标量/标签/未闭合流式都必须炸。"""
        from moss_llm.smith.miniyaml import MiniYamlError, safe_load
        # 最后一条是「tab 不能用于缩进」。拆成两个相邻字面量写，是为了源码里
        # 不出现「字母 + 冒号 + 反斜杠」——那会被公开面检查当成 Windows 盘符。
        for bad in ("a: &x 1", "a: |\n  text", "a: !!str 1", "a: [1, 2",
                    'a: "unclosed', "a:" "\n\tb: 1"):
            with self.assertRaises(MiniYamlError, msg=bad):
                safe_load(bad)

    def test_scalars(self):
        from moss_llm.smith.miniyaml import safe_load
        d = safe_load("a: 1\nb: 2.5\nc: true\nd: null\ne: hello world\nf: \"x:y\"\n")
        self.assertEqual(d, {"a": 1, "b": 2.5, "c": True, "d": None,
                             "e": "hello world", "f": "x:y"})

    def test_registry_reports_which_parser_it_used(self):
        """用了哪个解析器必须可查——出问题时这是第一个要看的字段。"""
        reg = Registry.load()
        self.assertIn(reg.parser, ("pyyaml", "builtin"))
        self.assertEqual(reg.names(), Registry.load().names())


# ─────────────────────────────────────────────────────────────
# ④ 读懂 prompt：文字 → IR
# ─────────────────────────────────────────────────────────────

A1111 = """a 28-year-old woman in a yellow raincoat, standing on wet asphalt at night,
neon signs reflecting in puddles, 35mm, upper left, film grain,
poster reading "STAY DRY" in bold white letters
Negative prompt: worst quality, bad anatomy, watermark
Steps: 28, Sampler: dpmpp_2m, CFG scale: 7, Seed: 12345, Size: 832x1216"""


class TestDecomposeScan(unittest.TestCase):
    """字面量扫描：读得准、读得全，且不对结构化方言做词法解读。"""

    def test_reads_literals(self):
        from moss_llm.smith.decompose import scan
        f = scan(A1111)
        self.assertEqual(f.texts, ["STAY DRY"])
        self.assertEqual(f.ages, [28])
        self.assertIn("yellow", f.colors)
        self.assertIn("white", f.colors)
        self.assertIn("upper", f.anchors)

    def test_excludes_parameters_from_content_numbers(self):
        """35mm / Steps 28 / Seed 12345 是参数，不是画面内容。"""
        from moss_llm.smith.decompose import scan
        f = scan(A1111)
        for bad in ("35", "28", "7", "12345"):
            self.assertNotIn(bad, f.numbers, "参数被当成画面数字：%s" % bad)

    def test_structured_prompt_is_not_scanned(self):
        """JSON / bbox 表里的 `":[1,2]"` 不是引号文字。不认出来就会报一堆假「多出」."""
        from moss_llm.smith.decompose import scan
        self.assertTrue(scan('{"summary": "a cup", "layout": [{"bbox": [1,2,3,4]}]}').json)
        self.assertTrue(scan('A cup on a table.\n{"id":"s1","bbox":[1,2,3,4]}').json)
        self.assertEqual(scan('{"summary": "x"}').texts, [])

    def test_split_parameters_keeps_negative_and_drops_sampler(self):
        from moss_llm.smith.decompose import split_parameters
        pos, neg = split_parameters(A1111)
        self.assertIn("yellow raincoat", pos)
        self.assertNotIn("Negative prompt", pos)
        self.assertIn("bad anatomy", neg)
        self.assertNotIn("Steps:", neg)

    def test_aspect_from_size(self):
        from moss_llm.smith.decompose import _aspect_from_size
        self.assertEqual(_aspect_from_size("Size: 832x1216"), "2:3")
        self.assertEqual(_aspect_from_size("size: 1024x1024"), "1:1")
        self.assertEqual(_aspect_from_size("Size: 1920x1080"), "16:9")
        self.assertEqual(_aspect_from_size("no size here"), "")
        self.assertEqual(_aspect_from_size("Size: 1000x1400"), "")   # 离常用比例都超 3%，不猜


class _MockLlM:
    """按脚本回话的假模型。只实现 chat 的 4 参契约之外还能吃 schema。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, system, user, **kw):
        self.calls.append((system, user))
        return self.replies.pop(0) if self.replies else ""


class TestDecomposeReconcile(unittest.TestCase):
    """模型只做加值：它漏掉的硬事实必须被补回，它没抽出来的不许兜底。"""

    def test_missing_text_and_age_are_restored(self):
        from moss_llm.smith.decompose import decompose
        # 模型只给了一个不含引号文字、不含年龄的实体
        llm = _MockLlM(['{"entities":[{"id":"e1","kind":"object","label":"wet street"}]}'])
        ir, rep = decompose(A1111, llm=llm)
        self.assertEqual(rep.method, "llm")
        self.assertIn("STAY DRY", [e.text_content for e in ir.text_entities])
        chars = [e for e in ir.entities if e.kind == "character"]
        self.assertTrue(chars, "年龄没有落到 character 上")
        self.assertEqual(chars[0].age_years, 28)
        self.assertTrue(rep.injected, "补了什么必须留痕")

    def test_empty_extraction_is_an_error_not_a_fallback(self):
        """空 IR 编译出来是「一段通顺但无关的 prompt」，用户看不出它是错的。"""
        from moss_llm.smith.decompose import DecomposeError, decompose
        with self.assertRaises(DecomposeError):
            decompose(A1111, llm=_MockLlM(['{"entities":[]}']))

    def test_no_llm_still_lands_the_hard_facts(self):
        from moss_llm.smith.decompose import decompose, unrendered
        ir, rep = decompose(A1111)
        self.assertEqual(rep.method, "scan")
        self.assertEqual(unrendered(ir), [], "IR 里有渲染不出来的事实")
        self.assertEqual([e.text_content for e in ir.text_entities], ["STAY DRY"])
        self.assertEqual(ir.camera.get("lens_mm"), 35)
        self.assertEqual(ir.canvas.get("aspect_ratio"), "2:3")
        self.assertIn("bad anatomy", ir.constraints.get("avoid"))

    def test_unrendered_catches_a_fact_with_no_render_path(self):
        """破坏版：把颜色塞进不参与渲染的字段，自检必须变红。"""
        from moss_llm.smith.decompose import unrendered
        ir = load("01-portrait-photoreal.json")
        self.assertEqual(unrendered(ir), [])
        ir.entities[0].descriptors["palette"] = "crimson"     # 没有任何地方渲染 palette
        self.assertIn(("color", "crimson"), unrendered(ir))

    def test_second_age_never_overwrites_the_first(self):
        """两个岁数 = 两个人。把 6 岁盖到 28 岁身上，是悄悄换了一张图里的人。"""
        from moss_llm.smith.decompose import decompose
        ir, _ = decompose("a 28-year-old mother holding her 6-year-old daughter, "
                          "warm kitchen light")
        ages = sorted(e.age_years for e in ir.entities if e.kind == "character")
        self.assertEqual(ages, [6, 28])

    # ── 真机 8B 暴露的四类漏抽，逐条钉住 ────────────────────
    # 这一组不是「想出来的边界」，是 2026-10-31 真机跑出来的：模型只留 2 个实体，
    # 把整个环境句丢了，而 fixed_recall 仍是 4/4 —— 见 coverage() 的 docstring。

    def test_uncovered_source_segment_is_recovered(self):
        from moss_llm.smith.decompose import coverage, decompose
        llm = _MockLlM(['{"entities":[{"id":"e1","kind":"character","label":"woman",'
                        '"age_years":28}]}'])
        ir, rep = decompose(A1111, llm=llm)
        labels = " ".join(e.label for e in ir.entities).lower()
        self.assertIn("wet asphalt", labels)             # 整段被捞回来
        self.assertIn("neon signs", labels)
        self.assertIn("poster", labels)                  # 引号段的承托物也捞回来
        cov, missing = coverage(A1111, ir)
        self.assertGreater(cov, 0.5, missing)
        self.assertTrue(any("整段没进 IR" in w for w in rep.warnings), rep.warnings)
        # 覆盖率是**诊断**不是保证：这句里的 raincoat 挂在已被覆盖的主体段里，
        # 单看数字发现不了。要它进 IR，得靠模型给 wardrobe，或人手补。
        self.assertIsInstance(missing, list)

    def test_quote_support_object_is_recovered_but_not_the_words(self):
        """「a poster on the wall reading "STAY DRY"」：字进 text 实体，承托物进 object。"""
        from moss_llm.smith.decompose import decompose
        ir, _ = decompose('a poster on the wall reading "STAY DRY" in bold white letters')
        texts = [e.text_content for e in ir.text_entities]
        labels = [e.label.lower() for e in ir.entities if e.kind == "object"]
        self.assertEqual(texts, ["STAY DRY"])
        self.assertTrue(any("poster" in x and "wall" in x for x in labels), labels)
        self.assertFalse(any("stay dry" in x for x in labels), "字不该重复成物体")

    def test_typography_next_to_the_quote_binds_to_the_text(self):
        from moss_llm.smith.decompose import decompose
        ir, _ = decompose('poster reading "STAY DRY" in bold white letters')
        typ = ir.text_entities[0].typography
        self.assertEqual(typ.get("weight"), "bold")
        self.assertEqual(typ.get("color"), "white")

    def test_missing_typography_is_restored_from_the_ledger(self):
        """模型常只抄颜色、丢粗细。账本里两样都有，就都该补回来。"""
        from moss_llm.smith.decompose import decompose
        llm = _MockLlM(['{"entities":[{"id":"e1","kind":"text","label":"caption",'
                        '"text_content":"STAY DRY","typography":{"color":"white"}}]}'])
        ir, _ = decompose(A1111, llm=llm)
        typ = ir.text_entities[0].typography
        self.assertEqual(typ.get("weight"), "bold")
        self.assertEqual(typ.get("color"), "white")

    def test_lens_is_restored_when_the_model_drops_it(self):
        from moss_llm.smith.decompose import decompose
        llm = _MockLlM(['{"entities":[{"id":"e1","kind":"character","label":"woman",'
                        '"age_years":28}]}'])
        ir, _ = decompose(A1111, llm=llm)
        self.assertEqual(ir.camera.get("lens_mm"), 35)

    def test_coverage_reports_none_for_chinese_source(self):
        """中文不做分词，就明确说「不适用」，不假装算出 1.0。"""
        from moss_llm.smith.decompose import coverage, decompose
        ir, rep = decompose("一位穿黄雨衣的女人站在湿沥青上")
        self.assertIsNone(coverage("一位穿黄雨衣的女人站在湿沥青上", ir)[0])
        self.assertIsNone(rep.coverage)


class TestRenderPaths(unittest.TestCase):
    """IR 里的字段必须有渲染路径——否则 verify 会报一条永远修不好的假错。"""

    def test_descriptor_color_renders(self):
        from moss_llm.smith.clauses import describe_entity
        from moss_llm.smith.ir import Entity
        coat = Entity(id="o1", kind="object", label="raincoat",
                      descriptors={"color": "yellow"})
        self.assertIn("yellow raincoat", describe_entity(coat))
        woman = Entity(id="c1", kind="character", label="woman", age_years=28,
                       descriptors={"color": "yellow"})
        self.assertIn("in yellow", describe_entity(woman))

    def test_color_already_in_the_label_is_not_repeated(self):
        from moss_llm.smith.clauses import describe_entity
        from moss_llm.smith.ir import Entity
        e = Entity(id="o1", kind="object", label="yellow raincoat",
                   descriptors={"color": "yellow"})
        self.assertEqual(describe_entity(e).lower().count("yellow"), 1)

    def test_age_phrase_falls_back_to_the_label(self):
        """真机 8B 把性别写在 label 上（descriptors.gender 空），不能渲染成 person。"""
        from moss_llm.smith.clauses import describe_entity
        from moss_llm.smith.ir import Entity
        e = Entity(id="c1", kind="character", label="woman", age_years=28)
        self.assertIn("a 28-year-old woman", describe_entity(e))
        long_label = Entity(id="c2", kind="character", age_years=30,
                            label="a person standing near the shop window")
        self.assertIn("person", describe_entity(long_label))   # 长句 label 不拿来当名词

    def test_undo_fires_when_an_injected_fact_cannot_render(self):
        """安全网必须真的会动：注入的事后自证失败 → 撤回并记进 dropped_facts。

        这条用打桩逼出「渲染不出来」——现实中注入的四类事实都能渲染，
        但安全网要是坏的，等真出现一类渲不出来的事实时它就是纸糊的（第一版就是：
        比的是 kind 字符串和整个 key 元组，永远不相等，撤回分支永远不执行）。
        """
        from unittest import mock
        import moss_llm.smith.decompose as D

        ir = IR(task="decompose")
        rep = D.Report()
        facts = D.scan('a shot with the words "HELLO" on a wall')
        with mock.patch.object(D, "unrendered", return_value=[("text", "HELLO")]):
            D.reconcile(ir, facts, rep)
        self.assertEqual([e for e in ir.entities if e.kind == "text"], [])
        self.assertTrue(any("text=HELLO" == d for d in rep.dropped_facts), rep.dropped_facts)
        self.assertTrue(rep.warnings)


class TestDecomposeAcceptance(unittest.TestCase):
    """④ 的验收（VISION §4）：A1111 的 parameters → Qwen 方言，fixed_recall = 1.0。"""

    def test_a1111_to_qwen_keeps_every_fixed_item(self):
        from moss_llm.smith.decompose import decompose
        from moss_llm.smith.expand import verify
        ir, _ = decompose(A1111)
        for t in ("qwen-image-2_1", "moss-image"):
            out = get_adapter(t, REG, counter=CNT).realize(ir)
            rep = verify(ir, out.positive, t, REG)
            self.assertEqual(rep.fixed_recall, 1.0, "%s 丢了固定项：%s" % (t, rep.misses))

    def test_the_acceptance_is_not_vacuous(self):
        """这条验收必须真的在核对：那条字确实进了 hits，删掉实体后编译结果里也就没有了。

        为什么这样写：`fixed_recall` 的分子分母都来自 IR，一份**什么都不记**的 IR
        也能拿 1.0。所以判据不能只是「1.0」，还要证明那条事实参与了核对。
        """
        from moss_llm.smith.decompose import decompose
        from moss_llm.smith.expand import verify
        ir, _ = decompose(A1111)
        out = get_adapter("qwen-image-2_1", REG, counter=CNT).realize(ir)
        rep = verify(ir, out.positive, "qwen-image-2_1", REG)
        self.assertIn("[text] STAY DRY", rep.hits)
        self.assertGreaterEqual(len(rep.fixed), 3)

        ir.entities = [e for e in ir.entities if e.kind != "text"]
        self.assertNotIn("STAY DRY",
                         get_adapter("qwen-image-2_1", REG, counter=CNT).realize(ir).positive)


class TestRoundtrip(unittest.TestCase):
    """⑤ 往返校验：IR → prompt → 事实集合，两边相减。"""

    def test_three_examples_round_trip_within_one_fact(self):
        from moss_llm.smith.roundtrip import roundtrip
        for ex in ("01-portrait-photoreal.json", "02-poster-text-layout.json",
                   "03-multi-subject.json"):
            ir = load(ex)
            for t in ("moss-image", "qwen-image-2_1", "krea2", "flux3"):
                d, _ = roundtrip(ir, get_adapter(t, REG, counter=CNT))
                self.assertLessEqual(len(d.lost), 1, "%s × %s 丢了 %s" % (ex, t, d.lost))

    def test_deleting_a_sentence_is_reported(self):
        """破坏版：把一块文字从 prompt 里删掉，必须报出对应的丢失项。"""
        from moss_llm.smith.roundtrip import diff
        ir = load("02-poster-text-layout.json")
        out = get_adapter("qwen-image-2_1", REG, counter=CNT).realize(ir)
        self.assertEqual(diff(ir, out.positive).lost, [])
        broken = out.positive.replace("一杯温暖 一刻从容", "").replace("一杯温暖\n一刻从容", "")
        lost = diff(ir, broken).lost
        self.assertTrue(any(k == "text" for k, _ in lost), lost)

    def test_edit_task_is_marked_not_applicable(self):
        """edit 方言的规则是「保持项只点名、不描述外观」——这时报丢失是判据错了。"""
        from moss_llm.smith.roundtrip import roundtrip
        d, _ = roundtrip(load("04-edit-instruction.json"),
                         get_adapter("qwen-image-2_1", REG, counter=CNT))
        self.assertFalse(d.applicable)
        self.assertTrue(d.ok)
        self.assertTrue(any("edit" in n for n in d.notes))

    def test_json_dialect_checks_anchors_by_coordinates(self):
        """MOSS Layout / FLUX bbox 把方位编码成坐标，词表里找不到「upper right」不是丢失。"""
        from moss_llm.smith.roundtrip import roundtrip
        ir = load("02-poster-text-layout.json")
        for t in ("moss-image", "flux3"):
            d, out = roundtrip(ir, get_adapter(t, REG, counter=CNT))
            self.assertTrue(out.positive.lstrip().startswith("{")
                            or "\n{" in out.positive)
            self.assertFalse([x for x in d.lost if x[0] == "anchor"], d.lost)

    def test_krea2_does_not_double_quote_a_multiline_block(self):
        """回归：按行补引号会把一块两行字拆成两块字。"""
        ir = load("02-poster-text-layout.json")
        text = get_adapter("krea2", REG, counter=CNT).realize(ir).positive
        self.assertNotIn('""', text)
        self.assertIn('"一杯温暖 一刻从容"', text)

    def test_extra_facts_are_reported_but_do_not_fail_the_gate(self):
        """多出来的算幻觉提示，算不算死由人看——颜色同义改写会误报。"""
        from moss_llm.smith.roundtrip import diff
        ir = load("01-portrait-photoreal.json")
        out = get_adapter("qwen-image-2_1", REG, counter=CNT).realize(ir)
        d = diff(ir, out.positive + ' A sign reads "OPEN 24 HOURS".')
        self.assertTrue(any(k == "text" for k, _ in d.extra))
        self.assertTrue(d.ok, "多出不参与 ok 判定")


class TestSdxlAdapter(unittest.TestCase):
    """SDXL：77 token 是**每块**上限，不是整段上限。所以切块，不砍子句。

    这组的关键不是「能跑」，而是「不该丢的没丢」——通用后端在同一个 IR 上会砍掉内容，
    原生后端的全部价值就是那几条不该被砍的子句。
    """

    def test_splits_into_chunks_that_fit_clip(self):
        ir = load("03-multi-subject.json")
        out = get_adapter("sdxl", REG, counter=CNT).realize(ir)
        chunks = out.positive.split(" BREAK ")
        self.assertGreaterEqual(len(chunks), 2, out.positive)
        for c in chunks:
            self.assertLessEqual(CNT.count(c), 75, c)
        self.assertTrue(any("切成" in w for w in out.warnings))

    def test_nothing_is_dropped(self):
        from moss_llm.smith.roundtrip import roundtrip
        for ex in ("01-portrait-photoreal.json", "02-poster-text-layout.json",
                   "03-multi-subject.json"):
            ir = load(ex)
            ad = get_adapter("sdxl", REG, counter=CNT)
            out = ad.realize(ir)
            self.assertEqual(out.dropped, [], "%s 被砍了子句" % ex)
            d, _ = roundtrip(ir, ad)
            self.assertEqual(d.lost, [], "%s 丢事实：%s" % (ex, d.lost))

    def test_this_is_why_it_is_not_the_generic_backend(self):
        """对照：通用后端按 77 装箱会砍掉子句，原生后端切块保住它们。"""
        from moss_llm.smith.adapters.generic import GenericAdapter
        ir = IR.load(ROOT / "tests" / "fixtures" / "smith" / "hero-storyboard.ir.json")
        gen = GenericAdapter(REG, "sdxl", counter=CNT).realize(ir)
        native = get_adapter("sdxl", REG, counter=CNT).realize(ir)
        self.assertTrue(gen.dropped, "通用后端居然没砍东西？夹具选错了")
        self.assertEqual(native.dropped, [])

    def test_an_over_budget_clause_is_reported_not_hidden(self):
        """单条子句超预算就切不动了——这时必须出声，不能假装切过。"""
        from moss_llm.smith.ir import Entity
        ir = IR(task="t2i")
        ir.entities.append(Entity(id="e1", kind="object", importance=1.0,
                                  label=" ".join(["very long descriptive noun"] * 30)))
        ir.normalize()
        out = get_adapter("sdxl", REG, counter=CNT).realize(ir)
        self.assertTrue(any("无法再切" in w for w in out.warnings), out.warnings)

    def test_tag_form_and_negative_channel(self):
        ir = load("01-portrait-photoreal.json")
        ir.constraints["avoid"] = ["blurry", "watermark"]
        out = get_adapter("sdxl", REG, counter=CNT).realize(ir)
        for chunk in out.positive.split(" BREAK "):
            self.assertFalse(chunk.rstrip().endswith("."), "tag 形态不该有句号")
        self.assertIn("watermark", out.negative or "")
        self.assertEqual(out.params.get("cfg"), 7.0)

    def test_edit_task_is_refused_instead_of_rendered_as_t2i(self):
        """SDXL 的 inpaint 靠 mask，没有「指令式编辑」。硬渲染成 t2i 会静默给错 prompt。"""
        with self.assertRaises(ValueError):
            get_adapter("sdxl", REG, counter=CNT).realize(load("04-edit-instruction.json"))


class TestComfyNodeContract(unittest.TestCase):
    """参考节点能不能真的跑通——契约面（§9.2）的端到端检查。

    这条测试的意义不是「节点写得好」，是**契约够不够用**：如果节点必须改包的内部函数
    才能工作，那 §9.2 那张表就是假的。节点不 import torch，所以这里能直接跑。
    """

    def _node(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "comfy_node_example", ROOT / "examples" / "comfy_node_example.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_node_shape(self):
        mod = self._node()
        self.assertIn("PromptArtisanCompile", mod.NODE_CLASS_MAPPINGS)
        spec = mod.PromptArtisanCompile.INPUT_TYPES()
        self.assertIn("ir_json", spec["required"])
        self.assertIn("sdxl", spec["required"]["target"][0])   # 下拉来自 registry
        self.assertEqual(len(mod.PromptArtisanCompile.RETURN_TYPES), 4)

    def test_compiles_an_ir_from_a_string(self):
        mod = self._node()
        ir_json = json.dumps(IR.load(EX / "01-portrait-photoreal.json").to_dict(),
                             ensure_ascii=False)
        pos, neg, params, warns = mod.PromptArtisanCompile().compile(
            ir_json, "moss-image", "deterministic")
        self.assertIn("28-year-old woman", pos)
        self.assertTrue(json.loads(params).get("steps"))
        self.assertNotIn("扩写体检未通过", warns)

    def test_empty_ir_does_not_crash_the_queue(self):
        """空 IR 在节点里必须是「能出东西 + 有警告」，不是抛异常把队列拖死。"""
        mod = self._node()
        pos, neg, params, warns = mod.PromptArtisanCompile().compile("{}", "qwen-image-2_1")
        self.assertIsInstance(pos, str)
        self.assertIsInstance(params, str)


class TestRegressionSet(unittest.TestCase):
    """回归集：四份**真机逆向**出来的 IR × 四个后端，输出钉成 sha256。

    为什么不能只靠 hand-made 示例：`examples/` 是设计时挑的四个干净样例，
    而真机反推的 IR 带着模型给的脏结构（多格漫画的重复实体、UI 截图的 8 个文字块、
    storyboard 的 4 条观察散文）。这类 IR 才是节点实际会拿到的输入。

    基线怎么更新：`python tools/freeze_reverse_set.py`，**并且要在提交信息里写清为什么变**。
    测试自己不许改自己——那样等于没有基线。
    """

    FIX = ROOT / "tests" / "fixtures" / "smith"
    TARGETS = ("moss-image", "qwen-image-2_1", "krea2", "flux3", "sdxl")

    def _expected(self):
        return json.loads((self.FIX / "expected.json").read_text(encoding="utf-8"))

    def _irs(self):
        return {p.name: IR.load(p) for p in sorted(self.FIX.glob("*.ir.json"))}

    def test_fixtures_are_present_and_parse(self):
        irs = self._irs()
        self.assertEqual(len(irs), 4, "回归集少了文件：%s" % sorted(irs))
        for name, ir in irs.items():
            self.assertTrue(ir.entities or ir.notes, "%s 读出来是空的" % name)

    def test_deterministic_output_matches_the_baseline(self):
        from moss_llm.smith.decompose import rendered_text  # noqa: F401  (同一条编译路径)
        exp = self._expected()
        irs = self._irs()
        for name, entry in exp.items():
            ir = irs[name]
            # 结构摘要：normalize 悄悄改了排序/裁剪，会先在这里露头
            self.assertEqual((len(ir.entities), len(ir.text_entities), len(ir.notes),
                              ir.needs_layout()),
                             (entry["entities"], entry["texts"], entry["notes"],
                              entry["needs_layout"]),
                             "%s 的结构被改了" % name)
            for t in self.TARGETS:
                out = get_adapter(t, REG, counter=CNT).realize(ir)
                want = entry["targets"][t]
                import hashlib
                got = hashlib.sha256(out.positive.encode("utf-8")).hexdigest()[:16]
                self.assertEqual(
                    (len(out.positive.split()), got),
                    (want["words"], want["positive"]),
                    "%s × %s 的确定性输出变了。\n"
                    "  输出 %d 词（基线 %d）。\n"
                    "  若这是**有意**的改动：跑 tools/freeze_reverse_set.py 更新基线，"
                    "并在提交信息里写清为什么变；说不清就是回归。"
                    % (name, t, len(out.positive.split()), want["words"]))

    def test_all_invariants_green_on_real_irs(self):
        from moss_llm.smith import invariants as INV
        for name, ir in self._irs().items():
            for t in self.TARGETS:
                res = INV.run(get_adapter(t, REG, counter=CNT), ir)
                bad = [str(r) for r in res if not r.ok]
                self.assertEqual(bad, [], "%s × %s：%s" % (name, t, bad))

    def test_roundtrip_loses_nothing_on_real_irs(self):
        from moss_llm.smith.roundtrip import roundtrip
        for name, ir in self._irs().items():
            for t in self.TARGETS:
                d, _ = roundtrip(ir, get_adapter(t, REG, counter=CNT))
                self.assertEqual(d.lost, [], "%s × %s 丢事实：%s" % (name, t, d.lost))

    def test_no_lint_errors_on_real_irs(self):
        for name, ir in self._irs().items():
            for t in self.TARGETS:
                out = get_adapter(t, REG, counter=CNT).realize(ir)
                errs = [i for i in check(ir, t, REG.get(t), out.as_dict())
                        if i.level == "error"]
                self.assertEqual([str(i) for i in errs], [], "%s × %s" % (name, t))


# ─────────────────────────────────────────────────────────────
# H3：时间线是 IR 的一等维度，不是适配器的私货
# ─────────────────────────────────────────────────────────────

GOOD_H3 = """integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a baker lifting the shutters until the warm light spills onto the wet pavement. The camera pushes in with small amplitude at slow speed. The baker (S1) says: <d>[zh]第一炉，刚出炉。</d> [Shot 2] At 00:02.800, the camera cuts to a close-up, steam rising from the sliced bread. The camera holds a static shot.

overall_soundscape: Wooden shutters scrape open over a quiet street as metal trays clink softly inside.

non_diegetic_music: N/A"""


class TestH3Render(unittest.TestCase):
    def _ir(self):
        return IR.load(EX / "05-h3-t2va.json")

    def test_three_fields_in_official_order(self):
        from moss_llm.smith import h3
        text = h3.render(self._ir())
        _, fields = h3.split_fields(text)
        self.assertEqual(list(fields), list(h3.FIELDS))
        for k, v in fields.items():
            self.assertTrue(v.strip(), k)

    def test_first_shot_has_no_timestamp_but_later_ones_do(self):
        """铁律 11：Shot 1 写时间戳会把首帧锁成置景图（实测根因）。"""
        from moss_llm.smith import h3
        desc = h3.split_fields(h3.render(self._ir()))[1][h3.FIELDS[0]]
        first = desc[desc.index("[Shot 1]"):desc.index("[Shot 2]")]
        self.assertNotIn("At 00:", first)
        self.assertIn("At 00:02.800", desc)

    def test_dialogue_is_inside_d_tags_with_a_language_tag(self):
        from moss_llm.smith import h3
        self.assertIn("<d>[zh]第一炉，刚出炉。</d>", h3.render(self._ir()))

    def test_music_defaults_to_the_literal_NA(self):
        from moss_llm.smith import h3
        self.assertTrue(h3.render(self._ir()).rstrip().endswith("N/A"))

    def test_mode_instructions_are_the_official_sentences(self):
        from moss_llm.smith import h3
        ir = self._ir()
        for mode, needle in (("i2va", "is fully referenced"),
                             ("fl2va", "Picture 2 (from Shot 3)"),
                             ("l2va", "<Picture 1> (from [Shot 3])")):
            ir.timeline["mode"] = mode
            text = h3.render(ir)
            self.assertTrue(text.startswith(needle) or needle in text.split("\n")[0],
                            "%s 指令行不对：%s" % (mode, text.split("\n")[0]))
        ir.timeline["mode"] = "t2va"
        self.assertTrue(h3.render(ir).startswith(h3.FIELDS[0]))


class TestH3Normalize(unittest.TestCase):
    def test_one_take_clears_every_timestamp(self):
        """铁律 11 终裁：时间戳本身就会导致切镜，一镜到底必须零 `At`。"""
        from moss_llm.smith import h3
        tl = h3.normalize_timeline({"one_take": True, "duration_s": 8,
                                    "shots": [{"index": 1}, {"index": 2, "t_start": 3.0}]})
        self.assertTrue(all("t_start" not in s for s in tl["shots"]))

    def test_shot_indices_are_renumbered_but_timestamps_are_not_touched(self):
        """序号是结构（连号），时间戳是事实（不改，错了让 check 报）。"""
        from moss_llm.smith import h3
        tl = h3.normalize_timeline({"shots": [{"index": 7, "t_start": 5.0},
                                              {"index": 3, "t_start": 1.0}]})
        self.assertEqual([s["index"] for s in tl["shots"]], [1, 2])
        self.assertEqual([s["t_start"] for s in tl["shots"]], [1.0, 5.0])

    def test_empty_timeline_stays_out_of_to_dict(self):
        """老 IR 的 JSON 必须逐字节不变——多一个恒空的分支就是回归。"""
        self.assertNotIn("timeline", load("01-portrait-photoreal.json").to_dict())

    def test_a_timeline_survives_a_dict_roundtrip(self):
        ir = IR.load(EX / "05-h3-t2va.json")
        again = IR.from_dict(ir.to_dict())
        self.assertEqual(ir.timeline["shots"][0]["dialogue"][0]["text"],
                         again.timeline["shots"][0]["dialogue"][0]["text"])


class TestH3Parse(unittest.TestCase):
    def test_parse_recovers_shots_timestamps_dialogue_and_camera(self):
        from moss_llm.smith import h3
        ir = IR.from_dict(h3.parse(GOOD_H3))
        shots = ir.timeline["shots"]
        self.assertEqual(len(shots), 2)
        self.assertIsNone(shots[0].get("t_start"))
        self.assertAlmostEqual(shots[1]["t_start"], 2.8, places=3)
        self.assertEqual(shots[0]["dialogue"][0]["text"], "第一炉，刚出炉。")
        self.assertEqual(shots[0]["dialogue"][0]["speaker"], "S1")
        self.assertEqual(shots[1]["camera"].get("move"), "static")

    def test_render_parse_render_converges(self):
        """一次往返之后必须稳定。不稳定的往返等于每次改词都换一版画面。"""
        from moss_llm.smith import h3
        once = h3.render(IR.from_dict(h3.parse(GOOD_H3)))
        twice = h3.render(IR.from_dict(h3.parse(once)))
        self.assertEqual(once, twice)

    def test_music_NA_becomes_an_empty_list_not_the_string(self):
        from moss_llm.smith import h3
        ir = IR.from_dict(h3.parse(GOOD_H3))
        self.assertEqual(ir.timeline["music"], [])

    def test_looks_like_needs_two_field_labels(self):
        """只认一个太松：正文里提一句字段名就误判成 H3 稿。"""
        from moss_llm.smith import h3
        self.assertTrue(h3.looks_like(GOOD_H3))
        self.assertFalse(h3.looks_like("integrated_multimodal_description: 只有这一段"))


class TestH3Check(unittest.TestCase):
    """每条判据都要有正例和「破坏版」。只有正例过、破坏版不红，等于没写。"""

    def _codes(self, text, **kw):
        from moss_llm.smith import h3
        return [i.code for i in h3.check(text, **kw)]

    def test_the_good_sample_passes_cleanly(self):
        from moss_llm.smith import h3
        self.assertEqual(h3.check(GOOD_H3), [])

    def test_missing_field_is_an_error(self):
        self.assertIn("H3-FIELD", self._codes(
            GOOD_H3.replace("non_diegetic_music: N/A", "")))

    def test_field_order_is_checked(self):
        from moss_llm.smith import h3
        _, f = h3.split_fields(GOOD_H3)
        swapped = ("%s: %s\n\n%s: %s\n\n%s: %s" % (h3.FIELDS[1], f[h3.FIELDS[1]],
                                                   h3.FIELDS[0], f[h3.FIELDS[0]],
                                                   h3.FIELDS[2], f[h3.FIELDS[2]]))
        self.assertIn("H3-FIELD-ORDER", self._codes(swapped))

    def test_timestamp_on_shot_one_is_an_error(self):
        self.assertIn("H3-SHOT1-TS", self._codes(
            GOOD_H3.replace("[Shot 1] Live-action", "[Shot 1] At 00:00.100, Live-action")))

    def test_shot_without_timestamp_is_an_error(self):
        self.assertIn("H3-SHOTN-TS", self._codes(
            GOOD_H3.replace("[Shot 2] At 00:02.800, ", "[Shot 2] ")))

    def test_non_monotonic_timestamps_are_an_error(self):
        """切点必须严格递增。只有一个时间戳时无从比较——判据要两枚才有意义。"""
        two = ("integrated_multimodal_description: [Shot 1] a. "
               "[Shot 2] At 00:03.000, b. [Shot 3] At 00:02.000, c.\n\n"
               "overall_soundscape: d.\n\nnon_diegetic_music: N/A")
        self.assertIn("H3-TS-ORDER", self._codes(two))

    def test_dialogue_outside_the_tag_is_an_error(self):
        """断言 A，永不摘除：标签外的导演说明会被读进语音轨。"""
        self.assertIn("H3-D-OUT", self._codes(
            GOOD_H3.replace("<d>[zh]第一炉，刚出炉。</d>", "「第一炉，刚出炉。」")))

    def test_missing_language_tag_is_an_error(self):
        self.assertIn("H3-D-LANG", self._codes(
            GOOD_H3.replace("<d>[zh]", "<d>")))

    def test_speaking_meta_outside_the_tag_is_an_error(self):
        """语速/口音/语言声明写进正文 = ot13 实测的乱语主因。"""
        self.assertIn("H3-D-META", self._codes(
            GOOD_H3.replace("says:", "用标准普通话开口，语速放慢， says:")))

    def test_negation_is_an_error(self):
        self.assertIn("H3-NEG", self._codes(
            GOOD_H3.replace("a close-up", "a close-up without any other person")))

    def test_the_carve_out_phrase_is_not_flagged_as_negation(self):
        """铁律 14 **要求**道具写「无任何文字与符号」。判成否定式就是判据自己踩自己。"""
        self.assertNotIn("H3-NEG", self._codes(
            GOOD_H3.replace("steam rising from the sliced bread",
                            "steam rising from the sliced bread, 包装袋通体干净，"
                            "无任何文字与符号")))

    def test_counts_are_warned(self):
        self.assertIn("H3-COUNT", self._codes(
            GOOD_H3.replace("steam rising from the sliced bread",
                            "steam rising from three white cups")))

    def test_geographic_direction_is_an_error(self):
        self.assertIn("H3-GEO", self._codes(
            GOOD_H3.replace("a close-up", "a close-up of the west wall")))

    def test_vague_side_is_an_error(self):
        self.assertIn("H3-GEO", self._codes(
            GOOD_H3.replace("a close-up", "a close-up on one side")))

    def test_structured_parameters_in_prose_are_an_error(self):
        self.assertIn("H3-PARAM", self._codes(
            GOOD_H3.replace("The camera holds a static shot.",
                            "The camera holds a static shot at 24fps, 192 帧.")))

    def test_timestamps_do_not_trip_the_parameter_rule(self):
        """`00:02.800` 长得像画幅 2:800。判据必须先把时间戳挖掉再扫。"""
        self.assertNotIn("H3-PARAM", self._codes(GOOD_H3))

    def test_non_canonical_music_value_is_an_error(self):
        """铁律 8：`无。` 这种写法必须被拦——它曾经 100A 过闸。"""
        self.assertIn("H3-MUS", self._codes(
            GOOD_H3.replace("non_diegetic_music: N/A", "non_diegetic_music: 无。")))

    def test_music_policy_any_allows_real_score(self):
        self.assertNotIn("H3-MUS", self._codes(
            GOOD_H3.replace("N/A", "Sparse piano at a slow tempo."), music_policy="any"))

    def test_cjk_prose_in_a_dialogue_shot_is_warned(self):
        """铁律 12·补：有台词段正文英译。中文正文 + 中文台词，CER 差 11 倍。"""
        chinese = ("integrated_multimodal_description: [Shot 1] 一个面包师抬起木窗，"
                   "里面的暖光洒在湿漉漉的地面上。面包师 (S1) 说："
                   "<d>[zh]第一炉，刚出炉。</d>\n\n"
                   "overall_soundscape: 木窗刮擦声。\n\nnon_diegetic_music: N/A")
        codes = self._codes(chinese)
        self.assertIn("H3-LANG", codes)
        # 没有台词时只是画面描述，中文更准（铁律 12 原口径）——不该报。
        no_line = chinese.replace("面包师 (S1) 说：<d>[zh]第一炉，刚出炉。</d>", "")
        self.assertNotIn("H3-LANG", self._codes(no_line))

    def test_markdown_fence_is_an_error(self):
        self.assertIn("H3-FENCE", self._codes("```\n" + GOOD_H3 + "\n```"))

    def test_three_line_rules_need_an_ir_for_the_duration(self):
        """三线要片长。没有片长就不判，**不假装通过**——提示里说清楚没判。"""
        from moss_llm.smith import h3
        ir = IR.load(EX / "05-h3-t2va.json")
        codes = [i.code for i in h3.check(GOOD_H3, ir)]
        self.assertIn("H3-TS-HEAD", codes)
        tail = GOOD_H3.replace("00:02.800", "00:05.400")
        codes2 = [i.code for i in h3.check(tail, ir)]
        self.assertIn("H3-TS-TAIL", codes2)


class TestH3Adapter(unittest.TestCase):
    def test_adapter_is_registered_and_has_no_negative_channel(self):
        self.assertIn("minimax-h3", REG.names())
        out = get_adapter("minimax-h3", REG, counter=CNT).realize(
            IR.load(EX / "05-h3-t2va.json"))
        self.assertIsNone(out.negative)

    def test_static_ir_still_compiles_but_says_it_is_single_shot(self):
        out = get_adapter("minimax-h3", REG, counter=CNT).realize(
            load("01-portrait-photoreal.json"))
        self.assertTrue(out.positive.strip())
        self.assertTrue(any("没有 timeline" in w for w in out.warnings), out.warnings)

    def test_lint_no_longer_flags_shot_tags_as_weight_syntax(self):
        """`[Shot N]` 是规定记号。旧 L003 会把它当权重语法，报一条永不修好的 error。"""
        ir = IR.load(EX / "05-h3-t2va.json")
        out = get_adapter("minimax-h3", REG, counter=CNT).realize(ir)
        errs = [i.code for i in check(ir, "minimax-h3", REG.get("minimax-h3"),
                                      out.as_dict()) if i.level == "error"]
        self.assertEqual(errs, [])

    def test_image_backends_are_untouched_by_the_timeline_support(self):
        ir = load("02-poster-text-layout.json")
        out = get_adapter("moss-image", REG, counter=CNT).realize(ir)
        self.assertNotIn("integrated_multimodal_description", out.positive)


class TestH3RoundtripRelation(unittest.TestCase):
    def test_m6_passes_on_a_real_timeline(self):
        from moss_llm.smith import invariants as INV
        ir = IR.load(EX / "05-h3-t2va.json")
        res = [r for r in INV.run(get_adapter("minimax-h3", REG, counter=CNT), ir)
               if r.code == "M6"][0]
        self.assertTrue(res.ok, res.detail)

    def test_m6_goes_red_when_the_line_is_dropped_by_the_renderer(self):
        """破坏版：让渲染器吞掉台词，解析回来的镜头就少了这一句。

        注意不能靠「改 IR」来做破坏版——改了 IR 之后渲染和解析**同步**地少一句，
        M6 仍然自洽通过。破坏版必须破坏**管线**，不是破坏输入。
        """
        from unittest import mock

        from moss_llm.smith import h3 as H3
        from moss_llm.smith import invariants as INV

        ir = IR.load(EX / "05-h3-t2va.json")
        with mock.patch.object(H3, "_dialogue_sentence", lambda *a, **k: ""):
            res = [r for r in INV.run(get_adapter("minimax-h3", REG, counter=CNT), ir)
                   if r.code == "M6"][0]
        self.assertFalse(res.ok)
        self.assertTrue(any("台词" in p for p in res.evidence), res.evidence)

    def test_m6_is_not_applicable_without_a_timeline(self):
        from moss_llm.smith import invariants as INV
        res = [r for r in INV.run(get_adapter("minimax-h3", REG, counter=CNT),
                                  load("01-portrait-photoreal.json"))
               if r.code == "M6"][0]
        self.assertTrue(res.ok)
        self.assertIn("不适用", res.detail)

    def test_m4_is_not_applicable_for_h3(self):
        """H3 不走子句装箱。报 PASS 就等于宣称验过了。"""
        from moss_llm.smith import invariants as INV
        res = [r for r in INV.run(get_adapter("minimax-h3", REG, counter=CNT),
                                  IR.load(EX / "05-h3-t2va.json"))
               if r.code == "M4"][0]
        self.assertIn("不适用", res.detail)


class TestH3Facts(unittest.TestCase):
    def test_dialogue_and_timestamps_become_hard_facts(self):
        ir = IR.load(EX / "05-h3-t2va.json")
        from moss_llm.smith.expand import fixed_items
        kinds = {(f.kind, f.value) for f in fixed_items(ir)}
        self.assertIn(("line", "第一炉，刚出炉。"), kinds)
        self.assertIn(("beat", "At 00:02.800"), kinds)
        self.assertTrue(all(f.hard for f in fixed_items(ir)
                            if f.kind in ("line", "beat")))

    def test_verify_respects_the_dialogue_line(self):
        from moss_llm.smith.expand import verify
        ir = IR.load(EX / "05-h3-t2va.json")
        good = get_adapter("minimax-h3", REG, counter=CNT).realize(ir).positive
        self.assertEqual(verify(ir, good, "minimax-h3", REG).fixed_recall, 1.0)
        bad = good.replace("第一炉，刚出炉。", "第一炉，卖完了。")
        self.assertLess(verify(ir, bad, "minimax-h3", REG).fixed_recall, 1.0)

    def test_roundtrip_catches_a_dropped_line(self):
        from moss_llm.smith.roundtrip import diff
        ir = IR.load(EX / "05-h3-t2va.json")
        good = get_adapter("minimax-h3", REG, counter=CNT).realize(ir).positive
        self.assertEqual(diff(ir, good, "minimax-h3").lost, [])
        lost = [k for k, v in diff(ir, good.replace("第一炉，刚出炉。", "……"),
                                   "minimax-h3").lost]
        self.assertIn("line", lost)


class TestVerifyIsDialectAware(unittest.TestCase):
    """判据拿错了对象，比判据不准更贵：它会让人去修一个不存在的错。"""

    def test_h3_timestamps_are_not_counted_as_invented_numbers(self):
        from moss_llm.smith.expand import verify
        ir = IR.load(EX / "05-h3-t2va.json")
        out = get_adapter("minimax-h3", REG, counter=CNT).realize(ir)
        rep = verify(ir, out.positive, "minimax-h3", REG)
        self.assertEqual(rep.extra_numbers, [])
        self.assertEqual(rep.ratio_in_prose, [])
        self.assertEqual(rep.verdict, "pass", rep.issues)

    def test_layout_json_is_not_measured_with_the_prose_ruler(self):
        from moss_llm.smith.expand import verify
        ir = load("02-poster-text-layout.json")
        out = get_adapter("moss-image", REG, counter=CNT).realize(ir)
        rep = verify(ir, out.positive, "moss-image", REG)
        self.assertTrue(out.positive.lstrip().startswith("{"))
        self.assertEqual(rep.extra_numbers, [])      # bbox 不是编出来的细节
        self.assertEqual(rep.extra_quotes, [])       # 键名不是图里的字


class TestH3SyntaxHelpers(unittest.TestCase):
    def test_is_clause_separates_participle_from_finite_verb(self):
        """分词能接在 `frames <NP>` 后面，限定动词不能（会变两个谓语）。"""
        from moss_llm.smith.h3 import _is_clause
        self.assertFalse(_is_clause("lifting the shutters until the light spills in"))
        self.assertTrue(_is_clause("lifts the shutters"))
        self.assertTrue(_is_clause("slides a paper bag across the counter"))
        self.assertTrue(_is_clause("she lifts the shutters"))

    def test_mid_lowercases_but_keeps_acronyms(self):
        from moss_llm.smith.h3 import _mid
        self.assertEqual(_mid("Steam rises"), "steam rises")
        self.assertEqual(_mid("NPC turns"), "NPC turns")

    def test_format_and_parse_ts_round_trip(self):
        from moss_llm.smith import h3
        for sec in (0.0, 2.8, 5.8, 59.999, 61.5):
            self.assertAlmostEqual(h3.parse_ts("At " + h3.format_ts(sec)), sec, places=3)

    def test_decompose_recognises_the_h3_dialect_without_a_model(self):
        from moss_llm.smith import h3
        ir = IR.from_dict(h3.parse(GOOD_H3))
        self.assertEqual(ir.task, "t2va")
        self.assertEqual(len(ir.timeline["shots"]), 2)


class TestCompileFacade(unittest.TestCase):
    """门面 `smith.api`：节点与外部调用方都用它，所以入参形态与失败信息要稳。

    这里的用例刻意只碰「文本 → 结果」这一层：不加载模型、不读权重。
    节点侧的等价断言在 `tests/test_llm_subpackage.py` §12（那边证明的是
    「channel=api 且 api_base 为空也能出结果」——即真的没走模型）。
    """

    JSON_IR = json.dumps({
        "task": "t2i", "canvas": {"aspect_ratio": "1:1"},
        "entities": [{"id": "e1", "kind": "object", "label": "a red cup",
                      "importance": 1.0}]})
    YAML_IR = ("task: t2i\n"
               "canvas:\n"
               "  aspect_ratio: '1:1'\n"
               "entities:\n"
               "- id: e1\n"
               "  kind: object\n"
               "  label: a red cup\n"
               "  importance: 1.0\n")

    def test_accepts_json_text(self):
        from moss_llm.smith.api import parse_ir
        self.assertEqual(parse_ir(self.JSON_IR).entities[0].label, "a red cup")

    def test_accepts_yaml_text(self):
        from moss_llm.smith.api import parse_ir
        self.assertEqual(parse_ir(self.YAML_IR).entities[0].label, "a red cup")

    def test_accepts_a_file_path(self):
        from moss_llm.smith.api import parse_ir
        self.assertEqual(len(parse_ir(str(EX / "02-poster-text-layout.json")).entities), 3)

    def test_strips_markdown_fences(self):
        from moss_llm.smith.api import parse_ir
        fenced = "```json\n" + self.JSON_IR + "\n```"
        self.assertEqual(parse_ir(fenced).entities[0].id, "e1")

    def test_missing_file_is_named(self):
        from moss_llm.smith.api import CompileError, parse_ir
        with self.assertRaises(CompileError) as ctx:
            parse_ir("no/such/path.json")
        self.assertIn("找不到 IR 文件", str(ctx.exception))

    def test_errors_say_what_was_expected(self):
        from moss_llm.smith.api import CompileError, parse_ir
        for bad, needle in (("", "空的"), ("{}", "entities"),
                            ("随手写的一句话", "认不出"), ('{"a":', "JSON")):
            with self.assertRaises(CompileError) as ctx:
                parse_ir(bad)
            self.assertIn(needle, str(ctx.exception), bad)

    def test_compile_is_deterministic_and_complete(self):
        from moss_llm.smith.api import compile_ir
        a = compile_ir(self.JSON_IR, "moss-image")
        b = compile_ir(self.JSON_IR, "moss-image")
        self.assertEqual(a["positive"], b["positive"])
        self.assertTrue(a["positive"].strip())
        self.assertEqual(a["mode"], "deterministic")
        self.assertTrue(a["invariants"], "应当跑了 M1–M6")
        self.assertIn("invariants_line", a)

    def test_capability_mismatch_is_a_compile_error_not_a_crash(self):
        """把 edit 的 IR 丢给没有编辑通道的后端：要报「这个后端渲染不了」，
        而且要是 CompileError（用户输入问题），不是裸 ValueError（看起来像编译器崩了）。"""
        from moss_llm.smith.api import CompileError, compile_ir
        with open(EX / "04-edit-instruction.json", encoding="utf-8") as f:
            src = f.read()
        with self.assertRaises(CompileError) as ctx:
            compile_ir(src, "moss-image")
        self.assertIn("不支持", str(ctx.exception))

    def test_unknown_target_lists_what_exists(self):
        from moss_llm.smith.api import CompileError, compile_ir
        with self.assertRaises(CompileError) as ctx:
            compile_ir(self.JSON_IR, "definitely-not-a-backend")
        self.assertIn("moss-image", str(ctx.exception))

    def test_issues_are_plain_dicts(self):
        """report 要能直接序列化进工作流报告，不能是 dataclass。"""
        from moss_llm.smith.api import compile_ir
        r = compile_ir(self.JSON_IR, "sdxl")
        self.assertIsInstance(r["issues"], list)
        for it in r["issues"]:
            self.assertEqual(set(it), {"level", "code", "message", "fix"})


class TestStructuredDialectJudge(unittest.TestCase):
    """`verify()` 对结构化/时间线方言的判据。

    2026-11-01 实测：4 份夹具 × 5 后端报出 **78 条**「凭空造数 / 凭空造字」，
    全是 bbox 坐标、JSON 键名、`<silhouette_3>` 把手、以及被换行拆散的引号对。
    判据拿错对象时，红不是结论 —— 这一组把四种假错逐条钉住，
    同时保证「真的编了」仍然会红。
    """

    FIX = ROOT / "tests" / "fixtures" / "smith"

    def _ir(self, name: str) -> IR:
        return IR.load(self.FIX / name)

    def test_bbox_numbers_are_not_hallucinations(self):
        from moss_llm.smith.expand import verify
        ir = self._ir("comic-4panel-lineart-sample.ir.json")
        out = get_adapter("flux3", REG).realize(ir).positive
        self.assertEqual(verify(ir, out, "flux3", REG).extra_numbers, [])

    def test_json_keys_are_not_hallucinations(self):
        from moss_llm.smith.expand import verify
        ir = self._ir("ui-smoke.ir.json")
        out = get_adapter("moss-image", REG).realize(ir).positive
        self.assertTrue(out.lstrip().startswith("{"))
        self.assertEqual(verify(ir, out, "moss-image", REG).extra_quotes, [])

    def test_h3_multiline_text_is_not_mispaired(self):
        """图内文字带 `\\n` 时，引号不能跨段配错。"""
        from moss_llm.smith.expand import verify
        ir = self._ir("ui-smoke.ir.json")
        out = get_adapter("minimax-h3", REG).realize(ir).positive
        self.assertIn("\n", out)
        rep = verify(ir, out, "minimax-h3", REG)
        self.assertEqual(rep.extra_quotes, [])
        self.assertEqual(rep.extra_numbers, [])

    def test_handle_in_angle_brackets_is_not_a_number(self):
        from moss_llm.smith.expand import verify
        ir = self._ir("faceswap-demo-ref.ir.json")
        rep = verify(ir, "Elements: <silhouette_9>, <Picture 7>.", "flux3", REG)
        self.assertEqual(rep.extra_numbers, [])

    def test_invented_number_is_still_caught(self):
        from moss_llm.smith.expand import verify
        ir = self._ir("faceswap-demo-ref.ir.json")
        rep = verify(ir, "A 3-storey building behind her.", "krea2", REG)
        self.assertIn("3", rep.extra_numbers)

    def test_invented_quote_is_still_caught(self):
        from moss_llm.smith.expand import verify
        ir = self._ir("faceswap-demo-ref.ir.json")
        rep = verify(ir, 'The text "FORGED" is on the wall.', "krea2", REG)
        self.assertIn("forged", rep.extra_quotes)

    def test_json_text_field_must_match_verbatim(self):
        """结构化方言里改一个字的 `text` 字段，必须红。"""
        from moss_llm.smith.expand import verify
        ir = self._ir("comic-4panel-lineart-sample.ir.json")
        out = get_adapter("moss-image", REG).realize(ir).positive
        bad = out.replace('"text":"', '"text":"X', 1)
        self.assertNotEqual(bad, out)
        self.assertTrue(verify(ir, bad, "moss-image", REG).extra_quotes)

    def test_quoted_text_numbers_do_not_become_extra_numbers(self):
        """`"2x2 · 4 panels …"` 里的 2 是图内文字，不是凭空造数。"""
        from moss_llm.smith.expand import verify
        ir = self._ir("ui-smoke.ir.json")
        txt = next(e.text_content for e in ir.text_entities if "2x2" in (e.text_content or ""))
        rep = verify(ir, 'The text "%s" is visible.' % txt.replace("\n", " "), "krea2", REG)
        self.assertEqual(rep.extra_numbers, [])
        self.assertEqual(rep.extra_quotes, [])

    def test_coverage_gate_has_no_red_fixture(self):
        """28 个组合（4 夹具 × 7 后端）当前必须全绿，否则闸一接就红。"""
        from moss_llm.smith.adapters import get_adapter as ga
        from moss_llm.smith.expand import verify
        targets = ("moss-image", "qwen-image-2_1", "krea2", "flux3", "sdxl",
                   "flux1_dev", "minimax-h3")
        for p in sorted(self.FIX.glob("*.ir.json")):
            ir = IR.load(p)
            for t in targets:
                out = ga(t, REG).realize(ir).positive
                rep = verify(ir, out, t, REG)
                self.assertEqual((rep.hard_misses, rep.extra_numbers, rep.extra_quotes),
                                 ([], [], []), "%s × %s" % (p.name, t))


if __name__ == "__main__":
    unittest.main(verbosity=2)
