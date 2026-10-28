# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""端到端矩阵：把交付工作流按多维组合真跑一遍，逐条机检产物。

为什么要有它：`--dry` 只能证明图能提交，证明不了"3/4/6/8/9 格、横竖屏、无缝、
纯提示词路线在**别人的机器上**都出得来且尺寸对"。开源前必须用真跑的数据说话。

每条用例都做四类断言：
1. 任务 `status == success`；
2. 整图尺寸 == `core.layout` 算出的理论值（不是"看起来差不多"）；
3. 逐格数量 == N，且每格 == panel_w × panel_h；
4. 宫格线真的是白的、格内真的不是白的、各格内容互不相同（防"全黑/全同"）。

跑法：
    python tools/e2e_matrix.py                 # 全矩阵（会重写维护者本地 e2e 报告）
    python tools/e2e_matrix.py --only 9格      # 只跑名字含该串的用例（不写报告）
    python tools/e2e_matrix.py --no-doc        # 全矩阵但不重写报告
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import layout as L          # noqa: E402
from core import prompt as T          # noqa: E402
import ui_to_api as U                 # noqa: E402
from _paths import host_root  # noqa: E402  （脚本同目录，见 tools/_paths.py）

API = os.environ.get("H3_API", "http://127.0.0.1:8188")
WF = os.path.join(host_root(), "user", "default",
                  "workflows", "MOSS 故事板宫格.json")
OUT_DIR = os.path.join(host_root(), "output")
DOC = os.path.join(ROOT, "docs", "02-e2e-matrix.md")

#: (用例名, 覆盖项)。4.* 全是 Layout 节点（几何权威），5.* 是采样器。
CASES = [
    ("4格 16:9 交付默认", {}),
    ("3格 16:9 竖条", {"4.panels": 3}),
    ("6格 16:9", {"4.panels": 6}),
    ("8格 16:9 宽条", {"4.panels": 8}),
    ("9格 16:9", {"4.panels": 9}),
    ("4格 9:16 竖屏", {"4.panel_aspect": "9:16"}),
    ("6格 9:16 竖屏", {"4.panels": 6, "4.panel_aspect": "9:16"}),
    ("1格 无宫格", {"4.panels": 1}),
    ("4格 无缝无边框", {"4.gutter": 0, "4.margin": 0}),
    ("4格 粗缝 32px", {"4.gutter": 32, "4.margin": 32}),
    ("4格 init=plain", {"5.init": "plain"}),
    ("4格 小画布 edge=256", {"4.panel_edge": 256}),
    ("4格 逐格 layout", {"5.mode": "逐格", "5.sample_edge": 512}),
    ("4格 逐格 plain", {"5.mode": "逐格", "5.init": "plain", "5.sample_edge": 512}),
]

#: 随机种子池。固定 seed 会让整张矩阵出一模一样的图，等于只测了一张。
RNG = random.SystemRandom()


def recount(text, n):
    """把稿子里「恰好 4 个面板」这类显式格数改成 n，保留原措辞（恰好/正好/共）。

    为什么必须改：`core.prompt.board_mismatch` 在采样前对账「全局段写的格数」与
    「布局格数」，不等就直接报错（2026-10-27 起的守卫）。
    """
    def sub(m):
        if m.group(1) is not None:
            return m.group(0).replace(m.group(1), str(n), 1)
        return "%d panels" % n
    return T.COUNT_RE.sub(sub, text)


def fit_prompt(text, n):
    """把稿子对齐到 n 格：补/截 `[PANEL]` 段，并同步全局段的显式格数。

    🔴 只补段不改格数是不行的 —— 那条守卫两处都查。旧版 `pad_prompt` 只补段，
    于是 3/6/8/9/1 格全部被拦下（报「全局段写「恰好 4 个面板」，但布局是 N 格」），
    而端到端报告早从公开树删了，没人发现矩阵其实只剩 4 格能过。
    样例稿只有 4 段，其余格数就循环复用这 4 条描述。
    """
    text = recount(text, n)
    _, caps = T.split_panels(text)
    if not caps or len(caps) == n:
        return text                      # 已对齐：原文一字不动（保住 [STYLE] 等标签）
    caps_txt = [c for _, c in caps]
    if len(caps) > n:
        marks = list(T.PANEL_RE.finditer(text))
        return text[: marks[n].start()].rstrip()
    out = [text.rstrip()]
    for i in range(len(caps) + 1, n + 1):
        out.append("[PANEL %d]: %s" % (i, caps_txt[(i - 1) % len(caps_txt)]))
    return "\n\n".join(out)


#: 只打本机宿主，必须绕开系统代理：本机设了 `HTTP_PROXY=127.0.0.1:6910`，
#: urllib 会老实地走它，对 127.0.0.1:8188 的请求一律回 502（踩过，白跑一轮）。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url):
    with _OPENER.open(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def wait(pid, timeout=420):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        h = get("%s/history/%s" % (API, pid))
        if pid in h:
            return h[pid], time.time() - t0
    return None, time.time() - t0


def md5(path):
    if not path or not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def panel_stats(path):
    from PIL import Image, ImageStat
    im = Image.open(path).convert("RGB")
    st = ImageStat.Stat(im)
    return im.size, sum(st.mean) / 3, sum(st.stddev) / 3


def run_case(name, over, wf, infos, seed=None):
    api, _, _ = U.convert(json.loads(json.dumps(wf)), infos)
    seed = RNG.randint(1, 2 ** 32 - 1) if seed is None else seed

    # 几何权威 = Layout 节点；采样器的同名参数被 spec 覆盖，但仍同步，避免两处不一致
    lay_over = {k.split(".", 1)[1]: v for k, v in over.items() if k.startswith("4.")}
    lay = L.build_layout(
        panels=lay_over.get("panels", 4),
        panel_edge=lay_over.get("panel_edge", 496),
        panel_aspect=lay_over.get("panel_aspect", "16:9"),
        gutter=lay_over.get("gutter", 8),
        margin=lay_over.get("margin", 8),
        canvas_aspect="auto",
    )
    # 提示词补段（跟格数对齐）
    api["5"]["inputs"]["positive"] = fit_prompt(api["5"]["inputs"]["positive"], lay.panels)

    # 🔴 覆盖项必须写到**节点 4（Layout）**：几何唯一权威是它产出的 spec，
    #   采样器上的同名参数会被 spec 覆盖 ⇒ 写到 5 上等于没改（本脚本第一版就犯了这个错，
    #   整张矩阵实际只测了同一个 4 格配置）。
    for k, v in over.items():
        nid, key = k.split(".", 1)
        if nid in api:
            api[nid]["inputs"][key] = v
    api["5"]["inputs"]["seed"] = seed        # 每条用例换随机种，避免整批图雷同

    res = post(API + "/prompt", {"prompt": api, "client_id": "e2e"})
    if res.get("node_errors"):
        return {"用例": name, "结果": "拒收", "种子": seed, "细节": str(res["node_errors"])[:200]}
    pid = res["prompt_id"]
    entry, dt = wait(pid)
    if entry is None:
        return {"用例": name, "结果": "超时", "种子": seed, "细节": "%.0fs" % dt}

    status = (entry.get("status") or {}).get("status_str")
    outs = []
    for nid, out in (entry.get("outputs") or {}).items():
        for items in out.values():
            for it in items:
                if it.get("type") == "output":
                    outs.append((nid, it.get("subfolder", ""), it.get("filename")))

    grid_path = None
    panels = []
    for nid, sub, fn in outs:
        p = os.path.join(OUT_DIR, sub, fn)
        if nid == "7":
            grid_path = p
        elif nid == "8":
            panels.append(p)
    panels.sort(key=lambda p: os.path.basename(p))

    detail, ok = [], True
    if status != "success":
        ok = False
        detail.append("status=%s" % status)
    if not grid_path or not os.path.exists(grid_path):
        ok = False
        detail.append("没有整图产物")
    else:
        from PIL import Image
        size, mean, std = panel_stats(grid_path)
        if size != (lay.width, lay.height):
            ok = False
            detail.append("整图 %s != 理论 %s" % (size, (lay.width, lay.height)))
        if not (15 < mean < 248) or std < 12:
            ok = False
            detail.append("画面退化 mean=%.1f std=%.1f" % (mean, std))
        im = Image.open(grid_path).convert("RGB")
        if lay.gutter > 0:      # 竖缝中点必须是白
            px = im.getpixel((lay.margin + lay.panel_w + lay.gutter // 2, lay.margin + lay.panel_h // 2))
            if min(px) < 235:
                ok = False
                detail.append("竖缝不是白 %s" % (px,))
        if lay.margin > 0:      # 外框必须是白
            px0 = im.getpixel((2, 2))
            if min(px0) < 235:
                ok = False
                detail.append("外框不是白 %s" % (px0,))
    if len(panels) != lay.panels:
        ok = False
        detail.append("逐格数 %d != %d" % (len(panels), lay.panels))
    means = []
    for p in panels:
        s, m, sd = panel_stats(p)
        if s != (lay.panel_w, lay.panel_h):
            ok = False
            detail.append("格 %s 尺寸 %s != %s" % (os.path.basename(p), s, (lay.panel_w, lay.panel_h)))
        means.append(round(m, 1))
    # 内容雷同只在 ≥2 格时才有意义（1 格无从比较，别造出假红）
    if len(means) >= 2 and len(set(means)) < len(means) - 1:
        ok = False
        detail.append("多格内容雷同 means=%s" % means[:6])

    return {"用例": name, "结果": "OK" if ok else "FAIL", "种子": seed,
            "画布": "%dx%d" % (lay.width, lay.height), "格": "%dx%d×%d" % (lay.panel_w, lay.panel_h, lay.panels),
            "耗时": "%.0fs" % dt, "细节": "；".join(detail) or "-",
            "_means": means, "_grid": grid_path}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-doc", action="store_true", help="不重写本地 e2e 报告")
    args = ap.parse_args()

    with open(WF, encoding="utf-8") as fh:
        wf = json.load(fh)
    infos = get(API + "/object_info")
    rows = []
    for name, over in CASES:
        if args.only and args.only not in name:
            continue
        print("→ %s" % name, flush=True)
        r = run_case(name, over, wf, infos)
        print("   %s %s %s" % (r["结果"], r.get("画布", ""), r.get("细节", "")), flush=True)
        rows.append(r)

    ok = sum(1 for r in rows if r["结果"] == "OK")

    # 随机种必须真的生效：同参数换种子 ⇒ 整图字节必须不同。
    # 不查这条，"随机 seed" 可能只是 UI 上的摆设（值被覆盖/没送进图里）。
    if not args.only:
        a = run_case("随机种·A", {}, wf, infos)
        b = run_case("随机种·B", {}, wf, infos)
        rows += [a, b]
        ma = md5(a.get("_grid"))
        mb = md5(b.get("_grid"))
        same = (ma == mb and ma is not None)
        for r in (a, b):
            if same:
                r["结果"] = "FAIL"
                r["细节"] = "换种子后图完全一样（%s）" % ma
        print("   随机种差异：%s vs %s → %s" % (ma[:8] if ma else "-", mb[:8] if mb else "-",
                                            "雷同(FAIL)" if same else "不同(OK)"))

    ok = sum(1 for r in rows if r["结果"] == "OK")
    print("\n结果：%d/%d 通过" % (ok, len(rows)))

    lines = ["| 用例 | 结果 | 种子 | 画布 | 格 | 耗时 | 细节 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["用例"], r["结果"], r.get("种子", "-"), r.get("画布", "-"), r.get("格", "-"),
            r.get("耗时", "-"), r.get("细节", "-")))
    table = "\n".join(lines)
    write_doc = not (args.only or args.no_doc)
    with open(DOC if write_doc else os.devnull, "w", encoding="utf-8") as fh:
        fh.write("# 02 · 端到端矩阵（真跑）\n\n")
        fh.write("宿主 %s ｜ 工作流 `%s`\n\n" % (get(API + "/system_stats")["system"]["comfyui_version"],
                                            os.path.basename(WF)))
        fh.write("判定：任务成功 + 整图尺寸 == 理论值 + 逐格数量/尺寸 == 理论值 + 宫格线为白 + "
                 "格内非白 + 各格内容不雷同。\n\n")
        fh.write(table + "\n")
    if write_doc:
        print("报告已写出 %s" % DOC)
    else:
        print("（部分运行 / --no-doc：未重写 %s）" % DOC)
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
