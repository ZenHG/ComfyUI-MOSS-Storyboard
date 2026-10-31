# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""ComfyUI 参考节点：把 prompt-artisan 接成一个「IR → prompt」节点。

**这是参考实现，不是包的一部分**。它的用处有三个：

1. 证明契约面真的够用——节点只需要 `Registry` / `IR` / `get_adapter` / `llm.chat` 四样。
2. 给一个可以照抄的骨架：输入端怎么接、`Realized` 的字段怎么摊成节点输出、警告怎么上 UI。
3. 让「节点里不该做什么」有具体反例可指：不要在 `execute()` 里 `start()` 一个 llama-server
   （ComfyUI 执行是热路径，起进程会把队列卡住），要连常驻端点，或在类初始化时建一次复用。

放到 `ComfyUI/custom_nodes/moss_llm.smith_node/` 下即可被加载；本文件不依赖 torch，
只用标准库 + 本包，所以也能在没装 ComfyUI 的机器上直接 import 做单测。

    NODE_CLASS_MAPPINGS = {"PromptArtisanCompile": PromptArtisanCompile, ...}
"""

from __future__ import annotations

import json

#: 进程级单例：registry 只读一次。每次节点执行都重读 YAML 是纯浪费（而且会拖慢队列）。
_REG = None
_LLM = None


def _registry():
    global _REG
    if _REG is None:
        from moss_llm.smith.registry import Registry
        _REG = Registry.load()
    return _REG


def _llm(base_url: str):
    """连一个**已经跑着**的 OpenAI 兼容端点。空字符串 = 不用 LLM（确定性编译）。

    注意这里只做健康检查式的 start()（有 base_url 时不拉进程）。
    真要本地拉起，请在节点类外面起一次，别放进 execute()。
    """
    global _LLM
    if not base_url:
        return None
    if _LLM is None or getattr(_LLM, "base_url", "") != base_url:
        from moss_llm.smith.llm import LlamaServer
        _LLM = LlamaServer(base_url=base_url)
        _LLM.start()
    return _LLM


class PromptArtisanCompile:
    """IR(JSON 字符串) + 目标 + 模式 → 正向 / 负向 / 参数 / 警告。"""

    CATEGORY = "moss_llm.smith"
    FUNCTION = "compile"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive", "negative", "params_json", "warnings")

    @classmethod
    def INPUT_TYPES(cls):
        targets = _registry().names()
        return {
            "required": {
                "ir_json": ("STRING", {"multiline": True, "default": "{}"}),
                "target": (targets, {"default": "moss-image"}),
                "mode": (["deterministic", "llm"], {"default": "deterministic"}),
                "length": (["brief", "standard", "detailed"], {"default": "standard"}),
            },
            "optional": {
                "llm_base_url": ("STRING", {"default": ""}),
                "candidates": ("INT", {"default": 4, "min": 1, "max": 8}),
            },
        }

    def compile(self, ir_json, target, mode="deterministic", length="standard",
                llm_base_url="", candidates=4):
        from moss_llm.smith.adapters import get_adapter
        from moss_llm.smith.ir import IR

        ir = IR.from_dict(json.loads(ir_json or "{}"))
        ad = get_adapter(target, _registry(), llm=_llm(llm_base_url))
        ad.length = length
        ad.gen_candidates = candidates
        out = ad.realize(ir, mode=mode)

        # 扩写判 fail 必须让人看见：它意味着模型改了年龄、编了数字或编了图里的字。
        # 静默通过 = 用户拿到一张错图却不知道为什么。
        warn = list(out.warnings)
        if (out.expand or {}).get("verdict") == "fail":
            warn.insert(0, "扩写体检未通过：" + "；".join(
                (out.expand or {}).get("issues") or [])[:400])
        return (out.positive, out.negative or "",
                json.dumps(out.params or {}, ensure_ascii=False), "\n".join(warn))


NODE_CLASS_MAPPINGS = {"PromptArtisanCompile": PromptArtisanCompile}
NODE_DISPLAY_NAME_MAPPINGS = {"PromptArtisanCompile": "Prompt Artisan · Compile IR"}
