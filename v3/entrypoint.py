# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""V3 出口：`comfy_entrypoint()`。

逐个 `GET_SCHEMA()` 预检的理由：V3 是**加载时**就取 schema（V1 是惰性），
某个节点的 schema 一抛错，整个扩展注册失败 ⇒ 全部节点消失。这里逐节点兜住。
"""

from ._compat import ComfyExtension, io
from .nodes_v3 import NODES

# 可选 LLM 子包（`moss_llm/`）：追加注册，**不属于核心 7 节点**。
# 它 import 失败只意味着少一个可选节点，绝不能把核心节点一起拖没 ⇒ 单独兜住并出声。
try:
    from ..moss_llm.v3_shells import LLM_NODES
except Exception as _llm_err:
    LLM_NODES = []
    print("[MOSS SB] ⚠️ 可选 LLM 子包未注册（核心 %d 个节点不受影响）：%s: %s"
          % (len(NODES), type(_llm_err).__name__, _llm_err))


class MossStoryboardExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        ok = []
        for cls in list(NODES) + list(LLM_NODES):
            try:
                cls.GET_SCHEMA()
            except Exception as exc:
                print("[MOSS SB] V3 节点 %s 的 schema 构造失败 ⇒ 跳过它，其余照常加载。原因：%s: %s"
                      % (cls.__name__, type(exc).__name__, exc))
                continue
            ok.append(cls)
        return ok


async def comfy_entrypoint() -> MossStoryboardExtension:
    return MossStoryboardExtension()
