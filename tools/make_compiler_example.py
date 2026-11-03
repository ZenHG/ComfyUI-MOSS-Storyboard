# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成两个**零模型/零权重**示例工作流。

1. `examples/moss-prompt-compiler.json` —— 第 6 个节点（草稿编译）的示例。
   为什么要有它：`MossPromptCompiler` 在 README / docs 里写了用法，却是 6 个节点里
   唯一没有示例工作流的（2026-10-28 实测：其余 5 个都有）。零模型的节点反而最容易
   被忽略——用户不知道该往里填什么、两个输出的 state 又该拿去干嘛。

2. `examples/llm-compile-zero-model.json` —— **第一次打开就能跑**的入门工作流：
   `MossPromptSmith` 的 `task=compile`（把一段 IR 编译成某后端提示词），
   **不开模型、不加载权重、毫秒级**。为什么值得单列：其余三个 `llm-*` 示例都要 6G
   档模型；新用户第一次拖进画布时最需要的是「按 Run 就有输出」，而不是先配侧车。

为什么用脚本生成而不是手写 JSON：`widgets_values` 按位存，手写错一位参数就静默串位；
这里槽位与连线是同一次定义的，`tools/check_ui_workflow.py` 会拿 `INPUT_TYPES()` 反查。

跑法：`python tools/make_compiler_example.py`
产物：`examples/moss-prompt-compiler.json`、`examples/llm-compile-zero-model.json`
"""

import importlib.machinery
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_COMPILER = os.path.join(ROOT, "examples", "moss-prompt-compiler.json")
OUT_LLM_COMPILE = os.path.join(ROOT, "examples", "llm-compile-zero-model.json")

#: 只接线的类型（不是 widget）——与 `tools/check_ui_workflow.LINK_ONLY` 同一口径。
LINK_ONLY = {"MODEL", "CLIP", "VAE", "IMAGE", "LATENT", "CONDITIONING", "MASK", "SIGMAS"}

#: 生成示例时写进 `model` 下拉的值。compile 任务**不会加载它**，写一个稳定值只是
#: 为了让「同一份示例在任何机器上槽位一致」；CI 用同名空文件占位（见 ci.yml）。
ZERO_WEIGHT_MODEL = "GGUF/Qwen/Qwen3-VL-4B-Instruct-GGUF/Qwen3VL-4B-Instruct-Q4_K_M.gguf"

#: 零权重示例里贴的 IR：人像单主体，最小可编译形状（compile 不需要任何模型）。
ZERO_WEIGHT_IR = json.dumps({
    "task": "t2i",
    "canvas": {"aspect_ratio": "1:1"},
    "entities": [{
        "id": "e1", "kind": "character", "label": "woman",
        "importance": 1.0, "age_years": 28,
        "descriptors": {"gender": "woman", "wardrobe": ["a yellow raincoat"]},
        "spatial": {"anchor": "left foreground"},
    }],
    "style": {"medium": "photograph"},
}, ensure_ascii=False)

#: 一份合法的 **layout 草稿**（官方 prompt-enhancer 的产物形态）。
#: 1024×576、4 格、abs 坐标 xyxy —— 与示例画布一致，编译后会转成 abs_centralized/yxyx。
DRAFT = json.dumps({
    "width": 1024,
    "height": 576,
    "bbox_coord_space": "abs",
    "bbox_format": "xyxy",
    "summary": {"caption": "雨夜咖啡店靠窗双人桌，暖黄吊灯，玻璃雨痕。"},
    "items": [
        {"label": "object", "caption": "中景，苏晚与陈屿同框对坐", "bbox": [16, 16, 496, 272]},
        {"label": "object", "caption": "特写，苏晚克制的神情", "bbox": [512, 16, 1008, 272]},
        {"label": "object", "caption": "中近景，陈屿手指在杯上收紧", "bbox": [16, 288, 496, 560]},
        {"label": "object", "caption": "特写，苏晚视线落在桌面", "bbox": [512, 288, 1008, 560]},
    ],
}, ensure_ascii=False, indent=1)

NOTE = """## 草稿编译（`MossPromptCompiler`）

**零模型、不占显存**，秒出。它只做一件事：把**官方 MOSS-Image Prompt Enhancer 的草稿**
编译成能直接用的东西。

### 填什么
把草稿 JSON 粘进 `draft`。两种草稿：

| 草稿类型 | 长什么样 | 输出 |
|---|---|---|
| **caption** | `{"caption": "..."}` | `prompt` = 提示词文本；`state` 为空 |
| **layout** | `width/height/bbox_coord_space/bbox_format/summary/items` | `prompt` = 原生布局 JSON；`state` = 编辑器 state |

本工作流填的是 **layout 草稿**（4 格）。

### 两个输出拿去干嘛
- **`prompt`** → 接宿主核心节点 `MOSS-Image Layout Prompt`，或 `CLIPTextEncode`。
- **`state`** → 复制，粘进**布局编辑器**的「导入」框 —— 草稿里的框就变成可拖的画布。

### 注意
- 最多 256 个元素；未知字段、越界坐标、非 16 倍数的画布**直接报错**，不猜。
- `report` 会说清编译时做了什么（改坐标系 / 量化 / 丢弃了什么）。

> 本包不做图生图，这个节点也不加载任何模型。想直接出图请用另外三个示例。
"""

#: 零权重入门示例的说明卡。⚠ 不要用反引号（ComfyUI 的 MarkdownNote 渲染不支持嵌套）。
NOTE_ZERO = """## 第一次打开：零模型编译（MOSS Prompt Smith · task=compile）

**不用下模型、不占显存、按 Run 就有输出。** 这是最快的「装好了吗」自检：

### 怎么跑
1. 拖进画布 → 直接按 **Run**（队列里秒出，通常几十毫秒量级）。
2. 看右侧两个预览：
   - 「提示词」= 编译结果；
   - 「report」= 这次编译改了什么（方言 / 长度 / 丢弃项）。

### 它是什么
左上「📝 文字输入」里是一段 **IR（JSON）**；节点把 IR 编译成 **target** 选的图像后端提示词。
本示例 target=moss-image。改 target 可看 qwen-image-2_1 / krea2 / flux3 / sdxl 的方言差异。

### 下一步
- 想用 LLM 反推/扩写：换 llm-prompt-workbench.json / llm-idea-to-image.json，并在节点里选本机 GGUF。
- 想真出图：用 moss-storyboard-4panels-ui.json 等图像示例，需要 MOSS-Image 权重。

> 环境不满足时 report 会说清缺什么；本示例本身不需要任何权重。
"""


def node(nid, ntype, pos, size, widgets, inputs=(), outputs=(), title=None, order=0, ours=True):
    n = {
        "id": nid, "type": ntype, "pos": list(pos), "size": list(size),
        "flags": {}, "order": order, "mode": 0,
        "inputs": [dict(i) for i in inputs],
        "outputs": [dict(o) for o in outputs],
        "properties": {"Node name for S&R": ntype},
        "widgets_values": list(widgets),
    }
    if ours:
        n["properties"].update({"cnr_id": "moss-storyboard", "ver": "0.3.3"})
    if title:
        n["title"] = title
    return n


def inp(name, typ, link=None):
    return {"name": name, "type": typ, "link": link}


def out(name, typ, links=()):
    return {"name": name, "type": typ, "links": list(links)}


def _wire(nodes, links, src, sslot, dst, dslot, typ):
    lid = len(links) + 1
    links.append([lid, src, sslot, dst, dslot, typ])
    nodes[src - 1]["outputs"][sslot]["links"] = [lid]
    nodes[dst - 1]["inputs"][dslot]["link"] = lid


def _workflow(nodes, links, wf_id):
    return {
        "id": wf_id,
        "revision": 0,
        "last_node_id": len(nodes),
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.9, "offset": [780, 420]}},
        "version": 0.4,
    }


# ─────────────────────────────────────────────────────────────
# 示例 1：草稿编译（MossPromptCompiler）
# ─────────────────────────────────────────────────────────────

def build_compiler():
    nodes, links = [], []

    # 1) 说明卡
    nodes.append(node(1, "MarkdownNote", (-620, -240), (560, 640), [NOTE],
                      inputs=[], outputs=[], title="这是什么 · 怎么用", order=0, ours=False))

    # 2) 编译节点：draft 是唯一入参
    nodes.append(node(2, "MossPromptCompiler", (0, -240), (460, 620), [DRAFT],
                      inputs=[], outputs=[out("prompt", "STRING"), out("report", "STRING"),
                                          out("state", "STRING")],
                      title="草稿编译（零模型）", order=1))

    # 3) 看 prompt
    nodes.append(node(3, "PreviewAny", (520, -240), (420, 300),
                      [], inputs=[inp("source", "*", 1)],
                      outputs=[out("STRING", "STRING")], title="prompt", order=2, ours=False))

    # 4) 看 report
    nodes.append(node(4, "PreviewAny", (520, 100), (420, 260),
                      [], inputs=[inp("source", "*", 2)],
                      outputs=[out("STRING", "STRING")], title="report", order=3, ours=False))

    # 5) 看 state（复制去粘编辑器）
    nodes.append(node(5, "PreviewAny", (520, 400), (420, 300),
                      [], inputs=[inp("source", "*", 3)],
                      outputs=[out("STRING", "STRING")],
                      title="state → 粘进布局编辑器的导入框", order=4, ours=False))

    # ---- 连线：三处都要登记（links 表 / 源输出 / 目标输入），少一处宿主就丢线 ----
    # ⚠ links 表里第 4 列是**目标节点 id**，不是数组下标；第 6 列是**源输出**的类型
    #   （`PreviewAny` 的入口是通配 `*`，但线上记的仍是源那头的 STRING）。
    _wire(nodes, links, 2, 0, 3, 0, "STRING")   # prompt → PreviewAny#3
    _wire(nodes, links, 2, 1, 4, 0, "STRING")   # report → PreviewAny#4
    _wire(nodes, links, 2, 2, 5, 0, "STRING")   # state  → PreviewAny#5

    return _workflow(nodes, links, "moss-prompt-compiler")


# ─────────────────────────────────────────────────────────────
# 示例 2：零权重入门（MossPromptSmith · task=compile）
# ─────────────────────────────────────────────────────────────

def load_smith_class():
    """按 `tools/check_ui_workflow.py` 同一路径加载本包，取 `MossPromptSmith`。"""
    loader = importlib.machinery.SourceFileLoader("moss_sb_mk", os.path.join(ROOT, "__init__.py"))
    spec = importlib.util.spec_from_loader(
        "moss_sb_mk", loader, origin=os.path.join(ROOT, "__init__.py"), is_package=True)
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [ROOT]
    sys.modules["moss_sb_mk"] = mod
    loader.exec_module(mod)
    from moss_sb_mk import moss_llm as LLM
    return LLM.NODE_CLASS_MAPPINGS["MossPromptSmith"]


def expected_widgets(cls):
    """与 `tools/check_ui_workflow.expected_widgets()` 同一口径：widget 名顺序 + seed 控制项。"""
    it = cls.INPUT_TYPES()
    names = []
    for section in ("required", "optional"):
        for name, spec in (it.get(section) or {}).items():
            t, opts = (spec[0], spec[1] if len(spec) > 1 else {}) if isinstance(spec, tuple) \
                else (spec, {})
            if isinstance(opts, dict) and opts.get("forceInput"):
                continue
            if isinstance(t, str) and t.upper() in LINK_ONLY:
                continue
            names.append(name)
            if name == "seed":
                names.append("<control_after_generate>")
    return names


def widget_specs(cls):
    it = cls.INPUT_TYPES()
    out_ = {}
    for section in ("required", "optional"):
        for name, spec in (it.get(section) or {}).items():
            t, opts = (spec[0], spec[1] if len(spec) > 1 else {}) if isinstance(spec, tuple) \
                else (spec, {})
            if isinstance(opts, dict) and opts.get("forceInput"):
                continue
            if isinstance(t, str) and t.upper() in LINK_ONLY:
                continue
            out_[name] = (t, opts if isinstance(opts, dict) else {})
    return out_


def default_value(name, typ, opts):
    """widget 默认值：优先 INPUT_TYPES 的 default，其次该类型的常见零值。"""
    if name == "<control_after_generate>":
        return "fixed"
    if isinstance(typ, (list, tuple)):
        if opts.get("default") is not None:
            return opts["default"]
        return list(typ)[0] if typ else ""
    t = str(typ).upper()
    if opts.get("default") is not None:
        return opts["default"]
    return {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(t, "")


def build_llm_compile():
    cls = load_smith_class()
    names = expected_widgets(cls)
    specs = widget_specs(cls)

    #: 覆盖项：compile 只需要 task/text/target；model 写稳定值（不加载，只保证槽位可复现）。
    overrides = {
        "task": "compile",
        "channel": "local",
        "model": ZERO_WEIGHT_MODEL,
        "text": ZERO_WEIGHT_IR,
        "target": "moss-image",
        "runtime": "current",
    }
    for key in overrides:
        if key not in specs:
            raise SystemExit("MossPromptSmith 的 INPUT_TYPES 里没有 %r：示例生成器与本节点脱节" % key)
    widgets = []
    for name in names:
        if name == "<control_after_generate>":
            widgets.append("fixed")
            continue
        if name in overrides:
            widgets.append(overrides[name])
            continue
        typ, opts = specs[name]
        widgets.append(default_value(name, typ, opts))

    nodes, links = [], []
    nodes.append(node(1, "MarkdownNote", (-700, -260), (600, 700), [NOTE_ZERO],
                      inputs=[], outputs=[], title="这是什么 · 怎么跑", order=0, ours=False))
    nodes.append(node(2, "MossPromptSmith", (0, -260), (480, 700), widgets,
                      inputs=[inp("image", "IMAGE")],
                      outputs=[out("提示词", "STRING"), out("report", "STRING")],
                      title="🖊 Prompt Smith · zero-model compile", order=1))
    nodes.append(node(3, "PreviewAny", (560, -260), (440, 330),
                      [], inputs=[inp("source", "*", 1)],
                      outputs=[out("STRING", "STRING")], title="编译出的提示词", order=2, ours=False))
    nodes.append(node(4, "PreviewAny", (560, 90), (440, 350),
                      [], inputs=[inp("source", "*", 2)],
                      outputs=[out("STRING", "STRING")], title="report（改了什么）", order=3, ours=False))
    _wire(nodes, links, 2, 0, 3, 0, "STRING")
    _wire(nodes, links, 2, 1, 4, 0, "STRING")
    return _workflow(nodes, links, "llm-compile-zero-model")


def write_json(path, wf):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
    print("已写出 %s（%d 节点 / %d 连线）"
          % (os.path.relpath(path, ROOT), len(wf["nodes"]), len(wf["links"])))


def main(argv=None) -> int:
    """默认只生成**零权重入门示例**；`--with-compiler` 才重生成旧的草稿编译示例。

    为什么默认不重写 `examples/moss-prompt-compiler.json`：它是 0.2.x 时期生成的
    历史产物（标题 / ver / 说明卡与当前生成器不同）。重生成会改掉这些字节而产生无关
    diff；要更新它请显式 `--with-compiler` 并确认示例复核通过。
    """
    import argparse

    ap = argparse.ArgumentParser(description="生成零模型示例工作流")
    ap.add_argument("--with-compiler", action="store_true",
                    help="同时重生成 examples/moss-prompt-compiler.json（历史产物，通常不需要）")
    args = ap.parse_args(argv)
    write_json(OUT_LLM_COMPILE, build_llm_compile())
    if args.with_compiler:
        write_json(OUT_COMPILER, build_compiler())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
