# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""JSON 提取器机检（零 GPU、零宿主、零依赖）。"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import extract as X  # noqa: E402

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


def main():
    print("== 基本 ==")
    check("纯 JSON", X.extract_json('{"a": 1}') == {"a": 1})
    check("前后有说明",
          X.extract_json('这是结果：\n{"a": [1, 2]}\n以上。') == {"a": [1, 2]})
    check("代码块",
          X.extract_json('```json\n{"version": 1}\n```') == {"version": 1})
    check("数组根", X.extract_json("前言 [1, 2, 3] 后记") == [1, 2, 3])
    check("空白与换行", X.extract_json("\n\t {\"a\": 1} \n") == {"a": 1})

    print("== 字符串与嵌套 ==")
    check("字符串里的括号", X.extract_json('{"a": "}{"}' ) == {"a": "}{"})
    check("转义引号", X.extract_json('{"a": "say \\"hi\\""}') == {"a": 'say "hi"'})
    check("转义反斜杠", X.extract_json('{"a": "c:\\\\tmp"}') == {"a": "c:\\tmp"})
    check("嵌套对象", X.extract_json('x {"a": {"b": {"c": 1}}} y') == {"a": {"b": {"c": 1}}})
    check("中文内容", X.extract_json('{"desc": "夜雨咖啡馆"}')["desc"] == "夜雨咖啡馆")

    print("== 选择与容错 ==")
    check("取第一个合法", X.extract_json('{"a": 1} {"b": 2}') == {"a": 1})
    check("跳过非法外层", X.extract_json('{oops} {"b": 2}') == {"b": 2})
    msg = raises(X.extract_json, '{"a": 1')
    check("括号不配对", msg and "没有找到" in msg, msg)
    msg = raises(X.extract_json, "纯文字，没有结构")
    check("没有 JSON", msg and "没有找到" in msg, msg)
    msg = raises(X.extract_json, {"a": 1})
    check("非字符串", msg and "字符串" in msg, msg)

    print("== 上限 ==")
    msg = raises(X.extract_json, "a" * (X.MAX_INPUT_CHARS + 1))
    check("输入超长报错", msg and "超过上限" in msg, msg)
    deep = "{" * (X.MAX_DEPTH + 1) + "}" * (X.MAX_DEPTH + 1)
    msg = raises(X.extract_json, deep)
    check("嵌套过深报错", msg and "嵌套" in msg, msg)
    many = "{" * (X.MAX_CANDIDATES + 5)
    msg = raises(X.extract_json, many)
    check("候选上限报错", msg and "开括号" in msg, msg)
    old_budget = X.MAX_SCAN_CHARS
    X.MAX_SCAN_CHARS = 1000
    try:
        msg = raises(X.extract_json, "{" * 5 + "x" * 5000)
    finally:
        X.MAX_SCAN_CHARS = old_budget
    check("扫描预算报错", msg and "扫描" in msg, msg)
    check("空字符串值", X.extract_json('{"a": ""}') == {"a": ""})

    print("== CLI ==")
    with tempfile.TemporaryDirectory(prefix="moss_extract_") as tmp:
        prose = os.path.join(tmp, "plan.md")
        with open(prose, "w", encoding="utf-8") as fh:
            fh.write('规划如下：\n```json\n{"version": 1, "elements": []}\n```\n完。\n')
        r = subprocess.run([sys.executable, TOOL, prose, "--extract"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("CLI --extract 成功", r.returncode == 0 and "OK" in r.stdout, r.stderr[:300])
        r = subprocess.run([sys.executable, TOOL, prose], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("CLI 默认严格（非 JSON 退出 1）", r.returncode == 1 and "--extract" in r.stderr, r.stderr[:300])

    print("\n结果：通过 %d / 失败 %d" % (_ok, len(_bad)))
    if _bad:
        for b in _bad:
            print("  - " + b)
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
