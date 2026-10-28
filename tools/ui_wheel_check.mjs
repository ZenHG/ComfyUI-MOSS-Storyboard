// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 滚轮归属的真机验收（无头浏览器 × 三种引擎）：**节点内滚轮只滚编辑器，绝不缩放画布**。
//
// 为什么非要有它：`tools/check_editor_ui.py` 的 W15/W16 只读源码字面量，证明不了
//   ① 真前端在 capture 阶段把 wheel 转给画布时，我们的拦截是不是真的更早；
//   ② Chromium / Firefox / WebKit 三个引擎的事件顺序、deltaMode、Shift 行为是否一致。
//
// 用法：
//   node tools/ui_wheel_check.mjs [--url http://127.0.0.1:8188]
//        [--engine chromium|firefox|webkit|all] [--out <png 目录>] [--json <记账文件>]
//   PWCORE=/path/to/playwright-core   PWBROWSERS=/path/to/ms-playwright  可显式指定
//
// 退出码：0 全过；1 有失败；2 环境缺依赖（playwright-core / 对应引擎没装）
//
// 覆盖：真前端的 capture 转发链路、三个引擎（Chromium / Firefox / WebKit）的
//   事件顺序、悬停不聚焦能不能滚、边缘会不会外泄、手势放行、以及「我们到底有没有
//   接管滚动」（探针读 defaultPrevented——接管了就丢掉原生惯性）。
// 不覆盖：本机是 Windows，**macOS 触控板与真 Safari 捏合没有真机取证**；
//   gesture* 只在 WebKit 上确认「不误伤其它引擎」。
//
// ⚠ 测试内容是自己塞进文本框的长文本（不触发 input、不动 state）：这里验的是**滚轮
//   归属**，不是内容管线——内容管线由 ui_smoke.mjs 与 tests/ 负责。

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { arg, checks, launch, ROOT } from "./ui_kit.mjs";

const URL_ = arg("url", process.env.COMFY_API || "http://127.0.0.1:8188");
const ENGINES = (() => {
    const e = arg("engine", "all");
    return e === "all" ? ["chromium", "firefox", "webkit"] : [e];
})();
const OUTDIR = arg("out", join(ROOT, "docs"));
const JSONOUT = arg("json", "");

const c = checks("MOSS Storyboard · 滚轮归属 真机验收");
const check = (n, cond, d) => c.check(n, cond, d);

// ---------------------------------------------------------------------------
// 页面侧小工具
// ---------------------------------------------------------------------------
const VIEW = () => {
    const ds = globalThis.app.canvas.ds;
    return { scale: +ds.scale.toFixed(4), ox: +ds.offset[0].toFixed(2), oy: +ds.offset[1].toFixed(2) };
};
const same = (a, b) => a.scale === b.scale && a.ox === b.ox && a.oy === b.oy;
const fmt = (v) => `${v.scale}x@(${v.ox},${v.oy})`;

async function view(page) { return page.evaluate(VIEW); }

async function wheel(page, x, y, { dx = 0, dy = 0, ctrl = false, shift = false } = {}) {
    await page.mouse.move(x, y);
    if (ctrl) await page.keyboard.down("Control");
    if (shift) await page.keyboard.down("Shift");
    await page.mouse.wheel(dx, dy);
    if (shift) await page.keyboard.up("Shift");
    if (ctrl) await page.keyboard.up("Control");
    await page.waitForTimeout(180);
}

/**
 * 带探针的滚轮：探针注册在扩展**之后**（同一 target 上按注册顺序跑），
 * 因此能读到我们处理完之后 `defaultPrevented` 是什么——
 * false = 我们放给浏览器原生滚；true = 我们吃掉了。
 * 前提：实现里只能 `stopPropagation`，不能 `stopImmediatePropagation`。
 */
async function wheelWithProbe(page, x, y, opt = {}) {
    await page.evaluate(() => {
        globalThis.__wheelProbe = null;
        globalThis.__wheelProbeFn = (e) => {
            globalThis.__wheelProbe = { dp: e.defaultPrevented, dY: e.deltaY, dMode: e.deltaMode };
        };
        globalThis.addEventListener("wheel", globalThis.__wheelProbeFn, true);
    });
    await wheel(page, x, y, opt);
    return page.evaluate(() => {
        globalThis.removeEventListener("wheel", globalThis.__wheelProbeFn, true);
        return globalThis.__wheelProbe;
    });
}

/** 把某个元素塞成长文本（不触发 input ⇒ 不改 state，不会引发布局重算）。 */
async function stuff(page, sel, lines, overflow) {
    return page.evaluate(([s, n, ov]) => {
        const el = document.querySelector(s);
        if (!el) return null;
        el.value = Array.from({ length: n }, (_, i) => `第 ${i + 1} 行 filler text for wheel test`).join("\n");
        if (ov) el.style.overflowY = "auto";
        el.scrollTop = 0;
        return { sh: el.scrollHeight, ch: el.clientHeight };
    }, [sel, lines, overflow]);
}

const stats = (page, sel) => page.evaluate((s) => {
    const el = document.querySelector(s);
    if (!el) return null;
    return { top: Math.round(el.scrollTop), left: Math.round(el.scrollLeft),
             sh: el.scrollHeight, ch: el.clientHeight };
}, sel);

// ---------------------------------------------------------------------------
// 一个引擎跑一遍
// ---------------------------------------------------------------------------
async function runEngine(engine) {
    c.setPrefix(engine);
    console.log(`\n===== ${engine} =====`);
    const browser = await launch(engine);
    const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
    // 诊断用：抢在扩展之前注册（注册顺序决定同一 target 上的执行顺序），只记不拦
    await page.addInitScript(() => {
        globalThis.__lastWheel = null;
        globalThis.addEventListener("wheel", (e) => {
            globalThis.__lastWheel = { dMode: e.deltaMode, dX: e.deltaX, dY: e.deltaY,
                ctrl: e.ctrlKey, shift: e.shiftKey, trusted: e.isTrusted,
                target: (e.target && e.target.className) || String(e.target) };
        }, { capture: true, passive: true });
    });

    try {
        await page.goto(URL_, { waitUntil: "domcontentloaded", timeout: 60000 });
        await page.waitForFunction(() => !!globalThis.app?.canvas, null, { timeout: 60000 });

        // 载仓库里那份真实工作流（宫格 4 段），再把视野钉死：节点左上角落到 (40,40)、缩放 1
        const wf = JSON.parse(readFileSync(join(ROOT, "examples", "moss-layout-editor-storyboard.json"), "utf8"));
        await page.evaluate(async (g) => { await globalThis.app.loadGraphData(g); }, wf);
        await page.waitForTimeout(1500);
        const placed = await page.evaluate(() => {
            const n = (globalThis.app.graph._nodes || []).find((x) => x.type === "MossLayoutEditor");
            if (!n) return null;
            const ds = globalThis.app.canvas.ds;
            ds.scale = 1;
            ds.offset = [40 - n.pos[0], 40 - n.pos[1]];
            globalThis.app.canvas.setDirty(true, true);
            return { pos: [n.pos[0], n.pos[1]], topScreen: 40 };
        });
        check("编辑器节点已挂载", !!placed, JSON.stringify(placed));
        await page.waitForTimeout(500);

        const rect = (s) => page.evaluate((sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return { x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
        }, s);

        const root = await rect(".mossed-root");
        const ta = await rect(".mossed-root textarea.mossed-prompt-text");
        check("根节点与最终提示词框都在视口里", !!root && !!ta
            && ta.y > 0 && ta.y + ta.h < 1200 && ta.x > 0 && ta.x + ta.w < 1600, JSON.stringify({ root, ta }));

        // ---------- ① 最终提示词：滚轮只滚它自己 ----------
        await stuff(page, ".mossed-root textarea.mossed-prompt-text", 40, false);
        const v0 = await view(page);
        await wheel(page, ta.cx, ta.cy, { dy: 240 });
        const t1 = await stats(page, ".mossed-root textarea.mossed-prompt-text");
        const v1 = await view(page);
        check("① 滚轮在最终提示词上：文本滚了", !!t1 && t1.top > 0, `scrollTop=${t1?.top} sh=${t1?.sh} ch=${t1?.ch}`);
        check("① 滚轮在最终提示词上：画布一动不动", same(v0, v1), `${fmt(v0)} → ${fmt(v1)}`);

        // ---------- ② 滚到底再滚：不许外泄成缩放 ----------
        await page.evaluate((s) => { const el = document.querySelector(s); el.scrollTop = el.scrollHeight; }, ".mossed-root textarea.mossed-prompt-text");
        const v2 = await view(page);
        await wheel(page, ta.cx, ta.cy, { dy: 400 });
        const v3 = await view(page);
        check("② 已经滚到底再滚：画布仍不动（不穿透）", same(v2, v3), `${fmt(v2)} → ${fmt(v3)}`);

        // ---------- ③ Ctrl+滚轮 = 宿主的画布缩放手势，必须放行 ----------
        const v4 = await view(page);
        await wheel(page, ta.cx, ta.cy, { dy: -120, ctrl: true });
        const v5 = await view(page);
        check("③ Ctrl+滚轮在提示词上：画布缩放（放行手势）", !same(v4, v5), `${fmt(v4)} → ${fmt(v5)}`);
        // 还原视野，后面的断言都按 scale=1 摆位
        await page.evaluate(() => {
            const n = (globalThis.app.graph._nodes || []).find((x) => x.type === "MossLayoutEditor");
            const ds = globalThis.app.canvas.ds;
            ds.scale = 1;
            ds.offset = [40 - n.pos[0], 40 - n.pos[1]];
            globalThis.app.canvas.setDirty(true, true);
        });
        await page.waitForTimeout(300);

        // ---------- ④ Shift+滚轮 = 横向，不许动画布 ----------
        const v6 = await view(page);
        await wheel(page, ta.cx, ta.cy, { dx: 60, dy: 0, shift: true });
        const v7 = await view(page);
        check("④ Shift+滚轮在提示词上：画布不横向平移", same(v6, v7), `${fmt(v6)} → ${fmt(v7)}`);

        // ---------- ⑤ 负对照：同一位置、去掉 root 标记 ⇒ 就该缩放 ----------
        // 没有这条，"画布没动"可能只是因为我们压根没把滚轮发过去（假绿）。
        const v8 = await view(page);
        await page.evaluate(() => {
            const r = document.querySelector(".mossed-root");
            r.setAttribute("data-tmp-root", "1");
            r.classList.remove("mossed-root");
        });
        await wheel(page, ta.cx, ta.cy, { dy: 240 });
        const v9 = await view(page);
        check("⑤ 负对照：去掉 root 标记后同一位置会缩放画布", !same(v8, v9), `${fmt(v8)} → ${fmt(v9)}`);
        await page.evaluate(() => {
            const r = document.querySelector('[data-tmp-root="1"]');
            r.classList.add("mossed-root");
            r.removeAttribute("data-tmp-root");
            const n = (globalThis.app.graph._nodes || []).find((x) => x.type === "MossLayoutEditor");
            const ds = globalThis.app.canvas.ds;
            ds.scale = 1;
            ds.offset = [40 - n.pos[0], 40 - n.pos[1]];
            globalThis.app.canvas.setDirty(true, true);
        });
        await page.waitForTimeout(400);

        // ---------- ⑥ 画布空白处 / 节点标题栏：宿主照常工作 ----------
        // "空白处"不能写死坐标：编辑器节点一改尺寸/位置，写死的点就可能落进节点里，
        // 于是滚轮被编辑器正常吃掉、断言却报"宿主不缩放"（2026-10-28 节点长高后三引擎同红）。
        // 判据改成"elementFromPoint 命中的就是画布本身"——这才是真的空白。
        const blank = await page.evaluate(() => {
            const cv = globalThis.app.canvas.canvas;
            const r = cv.getBoundingClientRect();
            for (let y = Math.round(r.top) + 80; y < r.bottom - 60; y += 24) {
                for (let x = Math.round(r.left) + 80; x < r.right - 60; x += 24) {
                    if (document.elementFromPoint(x, y) === cv) return { x, y };
                }
            }
            return null;
        });
        check("⑥ 存在一块真正的空白画布（未被节点/面板压住）", !!blank, JSON.stringify(blank));
        const v10 = await view(page);
        if (blank) await wheel(page, blank.x, blank.y, { dy: -240 });
        const v11 = await view(page);
        check("⑥ 空白画布上滚轮：画布照常缩放/平移", !same(v10, v11), `${fmt(v10)} → ${fmt(v11)}`);

        const v12 = await view(page);
        await wheel(page, root.x + 30, Math.max(placed.topScreen + 12, root.y - 14), { dy: -120, ctrl: true });
        const v13 = await view(page);
        check("⑥ 节点标题栏上 Ctrl+滚轮：画布照常缩放", !same(v12, v13), `${fmt(v12)} → ${fmt(v13)}`);

        // ---------- ⑦ 只抢归属、不接管滚动 ----------
        // 判据：命中可滚容器时**不能** preventDefault——一旦 preventDefault 就是我们在
        // 手算 scrollTop，原生惯性 / 行模式 / 小数 delta 全丢。
        // 这个探针注册在扩展之后，靠 stopPropagation（而非 stopImmediate）才能跑到。
        // 先把视野复位：⑥ 的 Ctrl+滚轮会缩放画布，不复位的话下面按 ds=1 量到的坐标全偏。
        await page.evaluate(() => {
            const n = (globalThis.app.graph._nodes || []).find((x) => x.type === "MossLayoutEditor");
            const ds = globalThis.app.canvas.ds;
            ds.scale = 1;
            ds.offset = [40 - n.pos[0], 40 - n.pos[1]];
            globalThis.app.canvas.setDirty(true, true);
        });
        await page.waitForTimeout(300);
        await page.evaluate((s) => { document.querySelector(s).scrollTop = 0; }, ".mossed-root textarea.mossed-prompt-text");
        const probe = await wheelWithProbe(page, ta.cx, ta.cy, { dy: 200 });
        const t7 = await stats(page, ".mossed-root textarea.mossed-prompt-text");
        check("⑦ 命中滚动容器：不 preventDefault（浏览器原生滚，不自己算 scrollTop）",
            !!probe && probe.dp === false, JSON.stringify(probe));
        check("⑦ 原生滚动确实生效（文本动了）", !!t7 && t7.top > 0, `scrollTop=${t7?.top}`);

        // ---------- ⑦b 非滚动区（预览画布）：吃掉滚轮，绝不误缩放 ----------
        const svg = await rect(".mossed-svg");
        if (svg) {
            const v7b = await view(page);
            const probeB = await wheelWithProbe(page, svg.x + svg.w / 2, svg.y + Math.min(40, svg.h / 2), { dy: 240 });
            const v7c = await view(page);
            check("⑦b 节点内非滚动区：preventDefault 吃掉滚轮", !!probeB && probeB.dp === true, JSON.stringify(probeB));
            check("⑦b 节点内非滚动区：画布不动（不误缩放）", same(v7b, v7c), `${fmt(v7b)} → ${fmt(v7c)}`);
        }

        // ---------- ⑨ 单张模式：描述框 / 属性面板 ----------
        await page.evaluate(() => {
            const n = (globalThis.app.graph._nodes || []).find((x) => x.type === "MossLayoutEditor");
            const ds = globalThis.app.canvas.ds;
            ds.scale = 1;
            ds.offset = [40 - n.pos[0], 40 - n.pos[1]];
            globalThis.app.canvas.setDirty(true, true);
        });
        await page.evaluate(() => globalThis.MOSS_SB_LANG?.set?.("zh"));
        await page.waitForTimeout(300);
        await page.evaluate(async () => {
            const rootEl = document.querySelector(".mossed-root");
            const sel = rootEl.querySelector(".mossed-bar select");
            sel.value = "title-body";
            sel.dispatchEvent(new Event("change", { bubbles: true }));
            await new Promise((r) => setTimeout(r, 250));
            const apply = Array.from(rootEl.querySelectorAll("button")).find((b) => /生成初始框|Generate/i.test(b.textContent));
            if (!apply) throw new Error("找不到「生成初始框」按钮");
            const orig = window.confirm; window.confirm = () => true;
            apply.click();
            await new Promise((r) => setTimeout(r, 500));
            window.confirm = orig;
        });
        await page.evaluate(() => {
            const el = document.querySelector('.mossed-svg [data-role="item"]');
            if (!el) return;
            const r = el.getBoundingClientRect();
            const o = { bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, pointerId: 1, isPrimary: true, button: 0 };
            el.dispatchEvent(new PointerEvent("pointerdown", o));
            el.dispatchEvent(new PointerEvent("pointerup", o));
        });
        await page.waitForTimeout(500);

        const cap = await rect(".mossed-root textarea.mossed-cap");
        const prop = await rect(".mossed-root .mossed-prop");
        check("⑨ 单张模式下描述框存在", !!cap, JSON.stringify(cap));
        if (cap) {
            const st = await stuff(page, ".mossed-root textarea.mossed-cap", 30, true);
            const v14 = await view(page);
            await wheel(page, cap.cx, cap.y + Math.min(cap.h / 2, 60), { dy: 160 });
            const capS = await stats(page, ".mossed-root textarea.mossed-cap");
            const propS = await stats(page, ".mossed-root .mossed-prop");
            const v15 = await view(page);
            check("⑨ 滚轮在描述框上：描述框（或属性面板）滚了",
                (capS && capS.top > 0) || (propS && propS.top > 0),
                `cap=${capS?.top} prop=${propS?.top} sh=${st?.sh} ch=${st?.ch}`);
            check("⑨ 滚轮在描述框上：画布一动不动", same(v14, v15), `${fmt(v14)} → ${fmt(v15)}`);
        }
        if (prop) {
            const p0 = await stats(page, ".mossed-root .mossed-prop");
            const v16 = await view(page);
            // 取面板**右上角**的空白处：面板中部现在压着描述框（它自己也能滚），
            // 落在它上面测的就不是"面板空隙"了（2026-10-28 布局调整后第一次翻车在此）。
            await wheel(page, prop.x + prop.w - 14, prop.y + 12, { dy: 200 });
            const p1 = await stats(page, ".mossed-root .mossed-prop");
            const v17 = await view(page);
            check("⑨ 滚轮在属性面板空隙上：面板滚了、画布不动",
                !!p1 && (p1.top > (p0?.top || 0) || p1.sh <= p1.ch) && same(v16, v17),
                `top ${p0?.top}→${p1?.top} sh=${p1?.sh} ch=${p1?.ch} ${fmt(v16)} → ${fmt(v17)}`);
        }

        // ---------- ⑩ 页面没有被浏览器整页缩放 ----------
        const z = await page.evaluate(() => globalThis.visualViewport?.scale ?? 1);
        check("⑩ 浏览器页面缩放比例仍是 1", Math.abs(z - 1) < 0.001, `visualViewport.scale=${z}`);

        const last = await page.evaluate(() => globalThis.__lastWheel);
        console.log("  最后一次滚轮事件 =", JSON.stringify(last));
        check("页面无未捕获异常", errors.length === 0, errors.slice(0, 2).join(" | "));

        mkdirSync(OUTDIR, { recursive: true });
        const shot = join(OUTDIR, `ui-wheel-${engine}.png`);
        await page.screenshot({ path: shot });
        console.log("  截图 =", shot);
    } catch (e) {
        check("本引擎跑完（无中断）", false, String(e).slice(0, 200));
    } finally {
        await browser.close();
    }
}

for (const e of ENGINES) {
    try { await runEngine(e); }
    catch (err) { check("引擎可启动", false, e + ": " + String(err).slice(0, 160)); }
}

if (JSONOUT) {
    mkdirSync(dirname(JSONOUT), { recursive: true });
    writeFileSync(JSONOUT, JSON.stringify({ when: new Date().toISOString(), engines: ENGINES,
        pass: c.pass, fails: c.fails }, null, 2) + "\n");
    console.log("记账 =", JSONOUT);
}
process.exit(c.done());
