# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""换脸节点的**接线**断言：桩 clip / vae / 采样，不加载模型、不碰 GPU。

方法抄自 `审计报告/实验/e28_execute_wiring.py`（2026-10-28 重构 RefHiRes 时定下的）：
「重启 ComfyUI 之前能做到的最强验证」。那次证明了桩对象能抓住真机跑不出来的接线错误
—— 真机只会告诉你「图不对」，桩会告诉你「前缀多了 1 条」。

这里覆盖 7 件事，全部可在 3 秒内跑完：
1. `Picture 1:` 前缀**恰好一条**，用户自己写了也不会变成两条
2. 参考图预算**只降不升**：小图不缩放、大图缩到预算内、报告与实际一致
3. 单格抬分辨率与缩回：抬完尺寸、缩回尺寸、只升不降
4. 裁格 + 贴回：**只有目标格变化**，缝与外边距逐像素不变
5. `denoise` 名义 → 实际（8 步 0.95 等价 1.0，0.875 才是真 img2img）
6. 越界 / 画布不符 / 空提示词 → 明确报错，不静默
7. 不接 `spec` 时按单格处理

跑法：`python tests/test_faceswap_wiring.py`
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import torch  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("[OK]  " if ok else "[FAIL]", name,
                         ("  " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def close(a, b, tol=1e-6):
    return bool(torch.allclose(torch.as_tensor(a, dtype=torch.float64),
                               torch.as_tensor(b, dtype=torch.float64), atol=tol))


# ---------------------------------------------------------------------------
# 桩：宿主 `comfy.utils.common_upscale`（真机是 lanczos/area，这里只要尺寸对）
# ---------------------------------------------------------------------------
def _install_stub_comfy():
    if "comfy.utils" in sys.modules:
        return
    pkg = types.ModuleType("comfy")
    utils = types.ModuleType("comfy.utils")

    def common_upscale(samples, width, height, upscale_method="nearest-exact", crop="disabled"):
        # samples: [B, C, H, W]（宿主约定，通道在前）
        return torch.nn.functional.interpolate(
            samples.float(), size=(int(height), int(width)), mode="bilinear", align_corners=False)

    utils.common_upscale = common_upscale
    pkg.utils = utils
    sys.modules.setdefault("comfy", pkg)
    sys.modules["comfy.utils"] = utils


_install_stub_comfy()

from core import faceswap as FS  # noqa: E402
from core import layout as L  # noqa: E402
from core import pixels as P  # noqa: E402
from core import refimage as RI  # noqa: E402
from core import sampling as SMP  # noqa: E402


class StubClip:
    """记录 tokenize 收到的提示词与图片。"""

    def __init__(self):
        self.prompt = None
        self.images = None

    def tokenize(self, text, images=None):
        self.prompt = text
        self.images = images
        return {"text": text}

    def encode_from_tokens_scheduled(self, tokens):
        return [[tokens["text"], {"stub": True}]]


def img(h, w, value=0.5):
    return torch.full((1, h, w, 3), value, dtype=torch.float32)


def main():
    print("== 1. Picture 前缀 ==")
    clip = StubClip()
    cond, rep = RI.encode(clip, "a man in Picture 1 at a cafe", img(512, 512), 1.05)
    check("前缀恰好一条", clip.prompt.count("Picture 1:") == 1, clip.prompt[:80])
    check("前缀在最前面", clip.prompt.startswith("Picture 1: <|vision_start|>"), clip.prompt[:40])
    check("原提示词完整保留", clip.prompt.endswith("a man in Picture 1 at a cafe"))
    check("只送一张图", len(clip.images) == 1, len(clip.images or []))
    check("返回 CONDITIONING 结构", isinstance(cond, list) and cond[0][0].startswith("Picture 1:"))

    # 用户自己写了前缀：仍只应有一条（否则 tokenizer 会看到两个 image_pad）
    clip2 = StubClip()
    RI.encode(clip2, "Picture 1: a man at a cafe", img(512, 512), 1.05)
    check("用户自己写前缀也不会变成两条", clip2.prompt.count("Picture 1:") == 1,
          clip2.prompt.count("Picture 1:"))

    print("== 2. 参考图预算只降不升 ==")
    small = img(512, 512)
    _, r = RI.fit_reference(small, 1.05)
    check("小图不缩放（1.05 > 0.262MP）", r["scale"] == 1.0 and r["target"] == (512, 512), r)
    big = img(1152, 2048)
    out, r = RI.fit_reference(big, 0.59)
    check("大图缩到预算内", r["target_megapixels"] <= 0.60, r["target_megapixels"])
    check("缩放后尺寸与报告一致", tuple(out.shape[1:3]) == (r["target"][1], r["target"][0]),
          (tuple(out.shape[1:3]), r["target"]))
    check("比例不变（±1px）", abs(r["target"][0] / r["target"][1] - 2048 / 1152) < 0.01,
          r["target"])
    check("预算 0 = 原生不缩放", RI.fit_reference(big, 0)[1]["scale"] == 1.0)
    check("宿主默认值会告警", any("0.008" in n for n in RI.budget_notes(RI.STOCK_MEGAPIXELS, r)))
    check("塌陷带会告警", any("塌陷带" in n for n in RI.budget_notes(0.30, r)))

    print("== 3. 单格抬分辨率 / 缩回 ==")
    panel = img(272, 496)
    up, rep_up = FS.to_sampling(panel, 1024)
    check("抬到长边 1024", max(up.shape[1], up.shape[2]) == 1024, tuple(up.shape[1:3]))
    check("抬后仍是 16 的倍数", up.shape[1] % 16 == 0 and up.shape[2] % 16 == 0)
    back = FS.to_cell(up, (496, 272))
    check("缩回格子精确尺寸", tuple(back.shape[1:3]) == (272, 496), tuple(back.shape[1:3]))
    same, rep0 = FS.to_sampling(img(272, 1024), 1024)
    check("已经够大就不再放大", rep0["scale"] == 1.0)
    zero, _ = FS.to_sampling(panel, 0)
    check("sample_edge=0 不抬", tuple(zero.shape[1:3]) == (272, 496))

    print("== 4. 裁格 + 贴回：只动目标格 ==")
    lay = L.build_layout(4, panel_edge=496, panel_aspect="16:9", gutter=8, margin=8)
    board = torch.zeros((1, lay.height, lay.width, 3), dtype=torch.float32)
    board[:, :, :, 0] = 0.25                       # 用不同通道区分区域
    panel, where = FS.resolve_target(board, lay, 1)
    check("取到的格子尺寸正确", tuple(panel.shape[1:3]) == (272, 496), tuple(panel.shape[1:3]))
    check("说明里写了第几格", "第 1 格" in where, where)
    patch = torch.full_like(panel, 0.9)
    merged, _ = P.paste_panel(board, lay, 1, patch, shave=0)
    diff = (merged - board).abs().sum(dim=-1)[0]
    ys, xs = torch.where(diff > 1e-6)
    cell = lay.cell(1)
    inside = bool((ys.min() >= cell.y) and (ys.max() <= cell.y + cell.h - 1)
                  and (xs.min() >= cell.x) and (xs.max() <= cell.x + cell.w - 1))
    check("改动全部落在目标格内", inside,
          (int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())))
    check("缝与外边距逐像素不变",
          float((merged[:, :8, :, :] - board[:, :8, :, :]).abs().max()) == 0.0)

    print("== 5. denoise 名义 → 实际 ==")
    check("8 步 0.95 = 1.0（源图被丢弃）", SMP.effective_denoise(8, 0.95) == 1.0)
    check("8 步 0.875 是真 img2img", SMP.effective_denoise(8, 0.875) == 0.875)
    check("8 步 0.90 = 1.0（截断带）", SMP.effective_denoise(8, 0.90) == 1.0)

    print("== 6. 越界与错配要报错 ==")
    for name, fn, want in (
        ("index 越界", lambda: FS.resolve_target(board, lay, 4), "越界"),
        ("画布不符", lambda: FS.resolve_target(img(512, 512), lay, 0), "不一致"),
        ("批次 >1", lambda: FS.resolve_target(torch.cat([board, board]), lay, 0), "批次"),
        ("源图为空", lambda: FS.resolve_target(None, lay, 0), "源图为空"),
        ("参考图为空", lambda: RI.fit_reference(None, 1.05), "参考图为空"),
    ):
        try:
            fn()
        except (ValueError, RuntimeError) as exc:
            check(name + " → 报错", want in str(exc), str(exc)[:60])
        else:
            check(name + " → 报错", False, "没有报错")

    print("== 7. 不接 spec = 单格 ==")
    single = img(576, 1024)
    panel, where = FS.resolve_target(single, None, 0)
    check("整图当单格", tuple(panel.shape[1:3]) == (576, 1024))
    check("说明写明未接 spec", "未接 spec" in where, where)

    print()
    if FAILED:
        print("结果：失败 %d 项 —— %s" % (len(FAILED), "；".join(FAILED[:5])))
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
