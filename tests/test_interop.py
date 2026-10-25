# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""elements-v1 互操作层机检（零 GPU、零宿主）。

覆盖：映射 / 别名 / id / z 序 / 未知字段 / 错误路径 / 往返保真 / CLI 退出码。
"""

import json
import os
import random
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import editor as E   # noqa: E402
from core import interop as I  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "check_elements.py")

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


def doc(elements, **kw):
    out = {"version": 1, "elements": elements}
    out.update(kw)
    return out


def el(role="object", bbox=(100, 100, 900, 900), desc="主体", **kw):
    out = {"role": role, "desc": desc, "bbox": list(bbox)}
    out.update(kw)
    return out


def test_mapping():
    st = I.to_state(doc([el()]))
    check("默认预设 free", st["preset"] == "free", st.get("preset"))
    check("默认画布 1536×864", st["canvas"] == {"width": 1536, "height": 864}, st["canvas"])
    check("生成的 state 过契约校验", E.parse_state(json.dumps(st)) == st)
    check("bbox 0–1000 → 0–1", st["items"][0]["bbox"] == [0.1, 0.1, 0.9, 0.9], st["items"][0]["bbox"])
    check("desc → caption", st["items"][0]["caption"] == "主体")
    check("role → label", st["items"][0]["label"] == "object")

    st = I.to_state(doc([el(role="text", text="标题", palette=["#c8a15a"])],
                        canvas={"width": 1024, "height": 1536}, style="胶片"),
                    preset="title-body")
    check("自定义画布", st["canvas"] == {"width": 1024, "height": 1536}, st["canvas"])
    check("文档 style 透传", st["style"] == "胶片")
    check("caller 覆盖 style", I.to_state(doc([]), style="x")["style"] == "x")
    check("text 元素带 text", st["items"][0]["text"] == "标题")
    check("palette 透传", st["items"][0]["palette"] == ["#c8a15a"])
    check("preset 透传", st["preset"] == "title-body")


def test_roles():
    check("别名 obj", I.to_state(doc([el(role="obj")]))["items"][0]["label"] == "object")
    check("别名 person", I.to_state(doc([el(role="person")]))["items"][0]["label"] == "character")
    check("type 可当 role", I.to_state(doc([{"type": "text", "text": "字", "desc": "d",
                                             "bbox": [0, 0, 10, 10]}]))["items"][0]["label"] == "text")
    msg = raises(I.to_state, doc([{"role": "person", "type": "obj", "desc": "d",
                                   "bbox": [0, 0, 10, 10]}]))
    check("role 与 type 冲突报错", msg and "冲突" in msg, msg)
    msg = raises(I.to_state, doc([{"role": "background", "desc": "d", "bbox": [0, 0, 10, 10]}]))
    check("未知 role 报错", msg and "不认识" in msg, msg)
    msg = raises(I.to_state, doc([{"desc": "d", "bbox": [0, 0, 10, 10]}]))
    check("缺 role 报错", msg and "缺 role" in msg, msg)


def test_fields():
    st = I.to_state(doc([{"role": "text", "caption": "样式", "text": "字",
                          "bbox": [0, 0, 10, 10]}]))
    check("caption 可当 desc", st["items"][0]["caption"] == "样式")
    msg = raises(I.to_state, doc([{"role": "object", "bbox": [0, 0, 10, 10]}]))
    check("缺 desc 报错", msg and "缺 desc" in msg, msg)
    msg = raises(I.to_state, doc([el(desc="  ")]))
    check("desc 空白报错", msg and "缺 desc" in msg, msg)
    msg = raises(I.to_state, doc([el(role="text")]))
    check("text 元素缺 text 报错", msg and "必须填 text" in msg, msg)
    msg = raises(I.to_state, doc([el(role="text", text=" ")]))
    check("text 空白报错", msg and "必须填 text" in msg, msg)
    msg = raises(I.to_state, doc([el(text="不该有")]))
    check("非 text 带 text 报错", msg and "不支持 text" in msg, msg)
    msg = raises(I.to_state, doc([el(palette=["#111111"] * 6)]))
    check("palette 超 5 色报错", msg and "最多 5" in msg, msg)
    msg = raises(I.to_state, doc([el(palette=["#12345"])]))
    check("palette 非 6 位报错", msg and "#rrggbb" in msg, msg)


def test_bbox():
    st = I.to_state(doc([el(bbox=(0, 0, 1000, 1000))]))
    check("bbox 端点 0 / 1000", st["items"][0]["bbox"] == [0.0, 0.0, 1.0, 1.0],
          st["items"][0]["bbox"])
    for name, bbox, key in [
        ("反向", (900, 100, 100, 900), "越界或反向"),
        ("超 1000", (0, 0, 1000.1, 100), "越界或反向"),
        ("负值", (-1, 0, 100, 100), "越界或反向"),
        ("零宽", (100, 100, 100, 200), "越界或反向"),
        ("字符串", ("0", 0, 100, 100), "全是数字"),
        ("布尔", (True, 0, 100, 100), "全是数字"),
        ("三个数", (0, 0, 100), "必须是 4 个数"),
    ]:
        msg = raises(I.to_state, doc([el(bbox=bbox)]))
        check("bbox %s 报错" % name, msg and key in msg, msg)


def test_ids():
    st = I.to_state(doc([el(id="main", role="character"),
                         el(id="title", role="text", text="字")]))
    check("合法 id 保留", [it["id"] for it in st["items"]] == ["main", "title"])
    st = I.to_state(doc([el(role="text", text="字"), el(role="text", text="字"),
                         el(role="object")]))
    check("缺 id 按 role 生成", [it["id"] for it in st["items"]] == ["t1", "t2", "o1"],
          [it["id"] for it in st["items"]])
    check("生成 id 可复现", I.to_state(doc([el(role="text", text="字")]))["items"][0]["id"] == "t1")
    msg = raises(I.to_state, doc([el(id="a"), el(id="a")]))
    check("重复 id 报错", msg and "重复" in msg, msg)
    msg = raises(I.to_state, doc([el(id=5)]))
    check("id 类型错报错", msg and "非空字符串" in msg, msg)
    msg = raises(I.to_state, doc([el(id="")]))
    check("id 空串报错", msg and "非空字符串" in msg, msg)


def test_z():
    st = I.to_state(doc([el(id="c", z=2), el(id="a", z=0), el(id="b", z=1)]))
    check("按 z 升序", [it["id"] for it in st["items"]] == ["a", "b", "c"],
          [it["id"] for it in st["items"]])
    st = I.to_state(doc([el(id="b"), el(id="a")]))
    check("无 z 保持数组顺序", [it["id"] for it in st["items"]] == ["b", "a"])
    st = I.to_state(doc([el(id="b", z=1), el(id="a", z=1), el(id="c", z=0)]))
    check("同 z 稳定", [it["id"] for it in st["items"]] == ["c", "b", "a"])
    msg = raises(I.to_state, doc([el(z="top")]))
    check("z 非数字报错", msg and "z 必须是数字" in msg, msg)


def test_limits():
    msg = raises(I.to_state, doc([el()] * (I.MAX_ELEMENTS + 1)))
    check("超过 256 元素报错", msg and "超过上限" in msg, msg)
    msg = raises(I.to_state, {"version": 2, "elements": []})
    check("elements 版本闸", msg and "只认 1" in msg, msg)
    msg = raises(I.to_state, {"version": 1})
    check("缺 elements 报错", msg and "elements 数组" in msg, msg)
    msg = raises(I.to_state, [])
    check("文档不是对象报错", msg and "JSON 对象" in msg, msg)
    msg = raises(I.to_state, doc([el()]), preset="grid")
    check("grid 预设拒绝", msg and "grid 不支持导入" in msg, msg)
    msg = raises(I.to_state, doc([el()]), preset="nope")
    check("未知预设拒绝", msg and "不认识" in msg, msg)


def test_unknown():
    st = I.to_state(doc([el(weight=3, note="x")]))
    check("元素未知字段进 x_ext", st["items"][0].get("x_ext") == {"weight": 3, "note": "x"},
          st["items"][0].get("x_ext"))
    back = I.from_state(st)
    check("导出带回 x_ext", back["elements"][0].get("x_ext") == {"weight": 3, "note": "x"})
    st2 = I.to_state(back)
    check("x_ext 二次导入稳定", st2["items"][0].get("x_ext") == {"weight": 3, "note": "x"})

    st = I.to_state(doc([el()], x_ext={"a": 1}, tool="planner-x"))
    check("文档未知字段进 state.x_ext",
          st.get("x_ext") == {"a": 1, "tool": "planner-x"}, st.get("x_ext"))
    back = I.from_state(st)
    check("导出还原文档未知字段", back.get("a") == 1 and back.get("tool") == "planner-x", back)

    msg = raises(I.to_state, doc([el(x_ext=[1, 2])]))
    check("x_ext 非对象报错", msg and "x_ext 必须是对象" in msg, msg)


def test_export():
    st = E.empty_state("free")
    st["canvas"] = {"width": 1024, "height": 1024}
    st["style"] = "夜晚"
    st["items"] = [
        {"id": "t1", "label": "text", "caption": "标题", "text": "字",
         "bbox": [0.123, 0.1, 0.9, 0.2], "palette": ["#ffffff"]},
        {"id": "o1", "label": "object", "caption": "主体", "bbox": [0.2, 0.3, 0.8, 0.9]},
    ]
    back = I.from_state(st)
    check("导出画布", back["canvas"] == {"width": 1024, "height": 1024}, back["canvas"])
    check("导出 style", back["style"] == "夜晚")
    check("导出 bbox 0–1000", back["elements"][0]["bbox"] == [123.0, 100.0, 900.0, 200.0],
          back["elements"][0]["bbox"])
    check("导出文本", back["elements"][0]["text"] == "字")
    check("非 text 不带 text", "text" not in back["elements"][1])
    check("导出顺序 = 数组顺序", [e["id"] for e in back["elements"]] == ["t1", "o1"])
    st2 = I.to_state(back)
    check("state → elements → state 往返", st2["items"] == st["items"],
          json.dumps(st2["items"], ensure_ascii=False)[:300])
    check("往返保留 preset", st2["preset"] == st["preset"])

    msg = raises(I.from_state, E.empty_state("grid"))
    check("grid state 不能导出", msg and "不能导出" in msg, msg)

    st3 = E.empty_state("free")
    st3["items"] = [{"id": "o1", "label": "object", "caption": "d",
                     "bbox": [0.12345, 0.2, 0.8, 0.9]}]
    st4 = I.to_state(I.from_state(st3))
    check("导出量化到 0.001（误差 ≤ 0.0005）",
          abs(st4["items"][0]["bbox"][0] - 0.12345) <= 0.0005,
          st4["items"][0]["bbox"])


def test_random_roundtrip():
    rng = random.Random(20261026)
    roles = ["text", "object", "character"]
    failed = None
    for n in range(100):
        elements = []
        for i in range(rng.randint(0, 12)):
            role = rng.choice(roles)
            x0 = rng.randint(0, 900)
            y0 = rng.randint(0, 900)
            x1 = rng.randint(x0 + 1, 1000)
            y1 = rng.randint(y0 + 1, 1000)
            e = {"role": role, "desc": "元素 %d" % i, "bbox": [x0, y0, x1, y1]}
            if role == "text":
                e["text"] = "字%d" % i
            if rng.random() < 0.3:
                e["palette"] = ["#c8a15a"]
            if rng.random() < 0.2:
                e["id"] = "p%d" % i
            if rng.random() < 0.1:
                e["note"] = "n%d" % i
            elements.append(e)
        st = I.to_state(doc(elements))
        doc2 = I.from_state(st)
        st2 = I.to_state(doc2)
        if st2["items"] != st["items"]:
            failed = (n, st["items"], st2["items"])
            break
        for e, out in zip(elements, doc2["elements"], strict=True):
            want = [float(v) for v in e["bbox"]]
            got = out["bbox"]
            if any(abs(a - b) > 0.0005 for a, b in zip(want, got, strict=True)):
                failed = (n, e, out)
                break
        if failed:
            break
    check("100 组随机布局往返一致", failed is None, json.dumps(failed, ensure_ascii=False)[:400])


def test_compile():
    st = I.to_state(doc([el(role="text", text="标题", bbox=(80, 100, 920, 190)),
                         el(role="character", bbox=(100, 240, 900, 800))],
                        style="电影感"))
    payload = E.layout_json(st)
    data = json.loads(payload)
    check("互操作 state 能编译", data["items"] and data["summary"]["caption"] == "电影感")
    check("编译元素数一致", len(data["items"]) == 2)
    check("编译 bbox 是像素", max(abs(v) for v in data["items"][0]["bbox"]) > 1,
          data["items"][0]["bbox"])


def test_sniff():
    check("sniff elements", I.sniff(doc([])) == "elements")
    check("sniff state", I.sniff(E.empty_state()) == "state")
    check("sniff 其它", I.sniff({"version": 3}) is None)
    check("to_elements_json 幂等", I.to_elements_json(E.empty_state()) == I.from_state(E.empty_state()))


def run_tool(args, stdin=None):
    return subprocess.run([sys.executable, TOOL] + args, cwd=ROOT, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_cli():
    with tempfile.TemporaryDirectory(prefix="moss_iface_") as tmp:
        good = os.path.join(tmp, "good.json")
        with open(good, "w", encoding="utf-8") as fh:
            json.dump(doc([el(role="text", text="标题")]), fh)
        r = run_tool([good])
        check("CLI 合法文件退出 0", r.returncode == 0, r.stderr[:200])
        check("CLI 打印摘要", "OK" in r.stdout and "单张模式" in r.stdout, r.stdout[:200])

        r = run_tool([good, "--emit", "state"])
        ok = r.returncode == 0
        try:
            parsed = json.loads(r.stdout)
        except ValueError:
            parsed = None
        check("CLI --emit state 输出可解析", ok and parsed and len(parsed["items"]) == 1,
              r.stderr[:200])
        check("CLI --emit state 过校验", parsed and E.parse_state(json.dumps(parsed)) == parsed)

        st_path = os.path.join(tmp, "state.json")
        with open(st_path, "w", encoding="utf-8") as fh:
            json.dump(E.empty_state(), fh)
        r = run_tool([st_path, "--emit", "elements"])
        check("CLI state → elements", r.returncode == 0 and json.loads(r.stdout)["version"] == 1,
              r.stderr[:200])

        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump(doc([el(bbox=(900, 0, 100, 100))]), fh)
        r = run_tool([bad])
        check("CLI 不合法退出 1", r.returncode == 1 and "不合法" in r.stderr, r.stderr[:200])

        r = run_tool([os.path.join(tmp, "missing.json")])
        check("CLI 缺文件退出 2", r.returncode == 2 and "文件不存在" in r.stderr, r.stderr[:200])

        r = run_tool([good, "--preset", "grid"])
        check("CLI grid 预设退出 2", r.returncode == 2, r.stderr[:200])

        r = run_tool(["-"], stdin=json.dumps(doc([el()])))
        check("CLI 读 stdin", r.returncode == 0 and "OK" in r.stdout, r.stderr[:200])


def main():
    for name, fn in [
        ("映射", test_mapping), ("role", test_roles), ("字段", test_fields),
        ("bbox", test_bbox), ("id", test_ids), ("z 序", test_z),
        ("上限与版本", test_limits), ("未知字段", test_unknown), ("导出", test_export),
        ("随机往返", test_random_roundtrip), ("编译", test_compile),
        ("嗅探", test_sniff), ("CLI", test_cli),
    ]:
        print("== %s ==" % name)
        fn()
    print("\n结果：通过 %d / 失败 %d" % (_ok, len(_bad)))
    if _bad:
        for b in _bad:
            print("  - " + b)
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
