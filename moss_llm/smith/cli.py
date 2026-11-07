# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""命令行入口。

  python -m prompt_artisan targets
  python -m prompt_artisan compile examples/poster.json --target moss-image
  python -m prompt_artisan compile examples/poster.json --target qwen-image-2_1 --mode llm --model <gguf>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__
from . import paths as PATHS
from . import zh as ZH
from .adapters import ADAPTERS, get_adapter
from .ir import IR
from .lint import check
from .llm import LlamaServer
from .registry import Registry
from .tokens import HeuristicCounter


def _load_registry(path: str | None) -> Registry:
    return Registry.load(path)


def cmd_targets(args) -> int:
    reg = _load_registry(args.registry)
    print("%-18s %-8s %-10s %-8s %-6s %-6s %s"
          % ("target", "实现", "编码器", "形态", "负向", "布局", "编辑"))
    for name in reg.names():
        caps = reg.get(name)
        form = (caps.get("prompt") or {}).get("mode") or "prose"
        print("%-18s %-8s %-10s %-8s %-6s %-6s %s" % (
            name,
            "原生" if name in ADAPTERS else "通用",
            (reg.encoder(name).get("name") or "?")[:18],
            form,
            "有" if (caps.get("negative") or {}).get("supported") else "无",
            "有" if (caps.get("layout") or {}).get("supported") else "无",
            "有" if (caps.get("edit") or {}).get("supported") else "无",
        ))
    print("\n原生 = 有专属方言实现；通用 = 按 registry 声明渲染（散文/tag + 负向 + 预算）")
    return 0


def _run(args):
    reg = _load_registry(args.registry)
    ir = IR.load(args.ir)
    # 「--lang zh 与 --mode llm 互斥」必须在**起侧车之前**判：这条校验放在 try 里
    # server.start() 之后时，侧车不可达的用户先看到「连不上」，真正的参数错误被
    # 盖在下面（交叉复核 2026-11-01 用 stub 复现：started=['start','stop'] 才抛错）。
    use_zh, lang_note = ZH.resolve_language(getattr(args, "lang", None),
                                            reg, args.target)
    if use_zh and args.mode == "llm":
        raise SystemExit(
            "--lang zh 目前只支持确定性路径（--mode deterministic）：\n"
            "  扩写模式由英文扩写提示词驱动，中文整句重排没有对应的扩写实现。\n"
            "  去掉 --mode llm，或去掉 --lang zh。")
    counter = HeuristicCounter()
    server = None
    try:
        if args.mode == "llm":
            if not args.model and not args.base_url:
                raise SystemExit("--mode llm 需要 --model <gguf 路径> 或 --base-url <已跑着的端点>")
            server = LlamaServer(args.model, exe=args.server, port=args.port,
                                 ctx=args.ctx, ngl=args.ngl, timeout=args.startup_timeout,
                                 base_url=args.base_url)
            server.start()
            for n in server.notes:
                print("[server] " + n, file=sys.stderr)
            from .tokens import ServerCounter
            counter = ServerCounter(server.v1)
        adapter = get_adapter(args.target, reg, counter=counter, llm=server)
        adapter.length = getattr(args, "length", "standard")
        if getattr(args, "candidates", None):
            adapter.gen_candidates = int(args.candidates)
        if use_zh:
            out = ZH.realize_zh(adapter, ir, profile=args.profile)
            if getattr(out, "zh_applied", True):
                out.lang = ZH.LANG_ZH
        else:
            out = adapter.realize(ir, profile=args.profile, mode=args.mode)
        if lang_note:
            out.warnings.append(lang_note)
        issues = check(ir, args.target, reg.get(args.target), out.as_dict())
        return out, issues, server
    finally:
        if server is not None and not args.keep_server:
            server.stop()


def cmd_compile(args) -> int:
    out, issues, _ = _run(args)
    payload = {
        "target": out.target,
        "positive": out.positive,
        "negative": out.negative,
        "layout": ({k: v for k, v in out.layout.items() if not k.startswith("_")}
                   if out.layout else None),
        "params": out.params,
        "token_count": out.token_count,
        "token_limit": out.token_limit,
        "mode": out.mode,
        "warnings": out.warnings,
        "expand": out.expand,
        "issues": [i.__dict__ for i in issues],
    }
    if getattr(out, "lang", ""):
        payload["lang"] = out.lang
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写入 %s" % args.out, file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("── positive (%d token%s) ──" % (out.token_count,
              "/%s" % out.token_limit if out.token_limit else ""))
        print(out.positive)
        if out.negative:
            print("\n── negative ──\n" + out.negative)
        if out.params:
            print("\n── params ──\n" + json.dumps(out.params, ensure_ascii=False))
        for w in out.warnings:
            print("[warn] " + w, file=sys.stderr)
        for i in issues:
            print(str(i), file=sys.stderr)
    return 1 if any(i.level == "error" for i in issues) else 0


def cmd_lint(args) -> int:
    args.json = False
    out, issues, _ = _run(args)
    errors = [i for i in issues if i.level == "error"]
    for i in issues:
        print(str(i))
    print("\n%d error / %d warn" % (len(errors), len(issues) - len(errors)))
    return 1 if errors else 0


def _server_from(args) -> LlamaServer:
    """两种模式共用：给 --base-url 就连外部端点，否则自己拉进程。"""
    if not args.model and not args.base_url:
        raise SystemExit(
            "需要二选一：\n"
            "  --base-url http://127.0.0.1:8080   （连一个已经跑着的 OpenAI 兼容端点）\n"
            "  --model <多模态 gguf> --mmproj <mmproj.gguf>   （本地拉起）")
    if not args.base_url and not args.mmproj:
        raise SystemExit(
            "--mmproj 缺失：没有它模型看不见图。\n"
            "  本地拉起：--mmproj <mmproj-*.gguf>\n"
            "  连外部端点：改用 --base-url，并确认那边加载的是 VL 模型。")
    return LlamaServer(args.model, exe=args.server, port=args.port,
                       ctx=args.ctx, ngl=args.ngl, mmproj=args.mmproj,
                       timeout=args.startup_timeout, base_url=args.base_url,
                       vision_tokens=getattr(args, "vision_tokens", 0),
                       model_id=getattr(args, "model_id", ""))


def _emit_metadata(args, emb) -> int:
    """零模型洗图：直接把图里内嵌的原 prompt 交出去。"""
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.image).stem
    payload = {"image": str(args.image), "source": "embedded", "embedded": emb.to_dict()}
    (outdir / ("%s.embedded.json" % stem)).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("── 内嵌元数据（%s，逐字精确，未经模型改写）──" % emb.dialect)
    print(emb.positive)
    if emb.negative:
        print("\n[negative] " + emb.negative)
    if emb.params:
        print("\n[params] " + json.dumps(emb.params, ensure_ascii=False))
    print("\n已写入 %s" % outdir, file=sys.stderr)
    return 0


def cmd_verify(args) -> int:
    """扩写体检：IR 对得上这段 prompt 吗。零模型、零网络，可验任何来源的 prompt。"""
    from .expand import verify

    reg = _load_registry(args.registry)
    ir = IR.load(args.ir)
    text = Path(args.prompt).read_text(encoding="utf-8")
    rep = verify(ir, text, args.target, reg, length=args.length)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("目标 %s · %d 词 · %d token · 固定项 %d/%d 命中 · 密度 %.2f 条/100token"
              % (args.target, rep.words, rep.tokens, len(rep.hits), len(rep.fixed),
                 rep.density))
        if rep.band:
            print("长度带 %d–%d 词（%s）" % (rep.band[0], rep.band[1], args.length))
        for i in rep.errors:
            print("  [error] " + i)
        for i in rep.warns:
            print("  [warn ] " + i)
        print("判定：%s" % rep.verdict)
    return 1 if rep.verdict == "fail" else 0


def cmd_meta(args) -> int:
    """只看元数据，不做任何推断。"""
    from . import embedded as EMB

    emb = EMB.read(args.image)
    if emb is None:
        print("没有内嵌生成参数（不是 A1111/ComfyUI/InvokeAI/NovelAI 写的图，或元数据被平台剥掉了）",
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(emb.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print("方言 %s" % emb.dialect)
    print("── positive ──\n%s" % emb.positive)
    if emb.negative:
        print("\n── negative ──\n%s" % emb.negative)
    if emb.params:
        print("\n── params ──\n" + json.dumps(emb.params, ensure_ascii=False, indent=2))
    for n in emb.notes:
        print("[note] " + n, file=sys.stderr)
    return 0


def cmd_reverse(args) -> int:
    """图片 → IR → 各后端 prompt。可带审计闭环。"""
    from . import embedded as EMB
    from .audit import reverse_with_audit
    from .reverse import reverse

    reg = _load_registry(args.registry)
    targets = [t.strip() for t in args.target.split(",") if t.strip()]
    for t in targets:
        if t not in reg.names():
            raise SystemExit("未知后端 %r，可选：%s" % (t, ", ".join(reg.names())))

    # 先看有没有内嵌元数据：有的话是逐字精确的原 prompt，比反推准，
    # 而且这条路不需要模型、不需要 GPU。
    emb = EMB.read(args.image)
    if emb is not None:
        print("[meta] 图内嵌生成参数：%s" % emb.summary(), file=sys.stderr)
        for n in emb.notes:
            print("[meta] " + n, file=sys.stderr)

    if getattr(args, "from_metadata", False):
        if emb is None or not emb.usable:
            raise SystemExit(
                "这张图没有可用的内嵌 prompt（只认 A1111/ComfyUI/InvokeAI/NovelAI 写的元数据）。\n"
                "  去掉 --from-metadata 改走 VLM 反推：--base-url <端点> 或 --model + --mmproj")
        return _emit_metadata(args, emb)

    server = _server_from(args)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.image).stem
    seen = 0
    try:
        server.start()
        for n in server.notes:
            print("[server] " + n, file=sys.stderr)
        seen = len(server.notes)

        counter = HeuristicCounter()
        first = get_adapter(targets[0], reg, counter=counter, llm=server)

        if args.rounds > 0:
            ir, report, history = reverse_with_audit(
                args.image, server,
                compile_fn=lambda i: first.realize(i, mode="deterministic").positive,
                rounds=args.rounds, target=args.min_score,
                schema=not args.no_schema, text_pass=not args.no_text_pass,
                max_side=args.max_side)
        else:
            ir, report = reverse(args.image, server, schema=not args.no_schema,
                                 text_pass=not args.no_text_pass,
                                 max_side=args.max_side)
            history = []
    finally:
        if not args.keep_server:
            server.stop()

    # 请求期间产生的备注（截断、降级、思考没关）必须当场回显，
    # 否则用户只会看到「JSON 没有闭合」，不知道是 token 不够还是模型抽风。
    for n in server.notes[seen:]:
        print("[server] " + n, file=sys.stderr)

    ir_path = outdir / ("%s.ir.json" % stem)
    # 内嵌元数据写进 provenance：它是**证据**，不是我们编的，
    # 后面谁想核对「模型是不是把 sampler 猜错了」有据可查。
    if emb is not None:
        ir.provenance["embedded"] = {
            "dialect": emb.dialect, "summary": emb.summary(),
            "positive": emb.positive[:4000], "negative": emb.negative[:2000],
            "params": emb.params,
        }
    ir_path.write_text(json.dumps(ir.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    results = []
    for t in targets:
        ad = get_adapter(t, reg, counter=HeuristicCounter())
        out = ad.realize(ir, mode="deterministic")
        issues = check(ir, t, reg.get(t), out.as_dict())
        (outdir / ("%s__%s.txt" % (stem, t))).write_text(out.positive, encoding="utf-8")
        results.append({"target": t, "positive": out.positive,
                        "negative": out.negative, "params": out.params,
                        "tokens": out.token_count, "limit": out.token_limit,
                        "warnings": out.warnings,
                        "issues": [i.__dict__ for i in issues]})

    payload = {"image": str(args.image), "ir": ir.to_dict(),
               "embedded": emb.to_dict() if emb else None,
               "report": report.to_dict(), "audit": history, "targets": results}
    (outdir / ("%s.report.json" % stem)).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("── 取证 ── %s  %s  实体 %d" % (Path(args.image).name,
          "%dx%d" % report.size if report.size else "尺寸未知", report.entity_count))
    for w in report.warnings:
        print("[warn] " + w, file=sys.stderr)
    for a in report.age_gate:
        print("[age ] " + a, file=sys.stderr)
    for h in history:
        print("[audit] 第%d轮 分 %d (%s)  漏%d 错%d" % (
            h["round"], h["score"], h["verdict"],
            len(h["missing"]), len(h["wrong"])))
        for c in h.get("changes", []):
            print("        " + c)
    for r in results:
        print("\n── %s（%d token%s）──" % (r["target"], r["tokens"],
              "/%s" % r["limit"] if r["limit"] else ""))
        print(r["positive"])
        if r["negative"]:
            print("\n[negative] " + r["negative"])
        for w in r["warnings"]:
            print("[warn] " + w, file=sys.stderr)
        for i in r["issues"]:
            print("[%s] %s %s" % (i["level"], i["code"], i["message"]), file=sys.stderr)
    print("\n已写入 %s" % outdir, file=sys.stderr)
    return 0


def _text_server(args) -> LlamaServer:
    """文本模式的模型服务：**不需要 mmproj**，模型也不必是多模态的。"""
    if not args.base_url and not args.model:
        raise SystemExit(
            "需要二选一：\n"
            "  --base-url http://127.0.0.1:8080   （连一个已经跑着的 OpenAI 兼容端点）\n"
            "  --model <gguf>                     （本地拉起；读 prompt 不需要 mmproj）")
    # 显式 `--server` 必须同时把 source 标成 custom：定位逻辑刻意规定
    # 「explicit 只在 custom 下生效」（不跟 auto 抢），只传 exe 会被静默忽略。
    return LlamaServer(args.model, exe=args.server, port=args.port, ctx=args.ctx,
                       ngl=args.ngl, timeout=args.startup_timeout,
                       base_url=args.base_url, model_id=getattr(args, "model_id", ""),
                       lora=getattr(args, "lora", "") or "",
                       lora_scale=getattr(args, "lora_scale", None),
                       source="custom" if (args.server or "").strip() else "auto")


def cmd_decompose(args) -> int:
    """文字 prompt → IR → 各后端 prompt。读进来再写出去，一条命令走完 ④⑤。"""
    from . import embedded as EMB
    from .decompose import DecomposeError, decompose
    from .expand import verify
    from .roundtrip import roundtrip

    reg = _load_registry(args.registry)
    targets = [t.strip() for t in args.target.split(",") if t.strip()]
    for t in targets:
        if t not in reg.names():
            raise SystemExit("未知后端 %r，可选：%s" % (t, ", ".join(reg.names())))
    if args.mode == "llm" and not (args.base_url or args.model):
        raise SystemExit(
            "--mode llm 要一个模型端点：\n"
            "  --base-url http://127.0.0.1:8080   或   --model <gguf> --server <llama-server.exe>\n"
            "  只想读 prompt 不要扩写：去掉 --mode llm（默认就是确定性）")

    if args.image:
        emb = EMB.read(args.image)
        if emb is None or not emb.usable:
            raise SystemExit("这张图没有内嵌 prompt（只认 A1111/ComfyUI/InvokeAI/NovelAI 写的元数据）")
        text, negative, params, source = emb.positive, emb.negative, emb.params, str(args.image)
    elif args.prompt:
        text = Path(args.prompt).read_text(encoding="utf-8")
        negative, params, source = "", None, str(args.prompt)
    else:
        raise SystemExit("给一个 prompt 文本文件，或用 --image 从图内嵌元数据取")

    llm = None
    if args.base_url or args.model:
        llm = _text_server(args)
        llm.start()
        for n in llm.notes:
            print("[server] " + n, file=sys.stderr)
    try:
        from . import h3 as H3

        if H3.looks_like(text) and args.dialect in ("auto", "h3"):
            # H3 终稿是三字段块，结构完全确定 —— 不需要模型，也不会抽错。
            # 走这里而不是走 LLM 抽取，是因为「能确定性读的，不要用概率读」。
            from .decompose import Report as _Report

            ir = IR.from_dict(H3.parse(text))
            report = _Report(source=source, method="h3-parse", chars=len(text),
                             image=source, raw_chars=len(text))
            report.facts = {"texts": [], "ages": [], "colors": [], "numbers": [],
                            "counts": [], "lens_mm": None}
        else:
            try:
                ir, report = decompose(text, llm=llm, negative=negative, params=params,
                                       source=source)
            except DecomposeError as exc:
                print("抽取失败：%s" % exc, file=sys.stderr)
                return 2

        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        stem = Path(args.image).stem if args.image else Path(args.prompt).stem
        (outdir / ("%s.ir.json" % stem)).write_text(
            json.dumps(ir.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        results, failed = [], 0
        for t in targets:
            ad = get_adapter(t, reg, counter=HeuristicCounter(), llm=llm)
            ad.length = args.length
            d, out = roundtrip(ir, ad, mode=args.mode)
            rep = verify(ir, out.positive, t, reg, length=args.length)
            failed += 0 if d.ok else 1
            (outdir / ("%s__%s.txt" % (stem, t))).write_text(out.positive, encoding="utf-8")
            results.append({"target": t, "positive": out.positive, "roundtrip": d.to_dict(),
                            "fixed_recall": round(rep.fixed_recall, 3),
                            "fixed_hits": len(rep.hits), "fixed": len(rep.fixed),
                            "verdict": rep.verdict, "warnings": out.warnings})
    finally:
        if llm is not None and not args.keep_server:
            llm.stop()

    payload = {"source": source, "ir": ir.to_dict(), "report": report.to_dict(),
               "targets": results}
    (outdir / ("%s.report.json" % stem)).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    f = report.facts
    print("── 抽取 ── %s  %s 模式 · %d 字 · 实体 %d → 比例 %s"
          % (Path(source).name, report.method, report.chars, len(ir.entities),
             ir.canvas.get("aspect_ratio", "?")))
    print("[facts] 引号 %d · 年龄 %d · 颜色 %d · 数字 %d · 数量 %d"
          % (len(f.get("texts") or []), len(f.get("ages") or []),
             len(f.get("colors") or []), len(f.get("numbers") or []),
             len(f.get("counts") or [])))
    if report.coverage is None:
        print("[覆盖] 不适用（中文源不做分词）")
    else:
        print("[覆盖] 源内容词 %.0f%% 进了 IR%s"
              % (report.coverage * 100,
                 "" if report.coverage >= 0.99 else
                 "，没进：" + "、".join(report.missing[:8])))
    for s in report.injected:
        print("[补] " + s)
    for s in report.dropped_facts:
        print("[撤] " + s + "（没有渲染路径）")
    for w in report.warnings:
        print("[warn] " + w, file=sys.stderr)
    for r in results:
        print("\n── %s（%d 词）──" % (r["target"], len(r["positive"].split())))
        print(r["positive"])
        for ln in _roundtrip_lines(r["roundtrip"]):
            print("  " + ln)
        print("  固定项 %d/%d · 判定 %s"
              % (r["fixed_hits"], r["fixed"], r["verdict"]))
        for w in r["warnings"]:
            print("  [warn] " + w, file=sys.stderr)
    print("\n已写入 %s" % outdir, file=sys.stderr)
    return 1 if failed else 0


def cmd_h3_check(args) -> int:
    """H3 终稿的机检闸。零模型，只覆盖可机检的子集。"""
    from . import h3

    text = Path(args.prompt).read_text(encoding="utf-8")
    ir = IR.load(args.ir) if args.ir else None
    issues = h3.check(text, ir, music_policy=args.music)
    if args.json:
        print(json.dumps({"verdict": h3.verdict(issues),
                          "issues": [i.__dict__ for i in issues]},
                         ensure_ascii=False, indent=2))
    else:
        print("── %s ── %s" % (Path(args.prompt).name, h3.verdict(issues)))
        print(h3.report(issues))
        if ir is None:
            print("\n[提示] 没给 --ir，三线（首个切点/末切点/间隔）没有判。"
                  "补 --ir 才能查「铺满时长窗口」那三条。")
    return 1 if h3.verdict(issues) == "fail" else 0


def cmd_h3_parse(args) -> int:
    """H3 终稿 → IR。确定性，不用模型。"""
    from . import h3

    text = Path(args.prompt).read_text(encoding="utf-8")
    if not h3.looks_like(text):
        print("这段文本不像 H3 三字段终稿（至少要认出两个字段名）。"
              "用 h3 check 可以直接看它哪里不像。", file=sys.stderr)
        return 2
    ir = IR.from_dict(h3.parse(text))
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / (Path(args.prompt).stem + ".ir.json")
    dest.write_text(json.dumps(ir.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    if args.json:
        print(json.dumps(ir.to_dict(), ensure_ascii=False, indent=2))
        return 0
    tl = ir.timeline or {}
    print("── %s ── 模式 %s · %d 镜 · %.2fs"
          % (Path(args.prompt).name, tl.get("mode"), len(tl.get("shots") or []),
             float(tl.get("duration_s") or 0)))
    for s in tl.get("shots") or []:
        ts = h3.format_ts(s["t_start"]) if s.get("t_start") is not None else "—"
        cam = (s.get("camera") or {}).get("move") or "—"
        print("  [Shot %d] %-10s 运镜 %-14s 台词 %d · 文字 %d"
              % (s.get("index"), ts, cam, len(s.get("dialogue") or []),
                 len(s.get("onscreen_text") or [])))
    print("\n已写入 %s" % dest, file=sys.stderr)
    return 0


def cmd_h3_rewrite(args) -> int:
    """短提示词 → H3 终稿。挂了 Rewriter LoRA 就是官方那条路径。"""
    from . import h3

    text = Path(args.prompt).read_text(encoding="utf-8").strip() if Path(
        args.prompt).exists() else args.prompt.strip()
    if args.model and not Path(args.model).exists():
        print("找不到基座权重：%s" % args.model, file=sys.stderr)
        print("先跑 `python -m prompt_artisan h3 doctor` 看缺什么。", file=sys.stderr)
        return 2

    reg = _load_registry(None)
    ir = IR.from_dict({
        "task": "t2va",
        "canvas": {"aspect_ratio": args.resolution},
        "timeline": {"mode": "t2va", "duration_s": args.duration,
                     "shots": [{"index": 1, "action": text}],
                     "music": []},
        "entities": [],
    })
    ad = get_adapter("minimax-h3", reg, counter=HeuristicCounter())
    if not (args.base_url or args.model):
        out = ad.realize(ir)
        print(out.positive)
        for w in out.warnings:
            print("[warn] " + w, file=sys.stderr)
        print("\n[提示] 没给模型端点，上面是按 IR 确定性渲染的结果（1 镜）。"
              "要真改写请给 --model/--base-url，并可挂 --lora。"
              "确定性路径不会自己编声景与配乐，所以那两项是 N/A。", file=sys.stderr)
        return 1
    ad.llm = _text_server(args)
    ad.llm.start()
    for n in ad.llm.notes:
        print("[server] " + n, file=sys.stderr)
    try:
        out = ad.realize(ir, mode="llm")
    finally:
        if not args.keep_server:
            ad.llm.stop()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / (Path(args.prompt).stem + ".h3.txt"
                     if Path(args.prompt).exists() else "rewritten.h3.txt")
    dest.write_text(out.positive, encoding="utf-8")
    # 必须带 ir：时间戳三条（HEAD/TAIL/GAP）要靠 ir.duration_s 才判得了。
    # 不带 ir 时这些规则静默跳过，同一份稿子 adapter 报 error、这里报 pass。
    issues = h3.check(out.positive, ir)
    if args.json:
        print(json.dumps({"prompt": out.positive, "verdict": h3.verdict(issues),
                          "issues": [i.__dict__ for i in issues]},
                         ensure_ascii=False, indent=2))
    else:
        print(out.positive)
        print("\n── 机检 ── %s" % h3.verdict(issues))
        print(h3.report(issues))
        for w in out.warnings:
            print("[warn] " + w, file=sys.stderr)
        print("\n已写入 %s" % dest, file=sys.stderr)
    return 0 if h3.verdict(issues) != "fail" else 1


def _name_matches(filename: str, model_name: str) -> bool:
    """文件名是不是 `model_name` 那一版（而不是同架构的另一版）。

    `Qwen3-VL-8B-Instruct` vs 同架构、同尺寸的另一套 8B 权重：架构、尺寸都一样，
    **权重不是一套**。
    判据取模型名里的每个版本号片段，要求全部出现在文件名里。不硬编码任何具体
    版本号——那是模型知识，写进代码就成了「只有作者盘上那份能用」。
    """
    if not model_name:
        return False
    name = (filename or "").lower()
    tags = re.findall(r"\d+(?:\.\d+)?b?|\d+\.\d+", model_name.lower())
    if not tags:
        return model_name.lower() in name
    return all(tag in name for tag in tags)


def cmd_h3_doctor(args) -> int:
    """清点 H3 需要的三样东西：适配器、基座、服务端。**缺什么说什么**。"""
    import struct

    reg = _load_registry(None)
    caps = reg.get("minimax-h3")
    rw = caps.get("rewriter") or {}

    def gguf_meta(path: str) -> dict:
        """读 GGUF 头里的 kv。零依赖——只为报三个字段，不值得引一个库。"""
        want = {"general.architecture", "general.name", "general.base_model.0.name",
                "general.type", "adapter.lora.alpha", "adapter.type"}
        tmap = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
                8: "s", 9: "arr", 10: "Q", 11: "q", 12: "d"}
        out: dict = {}
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return out
            f.read(4)
            _n_t = struct.unpack("<Q", f.read(8))[0]
            n_kv = struct.unpack("<Q", f.read(8))[0]

            def rs() -> str:
                n = struct.unpack("<Q", f.read(8))[0]
                return f.read(n).decode("utf-8", "replace")

            def rv(kind: int):
                if kind == 8:
                    return rs()
                if kind == 9:
                    et = struct.unpack("<I", f.read(4))[0]
                    n = struct.unpack("<Q", f.read(8))[0]
                    return [rv(et) for _ in range(n)]
                fmt = "<" + tmap[kind]
                return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]

            for _ in range(n_kv):
                k = rs()
                try:
                    v = rv(struct.unpack("<I", f.read(4))[0])
                except (KeyError, struct.error):
                    break
                if k in want:
                    out[k] = v
        return out

    rows = []

    def row(item: str, path: str, ok: bool, detail: str) -> None:
        rows.append({"item": item, "path": path, "ok": ok, "detail": detail})

    # 适配器位置：--lora → registry 记的文件名 → 在各模型目录里按文件名找。
    lora = args.lora or ""
    if not lora:
        fname = rw.get("file") or ""
        found = PATHS.find_files(fname, min_size=1 << 20, exclude=()) if fname else []
        lora = str(found[0]) if found else ""
    if not lora or not Path(lora).exists():
        row("Rewriter LoRA", lora or rw.get("file") or "（未找到）", False,
            "从 %s 下载后放进模型目录，或用 --lora 指过去"
            % (rw.get("repo") or "?"))
    else:
        m = gguf_meta(lora)
        row("Rewriter LoRA", lora, True,
            "arch=%s alpha=%s base=%s"
            % (m.get("general.architecture"), m.get("adapter.lora.alpha"),
               m.get("general.base_model.0.name")))

    # 基座 = 适配器训练时用的那个模型。位置由用户决定，这里只在约定目录里找，
    # 找不到就明说没有（`paths.model_dirs()` 写清了去哪找、怎么覆盖）。
    wanted = str(rw.get("base_model") or "")
    token = wanted.rsplit("/", 1)[-1]              # `Qwen/Qwen3-VL-8B-Instruct` → `Qwen3-VL-8B-Instruct`
    base = args.base or ""
    if not base and token:
        exact = PATHS.find_files(token)
        if not exact:
            # 官方 GGUF 文件名常把模型 id 的标点去掉（`Qwen3-VL-8B-Instruct` →
            # `Qwen3VL-8B-Instruct-Q4_K_M.gguf`）。按「只留字母数字」再比一次，
            # 否则正确基座会被当成「同架构的别的权重」报红。
            norm = re.sub(r"[^a-z0-9]", "", token.lower())
            if norm:
                exact = [c for c in PATHS.find_files(None)
                         if norm in re.sub(r"[^a-z0-9]", "", c.stem.lower())]
        base = str(exact[0]) if exact else ""
    # 全部候选单独取一份：要报的坑是「同架构同尺寸、但版本不是这一版」，
    # 拿被名字筛过的列表去比，永远比不出来（自己把自己筛掉了）。
    cands = PATHS.find_files(None)
    if not base or not Path(base).exists():
        # 🔴 这一条是本模块最重要的输出：同架构 ≠ 同权重。
        # 27B 契约时期实测：盘上的 Qwen3.8-27B 与基座 Qwen3.6-27B 的 ssm_alpha /
        # attn_qkv / attn_gate 等张量全部同名；llama.cpp 的 LoRA 加载器**不会报错**——
        # 它会安静地把 A 训出来的增量加到 B 的权重上，然后输出一段语法正确、语义无效的提示词。
        # 这类静默失败正是这个项目存在的理由，所以这里必须点名，而不是只说「没找到」。
        # 只报「同架构 **且** 同参数量」的：尺寸不同的模型连张量形状都对不上，
        # llama.cpp 会直接报错，那反而是安全的。危险的是同尺寸不同版本。
        size_tag = re.sub(r"^.*?(\d+(?:\.\d+)?B).*$", r"\1", wanted or "")
        same_arch = []
        for c in cands:
            m = gguf_meta(str(c))
            if m.get("general.architecture") != rw.get("arch"):
                continue
            if size_tag and size_tag not in c.name:
                continue
            same_arch.append(c.name)
        detail = ("适配器是在 %s 上训的。盘上没有同款基座。" % (wanted or "?"))
        if same_arch:
            detail += ("\n       同架构的其他基座 %d 个（%s）——**llama.cpp 会把适配器"
                       "加载成功而不报错**，但那是另一套权重，输出无意义。不要用。"
                       % (len(same_arch), "、".join(same_arch[:3])))
        row("基座权重", base or "（未找到）", False, detail)
    else:
        m2 = gguf_meta(base)
        same = _name_matches(Path(base).name, token)
        row("基座权重", base, same,
            "arch=%s；%s" % (m2.get("general.architecture"),
                            "与适配器同款" if same else
                            "**不是适配器训练时用的那一版**（%s）" % (wanted or "?")))

    exe = args.server or PATHS.find_llama_server()
    has_lora_flag = PATHS.supports_flag(exe, "--lora")
    row("llama-server", exe or "（未找到）", bool(exe) and has_lora_flag,
        "支持 --lora" if has_lora_flag else
        ("未找到可执行文件" if not exe else "这个版本没有 --lora"))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print("[%s] %-16s %s" % ("OK  " if r["ok"] else "MISS", r["item"], r["path"]))
            print("       " + r["detail"])
        missing = [r["item"] for r in rows if not r["ok"]]
        print()
        if missing:
            print("缺 %d 项：%s" % (len(missing), "、".join(missing)))
            print("缺基座时仍然可用：`h3 rewrite --base-url <任意 OpenAI 兼容端点>` "
                  "走同一套官方模板，只是没有 LoRA 的改写质量。")
            print(PATHS.hint() + "。")
        else:
            print("三样齐全：可以 `h3 rewrite --model <基座> --lora <适配器>`")
    return 0 if all(r["ok"] for r in rows) else 1


def _roundtrip_lines(d: dict) -> list:
    if not d.get("applicable", True):
        return ["不适用：" + (d["notes"][0] if d.get("notes") else "")]
    if d.get("ok") and not d.get("extra"):
        return ["往返一致：零丢失零多余"]
    lines = ["丢失 [%s] %s" % (k, v) for k, v in d.get("lost") or []]
    lines += ["多出 [%s] %s（IR 里没有）" % (k, v) for k, v in d.get("extra") or []]
    return lines


def cmd_roundtrip(args) -> int:
    """只做往返校验：IR → 各后端 prompt → 事实差（零模型）。"""
    from .roundtrip import roundtrip

    reg = _load_registry(args.registry)
    ir = IR.load(args.ir)
    targets = [t.strip() for t in args.target.split(",") if t.strip()]
    for t in targets:
        if t not in reg.names():
            raise SystemExit("未知后端 %r，可选：%s" % (t, ", ".join(reg.names())))

    payload, failed = {}, 0
    for t in targets:
        ad = get_adapter(t, reg, counter=HeuristicCounter())
        ad.length = args.length
        try:
            d, out = roundtrip(ir, ad, mode=args.mode)
        except ValueError as exc:
            # 能力不匹配：不是「丢失」，是这套核对不适用于这次组合（同 invariants）。
            print("── %s · 不适用：%s" % (t, exc))
            payload[t] = {"applicable": False, "reason": str(exc)}
            continue
        failed += 0 if d.ok else 1
        payload[t] = {"diff": d.to_dict(), "positive": out.positive}
        if args.json:
            continue
        print("── %s · %d 词" % (t, len(out.positive.split())))
        for ln in d.lines():
            print("   " + ln)
        for n in d.notes:
            print("   [note] " + n)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def cmd_audit(args) -> int:
    """只审计：给图和已写好的 prompt，问模型对不对得上。"""
    from .audit import audit

    server = _server_from(args)
    prompt = Path(args.prompt).read_text(encoding="utf-8")
    try:
        server.start()
        result = audit(args.image, prompt, server, schema=not args.no_schema)
    finally:
        if not args.keep_server:
            server.stop()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.degraded:
        print("\n判定：degraded —— 模型没做审计就交卷了（空结果不算通过）。"
              "\n原始响应：%s" % result.raw, file=sys.stderr)
        return 2
    print("\n判定：%s" % result.verdict)
    return 0 if result.verdict == "pass" else 1


def cmd_invariants(args) -> int:
    """不变式体检：不需要 ground truth 的一层验证（零模型、零网络）。"""
    from . import invariants as INV

    reg = _load_registry(args.registry)
    ir = IR.load(args.ir)
    targets = [t.strip() for t in args.target.split(",") if t.strip()]
    for t in targets:
        if t not in reg.names():
            raise SystemExit("未知后端 %r，可选：%s" % (t, ", ".join(reg.names())))

    payload, failed = {}, 0
    for t in targets:
        ad = get_adapter(t, reg, counter=HeuristicCounter())
        try:
            results = INV.run(ad, ir, only=tuple(args.only.split(",")) if args.only else None)
        except ValueError as exc:
            # 能力不匹配（例如拿 edit 的 IR 跑没编辑通道的后端）不是不变式失败，
            # 是这次核对不适用。报 5 条 FAIL 只会教人忽略这个命令。
            if not args.json:
                print("── %s · 不适用：%s" % (t, exc))
            payload[t] = {"applicable": False, "reason": str(exc)}
            continue
        ok, line = INV.summarize(results)
        failed += 0 if ok else 1
        payload[t] = [r.to_dict() for r in results]
        if args.json:
            continue
        print("── %s · %s" % (t, line))
        for r in results:
            print("   " + str(r))
            for ev in r.evidence:
                print("      " + ev)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def cmd_setup(args) -> int:
    """装一个 llama-server 运行时（可选，不装也能用 --base-url 连已有服务）。

    实现用父包的 `moss_llm.runtime_install`：那边有镜像链、GitHub 摘要校验、
    断点续传、原子替换与 GPU/CUDA 能力探测。本层以前自带过一份更弱的（无摘要、
    无镜像、先解压到一半就可能砸掉可用运行时），合流时删掉了。
    """
    from .. import runtime_install as RI

    print("目标后端 %s（auto = 按本机探测）" % args.backend, file=sys.stderr)
    try:
        exe, tag, note = RI.install(force=bool(getattr(args, "force", False)),
                                    backend="" if args.backend == "auto" else args.backend,
                                    tag=args.tag)
    except Exception as exc:
        print("安装失败：%s" % exc, file=sys.stderr)
        return 1
    print(exe)
    print("\n已装 %s（%s）" % (tag or "未取到 tag", note), file=sys.stderr)
    print("用法：--server \"%s\"" % exe, file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m moss_llm.smith",
        description="moss_llm.smith v%s —— 语义 IR 与多方言确定性编译" % __version__)
    p.add_argument("--registry", help="model-registry.yaml 路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("targets", help="列出所有后端与能力").set_defaults(func=cmd_targets)

    s = sub.add_parser("setup", help="下载一个 llama-server 运行时（约 35MB）")
    s.add_argument("--backend", default="auto",
                   choices=["auto", "cpu", "vulkan", "cuda", "rocm", "sycl", "metal"],
                   help="auto = 按本机厂商探测（N 卡 cuda 并卡驱动上限 / AMD 与 Intel vulkan / 无卡 cpu）；cuda 包会额外拖一个同版本的 cudart")
    s.add_argument("--tag", default="", help="指定 b<build> 标签，默认取最新")
    s.set_defaults(func=cmd_setup)

    def common(sp):
        sp.add_argument("ir", help="IR 文件（.json / .yaml）")
        sp.add_argument("--target", required=True)
        sp.add_argument("--mode", choices=["deterministic", "llm"], default="deterministic")
        sp.add_argument("--profile", help="采样档，如 text_dense / anime_painterly")
        sp.add_argument("--length", choices=["brief", "standard", "detailed"],
                        default="standard",
                        help="篇幅档。长度是方言参数：FLUX 3 要短、Krea 2 要长，带子在 registry")
        sp.add_argument("--lang", choices=["en", "zh"], default=None,
                        help="提示词语言。不填 = 按 registry 的 prompt.language；"
                             "zh = 中文整句重排（主体→动作→场景→镜头→光），"
                             "只影响确定性路径，编辑/Layout JSON/时间线体裁原样输出")
        sp.add_argument("--candidates", type=int, default=0,
                        help="扩写采样数（llm 模式）。0 = 用适配器默认值（4）；1 = 关掉搜索")
        sp.add_argument("--model", help="llm 模式下的 GGUF 路径")
        sp.add_argument("--base-url", default="",
                        help="连一个已跑着的 OpenAI 兼容端点，如 http://127.0.0.1:8080")
        sp.add_argument("--model-id", default="",
                        help="请求体里的 model 名（Ollama/LM Studio 必填；不填会自动探测）")
        sp.add_argument("--server", help="llama-server 可执行文件路径")
        sp.add_argument("--port", type=int, default=8091)
        sp.add_argument("--ctx", type=int, default=8192)
        sp.add_argument("--ngl", default="99")
        sp.add_argument("--startup-timeout", type=float, default=240.0)
        sp.add_argument("--keep-server", action="store_true")
        sp.add_argument("--lora", default="",
                        help="GGUF 格式的 LoRA 适配器（llama-server --lora）")
        sp.add_argument("--lora-scale", type=float, default=None,
                        help="适配器的 scale（默认不传，用文件里的 alpha 推算）")

    c = sub.add_parser("compile", help="编译 IR 到目标 prompt")
    common(c)
    c.add_argument("--out", help="把结果写成 JSON")
    c.add_argument("--json", action="store_true", help="直接打印 JSON")
    c.set_defaults(func=cmd_compile)

    lp = sub.add_parser("lint", help="只跑静态检查")
    common(lp)
    lp.set_defaults(func=cmd_lint)

    def vision_args(sp):
        sp.add_argument("image", help="输入图片")
        sp.add_argument("--model", default="", help="多模态 GGUF（连外部端点时可省）")
        sp.add_argument("--mmproj", default="", help="mmproj-*.gguf（不加载就看不见图）")
        sp.add_argument("--base-url", default="",
                        help="连一个已跑着的 OpenAI 兼容端点，如 http://127.0.0.1:8080")
        sp.add_argument("--model-id", default="",
                        help="请求体里的 model 名（Ollama/LM Studio 必填；不填会自动探测）")
        sp.add_argument("--server", help="llama-server 可执行文件路径")
        sp.add_argument("--port", type=int, default=8092)
        sp.add_argument("--ctx", type=int, default=16384)
        sp.add_argument("--ngl", default="99")
        sp.add_argument("--vision-tokens", type=int, default=1024,
                        help="视觉 token 上限，防一张图吃掉上下文")
        sp.add_argument("--max-side", type=int, default=1536,
                        help="送进 VLM 前的最长边；小模型可降到 1024 省 token")
        sp.add_argument("--no-schema", action="store_true",
                        help="关掉结构化输出约束（服务端不认 response_format 时用）")
        sp.add_argument("--no-text-pass", action="store_true",
                        help="关掉文字专线（第二遍只转录文字，UI/海报/漫画建议开）")
        sp.add_argument("--startup-timeout", type=float, default=240.0)
        sp.add_argument("--keep-server", action="store_true")
        sp.add_argument("--lora", default="",
                        help="GGUF 格式的 LoRA 适配器（llama-server --lora）")
        sp.add_argument("--lora-scale", type=float, default=None,
                        help="适配器的 scale（默认不传，用文件里的 alpha 推算）")

    r = sub.add_parser("reverse", help="图片 → IR → 各后端 prompt（洗图）")
    vision_args(r)
    r.add_argument("--target", default="moss-image,qwen-image-2_1",
                   help="逗号分隔，可多选")
    r.add_argument("--rounds", type=int, default=0,
                   help="审计闭环轮数，0 = 不审（默认 0，因为每轮多一次带图调用）")
    r.add_argument("--min-score", type=int, default=85)
    r.add_argument("--from-metadata", action="store_true",
                   help="只读图内嵌的生成参数，不起服务（A1111/ComfyUI/InvokeAI/NovelAI 图适用）")
    r.add_argument("--outdir", default="out/reverse")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_reverse)

    m = sub.add_parser("meta", help="只看图内嵌的生成参数（零模型、零网络）")
    m.add_argument("image", help="输入图片")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_meta)

    v = sub.add_parser("verify", help="扩写体检：IR 对得上这段 prompt 吗（零模型）")
    v.add_argument("ir", help="IR 文件（.json / .yaml）")
    v.add_argument("--prompt", required=True, help="待体检的 prompt 文本文件")
    v.add_argument("--target", default="moss-image", help="按哪个方言的长度带判")
    v.add_argument("--length", choices=["brief", "standard", "detailed"],
                   default="standard")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)

    i = sub.add_parser("invariants", help="不变式体检：5 条 metamorphic 关系（零模型）")
    i.add_argument("ir", help="IR 文件（.json / .yaml）")
    i.add_argument("--target", default="moss-image,qwen-image-2_1,krea2,flux3,sdxl",
                   help="逗号分隔，可多选")
    i.add_argument("--only", default="", help="只跑某几条，如 M2,M4")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_invariants)

    a = sub.add_parser("audit", help="图 + prompt 的忠实度审计")
    vision_args(a)
    a.add_argument("--prompt", required=True, help="待审的 prompt 文本文件")
    a.set_defaults(func=cmd_audit)

    def text_args(sp):
        """文本模式的模型参数：不给就不起服务，走字面量归位。"""
        sp.add_argument("--model", default="", help="gguf 路径（读 prompt 不需要 mmproj）")
        sp.add_argument("--base-url", default="",
                        help="连一个已跑着的 OpenAI 兼容端点，如 http://127.0.0.1:8080")
        sp.add_argument("--model-id", default="", help="请求体里的 model 名")
        sp.add_argument("--server", help="llama-server 可执行文件路径")
        sp.add_argument("--port", type=int, default=8093)
        sp.add_argument("--ctx", type=int, default=16384)
        sp.add_argument("--ngl", default="99")
        sp.add_argument("--startup-timeout", type=float, default=240.0)
        sp.add_argument("--keep-server", action="store_true")
        sp.add_argument("--lora", default="",
                        help="GGUF 格式的 LoRA 适配器（llama-server --lora）")
        sp.add_argument("--lora-scale", type=float, default=None,
                        help="适配器的 scale（默认不传，用文件里的 alpha 推算）")

    d = sub.add_parser("decompose",
                       help="文字 prompt → IR，并编译回去核对（④+⑤，零模型也能跑）")
    d.add_argument("prompt", nargs="?", default="",
                   help="prompt 文本文件；A1111 的整块 parameters 也认")
    d.add_argument("--image", default="", help="改从图内嵌元数据取 prompt（不起视觉模型）")
    d.add_argument("--target", default="moss-image,qwen-image-2_1", help="逗号分隔，可多选")
    d.add_argument("--mode", choices=["deterministic", "llm"], default="deterministic",
                   help="输出端的模式：deterministic 只编译，llm 顺带扩写（要端点）")
    d.add_argument("--length", choices=["brief", "standard", "detailed"], default="standard")
    text_args(d)
    d.add_argument("--dialect", choices=["auto", "h3", "generic"], default="auto",
                   help="auto：认出 H3 三字段就按 H3 解析；generic：一律走 LLM/字面量抽取")
    d.add_argument("--outdir", default="out/decompose")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_decompose)

    h = sub.add_parser("h3", help="MiniMax-H3 音视频提示词：解析 / 判定 / 改写")
    hs = h.add_subparsers(dest="h3cmd", required=True)

    c1 = hs.add_parser("check", help="H3 终稿的机检闸（零模型）")
    c1.add_argument("prompt", help="H3 终稿文本文件")
    c1.add_argument("--ir", default="", help="可选：配套 IR，用来查三线（时间戳铺满片长）")
    c1.add_argument("--music", choices=["any", "na"], default="any",
                    help="any（默认）：N/A 或配乐描述都合法；"
                         "na：产出端不配乐、要求严格写 N/A")
    c1.add_argument("--json", action="store_true")
    c1.set_defaults(func=cmd_h3_check)

    c2 = hs.add_parser("parse", help="H3 终稿 → IR（零模型，确定性）")
    c2.add_argument("prompt", help="H3 终稿文本文件")
    c2.add_argument("--out", default="out/h3", help="IR 输出目录")
    c2.add_argument("--json", action="store_true")
    c2.set_defaults(func=cmd_h3_parse)

    c3 = hs.add_parser("rewrite", help="短提示词 → H3 终稿（可挂 Rewriter LoRA）")
    c3.add_argument("prompt", help="原始短提示词（一句话点子也行）")
    c3.add_argument("--duration", type=int, default=8, help="4–15 秒")
    c3.add_argument("--resolution", default="16:9",
                    help="21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16")
    c3.add_argument("--out", default="out/h3", help="输出目录")
    c3.add_argument("--json", action="store_true")
    text_args(c3)
    c3.set_defaults(func=cmd_h3_rewrite)

    c4 = hs.add_parser("doctor", help="清点 H3 需要的权重/适配器/服务端，缺什么明说")
    c4.add_argument("--lora", default="", help="LoRA gguf 路径（默认去 registry 里找）")
    c4.add_argument("--base", default="", help="基座 gguf 路径（默认在模型目录里找）")
    c4.add_argument("--server", default="", help="llama-server 路径（默认找 PATH）")
    c4.add_argument("--json", action="store_true")
    c4.set_defaults(func=cmd_h3_doctor)

    rt = sub.add_parser("roundtrip", help="往返校验：IR → prompt → 事实差（零模型）")
    rt.add_argument("ir", help="IR 文件（.json / .yaml）")
    rt.add_argument("--target", default="moss-image,qwen-image-2_1,krea2,flux3,sdxl",
                    help="逗号分隔，可多选")
    rt.add_argument("--mode", choices=["deterministic", "llm"], default="deterministic")
    rt.add_argument("--length", choices=["brief", "standard", "detailed"], default="standard")
    rt.add_argument("--json", action="store_true")
    rt.set_defaults(func=cmd_roundtrip)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
