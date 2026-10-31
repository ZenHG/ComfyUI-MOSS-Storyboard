# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""对外门面：一段 IR 文本 → 某后端的提示词 + 体检报告。**零模型、零网络。**

存在的理由：`moss_llm/nodes.py`（ComfyUI 节点）要用编译器，但它不该知道
`IR` / `Registry` / `Adapter` / `lint` / `invariants` 这五个东西怎么接。
门面把这条链路收成一个函数，节点只负责搬运字符串与排版报告。
CLI 那条路仍直接用底层模块（它需要 profile / mode / 长度档这些旋钮）。

    from moss_llm.smith.api import compile_ir
    r = compile_ir('{"task":"t2i","entities":[...]}', "moss-image")
    r["positive"], r["issues"], r["invariants"]
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import invariants as INV
from .adapters import get_adapter
from .ir import IR
from .lint import check
from .miniyaml import safe_load
from .registry import Registry
from .tokens import HeuristicCounter

#: 剥 Markdown 代码围栏。用户会从聊天窗口直接粘贴。
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class CompileError(ValueError):
    """IR 读不进来。消息要能直接展示给用户——说清「给的是什么、期望什么」。"""


def parse_ir(source) -> IR:
    """接受四种写法：IR 文件路径、JSON 文本、YAML 文本、带围栏的上面几种。

    优先级是「先看能不能当路径，再看内容」。**不猜**：四条都不成立就报错并说清
    期望的形状，不悄悄给个空 IR 让它编译出一段废话。
    """
    s = (source or "").strip()
    s = _FENCE.sub("", s).strip()
    if not s:
        raise CompileError(
            "IR 是空的。三种给法任选一种：\n"
            "  ① 粘贴 IR JSON（以 { 开头，含 entities 数组）\n"
            "  ② 粘贴 IR YAML（以 task: 开头）\n"
            "  ③ 填一个 .json / .yaml 文件的完整路径")
    if "\n" not in s:
        p = Path(s).expanduser()
        if p.suffix.lower() in (".json", ".yaml", ".yml") and p.is_file():
            return IR.load(p)
        if p.suffix.lower() in (".json", ".yaml", ".yml") and not p.is_file():
            raise CompileError("找不到 IR 文件：%s" % p)
    if s.lstrip().startswith("{"):
        try:
            data = json.loads(s)
        except ValueError as e:
            raise CompileError("IR 不是合法 JSON：%s" % e) from e
        return _from_obj(data)
    if re.match(r"^[A-Za-z_][\w-]*\s*:", s):
        try:
            data = safe_load(s)
        except Exception as e:                       # miniyaml 自己的异常类型不固定
            raise CompileError("IR 不是合法 YAML：%s" % e) from e
        return _from_obj(data)
    raise CompileError(
        "认不出这段 IR：既不是文件路径，也不以 { 或 `key:` 开头。"
        "开头 40 个字是：%r" % s[:40])


def _from_obj(data) -> IR:
    if not isinstance(data, dict):
        raise CompileError("IR 顶层必须是对象（键值对），给的是 %s。" % type(data).__name__)
    if "entities" not in data:
        raise CompileError(
            "IR 缺 `entities` 数组。最小可编译的 IR 是：\n"
            '  {"task":"t2i","canvas":{"aspect_ratio":"1:1"},'
            '"entities":[{"id":"e1","kind":"object","label":"a red cup","importance":1}]}')
    try:
        return IR.from_dict(data)
    except Exception as e:
        raise CompileError("IR 字段不合法：%s: %s" % (type(e).__name__, e)) from e


def compile_ir(source, target="moss-image", registry=None, counter=None,
               run_invariants=True, profile=None):
    """编译。返回 dict（不是对象）——调用方要直接塞进 report 或 JSON。

    失败一律 raise `CompileError`（能展示给用户的句子），不返回半成品。
    """
    ir = parse_ir(source)
    reg = registry or Registry.load()
    name = target or "moss-image"
    if name not in reg.names():
        raise CompileError("没有 %r 这个后端。可选：%s"
                           % (name, "、".join(reg.names())))
    ad = get_adapter(name, reg, counter=counter or HeuristicCounter())
    try:
        out = ad.realize(ir, profile=profile, mode="deterministic")
    except ValueError as e:
        # 能力不匹配（把 edit 的 IR 丢给没有编辑通道的后端）是**用户输入问题**，
        # 不是编译器崩了。统一成 CompileError，调用方才能只捕一种异常就给出人话。
        raise CompileError("这个后端渲染不了这份 IR：%s" % e) from e
    caps = reg.get(name)
    issues = check(ir, name, caps, out.as_dict())

    inv, inv_note = [], ""
    if run_invariants:
        try:
            inv = INV.run(ad, ir)
        except ValueError as e:      # 关系自身的前置条件不满足（保留的兜底）
            inv_note = "不适用：%s" % e
    ok, line = INV.summarize(inv) if inv else (True, inv_note or "未跑")

    return {
        "target": name,
        "positive": out.positive,
        "negative": out.negative,
        "layout": out.layout,
        "params": out.params,
        "warnings": list(out.warnings),
        "dropped": list(out.dropped),
        "token_count": out.token_count,
        "token_limit": out.token_limit,
        "mode": out.mode,
        "issues": [{"level": i.level, "code": i.code, "message": i.message,
                    "fix": i.fix} for i in issues],
        "invariants": [r.to_dict() for r in inv],
        "invariants_ok": ok,
        "invariants_line": line,
        "ir": {"task": ir.task, "entities": len(ir.entities),
               "timeline": len(getattr(ir, "timeline", None) or []),
               "canvas": (ir.canvas or {}).get("aspect_ratio", "")},
    }


def error_count(result) -> int:
    """report 里 error 级问题的条数。节点据此决定要不要在报告开头点名。"""
    return sum(1 for i in (result or {}).get("issues") or [] if i["level"] == "error")
