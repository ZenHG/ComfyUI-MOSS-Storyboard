# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成「只改一格」端到端示例工作流（UI 版）。

覆盖 README「只改一格」写的那条链：
  生成宫格 → 裁出要改的那格 → 官方编辑节点改好 → 贴回整张宫格

与 `make_ui_workflow.py`（纯生成）分开的原因：这条链多出四个宿主节点
（LoraLoaderModelOnly / TextEncodeMOSSImageEdit / EmptyFlux2LatentImage / KSampler）
和一次 VAEDecode，混在一起会让最小工作流变得很难读。

槽位纪律同 `make_ui_workflow.py`：`widgets_values` 按位置存，
所以用 `decl_inputs()` 声明一次，widget 值与连线槽位都从这一份推导。

跑法：`python tools/make_edit_workflow.py`
产物：`<ComfyUI>/user/default/workflows/MOSS 故事板宫格-只改一格.json`
仓库内副本（`--out examples`）用 ASCII 名 —— registry 打包按 latin-1 解路径，
中文名会被静默丢掉（2026-10-23 实测）。
"""

import json
import os
import sys
from _paths import host_root

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFY = host_root()
OUT_DIR = os.path.join(COMFY, "user", "default", "workflows")
OUT_NAME = "MOSS 故事板宫格-只改一格.json"
OUT_NAME_ASCII = "moss-storyboard-4panels-edit.json"

#: 与 `make_ui_workflow.py` 同一份稿，保证两个示例跑出来是同一张图。
PROMPT = """[STYLE]: 单一故事板画布分割为严格对称网格，恰好 4 个面板，每个面板展示雨夜咖啡店叙事的顺序推进，所有面板由细白边框线整齐分隔，完全没有粗框、黑边或跨面板全景溢出；苏晚：黑发低马尾、米白高领针织衫、哑光织物、自然裸肤毛孔；陈屿：深灰外套、黑衬衫、短发、修长手指、哑光面料；固定环境：雨夜咖啡店靠窗双人桌、湿街反光、玻璃雨痕、暖黄吊灯、柠檬水杯、咖啡杯；相机规格：35mm 胶片摄影、真实演员、自然原始皮肤纹理、有机胶片颗粒、锐利光学镜头焦点、浅景深、近距取景

[PANEL 1]: 画面为中景腰以上构图，相机位于双人桌侧前方，苏晚与陈屿同框对坐，苏晚把柠檬水杯向两人正中轻推，暖黄吊灯主光照亮桌面与玻璃雨痕

[PANEL 2]: 画面为 Choker 特写裁切额头与下巴，相机位于苏晚正前方偏三分法，苏晚面部神情克制、嘴唇微启、眼睑微垂，陈屿在背景中虚化

[PANEL 3]: 画面为胸以上中近景，相机位于陈屿侧前方低角度，陈屿修长手指在咖啡杯上收紧、视线压在杯口、眉骨微紧

[PANEL 4]: 画面为特写，相机位于苏晚眼部与手部之间，苏晚眼睛看向桌面、睫毛投下细影、手指搭在柠檬水杯旁"""

NEGATIVE = ""

#: 编辑指令只改"衣服 + 帽子"，明确要求保住脸/机位/背景。
#: ⚠ 不要在指令里要求换场景——那会让参考图的作用看不出来，也不再是"编辑"。
EDIT = """Change the off-white high-collar knit sweater into a bright emerald-green hooded cloak, and add a tall pointed red hat on her head. Keep the face, the framing, the camera angle, the rainy-night cafe background and the warm yellow lighting exactly the same."""

NOTE = """## 这条链干什么
只改一格，不重跑整张。某一格不满意时，重跑会让**所有格的种子一起变**；
这条链把那一格裁出来、单独改好、再按 `spec` 坐标贴回去。

## 五步
1. **① 装两个 LoRA（可选）**：
   - `4-step` 蒸馏 LoRA —— 让宫格 4 步出图
   - `Moss-Image-Edit` —— 编辑能力全部来自它（约 285 MB）
     Hugging Face `huggingface.co/chinoll/Moss-Image-Edit`
     魔搭 `modelscope.cn/models/ChenkinLab/Moss-Image-Edit`
     两个文件放 `models/loras/Moss-Image-Edit/`
2. **② 选采样配方（先选一档）**：
   - **不装 LoRA（默认）**：两个 LoRA 保持旁路；生成节点与编辑 `KSampler` 都用
     **MOSS-Image-SFT 模型卡口径 50 步 / cfg 3 / euler / sgm_uniform**。
     能完整生成 + 贴回，但没有 MOSS 指令编辑。
   - **装了两个 LoRA**：选中两个 LoRA 节点按 `Ctrl+B` 取消旁路；生成节点改 **4 步 / cfg 1.0**；
     编辑 `KSampler` 改 **8 步 / cfg 1.0**（本机实测背景差异 8.1/255，优于此前 30 步 / cfg 4 档的 11.4/255）。
3. **③ 改哪一格**：`MossStoryboardCrop` 的 `index`，**0 起、行主序**
   （从左到右、从上到下）。默认 1 = 右上那格。它的 `index` 输出直接接给贴回节点，
   **不用手打第二遍**。
4. **④ 编辑指令**：`TextEncodeMOSSImageEdit` 的 prompt，用英文写指令。
   ⚠ **别在指令里要求换场景**——那不是编辑，是重画。写「把 X 换成 Y，其余保持不变」。
5. 跑。产物见 ⑥。

## 三个要知道的事
- **`vae` 必须接线**：`edit_format` 选 `Moss-Image-Edit` 时，参考图**必须**编码成
  `reference_latents` 才是这个 LoRA 训练时的布局。留空则参考图几乎不起作用，
  模型会按指令重画——换掉人脸和机位。实测：不接 vae 脸部差异 78.5/255，
  接上 21.6/255。
- **编辑也可以叠 4 步 LoRA**：④ 的 `steps` / `cfg` 改成 `8` / `1.0`，
  并让 4 步 LoRA 串在编辑 LoRA 之前。本机实测 8 步 / cfg 1.0：背景差异 8.1/255，
  优于此前 30 步 / cfg 4 档的 11.4/255，且快 8 倍。
- **贴回去的那格会更亮更锐**：编辑档与宫格档不同，这是两档配方的固有差异，不是 bug。
  想接近就把 ④ 的 `steps` 调小。
- **本包自身不需要任何 LoRA**：不装也能完整生成 + 贴回，只是不能用 MOSS 的指令编辑。

## 许可
`Moss-Image-Edit` 是第三方社区权重，不随本包分发；其代码部分为 GPLv3。
本包（MIT）只在使用文档里指向它。"""

L4STEP = "loras_moss_image_sft_4_steps_lora_v2_600_avg_rank_72_bf16.safetensors"
EDIT_LORA = "Moss-Image-Edit\\adapter_model.safetensors"


class NodeBuilder:
    """按名字索引输入槽，widget 值与槽位共用一份声明。"""

    def __init__(self, nid, ntype, pos, size, title=None):
        self.id = nid
        self.type = ntype
        self.pos = list(pos)
        self.size = list(size)
        self.title = title
        self.inputs = []
        self.outputs = []
        self.widgets = []
        self._idx = {}

    def decl_inputs(self, spec):
        for name, typ, is_widget, value in spec:
            item = {"name": name, "type": typ, "link": None}
            if is_widget:
                item["widget"] = {"name": name}
                self.widgets.append(value)
            self._idx[name] = len(self.inputs)
            self.inputs.append(item)
        return self

    def decl_outputs(self, spec):
        for name, typ in spec:
            self.outputs.append({"name": name, "type": typ, "links": []})
        return self

    def slot(self, name):
        return self._idx[name]

    def out_slot(self, name):
        return [i for i, o in enumerate(self.outputs) if o["name"] == name][0]

    def insert_seed_control(self):
        """seed 的"生成后控制"下拉占一个槽位（V1 自动加，V3 由 _compat 显式声明）。
        位置 = 包含 seed 在内的 widget 个数；算错就整体错位。"""
        i = self.slot("seed")
        n = sum(1 for x in self.inputs[: i + 1] if "widget" in x)
        self.widgets.insert(n, "randomize")

    def to_dict(self, order):
        d = {
            "id": self.id, "type": self.type, "pos": self.pos, "size": self.size,
            "flags": {}, "order": order, "mode": 0,
            "inputs": self.inputs, "outputs": self.outputs,
            "properties": {"Node name for S&R": self.type},
            "widgets_values": self.widgets,
        }
        if self.title:
            d["title"] = d.get("title") or self.title
        return d


def build():
    nodes, links, pending = [], [], []

    def N(nid, ntype, pos, size, title=None):
        b = NodeBuilder(nid, ntype, pos, size, title)
        nodes.append(b)
        return b

    # ---- ① 模型加载 ----
    unet = N(1, "UNETLoader", (-1500, -120), (350, 82), "① MOSS-Image")
    unet.decl_inputs([("unet_name", "COMBO", True, "moss_image_sft_bf16.safetensors"),
                      ("weight_dtype", "COMBO", True, "default")])
    unet.decl_outputs([("MODEL", "MODEL")])

    clip = N(2, "CLIPLoader", (-1500, -20), (350, 106), "① 文本编码器")
    clip.decl_inputs([("clip_name", "COMBO", True, "qwen3.5_9b_int8_convrot.safetensors"),
                      ("type", "COMBO", True, "moss_image"),
                      ("device", "COMBO", True, "default")])
    clip.decl_outputs([("CLIP", "CLIP")])

    vae = N(3, "VAELoader", (-1500, 110), (350, 58), "① VAE")
    vae.decl_inputs([("vae_name", "COMBO", True, "flux2-vae.safetensors")])
    vae.decl_outputs([("VAE", "VAE")])

    lora4 = N(4, "LoraLoaderModelOnly", (-1500, 196), (350, 82), "① LoRA · 4 步蒸馏（可选）")
    lora4.decl_inputs([("model", "MODEL", False, None),
                       ("lora_name", "COMBO", True, L4STEP),
                       ("strength_model", "FLOAT", True, 1.0)])
    lora4.decl_outputs([("MODEL", "MODEL")])

    # ---- ② 生成宫格 ----
    lay = N(5, "MossStoryboardLayout", (-1100, -120), (460, 640), None)
    lay.decl_inputs([
        ("prompt", "STRING", True, ""),
        ("panels", "INT", True, 4),
        ("panel_aspect", "COMBO", True, "16:9"),
        ("panel_edge", "INT", True, 496),
        ("canvas_aspect", "COMBO", True, "auto"),
        ("columns", "INT", True, 0),
        ("rows", "INT", True, 0),
        ("gutter", "INT", True, 8),
        ("margin", "INT", True, 8),
        ("cell_gray", "INT", True, 25),
        ("invert", "BOOLEAN", True, False),
        ("label", "COMBO", True, "object"),
    ])
    lay.decl_outputs([("width", "INT"), ("height", "INT"), ("columns", "INT"), ("rows", "INT"),
                      ("spec", "STRING"), ("layout_json", "STRING"), ("template", "IMAGE"),
                      ("report", "STRING")])

    smp = N(6, "MossStoryboardSampler", (-580, -120), (520, 800), None)
    smp.decl_inputs([
        ("model", "MODEL", False, None),
        ("clip", "CLIP", False, None),
        ("vae", "VAE", False, None),
        ("positive", "STRING", True, PROMPT),
        ("negative", "STRING", True, NEGATIVE),
        ("steps", "INT", True, 50),
        ("cfg", "FLOAT", True, 3.0),
        ("sampler_name", "COMBO", True, "euler"),
        ("scheduler", "COMBO", True, "sgm_uniform"),
        ("seed", "INT", True, 555),
        ("denoise", "FLOAT", True, 1.0),
        ("spec", "STRING", False, None),
        ("panels", "INT", True, 4),
        ("panel_aspect", "COMBO", True, "16:9"),
        ("panel_edge", "INT", True, 496),
        ("canvas_aspect", "COMBO", True, "auto"),
        ("gutter", "INT", True, 8),
        ("margin", "INT", True, 8),
        ("init", "COMBO", True, "layout"),
        ("label", "COMBO", True, "object"),
        ("reborder", "BOOLEAN", True, True),
        ("border_px", "INT", True, 0),
        ("shave", "INT", True, 0),
        # 0.2.0 新增：只能追加在旧 widget 之后（widgets_values 按位置存）
        ("mode", "COMBO", True, "整张"),
        ("sample_edge", "INT", True, 1024),
    ])
    smp.decl_outputs([("grid", "IMAGE"), ("panels", "IMAGE"), ("latent", "LATENT"),
                      ("spec", "STRING"), ("prompt_used", "STRING"), ("report", "STRING")])
    smp.insert_seed_control()

    save_orig = N(7, "SaveImage", (-580, 720), (520, 560), "⑥ 原图（贴回前存一份）")
    save_orig.decl_inputs([("images", "IMAGE", False, None),
                           ("filename_prefix", "STRING", True, "moss/storyboard/edit-orig")])
    save_orig.decl_outputs([("images", "IMAGE")])

    # ---- ③ 裁出要改的那一格 ----
    crop = N(8, "MossStoryboardCrop", (20, -120), (400, 400), "🎬 故事板 · 裁格（要改的那格）")
    crop.decl_inputs([
        ("image", "IMAGE", False, None),
        ("target_aspect", "COMBO", True, "keep"),
        ("padding_mode", "COMBO", True, "crop"),
        ("resize_edge", "INT", True, 0),
        ("shave", "INT", True, 0),
        ("spec", "STRING", False, None),
        ("columns", "INT", True, 0),
        ("rows", "INT", True, 0),
        ("show_guides", "BOOLEAN", True, False),
        ("index", "INT", True, 1),
    ])
    crop.decl_outputs([("panels", "IMAGE"), ("preview", "IMAGE"), ("report", "STRING"),
                       ("index", "INT")])

    # ---- ④ 编辑那一格 ----
    lora_edit = N(9, "LoraLoaderModelOnly", (20, 330), (400, 82), "④ LoRA · 编辑（必需）")
    lora_edit.decl_inputs([("model", "MODEL", False, None),
                           ("lora_name", "COMBO", True, EDIT_LORA),
                           ("strength_model", "FLOAT", True, 1.0)])
    lora_edit.decl_outputs([("MODEL", "MODEL")])

    enc = N(10, "TextEncodeMOSSImageEdit", (20, 440), (400, 300), "④ 编辑指令")
    enc.decl_inputs([
        ("clip", "CLIP", False, None),
        ("prompt", "STRING", True, EDIT),
        ("edit_format", "COMBO", True, "Moss-Image-Edit"),
        # Autogrow 输入：UI 里是单个 images 槽，API 侧键名 images.image_0
        ("images", "IMAGE", False, None),
        ("image_strength", "FLOAT", True, 1.0),
        ("vae", "VAE", False, None),
    ])
    enc.decl_outputs([("CONDITIONING", "CONDITIONING")])

    empty = N(11, "EmptyFlux2LatentImage", (20, 770), (400, 90), "④ 画布（与格子同尺寸）")
    empty.decl_inputs([("width", "INT", True, 496),
                       ("height", "INT", True, 272),
                       ("batch_size", "INT", True, 1)])
    empty.decl_outputs([("LATENT", "LATENT")])

    ksam = N(12, "KSampler", (480, 330), (400, 300), "④ 重采样（模型卡口径 50 步 / cfg 3）")
    ksam.decl_inputs([
        ("model", "MODEL", False, None),
        ("positive", "CONDITIONING", False, None),
        ("negative", "CONDITIONING", False, None),
        ("latent_image", "LATENT", False, None),
        ("seed", "INT", True, 20261024),
        ("steps", "INT", True, 50),
        ("cfg", "FLOAT", True, 3.0),
        ("sampler_name", "COMBO", True, "euler"),
        ("scheduler", "COMBO", True, "sgm_uniform"),
        ("denoise", "FLOAT", True, 1.0),
    ])
    ksam.decl_outputs([("LATENT", "LATENT")])
    ksam.insert_seed_control()

    dec = N(13, "VAEDecode", (480, 660), (400, 60), None)
    dec.decl_inputs([("samples", "LATENT", False, None), ("vae", "VAE", False, None)])
    dec.decl_outputs([("IMAGE", "IMAGE")])

    save_panel = N(14, "SaveImage", (480, 750), (400, 500), "⑥ 改好的单格")
    save_panel.decl_inputs([("images", "IMAGE", False, None),
                            ("filename_prefix", "STRING", True, "moss/storyboard/edit-panel")])
    save_panel.decl_outputs([("images", "IMAGE")])

    # ---- ⑤ 贴回 ----
    paste = N(15, "MossStoryboardPaste", (940, -120), (430, 320), "🎬 故事板 · 贴回（改好的整张）")
    paste.decl_inputs([
        ("grid", "IMAGE", False, None),
        ("panel", "IMAGE", False, None),
        ("index", "INT", True, 1),
        ("shave", "INT", True, 0),
        ("show_guides", "BOOLEAN", True, False),
        ("spec", "STRING", False, None),
        ("columns", "INT", True, 0),
        ("rows", "INT", True, 0),
    ])
    paste.decl_outputs([("grid", "IMAGE"), ("marked", "IMAGE"), ("report", "STRING")])

    save_final = N(16, "SaveImage", (940, 240), (430, 500), "⑥ 最终宫格")
    save_final.decl_inputs([("images", "IMAGE", False, None),
                            ("filename_prefix", "STRING", True, "moss/storyboard/edit-final")])
    save_final.decl_outputs([("images", "IMAGE")])

    note = N(17, "MarkdownNote", (-1500, 320), (350, 620), "使用说明")
    note.decl_inputs([])
    note.decl_outputs([])
    note.widgets = [NOTE]

    # ---- 接线（按名字，不按索引）----
    pending += [
        (unet, "MODEL", lora4, "model"),
        (lora4, "MODEL", smp, "model"),
        # 编辑段也串 4 步 LoRA：unet → 4步 → 编辑LoRA → KSampler
        # （不串的话编辑段只有模型卡的 50 步/cfg3 一档；串上可切 8 步/cfg1，快 8 倍）
        (lora4, "MODEL", lora_edit, "model"),
        (clip, "CLIP", smp, "clip"),
        (clip, "CLIP", enc, "clip"),
        (vae, "VAE", smp, "vae"),
        (vae, "VAE", dec, "vae"),
        # ⚠ 必须接：edit_format=Moss-Image-Edit 时参考图要编码成 reference_latents，
        #    留空则参考图几乎不起作用，模型按指令重画（人脸/机位都会变）。
        (vae, "VAE", enc, "vae"),
        (lay, "spec", smp, "spec"),
        (lay, "spec", crop, "spec"),
        (lay, "spec", paste, "spec"),
        (smp, "grid", crop, "image"),      # 裁格吃的是整张宫格
        (smp, "grid", save_orig, "images"),
        (smp, "grid", paste, "grid"),
        (crop, "panels", enc, "images"),
        (crop, "index", paste, "index"),    # 格号不用手打第二遍
        (lora_edit, "MODEL", ksam, "model"),
        (enc, "CONDITIONING", ksam, "positive"),
        (enc, "CONDITIONING", ksam, "negative"),
        (empty, "LATENT", ksam, "latent_image"),
        (ksam, "LATENT", dec, "samples"),
        (dec, "IMAGE", save_panel, "images"),
        (dec, "IMAGE", paste, "panel"),
        (paste, "grid", save_final, "images"),
    ]
    for src, out_name, dst, in_name in pending:
        lid = len(links) + 1
        oslot = src.out_slot(out_name)
        dslot = dst.slot(in_name)
        links.append([lid, src.id, oslot, dst.id, dslot, src.outputs[oslot]["type"]])
        src.outputs[oslot]["links"].append(lid)
        dst.inputs[dslot]["link"] = lid

    groups = [
        ("① 模型加载", [unet, clip, vae, lora4]),
        ("② 生成宫格", [lay, smp, save_orig]),
        ("③ 裁出那一格", [crop]),
        ("④ 编辑", [lora_edit, enc, empty, ksam, dec, save_panel]),
        ("⑤ 贴回", [paste, save_final]),
    ]
    groups_json = []
    for title, members in groups:
        xs = [m.pos[0] for m in members]
        ys = [m.pos[1] for m in members]
        xe = [m.pos[0] + m.size[0] for m in members]
        ye = [m.pos[1] + m.size[1] for m in members]
        pad = 26
        groups_json.append({
            "title": title,
            "bounding": [min(xs) - pad, min(ys) - pad - 30,
                         max(xe) - min(xs) + 2 * pad, max(ye) - min(ys) + 2 * pad + 30],
            "color": "#3f789e", "font_size": 26, "flags": {},
        })

    order = {n.id: i for i, n in enumerate(nodes)}
    return {
        "id": "moss-storyboard-edit",
        "revision": 0,
        "last_node_id": max(n.id for n in nodes),
        "last_link_id": max((lk[0] for lk in links), default=0),
        "nodes": [n.to_dict(order[n.id]) for n in nodes],
        "links": links,
        "groups": groups_json,
        "config": {},
        "extra": {"ds": {"scale": 0.62, "offset": [1500, 400]}},
        "version": 0.4,
    }


BYPASS = ("LoraLoaderModelOnly",)


def main(argv):
    out_dir, name = OUT_DIR, OUT_NAME
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]
        name = OUT_NAME_ASCII
    wf = build()
    for n in wf["nodes"]:
        if n["type"] in BYPASS:
            n["mode"] = 4          # 旁路：没装权重也不会报红，装了再 Ctrl+B 取消
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
    print("已写出 %s" % path)
    print("节点 %d 个 / 连线 %d 条 / 分组 %d 个" % (len(wf["nodes"]), len(wf["links"]), len(wf["groups"])))
    for n in wf["nodes"]:
        if n["type"].startswith("MossStoryboard"):
            print("  %-26s widgets=%-3d inputs=%d  mode=%d"
                  % (n["type"], len(n["widgets_values"]), len(n["inputs"]), n["mode"]))


if __name__ == "__main__":
    main(sys.argv[1:])
