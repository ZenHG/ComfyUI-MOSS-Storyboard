// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 旧工作流错位修复的**真机验收**：改的是判据，就必须在真前端上看见红框消失。
// 纯函数单测（tests/test_compat.mjs）证明规则对；这里证明规则**真的被用上了**。
//
// 三条容易假绿的路径，这里一条都不放过：
//   ① 只测 `loadGraphData`：真实用户是"刷新后"才红 —— 刷新走本地恢复，是另一条路径。
//   ② 只看 `widgets_values`：存档数组对、界面值错，一样是红的 —— 必须读 widget.value。
//   ③ 对齐的稿子被"修"了：没坏就别动，所以还要验示例稿一个字节都没改。
//
// 用法：
//   node tools/ui_widget_compat.mjs [--url http://127.0.0.1:8188] [--headed]
//        [--out docs/ui-widget-compat.png] [--workflow examples/moss-storyboard-4panels-ui.json]
//   PWCORE=<playwright-core>  CHROME=<chrome.exe>  可显式指定
//
// 退出码：0 全过；1 有失败；2 环境问题（宿主没起 / 缺 playwright）

import { arg, boot, checks, flag, launch, loadGraph, preflight, readJson, redWidgets,
         reload, ROOT, shotNode, snapNodes } from "./ui_kit.mjs";
import { join, resolve } from "node:path";

const URL_ = arg("url", process.env.COMFY_API || "http://127.0.0.1:8188");
const OUT = arg("out", join(ROOT, "docs", "ui-widget-compat.png"));
const WF = resolve(ROOT, arg("workflow", join("examples", "moss-storyboard-4panels-ui.json")));
const PREFIX = arg("prefix", "Moss");

const up = await preflight(URL_);
if (!up.ok) {
    console.log(`宿主连不上（${URL_}/system_stats：${up.why}）——先把 ComfyUI 起起来再跑本用例。`);
    process.exit(2);
}

const browser = await launch("chromium", { headless: !flag("headed") });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const compatLogs = [];
page.on("console", (m) => { const t = m.text(); if (t.includes("compat:")) compatLogs.push(t); });

const c = checks("MOSS Storyboard · 旧工作流错位修复 真机验收");
const summary = (snap) => snap.map((n) => `${n.type}(w${n.widgets.length}/v${(n.values || []).length})`).join(" ");
const reds = (snap) => snap.flatMap((n) => redWidgets(n).map((x) => `${n.type}.${x}`));
const pick = (snap, type, names) => {
    const n = snap.find((x) => x.type === type);
    return n ? names.map((k) => `${k}=${JSON.stringify(n.widgets.find((w) => w.name === k)?.value)}`) : ["(缺节点)"];
};

try {
    await boot(page, URL_);

    // ---------- ① 对齐的示例稿：不许动 ----------
    const wf = readJson(WF);
    compatLogs.length = 0;
    const r1 = await loadGraph(page, wf);
    c.check("示例稿能载入", r1.ok, r1.why || `${r1.nodes} 个节点`);
    const s1 = await snapNodes(page, { prefix: PREFIX });
    c.check("示例稿无红框", reds(s1).length === 0, reds(s1).join("；") || summary(s1));
    c.check("示例稿没触发修复（没坏就不动）", compatLogs.length === 0, compatLogs.join(" | ").slice(0, 160));

    // ---------- ② 人为造错位：中间丢 3 个值、尾部补 3 个空串（真机稿的形态） ----------
    const broken = JSON.parse(JSON.stringify(wf));
    let hit = 0;
    for (const n of broken.nodes || []) {
        if (n.type !== "MossStoryboardSampler" || !Array.isArray(n.widgets_values)) continue;
        const v = n.widgets_values.slice();
        v.splice(9, 3);
        v.push("", "", "");
        n.widgets_values = v;
        hit += 1;
    }
    c.check("构造了错位稿（删中间 3 格 + 尾部补 3 个空串）", hit > 0, `${hit} 个节点`);
    compatLogs.length = 0;
    const r2 = await loadGraph(page, broken);
    c.check("错位稿能载入", r2.ok, r2.why || "");
    const s2 = await snapNodes(page, { prefix: PREFIX });
    c.check("错位稿加载后无红框", reds(s2).length === 0, reds(s2).join("；") || "");
    c.check("修复真的跑了（日志有 compat 行）", compatLogs.length > 0, compatLogs[0]?.slice(0, 120) || "");
    c.check("三格归位（单个比例/画布比例/边长都是合法值）",
        pick(s2, "MossStoryboardSampler", ["panels", "panel_aspect", "panel_edge"])
            .every((x) => !x.includes("undefined")),
        pick(s2, "MossStoryboardSampler", ["panels", "panel_aspect", "panel_edge"]).join(" "));

    // ---------- ③ 刷新：用户报的就是这条路径 ----------
    compatLogs.length = 0;
    await reload(page);
    const s3 = await snapNodes(page, { prefix: PREFIX });
    c.check("刷新后仍然无红框", reds(s3).length === 0, reds(s3).join("；") || summary(s3));
    c.check("刷新后值没被二次搬动",
        pick(s3, "MossStoryboardSampler", ["panels", "panel_aspect", "panel_edge"])
            .join(" ") === pick(s2, "MossStoryboardSampler", ["panels", "panel_aspect", "panel_edge"]).join(" "),
        pick(s3, "MossStoryboardSampler", ["panels", "panel_aspect", "panel_edge"]).join(" "));

    // ---------- ④ 存档口径：DOM 组件（serialize:false）不占格子，其余每个都要占一格 ----------
    // 实测（前端 1.57.4）：`serialize()` 不认 `serialize:false`，会连 DOM 组件一起写；
    // 而它们是 `configure` **之后**才挂上的 ⇒ 存档比"载入时的 widget 数"多，
    // 每保存一次就整体错位一位。所以期望是「非虚拟 widget 数」。
    const slots = (n) => n.widgets.filter((w) => !w.virtual).length;
    const mismatch = s3.filter((n) => n.serialized !== slots(n))
        .map((n) => `${n.type} widgets=${n.widgets.length}(虚拟 ${n.widgets.filter((w) => w.virtual).length}) 期望存档=${slots(n)} 实际=${n.serialized}`);
    c.check("存档长度 = 非虚拟 widget 数（不多不少）", mismatch.length === 0, mismatch.join("；") || "");

    // ---------- ⑤ 保存 → 再打开：用户真正的循环，也是"存一次多一格"最容易翻车的地方 ----------
    const saved = await page.evaluate(() => globalThis.app.graph.serialize());
    const sNode = (saved.nodes || []).find((n) => n.type === "MossStoryboardSampler");
    c.check("存出来的采样节点存档长度 = 22（全部是真实 widget）",
        Array.isArray(sNode?.widgets_values) && sNode.widgets_values.length === 22,
        `长度 ${sNode?.widgets_values?.length}`);

    compatLogs.length = 0;
    const r5 = await loadGraph(page, saved);
    const s5 = await snapNodes(page, { prefix: PREFIX });
    c.check("把存出来的结果重新打开：无红框", r5.ok && reds(s5).length === 0,
        reds(s5).join("；") || summary(s5));
    c.check("重新打开不需要再修（存出来的就是对齐的）", compatLogs.length === 0,
        compatLogs[0]?.slice(0, 140) || "");

    await shotNode(page, "MossStoryboardSampler", OUT);
    console.log(`  截图：${OUT}`);
} catch (e) {
    c.check("用例执行不中断", false, String(e.message || e).slice(0, 200));
} finally {
    await browser.close();
}

process.exit(c.done());
