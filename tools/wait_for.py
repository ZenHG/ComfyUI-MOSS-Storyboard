# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
"""阻塞式等待：把「每隔几秒发一次工具调用」换成「一次调用等到条件成立」。

为什么有它：本机 8 个 DSH 会话里，2,152 个 pwsh 作业有 464 个（21.6%）是在轮询
（`Test-Path out\gpu.lock`、`nvidia-smi`、`Get-ChildItem out\g7\round2\json`）。
每次轮询都是一次完整的模型回调，而每次回调重发的上下文中位数是 43 万 token。

用法（条件在满足前不返回，因此一次调用替代十次）：

    python tools/wait_for.py --until file-exists:out/gpu.lock --absent --timeout 1800
    python tools/wait_for.py --until file-absent:out/gpu.lock --timeout 1800
    python tools/wait_for.py --until dir-min-count:out/g7/round2/json --min 40 --pattern '*.json'
    python tools/wait_for.py --until gpu-free --min-mb 2048
    python tools/wait_for.py --until cmd:"python -c \"import sys; sys.exit(0 if Path('a').exists() else 1)\"" --interval 5

退出码（与仓库口径一致：**"没判定"不能混进"通过"**）：
    0  条件成立
    1  超时（没等到，也**不是失败**，是"没结论"）
    2  无法判定（缺 nvidia-smi / 参数错 / 路径不可读）

纪律：只读等待，不做任何写操作；`--json` 输出机器可读结果，便于上游脚本记账。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

EXIT_OK, EXIT_TIMEOUT, EXIT_CANNOT_JUDGE = 0, 1, 2


def _gpu_free_mb() -> int:
    exe = shutil.which("nvidia-smi")
    if not exe:
        raise RuntimeError("nvidia-smi 不在 PATH（无法判定）")
    out = subprocess.run(
        [exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=20,
    )
    if out.returncode != 0:
        raise RuntimeError("nvidia-smi 调用失败：%s" % (out.stderr or "").strip()[:200])
    first = (out.stdout or "").strip().splitlines()[0]
    used, total = [int(x.strip()) for x in first.split(",")[:2]]
    return total - used


def probe(kind: str, target: str, opts: argparse.Namespace) -> tuple[bool | None, str]:
    """返回 (是否成立, 观测描述)。None 表示无法判定。"""
    if kind == "file-exists":
        p = target
        if opts.absent:
            return (not os.path.exists(p)), "%s %s" % (p, "存在" if os.path.exists(p) else "不存在")
        return os.path.exists(p), "%s %s" % (p, "存在" if os.path.exists(p) else "不存在")

    if kind == "file-absent":
        return (not os.path.exists(target)), "%s 是否消失：%s" % (target, not os.path.exists(target))

    if kind == "dir-min-count":
        if not os.path.isdir(target):
            return False, "目录尚不存在：%s" % target
        pattern = os.path.join(target, opts.pattern or "*")
        n = len(glob.glob(pattern))
        return (n >= opts.min), "%s 命中 %d 个文件（要求 ≥ %d）" % (pattern, n, opts.min)

    if kind == "gpu-free":
        try:
            free = _gpu_free_mb()
        except Exception as e:  # noqa: BLE001
            return None, str(e)
        return (free >= opts.min_mb), "GPU 空闲 %d MB（要求 ≥ %d）" % (free, opts.min_mb)

    if kind == "cmd":
        rc = subprocess.run(target, shell=True, capture_output=True, text=True).returncode
        return (rc == 0), "命令退出码 %d" % rc

    return None, "未知条件：%s" % kind


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="阻塞等待一个条件成立（替代轮询）")
    ap.add_argument("--until", required=True, metavar="KIND:TARGET",
                    help="file-exists:PATH | file-absent:PATH | dir-min-count:DIR | gpu-free | cmd:SHELL")
    ap.add_argument("--absent", action="store_true", help="file-exists 的取反（等它消失）")
    ap.add_argument("--min", type=int, default=1, help="dir-min-count 的文件数下限")
    ap.add_argument("--pattern", default="*", help="dir-min-count 的文件名模式")
    ap.add_argument("--min-mb", type=int, default=2048, help="gpu-free 的空闲显存下限（MB）")
    ap.add_argument("--timeout", type=float, default=600.0, help="秒；到点返回 1，不阻塞到天荒地老")
    ap.add_argument("--interval", type=float, default=5.0, help="轮询间隔（秒）；这个循环在进程内，不发模型调用")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv[1:])

    if ":" in args.until:
        kind, target = args.until.split(":", 1)
    else:
        kind, target = args.until, ""

    t0 = time.time()
    last = ""
    while True:
        ok, last = probe(kind, target, args)
        if ok is None:
            payload = {"ok": None, "reason": "cannot-judge", "detail": last, "elapsed_s": round(time.time() - t0, 1)}
            print(json.dumps(payload, ensure_ascii=False) if args.json else "无法判定：" + last)
            return EXIT_CANNOT_JUDGE
        if ok:
            payload = {"ok": True, "condition": args.until, "detail": last, "elapsed_s": round(time.time() - t0, 1)}
            print(json.dumps(payload, ensure_ascii=False) if args.json else
                  "条件成立（%.1fs）：%s" % (payload["elapsed_s"], last))
            return EXIT_OK
        if time.time() - t0 >= args.timeout:
            payload = {"ok": False, "reason": "timeout", "condition": args.until, "detail": last,
                       "elapsed_s": round(time.time() - t0, 1)}
            print(json.dumps(payload, ensure_ascii=False) if args.json else
                  "超时（%.1fs）：%s" % (payload["elapsed_s"], last))
            return EXIT_TIMEOUT
        time.sleep(min(args.interval, max(0.05, args.timeout - (time.time() - t0))))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
