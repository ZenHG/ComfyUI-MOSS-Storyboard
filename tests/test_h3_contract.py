# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`minimax-h3.rewriter` 8B 契约的**冻结测试**（离线、零权重）。

为什么单独一个文件：2026-11-01 T12 独立复核时临时改坏 registry
（`lora_alpha` 256→512、`arch` qwen3vl→qwen35、`tasks` 加 fl2va）后，
`h3 doctor` 仍 rc=0、既有测试全绿——**契约没有测试看住**。这里把六字段的值钉死：
改任何一项都会红，且报错写清期望值与实际值。

值的来源（docs/61 §2）：repo/base_model/file 来自 HF 元数据；`arch` 来自下载后
GGUF 头实际值；`lora_rank/lora_alpha` 来自官方 adapter_config + GGUF
`adapter.lora.alpha`。本文件**不读权重**，只读 registry，因此 CI 零权重可跑。

跑：`python tests/test_h3_contract.py -v`
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.registry import Registry     # noqa: E402

#: 冻结值（2026-11-01 8B 契约）。任何一项变化都必须同时：改本表 + docs/61 + 迁移说明。
FROZEN = {
    "repo": "lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B",
    "base_model": "Qwen/Qwen3-VL-8B-Instruct",
    "file": "MiniMax-H3-Prompt-Rewriter-LoRA-8B-Q8_0.gguf",
    "arch": "qwen3vl",
    "lora_rank": 256,
    "lora_alpha": 256,
}


class TestRewriterContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = Registry.load()
        cls.rewriter = cls.reg.get("minimax-h3")["rewriter"]

    def _assert_contract(self, rw: dict) -> None:
        for key, want in FROZEN.items():
            self.assertEqual(
                rw.get(key), want,
                "rewriter.%s 被改了：期望 %r，实际 %r（docs/61 §2 是冻结依据）"
                % (key, want, rw.get(key)))

    # ── 六个字段逐个钉（失败信息能直接指出是哪一项被改）──
    def test_repo_is_frozen(self):
        self.assertEqual(self.rewriter.get("repo"), FROZEN["repo"])

    def test_base_model_is_frozen(self):
        self.assertEqual(self.rewriter.get("base_model"), FROZEN["base_model"])

    def test_file_is_frozen(self):
        self.assertEqual(self.rewriter.get("file"), FROZEN["file"])

    def test_arch_is_frozen(self):
        self.assertEqual(self.rewriter.get("arch"), FROZEN["arch"])

    def test_lora_rank_is_frozen(self):
        self.assertEqual(self.rewriter.get("lora_rank"), FROZEN["lora_rank"])

    def test_lora_alpha_is_frozen(self):
        self.assertEqual(self.rewriter.get("lora_alpha"), FROZEN["lora_alpha"])

    # ── tasks 的理由：只覆盖 T2VA 是本仓库实现子集，不是官方不支持 ──
    def test_tasks_is_repository_subset_not_official_limit(self):
        self.assertEqual(self.rewriter.get("tasks"), ["t2va"])
        note = str(self.rewriter.get("note") or "")
        self.assertIn("本仓库", note, "tasks 的理由必须说明是「本仓库实现子集」：%r" % note)
        self.assertIn("docs/61", note, "tasks 的理由必须指向官方能力清单 docs/61 §2：%r" % note)
        for word in ("官方不支持 FL2VA", "FL2VA / Ref2VA 官方标为未实现"):
            self.assertNotIn(word, note, "不得把官方能力写成「不支持 FL2VA」：%r" % note)

    # ── 反证：这套断言真的能发现改坏（而不是永远绿）──
    def test_assertions_catch_each_mutated_field(self):
        for key in FROZEN:
            bad = dict(self.rewriter)
            bad[key] = "MUTATED" if not isinstance(FROZEN[key], int) else 999
            with self.assertRaises(AssertionError, msg="%s 改坏时没有红" % key):
                self._assert_contract(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
