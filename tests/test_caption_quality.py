# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 系统缺陷修复的离线判据（M1a）。

两类缺陷（第一轮 G7 实测，`docs/G7-反推质量-第一轮.md` §7）：

1. **非人物体被标成 `character`**（58/110 = 53%）：人物分支不渲染 `descriptors.material`，
   又强制年龄短语 ⇒ 材质绑定在「材质与反光」组只剩 0% / 8%，并给物体编年龄。
   修法（本文件钉住）：取证提示词约束 + `coerce_ir` 类型常识闸（降级 `object` 并出声）。
2. **JSON 模式无重复保护**：4/40 次跑批因重复循环撞 completion 上限、整单归零。
   修法（本文件钉住）：schema `maxItems` + `coerce_ir` 上限兜底 + JSON 请求保留
   `repeat_penalty`。

每条修法都配「旧行为必红」的反证：把修复前会出现的坏结果手工复现出来，断言它确实
违反判据——否则就是「写了一条永远绿的测试」。

跑：python tests/test_caption_quality.py
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.clauses import (DIM_ORDER, build_clauses, describe_entity,  # noqa: E402
                                    render_prose)
from moss_llm.smith.expand import fixed_items                                # noqa: E402
from moss_llm.smith.ir import IR                                             # noqa: E402
from moss_llm.smith.llm import DEFAULT_SAMPLING, LlamaServer                 # noqa: E402
from moss_llm.smith.reverse import (EVIDENCE_SCHEMA, MAX_ENTITIES,           # noqa: E402
                                    MAX_TEXT_BLOCKS, SYSTEM, TEXT_SCHEMA,
                                    Report, ReverseError,
                                    _gate_character_kind, _is_placeholder_text,
                                    coerce_ir)

PERSON = ("gender", "hair", "skin", "face", "expression")
OBJECT = ("material", "color", "build", "detail")


# ── 通用夹具 ─────────────────────────────────────────────────

def obs(*entities, **kw) -> dict:
    base = {"style": {"medium": "photograph"}, "entities": list(entities), "relations": []}
    base.update(kw)
    return base


def caption_of(ir: IR) -> str:
    """演示层：IR → 该后端确定性散文（与 freeze 基线同一条渲染路径）。"""
    return render_prose(build_clauses(ir), DIM_ORDER)


def mislabeled_characters(ir: IR) -> list:
    """「类型闸不变量」：character 且无人物字段、却带物体字段 = 误标。

    与 `_gate_character_kind` 同一口径；测试用它断言「旧行为必红、修后绿」。
    """
    bad = []
    for e in ir.entities:
        if e.kind != "character":
            continue
        desc = e.descriptors or {}
        people = [k for k in PERSON if str(desc.get(k) or "").strip()]
        things = [k for k in OBJECT if str(desc.get(k) or "").strip()]
        if not people and things:
            bad.append(e.id)
    return bad


GRINDER = {
    "id": "e1", "kind": "character", "label": "coffee grinder",
    "importance": 0.9, "age_years": 80,
    "descriptors": {"material": "brushed stainless steel"},
    "spatial": {"anchor": "left third"},
}
PERSON_ENTITY = {
    "id": "e1", "kind": "character", "label": "woman",
    "importance": 0.9, "age_years": 28,
    "descriptors": {"gender": "woman", "hair": "black, shoulder length",
                    "face": "round, calm expression"},
    "spatial": {"anchor": "center"},
}


# ── 缺陷 1：类型闸 ────────────────────────────────────────────

class TestCharacterKindGate(unittest.TestCase):
    def _coerce(self, *entities, **kw):
        rep = Report()
        ir = coerce_ir(obs(*entities, **kw), rep)
        return ir, rep

    def test_mislabeled_object_is_downgraded_and_agedropped(self):
        """没有人物字段、带 material 的 character → object；年龄一并丢弃并出声。"""
        ir, rep = self._coerce(GRINDER)
        e = ir.entities[0]
        self.assertEqual(e.kind, "object")
        self.assertIsNone(e.age_years)                       # 不再编「80 岁」
        self.assertEqual(len(rep.kind_downgrades), 1)
        self.assertTrue(any("character" in d and "object" in d for d in rep.kind_downgrades))
        self.assertEqual(len(rep.age_dropped), 1)
        self.assertTrue(any("年龄" in w and "80" in w for w in rep.warnings))
        # report 要能被节点/序列化层读走（新键向后兼容，旧键不动）
        d = rep.to_dict()
        self.assertIn("kind_downgrades", d)
        self.assertIn("age_dropped", d)
        self.assertEqual(d["kind_downgrades"], rep.kind_downgrades)

    def test_legit_person_is_untouched(self):
        ir, rep = self._coerce(PERSON_ENTITY)
        e = ir.entities[0]
        self.assertEqual(e.kind, "character")
        self.assertEqual(e.age_years, 28)
        self.assertEqual(rep.kind_downgrades, [])
        self.assertEqual(rep.age_dropped, [])

    def test_character_with_no_person_and_no_object_fields_is_kept(self):
        """判据只用字段、不猜语义：没有可判信号就不动，照旧走年龄闸。"""
        ir, rep = self._coerce({"id": "e1", "kind": "character", "label": "girl"})
        self.assertEqual(ir.entities[0].kind, "character")
        self.assertEqual(rep.kind_downgrades, [])
        self.assertEqual(len(rep.age_gate), 1)               # 原年龄闸仍出声

    def test_person_like_character_with_material_is_kept(self):
        """有任一人物字段就保留 character——这是 c 方案未做的已知残余（如铜鹰雕像）。"""
        ir, rep = self._coerce({
            "id": "e1", "kind": "character", "label": "bronze eagle statue",
            "age_years": 50,
            "descriptors": {"face": "beak and brow", "material": "bronze with patina"},
        })
        self.assertEqual(ir.entities[0].kind, "character")   # 不降级
        self.assertEqual(rep.kind_downgrades, [])
        self.assertEqual(ir.entities[0].age_years, 50)

    def test_object_with_age_is_dropped_and_reported(self):
        ir, rep = self._coerce({
            "id": "e1", "kind": "object", "label": "ceramic cup",
            "age_years": 80, "descriptors": {"material": "glaze"},
        })
        self.assertEqual(ir.entities[0].kind, "object")
        self.assertIsNone(ir.entities[0].age_years)
        self.assertEqual(len(rep.age_dropped), 1)

    def test_gate_helper_is_pure_and_direction_explicit(self):
        self.assertEqual(_gate_character_kind("object", {"material": "x"})[0], "object")
        self.assertEqual(_gate_character_kind("character", {"gender": "woman"})[0],
                         "character")
        kind, reason = _gate_character_kind("character", {"material": "glaze"})
        self.assertEqual(kind, "object")
        self.assertIn("material", reason)

    def test_downgraded_object_renders_material_and_no_age_phrase(self):
        """修复的直接收益：材质绑进实体自己的子句（binding 判据数得到），年龄短语消失。"""
        ir, _rep = self._coerce(GRINDER)
        text = caption_of(ir)
        self.assertIn("brushed stainless steel", text)       # 材质渲染得出来
        self.assertNotIn("80-year-old", text)                # 不再编年龄
        # 关键差异：材质必须在**实体自己的描述**里（旧行为是游离的 "Textures:" 子句，
        # G7 binding 只认同一子句，所以旧行为材质类 = 0%）。
        self.assertIn("brushed stainless steel", describe_entity(ir.entities[0]))
        self.assertNotIn("textures:", text.lower())          # 不再重复成游离子句
        # 固定项账本里也不再出现「物体年龄」这条永远对不上的事实
        kinds = [f.kind for f in fixed_items(ir)]
        self.assertNotIn("age", kinds)

    def test_old_behavior_would_be_red(self):
        """反证：修复前的形态（character + material + age）必然违反三条判据。"""
        legacy = IR.from_dict({
            "task": "t2i", "canvas": {"aspect_ratio": "1:1"},
            "entities": [GRINDER],
        })
        # 旧行为必红 ①：类型闸不变量
        self.assertEqual(mislabeled_characters(legacy), ["e1"])
        legacy_text = caption_of(legacy)
        # 旧行为必红 ②：物体被编年龄
        self.assertIn("80-year-old", legacy_text)
        # 旧行为必红 ③：材质不在实体自己的子句里（而是游离的 textures 子句）——
        # G7 binding 按「实体自己的子句」数 → 这正是材质类 0% 的机制。
        self.assertNotIn("brushed stainless steel", describe_entity(legacy.entities[0]))
        self.assertIn("textures: brushed stainless steel", legacy_text.lower())

        fixed_ir, _rep = self._coerce(GRINDER)
        self.assertEqual(mislabeled_characters(fixed_ir), [])
        fixed_text = caption_of(fixed_ir)
        self.assertNotIn("80-year-old", fixed_text)
        self.assertIn("brushed stainless steel", describe_entity(fixed_ir.entities[0]))
        self.assertIn("brushed stainless steel", fixed_text)


# ── 缺陷 1 配套：schema / prompt 约束 ────────────────────────

class TestEvidenceSchemaCaps(unittest.TestCase):
    def test_entities_have_max_items(self):
        ent = EVIDENCE_SCHEMA["properties"]["entities"]
        self.assertEqual(ent.get("maxItems"), MAX_ENTITIES)
        self.assertGreater(MAX_ENTITIES, 0)
        self.assertLessEqual(MAX_ENTITIES, 100)              # 上限本身要合理

    def test_text_blocks_have_max_items(self):
        blocks = TEXT_SCHEMA["properties"]["blocks"]
        self.assertEqual(blocks.get("maxItems"), MAX_TEXT_BLOCKS)

    def test_schema_still_serializable_and_contract_unchanged(self):
        json.dumps(EVIDENCE_SCHEMA)                          # 语法约束本身仍是合法 JSON
        self.assertEqual(set(EVIDENCE_SCHEMA["required"]),
                         {"canvas", "style", "entities"})    # 只 required 三字段，不逼供
        kinds = (EVIDENCE_SCHEMA["properties"]["entities"]["items"]
                 ["properties"]["kind"]["enum"])
        self.assertEqual(kinds, ["character", "object", "text", "background"])

    def test_old_schema_would_be_red(self):
        """反证：没有 maxItems 的旧 schema 必然违反「重复循环有上限」这条判据。"""
        def entities_capped(schema):
            return isinstance(schema["properties"]["entities"].get("maxItems"), int)

        legacy = copy.deepcopy(EVIDENCE_SCHEMA)
        legacy["properties"]["entities"].pop("maxItems", None)
        self.assertFalse(entities_capped(legacy))
        self.assertTrue(entities_capped(EVIDENCE_SCHEMA))

    def test_prompt_carries_the_type_and_repeat_constraints(self):
        """schema 只锁形状；内容约束必须在 prompt 里逐条可查。"""
        self.assertIn("reserved for a person", SYSTEM)
        self.assertIn("Never give age_years", SYSTEM)
        self.assertIn("Every entity label must be unique", SYSTEM)
        self.assertIn("STOP repeating", SYSTEM)
        self.assertIn("must not exceed %d items" % MAX_ENTITIES, SYSTEM)
        self.assertIn("Output the JSON object only", SYSTEM)


class TestEntityCountCap(unittest.TestCase):
    def test_over_limit_unique_entities_are_truncated_not_failed(self):
        """49 个**唯一** label（>上限）→ 仍按截断处理（唯一实体是合法路径）。"""
        entities = [{"id": "e%d" % i, "kind": "object", "label": "object %d" % i}
                    for i in range(1, 50)]                       # 49 条唯一 label
        rep = Report()
        ir = coerce_ir(obs(*entities), rep)
        self.assertEqual(len(ir.entities), MAX_ENTITIES)
        self.assertTrue(rep.capped)                              # 超过上限也必须 capped
        self.assertEqual(rep.capped_at, 49)                      # >上限时记原始条数
        self.assertTrue(any("超过上限" in d for d in rep.dropped))

    def test_label_repetition_loop_at_limit_fails_loudly(self):
        """40 条 / 8 个 label 的重复循环 → 显式 ReverseError，不返回 IR、不静默截断。"""
        labels = ["cup", "window", "lamp", "table", "chair", "wall", "door", "rug"]
        entities = [{"id": "e%d" % i, "kind": "object",
                     "label": labels[i % len(labels)]} for i in range(MAX_ENTITIES)]
        rep = Report()
        with self.assertRaises(ReverseError) as ctx:
            coerce_ir(obs(*entities), rep)
        msg = str(ctx.exception)
        self.assertIn("疑似重复循环", msg)
        self.assertIn("不同 label", msg)
        self.assertIn("已按失败处理", msg)
        self.assertIn("建议重试", msg)
        self.assertIsInstance(ctx.exception.report, Report)
        self.assertTrue(ctx.exception.report.capped)
        self.assertTrue(any("疑似重复循环" in w
                            for w in ctx.exception.report.warnings))

    def test_label_normalization_catches_case_and_space_repeats(self):
        """label 归一化（strip + casefold）：'Cup' / 'cup ' / 'CUP' 视为同一个 label。"""
        labels = ["Cup", "cup ", "CUP", "Window", "window ", "WINDOW",
                  "Lamp", "lamp ", "LAMP", "Table"]
        entities = [{"id": "e%d" % i, "kind": "object",
                     "label": labels[i % len(labels)]} for i in range(MAX_ENTITIES)]
        with self.assertRaises(ReverseError):
            coerce_ir(obs(*entities), Report())

    def test_light_duplicates_at_limit_are_merged_with_warning(self):
        """40 条里仅 1 条重复（5%）→ 合并保留第一条 + warning，不按循环失败。"""
        entities = [{"id": "e%d" % i, "kind": "object", "label": "object %d" % i}
                    for i in range(1, MAX_ENTITIES)]              # 39 个唯一 label
        entities.append({"id": "e40", "kind": "object", "label": "object 1"})
        rep = Report()
        ir = coerce_ir(obs(*entities), rep)                       # 不应抛
        self.assertEqual(len(ir.entities), MAX_ENTITIES - 1)      # 合并后 39
        self.assertTrue(rep.capped)                               # 原始 40 触达上限
        self.assertEqual(rep.capped_at, MAX_ENTITIES)
        self.assertTrue(any("合并 1 条重复实体" in w and "object 1" in w
                            for w in rep.warnings))

    def test_light_duplicates_below_limit_are_merged_with_warning(self):
        """未达上限的轻度重复也要合并出声（'cup'/'cup '/'CUP' → 1 条）。"""
        entities = [{"id": "e1", "kind": "object", "label": "cup"},
                    {"id": "e2", "kind": "object", "label": "cup "},
                    {"id": "e3", "kind": "object", "label": "CUP"}]
        rep = Report()
        ir = coerce_ir(obs(*entities), rep)
        self.assertEqual(len(ir.entities), 1)
        self.assertFalse(rep.capped)
        self.assertTrue(any("合并 2 条重复实体" in w for w in rep.warnings))

    def test_entity_count_over_limit_records_original_count(self):
        """41（>40 边界）：capped=True、capped_at=原始 41、IR 截到 40 并报丢弃。"""
        entities = [{"id": "e%d" % i, "kind": "object", "label": "object %d" % i}
                    for i in range(1, MAX_ENTITIES + 2)]         # 41 条
        rep = Report()
        ir = coerce_ir(obs(*entities), rep)
        self.assertEqual(len(ir.entities), MAX_ENTITIES)
        self.assertTrue(rep.capped)
        self.assertEqual(rep.capped_at, MAX_ENTITIES + 1)
        self.assertTrue(any("超过上限" in d for d in rep.dropped))
        self.assertTrue(rep.to_dict()["capped"])

    def test_entity_count_at_limit_is_flagged_as_possible_truncation(self):
        """恰好 40 个（=上限）也要出声：可能是 json_schema 在语法层静默截断。

        不能断言一定错了（恰好 40 个合法实体也可能），所以只能报「疑似截断，请人工确认」。
        """
        entities = [{"id": "e%d" % i, "kind": "object", "label": "object %d" % i}
                    for i in range(1, MAX_ENTITIES + 1)]
        rep = Report()
        ir = coerce_ir(obs(*entities), rep)
        self.assertEqual(len(ir.entities), MAX_ENTITIES)
        self.assertTrue(rep.capped)
        self.assertEqual(rep.capped_at, MAX_ENTITIES)
        self.assertTrue(any("疑似截断" in w and "请人工确认" in w for w in rep.warnings))
        self.assertEqual(rep.dropped, [])                        # 没有发生真截断
        d = rep.to_dict()
        self.assertTrue(d["capped"])
        self.assertEqual(d["capped_at"], MAX_ENTITIES)

    def test_entity_count_below_limit_is_not_flagged(self):
        """39 个 < 上限：不应误报 capped（否则告警会变成噪音）。"""
        entities = [{"id": "e%d" % i, "kind": "object", "label": "object %d" % i}
                    for i in range(1, MAX_ENTITIES)]             # 39 个
        rep = Report()
        ir = coerce_ir(obs(*entities), rep)
        self.assertEqual(len(ir.entities), MAX_ENTITIES - 1)
        self.assertFalse(rep.capped)
        self.assertIsNone(rep.capped_at)
        self.assertFalse(any("疑似截断" in w for w in rep.warnings))
        self.assertEqual(rep.dropped, [])

    def test_small_record_is_untouched(self):
        rep = Report()
        ir = coerce_ir(obs(
            {"id": "e1", "kind": "object", "label": "cup"},
            {"id": "e2", "kind": "object", "label": "saucer"},
        ), rep)
        self.assertEqual(len(ir.entities), 2)
        self.assertFalse(any("超过上限" in d for d in rep.dropped))

    def test_old_behavior_would_be_red(self):
        """反证：修复前 60 个重复实体会全进 IR；现在这种循环必须直接失败（不再静默截断）。"""
        entities = [{"id": "e%d" % i, "kind": "object", "label": "tent fabric"}
                    for i in range(60)]
        raw_ir = IR.from_dict({"entities": entities})
        self.assertGreater(len(raw_ir.entities), MAX_ENTITIES)   # 旧行为（未截断）
        with self.assertRaises(ReverseError):
            coerce_ir(obs(*entities), Report())                  # 修后：显式失败


# ── 缺陷 2：JSON 重复保护 ────────────────────────────────────

class TestJSONRepetitionProtection(unittest.TestCase):
    def _srv(self, **kw):
        return LlamaServer(model="x.gguf", **kw)

    @staticmethod
    def json_repeat_safe(body: dict) -> list:
        """「JSON 重复保护」不变量：返回违反项（空 = 安全）。

        这是在 G7 失败模式上写死的判据：JSON 模式下
        ① 必须有 repeat_penalty 且 >1；② 不许带按段判重的 DRY 家族；
        ③ presence/frequency 必须归零（键名会合法重复，惩罚键名 = 惩罚正确输出）。
        """
        bad = []
        rp = body.get("repeat_penalty")
        if not isinstance(rp, (int, float)) or rp <= 1.0:
            bad.append("repeat_penalty=%r" % (rp,))
        for k in body:
            if k.startswith("dry_"):
                bad.append("dry-family:%s" % k)
        if body.get("presence_penalty", 0) != 0:
            bad.append("presence_penalty=%r" % body.get("presence_penalty"))
        if body.get("frequency_penalty", 0) != 0:
            bad.append("frequency_penalty=%r" % body.get("frequency_penalty"))
        return bad

    def test_json_body_keeps_repeat_penalty(self):
        body = self._srv().build_body([], 0.2, 800, json_mode=True)
        self.assertEqual(body.get("repeat_penalty"), DEFAULT_SAMPLING["repeat_penalty"])
        self.assertEqual(self.json_repeat_safe(body), [])

    def test_json_body_keeps_custom_repeat_penalty(self):
        body = self._srv(sampling={"repeat_penalty": 1.3}).build_body(
            [], 0.2, 800, json_mode=True)
        self.assertEqual(body["repeat_penalty"], 1.3)

    def test_json_body_adds_default_when_custom_sampling_omits_it(self):
        srv = self._srv(sampling={})
        json_body = srv.build_body([], 0.2, 800, json_mode=True)
        self.assertEqual(json_body["repeat_penalty"],
                         DEFAULT_SAMPLING["repeat_penalty"])
        prose_body = srv.build_body([], 0.7, 500)
        self.assertNotIn("repeat_penalty", prose_body)       # 非 JSON 不额外注入

    def test_old_behavior_would_be_red(self):
        """反证：把 JSON 请求里的 repeat_penalty 摘掉（修复前的形态）→ 判据必红。"""
        body = self._srv().build_body([], 0.2, 800, json_mode=True)
        self.assertEqual(self.json_repeat_safe(body), [])
        legacy = dict(body)
        legacy.pop("repeat_penalty", None)
        self.assertNotEqual(self.json_repeat_safe(legacy), [])
        legacy2 = dict(body)
        legacy2["dry_multiplier"] = 0.8                      # 旧纪律：整族 DRY 被带回来
        self.assertNotEqual(self.json_repeat_safe(legacy2), [])

    def test_non_json_sampling_discipline_unchanged(self):
        body = self._srv().build_body([], 0.7, 500)
        self.assertIn("dry_multiplier", body)                # 散文仍用 DRY 压复述
        self.assertGreater(body["dry_multiplier"], 0)
        self.assertNotIn("presence_penalty", body)           # 非 JSON 不强行归零

    def test_thinking_and_format_ladder_not_regressed(self):
        srv = self._srv()
        body = srv.build_body([], 0.2, 800, json_mode=True,
                              response_format={"type": "json_object"})
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        ladder = LlamaServer._formats({"type": "object", "properties": {}}, "x")
        self.assertEqual(len(ladder), 4)
        self.assertEqual(ladder[0]["type"], "json_schema")
        self.assertIsNone(ladder[-1])                        # 仍有「无约束」末档


class TestPlaceholderText(unittest.TestCase):
    """A1：占位符式 text_content 不能当图内文字静默传下去。"""

    def test_placeholder_text_entity_is_dropped_and_reported(self):
        rep = Report()
        ir = coerce_ir(obs({"id": "t1", "kind": "text", "label": "sign",
                            "text_content": "the string exactly as printed"}), rep)
        self.assertEqual(ir.entities, [])
        self.assertEqual(rep.placeholder_texts, ["the string exactly as printed"])
        self.assertTrue(any("占位符" in w and "the string exactly as printed" in w
                            and "sign" in w for w in rep.warnings))
        self.assertEqual(rep.to_dict()["placeholder_texts"], rep.placeholder_texts)

    def test_placeholder_substring_in_real_text_is_not_killed(self):
        """包含匹配会误杀这块正常招牌：整串不等值 ⇒ 保留。"""
        text = "The string exactly as printed on the label"
        rep = Report()
        ir = coerce_ir(obs({"id": "t1", "kind": "text", "label": "label",
                            "text_content": text}), rep)
        self.assertEqual(len(ir.entities), 1)
        self.assertEqual(ir.entities[0].text_content, text)
        self.assertEqual(rep.placeholder_texts, [])
        self.assertFalse(any("占位符" in w for w in rep.warnings))

    def test_no_text_is_dropped_with_delete_entity_guidance(self):
        rep = Report()
        ir = coerce_ir(obs({"id": "t1", "kind": "text", "label": "sign",
                            "text_content": "NO TEXT"}), rep)
        self.assertEqual(ir.entities, [])
        self.assertEqual(rep.placeholder_texts, ["NO TEXT"])
        self.assertTrue(any("应删实体而不是写 NO TEXT" in w for w in rep.warnings),
                        rep.warnings)

    def test_relation_to_placeholder_is_dropped(self):
        """占位符 text 实体被丢弃后，指向它的关系不能留下悬空指针。"""
        rep = Report()
        ir = coerce_ir(obs(
            {"id": "e1", "kind": "object", "label": "cup"},
            {"id": "t1", "kind": "text", "label": "sign",
             "text_content": "NO TEXT"},
            relations=[{"subject": "e1", "predicate": "touching", "object": "t1"}],
        ), rep)
        self.assertEqual([e.id for e in ir.entities], ["e1"])
        self.assertEqual(ir.relations, [])
        self.assertEqual(rep.placeholder_texts, ["NO TEXT"])

    def test_real_text_is_preserved_and_to_dict_has_old_keys(self):
        real = ["MANOR AVIE", "仙山", "QZOTERAN", "COMING 2026.12"]
        ents = [{"id": "t%d" % i, "kind": "text", "label": "sign%d" % i,
                 "text_content": value} for i, value in enumerate(real, 1)]
        rep = Report()
        ir = coerce_ir(obs(*ents), rep)
        self.assertEqual([e.text_content for e in ir.entities], real)
        self.assertEqual(rep.placeholder_texts, [])
        d = rep.to_dict()
        old_keys = {"image", "size", "warnings", "dropped", "entity_count",
                    "age_gate", "raw_chars", "kind_downgrades", "age_dropped",
                    "capped", "capped_at"}
        self.assertTrue(old_keys <= set(d), "旧键必须保留")
        self.assertIn("placeholder_texts", d)

    def test_placeholder_detector_normalizes_whole_string_only(self):
        for value in ("the string exactly as printed",
                      "The String Exactly As Printed.",
                      "string exactly as printed", "text exactly as printed",
                      "NO TEXT", "n/a", "TBD", "...", "…", "xxx", "???"):
            self.assertTrue(_is_placeholder_text(value), value)
        for value in ("The string exactly as printed on the label",
                      "MANOR AVIE", "COMING 2026.12", "xxx mark"):
            self.assertFalse(_is_placeholder_text(value), value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
