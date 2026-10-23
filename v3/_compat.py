# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""V1 → V3 适配层：本包**唯一** import `comfy_api` 的地方。

为什么不在 `nodes_v3.py` 手写 `io.Schema`：V3 要求 input 的 id 名、顺序、默认值、
min/max/step、Combo 选项**全序**与 V1 逐字段一致——老工作流的 `widgets_values`
是按位置存的，手抄一百多个字段必然错一次，而且错得静默（参数串位）。
所以让 `nodes.py` 的 `INPUT_TYPES()` 当唯一真相源，V3 外壳由这里生成。
"""

from __future__ import annotations

from comfy_api.latest import ComfyExtension, io

from .. import nodes as V1

# V1 类型字符串 → V3 类型类。
# ⚠ 漏一个不会报错，会**静默降级**成 io.Custom，画布上就接不了标准节点的输出。
_TYPES = {
    "INT": io.Int,
    "FLOAT": io.Float,
    "STRING": io.String,
    "BOOLEAN": io.Boolean,
    "IMAGE": io.Image,
    "LATENT": io.Latent,
    "MASK": io.Mask,
    "MODEL": io.Model,
    "CLIP": io.Clip,
    "VAE": io.Vae,
    "CONDITIONING": io.Conditioning,
    "SIGMAS": io.Sigmas,
}

# V1 选项键 → V3 构造参数名（值语义相同）。
_OPTS = {
    "default": "default",
    "min": "min",
    "max": "max",
    "step": "step",
    "tooltip": "tooltip",
    "multiline": "multiline",
    "placeholder": "placeholder",
    "forceInput": "force_input",
    "advanced": "advanced",
    "dynamicPrompts": "dynamic_prompts",
}

_DISPLAY = V1.NODE_DISPLAY_NAME_MAPPINGS
_CATEGORY = V1.CATEGORY


def _one_input(name, spec, optional):
    if isinstance(spec, tuple):
        t, opts = spec[0], (spec[1] if len(spec) > 1 else {})
    else:
        t, opts = spec, {}
    kw = {_OPTS[k]: v for k, v in (opts or {}).items() if k in _OPTS}
    if optional:
        kw["optional"] = True
    # 🔴 双出口的槽位一致性陷阱：V1 下前端**自动**给名为 seed/noise_seed 的 INT 加
    # "生成后控制"下拉（fixed/randomize…），V3 下不声明就没有。于是同一个工作流
    # 文件在两条出口上 widgets_values 会**差一个槽**，后续参数整体错位且不报错。
    # 这里补上，让 V3 与 V1 的 widget 序列逐位相同。
    if str(t).upper() == "INT" and name in ("seed", "noise_seed"):
        kw.setdefault("control_after_generate", True)
    if isinstance(t, (list, tuple)):           # V1 的组合列表 → Combo（全序保留）
        return io.Combo.Input(name, options=list(t), **kw)
    cls = _TYPES.get(str(t).upper())
    if cls is None:                            # 未知类型兜底：宁可自定义，也不静默丢
        return io.Custom(str(t)).Input(name, **kw)
    return cls.Input(name, **kw)


def v1_inputs(input_types):
    """`INPUT_TYPES()` → V3 inputs 列表。保序：required 全在前，optional 随后。"""
    out = []
    for section, optional in (("required", False), ("optional", True)):
        for name, spec in (input_types.get(section) or {}).items():
            out.append(_one_input(name, spec, optional))
    return out


def v1_outputs(return_types, return_names):
    out = []
    for i, t in enumerate(return_types):
        name = return_names[i] if i < len(return_names) else str(t).lower()
        cls = _TYPES.get(str(t).upper())
        if cls is None:
            out.append(io.Custom(str(t)).Output(display_name=name))
        else:
            out.append(cls.Output(display_name=name))
    return out


def schema_from_v1(v1_cls) -> io.Schema:
    name = v1_cls.__name__
    return io.Schema(
        node_id=name,
        display_name=_DISPLAY.get(name, name),
        category=_CATEGORY,
        description=getattr(v1_cls, "DESCRIPTION", "") or "",
        inputs=v1_inputs(v1_cls.INPUT_TYPES()),
        outputs=v1_outputs(v1_cls.RETURN_TYPES, v1_cls.RETURN_NAMES),
        # 搜索别名：用户在节点搜索框里打 "故事板"/"storyboard" 才找得到本包
        search_aliases=list(getattr(v1_cls, "SEARCH_ALIASES", []) or []),
    )


def node_output(raw):
    if raw is None:
        return io.NodeOutput()
    res = raw if isinstance(raw, tuple) else (raw,)
    return io.NodeOutput(*res)


def call_v1(class_name, kwargs):
    cls = getattr(V1, class_name)
    return node_output(getattr(cls(), cls.FUNCTION)(**kwargs))


__all__ = ["ComfyExtension", "io", "schema_from_v1", "call_v1", "node_output"]
