# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
from __future__ import annotations

from ..registry import Registry
from .base import Adapter, Realized
from .flux3 import Flux3Adapter
from .h3 import H3Adapter
from .generic import GenericAdapter
from .krea2 import Krea2Adapter
from .moss_image import MossImageAdapter
from .qwen_image_21 import QwenImage21Adapter
from .sdxl import SdxlAdapter

ADAPTERS = {
    MossImageAdapter.name: MossImageAdapter,
    QwenImage21Adapter.name: QwenImage21Adapter,
    Krea2Adapter.name: Krea2Adapter,
    Flux3Adapter.name: Flux3Adapter,
    SdxlAdapter.name: SdxlAdapter,
    H3Adapter.name: H3Adapter,
}


def get_adapter(name: str, registry: Registry, counter=None, llm=None) -> Adapter:
    """优先用原生后端；registry 里有声明但没原生实现的，走通用后端。

    这样用户接一个新模型只需要改 YAML，不用写 Python——开源项目里，
    「必须改源码才能用」本身就是一道门槛。
    """
    if name in ADAPTERS:
        return ADAPTERS[name](registry, counter=counter, llm=llm)
    if name in registry.names():
        return GenericAdapter(registry, name, counter=counter, llm=llm)
    raise KeyError("没有 %r 这个后端。registry 里有：%s；原生实现：%s"
                   % (name, ", ".join(registry.names()), ", ".join(sorted(ADAPTERS))))


__all__ = ["ADAPTERS", "Adapter", "GenericAdapter", "Realized", "get_adapter"]
