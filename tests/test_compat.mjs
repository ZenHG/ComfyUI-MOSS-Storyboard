// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 旧工作流错位修复的**纯函数单测**（零依赖、零浏览器）：
//     node tests/test_compat.mjs
//
// 测的是判据，不是覆盖率：
//   ① 长度一致 ⇒ 一律不动（哪怕有单个非法值——那是用户手改的，替他做主比报错更坏）
//   ② 少一格 ⇒ 补默认值并把后段右移，且"补哪一格"要选对
//   ③ 多一格 ⇒ 删掉多余的那个
//   ④ 差得太多 ⇒ 不猜
//   ⑤ 类型口径：下拉认选项、数字认 number、开关认 boolean、文本认 string

import { repairValues, widgetSpec, valueOk } from "../web/moss_sb_widget_order.js";

let pass = 0;
const fails = [];

function check(name, cond, detail = "") {
    if (cond) { pass += 1; console.log("  [OK]   " + name + (detail ? "  " + detail : "")); }
    else { fails.push(name); console.log("  [FAIL] " + name + (detail ? "  " + detail : "")); }
}

/** 造一组"像 litegraph widget"的纯对象。 */
function W(name, type, value, values) {
    const w = { name, type, value };
    if (values) w.options = { values };
    return w;
}

/** 采样节点当前顺序（与 nodes.py 一致，含前端插入的 control_after_generate）。 */
function sampler() {
    return [
        W("positive", "customtext", ""),
        W("negative", "customtext", ""),
        W("steps", "number", 50),
        W("cfg", "number", 3),
        W("sampler_name", "combo", "euler", ["euler", "heun"]),
        W("scheduler", "combo", "sgm_uniform", ["simple", "sgm_uniform"]),
        W("seed", "number", 0),
        W("control_after_generate", "combo", "randomize", ["fixed", "increment", "decrement", "randomize"]),
        W("denoise", "number", 1),
        W("panels", "number", 8),
        W("panel_aspect", "combo", "16:9", ["16:9", "3:2", "1:1"]),
        W("panel_edge", "number", 512),
        W("canvas_aspect", "combo", "auto", ["auto", "16:9", "1:1"]),
        W("gutter", "number", 8),
        W("margin", "number", 8),
        W("init", "combo", "auto", ["auto", "layout", "plain"]),
        W("label", "combo", "object", ["object", "character", "text"]),
        W("reborder", "toggle", true),
        W("border_px", "number", 0),
        W("shave", "number", 0),
        W("mode", "combo", "整张", ["整张", "逐格"]),
        W("sample_edge", "number", 1024),
    ];
}

console.log("MOSS Storyboard · 旧工作流错位修复 单测");
console.log("=".repeat(64));

// ---------- 类型口径 ----------
check("下拉的值必须在选项里", valueOk(widgetSpec(W("a", "combo", "x", ["x", "y"])), "x")
    && !valueOk(widgetSpec(W("a", "combo", "x", ["x", "y"])), "16:9"));
check("数字只认 number（'16:9' 不算）", valueOk(widgetSpec(W("n", "number", 1)), 3)
    && !valueOk(widgetSpec(W("n", "number", 1)), "16:9"));
check("开关只认 boolean（0 不算）", valueOk(widgetSpec(W("b", "toggle", true)), false)
    && !valueOk(widgetSpec(W("b", "toggle", true)), 0));
check("文本只认 string（数字不算）", valueOk(widgetSpec(W("s", "customtext", "")), "")
    && !valueOk(widgetSpec(W("s", "customtext", "")), 3));
// ---------- 不该动的三种情形 ----------
const w = sampler();
check("长度一致 ⇒ 返回 null（不猜）", repairValues(w.map((x) => x.value), w) === null);
const one_bad = w.map((x) => x.value);
one_bad[10] = "nope";
check("长度一致但有单个非法值 ⇒ 仍然不猜", repairValues(one_bad, w) === null);
check("差 4 格 ⇒ 超出上限（默认 3），不猜", repairValues(w.slice(0, -4).map((x) => x.value), w) === null);
check("非数组 / 空 ⇒ null", repairValues(null, w) === null && repairValues([], []) === null);

// ---------- 少一格：必须补在正确的下标 ----------
const correct = w.map((x) => x.value);
const missingPanels = correct.slice(0, 9).concat(correct.slice(10));   // 旧稿没有 panels 这一格
const r1 = repairValues(missingPanels, w);
check("少一格能修", !!r1, r1 ? `补在 ${r1.at}（${r1.added}）` : "");
check("补的位置 = panels（下标 9）", !!r1 && r1.at === 9 && r1.added[0] === "panels");
check("补完与正确答案逐项一致（那一格是默认值）",
    !!r1 && r1.values.every((v, i) => i === 9 ? v === 8 : v === correct[i]));

// ---------- 末尾缺值：不会错位，所以**不该动**（宿主本来就用默认值兜底） ----------
const shortTail = correct.slice(0, -1);
check("末尾少一格 ⇒ 返回 null（尾部缺失不会错位）", repairValues(shortTail, w) === null);

// ---------- 多一格：删掉 ----------
const extra = correct.slice(0, 9).concat(["", correct[9]], correct.slice(10));  // 多了一个空串
const r3 = repairValues(extra, w);
check("多一格 ⇒ 能删掉", !!r3 && r3.removed.length === 1, r3 ? `删 ${r3.removed}` : "");
check("删完与正确答案一致", !!r3 && r3.values.every((v, i) => v === correct[i]));

// ---------- 差两格：不能"补到末尾看着合法"就算完 ----------
// 删掉 gutter / margin（下标 13、14）。它们的默认值都是 8，
// 若从末尾往前试，会得到"在尾部补两个 8"这种**全合法但整体右移一格**的错误修复。
const missTwo = correct.slice(0, 13).concat(correct.slice(15));
const r5 = repairValues(missTwo, w);
check("少两格能修", !!r5, r5 ? `补在 ${r5.at}（${r5.added}）` : "");
check("少两格补在原位（不是补到末尾）", !!r5 && r5.at === 13);
check("少两格修完逐项一致（补在原位，不是塞到尾部）",
    !!r5 && r5.values.every((v, i) => v === correct[i]));

const extraTwo = correct.slice(0, 13).concat(["x", "y"], correct.slice(13));
const r6 = repairValues(extraTwo, w);
check("多两格能删", !!r6 && r6.removed.length === 2, r6 ? `删 ${r6.removed}` : "");
check("多两格删完逐项一致", !!r6 && r6.values.every((v, i) => v === correct[i]));

// ---------- 真实事故样本（2026-10-28 用户工作流） ----------
const realBad = correct.slice(0, 9).concat(correct.slice(10));
const r4 = repairValues(realBad, w);
check("事故样本修完：panel_aspect 回到 16:9，panel_edge 回到 512",
    !!r4 && r4.values[10] === "16:9" && r4.values[11] === 512);

// ---------- 线上真实形态：23 格（末尾带 i18n 语言条），存档 21 项缺中间一格 ----------
// 前端存 workflow 时会跳过 i18n 语言条，所以"存档格数"天生比 widget 数少 1。
// 一份"中间缺一格"的稿子于是差 2 格，若按差值补 2 格就永远补不进去 ⇒ 界面一直红。
const withBar = sampler().concat([{
    name: "mosssb_i18n_bar", type: "custom", value: "", element: {}, options: { serialize: false },
}]);
const liveBad = correct.slice(0, 9).concat(correct.slice(10));      // 21 项，缺 panels
const r7 = repairValues(liveBad, withBar);
check("23 格含 i18n 条 + 存档 21 项 ⇒ 能修", !!r7, r7 ? `补在 ${r7.at}，补 ${r7.delta} 格` : "");
check("补的格数是 1（不是长度差 2）", !!r7 && r7.delta === 1);
check("补在 panels（下标 9）且后续归位",
    !!r7 && r7.at === 9 && r7.values[9] === 8 && r7.values[10] === "16:9" && r7.values[11] === 512);

// 存活的当前版本稿子：22 项（末尾就少语言条那一格）⇒ 不该被动
const liveOk = correct.slice(0, 22);
check("当前版本稿子（22 项 / 23 格，只少尾部语言条）⇒ 不猜", repairValues(liveOk, withBar) === null);

console.log(`\n结果：通过 ${pass} / 失败 ${fails.length}`);if (fails.length) { for (const f of fails) console.log("  - " + f); process.exit(1); }
process.exit(0);
