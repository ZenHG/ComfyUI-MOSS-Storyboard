# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""真宿主冒烟：在 ComfyUI 进程内加载本包，验证 V1/V3 两条出口与节点真跑。

不用 GPU、不加载权重，只证明：
- 两条出口都能注册，清单一致；
- 每个节点的 schema 都构造得出来（V3 是加载时就取 schema，构造失败 = 节点消失）；
- 纯节点（Layout / Crop / Paste / Compiler）真跑一遍，产物形状与 spec 自洽。

跑法：`python tools/smoke_nodes.py`（宿主根见 tools/_paths.py；可用 COMFYUI_PATH 覆盖）
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
from _paths import host_root

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFY = host_root()
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def load_pkg(name="moss_sb"):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(PKG_DIR, "__init__.py"))
    spec = importlib.util.spec_from_loader(name, loader, origin=os.path.join(PKG_DIR, "__init__.py"),
                                           is_package=True)
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [PKG_DIR]
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def main():
    if COMFY not in sys.path:
        sys.path.insert(0, COMFY)
    pkg = load_pkg()
    print("\n== 1. 加载摘要 ==")
    check("__version__ 存在", bool(getattr(pkg, "__version__", "")))
    check("默认出口 = v3", pkg.NODE_API == "v3", "实际 %s（原因 %s）" % (pkg.NODE_API, pkg.NODE_API_FALLBACK_REASON))

    print("\n== 2. V1 契约 ==")
    import torch
    from moss_sb import nodes as V1
    from moss_sb.core import editor as E
    from moss_sb.core import layout as L
    check("V1 注册 6 个节点", len(V1.NODE_CLASS_MAPPINGS) == 6, str(sorted(V1.NODE_CLASS_MAPPINGS)))
    for name, cls in V1.NODE_CLASS_MAPPINGS.items():
        it = cls.INPUT_TYPES()
        req = list(it.get("required") or {})
        opt = list(it.get("optional") or {})
        ok = bool(req) and hasattr(cls, cls.FUNCTION) and len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES)
        check("%s 契约完整（必填 %d / 可选 %d）" % (name, len(req), len(opt)), ok)
        missing_tip = [k for section in ("required", "optional")
                       for k, spec in (it.get(section) or {}).items()
                       if not (isinstance(spec, tuple) and len(spec) > 1 and spec[1].get("tooltip"))]
        check("%s 每个参数都有 tooltip" % name, not missing_tip, str(missing_tip))

    print("\n== 3. V3 出口 ==")
    from moss_sb.v3.nodes_v3 import NODES
    check("V3 清单 6 个节点", len(NODES) == 6, str([c.__name__ for c in NODES]))
    for cls in NODES:
        try:
            sch = cls.GET_SCHEMA()
            ids = [i.id for i in sch.inputs]
            v1 = V1.NODE_CLASS_MAPPINGS[cls.__name__].INPUT_TYPES()
            want = list((v1.get("required") or {})) + list((v1.get("optional") or {}))
            check("%s schema 可构造（%d 入参）" % (cls.__name__, len(ids)), True)
            check("%s 入参名与顺序 == V1" % cls.__name__, ids == want,
                  "" if ids == want else "V3=%s V1=%s" % (ids, want))
            seed_in = [i for i in sch.inputs if i.id == "seed"]
            if seed_in:
                d = seed_in[0].as_dict() if hasattr(seed_in[0], "as_dict") else {}
                check("%s 的 seed 带 control_after_generate（否则与 V1 差一个槽）" % cls.__name__,
                      bool(d.get("control_after_generate")))
        except Exception as e:
            check("%s schema 可构造" % cls.__name__, False, "%s: %s" % (type(e).__name__, e))

    print("\n== 4. 纯节点真跑 ==")
    lay_node = V1.MossStoryboardLayout()
    blob = ("[STYLE]: 雨夜咖啡店，35mm 胶片\n" + "\n".join(
        "[PANEL %d]: 第 %d 格的中景描述" % (i, i) for i in range(1, 9)))
    w, h, cols, rows, spec, text, tpl, report, elements = lay_node.build(
        prompt=blob, panels=8, panel_aspect="16:9", panel_edge=512, canvas_aspect="auto",
        columns=0, rows=0, gutter=8, margin=8, cell_gray=25, invert=False, label="object")
    check("Layout: 8 格 = 2 行 4 列", (rows, cols) == (2, 4), "%d行%d列" % (rows, cols))
    check("Layout: 画布 2096x608", (w, h) == (2096, 608), "%dx%d" % (w, h))
    check("Layout: 模板形状自洽", tuple(tpl.shape) == (1, h, w, 3), str(tuple(tpl.shape)))
    check("Layout: spec 前缀正确", spec.startswith("MOSS-SB1"), spec[:40])
    check("Layout: 布局文本是 JSON", text.startswith("{") and '"bbox_format":"yxyx"' in text, text[:60])

    from moss_sb.core import sampling as S  # noqa: F401  (只为确认宿主 import 链通)
    crop_node = V1.MossStoryboardCrop()
    grid = tpl * 0.5
    panels_img, preview, creport, cidx = crop_node.run(image=grid, target_aspect="keep", padding_mode="crop",
                                                       resize_edge=0, shave=0, spec=spec,
                                                       columns=0, rows=0, show_guides=True)
    check("Crop: 8 张", panels_img.shape[0] == 8, str(tuple(panels_img.shape)))
    check("Crop: 单格 512x288", tuple(panels_img.shape[1:3]) == (288, 512), str(tuple(panels_img.shape)))
    check("Crop: index 默认 -1 时输出全部", cidx == -1, str(cidx))
    try:
        crop_node.run(image=grid, target_aspect="keep", padding_mode="crop", resize_edge=0, shave=0,
                      spec="", columns=0, rows=0, show_guides=False)
        check("Crop: 无 spec 且无行列 → 报错", False, "未抛异常")
    except Exception as e:
        check("Crop: 无 spec 且无行列 → 报错", "必须填 columns" in str(e), str(e).splitlines()[0][:80])

    # ---- Paste：Crop 的逆运算 ----
    one, _, _, one_idx = crop_node.run(image=grid, target_aspect="keep", padding_mode="crop",
                                       resize_edge=0, shave=0, spec=spec, index=3)
    check("Crop: index=3 只出 1 张", one.shape[0] == 1 and one_idx == 3,
          "%d 张 / idx=%s" % (one.shape[0], one_idx))
    paste_node = V1.MossStoryboardPaste()
    pasted, marked, prep = paste_node.run(grid=grid, panel=one * 0.5, index=one_idx, shave=0, spec=spec)
    check("Paste: 画布尺寸不变", tuple(pasted.shape) == tuple(grid.shape), str(tuple(pasted.shape)))
    tgt = L.parse_spec(spec).cell(3)
    check("Paste: 目标格被写入",
          torch.equal(pasted[0, tgt.y:tgt.y + tgt.h, tgt.x:tgt.x + tgt.w, :], (one * 0.5)[0]))
    check("Paste: 非目标格未动",
          torch.equal(pasted[0, :tgt.y, :, :], grid[0, :tgt.y, :, :]))
    check("Paste: guides 关闭时 marked == grid", torch.equal(marked, grid))
    try:
        paste_node.run(grid=grid, panel=one, index=99, shave=0, spec=spec)
        check("Paste: index 越界 → 报错", False, "未抛异常")
    except Exception as e:
        check("Paste: index 越界 → 报错", "越界" in str(e), str(e).splitlines()[0][:80])
    # 接线方式：Crop.index 直接喂给 Paste.index，不需要手打
    wired, _, _ = paste_node.run(grid=grid, panel=one, index=one_idx, shave=0, spec=spec)
    check("Paste: 用 Crop 回传的 index 结果一致",
          torch.equal(wired[0, tgt.y:tgt.y + tgt.h, tgt.x:tgt.x + tgt.w, :], one[0]))

    comp_node = V1.MossPromptCompiler()
    cp, crep, cstate = comp_node.compile('{"caption":"一盏白瓷杯"}')
    check("Compiler: caption 出纯文本", cp == "一盏白瓷杯" and cstate == "", (cp, cstate))
    check("Compiler: caption report 说明无布局", "caption" in crep and "state" in crep, crep)
    draft = {
        "width": 1536, "height": 1024,
        "bbox_coord_space": "abs", "bbox_format": "xyxy",
        "summary": {"caption": "测试海报"},
        "items": [{"label": "object", "caption": "一只杯子", "bbox": [176, 224, 800, 864]}],
    }
    lp, lrep, lstate = comp_node.compile(json.dumps(draft, ensure_ascii=False))
    check("Compiler: layout 出原生布局 JSON",
          json.loads(lp)["bbox_coord_space"] == "abs_centralized", lp[:60])
    try:
        E.parse_state(lstate)
        check("Compiler: state 通过编辑器校验", True)
    except Exception as exc:
        check("Compiler: state 通过编辑器校验", False, str(exc))
    try:
        comp_node.compile('{"caption":"x","width":1024}')
        check("Compiler: 混合草稿 → 报错", False, "未抛异常")
    except Exception as exc:
        check("Compiler: 混合草稿 → 报错", "未知字段" in str(exc), str(exc).splitlines()[0][:80])

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
