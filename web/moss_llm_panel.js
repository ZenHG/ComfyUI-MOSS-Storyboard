// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// LLM Prompt · 参数面板 —— 18 个参数的状态行与行内小工具
//
// 设计口径（美学 / 高频用户两条视角，2026-10-30）：
//   1. **默认只露常驻 5 项**（task/channel/dialect/model/text）。这个节点的日常动作是
//      「贴图/贴文本 → 跑」，其余 34 项是配置而不是操作。节点因此从 978 px 降到 306 px
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
//      开关条透明底 + 上边一道发丝线，读起来是工具栏而不是第 40 个参数。
//   5. **说明行高度恒定**（`height:2.7em`）：悬停换 tooltip 时行数变化不再顶动下面的参数
//      （2026-10-30 实测：这是鼠标定位失败的直接原因）。「中/EN」按钮挂在说明行右端的
//      `langSlot`（核心 i18n 画），不另开 DOM 行；切语言时面板文案跟着一起换。
//
// 🔴 一条都不许碰存档。`widgets_values` 按**位置**存（CONTRIBUTING 硬规矩 1、docs/10 §三·4）。
//    这里只改 `widget.options.hidden`（宿主的 byExtension 抑制位）+ 给宿主的行元素加类名。
//    实测前端 1.57.6 / Vue 节点模式：隐藏的行不画、`serialize()` 照样按 `serialize !== false`
//    收值 ⇒ 隐藏不改变存档格数与位置；折叠状态只活在节点实例的 `__mossllm` 上，不序列化。
//    DOM 开关条那一格由 `moss_sb_widget_order.js::installSaveCleanup` 在存盘时剔掉（实测 19 格）。
//    **唯一会写进 JSON 的东西是节点 `size`**（`fitHeight` 按可见内容调高度）—— 那是画布尺寸，
//    不是参数值；改分组时会重算，用户手拖的高度在下次换组时被取代（可见、可撤销）。
//
// 为什么不用宿主自带的「显示高级参数」：18 项一屏放得下，不需要按桶显隐；
//    面板只提供一行只读状态与两个行内小工具。（`advanced` 在各渲染层的实际落点随前端
//    版本变，这里不依赖它。）
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
/**
 * 信息架构：按**参数名**分组（名字是契约，位置不是）。表里没写的参数落进 `other` 且常显
 * —— 以后往参数表尾部追加新旋钮时，默认是「看得见」，不会因为这张表没同步就被藏掉。
 */
/**
 * 信息架构：按**参数名**分组（名字是契约，位置不是）。0.5.2 起 18 项一屏全显示，
 * 这张表不再决定任何显隐，只剩两个消费方：控制台排障的「与默认不同」计数，
 * 和给读代码的人一张参数地图。
 */
const GROUPS = [
    // 决策：每次都要动的三项 + 模型（本地/API 都可能要，PE 改写器也读它）。
    { key: "core", items: ["task", "model", "text"] },
    // 写法：怎么写、写给哪个后端。
    { key: "style", items: ["style_class", "style", "target", "style_note"] },
    // 采样与内容：写多长、多稳、保留什么。
    { key: "sampling", items: ["length", "temperature", "panels", "language", "keep", "seed"] },
    // 在线连接：api_base 留空 = 本机（通道由后端 smith() 推断，节点上没有 channel 格）。
    { key: "api", items: ["api_base", "api_model", "api_key"] },
    // 本机运行：运行时来源与快慢档。
    { key: "local", items: ["runtime", "speed"] },
];

/** seed 每次跑都会变、「生成后控制」是它的伴侣 —— 拿它们算「已改」只会永远亮着。 */
const DIRTY_SKIP = new Set(["seed", "control_after_generate"]);

// 全走宿主 CSS 变量；置顶 order 与临时网格样式例外。DOM 顺序 ≠ 视觉顺序，见 fitHeight。
// `flex:0 0 auto` 必须有：宿主给 DOM 组件的单元格套了 `*:flex-1`（flex-grow:1），
// 不覆盖它，开关条就会被撑到整个剩余高度，线条被 align-content 摊开成一列散字（实测 434px）。
const CSS = `
/* 开关条置顶：order 打在宿主包行的 wrapper 上；只改视觉顺序，不动存档。
   flex:0 0 auto 必须有：宿主给 DOM 组件的单元格套了 *:flex-1（flex-grow:1），
   不覆盖它，状态卡就会被撑到整个剩余高度（实测 434px）。 */
[data-widgets-grid-node-id] > div:has([data-widget-name="mosssb_llm_panel"]){order:-2 !important}
.mossllm-bar{display:flex;flex-direction:column;flex-wrap:nowrap;flex:0 0 auto;
 align-items:stretch;gap:0;box-sizing:border-box;width:100%;min-width:0;max-width:100%;
 overflow:hidden;padding:0;border-top:1px solid var(--border-color,#4e4e4e);
 color:var(--descrip-text,#999);font:12px/1.5 system-ui,sans-serif}
.mossllm-status{flex:0 0 auto;min-width:0;display:flex;align-items:center;gap:10px;
 height:38px;padding:0 10px;overflow:hidden;border-bottom:1px solid var(--border-color,#4e4e4e)}
.mossllm-status .mossllm-hint{flex:1 1 auto;min-width:0;height:auto;line-height:1.4;
 font-size:15px;font-weight:500;color:var(--input-text,#ddd);opacity:1;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mossllm-status .mossllm-warn{flex:0 1 auto;max-width:45%;min-width:0;font-size:12px;
 font-weight:400;background:transparent;border:0;padding:0;color:#e8b64c;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.mossllm-status .mossllm-warn[hidden]{display:none}
.mossllm-status .mossllm-warn:hover{background:transparent;text-decoration:underline}
.mossllm-status .mossllm-langslot{flex:0 0 auto;min-width:0;max-width:64px;overflow:hidden}
.mossllm-langslot{flex:0 0 auto;display:flex;align-items:center;min-height:1.5em}
/* 「↻ 拉取」贴在 api_model 行右端 —— 控件必须挨着它填的那一格，放顶部条上等于没放。
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
/* 拉到的模型清单：与 ↻ 同盒，拉过才出现（hidden），选完即回位 —— 只是快捷方式，不是值的事实源 */
.mossllm-pick select{flex:0 1 auto;min-width:0;max-width:200px;height:20px;padding:0 3px;margin-right:4px;
 border-radius:5px;border:1px solid var(--border-color,#4e4e4e);
 background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);cursor:pointer;
 font:11.5px/1 system-ui,sans-serif}
.mossllm-pick select[hidden]{display:none}
.mossllm-pick select:hover{background:var(--comfy-menu-bg,#333)}
/* 「常用端点」贴在 api_base 行右端 —— 与 ↻ 同一套路。 */
[data-widgets-grid-node-id] [data-widget-name="api_base"]{position:relative}
.mossllm-ep{position:absolute;right:4px;top:50%;transform:translateY(-50%);z-index:3;max-width:52%}
.mossllm-ep select{max-width:100%;height:20px;padding:0 3px;border-radius:5px;
 border:1px solid var(--border-color,#4e4e4e);background:var(--comfy-input-bg,#222);
 color:var(--input-text,#ddd);cursor:pointer;font:11.5px/1 system-ui,sans-serif}
.mossllm-ep select:hover{background:var(--comfy-menu-bg,#333)}
/* 输入框右侧留出下拉的宽度，否则长 URL 的尾巴被盖住（只作用于 api_base 这一格）。 */
[data-widgets-grid-node-id] [data-widget-name="api_base"] input{padding-right:116px}
@keyframes mossllm-spin{to{transform:rotate(360deg)}}
`;

/** 面板自己的词条。参数标签 / 提示不在这一层 —— 唯一源在 i18n 与后端 tooltip。
 *  0.5.2 起面板只剩：↻ 拉取、常用端点下拉，词条也就只剩这两组。 */
const I18N = {
    zh: {
        fetchTip: "用当前 api_base 与 Key 拉模型清单，灌进左边这个下拉；拉不到也可以直接手填模型名",
        fetched: "已拉到 %d 个模型：下拉选或直接手填",
        fetching: "拉模型清单中… %ds",
        fetchFail: "拉模型失败", needBase: "先填 api_base 再拉模型",
        mdlPh: "选模型", mdlTip: "这次拉到的模型；手填任何 id 也一样能用",
        ep_custom: "自定义（不动）",
        ep_deepseek: "DeepSeek", ep_opencode: "OpenCode", ep_odesign: "OpenDesign CoRouter",
        ep_ollama: "Ollama 本地", ep_lmstudio: "LM Studio 本地",
        epDone: "已填 %s 端点，点 ↻ 拉模型",
        epTip: "选常用端点会把地址写进 api_base（填了就不再走本机）；「自定义」不动当前值",
    },
    en: {
        fetchTip: "Fetch models for this api_base + key into the dropdown; you can always type the id instead",
        fetched: "%d models fetched — pick from the dropdown or type the id",
        fetching: "Fetching model list… %ds",
        fetchFail: "Model list failed", needBase: "Fill api_base first",
        mdlPh: "Pick model", mdlTip: "Models from the last fetch; typing any id works too",
        ep_custom: "Custom (leave as is)",
        ep_deepseek: "DeepSeek", ep_opencode: "OpenCode", ep_odesign: "OpenDesign CoRouter",
        ep_ollama: "Ollama (local)", ep_lmstudio: "LM Studio (local)",
        epDone: "Filled %s endpoint; press the arrow to fetch models",
        epTip: "Picking a known endpoint writes api_base (non-empty = online);"
            + " Custom leaves the current value alone",
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

/** 占位替换按**出现顺序**吃参数：模板里 %s 与 %d 混用（「已套用「%s」，改了 %d 项」）也照填。 */
const fmt = (s, ...a) => String(s).replace(/%[sd]/g, () => String(a.shift()));

const widgetOf = (node, name) => (node.widgets || []).find((w) => w && w.name === name);
const hideable = (w) => !!w && typeof w.syncVisibilityFromOptions === "function";
const realWidgets = (node) => (node.widgets || []).filter((w) => w && w.name !== BAR_NAME);
/** 旧稿把中文写进 task 枚举；归一口径与后端 _LEGACY_VALUES 一致（`compile` 是改名前的英文值）。 */
const TASK_LEGACY = { "反推": "caption", "扩写": "expand", "编辑指令": "edit",
    "编译": "assemble", "compile": "assemble" };
const taskOf = (node) => {
    const w = widgetOf(node, "task");
    const v = w ? String(w.value) : "";
    return TASK_LEGACY[v] || v;
};
/** 可静态判定的模式知识；tasks/channels 由 check_frontend.py 的 F11 与后端常量对账。
 *  PE 命中取决于模型目录里的文件，属运行时探测，不进这张表（docs/21 A2'）。
 *  通道不是参数：api_base 留空 = 本机、填了 = 在线，由后端 smith() 推断。 */
const MODE_SPEC = {
    tasks: ["caption", "expand", "edit", "assemble"],
    channels: ["local", "api"],
    inertOnCompile: ["model"],
};
/**
 * 常用端点：值与顺序都取自 `docs/20` 的「已实测的 api_base」表（2026-11-05 实测）。
 * 只放测过能直接跑通的那几个 —— 没实测过的地址不进来，免得「选中即失败」。
 * 用户自己的网关不在表里：下拉末项「自定义」不改值，手填照样能跑（api_base 仍是 STRING）。
 */
const ENDPOINTS = [
    { id: "deepseek", url: "https://api.deepseek.com/v1" },
    { id: "opencode", url: "https://opencode.ai/inference/go/openai/v1" },
    { id: "odesign", url: "https://amr-link.open-design.ai/v1" },
    { id: "ollama", url: "http://127.0.0.1:11434/v1" },
    { id: "lmstudio", url: "http://127.0.0.1:1234/v1" },
];
const ENDPOINTS_BY_NAME = Object.assign(
    { "本机（自动）": null },
    ...ENDPOINTS.map((e) => ({ [({ deepseek: "DeepSeek", opencode: "OpenCode",
        odesign: "OpenDesign CoRouter", ollama: "Ollama 本机", lmstudio: "LM Studio 本机" })[e.id] || e.id]: e }))
);

// ---------------------------------------------------------------------------
// 遥控器：宿主原生 combo，一个控件写多个原始 widget。serialize=false ⇒ 不进存档。
// 目的：用户要理解的概念从 39 个降到 8 个，且全部是宿主控件（存档 / 多实例 / 主题 /
// /prompt API 全部自动成立）。唯一自定义 DOM 只剩一行只读状态。
// ---------------------------------------------------------------------------
const STYLE_CLASS = {
    "连贯描述": ["英文详述", "英文通用", "中文"],
    "风格": ["写实摄影", "电影剧照", "胶片复古", "暗调氛围", "商业广告",
             "二次元插画", "水彩", "赛博朋克", "黑白纪实"],
    "自定义": ["自定义"],
    "标签串": ["英文标签"],
    "分段结构": ["宫格稿", "时间线六段式"],
};
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
        const st = owner && owner.__mossllm;
        if (!st || !st.hint || !row) return;
        await loadDefs();
        const name = row.getAttribute("data-widget-name");
        const w = owner && widgetOf(owner, name);
        const text = (w && w.tooltip) || tipOf(name) || st.summary || "";
        // 只给浏览器原生 tooltip。状态行文字与所有元素位置永不因 hover 改变。
        st.hint.title = text;
    }, true);
}

// ---------------------------------------------------------------------------
// 状态（只在内存，绝不进存档）
// ---------------------------------------------------------------------------
function stateOf(node) {
    let st = node.__mossllm;
    if (!st) {
        st = node.__mossllm = {
            focus: null,           // 排障入口用（MOSS_SB_LLM_PANEL.focus）
            force: false,          // 同上（expand / fold）
        };
    }
    return st;
}

/** 18 项一屏放得下：全部显示，不做任何隐藏（按组收起是旧 39 项时代的做法）。 */
function plan(node) {
    const shown = new Set();
    for (const w of realWidgets(node)) shown.add(w.name);
    return shown;
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
        // 按 DOM 高度数会把 39 行全算进去（实测因此把节点从 319 调到 1286）。
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
        + Math.max(0, rects[0].top - gr.top) + 8 * zoom;
    // 8px 余量：状态卡是开关条内独立卡片，有自己的 padding/border；
    // 行数多时累积误差会顶出 4px（实测 21 行时 scrollHeight 775 / clientHeight 771）。
    // 实测宿主：节点盒高 =（size 高 + 30）× zoom ⇒ size = 盒高 / zoom − 30。
    const want = Math.round((br.height - gr.height + content) / zoom - 30);
    if (!Number.isFinite(want) || want < 120) return false;
    if (Math.abs(node.size[1] - want) < 6) return false;
    node.setSize([node.size[0], want]);
    return true;
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

function apiVal(node, name) {
    const w = widgetOf(node, name);
    return w ? String(w.value == null ? "" : w.value) : "";
}

function setModel(node, id) {
    const w = widgetOf(node, "api_model");
    if (!w || !id) return;
    w.value = id;
    try { w.callback?.(id); } catch (e) { /* 老前端没有 callback */ }
    syncWidgetDom(node, "api_model");
    app?.graph?.setDirtyCanvas?.(true, true);
}

/** 程序化改值后把可见输入框对齐（宿主只绑 input 事件 → w.value，反向不刷）。 */
function syncWidgetDom(node, name) {
    const row = rowOf(node, name);
    const el = row && row.querySelector("input,textarea");
    const w = widgetOf(node, name);
    if (el && w) {
        const v = String(w.value == null ? "" : w.value);
        if (el.value !== v) el.value = v;   // 不派发事件：避免触发宿主回写循环
    }
}

/**
 * 写 `api_base` 并走它自己的 callback：挂载时装过钩子（换端点清旧模型 + 切通道），
 * 绕开 callback 就等于绕过那两件事 —— 用户会看到「填了端点，跑起来还去找本地 LLM」。
 */
function setApiBase(node, url) {
    const w = widgetOf(node, "api_base");
    if (!w || apiVal(node, "api_base") === url) return false;
    w.value = url;
    try { w.callback?.(url); } catch (e) { /* 老前端没有 callback */ }
    syncWidgetDom(node, "api_base");
    app?.graph?.setDirtyCanvas?.(true, true);
    return true;
}

/**
 * 把拉到的模型灌进 api_model 行内的下拉（`st.pick.sel`，与 ↻ 同一个盒子）。
 * `api_model` 本身是 STRING：手填任何 id 都成立，这个下拉只是「拉过之后的快捷选择」。
 */
function setModelChoices(node, models) {
    const st = node.__mossllm;
    if (!st || !st.pick || !st.pick.sel) return;
    const sel = st.pick.sel;
    const cur = apiVal(node, "api_model");
    const vals = (models || []).slice();
    // 手填过的自定义值也进清单，免得下次想换回来时找不到。
    if (cur && !vals.includes(cur)) vals.unshift(cur);
    sel.textContent = "";
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = t().mdlPh;
    sel.append(ph);
    for (const m of vals) {
        const o = document.createElement("option");
        o.value = m;
        o.textContent = m;
        sel.append(o);
    }
    sel.hidden = vals.length === 0;
    sel.value = "";
    sel.title = t().mdlTip;
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
        // 拉取的前提是 api_base 已填 ⇒ 通道本来就在线（0.5.2 起通道由 api_base
        // 是否为空推断，节点上没有 channel 格，没有「拨通道」一说）。
        // 拉完必须看得见：空着就选中第一个（否则屏幕上除了下拉内部什么都没变）。
        if (!apiVal(node, "api_model") && d.models.length) setModel(node, d.models[0]);
        setModelChoices(node, d.models);
        pick.err = null;
        delete pick.btn.dataset.bad;
        pick.note = fmt(t().fetched, d.models.length);
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
    }
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
    setModelChoices(node, []);   // 清掉上一批：下拉随即隐藏，手填值不受影响
}

/** 一次性说明：把状态的改变说出来，但不能一直占着摘要行（8s 后自己收）。 */
function noteOnce(node, st, text) {
    st.note = text;
    clearTimeout(st.noteTimer);
    st.noteTimer = setTimeout(() => {
        if (st.note === text) { st.note = ""; apply(node); }
    }, 8000);
}

/** 把「下拉选模型 + ↻ 拉取」贴在 api_model 行右端；宿主重建行元素后自愈。 */
function pinPicker(node, st) {
    const row = rowOf(node, "api_model");
    if (!row) return false;
    if (!st.pick) {
        const box = document.createElement("span");
        box.className = "mossllm-pick";
        // 下拉只是快捷选择（拉过才出现），不是值的事实源 —— api_model 是 STRING，手填为准。
        const sel = document.createElement("select");
        sel.hidden = true;
        for (const ev of ["pointerdown", "mousedown", "dblclick", "click"]) {
            sel.addEventListener(ev, (e) => e.stopPropagation());
        }
        sel.onchange = (e) => {
            e.stopPropagation();
            const id = String(sel.value || "");
            sel.value = "";                    // 用完回位：它是发射器，不是镜像
            if (id) setModel(node, id);
        };
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "↻";
        btn.onclick = (e) => {
            e.stopPropagation();
            fetchModels(node, st);
        };
        box.append(sel, btn);
        st.pick = { box, btn, sel, err: null, note: "", busy: "", timer: 0, autoDone: false };
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

/** 下拉只是 `api_base` 的镜像：值以存档为准，每次 apply 对一次，所以不会漂。 */
function syncEndpointSelect(node, st) {
    const ep = st.ep;
    if (!ep) return;
    const L = t();
    // 换语言 ⇒ 只改选项文字，不重建 select（别打断可能正开着的那次下拉）。
    if (ep.L !== L) {
        ep.L = L;
        const items = [{ url: "", text: L.ep_custom }]
            .concat(ENDPOINTS.map((p) => ({ url: p.url, text: L["ep_" + p.id] })));
        ep.sel.textContent = "";
        for (const it of items) {
            const o = document.createElement("option");
            o.value = it.url;
            o.textContent = it.text;
            ep.sel.append(o);
        }
        ep.sel.title = L.epTip;
    }
    const cur = apiVal(node, "api_base");
    ep.sel.value = ENDPOINTS.some((p) => p.url === cur) ? cur : "";
}

/**
 * 把「常用端点」贴在 api_base 行右端；宿主重建行元素后自愈（与 pinPicker 同一手法，
 * 两处各管一格：↻ 在 api_model 行，这个在 api_base 行）。
 * 它**不是 widget**：不进 `widgets_values`，存档格数一格都不动。
 */
function pinEndpoints(node, st) {
    const row = rowOf(node, "api_base");
    if (!row) return false;
    if (!st.ep) {
        const box = document.createElement("span");
        box.className = "mossllm-ep";
        const sel = document.createElement("select");
        sel.className = "mossllm-epsel";
        sel.dataset.mossllmEndpoint = "1";
        // 行内控件同样不该把点击变成拖节点（与开关条同一套事件）。
        for (const ev of ["pointerdown", "mousedown", "dblclick", "click"]) {
            sel.addEventListener(ev, (e) => e.stopPropagation());
        }
        sel.onchange = (e) => {
            e.stopPropagation();
            const url = String(sel.value || "");
            if (!url) { syncEndpointSelect(node, st); return; }   // 「自定义」：一个字节都不改
            if (setApiBase(node, url)) {
                const hit = ENDPOINTS.find((p) => p.url === url) || {};
                noteOnce(node, st, t().epDone.replace("%s", t()["ep_" + hit.id] || url));
            }
            applySoon(node);
        };
        box.append(sel);
        st.ep = { box, sel, L: null };
    }
    if (st.ep.box.parentElement !== row) row.appendChild(st.ep.box);
    syncEndpointSelect(node, st);
    return true;
}


function buildBar(node, st) {
    const bar = document.createElement("div");
    bar.className = "mossllm-bar";
    bar.style.flex = "0 0 auto";
    bar.style.alignContent = "flex-start";
    for (const ev of ["pointerdown", "mousedown", "dblclick", "keydown", "click"]) {
        bar.addEventListener(ev, (e) => e.stopPropagation());
    }
    st.statusCard = document.createElement("div");
    st.statusCard.className = "mossllm-status";
    st.hint = document.createElement("span");
    st.hint.className = "mossllm-hint";
    st.warn = document.createElement("span");
    st.warn.className = "mossllm-warn";
    st.warn.hidden = true;
    st.langSlot = document.createElement("span");
    st.langSlot.className = "mossllm-langslot";
    st.statusCard.append(st.hint, st.warn, st.langSlot);
    bar.append(st.statusCard);
    return bar;
}

/** 状态行每帧从 widget / st 读回，不另存与真实值可能漂移的状态。 */
function paintBar(node, st) {
    if (!st.hint) return;
    const task = String(apiVal(node, "task") || "反推");
    const style = String(apiVal(node, "style") || "");
    const hasImg = (node.inputs || []).some((i) => i && i.name
        && i.name.startsWith("image") && i.link != null);
    const txt = String(apiVal(node, "text") || "").trim();
    const online = !!String(apiVal(node, "api_base") || "").trim();
    st.hint.textContent = task + " · " + (online ? "在线服务" : "本机")
        + (style ? " ｜ 写法：" + style : "");
    let need = "";
    if ((task === "反推" || task === "洗图") && !hasImg) need = "还接一张图";
    else if (task === "扩写" && !txt) need = "还填一句想写什么";
    else if (online && !String(apiVal(node, "api_model") || "").trim()) need = "还填在线模型名";
    st.warn.hidden = !need;
    st.warn.textContent = need;
    st.hint.title = need || "可以直接运行";
    st.summary = st.hint.textContent;
}

/** style_class → style 子档级联。 */
function syncStyle(node) {
    const cls = widgetOf(node, "style_class");
    const sty = widgetOf(node, "style");
    if (!cls || !sty) return;
    const subs = STYLE_CLASS[String(cls.value)] || [];
    if (!subs.length) return;
    // 宿主不同版本读不同字段：options.values 与 w.values 都写。
    const cur = (sty.options && Array.isArray(sty.options.values)) ? sty.options.values
        : (Array.isArray(sty.values) ? sty.values : null);
    const same = cur && cur.length === subs.length && cur.every((v, i) => v === subs[i]);
    if (!same) {
        if (!sty.options) sty.options = {};
        sty.options.values = subs.slice();
        sty.values = subs.slice();
    }
    if (!subs.includes(String(sty.value))) sty.value = subs[0];
}

function applySoon(node) {
    apply(node);
    try { requestAnimationFrame(() => apply(node)); } catch (e) { /* 无 rAF */ }
    setTimeout(() => {
        apply(node);
        // 行重建/CSS order 晚于首帧；补算一次，尺寸已对时 fitHeight 自带早退。
        // 必须与 fitHeight 的排序同做，否则会把虚高 want 写得更稳。
        try { fitHeight(node, plan(node)); } catch (e) { /* noop */ }
    }, 300);
}

/**
 * 开关条**置顶**：`addDOMWidget` 只会追加到末尾，而末尾意味着每展开一组它就被顶下一截
 * （2026-10-30 实测反馈：点开一组，开关就跑到最低下）。这里把那个 widget 挪到数组首位 ——
 * 只动「不参与存档的 DOM 组件」的位置，18 个参数之间的相对顺序一个都没变；
 * 存盘时 `installSaveCleanup` 照样把它那一格剔掉（实测仍 19 格、逐位一致）。
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

function apply(node) {
    const st = stateOf(node);
    ensureBarFirst(node, st);
    syncStyle(node);
    const shown = plan(node);
    paintBar(node, st);
    try { pinPicker(node, st); } catch (e) { /* 行内挂件失败不影响状态行 */ }
    try { pinEndpoints(node, st); } catch (e) { /* 同上 */ }
    return shown.size;
}

// ---------------------------------------------------------------------------
// 挂载
// ---------------------------------------------------------------------------
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
    // 存盘时清空 Key（依赖倒置：通用模块只认字段名集合，不认识业务字段）。
    node.__mosssbOmitOnSave = new Set(["api_key"]);
    installHintListener();
    applySoon(node);
    loadDefs();

    // 换端点 ⇒ 上一批模型名立刻作废。等 1.5s 兜底轮询太慢，更像"没反应"。
    // 同一处顺手把通道拨过去：填了端点 = 要用这个 API（踩过的坑：填完直接跑，去本地找 llama-server）。
    const bw = widgetOf(node, "api_base");
    if (bw) {
        const orig = bw.callback;
        bw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            // 只重画状态行 —— 通道由 api_base 是否为空决定，这里不替用户改值。
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
            // 只重画状态行。
            applySoon(node);
            return r;
        };
        mw.__mossChanHooked = true;
    }
    hookAllRepaint(node);
    const oc = node.onConfigure;
    node.onConfigure = function (...args) {
        const r = oc ? oc.apply(this, args) : undefined;
        applySoon(node);                       // 载入时 widget 是重建的，行内挂件位置要重打
        return r;
    };
    return true;
}

const smiths = () => (app?.graph?.nodes || []).filter((n) => n && n.type === TYPE);

let booted = false;
let tries = 0;

/** 已有专用回调的 widget（它们自己会调 applySoon）不重复包装。 */
const REPAINT_SKIP = new Set(["api_base", "api_model", "task", "model"]);
/** 其余 widget 一改值立刻重画；宿主重建 widget 后由 guard 补挂。 */
function hookAllRepaint(node) {
    for (const w of realWidgets(node)) {
        if (!w || w.__mossRepaintHooked || REPAINT_SKIP.has(w.name)) continue;
        const orig = w.callback;
        w.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            applySoon(node);
            return r;
        };
        w.__mossRepaintHooked = true;
    }
}

function guard() {
    for (const node of smiths()) {
        try {
            if (!node.__mossllmBar) mount(node);
            else if (node.__mossllmBar.nodeType) {
                hookAllRepaint(node);
                apply(node);                                   // 抗覆盖：别的扩展可能改回 hidden
            }
        } catch (e) {
            // 静默会让「状态行一直空」这种问题无从定位。出声，但每节点只报一次。
            if (!node.__mossErrOnce) {
                node.__mossErrOnce = true;
                console.error(TAG, "apply 失败（后续每轮仍会重试）", e);
            }
        }
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
        // 状态行；18 个参数的标签与提示由 `applyLang` 负责。
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
