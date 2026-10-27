# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""编辑器状态层 v3 的机检（零 GPU、零 comfy、零 torch）。

覆盖：
1. `parse_state` 的校验与错误路径（不静默兜底）
2. 宫格模式编译 `[PANEL n]`（编号必须连续）
3. 单张模式编译多元素 layout JSON
4. 预设生成
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import editor as E
from core import layout as L
from core import presets as P

_ok = 0
_bad = []


def check(name, cond, detail=""):
    global _ok
    if cond:
        _ok += 1
        print("  [OK]   %s" % name)
    else:
        _bad.append(name)
        print("  [FAIL] %s  %s" % (name, detail))


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError as exc:
        return str(exc)
    return None


def grid_state(**kw):
    st = {
        "version": 3, "preset": "grid", "style": "雨夜咖啡店，湿街反光。",
        "grid": {"cols": 2, "rows": 2, "panel_aspect": "16:9",
                 "panel_edge": 512, "gutter": 8, "margin": 8},
        "items": [
            {"id": "s1", "label": "object", "no": 1, "size": "中景",
             "caption": "苏晚坐在窗边", "bbox": [0, 0, 0.5, 0.5]},
            {"id": "s2", "label": "object", "no": 2, "size": "近景",
             "caption": "苏晚抬眼看镜头", "bbox": [0.5, 0, 1, 0.5]},
            {"id": "s3", "label": "object", "no": 3, "size": "特写",
             "caption": "杯沿一道裂痕", "bbox": [0, 0.5, 0.5, 1]},
            {"id": "s4", "label": "object", "no": 4, "size": "全景",
             "caption": "雨巷全景", "bbox": [0.5, 0.5, 1, 1]},
        ],
    }
    st.update(kw)
    return st


def single_state(**kw):
    st = {
        "version": 3, "preset": "title-body", "style": "暗色雪原，飘雪，胶片颗粒。",
        "canvas": {"width": 1536, "height": 864},
        "items": [
            {"id": "t1", "label": "text", "text": "永兵传说",
             "caption": "粗体无衬线大标题，浅金色", "palette": ["#c8a15a"],
             "bbox": [0.08, 0.10, 0.92, 0.22]},
            {"id": "c1", "label": "character", "caption": "主角半身，侧对镜头",
             "bbox": [0.10, 0.26, 0.90, 0.80]},
        ],
    }
    st.update(kw)
    return st


def dump(s):
    return json.dumps(s, ensure_ascii=False)


print("\n== 1. 解析与模式 ==")
check("空串 → 空 state", E.parse_state("").get("items") == [])
check("版本是 3", E.STATE_VERSION == 3)
check("默认空 state 画布 = 1536×864",
      E.empty_state()["canvas"] == {"width": 1536, "height": 864},
      str(E.empty_state().get("canvas")))
blank = E.empty_state("grid")
check("空白宫格 = 1×1 / 0 项", blank["grid"]["cols"] == 1 and blank["grid"]["rows"] == 1
      and blank["items"] == [])
check("空白宫格 state 通过自己的校验", raises(E.parse_state, dump(blank)) is None)
check("空白宫格几何按 1 格占位", E.geometry(E.parse_state(dump(blank))).panels == 1)
check("grid 识别", E.is_grid(E.parse_state(dump(grid_state()))))
check("single 识别", not E.is_grid(E.parse_state(dump(single_state()))))

print("\n== 2. 错误路径 ==")
msg = raises(E.parse_state, "{不是 json")
check("非法 JSON 报错", msg and "JSON" in msg, msg)
msg = raises(E.parse_state, dump(grid_state(version=2)))
check("版本不符报错", msg and "版本" in msg, msg)
msg = raises(E.parse_state, dump(grid_state(preset="不存在的预设")))
check("未知 preset 报错", msg and "preset" in msg, msg)

bad = grid_state()
bad["grid"]["cols"] = 3
msg = raises(E.parse_state, dump(bad))
check("grid 行列×格数不符报错", msg and "行列" in msg, msg)

bad = grid_state()
bad["grid"]["panel_edge"] = 500
msg = raises(E.parse_state, dump(bad))
check("panel_edge 非 16 倍数报错", msg and "16" in msg, msg)

bad = single_state()
bad["canvas"]["width"] = 1000
msg = raises(E.parse_state, dump(bad))
check("canvas 非 16 倍数报错", msg and "canvas" in msg, msg)

bad = single_state()
bad["items"][0].pop("text")
msg = raises(E.parse_state, dump(bad))
check("文字元素缺 text 报错", msg and "text" in msg, msg)

bad = single_state()
bad["items"][0]["palette"] = ["red"]
msg = raises(E.parse_state, dump(bad))
check("palette 格式错误报错", msg and "palette" in msg, msg)

bad = single_state()
bad["items"][1]["bbox"] = [0.9, 0.1, 0.2, 0.8]
msg = raises(E.parse_state, dump(bad))
check("bbox 反向报错", msg and "bbox" in msg, msg)

bad = grid_state()
bad["items"][1]["no"] = 1
msg = raises(E.parse_state, dump(bad))
check("镜号重复报错", msg and "镜号" in msg, msg)

bad = grid_state()
bad["items"][0]["size"] = "x" * (E.SIZE_MAX + 1)
msg = raises(E.parse_state, dump(bad))
check("景别超长报错", msg and "景别" in msg, msg)

# 自定义景别是合法输入（八档只是预设），原样进提示词
custom = grid_state()
custom["items"][0]["size"] = "从背后俯拍"
check("自定义景别通过校验",
      E.parse_state(dump(custom))["items"][0]["size"] == "从背后俯拍")

bad = single_state()
bad["items"][1]["id"] = bad["items"][0]["id"]
msg = raises(E.parse_state, dump(bad))
check("id 重复报错", msg and "id" in msg, msg)

bad = single_state()
bad["items"][1].pop("label")
msg = raises(E.parse_state, dump(bad))
check("label 必填（不再默认 object）", msg and "label" in msg, msg)

bad = single_state()
bad["items"][0]["palette"] = None
msg = raises(E.parse_state, dump(bad))
check("palette=null 报错", msg and "palette" in msg, msg)

bad = single_state()
bad["items"][0]["palette"] = ["#c8a15aff"]
msg = raises(E.parse_state, dump(bad))
check("palette 8 位十六进制报错", msg and "palette" in msg, msg)

bad = single_state()
bad["items"][0]["palette"] = ["#c8a15g"]
msg = raises(E.parse_state, dump(bad))
check("palette 非十六进制报错", msg and "palette" in msg, msg)

bad = single_state()
bad["items"][0]["palette"] = ["#ffffff"] * 6
msg = raises(E.parse_state, dump(bad))
check("palette > 5 个报错", msg and "palette" in msg, msg)

bad = single_state()
bad["items"][1]["bbox"] = [False, 0.1, 0.9, 0.8]
msg = raises(E.parse_state, dump(bad))
check("bbox 布尔报错", msg and "bbox" in msg, msg)

bad = single_state()
bad["items"][1]["bbox"] = ["0.1", 0.1, "0.9", 0.8]
msg = raises(E.parse_state, dump(bad))
check("bbox 字符串报错", msg and "bbox" in msg, msg)

bad = single_state()
bad["canvas"]["width"] = 32
msg = raises(E.parse_state, dump(bad))
check("canvas < 64 报错", msg and "canvas" in msg, msg)

bad = single_state()
bad["canvas"]["width"] = 4112  # 16 的倍数，但超过 4096
msg = raises(E.parse_state, dump(bad))
check("canvas > 4096 报错", msg and "canvas" in msg, msg)

bad = grid_state()
bad["grid"]["cols"] = 13
msg = raises(E.parse_state, dump(bad))
check("grid cols > 12 报错", msg and "grid.cols" in msg, msg)

bad = grid_state()
bad["grid"]["panel_edge"] = 4112
msg = raises(E.parse_state, dump(bad))
check("panel_edge > 4096 报错", msg and "panel_edge" in msg, msg)

bad = grid_state()
bad["grid"]["gutter"] = -1
msg = raises(E.parse_state, dump(bad))
check("gutter < 0 报错", msg and "gutter" in msg, msg)

bad = grid_state()
bad["grid"]["margin"] = 257
msg = raises(E.parse_state, dump(bad))
check("margin > 256 报错", msg and "margin" in msg, msg)

bad = single_state(style="x" * 1_000_001)
msg = raises(E.parse_state, dump(bad))
check("state > 1,000,000 字符报错", msg and "上限" in msg, msg)

bad = single_state(preset="free", items=[{"id": "i%d" % i} for i in range(257)])
msg = raises(E.parse_state, dump(bad))
check("items > 256 报错", msg and "上限" in msg, msg)

s = E.parse_state(dump(single_state(version=3.0)))
check("version 3.0 按整数接受（JSON 无 int/float 之分）", s["version"] == 3)

bad = single_state(version=3.5)
msg = raises(E.parse_state, dump(bad))
check("version 非整数（3.5）报错", msg and "版本" in msg, msg)

print("\n== 3. 宫格模式 ==")
s = E.parse_state(dump(grid_state()))
p = E.compile_prompt(s)
lines = [ln for ln in p.split("\n") if ln.strip()]
check("首行 [STYLE]", lines[0].startswith("[STYLE] "), lines[0])
check("段落数 = 1 + 格数", len(lines) == 5, str(len(lines)))
check("PANEL 编号连续", all(lines[i].startswith("[PANEL %d] " % i) for i in range(1, 5)))
check("景别拼进 caption（英文子句）",
      lines[1].startswith("[PANEL 1] " + E.SIZE_EN["中景"] + ". "), lines[1])

lay = E.geometry(s)
ref = L.build_layout(panels=4, cols=2, rows=2, gutter=8, margin=8,
                     panel_edge=512, panel_aspect="16:9")
check("几何与 build_layout 一致", lay.spec() == ref.spec(), lay.spec())
check("canvas_size 与几何一致", E.canvas_size(s) == (lay.width, lay.height))
check("单张 layout_json 在 grid 下报错", raises(E.layout_json, s) is not None)

shot = s["items"][0]
sj = json.loads(E.shot_layout_json(s, shot, 496, 280))
check("逐格 JSON 只有一个 item", len(sj["items"]) == 1)
check("逐格 bbox 是整幅", sj["items"][0]["bbox"] == [-140, -248, 140, 248],
      str(sj["items"][0]["bbox"]))

print("\n== 4. 单张模式 ==")
s2 = E.parse_state(dump(single_state()))
p2 = E.compile_prompt(s2)
check("单张模式 prompt 只有 [STYLE]", p2.startswith("[STYLE] ") and "[PANEL" not in p2, p2)

lj = json.loads(E.layout_json(s2))
check("items 数 = 元素数", len(lj["items"]) == 2, str(len(lj["items"])))
check("text 元素带 text 字段", lj["items"][0].get("text") == "永兵传说")
check("text 元素带 palette", lj["items"][0].get("palette") == ["#c8a15a"])
check("character 无 text 字段", "text" not in lj["items"][1])
check("summary 是全局段", lj["summary"]["caption"] == "暗色雪原，飘雪，胶片颗粒。")
check("坐标空间标记", lj["bbox_coord_space"] == "abs_centralized"
      and lj["bbox_format"] == "yxyx")
check("bbox 落在画布内",
      all(-432 <= it["bbox"][0] and it["bbox"][2] <= 432 and
          -768 <= it["bbox"][1] and it["bbox"][3] <= 768 for it in lj["items"]),
      str([it["bbox"] for it in lj["items"]]))
check("canvas_size 用 canvas 字段", E.canvas_size(s2) == (1536, 864))

print("\n== 5. report ==")
r = E.report(E.parse_state(dump(grid_state())))
check("宫格 report 有行列", "4 格 = 2 列 × 2 行" in r, r.split("\n")[0])
check("宫格 report 有景别序列", "中景 → 近景 → 特写 → 全景" in r)

same = grid_state()
for it in same["items"]:
    it["size"] = "中景"
check("景别全同 → 警告", "⚠" in E.report(E.parse_state(dump(same))))

r2 = E.report(E.parse_state(dump(single_state())))
check("单张 report 有模式", "单张模式" in r2, r2.split("\n")[0])
check("单张 report 统计元素", "元素 2 个" in r2, r2)

risky = single_state()
risky["items"][0]["bbox"] = [0.08, 0.03, 0.92, 0.20]
check("文字贴顶 → 警告", "贴边" in E.report(E.parse_state(dump(risky))))

print("\n== 6. 预设 ==")
for name in E.PRESETS:
    st = P.build(name, grid={"cols": 3, "rows": 2} if name == "grid" else None)
    E.parse_state(dump(st))  # 必须能通过自己的校验
    if name == "grid":
        check("预设 %s 生成 6 格" % name, len(st["items"]) == 6, str(len(st["items"])))
        check("预设 %s 景别不重复" % name, len({i["size"] for i in st["items"]}) > 1)
    else:
        check("预设 %s 可用" % name, st["preset"] == name)

check("free 生成 0 元素", len(P.build("free")["items"]) == 0)
check("title-body 有 3 个元素", len(P.build("title-body")["items"]) == 3)
check("stack 刻意重叠", len(E._overlaps(P.build("stack")["items"])) > 0)
check("three-col 不重叠", len(E._overlaps(P.build("three-col")["items"])) == 0)
check("center 不重叠", len(E._overlaps(P.build("center")["items"])) == 0)
check("split-v 不重叠", len(E._overlaps(P.build("split-v")["items"])) == 0)
check("split-h 不重叠", len(E._overlaps(P.build("split-h")["items"])) == 0)
check("预设 id 唯一",
      len({i["id"] for i in P.build("three-col")["items"]}) == 3)
check("未知预设报错", raises(P.build, "nope") is not None)

st = E.parse_state(dump(P.build("title-body", canvas=(1536, 864))))
check("预设 state 能编译 layout_json", "items" in json.loads(E.layout_json(st)))

print("\n== 7. 默认值与前端一致 ==")
check("presets.build 默认画布 = 1536×864",
      P.build()["canvas"] == {"width": 1536, "height": 864},
      str(P.build().get("canvas")))
g8 = P.build("grid")
check("presets.build('grid') 默认 4×2 = 8 格",
      (g8["grid"]["cols"], g8["grid"]["rows"], len(g8["items"])) == (4, 2, 8),
      str((g8["grid"]["cols"], g8["grid"]["rows"], len(g8["items"]))))
check("默认宫格首格 bbox = [0,0,0.25,0.5]",
      [round(v, 4) for v in g8["items"][0]["bbox"]] == [0, 0, 0.25, 0.5],
      str(g8["items"][0]["bbox"]))
check("默认宫格 caption = 「第 1 格画面」",
      g8["items"][0]["caption"] == "第 1 格画面", g8["items"][0]["caption"])
check("默认宫格景别铺满 8 档",
      [it["size"] for it in g8["items"]] == list(E.SIZES))
check("n=1 用中景",
      P.build("grid", grid={"cols": 1, "rows": 1})["items"][0]["size"] == "中景")

# 与前端 `presetItems` 同式：floor(i*(8-1)/(n-1)+0.5)。逐项写死期望值，
# 防止有人改回 Python 的银行家舍入。
SIZE_CASES = {
    (1, 2): ["大远景", "大特写"],
    (3, 1): ["大远景", "中近景", "大特写"],
    (2, 2): ["大远景", "全景", "近景", "大特写"],
    (3, 2): ["大远景", "远景", "中景", "中近景", "特写", "大特写"],
    (3, 3): ["大远景", "远景", "全景", "中景", "中近景", "中近景", "近景", "特写", "大特写"],
}
for (cols, rows), want in SIZE_CASES.items():
    got = [it["size"] for it in P.build("grid", grid={"cols": cols, "rows": rows})["items"]]
    check("%d×%d 景别序列与前端一致" % (cols, rows), got == want, str(got))

print("\n== 8. 单张预设逐项对齐 PRESET_SPECS ==")
# (label, caption, bbox, palette, text)；palette=None / text=None 表示该键不存在。
WANT_PRESETS = {
    "split-v": [
        ("object", "主体", [0.03, 0.03, 0.485, 0.97], None, None),
        ("text", "粗体无衬线标题", [0.515, 0.03, 0.97, 0.97],
         ["#c8a15a"], "标题文字"),
    ],
    "split-h": [
        ("object", "主体", [0.03, 0.03, 0.97, 0.485], None, None),
        ("text", "细体说明文字", [0.03, 0.515, 0.97, 0.97],
         ["#ffffff"], "正文文字"),
    ],
    "title-body": [
        ("text", "粗体无衬线标题，浅金色", [0.08, 0.10, 0.92, 0.19],
         ["#c8a15a"], "标题文字"),
        ("character", "主角半身，侧对镜头", [0.10, 0.24, 0.90, 0.80], None, None),
        ("text", "细体小字，白色", [0.08, 0.84, 0.92, 0.90],
         ["#ffffff"], "副标题文字"),
    ],
    "three-col": [
        ("object", "主体（左）", [0.03, 0.03, 0.33, 0.97], None, None),
        ("object", "主体（中）", [0.35, 0.03, 0.65, 0.97], None, None),
        ("object", "主体（右）", [0.67, 0.03, 0.97, 0.97], None, None),
    ],
    "center": [
        ("character", "主体，居中，四周留白", [0.30, 0.18, 0.70, 0.82], None, None),
    ],
    "stack": [
        ("object", "背景元素", [0.05, 0.06, 0.95, 0.72], None, None),
        ("character", "前景主体", [0.28, 0.34, 0.72, 0.95], None, None),
    ],
    "free": [],
}
parity_bad = []
for name, want_rows in WANT_PRESETS.items():
    got_items = P.build(name)["items"]
    if len(got_items) != len(want_rows):
        parity_bad.append("%s 数量 %d != %d" % (name, len(got_items), len(want_rows)))
        continue
    for it, want in zip(got_items, want_rows, strict=True):
        got = (it["label"], it["caption"],
               [round(v, 4) for v in it["bbox"]], it.get("palette"), it.get("text"))
        if got != want:
            parity_bad.append("%s: %r != %r" % (name, got, want))
check("7 个单张预设 label/caption/bbox/palette 逐项一致", not parity_bad,
      str(parity_bad[:2]))
check("title-body 标题 y0=0.10 / 副标 y1=0.90",
      P.build("title-body")["items"][0]["bbox"][1] == 0.10
      and P.build("title-body")["items"][2]["bbox"][3] == 0.90)

print("\n== 9. 布局硬上限 ==")
for n in (3, 4, 6, 8, 9):
    check("%d 格正常（不受上限影响）" % n,
          L.build_layout(panels=n, panel_edge=512).panels == n)
msg = raises(L.build_layout, panels=145, panel_edge=512)
check("panels > 144 报错", msg and "上限" in msg, msg)
msg = raises(L.build_layout, panels=1_000_000, panel_edge=512)
check("panels = 1,000,000 报错", msg and "上限" in msg, msg)
msg = raises(L.build_layout, panels=13, cols=13)
check("显式 cols > 12 报错", msg and "不能超过" in msg, msg)
msg = raises(L.build_layout, panels=13, panel_edge=512)
check("13 格装不进 ≤12 轴 → 报错", msg and "上限" in msg, msg)
check("144 格 12×12 正常",
      L.build_layout(panels=144, cols=12, rows=12, panel_edge=128).panels == 144)

print("\n== 10. 占位守卫（预设文案没改就得出声） ==")
g = E.parse_state(dump(grid_state()))
s = E.parse_state(dump(single_state()))
check("宫格最终提示词 = compile_prompt", E.compile_final_prompt(g) == E.compile_prompt(g))
check("单张最终提示词 = layout_json", E.compile_final_prompt(s) == E.layout_json(s),
      E.compile_final_prompt(s)[:80])
check("手写 state 无占位", E.find_placeholders(g) == [], str(E.find_placeholders(g)))
check("手写 state 不报占位", E.placeholder_summary(g) is None)

pg = E.parse_state(dump(P.build("grid")))
ph = E.find_placeholders(pg)
check("预设宫格每格 caption 都算占位",
      len(ph) == len(pg["items"]) and all(f["field"] == "caption" and f["kind"] == "generic" for f in ph),
      str(ph[:2]))
check("预设宫格 report 报占位", "预设占位" in E.report(pg), E.report(pg))
check("英文宫格占位（Panel n）也认",
      len(E.find_placeholders({"items": [{"id": "x", "caption": "Panel 3"}]})) == 1)
check("正常描述不误伤",
      E.find_placeholders({"items": [{"id": "x", "caption": "Panel 3 号选手起身"}]}) == [])

pt = E.parse_state(dump(P.build("title-body")))
found = E.find_placeholders(pt)
check("预设单张文字框报 render 级（会被逐字渲染）",
      sum(1 for f in found if f["kind"] == "render") == 2, str(found))
check("预设单张 report 同时点出文案与描述",
      "预设文案" in E.report(pt) and "预设占位" in E.report(pt), E.report(pt))
many = single_state()
many["items"] = [{"id": "t%d" % k, "label": "text", "text": "标题文字",
                  "caption": "粗体无衬线标题", "bbox": [0.1, 0.1, 0.9, 0.2]}
                 for k in range(1, 6)]
many_sum = E.placeholder_summary(E.parse_state(dump(many)))
check("占位报位置不报裸 id，且只列前 3（计数仍是 5）",
      "5 个" in many_sum and "第 3 个" in many_sum and "第 4 个" not in many_sum
      and "t4" not in many_sum, many_sum)
gl = grid_state()
for k in (0, 2):
    gl["items"][k]["caption"] = "第 %d 格画面" % (k + 1)
gl_sum = E.placeholder_summary(E.parse_state(dump(gl)))
check("宫格占位按镜号报「第 N 格」", "第 1 格" in gl_sum and "第 3 格" in gl_sum
      and "s1" not in gl_sum, gl_sum)
check("改完文字后 render 级消失",
      all(f["kind"] != "render" for f in E.find_placeholders(
          {"items": [{"id": "t1", "label": "text", "text": "永兵传说",
                      "caption": "主体"}]})))

check("干净宫格没有忽略字段", E.find_ignored_grid_fields(g) == [])
check("单张模式不报忽略字段", E.find_ignored_grid_fields(s) == [])
mixed = grid_state()
mixed["items"][0]["label"] = "character"
mixed["items"][1]["label"] = "text"
mixed["items"][1]["text"] = "店内促销"
mixed["items"][2]["palette"] = ["#c8a15a"]
ig = E.find_ignored_grid_fields(E.parse_state(dump(mixed)))
check("宫格 label/text/palette 逐字段点名",
      sorted(f["fields"] for f in ig) == sorted([["label"], ["label", "text"], ["palette"]]),
      str(ig))
check("宫格 report 说明这些字段不进提示词",
      "宫格模式只消费" in E.report(E.parse_state(dump(mixed))),
      E.report(E.parse_state(dump(mixed))))
check("空 items / 脏输入不崩",
      E.find_placeholders({}) == [] and E.find_ignored_grid_fields({}) == []
      and len(E.find_placeholders({"items": [None, {"caption": 3}, {"id": "q"}]})) == 0)


print("\n== 11. 外接提示词 ==")
from core import intake as IN  # noqa: E402

g = E.parse_state(dump(grid_state()))
ext = ("[STYLE] 雨夜咖啡店\n\n[PANEL 1] 特写，眼睛里有霓虹\n"
       "[PANEL 2] 全景，街道全貌\n[PANEL 3] 近景，手在抖\n[PANEL 4] 大特写，杯沿")
st2, notes = IN.apply_to_state(g, ext)
caps = {it["no"]: (it["size"], it["caption"]) for it in st2["items"]}
check("外接稿接管逐格描述", caps[1] == ("特写", "眼睛里有霓虹"), str(caps[1]))
check("外接稿接管景别", caps[2] == ("全景", "街道全貌"), str(caps[2]))
check("外接稿接管全局描述", st2["style"] == "雨夜咖啡店", st2["style"])
check("外接稿不改入参", g["style"] == "雨夜咖啡店，湿街反光。"
      and g["items"][0]["caption"] == "苏晚坐在窗边", g["items"][0]["caption"])
check("外接稿 notes 出声", any("4" in n for n in notes), str(notes))

# 段数少于格数：前 K 格接管，其余沿用编辑器
few = "[STYLE] s\n\n[PANEL 1] 中景，只有一格"
st3, notes3 = IN.apply_to_state(g, few)
check("段数少于格数：只接管前 1 格",
      st3["items"][0]["caption"] == "只有一格" and st3["items"][1]["caption"] == "苏晚抬眼看镜头",
      str([it["caption"] for it in st3["items"]]))
check("段数少于格数：notes 说明", any("沿用" in n for n in notes3), str(notes3))

# 段数多于格数：尾部丢弃并出声
many = "[STYLE] s\n" + "\n".join("[PANEL %d] 近景，第 %d 段" % (i, i) for i in range(1, 7))
st4, notes4 = IN.apply_to_state(g, many)
check("段数多于格数：尾部丢弃并出声", any("丢弃" in n for n in notes4), str(notes4))

# 没写景别 ⇒ 沿用编辑器（v3 契约里 size 必填，不能清空）
nz = "[STYLE] s\n\n[PANEL 1] 只写描述不写景别"
st5, notes5 = IN.apply_to_state(g, nz)
check("外接稿没写景别 ⇒ 不注入（不拿编辑器景别拼回去）",
      st5["items"][0]["size"] == "", repr(st5["items"][0]["size"]))

# 无标记：只接管全局
nm = "一段没有分段的全局描述"
st6, notes6 = IN.apply_to_state(g, nm)
check("无 [PANEL] 标记 ⇒ 只接管全局",
      st6["style"] == nm and st6["items"][0]["caption"] == "苏晚坐在窗边", st6["style"])
check("无标记 notes 说明", any("没有" in n for n in notes6), str(notes6))
check("空外接稿不动 state", IN.apply_to_state(g, "   ")[0]["style"] == g["style"])

print("\n== 12. 景别与描述打架（只报警） ==")
clash = grid_state()
clash["items"][0]["size"] = "大远景"
clash["items"][0]["caption"] = "她面部占满画面，眼里有泪"
found = E.find_size_conflicts(E.parse_state(dump(clash)))
check("景别与描述冲突被点名", len(found) == 1 and found[0]["says"] == "特写", str(found))
check("冲突写进 report", "打架" in E.report(E.parse_state(dump(clash))),
      E.report(E.parse_state(dump(clash))))
clash["items"][0]["size"] = "特写"
check("相邻档不误报", E.find_size_conflicts(E.parse_state(dump(clash))) == [],
      str(E.find_size_conflicts(E.parse_state(dump(clash)))))

print("\n结果：通过 %d / 失败 %d" % (_ok, len(_bad)))
if _bad:
    print("失败项：")
    for b in _bad:
        print("  -", b)
    sys.exit(1)
