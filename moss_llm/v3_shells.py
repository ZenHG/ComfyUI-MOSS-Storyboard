# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""可选 LLM 子包的 V3 外壳：schema 与执行都转发给 `moss_llm.nodes`（唯一真相源）。

只在宿主支持 V3 时才由 `v3/entrypoint.py` 导入——它 import `comfy_api`，
所以本模块不能在纯 Python 环境里被加载。
"""

from ..v3._compat import call_class, io, schema_from_v1
from . import nodes as LLM


class _LlmShell(io.ComfyNode):
    """与核心的 `_Shell` 同一手法，只是真相源在子包而不是根 `nodes.py`。"""

    _V1_CLS = None

    @classmethod
    def define_schema(cls) -> io.Schema:
        return schema_from_v1(cls._V1_CLS)

    @classmethod
    def validate_inputs(cls, api_model=None):
        """`api_model` 不按下拉列表校验。

        清单是运行时拉来的，进程重启就空；用户手填服务商给的 id（或旧存档里的值）不该被判非法。
        `nodes.py` 的 V1 出口靠 `VALIDATE_INPUTS` 做到这点，V3 出口走 `validate_inputs` 且
        **不继承** V1 那份 —— 漏了它就只能「先点 ↻ 拉到清单」才能运行，实测报
        `value_not_in_list: api_model: 'xxx' not in ['']`。
        只声明这一个参数：其余输入的范围/类型校验照旧（写 **kwargs 会整片关掉）。
        """
        return True

    @classmethod
    def execute(cls, **kwargs) -> io.NodeOutput:
        return call_class(cls._V1_CLS, kwargs)


class MossPromptSmith(_LlmShell):
    """MOSS Prompt Smith：本地 LLM 反推 / 扩写 / 编辑指令。"""
    _V1_CLS = LLM.MossPromptSmith


#: 可选子包的注册清单。核心 7 个节点的清单（`v3/nodes_v3.NODES`）不含它。
LLM_NODES = [MossPromptSmith]
