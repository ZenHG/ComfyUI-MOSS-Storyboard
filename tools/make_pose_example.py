# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 examples/moss-pose-generate-ui.json —— 「参考图 + 提示词 → 一张带指定动作的图」。

**没有第三方节点，也没有新增节点。** 靠换脸节点自己的 init 开关：

    init = source（默认）→ 吃 image 做图生图，有蒙版就只重画蒙版内（换脸）
    init = empty        → 不吃 image，从空 latent 生成（参考图 + 提示词 → 一张新图）

两者是同一件事的两个特例，差别只有「从哪开始」。所以姿态/动作这条路径
不需要装 GPL 的 RefHiRes，也不需要第 8 个节点。

跑法：python tools/make_pose_example.py
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples", "moss-pose-generate-ui.json")

#: 中景基线（E21 原文）。脸应占画幅 0.08–0.20。
BASE = ("A medium shot photograph of the person in Picture 1, framed from the knees up, "
        "standing in a plain grey studio, soft key light from the left, "
        "calm neutral expression, ")

#: 九档动作（E21）。默认 a02_cross（抱胸）—— 幅度明显、又不带转头。
ACTION = "standing with both arms crossed over the chest, looking at the camera."

NOTE = """## 姿态 / 肢体动作（参考图 → 一张图）

**一个节点搞定**：本包「🎬 故事板 · 换脸」的 init 设成 **empty**。
它和换脸是同一件事的两个特例，差别只有「从哪开始」：

| init | 从哪开始 | 用途 |
|---|---|---|
| source（默认） | 吃 image 做图生图 | 换脸；有蒙版只重画蒙版内 |
| **empty** | 空 latent | 参考图 + 提示词 → 一张新图（本示例） |

所以这条路径**不需要**装 ComfyUI-MOSS-RefHiRes，也不需要别的节点。

### 三处必须改
1. **参考图**：换成**正脸**特写。⚠ 参考图的姿态会锁死输出 —— 侧脸参考图喂进去，
   提示词怎么写都拉不回正脸（实测参考图 yaw +5.20 时七档提示词全画成侧脸）。
2. **提示词尾巴**：只改最后一句，前半段别动（那是「中景 + 灰棚 + 柔光」的基线）。
3. **width / height**：默认 1024×576（16:9），必须是 16 的倍数。
   接了 spec 就由 spec 定画布，这两个值被忽略。

### init=empty 下不生效的旋钮
denoise / sample_edge / auto_mask / face_pick / index —— 没有源图，
也就没有「保留原图」和「只改哪一块」。report 会写明这一点。

### 九档动作（把提示词尾巴换成其中一句）
| 档 | 尾巴 |
|---|---|
| a00_still | standing still with both arms relaxed at the sides, looking at the camera. |
| a01_pocket | standing with both hands in the pockets, looking at the camera. |
| a02_cross | standing with both arms crossed over the chest, looking at the camera. |
| a03_walk | walking forward toward the camera, one leg in front of the other, arms swinging naturally. |
| a04_reach | standing and reaching forward with the right hand as if picking something up, the head still facing the camera. |
| a05_sit | sitting on a simple wooden stool, hands resting on the knees, looking at the camera. |
| a06_turn | standing with the torso turned sideways, but the head still turned toward the camera. |
| a07_handface | standing with the right hand raised to the chin, fingers touching the jaw, thinking. |
| a08_back | standing with the back to the camera, head turned back over the shoulder. |

### 七档姿态（要测转头，把前半段换成「特写」基线）
前半段：An extreme close-up portrait photograph of a person, the face filling most of the
frame, head and shoulders, plain grey studio wall behind, soft key light from the left,
calm neutral expression,

| 档 | 尾巴 |
|---|---|
| p00_front | looking straight at the camera, head perfectly frontal, both ears visible. |
| p15_soft | head turned slightly, a little more of the right cheek visible than the left, eyes still toward the camera. |
| p30_three | head turned in a three-quarter view, clearly more of the right cheek visible, the right ear half hidden. |
| p50_strong | head turned far in a strong three-quarter view, the right ear is hidden behind the cheek, the left eye near the silhouette edge. |
| p75_profile | head turned almost in profile, only a sliver of the far eye visible, the nose breaking the silhouette. |
| pd_down | head tilted down, chin lowered toward the chest, eyes looking down. |
| pu_up | head tilted up, chin raised, eyes looking up at the ceiling. |

> 左右方向一律用「**哪边脸颊可见**」描述，别写 left / right —— 后者在模型里歧义。

### 实测结论（照抄不要凭感觉）
- **动作九档全部生效**，身份净损失 −0.086 … +0.112，七档在 ±0.05 以内（采样地板量级）。
- **伤脸的是连带转头**：背身回看（yaw 0.76）→ −0.086；躯干转侧但头朝镜头只有 +0.036。
- **手抵下颌无害**（+0.112，高于地板）—— 只挡到下颌线，没挡眼鼻。
- **a01_pocket 像素差只有 8.05**，「手插兜」画面几乎没变，不能当「动作生效」的样本。

### 还想再收紧身份
把这里的 image 输出接到 examples/moss-faceswap-ui.json 的换脸节点（init=source，
auto_mask 开着）。顺序不能倒：**先定动作，再换脸** —— 换脸只重画蒙版内的脸，
身体在蒙版外逐像素保留，提示词动不了动作。
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

    nodes.append(node(1, "MarkdownNote", (-1060, -300), (600, 900), [NOTE],
                      title="这是什么 · 怎么用", order=0, ours=False))

    # ---- 模型三件套 + 两层 LoRA（8 步蒸馏 → 编辑）----
    nodes.append(node(2, "UNETLoader", (-420, -300), (340, 82),
                      ["moss_image_sft_bf16.safetensors", "default"],
                      outputs=[out("MODEL", "MODEL")], order=1, ours=False))
    nodes.append(node(3, "LoraLoaderModelOnly", (-420, -180), (340, 82),
                      ["8-steps-dmd2-lora.safetensors", 1.0],
                      inputs=[inp("model", "MODEL", 1, "model")],
                      outputs=[out("MODEL", "MODEL")], order=2, ours=False))
    nodes.append(node(4, "LoraLoaderModelOnly", (-420, -60), (340, 82),
                      ["Moss-Image-Edit\\adapter_model.safetensors", 1.0],
                      inputs=[inp("model", "MODEL", 2, "model")],
                      outputs=[out("MODEL", "MODEL")], order=3, ours=False))
    nodes.append(node(5, "CLIPLoader", (-420, 60), (340, 106),
                      ["qwen3.5_9b_int8_convrot.safetensors", "moss_image", "default"],
                      outputs=[out("CLIP", "CLIP")], order=4, ours=False))
    nodes.append(node(6, "VAELoader", (-420, 200), (340, 58),
                      ["flux2-vae.safetensors"],
                      outputs=[out("VAE", "VAE")], order=5, ours=False))

    # ---- 参考图（只给一张）----
    nodes.append(node(7, "LoadImage", (-420, 300), (340, 320), ["moss_faceswap_ref.png", "image"],
                      inputs=[inp("image", "COMBO", None, "image"),
                              inp("upload", "IMAGEUPLOAD", None, "upload")],
                      outputs=[out("IMAGE", "IMAGE"), out("MASK", "MASK")],
                      title="ref · 正脸参考图（侧脸会锁死输出姿态）", order=6, ours=False))

    # ---- 本包节点：init=empty ⇒ 不吃 image，从空 latent 生成 ----
    # widgets 顺序 = required 的 widget + optional 的 widget（image / spec / mask 是接线）
    nodes.append(node(8, "MossStoryboardFaceSwap", (0, 300), (440, 720),
                      [BASE + ACTION, 8, 1.0, "euler", "simple", 0, "randomize",
                       0.95, 1.05, 0, 1024, "", True, "largest", "empty", 1024, 576],
                      inputs=[inp("model", "MODEL", 3),
                              inp("clip", "CLIP", 4),
                              inp("vae", "VAE", 5),
                              inp("ref", "IMAGE", 5),
                              inp("prompt", "STRING", None, "prompt"),
                              inp("steps", "INT", None, "steps"),
                              inp("cfg", "FLOAT", None, "cfg"),
                              inp("sampler_name", "COMBO", None, "sampler_name"),
                              inp("scheduler", "COMBO", None, "scheduler"),
                              inp("seed", "INT", None, "seed"),
                              inp("denoise", "FLOAT", None, "denoise"),
                              inp("vision_megapixels", "FLOAT", None, "vision_megapixels"),
                              inp("image", "IMAGE"),
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
                      title="🎬 故事板 · 换脸（init=empty ⇒ 参考图生成）", order=7))

    nodes.append(node(9, "SaveImage", (520, 300), (420, 560), ["moss/pose/pose"],
                      inputs=[inp("images", "IMAGE", 6)],
                      outputs=[out("images", "IMAGE")],
                      order=8, ours=False))

    # report 一定要接出来：init=empty 下哪些旋钮不生效、画布来自哪里，全写在里面。
    nodes.append(node(10, "PreviewAny", (520, 900), (420, 260), [],
                      inputs=[inp("source", "*", 7)],
                      outputs=[out("STRING", "STRING")],
                      title="report（看画布来源与哪些旋钮不生效）", order=9, ours=False))

    #: 按 **id** 找节点（节点数组顺序与 id 不一定一致）。
    by_id = {n["id"]: n for n in nodes}

    def wire(src, sslot, dst, dslot, typ):
        lid = len(links) + 1
        links.append([lid, src, sslot, dst, dslot, typ])
        # 同一个输出可能被多处消费 ⇒ 追加，不能覆盖
        cur = by_id[src]["outputs"][sslot]["links"]
        by_id[src]["outputs"][sslot]["links"] = list(cur) + [lid]
        by_id[dst]["inputs"][dslot]["link"] = lid

    wire(2, 0, 3, 0, "MODEL")     # UNET → DMD2
    wire(3, 0, 4, 0, "MODEL")     # DMD2 → Edit LoRA
    wire(4, 0, 8, 0, "MODEL")     # LoRA 链 → 换脸节点
    wire(5, 0, 8, 1, "CLIP")
    wire(6, 0, 8, 2, "VAE")
    wire(7, 0, 8, 3, "IMAGE")     # 参考图 → ref
    wire(8, 0, 9, 0, "IMAGE")
    wire(8, 1, 10, 0, "STRING")   # report → 预览

    return {
        "last_node_id": 10,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.8, "offset": [1100, 560]}},
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
