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
- `server.py`  llama-server 侧车与客户端（`LlamaServer`：fit / KV 量化 / MTP 判定与回退 /
  flag 探测 / 日志 / attached 模式；`GgufLocalServer` 是节点用的薄壳）+ GGUF 头部读取 +
  mmproj 投影宽度配对
- `nodes.py`   `MossPromptSmith`（V1 契约 = 参数表唯一真相源；四个任务：反推/扩写/编辑/编译）
- `vision.py`  图像预处理（最长边 1536 + JPEG 4:4:4 + EXIF 转正 + 透明合成白底）
- `pack.py`    提示词包加载与 sha 校验（`python -m moss_llm.pack --write` 重新生成 manifest）
- `runtime_install.py`  一键装/更新 llama.cpp 运行时（`python -m moss_llm.runtime_install`）
- `smith/`     IR 编译层（可选；节点只在 `task=compile` 时懒加载它）
- `v3_shells.py`  V3 外壳（需要宿主 `comfy_api`，故不在这里 import）
"""

from .nodes import CATEGORY, NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, list_models

__all__ = ["CATEGORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS",
           "llama_server_hint", "smith_available", "list_models"]


def register_models_route():
    """前端「拉取模型」打这个路由。Key 只在请求体里过一趟：不回显、不落日志、不存盘。"""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:            # 不在 ComfyUI 里（单测 / CLI）⇒ 没有路由可挂
        return False

    @PromptServer.instance.routes.post("/moss_llm/models")
    async def _models(request):
        try:
            data = await request.json()
            ids, url = list_models(data.get("api_base"), data.get("api_key"),
                                   data.get("api_key_env"))
            return web.json_response({"ok": True, "models": ids, "url": url})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    return True


register_models_route()


def smith_available() -> bool:
    """`moss_llm.smith`（IR 编译层）是否可用。

    用 `find_spec` 而不是 import：那一层有 8000 行，节点不用它时不该付 import 成本；
    它也**不依赖**本子包的任何东西，删掉不影响节点。两件事互不牵连。
    """
    import importlib.util
    return importlib.util.find_spec("moss_llm.smith") is not None


def llama_server_hint():
    """没找到 llama-server 时给一句话提示（找到就返回 None）。"""
    from .server import GgufLocalServer
    if GgufLocalServer.find_exe():
        return None
    return ("未检测到 llama-server：本包节点仍会出现，但跑起来会报错。"
            "把节点的「运行时」选成「更新到最新版」可一键安装，"
            "或在「服务器程序」里填已有路径 / 设环境变量 MOSS_LLM_SERVER。")
