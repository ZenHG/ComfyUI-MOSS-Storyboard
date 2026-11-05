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

/**
 * 有界轮询：条件成立就往下走，等不到返回 null。
 *
 * 为什么需要它：面板的挂载靠 `setInterval(guard, 1500)` 那一拍，而"某一行 / 挂在行上的
 * 行内控件"要等宿主下一次重绘才出现。固定 `wait(900)` 会随机落在两次轮询之间 ——
 * 同一份字节跑三次能出 78/3 与 81/0 两种结果（审校实测）。调大等待只是换个抽签概率，
 * 所以改成「每 100ms 探一次，最多 4000ms」。等不到**不跳过**：调用方照旧读状态、照旧断言，
 * 该红还是红。
 */
const pollUntil = async (probe, timeoutMs = 4000, stepMs = 100, pg = page) => {
    const t0 = Date.now();
    for (;;) {
        const v = await probe();
        if (v) return v;
        if (Date.now() - t0 >= timeoutMs) return null;
        await pg.waitForTimeout(stepMs);
    }
};

await boot(page, URL_);
const wf = JSON.parse(readFileSync(join(ROOT, "examples", "llm-prompt-workbench.json"), "utf8"));
check("载入 examples/llm-prompt-workbench.json", (await loadGraph(page, wf)).ok);
// 原为固定 `waitForTimeout(2500)`（等面板挂上）。改成有界轮询：等「四个节点都 mounted
// 且网格里至少有 5 行是真渲染出来的」，最多 4000ms；等不到就往下走，断言照旧判红。
const ready = await pollUntil(() => page.evaluate(() => {
    const api = globalThis.MOSS_SB_LLM_PANEL;
    if (!api) return 0;
    const rows = api.where();
    if (!rows.length || !rows.every((r) => r.mounted)) return 0;
    // 按节点逐个判：每个已挂载节点**自己的网格**里都要有 ≥5 行真渲染的行。
    // 早先写成「全页所有网格合计 ≥5」，14 个网格里任一节点画完 5 行就放行，
    // 实际只有 4 个该类节点 ⇒ 把这 2.48s 的整体等待提前放掉了（审校实测 3ms 就放行）。
    const ok = rows.every((r) => {
        const g = document.querySelector('[data-widgets-grid-node-id="' + r.id + '"]');
        if (!g) return false;
        return [...g.querySelectorAll("[data-widget-name]")]
            .filter((e) => e.dataset.widgetName !== "mosssb_llm_panel"
                && e.getBoundingClientRect().height > 0).length >= 5;
    });
    return ok ? rows.length : 0;
}));
console.log("  面板挂载轮询 =", ready ? (ready + " 个节点就绪") : "4000ms 内未就绪（下面照旧判红）");
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
check("点「常用调优」⇒ 5 + 12 = 17 行（seed 的控件不单独占行；target 只在 assemble 时有用，也归这组）",
    r.shown === 17 && r.heads.length === 1 && r.heads[0] === "常用调优", JSON.stringify(r));
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
// 预设按钮让开关条高了 21px（草稿/精修/默认那排）。核心不变量是「顶端不动」，
// 再加上因果：下面的行是被推下去的（taskTop 增量 == barH 增量），不是被叠住。
// ⚠️ 这条只允许更严，不许删或改恒真 —— 见任务书 T00。
const dBarH = layout1.barH - layout0.barH;
const dTaskTop = layout1.taskTop - layout0.taskTop;
check("点开一组后开关条顶端不动；增高 ≤24px；下面各行是被推下去的（taskTop 增量 == barH 增量）",
    layout1.first === "mosssb_llm_panel" && Math.abs(layout1.barTop - layout0.barTop) <= 1
    && dBarH >= 0 && dBarH <= 24
    && Math.abs(dTaskTop - dBarH) <= 1,
    JSON.stringify({ layout0, layout1, dBarH, dTaskTop }));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-2-tune.png"), { y: 30 });

// —— (a1) 一次性预设（草稿 / 恢复默认 / 显隐）——
const presetState = () => page.evaluate((id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const val = (k) => { const w = n.widgets.find((x) => x.name === k); return w ? String(w.value) : null; };
    const wrap = document.querySelector(".mossllm-presets");
    // 说明行的「摘要」才是 st.note 的落点；指针悬停时 .mossllm-hint 会被 tooltip 顶替，
    // 所以这里断言 __mossllm.summary（用户不悬停时看到的那一行），不去赌 DOM 时序。
    const summary = (n.__mossllm && n.__mossllm.summary) || "";
    return { length: val("length"), temperature: val("temperature"), max_tokens: val("max_tokens"),
        gpu_layers: val("gpu_layers"), hidden: wrap ? wrap.hidden : null, summary };
}, ID);
const clickPreset = async (key) => {
    await page.evaluate((k) => { const b = document.querySelector('[data-moss-preset="' + k + '"]'); if (b) b.click(); }, key);
    await page.waitForTimeout(600);
};
check("「常用调优」展开时预设按钮才出现（不常驻占位）", (await presetState()).hidden === false);
await clickPreset("draft");
const pDraft = await presetState();
check("点「草稿」⇒ length=brief / temperature=0.9 / max_tokens=512，说明行是整句「已套用「草稿（快）」，改了 N 项」",
    pDraft.length === "brief" && pDraft.temperature === "0.9" && pDraft.max_tokens === "512"
    && /已套用「草稿（快）」，改了 \d+ 项/.test(pDraft.summary), JSON.stringify(pDraft));
// 对照组：先给「本地引擎」组的 gpu_layers 塞一个非默认值，验证恢复默认不越组。
// ⚠️ 不许用 temperature 当判据：默认值与「精修」都是 0.2，恢复前后没差别。
const gpuSet = await page.evaluate((id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    const w = n.widgets.find((x) => x.name === "gpu_layers");
    if (!w) return null;
    w.value = "7"; w.callback?.("7");   // gpu_layers 是 STRING 控件，别塞数字
    app?.graph?.setDirtyCanvas?.(true, true);
    return String(w.value);
}, ID);
await page.waitForTimeout(400);
const pBeforeReset = await presetState();
await clickPreset("reset");
const pReset = await presetState();
check("点「恢复默认」⇒ length=standard / max_tokens=1024，且非本组的 gpu_layers 不被改动",
    pReset.length === "standard" && pReset.max_tokens === "1024"
    && pReset.gpu_layers === pBeforeReset.gpu_layers,
    JSON.stringify({ gpuSet, pBeforeReset, pReset }));
await clickChip("tune");
check("「常用调优」收起时预设按钮隐藏（不占地方）", (await presetState()).hidden === true);
await clickChip("tune");

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
await page.waitForTimeout(900);
const pinned = await page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const grid = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const row = grid && grid.querySelector('[data-widget-name="server_exe"]');
    const rt = grid && grid.querySelector('[data-widget-name="runtime"]');
    const bar = grid && grid.querySelector('[data-widget-name="mosssb_llm_panel"]');
    return row && rt && bar ? {
        serverTop: Math.round(row.getBoundingClientRect().top),
        runtimeTop: Math.round(rt.getBoundingClientRect().top),
        barTop: Math.round(bar.getBoundingClientRect().top),
    } : {};
});
check("服务器程序行置顶（在开关条下方、runtime 上方，不改存档）",
    pinned.barTop < pinned.serverTop && pinned.serverTop < pinned.runtimeTop, JSON.stringify(pinned));

// 尺寸回归：展开后不得出现大面积空白或虚高。
const probeFit = () => page.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const g = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const rs = [...g.querySelectorAll("[data-widget-name]")]
        .map((e) => e.getBoundingClientRect())
        .filter((r) => r.height > 0)
        .sort((a, b) => a.top - b.top);
    const hs = rs.map((r) => r.height).sort((a, b) => a - b);
    const med = hs[Math.floor(hs.length / 2)] || 1;
    let maxGap = 0;
    for (let i = 1; i < rs.length; i += 1) {
        maxGap = Math.max(maxGap, Math.round(rs[i].top - rs[i - 1].bottom));
    }
    return { rows: rs.length, med: Math.round(med), maxGap,
        boxH: Math.round(g.closest(".lg-node").getBoundingClientRect().height),
        sizeH: Math.round(n.size[1]), spill: g.scrollHeight - g.clientHeight };
});
const fit = await probeFit();
check("展开分组后行间无大片空白", fit.maxGap < fit.med * 2, JSON.stringify(fit));
check("节点高度与可见内容相称（不是 order 算出的虚高）",
    fit.sizeH < 1200 && fit.spill <= fit.med, JSON.stringify(fit));
await clickChip("local");
await clickChip("local");
const fit2 = await probeFit();
check("收起再展开不复发",
    fit2.maxGap < fit2.med * 2 && fit2.sizeH < 1200 && fit2.spill <= fit2.med, JSON.stringify(fit2));
await shotNode(page, "MossPromptSmith", join(ROOT, "docs", "_local", "panel-3-local.png"), { y: 30 });

await clickChip("api");
r = await visRows(ID);
// 4 行：api_base / api_model / api_key，外加界面上不提供的 api_key_env（不计可见）。
check("点「外部 API」⇒ 5 + 3 = 8 行，标题只出现一次",
    r.shown === 8 && r.heads.length === 1 && r.heads[0] === "外部 API", JSON.stringify(r));
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

// —— (c) 一键全开：39 个参数一个不少 ——
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
// `target` 归 tune 组，但为了不挪动既有 widgets_values 的位置，它只能加在**最后一个**
// 控件位。全展开时它就在「本地引擎」「外部 API」之后，于是末尾多出一段「常用调优」——
// 7 段 / 本地引擎 3 次是存档契约换来的，不是画错。
check("全展开时每组标题按段画（顺序是存档契约：7 段、本地引擎 3 次、末尾回到常用调优）",
    r.heads.length === 7 && r.heads.filter((x) => x === "本地引擎 · llama.cpp").length === 3
    && r.heads[r.heads.length - 1] === "常用调优",
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
    const before = app.graph.nodes.filter((x) => x.type === "MossPromptSmith").length;
    const n = LiteGraph.createNode("MossPromptSmith");
    app.graph.add(n); n.pos = [820, 60];
    await new Promise((r) => setTimeout(r, 2200));
    const all = app.graph.nodes.filter((x) => x.type === "MossPromptSmith");
    const fresh = all.find((m) => m.id === n.id);
    return { before, after: all.length, fresh: fresh && {
        mounted: !!(fresh.__mossllmBar && fresh.__mossllmBar.nodeType),
        widgets: fresh.widgets.length, values: (fresh.serialize().widgets_values || []).length,
        hidden: fresh.widgets.filter((w) => w.options && w.options.hidden).length } };
});
check("新加的同型节点独立挂载、38 格存档（不干扰已有节点）",
    two.after === two.before + 1 && two.fresh && two.fresh.mounted && two.fresh.widgets === 39
    && two.fresh.values === 38 && two.fresh.hidden === 33, JSON.stringify(two));

// 接了线的参数不许藏：**造真机条件**——PrimitiveNode 连一根真链到 ctx_size 的输入槽。
//
// 以前 SKIP 的原因不是「宿主不允许接线」，是造法不对：直接写 `slot.link = 999001`
// 不是图上的链接对象，宿主下一帧就把 link 归零（实测 seen=[null]）。
// `LiteGraph.createNode("PrimitiveNode")` + `primitive.connect(0, node, inputIndex)`
// 走宿主自己的连接路径：链接进 `graph.links`，也会随 `serialize()`/`loadGraphData()` 往返。
//
// 三种「藏」要分开（实测口径）：
//   ① 面板旗标 `widget.options.hidden` —— 本仓面板负责：接了线必须保持 false；
//   ② 宿主按连接抑制控件 `widget.connectionSuppressed` —— 宿主行为，面板改不了；
//      断言它 true 只是把预期写清楚，不算面板绿；
//   ③ 存档 `widgets_values` —— 接线/折叠都不许动它，逐位比。
const linkProof = await page.evaluate(async () => {
    const m = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const slot = (m.inputs || []).find((i) => i.name === "ctx_size");
    if (!slot) return { noSlot: true, inputs: (m.inputs || []).map((i) => i.name) };
    const ctxW = () => m.widgets.find((x) => x.name === "ctx_size");
    const ubW = () => m.widgets.find((x) => x.name === "ubatch");
    const snap = () => ({ hidden: !!((ctxW().options || {}).hidden),
        ubatchHidden: !!((ubW().options || {}).hidden),
        suppressed: !!ctxW().connectionSuppressed, slotLink: slot.link });
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const click = (key) => {
        const b = document.querySelector('[data-mossllm-chip="' + key + '"]');
        if (b) b.click();
        return !!b;
    };
    // 先回到默认视图（只露常驻）：此时 ctx_size 与 ubatch 本来都该被藏
    globalThis.MOSS_SB_LLM_PANEL.focus(null);
    m.__mossllm.force = false;
    await wait(500);
    const baseline = snap();
    const valuesBefore = JSON.parse(JSON.stringify(m.widgets_values));
    const linksBefore = Object.keys(app.graph.links || {}).length;
    const p = LiteGraph.createNode("PrimitiveNode");
    app.graph.add(p);
    p.pos = [1150, 80];
    await wait(300);
    const link = p.connect(0, m, m.inputs.indexOf(slot));       // 真链：宿主连接路径
    await wait(600);
    const connected = snap();                                    // 还没通知面板，旗标可能仍是旧值
    click("tune");                                              // ctx_size 属「本地引擎」组，tune 视图里本应被藏
    await wait(700);
    const atTune = snap();
    click("api");                                               // 换到「外部 API」，本地组整体本应被藏
    await wait(700);
    const atApi = snap();
    click("api");                                               // 再点一次 = 收起，回默认视图
    await wait(700);
    const folded = snap();
    return { noSlot: false, linkOk: !!link, linkId: slot.link, linksBefore,
        linksAfter: Object.keys(app.graph.links || {}).length,
        baseline, connected, atTune, atApi, folded,
        valuesBefore, valuesAfter: JSON.parse(JSON.stringify(m.widgets_values)),
        primitive: { id: p.id, widgets: (p.widgets || []).map((x) => x.name) } };
});
check("造出真机条件：PrimitiveNode 连到 ctx_size 的真链（走宿主连接路径，不是手写 slot.link）",
    !linkProof.noSlot && linkProof.linkOk && linkProof.linkId != null
    && linkProof.linksAfter === linkProof.linksBefore + 1,
    JSON.stringify({ slot: linkProof.linkId, links: [linkProof.linksBefore, linkProof.linksAfter] }));
check("对照组成立：接线前默认视图里 ctx_size 与 ubatch 都被面板隐藏（条件非空转）",
    linkProof.baseline && linkProof.baseline.hidden === true
    && linkProof.baseline.ubatchHidden === true, JSON.stringify(linkProof.baseline));
check("接线后切到「常用调优」：ctx_size 不被藏（hidden=false），未接线的 ubatch 照常藏",
    linkProof.atTune && linkProof.atTune.hidden === false
    && linkProof.atTune.ubatchHidden === true && linkProof.atTune.suppressed === true,
    JSON.stringify(linkProof.atTune));
check("切到「外部 API」再折回默认：已接线参数仍然不藏（每次重算都尊重 isLinked）",
    linkProof.atApi && linkProof.folded
    && linkProof.atApi.hidden === false && linkProof.folded.hidden === false
    && linkProof.folded.ubatchHidden === true,
    JSON.stringify({ api: linkProof.atApi, folded: linkProof.folded }));
console.log("  真链证据 =", JSON.stringify({ slot: linkProof.linkId, baseline: linkProof.baseline,
    connected: linkProof.connected, atTune: linkProof.atTune, atApi: linkProof.atApi,
    folded: linkProof.folded }));

// —— 真链还得过「存盘 → 重开」：链接恢复、旗标不复发、widgets_values 一位不动 ——
const reloadLinked = await page.evaluate(async () => {
    const m = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const orig = JSON.parse(JSON.stringify(m.widgets_values));
    const g = app.graph.serialize();
    await app.loadGraphData(JSON.parse(JSON.stringify(g)));
    await new Promise((r) => setTimeout(r, 2800));
    const m2 = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const slot2 = m2 && (m2.inputs || []).find((i) => i.name === "ctx_size");
    const w2 = m2 && m2.widgets.find((x) => x.name === "ctx_size");
    const u2 = m2 && m2.widgets.find((x) => x.name === "ubatch");
    const after = m2 ? JSON.parse(JSON.stringify(m2.widgets_values)) : [];
    const diff = [];
    for (let i = 0; i < Math.max(orig.length, after.length); i += 1) {
        if (JSON.stringify(orig[i]) !== JSON.stringify(after[i])) diff.push([i, orig[i], after[i]]);
    }
    return { slotLink: slot2 ? slot2.link : null,
        hidden: w2 ? !!((w2.options || {}).hidden) : null,
        suppressed: w2 ? !!w2.connectionSuppressed : null,
        ubatchHidden: u2 ? !!((u2.options || {}).hidden) : null,
        len: after.length, diff: diff.slice(0, 3),
        mounted: !!(m2 && m2.__mossllmBar && m2.__mossllmBar.nodeType) };
});
check("重开工作流后真链还在（ctx_size 输入槽 slot.link 非空）",
    reloadLinked.slotLink != null, "slot.link=" + reloadLinked.slotLink);
check("重开后 widgets_values 逐位一致（38 格；接线与折叠都不写存档）",
    reloadLinked.len === 38 && reloadLinked.diff.length === 0,
    "len=" + reloadLinked.len + " diff=" + JSON.stringify(reloadLinked.diff));
check("重开后已接线参数仍不被面板藏（hidden=false；宿主 byConnection 抑制控件本身是宿主行为，如实记录）",
    reloadLinked.hidden === false && reloadLinked.suppressed === true
    && reloadLinked.ubatchHidden === true && reloadLinked.mounted,
    JSON.stringify(reloadLinked));
console.log("  真链重开证据 =", JSON.stringify(reloadLinked));

// —— (h) T14：按 task 动态常驻 / inert 标注 / 冷载入 / 焦点保护 ——
const t14 = await page.evaluate(async () => {
    // (g) 给第一个节点接了真链；链接行按 INV-6 恒可见，会多出一行。挑干净节点测常驻集。
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith"
        && !(x.inputs || []).some((i) => i && i.link != null));
    const W = (name) => n.widgets.find((x) => x.name === name);
    const grid = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const box = grid.closest(".lg-node");
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const shown = () => [...grid.querySelectorAll("[data-widget-name]")]
        .filter((e) => e.dataset.widgetName !== "mosssb_llm_panel")
        .filter((e) => e.getBoundingClientRect().height > 0)
        .map((e) => e.dataset.widgetName);
    const inert = () => [...grid.querySelectorAll(".mossllm-inert")]
        .map((e) => e.dataset.widgetName);
    const hint = () => box.querySelector(".mossllm-hint").textContent;
    const clearHover = () => document.body.dispatchEvent(
        new PointerEvent("pointerover", { bubbles: true }));

    W("task").value = "assemble";
    globalThis.MOSS_SB_LLM_PANEL.focus(null);
    await wait(1900);
    clearHover();
    await wait(100);
    const chanRow = grid.querySelector('[data-widget-name="channel"]');
    const c = { names: shown(), inert: inert(), hint: hint(), title: chanRow ? chanRow.title : "" };

    W("task").value = "caption";
    globalThis.MOSS_SB_LLM_PANEL.focus(null);
    await wait(1900);
    clearHover();
    await wait(100);
    const cap = { names: shown(), inert: inert(), hint: hint() };
    return { c, cap };
});
check("assemble ⇒ target 提为常驻（6 行），不再折叠在 tune 组",
    t14.c.names.length === 6 && t14.c.names.includes("target"), JSON.stringify(t14.c.names));
check("assemble ⇒ channel/dialect/model 标 inert（不隐藏，带文字）",
    ["channel", "dialect", "model"].every((x) => t14.c.inert.includes(x))
    && /不生效|No effect/.test(t14.c.title),
    JSON.stringify(t14.c.inert) + " title=" + t14.c.title);
check("assemble ⇒ 说明行点名被绕过的参数",
    /拼装模式|Assemble:/.test(t14.c.hint), t14.c.hint.slice(0, 70));
check("切回 caption ⇒ 恢复 5 行、inert 清除、出现零模型自检引导",
    t14.cap.names.length === 5 && t14.cap.inert.length === 0
    && /零模型自检|No-weight check/.test(t14.cap.hint), JSON.stringify(t14.cap));

const cold = await page.evaluate(async () => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    n.widgets.find((x) => x.name === "channel").value = "api";
    const g = app.graph.serialize();
    await app.loadGraphData(JSON.parse(JSON.stringify(g)));
    await new Promise((r) => setTimeout(r, 2200));
    const m = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    return { focus: m.__mossllm ? m.__mossllm.focus : "NO_STATE",
        apiVisible: !m.widgets.find((x) => x.name === "api_model").options.hidden };
});
check("冷载入已存的 api 工作流 ⇒ 自动聚焦 api 组、api_model 可见",
    cold.focus === "api" && cold.apiVisible, JSON.stringify(cold));

const fk = await page.evaluate(async () => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const grid = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const box = grid.closest(".lg-node");
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const chipTune = box.querySelector('[data-mossllm-chip="tune"]');
    chipTune.click();
    await wait(1900);
    const row = grid.querySelector('[data-widget-name="temperature"]');
    const input = row && row.querySelector("input,select,textarea");
    if (input) input.focus();
    const before = !!input && document.activeElement === input;
    const chipLocal = box.querySelector('[data-mossllm-chip="local"]');
    chipLocal.click();
    await wait(1900);
    return { before, after: document.activeElement === chipLocal,
        active: document.activeElement && document.activeElement.dataset.mossllmChip };
});
check("切组时焦点落在被隐藏行 ⇒ 移到目标 chip（不丢焦点）",
    fk.before && fk.after, JSON.stringify(fk));

check("只作用于 MossPromptSmith", await page.evaluate(() => app.graph.nodes.filter((n) => n.type !== "MossPromptSmith")
    .every((n) => !n.__mossllmBar && !(n.widgets || []).some((w) => w && w.name === "mosssb_llm_panel"))));

// —— 拉取模型：真 combo、有可见进度、拉完自己展开、换端点不留旧值 ——
const combo = await page.evaluate(async () => {
    const n = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const W = (name) => n.widgets.find((x) => x.name === name);
    const cw = W("channel");
    cw.value = "api";
    if (cw.callback) cw.callback("api");
    W("api_base").value = "https://api.example.com/v1";
    const grid = () => document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const row = () => {
        const g = grid();
        return g ? g.querySelector('[data-widget-name="api_model"]') : null;
    };
    // 行内的 ↻ 是面板 apply 时贴到 api_model 行上的，而那次 apply 可能来自
    // setInterval(guard, 1500)：固定等 900ms 会随机落在两次轮询之间（与「常用端点下拉」
    // 是同一个竞态）。改成每 100ms 探一次、最多 4000ms；等不到 pick=false，下面照旧判红。
    const t1 = Date.now();
    while (Date.now() - t1 < 4000) {
        const r0 = row();
        if (r0 && r0.querySelector(".mossllm-pick")) break;
        await new Promise((r) => setTimeout(r, 100));
    }
    const pick = row() && row().querySelector(".mossllm-pick");
    const w = W("api_model");
    const typeIsCombo = !!w && Array.isArray(w.options && w.options.values);
    if (!pick) return { typeIsCombo, pick: false };

    // 桩故意慢 900ms：好观察「拉取中」有没有可见进度（真网络 2s 起）。
    window.fetch = async () => {
        await new Promise((r) => setTimeout(r, 900));
        return { ok: true, status: 200,
            json: async () => ({ ok: true, url: "https://api.example.com/v1/models",
                                 models: ["model-a", "model-b"] }) };
    };
    pick.querySelector("button").click();
    await new Promise((r) => setTimeout(r, 350));
    const busyAttr = row().querySelector(".mossllm-pick button").dataset.busy || "";
    const busyHint = (grid().querySelector(".mossllm-hint") || {}).textContent || "";
    await new Promise((r) => setTimeout(r, 1600));

    const values = [...(W("api_model").options.values || [])];
    // 不手动点下拉：拉完应当自己展开，用户直接就能选。
    const autoOpts = [...document.querySelectorAll('[role="option"]')]
        .map((e) => e.textContent.trim()).filter(Boolean);
    const value = W("api_model").value;
    const okHint = (grid().querySelector(".mossllm-hint") || {}).textContent || "";
    document.body.click();
    await new Promise((r) => setTimeout(r, 250));

    // 换端点：上一批模型名不许留着冒充这次的结果。真实输入走 callback，纯赋值不走。
    const bw = W("api_base");
    bw.value = "https://other.example.com/v1";
    if (bw.callback) bw.callback(bw.value);
    await new Promise((r) => setTimeout(r, 700));
    const afterSwap = [...(W("api_model").options.values || [])];

    // 失败路径：标红 + 说明行点名原因
    w.options.values = [""];
    window.fetch = async () => ({ ok: false, status: 401,
        json: async () => ({ ok: false, error: "拉不到模型清单：HTTP 401" }) });
    row().querySelector(".mossllm-pick button").click();
    await new Promise((r) => setTimeout(r, 1200));
    const bad = row().querySelector(".mossllm-pick button");
    return { typeIsCombo, pick: true, values, autoOpts, value, okHint,
        busy: busyAttr, busyHint, afterSwap,
        bad: (bad && bad.dataset.bad) || "",
        hint: (grid().querySelector(".mossllm-hint") || {}).textContent || "" };
});
check("api_model 是原生 combo（不是手填输入框）", combo.typeIsCombo, JSON.stringify(combo).slice(0, 100));
check("拉取中看得出在动（按钮转 + 说明行带秒数）",
    combo.busy === "1" && /\d+s/.test(combo.busyHint || ""),
    JSON.stringify({ busy: combo.busy, h: combo.busyHint }));
check("拉完自己展开下拉，不用再点一次",
    (combo.autoOpts || []).includes("model-a") && (combo.autoOpts || []).includes("model-b"),
    JSON.stringify(combo.autoOpts));
check("拉取后下拉里真能选到模型", combo.pick && combo.values.includes("model-a"),
    JSON.stringify(combo.values));
check("拉取后控件里立刻看得见模型名（不是只有下拉内部变了）",
    combo.value === "model-a" && /2/.test(combo.okHint || ""),
    JSON.stringify({ v: combo.value, h: combo.okHint }));
// 当前选中值要留着（清掉会让存盘丢值），但旧批次里其余名字必须消失。
const stale = (combo.afterSwap || []).filter((x) => x && x !== combo.value);
check("换 api_base 后旧模型名不残留（只留当前选中值）",
    // ⚠️ afterSwap 缺失时 filter 出空数组会假绿（早于本批的既有问题）：必须先确认它是数组。
    Array.isArray(combo.afterSwap) && stale.length === 0, JSON.stringify(combo.afterSwap));
check("拉取失败留得下痕迹（按钮标红 + 说明行点名原因）", combo.bad === "1"
    && combo.hint.includes("401"), JSON.stringify(combo.hint.slice(-40)));

// —— 通道与端点的一致性：填端点即切通道；显式切回本地则状态块必须显眼、可一键改道 ——
const autoChan = await page.evaluate(async () => {
    const n = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const W = (name) => n.widgets.find((x) => x.name === name);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const cw = W("channel");
    cw.value = "local";
    if (cw.callback) cw.callback("local");
    await wait(200);
    const bw = W("api_base");
    bw.value = "https://auto.example.com/v1";
    if (bw.callback) bw.callback(bw.value);      // 真实输入走 callback，纯赋值不走
    await wait(400);
    return String(W("channel").value);
});
check("填端点即把通道拨到外部 API（不用记得还有第二个开关）",
    autoChan === "api", autoChan);

const idle = await page.evaluate(async () => {
    const n = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const W = (name) => n.widgets.find((x) => x.name === name);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    W("api_base").value = "https://api.example.com/v1";
    W("api_model").value = "model-a";
    const cw = W("channel");
    cw.value = "local";
    if (cw.callback) cw.callback("local");       // 用户显式切回本地 ⇒ 不抢，但要说明
    await wait(1900);
    const grid = () => document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const badge = grid().querySelector(".mossllm-warn");
    const shown = !!badge && !badge.hidden;
    const out = { shown, text: shown ? badge.textContent : "",
                  tip: shown ? badge.title : "" };
    if (shown) badge.click();
    await wait(500);
    out.chanAfter = String(W("channel").value);
    out.hiddenAfter = !badge || badge.hidden;
    return out;
});
check("端点已填 + 通道=本地 ⇒ 开关条上有显眼状态块（不是摘要里的灰字）",
    idle.shown && /端点已填/.test(idle.text) && idle.text.length > 10,
    JSON.stringify({ shown: idle.shown, text: idle.text }));
check("状态块带完整说明（点它会发生什么、不想用 API 怎么办）",
    /llama-server/.test(idle.tip) && /通道/.test(idle.tip), idle.tip.slice(0, 40));
check("点状态块即切到外部 API（一键修复，端点与模型名原样保留）",
    idle.chanAfter === "api" && idle.hiddenAfter,
    JSON.stringify({ chan: idle.chanAfter, hidden: idle.hiddenAfter }));

// 用户真实踩过的存档形态：端点填着、通道还写本地。**用新页面载入**（复现「打开工作流」），
// 断言面板把它对齐到外部 API 并说明一句 —— 这里不 serialize，避免把宿主内部的 Map 结构带进来。
const page2 = await browser.newPage({ viewport: { width: 1500, height: 1500 } });
await boot(page2, URL_);
const wf2 = JSON.parse(readFileSync(join(ROOT, "examples", "llm-prompt-workbench.json"), "utf8"));
const smith2 = wf2.nodes.find((x) => x.type === "MossPromptSmith");
smith2.widgets_values[1] = "local";
smith2.widgets_values[19] = "https://api.example.com/v1";
smith2.widgets_values[20] = "model-a";
const adopted = await page2.evaluate(async () => {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    await wait(200);
    return true;
});
await loadGraph(page2, wf2);
// 原为固定 `waitForTimeout(3000)`（等面板挂上并 apply 过一次）。改成有界轮询：
// 等这个节点出现「说明行摘要」（= 面板已完成一次 apply，adoptApiIntent 就在那次 apply 里），
// 最多 4000ms；等不到就往下走，断言照旧判红。
const adoptedReady = await pollUntil(() => page2.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    return !!(n && n.__mossllm && n.__mossllm.summary);
}), 4000, 100, page2);
console.log("  page2 面板就绪轮询 =", adoptedReady ? "已 apply 过一次" : "4000ms 内未就绪（下面照旧判红）");
const adopt = await page2.evaluate(() => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const grid = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    return { ch: String(n.widgets.find((x) => x.name === "channel").value),
             hint: grid ? ((grid.querySelector(".mossllm-hint") || {}).textContent || "") : "" };
});
await page2.close();
check("打开「端点已填、通道还写本地」的存档 ⇒ 自动对齐到外部 API，并在摘要里说一句",
    adopt.ch === "api" && /外部 API|External API/.test(adopt.hint), JSON.stringify(adopt));

// —— 模式摘要（B）与执行前缺项（C）：都要靠真页面读 DOM，不能只测函数 ——
const guide = await page.evaluate(async () => {
    const n = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
    const W = (name) => n.widgets.find((x) => x.name === name);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const grid = () => document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
    const badge = () => grid().querySelector(".mossllm-warn");
    const hint = () => grid().querySelector(".mossllm-hint");
    // 隐藏的 widget 行仍在 DOM 里（宿主只是不画），必须按实际高度判可见。
    const vis = (name) => {
        const e = grid().querySelector('[data-widget-name="' + name + '"]');
        return !!e && e.getBoundingClientRect().height > 0;
    };
    const set = async (name, v) => {
        const w = W(name);
        w.value = v;
        if (w.callback) w.callback(v);            // 纯赋值不走 callback，面板收不到
        await wait(700);                          // paintBar 走 rAF + 300ms 兜底，等够再读
    };
    const out = {};

    // ① 本地 + 没有 GGUF：说缺模型，点一下把「本地引擎」组展开
    n.__mossllm.focus = "";
    n.__mossllm.force = false;      // 前面的用例可能停在「全部展开」，那状态下什么都可见
    await set("channel", "local");
    await set("api_base", "");
    W("model").value = "(未找到 GGUF 模型)";
    await set("task", "caption");
    // 切通道会自动聚焦该通道的组，这里先把视图挪到 tune，才测得出「点一下才展开」
    globalThis.MOSS_SB_LLM_PANEL.focus("tune");
    await wait(500);
    out.lackModel = { shown: !badge().hidden, text: badge().textContent,
                      tip: badge().title, act: badge().dataset.act, visBefore: vis("gpu_layers") };
    if (!badge().hidden) badge().click();
    await wait(400);
    out.lackModel.visAfter = vis("gpu_layers");

    // ② 外部 API + 没端点：说缺 api_base
    await set("channel", "api");
    globalThis.MOSS_SB_LLM_PANEL.focus("tune");
    await wait(500);
    out.lackBase = { shown: !badge().hidden, text: badge().textContent, act: badge().dataset.act };
    if (!badge().hidden) badge().click();
    await wait(400);
    out.lackBase.visAfter = vis("api_key");

    // ③ 有端点、没模型名
    await set("api_model", "");
    await set("api_base", "https://guide.example.com/v1");
    await wait(500);
    out.lackName = { shown: !badge().hidden, text: badge().textContent };

    // ④ Key 空不算缺（Ollama / LM Studio 不要 Key）—— 防误报的对照组
    await set("api_model", "model-a");
    out.noKey = { shown: !badge().hidden, text: badge().hidden ? "" : badge().textContent };

    // ⑤ 拼装任务不开模型 ⇒ 不该报缺模型
    await set("task", "assemble");
    await wait(500);
    out.compile = { shown: !badge().hidden, hint: hint().textContent };

    // ⑥ 模式摘要随 task/channel 变，完整说明在 title 里
    await set("task", "expand");
    await wait(300);
    out.expand = { hint: hint().textContent, title: hint().title };
    await set("task", "caption");
    await wait(500);
    out.caption = { hint: hint().textContent, title: hint().title };

    // 复原，别把现场留给后面的用例
    n.__mossllm.focus = "";
    await set("channel", "local");
    await set("api_base", "");
    await set("task", "caption");
    return out;
});
check("本地没有 GGUF ⇒ 跑之前就指出缺模型（不用等执行报错）",
    guide.lackModel.shown && /GGUF/.test(guide.lackModel.text), JSON.stringify(guide.lackModel.text));
check("点缺项提示会展开对应分组（本地引擎），不替用户填值",
    guide.lackModel.act === "open-local" && !guide.lackModel.visBefore && guide.lackModel.visAfter,
    JSON.stringify({ act: guide.lackModel.act, before: guide.lackModel.visBefore, after: guide.lackModel.visAfter }));
check("外部 API 没填端点 ⇒ 指出缺 api_base，并展开 API 组",
    guide.lackBase.shown && /api_base/.test(guide.lackBase.text) && guide.lackBase.visAfter,
    JSON.stringify(guide.lackBase));
check("有端点没模型名 ⇒ 指出缺模型名",
    guide.lackName.shown && /模型名/.test(guide.lackName.text), JSON.stringify(guide.lackName.text));
check("Key 留空不算缺（Ollama / LM Studio 本来就不要 Key）",
    !guide.noKey.shown, JSON.stringify(guide.noKey));
check("拼装任务不报缺模型（它不开模型）",
    !guide.compile.shown && /拼装/.test(guide.compile.hint), JSON.stringify(guide.compile));
check("摘要行说人话：现在是哪个模式 + 哪个通道",
    /扩写/.test(guide.expand.hint) && /外部 API/.test(guide.expand.hint)
    && /读图/.test(guide.caption.hint), JSON.stringify([guide.expand.hint, guide.caption.hint]));
check("完整说明（要不要视觉塔 / 要不要图）挂在 title 上，不占视觉宽度",
    /视觉塔/.test(guide.caption.title) && /不需要图/.test(
        await page.evaluate(() => {
            const n = app.graph.nodes.filter((x) => x.type === "MossPromptSmith")[0];
            const w = n.widgets.find((x) => x.name === "task");
            w.value = "expand"; if (w.callback) w.callback("expand");
            return document.querySelector('[data-widgets-grid-node-id="' + n.id + '"] .mossllm-hint').title;
        })),
    guide.caption.title.slice(0, 40));

// —— (d0) 常用端点下拉：api_base 行内的 select（不是 widget、不改存档）——
const ep = await page.evaluate(async () => {
    const n = app.graph.nodes.find((x) => x.type === "MossPromptSmith");
    const W = (name) => n.widgets.find((x) => x.name === name);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const setW = async (name, v) => {
        const w = W(name);
        w.value = v;
        if (w.callback) w.callback(v);            // 纯赋值不走 callback，面板收不到
        await wait(700);
    };
    // 宿主只为**可见**的控件渲染行：api_base 属于 API 组，折叠时 DOM 里根本没有这一行。
    // 所以先把视图挪到 API 组，再去找挂在上面的下拉（这也正是用户看得见它的状态）。
    const selOf = () => {
        const g = document.querySelector('[data-widgets-grid-node-id="' + n.id + '"]');
        return g ? g.querySelector("[data-mossllm-endpoint]") : null;
    };
    const out = {};
    await setW("api_base", "");
    await setW("channel", "local");               // 从「本地」起步，才验得出它会切通道
    globalThis.MOSS_SB_LLM_PANEL.focus("api");
    // 原为固定 wait(900)：下拉要等宿主重绘出 api_base 那一行、再等面板下一次 apply 挂上去，
    // 而那次 apply 可能来自 setInterval(guard, 1500) ⇒ 900ms 常落在两次轮询之间。
    // 改成每 100ms 探一次、最多 4000ms；等不到 mounted=false，下面的断言照旧判红。
    const t0 = Date.now();
    let sel = null;
    while (!sel && Date.now() - t0 < 4000) {
        sel = selOf();
        if (!sel) await wait(100);
    }
    out.mounted = !!sel;
    out.waited = Date.now() - t0;                 // 实际等了多久（证明不是靠调大超时蒙）
    if (!sel) return out;
    out.idle = { base: W("api_base").value, channel: W("channel").value, sel: sel.value };
    // a) 选 DeepSeek：写入实测值 + 切通道 + 说明行点名
    sel.value = "https://api.deepseek.com/v1";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    await wait(1000);
    out.pick = { base: W("api_base").value, channel: W("channel").value,
                 sel: selOf() ? selOf().value : null,
                 hint: document.querySelector(".mossllm-hint").textContent };
    // b) 手填自己的网关 ⇒ 下拉回落到「自定义」；再选「自定义」不许改掉它
    await setW("api_base", "https://my.gateway.example/v1");
    sel = selOf();
    out.custom = { sel: sel ? sel.value : null, base: W("api_base").value };
    if (sel) {
        sel.value = "";
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        await wait(700);
    }
    const selAfter = selOf();
    out.customAfter = { sel: selAfter ? selAfter.value : null, base: W("api_base").value };
    // c) 它不是 widget：控件仍 38 个、存档格数仍 38、没有多出 widget 名
    out.store = { declared: (globalThis.MOSS_SB_LLM_PANEL.where()[0] || {}).widgets,
                  saved: globalThis.MOSS_SB_LLM_PANEL.values(),
                  asWidget: n.widgets.filter((w) => /endpoint/i.test(String(w.name))).length };
    // 复原现场，别留给后面的用例
    await setW("api_base", "");
    await setW("channel", "local");
    globalThis.MOSS_SB_LLM_PANEL.fold();
    await wait(400);
    return out;
});
console.log("  端点下拉轮询等待 =", ep.waited, "ms", ep.mounted ? "（挂上了）" : "（4000ms 未挂上，下面照旧判红）");
check("选中预置端点 ⇒ api_base 写入实测值、通道从本地切到外部 API、说明行点名",
    ep.mounted && ep.pick && ep.idle.channel === "local" && ep.idle.sel === ""
    && ep.pick.base === "https://api.deepseek.com/v1" && ep.pick.channel === "api"
    && ep.pick.sel === "https://api.deepseek.com/v1" && /DeepSeek/.test(ep.pick.hint),
    JSON.stringify({ idle: ep.idle, pick: ep.pick }));
check("选「自定义」不改原值（下拉只是 api_base 的镜像，手填的网关不被覆盖）",
    ep.custom && ep.custom.sel === "" && ep.custom.base === "https://my.gateway.example/v1"
    && ep.customAfter.base === "https://my.gateway.example/v1" && ep.customAfter.sel === "",
    JSON.stringify([ep.custom, ep.customAfter]));
check("常用端点下拉不是 widget：控件 38 个、存档格数仍是 38、没有新 widget 名",
    ep.store && ep.store.declared === 38 && ep.store.saved.length > 0
    && ep.store.saved.every((x) => x === 38) && ep.store.asWidget === 0,
    JSON.stringify(ep.store));

// —— (e) 窄宽度：加了「通道」前缀后，三个 chip 仍要落在开关条里 ——
const origSize = await page.evaluate((id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    return [n.size[0], n.size[1]];
}, ID);
await page.evaluate((id) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    n.setSize([260, n.size[1]]);
}, ID);
await page.waitForTimeout(800);
const narrow = await page.evaluate((id) => {
    const g = document.querySelector('[data-widgets-grid-node-id="' + id + '"]');
    const bar = g.querySelector('[data-widget-name="mosssb_llm_panel"]');
    const bb = bar.getBoundingClientRect();
    const rects = ["tune", "local", "api"].map((k) => {
        const e = document.querySelector('[data-mossllm-chip="' + k + '"]');
        if (!e) return { k, inside: false };
        const r = e.getBoundingClientRect();
        return { k, inside: r.left >= bb.left - 1 && r.right <= bb.right + 1
            && r.top >= bb.top - 1 && r.bottom <= bb.bottom + 1 };
    });
    return { rects, overflow: bar.scrollWidth > bar.clientWidth + 1, sw: bar.scrollWidth, cw: bar.clientWidth };
}, ID);
check("节点宽 260px 时三个 chip 都在开关条范围内，且开关条不横向溢出",
    narrow.rects.every((x) => x.inside) && !narrow.overflow, JSON.stringify(narrow));
await page.evaluate(([id, w, h]) => {
    const n = app.graph.nodes.find((x) => String(x.id) === id);
    n.setSize([w, h]);
}, [ID, origSize[0], origSize[1]]);

check("页面无 JS 异常", errs.length === 0, errs.join(" | "));
const code = c.done();
await browser.close();
process.exit(code);
