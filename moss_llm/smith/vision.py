# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""图像预处理 —— **转发到 `moss_llm.vision`**（合流后只剩一份实现）。

以前这里有一份独立实现（缩放 / EXIF 转正 / 透明合成到白底），节点那边另有一份
（原始尺寸直接编 PNG）。同一件事两个尺度，报告里还看不出来。现在父包是唯一实现：
节点走 `prepare_tensor`（张量入口），本层走 `prepare`（文件入口），纪律同一套。

保留这个模块名是为了不打断既有调用方与测试；新代码直接 `from moss_llm.vision import ...`。
"""

from __future__ import annotations

from ..vision import MAX_SIDE, Prepared, prepare, prepare_tensor

__all__ = ["MAX_SIDE", "Prepared", "prepare", "prepare_tensor"]
