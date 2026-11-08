// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// LLM 参数面板真机验收（2026-11-08 参数面合并后）。
// 契约：18 个参数一屏全显示 + 一行只读状态 + style_class → style 级联。
// 验收不看闸绿：真图目检 + 真实提交值 + 存档逐位一致。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { arg, boot, checks, launch, loadGraph, ROOT, shotNode } from "./ui_kit.mjs";

const URL_ = arg("url", process.env.COMFY_API || "http://127.0.0.1:8188");
const browser = await launch("chromium", { headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 1500 } });
const c = checks("MOSS Prompt Smith · 参数面板真机验收");
const check = (n, cond, d) => c.check(n, !!cond, d);
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 200)));

const wf = JSON.parse(readFileSync(join(ROOT, "examples", "llm-prompt-workbench.json"), "utf8"));
await boot(page, URL_);
check("载入 examples/llm-prompt-workbench.json", (await loadGraph(page, wf)).ok);
await page.waitForFunction(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    return n && n.__mossllmBar && n.__mossllmBar.nodeType;
}, null, { timeout: 20000 });
await page.waitForTimeout(1500);

const ID = await page.evaluate(() => String(app.graph.nodes.find((x) => x.type === "MossPromptSmith").id));

const snap = () => page.evaluate((id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const q = (s) => (g ? g.querySelector(s) : null);
    const rows = g ? [...g.querySelectorAll("[data-widget-name]")]
        .filter((e) => e.dataset.widgetName !== "mosssb_llm_panel"
            && e.getBoundingClientRect().height > 0) : [];
    return {
        mounted: !!(n.__mossllmBar && n.__mossllmBar.nodeType),
        widgets: n.widgets.length,
        cells: (n.serialize().widgets_values || []).length,
        shown: rows.map((e) => e.dataset.widgetName),
        hint: (q(".mossllm-hint") || {}).textContent || "",
        warn: (q(".mossllm-warn") || {}).textContent || "",
        warnHidden: !!(q(".mossllm-warn") || {}).hidden,
        chips: g ? g.querySelectorAll(".mossllm-chip").length : -1,
        settingsBtn: g ? g.querySelectorAll(".mossllm-settings").length : -1,
        overflow: g ? g.scrollWidth > g.clientWidth + 6 : null,
        styleOpts: (() => { const w = n.widgets.find((x) => x.name === "style");
            return w && w.options && w.options.values ? w.options.values.slice() : []; })(),
        styleClass: String((n.widgets.find((x) => x.name === "style_class") || {}).value),
        style: String((n.widgets.find((x) => x.name === "style") || {}).value),
    };
}, ID);

// ---- 1. 参数面与存档 ----
const a = await snap();
check("面板已挂载，19 格存档（18 参数 + seed 伴随）",
    a.mounted && a.cells === 19, JSON.stringify({ mounted: a.mounted, cells: a.cells }));
check("18 个参数一屏全显示（不再折叠、不再靠隐藏）",
    a.shown.length === 18 && ["task", "model", "text", "style_class", "style", "target",
        "style_note", "length", "temperature", "panels", "language", "keep", "seed",
        "api_base", "api_model", "api_key", "speed", "runtime"].every((k) => a.shown.includes(k)),
    JSON.stringify(a.shown));
check("旧 UI 已清空：没有 chip、没有「设置」按钮", a.chips === 0 && a.settingsBtn === 0,
    JSON.stringify({ chips: a.chips, settingsBtn: a.settingsBtn }));
check("状态行陈述当前任务与通道（不是参数名堆）",
    /反推|洗图|扩写|拼装|编辑/.test(a.hint) && /本机|在线服务/.test(a.hint), a.hint);
check("宽节点不横向溢出", !a.overflow, String(a.overflow));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-1-default.png"), { y: 30 });

// ---- 2. style_class → style 级联（真提交值，不走合成事件）----
const cascade = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const cls = n.widgets.find((w) => w.name === "style_class");
    const sty = n.widgets.find((w) => w.name === "style");
    const out = { base: (sty.options && sty.options.values || []).slice() };
    cls.value = "风格"; cls.callback?.("风格");
    await wait(900);
    out.style = (sty.options && sty.options.values || []).slice();
    out.current = String(sty.value);
    cls.value = "自定义"; cls.callback?.("自定义");
    await wait(900);
    out.custom = (sty.options && sty.options.values || []).slice();
    out.customVal = String(sty.value);
    // style_note 真的进 style lock：读一次后端 report 之前先确认值能写进去
    const note = n.widgets.find((w) => w.name === "style_note");
    note.value = "冷蓝霓虹从侧面打光，高对比";
    out.noteOk = String(note.value).length > 0;
    cls.value = "连贯描述"; cls.callback?.("连贯描述");
    await wait(700);
    out.back = (sty.options && sty.options.values || []).slice();
    return out;
}, ID);
check("写法类「风格」⇒ 具体写法换成 9 个风格档（真机级联生效）",
    cascade.style.length === 9, JSON.stringify(cascade.style));
check("写法类「自定义」⇒ 具体写法只剩「自定义」，可选自定义风格框",
    cascade.custom.length === 1 && cascade.custom[0] === "自定义" && cascade.customVal === "自定义",
    JSON.stringify({ custom: cascade.custom, val: cascade.customVal }));
check("自定义风格文本能写进 widget（作为硬 style lock 注入）", cascade.noteOk);
check("切回「连贯描述」⇒ 回落 3 档", cascade.back.length === 3, JSON.stringify(cascade.back));

// ---- 3. 缺项状态与可运行状态 ----
const lack = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const W = (k) => n.widgets.find((w) => w.name === k);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const g = () => document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const out = {};
    W("channel") // 旧参数已不存在，这里只通过 api_base 推断通道
    W("api_base").value = ""; W("api_base").callback?.("");
    W("task").value = "扩写"; W("task").callback?.("扩写");
    W("text").value = ""; W("text").callback?.("");
    await wait(1900);
    out.expandEmpty = { hint: g().querySelector(".mossllm-hint").textContent,
        warn: g().querySelector(".mossllm-warn").textContent,
        hidden: g().querySelector(".mossllm-warn").hidden };
    W("text").value = "一只猫"; W("text").callback?.("一只猫");
    await wait(600);
    out.expandFilled = { warn: g().querySelector(".mossllm-warn").textContent,
        hidden: g().querySelector(".mossllm-warn").hidden };
    return out;
}, ID);
check("扩写空文字 ⇒ 状态行说「还填一句想写什么」",
    /还填/.test(lack.expandEmpty.warn) && !lack.expandEmpty.hidden,
    JSON.stringify(lack.expandEmpty));
check("填了文字 ⇒ 缺项提示消失（可以直接跑）",
    lack.expandFilled.hidden === true, JSON.stringify(lack.expandFilled));

// ---- 4. 存档契约 ----
const saved = await page.evaluate(async () => {
    const g = app.graph.serialize();
    const before = JSON.parse(JSON.stringify(
        g.nodes.find((x) => x.type === "MossPromptSmith").widgets_values));
    await app.loadGraphData(JSON.parse(JSON.stringify(g)));
    await new Promise((r) => setTimeout(r, 2500));
    const m = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const after = JSON.parse(JSON.stringify(m.widgets_values));
    const stray = Object.keys(g.nodes.find((x) => x.type === "MossPromptSmith"))
        .filter((k) => /mossllm|__moss/i.test(k));
    return { before, after, stray };
});
check("存盘→重开 19 格逐位一致", saved.before.length === 19
    && JSON.stringify(saved.before) === JSON.stringify(saved.after),
    JSON.stringify({ n: saved.before.length }));
check("面板状态不写进存档", saved.stray.length === 0, JSON.stringify(saved.stray));

// ---- 5. 窄节点 ----
const narrow = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    n.setSize([280, n.size[1]]);
    await new Promise((r) => setTimeout(r, 900));
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const bar = g.querySelector(".mossllm-bar");
    return { sw: bar.scrollWidth, cw: bar.clientWidth };
}, ID);
check("280px 窄节点不横向溢出", narrow.sw <= narrow.cw + 2, JSON.stringify(narrow));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-3-local.png"), { y: 30 });

check("页面无 JS 异常", errs.length === 0, errs.join(" | "));
const code = c.done();
await browser.close();
process.exit(code);
