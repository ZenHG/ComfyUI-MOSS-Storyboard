# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 `moss_llm` 的三个示例工作流（UI 格式，可直接在 ComfyUI 打开）。

为什么用脚本而不是手写 JSON：`MossPromptSmith` 有 33 个 widget 槽，`widgets_values`
**按位置存** —— 手抄必错一次，而且错得静默（参数串位不报错）。这里先按
`INPUT_TYPES()` 自动展开成声明，再逐例覆盖要改的值，槽位序与
`tools/check_ui_workflow.py` 同一份判据。

跑法：`python tools/make_llm_examples.py`
产物：`examples/llm-caption-to-moss.json` / `llm-idea-to-storyboard.json` / `llm-wash.json`
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


# ───────────────────────────────────────────── 例 1：反推 → 可用提示词
def ex_caption():
    nodes, links, groups = [], 1, []
    n = note(1, (-700, -260), (580, 620),
             "## 反推：一张图 → 能直接用的提示词\n\n"
             "**① 换图**：把仓库 assets/ 里的 「comic-4panel-lineart-sample.png」 拷进 ComfyUI/input/，再用 LoadImage 选它（LoadImage 只读 input/，不是 assets/）。\n"
             "**② 选模型**：model 下拉是扫 「models/LLM」 + 「models/text_encoders」 得到的；"
             "反推要有视觉能力——主模型 + mmproj，或直接用 VL 一体模型。\n"
             "实测两条都通：MOSS 文本编码器（qwen35）配 Qwen3.5-9B 的 mmproj；"
             "Qwen3-VL-4B 自动配同目录 mmproj。\n"
             "**③ 跑**：输出的 prompt 就是成品提示词，report 是这一跑的证据。\n\n"
             "### 为什么默认关思考\n"
             "思考开着时，模型把内容全写进 「reasoning_content」，正文为空、finish=length，"
             "提示词直接空掉。实测只有 「chat_template_kwargs.enable_thinking」 这一种传法生效。\n\n"
             "### 下一步\n"
             "把 prompt 接进 「CLIPTextEncode」 就能出图；想按同比例重绘（洗图）看第三个示例。")
    img = load_image(2, (-60, -260), (340, 320), "comic-4panel-lineart-sample.png", "① 输入图片")
    sm = smith_node(3, (320, -260), (420, 351), "🖊 提示词工匠 · 反推",
                    {"task": "caption", "dialect": "moss_image", "length": "standard"})
    p1 = preview(4, (800, -260), (460, 420), "prompt（成品提示词）")
    p2 = preview(5, (800, 200), (460, 300), "report（模型/显存/tokens/省略项）")
    links = []
    lid = wire(1, img, 0, sm, sm.slot("image"), "IMAGE", links)
    wire(lid, sm, 0, p1, 0, "STRING", links)
    lid += 1
    wire(lid, sm, 1, p2, 0, "STRING", links)
    groups = [group(1, "反推 → 提示词", (-700, -320), (2000, 880))]
    return finish(nodes + [n, img, sm, p1, p2], links, groups, "llm-caption-to-moss")


# ───────────────────────────────────────────── 例 2：想法 → 宫格稿 → 出图
def ex_storyboard():
    nodes, links = [], []
    n = note(1, (-760, -300), (600, 760),
             "## 一句话想法 → 4 格故事板 → 出图\n\n"
             "**① 写想法**：填 「text」（要固定的字/数/色/位置写进 「must_keep」，别混在想法里）。\n"
             "**② 出宫格稿**：dialect 选 「moss_storyboard」，panels 与 ③ 的格数**必须相等**"
             "（不等会在 report 里点名，不静默改）。\n"
             "**③ 出图**：稿子直接接进 Sampler 的 positive。\n\n"
             "### 格数改两处\n"
             "②的 panels 和 ③ 的 panels 一起改（3/4/6/8/9）。只改一处 = 段数与格数不符。\n\n"
             "### 采样配方\n"
             "示例按模型卡口径：**50 步 / cfg 3 / euler / sgm_uniform**，LoRA 已旁路。"
             "装了 4-step 蒸馏 LoRA 就按 Ctrl+B 取消旁路并改成 4 步 / cfg 1.0。\n\n"
             "### 全局段与格内景别不能打架\n"
             "全局写「浅景深」却又要求「大远景、环境完整进画」，模型会退回中景。"
             "焦距/机位/景深写在每格，全局只留场景·角色·道具·质感（见 docs/07 §三）。")
    sm = smith_node(2, (0, -300), (420, 351), "🖊 提示词工匠 · 宫格稿",
                    {"task": "expand", "dialect": "moss_storyboard", "panels": 4,
                     "length": "standard", "temperature": 0.7, "text":
                     "雨夜便利店，一个加班的女人买关东煮，店员递袋子时说了句「路上小心」，"
                     "店里的灯把水汽照得很亮",
                     "must_keep": "路上小心；关东煮"})
    unet = NodeBuilder(3, "UNETLoader", (560, 520), (360, 90), "① MOSS-Image")
    unet.decl_inputs([("unet_name", "COMBO", True, UNET[0]), ("weight_dtype", "COMBO", True, UNET[1])])
    unet.decl_outputs([("MODEL", "MODEL")])
    clip = NodeBuilder(4, "CLIPLoader", (560, 660), (360, 110), "① 文本编码器")
    clip.decl_inputs([("clip_name", "COMBO", True, CLIP[0]), ("type", "COMBO", True, CLIP[1]),
                      ("device", "COMBO", True, CLIP[2])])
    clip.decl_outputs([("CLIP", "CLIP")])
    vae = NodeBuilder(5, "VAELoader", (560, 810), (360, 60), "① VAE")
    vae.decl_inputs([("vae_name", "COMBO", True, VAE[0])])
    vae.decl_outputs([("VAE", "VAE")])
    lay = NodeBuilder(6, "MossStoryboardLayout", (560, 910), (380, 420))
    lay.decl_inputs(LAYOUT_DECL)
    lay.decl_outputs([("width", "INT"), ("height", "INT"), ("columns", "INT"), ("rows", "INT"),
                      ("spec", "STRING"), ("layout_json", "STRING"), ("template", "IMAGE"),
                      ("report", "STRING")])
    smp = NodeBuilder(7, "MossStoryboardSampler", (1010, -300), (420, 900))
    smp.decl_inputs([("model", "MODEL", False, None), ("clip", "CLIP", False, None),
                     ("vae", "VAE", False, None)] + SAMPLER_DECL)
    smp.decl_outputs([("grid", "IMAGE"), ("panels", "IMAGE"), ("latent", "LATENT"),
                      ("spec", "STRING"), ("prompt_used", "STRING"), ("report", "STRING"),
                      ("prompts_json", "STRING")])
    save = NodeBuilder(8, "SaveImage", (1500, -300), (380, 320), "⑤ 存整张")
    save.decl_inputs([("images", "IMAGE", False, None), ("filename_prefix", "COMBO", True, "MOSS_llm")])
    tpl = NodeBuilder(9, "PreviewImage", (1010, 660), (300, 260), "模板预览")
    tpl.decl_inputs([("images", "IMAGE", False, None)])

    lid = 1
    lid = wire(lid, unet, 0, smp, 0, "MODEL", links)
    lid = wire(lid, clip, 0, smp, 1, "CLIP", links)
    lid = wire(lid, vae, 0, smp, 2, "VAE", links)
    lid = wire(lid, sm, 0, smp, smp.slot("positive"), "STRING", links)
    lid = wire(lid, lay, 4, smp, smp.slot("spec"), "STRING", links)
    lid = wire(lid, smp, 0, save, 0, "IMAGE", links)
    wire(lid, lay, 6, tpl, 0, "IMAGE", links)
    groups = [{"id": 1, "title": "想法 → 宫格稿 → 出图", "bounding": [-760, -360, 2700, 1400],
               "color": "NO_COLOR", "font_size": 24, "flags": {}}]
    return finish(nodes + [n, sm, unet, clip, vae, lay, smp, save, tpl], links, groups,
                  "llm-idea-to-storyboard")


# ───────────────────────────────────────────── 例 3：洗图（反推 → 改词 → 重绘）
def ex_wash():
    nodes, links = [], []
    n = note(1, (-720, -280), (600, 700),
             "## 洗图 = 反推 + 改词 + 重绘（**不是**保脸）\n\n"
             "**⓪ 图**：同第一个示例 —— 先把 assets/comic-4panel-lineart-sample.png 拷进 ComfyUI/input/。\n\n"
             "**① 反推**：「wash=开」 → 忽略压缩噪点/水印/UI 杂项，并把省略了什么列进 "
             "「omitted_artifacts」（不静默省略）。\n"
             "**② 改词**：prompt 是纯文本，直接改——删掉不想要的、加上要换的。\n"
             "**③ 重绘**：按**原图同比例**开一张新图。尺寸照 report 里的 「aspect_ratio」 取"
             "（MOSS 尺寸必须是 16 的倍数）。\n\n"
             "### 能力边界（必须先知道）\n"
             "洗图只有文字约束、没有像素约束 ⇒ **身份与构图会漂，这是机制不是提示词没写好**"
             "（docs/07 实测：vision-only 参考图保持不住其它格，跨格一致性只有像素复制才算强约束）。"
             "要保脸走 FaceSwap 或编辑链，别指望文本重绘。\n\n"
             "### 想更稳\n"
             "「two_pass=开」：先结构化盘点再写散文（两次调用同一个侧车，慢一点、稳一点）。")
    img = load_image(2, (-60, -280), (340, 320), "comic-4panel-lineart-sample.png", "① 要洗的图")
    sm = smith_node(3, (340, -280), (420, 351), "🖊 提示词工匠 · 洗图档",
                    {"task": "caption", "dialect": "moss_image", "wash": True, "two_pass": True,
                     "length": "standard"})
    p1 = preview(4, (820, -280), (460, 460), "prompt（拿去改）")
    p2 = preview(5, (820, 220), (460, 320), "report（含 aspect_ratio 与省略清单）")
    lid = wire(1, img, 0, sm, sm.slot("image"), "IMAGE", links)
    lid = wire(lid, sm, 0, p1, 0, "STRING", links)
    lid += 1
    wire(lid, sm, 1, p2, 0, "STRING", links)
    groups = [{"id": 1, "title": "洗图三步", "bounding": [-720, -340, 2020, 940],
               "color": "NO_COLOR", "font_size": 24, "flags": {}}]
    return finish(nodes + [n, img, sm, p1, p2], links, groups, "llm-wash")


def main():
    if COMFY not in sys.path:
        sys.path.insert(0, COMFY)
    load_pkg()
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, fn in (("llm-caption-to-moss", ex_caption),
                     ("llm-idea-to-storyboard", ex_storyboard),
                     ("llm-wash", ex_wash)):
        wf = fn()
        path = os.path.join(OUT_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(wf, fh, ensure_ascii=False, indent=1)
        sm = [x for x in wf["nodes"] if x["type"] == "MossPromptSmith"][0]
        print("已写 %-28s 节点 %d / 连线 %d / Smith widgets=%d"
              % (name + ".json", len(wf["nodes"]), len(wf["links"]), len(sm["widgets_values"])))


if __name__ == "__main__":
    main()
