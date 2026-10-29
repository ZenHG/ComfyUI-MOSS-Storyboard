# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""换脸节点 `init` 两个分支的接线断言：桩 model / vae / 采样，不加载模型、不碰 GPU。

方法同 `tests/test_faceswap_wiring.py`（源自 `e28_execute_wiring.py`）：
真机只会告诉你「图不对」，桩会告诉你「`vae.encode` 被不该调的地方调了」。

为什么要单列一个文件：`init=empty` 是**新增分支**，它必须证明四件事 ——
1. **不碰源图**：`vae.encode` 一次都不调（没有源图可编码）
2. 画布来源明确：没接 `spec` 用 `width×height`，接了用 spec 的整张宫格
3. 接进来的 `image` / `mask` 被**明确忽略**并在 report 里写出来（不静默吞掉）
4. `init=source` 缺 `image` 时**立刻报错**，不回退成生成

跑法：`python tests/test_init_empty.py`
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import torch  # noqa: E402


def _load_nodes_module():
    """按「包」的方式加载本仓的 `nodes.py`。

    `import nodes` 会炸：`nodes.py` 里全是 `from .core import ...` 的相对导入，
    而目录名带连字符（`ComfyUI-MOSS-Storyboard`）没法当包名。所以手工注册一个包壳，
    让 `importlib` 能找到 `__path__`。**不执行 `__init__.py`** —— 那会去注册节点、读 i18n，
    测试不需要。
    """
    import importlib

    pkg_name = "moss_sb_under_test"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [ROOT]
        sys.modules[pkg_name] = pkg
    return importlib.import_module(pkg_name + ".nodes")

FAILED = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("[OK]  " if ok else "[FAIL]", name,
                         ("  " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def _install_stub_comfy():
    if "comfy.utils" in sys.modules:
        return
    pkg = types.ModuleType("comfy")
    utils = types.ModuleType("comfy.utils")

    def common_upscale(samples, width, height, upscale_method="nearest-exact", crop="disabled"):
        return torch.nn.functional.interpolate(
            samples.float(), size=(int(height), int(width)), mode="bilinear", align_corners=False)

    utils.common_upscale = common_upscale
    pkg.utils = utils
    sys.modules["comfy"] = pkg
    sys.modules["comfy.utils"] = utils


class StubClip:
    """够 `refimage.encode` 用：tokenize 记下图片张数，encode 返回一个假条件。"""

    def tokenize(self, text, images=None):
        return {"text": text, "n_images": len(images or [])}

    def encode_from_tokens_scheduled(self, tokens):
        return [["fake_cond", {"tokens": tokens}]]


class StubVAE:
    """`encode` 一旦被调就记账 —— `init=empty` 下它必须保持 0 次。"""

    def __init__(self):
        self.encode_calls = 0

    def encode(self, pixels):
        self.encode_calls += 1
        return {"samples": torch.zeros(1, 16, 8, 8)}


def img(h, w, value=0.5):
    return torch.full((1, h, w, 3), value, dtype=torch.float32)


def main():
    _install_stub_comfy()

    N = _load_nodes_module()
    # ⚠ 必须用**同一个包路径**取模块：`from core import ...` 拿到的是顶层 `core`，
    # 而 `nodes.py` 里的 `from .core import ...` 拿的是 `moss_sb_under_test.core` ——
    # 两个不同的模块对象，给前者打桩节点根本看不见（这个坑踩过一次）。
    import importlib
    S = importlib.import_module("moss_sb_under_test.core.sampling")
    L = importlib.import_module("moss_sb_under_test.core.layout")

    calls = {}

    def fake_empty_latent(model, width, height, batch=1):
        calls["empty"] = (int(width), int(height))
        return {"samples": torch.zeros(1, 16, int(height) // 8, int(width) // 8)}

    def fake_sample(model, positive, negative, latent, steps, cfg, sampler_name,
                    scheduler, denoise=1.0, seed=0, preview=True, noise_mask=None):
        calls["sample"] = {"denoise": float(denoise), "mask": noise_mask}
        return latent

    def fake_decode(vae, sampled):
        h = int(sampled["samples"].shape[-2]) * 8
        w = int(sampled["samples"].shape[-1]) * 8
        return torch.zeros(1, h, w, 3)

    S.empty_latent = fake_empty_latent
    S.sample = fake_sample
    S.decode = fake_decode
    S.encode_text = lambda clip, text: [["neg", {}]]

    REF = img(512, 512)

    def run(**kw):
        args = {"model": object(), "clip": StubClip(), "vae": StubVAE(),
                "ref": REF, "prompt": "a person in Picture 1", "steps": 8, "cfg": 1.0,
                "sampler_name": "euler", "scheduler": "simple", "seed": 0,
                "denoise": 0.95, "vision_megapixels": 1.05}
        args.update(kw)
        return N.MossStoryboardFaceSwap().run(**args)

    # ------------------------------------------------------------------
    print("== 1. init=empty：不吃源图 ==")
    vae = StubVAE()
    out, rep = run(vae=vae, init="empty", width=1024, height=576)
    check("vae.encode 一次都没调", vae.encode_calls == 0, vae.encode_calls)
    check("走了空 latent", calls.get("empty") == (1024, 576), calls.get("empty"))
    check("输出尺寸 = 画布", tuple(out.shape[1:3]) == (576, 1024), tuple(out.shape[1:3]))
    check("report 写明模式", "init=empty" in rep, rep[:60])
    check("report 写明画布来源", "width × height" in rep, rep[:120])

    print("== 2. init=empty + spec：画布来自 spec ==")
    lay = L.build_layout(4, panel_edge=496, panel_aspect="16:9", gutter=8, margin=8)
    calls.clear()
    out2, rep2 = run(init="empty", spec=lay.spec())
    check("画布 = spec 整张宫格", calls.get("empty") == (lay.width, lay.height), calls.get("empty"))
    check("report 写明来自 spec", "spec 画布" in rep2, rep2[:120])
    check("输出尺寸 = spec 画布", tuple(out2.shape[1:3]) == (lay.height, lay.width),
          tuple(out2.shape[1:3]))

    print("== 3. init=empty 下接进来的东西要明确忽略 ==")
    _, rep3 = run(init="empty", image=img(576, 1024), mask=torch.zeros(1, 576, 1024))
    check("image 被忽略且写明", "`image` 被忽略了" in rep3, rep3)
    check("mask 被忽略且写明", "`mask` 被忽略了" in rep3, rep3)
    check("写明哪些旋钮不生效", "denoise / sample_edge / auto_mask" in rep3, rep3)

    print("== 4. init=empty 空提示词 → 报错 ==")
    try:
        run(init="empty", prompt="   ")
    except ValueError as exc:
        check("报错提到 init=empty", "init=empty" in str(exc), str(exc)[:60])
    else:
        check("报错提到 init=empty", False, "没有报错")

    print("== 5. init=source 缺 image → 报错（不回退成生成）==")
    try:
        run(init="source")
    except ValueError as exc:
        ok = ("image" in str(exc)) and ("empty" in str(exc))
        check("报错并指路 init=empty", ok, str(exc)[:80])
    else:
        check("报错并指路 init=empty", False, "没有报错")

    print("== 6. init 拼错 → 报错 ==")
    try:
        run(init="source2", image=img(576, 1024))
    except ValueError as exc:
        check("报错列出合法值", "source" in str(exc) and "empty" in str(exc), str(exc)[:60])
    else:
        check("报错列出合法值", False, "没有报错")

    print("== 7. 回归：init=source 仍然走 vae.encode ==")
    vae2 = StubVAE()
    calls.clear()
    _, rep7 = run(vae=vae2, init="source", image=img(576, 1024), auto_mask=False)
    check("vae.encode 被调了 1 次", vae2.encode_calls == 1, vae2.encode_calls)
    check("没走空 latent", "empty" not in calls, calls)
    check("report 是换脸口径", "换哪一张" in rep7, rep7[:60])

    print()
    if FAILED:
        print("结果：失败 %d 项 —— %s" % (len(FAILED), "；".join(FAILED[:5])))
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
