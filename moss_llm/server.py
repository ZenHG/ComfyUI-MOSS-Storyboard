# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""llama-server 侧车：**一个类，两种用法**。

    ① 自己拉起进程（本地权重）：
         LlamaServer(model="…/x.gguf", mmproj="…/mmproj.gguf")
    ② 连一个已经跑着的 OpenAI 兼容端点（开源用户的主流用法）：
         LlamaServer(base_url="http://127.0.0.1:8080")

为什么必须有 ②：本项目是开源的，用户机器上不一定有 llama-server，更不一定有 WSL。
把「进程管理」当成必经之路，等于把一半用户挡在门外。② 模式下本类只做 HTTP 客户端，
不 spawn、不 kill、不读日志。

## 这一版做过什么（合流记录）

2026-10-31 之前有**两份**实现：`moss_llm/server.py` 的 `GgufLocalServer`（节点用，有真机
验证：fit / KV 量化 / ubatch / MTP 元数据判定 / mmproj 按投影宽度配对）与
`moss_llm/smith/llm.py` 的 `LlamaServer`（编译器用，有 300+ 条单测：flag 探测 / 日志 /
attached 模式 / JSON 约束三级降级 / 采样纪律）。两边各缺一半，而且都在长——这就是漂移的
标准起点。现在**只剩这一份**，两家能力都在这里，`smith/llm.py` 只是转发。

合流时**取长补短**的具体账：
- 取 A：fit/fit-target、KV cache 量化、ubatch、parallel、MTP 元数据判定与回退、
  GGUF 头部只读解析、mmproj 投影宽度硬配对。
- 取 B：`--help` 探测（旧版 llama-server 没有 `--jinja` / `--image-max-tokens`，
  直接拼上去是「启动即退出」，报错还和真实原因无关）、日志落盘、attached 模式、
  `build_body` / `build_cmd` 抽成纯函数（参数拼错只有真机才炸，代价太高）、
  JSON 约束三级降级、JSON 模式下摘 DRY/presence/frequency（但保留 repeat_penalty）。
- 🔴 **改掉一处**：A 给侧车加过 `--api-key comfyui-local`。合流后 B 的 `ServerCounter`
  要打 `/tokenize`，带口令的服务会回 401，而它**捕获异常后退回估算**——真实 token 数
  被静默降级成 ±15% 的猜测，报告里看不出来。所以服务端口令不再加（进程本来只绑
  127.0.0.1），客户端侧保留 `api_key` 供外部端点用。

四条实测纪律（别改）：
1. 思考模型必须显式关思考：`chat_template_kwargs: {"enable_thinking": false}`。
   只发 `thinking: false` 或顶层 `enable_thinking` 都无效，正文会空。
2. 服务端拒 `chat_template_kwargs` 时，摘掉重试并**出声**，不静默降级。
3. 正文为空就报错。绝不把 reasoning_content 当答案端上桌。
4. **JSON 模式只摘「会伤键名」的重复惩罚，保留 `repeat_penalty`**（见下）。
   llama.cpp 官方原话：JSON schema 只锁形状，不注入 prompt，模型看不见 schema ⇒
   结构化输出必须同时在 prompt 里写字段说明。反过来说，一旦上了语法约束，`"label":`
   这些键名会合法地重复 N 次，DRY / presence / frequency 全按「重复即病」惩罚，会把
   键名惩罚成乱码——所以这几个参数在 JSON 调用里强制摘除/归零。但 **`repeat_penalty`
   必须留**：它压的是 token 级整段循环（G7 第一轮 4/40 次跑批整单归零就是重复循环
   撞 completion 上限），键名由语法约束兜住，不会被它惩罚成乱码。
"""

import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

#: GGUF 标量类型 → (struct 格式, 字节数)。数组与字符串走各自分支。
_GGUF_SCALARS = {0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2),
                 4: ("I", 4), 5: ("i", 4), 6: ("f", 4), 7: ("?", 1),
                 10: ("Q", 8), 11: ("q", 8), 12: ("d", 8)}

#: 显式指定 llama-server 可执行文件的环境变量（优先级最高的自动发现途径）。
SERVER_EXE_ENV = "MOSS_LLM_SERVER"

#: 旧名（本层独立成包时用过）。并存期一并认——用户设过的变量不能因为改名就失效。
LEGACY_SERVER_ENVS = ("PROMPT_ARTISAN_SERVER", "PROMPT_ARTISAN_LLAMA_SERVER")


def _exe_name():
    return "llama-server.exe" if os.name == "nt" else "llama-server"


# ─────────────────────────────────────────────────────────────
# GGUF 头部（只读 KV 段，毫秒级）
# ─────────────────────────────────────────────────────────────

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
    文件名判据会漏掉「元数据有 nextn 但名字没有 -mtp」的模型（实测 dhq 版就是）。
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


def gguf_text_width(model_path):
    """模型文本宽度 = `<arch>.embedding_length`；读不懂返回 None。"""
    try:
        md = gguf_metadata(model_path)
    except (OSError, EOFError):
        return None
    arch = md.get("general.architecture") or ""
    w = md.get("%s.embedding_length" % arch) if arch else None
    return w if isinstance(w, int) else None


def gguf_proj_width(mmproj_path):
    """塔投影宽度 = `clip.vision.projection_dim`。

    不能拿 `clip.vision.embedding_length` 顶替：那是塔内部的视觉宽度（如 1152），
    决定「配不配得上」的只有 projection_dim。
    """
    try:
        w = gguf_metadata(mmproj_path).get("clip.vision.projection_dim")
    except (OSError, EOFError):
        return None
    return w if isinstance(w, int) else None


def pair_key(name):
    """认「同一套权重的塔」用的名字键：去掉量化档与 mmproj 标记；认不出返回空串。"""
    stem = Path(name).stem.lower()
    stem = re.split(
        r"[-.](?=(?:ud-)?(?:q\d|iq\d|bf16|f16|f32|mxfp\d|nvfp\d|tq\d|pq\d|ptq\d))",
        stem)[0]
    return stem.replace(".mmproj", "").replace("-mmproj", "").strip("-.")


def check_mmproj_pair(model_path, mmproj_path):
    """校验塔宽 == 模型文本宽；不符 raise，读不懂返回一句「未验证」写进 report。"""
    want, got = gguf_text_width(model_path), gguf_proj_width(mmproj_path)
    if want is None or got is None:
        return ("⚠️ mmproj 配对未验证（模型宽度 %s，塔投影 %s）：头里缺字段。"
                % (want, got))
    if want != got:
        raise ValueError(
            "mmproj 与模型不配对：塔投影宽度 %d，模型文本宽度 %d。\n"
            "    换一个投影宽度相同的塔，或把「mmproj」留「自动匹配」让节点自己找。"
            % (got, want))
    return ""


MMPROJ_AUTO = "auto"
MMPROJ_NONE = "none"


def resolve_mmproj(model_path, mmprojs=None, choice=MMPROJ_AUTO):
    """选塔：按投影宽度硬校验，只在无歧义时自动选，否则报错让用户选。

    旧版只按文件名前缀猜（`qwen3.8-27b` 被削成 `qwen3`，两个塔时按字母序取首个），
    不同家族只要 proj_dim 恰好相等就能配上 —— 那种错配不报错，只会「看错图」。
    """
    if choice == MMPROJ_NONE:
        return "", ""
    if choice and choice not in (MMPROJ_AUTO, ""):
        path = (mmprojs or {}).get(choice) or (choice if Path(choice).is_file() else "")
        if not path:
            raise ValueError("mmproj 不存在：%r。请在「mmproj」下拉里重选。" % choice)
        return path, check_mmproj_pair(model_path, path)

    p = Path(model_path)
    seen, cands = set(), []
    for f in sorted(p.parent.glob("*.gguf")):
        if "mmproj" in f.name.lower() and str(f) not in seen:
            seen.add(str(f))
            cands.append(f)
    for v in (mmprojs or {}).values():
        if str(v) not in seen:
            seen.add(str(v))
            cands.append(Path(v))
    if not cands:
        return "", ""
    want = gguf_text_width(model_path)
    if want is None:
        raise ValueError(
            "读不到模型头里的文本宽度（%s），没法验证 mmproj 配对。\n"
            "    在「mmproj」下拉里显式选一个塔；纯文本任务可选「(不使用/纯文本)」。"
            % Path(model_path).name)
    ok = [f for f in cands if gguf_proj_width(str(f)) == want]
    if not ok:
        lines = "\n".join("    - %s（投影 %s）" % (f.name, gguf_proj_width(str(f)))
                          for f in cands[:6])
        raise ValueError(
            "找到 %d 个 mmproj，但没有一个能配到 %s：模型文本宽度 %d。候选：\n%s\n"
            "    换一个投影宽度 %d 的塔。"
            % (len(cands), Path(model_path).name, want, lines, want))
    key = pair_key(p.name)
    for pool in ([f for f in ok if key and pair_key(f.name) == key],
                 [f for f in ok if f.parent == p.parent],
                 ok):
        if len(pool) == 1:
            return str(pool[0]), check_mmproj_pair(model_path, str(pool[0]))
        if pool:
            lines = "\n".join("    - %s" % f.name for f in pool[:8])
            raise ValueError(
                "有 %d 个 mmproj 都能配到 %s（投影宽度 %d）：\n%s\n"
                "    请在「mmproj」下拉里显式选一个。"
                % (len(pool), Path(model_path).name, want, lines))
    return "", ""


# ─────────────────────────────────────────────────────────────
# 侧车身份指纹
# ─────────────────────────────────────────────────────────────

def instance_alias(model_path, mmproj="", ctx_size=8192):
    """侧车的身份 = alias 里的短指纹。

    为什么不能只按端口判「能不能复用」：端口上可能坐着一个**载着别的模型**的残留服务
    （宿主重启后先前遗留的孤儿就是这么骗过判据的：8s 出词、显存没还）。复用前比指纹，
    不一致就报错要用户释放，绝不拿别人的模型当自己的用。

    指纹用**完整路径**而不是文件名：同名不同目录的两个权重用文件名算会撞指纹，
    那就等于允许借用错模型。
    """
    model = os.path.abspath(str(model_path)) if model_path else ""
    mp = os.path.abspath(str(mmproj)) if mmproj else "-"
    tag = "%s|%s|%d" % (model, mp, int(ctx_size))
    return "moss-llm-" + hashlib.sha1(tag.encode("utf-8")).hexdigest()[:10]


def _port_open(port):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def port_free(port):
    return not _port_open(port)


def pick_free_port(start, limit=20):
    """从 start 起找第一个空闲端口；找不到返回 None（不静默抢别人的端口）。"""
    for port in range(int(start), int(start) + int(limit) + 1):
        if port_free(port):
            return port
    return None


# ─────────────────────────────────────────────────────────────
# 采样纪律
# ─────────────────────────────────────────────────────────────

#: 散文默认采样。DRY 专门压「同一句换个说法再说一遍」，这是反推 note 的老毛病。
#: 只在**非 JSON** 调用里生效（见纪律 4）。
DEFAULT_SAMPLING = {
    "dry_multiplier": 0.8,
    "dry_base": 1.75,
    "dry_allowed_length": 3,      # 3 词以上才判重复，2 词会误伤 "of the"
    "dry_penalty_last_n": 1024,
    "repeat_penalty": 1.05,
}

#: JSON 调用里必须清零的字段。
_JSON_HOSTILE = ("dry_multiplier", "presence_penalty", "frequency_penalty")


class _BadRequest(RuntimeError):
    """服务端 4xx。用来区分「这个参数它不认」和「真出错了」。"""


def _env_server():
    """按优先级取环境变量里的 llama-server 路径（含旧名）。"""
    for k in (SERVER_EXE_ENV,) + LEGACY_SERVER_ENVS:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def find_server_exe(explicit="", source="auto"):
    """按来源定位 llama-server（单一权威，不做隐式回退）。

    source 取值：
      auto    —— 环境变量 > LLAMA_CPP_HOME > 本包 runtime/ > PATH（默认）
      runtime —— 只认本包 runtime/（含 MOSS_LLM_RUNTIME_DIR）
      path    —— 只认 PATH 里的 llama-server
      custom  —— 只认显式传入的 server_exe

    刻意**不扫**别的节点包的 runtime 目录：那等于把「用户装过某工具」写成本项目的
    隐性依赖。找不到返回 None，由调用方给出可执行的下一步。
    """
    from shutil import which
    if source == "custom":
        return str(explicit) if explicit and Path(str(explicit)).is_file() else None
    cands = []
    if source == "auto":
        cands.append(_env_server())
        home = os.environ.get("LLAMA_CPP_HOME", "")
        if home:
            for sub in ("build/bin/Release", "build/bin", "bin", ""):
                cands.append(str(Path(home) / sub / _exe_name()))
    if source in ("auto", "runtime"):
        here = Path(__file__).resolve()
        bases = [here.parent, here.parent.parent]
        custom_rt = os.environ.get("MOSS_LLM_RUNTIME_DIR", "").strip()
        if custom_rt:
            bases.insert(0, Path(custom_rt).expanduser())
        for base in bases:
            for sub in ("runtime", "bin"):
                d = base / sub
                if d.is_dir():
                    cands.extend(str(p) for p in sorted(d.glob("**/" + _exe_name())))
    for c in cands:
        if c and Path(c).is_file():
            return str(c)
    if source in ("auto", "path"):
        for name in ("llama-server", "llama-server.exe"):
            hit = which(name)
            if hit:
                return hit
    return None


def missing_exe_error():
    """找不到 llama-server 时说什么：按「最省事 → 已有 → 不用本机」排序。"""
    return (
        "找不到 llama-server 可执行文件。三条路，任选一条：\n"
        "    No llama-server executable found. Pick one of three options:\n"
        "  ① 节点内自动装（推荐）：把「运行时 / runtime」设为「自动下载」，再运行。\n"
        "     自动按本机后端选 CUDA/ROCm/Vulkan/SYCL/Metal/CPU；国内慢设 MOSS_LLM_MIRROR=<前缀>，\n"
        "     选错后端设 MOSS_LLM_BACKEND=cuda|vulkan|rocm|sycl|metal|cpu。\n"
        "  ② 已有 llama.cpp：节点「服务器程序 / server_exe」填完整路径；\n"
        "     或设环境变量 %s=<路径>；也可以设 LLAMA_CPP_HOME=<解压目录>，或放进 PATH。\n"
        "  ③ 已有 OpenAI 兼容服务（LM Studio / Ollama / llama-swap / 远端）：\n"
        "     节点「通道 / channel」选「外部 API」，填 api_base（--base-url，要带 /v1）与模型名；不需要本机二进制。\n"
        "  命令行等价：python -m moss_llm.smith setup --backend auto\n"
        "  官方构建：https://github.com/ggml-org/llama.cpp/releases" % SERVER_EXE_ENV)


# ─────────────────────────────────────────────────────────────
# 侧车
# ─────────────────────────────────────────────────────────────

class LlamaServer:
    """llama-server 的客户端 + 进程管理。参考下图两种用法见模块文档。"""

    def __init__(self, model="", exe=None, port=8091, ctx=8192, ngl=99,
                 mmproj="", timeout=240.0, log_path=None, sampling=None,
                 base_url="", vision_tokens=1024, model_id="",
                 lora="", lora_scale=None,
                 cache_type_k="q8_0", cache_type_v="q8_0", ubatch=512, parallel=1,
                 fit_on=True, fit_target_mib=1024, flash_attn=True,
                 mtp="auto", draft_n_max=2, api_key="", source="auto"):
        self.model = str(Path(model).resolve()) if model else ""
        self.model_id = model_id.strip()
        self._served_model_id = ""
        self.exe = exe if exe is not None else ""
        self.source = source          # auto / runtime / path / custom（单一权威）
        self.port = int(port)
        self.ctx = int(ctx)
        self.ngl = ngl
        self.mmproj = mmproj
        # LoRA 适配器（GGUF 格式）。`--lora-scaled` 带 scale 时用它——
        # 某些训练出来的 alpha 与推理端假设的缩放不同，能不能对齐要看实测，
        # 所以 scale 做成显式参数，不在代码里猜。
        self.lora = str(Path(lora).resolve()) if lora else ""
        self.lora_scale = lora_scale
        self.vision_tokens = int(vision_tokens or 0)
        self.timeout = timeout
        self.attached = bool((base_url or "").strip())
        self._base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        if log_path:
            self.log_path = log_path
        elif self.model:
            self.log_path = str(Path(self.model).with_suffix(".server.log"))
        else:
            self.log_path = str(Path(tempfile.gettempdir()) / "moss-llm-server.log")
        self.sampling = dict(DEFAULT_SAMPLING if sampling is None else sampling)
        self.proc = None
        self.notes = []
        self.last_finish = ""
        self._thinking_kwargs = True
        # ── 进程参数（原 GgufLocalServer 的能力）──
        self.cache_type_k = cache_type_k or "q8_0"
        self.cache_type_v = cache_type_v or "q8_0"
        self.ubatch = int(ubatch or 512)
        self.parallel = max(1, int(parallel or 1))
        self.fit_on = bool(fit_on)
        self.fit_target_mib = int(fit_target_mib or 0)
        self.flash_attn = bool(flash_attn)
        self.mtp = mtp
        self.draft_n_max = int(draft_n_max or 2)

    # ── 地址 ────────────────────────────────────────────────
    @property
    def base(self):
        if self.attached:
            return self._base_url
        return "http://127.0.0.1:%d" % self.port

    @property
    def v1(self):
        return self.base + "/v1"

    # ── 身份 ────────────────────────────────────────────────
    def alias(self):
        """本次要跑的实例指纹。attached 模式下没有意义（我们不管它的进程）。"""
        if not self.model:
            return instance_alias("-", self.mmproj, self.ctx)
        return instance_alias(self.model, self.mmproj, self.ctx)

    def served_alias(self):
        """问 `/v1/models` 拿当前服务自己的 id。问不到返回空串。"""
        try:
            with urllib.request.urlopen(self.v1 + "/models", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8")).get("data") or []
            return (data[0].get("id") or "") if data else ""
        except Exception:
            return ""

    def model_name(self):
        """请求体里的 `model` 字段该填什么。

        这个字段不是装饰：Ollama 要模型名，LM Studio 要它注册的 id，vLLM 要
        served-model-name。填错了轻则 404，重则被路由到另一个模型上。

        顺序：显式 `model_id` > 连上时探到的 id > 自己拉的 alias。
        """
        if self.model_id:
            return self.model_id
        if self._served_model_id:
            return self._served_model_id
        if self.attached:
            return ""            # 探不到就干脆不发这个字段，很多服务端允许省略
        return self.alias()

    # ── 可执行文件 ──────────────────────────────────────────
    #: exe 路径 → 支持的 flag 集合。`--help` 只跑一次。
    _flag_cache = {}

    @classmethod
    def help_flags(cls, exe):
        """问 `llama-server --help` 它到底支持哪些参数。

        为什么要问：不同版本的 llama.cpp 参数增删频繁（`--image-max-tokens` 是后加的，
        `--jinja` 更晚）。拼一个它不认的参数，进程会**启动即退出**，而错误信息只有
        退出码——用户根本看不出是版本太旧。
        """
        key = str(Path(exe))
        if key in cls._flag_cache:
            return cls._flag_cache[key]
        flags = set()
        try:
            p = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=20)
            text = (p.stdout or "") + (p.stderr or "")
            flags = set(re.findall(r"--[a-z0-9][a-z0-9-]+", text))
        except Exception:
            flags = set()
        cls._flag_cache[key] = flags
        return flags

    def find_exe(self):
        hit = find_server_exe(self.exe, getattr(self, "source", "auto"))
        if not hit:
            raise RuntimeError(missing_exe_error())
        return hit

    # ── 命令行（纯函数，可离线单测）─────────────────────────
    def mtp_enabled(self):
        """本次要不要开 draft-mtp。`auto` = 看 GGUF 元数据里的 nextn 层数。"""
        if self.mtp is True or self.mtp == "on":
            return True
        if self.mtp is False or self.mtp == "off":
            return False
        return bool(gguf_mtp_layers(self.model)) if self.model else False

    def spec_args(self):
        return ["--spec-type", "draft-mtp",
                "--spec-draft-n-max", str(self.draft_n_max)]

    def build_cmd(self, exe, flags=None):
        """拼启动命令。返回 `(cmd, notes)`。

        抽成纯函数是为了能离线单测：参数拼错只有真机才炸，而真机一次加载几十秒，
        代价太高。`flags` 是 `--help` 探到的开关集合；空集合按「都支持」处理
        （探不出来的老版本保持原行为）。
        """
        flags = self.help_flags(exe) if flags is None else flags
        notes = []

        def has(flag):
            return (not flags) or (flag in flags)

        ngl = "auto" if self.ngl in (None, "", "auto") else str(self.ngl)
        cmd = [exe, "--model", self.model, "--alias", self.alias(),
               "--host", "127.0.0.1", "--port", str(self.port),
               "--ctx-size", str(self.ctx), "--n-gpu-layers", ngl]
        if has("--jinja"):
            cmd.append("--jinja")
        if self.mmproj and Path(self.mmproj).is_file():
            cmd += ["--mmproj", str(self.mmproj)]
        if self.lora:
            if not has("--lora"):
                raise RuntimeError("这个 llama-server 不支持 --lora，装不了适配器：%s"
                                   % self.lora)
            if self.lora_scale is None:
                cmd += ["--lora", self.lora]
            else:
                cmd += ["--lora-scaled", "%s:%.4f" % (self.lora, float(self.lora_scale))]
        if has("--flash-attn"):
            cmd += ["--flash-attn", "on" if self.flash_attn else "off"]
        if has("--cache-type-k"):
            cmd += ["--cache-type-k", str(self.cache_type_k),
                    "--cache-type-v", str(self.cache_type_v)]
        if has("--batch-size"):
            cmd += ["--batch-size", "2048"]
        if has("--ubatch-size"):
            cmd += ["--ubatch-size", str(self.ubatch)]
        if has("--parallel"):
            cmd += ["--parallel", str(self.parallel)]
        if self.vision_tokens and has("--image-max-tokens"):
            # 视觉 token 上限。不设的话一张 1536 宽的图能吃掉几千 token，
            # 留给正文的上下文就不够了（实测 4 格漫画 + 3 个气泡会顶到 ctx 边缘）。
            cmd += ["--image-min-tokens", str(self.vision_tokens),
                    "--image-max-tokens", str(self.vision_tokens)]
        elif self.vision_tokens:
            notes.append("这个 llama-server 不支持 --image-max-tokens，"
                         "视觉 token 上限未生效（升级到较新版本可解）")
        if self.fit_on and has("--fit"):
            cmd += ["--fit", "on", "--fit-target", str(self.fit_target_mib)]
        elif self.fit_on:
            notes.append("这个 llama-server 不支持 --fit，显存适配未生效"
                         "（放不下时自己调「GPU 层数」或降低上下文）")
        return cmd, notes

    # ── 启动 ────────────────────────────────────────────────
    def start(self):
        if self.attached:
            if not self._healthy():
                raise RuntimeError(
                    "连不上 %s。确认三件事：\n"
                    "  ① 服务确实在跑（浏览器打开 %s/health 看看）\n"
                    "  ② 它是 OpenAI 兼容端点（路径要能接受 /v1/chat/completions）\n"
                    "  ③ 反推需要视觉能力：该服务加载了 mmproj / 是 VL 模型"
                    % (self.base, self.base))
            self.notes.append("已连上外部端点 %s（本进程不管理它的生命周期）" % self.base)
            self._served_model_id = self.served_alias()
            if self._served_model_id and not self.model_id:
                self.notes.append("探测到服务端模型 id：%s" % self._served_model_id)
            elif not self._served_model_id and not self.model_id:
                self.notes.append("没探到模型 id，请求体里将省略 model 字段"
                                  "（多数服务端允许；若报 404 请加 --model-id）")
            return self
        if self._healthy():
            want, got = self.alias(), self.served_alias()
            if not got or got == want:
                self.notes.append("端口 %d 上正是同一个实例（alias 相符），直接复用" % self.port)
                return self
            self.notes.append(
                "端口 %d 被另一个 llama-server 占用（它在跑 %s），不借用它的模型\n"
                "    Port is held by another llama-server; not borrowing its model." % (self.port, got))
        if not port_free(self.port):
            new_port = pick_free_port(self.port + 1)
            if new_port is None:
                raise RuntimeError(
                    "端口 %d 起连续 20 个端口都被占用，无法启动侧车。\n"
                    "    Ports from this one are all busy; cannot start the sidecar.\n"
                    "  请释放一个端口，或把节点的「端口」改到空闲值。" % self.port)
            self.notes.append("端口 %d 被占用，自动改用 %d" % (self.port, new_port))
            self.port = new_port
        if not self.model:
            raise RuntimeError("没有给 --model，也没有给 --base-url：不知道要跑哪个权重。")

        exe = self.find_exe()
        cmd, notes = self.build_cmd(exe)
        self.notes.extend(notes)
        wants_mtp = self.mtp_enabled()

        self._spawn(exe, cmd + (self.spec_args() if wants_mtp else []))
        if self._wait_ready():
            self.notes.append("模型已就绪")
            if wants_mtp:
                self.notes.append("MTP(draft-mtp d=%d) 已启用" % self.draft_n_max)
            return self

        self.stop()
        if not wants_mtp:
            raise RuntimeError(
                "llama-server 启动失败。模型: %s\n"
                "    排查：① 显存是否被占用（生视频/游戏会挤掉模型层）② 模型文件是否完整\n"
                "    ③ 看日志：%s" % (Path(self.model).name, self.log_path))
        # MTP 起不来 → 回退普通模式重启（不同模型/版本支持不一，绝不卡死）。
        # 回退必须**出声**：写进 notes，让 report 里看得见。
        self.notes.append("MTP(draft-mtp) 启动失败（模型可能不含内嵌 MTP 层或不兼容当前 "
                          "runtime），已回退普通模式重启")
        self._spawn(exe, cmd)
        if not self._wait_ready():
            self.stop()
            raise RuntimeError(
                "llama-server 启动失败（含 MTP 回退后仍失败）。模型: %s\n"
                "    排查：① 显存是否被占用 ② 模型文件是否完整 ③ 看日志：%s"
                % (Path(self.model).name, self.log_path))
        return self

    def _spawn(self, exe, cmd):
        env = dict(os.environ)
        # DLL 目录进 PATH（cublas / cudart 加载关键）
        env["PATH"] = str(Path(exe).parent) + os.pathsep + env.get("PATH", "")
        log = open(self.log_path, "wb")
        try:
            self.proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        finally:
            log.close()          # 子进程已继承句柄，父进程这边可以立刻关
        self.notes.append("启动 llama-server pid=%d，日志 %s" % (self.proc.pid, self.log_path))

    def _wait_ready(self):
        t0 = time.time()
        deadline = t0 + self.timeout
        last_beat = 0
        while time.time() < deadline:
            if self._healthy():
                self.notes.append("加载耗时 %.1fs" % (time.time() - t0))
                return True
            if self.proc is not None and self.proc.poll() is not None:
                self.notes.append("llama-server 启动即退出（退出码 %s）" % self.proc.returncode)
                return False
            waited = int(time.time() - t0)
            if waited - last_beat >= 30:      # 心跳：让用户知道在加载，不是卡死
                self.notes.append("正在加载模型… 已等待 %ds" % waited)
                last_beat = waited
            time.sleep(2)
        self.notes.append("llama-server %.0fs 内未就绪" % self.timeout)
        return False

    def _healthy(self):
        """探活。两条路都要试：

        `/health` 是 llama-server 专有的；LM Studio、Ollama、vLLM 没有这个路径，
        打过去是 404。只认 /health 会把「接别人的服务」这条路堵死一半。
        所以退一步看 `/v1/models`——OpenAI 兼容端点一定有。
        """
        for path in ("/health", "/v1/models"):
            try:
                with urllib.request.urlopen(self.base + path, timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                continue
        return False

    def alive(self):
        """我们自己拉起来的进程还活着吗。attached 模式恒 False（不是我们的进程）。"""
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=15)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    # ── 请求 ────────────────────────────────────────────────
    def chat(self, system, user, temperature=0.3, max_tokens=1200,
             schema=None, name="scene_record", seed=None):
        return self._chat([{"role": "system", "content": system},
                           {"role": "user", "content": user}],
                          temperature, max_tokens, schema, name, seed=seed)

    def chat_vision(self, system, user, data_url, temperature=0.2,
                    max_tokens=3000, schema=None, name="scene_record",
                    seed=None):
        """带图请求。`data_url` 由 `moss_llm.vision.prepare()` 产出。

        温度默认压到 0.2：反推是**取证**不是创作，采样越保守越好。
        `schema` 给了就走语法约束——但 prompt 里仍然要写字段说明（见纪律 4）。
        """
        if not self.mmproj and not self.attached:
            raise RuntimeError(
                "这个服务没有加载 mmproj，看不了图。\n"
                "  启动时加 --mmproj <mmproj-*.gguf>，或把 LlamaServer(mmproj=...) 填上。\n"
                "  如果你连的是外部端点（--base-url），确认那边跑的是 VL 模型。")
        return self._chat([
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], temperature, max_tokens, schema, name, seed=seed)

    def build_body(self, messages, temperature, max_tokens,
                   response_format=None, json_mode=False, seed=None):
        """拼请求体。抽出来是为了能离线单测——参数拼错只有真机才炸，代价太高。

        `seed` 默认 None（不写进 payload，与旧行为字节级一致）；只有显式给值才写，
        让重复跑批可复现。
        """
        body = {
            "messages": messages,
            "temperature": temperature,
            "top_p": 0.9,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if seed is not None:
            body["seed"] = int(seed)
        mid = self.model_name()
        if mid:
            body["model"] = mid
        for k, v in self.sampling.items():
            if json_mode and (k in _JSON_HOSTILE or k.startswith("dry_")):
                continue          # 纪律 4：整族 DRY 都要摘，只摘 multiplier 不够
            body[k] = v
        if json_mode:
            # 键名重复是 JSON 的常态，惩罚它就是惩罚正确输出
            body["presence_penalty"] = 0
            body["frequency_penalty"] = 0
            # 但「整段循环」不是键名重复：G7 第一轮 4/40 次整单归零，形态都是模型把
            # 同一对 label 左右交替写满几十个实体，直到撞 completion 上限。repeat_penalty
            # 是按 token 频次压的、不像 DRY 那样按段判重，语法约束下不会伤键名——所以
            # JSON 模式必须保留它；自定义 sampling 没给时用默认值补上（宁可保守）。
            body.setdefault("repeat_penalty", DEFAULT_SAMPLING["repeat_penalty"])
        if response_format is not None:
            body["response_format"] = response_format
        if self._thinking_kwargs:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return body

    @staticmethod
    def _formats(schema, name="scene_record"):
        """约束强度从强到弱。服务端不认哪一档就往下降，每降一档都要出声。"""
        if not schema:
            return [None]
        return [
            {"type": "json_schema",
             "json_schema": {"name": name, "schema": schema}},
            {"type": "json_object", "schema": schema},   # llama.cpp 私有写法
            {"type": "json_object"},                     # 只保证是 JSON
            None,                                        # 什么都不保证
        ]

    _LABEL = {0: "json_schema", 1: "json_object+schema", 2: "json_object", 3: "无约束"}

    def _chat(self, messages, temperature, max_tokens,
              schema=None, name="scene_record", seed=None):
        formats = self._formats(schema, name)
        detail = ""
        for i, rf in enumerate(formats):
            body = self.build_body(messages, temperature, max_tokens, rf,
                                   json_mode=bool(schema), seed=seed)
            try:
                data = self._post("/v1/chat/completions", body)
            except _BadRequest as e:
                detail = str(e)
                if i == 0:
                    self.notes.append("服务端不认 response_format=%s，正在降级约束"
                                      "（JSON 合法性不再有保证）：%s"
                                      % (self._LABEL[0], detail[:120]))
                continue
            if i > 0:
                self.notes.append("JSON 约束降级为 %s——模型可能输出不合法的 JSON"
                                  % self._LABEL[i])
            return self._content(data)
        raise RuntimeError(
            "所有 JSON 约束档位都被服务端拒绝，最后一次报错：%s\n"
            "  用 --no-schema 关掉结构化输出再试。" % detail)

    def _content(self, data):
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning_content") or "").strip()
        self.last_finish = (data.get("choices") or [{}])[0].get("finish_reason") or ""
        if not content and reasoning:
            raise RuntimeError(
                "模型只输出了思考内容（%d 字），正文为空。\n"
                "  这不是可用的提示词。检查服务端是否支持 chat_template_kwargs。"
                % len(reasoning))
        if not content:
            raise RuntimeError("模型返回空内容。原始响应：%s"
                               % json.dumps(data, ensure_ascii=False)[:400])
        if self.last_finish == "length":
            # 截断的 JSON 必然解析失败。在这里记一笔，下游报错时能直接归因到 max_tokens。
            self.notes.append("本次响应被 max_tokens 截断（finish_reason=length）")
        return content

    def _post(self, path, body, _retry=True):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            # 4xx 一律当作「这个参数它不认」，交给上层逐档降级。
            # 只认 400 是不够的：有的网关把不认识的字段判 422，有的判 404。
            if 400 <= e.code < 500:
                if "chat_template_kwargs" in body and _retry:
                    self._thinking_kwargs = False
                    self.notes.append("服务端拒收 chat_template_kwargs，已摘掉重试（思考可能没关）")
                    return self._post(path, {k: v for k, v in body.items()
                                             if k != "chat_template_kwargs"}, _retry=False)
                raise _BadRequest(detail) from e
            raise RuntimeError("HTTP %d：%s" % (e.code, detail)) from e

    def tokenize(self, text):
        try:
            data = self._post("/tokenize", {"content": text})
            return len(data.get("tokens") or [])
        except Exception:
            return 0


# ─────────────────────────────────────────────────────────────
# 兼容外壳：节点侧的历史入口
# ─────────────────────────────────────────────────────────────

class GgufLocalServer:
    """进程内单例。`acquire` 幂等：同模型同端口直接复用 base_url。

    存在的理由只有一个：`MossPromptSmith` 与它的测试是按「一个节点跑一次、跑完还显存」
    写的，用的是 `acquire/stop` 这对类方法。真正的实现全在 `LlamaServer` 里——
    这里只做「留一个还是新建一个」的判断，不重复拼命令。
    """

    _server = None
    _lock = threading.Lock()
    #: 最近一次 acquire 的备注（MTP 启用/回退、端口复用），供 report 回显
    last_notes = []

    @classmethod
    def base_url(cls):
        port = cls._server.port if cls._server is not None else 8091
        return "http://127.0.0.1:%d/v1" % port

    @classmethod
    def running(cls):
        return cls._server is not None and cls._server.alive()

    @classmethod
    def find_exe(cls, server_exe="", source="auto"):
        return find_server_exe(server_exe, source)

    @classmethod
    def stop(cls):
        with cls._lock:
            if cls._server is not None:
                cls._server.stop()
            cls._server = None

    @classmethod
    def acquire(cls, model_path, mmproj="", ctx_size=8192, gpu_layers=None,
                cache_type_k="q8_0", cache_type_v="q8_0", flash_attn=True,
                image_max_tokens=1024, ubatch=512, parallel=1,
                fit_on=True, fit_target_mib=1024, mtp="auto",
                port=8091, server_exe="", timeout=300, draft_n_max=2, source="auto"):
        """返回 `(base_url, notes)`。失败一律 raise 带排查指引的中文错误，不静默回退。"""
        with cls._lock:
            model_path = str(Path(model_path).resolve())
            cur = cls._server
            if (cur is not None and cur.alive() and cur.model == model_path
                    and cur.port == int(port)):
                cls.last_notes = list(cur.notes)
                return cls.base_url(), cls.last_notes
            if cur is not None:
                cur.stop()                       # 模型/端口变了，或进程已死：清残壳
                cls._server = None
            srv = LlamaServer(
                model=model_path, exe=server_exe, source=source, port=port, ctx=ctx_size,
                ngl=gpu_layers if gpu_layers not in (None, "") else "auto",
                mmproj=mmproj, timeout=timeout, vision_tokens=image_max_tokens,
                cache_type_k=cache_type_k, cache_type_v=cache_type_v,
                ubatch=ubatch, parallel=parallel, fit_on=fit_on,
                fit_target_mib=fit_target_mib, flash_attn=flash_attn,
                mtp=mtp, draft_n_max=draft_n_max)
            try:
                srv.start()
            except Exception:
                srv.stop()
                raise
            cls._server = srv
            cls.last_notes = list(srv.notes)
            return cls.base_url(), cls.last_notes
