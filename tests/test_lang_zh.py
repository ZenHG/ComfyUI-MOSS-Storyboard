# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""T5 单测：`--lang zh` 整句重排（中文路径）。零 GPU、零网络。

覆盖四条验收：
1. 同一 IR 中英两份事实集合相等（roundtrip lost=0，4 份 fixtures）；
2. 中文路径「无英文语序」的**机检规则**（不是靠人眼）；
3. 默认 en 输出与改前逐字节一致（直接对 freeze 基线 sha256 对账）；
4. `CLI --lang` / `compile_ir(lang=)` / registry `prompt.language` 三条入口都通。

跑：python -m unittest tests.test_lang_zh -v
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith import zh as ZH                       # noqa: E402
from moss_llm.smith.adapters import get_adapter           # noqa: E402
from moss_llm.smith.api import compile_ir                 # noqa: E402
from moss_llm.smith.clauses import DIM_ORDER              # noqa: E402
from moss_llm.smith.ir import IR                          # noqa: E402
from moss_llm.smith.registry import Registry              # noqa: E402
from moss_llm.smith.roundtrip import diff                 # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter        # noqa: E402

REG = Registry.load()
CNT = HeuristicCounter()
FIX = ROOT / "tests" / "fixtures" / "smith"
EX = ROOT / "moss_llm" / "smith" / "examples"
FIXTURES = sorted(FIX.glob("*.ir.json"))

#: 只测「散文 / tag 正文」目标。moss-image 在带 bbox 的 IR 上切 Layout JSON、
#: flux3 会追加 bbox 表——这两种形态**不做整句重排**（见 docs/28 §6 限制），
#: 不是漏做，是有意的：坐标/结构不是一句话，硬翻会把事实弄丢。
TARGETS = ("qwen-image-2_1", "krea2")
TAG_TARGET = "sdxl"

#: 带角色、且不会被切到 Layout JSON 的 fixtures（用于结构断言）。
CHAR_FIXTURES = [p for p in FIXTURES
                 if any(e.kind == "character" for e in IR.load(p).entities)]


def _realize(path: Path, target: str):
    ir = IR.load(path)
    ad = get_adapter(target, REG, counter=CNT)
    return ir, ad, ad.realize(ir).positive


def _realize_zh(path: Path, target: str):
    ir = IR.load(path)
    ad = get_adapter(target, REG, counter=CNT)
    return ir, ad, ZH.realize_zh(ad, ir)


def _dim_order(target: str, ad):
    """tag 方言按重要度分块，句子顺序不再受维度锁；用宽松判据。"""
    return () if target == TAG_TARGET else ad.dim_order


class TestChineseFacts(unittest.TestCase):
    """验收①：中英两份事实集合相等。"""

    def test_fact_sets_equal_and_roundtrip_lost_zero(self):
        for path in FIXTURES:
            for target in TARGETS:
                with self.subTest(fixture=path.name, target=target):
                    ir, ad, zh = _realize_zh(path, target)
                    en = ad.realize(ir).positive
                    en_diff = diff(ir, en)
                    zh_diff = diff(ir, ZH.fact_surface(zh.positive, ir))
                    self.assertEqual(en_diff.lost, [],
                                     "英文基线就丢了事实：%r" % (en_diff.lost,))
                    self.assertEqual(zh_diff.lost, [],
                                     "中文丢事实：%r" % (zh_diff.lost,))
                    # 「互转后相等」：两边多出来的事实也必须一样多、一样是哪些。
                    self.assertEqual(sorted(en_diff.extra), sorted(zh_diff.extra))

    def test_fact_surface_does_not_launder_lost_facts(self):
        """反证：颜色/方位真被删掉时，fact_surface 必须让 roundtrip 报丢失。"""
        ir, _ad, zh = _realize_zh(FIX / "hero-storyboard.ir.json", "qwen-image-2_1")
        self.assertIn("白色", zh.positive)
        broken = zh.positive.replace("白色", "")
        lost = {tuple(x) for x in diff(ir, ZH.fact_surface(broken, ir)).lost}
        self.assertIn(("color", "white"), lost)
        anchored = zh.positive.replace("画面左前景", "某处").replace("画面右前景", "某处")
        lost2 = {tuple(x) for x in diff(ir, ZH.fact_surface(anchored, ir)).lost}
        self.assertTrue({x for x in lost2 if x[0] == "anchor"}, "方位丢失必须报出来")

    def test_lint_parity_en_vs_zh(self):
        """中文路径不得引入新的 error/warn（L006 年龄闸这类与语言无关的判据两边一致）。"""
        for path in FIXTURES:
            for target in TARGETS + (TAG_TARGET,):
                with self.subTest(fixture=path.name, target=target):
                    r_en = compile_ir(str(path), target)
                    r_zh = compile_ir(str(path), target, lang="zh")
                    a = sorted((i["level"], i["code"]) for i in r_en["issues"])
                    b = sorted((i["level"], i["code"]) for i in r_zh["issues"])
                    self.assertEqual(a, b)


class TestNoEnglishOrder(unittest.TestCase):
    """验收②的机检部分：规则必须能抓住英文语序，也不能误伤中文输出。"""

    def test_zh_outputs_pass_machine_rules(self):
        for path in FIXTURES:
            for target in TARGETS:
                with self.subTest(fixture=path.name, target=target):
                    ir, ad, out = _realize_zh(path, target)
                    self.assertEqual(
                        ZH.zh_issues(out.positive, ir, _dim_order(target, ad)), [])

    def test_checker_flags_english_output(self):
        """规则非空转：同一份 IR 的英文输出必须被 Z01/Z03 抓到。"""
        ir, _ad, en = _realize(FIX / "hero-storyboard.ir.json", "qwen-image-2_1")
        issues = ZH.zh_issues(en, ir)
        self.assertTrue(any(v.startswith("Z01") for v in issues), issues)
        self.assertTrue(any(v.startswith("Z03") for v in issues), issues)

    def test_subject_leads_and_measure_words(self):
        for path in CHAR_FIXTURES:
            with self.subTest(fixture=path.name):
                ir, _ad, out = _realize_zh(path, "qwen-image-2_1")
                text = out.positive
                self.assertRegex(text, r"^[\u3400-\u9fff]")          # 首字中文
                self.assertRegex(text, r"\d+岁")                     # 年龄写成中文式
                self.assertRegex(text, r"一个\d+岁的[男女]")          # 量词 + 主语短语
                # 主语在第一个「一个N岁的…」里，且在输出前半段
                m = text.find("一个")
                self.assertGreaterEqual(m, 0)
                self.assertLess(m, max(1, len(text) // 2))
                # 「的」字不堆叠（英文 of the … of the 的直译痕迹）
                self.assertIsNone(ZH._DE_STACK.search(CJK_MASK(text)))

    def test_english_residue_is_named_not_silent(self):
        """词表外英文词必须被点名（warnings + Z02），不能静默吞掉或假装翻译过。"""
        ir = IR.from_dict({
            "task": "t2i", "canvas": {"aspect_ratio": "1:1"},
            "entities": [{"id": "e1", "kind": "object", "importance": 1.0,
                          "label": "flumbus gizmo", "age_years": 30}]})
        ad = get_adapter("qwen-image-2_1", REG, counter=CNT)
        out = ZH.realize_zh(ad, ir)
        self.assertIn("flumbus", out.positive)
        self.assertTrue(any("不在词表内" in w for w in out.warnings), out.warnings)
        self.assertTrue(any(v.startswith("Z02") for v in ZH.zh_issues(out.positive, ir)))

    def test_quoted_text_is_verbatim(self):
        ir, _ad, out = _realize_zh(FIX / "ui-smoke.ir.json", "qwen-image-2_1")
        flat = " ".join(out.positive.split())
        for e in ir.text_entities:
            for line in (e.text_content or "").splitlines():
                line = line.strip()
                if line:
                    self.assertIn(line, flat, "图内文字被改写：%r" % line)


class TestEnByteIdentical(unittest.TestCase):
    """验收③：默认 en 输出与改前逐字节一致。"""

    TARGETS = ("moss-image", "qwen-image-2_1", "krea2", "flux3", "sdxl")

    def test_en_matches_frozen_sha256(self):
        import hashlib

        expected = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))

        def digest(text):
            return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

        for path in FIXTURES:
            ir = IR.load(path)
            entry = expected[path.name]
            for target in self.TARGETS:
                with self.subTest(fixture=path.name, target=target):
                    out = get_adapter(target, REG, counter=HeuristicCounter()).realize(ir)
                    self.assertEqual(digest(out.positive),
                                     entry["targets"][target]["positive"])
                    self.assertEqual(digest(out.negative or ""),
                                     entry["targets"][target]["negative"])

    def test_importing_zh_does_not_change_en_api_result(self):
        path = EX / "01-portrait-photoreal.json"
        ir = IR.load(path)
        plain = get_adapter("moss-image", REG, counter=CNT).realize(ir).positive
        r = compile_ir(str(path), "moss-image")
        self.assertEqual(r["positive"], plain)
        self.assertNotIn("lang", r, "默认 en 结果不得多出语言键")


class TestLanguageSelection(unittest.TestCase):
    """验收④：CLI / API / registry 三条入口。"""

    def test_api_lang_parameter(self):
        path = str(EX / "01-portrait-photoreal.json")
        en = compile_ir(path, "moss-image")["positive"]
        zh = compile_ir(path, "moss-image", lang="zh")
        self.assertEqual(zh.get("lang"), "zh")
        self.assertIsNone(ZH._DE_STACK.search(zh["positive"]))
        self.assertNotEqual(zh["positive"], en)
        self.assertTrue(zh["positive"].startswith("一个"))

    def test_registry_language_field_drives_path(self):
        """复用 registry 的 prompt.language 字段（只读，不改结构）。"""
        ir_src = str(EX / "01-portrait-photoreal.json")
        data = copy.deepcopy(REG.data)
        data["targets"]["moss-image"]["prompt"]["language"] = "zh"
        reg_zh = Registry(data)
        r = compile_ir(ir_src, "moss-image", registry=reg_zh)
        self.assertEqual(r.get("lang"), "zh")
        # 显式 --lang en 必须能盖回英文（开关是双向的）
        r_en = compile_ir(ir_src, "moss-image", registry=reg_zh, lang="en")
        self.assertNotIn("lang", r_en)
        self.assertIn("year-old woman", r_en["positive"])

    def test_cli_lang_flag(self):
        fixture = str(FIX / "hero-storyboard.ir.json")
        base = [sys.executable, "-m", "moss_llm.smith", "compile", fixture,
                "--target", "qwen-image-2_1", "--json"]
        zh = subprocess.run(base + ["--lang", "zh"], cwd=str(ROOT),
                            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(zh.returncode, 0, zh.stderr)
        payload = json.loads(zh.stdout)
        self.assertEqual(payload["lang"], "zh")
        self.assertRegex(payload["positive"], r"^[\u3400-\u9fff]")
        en = subprocess.run(base, cwd=str(ROOT), capture_output=True,
                            text=True, encoding="utf-8")
        self.assertEqual(en.returncode, 0, en.stderr)
        self.assertNotIn("lang", json.loads(en.stdout))

    def test_h3_timeline_ignores_zh(self):
        """H3 是时间线体裁、自带中文骨架：--lang 不能把它的三字段渲染翻掉。"""
        fixture = str(FIX / "hero-storyboard.ir.json")
        en = compile_ir(fixture, "minimax-h3")
        zh = compile_ir(fixture, "minimax-h3", lang="zh")
        self.assertEqual(en["positive"], zh["positive"])
        self.assertNotIn("lang", zh)
        self.assertTrue(any("时间线" in w for w in zh["warnings"]), zh["warnings"])

    def test_api_rejects_unknown_lang(self):
        """API 没有 argparse 的 choices：非法 lang 必须报错，不能静默回退 registry。"""
        path = str(EX / "01-portrait-photoreal.json")
        with self.assertRaises(ValueError) as ctx:
            compile_ir(path, "moss-image", lang="fr")
        msg = str(ctx.exception)
        for needle in ("fr", "en", "zh"):
            self.assertIn(needle, msg)
        # 合法值仍照常（大小写归一化不算放宽取值）
        self.assertNotIn("lang", compile_ir(path, "moss-image", lang="EN"))
        self.assertEqual(compile_ir(path, "moss-image", lang="zh").get("lang"), "zh")
        # 参数错误优先于内容错误：IR 本身不合法时也应该先报 lang
        with self.assertRaises(ValueError):
            compile_ir("不是 IR", "moss-image", lang="xx")

    def test_cli_rejects_zh_with_llm_before_starting_server(self):
        """互斥校验必须早于侧车启动：否则端点不可达时用户先看到网络错误，真因被盖住。"""
        fixture = str(FIX / "hero-storyboard.ir.json")
        r = subprocess.run(
            [sys.executable, "-m", "moss_llm.smith", "compile", fixture,
             "--target", "qwen-image-2_1", "--mode", "llm", "--lang", "zh",
             "--base-url", "http://127.0.0.1:1"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        combined = (r.stdout or "") + (r.stderr or "")
        self.assertNotEqual(r.returncode, 0)                      # 拒绝，不是静默降级
        self.assertIn("只支持确定性路径", combined)               # 真因先报出来
        self.assertNotIn("[server]", combined,
                         "互斥校验早于 server.start()，不该先起侧车：" + combined[:300])


class TestClausePlanZh(unittest.TestCase):
    """结构层：中文子句计划与方言参数化。"""

    def test_subject_clause_comes_first(self):
        for path in CHAR_FIXTURES:
            with self.subTest(fixture=path.name):
                ir = IR.load(path)
                clauses = ZH.build_clauses_zh(ir)
                self.assertTrue(clauses)
                # 渲染按维度顺序成句：只要存在 subject 子句，第一个出声的维度就是它，
                # 与「重要度排序」（预算裁谁先走）不是同一回事。
                first_dim = next(d for d in DIM_ORDER
                                 if any(c.dim == d for c in clauses))
                self.assertEqual(first_dim, "subject")
                self.assertEqual(clauses[0].source in ("fact", "note"), True)

    def test_dim_order_is_dialect_parameter(self):
        hero = FIX / "hero-storyboard.ir.json"
        ir = IR.load(hero)
        qwen = ZH.realize_zh(get_adapter("qwen-image-2_1", REG, counter=CNT), ir)
        krea = ZH.realize_zh(get_adapter("krea2", REG, counter=CNT), ir)
        self.assertTrue(qwen.positive.startswith("一个"))
        self.assertTrue(krea.positive.startswith("摄影作品"),
                        "Krea 2 的 medium-first 方言必须保留：%r" % krea.positive[:12])

    def test_sdxl_tag_dialect_chunks_with_break(self):
        ir = IR.load(FIX / "hero-storyboard.ir.json")
        ad = get_adapter(TAG_TARGET, REG, counter=CNT)
        out = ZH.realize_zh(ad, ir)
        self.assertIn(" BREAK ", out.positive)
        self.assertFalse(out.positive.rstrip().endswith("。"))
        self.assertEqual(ZH.zh_issues(out.positive, ir, ()), [])

    def test_budget_pack_still_respected(self):
        raw = IR.load(FIX / "hero-storyboard.ir.json").to_dict()
        raw["budget"]["target_tokens"] = 120
        ir = IR.from_dict(raw)
        ad = get_adapter("krea2", REG, counter=CNT)
        out = ZH.realize_zh(ad, ir)
        self.assertTrue(out.dropped, "120 token 必然砍掉低权子句")
        self.assertLessEqual(out.token_count, 120)
        self.assertGreater(len(out.positive), 0)


_CJK_MASK = None


def CJK_MASK(text: str) -> str:
    """机检前的遮罩：引号内逐字文字不参与前瞻判断。"""
    import re
    return re.sub(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」', " ", text or "")

if __name__ == "__main__":
    unittest.main(verbosity=2)
