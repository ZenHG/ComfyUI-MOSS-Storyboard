# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""逐格拼版的宫格线语义与逐格 latent 契约（零 GPU、零宿主）。

锁两条真实回归：
1. `reborder=False` 时逐格模式仍会画线、`border_px=0` 时反而不画 ——
   拼版与复绘必须和整张模式共用同一套 `reborder` 语义。
2. 逐格 latent 必须回 LATENT 字典且是 batch（不是裸张量、不是空间拼版）——
   宿主 `VAEDecode` 取 `samples["samples"]`，类型错了只在跑起来才炸。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import torch  # noqa: E402  (路径先插入，再导入)

from core import layout as L  # noqa: E402
from core import pixels as P  # noqa: E402
from core import prompt as T  # noqa: E402
from core import sampling as S  # noqa: E402

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

    # 逐格 latent：必须是 LATENT 字典，且拼成 batch 而不是空间拼版
    lat = S.stack_latents([{"samples": torch.zeros(1, 4, 8, 8)} for _ in range(3)])
    check("latent 回 LATENT 字典", isinstance(lat, dict) and set(lat) == {"samples"}, type(lat).__name__)
    check("latent 第 0 维 = 格数", tuple(lat["samples"].shape) == (3, 4, 8, 8), tuple(lat["samples"].shape))
    check("latent 空间维不被拼版放大", tuple(lat["samples"].shape[-2:]) == (8, 8),
          tuple(lat["samples"].shape[-2:]))
    # 一格一个 latent：[1,C,h,w]。异形或 batch>1 都要当场拒绝 ——
    # 静默通过的话下游会多解出图，而报错现场离真因很远。
    for label, bad in (("异形 latent", [torch.zeros(1, 4, 8, 8), torch.zeros(1, 4, 9, 8)]),
                       ("batch>1 的 latent", [torch.zeros(2, 4, 8, 8)])):
        try:
            S.stack_latents([{"samples": t} for t in bad])
            check("%s 直接报错" % label, False, "没有抛异常")
        except ValueError as exc:
            check("%s 直接报错" % label, "同形状" in str(exc), str(exc)[:40])

    # 档位提示：只出声不改参数，但判错就等于没提示
    sft, mix_step, mix_cfg, lora = (S.profile_hint(s, c) for s, c in
                                    ((50, 3.0), (50, 1.0), (8, 3.0), (4, 1.0)))
    check("档位：50/3 判 SFT 档", "SFT" in sft and "混搭" not in sft, sft)
    check("档位：50/1 判混搭", "混搭" in mix_step, mix_step[:28])
    check("档位：8/3 混搭、4/1 蒸馏", "混搭" in mix_cfg and "蒸馏" in lora, lora)

    # 稿子-布局对账：段数与显式格数都必须等于布局格数；一致返回 None
    def board(n, head="[STYLE]: x"):
        return "\n\n".join([head] + ["[PANEL %d]: y" % i for i in range(1, n + 1)])

    bad = T.board_mismatch(board(1), 2)
    check("对账：1 段 / 2 格 → 报矛盾", bad and "1 段" in bad[0] and bad[1] == 1, bad)
    bad = T.board_mismatch(board(8, "[STYLE]: 恰好 4 个面板"), 8)
    check("对账：显式 4 vs 8 → 报矛盾且指出原句",
          bad and "恰好 4 个面板" in bad[0] and bad[1] == 8, bad)
    bad = T.board_mismatch(board(8, "[STYLE]: 4 panels, rainy"), 8)
    check("对账：英文 4 panels vs 8 → 报矛盾", bad and "4 panels" in bad[0], bad)
    bad = T.board_mismatch(board(9), 8)
    check("对账：9 段 / 8 格 → 隐含格数 9", bad and bad[1] == 9, bad)
    check("对账：8 段 / 8 格（含乱序镜号）→ None",
          T.board_mismatch(board(8), 8) is None
          and T.board_mismatch("\n\n".join(["[STYLE]: x"]
                                          + ["[PANEL %d]: y" % i
                                             for i in (1, 2, 5, 6, 7, 8, 9, 10)]), 8) is None)
    check("对账：无 [PANEL] 且无计数 → None", T.board_mismatch("[STYLE]: x", 8) is None)

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
