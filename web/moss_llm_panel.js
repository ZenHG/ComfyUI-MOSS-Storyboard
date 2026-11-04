// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// LLM Prompt · 参数面板 —— 38 个参数的信息架构层
//
// 设计口径（美学 / 高频用户两条视角，2026-10-30）：
//   1. **默认只露常驻 5 项**（task/channel/dialect/model/text）。这个节点的日常动作是
//      「贴图/贴文本 → 跑」，其余 33 项是配置而不是操作。节点因此从 978 px 降到 306 px
//      （1500×1500 视口、80% 缩放实测；size 高 353，100% 缩放下盒高 383 px）。
//   0. **开关条钉在节点顶部**，点开任一组都在它**下方**展开 —— 控制件的坐标永远不变
//      （旧版在底部，每展开一组它就往下跑一截，2026-10-30 实测点名）。
//   2. **一次只看一组**。点「常用调优」只看调优，点「本地引擎」只看引擎 —— 不叠。
//      理由：参数顺序是存档契约，改不了；`mmproj`/`runtime` 夹在别的组中间，
//      多组同时展开时标题只能来回重画（上一版全展开里「本地引擎」出现了 3 次）。
//      单组视图让同组行在视觉上连成一片，标题只出现一次。
//   3. **「已改」小点**：折叠着的组里只要有一项与默认不同，chip 上点一个小蓝点
//      （悬停看条数）。高频用户最怕「我改过的东西被藏起来了」。
//   4. 视觉只用宿主 CSS 变量；置顶 order 与临时网格样式需 !important。
//      开关条透明底 + 上边一道发丝线，读起来是工具栏而不是第 39 个参数。
//   5. **说明行高度恒定**（`height:2.7em`）：悬停换 tooltip 时行数变化不再顶动下面的参数
//      （2026-10-30 实测：这是鼠标定位失败的直接原因）。「中/EN」按钮挂在说明行右端的
//      `langSlot`（核心 i18n 画），不另开 DOM 行；切语言时面板文案跟着一起换。
//
// 🔴 一条都不许碰存档。`widgets_values` 按**位置**存（CONTRIBUTING 硬规矩 1、docs/10 §三·4）。
//    这里只改 `widget.options.hidden`（宿主的 byExtension 抑制位）+ 给宿主的行元素加类名。
//    实测前端 1.57.6 / Vue 节点模式：隐藏的行不画、`serialize()` 照样按 `serialize !== false`
//    收值 ⇒ 隐藏不改变存档格数与位置；折叠状态只活在节点实例的 `__mossllm` 上，不序列化。
//    DOM 开关条那一格由 `moss_sb_widget_order.js::installSaveCleanup` 在存盘时剔掉（实测 38 格）。
//    **唯一会写进 JSON 的东西是节点 `size`**（`fitHeight` 按可见内容调高度）—— 那是画布尺寸，
//    不是参数值；改分组时会重算，用户手拖的高度在下次换组时被取代（可见、可撤销）。
//
// 为什么不用宿主自带的「显示高级参数」：它只有一个桶，切不出「本地引擎 / 外部 API」两组，
//    也承载不了「默认只露 5 项」这件事。（`advanced` 在各渲染层的实际落点随前端版本变，
//    这里不依赖它。）
//
// 为什么只挂**一个** DOM 行：组标题要插在两组参数之间，就得往 `node.widgets` 中间塞东西
//    —— 那是重排 widget = 改存档顺序。所以组标题改由 CSS 画在每组第一行上方
//    （`.mossllm-head::before`），行元素用宿主自己打的 `data-widget-name` 定位。
//
// 🔴 不静默兜底：拿不到隐藏能力、或挂不上这一行 ⇒ **什么都不藏**（只出声）——
//    「藏了却没有开关」等于把参数弄丢。接了线的参数永远不藏。

import { app } from "../../scripts/app.js";
import { installSaveCleanup } from "./moss_sb_widget_order.js";
import { LANG_EVENT, LANG_KEY, normLang } from "./moss_sb_i18n.js";

const TAG = "[MOSS SB] llm panel";
const TYPE = "MossPromptSmith";
const BAR_NAME = "mosssb_llm_panel";
const STYLE_ID = "mosssb-llm-panel-style";
const CH_LOCAL = "local";
const CH_API = "api";
/** 初版把中文写进了枚举值（2026-10-30 早场）。旧工作流里还是这两个 —— 两边都认。 */
const CH_LEGACY = { "本地 GGUF": CH_LOCAL, "外部 API": CH_API };
const chanOf = (v) => CH_LEGACY[v] || v;

/**
 * 信息架构：按**参数名**分组（名字是契约，位置不是）。表里没写的参数落进 `other` 且常显
 * —— 以后往参数表尾部追加新旋钮时，默认是「看得见」，不会因为这张表没同步就被藏掉。
 */
const GROUPS = [
    { key: "core", always: true, items: ["task", "channel", "dialect", "model", "text"] },
    { key: "tune", items: ["length", "temperature", "max_tokens", "seed", "control_after_generate",
        "language", "thinking", "panels", "style_lock", "must_keep", "wash", "two_pass",
        // 编译目标放 tune 而不是 core：core 是「常驻 5 行」的口径（见 docs/20 §九），
        // 多一项就把默认视图改成 6 行。它只在 task=compile 时有意义。
        "target"] },
    { key: "local", channel: CH_LOCAL, items: ["mmproj", "runtime", "ctx_size", "gpu_layers",
        "cache_type_k", "cache_type_v", "flash_attn", "image_max_tokens", "ubatch", "parallel",
        "fit_on", "fit_target_mib", "mtp", "mtp_draft_n", "server_exe", "port", "release_after_run"] },
    { key: "api", channel: CH_API, items: ["api_base", "api_model", "api_key"] },
];
const OTHER = { key: "other", always: true };
const BY_NAME = new Map();
for (const g of GROUPS) for (const n of g.items) BY_NAME.set(n, g);

/** seed 每次跑都会变、「生成后控制」是它的伴侣 —— 拿它们算「已改」只会永远亮着。 */
const DIRTY_SKIP = new Set(["seed", "control_after_generate"]);

// 全走宿主 CSS 变量；置顶 order 与临时网格样式例外。DOM 顺序 ≠ 视觉顺序，见 fitHeight。
// `flex:0 0 auto` 必须有：宿主给 DOM 组件的单元格套了 `*:flex-1`（flex-grow:1），
// 不覆盖它，开关条就会被撑到整个剩余高度，线条被 align-content 摊开成一列散字（实测 434px）。
const CSS = `
/* server_exe 在参数表尾部：宿主每行包了一层 wrapper，order 要打在 wrapper 上。
   把它的 wrapper 排到最前（但仍低于开关条），避免用户漏看；只改视觉顺序，不动存档。 */
[data-widgets-grid-node-id] > div:has([data-widget-name="server_exe"]){order:-1 !important}
[data-widgets-grid-node-id] > div:has([data-widget-name="mosssb_llm_panel"]){order:-2 !important}
.mossllm-bar{display:flex;flex-wrap:wrap;align-content:flex-start;flex:0 0 auto;
 align-items:center;gap:2px 4px;
 box-sizing:border-box;width:100%;padding:6px 2px 1px;
 border-top:1px solid var(--border-color,#4e4e4e);
 color:var(--descrip-text,#999);font:11px/1.5 system-ui,sans-serif}
.mossllm-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border-radius:7px;
 cursor:pointer;border:1px solid transparent;background:transparent;
 color:var(--input-text,#ddd);font:inherit;white-space:nowrap}
.mossllm-chip[hidden]{display:none}
.mossllm-chip:hover{background:var(--comfy-input-bg,#222)}
.mossllm-chip:focus-visible{outline:1px solid var(--border-color,#4e4e4e);outline-offset:1px}
.mossllm-chip[data-state="open"]{border-color:var(--border-color,#4e4e4e);background:var(--comfy-input-bg,#222)}
.mossllm-chip[data-state="closed"]{color:var(--descrip-text,#999)}
.mossllm-chip[data-state="outside"]{color:var(--descrip-text,#999);opacity:.65}
/* 「端点已填，但通道=本地」：这不是提示语，是当前状态 —— 用琥珀色块钉在开关条上，
   而不是缩成摘要里的一小段灰字（实测那样等于没看见）。点它即可切到外部 API。 */
.mossllm-warn{display:inline-flex;align-items:center;gap:5px;max-width:100%;padding:2px 8px;
 border-radius:7px;border:1px solid #b8860b;background:rgba(217,164,65,.16);color:#e8b64c;
 font:600 11px/1.5 system-ui,sans-serif;cursor:pointer;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis}
.mossllm-warn[hidden]{display:none}
.mossllm-warn:hover{background:rgba(217,164,65,.28)}
.mossllm-caret{width:9px;opacity:.55;font-size:11px;line-height:1}
/* 三个 chip 并排，但「常用调优」是参数分组、「本地引擎 / 外部 API」是通道 —— 不是同一维度。
   给通道两个加前缀小字，别让人以为调优属于本地引擎。 */
.mossllm-kind{font-size:9.5px;line-height:1;opacity:.45;margin-right:3px;letter-spacing:.04em}
/* 一次性预设：只在「常用调优」展开时出现。按钮不是 widget ⇒ 不进 widgets_values。 */
.mossllm-presets{display:inline-flex;align-items:center;gap:3px;margin-left:2px}
.mossllm-presets[hidden]{display:none}
.mossllm-preset{padding:0 7px;border-radius:6px;border:1px dashed var(--border-color,#4e4e4e);
 background:transparent;color:var(--input-text,#ddd);font:11px/1.7 system-ui,sans-serif;cursor:pointer}
.mossllm-preset:hover{background:var(--comfy-input-bg,#222)}
.mossllm-preset:focus-visible{outline:1px solid var(--border-color,#4e4e4e);outline-offset:1px}
.mossllm-count{opacity:.55;font-variant-numeric:tabular-nums}
.mossllm-dot{display:none;width:6px;height:6px;margin-left:2px;border-radius:50%;
 background:#5b9cf6;flex:0 0 auto}
.mossllm-chip[data-dirty="1"] .mossllm-dot{display:inline-block}
.mossllm-btn{flex:0 0 auto;margin-left:auto;padding:2px 6px;border:0;border-radius:6px;background:transparent;
 color:var(--descrip-text,#999);cursor:pointer;font:inherit;
 text-decoration:underline;text-underline-offset:2px}
.mossllm-btn:hover{color:var(--input-text,#ddd);background:var(--comfy-input-bg,#222)}
/* 「拉取模型」贴在 api_model 行里 —— 控件必须挨着它填的那一格，放顶部条上等于没放。
   行本身是 static 网格，设 relative 只为定位，不改宿主布局。 */
[data-widgets-grid-node-id] [data-widget-name="api_model"]{position:relative}
.mossllm-pick{position:absolute;right:4px;top:50%;transform:translateY(-50%);
 display:flex;align-items:center;gap:4px;max-width:60%;z-index:3}
.mossllm-pick button{flex:0 0 auto;width:20px;height:20px;padding:0;border-radius:5px;
 border:1px solid var(--border-color,#4e4e4e);background:var(--comfy-input-bg,#222);
 color:var(--input-text,#ddd);cursor:pointer;font:12px/1 system-ui,sans-serif}
.mossllm-pick button:hover{background:var(--comfy-menu-bg,#333)}
.mossllm-pick button[data-bad="1"]{border-color:#c0504d;color:#e07b78}
.mossllm-pick button[data-busy="1"]{color:#d9a441;border-color:#8a6d2f;
 animation:mossllm-spin 1s linear infinite}
@keyframes mossllm-spin{to{transform:rotate(360deg)}}
.mossllm-hintrow{flex:0 0 100%;display:flex;align-items:flex-start;gap:6px;min-width:0}
.mossllm-hint{flex:1 1 auto;min-width:0;overflow-wrap:anywhere;
 /* 🔴 高度必须恒定：悬停换 tooltip 时行数一变，下面每个参数都会跟着位移（实测：鼠标定位失败）。 */
 line-height:1.35;height:2.7em;overflow:hidden;opacity:.8}
.mossllm-langslot{flex:0 0 auto;display:flex;align-items:center;min-height:2.7em}
.mossllm-inert{opacity:.5}
.mossllm-head{margin-top:18px;position:relative}
.mossllm-head::before{content:attr(data-mossllm-title);position:absolute;left:2px;top:-16px;
 font:600 10.5px/16px system-ui,sans-serif;letter-spacing:.08em;
 color:var(--input-text,#ddd);opacity:.75}
`;

const I18N = {
    zh: { core: "常驻", tune: "常用调优", local: "本地引擎 · llama.cpp", api: "外部 API",
        other: "其它", offchannel: "通道外", expand: "全部展开", back: "回到分组",
        count: "%d 个参数 · %d 个可见", dirty: "%d 项与默认不同",
        openTip: "只展开这一组（其余收起来）", closeTip: "收起，回到常驻",
        expandTip: "一次展开全部 %d 项，包括当前通道用不到的旋钮",
        hideTip: "隐藏只是不画出来：参数还在工作流里，值不变",
        backTip: "恢复分组：默认只露常驻，一次看一组",
        inert: "本次不生效（编译任务不开模型）",
        inertHint: "编译模式：%d 项本次不生效",
        selfCheck: "零模型自检：任务选「编译」",
        fetchTip: "用当前 api_base 与 Key 拉模型清单，灌进这个下拉",
        fetched: "已拉到 %d 个模型，可直接下拉换",
        fetching: "拉模型清单中… %ds",
        fetchFail: "拉模型失败", needBase: "先填 api_base 再拉模型",
        switched: "通道已切到外部 API",
        apiIdle: "⚠ 端点已填，但通道=本地 —— 本次走本地模型",
        apiIdleFix: "点此改用 API",
        apiIdleTip: "api_base 已填，但「通道 / channel」还是本地 GGUF：本次运行会去找本机 llama-server，"
            + "端点不生效。点这一个提示，就把通道切到「外部 API」（端点与模型名都已填好）。"
            + "想保留本地就无视它 —— 通道=本地 是显式选择，节点不会替你改。",
        chanAuto: "已按已填的端点切到「外部 API」",
        // 模式摘要：首屏唯一一处解释「这个节点现在要干什么」的地方（不新增控件、不动存档）。
        chLocalShort: "本地 GGUF", chApiShort: "外部 API",
        mode_caption: "读图说话", mode_expand: "扩写提示词",
        mode_edit: "按指令改图", mode_compile: "只编译提示词",
        modeTip_caption: "读图说话：把输入图写成提示词。本地要 GGUF 模型 + 视觉塔（mmproj）；"
            + "切到外部 API 则用服务商的视觉模型",
        modeTip_expand: "扩写：纯文字 → 更详细的提示词。不需要图，也不需要视觉塔",
        modeTip_edit: "按指令改图：接输入图 + 指令，输出改写后的提示词。本地需要视觉塔",
        modeTip_compile: "只编译提示词：不开模型、不占显存，用来检查拼装结果",
        // 执行前就能看出来的缺项（Key 空不算缺 —— Ollama / LM Studio 本来就不要 Key）
        lackBase: "⚠ 还没填 api_base", lackName: "⚠ 还没选模型名",
        lackModel: "⚠ 本机没有 GGUF 模型",
        lackTipBase: "端点还没填，这次运行必然失败。点一下展开「外部 API」组。",
        lackTipName: "模型名还没选。点一下展开「外部 API」组，或先用 ↻ 拉一份清单。",
        lackTipModel: "本地通道要一个 GGUF 模型才能跑。点一下展开「本地引擎」组；"
            + "或把通道切到「外部 API」。",
        kindChannel: "通道",
        preset_draft: "草稿（快）", preset_fine: "精修（细）", preset_reset: "恢复默认",
        presetTip_draft: "一次写入：长度=简短 · 温度 0.9 · 上限 512 tokens。先看方向对不对时用，最省时间",
        presetTip_fine: "一次写入：长度=详细 · 温度 0.2 · 上限 2048 tokens。要出成品时用",
        presetTip_reset: "把「常用调优」这一组恢复成默认值（不动其它组、不动通道）",
        presetDone: "已套用「%s」，改了 %d 项",
        presetResetDone: "「常用调优」已恢复默认值",
        },
    en: { core: "Basic", tune: "Common", local: "Local engine · llama.cpp", api: "External API",
        other: "Other", offchannel: "other channel", expand: "Show all", back: "Grouped",
        count: "%d params · %d visible", dirty: "%d differ from default",
        openTip: "Show only this group", closeTip: "Fold back to Basic",
        expandTip: "Show all %d params, including the other channel's",
        hideTip: "Hiding only affects the canvas — values stay in the workflow",
        backTip: "Back to grouped view (Basic only, one group at a time)",
        inert: "No effect in this run (compile does not load a model)",
        inertHint: "Compile: %d controls have no effect",
        selfCheck: "No-weight check: set task=compile",
        fetchTip: "Fetch models for this api_base + key into this dropdown",
        fetched: "%d models loaded — pick from the dropdown",
        fetching: "Fetching model list… %ds",
        fetchFail: "Model list failed", needBase: "Fill api_base first",
        switched: "channel switched to external API",
        chLocalShort: "Local GGUF", chApiShort: "External API",
        mode_caption: "Caption image", mode_expand: "Expand prompt",
        mode_edit: "Edit by instruction", mode_compile: "Compile only",
        modeTip_caption: "Caption: turn the input image into a prompt. Local needs a GGUF model "
            + "plus a vision tower (mmproj); the API channel uses the provider's vision model",
        modeTip_expand: "Expand: plain text to a longer prompt. No image, no vision tower",
        modeTip_edit: "Edit: input image plus instruction, output the rewritten prompt. "
            + "Local needs a vision tower",
        modeTip_compile: "Compile only: no model, no VRAM, just check the assembled prompt",
        lackBase: "Missing api_base", lackName: "No model name yet",
        lackModel: "No local GGUF found",
        lackTipBase: "api_base is empty, so this run cannot work. Click to open External API.",
        lackTipName: "Pick a model name. Click to open External API, or press the arrow to fetch a list.",
        lackTipModel: "The local channel needs a GGUF model. Click to open Local engine, "
            + "or switch the channel to External API.",
        kindChannel: "ch",
        preset_draft: "Draft (fast)", preset_fine: "Fine (slow)", preset_reset: "Reset",
        presetTip_draft: "Writes: length=brief, temp 0.9, max 512 tokens. Cheapest way to check direction",
        presetTip_fine: "Writes: length=detailed, temp 0.2, max 2048 tokens. For final output",
        presetTip_reset: "Restore this group to defaults (other groups and the channel are untouched)",
        presetDone: "Applied %s, %d values changed",
        presetResetDone: "Common settings restored to defaults",
        apiIdle: "Endpoint filled but channel=local — this run uses local",
        apiIdleFix: "Click to use the API",
        apiIdleTip: "api_base is filled but the channel is still Local GGUF: this run will look for a local"
            + " llama-server and the endpoint will be ignored. Click this badge to switch the channel to"
            + " External API. Keep local on purpose? Ignore it — the channel is respected, never overridden.",
        chanAuto: "Channel switched to External API (endpoint filled)",
        },
};
let T = null;
/** 宿主界面语言（没点过「中/EN」时跟随它）。顺序与 `moss_sb_i18n_ui.js` 完全一致。 */
function hostLang() {
    let s = "";
    // 先 `<html lang>` 再 `Comfy.Locale`：与核心 i18n 的 readHostLang 同步，
    // 否则同一次界面里会出现「节点标题英文、面板芯片中文」的撕裂（实测）。
    try { s = String(document.documentElement.getAttribute("lang") || ""); } catch (e) { /* 无 DOM */ }
    if (!s) { try { s = String(app?.ui?.settings?.getSettingValue?.("Comfy.Locale") || ""); } catch (e) { /* 老前端 */ } }
    if (!s) s = String(navigator?.language || "");
    return normLang(s);
}
function t() {
    if (T) return T;
    let raw = "";
    try { raw = globalThis.localStorage?.getItem(LANG_KEY) || ""; } catch (e) { /* 隐私模式 */ }
    T = I18N[raw ? normLang(raw) : hostLang()];
    return T;
}

const fmt = (s, ...a) => String(s).replace(/%d/g, () => String(a.shift()));

const widgetOf = (node, name) => (node.widgets || []).find((w) => w && w.name === name);
const hideable = (w) => !!w && typeof w.syncVisibilityFromOptions === "function";
const realWidgets = (node) => (node.widgets || []).filter((w) => w && w.name !== BAR_NAME);
const groupOf = (w) => BY_NAME.get(w.name) || OTHER;
/** 旧稿把中文写进 task 枚举；归一口径与后端 _LEGACY_VALUES 一致。 */
const TASK_LEGACY = { "反推": "caption", "扩写": "expand", "编辑指令": "edit", "编译": "compile" };
const taskOf = (node) => {
    const w = widgetOf(node, "task");
    const v = w ? String(w.value) : "";
    return TASK_LEGACY[v] || v;
};
/** 可静态判定的模式知识；tasks/channels 由 check_frontend.py 的 F11 与后端常量对账。
 *  PE 命中取决于模型目录里的文件，属运行时探测，不进这张表（docs/21 A2'）。 */
const MODE_SPEC = {
    tasks: ["caption", "expand", "edit", "compile"],
    channels: ["local", "api"],
    inertOnCompile: ["channel", "dialect", "model"],
    pinnedOnCompile: ["target"],
};
/** 接了线的参数不许藏（藏了用户找不到那根线接到哪）。 */
const isLinked = (node, name) => (node.inputs || [])
    .some((i) => i && i.name === name && i.link != null);
/** 宿主给每行打了 `data-widget-name`、给网格打了 `data-widgets-grid-node-id` = 节点 id。 */
function rowOf(node, name) {
    try {
        return document.querySelector(
            '[data-widgets-grid-node-id="' + node.id + '"] [data-widget-name="' + name + '"]');
    } catch (e) { return null; }
}

function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
}

// ---------------------------------------------------------------------------
// 参数说明 + 默认值：读宿主缓存里的**同一份** nodeDef，前端不复制第二份中文
// ---------------------------------------------------------------------------
let TIPS = null;
let DEFAULTS = null;
let LOADING = null;
function loadDefs() {
    if (LOADING) return LOADING;
    LOADING = (async () => {
        const tips = {};
        const def = {};
        try {
            const defs = await app.getNodeDefs();
            const inp = (defs && defs[TYPE] && defs[TYPE].input) || {};
            for (const sec of ["required", "optional"]) {
                for (const [name, spec] of Object.entries(inp[sec] || {})) {
                    const arr = Array.isArray(spec) ? spec : [spec];
                    const opts = arr[1] || {};
                    if (opts.tooltip) tips[name] = String(opts.tooltip);
                    if ("default" in opts) def[name] = opts.default;
                    else if (Array.isArray(arr[0])) def[name] = arr[0][0];
                }
            }
        } catch (e) {
            console.warn("%s: 取不到参数说明/默认值（说明行会空着，且不画「已改」点）", TAG, e);
        }
        TIPS = tips;
        DEFAULTS = def;
        return { tips, def };
    })();
    return LOADING;
}
const tipOf = (name) => (TIPS && TIPS[name]) || "";

let hintOn = false;
function installHintListener() {
    if (hintOn) return;
    hintOn = true;
    document.addEventListener("pointerover", async (e) => {
        const row = e.target && e.target.closest ? e.target.closest("[data-widget-name]") : null;
        const grid = row && row.closest("[data-widgets-grid-node-id]");
        const hovered = grid ? (app?.graph?.nodes || [])
            .find((n) => String(n.id) === grid.getAttribute("data-widgets-grid-node-id")) : null;
        const owner = hovered && hovered.type === TYPE ? hovered : null;
        // 指针离开本节点的参数区 ⇒ 说明行回到摘要（否则最后一条 tooltip 会一直挂着）
        for (const n of (app?.graph?.nodes || [])) {
            if (n === owner || n.type !== TYPE) continue;
            const s = n.__mossllm;
            if (!s || !s.hint || !s.hint.dataset.hover) continue;
            delete s.hint.dataset.hover;
            s.hint.textContent = s.summary || "";
            s.hint.title = s.modeTip || "";
        }
        const st = owner && owner.__mossllm;
        if (!st || !st.hint) return;
        await loadDefs();
        const name = row.getAttribute("data-widget-name");
        const w = owner && widgetOf(owner, name);
        // 英文态下 `applyLang` 把 en 提示写在 `w.tooltip` 上（与核心 7 节点同一份词表）；
        // 中文态它把覆盖撤掉 ⇒ 回落到后端 nodeDef 里的中文原文。
        const text = (w && w.tooltip) || tipOf(name) || st.summary || "";
        st.hint.dataset.hover = "1";
        // 说明行高度固定（不抖）⇒ 长 tooltip 会被裁掉两行；原生 title 给全文，不改变布局。
        st.hint.title = text;
        if (st.hint.textContent !== text) st.hint.textContent = text;
    }, true);
}

// ---------------------------------------------------------------------------
// 状态（只在内存，绝不进存档）
// ---------------------------------------------------------------------------
function stateOf(node) {
    let st = node.__mossllm;
    if (!st) {
        st = node.__mossllm = {
            focus: null,           // null = 只看常驻；否则是某组 key
            force: false,          // 一键全展开
            warnedChannel: false,
        };
    }
    return st;
}

/** 该显示哪些。通道标签认不出来时**不做通道门控**（宁可多显示，不可藏错）。 */
function plan(node, st) {
    const cw = widgetOf(node, "channel");
    const ch = cw ? chanOf(String(cw.value)) : "";
    const gated = MODE_SPEC.channels.includes(ch);
    if (cw && !gated && !st.warnedChannel) {
        st.warnedChannel = true;
        console.warn("%s: 不认识 channel 取值 %j ⇒ 停用通道标记（分组仍可用）", TAG, ch);
    }
    const task = taskOf(node);
    const compile = MODE_SPEC.tasks.includes(task) && task === "compile";
    const shown = new Set();
    const off = new Set();                       // 通道对不上的组（只影响 chip 的标记）
    const inert = new Set();                     // 本次执行读不到、但仍显示并标注的控件
    for (const w of realWidgets(node)) {
        const g = groupOf(w);
        const outside = gated && !!g.channel && g.channel !== ch;
        if (outside) off.add(g.key);
        if (compile && MODE_SPEC.inertOnCompile.includes(w.name)) inert.add(w.name);
        // compile 唯一有效的旋钮是 target；它排在存档末尾，折叠时用户换不了后端。
        const pinned = compile && MODE_SPEC.pinnedOnCompile.includes(w.name);
        // 优先级：接了线 > 一键全开 > compile 必填 > 常驻 > 当前聚焦组
        const ok = isLinked(node, w.name) || st.force || pinned || !!g.always || g.key === st.focus;
        if (ok) shown.add(w.name);
    }
    return { shown, off, inert, compile, task, ch };
}

function syncWidgets(node, shown) {
    let changed = false;
    for (const w of realWidgets(node)) {
        if (!hideable(w)) continue;
        const wantHidden = !shown.has(w.name);
        if (!!w.options.hidden !== wantHidden) {
            w.options.hidden = wantHidden;
            w.syncVisibilityFromOptions();
            changed = true;
        }
    }
    return changed;
}

/**
 * 按可见内容调节点高度（**只动 `size`，不动 `widgets_values`**）。
 *
 * 为什么必须调：宿主不会随隐藏行自动收缩；DOM 组件那一格又被 `*:flex-1` 拉满 ⇒ 折叠后
 * 原尺寸会留一大片空白。这里用宿主自己渲染的行元素实测内容高度。
 * 量不到（画布模式没有 DOM 行）就**不猜** —— 宁可高，不可裁。
 * 只调高度、不调宽度；用户手拖的高度会在下一次「切换分组」时被内容高度取代（这是可预期的）。
 */
function fitHeight(node, shown) {
    const grid = document.querySelector('[data-widgets-grid-node-id="' + node.id + '"]');
    const box = grid && grid.closest(".lg-node");
    if (!grid || !box || typeof node.setSize !== "function") return false;
    // 宿主的网格会把多余高度**平摊进每一行**（自动行被 stretch）⇒ 直接量到的是"摊平后"
    // 的高度，拿它当内容高度会越调越高。临时把行压成 min-content 再量，量完立刻还原：
    // 同一个任务里改样式再读回，浏览器不会 paint，看不到跳动。
    grid.style.setProperty("grid-auto-rows", "min-content", "important");
    grid.style.setProperty("align-content", "start", "important");
    let rects;
    try {
        // 只量**这一轮要显示**的行：宿主删 DOM 行是异步的，刚藏起来的行这一刻还在页面上，
        // 按 DOM 高度数会把 38 行全算进去（实测因此把节点从 319 调到 1286）。
        rects = [...grid.querySelectorAll("[data-widget-name]")]
            .filter((e) => e.dataset.widgetName === BAR_NAME || shown.has(e.dataset.widgetName))
            .map((e) => e.getBoundingClientRect());
        // order 只改视觉顺序；DOM 首行不是视觉首行，顶部留白会被算成置顶行高度。
        rects.sort((a, b) => a.top - b.top);
    } finally {
        grid.style.removeProperty("grid-auto-rows");
        grid.style.removeProperty("align-content");
    }
    // 还没排完版（有 0 高）⇒ 这一轮不猜，等下一次 applySoon
    if (!rects.length || rects.some((r) => r.height <= 0)) return false;
    const gr = grid.getBoundingClientRect();
    const br = box.getBoundingClientRect();
    const zoom = (app?.canvas?.ds?.scale) || 1;
    let gaps = 0;
    for (let i = 1; i < rects.length; i += 1) gaps += Math.max(0, rects[i].top - rects[i - 1].bottom);
    const content = rects.reduce((a, r) => a + r.height, 0) + gaps
        + Math.max(0, rects[0].top - gr.top) + 4 * zoom;
    // 实测宿主：节点盒高 =（size 高 + 30）× zoom ⇒ size = 盒高 / zoom − 30。
    const want = Math.round((br.height - gr.height + content) / zoom - 30);
    if (!Number.isFinite(want) || want < 120) return false;
    if (Math.abs(node.size[1] - want) < 6) return false;
    node.setSize([node.size[0], want]);
    return true;
}

function paintInert(node, inert) {
    const L = t();
    for (const w of realWidgets(node)) {
        const row = rowOf(node, w.name);
        if (!row) continue;
        const on = inert.has(w.name);
        row.classList.toggle("mossllm-inert", on);
        if (on) {
            row.title = L.inert;
            row.dataset.mossllmInert = "1";
        } else if (row.dataset.mossllmInert) {
            delete row.dataset.mossllmInert;
            row.title = "";
        }
    }
}

/**
 * 组标题：每次「可见行的组」换人时标一条。
 * 单组视图下每组只出现一次；`全部展开` 时按段画（顺序是存档契约，不能为了标题去重排）。
 */
function paintHeaders(node, shown) {
    let prev = null;
    let found = 0;
    for (const w of realWidgets(node)) {
        if (!shown.has(w.name)) continue;
        const row = rowOf(node, w.name);
        if (!row) continue;
        found += 1;
        const g = groupOf(w);
        const head = prev !== null && !g.always && g.key !== prev;
        row.classList.toggle("mossllm-head", head);
        if (head) row.dataset.mossllmTitle = t()[g.key] || g.key;
        prev = g.key;
    }
    return found;
}

/** 这一组里有几项与默认不同（拿宿主 nodeDef 的 default 比）。 */
function countDirty(node, g) {
    if (!DEFAULTS) return 0;
    let n = 0;
    for (const name of g.items) {
        if (DIRTY_SKIP.has(name)) continue;
        const w = widgetOf(node, name);
        if (!w || !(name in DEFAULTS)) continue;
        const dv = DEFAULTS[name];
        if (dv === undefined) continue;
        const a = w.value;
        const same = (typeof dv === "number" && typeof a === "number")
            ? Math.abs(a - dv) < 1e-9 : a === dv;
        if (!same) n += 1;
    }
    return n;
}

// ---------------------------------------------------------------------------
// 开关条：分组 chip + 全部展开 + 说明行
// ---------------------------------------------------------------------------
function makeChip(node, st, g) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mossllm-chip";
    b.dataset.mossllmChip = g.key;
    const caret = document.createElement("span");
    caret.className = "mossllm-caret";
    const label = document.createElement("span");
    const count = document.createElement("span");
    count.className = "mossllm-count";
    const dot = document.createElement("span");
    dot.className = "mossllm-dot";
    // 通道组带一个前缀小字：三个 chip 并排时，「常用调优」是参数分组、
    // 「本地引擎 / 外部 API」是通道，不是同一维度，不标出来就会被当成同级三选一。
    const kind = g.channel ? document.createElement("span") : null;
    if (kind) {
        kind.className = "mossllm-kind";
        b.append(kind);
    }
    b.append(caret, label, count, dot);
    b.onclick = (e) => {
        e.stopPropagation();
        const wasForce = st.force;
        st.force = false;
        st.focus = wasForce ? g.key : (st.focus === g.key ? null : g.key);
        applySoon(node);
    };
    return { b, caret, label, count, dot, kind };
}

function apiVal(node, name) {
    const w = widgetOf(node, name);
    return w ? String(w.value == null ? "" : w.value) : "";
}

function setModel(node, id) {
    const w = widgetOf(node, "api_model");
    if (!w || !id) return;
    w.value = id;
    try { w.callback?.(id); } catch (e) { /* 老前端没有 callback */ }
    app?.graph?.setDirtyCanvas?.(true, true);
}

/**
 * 把「通道」拨到 API。点「↻ 拉模型」本身就是「我要用这个 API」的明确动作：
 * 拉完仍停在「本地 GGUF」，下次运行就会去找 llama-server（实测踩过）。
 */
function setChannel(node, v) {
    const w = widgetOf(node, "channel");
    if (!w || chanOf(String(w.value)) === v) return false;
    w.value = v;
    try { w.callback?.(v); } catch (e) { /* 老前端没有 callback */ }
    app?.graph?.setDirtyCanvas?.(true, true);
    return true;
}

/**
 * 把拉到的模型灌进 `api_model` 这个 combo 的选项。
 * 直接改 `options.values` 宿主不重画（实测下拉仍是旧列表），必须把同一个 widget
 * 在同索引 splice 一次触发响应式；位置不变 ⇒ 存档格数与顺序不受影响。
 */
function setModelChoices(node, models) {
    const w = widgetOf(node, "api_model");
    if (!w) return;
    const vals = [""].concat(models);
    // 存过的自定义值不在清单里时要留在选项中，否则 combo 会显示空白、存盘把值弄丢。
    const cur = String(w.value == null ? "" : w.value);
    if (cur && !vals.includes(cur)) vals.unshift(cur);
    w.options.values = vals;
    const i = node.widgets.indexOf(w);
    if (i >= 0) {
        node.widgets.splice(i, 1);
        node.widgets.splice(i, 0, w);
    }
    app?.graph?.setDirtyCanvas?.(true, true);
}

/** 拉模型清单：Key 只在这一次 POST 里过一趟，服务端不回显、不落盘。 */
async function fetchModels(node, st) {
    const base = apiVal(node, "api_base");
    const pick = st.pick;
    if (!pick) return;
    if (!base) {
        // 空端点就点「拉取」是静默空转 —— 必须说清缺哪一项。
        pick.err = t().needBase;
        pick.note = "";
        pick.btn.dataset.bad = "1";
        apply(node);
        return;
    }
    syncModelBase(node, st);
    pick.btn.disabled = true;
    pick.btn.dataset.busy = "1";
    pick.err = null;
    pick.note = "";
    const t0 = Date.now();
    // 兜底链最长要几秒，没有可见进度就像点了没反应。
    pick.busy = fmt(t().fetching, 0);
    pick.timer = setInterval(() => {
        if (!node.graph) {           // 节点已被删除：定时器必须自己停
            clearInterval(pick.timer);
            return;
        }
        pick.busy = fmt(t().fetching, Math.round((Date.now() - t0) / 1000));
        apply(node);
    }, 1000);
    apply(node);
    try {
        const r = await fetch("/moss_llm/models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_base: base, api_key: apiVal(node, "api_key") }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || ("HTTP " + r.status));
        setModelChoices(node, d.models);
        pick.err = null;
        delete pick.btn.dataset.bad;
        // 拉成功 = 用这个 API。通道还停在本地就顺手拨过去，否则下次运行又去找 llama-server。
        const switched = setChannel(node, CH_API);
        // 拉完必须看得见：空着就选中第一个（否则屏幕上除了下拉内部什么都没变）。
        if (!apiVal(node, "api_model") && d.models.length) setModel(node, d.models[0]);
        pick.note = fmt(t().fetched, d.models.length) + (switched ? "｜" + t().switched : "");
        pick.autoOpen = true;
        pick.btn.title = t().fetchTip + (d.url ? "\n" + d.url : "");
    } catch (e) {
        // 失败必须留下痕迹：按钮变红、说明行常驻原因（不靠会过期的一闪而过）。
        pick.err = t().fetchFail + "：" + String((e && e.message) || e).split("\n")[0];
        pick.note = "";
        pick.btn.dataset.bad = "1";
        console.warn("%s: 拉取模型失败", TAG, e);
    } finally {
        clearInterval(pick.timer);
        pick.timer = 0;
        pick.busy = "";
        pick.btn.disabled = false;
        delete pick.btn.dataset.busy;
        pick.btn.textContent = "↻";
        apply(node);
        if (pick.autoOpen) {
            pick.autoOpen = false;
            setTimeout(() => openCombo(node), 60);
        }
    }
}

/** 拉开原生 combo（宿主用 button[role=combobox] 渲染）：拉完直接让用户看见全部可选项。 */
function openCombo(node) {
    const row = document.querySelector('[data-widgets-grid-node-id="' + node.id
        + '"] [data-widget-name="api_model"]');
    const ctl = row && row.querySelector('[role="combobox"]');
    if (ctl) ctl.click();
}

/**
 * 换了 api_base，上一批模型名就对不上了。不清理会拿旧列表冒充这次的结果
 * （实测：新端点 404 时下拉里还是上一个服务的模型，看起来像"拉到了"）。
 */
function syncModelBase(node, st) {
    if (!st.pick) return;
    const base = apiVal(node, "api_base");
    if (st.pick.base === undefined) {
        st.pick.base = base;
        return;
    }
    if (st.pick.base === base) return;
    st.pick.base = base;
    st.pick.autoDone = false;
    st.pick.err = null;
    st.pick.note = "";
    setModelChoices(node, []);   // 只留当前值与空串
}

/**
 * 旧工作流存过的模型名可能不在这次拉到的清单里：不补进选项，combo 就显示空白，
 * 用户以为没选、存盘还可能丢值。补进去即可（只会补一次）。
 */
function ensureModelValue(node) {
    const w = widgetOf(node, "api_model");
    const vals = w && w.options && w.options.values;
    if (!Array.isArray(vals)) return;
    const cur = String(w.value == null ? "" : w.value);
    if (!cur || vals.includes(cur)) return;
    w.options.values = [cur].concat(vals);
    const i = node.widgets.indexOf(w);
    if (i >= 0) {
        node.widgets.splice(i, 1);
        node.widgets.splice(i, 0, w);
    }
}

/**
 * 载入存档后的通道对齐：旧存档里「端点填着、通道还写本地」是踩坑态（运行时会去找本地 LLM）。
 * 对齐只做一次 —— 之后用户手动切回本地就是显式选择，不再抢。
 *
 * 为什么放在 apply 而不是 onConfigure：宿主把 `widgets_values` 灌进 widgets 的时机晚于
 * onConfigure（实测：在那里读 api_base 还是空串），只能等值真的出现。
 */
function adoptApiIntent(node, st) {
    if (st.chanAligned || !apiVal(node, "api_base").trim()) return;
    st.chanAligned = true;
    if (chanOf(apiVal(node, "channel")) !== CH_API && setChannel(node, CH_API)) {
        noteOnce(node, st, t().chanAuto);
    }
}

/** 一次性说明：把状态的改变说出来，但不能一直占着摘要行（8s 后自己收）。 */
function noteOnce(node, st, text) {
    st.note = text;
    clearTimeout(st.noteTimer);
    st.noteTimer = setTimeout(() => {
        if (st.note === text) { st.note = ""; apply(node); }
    }, 8000);
}

/** 把「↻ 拉取」贴在 api_model 行右端；宿主重建行元素后自愈。 */
function pinPicker(node, st) {
    const row = rowOf(node, "api_model");
    if (!row) return false;
    if (!st.pick) {
        const box = document.createElement("span");
        box.className = "mossllm-pick";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "↻";
        btn.onclick = (e) => {
            e.stopPropagation();
            fetchModels(node, st);
        };
        box.append(btn);
        st.pick = { box, btn, err: null, note: "", busy: "", timer: 0, autoDone: false };
    }
    if (st.pick.box.parentElement !== row) row.appendChild(st.pick.box);
    st.pick.btn.title = t().fetchTip;
    // 端点与 Key 都填了、模型还空着 ⇒ 自动拉一次（每节点一次，失败不反复打服务端）
    if (!st.pick.autoDone && !apiVal(node, "api_model") && apiVal(node, "api_base")
        && apiVal(node, "api_key")) {
        st.pick.autoDone = true;
        fetchModels(node, st);
    }
    return true;
}

function buildBar(node, st) {
    const bar = document.createElement("div");
    bar.className = "mossllm-bar";
    // 宿主给 DOM 组件单元格套了 `*:flex-1`（flex-grow:1）。那条规则在样式级联里压过
    // `.mossllm-bar` 的普通声明（实测），于是开关条会被拉满剩余高度、文字被摊成一列。
    // 用**内联**声明钉住（内联优先于任何无 !important 的类规则）。
    bar.style.flex = "0 0 auto";
    bar.style.alignContent = "flex-start";
    // 点芯片不该变成拖节点（与语言条同一套事件）
    for (const ev of ["pointerdown", "mousedown", "dblclick", "keydown", "click"]) {
        bar.addEventListener(ev, (e) => e.stopPropagation());
    }
    st.chips = {};
    for (const g of GROUPS) {
        if (g.always) continue;
        const c = makeChip(node, st, g);
        st.chips[g.key] = c;
        bar.appendChild(c.b);
    }
    st.btn = document.createElement("button");
    st.btn.type = "button";
    st.btn.className = "mossllm-btn";
    st.btn.onclick = (e) => {
        e.stopPropagation();
        st.force = !st.force;
        applySoon(node);
    };
    st.hint = document.createElement("span");
    st.hint.className = "mossllm-hint";
    // 「中 / EN」按钮由 `moss_sb_i18n_ui.js` 挂进这个槽（与布局编辑器的 langSlot 同一机制）：
    // 一个节点只留一条 DOM 行，语言按钮和摘要/说明同一行。
    st.langSlot = document.createElement("span");
    st.langSlot.className = "mossllm-langslot";
    const hintRow = document.createElement("div");
    hintRow.className = "mossllm-hintrow";
    hintRow.append(st.hint, st.langSlot);
    st.warn = document.createElement("span");
    st.warn.className = "mossllm-warn";
    st.warn.hidden = true;
    st.warn.onclick = (e) => {
        e.stopPropagation();
        const act = st.warn.dataset.act || "chan";
        // 缺项：只把对应分组展开（不替用户改值）—— 修复动作是「填一个值」，不该由按钮代填。
        if (act === "open-api" || act === "open-local") {
            st.focus = act === "open-api" ? "api" : "local";
            applySoon(node);
            return;
        }
        // 通道矛盾：拨到外部 API（端点/模型名原样保留）。用户显式选本地时不会被这个按钮碰到。
        if (setChannel(node, CH_API)) {
            noteOnce(node, st, t().chanAuto);
            applySoon(node);
        }
    };
    // 一次性预设：跟着「常用调优」组的展开/收起显隐 —— 不常驻占位，但一展开就在手边。
    st.presets = document.createElement("span");
    st.presets.className = "mossllm-presets";
    st.presets.hidden = true;
    for (const key of ["draft", "fine", "reset"]) {
        const pb = document.createElement("button");
        pb.type = "button";
        pb.className = "mossllm-preset";
        pb.dataset.mossPreset = key;
        pb.onclick = (e) => {
            e.stopPropagation();
            applyPreset(node, st, key);
        };
        st.presets.appendChild(pb);
    }
    bar.append(st.warn, st.presets, st.btn, hintRow);
    return bar;
}

/**
 * 一次性预设：点一下写入几个值，之后照旧各改各的。
 *
 * 为什么不是常驻下拉：下拉要存「当前是哪档」，用户手改一个旋钮后它就得显示「自定义」，
 * 于是多一个必须与真实值同步的状态。按钮没有这个问题 —— 它是一次动作，不是状态，
 * 也不用进 `widgets_values`（按钮本身不是 widget）。
 *
 * 只动采样三项：two_pass / wash 是反推（caption）专用，无脑打开会让别的模式变慢变贵。
 */
const PRESETS = {
    draft: { length: "brief", temperature: 0.9, max_tokens: 512 },
    fine: { length: "detailed", temperature: 0.2, max_tokens: 2048 },
};

/** `reset` 用后端下发的 nodeDef 默认值，只作用于「常用调优」这一组。 */
function applyPreset(node, st, key) {
    const L = t();
    const spec = key === "reset" ? (DEFAULTS || {}) : PRESETS[key];
    if (!spec) return;
    const names = key === "reset"
        ? (GROUPS.find((g) => g.key === "tune").items || []) : Object.keys(spec);
    let n = 0;
    for (const name of names) {
        const w = widgetOf(node, name);
        if (!w || !(name in spec)) continue;
        if (String(w.value) === String(spec[name])) continue;
        w.value = spec[name];
        try { w.callback?.(spec[name]); } catch (e) { /* 老前端没有 callback */ }
        n += 1;
    }
    // 值改了但参数可能是收起的看不见 ⇒ 必须说一句，否则又成「点了没反应」。
    noteOnce(node, st, key === "reset" ? L.presetResetDone
        : fmt(L.presetDone, L["preset_" + key], n));
    applySoon(node);
    app?.graph?.setDirtyCanvas?.(true, true);
}

/**
 * 执行前就能判断的「还缺什么」——只列**前端看得见、且缺了必然跑不起来**的项。
 *
 * Key 空不算缺：Ollama / LM Studio / vLLM 本来就不要 Key，报「缺 Key」会把人往错方向带。
 * llama-server 有没有也不在这里判断（那要碰文件系统），交给执行期那条定向报错。
 */
function lacksOf(node, meta) {
    if (!meta || meta.compile) return null;          // 编译任务不开模型
    if (meta.ch === CH_API) {
        if (!apiVal(node, "api_base").trim()) return "base";
        if (!apiVal(node, "api_model").trim()) return "name";
        return null;
    }
    // 后端在没有 GGUF 时把占位符塞进选项（"(未找到 GGUF 模型)"）；真实文件名不会以括号开头。
    const m = apiVal(node, "model");
    return (!m || m.startsWith("(")) ? "model" : null;
}

function paintBar(node, st, shown, off, meta) {
    const L = t();
    let hidden = 0;
    for (const g of GROUPS) {
        if (g.always) continue;
        const { b, caret, label, count, dot, kind } = st.chips[g.key];
        const total = g.items.filter((n) => widgetOf(node, n)).length;
        b.hidden = total === 0;                          // 参数表里没这些名字 ⇒ 不摆空开关
        if (!total) continue;
        const expanded = st.force || st.focus === g.key;
        const outside = off.has(g.key);
        const vis = g.items.filter((n) => shown.has(n)).length;
        hidden += total - vis;
        caret.textContent = expanded ? "▾" : "▸";
        if (kind) kind.textContent = L.kindChannel;
        label.textContent = L[g.key] + (outside ? " · " + L.offchannel : "");
        count.textContent = total;
        b.dataset.state = expanded ? (outside ? "outside" : "open") : "closed";
        const dirty = countDirty(node, g);
        b.title = (expanded ? L.closeTip : L.openTip)
            + (dirty ? "｜" + fmt(L.dirty, dirty) : "")
            + "｜" + L.hideTip;
        b.dataset.dirty = dirty ? "1" : "0";
        dot.title = dirty ? fmt(L.dirty, dirty) : "";
    }
    const all = realWidgets(node).length;
    st.btn.textContent = st.force ? L.back : L.expand;
    st.btn.title = st.force ? L.backTip : fmt(L.expandTip, hidden);

    // 状态块两用，且两者天然互斥（通道矛盾要求 api_base 非空，缺端点要求它为空）：
    //   ① 端点已填、通道却停在本地 —— 通道矛盾，优先（它往往是缺项之前要先解决的那件事）；
    //   ② 还缺必填项 —— 跑之前就说出来，而不是等执行完再报错。
    const idle = chanOf(apiVal(node, "channel")) !== CH_API && !!apiVal(node, "api_base").trim();
    const lack = idle ? null : lacksOf(node, meta);
    if (st.warn) {
        st.warn.hidden = !idle && !lack;
        st.warn.dataset.act = idle ? "chan" : (lack === "model" ? "open-local" : "open-api");
        if (idle) {
            st.warn.textContent = L.apiIdle + " · " + L.apiIdleFix;
            st.warn.title = L.apiIdleTip;
        } else if (lack) {
            const K = lack[0].toUpperCase() + lack.slice(1);
            st.warn.textContent = L["lack" + K];
            st.warn.title = L["lackTip" + K];
        }
    }

    // 预设按钮跟着调参组走：展开才有意义，收起时别占地方。
    if (st.presets) {
        st.presets.hidden = !(st.force || st.focus === "tune");
        if (!st.presets.hidden) {
            for (const pb of st.presets.children) {
                const k = pb.dataset.mossPreset;
                pb.textContent = L["preset_" + k];
                pb.title = L["presetTip_" + k];
            }
        }
    }

    // 模式摘要：首屏唯一解释「这个节点现在要干什么」的一行（不加控件、不动存档）。
    // 完整说明（要不要视觉塔、要不要图）放 title，不占视觉宽度。
    const modeName = (meta && L["mode_" + meta.task]) || (meta && meta.task) || "";
    const chanName = !meta ? "" : (meta.ch === CH_API ? L.chApiShort
        : meta.ch === CH_LOCAL ? L.chLocalShort : "");
    st.modeTip = (meta && L["modeTip_" + meta.task]) || "";
    if (!st.hint.dataset.hover) st.hint.title = st.modeTip;

    // 「通道外」已经写在对应 chip 上，摘要里不再重复一遍（少一行字就少一分噪音）。
    let summary = (modeName && chanName ? modeName + " · " + chanName + "｜" : "")
        + fmt(L.count, all, all - hidden);
    if (st.pick && st.pick.err) summary += "｜" + st.pick.err.slice(0, 70);
    else if (st.pick && st.pick.busy) summary += "｜" + st.pick.busy;
    else if (st.pick && st.pick.note) summary += "｜" + st.pick.note;
    else if (st.note) summary += "｜" + st.note;
    if (meta && meta.inert.size) summary += "｜" + fmt(L.inertHint, meta.inert.size);
    else if (meta && !meta.compile && meta.task === "caption" && meta.ch === CH_LOCAL) {
        summary += "｜" + L.selfCheck;
    }
    if (!st.hint.dataset.hover) st.hint.textContent = summary;
    st.summary = summary;
}

/**
 * 宿主重建行元素是异步的：刚露出的行在这一帧还 `querySelector` 不到 ⇒ 标题会漏画，
 * 直到下一次兜底轮询（1.5s）才补上。所以每次改完显隐都补两帧。
 */
function applySoon(node) {
    apply(node);
    try { requestAnimationFrame(() => apply(node)); } catch (e) { /* 无 rAF */ }
    setTimeout(() => {
        apply(node);
        // 行重建/CSS order 晚于首帧；补算一次，尺寸已对时 fitHeight 自带早退。
        // 必须与 fitHeight 的排序同做，否则会把虚高 want 写得更稳。
        try { fitHeight(node, plan(node, stateOf(node)).shown); } catch (e) { /* noop */ }
    }, 300);
}

/**
 * 开关条**置顶**：`addDOMWidget` 只会追加到末尾，而末尾意味着每展开一组它就被顶下一截
 * （2026-10-30 实测反馈：点开一组，开关就跑到最低下）。这里把那个 widget 挪到数组首位 ——
 * 只动「不参与存档的 DOM 组件」的位置，38 个参数之间的相对顺序一个都没变；
 * 存盘时 `installSaveCleanup` 照样把它那一格剔掉（实测仍 38 格、逐位一致）。
 * 宿主重建 widget 数组后位置会退回末尾，所以 `apply()` 每次都自愈。
 */
function ensureBarFirst(node, st) {
    const w = st.barWidget;
    if (!w || !node.widgets || node.widgets[0] === w) return false;
    const i = node.widgets.indexOf(w);
    if (i < 0) return false;
    node.widgets.splice(i, 1);
    node.widgets.unshift(w);
    return true;
}

function protectFocus(node, st, shown) {
    const act = document.activeElement;
    const row = act && act.closest ? act.closest("[data-widget-name]") : null;
    if (!row) return;
    const grid = row.closest('[data-widgets-grid-node-id="' + node.id + '"]');
    if (!grid || shown.has(row.dataset.widgetName)) return;
    const c = st.chips && (st.chips[st.focus] || st.chips.tune);
    if (c) { try { c.b.focus(); } catch (e) { /* noop */ } }
}

function apply(node) {
    const st = stateOf(node);
    ensureBarFirst(node, st);
    // 开关条自己也会长高（换语言/换主题/换行数）⇒ 高度变了就要重算节点高度，
    // 否则 canvas 的 size 比 DOM 盒小，底部那截点不到、也拖不动。
    const el = st.barWidget && st.barWidget.element;
    const barH = el ? el.getBoundingClientRect().height : 0;
    if (barH && Math.abs(barH - (st.barH || 0)) > 2) {
        st.barH = barH;
        st.fitted = false;
    }
    adoptApiIntent(node, st);
    const meta = plan(node, st);
    const { shown, off, inert } = meta;
    const changed = syncWidgets(node, shown);
    paintInert(node, inert);
    const found = paintHeaders(node, shown);
    syncModelBase(node, st);
    ensureModelValue(node);
    pinPicker(node, st);
    if (found === 0 && !st.domWarned) {
        st.domWarned = true;
        console.warn("%s: 这个前端定位不到参数行（旧版画布）⇒ 组标题不画，显隐照常生效", TAG);
    }
    if (st.chips) paintBar(node, st, shown, off, meta);
    if (changed) protectFocus(node, st, shown);
    if (changed || !st.fitted) st.fitted = fitHeight(node, shown) || !!st.fitted;
    return shown.size;
}

// ---------------------------------------------------------------------------
// 挂载
// ---------------------------------------------------------------------------
function focusForChannel(node, st, load) {
    if (st.force) return;
    const cw = widgetOf(node, "channel");
    if (!cw) return;
    const ch = chanOf(String(cw.value));
    const cur = GROUPS.find((g) => g.key === st.focus);
    if (load) {
        // 冷载入只对 api 兜底：local 保持默认 5 行，别把首屏撑开。
        if (ch === CH_API && (!cur || cur.channel !== CH_API)) st.focus = "api";
        return;
    }
    const want = GROUPS.find((g) => g.channel && g.channel === ch);
    if (want && (!cur || cur.channel !== want.channel)) st.focus = want.key;
}

function mount(node) {
    if (!node || node.type !== TYPE || node.__mossllmBar) return false;
    const ws = realWidgets(node);
    if (!ws.some(hideable)) {
        node.__mossllmBar = "unsupported";
        console.warn("%s: 本前端没有 widget 显隐 API ⇒ 不折叠，%d 个参数全部照常显示", TAG, ws.length);
        return false;
    }
    const st = stateOf(node);
    let bar;
    try {
        ensureStyle();
        bar = buildBar(node, st);
        // `serialize:false` = 告诉宿主别存；存盘时由 `moss_sb_widget_order.js` 真把那一格剔掉。
        st.barWidget = node.addDOMWidget(BAR_NAME, "custom", bar,
            { serialize: false, hideOnZoom: false, getMinHeight: () => 40 });
    } catch (e) {
        // 挂不上开关就不藏任何东西 —— 没有开关的隐藏等于把参数弄丢
        node.__mossllmBar = "noDom";
        console.warn("%s: 该前端不支持节点 DOM 组件 ⇒ 面板不启用，参数全部照常显示", TAG, e);
        return false;
    }
    node.__mossllmBar = bar;
    ensureBarFirst(node, st);
    installSaveCleanup(node);
    installHintListener();
    focusForChannel(node, st, true);
    applySoon(node);
    loadDefs();

    const cw = widgetOf(node, "channel");
    if (cw) {
        const orig = cw.callback;
        cw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            // 换通道 ⇒ 把参数面板切到那个通道的组：不切的话用户看不到 api_* / mmproj，
            // 填不了必填项还以为节点坏了。
            focusForChannel(node, st, false);
            applySoon(node);
            return r;
        };
    }
    // 换端点 ⇒ 上一批模型名立刻作废。等 1.5s 兜底轮询太慢，更像"没反应"。
    // 同一处顺手把通道拨过去：填了端点 = 要用这个 API（踩过的坑：填完直接跑，去本地找 llama-server）。
    const bw = widgetOf(node, "api_base");
    if (bw) {
        const orig = bw.callback;
        bw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            if (String(args[0] == null ? "" : args[0]).trim()) setChannel(node, CH_API);
            applySoon(node);
            return r;
        };
    }
    // 摘要行与缺项都在读 task / model：值一变就必须重画。不装这个回调，只能等 1.5s 兜底轮询，
    // 用户看到的就是「切了模式，上面那行字没动」（实测）。
    for (const name of ["task", "model"]) {
        const w = widgetOf(node, name);
        if (!w || w.__mossRepaintHooked) continue;
        const orig = w.callback;
        w.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            applySoon(node);
            return r;
        };
        w.__mossRepaintHooked = true;
    }
    // 手输模型名（不走 ↻ 拉取）同样是"我要用这个 API"。
    const mw = widgetOf(node, "api_model");
    if (mw && !mw.__mossChanHooked) {
        const orig = mw.callback;
        mw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            if (String(args[0] == null ? "" : args[0]).trim()) setChannel(node, CH_API);
            applySoon(node);
            return r;
        };
        mw.__mossChanHooked = true;
    }
    const oc = node.onConfigure;
    node.onConfigure = function (...args) {
        const r = oc ? oc.apply(this, args) : undefined;
        focusForChannel(node, st, true);       // 存过的 api 工作流重开后要能看见 api_model
        applySoon(node);                       // 载入时 widget 是重建的，隐藏位要重打
        return r;
    };
    return true;
}

const smiths = () => (app?.graph?.nodes || []).filter((n) => n && n.type === TYPE);

let booted = false;
let tries = 0;

function guard() {
    for (const node of smiths()) {
        try {
            if (!node.__mossllmBar) mount(node);
            else if (node.__mossllmBar.nodeType) apply(node);   // 抗覆盖：别的扩展可能改回 hidden
        } catch (e) { /* 这一轮跳过，绝不掀画布 */ }
    }
}

function boot(why) {
    if (booted) return;
    try {
        if (!document.body || !app || !app.graph) {
            if (tries++ < 60) setTimeout(() => boot("retry"), 500);     // 最多 30s
            return;
        }
        booted = true;
        // 语言按钮是核心 i18n 画的（挂在面板的 langSlot 里）—— 它发广播，这里跟着换
        // 自己的分组标题 / 摘要 / 说明行；38 个参数的标签与提示由 `applyLang` 负责。
        try {
            window.addEventListener(LANG_EVENT, (e) => {
                const lang = String((e && e.detail && e.detail.lang) || "");
                T = lang ? I18N[lang === "zh" ? "zh" : "en"] : null;
                for (const n of smiths()) {
                    try { apply(n); } catch (err) { /* 单个节点失败不影响其它 */ }
                }
            });
        } catch (e) { /* 无 window */ }
        guard();
        // 载入工作流时 `nodeCreated` 不一定触发（i18n 实测同一坑），所以留一轮兜底轮询。
        setInterval(guard, 1500);
        console.info("%s: 就绪（入口 %s，本节点 %d 个）", TAG, why, smiths().length);
    } catch (e) {
        console.error("%s: 启动异常（面板不可用，参数全部照常显示）", TAG, e);
    }
}

app.registerExtension({
    name: "mosssb.llm.panel",
    async setup() { boot("setup"); },
    nodeCreated(node) { if (node && node.type === TYPE) mount(node); },
});

boot("module");

// 排障入口：控制台 `MOSS_SB_LLM_PANEL.where()` / `.focus("local")` / `.expand()` / `.values()`
try {
    globalThis.MOSS_SB_LLM_PANEL = {
        where: () => smiths().map((n) => ({
            id: n.id,
            mounted: !!(n.__mossllmBar && n.__mossllmBar.nodeType),
            widgets: realWidgets(n).length,
            hidden: realWidgets(n).filter((w) => w.options && w.options.hidden).length,
            focus: n.__mossllm ? n.__mossllm.focus : null,
            task: taskOf(n),
            force: !!(n.__mossllm && n.__mossllm.force),
            dirty: GROUPS.filter((g) => !g.always)
                .map((g) => g.key + ":" + countDirty(n, g)).join(","),
        })),
        focus: (key) => { for (const n of smiths()) { stateOf(n).force = false; stateOf(n).focus = key || null; apply(n); } },
        expand: () => { for (const n of smiths()) { stateOf(n).force = true; apply(n); } },
        fold: () => { for (const n of smiths()) { stateOf(n).force = false; stateOf(n).focus = null; apply(n); } },
        values: () => smiths().map((n) => (n.serialize?.().widgets_values || []).length),
    };
} catch (e) { /* 无 window */ }
