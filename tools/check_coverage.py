# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""覆盖率闸：四份夹具 IR × 七个后端，两件事必须成立。

1. **不丢**：每份 IR 的固定项（图内文字 / 年龄 / 用户指定项）都要出现在每个后端的
   输出里。`hard_misses` 必须为 0。
2. **不编**：输出里不许出现 IR 中没有的数字或引号文字。`extra_numbers` /
   `extra_quotes` 必须为 0。

外加一个**冻结**的覆盖率基线：`coverage(人类可读文本, IR)`。
它不是「效果分」，是回归尺——掉下去说明某个后端开始漏内容。

为什么不放进单测：这两条要对**每个后端**都跑一遍，单测里写会变成 28 个方法；
而且基线是数据（`tests/fixtures/smith/coverage.json`），不是断言。

用法：
    python tools/check_coverage.py --check     # 对账（CI 用）
    python tools/check_coverage.py --freeze    # 重建基线，提交信息里写清为什么变
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.adapters import get_adapter                 # noqa: E402
from moss_llm.smith.decompose import coverage                   # noqa: E402
from moss_llm.smith.expand import (_split_structured,           # noqa: E402
                                   _string_values, verify)
from moss_llm.smith.ir import IR                                # noqa: E402
from moss_llm.smith.registry import Registry                    # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "smith"
BASELINE = FIXTURES / "coverage.json"
TARGETS = ("moss-image", "qwen-image-2_1", "krea2", "flux3", "sdxl",
           "flux1_dev", "minimax-h3")


def human_text(out: str) -> str:
    """后端输出 → 人类可读文本（去掉 JSON 结构，保留散文与字符串值）。

    `coverage()` 数的是「内容词」。直接对 Layout JSON 数会得到一堆键名
    （`summary` / `canvas` / `items`），那是结构，不是内容——实测 moss-image 上
    因此只有 0.66。剥掉结构再算，剩下的才是「这句话里说了什么」。
    """
    prose, blocks = _split_structured(out)
    return "\n".join([prose] + _string_values(blocks))


def measure() -> tuple:
    reg = Registry.load()
    rows, bad = {}, []
    for p in sorted(FIXTURES.glob("*.ir.json")):
        ir = IR.load(p)
        key = p.stem.replace(".ir", "")
        for t in TARGETS:
            try:
                out = get_adapter(t, reg).realize(ir).positive
            except ValueError as e:            # 能力不匹配（如 edit IR 给没有编辑通道的后端）
                rows["%s|%s" % (key, t)] = {"skip": str(e)[:80]}
                continue
            rep = verify(ir, out, t, reg)
            cov, _missing = coverage(human_text(out), ir)
            row = {
                "fixed": len(rep.fixed),
                "hard_misses": len(rep.hard_misses),
                "extra_numbers": len(rep.extra_numbers),
                "extra_quotes": len(rep.extra_quotes),
                "coverage": None if cov is None else round(cov, 4),
            }
            rows["%s|%s" % (key, t)] = row
            if row["hard_misses"] or row["extra_numbers"] or row["extra_quotes"]:
                bad.append(("%s × %s" % (key, t), row))
    return rows, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true", help="重建基线")
    ap.add_argument("--check", action="store_true", help="对账（默认）")
    args = ap.parse_args()

    rows, bad = measure()
    pairs = len(rows)
    skipped = sum(1 for r in rows.values() if "skip" in r)

    if bad:
        print("FAIL 事实闸：%d 个组合丢固定项或编了东西" % len(bad))
        for name, row in bad[:12]:
            print("  · %-42s hard=%d num=%d quote=%d"
                  % (name, row["hard_misses"], row["extra_numbers"], row["extra_quotes"]))
    else:
        print("OK   事实闸：%d 个组合（%d 跳过）全部不丢不编" % (pairs - skipped, skipped))

    if args.freeze:
        BASELINE.write_text(json.dumps({"rows": rows}, ensure_ascii=False,
                                       indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("已写基线 %s（%d 行）" % (BASELINE.relative_to(ROOT), len(rows)))
        return 0 if not bad else 1

    base = {}
    if BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8")).get("rows", {})
    drops = []
    for k, row in rows.items():
        if "skip" in row or "coverage" not in row:
            continue
        want = (base.get(k) or {}).get("coverage")
        got = row["coverage"]
        if want is None or got is None:
            continue
        if got + 1e-9 < want:
            drops.append((k, want, got))
    if drops:
        print("FAIL 覆盖率跌破基线：%d 处" % len(drops))
        for k, want, got in drops[:12]:
            print("  · %-42s %.3f → %.3f" % (k, want, got))
    else:
        print("OK   覆盖率不低于基线（%d 行）" % len(base))

    return 0 if (not bad and not drops) else 1


if __name__ == "__main__":
    sys.exit(main())
