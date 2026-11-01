# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""本地真机预检：**有环境就跑，没有就明确 SKIP（未跑），绝不混同「通过」。**

## 为什么需要它

CI 跑在无 GPU、无宿主、无浏览器的 runner 上。有些闸只能在真机跑：
`host_loader_check.py`（走宿主自己的 `load_custom_node`）、四个 `ui_*.mjs`
（无头浏览器 + 活宿主）、`e2e_matrix.py`（GPU + 权重 + 端到端出图）。
把它们塞进 CI 只剩两种坏结果：**永远红**（没人看了）或**静默跳过**（假装验过）。

所以拆成两层：
- **CI**：只跑 CPU 可确定判定的步骤（见 `docs/44`）；
- **本地预检（本脚本）**：维护者在真机上一条命令跑完「只有真机能验」的那批，
  并在缺环境时输出 `SKIP（未跑）`。发布前用 `--require-all`：缺任何一项即非 0。

## 判据边界（写在最前面，别把它当质量结论）

- PASS / SKIP / FAIL 只回答「这些脚本跑没跑、退出码对不对」；
  **SKIP 不是 PASS**：缺 host / 缺 GPU / 缺 playwright 时脚本**没跑过**。
- `--require-all` 只把 SKIP 升级成「未过」（退出码 2），不会把 SKIP 改写成 PASS。
- GPU 端到端（`e2e_matrix.py`）默认**不跑**（会真出图、占显存）：发布前显式加
  `--with-gpu`；不加时该项是 SKIP，`--require-all` 会因此判未过（这是有意的）。
- 权重不在本脚本里猜：`e2e_matrix.py` 需要的端到端工作流文件不存在时直接 SKIP，
  并把「缺什么文件」打出来。

## 用法

    python tools/ci_preflight.py                  # 有环境就跑；缺的 SKIP，退出码 0（除非有 FAIL）
    python tools/ci_preflight.py --probe-only     # 只探测环境与计划，不跑任何闸
    python tools/ci_preflight.py --require-all    # 发布前：缺环境/未启用 GPU 即退出码 2
    python tools/ci_preflight.py --require-all --with-gpu   # 发布前全量（会真出图）
    python tools/ci_preflight.py --json           # 机器可读结果

退出码：0 = 没有 FAIL（SKIP 允许）；1 = 有 FAIL；2 = `--require-all` 下仍有 SKIP。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = "PASS"
SKIP = "SKIP"
FAIL = "FAIL"

#: `ui_*.mjs` 的契约：退出码 2 = 环境缺依赖（playwright / chromium / 宿主没起），不是断言失败。
ENV_EXIT = 2

#: e2e_matrix 需要的端到端工作流（在宿主 user 目录下）。缺它 → SKIP 并说明。
E2E_WORKFLOW = os.path.join("user", "default", "workflows", "MOSS 故事板宫格.json")


# ─────────────────────────────────────────────────────────────
# 环境探测
# ─────────────────────────────────────────────────────────────

def _run(cmd, cwd=ROOT, timeout=1800):
    """执行子命令 → (exit_code, 合并后的输出)。超时也当一次失败（不静默）。"""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except FileNotFoundError as exc:
        return 127, "找不到可执行文件：%s" % exc
    except subprocess.TimeoutExpired:
        return 124, "超时（%ss）" % timeout


def detect_host(environ=None) -> tuple[str | None, str]:
    """找宿主 ComfyUI 根目录：`COMFYUI_PATH` → 仓库根 `.comfyui_path` → 标准安装推断。

    判据是「`<根>/custom_nodes` 真的存在」——只存在一个同名目录不算。
    """
    env = os.environ if environ is None else environ

    def usable(path: str) -> bool:
        return bool(path) and os.path.isdir(os.path.join(path, "custom_nodes"))

    explicit = (env.get("COMFYUI_PATH") or "").strip()
    if explicit:
        if usable(explicit):
            return explicit, ""
        return None, "COMFYUI_PATH=%r 下没有 custom_nodes（路径无效）" % explicit
    local = os.path.join(ROOT, ".comfyui_path")
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as fh:
            hit = fh.read().strip()
        if hit:
            if usable(hit):
                return hit, ""
            return None, ".comfyui_path=%r 下没有 custom_nodes（路径无效）" % hit
    inferred = os.path.abspath(os.path.join(ROOT, os.pardir, os.pardir))
    if usable(inferred):
        return inferred, ""
    return None, "缺宿主：COMFYUI_PATH / .comfyui_path / 标准安装位置都没有 custom_nodes"


def detect_gpu(which=None, runner=None) -> tuple[str | None, str]:
    """探测 NVIDIA GPU（`nvidia-smi` 可用且能列出设备）。"""
    which = which or shutil.which
    runner = runner or _run
    exe = which("nvidia-smi")
    if not exe:
        return None, "缺 GPU：PATH 里没有 nvidia-smi（无 NVIDIA 驱动或非 NVIDIA 平台）"
    rc, out = runner([exe, "--query-gpu=name", "--format=csv,noheader"], timeout=30)
    if rc != 0 or not out.strip():
        return None, "缺 GPU：nvidia-smi 运行失败（exit %s）" % rc
    return out.strip().splitlines()[0].strip(), ""


def detect_playwright(which=None, runner=None) -> tuple[str | None, str]:
    """探测 Playwright：`node` 可用，且 `tools/ui_kit.mjs` 能找到 playwright-core + chromium。"""
    which = which or shutil.which
    runner = runner or _run
    node = which("node")
    if not node:
        return None, "缺浏览器依赖：PATH 里没有 node"
    js = (
        "import('./tools/ui_kit.mjs').then(m=>{"
        "const core=!!m.findPwCore();const exe=!!m.findBrowserExe('chromium');"
        "console.log(JSON.stringify({core,exe}));"
        "}).catch(e=>console.log(JSON.stringify({error:String(e)})));"
    )
    rc, out = runner([node, "-e", js], timeout=60)
    if rc != 0:
        return None, "缺浏览器依赖：node 探测失败（exit %s）" % rc
    data = {}
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                data = {}
            break
    if data.get("core") and data.get("exe"):
        return "playwright-core + chromium", ""
    miss = []
    if not data.get("core"):
        miss.append("playwright-core")
    if not data.get("exe"):
        miss.append("chromium")
    why = "缺浏览器依赖：ui_kit 找不到 " + " / ".join(miss or ["(未知)"])
    if data.get("error"):
        why += "（%s）" % str(data["error"])[:80]
    return None, why


def detect_environment(environ=None, which=None, runner=None) -> dict:
    host, host_why = detect_host(environ)
    gpu, gpu_why = detect_gpu(which, runner)
    pw, pw_why = detect_playwright(which, runner)
    return {"host": host, "host_why": host_why,
            "gpu": gpu, "gpu_why": gpu_why,
            "playwright": pw, "playwright_why": pw_why}


# ─────────────────────────────────────────────────────────────
# 步骤计划
# ─────────────────────────────────────────────────────────────

def build_steps(env: dict, *, with_gpu: bool = False) -> list[dict]:
    """按环境与开关生成步骤。每步：{name, kind, cmd, skip}；skip 非空即不跑。"""
    host = env.get("host")
    pw = env.get("playwright")
    gpu = env.get("gpu")
    node_cmds = {
        "ui-smoke": ["node", os.path.join("tools", "ui_smoke.mjs")],
        "ui-widget-compat": ["node", os.path.join("tools", "ui_widget_compat.mjs")],
        "ui-llm-panel": ["node", os.path.join("tools", "ui_llm_panel.mjs")],
    }
    steps = [{
        "name": "host-loader",
        "kind": "host",
        "cmd": [sys.executable, os.path.join("tools", "host_loader_check.py")],
        "skip": "" if host else (env.get("host_why") or "缺宿主"),
    }]
    for name, cmd in node_cmds.items():
        why = ""
        if not host:
            why = env.get("host_why") or "缺宿主"
        elif not pw:
            why = env.get("playwright_why") or "缺浏览器依赖"
        steps.append({"name": name, "kind": "browser", "cmd": cmd, "skip": why})

    # GPU 端到端：默认不跑（会真出图）。发布前 `--with-gpu`。
    why = ""
    if not with_gpu:
        why = "未启用（发布前用 --with-gpu；会真实出图、占显存）"
    elif not gpu:
        why = env.get("gpu_why") or "缺 GPU"
    elif not host:
        why = env.get("host_why") or "缺宿主"
    else:
        wf = os.path.join(host, E2E_WORKFLOW)
        if not os.path.isfile(wf):
            why = "缺端到端工作流文件：<宿主>/%s（无权重要求时说明缺什么，不猜）" % E2E_WORKFLOW
    steps.append({
        "name": "e2e-matrix",
        "kind": "gpu",
        "cmd": [sys.executable, os.path.join("tools", "e2e_matrix.py"), "--no-doc"],
        "skip": why,
    })
    return steps


# ─────────────────────────────────────────────────────────────
# 执行与判定
# ─────────────────────────────────────────────────────────────

def _tail(text: str, n: int = 2) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return " ｜ ".join(ln.strip()[:160] for ln in lines[-n:])


def run_step(step: dict, runner=None) -> dict:
    runner = runner or _run
    if step["skip"]:
        return {"name": step["name"], "kind": step["kind"], "status": SKIP,
                "detail": step["skip"], "exit": None, "seconds": 0.0}
    t0 = time.time()
    rc, out = runner(step["cmd"])
    dt = round(time.time() - t0, 1)
    if rc == 0:
        return {"name": step["name"], "kind": step["kind"], "status": PASS,
                "detail": _tail(out) or "脚本 exit 0", "exit": 0, "seconds": dt}
    if rc == ENV_EXIT:
        # ui_*.mjs 的契约：2 是环境问题（宿主没起 / 浏览器缺），不是断言失败。
        return {"name": step["name"], "kind": step["kind"], "status": SKIP,
                "detail": "环境问题（脚本 exit 2）：" + (_tail(out) or "无输出"),
                "exit": rc, "seconds": dt}
    return {"name": step["name"], "kind": step["kind"], "status": FAIL,
            "detail": "exit %s：%s" % (rc, _tail(out)), "exit": rc, "seconds": dt}


def run_all(steps: list[dict], runner=None) -> list[dict]:
    return [run_step(s, runner) for s in steps]


def counts(results: list[dict]) -> dict:
    return {k: sum(1 for r in results if r["status"] == k) for k in (PASS, SKIP, FAIL)}


def exit_code(results: list[dict], *, require_all: bool = False) -> int:
    """0 = 没有 FAIL；1 = 有 FAIL；2 = require-all 下仍有 SKIP（缺环境/未启用）。"""
    n = counts(results)
    if n[FAIL]:
        return 1
    if require_all and n[SKIP]:
        return 2
    return 0


def format_results(results: list[dict]) -> str:
    lines = []
    for r in results:
        label = {"PASS": "PASS    ", "SKIP": "SKIP（未跑）", "FAIL": "FAIL    "}[r["status"]]
        lines.append("%s %-16s %s" % (label, r["name"], r["detail"]))
    n = counts(results)
    lines.append("")
    lines.append("通过 %d / 跳过（未跑）%d / 失败 %d" % (n[PASS], n[SKIP], n[FAIL]))
    if n[SKIP]:
        lines.append("注意：SKIP（未跑）≠ 通过；发布前请用 `--require-all`（GPU 项再加 `--with-gpu`）")
    return "\n".join(lines)


def format_env(env: dict) -> str:
    def cell(name, ok, detail):
        return "  %-12s %s%s" % (name, ok if ok else "缺", ("  " + detail) if detail and not ok else "")
    return "\n".join([
        "宿主 ComfyUI : %s" % (env["host"] or "缺（%s）" % env["host_why"]),
        "GPU          : %s" % (env["gpu"] or "缺（%s）" % env["gpu_why"]),
        "Playwright   : %s" % (env["playwright"] or "缺（%s）" % env["playwright_why"]),
    ])


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python tools/ci_preflight.py",
        description="本地真机预检：有环境就跑，没有就明确 SKIP（未跑），不混同通过。")
    p.add_argument("--require-all", action="store_true",
                   help="发布前用：缺任何环境/未启用 GPU 即退出码 2（SKIP 升级为「未过」）")
    p.add_argument("--with-gpu", action="store_true",
                   help="跑 GPU 端到端矩阵（会真实出图、占显存；缺权重/工作流则 SKIP）")
    p.add_argument("--probe-only", action="store_true",
                   help="只探测环境并列出计划，不跑任何闸")
    p.add_argument("--json", action="store_true", help="机器可读输出（stdout 只输出 JSON）")
    p.add_argument("--timeout", type=float, default=1800.0, help="单步超时秒数（默认 1800）")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    env = detect_environment()
    steps = build_steps(env, with_gpu=args.with_gpu)

    def emit(payload):
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print(payload["text"])

    if args.probe_only:
        planned = [{"name": s["name"], "kind": s["kind"],
                    "skip": s["skip"] or "将运行"} for s in steps]
        payload = {"probe": True, "env": env, "steps": planned,
                   "text": format_env(env) + "\n\n计划：\n" + "\n".join(
                       "  %-16s %s" % (s["name"], s["skip"] or "将运行") for s in steps)}
        emit(payload)
        return 0

    results = []
    for step in steps:
        if not args.json:
            print("…… %s" % step["name"], flush=True)
        results.append(run_step(step, runner=lambda cmd: _run(cmd, timeout=args.timeout)))
    n = counts(results)
    code = exit_code(results, require_all=args.require_all)
    summary = format_results(results)
    if args.require_all and n[SKIP]:
        summary += "\nREQUIRE-ALL 未过：有 %d 项没跑（缺环境或未启用）" % n[SKIP]
    emit({"env": env, "results": results, "counts": n,
          "require_all": args.require_all, "exit": code, "text": summary})
    return code


if __name__ == "__main__":
    sys.exit(main())
