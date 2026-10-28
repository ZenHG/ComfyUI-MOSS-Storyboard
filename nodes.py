# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""V1 节点契约（本包唯一真相源，V3 外壳由 `v3/_compat.py` 从这里生成）。

六个节点：
- `MossLayoutEditor`：画布式布局编辑器，state 编译成提示词/几何（零模型）。
- `MossStoryboardLayout`：算宫格几何、生成 MOSS 原生布局 JSON、出模板图。零模型。
- `MossStoryboardSampler`：一步到位——布局 + 采样 + 复绘宫格线 + 裁格。
- `MossStoryboardCrop`：只裁格（给想自己接 KSampler 的人）。
- `MossStoryboardPaste`：把改好的单格按 spec 坐标贴回整张宫格。
- `MossPromptCompiler`：官方 prompt-enhancer 草稿 → MOSS 提示词 / state（零模型）。
"""

import functools
import inspect
import json
import os

from .core import editor as E
from .core import faceswap as FS
from .core import intake as IN
from .core import layout as L
from .core import pixels as P
from .core import prompt as T
from .core import refimage as RI
from .core import skill_draft as S

CATEGORY = "moss/storyboard"

_INIT_MODES = ["auto", "layout", "plain"]

#: 生成方式：整张（一次采样）vs 逐格（每格独立采样后拼版）。
#: 后者慢 N 倍，但跨格角色一致、每格可给足分辨率（实测结论，见 docs/07）。
_GEN_MODES = ["整张", "逐格"]
_ASPECT_KEYS = list(L.ASPECTS)
_CROP_ASPECTS = ["keep", "1:1", "16:9", "4:3", "9:16", "21:9"]

#: Crop / Paste / Sampler 三处 `shave` 的同一个上限。
#: 必须一致：裁的时候内缩多少，贴回就得用同一个数；上限不同会让「按 UI 能选到的最大值
#: 裁、再贴回」变成一次静默重采样。上限只能升不能降 —— 降了会拒绝既有合法工作流。
_SHAVE_MAX = 64

#: 调度器清单的**冻结回落值**（宿主取不到时才用）。
#: 只作兜底：真值来自宿主 `comfy.samplers.SCHEDULER_NAMES`，见 `_scheduler_names()`。
_FALLBACK_SCHEDULERS = ("simple", "normal", "beta", "sgm_uniform", "karras",
                        "exponential", "ddim_uniform", "linear_quadratic", "kl_optimal")


def _scheduler_names():
    """调度器下拉**从宿主取**，不自己维护一份。

    🔴 为什么（2026-10-26 实测「调度器比 comfyui 支持的少」）：宿主会加新调度器
    （`linear_quadratic` / `kl_optimal` 就是后加的），本包硬编码一份就等于
    「宿主能跑的这里选不到」。判据与被测对象同源，才不会再次漏。
    宿主不可导入（单测 / 纯离线）时回落到冻结清单。
    """
    names = []
    try:
        import comfy.samplers as _cs
        names = [str(n) for n in (getattr(_cs, "SCHEDULER_NAMES", None) or [])]
    except Exception:
        names = []
    if not names or "sgm_uniform" not in names:
        # 默认值不在表里 ⇒ 提交的是非法值，宁可用冻结清单也不要静默坏掉
        names = list(_FALLBACK_SCHEDULERS)
    return names


# MOSS-Image 训练区间 1–2MP，官方声明外推到 ~4MP。超了只警告，不拦。
_MP_COMFORT = 2.0
_MP_LIMIT = 4.0

# 单格有效像素的阈值。故事板每格只分到这么多 ⇒ 低于 0.15MP 明显糊，
# 低于 0.08MP 很糊（MOSS 训练在 1–2MP，整张 4 格时每格约 0.15MP 是"及格线"）。
_PM_PANEL_OK = 0.15
_PM_PANEL_LOW = 0.08


# ---------------------------------------------------------------------------
# 节点层异常上下文：任何异常都要能回答"哪个节点、哪组参数、原始错误是什么"
# ---------------------------------------------------------------------------
_THIS_FILE = os.path.abspath(__file__)


def _val(v):
    if v is None or isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, str):
        return v if len(v) <= 48 else v[:48] + "…"
    return "<%s>" % type(v).__name__


def _origin_is_self(exc):
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    return os.path.abspath(tb.tb_frame.f_code.co_filename) == _THIS_FILE


def _node_errors(*keys):
    """异常时补「节点名 + 关键入参实值」，并原样保留原始错误全文。

    本节点自己 raise 的中文错误（最深帧在本文件）直接放行——那些文案已经带着
    操作指引，再包一层只会把有用信息变成噪音。
    """

    def deco(fn):
        try:
            defaults = {k: p.default for k, p in inspect.signature(fn).parameters.items()
                        if k != "self" and p.default is not inspect.Parameter.empty}
        except (TypeError, ValueError):
            defaults = {}

        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as exc:
                if _origin_is_self(exc):
                    raise
                try:
                    bound = inspect.signature(fn).bind_partial(self, *args, **kwargs).arguments
                except Exception:
                    bound = {}
                vals = []
                for k in keys:
                    if k in bound:
                        vals.append("%s=%s" % (k, _val(bound[k])))
                    elif k in defaults:
                        vals.append("%s=%s" % (k, _val(defaults[k])))
                raise RuntimeError(
                    "【%s】处理失败：入参 %s。\n原因：%s\n（原始异常：%s）"
                    % (type(self).__name__, "、".join(vals) or "无", exc, type(exc).__name__)
                ) from exc

        return wrapper

    return deco


def _mp_note(layout, per_panel=False, sample_px=(0, 0)):
    """分辨率告警：只按**真正进模型的那张画布**判。

    整张 = 宫格画布（画布越大每格越小，单格 MP 才是瓶颈；2026-10-24 修正前只看画布 MP，
    漏掉了这个——画布看着正常、每格照样糊）。
    逐格 = 单格采样画布 `sample_px`（由 `sample_edge` 决定）。拼版后的整张从不进模型 ⇒
    拿它的 MP 去比官方上限是假红，告警也就不能建议"改逐格"（已经在逐格里了）。
    """
    mp = layout.megapixels
    pm = layout.panel_megapixels
    if per_panel:
        sw, sh = sample_px
        sm = sw * sh / 1e6
        if sm > _MP_LIMIT:
            return ("\n⚠ 单格采样画布 %.2fMP（%d×%d）超出官方外推上限（≈4MP）：文字与细节退化。"
                    "把 `sample_edge` 调小。" % (sm, sw, sh))
        if sm < _PM_PANEL_LOW:
            return ("\n⚠ 单格采样画布只有 %.2fMP（%d×%d）：低于 MOSS 的训练区间，每格会明显糊。\n"
                    "  逐格的清晰度只由 `sample_edge` 决定（与格数、`panel_edge` 无关），加大它。"
                    % (sm, sw, sh))
        if sm < _PM_PANEL_OK:
            return ("\n· 单格采样画布 %.2fMP（%d×%d）：偏小，细节会偏软。可加大 `sample_edge`。"
                    % (sm, sw, sh))
        return ""
    out = ""
    if mp > _MP_LIMIT:
        out += ("\n⚠ 画布 %.2fMP 已超出 MOSS-Image 官方外推上限（≈4MP）：文字与布局退化风险高。"
                "把 panel_edge 调小，或改用「逐格」模式。" % mp)
    elif mp > _MP_COMFORT:
        out += ("\n⚠ 画布 %.2fMP 高于训练区间（1–2MP）：可跑，但小字与细节开始不稳。"
                "建议 panel_edge 降到能压进 2MP 的值。" % mp)
    if pm < _PM_PANEL_LOW:
        out += ("\n⚠ 单格只有 %.2fMP（%d×%d）：**这才是画质瓶颈** —— 远低于 MOSS 的训练区间，"
                "每格会明显糊（画布 MP 看着正常也一样糊）。\n"
                "  解法：① 加大 `panel_edge`（单格清晰度的直接杠杆）；"
                "② 改用「逐格」模式（每格独立采样，分辨率交给 `sample_edge`）；"
                "③ 减少格数（整张画布有官方 MP 上限，格数少了才腾得出 panel_edge）。"
                % (pm, layout.panel_w, layout.panel_h))
    elif pm < _PM_PANEL_OK:
        out += ("\n· 单格 %.2fMP（%d×%d）：偏小，细节会偏软。加大 `panel_edge` 或改「逐格」。"
                % (pm, layout.panel_w, layout.panel_h))
    return out


def _resolve_layout(panels, panel_aspect, panel_edge, canvas_aspect, gutter, margin, columns=0, rows=0):
    return L.build_layout(panels=panels, cols=columns, rows=rows, gutter=gutter, margin=margin,
                          panel_edge=panel_edge, panel_aspect=panel_aspect,
                          canvas_aspect=canvas_aspect)


def _prompt_for_layout(text, lay, label="object", char_boxes=""):
    """把提示词稿转成布局 JSON 文本，返回 `(最终提示词, 摘要行)`。"""
    head, caps = T.split_panels(text)
    if not caps:
        return (head or "").strip(), "未找到 [PANEL n] 标记 ⇒ 按普通文生图处理（无布局约束）"
    pairs, notes = T.pair_captions(caps, lay.cells)
    out = T.layout_json_text(head, pairs, lay.boxes_abs_centered(), label=label,
                             char_boxes=char_boxes)
    info = "解析 %d 段面板描述，%d 段带框" % (len(caps), len(pairs))
    if (char_boxes or "").strip():
        info += "；人物格 %s（编号从 1 起）" % char_boxes.strip()
    if notes:
        info += "；" + "；".join(notes)
    return out, info


class MossStoryboardLayout:
    """宫格几何 + 布局提示词 + 模板图（零模型，可单独用）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {
                    "multiline": True, "default": "", "dynamicPrompts": False,
                    "tooltip": "【填什么】故事板稿：开头一段全局描述，然后每格一段用 [PANEL 1] … [PANEL n] 分开。\n"
                               "段数可以少于格数（多余格不约束），也可以多于格数（尾部丢弃）。\n"
                               "⚠ 没有 [PANEL n] 标记时只输出普通提示词；模板只是预览，不再参与采样。",
                }),
                "panels": ("INT", {
                    "default": 8, "min": 1, "max": 12, "step": 1,
                    "tooltip": "【填什么】一共几格。常用 3 / 4 / 6 / 8 / 9；其它值会自动做整数分解。",
                }),
                "panel_aspect": (_ASPECT_KEYS, {
                    "default": "16:9",
                    "tooltip": "【填什么】单格宽高比。产线做横屏短剧选 16:9；竖屏选 9:16。",
                }),
                "panel_edge": ("INT", {
                    "default": 512, "min": 128, "max": 2048, "step": 16,
                    "tooltip": "【填什么】单格长边像素。8 格推荐 512（整图约 1.3MP，落在训练区间内）。\n"
                               "⚠ 调大会让整图离开 1–2MP 训练区间，小字先崩。",
                }),
                "canvas_aspect": (L.CANVAS_ASPECTS, {
                    "default": "auto",
                    "tooltip": "【填什么】整张画布的宽高比目标。auto = 按单格朝向取 16:9 或 9:16。\n"
                               "用途：同样 6 格，选 9:16 会排成 2 列 3 行，选 16:9 会排成 3 列 2 行。",
                }),
                "columns": ("INT", {
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "【可留 0】0 = 自动按 canvas_aspect 选。填了就强制列数（行数自动补齐）。",
                }),
                "rows": ("INT", {
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "【可留 0】0 = 自动。columns 与 rows 都填时必须满足 列×行 = 格数。",
                }),
                "gutter": ("INT", {
                    "default": 8, "min": 0, "max": 64, "step": 2,
                    "tooltip": "【填什么】格与格之间的缝宽（像素）。0 = 无内缝，模型会跨格连画。",
                }),
                "margin": ("INT", {
                    "default": 8, "min": 0, "max": 64, "step": 2,
                    "tooltip": "【填什么】画布最外圈留白。0 = 内容直接到边，裁格时容易带进毛边。",
                }),
                "cell_gray": ("INT", {
                    "default": 25, "min": 0, "max": 255, "step": 1,
                    "tooltip": "【进阶】模板里单格填充的灰度。近黑（25）最稳；中灰会让成片整体发灰。",
                }),
                "invert": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "【进阶】反相模板：黑底白缝变成白底黑缝。仅在模型不认白缝时试。",
                }),
                "label": (list(T.LABELS), {
                    "default": "object",
                    "tooltip": "【进阶】布局框的类型。object = 一格当一块画面；character = 强调该格是人物。",
                }),
            },
            "optional": {
                "char_boxes": ("STRING", {
                    "default": "",
                    "tooltip": "【可留空】把哪几格标成人物框，逗号分隔、**编号从 1 起**（如 `1, 3`）。\n"
                               "留空 = 全部用上面的 label。用于同一张里既有主角格又有空镜的场合。",
                }),
            },
        }

    RETURN_TYPES = ("INT", "INT", "INT", "INT", "STRING", "STRING", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("width", "height", "columns", "rows", "spec", "layout_json", "template",
                    "report", "elements")
    FUNCTION = "build"
    CATEGORY = CATEGORY
    DESCRIPTION = "算 3/4/6/8/9 宫格的尺寸与每格坐标，输出布局提示词与宫格模板图。不加载模型，秒出。"
    SEARCH_ALIASES = ["storyboard", "grid", "宫格", "故事板", "分镜", "panel layout", "moss layout"]

    @_node_errors("panels", "panel_aspect", "panel_edge")
    def build(self, prompt, panels, panel_aspect, panel_edge, canvas_aspect, columns, rows,
              gutter, margin, cell_gray, invert, label, char_boxes=""):
        lay = _resolve_layout(panels, panel_aspect, panel_edge, canvas_aspect, gutter, margin, columns, rows)
        text, info = _prompt_for_layout(prompt, lay, label, char_boxes)
        template = P.render_template(lay, cell_gray=cell_gray, invert=invert)

        report = "\n".join([
            "布局 %d 格 = %d 列 × %d 行（%s）" % (lay.panels, lay.cols, lay.rows, lay.note or "手工指定"),
            "画布 %d × %d（%.2fMP，对齐余量 右%d/下%d）" % (lay.width, lay.height, lay.megapixels,
                                                    lay.pad_x, lay.pad_y),
            "单格 %d × %d（%.2fMP/格）｜缝 %dpx｜外边距 %dpx"
            % (lay.panel_w, lay.panel_h, lay.panel_megapixels, lay.gutter, lay.margin),
            "提示词：%s" % info,
            "布局文本 %d 字%s" % (len(text), _mp_note(lay)),
        ])
        return (lay.width, lay.height, lay.cols, lay.rows, lay.spec(), text, template, report,
                _elements_json(lay, prompt))


def _elements_json(lay, prompt):
    """把宫格转成官方 `Create Bounding Boxes` / `MossImagePromptBuilderKJ` 能吃的元素格式。

    格式：`{"type": "obj", "desc": "...", "bbox": [y0, x0, y1, x1]}`，bbox 是 **0–1000 归一化**。
    用户可以把它接给官方的画布节点做可视化微调，再把调好的结果喂回来。

    ⚠ 与我们的 `layout_json` 不是同一套坐标：那套是绝对像素 + 画布中心原点，
    这套是 0–1000 归一化 + 左上角原点（官方 `elements_to_boxes` 的规定）。
    """
    head, caps = T.split_panels(prompt)
    descs = [c[1] for c in caps] if caps else []
    boxes = lay.boxes_1000()
    items = []
    for i, bx in enumerate(boxes):
        desc = descs[i] if i < len(descs) else ""
        items.append({"type": "obj", "desc": desc, "bbox": bx})
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _blank_image(width, height, gray=25):
    """单张模式的模板占位图。宫格有 `render_template`，单张没有格子可画。"""
    import torch
    level = max(0.0, min(1.0, float(gray) / 255.0))
    return torch.full((1, int(height), int(width), 3), level)


class MossLayoutEditor:
    """布局编辑器：把编辑器 state 编译成提示词与几何。零模型。

    两种模式共用一套 state：`preset=grid` 出宫格稿（`[STYLE]` + `[PANEL n]`），
    其他预设出单张多元素 layout JSON。state 结构见 `docs/10-contract.md`。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "editor_state": ("STRING", {
                    "default": json.dumps(E.empty_state(), ensure_ascii=False),
                    "multiline": False, "dynamicPrompts": False,
                    "tooltip": "【不用手填】编辑器的存档：整份布局状态序列化成的 JSON。\n"
                               "在节点里用画布改，这里会自动更新。\n"
                               "手改也行，但字段必须符合 v3 结构，否则会报错。",
                }),
            },
            "optional": {
                "prompt": ("STRING", {
                    "default": "", "forceInput": True,
                    "tooltip": "【接线，可选】外接故事板稿：[STYLE] 全局描述 + 每格 [PANEL n] 描述。\n"
                               "接上就接管逐格描述（编辑器里的描述框变只读，预设文案会被覆盖），断开恢复手编。\n"
                               "画布、格数、每格坐标仍来自编辑器——外接稿只改文字。\n"
                               "段首写景别（如「中景，…」）会用外接的；没写则该格不注入景别。\n"
                               "段数不足时后几格沿用编辑器描述；多出的段被丢弃，都写进 report，不静默。",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "INT", "STRING", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "width", "height", "spec", "template", "report", "negative")
    FUNCTION = "build"
    CATEGORY = CATEGORY
    DESCRIPTION = ("在画布上摆元素（人物 / 物体 / 文字），一键出 MOSS 布局提示词。\n"
                   "宫格模式出故事板稿；单张模式出海报 / 封面 / 概念图的多元素布局。")
    SEARCH_ALIASES = ["layout editor", "布局编辑", "可视化编辑", "海报", "poster",
                      "storyboard", "故事板", "分镜", "封面"]

    @_node_errors("editor_state")
    def build(self, editor_state, prompt=""):
        state = E.parse_state(editor_state)
        notes = []
        if (prompt or "").strip():
            state, notes = IN.apply_to_state(state, prompt)
        width, height = E.canvas_size(state)

        if E.is_grid(state):
            lay = E.geometry(state)
            prompt_text = E.compile_prompt(state)
            spec = lay.spec()
            template = P.render_template(lay)
        else:
            prompt_text = E.layout_json(state)
            # 单张模式也出 spec（panels=1）。**这是「选了什么预设」与「出几张」之间的契约**：
            # 以前这里给空串，接到 Sampler 上就等于「这根线等于没接」，Sampler 静默回退到
            # 自己的 panels=4 —— 编辑器选的是海报，出来还是四宫格，谁也说不清为什么。
            # 1 格宫格的画布就是编辑器画布本身，缝与边距都是 0，不会多画一圈线。
            spec = L.build_layout(1, cols=1, rows=1, panel_w=width, panel_h=height,
                                  gutter=0, margin=0).spec()
            template = _blank_image(width, height)

        rep = E.report(state)
        if notes:
            rep = "\n".join([rep] + ["· " + n for n in notes])
        # `negative` 只在「单张 + 融合」时非空：那几句反拼贴词不该让用户自己抄。
        return (prompt_text, width, height, spec, template, rep, E.negative(state))


class MossStoryboardSampler:
    """一步出故事板：布局约束（可选）+ 采样 + 复绘宫格线 + 裁格。起点恒为空 latent（不做图生图）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "【接线】UNETLoader 输出的 MOSS-Image 模型（可先过 LoRA）。"}),
                "clip": ("CLIP", {"tooltip": "【接线】CLIPLoader，type 必须是 moss_image。\n"
                                             "⚠ 用错 type 会静默拿到错误嵌入，画面语义跑飞。"}),
                "vae": ("VAE", {"tooltip": "【接线】FLUX.2 VAE（moss 的 latent 空间就是它）。\n"
                                           "解码精度由你用的 VAE 加载器决定，本节点不干预。\n"
                                           "⚠ 精度跑成 fp32 会让宿主把解码显存预估放大到 4 倍（不报错），\n"
                                           "可能因此触发分块解码。要省显存请用 fp16 / bf16。"}),
                "positive": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "【填什么】故事板稿：[STYLE] 全局描述 + 每格 [PANEL n]。\n"
                               "init=layout 时会自动转成 MOSS 布局 JSON 再编码。",
                }),
                "negative": ("STRING", {
                    "multiline": True, "default": "",
                    # 「高级」只表示可选，不代表无效：模型卡默认 cfg=3（>1），
                    # 填了就会进负面分支；留空则就是空条件。
                    "advanced": True,
                    "tooltip": "【可选，收在「高级」里】负面提示词。默认留空。\n"
                               "MOSS-Image-SFT 模型卡默认 cfg=3 > 1 ⇒ 填了会进计算图；\n"
                               "只有 cfg=1.0 时宿主才跳过负面分支（comfy/samplers.py 的 cfg1 优化）。",
                }),
                "steps": ("INT", {
                    "default": 50, "min": 1, "max": 200, "step": 1,
                    "tooltip": "【填什么】采样步数。默认 50 = MOSS-Image-SFT 模型卡口径\n"
                               "（50 步 / cfg 3 / euler / shift 5 / SLG 负分支层 22）。\n"
                               "· 官方 Base 模型卡：同样 50 步 / cfg 3 / euler（CFG 范围 3–6）；\n"
                               "· 叠加 4 步蒸馏 LoRA：4–8 步 / cfg 1.0 —— 调高会破坏蒸馏效果。",
                }),
                "cfg": ("FLOAT", {
                    "default": 3.0, "min": 0.0, "max": 30.0, "step": 0.1,
                    "tooltip": "【填什么】引导强度。默认 3.0 = MOSS-Image-SFT 模型卡口径。\n"
                               "· 官方 Base 模型卡：cfg 3（官方给的适用范围 3–6）；\n"
                               "· 叠加 4 步蒸馏 LoRA：1.0。\n"
                               "⚠ 1.0 时宿主跳过负面分支 ⇒ 负面提示词填了也不生效。",
                }),
                "sampler_name": (["euler", "euler_ancestral", "heun", "dpmpp_2m", "dpmpp_2m_sde",
                                  "dpmpp_3m_sde", "res_multistep", "er_sde", "lms"], {
                    "default": "euler", "tooltip": "【填什么】默认 euler = MOSS-Image-SFT 模型卡口径。\n"
                                                    "ComfyUI 原生模板另用 er_sde / 25 步（Comfy-Org 的选择）。"}),
                "scheduler": (_scheduler_names(), {
                    "default": "sgm_uniform", "tooltip": "【填什么】默认 sgm_uniform = OpenMOSS 官方\n"
                                                         "ComfyUI 插件工作流的配对；模型卡未指定 scheduler。\n"
                                                         "清单与宿主同源（comfy.samplers.SCHEDULER_NAMES）："
                                                         "宿主装了新调度器这里自动出现。"}),
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xFFFFFFFF, "step": 1,
                    "tooltip": "【填什么】随机种。同种子同参数才可复现。",
                }),
                "denoise": ("FLOAT", {
                    "default": 1.0, "min": 0.05, "max": 1.0, "step": 0.01,
                    "tooltip": "【留 1.0】降噪强度。1.0 = 从纯噪声开始完整生成，这是本包的正常用法。\n"
                               "⚠ 这里没有图生图：调 <1 不会参考任何图，只会让宿主按 int(步数/降噪) 截断 sigma，\n"
                               "实际效果就是少跑几步（想省时间请直接调 steps，更直观）。",
                }),
            },
            "optional": {
                "spec": ("STRING", {
                    "default": "", "forceInput": True,
                    "tooltip": "【接线】接 MossStoryboardLayout 的 spec。接了就用它的几何，下面几何参数被忽略。",
                }),
                "panels": ("INT", {
                    "default": 8, "min": 1, "max": 12, "step": 1,
                    "tooltip": "【未接 spec 时生效】格数。",
                }),
                "panel_aspect": (_ASPECT_KEYS, {
                    "default": "16:9", "tooltip": "【未接 spec 时生效】单格宽高比。",
                }),
                "panel_edge": ("INT", {
                    "default": 512, "min": 128, "max": 2048, "step": 16,
                    "tooltip": "【未接 spec 时生效】单格长边像素。",
                }),
                "canvas_aspect": (L.CANVAS_ASPECTS, {
                    "default": "auto", "tooltip": "【未接 spec 时生效】画布宽高比目标。",
                }),
                "gutter": ("INT", {
                    "default": 8, "min": 0, "max": 64, "step": 2, "tooltip": "【未接 spec 时生效】缝宽。",
                }),
                "margin": ("INT", {
                    "default": 8, "min": 0, "max": 64, "step": 2, "tooltip": "【未接 spec 时生效】外边距。",
                }),
                "init": (_INIT_MODES, {
                    "default": "auto",
                    "tooltip": "【填什么】怎么送提示词：\n"
                               "auto（默认）= 把每格描述转成布局框再送，纯生成；\n"
                               "layout = 同上，显式写法；\n"
                               "plain = 纯提示词，不加布局框：整张=整段原样送（等于普通 txt2img）；\n"
                               "        逐格=全局段（去掉多格措辞）+ 本格描述。\n"
                               "⚠ auto/layout 会先对账：`[PANEL n]` 段数与全局段「N 个面板」\n"
                               "必须等于实际格数，不等直接报错（不自动补段）；要绕过选 plain。\n"
                               "⚠ 本包不做图生图：起点恒为空 latent。实测模板 latent 起点\n"
                               "（35 步/0.85 纯黑，12/0.6 与 4/0.5 残影）在 MOSS 上不成立，\n"
                               "所以这条通路已移除。想改已有图见 README「只改一格」。",
                }),
                "label": (list(T.LABELS), {
                    "default": "object", "tooltip": "【进阶】布局框类型，同 Layout 节点。",
                }),
                "reborder": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "【建议开】解码后按几何坐标重画笔直的宫格线。\n"
                               "关掉就只能指望模型自己把线画直——实测常歪、常有断口。",
                }),
                "border_px": ("INT", {
                    "default": 0, "min": 0, "max": 64, "step": 1,
                    "tooltip": "【可留 0】复绘线宽。0 = 与缝宽一致（铺满整条缝）。",
                }),
                "shave": ("INT", {
                    "default": 0, "min": 0, "max": _SHAVE_MAX, "step": 1,
                    "tooltip": "【可留 0】裁格时每格四边内缩像素。线已复绘在缝里，正常留 0。",
                }),
                # ⚠ 新增 widget 只能追加在末尾：`widgets_values` 按位置存。
                # `mode` / `sample_edge` 是 0.2.0 新增的，必须排在旧参数之后，
                # 否则 0.1.x 存的工作流会整体错位（实测：mode=4、panels="16:9"）。
                "mode": (_GEN_MODES, {
                    "default": "整张",
                    "tooltip": "【填什么】怎么生成：\n"
                               "整张（默认）= 一次采样出整张宫格，快；跨格是「同一张脸」更稳。\n"
                               "逐格 = 每格独立采样再拼版，慢 N 倍；每格可给 4 倍像素，\n"
                               "        服装 / 道具 / 文字更清楚，但每格各画一张脸。\n"
                               "⚠ 别按「谁更一致」选：同一女性 ArcFace 实测整张 0.553 > 逐格 0.479。\n"
                               "一次成型用整张，要逐格返工用逐格。",
                }),
                "sample_edge": ("INT", {
                    "default": 1024, "min": 256, "max": 4096, "step": 16,
                    "tooltip": "【填什么】逐格模式下，**每格采样时**的画布长边像素。\n"
                               "1024 = 每格按 1024×N 采样，缩回最终格子后更清晰。\n"
                               "⚠ 只在「逐格」模式生效；「整张」模式忽略它。\n"
                               "本机实测（4 格 / 16:9）：496 → 偏糊，992 → 最优，1984 → 反而变松。\n"
                               "代价：显存与耗时按像素平方涨。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "LATENT", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("grid", "panels", "latent", "spec", "prompt_used", "report", "prompts_json")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = "一次生成 3/4/6/8/9 宫格故事板，并裁出逐格。\n每格画什么由 MOSS 原生布局能力约束（官方标为实验特性，不保证每格都照办）。"
    SEARCH_ALIASES = ["storyboard", "grid", "宫格", "故事板", "分镜", "moss storyboard", "生成故事板"]

    @_node_errors("panels", "steps", "cfg", "denoise")
    def run(self, model, clip, vae, positive, negative, steps, cfg, sampler_name, scheduler,
            seed, denoise, spec="", panels=8, panel_aspect="16:9", panel_edge=512,
            canvas_aspect="auto", gutter=8, margin=8, init="auto", label="object",
            reborder=True, border_px=0, shave=0, mode="整张", sample_edge=1024):
        from .core import sampling as S

        S.probe_host_api()   # 宿主内核指纹：版本不匹配时给出"改了什么"，而不是 AttributeError

        spec_given = bool(spec and str(spec).strip())
        if spec_given:
            lay = L.parse_spec(spec)
            # 明说"未生效"：静默忽略是最常踩的坑 —— 改了格数却没变化，人只会以为节点坏了。
            geom_src = "spec 接线（本节点的 panels / panel_edge / gutter / margin 未生效）"
        else:
            lay = _resolve_layout(panels, panel_aspect, panel_edge, canvas_aspect, gutter, margin)
            geom_src = "本节点参数"

        notes = []
        init_mode = init if init in _INIT_MODES else "auto"
        if init_mode == "auto":
            init_mode = "layout"

        # `mode` 是「整张 / 逐格」，`init_mode` 是「auto / layout / plain」——
        # 两个都用 mode 会互相覆盖（踩过一次：逐格分支永远进不去）。
        gen_mode = mode if mode in _GEN_MODES else "整张"

        if gen_mode == "逐格":
            return self._run_per_panel(model, clip, vae, positive, negative, steps, cfg,
                                       sampler_name, scheduler, seed, denoise, lay, geom_src,
                                       init_mode=init_mode, label=label, reborder=reborder,
                                       border_px=border_px, shave=shave, sample_edge=sample_edge)

        # 稿子与布局对账：不自动改写/补段。2026-10-27 实测「4 段 + 恰好 4 个面板」
        # 配 8 格画布，模型把 4 个节拍画两遍（上下两条），而 report 里的告警没人看。
        if init_mode == "layout":
            bad = T.board_mismatch(positive, lay.panels)
            if bad:
                msg, implied = bad
                lines = [msg + "。",
                         "当前布局 %d 格 = %d 列 × %d 行，画布 %d × %d。"
                         % (lay.panels, lay.cols, lay.rows, lay.width, lay.height)]
                if implied and implied != lay.panels:
                    who = "上游 Layout 的 panels" if spec_given else "本节点 panels"
                    lines.append("可选：把%s 改成 %d。" % (who, implied))
                lines.append("出路：① 把稿子改到 %d 段并同步全局段计数；"
                             "② init=plain 原样送词（不做布局，也就没有宫格框）。" % lay.panels)
                raise ValueError("\n".join(lines))

        # ---- 提示词：layout 时转成布局 JSON ----
        if init_mode == "layout":
            # 1 格时这条告警没有意义：单张海报的稿子本来就不该声明宫格。
            if lay.panels > 1 and T.grid_declared(positive) is False:
                # 只出声不改写：模型是靠**语义**画宫格的，全局段不说「分成 N 格」就会不按格分配。
                notes.append("⚠ 全局段未声明宫格（没有「恰好 N 个面板」这类说法）⇒ 模型可能不按格"
                             "分配画面；写法参考 examples/moss-storyboard-4panels-ui.json")
            text, info = _prompt_for_layout(positive, lay, label)
            notes.append("布局提示词：%s" % info)
        else:
            text, info = (positive or "").strip(), "原样送提示词（未加布局框）"
            notes.append(info)

        cond_pos = S.encode_text(clip, text)
        cond_neg = S.encode_text(clip, negative or "")

        # ---- 起点 latent：恒为空（本包不做图生图，MOSS 不以参考图条件化） ----
        latent = S.empty_latent(model, lay.width, lay.height)
        notes.append("起点 = 空 latent %d×%d" % (lay.width, lay.height))

        # ---- 采样 ----
        out = S.sample(model, cond_pos, cond_neg, latent, steps=steps, cfg=cfg,
                       sampler_name=sampler_name, scheduler=scheduler, denoise=denoise, seed=seed)
        image = S.decode(vae, out)
        if image.ndim == 3:
            image = image.unsqueeze(0)
        if image.shape[1] != lay.height or image.shape[2] != lay.width:
            notes.append("⚠ 解码尺寸 %dx%d 与布局 %dx%d 不一致（VAE 截断？）⇒ 按解码结果重新裁格"
                         % (image.shape[2], image.shape[1], lay.width, lay.height))
            lay = _match_layout_to_image(lay, image.shape[2], image.shape[1])

        grid = P.composite_borders(image, lay, thickness=(border_px or None)) if reborder else image
        panels_img = P.crop_panels(grid, lay, shave=shave)

        report = "\n".join([
            S.profile_hint(steps, cfg, negative),
            "几何来源：%s" % geom_src,
            "布局 %d 格 = %d 列 × %d 行，画布 %d × %d（%.2fMP）"
            % (lay.panels, lay.cols, lay.rows, lay.width, lay.height, lay.megapixels),
            "单格 %d × %d，缝 %dpx，外边距 %dpx，对齐余量 右%d/下%d"
            % (lay.panel_w, lay.panel_h, lay.gutter, lay.margin, lay.pad_x, lay.pad_y),
            "init=%s｜%d 步｜cfg %.2f｜%s/%s｜种子 %d｜denoise 名义 %.2f / 实际 %.2f"
            % (init_mode, steps, cfg, sampler_name, scheduler, seed, denoise,
               S.effective_denoise(steps, denoise)),
            "输出：grid %s，panels %s" % (
                "×".join(str(v) for v in grid.shape), "×".join(str(v) for v in panels_img.shape)),
        ] + notes) + _mp_note(lay)

        return (grid, panels_img, out, lay.spec(), text, report,
                json.dumps([text], ensure_ascii=False))

    def _run_per_panel(self, model, clip, vae, positive, negative, steps, cfg,
                       sampler_name, scheduler, seed, denoise, lay, geom_src,
                       init_mode="layout", label="object", reborder=True, border_px=0,
                       shave=0, sample_edge=1024):
        """逐格生成：每格独立采样，再按几何坐标拼成宫格。

        返回的 `latent` 是 **N 个单格 latent 的 batch**（`[N,C,h,w]`，行主序），不是拼版。

        为什么值得多花 N 倍时间（2026-10-24 实测）：
        - 整张生成时，4 格里同一角色的帽子颜色、脸型**不统一**（模型把全局描述
          摊薄到 4 个区域，每格各"想象"一遍）；
        - 逐格生成时每格独立完整地读一遍全局描述 ⇒ 角色特征明显统一，
          且每格画布可以给到 4 倍像素 ⇒ 更清晰。

        关键实现点：提示词里必须去掉「恰好 N 个面板」这类多格语义，
        否则模型照样画多格（实测：给 1 个 bbox 也会画出 4 格）。
        """
        from .core import sampling as S
        import torch

        notes = []
        head, caps = T.split_panels(positive)
        if not caps:
            raise ValueError(
                "逐格模式需要 [PANEL n] 分段描述（现在只有全局描述）。\n"
                "写法：先写一段全局风格/角色/环境，然后每格一段 [PANEL 1] … [PANEL n]。")

        # 单格采样画布：长边 = sample_edge（绝对像素）。
        # ⚠ 逐格模式最大的优势就在这里——单格的**采样**分辨率不受整张宫格尺寸约束，
        #   可以比最终格子大几倍，采样完再缩小 ⇒ 每格更清晰。
        #   绝对像素而不是"倍数"：与宿主 `EmptyFlux2LatentImage` 的心智一致，
        #   且「画质需求」与「排版需求」互不绑架（倍数会让后者决定前者）。
        #   不小于最终格子：否则是插值放大，白费算力还更糊。
        edge = max(int(sample_edge), lay.panel_w, lay.panel_h)
        cell_ratio = lay.panel_w / float(lay.panel_h)
        if cell_ratio >= 1:
            pw = int(round(edge / 16.0)) * 16
            ph = max(16, int(round(pw / cell_ratio / 16.0)) * 16)
        else:
            ph = int(round(edge / 16.0)) * 16
            pw = max(16, int(round(ph * cell_ratio / 16.0)) * 16)
        if (pw, ph) == (lay.panel_w, lay.panel_h):
            notes.append("采样画布与最终格子同尺寸（%d×%d）：不会更清晰。" % (pw, ph))

        pairs, pair_notes = T.pair_captions(caps, lay.cells)
        notes.extend(pair_notes)
        if len(pairs) < lay.panels:
            raise ValueError(
                "逐格模式要求每格都有一句描述：提示词只有 %d 段，布局有 %d 格。\n"
                "补足 [PANEL 1]..[PANEL %d]，或把格数改小；逐格模式不会用空白格凑数。"
                % (len(pairs), lay.panels, lay.panels))

        # 负面提示词与每格无关：编码一次，所有格复用。
        # 9B 文本编码器编 N 次纯属浪费（8 格就多 7 次前向）。
        cond_neg = S.encode_text(clip, negative or "")

        # 逐格采样
        shots = []
        latents = []
        sent = []
        for i, (caption, _cell) in enumerate(pairs):
            text = T.panel_prompt(head, caption, pw, ph, init_mode=init_mode, label=label)
            sent.append(text)
            cond_pos = S.encode_text(clip, text)
            latent = S.empty_latent(model, pw, ph)
            out_i = S.sample(model, cond_pos, cond_neg, latent, steps=steps, cfg=cfg,
                             sampler_name=sampler_name, scheduler=scheduler,
                             denoise=denoise, seed=seed + i)
            latents.append(out_i)
            img = S.decode(vae, out_i)
            if img.ndim == 3:
                img = img.unsqueeze(0)
            shots.append(img[0])
            notes.append("格 %d：%d×%d，种子 %d" % (i + 1, pw, ph, seed + i))

        shots_t = torch.stack(shots, dim=0)

        # ⚠ 缩放顺序很重要：**先每格缩到最终格子尺寸，再拼版**。
        #   反过来（先拼成大图再整张缩小）会让宫格线也一起被缩放变细，
        #   而且多一次全图重采样。
        if (pw, ph) != (lay.panel_w, lay.panel_h):
            # ⚠ 不能用 lanczos+antialias：PyTorch 的抗锯齿下采样只对 float32 实现，
            #   fp16 + CUDA 会抛 NotImplementedError("compute_index_ranges_weights")。
            #   `area` 对下采样是正确选择（面积平均），且全精度可用。
            shots_t = torch.nn.functional.interpolate(
                shots_t.permute(0, 3, 1, 2), size=(lay.panel_h, lay.panel_w),
                mode="area").permute(0, 2, 3, 1)

        # 拼版目标：最终布局的画布（单格 = 布局单格尺寸）。
        # 宫格线由 _assemble_per_panel 统一处理：只拼版，再按 reborder 决定是否复绘。
        grid, panels_img = _assemble_per_panel(
            shots_t, lay, reborder=reborder, border_px=border_px, shave=shave)

        # latent 每格各一个，拼成 batch 而不是拼版：拼版没有画质意义，拼了还得再切回来。
        # 回的是 LATENT 字典，宿主 VAEDecode 取的是 samples["samples"]。
        latent_batch = S.stack_latents(latents)

        report = "\n".join([
            S.profile_hint(steps, cfg, negative),
            "latent：%d 个单格 latent 的 batch（行主序 = 格号 1..%d，**不是拼版**）%s"
            % (lay.panels, lay.panels,
               "×".join(str(v) for v in latent_batch["samples"].shape)),
            "几何来源：%s" % geom_src,
            "逐格生成：%d 格各自独立采样后拼版" % lay.panels,
            "单格画布 %d × %d（%.2fMP）｜整张 %d × %d（%.2fMP）"
            % (pw, ph, pw * ph / 1e6, lay.width, lay.height, lay.megapixels),
            "布局 %d 格 = %d 列 × %d 行，单格 %d × %d，缝 %dpx，外边距 %dpx"
            % (lay.panels, lay.cols, lay.rows, lay.panel_w, lay.panel_h, lay.gutter, lay.margin),
            "%d 步｜cfg %.2f｜%s/%s｜种子 %d（每格 +格号）｜denoise 名义 %.2f / 实际 %.2f"
            % (steps, cfg, sampler_name, scheduler, seed,
               denoise, S.effective_denoise(steps, denoise)),
            "prompt_used = 原始故事板稿；每格**真正送模型**的那句在输出 `prompts_json`（%d 条）"
            % len(sent),
            "输出：grid %s，panels %s" % (
                "×".join(str(v) for v in grid.shape), "×".join(str(v) for v in panels_img.shape)),
        ] + notes) + _mp_note(lay, per_panel=True, sample_px=(pw, ph))

        return (grid, panels_img, latent_batch, lay.spec(), (positive or "").strip(), report,
                json.dumps(sent, ensure_ascii=False))


def _assemble_per_panel(shots, lay, reborder=True, border_px=0, shave=0):
    """逐格拼版 → 可选复绘宫格线 → 裁格。与整张模式共用同一套 reborder 语义。"""
    return P.assemble_panels(shots, lay, reborder=reborder, border_px=border_px, shave=shave)


def _match_layout_to_image(lay, width, height):
    """解码尺寸与布局不一致时，按同一套比例重建几何（保持格数/列行）。

    必须保证重建后的 `width/height` **正好等于解码尺寸**：`build_layout` 默认会把
    对齐余量补到 16 的倍数，而解码结果不一定对齐，补出来的画布会比输入大几像素，
    下一步 `crop_panels` 就会因尺寸不符直接报错。这里把差额显式写进 pad。
    """
    pw = max(1, (width - 2 * lay.margin - (lay.cols - 1) * lay.gutter) // lay.cols)
    ph = max(1, (height - 2 * lay.margin - (lay.rows - 1) * lay.gutter) // lay.rows)
    raw_w = lay.cols * pw + (lay.cols - 1) * lay.gutter + 2 * lay.margin
    raw_h = lay.rows * ph + (lay.rows - 1) * lay.gutter + 2 * lay.margin
    pad_x, pad_y = width - raw_w, height - raw_h
    if pad_x < 0 or pad_y < 0:
        raise ValueError("解码尺寸 %dx%d 小于布局下限 %dx%d；无法重建裁切几何"
                         % (width, height, raw_w, raw_h))
    return L.build_layout(panels=lay.panels, cols=lay.cols, rows=lay.rows,
                          gutter=lay.gutter, margin=lay.margin,
                          panel_w=pw, panel_h=ph, pad_x=pad_x, pad_y=pad_y)


class MossStoryboardCrop:
    """只裁格：给想自己接 KSampler / 后期节点的用户。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "【接线】整张宫格图（KSampler→VAEDecode 的结果）。"}),
                "target_aspect": (_CROP_ASPECTS, {
                    "default": "keep",
                    "tooltip": "【填什么】keep = 保持单格原比例（推荐，零重采样）。\n"
                               "选具体比例会做中心裁切，非等比，慎用。",
                }),
                "padding_mode": (["crop", "pad"], {
                    "default": "crop",
                    "tooltip": "【填什么】crop = 中心裁掉多余边；pad = 补黑边（不丢画面）。",
                }),
                "resize_edge": ("INT", {
                    "default": 0, "min": 0, "max": 4096, "step": 16,
                    "tooltip": "【可留 0】0 = 保持原尺寸（推荐）。>0 时把每格长边缩放到该值。",
                }),
                "shave": ("INT", {
                    "default": 0, "min": 0, "max": _SHAVE_MAX, "step": 1,
                    "tooltip": "【可留 0】每格四边内缩像素，用来吃掉溢出的缝。",
                }),
            },
            "optional": {
                "spec": ("STRING", {
                    "default": "", "forceInput": True,
                    "tooltip": "【接线】接 MossStoryboardLayout/Sampler 的 spec，裁切位置才与生成时完全一致。",
                }),
                "columns": ("INT", {
                    "default": 0, "min": 0, "max": L.MAX_AXIS, "step": 1,
                    "tooltip": "【未接 spec 时必填】列数。填 0 且无 spec 会报错，不会瞎猜。",
                }),
                "rows": ("INT", {
                    "default": 0, "min": 0, "max": L.MAX_AXIS, "step": 1,
                    "tooltip": "【未接 spec 时必填】行数。",
                }),
                "show_guides": ("BOOLEAN", {
                    "default": False, "advanced": True,
                    "tooltip": "【调试】在 preview 输出上把裁切线画成红色，用来目检对齐。",
                }),
                "index": ("INT", {
                    "default": -1, "min": -1, "max": L.MAX_PANELS - 1, "step": 1,
                    "tooltip": "【可留 -1】-1 = 输出全部格子（默认，行为不变）。\n"
                               "填 0 起的格子号 = 只输出那一格，并把格号送到第 4 个输出 index 上。\n"
                               "接给 MossStoryboardPaste 就不用手打格号，不会错位。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING", "INT")
    RETURN_NAMES = ("panels", "preview", "report", "index")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = "把整张宫格图按几何参数精确切成逐格。接了 Layout 的 spec 就与生成时逐像素一致。"
    SEARCH_ALIASES = ["crop", "split", "裁格", "切图", "分格", "panel crop", "grid crop"]

    @_node_errors("columns", "rows", "shave", "index")
    def run(self, image, target_aspect, padding_mode, resize_edge, shave,
            spec="", columns=0, rows=0, show_guides=False, index=-1):
        if spec and str(spec).strip():
            lay = L.parse_spec(spec)
            src = "spec 接线"
        else:
            if columns <= 0 or rows <= 0:
                raise ValueError("没接 spec 时必须填 columns 和 rows（都 >0），否则无法确定裁切框")
            h, w = int(image.shape[1]), int(image.shape[2])
            if w % columns or h % rows:
                raise ValueError("图像 %dx%d 不能被 %dx%d 整除：会得到差 1px 的格子，"
                                 "下游拼接必崩。请改用被整除的画布，或接 spec 让几何一致。"
                                 % (w, h, columns, rows))
            lay = L.build_layout(panels=columns * rows, cols=columns, rows=rows, gutter=0, margin=0,
                                 panel_w=w // columns, panel_h=h // rows)
            src = "columns/rows 均分（无外边距）"

        ratio = None if target_aspect == "keep" else L.ASPECTS[target_aspect]
        panels_img = P.crop_panels(image, lay, shave=shave, target_aspect=ratio,
                                   padding_mode=padding_mode, resize_edge=resize_edge)
        preview = P.guide_preview(image, lay) if show_guides else image

        picked = int(index)
        if picked >= 0:
            if picked >= panels_img.shape[0]:
                raise ValueError("index=%d 越界：共 %d 格（0 起）" % (picked, panels_img.shape[0]))
            panels_img = panels_img[picked:picked + 1]

        report = "\n".join([
            "几何来源：%s" % src,
            "%d 格 = %d 列 × %d 行，单格 %d × %d" % (lay.panels, lay.cols, lay.rows,
                                                lay.panel_w, lay.panel_h),
            "输出 %d 张，尺寸 %s" % (panels_img.shape[0], "×".join(str(v) for v in panels_img.shape[1:])),
        ] + (["只取第 %d 格（0 起）= 第 %d 行第 %d 列" % (picked, lay.cell(picked).row + 1,
                                                    lay.cell(picked).col + 1)] if picked >= 0 else []))
        return (panels_img, preview, report, picked)


class MossStoryboardPaste:
    """把改过的单格贴回宫格：`MossStoryboardCrop` 的逆运算。

    用途是"只改一格、不重跑整张"。宫格采样一次要几十秒到几分钟，而某一格不对时
    重跑会把所有格的种子一起动掉；先用官方 MOSS 编辑节点改那一格，再用本节点
    按 `spec` 的坐标逐像素贴回去，其它格与宫格线一个字节都不动。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "grid": ("IMAGE", {
                    "tooltip": "【接线】整张宫格图（Sampler 的 grid 输出，或上一级 Paste 的输出）。",
                }),
                "panel": ("IMAGE", {
                    "tooltip": "【接线】改好的那一格（单张）。接 Crop 只取一格后的输出最稳。",
                }),
                "index": ("INT", {
                    "default": 0, "min": 0, "max": L.MAX_PANELS - 1, "step": 1,
                    "tooltip": "【填什么 / 可接线】贴到第几格，**0 起、行主序**（从左到右、从上到下）。\n"
                               "接了 Crop 的 index 输出就以它为准，不用手打，也不会和裁的那格对不上。",
                }),
                "shave": ("INT", {
                    "default": 0, "min": 0, "max": _SHAVE_MAX, "step": 1,
                    "tooltip": "【可留 0】必须与当初 Crop 的 shave 一致：裁时四边内缩 s 像素，\n"
                               "贴回也写在同一个内缩矩形里。不一致不会静默偏移 —— 尺寸对不上会\n"
                               "bicubic 重采样并写进 report，代价是画面变糊。",
                }),
                "show_guides": ("BOOLEAN", {
                    "default": False, "advanced": True,
                    "tooltip": "【调试】在 marked 输出上把贴回区域框成纯红，用来目检「贴进了哪一格」。",
                }),
            },
            "optional": {
                "spec": ("STRING", {
                    "default": "", "forceInput": True,
                    "tooltip": "【接线】接 Layout/Sampler 的 spec，贴回位置才与生成时逐像素一致。",
                }),
                "columns": ("INT", {
                    "default": 0, "min": 0, "max": L.MAX_AXIS, "step": 1,
                    "tooltip": "【未接 spec 时必填】列数。填 0 且无 spec 会报错，不会瞎猜。",
                }),
                "rows": ("INT", {
                    "default": 0, "min": 0, "max": L.MAX_AXIS, "step": 1,
                    "tooltip": "【未接 spec 时必填】行数。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("grid", "marked", "report")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = ("把改好的单格按 spec 坐标逐像素贴回宫格，输出改好的整张宫格。"
                   "只动目标格的内容区，宫格线与其他格不受影响；可串联多次改多格。")
    SEARCH_ALIASES = ["paste", "merge", "贴回", "合回", "回填", "panel paste", "grid paste"]

    @_node_errors("index", "shave")
    def run(self, grid, panel, index, shave, show_guides=False,
            spec="", columns=0, rows=0):
        if spec and str(spec).strip():
            lay = L.parse_spec(spec)
            src = "spec 接线"
        else:
            if columns <= 0 or rows <= 0:
                raise ValueError("没接 spec 时必须填 columns 和 rows（都 >0），否则无法确定贴回位置")
            h, w = int(grid.shape[1]), int(grid.shape[2])
            if w % columns or h % rows:
                raise ValueError("图像 %dx%d 不能被 %dx%d 整除：会得到差 1px 的格子，"
                                 "下游拼接必崩。请改用被整除的画布，或接 spec 让几何一致。"
                                 % (w, h, columns, rows))
            lay = L.build_layout(panels=columns * rows, cols=columns, rows=rows, gutter=0, margin=0,
                                 panel_w=w // columns, panel_h=h // rows)
            src = "columns/rows 均分（无外边距）"

        out, notes = P.paste_panel(grid, lay, index, panel, shave=shave)
        marked = P.paste_marker(grid, lay, index, shave=shave) if show_guides else grid

        report = "\n".join(["几何来源：%s" % src] + notes)
        return (out, marked, report)


class MossStoryboardFaceSwap:
    """换脸：把参考图的身份换到某一格（或整图）。图生图 + vision 通道注入。

    与「贴回」的分工：贴回处理**几何**（把改好的格子按 spec 坐标写回去），
    本节点处理**身份**（用参考图重画这张图）。两者可以串起来用。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "【接线】MOSS-Image 模型。产线配方：先接 8 步 DMD2 蒸馏 LoRA，"
                                               "再接 Moss-Image-Edit 编辑 LoRA。"}),
                "clip": ("CLIP", {"tooltip": "【接线】CLIPLoader，type 必须是 moss_image。\n"
                                             "参考图就是从这里进 Qwen3.5 vision tower 的。"}),
                "vae": ("VAE", {"tooltip": "【接线】FLUX.2 VAE。源图要它编码成 latent（图生图），"
                                           "结果也要它解码。"}),
                "image": ("IMAGE", {"tooltip": "【接线】要换脸的图。接了 spec 时给**整张宫格**，"
                                               "节点自己裁第 index 格；不接 spec 就当单格处理。"}),
                "ref": ("IMAGE", {"tooltip": "【接线】身份参考图，**紧特写**最好（脸占画幅越大越好）。\n"
                                             "⚠ 只给一张：实测双视角比单张更差（0.384 vs 0.463，视觉 token 被摊薄）。"}),
                "prompt": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "【填什么】重画时用的场景描述，**用英文写**，并且要提到参考图，例如：\n"
                               "A medium shot photograph of the man in Picture 1, sitting at a wooden table "
                               "in a warm cafe, soft warm light on his face.\n"
                               "⚠ 不要自己写 `Picture 1:` 前缀，节点会自动拼。",
                }),
                "steps": ("INT", {
                    "default": 8, "min": 1, "max": 200, "step": 1,
                    "tooltip": "【填什么】默认 8 = 8 步 DMD2 蒸馏 LoRA 的口径。\n"
                               "没接蒸馏 LoRA 时按模型卡 50 步。",
                }),
                "cfg": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 30.0, "step": 0.1,
                    "tooltip": "【填什么】默认 1.0 = 蒸馏档。cfg=1.0 时宿主跳过负面分支，"
                               "负面提示词填了也不生效。",
                }),
                "sampler_name": (["euler", "euler_ancestral", "heun", "dpmpp_2m", "dpmpp_2m_sde",
                                  "dpmpp_3m_sde", "res_multistep", "er_sde", "lms"], {
                    "default": "euler", "tooltip": "【填什么】默认 euler = 实测配方。"}),
                "scheduler": (_scheduler_names(), {
                    "default": "simple", "tooltip": "【填什么】默认 simple = 实测配方（8 步档）。"}),
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xFFFFFFFF, "step": 1,
                    "tooltip": "【填什么】随机种。同种子同参数才可复现。",
                }),
                "denoise": ("FLOAT", {
                    "default": 0.95, "min": 0.05, "max": 1.0, "step": 0.01,
                    "tooltip": "【填什么】降噪强度。这是「换多少」的总闸：\n"
                               "· 0.95（默认）= 实测配方，**8 步下等价于 1.0**：源图被完全丢弃，"
                               "构图由提示词决定，身份由参考图决定。\n"
                               "· ≤0.875 才真的保住源图构图（8 步时 0.875 是分水岭："
                               "int(8/0.875)=9>8 才触发 img2img）。\n"
                               "⚠ 宿主按 int(步数/降噪) 截断 sigma，所以 0.875~0.99 之间"
                               "在 8 步下**全部等于 1.0**。report 会打印真实生效值。",
                }),
                "vision_megapixels": ("FLOAT", {
                    "default": 1.05, "min": 0.0, "max": 4.0, "step": 0.01,
                    "tooltip": "【填什么】每张参考图进 vision tower 前的像素预算。\n"
                               "· 1.05（默认）= 实测产线值，约 1024×1025\n"
                               "· 0 = 原生分辨率，不缩放\n"
                               "· 0.59 = 768²，实测安全线\n"
                               "⚠ 0.147 是宿主默认值，那个值下身份余弦只有 0.008；"
                               "0.30 会**变成另一个人**（塌陷带）。",
                }),
            },
            "optional": {
                "spec": ("STRING", {
                    "default": "", "forceInput": True,
                    "tooltip": "【接线】接 Layout/Sampler 的 spec：给整张宫格 + index，只换那一格。\n"
                               "不接就按单格处理（整张 image 当成一张图）。",
                }),
                "index": ("INT", {
                    "default": 0, "min": 0, "max": L.MAX_PANELS - 1, "step": 1,
                    "tooltip": "【接了 spec 时生效】换第几格，**0 起、行主序**。",
                }),
                "sample_edge": ("INT", {
                    "default": FS.DEFAULT_SAMPLE_EDGE, "min": 0, "max": 2048, "step": 16,
                    "tooltip": "【可留 1024】单格抬到多长边再采样，采样完缩回格子。\n"
                               "4 格 496 长边时一格只有 0.135MP，脸约 50×27 像素——那个尺度认不出是谁。\n"
                               "0 = 不抬（严格按格子尺寸采样，最省显存）。",
                }),
                "negative": ("STRING", {
                    "multiline": True, "default": "", "advanced": True,
                    "tooltip": "【可选】负面提示词。默认 cfg=1.0 时宿主跳过负面分支，填了也不生效。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "report")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = ("换脸：把参考图的脸换到某一格（或整图）。参考图走 vision 通道，"
                   "源图走图生图重画。接了 spec 就只换那一格，其余格与宫格线不动。")
    SEARCH_ALIASES = ["face swap", "faceswap", "换脸", "人脸替换", "换人脸", "identity", "参考图"]

    @_node_errors("index", "denoise", "vision_megapixels", "sample_edge")
    def run(self, model, clip, vae, image, ref, prompt, steps, cfg,
            sampler_name, scheduler, seed, denoise, vision_megapixels,
            spec="", index=0, sample_edge=FS.DEFAULT_SAMPLE_EDGE, negative=""):
        from .core import sampling as S      # 惰性导入：本文件顶层不拉 torch

        if not str(prompt or "").strip():
            raise ValueError(
                "prompt 为空：换脸要重画这张图，必须给场景描述，"
                "并且里面要提到参考图（例如 the man in Picture 1）。")

        lay = L.parse_spec(spec) if str(spec or "").strip() else None
        panel, where = FS.resolve_target(image, lay, index)
        cell_w, cell_h = int(panel.shape[2]), int(panel.shape[1])

        # 抬分辨率必须在编码之前：源图 latent 的尺寸决定了输出尺寸
        panel_up, up = FS.to_sampling(panel, sample_edge)
        positive, ref_rep = RI.encode(clip, prompt, ref, vision_megapixels)
        negative_cond = S.encode_text(clip, str(negative or ""))

        latent = vae.encode(panel_up)
        sampled = S.sample(model, positive, negative_cond, latent, steps, cfg,
                           sampler_name, scheduler, denoise=denoise, seed=seed)
        out = S.decode(vae, sampled)
        out = FS.to_cell(out, (cell_w, cell_h))

        if lay is None:
            final, paste_notes = out, []
        else:
            final, paste_notes = P.paste_panel(image, lay, index, out, shave=0)

        real_denoise = S.effective_denoise(steps, denoise)
        lines = [
            "换哪一张：%s" % where,
            "格子 %d×%d（%.3f MP）→ 采样 %d×%d（%.3f MP）"
            % (cell_w, cell_h, FS.megapixels(panel), up["sampled"][0], up["sampled"][1],
               FS.megapixels(panel_up)),
            "参考图 %d×%d（%.3f MP）→ 进 vision %d×%d（%.3f MP，系数 %.2f）"
            % (ref_rep["native"][0], ref_rep["native"][1], ref_rep["native_megapixels"],
               ref_rep["target"][0], ref_rep["target"][1], ref_rep["target_megapixels"],
               ref_rep["scale"]),
            "采样：%d 步 / cfg %.2f / %s + %s / 种子 %d"
            % (int(steps), float(cfg), sampler_name, scheduler, int(seed)),
            "降噪：名义 %.2f → **实际 %.2f**%s"
            % (float(denoise), real_denoise,
               "（源图被完全丢弃，构图来自提示词）" if real_denoise >= 1.0
               else "（保留源图构图）"),
        ]
        if ref_rep.get("batch_dropped", 1) > 1:
            lines.append("参考图批次 %d 张，只用了第一张。" % ref_rep["batch_dropped"])
        lines += ref_rep.get("notes", [])
        if up["scale"] > 1.0:
            lines.append("单格被抬了 %.2f 倍再采样：脸在那个尺度上才认得出，缩回后细节更实。"
                         % up["scale"])
        lines += paste_notes
        if real_denoise >= 1.0 and float(denoise) < 1.0:
            lines.append(
                "⚠ 名义降噪 %.2f 在 %d 步下被宿主截断成 1.0（int(%d/%.2f)=%d 不大于步数）。"
                "想真的保住源图构图，要 ≤%.3f。"
                % (float(denoise), int(steps), int(steps), float(denoise),
                   int(int(steps) / float(denoise)) if denoise else 0,
                   max(0.05, 1.0 - 1.0 / int(steps))))
        return (final, "\n".join(lines))


class MossPromptCompiler:
    """把官方 MOSS-Image Prompt Enhancer 的 caption / layout 草稿编译成提示词。零模型。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "draft": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "【填什么】官方 MOSS-Image Prompt Enhancer 产出的**草稿**（不是已编译的提示词）。\n"
                               "caption 草稿：{\"caption\": \"...\"}；\n"
                               "layout 草稿：width / height / bbox_coord_space=\"abs\" / bbox_format=\"xyxy\" / summary / items。\n"
                               "输出 prompt 可直接接 CLIPTextEncode 或采样器 positive；\n"
                               "layout 草稿另出 state（可粘贴到布局编辑器的导入框）。\n"
                               "⚠ 最多 256 个元素；未知字段、越界、非 16 倍数画布会报错。",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "report", "state")
    FUNCTION = "compile"
    CATEGORY = CATEGORY
    DESCRIPTION = ("编译官方 prompt-enhancer 草稿为 MOSS 提示词；"
                   "layout 草稿另出 state v3，可导入布局编辑器。零模型。")
    SEARCH_ALIASES = ["prompt enhancer", "prompt compiler", "skill", "layout draft",
                      "提示词增强", "草稿编译", "提示词编译"]

    @_node_errors("draft")
    def compile(self, draft):
        text = "" if draft is None else str(draft)
        if not text.strip():
            raise ValueError("draft 为空：请粘贴官方 prompt-enhancer 的 caption/layout 草稿 JSON。")
        obj = S.loads(text)
        prompt, rep = S.compile_draft(obj)
        state = ""
        notes = list(rep.get("notes") or [])
        if rep["kind"] == "caption":
            head = "类型：caption（纯文本，无布局框）"
            notes.append("caption 草稿没有 state 输出。")
        else:
            labels = "、".join("%s %d" % (k, v) for k, v in sorted(rep["labels"].items()))
            head = "类型：layout｜画布 %d×%d｜元素 %d（%s）" % (
                rep["width"], rep["height"], rep["items"], labels)
            try:
                state_obj, state_notes = S.draft_to_state(obj)
            except ValueError as exc:
                notes.append("state 未生成：%s" % exc)
            else:
                state = json.dumps(state_obj, ensure_ascii=False, separators=(",", ":"))
                notes.extend(state_notes)
                notes.append("state 已生成：可粘贴到布局编辑器的导入框。")
        return (prompt, "\n".join([head] + notes), state)


NODE_CLASS_MAPPINGS = {
    "MossLayoutEditor": MossLayoutEditor,
    "MossStoryboardLayout": MossStoryboardLayout,
    "MossStoryboardSampler": MossStoryboardSampler,
    "MossStoryboardCrop": MossStoryboardCrop,
    "MossStoryboardPaste": MossStoryboardPaste,
    "MossPromptCompiler": MossPromptCompiler,
    "MossStoryboardFaceSwap": MossStoryboardFaceSwap,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MossLayoutEditor": "MOSS Layout Editor",
    "MossStoryboardLayout": "MOSS Storyboard Layout",
    "MossStoryboardSampler": "MOSS Storyboard Sampler",
    "MossStoryboardCrop": "MOSS Storyboard Crop",
    "MossStoryboardPaste": "MOSS Storyboard Paste",
    "MossPromptCompiler": "MOSS Prompt Compiler",
    "MossStoryboardFaceSwap": "MOSS Storyboard Face Swap",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
