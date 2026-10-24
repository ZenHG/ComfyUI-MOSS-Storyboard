# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""编辑器前端自检（零 GPU、零浏览器、秒级）。

三查（对应 docs/10 F6）：
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
# 契约解析（docs/10 是唯一真相源）
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
    TYPE_COLORS, CANVAS_PRESETS,
    presetItems, validateState, readState, snapAxis, stageAspect, makeItemId, emptyState,
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

const enErrors = validateState({ version: 2, preset: "free", style: "", canvas: { width: 1536, height: 864 }, items: [] }, "en").errors.join(" | ");
ok("英文校验文案无 CJK", enErrors.includes("version must be") && !/[\u4e00-\u9fff]/.test(enErrors), enErrors);
const enFatal = readState("{}", "en").fatal || "";
ok("英文 fatal 文案无 CJK", enFatal.includes("version") && !/[\u4e00-\u9fff]/.test(enFatal), enFatal);

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
