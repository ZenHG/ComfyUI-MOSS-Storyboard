# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""V3 子包。

两条出口**必须互斥**：宿主加载器是
`if hasattr(mod, "NODE_CLASS_MAPPINGS") and ... is not None: ... return True`
`elif hasattr(mod, "comfy_entrypoint"): ...`
⇒ 同时导出两者时 V1 先生效，**V3 永不生效且完全静默**。
所以 V3 模式下 `__init__.py` 把 `NODE_CLASS_MAPPINGS` 显式设为 `None`。

节点清单的唯一真相源是 `nodes_v3.NODES`，别在别处再写一份。
"""
