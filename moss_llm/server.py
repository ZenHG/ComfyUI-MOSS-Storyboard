# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""llama-server 侧车：一次加载、跑完释放，供提示词节点复用。

从 Banzhang 包的 `_GgufLocalServer` 抽出，改掉三处：
① `--spec-type` 原先在 cmd 里加一次、`_popen` 又加一次（重复参数）——
   现在基础命令里**不放**投机参数，只在需要时由 `_popen` 追加一次；
② MTP 的 `auto` 原先只认文件名 `-mtp`，漏掉「元数据有 nextn 但名字没有」的模型
   （实测 `moss-te-heretic-dhq-K-6.6G.gguf`：`qwen35.nextn_predict_layers=1`，
   文件名不含 `-mtp`）——改为读 GGUF 头的**键名与取值**；
③ 端口原先写死 8080（与班长包抢），改为按实例传入，默认 8091。
"""

import json
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

#: GGUF 标量类型 → (struct 格式, 字节数)。数组与字符串走各自分支。
_GGUF_SCALARS = {0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2),
                 4: ("I", 4), 5: ("i", 4), 6: ("f", 4), 7: ("?", 1),
                 10: ("Q", 8), 11: ("q", 8), 12: ("d", 8)}

#: 显式指定 llama-server 可执行文件的环境变量（优先级最高的自动发现途径）。
SERVER_EXE_ENV = "MOSS_LLM_SERVER"


def instance_alias(model_path, mmproj="", ctx_size=8192):
    """侧车的身份 = alias 里的短指纹。

    为什么不能只按端口判「能不能复用」：端口上可能坐着一个**载着别的模型**的残留服务
    （宿主重启后先前遗留的孤儿就是这么骗过判据的：8s 出词、显存没还）。复用前比指纹，
    不一致就报错要用户释放，绝不拿别人的模型当自己的用。
    """
    import hashlib
    tag = "%s|%s|%s" % (Path(model_path).name, Path(mmproj).name if mmproj else "-",
                        int(ctx_size))
    return "moss-llm-" + hashlib.sha1(tag.encode("utf-8")).hexdigest()[:10]


def _served_alias(base_url):
    """问 `/models` 拿当前服务自己的 alias；问不到返回 None（当作不一致）。"""
    try:
        with urllib.request.urlopen(base_url + "/models", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8")).get("data") or []
        return (data[0].get("id") or "") if data else None
    except Exception:
        return None


def _port_open(port):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _health_ok(base_url):
    try:
        req = urllib.request.Request(base_url + "/health")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def gguf_metadata(path):
    """只读 GGUF 的 KV 头，返回 `{key: value}`（数组只给长度）。

    读到 KV 段就停，不遍历张量表——6.6G 的文件也是毫秒级。
    """
    out = {}
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            return out
        struct.unpack("<I", f.read(4))[0]                      # version
        struct.unpack("<Q", f.read(8))[0]                      # n_tensors
        n_kv = struct.unpack("<Q", f.read(8))[0]

        def rd(n):
            b = f.read(n)
            if len(b) != n:
                raise EOFError
            return b

        def s():
            return rd(struct.unpack("<Q", rd(8))[0]).decode("utf-8", "replace")

        for _ in range(n_kv):
            k = s()
            t = struct.unpack("<I", rd(4))[0]
            if t == 8:
                out[k] = s()
            elif t == 9:
                at = struct.unpack("<I", rd(4))[0]
                ln = struct.unpack("<Q", rd(8))[0]
                if at == 8:
                    for _i in range(ln):
                        s()
                elif at in _GGUF_SCALARS:
                    rd(_GGUF_SCALARS[at][1] * ln)
                else:
                    break
                out[k] = "<array %d>" % ln
            elif t in _GGUF_SCALARS:
                fmt, sz = _GGUF_SCALARS[t]
                out[k] = struct.unpack("<" + fmt, rd(sz))[0]
            else:
                break
    return out


def gguf_mtp_layers(path):
    """内嵌 MTP（nextn）层数；0 = 没有或读不懂。

    判据是元数据而非文件名：`<arch>.nextn_predict_layers` 的**取值** >0 才算有。
    退一步认键名里带 `nextn` / `mtp` 的整数值（不同 arch 前缀不同，不能写死 qwen35）。
    """
    try:
        md = gguf_metadata(path)
    except Exception:
        return 0
    for k, v in md.items():
        low = k.lower()
        if "nextn_predict_layers" in low and isinstance(v, int):
            if v > 0:
                return v
    for k, v in md.items():
        low = k.lower()
        if ("nextn" in low or "mtp" in low) and isinstance(v, int) and v > 0:
            return v
    return 0


class GgufLocalServer:
    """进程内单例。`acquire` 幂等：同模型同端口直接复用 base_url。"""

    _proc = None
    _model_path = None
    _port = None
    _lock = threading.Lock()
    #: 最近一次 acquire 的备注（MTP 启用/回退、端口复用），供 report 回显
    last_notes = []

    @classmethod
    def base_url(cls):
        return "http://127.0.0.1:%d/v1" % (cls._port or 8091)

    @classmethod
    def find_exe(cls, server_exe=""):
        """定位 llama-server：显式路径 > 环境变量 > 本包 runtime/ > 系统 PATH。

        绝不扫其它节点包的 runtime——那等于把「装了班长包」写成本包的隐性依赖。
        """
        for cand in (server_exe, os.environ.get(SERVER_EXE_ENV, "").strip()):
            if cand and Path(cand).is_file():
                return str(cand)
        here = Path(__file__).resolve()
        for base in (here.parent, here.parent.parent):
            for sub in ("runtime", "bin"):
                d = base / sub
                if d.is_dir():
                    hits = sorted(p for p in d.glob("**/llama-server*") if p.is_file())
                    if hits:
                        return str(hits[0])
        from shutil import which
        return which("llama-server")

    @classmethod
    def _stop_locked(cls):
        if cls._proc is not None and cls._proc.poll() is None:
            try:
                cls._proc.terminate()
                cls._proc.wait(timeout=20)
            except Exception:
                try:
                    cls._proc.kill()
                except Exception:
                    pass
        cls._proc = None
        cls._model_path = None

    @classmethod
    def stop(cls):
        with cls._lock:
            cls._stop_locked()
            cls._port = None

    @classmethod
    def running(cls):
        return cls._proc is not None and cls._proc.poll() is None

    @classmethod
    def acquire(cls, model_path, mmproj="", ctx_size=8192, gpu_layers=None,
                cache_type_k="q8_0", cache_type_v="q8_0", flash_attn=True,
                image_max_tokens=1024, ubatch=512, parallel=1,
                fit_on=True, fit_target_mib=1024, mtp="auto",
                port=8091, server_exe="", timeout=300, draft_n_max=2):
        """返回 `(base_url, notes)`。失败一律 raise 带排查指引的中文错误，不静默回退。"""
        with cls._lock:
            cls.last_notes = []
            notes = cls.last_notes
            model_path = str(Path(model_path).resolve())
            if cls._proc is not None and cls._proc.poll() is None:
                if cls._model_path == model_path and cls._port == port:
                    return cls.base_url(), notes
                cls._stop_locked()                      # 模型/端口变了 → 重启
            elif cls._proc is not None:
                cls._stop_locked()                      # 进程已死：清残壳

            if cls._port != port and _port_open(port):
                alias = instance_alias(model_path, mmproj, ctx_size)
                if _served_alias(cls.base_url()) == alias:
                    # 同一实例（同模型同 mmproj 同 ctx）→ 复用，不抢端口、不重载
                    cls._proc, cls._model_path, cls._port = None, model_path, port
                    notes.append("端口 %d 上正是同一个实例（alias 相符），直接复用" % port)
                    return cls.base_url(), notes
                raise RuntimeError(
                    "端口 %d 上坐着一个 llama-server，但它载的**不是**你要的模型（alias 不符）。\n"
                    "    不静默借用别人的模型。两条出路：\n"
                    "    ① 让占用者退出（先确认那是谁，再 `taskkill /PID <pid> /F`）；\n"
                    "    ② 换个端口（比如 %d）。" % (port, port + 1))

            exe = cls.find_exe(server_exe)
            if not exe:
                raise RuntimeError(
                    "找不到 llama-server。三条路任选一条：\n"
                    "    ① 把「运行时」选成「更新到最新版」，本节点会从 GitHub 官方 release 装；\n"
                    "    ② 在「服务器程序」里填已有的 llama-server 完整路径，"
                    "或设环境变量 %s=<路径>；\n"
                    "    ③ 把它放进本包 moss_llm/runtime/ 或系统 PATH。\n"
                    "    官方构建：https://github.com/ggml-org/llama.cpp/releases" % SERVER_EXE_ENV)

            ngl = "auto" if gpu_layers in (None, "", "auto") else str(gpu_layers)
            if mtp == "auto":
                mtp = bool(gguf_mtp_layers(model_path))
            cmd = [exe,
                   "--model", model_path, "--alias", instance_alias(model_path, mmproj, ctx_size),
                   "--api-key", "comfyui-local", "--host", "127.0.0.1", "--port", str(port),
                   "--ctx-size", str(int(ctx_size)),
                   "--n-gpu-layers", ngl,
                   "--flash-attn", ("on" if flash_attn else "off"),
                   "--cache-type-k", str(cache_type_k or "q8_0"),
                   "--cache-type-v", str(cache_type_v or "q8_0"),
                   "--batch-size", "2048", "--ubatch-size", str(int(ubatch or 512)),
                   "--parallel", str(max(1, int(parallel or 1))),
                   "--image-min-tokens", str(int(image_max_tokens or 1024)),
                   "--image-max-tokens", str(int(image_max_tokens or 1024)),
                   "--jinja"]
            if fit_on:
                cmd += ["--fit", "on", "--fit-target", str(int(fit_target_mib or 1024))]
            if mmproj and Path(mmproj).is_file():
                cmd += ["--mmproj", str(mmproj)]
            spec_args = ["--spec-type", "draft-mtp",
                         "--spec-draft-n-max", str(int(draft_n_max or 2))]

            print("[moss_llm] 启动 llama-server | %s | ctx=%s | ngl=%s | KV=%s/%s | FA=%s | "
                  "视觉token≤%s | ubatch=%s | parallel=%s | fit=%s | MTP=%s(d%s) | mmproj=%s"
                  % (Path(model_path).name, ctx_size, ngl, cache_type_k, cache_type_v,
                     "on" if flash_attn else "off", image_max_tokens, ubatch, parallel,
                     ("on/%sMiB" % fit_target_mib) if fit_on else "off",
                     "on" if mtp else "off", draft_n_max,
                     "有" if (mmproj and Path(mmproj).is_file()) else "无"))

            env = dict(os.environ)
            dll_dir = str(Path(exe).parent)        # DLL 目录进 PATH（cublas 加载关键）
            env["PATH"] = dll_dir + os.pathsep + env.get("PATH", "")

            def _popen(with_mtp):
                full = list(cmd)
                if with_mtp:
                    full += spec_args
                cls._proc = subprocess.Popen(full, env=env,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                cls._model_path, cls._port = model_path, port

            def _wait_ready(limit):
                deadline = time.time() + limit
                last_beat = 0
                while time.time() < deadline:
                    if _health_ok(cls.base_url()):
                        return True
                    if cls._proc is None or cls._proc.poll() is not None:
                        return False                    # 进程退出 = 启动失败
                    waited = int(time.time() - (deadline - limit))
                    if waited - last_beat >= 30:        # 心跳：让用户知道在加载，不是卡死
                        print("[moss_llm] llama-server 正在加载模型… 已等待 %ds" % waited)
                        last_beat = waited
                    time.sleep(3)
                return False

            _popen(bool(mtp))
            if _wait_ready(timeout):
                notes.append("MTP(draft-mtp d=%d) 已启用" % int(draft_n_max or 2) if mtp
                             else "MTP 未启用")
                return cls.base_url(), notes

            cls._stop_locked()
            if not mtp:
                raise RuntimeError(
                    "llama-server 启动失败。模型: %s\n"
                    "    排查：① 显存是否被占用（生视频/游戏会挤掉模型层）② 模型文件是否完整\n"
                    "    ③ llama-server 原生日志" % Path(model_path).name)
            # MTP 起不来 → 回退普通模式重启（不同模型/版本支持不一，绝不卡死）。
            # 回退必须**出声**：写进 notes，让 report 里看得见。
            print("[moss_llm] ⚠️ draft-mtp 启动失败（模型可能不含内嵌 MTP 层或不兼容当前 runtime），"
                  "已回退普通模式重启。")
            notes.append("MTP(draft-mtp) 启动失败，已回退普通模式")
            _popen(False)
            if not _wait_ready(timeout):
                cls._stop_locked()
                raise RuntimeError(
                    "llama-server 启动失败（含 MTP 回退后仍失败）。模型: %s\n"
                    "    排查：① 显存是否被占用 ② 模型文件是否完整 ③ llama-server 原生日志"
                    % Path(model_path).name)
            return cls.base_url(), notes
