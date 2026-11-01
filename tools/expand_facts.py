# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""W2 · 扩写事实保持（60 图 × 2 臂，moss-image，CPU 零 GPU）。

回答一个问题：**扩写会不会编事实**。本工具的诚实边界先说在前面：

- 本机没有可用的扩写端点（任务禁起 llama-server、禁 GPU），所以 LLM 用
  **确定性重排桩**（`StubLLM`：只把句子顺序倒过来，不新增/不删句子）。
  因此下面的 lost/extra 数字测的是「管线 + 对账器」的**机械保持下限**，
  **不是真实模型的事实保持率**——真模型数字未验证（需要端点或 GPU）。
- 只测 `moss-image` 目标方言；只走 **prose** 路径（`use_layout=False`：
  G7 的 reverse IR 带放置信息，默认会切 Layout JSON、根本不经过 LLM）。
- 两组开关：`all` = 三步全开（completion/translation/rewrite）；
  `rewrite` = 只改写。每单元两组的 prompt 都从**确定性草稿**起步。

指标口径：
- `lost` / `extra` 用仓库现有 `moss_llm.smith.roundtrip.diff`（与 `expand.verify`
  同一套 fixed_items 口径）；`lost=0` 才算事实保持。
- 分母 = 该臂 `ok=true` 且**跑成功**的单元数；`ok=false` 与跑失败的单列、不进分母。
- 同时记录 prompt 字符数/启发式 token 数/词数，以及 moss-image `standard` 带
  （150–300 词）的 under/over 标记；超长/欠长不判失败，只标记。

跑法：
    python tools/expand_facts.py
    python tools/expand_facts.py --groups all,rewrite --limit 10
产物（out/ 不入库）：
    out/expand_facts/results.json   逐单元（两组各一条）
    out/expand_facts/summary.json   按臂/组汇总 + lost 事实类计数 + extra 明细
    out/expand_facts/failures.json  ok=false -> skipped；运行异常 -> failed
同输入同代码 → `results.json` 逐字节相同（不含时间戳；测试钉住）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moss_llm.smith.adapters import get_adapter      # noqa: E402
from moss_llm.smith.expand import length_band        # noqa: E402
from moss_llm.smith.ir import IR                     # noqa: E402
from moss_llm.smith.registry import Registry         # noqa: E402
from moss_llm.smith.roundtrip import diff            # noqa: E402
from moss_llm.smith.steps import STEP_ORDER          # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter   # noqa: E402

TARGET = "moss-image"
ARMS = ("A-4B", "B-9B")
GROUPS = {"all": tuple(STEP_ORDER), "rewrite": ("rewrite",)}
TOOL_VERSION = "w2-expand-facts/1"
DEFAULT_JSON_ROOT = ROOT / "out" / "g7" / "round2" / "json60"
DEFAULT_OUT = ROOT / "out" / "expand_facts"

_CURRENT = "CURRENT DESCRIPTION:\n"


def _extract_current(user: str) -> str:
    """从 step user prompt 里取 CURRENT DESCRIPTION 正文。

    completion 的 user 在正文后还会跟 `Aim for N-M words` / `Return ...`；
    必须切掉，否则桩会把「150-300 words」当正文抄回去（被 step 判据当成编造数字）。
    """
    user = user or ""
    i = user.find(_CURRENT)
    if i < 0:
        return user.strip()
    rest = user[i + len(_CURRENT):]
    rest = re.split(r"\n\s*\n|\nAim for |\nReturn |\nPREFERRED ORDER:", rest, maxsplit=1)[0]
    return rest.strip()


def _reorder_sentences(text: str) -> str:
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]
    return " ".join(reversed(sents)) if sents else ""


class StubLLM:
    """确定性重排桩：只倒句子顺序，不新增/不删句子。**不是真模型**。"""

    name = "stub-deterministic-reorder"

    def __init__(self) -> None:
        self.calls: list = []

    def chat(self, system, user, temperature=0.4, max_tokens=1200):
        self.calls.append({"system_head": (system or "").splitlines()[0][:80] if system else "",
                           "user_chars": len(user or "")})
        return _reorder_sentences(_extract_current(user))


class _DropStub(StubLLM):
    """测试用：只回最后一句（模拟丢事实的模型）。"""

    def chat(self, system, user, temperature=0.4, max_tokens=1200):
        self.calls.append({})
        text = _extract_current(user)
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        return sents[-1] if sents else ""


class _InventStub(StubLLM):
    """测试用：在正文后加一句 IR 里没有的引号文字（模拟编事实）。"""

    def chat(self, system, user, temperature=0.4, max_tokens=1200):
        self.calls.append({})
        return (_extract_current(user) + ' A sign reads "INVENTED-TEXT".').strip()


class RealLLM:
    """真实 llama-server 文本端点（仅 `--llm real` 时启用；默认仍是桩）。

    用途：等 GPU 空闲后做 **spot-check**（真实 A-4B）用，不参与全量默认跑。
    模型：`Qwen3.5-4B-QUASAR-Q4_0.gguf` + `mmproj-F16.gguf`（G7 的 A 臂）；
    `--ngl` 由参数给（默认 auto，交给 llama-server 的 --fit 自适应）。
    """

    name = "real-llama-server"

    def __init__(self, model, *, exe="", mmproj="", ctx=8192, ngl="auto", port=8097,
                 startup_timeout=600.0, lora="", lora_scale=None, seed=None):
        if not model:
            raise SystemExit("--llm real 需要 --model <基座 gguf>（可再给 --mmproj）")
        self.model = str(model)
        self.exe, self.mmproj = str(exe or ""), str(mmproj or "")
        self.ctx, self.ngl, self.port = int(ctx), str(ngl), int(port)
        self.startup_timeout = float(startup_timeout)
        self.lora, self.lora_scale = str(lora or ""), lora_scale
        self.seed = seed
        self.calls: list = []
        self._srv = None

    def start(self):
        from moss_llm.server import LlamaServer
        self._srv = LlamaServer(
            self.model, exe=(self.exe or None), port=self.port, ctx=self.ctx,
            ngl=self.ngl, mmproj=self.mmproj, timeout=self.startup_timeout,
            lora=self.lora, lora_scale=self.lora_scale)
        self._srv.start()
        return self

    def chat(self, system, user, temperature=0.4, max_tokens=1200):
        self.calls.append({"system_head": (system or "")[:80], "user_chars": len(user or "")})
        return self._srv.chat(system, user, temperature=temperature, max_tokens=max_tokens,
                              seed=self.seed)

    def stop(self):
        if self._srv is not None:
            self._srv.stop()
            self._srv = None


def make_llm(args) -> tuple:
    """按参数返回 (模式名, factory, 需要 close 的实例或 None)。默认走桩，零网络。"""
    mode = getattr(args, "llm", "stub")
    if mode == "stub":
        return "stub", StubLLM, None
    if mode == "real":
        model = getattr(args, "model", "") or ""
        if not model:
            raise SystemExit("--llm real 需要 --model <基座 gguf>（可再给 --mmproj）")
        llm = RealLLM(model, exe=getattr(args, "server", "") or "",
                      mmproj=getattr(args, "mmproj", "") or "",
                      ctx=getattr(args, "ctx", 8192) or 8192,
                      ngl=getattr(args, "ngl", "auto") or "auto",
                      port=getattr(args, "port", 8097) or 8097,
                      startup_timeout=getattr(args, "startup_timeout", 600.0) or 600.0,
                      lora=getattr(args, "lora", "") or "",
                      lora_scale=getattr(args, "lora_scale", None),
                      seed=getattr(args, "seed", None)).start()
        return "real", (lambda: llm), llm
    raise SystemExit("未知 --llm %r（可用：stub / real）" % (mode,))


def load_units(json_root: Path) -> list:
    """读 60 图两臂的单元；返回按 (arm, image_id) 排序的 dict 列表。"""
    units = []
    for arm in ARMS:
        for path in sorted((json_root / arm).glob("img*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            units.append({
                "arm": arm,
                "image_id": path.stem,
                "unit": "%s/%s" % (arm, path.stem),
                "path": path,
                "ok": bool(data.get("ok")),
                "ir_dict": data.get("ir"),
                "schema": data.get("schema", ""),
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    return units


def run_unit(unit: dict, group: str, registry: Registry, *, llm_factory=StubLLM,
             target: str = TARGET, include_prompt: bool = False,
             fixed_guard: bool = False, temperature: float = 0.4) -> dict:
    """跑一个单元的一组开关；返回可直接 JSON 化的记录（不写盘）。

    `include_prompt=True` 时额外返回完整 prompt——只给 review 导出的临时重跑用；
    默认产物里只留 sha/head，避免 results.json 变成半本语料。
    `fixed_guard=True` 打开 adapter 固定项护栏：丢固定项时回退确定性草稿。
    """
    base = {"arm": unit["arm"], "image_id": unit["image_id"], "unit": unit["unit"],
            "group": group, "source_sha256": unit["source_sha256"]}
    if not unit["ok"]:
        return dict(base, status="skipped", reason="ok=false（失败单元不进分母）")
    try:
        ir = IR.from_dict(unit["ir_dict"])
        ad = get_adapter(target, registry, counter=HeuristicCounter())
        ad.length = "standard"
        ad.gen_candidates = 1          # 抽样/回炉不参与事实保持；一次就够
        ad.gen_retries = 0
        ad.gen_temperature = float(temperature)
        ad.expand_steps = GROUPS[group]
        ad.expand_fixed_guard = bool(fixed_guard)
        llm = llm_factory()
        ad.llm = llm
        out = ad.realize(ir, mode="llm", use_layout=False)
        text = out.positive or ""
        d = diff(ir, text, target=target)
        band = length_band(registry, target, ad.length)
        words = d.words
        expand = dict(out.expand or {})
        guard = expand.get("fixed_guard") if isinstance(expand.get("fixed_guard"), dict) else {}
        return dict(base, status="ok",
                    words=words, chars=len(text), tokens=ad._count(text),
                    band=list(band) if band else None,
                    under_band=bool(band and words < band[0]),
                    over_band=bool(band and words > band[1]),
                    lost=[[k, v] for k, v in d.lost],
                    extra=[[k, v] for k, v in d.extra],
                    lost_n=len(d.lost), extra_n=len(d.extra),
                    lost0=not d.lost,
                    applicable=bool(d.applicable),
                    guard_fallback=bool(guard.get("fallback")),
                    pre_guard_lost=list(guard.get("pre_guard_misses") or []),
                    guard_reason=str(guard.get("reason") or ""),
                    prompt_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    prompt_head=text[:120],
                    **({"prompt": text} if include_prompt else {}),
                    llm=llm.name,
                    llm_calls=len(llm.calls),
                    expand_stage=expand.get("stage", ""),
                    expand_warnings=[w for w in (out.warnings or []) if "扩写" in w][:4])
    except Exception as exc:
        return dict(base, status="failed", error="%s: %s" % (type(exc).__name__, exc))


def _group_summary(rows: list) -> dict:
    ok = [r for r in rows if r.get("status") == "ok"]
    skipped = [r for r in rows if r.get("status") == "skipped"]
    failed = [r for r in rows if r.get("status") == "failed"]
    denom = len(ok)
    lost_kinds = Counter(k for r in ok for k, _ in r["lost"])
    extra_kinds = Counter(k for r in ok for k, _ in r["extra"])
    pre_guard_kinds = Counter(
        (re.match(r"^\[([^\]]+)\]", str(m)) or [None, "?"])[1]
        for r in ok for m in (r.get("pre_guard_lost") or []))
    return {
        "units_total": len(rows),
        "skipped_ok_false": len(skipped),
        "failed_run": len(failed),
        "denominator_ok": denom,
        "failed_units": [r["unit"] for r in failed],
        "skipped_units": [r["unit"] for r in skipped],
        "lost0": sum(1 for r in ok if r["lost0"]),
        "lost0_ratio": round(sum(1 for r in ok if r["lost0"]) / denom, 4) if denom else None,
        "lost_total": sum(r["lost_n"] for r in ok),
        "extra_total": sum(r["extra_n"] for r in ok),
        "guard_fallback": sum(1 for r in ok if r.get("guard_fallback")),
        "guard_fallback_ratio": round(sum(1 for r in ok if r.get("guard_fallback")) / denom, 4) if denom else None,
        "pre_guard_lost_total": sum(len(r.get("pre_guard_lost") or []) for r in ok),
        "pre_guard_lost_kinds": dict(sorted(pre_guard_kinds.items(), key=lambda kv: (-kv[1], kv[0]))),
        "fallback_units": [r["unit"] for r in ok if r.get("guard_fallback")],
        "lost_kinds": dict(sorted(lost_kinds.items(), key=lambda kv: (-kv[1], kv[0]))),
        "extra_kinds": dict(sorted(extra_kinds.items(), key=lambda kv: (-kv[1], kv[0]))),
        "under_band": sum(1 for r in ok if r["under_band"]),
        "over_band": sum(1 for r in ok if r["over_band"]),
        "words_mean": round(sum(r["words"] for r in ok) / denom, 1) if denom else None,
        "tokens_mean": round(sum(r["tokens"] for r in ok) / denom, 1) if denom else None,
        "chars_mean": round(sum(r["chars"] for r in ok) / denom, 1) if denom else None,
    }


def summarize(results: list, groups=tuple(GROUPS), llm_label: str | None = None) -> dict:
    """按组 + 按臂汇总；再给组内两臂合并数。"""
    out = {"target": TARGET, "llm": llm_label or (StubLLM.name + "（桩，不是真模型）"),
           "tool": TOOL_VERSION, "groups": {}}
    for group in groups:
        rows = [r for r in results if r.get("group") == group]
        per_arm = {}
        for arm in ARMS:
            arm_rows = [r for r in rows if r["arm"] == arm]
            if arm_rows:
                per_arm[arm] = _group_summary(arm_rows)
        overall = _group_summary(rows)
        extras = [{"unit": r["unit"], "kind": k, "value": v}
                  for r in rows if r.get("status") == "ok"
                  for k, v in r["extra"]][:200]
        lost_units = [{"unit": r["unit"], "lost": r["lost"]}
                      for r in rows if r.get("status") == "ok" and r["lost_n"]]
        out["groups"][group] = {"overall": overall, "per_arm": per_arm,
                                "extra_details": extras, "lost_details": lost_units}
    return out


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(s).lower())


def _find_ir_refs(ir_dict, value, limit: int = 3) -> list:
    """在源 IR 里找该值可能出自哪个字段（给人工看，不当作结论）。"""
    refs, target = [], _norm(value)[:64]

    def walk(node, path):
        if len(refs) >= limit:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [str(k)])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [str(i)])
        elif isinstance(node, str):
            n = _norm(node)
            if target and len(n) >= 4 and (target in n or (len(n) >= 8 and n in target)):
                refs.append({"path": ".".join(path), "snippet": node[:220]})

    walk(ir_dict, [])
    return refs


def _prompt_fragment(prompt: str, value, width: int = 90) -> str:
    """在扩写 prompt 里找该值（或其第一个可区分词）的上下文。"""
    prompt, v = prompt or "", str(value)
    i = prompt.find(v)
    if i < 0:
        for tok in (t for t in re.split(r"[\s,;，；、]+", v) if len(t) >= 4):
            i = prompt.find(tok)
            if i >= 0:
                break
    if i < 0:
        return ""
    a = max(0, i - width // 2)
    return prompt[a:i + len(v) + width // 2].replace("\n", " ⏎ ")


def build_review(summary: dict, units: list, registry: Registry, out_dir: Path,
                 llm_factory, *, limit: int = 20, extra_group: str = "all") -> list:
    """导出人工可审清单（review.md）：extra/lost 各 ≤limit 条。

    每条给：单元 id、事实类、源 IR 字段原文、扩写 prompt 对应片段、归因猜测。
    归因只是**打标签帮人看**，不是结论——真盲区要人审 IR 对照。
    """
    unit_map = {u["unit"]: u for u in units}
    prompt_cache: dict = {}

    def prompt_for(unit_id: str, group: str) -> str:
        key = (unit_id, group)
        if key not in prompt_cache:
            rec = run_unit(unit_map[unit_id], group, registry,
                           llm_factory=llm_factory, include_prompt=True)
            prompt_cache[key] = rec.get("prompt", "")
        return prompt_cache[key]

    extras = (summary["groups"][extra_group]["extra_details"][:limit]
              if extra_group in summary["groups"] else [])
    lost = []
    for group in summary["groups"]:
        for d in summary["groups"][group]["lost_details"]:
            for kind, value in d["lost"]:
                lost.append({"group": group, "unit": d["unit"], "kind": kind,
                             "value": value})
    lost = lost[:limit]

    events = []

    def event(kind: str, e: dict) -> None:
        group = e.get("group", extra_group)
        fact_kind = e.get("kind", "")
        prompt = prompt_for(e["unit"], group)
        refs = _find_ir_refs(unit_map[e["unit"]]["ir_dict"], e["value"])
        frag = _prompt_fragment(prompt, e["value"])
        if kind == "extra":
            guess = ("对账器口径：IR 里有这条（text_content/描述字段），"
                     "prompt 回解析没对上（多行/分行/整块差异）" if refs else
                     "疑似渲染/对账器插入：IR 里也找不到（桩不增句，待人工）")
        else:
            guess = ("对账器口径：prompt 里还能找到该值，diff 却判 lost（匹配口径差异）"
                     if frag else "扩写/渲染丢失：prompt 里没有该值（待人工确认）")
        events.append(dict(e, event_kind=kind, kind=fact_kind, group=group,
                           prompt=prompt, refs=refs, fragment=frag, guess=guess))

    for e in extras:
        event("extra", dict(e, group=extra_group))
    for e in lost:
        event("lost", e)

    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# W2 扩写事实保持 · 人工可审清单",
        "",
        "> 生成器：`tools/expand_facts.py --review`。LLM = 确定性重排桩（不是真模型）；",
        "> 本清单只帮人看「extra/lost 是 IR/对账器口径问题，还是真的扩写丢/编事实」。",
        "> 归因列是**启发式标签**，不是结论；请对照 IR 原文与 prompt 片段人工判定。",
        "",
        "| 小节 | 条数 |",
        "|---|---|",
        "| extra（组=%s，前 %d 条） | %d |" % (extra_group, limit, len(extras)),
        "| lost（两组，前 %d 条） | %d |" % (limit, len(lost)),
        "",
    ]
    for title, kind in (("extra（IR 里没有 / 对账器没对上）", "extra"),
                        ("lost（源 IR 有、扩写后没对上）", "lost")):
        rows = [e for e in events if e["event_kind"] == kind]
        lines += ["## %s" % title, ""]
        if not rows:
            lines += ["（无）", ""]
            continue
        for i, e in enumerate(rows, 1):
            lines += [
                "### %d. `%s` · %s · %s" % (i, e["unit"], e.get("event_kind", ""),
                                            e.get("kind", "")),
                "- 事实类：`%s`；值：`%s`" % (e.get("kind", ""), str(e["value"]).replace("\n", " ⏎ ")[:300]),
                "- 源 IR 字段：%s" % (
                    "；".join("`%s` = `%s`" % (r["path"], r["snippet"].replace("\n", " ⏎ "))
                             for r in e["refs"]) or "**IR 里找不到匹配字段**"),
                "- prompt 片段：%s" % ("`%s`" % e["fragment"] if e["fragment"]
                                       else "**prompt 里找不到该值**"),
                "- 归因猜测：%s" % e["guess"],
                "",
            ]
    text = "\n".join(lines)
    (out_dir / "review.md").write_text(text + "\n", encoding="utf-8")
    return events


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="W2 扩写事实保持（moss-image；默认 CPU 桩，--llm real 才起 llama-server）")
    ap.add_argument("--json-root", default=str(DEFAULT_JSON_ROOT), help="60 图 json 根（含 A-4B/B-9B）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="产物目录（默认 out/expand_facts）")
    ap.add_argument("--groups", default="all,rewrite", help="逗号分隔：all / rewrite")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个单元（调试用，0=全量）")
    ap.add_argument("--llm", choices=["stub", "real"], default="stub",
                    help="stub（默认，确定性重排，零网络）/ real（真实 llama-server，仅 spot-check）")
    ap.add_argument("--model", default="", help="--llm real 时的基座 gguf（A-4B）")
    ap.add_argument("--mmproj", default="", help="--llm real 时的 mmproj（可选）")
    ap.add_argument("--server", default="", help="llama-server 可执行文件（可选）")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--ngl", default="auto", help="--ngl auto 交给 llama-server 的 --fit 自适应")
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--lora", default="")
    ap.add_argument("--startup-timeout", type=float, default=600.0)
    ap.add_argument("--fixed-guard", action="store_true",
                    help="打开 adpater 固定项护栏：模型候选丢固定项时回退确定性草稿（默认 off）")
    ap.add_argument("--seed", type=int, default=None,
                    help="采样 seed；默认 None=不传（llama-server 随机），显式给值才可复现")
    ap.add_argument("--temperature", type=float, default=None,
                    help="采样温度；默认沿用 adapter 默认值 0.4（不改变历史行为）")
    ap.add_argument("--review", action="store_true", help="额外导出人工可审 review.md（会重跑涉及单元取 prompt）")
    ap.add_argument("--review-limit", type=int, default=20)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    groups = [g.strip() for g in (args.groups or "").split(",") if g.strip()]
    bad = [g for g in groups if g not in GROUPS]
    if bad:
        print("未知组 %r；可用：%s" % (bad, ", ".join(GROUPS)), file=sys.stderr)
        return 2
    json_root = Path(args.json_root)
    if not json_root.is_dir():
        print("找不到 json 根：%s" % json_root, file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    reg = Registry.load()
    units = load_units(json_root)
    if args.limit:
        units = units[:args.limit]
    mode, factory, closer = make_llm(args)
    label = (StubLLM.name + "（桩，不是真模型）" if mode == "stub"
             else RealLLM.name + "（真实模型 spot-check，非默认结论）")
    temperature = 0.4 if args.temperature is None else float(args.temperature)
    results = []
    try:
        for group in groups:
            for unit in units:
                results.append(run_unit(unit, group, reg, llm_factory=factory,
                                        fixed_guard=bool(args.fixed_guard),
                                        temperature=temperature))
    finally:
        if closer is not None:
            closer.stop()
    results.sort(key=lambda r: (r["group"], r["arm"], r["image_id"]))
    summary = summarize(results, tuple(groups), llm_label=label)
    summary["fixed_guard"] = bool(args.fixed_guard)
    summary["seed"] = args.seed
    summary["temperature"] = temperature
    failures = [r for r in results if r.get("status") in ("failed", "skipped")]
    payload = {
        "schema": "expand-facts/1",
        "tool": TOOL_VERSION,
        "target": TARGET,
        "llm": label,
        "fixed_guard": bool(args.fixed_guard),
        "seed": args.seed,
        "temperature": temperature,
        "groups": groups,
        "inputs": {u["unit"]: u["source_sha256"] for u in units},
        "results": results,
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.review:
        build_review(summary, units, reg, out_dir, factory, limit=args.review_limit)

    if not args.quiet:
        print("目标 %s｜LLM %s｜单元 %d × 组 %d" % (TARGET, label, len(units), len(groups)))
        for group in groups:
            o = summary["groups"][group]["overall"]
            print("[%s] 可判 %d｜lost=0 %d（%s）｜失败 %d｜跳过 %d｜欠长 %d｜超长 %d"
                  % (group, o["denominator_ok"], o["lost0"], o["lost0_ratio"],
                     o["failed_run"], o["skipped_ok_false"], o["under_band"], o["over_band"]))
        if args.review:
            print("人工可审清单：%s" % (out_dir / "review.md"))
        print("产物：%s" % out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
