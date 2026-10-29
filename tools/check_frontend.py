# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""前端静态一致性机检（零 GPU、零浏览器、秒级）。

为什么单独一个工具：`test_i18n.mjs` 测的是**纯函数行为**，它看不见三类问题——
词表里混进了中文、示例图的标题把 i18n 锁死、前端文件根本没被宿主登记。
这三类都只在"读文件 + 读工作流"时才现形，所以归到这里。

判据（10 项，任何一项失败即非零退出）：
- F1 英文面向的字段不许出现中日韩字符或全角标点（`NODES.*.en` / `TIPS.*.en`）
- F2 `web/*.js` 语法可解析（`node --check`，ESM 模式）
- F3 `WEB_DIRECTORY` 非 None、目录存在、`_ui.js` 里 import 的本地文件都存在
- F4 工作流里本包节点的 `title` 必须**能被 i18n 换前缀**
      （空 / 等于中英默认名 / 以默认名开头；写死自定义标题 ⇒ 中英切换对它无效）
- F5 `TIPS` 的键都能对上真实节点与真实参数名（防改名后残留孤儿键）
- F6 每个入参都有中文标签、标签表没有孤儿键、按节点覆盖的标签对得上真实参数
      （2026-10-30 实测：换脸节点 8 个入参不在 `LABELS` 里 ⇒ 中文界面显示英文参数名）
- F7 每个**静态枚举**的每个取值都有中文显示（宿主给的采样器/调度器列表除外）
- F8 编辑器文案表 `STR` 每条都有 zh 与 en，且没有死键
- F9 `NODES` 覆盖全部 7 个节点，中英名都不为空
- F10 每个入参都有**中文** tooltip（界面汉化最后一道：没有说明等于没汉化）

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
import tempfile

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
        # --experimental-default-type=module：.js 按 ESM 解析，import 语法才合法。
        # Node >= 22 已移除该 flag（报 bad option）⇒ 退到 .mjs 副本，仍按 ESM 解析。
        r = subprocess.run([node, "--check", "--experimental-default-type=module", p],
                           capture_output=True, text=True)
        err = (r.stderr or "") + (r.stdout or "")
        if r.returncode != 0 and "bad option" in err:
            tmp = tempfile.mkdtemp(prefix="mosssb_syntax_")
            try:
                dst = os.path.join(tmp, "check.mjs")
                shutil.copyfile(p, dst)
                r = subprocess.run([node, "--check", dst], capture_output=True, text=True)
                err = (r.stderr or "") + (r.stdout or "")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if r.returncode != 0:
            bad.append("%s: %s" % (f, err.strip().splitlines()[:1]))
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


def f6_label_coverage(param_names, src):
    """每个入参都要有中文标签，标签表也不许有孤儿键。

    为什么单独一条：`LABELS` 是**手写表**，加了参数忘了加标签，界面就静默显示英文参数名 ——
    不报错、不影响出图，只有中文用户看得见。2026-10-30 一次查出 11 个（换脸节点占 8 个）。
    """
    m = re.search(r"const LABELS = \{(.*?)\n\};", src, re.S)
    labels = set(re.findall(r"^\s+([a-z_0-9]+):", m.group(1), re.M)) if m else set()
    params = {p for _, p in param_names}
    #: 前端给 seed 自动加的控件，不是 INPUT_TYPES 里的参数。
    ALLOW = {"control_after_generate"}
    missing = sorted(params - labels)
    check("F6a 每个入参都有中文标签（%d 个入参）" % len(params), not missing, ", ".join(missing[:8]))
    orphan = sorted(labels - params - ALLOW)
    check("F6b 标签表没有孤儿键", not orphan, ", ".join(orphan[:8]))

    # 按节点覆盖表：键必须是真实的（节点, 参数）对，否则是改名后留下的死键。
    m2 = re.search(r"const LABELS_NODE = \{(.*?)\n\};", src, re.S)
    per = []
    if m2:
        cur = None
        for ln in m2.group(1).splitlines():
            t = re.match(r"\s{4}(\w+):\s*\{", ln)
            if t:
                cur = t.group(1)
                continue
            kv = re.match(r"\s{8}([a-z_0-9]+):", ln)
            if kv and cur:
                per.append((cur, kv.group(1)))
    bad = [(t, p) for t, p in per if (t, p) not in param_names]
    check("F6c 按节点覆盖的标签都对得上真实参数（%d 条）" % len(per), bool(per) and not bad, str(bad[:4]))


def _class_body(src, cls):
    m = re.search(r"^class %s\b.*?(?=^class |\Z)" % cls, src, re.M | re.S)
    return m.group(0) if m else ""


#: 宿主给的列表，值就是英文 ID（euler / dpmpp_2m …），翻译反而会让人对不上别的节点。
HOST_COMBO = {"sampler_name", "scheduler"}


def f7_combo_labels(mapping, src_js):
    """每个**枚举入参**的每个取值都要有中文显示。

    为什么单列一条：`COMBOS` 也是手写表。加一个档位忘了加中文，界面上就是一个英文下拉项 ——
    和参数标签一样的静默失败。

    取值来源用 `INPUT_TYPES()` 而不是正则：多数枚举是常量（`_GEN_MODES` / `_ASPECT_KEYS` / `PICK_MODES`），
    正则只看得到写成字面量列表的那两个（2026-10-30 实测：正则口径只数出 2 组，实际有 10 组）。
    值本身是中文的（`整张` / `逐格`）不用查表。
    """
    m = re.search(r"const COMBOS = \{(.*?)\n\};", src_js, re.S)
    blk = m.group(1) if m else ""
    groups, cur = {}, None
    for ln in blk.splitlines():
        g = re.match(r"\s{4}([a-z_0-9]+):\s*\{(.*)", ln)
        if g:
            cur = g.group(1)
            groups[cur] = set(re.findall(r'([\w:.\-]+):\s*"', g.group(2)))
            continue
        if cur:
            groups[cur] |= set(re.findall(r'([\w:.\-]+):\s*"', ln))

    bad, seen = [], 0
    for _cls, klass in mapping.items():
        it = klass.INPUT_TYPES()
        for sec in ("required", "optional"):
            for name, spec in (it.get(sec) or {}).items():
                opts = spec[0] if spec else None
                if not isinstance(opts, list) or name in HOST_COMBO:
                    continue
                # 比例值（16:9 / 2.39:1）本身就能读，不要求翻译；其余值必须有中文。
                vals = [str(v) for v in opts
                        if not CJK.search(str(v)) and not re.match(r"^\d+(\.\d+)?:\d+(\.\d+)?$", str(v))]
                if not vals:
                    continue
                seen += 1
                miss = [v for v in vals if v not in groups.get(name, set())]
                if miss:
                    bad.append("%s 缺 %s" % (name, miss[:3]))
    check("F7 枚举取值都有中文（%d 组）" % seen, seen >= 8 and not bad, "；".join(bad[:4]))


def f8_editor_strings():
    """编辑器文案表：每条都要 zh + en，且不能有死键。

    条目有两种写法（单行 `preset: { zh: "…", en: "…" },` 与多行），所以按
    「行首 4 空格 + 键名」切块累积，不能只靠单行正则。
    死键判据看**实际引用**：`t("key")` 是编辑器里唯一的取词入口（`const t = (k) => STR[k]?.[lang]`）。
    """
    p = os.path.join(ROOT, "web", "moss_sb_editor.js")
    src = open(p, encoding="utf-8").read()
    m = re.search(r"export const STR = \{(.*?)\n\};", src, re.S)
    blk = m.group(1) if m else ""
    entries, cur = {}, None
    for ln in blk.splitlines():
        mm = re.match(r"\s{4}([A-Za-z_0-9]+):\s*\{(.*)$", ln)
        if mm:
            cur = mm.group(1)
            entries[cur] = mm.group(2)
            continue
        if cur is not None:
            entries[cur] += ln
    no_zh = [k for k, b in entries.items() if "zh:" not in b]
    no_en = [k for k, b in entries.items() if "en:" not in b]
    dead = [k for k in entries
            if ('"%s"' % k) not in src and ("STR." + k) not in src]
    check("F8a 编辑器文案都有中文（%d 条）" % len(entries),
          len(entries) >= 100 and not no_zh, str(no_zh[:5]))
    check("F8b 编辑器文案都有英文", not no_en, str(no_en[:5]))
    check("F8c 编辑器文案没有死键", not dead, str(dead[:5]))


def f9_node_titles(src_js, classes):
    """每个节点都要有中英显示名 —— 少一个，中文界面下那个节点就是英文标题。"""
    m = re.search(r"export const NODES = \{(.*?)\n\};", src_js, re.S)
    blk = m.group(1) if m else ""
    missing, no_cjk = [], []
    for cls in classes:
        # ⚠ 这里用 [ \t]* 而不是空白类转义：参数名以 r 结尾时，冒号后紧跟反斜杠会被 S2 的
        #    盘符正则误判成本机绝对路径（2026-10-30 踩过）。
        mm = re.search(re.escape(cls) + r":[ \t]*\{(.*?)\}", blk, re.S)
        if not mm:
            missing.append(cls)
            continue
        body = mm.group(1)
        zh = re.search(r'zh:\s*"([^"]*)"', body)
        en = re.search(r'en:\s*"([^"]*)"', body)
        if not zh or not en or not zh.group(1).strip() or not en.group(1).strip():
            missing.append(cls)
        elif not CJK.search(zh.group(1)):
            no_cjk.append(cls)
    check("F9 七个节点都有中英显示名", not missing, str(missing))
    check("F9b 中文显示名确实是中文", not no_cjk, str(no_cjk))


def f10_tooltips(src, classes):
    """每个入参都要有**中文** tooltip：界面汉化不是只翻标签，说明也得是中文。"""
    miss, seen = [], 0
    for cls in classes:
        body = _class_body(src, cls)
        parts = re.split(r'^\s{12,16}"([a-z_0-9]+)": \(', body, flags=re.M)
        for i in range(1, len(parts), 2):
            name, blk = parts[i], parts[i + 1]
            seen += 1
            if '"tooltip"' not in blk:
                miss.append("%s.%s" % (cls, name))
                continue
            tip = blk.split('"tooltip"', 1)[1][:600]
            if not CJK.search(tip):
                miss.append("%s.%s(英文)" % (cls, name))
    check("F10 每个入参都有中文 tooltip（%d 个）" % seen, seen >= 40 and not miss, "；".join(miss[:5]))


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
    print("\n== F6 中文标签覆盖 ==")
    f6_label_coverage(param_names, src)

    classes = list(V1)
    print("\n== F7 枚举取值中文 ==")
    f7_combo_labels(V1, src)
    print("\n== F8 编辑器文案中英 ==")
    f8_editor_strings()
    print("\n== F9 节点显示名 ==")
    f9_node_titles(src, classes)
    print("\n== F10 中文 tooltip ==")
    f10_tooltips(open(os.path.join(ROOT, "nodes.py"), encoding="utf-8").read(), classes)

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
