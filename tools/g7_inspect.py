# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 · 代理目检（AI 看图，**不是人类**）与校准统计。

两件事：

1. `sheet --ids ...`：把某张图的「两个模型产出 + 机检结论」打成一页 markdown，
   供代理目检逐张核对（图本身用 read_image 看，本工具只负责把文字证据摆齐）。
2. `stats`：读 `out/g7/inspect/agent.json`，算
   - 机检 vs 代理目检的一致率（校准硬要求：< 80% 先修判据再出结论）；
   - 事实准确率 / 幻觉率（这是「反推准不准」的直接证据，机检判不了）。

代理目检的边界必须如实写进产出：判读者是 **AI（Claude/DSH 会话）**，不是人类；
结论要写「代理目检」，且 Lead 会独立复核抽检。

agent.json 结构::

    {"images": {"img01": {"A-4B": {
        "facts": [{"claim": "...", "verdict": "correct|wrong|unverifiable"}],
        "hallucination": false,
        "binding": {"color": "pass|fail", "material": ..., "spatial": ...,
                    "text": ..., "count": ...},
        "notes": "..."}, "B-9B": {...}}}}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "g7"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLASSES = ("color", "material", "spatial", "text", "count")


def _load_binding() -> dict:
    return json.loads((OUT / "binding.json").read_text(encoding="utf-8"))


def _load_images() -> dict:
    data = json.loads((OUT / "images.json").read_text(encoding="utf-8"))
    return {i["id"]: i for i in data["images"]}


def _entity_table(ir: dict) -> str:
    rows = ["| id | kind | label | age | anchor | bbox源 | 描述字段 | text |",
            "|---|---|---|---|---|---|---|---|"]
    for e in ir.get("entities") or []:
        desc = []
        d = e.get("descriptors") or {}
        for k in ("gender", "hair", "skin", "face", "wardrobe", "props", "color",
                  "material", "build", "detail", "tags", "expression", "pose"):
            if d.get(k):
                desc.append("%s=%s" % (k, json.dumps(d[k], ensure_ascii=False)))
        sp = e.get("spatial") or {}
        typ = e.get("typography") or {}
        if typ.get("color"):
            desc.append("typography.color=%s" % typ["color"])
        rows.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            e.get("id"), e.get("kind"), e.get("label"), e.get("age_years", ""),
            sp.get("anchor", ""), sp.get("bbox_source", ""),
            "；".join(desc)[:600], json.dumps(e.get("text_content"), ensure_ascii=False)
            if e.get("text_content") else ""))
    return "\n".join(rows)


def _machine_cells(rec: dict) -> str:
    if not rec or not rec.get("ok"):
        return "（无记录 / 记录失败：%s）" % ((rec or {}).get("error", "缺")[:200])
    rows = ["| 类别 | bound/分母 | 机检判 | 未命中 |", "|---|---|---|---|"]
    for cls in CLASSES:
        box = rec["classes"][cls]
        miss = []
        for inst in box["instances"]:
            if not inst["bound"]:
                miss.append("%s:%s" % (inst.get("entity_id") or inst.get("entity_ids"),
                                       ",".join(inst.get("missing") or [])))
        verdict = ("n/a" if not box["applicable"] else
                   ("pass" if box["bound"] == box["applicable"] else "fail"))
        rows.append("| %s | %d/%d | %s | %s |" % (
            cls, box["bound"], box["applicable"], verdict, "；".join(miss)[:300]))
    return "\n".join(rows)


def cmd_sheet(args) -> int:
    images = _load_images()
    binding = _load_binding()
    by_key = {(r["model"], r["image_id"]): r for r in binding["records"]}
    ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    dest_dir = OUT / "inspect"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for img_id in ids:
        meta = images[img_id]
        parts = ["# 代理目检 · %s（%s）" % (img_id, meta["category"]),
                 "",
                 "- 图片：`%s`（%dx%d）" % (meta["path"], meta["w"], meta["h"]),
                 "- 判读者：**AI（代理目检，不是人类）**；Lead 会独立复核。",
                 "- 待办：看原图 → 给每个模型填 facts 判定（correct/wrong/unverifiable）、"
                 "hallucination（有/无）、binding 五类 pass/fail。",
                 ""]
        for model in models:
            rec = by_key.get((model, img_id))
            payload_path = OUT / "json" / model / ("%s.json" % img_id)
            parts.append("## %s" % model)
            parts.append("")
            parts.append("### 机检")
            parts.append(_machine_cells(rec))
            parts.append("")
            if payload_path.is_file():
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
                if payload.get("ok"):
                    parts.append("### caption（成品散文）")
                    parts.append("")
                    parts.append(payload.get("caption") or "")
                    parts.append("")
                    parts.append("### IR 实体")
                    parts.append("")
                    parts.append(_entity_table(payload["ir"]))
                    parts.append("")
                    rep = payload.get("report") or {}
                    parts.append("### 取证报告")
                    parts.append("```json")
                    parts.append(json.dumps({k: rep.get(k) for k in
                                             ("entity_count", "warnings", "dropped",
                                              "age_gate")}, ensure_ascii=False,
                                            indent=1)[:2500])
                    parts.append("```")
                else:
                    parts.append("### 记录失败\n\n`%s`" % payload.get("error", "")[:500])
            parts.append("")
        dest = dest_dir / ("%s.md" % img_id)
        dest.write_text("\n".join(parts), encoding="utf-8")
        print("已写 %s" % dest)
    return 0


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def cmd_stats(args) -> int:
    agent_path = OUT / "inspect" / "agent.json"
    if not agent_path.is_file():
        print("缺 %s（逐张目检后填）" % agent_path, file=sys.stderr)
        return 2
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    binding = _load_binding()
    by_key = {(r["model"], r["image_id"]): r for r in binding["records"]}

    models = sorted({m for img in agent["images"].values() for m in img})
    verification = {"作者": "AI 代理目检（不是人类）", "images": sorted(agent["images"]),
                    "models": models}

    # ── 1) 校准：机检 pass/fail vs 代理目检 pass/fail ──
    total = agree = 0
    disagreements = []
    cells_out = []
    for img_id, per_model in agent["images"].items():
        for model, box in per_model.items():
            rec = by_key.get((model, img_id)) or {}
            for cls in CLASSES:
                agent_verdict = (box.get("binding") or {}).get(cls)
                if agent_verdict not in ("pass", "fail"):
                    continue
                mbox = (rec.get("classes") or {}).get(cls) or {}
                if not mbox.get("applicable"):
                    continue
                machine_pass = mbox["bound"] == mbox["applicable"]
                cells_out.append({"model": model, "image_id": img_id, "class": cls,
                                  "machine_pass": machine_pass,
                                  "agent_pass": agent_verdict == "pass"})
                total += 1
                if machine_pass == (agent_verdict == "pass"):
                    agree += 1
                else:
                    disagreements.append({
                        "model": model, "image_id": img_id, "class": cls,
                        "machine": "%d/%d" % (mbox["bound"], mbox["applicable"]),
                        "agent": agent_verdict,
                        "note": (box.get("notes") or "")[:200]})

    # ── 2) 事实准确率 / 幻觉 ──
    fact_stats = {}
    for model in models:
        corr = wrong = unver = 0
        hallu_images = judged_images = 0
        for per_model in agent["images"].values():
            box = per_model.get(model)
            if not box:
                continue
            judged_images += 1
            for f in box.get("facts") or []:
                v = f.get("verdict")
                if v == "correct":
                    corr += 1
                elif v == "wrong":
                    wrong += 1
                elif v == "unverifiable":
                    unver += 1
            if box.get("hallucination") is True:
                hallu_images += 1
        fact_stats[model] = {
            "facts_total": corr + wrong + unver,
            "correct": corr, "wrong": wrong, "unverifiable": unver,
            "accuracy": _rate(corr, corr + wrong),
            "hallucination_images": hallu_images,
            "judged_images": judged_images,
            "hallucination_rate": _rate(hallu_images, judged_images),
        }

    payload = {
        "generated_by": "tools/g7_inspect.py stats",
        "verification": verification,
        "calibration": {
            "cells_compared": total, "agreement": _rate(agree, total),
            "threshold": args.threshold,
            "verdict": "pass" if total and agree / total >= args.threshold else "fail",
            "disagreements": disagreements,
        },
        "facts": fact_stats,
        "cells": cells_out,
    }
    (OUT / "inspect" / "stats.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "calib").mkdir(parents=True, exist_ok=True)
    (OUT / "calib" / "agent_cells.json").write_text(json.dumps(
        {"cells": [{"model": c["model"], "image_id": c["image_id"],
                    "class": c["class"], "agent_pass": c["agent_pass"],
                    "note": ""} for c in cells_out]},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print("校对（机检 vs 代理目检）：%d/%d 格一致 = %s（阈值 %.0f%%）"
          % (agree, total, "n/a" if not total else "%.1f%%" % (100.0 * agree / total),
             args.threshold * 100))
    for d in disagreements[:20]:
        print("  ✗ %s %s %-8s 机检=%s 目检=%s %s" % (
            d["model"], d["image_id"], d["class"], d["machine"], d["agent"], d["note"]))
    print()
    for model, s in fact_stats.items():
        print("%s：事实 %d 条（对 %d / 错 %d / 无法判 %d），准确率 %s；"
              "幻觉 %d/%d 图" % (
                  model, s["facts_total"], s["correct"], s["wrong"],
                  s["unverifiable"],
                  "n/a" if s["accuracy"] is None else "%.1f%%" % (100 * s["accuracy"]),
                  s["hallucination_images"], s["judged_images"]))
    return 0 if payload["calibration"]["verdict"] == "pass" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G7 代理目检与校准统计")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet")
    s.add_argument("--ids", required=True)
    s.add_argument("--models", default="A-4B,B-9B")
    s.set_defaults(func=cmd_sheet)
    t = sub.add_parser("stats")
    t.add_argument("--threshold", type=float, default=0.80)
    t.set_defaults(func=cmd_stats)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
