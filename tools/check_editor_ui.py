# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""编辑器前端自检（零 GPU、零浏览器、秒级）。

三查（编辑器 F6 验收）：
- **词表**：预设表 / state 校验 / 吸附 —— 直接 import `web/moss_sb_editor.js` 的纯函数执行，
  不靠正则猜。用临时目录 + 一个 `app.js` 桩绕开宿主模块。
- **语法**：`node --check`（Node 24 已移除 `--experimental-default-type`，自动退 .mjs 副本）。
- **接线**：契约里的节点名 / widget 名 / 8 个 preset 必须与 JS 一致；
  `WEB_DIRECTORY` 存在；编辑器只 import 宿主 `app.js`（单文件约束，不拆本地 module）。

用法：
    python tools/check_editor_ui.py

退出码：0 全过；1 有失败；2 环境缺 node（词表执行与语法检查跳过，接线仍查）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR = os.path.join(ROOT, "web", "moss_sb_editor.js")
DOC = os.path.join(ROOT, "docs", "10-contract.md")
PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + str(detail)) if detail else ""))


def skip(name, why):
    SKIP.append(name)
    print("  [SKIP] " + name + "  " + why)


def find_node():
    for b in ("node", "node.exe"):
        p = shutil.which(b)
        if p:
            return p
    for c in (r"C:/Program Files/nodejs/node.exe",):
        if os.path.exists(c):
            return c
    return None


# ---------------------------------------------------------------------------
# 契约解析（state v3 契约是唯一真相源）
# ---------------------------------------------------------------------------
def load_contract():
    src = open(DOC, encoding="utf-8").read()
    node = re.search(r"`(MossLayoutEditor)`", src)
    widget = re.search(r"(editor_state)\s*:\s*STRING", src)
    ids = []
    m = re.search(r"\| `preset` \| str \| ✅ \| ([^|]+) \|", src)
    if m:
        ids = re.findall(r"`([a-z-]+)`", m.group(1))
    example = None
    # 公开契约里的第一段 json 就是单张示例；宫格示例跟在后面
    m = re.search(r"```json\s*\n(.*?)\n```", src, re.S)
    if m:
        try:
            example = json.loads(m.group(1))
        except ValueError:
            example = None
    return {
        "node": node.group(1) if node else None,
        "widget": widget.group(1) if widget else None,
        "presets": ids,
        "example": example,
    }


# ---------------------------------------------------------------------------
# 语法
# ---------------------------------------------------------------------------
def check_syntax(node):
    if not node:
        skip("S1 编辑器语法", "没有 node")
        return
    r = subprocess.run([node, "--check", "--experimental-default-type=module", EDITOR],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        check("S1 编辑器语法（ESM）", True)
        return
    err = (r.stderr or "") + (r.stdout or "")
    if "bad option" in err:
        # Node >= 22 已移除该 flag：用 .mjs 副本按 ESM 解析
        tmp = tempfile.mkdtemp(prefix="mosssb_syntax_")
        try:
            dst = os.path.join(tmp, "moss_sb_editor.mjs")
            shutil.copyfile(EDITOR, dst)
            r2 = subprocess.run([node, "--check", dst], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            check("S1 编辑器语法（ESM，.mjs 副本）", r2.returncode == 0,
                  (r2.stderr or "").strip().splitlines()[:2])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return
    check("S1 编辑器语法（ESM）", False, err.strip().splitlines()[:2])


# ---------------------------------------------------------------------------
# 纯函数执行（词表）
# ---------------------------------------------------------------------------
RUNNER = r"""
import {
    PRESET_IDS, PRESET_SPECS, PRESET_HINTS, SIZES, ITEM_LABELS,
    TYPE_COLORS, CANVAS_PRESETS, STR,
    presetItems, validateState, readState, snapAxis, stageAspect, makeItemId, emptyState,
    makeHistory, recordHistory, stepHistory, HISTORY_MAX, HISTORY_MAX_CHARS,
    MAX_ITEMS,
    elementsToState, stateToElements, sniffLayoutDoc, ELEMENTS_VERSION,
    alignBbox, moveItemInArray,
    compileFinalPrompt, gridPromptText, layoutJsonText,
    findPlaceholders, findIgnoredGridFields,
} from "./extensions/pack/moss_sb_editor.js";
import { readFileSync } from "node:fs";

const results = [];
const ok = (name, cond, detail = "") => results.push({ name, ok: !!cond, detail: String(detail).slice(0, 300) });
const GRID = { cols: 4, rows: 2, panel_aspect: "16:9", panel_edge: 512, gutter: 8, margin: 8 };
const contract = JSON.parse(readFileSync("./contract.json", "utf8"));

ok("preset 数量与契约一致（8）", PRESET_IDS.length === 8 && PRESET_SPECS.grid, PRESET_IDS.join(","));
ok("契约示例 state 合法", validateState(contract).errors.length === 0, JSON.stringify(validateState(contract).errors));
ok("空白 state 合法", validateState(emptyState()).errors.length === 0, JSON.stringify(validateState(emptyState()).errors));

const counts = {};
for (const id of PRESET_IDS) {
    const isGrid = id === "grid";
    const items = presetItems(id, { cols: GRID.cols, rows: GRID.rows, lang: "zh" });
    counts[id] = items.length;
    const st = isGrid
        ? { version: 3, preset: id, style: "", grid: GRID, items }
        : { version: 3, preset: id, style: "", canvas: { width: 1536, height: 864 }, items };
    ok(`预设 ${id} 生成合法 state`, validateState(st).errors.length === 0, JSON.stringify(validateState(st).errors));
}
ok("预设生成数量", JSON.stringify(counts) === JSON.stringify({ grid: 8, "split-v": 2, "split-h": 2, "title-body": 3, "three-col": 3, center: 1, stack: 2, free: 0 }), JSON.stringify(counts));

// 不重叠（stack 刻意层叠；grid 是格位；free 为空）
for (const id of PRESET_IDS) {
    if (["grid", "stack", "free"].includes(id)) continue;
    const items = presetItems(id, { lang: "zh" });
    let overlap = false;
    for (let i = 0; i < items.length; i += 1) {
        for (let j = i + 1; j < items.length; j += 1) {
            const a = items[i].bbox, b = items[j].bbox;
            if (Math.min(a[2], b[2]) - Math.max(a[0], b[0]) > 0.001
                && Math.min(a[3], b[3]) - Math.max(a[1], b[1]) > 0.001) overlap = true;
        }
    }
    ok(`预设 ${id} 框不重叠`, !overlap);
}

const gi = presetItems("grid", { cols: GRID.cols, rows: GRID.rows });
ok("grid bbox = 格位", gi[0].bbox.join(",") === "0,0,0.25,0.5" && gi[7].bbox.join(",") === "0.75,0.5,1,1", gi.map((x) => x.bbox.join(",")).join(" | "));
ok("grid 景别铺满八档", new Set(gi.map((x) => x.size)).size === SIZES.length);
ok("grid 镜号 1..N", gi.every((x, i) => x.no === i + 1));
ok("所有 id 唯一（各自预设内）", PRESET_IDS.every((id) => {
    const ids = presetItems(id, { cols: 3, rows: 2 }).map((x) => x.id);
    return new Set(ids).size === ids.length;
}));
ok("text 元素必带 text", PRESET_IDS.flatMap((id) => presetItems(id, { lang: "zh" }))
    .filter((x) => x.label === "text").every((x) => typeof x.text === "string" && x.text));

const base = { version: 3, preset: "title-body", style: "", canvas: { width: 1536, height: 864 },
    items: [{ id: "t1", label: "text", caption: "标题", text: "字", bbox: [0.1, 0.1, 0.9, 0.2] }] };
const bad = [
    ["version 2", { ...base, version: 2 }],
    ["bbox 反向", { ...base, items: [{ ...base.items[0], bbox: [0.9, 0.1, 0.1, 0.2] }] }],
    ["text 为空", { ...base, items: [{ ...base.items[0], text: "" }] }],
    ["palette 6 色", { ...base, items: [{ ...base.items[0], palette: ["#111111", "#222222", "#333333", "#444444", "#555555", "#666666"] }] }],
    ["id 重复", { ...base, items: [base.items[0], base.items[0]] }],
    ["canvas 非 16 倍数", { ...base, canvas: { width: 1000, height: 864 } }],
    ["grid 数量不符", { version: 3, preset: "grid", style: "", grid: GRID, items: gi.slice(0, 1) }],
];
for (const [name, obj] of bad) ok(`拒绝 ${name}`, validateState(obj).errors.length > 0);
ok("拒绝零宽 caption", validateState({ ...base, items: [{ ...base.items[0], caption: "\u200b \u200f" }] }).errors.length > 0);
ok("拒绝零宽 text", validateState({ ...base, items: [{ ...base.items[0], text: "\u202e\u2060" }] }).errors.length > 0);
ok("整值浮点 canvas 接受", validateState({ ...base, canvas: { width: 1536.0, height: 864.0 } }).errors.length === 0);
ok("非整浮点 canvas 拒绝", validateState({ ...base, canvas: { width: 1536.5, height: 864.0 } }).errors.length > 0);
const tooMany = {
    ...base,
    items: Array.from({ length: MAX_ITEMS + 1 }, (_, i) => ({ ...base.items[0], id: "t" + i })),
};
ok(`validateState 拒绝 ${MAX_ITEMS + 1} 个元素`,
    validateState(tooMany).errors.some((e) => e.includes(String(MAX_ITEMS))),
    JSON.stringify(validateState(tooMany).errors).slice(0, 120));

ok("readState 空 → 空白 state", readState("").fixed === true && readState("").state.items.length === 0);
ok("readState 坏 JSON → fatal", !!readState("{oops").fatal);
ok("readState v2 → fatal", !!readState(JSON.stringify({ ...base, version: 2 })).fatal);
const fixed = readState(JSON.stringify({ ...base, items: base.items.map(({ id, ...r }) => r) }));
ok("readState 补 id", !fixed.fatal && fixed.fixed && fixed.state.items.every((x) => x.id));

const center = snapAxis([0.494], [0.5], 0.01, 0.01);
ok("吸附到中线", center && Math.abs(center.d - 0.006) < 1e-9 && center.guide === 0.5, JSON.stringify(center));
const thirds = snapAxis([0.339], [1 / 3], 0.01, 0);
ok("吸附到三分线", thirds && Math.abs(thirds.guide - 1 / 3) < 1e-9, JSON.stringify(thirds));
const gridSnap = snapAxis([0.3239], [], 0.003, 16 / 1536);
ok("吸附到 16px 网格", gridSnap && Math.abs(gridSnap.guide - 31 * 16 / 1536) < 1e-9, JSON.stringify(gridSnap));
ok("远离目标不吸附", snapAxis([0.123], [0.5], 0.005, 0) === null);
ok("makeItemId 跳过占用", makeItemId("text", [{ id: "t1" }, { id: "t2" }]) === "t3");
ok("stageAspect single", Math.abs(stageAspect(base) - 1536 / 864) < 1e-9);
ok("stageAspect grid", Math.abs(stageAspect({ preset: "grid", grid: GRID }) - 4 * (16 / 9) / 2) < 1e-9);
ok("emptyState 合法", validateState(emptyState()).errors.length === 0);

ok("预设说明 8 条中英齐全", Object.keys(PRESET_HINTS).length === 8
    && PRESET_IDS.every((id) => PRESET_HINTS[id]?.zh && PRESET_HINTS[id]?.en));
ok("类型色覆盖三类", ITEM_LABELS.every((k) => /^#[0-9a-f]{6}$/i.test(TYPE_COLORS[k] || "")));
ok("画布预设都是 16 倍数", CANVAS_PRESETS.length >= 5
    && CANVAS_PRESETS.every((p) => p.width % 16 === 0 && p.height % 16 === 0 && p.width >= 256 && p.height >= 256));

// --- 最终提示词预览 + 占位守卫（与 core/editor.py 同一口径） ---
{
    const gs = { version: 3, preset: "grid", style: "雨夜咖啡店。", grid: GRID,
        items: presetItems("grid", { cols: GRID.cols, rows: GRID.rows, lang: "zh" }) };
    const gp = compileFinalPrompt(gs);
    ok("宫格预览 = [STYLE] + 连续 [PANEL n]",
        gp.startsWith("[STYLE] 雨夜咖啡店。") && gp.split("\n").filter((l) => l.startsWith("[PANEL")).length === 8
        && gp.includes("[PANEL 8]"), gp.slice(0, 120));
    ok("宫格预览景别输出英文子句 / 已带则不重复 / 自定义原样", (() => {
        const one = { version: 3, preset: "grid", style: "s", grid: GRID,
            items: [{ id: "s1", label: "object", no: 1, size: "近景", caption: "杯沿裂痕", bbox: [0, 0, 1, 1] }] };
        if (compileFinalPrompt(one).split("\n")[2] !== "[PANEL 1] Close shot, framed from the shoulders up. 杯沿裂痕") return false;
        const same = { ...one, items: [{ ...one.items[0], caption: "近景 里的杯沿" }] };
        if (compileFinalPrompt(same).split("\n")[2] !== "[PANEL 1] 近景 里的杯沿") return false;
        const custom = { ...one, items: [{ ...one.items[0], size: "从背后俯拍" }] };
        return compileFinalPrompt(custom).split("\n")[2] === "[PANEL 1] 从背后俯拍，杯沿裂痕";
    })(), compileFinalPrompt(gs).split("\n")[2]);
    ok("宫格预览按镜号排序（不是数组序）", (() => {
        const rev = { ...gs, items: [...gs.items].reverse() };
        return compileFinalPrompt(rev) === gp;
    })());
    ok("宫格空 items 预览为空串", compileFinalPrompt({ ...gs, items: [] }) === "");
    ok("宫格无 style 时只出 [PANEL]",
        compileFinalPrompt({ ...gs, style: "  " }).startsWith("[STYLE]\n\n[PANEL 1]"),
        compileFinalPrompt({ ...gs, style: "  " }).slice(0, 40));

    const ss = { version: 3, preset: "title-body", style: "暗色雪原。",
        canvas: { width: 1536, height: 864 },
        items: [{ id: "t1", label: "text", caption: "标题", text: "永兵",
                  palette: ["#c8a15a"], bbox: [0.08, 0.10, 0.92, 0.19] }] };
    const sp = compileFinalPrompt(ss);
    const parsed = JSON.parse(sp);
    ok("单张预览 = 原生布局 JSON（abs_centralized / yxyx）",
        parsed.bbox_coord_space === "abs_centralized" && parsed.bbox_format === "yxyx"
        && parsed.summary.caption === "暗色雪原。", sp.slice(0, 120));
    ok("单张预览键序 label,caption,bbox,text,palette",
        Object.keys(parsed.items[0]).join(",") === "label,caption,bbox,text,palette",
        Object.keys(parsed.items[0]).join(","));
    ok("单张预览坐标中心原点（[y0,x0,y1,x1]）", parsed.items[0].bbox.join(",") === "-346,-646,-267,646",
        parsed.items[0].bbox.join(","));
    // 取整必须是 floor(左/上) + ceil(右/下)：框要**包住**，与宿主
    // comfy_extras/nodes_moss_image.py 和 KJ 的 _moss_bbox 同规则。
    ok("单张预览 bbox 用 floor/ceil 包住框", (() => {
        const [y0, x0, y1, x1] = parsed.items[0].bbox;
        return y0 === Math.floor(0.10 * 864) - 432 && x0 === Math.floor(0.08 * 1536) - 768
            && y1 === Math.ceil(0.19 * 864) - 432 && x1 === Math.ceil(0.92 * 1536) - 768;
    })());
    ok("单张预览无元素时退化成全局段",
        compileFinalPrompt({ ...ss, items: [], style: " 只有风格 " }) === "只有风格");
    ok("单张预览非 text 元素不带 text 键", (() => {
        const j = JSON.parse(compileFinalPrompt({
            ...ss,
            items: [{ id: "o1", label: "object", caption: "主体", text: "多余", bbox: [0, 0, 1, 1] }],
        }));
        return !("text" in j.items[0]) && j.items[0].bbox.join(",") === "-432,-768,432,768";
    })());
    ok("单张预览空 palette 不输出键", (() => {
        const j = JSON.parse(compileFinalPrompt({ ...ss, items: [{ ...ss.items[0], palette: [] }] }));
        return !("palette" in j.items[0]);
    })());
    ok("gridPromptText / layoutJsonText 与 compileFinalPrompt 分派一致",
        gridPromptText(gs) === compileFinalPrompt(gs) && layoutJsonText(ss) === compileFinalPrompt(ss));
}
{
    const tb = { version: 3, preset: "title-body", style: "", canvas: { width: 1536, height: 864 },
        items: presetItems("title-body", { lang: "zh" }) };
    const ph = findPlaceholders(tb);
    ok("预设单张：文字框算 render 级",
        ph.filter((f) => f.kind === "render").length === 2, JSON.stringify(ph));
    ok("预设单张：描述算 generic 级",
        ph.filter((f) => f.kind === "generic").length === 3, JSON.stringify(ph));
    const fixed = { ...tb, items: tb.items.map((it, i) => ({ ...it,
        caption: "雪原上的旗号", text: it.text ? "永兵传说" : it.text })) };
    ok("改完后不再报占位", findPlaceholders(fixed).length === 0,
        JSON.stringify(findPlaceholders(fixed)));
    ok("手写描述不会被误报",
        findPlaceholders({ items: [{ id: "a", caption: "Panel 3 号选手起身" }] }).length === 0);
    const gsPh = { version: 3, preset: "grid", style: "", grid: GRID,
        items: presetItems("grid", { cols: 2, rows: 1, lang: "zh" }) };
    ok("预设宫格每格都是占位",
        findPlaceholders(gsPh).length === 2
        && findPlaceholders(gsPh).every((f) => f.kind === "generic" && f.field === "caption"));
    ok("宫格模式不拦 text 占位（它根本不进提示词）",
        findPlaceholders({ ...gsPh, items: gsPh.items.map((x) => ({ ...x, label: "text", text: "标题文字" })) })
            .every((f) => f.field === "caption"),
        JSON.stringify(findPlaceholders({ ...gsPh, items: gsPh.items.map((x) => ({ ...x, label: "text", text: "标题文字" })) })));

    ok("干净宫格没有忽略字段",
        findIgnoredGridFields({ ...gsPh, items: gsPh.items.map((x) => ({ ...x, label: "object" })) }).length === 0);
    const dirty = { ...gsPh, items: [{ id: "s1", label: "character", caption: "c", no: 1, size: "中景",
        text: "字", palette: ["#c8a15a"], bbox: [0, 0, .5, 1] },
        { id: "s2", label: "object", caption: "c", no: 2, size: "近景", text: "",
        palette: [], bbox: [.5, 0, 1, 1] }] };
    const ign = findIgnoredGridFields(dirty);
    ok("宫格忽略字段逐条点名",
        ign.length === 1 && ign[0].id === "s1" && ign[0].fields.join(",") === "label,text,palette",
        JSON.stringify(ign));
    ok("空 text / 空 palette 不算忽略字段",
        findIgnoredGridFields({ preset: "grid", items: [{ id: "s1", label: "object", text: "", palette: [] }] }).length === 0);
    ok("单张模式不报忽略字段", findIgnoredGridFields(tb).length === 0);
}

const strBad = Object.entries(STR).filter(([k, v]) => !v || typeof v.zh !== "string" || !v.zh
    || typeof v.en !== "string" || !v.en || /[\u4e00-\u9fff]/.test(v.en));
ok("界面文案每条都有中英且英文无汉字", strBad.length === 0,
    strBad.map(([k]) => k).join(","));
const strPh = Object.entries(STR).filter(([, v]) => /{[a-z]+}/.test(v.zh) !== /{[a-z]+}/.test(v.en));
ok("占位符 {n}/{ids} 中英成对", strPh.length === 0, strPh.map(([k]) => k).join(","));

const enErrors = validateState({ version: 2, preset: "free", style: "", canvas: { width: 1536, height: 864 }, items: [] }, "en").errors.join(" | ");
ok("英文校验文案无 CJK", enErrors.includes("version must be") && !/[\u4e00-\u9fff]/.test(enErrors), enErrors);
const enFatal = readState("{}", "en").fatal || "";
ok("英文 fatal 文案无 CJK", enFatal.includes("version") && !/[\u4e00-\u9fff]/.test(enFatal), enFatal);

// --- elements-v1 互操作（与 core/interop.py 同一套规则） ---
const edoc = {
    version: 1,
    canvas: { width: 1024, height: 1024 },
    style: "胶片",
    elements: [
        { role: "text", desc: "标题", text: "字", bbox: [80, 100, 920, 190], palette: ["#c8a15a"] },
        { type: "obj", desc: "主体", bbox: [0, 0, 1000, 1000], weight: 2, z: 0 },
    ],
};
const est = elementsToState(edoc, { lang: "zh" });
ok("interop role 与别名", est.items[0].label === "text" && est.items[1].label === "object",
    JSON.stringify(est.items.map((x) => x.label)));
ok("interop bbox 0–1000 → 0–1", est.items[0].bbox.join(",") === "0.08,0.1,0.92,0.19",
    est.items[0].bbox.join(","));
ok("interop 自动 id", est.items[0].id === "t1" && est.items[1].id === "o1",
    est.items.map((x) => x.id).join(","));
ok("interop 未知字段进 x_ext", est.items[1].x_ext && est.items[1].x_ext.weight === 2,
    JSON.stringify(est.items[1].x_ext));
ok("interop 生成 state 合法", validateState(est).errors.length === 0,
    JSON.stringify(validateState(est).errors));
const eback = stateToElements(est, "zh");
ok("interop 导出往返", eback.elements[1].bbox.join(",") === "0,0,1000,1000"
    && eback.elements[0].text === "字" && eback.style === "胶片", JSON.stringify(eback.elements));
ok("interop grid 拒绝导入", (() => {
    try { elementsToState({ version: 1, elements: [] }, { preset: "grid" }); return false; }
    catch (e) { return /grid/.test(e.message); }
})());
ok("interop 非 text 带 text 拒绝", (() => {
    try {
        elementsToState({ version: 1, elements: [{ role: "object", desc: "d", text: "x", bbox: [0, 0, 10, 10] }] });
        return false;
    } catch (e) { return /text/.test(e.message); }
})());
ok("interop sniff", sniffLayoutDoc(edoc) === "elements" && sniffLayoutDoc(emptyState()) === "state"
    && sniffLayoutDoc({}) === null);
ok("interop 版本常量", ELEMENTS_VERSION === 1);
const protoDoc = JSON.parse('{"version":1,"elements":[{"role":"object","desc":"d","bbox":[0,0,10,10],"x_ext":{"__proto__":{"polluted":1},"ok":2}}],"x_ext":{"__proto__":{"p":1},"keep":3}}');
const pst = elementsToState(protoDoc, { lang: "zh" });
ok("interop 危险键丢弃", pst.items[0].x_ext.ok === 2 && pst.x_ext.keep === 3
    && !Object.prototype.hasOwnProperty.call(pst.items[0].x_ext, "__proto__")
    && ({}).polluted === undefined, JSON.stringify(pst.x_ext));
ok("interop 文档 x_ext=null 拒绝", (() => {
    try { elementsToState({ version: 1, elements: [], x_ext: null }); return false; }
    catch (e) { return /x_ext/.test(e.message); }
})());
const backProto = stateToElements(pst, "zh");
ok("interop 危险键不回流", !Object.prototype.hasOwnProperty.call(backProto, "__proto__")
    && ({}).polluted === undefined);
ok("interop role 原型链名字拒绝", (() => {
    try {
        elementsToState({ version: 1, elements: [{ role: "toString", desc: "d", bbox: [0, 0, 10, 10] }] });
        return false;
    } catch (e) { return /role/.test(e.message); }
})());
ok("interop 零宽 desc 拒绝", (() => {
    try {
        elementsToState({ version: 1, elements: [{ role: "object", desc: "\u200b", bbox: [0, 0, 10, 10] }] });
        return false;
    } catch (e) { return /desc/.test(e.message); }
})());
ok("interop 零宽 text 拒绝", (() => {
    try {
        elementsToState({ version: 1, elements: [{ role: "text", desc: "d", text: "\u202e", bbox: [0, 0, 10, 10] }] });
        return false;
    } catch (e) { return /text/.test(e.message); }
})());
const thinState = {
    version: 3, preset: "free", style: "", canvas: { width: 1536, height: 864 },
    items: [{ id: "o1", label: "object", caption: "d", bbox: [0.5, 0.5, 0.5000001, 0.5000001] }],
};
const thinExp = stateToElements(thinState, "zh");
ok("interop 超窄 bbox 导出补到 0.001", thinExp.elements[0].bbox.join(",") === "500,500,501,501",
    thinExp.elements[0].bbox.join(","));
ok("interop 超窄 bbox 可再导入", elementsToState(thinExp, { lang: "zh" }).items.length === 1);
const edgeState = {
    version: 3, preset: "free", style: "", canvas: { width: 1536, height: 864 },
    items: [{ id: "o1", label: "object", caption: "d", bbox: [0.9999999, 0.9999999, 0.99999995, 0.99999995] }],
};
const edgeExp = stateToElements(edgeState, "zh");
ok("interop 右/下边界超窄 bbox 不越界", edgeExp.elements[0].bbox.join(",") === "999,999,1000,1000",
    edgeExp.elements[0].bbox.join(","));

// --- 层级 / 对齐 ---
const BB = [0.2, 0.1, 0.6, 0.5];
ok("对齐 left", alignBbox(BB, "left").join(",") === "0,0.1,0.4,0.5", alignBbox(BB, "left").join(","));
ok("对齐 hcenter", alignBbox(BB, "hcenter").join(",") === "0.3,0.1,0.7,0.5",
    alignBbox(BB, "hcenter").join(","));
ok("对齐 right", alignBbox(BB, "right").join(",") === "0.6,0.1,1,0.5",
    alignBbox(BB, "right").join(","));
ok("对齐 top", alignBbox(BB, "top").join(",") === "0.2,0,0.6,0.4",
    alignBbox(BB, "top").join(","));
ok("对齐 vcenter", alignBbox(BB, "vcenter").join(",") === "0.2,0.3,0.6,0.7",
    alignBbox(BB, "vcenter").join(","));
ok("对齐 bottom", alignBbox(BB, "bottom").join(",") === "0.2,0.6,0.6,1",
    alignBbox(BB, "bottom").join(","));
ok("对齐保持尺寸", ["left", "hcenter", "right", "top", "vcenter", "bottom"].every((m) => {
    const b = alignBbox(BB, m);
    return Math.abs((b[2] - b[0]) - 0.4) < 1e-9 && Math.abs((b[3] - b[1]) - 0.4) < 1e-9;
}));
ok("未知对齐返回 null", alignBbox(BB, "diag") === null);
const ZARR = [{ id: "a" }, { id: "b" }, { id: "c" }];
ok("层级上移", moveItemInArray(ZARR, "b", -1).map((x) => x.id).join("") === "bac");
ok("层级下移", moveItemInArray(ZARR, "b", 1).map((x) => x.id).join("") === "acb");
ok("层级边界不动", moveItemInArray(ZARR, "a", -1) === ZARR && moveItemInArray(ZARR, "c", 1) === ZARR);
ok("层级找不到原样", moveItemInArray(ZARR, "z", 1) === ZARR);

// 撤销 / 重做（纯函数）：状态用字符串代表
{
    const hst = makeHistory();
    ok("撤销：空历史不动", stepHistory(hst, "A", -1) === null && stepHistory(hst, "A", 1) === null);
    recordHistory(hst, "A");          // A → B
    recordHistory(hst, "B");          // B → C
    ok("撤销：回到上一步", stepHistory(hst, "C", -1) === "B");
    ok("撤销：再回一步", stepHistory(hst, "B", -1) === "A");
    ok("重做：前进", stepHistory(hst, "A", 1) === "B");
    recordHistory(hst, "B");          // B → D：新改动清空重做
    ok("新改动清空重做栈", stepHistory(hst, "D", 1) === null && hst.redo.length === 0);

    const typing = makeHistory();
    recordHistory(typing, "s0", "cap:o1");
    recordHistory(typing, "s1", "cap:o1");
    recordHistory(typing, "s2", "cap:o1");
    ok("同一 key 连续改动合并为一步", typing.undo.length === 1 && stepHistory(typing, "s3", -1) === "s0",
       JSON.stringify(typing.undo));
    recordHistory(typing, "s0", "cap:o1");
    recordHistory(typing, "s1", "cap:o2");
    ok("换元素不合并", typing.undo.length === 2, JSON.stringify(typing.undo));
    recordHistory(typing, "s2");
    recordHistory(typing, "s3", "cap:o2");
    ok("中间夹无 key 改动后不合并", typing.undo.length === 4, JSON.stringify(typing.undo));
    stepHistory(typing, "s4", -1);
    recordHistory(typing, "s3", "cap:o2");
    ok("撤销后再输入不并进旧步", typing.undo.length === 4, JSON.stringify(typing.undo));

    const cap = makeHistory();
    for (let i = 0; i < HISTORY_MAX + 20; i += 1) recordHistory(cap, "x" + i);
    ok(`历史上限 ${HISTORY_MAX}，丢最旧`, cap.undo.length === HISTORY_MAX && cap.undo[0] === "x20", cap.undo[0]);

    const chunk = "x".repeat(1000000);
    const keep = Math.floor(HISTORY_MAX_CHARS / (chunk.length + 1));
    const budget = makeHistory();
    const pushes = keep + 3;
    for (let i = 0; i < pushes; i += 1) recordHistory(budget, chunk + i);
    ok(`历史字符预算 ${HISTORY_MAX_CHARS}`, budget.undo.length === keep
        && budget.undo[budget.undo.length - 1].endsWith(String(pushes - 1)),
    `${budget.undo.length} / ${keep}`);
    const redo = makeHistory();
    for (let i = 0; i < pushes; i += 1) recordHistory(redo, chunk + i);
    let cur = "current";
    for (let i = 0; i < HISTORY_MAX; i += 1) {
        const step = stepHistory(redo, cur, -1);
        if (step == null) break;
        cur = step;
    }
    ok(`重做栈也受字符预算 ${HISTORY_MAX_CHARS}`, redo.redo.length === keep, String(redo.redo.length));
}

console.log("__RESULT__" + JSON.stringify(results));
"""


def check_tables(node, contract_state):
    if not node:
        skip("T1-T9 词表与纯函数", "没有 node")
        return
    tmp = tempfile.mkdtemp(prefix="mosssb_editor_")
    try:
        os.makedirs(os.path.join(tmp, "extensions", "pack"))
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copyfile(EDITOR, os.path.join(tmp, "extensions", "pack", "moss_sb_editor.js"))
        with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as f:
            f.write('{"type":"module"}')
        with open(os.path.join(tmp, "scripts", "app.js"), "w", encoding="utf-8") as f:
            f.write("export const app = { registerExtension() {}, graph: null, canvas: null };\n")
        with open(os.path.join(tmp, "contract.json"), "w", encoding="utf-8") as f:
            json.dump(contract_state or {}, f, ensure_ascii=False)
        with open(os.path.join(tmp, "run.mjs"), "w", encoding="utf-8") as f:
            f.write(RUNNER)
        r = subprocess.run([node, "run.mjs"], cwd=tmp, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        line = ""
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("__RESULT__"):
                line = ln[len("__RESULT__"):]
        if not line:
            check("T1 纯函数可执行", False, ((r.stderr or "") + (r.stdout or "")).strip().splitlines()[:4])
            return
        rows = json.loads(line)
        for row in rows:
            check(row["name"], row["ok"], row.get("detail", ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 接线
# ---------------------------------------------------------------------------
def check_wiring(contract, src):
    node = contract.get("node")
    widget = contract.get("widget")
    presets = contract.get("presets") or []
    check("W1 契约里能解析出节点名", node == "MossLayoutEditor", node)
    check("W2 契约里能解析出 widget 名", widget == "editor_state", widget)
    check("W3 契约 preset 表是 8 个", len(presets) == 8, ",".join(presets))
    check("W4 JS 绑定节点名", f'const NODE_TYPE = "{node}"' in src, node)
    check("W5 JS 绑定 widget 名", f'const STATE_WIDGET = "{widget}"' in src, widget)
    m = re.search(r"export const PRESET_IDS = \[([^\]]+)\]", src)
    js_ids = re.findall(r'"([a-z-]+)"', m.group(1)) if m else []
    check("W6 JS preset 表与契约一致", js_ids == presets, f"{js_ids} vs {presets}")

    init = open(os.path.join(ROOT, "__init__.py"), encoding="utf-8").read()
    m = re.search(r'WEB_DIRECTORY\s*=\s*"([^"]+)"', init)
    web_dir = os.path.normpath(os.path.join(ROOT, m.group(1))) if m else ""
    check("W7 WEB_DIRECTORY 指向 web/ 且编辑器在其中", bool(m) and os.path.isfile(os.path.join(web_dir, "moss_sb_editor.js")), web_dir)
    check("W8 用 registerExtension 注册", "app.registerExtension({" in src)

    imports = list(re.findall(r'from\s+"(\.[^"]+)"', src))
    local = [p for p in imports if not p.startswith("../../scripts/")]
    check("W9 单文件：不 import 本地 module", not local, str(local))
    check("W10 不越权写 widgets_values", "widgets_values" not in src and "widgets.push" not in src)
    check("W11 只找 editor_state 这一个 widget", "w.name === STATE_WIDGET" in src)
    check("W12 有导入 / 导出接线", "ui.importBtn.addEventListener" in src
          and "ui.exportBtn.addEventListener" in src and 'fileIn.type = "file"' in src)
    check("W13 导入走 sniff + elementsToState", "sniffLayoutDoc(obj)" in src
          and "elementsToState(obj" in src)
    check("W14 有 Queue 接线且不改值", "ui.queueBtn.addEventListener" in src
          and "app.queuePrompt(0, 1)" in src and "widgets_values" not in src)
    # 滚轮：宿主在画布外层 transform pane 的 **capture** 阶段转发给画布（放大缩小），
    # 节点内的 bubble 拦截永远轮不到 ⇒ 必须在 window capture 抢在它前面。
    # 这两条缺任一，滚文本框都会变成缩放画布（2026-10-28 复发的那个 bug）。
    check("W15 滚轮守卫在 window capture（早于宿主 onWheelCapture）",
          re.search(r'addEventListener\("wheel",\s*onWheel,\s*\{\s*capture:\s*true', src) is not None
          and 'closest(".mossed-root")' in src)
    check("W16 根节点声明 data-capture-wheel（宿主自带判定）",
          'setAttribute("data-capture-wheel", "true")' in src
          and 'data-capture-wheel", "false"' not in src)
    # 跨平台/跨引擎：同一份 JS 要在 Chromium / Firefox / WebKit 上表现一致。
    # 系统级差异只有三种输入形态（鼠标滚轮、触控板、捏合），这里逐条钉住。
    check("W17 目标判定走 composedPath（shadow DOM 里 e.target 会被重定向到宿主）",
          "function inEditor(e)" in src and "e.composedPath()" in src)
    check("W18 只抢归属不接管滚动：命中可滚容器放手（不 preventDefault），没有才吃掉",
          "function claimWheel(e, target)" in src
          and re.search(r"if\s*\(!sc\)\s*\{\s*e\.preventDefault\(\);\s*return false;\s*\}", src) is not None)
    check("W19 不自己算 scrollTop（手算会丢原生惯性 / 行模式 / 小数 delta）",
          "scrollTop +=" not in src and "WHEEL_REST" not in src and "scrollableFrom" in src)
    check("W20 编辑器内拦 Safari 捏合 gesture*（Safari 捏合不发 wheel，不拦就整页缩放）",
          '"gesturechange"' in src and "root.addEventListener(ev, (e) => e.preventDefault()" in src)
    check("W21 overscroll-behavior:contain 覆盖 根 / 属性面板 / 提示词框（JS 失效时的兜底）",
          src.count("overscroll-behavior:contain") >= 3,
          "count=%d" % src.count("overscroll-behavior:contain"))

    # 旧工作流兼容：`widgets_values` 按位置存，参数个数变过一次就整体错位，
    # 界面上那几个下拉会变红而且不报错（2026-10-28 用户报的就是这个）。
    compat = open(os.path.join(ROOT, "web", "moss_sb_compat.js"), encoding="utf-8").read()
    order = open(os.path.join(ROOT, "web", "moss_sb_widget_order.js"), encoding="utf-8").read()
    check("W22 兼容层只给自己包的节点挂钩子（外来节点一个字节都不动）",
          "OWN.has(nodeData?.name)" in compat and "repairValues" in compat)
    check("W23 长度一致时绝不猜（单个非法值更可能是用户手改，不替人做主）",
          "delta === 0" in order and "return null" in order)
    check("W24 只有 serialize:false **且**是 DOM 组件才不占位（control_after_generate 要占位）",
          "w.options.serialize === false && w.element" in order)


def main():
    src = open(EDITOR, encoding="utf-8").read()
    contract = load_contract()
    node = find_node()

    print("== W 接线 ==")
    check_wiring(contract, src)
    print("\n== S 语法 ==")
    check_syntax(node)
    print("\n== T 词表 / 纯函数 ==")
    check_tables(node, contract.get("example"))

    print("\n结果：通过 %d / 失败 %d / 跳过 %d" % (len(PASS), len(FAIL), len(SKIP)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    if not node:
        print("  （没有 node：S/T 检查未执行）")
        return 2
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
