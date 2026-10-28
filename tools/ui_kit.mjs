// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 前端真机测试的公共底座。**用例只写"验什么"**，不再各抄一份找依赖/起浏览器/等就绪。
//
// 三条纪律（都是踩过的坑）：
//   ① 依赖找不到要 **exit 2**，不要静默跳过——"跳过了"和"通过了"在报告里必须分得开。
//   ② 等 `app.graph` **和** `app.canvas` 都就绪再载工作流：模块跑得早，画布建得晚，
//      早载会被随后的自动恢复覆盖成空图（实测：节点数 0 + "getCanvas: canvas is null"）。
//   ③ 断言读 **widget.value**（界面真正显示的东西），不是 `widgets_values`（存档数组）。
//      两者可能一个对一个错，只看档位数组会得到"假绿"。
//
// 用法（用例脚本）：
//   import { arg, boot, checks, launch, loadGraph, reload, snapNodes, shotNode } from "./ui_kit.mjs";
//
// 环境变量：PWCORE=<playwright-core 目录>，CHROME=<chrome.exe>，MOSS_PW_BROWSERS=<ms-playwright 目录>

import { createRequire } from "node:module";
import { existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require_ = createRequire(import.meta.url);

/** `--name value` 取值；没有就给默认值。 */
export function arg(name, fallback) {
    const i = process.argv.indexOf("--" + name);
    return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

export const flag = (name) => process.argv.includes("--" + name);

/** 找 playwright-core：env → 本仓 node_modules → npm 全局 → npx 缓存。 */
export function findPwCore() {
    if (process.env.PWCORE) return process.env.PWCORE;
    try { return dirname(require_.resolve("playwright-core/package.json")); } catch (e) { /* 继续找 */ }
    const bases = [join(ROOT, "node_modules"),
                   join(process.env.APPDATA || "", "npm", "node_modules"),
                   join(process.env.LOCALAPPDATA || "", "npm-cache", "_npx")];
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

/** 找某个引擎的可执行文件；找不到返回 null（交给 playwright-core 自己登记表去找）。 */
export function findBrowserExe(engine = "chromium") {
    if (engine === "chromium" && process.env.CHROME) return process.env.CHROME;
    const base = process.env.MOSS_PW_BROWSERS || process.env.PWBROWSERS
        || join(process.env.LOCALAPPDATA || "", "ms-playwright");
    if (!existsSync(base)) return null;
    const pick = (prefix, rels) => {
        for (const d of readdirSync(base)) {
            if (!d.startsWith(prefix)) continue;
            for (const rel of rels) {
                const p = join(base, d, ...rel.split("/"));
                if (existsSync(p)) return p;
            }
        }
        return null;
    };
    if (engine === "chromium") return pick("chromium-", ["chrome-win64/chrome.exe", "chrome-win/chrome.exe", "chrome-linux/chrome"]);
    if (engine === "firefox") return pick("firefox-", ["firefox/firefox.exe", "firefox/firefox"]);
    if (engine === "webkit") return pick("webkit-", ["pw_run.sh", "Playwright.exe"]);
    return null;
}

/** 载入 playwright-core；缺依赖时抛 `code = "ENOPW"`，调用方据此 exit 2。 */
export function loadPlaywright() {
    const p = findPwCore();
    if (!p) {
        const e = new Error("找不到 playwright-core；用 PWCORE=<path> 指定");
        e.code = "ENOPW";
        throw e;
    }
    return { pw: require_(p), path: p };
}

/** 起浏览器：先按默认路径，失败再退回显式 executablePath（离线/多版本机器常见）。 */
export async function launch(engine = "chromium", { headless = true } = {}) {
    const { pw } = loadPlaywright();
    const type = pw[engine];
    if (!type) {
        const e = new Error("playwright-core 里没有 " + engine);
        e.code = "ENOPW";
        throw e;
    }
    try {
        return await type.launch({ headless });
    } catch (e) {
        const exe = findBrowserExe(engine);
        if (!exe) throw e;
        return await type.launch({ headless, executablePath: exe });
    }
}

/** 宿主是否活着。**先探一次**：比等 90 秒超时再报错友好得多。 */
export async function preflight(url) {
    try {
        const r = await fetch(url + "/system_stats", { signal: AbortSignal.timeout(4000) });
        return { ok: r.ok, why: "http " + r.status };
    } catch (e) {
        return { ok: false, why: String(e.message || e).slice(0, 120) };
    }
}

/** 等宿主脚本就绪（`app.graph`）。 */
export async function waitApp(page, timeout = 120000) {
    await page.waitForFunction(() => !!globalThis.app?.graph, null, { timeout });
}

export async function waitCanvas(page, timeout = 60000) {
    await page.waitForFunction(() => !!globalThis.app?.canvas, null, { timeout });
}

/** 打开页面并等"真能用"：graph + canvas 都就绪。 */
export async function boot(page, url, { settle = 1500 } = {}) {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await waitApp(page);
    await waitCanvas(page);
    await page.waitForTimeout(settle);
}

/** 载入工作流对象（不是文件路径——用例自己决定从哪读）。 */
export async function loadGraph(page, wf, { settle = 1500 } = {}) {
    const r = await page.evaluate(async (w) => {
        try {
            await globalThis.app.loadGraphData(w);
            return { ok: true, nodes: globalThis.app.graph.nodes.length };
        } catch (e) {
            return { ok: false, why: String(e).slice(0, 200) };
        }
    }, wf);
    await page.waitForTimeout(settle);
    return r;
}

/** 刷新并重新等就绪——"刷新后才出问题"这类现象只能走这条路径验。 */
export async function reload(page, { settle = 3500 } = {}) {
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitApp(page);
    await waitCanvas(page);
    await page.waitForTimeout(settle);
}

export const getSetting = (page, key) => page.evaluate((k) => {
    try { return globalThis.app.ui.settings.getSettingValue(k); } catch (e) { return null; }
}, key);

export const setSetting = (page, key, value) => page.evaluate(([k, v]) => {
    try { globalThis.app.ui.settings.setSettingValue(k, v); return true; } catch (e) { return false; }
}, [key, value]);

/**
 * 快照所有节点：**界面值**（widget.value）+ 存档值（widgets_values）+ 存档长度。
 * 不判断对错——"什么算非法"是各包自己的口径，交给用例。
 */
export async function snapNodes(page, { prefix = "" } = {}) {
    return page.evaluate((p) => (globalThis.app.graph.nodes || [])
        .filter((n) => n.type && String(n.type).startsWith(p))
        .map((n) => ({
            type: String(n.type),
            widgets: (n.widgets || []).filter(Boolean).map((w) => ({
                name: w.name,
                type: String(w.type || ""),
                value: w.value,
                values: Array.isArray(w.options && w.options.values) ? w.options.values : null,
            })),
            values: Array.isArray(n.widgets_values) ? n.widgets_values.slice() : null,
            serialized: (n.serialize?.()?.widgets_values || []).length,
        })), prefix);
}

/** 把节点摆到视口里再截图（默认的整页截图常常只拍到空白画布）。 */
export async function shotNode(page, type, outPath, { x = 330, y = 40 } = {}) {
    await page.evaluate(([t, ox, oy]) => {
        const n = globalThis.app.graph.nodes.find((x) => x.type === t);
        if (!n) return;
        const ds = globalThis.app.canvas.ds;
        ds.scale = 1;
        ds.offset = [ox - n.pos[0], oy - n.pos[1]];
        globalThis.app.canvas.setDirty(true, true);
        globalThis.app.canvas.draw(true, true);
    }, [type, x, y]);
    await page.waitForTimeout(600);
    mkdirSync(dirname(outPath), { recursive: true });
    await page.screenshot({ path: outPath });
    return outPath;
}

export const readJson = (p) => JSON.parse(readFileSync(p, "utf8"));

/**
 * 界面上会不会显示成红框（与宿主判定一致）：下拉的值不在选项里 / 数字不是数 / 开关不是布尔。
 * 读的是 `widget.value`——**界面真正显示的东西**，不是存档数组。
 */
export function redWidgets(node) {
    const bad = [];
    for (const w of node.widgets || []) {
        let ok;
        if (w.values) ok = w.values.some((x) => x === w.value || String(x) === String(w.value));
        else if (w.type === "number" || w.type === "slider") ok = typeof w.value === "number" && Number.isFinite(w.value);
        else if (w.type === "toggle") ok = typeof w.value === "boolean";
        else ok = w.value == null || typeof w.value === "string";
        if (!ok) bad.push(`${w.name}=${JSON.stringify(w.value)}`);
    }
    return bad;
}

/** 断言计数：`done()` 返回进程退出码（0 全过 / 1 有失败）。 */
export function checks(title, { prefix = "" } = {}) {
    const pass = [];
    const fails = [];
    let pre = prefix;
    console.log(title);
    console.log("=".repeat(64));
    return {
        setPrefix(p) { pre = p; },
        get pass() { return pass; },
        get fails() { return fails; },
        check(name, cond, detail = "") {
            const n = pre ? `${pre}｜${name}` : name;
            if (cond) { pass.push(n); console.log("  [OK]   " + n + (detail ? "  " + detail : "")); }
            else { fails.push(n); console.log("  [FAIL] " + n + (detail ? "  " + detail : "")); }
            return !!cond;
        },
        skip(name, why) { console.log(`  [SKIP] ${name}  ${why}`); },
        done() {
            console.log(`\n结果：通过 ${pass.length} / 失败 ${fails.length}`);
            if (fails.length) { for (const f of fails) console.log("  - " + f); }
            return fails.length ? 1 : 0;
        },
    };
}
