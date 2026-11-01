# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""覆盖率闸自身的测试：**基线绿**与**红时能指出少了哪条**。

`tools/check_coverage.py` 的验收要求是两句话：
1. 当前 28 个组合（4 夹具 × 7 后端）必须全绿；
2. 删掉一条事实必须红，**并且报告要说出是哪条**。

光测 ① 不够——一个永远返回绿的闸也能通过 ①。所以 ② 用临时目录造一个必然红的
IR：往 `constraints.fixed` 里塞一条没有任何渲染路径的事实（用户固定项，编译器不该
静默吞掉），闸必须以 `[user] …` 的标签点名它。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "check_coverage_tool", ROOT / "tools" / "check_coverage.py")
CC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CC)


class TestCoverageGate(unittest.TestCase):
    def test_gate_is_green_on_fixtures(self):
        rows, bad, _detail = CC.measure()
        self.assertEqual(bad, [], "闸在自己的夹具上就红了：%r" % (bad[:2],))
        self.assertEqual(len(rows), 28)

    def test_red_report_names_the_dropped_fact(self):
        """IR 里有、编译产物里没有的事实，闸必须红并点名。"""
        tmp = Path(tempfile.mkdtemp(prefix="coverage_gate_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for p in CC.FIXTURES.glob("*.ir.json"):
            shutil.copy(p, tmp)
        target = tmp / "faceswap-demo-ref.ir.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        fact = "只有手机屏幕里才有的那行字"
        data.setdefault("constraints", {})["fixed"] = [fact]
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        old_fix, old_base = CC.FIXTURES, CC.BASELINE
        CC.FIXTURES, CC.BASELINE = tmp, tmp / "coverage.json"
        try:
            _rows, bad, detail = CC.measure()
        finally:
            CC.FIXTURES, CC.BASELINE = old_fix, old_base

        self.assertTrue(bad, "塞了一条没有渲染路径的事实，闸却没有红")
        labels = [lab for d in detail.values() for lab in d.get("hard_misses") or []]
        self.assertTrue(any(fact in lab for lab in labels),
                        "闸红了，但没点名是哪条事实：%r" % (labels[:4],))
        self.assertTrue(any(lab.startswith("[user]") for lab in labels))


if __name__ == "__main__":
    unittest.main(verbosity=2)
