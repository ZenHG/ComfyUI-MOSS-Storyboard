#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""编译官方 MOSS-Image Prompt Enhancer 草稿（caption / layout）。

草稿格式见 OpenMOSS/MOSS-Image：
`skills/moss-image-prompt-enhancer`（Apache-2.0）。
本工具独立实现，未复制该 skill 的代码。

用法：
  python tools/compile_skill_draft.py draft.json
  python tools/compile_skill_draft.py draft.json --output prompt.txt
  python tools/compile_skill_draft.py draft.json --state state.json
  python tools/compile_skill_draft.py draft.json --output prompt.txt --state state.json

- 不带 `--output` 时，prompt 打到 stdout；
- `--state` 只对 layout 草稿有效，输出 state v3（可导入布局编辑器）；
- 报告打到 stderr；错误退出码 2，且不覆盖已存在的输出文件。
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import skill_draft as S  # noqa: E402


def _same(a, b):
    return os.path.realpath(os.path.abspath(a)) == os.path.realpath(os.path.abspath(b))


def _write(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft", help="UTF-8 caption/layout 草稿 JSON")
    ap.add_argument("--output", help="prompt 写到文件（默认 stdout）")
    ap.add_argument("--state", help="layout 草稿另存 state v3 JSON")
    args = ap.parse_args(argv[1:])

    try:
        if args.output and _same(args.output, args.draft):
            raise ValueError("--output 不能与输入草稿是同一个文件。")
        if args.state and _same(args.state, args.draft):
            raise ValueError("--state 不能与输入草稿是同一个文件。")
        if args.output and args.state and _same(args.output, args.state):
            raise ValueError("--output 与 --state 不能是同一个文件。")

        with open(args.draft, encoding="utf-8") as fh:
            obj = S.loads(fh.read())
        prompt, rep = S.compile_draft(obj)

        notes = list(rep.get("notes") or [])
        state_text = None
        if args.state:
            st, st_notes = S.draft_to_state(obj)
            state_text = json.dumps(st, ensure_ascii=False, indent=2) + "\n"
            notes.extend(st_notes)
            notes.append("state 已写到 %s" % args.state)

        if args.output:
            _write(args.output, prompt + "\n")
        else:
            print(prompt)
        if state_text is not None:
            _write(args.state, state_text)

        if rep["kind"] == "caption":
            print("类型：caption（纯文本，无布局框）", file=sys.stderr)
        else:
            labels = "、".join("%s %d" % (k, v) for k, v in sorted(rep["labels"].items()))
            print("类型：layout｜画布 %d×%d｜元素 %d（%s）"
                  % (rep["width"], rep["height"], rep["items"], labels), file=sys.stderr)
        for n in notes:
            print("说明：" + n, file=sys.stderr)
        if args.output:
            print("prompt 已写到 %s" % args.output, file=sys.stderr)
        return 0
    except (OSError, ValueError) as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
