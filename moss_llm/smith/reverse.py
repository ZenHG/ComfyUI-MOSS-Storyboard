# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""反推：图片 → IR。

为什么不是「让 VLM 直接写一段 prompt」：
直接写 prompt 的模型会**边看边编**——本机实测，8B 在空 IR 上硬凑 450 词时凭空造出
「茶屋」招牌、灯笼、自行车。反推是取证，不是创作。所以分两段：

  ① 取证（VLM，温度 0.2）：只记录看得见的东西，写成结构化 JSON。
  ② 融合（确定性 Python）：修类型、补 id、校坐标、过年龄闸、算重要度。

第 ② 段不调模型，因此可单测、可复现、不会幻觉。模型只在第 ① 段出现一次。


## 与节点的 caption 通道不是同一件事（合流时确认过）

节点那条是「图 → 一段成品提示词」，一次模型调用，**产物就是终点**；
本模块是「图 → 结构化证据 → IR」，模型只出现在第一段，第二段是确定性 Python。
后者的价值是「同一份事实可以再换任意后端渲染」，前者换后端要重读一遍图。
所以两份都留；但它们**共用**同一套图像预处理（`moss_llm.vision`）与同一个侧车类
（`moss_llm.server.LlamaServer`），这两处已经收敛。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ir import Entity, IR
from .vision import Prepared, prepare

# ─────────────────────────────────────────────────────────────
# 取证提示词
# ─────────────────────────────────────────────────────────────

SYSTEM = """You are an image analyst. You produce a machine-readable scene record that a \
prompt compiler will later turn into generation prompts. You are taking evidence, not \
writing marketing copy.

Return exactly ONE JSON object. No markdown, no code fence, no commentary before or after.

Schema (types are mandatory, values are examples):

{
  "canvas": "single | multi_panel | collage",
  "style": {
    "medium": "photograph | oil painting | anime | 3d render | watercolor | vector | manga",
    "substyle": ["cinematic", "film noir"],
    "notes": "1-2 sentences on the rendering language: line quality, brushwork, grain, shading model."
  },
  "camera": {
    "shot": "extreme close-up | close-up | medium shot | full body | wide shot",
    "angle": "eye level | low angle | high angle | dutch angle | over the shoulder",
    "lens_mm": 50,
    "dof": "shallow | deep",
    "focus": "what is actually in focus",
    "notes": "1-2 sentences on framing, crop, perspective and what falls out of frame."
  },
  "lighting": {
    "key": "direction + quality of the main light",
    "fill": "what fills the shadows",
    "rim": "any rim or edge light",
    "time_of_day": "dawn | noon | dusk | night | unknown",
    "mood": "one or two words",
    "notes": "1-2 sentences on how light behaves on surfaces: falloff, speculars, shadow edges."
  },
  "composition": {
    "rules": ["rule of thirds", "centered", "leading lines"],
    "horizon": "where the horizon or ground line sits",
    "negative_space": "where the frame is empty",
    "notes": "1-2 sentences on depth layering and how the eye moves through the frame."
  },
  "atmosphere": ["three to six short atmosphere words"],
  "entities": [
    {
      "id": "e1",
      "kind": "character",
      "label": "short noun, lowercase, e.g. woman / ceramic cup / background",
      "importance": 0.9,
      "age_years": 28,
      "age_confidence": "high | medium | low",
      "descriptors": {
        "gender": "woman | man | androgynous | unknown",
        "hair": "colour, length, texture, styling",
        "skin": "tone and surface",
        "face": "bone structure, distinguishing features",
        "expression": "what the face is doing",
        "pose": "what the body is doing, including hands",
        "wardrobe": ["garment with colour, fabric, fit, condition"],
        "props": ["object held or worn"],
        "material": "for non-characters: surface material and its condition"
      },
      "spatial": {"anchor": "left foreground", "bbox": {"x_min": 8, "y_min": 12, "x_max": 46, "y_max": 96}},
      "evidence": ["the visible cue that justifies this claim"]
    },
    {
      "id": "e2",
      "kind": "text",
      "label": "headline",
      "importance": 0.9,
      "spatial": {"anchor": "top-center", "bbox": {"x_min": 20, "y_min": 4, "x_max": 80, "y_max": 14}},
      "text_content": "the exact string as printed, same language, same line breaks",
      "typography": {"weight": "bold", "case": "upper", "color": "cream", "align": "center", "style_hint": "condensed sans-serif"},
      "evidence": ["the visible cue that justifies this claim"]
    }
  ],
  "relations": [{"subject": "e1", "predicate": "holding | touching | facing | left_of | right_of | above | below | behind | in_front_of | occluding", "object": "e2"}]
}

Note the difference between the two entities above: a text entity carries "text_content" and \
"typography", a non-text entity carries "descriptors" instead. Emit the fields that fit the kind.

Rules:
1. Describe only what is visible or unambiguously implied by what is visible. Never guess brand names, logos, real identities, place names, events, prices, or text you cannot read.
2. Every character carries an explicit age in whole years. If it is ambiguous, give your single best estimate and set age_confidence accordingly. Never inflate a child's age.
3. Copy visible text verbatim, including punctuation, capitalisation and line breaks, and keep its original language. Do not translate or romanise. If it is unreadable, set text_content to "" and describe its appearance only.
4. bbox is in percent of the frame, 0-100, origin at the top-left. It must enclose the whole element, not its centre.
5. importance: 1.0 is the first thing the eye lands on; 0.3 is background texture.
6. notes must ADD what the structured fields cannot express — spatial rhythm, depth layering, surface wear, how light falls. Do not restate the fields.
7. Cover the whole frame. If a corner is empty, say so in composition.negative_space.
8. No quality words: no masterpiece, 8k, ultra detailed, best quality. No aspect ratio, resolution, step count or CFG.
9. Every entity needs at least one entry in evidence. If you cannot name a cue, drop the entity.
10. Never write none, null, unknown, n/a or not applicable as a value. If a field has nothing behind it, omit the key entirely — a key holding "none" becomes noise in the final prompt.
11. Omit text_content and typography entirely unless kind is "text".
12. Every image with visible content yields at least one entity. A record with an empty entities list is wrong for any real photograph, drawing or screenshot.
13. If the image is a screenshot, poster, slide, comic page or any page layout, treat the text as the subject: read the largest string first, then every label, then every button caption, and give each its own entity. Do not stop at the pictures.
14. canvas counts separate pictures, not boxes. A screenshot, a slide, a poster or a document page is "single" even when it is full of text and framed panels. Use multi_panel only for comic pages, storyboards and collages built from distinct images.
15. Output the JSON object only."""

USER = ("Analyse this image and return the scene record JSON. "
        "Start with { and end with }. Nothing else.")

# ─────────────────────────────────────────────────────────────
# 取证 JSON 的结构约束
# ─────────────────────────────────────────────────────────────
# llama.cpp 官方原话：「The JSON schema is only used to constrain the model output and
# is not injected into the prompt. The model has no visibility into the schema.」
# 所以这份 schema 只负责**形状**（能不能解析），内容靠上面那段 prompt。
#
# 刻意只 required 三个字段：canvas / style / entities。
# 全字段 required 会逼模型给没看见的字段编值——那正是 "none, shadows softly diffused"
# 的来源。语法严格用在校验，不用在逼供。
EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "canvas": {"type": "string", "enum": ["single", "multi_panel", "collage"]},
        "style": {
            "type": "object",
            "properties": {
                "medium": {"type": "string"},
                "substyle": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["medium"],
        },
        "camera": {
            "type": "object",
            "properties": {
                "shot": {"type": "string"},
                "angle": {"type": "string"},
                "lens_mm": {"type": "number"},
                "dof": {"type": "string"},
                "focus": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
        "lighting": {
            "type": "object",
            "properties": {
                "key": {"type": "string"}, "fill": {"type": "string"},
                "rim": {"type": "string"}, "time_of_day": {"type": "string"},
                "mood": {"type": "string"}, "notes": {"type": "string"},
            },
        },
        "composition": {
            "type": "object",
            "properties": {
                "rules": {"type": "array", "items": {"type": "string"}},
                "horizon": {"type": "string"},
                "negative_space": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
        "atmosphere": {"type": "array", "items": {"type": "string"}},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["character", "object", "text", "background"]},
                    "label": {"type": "string"},
                    "importance": {"type": "number"},
                    "age_years": {"type": "integer"},
                    "age_confidence": {"type": "string",
                                       "enum": ["high", "medium", "low"]},
                    "descriptors": {"type": "object"},
                    "spatial": {
                        "type": "object",
                        "properties": {
                            "anchor": {"type": "string"},
                            "bbox": {
                                "type": "object",
                                "properties": {
                                    "x_min": {"type": "number"},
                                    "y_min": {"type": "number"},
                                    "x_max": {"type": "number"},
                                    "y_max": {"type": "number"},
                                },
                                "required": ["x_min", "y_min", "x_max", "y_max"],
                            },
                        },
                        "required": ["anchor"],
                    },
                    "text_content": {"type": "string"},
                    "typography": {"type": "object"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "label", "evidence"],
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object": {"type": "string"},
                },
                "required": ["subject", "predicate", "object"],
            },
        },
    },
    "required": ["canvas", "style", "entities"],
}


#: 允许的实体类型（与 MOSS 的 label 白名单对齐，多出的在此收敛）。
#: 收中文别名不是为了「支持中文」这种面子工程：中文能力强的开源 VLM
#: （InternVL / GLM-4V / MiniCPM-V 的中文版）会用中文回 kind，认不出就全降级成 object。
_KIND_MAP = {
    "character": "character", "person": "character", "human": "character",
    "people": "character", "figure": "character", "animal": "object",
    "人物": "character", "角色": "character", "人": "character",
    "女性": "character", "男性": "character", "女孩": "character", "男孩": "character",
    "object": "object", "item": "object", "prop": "object", "vehicle": "object",
    "物体": "object", "物品": "object", "道具": "object", "动物": "object", "车辆": "object",
    "text": "text", "typography": "text", "lettering": "text", "logo": "text",
    "文字": "text", "文本": "text", "标题": "text", "字幕": "text", "招牌": "text",
    "background": "background", "environment": "background", "scene": "background",
    "背景": "background", "环境": "background", "场景": "background",
    "panel": "panel", "分格": "panel", "画格": "panel",
    "effect": "effect", "特效": "effect",
}

#: 年龄段 → 代表值。只在模型没给数字时兜底，且必然报警。
#: 中英各一套：模型可能用任一语言写年龄。
_AGE_WORDS = (
    (r"\b(infant|baby|toddler)\b|婴儿|宝宝|幼儿", 2),
    (r"\b(young child|little child|small child)\b", 7),
    (r"\b(child|kid)\b|小孩|儿童|孩子", 9),
    (r"\b(pre-?teen|preteen)\b", 12),
    (r"\b(early teen|young teen)\b", 14),
    (r"\b(teen|teenager|adolescent)\b|少年|少女|青少年", 16),
    (r"\b(young adult|early twenties)\b", 24),
    (r"\b(twenties|20s)\b|二十多岁|青年", 26),
    (r"\b(thirties|30s)\b|三十多岁", 34),
    (r"\b(forties|40s)\b|四十多岁", 44),
    (r"\b(fifties|50s)\b|五十多岁", 54),
    (r"\b(sixties|60s)\b|六十多岁", 64),
    (r"\b(seventies|70s)\b|七十多岁", 74),
    (r"\b(elderly|old woman|old man|senior)\b|老人|老年|老妇|老汉", 78),
    (r"\b(middle-aged|middle aged)\b|中年", 45),
)


class ReverseError(RuntimeError):
    pass


@dataclass
class Report:
    image: str = ""
    size: tuple[int, int] | None = None
    warnings: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    entity_count: int = 0
    age_gate: list[str] = field(default_factory=list)
    raw_chars: int = 0

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def to_dict(self) -> dict:
        return {"image": self.image, "size": self.size, "warnings": self.warnings,
                "dropped": self.dropped, "entity_count": self.entity_count,
                "age_gate": self.age_gate, "raw_chars": self.raw_chars}


# ─────────────────────────────────────────────────────────────
# JSON 提取：模型一定会加围栏、加前言、加尾注
# ─────────────────────────────────────────────────────────────

_FENCE = re.compile(r"```(?:json|JSON)?\s*")


def extract_json(text: str) -> dict:
    """从任意脏文本里抠出第一个完整 JSON 对象。失败抛 ReverseError。"""
    if not text or not text.strip():
        raise ReverseError("模型返回空内容")
    s = _FENCE.sub("", text).strip()
    start = s.find("{")
    if start < 0:
        raise ReverseError("响应里没有 JSON 对象（前 200 字：%r）" % s[:200])

    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                chunk = s[start:i + 1]
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    fixed = _repair_json(chunk)
                    try:
                        return json.loads(fixed)
                    except json.JSONDecodeError as exc:
                        raise ReverseError(
                            "JSON 结构不合法（%s）。原文前 300 字：%r"
                            % (exc, chunk[:300])) from exc
    raise ReverseError(
        "JSON 没有闭合（缺右括号）。\n"
        "  最常见的原因是 max_tokens 不够，模型在对象中途被截断——"
        "满屏文字的图尤其容易。\n"
        "  末尾 160 字：%r" % s[-160:])


def _repair_json(chunk: str) -> str:
    """修两类最常见的坏 JSON：尾逗号、单引号键。不做语义猜测。"""
    out = re.sub(r",\s*([}\]])", r"\1", chunk)
    return re.sub(r"'([^'\\]*)'\s*:", r'"\1":', out)


# ─────────────────────────────────────────────────────────────
# 融合：脏 JSON → 干净 IR
# ─────────────────────────────────────────────────────────────

#: 模型爱把「没有」写成值。这些进了 prompt 就是「not applicable, none, none」这种噪声。
_JUNK = {"none", "n/a", "na", "not applicable", "unknown", "null", "nil", "none.",
         "-", "--", "—", "", "[]", "{}", "not visible", "none visible", "no",
         "nothing", "not present", "absent", "not shown", "unclear", "indeterminate",
         "tbd", "undefined", "unspecified", "not specified", "no data"}


def _prune(obj, hits: list | None = None, _depth: int = 0) -> object:
    """递归剔除空值/占位值。**不碰 text_content**——招牌上写着 NONE 是合法内容。

    `hits` 收集被剔除的原值，用于出声（静默清洗等于让用户以为模型没犯这错）。
    """
    if _depth > 12:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "text_content":
                if isinstance(v, str) and v.strip():
                    out[k] = v
                continue
            pv = _prune(v, hits, _depth + 1)
            if pv in ("", [], {}, None) and not isinstance(pv, (int, float)):
                continue
            out[k] = pv
        return out
    if isinstance(obj, list):
        return [pv for pv in (_prune(v, hits, _depth + 1) for v in obj)
                if not (pv in ("", [], {}, None) and not isinstance(pv, (int, float)))]
    if isinstance(obj, str):
        s = " ".join(obj.split())
        if s.strip().lower().strip(".") in _JUNK:
            if hits is not None:
                hits.append(s)
            return ""
        # 模型会把三个字段挤进一个字符串："none, shadows softly diffused"。
        # 整串匹配抓不住，必须按逗号拆开逐段判。
        if "," in s:
            segs = [x.strip() for x in s.split(",")]
            keep = [x for x in segs if x and x.lower().strip(".") not in _JUNK]
            if len(keep) != len([x for x in segs if x]):
                if hits is not None:
                    hits.extend(x for x in segs if x and x.lower().strip(".") in _JUNK)
                s = ", ".join(keep)
        return s
    return obj


#: 方位词 → 期望区域（x0,x1,y0,y1），坐标归一化
_ANCHOR_ZONE = {
    "left": (0.0, 0.5, 0.0, 1.0), "right": (0.5, 1.0, 0.0, 1.0),
    "top": (0.0, 1.0, 0.0, 0.5), "bottom": (0.0, 1.0, 0.5, 1.0),
    "upper": (0.0, 1.0, 0.0, 0.5), "lower": (0.0, 1.0, 0.5, 1.0),
    "top-left": (0.0, 0.5, 0.0, 0.5), "top-right": (0.5, 1.0, 0.0, 0.5),
    "bottom-left": (0.0, 0.5, 0.5, 1.0), "bottom-right": (0.5, 1.0, 0.5, 1.0),
}


def _anchor_zone(anchor: str) -> tuple[float, float, float, float] | None:
    """从自由文本方位里取区域。取不到就返回 None（表示不做校验）。

    中英都认：中文能力强的开源 VLM 会写「左上角」「画面右侧」。
    认不出就等于放弃校验，坐标就会原样透传——这是静默降级，所以要认全。
    """
    a = _norm_where(anchor)
    if a in _ANCHOR_ZONE:
        return _ANCHOR_ZONE[a]
    vert = horiz = None
    if "top" in a or "upper" in a or "上" in a or "顶" in a:
        vert = (0.0, 0.5)
    elif "bottom" in a or "lower" in a or "下" in a or "底" in a:
        vert = (0.5, 1.0)
    if "left" in a or "左" in a:
        horiz = (0.0, 0.5)
    elif "right" in a or "右" in a:
        horiz = (0.5, 1.0)
    if vert is None and horiz is None:
        return None
    x0, x1 = horiz or (0.0, 1.0)
    y0, y1 = vert or (0.0, 1.0)
    return (x0, x1, y0, y1)


def _resolve_bbox(anchor: str, box: list[float] | None,
                  kind: str = "object") -> tuple[list[float] | None, str]:
    """用方位校验坐标，冲突时**换通道**重建。返回 (box, source)。

    source 取值：
      "model"  —— 框来自模型坐标，且方位不冲突（或没给方位，无从校验）
      "anchor" —— 框由方位重建：位置取方位，尺寸取模型给的宽高
      "none"   —— 没有可用的几何信息

    为什么不是「裁剪」而是「重建」：
    实测四格漫画，同一个响应里 anchor 7/7 全对，bbox 有 3/7 落在错误象限。
    这不是「坐标略有偏差」，是**坐标通道本身坏了**（学界对 8B 级 VLM 的共识，
    见 docs/RESEARCH-2026-10-31-业界对照.md）。坏通道的修法不是把它的输出裁一裁
    ——裁剪后尺寸仍然来自坏通道，只能算半修。真正该做的是换通道：
    方位可靠，位置就用方位；坐标里唯一还值钱的是**宽高比**（气泡是扁的、
    标题是横条），所以保留尺寸、替换位置。

    为什么没框的 text 实体也要造一个框：
    文字块是布局唯一真正需要的对象（MOSS 的 Layout JSON 只排文字）。一个气泡
    只要有 "top-left"，就足以定位，缺框不该导致整页失去布局能力。
    非 text 实体不造框——全身人像的 "left foreground" 造出来的框会把
    单张照片误判成需要布局（这是踩过的坑，见 needs_layout 的注释）。
    """
    zone = _anchor_zone(anchor)
    if box is None:
        if kind != "text":
            return None, "none"
        # 九宫格优先：比"左半幅/上半幅"精细，且能认 "center" 这种粗方位
        fine = _where_box(anchor)
        if fine:
            return fine, "anchor"
        if zone is None:
            return None, "none"
        return _box_in_zone(zone, None), "anchor"

    if zone is None:
        return box, "model"

    x0z, x1z, y0z, y1z = zone
    ix = max(0.0, min(box[2], x1z) - max(box[0], x0z))
    iy = max(0.0, min(box[3], y1z) - max(box[1], y0z))
    area = _area(box)
    if area <= 0:
        return None, "none"
    if (ix * iy) / area >= 0.5:
        return box, "model"
    return _box_in_zone(zone, box), "anchor"


def _box_in_zone(zone: tuple[float, float, float, float],
                 box: list[float] | None) -> list[float]:
    """在方位区域里摆一个框：位置居中于区域，宽高沿用 `box`（无则取区域 50%）。"""
    x0z, x1z, y0z, y1z = zone
    zw, zh = x1z - x0z, y1z - y0z
    w = (box[2] - box[0]) if box else zw * 0.5
    h = (box[3] - box[1]) if box else zh * 0.5
    w, h = min(w, zw), min(h, zh)
    cx = min(max((x0z + x1z) / 2.0, x0z + w / 2.0), x1z - w / 2.0)
    cy = min(max((y0z + y1z) / 2.0, y0z + h / 2.0), y1z - h / 2.0)
    return [round(cx - w / 2.0, 4), round(cy - h / 2.0, 4),
            round(cx + w / 2.0, 4), round(cy + h / 2.0, 4)]


def _canvas_type(obs: dict, entities: list | None = None) -> str:
    """单图 / 多格 / 拼贴。MOSS 的 summary.canvas 是枚举，写错会被编译器拒。

    两个方向都要救，因为模型两边都会错：

    ① 四格漫画说成 single（实测）——一页里有多个独立文字块、且有角色，基本是分格页。
    ② **UI 截图说成 multi_panel**（实测 ui-smoke.png）——模型看见「4 格故事板」
       这几个字就以为画面是四格。判据反过来了：满屏文字、一个角色都没有，
       那是文档/界面，不是分格画。

    ②这条是加了文字专线之后才冒出来的：文字块一多，「文字多 ⇒ 分格」的旧判据
    会把所有截图都判成分格页。
    """
    raw = str(obs.get("canvas") or obs.get("canvas_type") or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    ents = entities if entities is not None else [
        e for e in (obs.get("entities") or []) if isinstance(e, dict)]
    texts = [e for e in ents if _kind_of(e) == "text"]
    chars = [e for e in ents if _kind_of(e) == "character"]

    if raw in ("single", "multi_panel", "collage"):
        if raw == "multi_panel" and len(texts) >= 6 and not chars:
            return "single"
        return raw
    if len(texts) >= 3 and chars:
        return "multi_panel"
    return "single"


def _kind_of(e) -> str:
    """实体（dataclass 或原始 dict）的归一化 kind。"""
    if isinstance(e, dict):
        return _KIND_MAP.get(str(e.get("kind") or e.get("type") or "").lower().strip(), "")
    return getattr(e, "kind", "")


def _as_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        n = int(round(float(v)))
        return n if 0 < n < 130 else None
    if isinstance(v, str):
        # 用数字边界而不是 \b：Python 的 \w 含中日韩字符，所以 "28岁" 里
        # "8" 和 "岁" 之间**没有**词边界，\b\d+\b 一个都匹配不到。
        m = re.search(r"(?<!\d)(\d{1,3})(?!\d)", v)
        if m:
            n = int(m.group(1))
            return n if 0 < n < 130 else None
        low = v.lower()
        for pat, val in _AGE_WORDS:
            if re.search(pat, low):
                return val
    return None


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [x for x in v if str(x).strip()]
    if isinstance(v, str):
        return [p.strip() for p in re.split(r";|\n|\u3001", v) if p.strip()]
    return [v]


def _as_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _coerce_bbox(raw) -> list[float] | None:
    """任意写法的框 → 归一化 [x0,y0,x1,y1]（0-1，左上原点）。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        keys = ("x_min", "y_min", "x_max", "y_max")
        if all(k in raw for k in keys):
            vals = [_as_float(raw[k]) for k in keys]
        else:
            alias = (("left", "top", "right", "bottom"), ("x0", "y0", "x1", "y1"),
                     ("xmin", "ymin", "xmax", "ymax"))
            vals = None
            for group in alias:
                if all(k in raw for k in group):
                    vals = [_as_float(raw[k]) for k in group]
                    break
            if vals is None:
                return None
        x0, y0, x1, y1 = vals
    elif isinstance(raw, (list, tuple)) and len(raw) >= 4:
        x0, y0, x1, y1 = [_as_float(v) for v in raw[:4]]
    else:
        return None

    # 尺度判定。实测模型会给出三种刻度，必须都认：
    #   0-1（归一化）、0-100（prompt 要求的百分比）、0-1000（Qwen-VL 系的老习惯）
    # 只按「>1 就除 100」判，0-1000 会被除成 7.84 再夹到 1.0，框直接烂掉。
    m = max(x0, y0, x1, y1)
    if m > 100.001:
        x0, y0, x1, y1 = x0 / 1000.0, y0 / 1000.0, x1 / 1000.0, y1 / 1000.0
    elif m > 1.001:
        x0, y0, x1, y1 = x0 / 100.0, y0 / 100.0, x1 / 100.0, y1 / 100.0
    # 反了就是 xywh（宽高被当成了终点）
    if x1 < x0:
        x0, x1 = x0, x0 + x1
    if y1 < y0:
        y0, y1 = y0, y0 + y1
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    def clamp(v):
        return min(1.0, max(0.0, v))

    box = [clamp(x0), clamp(y0), clamp(x1), clamp(y1)]
    if box[2] - box[0] < 1e-3 or box[3] - box[1] < 1e-3:
        return None
    return [round(v, 4) for v in box]


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def coerce_ir(obs: dict, report: Report | None = None,
              prepared: Prepared | None = None, task: str = "reverse") -> IR:
    """把取证 JSON 修成合法 IR。所有修正都记进 report，不静默。

    `task` 由调用方给：反推是 `reverse`，读 prompt 是 `decompose`。两者用同一套
    修正逻辑——碰到的脏值是同一批（占位值、悬空关系、越界坐标），修法也该是同一套。
    """
    r = report or Report()
    if not isinstance(obs, dict):
        raise ReverseError("取证结果不是 JSON 对象")

    # 先洗值：模型会把「没有」写成 "none" / "not applicable" / "unknown"，
    # 这些词一旦进了散文就是噪声（实测产出过 "none, unknown, neutral"）。
    hits: list[str] = []
    pruned = _prune(obs, hits)
    if hits:
        r.warn("已剔除 %d 处占位值：%s" % (len(hits), "、".join(sorted(set(hits))[:6])))
    obs = pruned

    # 常见包裹层
    for key in ("scene", "observation", "result", "ir", "record"):
        if isinstance(obs.get(key), dict) and "entities" not in obs:
            obs = obs[key]
            r.warn("取证结果被包在 %r 里，已拆开" % key)
            break

    raw_entities = obs.get("entities") or obs.get("objects") or []
    if isinstance(raw_entities, dict):
        raw_entities = [dict(v, id=k) if isinstance(v, dict) else {"id": k}
                        for k, v in raw_entities.items()]
    if not isinstance(raw_entities, list):
        raw_entities = []

    entities: list[Entity] = []
    used_ids: set[str] = set()
    for i, raw in enumerate(raw_entities, start=1):
        if not isinstance(raw, dict):
            r.dropped.append("非对象实体 #%d" % i)
            continue
        eid = str(raw.get("id") or "e%d" % i).strip() or "e%d" % i
        while eid in used_ids:
            eid += "_"
        used_ids.add(eid)

        kind_raw = str(raw.get("kind") or raw.get("type") or "object").lower().strip()
        kind = _KIND_MAP.get(kind_raw)
        if kind is None:
            r.warn("未知实体类型 %r，按 object 处理" % kind_raw)
            kind = "object"

        desc = _as_dict(raw.get("descriptors"))
        for key in ("wardrobe", "props", "tags"):
            if key in desc:
                desc[key] = _as_list(desc[key])

        text_content = raw.get("text_content")
        if isinstance(text_content, list):
            text_content = "\n".join(str(t) for t in text_content)
        if kind == "text":
            if not (isinstance(text_content, str) and text_content.strip()):
                # 直接丢掉，不降级成 object。
                # 实测 UI 截图：模型把 10 处文字都标成 text 却不抄内容，降级成 object 后
                # 变成 10 个 label="unsaved workflow (3)" 的空壳，既进散文又进 Layout，
                # 把画面描述污染成「10 个主体」。文字专线会把这些补回来，且带内容。
                r.dropped.append("实体 %s 标为 text 但没有文字" % eid)
                continue
            text_content = text_content.replace("\r\n", "\n").strip()

        spatial = _as_dict(raw.get("spatial"))
        box = None
        for key in ("bbox", "bbox_pct", "bbox_norm", "box", "bbox_2d"):
            if key in spatial:
                box = _coerce_bbox(spatial[key])
                break
            if key in raw:
                box = _coerce_bbox(raw[key])
                break
        spatial = {k: v for k, v in spatial.items()
                   if k in ("anchor", "depth", "occlusion") and str(v).strip()}
        had_box = box is not None
        box, source = _resolve_bbox(str(spatial.get("anchor") or ""), box, kind)
        if source == "anchor":
            if had_box:
                r.warn("实体 %s 的 bbox 与方位 %r 冲突，已按方位重建（坐标只作尺寸提示）"
                       % (eid, spatial.get("anchor")))
            else:
                r.warn("实体 %s 没给 bbox，已按方位 %r 生成近似框"
                       % (eid, spatial.get("anchor")))
        if box:
            spatial["bbox_norm"] = box
            spatial["bbox_source"] = source
        elif had_box:
            r.warn("实体 %s 的 bbox 无法解析，已丢弃坐标" % eid)

        imp = raw.get("importance")
        importance = _as_float(imp, -1.0)
        if not (0.0 <= importance <= 1.0):
            importance = 0.45 + 0.5 * min(1.0, _area(box) ** 0.5) if box else 0.6
            # 缺字段是常态（schema 只 required 三个字段），不报；
            # 报了值但越界才是模型犯错，那才值得出声。
            if imp not in (None, ""):
                r.warn("实体 %s 的 importance=%r 越界，按面积推算为 %.2f"
                       % (eid, imp, importance))

        age = _as_int(raw.get("age_years")) if kind == "character" else None
        if kind == "character":
            if age is None:
                age = _as_int(desc.get("age") or desc.get("apparent_age"))
            if age is None:
                age = _as_int(raw.get("age") or "")
            if age is None:
                r.age_gate.append("%s 没有年龄，已留给人工补（MOSS 要求显式岁数）" % eid)
            elif str(raw.get("age_confidence", "")).lower() in ("low", "very low"):
                r.warn("实体 %s 的年龄 %d 是低置信估计" % (eid, age))

        label = str(raw.get("label") or "").strip().lower()
        if not label:
            label = str(desc.get("gender") or "").strip().lower() or kind
            r.warn("实体 %s 缺 label，用 %r 兜底" % (eid, label))

        entities.append(Entity(
            id=eid, kind=kind, label=label, importance=round(importance, 3),
            age_years=age, descriptors=desc, spatial=spatial,
            text_content=text_content,
            typography=_as_dict(raw.get("typography")),
            evidence=[str(x) for x in _as_list(raw.get("evidence"))],
        ))

    # 关系：两端必须存在，且不能指向文字块
    # （"the woman faces the 他还在楼下" 是把排版当成了人物，纯噪声）
    text_ids = {e.id for e in entities if e.kind == "text"}
    relations = []
    for rel in _as_list(obs.get("relations")):
        if not isinstance(rel, dict):
            continue
        s, o = str(rel.get("subject") or ""), str(rel.get("object") or "")
        if s in used_ids and o in used_ids and s != o:
            if s in text_ids or o in text_ids:
                r.dropped.append("指向文字块的关系 %s→%s" % (s, o))
                continue
            relations.append({"subject": s,
                              "predicate": str(rel.get("predicate") or "relates_to"),
                              "object": o})
        else:
            r.dropped.append("悬空关系 %s→%s" % (s, o))

    style = _as_dict(obs.get("style"))
    style["substyle"] = _as_list(style.get("substyle"))
    notes = {}
    for key in ("style", "camera", "lighting", "composition", "subject",
                "setting", "material", "medium"):
        src = style if key == "style" else _as_dict(obs.get(key))
        if key == "style":
            val = src.get("notes")
        else:
            val = src.get("notes")
        if isinstance(val, str) and val.strip():
            notes[key if key != "style" else "medium"] = " ".join(val.split())

    ir = IR(
        task=task,
        canvas={"aspect_ratio": (prepared.aspect_ratio if prepared and prepared.aspect_ratio
                                 else "1:1"),
                "canvas_type": _canvas_type(obs, entities)},
        entities=entities, relations=relations, style=style,
        camera=_as_dict(obs.get("camera")), lighting=_as_dict(obs.get("lighting")),
        composition=_as_dict(obs.get("composition")),
        atmosphere=[str(a) for a in _as_list(obs.get("atmosphere"))],
        notes=notes,
        provenance={"source": task, "image": r.image,
                    "model_size": r.size, "prep": prepared.notes if prepared else []},
    )
    ir.normalize()
    r.entity_count = len(ir.entities)

    # 空取证硬闸。
    # 为什么必须在这里抛：模型什么都没看见时，下游每个后端都仍然会「写出点什么」——
    # 拿空 IR 硬凑出来的是一段通顺但完全无关的 prompt，用户看不出它是错的。
    # 学界的结论是幻觉在解码层无解（VCD/OPERA/DoLa 都依赖 logits 后处理，
    # llama-server 没有这个钩子），只能在上层拦：**空就是空，不许兜底**。
    if not ir.entities and not ir.notes and not (ir.style or {}).get("medium"):
        raise ReverseError(
            "取证为空：模型没有报出任何实体、观察或媒介信息。\n"
            "  这不能当成「这张图很简单」——它通常意味着模型没看清图或没听懂任务。\n"
            "  排查顺序：① 确认 --mmproj 已加载；② 换 --no-schema 再跑一次；\n"
            "  ③ 看 out/reverse/*.report.json 里的 raw_chars，接近 0 就是模型没出话。")
    return ir


# ─────────────────────────────────────────────────────────────
# 文字专线：第二遍只看字
# ─────────────────────────────────────────────────────────────
# 为什么需要单独一遍：
# 实测一张 1600x900 的 UI 截图，第一遍取证只描述了四张照片，标题和全部标签
# 一个字没提——而文字正是这张图的全部信息。原因是**任务竞争**：模型把预算花在
# 「这图里有什么」上，文字的优先级被图片挤掉了。
# 学界的做法是拆任务（OCR 专线 / grounding 专线），我们照做：
# 第二遍只问「把字抄下来」，不问别的。代价是一次额外带图调用，换来 UI/海报可用。

TEXT_SYSTEM = """You are a transcription pass. The image is given only so that you can \
read its text. Ignore composition, lighting, style and subject matter entirely.

Return exactly ONE JSON object, no markdown, no commentary:

{"blocks": [{"text": "the string exactly as printed", "where": "top-left", "kind": "title"}]}

Rules:
1. Copy every string verbatim, in its original language and script. Never translate, \
romanise, correct spelling, or complete a truncated word.
2. Keep the original capitalisation and punctuation. Keep a multi-line block as one \
string with \\n between lines.
3. `where` is one of: top-left, top-center, top-right, middle-left, center, \
middle-right, bottom-left, bottom-center, bottom-right. Use the block's centre.
4. `kind` is one of: title, subtitle, label, button, caption, body, sign, menu. \
When unsure, use label.
5. Read largest first. Include small print if you can actually read it; skip anything \
you cannot read with confidence — a wrong string is worse than a missing one.
6. At most 24 blocks. If there is no readable text at all, return {"blocks": []}.
7. Output the JSON object only."""

TEXT_USER = "Transcribe the text in this image. Output the JSON object only."

TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "where": {"type": "string", "enum": [
                        "top-left", "top-center", "top-right",
                        "middle-left", "center", "middle-right",
                        "bottom-left", "bottom-center", "bottom-right"]},
                    "kind": {"type": "string", "enum": [
                        "title", "subtitle", "label", "button",
                        "caption", "body", "sign", "menu"]},
                },
                "required": ["text", "where"],
            },
        },
    },
    "required": ["blocks"],
}

#: 九宫格。文字块只要落对格子就够排了，不需要像素级坐标。
_CELL = {"left": (0.0, 1 / 3), "center": (1 / 3, 2 / 3), "right": (2 / 3, 1.0)}

#: 中文方位 → 九宫格键。按长度降序匹配，避免「左上」被「上中」抢先。
_CJK_WHERE = (
    ("左上", "top-left"), ("左下", "bottom-left"),
    ("右上", "top-right"), ("右下", "bottom-right"),
    ("上中", "top-center"), ("下中", "bottom-center"),
    ("左中", "middle-left"), ("右中", "middle-right"),
    ("正中", "center"), ("中心", "center"), ("中间", "center"),
    ("顶部", "top-center"), ("底部", "bottom-center"),
    ("左侧", "middle-left"), ("右侧", "middle-right"),
)


def _norm_where(where: str) -> str:
    """把任意写法的方位收敛成九宫格键（或原样返回）。"""
    w = (where or "").strip().lower().replace("_", "-").replace(" ", "-")
    for cjk, en in _CJK_WHERE:
        if cjk in w:
            return en
    return w

#: kind → (标签, 重要度)
_TEXT_KIND = {
    "title": ("headline", 0.92), "subtitle": ("subheading", 0.75),
    "label": ("label", 0.6), "button": ("button", 0.6),
    "caption": ("caption", 0.5), "body": ("body text", 0.5),
    "sign": ("sign", 0.7), "menu": ("menu item", 0.6),
}


def _where_box(where: str) -> list[float] | None:
    """九宫格 → 归一化框。格内收 15%，得到一个像样的文字块尺寸。"""
    w = _norm_where(where)
    parts = w.split("-")
    if len(parts) == 2:
        vert, horiz = parts
    elif w == "center":
        vert, horiz = "center", "center"
    else:
        return None
    vmap = {"top": (0.0, 1 / 3), "middle": (1 / 3, 2 / 3),
            "center": (1 / 3, 2 / 3), "bottom": (2 / 3, 1.0)}
    if vert not in vmap or horiz not in _CELL:
        return None
    y0, y1 = vmap[vert]
    x0, x1 = _CELL[horiz]
    return [round(x0 + (x1 - x0) * 0.15, 4), round(y0 + (y1 - y0) * 0.15, 4),
            round(x1 - (x1 - x0) * 0.15, 4), round(y1 - (y1 - y0) * 0.15, 4)]


def _norm_text(s: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", (s or "").lower())


def merge_text_blocks(ir: IR, blocks: list, report: Report | None = None) -> list[str]:
    """把转录结果并进 IR。返回变更说明。不改入参之外的任何东西。

    合并规则刻意保守：同一条文字只保留一份。第一遍已经读到的不再重复添加，
    但会补上缺的坐标——坐标正是第一遍最容易漏的东西。
    """
    r = report or Report()
    changes: list[str] = []
    existing = [e for e in ir.entities if e.kind == "text"]
    used = {e.id for e in ir.entities}
    n = 0

    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        text = str(blk.get("text") or "").replace("\r\n", "\n").strip()
        key = _norm_text(text)
        if len(key) < 2:
            continue
        where = str(blk.get("where") or "").strip().lower()
        label, imp = _TEXT_KIND.get(str(blk.get("kind") or "").strip().lower(),
                                    ("text block", 0.55))

        hit = None
        for e in existing:
            ek = _norm_text(e.text_content or "")
            if not ek:
                continue
            if ek == key or (min(len(ek), len(key)) >= 4 and (ek in key or key in ek)):
                hit = e
                break

        if hit is not None:
            if not (hit.spatial or {}).get("bbox_norm"):
                box = _where_box(where)
                if box:
                    hit.spatial["bbox_norm"] = box
                    hit.spatial["bbox_source"] = "anchor"
                    changes.append("补坐标：%s → %s" % (text[:24], where))
            # 同一格里的碎片要清掉：证据阶段常把一句拆成两个实体
            # （"caption" + 整句），转录块是权威读法，块里包含的短实体就是碎片。
            # 判据必须带「同一格」——否则 "使用说明" 这种真标签会因为出现在
            # 某个长块里被误删（实测过）。
            for frag in list(existing):
                if frag is hit or frag.kind != "text":
                    continue
                fk = _norm_text(frag.text_content or "")
                same_cell = (frag.spatial or {}).get("anchor") == (hit.spatial or {}).get("anchor")
                if same_cell and 3 <= len(fk) < len(key) and fk in key:
                    ir.entities = [x for x in ir.entities if x is not frag]
                    existing.remove(frag)
                    changes.append("合并碎片：%s" % (frag.text_content or "")[:24])
            continue

        n += 1
        eid = "t%d" % n
        while eid in used:
            eid += "_"
        used.add(eid)
        spatial = {"anchor": where or "center"}
        box = _where_box(where)
        if box:
            spatial["bbox_norm"] = box
            spatial["bbox_source"] = "anchor"
        ir.entities.append(Entity(
            id=eid, kind="text", label=label, importance=imp,
            text_content=text, spatial=spatial, evidence=["transcription pass"]))
        existing.append(ir.entities[-1])       # 后续块要能跟它去重
        changes.append("补文字：%s" % text[:40])

    if changes:
        r.warn("文字专线并入 %d 条（%d 条新增）" % (len(changes), n))
    return changes


def transcribe(image: str | Path, llm, prepared: Prepared | None = None,
               schema: bool = True, temperature: float = 0.1,
               max_tokens: int = 1200) -> list[dict]:
    """第二遍：只转录文字。返回 blocks 列表（已剔除空串）。"""
    p = prepared or prepare(image)
    raw = llm.chat_vision(TEXT_SYSTEM, TEXT_USER, p.data_url,
                          temperature=temperature, max_tokens=max_tokens,
                          schema=TEXT_SCHEMA if schema else None, name="text_blocks")
    data = extract_json(raw)
    blocks = data.get("blocks") or data.get("text_blocks") or data.get("items") or []
    if isinstance(blocks, dict):
        blocks = [blocks]
    return [b for b in blocks if isinstance(b, dict) and str(b.get("text") or "").strip()]


def spread_anchored(ir: IR) -> int:
    """同一方位上堆了多个文字块时，把那个框竖着等分成 N 条，逐条下移。

    为什么必须做：方位只到「左上 / 左中」这个精度，同一格里的三个块会拿到
    **完全相同**的框。MOSS 的 Layout 按框去重，结果只排进去一个，另外两个
    直接消失——实测 11 个文字块只排出 9 个，还触发了 L010「文字未逐字出现」。
    把重叠的框竖排开，是让「方位」这个粗信息能撑起一列文字的代价最小的一招。

    只动 bbox_source == "anchor" 的实体：模型自己给的坐标有真信息，不去碰它。
    """
    groups: dict[tuple, list] = {}
    for e in ir.entities:
        sp = e.spatial or {}
        if e.kind != "text" or sp.get("bbox_source") != "anchor":
            continue
        key = (sp.get("anchor") or "", tuple(sp.get("bbox_norm") or ()))
        groups.setdefault(key, []).append(e)

    moved = 0
    for (_anchor, box), group in groups.items():
        if len(group) < 2 or len(box) != 4:
            continue
        x0, y0, x1, y1 = box
        n = len(group)
        # 每条至少要有 6% 画高，否则排出来是 20px 的细缝，下游没法写字。
        # 保底会撑出格子外——但近似框本来就只表达相对位置，溢出比细缝好。
        h = max((y1 - y0) / n, 0.06) if n <= 16 else 1.0 / n
        total = h * n
        start = min(max((y0 + y1) / 2.0 - total / 2.0, 0.0), max(0.0, 1.0 - total))
        for i, e in enumerate(group):
            e.spatial["bbox_norm"] = [round(x0, 4), round(start + i * h, 4),
                                      round(x1, 4), round(start + (i + 1) * h, 4)]
            moved += 1
    return moved


def reverse(image: str | Path, llm, temperature: float = 0.1,
            max_tokens: int = 8192, max_side: int = 1536,
            schema: bool = True, text_pass: bool = True) -> tuple[IR, Report]:
    """图片 → (IR, 取证报告)。`llm` 需是 LlamaServer 且看得见图。

    温度默认 0.1 而不是 0.2：同一张 UI 截图连跑两次，0.2 一次报 11 个实体、
    一次报 6 个，差的那 5 条全是文字块。取证任务上，方差就是缺陷。

    `max_tokens` 默认 8192：满屏文字的截图会写出十几条带 evidence 的实体，
    4096 实测仍会在 JSON 中途截断，而截断只会报「没有闭合」，看不出是 token 不够。
    """
    report = Report(image=str(image))
    prepared = prepare(image, max_side=max_side)
    report.size = (prepared.width, prepared.height) if prepared.width else None
    for n in prepared.notes:
        report.warn(n)

    raw = llm.chat_vision(SYSTEM, USER, prepared.data_url,
                          temperature=temperature, max_tokens=max_tokens,
                          schema=EVIDENCE_SCHEMA if schema else None,
                          name="scene_record")
    report.raw_chars = len(raw)
    obs = extract_json(raw)
    ir = coerce_ir(obs, report, prepared)

    if text_pass:
        # 失败不影响主流程：文字专线是增益，不是前提。
        try:
            blocks = transcribe(image, llm, prepared, schema=schema)
            merge_text_blocks(ir, blocks, report)
            ir.provenance["text_pass"] = {"blocks": len(blocks)}
        except (ReverseError, RuntimeError) as exc:
            report.warn("文字专线失败，已跳过：%s" % str(exc)[:160])
            ir.provenance["text_pass"] = {"blocks": 0, "error": str(exc)[:200]}

    moved = spread_anchored(ir)
    if moved:
        report.warn("%d 个文字块共用同一个近似框，已竖排等分避免 Layout 去重丢失" % moved)

    ir.provenance.update({"image": str(image), "size": report.size,
                          "prep": prepared.notes})
    return ir, report

