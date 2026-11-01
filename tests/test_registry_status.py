# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`tools/registry_status.py` 的**离线**单测：零网络、零第三方、零 GPU。

跑：
    python tests/test_registry_status.py
    python -m unittest tests.test_registry_status -v

覆盖原则（这个工具的失败模式都很安静，所以每条都要能「真红」）：
1. **退出码真的会随状态变**：Active→0 / Flagged,Pending→2 / Banned→3 / 取数失败→4；
   用注入的假取回器切状态，不接近真实网络。
2. **错误路径不静默**：网络错、非 200、坏 JSON、schema 不对，都必须 4 + 人读原因，
   且失败时不留下半截快照目录。
3. **重试参数是明确的**：重试次数、退避秒数、超时值都能被注入断言，坏 JSON/5xx 才重试，
   4xx 不重试。
4. **落盘纪律**：目录名取采集时间、原子写不留 `.tmp`、`summary.md` 含关键事实但
   **不含本机路径**，且措辞不把 Flagged 写成安全证明。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.registry_status as registry_status  # noqa: E402

STATUS_ACTIVE = registry_status.STATUS_ACTIVE
STATUS_FLAGGED = registry_status.STATUS_FLAGGED
STATUS_PENDING = registry_status.STATUS_PENDING
STATUS_BANNED = registry_status.STATUS_BANNED
STATUS_DELETED = "NodeVersionStatusDeleted"

NODE_ID = "moss-storyboard"
NODE_URL = "https://api.comfy.org/nodes/moss-storyboard"
VERSIONS_URL = "https://api.comfy.org/nodes/moss-storyboard/versions"
INSTALL_URL = "https://api.comfy.org/nodes/moss-storyboard/install?version=0.3.3"

#: 固定时钟：Asia/Shanghai 2026-11-01 14:06:38 ⇒ UTC 2026-11-01T06:06:38Z。
FIXED_NOW = datetime(2026, 11, 1, 14, 6, 38, tzinfo=timezone(timedelta(hours=8)))
EXPECTED_DIR = "20261101-140638"
EXPECTED_UTC = "2026-11-01T06:06:38Z"


# ─────────────────────────────────────────────────────────────
# 假响应（注入点）
# ─────────────────────────────────────────────────────────────

class Seq(list):
    """显式的「多响应序列」包装。

    不能用一个裸 list 表达序列：`/versions` 的正常 payload 本来就是 list。要区分
    「payload 是数组」和「按调用依次返回多个响应」，就多一层类型。
    """


def make_node(
    *,
    status: str = STATUS_FLAGGED,
    downloads: int = 17,
    node_status: str = "NodeStatusActive",
    latest: bool = True,
    version: str = "0.3.3",
    publisher_status: str = "PublisherStatusActive",
) -> dict:
    payload = {
        "id": NODE_ID,
        "status": node_status,
        "downloads": downloads,
        "publisher": {"id": "zenhg", "status": publisher_status},
    }
    if latest:
        payload["latest_version"] = {
            "version": version,
            "status": status,
            "createdAt": "2026-10-30T01:52:35.49355Z",
        }
    return payload


def make_versions(
    *,
    status: str = STATUS_FLAGGED,
    versions: tuple[str, ...] = ("0.3.3", "0.3.2", "0.3.1"),
    statuses: list[str] | None = None,
) -> list[dict]:
    statuses = list(statuses or [status] * len(versions))
    return [
        {
            "version": version,
            "status": statuses[index],
            "createdAt": "2026-10-30T01:52:35.49355Z",
            "deprecated": False,
        }
        for index, version in enumerate(versions)
    ]


def make_install(*, status: str = STATUS_FLAGGED, version: str = "0.3.3") -> dict:
    return {
        "version": version,
        "status": status,
        "downloadUrl": "https://cdn.comfy.org/zenhg/moss-storyboard/%s/node.zip" % version,
        "dependencies": [],
        "deprecated": False,
    }


class FakeResponses:
    """注入用的假取回器：按端点排队返回 payload / `(status, body)` / 异常。

    - `payload` 为 dict / str 时按 HTTP 200 返回；
    - `(status, body)` 元组按给定状态码返回；
    - `Exception` 实例直接抛出（模拟网络错）；
    - `Seq([...])` 表示同一端点的多次调用依次取响应（模拟重试）。
    """

    def __init__(self, *, node=None, versions=None, install=None) -> None:
        self.queues = {
            "node": self._queue(make_node() if node is None else node),
            "versions": self._queue(make_versions() if versions is None else versions),
            "install": self._queue(make_install() if install is None else install),
        }
        self.calls: list[tuple[str, float]] = []

    @staticmethod
    def _queue(value) -> list:
        return list(value) if isinstance(value, Seq) else [value]

    @staticmethod
    def _kind(url: str) -> str:
        if "/install?version=" in url:
            return "install"
        if url.endswith("/versions"):
            return "versions"
        return "node"

    @staticmethod
    def _respond(item):
        if isinstance(item, Exception):
            raise item
        status, body = item if isinstance(item, tuple) else (200, item)
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        return status, body

    def __call__(self, url: str, timeout: float) -> tuple[int, str]:
        kind = self._kind(url)
        self.calls.append((url, timeout))
        queue = self.queues[kind]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return self._respond(item)

    def urls(self) -> list[str]:
        return [url for url, _timeout in self.calls]


def run(argv: list[str], **kwargs) -> tuple[int, str, str]:
    """跑一次 `main()`，返回 `(exit_code, stdout, stderr)`；默认不睡、固定时钟。"""
    kwargs.setdefault("now", FIXED_NOW)
    kwargs.setdefault("sleep", lambda _seconds: None)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = registry_status.main(argv, **kwargs)
    return code, out.getvalue(), err.getvalue()


def run_check(*, expect: bool = True, extra: list[str] | None = None, **kwargs) -> tuple[int, str, str]:
    argv = ["check"]
    if expect:
        argv.append("--expect-latest-active")
    argv.extend(extra or [])
    return run(argv, **kwargs)


def snapshot_dir(root: Path) -> Path:
    """`--out-root` 下唯一的快照目录。"""
    dirs = sorted(path for path in root.iterdir() if path.is_dir())
    assert len(dirs) == 1, "期望恰好一个快照目录，实际 %r" % dirs
    return dirs[0]


# ─────────────────────────────────────────────────────────────
# 1. 退出码：状态 → 码
# ─────────────────────────────────────────────────────────────

class TestCheckExitCodes(unittest.TestCase):
    def test_active_is_zero(self):
        code, out, _err = run_check(opener=FakeResponses(node=make_node(status=STATUS_ACTIVE)))
        self.assertEqual(code, 0, out)
        self.assertIn("Active", out)
        self.assertIn("退出码：0", out)

    def test_flagged_is_two_and_says_pending_review(self):
        code, out, _err = run_check(opener=FakeResponses(node=make_node(status=STATUS_FLAGGED)))
        self.assertEqual(code, 2, out)
        self.assertIn("待安全复核", out)
        self.assertIn("退出码：2", out)

    def test_pending_is_two(self):
        code, out, _err = run_check(opener=FakeResponses(node=make_node(status=STATUS_PENDING)))
        self.assertEqual(code, 2, out)
        self.assertIn("Pending", out)

    def test_banned_is_three(self):
        code, out, _err = run_check(opener=FakeResponses(node=make_node(status=STATUS_BANNED)))
        self.assertEqual(code, 3, out)
        self.assertIn("Banned", out)

    def test_node_banned_wins_over_active_version(self):
        """节点级 Banned 比版本状态更严重：版本 Active 也判 3。"""
        code, out, _err = run_check(
            opener=FakeResponses(node=make_node(status=STATUS_ACTIVE, node_status="NodeStatusBanned"))
        )
        self.assertEqual(code, 3, out)

    def test_deleted_status_is_unclassified_error(self):
        code, out, _err = run_check(opener=FakeResponses(node=make_node(status=STATUS_DELETED)))
        self.assertEqual(code, 4, out)
        self.assertIn("人工", out)

    def test_without_flag_is_probe_only(self):
        """不加断言标志时只探测：Flagged 也返回 0，但必须明说「不判定发布门槛」。"""
        code, out, _err = run_check(expect=False, opener=FakeResponses())
        self.assertEqual(code, 0, out)
        self.assertIn("不判定发布门槛", out)
        self.assertIn("--expect-latest-active", out)

    def test_missing_latest_version_cannot_be_judged(self):
        code, out, _err = run_check(opener=FakeResponses(node=make_node(latest=False)))
        self.assertEqual(code, 4, out)
        self.assertIn("没有 latest_version.version", out)

    def test_mixed_version_statuses_reported(self):
        versions = make_versions(
            versions=("0.3.3", "0.3.2", "0.3.1"),
            statuses=[STATUS_FLAGGED, STATUS_FLAGGED, STATUS_ACTIVE],
        )
        code, out, _err = run_check(
            opener=FakeResponses(node=make_node(status=STATUS_FLAGGED), versions=versions)
        )
        self.assertEqual(code, 2, out)
        self.assertIn("NodeVersionStatusFlagged=2", out)
        self.assertIn("NodeVersionStatusActive=1", out)


# ─────────────────────────────────────────────────────────────
# 2. 错误路径：必须 4，且有原因，且不留半截目录
# ─────────────────────────────────────────────────────────────

class TestErrorPaths(unittest.TestCase):
    def setUp(self) -> None:
        import shutil
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="registry_status_err_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def test_network_error_is_four_and_not_a_status_conclusion(self):
        opener = FakeResponses(node=urllib.error.URLError("dns boom"))
        out_root = self.tmp / "snapshots"
        code, out, err = run(["snapshot", "--out-root", str(out_root)], opener=opener)
        self.assertEqual(code, 4, out + err)
        self.assertIn("network", err)
        self.assertIn("不是", err)
        self.assertFalse(out_root.exists(), "取数失败时不该创建快照目录")
        self.assertEqual(len(opener.calls), 3, "默认应重试 2 次（共 3 次请求）：%r" % opener.calls)

    def test_http_404_is_not_retried(self):
        opener = FakeResponses(node=(404, "{}"))
        code, _out, err = run_check(opener=opener)
        self.assertEqual(code, 4)
        self.assertIn("HTTP 404", err)
        self.assertIn("不重试", err)
        self.assertEqual(len(opener.calls), 1, "4xx 不该重试：%r" % opener.calls)

    def test_bad_json_is_four_after_retries(self):
        opener = FakeResponses(node=(200, "{not json"))
        code, _out, err = run_check(opener=opener)
        self.assertEqual(code, 4)
        self.assertIn("json", err)
        self.assertIn("JSON 解析失败", err)
        # 默认 --retries 2 ⇒ 同一端点共 3 次请求。
        self.assertEqual(len(opener.calls), 3, "坏 JSON 应按默认重试次数重试：%r" % opener.calls)

    def test_bad_versions_schema_is_four(self):
        opener = FakeResponses(versions={"unexpected": 1})
        code, _out, err = run_check(opener=opener)
        self.assertEqual(code, 4)
        self.assertIn("schema", err)

    def test_versions_items_must_be_objects(self):
        opener = FakeResponses(versions=["0.3.3"])
        code, _out, err = run_check(opener=opener)
        self.assertEqual(code, 4)
        self.assertIn("schema", err)

    def test_node_payload_not_object_is_four(self):
        opener = FakeResponses(node=[1, 2, 3])
        code, _out, err = run_check(opener=opener)
        self.assertEqual(code, 4)
        self.assertIn("schema", err)

    def test_install_payload_not_object_is_four(self):
        opener = FakeResponses(install=["nope"])
        code, _out, err = run_check(opener=opener)
        self.assertEqual(code, 4)
        self.assertIn("schema", err)

    def test_http_500_then_success_passes(self):
        opener = FakeResponses(node=Seq([(500, "boom"), (503, "boom"), make_node()]))
        code, out, _err = run_check(expect=False, opener=opener)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(opener.calls), 5, "node 3 次 + versions 1 次 + install 1 次：%r" % opener.calls)

    def test_http_429_is_retried(self):
        opener = FakeResponses(node=Seq([(429, "slow down"), make_node()]))
        code, out, _err = run_check(expect=False, opener=opener)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(opener.calls), 4)


# ─────────────────────────────────────────────────────────────
# 3. 重试/超时/退避参数是明确的
# ─────────────────────────────────────────────────────────────

class TestRetryControls(unittest.TestCase):
    def test_retries_zero_means_single_attempt(self):
        opener = FakeResponses(node=urllib.error.URLError("down"))
        code, _out, err = run_check(extra=["--retries", "0"], opener=opener)
        self.assertEqual(code, 4)
        self.assertEqual(len(opener.calls), 1, "retries=0 应只请求 1 次：%r" % opener.calls)
        self.assertIn("network", err)

    def test_backoff_schedule_is_injected(self):
        sleeps: list[float] = []
        opener = FakeResponses(node=urllib.error.URLError("down"))
        code, _out, _err = run_check(
            extra=["--retries", "2", "--backoff", "0.5"],
            opener=opener,
            sleep=sleeps.append,
        )
        self.assertEqual(code, 4)
        self.assertEqual(sleeps, [0.5, 1.0], "第 n 次重试前应等 backoff*n")

    def test_default_backoff_schedule(self):
        sleeps: list[float] = []
        opener = FakeResponses(node=urllib.error.URLError("down"))
        run_check(opener=opener, sleep=sleeps.append)
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_timeout_flag_reaches_opener(self):
        opener = FakeResponses()
        code, _out, _err = run_check(expect=False, extra=["--timeout", "3.5"], opener=opener)
        self.assertEqual(code, 0)
        self.assertTrue(all(timeout == 3.5 for _url, timeout in opener.calls), opener.calls)

    def test_urls_are_the_three_public_endpoints(self):
        opener = FakeResponses()
        run_check(expect=False, opener=opener)
        self.assertEqual(opener.urls(), [NODE_URL, VERSIONS_URL, INSTALL_URL])
        self.assertTrue(all(url.startswith("https://") for url in opener.urls()))

    def test_node_id_and_api_base_override(self):
        opener = FakeResponses()
        code, _out, _err = run_check(
            expect=False,
            extra=["--node-id", "other-node", "--api-base", "https://example.test/"],
            opener=opener,
        )
        self.assertEqual(code, 0)
        self.assertEqual(opener.urls()[0], "https://example.test/nodes/other-node")
        self.assertEqual(opener.urls()[2], "https://example.test/nodes/other-node/install?version=0.3.3")


# ─────────────────────────────────────────────────────────────
# 4. snapshot 落盘
# ─────────────────────────────────────────────────────────────

class TestSnapshot(unittest.TestCase):
    def setUp(self) -> None:
        import shutil
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="registry_status_snap_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def _snapshot(self, **kwargs) -> tuple[int, str, str, Path]:
        code, out, err = run(["snapshot", "--out-root", str(self.tmp)], **kwargs)
        return code, out, err, snapshot_dir(self.tmp)

    def test_files_and_names(self):
        opener = FakeResponses()
        code, out, err, out_dir = self._snapshot(opener=opener)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(out_dir.name, EXPECTED_DIR, "目录名应取采集时间的本地 YYYYMMDD-HHMMSS")
        expected = {"node.json", "versions.json", "install.json", "meta.json", "summary.md"}
        self.assertEqual({path.name for path in out_dir.iterdir()}, expected)
        # 两阶段写入：成功路径下 out-root 里只应有最终目录，没有临时目录残留。
        self.assertEqual([path.name for path in self.tmp.iterdir()], [EXPECTED_DIR])
        self.assertIn("快照已落盘", out)

    def test_raw_json_is_the_api_payload(self):
        node = make_node(downloads=23)
        versions = make_versions()
        install = make_install()
        code, _out, _err, out_dir = self._snapshot(
            opener=FakeResponses(node=node, versions=versions, install=install)
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((out_dir / "node.json").read_text(encoding="utf-8")), node)
        self.assertEqual(json.loads((out_dir / "versions.json").read_text(encoding="utf-8")), versions)
        self.assertEqual(json.loads((out_dir / "install.json").read_text(encoding="utf-8")), install)

    def test_meta_facts(self):
        code, _out, _err, out_dir = self._snapshot(opener=FakeResponses())
        self.assertEqual(code, 0)
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["captured_at_utc"], EXPECTED_UTC)
        self.assertEqual(meta["captured_at_local"], "2026-11-01 14:06:38 +0800")
        self.assertEqual(meta["node_status"], "NodeStatusActive")
        self.assertEqual(meta["latest_version"], "0.3.3")
        self.assertEqual(meta["latest_status"], STATUS_FLAGGED)
        self.assertEqual(meta["downloads"], 17)
        self.assertEqual(meta["versions_total"], 3)
        self.assertEqual(meta["status_counts"], {STATUS_FLAGGED: 3})
        self.assertEqual(meta["attempts"], {"node": 1, "versions": 1, "install": 1})
        self.assertEqual(meta["exit_code_if_checked"], 2)
        self.assertEqual(
            meta["install_download_url"],
            "https://cdn.comfy.org/zenhg/moss-storyboard/0.3.3/node.zip",
        )

    def test_summary_contains_facts_not_safety_claim(self):
        code, _out, _err, out_dir = self._snapshot(opener=FakeResponses(node=make_node(downloads=23)))
        self.assertEqual(code, 0)
        summary = (out_dir / "summary.md").read_text(encoding="utf-8")
        for needle in (
            "# Registry 状态快照",
            "moss-storyboard",
            STATUS_FLAGGED,
            "0.3.3",
            "23",
            "待安全复核",
            "不是安全证明",
            "Flagged=3",
            "https://cdn.comfy.org/zenhg/moss-storyboard/0.3.3/node.zip",
            "docs/52",
            "docs/60",
        ):
            self.assertIn(needle, summary, "summary.md 缺少 %r" % needle)
        self.assertNotIn("已通过安全审核", summary)
        self.assertNotIn("恶意软件", summary)

    def test_summary_has_no_local_paths(self):
        code, _out, _err, out_dir = self._snapshot(opener=FakeResponses())
        self.assertEqual(code, 0)
        for name in ("summary.md", "meta.json"):
            text = (out_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(":\\", text, "%s 里出现了本机盘符路径" % name)
            self.assertNotIn(str(ROOT), text, "%s 里出现了仓库绝对路径" % name)

    def test_atomic_write_leaves_no_tmp_files(self):
        _code, _out, _err, out_dir = self._snapshot(opener=FakeResponses())
        # 单文件 .tmp 与两阶段目录的 .tmp-* 都不许留在快照目录或 out-root 里。
        leftovers = [
            path.name
            for path in list(out_dir.iterdir()) + list(out_dir.parent.iterdir())
            if ".tmp" in path.name
        ]
        self.assertEqual(leftovers, [], "原子写不应留下 .tmp / .tmp-*：%r" % leftovers)

    def test_same_second_second_snapshot_gets_suffix(self):
        first, _out, _err = run(["snapshot", "--out-root", str(self.tmp)], opener=FakeResponses())
        second, out, _err = run(["snapshot", "--out-root", str(self.tmp)], opener=FakeResponses())
        self.assertEqual(first, 0)
        self.assertEqual(second, 0, out)
        names = sorted(path.name for path in self.tmp.iterdir() if path.is_dir())
        self.assertEqual(names, [EXPECTED_DIR, EXPECTED_DIR + "-01"])

    def test_install_skipped_when_no_latest_version(self):
        opener = FakeResponses(node=make_node(latest=False))
        code, out, err, out_dir = self._snapshot(opener=opener)
        self.assertEqual(code, 0, out + err)
        self.assertFalse((out_dir / "install.json").exists(), "没有 latest version 就不该请求 install 端点")
        self.assertEqual(len(opener.calls), 2, "只该请求 node + versions：%r" % opener.calls)
        summary = (out_dir / "summary.md").read_text(encoding="utf-8")
        self.assertIn("未请求", summary)
        # 同一份数据用 check 断言时无法判定。
        _code, _out, _err = run_check(opener=FakeResponses(node=make_node(latest=False)))
        self.assertEqual(_code, 4)

    def test_snapshot_failure_writes_nothing(self):
        out_root = self.tmp / "nested" / "snapshots"
        opener = FakeResponses(versions=(200, "<html>"))
        code, _out, err = run(["snapshot", "--out-root", str(out_root)], opener=opener)
        self.assertEqual(code, 4, err)
        self.assertFalse(out_root.exists(), "取数失败时不该创建快照根目录")

    def test_snapshot_io_failure_is_four(self):
        """落盘失败（本地 IO）也是 4，且要说清是 IO 不是状态结论。"""
        blocker = self.tmp / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        code, _out, err = run(["snapshot", "--out-root", str(blocker)], opener=FakeResponses())
        self.assertEqual(code, 4, err)
        self.assertIn("io", err)
        self.assertIn("不是 Registry 状态结论", err)

    def test_midway_write_failure_leaves_no_residue(self):
        """F1：第 2 次写盘失败后 out-root 下不许有残留目录/文件/.tmp-*。

        复核（docs/64 F1）实测旧实现会留下「快照目录 + 已写好的 node.json」，让人误以为
        是可用证据。两阶段写入后：失败必须整体消失。
        """
        out_root = self.tmp / "nested" / "snapshots"
        real_write = registry_status._write_atomic
        calls = {"count": 0}

        def flaky_write(path, text):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("disk full (injected)")
            real_write(path, text)

        with mock.patch.object(registry_status, "_write_atomic", flaky_write):
            code, _out, err = run(["snapshot", "--out-root", str(out_root)], opener=FakeResponses())
        self.assertEqual(code, 4, err)
        self.assertIn("io", err)
        self.assertIn("不是 Registry 状态结论", err)
        self.assertEqual(calls["count"], 2, "应在第 2 次写盘时失败（注入点命中）")
        residue = list(out_root.iterdir()) if out_root.exists() else []
        self.assertEqual(residue, [], "中途写盘失败后 out-root 下不该有残留：%r" % residue)
        self.assertEqual(list(self.tmp.rglob("*.tmp-*")), [], "不该留下临时目录")
        self.assertFalse(out_root.exists(), "本次新建的 out-root 也应被清理")

    def test_midway_failure_keeps_preexisting_out_root_empty(self):
        """用户预建的 out-root 不能被误删；但里面不许有残留。"""
        out_root = self.tmp / "snapshots"
        out_root.mkdir()
        real_write = registry_status._write_atomic

        def flaky_write(path, text):
            if path.name == "versions.json":
                raise OSError("disk full (injected)")
            real_write(path, text)

        with mock.patch.object(registry_status, "_write_atomic", flaky_write):
            code, _out, err = run(["snapshot", "--out-root", str(out_root)], opener=FakeResponses())
        self.assertEqual(code, 4, err)
        self.assertTrue(out_root.exists(), "用户预建的 out-root 不该被删除")
        self.assertEqual(list(out_root.iterdir()), [], "预建 out-root 里不该有残留")
        self.assertEqual(list(self.tmp.rglob("*.tmp-*")), [])


# ─────────────────────────────────────────────────────────────
# 5. 注入方式与 JSON 输出
# ─────────────────────────────────────────────────────────────

class TestInjection(unittest.TestCase):
    def test_monkeypatched_network_opener_is_used(self):
        """文档写明的猴补注入点：替换模块级 network_opener，不传 opener 也能离线跑。"""
        fake = FakeResponses(node=make_node(status=STATUS_ACTIVE))
        with mock.patch.object(registry_status, "network_opener", fake):
            code, out, _err = run_check(now=FIXED_NOW, sleep=lambda _s: None)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(fake.calls), 3)

    def test_json_output_is_machine_readable(self):
        code, out, err = run_check(extra=["--json"], opener=FakeResponses())
        self.assertEqual(code, 2, out + err)
        payload = json.loads(out)
        self.assertEqual(payload["exit_code"], 2)
        self.assertEqual(payload["latest_status"], STATUS_FLAGGED)
        self.assertEqual(payload["status_counts"], {STATUS_FLAGGED: 3})
        self.assertEqual(payload["expect_latest_active"], True)
        self.assertIn("next_steps", payload)
        self.assertEqual(payload["captured_at_utc"], EXPECTED_UTC)

    def test_json_output_on_active(self):
        code, out, _err = run_check(extra=["--json"], opener=FakeResponses(node=make_node(status=STATUS_ACTIVE)))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["latest_status"], STATUS_ACTIVE)

    def test_json_is_only_output(self):
        _code, out, _err = run_check(extra=["--json"], opener=FakeResponses())
        self.assertTrue(out.lstrip().startswith("{"), "JSON 模式不应混入人读文本：%r" % out[:80])


# ─────────────────────────────────────────────────────────────
# 6. 措辞纪律
# ─────────────────────────────────────────────────────────────

class TestWordingDiscipline(unittest.TestCase):
    def test_flagged_never_called_safe_or_malicious(self):
        code, out, _err = run_check(opener=FakeResponses())
        self.assertEqual(code, 2)
        self.assertIn("不是安全证明", out)
        self.assertNotIn("已通过安全审核", out)
        # 不把 Flagged 写成「已判定有问题」；只允许出现否定句式。
        self.assertNotIn("恶意软件", out)
        self.assertIn("不是恶意判定", out)

    def test_opt_in_not_recommended(self):
        _code, out, _err = run_check(opener=FakeResponses())
        self.assertIn("不推荐常开", out)
        self.assertNotIn("建议常开", out)

    def test_error_message_does_not_pretend_status(self):
        _code, _out, err = run_check(opener=FakeResponses(node=urllib.error.URLError("boom")))
        self.assertIn("不是", err)
        self.assertIn("Registry 状态结论", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
