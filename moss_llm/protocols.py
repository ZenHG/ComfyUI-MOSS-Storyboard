# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""外部 API 的四种线格式：OpenAI `/chat/completions`、OpenAI `/responses`、
Anthropic `/v1/messages`、Google GenAI `:generateContent`。

业界两条路：统一内部 IR + 每协议适配器（LLM-Rosetta / LiteLLM / agno），或在边界按协议
精确翻译、不做最小公分母（AI Gateway）。本节点只有「系统词 + 用户文本 + 可选图」一种输入、
一种取正文需求，用后者 —— 每种协议各一对 build/parse，不引中间表示。

协议由**端点**判定，不另开参数：用户填的 `api_base` 决定拼哪个端点，端点决定用哪套线格式。
"""

import json
import os
import urllib.parse
import uuid

OPENAI = "openai"
RESPONSES = "responses"
ANTHROPIC = "anthropic"
GEMINI = "gemini"

#: Anthropic 的版本头是必填项；不带它服务端直接 400。
ANTHROPIC_VERSION = "2023-06-01"
#: Gemini 把模型名写进 URL 而不是 body；用户没填模型时用这个官方名。
GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"
#: urllib 默认 UA 会被 Cloudflare 一类网关按脚本签名拦掉（实测 403 error 1010）。
USER_AGENT = "Mozilla/5.0 (compatible; ComfyUI-MOSS-Storyboard)"
#: opencode 的 Go 网关要求每段对话带一个稳定的 `x-opencode-session`：缺了直接 400
#: `MissingSessionID`（实测），它同时用于路由与 prompt 缓存。进程内一个 id 就够稳定。
SESSION_ID = uuid.uuid4().hex


def _path(url):
    return urllib.parse.urlsplit(url or "").path.rstrip("/").lower()


def join(url, tail):
    """给 URL 追加路径段并保留 query/fragment。

    Azure 一类端点把 `?api-version=` 放在 query 上，直接 `base + "/chat/completions"`
    会把它顶成 `...?api-version=2024/chat/completions`，请求直接失效。
    """
    bits = urllib.parse.urlsplit(url or "")
    return urllib.parse.urlunsplit((bits.scheme, bits.netloc,
                                    bits.path.rstrip("/") + tail, bits.query, ""))


def _is_gemini(url):
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    p = _path(url)
    return "generativelanguage" in host or ":generatecontent" in p or p.endswith("/v1beta")


def detect(endpoint):
    p = _path(endpoint)
    if p.endswith("/messages") or "/anthropic/" in p + "/":
        return ANTHROPIC
    if _is_gemini(endpoint):
        return GEMINI
    # `/responses` 与 `/chat/completions` 同一家也可能并存，但 body/回包结构不同，按端点分开。
    if p.endswith("/responses"):
        return RESPONSES
    return OPENAI


def endpoint(base, model=""):
    """`api_base` → 真正要 POST 的端点。已写全的 URL 原样保留（不重复拼）。"""
    base = (base or "").strip().rstrip("/")
    path = _path(base)
    if path.endswith(("/chat/completions", "/messages", "/responses")) or ":generatecontent" in path:
        return base
    # `/anthropic` 是 Anthropic 兼容入口：它下面挂的是 /v1/messages，不是 chat/completions。
    if "/anthropic" in path:
        return join(base, "/messages" if path.endswith("/v1") else "/v1/messages")
    if _is_gemini(base):
        return "%s/models/%s:generateContent" % (base, (model or "").strip() or GEMINI_DEFAULT_MODEL)
    return join(base, "/chat/completions")


def extra_headers():
    """`MOSS_LLM_EXTRA_HEADERS='{"x-foo":"bar"}'`：网关要的自定义头。

    这类要求穷举不完（会话 id、租户、路由标签…），又不该进工作流/存档 —— 只从环境变量来。
    解析失败就当没设：多一个请求头不该让整个节点报错，但也不静默改写成别的东西。
    """
    raw = (os.environ.get("MOSS_LLM_EXTRA_HEADERS") or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in obj.items()} if isinstance(obj, dict) else {}


def headers(proto, key, url=""):
    # 明确不要压缩：网关强制 gzip 时 body 是二进制，json 解析会炸。
    head = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
            "Accept-Encoding": "identity"}
    # 服务商要求的头与鉴权无关，必须在「没 Key 就返回」之前加（本地/无鉴权网关也要）。
    if (urllib.parse.urlsplit(url or "").hostname or "").lower().endswith("opencode.ai"):
        head["x-opencode-session"] = SESSION_ID
    head.update(extra_headers())
    if not key:
        return head
    if (urllib.parse.urlsplit(url or "").hostname or "").lower().endswith("openai.azure.com"):
        # Azure 不认 Bearer，只认 api-key。
        head["api-key"] = key
    elif proto == ANTHROPIC:
        head["x-api-key"] = key
        head["anthropic-version"] = ANTHROPIC_VERSION
    elif proto == GEMINI:
        head["x-goog-api-key"] = key
    else:
        head["Authorization"] = "Bearer " + key
    return head


def _image_block(proto, data_url):
    """`data:image/jpeg;base64,...` → 各自的图块。"""
    head, _, b64 = (data_url or "").partition(",")
    media = head.split(";")[0].replace("data:", "") or "image/jpeg"
    if proto == ANTHROPIC:
        return {"type": "image",
                "source": {"type": "base64", "media_type": media, "data": b64}}
    if proto == GEMINI:
        return {"inline_data": {"mime_type": media, "data": b64}}
    if proto == RESPONSES:
        return {"type": "input_image", "image_url": data_url}
    return {"type": "image_url", "image_url": {"url": data_url}}


def body(proto, model, system, user, image_url, temperature, max_tokens, seed):
    """同一份语义 → 三套 body。system 在 OpenAI 并进正文、在另两家是独立字段。"""
    if proto == ANTHROPIC:
        blocks = [{"type": "text", "text": user or ""}]
        if image_url:
            blocks.append(_image_block(proto, image_url))
        return {"model": model, "system": system.strip(),
                "messages": [{"role": "user", "content": blocks}],
                "max_tokens": int(max_tokens), "temperature": float(temperature),
                "stream": False}
    if proto == GEMINI:
        parts = [{"text": user or ""}]
        if image_url:
            parts.append(_image_block(proto, image_url))
        return {"systemInstruction": {"parts": [{"text": system.strip()}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": float(temperature),
                                     "maxOutputTokens": int(max_tokens)}}
    if proto == RESPONSES:
        content = [{"type": "input_text", "text": user or ""}]
        if image_url:
            content.append(_image_block(proto, image_url))
        # Responses 用 instructions + input，没有 messages；max_tokens 叫 max_output_tokens。
        return {"model": model, "instructions": system.strip(),
                "input": [{"role": "user", "content": content}],
                "temperature": float(temperature),
                "max_output_tokens": int(max_tokens), "store": False}
    blocks = [{"type": "text", "text": (system + "\n\n" + (user or "")).strip()}]
    if image_url:
        blocks.append(_image_block(proto, image_url))
    return {"model": model, "messages": [{"role": "user", "content": blocks}],
            "temperature": float(temperature), "max_tokens": int(max_tokens),
            "seed": int(seed), "stream": False}


def read(proto, obj):
    """回包 → (正文, finish, usage)。三套结构在这里收口，调用方只看见这三个值。"""
    if proto == ANTHROPIC:
        blocks = obj.get("content") or []
        raw = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
        u = obj.get("usage") or {}
        return raw, obj.get("stop_reason"), {
            "prompt_tokens": u.get("input_tokens"),
            "completion_tokens": u.get("output_tokens"),
            "total_tokens": (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)}
    if proto == GEMINI:
        cand = (obj.get("candidates") or [None])[0]
        if not cand:
            # 安全拦截时没有候选，只有 promptFeedback：不静默返回空串。
            raise RuntimeError("Gemini 没有返回候选：%s"
                               % str(obj.get("promptFeedback") or obj.get("error") or obj)[:200])
        parts = (cand.get("content") or {}).get("parts") or []
        u = obj.get("usageMetadata") or {}
        return ("".join(p.get("text") or "" for p in parts), cand.get("finishReason"),
                {"prompt_tokens": u.get("promptTokenCount"),
                 "completion_tokens": u.get("candidatesTokenCount"),
                 "total_tokens": u.get("totalTokenCount")})
    if proto == RESPONSES:
        texts = []
        for item in obj.get("output") or []:
            if item.get("type") == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        texts.append(c.get("text") or "")
        u = obj.get("usage") or {}
        reason = (obj.get("incomplete_details") or {}).get("reason")
        return ("".join(texts), reason or obj.get("status"),
                {"prompt_tokens": u.get("input_tokens"),
                 "completion_tokens": u.get("output_tokens"),
                 "total_tokens": u.get("total_tokens")})
    choice = (obj.get("choices") or [{}])[0]
    return ((choice.get("message") or {}).get("content"),
            choice.get("finish_reason"), obj.get("usage") or {})
