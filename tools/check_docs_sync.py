#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""README 双语节级同步闸：`README.md` ↔ `README_ZH.md` 的标题必须成对、层级与顺序一致。

为什么需要它：中英两份 README 是**手工维护**的，历史上已经漂移过——英文加了一节、中文
忘了加，读者按语言看到的是两个产品。逐节对译没法机检（译者措辞必然不同），但
**结构可以机检**：标题层级、编号、顺序、有无。

判据（可机检的部分）：
1. 两份文档的标题**数量**一致；
2. 同一位置（跳过显式例外后）的标题**层级**一致（`##` 不能对 `###`）；
3. 带编号的标题**编号序列**一致（`1.2` 不能对 `1.3`）——这条能抓住「两节顺序反了」；
4. 显式例外必须写理由，且例外条目不能是「过期例外」（两边都找不到对应标题）。

**这里不判**：标题文字是否互译正确、正文内容是否等价 —— 那是人/ZH 审校的事，
不要假装它能判。

用法：
    python tools/check_docs_sync.py            # 绿/红 + 节表
    python tools/check_docs_sync.py --json     # 机读结果
    python tools/check_docs_sync.py --explain  # 打印 EN/ZH 逐节对照表
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EN = "README.md"
DEFAULT_ZH = "README_ZH.md"

#: 显式例外：允许只在一侧出现的节。**每条必须写 why**，否则闸红。
#: 格式：{"en": "EN 标题前缀", "zh": "ZH 标题前缀", "why": "理由"}
#: 允许一侧留空字符串（表示该节只应存在于另一侧）。
#: 当前为空：两份 README 结构完全成对。
EXCEPTIONS: list[dict] = []

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_NUM = re.compile(r"^(\d+(?:\.\d+)*)\s*[·.)、]?\s+")


def parse_headings(text: str, source: str = "") -> list[dict]:
    """解析 markdown 标题；跳过代码围栏里的 `#`（那是示例，不是结构）。"""
    out: list[dict] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEAD.match(line)
        if not m:
            continue
        title = m.group(2).strip()
        out.append({"level": len(m.group(1)), "title": title,
                    "line": lineno, "num": numeric_prefix(title),
                    "source": source})
    return out


def numeric_prefix(title: str) -> str | None:
    """标题开头的编号（`1` / `1.2` / `2.5`）；没有编号返回 None。"""
    m = _NUM.match(title or "")
    return m.group(1) if m else None


def _match_prefix(heads: list[dict], prefix: str) -> int | None:
    if not prefix:
        return None
    for i, h in enumerate(heads):
        if h["title"].startswith(prefix):
            return i
    return None


def _apply_exceptions(en: list[dict], zh: list[dict],
                      exceptions: list[dict] | None = None):
    """把例外条目从两侧序列里摘掉。返回 (en_kept, zh_kept, problems)。"""
    problems: list[str] = []
    en_kept = list(en)
    zh_kept = list(zh)
    for exc in (EXCEPTIONS if exceptions is None else exceptions):
        why = (exc.get("why") or "").strip()
        if not why:
            problems.append("例外缺理由：%r" % (exc,))
            continue
        en_i = _match_prefix(en_kept, (exc.get("en") or "").strip())
        zh_i = _match_prefix(zh_kept, (exc.get("zh") or "").strip())
        if en_i is None and zh_i is None:
            problems.append("过期例外（两侧都没有对应标题）：%r" % (exc,))
            continue
        # 先删大下标，避免位移
        for idx, seq in sorted(((en_i, en_kept), (zh_i, zh_kept)),
                               key=lambda kv: -1 if kv[0] is None else -kv[0]):
            if idx is not None and 0 <= idx < len(seq):
                seq.pop(idx)
    return en_kept, zh_kept, problems


def compare_headings(en: list[dict], zh: list[dict],
                     exceptions: list[dict] | None = None) -> list[str]:
    """比较两份标题序列。返回问题列表（空 = 同步）。"""
    problems: list[str] = []
    if not en:
        problems.append("README.md 没有解析到任何标题（正则是空的？）")
    if not zh:
        problems.append("README_ZH.md 没有解析到任何标题（正则是空的？）")
    if problems:
        return problems

    en_kept, zh_kept, exc_problems = _apply_exceptions(en, zh, exceptions)
    problems.extend(exc_problems)

    # ① 成对数量
    if len(en_kept) != len(zh_kept):
        problems.append("节数不一致：README.md %d 节 / README_ZH.md %d 节（差 %d）"
                        % (len(en_kept), len(zh_kept), abs(len(en_kept) - len(zh_kept))))

    # ② H1 必须各一个
    for name, seq in (("README.md", en_kept), ("README_ZH.md", zh_kept)):
        h1 = [h for h in seq if h["level"] == 1]
        if len(h1) != 1:
            problems.append("%s 的 H1 数为 %d（应为 1）" % (name, len(h1)))

    # ③ 逐节：层级 + 编号
    for i, (a, b) in enumerate(zip(en_kept, zh_kept, strict=False), 1):
        if a["level"] != b["level"]:
            problems.append("第 %d 节层级不一致：EN H%d %r（行 %d） vs ZH H%d %r（行 %d）"
                            % (i, a["level"], a["title"], a["line"],
                               b["level"], b["title"], b["line"]))
            continue
        if a["num"] != b["num"]:
            problems.append("第 %d 节编号不一致：EN %r vs ZH %r（%r / %r）"
                            % (i, a["num"], b["num"], a["title"], b["title"]))
    return problems


def compare_docs(en_path: str, zh_path: str,
                 exceptions: list[dict] | None = None) -> tuple[list[str], list, list]:
    """读两份文件并比较。返回 (problems, en_headings, zh_headings)。"""
    with open(en_path, encoding="utf-8") as fh:
        en = parse_headings(fh.read(), source=os.path.basename(en_path))
    with open(zh_path, encoding="utf-8") as fh:
        zh = parse_headings(fh.read(), source=os.path.basename(zh_path))
    return compare_headings(en, zh, exceptions), en, zh


def _explain(en: list[dict], zh: list[dict]) -> str:
    lines = ["%-4s %-42s | %-4s %s" % ("EN", "title", "ZH", "title"),
             "-" * 96]
    for i in range(max(len(en), len(zh))):
        a = en[i] if i < len(en) else None
        b = zh[i] if i < len(zh) else None
        lines.append("%-4s %-42s | %-4s %s"
                     % ("H%d" % a["level"] if a else "-",
                        (a["title"][:40] if a else ""),
                        "H%d" % b["level"] if b else "-",
                        (b["title"][:44] if b else "")))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="README 中英节级同步闸")
    ap.add_argument("--en", default=os.path.join(ROOT, DEFAULT_EN))
    ap.add_argument("--zh", default=os.path.join(ROOT, DEFAULT_ZH))
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--explain", action="store_true", help="打印逐节对照表")
    args = ap.parse_args(argv)

    problems, en, zh = compare_docs(args.en, args.zh)
    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems,
                          "en": len(en), "zh": len(zh)}, ensure_ascii=False, indent=2))
    else:
        print("中英 README 节级同步：EN %d 节 / ZH %d 节" % (len(en), len(zh)))
        if args.explain:
            print(_explain(en, zh))
        for p in problems:
            print("  ✗ " + p)
        if not problems:
            print("  ✓ 标题层级/编号/顺序成对（例外 %d 条）" % len(EXCEPTIONS))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
