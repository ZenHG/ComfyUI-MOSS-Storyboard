# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""T6：把 `registry.pitfalls` 里 3 条断言做成**可证伪 A/B**。

做法（先注册判据，再看图——顺序不能反）：

1. 每条 pitfall 写清 H0/H1、操作化定义、逐对判定规则、三档汇总规则；
2. 同 seed / 同分辨率 / 同采样参数成对出图（A=被测现象，B=对照），每条 ≥3 对；
3. 逐对目检（`read_image`），**先看 A/B 拼图再填 observation**；
4. `judge()` 按预注册规则机械求「支持 / 推翻 / 不显著」——判定不靠临场发挥；
5. 结论与对 registry 的改动建议落在 `docs/30-pitfalls-AB-第一轮.md`。

为什么同一对里 seed/尺寸/采样必须完全一致：A/B 的唯一变量是 prompt 里被测的那一小段，
其余任何差异都会变成混淆项。`check_design()` 与 `tests/test_pitfalls_ab.py` 把这条钉死。

为什么判定要机械：`docs/22` §5 与判据坑第 7 条——「模型说得对」不等于「判据对」。
先写死规则，图出来之后只允许填 observation（我看到了什么），不允许改规则。

三条 pitfall 的取证：

| id | backend | registry 原文 | 为什么选它 |
|---|---|---|---|
| P1 | moss-image | 相机型号/渲染引擎/画师名单写无效（须整句词组化） | 纯「写了等于没写」的物理断言，可 A/B 直接删不掉 |
| P2 | moss-image | 正文残留写实词会压制开头画风词 | 「压制」有方向性：A 应更写实、B 应更动漫 |
| P3 | krea2 | >512 token 静默截断，尾部（光照/媒介/文字）直接丢失 | 按位置砍 ⇒ 同内容换顺序即可对照，不需要人判风格 |

未选「否定式封锁无效」：它挂在 minimax-h3（视频+音轨）上，本机跑一条 8 秒 H3 视频
的成本与 T1/T6 的 GPU 串行纪律冲突，且视频目检（逐帧）不是本工具链现有的能力。
这条留在 registry 里，标「未做 A/B」，不假装推翻或支持。

用法：
    python tools\\pitfalls_ab.py --check            # 离线验设计
    python tools\\pitfalls_ab.py --check-workflows  # 只读 /object_info 验图
    python tools\\pitfalls_ab.py --plan             # 落实验计划
    python tools\\pitfalls_ab.py --run --dry        # 落要提交的 workflow，不排队
    python tools\\pitfalls_ab.py --run              # 真出图（占 GPU）
    python tools\\pitfalls_ab.py --observations-template
    python tools\\pitfalls_ab.py --judge out\\pitfalls_ab\\observations.json
    python tools\\pitfalls_ab.py --report           # 汇总 markdown 片段
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from moss_llm.smith.registry import Registry               # noqa: E402
from moss_llm.smith.tokens import HeuristicCounter        # noqa: E402

INDEX = ROOT / "tools" / "workflows" / "_index.json"
OUT = ROOT / "out" / "pitfalls_ab"
PLAN = OUT / "plan.json"
MANIFEST = OUT / "manifest.json"
VERDICTS = OUT / "verdicts.json"
DEFAULT_API = "http://127.0.0.1:8188"

#: 真实 token 计数用的侧车模板（Qwen3.5-4B 与 Krea2 的 Qwen3-VL-4B 同族 tokenizer，
#: 只能算**近似真实值**；用途是判「B 的 900 token 是否真的越过 512 截断点」）。
#:
#: ⚠ 这里**不写死任何本机路径**（公开仓要求，也会在别人机器上失效）。运行 `--tokenize`
#: 时必须给模型权重：优先 CLI `--model/--mmproj`，其次环境变量 `MOSS_LLM_MODEL`
#: / `G7_MODEL`（mmproj 同理 MOSS_LLM_MMPROJ/G7_MMPROJ）；找不到就明确报错不猜。
#: llama-server 路径：CLI `--server` → `MOSS_LLM_SERVER` → `G7_SERVER` → 包内
#: `find_server_exe()` 的常规查找（与 tools/g7_caption.py 同口径）。
TOKENIZER = {"model": "", "mmproj": "", "exe": "", "port": 8194, "ctx": 4096}

SERVER_ENVS = ("MOSS_LLM_SERVER", "G7_SERVER")
MODEL_ENVS = ("MOSS_LLM_MODEL", "G7_MODEL")
MMPROJ_ENVS = ("MOSS_LLM_MMPROJ", "G7_MMPROJ")

REGISTRY_PATH = ROOT / "moss_llm" / "smith" / "data" / "model-registry.yaml"

#: 预注册的断言原文。2026-11-01 实验后 Lead 已按 docs/30 §5 把被推翻的条目改写、
#: 原说法保留在 YAML 注释里；所以「防自己编断言」的校验改成：原文或其关键词必须
#: 仍能在 registry 文本里检索到。**不要求跑实验后文本还逐字不变**（那会把
#: 「改了 registry」误判成「断言是编的」）。
PITFALL_KEYWORD = {
    "P1": "相机型号/渲染引擎/画师名单",
    "P2": "正文残留写实词会压制开头画风词",
    "P3": ">512 token 静默截断",
}


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def resolve_tokenizer_conf(server: str = "", model: str = "", mmproj: str = "",
                           port: int | None = None, ctx: int | None = None) -> dict:
    """token 计数侧车的配置：CLI > 环境变量 > 明确报错。

    **不猜本机路径**（公开仓硬要求：别人机器上没有这台机的盘符）。
    llama-server 可执行文件允许留空，交给 `moss_llm.server.find_server_exe()` 按
    环境变量 / PATH 常规查找；找不到时它会给出可操作的报错。
    """
    conf = dict(TOKENIZER)
    conf["exe"] = (server or "").strip() or _first_env(SERVER_ENVS)
    model_p = (model or "").strip() or _first_env(MODEL_ENVS)
    if not model_p:
        raise RuntimeError(
            "不知道用哪个 GGUF 模型数 token。两种给法：\n"
            "  ① --model <Qwen 系 GGUF>（可再加 --mmproj）\n"
            "  ② 环境变量 MOSS_LLM_MODEL=<模型路径> / MOSS_LLM_MMPROJ=<视觉塔路径>\n"
            "本工具不写死本机路径（公开仓要求）。")
    conf["model"] = model_p
    conf["mmproj"] = (mmproj or "").strip() or _first_env(MMPROJ_ENVS)
    if port is not None:
        conf["port"] = int(port)
    if ctx is not None:
        conf["ctx"] = int(ctx)
    return conf

#: registry 的 pitfalls 原文。**逐字**引用，测试会拿它跟 YAML 对账——
#: 防止「实验测了一句自己编的话」。
PITFALL_TEXT = {
    "P1": "相机型号/渲染引擎/画师名单写无效（须整句词组化）",
    "P2": "正文残留写实词会压制开头画风词",
    "P3": ">512 token 静默截断，尾部（光照/媒介/文字）直接丢失",
}

#: 判定用的三档。
SUPPORT, REFUTE, INCONCLUSIVE = "support", "refute", "inconclusive"
VERDICT_ZH = {SUPPORT: "支持", REFUTE: "推翻", INCONCLUSIVE: "不显著"}

#: 目检可接受的风格档（P2 用）。数字越大越写实。
STYLE_RANK = {"anime": 0, "cel": 0, "illustration": 0, "manga": 0,
              "hybrid": 1, "mixed": 1,
              "photoreal": 2, "photo": 2, "realistic": 2}


# ─────────────────────────────────────────────────────────────
# 实验设计（先注册，后看图）
# ─────────────────────────────────────────────────────────────

def _pair(pid, seed, base, treatment, control, effect, variable):
    return {"id": pid, "seed": seed, "width": 1024, "height": 1024,
            "variable": variable, "base": base,
            "A": treatment, "B": control,
            "expected_effect": effect}


EXPERIMENTS = [
    {
        "id": "P1-token-names",
        "pitfall_key": "P1",
        "backend": "moss-image",
        "registry_target": "moss-image",
        "claim": PITFALL_TEXT["P1"],
        "H0": "直接写相机型号/渲染引擎名/画师名**不会**改变画面在对应属性上的表现（写了等于没写）。",
        "H1": "会改变：A 图出现该 token 指向的可辨识属性，B 图没有。",
        "operationalization": (
            "每个子断言一对图，A=基础散文后追加该 token，B=同一基础散文不加。"
            "目检只回答两件事：A 是否出现该 token 的**预期视觉属性**、B 是否也出现。"
            "预期属性预先写死在 expected_effect 里，不许看图后补。"),
        "pair_rule": "tokens_effective",
        "judge_note": (
            "A 有、B 无 ⇒ 该 token 有效（推翻「写无效」）；"
            "A 无、B 无 ⇒ 该 token 无效（支持）；其余（含 A 无 B 有 / 两边都有）⇒ 本对无法归因。"),
        "pairs": [
            _pair("P1a-camera", 11011,
                  "A woman sitting at a wooden table beside a cafe window, soft daylight, medium shot.",
                  "A woman sitting at a wooden table beside a cafe window, soft daylight, medium shot, "
                  "shot on a Canon EOS R5 with an RF 85mm f/1.2 lens, shallow depth of field.",
                  "A woman sitting at a wooden table beside a cafe window, soft daylight, medium shot.",
                  "A 的景深明显更浅（背景/桌上物件虚化）、出现 85mm f/1.2 的光学散景；B 相反或无法区分。",
                  "camera model / lens"),
            _pair("P1b-engine", 11012,
                  "A woman sitting on a wooden bench in a park, golden afternoon light, medium full shot.",
                  "A woman sitting on a wooden bench in a park, golden afternoon light, medium full shot, "
                  "rendered in Unreal Engine 5 with ray-traced lighting.",
                  "A woman sitting on a wooden bench in a park, golden afternoon light, medium full shot.",
                  "A 呈现 3D 游戏引擎渲染质感（CG 皮肤、PBR 材质、边缘过于干净）；B 无此质感。",
                  "render engine name"),
            _pair("P1c-artist", 11013,
                  "Portrait of a woman standing in a wheat field at sunset, medium close-up.",
                  "Portrait of a woman standing in a wheat field at sunset, medium close-up, "
                  "in the style of Hayao Miyazaki.",
                  "Portrait of a woman standing in a wheat field at sunset, medium close-up.",
                  "A 呈现宫崎骏式动画/绘本风格（赛璐璐、平涂、手绘云）；B 为写实照片或未出现该画风。",
                  "artist name"),
        ],
        "verdict_rule": "same 2-of-3：support≥2 且 >refute ⇒ 支持；refute≥2 且 >support ⇒ 推翻；否则不显著。",
    },
    {
        "id": "P2-realword-suppression",
        "pitfall_key": "P2",
        "backend": "moss-image",
        "registry_target": "moss-image",
        "claim": PITFALL_TEXT["P2"],
        "H0": "开头已写动画/赛璐璐画风词时，正文后段的写实词**会压制**它：A 比 B 更写实。",
        "H1": "不压制：A 与 B 的画风档相同或 A 更动画。",
        "operationalization": (
            "每对只差**尾段**：A 尾段是写实词（photorealistic skin / DSLR / film grain），"
            "B 尾段是动画一致的词（matte shading / flat colors / ink contours）。"
            "目检给整图判一个画风档：anime / hybrid / photoreal。"
            "判定用序关系（rank_a > rank_b 才算支持），不用「像不像」这种不可复现的说法。"),
        "pair_rule": "style_suppression",
        "judge_note": (
            "A 更写实（rank A > rank B）⇒ 支持；A 不更写实（rank A ≤ rank B）⇒ 推翻；"
            "任一张判不出画风档 ⇒ 本对无法归因。"),
        "pairs": [
            _pair("P2a-cafe", 12021,
                  "Anime style, cel-shaded flat colors, clean line art.",
                  "Anime style, cel-shaded flat colors, clean line art. A young woman sits by a cafe "
                  "window holding a ceramic cup, soft daylight. Photorealistic skin texture, visible "
                  "pores, 85mm DSLR photograph, natural film grain.",
                  "Anime style, cel-shaded flat colors, clean line art. A young woman sits by a cafe "
                  "window holding a ceramic cup, soft daylight. Matte shading, crisp outlines, flat "
                  "color blocks, two-tone shadows.",
                  "A 比 B 更写实（hybrid/photoreal）；B 保持 anime。",
                  "尾段写实词 vs 尾段动画词"),
            _pair("P2b-rain", 12022,
                  "Anime style, manga line art.",
                  "Anime style, manga line art. A boy rides a bicycle down a rainy street, holding an "
                  "umbrella. Ultra-realistic photograph, real human skin details, DSLR bokeh.",
                  "Anime style, manga line art. A boy rides a bicycle down a rainy street, holding an "
                  "umbrella. Screen-tone shading, bold ink contours, limited palette.",
                  "A 比 B 更写实；B 保持 manga。",
                  "尾段写实词 vs 尾段动画词"),
            _pair("P2c-hill", 12023,
                  "Anime style, painterly watercolor background.",
                  "Anime style, painterly watercolor background. Two children stand on a hilltop under "
                  "a huge cloudy sky. Photorealistic clouds, real film grain, 35mm camera.",
                  "Anime style, painterly watercolor background. Two children stand on a hilltop under "
                  "a huge cloudy sky. Soft watercolor washes, flat pastel palette, clean contours.",
                  "A 比 B 更写实；B 保持动画/水彩。",
                  "尾段写实词 vs 尾段动画词"),
        ],
        "verdict_rule": "same 2-of-3：support≥2 且 >refute ⇒ 支持；refute≥2 且 >support ⇒ 推翻；否则不显著。",
    },
    {
        "id": "P3-krea2-truncation",
        "pitfall_key": "P3",
        "backend": "krea2",
        "registry_target": "krea2",
        "claim": PITFALL_TEXT["P3"],
        "H0": "encoder 在 512 token 处按位置静默截断：位于 >512 token 处的尾部内容丢失。",
        "H1": "不按位置丢：尾部内容同样出现。",
        "operationalization": (
            "同一对里 A/B 的**词与总长完全相同**，只有顺序不同："
            "A = 基础句 + 关键内容 + 长填充；B = 基础句 + 长填充 + 关键内容。"
            "关键内容是两个可目检的实体（物体 + 图内文字），放在 A 的前 ~60 token、"
            "B 的 ~900 token 之后。A 出现、B 丢失 ⇒ 位置性丢弃。"),
        "pair_rule": "tail_lost",
        "judge_note": (
            "A 两个 cue 都在、B 至少丢一个 ⇒ 支持；B 两个 cue 都在 ⇒ 推翻；"
            "A 也丢（生成失败/被别的原因吃掉）⇒ 本对无法归因。"),
        "pairs": [
            _pair("P3a-cafe", 13031,
                  "A cafe interior with a wooden table beside a window.",
                  "A cafe interior with a wooden table beside a window. A red banner hangs in the "
                  "top-right corner with the white word OPEN printed on it. A small brass key lies on "
                  "the table.",
                  "A cafe interior with a wooden table beside a window.",
                  "A：右上红横幅 + OPEN 字样、桌上黄铜钥匙都出现；B：至少丢一个。",
                  "关键内容前置 vs 后置"),
            _pair("P3b-street", 13032,
                  "A woman standing in a narrow city street at dusk.",
                  "A woman standing in a narrow city street at dusk. A green paper lantern hangs in "
                  "the upper-left corner. She holds a white paper bag in her right hand.",
                  "A woman standing in a narrow city street at dusk.",
                  "A：左上有绿灯笼、她右手拿白色纸袋；B：至少丢一个。",
                  "关键内容前置 vs 后置"),
            _pair("P3c-market", 13033,
                  "A wooden market stall in the evening.",
                  "A wooden market stall in the evening. A yellow umbrella stands open in the "
                  "bottom-right corner. A sign above the stall reads SALE.",
                  "A wooden market stall in the evening.",
                  "A：右下有撑开的黄伞、摊上招牌写 SALE；B：至少丢一个。",
                  "关键内容前置 vs 后置"),
        ],
        "verdict_rule": "same 2-of-3：support≥2 且 >refute ⇒ 支持；refute≥2 且 >support ⇒ 推翻；否则不显著。",
    },
]

#: P3 的长填充。用固定句子池确定性拼装——同一次实验里 A/B 用的是**同一串词**，
#: 只有顺序不同，所以任何差异只能来自位置。句子只描述场景氛围，不含 cue 词
#: （banner/OPEN/key/lantern/bag/umbrella/SALE），否则 B 的填充自己就会触发 cue。
FILLER_SENTENCES = [
    "The room is quiet and the air is still.",
    "Warm light falls across the wooden floor boards.",
    "A soft haze hangs in the middle distance.",
    "Small dust motes drift slowly through the air.",
    "The walls are painted in a muted warm grey.",
    "A few chairs are pushed back from the tables.",
    "The window glass carries a faint reflection of the street.",
    "Shadows gather under the furniture and along the skirting board.",
    "The floor has a slight sheen from years of use.",
    "Outside, the city goes on with its ordinary noise.",
    "The colour palette stays low and earthy overall.",
    "A thin band of brighter light picks out the far wall.",
    "The wooden surface shows fine grain and a few scratches.",
    "The atmosphere is calm, everyday and unremarkable.",
    "The framing keeps the background soft and unobtrusive.",
    "Everything is placed with the casual order of daily life.",
    "The light is diffuse rather than direct.",
    "The composition stays simple and readable.",
    "There is no strong colour accent anywhere in the frame.",
    "The texture of the materials is matte rather than glossy.",
    "The scene suggests late afternoon rather than morning.",
    "The perspective is natural and slightly wide.",
    "A narrow strip of ceiling is visible at the top of the frame.",
    "The furniture edges are rounded from use.",
    "The overall contrast is gentle and even.",
    "Nothing in the frame draws attention to itself.",
    "The space feels lived in and lightly worn.",
    "The air feels cool but not cold.",
    "The background details stay out of focus.",
    "The image has the calm of a place between busy hours.",
]


def build_filler(min_tokens: int = 900) -> str:
    """确定性拼装填充文本，直到启发式 token 数 ≥ min_tokens。不依赖随机。"""
    counter = HeuristicCounter()
    text = ""
    for sentence in FILLER_SENTENCES:
        text = (text + " " + sentence).strip()
        if counter.count(text) >= min_tokens:
            break
    else:                                   # 句子池不够长就往回补（可复现）
        while counter.count(text) < min_tokens:
            text = text + " " + FILLER_SENTENCES[len(text) % len(FILLER_SENTENCES)]
    return text


def resolved_prompts(exp: dict, pair: dict) -> tuple[str, str]:
    """返回 (A, B) 的**最终 prompt**。设计里只写关键内容，填充在这里生成。

    P3 的 A/B 是同一串词换顺序；其余实验 A/B 就是设计里的字面值。
    """
    if exp["pair_rule"] != "tail_lost":
        return pair["A"], pair["B"]
    filler = build_filler()
    cues = pair["A"][len(pair["base"]):].strip()      # 设计里 A = base + cues
    control = pair["B"]
    if control != pair["base"]:
        raise ValueError("P3 的 B 必须是 base 本身（顺序对照由本函数拼）")
    a = pair["base"] + " " + cues + " " + filler
    b = pair["base"] + " " + filler + " " + cues
    return a, b


# ─────────────────────────────────────────────────────────────
# 判定（纯函数，可离线单测）
# ─────────────────────────────────────────────────────────────

def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("yes", "true", "y", "1", "有", "出现"):
            return True
        if s in ("no", "false", "n", "0", "无", "没有", "未出现"):
            return False
    return None


def classify_pair(experiment: dict, obs: dict) -> tuple[str, str]:
    """逐对判定。返回 (SUPPORT|REFUTE|INCONCLUSIVE, 一句理由)。

    `obs` 是**目检填的 observation**，字段由 `observation_template()` 给出。
    本函数只做机械规则，不看图、不调模型。
    """
    rule = experiment["pair_rule"]
    if rule == "tokens_effective":
        a = _as_bool(obs.get("a_shows_effect"))
        b = _as_bool(obs.get("b_shows_effect"))
        if a is None or b is None:
            return INCONCLUSIVE, "observation 缺 a_shows_effect / b_shows_effect"
        if a and not b:
            return REFUTE, "A 出现预期属性、B 没有 ⇒ 该 token 有效"
        if not a and not b:
            return SUPPORT, "A/B 都没有预期属性 ⇒ 该 token 无效"
        return INCONCLUSIVE, "两臂属性出现方式相同/相反，无法归因到该 token"

    if rule == "style_suppression":
        sa = STYLE_RANK.get(str(obs.get("style_a") or "").strip().lower())
        sb = STYLE_RANK.get(str(obs.get("style_b") or "").strip().lower())
        if sa is None or sb is None:
            return INCONCLUSIVE, "style_a/style_b 不是可识别的画风档"
        if sa > sb:
            return SUPPORT, "A(%s) 比 B(%s) 更写实 ⇒ 写实词压制了开头画风" % (
                obs.get("style_a"), obs.get("style_b"))
        return REFUTE, "A(%s) 不比 B(%s) 更写实 ⇒ 未见压制" % (
            obs.get("style_a"), obs.get("style_b"))

    if rule == "tail_lost":
        bt = obs.get("b_tokens")
        if isinstance(bt, int) and bt <= 512:
            return INCONCLUSIVE, ("B 的真实 token 数 %d ≤ 512，本对不构成截断测试"
                                  "（先修 prompt 长度再谈支持/推翻）" % bt)
        a_cues = [_as_bool(x) for x in (obs.get("a_cues") or [])]
        b_cues = [_as_bool(x) for x in (obs.get("b_cues") or [])]
        if not a_cues or len(a_cues) != len(b_cues) or any(x is None for x in a_cues + b_cues):
            return INCONCLUSIVE, "cue 观察不完整（需要等长、可判的 a_cues/b_cues）"
        if all(a_cues) and not all(b_cues):
            return SUPPORT, "A 的 cue 全在、B 至少丢一个 ⇒ 尾部按位置丢失"
        if all(b_cues):
            return REFUTE, "B 的 cue 也全在 ⇒ 未按位置丢失"
        return INCONCLUSIVE, "A 自己也没保住 cue，无法归因到截断"

    raise ValueError("未知 pair_rule：%r" % rule)


def aggregate(pair_results: list[str]) -> tuple[str, dict]:
    """三档汇总：任一方 ≥2 且严格多于另一方 ⇒ 该结论；否则不显著。"""
    counts = {SUPPORT: 0, REFUTE: 0, INCONCLUSIVE: 0}
    for r in pair_results:
        counts[r] = counts.get(r, 0) + 1
    s, r = counts[SUPPORT], counts[REFUTE]
    if s >= 2 and s > r:
        verdict = SUPPORT
    elif r >= 2 and r > s:
        verdict = REFUTE
    else:
        verdict = INCONCLUSIVE
    return verdict, counts


def judge(observations: dict, experiments: list[dict] | None = None) -> dict:
    """对全部实验求结论。`observations` 形状见 `observation_template()`。"""
    experiments = experiments or EXPERIMENTS
    out = {"experiments": [], "verdict": {}, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for exp in experiments:
        eid = exp["id"]
        per = []
        obs_all = observations.get(eid) or {}
        for pair in exp["pairs"]:
            obs = obs_all.get(pair["id"]) or {}
            cls, reason = classify_pair(exp, obs)
            per.append({"pair": pair["id"], "class": cls, "reason": reason,
                        "observation": obs})
        verdict, counts = aggregate([p["class"] for p in per])
        out["experiments"].append({
            "id": eid, "pitfall_key": exp["pitfall_key"], "backend": exp["backend"],
            "claim": exp["claim"], "H0": exp["H0"], "H1": exp["H1"],
            "pair_rule": exp["pair_rule"],
            "pairs": per, "counts": counts,
            "verdict": verdict, "verdict_zh": VERDICT_ZH[verdict],
        })
        out["verdict"][eid] = verdict
    return out


def observation_template(experiments: list[dict] | None = None) -> dict:
    """目检要填的 JSON 骨架。字段不含结论，只含「我看到了什么」。"""
    experiments = experiments or EXPERIMENTS
    tpl = {}
    for exp in experiments:
        bucket = {}
        for pair in exp["pairs"]:
            if exp["pair_rule"] == "tokens_effective":
                bucket[pair["id"]] = {"a_shows_effect": None, "b_shows_effect": None,
                                      "note": ""}
            elif exp["pair_rule"] == "style_suppression":
                bucket[pair["id"]] = {"style_a": None, "style_b": None, "note": ""}
            elif exp["pair_rule"] == "tail_lost":
                bucket[pair["id"]] = {"a_cues": [None, None], "b_cues": [None, None],
                                      "note": ""}
            bucket[pair["id"]]["seed"] = pair["seed"]
            bucket[pair["id"]]["compare"] = ""
        tpl[exp["id"]] = bucket
    return tpl


def observation_draft(manifest: dict | None = None) -> dict:
    """骨架 + manifest 里的拼图路径。目检只需要在它上面填 observation 字段。"""
    tpl = observation_template()
    man = manifest
    if man is None:
        man = (json.loads(MANIFEST.read_text(encoding="utf-8"))
               if MANIFEST.is_file() else {})
    for exp in man.get("experiments") or []:
        bucket = tpl.get(exp.get("id"))
        if not bucket:
            continue
        for pr in exp.get("pairs") or []:
            slot = bucket.get(pr.get("id"))
            if slot is None:
                continue
            slot["compare"] = pr.get("compare") or _pair_compare_fallback(pr)
    return tpl


def _pair_compare_fallback(pair_manifest: dict) -> str:
    """dry/旧 manifest 没有拼图时，退回到 A/B 两张图路径（拼图是后处理产物）。"""
    arms = pair_manifest.get("arms") or {}
    return ";".join((arms[a].get("image") or "") for a in ("A", "B") if a in arms)


def validate_observations(observations: dict,
                          experiments: list[dict] | None = None) -> list[str]:
    """判前体检：哪些 pair 的 observation 还是空的/不可判的。不许静默算成「不显著」。"""
    experiments = experiments or EXPERIMENTS
    problems = []
    for exp in experiments:
        bucket = observations.get(exp["id"]) or {}
        for pair in exp["pairs"]:
            obs = bucket.get(pair["id"]) or {}
            rule = exp["pair_rule"]
            missing = []
            if rule == "tokens_effective":
                for key in ("a_shows_effect", "b_shows_effect"):
                    if _as_bool(obs.get(key)) is None:
                        missing.append(key)
            elif rule == "style_suppression":
                for key in ("style_a", "style_b"):
                    if str(obs.get(key) or "").strip().lower() not in STYLE_RANK:
                        missing.append(key)
            elif rule == "tail_lost":
                for key in ("a_cues", "b_cues"):
                    vals = obs.get(key)
                    if not isinstance(vals, list) or len(vals) != 2 \
                            or any(_as_bool(v) is None for v in vals):
                        missing.append(key)
            if missing:
                problems.append("%s/%s: 缺或不可判 %s"
                                % (exp["id"], pair["id"], "、".join(missing)))
    return problems



def token_counts(conf: dict | None = None, out: Path | None = None) -> dict:
    """给每条 A/B prompt 数**真实** token（llama-server /tokenize）。占 GPU 一小会儿。

    为什么必须做：P3 的「按位置截断」只有在 B 的关键内容**真的**落在 512 token 之后
    才构成测试。启发式 count 有 ±15% 误差，不能拿它当唯一依据。
    """
    from moss_llm.server import LlamaServer
    from moss_llm.smith.tokens import ServerCounter

    conf = dict(TOKENIZER if conf is None else conf)
    out = out or OUT
    rows = {}
    with LlamaServer(model=conf["model"], exe=conf.get("exe", ""),
                     port=conf.get("port", 8194), ctx=conf.get("ctx", 4096),
                     mmproj=conf.get("mmproj", "")) as llm:
        counter = ServerCounter(llm.base)
        for exp in EXPERIMENTS:
            for pair in exp["pairs"]:
                a, b = resolved_prompts(exp, pair)
                rows["%s/%s" % (exp["id"], pair["id"])] = {
                    "A": counter.count(a), "B": counter.count(b),
                    "heuristic_A": HeuristicCounter().count(a),
                    "heuristic_B": HeuristicCounter().count(b),
                }
    doc = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "tokenizer": {"model": Path(conf["model"]).name,
                         "note": "Qwen3.5-4B 同族 tokenizer，不是 Krea2 encoder 本身；"
                                 "只能算近似真实值，用于判断 B 是否越过 512"},
           "pairs": rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / "tokens.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return doc


def inject_tokens(observations: dict, tokens: dict) -> dict:
    """把真实 token 数塞进 observation（判据要用它挡掉「B 没超过 512」的假测试）。"""
    for key, row in (tokens.get("pairs") or {}).items():
        eid, _, pid = key.partition("/")
        slot = (observations.get(eid) or {}).get(pid)
        if isinstance(slot, dict):
            slot["a_tokens"] = row.get("A")
            slot["b_tokens"] = row.get("B")
    return observations


# ─────────────────────────────────────────────────────────────
# ComfyUI 客户端
# ─────────────────────────────────────────────────────────────

def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def load_template(backend: str) -> dict:
    idx = load_index()
    if backend not in idx["backends"]:
        raise KeyError("workflow 索引里没有后端 %r；有：%s"
                       % (backend, "、".join(idx["backends"])))
    path = INDEX.parent / idx["backends"][backend]["file"]
    return json.loads(path.read_text(encoding="utf-8"))


def build_workflow(backend: str, prompt: str, seed: int, width: int, height: int,
                   prefix: str) -> dict:
    """按 `_index.json` 的 node 映射把 prompt/seed/尺寸/前缀灌进模板。"""
    wf = load_template(backend)
    b = load_index()["backends"][backend]
    nodes = b["nodes"]

    def node(role):
        nid = nodes[role]
        if nid not in wf:
            raise KeyError("模板 %s 缺节点 id %s（role=%s）" % (backend, nid, role))
        return wf[nid]

    node("positive")["inputs"]["text"] = prompt
    node("sampler")["inputs"]["seed"] = int(seed)
    node("sampler")["inputs"]["steps"] = int(b["defaults"]["steps"])
    node("sampler")["inputs"]["cfg"] = float(b["defaults"]["cfg"])
    node("sampler")["inputs"]["sampler_name"] = b["defaults"]["sampler_name"]
    node("sampler")["inputs"]["scheduler"] = b["defaults"]["scheduler"]
    node("latent")["inputs"]["width"] = int(width)
    node("latent")["inputs"]["height"] = int(height)
    node("save")["inputs"]["filename_prefix"] = prefix
    if "shift" in nodes:
        node("shift")["inputs"]["shift"] = float(b["defaults"]["shift"])
    return wf


def validate_workflow(wf: dict, infos: dict, backend: str = "") -> list[str]:
    """静态 + /object_info 校验。返回问题列表（空 = 可提交）。"""
    problems = []
    for nid, node in wf.items():
        ct = node.get("class_type")
        info = infos.get(ct)
        if info is None:
            problems.append("%s: 服务端没有节点类型 %r" % (nid, ct))
            continue
        inputs = node.get("inputs") or {}
        spec_required = (info.get("input") or {}).get("required") or {}
        for name in spec_required:
            if name not in inputs:
                problems.append("%s(%s): 缺必填输入 %s" % (nid, ct, name))
        for name, val in inputs.items():
            if isinstance(val, list):
                if len(val) != 2:
                    problems.append("%s(%s): 连线输入 %s 形状不对" % (nid, ct, name))
                    continue
                src = str(val[0])
                if src not in wf:
                    problems.append("%s(%s): 连线 %s -> 不存在的节点 %s"
                                    % (nid, ct, name, src))
                    continue
                src_info = infos.get(wf[src].get("class_type")) or {}
                outs = src_info.get("output") or []
                slot = val[1]
                if not isinstance(slot, int) or slot < 0 or (outs and slot >= len(outs)):
                    problems.append("%s(%s): 连线 %s -> %s 槽位 %r 越界"
                                    % (nid, ct, name, src, slot))
            elif name in spec_required:
                spec = spec_required[name][0]
                if isinstance(spec, list) and val not in spec:
                    problems.append("%s(%s): 输入 %s 的值 %r 不在可选项里"
                                    % (nid, ct, name, val))
    return problems


def fetch_object_info(api: str) -> dict:
    return get_json(api.rstrip("/") + "/object_info", timeout=60)


def check_workflows(api: str = DEFAULT_API) -> list[str]:
    """只读 /object_info，校验三份模板 + 索引里登记的节点 id/类型。"""
    infos = fetch_object_info(api)
    idx = load_index()
    problems = []
    for backend, b in idx["backends"].items():
        try:
            wf = load_template(backend)
        except Exception as exc:                       # 文件缺失/坏 JSON
            problems.append("%s: 读模板失败 %s" % (backend, exc))
            continue
        for p in validate_workflow(wf, infos, backend):
            problems.append("%s: %s" % (backend, p))
        for role, nid in b["nodes"].items():
            if nid not in wf:
                problems.append("%s: 索引登记 %s=%s，模板里没有" % (backend, role, nid))
    return problems


def check_design(experiments: list[dict] | None = None,
                 registry: Registry | None = None) -> list[str]:
    """离线验实验设计。任何一条不满足，实验就不该开跑。"""
    experiments = experiments or EXPERIMENTS
    reg = registry or Registry.load()
    problems = []

    seen_pairs = set()
    raw_registry = (REGISTRY_PATH.read_text(encoding="utf-8")
                    if REGISTRY_PATH.is_file() else "")
    for exp in experiments:
        eid = exp["id"]
        key = exp["pitfall_key"]
        # ① 断言必须能在 registry 里找到（原文或关键词；实验后条目可能已被改写+加注）
        caps = reg.get(exp["registry_target"])
        pitfalls = caps.get("pitfalls") or []
        if not pitfalls:
            problems.append("%s: %s 的 registry.pitfalls 是空的"
                            % (eid, exp["registry_target"]))
        keyword = PITFALL_KEYWORD[key]
        if PITFALL_TEXT[key] not in raw_registry and keyword not in raw_registry:
            problems.append("%s: 选中的 pitfall 在 registry 文本里找不到了（原文/关键词都没有）"
                            % eid)
        # ② 每条 ≥3 对
        if len(exp["pairs"]) < 3:
            problems.append("%s: 只有 %d 对，要求 ≥3" % (eid, len(exp["pairs"])))
        for pair in exp["pairs"]:
            pid = pair["id"]
            if pid in seen_pairs:
                problems.append("重复 pair id：%s" % pid)
            seen_pairs.add(pid)
            # ③ 同 seed、同分辨率、同采样（sampling 由模板固定在 backend 上）
            if pair["width"] % 16 or pair["height"] % 16:
                problems.append("%s: 尺寸不是 16 的倍数" % pid)
            a, b = resolved_prompts(exp, pair)
            if not a.strip() or not b.strip():
                problems.append("%s: A/B prompt 有空串" % pid)
            if a == b:
                problems.append("%s: A 与 B 完全相同，测不出东西" % pid)
            # ④ P3 的顺序对照：同词同长，只有顺序不同
            if exp["pair_rule"] == "tail_lost":
                ca, cb = HeuristicCounter().count(a), HeuristicCounter().count(b)
                if ca != cb:
                    problems.append("%s: A/B token 数不等（%d vs %d），顺序对照不成立"
                                    % (pid, ca, cb))
                if ca < 900:
                    problems.append("%s: 总长 %d token 不够越过 512 截断点" % (pid, ca))
                cues = pair["A"][len(pair["base"]):].strip()
                if HeuristicCounter().count(pair["base"] + " " + cues) > 200:
                    problems.append("%s: A 里的关键内容没有落在前 512 token 内" % pid)
        # ⑤ 两臂用同一后端模板
        if exp["backend"] not in load_index()["backends"]:
            problems.append("%s: 后端 %r 没有 API workflow" % (eid, exp["backend"]))
    return problems


# ─────────────────────────────────────────────────────────────
# 出图
# ─────────────────────────────────────────────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _queue_and_wait(api: str, wf: dict, timeout: float = 900.0) -> dict:
    api = api.rstrip("/")
    t0 = time.time()
    res = post_json(api + "/prompt", {"prompt": wf, "client_id": "pitfalls-ab"})
    if res.get("node_errors"):
        raise RuntimeError("服务端拒收：%s" % json.dumps(res["node_errors"],
                                                        ensure_ascii=False)[:800])
    pid = res.get("prompt_id")
    if not pid:
        raise RuntimeError("提交没有拿到 prompt_id：%r" % res)
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        hist = get_json("%s/history/%s" % (api, pid))
        if pid in hist:
            entry = hist[pid]
            status = (entry.get("status") or {}).get("status_str")
            return {"prompt_id": pid, "status": status, "entry": entry,
                    "elapsed_s": round(time.time() - t0, 1)}
    raise TimeoutError("prompt %s 超时 %.0fs 未完成" % (pid, timeout))


def _collect_outputs(entry: dict, save_node: str) -> list[dict]:
    out = (entry.get("outputs") or {}).get(save_node) or {}
    return [it for it in (out.get("images") or []) if isinstance(it, dict)]


def _download(api: str, item: dict, dest: Path) -> Path:
    query = urllib.parse.urlencode({"filename": item.get("filename", ""),
                                    "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
    url = api.rstrip("/") + "/view?" + query
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    dest.write_bytes(data)
    return dest


def compose_pair(image_a: Path, image_b: Path, dest: Path) -> Path:
    """A|B 并排拼图（左 A 右 B），减少目检要看的张数，也避免来回切图造成的判偏。"""
    from PIL import Image, ImageDraw
    ia, ib = Image.open(image_a).convert("RGB"), Image.open(image_b).convert("RGB")
    h = max(ia.height, ib.height)
    ia = ia.resize((round(ia.width * h / ia.height), h))
    ib = ib.resize((round(ib.width * h / ib.height), h))
    band = 28
    canvas = Image.new("RGB", (ia.width + ib.width + 8, h + band), (24, 24, 24))
    canvas.paste(ia, (0, band))
    canvas.paste(ib, (ia.width + 8, band))
    draw = ImageDraw.Draw(canvas)
    draw.text((6, 6), "A (treatment)", fill=(255, 255, 255))
    draw.text((ia.width + 14, 6), "B (control)", fill=(255, 255, 255))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest


def run_experiments(api: str = DEFAULT_API, dry: bool = False,
                    only: list[str] | None = None, timeout: float = 900.0) -> dict:
    """真出图（占 GPU）。`dry=True` 只把要提交的 workflow 落盘。"""
    idx = load_index()
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest = {"started_at": started, "api": api, "dry": bool(dry),
                "experiments": [], "images": []}
    for exp in EXPERIMENTS:
        if only and exp["id"] not in only:
            continue
        backend = exp["backend"]
        spec = idx["backends"][backend]
        exp_dir = OUT / exp["id"]
        exp_entry = {"id": exp["id"], "backend": backend, "pitfall": PITFALL_TEXT[exp["pitfall_key"]],
                     "pairs": []}
        for pair in exp["pairs"]:
            a, b = resolved_prompts(exp, pair)
            pr = {"id": pair["id"], "seed": pair["seed"],
                  "width": pair["width"], "height": pair["height"],
                  "steps": spec["defaults"]["steps"], "cfg": spec["defaults"]["cfg"],
                  "sampler": spec["defaults"]["sampler_name"],
                  "scheduler": spec["defaults"]["scheduler"],
                  "variable": pair["variable"],
                  "A": {"prompt": a, "sha": _sha256(a)},
                  "B": {"prompt": b, "sha": _sha256(b)},
                  "arms": {}}
            for arm, prompt in (("A", a), ("B", b)):
                prefix = "pitfalls_ab/%s/%s/%s" % (exp["id"], pair["id"], arm)
                wf = build_workflow(backend, prompt, pair["seed"], pair["width"],
                                    pair["height"], prefix)
                if dry:
                    wpath = exp_dir / "workflows" / ("%s.%s.api.json" % (pair["id"], arm))
                    wpath.parent.mkdir(parents=True, exist_ok=True)
                    wpath.write_text(json.dumps(wf, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                    pr["arms"][arm] = {"workflow": str(wpath.relative_to(ROOT))}
                    continue
                q = _queue_and_wait(api, wf, timeout=timeout)
                if q["status"] != "success":
                    raise RuntimeError("%s/%s status=%s：%s"
                                       % (pair["id"], arm, q["status"],
                                          json.dumps(q["entry"].get("status"),
                                                     ensure_ascii=False)[:600]))
                items = _collect_outputs(q["entry"], spec["nodes"]["save"])
                if not items:
                    raise RuntimeError("%s/%s 没有输出图" % (pair["id"], arm))
                image = _download(api, items[0],
                                  exp_dir / pair["id"] / ("%s.png" % arm))
                pr["arms"][arm] = {"prompt_id": q["prompt_id"],
                                   "elapsed_s": q["elapsed_s"],
                                   "server_file": items[0],
                                   "image": str(image.relative_to(ROOT))}
                manifest["images"].append(pr["arms"][arm]["image"])
                print("  ✓ %-10s %s %s %.1fs -> %s"
                      % (exp["id"], pair["id"], arm, q["elapsed_s"], image.name))
            if not dry:
                a_path = ROOT / pr["arms"]["A"]["image"]
                b_path = ROOT / pr["arms"]["B"]["image"]
                pr["compare"] = str(compose_pair(a_path, b_path,
                                                 exp_dir / pair["id"] / "compare.png"
                                                 ).relative_to(ROOT))
            exp_entry["pairs"].append(pr)
        manifest["experiments"].append(exp_entry)
        # 增量落盘：中途被打断也能看到已跑到哪、已有哪些图（不留半截记忆）
        manifest.setdefault("partial", True)
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    manifest.pop("partial", None)
    manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest["note"] = ("dry 模式：只落 workflow，未提交" if dry else
                        "AI 目检不是人类目检；判定规则见 EXPERIMENTS.pair_rule")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


# ─────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────

def report_markdown(verdicts: dict | None = None) -> str:
    """把 verdicts 汇总成可直接粘进 docs 的 markdown 片段。"""
    if verdicts is None:
        if not VERDICTS.is_file():
            return "（还没有 verdicts.json：先跑 --judge）"
        verdicts = json.loads(VERDICTS.read_text(encoding="utf-8"))
    lines = ["| pitfall | 后端 | 结论 | 逐对 |", "|---|---|---|---|"]
    for exp in verdicts.get("experiments") or []:
        pairs = "；".join("%s=%s" % (p["pair"], {
            SUPPORT: "支持", REFUTE: "推翻", INCONCLUSIVE: "无法归因"}[p["class"]])
            for p in exp["pairs"])
        lines.append("| %s | %s | **%s** | %s |"
                     % (exp["pitfall_key"], exp["backend"], exp["verdict_zh"], pairs))
    return "\n".join(lines)


def pair_table_markdown(verdicts: dict | None = None,
                        manifest: dict | None = None) -> str:
    """docs §4 的逐对证据表。图片路径取自 manifest（拼图优先）。"""
    if verdicts is None:
        verdicts = (json.loads(VERDICTS.read_text(encoding="utf-8"))
                    if VERDICTS.is_file() else {"experiments": []})
    if manifest is None:
        manifest = (json.loads(MANIFEST.read_text(encoding="utf-8"))
                    if MANIFEST.is_file() else {})
    compares = {}
    for exp in manifest.get("experiments") or []:
        for pr in exp.get("pairs") or []:
            compares[(exp.get("id"), pr.get("id"))] = pr.get("compare") or ""
    out = []
    for exp in verdicts.get("experiments") or []:
        out.append("### %s · %s（结论：**%s**，逐对 %s）"
                   % (exp["id"], exp["claim"], exp["verdict_zh"],
                      "/".join("%s=%d" % kv for kv in exp["counts"].items())))
        out.append("")
        out.append("| pair | 拼图 | 目检观察 | 逐对分类 |")
        out.append("|---|---|---|---|")
        for pr in exp.get("pairs") or []:
            img = compares.get((exp["id"], pr["pair"])) or ""
            obs = pr.get("observation") or {}
            note = str(obs.get("note") or "").replace("|", "\\|")
            fields = {k: v for k, v in obs.items()
                      if k not in ("note", "seed", "compare")}
            out.append("| %s | %s | %s；%s | %s |"
                       % (pr["pair"],
                          ("[compare](%s)" % img) if img else "—",
                          json.dumps(fields, ensure_ascii=False), note,
                          {SUPPORT: "支持", REFUTE: "推翻",
                           INCONCLUSIVE: "无法归因"}[pr["class"]]))
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T6：registry.pitfalls 的可证伪 A/B")
    ap.add_argument("--check", action="store_true", help="离线验实验设计")
    ap.add_argument("--check-workflows", action="store_true", help="只读 /object_info 验 API 图")
    ap.add_argument("--plan", action="store_true", help="落实验计划 plan.json")
    ap.add_argument("--run", action="store_true", help="真出图（占 GPU）")
    ap.add_argument("--dry", action="store_true", help="配合 --run：只落 workflow 不提交")
    ap.add_argument("--only", action="append", default=[], help="只跑指定实验 id")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--observations-template", action="store_true",
                    help="打印目检 observation 的 JSON 骨架")
    ap.add_argument("--observations-draft", action="store_true",
                    help="写 out/pitfalls_ab/observations.json（含拼图路径，字段留空待填）")
    ap.add_argument("--force", action="store_true", help="配合 --observations-draft：覆盖已有文件")
    ap.add_argument("--judge", metavar="OBS.json", help="读目检 observation → 落 verdicts.json")
    ap.add_argument("--strict", action="store_true",
                    help="配合 --judge：observation 不完整就直接报错，不写 verdicts")
    ap.add_argument("--tokenize", action="store_true",
                    help="用 llama-server /tokenize 数每条 prompt 的真实 token（占 GPU 一小会儿）")
    ap.add_argument("--server", default="", help="llama-server 可执行文件（默认查 MOSS_LLM_SERVER/G7_SERVER）")
    ap.add_argument("--model", default="", help="token 计数用的 GGUF（默认查 MOSS_LLM_MODEL/G7_MODEL）")
    ap.add_argument("--mmproj", default="", help="视觉塔 GGUF（默认查 MOSS_LLM_MMPROJ/G7_MMPROJ）")
    ap.add_argument("--ctx", type=int, default=None, help="token 计数侧车 ctx（默认 4096）")
    ap.add_argument("--inject-tokens", action="store_true",
                    help="把 out/pitfalls_ab/tokens.json 的真实 token 数并入 observations.json")
    ap.add_argument("--report", action="store_true", help="打印汇总 markdown")
    ap.add_argument("--pair-table", action="store_true",
                    help="打印 docs §4 的逐对证据表（并落 out/pitfalls_ab/pair_table.md）")
    args = ap.parse_args(argv)

    if args.check:
        problems = check_design()
        for p in problems:
            print("✗ " + p)
        if not problems:
            print("✓ 实验设计通过：3 条 pitfall × ≥3 对，seed/尺寸/参数一致，断言逐字来自 registry")
        return 1 if problems else 0

    if args.check_workflows:
        problems = check_workflows(args.api)
        for p in problems:
            print("✗ " + p)
        if not problems:
            print("✓ 3 份 API workflow 通过 /object_info 校验（未提交任何任务）")
        return 1 if problems else 0

    if args.observations_template:
        print(json.dumps(observation_template(), ensure_ascii=False, indent=2))
        return 0

    if args.observations_draft:
        OUT.mkdir(parents=True, exist_ok=True)
        target = OUT / "observations.json"
        if target.is_file() and not args.force:
            print("已存在 %s（要覆盖加 --force）" % target.relative_to(ROOT))
            return 0
        target.write_text(json.dumps(observation_draft(), ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print("已写 %s：把每对的 observation 字段填上，再跑 --judge" % target.relative_to(ROOT))
        return 0

    if args.tokenize:
        try:
            conf = resolve_tokenizer_conf(server=args.server, model=args.model,
                                          mmproj=args.mmproj, ctx=args.ctx)
        except RuntimeError as exc:
            print("✗ " + str(exc))
            return 2
        doc = token_counts(conf=conf)
        for key, row in doc["pairs"].items():
            print("%-40s 真实 A=%4d B=%4d ｜ 启发式 A=%4d B=%4d"
                  % (key, row["A"], row["B"], row["heuristic_A"], row["heuristic_B"]))
        print("\n已写 %s" % (OUT / "tokens.json").relative_to(ROOT))
        return 0

    if args.inject_tokens:
        obs_path = OUT / "observations.json"
        tok_path = OUT / "tokens.json"
        if not obs_path.is_file() or not tok_path.is_file():
            print("缺 %s 或 %s" % (obs_path.relative_to(ROOT), tok_path.relative_to(ROOT)))
            return 2
        obs = json.loads(obs_path.read_text(encoding="utf-8"))
        tok = json.loads(tok_path.read_text(encoding="utf-8"))
        inject_tokens(obs, tok)
        obs_path.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
        for key, row in tok["pairs"].items():
            print("%-40s 真实 token A=%d B=%d" % (key, row["A"], row["B"]))
        print("已并入 %s" % obs_path.relative_to(ROOT))
        return 0

    if args.judge:
        obs = json.loads(Path(args.judge).read_text(encoding="utf-8"))
        problems = validate_observations(obs)
        if problems:
            print("⚠ observation 不完整，以下 pair 会被判成「无法归因」：")
            for p in problems:
                print("  - " + p)
            if args.strict:
                print("✗ --strict：不写 verdicts")
                return 2
        verdicts = judge(obs)
        OUT.mkdir(parents=True, exist_ok=True)
        VERDICTS.write_text(json.dumps(verdicts, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        for exp in verdicts["experiments"]:
            print("%-24s %s（%s）" % (exp["id"], exp["verdict_zh"],
                                     ", ".join("%s:%d" % kv for kv in exp["counts"].items())))
        print("\n" + report_markdown(verdicts))
        print("\n已写 %s" % VERDICTS.relative_to(ROOT))
        return 0

    if args.report:
        print(report_markdown())
        return 0

    if args.pair_table:
        md = pair_table_markdown()
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "pair_table.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    if args.plan:
        plan = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "design": EXPERIMENTS, "pitfall_text": PITFALL_TEXT,
                "workflow_index": load_index()}
        OUT.mkdir(parents=True, exist_ok=True)
        PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写 %s（%d 个实验 / %d 对 / %d 张图）"
              % (PLAN.relative_to(ROOT), len(EXPERIMENTS),
                 sum(len(e["pairs"]) for e in EXPERIMENTS),
                 2 * sum(len(e["pairs"]) for e in EXPERIMENTS)))
        return 0

    if args.run:
        manifest = run_experiments(api=args.api, dry=args.dry, only=args.only,
                                   timeout=args.timeout)
        n = len(manifest["images"])
        print("完成：%d 张图（dry=%s），manifest=%s"
              % (n, args.dry, MANIFEST.relative_to(ROOT)))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
