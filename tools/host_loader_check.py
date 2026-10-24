# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""宿主编译器级验证：走 ComfyUI **自己的**加载函数，而不是我们自己的 importlib。

为什么单独一个脚本：`smoke_nodes.py` 证明"包能 import、schema 能构造"，
但它用的是我们自己的 loader。**配置写对 ≠ 宿主真的调你**——所以这里直接调
`nodes.load_custom_node()`（宿主启动时扫 custom_nodes 用的同一个函数），
再查 `nodes.NODE_CLASS_MAPPINGS` 里到底有没有我们的 node_id。

跑法：`python tools/host_loader_check.py`
"""

import asyncio
import os
import sys
from _paths import host_root

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFY = host_root()
TARGET = os.environ.get("MOSSSB_PATH", os.path.join(COMFY, "custom_nodes", "ComfyUI-MOSS-Storyboard"))

EXPECT = {
    "MossStoryboardLayout": "MOSS Storyboard Layout",
    "MossStoryboardPaste": "MOSS Storyboard Paste",
    "MossStoryboardSampler": "MOSS Storyboard Sampler",
    "MossStoryboardCrop": "MOSS Storyboard Crop",
}

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def main():
    if COMFY not in sys.path:
        sys.path.insert(0, COMFY)
    print("宿主 = %s\n目标 = %s" % (COMFY, TARGET))
    if not os.path.isdir(TARGET):
        print("目标不存在：先建目录联接（mklink /J）或 clone")
        return 1

    print("\n== 1. 宿主能否解析 pyproject.toml ==")
    try:
        from comfy_config import config_parser
        cfg = config_parser.extract_node_configuration(TARGET)
        check("pyproject 可解析", cfg is not None)
        # ⚠ 宿主**不**从 tool_comfy 读版本下限：comfy_config/config_parser.py:125 把
        #   `[tool.comfy] requires-comfyui` 塞进 project 的 supported_comfyui_version。
        #   查错地方会得到"配了但读不到"的假红（本脚本第一版就踩了）。
        p = cfg.project
        get = (lambda k, d=None: p.get(k, d)) if isinstance(p, dict) else (lambda k, d=None: getattr(p, k, d))
        ver = get("supported_comfyui_version", "") or ""
        check("requires-comfyui 读得到", bool(ver), repr(ver))
        check("requires-comfyui == >=0.42.0", ver == ">=0.42.0", repr(ver))
        check("项目名不含 ComfyUI（Registry 规范）",
              "comfyui" not in (get("name") or "").lower(), str(get("name")))
    except Exception as e:
        check("pyproject 可解析", False, "%s: %s" % (type(e).__name__, e))

    print("\n== 2. 走宿主的 load_custom_node ==")
    import nodes as host_nodes
    before = set(host_nodes.NODE_CLASS_MAPPINGS)
    ok = asyncio.run(host_nodes.load_custom_node(TARGET))
    check("load_custom_node 返回 True", ok is True, str(ok))
    after = set(host_nodes.NODE_CLASS_MAPPINGS)
    added = after - before
    print("  宿主新增注册：%s" % sorted(added))
    for nid, disp in EXPECT.items():
        check("注册了 %s" % nid, nid in after)
        check("%s 显示名正确" % nid, host_nodes.NODE_DISPLAY_NAME_MAPPINGS.get(nid) == disp,
              str(host_nodes.NODE_DISPLAY_NAME_MAPPINGS.get(nid)))
        cls = host_nodes.NODE_CLASS_MAPPINGS.get(nid)
        if cls is not None:
            check("%s 带 RELATIVE_PYTHON_MODULE" % nid, bool(getattr(cls, "RELATIVE_PYTHON_MODULE", "")))

    print("\n== 3. 宿主注册的是 V3 类（不是 V1 回退）==")
    cls = host_nodes.NODE_CLASS_MAPPINGS.get("MossStoryboardLayout")
    if cls is not None:
        is_v3 = hasattr(cls, "define_schema") and hasattr(cls, "GET_SCHEMA")
        check("MossStoryboardLayout 是 V3 ComfyNode", is_v3, type(cls).__mro__[1].__name__)
        check("schema.node_id 与注册名一致", cls.GET_SCHEMA().node_id == "MossStoryboardLayout")

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
