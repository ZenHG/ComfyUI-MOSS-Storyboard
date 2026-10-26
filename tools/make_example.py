# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成示例工作流（两个：原生布局路线 / 模板混合路线）。

为什么用脚本生成而不是手写 JSON：UI 工作流的 `widgets_values` 是**按位**存的，
手写时错一个位置，参数就静默串位（画布 8 格却按 4 格裁）。脚本里几何与槽位
是同一份定义，并且 `tools/check_ui_workflow.py` 会拿 `INPUT_TYPES()` 反向核对。

跑法：`python tools/make_example.py`
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "fixtures")

PROMPT = """[STYLE]: 单一故事板画布分割为严格对称网格，恰好 8 个面板，每个面板展示雨夜咖啡店叙事的顺序推进，所有面板由细白边框线整齐分隔，完全没有粗框、黑边或跨面板全景溢出；苏晚：黑发低马尾、米白高领针织衫、哑光织物、自然裸肤毛孔；陈屿：深灰外套、黑衬衫、短发、修长手指、哑光面料；固定环境：雨夜咖啡店靠窗双人桌、湿街冷反光、玻璃雨痕、暖黄吊灯、柠檬水杯、咖啡杯；相机规格：35mm 胶片摄影、真实演员、自然原始皮肤纹理、有机胶片颗粒、锐利光学镜头焦点、浅景深、近距取景

[PANEL 1]: 画面为中景腰以上构图，相机位于双人桌侧前方，苏晚与陈屿同框对坐，苏晚把柠檬水杯向两人正中轻推，暖黄吊灯主光照亮桌面与玻璃雨痕

[PANEL 2]: 画面为 Choker 特写裁切额头与下巴，相机位于苏晚正前方偏三分法，苏晚面部神情克制、嘴唇微启、眼睑微垂，陈屿在背景中虚化

[PANEL 3]: 画面为胸以上中近景，相机位于陈屿侧前方低角度，陈屿修长手指在咖啡杯上收紧、视线压在杯口、眉骨微紧

[PANEL 4]: 画面为特写，相机位于苏晚眼部与手部之间，苏晚眼睛看向桌面、睫毛投下细影、手指搭在柠檬水杯旁

[PANEL 5]: 画面为过肩中景，相机位于苏晚右肩后方，陈屿在对面可见，握住咖啡杯、视线抬起、嘴唇微动

[PANEL 6]: 画面为 Choker 特写，相机位于陈屿正前方偏三分法，陈屿视线从杯口抬向苏晚、眉骨压低、嘴唇闭紧

[PANEL 7]: 画面为胸以上中近景，相机位于苏晚侧前方三分法，苏晚面部克制、视线落在陈屿方向、嘴唇轻抿

[PANEL 8]: 画面为特写，相机位于桌面正中偏上，苏晚的手停在柠檬水杯上没有收回，陈屿手指在咖啡杯旁收紧，雨声填满停顿"""

NEGATIVE = "多余面板，错位拼贴，粗黑边框，跨格溢出，文字水印，畸变手指，塑料皮肤"


def node(nid, ntype, pos, size, widgets, inputs=(), outputs=(), title=None, order=0):
    n = {
        "id": nid, "type": ntype, "pos": list(pos), "size": list(size),
        "flags": {}, "order": order, "mode": 0,
        "inputs": [dict(i) for i in inputs],
        "outputs": [dict(o) for o in outputs],
        "properties": {"Node name for S&R": ntype, "cnr_id": "moss-storyboard", "ver": "0.1.0"},
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
    """8 格纯布局约束示例（本包不做图生图，只保留这一种形态）。"""
    nodes, links = [], []

    def link(fr, fslot, to, tslot, typ):
        lid = len(links) + 1
        links.append([lid, fr, fslot, to, tslot, typ])
        return lid

    # ---- 模型三件套（对齐 Moss 官方节点工作流）----
    nodes.append(node(1, "UNETLoader", (-980, -120), (340, 82),
                      ["moss_image_sft_bf16.safetensors", "default"],
                      outputs=[out("MODEL", "MODEL")], title=None, order=0))
    nodes.append(node(2, "CLIPLoader", (-980, 20), (340, 106),
                      ["qwen3.5_9b_int8_convrot.safetensors", "moss_image", "default"],
                      outputs=[out("CLIP", "CLIP")], title=None, order=1))
    nodes.append(node(3, "VAELoader", (-980, 170), (340, 58),
                      ["flux2-vae.safetensors"],
                      outputs=[out("VAE", "VAE")], title=None, order=2))

    # ---- 布局节点 ----
    lay_inputs = [inp(n, t, widget=(n if t in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO") else None))
                  for n, t in (("prompt", "STRING"), ("panels", "INT"), ("panel_aspect", "COMBO"),
                               ("panel_edge", "INT"), ("canvas_aspect", "COMBO"), ("columns", "INT"),
                               ("rows", "INT"), ("gutter", "INT"), ("margin", "INT"),
                               ("cell_gray", "INT"), ("invert", "BOOLEAN"), ("label", "COMBO"))]
    lay_outputs = [
        out("width", "INT"), out("height", "INT"), out("columns", "INT"), out("rows", "INT"),
        out("spec", "STRING"), out("layout_json", "STRING"), out("template", "IMAGE"), out("report", "STRING"),
    ]
    nodes.append(node(4, "MossStoryboardLayout", (-560, -120), (420, 560),
                      [PROMPT, 8, "16:9", 512, "auto", 0, 0, 8, 8, 25, False, "object"],
                      inputs=lay_inputs, outputs=lay_outputs, title=None, order=3))

    # ---- 采样器 ----
    denoise = 1.0
    init = "layout"
    sampler_inputs = [
        inp("model", "MODEL"), inp("clip", "CLIP"), inp("vae", "VAE"),
        inp("positive", "STRING", widget="positive"), inp("negative", "STRING", widget="negative"),
        inp("spec", "STRING"), inp("panels", "INT", widget="panels"),
        inp("panel_aspect", "COMBO", widget="panel_aspect"),
        inp("panel_edge", "INT", widget="panel_edge"), inp("canvas_aspect", "COMBO", widget="canvas_aspect"),
        inp("gutter", "INT", widget="gutter"), inp("margin", "INT", widget="margin"),
        inp("init", "COMBO", widget="init"), inp("label", "COMBO", widget="label"),
        inp("reborder", "BOOLEAN", widget="reborder"), inp("border_px", "INT", widget="border_px"),
        inp("shave", "INT", widget="shave"),
        inp("mode", "COMBO", widget="mode"), inp("sample_edge", "INT", widget="sample_edge"),
        inp("seed", "INT", widget="seed"),
        inp("steps", "INT", widget="steps"), inp("cfg", "FLOAT", widget="cfg"),
        inp("sampler_name", "COMBO", widget="sampler_name"), inp("scheduler", "COMBO", widget="scheduler"),
        inp("denoise", "FLOAT", widget="denoise"),
    ]
    sampler_outputs = [
        out("grid", "IMAGE"), out("panels", "IMAGE"), out("latent", "LATENT"),
        out("spec", "STRING"), out("prompt_used", "STRING"), out("report", "STRING"),
    ]
    # ⚠ widgets_values 按**位置**存：顺序必须与节点 INPUT_TYPES 的 widget 顺序一致
    #   （seed 后面跟一个"生成后控制"槽位）。少一个就整体错位。
    sampler_widgets = [PROMPT, NEGATIVE, 25, 2.5, "er_sde", "simple", 0, "fixed", denoise,
                       8, "16:9", 512, "auto", 8, 8, init, "object", True, 0, 0, "整张", 1024]
    nodes.append(node(5, "MossStoryboardSampler", (-60, -120), (480, 700), sampler_widgets,
                      inputs=sampler_inputs, outputs=sampler_outputs,
                      title=None, order=6))

    nodes.append(node(6, "SaveImage", (500, -120), (520, 620),
                      ["moss/storyboard_%date:yyyyMMddhhmmss%/%mode%/grid"],
                      inputs=[inp("images", "IMAGE"), inp("filename_prefix", "STRING")],
                      outputs=[out("images", "IMAGE")], title=None, order=8))
    nodes.append(node(7, "SaveImage", (500, 540), (520, 620),
                      ["moss/storyboard_%date:yyyyMMddhhmmss%/%mode%/panel"],
                      inputs=[inp("images", "IMAGE"), inp("filename_prefix", "STRING")],
                      outputs=[out("images", "IMAGE")], title=None, order=8))
    nodes.append(node(8, "PreviewImage", (1040, -120), (480, 360), [],
                      inputs=[inp("images", "IMAGE")], title=None, order=10))

    # ---- 连线 ----
    def set_links(nid, slot, lid_list):
        nodes[nid - 1]["outputs"][slot]["links"] = lid_list

    def wire(src, sslot, dst_idx, dslot, typ, dst_node_idx):
        """接一根线：登记到 links 表、源输出、目标输入三处（少一处宿主就丢线）。"""
        lid = link(src, sslot, dst_idx, dslot, typ)
        set_links(src, sslot, [lid])
        nodes[dst_node_idx]["inputs"][dslot]["link"] = lid
        return lid

    # UNET → LoRA → sampler.model 由 wire 保证三处一致
    wire(1, 0, 5, 0, "MODEL", 4)
    wire(2, 0, 5, 1, "CLIP", 4)
    wire(3, 0, 5, 2, "VAE", 4)
    wire(4, 4, 5, 5, "STRING", 4)
    # Layout.template → PreviewImage（模板图只作几何示意，不再进采样器）
    set_links(4, 6, [wire(4, 6, 8, 0, "IMAGE", 7)])
    wire(5, 0, 6, 0, "IMAGE", 5)
    wire(5, 1, 7, 0, "IMAGE", 6)

    return {
        "id": "moss-storyboard-layout",
        "revision": 0,
        "last_node_id": 8,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.85, "offset": [1200, 420]}},
        "version": 0.4,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in ("moss-storyboard-8panels-layout.json",):
        wf = build()
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(wf, fh, ensure_ascii=False, indent=1)
        print("已写出 %s（%d 节点 / %d 连线）" % (name, len(wf["nodes"]), len(wf["links"])))


if __name__ == "__main__":
    main()
