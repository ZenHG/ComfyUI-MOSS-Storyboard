# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""V1 节点契约（本包唯一真相源，V3 外壳由 `v3/_compat.py` 从这里生成）。

三个节点：
- `MossStoryboardLayout`：算宫格几何、生成 MOSS 原生布局 JSON、出模板图。零模型。
- `MossStoryboardSampler`：一步到位——布局 + 采样 + 复绘宫格线 + 裁格。
- `MossStoryboardCrop`：只裁格（给想自己接 KSampler 的人）。
"""

import functools
import inspect
import os

from .core import layout as L
from .core import pixels as P
from .core import prompt as T

CATEGORY = "moss/storyboard"

_INIT_MODES = ["auto", "layout", "template", "both", "plain"]
_ASPECT_KEYS = list(L.ASPECTS)
_CROP_ASPECTS = ["keep", "1:1", "16:9", "4:3", "9:16", "21:9"]

# MOSS-Image 训练区间 1–2MP，官方声明外推到 ~4MP。超了只警告，不拦。
_MP_COMFORT = 2.0
_MP_LIMIT = 4.0


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


def _mp_note(layout):
    mp = layout.megapixels
    if mp > _MP_LIMIT:
        return ("\n⚠ 画布 %.2fMP 已超出 MOSS-Image 官方外推上限（≈4MP）：文字与布局退化风险高。"
                "把 panel_edge 调小，或改用「逐格生成 + 拼版」。" % mp)
    if mp > _MP_COMFORT:
        return ("\n⚠ 画布 %.2fMP 高于训练区间（1–2MP）：可跑，但小字与细节开始不稳。"
                "建议 panel_edge 降到能压进 2MP 的值。" % mp)
    return ""


def _resolve_layout(panels, panel_aspect, panel_edge, canvas_aspect, gutter, margin, columns=0, rows=0):
    return L.build_layout(panels=panels, cols=columns, rows=rows, gutter=gutter, margin=margin,
                          panel_edge=panel_edge, panel_aspect=panel_aspect,
                          canvas_aspect=canvas_aspect)


def _prompt_for_layout(text, lay, label="object"):
    """把提示词稿转成布局 JSON 文本，返回 `(最终提示词, 摘要行)`。"""
    head, caps = T.split_panels(text)
    if not caps:
        return (head or "").strip(), "未找到 [PANEL n] 标记 ⇒ 按普通文生图处理（无布局约束）"
    pairs, notes = T.pair_captions(caps, lay.cells)
    out = T.layout_json_text(head, pairs, lay.boxes_abs_centered(), label=label)
    info = "解析 %d 段面板描述，%d 段带框" % (len(caps), len(pairs))
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
                               "⚠ 没有 [PANEL n] 标记时按普通文生图处理，宫格只靠模板锁定。",
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
        }

    RETURN_TYPES = ("INT", "INT", "INT", "INT", "STRING", "STRING", "IMAGE", "STRING")
    RETURN_NAMES = ("width", "height", "columns", "rows", "spec", "layout_json", "template", "report")
    FUNCTION = "build"
    CATEGORY = CATEGORY
    DESCRIPTION = "算 3/4/6/8/9 宫格几何，生成 MOSS-Image 原生布局提示词与宫格模板图。"

    @_node_errors("panels", "panel_aspect", "panel_edge")
    def build(self, prompt, panels, panel_aspect, panel_edge, canvas_aspect, columns, rows,
              gutter, margin, cell_gray, invert, label):
        lay = _resolve_layout(panels, panel_aspect, panel_edge, canvas_aspect, gutter, margin, columns, rows)
        text, info = _prompt_for_layout(prompt, lay, label)
        template = P.render_template(lay, cell_gray=cell_gray, invert=invert)

        report = "\n".join([
            "布局 %d 格 = %d 列 × %d 行（%s）" % (lay.panels, lay.cols, lay.rows, lay.note or "手工指定"),
            "画布 %d × %d（%.2fMP，对齐余量 右%d/下%d）" % (lay.width, lay.height, lay.megapixels,
                                                    lay.pad_x, lay.pad_y),
            "单格 %d × %d，缝 %dpx，外边距 %dpx" % (lay.panel_w, lay.panel_h, lay.gutter, lay.margin),
            "提示词：%s" % info,
            "布局文本 %d 字%s" % (len(text), _mp_note(lay)),
        ])
        return (lay.width, lay.height, lay.cols, lay.rows, lay.spec(), text, template, report)


class MossStoryboardSampler:
    """一步出故事板：布局约束（可选）+ 模板初始化（可选）+ 采样 + 复绘宫格线 + 裁格。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "【接线】UNETLoader 输出的 MOSS-Image 模型（可先过 LoRA）。"}),
                "clip": ("CLIP", {"tooltip": "【接线】CLIPLoader，type 必须是 moss_image。\n"
                                             "⚠ 用错 type 会静默拿到错误嵌入，画面语义跑飞。"}),
                "vae": ("VAE", {"tooltip": "【接线】FLUX.2 VAE（moss 的 latent 空间就是它）。"}),
                "positive": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "【填什么】故事板稿：[STYLE] 全局描述 + 每格 [PANEL n]。\n"
                               "init=layout/both 时会自动转成 MOSS 布局 JSON 再编码。",
                }),
                "negative": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "【可留空】负面提示词。\n"
                               "⚠ cfg=1.0 时负面分支不参与计算，这段等于没写（本地实测配置即 cfg=1.0）。",
                }),
                "steps": ("INT", {
                    "default": 35, "min": 1, "max": 200, "step": 1,
                    "tooltip": "【填什么】采样步数。默认 35 = 本地已实测可用的官方节点配置。\n"
                               "⚠ 官方文档的非蒸馏配方是 50 步 / cfg 3；要更高保真可试，但更慢。",
                }),
                "cfg": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 30.0, "step": 0.1,
                    "tooltip": "【填什么】引导强度。默认 1.0 = 本地已实测可用的配置（负面分支失效）。\n"
                               "⚠ 要负面提示词生效必须 >1；官方非蒸馏配方给 3。换成大步数+高 cfg 前先小样对比。",
                }),
                "sampler_name": (["euler", "euler_ancestral", "heun", "dpmpp_2m", "dpmpp_2m_sde",
                                  "dpmpp_3m_sde", "res_multistep", "er_sde", "lms"], {
                    "default": "euler", "tooltip": "【填什么】默认 euler（本地实测配置同款）。"}),
                "scheduler": (["simple", "normal", "beta", "sgm_uniform", "karras", "exponential", "ddim_uniform"], {
                    "default": "simple", "tooltip": "【填什么】默认 simple（本地实测配置同款）。"}),
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xFFFFFFFF, "step": 1,
                    "tooltip": "【填什么】随机种。同种子同参数才可复现。",
                }),
                "denoise": ("FLOAT", {
                    "default": 1.0, "min": 0.05, "max": 1.0, "step": 0.01,
                    "tooltip": "【填什么】降噪强度。1.0 = 纯生成（布局 JSON 起作用）。\n"
                               "⚠ 用模板初始化时必须 <1：0.75–0.90 是保宫格线的常用区间，越低越像模板、越高越自由。",
                }),
            },
            "optional": {
                "spec": ("STRING", {
                    "default": "", "forceInput": True,
                    "tooltip": "【接线】接 MossStoryboardLayout 的 spec。接了就用它的几何，下面几何参数被忽略。",
                }),
                "template": ("IMAGE", {
                    "tooltip": "【接线】宫格模板图。不接时若 init 需要模板会自动现算一张。",
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
                    "tooltip": "【填什么】怎么锁布局：\n"
                               "auto（默认）= 只把面板描述转成布局框，纯生成；\n"
                               "layout = 同上，显式写法；\n"
                               "template = 只用模板图做 img2img 起点（⚠ 实测无效：MOSS 无原生 img2img，"
                               "会得到模板残影或纯黑）；\n"
                               "both = 布局框 + 模板（⚠ 同上，模板那段无效）；\n"
                               "plain = 原样送提示词，纯 txt2img，无布局框。",
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
                    "default": 0, "min": 0, "max": 32, "step": 1,
                    "tooltip": "【可留 0】裁格时每格四边内缩像素。线已复绘在缝里，正常留 0。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "LATENT", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("grid", "panels", "latent", "spec", "prompt_used", "report")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = "用 MOSS-Image 一步生成 3/4/6/8/9 宫格故事板，并输出逐格切片。"

    @_node_errors("panels", "steps", "cfg", "denoise")
    def run(self, model, clip, vae, positive, negative, steps, cfg, sampler_name, scheduler,
            seed, denoise, spec="", template=None, panels=8, panel_aspect="16:9", panel_edge=512,
            canvas_aspect="auto", gutter=8, margin=8, init="auto", label="object",
            reborder=True, border_px=0, shave=0):
        from .core import sampling as S

        if spec and str(spec).strip():
            lay = L.parse_spec(spec)
            geom_src = "spec 接线"
        else:
            lay = _resolve_layout(panels, panel_aspect, panel_edge, canvas_aspect, gutter, margin)
            geom_src = "本节点参数"

        notes = []
        mode = init
        if mode == "auto":
            mode = "layout"          # 模板路线在 MOSS 上实测走不通（见 README §七），不设默认
            if template is not None:
                notes.append("模板已接线，但 init=auto 走纯布局：MOSS 无 img2img，"
                             "实测模板路线会得到模板残影或纯黑（要试就显式选 template/both）")

        # ---- 提示词：layout / both 时转成布局 JSON ----
        if mode in ("layout", "both"):
            text, info = _prompt_for_layout(positive, lay, label)
            notes.append("布局提示词：%s" % info)
        else:
            text, info = (positive or "").strip(), "原样送提示词（未加布局框）"
            notes.append(info)

        cond_pos = S.encode_text(clip, text)
        cond_neg = S.encode_text(clip, negative or "")

        # ---- 起点 latent ----
        use_template = mode in ("template", "both")
        if use_template:
            tpl = template if template is not None else P.render_template(lay, cell_gray=25)
            if tpl.shape[1] != lay.height or tpl.shape[2] != lay.width:
                notes.append("⚠ 传入模板 %dx%d 与布局 %dx%d 不一致 ⇒ 改用按布局现算的模板"
                             % (tpl.shape[2], tpl.shape[1], lay.width, lay.height))
                tpl = P.render_template(lay, cell_gray=25)
            if denoise > 0.999:
                notes.append("⚠ denoise=1.0：模板被完全覆盖，等于没用（要模板效果请调 0.75–0.90）")
            elif S.effective_denoise(steps, denoise) >= 1.0:
                notes.append("⚠ 步数 %d 太小：int(%d/%.2f)=%d 没有截断 sigma ⇒ denoise 实际等于 1.0，"
                             "模板完全没参与。要真 img2img 请把 denoise 降到 ≤%.2f，或把步数提到 ≥%d"
                             % (steps, steps, denoise, int(steps / denoise),
                                steps / (steps + 1.0), steps + 1))
            notes.append("⚠ MOSS 无原生 img2img：实测模板路段会得到模板残影或纯黑（3 组步数/降噪都复现）")
            latent = S.encode_image(vae, tpl)
            notes.append("起点 = 模板 latent（denoise %.2f）" % denoise)
        else:
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
            "几何来源：%s" % geom_src,
            "布局 %d 格 = %d 列 × %d 行，画布 %d × %d（%.2fMP）"
            % (lay.panels, lay.cols, lay.rows, lay.width, lay.height, lay.megapixels),
            "单格 %d × %d，缝 %dpx，外边距 %dpx，对齐余量 右%d/下%d"
            % (lay.panel_w, lay.panel_h, lay.gutter, lay.margin, lay.pad_x, lay.pad_y),
            "init=%s｜%d 步｜cfg %.2f｜%s/%s｜种子 %d｜denoise 名义 %.2f / 实际 %.2f"
            % (mode, steps, cfg, sampler_name, scheduler, seed, denoise,
               S.effective_denoise(steps, denoise)),
            "输出：grid %s，panels %s" % (
                "×".join(str(v) for v in grid.shape), "×".join(str(v) for v in panels_img.shape)),
        ] + notes) + _mp_note(lay)

        return (grid, panels_img, out, lay.spec(), text, report)


def _match_layout_to_image(lay, width, height):
    """解码尺寸与布局不一致时，按同一套比例重建几何（保持格数/列行）。"""
    kw = dict(panels=lay.panels, cols=lay.cols, rows=lay.rows, gutter=lay.gutter,
              margin=lay.margin, panel_w=max(64, (width - 2 * lay.margin
                                                  - (lay.cols - 1) * lay.gutter) // lay.cols),
              panel_h=max(64, (height - 2 * lay.margin
                               - (lay.rows - 1) * lay.gutter) // lay.rows))
    return L.build_layout(**kw)


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
                    "default": 0, "min": 0, "max": 64, "step": 1,
                    "tooltip": "【可留 0】每格四边内缩像素，用来吃掉溢出的缝。",
                }),
            },
            "optional": {
                "spec": ("STRING", {
                    "default": "", "forceInput": True,
                    "tooltip": "【接线】接 MossStoryboardLayout/Sampler 的 spec，裁切位置才与生成时完全一致。",
                }),
                "columns": ("INT", {
                    "default": 0, "min": 0, "max": 20, "step": 1,
                    "tooltip": "【未接 spec 时必填】列数。填 0 且无 spec 会报错，不会瞎猜。",
                }),
                "rows": ("INT", {
                    "default": 0, "min": 0, "max": 20, "step": 1,
                    "tooltip": "【未接 spec 时必填】行数。",
                }),
                "show_guides": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "【调试】在 preview 输出上把裁切线画成红色，用来目检对齐。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("panels", "preview", "report")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = "按 spec（或列/行）把宫格图精确切成逐格，附带裁切线预览。"

    @_node_errors("columns", "rows", "shave")
    def run(self, image, target_aspect, padding_mode, resize_edge, shave,
            spec="", columns=0, rows=0, show_guides=False):
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

        report = "\n".join([
            "几何来源：%s" % src,
            "%d 格 = %d 列 × %d 行，单格 %d × %d" % (lay.panels, lay.cols, lay.rows,
                                                lay.panel_w, lay.panel_h),
            "输出 %d 张，尺寸 %s" % (panels_img.shape[0], "×".join(str(v) for v in panels_img.shape[1:])),
        ])
        return (panels_img, preview, report)


NODE_CLASS_MAPPINGS = {
    "MossStoryboardLayout": MossStoryboardLayout,
    "MossStoryboardSampler": MossStoryboardSampler,
    "MossStoryboardCrop": MossStoryboardCrop,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MossStoryboardLayout": "MOSS Storyboard Layout",
    "MossStoryboardSampler": "MOSS Storyboard Sampler",
    "MossStoryboardCrop": "MOSS Storyboard Crop",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
