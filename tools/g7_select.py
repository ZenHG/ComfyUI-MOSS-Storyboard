# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 gold set · 选图脚手架。

两步，中间隔一次**人的眼睛**（这一步刻意不自动化）：

  python tools/g7_select.py scan            # 扫候选 → out/g7/candidates.json + 缩略图总览
  （看 out/g7/sheets/*.jpg，把「第几张 → 哪一类」写进 out/g7/picks.txt）
  python tools/g7_select.py freeze          # picks.txt → out/g7/images.json（含尺寸与 sha256）

为什么选图要人看：G7 的题目是「反推得准不准」，选图本身若混进纯色块 / UI 截图 /
模糊图，后面所有指标都会变成噪声。本脚本只负责**挡掉可机判的废图**（近纯色、
尺寸过小、mask 类近二值图），画面够不够「真实可判读」由人定。

picks.txt 格式（一行一张，# 开头是注释）::

    12  单人特写
    057 多人互动
    88  含图内文字
    103 材质与反光

编号 = candidates.json 里的序号（0 起，缩略图上的标签同号）。
类别名必须落在 CATEGORIES 里，脚本据此验「每类 5 张、共 20 张」。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from _paths import host_root

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "g7"

#: 四类题目。每类 5 张，合计 20。
CATEGORIES = ("单人特写", "多人互动", "含图内文字", "材质与反光")

#: 候选来源。output 根目录文件太多（几千张），按「文件名家族」抽样，
#: 只取每族的头几张——选图看的是多样性，不是把盘扫一遍。
#:
#: **路径是用户私事，不在代码里写死盘符**：优先 `COMFYUI_INPUT` / `COMFYUI_OUTPUT`
#: 环境变量，否则用 `tools/_paths.host_root()`（`COMFYUI_PATH` → `.comfyui_path` →
#: 标准安装位置推断）拼出 `<宿主>/input` 与 `<宿主>/output`。
def source_dirs() -> tuple:
    env_in = (os.environ.get("COMFYUI_INPUT") or "").strip()
    env_out = (os.environ.get("COMFYUI_OUTPUT") or "").strip()
    base = None if (env_in and env_out) else Path(host_root())
    return (Path(env_in) if env_in else base / "input",
            Path(env_out) if env_out else base / "output")
FAMILY_CAP = 4          # 每个文件名家族最多取几张
MIN_SIDE = 384          # 短边下限：再小的图文字/材质都看不清，反推没有信息量
SHEET_COLS, SHEET_ROWS = 8, 5
THUMB = 220

_MASKISH_NAME = re.compile(r"(^|[_\-])(mask|body_mask|e_face|e_refine|clipspace-mask)", re.I)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def family_of(name: str) -> str:
    """文件名家族：取第一段字母（含数字前的连字符段），用于抽样限流。"""
    stem = name.rsplit(".", 1)[0]
    m = re.match(r"([A-Za-z][A-Za-z0-9\-]*)", stem)
    if m:
        return m.group(1).lower()
    m = re.match(r"([\u4e00-\u9fff]{1,3})", stem)
    return m.group(1) if m else stem[:4].lower()


def image_stats(path: Path) -> dict | None:
    """尺寸 / 亮度分布 / 饱和度 / 近似二值度。解码用 draft 降采样，只求快。"""
    try:
        with Image.open(path) as im:
            im.draft("RGB", (THUMB * 2, THUMB * 2))
            w, h = im.size
            small = im.convert("RGB").resize((64, 64))
    except Exception as exc:                                # 坏图不该让整轮扫描死掉
        return {"path": str(path), "error": str(exc)[:120]}

    import numpy as np

    arr = np.asarray(small, dtype=np.float32) / 255.0
    lum = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    binary = float(((lum < 0.03) | (lum > 0.97)).mean())
    return {
        "path": str(path), "name": path.name, "w": w, "h": h,
        "aspect": round(w / h, 3),
        "lum_mean": round(float(lum.mean()), 3),
        "lum_std": round(float(lum.std()), 3),
        "sat_mean": round(float(sat.mean()), 3),
        "near_binary": round(binary, 3),
        "family": family_of(path.name),
    }


def is_usable(st: dict) -> tuple[bool, str]:
    """机判废图。返回 (可用, 原因)。"""
    if "error" in st:
        return False, "decode-fail"
    if min(st["w"], st["h"]) < MIN_SIDE:
        return False, "too-small"
    if st["lum_std"] < 0.045:
        return False, "near-solid"
    if st["near_binary"] > 0.90:
        return False, "near-binary(mask)"
    if st["sat_mean"] < 0.02 and st["lum_std"] < 0.09:
        return False, "flat-gray"
    if _MASKISH_NAME.search(st["name"]):
        return False, "mask-like-name"
    return True, ""


def scan() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sheets").mkdir(parents=True, exist_ok=True)
    cands: list[dict] = []
    dropped: list[dict] = []
    per_family: dict[str, int] = {}

    dirs = source_dirs()
    for i, d in enumerate(dirs):
        if not d.is_dir():
            print("[skip] 目录不存在：%s" % d, file=sys.stderr)
            continue
        files = sorted(p for p in d.glob("*.png") if p.is_file())
        print("[scan] %s：%d 张 png" % (d, len(files)))
        for p in files:
            st = image_stats(p)
            if st is None:
                continue
            ok, why = is_usable(st)
            if ok and i == 1:                       # output 根目录按家族限流
                n = per_family.get(st["family"], 0)
                if n >= FAMILY_CAP:
                    ok, why = False, "family-cap"
            if ok:
                per_family[st["family"]] = per_family.get(st["family"], 0) + 1
                st["index"] = len(cands)
                cands.append(st)
            else:
                st["why"] = why
                dropped.append(st)

    payload = {"generated_by": "tools/g7_select.py scan", "categories": list(CATEGORIES),
               "min_side": MIN_SIDE, "family_cap": FAMILY_CAP,
               "candidates": cands, "dropped": dropped}
    (OUT / "candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # 总览图：每张带序号，序号与 candidates.json 的 index 一致。
    n = len(cands)
    sheets = 0
    per_sheet = SHEET_COLS * SHEET_ROWS
    for start in range(0, n, per_sheet):
        chunk = cands[start:start + per_sheet]
        rows = (len(chunk) + SHEET_COLS - 1) // SHEET_COLS
        sheet = Image.new("RGB", (SHEET_COLS * THUMB, rows * THUMB), (20, 20, 20))
        draw = ImageDraw.Draw(sheet)
        for i, st in enumerate(chunk):
            x, y = (i % SHEET_COLS) * THUMB, (i // SHEET_COLS) * THUMB
            try:
                with Image.open(st["path"]) as im:
                    im.draft("RGB", (THUMB, THUMB))
                    im = im.convert("RGB")
                    im.thumbnail((THUMB, THUMB - 18))
                    sheet.paste(im, (x + (THUMB - im.width) // 2,
                                     y + (THUMB - 18 - im.height) // 2))
            except Exception:
                draw.rectangle([x + 2, y + 2, x + THUMB - 2, y + THUMB - 20],
                               outline=(120, 40, 40))
            label = "#%03d %s" % (st["index"], st["name"][:18])
            draw.text((x + 3, y + THUMB - 16), label, fill=(235, 235, 235))
        dest = OUT / "sheets" / ("sheet_%02d.jpg" % sheets)
        sheet.save(dest, quality=82)
        print("[sheet] %s  #%03d–#%03d" % (dest, start, start + len(chunk) - 1))
        sheets += 1

    print("\n候选 %d 张，机判丢弃 %d 张；总览 %d 张，见 %s"
          % (n, len(dropped), sheets, OUT / "sheets"))
    print("下一步：把「编号 类别」写进 %s，再跑 freeze。" % (OUT / "picks.txt"))
    return 0


def freeze() -> int:
    """picks.txt → images.json。验证：20 张、每类 5 张、编号不重复。"""
    pool = json.loads((OUT / "candidates.json").read_text(encoding="utf-8"))
    cands = {c["index"]: c for c in pool["candidates"]}
    picks_path = OUT / "picks.txt"
    if not picks_path.is_file():
        print("缺 %s。先 scan 并看图填编号。" % picks_path, file=sys.stderr)
        return 2

    picks, errors = [], []
    for ln, line in enumerate(picks_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"(\d{1,3})\s+(\S+)$", line)
        if not m:
            errors.append("picks.txt:%d 格式应为「编号 类别」：%r" % (ln, line))
            continue
        idx, cat = int(m.group(1)), m.group(2)
        if idx not in cands:
            errors.append("picks.txt:%d 编号 #%d 不在候选里" % (ln, idx))
            continue
        if cat not in CATEGORIES:
            errors.append("picks.txt:%d 类别 %r 不在 %s" % (ln, cat, "、".join(CATEGORIES)))
            continue
        st = cands[idx]
        p = Path(st["path"])
        picks.append({
            "index": idx, "id": "img%02d" % (len(picks) + 1), "category": cat,
            "name": p.name, "path": st["path"], "w": st["w"], "h": st["h"],
            "bytes": p.stat().st_size, "sha256": sha256_file(p),
        })

    seen = {p["index"] for p in picks}
    if len(seen) != len(picks):
        errors.append("同一编号被选了两次")
    counts = {c: sum(1 for p in picks if p["category"] == c) for c in CATEGORIES}
    if len(picks) != 20:
        errors.append("总数应为 20，实际 %d" % len(picks))
    for c, n in counts.items():
        if n != 5:
            errors.append("类别 %s 应为 5 张，实际 %d" % (c, n))
    if errors:
        print("冻结失败：", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    dest = OUT / "images.json"
    dest.write_text(json.dumps({
        "generated_by": "tools/g7_select.py freeze",
        "note": "G7 gold set 第一轮 20 图；类别各 5 张；sha256 用于复核同一张图没被换过。",
        "categories": list(CATEGORIES),
        "counts": counts,
        "images": picks,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 %s：%d 张，%s" % (dest, len(picks),
                                 "，".join("%s %d" % (c, counts[c]) for c in CATEGORIES)))
    for p in picks:
        print("  %-7s %-10s %-32s %dx%d" % (p["id"], p["category"], p["name"], p["w"], p["h"]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G7 gold set 选图脚手架")
    ap.add_argument("cmd", choices=["scan", "freeze"])
    return scan() if ap.parse_args(argv).cmd == "scan" else freeze()


if __name__ == "__main__":
    raise SystemExit(main())
