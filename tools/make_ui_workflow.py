# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成可直接在 ComfyUI 界面打开的故事板工作流（UI 版）。

与 `make_example.py` 的区别：
- 这个版本是**给人用**的：节点尺寸够大、按 ① ② ③ ④ 分组、带 MarkdownNote 使用说明、
  画布视角对准内容。`make_example.py` 那两个是给机检和最小复现用的。

槽位纪律（本脚本的核心）：`widgets_values` 按位置存储，所以这里用
`decl_inputs()` 声明一次输入序列，widget 值与连线槽位**都从这一份推导**，
不手写第二份顺序。

跑法：`python tools/make_ui_workflow.py`
产物：`<ComfyUI>/user/default/workflows/MOSS 故事板宫格.json`
"""

import json
import os
import sys
from _paths import host_root

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFY = host_root()
OUT_DIR = os.path.join(COMFY, "user", "default", "workflows")
OUT_NAME = "MOSS 故事板宫格.json"
#: 仓库内（`--out`）必须用 ASCII 名：registry 打包按 latin-1 解路径，
#: 中文名会被静默跳过（2026-10-23 实测：包内少了整个 examples 里的工作流）。
OUT_NAME_ASCII = "moss-storyboard-4panels-ui.json"

PROMPT = """[STYLE]: 单一故事板画布分割为严格对称网格，恰好 4 个面板，每个面板展示雨夜咖啡店叙事的顺序推进，所有面板由细白边框线整齐分隔，完全没有粗框、黑边或跨面板全景溢出；苏晚：黑发低马尾、米白高领针织衫、哑光织物、自然裸肤毛孔；陈屿：深灰外套、黑衬衫、短发、修长手指、哑光面料；固定环境：雨夜咖啡店靠窗双人桌、湿街反光、玻璃雨痕、暖黄吊灯、柠檬水杯、咖啡杯；相机规格：35mm 胶片摄影、真实演员、自然原始皮肤纹理、有机胶片颗粒、锐利光学镜头焦点、浅景深、近距取景

[PANEL 1]: 画面为中景腰以上构图，相机位于双人桌侧前方，苏晚与陈屿同框对坐，苏晚把柠檬水杯向两人正中轻推，暖黄吊灯主光照亮桌面与玻璃雨痕

[PANEL 2]: 画面为 Choker 特写裁切额头与下巴，相机位于苏晚正前方偏三分法，苏晚面部神情克制、嘴唇微启、眼睑微垂，陈屿在背景中虚化

[PANEL 3]: 画面为胸以上中近景，相机位于陈屿侧前方低角度，陈屿修长手指在咖啡杯上收紧、视线压在杯口、眉骨微紧

[PANEL 4]: 画面为特写，相机位于苏晚眼部与手部之间，苏晚眼睛看向桌面、睫毛投下细影、手指搭在柠檬水杯旁"""

NEGATIVE = "多余面板，错位拼贴，粗黑边框，跨格溢出，文字水印，畸变手指，塑料皮肤"

NOTE = """## 三步
1. ② 改 `panels`（3/4/6/8/9），画布自动算。
2. ③ 提示词**只填这里**：`[STYLE]` 一段 + `[PANEL 1]`…`[PANEL n]` 每格一段。
3. 跑。产物 = 整张宫格 + 逐格。

## 三条不要动
- ① `type` 必须是 `moss_image`。
- ③ `steps=4`（配 4-step LoRA）、`denoise=1.0`。
- ② 提示词留空 —— 它只算尺寸。

## 格数 → 画布（单格 16:9，长边 496）
3=512×848　4=1024×576　6=1520×576　8=2032×576　9=1520×848

## ① LoRA（4-step）
没装这个 LoRA 就选中它按 `Ctrl+B` 旁路，不影响其它接线。
宫格线由节点按坐标复绘（不依赖模型画直线）。"""


class NodeBuilder:
    """按名字索引输入槽，widget 值与槽位共用一份声明。"""

    def __init__(self, nid, ntype, pos, size, title=None):
        self.id = nid
        self.type = ntype
        self.pos = list(pos)
        self.size = list(size)
        self.title = title
        self.inputs = []        # [{name,type,widget?,link}]
        self.outputs = []       # [{name,type,links:[]}]
        self.widgets = []       # 按位置
        self._idx = {}

    def decl_inputs(self, spec):
        """spec: [(name, type, widget_bool, widget_value_or_None), ...]"""
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

    def to_dict(self, order):
        d = {
            "id": self.id, "type": self.type, "pos": self.pos, "size": self.size,
            "flags": {}, "order": order, "mode": 0,
            "inputs": self.inputs, "outputs": self.outputs,
            "properties": {"Node name for S&R": self.type},
            "widgets_values": self.widgets,
        }
        if self.title:
            d["title"] = self.title
        return d

    def bounds(self):
        # 估算渲染高度：widget 多时比声明的 size 高；这里以声明 size 为准，够用
        return (self.pos[0], self.pos[1], self.size[0], self.size[1])


def build():
    nodes = []
    links = []           # [id, from_node, from_slot, to_node, to_slot, type]
    pending = []         # (from_builder, out_name, to_builder, in_name)

    def N(nid, ntype, pos, size, title=None):
        b = NodeBuilder(nid, ntype, pos, size, title)
        nodes.append(b)
        return b

    unet = N(1, "UNETLoader", (-1150, -100), (340, 82), "① MOSS-Image")
    unet.decl_inputs([("unet_name", "COMBO", True, "moss_image_sft_bf16.safetensors"),
                      ("weight_dtype", "COMBO", True, "default")])
    unet.decl_outputs([("MODEL", "MODEL")])

    clip = N(2, "CLIPLoader", (-1150, -6), (340, 106), "① 文本编码器")
    clip.decl_inputs([("clip_name", "COMBO", True, "qwen3.5_9b_int8_convrot.safetensors"),
                      ("type", "COMBO", True, "moss_image"),
                      ("device", "COMBO", True, "default")])
    clip.decl_outputs([("CLIP", "CLIP")])

    vae = N(3, "VAELoader", (-1150, 120), (340, 58), "① VAE")
    vae.decl_inputs([("vae_name", "COMBO", True, "flux2-vae.safetensors")])
    vae.decl_outputs([("VAE", "VAE")])

    lora = N(10, "LoraLoaderModelOnly", (-1150, 214), (340, 82), "① LoRA（4-step，装了再取消旁路）")
    lora.decl_inputs([("model", "MODEL", False, None),
                      ("lora_name", "COMBO", True, "loras_moss_image_sft_4_steps_lora_v2_600_avg_rank_72_bf16.safetensors"),
                      ("strength_model", "FLOAT", True, 1.0)])
    lora.decl_outputs([("MODEL", "MODEL")])

    lay = N(4, "MossStoryboardLayout", (-740, -100), (470, 620), None)
    lay.decl_inputs([
        ("prompt", "STRING", True, ""),          # 故意留空：提示词只填在 ③
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

    smp = N(5, "MossStoryboardSampler", (-210, -100), (520, 800), None)
    smp.decl_inputs([
        ("model", "MODEL", False, None),
        ("clip", "CLIP", False, None),
        ("vae", "VAE", False, None),
        ("positive", "STRING", True, PROMPT),
        ("negative", "STRING", True, NEGATIVE),
        ("steps", "INT", True, 4),
        ("cfg", "FLOAT", True, 1.0),
        ("sampler_name", "COMBO", True, "euler"),
        ("scheduler", "COMBO", True, "simple"),
        ("seed", "INT", True, 555),
        ("denoise", "FLOAT", True, 1.0),
        # —— 以下为 optional ——
        ("spec", "STRING", False, None),
        ("template", "IMAGE", False, None),
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
    ])
    smp.decl_outputs([("grid", "IMAGE"), ("panels", "IMAGE"), ("latent", "LATENT"),
                      ("spec", "STRING"), ("prompt_used", "STRING"), ("report", "STRING")])
    # seed 的"生成后控制"下拉占一个槽位（V1 自动加；V3 由 _compat 显式声明）。
    # 位置 = 包含 seed 在内的 widget 个数（widgets_values 是按位存的，这里算错就整体错位）。
    seed_slot = smp.slot("seed")
    seed_widget_i = sum(1 for n in smp.inputs[: seed_slot + 1] if "widget" in n)
    smp.widgets.insert(seed_widget_i, "randomize")

    prev = N(6, "PreviewImage", (-740, 560), (470, 260), "模板预览")
    prev.decl_inputs([("images", "IMAGE", False, None)])
    prev.decl_outputs([])

    save_grid = N(7, "SaveImage", (370, -100), (500, 620), "④ 存整张")
    save_grid.decl_inputs([("images", "IMAGE", False, None),
                           ("filename_prefix", "STRING", True, "moss/storyboard/grid")])
    save_grid.decl_outputs([("images", "IMAGE")])

    save_pan = N(8, "SaveImage", (370, 560), (500, 620), "④ 存逐格")
    save_pan.decl_inputs([("images", "IMAGE", False, None),
                          ("filename_prefix", "STRING", True, "moss/storyboard/panel")])
    save_pan.decl_outputs([("images", "IMAGE")])

    note = N(9, "MarkdownNote", (-1150, 230), (340, 560), "使用说明")
    note.decl_inputs([])
    note.decl_outputs([])
    note.widgets = [NOTE]

    # ---- 接线（按名字，不按索引）----
    pending += [
        (unet, "MODEL", lora, "model"),
        (lora, "MODEL", smp, "model"),
        (clip, "CLIP", smp, "clip"),
        (vae, "VAE", smp, "vae"),
        (lay, "spec", smp, "spec"),
        (lay, "template", smp, "template"),
        (lay, "template", prev, "images"),
        (smp, "grid", save_grid, "images"),
        (smp, "panels", save_pan, "images"),
    ]
    for src, out_name, dst, in_name in pending:
        lid = len(links) + 1
        oslot = src.out_slot(out_name)
        dslot = dst.slot(in_name)
        links.append([lid, src.id, oslot, dst.id, dslot, src.outputs[oslot]["type"]])
        src.outputs[oslot]["links"].append(lid)
        dst.inputs[dslot]["link"] = lid

    # ---- 分组：由子节点包围盒自动推出，避免手写矩形和实际位置对不上 ----
    groups = [
        ("① 模型加载（LoRA 可选）", [unet, clip, vae, lora]),
        ("② 宫格几何 + 模板预览", [lay, prev]),
        ("③ 故事板生成", [smp]),
        ("④ 出片", [save_grid, save_pan]),
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
            "color": "#3f789e",
            "font_size": 26,
            "flags": {},
        })

    order = {n.id: i for i, n in enumerate([unet, clip, vae, lora, lay, prev, smp, save_grid, save_pan, note])}
    return {
        "id": "moss-storyboard-grid",
        "revision": 0,
        "last_node_id": max(n.id for n in nodes),
        "last_link_id": max((lk[0] for lk in links), default=0),
        "nodes": [n.to_dict(order[n.id]) for n in nodes],
        "links": links,
        "groups": groups_json,
        "config": {},
        "extra": {"ds": {"scale": 0.62, "offset": [1500, 500]}},
        "version": 0.4,
    }


def main(argv):
    # `--out <dir>`：仓库里也存一份（否则 clone 下来只有最小示例，没有带说明的 UI 版）
    out_dir, name = OUT_DIR, OUT_NAME
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]
        name = OUT_NAME_ASCII
    wf = build()
    if "--bypass-lora" in argv:
        # 仓库示例默认旁路 LoRA：别人没有这个权重文件也不会报红，装了再取消旁路
        for n in wf["nodes"]:
            if n["type"] == "LoraLoaderModelOnly":
                n["mode"] = 4
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
    print("已写出 %s" % path)
    print("节点 %d 个 / 连线 %d 条 / 分组 %d 个" % (len(wf["nodes"]), len(wf["links"]), len(wf["groups"])))
    for n in wf["nodes"]:
        if n["type"] in ("MossStoryboardLayout", "MossStoryboardSampler"):
            print("  %s: widgets=%d inputs=%d" % (n["type"], len(n["widgets_values"]), len(n["inputs"])))


if __name__ == "__main__":
    main(sys.argv[1:])
