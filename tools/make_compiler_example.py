# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 `examples/moss-prompt-compiler.json` —— 第 6 个节点（草稿编译）的示例。

为什么要有它：`MossPromptCompiler` 在 README / docs 里写了用法，却是 6 个节点里
**唯一没有示例工作流**的（2026-10-28 实测：其余 5 个都有）。零模型的节点反而最容易
被忽略——用户不知道该往里填什么、两个输出的 state 又该拿去干嘛。

为什么用脚本生成而不是手写 JSON：`widgets_values` 按位存，手写错一位参数就静默串位；
这里槽位与连线是同一次定义的，`tools/check_ui_workflow.py` 会拿 `INPUT_TYPES()` 反查。

跑法：`python tools/make_compiler_example.py`
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples", "moss-prompt-compiler.json")

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
        n["properties"].update({"cnr_id": "moss-storyboard", "ver": "0.3.0"})
    if title:
        n["title"] = title
    return n


def inp(name, typ, link=None):
    return {"name": name, "type": typ, "link": link}


def out(name, typ, links=()):
    return {"name": name, "type": typ, "links": list(links)}


def build():
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
    def wire(src, sslot, dst, dslot, typ):
        lid = len(links) + 1
        links.append([lid, src, sslot, dst, dslot, typ])
        nodes[src - 1]["outputs"][sslot]["links"] = [lid]
        nodes[dst - 1]["inputs"][dslot]["link"] = lid

    wire(2, 0, 3, 0, "STRING")   # prompt → PreviewAny#3
    wire(2, 1, 4, 0, "STRING")   # report → PreviewAny#4
    wire(2, 2, 5, 0, "STRING")   # state  → PreviewAny#5

    return {
        "id": "moss-prompt-compiler",
        "revision": 0,
        "last_node_id": 5,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.9, "offset": [780, 420]}},
        "version": 0.4,
    }


def main():
    wf = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
    print("已写出 %s（%d 节点 / %d 连线）" % (OUT, len(wf["nodes"]), len(wf["links"])))


if __name__ == "__main__":
    main()
