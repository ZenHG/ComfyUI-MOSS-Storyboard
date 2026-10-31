# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""极简 YAML 子集解析器：只为「没装 PyYAML 也能跑」。

为什么不干脆依赖 PyYAML：这是个开源工具，用户可能在一个装不上包的环境里
（内网、离线、pip 索引不全、或者只是不想为一个配置文件引入依赖）。
但也不该为此写一个「差不多能用」的 YAML——猜错会静默出错，比报错糟得多。

纪律：**只认明确支持的写法，遇到不认识的语法立刻抛 MiniYamlError**，
绝不返回一个看起来对的错结果。

支持：
  块映射 `k: v` / 块序列 `- item` / 单行流式 `{a: 1, b: [2, 3]}` /
  "双引号"与'单引号' / 整数浮点布尔 null / 行尾 `# 注释`
不支持（遇到就抛）：
  锚点 & * / 多行块标量 | > / 多文档 / 标签 !! / tab 缩进

可信度来自测试：`tests` 里拿 PyYAML 做对照，逐字段比对真实的 model-registry.yaml。
"""

from __future__ import annotations

import re

__all__ = ["MiniYamlError", "safe_load"]

_INT = re.compile(r"[-+]?\d+")
_FLOAT = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
_ESC = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r",
        "0": "\0", "b": "\b", "f": "\f", " ": " "}


class MiniYamlError(ValueError):
    """不支持的语法。宁可炸，也不要静默解析成别的东西。"""


# ── 预处理 ────────────────────────────────────────────────────

def _lines(text: str) -> list[tuple[int, str, int]]:
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        stripped = raw.rstrip()
        if not stripped.strip():
            continue
        lead = stripped[:len(stripped) - len(stripped.lstrip())]
        if "\t" in lead:
            raise MiniYamlError("第%d行：不允许 tab 缩进（YAML 规范如此）" % n)
        body = stripped.lstrip()
        if body.startswith("#"):
            continue
        if body in ("---", "..."):
            continue
        out.append((len(lead), body, n))
    return out


def _strip_comment(s: str) -> str:
    """去掉行尾注释。引号内的 # 不算注释。"""
    inq = None
    for j, ch in enumerate(s):
        if inq:
            if ch == inq:
                inq = None
            continue
        if ch in "\"'":
            inq = ch
        elif ch == "#" and (j == 0 or s[j - 1] in " \t"):
            return s[:j].rstrip()
    return s.rstrip()


# ── 标量 ──────────────────────────────────────────────────────

def _double_quoted(s: str, ln: int) -> tuple[str, str]:
    buf = []
    i = 1
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            if i + 1 >= len(s):
                raise MiniYamlError("第%d行：转义符后没有字符" % ln)
            nxt = s[i + 1]
            if nxt == "u":
                hexs = s[i + 2:i + 6]
                if len(hexs) < 4:
                    raise MiniYamlError("第%d行：\\u 转义不完整" % ln)
                buf.append(chr(int(hexs, 16)))
                i += 6
                continue
            if nxt == "x":
                hexs = s[i + 2:i + 4]
                if len(hexs) < 2:
                    raise MiniYamlError("第%d行：\\x 转义不完整" % ln)
                buf.append(chr(int(hexs, 16)))
                i += 4
                continue
            if nxt not in _ESC:
                raise MiniYamlError("第%d行：不认识的转义 \\%s" % (ln, nxt))
            buf.append(_ESC[nxt])
            i += 2
            continue
        if ch == '"':
            return "".join(buf), s[i + 1:]
        buf.append(ch)
        i += 1
    raise MiniYamlError("第%d行：双引号没闭合" % ln)


def _single_quoted(s: str, ln: int) -> tuple[str, str]:
    buf = []
    i = 1
    while i < len(s):
        if s[i] == "'":
            if s[i + 1:i + 2] == "'":       # '' 表示一个 '
                buf.append("'")
                i += 2
                continue
            return "".join(buf), s[i + 1:]
        buf.append(s[i])
        i += 1
    raise MiniYamlError("第%d行：单引号没闭合" % ln)


def _scalar(s: str, ln: int):
    s = s.strip()
    if not s:
        return None
    if s[0] == '"':
        val, rest = _double_quoted(s, ln)
        _no_tail(rest, ln)
        return val
    if s[0] == "'":
        val, rest = _single_quoted(s, ln)
        _no_tail(rest, ln)
        return val
    s = _strip_comment(s)
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    if s in ("true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"):
        return True
    if s in ("false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"):
        return False
    if _INT.fullmatch(s):
        return int(s)
    if _FLOAT.fullmatch(s):
        return float(s)
    if s[0] in "&*!|>":
        raise MiniYamlError("第%d行：不支持锚点/标签/块标量（%s）" % (ln, s[0]))
    return s


def _no_tail(rest: str, ln: int) -> None:
    rest = rest.strip()
    if rest and not rest.startswith("#"):
        raise MiniYamlError("第%d行：引号字符串后面还有内容：%r" % (ln, rest))


# ── 流式 ──────────────────────────────────────────────────────

def _flow_inner(s: str, ln: int, op: str, cl: str) -> tuple[str, str]:
    """取出最外层 op..cl 之间的内容，返回 (inner, 剩余)。支持嵌套与引号。"""
    if s[0] != op:
        raise MiniYamlError("第%d行：期望 %s 开头" % (ln, op))
    depth = 0
    inq = None
    i = 0
    while i < len(s):
        ch = s[i]
        if inq:
            if ch == "\\" and inq == '"':
                i += 2
                continue
            if ch == inq:
                inq = None
        elif ch in "\"'":
            inq = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                if ch != cl:
                    raise MiniYamlError("第%d行：括号不配对（%s 对上了 %s）" % (ln, op, ch))
                return s[1:i], s[i + 1:]
        i += 1
    raise MiniYamlError("第%d行：%s 没有闭合" % (ln, op))


def _split_top(s: str, ln: int, sep: str = ",") -> list[str]:
    out, buf = [], []
    depth = 0
    inq = None
    i = 0
    while i < len(s):
        ch = s[i]
        if inq:
            buf.append(ch)
            if ch == "\\" and inq == '"' and i + 1 < len(s):
                buf.append(s[i + 1])
                i += 2
                continue
            if ch == inq:
                inq = None
            i += 1
            continue
        if ch in "\"'":
            inq = ch
            buf.append(ch)
            i += 1
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [x for x in out if x.strip()]


def _split_key(s: str, ln: int) -> tuple[str, str]:
    inq = None
    depth = 0
    for j, ch in enumerate(s):
        if inq:
            if ch == inq:
                inq = None
            continue
        if ch in "\"'":
            inq = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            if s[j + 1:j + 2] in ("", " ", "\t"):
                key = s[:j].strip()
                if key[:1] in ("\"", "'"):
                    key = _scalar(key, ln)
                if not key:
                    raise MiniYamlError("第%d行：key 为空" % ln)
                return key, s[j + 1:]
    raise MiniYamlError("第%d行：这行既不是 key: value，也不是序列项：%r" % (ln, s.strip()))


def _value(s: str, ln: int):
    s = s.strip()
    if s.startswith("{"):
        inner, rest = _flow_inner(s, ln, "{", "}")
        _no_tail(rest, ln)
        out = {}
        for part in _split_top(inner, ln):
            k, r = _split_key(part, ln)
            out[k] = _value(r, ln) if r.strip() else None
        return out
    if s.startswith("["):
        inner, rest = _flow_inner(s, ln, "[", "]")
        _no_tail(rest, ln)
        return [_value(p, ln) for p in _split_top(inner, ln)]
    return _scalar(s, ln)


# ── 块结构 ────────────────────────────────────────────────────

def _is_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _map(lines, i: int, indent: int):
    out: dict = {}
    while i < len(lines):
        ind, content, ln = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise MiniYamlError("第%d行：缩进比对不齐（%d > %d）" % (ln, ind, indent))
        if _is_item(content):
            break                                   # 属于上层序列
        key, rest = _split_key(content, ln)
        i += 1
        if rest.strip():
            out[key] = _value(rest, ln)
            continue
        if i < len(lines) and lines[i][0] > indent:
            out[key], i = _node(lines, i, lines[i][0])
        elif i < len(lines) and lines[i][0] == indent and _is_item(lines[i][1]):
            out[key], i = _seq(lines, i, indent)
        else:
            out[key] = None
    return out, i


def _seq(lines, i: int, indent: int):
    out: list = []
    while i < len(lines):
        ind, content, ln = lines[i]
        if ind != indent or not _is_item(content):
            break
        rest = content[1:].lstrip()
        i += 1
        if not rest:
            if i < len(lines) and lines[i][0] > indent:
                val, i = _node(lines, i, lines[i][0])
            else:
                val = None
            out.append(val)
            continue
        if rest[0] not in "[{\"'" and _pair_like(rest):
            # `- key: value`：本行是映射的第一对，后续键在 "- " 之后的位置对齐
            key, r = _split_key(rest, ln)
            if not r.strip():
                raise MiniYamlError("第%d行：序列项里的 key 没有值，请改用嵌套映射" % ln)
            item = {key: _value(r, ln)}
            item_indent = indent + (len(content) - len(rest))
            if i < len(lines) and lines[i][0] == item_indent:
                more, i = _map(lines, i, item_indent)
                item.update(more)
            out.append(item)
            continue
        out.append(_value(rest, ln))
    return out, i


def _pair_like(s: str) -> bool:
    try:
        _split_key(s, 0)
        return True
    except MiniYamlError:
        return False


def _node(lines, i: int, indent: int):
    if lines[i][0] != indent:
        raise MiniYamlError("第%d行：缩进异常" % lines[i][2])
    if _is_item(lines[i][1]):
        return _seq(lines, i, indent)
    return _map(lines, i, indent)


def safe_load(text: str):
    """解析 YAML 子集。空文档返回 None，与 PyYAML 一致。"""
    lines = _lines(text)
    if not lines:
        return None
    val, i = _node(lines, 0, lines[0][0])
    if i < len(lines):
        raise MiniYamlError("第%d行：结构没有收敛（缩进突变？）" % lines[i][2])
    return val


def load_file(path) -> tuple[object, str]:
    """读一个 YAML 文件，返回 (数据, 用的哪个解析器)。

    有 PyYAML 就用它（完整语义，别人的 YAML 也能读）；没有就用子集解析器。
    子集解析器只保证读得动**我们自己的** registry 与 IR，别人的花式 YAML
    会抛 MiniYamlError —— 那时候再装 PyYAML，而不是让它静默读错。
    """
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return safe_load(text), "builtin"
    return yaml.safe_load(text), "pyyaml"
