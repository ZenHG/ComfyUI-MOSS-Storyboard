# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""E1 · 扩写探针：11 条输入走**节点自己的 expand 路径**，量化保留/乱加/长度。

唯一允许的调用方式（任务卡要求）：
    node = MossPromptSmith()
    prompt, report = node.smith(task="expand", channel="local", ...)

不自己拼 system prompt，不换 smith 层 adapter。节点内部的 llama-server 侧车是产品路径；
本工具只负责准备输入、调用、统计。

用法：
    python tools/expand_probe.py --dry-run        # 只校验输入集
    python tools/expand_probe.py                  # 真跑（需 GPU/llama-server 侧车）
产物：out/expand_probe/results.json、out/expand_probe/summary.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "expand_probe" / "inputs.json"
OUT = ROOT / "out" / "expand_probe"


def _comfy_root() -> Path:
    """宿主 ComfyUI 根：环境变量 → 仓库 `.comfyui_path` → 仓库上两级（custom_nodes/<pkg>）。"""
    env = os.environ.get("COMFYUI_PATH")
    if env:
        return Path(env)
    marker = ROOT / ".comfyui_path"
    if marker.is_file():
        v = marker.read_text(encoding="utf-8").strip()
        if v:
            return Path(v)
    return ROOT.parent.parent


def _find_server_exe() -> Path:
    """llama-server：MOSS_LLM_SERVER / LLAMA_SERVER → PATH；都没有就交给节点自己找。"""
    import shutil
    env = os.environ.get("MOSS_LLM_SERVER") or os.environ.get("LLAMA_SERVER")
    if env:
        return Path(env)
    which = shutil.which("llama-server")
    return Path(which) if which else Path("")


#: 宿主 models 目录的父级；节点用 `folder_paths.models_dir` 扫 GGUF（产品路径）。
COMFY_ROOT = _comfy_root()
SERVER_EXE = _find_server_exe()
MODEL_HINT = "Qwen3.5-4B-QUASAR-Q4_0.gguf"

QUOTE_RE = re.compile(r'"([^"\n]{1,300})"|“([^”\n]{1,300})”|「([^」\n]{1,300})」')
NUM_RE = re.compile(r"(?<![\d.])\d{1,4}(?:\.\d+)?(?![\d.])")
COLORS = {
    "red", "orange", "yellow", "green", "cyan", "blue", "purple", "violet",
    "magenta", "pink", "brown", "black", "white", "gray", "grey", "beige",
    "cream", "ivory", "gold", "golden", "silver", "bronze", "copper", "navy",
    "teal", "olive", "maroon", "turquoise", "amber", "crimson", "scarlet",
    "indigo", "lavender", "emerald", "ruby", "sapphire",
}
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
#: 仅用于「跨语言等价折算」的说明列；原始 extras 仍按字面口径统计。
ZH_COLOR = {
    "红色": "red", "红": "red", "黑色": "black", "黑": "black",
    "灰色": "grey", "灰": "grey", "深灰": "grey", "浅灰": "grey",
    "蓝色": "blue", "蓝": "blue", "白色": "white", "白": "white",
    "黄色": "yellow", "黄": "yellow", "暖黄": "yellow",
    "绿色": "green", "绿": "green", "棕色": "brown", "棕": "brown",
    "金色": "gold", "金": "gold", "橙色": "orange", "橙": "orange",
    "粉色": "pink", "粉": "pink", "紫色": "purple", "紫": "purple",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"\s+", "", s)


def is_zh(text: str) -> bool:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    en = len(re.findall(r"[A-Za-z][A-Za-z'’\-]*", text or ""))
    return cjk > 10 and cjk >= en * 2


def length_of(text: str, zh: bool) -> int:
    if zh:
        return len(re.sub(r"\s+", "", text or ""))
    return len(re.findall(r"[A-Za-z][A-Za-z'’\-]*", text or ""))


def quotes(text: str) -> list:
    out = []
    for m in QUOTE_RE.finditer(text or ""):
        q = next(g for g in m.groups() if g)
        if q.strip() and q not in out:
            out.append(q)
    return out


def validate(data: dict) -> list:
    errs = []
    inputs = data.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return ["inputs 必须是非空数组"]
    seen = set()
    for i, item in enumerate(inputs, 1):
        for k in ("id", "category", "text", "dialect", "length", "must_keep", "facts"):
            if k not in item:
                errs.append("第%d条缺字段 %s" % (i, k))
        if item.get("id") in seen:
            errs.append("重复 id: %r" % item.get("id"))
        seen.add(item.get("id"))
        if not str(item.get("text") or "").strip():
            errs.append("%s text 为空" % item.get("id"))
        if not isinstance(item.get("facts"), list):
            errs.append("%s facts 必须是数组" % item.get("id"))
        elif any(not str(f).strip() for f in item["facts"]):
            errs.append("%s facts 含空项" % item.get("id"))
        if item.get("dialect") not in ("moss_image", "moss_storyboard",
                                       "generic_natural", "generic_tags",
                                       "cinematic", "cn_natural"):
            errs.append("%s dialect 未知：%r" % (item.get("id"), item.get("dialect")))
        if item.get("length") not in ("brief", "standard", "detailed"):
            errs.append("%s length 未知：%r" % (item.get("id"), item.get("length")))
    return errs


def ratio(inp: int, out: int):
    if not inp:
        return None
    return round((out - inp) * 100.0 / inp, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E1 扩写探针（节点 expand 路径）")
    ap.add_argument("--dry-run", action="store_true", help="只校验输入集，不启动模型")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试）")
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--json-out", default=str(OUT / "results.json"))
    args = ap.parse_args(argv)

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    errs = validate(data)
    if args.dry_run:
        print("inputs:", len(data.get("inputs") or []), "| errors:", errs)
        for it in data.get("inputs") or []:
            print("-", it.get("id"), it.get("category"), "| facts:", len(it.get("facts") or []),
                  "| dialect:", it.get("dialect"), "| length:", it.get("length"))
        return 0 if not errs else 2
    if errs:
        print("输入集校验失败：", errs, file=sys.stderr)
        return 2

    # 节点是产品路径：让它自己用 folder_paths 扫 GGUF、自己起侧车。
    sys.path.insert(0, str(COMFY_ROOT))          # 让 folder_paths 可 import
    sys.path.insert(0, str(ROOT))
    from moss_llm.nodes import MossPromptSmith, _scan_gguf
    from moss_llm.server import GgufLocalServer

    models, _mmprojs = _scan_gguf()
    key = next((k for k in models if Path(k).name == MODEL_HINT), None)
    if key is None:
        print("找不到模型 %r；_scan_gguf 可见：%s" % (MODEL_HINT, list(models)[:10]), file=sys.stderr)
        return 2
    print("model key:", key, "| path:", models[key])
    print("server exe:", SERVER_EXE, "| exists:", Path(SERVER_EXE).is_file())

    node = MossPromptSmith()
    inputs = data["inputs"]
    if args.limit:
        inputs = inputs[:args.limit]
    records = []
    t_all = time.time()
    try:
        for item in inputs:
            t0 = time.time()
            rec = {
                "id": item["id"], "category": item["category"],
                "text": item["text"], "must_keep": item.get("must_keep", ""),
                "dialect": item["dialect"], "length": item["length"],
                "facts": list(item["facts"]),
                "finish_reason": "未暴露（node.smith 只返回 prompt/report，不返回 finish_reason）",
            }
            try:
                prompt, report = node.smith(
                    task="expand", channel="local", dialect=item["dialect"],
                    model=key, text=item["text"], length=item["length"],
                    temperature=0.7, max_tokens=1024, seed=42,
                    must_keep=item.get("must_keep", ""),
                    release_after_run=False, port=args.port,
                    server_exe=SERVER_EXE if Path(SERVER_EXE).is_file() else "")
                rec["prompt"] = prompt
                rec["report"] = report
                rec["error"] = ""
            except Exception as exc:
                rec["prompt"] = ""
                rec["report"] = ""
                rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            rec["elapsed_s"] = round(time.time() - t0, 2)
            zh = is_zh(item["text"])
            rec["lang"] = "zh" if zh else "en"
            rec["input_length"] = length_of(item["text"], zh)
            rec["output_length"] = length_of(rec["prompt"], zh)
            rec["length_delta_pct"] = ratio(rec["input_length"], rec["output_length"])
            out_n = norm(rec["prompt"])
            hits = [f for f in item["facts"] if norm(f) in out_n]
            rec["fact_hits"] = hits
            rec["fact_missed"] = [f for f in item["facts"] if f not in hits]
            ref = norm(item["text"] + "\n" + item.get("must_keep", ""))
            extras = []
            for q in quotes(rec["prompt"]):
                if norm(q) not in ref:
                    extras.append({"type": "quote", "value": q})
            for n in sorted(set(NUM_RE.findall(rec["prompt"])), key=lambda x: (len(x), x)):
                if n not in ref:
                    extras.append({"type": "number", "value": n})
            low_out = unicodedata.normalize("NFKC", rec["prompt"]).casefold()
            for c in sorted(COLORS):
                if re.search(r"\b%s\b" % re.escape(c), low_out) and c not in ref:
                    extras.append({"type": "color", "value": c})
            rec["extras"] = extras
            rec["report_must_keep"] = [
                ln for ln in (rec["report"] or "").splitlines()
                if ("保留" in ln or "必保" in ln or "must_keep" in ln.lower()
                    or any(norm(tok) in norm(ln) for tok in re.split(r"[；;,\s]+", item.get("must_keep", "")) if tok.strip()))
            ]
            records.append(rec)
            print("%-3s %-22s facts %d/%d | extras %d | %d→%d | %.1fs%s"
                  % (rec["id"], rec["category"][:22], len(hits), len(item["facts"]),
                     len(extras), rec["input_length"], rec["output_length"],
                     rec["elapsed_s"], ("  ERROR " + rec["error"][:60]) if rec["error"] else ""))
    finally:
        GgufLocalServer.stop()

    total = round(time.time() - t_all, 2)

    # 后处理：语言错配、坏输出（注入用例）、跨语言颜色折算；原始口径全部保留。
    for rec in records:
        out = rec["prompt"]
        out_has_cjk = bool(CJK_RE.search(out))
        rec["out_has_cjk"] = out_has_cjk
        rec["output_words"] = len(re.findall(r"[A-Za-z][A-Za-z'’\-]*", out))
        rec["fact_lang_mismatch"] = [
            f for f in rec["facts"] if CJK_RE.search(f) and not out_has_cjk]
        rec["facts_measurable"] = [
            f for f in rec["facts"] if f not in rec["fact_lang_mismatch"]]
        rec["fact_measurable_hits"] = [
            f for f in rec["facts_measurable"] if norm(f) in norm(out)]
        rec["bad_output"] = False
        rec["bad_output_reason"] = ""
        s = (out or "").strip()
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except ValueError:
                obj = None
            if isinstance(obj, dict) and "rewritten_prompt" in obj:
                if not str(obj.get("rewritten_prompt") or "").strip():
                    rec["bad_output"] = True
                    rec["bad_output_reason"] = (
                        "JSON 空壳：rewritten_prompt 为空（注入用例未产出提示词）")
        ref_text = rec["text"] + "\n" + rec["must_keep"]
        semantic_colors = {en for zh, en in ZH_COLOR.items() if zh in ref_text}
        adjusted = []
        for e in rec["extras"]:
            ee = dict(e)
            if e["type"] == "color" and e["value"] in semantic_colors:
                ee["note"] = "输入中文颜色对应，折算不计（原始口径仍计入）"
            elif e["type"] == "quote" and rec["bad_output"]:
                ee["note"] = "坏输出 JSON 键名，不是画面文字"
            else:
                adjusted.append(ee)
        rec["extras_adjusted"] = adjusted

    payload = {
        "schema": "expand-probe-results/1",
        "tool": "tools/expand_probe.py",
        "node_path": "MossPromptSmith.smith(task=expand, channel=local)",
        "model_key": key, "model_path": models[key],
        "server_exe": SERVER_EXE,
        "params": {"temperature": 0.7, "max_tokens": 1024, "seed": 42,
                   "release_after_run": False, "port": args.port},
        "total_elapsed_s": total,
        "records": records,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # summary.md
    lines = ["# 扩写探针（E1）", "",
             "- 路径：`MossPromptSmith.smith(task=\"expand\", channel=\"local\")`（节点自有路径）",
             "- 模型：`%s`｜seed 42｜temp 0.7｜max_tokens 1024｜单次采样" % Path(models[key]).name,
             "- 总耗时：%.1fs｜输入 %d 条" % (total, len(records)), "",
             "| id | 类别 | facts 命中（可判） | 乱加（原始 / 折算后） | 长度（输入→输出） | 耗时 | 状态 |",
             "|---|---|---|---|---|---|---|"]
    ok = bad = bad_outputs = 0
    all_hit = all_facts = 0
    all_hit_m = all_meas = 0
    all_extras = all_extras_adj = 0
    for r in records:
        all_hit += len(r["fact_hits"])
        all_facts += len(r["facts"])
        all_hit_m += len(r["fact_measurable_hits"])
        all_meas += len(r["facts_measurable"])
        all_extras += len(r["extras"])
        all_extras_adj += len(r["extras_adjusted"])
        extra_txt = "、".join("%s:%s" % (e["type"], e["value"]) for e in r["extras"]) or "—"
        adj_txt = "、".join("%s:%s" % (e["type"], e["value"])
                           for e in r["extras_adjusted"]) or "—"
        if r["error"]:
            status = "ERROR: " + r["error"][:80]
        elif r["bad_output"]:
            status = "BAD_OUTPUT: " + r["bad_output_reason"]
        else:
            status = "ok"
        is_bad = bool(r["error"] or r["bad_output"])
        ok += 0 if is_bad else 1
        bad += 1 if r["error"] else 0
        bad_outputs += 1 if r["bad_output"] else 0
        docs = ("（缺：%s）" % "、".join(r["fact_missed"])) if r["fact_missed"] else ""
        lines.append("| %s | %s | %d/%d；可判 %d/%d%s | %s / %s | %d→%d（%+.1f%%；英文词 %d） | %.1fs | %s |"
                     % (r["id"], r["category"], len(r["fact_hits"]), len(r["facts"]),
                        len(r["fact_measurable_hits"]), len(r["facts_measurable"]), docs,
                        extra_txt, adj_txt, r["input_length"], r["output_length"],
                        r["length_delta_pct"] if r["length_delta_pct"] is not None else 0,
                        r["output_words"], r["elapsed_s"], status))
    lines += ["", "## 汇总",
              "- 成功/失败（含坏输出）：%d/%d；其中异常 %d、坏输出 %d" % (ok, bad + bad_outputs, bad, bad_outputs),
              "- facts 字面命中：%d/%d（%.1f%%，含中文事实对英文输出的语言错配）"
              % (all_hit, all_facts, (100.0 * all_hit / all_facts) if all_facts else 0.0),
              "- facts 可判（排除中文事实对纯英文输出）：%d/%d（%.1f%%）"
              % (all_hit_m, all_meas, (100.0 * all_hit_m / all_meas) if all_meas else 0.0),
              "- 乱加硬事实：原始 %d / 颜色按中文等价折算后 %d" % (all_extras, all_extras_adj),
              "- 总耗时：%.1fs" % total, "",
              "## 说明",
              "- 「语言错配」= 事实是中文、输出无任何中文字符；字面归一化必然 miss，不等于语义丢失。",
              "- 「折算后」只把输入里中文本色/颜色词对应的英文颜色从 extras 中剔除；原始 extras 仍按字面口径。",
              "- 坏输出=成功返回但内容不是提示词（如注入用例返回 JSON 空壳）。",
              "- `finish_reason` 未由 `node.smith()` 暴露，无法记录截断信号。", "",
              "## 逐条 report 中 must_keep 相关行", ""]
    for r in records:
        lines.append("### %s" % r["id"])
        lines.append("```")
        lines.append("\n".join(r["report_must_keep"]) or "（report 中没有 must_keep/保留 相关行）")
        lines.append("```")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", out, "and", OUT / "summary.md", "| total %.1fs" % total,
          "| ok/err/bad_output %d/%d/%d" % (ok, bad, bad_outputs))
    return 0 if (bad == 0 and bad_outputs == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
