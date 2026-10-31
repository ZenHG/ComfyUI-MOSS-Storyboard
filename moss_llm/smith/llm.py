# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""侧车客户端 —— **转发到 `moss_llm.server`**（合流后只剩一份实现）。

2026-10-31 之前这里有一份独立实现，父包 `server.py` 也有一份：前者有 flag 探测 /
attached 模式 / 日志 / JSON 三级降级，后者有 fit / KV 量化 / ubatch / MTP 元数据判定 /
mmproj 投影宽度配对。两份都在长，而且**没有一条测试能发现它们已经不一致**——
这就是漂移的标准写法。现在唯一实现在父包，这里只保留导入路径，不重复逻辑。

四条出词纪律（思考开关 / 不静默降级 / 空正文报错 / JSON 模式与重复惩罚互斥）
的说明在 `moss_llm/server.py` 的模块文档里，改之前先读那一段。
"""

from __future__ import annotations

from ..server import (DEFAULT_SAMPLING, LlamaServer,
                      SERVER_EXE_ENV, gguf_metadata, gguf_mtp_layers,
                      instance_alias, port_free)

__all__ = ["DEFAULT_SAMPLING", "LlamaServer", "SERVER_EXE_ENV", "gguf_metadata",
           "gguf_mtp_layers", "instance_alias", "port_free"]
