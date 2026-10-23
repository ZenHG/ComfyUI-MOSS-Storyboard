# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""零 GPU 的「宿主 API 契约」测试：用桩替换 comfy 模块，断言我们传进去的类型对。

为什么要有这个：`sample_custom` / `prepare_noise` 要的是 **latent 张量**，
传 `{"samples": ...}` 会报 `'dict' object has no attribute 'is_nested'` ——
这条错误在真机上才出现（需要模型），而它既不提 latent 也不提节点，极难反推。
本测试把"我觉得对"变成机器断言：真机之外也能拦住同类回归。

跑法：python tests/test_sampling_contract.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402

from core import sampling as S  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


class Rec:
    """记录一次调用的入参类型，同时返回可用的假值。"""

    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return self.ret(*a, **kw) if hasattr(self, "ret") else None


def install_stubs():
    """把 comfy.* 与 latent_preview 换成桩，返回记录器。"""
    rec = {}

    comfy = types.ModuleType("comfy")
    samplers = types.ModuleType("comfy.samplers")
    sample_mod = types.ModuleType("comfy.sample")
    utils = types.ModuleType("comfy.utils")
    mm = types.ModuleType("comfy.model_management")

    class FakeSampler:
        def __init__(self, *a, **kw):
            self.sigmas = torch.tensor([1.0, 0.5, 0.0])

    samplers.KSampler = FakeSampler
    samplers.sampler_object = lambda name: ("sampler", name)

    rec["prepare_noise"] = Rec()

    def _prepare_noise(latent_image, seed, noise_inds=None):
        rec["prepare_noise"](latent_image)
        # 真实实现会调 .size()/.is_nested —— 这里复刻同样的硬约束
        latent_image.is_nested
        latent_image.size()
        return torch.zeros_like(latent_image)

    sample_mod.prepare_noise = _prepare_noise

    def _sample_custom(model, noise, cfg, sampler, sigmas, positive, negative, latent_image,
                       noise_mask=None, callback=None, disable_pbar=False, seed=None):
        rec.setdefault("sample_custom", Rec())(latent_image)
        latent_image.is_nested  # 真实实现同样会碰这个属性
        return torch.zeros_like(latent_image)

    sample_mod.sample_custom = _sample_custom
    sample_mod.fix_empty_latent_channels = lambda model, latent, *a, **kw: latent
    utils.PROGRESS_BAR_ENABLED = True
    mm.intermediate_device = lambda: torch.device("cpu")

    comfy.samplers = samplers
    comfy.sample = sample_mod
    comfy.utils = utils
    comfy.model_management = mm

    latent_preview = types.ModuleType("latent_preview")
    latent_preview.prepare_callback = lambda model, steps: None

    for name, mod in (("comfy", comfy), ("comfy.samplers", samplers), ("comfy.sample", sample_mod),
                      ("comfy.utils", utils), ("comfy.model_management", mm),
                      ("latent_preview", latent_preview)):
        sys.modules[name] = mod
    return rec


class FakeModel:
    load_device = "cpu"
    model_options = {}

    def get_model_object(self, name):
        raise AssertionError("不该被调用：sigma 一律走宿主 KSampler")


def main():
    rec = install_stubs()
    latent_t = torch.zeros(1, 128, 36, 64)

    print("== 1. 传字典进来也要被摊平成张量 ==")
    S.sample(FakeModel(), "pos", "neg", {"samples": latent_t}, steps=4, cfg=1.0,
             sampler_name="euler", scheduler="simple", denoise=0.85, seed=7, preview=False)
    got = rec["prepare_noise"].calls[0][0][0]
    check("prepare_noise 收到张量", torch.is_tensor(got), type(got).__name__)
    got2 = rec["sample_custom"].calls[0][0][0]
    check("sample_custom 收到张量", torch.is_tensor(got2), type(got2).__name__)

    print("\n== 2. 传张量也照样能用 ==")
    rec["prepare_noise"].calls.clear()
    S.sample(FakeModel(), "pos", "neg", latent_t, steps=4, cfg=1.0,
             sampler_name="euler", scheduler="simple", denoise=1.0, seed=7, preview=False)
    check("直接传张量 OK", len(rec["prepare_noise"].calls) == 1)

    print("\n== 3. 错误必须说得清 ==")
    for bad, needle, tag in (
        ({"noise": latent_t}, "缺 'samples'", "字典缺 samples"),
        ("not-a-latent", "必须是张量", "传字符串"),
        (123, "必须是张量", "传整数"),
    ):
        try:
            S.sample(FakeModel(), "pos", "neg", bad, steps=4, cfg=1.0,
                     sampler_name="euler", scheduler="simple", seed=1, preview=False)
            check("%s → 应报错" % tag, False, "未抛异常")
        except (ValueError, TypeError) as e:
            check("%s → %s" % (tag, needle), needle in str(e), str(e)[:70])

    print("\n== 4. denoise/steps 边界 ==")
    for kw, needle, tag in ((dict(steps=0, denoise=1.0), "steps", "steps=0"),
                            (dict(steps=4, denoise=0.0), "denoise", "denoise=0"),
                            (dict(steps=4, denoise=1.5), "denoise", "denoise>1")):
        try:
            S.sample(FakeModel(), "p", "n", latent_t, cfg=1.0, sampler_name="euler",
                     scheduler="simple", seed=1, preview=False, **kw)
            check("%s → 应报错" % tag, False, "未抛异常")
        except ValueError as e:
            check("%s → 提到 %s" % (tag, needle), needle in str(e), str(e)[:60])

    print("\n== 5. as_tensor 直测 ==")
    check("as_tensor(dict) 返回张量", S.as_tensor({"samples": latent_t}) is latent_t)
    check("as_tensor(张量) 原样返回", S.as_tensor(latent_t) is latent_t)

    print("\n== 6. 实际降噪（denoise 会不会被静默吃掉）==")
    # 宿主用 int(steps/denoise) 重算 sigma；等于 steps 时一段都没截 ⇒ 等价 denoise=1.0
    cases = [
        (4, 0.85, 1.0, "4 步 0.85 → int(4/0.85)=4，未截断"),
        (4, 0.90, 1.0, "4 步 0.90 → 未截断"),
        (4, 0.80, 0.80, "4 步 0.80 → int(5)=5 >4，刚好还是真截断（边界）"),
        (4, 0.50, 0.5, "4 步 0.50 → int(8)=8 >4，真截断"),
        (12, 0.60, 0.6, "12 步 0.60 → int(20)=20 >12，真截断"),
        (35, 0.85, 0.85, "35 步 0.85 → int(41)=41 >35，真截断"),
        (35, 1.00, 1.0, "35 步 1.00 → 名义即实际"),
    ]
    for steps, den, want, tag in cases:
        got = S.effective_denoise(steps, den)
        check("%s ⇒ 实际 %.2f" % (tag, got), abs(got - want) < 1e-9, "期望 %.2f" % want)

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
