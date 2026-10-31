# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""重建回归基线：`tests/fixtures/smith/expected.json`。

为什么要有这个脚本：`TestRegressionSet` 把「四份真机逆向 IR × 四个后端」的确定性输出
钉成 sha256。**输出变了就必须要么是 bug、要么是有意的改动**，而后者只有人能判断。
所以更新基线不写在测试里（不许测试自己改自己），而是跑一次这个脚本，
在提交信息里写清「为什么变」。

用法：
    python tools/freeze_reverse_set.py            # 重建
    python tools/freeze_reverse_set.py --check    # 只对账，不写（等价于跑测试里的那部分）

基线覆盖的四个目标写死在 TARGETS 里：它们代表四种方言形态
（Layout JSON / 长散文 / 512 硬截断 / 短描述 + bbox 表）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.adapters import get_adapter            # noqa: E402
from moss_llm.smith.ir import IR                          # noqa: E402
from moss_llm.smith.registry import Registry              # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter         # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "smith"
EXPECTED = FIXTURES / "expected.json"
TARGETS = ("moss-image", "qwen-image-2_1", "krea2", "flux3", "sdxl")


def digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def snapshot() -> dict:
    reg = Registry.load()
    out = {}
    for path in sorted(FIXTURES.glob("*.ir.json")):
        ir = IR.load(path)
        entry = {"entities": len(ir.entities),
                 "texts": len(ir.text_entities),
                 "notes": len(ir.notes),
                 "needs_layout": ir.needs_layout(),
                 "targets": {}}
        for t in TARGETS:
            res = get_adapter(t, reg, counter=HeuristicCounter()).realize(ir)
            entry["targets"][t] = {"words": len(res.positive.split()),
                                   "positive": digest(res.positive),
                                   "negative": digest(res.negative or "")}
        out[path.name] = entry
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只对账，不写文件")
    args = ap.parse_args(argv)

    got = snapshot()
    if args.check:
        old = json.loads(EXPECTED.read_text(encoding="utf-8")) if EXPECTED.exists() else {}
        if old == got:
            print("基线一致：%d 份 IR × %d 个后端" % (len(got), len(TARGETS)))
            return 0
        for name in sorted(set(old) | set(got)):
            if old.get(name) != got.get(name):
                print("变了：%s\n  旧 %s\n  新 %s"
                      % (name, json.dumps(old.get(name), ensure_ascii=False),
                         json.dumps(got.get(name), ensure_ascii=False)))
        return 1

    EXPECTED.write_text(json.dumps(got, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
    print("已写入 %s（%d 份 IR × %d 个后端）" % (EXPECTED, len(got), len(TARGETS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
