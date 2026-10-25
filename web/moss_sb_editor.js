// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// MOSS Layout Editor · state v3 的可视化编辑器（单文件，DOM + SVG，无框架）
//
// 唯一接口 = 节点上的 STRING widget `editor_state`（契约见 docs/10-contract.md）：
//   · 只读写这一个 widget；不 import Python、不自己算 width/height（几何归后端）
//   · bbox 一律归一化 0–1；显示尺寸与像素尺寸解耦
//   · widget 顺序一个字节都不动（铁律 1）；UI 本身是 `serialize:false` 的 DOM widget
//
// 结构（docs/08 §七 · 借鉴 ComfyUI-NewBie-LLM-Formatter 的 state-in-widget，MIT）：
//   grid 模式 = 只读宫格总览（格位由 cols/rows 算死）
//   single 模式 = 可画框 / 拖动 / 8 向缩放 + 吸附（框边、中线、三分线、16px 网格）
//
// 🔴 前端不编译 prompt。state 是编译层的输入；本文件只保证 state 合法、可编辑、可存档。

import { app } from "../../scripts/app.js";

const TAG = "[MOSS SB] editor";
const NODE_TYPE = "MossLayoutEditor";
const STATE_WIDGET = "editor_state";
export const STATE_VERSION = 3;

export const PRESET_IDS = ["grid", "split-v", "split-h", "title-body", "three-col", "center", "stack", "free"];
export const ITEM_LABELS = ["object", "character", "text"];
export const SIZES = ["大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写"];
/** 与 core.layout.ASPECTS 的键逐项一致。 */
export const ASPECT_IDS = ["16:9", "3:2", "4:3", "1:1", "4:5", "3:4", "2:3", "9:16", "21:9", "2.39:1"];
export const MAX_ITEMS = 256;
export const MAX_SIDE = 4096;
export const MAX_STATE_CHARS = 1000000;

export const DEFAULT_GRID = {
    cols: 4, rows: 2, panel_aspect: "16:9", panel_edge: 512, gutter: 8, margin: 8,
};
export const DEFAULT_CANVAS = { width: 1536, height: 864 };

const BOX_M = 0.03;

//: 预设 = 一组初始 bbox + 建议的元素类型（docs/09 §3.2）。
//: `stack` 刻意重叠（后画的在前）；其余预设互不重叠。
export const PRESET_SPECS = {
    grid: { mode: "grid", label: { zh: "宫格", en: "Grid" }, boxes: [] },
    "split-v": {
        mode: "single", label: { zh: "左右分栏", en: "Split vertical" },
        boxes: [
            { label: "object", caption: { zh: "主体", en: "Main subject" }, bbox: [BOX_M, BOX_M, 0.485, 1 - BOX_M] },
            {
                label: "text", caption: { zh: "粗体无衬线标题", en: "Bold sans-serif title" },
                text: { zh: "标题文字", en: "Title" }, palette: ["#c8a15a"],
                bbox: [0.515, BOX_M, 1 - BOX_M, 1 - BOX_M],
            },
        ],
    },
    "split-h": {
        mode: "single", label: { zh: "上下分栏", en: "Split horizontal" },
        boxes: [
            { label: "object", caption: { zh: "主体", en: "Main subject" }, bbox: [BOX_M, BOX_M, 1 - BOX_M, 0.485] },
            {
                label: "text", caption: { zh: "细体说明文字", en: "Light body text" },
                text: { zh: "正文文字", en: "Body text" }, palette: ["#ffffff"],
                bbox: [BOX_M, 0.515, 1 - BOX_M, 1 - BOX_M],
            },
        ],
    },
    "title-body": {
        mode: "single", label: { zh: "标题 + 主体 + 正文", en: "Title + subject + body" },
        boxes: [
            {
                label: "text", caption: { zh: "粗体无衬线标题，浅金色", en: "Bold sans-serif title, pale gold" },
                text: { zh: "标题文字", en: "Title" }, palette: ["#c8a15a"],
                // y0=0.05 实测会被裁；0.10 是保守值（是否完全安全未验证）
                bbox: [0.08, 0.10, 0.92, 0.19],
            },
            { label: "character", caption: { zh: "主角半身，侧对镜头", en: "Main character, half body, side profile" }, bbox: [0.10, 0.24, 0.90, 0.80] },
            {
                label: "text", caption: { zh: "细体小字，白色", en: "Small light text, white" },
                text: { zh: "副标题文字", en: "Subtitle" }, palette: ["#ffffff"],
                bbox: [0.08, 0.84, 0.92, 0.90],
            },
        ],
    },
    "three-col": {
        mode: "single", label: { zh: "三列", en: "Three columns" },
        boxes: [
            { label: "object", caption: { zh: "主体（左）", en: "Subject (left)" }, bbox: [BOX_M, BOX_M, 0.33, 1 - BOX_M] },
            { label: "object", caption: { zh: "主体（中）", en: "Subject (middle)" }, bbox: [0.35, BOX_M, 0.65, 1 - BOX_M] },
            { label: "object", caption: { zh: "主体（右）", en: "Subject (right)" }, bbox: [0.67, BOX_M, 1 - BOX_M, 1 - BOX_M] },
        ],
    },
    center: {
        mode: "single", label: { zh: "中心主体", en: "Centered subject" },
        boxes: [
            { label: "character", caption: { zh: "主体，居中，四周留白", en: "Main subject, centered, generous margins" }, bbox: [0.30, 0.18, 0.70, 0.82] },
        ],
    },
    stack: {
        mode: "single", label: { zh: "层叠", en: "Stacked" },
        boxes: [
            { label: "object", caption: { zh: "背景元素", en: "Background element" }, bbox: [0.05, 0.06, 0.95, 0.72] },
            { label: "character", caption: { zh: "前景主体", en: "Foreground subject" }, bbox: [0.28, 0.34, 0.72, 0.95] },
        ],
    },
    free: { mode: "single", label: { zh: "自由", en: "Free" }, boxes: [] },
};

const STR = {
    preset: { zh: "预设", en: "Preset" },
    apply: { zh: "生成初始框", en: "Generate boxes" },
    cols: { zh: "列", en: "Cols" },
    rows: { zh: "行", en: "Rows" },
    aspect: { zh: "单格比例", en: "Panel aspect" },
    width: { zh: "宽", en: "W" },
    height: { zh: "高", en: "H" },
    label: { zh: "类型", en: "Type" },
    caption: { zh: "描述", en: "Caption" },
    captionPh: { zh: "这个元素长什么样（不是要渲染的字）", en: "What it looks like (not the rendered text)" },
    text: { zh: "文字", en: "Text" },
    textPh: { zh: "实际要渲染的字（label=text 必填）", en: "The exact text to render (required for text)" },
    palette: { zh: "主色", en: "Palette" },
    palettePh: { zh: "#rrggbb，逗号分隔，最多 5 个", en: "#rrggbb, comma separated, up to 5" },
    bbox: { zh: "框（格内归一化）", en: "Box (normalized)" },
    size: { zh: "景别", en: "Shot size" },
    panelNo: { zh: "镜号", en: "Panel no." },
    del: { zh: "删除元素", en: "Delete item" },
    newItem: { zh: "主体", en: "Main subject" },
    fatal: { zh: "状态不可编辑", en: "State cannot be edited" },
    reset: { zh: "重置为空白", en: "Reset to blank" },
    resetAsk: { zh: "重置会丢掉当前 state，确定？", en: "This discards the current state. Continue?" },
    warnCaption: { zh: "caption 为空：编译时会报错", en: "caption is empty: the compiler will raise" },
    warnText: { zh: "text 为空：编译时会报错", en: "text is empty: the compiler will raise" },
    warnGrid: { zh: "items 数量与 cols×rows 不一致，点「生成初始框」修正", en: "items count != cols x rows; click Generate boxes" },
    warnPalette: { zh: "palette 只接受 #rrggbb，最多 5 个", en: "palette takes #rrggbb only, up to 5" },
    warnUnknownLabel: { zh: "label 不在 object/character/text 里", en: "label is not object/character/text" },
    welcomeTitle: { zh: "从一个预设开始", en: "Start from a preset" },
    welcomeSub: { zh: "选一个预设生成初始框，之后每个框都能拖动、缩放、改属性。", en: "Pick a preset to create boxes. Every box stays editable afterwards." },
    welcomeGrid: { zh: "宫格模式只读：改行列数后点「生成初始框」。", en: "Grid mode is read-only. Set columns/rows, then hit Generate boxes." },
    duplicate: { zh: "复制元素", en: "Duplicate" },
    center: { zh: "居中", en: "Center" },
    help: { zh: "帮助", en: "Help" },
    undo: { zh: "撤销", en: "Undo" },
    redo: { zh: "重做", en: "Redo" },
    moveUp: { zh: "上移", en: "Raise" },
    moveDown: { zh: "下移", en: "Lower" },
    alignLeft: { zh: "对齐左边缘", en: "Align left" },
    alignH: { zh: "水平居中", en: "Center horizontally" },
    alignRight: { zh: "对齐右边缘", en: "Align right" },
    alignTop: { zh: "对齐上边缘", en: "Align top" },
    alignV: { zh: "垂直居中", en: "Center vertically" },
    alignBottom: { zh: "对齐下边缘", en: "Align bottom" },
    order: { zh: "层级 / 对齐", en: "Order / align" },
    importBtn: { zh: "导入", en: "Import" },
    exportBtn: { zh: "导出", en: "Export" },
    importTitle: { zh: "导入 elements-v1 或 state v3 JSON", en: "Import elements-v1 or state v3 JSON" },
    exportTitle: { zh: "导出 elements-v1 JSON（单张模式）", en: "Export elements-v1 JSON (single-canvas mode)" },
    importConfirm: { zh: "导入会替换当前 {n} 个元素，确定？", en: "Import replaces the current {n} items. Continue?" },
    importOk: { zh: "已导入 {n} 个元素", en: "Imported {n} items" },
    importFail: { zh: "导入失败：", en: "Import failed: " },
    importTooBig: { zh: "文件超过 1,000,000 字符", en: "File exceeds 1,000,000 characters" },
    importUnknown: { zh: "既不是 elements-v1 也不是 state v3", en: "Neither elements-v1 nor state v3" },
    exportOk: { zh: "已导出 elements JSON", en: "Exported elements JSON" },
    exportFail: { zh: "导出失败：", en: "Export failed: " },
    exportGrid: { zh: "宫格没有元素，不能导出 elements", en: "Grid mode has no elements to export" },
    exportNoState: { zh: "没有可导出的 state", en: "No state to export" },
    queueBtn: { zh: "排队", en: "Queue" },
    queueTitle: { zh: "按当前工作流排队（不改任何值）", en: "Queue the current workflow (changes nothing)" },
    queueOk: { zh: "已加入队列", en: "Queued" },
    queueFail: { zh: "排队失败：", en: "Queue failed: " },
    queueNoState: { zh: "没有可排队的 state", en: "No state to queue" },
    helpTitle: { zh: "操作提示", en: "Controls" },
    helpKeys: {
        zh: "点选：单击框　拖动：按住框　画框：在空白处拖\n缩放：八个把手　删除：Delete / Backspace　微调：方向键（Shift ×10）\n复制：Ctrl/⌘ + D　取消选择：Esc\n撤销：Ctrl/⌘ + Z　重做：Ctrl/⌘ + Shift + Z（或 Ctrl + Y）\n导入 / 导出：工具栏按钮（elements-v1 JSON）",
        en: "Select: click a box · Move: drag it · Draw: drag on empty canvas\nResize: 8 handles · Delete: Delete/Backspace · Nudge: arrow keys (Shift x10)\nDuplicate: Ctrl/Cmd + D · Deselect: Esc\nUndo: Ctrl/Cmd + Z · Redo: Ctrl/Cmd + Shift + Z (or Ctrl + Y)\nImport / Export: toolbar buttons (elements-v1 JSON)",
    },
    helpFields: {
        zh: "描述 = 元素长什么样；文字 = 实际渲染的字。二者不能混用。",
        en: "Caption = what it looks like; Text = the exact glyphs to render. Do not mix them.",
    },
    advanced: { zh: "高级", en: "Advanced" },
    edge: { zh: "单格长边", en: "Panel edge" },
    gutter: { zh: "缝宽", en: "Gutter" },
    margin: { zh: "外边距", en: "Margin" },
    advancedNote: {
        zh: "预览只画格位，不画缝宽/外边距；真实几何由节点计算。",
        en: "The preview shows cell regions only; gutters and margins are computed by the node.",
    },
    canvasPreset: { zh: "常用尺寸", en: "Canvas preset" },
    custom: { zh: "自定义", en: "Custom" },
    confirmApply: { zh: "用当前预设替换这 {n} 个元素？", en: "Replace the current {n} items with this preset?" },
    emptySelection: { zh: "未选中元素。", en: "No item selected." },
};

/** 预设的一句话用途（帮助开源用户选对起点）。 */
export const PRESET_HINTS = {
    grid: { zh: "N 个等大格：故事板、三视图、动作拆解。", en: "N equal panels: storyboards, turnarounds, action breakdowns." },
    "split-v": { zh: "左主体 / 右文案：产品图、封面。", en: "Subject left, copy right: product shots and covers." },
    "split-h": { zh: "上主体 / 下文案：海报、上图下文。", en: "Subject above, copy below: posters and article art." },
    "title-body": { zh: "标题 / 主体 / 正文：海报、封面、信息图。", en: "Title, subject, body: posters, covers, infographics." },
    "three-col": { zh: "三等分竖列：多角度、并列对比。", en: "Three equal columns: multiple angles or side-by-side comparison." },
    center: { zh: "中心主体 + 四周留白：单幅插画、概念图。", en: "Centered subject with breathing room: illustration and concept art." },
    stack: { zh: "前景压背景：层叠场景、人物加环境。", en: "Foreground over background: layered scenes." },
    free: { zh: "空画布：自己画框。", en: "Blank canvas: draw every box yourself." },
};

/** 元素类型的颜色（画布与属性面板共用，便于一眼区分）。 */
export const TYPE_COLORS = { object: "#5aa9ff", character: "#5fd39a", text: "#ffc857" };

const ALIGN_LABELS = {
    left: "alignLeft", hcenter: "alignH", right: "alignRight",
    top: "alignTop", vcenter: "alignV", bottom: "alignBottom",
};

/** 常用画布尺寸（16 的倍数；用户仍可手改宽高）。 */
export const CANVAS_PRESETS = [
    { id: "16:9", label: "16:9 · 1536×864", width: 1536, height: 864 },
    { id: "9:16", label: "9:16 · 864×1536", width: 864, height: 1536 },
    { id: "1:1", label: "1:1 · 1024×1024", width: 1024, height: 1024 },
    { id: "4:3", label: "4:3 · 1152×864", width: 1152, height: 864 },
    { id: "3:4", label: "3:4 · 864×1152", width: 864, height: 1152 },
];

const LABEL_ZH = { object: "物体", character: "人物", text: "文字" };
const LABEL_EN = { object: "Object", character: "Character", text: "Text" };
const SIZE_EN = {
    大远景: "Extreme wide", 远景: "Wide", 全景: "Full", 中景: "Medium",
    中近景: "Medium close", 近景: "Close", 特写: "Tight", 大特写: "Extreme close",
};

function uiLang() {
    try {
        const v = globalThis.localStorage?.getItem("mosssb_lang");
        if (v === "zh" || v === "en") return v;
    } catch (e) { /* 隐私模式 */ }
    try {
        return /^zh/i.test(document.documentElement?.lang || "") ? "zh" : "en";
    } catch (e) { return "en"; }
}

const t = (k) => (STR[k]?.[uiLang()] ?? k);
const pick = (v, lang) => (v && typeof v === "object" ? (v[lang] ?? v.zh ?? v.en) : v);
const labelText = (v) => (uiLang() === "zh" ? LABEL_ZH[v] : LABEL_EN[v]) || String(v);
const sizeText = (v) => (uiLang() === "zh" ? v : (SIZE_EN[v] || v));

// ---------------------------------------------------------------------------
// 纯函数层（可被 tools/check_editor_ui.py 直接 import）
// ---------------------------------------------------------------------------

export function emptyState() {
    return {
        version: STATE_VERSION,
        preset: "free",
        style: "",
        canvas: { ...DEFAULT_CANVAS },
        items: [],
    };
}

export function makeItemId(label, items, taken) {
    const prefix = label === "text" ? "t" : label === "character" ? "c" : "o";
    const used = taken || new Set((items || []).map((it) => it && it.id).filter(Boolean));
    for (let n = 1; ; n += 1) {
        const id = prefix + n;
        if (!used.has(id)) { used.add(id); return id; }
    }
}

/** 预设 → 初始 items。grid 需要 opt.cols / opt.rows；其余预设忽略。 */
export function presetItems(preset, opt = {}) {
    const spec = PRESET_SPECS[preset];
    if (!spec) throw new Error("unknown preset: " + preset);
    const lang = opt.lang === "en" ? "en" : "zh";
    const items = [];
    if (preset === "grid") {
        const cols = Math.max(1, Math.floor(opt.cols || DEFAULT_GRID.cols));
        const rows = Math.max(1, Math.floor(opt.rows || DEFAULT_GRID.rows));
        const n = cols * rows;
        for (let i = 0; i < n; i += 1) {
            const c = i % cols;
            const r = Math.floor(i / cols);
            const k = n > 1 ? Math.round((i * (SIZES.length - 1)) / (n - 1)) : 3;
            items.push({
                id: `s${i + 1}`,
                label: "object",
                no: i + 1,
                size: SIZES[k],
                caption: lang === "zh" ? `第 ${i + 1} 格画面` : `Panel ${i + 1}`,
                bbox: [c / cols, r / rows, (c + 1) / cols, (r + 1) / rows],
            });
        }
        return items;
    }
    const used = new Set();
    for (const b of spec.boxes) {
        const it = {
            id: makeItemId(b.label, [], used),
            label: b.label,
            caption: pick(b.caption, lang),
            bbox: b.bbox.slice(),
        };
        if (b.label === "text") it.text = pick(b.text, lang) || "";
        if (b.palette) it.palette = b.palette.slice();
        items.push(it);
    }
    return items;
}

export const HISTORY_MAX = 100;

export function makeHistory() {
    return { undo: [], redo: [], key: null };
}

/** 记一步：prev = 改动前的序列化 state。同一 key 的连续改动（打字、连按方向键）合并成一步。 */
export function recordHistory(hist, prev, key = null) {
    if (key && hist.key === key) return;
    hist.undo.push(prev);
    if (hist.undo.length > HISTORY_MAX) hist.undo.shift();
    hist.redo.length = 0;
    hist.key = key;
}

/** dir=-1 撤销 / +1 重做；返回要恢复的序列化 state，没有可走的步返回 null。 */
export function stepHistory(hist, current, dir) {
    const from = dir < 0 ? hist.undo : hist.redo;
    const to = dir < 0 ? hist.redo : hist.undo;
    if (!from.length) return null;
    to.push(current);
    hist.key = null;
    return from.pop();
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isHex = (v) => typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v);
const isInt = (v) => Number.isInteger(v);

/** 严格校验（契约 §2.1）。返回错误列表；空数组 = 合法。 */
export function validateState(obj, lang = "zh") {
    const errors = [];
    const en = lang === "en";
    const E = (zh, enText) => (en ? enText : zh);
    const add = (m) => errors.push(m);
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
        add(E("state 必须是 JSON 对象", "state must be a JSON object"));
        return { errors };
    }
    if (obj.version !== STATE_VERSION) {
        add(E(`version 必须是 ${STATE_VERSION}（收到 ${obj.version}）`,
            `version must be ${STATE_VERSION} (got ${obj.version})`));
    }
    if (!PRESET_IDS.includes(obj.preset)) add(E(`preset 非法：${obj.preset}`, `invalid preset: ${obj.preset}`));
    if (typeof obj.style !== "string") add(E("style 必须是字符串", "style must be a string"));
    const items = obj.items;
    if (!Array.isArray(items)) {
        add(E("items 必须是数组", "items must be an array"));
        return { errors };
    }
    if (items.length > MAX_ITEMS) {
        add(E(`items 最多 ${MAX_ITEMS} 个（收到 ${items.length}）`, `at most ${MAX_ITEMS} items (got ${items.length})`));
    }
    if (obj.preset === "grid") {
        const g = obj.grid;
        if (!g || typeof g !== "object") add(E("grid 模式缺 grid 对象", "grid mode requires a grid object"));
        else {
            if (!isInt(g.cols) || g.cols < 1 || g.cols > 12) add(E("grid.cols 必须是 1..12 的整数", "grid.cols must be an integer in 1..12"));
            if (!isInt(g.rows) || g.rows < 1 || g.rows > 12) add(E("grid.rows 必须是 1..12 的整数", "grid.rows must be an integer in 1..12"));
            // 1×1 且无 items = 空白宫格占位（与 Python parse_state 同口径）
            const blank = !items.length && g.cols === 1 && g.rows === 1;
            if (!blank && isInt(g.cols) && isInt(g.rows) && g.cols * g.rows !== items.length) {
                add(E(`grid 的 cols×rows=${g.cols * g.rows} 与 items 数量 ${items.length} 不一致`,
                    `grid cols x rows = ${g.cols * g.rows} does not match ${items.length} items`));
            }
            if (!ASPECT_IDS.includes(g.panel_aspect)) {
                add(E(`grid.panel_aspect 非法：${g.panel_aspect}（可选 ${ASPECT_IDS.join(" / ")}）`,
                    `invalid grid.panel_aspect: ${g.panel_aspect}`));
            }
            if (!isInt(g.panel_edge) || g.panel_edge < 64 || g.panel_edge > MAX_SIDE || g.panel_edge % 16 !== 0) {
                add(E(`grid.panel_edge 必须是 64..${MAX_SIDE} 且为 16 的倍数`,
                    `grid.panel_edge must be 64..${MAX_SIDE} and a multiple of 16`));
            }
            if (!isInt(g.gutter) || g.gutter < 0 || g.gutter > 256) add(E("grid.gutter 必须是 0..256 的整数", "grid.gutter must be an integer in 0..256"));
            if (!isInt(g.margin) || g.margin < 0 || g.margin > 256) add(E("grid.margin 必须是 0..256 的整数", "grid.margin must be an integer in 0..256"));
        }
    } else {
        const c = obj.canvas;
        if (!c || typeof c !== "object") add(E("single 模式缺 canvas 对象", "single mode requires a canvas object"));
        else {
            for (const k of ["width", "height"]) {
                if (!isInt(c[k]) || c[k] < 64 || c[k] > MAX_SIDE || c[k] % 16 !== 0) {
                    add(E(`canvas.${k} 必须是 64..${MAX_SIDE} 且为 16 的倍数`,
                        `canvas.${k} must be 64..${MAX_SIDE} and a multiple of 16`));
                }
            }
        }
    }
    const ids = new Set();
    const nos = new Set();
    items.forEach((it, i) => {
        const w = `items[${i}]`;
        if (!it || typeof it !== "object" || Array.isArray(it)) {
            add(E(`${w} 必须是对象`, `${w} must be an object`));
            return;
        }
        if (typeof it.id !== "string" || !it.id) add(E(`${w}.id 必须是非空字符串`, `${w}.id must be a non-empty string`));
        else if (ids.has(it.id)) add(E(`${w}.id 重复：${it.id}`, `${w}.id is duplicated: ${it.id}`));
        else ids.add(it.id);
        if (!ITEM_LABELS.includes(it.label)) add(E(`${w}.label 非法：${it.label}`, `${w}.label is invalid: ${it.label}`));
        if (typeof it.caption !== "string" || !it.caption.trim()) add(E(`${w}.caption 不能为空`, `${w}.caption must not be empty`));
        if (it.label === "text" && (typeof it.text !== "string" || !it.text.trim())) {
            add(E(`${w}.text 必填（label=text），且不能拿 caption 兜底`,
                `${w}.text is required when label=text (caption is not a fallback)`));
        }
        if (it.palette !== undefined) {
            if (!Array.isArray(it.palette) || it.palette.length > 5 || it.palette.some((c) => !isHex(c))) {
                add(E(`${w}.palette 必须是 ≤5 个 #rrggbb`, `${w}.palette must be at most 5 #rrggbb values`));
            }
        }
        const b = it.bbox;
        if (!Array.isArray(b) || b.length !== 4 || b.some((v) => !isNum(v))) {
            add(E(`${w}.bbox 必须是 4 个数 [x0,y0,x1,y1]`, `${w}.bbox must be 4 numbers [x0,y0,x1,y1]`));
        } else if (!(b[0] >= 0 && b[0] < b[2] && b[2] <= 1 && b[1] >= 0 && b[1] < b[3] && b[3] <= 1)) {
            add(E(`${w}.bbox 越界或反向（需要 0 ≤ x0 < x1 ≤ 1、0 ≤ y0 < y1 ≤ 1）`,
                `${w}.bbox is out of range or inverted (0 <= x0 < x1 <= 1, 0 <= y0 < y1 <= 1)`));
        }
        if (obj.preset === "grid") {
            if (!isInt(it.no)) add(E(`${w}.no 必须是整数`, `${w}.no must be an integer`));
            else if (nos.has(it.no)) add(E(`${w}.no 重复：${it.no}`, `${w}.no is duplicated: ${it.no}`));
            else nos.add(it.no);
            if (!SIZES.includes(it.size)) add(E(`${w}.size 非法：${it.size}`, `${w}.size is invalid: ${it.size}`));
        }
    });
    return { errors };
}

/**
 * 宽容读取：能修的自动修（补 id / 补 grid / canvas 默认值），修不了的降级为警告。
 * `fatal` 只用于"继续编辑会破坏用户数据"的情形（不是 JSON / 版本不对 / preset 不认识）。
 */
export function readState(raw, lang = "zh") {
    const en = lang === "en";
    const E = (zh, enText) => (en ? enText : zh);
    if (raw == null || String(raw).trim() === "") {
        return { state: emptyState(), fixed: true, warnings: [] };
    }
    if (String(raw).length > MAX_STATE_CHARS) {
        return { fatal: E(`state 超过 ${MAX_STATE_CHARS} 字符上限`, `state exceeds ${MAX_STATE_CHARS} characters`) };
    }
    let obj;
    try {
        obj = JSON.parse(String(raw));
    } catch (e) {
        return { fatal: E("state 不是合法 JSON：" + e.message, "state is not valid JSON: " + e.message) };
    }
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
        return { fatal: E("state 必须是 JSON 对象", "state must be a JSON object") };
    }
    if (obj.version !== STATE_VERSION) {
        return {
            fatal: E(`state version=${obj.version}，本编辑器只认 ${STATE_VERSION}（旧工作流需要重存）`,
                `state version=${obj.version}; this editor accepts ${STATE_VERSION} only (re-save older workflows)`),
        };
    }
    if (!PRESET_IDS.includes(obj.preset)) {
        return { fatal: E(`未知 preset：${obj.preset}`, `unknown preset: ${obj.preset}`) };
    }
    const st = { ...obj };
    let fixed = false;
    if (typeof st.style !== "string") { st.style = ""; fixed = true; }
    if (!Array.isArray(st.items)) { st.items = []; fixed = true; }
    if (st.preset === "grid") {
        const g = (st.grid && typeof st.grid === "object") ? { ...DEFAULT_GRID, ...st.grid } : { ...DEFAULT_GRID };
        if (JSON.stringify(g) !== JSON.stringify(st.grid)) { st.grid = g; fixed = true; }
    } else if (!st.canvas || !isInt(st.canvas.width) || !isInt(st.canvas.height)
        || st.canvas.width < 64 || st.canvas.height < 64
        || st.canvas.width > MAX_SIDE || st.canvas.height > MAX_SIDE
        || st.canvas.width % 16 !== 0 || st.canvas.height % 16 !== 0) {
        st.canvas = { ...DEFAULT_CANVAS };
        fixed = true;
    }
    const used = new Set();
    st.items = st.items.map((it) => {
        if (!it || typeof it !== "object" || Array.isArray(it)) return it;
        const copy = { ...it };
        if (typeof copy.id !== "string" || !copy.id || used.has(copy.id)) {
            copy.id = makeItemId(copy.label || "object", [], used);
            fixed = true;
        }
        used.add(copy.id);
        return copy;
    });
    const warnings = validateState(st, lang).errors;
    return { state: st, warnings, fixed };
}

/**
 * 单轴吸附：候选边 → 目标（框边 / 中线 / 三分线）；都没中再退 16px 网格。
 * 返回 {d, guide}：d 是要加的位移（归一化），guide 是画参考线的位置。
 */
export function snapAxis(values, targets, threshold, step) {
    let best = null;
    for (const v of values) {
        if (!isNum(v)) continue;
        for (const target of targets) {
            if (!isNum(target)) continue;
            const d = target - v;
            if (Math.abs(d) <= threshold && (!best || Math.abs(d) < Math.abs(best.d))) {
                best = { d, guide: target };
            }
        }
    }
    if (!best && step > 0) {
        for (const v of values) {
            if (!isNum(v)) continue;
            const g = Math.round(v / step) * step;
            const d = g - v;
            if (Math.abs(d) <= threshold && (!best || Math.abs(d) < Math.abs(best.d))) {
                best = { d, guide: g };
            }
        }
    }
    return best;
}

function commonTargets(items, excludeId) {
    const xs = [0, 1, 1 / 3, 0.5, 2 / 3];
    const ys = [0, 1, 1 / 3, 0.5, 2 / 3];
    for (const it of items) {
        if (!it || it.id === excludeId || !Array.isArray(it.bbox) || it.bbox.length !== 4) continue;
        xs.push(it.bbox[0], it.bbox[2]);
        ys.push(it.bbox[1], it.bbox[3]);
    }
    return { xs, ys };
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const round3 = (v) => Math.round(v * 1000) / 1000;

/** 对齐画布：mode = left / hcenter / right / top / vcenter / bottom；尺寸不变。 */
export function alignBbox(bbox, mode) {
    if (!Array.isArray(bbox) || bbox.length !== 4) return null;
    const [x0, y0, x1, y1] = bbox;
    const w = x1 - x0;
    const h = y1 - y0;
    let nx = x0;
    let ny = y0;
    if (mode === "left") nx = 0;
    else if (mode === "hcenter") nx = (1 - w) / 2;
    else if (mode === "right") nx = 1 - w;
    else if (mode === "top") ny = 0;
    else if (mode === "vcenter") ny = (1 - h) / 2;
    else if (mode === "bottom") ny = 1 - h;
    else return null;
    return [round3(nx), round3(ny), round3(nx + w), round3(ny + h)];
}

/** 数组顺序即 z 序：把 id 上移 / 下移一位；越界或找不到就原样返回。 */
export function moveItemInArray(items, id, dir) {
    if (!Array.isArray(items)) return items;
    const i = items.findIndex((it) => it && it.id === id);
    if (i < 0) return items;
    const j = i + (dir < 0 ? -1 : 1);
    if (j < 0 || j >= items.length) return items;
    const out = items.slice();
    const t = out[i];
    out[i] = out[j];
    out[j] = t;
    return out;
}

/** 画布宽高比（grid 模式由 cols/rows × 单格比例推出来，只用于显示）。 */
export function stageAspect(state) {
    if (!state) return 16 / 9;
    if (state.preset === "grid") {
        const g = state.grid || DEFAULT_GRID;
        const m = String(g.panel_aspect || "16:9").split(":");
        const ar = (Number(m[0]) || 16) / (Number(m[1]) || 9);
        const cols = Math.max(1, Math.floor(g.cols || 1));
        const rows = Math.max(1, Math.floor(g.rows || 1));
        return (cols * ar) / rows;
    }
    const c = state.canvas || DEFAULT_CANVAS;
    return (c.width || 16) / (c.height || 9);
}

// ---------------------------------------------------------------------------
// 互操作层（elements-v1 ⇄ state v3；与 core/interop.py 逐项对齐）
// ---------------------------------------------------------------------------
export const ELEMENTS_VERSION = 1;
export const ELEMENTS_UNITS = 1000;

const ROLE_ALIASES = {
    text: "text", object: "object", obj: "object", character: "character", person: "character",
};
const ELEMENT_KEYS = new Set(["id", "role", "type", "text", "desc", "caption", "bbox", "palette", "z", "x_ext"]);
const DOC_KEYS = new Set(["version", "canvas", "style", "elements", "x_ext"]);
const ID_PREFIX = { text: "t", object: "o", character: "c" };
const HEX_RE = /^#[0-9a-fA-F]{6}$/;
const UNSAFE_EXT_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const safeExtKey = (key) => !UNSAFE_EXT_KEYS.has(key);

const interopLang = (lang) => (lang === "en" ? "en" : "zh");
const hasKey = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

function nextInteropId(role, used) {
    const prefix = ID_PREFIX[role];
    for (let n = 1; ; n += 1) {
        const id = prefix + n;
        if (!used.has(id)) { used.add(id); return id; }
    }
}

function interopRole(el, where, E) {
    const raw = hasKey(el, "role") ? el.role : el.type;
    if (raw == null) {
        throw new Error(E(`${where} 缺 role（可选 text / object / character；也可用 type 字段）。`,
            `${where} is missing role (text / object / character; type is accepted as an alias)`));
    }
    if (hasKey(el, "role") && hasKey(el, "type") && el.role !== el.type) {
        throw new Error(E(`${where} 的 role=${el.role} 与 type=${el.type} 冲突，只留一个。`,
            `${where} has conflicting role=${el.role} and type=${el.type}; keep one`));
    }
    if (typeof raw !== "string" || !(raw in ROLE_ALIASES)) {
        throw new Error(E(`${where} 的 role=${raw} 不认识，可选：text、object、character（别名 obj、person）。`,
            `${where} has an unknown role ${raw}; use text, object or character (aliases: obj, person)`));
    }
    return ROLE_ALIASES[raw];
}

function interopExtras(el, where, E) {
    const out = {};
    if (hasKey(el, "x_ext")) {
        if (!el.x_ext || typeof el.x_ext !== "object" || Array.isArray(el.x_ext)) {
            throw new Error(E(`${where} 的 x_ext 必须是对象。`, `${where} x_ext must be an object`));
        }
        for (const [key, val] of Object.entries(el.x_ext)) {
            if (safeExtKey(key)) out[key] = val;
        }
    }
    for (const key of Object.keys(el)) {
        if (!ELEMENT_KEYS.has(key) && safeExtKey(key)) out[key] = el[key];
    }
    return out;
}

/**
 * elements-v1 文档 → state v3（单张模式）。与 `core.interop.to_state` 同一套规则：
 * bbox 0–1000、role 别名、id 生成、z 稳定排序、未知字段进 x_ext、最终过 validateState。
 */
export function elementsToState(doc, opts = {}) {
    const lang = interopLang(opts.lang);
    const E = (zh, en) => (lang === "en" ? en : zh);
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) {
        throw new Error(E("elements 文档必须是 JSON 对象。", "the elements document must be a JSON object"));
    }
    if (doc.version !== ELEMENTS_VERSION) {
        throw new Error(E(`elements 版本是 ${doc.version}，本版只认 ${ELEMENTS_VERSION}。`,
            `elements version is ${doc.version}; this build accepts ${ELEMENTS_VERSION} only`));
    }
    const preset = opts.preset || "free";
    if (preset === "grid") {
        throw new Error(E("grid 不支持导入：宫格的 item 是格位，不是元素。请用单张模式（free 等）。",
            "grid import is not supported: grid items are panels, not elements; use a single-canvas preset"));
    }
    if (!PRESET_IDS.includes(preset)) {
        throw new Error(E(`preset=${preset} 不认识。`, `unknown preset: ${preset}`));
    }
    if (!Array.isArray(doc.elements)) {
        throw new Error(E("文档缺 elements 数组。", "the document is missing the elements array"));
    }
    if (doc.elements.length > MAX_ITEMS) {
        throw new Error(E(`elements 有 ${doc.elements.length} 个，超过上限 ${MAX_ITEMS}。请拆成多张画布。`,
            `too many elements: ${doc.elements.length} (max ${MAX_ITEMS})`));
    }
    const canvas = doc.canvas == null ? DEFAULT_CANVAS : doc.canvas;
    if (!canvas || typeof canvas !== "object" || Array.isArray(canvas)) {
        throw new Error(E("canvas 必须是对象 {width,height}。", "canvas must be an object {width,height}"));
    }
    const style = opts.style != null ? opts.style : (doc.style == null ? "" : doc.style);
    if (typeof style !== "string") {
        throw new Error(E("style 必须是字符串。", "style must be a string"));
    }

    const used = new Set();
    const rows = doc.elements.map((el, i) => {
        const where = lang === "en" ? `element ${i + 1}` : `第 ${i + 1} 个元素`;
        if (!el || typeof el !== "object" || Array.isArray(el)) {
            throw new Error(E(`${where} 必须是对象。`, `${where} must be an object`));
        }
        const role = interopRole(el, where, E);

        let id;
        if (!hasKey(el, "id") || el.id == null) id = nextInteropId(role, used);
        else if (typeof el.id !== "string" || !el.id) {
            throw new Error(E(`${where} 的 id 必须是非空字符串。`, `${where} id must be a non-empty string`));
        } else if (used.has(el.id)) {
            throw new Error(E(`${where} 的 id=${el.id} 重复了；id 必须唯一。`, `${where} id ${el.id} is duplicated`));
        } else id = el.id;
        used.add(id);

        const desc = hasKey(el, "desc") ? el.desc : el.caption;
        if (typeof desc !== "string" || !desc.trim()) {
            throw new Error(E(`${where} 缺 desc（这个元素长什么样，必填，不能为空）。`,
                `${where} is missing desc (what it looks like; required)`));
        }

        const b = el.bbox;
        if (!Array.isArray(b) || b.length !== 4) {
            throw new Error(E(`${where} 的 bbox 必须是 4 个数 [x0,y0,x1,y1]（单位 0–${ELEMENTS_UNITS}）。`,
                `${where} bbox must be 4 numbers [x0,y0,x1,y1] in 0–${ELEMENTS_UNITS}`));
        }
        if (b.some((v) => typeof v !== "number" || !Number.isFinite(v))) {
            throw new Error(E(`${where} 的 bbox 必须全是数字，不收字符串/布尔。`,
                `${where} bbox must contain numbers only`));
        }
        const [x0, y0, x1, y1] = b;
        if (!(x0 >= 0 && x0 < x1 && x1 <= ELEMENTS_UNITS && y0 >= 0 && y0 < y1 && y1 <= ELEMENTS_UNITS)) {
            throw new Error(E(`${where} 的 bbox 越界或反向：需要 0 ≤ x0 < x1 ≤ ${ELEMENTS_UNITS}、0 ≤ y0 < y1 ≤ ${ELEMENTS_UNITS}；单位不是 0–1。`,
                `${where} bbox is out of range or inverted: need 0 <= x0 < x1 <= ${ELEMENTS_UNITS}, 0 <= y0 < y1 <= ${ELEMENTS_UNITS}; units are not 0–1`));
        }

        const item = {
            id, label: role, caption: desc,
            bbox: [x0 / ELEMENTS_UNITS, y0 / ELEMENTS_UNITS, x1 / ELEMENTS_UNITS, y1 / ELEMENTS_UNITS],
        };
        if (role === "text") {
            if (typeof el.text !== "string" || !el.text.trim()) {
                throw new Error(E(`${where} 是 text 元素，必须填 text（实际渲染的字；desc 不能当 text 用）。`,
                    `${where} is a text element and needs text (the exact glyphs; desc is not a fallback)`));
            }
            item.text = el.text;
        } else if (hasKey(el, "text")) {
            throw new Error(E(`${where} 的 role=${role} 不支持 text；要渲染文字请用 role=text。`,
                `${where} role=${role} does not support text; use role=text to render glyphs`));
        }
        if (hasKey(el, "palette")) {
            const pal = el.palette;
            if (!Array.isArray(pal) || pal.length > 5 || pal.some((c) => typeof c !== "string" || !HEX_RE.test(c))) {
                throw new Error(E(`${where} 的 palette 必须是 ≤5 个 #rrggbb。`,
                    `${where} palette must be at most 5 #rrggbb values`));
            }
            item.palette = pal.slice();
        }
        const ext = interopExtras(el, where, E);
        if (Object.keys(ext).length) item.x_ext = ext;

        const z = hasKey(el, "z") ? el.z : i;
        if (typeof z !== "number" || !Number.isFinite(z)) {
            throw new Error(E(`${where} 的 z 必须是数字。`, `${where} z must be a number`));
        }
        return { z, i, item };
    });

    rows.sort((a, b) => (a.z - b.z) || (a.i - b.i));
    const state = {
        version: STATE_VERSION,
        preset,
        style,
        canvas: { width: canvas.width, height: canvas.height },
        items: rows.map((r) => r.item),
    };
    if (hasKey(doc, "x_ext")) {
        if (!doc.x_ext || typeof doc.x_ext !== "object" || Array.isArray(doc.x_ext)) {
            throw new Error(E("文档级 x_ext 必须是对象。", "document-level x_ext must be an object"));
        }
        for (const [key, val] of Object.entries(doc.x_ext)) {
            if (!safeExtKey(key)) continue;
            if (!state.x_ext) state.x_ext = {};
            state.x_ext[key] = val;
        }
    }
    for (const key of Object.keys(doc)) {
        if (DOC_KEYS.has(key) || !safeExtKey(key)) continue;
        if (!state.x_ext) state.x_ext = {};
        state.x_ext[key] = doc[key];
    }
    const errors = validateState(state, lang).errors;
    if (errors.length) throw new Error(errors.join(" | "));
    return state;
}

/** state v3（单张模式）→ elements-v1 文档；bbox 量化到 0.001，element 顺序即 z 序。 */
export function stateToElements(state, lang = "zh") {
    const L = interopLang(lang);
    const E = (zh, en) => (L === "en" ? en : zh);
    if (!state || typeof state !== "object" || Array.isArray(state)) {
        throw new Error(E("state 必须是 JSON 对象。", "state must be a JSON object"));
    }
    if (state.preset === "grid") {
        throw new Error(E("宫格 state 不能导出成 elements：每格是格位，不是元素。",
            "a grid state cannot be exported as elements: every item is a panel, not an element"));
    }
    const errors = validateState(state, L).errors;
    if (errors.length) throw new Error(errors.join(" | "));
    const canvas = state.canvas || DEFAULT_CANVAS;
    const out = {
        version: ELEMENTS_VERSION,
        canvas: { width: canvas.width, height: canvas.height },
        style: state.style || "",
        elements: [],
    };
    for (const it of state.items || []) {
        const el = {
            id: it.id, role: it.label, desc: it.caption,
            bbox: (it.bbox || []).map((v) => Math.round(Number(v) * ELEMENTS_UNITS * 1000) / 1000),
        };
        if (it.label === "text") el.text = it.text;
        if (Array.isArray(it.palette) && it.palette.length) el.palette = it.palette.slice();
        if (it.x_ext && typeof it.x_ext === "object" && !Array.isArray(it.x_ext)) {
            const ext = {};
            for (const [key, val] of Object.entries(it.x_ext)) {
                if (safeExtKey(key)) ext[key] = val;
            }
            if (Object.keys(ext).length) el.x_ext = ext;
        }
        out.elements.push(el);
    }
    const ext = state.x_ext;
    if (ext && typeof ext === "object" && !Array.isArray(ext)) {
        for (const [key, val] of Object.entries(ext)) {
            if (!safeExtKey(key)) continue;
            if (!hasKey(out, key)) out[key] = val;
            else {
                if (!out.x_ext) out.x_ext = {};
                out.x_ext[key] = val;
            }
        }
    }
    return out;
}

export function sniffLayoutDoc(obj) {
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    if (hasKey(obj, "elements")) return "elements";
    if (obj.version === STATE_VERSION && hasKey(obj, "items")) return "state";
    return null;
}

// ---------------------------------------------------------------------------
// DOM / SVG 工具
// ---------------------------------------------------------------------------
const SVG_NS = "http://www.w3.org/2000/svg";
const STYLE_ID = "mosssb-editor-style";

function ensureStyle() {
    try {
        if (document.getElementById(STYLE_ID)) return;
        const st = document.createElement("style");
        st.id = STYLE_ID;
        st.textContent = `
.mossed-root{position:relative;display:flex;flex-direction:column;gap:7px;width:100%;height:100%;box-sizing:border-box;padding:8px;color:var(--input-text,#ddd);background:var(--comfy-menu-bg,#1e1e1e);border-radius:8px;overflow:hidden;font:12px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif}
.mossed-bar{display:flex;align-items:center;gap:6px;flex:0 0 auto;flex-wrap:wrap}
.mossed-row{display:flex;align-items:center;gap:10px;flex:0 0 auto;flex-wrap:wrap}
.mossed-field{display:flex;align-items:center;gap:4px;white-space:nowrap;color:var(--input-text,#ddd)}
.mossed-field-label{opacity:.7;font-size:11px}
.mossed-root input[type=number],.mossed-root input[type=text],.mossed-root select,.mossed-root textarea{background:var(--comfy-input-bg,#2a2a2a);color:var(--input-text,#ddd);border:1px solid var(--border-color,#4a4f59);border-radius:6px;padding:2px 5px;font:inherit;min-width:0;height:24px;box-sizing:border-box}
.mossed-root textarea{height:auto;padding:5px 6px;line-height:1.4}
.mossed-root input:disabled{opacity:.45}
.mossed-root select{cursor:pointer}
.mossed-root input:focus-visible,.mossed-root select:focus-visible,.mossed-root textarea:focus-visible,.mossed-root button:focus-visible{outline:2px solid #5aa9ff;outline-offset:1px}
.mossed-num{width:64px}
.mossed-bbox{width:62px;font-variant-numeric:tabular-nums}
.mossed-text,.mossed-palette{width:100%}
.mossed-cap{width:100%;resize:vertical;min-height:38px}
.mossed-btn{height:24px;border:1px solid var(--border-color,#4a4f59);background:var(--comfy-input-bg,#2a2a2a);color:var(--input-text,#ddd);border-radius:6px;padding:0 9px;cursor:pointer;font:inherit;white-space:nowrap}
.mossed-btn:hover{filter:brightness(1.22)}
.mossed-btn:disabled{opacity:.4;cursor:not-allowed;filter:none}
.mossed-primary{background:#f0b35a;border-color:#f0b35a;color:#15181d;font-weight:600}
.mossed-primary:hover{filter:brightness(1.08)}
.mossed-danger{border-color:#a33;color:#f88}
.mossed-iconbtn{width:24px;height:24px;border-radius:50%;border:1px solid var(--border-color,#4a4f59);background:var(--comfy-input-bg,#2a2a2a);color:var(--input-text,#ddd);cursor:pointer;font:600 13px/1 system-ui,sans-serif;padding:0}
.mossed-iconbtn:hover{filter:brightness(1.2)}
.mossed-iconbtn:disabled{opacity:.35;cursor:default;filter:none}
.mossed-alignbtn{width:22px;height:22px;border-radius:6px;font-size:13px}
.mossed-linkbtn{border:0;background:none;color:var(--input-text,#ddd);opacity:.65;cursor:pointer;font:inherit;text-decoration:underline;text-underline-offset:2px;padding:0}
.mossed-linkbtn:hover{opacity:1}
.mossed-spacer{flex:1 1 auto}
.mossed-chip{opacity:.68;font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}
.mossed-notice{font-size:11px;opacity:.78;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mossed-notice.mossed-err{color:#f88;opacity:.95}
.mossed-id{font:11px/1 ui-monospace,SFMono-Regular,Consolas,monospace;opacity:.72}
.mossed-typedot{width:10px;height:10px;border-radius:50%;display:inline-block;flex:0 0 auto;box-shadow:0 0 0 1px #0007,0 0 8px currentColor}
.mossed-hintline{flex:0 0 auto;font-size:11px;opacity:.62;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 2px;margin-top:-3px}
.mossed-note{font-size:10px;opacity:.6}
.mossed-stage{position:relative;flex:1 1 auto;min-height:170px;display:flex;align-items:center;justify-content:center;background-color:#0f1115;background-image:radial-gradient(circle at 1px 1px,#ffffff0d 1px,transparent 0);background-size:16px 16px;border:1px solid #2a2f38;border-radius:8px;overflow:hidden}
.mossed-svg{width:100%;height:100%;display:block;touch-action:none;user-select:none;cursor:crosshair}
.mossed-svg:focus{outline:none}
.mossed-svg.mossed-gridmode{cursor:default}
.mossed-canvas-bg{fill:#161a20;stroke:#4a5566;stroke-width:1;filter:drop-shadow(0 10px 24px #0009)}
.mossed-guide{stroke:#ffffff12;stroke-width:1}
.mossed-snapline{stroke:#ffd24a;stroke-width:1.5;stroke-dasharray:6 4;vector-effect:non-scaling-stroke}
.mossed-item{fill:var(--item-fill,#ffffff10);stroke:var(--type-color,#5aa9ff);stroke-width:1.5;vector-effect:non-scaling-stroke;cursor:move;transition:stroke-width .08s linear}
.mossed-cellitem{fill:#ffffff08;stroke:#ffffff55}
.mossed-itemg:hover .mossed-item{stroke-width:2.5;filter:brightness(1.15)}
.mossed-item.mossed-sel{stroke:#ffd24a;stroke-width:2.5;filter:drop-shadow(0 0 6px #ffd24a55)}
.mossed-cell{fill:#ffffff06;stroke:#ffffff30;vector-effect:non-scaling-stroke}
.mossed-itemlabel{fill:#eef2f7;font-size:18px;pointer-events:none;paint-order:stroke;stroke:#0b0d11aa;stroke-width:3}
.mossed-num{fill:#ffd24a;font-size:18px;font-weight:600;pointer-events:none}
.mossed-badge{fill:#ffd24a;opacity:.92}
.mossed-badge-text{fill:#15181d;font-size:13px;font-weight:700;text-anchor:middle;pointer-events:none}
.mossed-typedot{fill:var(--type-color,#5aa9ff);stroke:#0b0d11;stroke-width:1;pointer-events:none}
.mossed-draw{fill:#ffd24a14;stroke:#ffd24a;stroke-width:1.5;stroke-dasharray:6 4;vector-effect:non-scaling-stroke;pointer-events:none}
.mossed-handle{fill:#ffd24a;stroke:#111;stroke-width:1;vector-effect:non-scaling-stroke;rx:3}
.mossed-svg.mossed-dragging .mossed-handle{display:none}
.mossed-svg.mossed-dragging .mossed-itemlabel{opacity:.55}
.mossed-h-nw,.mossed-h-se{cursor:nwse-resize}
.mossed-h-ne,.mossed-h-sw{cursor:nesw-resize}
.mossed-h-n,.mossed-h-s{cursor:ns-resize}
.mossed-h-w,.mossed-h-e{cursor:ew-resize}
.mossed-welcome{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:min(540px,92%);box-sizing:border-box;padding:14px 16px;border-radius:12px;border:1px solid #3a4250;background:#171b22f2;box-shadow:0 18px 50px #000a;display:flex;flex-direction:column;gap:7px;text-align:center;z-index:2;pointer-events:none}
.mossed-welcome .mossed-chipbtn{pointer-events:auto}
.mossed-welcome[hidden]{display:none}
.mossed-welcome-title{font-size:14px;font-weight:600}
.mossed-welcome-sub{font-size:11px;opacity:.72;line-height:1.5}
.mossed-welcome-chips{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin-top:2px}
.mossed-chipbtn{border:1px solid #3a4250;background:#20252e;color:#e8edf5;border-radius:999px;padding:4px 11px;cursor:pointer;font:inherit;white-space:nowrap}
.mossed-chipbtn:hover{border-color:#f0b35a;color:#f0b35a}
.mossed-fatal{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:14px;background:#0b0d11e8;text-align:center;z-index:3}
.mossed-fatal[hidden]{display:none}
.mossed-fatal-title{font-weight:600;color:#f88}
.mossed-fatal-msg{font-size:11px;color:#ddd;max-height:50%;overflow:auto;word-break:break-word}
.mossed-help{position:absolute;right:10px;top:64px;width:min(380px,calc(100% - 20px));box-sizing:border-box;padding:12px 14px;border-radius:10px;border:1px solid #3a4250;background:#171b22f7;box-shadow:0 18px 50px #000b;z-index:6;display:flex;flex-direction:column;gap:7px}
.mossed-help[hidden]{display:none}
.mossed-help-title{font-weight:600;font-size:12px}
.mossed-help-keys{white-space:pre-line;font-size:11px;line-height:1.7;opacity:.9}
.mossed-help-note{font-size:11px;opacity:.65;border-top:1px solid #2a2f38;padding-top:6px}
.mossed-status{flex:0 0 auto;font-size:11px;opacity:.68;min-height:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
.mossed-status-warn{color:#f0b35a;opacity:1}
.mossed-prop{flex:0 0 auto;height:218px;max-height:218px;overflow:auto;display:flex;flex-direction:column;gap:6px;border-top:1px solid #2a2f38;padding-top:7px}
.mossed-empty{opacity:.6;font-size:11px;padding:6px 2px}
.mossed-empty[hidden]{display:none}
.mossed-form{display:flex;flex-direction:column;gap:6px}
.mossed-form[hidden],.mossed-row[hidden]{display:none}
.mossed-warn{color:#f0b35a;font-size:11px;min-height:13px}
.mossed-swatches{display:inline-flex;gap:3px}
.mossed-swatch{width:16px;height:16px;border-radius:4px;border:1px solid #0006;display:inline-block;box-shadow:0 0 0 1px #ffffff1a}
.mossed-boxrow{margin-top:2px}
`;
        (document.head || document.documentElement).appendChild(st);
    } catch (e) { /* 样式注入失败不影响功能 */ }
}

function h(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
}

function s(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
    return el;
}

function mountRoot() {
    const root = h("div", "mossed-root");
    // 编辑器吃掉指针/滚轮事件：否则拖框会拖动整个节点、滚文本会缩放画布
    for (const ev of ["pointerdown", "mousedown", "dblclick", "click", "keydown", "wheel"]) {
        root.addEventListener(ev, (e) => e.stopPropagation());
    }
    return root;
}

function field(labelText_, input) {
    const wrap = h("label", "mossed-field");
    wrap.appendChild(h("span", "mossed-field-label", labelText_));
    wrap.appendChild(input);
    return wrap;
}

function numInput(min, max, step, cls) {
    const inp = h("input", cls || "mossed-num");
    inp.type = "number";
    inp.min = String(min);
    inp.max = String(max);
    inp.step = String(step);
    return inp;
}

function buildUI() {
    ensureStyle();
    const root = mountRoot();

    // ---- 顶栏 ---------------------------------------------------------
    const bar = h("div", "mossed-bar");
    const presetSel = h("select", "mossed-preset");
    for (const id of PRESET_IDS) {
        const op = h("option", null, pick(PRESET_SPECS[id].label, uiLang()));
        op.value = id;
        presetSel.appendChild(op);
    }
    bar.appendChild(field(t("preset"), presetSel));
    const applyBtn = h("button", "mossed-btn mossed-primary", t("apply"));
    applyBtn.type = "button";
    applyBtn.title = t("apply");
    bar.appendChild(applyBtn);
    const importBtn = h("button", "mossed-btn", t("importBtn"));
    importBtn.type = "button";
    importBtn.title = t("importTitle");
    bar.appendChild(importBtn);
    const exportBtn = h("button", "mossed-btn", t("exportBtn"));
    exportBtn.type = "button";
    exportBtn.title = t("exportTitle");
    bar.appendChild(exportBtn);
    const queueBtn = h("button", "mossed-btn", t("queueBtn"));
    queueBtn.type = "button";
    queueBtn.title = t("queueTitle");
    bar.appendChild(queueBtn);
    const chip = h("span", "mossed-chip", "");
    bar.appendChild(chip);
    const notice = h("span", "mossed-notice", "");
    bar.appendChild(notice);
    const spacer = h("span", "mossed-spacer", "");
    bar.appendChild(spacer);
    const undoBtn = h("button", "mossed-iconbtn", "↶");
    undoBtn.type = "button";
    undoBtn.disabled = true;
    bar.appendChild(undoBtn);
    const redoBtn = h("button", "mossed-iconbtn", "↷");
    redoBtn.type = "button";
    redoBtn.disabled = true;
    bar.appendChild(redoBtn);
    const helpBtn = h("button", "mossed-iconbtn", "?");
    helpBtn.type = "button";
    helpBtn.title = t("help");
    helpBtn.setAttribute("aria-label", t("help"));
    bar.appendChild(helpBtn);
    root.appendChild(bar);

    const fileIn = document.createElement("input");
    fileIn.type = "file";
    fileIn.accept = ".json,application/json";
    fileIn.hidden = true;
    root.appendChild(fileIn);

    const presetHint = h("div", "mossed-hintline", "");
    root.appendChild(presetHint);

    // ---- 宫格参数 -----------------------------------------------------
    const gridRow = h("div", "mossed-row mossed-gridrow");
    const colsIn = numInput(1, 12, 1);
    const rowsIn = numInput(1, 12, 1);
    const aspectSel = h("select", "mossed-aspect");
    for (const a of ASPECT_IDS) {
        const op = h("option", null, a);
        op.value = a;
        aspectSel.appendChild(op);
    }
    const gridAdvBtn = h("button", "mossed-linkbtn", t("advanced"));
    gridAdvBtn.type = "button";
    gridRow.appendChild(field(t("cols"), colsIn));
    gridRow.appendChild(field(t("rows"), rowsIn));
    gridRow.appendChild(field(t("aspect"), aspectSel));
    gridRow.appendChild(gridAdvBtn);
    root.appendChild(gridRow);

    const gridAdvRow = h("div", "mossed-row mossed-gridadv");
    gridAdvRow.hidden = true;
    const edgeIn = numInput(64, 4096, 16);
    const gutterIn = numInput(0, 256, 1);
    const marginIn = numInput(0, 256, 1);
    gridAdvRow.appendChild(field(t("edge"), edgeIn));
    gridAdvRow.appendChild(field(t("gutter"), gutterIn));
    gridAdvRow.appendChild(field(t("margin"), marginIn));
    gridAdvRow.appendChild(h("span", "mossed-note", t("advancedNote")));
    root.appendChild(gridAdvRow);

    // ---- 画布参数（single） -------------------------------------------
    const canvasRow = h("div", "mossed-row mossed-canvasrow");
    const canvasPresetSel = h("select", "mossed-canvaspreset");
    for (const p of CANVAS_PRESETS) {
        const op = h("option", null, p.label);
        op.value = p.id;
        canvasPresetSel.appendChild(op);
    }
    const opCustom = h("option", null, t("custom"));
    opCustom.value = "custom";
    canvasPresetSel.appendChild(opCustom);
    const wIn = numInput(256, 4096, 16);
    const hIn = numInput(256, 4096, 16);
    canvasRow.appendChild(field(t("canvasPreset"), canvasPresetSel));
    canvasRow.appendChild(field(t("width"), wIn));
    canvasRow.appendChild(field(t("height"), hIn));
    const aspectChip = h("span", "mossed-chip", "");
    canvasRow.appendChild(aspectChip);
    root.appendChild(canvasRow);

    // ---- 画布 ---------------------------------------------------------
    const stage = h("div", "mossed-stage");
    const svg = s("svg", {
        class: "mossed-svg", viewBox: `0 0 1000 ${(1000 * 9) / 16}`,
        preserveAspectRatio: "xMidYMid meet", tabindex: "0", "aria-label": "Layout canvas",
    });
    stage.appendChild(svg);

    const welcome = h("div", "mossed-welcome");
    const welcomeTitle = h("div", "mossed-welcome-title", t("welcomeTitle"));
    const welcomeSub = h("div", "mossed-welcome-sub", t("welcomeSub"));
    const welcomeChips = h("div", "mossed-welcome-chips");
    for (const id of PRESET_IDS) {
        const b = h("button", "mossed-chipbtn", pick(PRESET_SPECS[id].label, uiLang()));
        b.type = "button";
        b.dataset.preset = id;
        b.title = pick(PRESET_HINTS[id] || {}, uiLang());
        welcomeChips.appendChild(b);
    }
    welcome.appendChild(welcomeTitle);
    welcome.appendChild(welcomeSub);
    welcome.appendChild(welcomeChips);
    stage.appendChild(welcome);

    const fatal = h("div", "mossed-fatal");
    fatal.hidden = true;
    stage.appendChild(fatal);
    root.appendChild(stage);

    const status = h("div", "mossed-status", "");
    status.setAttribute("aria-live", "polite");
    root.appendChild(status);

    // ---- 帮助浮层 -----------------------------------------------------
    const helpBox = h("div", "mossed-help");
    helpBox.hidden = true;
    helpBox.appendChild(h("div", "mossed-help-title", t("helpTitle")));
    const helpKeys = h("div", "mossed-help-keys", t("helpKeys"));
    helpBox.appendChild(helpKeys);
    helpBox.appendChild(h("div", "mossed-help-note", t("helpFields")));
    root.appendChild(helpBox);

    // ---- 属性面板 -----------------------------------------------------
    const panel = h("div", "mossed-prop");
    const empty = h("div", "mossed-empty", t("emptySelection"));
    panel.appendChild(empty);
    const form = h("div", "mossed-form");
    form.hidden = true;

    const row1 = h("div", "mossed-row");
    const typeDot = h("span", "mossed-typedot", "");
    const idChip = h("span", "mossed-id", "");
    const labelSel = h("select", "mossed-label");
    for (const v of ITEM_LABELS) {
        const op = h("option", null, labelText(v));
        op.value = v;
        labelSel.appendChild(op);
    }
    const dupBtn = h("button", "mossed-btn", t("duplicate"));
    dupBtn.type = "button";
    dupBtn.title = "Ctrl/Cmd + D";
    const delBtn = h("button", "mossed-btn mossed-danger", t("del"));
    delBtn.type = "button";
    row1.appendChild(typeDot);
    row1.appendChild(idChip);
    row1.appendChild(field(t("label"), labelSel));
    row1.appendChild(dupBtn);
    row1.appendChild(delBtn);
    form.appendChild(row1);

    const capTa = h("textarea", "mossed-cap");
    capTa.rows = 2;
    capTa.placeholder = t("captionPh");
    form.appendChild(field(t("caption"), capTa));

    const textRow = h("div", "mossed-row");
    const textIn = h("input", "mossed-text");
    textIn.type = "text";
    textIn.placeholder = t("textPh");
    textRow.appendChild(field(t("text"), textIn));
    form.appendChild(textRow);

    const paletteRow = h("div", "mossed-row");
    const palIn = h("input", "mossed-palette");
    palIn.type = "text";
    palIn.placeholder = t("palettePh");
    const swatches = h("span", "mossed-swatches", "");
    paletteRow.appendChild(field(t("palette"), palIn));
    paletteRow.appendChild(swatches);
    form.appendChild(paletteRow);

    const boxRow = h("div", "mossed-row mossed-boxrow");
    const bx = [numInput(0, 1, 0.001, "mossed-bbox"), numInput(0, 1, 0.001, "mossed-bbox"),
        numInput(0, 1, 0.001, "mossed-bbox"), numInput(0, 1, 0.001, "mossed-bbox")];
    ["x0", "y0", "x1", "y1"].forEach((k, i) => {
        bx[i].placeholder = k;
        bx[i].title = k;
    });
    const centerBtn = h("button", "mossed-btn", t("center"));
    centerBtn.type = "button";
    boxRow.appendChild(h("span", "mossed-field-label", t("bbox")));
    for (const inp of bx) boxRow.appendChild(inp);
    boxRow.appendChild(centerBtn);
    form.appendChild(boxRow);

    const orderRow = h("div", "mossed-row mossed-orderrow");
    orderRow.appendChild(h("span", "mossed-field-label", t("order")));
    const upBtn = h("button", "mossed-btn", t("moveUp"));
    upBtn.type = "button";
    const downBtn = h("button", "mossed-btn", t("moveDown"));
    downBtn.type = "button";
    orderRow.appendChild(upBtn);
    orderRow.appendChild(downBtn);
    const alignRow = h("div", "mossed-row mossed-alignrow");
    const alignBtns = {};
    for (const [mode, glyph] of [["left", "⇤"], ["hcenter", "↔"], ["right", "⇥"],
        ["top", "⤒"], ["vcenter", "↕"], ["bottom", "⤓"]]) {
        const b = h("button", "mossed-iconbtn mossed-alignbtn", glyph);
        b.type = "button";
        b.dataset.mode = mode;
        alignBtns[mode] = b;
        alignRow.appendChild(b);
    }
    form.appendChild(orderRow);
    form.appendChild(alignRow);

    const gridOnly = h("div", "mossed-row");
    const sizeSel = h("select", "mossed-size");
    for (const v of SIZES) {
        const op = h("option", null, sizeText(v));
        op.value = v;
        sizeSel.appendChild(op);
    }
    const noChip = h("span", "mossed-chip", "");
    gridOnly.appendChild(field(t("size"), sizeSel));
    gridOnly.appendChild(noChip);
    form.appendChild(gridOnly);

    const warn = h("div", "mossed-warn", "");
    form.appendChild(warn);
    panel.appendChild(form);
    root.appendChild(panel);

    return {
        root, bar, presetSel, applyBtn, chip, spacer, undoBtn, redoBtn, helpBtn, helpBox, presetHint,
        importBtn, exportBtn, queueBtn, fileIn, notice,
        gridRow, colsIn, rowsIn, aspectSel, gridAdvBtn, gridAdvRow, edgeIn, gutterIn, marginIn,
        canvasRow, canvasPresetSel, wIn, hIn, aspectChip,
        stage, svg, welcome, welcomeTitle, welcomeSub, welcomeChips,
        fatalBox: fatal, status, panel,
        empty, form, typeDot, idChip, labelSel, dupBtn, delBtn, capTa, textRow, textIn,
        palIn, swatches, boxRow, bx, centerBtn, upBtn, downBtn, alignBtns,
        gridOnly, sizeSel, noChip, warn,
    };
}

// ---------------------------------------------------------------------------
// 渲染
// ---------------------------------------------------------------------------
function boxOf(ui, item) {
    if (ui.drag && ui.drag.id === item.id && ui.drag.box) return ui.drag.box;
    return item.bbox;
}

/** 按框宽裁剪标签：CJK ≈ 1em，ASCII ≈ 0.55em（18 号字）。 */
function fitLabel(text, w) {
    const maxUnits = (w - 20) / 18;
    if (maxUnits < 3) return "";
    let units = 0;
    let out = "";
    for (const ch of text) {
        const u = /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? 1 : 0.55;
        if (units + u > maxUnits) return out + "…";
        units += u;
        out += ch;
    }
    return out;
}

function itemCaption(item) {
    return `${labelText(item.label)}${item.caption ? " · " + item.caption : ""}`;
}

function placeItem(rec, b, item, W, H, isGrid) {
    const x = b[0] * W;
    const y = b[1] * H;
    const w = Math.max(1, (b[2] - b[0]) * W);
    const hh = Math.max(1, (b[3] - b[1]) * H);
    rec.rect.setAttribute("x", String(x));
    rec.rect.setAttribute("y", String(y));
    rec.rect.setAttribute("width", String(w));
    rec.rect.setAttribute("height", String(hh));
    rec.label.setAttribute("x", String(x + (isGrid ? 48 : 26)));
    rec.label.setAttribute("y", String(y + 22));
    rec.label.textContent = fitLabel(itemCaption(item), w - (isGrid ? 40 : 8));
    rec.dot.setAttribute("cx", String(x + 15));
    rec.dot.setAttribute("cy", String(y + 16));
    // grid 左上角已有镜号徽章，类型点让位
    rec.dot.style.display = isGrid || w < 46 || hh < 30 ? "none" : "";
    rec.label.style.display = rec.label.textContent ? "" : "none";
    if (rec.badge) {
        rec.badge.setAttribute("x", String(x + 6));
        rec.badge.setAttribute("y", String(y + 6));
        rec.badgeText.setAttribute("x", String(x + 17));
        rec.badgeText.setAttribute("y", String(y + 20));
        const show = w > 40 && hh > 34;
        rec.badge.style.display = show ? "" : "none";
        rec.badgeText.style.display = show ? "" : "none";
    }
}

function paintSnap(ui, guides) {
    if (!ui.els?.snap) return;
    const { W, H } = ui.vb;
    ui.els.snap.replaceChildren();
    for (const gd of guides || []) {
        if (gd.axis === "x") {
            ui.els.snap.appendChild(s("line", {
                x1: gd.at * W, y1: 0, x2: gd.at * W, y2: H, class: "mossed-snapline",
            }));
        } else {
            ui.els.snap.appendChild(s("line", {
                x1: 0, y1: gd.at * H, x2: W, y2: gd.at * H, class: "mossed-snapline",
            }));
        }
    }
}

function paintDraw(ui, b) {
    if (!ui.els?.draw) return;
    const { W, H } = ui.vb;
    if (!b) {
        ui.els.draw.setAttribute("visibility", "hidden");
        return;
    }
    ui.els.draw.setAttribute("visibility", "visible");
    ui.els.draw.setAttribute("x", String(b[0] * W));
    ui.els.draw.setAttribute("y", String(b[1] * H));
    ui.els.draw.setAttribute("width", String(Math.max(1, (b[2] - b[0]) * W)));
    ui.els.draw.setAttribute("height", String(Math.max(1, (b[3] - b[1]) * H)));
}

/** 只重画被拖的那一个框 + 吸附线；拖动路径不重建整棵 SVG。 */
function paintDrag(ui) {
    const d = ui.drag;
    if (!d) return;
    ui.paintCount = (ui.paintCount || 0) + 1;
    const { W, H } = ui.vb;
    if (d.id) {
        const item = ui.state.items.find((x) => x && x.id === d.id);
        const rec = ui.els?.items.get(d.id);
        if (item && rec) placeItem(rec, d.box, item, W, H, ui.state.preset === "grid");
        paintDraw(ui, null);
    } else if (d.mode === "draw") {
        paintDraw(ui, d.box);
    }
    paintSnap(ui, d.guides);
}

function render(ui) {
    ui.renderCount = (ui.renderCount || 0) + 1;
    const st = ui.state;
    if (!st) {
        ui.svg.replaceChildren();
        ui.welcome.hidden = true;
        return;
    }
    const aspect = stageAspect(st);
    const W = 1000;
    const H = W / aspect;
    ui.vb = { W, H };
    ui.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    ui.svg.replaceChildren();

    ui.svg.appendChild(s("rect", { x: 0, y: 0, width: W, height: H, class: "mossed-canvas-bg", rx: 4 }));
    const guides = s("g", { class: "mossed-guides" });
    for (const f of [1 / 3, 0.5, 2 / 3]) {
        guides.appendChild(s("line", { x1: f * W, y1: 0, x2: f * W, y2: H, class: "mossed-guide" }));
        guides.appendChild(s("line", { x1: 0, y1: f * H, x2: W, y2: f * H, class: "mossed-guide" }));
    }
    ui.svg.appendChild(guides);

    const isGrid = st.preset === "grid";
    ui.svg.classList.toggle("mossed-gridmode", isGrid);
    const cells = s("g", { class: "mossed-cells" });
    if (isGrid && !st.items.length) {
        const g = st.grid || DEFAULT_GRID;
        const cols = Math.max(1, Math.floor(g.cols || 1));
        const rows = Math.max(1, Math.floor(g.rows || 1));
        for (let i = 0; i < cols * rows; i += 1) {
            const c = i % cols;
            const r = Math.floor(i / cols);
            cells.appendChild(s("rect", {
                x: (c / cols) * W + 2, y: (r / rows) * H + 2,
                width: W / cols - 4, height: H / rows - 4, class: "mossed-cell",
            }));
            const tx = s("text", { x: (c / cols) * W + 12, y: (r / rows) * H + 28, class: "mossed-num" });
            tx.textContent = String(i + 1);
            cells.appendChild(tx);
        }
    }
    ui.svg.appendChild(cells);

    const itemsG = s("g", { class: "mossed-items" });
    ui.svg.appendChild(itemsG);
    const drawRect = s("rect", { class: "mossed-draw", visibility: "hidden" });
    ui.svg.appendChild(drawRect);
    const handlesG = s("g", { class: "mossed-handles" });
    ui.svg.appendChild(handlesG);
    const snapG = s("g", { class: "mossed-snaplines" });
    ui.svg.appendChild(snapG);
    ui.els = { items: new Map(), snap: snapG, draw: drawRect };

    for (const item of st.items) {
        if (!item || typeof item !== "object") continue;
        const b = boxOf(ui, item);
        if (!Array.isArray(b) || b.length !== 4 || !b.every(isNum)) continue;
        const sel = ui.sel === item.id;
        const g = s("g", { class: "mossed-itemg" });
        g.style.setProperty("--type-color", TYPE_COLORS[item.label] || "#9aa4b2");
        if (Array.isArray(item.palette) && isHex(item.palette[0])) {
            g.style.setProperty("--item-fill", item.palette[0] + "24");
        }
        const rect = s("rect", {
            class: "mossed-item" + (sel ? " mossed-sel" : "") + (isGrid ? " mossed-cellitem" : ""),
            rx: 3,
        });
        rect.dataset.role = "item";
        rect.dataset.id = item.id;
        g.appendChild(rect);

        const dot = s("circle", { r: 5, class: "mossed-typedot" });
        g.appendChild(dot);
        const label = s("text", { class: "mossed-itemlabel" });
        g.appendChild(label);

        let badge = null;
        let badgeText = null;
        if (isGrid) {
            badge = s("rect", { width: 22, height: 18, rx: 5, class: "mossed-badge" });
            badgeText = s("text", { class: "mossed-badge-text" });
            badgeText.textContent = String(item.no ?? "?");
            g.appendChild(badge);
            g.appendChild(badgeText);
        }
        const tip = s("title");
        tip.textContent = `${itemCaption(item)}${item.label === "text" && item.text ? " — " + item.text : ""}`;
        g.appendChild(tip);

        itemsG.appendChild(g);
        const rec = { g, rect, dot, label, badge, badgeText };
        ui.els.items.set(item.id, rec);
        placeItem(rec, b, item, W, H, isGrid);
    }

    if (ui.sel && !isGrid) {
        const item = st.items.find((it) => it && it.id === ui.sel);
        const b = item && boxOf(ui, item);
        if (item && Array.isArray(b)) {
            const x = b[0] * W;
            const y = b[1] * H;
            const w = (b[2] - b[0]) * W;
            const hh = (b[3] - b[1]) * H;
            const hs = 16;
            const spots = {
                nw: [x, y], n: [x + w / 2, y], ne: [x + w, y],
                w: [x, y + hh / 2], e: [x + w, y + hh / 2],
                sw: [x, y + hh], s: [x + w / 2, y + hh], se: [x + w, y + hh],
            };
            for (const [k, [px, py]] of Object.entries(spots)) {
                const hd = s("rect", {
                    x: px - hs / 2, y: py - hs / 2, width: hs, height: hs, class: `mossed-handle mossed-h-${k}`,
                });
                hd.dataset.role = "handle";
                hd.dataset.id = item.id;
                hd.dataset.h = k;
                handlesG.appendChild(hd);
            }
        }
    }

    paintSnap(ui, (ui.drag && ui.drag.guides) || []);
    ui.welcome.hidden = !!st.items.length || !!(ui.drag && ui.drag.mode === "draw");
}

function syncStatic(ui) {
    const st = ui.state;
    const lang = uiLang();
    const L = (v) => pick(v, lang);
    for (const op of ui.presetSel.options) op.textContent = L(PRESET_SPECS[op.value].label);
    for (const op of ui.labelSel.options) op.textContent = labelText(op.value);
    for (const op of ui.sizeSel.options) op.textContent = sizeText(op.value);

    ui.applyBtn.textContent = t("apply");
    ui.applyBtn.title = t("apply");
    ui.importBtn.textContent = t("importBtn");
    ui.importBtn.title = t("importTitle");
    ui.exportBtn.textContent = t("exportBtn");
    ui.exportBtn.title = t("exportTitle");
    ui.queueBtn.textContent = t("queueBtn");
    ui.queueBtn.title = t("queueTitle");
    ui.helpBtn.title = t("help");
    ui.helpBtn.setAttribute("aria-label", t("help"));
    ui.undoBtn.title = t("undo") + " (Ctrl/Cmd + Z)";
    ui.undoBtn.setAttribute("aria-label", t("undo"));
    ui.redoBtn.title = t("redo") + " (Ctrl/Cmd + Shift + Z)";
    ui.redoBtn.setAttribute("aria-label", t("redo"));
    ui.gridAdvBtn.textContent = t("advanced");
    ui.dupBtn.textContent = t("duplicate");
    ui.dupBtn.title = "Ctrl/Cmd + D";
    ui.delBtn.textContent = t("del");
    ui.centerBtn.textContent = t("center");
    ui.upBtn.textContent = t("moveUp");
    ui.downBtn.textContent = t("moveDown");
    for (const [mode, btn] of Object.entries(ui.alignBtns)) {
        btn.title = t(ALIGN_LABELS[mode]);
        btn.setAttribute("aria-label", t(ALIGN_LABELS[mode]));
    }
    ui.empty.textContent = t("emptySelection");
    ui.capTa.placeholder = t("captionPh");
    ui.textIn.placeholder = t("textPh");
    ui.palIn.placeholder = t("palettePh");

    const helpTitle = ui.helpBox.querySelector(".mossed-help-title");
    const helpKeys = ui.helpBox.querySelector(".mossed-help-keys");
    const helpNote = ui.helpBox.querySelector(".mossed-help-note");
    if (helpTitle) helpTitle.textContent = t("helpTitle");
    if (helpKeys) helpKeys.textContent = t("helpKeys");
    if (helpNote) helpNote.textContent = t("helpFields");

    const labels = ui.gridRow.querySelectorAll(".mossed-field-label");
    if (labels[0]) labels[0].textContent = t("cols");
    if (labels[1]) labels[1].textContent = t("rows");
    if (labels[2]) labels[2].textContent = t("aspect");
    const alabels = ui.gridAdvRow.querySelectorAll(".mossed-field-label");
    if (alabels[0]) alabels[0].textContent = t("edge");
    if (alabels[1]) alabels[1].textContent = t("gutter");
    if (alabels[2]) alabels[2].textContent = t("margin");
    const anote = ui.gridAdvRow.querySelector(".mossed-note");
    if (anote) anote.textContent = t("advancedNote");
    const clabels = ui.canvasRow.querySelectorAll(".mossed-field-label");
    if (clabels[0]) clabels[0].textContent = t("canvasPreset");
    if (clabels[1]) clabels[1].textContent = t("width");
    if (clabels[2]) clabels[2].textContent = t("height");

    ui.welcomeTitle.textContent = t("welcomeTitle");
    ui.welcomeSub.textContent = st && st.preset === "grid" ? t("welcomeGrid") : t("welcomeSub");
    for (const b of Array.from(ui.welcomeChips.children)) {
        b.textContent = L(PRESET_SPECS[b.dataset.preset].label);
        b.title = L(PRESET_HINTS[b.dataset.preset] || {});
    }
    const opCustom = ui.canvasPresetSel.querySelector('option[value="custom"]');
    if (opCustom) opCustom.textContent = t("custom");

    if (!st) return;
    const isGrid = st.preset === "grid";
    const g = st.grid || DEFAULT_GRID;
    const c = st.canvas || DEFAULT_CANVAS;
    ui.presetSel.value = st.preset;
    ui.gridRow.hidden = !isGrid;
    ui.gridAdvRow.hidden = !isGrid || ui.gridAdvRow.hidden;
    ui.canvasRow.hidden = !!isGrid;
    if (document.activeElement !== ui.colsIn) ui.colsIn.value = String(g.cols);
    if (document.activeElement !== ui.rowsIn) ui.rowsIn.value = String(g.rows);
    if (document.activeElement !== ui.aspectSel) ui.aspectSel.value = g.panel_aspect;
    if (document.activeElement !== ui.edgeIn) ui.edgeIn.value = String(g.panel_edge);
    if (document.activeElement !== ui.gutterIn) ui.gutterIn.value = String(g.gutter);
    if (document.activeElement !== ui.marginIn) ui.marginIn.value = String(g.margin);
    if (document.activeElement !== ui.wIn) ui.wIn.value = String(c.width);
    if (document.activeElement !== ui.hIn) ui.hIn.value = String(c.height);
    const cp = CANVAS_PRESETS.find((p) => p.width === c.width && p.height === c.height);
    if (document.activeElement !== ui.canvasPresetSel) ui.canvasPresetSel.value = cp ? cp.id : "custom";
    ui.chip.textContent = `${L(PRESET_SPECS[st.preset]?.label || {}) || st.preset} · `
        + (isGrid ? `${g.cols}×${g.rows}` : `${c.width}×${c.height}`);
    ui.aspectChip.textContent = `${(stageAspect(st)).toFixed(2)}:1`;
    ui.presetHint.textContent = L(PRESET_HINTS[st.preset] || {});
}

function updateFatal(ui) {
    if (!ui.fatal) {
        if (ui.fatalBox) ui.fatalBox.hidden = true;
        return;
    }
    if (!ui.fatalBox) return;
    ui.fatalBox.hidden = false;
    ui.fatalBox.replaceChildren();
    ui.fatalBox.appendChild(h("div", "mossed-fatal-title", t("fatal")));
    ui.fatalBox.appendChild(h("div", "mossed-fatal-msg", ui.fatalMsg || ""));
    const btn = h("button", "mossed-btn mossed-danger", t("reset"));
    btn.type = "button";
    btn.addEventListener("click", (e) => {
        e.stopPropagation();
        try { if (!window.confirm(t("resetAsk"))) return; } catch (err) { /* 无 confirm */ }
        const st = emptyState();
        ui.fatal = null;
        ui.fatalMsg = "";
        ui.state = st;
        ui.sel = null;
        ui.warnings = [];
        writeState(ui, st);
        updateFatal(ui);
        render(ui);
        syncPanel(ui, true);
        updateStatus(ui);
    });
    ui.fatalBox.appendChild(btn);
}

function updateStatus(ui) {
    const st = ui.state;
    if (!st) { ui.status.textContent = ""; return; }
    const parts = [];
    parts.push(st.preset === "grid"
        ? `${st.grid?.cols ?? "?"}×${st.grid?.rows ?? "?"}`
        : `${st.canvas?.width ?? "?"}×${st.canvas?.height ?? "?"}`);
    parts.push(`${st.items.length} items`);
    const warns = [];
    if (st.preset === "grid") {
        const n = (st.grid?.cols || 0) * (st.grid?.rows || 0);
        if (n !== st.items.length) warns.push(t("warnGrid"));
    }
    for (const w of ui.warnings || []) {
        if (warns.length >= 3) break;
        if (!warns.includes(w)) warns.push(w);
    }
    ui.status.textContent = parts.join(" · ") + (warns.length ? "  ⚠ " + warns.slice(0, 3).join("；") : "");
    ui.status.classList.toggle("mossed-status-warn", warns.length > 0);
}

// ---------------------------------------------------------------------------
// 属性面板
// ---------------------------------------------------------------------------
function selectedItem(ui) {
    if (!ui.state || !ui.sel) return null;
    return ui.state.items.find((it) => it && it.id === ui.sel) || null;
}

function syncPanel(ui, force) {
    const it = selectedItem(ui);
    const isGrid = ui.state && ui.state.preset === "grid";
    ui.form.hidden = !it;
    ui.empty.hidden = !!it;
    ui.gridRow.hidden = !isGrid;
    ui.canvasRow.hidden = !!isGrid;
    if (!it) return;

    ui.idChip.textContent = it.id;
    ui.typeDot.style.background = TYPE_COLORS[it.label] || "#9aa4b2";
    ui.typeDot.style.color = TYPE_COLORS[it.label] || "#9aa4b2";
    ui.dupBtn.disabled = !!isGrid;
    ui.centerBtn.disabled = !!isGrid;
    ui.upBtn.disabled = !!isGrid;
    ui.downBtn.disabled = !!isGrid;
    for (const btn of Object.values(ui.alignBtns)) btn.disabled = !!isGrid;
    if (force || document.activeElement !== ui.labelSel) ui.labelSel.value = ITEM_LABELS.includes(it.label) ? it.label : "object";
    if (force || document.activeElement !== ui.capTa) ui.capTa.value = it.caption || "";
    if (force || document.activeElement !== ui.textIn) ui.textIn.value = it.text || "";
    if (force || document.activeElement !== ui.palIn) {
        ui.palIn.value = Array.isArray(it.palette) ? it.palette.join(", ") : "";
    }
    ui.textRow.hidden = it.label !== "text";
    ui.gridOnly.hidden = !isGrid;
    ui.noChip.textContent = isGrid ? `${t("panelNo")} ${it.no ?? "?"}` : "";
    if (isGrid) {
        if (force || document.activeElement !== ui.sizeSel) ui.sizeSel.value = SIZES.includes(it.size) ? it.size : SIZES[3];
    }
    const b = Array.isArray(it.bbox) ? it.bbox : [0, 0, 0, 0];
    ui.bx.forEach((inp, i) => {
        if (force || document.activeElement !== inp) inp.value = isNum(b[i]) ? String(round3(b[i])) : "";
        inp.disabled = !!isGrid;
    });

    renderSwatches(ui, it);
    const warns = [];
    if (!String(it.caption || "").trim()) warns.push(t("warnCaption"));
    if (it.label === "text" && !it.text) warns.push(t("warnText"));
    if (!ITEM_LABELS.includes(it.label)) warns.push(t("warnUnknownLabel"));
    if (Array.isArray(it.palette) && (it.palette.length > 5 || it.palette.some((c) => !isHex(c)))) {
        warns.push(t("warnPalette"));
    }
    ui.warn.textContent = warns.join("；");
}

function renderSwatches(ui, it) {
    ui.swatches.replaceChildren();
    for (const c of (it.palette || []).slice(0, 5)) {
        if (!isHex(c)) continue;
        const sw = h("span", "mossed-swatch");
        sw.style.background = c;
        sw.title = c;
        ui.swatches.appendChild(sw);
    }
}

// ---------------------------------------------------------------------------
// 读写 state
// ---------------------------------------------------------------------------
function writeState(ui, st) {
    const raw = JSON.stringify(st);
    ui.raw = raw;
    ui.widget.value = raw;
    try { ui.node.setDirtyCanvas?.(true, true); } catch (e) { /* 老前端 */ }
}

function commit(ui, historyKey = null) {
    if (!ui.state) return;
    ui.state.version = STATE_VERSION;
    // 原地改 state 后才调 commit ⇒ 改动前的样子只能靠上次记下的 histRaw
    const raw = JSON.stringify(ui.state);
    if (ui.histRaw != null && raw !== ui.histRaw) recordHistory(ui.hist, ui.histRaw, historyKey);
    ui.histRaw = raw;
    writeState(ui, ui.state);
    ui.warnings = validateState(ui.state, uiLang()).errors;
    render(ui);
    updateStatus(ui);
    syncHistoryButtons(ui);
}

function resetHistory(ui) {
    ui.hist = makeHistory();
    ui.histRaw = ui.state ? JSON.stringify(ui.state) : null;
    syncHistoryButtons(ui);
}

function syncHistoryButtons(ui) {
    ui.undoBtn.disabled = !ui.state || !ui.hist.undo.length;
    ui.redoBtn.disabled = !ui.state || !ui.hist.redo.length;
    if (ui.queueBtn) ui.queueBtn.disabled = !ui.state;
}

function flash(ui, msg, isErr = false) {
    if (!ui.notice) return;
    ui.notice.textContent = msg;
    ui.notice.title = msg;
    ui.notice.classList.toggle("mossed-err", !!isErr);
    clearTimeout(ui.noticeTimer);
    ui.noticeTimer = setTimeout(() => {
        ui.notice.textContent = "";
        ui.notice.classList.remove("mossed-err");
    }, 6000);
}

function stepUndo(ui, dir) {
    if (!ui.state || ui.drag) return;
    const raw = stepHistory(ui.hist, ui.histRaw, dir);
    if (raw == null) return;
    ui.state = readState(raw, uiLang()).state;
    ui.histRaw = JSON.stringify(ui.state);
    if (ui.sel && !ui.state.items.some((it) => it && it.id === ui.sel)) ui.sel = null;
    writeState(ui, ui.state);
    ui.warnings = validateState(ui.state, uiLang()).errors;
    render(ui);
    syncPanel(ui, true);
    updateStatus(ui);
    syncStatic(ui);
    syncHistoryButtons(ui);
}

function loadFromWidget(ui, force) {
    let raw;
    try {
        const v = ui.widget.value;
        raw = (v == null) ? "" : (typeof v === "string" ? v : JSON.stringify(v));
    } catch (e) {
        raw = "";
    }
    if (!force && raw === ui.raw) return;
    const res = readState(raw, uiLang());
    ui.raw = raw;
    if (res.fatal) {
        ui.state = null;
        ui.fatal = true;
        ui.fatalMsg = res.fatal;
        ui.sel = null;
        updateFatal(ui);
        render(ui);
        syncPanel(ui, true);
        updateStatus(ui);
        resetHistory(ui);
        return;
    }
    ui.fatal = null;
    ui.fatalMsg = "";
    ui.state = res.state;
    ui.warnings = res.warnings || [];
    if (ui.sel && !ui.state.items.some((it) => it && it.id === ui.sel)) ui.sel = null;
    if (res.fixed) writeState(ui, ui.state);
    updateFatal(ui);
    render(ui);
    syncPanel(ui, true);
    updateStatus(ui);
    syncStatic(ui);
    // 外部改了 widget（打开工作流 / 宿主级撤销）⇒ 旧历史对不上，清空
    resetHistory(ui);
}

function currentGrid(ui) {
    return ui.state?.grid || DEFAULT_GRID;
}

function regenerate(ui) {
    if (!ui.state) return;
    const preset = ui.presetSel.value;
    const st = ui.state;
    st.preset = preset;
    if (preset === "grid") {
        st.grid = { ...DEFAULT_GRID, ...currentGrid(ui) };
        st.items = presetItems("grid", { cols: st.grid.cols, rows: st.grid.rows, lang: uiLang() });
    } else {
        st.items = presetItems(preset, { lang: uiLang() });
    }
    ui.sel = st.items[0]?.id || null;
    commit(ui);
    syncPanel(ui, true);
    syncStatic(ui);
}

function regrid(ui) {
    const st = ui.state;
    if (!st || st.preset !== "grid" || !st.grid) return;
    const old = st.items.slice();
    st.items = presetItems("grid", { cols: st.grid.cols, rows: st.grid.rows, lang: uiLang() })
        .map((it, i) => {
            const prev = old[i];
            if (!prev || typeof prev !== "object") return it;
            return {
                ...it,
                label: prev.label || it.label,
                caption: typeof prev.caption === "string" && prev.caption ? prev.caption : it.caption,
                size: SIZES.includes(prev.size) ? prev.size : it.size,
            };
        });
    if (ui.sel && !st.items.some((it) => it.id === ui.sel)) ui.sel = null;
    commit(ui);
    syncPanel(ui, true);
}

function setCanvas(ui, w, h) {
    const st = ui.state;
    if (!st) return;
    const cw = clamp(Math.round(w / 16) * 16, 256, 4096);
    const ch = clamp(Math.round(h / 16) * 16, 256, 4096);
    if (st.canvas?.width === cw && st.canvas?.height === ch) return;
    st.canvas = { width: cw, height: ch };
    commit(ui);
    syncStatic(ui);
}

// ---------------------------------------------------------------------------
// 画布交互
// ---------------------------------------------------------------------------
function toNorm(ui, ev) {
    const svg = ui.svg;
    const vb = svg.viewBox?.baseVal;
    if (!vb || !vb.width || !vb.height) return null;
    let p;
    try {
        const pt = svg.createSVGPoint();
        pt.x = ev.clientX;
        pt.y = ev.clientY;
        p = pt.matrixTransform(svg.getScreenCTM().inverse());
    } catch (e) { return null; }
    if (!isNum(p.x) || !isNum(p.y)) return null;
    return { x: clamp(p.x / vb.width, 0, 1), y: clamp(p.y / vb.height, 0, 1) };
}

function snapThreshold(ui) {
    const r = ui.svg.getBoundingClientRect();
    const vb = ui.svg.viewBox?.baseVal;
    if (!r.width || !vb || !vb.width || !vb.height) return { tx: 0.01, ty: 0.01 };
    const scale = Math.min(r.width / vb.width, r.height / vb.height) || 0;
    if (!scale) return { tx: 0.01, ty: 0.01 };
    return {
        tx: 7 / (vb.width * scale),
        ty: 7 / (vb.height * scale),
        drawnW: vb.width * scale,
    };
}

function snapBox(ui, box, movingEdges, excludeId) {
    const st = ui.state;
    const isGrid = st.preset === "grid";
    if (isGrid) return { box, guides: [] };
    const { xs, ys } = commonTargets(st.items, excludeId);
    const th = snapThreshold(ui);
    const c = st.canvas || DEFAULT_CANVAS;
    const guides = [];
    const out = box.slice();
    const useX = movingEdges.x === "both" ? [out[0], out[2]] : [out[movingEdges.x === "x0" ? 0 : 2]];
    const useY = movingEdges.y === "both" ? [out[1], out[3]] : [out[movingEdges.y === "y0" ? 1 : 3]];
    const sx = snapAxis(useX, xs, th.tx, 16 / c.width);
    if (sx) {
        if (movingEdges.x === "both") { out[0] += sx.d; out[2] += sx.d; }
        else if (movingEdges.x === "x0") out[0] += sx.d;
        else out[2] += sx.d;
        guides.push({ axis: "x", at: sx.guide });
    }
    const sy = snapAxis(useY, ys, th.ty, 16 / c.height);
    if (sy) {
        if (movingEdges.y === "both") { out[1] += sy.d; out[3] += sy.d; }
        else if (movingEdges.y === "y0") out[1] += sy.d;
        else out[3] += sy.d;
        guides.push({ axis: "y", at: sy.guide });
    }
    return { box: out, guides };
}

function startDrag(ui, ev) {
    const st = ui.state;
    if (!st) return;
    const role = ev.target?.dataset?.role;
    const pt = toNorm(ui, ev);
    if (!pt) return;
    const isGrid = st.preset === "grid";
    try { ui.svg.focus({ preventScroll: true }); } catch (e) { /* 老前端 */ }

    if (role === "item" || role === "handle") {
        const id = ev.target.dataset.id;
        const item = st.items.find((it) => it && it.id === id);
        if (!item) return;
        ui.sel = id;
        syncPanel(ui, true);
        render(ui);
        if (isGrid) return;   // 宫格只读：能选不能拖

        const hdl = role === "handle" ? ev.target.dataset.h : null;
        const box = Array.isArray(item.bbox) ? item.bbox.slice() : [0, 0, 0.1, 0.1];
        // orig = 拖动起点，applyMove 每次都从它算增量（拿 box 当基准会一帧叠一次）
        if (hdl) {
            const xEdge = hdl.includes("w") ? "x0" : hdl.includes("e") ? "x1" : "none";
            const yEdge = hdl.includes("n") ? "y0" : hdl.includes("s") ? "y1" : "none";
            ui.drag = { mode: "resize", id, h: hdl, start: pt, orig: box.slice(), box, moving: { x: xEdge, y: yEdge }, guides: [] };
        } else {
            ui.drag = { mode: "move", id, start: pt, orig: box.slice(), box, moving: { x: "both", y: "both" }, guides: [] };
        }
        ui.svg.classList.add("mossed-dragging");
        try { ui.svg.setPointerCapture(ev.pointerId); } catch (e) { /* 老前端 */ }
        return;
    }

    // 空白处：取消选择；single 模式开始画新框
    ui.sel = null;
    syncPanel(ui, true);
    if (isGrid) { render(ui); return; }
    ui.drag = {
        mode: "draw", id: null, start: pt,
        box: [pt.x, pt.y, pt.x, pt.y], moving: { x: "both", y: "both" }, guides: [],
    };
    ui.svg.classList.add("mossed-dragging");
    try { ui.svg.setPointerCapture(ev.pointerId); } catch (e) { /* 老前端 */ }
    render(ui);
}

function updateBoxInputs(ui, box) {
    ui.bx.forEach((inp, i) => {
        if (document.activeElement !== inp) inp.value = String(round3(box[i]));
    });
}

/** 计算一帧拖动结果并只重画受影响的部分。 */
function applyMove(ui, clientX, clientY) {
    const d = ui.drag;
    if (!d) return;
    const pt = toNorm(ui, { clientX, clientY });
    if (!pt) return;
    let box;
    if (d.mode === "draw") {
        box = [Math.min(d.start.x, pt.x), Math.min(d.start.y, pt.y), Math.max(d.start.x, pt.x), Math.max(d.start.y, pt.y)];
        if (box[2] - box[0] < 0.005 || box[3] - box[1] < 0.005) {
            d.box = box;
            d.guides = [];
            paintDraw(ui, box);
            paintSnap(ui, []);
            return;
        }
    } else if (d.mode === "move") {
        const dx = pt.x - d.start.x;
        const dy = pt.y - d.start.y;
        box = d.orig.slice();
        box[0] += dx; box[2] += dx; box[1] += dy; box[3] += dy;
        const w = d.orig[2] - d.orig[0];
        const hh = d.orig[3] - d.orig[1];
        box[0] = clamp(box[0], 0, 1 - w);
        box[2] = box[0] + w;
        box[1] = clamp(box[1], 0, 1 - hh);
        box[3] = box[1] + hh;
    } else {
        box = d.orig.slice();
        const dx = pt.x - d.start.x;
        const dy = pt.y - d.start.y;
        if (d.moving.x === "x0") box[0] = clamp(d.orig[0] + dx, 0, d.orig[2] - 0.01);
        if (d.moving.x === "x1") box[2] = clamp(d.orig[2] + dx, d.orig[0] + 0.01, 1);
        if (d.moving.y === "y0") box[1] = clamp(d.orig[1] + dy, 0, d.orig[3] - 0.01);
        if (d.moving.y === "y1") box[3] = clamp(d.orig[3] + dy, d.orig[1] + 0.01, 1);
    }
    const snapped = snapBox(ui, box, d.moving, d.id);
    d.box = snapped.box;
    d.guides = snapped.guides;
    if (d.id) {
        const it = ui.state.items.find((x) => x && x.id === d.id);
        if (it) it.bbox = d.box.slice();
    }
    paintDrag(ui);
    updateBoxInputs(ui, d.box);
}

/** pointermove 只记最新点；一帧最多算一次。 */
function onPointerMove(ui, ev) {
    if (!ui.drag) return;
    ev.preventDefault();
    ui.pending = { x: ev.clientX, y: ev.clientY };
    if (ui.raf) return;
    ui.raf = requestAnimationFrame(() => {
        ui.raf = 0;
        const p = ui.pending;
        ui.pending = null;
        if (p && ui.drag) applyMove(ui, p.x, p.y);
    });
}

function flushPending(ui) {
    if (ui.raf) { cancelAnimationFrame(ui.raf); ui.raf = 0; }
    const p = ui.pending;
    ui.pending = null;
    if (p && ui.drag) applyMove(ui, p.x, p.y);
}

function endDrag(ui, ev) {
    flushPending(ui);
    const d = ui.drag;
    if (!d) return;
    ui.drag = null;
    ui.svg.classList.remove("mossed-dragging");
    paintSnap(ui, []);
    paintDraw(ui, null);
    const st = ui.state;
    if (!st) { render(ui); return; }
    if (d.mode === "draw") {
        const b = d.box.map(round3);
        if (b[2] - b[0] >= 0.01 && b[3] - b[1] >= 0.01) {
            const it = {
                id: makeItemId("object", st.items),
                label: "object",
                caption: t("newItem"),
                bbox: b,
            };
            st.items.push(it);
            ui.sel = it.id;
            commit(ui);
            syncPanel(ui, true);
            try { ui.svg.releasePointerCapture(ev.pointerId); } catch (e) { /* 已释放 */ }
            return;
        }
        render(ui);
        try { ui.svg.releasePointerCapture(ev.pointerId); } catch (e) { /* 已释放 */ }
        return;
    }
    const it = st.items.find((x) => x && x.id === d.id);
    if (it && Array.isArray(d.box)) it.bbox = d.box.map(round3);
    commit(ui);
    syncPanel(ui, true);
    try { ui.svg.releasePointerCapture(ev.pointerId); } catch (e) { /* 已释放 */ }
}

/** 预设 → 生成初始框（欢迎面板/工具栏共用）。 */
function applyPreset(ui, id, generate = true) {
    if (!ui.state || !PRESET_IDS.includes(id)) return;
    ui.presetSel.value = id;
    ui.state.preset = id;
    if (id === "grid") {
        if (!ui.state.grid) ui.state.grid = { ...DEFAULT_GRID };
    } else if (!ui.state.canvas) {
        ui.state.canvas = { ...DEFAULT_CANVAS };
    }
    if (generate) regenerate(ui);
    else { commit(ui); syncPanel(ui, true); syncStatic(ui); }
}

function deleteSelected(ui) {
    const it = selectedItem(ui);
    if (!it || !ui.state) return;
    ui.state.items = ui.state.items.filter((x) => x !== it);
    ui.sel = null;
    commit(ui);
    syncPanel(ui, true);
}

function duplicateItem(ui) {
    const it = selectedItem(ui);
    if (!it || !ui.state || ui.state.preset === "grid") return;
    const copy = JSON.parse(JSON.stringify(it));
    copy.id = makeItemId(it.label, ui.state.items);
    const b = it.bbox.slice();
    const dx = 0.03;
    const dy = 0.03;
    const w = b[2] - b[0];
    const hh = b[3] - b[1];
    b[0] = clamp(b[0] + dx, 0, 1 - w);
    b[2] = b[0] + w;
    b[1] = clamp(b[1] + dy, 0, 1 - hh);
    b[3] = b[1] + hh;
    copy.bbox = b.map(round3);
    ui.state.items.push(copy);
    ui.sel = copy.id;
    commit(ui);
    syncPanel(ui, true);
}

function moveSelectedZ(ui, dir) {
    const it = selectedItem(ui);
    if (!it || ui.state?.preset === "grid") return;
    const next = moveItemInArray(ui.state.items, it.id, dir);
    if (next === ui.state.items) return;
    ui.state.items = next;
    commit(ui);
    syncPanel(ui, true);
}

function alignSelected(ui, mode) {
    const it = selectedItem(ui);
    if (!it || ui.state?.preset === "grid") return;
    const next = alignBbox(it.bbox, mode);
    if (!next) return;
    it.bbox = next;
    commit(ui);
    syncPanel(ui, true);
}

function centerItem(ui) {
    const it = selectedItem(ui);
    if (!it || !Array.isArray(it.bbox) || ui.state?.preset === "grid") return;
    const b = it.bbox.slice();
    const w = b[2] - b[0];
    const hh = b[3] - b[1];
    it.bbox = [round3((1 - w) / 2), round3((1 - hh) / 2), round3((1 + w) / 2), round3((1 + hh) / 2)];
    commit(ui);
    syncPanel(ui, true);
}

function nudgeItem(ui, dx, dy) {
    const it = selectedItem(ui);
    const st = ui.state;
    if (!it || !st || st.preset === "grid" || !Array.isArray(it.bbox)) return;
    const c = st.canvas || DEFAULT_CANVAS;
    const sx = dx / (c.width || 1);
    const sy = dy / (c.height || 1);
    const b = it.bbox.slice();
    const w = b[2] - b[0];
    const hh = b[3] - b[1];
    b[0] = clamp(b[0] + sx, 0, 1 - w);
    b[2] = b[0] + w;
    b[1] = clamp(b[1] + sy, 0, 1 - hh);
    b[3] = b[1] + hh;
    it.bbox = b.map(round3);
    commit(ui, "nudge:" + it.id);
    syncPanel(ui, true);
}

function onKeyDown(ui, ev) {
    const tag = String(ev.target?.tagName || "").toUpperCase();
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (ev.key === "Escape") {
        if (typing) { ev.target.blur(); return; }
        if (!ui.helpBox.hidden) { ui.helpBox.hidden = true; return; }
        if (ui.drag) {
            const d = ui.drag;
            ui.drag = null;
            ui.svg.classList.remove("mossed-dragging");
            if (d.id && ui.state) {
                const it = ui.state.items.find((x) => x && x.id === d.id);
                if (it && Array.isArray(d.orig)) it.bbox = d.orig.map(round3);
            }
            render(ui);
            syncPanel(ui, true);
            return;
        }
        if (ui.sel) { ui.sel = null; syncPanel(ui, true); render(ui); }
        return;
    }
    if (typing) return;
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey) {
        const k = ev.key.toLowerCase();
        if (k === "z" || k === "y") {
            ev.preventDefault();
            stepUndo(ui, k === "y" || ev.shiftKey ? 1 : -1);
            return;
        }
    }
    if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); deleteSelected(ui); return; }
    if (ev.key === "ArrowLeft" || ev.key === "ArrowRight" || ev.key === "ArrowUp" || ev.key === "ArrowDown") {
        if (!ui.sel || ui.state?.preset === "grid") return;
        ev.preventDefault();
        const step = ev.shiftKey ? 10 : 1;
        const dx = ev.key === "ArrowLeft" ? -step : ev.key === "ArrowRight" ? step : 0;
        const dy = ev.key === "ArrowUp" ? -step : ev.key === "ArrowDown" ? step : 0;
        nudgeItem(ui, dx, dy);
        return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "d") {
        ev.preventDefault();
        duplicateItem(ui);
    }
}

// ---------------------------------------------------------------------------
// 事件接线
// ---------------------------------------------------------------------------
function wire(ui) {
    ui.presetSel.addEventListener("change", () => {
        if (!ui.state) return;
        applyPreset(ui, ui.presetSel.value, false);
    });

    ui.applyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const n = ui.state?.items?.length || 0;
        try {
            if (n && !window.confirm(t("confirmApply").replace("{n}", String(n)))) return;
        } catch (err) { /* 无 confirm */ }
        regenerate(ui);
    });

    ui.importBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        ui.fileIn.value = "";
        ui.fileIn.click();
    });
    ui.fileIn.addEventListener("change", async () => {
        const file = ui.fileIn.files && ui.fileIn.files[0];
        if (!file) return;
        try {
            if (file.size > MAX_STATE_CHARS) throw new Error(t("importTooBig"));
            const text = await file.text();
            if (text.length > MAX_STATE_CHARS) throw new Error(t("importTooBig"));
            let obj;
            try {
                obj = JSON.parse(text);
            } catch (err) {
                throw new Error("JSON · " + err.message);
            }
            const kind = sniffLayoutDoc(obj);
            let st;
            if (kind === "elements") st = elementsToState(obj, { lang: uiLang(), preset: "free" });
            else if (kind === "state") {
                const res = readState(text, uiLang());
                if (res.fatal) throw new Error(res.fatal);
                st = res.state;
            } else {
                throw new Error(t("importUnknown"));
            }
            const n = ui.state?.items?.length || 0;
            if (n) {
                try {
                    if (!window.confirm(t("importConfirm").replace("{n}", String(n)))) return;
                } catch (err) { /* 无 confirm */ }
            }
            ui.state = st;
            ui.sel = st.items[0]?.id || null;
            commit(ui, "import");
            syncPanel(ui, true);
            syncStatic(ui);
            flash(ui, t("importOk").replace("{n}", String(st.items.length)));
        } catch (err) {
            flash(ui, t("importFail") + (err && err.message ? err.message : String(err)), true);
        } finally {
            ui.fileIn.value = "";
        }
    });
    ui.queueBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!ui.state) { flash(ui, t("queueNoState"), true); return; }
        try {
            if (!app || typeof app.queuePrompt !== "function") {
                throw new Error("app.queuePrompt is unavailable in this front-end");
            }
            app.queuePrompt(0, 1);
            flash(ui, t("queueOk"));
        } catch (err) {
            flash(ui, t("queueFail") + (err && err.message ? err.message : String(err)), true);
        }
    });
    ui.exportBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const st = ui.state;
        if (!st) { flash(ui, t("exportNoState"), true); return; }
        if (st.preset === "grid") { flash(ui, t("exportGrid"), true); return; }
        try {
            const doc = stateToElements(st, uiLang());
            const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `moss-elements-${st.preset}.json`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            flash(ui, t("exportOk"));
        } catch (err) {
            flash(ui, t("exportFail") + (err && err.message ? err.message : String(err)), true);
        }
    });

    ui.helpBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        ui.helpBox.hidden = !ui.helpBox.hidden;
        if (!ui.helpBox.hidden) syncStatic(ui);
    });
    ui.gridAdvBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        ui.gridAdvRow.hidden = !ui.gridAdvRow.hidden;
    });
    ui.welcomeChips.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-preset]");
        if (!b) return;
        e.stopPropagation();
        applyPreset(ui, b.dataset.preset, true);
    });

    ui.colsIn.addEventListener("change", () => {
        if (!ui.state) return;
        const g = currentGrid(ui);
        const cols = clamp(Math.round(Number(ui.colsIn.value) || g.cols), 1, 12);
        ui.state.grid = { ...DEFAULT_GRID, ...g, cols };
        ui.colsIn.value = String(cols);
        regrid(ui);
        syncStatic(ui);
    });
    ui.rowsIn.addEventListener("change", () => {
        if (!ui.state) return;
        const g = currentGrid(ui);
        const rows = clamp(Math.round(Number(ui.rowsIn.value) || g.rows), 1, 12);
        ui.state.grid = { ...DEFAULT_GRID, ...g, rows };
        ui.rowsIn.value = String(rows);
        regrid(ui);
        syncStatic(ui);
    });
    ui.aspectSel.addEventListener("change", () => {
        if (!ui.state) return;
        ui.state.grid = { ...DEFAULT_GRID, ...currentGrid(ui), panel_aspect: ui.aspectSel.value };
        commit(ui);
        syncStatic(ui);
    });
    const setGridNumber = (inp, key, min, max, step) => {
        if (!ui.state) return;
        const g = currentGrid(ui);
        let v = clamp(Math.round(Number(inp.value) || g[key]), min, max);
        if (step > 1) v = Math.round(v / step) * step;
        ui.state.grid = { ...DEFAULT_GRID, ...g, [key]: v };
        inp.value = String(v);
        commit(ui);
        syncStatic(ui);
    };
    ui.edgeIn.addEventListener("change", () => setGridNumber(ui.edgeIn, "panel_edge", 64, 4096, 16));
    ui.gutterIn.addEventListener("change", () => setGridNumber(ui.gutterIn, "gutter", 0, 256, 1));
    ui.marginIn.addEventListener("change", () => setGridNumber(ui.marginIn, "margin", 0, 256, 1));
    ui.canvasPresetSel.addEventListener("change", () => {
        if (!ui.state) return;
        const p = CANVAS_PRESETS.find((x) => x.id === ui.canvasPresetSel.value);
        if (p) setCanvas(ui, p.width, p.height);
        else syncStatic(ui);
    });
    ui.wIn.addEventListener("change", () => setCanvas(ui, Number(ui.wIn.value), ui.state.canvas?.height || DEFAULT_CANVAS.height));
    ui.hIn.addEventListener("change", () => setCanvas(ui, ui.state.canvas?.width || DEFAULT_CANVAS.width, Number(ui.hIn.value)));

    ui.labelSel.addEventListener("change", () => {
        const it = selectedItem(ui);
        if (!it) return;
        it.label = ui.labelSel.value;
        if (it.label === "text") {
            if (typeof it.text !== "string") it.text = "";
        } else {
            delete it.text;
        }
        commit(ui);
        syncPanel(ui, true);
    });
    ui.capTa.addEventListener("input", () => {
        const it = selectedItem(ui);
        if (!it) return;
        it.caption = ui.capTa.value;
        commit(ui, "caption:" + it.id);
    });
    ui.textIn.addEventListener("input", () => {
        const it = selectedItem(ui);
        if (!it) return;
        it.text = ui.textIn.value;
        commit(ui, "text:" + it.id);
    });
    ui.palIn.addEventListener("input", () => {
        const it = selectedItem(ui);
        if (!it) return;
        const vals = ui.palIn.value.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean);
        const good = vals.filter(isHex).slice(0, 5);
        if (good.length) it.palette = good;
        else delete it.palette;
        commit(ui, "palette:" + it.id);
        renderSwatches(ui, it);
        syncPanelTail(ui, it);
    });
    ui.palIn.addEventListener("blur", () => syncPanel(ui, true));
    ui.bx.forEach((inp, i) => {
        inp.addEventListener("change", () => {
            const it = selectedItem(ui);
            if (!it || !Array.isArray(it.bbox)) return;
            const v = Number(inp.value);
            if (!isNum(v)) { syncPanel(ui, true); return; }
            const b = it.bbox.slice();
            b[i] = clamp(v, 0, 1);
            if (b[0] >= b[2] || b[1] >= b[3]) { syncPanel(ui, true); return; }
            it.bbox = b.map(round3);
            commit(ui);
            syncPanel(ui, true);
        });
    });
    ui.sizeSel.addEventListener("change", () => {
        const it = selectedItem(ui);
        if (!it) return;
        it.size = ui.sizeSel.value;
        commit(ui);
        syncPanel(ui, true);
    });
    ui.delBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteSelected(ui); });
    ui.undoBtn.addEventListener("click", (e) => { e.stopPropagation(); stepUndo(ui, -1); });
    ui.redoBtn.addEventListener("click", (e) => { e.stopPropagation(); stepUndo(ui, 1); });
    ui.dupBtn.addEventListener("click", (e) => { e.stopPropagation(); duplicateItem(ui); });
    ui.centerBtn.addEventListener("click", (e) => { e.stopPropagation(); centerItem(ui); });
    ui.upBtn.addEventListener("click", (e) => { e.stopPropagation(); moveSelectedZ(ui, -1); });
    ui.downBtn.addEventListener("click", (e) => { e.stopPropagation(); moveSelectedZ(ui, 1); });
    for (const [mode, btn] of Object.entries(ui.alignBtns)) {
        btn.addEventListener("click", (e) => { e.stopPropagation(); alignSelected(ui, mode); });
    }

    ui.svg.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        startDrag(ui, ev);
    });
    ui.svg.addEventListener("pointermove", (ev) => onPointerMove(ui, ev));
    ui.svg.addEventListener("pointerup", (ev) => endDrag(ui, ev));
    ui.svg.addEventListener("pointercancel", (ev) => endDrag(ui, ev));
    ui.root.addEventListener("keydown", (ev) => onKeyDown(ui, ev));
    ui.root.addEventListener("click", (ev) => {
        if (ui.helpBox.hidden) return;
        if (ui.helpBox.contains(ev.target) || ui.helpBtn.contains(ev.target)) return;
        ui.helpBox.hidden = true;
    });
}

function syncPanelTail(ui, it) {
    const warns = [];
    if (!String(it.caption || "").trim()) warns.push(t("warnCaption"));
    if (it.label === "text" && !it.text) warns.push(t("warnText"));
    if (Array.isArray(it.palette) && (it.palette.length > 5 || it.palette.some((c) => !isHex(c)))) {
        warns.push(t("warnPalette"));
    }
    ui.warn.textContent = warns.join("；");
}

// ---------------------------------------------------------------------------
// 挂载 / 启动
// ---------------------------------------------------------------------------
function hideWidget(widget) {
    try {
        // 压成 0 高：仍然在 widgets 数组里 ⇒ 照常序列化（铁律 4：顺序不动）
        widget.computeSize = () => [0, -4];
    } catch (e) { /* 冻结对象 */ }
}

function mount(node) {
    if (!node || node.type !== NODE_TYPE || node.__mossedUI) return false;
    const widget = (node.widgets || []).find((w) => w && w.name === STATE_WIDGET);
    if (!widget) return false;
    let ui;
    try {
        ui = buildUI();
        node.addDOMWidget("mosssb_editor_ui", "moss-layout-editor", ui.root, {
            serialize: false,
            hideOnZoom: false,
            // ⚠ 不要同时给 getMinHeight：宿主 computeLayoutSize 会优先用 min，
            // 忽略 getHeight，编辑器只能分到剩余空间（实测会被压到 ~450px）。
            getHeight: () => 700,
            getMaxHeight: () => 2000,
        });
    } catch (e) {
        // 失败时**不藏** widget：至少还能直接编辑 JSON，不至于整个 state 消失
        console.warn("%s: 该前端不支持节点 DOM 组件，编辑器不可用", TAG, e);
        return false;
    }
    hideWidget(widget);
    ui.node = node;
    ui.widget = widget;
    ui.state = null;
    ui.raw = null;
    ui.sel = null;
    ui.drag = null;
    ui.fatal = null;
    ui.fatalMsg = "";
    ui.warnings = [];
    ui.hist = makeHistory();
    ui.histRaw = null;
    node.__mossedUI = ui;

    try {
        node.setSize?.([Math.max(node.size?.[0] || 0, 620), Math.max(node.size?.[1] || 0, 800)]);
    } catch (e) { /* 尺寸设不了不影响功能 */ }

    wire(ui);
    loadFromWidget(ui, true);
    syncStatic(ui);

    const origConfigure = node.onConfigure;
    node.onConfigure = function onConfigure() {
        const r = origConfigure?.apply(this, arguments);
        try { loadFromWidget(ui, true); syncStatic(ui); } catch (e) { /* 单节点失败 */ }
        return r;
    };
    return true;
}

function ownNodes() {
    try { return (app?.graph?._nodes || []).filter((n) => n && n.type === NODE_TYPE); }
    catch (e) { return []; }
}

function mountAll() {
    let n = 0;
    for (const node of ownNodes()) {
        if (mount(node)) n += 1;
    }
    return n;
}

/** 兜底轮询：工作流载入时 nodeCreated 不一定触发；同时同步外部改动过的 widget。 */
function guard() {
    try {
        for (const node of ownNodes()) {
            const ui = node.__mossedUI;
            if (!ui) { mount(node); continue; }
            let raw;
            try {
                const v = ui.widget.value;
                raw = v == null ? "" : (typeof v === "string" ? v : JSON.stringify(v));
            } catch (e) { continue; }
            if (raw !== ui.raw) loadFromWidget(ui, true);
        }
    } catch (e) { /* 这一轮跳过 */ }
}

let booted = false;
let tries = 0;

function bootstrap(why) {
    if (booted) return;
    try {
        if (!globalThis.document?.body || !app?.graph) {
            if (tries++ < 60) setTimeout(() => bootstrap("retry"), 500);
            return;
        }
        booted = true;
        mountAll();
        setInterval(guard, 1500);
        try {
            globalThis.addEventListener?.("mosssb:lang-changed", () => {
                for (const node of ownNodes()) if (node.__mossedUI) { syncStatic(node.__mossedUI); render(node.__mossedUI); syncPanel(node.__mossedUI, false); }
            });
        } catch (e) { /* 无 window */ }
        console.info("%s: 就绪（本包节点 %d，入口=%s）", TAG, ownNodes().length, why);
    } catch (e) {
        console.error("%s: 启动异常（编辑器不可用，画布不受影响）", TAG, e);
    }
}

try {
    if (typeof document !== "undefined" && app?.registerExtension) {
        app.registerExtension({
            name: "mosssb.editor",
            async setup() { bootstrap("setup"); },
            nodeCreated(node) {
                try {
                    if (node?.type !== NODE_TYPE) return;
                    if (!mount(node)) setTimeout(() => { try { mount(node); } catch (e) { /* 忽略 */ } }, 0);
                } catch (e) { /* 单节点失败忽略 */ }
            },
        });
        bootstrap("module");
    }
} catch (e) {
    console.warn("%s: 模块加载失败", TAG, e);
}

// 排障/冒烟入口（只读为主；业务代码不要依赖）
try {
    globalThis.MOSS_SB_EDITOR = {
        nodeType: NODE_TYPE,
        stateWidget: STATE_WIDGET,
        presets: PRESET_IDS,
        app: () => app,
        graph: () => app?.graph,
        canvas: () => app?.canvas,
        where: () => ({
            booted,
            nodes: ownNodes().length,
            mounted: ownNodes().filter((n) => !!n.__mossedUI).length,
            ui: ownNodes().map((n) => ({
                id: n.id, sel: n.__mossedUI?.sel ?? null,
                items: n.__mossedUI?.state?.items?.length ?? null,
                fatal: !!n.__mossedUI?.fatal,
            })),
        }),
        mountAll,
        mount,
        stats: (node) => ({
            renders: node?.__mossedUI?.renderCount || 0,
            paints: node?.__mossedUI?.paintCount || 0,
            items: node?.__mossedUI?.state?.items?.length ?? null,
        }),
        stateOf: (node) => (node?.__mossedUI?.state ? JSON.parse(JSON.stringify(node.__mossedUI.state)) : null),
        setState: (node, obj) => {
            const ui = node?.__mossedUI;
            if (!ui) return false;
            writeState(ui, obj);
            loadFromWidget(ui, true);
            syncStatic(ui);
            return true;
        },
        validate: validateState,
        presets_json: (preset, opt) => presetItems(preset, opt),
    };
} catch (e) { /* 无 window */ }
