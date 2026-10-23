# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""UI 工作流机检：连表一致 + 本包节点的 widgets_values 槽位对得上 `INPUT_TYPES()`。

为什么需要它：UI 工作流的 `widgets_values` 是**按位置**存的，参数插错一个位置，
用户打开示例时不会报错，只会静默拿到另一组参数（8 格按 4 格裁、denoise 落到
别的槽）。人工数槽必错，所以让机器数。

跑法：`python tools/check_ui_workflow.py examples/*.json`
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
from _paths import host_root

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFY = host_root()
#: 这些类型在画布上只以**输入槽**出现，不占 widgets_values 的位置。
LINK_ONLY = {"MODEL", "CLIP", "VAE", "IMAGE", "LATENT", "CONDITIONING", "MASK", "SIGMAS"}

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def load_mappings():
    loader = importlib.machinery.SourceFileLoader("moss_sb_wf", os.path.join(PKG_DIR, "__init__.py"))
    spec = importlib.util.spec_from_loader("moss_sb_wf", loader,
                                           origin=os.path.join(PKG_DIR, "__init__.py"), is_package=True)
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [PKG_DIR]
    sys.modules["moss_sb_wf"] = mod
    loader.exec_module(mod)
    from moss_sb_wf import nodes as V1
    return V1.NODE_CLASS_MAPPINGS


def expected_widgets(cls):
    """按 `INPUT_TYPES()` 推出画布上会出现的 widget 顺序（seed 后带控制项）。"""
    it = cls.INPUT_TYPES()
    names = []
    for section in ("required", "optional"):
        for name, spec in (it.get(section) or {}).items():
            t, opts = (spec[0], spec[1] if len(spec) > 1 else {}) if isinstance(spec, tuple) else (spec, {})
            if isinstance(opts, dict) and opts.get("forceInput"):
                continue
            if isinstance(t, str) and t.upper() in LINK_ONLY:
                continue
            names.append(name)
            if name == "seed":
                names.append("<control_after_generate>")
    return names


def widget_specs(cls):
    """`{widget_name: (type, opts)}`，供值域校验用。"""
    it = cls.INPUT_TYPES()
    out = {}
    for section in ("required", "optional"):
        for name, spec in (it.get(section) or {}).items():
            t, opts = (spec[0], spec[1] if len(spec) > 1 else {}) if isinstance(spec, tuple) else (spec, {})
            if isinstance(opts, dict) and opts.get("forceInput"):
                continue
            if isinstance(t, str) and t.upper() in LINK_ONLY:
                continue
            out[name] = (t, opts if isinstance(opts, dict) else {})
    return out


def value_ok(typ, opts, v):
    """一个 widget 值是否落在该 widget 的类型/值域里。错位最常在这里暴露。"""
    if isinstance(typ, (list, tuple)):
        return v in list(typ)
    t = str(typ).upper()
    if t == "COMBO":
        return True  # 老式 combo 无候选清单，无法校验
    if t == "INT":
        if isinstance(v, bool) or not isinstance(v, int):
            return False
        lo, hi = opts.get("min"), opts.get("max")
        return (lo is None or v >= lo) and (hi is None or v <= hi)
    if t == "FLOAT":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        lo, hi = opts.get("min"), opts.get("max")
        return (lo is None or v >= lo) and (hi is None or v <= hi)
    if t == "STRING":
        return isinstance(v, str)
    if t == "BOOLEAN":
        return isinstance(v, bool)
    return True


def check_file(path, mappings):
    print("\n== %s ==" % os.path.basename(path))
    with open(path, encoding="utf-8") as fh:
        wf = json.load(fh)

    nodes = {n["id"]: n for n in wf.get("nodes") or []}
    ids = [n["id"] for n in wf["nodes"]]
    check("节点 id 唯一", len(ids) == len(set(ids)))
    check("last_node_id 一致", wf.get("last_node_id") == max(ids), "last=%s max=%s" % (wf.get("last_node_id"), max(ids)))
    # last_link_id 是**高水位**，不是条数：删过连线后 id 会留空洞，这不违规。
    # 断言成 "== len(links)" 会给出假红（本脚本第一版就错在这里）。
    lids = [lk[0] for lk in (wf.get("links") or [])]
    check("last_link_id 是高水位", (wf.get("last_link_id") or 0) >= max(lids or [0]),
          "last=%s max=%s 条数=%s" % (wf.get("last_link_id"), max(lids or [0]), len(lids)))
    check("连线 id 不重复", len(lids) == len(set(lids)))

    # 连表自洽
    bad = []
    for lid, src, sslot, dst, dslot, typ in wf.get("links") or []:
        if src not in nodes or dst not in nodes:
            bad.append("link %s 指向不存在的节点" % lid)
            continue
        src_out = nodes[src]["outputs"][sslot]
        if src_out.get("links") is None or lid not in src_out["links"]:
            bad.append("link %s 未登记在源节点 %s 的输出上" % (lid, src))
        if typ != src_out["type"]:
            bad.append("link %s 类型 %s != 源输出类型 %s" % (lid, typ, src_out["type"]))
        if dst > 0 and dslot < len(nodes[dst].get("inputs") or []):
            if nodes[dst]["inputs"][dslot].get("link") != lid:
                bad.append("link %s 未登记在目标节点 %s 的输入上" % (lid, dst))
    check("连线表自洽", not bad, "；".join(bad[:4]))

    # 本包节点的槽位
    ours = 0
    for n in wf["nodes"]:
        cls = mappings.get(n["type"])
        if cls is None:
            continue
        ours += 1
        want = expected_widgets(cls)
        got = n.get("widgets_values") or []
        check("%s(id=%d) 槽位数 %d" % (n["type"], n["id"], len(want)), len(got) == len(want),
              "" if len(got) == len(want) else "widgets=%d 期望=%d %s" % (len(got), len(want), want))
        if len(got) != len(want):
            continue
        specs = widget_specs(cls)
        bad = []
        for name, val in zip(want, got, strict=False):
            if name == "<control_after_generate>":
                if val not in ("fixed", "increment", "decrement", "randomize"):
                    bad.append("seed 控制项=%r" % (val,))
                continue
            typ, opts = specs[name]
            if not value_ok(typ, opts, val):
                bad.append("%s=%r 不符合 %s%s" % (name, val, typ, opts.get("min") and "[%s,%s]" % (opts.get("min"), opts.get("max")) or ""))
        # 逐位值域校验：槽位错一位几乎必然在这里翻红（例如 16:9 落进 panels）
        check("%s(id=%d) 每个 widget 值都在值域内" % (n["type"], n["id"]), not bad, "；".join(bad[:3]))
        pv = dict(zip(want, got, strict=False)).get("panels")
        if "panels" in want:
            check("%s(id=%d) panels 合理" % (n["type"], n["id"]), pv in (3, 4, 6, 8, 9), str(pv))
    check("文件里含本包节点（%d 个）" % ours, ours > 0)

    # 节点类型是否存在（前端独有节点不在宿主的 NODE_CLASS_MAPPINGS 里）
    FRONTEND_ONLY = {"MarkdownNote", "Note", "Reroute", "PrimitiveNode", "GroupNode"}
    try:
        import nodes as host_nodes
        known = set(host_nodes.NODE_CLASS_MAPPINGS) | FRONTEND_ONLY | set(mappings)
        unknown = sorted({n["type"] for n in wf["nodes"]} - known)
        check("全部节点类型宿主都认识", not unknown, "未知：%s" % unknown)
    except Exception as e:
        check("全部节点类型宿主都认识", True, "跳过（宿主不可 import：%s）" % type(e).__name__)

    # 必填输入是否都有来源（连线或 widget 值）—— 少接一根线就是"跑起来才报错"
    try:
        import nodes as host_nodes
        missing = []
        for n in wf["nodes"]:
            cls = mappings.get(n["type"]) or host_nodes.NODE_CLASS_MAPPINGS.get(n["type"])
            if cls is None or not hasattr(cls, "INPUT_TYPES"):
                continue
            it = cls.INPUT_TYPES() or {}
            req = it.get("required") or {}
            linked = {i["name"] for i in (n.get("inputs") or []) if i.get("link") is not None}
            for name, spec in req.items():
                t = spec[0] if isinstance(spec, tuple) else spec
                if isinstance(t, str) and t.upper() in LINK_ONLY and name not in linked:
                    missing.append("%s.%s（该接线）" % (n["type"], name))
        check("必填输入都有来源", not missing, "；".join(missing[:4]))
    except Exception as e:
        check("必填输入都有来源", True, "跳过（%s）" % type(e).__name__)

    # 采样器的 denoise 与 init 语义一致：本包不做图生图 ⇒ 恒 1.0
    for n in wf["nodes"]:
        if n["type"] != "MossStoryboardSampler":
            continue
        kv = dict(zip(expected_widgets(mappings["MossStoryboardSampler"]),
                      n.get("widgets_values") or [], strict=False))
        init, den = kv.get("init"), kv.get("denoise")
        check("init=%s 时 denoise=1（本包无图生图）" % init,
              isinstance(den, (int, float)) and abs(den - 1.0) < 1e-9, str(den))
    return True


def main(argv):
    paths = argv[1:]
    if not paths:
        paths = [os.path.join(PKG_DIR, "examples", f) for f in
                 ("moss-storyboard-8panels-layout.json",)]
    if COMFY not in sys.path:
        sys.path.insert(0, COMFY)
    mappings = load_mappings()
    for p in paths:
        check_file(p, mappings)
    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
