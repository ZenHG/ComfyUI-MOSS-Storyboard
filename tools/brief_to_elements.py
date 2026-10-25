# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""brief → elements-v1 / state v3（确定性模板，零 GPU、零宿主、零模型）。

用法：
    python tools/brief_to_elements.py brief.json                 # 输出 elements JSON
    python tools/brief_to_elements.py brief.json --state         # 输出 state v3 JSON
    cat brief.json | python tools/brief_to_elements.py - --state

退出码：0 成功；1 brief 不合法；2 读不到文件或参数错误。
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import editor as E     # noqa: E402
from core import planner as PL   # noqa: E402


def load(path):
    if path == "-":
        raw = sys.stdin.read()
    else:
        if not os.path.isfile(path):
            raise OSError("文件不存在：%s" % path)
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    if len(raw) > E.MAX_STATE_CHARS:
        raise ValueError("文件有 %d 个字符，超过上限 %d。" % (len(raw), E.MAX_STATE_CHARS))
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ValueError("brief 不是合法 JSON：%s" % exc) from None


def main():
    ap = argparse.ArgumentParser(description="brief → elements-v1 / state v3（title-body）")
    ap.add_argument("file", help="brief JSON；- 表示 stdin")
    ap.add_argument("--state", action="store_true", help="输出 state v3 而不是 elements")
    args = ap.parse_args()
    try:
        brief = load(args.file)
    except OSError as exc:
        print("[读取失败] %s" % exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print("[不合法] %s" % exc, file=sys.stderr)
        return 1
    try:
        out = PL.brief_to_state(brief) if args.state else PL.brief_to_elements(brief)
    except ValueError as exc:
        print("[不合法] %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
