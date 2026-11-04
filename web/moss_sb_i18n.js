// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// MOSS Storyboard · 画布侧中英切换 —— **纯函数层**（可 node 单测，零 DOM）
//
// 词表与改写逻辑是这个功能的全部风险所在（改错一个 key 会静默把用户的标题/参数弄坏），
// 所以必须能在没有浏览器的情况下测。挂钩与按钮在 `moss_sb_i18n_ui.js`。
//
// 🔴 四条铁律（抄 H 仓 `ComfyUI-H3-Latent-Relay` 的同类约束，逐条对应一次事故）：
//   ① **只改 `label` / `title` / `tooltip`，绝不改 `name`** —— `name` 是后端取参与
//      序列化的依据，改它等于改图（工作流 JSON 会脏、连线会丢）。
//   ② **绝不改 `widget.options.values`** —— 那是**存档值**（模型文件名、枚举值一律不翻）。
//      枚举的中文显示走官方 `options.getOptionLabel` 钩子，函数不进 JSON ⇒ 分享不污染别人的图。
//   ③ **用户的自定义标题要保留** —— 只在标题「还是任一语言的默认名」时才替换，且只换前缀。
//   ④ **悬浮提示：中文只在后端写一份**（`nodes.py` 的 `tooltip`），本表**只存 `en`**。
//      切中文时 `tipFor()` 返回 `null` ⇒ 什么都不写 ⇒ 前端回落后端原文。
//      ⇒ 同一个参数不可能出现两套中文说法。
//      词表里没有的组合 ⇒ 一个字节都不动（宁缺勿错）。

/** 语言存取键（**刻意不与别的包共用**，避免互相覆盖 localStorage）。 */
export const LANG_KEY = "mosssb_lang";
/** 语言变化广播事件名（本包私有）。 */
export const LANG_EVENT = "mosssb:lang-changed";

/** 归一化：只认 `zh` / `en`，其余（含 undefined）⇒ 落回 `en`。 */
export function normLang(v) {
    const s = String(v == null ? "" : v).trim().toLowerCase();
    return (s === "zh" || s.startsWith("zh-") || s.startsWith("zh_")) ? "zh" : "en";
}

/**
 * 默认语言 = **宿主界面语言**（调用方去读 `<html lang>` / `Comfy.Locale`）。
 * 不默认中文：界面是英文却把节点翻成中文反而更乱。跟随宿主，用户点按钮才覆盖。
 */
export function defaultLang(readHostLang) {
    try {
        const raw = typeof readHostLang === "function" ? readHostLang() : "";
        const s = String(raw || "").trim().toLowerCase();
        if (s) return normLang(s);
    } catch (e) { /* 读不到就走下面的兜底 */ }
    try {
        const nav = (globalThis.navigator && globalThis.navigator.language) || "";
        if (nav) return normLang(nav);
    } catch (e) { /* node 单测无 navigator */ }
    return "en";
}

// ---------------------------------------------------------------------------
// 词表 1 · 节点标题（class_type → 标题）
// 后端 `NODE_DISPLAY_NAME_MAPPINGS` 存**英文**（无 JS 时英文用户看到英文）；
// 这里补中文，切换时只换标题前缀。
// ---------------------------------------------------------------------------
// `legacy` = 后端 `NODE_DISPLAY_NAME_MAPPINGS` 里的名字（没有图标）。
// 老工作流里节点标题就是它 —— 不把它算进"默认名"，就会被当成**用户自定义标题**，
// 中英切换对这类节点永久失效（2026-10-23 真机踩到）。
export const NODES = {
    MossLayoutEditor: {
        zh: "🎬 布局编辑器", en: "🎬 Layout Editor",
        legacy: "MOSS Layout Editor",
    },
    MossStoryboardLayout: {
        zh: "🎬 故事板 · 宫格几何", en: "🎬 Storyboard · Grid Layout",
        legacy: "MOSS Storyboard Layout",
    },
    MossStoryboardSampler: {
        zh: "🎬 故事板 · 生成", en: "🎬 Storyboard · Generate",
        legacy: "MOSS Storyboard Sampler",
    },
    MossStoryboardCrop: {
        zh: "🎬 故事板 · 裁格", en: "🎬 Storyboard · Split Panels",
        legacy: "MOSS Storyboard Crop",
    },
    MossStoryboardPaste: {
        zh: "🎬 故事板 · 贴回", en: "🎬 Storyboard · Paste Panel",
        legacy: "MOSS Storyboard Paste",
    },
    MossPromptCompiler: {
        zh: "🎬 提示词 · 草稿编译", en: "🎬 Prompt · Draft Compiler",
        legacy: "MOSS Prompt Compiler",
    },
    MossStoryboardFaceSwap: {
        zh: "🎬 故事板 · 换脸", en: "🎬 Storyboard · Face Swap",
        legacy: "MOSS Storyboard Face Swap",
    },
    //: 可选 LLM 子包（分类 `LLM`）的唯一节点。后端显示名仍是英文
    //: （`LLM Prompt`；更早叫 `MOSS Prompt Smith`）⇒ 老工作流的英文标题收进 legacy。
    MossPromptSmith: {
        zh: "🧠 LLM 提示词", en: "🧠 LLM Prompt",
        legacy: "MOSS Prompt Smith",
    },
};

// ---------------------------------------------------------------------------
// 词表 2 · 参数标签（只存中文；英文回落到 widget 原名，原名本身已是英文）
// 键是**参数名**（三节点共用，同名参数语义相同才放进来）
// ---------------------------------------------------------------------------
/** 英文界面下比原名更好读的少数几个（其余英文直接用原名）。 */
export const LABELS_EN = {
    control_after_generate: "after generate",
    panel_edge: "panel long edge",
    cell_gray: "template gray",
    border_px: "line width",
    draft: "prompt draft",
    show_guides: "show crop lines",
    resize_edge: "output long edge",
    spec: "geometry spec",
    target_aspect: "target aspect",
    padding_mode: "padding mode",
    index: "panel index",
    panel: "edited panel",
    grid: "grid image",
    marked: "marked grid",
    // ---- LLM 子包（MossPromptSmith）----
    task: "task",
    channel: "channel",
    dialect: "dialect",
    target: "compile target",
    text: "idea / change",
    length: "length",
    temperature: "temperature",
    max_tokens: "max tokens",
    mmproj: "vision mmproj",
    language: "prose language",
    thinking: "thinking",
    style_lock: "style lock",
    must_keep: "must keep",
    wash: "ignore artifacts",
    two_pass: "two passes",
    runtime: "runtime",
    api_base: "API endpoint",
    api_model: "API model",
    api_key: "API key",
    ctx_size: "context size",
    gpu_layers: "GPU layers",
    cache_type_k: "K cache type",
    cache_type_v: "V cache type",
    flash_attn: "flash attention",
    image_max_tokens: "image max tokens",
    ubatch: "micro-batch",
    parallel: "parallel",
    fit_on: "fit to VRAM",
    fit_target_mib: "VRAM reserve (MiB)",
    mtp: "MTP draft",
    mtp_draft_n: "draft tokens",
    server_exe: "llama-server path",
    port: "port",
    release_after_run: "free VRAM after run",
};

export const LABELS = {
    prompt: "提示词（留空）",
    panels: "格数",
    panel_aspect: "单格比例",
    panel_edge: "单格长边",
    canvas_aspect: "画布比例",
    columns: "列数",
    rows: "行数",
    gutter: "缝宽",
    margin: "外边距",
    cell_gray: "模板灰度",
    invert: "模板反相",
    label: "布局框类型",
    model: "模型",
    clip: "文本编码器",
    vae: "VAE",
    positive: "正面提示词",
    negative: "负面提示词",
    steps: "步数",
    cfg: "CFG",
    sampler_name: "采样器",
    scheduler: "调度器",
    seed: "种子",
    denoise: "降噪强度",
    spec: "几何 spec",
    init: "送词方式",
    reborder: "复绘宫格线",
    border_px: "线宽",
    shave: "内缩",
    image: "图像",
    target_aspect: "目标比例",
    padding_mode: "填充方式",
    resize_edge: "输出长边",
    show_guides: "显示裁切线",
    index: "格号（0 起）",
    panel: "改好的单格",
    grid: "整张宫格",
    draft: "提示词草稿",
    control_after_generate: "生成后",
    // ---- 2026-10-30 补：这几个以前没进表，中文界面下显示的是英文参数名 ----
    editor_state: "布局状态",
    char_boxes: "人物格",
    mode: "生成方式",
    sample_edge: "采样边长",
    ref: "参考图",
    vision_megapixels: "参考图像素预算",
    auto_mask: "自动蒙版",
    face_pick: "挑哪张脸",
    mask: "蒙版",
    width: "宽",
    height: "高",
    // ---- LLM 子包（MossPromptSmith）----
    task: "任务",
    channel: "通道",
    dialect: "方言",
    target: "编译目标",
    text: "想法 / 要改什么",
    length: "篇幅",
    temperature: "温度",
    max_tokens: "最大 token",
    mmproj: "视觉投影",
    language: "说明语言",
    thinking: "思考模式",
    style_lock: "角色 / 风格锁定",
    must_keep: "必留项",
    wash: "忽略杂项",
    two_pass: "两遍法",
    runtime: "运行时",
    api_base: "API 端点",
    api_model: "API 模型名",
    api_key: "API Key",
    ctx_size: "上下文长度",
    gpu_layers: "GPU 层数",
    cache_type_k: "K 缓存精度",
    cache_type_v: "V 缓存精度",
    flash_attn: "闪存注意力",
    image_max_tokens: "每图视觉 token",
    ubatch: "微批大小",
    parallel: "并发请求",
    fit_on: "显存适配",
    fit_target_mib: "预留显存（MiB）",
    mtp: "推测解码 MTP",
    mtp_draft_n: "草稿 token 数",
    server_exe: "服务器程序",
    port: "端口",
    release_after_run: "跑完释放显存",
};

//: **同名参数在不同节点上含义不同** —— 全局标签只能取一个，所以这里按节点覆盖。
//: 例：`prompt` 在「宫格几何」上该留空，在「换脸」上必须提到参考图；`image` 在裁格上是整张宫格，
//: 在换脸上是被改的那张（源图）。只写有差异的，其余回落 LABELS。
const LABELS_NODE = {
    MossStoryboardFaceSwap: {
        prompt: "提示词（要提到 Picture 1）",
        image: "源图",
        index: "换第几格",
    },
    MossStoryboardCrop: {
        image: "整张宫格",
        index: "只输出第几格",
    },
    MossStoryboardPaste: {
        index: "贴到第几格",
        panel: "改好的单格",
    },
    MossStoryboardLayout: {
        prompt: "提示词（留空即可）",
    },
    //: `image` 在子包里是给模型看的参考图（反推必接），不是被改的源图。
    //: `panels` 不是截图的格数，而是宫格稿里声明的面板数。
    MossPromptSmith: {
        image: "参考图（反推必接）",
        panels: "面板数（宫格稿）",
    },
};

// ---------------------------------------------------------------------------
// 词表 3 · 枚举显示（值 → 中文）。**只影响显示**，存档值不变。
// ---------------------------------------------------------------------------
export const COMBOS = {
    canvas_aspect: { auto: "自动" },
    init: {
        // 界面上只写"是什么"，不写"有没有用"——结论放 tooltip，且带版本口径。
        auto: "自动（纯布局）",
        layout: "纯布局",
        plain: "纯提示词",
        // ⚠ 换脸节点也有一个叫 init 的下拉，值是 source / empty —— 同名不同值。
        //    comboLabel 只按 widget 名查表，靠 value 不重叠来区分。加新值前先确认不撞。
        source: "源图（图生图）",
        empty: "空 latent（生成）",
    },
    face_pick: { largest: "最大那张", left: "最左那张", right: "最右那张" },
    label: { object: "物体", character: "人物", text: "文字" },
    target_aspect: { keep: "保持原样" },
    padding_mode: { crop: "中心裁切", pad: "补黑边" },
    panel_aspect: { "1:1": "1:1 方形" },
    weight_dtype: { default: "默认" },
    // 布尔在 V3 里常渲染成 true/false 下拉 ⇒ 也要翻，否则界面上就是裸 true/false
    invert: { true: "开", false: "关" },
    reborder: { true: "开", false: "关" },
    show_guides: { true: "开", false: "关" },
    // 种子下面那一栏（生成后控制）：值是 fixed/increment/decrement/randomize
    control_after_generate: { fixed: "固定", increment: "递增", decrement: "递减", randomize: "随机" },

    // ===== LLM 子包 =====
    // 存档值一律 ASCII id（见 `moss_llm/nodes.py` 顶部注释），这里只给中文显示。
    // 动态下拉（模型文件名 / mmproj 文件名）没有条目 ⇒ 原样显示文件名。
    task: { caption: "反推", expand: "扩写", edit: "编辑指令", compile: "编译（零模型）" },
    channel: { local: "本地 GGUF", api: "外部 API" },
    target: {
        "moss-image": "MOSS 单图",
        "qwen-image-2_1": "Qwen-Image 2.1",
        krea2: "Krea 2",
        flux3: "FLUX 3",
        flux1_dev: "FLUX.1 dev",
        sdxl: "SDXL",
        "minimax-h3": "MiniMax H3（视频）",
        "nano-banana": "Nano Banana（Gemini 图像）",
    },
    dialect: {
        moss_image: "MOSS 单图（英文散文）",
        moss_storyboard: "MOSS 宫格（中文 [PANEL]）",
        generic_natural: "通用自然语言",
        generic_tags: "通用标签",
        cinematic: "电影感",
        cn_natural: "国内模型（中文）",
    },
    length: { brief: "简短", standard: "标准", detailed: "详细" },
    language: { auto: "自动", zh: "中文", en: "英文" },
    thinking: { off: "关（推荐）", on: "开" },
    runtime: { current: "用现有", update: "更新到最新版" },
    mmproj: { auto: "自动匹配", none: "不使用（纯文本）" },
    cache_type_k: { q8_0: "q8_0（省一半）", q4_0: "q4_0（再省一半）", f16: "f16（不省）" },
    cache_type_v: { q8_0: "q8_0（省一半）", q4_0: "q4_0（再省一半）", f16: "f16（不省）" },
    flash_attn: { on: "开", off: "关" },
    fit_on: { on: "开", off: "关" },
    mtp: { auto: "自动", on: "开", off: "关" },
    wash: { true: "开", false: "关" },
    two_pass: { true: "开", false: "关" },
    release_after_run: { true: "开", false: "关" },
};

// ---------------------------------------------------------------------------
// 词表 4 · 悬浮提示（**只存 en**）
// 键 = `class_type.参数名`；另有 `*.参数名` 通配（精确键优先）。
// 🔴 中文的唯一来源是 `nodes.py` 的 tooltip —— 别在这里加 zh。
// ---------------------------------------------------------------------------
export const TIPS = {
    // ===== 布局编辑器 =====
    "MossLayoutEditor.editor_state": {
        en: "[Do not type here] The editor's save file: the whole layout state as JSON.\n"
            + "Edit it on the node's canvas; this field updates itself.\n"
            + "Hand-editing works but the JSON must match schema v3, otherwise the node raises.",
    },
    "MossLayoutEditor.prompt": {
        en: "[Wire in, optional] External storyboard script: [STYLE] global description,\n"
            + "then one [PANEL n] description per panel.\n"
            + "Wired in, it takes over the per-panel descriptions (the editor's narrow boxes go read-only);\n"
            + "unplug it to edit by hand again.\n"
            + "A leading shot size (e.g. \"Medium shot, ...\") is used from the wire; if absent that panel gets no size clause.\n"
            + "Segment/panel count mismatches and dropped trailing segments are written to report, never silently.",
    },
    // ===== 宫格几何 =====
    "MossStoryboardLayout.prompt": {
        en: "[What to enter] Leave this empty. The storyboard script belongs in the Generate node.\n"
            + "This node only computes canvas/panel sizes and the grid template.",
    },
    "MossStoryboardLayout.panels": {
        en: "[What to enter] Number of panels: 3 / 4 / 6 / 8 / 9 are the common ones.\n"
            + "Any other count is factorised into rows x columns automatically.",
    },
    "MossStoryboardLayout.panel_aspect": {
        en: "[What to enter] Aspect ratio of ONE panel. 16:9 for landscape drama, 9:16 for vertical.",
    },
    "MossStoryboardLayout.panel_edge": {
        en: "[What to enter] Long edge of one panel in pixels. 496 gives a 1024x576 canvas for 4 panels.\n"
            + "Bigger values push the whole canvas out of the 1-2MP training range; small text breaks first.",
    },
    "MossStoryboardLayout.canvas_aspect": {
        en: "[What to enter] Target aspect of the WHOLE canvas. auto follows the panel orientation.\n"
            + "Same 6 panels: 16:9 lays out 3 columns x 2 rows, 9:16 lays out 2 x 3.",
    },
    "MossStoryboardLayout.columns": {
        en: "[Optional] 0 = automatic. Set it to force the column count (rows are derived).",
    },
    "MossStoryboardLayout.rows": {
        en: "[Optional] 0 = automatic. columns x rows must equal the panel count.",
    },
    "MossStoryboardLayout.gutter": {
        en: "[What to enter] Gap between panels in pixels. 0 = no inner line; the model may paint across cells.",
    },
    "MossStoryboardLayout.margin": {
        en: "[What to enter] Outer frame width. 0 means content touches the edge and crops pick up fringes.",
    },
    "MossStoryboardLayout.cell_gray": {
        en: "[Advanced] Grey level of the cells in the template. Near-black (25) is the stable choice;\n"
            + "mid-grey tends to make the final image look washed out.",
    },
    "MossStoryboardLayout.invert": {
        en: "[Advanced] Invert the template (black lines on white). Only if the model ignores white gutters.",
    },
    "MossStoryboardLayout.label": {
        en: "[Advanced] Type of the layout boxes. object treats a cell as a scene region; character emphasises a person.",
    },
    "MossStoryboardLayout.char_boxes": {
        en: "[Optional] Which cells should be character boxes, comma separated, **1-based** (e.g. `1, 3`).\n"
            + "Empty = every cell uses the label above. Handy when one board mixes hero shots and empty plates.",
    },

    // ===== 生成 =====
    "MossStoryboardSampler.model": {
        en: "[Wiring] MOSS-Image model from UNETLoader (a LoRA may sit in between).",
    },
    "MossStoryboardSampler.clip": {
        en: "[Wiring] CLIPLoader with type = moss_image.\n"
            + "⚠ A wrong type fails silently: you get a wrong embedding, not an error.",
    },
    "MossStoryboardSampler.vae": {
        en: "[Wiring] FLUX.2 VAE - it is the latent space MOSS works in.\n"
            + "Decode precision is decided by whichever VAE loader you use; this node does not touch it.\n"
            + "⚠ Running it in fp32 makes the host's decode memory estimate 4x larger (silently),\n"
            + "which can trigger tiled decoding. Use fp16 / bf16 to keep memory down.",
    },
    "MossStoryboardSampler.positive": {
        en: "[What to enter] The storyboard script: one global paragraph, then [PANEL 1] ... [PANEL n].\n"
            + "Fewer sections than cells = the rest stay unconstrained; extra sections are dropped (see report).",
    },
    "MossStoryboardSampler.negative": {
        en: "[Optional, under Advanced] Negative prompt. Empty by default.\n"
            + "The MOSS-Image-SFT model card uses cfg 3 (> 1), so a non-empty value does enter the graph;\n"
            + "only at cfg = 1.0 does the host skip the uncond branch entirely.",
    },
    "MossStoryboardSampler.steps": {
        en: "[What to enter] Sampling steps. The default 50 is the MOSS-Image-SFT model card setting\n"
            + "(50 steps / cfg 3 / euler / shift 5 / SLG negative-branch layer 22).\n"
            + "· Official Base model card: the same 50 steps / cfg 3 / euler (cfg range 3-6);\n"
            + "· With a 4-step distilled LoRA: 4-8 steps / cfg 1.0 - higher values break the distillation.",
    },
    "MossStoryboardSampler.cfg": {
        en: "[What to enter] Guidance scale. The default 3.0 is the MOSS-Image-SFT model card setting.\n"
            + "· Official Base model card: cfg 3 (official range 3-6);\n"
            + "· With the 4-step distilled LoRA: 1.0.\n"
            + "⚠ At 1.0 the host skips the uncond branch entirely, so a negative prompt does nothing.",
    },
    "MossStoryboardSampler.sampler_name": {
        en: "[What to enter] euler is the MOSS-Image-SFT model card default; the ComfyUI native template uses er_sde / 25 steps (Comfy-Org's choice).",
    },
    "MossStoryboardSampler.scheduler": {
        en: "[What to enter] sgm_uniform matches the official OpenMOSS ComfyUI plugin workflow; the model card does not specify a scheduler.",
    },
    "MossStoryboardSampler.seed": {
        en: "[What to enter] Random seed. Keep the control on randomize unless you need to reproduce a frame.",
    },
    "MossStoryboardSampler.denoise": {
        en: "[Keep at 1.0] Denoise strength. 1.0 = generate from pure noise, which is the normal path here.\n"
            + "⚠ There is no img2img: values below 1.0 do not reference any image, they only make the host\n"
            + "truncate sigmas via int(steps/denoise) - i.e. you run fewer steps. Lower steps directly instead.",
    },
    "MossStoryboardSampler.spec": {
        en: "[Wiring] Geometry from the Layout node. When wired, the geometry widgets below are ignored.",
    },
    "MossStoryboardSampler.panels": { en: "[Used only without spec] Panel count." },
    "MossStoryboardSampler.panel_aspect": { en: "[Used only without spec] Aspect ratio of one panel." },
    "MossStoryboardSampler.panel_edge": { en: "[Used only without spec] Long edge of one panel." },
    "MossStoryboardSampler.canvas_aspect": { en: "[Used only without spec] Target canvas aspect." },
    "MossStoryboardSampler.gutter": { en: "[Used only without spec] Gap between panels." },
    "MossStoryboardSampler.margin": { en: "[Used only without spec] Outer frame width." },
    "MossStoryboardSampler.init": {
        en: "[What to enter] How the prompt is sent.\n"
            + "auto / layout = panel captions are turned into native JSON layout boxes (recommended);\n"
            + "plain = plain text, no layout boxes: whole-canvas mode sends the prompt as-is"
            + " (like plain txt2img); per-panel mode sends the global section (multi-panel"
            + " wording stripped) plus that panel's caption.\n"
            + "⚠ auto/layout reconciles first: the number of [PANEL n] sections and any"
            + " \"N panels\" in the global section must equal the actual panel count; a"
            + " mismatch stops before sampling (no auto-fill). Choose plain to bypass.\n"
            + "⚠ This pack does not do img2img: the latent starts empty, MOSS does not condition on reference images.",
    },
    "MossStoryboardSampler.mode": {
        en: "[What to enter] How the panels are produced.\n"
            + "Whole grid (default) = one sampling pass for the whole board: fast, and the same face\n"
            + "is more stable across panels.\n"
            + "Per panel = every panel is sampled on its own and then assembled: N times slower,\n"
            + "but each panel gets its own pixels (clothes / props / text come out clearer) and can be\n"
            + "redone alone - at the cost of each panel drawing its own face.\n"
            + "⚠ Do not pick by \"which one is more consistent\": measured ArcFace, same woman,\n"
            + "whole grid 0.553 > per panel 0.479 (relative only - no threshold, no pass mark).\n"
            + "One-shot board -> whole grid. Per-panel rework -> per panel.",
    },
    "MossStoryboardSampler.sample_edge": {
        en: "[What to enter] Per-panel mode only: the **absolute long-edge pixels** each panel is\n"
            + "sampled at, then downscaled into its final cell. 1024 = sample at a 1024 px long edge.\n"
            + "It is a pixel count, NOT a multiplier of the final cell size.\n"
            + "⚠ 1024 is the factory starting point, not a measured optimum: the scan covered\n"
            + "496 (soft) / 992 (sharp) / 1984 (looser composition), not 1024.\n"
            + "Cost: VRAM and time scale with the pixel count. Ignored in whole-grid mode.",
    },
    "MossStoryboardSampler.label": { en: "[Advanced] Layout box type, same as the Layout node." },
    "MossStoryboardSampler.reborder": {
        en: "[Keep on] Redraw the gutters from the layout coordinates after decoding.\n"
            + "Off means trusting the model to paint straight lines - in practice they bend and break.",
    },
    "MossStoryboardSampler.border_px": {
        en: "[Optional] Width of the redrawn lines. 0 = fill the whole gutter.",
    },
    "MossStoryboardSampler.shave": {
        en: "[Optional] Inset each panel when cropping. Lines are already redrawn inside the gutter, so 0 is normal.",
    },

    // ===== 裁格 =====
    "MossStoryboardCrop.image": { en: "[Wiring] The full grid image (KSampler -> VAEDecode)." },
    "MossStoryboardCrop.target_aspect": {
        en: "[What to enter] keep = panel aspect unchanged (no resampling, recommended).\n"
            + "Any other value centre-crops, which is not equal-area - use with care.",
    },
    "MossStoryboardCrop.padding_mode": {
        en: "[What to enter] crop = cut the extra pixels; pad = add black bars instead of losing content.",
    },
    "MossStoryboardCrop.resize_edge": {
        en: "[Optional] 0 keeps the native panel size (recommended). Above 0 rescales each panel's long edge.",
    },
    "MossStoryboardCrop.shave": {
        en: "[Optional] Inset each panel to eat fringe lines that bled into the cell.",
    },
    "MossStoryboardCrop.spec": {
        en: "[Wiring] Spec from the Layout/Sampler node - the only way to guarantee the crop matches generation.",
    },
    "MossStoryboardCrop.columns": {
        en: "[Required without spec] Column count. 0 with no spec raises an error instead of guessing.",
    },
    "MossStoryboardCrop.rows": { en: "[Required without spec] Row count." },
    "MossStoryboardCrop.show_guides": {
        en: "[Debug] Draw the crop lines in red on the preview output to eyeball alignment.",
    },
    "MossStoryboardCrop.index": {
        en: "[Optional] -1 outputs every panel (default, unchanged behaviour).\n"
            + "A 0-based panel number outputs just that one and emits it on the index output,\n"
            + "which wires straight into MossStoryboardPaste - no manual counting, no mismatch.",
    },
    "MossStoryboardPaste.grid": {
        en: "[Wiring] The full grid image (Sampler's grid output, or an earlier Paste's output).",
    },
    "MossStoryboardPaste.panel": {
        en: "[Wiring] The corrected panel (single image). Crop with index set is the safest source.",
    },
    "MossStoryboardPaste.index": {
        en: "[Enter / wire] Which panel to write into - 0-based, row-major (left to right, top to bottom).\n"
            + "Wiring Crop's index output overrides this widget, so it can never disagree with what you cut.",
    },
    "MossStoryboardPaste.shave": {
        en: "[Optional] Must match the shave you used on Crop. Cutting inset s pixels, so pasting\n"
            + "writes into the inset rectangle; a mismatch shifts content by s pixels without erroring.",
    },
    "MossStoryboardPaste.show_guides": {
        en: "[Debug] Frame the pasted region in red on the marked output to confirm which cell was hit.",
    },
    "MossStoryboardPaste.spec": {
        en: "[Wiring] Spec from the Layout/Sampler node - the only way to guarantee the paste matches generation.",
    },
    "MossStoryboardPaste.columns": {
        en: "[Required without spec] Column count. 0 with no spec raises an error instead of guessing.",
    },
    "MossStoryboardPaste.rows": { en: "[Required without spec] Row count." },
    "MossPromptCompiler.draft": {
        en: "[What to enter] A draft from the official MOSS-Image Prompt Enhancer (not an already-compiled prompt).\n"
            + "caption: {\"caption\": \"...\"}; layout: width / height / bbox_coord_space=\"abs\" / bbox_format=\"xyxy\" / summary / items.\n"
            + "The prompt output wires into CLIPTextEncode or the Sampler positive; a layout draft also outputs\n"
            + "state v3, which can be pasted into the Layout Editor import box.\n"
            + "⚠ At most 256 items; unknown fields, out-of-bounds boxes and canvases not divisible by 16 are rejected.",
    },
    "MossStoryboardFaceSwap.model": {
        en: "[Wire] MOSS-Image model. Production recipe: the 8-step DMD2 distil LoRA first, then the Moss-Image-Edit LoRA.",
    },
    "MossStoryboardFaceSwap.clip": {
        en: "[Wire] CLIPLoader with type = moss_image. The reference image enters the Qwen3.5 vision tower through here.",
    },
    "MossStoryboardFaceSwap.vae": {
        en: "[Wire] FLUX.2 VAE. The source image is encoded to a latent with it (img2img) and the result is decoded with it.",
    },
    "MossStoryboardFaceSwap.image": {
        en: "[Wire] The image to swap a face in. With spec connected, feed the whole board and the node crops panel `index` itself;\n"
            + "without spec the whole image is treated as one panel.",
    },
    "MossStoryboardFaceSwap.ref": {
        en: "[Wire] Identity reference, a tight close-up works best (the bigger the face in frame, the better).\n"
            + "⚠ One image only: two views measured worse than one (0.384 vs 0.463 — the vision tokens get diluted).",
    },
    "MossStoryboardFaceSwap.prompt": {
        en: "[What to enter] The scene to repaint, in English, and it must mention the reference, e.g.\n"
            + "\"A medium shot photograph of the man in Picture 1, sitting at a wooden table in a warm cafe.\"\n"
            + "⚠ Do not type the `Picture 1:` prefix yourself — the node adds it.",
    },
    "MossStoryboardFaceSwap.steps": {
        en: "[What to enter] 8 = the 8-step DMD2 distil LoRA caliber. Without that LoRA use the model card's 50.",
    },
    "MossStoryboardFaceSwap.cfg": {
        en: "[What to enter] 1.0 for the distil recipe. At cfg = 1.0 the host skips the negative branch entirely.",
    },
    "MossStoryboardFaceSwap.sampler_name": {
        en: "[What to enter] euler is the measured recipe.",
    },
    "MossStoryboardFaceSwap.scheduler": {
        en: "[What to enter] simple is the measured recipe for the 8-step tier.",
    },
    "MossStoryboardFaceSwap.seed": {
        en: "[What to enter] Same seed plus the same settings is the only way to reproduce an image.",
    },
    "MossStoryboardFaceSwap.denoise": {
        en: "[What to enter] How much gets repainted.\n"
            + "· 0.95 (default) = the measured recipe, and at 8 steps it **equals 1.0**: the source image is discarded\n"
            + "  entirely, composition comes from the prompt and identity from the reference.\n"
            + "· ≤0.875 is what actually keeps the source composition at 8 steps (int(8/0.875)=9>8 triggers img2img).\n"
            + "⚠ The host truncates sigma by int(steps/denoise), so 0.875–0.99 all behave as 1.0 at 8 steps.\n"
            + "`report` prints the value that really took effect.",
    },
    "MossStoryboardFaceSwap.vision_megapixels": {
        en: "[What to enter] Pixel budget per reference image before it enters the vision tower.\n"
            + "· 1.05 (default) = the measured production value, about 1024x1025\n"
            + "· 0 = native resolution, never scaled\n"
            + "· 0.59 = 768 squared, the measured safety line\n"
            + "⚠ 0.147 is the host default, where identity cosine is about 0.008; 0.30 turns the person into someone else.",
    },
    "MossStoryboardFaceSwap.spec": {
        en: "[Wire] The spec from Layout/Sampler: feed the whole board plus `index` and only that panel is swapped.\n"
            + "Without it the image is treated as a single panel.",
    },
    "MossStoryboardFaceSwap.index": {
        en: "[Used when spec is connected] Which panel to swap, 0-based, row-major.",
    },
    "MossStoryboardFaceSwap.sample_edge": {
        en: "[1024 is fine] Long edge each panel is upscaled to before sampling, then scaled back into its cell.\n"
            + "On a 4-panel 496-edge board one cell is only 0.135 MP and the face is about 50x27 px — too small to identify.\n"
            + "0 = no upscale (sample at the cell size, lowest VRAM).",
    },
    "MossStoryboardFaceSwap.auto_mask": {
        en: "[On by default] Finds the face with insightface and builds the mask for you.\n"
            + "Needs `pip install insightface onnxruntime`; if it is missing the node says so instead of guessing.\n"
            + "A wired `mask` takes priority over this.",
    },
    "MossStoryboardFaceSwap.face_pick": {
        en: "[Which face] When a panel has several people, name the one you want://n"
            + "largest = biggest face; left / right = leftmost / rightmost.\n"
            + "`report` prints how many faces were found and which one was used.",
    },
    "MossStoryboardFaceSwap.init": {
        en: "[Where to start] Face swap and generation are the same operation with one knob different:////n"
            + "source (default) = take `image` and do img2img. With a mask only the mask is redrawn (face swap).\\n"
            + "empty = do NOT take `image`; start from an empty latent and build the whole frame from\\n"
            + "the prompt + the reference (this is the \"reference face -> one posed image\" path).\\n"
            + "Under empty, denoise / sample_edge / auto_mask / face_pick / index do nothing; `report` says so.",
    },
    "MossStoryboardFaceSwap.width": {
        en: "[Only when init=empty and no spec is wired] Canvas width; must be a multiple of 16.\\n"
            + "With a spec wired the canvas comes from the spec and this value is ignored.",
    },
    "MossStoryboardFaceSwap.height": {
        en: "[Only when init=empty and no spec is wired] Canvas height; must be a multiple of 16.",
    },
    "MossStoryboardFaceSwap.mask": {
        en: "[Wire, strongly recommended] Only the masked region is repainted; everything outside stays pixel-identical.\n"
            + "Without it the whole panel is repainted and the colour grade always drifts (measured R +24 to +53).\n"
            + "Do NOT use LoadImage's MASK output: that is the alpha channel, and a grey PNG without alpha returns 64x64 zeros,\n"
            + "so the mask silently does nothing. Use ImageToMask(channel=\"red\"). Feathering avoids a visible seam.",
    },
    "MossStoryboardFaceSwap.negative": {
        en: "[Optional] Negative prompt. At cfg = 1.0 the host skips the negative branch, so this does nothing.",
    },

    // ===== LLM 子包 · LLM 提示词 =====
    // 中文唯一源 = `moss_llm/nodes.py` 的 tooltip；这里只写 en，按键逐条对齐。
    "MossPromptSmith.task": {
        en: "[What to run] caption = look at the image and write the prompt (image required);\n"
            + "expand = turn your idea into a full scene description (text required);\n"
            + "edit = one instruction for image-to-image (image + what to change);\n"
            + "compile = render an IR into a backend dialect (NO model, milliseconds, reproducible --\n"
            + "paste the IR into the text box, pick the direction with target).",
    },
    "MossPromptSmith.channel": {
        en: "[Where the model runs] local = start a llama-server sidecar (no network, VRAM freed after the run);\n"
            + "api = any OpenAI-compatible endpoint (OpenAI / Volcengine Ark / Zhipu / Kimi / MiniMax /\n"
            + "DeepSeek / Ollama / LM Studio ...): no sidecar, no VRAM, but images and text leave the machine.",
    },
    "MossPromptSmith.dialect": {
        en: "[Output dialect] moss_image = MOSS single-image English prose (official default);\n"
            + "moss_storyboard = MOSS board Chinese [STYLE] + [PANEL n] (the format verified in this repo;\n"
            + "panels must equal the number of sections); generic_* for Flux/Krea/SDXL; cn_natural for\n"
            + "Seedream / Jimeng / Kling / Vidu.",
    },
    "MossPromptSmith.model": {
        en: "[Pick a file] Every GGUF under ComfyUI/models/LLM and models/text_encoders, shown by relative path;\n"
            + "any architecture llama.cpp supports. Captioning needs vision: either a main model + mmproj,\n"
            + "or a single VL model.",
    },
    "MossPromptSmith.text": {
        en: "[What to enter] expand = your idea (fixed facts go in must_keep, not here);\n"
            + "edit = what to change; compile = an IR (JSON or YAML, or a .json/.yaml file path).\n"
            + "Leave empty for caption.",
    },
    "MossPromptSmith.target": {
        en: "[compile only] Which backend to render for. moss-image = MOSS single image;\n"
            + "qwen-image-2_1 / krea2 / flux3 / flux1_dev / sdxl are their own dialects;\n"
            + "minimax-h3 is video (the IR needs a timeline). Compiling is deterministic Python:\n"
            + "no model is loaded, it takes milliseconds, and the same IR always gives the same text.",
    },
    "MossPromptSmith.length": {
        en: "[What to enter] brief 80-120 words / standard 160-260 / detailed 280-360, with a per-dialect cap.\n"
            + "Caption favours stable wording, expand favours variation.",
    },
    "MossPromptSmith.temperature": {
        en: "[What to enter] 0.2 for caption (stable, reproducible); 0.7 for expand (more varied).\n"
            + "Same seed + prompt + model reproduces.",
    },
    "MossPromptSmith.max_tokens": {
        en: "[What to enter] Output cap. 512-1024 for caption; 768-1024 for expand.\n"
            + "A long system prompt plus strict JSON can hit the cap occasionally; leave headroom.",
    },
    "MossPromptSmith.seed": {
        en: "[What to enter] Same seed + prompt + model reproduces. Changing the seed only changes sampling,\n"
            + "not what the prompt says.",
    },
    "MossPromptSmith.image": {
        en: "[Wire] Required for caption; optional reference for expand; required for edit.",
    },
    "MossPromptSmith.mmproj": {
        en: "[Vision tower] Required whenever an image goes in (without it the model cannot see the image).\n"
            + "auto = match by width (model embedding_length == tower projection_dim) in the model's folder\n"
            + "and both scan roots; several matches raise instead of guessing. none = never load an mmproj\n"
            + "even if one exists. MOSS TE pairs with Qwen3.5-9B.mmproj-Q8_0.gguf.",
    },
    "MossPromptSmith.language": {
        en: "[What to enter] Language of the prose description. Text inside the image keeps its own language.",
    },
    "MossPromptSmith.thinking": {
        en: "[Keep off] Thinking mode. Measured: with it on the answer goes to reasoning_content, content is\n"
            + "empty and finish=length, so the prompt comes back blank. Only enable for models that must\n"
            + "reason first, and raise max_tokens at the same time.",
    },
    "MossPromptSmith.panels": {
        en: "[moss_storyboard only] Panel count declared in the global section. The number of [PANEL] sections\n"
            + "must equal it; report warns on a mismatch and never edits silently.",
    },
    "MossPromptSmith.style_lock": {
        en: "[What to enter] Character / clothing / environment / medium lock. In a board script a character is\n"
            + "defined once here; each panel only states the difference (repeating the full description\n"
            + "squeezes out the shot-size clause).",
    },
    "MossPromptSmith.must_keep": {
        en: "[What to enter] Exact words / numbers / colours / positions the user pins, semicolon separated.\n"
            + "They must appear verbatim; expand may not change them. report checks each one.",
    },
    "MossPromptSmith.wash": {
        en: "[Caption only] Ignore compression noise / watermarks / UI clutter that are not design defects,\n"
            + "and list them under omitted_artifacts (never silently dropped).",
    },
    "MossPromptSmith.two_pass": {
        en: "[Caption only] Inventory first, then write the prose. Use it when cleaning up an image or when\n"
            + "fidelity matters; one pass is faster, two passes are steadier (both calls reuse one sidecar).",
    },
    "MossPromptSmith.runtime": {
        en: "[What to enter] current = find llama-server via server path / env var / this package's runtime / PATH;\n"
            + "update = download and install the latest official llama.cpp release from GitHub first\n"
            + "(CUDA/ROCm/Vulkan/SYCL/Metal/CPU picked for this machine; MOSS_LLM_MIRROR for a slow network,\n"
            + "MOSS_LLM_BACKEND to override), then use it.",
    },
    "MossPromptSmith.api_base": {
        en: "[api channel] OpenAI-compatible endpoint. Either https://host/v1 or the full .../v1/chat/completions\n"
            + "works. Examples: https://api.openai.com/v1, https://ark.cn-beijing.volces.com/api/v3,\n"
            + "http://127.0.0.1:11434/v1 (Ollama), http://127.0.0.1:1234/v1 (LM Studio).\n"
            + "Required when channel=api.",
    },
    "MossPromptSmith.api_model": {
        en: "[api channel] Model id as the provider names it. Required when channel=api; ignored on the local channel.",
    },
    "MossPromptSmith.api_key": {
        en: "[api channel] API key. NOTE: typed here it is saved into the workflow JSON and shared with it;\n"
            + "to share a workflow, clear this and set the MOSS_LLM_API_KEY env var instead.",
    },
    "MossPromptSmith.ctx_size": {
        en: "[Local engine] Caption prompts carry many image tokens; 8192 or more is recommended.\n"
            + "Bigger values cost more VRAM (KV cache).",
    },
    "MossPromptSmith.gpu_layers": {
        en: "[Local engine] Empty = auto (llama.cpp computes layers from free VRAM, together with fit);\n"
            + "a number = how many layers to put on the GPU.",
    },
    "MossPromptSmith.cache_type_k": {
        en: "[Local engine] K cache quantisation: q8_0 roughly halves it, q4_0 halves it again\n"
            + "(slight quality loss).",
    },
    "MossPromptSmith.cache_type_v": {
        en: "[Local engine] V cache quantisation, same choices as K. Usually keep it equal to K.",
    },
    "MossPromptSmith.flash_attn": {
        en: "[Local engine] Cuts KV VRAM and speeds things up; on by default.",
    },
    "MossPromptSmith.image_max_tokens": {
        en: "[Local engine] Maximum vision tokens per reference image - the biggest VRAM variable when\n"
            + "several images go in.",
    },
    "MossPromptSmith.ubatch": {
        en: "[Local engine] Tokens per forward batch; sets peak VRAM and speed. Drop to 256 if VRAM runs out.",
    },
    "MossPromptSmith.parallel": {
        en: "[Local engine] Concurrent requests; the KV cache grows by the same factor. Keep 1 for single-user prompting.",
    },
    "MossPromptSmith.fit_on": {
        en: "[Local engine] llama.cpp native VRAM fitting: squeeze usage under (total VRAM - reserve) and\n"
            + "offload layers that do not fit to the CPU. Keep it on for a 12 GB card shared with ComfyUI.",
    },
    "MossPromptSmith.fit_target_mib": {
        en: "[Local engine] VRAM reserve for the fit target (room left for ComfyUI sampling). Default 1024 MiB.",
    },
    "MossPromptSmith.mtp": {
        en: "[Local engine] auto = read GGUF metadata for nextn/mtp (never the file name - the dhq build has\n"
            + "nextn without -mtp in its name); on = force it (falls back with a warning if unsupported);\n"
            + "off = disable. Measured 1.48-1.64x on the same target; DFlash accepts only 10-31% for MOSS\n"
            + "targets, so it is not used.",
    },
    "MossPromptSmith.mtp_draft_n": {
        en: "[Local engine] --spec-draft-n-max for draft-mtp. Default 2.",
    },
    "MossPromptSmith.server_exe": {
        en: "[Local engine] Full path to llama-server. Empty = search env MOSS_LLM_SERVER >\n"
            + "this package's moss_llm/runtime/ > system PATH.",
    },
    "MossPromptSmith.port": {
        en: "[Local engine] Defaults to 8091, away from the 8080 that llama.cpp servers usually take.\n"
            + "An externally occupied port is reused, never killed.",
    },
    "MossPromptSmith.release_after_run": {
        en: "[Local engine] On (default): terminate llama-server and free VRAM after the run.\n"
            + "Off: keep the service for faster repeat calls, but it holds VRAM - release it by running this\n"
            + "node again or restarting before sampling.",
    },
};

// ---------------------------------------------------------------------------
// 纯函数
// ---------------------------------------------------------------------------

/** 该 class_type 的两种语言默认标题。 */
export function defaultTitles(type) {
    const c = NODES[String(type || "")];
    if (!c) return [];
    return [c.zh, c.en, c.legacy].filter(Boolean);
}

/** 标题当前是否仍是默认名（空标题按默认处理）。 */
export function isDefaultTitle(title, type) {
    const t = String(title == null ? "" : title);
    if (!t) return true;
    return defaultTitles(type).some((d) => t === d || t.startsWith(d));
}

/** 换标题，**只换前缀、保留后缀**（与其它模块追加的提示共存）。 */
export function swapTitle(title, type, lang) {
    const c = NODES[String(type || "")];
    if (!c) return title;                       // 未知类型 ⇒ 不碰
    const t = String(title == null ? "" : title);
    const L = normLang(lang);
    if (!t) return c[L];
    for (const d of defaultTitles(type)) {
        if (t === d) return c[L];
        if (t.startsWith(d)) return c[L] + t.slice(d.length);
    }
    return t;                                   // 自定义标题 ⇒ 一个字都不动
}

/** 枚举值 → 显示文本（白名单外返回 null ⇒ 显示原值）。 */
export function comboLabel(widgetName, value, lang) {
    const m = COMBOS[String(widgetName || "")];
    if (!m) return null;
    const hit = m[String(value)];
    if (hit == null) return null;
    return normLang(lang) === "zh" ? hit : String(value);
}

/** 给一个下拉 widget 装/卸中文显示钩子（英文态删钩子 ⇒ 回落原值）。 */
export function applyCombo(node, widgetName, lang) {
    for (const w of ((node && node.widgets) || [])) {
        if (!w || w.name !== widgetName) continue;
        if (!w.options || !Array.isArray(w.options.values)) continue;
        if (normLang(lang) === "zh") {
            w.options.getOptionLabel = (v) => (v == null ? "" : (comboLabel(widgetName, v, "zh") ?? String(v)));
        } else {
            try { delete w.options.getOptionLabel; } catch (e) { /* 只读对象 */ }
        }
    }
}

/** 提示查找（精确键优先，再退 `*.参数名` 通配）。 */
export function pickTip(type, widgetName) {
    const t = String(type || "");
    const n = String(widgetName || "");
    if (!t || !n) return null;
    const e = TIPS[t + "." + n] || TIPS["*." + n];
    return (e && e.en) ? e : null;
}

/** 该参数在当前语言下的提示；中文返回 null（唯一源在后端）。 */
export function tipFor(type, widgetName, lang) {
    const e = pickTip(type, widgetName);
    if (!e) return null;
    if (normLang(lang) !== "en") return null;   // 🔴 中文一律不写，回落 nodes.py 原文
    return e.en == null ? null : String(e.en);
}

/** 后端 RETURN_NAMES 是中文硬编码；英文界面在这里覆盖输出端口名。 */
const OUTPUT_LABELS = {
    MossPromptSmith: [{ zh: "提示词", en: "Prompt" }, { zh: "report", en: "Report" }],
};

/**
 * 把语言应用到节点：标题 + 参数标签 + 枚举显示 + 悬浮提示。
 * @returns {{title:boolean, labels:number, combos:number, tips:number}}
 */
export function applyLang(node, lang) {
    const out = { title: false, labels: 0, combos: 0, tips: 0, placeholders: 0, outputs: 0 };
    if (!node) return out;
    // 🔴 **只处理本包节点**。外来节点（官方 / 别人的）一个字节都不许动：
    //    同名参数（steps/cfg/seed…）在别人那里语义不同，改了 label 就是篡改别人的界面。
    //    这条不写在调用方，是因为调用方可能漏判（`nodeCreated` 对所有节点都触发）。
    if (!NODES[String(node.type || "")]) return out;
    const L = normLang(lang);

    // 输出端口名：后端 RETURN_NAMES 是中文；英文界面覆盖，中文还原。只处理本包节点。
    const outs = OUTPUT_LABELS[String(node.type || "")];
    if (outs) {
        for (let i = 0; i < outs.length; i += 1) {
            const o = (node.outputs || [])[i];
            if (!o) continue;
            const want = L === "en" ? outs[i].en : outs[i].zh;
            if (o.name !== want) { o.name = want; out.outputs += 1; }
        }
    }

    try {
        const t = swapTitle(node.title, node.type, L);
        if (t !== node.title) { node.title = t; out.title = true; }
    } catch (e) { /* 标题改不了不影响标签 */ }

    for (const w of (node.widgets || [])) {
        const name = w && w.name;
        if (!name) continue;

        // 标签：中文取 LABELS；英文优先 LABELS_EN，再回落 widget 原名
        const zh = (LABELS_NODE[node.type] || {})[name] || LABELS[name];
        const want = L === "zh" ? (zh || name) : (LABELS_EN[name] || name);
        if (w.label !== want) { w.label = want; out.labels += 1; }
        // 归属标记：界面汉化插件会按自己的词典改回中文，这行让兜底轮询能识别并写回。
        // ⚠ 只是内存标记，序列化只写 value ⇒ 不落进工作流 JSON。
        try { w.__mosssbWant = want; } catch (e) { /* 冻结对象 */ }

        // 🔴 多行文本框（DOM `<textarea class="comfy-multiline-input">`）的**标题其实是
        //    `placeholder`**，前端根本不读 `widget.label`。真机实测（前端 1.57.1）：
        //    `label` 已是「正面提示词」，界面上仍显示 `placeholder="positive"` —— 因为
        //    DOM 元素 `size-full` 把画布自绘的标题整个盖住了。
        //    ⇒ 只改 label 的写法对这两个框**永远无效**，必须同时写 placeholder。
        //    原始值先存起来：切英文时要能回滚成后端下发的那个（而不是写死参数名）。
        const el = w.element;
        if (el && String(el.tagName || "").toUpperCase() === "TEXTAREA") {
            try {
                if (w.__mosssbPlaceholder === undefined) {
                    w.__mosssbPlaceholder = el.getAttribute("placeholder") || name;
                }
                const ph = L === "zh" ? (zh || w.__mosssbPlaceholder)
                    : (LABELS_EN[name] || w.__mosssbPlaceholder);
                if (el.getAttribute("placeholder") !== ph) {
                    el.setAttribute("placeholder", ph);
                    out.placeholders = (out.placeholders || 0) + 1;
                }
                w.__mosssbWantPh = ph;
            } catch (e) { /* 无 element 的控件（纯画布自绘）走不到这里 */ }
        }

        // 枚举显示
        if (w.options && Array.isArray(w.options.values) && COMBOS[name]) {
            applyCombo(node, name, L);
            out.combos += 1;
        }

        // 悬浮提示：英文写 w.tooltip（覆盖后端原文），中文还原（删掉覆盖）
        try {
            const tip = tipFor(node.type, name, L);
            if (tip != null) {
                if (!w.__mosssbTipTouched) { w.__mosssbTipSaved = w.tooltip; w.__mosssbTipTouched = true; }
                if (w.tooltip !== tip) { w.tooltip = tip; out.tips += 1; }
                w.__mosssbWantTip = tip;
            } else if (w.__mosssbTipTouched) {
                // 切回中文：还原成"没覆盖过"的状态，前端自然回落后端原文
                if (w.__mosssbTipSaved === undefined) { try { delete w.tooltip; } catch (e) { w.tooltip = undefined; } }
                else w.tooltip = w.__mosssbTipSaved;
                delete w.__mosssbTipTouched;
                delete w.__mosssbTipSaved;
                delete w.__mosssbWantTip;
            }
        } catch (e) { /* 提示写不进去不影响其它 */ }
    }
    return out;
}

/** 过滤出本包的节点（画布上的 `node.type` 全名匹配）。 */
export function ownNodes(nodes) {
    return (nodes || []).filter((n) => n && NODES[String(n.type || "")]);
}

/** 选中语言存/取（localStorage 不可用时静默跳过）。 */
export function loadLang() {
    try { return normLang(globalThis.localStorage?.getItem(LANG_KEY) || ""); }
    catch (e) { return "en"; }
}

export function saveLang(lang) {
    try { globalThis.localStorage?.setItem(LANG_KEY, normLang(lang)); } catch (e) { /* 隐私模式 */ }
}
