# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""前端静态一致性机检（零 GPU、零浏览器、秒级）。

为什么单独一个工具：`test_i18n.mjs` 测的是**纯函数行为**，它看不见三类问题——
词表里混进了中文、示例图的标题把 i18n 锁死、前端文件根本没被宿主登记。
这三类都只在"读文件 + 读工作流"时才现形，所以归到这里。

判据（5 项，任何一项失败即非零退出）：
- F1 英文面向的字段不许出现中日韩字符或全角标点（`NODES.*.en` / `TIPS.*.en`）
- F2 `web/*.js` 语法可解析（`node --check`，ESM 模式）
- F3 `WEB_DIRECTORY` 非 None、目录存在、`_ui.js` 里 import 的本地文件都存在
- F4 工作流里本包节点的 `title` 必须**能被 i18n 换前缀**
      （空 / 等于中英默认名 / 以默认名开头；写死自定义标题 ⇒ 中英切换对它无效）
- F5 `TIPS` 的键都能对上真实节点与真实参数名（防改名后残留孤儿键）

用法：
    python tools/check_frontend.py                       # 默认查 examples/*.json
    python tools/check_frontend.py <workflow.json> ...   # 指定工作流
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = [], []

#: 中日韩统一表意文字 + 全角标点（英文文案里这两类都不该出现）
CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e\u30a0-\u30ff]")

NODE_BINARIES = ("node", "node.exe")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def find_node():
    for b in NODE_BINARIES:
        p = shutil.which(b)
        if p:
            return p
    for c in (r"C:/Program Files/nodejs/node.exe",
              shutil.which("node") or ""):
        if os.path.exists(c):
            return c
    return None


def load_tables():
    """粗解析 i18n.js 里的四张表（不执行 JS，避免依赖浏览器 API）。"""
    src = open(os.path.join(ROOT, "web", "moss_sb_i18n.js"), encoding="utf-8").read()

    def block(name):
        m = re.search(r"export const %s = \{(.*?)\n\};" % name, src, re.S)
        return m.group(1) if m else ""

    nodes = dict(re.findall(r'(\w+):\s*\{\s*zh:\s*"([^"]*)"', block("NODES")))
    nodes_en = dict(re.findall(r'(\w+):\s*\{\s*zh:\s*"[^"]*",\s*en:\s*"([^"]*)"', block("NODES")))
    tips = block("TIPS")
    return src, nodes, nodes_en, tips


def f1_english_clean(nodes_en, tips):
    bad = [k for k, v in nodes_en.items() if CJK.search(v)]
    check("F1a 节点英文名不含中文/全角标点", not bad, str(bad))

    entries = re.findall(r'"((?:\*|MossStoryboard\w+)\.(\w+)":\s*\{[^}]*\})', tips, re.S)
    bad_tip = []
    for key, _, body in [(e[0], e[1], e[0]) for e in entries]:
        m = re.search(r'en:\s*"((?:[^"\\]|\\.)*)"', body, re.S)
        text = m.group(1) if m else ""
        if CJK.search(text):
            bad_tip.append(key)
    check("F1b 英文提示不含中文/全角标点（%d 条）" % len(entries), not bad_tip, str(bad_tip[:4]))


def f2_syntax(node):
    files = sorted(f for f in os.listdir(os.path.join(ROOT, "web")) if f.endswith(".js"))
    if node is None:
        check("F2 前端语法（跳过：没有 node）", True, "CI 里由 frontend job 兜")
        return
    bad = []
    for f in files:
        p = os.path.join(ROOT, "web", f)
        # --experimental-default-type=module：.js 按 ESM 解析，import 语法才合法
        r = subprocess.run([node, "--check", "--experimental-default-type=module", p],
                           capture_output=True, text=True)
        if r.returncode != 0:
            bad.append("%s: %s" % (f, (r.stderr or "").strip().splitlines()[:1]))
    check("F2 web/*.js 语法可解析（%d 个文件）" % len(files), not bad, str(bad))


def f3_wiring():
    init = open(os.path.join(ROOT, "__init__.py"), encoding="utf-8").read()
    m = re.search(r'WEB_DIRECTORY\s*=\s*"([^"]+)"', init)
    check("F3a __init__.py 的 WEB_DIRECTORY 不是 None", bool(m), m.group(1) if m else "未设置")
    if not m:
        return
    web_dir = os.path.normpath(os.path.join(ROOT, m.group(1)))
    check("F3b WEB_DIRECTORY 目录存在", os.path.isdir(web_dir), web_dir)
    ui = os.path.join(web_dir, "moss_sb_i18n_ui.js")
    if not os.path.exists(ui):
        check("F3c _ui.js 存在", False, ui)
        return
    check("F3c _ui.js 存在", True)
    src = open(ui, encoding="utf-8").read()
    # `../../scripts/*` 是**宿主**提供的前端模块（app.js / api.js），不在本包里
    local = [p for p in re.findall(r'from\s+"(\.[^"]+)"', src)
             if not p.startswith("../../scripts/")]
    missing = [p for p in local
               if not os.path.exists(os.path.normpath(os.path.join(web_dir, p)))]
    check("F3d _ui.js 引用的本地文件都存在", not missing, str(missing))


def f4_titles(paths, nodes_zh, nodes_en):
    """工作流里本包节点的标题必须能被 swapTitle 换掉（否则中英切换对它无效）。"""
    bad, seen = [], 0
    for p in paths:
        if not os.path.exists(p):
            continue
        wf = json.load(open(p, encoding="utf-8"))
        for n in wf.get("nodes") or []:
            t = n.get("type")
            if t not in nodes_zh:
                continue
            seen += 1
            title = n.get("title")
            if not title:
                continue                     # 空 ⇒ i18n 会填默认名
            ok = any(title == d or title.startswith(d) for d in (nodes_zh[t], nodes_en.get(t, "")))
            if not ok:
                bad.append("%s: %s.title=%r" % (os.path.basename(p), t, title))
    check("F4 工作流标题可被 i18n 换前缀（检查 %d 个节点）" % seen, not bad, str(bad[:3]))


def f5_tip_keys(tips, param_names):
    keys = re.findall(r'"(\*|MossStoryboard\w+)\.(\w+)":', tips)
    orphan = [(t, p) for t, p in keys if t != "*" and (t, p) not in param_names]
    check("F5 提示键都能对上真实节点与参数（%d 条）" % len(keys), not orphan, str(orphan[:4]))


def main(argv):
    src, nodes_zh, nodes_en, tips = load_tables()

    # 真实参数名集合：直接从 nodes.py 的 INPUT_TYPES 取（唯一真相源）。
    # ⚠ nodes.py 用相对导入（`from .core import ...`），裸 `import nodes` 会 ImportError；
    #   必须按"包"加载（给模块塞 __path__），与 tools/smoke_nodes.py 同一手法。
    loader = importlib.machinery.SourceFileLoader("moss_sb_fe", os.path.join(ROOT, "__init__.py"))
    spec = importlib.util.spec_from_loader("moss_sb_fe", loader,
                                           origin=os.path.join(ROOT, "__init__.py"), is_package=True)
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [ROOT]
    sys.modules["moss_sb_fe"] = mod
    loader.exec_module(mod)
    from moss_sb_fe.nodes import NODE_CLASS_MAPPINGS as V1
    param_names = set()
    for name, cls in V1.items():
        it = cls.INPUT_TYPES()
        for section in ("required", "optional"):
            for pname in (it.get(section) or {}):
                param_names.add((name, pname))

    print("== F1 英文文案纯净度 ==")
    f1_english_clean(nodes_en, tips)
    print("\n== F2 前端语法 ==")
    f2_syntax(find_node())
    print("\n== F3 前端接线 ==")
    f3_wiring()

    wfs = argv[1:] or [os.path.join(ROOT, "examples", f) for f in sorted(os.listdir(os.path.join(ROOT, "examples")))
                       if f.endswith(".json")]
    print("\n== F4 工作流标题可换语言 ==")
    f4_titles(wfs, nodes_zh, nodes_en)
    print("\n== F5 提示键对账 ==")
    f5_tip_keys(tips, param_names)

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
