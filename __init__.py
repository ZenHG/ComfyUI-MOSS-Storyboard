# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""ComfyUI-MOSS-Storyboard

给 MOSS-Image（5B 单流 DiT + Qwen3.5-9B + FLUX.2 VAE）用的故事板宫格与布局节点包。

七个节点（分类 `moss/storyboard`）：
- **MOSS Layout Editor** 画布式布局编辑器：元素 / 文字 / 宫格，出提示词与几何。零模型。
- **MOSS Storyboard Layout** 算 3/4/6/8/9 宫格几何，出 MOSS 原生布局提示词与模板预览。零模型。
- **MOSS Storyboard Sampler** 一步出故事板：布局约束 + 采样 + 复绘宫格线 + 裁格。
  支持「整张」一次采样与「逐格」独立采样后拼版。
- **MOSS Storyboard Crop** 按 spec 精确裁格，附带裁切线预览。
- **MOSS Storyboard Paste** 把改好的单格按 spec 坐标贴回整张宫格。
- **MOSS Prompt Compiler** 把官方 prompt-enhancer 的 caption/layout 草稿编译成
  MOSS 提示词；layout 草稿另出 state v3（可导入布局编辑器）。零模型。
- **MOSS Storyboard Face Swap** 换脸：参考图走 vision 通道注入身份，源图走图生图重画；
  接了 spec 就只换那一格，其余格与宫格线不动。

三步跑通：
1. 接 `UNETLoader(moss_image_sft_bf16)` / `CLIPLoader(type=moss_image)` / `VAELoader(flux2-vae)`。
2. 把故事板稿（`[STYLE]` + `[PANEL 1..n]`）贴进 Sampler 的 positive。
3. 选格数，跑。产物 = 整张宫格 + 逐格切片。
"""

import os as _os
import sys as _sys

# Windows 控制台可能是 cp1252：启动日志含中文与 emoji，编码不了会抛异常。
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================================
# 节点 API 出口：v3（默认）｜v1（一行回退）
# ============================================================================
NODE_API_DEFAULT = "v3"
NODE_API = _os.environ.get("MOSSSB_NODE_API", NODE_API_DEFAULT).strip().lower()
NODE_API_FALLBACK_REASON = None

if NODE_API == "v3":
    try:
        from .v3.entrypoint import comfy_entrypoint
    except Exception as _v3_err:
        # 一个可选出口不许把整包搞挂：退回 V1 并**大声说明**（不静默降级）。
        NODE_API_FALLBACK_REASON = "%s: %s" % (type(_v3_err).__name__, _v3_err)
        print("[MOSS SB] ⚠️ V3 出口加载失败，已回退 V1：%s" % NODE_API_FALLBACK_REASON)
        print("[MOSS SB]    修 V3：确认宿主 ComfyUI 的 requirements 齐全（V3 要 import comfy_api）。"
              "要显式用 V1：设 MOSSSB_NODE_API=v1 后重启。")
        NODE_API = "v1"

# 可选 LLM 子包（`moss_llm/`）：🔴 **不属于核心 7 个节点**，装载失败只缺它自己。
# 判据「是不是核心」看的是 `nodes.py` 的注册表，这里只是把它并进宿主看得见的出口。
try:
    from .moss_llm import (NODE_CLASS_MAPPINGS as _LLM_CLASS_MAPPINGS,
                           NODE_DISPLAY_NAME_MAPPINGS as _LLM_DISPLAY_MAPPINGS,
                           llama_server_hint as _llama_server_hint)
except Exception as _llm_err:
    _LLM_CLASS_MAPPINGS, _LLM_DISPLAY_MAPPINGS = {}, {}
    _LLM_LOAD_ERROR = "%s: %s" % (type(_llm_err).__name__, _llm_err)
else:
    _LLM_LOAD_ERROR = None

if NODE_API == "v3":
    # ⚠ 必须是 None 而不是空 dict：宿主判据含 "is not None"。
    NODE_CLASS_MAPPINGS = None
    NODE_DISPLAY_NAME_MAPPINGS = None
    __all__ = ["comfy_entrypoint"]
else:
    from .nodes import NODE_CLASS_MAPPINGS as _CORE_CLASS_MAPPINGS
    from .nodes import NODE_DISPLAY_NAME_MAPPINGS as _CORE_DISPLAY_MAPPINGS
    # V1 出口也把可选子包并进来 ⇒ 两条出口的节点集一致（V3 由 `v3/entrypoint.py` 追加）。
    NODE_CLASS_MAPPINGS = {**_CORE_CLASS_MAPPINGS, **_LLM_CLASS_MAPPINGS}
    NODE_DISPLAY_NAME_MAPPINGS = {**_CORE_DISPLAY_MAPPINGS, **_LLM_DISPLAY_MAPPINGS}
    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

__version__ = "0.5.3"

# 前端扩展目录：中英切换（web/moss_sb_i18n.js 纯逻辑 + moss_sb_i18n_ui.js 挂钩）。
# ⚠ 宿主只在这个变量**非 None 且目录存在**时注册；写错路径是静默失效（画布上没按钮、没翻译）。
WEB_DIRECTORY = "./web"


def _node_names():
    try:
        if NODE_API == "v3":
            from .v3.nodes_v3 import NODES
            names = [getattr(c, "__name__", "?") for c in (NODES or ())]
            if names:
                return names
        else:
            # 只数核心：可选子包不进这个数（README 引用的是「节点 7 个」那行）
            return sorted(_CORE_CLASS_MAPPINGS)
    except Exception as _e:
        print("[MOSS SB] ⚠️ 节点清单取用失败（%s: %s）" % (type(_e).__name__, _e))
    try:
        from .nodes import NODE_CLASS_MAPPINGS as _v1
        return sorted(_v1 or {})
    except Exception:
        return []


def _load_banner():
    names = _node_names()
    api = NODE_API + ("（V3 失败已回退）" if NODE_API_FALLBACK_REASON else "")
    line = "[MOSS SB] v%s 已加载｜节点 %d 个（出口 %s）｜3/4/6/8/9 宫格" % (
        __version__, len(names) if names else -1, api)
    if _LLM_CLASS_MAPPINGS:
        line += "\n[MOSS SB] 可选 LLM 子包 %d 个节点（分类 moss/llm，不计入上面那 %d 个）" % (
            len(_LLM_CLASS_MAPPINGS), len(names))
        _hint = _llama_server_hint()
        if _hint:
            line += "\n[MOSS SB] ⚠ " + _hint
    elif _LLM_LOAD_ERROR:
        line += "\n[MOSS SB] ⚠️ 可选 LLM 子包未加载（核心不受影响）：%s" % _LLM_LOAD_ERROR
    return line


try:
    print(_load_banner())
except Exception as _e:
    print("[MOSS SB] ⚠ 加载摘要生成失败（不影响包加载）：%r" % (_e,))
