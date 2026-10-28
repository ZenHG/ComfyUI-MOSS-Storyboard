# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""零 GPU 回归测试：几何 / 提示词 / 像素。

不加载任何模型，不需要 CUDA。只依赖 torch + 本包 core。
跑法：`python tests/test_core.py`（在包目录下），或 `python H:/.../tests/test_core.py` 任意目录。
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch

from core import layout as L
from core import pixels as P
from core import prompt as T

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def expect_raise(name, fn, needle=""):
    try:
        fn()
    except Exception as e:
        ok = (needle in str(e)) if needle else True
        check(name, ok, "→ %s: %s" % (type(e).__name__, str(e).splitlines()[0][:100]))
        return
    check(name, False, "→ 未抛异常（应当 raise）")


def make_layout(panels=8, aspect="16:9", edge=512, canvas="auto", gutter=8, margin=8):
    return L.build_layout(panels=panels, gutter=gutter, margin=margin,
                          panel_edge=edge, panel_aspect=aspect, canvas_aspect=canvas)


print("== 1. 尺寸契约（16 对齐 / 格子不重叠 / 单格等大）==")
for panels in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12):
    for aspect in ("16:9", "9:16", "1:1", "4:3"):
        lay = make_layout(panels, aspect)
        tag = "%d格/%s" % (panels, aspect)
        check("%s 画布 16 对齐 %dx%d" % (tag, lay.width, lay.height),
              lay.width % 16 == 0 and lay.height % 16 == 0)
        check("%s 格数 = %d" % (tag, panels), len(lay.cells) == panels)
        check("%s 单格 16 对齐 %dx%d" % (tag, lay.panel_w, lay.panel_h),
              lay.panel_w % 16 == 0 and lay.panel_h % 16 == 0)
        inside = all(c.x >= lay.margin and c.y >= lay.margin
                     and c.x + c.w <= lay.width - lay.margin - lay.pad_x
                     and c.y + c.h <= lay.height - lay.margin - lay.pad_y for c in lay.cells)
        check("%s 全部格子在图内" % tag, inside)
        gaps = set()
        for c in lay.cells:
            right = [o for o in lay.cells if o.row == c.row and o.col == c.col + 1]
            down = [o for o in lay.cells if o.col == c.col and o.row == c.row + 1]
            if right:
                gaps.add(right[0].x - (c.x + c.w))
            if down:
                gaps.add(down[0].y - (c.y + c.h))
        check("%s 相邻缝宽恒为 gutter" % tag, gaps <= {8})

print("\n== 2. 布局选择（3/4/6/8/9 的默认排法）==")
expect = {
    (3, "16:9"): (3, 1),     # 竖条：3 行 1 列（3x1 会被 3:1 上限否掉）
    (4, "16:9"): (2, 2),
    (6, "16:9"): (2, 3),
    (8, "16:9"): (2, 4),
    (9, "16:9"): (3, 3),
}
for (panels, aspect), (rows, cols) in expect.items():
    lay = make_layout(panels, aspect)
    check("%d格 默认 = %d行%d列（实际 %d行%d列）" % (panels, rows, cols, lay.rows, lay.cols),
          (lay.rows, lay.cols) == (rows, cols), lay.note)

wide = make_layout(6, "16:9", canvas="9:16")
check("6格 指定 9:16 画布 → 竖向排布", wide.rows > wide.cols, "%d行%d列" % (wide.rows, wide.cols))
check("6格 指定 9:16 画布宽高比 <1", wide.height > wide.width, "%dx%d" % (wide.width, wide.height))

force = L.build_layout(panels=6, cols=6, rows=1, panel_edge=512, panel_aspect="16:9")
check("强制 6x1 生效", (force.rows, force.cols) == (1, 6))
expect_raise("6 格填 4 列 → 报错", lambda: L.build_layout(panels=6, cols=4, rows=0,
                                                       panel_edge=512, panel_aspect="16:9"), "整除")

print("\n== 3. spec 往返 ==")
lay = make_layout(8, "16:9")
back = L.parse_spec(lay.spec())
check("spec 往返几何一致",
      (back.width, back.height, back.cols, back.rows, back.panel_w, back.panel_h,
       back.gutter, back.margin, back.pad_x, back.pad_y)
      == (lay.width, lay.height, lay.cols, lay.rows, lay.panel_w, lay.panel_h,
          lay.gutter, lay.margin, lay.pad_x, lay.pad_y))
check("spec 往返格子坐标一致", [c.bbox_px() for c in back.cells] == [c.bbox_px() for c in lay.cells])
expect_raise("坏 spec → ValueError", lambda: L.parse_spec("hello"), "spec")
expect_raise("缺字段 spec → ValueError", lambda: L.parse_spec("MOSS-SB1 w=100"), "缺字段")
expect_raise("spec 单格超大 → ValueError",
             lambda: L.parse_spec("MOSS-SB1 panels=1 w=64 h=64 cols=1 rows=1 "
                                  "pw=999999999 ph=999999999 gutter=0 margin=0 padx=0 pady=0"),
             "越界")
expect_raise("spec 超长 → ValueError", lambda: L.parse_spec("MOSS-SB1 " + "panels=1 " * 600), "字符")
_pad = L.build_layout(panels=4, cols=2, rows=2, panel_w=250, panel_h=140, gutter=8, margin=8)
_bad_spec = _pad.spec().replace(" w=%d " % _pad.width, " w=%d " % (_pad.width + 16))
expect_raise("spec 的 w/h 与字段不自洽 → ValueError", lambda: L.parse_spec(_bad_spec), "自相矛盾")

print("\n== 4. 模板与边框复绘 ==")
tpl = P.render_template(lay, cell_gray=25)
check("模板形状 [1,H,W,3]", tuple(tpl.shape) == (1, lay.height, lay.width, 3), str(tuple(tpl.shape)))
c0 = lay.cells[0]
check("模板格内 = 25/255", abs(float(tpl[0, c0.y + 2, c0.x + 2, 0]) - 25 / 255) < 1e-6)
gx = lay.margin + lay.panel_w + 4            # 第一条竖缝中间
check("模板缝 = 1.0", abs(float(tpl[0, 10, gx, 0]) - 1.0) < 1e-6, "x=%d, val=%.3f" % (gx, float(tpl[0, 10, gx, 0])))
inv = P.render_template(lay, cell_gray=25, invert=True)
check("反相模板格内 = 1-25/255", abs(float(inv[0, c0.y + 2, c0.x + 2, 0]) - (1 - 25 / 255)) < 1e-6)
_huge = L.build_layout(panels=144, cols=12, rows=12, panel_edge=4096)
expect_raise("超大画布（1368MP）模板 → ValueError", lambda: P.render_template(_huge), "上限")

img = torch.rand(1, lay.height, lay.width, 3)
fixed = P.composite_borders(img, lay)
check("复绘后缝像素 = 1.0", abs(float(fixed[0, 10, gx, 0]) - 1.0) < 1e-6)
check("复绘后格外框 = 1.0", abs(float(fixed[0, 2, 2, 0]) - 1.0) < 1e-6)
check("复绘不动格内", torch.equal(fixed[0, c0.y + 2, c0.x + 2], img[0, c0.y + 2, c0.x + 2]))
expect_raise("复绘尺寸不符 → 报错",
             lambda: P.composite_borders(torch.rand(1, 64, 64, 3), lay), "不一致")

# 控制区域图（color_map）：颜色只是区域 ID，但两块必须成立 —— 尺寸对齐画布、中间没有黑洞
cmap = P.flat_color_map(lay)
check("color_map 形状 [1,H,W,3]", tuple(cmap.shape) == (1, lay.height, lay.width, 3),
      str(tuple(cmap.shape)))
check("color_map 盖满画布（缝与外边距不留黑）", float(cmap.min()) > 0.0, "%.3f" % float(cmap.min()))
check("color_map 每格一色（%d 格 %d 色）" % (lay.panels, lay.panels),
      len({tuple(cmap[0, c.y + 2, c.x + 2].tolist()) for c in lay.cells}) == lay.panels)
check("color_map 相邻格不同色",
      not torch.equal(cmap[0, lay.cells[0].y + 2, lay.cells[0].x + 2],
                      cmap[0, lay.cells[1].y + 2, lay.cells[1].x + 2]))
pal8 = ", ".join("#%02x0000" % (17 * i + 1) for i in range(lay.panels))
same = P.flat_color_map(lay, pal8)
check("给够颜色时每格一个 ID",
      len({tuple(same[0, c.y + 2, c.x + 2].tolist()) for c in lay.cells}) == lay.panels)
expect_raise("颜色少于格数 → 中文报错（ID 会重复）",
             lambda: P.flat_color_map(lay, "#ff0000, #00ff00, #0000ff"), "区域 ID")
expect_raise("颜色格式非法 → 中文报错", lambda: P.palette("#ff00", 1), "#rrggbb")

print("\n== 5. 裁格顺序与内容 ==")
img2 = torch.zeros(1, lay.height, lay.width, 3)
for c in lay.cells:
    img2[0, c.y:c.y + c.h, c.x:c.x + c.w, :] = (c.index + 1) / 100.0
panels = P.crop_panels(img2, lay)
check("裁出 %d 格" % lay.panels, panels.shape[0] == lay.panels, str(tuple(panels.shape)))
check("每格尺寸 = 单格尺寸",
      tuple(panels.shape[1:3]) == (lay.panel_h, lay.panel_w), str(tuple(panels.shape)))
ok = all(abs(float(panels[i, 1, 1, 0]) - (i + 1) / 100.0) < 1e-6 for i in range(lay.panels))
check("行优先顺序正确（第 i 张 = 第 i 格）", ok)

check("shave 后尺寸正确", tuple(P.crop_panels(img2, lay, shave=4).shape[1:3]) == (lay.panel_h - 8, lay.panel_w - 8))
expect_raise("shave 过大 → 报错", lambda: P.crop_panels(img2, lay, shave=999), "不足 16px")

sq = P.crop_panels(img2, lay, target_aspect=1.0, padding_mode="crop")
check("1:1 中心裁切 → 正方形（边长 = 短边）",
      sq.shape[1] == sq.shape[2] == lay.panel_h, str(tuple(sq.shape)))
pad = P.crop_panels(img2, lay, target_aspect=9 / 16, padding_mode="pad")
check("9:16 pad → 高度 > 原格", pad.shape[1] > lay.panel_h, str(tuple(pad.shape)))
rs = P.crop_panels(img2, lay, resize_edge=256)
check("resize_edge=256 → 长边 256", max(rs.shape[1], rs.shape[2]) == 256, str(tuple(rs.shape)))

prev = P.guide_preview(img2, lay)
check("预览线为纯红", abs(float(prev[0, 10, gx, 1]) - 0.0) < 1e-6 and abs(float(prev[0, 10, gx, 0]) - 1.0) < 1e-6)

print("\n== 5b. 贴回（crop_panels 的逆运算） ==")
mid = lay.panels // 2
cell = lay.cell(mid)
edited = torch.full((1, lay.panel_h, lay.panel_w, 3), 0.25)
back, notes = P.paste_panel(img2, lay, mid, edited)
check("贴回后画布尺寸不变", tuple(back.shape) == tuple(img2.shape), str(tuple(back.shape)))
check("目标格内容 == 贴入内容",
      torch.equal(back[0, cell.y:cell.y + cell.h, cell.x:cell.x + cell.w, :], edited[0]))
other = lay.cell(0 if mid != 0 else lay.panels - 1)
check("其它格一个字节都没动",
      torch.equal(back[0, other.y:other.y + other.h, other.x:other.x + other.w, :],
                  img2[0, other.y:other.y + other.h, other.x:other.x + other.w, :]))
seam_y = cell.y - 1 if cell.y > 0 else cell.y + cell.h
check("缝与外边距未触碰", torch.equal(back[0, seam_y, :, :], img2[0, seam_y, :, :]))
check("报告说明无重采样", any("无重采样" in n for n in notes))

# shave 往返：裁的时候缩了 s，贴回也要缩
sh = 6
shaved = P.crop_panels(img2, lay, shave=sh)
back2, _ = P.paste_panel(img2, lay, mid, shaved[mid] * 0.5, shave=sh)
check("shave=6 往返内容一致",
      torch.equal(back2[0, cell.y + sh:cell.y + cell.h - sh, cell.x + sh:cell.x + cell.w - sh, :],
                  shaved[mid] * 0.5))

# 尺寸不符 → 重采样 + 明确告知
odd = torch.rand(1, 100, 160, 3)
back3, notes3 = P.paste_panel(img2, lay, mid, odd)
check("尺寸不符 → 仍写满目标格",
      tuple(back3[0, cell.y:cell.y + cell.h, cell.x:cell.x + cell.w, :].shape) == tuple(edited[0].shape))
check("尺寸不符 → 报告重采样警告", any("重采样" in n for n in notes3), notes3[0][:40])
expect_raise("index 越界 → 报错", lambda: P.paste_panel(img2, lay, lay.panels, edited), "越界")
expect_raise("index 为负 → 报错", lambda: P.paste_panel(img2, lay, -1, edited), "越界")
expect_raise("panel 批次 >1 → 报错",
             lambda: P.paste_panel(img2, lay, mid, torch.rand(2, 8, 8, 3)), "一次只贴一格")

mk = P.paste_marker(img2, lay, mid)
check("marker 在目标格有红框", bool((mk[0, cell.y, cell.x:cell.x + cell.w, 0] > 0.99).all()))

print("\n== 6. 提示词解析 ==")
blob = ("[STYLE]: 雨夜咖啡店，35mm 胶片\n\n[PANEL 1]: 中景，两人对坐\n\n[PANEL 2]: 特写，苏晚眼睫\n\n"
        "[PANEL 3]: 过肩，陈屿抬眼")
head, caps = T.split_panels(blob)
check("全局段去掉 [STYLE] 标签", head.startswith("雨夜咖啡店") and "[STYLE]" not in head, repr(head[:20]))
check("解析出 3 段面板", len(caps) == 3)
check("面板编号顺序", [n for n, _ in caps] == [1, 2, 3])
check("面板 2 文本正确", caps[1][1] == "特写，苏晚眼睫", repr(caps[1][1]))

head2, caps2 = T.split_panels("只有一段普通提示词，没有任何标记")
check("无标记 → 全文当全局段", head2 == "只有一段普通提示词，没有任何标记" and caps2 == [])

marks = "[PANEL 3]: 三\n[PANEL 1]: 一"
_, caps3 = T.split_panels(marks)
check("乱序标记按编号排序", [n for n, _ in caps3] == [1, 3])

print("\n== 7. 段落与格子对齐 ==")
pairs, notes = T.pair_captions(caps, lay.cells)
check("3 段 8 格 → 只约束前 3 格", len(pairs) == 3, notes[0] if notes else "")
check("3 段 8 格 → 出声说明", any("后 5 格" in n for n in notes))
many = [(i, "第%d段" % i) for i in range(1, 13)]
pairs2, notes2 = T.pair_captions(many, lay.cells)
check("12 段 8 格 → 截到 8", len(pairs2) == 8, notes2[0] if notes2 else "")
check("12 段 8 格 → 出声说明", any("丢弃" in n for n in notes2))

print("\n== 8. 布局 JSON ==")
text = T.layout_json_text(head, pairs, lay.boxes_abs_centered(), label="object")
data = json.loads(text)
check("bbox_coord_space = abs_centralized", data["bbox_coord_space"] == "abs_centralized")
check("bbox_format = yxyx", data["bbox_format"] == "yxyx")
check("summary.caption = 全局段", data["summary"]["caption"] == head)
check("items 数 = 3", len(data["items"]) == 3)
check("item 结构完整", set(data["items"][0]) == {"label", "caption", "bbox"})
bx = data["items"][0]["bbox"]
hy, hx = lay.height // 2, lay.width // 2
check("首格 bbox 中心化正确",
      bx == [lay.margin - hy, lay.margin - hx,
             lay.margin + lay.panel_h - hy, lay.margin + lay.panel_w - hx],
      str(bx))
check("bbox 全部落在 |w/2|,|h/2| 内",
      all(abs(v) <= max(lay.width, lay.height) for it in data["items"] for v in it["bbox"]))
chars = T.layout_json_text(head, pairs, lay.boxes_abs_centered(), label="object", char_boxes="2")
check("character_boxes 生效", json.loads(chars)["items"][1]["label"] == "character")
check("无面板时退回纯文本", T.layout_json_text(head, [], []) == head)
expect_raise("非法 label → 报错",
             lambda: T.layout_json_text(head, pairs, lay.boxes_abs_centered(), label="region"), "label")

print("\n== 8b. 逐格提示词 ==")
plain = T.panel_prompt("4 个面板的故事板，雨夜咖啡店，35mm 胶片", "中景，两人对坐",
                       512, 288, init_mode="plain")
check("plain 保留全局段", "雨夜咖啡店" in plain and "35mm" in plain, plain)
check("plain 清掉裸计数多格措辞", "4 个面板" not in plain, plain)
check("plain 带本格描述", "两人对坐" in plain, plain)
panel_json = T.panel_prompt("雨夜咖啡店", "中景，两人对坐", 512, 288, init_mode="layout")
check("layout 模式给单格 JSON",
      json.loads(panel_json)["items"][0]["caption"].endswith("两人对坐"), panel_json[:80])

print("\n== 9. 分块约束（3 格 / 9 格 / 极端格数）==")
for panels in (3, 9):
    lay_i = make_layout(panels)
    ratio = lay_i.width / lay_i.height
    check("%d格 画布比例不极端（%.2f:1）" % (panels, ratio), 1 / 3 <= ratio <= 3)
check("12 格也能算", make_layout(12).width % 16 == 0)
expect_raise("0 格 → 报错", lambda: make_layout(0), "panels")

print("\n== 10. 浮点宽高比不产生漂移 ==")
for aspect in ("21:9", "2.39:1", "4:5", "3:2"):
    lay_f = make_layout(6, aspect)
    check("%s 单格 16 对齐" % aspect, lay_f.panel_w % 16 == 0 and lay_f.panel_h % 16 == 0,
          "%dx%d" % (lay_f.panel_w, lay_f.panel_h))
    check("%s 实际比例接近目标（±8%%）" % aspect,
          abs(math.log((lay_f.panel_w / lay_f.panel_h) / L.ASPECTS[aspect])) < 0.08,
          "%.3f vs %.3f" % (lay_f.panel_w / lay_f.panel_h, L.ASPECTS[aspect]))

print("\n== 11. 宫格声明（全局段有没有说「分成 N 格」）==")
# 模型是按**语义**画宫格的：examples/ 里验证过的配方全局段都带这句，删掉就不按格分配。
_declared = "[STYLE]: 单一故事板画布分割为严格对称网格，恰好 4 个面板，每格一格\n[PANEL 1]: x"
_bare = "[STYLE]: 雨夜咖啡店，暖黄吊灯\n[PANEL 1]: 大远景，两人对坐"
check("声明了宫格 → True", T.grid_declared(_declared) is True)
check("没声明 → False", T.grid_declared(_bare) is False)
check("无 [PANEL] 分段 → None（纯文生图不判）",
      T.grid_declared("[STYLE]: 雨夜咖啡店，暖黄吊灯") is None)
check("英文计数也算声明", T.grid_declared("[STYLE]: a storyboard split into 4 panels\n[PANEL 1]: x")
      is True)

print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
if "pytest" not in sys.modules:
    sys.exit(1 if FAIL else 0)
