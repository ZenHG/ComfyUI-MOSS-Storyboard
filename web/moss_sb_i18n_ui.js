// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// MOSS Storyboard · 画布侧中英切换 —— **挂钩层**（DOM / litegraph）
//
// 结构照 H 仓 `ComfyUI-H3-Latent-Relay/web/relay_kit_i18n_ui.js`：
//   · 「中/EN」按钮做成**节点内的 DOM 组件**（`node.addDOMWidget`），不挂页面浮层
//     —— 浮层容易被画布/侧栏挡住，用户也不知道去哪找；
//   · 按钮文字走 **CSS 伪元素**，不用 `textContent`：界面汉化插件按 DOM 文本翻译，
//     会把 "EN" 翻成「英语」，连 `translate="no"` 都不一定认；CSS `content` 不是 textNode，
//     翻译插件碰不到（H 仓 2026-10-06 真机实测）；
//   · 默认**跟随宿主语言**，用户点按钮才写 localStorage 覆盖；`.clear()` 可清掉覆盖。
//
// 🔴 启动不依赖 `setup()` 生命周期。实测（前端 1.57.1）模块能执行、`registerExtension`
//    也调了，但 `setup()` 没被调用 ⇒ 按钮永不出现。所以模块自己轮询到
//    `document.body` + `app.graph` 就启动；`setup` / `nodeCreated` 只作加速与兜底。
//
// 🔴 任何异常都不许掀画布：全部 try/catch，失败只是"没翻译"。

import { app } from "../../scripts/app.js";
import {
    LANG_EVENT, LANG_KEY, NODES, applyLang, defaultLang, normLang, ownNodes,
} from "./moss_sb_i18n.js";
import { installSaveCleanup } from "./moss_sb_widget_order.js";

const TAG = "[MOSS SB] i18n";
const BTN_CLASS = "mosssb-i18n-btn";
const STYLE_ID = "mosssb-i18n-btn-style";
const BAR_NAME = "mosssb_i18n_bar";

let _lang = null;
let booted = false;
let tries = 0;

/** 读宿主界面语言：先 `<html lang>`，再退 `Comfy.Locale`。 */
function readHostLang() {
    try {
        const h = document.documentElement && document.documentElement.getAttribute("lang");
        if (h) return h;
    } catch (e) { /* 无 DOM */ }
    try {
        if (globalThis.Comfy && Comfy.Locale) return Comfy.Locale;
    } catch (e) { /* 旧前端 */ }
    return "";
}

function readStored() {
    try { return globalThis.localStorage?.getItem(LANG_KEY) || ""; } catch (e) { return ""; }
}

/** 当前语言：有覆盖层用它，否则跟随宿主。 */
export function currentLang() {
    if (_lang == null) _lang = normLang(readStored() || defaultLang(readHostLang));
    return _lang;
}

/** 写覆盖层 + 应用 + 广播。**不动宿主 `Comfy.Locale`**。 */
export function setLang(v) {
    const L = normLang(v);
    _lang = L;
    try { globalThis.localStorage?.setItem(LANG_KEY, L); } catch (e) { /* 隐私模式 */ }
    applyAll();
    paintAll();
    try { window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: { lang: L } })); } catch (e) { /* 老浏览器 */ }
    console.info("%s: lang -> %s", TAG, L);
    return L;
}

/** 清掉覆盖层 ⇒ 回到"跟随宿主语言"（排障用）。 */
export function clearLangOverride() {
    _lang = null;
    try { globalThis.localStorage?.removeItem(LANG_KEY); } catch (e) { /* 隐私模式 */ }
    applyAll();
    paintAll();
    try { window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: { lang: currentLang() } })); } catch (e) { /* 老浏览器 */ }
    return currentLang();
}

export function toggleLang() {
    return setLang(currentLang() === "zh" ? "en" : "zh");
}

function applyAll() {
    let n = 0;
    try {
        for (const node of ownNodes(app?.graph?._nodes)) {
            try { applyLang(node, currentLang()); n += 1; } catch (e) { /* 单节点失败不影响其它 */ }
        }
        if (n) app?.canvas?.setDirty(true, true);
    } catch (e) { /* 画布还没就绪 */ }
    return n;
}

// ---------------------------------------------------------------------------
// 按钮：节点内 DOM 组件
// ---------------------------------------------------------------------------
function ensureStyle() {
    try {
        if (document.getElementById(STYLE_ID)) return;
        const st = document.createElement("style");
        st.id = STYLE_ID;
        // 「中」写成 \4e2d 转义：样式表与界面语言无关，英文界面下也稳定
        st.textContent = `.${BTN_CLASS}::before{content:'EN'}`
            + `.${BTN_CLASS}.zh::before{content:'\\4e2d'}`;
        (document.head || document.documentElement).appendChild(st);
    } catch (e) { /* 注入失败 ⇒ 按钮无字但功能不受影响 */ }
}

function paintBtn(btn) {
    if (!btn) return;
    const zh = currentLang() === "zh";
    try { btn.classList.toggle("zh", zh); } catch (e) { /* 类名都设不了就算了 */ }
    btn.style.background = zh ? "#2d6cdf" : "#c0392b";
    btn.title = zh
        ? "MOSS 故事板：切换到 English（只改本包节点，不改宿主语言）"
        : "MOSS Storyboard: switch to 中文 (this package's nodes only)";
}

const _BTNS = new Set();

function paintAll() {
    for (const b of Array.from(_BTNS)) paintBtn(b);
}

function makeBtn() {
    const btn = document.createElement("button");
    btn.type = "button";
    // 标准"不要翻译我"标记：多数汉化插件认这两个
    btn.setAttribute("translate", "no");
    btn.classList.add("notranslate", BTN_CLASS);
    ensureStyle();
    btn.style.cssText = "color:#fff;border:1px solid rgba(0,0,0,.35);border-radius:4px;"
        + "width:36px;height:22px;font-size:12px;font-weight:bold;cursor:pointer;"
        + "display:flex;align-items:center;justify-content:center;padding:0;margin:0;"
        + "flex:0 0 auto;user-select:none;transition:filter .15s;";
    btn.onmouseenter = () => (btn.style.filter = "brightness(1.2)");
    btn.onmouseleave = () => (btn.style.filter = "none");
    btn.onclick = (e) => { e.stopPropagation(); try { toggleLang(); } catch (err) { /* 不掀画布 */ } };
    paintBtn(btn);
    _BTNS.add(btn);
    return btn;
}

/**
 * 存盘前剔掉**所有**虚拟 widget（DOM 组件）的值 —— 实现在 `moss_sb_widget_order.js`。
 *
 * 为什么必须自己剔：`addDOMWidget(..., { serialize: false })` 只是**告诉宿主**别存，
 * 实测（前端 1.57.4）这版 litegraph 的 `serialize()` 照样把它写进 `widgets_values`。
 * 而它们都是载入后才挂上的 —— 于是「存档 n 项 / 载入那一刻 n-1 个 widget」，
 * 每保存一次就差一格，下次打开整条值右移一位、`panels/panel_aspect/init` 一起变红。
 *
 * 编辑器挂载时也会装同一个钩子（它自己那个 DOM 组件同样会写一格），
 * 所以这里只是"另一条入口"，重复调用无副作用。
 */

/**
 * 挂按钮。**优先挂进编辑器顶栏的插槽**（`node.__mossedUI.langSlot`）：
 * 单独开一个 DOM 组件行时，它和编辑器谁先挂上取决于挂载时机，实测同一份工作流
 * 按钮有时在编辑器上方、有时在下方，还白占 30~50px 高度。编辑器自己那一行永远对齐。
 * 编辑器没挂上（宿主不支持 DOM 组件 / 还没轮到挂）时才退回独立行。
 */
function mountBtn(node) {
    // 🔴 只给本包节点加按钮。官方节点、别人的节点一律不加（挂了就是往人家界面上塞东西）。
    if (!node || !NODES[String(node.type || "")]) return false;
    // 存盘钩子挂在这里而不是编辑器里：编辑器要保持"单文件、不 import 本地模块"（W9）。
    // 幂等，且放在 `__mosssbBar` 早退**之前** —— 已经有按钮的节点也要补装。
    installSaveCleanup(node);
    if (node.__mosssbBar) return false;

    const slot = node.__mossedUI && node.__mossedUI.langSlot;
    if (slot) {
        try {
            const btn = makeBtn();
            slot.appendChild(btn);
            node.__mosssbBar = btn;
            return true;
        } catch (e) { /* 落到下面的独立行 */ }
    }
    // 布局编辑器一定会有 DOM 界面：先等它挂上（下一轮 guard 再来），别急着占一行。
    // 连续几次都没等到就认命，退回独立行 —— 不能因为编辑器挂了按钮就消失。
    if (String(node.type || "") === "MossLayoutEditor") {
        const misses = (node.__mosssbWait = (node.__mosssbWait || 0) + 1);
        if (misses <= 4) return false;
    }

    try {
        const bar = document.createElement("div");
        // 左右内边距与编辑器根一致（10px）：语言条与下面那一列控件对同一条竖线。
        bar.style.cssText = "display:flex;align-items:center;gap:6px;padding:2px 10px;";
        bar.appendChild(makeBtn());
        for (const ev of ["pointerdown", "mousedown", "dblclick", "keydown", "click"]) {
            bar.addEventListener(ev, (e) => e.stopPropagation());   // 别让点按钮变成拖节点
        }
        // `serialize:false` = 绝不进工作流 JSON（分享出去不污染别人的图）
        node.addDOMWidget(BAR_NAME, "custom", bar, {
            serialize: false,
            hideOnZoom: false,
            getMinHeight: () => 28,
        });
        node.__mosssbBar = bar;
        installSaveCleanup(node);
        return true;
    } catch (e) {
        // 跨前端版本可能没有 addDOMWidget ⇒ 不掀画布，降级为"没有按钮但翻译照常"
        console.warn("%s: 该前端不支持节点 DOM 组件，按钮不可用（翻译仍生效）", TAG, e);
        return false;
    }
}

/**
 * 兜底入口：`addDOMWidget` 在个别前端版本不可用（会走 catch），此时节点上没有按钮。
 * 只要画布上**有本包节点**，就在右下角放一个全局按钮 —— 它同样只切本包节点。
 * 没有任何本包节点时不出现（不打扰别人的工作流）。
 */
function ensureFallbackButton() {
    try {
        const own = ownNodes(app?.graph?._nodes);
        const exist = document.getElementById("mosssb-lang");
        if (!own.length) { if (exist) exist.remove(); return; }
        if (_BTNS.size > 0) { if (exist) exist.remove(); return; }   // 节点内有按钮就不需要它
        if (exist || !document.body) return;
        const b = document.createElement("button");
        b.id = "mosssb-lang";
        b.type = "button";
        b.setAttribute("translate", "no");
        b.classList.add("notranslate");
        b.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:2147483000;"
            + "min-width:46px;height:30px;padding:0 10px;border-radius:6px;cursor:pointer;"
            + "border:1px solid rgba(255,255,255,.28);background:rgba(30,34,42,.92);color:#eaeaea;"
            + "font:600 12px/1 system-ui,sans-serif;user-select:none;box-shadow:0 2px 8px rgba(0,0,0,.35);";
        b.addEventListener("click", (e) => { e.stopPropagation(); toggleLang(); });
        document.body.appendChild(b);
        b.textContent = currentLang() === "zh" ? "中" : "EN";   // ⚠ 必须 append 之后再写文字
        console.info("%s: 节点内按钮不可用，已挂全局兜底按钮（只作用于本包节点）", TAG);
    } catch (e) { /* 兜底也失败就算了 */ }
}

function mountAllBtns() {
    let n = 0;
    for (const node of ownNodes(app?.graph?._nodes)) {
        if (mountBtn(node)) n += 1;
    }
    return n;
}

/**
 * 兜底轮询（1.5s）：干三件事
 *   ① 补挂按钮 —— 🔴 工作流**载入**时 `nodeCreated` 在部分前端版本不触发，
 *      实测（前端 1.57.1）载入后 10 个节点里本包 2 个 `hasBar=false`、标题还是空串。
 *      只靠 nodeCreated 会让"按钮不见了"，所以这里补一次（幂等，靠 `__mosssbBar` 去重）。
 *   ② 补翻译 —— 同上，载入路径不一定会调 applyLang。
 *   ③ 抗覆盖 —— 界面汉化插件会按自己的词典改回 label/tooltip，按标记比对写回。
 */
function guard() {
    try {
        applyAll();
        mountAllBtns();
        ensureFallbackButton();
    } catch (e) { /* 这一轮跳过 */ }
    try {
        for (const node of ownNodes(app?.graph?._nodes)) {
            for (const w of (node.widgets || [])) {
                if (!w) continue;
                if (w.__mosssbWant !== undefined && w.label !== w.__mosssbWant) w.label = w.__mosssbWant;
                if (w.__mosssbWantTip !== undefined && w.tooltip !== w.__mosssbWantTip) w.tooltip = w.__mosssbWantTip;
                // 多行文本框的标题是 placeholder，被别的东西改了也要写回
                if (w.__mosssbWantPh !== undefined
                    && w.element && w.element.getAttribute("placeholder") !== w.__mosssbWantPh) {
                    w.element.setAttribute("placeholder", w.__mosssbWantPh);
                }
            }
        }
    } catch (e) { /* 这一轮跳过 */ }
}

// ---------------------------------------------------------------------------
// 启动（不等 setup）
// ---------------------------------------------------------------------------
function bootstrap(why) {
    if (booted) return;
    try {
        if (!document.body || !app || !app.graph) {
            if (tries++ < 60) setTimeout(() => bootstrap("retry"), 500);   // 最多 30s
            return;
        }
        booted = true;
        applyAll();
        mountAllBtns();
        setInterval(guard, 1500);
        try { app.graph?.onGraphChanged?.(() => { applyAll(); mountAllBtns(); }); } catch (e) { /* 旧前端无此 API */ }
        console.info("%s: 就绪（lang=%s，本包节点 %d，入口=%s）", TAG, currentLang(),
            ownNodes(app?.graph?._nodes).length, why);
    } catch (e) {
        console.error("%s: 启动异常（翻译不可用，画布不受影响）", TAG, e);
    }
}

app.registerExtension({
    name: "mosssb.i18n",
    async setup() { bootstrap("setup"); },
    nodeCreated(node) {
        try {
            // 两个函数内部都判过 `NODES[type]`，这里再判一次是为了不白做功（外来节点占多数）
            if (!NODES[String(node?.type || "")]) return;
            applyLang(node, currentLang());
            mountBtn(node);          // 新建的本包节点立刻带按钮
        } catch (e) { /* 单节点失败忽略 */ }
    },
});

bootstrap("module");

// 排障入口：控制台 `MOSS_SB_LANG.where()` / `.set("zh")` / `.clear()`
try {
    globalThis.MOSS_SB_LANG = {
        get: currentLang,
        set: setLang,
        toggle: toggleLang,
        clear: clearLangOverride,
        apply: applyAll,
        nodes: NODES,
        // 排障用：这个前端的 `app` 是模块内变量，页面上没有全局 `app`，
        // 所以控制台/无头冒烟只能从这里拿。只读用途，别在业务代码里依赖它。
        graph: () => app?.graph,
        loadGraph: (wf) => app.loadGraphData(wf),
        where: () => ({
            booted,
            lang: currentLang(),
            nodes: ownNodes(app?.graph?._nodes).length,
            buttons: _BTNS.size,
        }),
    };
    console.info("%s: 模块已加载（v%s）", TAG, "0.1.0");
} catch (e) { /* 无 window */ }
