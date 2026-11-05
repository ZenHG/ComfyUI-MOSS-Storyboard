# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""一键装/更新 llama.cpp 运行时（llama-server + 运行库）。

为什么自带安装器而不是复用别的节点包：本包要**独立可分装**——开源用户只装这一个包时，
别人的 runtime 目录不一定存在，去扫别人的目录等于把隐性依赖写进代码。
所以这里独立实现，只做三件事：
探本机后端 → 按后端挑官方预编译包 → 下载解包到 moss_llm/runtime/。

    python -m moss_llm.runtime_install                    # 装 / 更新
    python -m moss_llm.runtime_install --check            # 只看本机与远端
    python -m moss_llm.runtime_install --backend vulkan   # 手动指定后端

环境变量：
    MOSS_LLM_BACKEND=cuda|vulkan|rocm|sycl|cpu|metal    覆盖自动探测
    MOSS_LLM_MIRROR=<前缀>                              指定镜像（查 API 与下载都用）
    MOSS_LLM_MIRRORS=<前缀1,前缀2>                       替换内置镜像链
    MOSS_LLM_RUNTIME_URL=<某个 release 包直链>           跳过查询，直接下这个包

镜像只用来**下载**，且每个包都必须通过 GitHub API 给的 sha256 校验才落盘；
没有摘要的包不启用第三方镜像（无法证伪被篡改）。
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from functools import lru_cache
from pathlib import Path

REPO = "ggml-org/llama.cpp"
API = "https://api.github.com/repos/" + REPO
def _runtime_dir():
    """默认在包内 `moss_llm/runtime/`；`MOSS_LLM_RUNTIME_DIR` 可改到持久目录。"""
    env = os.environ.get("MOSS_LLM_RUNTIME_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent / "runtime"


RUNTIME_DIR = _runtime_dir()
VERSION_FILE = "VERSION"
UA = {"User-Agent": "moss-llm-runtime-installer"}

#: 平台 → 官方资产名里的片段；macOS 的 Metal 是内建的，没有单独的后端 token
_PF = {"windows": "win", "linux": "ubuntu", "macos": "macos"}
_ARCH = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
_BACKENDS = ("cuda", "vulkan", "rocm", "sycl", "cpu", "metal")
#: 内置兜底镜像（实测可代取 release 资产与 API）。可用 MOSS_LLM_MIRRORS 整体替换。
_MIRRORS = ("https://gh-proxy.com/", "https://ghproxy.net/")


def _mirrors():
    env = os.environ.get("MOSS_LLM_MIRRORS", "").strip()
    if env:
        return [m.strip().rstrip("/") + "/" for m in env.split(",") if m.strip()]
    return list(_MIRRORS)


def _host(url):
    p = urllib.parse.urlsplit(url)
    return p.netloc or p.scheme or url


def _api_sources(url):
    out = []
    one = os.environ.get("MOSS_LLM_MIRROR", "").strip()
    if one:
        out.append(one.rstrip("/") + "/" + url)
    out.append(url)
    out += [m + url for m in _mirrors()]
    return list(dict.fromkeys(out))


def _get(url, timeout=30):
    """读 JSON：直连失败才走镜像（有的镜像只代 release 资产，不代 API）。"""
    errs = []
    for src in _api_sources(url):
        try:
            req = urllib.request.Request(src, headers=dict(UA, Accept="application/vnd.github+json"))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            errs.append("%s：%s" % (_host(src), str(e).splitlines()[0][:70]))
    raise RuntimeError("查 GitHub 失败（直连 + 镜像）：\n    " + "\n    ".join(errs))


def _ver(s):
    """'13.4' -> (13, 4)，用于版本比较。"""
    try:
        return tuple(int(x) for x in (s or "0").split("."))
    except ValueError:
        return (0,)


# ------------------------------------------------------------------ 探测

#: 型号关键词 -> 计算能力，依据 NVIDIA 官方 CUDA GPUs 列表（常见桌面/工作站卡）
_GPU_CC = (
    ("rtx 50", "12.0"), ("rtx 40", "8.9"), ("rtx 30", "8.6"), ("rtx 20", "7.5"),
    ("gtx 16", "7.5"), ("gtx 10", "6.1"), ("gtx 9", "5.2"), ("gtx 7", "5.0"),
    ("quadro rtx", "7.5"), ("quadro k", "3.0"), ("quadro m", "5.0"),
    ("quadro p", "6.1"), ("a100", "8.0"), ("h100", "9.0"), ("l40", "8.9"),
    ("tesla v", "7.0"),
)
_CUDA13_MIN_CC = (7, 5)   # CUDA 13 起最低要求 Turing(sm_75)


@lru_cache(maxsize=1)
def gpu_info():
    """返回 (厂商, 设备名原文)。

    不依赖 nvidia-smi：它在沙箱/驱动异常/远程会话下会失败，而系统设备查询仍认得出硬件，
    能区分「没装驱动」和「没这个硬件」。
    """
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=30).stdout or ""
        elif sys.platform == "darwin":
            out = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                                 capture_output=True, text=True, timeout=30).stdout or ""
        else:
            out = subprocess.run(["lspci"], capture_output=True, text=True,
                                 timeout=20).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None, ""
    low = out.lower()
    for vendor, keys in (("nvidia", ("nvidia", "geforce", "rtx", "quadro")),
                         ("amd", ("radeon", "amd")),
                         ("intel", ("intel", "arc"))):
        if any(k in low for k in keys):
            return vendor, out.strip()
    return None, out.strip()


def cuda_cap_from_name(name):
    """型号 -> 可用最高 CUDA；识别不了返回 None，交由调用方提示用户手选。"""
    n = (name or "").lower()
    for kw, cc in _GPU_CC:
        if kw in n:
            major, minor = (int(x) for x in cc.split("."))
            return (13, 9) if (major, minor) >= _CUDA13_MIN_CC else (12, 9)
    return None


def cuda_from_smi():
    """nvidia-smi 报告的驱动最高 CUDA 版本；拿不到返回 None。"""
    root = os.environ.get("SystemRoot")
    cmds = ([str(Path(root) / "System32" / "nvidia-smi.exe")]
            if os.name == "nt" and root else []) + ["nvidia-smi"]
    for c in cmds:
        try:
            out = subprocess.run([c], capture_output=True, text=True, timeout=25).stdout or ""
        except (OSError, subprocess.SubprocessError):
            continue
        hit = re.search(r"CUDA Version\s*:\s*(\d+)\.(\d+)", out)
        if hit:
            return (int(hit.group(1)), int(hit.group(2)))
    return None


def detect():
    """-> (平台-架构, 后端, 驱动最高支持 CUDA 版本 or None)。

    厂商识别不了退 CPU（任何机器都能跑，只是慢），绝不为了「看起来能用」硬塞 CUDA 包。
    """
    pf = ("windows" if sys.platform.startswith("win")
          else "macos" if sys.platform == "darwin" else "linux")
    plat = "%s-%s" % (pf, _ARCH.get((platform.machine() or "").lower(), "x64"))
    if pf == "macos":
        return plat, "metal", None
    env = (os.environ.get("MOSS_LLM_BACKEND") or "").strip().lower()
    if env in _BACKENDS:
        return plat, env, cuda_from_smi()
    vendor, name = gpu_info()
    cuda = cuda_from_smi() or cuda_cap_from_name(name)
    if vendor == "nvidia" or cuda:
        return plat, "cuda", cuda
    if vendor == "amd":
        return plat, ("rocm" if pf == "linux" else "vulkan"), None
    if vendor == "intel":
        return plat, "vulkan", None
    return plat, "cpu", None


# ------------------------------------------------------------------ 选包

def _entry(a):
    return (a["browser_download_url"], a["name"], a.get("size"),
            (a.get("digest") or "").replace("sha256:", "") or None)


def _cudart_map(assets, tok, arch):
    """本 build 里所有 `cudart-…-cuda-<ver>-<arch>` 运行库：{版本串: entry}。"""
    pat = re.compile(r"^cudart-llama-(?:b\d+-)?bin-%s-cuda-([\d.]+)-%s\.(?:zip|tar\.gz)$"
                     % (re.escape(tok), re.escape(arch)))
    out = {}
    for a in assets or []:
        m = pat.match((a.get("name") or "").lower())
        if m:
            out[m.group(1)] = _entry(a)
    return out


def _cudart(assets, tok, arch, ver):
    """同版本的运行库包。**不同版本不给**。

    旧版找不到同版本就退回最低版本，于是会出现「主包 cuda-13.4 + 运行库 cuda-12.4」——
    装完是加载失败，而错误信息只会说某个 DLL 找不到，和真实原因无关。
    宁可返回 None（上层据此换 build），也不给一个装不起来的组合。
    """
    for k, e in _cudart_map(assets, tok, arch).items():
        if _ver(k) == _ver(ver):
            return e
    return None



def _pick(assets, tag, plat, backend, max_cuda):
    """从给定 build 的 assets 里挑主包(+cudart)；返回 [(url, name, size, sha256), ...]。

    纯函数（不联网），供多个 build 从新到旧试探。三种资产命名都要认：
      · windows:  llama-<tag>-bin-win-<backend>-<cudaver>-x64.zip
      · linux:    llama-<tag>-bin-ubuntu-<arch>.tar.gz        ← CPU 包**没有** backend token
      · macos:    llama-<tag>-bin-macos-<arch>.tar.gz         ← Metal 内建，也没有 token
    """
    pf, arch = plat.split("-", 1)
    tok = _PF[pf]
    pat = re.compile(r"^llama-%s-bin-%s-(?:([a-z0-9]+?)(?:-([\d.]+))?-)?%s\.(?:zip|tar\.gz)$"
                     % (re.escape(tag.lower()), re.escape(tok), re.escape(arch)))
    by = {}
    for a in assets or []:
        m = pat.match((a.get("name") or "").lower())
        if not m:
            continue
        be = "metal" if pf == "macos" else (m.group(1) or "cpu")
        by.setdefault(be, []).append((m.group(2) or "0", a))
    if pf == "macos":
        lst = by.get("metal") or []
        return [_entry(lst[0][1])] if lst else []
    # 要哪个后端就给哪个后端。**不静默换**：给 Intel 卡装 cuda 包等于装完跑不起来，
    # 而报错会指向「显卡驱动」——和真实原因（选错包）无关。这个 build 没有就返回空，
    # 由 `latest_build` 往更旧的 build 找；全都没有就报错让人选。
    # （旧版这里会 fallback 到 cuda/vulkan/cpu，等于替用户做了个它看不见的决定。）
    if backend not in by:
        return []
    lst = by[backend]
    if backend != "cuda":
        return [_entry(lst[0][1])]
    if max_cuda:                       # 不让包超过驱动能力（老驱动跑不了新 CUDA）
        lst = [x for x in lst if _ver(x[0]) <= max_cuda]
        if not lst:
            return []
        picker = max
    else:
        # 驱动未知时选最低版本：CUDA 包对旧架构不向下兼容，低版可借 PTX 跑新卡
        picker = min
    # CUDA 主包没有运行库跑不起来，而运行库必须**同版本**。所以只在「有配套运行库」的
    # 版本里挑；一个都没有就返回空，让上层换 build 或在报错里说清。
    cud_missing = not _cudart_map(assets, tok, arch)
    if not cud_missing:
        lst = [x for x in lst if _cudart(assets, tok, arch, x[0])]
        if not lst:
            return []
    ver, a = picker(lst, key=lambda x: _ver(x[0]))
    out = [_entry(a)]
    cud = None if cud_missing else _cudart(assets, tok, arch, ver)
    return out + ([cud] if cud else [])


def latest_build(plat, backend, max_cuda):
    """最新且真带本平台可用包的 b<build>；返回 (tag, [(url, name, size, sha256), ...])。

    不能用 /releases/latest：它返回语义化版本 vX.Y.Z，预编译包挂在 b<build> 标签上。
    """
    rels = []
    for r in _get(API + "/releases?per_page=60"):
        m = re.match(r"^b(\d+)$", r.get("tag_name") or "")
        if m:
            rels.append((int(m.group(1)), r.get("tag_name"), r.get("assets") or []))
    for _n, tag, assets in sorted(rels, reverse=True):
        got = _pick(assets, tag, plat, backend, max_cuda)
        if got:
            return tag, got
    return None, []


def build_for(tag, plat, backend, max_cuda):
    """指定 build 标签的可用包。找不到返回 []（由调用方报错，不悄悄换成别的版本）。"""
    if not tag:
        return []
    for r in _get(API + "/releases?per_page=60"):
        if (r.get("tag_name") or "") == tag:
            return _pick(r.get("assets") or [], tag, plat, backend, max_cuda)
    return []



# ------------------------------------------------------------------ 安装

def installed_tag():
    p = RUNTIME_DIR / VERSION_FILE
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8").strip() or None


def find_exe():
    for base in (RUNTIME_DIR, RUNTIME_DIR / "build" / "bin"):
        if base.is_dir():
            # 只认 .exe：Win32 上 os.access(X_OK) 对任何存在的文件都为真，
            # 早先的写法会把 llama-server-impl.dll 也收进来，而按名字排序时
            # 「-」排在「.」前面 ⇒ 返回 DLL，Popen 直接 WinError 193。
            hits = sorted(
                (h for h in base.glob("**/llama-server*")
                 if h.is_file() and h.suffix.lower() == ".exe"),
                key=lambda h: (h.name.lower() != "llama-server.exe", str(h)),
            )
            if hits:
                return str(hits[0])
    return None


class TooSlow(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _matches(path, size, digest):
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    return not digest or _sha256(path) == digest


def _fetch(src, part, min_speed=30 * 1024):
    """把 src 流式写进 .part。同一来源可续传（`.src` 记来源，换源必须重下）。"""
    p = urllib.parse.urlsplit(src)
    scheme, host = p.scheme, (p.netloc or "file")
    srcfile = part.with_name(part.name + ".src")
    pos = 0
    if scheme in ("http", "https") and part.is_file() and srcfile.is_file():
        try:
            if srcfile.read_text(encoding="utf-8").strip() == host:
                pos = part.stat().st_size
        except OSError:
            pos = 0
    if not pos:
        part.unlink(missing_ok=True)
        if scheme in ("http", "https"):
            srcfile.write_text(host, encoding="utf-8")
    req = urllib.request.Request(src, headers=UA)
    if pos:
        req.add_header("Range", "bytes=%d-" % pos)
        print("  续传 %s（已有 %d MiB）…" % (part.name, pos >> 20))
    else:
        print("  下载 %s ← %s …" % (part.name, host))
    with urllib.request.urlopen(req, timeout=20) as r:
        mode = "ab" if (pos and getattr(r, "status", None) == 206) else "wb"
        if mode == "wb":
            pos = 0
        total = int(r.headers.get("Content-Length") or 0) + pos
        got, t0 = 0, time.time()
        tick = _bar(total)          # 前端进度条（没有宿主时是空操作）
        with open(part, mode) as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                tick(len(chunk))
                got += len(chunk)
                pos += len(chunk)
                el = time.time() - t0
                if el >= 5:                      # 每 5 秒评估一次，别卡死在极慢节点
                    if got / el < min_speed:
                        raise TooSlow("%.0f KB/s < %.0f KB/s" % (got / el / 1024,
                                                                 min_speed / 1024))
                    got, t0 = 0, time.time()
                if total:
                    print("\r    %5.1f%%  (%d MiB)" % (pos * 100.0 / total, pos >> 20),
                          end="", flush=True)
    print()
    if total and pos != total:
        raise RuntimeError("不完整（%d/%d 字节）" % (pos, total))


def _bar(total, label=""):
    """把进度推给 ComfyUI 前端（有 UI 时走它自己的进度条）；不在 ComfyUI 里跑时静默降级。

    只用官方入口 `comfy.utils.ProgressBar`——它内部就是 PromptServer 的 progress 事件，
    前端已经会画那一圈进度；不自己造事件名，避免和宿主版本漂移。
    """
    try:
        from comfy.utils import ProgressBar
        bar = ProgressBar(int(total) if total else 1)
        return lambda n=1: bar.update(int(n) if n else 1)
    except Exception:
        return lambda n=1: None


def _download(url, dest, size=None, digest=None, sources=None):
    """依次尝试各来源，只有**大小与 sha256 都对**才落盘；返回 (路径, 实际来源)。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size and _matches(dest, size, digest):
        print("用缓存 %s" % dest.name)
        return dest, "缓存"
    dest.unlink(missing_ok=True)
    part = dest.with_name(dest.name + ".part")
    tried = []
    for src in (sources if sources is not None
                else _sources(url, allow_mirror=bool(digest))):
        try:
            _fetch(src, part)
        except Exception as e:
            msg = str(e).splitlines()[0][:80]
            tried.append("%s：%s" % (_host(src), msg))
            print("  ✗ %s：%s" % (_host(src), msg))
            continue
        if not _matches(part, size, digest):
            part.unlink(missing_ok=True)
            part.with_name(part.name + ".src").unlink(missing_ok=True)
            tried.append("%s：大小/摘要不符" % _host(src))
            print("  ✗ %s：大小/摘要与 GitHub 记录不符，已丢弃" % _host(src))
            continue
        part.replace(dest)
        part.with_name(part.name + ".src").unlink(missing_ok=True)
        return dest, _host(src)
    raise RuntimeError(
        "下载失败：所有来源都不可用。\n"
        "    资产：%s%s\n    失败：\n    %s\n"
        "    三条路：① 设 MOSS_LLM_MIRROR=<镜像前缀> 重试（或用 MOSS_LLM_MIRRORS=前缀1,前缀2）；"
        "② 手动下载这个文件放到 %s；③ 手动解压后把 llama-server 目录填进「服务器程序」。"
        % (dest.name, ("（sha256 %s…）" % digest[:12]) if digest else "",
           "\n    ".join(tried), dest.parent))


def _sources(url, allow_mirror=True):
    out = []
    one = os.environ.get("MOSS_LLM_MIRROR", "").strip()
    if one:
        out.append(one.rstrip("/") + "/" + url)
    out.append(url)
    if allow_mirror:
        out += [m + url for m in _mirrors()]
    return list(dict.fromkeys(out))


def _unpack(path, dest):
    """解压；逐成员推进度（大包有上千个文件，一次 extractall 前端会长时间无反馈）。"""
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
            tick = _bar(len(members))
            for m in members:
                zf.extract(m, dest)
                tick(1)
    else:
        with tarfile.open(path) as tf:
            members = tf.getmembers()
            tick = _bar(len(members))
            for m in members:
                try:
                    tf.extract(m, dest, filter="data")
                except TypeError:      # Python < 3.12 没有 filter 参数
                    tf.extract(m, dest)
                tick(1)


def _exe_ok(exe, tries=2):
    """装完立刻验一次：能跑 `--version` 才算装好。

    防的是「解压还没落盘完就被 Popen」那类 WinError 193 —— 文件在，但不是完整的可执行文件。
    失败重试一次（落盘/杀软扫描常有秒级延迟），仍失败就报清楚，而不是让运行时去撞 193。
    """
    if not exe:
        return False
    for _ in range(tries):
        try:
            p = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
            if p.returncode == 0 and "version" in ((p.stdout or "") + (p.stderr or "")).lower():
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def install(force=False, backend="", tag=""):
    """装/更新。返回 (exe 路径, tag, 来源说明)。已是最新且不 force 时直接返回现有。

    `backend` / `tag` 是给命令行用的显式覆盖（`--backend` / `--tag`）：
    不填就和以前一样自动探测 + 取最新。**不猜**——指定的 tag 不存在就报错，
    不悄悄换成别的版本。
    """
    plat, detected, max_cuda = detect()
    backend = (backend or "").strip() or detected
    direct = os.environ.get("MOSS_LLM_RUNTIME_URL", "").strip()
    if direct:
        tag, want = None, [(direct, os.path.basename(direct.split("?")[0]), None, None)]
    elif tag:
        want = build_for(tag, plat, backend, max_cuda)
        if not want:
            raise RuntimeError(
                "官方 release 里 %s 没有 %s/%s 的 %s 包。用别的 tag，或省略 --tag 取最新。"
                % (tag, plat, backend, backend))
    else:
        tag, want = latest_build(plat, backend, max_cuda)
        if not want:
            raise RuntimeError(
                "拿不到 %s 的可用预编译包（平台 %s，后端 %s）。\n"
                "    手动下：https://github.com/%s/releases（选含 %s 的包），"
                "再把 llama-server 所在目录填进「🖥️ LLM 服务器程序」；\n"
                "    或用 MOSS_LLM_RUNTIME_URL 指直链，MOSS_LLM_BACKEND 指定后端。"
                % (REPO, plat, backend, REPO, backend))

    exe = find_exe()
    if tag and installed_tag() == tag and exe and not force:
        print("已是最新（%s），跳过下载。" % tag)
        return exe, tag, "已安装"

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    used = []
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "x"
        for url, name, size, digest in want:
            _path, src = _download(url, RUNTIME_DIR / "_downloads" / name, size, digest)
            used.append(src)
            _unpack(_path, stage)
        hits = sorted(p for p in stage.glob("**/llama-server*") if p.is_file())
        if not hits:
            raise RuntimeError("下载的包里没有 llama-server（%s）。" % [n for _, n, _, _ in want])
        # 先解到 .new，成功后再换——避免下到一半把可用的砸了。
        # 摊平：主包与 cudart 包各有自己的顶层目录，动态库必须和 exe 同级才找得到。
        new_dir = RUNTIME_DIR.with_name("runtime.new")
        if new_dir.exists():
            shutil.rmtree(new_dir, ignore_errors=True)
        new_dir.mkdir(parents=True)
        for p in stage.rglob("*"):
            if p.is_file():
                shutil.copy2(p, new_dir / p.name)
        old_dir = RUNTIME_DIR.with_name("runtime.old")
        if old_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)
        if RUNTIME_DIR.exists():
            RUNTIME_DIR.rename(old_dir)
        new_dir.rename(RUNTIME_DIR)
        shutil.rmtree(old_dir, ignore_errors=True)
    if tag:
        (RUNTIME_DIR / VERSION_FILE).write_text(tag + "\n", encoding="utf-8")
    exe_now = find_exe()
    if not _exe_ok(exe_now):
        raise RuntimeError(
            "运行时文件已解出，但 llama-server 起不来（多半是防病毒拦截或落盘不完整）：%s\n"
            "    先手动运行一次这个程序看具体报错；确认没问题再重试，或删掉 %s 重装。"
            % (exe_now, RUNTIME_DIR))
    note = "来源 %s" % "、".join(sorted(set(used)))
    print("运行时就绪：%s（%s，%s，%s）" % (find_exe(), plat, backend, note))
    return find_exe(), tag, note


def status():
    tag = installed_tag()
    exe = find_exe()
    plat, backend, max_cuda = detect()
    try:
        latest, _ = latest_build(plat, backend, max_cuda)
    except RuntimeError:
        latest = None
    print("本机：%s（%s；平台 %s，后端 %s）" % (exe or "未安装", tag or "无版本记录",
                                              plat, backend))
    print("GitHub 最新可用：%s" % (latest or "查询失败"))
    return exe, tag, latest


if __name__ == "__main__":
    if "--check" in sys.argv:
        status()
    else:
        if "--backend" in sys.argv:
            os.environ["MOSS_LLM_BACKEND"] = sys.argv[sys.argv.index("--backend") + 1]
        install(force="--force" in sys.argv)
