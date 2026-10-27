# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""JS ↔ Python 契约差分测试（零 GPU、零宿主）。

同一份契约有两套实现：`web/moss_sb_editor.js`（编辑器）与 `core/editor.py` /
`core/presets.py`（编译层）。任何一边改了预设、默认值或校验规则，这个测试都会红，
防止"编辑器绿灯、节点报错"或"同一个预设出两套版式"。

跑法：python tests/test_cross_language.py
需要 node（与 tools/check_editor_ui.py 相同）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import editor as E  # noqa: E402
from core import interop as I  # noqa: E402
from core import intake as IN  # noqa: E402
from core import layout as L  # noqa: E402
from core import planner as PL  # noqa: E402
from core import presets as P  # noqa: E402

PASS, FAIL = [], []
PRESETS = list(E.PRESETS)
GRID = {"cols": 4, "rows": 2, "panel_aspect": "16:9", "panel_edge": 512, "gutter": 8, "margin": 8}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + str(detail)[:500]) if detail else ""))


def find_node():
    for b in ("node", "node.exe"):
        p = shutil.which(b)
        if p:
            return p
    c = r"C:/Program Files/nodejs/node.exe"
    return c if os.path.exists(c) else None


def js_runner():
    return r"""
import {
    PRESET_IDS, PRESET_SPECS, presetItems, validateState, readState, emptyState,
    DEFAULT_GRID, DEFAULT_CANVAS, ASPECT_IDS, MAX_ITEMS, MAX_SIDE, MAX_STATE_CHARS, CANVAS_PRESETS,
    elementsToState, stateToElements, ELEMENTS_VERSION,
    compileFinalPrompt, findPlaceholders, findIgnoredGridFields, parseStoryboard, gridCanvasPixels,
} from "./extensions/pack/moss_sb_editor.js";
import { readFileSync } from "node:fs";

const fixtures = JSON.parse(readFileSync("./fixtures.json", "utf8"));
const elements = JSON.parse(readFileSync("./elements.json", "utf8"));
const states = JSON.parse(readFileSync("./states.json", "utf8"));
const compiles = JSON.parse(readFileSync("./compiles.json", "utf8"));
const texts = JSON.parse(readFileSync("./texts.json", "utf8"));
const grids = JSON.parse(readFileSync("./grids.json", "utf8"));
const out = { presets: {}, grid: {}, empty: emptyState(), default_grid: DEFAULT_GRID,
              default_canvas: DEFAULT_CANVAS, aspects: ASPECT_IDS, max_items: MAX_ITEMS,
              max_side: MAX_SIDE, max_state_chars: MAX_STATE_CHARS, canvas_presets: CANVAS_PRESETS,
              compiles: [], state_compiles: [], ph: [], state_ph: [], ign: [], validations: [] };
for (const id of PRESET_IDS) out.presets[id] = presetItems(id, { lang: "zh" });
for (const [cols, rows] of [[1,1],[2,1],[2,2],[3,2],[3,3]]) {
    out.grid[cols + "x" + rows] = presetItems("grid", { cols, rows, lang: "zh" });
}
// 最终提示词是"发出去的那一句"，两边必须逐字节相同（含坐标取整与键序）。
const safeCompile = (s) => { try { return compileFinalPrompt(s); } catch (e) { return "ERR:" + e.message; } };
out.compiles = compiles.map(safeCompile);
out.state_compiles = states.map(safeCompile);
out.ph = compiles.map((s) => findPlaceholders(s));
out.state_ph = states.map((s) => findPlaceholders(s));
out.ign = compiles.map((s) => findIgnoredGridFields(s));
for (const st of fixtures) out.validations.push(validateState(st).errors.length === 0);
out.elements_version = ELEMENTS_VERSION;
out.elements_states = elements.map((d) => elementsToState(d, { lang: "zh" }));
out.elements_exports = out.elements_states.map((s) => stateToElements(s, "zh"));
out.state_exports = states.map((s) => stateToElements(s, "zh"));
// 外接稿反解析：同一段文本两侧必须切出同样的段数/景别/描述
out.intake = texts.map((t) => parseStoryboard(t));
// 宫格总画布：UI 里显示的像素必须等于 build_layout 算的像素
out.grid_canvas = grids.map((g) => gridCanvasPixels(g));
out.read_canvas = [32, 4112].map((w) => {
    const r = readState(JSON.stringify({ version: 3, preset: "free", style: "",
                                         canvas: { width: w, height: 864 }, items: [] }));
    return { canvas: r.state.canvas, warnings: r.warnings.length };
});
console.log("__RESULT__" + JSON.stringify(out));
"""


def run_js(fixtures, elements=None, states=None, compiles=None, texts=None, grids=None):
    node = find_node()
    if not node:
        return None
    tmp = tempfile.mkdtemp(prefix="mosssb_xlang_")
    try:
        os.makedirs(os.path.join(tmp, "extensions", "pack"))
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copyfile(os.path.join(ROOT, "web", "moss_sb_editor.js"),
                        os.path.join(tmp, "extensions", "pack", "moss_sb_editor.js"))
        with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as f:
            f.write('{"type":"module"}')
        with open(os.path.join(tmp, "scripts", "app.js"), "w", encoding="utf-8") as f:
            f.write("export const app = { registerExtension() {}, graph: null, canvas: null };\n")
        with open(os.path.join(tmp, "fixtures.json"), "w", encoding="utf-8") as f:
            json.dump(fixtures, f, ensure_ascii=False)
        with open(os.path.join(tmp, "elements.json"), "w", encoding="utf-8") as f:
            json.dump(elements or [], f, ensure_ascii=False)
        with open(os.path.join(tmp, "states.json"), "w", encoding="utf-8") as f:
            json.dump(states or [], f, ensure_ascii=False)
        with open(os.path.join(tmp, "compiles.json"), "w", encoding="utf-8") as f:
            json.dump(compiles or [], f, ensure_ascii=False)
        with open(os.path.join(tmp, "texts.json"), "w", encoding="utf-8") as f:
            json.dump(texts or [], f, ensure_ascii=False)
        with open(os.path.join(tmp, "grids.json"), "w", encoding="utf-8") as f:
            json.dump(grids or [], f, ensure_ascii=False)
        with open(os.path.join(tmp, "run.mjs"), "w", encoding="utf-8") as f:
            f.write(js_runner())
        r = subprocess.run([node, "run.mjs"], cwd=tmp, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        for line in (r.stdout or "").splitlines():
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__"):])
        raise RuntimeError(((r.stderr or "") + (r.stdout or ""))[:1500])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def py_state(preset):
    if preset == "grid":
        return P.build("grid", grid=GRID)
    return P.build(preset, canvas=(1536, 864))


def comparable(items):
    return [{
        "label": it.get("label"),
        "caption": it.get("caption"),
        "bbox": [round(float(v), 9) for v in it.get("bbox") or []],
        "text": it.get("text"),
        "palette": it.get("palette"),
        "no": it.get("no"),
        "size": it.get("size"),
    } for it in items]


def fixtures():
    base = {
        "version": 3, "preset": "title-body", "style": "x",
        "canvas": {"width": 1536, "height": 864},
        "items": [{"id": "t1", "label": "text", "caption": "标题", "text": "字",
                   "palette": ["#c8a15a"], "bbox": [0.1, 0.1, 0.9, 0.2]}],
    }
    grid_items = P.build("grid", grid=GRID)["items"]
    grid = {"version": 3, "preset": "grid", "style": "", "grid": GRID, "items": grid_items}
    bad = [
        {**base, "version": 2},
        {**base, "items": [{**base["items"][0], "bbox": [0.9, 0.1, 0.1, 0.2]}]},
        {**base, "items": [{**base["items"][0], "text": ""}]},
        {**base, "items": [{**base["items"][0], "text": " "}]},
        {**base, "items": [{**base["items"][0], "palette": ["#111111"] * 6}]},
        {**base, "items": [{**base["items"][0], "palette": ["#12345678"]}]},
        {**base, "items": [{**base["items"][0], "palette": ["#zzzzzz"]}]},
        {**base, "items": [base["items"][0], base["items"][0]]},
        {**base, "items": [{**base["items"][0], "label": None}]},
        {**base, "items": [{**base["items"][0], "bbox": ["0.1", "0.1", "0.9", "0.2"]}]},
        {**base, "canvas": {"width": 32, "height": 864}},
        {**base, "canvas": {"width": 1536, "height": 864, "x": 1}},
        {**grid, "items": grid_items[:-1]},
        {**grid, "items": [{**grid_items[0], "no": 2}, *grid_items[1:]]},
        {**grid, "items": [{**grid_items[0], "size": "超特写"}, *grid_items[1:]]},
        {**grid, "grid": {**GRID, "panel_aspect": "banana"}},
        {"version": 3, "preset": "nope", "style": "", "items": []},
        [],
    ]
    blank_grid = {"version": 3, "preset": "grid", "style": "",
                  "grid": {**GRID, "cols": 1, "rows": 1}, "items": []}
    bad.append({**blank_grid, "grid": {**GRID, "cols": 2, "rows": 1}})
    return [base, grid, blank_grid] + bad


def element_docs():
    return [
        {"version": 1, "elements": [
            {"role": "text", "desc": "标题", "text": "字",
             "bbox": [80, 100, 920, 190], "palette": ["#c8a15a"]},
            {"role": "obj", "desc": "主体", "bbox": [0, 0, 1000, 1000]},
        ]},
        {"version": 1, "canvas": {"width": 1024, "height": 1024}, "style": "胶片",
         "elements": [
             {"type": "person", "desc": "人物", "bbox": [100, 100, 900, 900], "z": 2},
             {"role": "object", "desc": "背景", "bbox": [0, 0, 500, 500], "z": 1,
              "weight": 3},
             {"id": "title", "role": "text", "desc": "标题", "text": "夜雨",
              "bbox": [10, 10, 990, 90]},
         ]},
        {"version": 1, "x_ext": {"tool": "planner-x"},
         "elements": [{"id": "a", "role": "object", "desc": "d",
                       "bbox": [1, 2, 3, 4], "x_ext": {"n": 1}, "note": "x"}]},
        {"version": 1, "elements": []},
    ]


def random_states(n=100):
    import random
    rng = random.Random(20261026)
    out = []
    for k in range(n):
        items = []
        for i in range(rng.randint(1, 6)):
            x0 = rng.randint(0, 900000) / 1_000_000
            y0 = rng.randint(0, 900000) / 1_000_000
            x1 = x0 + rng.randint(1, 99999) / 1_000_000
            y1 = y0 + rng.randint(1, 99999) / 1_000_000
            if x1 > 1:
                x1 = 1.0
            if y1 > 1:
                y1 = 1.0
            label = rng.choice(["object", "character", "text"])
            item = {"id": "r%d_%d" % (k, i), "label": label,
                    "caption": "元素 %d" % i,
                    "bbox": [x0, y0, x1, y1]}
            if label == "text":
                item["text"] = "字%d" % i
            if rng.random() < 0.3:
                item["palette"] = ["#c8a15a"]
            items.append(item)
        out.append({"version": 3, "preset": rng.choice(["free", "title-body"]),
                    "style": "s%d" % k if rng.random() < 0.5 else "",
                    "canvas": rng.choice([{"width": 1536, "height": 864},
                                          {"width": 1024, "height": 1024}]),
                    "items": items})
    return out


def compile_cases(states):
    """两边同一份输入，比"真正发出去的那句提示词"的字节。

    专挑容易分叉的地方：坐标取整的 .5 平局、键序、`no` 乱序、景别前缀重复、
    空 palette、style 全空白、无元素退化。
    """
    grid_items = P.build("grid", grid=GRID)["items"]
    single = {
        "version": 3, "preset": "title-body", "style": "暗色雪原。",
        "canvas": {"width": 1536, "height": 864},
        "items": [
            {"id": "t1", "label": "text", "caption": "粗体大标题", "text": " 永兵传说 ",
             "palette": ["#c8a15a"], "bbox": [0.08, 0.10, 0.92, 0.19]},
            {"id": "o1", "label": "object", "caption": "主体", "text": "多余的字",
             "palette": [], "bbox": [0.30, 0.30, 0.70, 0.90]},
            {"id": "c1", "label": "character", "caption": "人物",
             "bbox": [0.00625, 0.00625, 1.0, 1.0]},
        ],
    }
    tie = {  # 0.00625 × 80 = 0.5 —— 逢半取偶 vs 四舍五入就在这里分家
        "version": 3, "preset": "free", "style": "", "canvas": {"width": 80, "height": 64},
        "items": [{"id": "o1", "label": "object", "caption": "贴边小物",
                   "bbox": [0.00625, 0.00625, 0.99375, 0.99375]}],
    }
    blank = {"version": 3, "preset": "free", "style": "  只有风格  ",
             "canvas": {"width": 1024, "height": 1024}, "items": []}
    nos = [{**it, "no": n} for it, n in zip(grid_items, (7, 3, 8, 1, 5, 2, 6, 4), strict=True)]
    dup_size = [{**it, "size": "中景", "caption": cap} for it, cap in zip(
        grid_items, ["中景里的桌面", "别的东西", "中景", "杯子", "窗外", "雨", "招牌", "街"],
        strict=True)]
    return [
        single, tie, blank,
        {"version": 3, "preset": "grid", "style": "格风格", "grid": GRID, "items": nos},
        {"version": 3, "preset": "grid", "style": "", "grid": GRID, "items": dup_size},
        {"version": 3, "preset": "grid", "style": "  ",
         "grid": {**GRID, "cols": 1, "rows": 1}, "items": []},
        {**single, "items": [dict(single["items"][0], palette=["#ffffff", "#000000",
                                                               "#123456", "#abcdef", "#111111"])]},
    ]


INTAKE_TEXTS = [
    "",
    "只有一段全局描述，没有分段标记。",
    "[STYLE] 雨夜咖啡店，暖黄吊灯\n\n[PANEL 1] 中景，她低头看杯子\n[PANEL 2] 特写，眼睛里有霓虹",
    "[STYLE] x\n\n[PANEL 1] Medium shot, framed from the waist up. 她低头\n"
    "[PANEL 2] Close-up, the face fills the frame. 眼睛",
    "[STYLE] x\n[画面 2] 大远景，城市全景\n[分镜 1] 全景，街道",
    "[PANEL 1] 从背后俯拍，人物在右下角\n[PANEL 2] 自定义镜位，斜切构图",
    "[PANEL 1] 中近景\n[PANEL 2] 胸部以上，她侧身",
]


def grid_size_cases():
    """宫格总画布：比例 × 格数 × 边长 × 缝/边距的边界组合（只取 Python 认为合法的那部分）。

    UI 里显示的「将生成 W×H」是 `build_layout` 的镜像实现 —— 差 1px 就是骗用户，
    所以这一组必须逐组对死。边长故意混入 1537（非 16 倍数）与 64（下限）。
    """
    cases, py = [], []
    for aspect in L.ASPECTS:
        for cols, rows in ((1, 1), (2, 1), (2, 2), (3, 1), (4, 2), (3, 3)):
            for edge in (64, 128, 256, 512, 768, 1024, 1537, 2048):
                for gutter, margin in ((0, 0), (8, 8), (17, 3), (64, 64)):
                    try:
                        lay = L.build_layout(panels=cols * rows, cols=cols, rows=rows,
                                             gutter=gutter, margin=margin,
                                             panel_edge=edge, panel_aspect=aspect)
                    except ValueError:
                        continue
                    cases.append({"cols": cols, "rows": rows, "panel_aspect": aspect,
                                  "panel_edge": edge, "gutter": gutter, "margin": margin})
                    py.append([lay.width, lay.height])
    return cases, py


def main():
    node = find_node()
    if not node:
        print("跳过：没有 node")
        return 2
    docs = element_docs()
    states = random_states()
    cases = compile_cases(states)
    gcases, gpy = grid_size_cases()
    data = run_js(fixtures(), docs, states, cases, INTAKE_TEXTS, gcases)
    if data is None:
        print("JS runner 无结果")
        return 1

    # 预设：逐个比较
    for preset in PRESETS:
        py = py_state(preset)
        js = data["presets"].get(preset) or []
        check(f"preset {preset} count", len(py["items"]) == len(js),
              f"{len(py['items'])} vs {len(js)}")
        check(f"preset {preset} items", comparable(py["items"]) == comparable(js),
              json.dumps({"py": comparable(py["items"]), "js": comparable(js)}, ensure_ascii=False)[:900])

    # 宫格矩阵
    for key, js in (data.get("grid") or {}).items():
        cols, rows = (int(x) for x in key.split("x"))
        py = P.build("grid", grid={**GRID, "cols": cols, "rows": rows})
        check(f"grid {key} count", len(py["items"]) == len(js), f"{len(py['items'])} vs {len(js)}")
        check(f"grid {key} items", comparable(py["items"]) == comparable(js),
              json.dumps({"py": comparable(py["items"]), "js": comparable(js)}, ensure_ascii=False)[:900])

    # 宫格总画布：UI 上那行「将生成 W×H」必须等于 build_layout 算的
    js_gc = data.get("grid_canvas") or []
    bad = [(g, p, j) for g, p, j in zip(gcases, gpy, js_gc, strict=False) if p != j]
    check(f"宫格总画布 UI==build_layout（{len(gcases)} 组）",
          len(js_gc) == len(gpy) and not bad,
          json.dumps({"数量": [len(gpy), len(js_gc)], "不符": bad[:3]}, ensure_ascii=False))

    # 默认值
    py_empty = E.empty_state()
    js_empty = data["empty"]
    check("empty canvas", py_empty["canvas"] == js_empty.get("canvas"),
          f"{py_empty.get('canvas')} vs {js_empty.get('canvas')}")
    py_grid = P.build("grid")
    js_dg = data.get("default_grid") or {}
    check("default grid cols/rows", (py_grid["grid"]["cols"], py_grid["grid"]["rows"]) ==
          (js_dg.get("cols"), js_dg.get("rows")), f"{py_grid['grid']} vs {js_dg}")
    check("default canvas constant", E.empty_state()["canvas"] == data.get("default_canvas"),
          f"{E.empty_state()['canvas']} vs {data.get('default_canvas')}")
    check("aspect ids", list(__import__("core.layout", fromlist=["ASPECTS"]).ASPECTS) ==
          data.get("aspects"), f"{data.get('aspects')}")
    js_canvas = {p["id"]: [p["width"], p["height"]] for p in (data.get("canvas_presets") or [])}
    check("canvas presets", js_canvas == {k: list(v) for k, v in PL.CANVAS_PRESETS.items()},
          f"{js_canvas} vs {PL.CANVAS_PRESETS}")

    # 校验 fixtures：接受/拒绝必须一致
    fx = fixtures()
    js_ok = data.get("validations") or []
    for i, st in enumerate(fx):
        try:
            E.parse_state(json.dumps(st))
            py_ok = True
        except ValueError:
            py_ok = False
        if i < len(js_ok):
            check(f"validate #{i}", py_ok == js_ok[i], f"py={py_ok} js={js_ok[i]}")
    check("validate 数量一致", len(js_ok) == len(fx), f"{len(js_ok)} vs {len(fx)}")

    # elements-v1 互操作：两侧必须逐字段相同（含生成的 id、z 序、x_ext）
    js_states = data.get("elements_states") or []
    js_exports = data.get("elements_exports") or []
    check("elements 版本一致", data.get("elements_version") == I.ELEMENTS_VERSION,
          f"{data.get('elements_version')} vs {I.ELEMENTS_VERSION}")
    check("elements 结果数量一致", len(js_states) == len(docs) == len(js_exports),
          f"{len(js_states)} / {len(docs)} / {len(js_exports)}")
    for i, d in enumerate(docs):
        py = I.to_state(d)
        if i < len(js_states):
            check(f"elements #{i} state", js_states[i] == py,
                  json.dumps({"py": py, "js": js_states[i]}, ensure_ascii=False)[:900])
        if i < len(js_exports):
            py_exp = I.from_state(py)
            check(f"elements #{i} export", js_exports[i] == py_exp,
                  json.dumps({"py": py_exp, "js": js_exports[i]}, ensure_ascii=False)[:900])

    # 导出量化：任意 6 位小数 bbox 两侧必须落成同一个 0–1000 值
    js_state_exports = data.get("state_exports") or []
    check("state 导出数量一致", len(js_state_exports) == len(states),
          f"{len(js_state_exports)} vs {len(states)}")
    bad_q = None
    for st, js_exp in zip(states, js_state_exports, strict=False):
        py_exp = I.from_state(st)
        if js_exp != py_exp:
            bad_q = {"py": py_exp, "js": js_exp}
            break
    check("state 导出量化一致（100 组随机）", bad_q is None,
          json.dumps(bad_q, ensure_ascii=False)[:900])

    # 最终提示词：真正发给模型的那句，两边必须逐字节相同
    js_compiles = data.get("compiles") or []
    check("compile 用例数量一致", len(js_compiles) == len(cases),
          f"{len(js_compiles)} vs {len(cases)}")
    for i, st in enumerate(cases):
        try:
            py = E.compile_final_prompt(E.parse_state(json.dumps(st)))
        except ValueError as exc:
            py = "ERR:" + str(exc)
        if i < len(js_compiles):
            check(f"最终提示词 #{i} 逐字节一致", js_compiles[i] == py,
                  json.dumps({"py": py, "js": js_compiles[i]}, ensure_ascii=False)[:700])

    js_state_compiles = data.get("state_compiles") or []
    check("随机 state compile 数量一致", len(js_state_compiles) == len(states),
          f"{len(js_state_compiles)} vs {len(states)}")
    bad_c = None
    for st, js in zip(states, js_state_compiles, strict=False):
        py = E.compile_final_prompt(E.parse_state(json.dumps(st)))
        if js != py:
            bad_c = {"py": py, "js": js}
            break
    check("随机 state 最终提示词一致（100 组）", bad_c is None,
          json.dumps(bad_c, ensure_ascii=False)[:700])

    # 占位守卫：同一份 state 两侧必须点出同一批字段
    js_ph = data.get("ph") or []
    for i, st in enumerate(cases):
        if i < len(js_ph):
            py = E.find_placeholders(E.parse_state(json.dumps(st)))
            check(f"占位 #{i} 一致", js_ph[i] == py,
                  json.dumps({"py": py, "js": js_ph[i]}, ensure_ascii=False)[:500])
    js_state_ph = data.get("state_ph") or []
    bad_ph = None
    for st, js in zip(states, js_state_ph, strict=False):
        py = E.find_placeholders(st)
        if js != py:
            bad_ph = {"py": py, "js": js}
            break
    check("随机 state 占位一致（100 组）", bad_ph is None,
          json.dumps(bad_ph, ensure_ascii=False)[:500])

    js_ign = data.get("ign") or []
    bad_ign = None
    for i, st in enumerate(cases):
        py = E.find_ignored_grid_fields(E.parse_state(json.dumps(st)))
        if i < len(js_ign) and js_ign[i] != py:
            bad_ign = {"i": i, "py": py, "js": js_ign[i]}
            break
    check("宫格忽略字段一致", bad_ign is None,
          json.dumps(bad_ign, ensure_ascii=False)[:500])

    # 宽容读取：Python 会拒的画布，JS 必须修回默认值而不是留着再报警告
    read_canvas = data.get("read_canvas") or []
    check("readState 结果数量", len(read_canvas) == 2, str(read_canvas))
    for w, r in zip((32, 4112), read_canvas, strict=False):
        check(f"readState canvas width={w} → 修回默认",
              r.get("canvas") == E.empty_state()["canvas"] and r.get("warnings") == 0, str(r))

    # 外接稿反解析：条数守恒 + 每段 style/景别/描述逐字段相同
    js_intake = data.get("intake") or []
    check("外接稿用例数量一致", len(js_intake) == len(INTAKE_TEXTS),
          f"{len(js_intake)} vs {len(INTAKE_TEXTS)}")
    bad_intake = None
    for i, txt in enumerate(INTAKE_TEXTS):
        if i >= len(js_intake):
            break
        py = IN.parse_storyboard(txt)
        js = js_intake[i]
        norm = {"style": js.get("style"), "panels": js.get("panels"),
                "has_marks": js.get("hasMarks")}
        if norm != py:
            bad_intake = {"i": i, "py": py, "js": norm}
            break
    check("外接稿反解析一致", bad_intake is None,
          json.dumps(bad_intake, ensure_ascii=False)[:700])

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
