// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// LLM 参数面板真机验收（2026-11-08 推倒重来版）。
// 验收不看闸绿：真图目检 + hover 零位移 + 零自造词 + 三梯度用户。
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

const visRows = (id) => page.evaluate((nid) => {
    const g = document.querySelector('[data-widgets-grid-node-id="' + nid + '"]');
    if (!g) return null;
    const rows = [...g.querySelectorAll("[data-widget-name]")]
        .filter((e) => e.dataset.widgetName !== "mosssb_llm_panel");
    const shown = rows.filter((e) => e.getBoundingClientRect().height > 0);
    return { shown: shown.length, names: shown.map((e) => e.dataset.widgetName),
        overflow: g.scrollWidth > g.clientWidth + 6,
        cut: (() => { const rs = shown; return rs.length
            ? rs[rs.length - 1].getBoundingClientRect().bottom
                > g.getBoundingClientRect().bottom + 1 : false; })() };
}, id);

const wf = JSON.parse(readFileSync(join(ROOT, "examples", "llm-prompt-workbench.json"), "utf8"));
await boot(page, URL_);
check("载入 examples/llm-prompt-workbench.json", (await loadGraph(page, wf)).ok);
await page.waitForFunction(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    return n && n.__mossllmBar && n.__mossllmBar.nodeType;
}, null, { timeout: 20000 });
await page.waitForTimeout(1200);

const ID = await page.evaluate(() => String(app.graph.nodes.find((x) => x.type === "MossPromptSmith").id));
const node = () => page.evaluate((id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const bar = g.querySelector(".mossllm-bar");
    const q = (s) => g.querySelector(s);
    const rect = (s) => { const e = q(s); if (!e) return null;
        const b = e.getBoundingClientRect(); return { top: Math.round(b.top), h: Math.round(b.height) }; };
    return {
        widgets: n.widgets.length, cells: (n.serialize().widgets_values || []).length,
        mounted: !!(n.__mossllmBar && n.__mossllmBar.nodeType),
        status: rect(".mossllm-status"), btn: rect(".mossllm-settings"),
        gapToRight: (() => {
            const t = q(".mossllm-toprow"), e = q(".mossllm-settings");
            return t && e ? Math.round(t.getBoundingClientRect().right
                - e.getBoundingClientRect().right) : null; })(),
        hint: (q(".mossllm-hint") || {}).textContent || "",
        warn: (q(".mossllm-warn") || {}).textContent || "",
        warnHidden: !!(q(".mossllm-warn") || {}).hidden,
        barText: bar ? bar.textContent : "",
        chips: g.querySelectorAll(".mossllm-chip").length,
        tabs: g.querySelectorAll("[data-mossllm-chip]").length,
        presets: g.querySelectorAll(".mossllm-preset, .mossllm-pztoggle").length,
        secs: g.querySelectorAll("[data-moss-sec]").length,
        noteHidden: !!(q(".mossllm-settings-note") || {}).hidden,
        presetBoxHidden: !!(q(".mossllm-presets-box") || {}).hidden,
        force: !!n.__mossllm.force,
    };
}, ID);

// ---- 1. 三层骨架 ----
const a = await node();
check("面板已挂载，39 个控件、39 格存档", a.mounted && a.widgets >= 39 && a.cells === 39,
    JSON.stringify({ mounted: a.mounted, widgets: a.widgets, cells: a.cells }));
let r = await visRows(ID);
check("默认只露 4 行（任务 / 通道 / 模型 / 文字）",
    r.shown === 4 && ["task", "channel", "model", "text"].every((k) => r.names.includes(k)),
    JSON.stringify(r));
check("默认态没有 tabs / 分区标题；预设区藏在设置里（默认隐藏）",
    a.chips === 0 && a.tabs === 0 && a.secs === 0 && a.presetBoxHidden,
    JSON.stringify({ chips: a.chips, tabs: a.tabs, secs: a.secs, presetBoxHidden: a.presetBoxHidden }));
check("顶层唯一按钮是「设置」，固定在状态行右侧",
    a.btn && a.status && a.btn.top === a.status.top && a.btn.h === a.status.h,
    JSON.stringify({ status: a.status, btn: a.btn }));
check("状态行只陈述输入→输出，不含自造词",
    /→|接一张图/.test(a.hint) && !/方言|拼装目标|引擎|采样|格式|两遍法|忽略杂项|看图写提示词|写得怎么样|输出给谁|本机怎么跑|本次/.test(a.barText),
    a.hint.slice(0, 90));
check("默认没有内容被裁掉", !r.cut && !r.overflow, JSON.stringify(r));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-1-default.png"), { y: 30 });

// ---- 2. hover 零位移 ----
const hover = await page.evaluate(async (id) => {
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const snap = () => {
        const btn = g.querySelector(".mossllm-settings").getBoundingClientRect();
        const st = g.querySelector(".mossllm-status").getBoundingClientRect();
        return { btnTop: Math.round(btn.top), btnLeft: Math.round(btn.left),
            statusH: Math.round(st.height), hint: g.querySelector(".mossllm-hint").textContent };
    };
    const before = snap();
    const row = g.querySelector('[data-widget-name="task"]');
    row.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
    await new Promise((r2) => setTimeout(r2, 500));
    const after = snap();
    document.body.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
    await new Promise((r2) => setTimeout(r2, 400));
    const back = snap();
    return { before, after, back,
        moved: after.btnTop - before.btnTop || after.btnLeft - before.btnLeft,
        grew: after.statusH - before.statusH, textChanged: after.hint !== before.hint };
}, ID);
check("hover 前后：按钮不动 / 状态行不长高 / 文字不换（零位移）",
    hover.moved === 0 && hover.grew === 0 && !hover.textChanged,
    JSON.stringify(hover));

// ---- 3. 设置：唯一入口，一下给全部 ----
await page.evaluate(() => document.querySelector(".mossllm-settings").click());
await page.waitForTimeout(900);
const b = await node();
r = await visRows(ID);
check("点「设置」⇒ 39 项全部可见，且给出「一般不用改」说明",
    b.force && r.shown >= 38 && !b.noteHidden && /一般不用改/.test(b.barText),
    JSON.stringify({ force: b.force, shown: r.shown, noteHidden: b.noteHidden }));
check("设置展开后按钮仍贴顶行右边界",
    b.btn && b.status && b.btn.top === b.status.top && b.gapToRight === 0
    && a.gapToRight === 0, JSON.stringify({ before: a.gapToRight, after: b.gapToRight }));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-2-tune.png"), { y: 30 });
await page.evaluate(() => document.querySelector(".mossllm-settings").click());
await page.waitForTimeout(900);
r = await visRows(ID);
check("收起设置 ⇒ 回到 4 行", r.shown === 4, JSON.stringify(r));
const gapBack = await page.evaluate((id) => {
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    return Math.round(g.querySelector(".mossllm-toprow").getBoundingClientRect().right
        - g.querySelector(".mossllm-settings").getBoundingClientRect().right);
}, ID);
check("展开→收起后设置按钮始终贴右（不再左右跳）", gapBack === 0,
    JSON.stringify({ first: a.gapToRight, expanded: b.gapToRight, collapsed: gapBack }));

// ---- 3b. 预设嵌套在设置内（不做顶层按钮）----
const nest = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const W = (k) => n.widgets.find((w) => w.name === k);
    const wait = (ms) => new Promise((r2) => setTimeout(r2, ms));
    g.querySelector(".mossllm-settings").click();       // 打开设置
    await wait(700);
    const box = g.querySelector(".mossllm-presets-box");
    const out = { visible: !box.hidden,
        bodyHidden: g.querySelector(".mossllm-pzbody").hidden };
    out.groups = [...g.querySelectorAll("[data-pz-group]")].map((b) => b.textContent);
    const eps = [...g.querySelectorAll("[data-pz-endpoint]")];
    out.addresses = eps.length;
    if (eps.length) eps[0].click();
    await wait(500);
    out.base = String(W("api_base").value);
    out.chan = String(W("channel").value);
    g.querySelector('[data-pz-group="tune"]').click();
    await wait(500);
    out.tunes = [...g.querySelectorAll("[data-moss-preset]")].map((b) => b.textContent);
    g.querySelector(".mossllm-settings").click();       // 收起
    await wait(600);
    out.hiddenAfter = box.hidden;
    return out;
}, ID);
check("设置展开即见嵌套预设（0 次额外点击）：两类 + 5 个地址",
    nest.visible && !nest.bodyHidden && nest.groups.length === 2 && nest.addresses === 5,
    JSON.stringify(nest));
check("点地址子档 ⇒ 写入 API地址并切在线服务",
    /^https?:\/\//.test(nest.base) && nest.chan === "api", JSON.stringify(nest));
check("设置内预设嵌套：换快慢有 3 个子档，收起设置后预设区隐藏",
    nest.tunes.length === 3 && nest.hiddenAfter, JSON.stringify(nest));

// ---- 4. 缺项状态与点击 ----
const lack = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const W = (k) => n.widgets.find((w) => w.name === k);
    W("channel").value = "api"; W("channel").callback?.("api");
    W("api_base").value = ""; W("api_base").callback?.("");
    W("api_model").value = ""; W("api_model").callback?.("");
    await new Promise((r2) => setTimeout(r2, 1900));
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const w = g.querySelector(".mossllm-warn");
    const out = { text: w.textContent, hidden: w.hidden, act: w.dataset.act };
    w.click();
    await new Promise((r2) => setTimeout(r2, 1000));
    out.forceAfter = !!n.__mossllm.force;
    const row = g.querySelector('[data-widget-name="api_base"]');
    const inp = row && row.querySelector("input,textarea,select");
    out.focused = !!inp && document.activeElement === inp;
    out.flashed = !!row && row.classList.contains("mossllm-flash");
    return out;
}, ID);
check("缺端点时状态行说「还缺：API地址」，点它真的聚焦到 API地址输入框",
    /还缺：API地址/.test(lack.text) && !lack.hidden && lack.forceAfter
    && lack.focused && lack.flashed, JSON.stringify(lack));

// ---- 5. 存档契约 / 接线保护 ----
const saved = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const g = app.graph.serialize();
    const before = JSON.parse(JSON.stringify(g.nodes.find((x) => x.type === "MossPromptSmith").widgets_values));
    await app.loadGraphData(JSON.parse(JSON.stringify(g)));
    await new Promise((r2) => setTimeout(r2, 2200));
    const m = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const after = JSON.parse(JSON.stringify(m.widgets_values));
    return { before, after, stray: Object.keys(g.nodes.find((x) => x.type === "MossPromptSmith"))
        .filter((k) => /mossllm|hidden|focus/i.test(k)) };
}, ID);
check("存盘→重开 39 格逐位一致", saved.before.length === 39
    && JSON.stringify(saved.before) === JSON.stringify(saved.after), JSON.stringify(saved.stray));
check("面板状态不写进存档", saved.stray.length === 0, JSON.stringify(saved.stray));

// ---- 6. 窄节点 ----
const narrow = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    n.setSize([260, n.size[1]]);
    await new Promise((r2) => setTimeout(r2, 900));
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const bar = g.querySelector(".mossllm-bar");
    return { sw: bar.scrollWidth, cw: bar.clientWidth,
        btn: Math.round(g.querySelector(".mossllm-settings").getBoundingClientRect().right),
        barRight: Math.round(bar.getBoundingClientRect().right) };
}, ID);
check("260px 窄节点不横向溢出，设置按钮仍在节点内",
    narrow.sw <= narrow.cw + 2 && narrow.btn <= narrow.barRight + 2, JSON.stringify(narrow));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-3-local.png"), { y: 30 });

// ---- 7. 三梯度用户 ----
const tiers = await page.evaluate(async (id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    n.setSize([420, n.size[1]]);
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const W = (k) => n.widgets.find((w) => w.name === k);
    const wait = (ms) => new Promise((r2) => setTimeout(r2, ms));
    n.__mossllm.force = false;
    W("channel").value = "local"; W("channel").callback?.("local");
    W("api_base").value = ""; W("api_base").callback?.("");
    W("text").value = ""; W("text").callback?.("");
    await wait(1700);
    const rows = () => [...g.querySelectorAll("[data-widget-name]")]
        .filter((e) => e.dataset.widgetName !== "mosssb_llm_panel"
            && e.getBoundingClientRect().height > 0).map((e) => e.dataset.widgetName);
    const novice = { rows: rows().length, hint: g.querySelector(".mossllm-hint").textContent,
        warn: g.querySelector(".mossllm-warn").textContent,
        buttons: g.querySelectorAll(".mossllm-settings").length };
    // 中级：填一句话，状态行立刻反映
    W("text").value = "一只猫坐在窗边"; W("text").callback?.("一只猫坐在窗边");
    await wait(150);
    const mid = { hint: g.querySelector(".mossllm-hint").textContent };
    // 专家：设置里 39 项都在
    g.querySelector(".mossllm-settings").click();
    await wait(900);
    const expert = { rows: rows().length, cells: (n.serialize().widgets_values || []).length };
    g.querySelector(".mossllm-settings").click();
    await wait(500);
    return { novice, mid, expert };
}, ID);
check("新手：4 行 + 状态行说明 + 只有一个按钮",
    tiers.novice.rows === 4 && tiers.novice.buttons === 1
    && /→|接一张图/.test(tiers.novice.hint), JSON.stringify(tiers.novice));
check("中级：填文字后 150ms 内状态行反映（不等轮询）",
    /已填文字|写了要改什么/.test(tiers.mid.hint) && tiers.mid.hint !== tiers.novice.hint,
    JSON.stringify({ before: tiers.novice.hint, after: tiers.mid.hint }));
check("专家：设置里 ≥38 项、39 格存档不变",
    tiers.expert.rows >= 38 && tiers.expert.cells === 39, JSON.stringify(tiers.expert));

check("页面无 JS 异常", errs.length === 0, errs.join(" | "));
const code = c.done();
await browser.close();
process.exit(code);
