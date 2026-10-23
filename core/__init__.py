# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""核心层：几何 / 提示词 / 像素 / 采样。

只有 `sampling` 依赖宿主 ComfyUI；其余三个模块只依赖 torch 或纯 Python，
所以 `tests/test_core.py` 可以在没有 ComfyUI 的环境里跑完几何与裁切断言。
"""

from . import layout, pixels, prompt  # noqa: F401

__all__ = ["layout", "prompt", "pixels"]
