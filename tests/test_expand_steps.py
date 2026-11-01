# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""扩写三拆（补全 / 翻译 / 改写）的单测。零 GPU、零网络、假 LLM。

覆盖三件事：
1. 每步的判据**能抓到该步的典型错误**（判据不是摆设）；
2. 每步**关掉后产物仍合法**（roundtrip 零丢失 + 不变式不红）；
3. report 能指认**问题来自哪一步**（stage / stage_issues），且旧键向后兼容。

跑：python tests/test_expand_steps.py
（与仓库其余测试一起跑：python -m unittest discover -s tests）
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith import expand, steps                      # noqa: E402
from moss_llm.smith import invariants as INV                  # noqa: E402
from moss_llm.smith.adapters import get_adapter               # noqa: E402
from moss_llm.smith.clauses import build_clauses              # noqa: E402
from moss_llm.smith.ir import IR                              # noqa: E402
from moss_llm.smith.registry import Registry                  # noqa: E402
from moss_llm.smith.roundtrip import diff as rt_diff          # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter            # noqa: E402

REG = Registry.load()
CNT = HeuristicCounter()

#: 测试用 IR：只有主体 + 画幅 + 媒介 → setting / camera / lighting / material 都是 OPEN。
SIMPLE = {
    "ir_version": "0.1", "task": "t2i", "canvas": {"aspect_ratio": "3:2"},
    "entities": [{
        "id": "e1", "kind": "character", "label": "woman in a yellow raincoat",
        "importance": 1.0, "age_years": 28,
        "descriptors": {"gender": "woman", "wardrobe": ["a yellow raincoat"]},
        "spatial": {"anchor": "left third"},
    }],
    "style": {"medium": "photograph"},
}

#: 中文口语 IR：背景与主体标签是中文 → 翻译步有可判对象（术语表能命中）。
CHINESE = {
    "ir_version": "0.1", "task": "t2i",
    "canvas": {"aspect_ratio": "3:2",
               "background": "雨后的窄巷，霓虹招牌的倒影落在湿沥青上"},
    "entities": [{
        "id": "e1", "kind": "character", "label": "穿黄雨衣的女人",
        "importance": 1.0, "age_years": 28,
        "descriptors": {"wardrobe": ["a yellow raincoat"]},
        "spatial": {"anchor": "left third"},
    }],
    "style": {"medium": "photograph"},
}

#: 翻译步「好答案」：术语全翻、引号内容没动、事实不变。
TRANSLATED = (
    "A 28-year-old woman in a yellow raincoat, in the left third. "
    "A narrow alley after rain, a neon sign reflected on wet asphalt. "
    "Soft overcast daylight from the left, a 35mm lens at eye level. Photograph.")


def simple_ir() -> IR:
    return IR.from_dict(SIMPLE)


def chinese_ir() -> IR:
    return IR.from_dict(CHINESE)


def det_base(ir: IR, target: str = "moss-image") -> str:
    """该方言的确定性草稿——拆步管线的起点，也是「关掉任一步仍合法」的底。"""
    return get_adapter(target, REG, counter=CNT).realize(ir).positive


class _Scripted:
    """按 system prompt 里的 STEP_MARKERS 分派回包（不依赖调用顺序）。

    ``per_step`` = {步名: [回包, ...]}，先进先出；没有回包时返回空串
    （模拟模型什么都没给——管线必须降级而不是炸）。
    """

    def __init__(self, per_step: dict):
        self.queues = {k: list(v) for k, v in (per_step or {}).items()}
        self.calls: list = []

    def chat(self, system, user, **kw):
        self.calls.append((system, user))
        for step, marker in steps.STEP_MARKERS.items():
            if marker in system:
                queue = self.queues.get(step) or []
                return queue.pop(0) if queue else ""
        return ""


class _StepCase(unittest.TestCase):
    """公共夹具：英文 IR 的确定性草稿 + 一份「只填 OPEN 槽位」的合法补全产物。"""

    def setUp(self):
        self.ir = simple_ir()
        self.base = det_base(self.ir)
        self.open_dims = expand.open_slot_dims(build_clauses(self.ir))
        self.filled = self.base + (" Soft overcast daylight from the left, "
                                   "a 35mm lens at eye level, wet asphalt.")

    def _adapter(self, per_step, enabled, candidates=1, retries=0):
        model = _Scripted(per_step)
        ad = get_adapter("moss-image", REG, counter=CNT, llm=model)
        ad.gen_candidates, ad.gen_retries = candidates, retries
        ad.expand_steps = enabled
        return ad, model

    def assertLegal(self, ir, out, ad=None):
        """「合法」= 体检不 fail + roundtrip 零丢失 + 不变式全绿。"""
        self.assertNotEqual(out.expand["verdict"], "fail", out.expand["issues"])
        d = rt_diff(ir, out.positive)
        self.assertTrue(d.ok, d.lines())
        if ad is not None:
            bad = [r.code + ":" + r.detail for r in INV.run(ad, ir) if not r.ok]
            self.assertEqual(bad, [], "不变式不应因为拆步/关步而变红")

    @staticmethod
    def by_step(out) -> dict:
        return {row["step"]: row for row in out.expand["steps"]}


# ── 1. 补全 ───────────────────────────────────────────────────

class TestCompletionStep(_StepCase):
    def test_fills_open_slots_traceably(self):
        """补全填的每一项都能在默认值库里找到；空着的槽位只提示、不罚。"""
        r = steps.check_completion(self.ir, self.base, self.filled,
                                   style="photograph", open_dims=self.open_dims)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.verdict, "warn")          # 只有「仍空着」的提示
        self.assertEqual(r.metrics["fill_attempts"], ["camera", "lighting", "material"])
        self.assertEqual([d for d, _ in r.metrics["traceable_fills"]],
                         ["camera", "lighting", "material"])
        self.assertEqual(r.metrics["untraceable_fills"], [])
        self.assertIn("setting", " ".join(r.warns))  # 没填的槽位要说出来

    def test_flags_invented_number(self):
        """补全最典型的失败就是「补」出一个数字（IR 里没有的数量/时间）。"""
        bad = self.base + " 3 wooden crates sit in the corner."
        r = steps.check_completion(self.ir, self.base, bad,
                                   style="photograph", open_dims=self.open_dims)
        self.assertEqual(r.verdict, "fail")
        self.assertTrue(any("凭空造数" in e and "3" in e for e in r.errors))

    def test_flags_untraceable_fill(self):
        """填了 OPEN 槽位，但填的内容不在该风格的默认值库里 → error，不是静默通过。"""
        bad = (self.base + " An aerial drone shot with an 8mm fisheye lens"
                           " under harsh magenta strobe light.")
        r = steps.check_completion(self.ir, self.base, bad,
                                   style="photograph", open_dims=self.open_dims)
        self.assertEqual(r.verdict, "fail")
        self.assertEqual(sorted(r.metrics["untraceable_fills"]), ["camera", "lighting"])
        self.assertTrue(any("不可追溯" in e for e in r.errors))

    def test_off_still_legal(self):
        """关掉补全：管线从确定性草稿直接走翻译/改写，产物仍合法。"""
        ad, model = self._adapter({steps.STEP_REWRITE: [self.filled]},
                                  (steps.STEP_TRANSLATION, steps.STEP_REWRITE))
        out = ad.realize(self.ir, mode="llm")
        row = self.by_step(out)["completion"]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["verdict"], "skipped")
        self.assertEqual(row["ran"], False)
        self.assertLegal(self.ir, out, ad)


# ── 2. 翻译 ───────────────────────────────────────────────────

class TestTranslationStep(_StepCase):
    def setUp(self):
        super().setUp()
        self.cir = chinese_ir()
        self.cbase = det_base(self.cir)
        self.cfill = self.cbase + (" Soft overcast daylight from the left,"
                                   " a 35mm lens at eye level.")

    def test_maps_glossary_terms(self):
        """中文口语术语全部命中术语表 → 命中率 1.0，且分母是「源文里真出现的术语」。"""
        r = steps.check_translation(self.cir, self.cbase, TRANSLATED)
        self.assertEqual(r.verdict, "pass", r.errors + r.warns)
        self.assertEqual(r.metrics["hit_rate"], 1.0)
        self.assertEqual(r.metrics["misses"], [])
        self.assertEqual(r.metrics["applicable_terms"],
                         ["黄雨衣", "雨后", "霓虹", "招牌", "倒影", "湿沥青"])

    def test_flags_untranslated_terms(self):
        """翻译步最典型的失败：术语没翻（或翻了又漏回去）——必须点名是哪个词。"""
        bad = TRANSLATED.replace("a neon sign", "霓虹招牌")
        r = steps.check_translation(self.cir, self.cbase, bad)
        self.assertEqual(r.verdict, "fail")
        joined = "；".join(r.errors)
        self.assertIn("未翻译", joined)
        self.assertIn("霓虹", joined)
        self.assertIn("招牌", joined)
        self.assertLess(r.metrics["hit_rate"], 1.0)

    def test_not_applicable_without_chinese(self):
        """源文本没有中文术语时分母为 0：判「不适用」，**不许按 100% 记成通过**。"""
        r = steps.check_translation(self.ir, self.base, self.filled)
        self.assertFalse(r.applicable)
        self.assertEqual(r.verdict, "skipped")
        self.assertEqual(r.metrics["applicable_terms"], [])
        self.assertIn("不适用", r.note)
        self.assertNotIn("hit_rate", r.metrics)      # 分母为 0 的比率不产出

    def test_off_still_legal(self):
        """关掉翻译：补全 + 改写照跑，产物仍合法（只是可能残留中文）。"""
        ad, model = self._adapter({steps.STEP_COMPLETION: [self.filled],
                                   steps.STEP_REWRITE: [self.filled]},
                                  (steps.STEP_COMPLETION, steps.STEP_REWRITE))
        out = ad.realize(self.ir, mode="llm")
        row = self.by_step(out)["translation"]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["verdict"], "skipped")
        self.assertLegal(self.ir, out, ad)


# ── 3. 改写 ───────────────────────────────────────────────────

class TestRewriteStep(_StepCase):
    SRC = ("Soft overcast daylight from the left. A 28-year-old woman in a yellow "
           "raincoat stands in a narrow alley.")
    SWAPPED = ("A 28-year-old woman in a yellow raincoat stands in a narrow alley. "
               "Soft overcast daylight from the left.")

    def test_reordering_keeps_facts(self):
        """只重排：事实集合不变、词袋不变 → pass；同时能量到「顺序真的变了」。"""
        r = steps.check_rewrite(self.ir, self.SRC, self.SWAPPED)
        self.assertEqual(r.verdict, "pass", r.errors + r.warns)
        self.assertTrue(r.metrics["facts_equal"])
        self.assertEqual(r.metrics["word_retention"], 1.0)
        self.assertTrue(r.metrics["order_changed"])

    def test_flags_changed_age(self):
        """改写步最典型的失败：重排时把年龄改了（换了一个人）。"""
        bad = self.SWAPPED.replace("28-year-old", "47-year-old")
        r = steps.check_rewrite(self.ir, self.SRC, bad)
        self.assertEqual(r.verdict, "fail")
        joined = "；".join(r.errors)
        self.assertIn("丢了事实", joined)
        self.assertIn("28", joined)
        self.assertFalse(r.metrics["facts_equal"])

    def test_flags_dropped_quoted_text(self):
        """改写步第二典型失败：重排时把图内文字弄丢（引号内容必须逐字还在）。"""
        src = 'A poster reads "限时特惠". A 28-year-old woman waits.'
        r = steps.check_rewrite(self.ir, src, "A 28-year-old woman waits.")
        self.assertEqual(r.verdict, "fail")
        self.assertTrue(any("丢了事实" in e and "限时特惠" in e for e in r.errors))

    def test_off_still_legal(self):
        """关掉改写：补全产物直接交付，产物仍合法。"""
        ad, model = self._adapter({steps.STEP_COMPLETION: [self.filled]},
                                  (steps.STEP_COMPLETION,))
        out = ad.realize(self.ir, mode="llm")
        row = self.by_step(out)["rewrite"]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["verdict"], "skipped")
        self.assertLegal(self.ir, out, ad)


# ── 4. 归因 / 开关表 / 默认路径 ───────────────────────────────

class TestReportAttribution(_StepCase):
    #: 旧版 report 的键。新增 stage 相关字段不许动它们。
    OLD_KEYS = {
        "target", "words", "tokens", "band", "fixed_total", "fixed_hits",
        "fixed_recall", "misses", "hard_misses", "extra_numbers", "extra_quotes",
        "quality", "negations", "ratio_in_prose", "density", "verdict",
        "candidates", "band_distance", "issues",
    }

    def test_names_the_completion_step(self):
        """错误由补全引入 → stage 指向 completion，且 errors 带上步名前缀。"""
        bad = self.base + " 3 wooden crates sit in the corner."
        ad, model = self._adapter({steps.STEP_COMPLETION: [bad],
                                   steps.STEP_REWRITE: [bad]},
                                  steps.STEP_ORDER)
        out = ad.realize(self.ir, mode="llm")
        d = out.expand
        self.assertEqual(d["verdict"], "fail")
        self.assertEqual(d["stage"], steps.STEP_COMPLETION)
        self.assertIn(steps.STEP_COMPLETION, d["stage_issues"])
        self.assertTrue(any("凭空造数" in i for i in d["stage_issues"][steps.STEP_COMPLETION]))
        self.assertTrue(any(str(i).startswith("[补全]") for i in d["issues"]))
        self.assertTrue(self.OLD_KEYS <= set(d))     # 旧键一个没少

    def test_names_the_translation_step(self):
        """事实都对、只有术语没翻 → 最终 verify 看不出来，靠步级判据归因到翻译。"""
        bad = TRANSLATED.replace("a neon sign", "霓虹招牌")
        cbase = det_base(chinese_ir())
        cfill = cbase + " Soft overcast daylight from the left, a 35mm lens at eye level."
        ad, model = self._adapter({steps.STEP_COMPLETION: [cfill],
                                   steps.STEP_TRANSLATION: [bad],
                                   steps.STEP_REWRITE: [bad]},
                                  steps.STEP_ORDER)
        out = ad.realize(chinese_ir(), mode="llm")
        d = out.expand
        self.assertEqual(d["stage"], steps.STEP_TRANSLATION)
        self.assertIn(steps.STEP_TRANSLATION, d["stage_issues"])
        self.assertTrue(any("未翻译" in i for i in d["stage_issues"][steps.STEP_TRANSLATION]))
        self.assertEqual(d["verdict"], "fail")

    def test_switch_table_visible_in_report(self):
        """每一步都在 steps 里留痕（关掉的也要在），否则没法回答「这步跑没跑」。"""
        ad, model = self._adapter({steps.STEP_REWRITE: [self.base]},
                                  (steps.STEP_REWRITE,))
        out = ad.realize(self.ir, mode="llm")
        rows = out.expand["steps"]
        self.assertEqual([r["step"] for r in rows], list(steps.STEP_ORDER))
        by = self.by_step(out)
        self.assertFalse(by["completion"]["enabled"])
        self.assertFalse(by["translation"]["enabled"])
        self.assertTrue(by["rewrite"]["enabled"])
        self.assertTrue(by["rewrite"]["ran"])
        self.assertEqual(by["rewrite"]["verdict"], "pass")
        self.assertIn("拆步模式", out.expand["stage_note"])
        self.assertEqual(len(model.calls), 1)        # 关掉的步不调模型

    def test_all_steps_off_is_deterministic_and_legal(self):
        """三步全关 = 直接交付确定性草稿：零调用、逐字节等于 deterministic 输出、合法。"""
        ad, model = self._adapter({}, ())
        out = ad.realize(self.ir, mode="llm")
        self.assertEqual(model.calls, [])
        self.assertEqual(out.positive, det_base(self.ir))
        self.assertTrue(all(r["enabled"] is False for r in out.expand["steps"]))
        self.assertLegal(self.ir, out, ad)

    def test_unknown_step_name_fails_loudly(self):
        """开关写错要有人看见，不能静默当「没开」。"""
        ad, model = self._adapter({}, ("translate",))
        with self.assertRaises(ValueError):
            ad.realize(self.ir, mode="llm")

    def test_attach_to_report_is_reachable_and_typed(self):
        """F821 的两面：注解必须能解析（不能 NameError），且拆步管线真的走到它。"""
        import typing
        hints = typing.get_type_hints(steps.attach_to_report)
        self.assertIs(hints["return"], expand.Report)
        ad, model = self._adapter({steps.STEP_COMPLETION: [self.filled]},
                                  (steps.STEP_COMPLETION,))
        out = ad.realize(self.ir, mode="llm")
        self.assertTrue(out.expand["steps"])                 # attach_to_report 写进去的
        self.assertEqual(out.expand["steps"][0]["step"], steps.STEP_COMPLETION)

    def test_non_sequence_switch_fails_loudly(self):
        """误写布尔/数字/dict → 带说明的 ValueError（不是 TypeError）；合法形态仍接受。"""
        ad = get_adapter("moss-image", REG, counter=CNT, llm=_Scripted({}))
        for bad in (True, 1, {"completion": True}):
            ad.expand_steps = bad
            with self.assertRaises(ValueError) as ctx:
                ad.split_steps()
            msg = str(ctx.exception)
            self.assertIn("expand_steps", msg)     # 说清是哪个开关
            self.assertIn("completion", msg)       # 列出可用步名
            with self.assertRaises(ValueError):    # 走 realize 的 llm 路径同样报错
                ad.realize(self.ir, mode="llm")
        for good in ("rewrite,completion", ("rewrite", "completion"),
                     ["rewrite", "completion"]):
            ad.expand_steps = good
            self.assertEqual(ad.split_steps(), ("completion", "rewrite"))
        for off in (None, False):
            ad.expand_steps = off
            self.assertIsNone(ad.split_steps())


class TestDefaultPathUnchanged(unittest.TestCase):
    """默认路径（expand_steps=None）必须还是历史行为：一次调用、无步级判据。"""

    LEGACY_GOOD = (
        "A 28-year-old woman in a yellow raincoat stands on wet asphalt, weight on one leg, "
        "her face turned up to an overcast sky. A narrow backstreet recedes behind her, shop "
        "signs doubled in the puddles. Soft daylight from the left, blue hour, thin mist.")

    class _Mock:
        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = []

        def chat(self, system, user, **kw):
            self.calls.append((system, user))
            return self.replies.pop(0) if self.replies else ""

    def test_legacy_llm_mode_is_one_call_with_single_stage(self):
        mock = self._Mock([self.LEGACY_GOOD])
        ad = get_adapter("moss-image", REG, counter=CNT, llm=mock)
        ad.gen_candidates, ad.gen_retries = 1, 0
        out = ad.realize(IR.load(ROOT / "moss_llm" / "smith" / "examples"
                                 / "01-portrait-photoreal.json"),
                         mode="llm", use_layout=False)
        self.assertEqual(len(mock.calls), 1)         # 不拆 = 不多调模型
        self.assertEqual(out.expand["stage"], "single")
        self.assertEqual(out.expand["stage_issues"], {})
        self.assertEqual(out.expand["steps"], [])    # 没有步级结果
        self.assertIn("无法归因", out.expand["stage_note"])
        # 旧键语义不变
        self.assertNotEqual(out.expand["verdict"], "fail")
        self.assertIn("fixed_recall", out.expand)

    def test_deterministic_mode_ignores_split_switch(self):
        """拆步开关只在 llm 模式内有意义：确定性输出逐字节不变、不调模型。"""
        class _Boom:
            def chat(self, system, user, **kw):
                raise AssertionError("确定性模式不许调模型")

        ad = get_adapter("moss-image", REG, counter=CNT, llm=_Boom())
        ad.expand_steps = steps.STEP_ORDER
        ir = simple_ir()
        det = ad.realize(ir)
        self.assertEqual(det.positive, det_base(ir))
        self.assertIsNone(det.expand)


if __name__ == "__main__":
    unittest.main(verbosity=2)
