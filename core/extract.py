# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""从自由文本里提取第一个合法 JSON 值（planner 输出常带说明文字或代码块）。

做法：扫 `{` / `[`，按字符串与转义规则找配对的闭括号，再交给 `json.loads`。
外层不合法时继续从下一个开括号试，直到命中或候选用尽。

**不做猜测**：不补逗号、不修引号、不把单引号当 JSON；解析失败就报错。
上限（防恶意/超长输入拖死调用方）：输入 ≤1,000,000 字符、候选 ≤32 个、嵌套 ≤64 层。
"""

import json

MAX_INPUT_CHARS = 1_000_000
MAX_CANDIDATES = 32
MAX_DEPTH = 64
#: 所有候选加起来最多扫这么多字符：防"大量未闭合括号"把调用方拖住（0.9MB×32 ≈ 3.4s）。
MAX_SCAN_CHARS = 4_000_000

_OPEN = {"{": "}", "[": "]"}


def _skip_string(text, i):
    """i 指向开引号；返回闭引号之后的下标（找不到就返回 len）。"""
    i += 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            return i + 1
        i += 1
    return len(text)


def _match_end(text, start):
    """start 指向开括号；返回 (配对闭括号之后的下标, 扫描字符数)，找不到下标为 None。"""
    stack = []
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i = _skip_string(text, i)
            continue
        if ch in _OPEN:
            stack.append(_OPEN[ch])
            if len(stack) > MAX_DEPTH:
                raise ValueError("JSON 嵌套超过 %d 层，拒绝解析。" % MAX_DEPTH)
            i += 1
            continue
        if ch in "}]":
            if not stack or ch != stack[-1]:
                return None, i - start + 1
            stack.pop()
            i += 1
            if not stack:
                return i, i - start
            continue
        i += 1
    return None, len(text) - start


def extract_json(text):
    """返回文本里第一个能 `json.loads` 成功的对象/数组。找不到就抛 ValueError。"""
    if not isinstance(text, str):
        raise ValueError("输入必须是字符串（收到 %s）。" % type(text).__name__)
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("输入有 %d 个字符，超过上限 %d。" % (len(text), MAX_INPUT_CHARS))
    tried = 0
    budget = MAX_SCAN_CHARS
    for start, ch in enumerate(text):
        if ch not in _OPEN:
            continue
        tried += 1
        if tried > MAX_CANDIDATES:
            break
        end, scanned = _match_end(text, start)
        budget -= scanned
        if budget <= 0:
            raise ValueError("提取扫描超过 %d 字符上限；文本里的括号结构太多或未闭合。"
                             % MAX_SCAN_CHARS)
        if end is None:
            continue
        try:
            return json.loads(text[start:end])
        except ValueError:
            continue
    raise ValueError("没有找到合法的 JSON 对象/数组（试了 %d 个开括号）。" % tried)
