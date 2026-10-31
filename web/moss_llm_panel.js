// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// MOSS Prompt Smith · 参数面板 —— 38 个参数的信息架构层
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
//   4. 视觉只用宿主自己的 CSS 变量（明/暗主题一起对），不覆盖宿主样式、不用 !important。
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
        "language", "thinking", "panels", "style_lock", "must_keep", "wash", "two_pass"] },
    { key: "local", channel: CH_LOCAL, items: ["mmproj", "runtime", "ctx_size", "gpu_layers",
        "cache_type_k", "cache_type_v", "flash_attn", "image_max_tokens", "ubatch", "parallel",
        "fit_on", "fit_target_mib", "mtp", "mtp_draft_n", "server_exe", "port", "release_after_run"] },
    { key: "api", channel: CH_API, items: ["api_base", "api_model", "api_key", "api_key_env"] },
];
const OTHER = { key: "other", always: true };
const BY_NAME = new Map();
for (const g of GROUPS) for (const n of g.items) BY_NAME.set(n, g);

/** seed 每次跑都会变、「生成后控制」是它的伴侣 —— 拿它们算「已改」只会永远亮着。 */
const DIRTY_SKIP = new Set(["seed", "control_after_generate"]);

// 全走宿主 CSS 变量（明/暗主题一起对）；不用 !important，不碰宿主核心样式。
// `flex:0 0 auto` 必须有：宿主给 DOM 组件的单元格套了 `*:flex-1`（flex-grow:1），
// 不覆盖它，开关条就会被撑到整个剩余高度，线条被 align-content 摊开成一列散字（实测 434px）。
const CSS = `
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
.mossllm-caret{width:9px;opacity:.55;font-size:11px;line-height:1}
.mossllm-count{opacity:.55;font-variant-numeric:tabular-nums}
.mossllm-dot{display:none;width:6px;height:6px;margin-left:2px;border-radius:50%;
 background:#5b9cf6;flex:0 0 auto}
.mossllm-chip[data-dirty="1"] .mossllm-dot{display:inline-block}
.mossllm-btn{flex:0 0 auto;margin-left:auto;padding:2px 6px;border:0;border-radius:6px;background:transparent;
 color:var(--descrip-text,#999);cursor:pointer;font:inherit;
 text-decoration:underline;text-underline-offset:2px}
.mossllm-btn:hover{color:var(--input-text,#ddd);background:var(--comfy-input-bg,#222)}
.mossllm-hintrow{flex:0 0 100%;display:flex;align-items:flex-start;gap:6px;min-width:0}
.mossllm-hint{flex:1 1 auto;min-width:0;overflow-wrap:anywhere;
 /* 🔴 高度必须恒定：悬停换 tooltip 时行数一变，下面每个参数都会跟着位移（实测：鼠标定位失败）。 */
 line-height:1.35;height:2.7em;overflow:hidden;opacity:.8}
.mossllm-langslot{flex:0 0 auto;display:flex;align-items:center;min-height:2.7em}
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
        backTip: "恢复分组：默认只露常驻，一次看一组" },
    en: { core: "Basic", tune: "Common", local: "Local engine · llama.cpp", api: "External API",
        other: "Other", offchannel: "other channel", expand: "Show all", back: "Grouped",
        count: "%d params · %d visible", dirty: "%d differ from default",
        openTip: "Show only this group", closeTip: "Fold back to Basic",
        expandTip: "Show all %d params, including the other channel's",
        hideTip: "Hiding only affects the canvas — values stay in the workflow",
        backTip: "Back to grouped view (Basic only, one group at a time)" },
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
            s.hint.title = "";
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
    const gated = ch === CH_LOCAL || ch === CH_API;
    if (cw && !gated && !st.warnedChannel) {
        st.warnedChannel = true;
        console.warn("%s: 不认识 channel 取值 %j ⇒ 停用通道标记（分组仍可用）", TAG, ch);
    }
    const shown = new Set();
    const off = new Set();                       // 通道对不上的组（只影响 chip 的标记）
    for (const w of realWidgets(node)) {
        const g = groupOf(w);
        const outside = gated && !!g.channel && g.channel !== ch;
        if (outside) off.add(g.key);
        // 优先级：接了线 > 一键全开 > 常驻 > 当前聚焦组
        const ok = isLinked(node, w.name) || st.force || !!g.always || g.key === st.focus;
        if (ok) shown.add(w.name);
    }
    return { shown, off };
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
    b.append(caret, label, count, dot);
    b.onclick = (e) => {
        e.stopPropagation();
        const wasForce = st.force;
        st.force = false;
        st.focus = wasForce ? g.key : (st.focus === g.key ? null : g.key);
        applySoon(node);
    };
    return { b, caret, label, count, dot };
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
    bar.append(st.btn, hintRow);
    return bar;
}

function paintBar(node, st, shown, off) {
    const L = t();
    let hidden = 0;
    for (const g of GROUPS) {
        if (g.always) continue;
        const { b, caret, label, count, dot } = st.chips[g.key];
        const total = g.items.filter((n) => widgetOf(node, n)).length;
        b.hidden = total === 0;                          // 参数表里没这些名字 ⇒ 不摆空开关
        if (!total) continue;
        const expanded = st.force || st.focus === g.key;
        const outside = off.has(g.key);
        const vis = g.items.filter((n) => shown.has(n)).length;
        hidden += total - vis;
        caret.textContent = expanded ? "▾" : "▸";
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
    // 「通道外」已经写在对应 chip 上，摘要里不再重复一遍（少一行字就少一分噪音）。
    const summary = fmt(L.count, all, all - hidden);
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
    setTimeout(() => apply(node), 300);
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
    const { shown, off } = plan(node, st);
    const changed = syncWidgets(node, shown);
    const found = paintHeaders(node, shown);
    if (found === 0 && !st.domWarned) {
        st.domWarned = true;
        console.warn("%s: 这个前端定位不到参数行（旧版画布）⇒ 组标题不画，显隐照常生效", TAG);
    }
    if (st.chips) paintBar(node, st, shown, off);
    if (changed || !st.fitted) st.fitted = fitHeight(node, shown) || !!st.fitted;
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
    installHintListener();
    applySoon(node);
    loadDefs();

    const cw = widgetOf(node, "channel");
    if (cw) {
        const orig = cw.callback;
        cw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            // 换通道 ⇒ 把参数面板切到那个通道的组：不切的话用户看不到 api_* / mmproj，
            // 填不了必填项还以为节点坏了。
            if (!st.force) {
                const ch = chanOf(String(cw.value));
                const want = GROUPS.find((g) => g.channel && g.channel === ch);
                const cur = GROUPS.find((g) => g.key === st.focus);
                if (want && (!cur || cur.channel !== want.channel)) st.focus = want.key;
            }
            applySoon(node);
            return r;
        };
    }
    const oc = node.onConfigure;
    node.onConfigure = function (...args) {
        const r = oc ? oc.apply(this, args) : undefined;
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
