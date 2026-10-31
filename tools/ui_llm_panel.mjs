// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// LLM 参数面板的真机验收：只读界面、不排任何任务（要跑着的 ComfyUI，默认 127.0.0.1:8188）。
//
// 它盯的是这个面板**唯一可能碰到存档的地方**：宿主 `serialize()` 按 `serialize !== false` 收值，
// 面板只改 `widget.options.hidden` + 按可见内容调节点 `size` ⇒ `widgets_values` 的格数与位置
// 必须一个字节不变（存盘→重开逐位比）。截图落 `docs/_local/`（已 ignore）。
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

const visRows = (nodeId) => page.evaluate((id) => {
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    if (!g) return null;
    // 开关条自己也是一行（name = mosssb_llm_panel），不算参数
    const rows = [...g.querySelectorAll("[data-widget-name]")]
        .filter((e) => e.dataset.widgetName !== "mosssb_llm_panel");
    const shownRows = rows.filter((e) => e.getBoundingClientRect().height > 0);
    return { total: rows.length, shown: shownRows.length,
        names: shownRows.map((e) => e.dataset.widgetName),
        heads: rows.filter((e) => e.classList.contains("mossllm-head")).map((e) => e.dataset.mossllmTitle),
        overflow: g.scrollHeight > g.clientHeight + 2, h: [g.scrollHeight, g.clientHeight],
        nodeH: Math.round((app.graph.nodes.find((n) => String(n.id) === id) || { size: [0, 0] }).size[1]),
        boxH: Math.round(g.closest(".lg-node").getBoundingClientRect().height) };
}, nodeId);
const where = () => page.evaluate(() => (globalThis.MOSS_SB_LLM_PANEL ? globalThis.MOSS_SB_LLM_PANEL.where() : null));
const chip = (key) => page.evaluate((k) => {
    const b = document.querySelector('[data-mossllm-chip="' + k + '"]');
    return b ? { text: b.textContent, state: b.dataset.state, dirty: b.dataset.dirty, title: b.title } : null;
}, key);
const clickChip = async (key) => {
    await page.evaluate((k) => document.querySelector('[data-mossllm-chip="' + k + '"]').click(), key);
    await page.waitForTimeout(700);
};

await boot(page, URL_);
const wf = JSON.parse(readFileSync(join(ROOT, "examples", "llm-wash.json"), "utf8"));
check("载入 examples/llm-wash.json", (await loadGraph(page, wf)).ok);
await page.waitForTimeout(2500);
const loaded = await where();
check("面板已挂载（DOM 组件行）", loaded && loaded[0] && loaded[0].mounted, JSON.stringify(loaded));
check("参数控件仍是 38 个（一个不多一个不少）", loaded && loaded[0].widgets === 38, "widgets=" + (loaded && loaded[0].widgets));
const ID = await page.evaluate(() => app.graph.nodes.find((x) => x.type === "MossPromptSmith").id);
// 面板文案与核心 i18n 同源（节点标题/标签/说明会一起换）⇒ 先钉中文，后面断言的
// 「常用调优 / 本地引擎 / 通道外 / 已改」才有确定口径。宿主自己的 `<html lang>` 可能是 en。
await page.evaluate(() => globalThis.MOSS_SB_LANG && globalThis.MOSS_SB_LANG.set("zh"));
await page.waitForTimeout(900);
let r = await visRows(ID);
check("默认只露常驻 5 项", r && r.shown === 5, JSON.stringify(r));
check("默认没有分组标题（只有常驻，不需要标题）", r && r.heads.length === 0, JSON.stringify(r && r.heads));
check("默认内容没被节点盒子裁掉", r && !r.overflow, "scroll/client=" + JSON.stringify(r && r.h));
check("折叠后节点按内容变矮（不是留一片空白）", r && r.boxH < 620, "盒高=" + (r && r.boxH) + " 行=" + r.shown);
console.log("  默认参数网格高度 =", r && r.h[0], "px  节点盒高 =", r && r.boxH, "  size =", r && r.nodeH);
console.log("  状态行 =", JSON.stringify(await page.evaluate(() => document.querySelector(".mossllm-hint").textContent)));
console.log("  芯片 =", JSON.stringify(await Promise.all(["tune", "local", "api"].map(chip))));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-1-default.png"), { y: 30 });

// —— (a0) 开关条在最上方，且悬停不抖 ——
const layout0 = await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const g = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const bar = g.querySelector('[data-widget-name="mosssb_llm_panel"]');
    const task = g.querySelector('[data-widget-name="task"]');
    const box = (e) => e.getBoundingClientRect();
    return { first: n.widgets[0].name, barTop: Math.round(box(bar).top), barH: Math.round(box(bar).height),
        taskTop: Math.round(box(task).top) };
});
check("开关条在参数上方（不是沉底），且是 widgets[0]",
    layout0.first === "mosssb_llm_panel" && layout0.barTop < layout0.taskTop, JSON.stringify(layout0));

await page.evaluate(() => {
    const row = document.querySelector('[data-widget-name="channel"]');
    row.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
});
await page.waitForTimeout(900);
const jitter = await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const g = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const box = (e) => e.getBoundingClientRect();
    return { barH: Math.round(box(g.querySelector('[data-widget-name="mosssb_llm_panel"]')).height),
        taskTop: Math.round(box(g.querySelector('[data-widget-name="task"]')).top),
        hintLen: document.querySelector(".mossllm-hint").textContent.length };
});
check("悬停只换说明文字：开关条高度与下面参数的 top 都不动（不抖）",
    Math.abs(jitter.barH - layout0.barH) <= 1 && jitter.taskTop === layout0.taskTop,
    JSON.stringify({ layout0, jitter }));

// —— (a) 单组视图 ——
await clickChip("tune");
r = await visRows(ID);
check("点「常用调优」⇒ 5 + 11 = 16 行（seed 的控件不单独占行），标题只出现一次",
    r.shown === 16 && r.heads.length === 1 && r.heads[0] === "常用调优", JSON.stringify(r));
// 2026-10-30 点名的那条：旧版开关条在底部，点开一组它就往下跑；现在必须原地不动。
const layout1 = await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const g = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const box = (e) => e.getBoundingClientRect();
    return { first: n.widgets[0].name,
        barTop: Math.round(box(g.querySelector('[data-widget-name="mosssb_llm_panel"]')).top),
        barH: Math.round(box(g.querySelector('[data-widget-name="mosssb_llm_panel"]')).height),
        taskTop: Math.round(box(g.querySelector('[data-widget-name="task"]')).top) };
});
check("点开一组后开关条顶端不动、下面的行只让位 ≤4px（宿主会把剩余高度摊进行里）",
    layout1.first === "mosssb_llm_panel" && Math.abs(layout1.barTop - layout0.barTop) <= 1
    && Math.abs(layout1.taskTop - layout0.taskTop) <= 4,
    JSON.stringify({ layout0, layout1 }));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-2-tune.png"), { y: 30 });

// 「已改」小点：改一项 → 折起来 → chip 应亮，且条数 +1
const dirty0 = await page.evaluate(() => globalThis.MOSS_SB_LLM_PANEL.where()[0].dirty);
await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    n.widgets.find((x) => x.name === "temperature").value = 0.55;
    n.widgets.find((x) => x.name === "temperature").callback?.(0.55);
});
await clickChip("tune");                                   // 收起
const ch = await chip("tune");
const before0 = Number(String(dirty0).match(/tune:(\d+)/)?.[1] ?? -1);
const after0 = Number(String(ch && ch.title).match(/(\d+) 项/)?.[1] ?? -2);
check("折起的组里有改动 ⇒ chip 亮「已改」点", ch && ch.dirty === "1", JSON.stringify(ch));
check("「已改」提示带条数，且比载入时多 1（不是永远亮着）", after0 === before0 + 1,
    dirty0 + " → " + (ch && ch.title));

await clickChip("local");
r = await visRows(ID);
check("点「本地引擎」⇒ 5 + 17 = 22 行，标题只出现一次",
    r.shown === 22 && r.heads.length === 1 && r.heads[0] === "本地引擎 · llama.cpp", JSON.stringify(r));
let cl = await chip("local");
check("当前通道是本地 ⇒ 本地 chip 不标「通道外」", cl && !/通道外/.test(cl.text), JSON.stringify(cl));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-3-local.png"), { y: 30 });

await clickChip("api");
r = await visRows(ID);
check("点「外部 API」⇒ 5 + 4 = 9 行，标题只出现一次",
    r.shown === 9 && r.heads.length === 1 && r.heads[0] === "外部 API", JSON.stringify(r));
let ca = await chip("api");
check("通道不在外部 ⇒ api chip 标「通道外」", ca && /通道外/.test(ca.text), JSON.stringify(ca));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-4-api.png"), { y: 30 });

// —— (b) 换通道 ⇒ 面板自动切到那一组 ——
await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const w = n.widgets.find((x) => x.name === "channel");
    w.value = "api"; w.callback("api");
});
await page.waitForTimeout(700);
let after = await where();
check("切到外部 API ⇒ 焦点自动落在 api 组", after && after[0].focus === "api", JSON.stringify(after));
cl = await chip("local");
check("本地 chip 变成「通道外」且不展开", cl && /通道外/.test(cl.text) && cl.state === "closed", JSON.stringify(cl));
await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const w = n.widgets.find((x) => x.name === "channel");
    w.value = "local"; w.callback("local");
});
await page.waitForTimeout(700);
after = await where();
check("切回本地 ⇒ 焦点自动回到 local 组", after && after[0].focus === "local", JSON.stringify(after));

// —— (c) 一键全开：38 个参数一个不少 ——
await page.evaluate(() => document.querySelector(".mossllm-btn").click());
await page.waitForTimeout(800);
r = await visRows(ID);
const all = await where();
const names = await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const ws = n.widgets.filter((w) => w.name !== "mosssb_llm_panel");
    return { total: ws.length, hidden: ws.filter((w) => w.options && w.options.hidden).length,
        invisible: ws.filter((w) => typeof n.isWidgetVisible === "function" && !n.isWidgetVisible(w)).map((w) => w.name) };
});
check("全展开后 38 个参数一个不少（逐个核 isWidgetVisible）",
    names.total === 38 && names.hidden === 0 && names.invisible.length === 0,
    "widgets=" + names.total + " hidden=" + names.hidden + " 不可见=" + JSON.stringify(names.invisible));
const bad = await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    return n.widgets.filter((w) => {
        const v = w.value;
        if (w.options && Array.isArray(w.options.values)) return !w.options.values.some((x) => x === v || String(x) === String(v));
        if (w.type === "number" || w.type === "slider") return typeof v !== "number";
        if (w.type === "toggle") return typeof v !== "boolean";
        return !(typeof v === "string" || v == null);
    }).map((w) => w.name + "=" + JSON.stringify(w.value));
});
check("全展开时值域不红（每个值都合法）", bad.length === 0, JSON.stringify(bad));
console.log("  全展开标题 =", JSON.stringify(r.heads), " 网格高度 =", r.h[0], "px");
check("全展开时每组标题按段画（顺序是存档契约：本地引擎出现 3 次是已知代价）",
    r.heads.length === 6 && r.heads.filter((x) => x === "本地引擎 · llama.cpp").length === 3,
    JSON.stringify(r.heads));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-5-expanded.png"), { y: 30 });
await page.evaluate(() => document.querySelector(".mossllm-btn").click());   // 回到分组
await page.waitForTimeout(700);
after = await where();
check("「回到分组」按回聚焦组（local），不是全收起",
    after && after[0].force === false && after[0].focus === "local", JSON.stringify(after));

// —— (d) 存盘 → 重开：widgets_values 逐位一致 ——
const save = await page.evaluate(async () => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    n.widgets.find((x) => x.name === "temperature").value = 0.55;      // 改一个值，验证它回得来
    n.widgets.find((x) => x.name === "must_keep").value = "路上小心";
    const g = app.graph.serialize();
    const sn = g.nodes.find((x) => x.type === "MossPromptSmith");
    const orig = JSON.parse(JSON.stringify(sn.widgets_values));
    const stray = Object.keys(sn).filter((k) => /mossllm|hidden|focus/i.test(k));
    await app.loadGraphData(JSON.parse(JSON.stringify(g)));
    await new Promise((r2) => setTimeout(r2, 2500));
    const m = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    return { orig, after: JSON.parse(JSON.stringify(m.widgets_values)),
        stray, widgets: m.widgets.length, mounted: !!(m.__mossllmBar && m.__mossllmBar.nodeType),
        hidden: m.widgets.filter((x) => x.options && x.options.hidden).length,
        focus: m.__mossllm ? m.__mossllm.focus : "NO_STATE",
        temp: m.widgets.find((x) => x.name === "temperature").value,
        keep: m.widgets.find((x) => x.name === "must_keep").value };
});
const diff = [];
for (let i = 0; i < Math.max(save.orig.length, save.after.length); i += 1) {
    if (JSON.stringify(save.orig[i]) !== JSON.stringify(save.after[i])) diff.push([i, save.orig[i], save.after[i]]);
}
check("存档里 widgets_values 长度 38（DOM 组件那格被剔掉）", save.orig.length === 38, "len=" + save.orig.length);
check("重开后 widgets_values 逐位一致", diff.length === 0, JSON.stringify(diff.slice(0, 3)));
check("折叠/聚焦状态没写进存档（无 mossllm/hidden/focus 键）", save.stray.length === 0, JSON.stringify(save.stray));
check("改过的值重开还在（0.55 / 路上小心）", save.temp === 0.55 && save.keep === "路上小心", save.temp + "/" + save.keep);
check("重后面板自动挂回、回到默认（常驻 5 行 / 33 项隐藏 / 无聚焦组）",
    save.mounted && save.hidden === 33 && save.focus === null, "mounted=" + save.mounted
    + " hidden=" + save.hidden + " focus=" + JSON.stringify(save.focus));
r = await visRows(await page.evaluate(() => app.graph.nodes.find((x) => x.type === "MossPromptSmith").id));
check("重开后界面上仍是 5 行", r && r.shown === 5, JSON.stringify(r));
const topAfter = await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    return { first: n.widgets[0] && n.widgets[0].name };
});
check("重开后开关条仍自愈到 widgets[0]（宿主按旧顺序重建 widget）",
    topAfter.first === "mosssb_llm_panel", JSON.stringify(topAfter));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-6-reloaded.png"), { y: 30 });

// —— (e) 说明行：悬停取后端那份 tooltip，离开回到摘要 ——
await page.evaluate(() => {
    const row = document.querySelector('[data-widgets-grid-node-id="'
        + app.graph.nodes.find((x) => x.type === "MossPromptSmith").id + '"] [data-widget-name="channel"]');
    row.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
});
await page.waitForTimeout(900);
const hintText = await page.evaluate(() => document.querySelector(".mossllm-hint").textContent);
check("说明行取到后端那份 tooltip（≥20 字）", hintText.length >= 20, hintText.slice(0, 40) + "…");
await page.evaluate(() => document.body.dispatchEvent(new PointerEvent("pointerover", { bubbles: true })));
await page.waitForTimeout(300);
const hintBack = await page.evaluate(() => document.querySelector(".mossllm-hint").textContent);
check("指针离开参数区 ⇒ 说明行回到摘要（不留最后一条 tooltip）",
    /个参数/.test(hintBack) && hintBack !== hintText, hintBack.slice(0, 40));

// —— (f) 不伤及别人：两个同型节点各自独立 ——
const two = await page.evaluate(async () => {
    const n = LiteGraph.createNode("MossPromptSmith");
    app.graph.add(n); n.pos = [820, 60];
    await new Promise((r) => setTimeout(r, 2200));
    const all = app.graph.nodes.filter((x) => x.type === "MossPromptSmith");
    return all.map((m) => ({ id: m.id, mounted: !!(m.__mossllmBar && m.__mossllmBar.nodeType),
        widgets: m.widgets.length, values: (m.serialize().widgets_values || []).length,
        hidden: m.widgets.filter((w) => w.options && w.options.hidden).length }));
});
check("两个同型节点各自挂载、各自 38 格存档、各自 33 项隐藏", two.length === 2
    && two.every((t) => t.mounted && t.widgets === 39 && t.values === 38 && t.hidden === 33), JSON.stringify(two));

// 接了线的参数不许藏（把 ctx_size 标成已连线）
const linked = await page.evaluate(async () => {
    const m = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const slot = (m.inputs || []).find((i) => i.name === "ctx_size");
    if (!slot) return { noSlot: true };
    const cw = m.widgets.find((x) => x.name === "channel");
    const w = m.widgets.find((x) => x.name === "ctx_size");
    slot.link = 999001;
    const seen = (m.inputs || []).filter((i) => i.name === "ctx_size").map((i) => i.link);
    m.__mossllm.force = false;
    cw.callback(String(cw.value));                       // 同步触发一次重算
    const out = { seen, hidden: !!(w.options && w.options.hidden) };
    slot.link = null;
    cw.callback(String(cw.value));
    out.restored = !!(w.options && w.options.hidden);
    return out;
});
c.skip("接了线的参数不会被藏", "造不出真机条件：直接写 slot.link 被宿主立刻归零（seen=" + JSON.stringify(linked.seen) + "）；"
    + "宿主自己会把已提升的 widget 行按 byConnection 抑制掉，代码里的 isLinked 只是防一手");

check("只作用于 MossPromptSmith", await page.evaluate(() => app.graph.nodes.filter((n) => n.type !== "MossPromptSmith")
    .every((n) => !n.__mossllmBar && !(n.widgets || []).some((w) => w && w.name === "mosssb_llm_panel"))));
check("页面无 JS 异常", errs.length === 0, errs.join(" | "));
const code = c.done();
await browser.close();
process.exit(code);
