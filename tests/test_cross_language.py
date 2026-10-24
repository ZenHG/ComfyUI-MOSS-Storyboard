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
    DEFAULT_GRID, DEFAULT_CANVAS, ASPECT_IDS, MAX_ITEMS, MAX_SIDE, MAX_STATE_CHARS,
} from "./extensions/pack/moss_sb_editor.js";
import { readFileSync } from "node:fs";

const fixtures = JSON.parse(readFileSync("./fixtures.json", "utf8"));
const out = { presets: {}, grid: {}, empty: emptyState(), default_grid: DEFAULT_GRID,
              default_canvas: DEFAULT_CANVAS, aspects: ASPECT_IDS, max_items: MAX_ITEMS,
              max_side: MAX_SIDE, max_state_chars: MAX_STATE_CHARS, validations: [] };
for (const id of PRESET_IDS) out.presets[id] = presetItems(id, { lang: "zh" });
for (const [cols, rows] of [[1,1],[2,1],[2,2],[3,2],[3,3]]) {
    out.grid[cols + "x" + rows] = presetItems("grid", { cols, rows, lang: "zh" });
}
for (const st of fixtures) out.validations.push(validateState(st).errors.length === 0);
out.read_canvas = [32, 4112].map((w) => {
    const r = readState(JSON.stringify({ version: 3, preset: "free", style: "",
                                         canvas: { width: w, height: 864 }, items: [] }));
    return { canvas: r.state.canvas, warnings: r.warnings.length };
});
console.log("__RESULT__" + JSON.stringify(out));
"""


def run_js(fixtures):
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


def main():
    node = find_node()
    if not node:
        print("跳过：没有 node")
        return 2
    data = run_js(fixtures())
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

    # 宽容读取：Python 会拒的画布，JS 必须修回默认值而不是留着再报警告
    read_canvas = data.get("read_canvas") or []
    check("readState 结果数量", len(read_canvas) == 2, str(read_canvas))
    for w, r in zip((32, 4112), read_canvas, strict=False):
        check(f"readState canvas width={w} → 修回默认",
              r.get("canvas") == E.empty_state()["canvas"] and r.get("warnings") == 0, str(r))

    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
