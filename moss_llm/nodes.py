# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""可选 LLM 子包：**一个节点吃下全部功能**。

2026-10-30 拍板的形态（对设计文档 §3.1「2+1 节点」的修订）：不拆 Config /
PromptSmith / PromptCheck，合成 `MossPromptSmith` 一个全功能节点；对外只出
**最终提示词**（+ report 供复盘）。理由：本包承诺「几何确定、提示词透明、返工便宜」，
多一个节点就多一层「配置在哪、提示词从哪来」的追踪成本。

通用性（开源前提）：不绑定任何具体模型/仓库——模型与 mmproj 从
`models/LLM` + `models/text_encoders` 扫描，任何 llama.cpp 支持的 GGUF 都能跑；
llama-server 运行时一键从 GitHub 官方 release 更新；提示词包按方言分发，
MOSS 两个方言只是其中两项。核心 7 节点不 import 本模块，本模块也不 import 核心包。
"""

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .pack import load_pack
from .server import GgufLocalServer, gguf_metadata, instance_alias

CATEGORY = "moss/llm"

#: 🔴 枚举的**存档值一律 ASCII**，与核心 7 节点同一口径（CONTRIBUTING 硬规矩 5）：
#: 中文只说在显示层 —— `web/moss_sb_i18n.js` 的 `COMBOS`（切中文时显示），
#: 切英文时回落成下面这些 id。把中文写进 `widgets_values` 会让英文用户的工作流里
#: 出现中文，也让值变成"已经翻译过一次"的一次性数据。
_TASKS = ["caption", "expand", "edit"]
_DIALECTS = ["moss_image", "moss_storyboard", "generic_natural", "generic_tags",
             "cinematic", "cn_natural"]
_LENGTHS = ["brief", "standard", "detailed"]
_LANGS = ["auto", "zh", "en"]
_RUNTIMES = ["current", "update"]
_ONOFF = ["on", "off"]
_MTP = ["auto", "on", "off"]
_KV = ["q8_0", "q4_0", "f16"]
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
#: mmproj 的两个哨兵值。其余取值是扫描到的文件名（原样显示，不翻译）。
_MMPROJ_AUTO = "auto"
_MMPROJ_NONE = "none"

#: 旧稿兼容：2026-10-30 的初版把中文写进了枚举值。载入旧工作流时在这里归一，
#: 归一过的字段写进 report（不静默改用户的图）。
_LEGACY_VALUES = {
    "反推": "caption", "扩写": "expand", "编辑指令": "edit",
    "本地 GGUF": "local", "外部 API": "api",
    "简短": "brief", "标准": "standard", "详细": "detailed",
    "自动": "auto", "中文": "zh", "英文": "en",
    "关": "off", "开": "on",
    "用现有": "current", "更新到最新版": "update",
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


def _text_width(model_path):
    """模型文本宽度 = `<arch>.embedding_length`；读不懂返回 None。"""
    try:
        md = gguf_metadata(model_path)
    except (OSError, EOFError):
        return None
    arch = md.get("general.architecture") or ""
    w = md.get("%s.embedding_length" % arch) if arch else None
    return w if isinstance(w, int) else None


def _proj_width(mmproj_path):
    """塔投影宽度 = `clip.vision.projection_dim`。

    不能拿 `clip.vision.embedding_length` 顶替：那是塔内部的视觉宽度（如 1152），
    决定「配不配得上」的只有 projection_dim。
    """
    try:
        w = gguf_metadata(mmproj_path).get("clip.vision.projection_dim")
    except (OSError, EOFError):
        return None
    return w if isinstance(w, int) else None


def _pair_key(name):
    """认「同一套权重的塔」用的名字键：去掉量化档与 mmproj 标记；认不出返回空串。"""
    stem = Path(name).stem.lower()
    stem = re.split(
        r"[-.](?=(?:ud-)?(?:q\d|iq\d|bf16|f16|f32|mxfp\d|nvfp\d|tq\d|pq\d|ptq\d))",
        stem)[0]
    return stem.replace(".mmproj", "").replace("-mmproj", "").strip("-.")


def _check_pair(model_path, mmproj_path):
    """校验塔宽 == 模型文本宽；不符 raise，读不懂返回一句「未验证」写进 report。"""
    want, got = _text_width(model_path), _proj_width(mmproj_path)
    if want is None or got is None:
        return ("⚠️ mmproj 配对未验证（模型宽度 %s，塔投影 %s）：头里缺字段。"
                % (want, got))
    if want != got:
        raise ValueError(
            "mmproj 与模型不配对：塔投影宽度 %d，模型文本宽度 %d。\n"
            "    换一个投影宽度相同的塔，或把「mmproj」留「自动匹配」让节点自己找。"
            % (got, want))
    return ""


def _resolve_mmproj(model_path, mmprojs, choice=_MMPROJ_AUTO):
    """选塔：按投影宽度硬校验，只在无歧义时自动选，否则报错让用户选。

    旧版只按文件名前缀猜（`qwen3.8-27b` 被削成 `qwen3`，两个塔时按字母序取首个），
    不同家族只要 proj_dim 恰好相等就能配上 —— 那种错配不报错，只会「看错图」。
    """
    if choice == _MMPROJ_NONE:
        return "", ""
    if choice and choice not in (_MMPROJ_AUTO, ""):
        path = mmprojs.get(choice) or (choice if Path(choice).is_file() else "")
        if not path:
            raise ValueError("mmproj 不存在：%r。请在「mmproj」下拉里重选。" % choice)
        return path, _check_pair(model_path, path)

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
    want = _text_width(model_path)
    if want is None:
        raise ValueError(
            "读不到模型头里的文本宽度（%s），没法验证 mmproj 配对。\n"
            "    在「mmproj」下拉里显式选一个塔；纯文本任务可选「(不使用/纯文本)」。"
            % Path(model_path).name)
    ok = [f for f in cands if _proj_width(str(f)) == want]
    if not ok:
        lines = "\n".join("    - %s（投影 %s）" % (f.name, _proj_width(str(f)))
                          for f in cands[:6])
        raise ValueError(
            "找到 %d 个 mmproj，但没有一个能配到 %s：模型文本宽度 %d。候选：\n%s\n"
            "    换一个投影宽度 %d 的塔。"
            % (len(cands), Path(model_path).name, want, lines, want))
    key = _pair_key(p.name)
    for pool in ([f for f in ok if key and _pair_key(f.name) == key],
                 [f for f in ok if f.parent == p.parent],
                 ok):
        if len(pool) == 1:
            return str(pool[0]), _check_pair(model_path, str(pool[0]))
        if pool:
            lines = "\n".join("    - %s" % f.name for f in pool[:8])
            raise ValueError(
                "有 %d 个 mmproj 都能配到 %s（投影宽度 %d）：\n%s\n"
                "    请在「mmproj」下拉里显式选一个。"
                % (len(pool), Path(model_path).name, want, lines))
    return "", ""


class MossPromptSmith:
    """提示词工匠：反推 / 扩写 / 编辑指令，一个 llama-server sidecar 跑完即还显存。"""

    @classmethod
    def INPUT_TYPES(cls):
        models, mmprojs = _scan_gguf()
        model_list = list(models.keys()) or ["(未找到 GGUF 模型)"]
        # 默认取第一个（按相对路径排序）。不硬编码任何模型——开源用户盘上有什么就跑什么。
        default_model = model_list[0]
        mmproj_list = [_MMPROJ_AUTO, _MMPROJ_NONE] + (list(mmprojs.keys()) or [])
        return {
            "required": {
                "task": (_TASKS, {"default": "caption",
                    "tooltip": "反推=看图写提示词（必须接图片）；扩写=把想法扩成完整画面描述（必须填文字）；"
                               "编辑指令=图生图的一条改法（接图片+填要改什么）"}),
                "channel": (_CHANNELS, {"default": "local",
                    "tooltip": "出词通道。本地 GGUF=起 llama-server 侧车（不联网、跑完还显存）；"
                               "外部 API=任何 OpenAI 兼容端点（OpenAI/火山方舟/智谱/Kimi/MiniMax/"
                               "DeepSeek/Ollama/LM Studio…），不起侧车、不占显存，但会联网外发图片与文字"}),
                "dialect": (_DIALECTS, {"default": "moss_image",
                    "tooltip": "输出方言。moss_image=MOSS 单图英文散文（官方默认）；"
                               "moss_storyboard=MOSS 宫格中文 [STYLE]+[PANEL n]（本仓已验证格式，"
                               "此时 panels 必须与生成段数一致）；generic_* 给 Flux/Krea/SDXL 等通用目标；"
                               "cn_natural 给 Seedream/即梦/可灵/万相等国内模型"}),
                "model": (model_list, {"default": default_model,
                    "tooltip": "递归扫描 ComfyUI/models/LLM 与 models/text_encoders 下全部 GGUF，"
                               "按相对路径显示；任何 llama.cpp 支持的架构都能跑。"
                               "反推任务需要视觉能力：主模型+mmproj，或直接用 VL 一体模型"}),
                "text": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "扩写=你的想法（固定项写进 must_keep，不要混在这里）；"
                               "编辑指令=要改什么。反推任务下留空"}),
                "length": (_LENGTHS, {"default": "standard",
                    "tooltip": "简短 80–120 词 / 标准 160–260 词 / 详细 280–360 词。"
                               "按目标方言有上限；反推要稳，扩写要活"}),
                "temperature": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 2.0, "step": 0.05,
                    "tooltip": "反推建议 0.2（要稳、要可复现）；扩写建议 0.7（要活）。"
                               "同 seed+同 prompt+同模型可复现"}),
                "max_tokens": ("INT", {"default": 1024, "min": 64, "max": 32768, "step": 64,
                    "tooltip": "输出上限。反推 512–1024；扩写 768–1024。"
                               "长 system+严格 JSON 可能偶发截断，留量即可"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                    "control_after_generate": True,
                    "tooltip": "同 seed + 同 prompt + 同模型可复现。改 seed 只换采样，不换提示词内容"}),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "反推必接；扩写可选作参考；编辑指令必接"}),
                "mmproj": (mmproj_list, {"default": _MMPROJ_AUTO,
                    "tooltip": "视觉投影文件，带图出词必需（没有它模型看不到图片）。"
                               "自动匹配=按宽度配对（模型 embedding_length == 塔 projection_dim），"
                               "在模型同目录与两个扫描目录里找；多个都能配上时直接报错让你选，不猜。"
                               "选「(不使用/纯文本)」即使本地有 mmproj 也不加载。"
                               "MOSS TE 配 Qwen3.5-9B.mmproj-Q8_0.gguf"}),
                "language": (_LANGS, {"default": "auto",
                    "tooltip": "说明文字的语言。图内文字永远保持原语言，不受此影响"}),
                "thinking": (_THINKING, {"default": "off",
                    "tooltip": "思考模式。默认关——实测开着时模型把内容全写进 reasoning_content、"
                               "content 为空且 finish=length，提示词直接空掉。"
                               "只给「想让模型先推理再作答」的模型开，且要同步加大最大 token 数"}),
                "panels": ("INT", {"default": 4, "min": 2, "max": 9, "step": 1,
                    "tooltip": "仅 dialect=moss_storyboard：全局段声明的面板数。"
                               "生成的 [PANEL] 段数必须与它相等，否则 report 报警（不静默改）"}),
                "style_lock": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "角色/服装/环境/媒介锁定。宫格稿里角色只在这里定义一次，"
                               "每格只写差异（重复全套会挤掉景别指令）"}),
                "must_keep": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "用户固定的字/数/色/位置，分号分隔。提示词里逐字出现才算数；"
                               "扩写不许改这些。report 会逐项核对"}),
                "wash": ("BOOLEAN", {"default": False,
                    "tooltip": "反推专用：忽略压缩噪点/水印/UI 杂项等非设计缺陷，"
                               "并把它列进 omitted_artifacts（不静默省略）"}),
                "two_pass": ("BOOLEAN", {"default": False,
                    "tooltip": "反推专用：先结构化盘点再写散文。洗图/高保真时开；"
                               "单遍快、双遍稳（两次调用同一 sidecar）"}),
                "runtime": (_RUNTIMES, {"default": "current",
                    "tooltip": "用现有=按「服务器程序/环境变量/本包 runtime/PATH」找 llama-server；"
                               "更新到最新版=先从 GitHub 官方 release 下载并安装最新 llama.cpp "
                               "（自动按本机后端选 CUDA/ROCm/Vulkan/SYCL/Metal/CPU 包；"
                               "国内慢可用 MOSS_LLM_MIRROR 指镜像，选错后端用 MOSS_LLM_BACKEND 覆盖），再用它"}),
                "api_base": ("STRING", {"default": "",
                    "tooltip": "外部 API 端点，OpenAI 兼容。填 `https://xxx/v1` 或直接填 "
                               "`.../v1/chat/completions` 都行。例：`https://api.openai.com/v1`、"
                               "`https://ark.cn-beijing.volces.com/api/v3`、"
                               "`http://127.0.0.1:11434/v1`（Ollama）、`http://127.0.0.1:1234/v1`"
                               "（LM Studio）。channel=外部 API 时必填", "advanced": True}),
                "api_model": ("STRING", {"default": "",
                    "tooltip": "外部 API 的模型名（服务商给的 id）。channel=外部 API 时必填；"
                               "本地 GGUF 通道不用填（填了也不看）", "advanced": True}),
                "api_key": ("STRING", {"default": "",
                    "tooltip": "API Key。**注意：写在这里会随工作流 JSON 一起保存/分享出去**，"
                               "要发给别人请清空改用下面的环境变量名", "advanced": True}),
                "api_key_env": ("STRING", {"default": "MOSS_LLM_API_KEY",
                    "tooltip": "从哪个环境变量读 Key（留空=不读）。"
                               "Key 不落进工作流才是安全做法；本地端点（Ollama/LM Studio）"
                               "通常不需要 Key，两处都留空即可", "advanced": True}),
                "ctx_size": ("INT", {"default": 8192, "min": 1024, "max": 262144, "step": 512,
                    "tooltip": "反推图片 token 多，建议 ≥8192。越大占显存越多（KV cache）",
                    "advanced": True}),
                "gpu_layers": ("STRING", {"default": "",
                    "tooltip": "留空=auto（llama.cpp 按显存自算层数，配合显存适配）；"
                               "填数字=手动指定放 GPU 的层数", "advanced": True}),
                "cache_type_k": (_KV, {"default": "q8_0",
                    "tooltip": "K cache 量化：q8_0 约省一半显存、q4_0 再省一半（质量略降）",
                    "advanced": True}),
                "cache_type_v": (_KV, {"default": "q8_0",
                    "tooltip": "V cache 量化，同 K。通常与 K 保持一致", "advanced": True}),
                "flash_attn": (_ONOFF, {"default": "on",
                    "tooltip": "显著降低 KV 显存占用并提速，默认开", "advanced": True}),
                "image_max_tokens": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 256,
                    "tooltip": "每张参考图最多消耗的视觉 token 数——多图出词时的最大显存变量",
                    "advanced": True}),
                "ubatch": ("INT", {"default": 512, "min": 64, "max": 4096, "step": 64,
                    "tooltip": "单次前向的 token 批大小，决定峰值显存与速度。爆显存降到 256",
                    "advanced": True}),
                "parallel": ("INT", {"default": 1, "min": 1, "max": 8, "step": 1,
                    "tooltip": "同时处理请求数，KV cache 会按倍数增长。单人出词保持 1",
                    "advanced": True}),
                "fit_on": (_ONOFF, {"default": "on",
                    "tooltip": "llama.cpp 原生显存适配：自动把显存占用压进「总显存−预留」，"
                               "放不下的层自动卸到 CPU。12GB 卡与 ComfyUI 共存时开", "advanced": True}),
                "fit_target_mib": ("INT", {"default": 1024, "min": 0, "max": 8192, "step": 128,
                    "tooltip": "显存适配的目标预留量（给 ComfyUI 采样留空间）。默认 1024",
                    "advanced": True}),
                "mtp": (_MTP, {"default": "auto",
                    "tooltip": "自动=读 GGUF 元数据判 nextn/mtp（不看文件名——dhq 版有 nextn "
                               "但名字没有 -mtp）；开=强制启用（不支持会自动回退并报警）；关=禁用。"
                               "实测同 target 1.48–1.64×；DFlash 对 MOSS target 接受率仅 10–31%，不用",
                    "advanced": True}),
                "mtp_draft_n": ("INT", {"default": 2, "min": 1, "max": 8, "step": 1,
                    "tooltip": "draft-mtp 的 --spec-draft-n-max。默认 2（d2）", "advanced": True}),
                "server_exe": ("STRING", {"default": "",
                    "tooltip": "llama-server 可执行文件完整路径。留空=按下面顺序找：环境变量 "
                               "MOSS_LLM_SERVER > 本包 moss_llm/runtime/ > 系统 PATH",
                    "advanced": True}),
                "port": ("INT", {"default": 8091, "min": 1024, "max": 65535, "step": 1,
                    "tooltip": "避开班长包的 8080。端口被外部占用时直接复用，不抢", "advanced": True}),
                "release_after_run": ("BOOLEAN", {"default": True,
                    "tooltip": "开启：跑完终止 llama-server 并释放显存（默认）；关闭：保留服务复用，"
                               "连续出词更快但常驻显存——采样前必须再跑一次本节点或重启来释放",
                    "advanced": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("提示词", "report")
    FUNCTION = "smith"
    CATEGORY = CATEGORY
    DISPLAY_NAME = "MOSS Prompt Smith"
    #: 该 runtime 拒绝 `response_format` 时置真，report 里出声（不静默降级）
    _json_unsupported = False
    #: 请求体里的 model 字段：本地=侧车的 --alias；外部 API=用户填的模型 id
    _model_id = "moss-llm"
    #: 被服务端 400 逼着摘掉的可选键（report 里逐项出声）
    _degraded = []
    _thinking_unsupported = False
    DESCRIPTION = ("本地 LLM 提示词工匠：反推（看图写提示词）/ 扩写（想法→完整画面描述）/ "
                   "编辑指令（图生图一条改法）。llama-server sidecar，跑完释放显存；"
                   "输出可直接接 CLIPTextEncode 或采样器 positive。")
    SEARCH_ALIASES = ["prompt smith", "reverse prompt", "caption", "expand", "prompt enhance",
                      "反推", "扩写", "caption", "expand", "洗图", "提示词工匠", "本地LLM", "gguf"]

    # ------------------------------------------------------------------ 主流程

    def smith(self, task, channel, dialect, model, text, length, temperature, max_tokens, seed,
              image=None, mmproj=_MMPROJ_AUTO, language="auto", thinking="off", panels=4,
              style_lock="", must_keep="", wash=False, two_pass=False, runtime="current",
              api_base="", api_model="", api_key="", api_key_env="MOSS_LLM_API_KEY",
              ctx_size=8192, gpu_layers="", cache_type_k="q8_0", cache_type_v="q8_0",
              flash_attn="on", image_max_tokens=1024, ubatch=512, parallel=1, fit_on="on",
              fit_target_mib=1024, mtp="auto", mtp_draft_n=2, server_exe="", port=8091,
              release_after_run=True):
        t0 = time.time()
        report = []
        self._degraded, self._json_unsupported, self._thinking_unsupported = [], False, False

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

        # 任务边界与通道无关，先判：反推/编辑必须接图；扩写必须有文字
        if task in ("caption", "edit") and image is None:
            raise ValueError("任务=%s 必须接「🖼️ 输入图片」。" % task)
        if task == "expand" and not (text or "").strip():
            raise ValueError("任务=expand 必须填「📝 文字输入」。")
        if dialect == "moss_storyboard" and not 2 <= int(panels) <= 9:
            raise ValueError("宫格数 panels=%s 超出 2–9。" % panels)

        pack = load_pack(_PROMPTS_DIR)
        report.append("提示词包 %s v%s（%s，%d 个文件）"
                      % (pack["pack"], pack["version"], pack["updated"], len(pack["files"])))
        system = self._build_system(pack, task, dialect, length, panels, style_lock,
                                    must_keep, wash, two_pass, language)
        user = (text or "").strip()

        if channel == "api":
            endpoint, key, key_src = _api_setup(api_base, api_model, api_key, api_key_env)
            self._model_id = api_model.strip()
            stop_server = lambda: None                                   # noqa: E731
            who = "外部 API %s｜model=%s｜Key：%s" % (_host_of(endpoint), api_model, key_src)
            report.append("通道：外部 API（联网外发图片与文字，不起侧车、不占显存）")
        else:
            if runtime == "update":
                from . import runtime_install      # 只在真要装时才 import（CLI 也用它）
                try:
                    exe, tag, src = runtime_install.install()
                except Exception as e:
                    raise RuntimeError("更新 llama.cpp 运行时失败：%r\n"
                                       "    国内网络可设 MOSS_LLM_MIRROR=<镜像前缀> 或 "
                                       "MOSS_LLM_RUNTIME_URL=<直链>后重试；后端认错可设 "
                                       "MOSS_LLM_BACKEND=cuda|vulkan|rocm|sycl|cpu；"
                                       "也可手动下载后填「服务器程序」。" % (e,)) from e
                report.append("llama.cpp 运行时：%s（%s；%s）"
                              % (Path(exe).name, tag or "未取到版本号", src))
                if not server_exe:
                    server_exe = exe

            models, mmprojs = _scan_gguf()
            model_path = models.get(model, "")
            if not model_path or not Path(model_path).is_file():
                raise ValueError("模型不存在或未选中：%r。请在下拉里选一个存在的 GGUF" % (model,))
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
                    "    ③ 把 mmproj 放到模型同目录。" % task)

            base_url, notes = GgufLocalServer.acquire(
                model_path, mmproj_path, ctx_size=int(ctx_size), gpu_layers=gpu_layers,
                cache_type_k=cache_type_k, cache_type_v=cache_type_v,
                flash_attn=(flash_attn == "on"), image_max_tokens=int(image_max_tokens),
                ubatch=int(ubatch), parallel=int(parallel), fit_on=(fit_on == "on"),
                fit_target_mib=int(fit_target_mib),
                mtp={"auto": "auto", "on": True, "off": False}.get(mtp, "auto"),
                port=int(port), server_exe=server_exe, draft_n_max=int(mtp_draft_n))
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
            usage = {}
            if task == "caption" and two_pass:
                raw1, u1 = self._chat(endpoint, system, user, image, temperature,
                                      int(max_tokens), int(seed), want_json=True,
                                      thinking=want_thinking, key=key)
                usage = u1 or {}
                inv, err = _loads_lenient(_strip_fences(raw1))
                if inv is None:
                    report.append("⚠️ 第一遍盘点不是合法 JSON（%s），已按单遍结果输出。" % err)
                    obj = None
                    prompt = _strip_fences(raw1)
                else:
                    report.append("双遍：第一遍盘点 %d 个字段" % len(inv))
                    sys2 = pack["texts"]["caption.pass2.en"]
                    raw2, u2 = self._chat(endpoint, sys2, json.dumps(inv, ensure_ascii=False),
                                          None, temperature, int(max_tokens), int(seed),
                                          want_json=True, thinking=want_thinking, key=key)
                    usage = u2 or usage
                    obj, err2 = _loads_lenient(_strip_fences(raw2))
                    if obj is None:
                        report.append("⚠️ 第二遍不是合法 JSON（%s），已按纯文本输出。" % err2)
                        prompt = _strip_fences(raw2)
                    else:
                        prompt = _pick_prompt(obj)
            else:
                raw, usage = self._chat(endpoint, system, user, image, temperature,
                                        int(max_tokens), int(seed), want_json=True,
                                        thinking=want_thinking, key=key)
                obj, err = _loads_lenient(_strip_fences(raw))
                if obj is None:
                    # 解析失败重试一次：把「只要 JSON」再说一遍，不静默改写内容
                    report.append("⚠️ 首次返回不是合法 JSON（%s），重试一次。" % err)
                    raw2, usage = self._chat(endpoint, system + _JSON_RETRY, user, image,
                                             max(0.0, float(temperature) - 0.1),
                                             int(max_tokens), int(seed), want_json=True,
                                             thinking=want_thinking, key=key)
                    obj, err2 = _loads_lenient(_strip_fences(raw2))
                    if obj is None:
                        report.append("⚠️ 重试后仍不是合法 JSON（%s），已按纯文本输出。" % err2)
                        prompt = _strip_fences(raw2)
                    else:
                        prompt = _pick_prompt(obj)
                else:
                    prompt = _pick_prompt(obj)
        finally:
            stop_server()

        checks = self._checks(prompt, obj if isinstance(obj, dict) else None,
                              task, dialect, panels, must_keep)
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
        if not prompt.strip():
            raise RuntimeError("模型返回空内容。")
        return (prompt, "\n".join(report))

    # ------------------------------------------------------------------ 提示词拼装

    def _build_system(self, pack, task, dialect, length, panels, style_lock,
                      must_keep, wash, two_pass, language):
        """四层拼装：任务主提示词 + 方言契约 + 篇幅/语言 + 用户约束。不写万能大 prompt。"""
        texts = pack["texts"]
        if task == "caption":
            if two_pass:
                sysmsg = texts["caption.pass1.en"]
                if wash:
                    sysmsg = texts["caption.clean.en"] + "\n\n" + sysmsg
            elif wash:
                sysmsg = texts["caption.clean.en"] + "\n\n" + texts["caption.faithful.en"]
            else:
                sysmsg = texts["caption.faithful.en"]
        elif task == "expand":
            sysmsg = texts["expand.method.en"]
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
              want_json=True, thinking=False, key=None):
        content = [{"type": "text", "text": system + "\n\n" + (user or "")}]
        if image is not None:
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + _b64_png(image)}})
        payload = {
            "model": self._model_id,
            "messages": [{"role": "user", "content": content}],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "seed": int(seed),
            "stream": False,
        }
        # 两个可选键都发：`chat_template_kwargs.enable_thinking` 是唯一实测能关掉思考的写法
        # （见 _THINKING 注释），vLLM / 火山方舟 / llama-server 都认它；严格 OpenAI 可能拒。
        # 拒了就**逐级摘掉再试**，并在 report 里说清摘了什么 —— 不静默降级，也不假装成功。
        payload["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        if want_json:
            payload["response_format"] = {"type": "json_object"}
        obj = None
        for drop in (None, "response_format", "chat_template_kwargs"):
            if drop:
                payload.pop(drop, None)
                self._degraded.append(drop)
            try:
                obj = _post(endpoint, payload, key)
                break
            except _Http400 as e:
                last = e
        if obj is None:
            raise RuntimeError("HTTP 400（去掉 response_format 与 chat_template_kwargs 后仍被拒）：%s"
                               % last.body) from last
        for dropped in self._degraded:
            if dropped == "response_format":
                self._json_unsupported = True
            elif dropped == "chat_template_kwargs":
                self._thinking_unsupported = True
        try:
            msg = obj["choices"][0]["message"]
            raw = msg["content"]
            finish = obj["choices"][0].get("finish_reason")
        except Exception as e:
            raise RuntimeError("响应缺 choices/message/content：%s"
                               % json.dumps(obj, ensure_ascii=False)[:400]) from e
        if not (raw or "").strip():
            # 思考模型最常见的失败：全写进 reasoning_content，正文空 + finish=length。
            # 不把思考内容当答案偷偷端上桌——那是另一种"静默兜底"。
            raise RuntimeError(
                "模型正文为空（finish=%s，reasoning %s 字）。"
                "把「思考模式」设为「关」，或加大「最大token数」后重试。"
                % (finish, len(str(msg.get("reasoning_content") or ""))))
        return raw, obj.get("usage") or {}

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


#: 解析失败重试时附加的一句话（不重写原提示词，只把输出契约再说一遍）
_JSON_RETRY = ("\n\n# REMINDER\nReturn exactly one JSON object and nothing else. "
               "No markdown, no code fences, no prose outside the JSON.")


# ---------------------------------------------------------------------- 纯函数


class _Http400(Exception):
    """HTTP 400 单独一类：只有它能触发「去掉 response_format 再试」。"""

    def __init__(self, body):
        super().__init__(body)
        self.body = body


def _post(endpoint, payload, key=None):
    """POST 一个 OpenAI 兼容的 /chat/completions。失败一律 raise 带 body 的中文错误。"""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            body = ""
        if e.code == 400:
            raise _Http400(body or "(无 body)") from e
        raise RuntimeError("服务端返回 HTTP %s：%s" % (e.code, body or "(无 body)")) from e
    except Exception as e:
        raise RuntimeError("请求失败：%r" % (e,)) from e


def _api_setup(api_base, api_model, api_key, api_key_env):
    """外部 API 通道：拼出 chat/completions 端点 + 取 Key（Key 不进工作流才算安全）。

    留空 Key 是合法状态（Ollama / LM Studio 一类本地端点不要 Key）。
    """
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise ValueError("通道=外部 API 必须填「api_base」。"
                         "例：`https://api.openai.com/v1`、`https://ark.cn-beijing.volces.com/api/v3`、"
                         "`http://127.0.0.1:11434/v1`（Ollama）")
    if not (api_model or "").strip():
        raise ValueError("通道=外部 API 必须填「api_model」（服务商给的模型 id）。")
    if not base.endswith("/chat/completions"):
        base += "/chat/completions"
    key = (api_key or "").strip()
    src = "写在工作流里（会随 JSON 分享出去）" if key else None
    env_name = (api_key_env or "").strip()
    if not key and env_name:
        key = os.environ.get(env_name, "").strip()
        if key:
            src = "环境变量 %s" % env_name
    if not key:
        src = "未提供（本地端点可留空）"
    return base, key, src


def _host_of(endpoint):
    m = re.match(r"https?://([^/]+)", endpoint or "")
    return m.group(1) if m else (endpoint or "?")


def _b64_png(image):
    """ComfyUI IMAGE tensor (B,H,W,C) float 0..1 → PNG base64。"""
    from PIL import Image
    try:
        import torch
        arr = (image[0].clamp(0.0, 1.0) * 255).round().to(torch.uint8).cpu().numpy()
    except Exception:
        arr = (image[0] * 255).round().astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


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
    return json.dumps(obj, ensure_ascii=False)


def _missing_must_keep(prompt, must_keep):
    return [s.strip() for s in re.split(r"[;；\n]", must_keep or "")
            if s.strip() and s.strip() not in prompt]


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
NODE_DISPLAY_NAME_MAPPINGS = {"MossPromptSmith": "MOSS Prompt Smith"}

__all__ = ["MossPromptSmith", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "CATEGORY"]
