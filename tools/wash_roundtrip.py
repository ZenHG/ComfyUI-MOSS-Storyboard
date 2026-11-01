# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""W3 · 洗图 roundtrip 事实保全（20 图）。

链路（GPU 串行，W1 出图后跑）：

```
G7 20 图源图 → 既有 B-9B round2 IR（事实账本）
            → 编译成 moss-image prompt（W1 使用）→ MOSS 生成图
            → 同一模型 B-9B 再反推 → 与源 IR 做事实 diff
```

本工具只做**可离线复算**的三段（纯函数，可单测）：

1. ``images``：从 W1 产物（``out/wash_benchmark/moss_q4_round1/run.json`` +
   ``images/moss-image-q4/*__seed42.png``）构造 ``out/wash_roundtrip/images.json``
   ——只取 ``seed=42`` 一档，id 映射回 G7 的 ``imgNN``，并核对源 IR 存在。
2. ``diff``：把再反推 JSON 与源 IR（``out/g7/round2/json/B-9B/img*.json``）做**事实集**
   对称差：``lost`` = 源 IR 有、再反推没有；``extra`` = 再反推有、源 IR 没有。
   事实口径与 ``moss_llm.smith.expand.fixed_items`` 完全一致（text/age/color/anchor/
   keep/user/line/beat），**文字实体单列**（不跑 OCR）。分母为 0 时率报 ``null``，
   不写 0/1。
3. ``report``：把汇总打印成人读表（典型 lost / 失败图单列）。

GPU 再反推不重复实现模型定位/侧车，直接复用 ``tools/g7_caption.py``：

```powershell
python tools/g7_caption.py --models B-9B `
  --images out/wash_roundtrip/images.json `
  --json-root out/wash_roundtrip/reverse `
  --run-dir out/wash_roundtrip/run
```

（模型仍是 B-9B：``moss-te-heretic-dhq-K-6.6G.gguf`` + ``Qwen3.5-9B.mmproj-Q8_0.gguf``，
与源 IR 同模型——换模型会把「洗图丢没丢事实」和「换模型差异」混在一起。）
也可以走本工具的 ``recaption`` 子命令（内部转发同一份 g7_caption 参数）。

纪律：

- 失败图**单列**、不跳过；汇总的分母只算成功再反推的图，并把失败数写进 stage 计数。
- 本工具**不改** W1/G7 产物，只读它们；输出只写 ``out/wash_roundtrip/``。
- 不跑 OCR：文字实体的「再反推是否逐字保留」单列成 text_lost/text_extra，不下 OCR 结论。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 直接以 `python tools/wash_roundtrip.py` 跑时 sys.path[0] 是 tools/，看不到 moss_llm；
# 这个工具同时被单测 import，所以这里显式补一次仓库根（重复插入无害）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / "out"
WASH = OUT / "wash_benchmark"
DEFAULT_W1_RUN_DIR = WASH / "moss_q4_round1"
DEFAULT_IMAGES_DIR = DEFAULT_W1_RUN_DIR / "images" / "moss-image-q4"
DEFAULT_G7_IMAGES = OUT / "g7" / "images.json"
DEFAULT_SOURCE_JSON = OUT / "g7" / "round2" / "json" / "B-9B"
DEFAULT_OUT_DIR = OUT / "wash_roundtrip"
DEFAULT_IMAGES_OUT = DEFAULT_OUT_DIR / "images.json"
DEFAULT_REVERSE_ROOT = DEFAULT_OUT_DIR / "reverse" / "B-9B"
DEFAULT_SUMMARY = DEFAULT_OUT_DIR / "summary.json"
DEFAULT_RECAPTION_RUN = DEFAULT_OUT_DIR / "run"

MODEL_KEY = "B-9B"
IMG_ID_RE = re.compile(r"(img\d{1,2})(?!\d)", re.I)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


# ─────────────────────────────────────────────────────────────
# 通用小工具
# ─────────────────────────────────────────────────────────────

def _load_json(path: str | Path, what: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SystemExit("找不到%s：%s" % (what, p))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit("%s 不是合法 JSON：%s（%s）" % (what, p, exc)) from exc


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def _norm(value) -> str:
    """事实键归一化：大小写/空白差异不算不同事实。"""
    return " ".join(str(value or "").lower().split())


def _rate(num: int | None, den: int | None):
    """分母 0 → None（绝不报 0/1）。"""
    if not den:
        return None
    return round(float(num or 0) / float(den), 4)


# ─────────────────────────────────────────────────────────────
# 1) 生成图清单：W1 产物 → images.json
# ─────────────────────────────────────────────────────────────

def _iter_run_entries(run) -> list:
    """容错读 W1 的 run.json（schema 未定；只取「文件 ↔ g7 id」映射）。"""
    if isinstance(run, list):
        return [e for e in run if isinstance(e, dict)]
    if not isinstance(run, dict):
        return []
    for key in ("images", "items", "entries", "records", "results"):
        val = run.get(key)
        if isinstance(val, list):
            return [e for e in val if isinstance(e, dict)]
    # W1 的实际 schema：{backends: {backend_name: {images: [...]}}} —— 跨后端合并
    backends = run.get("backends")
    if isinstance(backends, dict):
        out = []
        for name, box in backends.items():
            if not isinstance(box, dict):
                continue
            for entry in box.get("images") or []:
                if isinstance(entry, dict):
                    out.append(dict(entry, backend=name))
        if out:
            return out
    # 也接受 {img_id: {...}} 这种 dict-of-dict
    return [dict(v, g7_id=k) for k, v in run.items() if isinstance(v, dict)]


def _entry_image_id(entry: dict) -> str:
    for key in ("g7_id", "image_id", "source_id", "id", "source", "image"):
        m = IMG_ID_RE.search(str(entry.get(key) or ""))
        if m:
            return m.group(1).lower()
    for key in ("path", "file", "output", "image_path", "name"):
        m = IMG_ID_RE.search(str(entry.get(key) or ""))
        if m:
            return m.group(1).lower()
    return ""


def _run_seed(entry: dict):
    seed = entry.get("seed")
    try:
        return int(seed) if seed is not None else None
    except (TypeError, ValueError):
        return None


def build_images(run_json: str | Path, images_dir: str | Path,
                 g7_images: str | Path, source_json_root: str | Path) -> dict:
    """W1 产物 → 可直接喂给 `tools/g7_caption.py` 的 images.json。

    只取 ``filename 含 __seed42`` 的图；G7 id 优先从 run.json 的文件映射取，
    文件名里的 ``imgNN`` 兜底；缺源 IR 的图直接报错（不允许静默少图）。
    """
    images_dir = Path(images_dir)
    if not images_dir.is_dir():
        raise SystemExit(
            "找不到 W1 生成图目录：%s\n"
            "  W1（gen-bench）出图后会落在 <wash_benchmark>/moss_q4_round1/images/"
            "moss-image-q4/；若 W1 还没跑完，先别造 images.json。" % images_dir)
    run_path = Path(run_json)
    run = _load_json(run_path, "W1 run.json") if run_path.is_file() else {}
    entries = _iter_run_entries(run)
    by_name: dict[str, str] = {}
    for entry in entries:
        seed = _run_seed(entry)
        if seed is not None and seed != 42:
            continue
        gid = _entry_image_id(entry)
        for key in ("path", "file", "output", "image_path", "image", "name"):
            name = Path(str(entry.get(key) or "")).name
            if name and gid:
                by_name.setdefault(name, gid)

    g7 = _load_json(g7_images, "G7 图片清单")
    g7_by_id = {str(i["id"]): i for i in (g7.get("images") or []) if i.get("id")}
    src_root = Path(source_json_root)
    files = sorted(
        [p for p in images_dir.iterdir()
         if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")
         and "__seed42" in p.name])
    if not files:
        raise SystemExit(
            "在 %s 下没找到 *__seed42.png/*__seed42.jpg。\n"
            "  W1 的 seed 档命名若不同，请先用 run.json 核对；本工具只取 seed=42 一档。"
            % images_dir)

    images, skipped, errors = [], [], []
    seen: dict[str, Path] = {}
    for path in files:
        gid = by_name.get(path.name) or ""
        if not gid:
            m = IMG_ID_RE.search(path.stem)
            gid = m.group(1).lower() if m else ""
        if not gid:
            errors.append("%s：文件名里没有 imgNN，run.json 也没有对应映射" % path.name)
            continue
        if gid in seen:
            errors.append("%s 与 %s 都映射到 %s（id 冲突）"
                          % (seen[gid].name, path.name, gid))
            continue
        seen[gid] = path
        meta = g7_by_id.get(gid)
        if not meta:
            errors.append("%s：G7 images.json 里没有 %s" % (path.name, gid))
            continue
        src_json = src_root / ("%s.json" % gid)
        if not src_json.is_file():
            errors.append("%s：缺源 IR %s" % (path.name, src_json))
            continue
        try:
            src_payload = _load_json(src_json, "源 IR JSON")
        except SystemExit:
            errors.append("%s：源 IR JSON 不合法 %s" % (path.name, src_json))
            continue
        if not src_payload.get("ok"):
            skipped.append("%s：源 IR 记录是失败态（%s）"
                           % (gid, str(src_payload.get("error") or "")[:80]))
            continue
        images.append({
            "id": gid,
            "name": path.name,
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "category": meta.get("category") or src_payload.get("category") or "",
            "seed": 42,
            "backend": "moss-image-q4",
            "source_image": str(meta.get("path") or ""),
            "source_sha256": str(meta.get("sha256") or ""),
            "source_json": str(src_json),
        })
    if errors:
        raise SystemExit(
            "有 %d 张生成图无法映射/缺源数据（不允许静默少图）：\n  %s"
            % (len(errors), "\n  ".join(errors[:10])))
    images.sort(key=lambda i: i["id"])
    return {
        "schema": "wash-roundtrip-images/1",
        "generated_by": "tools/wash_roundtrip.py images",
        "model_key": MODEL_KEY,
        "seed": 42,
        "run_json": str(run_path),
        "images_dir": str(images_dir),
        "g7_images": str(g7_images),
        "source_json_root": str(src_root),
        "image_count": len(images),
        "skipped": skipped,
        "skipped_other_seed": sorted(
            p.name for p in images_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")
            and "__seed42" not in p.name),
        "images": images,
    }


# ─────────────────────────────────────────────────────────────
# 2) 事实 diff：源 IR vs 再反推 IR
# ─────────────────────────────────────────────────────────────

def _facts(ir) -> list:
    """IR → 事实列表（与 expand.fixed_items 同口径；不引入第二套口径）。"""
    from moss_llm.smith.expand import fixed_items
    return [{"kind": f.kind, "value": f.value, "hard": bool(f.hard),
             "origin": f.origin}
            for f in fixed_items(ir)]


def _fact_key(fact: dict) -> tuple:
    return (fact["kind"], _norm(fact["value"]))


def compare_irs(source_ir, reverse_ir, image_id: str = "") -> dict:
    """两份 IR 的事实集对称差（lost = 源有、再反推没有；extra 反之）。

    文本事实用 `expand.is_ir_text` 的**同一口径**：反推 IR 的 text_content 若只是
    源块的一行/连续子串（或被弯引号重新包裹），算同一事实，不因换行/引用方式
    报 lost+extra。非文本事实仍是严格等值（年龄/颜色/方位不宽容）。
    """
    from moss_llm.smith.expand import is_ir_text

    src = _facts(source_ir)
    rev = _facts(reverse_ir)
    src_keys = {_fact_key(f): f for f in src}
    rev_keys = {_fact_key(f): f for f in rev}
    lost_raw = [src_keys[k] for k in sorted(set(src_keys) - set(rev_keys))]
    extra_raw = [rev_keys[k] for k in sorted(set(rev_keys) - set(src_keys))]

    def text_match(a: dict, b: dict) -> bool:
        if a.get("kind") != "text" or b.get("kind") != "text":
            return False
        return is_ir_text(a["value"], [b["value"]]) or is_ir_text(b["value"], [a["value"]])

    def keep_unmatched(items, others):
        out = []
        for f in items:
            if f.get("kind") == "text" and any(text_match(f, o) for o in others):
                continue
            out.append(f)
        return out

    lost = keep_unmatched(lost_raw, rev)
    extra = keep_unmatched(extra_raw, src)

    def by_kind(items) -> dict:
        out: dict = {}
        for f in items:
            out[f["kind"]] = out.get(f["kind"], 0) + 1
        return dict(sorted(out.items()))

    text_lost = [f["value"] for f in lost if f["kind"] == "text"]
    text_extra = [f["value"] for f in extra if f["kind"] == "text"]
    denom = len(src)
    return {
        "image_id": image_id,
        "source_facts": denom,
        "reverse_facts": len(rev),
        "lost": lost,
        "extra": extra,
        "lost_count": len(lost),
        "extra_count": len(extra),
        "lost_by_kind": by_kind(lost),
        "extra_by_kind": by_kind(extra),
        # 分母是源 IR 的事实数；分母 0 → None（不写 0/1）
        "lost_rate": _rate(len(lost), denom),
        "extra_rate": _rate(len(extra), denom),
        "text_lost": text_lost,
        "text_extra": text_extra,
        "text_source_count": sum(1 for f in src if f["kind"] == "text"),
        "hard_lost_count": sum(1 for f in lost if f.get("hard")),
        "ok": not lost and not extra,
    }


def _payload_ir(payload: dict, what: str):
    from moss_llm.smith.ir import IR
    if not isinstance(payload.get("ir"), dict):
        raise SystemExit("%s 里没有 ir 对象" % what)
    return IR.from_dict(payload["ir"])


def build_summary(images: list, source_json_root: str | Path,
                  reverse_json_root: str | Path, max_examples: int = 20) -> dict:
    """逐图 diff + 汇总（失败图单列；分母只算成功再反推的图）。"""
    src_root = Path(source_json_root)
    rev_root = Path(reverse_json_root)
    rows, failed = [], []
    reverse_ok = reverse_failed = 0
    for img in images:
        gid = str(img["id"])
        rev_path = rev_root / ("%s.json" % gid)
        src_path = src_root / ("%s.json" % gid)
        if not src_path.is_file():
            failed.append({"id": gid, "stage": "source", "error": "缺源 IR %s" % src_path})
            continue
        if not rev_path.is_file():
            reverse_failed += 1
            failed.append({"id": gid, "stage": "reverse", "error": "缺再反推记录 %s" % rev_path})
            continue
        try:
            src = _load_json(src_path, "源 IR")
            rev = _load_json(rev_path, "再反推记录")
        except SystemExit as exc:
            failed.append({"id": gid, "stage": "load", "error": str(exc)})
            continue
        if not rev.get("ok"):
            reverse_failed += 1
            failed.append({"id": gid, "stage": "reverse",
                           "error": str(rev.get("error") or "(unknown)")[:300]})
            continue
        try:
            row = compare_irs(_payload_ir(src, "源 IR"), _payload_ir(rev, "再反推记录"),
                              image_id=gid)
        except (SystemExit, Exception) as exc:
            failed.append({"id": gid, "stage": "diff",
                           "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
            continue
        reverse_ok += 1
        row["category"] = str(img.get("category") or "")
        rows.append(row)

    lost_total = sum(r["lost_count"] for r in rows)
    extra_total = sum(r["extra_count"] for r in rows)
    fact_total = sum(r["source_facts"] for r in rows)
    lost_kind: dict = {}
    extra_kind: dict = {}
    for r in rows:
        for k, n in r["lost_by_kind"].items():
            lost_kind[k] = lost_kind.get(k, 0) + n
        for k, n in r["extra_by_kind"].items():
            extra_kind[k] = extra_kind.get(k, 0) + n
    examples = [{"id": r["image_id"], "kind": f["kind"], "value": f["value"]}
                for r in rows for f in r["lost"]][:max(0, int(max_examples))]
    return {
        "schema": "wash-roundtrip-summary/1",
        "generated_by": "tools/wash_roundtrip.py diff",
        "model_key": MODEL_KEY,
        "source_json_root": str(src_root),
        "reverse_json_root": str(rev_root),
        "stage": {
            "images": len(images),
            "diff_ok": len(rows),
            "reverse_ok": reverse_ok,
            "reverse_failed": reverse_failed,
            "failed": len(failed),
        },
        "failed": failed,
        "totals": {
            "source_facts": fact_total,
            "reverse_facts": sum(r["reverse_facts"] for r in rows),
            "lost": lost_total,
            "extra": extra_total,
            "lost_rate": _rate(lost_total, fact_total),
            "extra_rate": _rate(extra_total, fact_total),
            "text_source": sum(r["text_source_count"] for r in rows),
            "text_lost": sum(len(r["text_lost"]) for r in rows),
            "text_extra": sum(len(r["text_extra"]) for r in rows),
            "hard_lost": sum(r["hard_lost_count"] for r in rows),
            "lost_by_kind": dict(sorted(lost_kind.items())),
            "extra_by_kind": dict(sorted(extra_kind.items())),
        },
        "typical_lost": examples,
        "rows": rows,
    }


def format_report(summary: dict) -> str:
    """汇总 → 人读表（失败图与典型 lost 单列）。"""
    st, tot = summary["stage"], summary["totals"]

    def pct(x):
        return "n/a（分母 0）" if x is None else "%.1f%%" % (100.0 * x)

    lines = [
        "洗图 roundtrip 事实保全 · 模型 %s" % summary.get("model_key"),
        "阶段：应有 %d 图 | diff 成功 %d | 再反推失败/缺记录 %d"
        % (st["images"], st["diff_ok"], st["failed"]),
        "事实：源 %d 条 | 再反推 %d 条 | lost %d（%s）| extra %d（%s）"
        % (tot["source_facts"], tot["reverse_facts"], tot["lost"],
           pct(tot["lost_rate"]), tot["extra"], pct(tot["extra_rate"])),
        "文字实体（不跑 OCR）：源 %d 条 | lost %d 条 | extra %d 条"
        % (tot["text_source"], tot["text_lost"], tot["text_extra"]),
        "hard 型事实丢失：%d 条" % tot["hard_lost"],
        "按类 lost：%s" % (json.dumps(tot["lost_by_kind"], ensure_ascii=False)
                           if tot["lost_by_kind"] else "（无）"),
        "按类 extra：%s" % (json.dumps(tot["extra_by_kind"], ensure_ascii=False)
                            if tot["extra_by_kind"] else "（无）"),
    ]
    if summary["failed"]:
        lines.append("失败图（单列，不进分母）：")
        for f in summary["failed"]:
            lines.append("  - %s [%s] %s" % (f["id"], f["stage"], f["error"][:140]))
    if summary["typical_lost"]:
        lines.append("典型 lost（前 %d 条）：" % len(summary["typical_lost"]))
        for e in summary["typical_lost"]:
            lines.append("  - %s [%s] %s" % (e["id"], e["kind"], e["value"]))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 3) W6 归因拆解：多臂再反推计划 + 三态分类（纯函数）
# ─────────────────────────────────────────────────────────────

def parse_models(spec: str, repeats: int = 1) -> list:
    """``--models "A-4B,B-9B"``（可带 ``:N`` 覆盖该模型的重复次数）→ 归一化臂列表。

    - 默认每个模型跑 ``repeats`` 遍（seed = base_seed, base_seed+1, ...）；
    - ``B-9B:2`` 表示该模型单独跑 2 遍；没写 ``:N`` 的沿用全局 ``repeats``；
    - 保持用户给的顺序、去重（重复出现取最后一次的重复次数）。
    """
    out, index = [], {}
    for raw in [s.strip() for s in (spec or "").split(",") if s.strip()]:
        name, _, cnt = raw.partition(":")
        name = name.strip()
        if not name:
            continue
        try:
            n = int(cnt) if cnt else int(repeats)
        except ValueError as exc:
            raise SystemExit("--models 里 %r 的重复次数不是整数：%r" % (raw, cnt)) from exc
        if n < 1:
            raise SystemExit("--models 里 %r 的重复次数必须 ≥1：%d" % (raw, n))
        if name in index:
            out[index[name]] = {"model": name, "repeats": n}
        else:
            index[name] = len(out)
            out.append({"model": name, "repeats": n})
    if not out:
        raise SystemExit("--models 为空：至少给一个模型，例如 B-9B 或 A-4B,B-9B")
    return out


def plan_recaption(models: str, repeats: int, base_seed: int,
                   out_root: str | Path, run_root: str | Path | None = None) -> list:
    """多臂再反推的确定性计划（纯函数；GPU 执行严格按此顺序）。

    输出目录布局：``<out_root>/<model>/seed<seed>/<img>.json``，
    run 日志：``<run_root>/<model>/seed<seed>/``（默认 run_root=out_root/run）。
    """
    out_root = Path(out_root)
    run_root = Path(run_root) if run_root else out_root / "run"
    arms = []
    for spec in parse_models(models, repeats):
        for i in range(spec["repeats"]):
            seed = int(base_seed) + i
            arms.append({
                "model": spec["model"],
                "seed": seed,
                "repeat_index": i,
                "json_root": out_root / spec["model"] / ("seed%d" % seed),
                "run_dir": run_root / spec["model"] / ("seed%d" % seed),
            })
    return arms


def _payload_dir(json_root, model: str) -> Path:
    """g7_caption 会再拼一层 ``<model>/``：兼容「根目录直接放 JSON」与「嵌套一层」两种布局。"""
    root = Path(json_root)
    nested = root / str(model)
    return nested if nested.is_dir() else root


def scan_arm_payloads(arms: list) -> dict:
    """读每个臂的落盘 JSON → ``{(model, seed): {img_id: payload}}``（缺失=没跑）。"""
    runs = {}
    for arm in arms:
        root = _payload_dir(arm["json_root"], arm["model"])
        payloads = {}
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                try:
                    payloads[path.stem] = _load_json(path, "再反推记录")
                except SystemExit:
                    continue
        runs[(arm["model"], int(arm["seed"]))] = payloads
    return runs


def discover_arms(reverse_root: str | Path) -> list:
    """从 ``<reverse_root>/<model>/seed<seed>/`` 目录树反推臂列表。"""
    root = Path(reverse_root)
    arms = []
    if not root.is_dir():
        return arms
    for model_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if model_dir.name in ("retry", "run"):     # 重试/日志不在主归因里
            continue
        for seed_dir in sorted(p for p in model_dir.iterdir()
                               if p.is_dir() and p.name.startswith("seed")):
            try:
                seed = int(seed_dir.name[4:])
            except ValueError:
                continue
            arms.append({"model": model_dir.name, "seed": seed,
                         "json_root": seed_dir,
                         "run_dir": model_dir / "run" / seed_dir.name})
    return arms


def find_failed(payloads: dict) -> list:
    """某臂里失败/缺失（ok != true）的图 id；失败单列、不静默。"""
    return sorted(k for k, v in payloads.items()
                  if not (isinstance(v, dict) and v.get("ok")))


def collect_failed_ids(runs: dict, image_ids: list) -> list:
    """多臂里「至少一个臂失败/缺记录」的图 id（重试对象；纯函数，可单测）。"""
    bad = set()
    for payloads in runs.values():
        for gid in image_ids:
            p = payloads.get(gid)
            if not (isinstance(p, dict) and p.get("ok")):
                bad.add(str(gid))
    return sorted(bad)


def retry_plan(failed_ids: list, models: str, base_seed: int, repeats: int,
               seeds: list | None = None, out_root: str | Path = DEFAULT_OUT_DIR,
               run_root: str | Path | None = None) -> list:
    """失败图重试计划：每张失败图 × 模型 × seeds；已跑过的 (model,seed) 跳过。

    ``seeds`` 默认从 ``base_seed + repeats`` 起取两档（不重复烧已跑过的 seed）。
    """
    seeds = [int(s) for s in seeds] if seeds else [int(base_seed) + int(repeats) + k
                                                   for k in (0, 1)]
    model_names = [m["model"] for m in parse_models(models, 1)]
    out_root = Path(out_root)
    run_root = Path(run_root) if run_root else out_root / "run"
    plan = []
    for img_id in sorted({str(i) for i in failed_ids}):
        for model in model_names:
            for seed in seeds:
                plan.append({
                    "image_id": img_id, "model": model, "seed": int(seed),
                    "json_root": out_root / "retry" / model / ("seed%d" % int(seed)),
                    "run_dir": run_root / "retry" / model / ("seed%d" % int(seed)),
                })
    return plan


def _facts_by_kind(ir) -> dict:
    out: dict = {}
    for f in _facts(ir):
        out.setdefault(f["kind"], []).append(f)
    return out


def attribute(source_payloads: dict, runs: dict, images: list,
              b_model: str = MODEL_KEY, a_models: tuple = ("A-4B",)) -> dict:
    """三态归因：stable_lost / unstable_lost / cross_model_only_lost / never_lost。

    - 只统计「至少有 1 次 B 成功再反推」的图；其余单列 uncovered、不进分母。
    - 每个源事实在 B 的**成功重复**里看 lost 次数：
      * 全丢 → ``stable_lost``（更像生成图/视觉差异，不是单次采样方差）
      * 部分丢 → ``unstable_lost``（反推方差）
      * 全保留 → 若 A 有成功臂且丢过 → ``cross_model_only_lost``；否则 ``never_lost``
    - 分母 = 被覆盖图的源事实数；为 0 时率报 ``None``（不写 1.0）。
    """
    b_model = str(b_model)
    a_models = tuple(a_models)
    b_runs = {seed: payloads for (model, seed), payloads in runs.items()
              if model == b_model}
    a_runs = {seed: payloads for (model, seed), payloads in runs.items()
              if model in a_models}
    rows, uncovered, covered_images = [], [], []
    for img in images:
        gid = str(img["id"])
        src = source_payloads.get(gid)
        if not src or not src.get("ok"):
            uncovered.append({"id": gid, "why": "缺源 IR 或源 IR 失败"})
            continue
        b_ok = {seed: p for seed, p in b_runs.items()
                if isinstance(p.get(gid), dict) and p[gid].get("ok")}
        if not b_ok:
            uncovered.append({"id": gid, "why": "B 臂无成功再反推"})
            continue
        covered_images.append(gid)
        source_facts = _facts(_payload_ir(src, "源 IR"))
        b_sets = {seed: {_fact_key(f) for f in _facts(_payload_ir(p[gid], "B 再反推"))}
                  for seed, p in b_ok.items()}
        a_sets = {seed: {_fact_key(f) for f in _facts(_payload_ir(p[gid], "A 再反推"))}
                  for seed, p in a_runs.items()
                  if isinstance(p.get(gid), dict) and p[gid].get("ok")}
        for f in source_facts:
            key = _fact_key(f)
            b_lost = [key not in s for s in b_sets.values()]
            if all(b_lost):
                klass = "stable_lost"
            elif any(b_lost):
                klass = "unstable_lost"
            else:
                a_lost = [key not in s for s in a_sets.values()]
                klass = "cross_model_only_lost" if any(a_lost) else "never_lost"
            rows.append({"image": gid, "kind": f["kind"], "value": f["value"],
                         "hard": bool(f.get("hard")), "class": klass,
                         "b_repeats": len(b_sets), "a_repeats": len(a_sets)})

    classes = ("stable_lost", "unstable_lost", "cross_model_only_lost", "never_lost")
    counts = dict.fromkeys(classes, 0)
    by_kind: dict = {}
    by_image: dict = {}
    for r in rows:
        counts[r["class"]] += 1
        box = by_kind.setdefault(r["kind"], dict.fromkeys(classes, 0))
        box[r["class"]] += 1
        ibox = by_image.setdefault(r["image"], {"facts": 0,
                                                **dict.fromkeys(classes, 0)})
        ibox["facts"] += 1
        ibox[r["class"]] += 1
    denom = len(rows)
    hard = [r for r in rows if r["hard"]]
    hard_counts = dict.fromkeys(classes, 0)
    for r in hard:
        hard_counts[r["class"]] += 1
    arm_ok, arm_failed = {}, {}
    for (model, seed), payloads in sorted(runs.items()):
        label = "%s@%d" % (model, seed)
        arm_ok[label] = sum(1 for v in payloads.values()
                            if isinstance(v, dict) and v.get("ok"))
        failed = find_failed(payloads)
        if failed:
            arm_failed[label] = [{"id": i,
                                  "error": str((payloads.get(i) or {}).get("error")
                                               or "缺记录")[:160]}
                                 for i in failed]

    def examples(klass, limit=15):
        return [{"id": r["image"], "kind": r["kind"], "value": r["value"]}
                for r in rows if r["class"] == klass][:limit]

    return {
        "schema": "wash-roundtrip-attribution/1",
        "generated_by": "tools/wash_roundtrip.py attribution",
        "b_model": b_model, "a_models": list(a_models),
        "arms": {"ok": arm_ok, "failed": arm_failed},
        "images": {"total": len(images), "covered": len(covered_images),
                   "uncovered": uncovered},
        "totals": {
            "facts": denom,
            "class_counts": counts,
            "rates": {k: _rate(counts[k], denom) for k in classes},
            "hard": {"facts": len(hard), "class_counts": hard_counts,
                     "rates": {k: _rate(hard_counts[k], len(hard))
                               for k in classes}},
        },
        "by_kind": by_kind,
        "by_image": [{"id": k, **v} for k, v in sorted(by_image.items())],
        "examples": {k: examples(k) for k in classes},
    }


def format_attribution(summary: dict) -> str:
    """三态归因汇总 → 人读表。"""
    st, tot = summary["arms"], summary["totals"]
    lines = [
        "洗图 roundtrip 归因（B=%s × 多重复 | A=%s 交叉）"
        % (summary["b_model"], ",".join(summary["a_models"])),
        "臂成功数：%s" % json.dumps(st["ok"], ensure_ascii=False),
        "图覆盖：%d/%d（uncovered %d）"
        % (summary["images"]["covered"], summary["images"]["total"],
           len(summary["images"]["uncovered"])),
        "事实分母：%d 条" % tot["facts"],
    ]
    for k in ("stable_lost", "unstable_lost", "cross_model_only_lost", "never_lost"):
        rate = tot["rates"][k]
        lines.append("  %-22s %4d（%s）" % (
            k, tot["class_counts"][k],
            "n/a（分母 0）" if rate is None else "%.1f%%" % (100.0 * rate)))
    lines.append("hard 事实（%d 条）：stable %d / unstable %d / cross-model %d / never %d"
                 % (tot["hard"]["facts"],
                    tot["hard"]["class_counts"]["stable_lost"],
                    tot["hard"]["class_counts"]["unstable_lost"],
                    tot["hard"]["class_counts"]["cross_model_only_lost"],
                    tot["hard"]["class_counts"]["never_lost"]))
    if summary["arms"]["failed"]:
        lines.append("失败臂/图（单列，不进分母）：")
        for arm, items in summary["arms"]["failed"].items():
            lines.append("  - %s：%s" % (arm, ", ".join(
                "%s（%s）" % (i["id"], i["error"][:50]) for i in items)))
    for k in ("stable_lost", "unstable_lost", "cross_model_only_lost"):
        ex = summary["examples"][k]
        if ex:
            lines.append("%s 例子（前 %d）：" % (k, len(ex)))
            for e in ex:
                lines.append("  - %s [%s] %s" % (e["id"], e["kind"], e["value"]))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def cmd_images(args) -> int:
    images = build_images(args.run_json, args.images_dir, args.g7_images,
                          args.source_json_root)
    _dump(Path(args.out), images)
    print("已写 %s：%d 张（seed=42）" % (args.out, images["image_count"]))
    for img in images["images"]:
        print("  %s  %-28s %s" % (img["id"], img["name"], img["category"]))
    if images["skipped"]:
        print("跳过 %d 张（源 IR 失败态）：%s" % (len(images["skipped"]),
                                              "; ".join(images["skipped"][:5])))
    return 0


def cmd_diff(args) -> int:
    images = _load_json(args.images, "生成图清单").get("images") or []
    if not images:
        raise SystemExit("%s 里没有 images（先跑 images 子命令）" % args.images)
    summary = build_summary(images, args.source_json_root, args.reverse_json_root,
                            max_examples=args.max_examples)
    _dump(Path(args.out), summary)
    print(format_report(summary))
    print("\n已写 %s" % args.out)
    return 0


def cmd_report(args) -> int:
    print(format_report(_load_json(args.summary, "汇总")))
    return 0


def cmd_recaption(args) -> int:
    """GPU 再反推：转发给 tools/g7_caption.py。

    - 不给 ``--out-root`` 时保持 W3 的旧行为（单臂 B-9B，沿用 --json-root/--run-dir）；
    - 给 ``--out-root`` 时按 ``plan_recaption`` 的多臂布局跑
      ``<out_root>/<model>/seed<seed>/``，可选 ``--retry-failed`` 对失败图换 seed/模型重试。
    """
    sys.path.insert(0, str(ROOT))
    from tools import g7_caption

    def call(model: str, seed: int, json_root, run_dir, ids: str = "") -> int:
        argv = ["--models", model, "--images", str(args.images),
                "--json-root", str(json_root), "--run-dir", str(run_dir),
                "--seed", str(int(seed))]
        if args.overwrite:
            argv.append("--overwrite")
        if ids:
            argv += ["--ids", ids]
        return g7_caption.main(argv)

    if not args.out_root:                       # 旧默认行为（W3 兼容）
        argv = ["--models", args.models or MODEL_KEY, "--images", str(args.images),
                "--json-root", str(args.json_root), "--run-dir", str(args.run_dir)]
        if args.overwrite:
            argv.append("--overwrite")
        if args.ids:
            argv += ["--ids", args.ids]
        return g7_caption.main(argv)

    out_root = Path(args.out_root)
    arms = plan_recaption(args.models or MODEL_KEY, args.repeats, args.base_seed,
                          out_root, args.run_root)
    summaries = []
    for arm in arms:
        rc = call(arm["model"], arm["seed"], arm["json_root"], arm["run_dir"],
                  args.ids)
        summaries.append({"model": arm["model"], "seed": arm["seed"],
                          "json_root": str(arm["json_root"]), "rc": rc})
        print("[arm] %s@%d → rc=%d" % (arm["model"], arm["seed"], rc))
    _dump(out_root / "recaption-summary.json",
          {"generated_by": "tools/wash_roundtrip.py recaption",
           "models": args.models, "repeats": args.repeats,
           "base_seed": args.base_seed, "arms": summaries})

    if args.retry_failed:
        image_ids = [str(i["id"]) for i in
                     (_load_json(args.images, "生成图清单").get("images") or [])]
        failed = collect_failed_ids(scan_arm_payloads(arms), image_ids)
        if failed:
            seeds = [int(s) for s in args.retry_seeds.split(",") if s.strip()]
            plan = retry_plan(failed, args.models or MODEL_KEY, args.base_seed,
                              args.repeats, seeds or None, out_root=out_root)
            groups: dict = {}
            for item in plan:
                key = (item["model"], item["seed"],
                       str(item["json_root"]), str(item["run_dir"]))
                groups.setdefault(key, []).append(item["image_id"])
            retry_summary = {}
            for (model, seed, json_root, run_dir), ids in sorted(groups.items()):
                rc = call(model, seed, json_root, run_dir, ",".join(sorted(set(ids))))
                retry_summary["%s@%d" % (model, seed)] = {"rc": rc, "images": ids}
                print("[retry] %s@%d ids=%s → rc=%d" % (model, seed, ids, rc))
            plan_dump = [{k: (str(v) if isinstance(v, Path) else v)
                          for k, v in item.items()} for item in plan]
            _dump(out_root / "retry-summary.json",
                  {"generated_by": "tools/wash_roundtrip.py recaption --retry-failed",
                   "failed": failed, "plan": plan_dump,
                   "results": retry_summary})
        else:
            print("[retry] 没有失败图，跳过重试")
    return 0 if all(s["rc"] == 0 for s in summaries) else 1


def cmd_attribution(args) -> int:
    """扫多臂落盘 → 三态归因（stable/unstable/cross-model/never）+ 失败单列。"""
    images = _load_json(args.images, "生成图清单").get("images") or []
    if not images:
        raise SystemExit("%s 里没有 images（先跑 images 子命令）" % args.images)
    src_root = Path(args.source_json_root)
    source_payloads = {}
    for img in images:
        gid = str(img["id"])
        path = src_root / ("%s.json" % gid)
        if path.is_file():
            source_payloads[gid] = _load_json(path, "源 IR")
    arms = discover_arms(args.reverse_root)
    if not arms:
        raise SystemExit(
            "在 %s 下没找到 <model>/seed<seed>/ 再反推目录；\n"
            "  先跑：wash_roundtrip.py recaption --models B-9B:2,A-4B:1 "
            "--out-root <reverse_root>" % args.reverse_root)
    summary = attribute(source_payloads, scan_arm_payloads(arms), images,
                        b_model=args.b_model,
                        a_models=tuple(s.strip() for s in args.a_models.split(",")
                                       if s.strip()))
    _dump(Path(args.out), summary)
    print(format_attribution(summary))
    print("\n已写 %s" % args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="W3 洗图 roundtrip 事实保全（images / diff / report / recaption）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("images", help="W1 产物 → out/wash_roundtrip/images.json（seed=42）")
    a.add_argument("--run-json", default=str(DEFAULT_W1_RUN_DIR / "run.json"))
    a.add_argument("--images-dir", default=str(DEFAULT_IMAGES_DIR))
    a.add_argument("--g7-images", default=str(DEFAULT_G7_IMAGES))
    a.add_argument("--source-json-root", default=str(DEFAULT_SOURCE_JSON))
    a.add_argument("--out", default=str(DEFAULT_IMAGES_OUT))
    a.set_defaults(func=cmd_images)

    b = sub.add_parser("diff", help="再反推 JSON vs 源 IR 的事实 diff/汇总")
    b.add_argument("--images", default=str(DEFAULT_IMAGES_OUT))
    b.add_argument("--source-json-root", default=str(DEFAULT_SOURCE_JSON))
    b.add_argument("--reverse-json-root", default=str(DEFAULT_REVERSE_ROOT))
    b.add_argument("--out", default=str(DEFAULT_SUMMARY))
    b.add_argument("--max-examples", type=int, default=20)
    b.set_defaults(func=cmd_diff)

    c = sub.add_parser("report", help="打印已有 summary.json")
    c.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    c.set_defaults(func=cmd_report)

    d = sub.add_parser("recaption",
                       help="GPU 再反推（默认 B-9B 单臂；--out-root 走多臂 W6 布局）")
    d.add_argument("--images", default=str(DEFAULT_IMAGES_OUT))
    d.add_argument("--json-root", default=str(DEFAULT_OUT_DIR / "reverse"),
                   help="旧单臂模式的输出根（不给 --out-root 时生效）")
    d.add_argument("--run-dir", default=str(DEFAULT_RECAPTION_RUN))
    d.add_argument("--models", default="",
                   help="逗号分隔；支持 A-4B,B-9B 或 B-9B:2,A-4B:1（:N 覆盖重复次数）")
    d.add_argument("--repeats", type=int, default=1,
                   help="每个模型重复几遍（seed = base_seed, base_seed+1, ...）")
    d.add_argument("--base-seed", dest="base_seed", type=int, default=42)
    d.add_argument("--out-root", default="",
                   help="多臂布局根（<root>/<model>/seed<seed>/）；留空=旧单臂行为")
    d.add_argument("--run-root", default="", help="多臂日志根（默认 <out-root>/run）")
    d.add_argument("--retry-failed", action="store_true",
                   help="对失败图换 seed/模型重试（记录成功/仍失败）")
    d.add_argument("--retry-seeds", default="",
                   help="重试 seed，逗号分隔；默认 base_seed+repeats 起两档")
    d.add_argument("--ids", default="")
    d.add_argument("--overwrite", action="store_true")
    d.set_defaults(func=cmd_recaption)

    e = sub.add_parser("attribution",
                       help="W6 三态归因：stable/unstable/cross-model/never lost")
    e.add_argument("--images", default=str(DEFAULT_IMAGES_OUT))
    e.add_argument("--source-json-root", default=str(DEFAULT_SOURCE_JSON))
    e.add_argument("--reverse-root",
                   default=str(DEFAULT_OUT_DIR / "attribution" / "reverse"))
    e.add_argument("--b-model", dest="b_model", default=MODEL_KEY)
    e.add_argument("--a-models", dest="a_models", default="A-4B")
    e.add_argument("--out",
                   default=str(DEFAULT_OUT_DIR / "attribution" / "attribution.json"))
    e.set_defaults(func=cmd_attribution)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

