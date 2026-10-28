# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""核心层：几何 / 提示词 / 像素 / 采样。

**故意不在这里 import 子模块**：那样 `import core.layout` 会连带拉起
`pixels → torch`，于是"纯 Python 的几何层"就变成了"必须先装好 torch"。
改成惰性导入后：

- `core.layout` / `core.prompt` 在**没有 torch** 的环境里也能跑（外部工具可直接复用，
  也方便用户先在命令行算一遍宫格尺寸）；
- 只有真正要画像素 / 采样时才拉 torch 与宿主 API。
"""

__all__ = ["layout", "prompt", "pixels", "sampling", "refimage", "faceswap"]


def __getattr__(name):
    if name in __all__:
        import importlib
        return importlib.import_module("." + name, __name__)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
