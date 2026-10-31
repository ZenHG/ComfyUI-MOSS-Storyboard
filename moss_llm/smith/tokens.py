# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""token 计数。

两种实现：
- HeuristicCounter：离线、零依赖。英文 ~4 字符/token，CJK 1 字/token。
- ServerCounter：向 llama-server 的 /tokenize 要**真实**计数。

为什么不内置 transformers：本项目要求本地离线零重依赖，而 4/5 个目标模型
共用 Qwen tokenizer 家族，用同族模型的服务端计数已经足够准。
"""

from __future__ import annotations

import json
import re
import urllib.request

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_WORD = re.compile(r"[A-Za-z0-9']+")
_PUNCT = re.compile(r"[^\w\s]")


class HeuristicCounter:
    """离线估算。误差约 ±15%，只用于「是否明显超预算」的判定。"""

    name = "heuristic"

    def count(self, text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK.findall(text))
        rest = _CJK.sub(" ", text)
        words = len(_WORD.findall(rest))
        punct = len(_PUNCT.findall(rest))
        # 英文词平均拆成 ~1.3 个 token；标点各算 1
        return int(round(cjk + words * 1.3 + punct * 0.6))


class ServerCounter:
    """真实计数：llama.cpp 的 POST /tokenize。"""

    name = "server"

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def count(self, text: str) -> int:
        if not text:
            return 0
        req = urllib.request.Request(
            self.base_url + "/tokenize",
            data=json.dumps({"content": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return len(json.loads(r.read().decode("utf-8")).get("tokens") or [])
        except Exception:
            return HeuristicCounter().count(text)


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))
