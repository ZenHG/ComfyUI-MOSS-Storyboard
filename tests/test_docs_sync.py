# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/check_docs_sync.py` 的单测：零 GPU、零网络。

钉住三件事：
1. 两份**真实** README 当前必须同步（绿）；
2. 破坏结构必须红：删一节 / 多一节 / 改层级 / 两节换序 / H1 缺失；
3. 例外机制可用但必须写理由，**过期例外要自己报出来**（防止例外清单变成绕过闸的后门）。

跑：python tests/test_docs_sync.py -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "check_docs_sync", ROOT / "tools" / "check_docs_sync.py")
cds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cds)

EN_OK = """# Title

Intro.

## 1 · Install

### 1.1 Requirements

text

### 1.2 Install

text

## 2 · Use

### 2.1 First image

text

### 2.2 Ideas

text
"""

ZH_OK = """# 标题

引言。

## 1 · 装

### 1.1 前提

正文

### 1.2 安装

正文

## 2 · 用

### 2.1 第一张图

正文

### 2.2 点子

正文
"""


def _write_pair(tmp: str, en: str, zh: str) -> tuple[str, str]:
    en_path = os.path.join(tmp, "README.md")
    zh_path = os.path.join(tmp, "README_ZH.md")
    with open(en_path, "w", encoding="utf-8") as fh:
        fh.write(en)
    with open(zh_path, "w", encoding="utf-8") as fh:
        fh.write(zh)
    return en_path, zh_path


def _problems(en: str, zh: str, exceptions=None) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        en_path, zh_path = _write_pair(tmp, en, zh)
        problems, _, _ = cds.compare_docs(en_path, zh_path, exceptions)
        return problems


class TestParser(unittest.TestCase):
    def test_parse_levels_and_numbers(self):
        heads = cds.parse_headings(EN_OK)
        self.assertEqual([h["level"] for h in heads], [1, 2, 3, 3, 2, 3, 3])
        self.assertEqual([h["num"] for h in heads],
                         [None, "1", "1.1", "1.2", "2", "2.1", "2.2"])

    def test_headings_inside_code_fence_are_ignored(self):
        text = "# T\n\n```bash\n# not a heading\n## also not\n```\n\n## Real\n"
        heads = cds.parse_headings(text)
        self.assertEqual([h["title"] for h in heads], ["T", "Real"])

    def test_numeric_prefix_variants(self):
        self.assertEqual(cds.numeric_prefix("1 · Install"), "1")
        self.assertEqual(cds.numeric_prefix("2.5 Error table"), "2.5")
        self.assertIsNone(cds.numeric_prefix("Terms (one concept, one name)"))


class TestRealReadmes(unittest.TestCase):
    def test_real_readmes_are_in_sync(self):
        problems, en, zh = cds.compare_docs(str(ROOT / "README.md"),
                                            str(ROOT / "README_ZH.md"))
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(en), 20)
        self.assertEqual(len(en), len(zh))


class TestBrokenPair(unittest.TestCase):
    def test_missing_section_in_zh_is_red(self):
        broken = ZH_OK.replace("### 2.2 点子\n\n正文\n", "")
        problems = _problems(EN_OK, broken)
        self.assertTrue(any("节数不一致" in p for p in problems), problems)

    def test_extra_section_in_en_is_red(self):
        extra = EN_OK + "\n### 2.3 Bonus\n\ntext\n"
        problems = _problems(extra, ZH_OK)
        self.assertTrue(any("节数不一致" in p for p in problems), problems)

    def test_level_change_is_red(self):
        broken = ZH_OK.replace("### 1.2 安装", "#### 1.2 安装")
        problems = _problems(EN_OK, broken)
        self.assertTrue(any("层级不一致" in p for p in problems), problems)

    def test_swapped_numbered_sections_are_red(self):
        broken = (ZH_OK.replace("### 1.1 前提", "### TEMP")
                  .replace("### 1.2 安装", "### 1.1 前提")
                  .replace("### TEMP", "### 1.2 安装"))
        problems = _problems(EN_OK, broken)
        self.assertTrue(any("编号不一致" in p for p in problems), problems)

    def test_missing_h1_is_red(self):
        broken = EN_OK.replace("# Title\n", "Title\n", 1)
        problems = _problems(broken, ZH_OK)
        self.assertTrue(any("H1 数为 0" in p for p in problems), problems)


class TestExceptions(unittest.TestCase):
    def test_exception_with_reason_allows_one_sided_section(self):
        en = EN_OK + "\n## Appendix (EN only)\n\ntext\n"
        exc = [{"en": "Appendix", "zh": "", "why": "英文附录只在 EN 维护，中文版引用全文"}]
        self.assertEqual(_problems(en, ZH_OK, exc), [])

    def test_exception_without_reason_is_red(self):
        en = EN_OK + "\n## Appendix (EN only)\n\ntext\n"
        exc = [{"en": "Appendix", "zh": "", "why": ""}]
        problems = _problems(en, ZH_OK, exc)
        self.assertTrue(any("例外缺理由" in p for p in problems), problems)

    def test_stale_exception_is_reported(self):
        exc = [{"en": "No such section", "zh": "", "why": "历史遗留"}]
        problems = _problems(EN_OK, ZH_OK, exc)
        self.assertTrue(any("过期例外" in p for p in problems), problems)


class TestCli(unittest.TestCase):
    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            en_path, zh_path = _write_pair(tmp, EN_OK, ZH_OK)
            self.assertEqual(cds.main(["--en", en_path, "--zh", zh_path]), 0)
            bad_path = os.path.join(tmp, "ZH_bad.md")
            Path(bad_path).write_text(
                ZH_OK.replace("### 2.2 点子\n\n正文\n", ""), encoding="utf-8")
            self.assertEqual(cds.main(["--en", en_path, "--zh", bad_path]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
