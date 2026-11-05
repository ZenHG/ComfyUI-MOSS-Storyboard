# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`ci_preflight.impact_scope` 的回归测试：按**完整路径**判定，不按子串。

跑法：`python tests/test_ci_preflight_scope.py`
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("ci_preflight", os.path.join(ROOT, "tools", "ci_preflight.py"))
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

CASES = [
    (["moss_llm/nodes.py"], {"ui-llm-panel"}),
    (["web/moss_llm_panel.js", "README.md"], {"ui-llm-panel"}),
    (["docs/20-llm-prompt-nodes.md"], {"ui-llm-panel"}),
    (["examples/llm-prompt-workbench.json", "web/moss_sb_i18n.js"], {"ui-llm-panel"}),
    (["nodes.py"], None),
    (["tools/ci_preflight.py"], None),
    (["v3/entrypoint.py"], None),
    (["web/moss_llm_panel.js", "nodes.py"], None),
    ([], None),
]

fails = 0
for paths, want in CASES:
    got = ci.impact_scope(paths)
    ok = got == want
    print(("  [OK]  " if ok else "  [FAIL]") + " %-58s -> %s" % (paths, got))
    fails += 0 if ok else 1
print("\n结果：通过 %d / 失败 %d" % (len(CASES) - fails, fails))
sys.exit(1 if fails else 0)
