# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""T7 · 跨家族 registry 验证：只加一份 YAML 就接入一个新图像家族。

跑：python -m unittest tests.test_registry_family -v

## 这条测试要证明什么

不是「nano-banana 这个后端能用」（没有 API、没实跑），而是：

1. **新家族只靠 `data/model-registry.yaml` 进来**——代码里没有它的 adapter
   （`ADAPTERS` 表里没有），走的是 generic 兜底；
2. 同一套 IR（4 份真机反推 fixtures）对这个新方言**编译成功、不变式全绿、
   固定项零丢失**——也就是 registry 驱动真的可扩展，而不是「加一行配置但
   还得改 Python 才通」；
3. 方言声明是**自洽**的：prompt.mode/verbosity/language/长度带/负向/布局/
   采样/pitfalls 都在，source 是 URL（可复核），置信度与核验方式写实。

## 边界

- 没有实跑这个家族（无 API key）；`confidence: medium` 就是「文档级核验」，
  不能拿它当「实测通过」。
- 4 份 IR 来自 `tests/fixtures/smith/`（真机反推的 IR）；这里只验编译层，
  不验出图。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.adapters import ADAPTERS, GenericAdapter, get_adapter  # noqa: E402
from moss_llm.smith.api import compile_ir                                  # noqa: E402
from moss_llm.smith.invariants import run as run_invariants                # noqa: E402
from moss_llm.smith.ir import IR                                          # noqa: E402
from moss_llm.smith.registry import Registry                              # noqa: E402
from moss_llm.smith.roundtrip import roundtrip                            # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter                        # noqa: E402

REG = Registry.load()
CNT = HeuristicCounter()

#: 新家族（只存在于 YAML）。名字改了这里和 registry 要一起改。
TARGET = "nano-banana"

#: 四份真机反推 IR（比手写示例脏：多格漫画重复实体、UI 截图 8 个文字块等）。
IR_FILES = ("comic-4panel-lineart-sample.ir.json", "faceswap-demo-ref.ir.json",
            "hero-storyboard.ir.json", "ui-smoke.ir.json")
FIX = ROOT / "tests" / "fixtures" / "smith"

#: 接入前后必须原样保留的七个后端（新家族是**新增**，不是替换）。
OLD_TARGETS = {"moss-image", "qwen-image-2_1", "krea2", "flux3", "flux1_dev",
               "sdxl", "minimax-h3"}


class TestRegistryOnlyFamily(unittest.TestCase):
    """只加 YAML 的家族：注册、声明、渲染、不变式、往返。"""

    # ── ① registry 里有它，且是「只靠 YAML」进来的 ────────────
    def test_target_is_registered(self):
        self.assertIn(TARGET, REG.names())
        self.assertEqual(REG.names(), sorted(OLD_TARGETS | {TARGET}))

    def test_no_python_adapter_exists_for_it(self):
        """纯 YAML 的判据：ADAPTERS（原生实现表）里没有它，get_adapter 退到 generic。"""
        self.assertNotIn(TARGET, ADAPTERS)
        ad = get_adapter(TARGET, REG, counter=CNT)
        self.assertIsInstance(ad, GenericAdapter)
        self.assertEqual(ad.name, TARGET)

    def test_capability_block_is_complete(self):
        caps = REG.get(TARGET)
        for key in ("display", "vendor", "confidence", "source", "encoder",
                    "prompt", "negative", "layout", "edit", "sampling", "pitfalls"):
            self.assertIn(key, caps, "缺能力的声明项：%s" % key)
        policy = caps["prompt"]
        for key in ("mode", "verbosity", "language", "length", "quality_words",
                    "weight_syntax", "structured_output"):
            self.assertIn(key, policy, "缺 prompt 方言项：%s" % key)
        bands = policy["length"]
        self.assertEqual(set(bands), {"brief", "standard", "detailed"})
        for name, band in bands.items():
            self.assertEqual(len(band), 2, name)
            self.assertLess(band[0], band[1], name)

    def test_sources_are_reviewable_urls(self):
        """source 必须是 URL——「置信度」没有可复核的来源就是自说自话。"""
        sources = REG.get(TARGET)["source"]
        self.assertTrue(sources)
        for url in sources:
            self.assertRegex(url, r"^https?://\S+$")
        self.assertTrue(any("google" in u for u in sources))

    def test_confidence_is_documented_not_measured(self):
        """没有实跑就不许写 high：这条家族是文档级核验。"""
        caps = REG.get(TARGET)
        self.assertEqual(caps["confidence"], "medium")
        # 方言声明里要能看出口径（叙事段落，不是 tag）
        self.assertEqual(caps["prompt"]["mode"], "prose")
        self.assertEqual(caps["prompt"]["language"], "en")

    def test_declared_dialect_differences_are_explicit(self):
        """与现有家族的关键差异必须写进 registry，而不是只写在文档里。"""
        caps = REG.get(TARGET)
        self.assertFalse(caps["negative"]["supported"])      # API 无负向参数
        self.assertFalse(caps["layout"]["supported"])        # 无 bbox/区域提示词
        self.assertEqual(caps["prompt"]["weight_syntax"], "none")
        self.assertIsNone(caps["encoder"]["max_tokens"])     # 不编造 token 上限
        self.assertTrue(caps["pitfalls"])

    # ── ② 4 份真机 IR：编译成功、不变式绿、往返零丢 ────────────
    def _irs(self):
        files = [FIX / name for name in IR_FILES]
        self.assertEqual(len(files), 4, "回归集必须是 4 份")
        for f in files:
            self.assertTrue(f.is_file(), "缺 IR 夹具：%s" % f.name)
        return [(f.name, IR.load(f)) for f in files]

    def test_four_real_irs_compile(self):
        for name, _ir in self._irs():
            # 走对外门面（节点用的就是这条路径）：给文件路径，与用户操作一致
            res = compile_ir(str(FIX / name), TARGET, registry=REG, counter=CNT)
            self.assertEqual(res["target"], TARGET)
            self.assertTrue(res["positive"].strip(), name)
            self.assertGreater(res["token_count"], 0, name)
            self.assertTrue(res["invariants_ok"], "%s：不变式不绿" % name)
            self.assertFalse([i for i in res["issues"] if i["level"] == "error"],
                             "%s：lint 有 error：%s" % (name, res["issues"]))

    def test_invariants_are_green(self):
        ad = get_adapter(TARGET, REG, counter=CNT)
        for name, ir in self._irs():
            res = run_invariants(ad, ir)
            bad = [str(r) for r in res if not r.ok]
            self.assertEqual(bad, [], "%s × %s：%s" % (name, TARGET, bad))

    def test_roundtrip_loses_no_fixed_items(self):
        ad = get_adapter(TARGET, REG, counter=CNT)
        for name, ir in self._irs():
            d, _out = roundtrip(ir, ad)
            self.assertEqual(d.lost, [], "%s × %s 丢事实：%s" % (name, TARGET, d.lost))

    def test_compile_is_deterministic(self):
        """确定性是「registry 驱动」的前提：同一 IR 两次编译必须逐字节相同。"""
        ad = get_adapter(TARGET, REG, counter=CNT)
        for name, ir in self._irs():
            self.assertEqual(ad.realize(ir).positive, ad.realize(ir).positive, name)

    def test_rendered_prose_is_not_empty_and_not_json(self):
        ad = get_adapter(TARGET, REG, counter=CNT)
        for name, ir in self._irs():
            out = ad.realize(ir)
            self.assertGreaterEqual(len(out.positive.split()), 40, name)
            self.assertFalse(out.positive.lstrip().startswith("{"), name)

    # ── ③ CLI 能列出（`targets` 读的就是 registry） ────────────
    def test_cli_targets_lists_the_family(self):
        from moss_llm.smith.cli import cmd_targets

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_targets(argparse.Namespace(registry=None))
        self.assertEqual(rc, 0)
        listing = buf.getvalue()
        self.assertIn(TARGET, listing)
        for old in OLD_TARGETS:
            self.assertIn(old, listing, "旧后端从 targets 列表里掉了：%s" % old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
