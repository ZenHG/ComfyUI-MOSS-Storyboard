// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// MOSS Storyboard · 画布侧中英切换 —— **挂钩层**（DOM / litegraph）
// 纯逻辑全在 `moss_sb_i18n.js`（可 node 单测）。这里只做四件事：
//   ① 读宿主语言 ② 把语言灌到画布上的本包节点 ③ 放一个 中/EN 按钮 ④ 抗覆盖兜底
//
// 🔴 三条纪律：
//   1. **任何异常都不许掀画布** —— 全部 try/catch，失败就是"没翻译"，不是"打不开"。
//   2. `app.extensionManager` 在模块顶层还不存在 ⇒ 只能在 `setup()` 之后读。
//   3. 界面汉化插件（ComfyUI-Chinese-Translation 等）有守护轮询，会按它自己的词典
//      把我们的 label/tooltip 改回中文 ⇒ 兜底轮询按 `__mosssbWant` 比对后写回。

import { app } from "../../scripts/app.js";
import {
    LANG_EVENT, LANG_KEY, NODES, applyLang, defaultLang, loadLang, normLang, ownNodes, saveLang,
} from "./moss_sb_i18n.js";

let lang = "en";
let booted = false;

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

/** 把当前语言应用到画布上所有本包节点（幂等）。 */
function applyAll() {
    let n = 0;
    try {
        for (const node of ownNodes(app?.graph?._nodes)) {
            try { applyLang(node, lang); n += 1; } catch (e) { /* 单节点失败不影响其它 */ }
        }
        if (n) app?.canvas?.setDirty(true, true);
    } catch (e) { /* 画布还没就绪 */ }
    return n;
}

function setLang(next) {
    lang = normLang(next);
    saveLang(lang);
    applyAll();
    try { window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: { lang } })); } catch (e) { /* 无 window */ }
}

/** 抗覆盖：把被第三方插件改掉的本包 label/tooltip 写回（只认自己打过标记的）。 */
function guard() {
    try {
        for (const node of ownNodes(app?.graph?._nodes)) {
            for (const w of (node.widgets || [])) {
                if (!w) continue;
                if (w.__mosssbWant !== undefined && w.label !== w.__mosssbWant) {
                    w.label = w.__mosssbWant;
                }
                if (w.__mosssbWantTip !== undefined && w.tooltip !== w.__mosssbWantTip) {
                    w.tooltip = w.__mosssbWantTip;
                }
            }
        }
    } catch (e) { /* 轮询里出错就跳过这一轮 */ }
}

/** 右下角「中 / EN」按钮。已存在就不重复创建。 */
function mountButton() {
    try {
        if (!document.body || document.getElementById("mosssb-lang")) return;
        const b = document.createElement("button");
        b.id = "mosssb-lang";
        b.type = "button";
        b.title = "MOSS Storyboard: switch node labels / tooltips language";
        const paint = () => { b.textContent = lang === "zh" ? "中" : "EN"; };
        b.style.cssText = [
            "position:fixed", "right:14px", "bottom:14px", "z-index:10000",
            "width:38px", "height:30px", "border-radius:6px", "cursor:pointer",
            "border:1px solid rgba(255,255,255,.25)", "background:rgba(40,44,52,.85)",
            "color:#e6e6e6", "font:600 12px/1 system-ui,sans-serif", "user-select:none",
        ].join(";");
        paint();
        b.addEventListener("click", (e) => {
            e.stopPropagation();
            setLang(lang === "zh" ? "en" : "zh");
            paint();
        });
        document.body.appendChild(b);
        window.addEventListener(LANG_EVENT, paint);
    } catch (e) { /* 加不上按钮不影响翻译 */ }
}

app.registerExtension({
    name: "mosssb.i18n",
    async setup() {
        if (booted) return;
        booted = true;
        // 存储里有用户选择就用它，否则跟随宿主
        const saved = (() => {
            try { return globalThis.localStorage?.getItem(LANG_KEY) || ""; } catch (e) { return ""; }
        })();
        lang = saved ? normLang(saved) : defaultLang(readHostLang);
        applyAll();
        mountButton();
        // 兜底：1.5s 一轮，只改被外部改动的项；成本是几次字符串比较
        setInterval(guard, 1500);
        try { app.graph?.onGraphChanged?.(() => applyAll()); } catch (e) { /* 旧前端无此 API */ }
    },
    nodeCreated(node) {
        try { applyLang(node, lang); } catch (e) { /* 单个节点失败忽略 */ }
    },
});

// 供调试：控制台 `MOSS_SB_LANG.set("zh")`
try {
    globalThis.MOSS_SB_LANG = { get: () => lang, set: setLang, nodes: NODES, apply: applyAll };
} catch (e) { /* 无 window */ }
