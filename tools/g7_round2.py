# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 第二轮：两轮并排对比 + 第一轮产物完整性校验（纯离线、零 GPU）。

为什么单独一个工具：第二轮的问题不是「再跑一遍」，而是**同一批图、同一个口径，只换代码**，
要能回答「修复带来了什么」。所以这里固定四项对比（docs/41 §M1 的验收口径）：

| 指标 | 定义 | 分母 |
|---|---|---|
| 材质绑定覆盖率 | `material` 类的 bound / applicable（逐实例，见 `tools/g7_binding.py`） | 各自 IR 里记录材质属性的实体数 |
| 物体编年龄幻觉数 | 客观规则：`kind=character` 但**无人物字段**（gender/hair/skin/face/expression）**却有物体字段**（material/color/build/detail/tags）的实体里，`age_years` 非空的个数 | 每轮每模型一份，附 mis-kind 总数 |
| 跑批失败率 | `(失败 + 缺记录) / 应有张数`；失败按 finish_reason/错误类型分桶 | 应有张数（每轮每模型） |
| 文字两遍互证率 | 取证遍文字实体中被独立转录遍证实的比例（`g7_binding._text_cross_pass`） | 取证遍文字实体数 |

纪律：**分母为 0 一律报 `n/a`，绝不写成 1.0**；幅度用百分点（pp）并附两侧分母；
两轮设置原样并排（system prompt sha / 采样参数），提示词变了要显式标注「不是口径漂移」。

另附 `manifest` / `verify-manifest`：给「第一轮产物未被改动」留一条可重跑的证据链。

用法::

  python tools/g7_round2.py compare \
      --images out/g7/images.json \
      --r1-json-root out/g7/json --r2-json-root out/g7/round2/json \
      --r1-summary out/g7/run/batch-summary.json \
      --r2-summary out/g7/round2/run/batch-summary.json \
      --out-md out/g7/round2/compare-20.md --out-json out/g7/round2/compare-20.json

  python tools/g7_round2.py manifest  --out out/g7/round1-manifest.sha256
  python tools/g7_round2.py verify-manifest --manifest out/g7/round1-manifest.sha256
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "g7"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.g7_binding import aggregate, measure_record                    # noqa: E402

CLASSES = ("color", "material", "spatial", "text", "count")
CN = {"color": "颜色", "material": "材质", "spatial": "方位", "text": "图内文字",
      "count": "数量"}

#: 物体编年龄的客观规则（与第一轮 docs §7.1 同一口径，不新增主观判断）。
HUMAN_FIELDS = ("gender", "hair", "skin", "face", "expression")
OBJECT_FIELDS = ("material", "color", "build", "detail", "tags")


# ─────────────────────────────────────────────────────────────
# 数据装载
# ─────────────────────────────────────────────────────────────

def load_images(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["images"]


def _iter_model_files(json_root: Path, model: str):
    d = json_root / model
    return sorted(d.glob("*.json")) if d.is_dir() else []


def build_round(json_root: str | Path, images: list[dict],
                models: list[str]) -> dict:
    """把一轮的落盘 JSON 变成可对比的指标包。缺文件不是崩溃，是「缺记录」。"""
    json_root = Path(json_root)
    meta = {i["id"]: i for i in images}
    records, payloads, missing = [], {}, {m: [] for m in models}
    for model in models:
        present = {p.stem for p in _iter_model_files(json_root, model)}
        for img in images:
            img_id = img["id"]
            if img_id not in present:
                missing[model].append(img_id)
                continue
            path = json_root / model / ("%s.json" % img_id)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payloads[(model, img_id)] = payload
            records.append(measure_record(payload, meta[img_id]))

    summary = aggregate(records, models)
    stats = {}
    for model in models:
        recs = [r for r in records if r["model"] == model]
        ok = sum(1 for r in recs if r.get("ok"))
        failed = len(recs) - ok
        expected = len(images)
        miss = len(missing[model])
        reasons = Counter()
        for (m, _img_id), payload in payloads.items():
            if m != model or payload.get("ok"):
                continue
            calls = payload.get("calls") or []
            finish = (calls[0].get("finish_reason") if calls else "") or "unknown"
            err = str(payload.get("error") or "")
            kind = "finish=" + finish
            if "没有闭合" in err or "截断" in err:
                kind += "/JSON未闭合"
            reasons[kind] += 1
        # 物体编年龄（客观规则）
        mis_total = mis_age = age_gate = 0
        for (m, _img_id), payload in payloads.items():
            if m != model or not payload.get("ok"):
                continue
            for e in (payload.get("ir") or {}).get("entities") or []:
                if e.get("kind") != "character":
                    continue
                d = e.get("descriptors") or {}
                has_human = any(d.get(k) for k in HUMAN_FIELDS)
                has_object = any(d.get(k) for k in OBJECT_FIELDS)
                if has_human or not has_object:
                    continue
                mis_total += 1
                if e.get("age_years") is not None:
                    mis_age += 1
            age_gate += len((payload.get("report") or {}).get("age_gate") or [])
        stats[model] = {
            "expected": expected, "present": len(recs), "ok": ok,
            "failed": failed, "missing": miss,
            "failure_rate": ((failed + miss) / expected) if expected else None,
            "failure_reasons": dict(reasons),
            "object_like_characters": mis_total,
            "object_age_hallucinations": mis_age,
            "object_age_rate": (mis_age / mis_total) if mis_total else None,
            "age_gate_items": age_gate,
        }
    return {"json_root": str(json_root), "summary": summary,
            "stats": stats, "missing": missing}


# ─────────────────────────────────────────────────────────────
# 各指标视图（对比时不再回头翻原始记录）
# ─────────────────────────────────────────────────────────────

def _cov(bound: int, applicable: int):
    return (bound / applicable) if applicable else None


def _pp(r1, r2) -> tuple[str, float | None]:
    """两点覆盖率 → (方向, 百分点差)。任一侧 n/a 则方向 n/a。"""
    if r1 is None or r2 is None:
        return "n/a", None
    d = (r2 - r1) * 100.0
    return ("↑" if d > 0.05 else "↓" if d < -0.05 else "="), round(d, 1)


def material_rows(build: dict, images: list[dict], model: str) -> dict:
    """材质覆盖率：整体 + 按图片类别。返回 {key: {bound, applicable}}。"""
    cats = {i["id"]: i["category"] for i in images}
    per_image = build["summary"]["models"][model]["classes"]["material"]["per_image"]
    rows: dict[str, dict] = {}
    for key in ("__all__",) + tuple(dict.fromkeys(cats.values())):
        b = a = 0
        for img_id, cell in per_image.items():
            if key != "__all__" and cats.get(img_id) != key:
                continue
            b += cell["bound"]
            a += cell["applicable"]
        rows[key] = {"bound": b, "applicable": a, "coverage": _cov(b, a)}
    return rows


def compare(build1: dict, build2: dict, images: list[dict],
            models: list[str], s1: dict | None = None,
            s2: dict | None = None) -> dict:
    """两轮 → 对比数据结构（JSON 可序列化，报告层再排版）。"""
    out = {
        "classes": list(CLASSES),
        "models": models,
        "images": len(images),
        "settings": {"r1": _settings_of(s1), "r2": _settings_of(s2)},
        "binding": {}, "material": {}, "object_age": {}, "failure": {},
        "text_cross_pass": {}, "warnings": [],
    }
    if s1 and s2:
        sbox_a = out["settings"]["r1"] or {}
        sbox_b = out["settings"]["r2"] or {}
        a = sbox_a.get("settings") or {}
        b = sbox_b.get("settings") or {}
        for k in ("ctx", "max_tokens", "max_side", "vision_tokens", "seed",
                  "temperature", "text_pass", "schema", "port"):
            if a.get(k) != b.get(k):
                out["warnings"].append("采样参数不一致（%s）：%r vs %r"
                                        % (k, a.get(k), b.get(k)))
        if sbox_a.get("system_prompt_sha256") != sbox_b.get("system_prompt_sha256"):
            out["warnings"].append(
                "system prompt sha 变了（%s… → %s…）：这是 task-16 修复的一部分，"
                "不是口径漂移，但报告里要显式标注"
                % (str(sbox_a.get("system_prompt_sha256"))[:12],
                   str(sbox_b.get("system_prompt_sha256"))[:12]))

    for model in models:
        mrow = {}
        for cls in CLASSES:
            c1 = build1["summary"]["models"][model]["classes"][cls]
            c2 = build2["summary"]["models"][model]["classes"][cls]
            direction, delta = _pp(c1["coverage"], c2["coverage"])
            mrow[cls] = {
                "r1": {"bound": c1["bound"], "applicable": c1["applicable"],
                       "coverage": c1["coverage"]},
                "r2": {"bound": c2["bound"], "applicable": c2["applicable"],
                       "coverage": c2["coverage"]},
                "direction": direction, "delta_pp": delta,
            }
        out["binding"][model] = mrow

        rows1 = material_rows(build1, images, model)
        rows2 = material_rows(build2, images, model)
        mat = {}
        for key in rows1:
            direction, delta = _pp(rows1[key]["coverage"], rows2[key]["coverage"])
            mat[key] = {"r1": rows1[key], "r2": rows2[key],
                        "direction": direction, "delta_pp": delta}
        out["material"][model] = mat

        o1 = build1["stats"][model]
        o2 = build2["stats"][model]
        out["object_age"][model] = {
            "mis_kind_total": {"r1": o1["object_like_characters"],
                               "r2": o2["object_like_characters"]},
            "with_age": {"r1": o1["object_age_hallucinations"],
                         "r2": o2["object_age_hallucinations"],
                         "delta": o2["object_age_hallucinations"]
                         - o1["object_age_hallucinations"]},
            "age_gate_items": {"r1": o1["age_gate_items"], "r2": o2["age_gate_items"]},
        }
        out["failure"][model] = {
            "expected": {"r1": o1["expected"], "r2": o2["expected"]},
            "ok": {"r1": o1["ok"], "r2": o2["ok"]},
            "failed": {"r1": o1["failed"], "r2": o2["failed"]},
            "missing": {"r1": o1["missing"], "r2": o2["missing"]},
            "rate": {"r1": o1["failure_rate"], "r2": o2["failure_rate"],
                     "delta_pp": None if (o1["failure_rate"] is None
                                          or o2["failure_rate"] is None)
                     else round((o2["failure_rate"] - o1["failure_rate"]) * 100, 1)},
            "reasons": {"r1": o1["failure_reasons"], "r2": o2["failure_reasons"]},
        }
        cp1 = build1["summary"]["models"][model]["aux"].get("text_cross_pass") or {}
        cp2 = build2["summary"]["models"][model]["aux"].get("text_cross_pass") or {}
        r1 = _cov(cp1.get("corroborated", 0), cp1.get("from_evidence", 0))
        r2 = _cov(cp2.get("corroborated", 0), cp2.get("from_evidence", 0))
        direction, delta = _pp(r1, r2)
        out["text_cross_pass"][model] = {
            "r1": {"corroborated": cp1.get("corroborated", 0),
                   "from_evidence": cp1.get("from_evidence", 0), "rate": r1},
            "r2": {"corroborated": cp2.get("corroborated", 0),
                   "from_evidence": cp2.get("from_evidence", 0), "rate": r2},
            "direction": direction, "delta_pp": delta,
        }
    return out


def _settings_of(summary: dict | None) -> dict | None:
    if not summary:
        return None
    return {"settings": summary.get("settings"),
            "system_prompt_sha256": summary.get("system_prompt_sha256")}


# ─────────────────────────────────────────────────────────────
# 报告排版
# ─────────────────────────────────────────────────────────────

def _cov_str(c: dict | None, key: str = "coverage") -> str:
    if not c:
        return "n/a"
    v = c.get(key) if key in c else c.get("coverage")
    if c.get("applicable") == 0:
        return "n/a（分母 0）"
    return "%s（%d/%d）" % ("%.0f%%" % (v * 100) if v is not None else "n/a",
                           c.get("bound", 0), c.get("applicable", 0))


def format_markdown(cmp: dict, labels: tuple[str, str] = ("第一轮", "第二轮")) -> str:
    l1, l2 = labels
    models = cmp["models"]
    lines = []
    lines.append("### 0. 口径核对")
    lines.append("")
    lines.append("| 项 | %s | %s |" % (l1, l2))
    lines.append("|---|---|---|")
    for key in ("system_prompt_sha256", "settings"):
        v1 = (cmp["settings"]["r1"] or {}).get(key)
        v2 = (cmp["settings"]["r2"] or {}).get(key)
        if key == "settings":
            v1 = json.dumps(v1, ensure_ascii=False, sort_keys=True)
            v2 = json.dumps(v2, ensure_ascii=False, sort_keys=True)
        lines.append("| %s | `%s` | `%s` |" % (key, v1, v2))
    for w in cmp["warnings"]:
        lines.append("")
        lines.append("> ⚠️ " + w)
    lines.append("")

    lines.append("### 1. 材质绑定覆盖率（重点）")
    lines.append("")
    lines.append("| 图组 | 模型 | %s | %s | 方向/幅度 |" % (l1, l2))
    lines.append("|---|---|---|---|---|")
    for model in models:
        for key, mat in cmp["material"][model].items():
            name = (("全部 %d 图" % cmp["images"]) if key == "__all__" else key)
            delta = "n/a" if mat["delta_pp"] is None else "%+.1f pp" % mat["delta_pp"]
            lines.append("| %s | %s | %s | %s | %s %s |" % (
                name, model, _cov_str(mat["r1"]), _cov_str(mat["r2"]),
                mat["direction"], delta))
    lines.append("")

    lines.append("### 2. 物体编年龄幻觉（客观规则，非主观判读）")
    lines.append("")
    lines.append("| 模型 | 项 | %s | %s | 变化 |" % (l1, l2))
    lines.append("|---|---|---|---|---|")
    for model in models:
        o = cmp["object_age"][model]
        lines.append("| %s | 物体被标 character（mis-kind）总数 | %d | %d | — |" % (
            model, o["mis_kind_total"]["r1"], o["mis_kind_total"]["r2"]))
        lines.append("| %s | 其中编了年龄（幻觉数） | %d | %d | %+d |" % (
            model, o["with_age"]["r1"], o["with_age"]["r2"], o["with_age"]["delta"]))
        lines.append("| %s | age_gate 条目（角色缺年龄） | %d | %d | — |" % (
            model, o["age_gate_items"]["r1"], o["age_gate_items"]["r2"]))
    lines.append("")

    lines.append("### 3. 跑批失败率")
    lines.append("")
    lines.append("| 模型 | 应有 | %s ok/失败/缺 | 失败率 %s | %s ok/失败/缺 | 失败率 %s | 失败率变化 |"
                 % (l1, l1, l2, l2))
    lines.append("|---|---|---|---|---|---|---|")
    for model in models:
        f = cmp["failure"][model]
        r1 = "n/a" if f["rate"]["r1"] is None else "%.0f%%" % (f["rate"]["r1"] * 100)
        r2 = "n/a" if f["rate"]["r2"] is None else "%.0f%%" % (f["rate"]["r2"] * 100)
        d = "n/a" if f["rate"]["delta_pp"] is None else "%+.1f pp" % f["rate"]["delta_pp"]
        lines.append("| %s | %d | %d/%d/%d | %s | %d/%d/%d | %s | %s |" % (
            model, f["expected"]["r1"], f["ok"]["r1"], f["failed"]["r1"],
            f["missing"]["r1"], r1, f["ok"]["r2"], f["failed"]["r2"],
            f["missing"]["r2"], r2, d))
    lines.append("")
    for model in models:
        f = cmp["failure"][model]
        lines.append("- %s 失败类型：%s → %s" % (
            model, json.dumps(f["reasons"]["r1"], ensure_ascii=False),
            json.dumps(f["reasons"]["r2"], ensure_ascii=False)))
    lines.append("")

    lines.append("### 4. 文字两遍互证率")
    lines.append("")
    lines.append("| 模型 | %s | %s | 方向/幅度 |" % (l1, l2))
    lines.append("|---|---|---|---|")
    for model in models:
        t = cmp["text_cross_pass"][model]

        def cell(x):
            return "n/a（分母 0）" if not x["from_evidence"] else \
                "%s（%d/%d）" % ("%.0f%%" % (x["rate"] * 100)
                                 if x["rate"] is not None else "n/a",
                                 x["corroborated"], x["from_evidence"])
        delta = "n/a" if t["delta_pp"] is None else "%+.1f pp" % t["delta_pp"]
        lines.append("| %s | %s | %s | %s %s |" % (
            model, cell(t["r1"]), cell(t["r2"]), t["direction"], delta))
    lines.append("")

    lines.append("### 5. 五类覆盖率总表（附分母）")
    lines.append("")
    lines.append("| 类别 | 模型 | %s | %s | 方向/幅度 |" % (l1, l2))
    lines.append("|---|---|---|---|---|")
    for model in models:
        for cls in CLASSES:
            b = cmp["binding"][model][cls]
            delta = "n/a" if b["delta_pp"] is None else "%+.1f pp" % b["delta_pp"]
            lines.append("| %s | %s | %s | %s | %s %s |" % (
                CN[cls], model, _cov_str(b["r1"]), _cov_str(b["r2"]),
                b["direction"], delta))
    lines.append("")
    lines.append("> 读法：分母来自**各自轮次**的 IR，两轮分母可能不同（修复会改变模型记了什么），"
                 "所以覆盖率变化要连同分母一起读；分母为 0 一律 n/a，不写成 1.0。")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 第一轮产物完整性：manifest / verify
# ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST = OUT / "round1-manifest.sha256"

ROUND1_PATTERNS = (
    "images.json", "binding.json", "binding_table.md",
    "inspect/agent.json", "inspect/stats.json",
    "calib/agent_cells.json", "calib/machine_cells.json", "calib/calibration.json",
    "run/batch-summary.json",
    "json/A-4B/*.json", "json/B-9B/*.json",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_manifest(root: str | Path | None = None) -> dict:
    """收集第一轮产物的 sha256。**纯函数：只读，绝不写盘**。

    为什么必须只读：manifest 是「第一轮产物未被改动」的证据链。如果 verify 顺手把
    当前状态写回 manifest，那么「篡改后第一次 verify 报 changed、第二次就变 ok」——
    证据链自己把自己治好了（lang-zh 复核 F1 实测复现）。所以写盘只归 `write_manifest`，
    只有 `manifest` 子命令调它。

    `root` 可指定（单测用临时目录）；默认 `out/g7`。
    """
    base = Path(root) if root else OUT
    files: dict[str, str] = {}
    for pat in ROUND1_PATTERNS:
        for path in sorted(base.glob(pat)):
            if path.is_file():
                files[str(path.relative_to(base)).replace("\\", "/")] = sha256_file(path)
    return files


def write_manifest(files: dict, dest: str | Path = DEFAULT_MANIFEST) -> Path:
    """把快照写盘。**只有 `manifest` 子命令允许调它。**"""
    dest = Path(dest)
    dest.write_text("".join("%s  %s\n" % (files[k], k) for k in sorted(files)),
                    encoding="utf-8")
    return dest


def read_manifest(path: str | Path) -> dict[str, str]:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sha, rel = line.split(None, 1)
        out[rel.strip()] = sha
    return out


def verify_manifest(manifest: str | Path = DEFAULT_MANIFEST,
                    root: str | Path | None = None) -> dict:
    """重算并比对。返回 {ok, changed, missing, extra}；changed/missing 非空即 fail。"""
    old = read_manifest(manifest)
    new = collect_manifest(root=root)        # 纯只读：verify 绝不写 manifest
    changed = sorted(k for k in old if k in new and old[k] != new[k])
    missing = sorted(k for k in old if k not in new)
    extra = sorted(k for k in new if k not in old)
    return {"ok": not changed and not missing, "changed": changed,
            "missing": missing, "extra": extra}


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def cmd_compare(args) -> int:
    images = load_images(args.images)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    b1 = build_round(args.r1_json_root, images, models)
    b2 = build_round(args.r2_json_root, images, models)
    s1 = _read_json(args.r1_summary) if args.r1_summary else None
    s2 = _read_json(args.r2_summary) if args.r2_summary else None
    cmp = compare(b1, b2, images, models, s1, s2)
    md = format_markdown(cmp, (args.label1, args.label2))
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(
            {"generated_by": "tools/g7_round2.py compare", "images": str(args.images),
             "r1_json_root": str(args.r1_json_root),
             "r2_json_root": str(args.r2_json_root),
             "comparison": cmp}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)
    print("\n已写 %s%s" % (out_md, " / " + args.out_json if args.out_json else ""))
    return 0


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_manifest(args) -> int:
    files = collect_manifest()
    write_manifest(files, args.out)
    print("已写 %s：%d 个文件" % (args.out, len(files)))
    for rel in sorted(files)[:8]:
        print("  %s  %s" % (files[rel][:12], rel))
    if len(files) > 8:
        print("  …")
    return 0


def cmd_verify_manifest(args) -> int:
    res = verify_manifest(args.manifest)
    total = len(read_manifest(args.manifest))
    print("第一轮产物完整性：%s（%d 个文件）"
          % ("通过" if res["ok"] else "失败", total))
    for label in ("changed", "missing", "extra"):
        for rel in res[label]:
            print("  %s: %s" % (label, rel))
    return 0 if res["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G7 第二轮：两轮对比 + 第一轮完整性")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compare", help="两轮并排对比（材质/编年龄/失败率/文字互证）")
    c.add_argument("--images", required=True)
    c.add_argument("--r1-json-root", required=True)
    c.add_argument("--r2-json-root", required=True)
    c.add_argument("--r1-summary", default="", help="第一轮 batch-summary.json（口径核对用）")
    c.add_argument("--r2-summary", default="")
    c.add_argument("--models", default="A-4B,B-9B")
    c.add_argument("--label1", default="第一轮")
    c.add_argument("--label2", default="第二轮")
    c.add_argument("--out-md", required=True)
    c.add_argument("--out-json", default="")
    c.set_defaults(func=cmd_compare)

    m = sub.add_parser("manifest", help="给第一轮产物拍 sha256 快照")
    m.add_argument("--out", default=str(DEFAULT_MANIFEST))
    m.set_defaults(func=cmd_manifest)

    v = sub.add_parser("verify-manifest", help="校验第一轮产物未被改动")
    v.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    v.set_defaults(func=cmd_verify_manifest)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
