# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/g7_binding.py` 的离线单测（零模型、零网络、零 GPU）。

跑：
    python tests/test_g7_binding.py
    python -m unittest tests.test_g7_binding -v

覆盖四类容易悄悄变宽松的东西：
1. 定位层：caption 的重建必须与 `render_prose` 逐字节相同，否则判据算的就不是落盘文本；
2. 正例：每一类都能在「真绑对了」的合成样本上判 bound；
3. 反例：记了但没渲染出来 / 记在全局 notes 里 / 方位与 bbox 冲突 / 文字没进引号，
   必须判 unbound，不许因为「找不到」就放过；
4. 分母纪律：空 IR / 无适用实体时覆盖率是 n/a，不是 1.0（记忆总账 §9.7 的坑）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.clauses import build_clauses, render_prose   # noqa: E402
from moss_llm.smith.ir import IR                                  # noqa: E402

from tools.g7_binding import (COLOR_WORDS, DISTANCE_N, MATERIAL_WORDS,  # noqa: E402
                              _distance_ok, _tokens_in, aggregate, format_table,
                              measure_record, render_with_spans)

META = {"id": "imgTEST", "category": "单人特写"}


def make_ir(entities: list[dict], canvas: str = "single", notes: dict | None = None) -> IR:
    return IR.from_dict({
        "task": "t2i",
        "canvas": {"aspect_ratio": "1:1", "canvas_type": canvas},
        "entities": entities,
        "notes": notes or {},
    })


def payload_of(ir: IR, caption: str | None = None, ok: bool = True,
               model: str = "A-4B") -> dict:
    text = render_prose(build_clauses(ir)) if caption is None else caption
    return {"model_key": model, "ok": ok, "error": "" if ok else "boom",
            "ir": ir.to_dict(), "caption": text}


def measure(ir: IR, caption: str | None = None, model: str = "A-4B") -> dict:
    return measure_record(payload_of(ir, caption, model=model), META)


def cls_of(rec: dict, name: str) -> dict:
    return rec["classes"][name]


CHAR = {"id": "e1", "kind": "character", "label": "woman", "importance": 0.9,
        "age_years": 28,
        "descriptors": {"gender": "woman", "hair": "black, shoulder-length",
                        "wardrobe": ["yellow raincoat"]},
        "spatial": {"anchor": "left foreground"}}


class TestLocator(unittest.TestCase):
    def test_render_with_spans_matches_render_prose(self):
        """定位层必须与真实渲染逐字节一致——否则判据量的不是用户看到的文本。"""
        ir = make_ir([CHAR,
                      {"id": "e2", "kind": "object", "label": "cup", "importance": 0.5,
                       "descriptors": {"material": "glazed ceramic"},
                       "spatial": {"anchor": "bottom-right"}},
                      {"id": "t1", "kind": "text", "label": "headline", "importance": 0.9,
                       "text_content": "开门", "spatial": {"anchor": "top-center"}}],
                     notes={"camera": "Eye level, shallow focus."})
        clauses = build_clauses(ir)
        text, spans = render_with_spans(clauses)
        self.assertEqual(text, render_prose(clauses))
        self.assertEqual(len(spans), len(clauses))
        prev_end = -1
        for clause, start, end in spans:
            self.assertLessEqual(start, end)
            self.assertGreaterEqual(start, prev_end)          # 顺序且不重叠
            self.assertIn(clause.text.lower().split()[0], text[start:end].lower())
            prev_end = end
        self.assertLessEqual(prev_end, len(text))

    def test_render_mismatch_is_flagged_and_not_silently_reused(self):
        """落盘 caption 被改过时：要出声，且判据按重建文本执行（不许静默降级）。"""
        ir = make_ir([CHAR])
        rec = measure(ir, caption="Totally unrelated prose about nothing.")
        self.assertTrue(any("不一致" in w for w in rec["warnings"]))
        self.assertEqual(cls_of(rec, "color")["applicable"], 1)
        self.assertEqual(cls_of(rec, "color")["bound"], 0)

    def test_saved_entity_order_is_preserved(self):
        """落盘实体顺序 ≠ normalize 顺序时，重建 caption 必须跟着落盘顺序走。

        文字专线是在 normalize 之后追加实体的（不再重排），落盘 caption 因此带着
        未排序顺序；若判据先 normalize 再重建，子句顺序会变、定位层错位。
        """
        from moss_llm.smith.ir import Entity
        hi = {"id": "e1", "kind": "character", "label": "woman", "importance": 0.9,
              "age_years": 28, "descriptors": {"gender": "woman",
                                               "wardrobe": ["red coat"]},
              "spatial": {"anchor": "left foreground"}}
        lo = {"id": "t9", "kind": "text", "label": "sign", "importance": 0.2,
              "text_content": "开门", "spatial": {"anchor": "top-right"}}
        ir = make_ir([hi, lo])                     # normalize 后 hi 在前
        d = ir.to_dict()
        d["entities"] = list(reversed(d["entities"]))   # 模拟落盘顺序 t9, e1
        ir2 = IR.from_dict(d)
        ir2.entities = [Entity.from_dict(e) for e in d["entities"]]
        caption = render_prose(build_clauses(ir2))
        rec = measure_record({"model_key": "A-4B", "ok": True, "ir": d,
                              "caption": caption}, META)
        self.assertEqual(rec["warnings"], [])
        self.assertEqual(cls_of(rec, "text")["bound"], 1)
        self.assertEqual(cls_of(rec, "color")["bound"], 1)


class TestColor(unittest.TestCase):
    def test_color_bound_positive(self):
        rec = measure(make_ir([CHAR]))
        box = cls_of(rec, "color")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))
        self.assertIn("yellow", box["instances"][0]["expected"])

    def test_color_recorded_but_not_rendered_is_unbound(self):
        """tags 字段不会被 describe_entity 渲染 ⇒ 记了也没绑上，必须算失败。"""
        ent = dict(CHAR, descriptors={"gender": "woman", "tags": ["red"]})
        rec = measure(make_ir([ent]))
        box = cls_of(rec, "color")
        self.assertEqual((box["applicable"], box["bound"]), (1, 0))
        self.assertIn("red", box["instances"][0]["missing"])

    def test_color_only_in_global_notes_is_not_applicable(self):
        """全局 lighting/notes 里的颜色不是「绑到实体」的证据，不进分母。"""
        ent = {"id": "e1", "kind": "object", "label": "wall", "importance": 0.5,
               "spatial": {"anchor": "center"}}
        rec = measure(make_ir([ent], notes={"lighting": "warm red light spills"}))
        self.assertEqual(cls_of(rec, "color")["applicable"], 0)
        self.assertIsNone(cls_of(rec, "color")["coverage"])

    def test_chinese_color_token(self):
        self.assertIn("红", _tokens_in("红色雨衣", COLOR_WORDS))
        ent = {"id": "e1", "kind": "object", "label": "雨衣", "importance": 0.5,
               "descriptors": {"color": "红色"}, "spatial": {"anchor": "center"}}
        box = cls_of(measure(make_ir([ent])), "color")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))

    def test_hyphenated_color_is_split_into_parts(self):
        """`white-gold` 要拆成 white / gold 两个词——整词匹配会静默漏检（B · img14 实测）。"""
        toks = _tokens_in("white-gold", COLOR_WORDS)
        self.assertIn("white", toks)
        self.assertIn("gold", toks)
        ent = {"id": "t1", "kind": "text", "label": "particle text",
               "importance": 0.9, "text_content": "大爆炸",
               "typography": {"color": "white-gold"},
               "spatial": {"anchor": "center-right"}}
        box = cls_of(measure(make_ir([ent])), "color")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))

    def test_multichar_cjk_color_word_matches(self):
        """多字中文词靠子串匹配（逐词相等永远命不中 `琥珀` 这类词）。"""
        toks = _tokens_in("琥珀色玻璃瓶", COLOR_WORDS)
        self.assertIn("琥珀", toks)


class TestMaterial(unittest.TestCase):
    def test_material_bound_positive(self):
        ent = {"id": "e1", "kind": "object", "label": "kettle", "importance": 0.6,
               "descriptors": {"material": "brushed aluminium"},
               "spatial": {"anchor": "center"}}
        box = cls_of(measure(make_ir([ent])), "material")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))

    def test_multichar_cjk_material_token_binds(self):
        """中文多字材质词（`不锈钢`）要能在成品子句里被定位到，否则中文标的模型全判失败。"""
        self.assertIn("不锈钢", _tokens_in("磨砂不锈钢外壳", MATERIAL_WORDS))
        ent = {"id": "e1", "kind": "object", "label": "sink", "importance": 0.6,
               "descriptors": {"material": "不锈钢"}, "spatial": {"anchor": "center"}}
        box = cls_of(measure(make_ir([ent])), "material")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))

    def test_skin_words_do_not_make_material_applicable(self):
        """人物皮肤写 smooth/glossy 不算材质绑定——否则材质类退化成人人有分。"""
        ent = dict(CHAR, descriptors={"gender": "woman", "skin": "pale, smooth, glossy"})
        box = cls_of(measure(make_ir([ent])), "material")
        self.assertEqual(box["applicable"], 0)

    def test_material_unbound_when_not_rendered(self):
        """人物分支不渲染 descriptors.material（只有非人物才拼 "with …"）⇒ 记了也算没绑。"""
        ent = {"id": "e1", "kind": "character", "label": "man", "importance": 0.6,
               "age_years": 30,
               "descriptors": {"gender": "man", "material": "leather"}}
        box = cls_of(measure(make_ir([ent])), "material")
        self.assertEqual((box["applicable"], box["bound"]), (1, 0))


class TestSpatial(unittest.TestCase):
    def test_anchor_bound_positive(self):
        box = cls_of(measure(make_ir([CHAR])), "spatial")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))

    def test_model_bbox_conflicting_with_anchor_is_unbound(self):
        """bbox 与方位来自模型的两个通道；互相对不上就是没绑对（不能只看散文）。"""
        ent = dict(CHAR, spatial={"anchor": "left foreground",
                                  "bbox_norm": [0.62, 0.2, 0.82, 0.6],
                                  "bbox_source": "model"})
        box = cls_of(measure(make_ir([ent])), "spatial")
        self.assertEqual((box["applicable"], box["bound"]), (1, 0))
        self.assertTrue(box["instances"][0]["channel_conflict"])

    def test_anchor_derived_bbox_is_not_self_checked(self):
        """bbox 本来就是按方位重建的，再拿它证方位等于自己证自己：不判冲突。"""
        ent = dict(CHAR, spatial={"anchor": "left foreground",
                                  "bbox_norm": [0.62, 0.2, 0.82, 0.6],
                                  "bbox_source": "anchor"})
        box = cls_of(measure(make_ir([ent])), "spatial")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))
        self.assertIsNone(box["instances"][0]["channel_conflict"])

    def test_unknown_direction_degrades_to_phrase_presence(self):
        ent = dict(CHAR, spatial={"anchor": "mid-air"})
        box = cls_of(measure(make_ir([ent])), "spatial")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))


class TestText(unittest.TestCase):
    def test_text_verbatim_quoted_positive(self):
        ent = {"id": "t1", "kind": "text", "label": "headline", "importance": 0.9,
               "text_content": "开门", "spatial": {"anchor": "top-center"}}
        box = cls_of(measure(make_ir([ent])), "text")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))

    def test_multiline_text_all_lines_quoted(self):
        ent = {"id": "t1", "kind": "text", "label": "sign", "importance": 0.9,
               "text_content": "第一行\n第二行", "spatial": {"anchor": "center"}}
        box = cls_of(measure(make_ir([ent])), "text")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))

    def test_text_not_in_quotes_is_unbound(self):
        ent = {"id": "t1", "kind": "text", "label": "headline", "importance": 0.9,
               "text_content": "开门", "spatial": {"anchor": "top-center"}}
        rec = measure(make_ir([ent]), caption='The text 开门 appears unquoted.')
        box = cls_of(rec, "text")
        self.assertEqual((box["applicable"], box["bound"]), (1, 0))
        self.assertIn("开门", box["instances"][0]["missing"])

    def test_non_text_entity_with_text_content_is_not_text_class(self):
        """character/object 塞了 text_content 是类型错误，不算「图内文字」的分母。

        判据文档写的是 kind=text；实测 A 有 2 个 character 带 text_content，
        放开条件会让分母 26→28 并混进「人物描述没进引号」这种噪声。
        """
        ent = {"id": "e1", "kind": "character", "label": "coffee grinder",
               "importance": 0.6, "age_years": 30,
               "text_content": "The brand name is illegible.",
               "spatial": {"anchor": "center"}}
        box = cls_of(measure(make_ir([ent])), "text")
        self.assertEqual(box["applicable"], 0)
        self.assertIsNone(box["coverage"])


class TestCount(unittest.TestCase):
    @staticmethod
    def _twin(anchor2: str = "right foreground") -> IR:
        a = {"id": "e1", "kind": "character", "label": "woman", "importance": 0.9,
             "age_years": 28, "descriptors": {"gender": "woman"},
             "spatial": {"anchor": "left foreground"}}
        b = {"id": "e2", "kind": "character", "label": "woman", "importance": 0.9,
             "age_years": 28, "descriptors": {"gender": "woman"},
             "spatial": {"anchor": anchor2}}
        return make_ir([a, b])

    def test_two_identical_expressed_by_appears_again(self):
        box = cls_of(measure(self._twin()), "count")
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))
        self.assertEqual(box["instances"][0]["n"], 2)

    def test_three_identical_not_bound_by_appears_again(self):
        ir = self._twin()
        ir.entities.append(ir.entities[0].__class__.from_dict({
            "id": "e3", "kind": "character", "label": "woman", "importance": 0.9,
            "age_years": 28, "descriptors": {"gender": "woman"},
            "spatial": {"anchor": "center"}}))
        box = cls_of(measure(ir), "count")
        self.assertEqual((box["applicable"], box["bound"]), (1, 0))

    def test_multi_panel_repeats_are_not_counted(self):
        box = cls_of(measure(self._twin(), model="A-4B"), "count")
        self.assertEqual(box["applicable"], 1)
        rec = measure_record(payload_of(make_ir([
            {"id": "e1", "kind": "character", "label": "woman", "importance": 0.9,
             "age_years": 28, "descriptors": {"gender": "woman"},
             "spatial": {"anchor": "left foreground"}},
            {"id": "e2", "kind": "character", "label": "woman", "importance": 0.9,
             "age_years": 28, "descriptors": {"gender": "woman"},
             "spatial": {"anchor": "right foreground"}},
        ], canvas="multi_panel")), META)
        self.assertEqual(rec["classes"]["count"]["applicable"], 0)
        self.assertTrue(rec["classes"]["count"]["skipped"])

    def test_single_entity_is_not_applicable(self):
        box = cls_of(measure(make_ir([CHAR])), "count")
        self.assertEqual(box["applicable"], 0)
        self.assertIsNone(box["coverage"])


class TestDistanceWindow(unittest.TestCase):
    def test_token_beyond_window_is_unbound(self):
        caption = "a woman " + " ".join(["xx"] * (DISTANCE_N + 20)) + " red"
        ok, dist = _distance_ok(caption, (0, len(caption)), "red")
        self.assertFalse(ok)
        self.assertGreater(dist, DISTANCE_N)

    def test_hyphenated_token_matches_its_parts(self):
        """方位写成 "top-left" 时，方向词 top / left 都要算命中（分词器不拆连字符）。"""
        caption = "a woman in the top-left"
        self.assertTrue(_distance_ok(caption, (0, len(caption)), "top")[0])
        self.assertTrue(_distance_ok(caption, (0, len(caption)), "left")[0])

    def test_token_inside_window_is_bound(self):
        caption = "a woman in a red coat"
        ok, dist = _distance_ok(caption, (0, len(caption)), "red")
        self.assertTrue(ok)
        self.assertLessEqual(dist, DISTANCE_N)


class TestTextCrossPass(unittest.TestCase):
    @staticmethod
    def _record(evidence_text: str, block_text: str) -> dict:
        import json as _json
        ent = {"id": "t1", "kind": "text", "label": "headline", "importance": 0.9,
               "text_content": evidence_text, "spatial": {"anchor": "center"}}
        ir = make_ir([ent])
        payload = payload_of(ir)
        payload["calls"] = [
            {"name": "scene_record", "content": "{}"},
            {"name": "text_blocks",
             "content": _json.dumps({"blocks": [{"text": block_text,
                                                 "where": "center"}]},
                                    ensure_ascii=False)},
        ]
        return payload

    def test_corroborated_text_counts(self):
        rec = measure_record(self._record("开门", "开门"), META)
        cp = rec["aux"]["text_cross_pass"]
        self.assertEqual((cp["from_evidence"], cp["corroborated"]), (1, 1))

    def test_uncorroborated_text_is_visible(self):
        rec = measure_record(self._record("开门", "关门"), META)
        cp = rec["aux"]["text_cross_pass"]
        self.assertEqual((cp["from_evidence"], cp["corroborated"]), (1, 0))

    def test_transcription_only_entities_are_excluded(self):
        """转录遍补进来的文字本来就有第二遍背书，不算「取证遍被证实」的分子/分母。"""
        ent = {"id": "t1", "kind": "text", "label": "headline", "importance": 0.9,
               "text_content": "开门", "spatial": {"anchor": "center"},
               "evidence": ["transcription pass"]}
        rec = measure_record(payload_of(make_ir([ent])), META)
        cp = rec["aux"]["text_cross_pass"]
        self.assertEqual(cp["from_evidence"], 0)
        self.assertEqual(cp["transcription_only"], 1)


class TestAggregate(unittest.TestCase):
    def test_empty_ir_coverage_is_none_not_one(self):
        rec = measure(make_ir([]))
        self.assertTrue(rec["ok"])
        for name in ("color", "material", "spatial", "text", "count"):
            self.assertIsNone(cls_of(rec, name)["coverage"],
                              "%s：分母 0 时覆盖率必须是 n/a" % name)

    def test_failed_record_is_counted_not_dropped(self):
        bad = measure_record({"model_key": "B-9B", "ok": False, "error": "truncated"},
                             META)
        summary = aggregate([bad], ["B-9B"])
        self.assertEqual(summary["models"]["B-9B"]["failed"], 1)
        self.assertEqual(summary["models"]["B-9B"]["ok"], 0)

    def test_aggregate_arithmetic_and_table(self):
        ir = make_ir([CHAR])
        a = measure_record(payload_of(ir, model="A-4B"), META)
        b = measure_record(payload_of(ir, model="B-9B"), META)
        summary = aggregate([a, b], ["A-4B", "B-9B"])
        box = summary["models"]["A-4B"]["classes"]["color"]
        self.assertEqual((box["applicable"], box["bound"]), (1, 1))
        self.assertEqual(box["coverage"], 1.0)
        table = format_table(summary)
        self.assertIn("A-4B", table)
        self.assertIn("1/1", table)
        self.assertIn("category", META)          # 元数据不被改写

    def test_per_image_denominator_visible(self):
        ir = make_ir([CHAR])
        rec = measure_record(payload_of(ir), META)
        summary = aggregate([rec], ["A-4B"])
        per_image = summary["models"]["A-4B"]["classes"]["color"]["per_image"]
        self.assertIn("imgTEST", per_image)
        self.assertEqual(per_image["imgTEST"]["applicable"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
