# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""brief → elements 模板机检（零 GPU、零宿主、零模型）。"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import editor as E    # noqa: E402
from core import interop as I   # noqa: E402
from core import planner as PL  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "brief_to_elements.py")
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


def main():
    print("== 最小 / 完整 ==")
    doc = PL.brief_to_elements({"title": "夜雨咖啡馆"})
    check("最小 brief 产 1 个元素", len(doc["elements"]) == 1, doc)
    check("默认画布 1536×864", doc["canvas"] == {"width": 1536, "height": 864})
    check("标题文字", doc["elements"][0]["text"] == "夜雨咖啡馆")
    check("产物能被 interop 接收", I.to_state(doc)["items"][0]["caption"] == doc["elements"][0]["desc"])

    full = PL.brief_to_elements({
        "title": "夜雨咖啡馆", "subtitle": "第一集", "subject": "主角半身，侧对镜头",
        "style": "电影感", "canvas": "9:16", "palette": ["#ffcc00"],
        "title_desc": "粗体标题", "bottom_desc": "细体副标",
    })
    check("完整 brief 产 3 个元素", len(full["elements"]) == 3, full)
    check("元素顺序 title/character/text", [e["role"] for e in full["elements"]] ==
          ["text", "character", "text"], [e["role"] for e in full["elements"]])
    check("画布预设 9:16", full["canvas"] == {"width": 864, "height": 1536})
    check("style 透传", full["style"] == "电影感")
    check("palette 应用到文字", all(e.get("palette") == ["#ffcc00"] for e in full["elements"]
                                  if e["role"] == "text"))
    check("desc 覆盖", full["elements"][0]["desc"] == "粗体标题"
          and full["elements"][2]["desc"] == "细体副标")

    body = PL.brief_to_elements({"title": "T", "body": "正文"})
    check("body 走底部文字框", body["elements"][-1]["text"] == "正文")

    print("== 错误路径 ==")
    msg = raises(PL.brief_to_elements, {"title": "T", "subtitle": "S", "body": "B"})
    check("subtitle 与 body 互斥", msg and "只能给一个" in msg, msg)
    msg = raises(PL.brief_to_elements, {})
    check("缺 title 报错", msg and "缺 title" in msg, msg)
    msg = raises(PL.brief_to_elements, {"title": "  "})
    check("title 空白报错", msg and "非空字符串" in msg, msg)
    msg = raises(PL.brief_to_elements, {"title": "T", "layout": "split-v"})
    check("不支持的版式报错", msg and "不在 v1" in msg, msg)
    msg = raises(PL.brief_to_elements, {"title": "T", "canvas": "5:4"})
    check("未知画布预设报错", msg and "不认识" in msg, msg)
    msg = raises(PL.brief_to_elements, {"title": "T", "canvas": {"width": 1000, "height": 864}})
    check("画布非 16 倍数报错", msg and "16 的倍数" in msg, msg)
    msg = raises(PL.brief_to_elements, {"title": "T", "palette": ["#111111"] * 6})
    check("palette 超 5 色报错", msg and "最多 5" in msg, msg)
    msg = raises(PL.brief_to_elements, {"title": "T", "subject": "  "})
    check("subject 空白报错", msg and "非空字符串" in msg, msg)
    msg = raises(PL.brief_to_elements, [])
    check("brief 非对象报错", msg and "JSON 对象" in msg, msg)

    print("== 确定性与编译 ==")
    a = PL.brief_to_elements({"title": "T", "subject": "S"})
    b = PL.brief_to_elements({"title": "T", "subject": "S"})
    check("同 brief 同产物", a == b)
    st = I.to_state(a)
    check("brief → state 过契约", E.parse_state(json.dumps(st)) == st)
    payload = json.loads(E.layout_json(st))
    check("brief state 能编译", len(payload["items"]) == 2, payload.get("items"))

    print("== CLI ==")
    with tempfile.TemporaryDirectory(prefix="moss_brief_") as tmp:
        path = os.path.join(tmp, "brief.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"title": "T", "body": "B"}, fh)
        r = subprocess.run([sys.executable, TOOL, path], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        try:
            out = json.loads(r.stdout)
        except ValueError:
            out = None
        check("CLI 输出 elements", ok and out and len(out["elements"]) == 2, r.stderr[:300])
        r = subprocess.run([sys.executable, TOOL, path, "--state"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        try:
            st2 = json.loads(r.stdout)
        except ValueError:
            st2 = None
        check("CLI --state 输出 state", ok and st2 and st2["version"] == 3
              and len(st2["items"]) == 2, r.stderr[:300])
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"title": "T", "layout": "nope"}, fh)
        r = subprocess.run([sys.executable, TOOL, bad], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("CLI 非法 brief 退出 1", r.returncode == 1 and "不合法" in r.stderr, r.stderr[:300])

    print("\n结果：通过 %d / 失败 %d" % (_ok, len(_bad)))
    if _bad:
        for b in _bad:
            print("  - " + b)
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
