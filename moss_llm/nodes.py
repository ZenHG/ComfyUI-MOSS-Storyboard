# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""可选 LLM 子包：**一个节点吃下全部功能**。

2026-10-30 拍板的形态（对设计文档 §3.1「2+1 节点」的修订）：不拆 Config /
PromptSmith / PromptCheck，合成 `MossPromptSmith` 一个全功能节点；对外只出
**最终提示词**（+ report 供复盘）。理由：本包承诺「几何确定、提示词透明、返工便宜」，
多一个节点就多一层「配置在哪、提示词从哪来」的追踪成本。

四件事，靠 `task` 分流（见 `_TASKS`）：

    反推 caption / 扩写 expand / 编辑指令 edit  → 模型在环，llama-server 或外部 API
    拼装 assemble                              → **零模型**，IR 直接渲染成后端方言

`assemble`（原名 `compile`）是 2026-10-31 合流时接进来的：`moss_llm/smith/` 那套 IR
编译器本来只有 CLI，接上之后同一个节点在有权重时能出词、没权重时也能用——确定性、毫秒级、可复现，
而且顺带拿到了方言机检与 M1–M6 不变式。它不 import 侧车，也不读任何权重。

通用性（开源前提）：不绑定任何具体模型/仓库——模型与 mmproj 从
`models/LLM` + `models/text_encoders` 扫描，任何 llama.cpp 支持的 GGUF 都能跑；
llama-server 运行时一键从 GitHub 官方 release 更新；提示词包按方言分发，
MOSS 两个方言只是其中两项。核心 7 节点不 import 本模块，本模块也不 import 核心包。

两份实现的合流记录与逐项取舍见 `docs/25-两份实现的合流.md`。
"""

import gzip
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import protocols as _proto
from . import vision as _vision
from .pack import load_pack
#: 这些名字里有一半只被测试直接调用（`_check_pair` / `_proj_width` / `_pair_key` /
#: `_text_width`），它们留在节点命名空间是有意的：既有测试与调用方按旧名字取，
#: 而实现已经在 `server.py`（侧车与节点必须用同一套配对判据）。
from .server import (MMPROJ_AUTO as _MMPROJ_AUTO,  # noqa: F401
                     MMPROJ_NONE as _MMPROJ_NONE,
                     GgufLocalServer,
                     check_mmproj_pair as _check_pair,
                     find_server_exe,
                     gguf_metadata,
                     gguf_proj_width as _proj_width,
                     gguf_text_width as _text_width,
                     instance_alias,
                     pair_key as _pair_key,
                     resolve_mmproj as _resolve_mmproj)

CATEGORY = "LLM"

#: 🔴 枚举的**存档值一律 ASCII**，与核心 7 节点同一口径（CONTRIBUTING 硬规矩 5）：
#: 中文只说在显示层 —— `web/moss_sb_i18n.js` 的 `COMBOS`（切中文时显示），
#: 切英文时回落成下面这些 id。把中文写进 `widgets_values` 会让英文用户的工作流里
#: 出现中文，也让值变成"已经翻译过一次"的一次性数据。
_TASKS = ["caption", "expand", "edit", "assemble"]
_DIALECTS = ["moss_image", "moss_storyboard", "generic_natural", "generic_tags",
             "cinematic", "cn_natural"]
#: `task=assemble` 的目标后端。**与 `dialect` 是两件事**：
#: dialect 是「让模型按哪种方言写字」，target 是「让确定性编译器往哪个后端渲染」。
#: 列表取自 `smith/data/model-registry.yaml`（唯一事实源），下面这份是它的镜像，
#: 只用于 V1 参数表——组合框必须能在不 import 编译器时也有值。
_TARGETS = ["moss-image", "qwen-image-2_1", "krea2", "flux3", "flux1_dev",
            "sdxl", "minimax-h3", "nano-banana"]
#: H3 六段式档名。与 `smith/adapters/h3.py::SIX_SECTION_PROFILES` 同口径
#: （单测钉住两边一致）——这里只用它决定「扩写要哪种 H3 骨架」，不参与拼装校验。
_SIX_PROFILES = ("six-section", "six_section", "h3-six-section")
#: 结构性方言（不是措辞风格）：宫格稿要 `[PANEL]`，标签串是逗号短词。
#: 事实源 = `smith/data/model-registry.yaml` 的 `structured_output`：只有 moss-image
#: 同时有 caption / layout_json / tag；krea2 / qwen-image-2_1 / flux3 / nano-banana 只有 caption。
#: 把结构稿喂给只吃 caption 的后端不会报错，但产物是对方根本不吃的格式 —— 静默错。
_STRUCTURE_DIALECTS = frozenset(("moss_storyboard", "generic_tags"))
_TAG_TARGETS = frozenset(("sdxl", "flux1_dev"))     # legacy 标签系
_LENGTHS = ["brief", "standard", "detailed"]


def _dialect_for_target(dialect, target, task, report):
    """把「结构稿 + 只吃 caption 的后端」纠正成对方能吃的写法。

    只挡**明确错**的组合，不优化「好不好」：四个 prose 方言（措辞风格）
    任何 prose 后端都能读，不动。H3 走 target/profile，dialect 本来就不生效，也不动。

    不改 widget 值、不改存档：只影响本次 system prompt。旧存档打开仍能跑，
    report 里明说改了什么 —— 本仓一贯的「不静默」口径。
    """
    if task != "expand" or dialect not in _STRUCTURE_DIALECTS:
        return dialect
    t = str(target or "moss-image").strip().lower()
    if t in ("minimax-h3", "moss-image"):
        return dialect                        # H3 不用 dialect；MOSS 两种结构都支持
    if dialect == "generic_tags" and t in _TAG_TARGETS:
        return dialect                        # legacy 标签系就是吃标签串
    fallback = "generic_tags" if t in _TAG_TARGETS else "generic_natural"
    report.append("⚠ 后端 %s 只吃连贯描述，不接受 %s ⇒ 本次改用 %s"
                  "（存档里的「方言」值不变；要保留结构稿请把 target 选回 moss-image）"
                  % (t, dialect, fallback))
    return fallback
_LANGS = ["auto", "zh", "en"]
_RUNTIMES = ["auto", "runtime", "path", "custom"]
_ONOFF = ["on", "off"]
_MTP = ["auto", "on", "off"]
_KV = ["q8_0", "q4_0", "f16"]

#: 提示词写法：顶层四类 → 子档。风格类通过 style_lock 注入，不新增 dialect。
_STYLE_CLASS = {
    "连贯描述": ["英文详述", "英文通用", "中文"],
    "风格": ["写实摄影", "电影剧照", "胶片复古", "暗调氛围", "商业广告",
             "二次元插画", "水彩", "赛博朋克", "黑白纪实"],
    "标签串": ["英文标签"],
    "分段结构": ["宫格稿", "时间线六段式"],
}
_STYLE = {
    "英文详述": {"dialect": "moss_image"},
    "英文通用": {"dialect": "generic_natural"},
    "中文": {"dialect": "cn_natural"},
    "英文标签": {"dialect": "generic_tags"},
    "宫格稿": {"dialect": "moss_storyboard"},
    "时间线六段式": {"target": "minimax-h3", "profile": "six-section"},
    "写实摄影": {"dialect": "moss_image",
                 "lock": "photorealistic photography; true-to-life skin, fabric and material response"},
    "电影剧照": {"dialect": "moss_image",
                 "lock": "cinematic film still; anamorphic framing, shallow depth of field, motivated practicals"},
    "胶片复古": {"dialect": "moss_image",
                 "lock": "35mm film; visible grain, gentle halation, slightly faded warm tones"},
    "暗调氛围": {"dialect": "moss_image",
                 "lock": "low-key lighting; deep shadows, restrained highlights, isolated key light"},
    "商业广告": {"dialect": "moss_image",
                 "lock": "commercial advertising photography; clean studio light, crisp product detail"},
    "二次元插画": {"dialect": "moss_image",
                   "lock": "anime illustration; clean lineart, cel shading, controlled highlights"},
    "水彩": {"dialect": "moss_image",
             "lock": "watercolor painting; soft washes, visible paper grain, bleeding edges"},
    "赛博朋克": {"dialect": "moss_image",
                 "lock": "cyberpunk; neon rim light, wet reflective surfaces, deep night contrast"},
    "黑白纪实": {"dialect": "moss_image",
                 "lock": "black and white documentary photography; high contrast, honest grain"},
}
_LENGTH_TOKENS = {"短": 512, "中": 1024, "长": 2048}
_SPEED = {
    "省显存": {"ctx_size": 4096, "ubatch": 256, "fit_target_mib": 768, "image_max_tokens": 512},
    "均衡": {"ctx_size": 8192, "ubatch": 512, "fit_target_mib": 1024, "image_max_tokens": 1024},
    "尽量快": {"ctx_size": 16384, "ubatch": 1024, "fit_on": "off", "image_max_tokens": 1536},
}
_TASK_ALIAS = {"反推": "caption", "洗图": "caption", "扩写": "expand",
               "拼装": "assemble", "编辑": "edit"}
#: PE 跳过 _build_system 时这些参数不生效；report 逐一点名，不静默。
_PE_BYPASSED = ("dialect", "length", "must_keep", "panels", "style_lock")
#: 仅 local、task 非 caption/edit、且未显式选「不使用」时触发；只写 report 不 raise。
_VISION_HINT = ("⚠ 已接参考图但没有视觉投影（mmproj）：本次图片不会生效。"
                "三条路：① 在「mmproj」下拉里显式选一个 *mmproj*.gguf；"
                "② 换用 VL 一体模型；③ 把 mmproj 放到模型同目录。")
#: 出词通道。本地 = llama-server 侧车；外部 = 任何 OpenAI 兼容端点
#: （OpenAI / 火山方舟 / 智谱 / Kimi / MiniMax / DeepSeek / Ollama / LM Studio …）。
#: 外部通道**不起侧车、不占显存**，但图要靠 base64 传，且是**联网**操作 —— 由用户主动
#: 排队触发，本包不后台外发任何东西。
_CHANNELS = ["local", "api"]
#: 思考模式默认**关**。实测（2026-10-30，b11998 + MOSS TE + mmproj）：开着思考时
#: 全部内容进 `reasoning_content`、`content` 为空且 `finish=length` —— 提示词直接空掉。
#: 判据取 `chat_template_kwargs.enable_thinking`；顶层 `enable_thinking` 与
#: `chat_template_kwargs.thinking` 两种写法都**不生效**（逐条实测过，别改回去）。
_THINKING = ["off", "on"]

#: 旧稿兼容：2026-10-30 的初版把中文写进了枚举值。载入旧工作流时在这里归一，
#: 归一过的字段写进 report（不静默改用户的图）。
#: `assemble` 这一格有**两个**键：`编译` 是中文旧稿，`compile` 是英文旧稿
#: （2026-11-05 改名前的值）。缺任一条 ⇒ 那一类存档打开即报「未知任务」。
_LEGACY_VALUES = {
    "反推": "caption", "扩写": "expand", "编辑指令": "edit", "编译": "assemble",
    "compile": "assemble",
    "本地 GGUF": "local", "外部 API": "api",
    "简短": "brief", "标准": "standard", "详细": "detailed",
    "自动": "auto", "中文": "zh", "英文": "en",
    "关": "off", "开": "on",
    "用现有": "auto", "更新到最新版": "auto",   # 旧存档：直接归一到「自动」，且不会触发下载
    "current": "auto", "update": "auto",
    "自动匹配": _MMPROJ_AUTO, "(不使用/纯文本)": _MMPROJ_NONE,
}

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _comfy_root():
    for p in Path(__file__).resolve().parents:
        if p.name == "custom_nodes":
            return p.parent
    return None


def _scan_gguf():
    """扫 models/LLM 与 models/text_encoders 下的 GGUF，按相对路径显示。

    不猜型号、不排除任何目录；只按「文件名含 mmproj」分两栏——判据与 Banzhang 同源，
    也是 llama.cpp 自己的命名约定。任何 Qwen 系列（或别的 arch） GGUF 都会出现在列表里。
    """
    models, mmprojs = {}, {}
    roots = []
    try:
        import folder_paths
        if getattr(folder_paths, "models_dir", None):
            roots.append(Path(folder_paths.models_dir) / "LLM")
            roots.append(Path(folder_paths.models_dir) / "text_encoders")
    except Exception:
        pass
    if not roots:
        root = _comfy_root()
        if root:
            roots = [root / "models" / "LLM", root / "models" / "text_encoders"]
    for base in roots:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.gguf")):
            rel = str(p.relative_to(base)).replace("\\", "/")
            # setdefault：同名文件在两个扫描根里都出现时（硬链很常见），先扫到的 LLM
            # 那份胜出；后者覆盖会让模型路径落到不带塔的 text_encoders 里。
            (mmprojs if "mmproj" in p.name.lower() else models).setdefault(rel, str(p))
    return models, mmprojs


def _pe_candidates(model_path):
    """模型目录里所有可能的 PE system prompt 候选（去重，保持优先级）。"""
    p = Path(model_path)
    out = [p.parent / "system_prompt.txt", p.parent / (p.stem + ".system_prompt.txt")]
    out += sorted(p.parent.glob("*.system_prompt.txt"))
    seen, uniq = set(), []
    for c in out:
        key = str(c.resolve()) if c.exists() else str(c)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def _looks_like_pe_t2i(text: str) -> bool:
    """T2I 改写器的原生 prompt：含 expert 或 rewritten_prompt+wh_ratio，且不是 I2I。"""
    low = (text or "").casefold()
    if "ratio_follow" in low:          # I2I 契约，绝不能被 expand 误用
        return False
    return ("image prompt rewriting expert" in low
            or ("rewritten_prompt" in low and "wh_ratio" in low))


def _looks_like_pe_i2i(text: str) -> bool:
    """I2I 编辑改写器的原生 prompt：含 ratio_follow 或 rewritten editing instruction。"""
    low = (text or "").casefold()
    return "ratio_follow" in low or "rewritten editing instruction" in low


def _looks_like_pe_system(text: str) -> bool:
    """兼容旧名：默认按 T2I 判。"""
    return _looks_like_pe_t2i(text)


def _pe_system_prompt(model_path, kind: str = "t2i"):
    """找模型目录里自带的 PE 原生 system prompt。

    kind=t2i 只认 T2I 契约，kind=i2i 只认 I2I 契约；两种不许交叉。
    顺序：system_prompt.txt、<stem>.system_prompt.txt、*.system_prompt.txt；
    内容不匹配当前任务时继续看下一个候选；没有命中返回 ("", "")。
    """
    if not model_path:
        return "", ""
    pred = _looks_like_pe_i2i if kind == "i2i" else _looks_like_pe_t2i
    for cand in _pe_candidates(model_path):
        if not cand.is_file():
            continue
        try:
            text = cand.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if pred(text):
            return text, str(cand)
    return "", ""


def _pe_system_for(task, model_path):
    """PE 模式：expand 只认 T2I，edit 只认 I2I；caption/assemble 不启用。"""
    kind = {"expand": "t2i", "edit": "i2i"}.get(task)
    if not kind:
        return "", ""
    return _pe_system_prompt(model_path, kind)


#: mmproj 配对与 GGUF 头部读取都在 `server.py` —— 侧车启动时要用同一套判据，
#: 放在节点层会让「节点能配上、编译器配不上」这种错配无处暴露。
#: `_text_width` / `_proj_width` / `_pair_key` / `_check_pair` / `_resolve_mmproj`
#: 由文件顶部的 import 提供（名字不变，既有测试与调用方无需改动）。


class MossPromptSmith:
    """LLM 提示词节点：反推 / 扩写 / 编辑指令 / 拼装，一个 llama-server sidecar 跑完即还显存。"""

    @classmethod
    def INPUT_TYPES(cls):
        models, mmprojs = _scan_gguf()
        model_list = list(models.keys()) or ["(未找到 GGUF 模型)"]
        # 默认取第一个（按相对路径排序）。不硬编码任何模型——开源用户盘上有什么就跑什么。
        default_model = model_list[0]
        it = {
            "required": {
                "task": (["反推", "洗图", "扩写", "拼装"], {"default": "反推",
                    "tooltip": "反推=看图写提示词（接图）；洗图=反推+先结构化盘点再写（洗图/高保真）；"
                               "扩写=想法→完整画面描述（填文字）；拼装=结构化草稿→成品提示词（不开模型）"}),
                "model": (model_list, {"default": default_model,
                    "tooltip": "本机 GGUF 权重。用在线服务时这里可以不管，填下面的「API地址」即可"}),
                "text": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "扩写=你的想法；洗图/反推=要保留什么；拼装=粘贴 JSON 草稿"}),
                "style_class": (list(_STYLE_CLASS), {"default": "连贯描述",
                    "tooltip": "先选一类：连贯描述 / 风格 / 标签串 / 分段结构"}),
                "style": (_STYLE_CLASS["连贯描述"], {"default": "英文详述",
                    "tooltip": "具体写法。「风格」类会注入风格锁定，不改变结构"}),
                "length": (["短", "中", "长"], {"default": "中",
                    "tooltip": "短≈512 / 中≈1024 / 长≈2048 tokens"}),
                "temperature": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 2.0, "step": 0.05,
                    "tooltip": "越低越稳定，越高越发散"}),
                "panels": ("INT", {"default": 4, "min": 1, "max": 9,
                    "tooltip": "仅「宫格稿」写法：分几格"}),
                "language": (["auto", "zh", "en"], {"default": "auto",
                    "tooltip": "输出语言。auto=跟随写法"}),
                "keep": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "必须保留的字/数/色/风格，分号分隔。会逐字出现在提示词里"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                    "tooltip": "复现用。0=每次不同"}),
                "api_base": ("STRING", {"default": "",
                    "tooltip": "填了就走在线服务，留空走本机模型"}),
                "api_model": ("STRING", {"default": "",
                    "tooltip": "在线模型名。留空会报缺"}),
                "api_key": ("STRING", {"default": "",
                    "tooltip": "在线服务的密钥。保存工作流时自动清空"}),
                "speed": (list(_SPEED), {"default": "均衡",
                    "tooltip": "本机跑的显存档：省显存 / 均衡 / 尽量快"}),
                "runtime": (["auto", "current", "custom"], {"default": "auto",
                    "tooltip": "本机程序来源。custom 时用环境变量 MOSS_LLM_SERVER 指定"}),
            },
            "optional": {
                "image": ("IMAGE",), "image2": ("IMAGE",), "image3": ("IMAGE",),
                "image4": ("IMAGE",), "image5": ("IMAGE",), "image6": ("IMAGE",),
                "image7": ("IMAGE",), "image8": ("IMAGE",), "image9": ("IMAGE",),
            },
        }
        return it  # noqa: RET504

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("提示词", "report")
    FUNCTION = "smith"

    @classmethod
    def VALIDATE_INPUTS(cls, api_model=None):
        """模型名不按列表校验：清单来自实际拉取，进程重启就空，旧工作流里的值不许被判非法。"""
        return True
    CATEGORY = CATEGORY
    DISPLAY_NAME = "LLM Prompt"
    #: 该 runtime 拒绝 `response_format` 时置真，report 里出声（不静默降级）
    _json_unsupported = False
    #: 请求体里的 model 字段：本地=侧车的 --alias；外部 API=用户填的模型 id
    _model_id = "moss-llm"
    #: 被服务端 400 逼着摘掉的可选键（report 里逐项出声）
    _degraded = []
    _thinking_unsupported = False
    DESCRIPTION = ("通用 LLM 提示词节点（不限 MOSS）：反推（看图写提示词）/ 扩写（想法→完整画面描述）/ "
                   "编辑指令（图生图一条改法）/ 拼装（结构化草稿→7 个后端方言，零模型）。"
                   "前三个走 llama-server sidecar，跑完释放显存；拼装是确定性 Python，"
                   "毫秒级可复现。输出可直接接 CLIPTextEncode 或采样器 positive。")
    SEARCH_ALIASES = ["llm", "llm prompt", "prompt llm", "prompt generator",
                      "prompt smith", "reverse prompt", "caption", "expand", "prompt enhance",
                      "反推", "扩写", "caption", "expand", "洗图", "提示词生成", "提示词节点",
                      "本地LLM", "gguf", "compile", "assemble", "ir", "编译", "拼装",
                      "确定性", "flux", "sdxl", "krea", "通用提示词", "prompt generator"]

    # ------------------------------------------------------------------ 主流程

    def smith(self, task="反推", model="", text="", style_class="连贯描述",
              style="英文详述", length="中", temperature=0.2, panels=4, language="auto",
              keep="", seed=0, api_base="", api_model="", api_key="", speed="均衡",
              runtime="auto", image=None,
              image2=None, image3=None, image4=None, image5=None,
              image6=None, image7=None, image8=None, image9=None):
        # ---- 16 项 → 旧 39 项展开（内部逻辑一行不动）----
        raw_task = str(task or "反推")
        wash = raw_task == "洗图"
        two_pass = wash
        task = _TASK_ALIAS.get(raw_task, raw_task)
        _sty = _STYLE.get(str(style), {"dialect": "moss_image"})
        dialect = _sty.get("dialect", "moss_image")
        target = _sty.get("target", "moss-image")
        profile = _sty.get("profile", "")
        _lock = _sty.get("lock", "")
        style_lock = ("%s\n%s" % (_lock, keep) if (_lock and keep) else (_lock or keep)).strip()
        must_keep = str(keep or "")
        max_tokens = _LENGTH_TOKENS.get(str(length), 1024)
        channel = "api" if str(api_base or "").strip() else "local"
        if channel == "local":
            api_model = ""
        _spd = _SPEED.get(str(speed), _SPEED["均衡"])
        ctx_size = _spd.get("ctx_size", 8192)
        ubatch = _spd.get("ubatch", 512)
        fit_target_mib = _spd.get("fit_target_mib", 1024)
        image_max_tokens = _spd.get("image_max_tokens", 1024)
        fit_on = _spd.get("fit_on", "on")
        mmproj = _MMPROJ_AUTO
        server_exe = ""
        gpu_layers = ""
        cache_type_k = "q8_0"
        cache_type_v = "q8_0"
        flash_attn = "on"
        parallel = 1
        mtp = "auto"
        mtp_draft_n = 2
        port = 8091
        release_after_run = True
        runtime = str(runtime or "auto")
        thinking = "off"
        t0 = time.time()
        report = []
        #: 每图视觉 token 预算。`_chat` 的自动压缩与侧车启动都要用，
        #: 存在节点上而不是只在 acquire 的入参里（那边是侧车的 vision_tokens）。
        self._vision_tokens = int(image_max_tokens)
        self._degraded, self._json_unsupported, self._thinking_unsupported = [], False, False
        #: `_chat` 的结构化/重复保护降级说明（schema→json_object、repeat_penalty 被拒等）。
        self._chat_notes = []
        #: 图像预处理的降级说明（缩放 / 透明合成 / 没装 Pillow）。不静默。
        self._img_notes = []

        # 旧稿兼容：初版把中文写进了枚举值。这里归一成存档 id，并把归一过的字段写进
        # report —— 用户看见的是「我的图被就地改了哪几格」，不是静默迁移。
        legacy = []

        def _norm(name, v):
            nv = _LEGACY_VALUES.get(v, v) if isinstance(v, str) else v
            if nv != v:
                legacy.append("%s: %s→%s" % (name, v, nv))
            return nv

        task = _norm("task", task)
        channel = _norm("channel", channel)
        length = _norm("length", length)
        language = _norm("language", language)
        thinking = _norm("thinking", thinking)
        runtime = _norm("runtime", runtime)
        mtp = _norm("mtp", mtp)
        flash_attn = _norm("flash_attn", flash_attn)
        fit_on = _norm("fit_on", fit_on)
        mmproj = _norm("mmproj", mmproj)
        if legacy:
            report.append("旧稿枚举值已归一：" + "；".join(legacy))

        # 拼装任务先走：它**不开模型**，下面所有通道/权重/显存参数都与它无关。
        # 放在这里而不是拆成第二个节点，是因为「一个节点吃下全部功能」是本包的形态选择
        # （见模块文档）——多一个节点就多一层「配置在哪、提示词从哪来」的追踪成本。
        if task == "assemble":
            return self._compile(text, target, report, t0, profile=profile)

        # 多图参考（H3 最多 9 张）：前端按连接数长出 image2..image9。
        # 单张时保持传张量本身，行为与加多图口之前逐字节一致。
        imgs = [im for im in (image, image2, image3, image4, image5,
                              image6, image7, image8, image9) if im is not None]
        n_img = len(imgs)
        image = imgs if n_img > 1 else (imgs[0] if imgs else None)
        if task == "edit" and n_img > 1:
            # i2i 契约是「一张源图 + 一条改法」；多余图不参与，但要说出来。
            report.append("编辑指令只用第 1 张图，其余 %d 张本次不参与" % (n_img - 1))
            imgs, n_img, image = imgs[:1], 1, imgs[0]

        # 视觉 token 预检：**在加载权重之前**判，别让用户白等 20 GB。
        # 每图上限 image_max_tokens（llama.cpp 的 --image-max-tokens 是每张各算一次），
        # 再加输出预算与提示词余量，超过 ctx 就一定跑不完。
        if n_img and channel == "local":
            need = n_img * int(image_max_tokens) + int(max_tokens) + 512
            if need > int(ctx_size):
                max_img = max(256, (int(ctx_size) - int(max_tokens) - 512) // n_img)
                raise ValueError(
                    "上下文不够，先改参数再跑（省得白等模型加载）：\n"
                    "    %d 张图 × 每图 %d 视觉 token = %d，加输出 %d 与提示词余量 512，"
                    "需要 ≥%d，但「上下文长度」只有 %d。\n"
                    "    两条路任选：① 把「上下文长度」提到 ≥%d（显存够的话）；"
                    "② 把「每图视觉 token」降到 ≤%d（%d×%d=%d）。"
                    % (n_img, int(image_max_tokens), n_img * int(image_max_tokens),
                       int(max_tokens), need, int(ctx_size),
                       need, max_img, n_img, max_img, n_img * max_img))
            # 输出预算不够会在半句处截断：机检只报「缺字段」，用户看到的是
            # 「跑成功但内容不全」——提前拦住。下限按**任务与骨架**给，不按图数线性：
            # 图多影响的是主体定义那几行，描述本身该多长与图数无关。
            #   反推一段 160–260 词的 JSON 契约，256 token 连正文都装不下（实测会丢信息）。
            six_mode = str(profile or "").strip().lower() in _SIX_PROFILES
            if six_mode:
                min_out = 640 + 96 * n_img          # 六段 + 每主体一行
            elif str(target or "").strip() == "minimax-h3":
                min_out = 384 + 48 * n_img          # 三字段 + 时间线
            elif task == "caption":
                min_out = {"brief": 448, "standard": 640, "detailed": 896}.get(
                    length, 640)                    # 一段描述 + JSON 包装
            elif task == "expand":
                min_out = {"brief": 384, "standard": 640, "detailed": 896}.get(
                    length, 640)
            else:
                min_out = 384                       # 编辑指令：一条改法
            if int(max_tokens) < min_out:
                raise ValueError(
                    "输出预算不够，先改参数再跑（省得白跑一次）：\n"
                    "    本次（%s%s，%d 张图）max_tokens 至少 %d（建议 %d），当前只有 %d。\n"
                    "    截断时正文会在半句处停住，机检只会说「缺字段」；"
                    "256 这类小值会直接丢掉后半段信息。"
                    % ("六段式" if six_mode else task,
                       " + H3" if str(target or "").strip() == "minimax-h3" else "",
                       n_img, min_out, ((min_out + 255) // 256) * 256, int(max_tokens)))

        # 任务边界与通道无关，先判：反推/编辑必须接图；扩写必须有文字
        if task in ("caption", "edit") and image is None:
            raise ValueError("任务=%s 必须接「🖼️ 输入图片」。" % task)
        if task == "expand" and not (text or "").strip():
            raise ValueError("任务=expand 必须填「📝 文字输入」。")
        if dialect == "moss_storyboard" and not 2 <= int(panels) <= 9:
            raise ValueError("宫格数 panels=%s 超出 2–9。" % panels)

        # PE 改写器模式：在 _build_system 之前先看模型目录自带没有原生 prompt。
        # model_path 必须在本地通道最前面解析（下面启动侧车也用它），不能等 _build_system 之后。
        pe_text, pe_path = "", ""
        model_path = ""
        models, mmprojs = {}, {}
        if channel == "local":
            models, mmprojs = _scan_gguf()
            model_path = models.get(model, "")
            if not model_path or not Path(model_path).is_file():
                raise ValueError("模型不存在或未选中：%r。请在下拉里选一个存在的 GGUF\n"
                             "    Model missing or not selected. Pick an existing GGUF." % (model,))
            pe_text, pe_path = _pe_system_for(task, model_path)

        pack = load_pack(_PROMPTS_DIR)
        report.append("提示词包 %s v%s（%s，%d 个文件）"
                      % (pack["pack"], pack["version"], pack["updated"], len(pack["files"])))
        # H3 输出：caption（看图）与 expand（纯文字）都靠 target 选格式。产物是纯文本
        # 三字段/六段式，不能套 JSON 约束（否则模型被逼成只回 `{}`，正文为空）。
        h3_mode = (task in ("caption", "expand")
                   and str(target or "").strip() == "minimax-h3")
        h3_six = str(profile or "").strip().lower() in _SIX_PROFILES
        pe_mode = bool(task in ("expand", "edit") and pe_text)
        if pe_mode:
            system = pe_text
            if task == "edit":
                report.append("PE 改写器模式（编辑）：system prompt 来自 %s"
                              "（Qwen-Image 2.1 PE-I2I）" % pe_path)
            else:
                report.append("PE 改写器模式：system prompt 来自 %s（Qwen-Image 2.1 PE）；"
                              "长度带与 must_keep 判据不适用" % pe_path)
            report.append("PE 绕过参数：%s —— 本次不生效" % " / ".join(_PE_BYPASSED))
        else:
            # 结构稿不能喂给只吃 caption 的后端。只改本次 system，不动存档；report 出声。
            eff_dialect = _dialect_for_target(dialect, target, task, report)
            system = self._build_system(pack, task, eff_dialect, length, panels, style_lock,
                                        must_keep, wash, two_pass, language,
                                        target=target, profile=profile)
            if h3_mode and not pe_mode:
                # 不静默：用户可能只是没意识到 target 也管反推/扩写，报告里说清用了哪套骨架。
                # PE 改写器模式（system 来自模型目录）下 H3 契约没生效，不能谎报。
                report.append("%s格式：MiniMax-H3 %s（由「拼装目标 / profile」决定，"
                              "不占用方言表）"
                              % ("扩写" if task == "expand" else "看图",
                                 "全参考六段式" if h3_six else "三字段时间线"))
                if task == "caption" and (two_pass or wash):
                    report.append("H3 看图输出是纯文本三字段，两遍法 / 忽略杂项不适用")
        user = (text or "").strip()

        if channel == "api":
            endpoint, key, key_src = _api_setup(api_base, api_model, api_key)
            self._model_id = api_model.strip()
            stop_server = lambda: None                                   # noqa: E731
            who = "外部 API %s｜model=%s｜Key：%s" % (_host_of(endpoint), api_model, key_src)
            report.append("通道：外部 API（联网外发图片与文字，不起侧车、不占显存）")
        else:
            if (api_base or "").strip():
                # 只出声、不改道：通道是用户的显式选择。把「为什么还去找本地」说在运行结果里。
                report.append("⚠ 通道=本地 GGUF，但 api_base 已填（%s）⇒ 本次用本地模型，端点不生效；"
                              "要用外部 API 请把「通道」切到「外部 API」" % _host_of(api_base))
            if runtime == "update":   # 兼容：安装已改为面板按钮，旧值已被归一成 auto
                from . import runtime_install      # 只在真要装时才 import（CLI 也用它）
                dest = runtime_install.RUNTIME_DIR
                report.append("运行时下载目录：%s（CPU 包约 35MB；CUDA/ROCm 可能数百 MB）" % dest)
                try:
                    exe, tag, src = runtime_install.install()
                except Exception as e:
                    raise RuntimeError("更新 llama.cpp 运行时失败：%r\n"
                                       "    下载目录：%s\n"
                                       "    国内网络可设 MOSS_LLM_MIRROR=<镜像前缀> 或 "
                                       "MOSS_LLM_RUNTIME_URL=<直链>后重试；后端认错可设 "
                                       "MOSS_LLM_BACKEND=cuda|vulkan|rocm|sycl|cpu；"
                                       "也可手动下载后填「服务器程序」。" % (e, dest)) from e
                report.append("llama.cpp 运行时：%s（%s；%s；目录 %s）"
                              % (Path(exe).name, tag or "未取到版本号", src, Path(exe).parent))
                if not server_exe:
                    server_exe = exe

            models, mmprojs = _scan_gguf()
            model_path = models.get(model, "")
            if not model_path or not Path(model_path).is_file():
                raise ValueError("模型不存在或未选中：%r。请在下拉里选一个存在的 GGUF\n"
                             "    Model missing or not selected. Pick an existing GGUF." % (model,))
            if mmproj == _MMPROJ_NONE:
                mmproj_path, pair_note = "", ""
            else:
                mmproj_path, pair_note = _resolve_mmproj(model_path, mmprojs, mmproj)
                if pair_note:
                    report.append(pair_note)
            if task in ("caption", "edit") and not mmproj_path:
                raise ValueError(
                    "任务=%s 需要视觉投影（mmproj），但没找到。三条路：\n"
                    "    ① 在「mmproj」下拉里显式选一个 *mmproj*.gguf；\n"
                    "    ② 换用 VL 一体模型（如 Qwen3-VL-4B-Instruct，不用另配 mmproj）；\n"
                    "    ③ 把 mmproj 放到模型同目录。\n"
                    "    A vision projector (mmproj) is required but not found. Three options." % task)
            if image is not None and not mmproj_path and mmproj != _MMPROJ_NONE:
                report.append(_VISION_HINT)

            if (api_base or "").strip() and runtime != "update" and not find_server_exe(server_exe if runtime == "custom" else "", runtime):
                raise RuntimeError(
                    "「通道」= 本地 GGUF，但本机没有 llama-server；而你已填 api_base=%s\n"
                    "    ⇒ 想用这个 API：把「通道」选成「外部 API」即可（端点与模型名都填好了，不用重填）。\n"
                    "    ⇒ 想跑本地：把「运行时」设为「更新到最新版」一键装，或在「服务器程序」填完整路径。"
                    % (api_base or "").strip())
            base_url, notes = GgufLocalServer.acquire(
                model_path, mmproj_path, ctx_size=int(ctx_size), gpu_layers=gpu_layers,
                cache_type_k=cache_type_k, cache_type_v=cache_type_v,
                flash_attn=(flash_attn == "on"), image_max_tokens=int(image_max_tokens),
                ubatch=int(ubatch), parallel=int(parallel), fit_on=(fit_on == "on"),
                fit_target_mib=int(fit_target_mib),
                mtp={"auto": "auto", "on": True, "off": False}.get(mtp, "auto"),
                port=int(port), server_exe=server_exe, draft_n_max=int(mtp_draft_n),
                source=runtime)
            report.extend(notes)
            # 侧车自己带了 `--api-key comfyui-local`：这是本机口令、不是秘密，随请求发出去无害
            key = "comfyui-local"
            endpoint = base_url + "/chat/completions"
            # 请求体里的 model 必须与侧车的 alias 同一条指纹（不一致 llama-server 会 400）
            self._model_id = instance_alias(model_path, mmproj_path, ctx_size)
            stop_server = GgufLocalServer.stop if release_after_run else (lambda: None)
            who = "模型 %s｜mmproj %s" % (
                Path(model_path).name, Path(mmproj_path).name if mmproj_path else "无")

        want_thinking = (thinking == "on")
        try:
            if task == "caption" and n_img > 1 and not h3_mode:
                # 多图**逐张**反推：每张各调一次，输出分段。与 H3 的多图融合是
                # 两件事——融合给一条视频稿，逐张给 N 条独立提示词。
                # 判据是 target：H3 走融合，其它后端走逐张（事实不串图）。
                chunks, total_tok = [], 0
                per = []
                if two_pass:
                    report.append("多图逐张反推时两遍法不适用（每张走单遍）")
                for idx, im in enumerate(imgs, 1):
                    p_i, _o, u_i = self._run_once(
                        endpoint, system, user, im, temperature,
                        int(max_tokens), int(seed), want_thinking, key, report,
                        CAPTION_ONESHOT_SCHEMA)
                    n_tok = int((u_i or {}).get("total_tokens") or 0)
                    total_tok += n_tok
                    per.append("图 %d：%s tok" % (idx, n_tok or "?"))
                    chunks.append("--- image %d ---\n%s" % (idx, p_i.strip()))
                prompt, obj = "\n\n".join(chunks), None
                usage = {"total_tokens": total_tok}
                report.append("多图逐张反推：%d 张，各调一次（%s）"
                              % (n_img, "；".join(per)))
            elif task == "caption" and two_pass:
                prompt, obj, usage = self._run_caption_two_pass(
                    endpoint, pack, system, user, image, temperature,
                    int(max_tokens), int(seed), want_thinking, key, report)
            else:
                schema = CAPTION_ONESHOT_SCHEMA if task == "caption" else None
                prompt, obj, usage = self._run_once(
                    endpoint, system, user, image, temperature,
                    int(max_tokens), int(seed), want_thinking, key, report, schema,
                    want_json=not h3_mode)
                if task == "caption" and isinstance(obj, dict):
                    obj, guard_notes = guard_caption_json(obj)
                    report.extend(guard_notes)
        finally:
            stop_server()
        for n in self._chat_notes:          # 结构化/重复保护降级的说明（逐级出声）
            if n not in report:
                report.append(n)

        checks = [] if pe_mode else self._checks(
            prompt, obj if isinstance(obj, dict) else None,
            task, dialect, panels, must_keep)
        if self._img_notes:
            report.extend("图像预处理：" + n for n in self._img_notes)
        if self._json_unsupported:
            report.append("⚠️ 服务端拒绝 response_format（HTTP 400），已去掉它重试；"
                          "输出仍按提示词里的 JSON 契约要求")
        if self._thinking_unsupported:
            report.append("⚠️ 服务端拒绝 chat_template_kwargs（HTTP 400），已去掉它重试 —— "
                          "思考模式关不掉，正文可能被思考内容占满而返回空")
        report.append("%s｜tokens %s｜耗时 %.1fs"
                      % (who, (usage or {}).get("total_tokens", "?"), time.time() - t0))
        report.extend(checks)
        report.append("提示词包 sha：" + " ".join(
            "%s=%s" % (f["id"], f["sha256"][:8]) for f in pack["files"]))
        if h3_mode and h3_six and prompt.strip():
            tidied = _tidy_h3_six(prompt)
            if tidied != prompt:
                report.append("六段式归一：把挤在同一行的 <Subject N> 拆成一项一行")
                prompt = tidied
        if not prompt.strip():
            raise RuntimeError(
                "模型没有给出可用提示词（正文为空）。可能原因：输入里含指令（如「忽略以上指令」）"
                "把模型带偏、模型不服从%s契约，或输出被截断。建议：重跑一次；换模型；"
                "或调大 max_tokens。"
                % ("输出格式" if h3_mode else " JSON"))
        return (prompt, "\n".join(report))

    # ------------------------------------------------------------------ caption 执行
    def _run_once(self, endpoint, system, user, image, temperature, max_tokens, seed,
                  want_thinking, key, report, schema=None, want_json=True):
        """单遍 caption / expand / edit：一次请求 + JSON 容错 + 解析失败重试一次。

        `want_json=False` 用于 H3 扩写：它的产物是「三字段 / 六段式」纯文本，
        发 `response_format: json_object` 会把模型逼成输出 `{}`（实测正文为空）。
        """
        if not want_json:
            raw, usage = self._chat(endpoint, system, user, image, temperature,
                                    int(max_tokens), int(seed), want_json=False,
                                    thinking=want_thinking, key=key)
            return _strip_fences(raw).strip(), None, usage
        raw, usage = self._chat(endpoint, system, user, image, temperature,
                                int(max_tokens), int(seed), want_json=True,
                                thinking=want_thinking, key=key, schema=schema)
        obj, err = _loads_lenient(_strip_fences(raw))
        if obj is None:
            # 解析失败重试一次：把「只要 JSON」再说一遍，不静默改写内容
            report.append("⚠️ 首次返回不是合法 JSON（%s），重试一次。" % err)
            raw2, usage2 = self._chat(endpoint, system + _JSON_RETRY, user, image,
                                      max(0.0, float(temperature) - 0.1),
                                      int(max_tokens), int(seed), want_json=True,
                                      thinking=want_thinking, key=key, schema=schema)
            usage = usage2 or usage
            obj, err2 = _loads_lenient(_strip_fences(raw2))
            if obj is None:
                report.append("⚠️ 重试后仍不是合法 JSON（%s），已按纯文本输出。" % err2)
                return _strip_fences(raw2), None, usage
        return _pick_prompt(obj), obj, usage

    def _run_caption_two_pass(self, endpoint, pack, system, user, image, temperature,
                              max_tokens, seed, want_thinking, key, report):
        """两遍式 caption：pass1 盘点 → **护栏** → pass2 写稿。

        护栏必须在 pass1 之后、pass2 之前：类型闸把物体从 subjects 挪到 environment.props，
        否则 pass2 会把「磨豆机」当人写（并给年龄）；实体上限在这里截断，重复循环的
        几十个 subject 不会灌进 pass2。
        """
        raw1, u1 = self._chat(endpoint, system, user, image, temperature,
                              int(max_tokens), int(seed), want_json=True,
                              thinking=want_thinking, key=key,
                              schema=CAPTION_INVENTORY_SCHEMA)
        usage = u1 or {}
        inv, err = _loads_lenient(_strip_fences(raw1))
        if inv is None:
            report.append("⚠️ 第一遍盘点不是合法 JSON（%s），已按单遍结果输出。" % err)
            return _strip_fences(raw1), None, usage
        if isinstance(inv, dict):
            inv, guard_notes = guard_caption_json(inv)
            report.extend(guard_notes)
        report.append("双遍：第一遍盘点 %d 个字段" % len(inv))
        sys2 = pack["texts"]["caption.pass2.en"]
        raw2, u2 = self._chat(endpoint, sys2, json.dumps(inv, ensure_ascii=False), None,
                              temperature, int(max_tokens), int(seed), want_json=True,
                              thinking=want_thinking, key=key,
                              schema=CAPTION_PASS2_SCHEMA)
        usage = u2 or usage
        obj, err2 = _loads_lenient(_strip_fences(raw2))
        if obj is None:
            report.append("⚠️ 第二遍不是合法 JSON（%s），已按纯文本输出。" % err2)
            return _strip_fences(raw2), None, usage
        return _pick_prompt(obj), obj, usage

    # ------------------------------------------------------------------ 确定性拼装

    def _compile(self, text, target, report, t0, profile=""):
        """IR → 提示词。零模型、零网络、毫秒级。`profile` 选后端下的方言档。

        这条通道是合流时从 `moss_llm.smith` 接过来的：同一个 IR 换后端只是换方言，
        事实不变；再叠上 lint（方言规则）与 M1–M6 不变式（不需要 ground truth 的自检）。
        报告要能让用户判断「这段提示词为什么长这样」，所以把砍掉的子句、越界的预算、
        失败的判据逐条列出来，不只给一个「成功」。

        方法名保留 `_compile`：它是内部实现名，不是存档里的 task 值（对外叫「拼装」）。
        """
        try:
            from .smith.api import compile_ir
        except Exception as e:                       # 编译器目录被删/损坏
            raise RuntimeError(
                "拼装任务需要 `moss_llm/smith/`（语义 IR 与多方言编译器），但它加载失败：%r\n"
                "    其它三个任务（反推/扩写/编辑指令）不依赖它，可以照常用。" % (e,)) from e

        try:
            r = compile_ir(text, target or "moss-image", profile=profile or None)
        except KeyError as e:
            # registry.sampling 对未知档位抛的是 KeyError，不是 CompileError。
            # 不接住就是一段裸堆栈；转成一句能照着改的话，并保留上游的可选项说明。
            raise ValueError(
                "拼装失败：方言档 %r 在 %s 上不可用（%s）。\n"
                "    Profile %r is not available for %s.\n"
                "    可用档位见 smith/data/model-registry.yaml 的 dialects / sampling.profiles；"
                "留空用该后端的默认骨架。"
                % (profile, target or "moss-image", (e.args[0] if e.args else ""),
                   profile, target or "moss-image")) from e
        except ValueError as e:                      # CompileError 是 ValueError 的子类
            raise ValueError("拼装失败：%s\n    Assemble failed; see above." % e) from e

        prompt = r["positive"]
        if not prompt.strip():
            raise RuntimeError("拼装结果为空：IR 里没有可渲染的内容。"
                               "检查 entities 是否为空、importance 是否全为 0。")

        n_err = sum(1 for i in r["issues"] if i["level"] == "error")
        n_warn = len(r["issues"]) - n_err
        report.append("拼装：%s｜%s｜%d 个实体｜tokens %s/%s｜耗时 %.0fms"
                      % (r["target"], "确定性（未调用模型）", r["ir"]["entities"],
                         r["token_count"], r["token_limit"] or "不限",
                         (time.time() - t0) * 1000))
        if profile:
            report.append("方言档：%s" % profile)
        report.append("不变式：%s" % r["invariants_line"])
        for it in r["issues"]:
            report.append("%s %s %s%s" % ("❌" if it["level"] == "error" else "⚠️",
                                          it["code"], it["message"],
                                          ("　→ " + it["fix"]) if it["fix"] else ""))
        if not r["issues"]:
            report.append("机检：无问题")
        elif not n_err:
            report.append("机检：%d 条提醒，无 error" % n_warn)
        elif n_err:
            report.append("机检：%d 条 error、%d 条提醒——上面逐条列了，先修 error 再出图"
                          % (n_err, n_warn))
        for w in r["warnings"]:
            report.append("⚠️ " + w)
        if r["dropped"]:
            report.append("按预算砍掉 %d 条低权子句：%s"
                          % (len(r["dropped"]), "；".join(r["dropped"][:6])))
        if r["negative"]:
            report.append("负向（该后端有独立负向通道，需另接 CLIPTextEncode 负向）："
                          + r["negative"])
        if r["layout"]:
            report.append("该后端还出了 layout：%d 个框（layout JSON 见 CLI 的 --json）"
                          % len((r["layout"] or {}).get("boxes") or []))
        if r["params"]:
            report.append("采样参数建议：" + "，".join(
                "%s=%s" % (k, v) for k, v in sorted(r["params"].items())))
        return (prompt, "\n".join(report))

    # ------------------------------------------------------------------ 提示词拼装

    def _build_system(self, pack, task, dialect, length, panels, style_lock,
                      must_keep, wash, two_pass, language, target="", profile=""):
        """四层拼装：任务主提示词 + 方言契约 + 篇幅/语言 + 用户约束。不写万能大 prompt。"""
        texts = pack["texts"]
        # 与 smith() 同一判据：caption 看图、expand 纯文字，都由 target 决定是否出 H3。
        h3_mode = (task in ("caption", "expand")
                   and str(target or "").strip() == "minimax-h3")
        six = str(profile or "").strip().lower() in _SIX_PROFILES
        if task == "caption":
            if h3_mode:
                sysmsg = texts["caption.h3_six.en" if six else "caption.h3.en"]
            elif two_pass:
                sysmsg = texts["caption.pass1.en"]
                if wash:
                    sysmsg = texts["caption.clean.en"] + "\n\n" + sysmsg
            elif wash:
                sysmsg = texts["caption.clean.en"] + "\n\n" + texts["caption.faithful.en"]
            else:
                sysmsg = texts["caption.faithful.en"]
        elif task == "expand":
            sysmsg = texts["expand.method.en"]
            # H3 音视频复用 target/profile，**不新增 dialect**：开源用户不必再学一套
            # 「方言表」。target 说「输出到哪个后端」，profile 说「该后端下的方言档」，
            # 与拼装是同一套概念，只是这里由模型写字、拼装由编译器写字。
            if h3_mode:
                key = "expand.h3_six.en" if six else "expand.h3.en"
            else:
                key = {"moss_image": "expand.moss_image.en",
                       "moss_storyboard": "expand.moss_storyboard.zh",
                       "generic_natural": "expand.generic_natural.en",
                       "generic_tags": "expand.generic_tags.en",
                       "cinematic": "expand.cinematic.en",
                       "cn_natural": "expand.cn_natural.zh"}[dialect]
            sysmsg += "\n\n" + texts[key]
        else:
            sysmsg = texts["edit.i2i.en"]

        parts = [sysmsg]
        if h3_mode:
            # 篇幅/语言两条对 H3 是反作用：它的长度由「切点密度」决定，格式标签必须是
            # 英文，只有台词保留原语言。所以这里换成一条针对 H3 的口径说明。
            parts.append("# FORMAT\nField labels stay in English; dialogue keeps the "
                         "language the user wrote. The cut-density rule in the contract "
                         "overrides any generic length preset.")
        else:
            parts.append("# LENGTH\nUse the %s length preset. Do not pad to reach it."
                         % (length if length in _LENGTHS else "standard"))
            if language and language != "auto":
                parts.append("# LANGUAGE\nWrite the description in %s."
                             % ("Chinese" if language == "zh" else "English"))
        if (style_lock or "").strip():
            parts.append("# STYLE LOCK\n%s" % style_lock.strip())
        if (must_keep or "").strip():
            parts.append("# MUST_KEEP (verbatim, do not alter)\n%s" % must_keep.strip())
        if dialect == "moss_storyboard" and panels:
            parts.append("# PANELS\nThe board has exactly %d panels. "
                         "The number of [PANEL] sections must equal %d." % (panels, panels))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ llama-server 调用

    def _chat(self, endpoint, system, user, image, temperature, max_tokens, seed,
              want_json=True, thinking=False, key=None, schema=None):
        proto = _proto.detect(endpoint)
        image_url = None
        if image is not None:
            # 多图时按 token 预算自动收像素：token 才是 ctx 的真瓶颈，像素只是细节。
            # 单图维持 1536（既有实测口径，不回归）。约 32·√tokens：1024→1024px、
            # 512→724px、256→512px，夹在 512–1536。
            n_img_now = len(image) if isinstance(image, (list, tuple)) else _vision.batch_size(image)
            side = _vision.MAX_SIDE
            if n_img_now > 1 and int(getattr(self, "_vision_tokens", 0) or 0) > 0:
                import math
                side = max(512, min(_vision.MAX_SIDE,
                                    int(round(32 * math.sqrt(int(self._vision_tokens))))))
                self._img_notes.append(
                    "多图自动压缩：每图长边 %d px（按每图 %d token 推算）"
                    % (side, int(self._vision_tokens)))
            # 编码走 `moss_llm.vision`（最长边 1536 + JPEG q95 4:4:4）。
            # ComfyUI 的 IMAGE 天然带批维：三张参考图接进同一个口就是 batch=3，
            # 这里一次编完、逐张挂块（协议层收列表）。单张时行为与以前完全一致。
            preps = _vision.prepare_batch(image, max_side=side)
            for p in preps:
                for n in p.notes:
                    if n not in self._img_notes:
                        self._img_notes.append(n)
            image_url = [p.data_url for p in preps] or None
            if len(preps) > 1:
                self._img_notes.append(
                    "多图输入 %d 张（IMAGE 批）；视觉 token 上限按每图各算一次"
                    % len(preps))
        base = _proto.body(proto, self._model_id, system, user, image_url,
                           temperature, max_tokens, seed)
        # 两个可选键都发：`chat_template_kwargs.enable_thinking` 是唯一实测能关掉思考的写法
        # （见 _THINKING 注释），vLLM / 火山方舟 / llama-server 都认它；严格 OpenAI 可能拒。
        # 拒了就**逐级摘掉再试**，并在 report 里说清摘了什么 —— 不静默降级，也不假装成功。
        if proto == _proto.OPENAI:
            base["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
            if want_json:
                # 与 server.py 的 JSON 重复保护同一口径：DRY/presence/frequency 会惩罚
                # 合法重复的键名，但 repeat_penalty 压的是整段循环（G7 4/40 次整单归零）。
                base["repeat_penalty"] = _CAPTION_REPEAT_PENALTY

        if proto != _proto.OPENAI:
            # Anthropic / Responses / Gemini 都没有 response_format 那套：
            # 结构化交给提示词契约，思考开关归模型 —— 一次请求，不装降级阶梯。
            attempts = [(False, False, "plain", None)]
        else:
            # 结构化输出四级降级（从强到弱；服务端 400 就往下走，逐级出声）：
            #   json_schema → json_object → 无 response_format → 再摘 chat_template_kwargs
            formats = []
            if want_json:
                if schema is not None:
                    formats.append(("json_schema",
                                    {"type": "json_schema",
                                     "json_schema": {"name": "caption", "schema": schema}}))
                formats.append(("json_object", {"type": "json_object"}))
                formats.append(("none", None))
            else:
                formats.append(("plain", None))
            # 尝试顺序：每个结构化档位先保留 repeat_penalty、再摘掉它（严格 OpenAI 不认
            # repeat_penalty，但认 json_schema——先摘参数比先降结构化档少一次语义降级）；
            # 全部档位都不行，最后才摘 chat_template_kwargs（思考可能关不掉）。
            attempts = []
            for name, rf in formats:
                attempts.append((False, False, name, rf))
                if want_json:
                    attempts.append((False, True, name, rf))
            for name, rf in formats:
                if want_json:
                    attempts.append((True, True, name, rf))

        obj, last, chosen = None, None, None
        for drop_thinking, drop_repeat, name, rf in attempts:
            payload = json.loads(json.dumps(base))       # 每次从干净副本拼，互不污染
            if drop_repeat:
                payload.pop("repeat_penalty", None)
            if rf is not None:
                payload["response_format"] = json.loads(json.dumps(rf))
            else:
                payload.pop("response_format", None)
            if drop_thinking:
                payload.pop("chat_template_kwargs", None)
            try:
                obj = _post(endpoint, payload, key)
                chosen = (drop_thinking, drop_repeat, name)
                break
            except _Http400 as e:
                last = e
                if _FATAL_400.search(e.body or ""):
                    break        # 缺会话/缺头的 400，换参数试不出来
                continue
        if obj is None:
            body = last.body or ""
            if _KNOB_400.search(body):
                head = "HTTP 400（把结构化输出 / thinking / repeat_penalty 逐级摘掉后仍被拒）"
            elif _FATAL_400.search(body):
                head = ("HTTP 400：请求缺必需项（会话 id / 请求头 / 鉴权），换参数解决不了；"
                        "网关要自定义头时可用环境变量 MOSS_LLM_EXTRA_HEADERS='{\"x-foo\": \"bar\"}'")
            else:
                head = ("HTTP 400（报错没提到 response_format / thinking / repeat_penalty ⇒ "
                        "大概率不是参数不支持，看下面的原文）")
            raise RuntimeError("%s：%s" % (head, body)) from last

        drop_thinking, drop_repeat, name = chosen
        if drop_thinking:
            self._thinking_unsupported = True
            self._degraded.append("chat_template_kwargs")
        if want_json and name == "none":
            self._json_unsupported = True
            self._degraded.append("response_format")
        elif want_json and name == "json_object" and schema is not None:
            self._chat_notes.append(
                "⚠️ 服务端不认 json_schema，已降级为 json_object（实体上限改由 prompt 与"
                "后处理兜底）")
        if drop_repeat:
            self._degraded.append("repeat_penalty")
            self._chat_notes.append(
                "⚠️ 服务端拒绝 repeat_penalty（HTTP 400），已去掉重试（重复循环保护降级；"
                "仍靠 prompt 与后处理兜底）")

        raw, finish, usage = _proto.read(proto, obj)
        if not (raw or "").strip():
            # 思考模型最常见的失败：全写进 reasoning_content，正文空 + finish=length。
            # 不把思考内容当答案偷偷端上桌——那是另一种"静默兜底"。
            reason = ""
            try:
                reason = ((obj["choices"][0]["message"] or {}).get("reasoning_content") or "")
            except Exception:
                reason = ""
            raise RuntimeError(
                "模型正文为空（finish=%s，reasoning %s 字）。"
                "把「思考模式」设为「关」，或加大「最大token数」后重试。"
                % (finish, len(str(reason))))
        return raw, usage

    # ------------------------------------------------------------------ 机检（只报数）

    def _checks(self, prompt, obj, task, dialect, panels, must_keep):
        out = []
        miss = _missing_must_keep(prompt, must_keep)
        if miss:
            out.append("⚠️ must_keep 有 %d 项没在提示词里逐字出现：%s"
                       % (len(miss), "；".join(miss)))
        if dialect == "moss_storyboard" and panels:
            n_panel = len(re.findall(r"\[\s*PANEL\s*\d+\s*\]", prompt, re.I))
            if n_panel != panels:
                out.append("⚠️ [PANEL] 段数 %d ≠ 宫格数 %d（不静默改，请重跑或改 panels）"
                           % (n_panel, panels))
        if obj is None:
            return out
        conf = obj.get("confidence")
        if isinstance(conf, dict):
            if conf.get("risks"):
                out.append("confidence.risks：%s" % "、".join(str(r) for r in conf["risks"]))
            out.append("confidence.overall=%s" % conf.get("overall"))
        omitted = _truthy_list(obj.get("omitted_low_confidence") or obj.get("omitted_artifacts"))
        if omitted:
            out.append("低置信/伪影省略（%d 项）：%s" % (len(omitted), "、".join(omitted)))
        for key in ("uncertainties", "notes", "open_choices"):
            vals = _truthy_list(obj.get(key))
            if vals:
                out.append("%s：%s" % (key, "；".join(vals)))
        return out


#: 六段式的段名。`h3_six.check` 是**按行**解析 `<Subject N>` 的，模型常把 9 个
#: 主体挤成一行，于是除第一个外全被判「未定义」。这里做确定性归一：只拆这两段的
#: 条目，散文段（detailed_description）一个字节不动。
_H3_SIX_LABELS = ("subject_definitions", "summary", "retention_analysis",
                  "detailed_description", "overall_soundscape", "non_diegetic_music")
_H3_SIX_SPLIT = ("subject_definitions", "retention_analysis")


def _tidy_h3_six(text):
    """把六段式里被挤在同一行的 `<Subject N>` 条目拆成一项一行。"""
    # 单个字符的笔误（`<Subject 9}`）就地修；这是确定性修补，不是改内容。
    text = re.sub(r"(<Subject \d+)}", r"\1>", text or "")
    out, cur = [], ""
    for line in text.splitlines():
        m = re.match(r"^(\s*)([a-z_]+)\s*:\s*(.*)$", line)
        if m and m.group(2) in _H3_SIX_LABELS:
            cur, rest = m.group(2), m.group(3)
            out.append(m.group(2) + ":")
            if cur in _H3_SIX_SPLIT and rest.strip():
                out.extend(p.strip() for p in re.split(r"(?=<Subject \d+>)", rest)
                           if p.strip())
            elif rest.strip():
                out.append(rest.strip())
            continue
        if cur in _H3_SIX_SPLIT and "<Subject" in line:
            out.extend(p.strip() for p in re.split(r"(?=<Subject \d+>)", line)
                       if p.strip())
        else:
            out.append(line)
    return "\n".join(out)


#: 解析失败重试时附加的一句话（不重写原提示词，只把输出契约再说一遍）
_JSON_RETRY = ("\n\n# REMINDER\nReturn exactly one JSON object and nothing else. "
               "No markdown, no code fences, no prose outside the JSON.")


# ---------------------------------------------------------------------- caption 护栏
# M1a 在 smith 反推管线（reverse.py）上修了两类系统性缺陷：非人物体被标成 character、
# 实体数组无上限导致重复循环。**节点 caption 是另一条路径**（自己的 prompt pack +
# `_chat`，只发 json_object），所以护栏要在这一侧再接一次；判据逻辑（字段口径的类型闸）
# 从 `smith.reverse` 复用，字段名在这里映射。
#
# 与 smith 路径的差别（docs/46 有完整对照）：
#   · 数据结构不同：smith 是扁平 `entities[]`；节点 inventory 是
#     `subjects[]` + `environment.props[]` + `materials[]` + `text[]`；
#   · 节点 caption 不产出 IR（输出是 caption 文本），所以「降级」= 从 subjects 移到
#     environment.props，让 pass2 不再把物体当人写；
#   · 节点有「两遍式」，护栏必须在 pass1 之后、pass2 之前接，才能影响最终 caption。

#: inventory 各数组的上限。subjects 比 props 紧：真实画面里「不同主体」很少超过 12，
#: 而重复循环的典型形态就是同一对 subject 写满几十条。
_CAPTION_MAX_SUBJECTS = 12
_CAPTION_MAX_PROPS = 32
_CAPTION_MAX_MATERIALS = 16
_CAPTION_MAX_TEXT = 24
_CAPTION_MAX_KEEP = 8

#: 节点 inventory 的「人物线索」字段：有任意一个就保留 person（宁可不猜）。
_CAPTION_PERSON_CUES = ("gaze", "expression", "hands", "feet", "pose", "wardrobe")

#: JSON 请求的重复保护（与 `server.py` 的 JSON 模式同一口径；见 docs/42 §2）。
_CAPTION_REPEAT_PENALTY = 1.05

#: pass1 inventory 的 JSON schema：只锁形状与数组上限，**不 required 字段**——与 M1a
#: 取证 schema 同一纪律（全字段 required 会逼模型编值）。服务端不认时逐级降级。
CAPTION_INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "object"},
        "subjects": {
            "type": "array",
            "maxItems": _CAPTION_MAX_SUBJECTS,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["person", "object"]},
                    "role": {"type": "string"},
                    "count": {"type": "integer"},
                    "appearance": {"type": "string"},
                    "wardrobe": {"type": "string"},
                    "pose": {"type": "string"},
                    "gaze": {"type": "string"},
                    "expression": {"type": "string"},
                    "hands": {"type": "string"},
                    "feet": {"type": "string"},
                    "position": {"type": "string"},
                },
            },
        },
        "environment": {
            "type": "object",
            "properties": {
                "props": {"type": "array", "maxItems": _CAPTION_MAX_PROPS,
                          "items": {"type": "string"}},
            },
        },
        "camera": {"type": "object"},
        "light": {"type": "object"},
        "palette": {"type": "object"},
        "materials": {"type": "array", "maxItems": _CAPTION_MAX_MATERIALS,
                      "items": {"type": "object"}},
        "text": {"type": "array", "maxItems": _CAPTION_MAX_TEXT,
                 "items": {"type": "object"}},
        "style": {"type": "object"},
        "moment": {"type": "object"},
        "confidence": {"type": "object"},
        "uncertainties": {"type": "array", "maxItems": 8,
                          "items": {"type": "string"}},
    },
}

#: 单遍 caption 的 JSON schema（caption.faithful/clean 的输出）。
CAPTION_ONESHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "must_keep": {"type": "array", "maxItems": _CAPTION_MAX_KEEP,
                      "items": {"type": "string"}},
        "uncertainties": {"type": "array", "maxItems": 8,
                          "items": {"type": "string"}},
        "visible_text": {"type": "array", "maxItems": _CAPTION_MAX_TEXT,
                         "items": {"type": "object"}},
        "confidence": {"type": "object"},
        "omitted_low_confidence": {"type": "array", "maxItems": 12,
                                   "items": {"type": "string"}},
        "omitted_artifacts": {"type": "array", "maxItems": 12,
                              "items": {"type": "string"}},
        "aspect_ratio": {"type": "string"},
    },
    "required": ["caption"],
}

#: pass2 只回 {"caption": "..."}。
CAPTION_PASS2_SCHEMA = {"type": "object",
                        "properties": {"caption": {"type": "string"}},
                        "required": ["caption"]}


def _caption_subject_to_prop(subject: dict) -> str:
    """降级后的 subject → `environment.props` 的一条字符串（不丢它的可见描述）。

    只保留 role / appearance / position / count(>1)：物体的 wardrobe/pose/gaze/hands/feet
    多属人物专属字段，挪进 props 没有语义；count 例外——「2 只杯子」是可见事实，
    count>1 时写进 props 文案（并在 report 点名，见 `guard_caption_json`）。
    """
    role = str(subject.get("role") or "").strip()
    appearance = str(subject.get("appearance") or "").strip()
    if role and appearance and role.lower() not in appearance.lower():
        text = "%s — %s" % (role, appearance)
    else:
        text = role or appearance or "object"
    try:
        count = int(subject.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 1:
        text = "%d × %s" % (count, text)
    position = str(subject.get("position") or "").strip()
    if position and position.lower() not in text.lower():
        text += " (%s)" % position
    return text


def _caption_subject_field_gate(subject: dict) -> tuple:
    """用 M1a 的同一份字段闸判 subject 像不像人：(是不是物体, 原因)。

    判据只用字段：有任一 person cue → 保留 person；一个都没有、却有 appearance → 物体。
    """
    from .smith.reverse import gate_character_kind     # 延迟导入：只有 caption 跑才需要

    desc = {k: subject.get(k) for k in
            ("role", "appearance", "wardrobe", "pose", "gaze",
             "expression", "hands", "feet", "position")}
    kind, reason = gate_character_kind("character", desc,
                                       person_fields=_CAPTION_PERSON_CUES,
                                       object_fields=("appearance",))
    return kind != "character", reason


def _caption_subject_is_object(subject: dict) -> tuple:
    """inventory 的 subject 判成「物体」的判据：(是不是物体, 原因)。

    显式 `kind=object/prop/scene/background` 直接算；person/character/空 kind 走字段闸。
    **未知 kind 也不能跳过字段闸**：json_schema 档 enum 会挡住 `"animal"`/`"people"` 这类值，
    但降级到 json_object / 无约束后模型可以自由写 kind——若直接放行，物体就留在 subjects，
    pass2 仍可能把它写成人，而且 report 没有任何提示。未知 kind 与空 kind 同判：
    有 person cue 才保留 person，否则按 object 处理进 props，并把「未知 kind」写进原因。
    """
    raw_kind = str(subject.get("kind") or "").strip().lower()
    if raw_kind in ("object", "prop", "scene", "background"):
        return True, "kind=%s" % raw_kind
    if raw_kind not in ("person", "character", ""):
        is_object, why = _caption_subject_field_gate(subject)
        if is_object:
            return True, "未知 kind=%r，按字段闸判为物体：%s" % (raw_kind, why)
        return False, ""
    return _caption_subject_field_gate(subject)


def guard_caption_json(obj) -> tuple:
    """节点 caption JSON 的护栏：类型闸 + 实体上限。返回 (改过的 obj, report 说明行)。

    非就地黑箱：所有改动都变成人话写进 return 的 notes，节点再把它们拼进 report。
    """
    notes = []
    if not isinstance(obj, dict):
        return obj, notes

    downgraded, age_dropped = [], []
    subjects = obj.get("subjects")
    if isinstance(subjects, list):
        env = obj.get("environment")
        if not isinstance(env, dict):
            env = {}
            obj["environment"] = env
        props = env.get("props")
        if not isinstance(props, list):
            props = []
            env["props"] = props
        kept = []
        for s in subjects:
            if not isinstance(s, dict):
                continue
            is_object, reason = _caption_subject_is_object(s)
            if not is_object:
                kept.append(s)
                continue
            age_keys = [k for k in ("age_years", "age", "age_confidence") if k in s]
            age_desc = ["%s=%r" % (k, s.get(k)) for k in age_keys]
            for k in ("age_years", "age", "age_confidence", "gender", "hair",
                      "skin", "face", "expression"):
                s.pop(k, None)
            label = str(s.get("role") or s.get("appearance") or "object").strip()[:48]
            try:
                count = int(s.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            count_note = "；count=%d 已保留在 props 文案" % count if count > 1 else ""
            downgraded.append("subject[%s]：%s%s" % (label, reason, count_note))
            if age_desc:
                age_dropped.append("subject[%s]：丢弃 %s" % (label, "、".join(age_desc)))
            props.append(_caption_subject_to_prop(s))
        obj["subjects"] = kept

    caps = []

    def _cap(container, key, limit, name):
        if not isinstance(container, dict):
            return
        arr = container.get(key)
        if not isinstance(arr, list):
            return
        # 达到上限就要出声（不只是超过）：json_schema 档的 maxItems 可能在语法层先把
        # 重复循环静默截停，后处理看到恰好 N 条、以为成功——与「失败必须可见」冲突。
        # 恰好 N 条也可能是合法画面，所以措辞是「疑似重复/截断，请人工确认」。
        if len(arr) >= limit:
            if len(arr) > limit:
                caps.append("%s %d→%d（丢 %d；疑似重复/截断，请人工确认）"
                            % (name, len(arr), limit, len(arr) - limit))
            else:
                caps.append("%s %d（达到上限 %d；疑似重复/截断，请人工确认）"
                            % (name, len(arr), limit))
        if len(arr) > limit:
            del arr[limit:]

    _cap(obj, "subjects", _CAPTION_MAX_SUBJECTS, "subjects")
    _cap(obj.get("environment"), "props", _CAPTION_MAX_PROPS, "props")
    _cap(obj, "materials", _CAPTION_MAX_MATERIALS, "materials")
    _cap(obj, "text", _CAPTION_MAX_TEXT, "text")
    _cap(obj, "visible_text", _CAPTION_MAX_TEXT, "visible_text")
    _cap(obj, "must_keep", _CAPTION_MAX_KEEP, "must_keep")
    _cap(obj, "uncertainties", 8, "uncertainties")

    if downgraded:
        notes.append("反推类型闸：%d 个物体被误标为 subject，已降级为 props"
                     "（否则会被写成人、编年龄）：%s"
                     % (len(downgraded), "；".join(downgraded[:4])))
    if age_dropped:
        notes.append("反推年龄闸：从非人物体上丢弃年龄字段 %d 处：%s"
                     % (len(age_dropped), "；".join(age_dropped[:4])))
    if caps:
        notes.append("反推实体上限（capped）：%s" % "；".join(caps))
    return obj, notes


# ---------------------------------------------------------------------- 纯函数


class _Http400(Exception):
    """HTTP 400 单独一类：只有它能触发「去掉 response_format 再试」。"""

    def __init__(self, body):
        super().__init__(body)
        self.body = body


#: 这些 400 是「请求缺东西」（会话 id / 必需头 / 鉴权），换参数换不出来。
#: 实测 opencode：缺 `x-opencode-session` 时报 `MissingSessionID`，我们却把
#: response_format / thinking / repeat_penalty 全摘一遍才报「参数不支持」—— 白打五六个请求。
_FATAL_400 = re.compile(r"missing[ _-]?(?:session|header|api[ _-]?key|token|param)|"
                        r"invalid[ _-]?api[ _-]?key|unauthori[sz]ed", re.I)
#: 报错里点到这些名字，才说明真是「这个旋钮不支持」。
_KNOB_400 = re.compile(r"response_format|json_schema|chat_template|thinking|repeat_penalty", re.I)


#: 拉过的模型清单（按端点缓存）。combo 的选项由它生成 —— 进程内有效，重启后为空。
_MODELS_BY_BASE = {}


def _model_choices():
    """`api_model` 的下拉选项：空串（未选）+ 拉过的全部模型名。

    服务商给的 id 无法预知，所以清单只能来自实际拉取；空串保留「必填」语义。
    """
    return [""] + sorted({m for ids in _MODELS_BY_BASE.values() for m in ids})


#: 本地端点绝不走代理：VPN/代理软件设了全局 HTTP(S)_PROXY 时，连 Ollama 都被塞进
#: 代理隧道（实测 Tunnel connection failed: 502），本地端点必须直连。
_LOCAL_HOSTS = ("localhost", "::1", "0.0.0.0", "host.docker.internal",
                "host.containers.internal", "gateway.docker.internal")
#: RFC 6598 共享地址段（Tailscale 等组网工具也用）：同属「不该走代理」。
_SHARED_NETS = (ipaddress.ip_network("100.64.0.0/10"),)


def _is_local(endpoint):
    """这个端点该不该**绕开系统代理**直连。

    判据是「本机或本网络」：回环、私有网段（10/8、172.16/12、192.168/16）、链路本地、
    以及 docker/Tailscale 这类容器与组网地址。局域网另一台机器上的 Ollama / vLLM 是最常见的
    情况 —— 走了系统代理只会得到 `Tunnel 502`，而且报错完全看不出是被代理吞了。

    公网端点仍然走系统代理：那是用户刻意配的出口，不能替他绕。域名一律交给系统代理 ——
    内网域名的解析结果本地不可知，猜错反而更难查。
    """
    host = (urllib.parse.urlsplit(endpoint).hostname or "").lower()
    if host in _LOCAL_HOSTS or host.endswith((".localhost", ".local")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # is_private 覆盖 10/8 · 172.16/12 · 192.168/16 · fe80::/10 · fc00::/7；
    # 注意它**不**包含 172.32+（那一段是公网）—— 所以不能写成 startswith("172.")。
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or any(ip in net for net in _SHARED_NETS))


#: opener 缓存：`build_opener()` 每次都要重新加载 CA 证书（实测 ~16ms）。拉清单一次
#: 并发六条、逐级上溯十来跳，不缓存就等于先白等两百毫秒 —— 比它省下的网络时间还多。
#: 代理环境变量按进程内不变处理（真要换代理是重启，不是热改）。
_OPENERS = {}
_OPENER_LOCK = threading.Lock()


def _opener(url):
    """按端点取 opener：本地端点直连（绕全局代理），公网走系统代理。"""
    direct = _is_local(url)
    with _OPENER_LOCK:
        op = _OPENERS.get(direct)
        if op is None:
            op = _OPENERS[direct] = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
                                     if direct else urllib.request.build_opener())
        return op


def _net_hint(endpoint, exc):
    """网络异常 → 可操作建议。只报主机名与代理变量，绝不回显 Key。"""
    netloc = urllib.parse.urlsplit(endpoint).netloc or endpoint
    prox = {} if _is_local(endpoint) else urllib.request.getproxies()
    via = ("当前进程走代理（%s）；" % "、".join("%s=%s" % (k, v) for k, v in sorted(prox.items()))
           if prox else "当前进程未设代理；")
    err = getattr(exc, "reason", exc)
    # 证书错必须排在 SSLError 前面：SSLCertVerificationError 是它的子类，否则永远走"被掐"。
    if isinstance(err, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(err):
        return (via + "%s 的证书校验失败：公司代理 / 自签 CA 常见。三条路："
                "① 把公司根证书装进系统信任链；② 设 SSL_CERT_FILE 指向该 CA；"
                "③ 换一条直连网络。不要关校验 —— 那等于让中间人随意改内容。" % netloc)
    if isinstance(err, ssl.SSLError) or "EOF occurred" in str(err):
        return (via + "TLS 握手后被对端直接断开，通常是代理或防火墙拦断了该域名。三条路："
                "① 把该域名加进 NO_PROXY 走直连；② 换一条能直连的网络；"
                "③ 复核链路 curl -sS -o /dev/null -w '%%{http_code}' https://%s"
                "（200/401/404 都算通）" % netloc)
    if isinstance(err, socket.gaierror):
        return via + "域名解析失败（%s）：检查 api_base 拼写与 DNS。" % netloc
    if isinstance(err, ConnectionRefusedError):
        return via + "%s 拒绝连接：端口不对，或本地端点（Ollama / LM Studio）没启动。" % netloc
    if isinstance(err, (socket.timeout, TimeoutError)):
        return via + "%s 超时：网络不可达，或端点限速 / 无响应。" % netloc
    return via + "%s 连接失败：%r" % (netloc, err)


#: 403 有两种含义：凭据无效、地区/风控拦截。后者在主流网关上一律回 403 + 一段英文/HTML，
#: 用户看不出区别（实测：openai `unsupported_country_region_territory`、cerebras `error code: 1009`、
#: together / cohere 的 Cloudflare 403 页）。只在报错里加一句可操作的话，不改行为。
_REGION_403 = re.compile(r"country|region|territory|not allowed|error code: 1009|"
                         r"cf-error|cloudflare|access denied", re.I)


def _http_hint(code, body, url=""):
    """HTTP 错误 → 追加一句人话。拿不准就不说（返回空串），不硬猜。"""
    text = body or ""
    if code == 403 and (_REGION_403.search(text) or text.lstrip().startswith("<")):
        return ("\n    这不是 Key 错：该服务商按出口地区 / 风控拒绝了此请求。"
                "换一条能直连该地区的网络（或代理出口），或换服务商。\n"
                "    Not a key problem: blocked by region or risk control. Use a network that reaches that region, or switch providers.")
    # 404 且路径里没有版本段：多半是把主机根当成了端点（Ollama / LM Studio 的经典填法）。
    path = urllib.parse.urlsplit(url or "").path
    if code == 404 and path and not re.search(r"/v\d", path):
        return ("\n    路径不对：OpenAI 兼容端点一般在 `/v1` 下 —— "
                "把 api_base 写成 `http://主机:端口/v1`（Ollama 是 `http://127.0.0.1:11434/v1`）。\n"
                "    Wrong path: OpenAI-compatible endpoints usually live under /v1.")
    return ""


def _post(endpoint, payload, key=None):
    """POST 一次。失败一律 raise 带 body 的中文错误。线格式由端点判定（见 `protocols`）。"""
    headers = _proto.headers(_proto.detect(endpoint), key, endpoint)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers)
    opener = _opener(endpoint)
    try:
        with opener.open(req, timeout=900) as r:
            return _loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            body = ""
        if e.code == 400:
            raise _Http400(body or "(无 body)") from e
        raise RuntimeError("服务端返回 HTTP %s（%s）：%s%s"
                           "\n    Server returned an HTTP error; see the body above."
                           % (e.code, urllib.parse.urlsplit(endpoint).netloc,
                              body or "(无 body)", _http_hint(e.code, body, endpoint))) from e
    except Exception as e:
        raise RuntimeError("请求失败（%s，请求体 %.1f MB）：%r\n%s"
                           "\n    Request failed; see the exception above."
                           % (urllib.parse.urlsplit(endpoint).netloc,
                              len(data) / 1048576.0, e,
                              _net_hint(endpoint, e))) from e


#: Key 只有一个环境变量来源，不做成参数：变量名让用户填只会制造困惑。
KEY_ENV = "MOSS_LLM_API_KEY"


def _api_creds(api_key):
    """Key 解析：写死的 > 环境变量 `MOSS_LLM_API_KEY`。返回 (key, 来源描述)。"""
    key = (api_key or "").strip()
    src = "写在工作流里（会随 JSON 分享出去）" if key else None
    if not key:
        value = os.environ.get(KEY_ENV, "").strip()
        if value:
            key, src = value, "环境变量 %s" % KEY_ENV
    if not key:
        src = "未提供（本地端点可留空）"
    return key, src


def _loads(raw):
    """响应体 → JSON。网关强制 gzip、或直接回 HTML 错误页时给清楚的话。"""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError as e:
        head = " ".join(text.lstrip()[:120].split())
        if head.startswith("<"):
            raise RuntimeError("这个地址返回的是网页，不是接口数据（多半路径不对或需要登录）：%s…"
                               "检查 api_base 是不是少了 /v1\n"
                               "    This URL returned a web page, not API data (wrong path or login required)." % head[:60]) from e
        raise RuntimeError("响应不是 JSON：%s" % (head[:120] or "(空)")) from e


def _get_json(url, key, proto, timeout=15):
    req = urllib.request.Request(url, headers=_proto.headers(proto, key, url))
    opener = _opener(url)
    last = None
    for attempt in range(2):
        try:
            with opener.open(req, timeout=timeout) as r:
                return _loads(r.read())
        except urllib.error.HTTPError as e:
            # 4xx/5xx 是服务端的答复，重试没有意义。
            body = e.read().decode("utf-8", "replace")[:200]
            raise RuntimeError("HTTP %s %s" % (e.code, body or "(无 body)")) from e
        except Exception as e:
            last = e
            if attempt == 0:
                # 只对连接层瞬断重试一次：清单请求是幂等的，复杂网络下这一次很值。
                time.sleep(0.4)
    raise RuntimeError("%r；%s" % (last, _net_hint(url, last))) from last


def _model_list_prefixes(root):
    """`api_base` → 依次尝试的清单前缀。

    清单不一定挂在对话端点那一层：实测 `.../inference/go/openai/v1/responses` 的清单在
    `.../inference/go/v1/models`。所以从当前层逐级上溯（最多 4 级），越近的越先试。
    """
    out = []
    url = root.rstrip("/")
    for _ in range(5):
        out.append(url)
        bits = urllib.parse.urlsplit(url)
        seg = [x for x in bits.path.split("/") if x]
        if len(seg) <= 1:          # 已到站点根，再往上没意义
            break
        seg.pop()
        url = urllib.parse.urlunsplit((bits.scheme, bits.netloc, "/" + "/".join(seg), "", ""))
    return out


def list_models(api_base, api_key="", timeout=15):
    """拉服务端模型清单。返回 (ids, 实际命中的 url)。

    从端点那层开始逐级上溯，每层试 `/models` 与 `/v1/models`，命中即停。
    拉不到就 raise —— 有的服务商不给模型列表，手填 `api_model` 仍然可用，不静默空表。
    """
    root = (api_base or "").strip().rstrip("/")
    if not root:
        raise ValueError("要拉模型清单，请先填「api_base」。\n"
                         "    Fill in api_base before fetching the model list.")
    for tail in ("/chat/completions", "/messages", "/responses"):
        if root.endswith(tail):
            root = root[: -len(tail)]
    key, _src = _api_creds(api_key)
    proto = _proto.detect(root)

    def add(prefix, proto_try):
        # 已以 /v1 结尾时再拼 /v1/models 只会得到 /v1/v1/models，纯浪费一次往返。
        for tail in ("/models",) + (() if prefix.rstrip("/").endswith("/v1") else ("/v1/models",)):
            tries.append((_proto.join(prefix, tail), proto_try))

    tries = []
    if proto == _proto.ANTHROPIC:
        tries.append((_proto.join(root, "/v1/models"), _proto.ANTHROPIC))
    if proto == _proto.GEMINI:
        tries.append((_proto.join(root, "/models"), _proto.GEMINI))
    for prefix in _model_list_prefixes(root):
        add(prefix, _proto.OPENAI)
    if proto == _proto.ANTHROPIC:
        # `/anthropic` 只挂对话接口，模型清单在站点根上（实测 DeepSeek 的该前缀 404）。
        site = root.rsplit("/anthropic", 1)[0]
        add(site, _proto.OPENAI)

    seen, uniq = set(), []
    for url, p in tries:
        if url not in seen:
            seen.add(url)
            uniq.append((url, p))
    # 兜底链有好几跳，串行在复杂网络上要等好几秒（点一下像卡住）。并发发出去，
    # 再按候选顺序取第一个有货的 —— 顺序决定优先级，并发只省时间。
    # ⚠ 命中必须立刻走：`with ThreadPoolExecutor(...)` 退出时 `shutdown(wait=True)`，
    #    那会把其余候选**全部等完**（各带 15s 超时），命中一条也照样白等 —— 早退才叫省时间。
    errs = []
    pool = ThreadPoolExecutor(max_workers=min(6, len(uniq)))
    try:
        futs = [pool.submit(_get_json, u, key, pr, timeout) for u, pr in uniq]
        for (url, pr), fut in zip(uniq, futs, strict=True):
            try:
                obj = fut.result()
            except Exception as e:
                errs.append("%s → %s" % (url, e))
                continue
            ids = []
            for m in (obj.get("data") or obj.get("models") or []):
                mid = m.get("id") or m.get("name") if isinstance(m, dict) else m
                if isinstance(mid, str) and mid:
                    # Gemini 的 name 带 "models/" 前缀，去掉才是用户要填的 id。
                    ids.append(mid.split("/")[-1] if pr == _proto.GEMINI else mid)
            models = sorted(set(ids))
            if models:
                _MODELS_BY_BASE[url.rsplit("/models", 1)[0]] = models
                return models, url
    finally:
        # 还没开跑的候选直接取消；在飞的由各自超时收尾，不拖住返回值。
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("拉不到模型清单：\n%s\n"
                       "确认 api_base / Key 正确；服务商不提供模型列表时手填 api_model 即可。\n"
                       "    Could not fetch the model list; check api_base and the key." %
                       "\n".join(errs[-3:]))


def _api_setup(api_base, api_model, api_key):
    """外部 API 通道：拼出请求端点 + 取 Key（Key 不进工作流才算安全）。

    端点决定线格式：`/anthropic` 收尾走 `/v1/messages`，其余走 `/chat/completions`。
    留空 Key 是合法状态（Ollama / LM Studio 一类本地端点不要 Key）。
    """
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise ValueError("通道=外部 API 必须填「api_base」。"
                         "例：`https://api.openai.com/v1`、`https://ark.cn-beijing.volces.com/api/v3`、"
                         "`http://127.0.0.1:11434/v1`（Ollama）\n"
                         "    channel=External API requires api_base.")
    if not (api_model or "").strip():
        raise ValueError("通道=外部 API 必须填「api_model」（服务商给的模型 id）。\n"
                         "    channel=External API requires api_model (the provider model id).")
    key, src = _api_creds(api_key)
    return _proto.endpoint(base, api_model), key, src


def _host_of(endpoint):
    m = re.match(r"https?://([^/]+)", endpoint or "")
    return m.group(1) if m else (endpoint or "?")


def _strip_fences(text):
    """剥 JSON 代码围栏与常见前缀，不静默改内容。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = re.sub(r"^\s*(?:english\s+)?caption\s*[:：]\s*", "", t, flags=re.I)
    t = re.sub(r"<point>.*?</point>", "", t, flags=re.S)
    return t.strip()


def _loads_lenient(text):
    """容错 JSON：整体失败时截取第一个 { 到最后一个 } 再试。"""
    try:
        return json.loads(text), None
    except Exception as e:
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1]), None
            except Exception as e2:
                return None, "%s / 截取重试:%s" % (e, e2)
        return None, str(e)


def _pick_prompt(obj):
    for key in ("rewritten_prompt", "caption", "instruction", "prompt"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # 取不到正文就返回空串，交给调用方报错。
    # 绝不把 JSON 对象本身当提示词——那会把「模型没给正文」静默变成一段垃圾 prompt
    # （实测：注入输入会返回 {"rewritten_prompt": ""} 空壳，旧逻辑原样当提示词返回）。
    return ""


def _norm_keep_text(s):
    """must_keep 对账用的归一化：NFKC（全角→半角）+ 空白折叠 + 大小写 + 去首尾引号。

    只放宽**书写形式**，不放宽内容：`Red scarf` 与 `red scarf` 是同一项，
    `"OPEN"` 与 `OPEN` 是同一项；但 `red` 仍不算命中 `red scarf`。
    """
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s.strip("\"'“”‘’「」『』")


def _missing_must_keep(prompt, must_keep):
    hay = _norm_keep_text(prompt)
    out, seen = [], set()
    for item in re.split(r"[;；\n]", must_keep or ""):
        raw = item.strip()
        if not raw:
            continue
        key = _norm_keep_text(raw)
        if not key or key in seen:      # 重复项只核一次，也不重复报
            continue
        seen.add(key)
        if key not in hay:
            out.append(raw)
    return out


def _truthy_list(v):
    """模型常把空槽写成 `[null]` 或 `["None"]` —— 报数前先滤成真有内容的字符串。"""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    return [s for s in (str(x).strip() for x in v)
            if s and s.lower() not in ("none", "null", "n/a", "na")]


#: 子包自己的注册表：父包 `__init__.py` 把它并进节点集，核心 7 节点的表不动。
NODE_CLASS_MAPPINGS = {"MossPromptSmith": MossPromptSmith}
NODE_DISPLAY_NAME_MAPPINGS = {"MossPromptSmith": "LLM Prompt"}

__all__ = ["MossPromptSmith", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "CATEGORY"]
