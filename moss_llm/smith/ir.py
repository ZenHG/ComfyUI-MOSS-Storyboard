# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""IR（Scene Packet）：模型无关的中间表示。

三条设计原则：
1. 存事实，不存句子。存 "28 / 黄色雨衣 / 左侧前景"，不存英文散文。
2. 每条事实带 importance。预算不足时按重要度裁剪，不按位置截断。
3. bbox 一律归一化 0-1、左上原点。中心原点是 MOSS 的方言，不是事实。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DIMENSIONS = ("subject", "setting", "camera", "lighting", "material", "medium", "text")

#: `timeline` 的骨架版本。它不是一个「H3 专用字段」——**时间本来就是画面的一维**，
#: 只是四个图像后端只有一帧，所以这一维退化成常量。视频后端（H3）把它显式化。
#: 空 timeline 必须序列化成 `{}`：老 IR 的 JSON 与确定性输出要逐字节不变。
TIMELINE_KEYS = ("duration_s", "mode", "style", "one_take", "shots",
                 "soundscape", "music")


def _area(box: list[float]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


@dataclass
class Entity:
    id: str
    kind: str = "object"                      # character|object|text|panel|effect|background
    label: str = ""
    importance: float = 0.7
    age_years: int | None = None
    descriptors: dict[str, Any] = field(default_factory=dict)
    spatial: dict[str, Any] = field(default_factory=dict)
    text_content: str | None = None
    typography: dict[str, Any] = field(default_factory=dict)
    must_keep: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "Entity":
        return Entity(
            id=d["id"], kind=d.get("kind", "object"), label=d.get("label", ""),
            importance=float(d.get("importance", 0.7)), age_years=d.get("age_years"),
            descriptors=dict(d.get("descriptors") or {}),
            spatial=dict(d.get("spatial") or {}),
            text_content=d.get("text_content"),
            typography=dict(d.get("typography") or {}),
            must_keep=list(d.get("must_keep") or []),
            evidence=list(d.get("evidence") or []),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "label": self.label,
                               "importance": self.importance}
        for k in ("age_years", "text_content"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        for k in ("descriptors", "spatial", "typography"):
            v = getattr(self, k)
            if v:
                out[k] = v
        for k in ("must_keep", "evidence"):
            v = getattr(self, k)
            if v:
                out[k] = v
        return out


@dataclass
class IR:
    ir_version: str = "0.1"
    task: str = "t2i"
    canvas: dict = field(default_factory=lambda: {"aspect_ratio": "1:1"})
    entities: list[Entity] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    style: dict = field(default_factory=dict)
    camera: dict = field(default_factory=dict)
    lighting: dict = field(default_factory=dict)
    composition: dict = field(default_factory=dict)
    atmosphere: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    #: 维度 → 一段精确的观察性散文。反推时由 VLM 写入，确定性构造时为空。
    #: 为什么与结构化字段并存：结构化字段便宜、抗截断；notes 丰富但可牺牲。
    notes: dict = field(default_factory=dict)
    #: 时间线。图像后端不需要（只有一帧），视频后端必须。结构见 h3.py。
    #: 空 dict 表示「这份 IR 没有时间维」——序列化时整个键省略。
    timeline: dict = field(default_factory=dict)

    # ── 构造 ────────────────────────────────────────────────
    @staticmethod
    def from_dict(d: dict) -> "IR":
        known = set(IR.__dataclass_fields__)
        kw = {k: v for k, v in d.items() if k in known and k != "entities"}
        ir = IR(**kw)
        ir.entities = [Entity.from_dict(e) for e in d.get("entities", [])]
        ir.normalize()
        return ir

    @staticmethod
    def load(path: str | Path) -> "IR":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in (".yaml", ".yml"):
            # 没装 PyYAML 也能读：走内置子集解析器；读不懂就明确报错，不猜。
            from .miniyaml import load_file

            data, _ = load_file(p)
        else:
            data = json.loads(text)
        return IR.from_dict(data)

    def to_dict(self) -> dict:
        out = {
            "ir_version": self.ir_version, "task": self.task, "canvas": self.canvas,
            "entities": [e.to_dict() for e in self.entities], "relations": self.relations,
            "style": self.style, "camera": self.camera, "lighting": self.lighting,
            "composition": self.composition, "atmosphere": self.atmosphere,
            "constraints": self.constraints, "budget": self.budget,
            "provenance": self.provenance, "notes": self.notes,
        }
        # 只有真有时间线才写这个键。老 IR 的 JSON 因此逐字节不变——
        # 回归基线钉住的是「输出不变」，不是「多了一个恒为空的分支」。
        if self.timeline:
            out["timeline"] = self.timeline
        return out

    # ── 规范化 ──────────────────────────────────────────────
    def normalize(self) -> "IR":
        """补默认值、夹紧范围、稳定排序。幂等。"""
        self.entities.sort(key=lambda e: (-e.importance, e.id))
        for e in self.entities:
            e.importance = min(1.0, max(0.0, float(e.importance)))
            bb = e.spatial.get("bbox_norm")
            if bb:
                e.spatial["bbox_norm"] = [min(1.0, max(0.0, float(v))) for v in bb]
                x0, y0, x1, y1 = e.spatial["bbox_norm"]
                if x1 < x0 or y1 < y0:      # 容忍写反，不静默丢弃
                    e.spatial["bbox_norm"] = [min(x0, x1), min(y0, y1),
                                              max(x0, x1), max(y0, y1)]
            if e.kind == "text" and (e.text_content or "").strip():
                # 字号是**内容**属性，importance 是**排序**属性。两者以前挤在一个字段里，
                # 于是「为了不被预算砍掉而提权」会顺手把字放大——改了画面，不是改优先级。
                # 所以在 normalize 时**物化一次**：此后 importance 只决定谁先被砍。
                e.typography.setdefault("visual_weight",
                                        "large" if e.importance >= 0.95 else "small")
        self.constraints.setdefault("avoid", [])
        self.constraints.setdefault("age_gate", "enforced")
        self.budget.setdefault("target_tokens", None)
        self.budget.setdefault("verbosity", 0.7)
        self.budget.setdefault("priority_policy", "importance_desc")
        if self.timeline:
            # 时间线的规范化归 h3 管：镜头号连续、时间戳单调、时长与镜头不矛盾。
            # 放在 IR.normalize 里而不是适配器里，是因为「这份 IR 合不合法」不该
            # 取决于你问哪个后端。
            from .h3 import normalize_timeline

            self.timeline = normalize_timeline(self.timeline)
        return self

    # ── 便捷访问 ────────────────────────────────────────────
    @property
    def has_layout(self) -> bool:
        """有任何 bbox 数据。反推必然为真——它是证据，不代表要按布局编译。"""
        return any(e.spatial.get("bbox_norm") for e in self.entities)

    def layout_entities(self) -> list["Entity"]:
        return [e for e in self.entities if e.spatial.get("bbox_norm")]

    def needs_layout(self) -> bool:
        """是否该按「放置结构」编译，而不是按散文。

        官方原文：'A detailed image does not automatically need Layout.
        An ordinary photograph never does.' 所以判据不是「有没有 bbox」，
        而是「框里有没有相对位置要表达」：

        - 有文字框 → 是（文字位置必须精确）
        - 两个以上**未盖满画布**的框 → 是（有相对关系）
        - 一个盖满画布的框 → 否（那就是「全图即主体」，布局是废话）
        """
        if (self.constraints or {}).get("force_layout"):
            return True
        boxed = self.layout_entities()
        if not boxed:
            return False
        if any(e.kind == "text" for e in boxed):
            return True
        placed = [e for e in boxed if _area(e.spatial["bbox_norm"]) < 0.80]
        return len(placed) >= 2

    @property
    def text_entities(self) -> list[Entity]:
        return [e for e in self.entities if e.kind == "text" and e.text_content]

    def entity(self, eid: str) -> Entity | None:
        return next((e for e in self.entities if e.id == eid), None)

    def aspect(self) -> tuple[int, int]:
        try:
            w, h = self.canvas.get("aspect_ratio", "1:1").split(":")
            return int(w), int(h)
        except Exception:
            return 1, 1

    def pixel_size(self, megapixels: float = 1.0) -> tuple[int, int]:
        """按宽高比与目标像素量算 16 的倍数边长。"""
        ar_w, ar_h = self.aspect()
        total = megapixels * 1024 * 1024
        h = (total * ar_h / ar_w) ** 0.5
        w = h * ar_w / ar_h
        return (max(256, int(round(w / 16)) * 16), max(256, int(round(h / 16)) * 16))
