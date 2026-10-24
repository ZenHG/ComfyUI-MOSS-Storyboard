#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""构建/验证**干净分发包**（用户实际会装到 `custom_nodes/` 的那一份）。

与 H 仓 `make_minimal_bundle.py` 同一口径：
- 只收 `git ls-files` 里的文件（工作区里的临时件永远不进包）；
- 按 `.comfyignore` 排除开发设施；
- `--verify` 把产出目录当**独立包**加载，断言节点齐全、前端在位、
  编辑器/宫格/裁格真的能跑，并断言没有模块来自原仓（证明副本自足）。

用法：
    python tools/make_dist.py --out dist/moss                # 构建
    python tools/make_dist.py --out dist/moss --zip          # 再打 zip
    python tools/make_dist.py --out dist/moss --verify       # 构建 + 独立加载
    COMFYUI_PATH=<ComfyUI> python tools/make_dist.py ... --verify

退出码：0 = 通过；1 = 有失败；2 = 缺宿主（只影响 --verify）。
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_release_ready as R  # noqa: E402

PKG = "ComfyUI-MOSS-Storyboard"
REQUIRED = [
    "__init__.py", "nodes.py", "pyproject.toml", "requirements.txt", "LICENSE",
    "README.md", "THIRD-PARTY-NOTICES.md",
    "core/editor.py", "core/presets.py", "core/layout.py", "core/prompt.py",
    "core/pixels.py", "core/sampling.py",
    "v3/_compat.py", "v3/nodes_v3.py", "v3/entrypoint.py",
    "web/moss_sb_editor.js", "web/moss_sb_i18n.js", "web/moss_sb_i18n_ui.js",
    "examples/moss-layout-editor-poster.json", "examples/moss-layout-editor-storyboard.json",
    "examples/moss-storyboard-4panels-ui.json", "examples/moss-storyboard-4panels-edit.json",
]
DEV_PREFIXES = ("tests/", "tools/", "docs/", ".github/")
FAIL = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)
    return cond


def tracked():
    out = subprocess.check_output(["git", "-C", ROOT, "ls-files", "-z"])
    return [p for p in out.decode("utf-8", "replace").split("\x00") if p]


def build(out, force=False, make_zip=False):
    rules = R.comfyignore_rules()
    files = [f for f in tracked() if not R.ignored(f, rules)]
    missing = [f for f in REQUIRED if f not in files]
    check("必需运行期文件都在包内", not missing, "缺：%s" % "；".join(missing[:6]) if missing else "")
    dev = [f for f in files if f.startswith(DEV_PREFIXES)]
    check("包内没有开发设施", not dev, "；".join(dev[:5]) if dev else "")
    cjk = [f for f in files if re.search(r"[\u4e00-\u9fff]", f)]
    check("包内路径全 ASCII", not cjk, "；".join(cjk[:5]) if cjk else "")

    if os.path.exists(out):
        if not force:
            raise SystemExit("输出目录已存在：%s（加 --force 覆盖）" % out)
        shutil.rmtree(out)
    pkg_dir = os.path.join(out, PKG)
    os.makedirs(pkg_dir)
    total = 0
    for rel in files:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(pkg_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        total += os.path.getsize(src)
    print("  包内 %d 个文件 / %.1f MB → %s" % (len(files), total / 1e6, pkg_dir))

    # 自足性：不能有符号链接、字节码、__pycache__
    bad = []
    for base, dirs, names in os.walk(pkg_dir):
        for d in list(dirs):
            if d == "__pycache__":
                bad.append(os.path.join(base, d))
        for n in names:
            p = os.path.join(base, n)
            if os.path.islink(p) or n.endswith((".pyc", ".pyo")):
                bad.append(p)
    check("包内无符号链接/字节码", not bad, "；".join(bad[:5]) if bad else "")

    zpath = None
    if make_zip:
        zpath = shutil.make_archive(os.path.join(out, "moss-storyboard"), "zip",
                                    root_dir=out, base_dir=PKG)
        print("  zip：%s（%.1f MB）" % (zpath, os.path.getsize(zpath) / 1e6))
    return pkg_dir, zpath


def host_root():
    p = os.environ.get("COMFYUI_PATH", "").strip()
    if p and os.path.isdir(p):
        return p
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from _paths import host_root as hr
        return hr()
    except Exception:
        return ""


def load_host(host):
    if not host or not os.path.isdir(host):
        print("  [SKIP] 没有宿主：需要 COMFYUI_PATH 才能 --verify")
        return False
    if host not in sys.path:
        sys.path.insert(0, host)
    return True


def verify(pkg_dir, host):
    if not load_host(host):
        return 2
    print("\n独立加载：%s" % pkg_dir)
    spec = importlib.util.spec_from_file_location(
        "moss_dist", os.path.join(pkg_dir, "__init__.py"),
        submodule_search_locations=[pkg_dir])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["moss_dist"] = mod
    spec.loader.exec_module(mod)
    V1 = importlib.import_module("moss_dist.nodes")
    PR = importlib.import_module("moss_dist.core.presets")

    check("节点 5 个", len(V1.NODE_CLASS_MAPPINGS) == 5, sorted(V1.NODE_CLASS_MAPPINGS))
    check("WEB_DIRECTORY = ./web", str(mod.WEB_DIRECTORY).replace("\\", "/").rstrip("/") == "./web",
          mod.WEB_DIRECTORY)
    for js in ("moss_sb_editor.js", "moss_sb_i18n.js", "moss_sb_i18n_ui.js"):
        check("前端文件 %s" % js, os.path.isfile(os.path.join(pkg_dir, "web", js)))

    ed = V1.MossLayoutEditor()
    grid_state = PR.build("grid", grid={"cols": 2, "rows": 2, "panel_aspect": "16:9",
                                        "panel_edge": 496, "gutter": 8, "margin": 8})
    g = ed.build(editor_state=json.dumps(grid_state, ensure_ascii=False))
    check("编辑器 grid 出 prompt/spec", isinstance(g[0], str) and "[PANEL 1]" in g[0]
          and g[3].startswith("MOSS-SB1") and tuple(g[4].shape)[1:3] == (g[2], g[1]),
          (g[1], g[2], g[3][:40]))
    single = PR.build("title-body", canvas=(1536, 864))
    s = ed.build(editor_state=json.dumps(single, ensure_ascii=False))
    check("编辑器 single 出 layout JSON", isinstance(s[0], str) and '"items"' in s[0]
          and s[1] == 1536 and s[2] == 864, (s[1], s[2]))

    lay_node = V1.MossStoryboardLayout()
    lay = lay_node.build(prompt="[STYLE] x\n\n[PANEL 1] 中景，x", panels=4,
                         panel_aspect="16:9", panel_edge=496, canvas_aspect="auto",
                         columns=0, rows=0, gutter=8, margin=8,
                         cell_gray=25, invert=False, label="object")
    check("宫格几何出 spec", str(lay[4]).startswith("MOSS-SB1"), str(lay[4])[:60])
    import torch
    crop = V1.MossStoryboardCrop()
    img = torch.zeros(1, lay[1], lay[0], 3)
    out = crop.run(image=img, target_aspect="keep", padding_mode="crop", resize_edge=0,
                   shave=0, spec=lay[4], columns=0, rows=0, show_guides=False, index=-1)
    check("裁格 4 张", tuple(out[0].shape)[0] == 4, tuple(out[0].shape))

    # 不许有模块来自原仓
    leaked = []
    for name, m in list(sys.modules.items()):
        if name != "moss_dist" and not name.startswith("moss_dist."):
            continue
        f = getattr(m, "__file__", None)
        if f and not os.path.abspath(f).startswith(os.path.abspath(pkg_dir) + os.sep):
            leaked.append((name, f))
    check("没有任何模块来自原仓", not leaked, leaked[:3])
    print("\n结果：通过 %d / 失败 %d" % (CHECKS - len(FAIL), len(FAIL)))
    return 1 if FAIL else 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "moss-dist"))
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv[1:])
    pkg_dir, zpath = build(a.out, force=a.force, make_zip=a.zip)
    if a.verify:
        return verify(pkg_dir, host_root())
    print("\n构建完成（未 verify）。加 --verify 从干净副本独立加载。")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
