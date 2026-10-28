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

if NODE_API == "v3":
    # ⚠ 必须是 None 而不是空 dict：宿主判据含 "is not None"。
    NODE_CLASS_MAPPINGS = None
    NODE_DISPLAY_NAME_MAPPINGS = None
    __all__ = ["comfy_entrypoint"]
else:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

__version__ = "0.3.0"

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
        elif NODE_CLASS_MAPPINGS:
            return sorted(NODE_CLASS_MAPPINGS)
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
    return "[MOSS SB] v%s 已加载｜节点 %d 个（出口 %s）｜3/4/6/8/9 宫格" % (
        __version__, len(names) if names else -1, api)


try:
    print(_load_banner())
except Exception as _e:
    print("[MOSS SB] ⚠ 加载摘要生成失败（不影响包加载）：%r" % (_e,))
