# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""一键装/更新 llama.cpp 运行时（llama-server + 运行库）。

为什么自带安装器而不是复用别的节点包：本包要**独立可分装**——开源用户装了这个包
就可能没装班长包，去扫别人包的 runtime 目录等于把隐性依赖写进代码。
所以这里独立实现（探测与选包算法与班长包同源），只做三件事：
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
RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
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


def _cudart(assets, tok, arch, ver):
    """运行库包要和主包的 CUDA 版本一致；找不到同版本就退回最低版本。"""
    pat = re.compile(r"^cudart-llama-(?:b\d+-)?bin-%s-cuda-([\d.]+)-%s\.(?:zip|tar\.gz)$"
                     % (re.escape(tok), re.escape(arch)))
    cands = []
    for a in assets or []:
        m = pat.match((a.get("name") or "").lower())
        if m:
            cands.append((m.group(1), a))
    if not cands:
        return None
    same = [x for x in cands if _ver(x[0]) == _ver(ver)]
    return _entry((same or sorted(cands, key=lambda y: _ver(y[0])))[0][1])


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
    be = next((b for b in (backend, "cuda", "vulkan", "cpu") if b in by), None)
    if not be:
        return []
    lst = by[be]
    if be != "cuda":
        return [_entry(lst[0][1])]
    if max_cuda:                       # 不让包超过驱动能力（老驱动跑不了新 CUDA）
        lst = [x for x in lst if _ver(x[0]) <= max_cuda]
        if not lst:
            return []
        ver, a = max(lst, key=lambda x: _ver(x[0]))
    else:
        # 驱动未知时选最低版本：CUDA 包对旧架构不向下兼容，低版可借 PTX 跑新卡
        ver, a = min(lst, key=lambda x: _ver(x[0]))
    out = [_entry(a)]
    cud = _cudart(assets, tok, arch, ver)
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


# ------------------------------------------------------------------ 安装

def installed_tag():
    p = RUNTIME_DIR / VERSION_FILE
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8").strip() or None


def find_exe():
    for base in (RUNTIME_DIR, RUNTIME_DIR / "build" / "bin"):
        if base.is_dir():
            hits = sorted(h for h in base.glob("**/llama-server*")
                          if h.is_file() and (h.suffix == ".exe" or os.access(h, os.X_OK)))
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
        with open(part, mode) as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
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
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(path) as tf:
            try:
                tf.extractall(dest, filter="data")
            except TypeError:          # Python < 3.12 没有 filter 参数
                tf.extractall(dest)


def install(force=False):
    """装/更新。返回 (exe 路径, tag, 来源说明)。已是最新且不 force 时直接返回现有。"""
    plat, backend, max_cuda = detect()
    direct = os.environ.get("MOSS_LLM_RUNTIME_URL", "").strip()
    if direct:
        tag, want = None, [(direct, os.path.basename(direct.split("?")[0]), None, None)]
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
