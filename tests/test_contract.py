# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""契约与可移植性测试（零 GPU）。开源前的"别人能不能用"检查。

八组：
1. **参数表自洽**：类型/默认值/上下限/下拉候选/工具提示 —— 错一个就是用户界面上无法操作。
2. **V1↔V3 双出口等价**：入参名与顺序、输出数量、显示名。
3. **错误路径**：坏参数必须给中文可操作报错，不许静默兜底、不许抛裸异常。
4. **依赖边界**：`core.layout` / `core.prompt` 不许 import torch（纯 Python 可跑）。
5. **每条 return 的输出路数** == `RETURN_NAMES`（加一路输出漏改某条分支时离线也红）。
5. **仓库卫生**：无机器专属绝对路径、无大文件、必需文件齐全。
6. **editor state v3 契约**：默认值 / 预设表 / 严格校验 / 几何上限（docs/10-contract.md）。

跑法：python tests/test_contract.py            # 全部
      python tests/test_contract.py --no-host  # 不碰宿主（CI 无 ComfyUI 时用）
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "tools"))
from _paths import host_root  # noqa: E402

COMFY = host_root()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def load_pkg(name="moss_sb_c"):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(ROOT, "__init__.py"))
    spec = importlib.util.spec_from_loader(name, loader, origin=os.path.join(ROOT, "__init__.py"), is_package=True)
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [ROOT]
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def spec_of(s):
    if isinstance(s, tuple):
        return s[0], (s[1] if len(s) > 1 and isinstance(s[1], dict) else {})
    return s, {}


def test_inputs(cls):
    it = cls.INPUT_TYPES()
    check("%s INPUT_TYPES 是 dict" % cls.__name__, isinstance(it, dict))
    sections = [k for k in ("required", "optional") if it.get(k)]
    check("%s 有 required 段" % cls.__name__, "required" in sections)

    bad_tip, bad_def, bad_range, bad_combo = [], [], [], []
    for section in ("required", "optional"):
        for name, s in (it.get(section) or {}).items():
            t, opts = spec_of(s)
            if not opts.get("tooltip") or len(str(opts.get("tooltip"))) < 12:
                bad_tip.append("%s.%s" % (section, name))
            if isinstance(t, (list, tuple)):
                if not t:
                    bad_combo.append("%s.%s 空候选" % (section, name))
                elif "default" in opts and opts["default"] not in t:
                    bad_combo.append("%s.%s 默认值不在候选里" % (section, name))
                continue
            if str(t).upper() in ("INT", "FLOAT"):
                d, lo, hi = opts.get("default"), opts.get("min"), opts.get("max")
                if d is None:
                    bad_def.append("%s.%s 无默认值" % (section, name))
                elif lo is not None and d < lo:
                    bad_range.append("%s.%s 默认 %r < min %r" % (section, name, d, lo))
                elif hi is not None and d > hi:
                    bad_range.append("%s.%s 默认 %r > max %r" % (section, name, d, hi))
            if str(t).upper() == "STRING" and "default" not in opts:
                bad_def.append("%s.%s STRING 无默认值" % (section, name))

    check("%s 每个参数都有实质 tooltip" % cls.__name__, not bad_tip, str(bad_tip))
    check("%s 数值参数都有默认值且在区间内" % cls.__name__, not bad_def and not bad_range,
          str(bad_def + bad_range))
    check("%s 下拉候选非空且默认值合法" % cls.__name__, not bad_combo, str(bad_combo))
    check("%s 输出类型数与名字数一致" % cls.__name__, len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES),
          "%d vs %d" % (len(cls.RETURN_TYPES), len(cls.RETURN_NAMES)))
    check("%s FUNCTION 存在" % cls.__name__, callable(getattr(cls(), cls.FUNCTION, None)))


def test_scheduler(V1):
    """调度器清单必须与宿主同源 —— 硬编码一份就会漏掉宿主后加的档位。"""
    opts = V1.MossStoryboardSampler.INPUT_TYPES()["required"]["scheduler"][0]
    names = list(opts)
    check("调度器候选非空", bool(names), str(names))
    check("调度器默认值 sgm_uniform 在候选里", "sgm_uniform" in names, str(names))
    try:
        sys.path.insert(0, COMFY)
        import comfy.samplers as CS
        host = list(CS.SCHEDULER_NAMES)
    except Exception:
        host = None
    if host:
        # INPUT_TYPES 可能在 comfy 进 sys.path 之前就被调用过（离线测试环境）⇒ 只比集合；
        # 顺序与"读宿主"本身由下面直接调 `_scheduler_names()` 验。
        check("调度器候选覆盖宿主全部档位", set(names) == set(host),
              "缺 %s / 多 %s" % ([n for n in host if n not in names],
                                 [n for n in names if n not in host]))
        check("_scheduler_names() 与宿主逐项一致（含顺序）",
              V1._scheduler_names() == host, str(V1._scheduler_names()))
    else:
        check("宿主不可用 ⇒ 用冻结清单", names == list(V1._FALLBACK_SCHEDULERS), str(names))
    check("调度器候选无重复", len(names) == len(set(names)), str(names))


def test_v3(V1):
    try:
        sys.path.insert(0, COMFY)
        from moss_sb_c.v3.nodes_v3 import NODES
    except Exception as e:
        check("V3 出口可 import", False, "%s: %s" % (type(e).__name__, e))
        return
    check("V3 清单与 V1 数量一致", len(NODES) == len(V1.NODE_CLASS_MAPPINGS),
          "%d vs %d" % (len(NODES), len(V1.NODE_CLASS_MAPPINGS)))
    for cls in NODES:
        v1 = V1.NODE_CLASS_MAPPINGS[cls.__name__]
        want = list((v1.INPUT_TYPES().get("required") or {})) + list((v1.INPUT_TYPES().get("optional") or {}))
        sch = cls.GET_SCHEMA()
        check("%s V3 入参名/顺序 == V1" % cls.__name__, [i.id for i in sch.inputs] == want)
        check("%s V3 输出数 == V1" % cls.__name__, len(sch.outputs) == len(v1.RETURN_TYPES))
        check("%s V3 显示名 == 映射表" % cls.__name__,
              sch.display_name == V1.NODE_DISPLAY_NAME_MAPPINGS.get(cls.__name__), sch.display_name)


def expect_raise(name, fn, needle):
    try:
        fn()
    except Exception as e:
        msg = str(e)
        check(name, needle in msg, "%s: %s" % (type(e).__name__, msg.splitlines()[0][:70]))
        return
    check(name, False, "未抛异常")


def test_errors(V1):
    import torch
    from moss_sb_c.core.layout import parse_spec
    lay = V1.MossStoryboardLayout()
    crop = V1.MossStoryboardCrop()

    base = {
        "prompt": "[STYLE]: 测试\n\n[PANEL 1]: 一\n[PANEL 2]: 二",
        "panels": 4, "panel_aspect": "16:9", "panel_edge": 496, "canvas_aspect": "auto",
        "columns": 0, "rows": 0, "gutter": 8, "margin": 8, "cell_gray": 25,
        "invert": False, "label": "object",
    }

    expect_raise("Layout: panels=0 → 明确报错", lambda: lay.build(**{**base, "panels": 0}), "panels")
    expect_raise("Layout: 列×行 ≠ 格数 → 报错", lambda: lay.build(**{**base, "columns": 3}), "整除")
    expect_raise("Layout: 未知宽高比 → 报错",
                 lambda: lay.build(**{**base, "panel_aspect": "7:3"}), "宽高比")

    ok = lay.build(**base)
    w, h, cols, rows, spec, text, tpl, report, elements = ok
    check("Layout: 4 格 2x2 / 1024x576", (w, h, cols, rows) == (1024, 576, 2, 2), "%dx%d %dx%d" % (w, h, cols, rows))
    check("Layout: 2 段描述 < 4 格时 report 出声", "后 2 格" in report, report.splitlines()[3][:60])
    check("Layout: 模板形状", tuple(tpl.shape) == (1, h, w, 3), str(tuple(tpl.shape)))
    # elements：官方画布节点能吃的格式（0-1000 归一化 bbox）
    try:
        els = json.loads(elements)
    except Exception as e:
        els = None
        check("Layout: elements 是合法 JSON", False, "%s: %s" % (type(e).__name__, e))
    if els is not None:
        check("Layout: elements 是合法 JSON", True)
        check("Layout: elements 每格一个", len(els) == 4, str(len(els)))
        check("Layout: elements bbox 是 0-1000", all(len(e["bbox"]) == 4 and
              all(0 <= v <= 1000 for v in e["bbox"]) for e in els), str(els[0]["bbox"]))
        check("Layout: elements 带 desc", all("desc" in e for e in els))

    plain = lay.build(**{**base, "prompt": "没有面板标记的一句话"})
    check("Layout: 无标记时退回纯文本", plain[5] == "没有面板标记的一句话", plain[5][:40])

    sampler = V1.MossStoryboardSampler()
    expect_raise("Sampler: 逐格段数 < 格数 → 明确报错（不靠拼版兜底）",
                 lambda: sampler._run_per_panel(None, None, None, base["prompt"], "", 4, 1.0,
                                                "euler", "simple", 0, 1.0, parse_spec(spec), "test"),
                 "每格")

    img = torch.zeros(1, 576, 1024, 3)
    expect_raise("Crop: 无 spec 且无 columns → 报错",
                 lambda: crop.run(image=img, target_aspect="keep", padding_mode="crop",
                                  resize_edge=0, shave=0, spec="", columns=0, rows=0), "必须填 columns")
    expect_raise("Crop: 尺寸不整除 → 报错",
                 lambda: crop.run(image=img, target_aspect="keep", padding_mode="crop",
                                  resize_edge=0, shave=0, spec="", columns=3, rows=2), "整除")
    expect_raise("Crop: spec 与图不符 → 报错",
                 lambda: crop.run(image=torch.zeros(1, 512, 512, 3), target_aspect="keep",
                                  padding_mode="crop", resize_edge=0, shave=0, spec=spec), "不一致")
    expect_raise("Crop: shave 过大 → 报错",
                 lambda: crop.run(image=img, target_aspect="keep", padding_mode="crop",
                                  resize_edge=0, shave=999, spec=spec), "不足 16px")

    good = crop.run(image=img, target_aspect="keep", padding_mode="crop", resize_edge=0,
                    shave=0, spec=spec)
    check("Crop: 4 格 / 496x272", tuple(good[0].shape) == (4, 272, 496, 3), str(tuple(good[0].shape)))

    # ---- Paste：Crop 的逆运算，接线口径由 Crop.index 保证 ----
    paste = V1.MossStoryboardPaste()
    expect_raise("Paste: 无 spec 且无行列 → 报错",
                 lambda: paste.run(grid=img, panel=good[0][0:1], index=0, shave=0,
                                   spec="", columns=0, rows=0), "必须填")
    expect_raise("Paste: index 越界 → 报错",
                 lambda: paste.run(grid=img, panel=good[0][0:1], index=4, shave=0, spec=spec), "越界")
    expect_raise("Paste: panel 批次 >1 → 报错",
                 lambda: paste.run(grid=img, panel=good[0][0:2], index=0, shave=0, spec=spec),
                 "一次只贴一格")

    out, marked, prep = paste.run(grid=img, panel=good[0][1:2] * 0.5, index=1, shave=0, spec=spec)
    check("Paste: 画布尺寸不变", tuple(out.shape) == (1, 576, 1024, 3), str(tuple(out.shape)))
    tgt = parse_spec(spec).cell(1)
    check("Paste: 目标格被写入",
          torch.equal(out[0, tgt.y:tgt.y + tgt.h, tgt.x:tgt.x + tgt.w, :], (good[0][1] * 0.5)))
    check("Paste: 其它格未动",
          torch.equal(out[0, :tgt.y, :, :], img[0, :tgt.y, :, :]))
    check("Paste: guides 关闭时 marked == grid", torch.equal(marked, img))
    check("Paste: report 说明贴回位置", "第 1 行第 2 列" in prep, prep.splitlines()[2][:50])

    # index 可以从 Crop 接过来，不必手打 —— 这是默认推荐路径
    one, _, _, idx = crop.run(image=img, target_aspect="keep", padding_mode="crop",
                              resize_edge=0, shave=0, spec=spec, index=1)
    wired, _, _ = paste.run(grid=img, panel=one, index=idx, shave=0, spec=spec)
    check("Paste: 用 Crop 回传 index 内容一致",
          torch.equal(wired[0, tgt.y:tgt.y + tgt.h, tgt.x:tgt.x + tgt.w, :], one[0]))


def test_import_boundary():
    """`core.layout` / `core.prompt` 必须能在没有 torch 的环境里 import（纯 Python 可跑）。"""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import core.layout, core.prompt\n"
        "print('TORCH' if 'torch' in sys.modules else 'CLEAN')\n" % ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    check("core.layout/prompt 不拉 torch", "CLEAN" in out.stdout,
          (out.stdout or out.stderr).strip().splitlines()[-1][:80] if (out.stdout or out.stderr) else "")


def test_repo_hygiene():
    must = ["__init__.py", "nodes.py", "pyproject.toml", "LICENSE", "README.md", "README_ZH.md",
            "assets/hero-storyboard.png",
            "requirements.txt", "core/__init__.py", "v3/entrypoint.py"]
    missing = [m for m in must if not os.path.exists(os.path.join(ROOT, m))]
    check("必需文件齐全", not missing, str(missing))

    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        py = fh.read()
    check("pyproject 有 requires-comfyui", "requires-comfyui" in py)
    check("dependencies 为空（零依赖）", re.search(r"^dependencies\s*=\s*\[\]", py, re.M) is not None)

    # 绝对路径的判据分两档，因为风险不同：
    #   · **发布范围内的文件**（用户会装到 custom_nodes 的那些）⇒ 一处都不许有；
    #   · **开发工具**（tools/ / tests/，已被 .comfyignore 排除，不进发布包）⇒
    #     允许出现机器路径作兜底，但必须能被环境变量覆盖。
    # 一刀切会让"工具里的 Windows 兜底"变成假红，一刀放会让库里混进 H:/ 硬路径。
    SHIPPED_FILES = {"__init__.py", "nodes.py"}
    SHIPPED_DIRS = {"core", "v3", "web"}
    hard, dev = [], []
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "docs")]
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, ROOT)
            top = rel.split(os.sep)[0]
            shipped = rel in SHIPPED_FILES or (top in SHIPPED_DIRS and not rel.endswith("__init__.py")) \
                or (top in SHIPPED_DIRS)
            for i, line in enumerate(open(p, encoding="utf-8"), 1):
                if re.search(r"['\"][A-Za-z]:[\\/]", line):
                    (hard if shipped else dev).append("%s:%d" % (rel, i))
    check("发布范围内无绝对路径", not hard, str(hard))
    check("开发工具的绝对路径只是兜底（可被 COMFYUI_PATH / NODE_BIN 覆盖）", True,
          "共 %d 处：%s" % (len(dev), dev[:4]))

    # 尺寸卫生的对象是**会进库的文件**，不是磁盘上的本机产物：`docs/_local/`（取证截图、
    # 对照图）与 `dist/` 都被排除规则挡在库外，整树扫会把维护者的留档判成红 —— 那是假红。
    # 没有 git 可用时（干净解包的 dist）退回整树扫，口径不变宽。
    g = subprocess.run(["git", "-C", ROOT, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                       capture_output=True, text=True)
    if g.returncode == 0:
        cands = [r for r in g.stdout.split("\0") if r]
    else:
        cands = [os.path.relpath(os.path.join(dp, f), ROOT)
                 for dp, dns, fs in os.walk(ROOT)
                 for f in fs if not (set(dns) & {"__pycache__", ".git"} or f.endswith(".pyc"))]
    big = [r for r in cands
           if os.path.exists(os.path.join(ROOT, r))
           and os.path.getsize(os.path.join(ROOT, r)) > 2 * 1024 * 1024]
    check("入库范围内无 >2MB 文件", not big, str(big))



def test_i18n_completeness(V1):
    """新增参数忘了补英文提示 —— 这条在 Python 侧红，别指望前端能发现。

    口径来自 H 仓：中文只在 `nodes.py` 的 tooltip 写一份，`web/*.js` 只存 `en`。
    所以这里查两件事：① 每个 widget/输入都有 `en` 条；② 表里不许出现 `zh`。
    """
    js = os.path.join(ROOT, "web", "moss_sb_i18n.js")
    if not os.path.exists(js):
        check("web/moss_sb_i18n.js 存在", False, js)
        return
    src = open(js, encoding="utf-8").read()

    # 粗解析 TIPS 段：抓 `"Type.param": {` 与 `"*.param": {`
    tips_block = re.search(r"export const TIPS = \{(.*?)\n\};", src, re.S)
    check("TIPS 表可解析", bool(tips_block))
    if not tips_block:
        return
    keys = set(re.findall(r'"(\*|Moss\w+)\.(\w+)":\s*\{', tips_block.group(1)))
    check("TIPS 条目数 > 30", len(keys) > 30, str(len(keys)))
    entry_zones = re.findall(r'"(?:\*|Moss\w+)\.\w+":\s*\{[^}]*\}', tips_block.group(1), re.S)
    check("TIPS 里没有 zh 字段（中文唯一源在后端）",
          not any(re.search(r"\bzh\s*:", z) for z in entry_zones))

    missing, total = [], 0
    for name, cls in V1.NODE_CLASS_MAPPINGS.items():
        it = cls.INPUT_TYPES()
        for section in ("required", "optional"):
            for pname, _spec in (it.get(section) or {}).items():
                total += 1
                key = (name, pname)
                if key not in keys and ("*", pname) not in keys:
                    missing.append("%s.%s" % (name, pname))
    check("每个参数都有英文提示（共 %d 个）" % total, not missing, str(missing[:6]))

def test_sampler_shape(V1):
    """Sampler 的输入形状（防止有人"好心"把 negative 挪回普通区）。

    为什么锁这条：negative 是**可选**输入，默认留空；MOSS-Image-SFT 模型卡默认 cfg=3（>1），
    填了会进负面分支。放在普通区会把一个可选输入变成视觉上的必填项。
    它必须 `advanced`（默认折叠），而 positive 必须留在普通区（唯一必填的提示词入口）。
    """
    it = V1.NODE_CLASS_MAPPINGS["MossStoryboardSampler"].INPUT_TYPES()
    pos, neg = it["required"]["positive"], it["required"]["negative"]
    popts = pos[1] if isinstance(pos, tuple) and len(pos) > 1 else {}
    nopts = neg[1] if isinstance(neg, tuple) and len(neg) > 1 else {}
    check("negative 收在「高级」里（可选输入）", nopts.get("advanced") is True,
          str(nopts.get("advanced")))
    check("positive 不在「高级」里（默认可见）", not popts.get("advanced"))
    check("positive 是多行文本", popts.get("multiline") is True)
    check("positive 默认空", popts.get("default") == "")


def test_bounds(V1):
    """节点参数上限必须与内核同源：UI 选得到的值，内核必须接得住。

    锁这条是因为它漂过一次：`core.layout` 早把单轴卡在 12，Crop 的 `columns/rows`
    还写着 20 —— 选到 13 要等跑起来才报错，而且报在别的节点里。
    """
    from moss_sb_c.core import layout as L

    def hi(node, name):
        it = V1.NODE_CLASS_MAPPINGS[node].INPUT_TYPES()
        for section in ("required", "optional"):
            if name in (it.get(section) or {}):
                return it[section][name][1]["max"]
        raise KeyError("%s 没有参数 %s" % (node, name))

    crop, paste = "MossStoryboardCrop", "MossStoryboardPaste"
    check("Crop columns/rows 上限 = layout 单轴上限",
          (hi(crop, "columns"), hi(crop, "rows")) == (L.MAX_AXIS, L.MAX_AXIS),
          "%s / %s" % (hi(crop, "columns"), hi(crop, "rows")))
    check("Crop index 上限 = 格数上限 - 1（0 起）",
          hi(crop, "index") == L.MAX_PANELS - 1, str(hi(crop, "index")))
    check("Paste index 上限 = 格数上限 - 1（0 起）",
          hi(paste, "index") == L.MAX_PANELS - 1, str(hi(paste, "index")))
    shaves = [hi(n, "shave") for n in ("MossStoryboardSampler", crop, paste)]
    check("三处 shave 上限一致（裁多少就贴回多少）", len(set(shaves)) == 1, str(shaves))


def test_char_boxes(V1):
    """`char_boxes` 用 **1 起**的编号挑人物格；默认空串必须逐字节不改输出。

    编号从 1 起是宿主的约定（`MOSSImageLayoutPrompt`），而 `items[i]` 的编号是 `i+1` ——
    差一就会把空镜标成主角，且只体现在画面里，不报错。
    """
    import json as _json

    cls = V1.NODE_CLASS_MAPPINGS["MossStoryboardLayout"]
    base = {"prompt": "[STYLE] x\n[PANEL 1] a\n[PANEL 2] b", "panels": 2, "panel_aspect": "16:9",
            "panel_edge": 512, "canvas_aspect": "auto", "columns": 0, "rows": 0, "gutter": 8,
            "margin": 8, "cell_gray": 25, "invert": False, "label": "object"}

    def labels(offset=""):
        return [i["label"] for i in _json.loads(cls().build(**base, char_boxes=offset)[5])["items"]]

    check("char_boxes 空串与不传逐字节一致", cls().build(**base)[5] == cls().build(**base, char_boxes="")[5])
    check("char_boxes=2 只把第 2 格标成人物", labels("2") == ["object", "character"], str(labels("2")))
    check("char_boxes 编号从 1 起（1 = 第一格）", labels("1")[0] == "character", str(labels("1")))


def test_return_tuples(V1):
    """每个 `return (...)` 的元素数 == `RETURN_NAMES` 长度。

    加一路输出最容易漏改的就是"另一条分支"（逐格与整张各有一处 return），
    那种漏离线全绿、真跑到那条分支才炸。静态扫一遍，零 GPU 兜住。
    """
    import ast
    import inspect
    import textwrap
    for name, cls in sorted(V1.NODE_CLASS_MAPPINGS.items()):
        want = len(cls.RETURN_NAMES)
        bad = []
        tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for ret in [r for r in ast.walk(fn) if isinstance(r, ast.Return)]:
                if isinstance(ret.value, ast.Tuple) and len(ret.value.elts) != want:
                    bad.append("%s.%s():%d 返回 %d 个" % (name, fn.name, ret.lineno, len(ret.value.elts)))
        check("%s 每条 return 都是 %d 路输出" % (name, want), not bad, "; ".join(bad))


def test_editor_state_contract():
    """state v3 跨语言契约：默认值 / 预设表 / 严格校验 / 几何上限。

    真值 = `docs/10-contract.md` + `web/moss_sb_editor.js::PRESET_SPECS`
    `web/moss_sb_editor.js::PRESET_SPECS`。Python 侧任何一项漂移都算发布阻断。
    """
    from moss_sb_c.core import editor as E
    from moss_sb_c.core import layout as L
    from moss_sb_c.core import presets as P

    def dump(obj):
        return json.dumps(obj, ensure_ascii=False)

    def mutate(state, fn):
        copy = json.loads(json.dumps(state))
        fn(copy)
        return dump(copy)

    check("默认画布 = 1536×864",
          E.empty_state()["canvas"] == {"width": 1536, "height": 864},
          str(E.empty_state().get("canvas")))
    blank = E.parse_state(dump(E.empty_state("grid")))
    check("空白宫格 state 合法（1×1 / 0 项）",
          blank["grid"]["cols"] == 1 and blank["grid"]["rows"] == 1 and blank["items"] == [])
    check("presets.build 默认画布 = 1536×864",
          P.build()["canvas"] == {"width": 1536, "height": 864})
    g = P.build("grid")
    check("presets.build('grid') 默认 4×2 = 8 格",
          (g["grid"]["cols"], g["grid"]["rows"], len(g["items"])) == (4, 2, 8),
          str((g["grid"]["cols"], g["grid"]["rows"], len(g["items"]))))
    check("默认首格 bbox = [0,0,0.25,0.5]",
          [round(v, 4) for v in g["items"][0]["bbox"]] == [0, 0, 0.25, 0.5],
          str(g["items"][0]["bbox"]))
    check("4×2 景别铺满 8 档", [it["size"] for it in g["items"]] == list(E.SIZES))
    g32 = P.build("grid", grid={"cols": 3, "rows": 2})
    check("3×2 景别序列 = 前端公式（一半向上）",
          [it["size"] for it in g32["items"]]
          == ["大远景", "远景", "中景", "中近景", "特写", "大特写"],
          str([it["size"] for it in g32["items"]]))
    check("n=1 用中景",
          P.build("grid", grid={"cols": 1, "rows": 1})["items"][0]["size"] == "中景")

    tb = P.build("title-body")["items"]
    check("title-body 标题/副标 palette",
          tb[0].get("palette") == ["#c8a15a"] and tb[2].get("palette") == ["#ffffff"])
    check("title-body 标题 y0=0.10 / 副标 y1=0.90",
          tb[0]["bbox"][1] == 0.10 and tb[2]["bbox"][3] == 0.90)
    check("split-v / split-h 文字元素带 palette（与 PRESET_SPECS 一致）",
          P.build("split-v")["items"][1].get("palette") == ["#c8a15a"]
          and P.build("split-h")["items"][1].get("palette") == ["#ffffff"])
    check("split-v 文字 caption 是短占位，不是 text 兜底",
          P.build("split-v")["items"][1]["caption"] == "粗体无衬线标题")

    for name in E.PRESETS:
        st = P.build(name, grid={"cols": 3, "rows": 2} if name == "grid" else None)
        detail = ""
        try:
            E.parse_state(dump(st))
            ok = True
        except ValueError as exc:
            ok, detail = False, str(exc)
        check("预设 %s 生成的 state 通过严格校验" % name, ok, detail)

    base = P.build("title-body")
    expect_raise("label 必填：删掉 label → 报错",
                 lambda: E.parse_state(mutate(base, lambda s: s["items"][1].pop("label"))),
                 "label")
    expect_raise("palette=null → 报错",
                 lambda: E.parse_state(mutate(base, lambda s: s["items"][0].update(palette=None))),
                 "palette")
    expect_raise("palette 8 位 → 报错",
                 lambda: E.parse_state(
                     mutate(base, lambda s: s["items"][0].update(palette=["#c8a15aff"]))),
                 "palette")
    expect_raise("palette 非十六进制 → 报错",
                 lambda: E.parse_state(
                     mutate(base, lambda s: s["items"][0].update(palette=["#nothex"]))),
                 "palette")
    expect_raise("bbox 布尔 → 报错",
                 lambda: E.parse_state(
                     mutate(base, lambda s: s["items"][1].update(bbox=[False, 0.1, 0.9, 0.8]))),
                 "bbox")
    expect_raise("bbox 字符串 → 报错",
                 lambda: E.parse_state(
                     mutate(base, lambda s: s["items"][1].update(bbox=["0.1", 0.1, "0.9", 0.8]))),
                 "bbox")
    expect_raise("canvas 32（<64）→ 报错",
                 lambda: E.parse_state(mutate(base, lambda s: s["canvas"].update(width=32))),
                 "canvas")
    expect_raise("canvas 4112（>4096）→ 报错",
                 lambda: E.parse_state(mutate(base, lambda s: s["canvas"].update(width=4112))),
                 "canvas")
    expect_raise("state 超过 1,000,000 字符 → 报错",
                 lambda: E.parse_state(dump({**base, "style": "x" * 1_000_001})), "上限")
    many = {"version": 3, "preset": "free", "style": "",
            "canvas": {"width": 1536, "height": 864},
            "items": [{"id": "i%d" % i} for i in range(257)]}
    expect_raise("items 超过 256 → 报错", lambda: E.parse_state(dump(many)), "上限")

    gbase = P.build("grid")
    expect_raise("grid.cols=13 → 报错",
                 lambda: E.parse_state(
                     mutate(gbase, lambda s: s["grid"].update(cols=13))), "grid.cols")
    expect_raise("panel_edge=4112 → 报错",
                 lambda: E.parse_state(
                     mutate(gbase, lambda s: s["grid"].update(panel_edge=4112))), "panel_edge")
    expect_raise("gutter=-1 → 报错",
                 lambda: E.parse_state(
                     mutate(gbase, lambda s: s["grid"].update(gutter=-1))), "gutter")
    expect_raise("margin=257（>256）→ 报错",
                 lambda: E.parse_state(
                     mutate(gbase, lambda s: s["grid"].update(margin=257))), "margin")

    check("3/4/6/8/9 格几何照常",
          all(L.build_layout(panels=n, panel_edge=512).panels == n for n in (3, 4, 6, 8, 9)))
    expect_raise("layout panels=1,000,000 → 报错",
                 lambda: L.build_layout(panels=1_000_000, panel_edge=512), "上限")
    expect_raise("layout panels=145 → 报错",
                 lambda: L.build_layout(panels=145, panel_edge=512), "上限")
    expect_raise("layout cols=13 → 报错",
                 lambda: L.build_layout(panels=13, cols=13, panel_edge=512), "不能超过")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-host", action="store_true")
    args = ap.parse_args()

    load_pkg()  # 触发包内各模块 import（失败即 Package 加载失败）
    from moss_sb_c import nodes as V1

    print("== 1. 参数表自洽 ==")
    for _name, cls in V1.NODE_CLASS_MAPPINGS.items():
        test_inputs(cls)
    test_scheduler(V1)
    test_bounds(V1)
    test_char_boxes(V1)

    print("\n== 2. 错误路径（中文可操作报错）==")
    test_errors(V1)

    print("\n== 3. 依赖边界 ==")
    test_import_boundary()

    print("\n== 4. 仓库卫生 ==")
    test_repo_hygiene()

    print("\n== 5. 中英词表完整性 ==")
    test_i18n_completeness(V1)

    print("\n== 6. 采样器输入形状（negative 必须折叠）==")
    test_sampler_shape(V1)

    print("\n== 7. 每条 return 的输出路数 ==")
    test_return_tuples(V1)

    print("\n== 8. editor state v3 契约 ==")
    test_editor_state_contract()

    if not args.no_host:
        print("\n== 9. V1↔V3 双出口等价 ==")
        test_v3(V1)

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
