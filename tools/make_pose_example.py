# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 `examples/moss-pose-generate-ui.json` —— 「参考图 + 提示词 → 一张带指定动作的图」。

**这个示例里没有任何本包节点。** 它存在的理由：姿态/肢体动作那两条实测结论
（七档姿态、九档动作）走的是「空 latent + 参考图进 vision」这条生成路径，
而本包的生成节点（`MossStoryboardSampler`）入参里没有 IMAGE，复现不了。
与其为它加第 8 个节点，不如把宿主原生三节点搭好的图直接给出来。

链路（与 `审计报告/实验/e20_pose_gradient.py`、`e21_body_action.py` 一致）：

    UNETLoader → DMD2 LoRA → Edit LoRA
    CLIPLoader → TextEncodeMOSSImageEditHiRes (vision) ← LoadImage(参考图)
    EmptyFlux2LatentImage 1024×576 → KSampler → VAEDecode → SaveImage

跑法：`python tools/make_pose_example.py`
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples", "moss-pose-generate-ui.json")

#: 中景基线（E21 原文）。脸应占画幅 0.08–0.20。
BASE = ("A medium shot photograph of the person in Picture 1, framed from the knees up, "
        "standing in a plain grey studio, soft key light from the left, "
        "calm neutral expression, ")

#: 九档动作（E21）。默认用 a02_cross（抱胸）—— 动作幅度明显、又不带转头。
ACTION = ("standing with both arms crossed over the chest, looking at the camera.")

#: 换脸那一步的提示词。**最后一句是关键** —— 没有它，模型会把整张图的影调重画一遍。
#: 这里的源图已经是「动作对了、脸也对」的图，换脸只是把身份再收紧一次。
SWAP_PROMPT = ("A photograph of the person in Picture 1. Keep the eyes, the hair, the "
               "lighting, the clothing and the background exactly the same as the source image.")

NOTE = """## 姿态 / 肢体动作（参考图 → 一张图）

**这个工作流不含本包节点。** 本包的生成节点入参里没有 IMAGE，做不了「参考图 + 提示词 → 新图」，
所以这条路径直接用宿主原生三节点搭。

### 链路
```
UNETLoader → 8-steps-dmd2 LoRA → Moss-Image-Edit LoRA
CLIPLoader → TextEncodeMOSSImageEditHiRes  ← LoadImage(正脸参考图)
             ↑ edit_format = Moss-Image-Edit (vision)
             ↑ vision_megapixels = 1.05 · image_strength = 1.0 · ref 0.99/1.0（关 ref_kv）
EmptyFlux2LatentImage 1024×576 → KSampler → VAEDecode → SaveImage
KSampler：8 步 / cfg 1.0 / euler + simple / denoise 0.92
```

### 三处必须改
1. **参考图**：换成**正脸**特写。⚠ 参考图的姿态会锁死输出 —— 侧脸参考图喂进去，
   提示词怎么写都拉不回正脸（实测参考图 yaw +5.20 时七档提示词全画成侧脸）。
2. **提示词尾巴**：只改最后一句，前半段别动（那是「中景 + 灰棚 + 柔光」的基线，
   改了就不是同一组实验了）。
3. **`EmptyFlux2LatentImage` 的宽高**：1024×576 是实验用的；想换比例改这里，
   但要保持 16 的倍数。

### 九档动作（把提示词尾巴换成其中一句）
| 档 | 尾巴 |
|---|---|
| a00_still | `standing still with both arms relaxed at the sides, looking at the camera.` |
| a01_pocket | `standing with both hands in the pockets, looking at the camera.` |
| a02_cross | `standing with both arms crossed over the chest, looking at the camera.` |
| a03_walk | `walking forward toward the camera, one leg in front of the other, arms swinging naturally.` |
| a04_reach | `standing and reaching forward with the right hand as if picking something up, the head still facing the camera.` |
| a05_sit | `sitting on a simple wooden stool, hands resting on the knees, looking at the camera.` |
| a06_turn | `standing with the torso turned sideways, but the head still turned toward the camera.` |
| a07_handface | `standing with the right hand raised to the chin, fingers touching the jaw, thinking.` |
| a08_back | `standing with the back to the camera, head turned back over the shoulder.` |

### 七档姿态（要测转头，把前半段换成「特写」基线）
前半段：`An extreme close-up portrait photograph of a person, the face filling most of the
frame, head and shoulders, plain grey studio wall behind, soft key light from the left,
calm neutral expression, `

| 档 | 尾巴 |
|---|---|
| p00_front | `looking straight at the camera, head perfectly frontal, both ears visible.` |
| p15_soft | `head turned slightly, a little more of the right cheek visible than the left, eyes still toward the camera.` |
| p30_three | `head turned in a three-quarter view, clearly more of the right cheek visible, the right ear half hidden.` |
| p50_strong | `head turned far in a strong three-quarter view, the right ear is hidden behind the cheek, the left eye near the silhouette edge.` |
| p75_profile | `head turned almost in profile, only a sliver of the far eye visible, the nose breaking the silhouette.` |
| pd_down | `head tilted down, chin lowered toward the chest, eyes looking down.` |
| pu_up | `head tilted up, chin raised, eyes looking up at the ceiling.` |

> 左右方向一律用「**哪边脸颊可见**」描述，别写 `left` / `right` —— 后者在模型里歧义。

### 实测结论（照抄不要凭感觉）
- **动作九档全部生效**，身份净损失 `−0.086 … +0.112`，七档在 ±0.05 以内（采样地板量级）。
- **伤脸的是连带转头**：背身回看（yaw 0.76）→ `−0.086`；躯干转侧但头朝镜头只有 `+0.036`。
- **手抵下颌无害**（`+0.112`，高于地板）—— 只挡到下颌线，没挡眼鼻。
- **`a01_pocket` 像素差只有 8.05**，「手插兜」画面几乎没变，不能当「动作生效」的样本。

### 这个工作流分两段
```
[第一段] 宿主三节点：参考图 + 提示词 → 一张带动作的图   （预览节点，不落盘）
[第二段] 🎬 故事板 · 换脸：对上面那张图再收紧一次身份   （落盘）
```
**只要动作、不要换脸**：删掉第二段的两个节点（换脸 + SaveImage），把 `PreviewImage` 换成 `SaveImage`。
**只要换脸、不要动作**：直接用 `examples/moss-faceswap-ui.json`。

反过来不行：换脸节点的蒙版只重画脸，身体在蒙版外逐像素保留（实测 `0.40/255`），
提示词动不了动作。**先定动作，再换脸**，顺序不能倒。
"""


def node(nid, ntype, pos, size, widgets, inputs=(), outputs=(), title=None, order=0):
    n = {
        "id": nid, "type": ntype, "pos": list(pos), "size": list(size),
        "flags": {}, "order": order, "mode": 0,
        "inputs": [dict(i) for i in inputs],
        "outputs": [dict(o) for o in outputs],
        "properties": {"Node name for S&R": ntype},
        "widgets_values": list(widgets),
    }
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

    nodes.append(node(1, "MarkdownNote", (-1200, -300), (600, 900), [NOTE],
                      title="这是什么 · 怎么用", order=0))

    # ---- 模型三件套 + 两层 LoRA（8 步蒸馏 → 编辑）----
    nodes.append(node(2, "UNETLoader", (-540, -300), (340, 82),
                      ["moss_image_sft_bf16.safetensors", "default"],
                      outputs=[out("MODEL", "MODEL")], order=1))
    nodes.append(node(3, "LoraLoaderModelOnly", (-540, -180), (340, 82),
                      ["8-steps-dmd2-lora.safetensors", 1.0],
                      inputs=[inp("model", "MODEL", 1, "model")],
                      outputs=[out("MODEL", "MODEL")], order=2))
    nodes.append(node(4, "LoraLoaderModelOnly", (-540, -60), (340, 82),
                      ["Moss-Image-Edit\\adapter_model.safetensors", 1.0],
                      inputs=[inp("model", "MODEL", 2, "model")],
                      outputs=[out("MODEL", "MODEL")], order=3))
    nodes.append(node(5, "CLIPLoader", (-540, 60), (340, 106),
                      ["qwen3.5_9b_int8_convrot.safetensors", "moss_image", "default"],
                      outputs=[out("CLIP", "CLIP")], order=4))
    nodes.append(node(6, "VAELoader", (-540, 200), (340, 58),
                      ["flux2-vae.safetensors"],
                      outputs=[out("VAE", "VAE")], order=5))

    # ---- 参考图（只给一张）----
    nodes.append(node(7, "LoadImage", (-540, 300), (340, 320), ["moss_faceswap_ref.png", "image"],
                      inputs=[inp("image", "COMBO", None, "image"),
                              inp("upload", "IMAGEUPLOAD", None, "upload")],
                      outputs=[out("IMAGE", "IMAGE"), out("MASK", "MASK")],
                      title="ref · 正脸参考图（只给一张；侧脸会锁死输出姿态）", order=6))

    # ---- 编码：参考图进 vision；不接 vae（= 不挂 reference latents）----
    # widgets 顺序 = schema 声明顺序里的 widget（clip / vae / images 是接线）
    nodes.append(node(8, "TextEncodeMOSSImageEditHiRes", (-140, 300), (420, 420),
                      [BASE + ACTION, "Moss-Image-Edit (vision)", 1.05, 1.0, 0.99, 1.0, False],
                      inputs=[inp("clip", "CLIP", 3),
                              inp("prompt", "STRING", None, "prompt"),
                              inp("vae", "VAE"),
                              inp("edit_format", "COMBO", None, "edit_format"),
                              inp("images", "COMFY_AUTOGROW_V3", 4),
                              inp("vision_megapixels", "FLOAT", None, "vision_megapixels"),
                              inp("image_strength", "FLOAT", None, "image_strength"),
                              inp("ref_start_percent", "FLOAT", None, "ref_start_percent"),
                              inp("ref_end_percent", "FLOAT", None, "ref_end_percent"),
                              inp("verbose", "BOOLEAN", None, "verbose")],
                      outputs=[out("CONDITIONING", "CONDITIONING")],
                      title="编码 · 参考图进 vision（edit_format 用 vision）", order=7))

    # ---- 空 latent（1024×576）----
    nodes.append(node(9, "EmptyFlux2LatentImage", (-140, 780), (340, 106), [1024, 576, 1],
                      outputs=[out("LATENT", "LATENT")], order=8))

    # ---- 采样 ----
    nodes.append(node(10, "KSampler", (340, 300), (320, 262),
                      [0, "randomize", 8, 1.0, "euler", "simple", 0.92],
                      inputs=[inp("model", "MODEL", 5),
                              inp("positive", "CONDITIONING", 6),
                              inp("negative", "CONDITIONING", 7),
                              inp("latent_image", "LATENT", 8)],
                      outputs=[out("LATENT", "LATENT")], order=9))
    nodes.append(node(11, "VAEDecode", (700, 300), (210, 46),
                      [], inputs=[inp("samples", "LATENT", 9), inp("vae", "VAE", 10)],
                      outputs=[out("IMAGE", "IMAGE")], order=10))
    nodes.append(node(12, "PreviewImage", (700, 400), (420, 460), [],
                      inputs=[inp("images", "IMAGE", 11)],
                      outputs=[out("images", "IMAGE")],
                      title="pose 中间结果（只想看动作就停在这）", order=11))

    # ---- 换脸：动作定好之后，把身份再收紧一次 ----
    # 不接 spec ⇒ 整张图当一张图换（单图换脸），与上面的 1024×576 画布一致。
    nodes.append(node(13, "MossStoryboardFaceSwap", (700, 900), (420, 620),
                      [SWAP_PROMPT, 8, 1.0, "euler", "simple", 0, "randomize",
                       0.95, 1.05, 0, 1024, "", True, "largest"],
                      inputs=[inp("model", "MODEL", 12),
                              inp("clip", "CLIP", 13),
                              inp("vae", "VAE", 14),
                              inp("image", "IMAGE", 15),
                              inp("ref", "IMAGE", 16),
                              inp("prompt", "STRING", None, "prompt"),
                              inp("steps", "INT", None, "steps"),
                              inp("cfg", "FLOAT", None, "cfg"),
                              inp("sampler_name", "COMBO", None, "sampler_name"),
                              inp("scheduler", "COMBO", None, "scheduler"),
                              inp("seed", "INT", None, "seed"),
                              inp("denoise", "FLOAT", None, "denoise"),
                              inp("vision_megapixels", "FLOAT", None, "vision_megapixels"),
                              inp("spec", "STRING"),
                              inp("index", "INT", None, "index"),
                              inp("sample_edge", "INT", None, "sample_edge"),
                              inp("negative", "STRING", None, "negative"),
                              inp("auto_mask", "BOOLEAN", None, "auto_mask"),
                              inp("face_pick", "COMBO", None, "face_pick"),
                              inp("mask", "MASK")],
                      outputs=[out("image", "IMAGE"), out("report", "STRING")],
                      title="🎬 故事板 · 换脸（把身份收紧一次）", order=12))

    nodes.append(node(14, "SaveImage", (1180, 900), (420, 560), ["moss/pose/pose_swap"],
                      inputs=[inp("images", "IMAGE", 17)],
                      outputs=[out("images", "IMAGE")],
                      title="最终（动作 + 身份）", order=13))

    #: 按 **id** 找节点（节点数组顺序与 id 不一定一致）。
    by_id = {n["id"]: n for n in nodes}

    def wire(src, sslot, dst, dslot, typ):
        lid = len(links) + 1
        links.append([lid, src, sslot, dst, dslot, typ])
        # 同一个输出可能被多处消费（编码节点同时给 positive 和 negative）⇒ 追加，不能覆盖
        cur = by_id[src]["outputs"][sslot]["links"]
        by_id[src]["outputs"][sslot]["links"] = list(cur) + [lid]
        by_id[dst]["inputs"][dslot]["link"] = lid

    wire(2, 0, 3, 0, "MODEL")          # UNET → DMD2
    wire(3, 0, 4, 0, "MODEL")          # DMD2 → Edit LoRA
    wire(5, 0, 8, 0, "CLIP")           # CLIP → 编码
    wire(7, 0, 8, 4, "IMAGE")          # 参考图 → images（Autogrow 第 0 槽）
    wire(4, 0, 10, 0, "MODEL")         # LoRA 链 → KSampler
    wire(8, 0, 10, 1, "CONDITIONING")  # positive
    wire(8, 0, 10, 2, "CONDITIONING")  # negative = 同一个（cfg 1.0 时宿主跳过负面分支）
    wire(9, 0, 10, 3, "LATENT")        # 空 latent
    wire(10, 0, 11, 0, "LATENT")
    wire(6, 0, 11, 1, "VAE")
    wire(11, 0, 12, 0, "IMAGE")        # pose 结果 → 预览
    # ---- 换脸那一段：复用上面同一套 model / clip / vae / 参考图 ----
    wire(11, 0, 13, 3, "IMAGE")        # pose 结果 → 换脸的 image（源图）
    wire(7, 0, 13, 4, "IMAGE")         # 同一张参考图 → 换脸的 ref
    wire(4, 0, 13, 0, "MODEL")
    wire(5, 0, 13, 1, "CLIP")
    wire(6, 0, 13, 2, "VAE")
    wire(13, 0, 14, 0, "IMAGE")

    return {
        "last_node_id": 14,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.75, "offset": [1250, 560]}},
        "version": 0.4,
    }


def main():
    wf = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("写出 %s（%d 节点 / %d 连线）"
          % (os.path.relpath(OUT, ROOT), len(wf["nodes"]), len(wf["links"])))


if __name__ == "__main__":
    main()
