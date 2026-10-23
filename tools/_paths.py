# SPDX-License-Identifier: MIT
"""宿主根目录的解析（所有开发脚本共用）。

解析顺序：
  1. 环境变量 `COMFYUI_PATH`（最明确，CI / 多实例都用它）；
  2. 仓库根的 `.comfyui_path`（**本地文件，已在 .gitignore 里**，只服务开发机）；
  3. 按标准安装位置推断：本包位于 `<ComfyUI>/custom_nodes/<本包>/` ⇒ 上三级即宿主根。

为什么要有第 2 条：开发机常常把仓库放在盘符根下、再用目录联接挂进 `custom_nodes`，
此时第 3 条推断不出来。把路径写进一个被忽略的本地文件，比在代码里硬编码盘符干净——
代码是要公开的，路径不是。
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def host_root() -> str:
    """返回宿主 ComfyUI 根目录（可能是推断值，调用方自己判存在性）。"""
    env = (os.environ.get("COMFYUI_PATH") or "").strip()
    if env:
        return env
    local = os.path.join(_HERE, os.pardir, ".comfyui_path")
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as fh:
            hit = fh.read().strip()
        if hit:
            return hit
    return os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir))


def require_host_root() -> str:
    """同 `host_root()`，但要求目录真的存在；不存在时给出可操作的报错。"""
    root = host_root()
    if not os.path.isdir(os.path.join(root, "custom_nodes")):
        raise SystemExit(
            "找不到宿主 ComfyUI 根目录（%s 下没有 custom_nodes）。\n"
            "    设环境变量 COMFYUI_PATH=<宿主根>，或把路径写进仓库根的 .comfyui_path。" % root)
    return root
