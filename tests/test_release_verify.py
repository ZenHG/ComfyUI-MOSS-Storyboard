# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/release.py` 的 registry 判定：存在 ≠ 发布完成。

2026-11-03 实测：0.4.0 已上传但 status=NodeVersionStatusPending，release.py 只查
"版本号在列表里"就打了 OK，把「已提交待复核」误报成「双渠道已发布」。这里钉住：
只有 Active 才算发布完成；Pending / Flagged / 缺失都不算。
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools" / "release.py")
rel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rel)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + str(detail)) if detail and not cond else ""))


def V(ver, st):
    return {"version": ver, "status": st}

check("Active 才算在线上", rel.registry_verdict([V("0.4.0", "NodeVersionStatusActive")], "0.4.0")
      == (True, "NodeVersionStatusActive"))
check("Pending 是已提交、不是已发布", rel.registry_verdict([V("0.4.0", "NodeVersionStatusPending")], "0.4.0")
      == (True, "NodeVersionStatusPending"))
check("Flagged 同样不算发布完成", rel.registry_verdict([V("0.4.0", "NodeVersionStatusFlagged")], "0.4.0")
      == (True, "NodeVersionStatusFlagged"))
check("缺本版 → 不在列表", rel.registry_verdict([V("0.3.3", "NodeVersionStatusActive")], "0.4.0")
      == (False, ""))
check("非列表输入不崩", rel.registry_verdict({"versions": []}, "0.4.0") == (False, ""))

print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
