# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""恶意 / 极端输入回归（零 GPU、零宿主、零依赖）。

覆盖正常用户碰不到、但手改 JSON / 别家 planner / 恶意工作流能送进来的形态：
JSON 常量（NaN / Infinity）、整值浮点、原型链名字、零宽字符、量化塌缩、
深层嵌套、扫描预算、超窄 bbox 导出。

跑法：python tests/test_adversarial.py
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import editor as E
from core import extract as X
from core import interop as I
from core import layout as L
from core import planner as PL

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
    except Exception as exc:
        return "!!%s: %s" % (type(exc).__name__, exc)
    return None


def expect(name, fn, needle=""):
    msg = raises(fn)
    ok = msg is not None and not msg.startswith("!!") and (needle in msg if needle else True)
    check(name, ok, msg or "未抛异常")


def state_json(**over):
    base = {"version": 3, "preset": "free", "style": "",
            "canvas": {"width": 1536, "height": 864}, "items": []}
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


def item(ident="o1", label="object", caption="主体", bbox=None, **extra):
    it = {"id": ident, "label": label, "caption": caption,
          "bbox": bbox if bbox is not None else [0.1, 0.1, 0.9, 0.9]}
    if label == "text":
        it["text"] = "字"
    it.update(extra)
    return it


def edoc(elements, **extra):
    doc = {"version": I.ELEMENTS_VERSION, "elements": elements}
    doc.update(extra)
    return doc


def main():
    print("== 1. JSON 常量与数值口径 ==")
    expect("字面 NaN 拒绝", lambda: E.parse_state(
        '{"version":3,"preset":"free","style":"","canvas":{"width":1536,"height":864},'
        '"x_ext":{"a":NaN},"items":[]}'), "NaN")
    expect("字面 Infinity 拒绝", lambda: E.parse_state(
        '{"version":3,"preset":"free","style":"","canvas":{"width":1536,"height":864},'
        '"x_ext":{"a":Infinity},"items":[]}'), "Infinity")
    expect("bbox 溢出成 inf 拒绝", lambda: E.parse_state(
        '{"version":3,"preset":"free","style":"","canvas":{"width":1536,"height":864},'
        '"items":[{"id":"o1","label":"object","caption":"主体","bbox":[0,0,1e400,1]}]}'),
        "NaN / Infinity")
    expect("非整浮点 canvas 拒绝", lambda: E.parse_state(state_json(
        canvas={"width": 1536.5, "height": 864})), "canvas.width")
    expect("1e400 canvas 拒绝", lambda: E.parse_state(
        '{"version":3,"preset":"free","style":"","canvas":{"width":1e400,"height":864},"items":[]}'),
        "canvas.width")
    expect("bool 当 version 拒绝", lambda: E.parse_state(state_json(version=True)), "版本")
    st = E.parse_state(state_json(version=3.0, canvas={"width": 1536.0, "height": 864.0}))
    check("整值浮点 version / canvas 接受", st["version"] == 3
          and isinstance(st["canvas"]["width"], int) and st["canvas"]["width"] == 1536,
          repr(st["canvas"]))
    gst = E.parse_state(state_json(preset="grid", grid={
        "cols": 1.0, "rows": 1.0, "panel_aspect": "16:9", "panel_edge": 512.0,
        "gutter": 8.0, "margin": 8.0}, items=[item(no=1.0, size="中景")]))
    check("整值浮点宫格字段接受并归一",
          isinstance(gst["grid"]["panel_edge"], int) and isinstance(gst["items"][0]["no"], int),
          repr(gst["grid"]))

    print("== 2. 深层嵌套 ==")
    deep = ('{"version":3,"preset":"free","style":"","canvas":{"width":1536,"height":864},'
            '"x_ext":{"a":' + "[" * 20000 + "]" * 20000 + '},"items":[]}')
    expect("2 万层嵌套明确报错（不是 RecursionError）", lambda: E.parse_state(deep), "不是合法 JSON")

    print("== 3. elements 互操作 ==")
    for role in ("toString", "constructor", "__proto__"):
        expect("role=%s 拒绝（不走原型链）" % role,
               lambda r=role: I.to_state(edoc([{"role": r, "desc": "d", "bbox": [0, 0, 10, 10]}])), "不认识")
    expect("z=NaN 拒绝", lambda: I.to_state(edoc([{
        "role": "object", "desc": "d", "bbox": [0, 0, 10, 10], "z": float("nan")}])), "z 必须")
    expect("z=Infinity 拒绝", lambda: I.to_state(edoc([{
        "role": "object", "desc": "d", "bbox": [0, 0, 10, 10], "z": float("inf")}])), "z 必须")
    expect("bbox=NaN 拒绝", lambda: I.to_state(edoc([{
        "role": "object", "desc": "d", "bbox": [0, 0, float("nan"), 10]}])), "NaN / Infinity")
    st = I.to_state(edoc([{"role": "object", "desc": "d", "bbox": [0.0, 0.0, 1000.0, 1000.0]}],
                         canvas={"width": 1536.0, "height": 864.0}))
    check("bbox / canvas 整值浮点接受", st["canvas"]["width"] == 1536
          and st["items"][0]["bbox"] == [0.0, 0.0, 1.0, 1.0], repr(st))
    expect("x_ext 里的 NaN 拒绝", lambda: I.to_state(edoc([{
        "role": "object", "desc": "d", "bbox": [0, 0, 10, 10],
        "x_ext": {"v": float("nan")}}])), "无法序列化")
    proto = I.to_state(json.loads(
        '{"version":1,"elements":[{"role":"object","desc":"d","bbox":[0,0,10,10],'
        '"x_ext":{"__proto__":{"polluted":1},"keep":2}}],"x_ext":{"__proto__":{"p":1},"doc":3}}'))
    check("原型污染键丢弃、安全键保留",
          "keep" in proto["items"][0]["x_ext"] and "doc" in proto["x_ext"]
          and "__proto__" not in proto["items"][0]["x_ext"]
          and "__proto__" not in proto["x_ext"], repr(proto))
    expect("重复 id 拒绝", lambda: I.to_state(edoc([
        {"role": "object", "desc": "a", "bbox": [0, 0, 10, 10], "id": "x"},
        {"role": "object", "desc": "b", "bbox": [0, 0, 10, 10], "id": "x"}])), "重复")
    expect("非 text 带 text 拒绝", lambda: I.to_state(edoc([
        {"role": "object", "desc": "d", "text": "x", "bbox": [0, 0, 10, 10]}])), "不支持 text")
    expect("零宽 desc 拒绝", lambda: I.to_state(edoc([
        {"role": "object", "desc": "\u200b\u202e", "bbox": [0, 0, 10, 10]}])), "desc")
    expect("零宽 text 拒绝", lambda: I.to_state(edoc([
        {"role": "text", "desc": "d", "text": "\u2060\ufeff", "bbox": [0, 0, 10, 10]}])), "text")

    print("== 4. 导出量化不塌缩 ==")
    thin = E.parse_state(state_json(items=[item(
        bbox=[0.5, 0.5, 0.5000001, 0.5000001])]))
    exp = I.from_state(thin)
    check("超窄 bbox 导出补到 0.001", exp["elements"][0]["bbox"] == [500.0, 500.0, 501.0, 501.0],
          repr(exp["elements"][0]["bbox"]))
    back = I.to_state(exp)
    check("超窄 bbox 导出可再导入", len(back["items"]) == 1, repr(back["items"]))
    edge = E.parse_state(state_json(items=[item(
        bbox=[0.9999999, 0.9999999, 0.99999995, 0.99999995])]))
    check("右/下边界不越界", I.from_state(edge)["elements"][0]["bbox"] == [999, 999, 1000, 1000],
          repr(I.from_state(edge)["elements"][0]["bbox"]))
    normal = E.parse_state(state_json(items=[item(bbox=[0.1, 0.2, 0.3, 0.4])]))
    check("正常 bbox 量化不变", I.from_state(normal)["elements"][0]["bbox"] == [100.0, 200.0, 300.0, 400.0],
          repr(I.from_state(normal)["elements"][0]["bbox"]))
    negz = E.parse_state(state_json(items=[item(bbox=[-0.0, 0.0, 0.5, 0.5])]))
    check("-0.0 导出成 0", I.from_state(negz)["elements"][0]["bbox"][0] == 0,
          repr(I.from_state(negz)["elements"][0]["bbox"]))

    rng = random.Random(20261026)
    items = []
    for i in range(200):
        x0 = rng.randint(0, 900000) / 1_000_000
        y0 = rng.randint(0, 900000) / 1_000_000
        w = rng.randint(1, 99999) / 1_000_000
        h = rng.randint(1, 99999) / 1_000_000
        bbox = [x0, y0, min(1.0, x0 + w), min(1.0, y0 + h)]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        items.append(item("o%d" % i, bbox=bbox))
    bulk = E.parse_state(state_json(items=items))
    bulk_exp = I.from_state(bulk)
    bulk_back = I.to_state(bulk_exp)
    all_ok = len(bulk_back["items"]) == len(items) and all(
        it["bbox"][0] < it["bbox"][2] <= 1000 and it["bbox"][1] < it["bbox"][3] <= 1000
        for it in bulk_exp["elements"])
    check("200 组随机（含超窄）导出→再导入全部合法", all_ok,
          "%d / %d" % (len(bulk_back["items"]), len(items)))

    print("== 5. Unicode ==")
    cap = "😀 e\u0301 שלום مرحبا \u200b"
    uni = I.to_state(edoc([{"role": "text", "desc": cap, "text": "字😀\u200b",
                            "bbox": [0, 0, 1000, 1000]}]))
    uni_back = I.to_state(I.from_state(uni))
    check("emoji / RTL / 组合字符 / 零宽（非纯零宽）保留",
          uni_back["items"][0]["caption"] == cap and uni_back["items"][0]["text"] == "字😀\u200b",
          repr(uni_back["items"][0]))
    expect("纯空白 caption 拒绝", lambda: I.to_state(edoc([
        {"role": "object", "desc": " \t\n", "bbox": [0, 0, 10, 10]}])), "desc")
    expect("纯零宽 caption 拒绝", lambda: E.parse_state(state_json(items=[item(caption="\u200b\u200f")])),
           "caption")

    print("== 6. JSON 提取 ==")
    check("跳过 NaN 候选取下一个", X.extract_json('{"a": NaN} {"b": 2}') == {"b": 2})
    expect("只有 NaN 报错", lambda: X.extract_json('{"a": NaN}'), "没有找到")
    expect("Infinity 报错", lambda: X.extract_json('{"a": Infinity}'), "没有找到")
    check("字符串里的 NaN 是普通文本", X.extract_json('{"a": "NaN"}') == {"a": "NaN"})
    expect("非字符串输入拒绝", lambda: X.extract_json({"a": 1}), "字符串")
    expect("65 层嵌套拒绝", lambda: X.extract_json('{"a":' + "[" * 65 + "]" * 65 + "}"), "嵌套")
    expect("33 个坏候选拒绝", lambda: X.extract_json("{oops}" * 33), "开括号")
    spaced = "".join("{" + "x" * 90000 for _ in range(10)) + "y" * 90000
    expect("未闭合括号触发扫描预算", lambda: X.extract_json(spaced), "扫描")

    print("== 7. brief 模板 ==")
    expect("缺 title", lambda: PL.brief_to_elements({}), "title")
    expect("空白 title", lambda: PL.brief_to_elements({"title": "   "}), "title")
    expect("纯零宽 title", lambda: PL.brief_to_elements({"title": "\u200b"}), "title")
    expect("title 非字符串", lambda: PL.brief_to_elements({"title": 1}), "title")
    expect("subtitle+body 冲突", lambda: PL.brief_to_elements(
        {"title": "t", "subtitle": "s", "body": "b"}), "只能给一个")
    expect("未知 layout", lambda: PL.brief_to_elements({"title": "t", "layout": "x"}), "layout")
    expect("style 非字符串", lambda: PL.brief_to_elements({"title": "t", "style": 1}), "style")
    expect("palette 非数组", lambda: PL.brief_to_elements({"title": "t", "palette": "red"}), "palette")
    expect("palette 颜色非法", lambda: PL.brief_to_elements(
        {"title": "t", "palette": ["red"]}), "palette")
    expect("未知 canvas", lambda: PL.brief_to_elements({"title": "t", "canvas": "5:4"}), "canvas")
    expect("canvas 尺寸非法", lambda: PL.brief_to_elements(
        {"title": "t", "canvas": {"width": 100, "height": 864}}), "canvas.width")
    good = PL.brief_to_elements({"title": "😀 标题", "body": "副标题", "subject": "主体"})
    check("正常 brief 三元素且过契约", len(good["elements"]) == 3
          and I.to_state(good)["items"][0]["text"] == "😀 标题", repr(good)[:200])
    expect("超长 title 在 state 上限处拒绝", lambda: PL.brief_to_elements(
        {"title": "字" * 1_000_050}), "上限")

    print("== 8. spec 边界 ==")
    expect("负 gutter 拒绝", lambda: L.parse_spec(
        "MOSS-SB1 panels=1 w=64 h=64 cols=1 rows=1 pw=64 ph=64 gutter=-1 margin=0"), "越界")
    expect("padx=16 拒绝", lambda: L.parse_spec(
        "MOSS-SB1 panels=1 w=64 h=64 cols=1 rows=1 pw=64 ph=64 gutter=0 margin=0 padx=16"), "越界")
    # 16 的倍数：手写 spec 最容易漏的一条（VAE 按 16 倍下采样，非整倍数会被静默截断）
    expect("pw=84（非 16 倍数）拒绝", lambda: L.parse_spec(
        "MOSS-SB1 panels=1 w=84 h=84 cols=1 rows=1 pw=84 ph=84 gutter=0 margin=0"),
        "16 的倍数")
    expect("w=100（非 16 倍数）拒绝", lambda: L.parse_spec(
        "MOSS-SB1 panels=1 w=100 h=80 cols=1 rows=1 pw=80 ph=80 gutter=10 margin=0"),
        "16 的倍数")
    # 反向：节点自己生成的 spec 必须一条都不能被新校验误伤
    for kw in ({"panels": 4, "panel_edge": 496, "panel_aspect": "16:9"},
               {"panels": 9, "panel_edge": 512, "panel_aspect": "1:1"},
               {"panels": 6, "panel_edge": 544, "panel_aspect": "9:16", "canvas_aspect": "9:16"},
               {"panels": 3, "panel_edge": 496, "panel_aspect": "16:9"}):
        lay = L.build_layout(**kw)
        back = L.parse_spec(lay.spec())
        check("生成的 spec 可回读（%d 格 %s）" % (kw["panels"], kw["panel_aspect"]),
              (back.width, back.height) == (lay.width, lay.height),
              "%d×%d" % (back.width, back.height))
    expect("panels 超上限拒绝", lambda: L.parse_spec(
        "MOSS-SB1 panels=99999999999999999999 w=64 h=64 cols=1 rows=1 "
        "pw=64 ph=64 gutter=0 margin=0"), "上限")
    big = L.build_layout(panels=144, cols=12, rows=12, panel_edge=512)
    check("144 格 spec 可回读", L.parse_spec(big.spec()).panels == 144, big.spec()[:80])
    check("144 格画布仍低于 64MP", big.width * big.height <= L.MAX_CANVAS_PIXELS,
          "%d x %d" % (big.width, big.height))

    print("\n结果：通过 %d / 失败 %d" % (_ok, len(_bad)))
    if _bad:
        print("失败项：")
        for b in _bad:
            print("  -", b)
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
