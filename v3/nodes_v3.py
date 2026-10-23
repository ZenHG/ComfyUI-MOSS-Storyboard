# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""V3 外壳：每个节点一个只含 `_V1_NAME` 的空壳类。

schema 与执行都转发给 `nodes.py`（唯一真相源），这里不写任何业务。
"""

from ._compat import ComfyExtension, io, schema_from_v1, call_v1  # noqa: F401 (ComfyExtension 供 entrypoint 用)


class _Shell(io.ComfyNode):
    """外壳基类：`define_schema` 由 V1 类生成，`execute` 转调 V1 的 FUNCTION。"""

    _V1_NAME = ""

    @classmethod
    def define_schema(cls) -> io.Schema:
        from .. import nodes as V1
        return schema_from_v1(getattr(V1, cls._V1_NAME))

    @classmethod
    def execute(cls, **kwargs) -> io.NodeOutput:
        # 宿主按 schema 入参名以 kwargs 传参 ⇒ 全接住，不挑名字
        return call_v1(cls._V1_NAME, kwargs)


class MossStoryboardLayout(_Shell):
    """MOSS Storyboard Layout：宫格几何 + 布局提示词 + 模板。"""
    _V1_NAME = "MossStoryboardLayout"


class MossStoryboardSampler(_Shell):
    """MOSS Storyboard Sampler：一步出故事板。"""
    _V1_NAME = "MossStoryboardSampler"


class MossStoryboardCrop(_Shell):
    """MOSS Storyboard Crop：按 spec 精确裁格。"""
    _V1_NAME = "MossStoryboardCrop"


#: 注册清单（唯一真相源；`__init__.py` 的加载摘要也从这里数）。
NODES = [MossStoryboardLayout, MossStoryboardSampler, MossStoryboardCrop]
