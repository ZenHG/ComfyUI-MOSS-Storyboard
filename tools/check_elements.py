# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""elements-v1 ↔ state v3 命令行校验 / 转换（零 GPU、零宿主、零依赖）。

外部 planner / 脚本不需要装 ComfyUI，只要产出 elements JSON，就能先在这里验一遍，
再交给 `MossLayoutEditor` 的 `editor_state`。格式见 `docs/12-interop.md`。

用法：
    python tools/check_elements.py plan.json              # 校验并打印摘要
    python tools/check_elements.py plan.json --emit state # 输出 state v3 JSON（可管道）
    python tools/check_elements.py state.json --in-format state --emit elements
    cat plan.json | python tools/check_elements.py -      # 从 stdin 读

退出码：0 全过；1 内容不合法（版本 / 字段 / 越界）；2 读不到文件或参数错误。
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import editor as E   # noqa: E402
from core import interop as I  # noqa: E402


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
        raise ValueError("不是合法 JSON：%s" % exc) from None


def convert(obj, in_format, preset, style):
    kind = in_format if in_format != "auto" else I.sniff(obj)
    if kind == "elements":
        return I.to_state(obj, preset=preset, style=style)
    if kind == "state":
        return E.parse_state(json.dumps(obj, ensure_ascii=False))
    raise ValueError("既不是 elements 文档（缺 elements）也不是 state v3（缺 items）。")


def run_one(path, args):
    obj = load(path)
    state = convert(obj, args.in_format, args.preset, args.style)
    if args.emit == "state":
        print(json.dumps(state, ensure_ascii=False, indent=2))
    elif args.emit == "elements":
        print(json.dumps(I.from_state(state), ensure_ascii=False, indent=2))
    else:
        print("OK %s → state v3（preset=%s）" % (path, state["preset"]))
        print(E.report(state))


def main():
    ap = argparse.ArgumentParser(description="elements-v1 ↔ state v3（单张模式）")
    ap.add_argument("files", nargs="+", help="JSON 文件；- 表示 stdin")
    ap.add_argument("--preset", default="free",
                    help="单张模式预设（默认 free；不支持 grid）")
    ap.add_argument("--style", default=None, help="覆盖文档里的全局风格段")
    ap.add_argument("--emit", choices=("state", "elements"), default=None,
                    help="输出转换结果（默认只打印摘要）")
    ap.add_argument("--in-format", choices=("auto", "elements", "state"), default="auto",
                    help="输入类型（默认按内容判断）")
    args = ap.parse_args()

    if len(args.files) > 1 and args.emit:
        ap.error("--emit 一次只能处理一个文件")
    if args.preset == "grid":
        ap.error("grid 不支持：宫格的 item 是格位，不是元素")

    bad = 0
    for i, path in enumerate(args.files):
        if len(args.files) > 1:
            print("== %s ==" % path)
        try:
            run_one(path, args)
        except OSError as exc:
            print("[读取失败] %s" % exc, file=sys.stderr)
            return 2
        except ValueError as exc:
            print("[不合法] %s：%s" % (path, exc), file=sys.stderr)
            bad += 1
        if i + 1 < len(args.files):
            print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
