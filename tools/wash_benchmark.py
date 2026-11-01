# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""T8：洗图基准 = 源图 → 反推 IR → 编译到目标后端 → ComfyUI 出图 → triage 打分。

为什么拆成四步而不是一个函数：每一步都可能单独失败，而**失败的样子不同**。
反推失败是「没看见」，编译失败是「后端装不下这句话」，出图失败是「宿主/权重」，
打分失败是「判据自己坏了」。四步各自落盘，任何一步重跑都不用重烧前面的 GPU 时间。

流水线：

    源图 ──①反推(VLM, llama-server)──▶ IR(.ir.json)
         ──②编译(零模型)───────────▶ prompt + issues/invariants（每后端一份）
         ──③出图(ComfyUI /prompt)───▶ PNG
         ──④triage(纯 numpy/PIL)───▶ score.json

**打分口径（必须先读）**：本机没有 VQAScore（包未安装、模型权重不在本机、校准未做），
所以这里用的是**代理指标**：调色直方图相关、灰度结构相关、亮度/饱和/边缘密度差、比例一致性。
它只回答「漂了多少」，不回答「图好不好」，**未校准、只 triage、不作放行闸**
（`score.json` 里每个分数都带 `metric.calibrated=false`）。

用法：
    python tools\\wash_benchmark.py --check            # 离线验设计/源图/判据
    python tools\\wash_benchmark.py --check-workflows  # 只读 /object_info 验图
    python tools\\wash_benchmark.py --plan             # 落流水线计划
    python tools\\wash_benchmark.py --caption          # 只跑反推（占 GPU: llama-server）
    python tools\\wash_benchmark.py --generate --dry   # 只落 workflow，不排队
    python tools\\wash_benchmark.py --generate         # 只跑出图（占 GPU: ComfyUI）
    python tools\\wash_benchmark.py --score            # 只打分（零 GPU）
    python tools\\wash_benchmark.py --all              # 四步全跑
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from moss_llm.smith.api import compile_ir                # noqa: E402
from _paths import host_root                             # noqa: E402

INDEX = ROOT / "tools" / "workflows" / "_index.json"
OUT = ROOT / "out" / "wash_benchmark"
DEFAULT_API = "http://127.0.0.1:8188"

#: 反推侧车配置模板（T1 里核实过的 6G 组合）。
#:
#: ⚠ **不写死本机路径**（公开仓要求）：模型权重与 llama-server 由使用者给。
#:   llama-server：CLI `--server` → `MOSS_LLM_SERVER` → `G7_SERVER` → 包内
#:     `moss_llm.server.find_server_exe()` 常规查找（与 tools/g7_caption.py 同口径）
#:   模型：CLI `--model` → `MOSS_LLM_MODEL` / `G7_MODEL`
#:   视觉塔：CLI `--mmproj` → `MOSS_LLM_MMPROJ` / `G7_MMPROJ`
#:   端口刻意用 8193：8091 是 g7/T1 的常用口，T8 不与它抢。
CAPTION = {
    "model": "",
    "mmproj": "",
    "exe": "",
    "port": 8193,
    # 16K：满屏文字的 UI 截图会写出很长的结构化记录；实测 ctx=8192 时
    # 在 8191 token 处被截断（llama-server 日志 `n_tokens = 8191, truncated = 1`）。
    "ctx": 16384,
    "temperature": 0.1,
    "max_tokens": 8192,
    "max_side": 1536,
    # 文字专线默认 max_tokens=1200，满屏文字的 UI 截图会在 JSON 中途被截断（实测）。
    # 场景记录成功但文字专线失败时，用这个预算重试一次文字专线（多花一次带图调用）。
    "text_retry_max_tokens": 4000,
}

SERVER_ENVS = ("MOSS_LLM_SERVER", "G7_SERVER")
MODEL_ENVS = ("MOSS_LLM_MODEL", "G7_MODEL")
MMPROJ_ENVS = ("MOSS_LLM_MMPROJ", "G7_MMPROJ")


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def resolve_caption_conf(server: str = "", model: str = "", mmproj: str = "",
                         port: int | None = None, ctx: int | None = None) -> dict:
    """反推侧车配置：CLI > 环境变量 > 明确报错。**不猜本机路径**。"""
    conf = dict(CAPTION)
    conf["exe"] = (server or "").strip() or _first_env(SERVER_ENVS)
    model_p = (model or "").strip() or _first_env(MODEL_ENVS)
    if not model_p:
        raise RuntimeError(
            "不知道用哪个 GGUF 反推。两种给法：\n"
            "  ① --model <视觉语言模型 GGUF> --mmproj <视觉塔 GGUF>\n"
            "  ② 环境变量 MOSS_LLM_MODEL=... / MOSS_LLM_MMPROJ=...\n"
            "本工具不写死本机路径（公开仓要求）。")
    conf["model"] = model_p
    conf["mmproj"] = (mmproj or "").strip() or _first_env(MMPROJ_ENVS)
    if port is not None:
        conf["port"] = int(port)
    if ctx is not None:
        conf["ctx"] = int(ctx)
    return conf

#: 目标后端。`available=False` 的**如实记录缺失**，不构造假 workflow。
BACKENDS = {
    "moss-image": {
        "available": True,
        "compile_target": "moss-image",
        "workflow": "moss-image",
        "note": "MOSS-Image SFT 4steps turbo merged（本机权重；历史对照档，本轮不跑）",
    },
    "moss-image-q4": {
        "available": True,
        "compile_target": "moss-image",
        "workflow": "moss-image-q4",
        "note": ("用户指定档：MOSS-Image-SFT-Q4_K_S + 8-steps-dmd2 LoRA + "
                 "moss-te-heretic TE（8 步 / cfg 1.0 / euler / simple）"),
    },
    "krea2": {
        "available": True,
        "compile_target": "krea2",
        "workflow": "krea2",
        "note": "Krea 2 Turbo HD V1 NVFP4（本机权重）",
    },
    "qwen-image-2_1": {
        "available": True,
        "compile_target": "qwen-image-2_1",
        "workflow": "qwen-image-2_1",
        "note": "本机权重是 Qwen-Image-Flash Q4_K_M；registry target 是 2.1（同家族不同代，结论不外推）",
    },
    "flux3": {
        "available": False,
        "compile_target": "flux3",
        "workflow": None,
        "reason": "本机 models/diffusion_models 无 flux3 生成权重（只有 metadata 与历史产物）",
    },
    "flux1_dev": {
        "available": False,
        "compile_target": "flux1_dev",
        "workflow": None,
        "reason": "本机无 flux1 生成权重；FLUX.1 dev 的 28 步档也无对应宿主模型",
    },
}

#: 源图：四类各一张（单人特写 / 场景构图 / 含图内文字 / 材质反光）。
#: 仓库内的图用相对路径；宿主 ComfyUI 的图用 `host_root()` 拼（**不写死盘符**，
#: 见 tools/_paths.py：COMFYUI_PATH → .comfyui_path → 标准安装位置推断）。
_HOST = host_root()
SOURCES = [
    {"id": "ui-text", "category": "含图内文字", "path": "docs/ui-smoke.png",
     "why": "满屏文字的 UI 截图：文字专线与逐字保真是洗图最容易丢的东西"},
    {"id": "portrait", "category": "单人特写",
     "path": os.path.join(_HOST, "input", "主角C15-进击巨人-方下巴英气浓眉盘发_s88110015.png"),
     "why": "单人特写：身份/五官是洗图漂移最明显的地方"},
    {"id": "street", "category": "场景构图",
     "path": os.path.join(_HOST, "input", "主街场景首帧图.png"),
     "why": "场景：构图与透视能不能保住"},
    {"id": "material", "category": "材质与反光",
     "path": os.path.join(_HOST, "input", "灰岩法庭_日_03_特写背景_seed5302.png"),
     "why": "材质/反光：调色与质感统计能不能对上"},
]

#: 代理指标权重。刻意写在这里而不是散落在函数里：改权重就是改判据，得在文档里说。
METRIC = {
    "name": "proxy-v1",
    "calibrated": False,
    "not_for_gating": True,
    "vqascore": "absent：包未安装、权重不在本机、未做元评估；按任务口径退到代理指标",
    "weights": {"structure": 0.40, "palette": 0.25, "luma": 0.15,
                "saturation": 0.10, "edge": 0.10},
    "note": ("代理指标只回答「漂了多少」，不回答「图好不好」。"
             "没有与人类偏好做过一致性检验 ⇒ 只 triage、不作放行闸。"),
}


# ─────────────────────────────────────────────────────────────
# 源图与尺寸
# ─────────────────────────────────────────────────────────────

def _rel(path: Path) -> str:
    """展示用路径：在仓库内就写相对路径，临时目录/外部盘就原样写绝对路径。"""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_sources(sources: list[dict] | None = None) -> list[dict]:
    """核对源图存在性并算 sha256。缺失的记 missing，不静默跳过。"""
    out = []
    for src in (sources or SOURCES):
        p = Path(src["path"])
        if not p.is_absolute():
            p = ROOT / p
        item = {"id": src["id"], "category": src["category"], "path": str(p),
                "why": src.get("why", ""), "exists": p.is_file()}
        if item["exists"]:
            item["sha256"] = _sha256_file(p)
            item["bytes"] = p.stat().st_size
            from PIL import Image
            with Image.open(p) as im:
                item["size"] = list(im.size)
        else:
            item["missing_reason"] = "文件不存在"
        out.append(item)
    return out


def load_sources_file(path) -> list[dict]:
    """从 G7 格式 JSON（`{"images":[{id,category,path,...}]}`）载入源图清单。

    找不到文件 / 缺 path / 路径不存在都直接报错——**不静默跳过**（W1 任务口径）。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("--sources-file 不存在：%s" % p)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError("--sources-file 不是合法 JSON：%s（%s）" % (p, exc)) from exc
    imgs = doc.get("images") if isinstance(doc, dict) else None
    if not imgs:
        raise RuntimeError("--sources-file 没有 images 数组：%s" % p)
    out = []
    for i, im in enumerate(imgs):
        if not isinstance(im, dict) or not str(im.get("id") or "").strip():
            raise RuntimeError("--sources-file 第 %d 项缺 id：%s" % (i, p))
        raw = str(im.get("path") or "").strip()
        if not raw:
            raise RuntimeError("--sources-file 源图 %r 缺 path：%s" % (im.get("id"), p))
        src_path = Path(raw) if Path(raw).is_absolute() else ROOT / raw
        if not src_path.is_file():
            raise FileNotFoundError("--sources-file 源图 %r 不存在：%s"
                                    % (im.get("id"), src_path))
        out.append({"id": str(im["id"]), "category": str(im.get("category") or "未分类"),
                    "path": str(src_path),
                    "why": str(im.get("why") or im.get("name") or "")})
    return out


def parse_seeds(text: str | None) -> list[int] | None:
    """解析 `--seeds "42,43,44"`；空白 = None（单 seed 旧行为）。非法值直接报错。"""
    if text is None or not str(text).strip():
        return None
    seeds = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            sd = int(part)
        except ValueError:
            raise RuntimeError("--seeds 里有非整数：%r（示例：42,43,44）" % part) from None
        if sd < 0:
            raise RuntimeError("--seeds 不接受负数：%d" % sd)
        seeds.append(sd)
    if not seeds:
        raise RuntimeError("--seeds 解析后为空（示例：42,43,44）")
    if len(set(seeds)) != len(seeds):
        raise RuntimeError("--seeds 有重复：%r" % seeds)
    return seeds


def resolve_out(value: str | None) -> Path:
    """`--out`：相对路径按仓库根解析；默认沿用全局 OUT（旧产物路径行为不变）。"""
    if not value:
        return OUT
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def size_for(shape: tuple[int, int] | None, mp: float = 1.0,
             step: int = 16, lo: int = 512, hi: int = 1536) -> tuple[int, int]:
    """按源图比例算一个面积≈mp 兆像素、边长是 step 倍数、且落在 [lo, hi] 的画布。"""
    if not shape or not shape[0] or not shape[1]:
        return 1024, 1024
    ar = float(shape[0]) / float(shape[1])
    h = math.sqrt(mp * 1_000_000 / ar)
    w = h * ar
    w = max(lo, min(hi, int(round(w / step)) * step))
    h = max(lo, min(hi, int(round(h / step)) * step))
    return int(w), int(h)


# ─────────────────────────────────────────────────────────────
# 反推（VLM）
# ─────────────────────────────────────────────────────────────

def stage_sources(sources: list[dict] | None = None, out: Path | None = None) -> dict:
    """把源图复制进产物目录（零 GPU）。产物自包含：原图挪走了也能复核 sha256。

    增量合并：只 stage 一部分源图时，不清掉 sources.json 里已有的条目。
    """
    out = out or OUT
    srcdir = out / "sources"
    srcdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for src in resolve_sources(sources):
        row = dict(src)
        if src["exists"]:
            dest = srcdir / (src["id"] + Path(src["path"]).suffix.lower())
            # 源图本来就在 sources/ 里（二次 stage）时 copyfile 会 SameFileError，跳过即可
            if Path(src["path"]).resolve() != dest.resolve():
                shutil.copyfile(src["path"], dest)
            row["staged"] = _rel(dest)
        else:
            row["staged"] = None
        rows.append(row)
    path = out / "sources.json"
    merged = {}
    if path.is_file():
        try:
            for old in (json.loads(path.read_text(encoding="utf-8")).get("sources") or []):
                merged[old.get("id")] = old
        except (ValueError, OSError):
            merged = {}
    merged.update({r["id"]: r for r in rows})
    doc = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "sources": [merged[k] for k in merged]}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def stage_ir_sources(sources: list[dict] | None = None, ir_root=None,
                     out: Path | None = None) -> dict:
    """从既有 G7 产物复制 IR（不跑 llama-server / 不占 GPU）。

    `ir_root` 下每个 `<id>.json` 是一个 G7 单元记录：取其中 `ir` 字段落到
    `captions/<id>.ir.json`；`ok=false`（跑批失败）的单元记 `skipped` 并列出，
    不静默跳过。源图仍会 stage 一份（sha256 可复核）。
    """
    if ir_root is None:
        raise RuntimeError("需要 --ir-root <G7 产物目录>")
    root = Path(ir_root)
    if not root.is_dir():
        raise FileNotFoundError("--ir-root 不是目录：%s" % root)
    out = out or OUT
    capdir = out / "captions"
    capdir.mkdir(parents=True, exist_ok=True)
    staged = stage_sources(sources, out)["sources"]
    requested = {s["id"] for s in (sources or SOURCES)}
    ok_rows, skipped = [], []
    for src in staged:
        if src["id"] not in requested:
            continue
        record = root / ("%s.json" % src["id"])
        if not record.is_file():
            skipped.append({"id": src["id"], "reason": "缺 G7 记录 %s" % _rel(record)})
            print("  ✗ ir-root %-10s 缺记录 %s" % (src["id"], _rel(record)))
            continue
        try:
            doc = json.loads(record.read_text(encoding="utf-8"))
        except ValueError as exc:
            skipped.append({"id": src["id"], "reason": "G7 记录不是合法 JSON：%s" % exc})
            continue
        if doc.get("ok") is False:
            skipped.append({"id": src["id"],
                            "reason": "G7 单元 ok=false：%s"
                                      % str(doc.get("error") or "")[:200]})
            print("  - ir-root %-10s ok=false（单列，不判）" % src["id"])
            continue
        ir = doc.get("ir")
        if not isinstance(ir, dict):
            skipped.append({"id": src["id"], "reason": "G7 记录里没有 ir 对象"})
            continue
        ir_path = capdir / ("%s.ir.json" % src["id"])
        ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")
        ok_rows.append({"id": src["id"], "category": src["category"],
                        "ir": _rel(ir_path), "g7_record": _rel(record),
                        "staged_source": src.get("staged"),
                        "entities": len(ir.get("entities") or []),
                        "source": "g7-ir-root", "reverse_mode": "reuse-g7-ir"})
    cap_manifest_path = out / "captions" / "manifest.json"
    old_sources = {}
    if cap_manifest_path.is_file():
        try:
            for old in (json.loads(cap_manifest_path.read_text(encoding="utf-8"))
                        .get("sources") or []):
                old_sources[old.get("id")] = old
        except (ValueError, OSError):
            old_sources = {}
    merged = dict(old_sources)
    merged.update({e["id"]: e for e in ok_rows})
    manifest = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "mode": "ir-root", "ir_root": _rel(root),
                "sources": [merged[k] for k in merged], "skipped": skipped}
    cap_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return manifest


def _reverse_or_fallback(image_path: Path, llm, conf: dict):
    """反推；场景记录崩了/空实体时降级为文字专线重建。返回 (ir, report, mode)。

    为什么要有降级：满屏文字的 UI 截图会让场景记录写成很长的结构（实测 ctx=8192 时
    llama-server 在 8191 token 处 `truncated=1`，JSON 没闭合）。这不是「图有问题」，
    是 token 预算问题——所以补一次只问文字、更大预算的调用，而不是把这张图报成失败。
    """
    from moss_llm.smith.ir import IR as IRClass
    from moss_llm.smith.reverse import (Report as ReverseReport, merge_text_blocks,
                                        reverse, transcribe)

    mode = "scene+text"
    try:
        ir, report = reverse(image_path, llm,
                             temperature=conf.get("temperature", 0.1),
                             max_tokens=conf.get("max_tokens", 8192),
                             max_side=conf.get("max_side", 1536))
    except Exception as exc:                       # 场景记录失败：从空 IR 重建
        report = ReverseReport(image=str(image_path))
        ir = IRClass(task="reverse")
        report.warn("场景记录失败（%s: %s），改为文字专线重建"
                    % (type(exc).__name__, str(exc)[:160]))
        mode = "text_only"

    retry = conf.get("text_retry_max_tokens")
    tp = ir.provenance.get("text_pass") or {}
    need_rescue = bool(retry) and (not ir.entities or tp.get("blocks") == 0)
    if need_rescue:
        try:
            blocks = transcribe(image_path, llm, max_tokens=int(retry))
            changes = merge_text_blocks(ir, blocks, report)
            ir.normalize()
            ir.provenance["text_rescue"] = {"blocks": len(blocks),
                                            "changes": len(changes),
                                            "max_tokens": int(retry)}
            report.warn("文字专线补救：并入 %d 条（max_tokens=%d）"
                        % (len(changes), retry))
            if mode == "scene+text":
                mode = "scene+text_rescue"
        except Exception as exc:                   # 补救失败不拖垮整批
            ir.provenance["text_rescue"] = {"error": str(exc)[:200]}
            report.warn("文字专线补救失败：%s" % str(exc)[:160])
    return ir, report, mode


def caption_sources(sources: list[dict] | None = None, out: Path | None = None,
                    conf: dict | None = None, limit: int | None = None) -> dict:
    """源图 → IR + 取证报告，落 `captions/{id}.ir.json` / `.report.json`。占 GPU。"""
    from moss_llm.server import LlamaServer

    conf = resolve_caption_conf() if conf is None else dict(conf)
    out = out or OUT
    capdir = out / "captions"
    capdir.mkdir(parents=True, exist_ok=True)
    staged = stage_sources(sources, out)["sources"]
    # stage_sources 会跟已有 sources.json 合并（保留全量清单），所以这里必须按
    # 本次**请求的** id 过滤，否则 --only-source 会退化成「重跑全部」（踩过）。
    requested = {s["id"] for s in (sources or SOURCES)}
    manifest = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conf": conf,
                "sources": []}
    cap_manifest_path = out / "captions" / "manifest.json"
    old_sources = {}
    if cap_manifest_path.is_file():                    # 增量跑（--only-source）不清旧结果
        try:
            for old in (json.loads(cap_manifest_path.read_text(encoding="utf-8"))
                        .get("sources") or []):
                old_sources[old.get("id")] = old
        except (ValueError, OSError):
            old_sources = {}
    todo = [s for s in staged if s["exists"] and s["id"] in requested]
    if limit:
        todo = todo[:limit]
    if not todo:
        raise RuntimeError("没有可反推的源图（全部缺失？先跑 --plan 看 missing）")

    with LlamaServer(model=conf["model"], exe=conf.get("exe", ""),
                     port=conf.get("port", 8193), ctx=conf.get("ctx", 8192),
                     mmproj=conf["mmproj"]) as llm:
        manifest["server_notes"] = list(llm.notes)
        for src in todo:
            t0 = time.time()
            image_path = ROOT / src["staged"] if not Path(src["staged"]).is_absolute() \
                else Path(src["staged"])
            try:
                ir, report, mode = _reverse_or_fallback(image_path, llm, conf)
            except Exception as exc:                  # 单张失败只记一条，不拖垮整批
                manifest.setdefault("errors", []).append(
                    {"id": src["id"], "error": str(exc)[:400]})
                print("  ✗ caption %-10s %s" % (src["id"], str(exc)[:140]))
                continue
            if not ir.entities:
                manifest.setdefault("errors", []).append(
                    {"id": src["id"], "error": "反推回来 0 个实体（降级后仍空）"})
                print("  ✗ caption %-10s 0 实体" % src["id"])
                continue
            ir_path = capdir / ("%s.ir.json" % src["id"])
            ir_path.write_text(json.dumps(ir.to_dict(), ensure_ascii=False, indent=2),
                               encoding="utf-8")
            rep = report.to_dict()
            rep["elapsed_s"] = round(time.time() - t0, 1)
            rep["image"] = str(Path(src["path"]).name)
            rep["sha256"] = src["sha256"]
            rep["reverse_mode"] = mode
            (capdir / ("%s.report.json" % src["id"])).write_text(
                json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
            entry = {"id": src["id"], "category": src["category"],
                     "ir": _rel(ir_path),
                     "report": _rel(capdir / ("%s.report.json" % src["id"])),
                     "staged_source": src["staged"], "reverse_mode": mode,
                     "entities": len(ir.entities), "elapsed_s": rep["elapsed_s"],
                     "warnings": len(rep.get("warnings") or []),
                     "size": src.get("size")}
            manifest["sources"].append(entry)
            print("  ✓ caption %-10s %2d 实体 %5.1fs %-16s %s"
                  % (src["id"], entry["entities"], entry["elapsed_s"], mode,
                     "（有警告）" if entry["warnings"] else ""))
    manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    merged = dict(old_sources)
    merged.update({e["id"]: e for e in manifest["sources"]})
    manifest["sources"] = [merged[k] for k in merged]
    cap_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return manifest


# ─────────────────────────────────────────────────────────────
# 编译 + 出图
# ─────────────────────────────────────────────────────────────

def load_index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def load_template(backend: str) -> dict:
    idx = load_index()
    b = idx["backends"][backend]
    return json.loads((INDEX.parent / b["file"]).read_text(encoding="utf-8"))


def build_workflow(backend: str, prompt: str, seed: int, width: int, height: int,
                   prefix: str) -> dict:
    """与 tools/pitfalls_ab.py 同源的注入逻辑；模板来自 tools/workflows/_index.json。"""
    wf = load_template(backend)
    b = load_index()["backends"][backend]
    nodes = b["nodes"]
    wf[nodes["positive"]]["inputs"]["text"] = prompt
    wf[nodes["sampler"]]["inputs"]["seed"] = int(seed)
    wf[nodes["latent"]]["inputs"]["width"] = int(width)
    wf[nodes["latent"]]["inputs"]["height"] = int(height)
    wf[nodes["save"]]["inputs"]["filename_prefix"] = prefix
    if "shift" in nodes:
        wf[nodes["shift"]]["inputs"]["shift"] = float(b["defaults"]["shift"])
    return wf


def validate_workflow(wf: dict, infos: dict, backend: str = "") -> list[str]:
    """静态 + /object_info 校验（与 T6 同口径）。返回问题列表。"""
    problems = []
    for nid, node in wf.items():
        ct = node.get("class_type")
        info = infos.get(ct)
        if info is None:
            problems.append("%s: 服务端没有节点类型 %r" % (nid, ct))
            continue
        inputs = node.get("inputs") or {}
        for name in ((info.get("input") or {}).get("required") or {}):
            if name not in inputs:
                problems.append("%s(%s): 缺必填输入 %s" % (nid, ct, name))
        for name, val in inputs.items():
            if isinstance(val, list):
                if len(val) != 2:
                    problems.append("%s(%s): 连线输入 %s 形状不对" % (nid, ct, name))
                    continue
                src = str(val[0])
                if src not in wf:
                    problems.append("%s(%s): 连线 %s -> 不存在的节点 %s"
                                    % (nid, ct, name, src))
                    continue
                outs = (infos.get(wf[src].get("class_type")) or {}).get("output") or []
                if not isinstance(val[1], int) or val[1] < 0 or (outs and val[1] >= len(outs)):
                    problems.append("%s(%s): 连线 %s 槽位 %r 越界" % (nid, ct, name, val[1]))
    return problems


def check_workflows(api: str = DEFAULT_API) -> list[str]:
    infos = get_json(api.rstrip("/") + "/object_info", timeout=60)
    problems = []
    for backend, b in BACKENDS.items():
        if not b.get("available"):
            continue
        try:
            wf = load_template(b["workflow"])
        except Exception as exc:
            problems.append("%s: 读模板失败 %s" % (backend, exc))
            continue
        for p in validate_workflow(wf, infos, backend):
            problems.append("%s: %s" % (backend, p))
    # 模板索引里把 flux 家族登记为 unavailable（不是漏登记）
    idx = load_index()
    for name in ("flux3", "flux1_dev"):
        if name not in idx.get("unavailable", {}):
            problems.append("模板索引没有把 %s 记进 unavailable" % name)
    return problems


def seed_for(backend: str, source_id: str) -> int:
    """确定性 seed：同一 (后端, 源图) 每次跑都一样，便于回归对账。"""
    h = hashlib.sha256(("%s|%s" % (backend, source_id)).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % 2_000_000_000


def compile_for(ir_path: Path, backend: str) -> dict:
    """IR → 目标后端 prompt。失败返回 status=error 的 dict，不抛（一条后端坏不该拖垮整批）。"""
    target = BACKENDS[backend]["compile_target"]
    t0 = time.time()
    try:
        result = compile_ir(str(ir_path), target)
    except Exception as exc:                              # CompileError / 任何后端不匹配
        return {"backend": backend, "target": target, "status": "error",
                "error": "%s: %s" % (type(exc).__name__, exc),
                "elapsed_ms": round((time.time() - t0) * 1000, 1)}
    positive = result.get("positive") or ""
    return {
        "backend": backend, "target": target, "status": "ok",
        "positive": positive,
        "mode": "layout_json" if positive.lstrip().startswith("{") else "prose",
        "negative": result.get("negative") or "",
        "token_count": result.get("token_count"),
        "token_limit": result.get("token_limit"),
        "issues": [i for i in (result.get("issues") or []) if i.get("level") == "error"],
        "warnings": result.get("warnings") or [],
        "invariants_ok": result.get("invariants_ok"),
        "invariants_line": result.get("invariants_line"),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }


def generate(sources: list[dict] | None = None, out: Path | None = None,
             api: str = DEFAULT_API, only: list[str] | None = None,
             dry: bool = False, timeout: float = 900.0,
             seed: int | None = None, seeds: list[int] | None = None) -> dict:
    """IR → prompt → ComfyUI 出图。占 GPU。`dry=True` 只落 workflow。

    `seeds` 非空 = 多 seed 方差线：每个 (source, seed) 一条记录，文件命名
    `images/<backend>/<id>__seed<N>.png`；不传 `seeds` 时行为与旧版完全一致
    （`<id>.png`、每源一条、seed 用 `--seed` 或确定性 `seed_for`）。
    """
    out = out or OUT
    idx = load_index()
    srcs = [s for s in resolve_sources(sources) if s["exists"]]
    capdir = out / "captions"
    seed_list = list(seeds) if seeds else [seed if seed is not None else None]
    run = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "api": api, "dry": dry,
           "seeds": list(seeds) if seeds else None,
           "backends": {}, "skipped": {}}

    def save() -> None:
        # 增量落盘：中断/模型重载失败时前面的结果不丢
        (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2),
                                      encoding="utf-8")

    for backend, b in BACKENDS.items():
        if only and backend not in only:
            continue
        if not b.get("available"):
            run["skipped"][backend] = {"reason": b.get("reason", "不可用"),
                                       "workflow": None}
            print("  - 跳过 %-16s %s" % (backend, b.get("reason", "")))
            continue
        bucket = {"images": [], "errors": []}
        # 先挂进 run（引用），之后的增量 save() 才能带上本后端的中间结果；
        # 否则中断时 run.json 里的 backends 还是空的（本轮真跑踩到）。
        run["backends"][backend] = bucket
        for src in srcs:
            ir_path = capdir / ("%s.ir.json" % src["id"])
            if not ir_path.is_file():
                bucket["errors"].append({"source": src["id"],
                                         "error": "缺 %s（先跑 --caption / --stage）"
                                                  % ir_path.name})
                continue
            comp = compile_for(ir_path, backend)
            cdir = out / "compile" / backend
            cdir.mkdir(parents=True, exist_ok=True)
            comp_path = cdir / ("%s.json" % src["id"])
            comp_path.write_text(json.dumps(comp, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            if comp["status"] != "ok":
                bucket["errors"].append({"source": src["id"], "error": comp["error"]})
                save()
                continue
            w, h = size_for(tuple(src.get("size") or ()) or None)
            for sd_override in seed_list:
                sd = sd_override if sd_override is not None else seed_for(backend, src["id"])
                suffix = ("__seed%d" % sd_override) if seeds else ""
                prefix = "wash_benchmark/%s/%s%s" % (backend, src["id"], suffix)
                wf = build_workflow(backend, comp["positive"], sd, w, h, prefix)
                wpath = out / "workflows" / backend / (
                    "%s%s.%s.api.json" % (src["id"], suffix, "dry" if dry else "run"))
                wpath.parent.mkdir(parents=True, exist_ok=True)
                wpath.write_text(json.dumps(wf, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
                entry = {"source": src["id"], "category": src["category"],
                         "seed": sd, "width": w, "height": h,
                         "prompt_mode": comp["mode"],
                         "prompt_chars": len(comp["positive"]),
                         "token_count": comp["token_count"],
                         "token_limit": comp["token_limit"],
                         "compile_errors": comp["issues"], "workflow": _rel(wpath)}
                if seeds:
                    entry["seed_tag"] = sd_override
                if dry:
                    bucket["images"].append(entry)
                    save()
                    continue
                t0 = time.time()
                try:
                    q = queue_and_wait(api, wf, timeout=timeout)
                    if q["status"] != "success":
                        raise RuntimeError("status=%s：%s" % (
                            q["status"], json.dumps(q["entry"].get("status"),
                                                   ensure_ascii=False)[:600]))
                    items = collect_outputs(
                        q["entry"], idx["backends"][b["workflow"]]["nodes"]["save"])
                    if not items:
                        raise RuntimeError("没有输出图")
                    dest = out / "images" / backend / ("%s%s.png" % (src["id"], suffix))
                    download(api, items[0], dest)
                except Exception as exc:
                    bucket["errors"].append({"source": src["id"], "seed": sd,
                                             "error": str(exc)[:800]})
                    print("  ✗ %-16s %-10s seed=%-10s %s"
                          % (backend, src["id"], sd, str(exc)[:110]))
                    save()
                    continue
                entry.update({"image": _rel(dest), "prompt_id": q["prompt_id"],
                              "elapsed_s": round(time.time() - t0, 1),
                              "server_file": items[0]})
                bucket["images"].append(entry)
                print("  ✓ %-16s %-10s seed=%-10s %dx%d %.1fs"
                      % (backend, src["id"], sd, w, h, entry["elapsed_s"]))
                save()
        run["backends"][backend] = bucket
        run["partial"] = backend
        save()
    run.pop("partial", None)
    run["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save()
    return run


# ─────────────────────────────────────────────────────────────
# triage 打分（纯 numpy/PIL，零 GPU、零模型）
# ─────────────────────────────────────────────────────────────

def _load_rgb(path: Path):
    from PIL import Image
    with Image.open(path) as im:
        return im.convert("RGB")


def _normalize_hist(hist):
    import numpy as np
    arr = np.asarray(hist, dtype="float64")
    total = arr.sum()
    return arr / total if total else arr


def _bhattacharyya(p, q) -> float:
    """两个归一化直方图的 Bhattacharyya 系数（0..1，越大越像）。"""
    import numpy as np
    return float(np.sqrt(p * q).sum())


def _hsv_hist(img, bins_h=24, bins_s=12, bins_v=12):
    import numpy as np
    hsv = np.asarray(img.convert("HSV"), dtype="uint8")
    h = _normalize_hist(np.histogram(hsv[:, :, 0], bins=bins_h, range=(0, 256))[0])
    s = _normalize_hist(np.histogram(hsv[:, :, 1], bins=bins_s, range=(0, 256))[0])
    v = _normalize_hist(np.histogram(hsv[:, :, 2], bins=bins_v, range=(0, 256))[0])
    return h, s, v


def _gray_thumb(img, n=64):
    import numpy as np
    small = img.convert("L").resize((n, n))
    a = np.asarray(small, dtype="float64")
    a = a - a.mean()
    denom = a.std()
    return a / denom if denom > 1e-6 else a


def _edge_density(img):
    import numpy as np
    g = np.asarray(img.convert("L").resize((160, 160)), dtype="float64")
    gx = np.abs(np.diff(g, axis=1))
    gy = np.abs(np.diff(g, axis=0))
    return float((gx.mean() + gy.mean()) / 2.0)


def score_pair(source_path: Path, generated_path: Path) -> dict:
    """源图 vs 生成图的代理指标。返回原始分量 + triage_index（0..1）。"""
    import numpy as np
    from PIL import Image

    src, gen = _load_rgb(source_path), _load_rgb(generated_path)
    with Image.open(generated_path) as im:
        gen_size = list(im.size)
    with Image.open(source_path) as im:
        src_size = list(im.size)

    # ① 结构：64×64 灰度 z-score 后的相关系数
    a, b = _gray_thumb(src), _gray_thumb(gen)
    denom = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    r = float((a * b).sum() / denom) if denom > 1e-9 else 0.0
    structure = max(0.0, min(1.0, (r + 1.0) / 2.0))

    # ② 调色：HSV 三分量直方图的 Bhattacharyya 系数（色相权重更高）
    hs, ss, vs = _hsv_hist(src)
    hg, sg, vg = _hsv_hist(gen)
    palette = 0.5 * _bhattacharyya(hs, hg) + 0.25 * _bhattacharyya(ss, sg) \
        + 0.25 * _bhattacharyya(vs, vg)

    # ③ 亮度：均值/标准差的接近程度
    le_s = float(np.asarray(src.convert("L"), dtype="float64").mean()) / 255.0
    le_g = float(np.asarray(gen.convert("L"), dtype="float64").mean()) / 255.0
    ls_s = float(np.asarray(src.convert("L"), dtype="float64").std()) / 128.0
    ls_g = float(np.asarray(gen.convert("L"), dtype="float64").std()) / 128.0
    luma = max(0.0, 1.0 - (abs(le_s - le_g) * 1.5 + abs(ls_s - ls_g) * 0.75))

    # ④ 饱和度：均值差
    sat_s = float(np.asarray(src.convert("HSV"), dtype="float64")[:, :, 1].mean()) / 255.0
    sat_g = float(np.asarray(gen.convert("HSV"), dtype="float64")[:, :, 1].mean()) / 255.0
    saturation = max(0.0, 1.0 - abs(sat_s - sat_g) * 2.0)

    # ⑤ 边缘密度：相对差
    ed_s, ed_g = _edge_density(src), _edge_density(gen)
    edge = max(0.0, 1.0 - abs(ed_s - ed_g) / max(ed_s, 1e-6))

    # ⑥ 比例：生成图应保持源图比例（这条是硬结构，不算进加权分，单独记录）
    ar_s = src_size[0] / src_size[1]
    ar_g = gen_size[0] / gen_size[1]
    aspect = max(0.0, 1.0 - abs(ar_s - ar_g) / ar_s)

    scores = {"structure": round(structure, 4), "palette": round(palette, 4),
              "luma": round(luma, 4), "saturation": round(saturation, 4),
              "edge": round(edge, 4)}
    total_w = sum(METRIC["weights"].values())
    triage = sum(scores[k] * w for k, w in METRIC["weights"].items()) / total_w
    return {
        "scores": scores,
        "aspect_match": round(aspect, 4),
        "triage_index": round(triage, 4),
        "generated_size": gen_size,
        "source_size": src_size,
        "degenerate": _is_degenerate(gen),
    }


def _is_degenerate(img) -> bool:
    """全黑/全白/近单色的出图：分数没有意义，单独标出来。"""
    import numpy as np
    a = np.asarray(img.convert("L"), dtype="float64")
    return bool(a.std() < 4.0)


def make_compares(sources: list[dict] | None = None, out: Path | None = None) -> list[str]:
    """源图 | 生成图 并排拼图，供目检与文档引用（零 GPU）。返回拼图路径列表。"""
    from PIL import Image, ImageDraw

    out = out or OUT
    run_path = out / "run.json"
    if not run_path.is_file():
        raise RuntimeError("没有 %s：先跑 --generate" % _rel(run_path))
    run = json.loads(run_path.read_text(encoding="utf-8"))
    srcs = {s["id"]: s for s in resolve_sources(sources)}
    made = []
    for backend, bucket in (run.get("backends") or {}).items():
        for entry in bucket.get("images") or []:
            src = srcs.get(entry["source"])
            gen = ROOT / entry["image"]
            if not src or not src.get("exists") or not gen.is_file():
                continue
            ia = _load_rgb(Path(src["path"]))
            ib = _load_rgb(gen)
            h = 640
            ia = ia.resize((round(ia.width * h / ia.height), h))
            ib = ib.resize((round(ib.width * h / ib.height), h))
            band = 26
            canvas = Image.new("RGB", (ia.width + ib.width + 8, h + band), (24, 24, 24))
            canvas.paste(ia, (0, band))
            canvas.paste(ib, (ia.width + 8, band))
            draw = ImageDraw.Draw(canvas)
            draw.text((6, 5), "SOURCE: %s" % entry["source"], fill=(255, 255, 255))
            draw.text((ia.width + 14, 5), "GENERATED: %s" % backend, fill=(255, 255, 255))
            dest = out / "images" / backend / ("%s.compare.png" % entry["source"])
            canvas.save(dest)
            made.append(_rel(dest))
    return made


def score_run(sources: list[dict] | None = None, out: Path | None = None) -> dict:
    """读 run.json + 图片，产出 score.json。零 GPU。

    多 seed 时按 source 聚合：`mean/min/max/range`（同源跨 seed 的 triage 极差），
    同时保留每个 (source, seed) 的原始行（单 seed 时行结构不变）。
    """
    out = out or OUT
    run_path = out / "run.json"
    if not run_path.is_file():
        raise RuntimeError("没有 %s：先跑 --generate" % _rel(run_path))
    run = json.loads(run_path.read_text(encoding="utf-8"))
    srcs = {s["id"]: s for s in resolve_sources(sources)}
    result = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "metric": dict(METRIC), "backends": {}, "ranking": [],
              "missing_backends": {}}
    means = {}
    for backend, bucket in (run.get("backends") or {}).items():
        rows = []
        for entry in bucket.get("images") or []:
            src = srcs.get(entry["source"])
            gen = ROOT / entry["image"]
            if not src or not src.get("exists") or not gen.is_file():
                continue
            sc = score_pair(Path(src["path"]), gen)
            row = {"source": entry["source"], "category": entry.get("category"),
                   "image": entry["image"], **sc}
            if "seed" in entry:
                row["seed"] = entry.get("seed")
            if "seed_tag" in entry:
                row["seed_tag"] = entry["seed_tag"]
            rows.append(row)
        by_source: dict[str, list] = {}
        for row in rows:
            by_source.setdefault(row["source"], []).append(row)
        source_rows = []
        for sid in sorted(by_source):
            rs = by_source[sid]
            vals = [r["triage_index"] for r in rs]
            source_rows.append({
                "source": sid,
                "category": rs[0].get("category"),
                "n": len(rs),
                "mean_triage": round(sum(vals) / len(vals), 4),
                "min_triage": round(min(vals), 4),
                "max_triage": round(max(vals), 4),
                "range_triage": round(max(vals) - min(vals), 4),
                "seeds": [r.get("seed_tag", r.get("seed")) for r in rs],
                "images": [r["image"] for r in rs],
            })
        result["backends"][backend] = {
            "n": len(rows), "rows": rows, "sources": source_rows,
            "errors": bucket.get("errors") or [],
            "mean_triage": round(sum(r["triage_index"] for r in rows) / len(rows), 4)
            if rows else None,
            "mean_range_triage": round(
                sum(s["range_triage"] for s in source_rows) / len(source_rows), 4)
            if source_rows else None,
        }
        if rows:
            means[backend] = result["backends"][backend]["mean_triage"]
    for backend, meta in BACKENDS.items():
        if not meta.get("available"):
            result["missing_backends"][backend] = meta.get("reason", "不可用")
    result["ranking"] = sorted(means.items(), key=lambda kv: -kv[1])
    result["note"] = ("代理指标未校准：只用于 triage/回归，不是质量结论，不作放行闸。"
                      "分差 <0.02 视为噪声（同一后端不同源图的离散度就大于它）。"
                      "多 seed 的 range_triage 是同一 prompt 的采样噪声线，不是质量差。")
    (out / "score.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return result


# ─────────────────────────────────────────────────────────────
# ComfyUI 客户端（只读/提交，与 T6 同口径）
# ─────────────────────────────────────────────────────────────

def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url, payload, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def queue_and_wait(api: str, wf: dict, timeout: float = 900.0) -> dict:
    api = api.rstrip("/")
    t0 = time.time()
    res = post_json(api + "/prompt", {"prompt": wf, "client_id": "wash-benchmark"})
    if res.get("node_errors"):
        raise RuntimeError("服务端拒收：%s"
                           % json.dumps(res["node_errors"], ensure_ascii=False)[:800])
    pid = res.get("prompt_id")
    if not pid:
        raise RuntimeError("提交没有拿到 prompt_id：%r" % res)
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        hist = get_json("%s/history/%s" % (api, pid))
        if pid in hist:
            entry = hist[pid]
            return {"prompt_id": pid, "entry": entry,
                    "status": (entry.get("status") or {}).get("status_str"),
                    "elapsed_s": round(time.time() - t0, 1)}
    raise TimeoutError("prompt %s 超时 %.0fs" % (pid, timeout))


def collect_outputs(entry: dict, save_node: str) -> list[dict]:
    out = (entry.get("outputs") or {}).get(save_node) or {}
    return [it for it in (out.get("images") or []) if isinstance(it, dict)]


def download(api: str, item: dict, dest: Path) -> Path:
    query = urllib.parse.urlencode({"filename": item.get("filename", ""),
                                    "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(api.rstrip("/") + "/view?" + query, timeout=180) as r:
        dest.write_bytes(r.read())
    return dest


# ─────────────────────────────────────────────────────────────
# 离线自检 / 计划
# ─────────────────────────────────────────────────────────────

def check_design(sources: list[dict] | None = None) -> list[str]:
    problems = []
    if not BACKENDS:
        problems.append("没有登记任何后端")
    avail = [b for b, m in BACKENDS.items() if m.get("available")]
    if not avail:
        problems.append("没有可用后端")
    for backend in avail:
        idx = load_index()
        if BACKENDS[backend]["workflow"] not in idx["backends"]:
            problems.append("%s: workflow %r 不在模板索引里"
                            % (backend, BACKENDS[backend]["workflow"]))
    for name, meta in BACKENDS.items():
        if not meta.get("available") and not meta.get("reason"):
            problems.append("%s: 不可用但没写 reason（不许静默跳过）" % name)
    srcs = resolve_sources(sources)
    if not any(s["exists"] for s in srcs):
        problems.append("源图一张都不在：源图路径需要在本机核对")
    for s in srcs:
        if not s["exists"]:
            problems.append("源图缺失 %s：%s" % (s["id"], s["path"]))
    w, h = size_for((1920, 1080))
    if w % 16 or h % 16 or not (512 <= w <= 1536) or not (512 <= h <= 1536):
        problems.append("size_for(1920x1080) 不合法：%dx%d" % (w, h))
    if abs(sum(METRIC["weights"].values()) - 1.0) > 1e-6:
        problems.append("代理指标权重和不为 1")
    if METRIC.get("calibrated") or not METRIC.get("not_for_gating"):
        problems.append("代理指标被声明为已校准/可作闸：与事实不符")
    return problems


def plan(sources: list[dict] | None = None) -> dict:
    srcs = resolve_sources(sources)
    rows = []
    for backend, meta in BACKENDS.items():
        for src in srcs:
            rows.append({"backend": backend,
                         "available": bool(meta.get("available")),
                         "workflow": meta.get("workflow"),
                         "source": src["id"], "category": src["category"],
                         "exists": src["exists"]})
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pipeline": ["caption(reverse→IR)", "compile(IR→prompt)",
                         "generate(ComfyUI /prompt)", "score(proxy triage)"],
            "caption": dict(CAPTION),
            "sources": srcs, "backends": {k: dict(v)
                                          for k, v in BACKENDS.items()},
            "matrix": rows, "metric": METRIC,
            "planned_images": sum(1 for r in rows if r["available"] and r["exists"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T8：洗图基准（caption→生成→triage）")
    ap.add_argument("--check", action="store_true", help="离线验设计/源图/判据")
    ap.add_argument("--check-workflows", action="store_true", help="只读 /object_info 验图")
    ap.add_argument("--plan", action="store_true", help="落 plan.json")
    ap.add_argument("--stage", action="store_true",
                    help="只把源图另存进产物目录（零 GPU，核对 sha256）")
    ap.add_argument("--caption", action="store_true", help="源图 → IR（占 GPU）")
    ap.add_argument("--generate", action="store_true", help="IR → prompt → 出图（占 GPU）")
    ap.add_argument("--score", action="store_true", help="出图 → triage 打分（零 GPU）")
    ap.add_argument("--compare", action="store_true", help="生成 源图|出图 并排拼图（零 GPU）")
    ap.add_argument("--all", action="store_true", help="caption + generate + score")
    ap.add_argument("--dry", action="store_true", help="配合 --generate：只落 workflow")
    ap.add_argument("--only", action="append", default=[], help="只跑指定后端")
    ap.add_argument("--only-source", action="append", default=[],
                    help="只处理指定源图 id（配合 --caption 增量重跑）")
    ap.add_argument("--limit", type=int, default=None, help="反推只跑前 N 张源图")
    ap.add_argument("--seed", type=int, default=None, help="覆盖出图 seed（默认确定性生成）")
    ap.add_argument("--seeds", default="",
                    help="多 seed 方差线，如 \"42,43,44\"（同 prompt 重复出图）")
    ap.add_argument("--sources-file", default="",
                    help="G7 格式源图清单 {\"images\":[...]}；不给则用内建 4 张源图")
    ap.add_argument("--ir-root", default="",
                    help="配合 --stage：从既有 G7 产物复制 <id>.json 的 ir 字段（不跑反推）")
    ap.add_argument("--out", default="",
                    help="产物根目录（默认 out/wash_benchmark；本轮用 out/wash_benchmark/moss_q4_round1）")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--server", default="",
                    help="llama-server 可执行文件（默认查 MOSS_LLM_SERVER/G7_SERVER）")
    ap.add_argument("--model", default="",
                    help="反推用 GGUF（默认查 MOSS_LLM_MODEL/G7_MODEL）")
    ap.add_argument("--mmproj", default="",
                    help="视觉塔 GGUF（默认查 MOSS_LLM_MMPROJ/G7_MMPROJ）")
    ap.add_argument("--ctx", type=int, default=None, help="反推侧车 ctx（默认 16384）")
    args = ap.parse_args(argv)

    try:
        sources = load_sources_file(args.sources_file) if args.sources_file else None
        seeds = parse_seeds(args.seeds)
    except (FileNotFoundError, RuntimeError) as exc:
        print("✗ " + str(exc))
        return 2
    out = resolve_out(args.out)

    if args.check:
        problems = check_design(sources)
        for p in problems:
            print("✗ " + p)
        if not problems:
            p = plan(sources)
            print("✓ 设计通过：%d 张源图 × %d 可用后端 = %d 张计划出图；"
                  "代理指标声明为未校准、不作闸"
                  % (len(p["sources"]),
                     sum(1 for m in BACKENDS.values() if m.get("available")),
                     p["planned_images"]))
        return 1 if problems else 0

    if args.check_workflows:
        problems = check_workflows(args.api)
        for p in problems:
            print("✗ " + p)
        if not problems:
            print("✓ 可用后端的 API workflow 通过 /object_info 校验（未提交任何任务）")
        return 1 if problems else 0

    if args.plan:
        p = plan(sources)
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan.json").write_text(json.dumps(p, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print("已写 %s：源图 %d 张（可用 %d），计划出图 %d 张，缺后端 %s"
              % (_rel(out / "plan.json"), len(p["sources"]),
                 sum(1 for s in p["sources"] if s["exists"]), p["planned_images"],
                 "、".join(k for k, m in BACKENDS.items() if not m.get("available"))))
        return 0

    if args.stage:
        if args.ir_root:
            doc = stage_ir_sources(sources, args.ir_root, out)
            ok = sum(1 for s in doc["sources"] if s.get("ir"))
            skipped = doc.get("skipped") or []
            print("已从 %s 复制 IR %d 张到 %s/captions；跳过 %d 张（不判）"
                  % (_rel(Path(args.ir_root)), ok, _rel(out), len(skipped)))
            for row in skipped:
                print("  - %-10s %s" % (row.get("id"), row.get("reason")))
            return 0 if ok else 1
        doc = stage_sources(sources, out)
        ok = sum(1 for s in doc["sources"] if s["staged"])
        print("已另存 %d/%d 张源图到 %s/sources；清单 %s/sources.json"
              % (ok, len(doc["sources"]), _rel(out), _rel(out)))
        return 0 if ok == len(doc["sources"]) else 1

    rc = 0
    if args.caption or args.all:
        try:
            conf = resolve_caption_conf(server=args.server, model=args.model,
                                        mmproj=args.mmproj, ctx=args.ctx)
        except RuntimeError as exc:
            print("✗ " + str(exc))
            return 2
        srcs = [s for s in (sources or SOURCES)
                if not args.only_source or s["id"] in args.only_source]
        m = caption_sources(sources=srcs, out=out, conf=conf, limit=args.limit)
        print("caption 完成：%d 张（源图已另存到 sources/；本批 %s）"
              % (len(m["sources"]),
                 "、".join(s["id"] for s in srcs) if args.only_source else "全部"))
    if args.generate or args.all:
        run = generate(sources=sources, out=out, api=args.api, only=args.only,
                       dry=args.dry, timeout=args.timeout, seed=args.seed, seeds=seeds)
        ok = sum(len(b.get("images") or []) for b in run["backends"].values())
        bad = sum(len(b.get("errors") or []) for b in run["backends"].values())
        print("generate 完成：%d 张成功 / %d 条失败（dry=%s，seeds=%s，跳过 %d 个后端）"
              % (ok, bad, args.dry,
                 ",".join(str(s) for s in seeds) if seeds else "单 seed",
                 len(run["skipped"])))
        if not run["backends"] and not run["skipped"]:
            rc = 1
        if bad:
            rc = 1
    if args.score or args.all:
        sc = score_run(sources=sources, out=out)
        print("score 完成：%d 个后端有分；缺失 %d 个后端"
              % (sum(1 for b in sc["backends"].values() if b["n"]),
                 len(sc["missing_backends"])))
        for name, mean in sc["ranking"]:
            b = sc["backends"][name]
            print("  %-16s mean_triage=%.4f  mean_range=%.4f  n=%d"
                  % (name, mean, b.get("mean_range_triage") or 0.0, b["n"]))
        print("  ⚠ 代理指标未校准，只 triage、不作放行闸")
    if args.compare:
        made = make_compares(sources=sources, out=out)
        print("已生成 %d 张并排拼图：%s/images/<backend>/*.compare.png" % (len(made), _rel(out)))
        return 0 if made else 1

    if not any((args.caption, args.generate, args.score, args.all)):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
