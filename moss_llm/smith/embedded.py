# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""零模型洗图：读图片里内嵌的生成参数。

为什么单开一个模块，而不是塞进 reverse：
  生成图（A1111 / Forge / SD.Next / ComfyUI / InvokeAI / NovelAI / Fooocus）
  经常把当初的 prompt 原样写进 PNG 文本块或 JPEG EXIF。读到它就不需要 VLM、
  不需要 GPU，而且是**逐字精确**的——比任何反推都准。
  读不到也不影响反推：两条路互不依赖，只是多给一次机会。

只用标准库：PNG 的 tEXt/zTXt/iTXt 就是「四个长度字段 + 载荷」，
不值得为它引入 Pillow。JPEG 只解 APP1 里的 EXIF UserComment（最常见的那一种）。

支持的方言（按命中顺序）：
  parameters        → A1111 / Forge / SD.Next / Fooocus（同一种行格式）
  prompt(JSON)      → ComfyUI（API 格式图，顺着 positive/negative 连线回溯）
  invokeai_metadata → InvokeAI
  Comment(JSON)     → NovelAI（配合 Software: NovelAI 校验）
  EXIF UserComment  → 有些工具把同样的行格式写进 JPEG
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

#: ComfyUI 里承载文本的输入名。只认这些，避免把 "filename_prefix" 之类当 prompt。
_TEXT_INPUTS = ("text", "text_g", "text_l", "prompt", "t5xxl", "clip_l")

#: A1111 参数行永远以 Steps 开头，用它当锚点比「最后一行」稳。
_PARAM_ANCHOR = re.compile(r"(?m)^Steps:")


@dataclass
class Embedded:
    """一张图里读出来的生成参数。字段都是「有就有、没有就空」，不编。"""

    dialect: str = ""                       # a1111|comfyui|invokeai|novelai|unknown
    positive: str = ""
    negative: str = ""
    params: dict = field(default_factory=dict)
    chunks: dict = field(default_factory=dict)   # 原始块名 → 值（截断），便于排查
    notes: list = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """能不能直接当 prompt 用。只有负向不算。"""
        return bool(self.positive.strip())

    def to_dict(self) -> dict:
        return {"dialect": self.dialect, "positive": self.positive,
                "negative": self.negative, "params": self.params,
                "chunks": self.chunks, "notes": self.notes}

    def summary(self) -> str:
        bits = [self.dialect or "unknown"]
        for k in ("Model", "Sampler", "Steps", "CFG scale", "Seed", "Size",
                  "scheduler", "steps", "cfg_scale", "seed", "model"):
            v = self.params.get(k)
            if v not in (None, ""):
                bits.append("%s=%s" % (k, v))
        if self.negative:
            bits.append("负向 %d 字" % len(self.negative))
        return " · ".join(bits)


# ── 通用小工具 ────────────────────────────────────────────────

def _split_quoted(s: str, sep: str = ", ") -> list[str]:
    """按 sep 切分，但引号内不切。A1111 用 " 包住含逗号的值（如 Lora hashes）。"""
    out: list[str] = []
    buf: list[str] = []
    inq = esc = False
    i = 0
    while i < len(s):
        ch = s[i]
        if esc:
            buf.append(ch)
            esc = False
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            esc = True
            i += 1
            continue
        if ch == '"':
            inq = not inq
            buf.append(ch)
            i += 1
            continue
        if not inq and s.startswith(sep, i):
            out.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [x for x in out if x.strip()]


def _kv_line(line: str) -> dict:
    out = {}
    for part in _split_quoted(line):
        k, sep, v = part.partition(":")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        if k and v:
            out[k] = v[1:-1] if len(v) > 1 and v[0] == v[-1] == '"' else v
    return out


def _preview(v: str, n: int = 160) -> str:
    v = v.replace("\n", "\\n")
    return v if len(v) <= n else v[:n] + "…(%d 字)" % len(v)


# ── PNG 文本块 ────────────────────────────────────────────────

def png_chunks(data: bytes) -> dict:
    """tEXt / zTXt / iTXt 三兄弟。坏块跳过，不抛——洗图不该因为一个 CRC 就整个失败。"""
    if not data.startswith(_PNG_SIG):
        return {}
    out: dict = {}
    i, n = 8, len(data)
    while i + 8 <= n:
        (length,) = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        if length > n:                      # 长度字段坏了，立刻停
            break
        body = data[i + 8:i + 8 + length]
        i += 12 + length
        if ctype == b"tEXt":
            k, _, v = body.partition(b"\x00")
            out.setdefault(k.decode("latin-1"), v.decode("latin-1", "replace"))
        elif ctype == b"zTXt":
            k, _, rest = body.partition(b"\x00")
            if rest[:1] != b"\x00":
                continue
            try:
                v = zlib.decompress(rest[1:])
            except zlib.error:
                continue
            out.setdefault(k.decode("latin-1"), v.decode("latin-1", "replace"))
        elif ctype == b"iTXt":
            k, _, rest = body.partition(b"\x00")
            if len(rest) < 2:
                continue
            comp = rest[0]
            rest = rest[2:]
            _lang, _, rest = rest.partition(b"\x00")     # 语言标签
            _trans, _, rest = rest.partition(b"\x00")    # 译名
            if comp == 1:
                try:
                    rest = zlib.decompress(rest)
                except zlib.error:
                    continue
            out.setdefault(k.decode("latin-1"), rest.decode("utf-8", "replace"))
        elif ctype == b"IEND":
            break
    return out


# ── JPEG / EXIF ───────────────────────────────────────────────

def _tiff_value(tiff: bytes, want: int) -> bytes | None:
    """从 TIFF 头里取一个标签的原始字节（只走 IFD0，够用）。"""
    if tiff[:2] == b"II":
        e = "<"
    elif tiff[:2] == b"MM":
        e = ">"
    else:
        return None
    if struct.unpack(e + "H", tiff[2:4])[0] != 42:
        return None
    off = struct.unpack(e + "I", tiff[4:8])[0]
    if off + 2 > len(tiff):
        return None
    cnt = struct.unpack(e + "H", tiff[off:off + 2])[0]
    p = off + 2
    for _ in range(cnt):
        if p + 12 > len(tiff):
            return None
        tag, typ, num = struct.unpack(e + "HHI", tiff[p:p + 8])
        raw = tiff[p + 8:p + 12]
        if tag == want:
            size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1,
                    8: 2, 9: 4, 10: 8, 11: 4, 12: 8}.get(typ, 1) * num
            if size <= 4:
                return raw[:size]
            voff = struct.unpack(e + "I", raw)[0]
            return tiff[voff:voff + size]
        p += 12
    return None


def _decode_usercomment(raw: bytes) -> str:
    """EXIF UserComment 头 8 字节是字符集声明。声明经常是错的，所以解码要能退。"""
    head, body = raw[:8], raw[8:]
    if head.startswith(b"UNICODE"):
        for enc in ("utf-16-be", "utf-16-le"):
            try:
                s = body.decode(enc)
            except UnicodeDecodeError:
                continue
            # 编错字节序会得到一堆 U+FFFD 或 \x00 交错的怪串，用可打印率投票
            if s.count("\ufffd") <= len(s) // 20:
                return s.strip("\x00").strip()
        return body.decode("utf-8", "replace").strip("\x00").strip()
    if head.startswith(b"JIS"):
        return body.decode("shift_jis", "replace").strip("\x00").strip()
    # 未声明字符集时不能直接当 UTF-8：Windows 侧有些工具写 UTF-16 却不写声明。
    # 判字节序只看「零字节落在奇数位还是偶数位」——ASCII 文本在 UTF-16 里必然
    # 有一半是 0x00，位置就决定了字节序，比试解码可靠（解错也能出合法汉字）。
    if len(body) >= 4 and len(body) % 2 == 0:
        even = sum(1 for i in range(0, len(body), 2) if body[i] == 0)
        odd = sum(1 for i in range(1, len(body), 2) if body[i] == 0)
        if max(even, odd) >= len(body) // 4:
            enc = "utf-16-be" if even > odd else "utf-16-le"
            try:
                s = body.decode(enc)
            except UnicodeDecodeError:
                pass
            else:
                if s.count("\ufffd") <= len(s) // 20:
                    return s.strip("\x00").strip()
    return body.decode("utf-8", "replace").strip("\x00").strip()


def jpeg_text(data: bytes) -> dict:
    """只认 APP1 里的 Exif UserComment。别的段不解，避免变成半个 EXIF 库。"""
    if data[:2] != b"\xff\xd8":
        return {}
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xD9, 0xDA):           # EOI / SOS：后面是图像数据，没元数据了
            break
        (seg_len,) = struct.unpack(">H", data[i + 2:i + 4])
        seg = data[i + 4:i + 2 + seg_len]
        i += 2 + seg_len
        if marker != 0xE1 or not seg.startswith(b"Exif\x00\x00"):
            continue
        raw = _tiff_value(seg[6:], 0x9286)   # UserComment
        if raw:
            txt = _decode_usercomment(raw)
            if txt:
                return {"EXIF:UserComment": txt}
    return {}


# ── 各方言解析 ────────────────────────────────────────────────

def parse_a1111(text: str) -> Embedded:
    """A1111 行格式：正向 / `Negative prompt:` / 一行 `Steps: ...` 参数。"""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    params: dict = {}
    body = t
    anchors = list(_PARAM_ANCHOR.finditer(t))
    if anchors:
        m = anchors[-1]                       # 取最后一个，防止正向里恰好有 "Steps:"
        params = _kv_line(t[m.start():].strip())
        body = t[:m.start()]
    neg = ""
    idx = body.rfind("Negative prompt:")
    if idx >= 0:
        neg = body[idx + len("Negative prompt:"):].strip()
        body = body[:idx]
    return Embedded(dialect="a1111", positive=body.strip(), negative=neg, params=params)


def _walk(nodes: dict, link, seen: set, texts: list, depth: int = 0) -> None:
    if depth > 64 or not isinstance(link, (list, tuple)) or not link:
        return
    nid = str(link[0])
    if nid in seen:
        return
    seen.add(nid)
    node = nodes.get(nid)
    if not isinstance(node, dict):
        return
    inp = node.get("inputs") or {}
    for k in _TEXT_INPUTS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            texts.append(v)
    for v in inp.values():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            _walk(nodes, v, seen, texts, depth + 1)


def _comfy_side(nodes: dict, side: str) -> str:
    """顺着 positive/negative 连线往上回溯，收集文本节点的字符串。"""
    texts: list = []
    seen: set = set()
    for node in nodes.values():
        inp = node.get("inputs") or {}
        link = inp.get(side)
        if isinstance(link, (list, tuple)) and len(link) == 2:
            _walk(nodes, link, seen, texts)
    return "\n".join(t for t in texts if t.strip())


def parse_comfy(obj) -> Embedded:
    """ComfyUI API 格式：{节点id: {class_type, inputs}}。UI 格式（含 nodes 数组）不追连线。"""
    nodes = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and ("class_type" in v or "inputs" in v):
                nodes[str(k)] = v
    if not nodes:
        raise ValueError("不是 ComfyUI API 图")
    pos = _comfy_side(nodes, "positive")
    neg = _comfy_side(nodes, "negative")
    notes = []
    if not pos:                                # 没有 sampler 连线：退化成「按 id 顺序全收」
        found = []
        for nid in sorted(nodes, key=lambda x: (len(x), x)):
            inp = nodes[nid].get("inputs") or {}
            for k in _TEXT_INPUTS:
                v = inp.get(k)
                if isinstance(v, str) and v.strip():
                    found.append(v)
        pos = "\n".join(found)
        if found:
            notes.append("没找到 positive/negative 连线，正向取全部文本节点（可能含负向）")
    params = {}
    for node in nodes.values():
        inp = node.get("inputs") or {}
        ct = str(node.get("class_type") or "")
        if "Sampler" in ct:
            for k in ("seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler"):
                if k in inp and not isinstance(inp[k], (list, tuple)):
                    params.setdefault(k, inp[k])
        if "CheckpointLoader" in ct and isinstance(inp.get("ckpt_name"), str):
            params.setdefault("model", inp["ckpt_name"])
    return Embedded(dialect="comfyui", positive=pos, negative=neg,
                    params=params, notes=notes)


def parse_invokeai(obj) -> Embedded:
    if not isinstance(obj, dict):
        raise ValueError("不是 InvokeAI 元数据")
    pos = obj.get("positive_prompt") or ""
    neg = obj.get("negative_prompt") or ""
    params = {}
    model = obj.get("model")
    if isinstance(model, dict):
        params["model"] = model.get("model_name") or model.get("name") or ""
    for k in ("seed", "steps", "cfg_scale", "scheduler", "width", "height"):
        if obj.get(k) not in (None, ""):
            params[k] = obj[k]
    return Embedded(dialect="invokeai", positive=str(pos), negative=str(neg),
                    params={k: v for k, v in params.items() if v not in (None, "")})


def parse_novelai(obj) -> Embedded:
    if not isinstance(obj, dict):
        raise ValueError("不是 NovelAI 元数据")
    params = {k: obj[k] for k in ("steps", "scale", "seed", "sampler", "width", "height")
              if obj.get(k) not in (None, "")}
    return Embedded(dialect="novelai", positive=str(obj.get("prompt") or ""),
                    negative=str(obj.get("uc") or ""), params=params)


def _json(chunks: dict, key: str):
    raw = chunks.get(key)
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def from_chunks(chunks: dict) -> Embedded | None:
    """按方言优先级把原始块翻译成 Embedded。认不出就返回 None（不猜）。"""
    if not chunks:
        return None
    preview = {k: _preview(v) for k, v in chunks.items()}
    emb: Embedded | None = None

    if "parameters" in chunks:                      # A1111 系
        emb = parse_a1111(chunks["parameters"])
    elif "invokeai_metadata" in chunks:
        obj = _json(chunks, "invokeai_metadata")
        if obj is not None:
            try:
                emb = parse_invokeai(obj)
            except ValueError:
                emb = None
    elif "prompt" in chunks:
        obj = _json(chunks, "prompt")
        if isinstance(obj, dict):
            try:
                emb = parse_comfy(obj)
            except ValueError:
                emb = None
        elif obj is None and isinstance(chunks.get("prompt"), str):
            # 不是 JSON，才当明文 prompt；是 JSON 但不是图（比如列表）就别硬当文本
            emb = Embedded(dialect="plain", positive=chunks["prompt"].strip())
    elif "Comment" in chunks and "Software" in chunks:
        obj = _json(chunks, "Comment")
        if obj is not None:
            try:
                emb = parse_novelai(obj)
            except ValueError:
                emb = None
    elif "EXIF:UserComment" in chunks:
        txt = chunks["EXIF:UserComment"]
        if _PARAM_ANCHOR.search(txt) or "Negative prompt:" in txt:
            emb = parse_a1111(txt)
            emb.dialect = "a1111(exif)"
        else:
            emb = Embedded(dialect="exif-plain", positive=txt.strip())

    if emb is None:
        return None
    emb.chunks = preview
    if "workflow" in chunks and emb.dialect == "comfyui":
        emb.notes.append("同时存在 workflow（UI 格式），已忽略；只信 API 图")
    return emb


# ── 对外入口 ──────────────────────────────────────────────────

def read(path: str | Path) -> Embedded | None:
    """读一张图的生成元数据。读不到返回 None——这不是错误，多数图就是没有。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("找不到图片：%s" % p)
    data = p.read_bytes()
    if data[:8] == _PNG_SIG:
        chunks = png_chunks(data)
    elif data[:2] == b"\xff\xd8":
        chunks = jpeg_text(data)
    else:
        chunks = {}
    return from_chunks(chunks)


def read_bytes(data: bytes) -> Embedded | None:
    """给不便落盘的调用方（测试、管道）用。"""
    if data[:8] == _PNG_SIG:
        return from_chunks(png_chunks(data))
    if data[:2] == b"\xff\xd8":
        return from_chunks(jpeg_text(data))
    return None


def describe(emb: Embedded | None) -> str:
    """一行人类可读摘要，给 CLI 和 provenance 用。"""
    if emb is None:
        return "无内嵌生成参数"
    return emb.summary()
