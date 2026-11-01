# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/ci_preflight.py` 的单测（零 GPU、零网络、零宿主）。

判据：预检脚本最容易出的错不是「跑挂」，是**把 SKIP 说成 PASS**。
所以这里的重点是：缺环境/未启用时必须输出 SKIP（未跑），`--require-all` 时必须非 0，
以及环境探测的桩替身（宿主路径优先级 / 无 custom_nodes / nvidia-smi / node+playwright）。

跑：python tests/test_ci_preflight.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ci_preflight as P  # noqa: E402


def env(host=None, gpu=None, playwright=None):
    return {
        "host": host, "host_why": "" if host else "缺宿主（桩：无 custom_nodes）",
        "gpu": gpu, "gpu_why": "" if gpu else "缺 GPU（桩：无 nvidia-smi）",
        "playwright": playwright,
        "playwright_why": "" if playwright else "缺浏览器依赖（桩：找不到 chromium）",
    }


class FakeRunner:
    """按脚本名返回固定退出码；记录调用过哪些命令。"""

    def __init__(self, codes=None, out="fake ok"):
        self.codes = codes or {}
        self.out = out
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        base = os.path.basename(str(cmd[-1]))
        return self.codes.get(base, 0), self.out + " " + base


class TestEnvironmentDetection(unittest.TestCase):
    def test_host_path_takes_precedence_and_requires_custom_nodes(self):
        with tempfile.TemporaryDirectory() as good, tempfile.TemporaryDirectory() as bad:
            os.makedirs(os.path.join(good, "custom_nodes"))
            got, why = P.detect_host(environ={"COMFYUI_PATH": good})
            self.assertEqual(got, good)
            self.assertEqual(why, "")
            none, why2 = P.detect_host(environ={"COMFYUI_PATH": bad})
            self.assertIsNone(none)
            self.assertIn("没有 custom_nodes", why2)

    def test_missing_host_is_none_with_actionable_reason(self):
        with tempfile.TemporaryDirectory() as empty_repo:
            # 仓库根放空目录 ⇒ 三条路径都不成立
            with mock.patch.object(P, "ROOT", empty_repo):
                got, why = P.detect_host(environ={"COMFYUI_PATH": ""})
            self.assertIsNone(got)
            self.assertIn("缺宿主", why)
            self.assertIn("COMFYUI_PATH", why)

    def test_gpu_detection_missing_and_present(self):
        got, why = P.detect_gpu(which=lambda name: None)
        self.assertIsNone(got)
        self.assertIn("缺 GPU", why)

        got2, why2 = P.detect_gpu(
            which=lambda name: "nvidia-smi",
            runner=lambda cmd, **kw: (0, "NVIDIA RTX 4090\n"))
        self.assertEqual(got2, "NVIDIA RTX 4090")
        self.assertEqual(why2, "")

        got3, _ = P.detect_gpu(which=lambda name: "nvidia-smi",
                               runner=lambda cmd, **kw: (1, "driver error"))
        self.assertIsNone(got3)

    def test_playwright_detection_missing_node_and_ready(self):
        got, why = P.detect_playwright(which=lambda name: None)
        self.assertIsNone(got)
        self.assertIn("没有 node", why)

        payload = json.dumps({"core": True, "exe": True})
        got2, why2 = P.detect_playwright(
            which=lambda name: "node",
            runner=lambda cmd, **kw: (0, payload))
        self.assertIn("playwright-core", got2)
        self.assertEqual(why2, "")

        payload3 = json.dumps({"core": True, "exe": False})
        got3, why3 = P.detect_playwright(which=lambda name: "node",
                                        runner=lambda cmd, **kw: (0, payload3))
        self.assertIsNone(got3)
        self.assertIn("chromium", why3)


class TestStepPlan(unittest.TestCase):
    def test_all_missing_plans_are_skip_with_reasons(self):
        steps = P.build_steps(env(), with_gpu=False)
        names = [s["name"] for s in steps]
        self.assertEqual(names, ["host-loader", "ui-smoke", "ui-widget-compat",
                                 "ui-llm-panel", "e2e-matrix"])
        for s in steps:
            self.assertTrue(s["skip"], "%s 缺环境时不能计划去跑" % s["name"])
            self.assertTrue(("缺" in s["skip"]) or ("未启用" in s["skip"]),
                            "%s 的 SKIP 原因不明确：%r" % (s["name"], s["skip"]))

    def test_host_only_skips_browser_steps_not_host_loader(self):
        steps = P.build_steps(env(host="/host"), with_gpu=False)
        by = {s["name"]: s for s in steps}
        self.assertEqual(by["host-loader"]["skip"], "")
        for name in ("ui-smoke", "ui-widget-compat", "ui-llm-panel"):
            self.assertIn("浏览器依赖", by[name]["skip"])
        self.assertIn("--with-gpu", by["e2e-matrix"]["skip"])

    def test_gpu_step_requires_explicit_opt_in(self):
        steps = P.build_steps(env(host="/h", gpu="RTX 4090"), with_gpu=False)
        self.assertIn("--with-gpu", {s["name"]: s for s in steps}["e2e-matrix"]["skip"])
        on = P.build_steps(env(host="/h", gpu="RTX 4090"), with_gpu=True)
        # 工作流文件不存在时仍然 SKIP，且原因点名缺哪个文件
        e2e = {s["name"]: s for s in on}["e2e-matrix"]
        self.assertIn("MOSS 故事板宫格.json", e2e["skip"])

    def test_gpu_step_runs_when_enabled_and_workflow_present(self):
        with tempfile.TemporaryDirectory() as host:
            wf = os.path.join(host, P.E2E_WORKFLOW)
            os.makedirs(os.path.dirname(wf))
            Path(wf).write_text("{}", encoding="utf-8")
            steps = P.build_steps(env(host=host, gpu="RTX 4090"), with_gpu=True)
            e2e = {s["name"]: s for s in steps}["e2e-matrix"]
            self.assertEqual(e2e["skip"], "")
            runner = FakeRunner()
            res = P.run_all(steps, runner=runner)
            self.assertTrue(any("e2e_matrix.py" in " ".join(c) for c in runner.calls))
            n = P.counts(res)
            self.assertEqual((n[P.PASS], n[P.SKIP]), (2, 3))  # host-loader + e2e 跑；浏览器三项缺 playwright


class TestExecutionClassification(unittest.TestCase):
    def test_nothing_runs_and_exit_zero_by_default(self):
        steps = P.build_steps(env(), with_gpu=False)
        runner = FakeRunner()
        res = P.run_all(steps, runner=runner)
        self.assertEqual(runner.calls, [])          # 缺环境 ⇒ 一条都没跑
        n = P.counts(res)
        self.assertEqual((n[P.PASS], n[P.SKIP], n[P.FAIL]), (0, 5, 0))
        self.assertEqual(P.exit_code(res), 0)        # 默认允许 SKIP
        text = P.format_results(res)
        self.assertIn("SKIP（未跑）", text)
        self.assertIn("≠ 通过", text)
        self.assertNotIn("PASS ", text)

    def test_require_all_turns_skip_into_nonzero(self):
        res = P.run_all(P.build_steps(env(), with_gpu=False), runner=FakeRunner())
        self.assertEqual(P.exit_code(res, require_all=True), 2)

    def test_host_loader_pass_but_browser_steps_skip(self):
        steps = P.build_steps(env(host="/host"), with_gpu=False)
        runner = FakeRunner()                       # host_loader 返回 0
        res = P.run_all(steps, runner=runner)
        by = {r["name"]: r for r in res}
        self.assertEqual(by["host-loader"]["status"], P.PASS)
        self.assertEqual(by["ui-smoke"]["status"], P.SKIP)
        self.assertEqual(len(runner.calls), 1)

    def test_hard_failure_is_fail_and_exit_one(self):
        steps = P.build_steps(env(host="/host"), with_gpu=False)
        runner = FakeRunner(codes={"host_loader_check.py": 1})
        res = P.run_all(steps, runner=runner)
        self.assertEqual({r["name"]: r for r in res}["host-loader"]["status"], P.FAIL)
        self.assertEqual(P.exit_code(res), 1)

    def test_ui_environment_exit_2_is_skip_not_fail(self):
        steps = P.build_steps(env(host="/host", playwright="pw"), with_gpu=False)
        runner = FakeRunner(codes={"ui_smoke.mjs": 2})
        res = P.run_all(steps, runner=runner)
        smoke = {r["name"]: r for r in res}["ui-smoke"]
        self.assertEqual(smoke["status"], P.SKIP)
        self.assertIn("环境问题", smoke["detail"])
        self.assertEqual(P.exit_code(res), 0)


class TestCli(unittest.TestCase):
    def _fake_env(self):
        return env(host="/host", gpu="RTX 4090", playwright="pw")

    def test_probe_only_never_runs_and_exits_zero(self):
        with mock.patch.object(P, "detect_environment", return_value=env()):
            rc = P.main(["--probe-only", "--json"])
        self.assertEqual(rc, 0)

    def test_require_all_cli_returns_two_without_env(self):
        with mock.patch.object(P, "detect_environment", return_value=env()):
            rc = P.main(["--require-all", "--json"])
        self.assertEqual(rc, 2)

    def test_json_output_is_valid_and_separates_counts(self):
        with mock.patch.object(P, "detect_environment", return_value=env()):
            import io
            buf = io.StringIO()
            with mock.patch.object(sys, "stdout", buf):
                rc = P.main(["--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["counts"], {P.PASS: 0, P.SKIP: 5, P.FAIL: 0})
        self.assertEqual(data["exit"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
