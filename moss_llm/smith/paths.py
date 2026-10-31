# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""外部资源定位：模型权重目录（本模块）与 llama-server 可执行文件（转发父包）。

这个包零依赖、零配置也能跑——确定性编译不需要任何外部东西。只有「要调模型」
时才需要权重和服务端，那些东西的**位置是用户的私事**，所以不该写死在代码里。

规则按优先级，全部可解释：

1. 显式参数（`--model` / `--server` / `--lora`）永远优先。
2. 环境变量：
   - ``MOSS_LLM_MODEL_DIRS``   模型目录，多个用 ``os.pathsep`` 分隔
   - ``MOSS_LLM_SERVER``       llama-server 可执行文件（**与父包同一个变量名**）
   - 旧名 ``PROMPT_ARTISAN_MODEL_DIRS`` / ``PROMPT_ARTISAN_SERVER`` 一并认
3. 约定目录：``~/models``、``./models``、``models/LLM``、``models/text_encoders``。
4. ``PATH`` 里的 ``llama-server``（``shutil.which``）。

**不猜**：这几处都没有就明说没有，不扫全盘、不去网上找。找不到时的正确输出
是「缺什么」，不是「大概是这个」。

## 收敛记录（2026-10-31）

`find_llama_server` 与 `supports_flag` 以前各有一份实现，父包 `server.py` 的
`find_exe` / `help_flags` 是另一份，搜索顺序还不一样（父包多认 `LLAMA_CPP_HOME`
与包内 `runtime/`）。现在「可执行文件在哪」**只有父包一个答案**，本模块转发；
`supports_flag` 也走父包那份带缓存的 `--help` 探测，不再每次起一个进程。
模型目录这一半父包没有，因此留在这里。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 模型目录的环境变量。多个目录用 ``os.pathsep`` 分隔（POSIX 是 ``:``，Windows 是 ``;``）。
ENV_MODEL_DIRS = "MOSS_LLM_MODEL_DIRS"

#: llama-server 可执行文件的环境变量。**与父包 `moss_llm/server.py` 同一个**。
ENV_LLAMA_SERVER = "MOSS_LLM_SERVER"

#: 旧名（本层独立成包时用过）。并存期一并认，删掉会让设过变量的用户静默失效。
LEGACY_ENV = ("PROMPT_ARTISAN_MODEL_DIRS", "PROMPT_ARTISAN_LLAMA_SERVER",
              "PROMPT_ARTISAN_SERVER")

#: 约定目录。`models/LLM` 与 `models/text_encoders` 是 ComfyUI 的约定 ——
#: 绝大多数用户不会另设变量，他们的权重就在那里。
CONVENTIONAL_DIRS = ("~/models", "models", "models/LLM", "models/text_encoders")


def model_dirs():
    """按优先级列出要搜的模型目录。存在的在前，不存在的也保留（调用方可提示）。"""
    out = []
    raw = os.environ.get(ENV_MODEL_DIRS) or ""
    if not raw:
        raw = next((os.environ.get(k) or "" for k in LEGACY_ENV
                    if (os.environ.get(k) or "").strip()), "")
    for chunk in raw.split(os.pathsep):
        chunk = chunk.strip()
        if chunk:
            out.append(Path(chunk).expanduser())
    for d in CONVENTIONAL_DIRS:
        p = Path(d).expanduser()
        if p.is_absolute():
            out.append(p)
        else:
            out.append(Path.cwd() / d)
    # 去重但保序
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower() if os.name == "nt" else str(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def find_files(patterns, min_size=1 << 30, exclude=("lora",)):
    """在各模型目录里按文件名找文件（子串、不区分大小写）。

    `patterns` 可以是字符串或列表；`exclude` 里的词出现在文件名里就跳过
    （默认排除 LoRA 适配器——它们不是基座）。
    """
    if patterns is None:
        wants = []                      # None = 不按名字筛，全都要
    elif isinstance(patterns, str):
        wants = [patterns.lower()] if patterns else []
    else:
        wants = [p.lower() for p in patterns if p]
    hits = []
    seen = set()
    for d in model_dirs():
        if not d.is_dir():
            continue
        try:
            entries = list(d.glob("*.gguf"))
        except OSError:
            continue
        for f in entries:
            name = f.name.lower()
            if any(x and x in name for x in exclude):
                continue
            if wants and not any(w in name for w in wants):
                continue
            try:
                if f.stat().st_size < min_size:
                    continue
            except OSError:
                continue
            key = str(f).lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(f)
    return hits


def find_llama_server():
    """找 llama-server。显式环境变量 → LLAMA_CPP_HOME → 包内 runtime/ → PATH。

    实现转发父包（那里还认节点的「服务器程序」参数与旧环境变量名）。
    找不到返回空串，与历史签名一致。
    """
    from ..server import find_server_exe
    return find_server_exe() or ""


def supports_flag(exe, flag, timeout=20.0):
    """这个可执行文件支不支持某个命令行开关。走父包的 `--help` 探测（带缓存）。

    问不出来算不支持——拼一个它不认的参数，进程会启动即退出，报错还和真实原因无关。
    """
    if not exe:
        return False
    from ..server import LlamaServer
    return flag in LlamaServer.help_flags(exe)


def hint():
    """找不到东西时该告诉用户什么。一句话，不啰嗦。"""
    return ("把权重放进 ~/models（或设 %s），把 llama-server 放进 PATH"
            "（或设 %s）" % (ENV_MODEL_DIRS, ENV_LLAMA_SERVER))
