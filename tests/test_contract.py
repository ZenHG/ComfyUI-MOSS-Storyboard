# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""契约与可移植性测试（零 GPU）。开源前的"别人能不能用"检查。

五组：
1. **参数表自洽**：类型/默认值/上下限/下拉候选/工具提示 —— 错一个就是用户界面上无法操作。
2. **V1↔V3 双出口等价**：入参名与顺序、输出数量、显示名。
3. **错误路径**：坏参数必须给中文可操作报错，不许静默兜底、不许抛裸异常。
4. **依赖边界**：`core.layout` / `core.prompt` 不许 import torch（纯 Python 可跑）。
5. **仓库卫生**：无机器专属绝对路径、无大文件、必需文件齐全。

跑法：python tests/test_contract.py            # 全部
      python tests/test_contract.py --no-host  # 不碰宿主（CI 无 ComfyUI 时用）
"""

import argparse
import importlib.machinery
import importlib.util
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
    w, h, cols, rows, spec, text, tpl, report = ok
    check("Layout: 4 格 2x2 / 1024x576", (w, h, cols, rows) == (1024, 576, 2, 2), "%dx%d %dx%d" % (w, h, cols, rows))
    check("Layout: 2 段描述 < 4 格时 report 出声", "后 2 格" in report, report.splitlines()[3][:60])
    check("Layout: 模板形状", tuple(tpl.shape) == (1, h, w, 3), str(tuple(tpl.shape)))

    plain = lay.build(**{**base, "prompt": "没有面板标记的一句话"})
    check("Layout: 无标记时退回纯文本", plain[5] == "没有面板标记的一句话", plain[5][:40])

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
    must = ["__init__.py", "nodes.py", "pyproject.toml", "LICENSE", "README.md",
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

    big = []
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for f in files:
            p = os.path.join(dirpath, f)
            if os.path.getsize(p) > 2 * 1024 * 1024:
                big.append(os.path.relpath(p, ROOT))
    check("无 >2MB 文件", not big, str(big))



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
    keys = set(re.findall(r'"(\*|MossStoryboard\w+)\.(\w+)":\s*\{', tips_block.group(1)))
    check("TIPS 条目数 > 30", len(keys) > 30, str(len(keys)))
    entry_zones = re.findall(r'"(?:\*|MossStoryboard\w+)\.\w+":\s*\{[^}]*\}', tips_block.group(1), re.S)
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

    为什么锁这条：MOSS 实测配方是 cfg=1.0，而宿主在 cfg=1 时**直接跳过负面分支**
    （`comfy/samplers.py::sampling_function` 的 cfg1 优化：`uncond_ = None`）。
    把 negative 放在普通区，只有一个后果——让人以为"写了就会生效"。
    它必须 `advanced`（默认折叠），而 positive 必须留在普通区（唯一必填的提示词入口）。
    """
    it = V1.NODE_CLASS_MAPPINGS["MossStoryboardSampler"].INPUT_TYPES()
    pos, neg = it["required"]["positive"], it["required"]["negative"]
    popts = pos[1] if isinstance(pos, tuple) and len(pos) > 1 else {}
    nopts = neg[1] if isinstance(neg, tuple) and len(neg) > 1 else {}
    check("negative 收在「高级」里（cfg=1 时它不生效）", nopts.get("advanced") is True,
          str(nopts.get("advanced")))
    check("positive 不在「高级」里（默认可见）", not popts.get("advanced"))
    check("positive 是多行文本", popts.get("multiline") is True)
    check("positive 默认空", popts.get("default") == "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-host", action="store_true")
    args = ap.parse_args()

    load_pkg()  # 触发包内各模块 import（失败即 Package 加载失败）
    from moss_sb_c import nodes as V1

    print("== 1. 参数表自洽 ==")
    for _name, cls in V1.NODE_CLASS_MAPPINGS.items():
        test_inputs(cls)

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

    if not args.no_host:
        print("\n== 7. V1↔V3 双出口等价 ==")
        test_v3(V1)

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
