# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""逐格拼版的宫格线语义（零 GPU、零宿主）。

锁一条真实回归：`reborder=False` 时逐格模式仍会画线、`border_px=0` 时反而不画。
拼版与复绘必须和整张模式共用同一套 `reborder` 语义。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import torch  # noqa: E402  (路径先插入，再导入)

from core import layout as L  # noqa: E402
from core import pixels as P  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + str(detail)) if detail else ""))


def main():
    lay = L.build_layout(panels=4, cols=2, rows=2, gutter=8, margin=8,
                         panel_edge=64, panel_aspect="16:9")
    panels = torch.full((4, lay.panel_h, lay.panel_w, 3), 0.2)

    on, crops_on = P.assemble_panels(panels, lay, reborder=True, border_px=0, shave=0)
    off, _ = P.assemble_panels(panels, lay, reborder=False, border_px=0, shave=0)
    off4, _ = P.assemble_panels(panels, lay, reborder=False, border_px=4, shave=0)

    check("grid shape", tuple(on.shape) == (1, lay.height, lay.width, 3), tuple(on.shape))
    check("reborder=True 画白线", float(on.max()) > 0.9, float(on.max()))
    check("reborder=False 不画线（border_px=0）", float(off.max()) < 0.21, float(off.max()))
    check("reborder=False 不画线（border_px=4）", float(off4.max()) < 0.21, float(off4.max()))
    check("panels 裁切尺寸", tuple(crops_on.shape) == (4, lay.panel_h, lay.panel_w, 3),
          tuple(crops_on.shape))

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
