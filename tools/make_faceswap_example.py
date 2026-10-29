# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 examples/moss-faceswap-ui.json —— 第 7 个节点（换脸）的示例。

为什么用脚本生成：widgets_values 按位存，手写错一位参数就静默串位；这里槽位与连线
是同一次定义的，tools/check_ui_workflow.py 会拿 INPUT_TYPES() 反查。

示例走的是**故事板那条路**：源图是整张宫格，spec 来自宫格几何节点，index=0
只换第一格 —— 这正是本包与「拿一张图去换脸」的差别：几何由 spec 决定，
贴回只动那一格，宫格线与其他格一个字节都不碰。

跑法：python tools/make_faceswap_example.py
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples", "moss-faceswap-ui.json")

#: 提示词模板。**必须提到 Picture 1** —— 参考图是靠这个词进场景的。
#: 前缀 Picture 1: <|vision_start|>... 由节点自动拼，这里不要自己写。
PROMPT = ("A medium shot photograph of the person in Picture 1, sitting at a wooden table "
          "in a warm cafe at night, rain on the window, soft warm light on the face, "
          "35mm film, shallow depth of field")

NOTE = """## 换脸（MossStoryboardFaceSwap）

把**参考图的脸**换到**某一格**。两条通道各管一半：

| 通道 | 管什么 |
|---|---|
| 参考图 → vision tower | **是谁**（身份）。默认 vision_megapixels=1.05，宿主默认 0.147 那个值身份余弦只有 0.008 |
| 源图 → VAEEncode（图生图） | **画什么**（构图/场景），由 denoise 与提示词决定 |

### 这个工作流在做什么
源图是**整张宫格**（1024×576，4 格），spec 由「宫格几何」给，index=0 ⇒
**只换第一格**，其余三格与宫格线不动。

### init 还能做另一件事
把 init 从 source 改成 empty，节点就不吃 image，从空 latent 生成 ——
「参考图 + 提示词 → 一张带指定动作的图」。示例见 moss-pose-generate-ui.json。
此时 denoise / sample_edge / auto_mask / face_pick / index 都不生效，report 会写明。

### 三处必须改
1. **两个 LoadImage** 换成你自己的图：
   - src = 要换脸的那张（这里是宫格）
   - ref = 身份参考图，**紧特写**最好，只给一张（双视角实测更差：0.384 vs 0.463）
2. **提示词**里保留 in Picture 1 这个说法 —— 换成别的措辞参考图就不进场景了。
3. **index** 改成你要换的格（0 起、行主序）。

### ⚠ 先用单人源图
示例源图是**双人**场景，而「一格两个人」是本节点**已知不成立**的情况：两张脸都会被换掉。
拿它跑通流程没问题，但要看效果请换一张**单人**图（脸占画幅越大越好）。

### denoise 是最容易看错的一个旋钮
- 0.95（默认，实测配方）在 **8 步下等价于 1.0**：源图被**完全丢弃**，构图由提示词决定。
- 想保住源图构图，要 **≤0.875**（8 步时 int(8/0.875)=9>8 才真的触发 img2img）。
- 0.875~0.99 这一段在 8 步下**全部等于 1.0** —— 宿主按 int(步数/降噪) 截断 sigma。
  report 会打印**实际生效值**，别只看名义值。

### 单格太小怎么办
4 格 496 长边时一格只有 0.135MP，脸约 50×27 像素 —— 那个尺度模型认不出是谁。
所以节点默认把单格**抬到 1024 长边再采样**（sample_edge），采样完缩回格子。
这一机制与「逐格」模式同一个，不是新旋钮。

### 接不接 spec 的差别
- **接了**：给整张宫格 + index，只换那一格，输出仍是整张宫格。
- **不接**：整张 image 当成一张图，换完直接输出（单图换脸）。
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
        n["properties"].update({"cnr_id": "moss-storyboard", "ver": "0.3.2"})
    if title:
        n["title"] = title
    return n


def inp(name, typ, link=None, widget=None):
    d = {"name": name, "type": typ, "link": link}
    if widget:
        d["widget"] = {"name": widget}
    return d


def out(name, typ, links=()):
    return {"name": name, "type": typ, "links": list(links)}


def build():
    nodes, links = [], []

    nodes.append(node(1, "MarkdownNote", (-1180, -260), (560, 760), [NOTE],
                      title="这是什么 · 怎么用", ours=False))

    # ---- 模型三件套 + 两层 LoRA（8 步蒸馏 → 编辑）----
    nodes.append(node(2, "UNETLoader", (-560, -260), (340, 82),
                      ["moss_image_sft_bf16.safetensors", "default"],
                      outputs=[out("MODEL", "MODEL")], order=0, ours=False))
    nodes.append(node(3, "LoraLoaderModelOnly", (-560, -140), (340, 82),
                      ["8-steps-dmd2-lora.safetensors", 1.0],
                      inputs=[inp("model", "MODEL", 1, "model")],
                      outputs=[out("MODEL", "MODEL")], order=1, ours=False))
    nodes.append(node(4, "LoraLoaderModelOnly", (-560, -20), (340, 82),
                      ["Moss-Image-Edit\\adapter_model.safetensors", 1.0],
                      inputs=[inp("model", "MODEL", 2, "model")],
                      outputs=[out("MODEL", "MODEL")], order=2, ours=False))
    nodes.append(node(5, "CLIPLoader", (-560, 100), (340, 106),
                      ["qwen3.5_9b_int8_convrot.safetensors", "moss_image", "default"],
                      outputs=[out("CLIP", "CLIP")], order=3, ours=False))
    nodes.append(node(6, "VAELoader", (-560, 250), (340, 58),
                      ["flux2-vae.safetensors"],
                      outputs=[out("VAE", "VAE")], order=4, ours=False))

    # ---- 两张输入图 ----
    nodes.append(node(7, "LoadImage", (-560, 350), (340, 320), ["moss_faceswap_src.png", "image"],
                      inputs=[inp("image", "COMBO", None, "image"), inp("upload", "IMAGEUPLOAD", None, "upload")],
                      outputs=[out("IMAGE", "IMAGE"), out("MASK", "MASK")],
                      title="src · 要换脸的图（这里是整张宫格）", order=5, ours=False))
    nodes.append(node(8, "LoadImage", (-560, 710), (340, 320), ["moss_faceswap_ref.png", "image"],
                      inputs=[inp("image", "COMBO", None, "image"), inp("upload", "IMAGEUPLOAD", None, "upload")],
                      outputs=[out("IMAGE", "IMAGE"), out("MASK", "MASK")],
                      title="ref · 身份参考图（紧特写，只给一张）", order=6, ours=False))

    # ---- 几何：给 spec，换脸节点才知道「第几格」在哪 ----
    nodes.append(node(9, "MossStoryboardLayout", (-160, 350), (340, 300),
                      ["", 4, "16:9", 496, "auto", 0, 0, 8, 8, 25, False, "object", ""],
                      inputs=[inp("prompt", "STRING", None, "prompt"),
                              inp("panels", "INT", None, "panels"),
                              inp("panel_aspect", "COMBO", None, "panel_aspect"),
                              inp("panel_edge", "INT", None, "panel_edge"),
                              inp("canvas_aspect", "COMBO", None, "canvas_aspect"),
                              inp("columns", "INT", None, "columns"),
                              inp("rows", "INT", None, "rows"),
                              inp("gutter", "INT", None, "gutter"),
                              inp("margin", "INT", None, "margin"),
                              inp("cell_gray", "INT", None, "cell_gray"),
                              inp("invert", "BOOLEAN", None, "invert"),
                              inp("label", "COMBO", None, "label")],
                      outputs=[out("width", "INT"), out("height", "INT"),
                               out("columns", "INT"), out("rows", "INT"),
                               out("spec", "STRING"), out("layout_json", "STRING"),
                               out("template", "IMAGE"), out("report", "STRING"),
                               out("elements", "STRING")],
                      title="🎬 故事板 · 宫格几何（只为拿 spec）", order=7))

    # ---- 换脸本体 ----
    # widgets 顺序 = INPUT_TYPES 声明顺序里**有 widget 的那些**（spec 是 forceInput，不在其中）
    nodes.append(node(10, "MossStoryboardFaceSwap", (260, 350), (420, 620),
                      [PROMPT, 8, 1.0, "euler", "simple", 0, "randomize", 0.95, 1.05, 0, 1024, "",
                       True, "right", "source", 1024, 576],
                      inputs=[inp("model", "MODEL", 3),
                              inp("clip", "CLIP", 4),
                              inp("vae", "VAE", 5),
                              inp("ref", "IMAGE", 7),
                              inp("prompt", "STRING", None, "prompt"),
                              inp("steps", "INT", None, "steps"),
                              inp("cfg", "FLOAT", None, "cfg"),
                              inp("sampler_name", "COMBO", None, "sampler_name"),
                              inp("scheduler", "COMBO", None, "scheduler"),
                              inp("seed", "INT", None, "seed"),
                              inp("denoise", "FLOAT", None, "denoise"),
                              inp("vision_megapixels", "FLOAT", None, "vision_megapixels"),
                              inp("image", "IMAGE", 6),
                              inp("spec", "STRING", 8),
                              inp("index", "INT", None, "index"),
                              inp("sample_edge", "INT", None, "sample_edge"),
                              inp("negative", "STRING", None, "negative"),
                              inp("auto_mask", "BOOLEAN", None, "auto_mask"),
                              inp("face_pick", "COMBO", None, "face_pick"),
                              inp("mask", "MASK"),
                              inp("init", "COMBO", None, "init"),
                              inp("width", "INT", None, "width"),
                              inp("height", "INT", None, "height")],
                      outputs=[out("image", "IMAGE"), out("report", "STRING")],
                      title="🎬 故事板 · 换脸（只换第 0 格）", order=8))

    nodes.append(node(11, "SaveImage", (760, 350), (400, 560), ["moss/faceswap/board"],
                      inputs=[inp("images", "IMAGE", 9)],
                      outputs=[out("images", "IMAGE")],
                      title="换完的整张宫格", order=9, ours=False))
    nodes.append(node(12, "PreviewAny", (760, 940), (400, 220), [],
                      inputs=[inp("source", "*", 10)],
                      outputs=[out("STRING", "STRING")],
                      title="report（看实际生效的降噪）", order=10, ours=False))

    # ---- 连线：三处都要登记（links 表 / 源输出 / 目标输入）----
    # ⚠ 第 4 列是**目标节点 id**，不是数组下标；第 6 列是**源输出类型**。
    #: 按 **id** 找节点，不能按下标：节点数组的顺序与 id 不一定一致（加节点时最容易踩）。
    by_id = {n["id"]: n for n in nodes}

    def wire(src, sslot, dst, dslot, typ):
        """dslot 可以给**名字**。给数字在改 inputs 顺序时会静默错位 ——
        踩过：image 从 required 挪到 optional 后，源图那根线落到了 ref 上，
        参考图落到了 prompt 上，宿主才报 prompt received IMAGE。
        """
        if isinstance(dslot, str):
            dslot = [i["name"] for i in by_id[dst]["inputs"]].index(dslot)
        lid = len(links) + 1
        links.append([lid, src, sslot, dst, dslot, typ])
        cur = by_id[src]["outputs"][sslot]["links"]
        by_id[src]["outputs"][sslot]["links"] = list(cur) + [lid]
        by_id[dst]["inputs"][dslot]["link"] = lid

    wire(2, 0, 3, 0, "MODEL")    # UNET → dmd2 LoRA
    wire(3, 0, 4, 0, "MODEL")    # dmd2 → edit LoRA
    wire(4, 0, 10, "model", "MODEL")   # edit LoRA → 换脸
    wire(5, 0, 10, "clip", "CLIP")
    wire(6, 0, 10, "vae", "VAE")
    wire(7, 0, 10, "image", "IMAGE")   # 源图
    wire(8, 0, 10, "ref", "IMAGE")   # 参考图
    wire(9, 4, 10, "spec", "STRING")  # spec → 换脸（第 13 个输入）
    wire(10, 0, 11, 0, "IMAGE")
    wire(10, 1, 12, 0, "STRING")

    return {
        "id": "moss-faceswap",
        "revision": 0,
        "last_node_id": 12,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.75, "offset": [1180, 560]}},
        "version": 0.4,
    }


def main():
    wf = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
    print("已写出 %s（%d 节点 / %d 连线）" % (OUT, len(wf["nodes"]), len(wf["links"])))


if __name__ == "__main__":
    main()
