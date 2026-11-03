# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Comfy Registry 版本状态跟进工具（M4）：snapshot 取证 + check 断言。

## 为什么需要它

历史记录（git 历史）已把 `NodeVersionStatusFlagged` 的语义查清：它是**自动扫描有发现、等待安全
复核**的中间态（Registry 网页徽章文案就是 "Pending Security Review"），**既不等于
「已证明安全」，也不等于「已判定有问题」**。具体 issues 只进 Registry 团队的私有频道，
公开 API 的 `status_reason` 为空（或干脆没有这个键），所以跟进只能靠两件事：
**定期抓公开状态当证据** + **按流程向 Registry 团队要扫描清单**，不能靠猜。

这个脚本把「抓状态」固定成可复跑的命令，避免每次手敲 curl、凭记忆对比。

## 子命令

- `snapshot`：GET 三个公开只读端点（节点 / 版本列表 / 安装信息），把**原始 JSON**
  + `meta.json` + 人读 `summary.md` 原子落盘到
  `out/registry_status/<YYYYMMDD-HHMMSS>/`（目录名取本地时间）。
- `check`：GET 同一批端点，按「最新版本状态」给退出码，人或 CI 都能当闸用。

## 退出码

`check`（**加** `--expect-latest-active` 时按状态断言；不加时只探测，取数成功即 0）：

| 码 | 含义 |
|---|---|
| 0 | `NodeVersionStatusActive`（不加断言标志时 = 取数成功） |
| 2 | `Flagged` / `Pending`：待安全复核，尚未通过 |
| 3 | `Banned`（节点级 `NodeStatusBanned` 优先判 3，即使版本状态是 Active） |
| 4 | 取数失败：网络 / HTTP / 坏 JSON / schema 异常；或状态不在预期集合内（如 `Deleted`） |

**4 只表示「没取到可判定的数据」，不是 Registry 状态结论**——别把它读成「状态有问题」。
`snapshot` 退出码：0 = 三个端点都取到且落盘完成；4 = 取数 / 落盘失败。落盘用**两阶段写入**
（先写 `<最终目录>.tmp-<随机>`，全部文件成功后整体 rename 为最终目录；失败删除临时目录），
所以**快照目录存在就代表它是完整的**，失败路径不会留下半截目录/文件。

## 纪律

- 只用标准库（`urllib` / `json`）；**零第三方依赖**。不读 `.comfy_registry_token`、
  不带任何凭据/请求头之外的身份信息，只打公开只读端点。
- 不静默吞错：网络错误按 `--retries` / `--backoff` 重试；重试后仍失败就以退出码 4 +
  人读原因结束；坏 JSON 不会被当成空数据。
- 状态措辞：Flagged / Pending 只描述为「待安全复核」，不写成「已通过」或「有问题」；
  工具**不推荐**给用户常开 `allow_flagged_nodepack_install`（`CHANGES`）。

参考：`CHANGES.md`（语义与三条安装路径）、
`CHANGES.md`（决策树、英文联络稿、发布前后 24h 清单）。

## 用法

    python tools/registry_status.py snapshot
    python tools/registry_status.py check
    python tools/registry_status.py check --expect-latest-active
    python tools/registry_status.py check --expect-latest-active --json

参数（两个子命令共有）：`--timeout` 单次请求超时秒数（默认 20）、`--retries` 失败重试
次数（默认 2，总请求 1+N）、`--backoff` 第 n 次重试前等 `backoff * n` 秒（默认 2.0）、
`--node-id`（默认 `moss-storyboard`）、`--api-base`（默认 `https://api.comfy.org`）。
`snapshot` 另有 `--out-root`（默认 `out/registry_status`）；`check` 另有
`--expect-latest-active`、`--json`。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_ROOT = ROOT / "out" / "registry_status"

API_BASE = "https://api.comfy.org"
NODE_ID = "moss-storyboard"
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 2.0
USER_AGENT = (
    "moss-storyboard-registry-status/1.0 "
    "(+https://github.com/ZenHG/ComfyUI-MOSS-Storyboard)"
)

#: `check` 的退出码。4 表示「没取到可判定的数据」，不是状态结论。
EXIT_OK = 0
EXIT_UNEASY = 2
EXIT_BANNED = 3
EXIT_ERROR = 4

STATUS_ACTIVE = "NodeVersionStatusActive"
STATUS_FLAGGED = "NodeVersionStatusFlagged"
STATUS_PENDING = "NodeVersionStatusPending"
STATUS_BANNED = "NodeVersionStatusBanned"
NODE_STATUS_BANNED = "NodeStatusBanned"

#: 版本状态 → `check --expect-latest-active` 的退出码。未列出的枚举（如 Deleted）判 4。
STATUS_EXIT = {
    STATUS_ACTIVE: EXIT_OK,
    STATUS_FLAGGED: EXIT_UNEASY,
    STATUS_PENDING: EXIT_UNEASY,
    STATUS_BANNED: EXIT_BANNED,
}

STATUS_MEANING = {
    STATUS_ACTIVE: "自动检查通过，Registry 正常状态（发布闸放行）",
    STATUS_FLAGGED: "自动扫描有发现、等待安全复核（Pending Security Review）；不是安全证明",
    STATUS_PENDING: "新版本已上传、尚未跑完自动扫描；不是安全证明",
    STATUS_BANNED: "已拒绝 / 下架；安装不可用",
}


class RegistryStatusError(Exception):
    """取数或落盘失败。`kind` 用于人读归因（network / http / json / schema / io）。"""

    def __init__(self, kind: str, location: str, detail: str) -> None:
        super().__init__("%s @ %s: %s" % (kind, location, detail))
        self.kind = kind
        self.location = location
        self.detail = detail


# ─────────────────────────────────────────────────────────────
# 取数层
# ─────────────────────────────────────────────────────────────

def network_opener(url: str, timeout: float) -> tuple[int, str]:
    """默认取回器：返回 `(http_status, body_text)`。测试通过注入/猴补替换本函数。

    只发 `Accept` 与固定 `User-Agent`；不读任何 token、不带凭据。
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", None) or response.getcode()
        body = response.read().decode("utf-8", errors="replace")
    return int(status), body


def fetch_json(
    url: str,
    *,
    opener: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Any = None,
) -> tuple[Any, int]:
    """GET + 解析 JSON，返回 `(data, attempts)`。

    重试策略（明确写在这里，不静默）：
    - 网络错误（`URLError` / 超时 / `OSError`）、HTTP 5xx、HTTP 429、坏 JSON → 可重试；
    - 其它 4xx → **不重试**（重试一个 403/404 没有意义，只会掩盖问题）；
    - 第 n 次重试前等 `backoff * n` 秒（`sleep` 可注入，测试里传 no-op）。
    """
    do_open = network_opener if opener is None else opener
    do_sleep = time.sleep if sleep is None else sleep
    last: RegistryStatusError | None = None
    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            status, body = do_open(url, timeout)
        except RegistryStatusError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 or exc.code == 429:
                last = RegistryStatusError("http", url, "HTTP %s（可重试）" % exc.code)
            else:
                raise RegistryStatusError("http", url, "HTTP %s（不重试）" % exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = RegistryStatusError("network", url, "%s: %s" % (type(exc).__name__, exc))
        else:
            if status >= 500 or status == 429:
                last = RegistryStatusError("http", url, "HTTP %s（可重试）" % status)
            elif status != 200:
                raise RegistryStatusError("http", url, "HTTP %s（不重试）" % status)
            else:
                try:
                    return json.loads(body), attempts
                except (TypeError, ValueError) as exc:
                    # 截断的响应体也可能表现为坏 JSON，所以同样重试。
                    last = RegistryStatusError("json", url, "JSON 解析失败：%s" % exc)
        if attempt < retries:
            do_sleep(backoff * (attempt + 1))
    if last is None:  # 理论不可达：循环里每次都设置了失败原因
        last = RegistryStatusError("network", url, "未知失败（未设置原因）")
    raise last


def _version_list(raw: Any, url: str) -> list[dict]:
    """`/versions` 响应 → 版本对象列表（只认数组，或带 `versions` 键的对象）。"""
    if isinstance(raw, dict):
        for key in ("versions", "items", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        raise RegistryStatusError(
            "schema", url, "版本列表响应既不是数组，也没有 versions/items/data 数组键"
        )
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RegistryStatusError(
                "schema", url, "版本列表第 %d 项不是对象（类型 %s）" % (index, type(item).__name__)
            )
    return raw


def _latest_of(node: dict, url: str) -> dict:
    """节点响应 → `latest_version` 对象（缺失时返回空 dict，由调用方记警告）。"""
    latest = node.get("latest_version")
    if latest is None:
        return {}
    if not isinstance(latest, dict):
        raise RegistryStatusError(
            "schema", url, "latest_version 不是对象（类型 %s）" % type(latest).__name__
        )
    return latest


@dataclass
class Facts:
    """一次采集的全部事实。所有字段都是 API 原文或由原文直接抽取，不含推断。"""

    node_id: str
    api_base: str
    captured_at: datetime
    elapsed_s: float
    node: dict
    versions_raw: Any
    versions: list[dict]
    latest: dict
    install: dict | None
    install_url: str
    attempts: dict[str, int]
    warnings: list[str]

    @property
    def latest_version(self) -> str | None:
        value = self.latest.get("version")
        return value if isinstance(value, str) and value else None

    @property
    def latest_status(self) -> str | None:
        value = self.latest.get("status")
        return value if isinstance(value, str) and value else None

    @property
    def node_status(self) -> str | None:
        value = self.node.get("status")
        return value if isinstance(value, str) and value else None

    @property
    def publisher(self) -> dict:
        publisher = self.node.get("publisher")
        return publisher if isinstance(publisher, dict) else {}

    @property
    def downloads(self) -> Any:
        return self.node.get("downloads")

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.versions:
            key = item.get("status")
            key = key if isinstance(key, str) and key else "（缺 status）"
            counts[key] = counts.get(key, 0) + 1
        return counts


def collect(
    *,
    node_id: str = NODE_ID,
    api_base: str = API_BASE,
    opener: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Any = None,
    now: datetime | None = None,
) -> Facts:
    """取回三个公开端点 → `Facts`。任何一步失败都抛 `RegistryStatusError`（不吞错）。"""
    started = time.monotonic()
    captured_at = now if now is not None else datetime.now().astimezone()
    base = api_base.rstrip("/")
    quoted_id = urllib.parse.quote(node_id, safe="")
    node_url = "%s/nodes/%s" % (base, quoted_id)
    versions_url = "%s/nodes/%s/versions" % (base, quoted_id)

    node, node_attempts = fetch_json(
        node_url, opener=opener, timeout=timeout, retries=retries, backoff=backoff, sleep=sleep
    )
    if not isinstance(node, dict):
        raise RegistryStatusError(
            "schema", node_url, "节点响应不是对象（类型 %s）" % type(node).__name__
        )
    versions_raw, versions_attempts = fetch_json(
        versions_url, opener=opener, timeout=timeout, retries=retries, backoff=backoff, sleep=sleep
    )
    versions = _version_list(versions_raw, versions_url)
    latest = _latest_of(node, node_url)

    attempts = {"node": node_attempts, "versions": versions_attempts}
    warnings: list[str] = []
    install: dict | None = None
    install_url = ""
    version = latest.get("version")
    if isinstance(version, str) and version:
        install_url = "%s/nodes/%s/install?version=%s" % (
            base, quoted_id, urllib.parse.quote(version, safe="")
        )
        raw_install, install_attempts = fetch_json(
            install_url, opener=opener, timeout=timeout, retries=retries, backoff=backoff, sleep=sleep
        )
        attempts["install"] = install_attempts
        if not isinstance(raw_install, dict):
            raise RegistryStatusError(
                "schema", install_url, "install 端点返回的不是对象（类型 %s）" % type(raw_install).__name__
            )
        install = raw_install
    else:
        warnings.append(
            "节点响应没有 latest_version.version：已跳过 /install 端点；"
            "check --expect-latest-active 会以 4 退出（无法判定）"
        )

    return Facts(
        node_id=node_id,
        api_base=base,
        captured_at=captured_at,
        elapsed_s=time.monotonic() - started,
        node=node,
        versions_raw=versions_raw,
        versions=versions,
        latest=latest,
        install=install,
        install_url=install_url,
        attempts=attempts,
        warnings=warnings,
    )


# ─────────────────────────────────────────────────────────────
# 落盘层（两阶段：先写临时目录，全部成功再整体 rename 为最终快照目录）
#
# 为什么不是「直接建最终目录再逐个文件写」：那样中途 IO 失败会留下**看起来像证据**的
# 半截快照（已写好的 node.json + 目录）。两阶段写入让「目录存在」== 「快照完整」，
# 操作者不需要凭错误输出去猜该不该清理。
# ─────────────────────────────────────────────────────────────

def _dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _next_snapshot_dir(out_root: Path, captured_at: datetime) -> Path:
    """`<out_root>/<YYYYMMDD-HHMMSS>[/-NN]`。同一秒重复跑就加序号，不覆盖旧快照。"""
    stamp = captured_at.strftime("%Y%m%d-%H%M%S")
    candidate = out_root / stamp
    if not candidate.exists():
        return candidate
    for index in range(1, 100):
        candidate = out_root / ("%s-%02d" % (stamp, index))
        if not candidate.exists():
            return candidate
    raise RegistryStatusError("io", str(out_root), "同一秒内的快照目录已存在 100 个，疑似异常")


def _new_tmp_dir(final_dir: Path) -> Path:
    """最终目录旁的临时目录名（同卷 ⇒ `os.replace` 是目录级原子 rename）。"""
    return final_dir.with_name("%s.tmp-%s" % (final_dir.name, uuid.uuid4().hex[:8]))


def _discard_tmp(tmp_dir: Path | None, out_root: Path, out_root_existed: bool) -> None:
    """失败清理：删本次临时目录；out-root 若是本次新建且已空，也一并删掉。"""
    if tmp_dir is not None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if not out_root_existed:
        try:
            out_root.rmdir()  # 只删空目录；并发进程写进来就保留
        except OSError:
            pass


def _write_snapshot(facts: Facts, out_root: Path) -> Path:
    """写一份完整快照，返回最终目录。失败时抛 `RegistryStatusError` 且不留半截目录。"""
    out_root_existed = out_root.exists()
    tmp_dir: Path | None = None
    tmp_created = False
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        final_dir = _next_snapshot_dir(out_root, facts.captured_at)
        tmp_dir = _new_tmp_dir(final_dir)
        tmp_dir.mkdir()
        tmp_created = True
        _write_atomic(tmp_dir / "node.json", _dump_json(facts.node))
        _write_atomic(tmp_dir / "versions.json", _dump_json(facts.versions_raw))
        if facts.install is not None:
            _write_atomic(tmp_dir / "install.json", _dump_json(facts.install))
        _write_atomic(tmp_dir / "meta.json", _dump_json(_meta(facts)))
        _write_atomic(tmp_dir / "summary.md", render_summary(facts))
        os.replace(tmp_dir, final_dir)  # 同卷 rename：最终目录要么不存在，要么完整
        tmp_dir = None
        return final_dir
    except (OSError, RegistryStatusError) as exc:
        _discard_tmp(tmp_dir if tmp_created else None, out_root, out_root_existed)
        if isinstance(exc, RegistryStatusError):
            raise
        raise RegistryStatusError(
            "io", str(out_root), "快照写入失败（已删除本次临时目录）：%s" % exc
        ) from exc


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# ─────────────────────────────────────────────────────────────
# 人读输出
# ─────────────────────────────────────────────────────────────

def _fmt_local(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S %z")


def _fmt_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status_meaning(status: str | None) -> str:
    if status is None:
        return "（没有可判定的版本状态）"
    return STATUS_MEANING.get(status, "未在预期枚举内，需人工确认")


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "（空/未提供）"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _counts_text(counts: dict[str, int]) -> str:
    if not counts:
        return "（无版本）"
    return "，".join("%s=%d" % (key, value) for key, value in counts.items())


def next_steps(status: str | None) -> list[str]:
    """按当前状态给「下一步」，全部指向 `CHANGES` / git 历史 的既定流程。"""
    if status == STATUS_ACTIVE:
        return [
            "最新版本已是 Active：无需跟进动作；发布闸可放行，但仍按 CHANGES 里的发布前后 24h 清单复验。",
            "历史版本状态仍可能不同，复核用 snapshot 的 versions.json。",
        ]
    if status in (STATUS_FLAGGED, STATUS_PENDING):
        return [
            "按 CHANGES 里的英文联络稿，向 Registry 团队 / Comfy support 索取 moss-storyboard "
            "各版本的扫描 issues 清单（公开 API 不提供）。",
            "拿到清单后再二分：确认误报 → 请 Registry 走人工复核（Approve → Active）；"
            "确认代码/打包问题 → 修代码并发新的 patch 版本（新版本默认 Pending，等扫描）。",
            "不要重发同一版本号、不要伪造 status；不要用 git 直装对外宣称安装问题已解决。",
            "对外只写「待安全复核」：Flagged 是「扫描有发现、等复核」，不是安全证明，也不是恶意判定。",
            "用户侧不推荐常开 allow_flagged_nodepack_install；只如实说明 loopback 桌面/便携版按 "
            "Manager 指引可安装（语义见 CHANGES）。",
        ]
    if status == STATUS_BANNED:
        return [
            "最新版本被 Banned（已拒绝/下架）：立即联系 Comfy 支持确认原因与下架范围，"
            "不要用 git/link 安装对外宣称「问题已解决」。",
            "评估是否发布新的 patch 版本（新版本默认 Pending 并重新扫描）。",
            "不要重发同一版本号、不要伪造 status。",
        ]
    return [
        "状态不在预期集合内（如 Deleted）：到 https://registry.comfy.org/nodes/moss-storyboard "
        "人工核对，**不要**自动放行。",
        "复核后按 CHANGES 的决策树处理；把本次 snapshot 目录作为证据保留。",
    ]


def _check_exit_code(facts: Facts, *, expect_latest_active: bool) -> int:
    """`check` 的退出码。节点级 Banned 优先判 3；版本状态不在表内判 4。"""
    if facts.node_status == NODE_STATUS_BANNED:
        return EXIT_BANNED
    if not expect_latest_active:
        return EXIT_OK
    status = facts.latest_status
    if status is None:
        return EXIT_ERROR
    return STATUS_EXIT.get(status, EXIT_ERROR)


def render_check(facts: Facts, *, expect_latest_active: bool, exit_code: int) -> str:
    """人读的 check 结论 + 下一步（不写文件、不判「安全/不安全」）。"""
    counts = facts.status_counts()
    publisher = facts.publisher
    publisher_text = "%s / %s" % (
        _cell(publisher.get("id")),
        _cell(publisher.get("status")),
    )
    lines = [
        "Registry 状态检查 · %s" % facts.node_id,
        "",
        "  采集时间：%s（UTC %s）" % (_fmt_local(facts.captured_at), _fmt_utc(facts.captured_at)),
        "  数据来源：%s（公开只读 API，本次实测）" % facts.api_base,
        "  节点状态：%s" % _cell(facts.node_status),
        "  发布者：%s" % publisher_text,
        "  最新版本：%s → %s" % (_cell(facts.latest_version), _cell(facts.latest_status)),
        "  状态含义：%s" % _status_meaning(facts.latest_status),
        "  status_reason：%s" % _cell(facts.latest.get("status_reason")),
        "  downloads：%s（动态计数，只作参考）" % _cell(facts.downloads),
        "  历史版本：%d 个（%s）" % (len(facts.versions), _counts_text(counts)),
        "  安装端点：%s" % (
            "HTTP 200，downloadUrl=%s" % _cell(facts.install.get("downloadUrl"))
            if facts.install is not None
            else "（已跳过：没有 latest_version.version）"
        ),
    ]
    if facts.warnings:
        lines.append("  警告：%s" % "；".join(facts.warnings))
    if exit_code == EXIT_OK and not expect_latest_active:
        lines.extend([
            "",
            "结论：取数成功（本模式**不判定发布门槛**）。",
            "下一步：发布闸请运行 check --expect-latest-active；日常跟进可看 CHANGES。",
        ])
        return "\n".join(lines)

    conclusion = {
        EXIT_OK: "通过：最新版本是 Active。",
        EXIT_UNEASY: "未通过：最新版本处于待安全复核（Flagged / Pending），不是 Active。",
        EXIT_BANNED: "未通过：版本或节点被 Banned。",
        EXIT_ERROR: "无法判定：取数成功但状态不可判定（见上方警告/状态值）。",
    }[exit_code]
    lines.extend(["", "结论：" + conclusion, "下一步（CHANGES / git 历史）："])
    for index, step in enumerate(next_steps(facts.latest_status), 1):
        lines.append("  %d. %s" % (index, step))
    lines.append("")
    lines.append("退出码：%d" % exit_code)
    return "\n".join(lines)


def render_summary(facts: Facts) -> str:
    """`summary.md`：人读的采集事实 + 状态分布 + 下一步。不含 changelog、不含本机路径。"""
    counts = facts.status_counts()
    publisher = facts.publisher
    lines = [
        "# Registry 状态快照 · %s" % facts.node_id,
        "",
        "> 采集时间：%s（UTC %s）" % (_fmt_local(facts.captured_at), _fmt_utc(facts.captured_at)),
        "> 数据来源：%s（公开只读 API，本次采集实测）" % facts.api_base,
        "> 工具：`tools/registry_status.py`（标准库，零第三方依赖）；语义依据见 `CHANGES`。",
        "",
        "## 1. 节点",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| node id | %s |" % _cell(facts.node.get("id")),
        "| 节点状态 | %s |" % _cell(facts.node_status),
        "| 发布者 | %s / %s |" % (_cell(publisher.get("id")), _cell(publisher.get("status"))),
        "| downloads | %s（动态计数，只作参考） |" % _cell(facts.downloads),
        "",
        "## 2. 最新版本",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| version | %s |" % _cell(facts.latest_version),
        "| status | %s |" % _cell(facts.latest_status),
        "| 状态含义 | %s |" % _status_meaning(facts.latest_status),
        "| status_reason | %s |" % _cell(facts.latest.get("status_reason")),
        "| createdAt | %s |" % _cell(facts.latest.get("createdAt")),
        "| deprecated | %s |" % _cell(facts.latest.get("deprecated")),
        "",
        "## 3. 历史版本（%d 个）" % len(facts.versions),
        "",
        "| version | status | status_reason | createdAt | deprecated |",
        "|---|---|---|---|---|",
    ]
    for item in facts.versions:
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (
                _cell(item.get("version")),
                _cell(item.get("status")),
                _cell(item.get("status_reason")),
                _cell(item.get("createdAt")),
                _cell(item.get("deprecated")),
            )
        )
    lines.extend([
        "",
        "## 4. 状态分布",
        "",
        "`%s`" % _counts_text(counts),
        "",
        "## 5. 安装信息端点",
        "",
    ])
    if facts.install is None:
        lines.append("- 未请求：节点响应里没有 `latest_version.version`（见「警告」）。")
    else:
        lines.extend([
            "- 请求：`GET %s`" % facts.install_url,
            "- 响应：HTTP 200；`downloadUrl` = %s" % _cell(facts.install.get("downloadUrl")),
            "- `dependencies` = %s；`deprecated` = %s"
            % (_cell(facts.install.get("dependencies")), _cell(facts.install.get("deprecated"))),
            "- 说明：安装端点**不按版本状态拦截**（依据见 `CHANGES`，已验证：公开源码 + "
            "API 实测）。本快照只记录回包事实，**不**据此推断「该版本安全」或「安装一定成功」。",
        ])
    lines.extend(["", "## 6. 警告", ""])
    if facts.warnings:
        lines.extend("- %s" % warning for warning in facts.warnings)
    else:
        lines.append("- 无（三个端点均取到预期结构）。")
    lines.extend(["", "## 7. 下一步（`CHANGES` / git 历史）", ""])
    lines.extend("%d. %s" % (index, step) for index, step in enumerate(next_steps(facts.latest_status), 1))
    lines.extend([
        "",
        "## 8. 本次采集的请求记录",
        "",
        "| 端点 | 请求次数 | 请求 |",
        "|---|---|---|",
    ])
    lines.append("| 节点 | %d | `GET %s/nodes/%s` |" % (
        facts.attempts.get("node", 0), facts.api_base, facts.node_id))
    lines.append("| 版本列表 | %d | `GET %s/nodes/%s/versions` |" % (
        facts.attempts.get("versions", 0), facts.api_base, facts.node_id))
    lines.append("| 安装信息 | %s | %s |" % (
        facts.attempts.get("install", "未请求"),
        "`GET %s`" % facts.install_url if facts.install_url else "（跳过）",
    ))
    lines.append("")
    return "\n".join(lines)


def _meta(facts: Facts) -> dict:
    """机器可读的采集元数据（`meta.json`）。只记录相对量，不写本机绝对路径。"""
    return {
        "tool": "tools/registry_status.py",
        "tool_version": "1.0",
        "node_id": facts.node_id,
        "api_base": facts.api_base,
        "captured_at_local": _fmt_local(facts.captured_at),
        "captured_at_utc": _fmt_utc(facts.captured_at),
        "elapsed_s": round(facts.elapsed_s, 3),
        "attempts": facts.attempts,
        "urls": {
            "node": "%s/nodes/%s" % (facts.api_base, facts.node_id),
            "versions": "%s/nodes/%s/versions" % (facts.api_base, facts.node_id),
            "install": facts.install_url or None,
        },
        "node_status": facts.node_status,
        "publisher": {
            "id": facts.publisher.get("id"),
            "status": facts.publisher.get("status"),
        },
        "downloads": facts.downloads,
        "latest_version": facts.latest_version,
        "latest_status": facts.latest_status,
        "latest_status_reason": facts.latest.get("status_reason"),
        "latest_created_at": facts.latest.get("createdAt"),
        "versions_total": len(facts.versions),
        "status_counts": facts.status_counts(),
        "install_download_url": (facts.install or {}).get("downloadUrl"),
        "warnings": facts.warnings,
        "exit_code_if_checked": _check_exit_code(facts, expect_latest_active=True),
    }


# ─────────────────────────────────────────────────────────────
# 子命令实现
# ─────────────────────────────────────────────────────────────

def _print_error(exc: RegistryStatusError) -> None:
    print("错误（%s）：%s" % (exc.kind, exc.detail), file=sys.stderr)
    print("  位置：%s" % exc.location, file=sys.stderr)
    print("  说明：退出码 4 只表示「没取到可判定的数据」，**不是** Registry 状态结论。", file=sys.stderr)
    print(
        "  下一步：确认网络/代理能访问 %s 后重试；持续失败把本条输出交给维护者。" % API_BASE,
        file=sys.stderr,
    )


def _run_snapshot(facts: Facts, args: argparse.Namespace) -> int:
    out_root = Path(args.out_root)
    try:
        out_dir = _write_snapshot(facts, out_root)
    except RegistryStatusError as exc:
        print("错误（%s）：快照落盘失败：%s" % (exc.kind, exc.detail), file=sys.stderr)
        print("  位置：%s" % exc.location, file=sys.stderr)
        print("  说明：退出码 4 只表示「没落盘」，不是 Registry 状态结论。", file=sys.stderr)
        print("  清理：已删除本次临时目录；不会留下半截快照目录。", file=sys.stderr)
        return EXIT_ERROR

    written = ["node.json", "versions.json"]
    if facts.install is not None:
        written.append("install.json")
    written.extend(["meta.json", "summary.md"])
    print("快照已落盘：%s" % _display_path(out_dir))
    print("  文件：%s" % " / ".join(written))
    print("  最新版本：%s → %s" % (
        facts.latest_version or "（无）", facts.latest_status or "（无）"))
    print("  downloads：%s（采集时刻）" % _cell(facts.downloads))
    print("  历史版本：%d 个（%s）" % (len(facts.versions), _counts_text(facts.status_counts())))
    if facts.warnings:
        print("  警告：%s" % "；".join(facts.warnings))
    print("下一步：")
    for index, step in enumerate(next_steps(facts.latest_status), 1):
        print("  %d. %s" % (index, step))
    return EXIT_OK


def _run_check(facts: Facts, args: argparse.Namespace) -> int:
    expect = bool(args.expect_latest_active)
    exit_code = _check_exit_code(facts, expect_latest_active=expect)
    if args.json:
        payload = {
            "node_id": facts.node_id,
            "api_base": facts.api_base,
            "captured_at_utc": _fmt_utc(facts.captured_at),
            "captured_at_local": _fmt_local(facts.captured_at),
            "node_status": facts.node_status,
            "publisher": {
                "id": facts.publisher.get("id"),
                "status": facts.publisher.get("status"),
            },
            "downloads": facts.downloads,
            "latest_version": facts.latest_version,
            "latest_status": facts.latest_status,
            "latest_status_reason": facts.latest.get("status_reason"),
            "versions_total": len(facts.versions),
            "status_counts": facts.status_counts(),
            "install_download_url": (facts.install or {}).get("downloadUrl"),
            "expect_latest_active": expect,
            "exit_code": exit_code,
            "next_steps": next_steps(facts.latest_status),
            "warnings": facts.warnings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_check(facts, expect_latest_active=expect, exit_code=exit_code))
    return exit_code


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--node-id", default=NODE_ID, help="Registry 节点 id（默认 %(default)s）")
    parser.add_argument("--api-base", default=API_BASE, help="Registry API 根（默认 %(default)s）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="单次请求超时秒数（默认 %(default)s）")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                        help="失败重试次数；总请求 = 1 + N（默认 %(default)s）")
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF,
                        help="第 n 次重试前等 backoff*n 秒（默认 %(default)s）")


def _parse_args(argv: Any) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="registry_status.py",
        description="Comfy Registry 版本状态取证（snapshot）与断言（check）；只打公开只读 API。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser(
        "snapshot", help="抓取三个端点并原子落盘 raw JSON + meta.json + summary.md"
    )
    _add_common_arguments(snapshot)
    snapshot.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT),
                          help="快照根目录（默认 %(default)s）")

    check = subparsers.add_parser("check", help="按最新版本状态给退出码（Active=0 / Flagged,Pending=2 / "
                                                "Banned=3 / 取数失败=4）")
    _add_common_arguments(check)
    check.add_argument("--expect-latest-active", action="store_true",
                       help="按状态断言（发布闸用）；不加时只探测，取数成功即 0")
    check.add_argument("--json", action="store_true", help="输出机器可读 JSON（退出码语义不变）")

    return parser.parse_args(argv)


def main(
    argv: Any = None,
    *,
    opener: Any = None,
    now: datetime | None = None,
    sleep: Any = None,
) -> int:
    """CLI 入口。`opener` / `now` / `sleep` 是给离线测试用的注入点（默认走真实网络与时钟）。"""
    args = _parse_args(argv)
    try:
        facts = collect(
            node_id=args.node_id,
            api_base=args.api_base,
            opener=opener,
            timeout=args.timeout,
            retries=args.retries,
            backoff=args.backoff,
            sleep=sleep,
            now=now,
        )
    except RegistryStatusError as exc:
        _print_error(exc)
        return EXIT_ERROR
    if args.command == "snapshot":
        return _run_snapshot(facts, args)
    return _run_check(facts, args)


if __name__ == "__main__":
    sys.exit(main())
