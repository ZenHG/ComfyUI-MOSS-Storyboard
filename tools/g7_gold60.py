# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 gold set 20 → 60 扩样（纯 CPU，零 GPU）。

沿用第一轮**同一批** `out/g7/candidates.json`（240 张）与同一套采图标准：
旧 20 张原样保留（字段逐项相等），新增 40 张按四类各 +10，共 60 张、四类各 15。

排除项（与第一轮一致，全部可机判或人判一条线）：
- 近纯色 / 低纹理 / 近二值图（mask 类）/ 短边 < 384；
- 纯 UI 截图、带标注的探测图（bbox 框、红圈）、多格拼图 / 横条对比图 / 三联设定图；
- 画面不可判读（全黑、纯色块、截图带宿主 UI）。

三步（中间隔一次人的眼睛）::

  python tools/g7_gold60.py sheet     # → out/g7/sheets60/ + out/g7/round2/unused-candidates.json
  （看缩略图，把「编号 类别」写进 out/g7/picks60.txt，共 40 行、每类 10 行）
  python tools/g7_gold60.py freeze    # → out/g7/images60.json + images60.sha256 + 选图说明.md
  python tools/g7_gold60.py verify    # 离线冻结校验（退出码即结论）

编号 = candidates.json 里的 index（缩略图标签同号）。类别名必须落在 CATEGORIES。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "g7"
CANDIDATES = OUT / "candidates.json"
ROUND1 = OUT / "images.json"
PICKS60 = OUT / "picks60.txt"
IMAGES60 = OUT / "images60.json"
SHA60 = OUT / "images60.sha256"
SHEET_DIR = OUT / "sheets60"
ROUND2 = OUT / "round2"
SUPPLEMENT = ROUND2 / "supplement-candidates.json"

#: 补充候选：第一轮 candidates.json 里缺的一类干净画面（多为 output 家族的
#: 「每族限 4 张」被截掉的图，或子目录里的成品图）。路径相对宿主根，**不写死盘符**：
#: 宿主根由 `tools/_paths.host_root()` 解析（COMFYUI_PATH → .comfyui_path → 推断）。
SUPPLEMENT_SPECS = [
    ("s01", "output/e2e/editor_title_body_00001_.png", "含图内文字",
     "大标题中文字幕版式（永兵传说 / 第一章 雪夜），文字大而清晰"),
    ("s02", "output/poster_07_ledger_00001_.png", "含图内文字",
     "旧纸张文件特写：竖排中文 + 红章，文字可判读"),
    ("s03", "output/DS_手机亮屏_v2_00001_.png", "含图内文字",
     "实拍手机聊天界面：中文字串多且清晰（非纯 UI 截图，有场景与材质）"),
    ("s04", "input/灰岩法庭_日_01_全景_军官列队_seed5105.png", "多人互动",
     "十余名军官列队 + 主审席，多人同框（与 K2v1 同一类宽景，补多人样本）"),
    ("s05", "output/krea2/storyboard_20260914191210/scene_00005_.png", "多人互动",
     "酒吧双人对坐（一人白衣举杯、一人深色外套），真实电影级构图"),
    ("s06", "output/krea2/storyboard_20260914191210/scene_00006_.png", "多人互动",
     "同一酒吧场景另一构帧（双手与杯的特写），验证模型对『多人』的计数与绑定"),
    ("s07", "output/krea2/storyboard_20260914191210/scene_00001_.png", "多人互动",
     "酒吧双人正面对视、雨中窗与吊灯，交互关系明确"),
]

CATEGORIES = ("单人特写", "多人互动", "含图内文字", "材质与反光")
PER_CATEGORY = 15          # 每类目标张数（旧 5 + 新 10）
TOTAL = 60
NEW_TOTAL = 40
THUMB = 220
COLS, ROWS = 8, 5

#: 采图标准（写进冻结产物的 note，供复核）。
SELECTION_STANDARD = [
    "四类各 15 张：单人特写 / 多人互动 / 含图内文字 / 材质与反光",
    "旧 20 张原样保留（id、顺序、字段逐项相等，sha256 不变）",
    "新增 40 张来自同一批 out/g7/candidates.json（第一轮 240 张候选），按各类 +10",
    "偏好真实可判读画面：主体清晰、有足够可核对事实（颜色/材质/方位/文字）",
    "排除近纯色/低纹理/近二值(mask)/短边<384；排除纯 UI 截图、带标注探测图（bbox 框/红圈）",
    "排除多格拼图、横条对比图、三联设定图等多视图拼版（会把 canvas/数量判据污染）",
    "每类保持多样性：年龄/性别/光照/场景/构图不重复扎堆",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def host_root() -> Path:
    """宿主根：COMFYUI_PATH → 仓库根 .comfyui_path → 标准安装位置推断。"""
    from _paths import host_root as _hr
    return Path(_hr())


def round1_images() -> list[dict]:
    return load_json(ROUND1)["images"]


def candidates() -> list[dict]:
    return load_json(CANDIDATES)["candidates"]


def supplement() -> list[dict]:
    if not SUPPLEMENT.is_file():
        return []
    return load_json(SUPPLEMENT)["candidates"]


def all_pickable() -> dict:
    """可选池：candidates（int index）+ supplement（"sNN"）。"""
    pool = {c["index"]: c for c in candidates()}
    pool.update({c["index"]: c for c in supplement()})
    return pool


def _entry_from_path(idx, rel: str, category: str, reason: str) -> dict:
    p = host_root() / rel
    if not p.is_file():
        raise SystemExit("补充候选不存在：%s" % p)
    with Image.open(p) as im:
        w, h = im.size
    return {"index": idx, "path": str(p), "name": p.name, "w": w, "h": h,
            "bytes": p.stat().st_size, "sha256": sha256_file(p),
            "category_hint": category, "reason": reason, "source": "supplement"}


def cmd_supplement(args) -> int:
    """生成补充候选清单 + 一张总览（candidates.json 里该类的干净样本不足时用）。"""
    ROUND2.mkdir(parents=True, exist_ok=True)
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    errors = []
    for idx, rel, cat, reason in SUPPLEMENT_SPECS:
        try:
            e = _entry_from_path(idx, rel, cat, reason)
        except SystemExit as exc:
            errors.append(str(exc))
            continue
        e["rel"] = rel
        entries.append(e)
    if errors:
        for e in errors:
            print("  ✗ " + e, file=sys.stderr)
        return 1
    SUPPLEMENT.write_text(json.dumps({
        "generated_by": "tools/g7_gold60.py supplement",
        "note": "第一轮 candidates.json 中该类干净画面不足时的补充候选；index 用 sNN，"
                "路径相对宿主根（host_root），不写死盘符。",
        "candidates": entries}, ensure_ascii=False, indent=1), encoding="utf-8")
    # 总览（供人复核）
    cell = 300
    sheet = Image.new("RGB", (cell * len(entries), cell + 20), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for i, e in enumerate(entries):
        with Image.open(e["path"]) as im:
            im.draft("RGB", (cell, cell))
            im = im.convert("RGB")
            im.thumbnail((cell, cell))
            sheet.paste(im, (i * cell + (cell - im.width) // 2, 0))
        draw.text((i * cell + 3, cell + 3),
                  "%s %s" % (e["index"], e["category_hint"]), fill=(235, 235, 235))
    dest = SHEET_DIR / "supplement.jpg"
    sheet.save(dest, quality=85)
    print("已写 %s：%d 张补充候选；总览 %s" % (SUPPLEMENT, len(entries), dest))
    for e in entries:
        print("  %s %-6s %s" % (e["index"], e["category_hint"], e["name"]))
    return 0


# ─────────────────────────────────────────────────────────────
# sheet：只给「第一轮没用过」的候选出总览，避免重复看
# ─────────────────────────────────────────────────────────────

def cmd_sheet(args) -> int:
    cands = candidates()
    old_paths = {Path(i["path"]) for i in round1_images()}
    unused = [c for c in cands if Path(c["path"]) not in old_paths]
    if not unused:
        print("候选都已被第一轮用掉？检查 %s" % CANDIDATES, file=sys.stderr)
        return 2
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    ROUND2.mkdir(parents=True, exist_ok=True)
    (ROUND2 / "unused-candidates.json").write_text(json.dumps(
        {"generated_by": "tools/g7_gold60.py sheet",
         "count": len(unused),
         "note": "第一轮 20 张之外、可供 round2 扩样选择的候选；index 与 candidates.json 一致",
         "candidates": [{k: c.get(k) for k in
                         ("index", "path", "name", "w", "h", "category_hint", "family")}
                        for c in unused]},
        ensure_ascii=False, indent=1), encoding="utf-8")

    n = 0
    per_sheet = COLS * ROWS
    for start in range(0, len(unused), per_sheet):
        chunk = unused[start:start + per_sheet]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * THUMB, rows * THUMB), (20, 20, 20))
        draw = ImageDraw.Draw(sheet)
        for i, c in enumerate(chunk):
            x, y = (i % COLS) * THUMB, (i // COLS) * THUMB
            try:
                with Image.open(c["path"]) as im:
                    im.draft("RGB", (THUMB, THUMB))
                    im = im.convert("RGB")
                    im.thumbnail((THUMB, THUMB - 18))
                    sheet.paste(im, (x + (THUMB - im.width) // 2,
                                     y + (THUMB - 18 - im.height) // 2))
            except Exception:
                draw.rectangle([x + 2, y + 2, x + THUMB - 2, y + THUMB - 20],
                               outline=(120, 40, 40))
            draw.text((x + 3, y + THUMB - 16),
                      "#%03d %s" % (c["index"], c["name"][:18]),
                      fill=(235, 235, 235))
        dest = SHEET_DIR / ("sheet_%02d.jpg" % n)
        sheet.save(dest, quality=82)
        print("[sheet] %s  #%03d–#%03d" % (dest, chunk[0]["index"],
                                           chunk[-1]["index"]))
        n += 1
    print("\n未用候选 %d 张，总览 %d 张 → %s" % (len(unused), n, SHEET_DIR))
    print("下一步：把「编号 类别」写进 %s（40 行、每类 10 行），再跑 freeze。" % PICKS60)
    return 0


# ─────────────────────────────────────────────────────────────
# freeze：picks60.txt → images60.json（旧 20 张原样保留）
# ─────────────────────────────────────────────────────────────

def _parse_picks(path: Path) -> tuple[list[tuple[object, str]], list[str]]:
    picks, errors = [], []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"(\d{1,3}|s\d{2})\s+(\S+)$", line)
        if not m:
            errors.append("%s:%d 格式应为「编号 类别」或「sNN 类别」：%r"
                          % (path.name, ln, line))
            continue
        raw, cat = m.group(1), m.group(2)
        picks.append((raw if raw.startswith("s") else int(raw), cat))
    return picks, errors


def fmt_idx(idx) -> str:
    return ("#%03d" % idx) if isinstance(idx, int) else str(idx)


def cmd_freeze(args) -> int:
    if not PICKS60.is_file():
        print("缺 %s：先 sheet 看图并填 40 行（每类 10 行）。" % PICKS60, file=sys.stderr)
        return 2
    old = round1_images()
    pool = all_pickable()
    old_paths = {Path(i["path"]) for i in old}
    picks, errors = _parse_picks(PICKS60)

    if len(picks) != NEW_TOTAL:
        errors.append("新增张数应为 %d，实际 %d" % (NEW_TOTAL, len(picks)))
    seen: set = set()
    rows = []
    for idx, cat in picks:
        if idx in seen:
            errors.append("编号 %s 重复" % fmt_idx(idx))
            continue
        seen.add(idx)
        if cat not in CATEGORIES:
            errors.append("%s 类别 %r 不在 %s"
                          % (fmt_idx(idx), cat, "、".join(CATEGORIES)))
            continue
        c = pool.get(idx)
        if c is None:
            errors.append("编号 %s 不在 candidates.json / supplement-candidates.json"
                          % fmt_idx(idx))
            continue
        p = Path(c["path"])
        if p in old_paths:
            errors.append("%s 与第一轮旧图重复：%s" % (fmt_idx(idx), p.name))
            continue
        if not p.is_file():
            errors.append("%s 文件不存在：%s" % (fmt_idx(idx), p))
            continue
        rows.append({"index": idx, "category": cat, "path": str(p),
                     "name": p.name, "w": c.get("w"), "h": c.get("h"),
                     "source": c.get("source", "candidates")})

    counts = {c: sum(1 for r in rows if r["category"] == c) for c in CATEGORIES}
    for cat, got in counts.items():
        if got != PER_CATEGORY - 5:
            errors.append("类别 %s 新增应为 %d 张，实际 %d" % (cat, PER_CATEGORY - 5, got))
    if errors:
        print("冻结失败：", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    # 顺序：先按类别序，再按候选 index（int 在前、sNN 在后），保证 id 稳定可复现
    rows.sort(key=lambda r: (CATEGORIES.index(r["category"]),
                             0 if isinstance(r["index"], int) else 1,
                             r["index"] if isinstance(r["index"], int) else 0))
    new_images = []
    for n, r in enumerate(rows, start=21):
        p = Path(r["path"])
        new_images.append({
            "index": r["index"], "source": r.get("source", "candidates"),
            "id": "img%02d" % n, "category": r["category"],
            "name": r["name"], "path": str(p), "w": r["w"], "h": r["h"],
            "bytes": p.stat().st_size, "sha256": sha256_file(p),
        })
    all_images = [dict(i) for i in old] + new_images   # 旧 20 张字段逐项原样拷贝
    counts_all = {c: sum(1 for i in all_images if i["category"] == c)
                  for c in CATEGORIES}

    IMAGES60.write_text(json.dumps({
        "generated_by": "tools/g7_gold60.py freeze",
        "note": "G7 gold set 第二轮：60 图（四类各 15）。前 20 张与第一轮 images.json 逐项相等；"
                "新增 40 张来自同一批 out/g7/candidates.json（index 记录在每项里；"
                "index 为 sNN 的来自 round2/supplement-candidates.json 补充候选），sha256 用于复核。",
        "base": {"file": "out/g7/images.json", "count": 20, "unchanged": True},
        "categories": list(CATEGORIES),
        "counts": counts_all,
        "selection_standard": SELECTION_STANDARD,
        "images": all_images,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    SHA60.write_text("".join("%s  %s\n" % (i["sha256"], i["path"])
                             for i in all_images), encoding="utf-8")

    # 选图说明（可复核）：标准 + 新增清单
    lines = ["# G7 gold set 20→60 · 选图说明", "",
             "采图标准：", ""]
    lines += ["- " + s for s in SELECTION_STANDARD]
    lines += ["- 补充候选（sNN）来自 round2/supplement-candidates.json：第一轮 candidates 里"
              "该类干净画面不足时补入，逐张注明理由", "",
              "新增 40 张：", "",
              "| id | 类别 | index | 来源 | 文件 | 尺寸 | sha256 |",
              "|---|---|---|---|---|---|---|"]
    for i in new_images:
        lines.append("| %s | %s | %s | %s | %s | %dx%d | %s… |" % (
            i["id"], i["category"], fmt_idx(i["index"]), i["source"], i["name"],
            i["w"], i["h"], i["sha256"][:12]))
    (ROUND2 / "gold60-selection.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("已写 %s（%d 张，%s）" % (IMAGES60, len(all_images), "，".join(
        "%s %d" % (c, counts_all[c]) for c in CATEGORIES)))
    print("已写 %s；选图说明 %s" % (SHA60, ROUND2 / "gold60-selection.md"))
    for i in new_images:
        print("  %-6s %-6s %-8s %s" % (i["id"], i["category"],
                                       fmt_idx(i["index"]), i["name"]))
    return 0


# ─────────────────────────────────────────────────────────────
# verify：离线冻结校验（一条命令，退出码即结论）
# ─────────────────────────────────────────────────────────────

def cmd_verify(args) -> int:
    errors: list[str] = []
    if not IMAGES60.is_file():
        print("缺 %s：先 freeze。" % IMAGES60, file=sys.stderr)
        return 2
    data = load_json(IMAGES60)
    images = data.get("images") or []
    old = round1_images()

    if len(images) != TOTAL:
        errors.append("总数应为 %d，实际 %d" % (TOTAL, len(images)))
    counts = {c: sum(1 for i in images if i["category"] == c) for c in CATEGORIES}
    for cat in CATEGORIES:
        if counts[cat] != PER_CATEGORY:
            errors.append("类别 %s 应为 %d 张，实际 %d" % (cat, PER_CATEGORY, counts[cat]))
    for key, label in (("id", "id"), ("path", "路径"), ("sha256", "sha256")):
        vals = [i.get(key) for i in images]
        dup = sorted({v for v in vals if vals.count(v) > 1})
        if dup:
            errors.append("%s 重复：%s" % (label, dup[:4]))

    # 旧 20 张：字段逐项相等（含顺序）
    for n, (a, b) in enumerate(zip(old, images[:len(old)], strict=False)):
        if a != b:
            diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            errors.append("旧图 #%d（%s）与第一轮不一致，字段：%s"
                          % (n + 1, a.get("id"), diff))

    # 每张文件存在、尺寸/sha 一致
    for i in images:
        p = Path(i["path"])
        if not p.is_file():
            errors.append("文件缺失：%s" % p)
            continue
        if sha256_file(p) != i["sha256"]:
            errors.append("sha256 不匹配：%s" % p.name)
        if i.get("bytes") and p.stat().st_size != i["bytes"]:
            errors.append("字节数不匹配：%s" % p.name)

    # sha256 清单与 images60 一致
    if not SHA60.is_file():
        errors.append("缺 %s" % SHA60.name)
    else:
        listed = {}
        for line in SHA60.read_text(encoding="utf-8").splitlines():
            if line.strip():
                sha, path = line.split(None, 1)
                listed[path.strip()] = sha
        want = {i["path"]: i["sha256"] for i in images}
        if listed != want:
            errors.append("images60.sha256 与 images60.json 不一致（%d vs %d 条）"
                          % (len(listed), len(want)))

    # 新增 40 张必须能在 candidates / 补充候选里找到（选图来源可追溯）
    old_paths = {i["path"] for i in old}
    pool_paths = {c["path"] for c in all_pickable().values()}
    supp_paths = {c["path"] for c in supplement()}
    for i in images[len(old):]:
        if i["path"] in old_paths:
            errors.append("新增图与旧图重复：%s" % i["path"])
        if i["path"] not in pool_paths:
            errors.append("新增图不在 candidates/补充候选里：%s" % i["path"])
        if i.get("source") == "supplement" and i["path"] not in supp_paths:
            errors.append("标为 supplement 但不在补充清单里：%s" % i["path"])

    if errors:
        print("冻结校验：失败（%d 项）" % len(errors))
        for e in errors:
            print("  ✗ " + e)
        return 1
    n_supp = sum(1 for i in images[len(old):] if i.get("source") == "supplement")
    print("冻结校验：通过")
    print("  - 总数 %d（%s）" % (len(images), "，".join(
        "%s %d" % (c, counts[c]) for c in CATEGORIES)))
    print("  - 旧 20 张与 out/g7/images.json 字段逐项相等（含顺序）")
    print("  - 全部 %d 个文件的 sha256 与尺寸复核通过；无重复 id/路径/sha" % len(images))
    print("  - 新增 40 张可追溯：candidates %d 张 + 补充候选 %d 张"
          % (len(images) - len(old) - n_supp, n_supp))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G7 gold set 20→60 扩样（纯 CPU）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet", help="给第一轮未用的候选出缩略图总览")
    s.set_defaults(func=cmd_sheet)
    p = sub.add_parser("supplement", help="生成补充候选清单（sNN，candidates 里缺的干净画面）")
    p.set_defaults(func=cmd_supplement)
    f = sub.add_parser("freeze", help="picks60.txt → images60.json + sha256 清单")
    f.set_defaults(func=cmd_freeze)
    v = sub.add_parser("verify", help="离线冻结校验（sha256/四类各 15/无重复/旧 20 不变）")
    v.set_defaults(func=cmd_verify)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
