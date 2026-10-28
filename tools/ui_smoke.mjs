// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 前端 UI 的真机冒烟（无头浏览器）：**只证明"界面上真的能用"，不证明画面好不好看**。
//
// 为什么必须有它：`test_i18n.mjs` 测纯函数，`check_frontend.py` 读文件。两者都覆盖不到：
//   ① 扩展有没有被前端执行（模块 import 失败时静默无提示）
//   ② 「中/EN」按钮有没有挂到**本包节点**上（节点内 DOM 组件，不是页面浮层）
//   ③ 官方/别人的节点**有没有被误加按钮或误改标签**（本包只动自己的节点）
//
// 用法：
//   node tools/ui_smoke.mjs [--url http://127.0.0.1:8188] [--out docs/ui-smoke.png]
//   PWCORE=/path/to/playwright-core CHROME=/path/to/chrome.exe  可显式指定
//
// 退出码：0 全过；1 有失败；2 环境缺依赖（playwright-core / chromium）

import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { arg, boot, checks, launch, ROOT } from "./ui_kit.mjs";

const URL_ = arg("url", process.env.COMFY_API || "http://127.0.0.1:8188");
const OUT = arg("out", join(ROOT, "docs", "ui-smoke.png"));

let browser;
try {
    browser = await launch("chromium", { headless: true });
} catch (e) {
    console.log("起不了浏览器：" + String(e.message || e).slice(0, 160) + "\n用 PWCORE=<path> 指定 playwright-core");
    process.exit(2);
}
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
const c = checks("MOSS Storyboard · 前端真机冒烟");
const check = (n, cond, d) => c.check(n, cond, d);
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));

try {
    await boot(page, URL_);

    check("宿主画布已就绪（app.canvas）", await page.evaluate(() => !!globalThis.app?.canvas).catch(() => false));
    const lang = await page.evaluate(() => !!globalThis.MOSS_SB_LANG).catch(() => false);
    check("前端扩展已执行（window.MOSS_SB_LANG 存在）", lang);
    if (!lang) throw new Error("扩展未执行");

    const where = await page.evaluate(() => globalThis.MOSS_SB_LANG.where()).catch(() => null);
    console.log("  where =", JSON.stringify(where));
    check("模块已 bootstrap（booted=true）", !!where?.booted);

    // 载入**仓库里那份真实工作流**。⚠️ 不要手工拼最小图：真前端靠 `inputs[]` 与
    // widget 定义重建控件，字段不全时节点是空壳（widgets 为空、name 变成 UNKNOWN），
    // 于是"没有 label"这类断言会变成假红。
    const wfPath = join(ROOT, "examples", "moss-storyboard-4panels-ui.json");
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
            // 语言按钮可能挂进编辑器顶栏（__mosssbBar 就是按钮本身），也可能在独立 DOM 行里
            btn: !n.__mosssbBar ? 0
                : (n.__mosssbBar.classList?.contains("mosssb-i18n-btn") ? 1
                    : n.__mosssbBar.querySelectorAll(".mosssb-i18n-btn").length),
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
    check("官方/其它节点上一个按钮都没有（只看本包节点）",
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

    // 正/负面提示词的标题必须真的渲染出来
    const pos = oursBefore.find((n) => n.type === "MossStoryboardSampler");
    const posLabels = (after.find((n) => n.type === "MossStoryboardSampler")?.widgets || [])
        .filter((w) => w[0] === "positive" || w[0] === "negative").map((w) => [w[0], w[1]]);
    check("positive/negative 的 label 已写成中文",
        posLabels.length === 2 && posLabels.every((x) => /提示词/.test(x[1] || "")), JSON.stringify(posLabels));
    check("界面上真能看到「正面提示词」", bodyText.includes("正面提示词"));
    check("界面上真能看到「负面提示词」", bodyText.includes("负面提示词"));

    // 布尔与「生成后」下拉
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

    // ---- 编辑器：语义分层 / 最终提示词预览 / 占位守卫（真 DOM，纯函数闸覆盖不到） ----
    {
        const probe = () => page.evaluate(() => {
            const root = document.querySelector(".mossed-root");
            const node = (globalThis.app.graph._nodes || []).find((n) => n.type === "MossLayoutEditor");
            const hid = (sel, climb) => {
                const el = root?.querySelector(sel);
                if (!el) return null;
                const t = climb ? el.closest(climb) : el;
                return t ? !!t.hidden : null;
            };
            const pr = root?.querySelector(".mossed-prompt")?.getBoundingClientRect();
            const rr = root?.getBoundingClientRect();
            const state = node ? node.widgets.find((w) => w.name === "editor_state").value : "{}";
            return {
                label: hid(".mossed-label", ".mossed-field"), pal: hid(".mossed-palette", ".mossed-row"),
                text: hid(".mossed-text", ".mossed-row"), box: hid(".mossed-boxrow"),
                order: hid(".mossed-orderrow"), align: hid(".mossed-alignrow"),
                sizeVisible: !!root?.querySelector(".mossed-size")?.getClientRects().length,
                noteHidden: hid(".mossed-gridnote"),
                note: root?.querySelector(".mossed-gridnote")?.textContent || "",
                prompt: (() => {
                    const el = root?.querySelector(".mossed-prompt-text");
                    return el ? (el.value ?? el.textContent ?? "") : "";
                })(),
                status: root?.querySelector(".mossed-status")?.textContent || "",
                promptVisible: !!pr && !!rr && pr.top >= rr.top - 1 && pr.bottom <= rr.bottom + 1,
                compiled: globalThis.MOSS_SB_EDITOR.compile(JSON.parse(state)),
            };
        });
        const edWf = JSON.parse(readFileSync(join(ROOT, "examples", "moss-layout-editor-storyboard.json"), "utf8"));
        await page.evaluate((g) => globalThis.app.loadGraphData(g), edWf);
        await page.waitForTimeout(1800);
        const where = await page.evaluate(() => globalThis.MOSS_SB_EDITOR.where());
        check("编辑器节点已挂载", !!where?.booted && where.nodes > 0 && where.mounted === where.nodes,
            JSON.stringify(where));

        const g0 = await probe();
        check("宫格：预览不被面板裁掉（在 root 可见区内）", g0.promptVisible === true, JSON.stringify(g0.promptVisible));
        check("宫格：预览是 [STYLE]+[PANEL n] 稿",
            g0.prompt.startsWith("[STYLE]") && /[PANEL 1]/.test(g0.prompt) && /[PANEL 4]/.test(g0.prompt),
            g0.prompt.slice(0, 60));
        check("宫格：预览 == JS 编译器输出", g0.prompt === g0.compiled,
            `dom=${g0.prompt.length} js=${g0.compiled.length}`);
        check("宫格手写描述 ⇒ 不报占位", !/预设/.test(g0.status), g0.status.slice(0, 110));
        check("宫格状态栏数「格」不数「元素」", /格/.test(g0.status) && !/个元素/.test(g0.status),
            g0.status.slice(0, 60));

        await page.evaluate(() => {
            const rect = document.querySelector('.mossed-svg [data-role="item"]');
            const r = rect.getBoundingClientRect();
            const o = { bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2,
                pointerId: 1, isPrimary: true, button: 0 };
            rect.dispatchEvent(new PointerEvent("pointerdown", o));
            rect.dispatchEvent(new PointerEvent("pointerup", o));
        });
        await page.waitForTimeout(500);
        const flags = (o) => JSON.stringify({ label: o.label, pal: o.pal, text: o.text,
            box: o.box, order: o.order, align: o.align, note: o.noteHidden });
        const g1 = await probe();
        check("宫格选中项：类型/主色/文字/bbox/层级/对齐控件收起",
            [g1.label, g1.pal, g1.text, g1.box, g1.order, g1.align].every((v) => v === true), flags(g1));
        check("宫格选中项：景别与模式说明留下",
            g1.sizeVisible === true && g1.noteHidden === false && g1.note.includes("宫格模式只用镜号"),
            g1.note.slice(0, 60));

        await page.evaluate(async () => {
            const root = document.querySelector(".mossed-root");
            const sel = root.querySelector(".mossed-bar select");
            sel.value = "title-body";
            sel.dispatchEvent(new Event("change", { bubbles: true }));
            await new Promise((r) => setTimeout(r, 300));
            const apply = Array.from(root.querySelectorAll("button")).find((b) => /生成初始框/.test(b.textContent));
            const orig = window.confirm; window.confirm = () => true;
            apply.click();
            await new Promise((r) => setTimeout(r, 600));
            window.confirm = orig;
        });
        const s1 = await probe();
        check("单张：元素控件全部回来",
            [s1.label, s1.pal, s1.box, s1.order, s1.align].every((v) => v === false) && s1.noteHidden === true,
            flags(s1));
        check("单张：预览是原生布局 JSON 且 == JS 编译器输出",
            s1.prompt.includes('"bbox_coord_space":"abs_centralized"') && s1.prompt === s1.compiled,
            s1.prompt.slice(0, 60));
        check("单张：状态栏报出预设文案与占位", /预设文案/.test(s1.status) && /预设占位/.test(s1.status),
            s1.status.slice(0, 140));

        // ⚠ confirm 一律返回 false：这条测的是"排队前会问"，绝不能真的把 GPU 任务排进去。
        // 判据用**提示 id 集合的增量**而不是"队列 == 0"：8188 是共用宿主，别人排的队
        // 不该算成我们的失败（实测被另一路跑批顶出过假红），但我们自己一条都不能多。
        const nag = await page.evaluate(async () => {
            const calls = [];
            const ids = async () => {
                const q = await fetch("/queue").then((r) => r.json()).catch(() => null);
                return q ? [...q.queue_running, ...q.queue_pending]
                    .map((x) => (Array.isArray(x) ? x[1] : x && x.prompt_id)) : null;
            };
            const before = await ids();
            const orig = window.confirm;
            window.confirm = (m) => { calls.push(m); return false; };
            const btn = Array.from(document.querySelectorAll(".mossed-bar button")).find((b) => /排队/.test(b.textContent));
            btn?.click();
            await new Promise((r) => setTimeout(r, 900));
            window.confirm = orig;
            return { calls, before, after: await ids() };
        });
        check("文字占位在排队前确认一次并点名位置",
            nag.calls.length === 1 && /预设文案/.test(nag.calls[0]) && /第 \d/.test(nag.calls[0]),
            JSON.stringify(nag.calls).slice(0, 160));
        const added = nag.after && nag.before
            ? nag.after.filter((id) => !nag.before.includes(id)) : ["读不到队列"];
        check("取消确认后没有多出一条队列任务", added.length === 0,
            `新增 ${JSON.stringify(added)}｜点前 ${nag.before.length} 条（>0 = 宿主被别人占用）`);
    }

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

process.exit(c.done());
