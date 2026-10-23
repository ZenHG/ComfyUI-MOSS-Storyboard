# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""UI 工作流 → API prompt，并提交给运行中的 ComfyUI。

为什么不用前端：前端有 `graphToPrompt()`，命令行没有。这里用**服务端自己的**
`/object_info` 反推每个节点的 widget 顺序，因此不需要任何硬编码的参数表 ——
节点改了参数，本脚本自动跟上，不会出现"脚本里的顺序过时了"。

用法：
    python tools/ui_to_api.py "<workflow.json>" --dry            # 只打印，不提交
    python tools/ui_to_api.py "<workflow.json>" --run            # 提交并等结果
    python tools/ui_to_api.py "<workflow.json>" --run --set 5.steps=4 --set 5.cfg=1.0
    python tools/ui_to_api.py "<workflow.json>" --run --api http://127.0.0.1:8188
"""

import argparse
import json
import os
import sys
import time
import urllib.request

API_DEFAULT = os.environ.get("H3_API", "http://127.0.0.1:8188")

#: 这些类型只走连线，不占 widget 槽位。
LINK_TYPES = {"MODEL", "CLIP", "VAE", "IMAGE", "LATENT", "MASK", "CONDITIONING",
              "SIGMAS", "CONTROL_NET", "CLIP_VISION", "GUIDER", "SAMPLER", "NOISE",
              "BOUNDING_BOX", "ARRAY", "AUDIO", "VIDEO"}


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def widget_names(info):
    """按服务端的 input_order 推出 widget 顺序（含 seed 的生成后控制占位）。"""
    order = info.get("input_order") or {}
    spec = info.get("input") or {}
    names = []
    for section in ("required", "optional"):
        for name in order.get(section) or []:
            s = (spec.get(section) or {}).get(name)
            if not s:
                continue
            t, opts = s[0], (s[1] if len(s) > 1 and isinstance(s[1], dict) else {})
            if opts.get("forceInput"):
                continue
            if isinstance(t, str) and t.upper() in LINK_TYPES:
                continue
            names.append(name)
            if opts.get("control_after_generate"):
                names.append("<control_after_generate>")
    return names


def all_input_names(info):
    """按槽位顺序列出全部输入名（含连线型），用于建 links。"""
    order = info.get("input_order") or {}
    return list(order.get("required") or []) + list(order.get("optional") or [])


def convert(wf, infos):
    api, skipped, warn = {}, [], []
    for n in wf.get("nodes") or []:
        nid, ntype = str(n["id"]), n["type"]
        info = infos.get(ntype)
        if info is None:
            skipped.append((nid, ntype))
            continue
        wnames = widget_names(info)
        wvals = list(n.get("widgets_values") or [])
        # 有些节点（如 PrimitiveNode 之类）widgets_values 可能带多余项，按短的截断
        if len(wvals) != len(wnames):
            warn.append("%s(%s) widget 数 %d != 期望 %d（按名前缀对齐，可能错位）"
                        % (ntype, nid, len(wvals), len(wnames)))
        inputs = {}
        for name, val in zip(wnames, wvals, strict=False):
            if name == "<control_after_generate>":
                continue
            inputs[name] = val
        api[nid] = {"class_type": ntype, "inputs": inputs,
                    "_meta": {"title": n.get("title") or ntype}}

    # 连线：连线的 to_slot 是**这个节点自己的 inputs 数组**下标。
    # 🔴 别用服务端的 input_order 推槽位：前端的 `inputs` 数组会把**只接线的输入**
    # （forceInput / MODEL / IMAGE …）挪到前面，widget 输入排在后面，两者顺序不一致。
    # 按服务端顺序硬套的结果是 `positive ← 一张 IMAGE`，报错是
    # `negative, received_type(IMAGE) mismatch input_type(STRING)`，很难反推。
    slot_names = {}
    for n in wf.get("nodes") or []:
        names = [i.get("name") for i in (n.get("inputs") or [])]
        if names:
            slot_names[str(n["id"])] = names
        else:
            info = infos.get(n["type"])
            if info is not None:
                slot_names[str(n["id"])] = all_input_names(info)
    for lid, src, sslot, dst, dslot, _typ in wf.get("links") or []:
        ds, ss = str(dst), str(src)
        if ds not in api or ss not in api:
            continue
        names = slot_names.get(ds) or []
        if dslot >= len(names):
            warn.append("link %s: 目标槽位 %d 超出 %s 的输入数（%d）" % (lid, dslot, dst, len(names)))
            continue
        api[ds]["inputs"][names[dslot]] = [ss, sslot]
    return api, skipped, warn


def apply_sets(api, pairs):
    for p in pairs:
        nid, _, kv = p.partition(".")
        key, _, val = kv.partition("=")
        if nid not in api:
            raise SystemExit("--set 指向不存在的节点 %s" % nid)
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            parsed = val
        api[nid]["inputs"][key] = parsed
        print("  override %s.%s = %r" % (nid, key, parsed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow")
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--set", action="append", default=[], help="节点id.输入名=值")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    with open(args.workflow, encoding="utf-8") as fh:
        wf = json.load(fh)
    infos = get_json(args.api + "/object_info")

    api, skipped, warn = convert(wf, infos)
    print("转换结果：%d 个节点可提交，%d 个跳过" % (len(api), len(skipped)))
    if skipped:
        print("  跳过（服务端无此节点，通常是纯前端节点）：%s" % skipped)
    for w in warn:
        print("  ⚠ " + w)
    for nid, node in sorted(api.items(), key=lambda kv: int(kv[0])):
        keys = {k: ("<link %s>" % v if isinstance(v, list) else v)
                for k, v in node["inputs"].items() if k in ("model", "clip", "vae", "images",
                                                            "template", "spec")}
        print("  %-3s %-26s %s" % (nid, node["class_type"], keys if keys else ""))
    apply_sets(api, args.set)

    if not args.run:
        print("\n（--dry 未提交。加 --run 才会真的排队）")
        return 0

    # 提交前做一次"必填是否齐全"的自检，避免把缺线的图送进去被服务端拒
    missing = []
    for nid, node in api.items():
        info = infos.get(node["class_type"]) or {}
        for name in (info.get("input_order") or {}).get("required") or []:
            s = (info.get("input") or {}).get("required", {}).get(name) or ["?"]
            if isinstance(s[0], str) and s[0].upper() in LINK_TYPES and name not in node["inputs"]:
                missing.append("%s(%s).%s" % (node["class_type"], nid, name))
    if missing:
        print("✗ 缺必填连线：%s" % missing)
        return 2

    t0 = time.time()
    res = post_json(args.api + "/prompt", {"prompt": api, "client_id": "moss-sb-test"})
    pid = res.get("prompt_id")
    if res.get("node_errors"):
        # 被服务端拒收时 history 里仍可能有条目，别把"被拒"读成"成功"
        print("✗ 服务端拒收（node_errors）：")
        for nid, info in res["node_errors"].items():
            for e in info.get("errors") or []:
                print("   节点 %s：%s | %s" % (nid, e.get("message"), e.get("details")))
        return 4
    print("\n已提交 prompt_id=%s" % pid)

    last = 0
    while time.time() - t0 < args.timeout:
        time.sleep(2)
        hist = get_json("%s/history/%s" % (args.api, pid))
        if pid in hist:
            entry = hist[pid]
            status = (entry.get("status") or {}).get("status_str")
            print("完成，用时 %.1fs，status=%s" % (time.time() - t0, status))
            if status != "success":
                for m in (entry.get("status") or {}).get("messages") or []:
                    print("  ", m)
            for nid, out in (entry.get("outputs") or {}).items():
                for kind, items in out.items():
                    for it in items:
                        print("  输出 %s.%s -> %s" % (nid, kind, it.get("filename")))
            return 0 if status == "success" else 1
        q = get_json(args.api + "/queue")
        now = len(q.get("queue_running") or [])
        if now != last:
            print("  ...排队中 running=%d pending=%d（%.0fs）"
                  % (now, len(q.get("queue_pending") or []), time.time() - t0))
            last = now

    print("超时 %ds 仍未完成" % args.timeout)
    return 3


if __name__ == "__main__":
    sys.exit(main())
