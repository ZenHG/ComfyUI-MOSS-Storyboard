# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 `moss_llm` 的两个示例工作流（UI 格式，可直接在 ComfyUI 打开）。

为什么用脚本而不是手写 JSON：`MossPromptSmith` 有几十个 widget 槽（数量随参数表变），`widgets_values`
**按位置存** —— 手抄必错一次，而且错得静默（参数串位不报错）。这里先按
`INPUT_TYPES()` 自动展开成声明，再逐例覆盖要改的值，槽位序与
`tools/check_ui_workflow.py` 同一份判据。

跑法：`python tools/make_llm_examples.py`
产物：`examples/llm-prompt-workbench.json` / `examples/llm-idea-to-image.json`
"""

import importlib.machinery
import importlib.util
import json
import os
import sys

from _paths import host_root
from make_ui_workflow import NodeBuilder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "examples")
COMFY = host_root()

#: 只接线的类型（不是 widget）——与 `check_ui_workflow.LINK_ONLY` 同一口径。
LINK_ONLY = {"MODEL", "CLIP", "VAE", "IMAGE", "LATENT", "CONDITIONING", "MASK", "SIGMAS"}
PKG_VER = "0.3.3"

# ── 三例共用的 MOSS 采样配方（取自 examples/moss-storyboard-4panels-ui.json，别自创）──
UNET = ["moss_image_sft_bf16.safetensors", "default"]
CLIP = ["qwen3.5_9b_int8_convrot.safetensors", "moss_image", "default"]
VAE = ["flux2-vae.safetensors"]
#: Sampler 的 widget 声明。⚠ `spec` **不在里面** —— 它是 forceInput（只接线不算 widget），
#: 把它当 widget 填会让 widgets_values 多一位、后面全部串位（实测 22 → 23 报红）。
SAMPLER_DECL = [
    ("positive", "STRING", True, ""),
    ("negative", "STRING", True, ""),
    ("steps", "INT", True, 50),
    ("cfg", "FLOAT", True, 3.0),
    ("sampler_name", "COMBO", True, "euler"),
    ("scheduler", "COMBO", True, "sgm_uniform"),
    ("seed", "INT", True, 555),
    ("control_after_generate", "COMBO", True, "randomize"),
    ("denoise", "FLOAT", True, 1.0),
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
    ("spec", "STRING", False, None),
    ("mode", "COMBO", True, "整张"),
    ("sample_edge", "INT", True, 1024),
]
LAYOUT_DECL = [
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
    ("char_boxes", "STRING", True, ""),
]


def load_pkg(name="moss_llm_mk"):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(ROOT, "__init__.py"))
    spec = importlib.util.spec_from_loader(name, loader,
                                           origin=os.path.join(ROOT, "__init__.py"),
                                           is_package=True)
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [ROOT]
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def smith_decl(overrides):
    """把 `INPUT_TYPES()` 展开成 `decl_inputs()` 需要的 (name, type, is_widget, value)。

    `seed` 后面必须补一个 `control_after_generate` 槽：V1 前端自动加、V3 不声明就没有，
    两条出口的 widgets_values 会差一位（`_compat.py` 里锁的就是这条）。
    """
    nodes_mod = importlib.import_module("moss_llm_mk.moss_llm.nodes")
    it = nodes_mod.MossPromptSmith.INPUT_TYPES()
    decl, values = [], {}
    for section in ("required", "optional"):
        for name, spec in (it.get(section) or {}).items():
            typ = spec[0] if isinstance(spec, tuple) else spec
            opts = spec[1] if isinstance(spec, tuple) and len(spec) > 1 else {}
            if opts.get("forceInput"):
                continue
            is_widget = str(typ).upper() not in LINK_ONLY
            if is_widget:
                if isinstance(typ, list):
                    default = opts.get("default", typ[0])
                    if name in overrides:
                        default = overrides[name]
                        assert default in typ, "%s=%r 不在候选里" % (name, default)
                    values[name] = default
                else:
                    values[name] = overrides.get(name, opts.get("default", ""))
            decl.append((name, str(typ).upper(), is_widget, values.get(name)))
            if name == "seed":
                decl.append(("control_after_generate", "COMBO", True,
                             overrides.get("control_after_generate", "fixed")))
    return decl, values


def smith_node(nid, pos, size, title, overrides):
    decl, _ = smith_decl(overrides)
    b = NodeBuilder(nid, "MossPromptSmith", pos, size, title)
    b.decl_inputs(decl)
    b.decl_outputs([("提示词", "STRING"), ("report", "STRING")])
    return b


def note(nid, pos, size, text, title="这是什么 · 怎么用"):
    b = NodeBuilder(nid, "MarkdownNote", pos, size, title)
    b.decl_inputs([("text", "STRING", True, text)])
    return b


def preview(nid, pos, size, title):
    b = NodeBuilder(nid, "PreviewAny", pos, size, title)
    b.decl_inputs([("source", "*", False, None)])
    b.decl_outputs([("STRING", "STRING")])
    return b


def load_image(nid, pos, size, fname, title):
    b = NodeBuilder(nid, "LoadImage", pos, size, title)
    b.decl_inputs([("image", "COMBO", True, fname), ("upload", "IMAGE", True, None)])
    b.decl_outputs([("IMAGE", "IMAGE"), ("MASK", "MASK")])
    return b


def wire(link_id, src, src_slot, dst, dst_slot, typ, links):
    src.outputs[src_slot]["links"].append(link_id)
    dst.inputs[dst_slot]["link"] = link_id
    links.append([link_id, src.id, src_slot, dst.id, dst_slot, typ])
    return link_id + 1


def finish(nodes, links, groups, name):
    order = {n.id: i for i, n in enumerate(nodes)}
    out = {"id": name, "revision": 0,
           "last_node_id": max(n.id for n in nodes),
           "last_link_id": max(k[0] for k in links) if links else 0,
           "nodes": [n.to_dict(order[n.id]) for n in nodes],
           "links": links, "groups": groups, "config": {},
           "extra": {"ds": {"scale": 0.8, "offset": [820, 420]}}, "version": 0.4}
    for d in out["nodes"]:
        if d["type"].startswith("Moss"):
            d["properties"]["cnr_id"] = "moss-storyboard"
            d["properties"]["ver"] = PKG_VER
    return out


def group(gid, title, pos, size, color="NO_COLOR"):
    return {"id": gid, "title": title, "bounding": [pos[0], pos[1], size[0], size[1]],
            "color": color, "font_size": 24, "flags": {}}


# ───────────────────────────────────────────── 例 5：提示词工作台（四组，按需旁路）
def ex_prompt_workbench():
    nodes, links, groups = [], [], []
    n = note(1, (-840, -440), (620, 1180),
             "## 提示词工作台：反推 / 洗图 / 通用扩写 / PE-I2I（四组独立）\n\n"
             "**这不是一条流水线**：四组各干各的，默认只开「反推」，其余三组已旁路。\n"
             "用哪组就选中该组节点按 Ctrl+B 取消旁路，其余组保持旁路——一次只跑一组，省时间也省显存。\n\n"
             "**① 反推**：LoadImage 选图 → caption → prompt + report。\n"
             "**② 洗图**：同一张图 → caption（wash=开）→ prompt 拿去改词后重绘。\n"
             "**③ 通用扩写**：填 text 想法 → expand（generic_natural）→ 任意模型可用提示词。\n"
             "**④ PE-I2I 原生改写**：参考图 + 中文编辑指令 → edit → PE 改写的中文描述。\n\n"
             "### ④ 要自备什么（默认旁路，别直接跑）\n"
             "PE 权重按 qwen-research 发布，非商用、仅内部使用；本包不含权重、也不含 system_prompt.txt。\n"
             "把 PE-I2I GGUF、配套 mmproj 视觉塔、原生 system_prompt.txt 放进 models/LLM 下同一目录；\n"
             "然后在本组节点的 model 下拉里选它，mmproj 保持自动。没命中 PE 就是普通编辑路径。\n"
             "PE 没有前端开关：选对模型就自动进 PE。命中与否看第二个输出 report 里有没有\n"
             "「PE 改写器模式（编辑）」；命中后 dialect / length / must_keep / panels / style_lock\n"
             "本次不生效，report 会逐一点名。四步触发与非商用声明见两版 README 的 1.6。\n\n"
             "### 选模型\n"
             "model 下拉来自 models/LLM + models/text_encoders；反推/洗图/PE 要视觉模型 + mmproj，"
             "扩写只要文本模型。本地 GGUF 或「外部 API」都行。\n\n"
             "**这四个节点默认用的模型**\n"
             "指向 models/LLM/GGUF/Qwen/.../Qwen3VL-4B-Instruct-Q4_K_M.gguf（视觉塔走 auto）。\n"
             "本包不带权重；没这个文件时节点会直接说「本机没有 GGUF 模型」，两种走法：\n"
             "① 从 Qwen/Qwen3-VL-4B-Instruct-GGUF（HF 官方，Apache-2.0）下这两个文件放进 models/LLM：\n"
             "   Qwen3VL-4B-Instruct-Q4_K_M.gguf + mmproj-Qwen3VL-4B-Instruct-F16.gguf\n"
             "   （目录同名即可，mmproj=auto 会按投影宽度自己配对）；国内可换 hf-mirror.com；\n"
             "② 通道切「外部 API」，填端点与 Key（↻ 能拉清单）。\n\n"
             "### 边界\n"
             "①②③ 共用同一张输入图；换图后一起变。报告里的 must_keep 行是硬约束对账。")
    img = load_image(2, (-160, -440), (340, 320), "comic-4panel-lineart-sample.png", "① 共用输入图")
    cap = smith_node(3, (260, -440), (430, 371), "🧠 LLM Prompt · caption",
                     {"task": "caption", "dialect": "moss_image", "length": "standard"})
    wash = smith_node(4, (260, 80), (430, 371), "🧠 LLM Prompt · wash (bypassed)",
                      {"task": "caption", "dialect": "moss_image", "wash": True,
                       "two_pass": True, "length": "standard"})
    gen = smith_node(5, (260, 600), (430, 371), "🧠 LLM Prompt · expand (bypassed)",
                     {"task": "expand", "dialect": "generic_natural", "length": "standard",
                      "temperature": 0.7,
                      "text": "雨夜便利店门口，一个刚下班的年轻女人端着关东煮，"
                              "暖黄灯光与街上的冷蓝霓虹形成对比",
                      "must_keep": "关东煮；雨夜"})
    p1 = preview(6, (740, -440), (460, 380), "反推 prompt（成品提示词）")
    p2 = preview(7, (740, -40), (460, 260), "反推 report（事实/省略项/tokens）")
    p3 = preview(8, (740, 300), (460, 380), "洗图 prompt（拿去改词）")
    p4 = preview(9, (740, 700), (460, 260), "洗图 report（含 aspect_ratio）")
    p5 = preview(10, (740, 1040), (460, 380), "通用 prompt（接任意文生图节点）")
    p6 = preview(11, (740, 1440), (460, 260), "通用 report（must_keep/tokens）")
    pe = smith_node(12, (260, 1120), (430, 371), "🧠 LLM Prompt · PE-I2I edit (bypassed)",
                    {"task": "edit", "text": "把背景换成雪夜，人物不变"})
    p7 = preview(13, (740, 1840), (460, 380), "PE 编辑提示词（成品）")
    p8 = preview(14, (740, 2240), (460, 300), "PE report（看有没有 PE 改写器模式（编辑））")
    lid = wire(1, img, 0, cap, cap.slot("image"), "IMAGE", links)
    lid = wire(lid, img, 0, wash, wash.slot("image"), "IMAGE", links)
    lid = wire(lid, cap, 0, p1, 0, "STRING", links)
    lid = wire(lid, cap, 1, p2, 0, "STRING", links)
    lid = wire(lid, wash, 0, p3, 0, "STRING", links)
    lid = wire(lid, wash, 1, p4, 0, "STRING", links)
    lid = wire(lid, gen, 0, p5, 0, "STRING", links)
    lid = wire(lid, gen, 1, p6, 0, "STRING", links)
    lid = wire(lid, img, 0, pe, pe.slot("image"), "IMAGE", links)
    lid = wire(lid, pe, 0, p7, 0, "STRING", links)
    wire(lid, pe, 1, p8, 0, "STRING", links)
    groups = [
        group(1, "① 反推（默认开）", (-840, -500), (2100, 940)),
        group(2, "② 洗图（默认旁路）", (-840, 20), (2100, 940)),
        group(3, "③ 通用扩写（默认旁路）", (-840, 540), (2100, 940)),
        group(4, "④ PE-I2I（默认旁路 · 权重自备 · 非商用）", (-840, 1060), (2100, 1320)),
    ]
    wf = finish(nodes + [n, img, cap, wash, gen, p1, p2, p3, p4, p5, p6, pe, p7, p8],
                links, groups, "llm-prompt-workbench")
    for x in wf["nodes"]:
        if x["id"] in (4, 8, 9, 5, 10, 11, 12, 13, 14):
            x["mode"] = 4          # 4 = bypass：默认只跑反推组
    return wf


# ───────────────────────────────────────────── 例 6：单图 + 故事板（两组，按需旁路）
def ex_idea_to_image():
    nodes, links = [], []
    n = note(1, (-940, -440), (640, 820),
             "## 一句话 → 出图：单图 / 4 格故事板（两组独立）\n\n"
             "默认只开「单图」，故事板组已旁路；要出宫格时选中故事板组节点按 Ctrl+B，"
             "再选中单图组按 Ctrl+B 关掉它——一次只跑一组。\n\n"
             "**① 写想法**：两个 Smith 各有 text / must_keep；固定项写进 must_keep（；分隔）。\n"
             "**② 单图**：dialect=moss_image，Sampler 的 panels=1（不接 spec，不出宫格）。\n"
             "**③ 故事板**：dialect=moss_storyboard，panels 与 Layout/Sampler 必须同为 4。\n\n"
             "### 采样配方\n"
             "模型卡口径：50 步 / cfg 3 / euler / sgm_uniform。装了 4-step 蒸馏 LoRA 就旁路它并改 4 步 / cfg 1。\n\n"
             "### 为什么两组分开\n"
             "单图要「整张画面」的散文，故事板要 [STYLE]+[PANEL n] 的分格稿，"
             "用同一个 Smith 输出会在两种文风之间来回漂——各用各的 Smith 更稳。")
    unet = NodeBuilder(2, "UNETLoader", (0, 620), (360, 90), "① MOSS-Image")
    unet.decl_inputs([("unet_name", "COMBO", True, UNET[0]), ("weight_dtype", "COMBO", True, UNET[1])])
    unet.decl_outputs([("MODEL", "MODEL")])
    clip = NodeBuilder(3, "CLIPLoader", (0, 760), (360, 110), "① 文本编码器")
    clip.decl_inputs([("clip_name", "COMBO", True, CLIP[0]), ("type", "COMBO", True, CLIP[1]),
                      ("device", "COMBO", True, CLIP[2])])
    clip.decl_outputs([("CLIP", "CLIP")])
    vae = NodeBuilder(4, "VAELoader", (0, 910), (360, 60), "① VAE")
    vae.decl_inputs([("vae_name", "COMBO", True, VAE[0])])
    vae.decl_outputs([("VAE", "VAE")])

    sm_single = smith_node(5, (0, -440), (430, 371), "🧠 LLM Prompt · single image",
                           {"task": "expand", "dialect": "moss_image", "length": "standard",
                            "temperature": 0.7,
                            "text": "雨夜便利店门口，一个刚下班的年轻女人端着关东煮，"
                                    "暖黄灯光与街上的冷蓝霓虹形成对比",
                            "must_keep": "关东煮；雨夜"})
    smp_single = NodeBuilder(6, "MossStoryboardSampler", (520, -440), (420, 900), "🎬 Storyboard · Generate · single image")
    smp_single.decl_inputs([("model", "MODEL", False, None), ("clip", "CLIP", False, None),
                            ("vae", "VAE", False, None)] + _sampler_decl(panels=1))
    smp_single.decl_outputs([("grid", "IMAGE"), ("panels", "IMAGE"), ("latent", "LATENT"),
                             ("spec", "STRING"), ("prompt_used", "STRING"), ("report", "STRING"),
                             ("prompts_json", "STRING")])
    save_single = NodeBuilder(7, "SaveImage", (1000, -440), (380, 320), "③ 存单图")
    save_single.decl_inputs([("images", "IMAGE", False, None),
                             ("filename_prefix", "COMBO", True, "MOSS_single")])

    sm_board = smith_node(8, (0, 900), (430, 371), "🧠 LLM Prompt · storyboard (bypassed)",
                          {"task": "expand", "dialect": "moss_storyboard", "panels": 4,
                           "length": "standard", "temperature": 0.7,
                           "text": "雨夜便利店，一个加班的女人买关东煮，店员递袋子时说了句「路上小心」，"
                                   "店里的灯把水汽照得很亮",
                           "must_keep": "路上小心；关东煮"})
    lay = NodeBuilder(9, "MossStoryboardLayout", (520, 900), (380, 420), "🎬 Storyboard · Grid Layout · 4 panels (bypassed)")
    lay.decl_inputs(LAYOUT_DECL)
    lay.decl_outputs([("width", "INT"), ("height", "INT"), ("columns", "INT"), ("rows", "INT"),
                      ("spec", "STRING"), ("layout_json", "STRING"), ("template", "IMAGE"),
                      ("report", "STRING")])
    smp_board = NodeBuilder(10, "MossStoryboardSampler", (1000, 900), (420, 900), "🎬 Storyboard · Generate · 4 panels (bypassed)")
    smp_board.decl_inputs([("model", "MODEL", False, None), ("clip", "CLIP", False, None),
                           ("vae", "VAE", False, None)] + _sampler_decl(panels=4))
    smp_board.decl_outputs([("grid", "IMAGE"), ("panels", "IMAGE"), ("latent", "LATENT"),
                            ("spec", "STRING"), ("prompt_used", "STRING"), ("report", "STRING"),
                            ("prompts_json", "STRING")])
    save_board = NodeBuilder(11, "SaveImage", (1480, 900), (380, 320), "④ 存整张（默认旁路）")
    save_board.decl_inputs([("images", "IMAGE", False, None),
                            ("filename_prefix", "COMBO", True, "MOSS_board")])

    lid = 1
    lid = wire(lid, unet, 0, smp_single, 0, "MODEL", links)
    lid = wire(lid, clip, 0, smp_single, 1, "CLIP", links)
    lid = wire(lid, vae, 0, smp_single, 2, "VAE", links)
    lid = wire(lid, sm_single, 0, smp_single, smp_single.slot("positive"), "STRING", links)
    lid = wire(lid, smp_single, 0, save_single, 0, "IMAGE", links)
    lid = wire(lid, unet, 0, smp_board, 0, "MODEL", links)
    lid = wire(lid, clip, 0, smp_board, 1, "CLIP", links)
    lid = wire(lid, vae, 0, smp_board, 2, "VAE", links)
    lid = wire(lid, sm_board, 0, smp_board, smp_board.slot("positive"), "STRING", links)
    lid = wire(lid, lay, 4, smp_board, smp_board.slot("spec"), "STRING", links)
    wire(lid, smp_board, 0, save_board, 0, "IMAGE", links)
    groups = [
        group(1, "① 单图（默认开）", (-940, -500), (2500, 1000)),
        group(2, "② 4 格故事板（默认旁路）", (-940, 840), (2900, 1000)),
    ]
    wf = finish(nodes + [n, unet, clip, vae, sm_single, smp_single, save_single,
                         sm_board, lay, smp_board, save_board], links, groups, "llm-idea-to-image")
    for x in wf["nodes"]:
        if x["id"] in (8, 9, 10, 11):
            x["mode"] = 4
    return wf


def _sampler_decl(panels: int):
    out = []
    for name, typ, widget, value in SAMPLER_DECL:
        if name == "panels":
            value = panels
        out.append((name, typ, widget, value))
    return out


def main():
    if COMFY not in sys.path:
        sys.path.insert(0, COMFY)
    load_pkg()
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, fn in (("llm-prompt-workbench", ex_prompt_workbench),
                     ("llm-idea-to-image", ex_idea_to_image)):
        wf = fn()
        path = os.path.join(OUT_DIR, name + ".json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(wf, fh, ensure_ascii=False, indent=1)
        sm = [x for x in wf["nodes"] if x["type"] == "MossPromptSmith"][0]
        print("已写 %-28s 节点 %d / 连线 %d / Smith widgets=%d"
              % (name + ".json", len(wf["nodes"]), len(wf["links"]), len(sm["widgets_values"])))


if __name__ == "__main__":
    main()

