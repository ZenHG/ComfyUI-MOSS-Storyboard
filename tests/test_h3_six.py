# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""H3 六段式方言（h3-six-section）的专项单测。零 GPU、零网络。

跑：python -m unittest tests.test_h3_six -v
（与仓库其余测试一起跑：python -m unittest discover -s tests）

## 几段真稿是**逐字**夹具，不是手写样例

`tests/fixtures/h3_six/A01.txt` / `A03.txt` / `A06.txt` 摘自 2026-10-08 的产线
标注包（该包不在本仓）。它们的一句话不能改、一个全角标点不能改——六段式的「往返」
如果可以在夹具上顺手改字，那这条验收就等于自己给自己出题。所以：
① 文件 sha256 钉在测试里；② 真稿里的事实（台词、时间戳、声景原文）逐条断言。

## 往返口径

- **字节级**：`render(IR.from_dict(parse(t))) == t`（含空行与全角标点）。
- **事实级**：`timeline.shots` 的镜头数 / `At` 时间戳 / `<d>` 台词文本逐条对位；
  `duration_s` 来自 summary 的中文时长（「七点三秒」= 7.3）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith import h3, h3_six                      # noqa: E402
from moss_llm.smith.adapters import get_adapter             # noqa: E402
from moss_llm.smith.adapters.h3 import is_six_section       # noqa: E402
from moss_llm.smith.ir import IR                            # noqa: E402
from moss_llm.smith.registry import Registry                # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter          # noqa: E402

REG = Registry.load()
CNT = HeuristicCounter()
EX = ROOT / "moss_llm" / "smith" / "examples"
FIX = ROOT / "tests" / "fixtures" / "h3_six"

#: 真稿夹具的 sha256（不含行尾 \n 之外任何改动）。改夹具必须先解释为什么。
FIXTURE_SHA = {
    "A01": "e86ca16098cbe0f345088feb18b1d344586804921af57a8bd9b7e9ea7850ec4e",
    "A03": "b33a06e3593fef2fb38fd9b3dcef208d18b0807afd3bcb7704ea60321f9238d8",
    "A06": "394898b088a402c72468219160c3593f26f85c326e9bd9ee51bc60c4d16c5ca8",
}
TAGS = ("A01", "A03", "A06")


def fixture(tag: str) -> str:
    return (FIX / (tag + ".txt")).read_text(encoding="utf-8")


# ── 造坏稿用的小工具（只在测试里用） ──────────────────────────

def _blocks(text: str) -> dict:
    """按六段段头把文本切成块（块尾不含空行分隔符）。"""
    marks = list(re.finditer(r"^(%s)\s*:" % "|".join(h3_six.FIELDS), text, re.M))
    out: dict = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.start():end].rstrip("\n")
    return out


def _rebuild(text: str, order=None, drop=()) -> str:
    b = _blocks(text)
    for name in drop:
        b.pop(name, None)
    names = order or [f for f in h3_six.FIELDS if f in b]
    return "\n\n".join(b[n] for n in names) + "\n"


def _set_section(text: str, name: str, body: str) -> str:
    b = _blocks(text)
    b[name] = name + ":\n" + body
    return "\n\n".join(b[f] for f in h3_six.FIELDS if f in b) + "\n"


def _section_body(text: str, name: str) -> str:
    return _blocks(text)[name].split(":\n", 1)[1]


def _codes(text: str, ir=None, **kw) -> list:
    return [i.code for i in h3_six.check(text, ir, **kw)]


BIG3 = ("H3S-MISSING-SECTION", "H3S-SECTION-ORDER", "H3S-TS-RANGE")


# ─────────────────────────────────────────────────────────────
# 真稿夹具 + 往返
# ─────────────────────────────────────────────────────────────

class TestRealManuscripts(unittest.TestCase):
    """3 段真稿：逐字、往返、事实对位。"""

    def test_fixtures_are_present_and_verbatim(self):
        for tag in TAGS:
            raw = (FIX / (tag + ".txt")).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), FIXTURE_SHA[tag],
                             "%s.txt 被改过字。真稿夹具必须逐字保留（全角标点也算）"
                             % tag)
            text = raw.decode("utf-8")
            self.assertTrue(text.startswith("subject_definitions:\n"), tag)
            self.assertIn("non_diegetic_music:\nN/A", text, tag)

    def test_six_sections_in_canonical_order(self):
        for tag in TAGS:
            _preamble, sections, order = h3_six.split_sections(fixture(tag))
            self.assertEqual(order, list(h3_six.FIELDS), tag)
            self.assertEqual(sorted(sections), sorted(h3_six.FIELDS), tag)

    def test_byte_roundtrip_on_real_manuscripts(self):
        """render(parse(t)) 与 t **逐字节相等**（含空行与全角标点）。"""
        for tag in TAGS:
            text = fixture(tag)
            ir = IR.from_dict(h3_six.parse(text))
            self.assertEqual(h3_six.render(ir), text, tag)

    def test_roundtrip_is_idempotent(self):
        for tag in TAGS:
            once = h3_six.render(IR.from_dict(h3_six.parse(fixture(tag))))
            twice = h3_six.render(IR.from_dict(h3_six.parse(once)))
            self.assertEqual(once, twice, tag)

    def test_payload_survives_ir_dict_roundtrip(self):
        for tag in TAGS:
            ir = IR.from_dict(h3_six.parse(fixture(tag)))
            again = IR.from_dict(ir.to_dict())
            self.assertEqual(
                json.dumps(ir.timeline["six_section"], ensure_ascii=False, sort_keys=True),
                json.dumps(again.timeline["six_section"], ensure_ascii=False,
                           sort_keys=True),
                tag)

    def test_duration_comes_from_the_chinese_summary(self):
        expected = {"A01": 7.3, "A03": 6.6, "A06": 8.0}
        for tag, want in expected.items():
            ir = IR.from_dict(h3_six.parse(fixture(tag)))
            self.assertAlmostEqual(ir.timeline["duration_s"], want, places=3, msg=tag)

    def test_timeline_facts_are_recovered(self):
        # A01：两镜；Shot 1 无切点时间戳，Shot 2 在 3.0s；台词三句逐字。
        ir = IR.from_dict(h3_six.parse(fixture("A01")))
        shots = ir.timeline["shots"]
        self.assertEqual([s["index"] for s in shots], [1, 2])
        self.assertIsNone(shots[0].get("t_start"))
        self.assertAlmostEqual(shots[1]["t_start"], 3.0, places=3)
        self.assertEqual([d["text"] for d in shots[0]["dialogue"]], ["对，最后一站。"])
        self.assertEqual([d["text"] for d in shots[1]["dialogue"]],
                         ["我下一站下。", "顺路，送你到出口。"])
        self.assertEqual([d["speaker"] for d in shots[0]["dialogue"]], ["S1"])
        self.assertEqual([d["speaker"] for d in shots[1]["dialogue"]], ["S2", "S2"])

        # A03：一镜到底、不切、无时间戳、无台词。
        ir = IR.from_dict(h3_six.parse(fixture("A03")))
        shots = ir.timeline["shots"]
        self.assertEqual(len(shots), 1)
        self.assertIsNone(shots[0].get("t_start"))
        self.assertFalse(shots[0].get("dialogue"))

        # A06：三镜；切点 3.0 / 5.0；两句台词。
        ir = IR.from_dict(h3_six.parse(fixture("A06")))
        shots = ir.timeline["shots"]
        self.assertEqual([s.get("t_start") for s in shots], [None, 3.0, 5.0])
        self.assertEqual([d["text"] for d in shots[1]["dialogue"]], ["以前在。"])
        self.assertEqual([d["text"] for d in shots[2]["dialogue"]], ["现在……"])

    def test_subject_definitions_become_ir_entities(self):
        ir = IR.from_dict(h3_six.parse(fixture("A01")))
        labels = [e.label for e in ir.entities]
        self.assertEqual(labels, ["陈默", "林父", "高铁车厢", "行李"])
        # 三车厢是场景、行李是道具 —— 只有人物进 character
        self.assertEqual([e.kind for e in ir.entities],
                         ["character", "character", "object", "object"])

    def test_retention_entries_are_structured(self):
        ir = IR.from_dict(h3_six.parse(fixture("A01")))
        entries = ir.timeline["six_section"]["retention"]["entries"]
        refs = [e["ref"] for e in entries]
        self.assertEqual(refs, ["<Video 1>", "<Subject 1>", "<Subject 2>",
                                "<Subject 3>", "<Subject 4>"])
        self.assertEqual(entries[0]["status"], "fully_preserved")
        self.assertEqual(entries[1]["appears_in"], [1, 2])

    def test_source_lines_are_kept_verbatim(self):
        """逐字：真稿里的标记/台词/项目模板块必须原样出现在 parse 结果里。"""
        a01 = fixture("A01")
        for needle in ("<d>[zh]顺路，送你到出口。</d>",
                       "@图片4 D4 行李箱 定稿参考图。",
                       "【项目影像规格】",
                       "座位图（全片固定，逐镜不得改变）",
                       "机位基线（单人与双人镜一律用以下三种机位之一"):
            self.assertIn(needle, a01)
        self.assertIn("六点六秒续接段落", fixture("A03"))
        self.assertIn("一镜到底、全段连续不切", fixture("A03"))
        self.assertIn("八秒续接段落", fixture("A06"))
        self.assertIn("<d>[zh]现在……</d>", fixture("A06"))


class TestH3SixRender(unittest.TestCase):
    """没有骨架时从 IR 合成；有骨架时可以关掉回放重新合成。"""

    def test_synthesize_emits_all_six_sections_in_order(self):
        ir = IR.load(EX / "05-h3-t2va.json")
        text = h3_six.synthesize(ir)
        _preamble, sections, order = h3_six.split_sections(text)
        self.assertEqual(order, list(h3_six.FIELDS))
        self.assertTrue(all(sections[f].strip() for f in h3_six.FIELDS))

    def test_synthesize_keeps_shots_timestamps_and_dialogue(self):
        ir = IR.load(EX / "05-h3-t2va.json")
        text = h3_six.synthesize(ir)
        marks = re.findall(r"^\[Shot \d+\]", text, re.M)
        self.assertEqual(len(marks), 3)
        self.assertIn("[Shot 2] At 00:02.800,", text)
        self.assertIn("[Shot 3] At 00:05.800,", text)
        self.assertIn("<d>[zh]第一炉，刚出炉。</d>", text)
        self.assertIn("<Subject 1> (S1) 说", text)
        self.assertEqual(_codes(text, ir), [])

    def test_synthesize_continuation_adds_video_reference(self):
        ir = IR.load(EX / "05-h3-t2va.json")
        ir.timeline["mode"] = "i2va"
        text = h3_six.synthesize(ir)
        self.assertIn("<Video 1> ", text)
        self.assertIn("continuation source", text)
        self.assertIn("[video continuation]", text)

    def test_synthesize_static_ir_is_one_shot_and_honest_about_soundscape(self):
        ir = IR.load(ROOT / "tests" / "fixtures" / "smith" / "hero-storyboard.ir.json")
        text = h3_six.synthesize(ir)
        marks = re.findall(r"^\[Shot \d+\]", text, re.M)
        self.assertEqual(len(marks), 1)
        codes = set(_codes(text, ir))
        self.assertNotIn("H3S-NO-SHOTS", codes)
        self.assertIn("H3S-SOUNDSCAPE-NA", codes)   # 静态 IR 没有声景：报 warn，不替用户编

    def test_replay_can_be_turned_off(self):
        """改过 IR 之后要能重新合成，而不是回放旧骨架。"""
        text = fixture("A01")
        ir = IR.from_dict(h3_six.parse(text))
        replayed = h3_six.render(ir)
        rebuilt = h3_six.render(ir, replay=False)
        self.assertEqual(replayed, text)
        self.assertNotEqual(rebuilt, text)
        # 重新合成仍然是一份合法的六段式
        self.assertEqual([i.code for i in h3_six.check(rebuilt, ir)
                          if i.level == "error"], [])

    def test_synthesis_result_is_itself_parseable_and_stable(self):
        ir = IR.load(EX / "05-h3-t2va.json")
        text = h3_six.synthesize(ir)
        again = h3_six.render(IR.from_dict(h3_six.parse(text)))
        self.assertEqual(again, text)

    def test_looks_like_needs_two_section_labels(self):
        self.assertTrue(h3_six.looks_like(fixture("A01")))
        three = (EX / "h3-t2va-sample.txt").read_text(encoding="utf-8")
        self.assertFalse(h3_six.looks_like(three))
        self.assertFalse(h3_six.looks_like("summary: 只有一段"))


# ─────────────────────────────────────────────────────────────
# 判定：干净稿 + 三个独立错误码（每条都配破坏版）
# ─────────────────────────────────────────────────────────────

class TestH3SixCheck(unittest.TestCase):
    def test_three_real_manuscripts_pass_cleanly(self):
        for tag in TAGS:
            text = fixture(tag)
            ir = IR.from_dict(h3_six.parse(text))
            self.assertEqual(h3_six.check(text), [], tag)
            self.assertEqual(h3_six.check(text, ir), [], tag)
            self.assertEqual(h3_six.verdict([]), "pass")

    # ── 错误码 ①：缺段 ──────────────────────────────────────
    def test_missing_section_is_its_own_code(self):
        text = _rebuild(fixture("A01"), drop=("summary",))
        codes = _codes(text)
        self.assertIn("H3S-MISSING-SECTION", codes)
        self.assertNotIn("H3S-SECTION-ORDER", codes)
        self.assertNotIn("H3S-TS-RANGE", codes)

    def test_missing_sections_are_listed_one_by_one(self):
        text = _rebuild(fixture("A01"), drop=("summary", "non_diegetic_music"))
        issues = [i for i in h3_six.check(text) if i.code == "H3S-MISSING-SECTION"]
        self.assertEqual(len(issues), 2)
        msgs = " ".join(i.message for i in issues)
        self.assertIn("summary", msgs)
        self.assertIn("non_diegetic_music", msgs)

    # ── 错误码 ②：段序错 ────────────────────────────────────
    def test_section_order_is_its_own_code(self):
        text = _rebuild(fixture("A01"), order=[
            "subject_definitions", "retention_analysis", "summary",
            "detailed_description", "overall_soundscape", "non_diegetic_music"])
        codes = _codes(text)
        self.assertIn("H3S-SECTION-ORDER", codes)
        self.assertNotIn("H3S-MISSING-SECTION", codes)
        self.assertNotIn("H3S-TS-RANGE", codes)

    # ── 错误码 ③：时间戳越界 ────────────────────────────────
    def test_timestamp_out_of_range_is_its_own_code(self):
        text = fixture("A01").replace("At 00:05.000", "At 00:09.500")
        codes = _codes(text)
        self.assertIn("H3S-TS-RANGE", codes)
        self.assertNotIn("H3S-MISSING-SECTION", codes)
        self.assertNotIn("H3S-SECTION-ORDER", codes)
        self.assertNotIn("H3S-TS-ORDER", codes)

    def test_timestamp_range_uses_the_declared_duration_boundary(self):
        # A01 的 summary 声明 7.3s：7.299 合法，7.300 越界（边界是开区间语义）
        ok = fixture("A01").replace("At 00:05.000", "At 00:07.299")
        self.assertNotIn("H3S-TS-RANGE", _codes(ok))
        bad = fixture("A01").replace("At 00:05.000", "At 00:07.300")
        self.assertIn("H3S-TS-RANGE", _codes(bad))

    def test_explicit_duration_overrides_the_summary(self):
        text = fixture("A01")
        self.assertNotIn("H3S-TS-RANGE", _codes(text))
        self.assertIn("H3S-TS-RANGE", _codes(text, duration_s=4.0))

    def test_duration_unknown_is_a_warn_not_an_error(self):
        text = fixture("A01").replace("七点三秒续接段落", "续接段落")
        codes = _codes(text)
        self.assertIn("H3S-DURATION-UNKNOWN", codes)
        self.assertNotIn("H3S-TS-RANGE", codes)

    def test_duration_unknown_is_consistent_with_or_without_parsed_ir(self):
        """没写时长的稿，传不传 parse 出来的 ir 必须同结论（都 warn，不报越界）。

        回归的是：`parse` 把「无时长」写成 `timeline.duration_s=8.0` 是给
        `IR.normalize()` 用的结构兜底，不是事实。早前的 check 直接读它，于是同一份
        稿 `check(text)` 报 DURATION-UNKNOWN warn、`check(text, parse(text))` 变成
        TS-RANGE error——适配器路径恰好总传 ir（`_realize_six`），所以是真路径。
        """
        text = (fixture("A01").replace("七点三秒续接段落", "续接段落")
                .replace("At 00:05.000", "At 00:09.500"))
        without_ir = {i.code for i in h3_six.check(text)}
        ir = IR.from_dict(h3_six.parse(text))
        self.assertEqual(ir.timeline["duration_s"], 8.0)          # 结构兜底确实在
        self.assertIsNone(ir.timeline["six_section"]["duration_s"])  # 骨架说「不知道」
        with_ir = {i.code for i in h3_six.check(text, ir)}
        self.assertEqual(without_ir, with_ir)
        self.assertIn("H3S-DURATION-UNKNOWN", with_ir)
        self.assertNotIn("H3S-TS-RANGE", with_ir)

    def test_real_duration_s_still_catches_out_of_range(self):
        """真给了 duration_s 的 IR 仍要判越界（三种来源各验一次）。"""
        text = fixture("A01").replace("七点三秒续接段落", "续接段落")   # 文本不再带时长
        # ① 用户/调用方手写的 IR（没有六段骨架）
        user_ir = IR.from_dict({"timeline": {"duration_s": 4.0}, "entities": []})
        self.assertIn("H3S-TS-RANGE", _codes(text, user_ir))
        # ② parse 之后用户把 timeline.duration_s 改成非 8.0 兜底值
        parsed = IR.from_dict(h3_six.parse(text))
        parsed.timeline["duration_s"] = 4.0
        self.assertIn("H3S-TS-RANGE", _codes(text, parsed))
        # ③ 合成路径：骨架里登记了 IR 的 duration_s（8.0），超了就判
        ir = IR.load(EX / "05-h3-t2va.json")
        synth = h3_six.synthesize(ir).replace("At 00:05.800", "At 00:09.800")
        self.assertIn("H3S-TS-RANGE", _codes(synth, ir))

    def test_summary_duration_beats_a_manually_edited_timeline_duration(self):
        """summary 声明了时长时以它为准：手改 parsed IR 的 timeline.duration_s 不覆盖。"""
        parsed = IR.from_dict(h3_six.parse(fixture("A01")))
        parsed.timeline["duration_s"] = 2.0       # 与 summary 的 7.3s 冲突
        self.assertNotIn("H3S-TS-RANGE", _codes(fixture("A01"), parsed))

    # ── 其余码：各配一条破坏版 ──────────────────────────────
    def test_timestamp_order_is_checked(self):
        text = fixture("A01").replace("At 00:03.600", "At 00:02.000")
        self.assertIn("H3S-TS-ORDER", _codes(text))

    def test_duplicated_section_is_an_error(self):
        text = fixture("A01") + "\nsummary:\n[reference generation] 重复段。\n"
        self.assertIn("H3S-SECTION-DUP", _codes(text))

    def test_empty_section_is_an_error(self):
        text = _set_section(fixture("A01"), "summary", "")
        self.assertIn("H3S-SECTION-EMPTY", _codes(text))

    def test_no_shot_marker_is_an_error(self):
        text = _set_section(fixture("A01"), "detailed_description",
                            "本段只有一句说明，没有镜头标记。")
        self.assertIn("H3S-NO-SHOTS", _codes(text))

    def test_shot_numbering_must_be_consecutive(self):
        text = fixture("A01").replace("[Shot 2] At 00:03.000,",
                                      "[Shot 3] At 00:03.000,")
        self.assertIn("H3S-SHOT-SEQ", _codes(text))

    def test_undefined_subject_reference_is_an_error(self):
        text = "\n".join(ln for ln in fixture("A01").split("\n")
                         if not ln.startswith("<Subject 4> 行李"))
        codes = _codes(text)
        self.assertIn("H3S-REF-UNDEFINED", codes)
        self.assertTrue(all(c not in codes for c in BIG3))

    def test_retention_reference_must_be_defined(self):
        retention = _section_body(fixture("A01"), "retention_analysis")
        text = _set_section(fixture("A01"), "retention_analysis",
                            retention.replace("<Subject 4>", "<Subject 9>"))
        codes = _codes(text)
        self.assertIn("H3S-RETENTION-REF", codes)
        self.assertNotIn("H3S-REF-UNDEFINED", codes)

    def test_dialogue_language_tag_is_required(self):
        text = fixture("A01").replace("<d>[zh]对，最后一站。</d>",
                                      "<d>对，最后一站。</d>")
        self.assertIn("H3S-D-LANG", _codes(text))

    def test_music_value_is_validated(self):
        text = _set_section(fixture("A01"), "non_diegetic_music", "无。")
        self.assertIn("H3S-MUSIC", _codes(text))

    def test_music_policy_na_is_stricter(self):
        text = fixture("A01")
        self.assertNotIn("H3S-MUSIC", _codes(text, music_policy="na"))
        composed = _set_section(fixture("A01"), "non_diegetic_music",
                                "一层很低的氛围铺底，单一音色的低频合成垫缓慢起伏。")
        self.assertNotIn("H3S-MUSIC", _codes(composed))
        self.assertIn("H3S-MUSIC", _codes(composed, music_policy="na"))

    def test_soundscape_na_is_a_warn(self):
        text = _set_section(fixture("A01"), "overall_soundscape", "N/A")
        levels = {i.code: i.level for i in h3_six.check(text)}
        self.assertEqual(levels.get("H3S-SOUNDSCAPE-NA"), "warn")

    def test_summary_mode_tag_is_a_warn(self):
        text = _set_section(fixture("A01"), "summary",
                            "七点三秒续接段落，三拍。")
        levels = {i.code: i.level for i in h3_six.check(text)}
        self.assertEqual(levels.get("H3S-SUMMARY-TAG"), "warn")

    def test_shot1_timestamp_is_a_warn(self):
        text = fixture("A01").replace("[Shot 1] ", "[Shot 1] At 00:00.500, ", 1)
        levels = {i.code: i.level for i in h3_six.check(text)}
        self.assertEqual(levels.get("H3S-SHOT1-TS"), "warn")

    def test_markdown_fence_is_an_error(self):
        text = "```\n" + fixture("A01") + "```\n"
        self.assertEqual(_codes(text), ["H3S-FENCE"])

    def test_report_mentions_codes(self):
        text = _rebuild(fixture("A01"), drop=("summary",))
        rep = h3_six.report(h3_six.check(text))
        self.assertIn("H3S-MISSING-SECTION", rep)
        self.assertIn("0 error", h3_six.report([]))


# ─────────────────────────────────────────────────────────────
# 适配器：六段式是 minimax-h3 的方言档，默认三字段行为零变化
# ─────────────────────────────────────────────────────────────

class TestH3SixAdapterProfile(unittest.TestCase):
    def _adapter(self):
        return get_adapter("minimax-h3", REG, counter=CNT)

    def test_profile_renders_the_manuscript_byte_identically(self):
        text = fixture("A01")
        ir = IR.from_dict(h3_six.parse(text))
        out = self._adapter().realize(ir, profile="six-section")
        self.assertEqual(out.positive, text)
        self.assertEqual(out.params.get("dialect"), "h3-six-section")
        self.assertEqual(out.params.get("fields"), list(h3_six.FIELDS))
        self.assertIsNone(out.negative)
        self.assertGreater(out.token_count, 0)
        self.assertFalse([w for w in out.warnings if w.startswith("[H3S] ERROR")])

    def test_profile_aliases_are_accepted(self):
        ir = IR.from_dict(h3_six.parse(fixture("A03")))
        for alias in ("six-section", "six_section", "h3-six-section"):
            self.assertTrue(is_six_section(alias), alias)
            out = self._adapter().realize(ir, profile=alias)
            self.assertEqual(out.positive, fixture("A03"), alias)

    def test_default_path_is_three_field_and_unchanged(self):
        ir = IR.load(EX / "05-h3-t2va.json")
        out = self._adapter().realize(ir)
        self.assertEqual(out.positive, h3.render(ir))       # 逐字节同改前
        self.assertEqual(out.params, REG.sampling("minimax-h3"))
        self.assertFalse([w for w in out.warnings if w.startswith("[H3S]")])
        self.assertTrue(out.positive.startswith(h3.FIELDS[0]))

    def test_profile_warns_that_invariants_cover_the_three_field_path(self):
        ir = IR.from_dict(h3_six.parse(fixture("A03")))
        out = self._adapter().realize(ir, profile="six-section")
        self.assertTrue(any("M6" in w and "三字段" in w for w in out.warnings))

    def test_profile_works_on_a_static_ir(self):
        ir = IR.load(ROOT / "tests" / "fixtures" / "smith" / "hero-storyboard.ir.json")
        out = self._adapter().realize(ir, profile="six-section")
        self.assertEqual(out.positive.split("\n", 1)[0], "subject_definitions:")
        self.assertFalse([w for w in out.warnings if w.startswith("[H3S] ERROR")])

    def test_six_profile_does_not_create_a_target(self):
        """设计决策：六段式是 profile，不是新 target（历史记录见 git 历史）。"""
        self.assertNotIn("h3-six-section", REG.names())
        caps = REG.get("minimax-h3")
        self.assertIn("dialects", caps)
        dialect = caps["dialects"]["six-section"]
        self.assertEqual(dialect["fields"], list(h3_six.FIELDS))
        self.assertIn("six-section", dialect["selector"])
        self.assertIn(dialect["confidence"], ("high", "medium", "low"))
        self.assertTrue(dialect["source"])

    def test_three_field_contract_is_not_touched(self):
        caps = REG.get("minimax-h3")
        self.assertEqual(caps["prompt"]["fields"], list(h3.FIELDS))
        self.assertEqual(caps["prompt"]["mode"], "timeline")
        self.assertIn("minimax-h3", REG.names())


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────

class TestH3SixHelpers(unittest.TestCase):
    def test_duration_from_summary(self):
        cases = (("七点三秒续接段落", 7.3), ("六点六秒续接段落", 6.6),
                 ("八秒续接段落", 8.0), ("九点四秒开场段落", 9.4),
                 ("十二秒段落", 12.0), ("8.5秒段落", 8.5),
                 ("没有时长声明", None), ("", None))
        for text, want in cases:
            got = h3_six.duration_from_summary(text)
            if want is None:
                self.assertIsNone(got, text)
            else:
                self.assertAlmostEqual(got, want, places=3, msg=text)

    def test_split_sections_supports_inline_values(self):
        doc = ("subject_definitions:\n<Subject 1> 甲：白衬衫。\n\n"
               "overall_soundscape: 风声压在门槛一线。\n"
               "non_diegetic_music: N/A\n")
        _preamble, sections, order = h3_six.split_sections(doc)
        self.assertEqual(order, ["subject_definitions", "overall_soundscape",
                                 "non_diegetic_music"])
        self.assertEqual(sections["overall_soundscape"], "风声压在门槛一线。")
        self.assertEqual(sections["non_diegetic_music"], "N/A")
        # 缺的三段不补，交给 check 报
        missing = [f for f in h3_six.FIELDS if f not in sections]
        self.assertEqual(missing, ["summary", "retention_analysis",
                                   "detailed_description"])
        issues = h3_six.check(doc)
        coded = [i for i in issues if i.code == "H3S-MISSING-SECTION"]
        self.assertEqual(len(coded), 3)
        self.assertTrue(all("summary" in i.message or "retention_analysis" in i.message
                            or "detailed_description" in i.message for i in coded))

    def test_parse_keeps_preamble_out_of_sections(self):
        text = "一句话前言。\n\n" + fixture("A03")
        d = h3_six.parse(text)
        payload = d["timeline"]["six_section"]
        self.assertEqual(payload["preamble"], "一句话前言。")
        # 前言不影响六段本身
        self.assertEqual(payload["order"], list(h3_six.FIELDS))


class TestH3SixLintWiring(unittest.TestCase):
    """六段式产物过**通用 lint** 时不许报 L003 假错。

    `[video continuation]` / `[reference generation]` 是 summary 首行的**体裁记号**，
    不是权重语法；`lint.py` 的 timeline 分支必须先把它们剥掉。反例同样要红——
    真的权重语法（`(主体:1.3)`）不能被一刀切放过。
    """

    def _out(self, tag: str):
        ir = IR.from_dict(h3_six.parse(fixture(tag)))
        ad = get_adapter("minimax-h3", REG, counter=CNT)
        return ir, ad.realize(ir, profile="six-section")

    def _l003(self, ir, out) -> list:
        from moss_llm.smith.lint import check
        payload = out.as_dict() if hasattr(out, "as_dict") else out
        issues = check(ir, "minimax-h3", REG.get("minimax-h3"), payload)
        return [i for i in issues if i.code == "L003"]

    def test_video_continuation_marker_is_not_weight_syntax(self):
        ir, out = self._out("A01")
        self.assertIn("[video continuation]", out.positive)
        self.assertEqual(self._l003(ir, out), [])

    def test_reference_generation_marker_is_not_weight_syntax(self):
        ir, out = self._out("A03")
        bad = out.as_dict()
        bad["positive"] = "summary:\n[reference generation] 六点六秒段落。\n"
        self.assertEqual(self._l003(ir, bad), [])

    def test_real_weight_syntax_still_errors(self):
        ir, out = self._out("A01")
        bad = out.as_dict()
        bad["positive"] = out.positive.replace("subject_definitions:",
                                               "subject_definitions: (主体:1.3)", 1)
        hits = self._l003(ir, bad)
        self.assertEqual(len(hits), 1)
        self.assertIn("(主体:1.3)", hits[0].message)


class TestH3ParamFalsePositives(unittest.TestCase):
    """H3-PARAM 不能被自然语言里的 steps / 时间骗到（T12 真机首跑暴露的误报）。

    背景：挂 8B LoRA 的输出里有 "rhythmic steps"（脚步），旧判据 `\\bsteps?\\b`
    直接把它当结构化参数 steps；不挂 LoRA 的输出因时间戳被 `_blank_ts()` 挖掉
    反而 pass。判据要报的是「参数名 + 数值」的写法，不是自然词。
    """

    BASE = ("integrated_multimodal_description: [Shot 1] a red fox walks through "
            "a snowy forest at dawn.\n\n"
            "overall_soundscape: soft wind in the pines.\n\n"
            "non_diegetic_music: N/A")

    def _codes(self, first_sentence):
        text = self.BASE.replace(
            "[Shot 1] a red fox walks through a snowy forest at dawn.", first_sentence)
        return [i.code for i in h3.check(text)]

    def test_rhythmic_steps_is_not_a_parameter(self):
        self.assertNotIn("H3-PARAM", self._codes(
            "[Shot 1] The fox moves with rhythmic steps over packed snow."))

    def test_footsteps_and_two_steps_are_not_parameters(self):
        codes = self._codes(
            "[Shot 1] Footsteps crunch; the fox takes two steps forward.")
        self.assertNotIn("H3-PARAM", codes)

    def test_real_steps_parameter_still_errors(self):
        for body in ("[Shot 1] a fox, steps: 20.",
                     "[Shot 1] a fox, steps 20.",
                     "[Shot 1] a fox, steps=20."):
            self.assertIn("H3-PARAM", self._codes(body), body)

    def test_time_phrases_are_not_parameters(self):
        codes = self._codes(
            "[Shot 1] the fox walks on; a few seconds later it stops.")
        self.assertNotIn("H3-PARAM", codes)

    def test_mmss_timestamp_is_not_an_aspect_parameter(self):
        """`At 00:03` 不能被当成 `00:03` 画幅；时间戳规则另有 H3-SHOTN-TS。"""
        codes = self._codes(
            "[Shot 1] a fox. [Shot 2] At 00:03, the camera cuts to the snow.")
        self.assertNotIn("H3-PARAM", codes)

    def test_common_aspect_ratios_are_parameters(self):
        """白名单收窄会漏报：2.35:1 / 4:5 / 16:10 / 5:4 都是真参数。"""
        for body in ("[Shot 1] a fox, 2.35:1.",
                     "[Shot 1] a fox, 1.85:1.",
                     "[Shot 1] a fox, 4:5.",
                     "[Shot 1] a fox, 16:10.",
                     "[Shot 1] a fox, 5:4."):
            self.assertIn("H3-PARAM", self._codes(body), body)

    def test_chinese_suffix_ratio_is_parameter(self):
        self.assertIn("H3-PARAM", self._codes("[Shot 1] a fox, 画幅 4:5。"))

    def test_fps_before_number_is_parameter(self):
        for body in ("[Shot 1] a fox, fps 24.", "[Shot 1] a fox, fps: 24."):
            self.assertIn("H3-PARAM", self._codes(body), body)

    def test_rhythmic_steps_still_safe_after_ratio_restore(self):
        """恢复通用比例规则后，自然语言 steps 仍不能报（防回归）。"""
        self.assertNotIn("H3-PARAM", self._codes(
            "[Shot 1] rhythmic steps across snow; another two steps forward."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
