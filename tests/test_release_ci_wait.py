# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`release.ci_verdict` 的回归测试：**还在跑**绝不能被判成红。

起因（2026-11-05 实测）：`gh run watch --exit-status` 在 job 仍运行时提前返回 1，
发布器据此判「CI 红」，四分钟后同一 run 变绿 —— 白判一次。

跑法：`python tests/test_release_ci_wait.py`
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("release_tool", os.path.join(ROOT, "tools", "release.py"))
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

CASES = [
    (("queued", ""), "wait"),
    (("in_progress", ""), "wait"),
    (("in_progress", "failure"), "wait"),      # 还在跑：不算红
    (("waiting", ""), "wait"),
    (("completed", "success"), "ok"),
    (("completed", "failure"), "red"),
    (("completed", "cancelled"), "red"),
    (("completed", "timed_out"), "red"),
    (("completed", ""), "red"),               # 完成但没结论：不当绿
]

fails = 0
for args, want in CASES:
    got = rt.ci_verdict(*args)
    ok = got == want
    print(("  [OK]  " if ok else "  [FAIL]") + " %-28s -> %-5s (want %s)" % (args, got, want))
    fails += 0 if ok else 1
print("\n结果：通过 %d / 失败 %d" % (len(CASES) - fails, fails))
sys.exit(1 if fails else 0)
