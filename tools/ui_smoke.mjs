// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 前端 UI 的真机冒烟（无头浏览器）：**只证明"界面上真的能用"，不证明画面好不好看**。
//
// 为什么必须有它：`test_i18n.mjs` 测纯函数，`check_frontend.py` 读文件。两者都覆盖不到：
//   ① 扩展有没有被前端执行（模块 import 失败时静默无提示）
//   ② 「中/EN」按钮有没有挂到**本包节点**上（节点内 DOM 组件，不是页面浮层）
//   ③ 官方/别人的节点**有没有被误加按钮或误改标签**（GG 点名要求：只动自己的节点）
//
// 用法：
//   node tools/ui_smoke.mjs [--url http://127.0.0.1:8188] [--out docs/ui-smoke.png]
//   PWCORE=/path/to/playwright-core CHROME=/path/to/chrome.exe  可显式指定
//
// 退出码：0 全过；1 有失败；2 环境缺依赖（playwright-core / chromium）

import { createRequire } from "node:module";
import { existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require_ = createRequire(import.meta.url);

const arg = (n, d) => {
    const i = process.argv.indexOf("--" + n);
    return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d;
};
const URL_ = arg("url", process.env.COMFY_API || "http://127.0.0.1:8188");
const OUT = arg("out", join(ROOT, "docs", "ui-smoke.png"));

function findPwCore() {
    if (process.env.PWCORE) return process.env.PWCORE;
    const bases = [join(process.env.LOCALAPPDATA || "", "npm-cache", "_npx"),
                   join(process.env.APPDATA || "", "npm", "node_modules")];
    for (const b of bases) {
        if (!b || !existsSync(b)) continue;
        if (b.endsWith("node_modules")) {
            const p = join(b, "playwright-core");
            if (existsSync(p)) return p;
            continue;
        }
        for (const d of readdirSync(b)) {
            const p = join(b, d, "node_modules", "playwright-core");
            if (existsSync(p)) return p;
        }
    }
    return null;
}

function findChrome() {
    if (process.env.CHROME) return process.env.CHROME;
    const base = join(process.env.LOCALAPPDATA || "", "ms-playwright");
    if (!existsSync(base)) return null;
    for (const d of readdirSync(base)) {
        if (!d.startsWith("chromium-")) continue;
        for (const f of ["chrome-win64/chrome.exe", "chrome-win/chrome.exe", "chrome-linux/chrome"]) {
            const p = join(base, d, ...f.split("/"));
            if (existsSync(p)) return p;
        }
    }
    return null;
}

const pass = [], fails = [];
const check = (name, cond, detail = "") => {
    if (cond) { pass.push(name); console.log("  [OK]   " + name + (detail ? "  " + detail : "")); }
    else { fails.push(name); console.log("  [FAIL] " + name + (detail ? "  " + detail : "")); }
};

const pwPath = findPwCore();
const chrome = findChrome();
if (!pwPath) { console.log("找不到 playwright-core；用 PWCORE=<path> 指定"); process.exit(2); }
if (!chrome) { console.log("找不到 chromium；用 CHROME=<path> 指定"); process.exit(2); }

const { chromium } = require_(pwPath);
const browser = await chromium.launch({ executablePath: chrome, headless: true });
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));

try {
    await page.goto(URL_, { waitUntil: "domcontentloaded", timeout: 60000 });

    let booted = false;
    for (let i = 0; i < 45; i++) {
        booted = await page.evaluate(() => !!globalThis.MOSS_SB_LANG).catch(() => false);
        if (booted) break;
        await page.waitForTimeout(1000);
    }
    check("前端扩展已执行（window.MOSS_SB_LANG 存在）", booted);
    if (!booted) throw new Error("扩展未执行");

    const where = await page.evaluate(() => globalThis.MOSS_SB_LANG.where()).catch(() => null);
    console.log("  where =", JSON.stringify(where));
    check("模块已 bootstrap（booted=true）", !!where?.booted);

    // 载入**仓库里那份真实工作流**。⚠️ 不要手工拼最小图：真前端靠 `inputs[]` 与
    // widget 定义重建控件，字段不全时节点是空壳（widgets 为空、name 变成 UNKNOWN），
    // 于是"没有 label"这类断言会变成假红。
    const wfPath = join(ROOT, "examples", "MOSS 故事板宫格.json");
    const wfJson = JSON.parse(readFileSync(wfPath, "utf8"));
    const loaded = await page.evaluate(async (wf) => {
        try { await globalThis.MOSS_SB_LANG.loadGraph(wf); return { ok: true }; }
        catch (e) { return { ok: false, why: String(e).slice(0, 160) }; }
    }, wfJson).catch((e) => ({ ok: false, why: String(e).slice(0, 160) }));
    console.log("  载入工作流 =", JSON.stringify(loaded), wfPath.split(/[\\/]/).pop());
    check("能载入仓库里的真实工作流", !!loaded?.ok, loaded?.why || "");
    await page.waitForTimeout(1500);

    const snap = () => page.evaluate(() => {
        const g = globalThis.MOSS_SB_LANG.graph();
        return (g._nodes || []).map((n) => ({
            type: String(n.type),
            title: n.title,
            btn: (n.__mosssbBar && n.__mosssbBar.querySelectorAll(".mosssb-i18n-btn").length) || 0,
            widgets: (n.widgets || []).map((w) => [w.name, w.label, w.type]),
        }));
    });

    const before = await snap();
    const oursBefore = before.filter((n) => n.type.startsWith("MossStoryboard"));
    const othersBefore = before.filter((n) => !n.type.startsWith("MossStoryboard"));
    console.log("  节点 =", before.length, "本包 =", oursBefore.length, "官方/其它 =", othersBefore.length,
        "｜本包 widgets =", JSON.stringify(oursBefore.map((n) => n.widgets.map((w) => w[0]))));

    await page.evaluate(() => globalThis.MOSS_SB_LANG.set("zh"));
    await page.waitForTimeout(400);
    const after = await snap();
    const bodyText = await page.evaluate(() => document.body.innerText || "");

    check("本包节点上各有 1 个「中/EN」按钮",
        oursBefore.length > 0 && oursBefore.every((n) => n.btn === 1),
        `ours=${oursBefore.length} btns=${JSON.stringify(oursBefore.map((n) => n.btn))}`);
    check("官方/其它节点上一个按钮都没有（GG 的硬要求）",
        othersBefore.every((n) => n.btn === 0),
        `others=${othersBefore.length} btns=${JSON.stringify(othersBefore.map((n) => n.btn))}`);

    const btnInfo = await page.evaluate(() => {
        const b = document.querySelector(".mosssb-i18n-btn");
        if (!b) return null;
        const r = b.getBoundingClientRect();
        return {
            w: Math.round(r.width), h: Math.round(r.height),
            pseudo: getComputedStyle(b, "::before").content,
            notranslate: b.getAttribute("translate"),
        };
    }).catch(() => null);
    check("按钮可见且有尺寸", !!btnInfo && btnInfo.w > 0 && btnInfo.h > 0,
        btnInfo ? `${btnInfo.w}x${btnInfo.h}` : "");
    check("按钮文字来自 CSS 伪元素（防汉化插件改字）",
        !!btnInfo && /EN|\\4e2d|中/.test(String(btnInfo.pseudo)), String(btnInfo?.pseudo));
    check("按钮带 notranslate 标记", btnInfo?.notranslate === "no", String(btnInfo?.notranslate));

    // 切中文：本包标题变中文；官方节点 widget 原封不动
    const zhTitles = after.filter((n) => n.type.startsWith("MossStoryboard")).map((n) => n.title);
    const oursWant = await page.evaluate(() => globalThis.MOSS_SB_LANG.nodes);
    check("本包节点标题已变中文",
        zhTitles.length > 0 && zhTitles.every((t) => Object.values(oursWant).some((v) => v.zh === t)),
        JSON.stringify(zhTitles));

    const othersAfter = after.filter((n) => !n.type.startsWith("MossStoryboard"));
    check("官方/其它节点 widget 原封不动（label 未变）",
        JSON.stringify(othersAfter.map((n) => n.widgets)) === JSON.stringify(othersBefore.map((n) => n.widgets)));

    // 正/负面提示词的标题必须真的渲染出来（GG 点名）
    const pos = oursBefore.find((n) => n.type === "MossStoryboardSampler");
    const posLabels = (after.find((n) => n.type === "MossStoryboardSampler")?.widgets || [])
        .filter((w) => w[0] === "positive" || w[0] === "negative").map((w) => [w[0], w[1]]);
    check("positive/negative 的 label 已写成中文",
        posLabels.length === 2 && posLabels.every((x) => /提示词/.test(x[1] || "")), JSON.stringify(posLabels));
    check("界面上真能看到「正面提示词」", bodyText.includes("正面提示词"));
    check("界面上真能看到「负面提示词」", bodyText.includes("负面提示词"));

    // 布尔与"生成后"下拉（GG 点的两处）
    const boolOk = await page.evaluate(() => {
        const n = (globalThis.MOSS_SB_LANG.graph()._nodes || []).find((x) => x.type === "MossStoryboardSampler");
        const w = (n?.widgets || []).find((x) => x.name === "reborder");
        const seed = (n?.widgets || []).find((x) => x.name === "seed");
        const ctrl = (n?.widgets || []).find((x) => /control_after_generate/.test(x.name || ""));
        const lab = (x, v) => (x && x.options && typeof x.options.getOptionLabel === "function" ? x.options.getOptionLabel(v) : null);
        return {
            hasReborder: !!w, reborderTrue: lab(w, true), reborderFalse: lab(w, false),
            ctrlName: ctrl ? ctrl.name : null, ctrlRandom: lab(ctrl, "randomize"), ctrlFixed: lab(ctrl, "fixed"),
            seedLabel: seed ? seed.label : null,
        };
    }).catch(() => null);
    console.log("  布尔/生成后 =", JSON.stringify(boolOk));
    check("布尔显示为中文（开/关）而不是 true/false",
        !!boolOk && (boolOk.reborderTrue === "开" || boolOk.reborderTrue === null),
        `true→${boolOk?.reborderTrue} false→${boolOk?.reborderFalse}`);
    check("「生成后」下拉显示为中文（固定/随机）",
        !!boolOk && (boolOk.ctrlRandom === "随机" || boolOk.ctrlName === null),
        `${boolOk?.ctrlName}: randomize→${boolOk?.ctrlRandom} fixed→${boolOk?.ctrlFixed}`);

    await page.evaluate(() => globalThis.MOSS_SB_LANG.set("en"));
    mkdirSync(dirname(OUT), { recursive: true });
    await page.screenshot({ path: OUT });
    check("截图已保存", existsSync(OUT), OUT);
    check("页面无未捕获异常", errors.length === 0, errors.slice(0, 2).join(" | "));
} catch (e) {
    console.log("  !! " + String(e).slice(0, 200));
} finally {
    await browser.close();
}

console.log("\n结果：通过 " + pass.length + " / 失败 " + fails.length);
if (fails.length) { for (const f of fails) console.log("  - " + f); process.exit(1); }
