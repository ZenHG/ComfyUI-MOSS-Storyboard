# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""P1 · 多用户 persona 实测：11 种真实用法走 MossPromptSmith 节点自身路径。

目标不是评模型文笔，而是找「用户能不能用完 / 在哪里卡住」：
- 文本 expand 用本地 4B；caption/edit 用 Qwen3-VL-4B + mmproj；
- 图片取 out/g7/images60.json 第一条真实图，构造 torch IMAGE [1,H,W,3]；
- 全程 release_after_run=False，最后 GgufLocalServer.stop()；
- 不改节点代码，不写 docs，不 commit。

用法：
    python tools/persona_probe.py --dry-run
    python tools/persona_probe.py
产物：out/persona_probe/results.json、summary.md
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


COMFY_ROOT = _comfy_root()
OUT = ROOT / "out" / "persona_probe"
IMAGE60 = ROOT / "out" / "g7" / "images60.json"
PROBE = ROOT / "tests" / "fixtures" / "expand_probe" / "inputs.json"
IR_FIXTURE = ROOT / "tests" / "fixtures" / "smith" / "hero-storyboard.ir.json"
SERVER_EXE = _find_server_exe()
TEXT_MODEL = "Qwen3.5-4B-QUASAR-Q4_0.gguf"
VL_MODEL = "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
VL_MMPROJ = "mmproj-Qwen3VL-4B-Instruct-F16.gguf"
PORT = 8099


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"\s+", "", s)


def cjk_count(s: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", s or ""))


def en_words(s: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'’\-]*", s or ""))


def load_image_tensor(path: Path):
    from PIL import Image
    import numpy as np
    import torch
    arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)[None]


def plan() -> list:
    s10 = ""
    if PROBE.is_file():
        d = json.loads(PROBE.read_text(encoding="utf-8"))
        s10 = next((x["text"] for x in d["inputs"] if x["id"] == "S10"), "")
    return [
        {"id": "P1", "persona": "新手·中文一句话", "task": "expand",
         "text": "雨夜便利店，一个加班的女生买关东煮", "dialect": "moss_image",
         "length": "brief", "model": TEXT_MODEL, "mmproj": "none"},
        {"id": "P2", "persona": "进阶·英文散文", "task": "expand",
         "text": s10 or "At dusk, a woman in a red scarf stands in a narrow alley, looking at a small sign that reads \"OPEN\".",
         "dialect": "moss_image", "length": "standard", "model": TEXT_MODEL, "mmproj": "none"},
        {"id": "P3", "persona": "洗图用户·中文图", "task": "caption",
         "text": "", "dialect": "moss_image", "length": "standard",
         "model": VL_MODEL, "mmproj": VL_MMPROJ, "language": "auto"},
        {"id": "P4", "persona": "洗图用户·英文图", "task": "caption",
         "text": "", "dialect": "moss_image", "length": "standard",
         "model": VL_MODEL, "mmproj": VL_MMPROJ, "language": "en"},
        {"id": "P5", "persona": "编辑用户", "task": "edit",
         "text": "把背景换成雪夜，人物不变", "dialect": "moss_image",
         "length": "standard", "model": VL_MODEL, "mmproj": VL_MMPROJ},
        {"id": "P6", "persona": "开发者·IR", "task": "compile",
         "text": "", "dialect": "moss_image", "length": "standard",
         "model": TEXT_MODEL, "mmproj": "none", "target": "moss-image"},
        {"id": "P7", "persona": "宫格用户", "task": "expand",
         "text": "四个连续镜头：女孩走进雨夜便利店，买关东煮，看窗外，坐下吃。",
         "dialect": "moss_storyboard", "length": "standard",
         "model": TEXT_MODEL, "mmproj": "none", "panels": 4},
        {"id": "P8", "persona": "中文模型用户", "task": "expand",
         "text": "雨夜便利店门口，一个女生端着关东煮，抬头看霓虹灯。",
         "dialect": "cn_natural", "length": "standard",
         "model": TEXT_MODEL, "mmproj": "none"},
        {"id": "P9", "persona": "固定项用户", "task": "expand",
         "text": "深夜便利店门口，一个女生端着关东煮准备离开。",
         "dialect": "moss_image", "length": "brief",
         "model": TEXT_MODEL, "mmproj": "none", "must_keep": "路上小心；关东煮"},
        {"id": "P10", "persona": "误用 A（expand 空文本）", "task": "expand",
         "text": "", "dialect": "moss_image", "length": "brief",
         "model": TEXT_MODEL, "mmproj": "none", "expect_error": "空文本"},
        {"id": "P11", "persona": "误用 B（caption 无图 + compile 坏 JSON）",
         "task": "caption", "text": "", "dialect": "moss_image", "length": "standard",
         "model": VL_MODEL, "mmproj": VL_MMPROJ, "expect_error": "无图|坏JSON"},
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1 多用户 persona 实测")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    specs = plan()
    if args.dry_run:
        print("personas:", len(specs))
        for s in specs:
            print("-", s["id"], s["persona"], "| task:", s["task"],
                  "| dialect:", s["dialect"], "| model:", s["model"],
                  "| mmproj:", s["mmproj"])
        img = json.loads(IMAGE60.read_text(encoding="utf-8"))["images"][0]
        print("image:", img["id"], img["path"], "| exists:", Path(img["path"]).is_file())
        print("S10 len:", len(next((x["text"] for x in json.loads(PROBE.read_text(encoding="utf-8"))["inputs"] if x["id"] == "S10"), "")))
        return 0

    sys.path.insert(0, str(COMFY_ROOT))
    sys.path.insert(0, str(ROOT))
    from moss_llm.nodes import MossPromptSmith, _scan_gguf, _MMPROJ_NONE
    from moss_llm.server import GgufLocalServer

    models, mmprojs = _scan_gguf()
    text_key = next((k for k in models if Path(k).name == TEXT_MODEL), None)
    vl_key = next((k for k in models if Path(k).name == VL_MODEL), None)
    mm_key = next((k for k in mmprojs if Path(k).name == VL_MMPROJ), None)
    if not text_key or not vl_key or not mm_key:
        print("模型缺失：text=%r vl=%r mmproj=%r" % (text_key, vl_key, mm_key), file=sys.stderr)
        return 2
    img = json.loads(IMAGE60.read_text(encoding="utf-8"))["images"][0]
    image_t = load_image_tensor(Path(img["path"]))

    node = MossPromptSmith()
    results = {}
    t_all = time.time()
    try:
        # 文本 expand 组（同一文本模型复用侧车）
        for s in [x for x in specs if x["id"] in ("P1", "P2", "P7", "P8", "P9")]:
            t0 = time.time()
            rec = {"id": s["id"], "persona": s["persona"], "task": s["task"],
                   "request": {"model": text_key, "mmproj": "none", "dialect": s["dialect"],
                               "length": s["length"], "panels": s.get("panels"),
                               "must_keep": s.get("must_keep", ""),
                               "text": s["text"]},
                   "image": None, "prompt": "", "report": "", "error": "", "elapsed_s": 0.0}
            try:
                prompt, report = node.smith(
                    task=s["task"], channel="local", dialect=s["dialect"],
                    model=text_key, text=s["text"], length=s["length"],
                    temperature=0.7, max_tokens=1024, seed=42,
                    must_keep=s.get("must_keep", ""), release_after_run=False,
                    port=PORT, mmproj=_MMPROJ_NONE,
                    panels=s.get("panels", 4),
                    server_exe=SERVER_EXE if Path(SERVER_EXE).is_file() else "")
                rec["prompt"], rec["report"] = prompt, report
            except Exception as exc:
                rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            rec["elapsed_s"] = round(time.time() - t0, 2)
            results[s["id"]] = rec
            print(s["id"], rec["elapsed_s"], "s | err:", rec["error"][:80],
                  "| prompt:", rec["prompt"][:80].replace("\n", " "))
        GgufLocalServer.stop()

        # 视觉组（VL + mmproj）
        for s in [x for x in specs if x["id"] in ("P3", "P4", "P5")]:
            t0 = time.time()
            rec = {"id": s["id"], "persona": s["persona"], "task": s["task"],
                   "request": {"model": vl_key, "mmproj": mm_key, "dialect": s["dialect"],
                               "length": s["length"], "language": s.get("language", "auto"),
                               "text": s["text"]},
                   "image": img["id"], "prompt": "", "report": "", "error": "", "elapsed_s": 0.0}
            try:
                prompt, report = node.smith(
                    task=s["task"], channel="local", dialect=s["dialect"],
                    model=vl_key, text=s["text"], length=s["length"],
                    temperature=0.2, max_tokens=2048, seed=42,
                    language=s.get("language", "auto"),
                    image=image_t, mmproj=mm_key, release_after_run=False,
                    port=PORT,
                    server_exe=SERVER_EXE if Path(SERVER_EXE).is_file() else "")
                rec["prompt"], rec["report"] = prompt, report
            except Exception as exc:
                rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            rec["elapsed_s"] = round(time.time() - t0, 2)
            results[s["id"]] = rec
            print(s["id"], rec["elapsed_s"], "s | err:", rec["error"][:80],
                  "| prompt:", rec["prompt"][:80].replace("\n", " "))
        GgufLocalServer.stop()

        # P6 compile（零模型）
        s = next(x for x in specs if x["id"] == "P6")
        t0 = time.time()
        rec = {"id": "P6", "persona": s["persona"], "task": "compile",
               "request": {"target": "moss-image", "text": IR_FIXTURE.read_text(encoding="utf-8")[:200]},
               "image": None, "prompt": "", "report": "", "error": "", "elapsed_s": 0.0,
               "elapsed_ms": None}
        try:
            prompt, report = node.smith(
                task="compile", channel="local", dialect="moss_image",
                model=text_key, text=IR_FIXTURE.read_text(encoding="utf-8"),
                length="standard", temperature=0.0, max_tokens=1024, seed=42,
                target="moss-image", release_after_run=False, port=PORT,
                mmproj=_MMPROJ_NONE)
            rec["prompt"], rec["report"] = prompt, report
        except Exception as exc:
            rec["error"] = "%s: %s" % (type(exc).__name__, exc)
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["elapsed_ms"] = round(rec["elapsed_s"] * 1000, 1)
        results["P6"] = rec
        print("P6", rec["elapsed_ms"], "ms | err:", rec["error"][:80])

        # P10 / P11 误用（不应启动模型/不应崩栈）
        def expect(pid, persona, fn):
            rec = {"id": pid, "persona": persona, "task": "", "request": {},
                   "image": None, "prompt": "", "report": "", "error": "",
                   "elapsed_s": 0.0}
            t0 = time.time()
            try:
                fn()
                rec["error"] = "NO_ERROR（本应报错却成功返回）"
            except Exception as exc:
                rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            rec["elapsed_s"] = round(time.time() - t0, 2)
            return rec

        results["P10"] = expect(
            "P10", "误用 A（expand 空文本）",
            lambda: node.smith(task="expand", channel="local", dialect="moss_image",
                               model=text_key, text="", length="brief", temperature=0.0,
                               max_tokens=64, seed=42, release_after_run=False,
                               port=PORT, mmproj=_MMPROJ_NONE))
        results["P11"] = expect(
            "P11", "误用 B（caption 无图 + compile 坏 JSON）",
            lambda: node.smith(task="caption", channel="local", dialect="moss_image",
                               model=vl_key, text="", length="standard", temperature=0.0,
                               max_tokens=64, seed=42, image=None, mmproj=mm_key,
                               release_after_run=False, port=PORT))
        # P11b：坏 JSON compile（合并进 P11 的 error 文本）
        bad = expect(
            "P11b", "误用 B2（compile 坏 JSON）",
            lambda: node.smith(task="compile", channel="local", dialect="moss_image",
                               model=text_key, text="{bad json", length="standard",
                               temperature=0.0, max_tokens=64, seed=42,
                               target="moss-image", release_after_run=False,
                               port=PORT, mmproj=_MMPROJ_NONE))
        results["P11"]["error"] += " | compile坏JSON: " + bad["error"]
    finally:
        GgufLocalServer.stop()

    total = round(time.time() - t_all, 2)

    # 判定与逐条结论
    def judge(r):
        pid = r["id"]
        p = r["prompt"] or ""
        err = r["error"] or ""
        checks = {}
        if pid in ("P10", "P11"):
            action = bool(err) and not err.startswith("NO_ERROR") and (
                "必须" in err or "请" in err or "JSON" in err or "IR" in err or "图片" in err)
            r["verdict"] = "PASS" if action else "FAIL"
            r["conclusion"] = ("报错可操作：" + err[:120]) if action else ("报错不可用/未报错：" + err[:120])
            return {"actionable": action}
        if err:
            r["verdict"] = "FAIL"
            r["conclusion"] = "调用失败：" + err[:140]
            return {"error": True}
        if not p.strip():
            r["verdict"] = "FAIL"
            r["conclusion"] = "返回空提示词"
            return {"empty": True}
        if p.lstrip().startswith("{") and "rewritten_prompt" in p:
            r["verdict"] = "FAIL"
            r["conclusion"] = "返回 JSON 空壳，不是可用提示词"
            return {"json": True}
        if pid == "P1":
            w = en_words(p)
            checks["output_words"] = w
            r["verdict"] = "UX-ISSUE" if w > 300 else "PASS"
            r["conclusion"] = "返回可用英文提示词，但一句话被扩到 %d 词" % w if w > 300 else "一句话扩写可用"
        elif pid == "P2":
            keys = ["red scarf", "OPEN", "85mm"]
            miss = [k for k in keys if norm(k) not in norm(p)]
            checks["missing_keys"] = miss
            r["verdict"] = "UX-ISSUE" if miss else "PASS"
            r["conclusion"] = ("改写丢掉输入硬事实：%s" % miss) if miss else "输入硬事实保留"
        elif pid == "P3":
            checks["cjk"] = cjk_count(p)
            checks["en_words"] = en_words(p)
            r["verdict"] = "PASS"
            r["conclusion"] = "caption 返回（CJK %d / 英文 %d 词）" % (checks["cjk"], checks["en_words"])
        elif pid == "P4":
            cjk, w = cjk_count(p), en_words(p)
            checks.update({"cjk": cjk, "en_words": w})
            r["verdict"] = "PASS" if (cjk < 10 and w > 20) else "UX-ISSUE"
            r["conclusion"] = ("language=en 生效（英文 %d 词）" % w if cjk < 10 else
                               "language=en 但输出仍含中文（CJK %d）" % cjk)
        elif pid == "P5":
            hit = ("snow" in p.lower()) or ("雪" in p)
            checks["edit_subject_in_output"] = hit
            r["verdict"] = "PASS" if hit else "UX-ISSUE"
            r["conclusion"] = "编辑指令在输出中体现" if hit else "编辑指令未在输出中体现（背景雪夜）"
        elif pid == "P6":
            ms = r.get("elapsed_ms") or 0
            zero = "编译" in (r["report"] or "") or "模型" in (r["report"] or "")
            checks.update({"elapsed_ms": ms, "report_mentions_compile": zero})
            r["verdict"] = "PASS" if ms < 1000 else "UX-ISSUE"
            r["conclusion"] = "compile 零模型 %d ms" % ms if ms < 1000 else "compile 耗时 %d ms，超过毫秒级预期" % ms
        elif pid == "P7":
            n = len(re.findall(r"\[\s*PANEL\s*\d+\s*\]|【\s*PANEL\s*\d+\s*】", p, re.I))
            checks["panel_count"] = n
            r["verdict"] = "PASS" if n == 4 else "UX-ISSUE"
            r["conclusion"] = ("4 段宫格齐全" if n == 4 else "panels=4 但输出只有 %d 段" % n)
        elif pid == "P8":
            cjk, w = cjk_count(p), en_words(p)
            checks.update({"cjk": cjk, "en_words": w})
            r["verdict"] = "PASS" if cjk >= 20 else "UX-ISSUE"
            r["conclusion"] = "cn_natural 输出中文" if cjk >= 20 else "cn_natural 输出不是中文（CJK %d）" % cjk
        elif pid == "P9":
            tokens = ["路上小心", "关东煮"]
            miss = [t for t in tokens if norm(t) not in norm(p)]
            checks["missing"] = miss
            r["verdict"] = "PASS" if not miss else "UX-ISSUE"
            r["conclusion"] = "固定项齐" if not miss else "固定项缺失/未逐字出现：%s" % miss
        else:
            r["verdict"] = "PASS"
            r["conclusion"] = "返回可用结果"
        return checks

    for pid in sorted(results):
        r = results[pid]
        r["checks"] = judge(r)
        r["report_lines"] = [ln for ln in (r["report"] or "").splitlines()
                             if ln.strip()][:12]
        r["prompt_summary"] = (r["prompt"] or "").strip().replace("\n", " ")[:220]

    # 问题清单（按严重度）
    problems = []
    for pid in sorted(results):
        r = results[pid]
        if r["verdict"] == "FAIL":
            sev = "blocker"
        elif r["verdict"] == "UX-ISSUE":
            sev = "major" if pid in ("P2", "P4", "P7", "P8", "P9") else "minor"
        else:
            continue
        problems.append({"severity": sev, "id": pid, "issue": r["conclusion"]})

    # 非失败但用户可见的噪声/惊讶点（单列成通用观察，不改 persona 判定）。
    noisy = [r["id"] for r in results.values()
             if r["verdict"] != "FAIL" and "提示词包 sha" in (r["report"] or "")]
    if noisy:
        problems.append({"severity": "minor", "id": "GEN-1",
                         "issue": "report 末尾追加“提示词包 sha …”与英文 open_choices 等内部信息，普通用户难读（%s）"
                                  % "、".join(sorted(noisy))})
    p3 = results.get("P3")
    if p3 and p3["verdict"] != "FAIL" and (p3.get("checks") or {}).get("cjk", 0) == 0:
        problems.append({"severity": "minor", "id": "GEN-2",
                         "issue": "P3 默认 language=auto 的 caption 输出英文（CJK 0）；中文用户若期望中文，需要显式 language=zh（本次未验证 zh 输出质量）"})
    p1 = results.get("P1")
    if p1 and p1["verdict"] != "FAIL" and (p1.get("checks") or {}).get("output_words", 0) > 150:
        problems.append({"severity": "minor", "id": "GEN-3",
                         "issue": "P1 一句中文被扩成 %d 词英文，新手用户需要大量删减才能用"
                                  % p1["checks"]["output_words"]})
    problems.sort(key=lambda x: {"blocker": 0, "major": 1, "minor": 2}[x["severity"]])

    payload = {"schema": "persona-probe/1", "tool": "tools/persona_probe.py",
               "node_path": "MossPromptSmith.smith", "image": img,
               "params": {"seed": 42, "text_model": text_key, "vl_model": vl_key,
                          "vl_mmproj": mm_key, "port": PORT,
                          "release_after_run": False},
               "total_elapsed_s": total, "records": [results[k] for k in sorted(results)]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                      encoding="utf-8")

    lines = ["# 多用户 persona 实测（P1）", "",
             "- 路径：`MossPromptSmith.smith()` 节点自身路径；文本 `%s`；视觉 `%s` + `%s`"
             % (Path(text_key).name, Path(vl_key).name, Path(mm_key).name),
             "- 图片：`%s`（%s）｜seed 42｜release_after_run=False" % (img["id"], Path(img["path"]).name),
             "- 总耗时：%.1fs｜11 persona" % total, "",
             "| # | persona | 用法 | 耗时 | prompt 摘要 | 判定 | 一句结论 |",
             "|---|---|---|---|---|---|---|"]
    for pid in sorted(results):
        r = results[pid]
        lines.append("| %s | %s | %s/%s | %.1fs | %s | **%s** | %s |"
                     % (pid, r["persona"], r["task"],
                        r["request"].get("dialect") or r["request"].get("target") or "",
                        r["elapsed_s"], (r["prompt_summary"] or r["error"] or "(空)")[:110],
                        r["verdict"], r["conclusion"]))
    lines += ["", "## 问题清单（按严重度）"]
    if not problems:
        lines.append("- 无")
    for p in problems:
        lines.append("- **%s** %s：%s" % (p["severity"], p["id"], p["issue"]))
    lines += ["", "## 逐条报告（用户可见 report 前 12 行）"]
    for pid in sorted(results):
        r = results[pid]
        lines += ["### %s %s" % (pid, r["persona"]), "```",
                  "\n".join(r["report_lines"]) or "（无 report）", "```"]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT / "results.json", OUT / "summary.md",
          "| total %.1fs" % total, "| problems:", len(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
