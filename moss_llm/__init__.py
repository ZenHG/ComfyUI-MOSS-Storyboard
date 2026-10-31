# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`moss_llm` —— ComfyUI-MOSS-Storyboard 的**可选** LLM 子包。

🔴 它**不属于**核心 7 个节点：核心的文档、契约与测试按「7 个」计数，本子包的节点
由 `v3/entrypoint.py` 与根 `__init__.py` 以「追加」方式注册，删掉整个目录核心照常工作。

为什么独立成子包：核心包的承诺是「几何确定、提示词透明、返工便宜」，且刻意不做
LLM 自动补词（内容与节奏归作者，见 `docs/14-top-level-design.md` §六）。
本子包把「生成词」这件事放在作者明确伸手的地方（一个节点、一次调用、一份可读报告），
不改变核心的任何默认行为，也不给核心引入第三方依赖。

模块分工：
- `server.py`  llama-server 侧车（acquire/release、fit、KV 量化、MTP 元数据判定）
- `nodes.py`   `MossPromptSmith`（V1 契约 = 参数表唯一真相源）
- `pack.py`    提示词包加载与 sha 校验（`python -m moss_llm.pack --write` 重新生成 manifest）
- `runtime_install.py`  一键装/更新 llama.cpp 运行时（`python -m moss_llm.runtime_install`）
- `v3_shells.py`  V3 外壳（需要宿主 `comfy_api`，故不在这里 import）
"""

from .nodes import CATEGORY, NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["CATEGORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "llama_server_hint"]


def llama_server_hint():
    """没找到 llama-server 时给一句话提示（找到就返回 None）。"""
    from .server import GgufLocalServer
    if GgufLocalServer.find_exe():
        return None
    return ("未检测到 llama-server：本包节点仍会出现，但跑起来会报错。"
            "把节点的「运行时」选成「更新到最新版」可一键安装，"
            "或在「服务器程序」里填已有路径 / 设环境变量 MOSS_LLM_SERVER。")
