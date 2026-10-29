# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 examples/moss-faceswap-single-ui.json —— 换脸的**单图**版本。

与 `moss-faceswap-ui.json` 的差别只有一处：**不接 spec**。
不接 spec 时换脸节点把整张 image 当成一格（`core/faceswap.py::resolve_target`），
所以同一张图进来、换完直接出去，没有宫格、没有 index、没有贴回。

为什么单独做一个示例：接不接 spec 是这条链最容易踩的岔路 ——
**接了 spec 就必须喂同一张宫格**，否则节点直接报「源图 W×H 与 spec 画布不一致」。
单张照片换脸是更常见的用法，值得有一份能直接拖进去跑的。

跑法：python tools/make_faceswap_single_example.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples", "moss-faceswap-single-ui.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_faceswap_example as F  # noqa: E402  node / inp / out 三个小工具

#: 提示词模板。**必须提到 Picture 1** —— 参考图是靠这个词进场景的。
#: 前缀 `Picture 1: <|vision_start|>…` 由节点自动拼，这里不要自己写。
PROMPT = ("A close-up portrait photograph of the person in Picture 1, standing against a "
          "plain grey studio wall, soft key light from the left, calm neutral expression, "
          "35mm film, real skin texture")

NOTE = """## 换脸 · 单图（MossStoryboardFaceSwap，不接 spec）

一张图进，换完直接出。**没有宫格、没有 index、没有贴回。**

### 与 moss-faceswap-ui.json 的唯一差别
那个示例接了 spec，所以源图必须是**整张宫格**，只换第 index 格；
这个示例**不接 spec**，整张图被当成一格，换完整张出。

### 三处必须改
1. **两个 LoadImage** 换成你自己的图：
   - src = 要换脸的那张（**单人**、脸越大越好）
   - ref = 身份参考图，**紧特写**，只给一张（双视角实测更差：0.384 vs 0.463）
2. **提示词**里保留 in Picture 1 这个说法 —— 换成别的措辞参考图就不进场景了。
3. face_pick：一张图里有几个人时点名（largest / left / right），report 会写「几张脸、用了哪张」。

### 自动蒙版（默认开）
auto_mask=true 时节点用 insightface 找脸并生成**羽化矩形**蒙版，只重画蒙版内。
需要 pip install insightface onnxruntime + ~/.insightface/models/buffalo_l/det_10g.onnx（19 MB）。
**缺依赖会直接报错**，不会静默降级成整图重画 —— 报错里会打印它实际找的路径。

关掉 auto_mask 又没手接 mask ⇒ **整张重画**，色调必漂（实测 R +24…+53）。report 会写明走了哪条。

### 想只改一块（局部编辑）
同一个节点、换一个蒙版就行：ImageToMask(channel="red") → mask。
**别用 LoadImage 的 MASK 口** —— 那是 alpha，灰度图没 alpha 时返回 64×64 全零，
蒙版等于没接、整张被重画，而且不报错。

### 想改动作 / 换姿态
把 init 从 source 改成 empty：节点不吃 image，从空 latent 生成 ——
「参考图 + 提示词 → 一张新图」。示例见 moss-pose-generate-ui.json。

### 两档参数（同一张图同一蒙版实测）
| 档 | 步数 / cfg | 用途 |
|---|---|---|
| 快档（默认） | 8 / 1.0 | 换脸 —— 脸那个尺度上材质看不出来 |
| 编辑档 | 30 / 5 | 衣服、头发、较大区域 |
"""


def build():
    node, inp, out = F.node, F.inp, F.out
    nodes, links = [], []

    nodes.append(node(1, "MarkdownNote", (-1180, -260), (560, 720), [NOTE],
                      title="这是什么 · 怎么用", ours=False))

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

    nodes.append(node(7, "LoadImage", (-560, 350), (340, 320), ["moss_faceswap_src.png", "image"],
                      inputs=[inp("image", "COMBO", None, "image"),
                              inp("upload", "IMAGEUPLOAD", None, "upload")],
                      outputs=[out("IMAGE", "IMAGE"), out("MASK", "MASK")],
                      title="src · 要换脸的图（单张照片，不是宫格）", order=5, ours=False))
    nodes.append(node(8, "LoadImage", (-560, 710), (340, 320), ["moss_faceswap_ref.png", "image"],
                      inputs=[inp("image", "COMBO", None, "image"),
                              inp("upload", "IMAGEUPLOAD", None, "upload")],
                      outputs=[out("IMAGE", "IMAGE"), out("MASK", "MASK")],
                      title="ref · 身份参考图（紧特写，只给一张）", order=6, ours=False))

    # widgets 顺序 = INPUT_TYPES 里**有 widget 的那些**（spec / image / mask 是接线，不在其中）
    nodes.append(node(9, "MossStoryboardFaceSwap", (260, 350), (420, 620),
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
                              inp("spec", "STRING"),
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
                      title="🎬 故事板 · 换脸（不接 spec = 整图当一格）", order=7))

    nodes.append(node(10, "SaveImage", (760, 350), (400, 560), ["moss/faceswap/single"],
                      inputs=[inp("images", "IMAGE", 9)],
                      outputs=[out("images", "IMAGE")],
                      title="换完的图", order=8, ours=False))
    nodes.append(node(11, "PreviewAny", (760, 940), (400, 220), [],
                      inputs=[inp("source", "*", 10)],
                      outputs=[out("STRING", "STRING")],
                      title="report（蒙版覆盖 / 实际生效的降噪）", order=9, ours=False))

    by_id = {n["id"]: n for n in nodes}

    def wire(src, sslot, dst, dslot, typ):
        """dslot 给**名字**，不给下标 —— 下标在改 inputs 顺序时会静默错位。"""
        if isinstance(dslot, str):
            dslot = [i["name"] for i in by_id[dst]["inputs"]].index(dslot)
        lid = len(links) + 1
        links.append([lid, src, sslot, dst, dslot, typ])
        cur = by_id[src]["outputs"][sslot]["links"]
        by_id[src]["outputs"][sslot]["links"] = list(cur) + [lid]
        by_id[dst]["inputs"][dslot]["link"] = lid

    wire(2, 0, 3, 0, "MODEL")
    wire(3, 0, 4, 0, "MODEL")
    wire(4, 0, 9, "model", "MODEL")
    wire(5, 0, 9, "clip", "CLIP")
    wire(6, 0, 9, "vae", "VAE")
    wire(7, 0, 9, "image", "IMAGE")
    wire(8, 0, 9, "ref", "IMAGE")
    wire(9, 0, 10, 0, "IMAGE")
    wire(9, 1, 11, 0, "STRING")

    return {
        "id": "moss-faceswap-single",
        "revision": 0,
        "last_node_id": 11,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.8, "offset": [1240, 300]}},
        "version": 0.4,
    }


if __name__ == "__main__":
    wf = build()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(wf, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("写出 %s：%d 节点 / %d 连线" % (os.path.relpath(OUT, ROOT), len(wf["nodes"]), len(wf["links"])))
