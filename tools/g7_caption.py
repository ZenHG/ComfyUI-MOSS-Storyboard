# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 gold set · 反推批次跑批（T1 第 1 步）。

对 `out/g7/images.json` 里的每张图、每个模型跑一次**同口径**反推：

  同一 SYSTEM（moss_llm/smith/reverse.py 的取证提示词）
  同一 seed（写进请求体，llama.cpp 认这个字段）
  同一 temperature / max_tokens / ctx / 视觉 token 上限
  关 thinking（LlamaServer.build_body 默认带 chat_template_kwargs.enable_thinking=False，
  服务端拒收会被记进 notes，不静默）

产物：`out/g7/json/{model}/{img_id}.json`
  caption —— IR 的确定性散文渲染（clauses.render_prose / build_clauses），
             即反推管线「图 → 结构化账本 → 一段成品提示词」里的最后一段；
  raw_response —— 模型原话（取证 JSON 与文字专线 JSON），逐字保留；
  ir / report —— 机检与复核的对象；
  timing / tokens —— 每张的耗时与 usage；
  confidence —— 本管线不产出该字段（节点 caption 通道才有），如实写 null。

刻意**不**在这里做任何判据：跑批只管取证与落盘。binding 机检在 tools/g7_binding.py，
两者分开才能保证「判据改动不重跑模型、模型重跑不重算判据」。

GPU 纪律：一个模型一个侧车，串行、跑完必停；批次开始/结束各记一次 nvidia-smi。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "g7"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moss_llm.smith.clauses import build_clauses, render_prose          # noqa: E402
from moss_llm.smith.paths import ENV_MODEL_DIRS, hint as paths_hint     # noqa: E402
from moss_llm.smith.reverse import SYSTEM, reverse                      # noqa: E402
from moss_llm.server import LlamaServer, find_server_exe                # noqa: E402

#: 两个臂。B 是 heretic 血统，只作内部对照，不进发行默认（G6 红线）。
#: 权重/服务端位置**不写死在代码里**：按文件名在模型目录里找（与仓库其它工具一致），
#: 或由环境变量显式指定。找不到时给可执行的下一步，不猜。
MODELS = {
    "A-4B": {
        "label": "A / QUASAR-4B-Q4_0",
        "model_env": "G7_MODEL_A", "model_pattern": "Qwen3.5-4B-QUASAR-Q4_0",
        "mmproj_env": "G7_MMPROJ_A", "mmproj_pattern": "mmproj-F16",
    },
    "B-9B": {
        "label": "B / heretic-9B-dhq",
        "model_env": "G7_MODEL_B", "model_pattern": "moss-te-heretic-dhq",
        "mmproj_env": "G7_MMPROJ_B", "mmproj_pattern": "Qwen3.5-9B.mmproj-Q8_0",
    },
}

#: llama-server 路径：G7_SERVER → MOSS_LLM_SERVER → LLAMA_CPP_HOME → 包内 runtime → PATH。
SERVER_ENV = "G7_SERVER"

#: 权重小于这个字节数的不算（模型动辄 GB，别把碎片/草稿当基座）。
_MIN_WEIGHT_BYTES = 1 << 26


def _locate(env_name: str, pattern: str, what: str) -> str:
    """按「显式环境变量 → 模型目录里按文件名找」定位一个 gguf。"""
    explicit = (os.environ.get(env_name) or "").strip()
    if explicit:
        if not Path(explicit).is_file():
            raise SystemExit("%s 指向的文件不存在：%s" % (env_name, explicit))
        return explicit
    from moss_llm.smith import paths as PATHS
    hits = PATHS.find_files(pattern, min_size=_MIN_WEIGHT_BYTES, exclude=())
    if len(hits) == 1:
        return str(hits[0])
    if not hits:
        raise SystemExit(
            "找不到%s（文件名里应含 %r）。三条路：\n"
            "  ① 把权重放进模型目录（%s 指向的目录，或 ~/models、models/LLM）\n"
            "  ② 设环境变量 %s=<完整路径>\n"
            "  ③ %s" % (what, pattern, ENV_MODEL_DIRS, env_name, paths_hint()))
    raise SystemExit(
        "有 %d 个文件都匹配 %r，无法替你决定用哪个：\n%s\n  用 %s=<完整路径> 指定。"
        % (len(hits), pattern, "\n".join("  - %s" % Path(h).name for h in hits[:8]),
           env_name))


def resolve_model(key: str) -> dict:
    cfg = MODELS[key]
    return dict(cfg, key=key,
                model=_locate(cfg["model_env"], cfg["model_pattern"], "模型权重"),
                mmproj=_locate(cfg["mmproj_env"], cfg["mmproj_pattern"],
                               "mmproj（没有它模型看不见图）"))


def resolve_server() -> str:
    exe = find_server_exe((os.environ.get(SERVER_ENV) or "").strip())
    if not exe:
        raise SystemExit(
            "找不到 llama-server。四条路：\n"
            "  ① 设 %s=<完整路径>\n"
            "  ② 设 MOSS_LLM_SERVER=<完整路径>\n"
            "  ③ 设 LLAMA_CPP_HOME=<llama.cpp 解压目录>\n"
            "  ④ 把 llama-server 放进 PATH" % SERVER_ENV)
    return exe

#: 同口径参数（两个模型完全相同；改任何一个都必须整轮重跑，否则不可比）。
#: max_tokens=8192 = reverse.py 的默认值：5120 实测在「军官列队」这种多实体图上
#: 被 finish_reason=length 截断（A 臂 46.9s / 5120 tokens 停在 bbox 中段），
#: 截断的 JSON 等于没有取证，所以不压这个上限。
DEFAULTS = {
    "seed": 42,
    "temperature": 0.1,
    "max_tokens": 8192,
    "ctx": 12288,               # 输入约 2.6k + 输出上限 8.2k，留 ~1.5k 余量
    "vision_tokens": 1024,      # 每张图固定 ~1024 视觉 token，模型间同口径
    "max_side": 1536,
    "text_pass": True,          # reverse() 的文字专线；两个模型都开
    "schema": True,
    "port": 8096,
}

#: 每个记录与批次摘要里记哪些设置。显式列出来，避免以后加 CLI 参数时
#: 悄悄改变记录 schema（第一轮/第二轮的 settings 必须可直接并排比较）。
SETTING_KEYS = ("ctx", "max_tokens", "max_side", "vision_tokens",
                "seed", "temperature", "text_pass", "schema")


def nvidia_used_mib() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20)
        return (out.stdout or "").strip() or ("stderr:" + (out.stderr or "").strip())
    except Exception as exc:                    # GPU 探测失败不该打断批次
        return "nvidia-smi failed: %s" % exc


class G7Server(LlamaServer):
    """只加一件事：把每次带图调用的原文、usage、耗时、finish_reason 留下来。

    父类 `chat_vision` 只返回字符串，usage 在 `_content` 里被丢掉了。G7 要报
    「tokens 与耗时」，又不想另写一套 HTTP 客户端（那会跟父类漂移），所以在这里
    覆盖两个私有钩子——**不改父类**。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls: list[dict] = []
        self._pending: dict | None = None

    def chat_vision(self, system, user, data_url, temperature=0.2,
                    max_tokens=3000, schema=None, name="scene_record"):
        t0 = time.perf_counter()
        self._pending = {"name": name, "temperature": temperature,
                         "max_tokens": max_tokens, "schema": bool(schema)}
        try:
            out = super().chat_vision(system, user, data_url, temperature=temperature,
                                      max_tokens=max_tokens, schema=schema, name=name)
        except Exception as exc:
            self._pending.update({"elapsed_s": round(time.perf_counter() - t0, 2),
                                  "content": None, "error": str(exc)[:600]})
            self.calls.append(self._pending)
            self._pending = None
            raise
        self._pending.update({"elapsed_s": round(time.perf_counter() - t0, 2),
                              "content": out})
        self.calls.append(self._pending)
        self._pending = None
        return out

    def _content(self, data):
        if self._pending is not None:
            self._pending["usage"] = data.get("usage") or {}
            self._pending["finish_reason"] = (
                (data.get("choices") or [{}])[0].get("finish_reason") or "")
        return super()._content(data)


def caption_of(ir) -> str:
    """IR → 确定性散文。和产物链一致：build_clauses 之后走 render_prose。"""
    try:
        return render_prose(build_clauses(ir))
    except Exception as exc:                    # 渲染失败不该丢整条记录
        return "[caption 渲染失败] %r" % exc


def run_one(server: G7Server, img: dict, cfg: dict, args) -> dict:
    """跑一张图，返回要落盘的 payload（成功/失败都返回，绝不静默跳过）。"""
    server.calls = []
    notes_before = len(server.notes)
    t0 = time.perf_counter()
    payload = {
        "schema": "g7-caption/1",
        "model_key": cfg["key"], "model_label": cfg["label"],
        "model_file": Path(cfg["model"]).name, "mmproj_file": Path(cfg["mmproj"]).name,
        "image_id": img["id"], "image_name": img["name"], "image_path": img["path"],
        "image_sha256": img["sha256"], "category": img["category"],
        "settings": {k: getattr(args, k) for k in SETTING_KEYS},
        "system_prompt": SYSTEM,
        "caption_source": "moss_llm.smith.clauses.render_prose(build_clauses(ir))",
        "confidence": None,
        "confidence_note": "reverse()/Report 不产出 confidence；节点 caption 通道才有。"
                           "本任务机器判据的对象是 IR，不是 confidence。",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        ir, report = reverse(img["path"], server,
                             temperature=args.temperature,
                             max_tokens=args.max_tokens,
                             max_side=args.max_side,
                             schema=args.schema,
                             text_pass=args.text_pass)
        payload.update({
            "ok": True,
            "ir": ir.to_dict(),
            "report": report.to_dict(),
            "caption": caption_of(ir),
        })
    except Exception as exc:
        payload.update({
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:900]),
            "traceback": traceback.format_exc()[-2500:],
            "caption": None,
        })
    elapsed = time.perf_counter() - t0
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    for c in server.calls:
        u = c.get("usage") or {}
        tokens["prompt"] += int(u.get("prompt_tokens") or 0)
        tokens["completion"] += int(u.get("completion_tokens") or 0)
        tokens["total"] += int(u.get("total_tokens") or 0)
    payload["elapsed_s"] = round(elapsed, 2)
    payload["tokens"] = tokens
    payload["calls"] = server.calls
    payload["server_notes"] = list(server.notes[notes_before:])
    return payload


def run_model(key: str, images: list[dict], args) -> dict:
    cfg = resolve_model(key)
    dest_dir = Path(args.json_root) / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.run_dir) / ("batch-%s.log" % key)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")

    def log(msg: str) -> None:
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    log("模型 %s 启动中（%s）" % (cfg["label"], Path(cfg["model"]).name))
    log("输出 JSON 根：%s" % Path(args.json_root))
    log("显存（启动前）：%s" % nvidia_used_mib())
    server = G7Server(model=cfg["model"], mmproj=cfg["mmproj"], exe=resolve_server(),
                      port=args.port, ctx=args.ctx, ngl="auto", timeout=360.0,
                      vision_tokens=args.vision_tokens, fit_on=True,
                      fit_target_mib=1024, flash_attn=True, mtp="auto",
                      draft_n_max=2, cache_type_k="q8_0", cache_type_v="q8_0",
                      ubatch=512, parallel=1)
    server.sampling["seed"] = args.seed          # 同 seed；JSON 调用里 seed 照发
    t_start = time.perf_counter()
    done = failed = skipped = 0
    try:
        server.start()
        for n in server.notes:
            log("[server] " + n)
        log("显存（加载后）：%s" % nvidia_used_mib())
        for img in images:
            dest = dest_dir / ("%s.json" % img["id"])
            if dest.exists() and not args.overwrite:
                skipped += 1
                log("%s 已存在，跳过（resume）" % img["id"])
                continue
            payload = run_one(server, img, cfg, args)
            dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                            encoding="utf-8")
            if payload["ok"]:
                done += 1
                n_ent = len((payload.get("ir") or {}).get("entities") or [])
                log("%s %-10s ok  %6.1fs  实体 %2d  tokens %5d  调用 %d"
                    % (img["id"], img["category"], payload["elapsed_s"], n_ent,
                       payload["tokens"]["total"], len(payload["calls"])))
            else:
                failed += 1
                log("%s %-10s FAIL %6.1fs  %s"
                    % (img["id"], img["category"], payload["elapsed_s"],
                       payload["error"][:160]))
    finally:
        server.stop()
        log("侧车已停；显存（停止后）：%s" % nvidia_used_mib())
        log_fh.close()
    return {"model_key": key, "label": cfg["label"], "ok": done, "failed": failed,
            "skipped": skipped, "images": len(images),
            "wall_s": round(time.perf_counter() - t_start, 1),
            "log": str(log_path)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G7 反推跑批（两模型同口径）")
    ap.add_argument("--models", default="A-4B,B-9B",
                    help="逗号分隔：%s" % ",".join(MODELS))
    ap.add_argument("--images", default=str(OUT / "images.json"),
                    help="gold set 清单（默认第一轮 out/g7/images.json；第二轮可指向 images60.json）")
    ap.add_argument("--json-root", dest="json_root", default=str(OUT / "json"),
                    help="单图 JSON 输出根目录（默认第一轮 out/g7/json；round2 指到 out/g7/round2/json）")
    ap.add_argument("--run-dir", dest="run_dir", default=str(OUT / "run"),
                    help="日志与批次摘要目录（默认 out/g7/run；round2 用独立目录，绝不覆盖第一轮）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 张（冒烟用）")
    ap.add_argument("--ids", default="", help="只跑这些 image_id，如 img01,img12")
    ap.add_argument("--overwrite", action="store_true", help="重跑已存在的记录")
    ap.add_argument("--port", type=int, default=DEFAULTS["port"])
    ap.add_argument("--ctx", type=int, default=DEFAULTS["ctx"])
    ap.add_argument("--max-tokens", dest="max_tokens", type=int,
                    default=DEFAULTS["max_tokens"])
    ap.add_argument("--max-side", dest="max_side", type=int,
                    default=DEFAULTS["max_side"])
    ap.add_argument("--vision-tokens", dest="vision_tokens", type=int,
                    default=DEFAULTS["vision_tokens"])
    ap.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    ap.add_argument("--temperature", type=float, default=DEFAULTS["temperature"])
    ap.add_argument("--no-text-pass", dest="text_pass", action="store_false",
                    default=DEFAULTS["text_pass"])
    ap.add_argument("--no-schema", dest="schema", action="store_false",
                    default=DEFAULTS["schema"])
    args = ap.parse_args(argv)

    images = json.loads(Path(args.images).read_text(encoding="utf-8"))["images"]
    wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
    if wanted:
        images = [i for i in images if i["id"] in wanted]
    if args.limit:
        images = images[:args.limit]
    if not images:
        print("没有要跑的图（--ids/--limit 过滤后为空）", file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for key in [k.strip() for k in args.models.split(",") if k.strip()]:
        if key not in MODELS:
            print("未知模型 %r，可选：%s" % (key, ", ".join(MODELS)), file=sys.stderr)
            return 2
        try:                                  # 提前解析，缺什么在这里就说清
            resolve_model(key)
        except SystemExit as exc:
            print("[%s] %s" % (key, exc), file=sys.stderr)
            return 2
        summaries.append(run_model(key, images, args))

    payload = {"generated_by": "tools/g7_caption.py",
               "images_file": str(args.images),
               "json_root": str(args.json_root),
               "run_dir": str(args.run_dir),
               "image_count": len(images),
               "settings": {k: getattr(args, k) for k in SETTING_KEYS},
               "system_prompt_sha256": hashlib.sha256(
                   SYSTEM.encode("utf-8")).hexdigest(),
               "server_exe": Path(resolve_server()).name,
               "runs": summaries,
               "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (run_dir / "batch-summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n批次完成：%s" % json.dumps(summaries, ensure_ascii=False))
    return 0 if all(s["failed"] == 0 for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
