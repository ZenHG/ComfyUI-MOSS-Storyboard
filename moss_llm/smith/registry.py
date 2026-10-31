# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""模型能力矩阵加载器。registry 是唯一事实源，代码不硬编码任何模型参数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 事实源随包一起发（`pip install` 后也在），不是「仓库里才有的旁挂文件」。
#: 想改后端能力：改这个文件，或 `--registry` 指到自己的副本。
_DEFAULT = Path(__file__).resolve().parent / "data" / "model-registry.yaml"


class Registry:
    def __init__(self, data: dict, path: Path | None = None, parser: str = "pyyaml"):
        self.data = data
        self.path = path
        self.parser = parser
        self.targets: dict[str, dict] = data.get("targets") or {}

    @staticmethod
    def load(path: str | Path | None = None) -> "Registry":
        p = Path(path) if path else _DEFAULT
        if p.suffix.lower() == ".json":
            return Registry(json.loads(p.read_text(encoding="utf-8")) or {}, p, "json")
        # 有 PyYAML 就用它；没有就走内置子集解析器（不装任何东西也能启动）
        from .miniyaml import load_file

        data, parser = load_file(p)
        return Registry(data or {}, p, parser)

    def get(self, name: str) -> dict:
        if name not in self.targets:
            raise KeyError(
                "未知目标 %r。已注册：%s" % (name, ", ".join(sorted(self.targets)))
            )
        return self.targets[name]

    def names(self) -> list[str]:
        return sorted(self.targets)

    # ── 便捷读取（带默认值，避免到处 .get 链） ──────────────
    def encoder(self, name: str) -> dict:
        return self.get(name).get("encoder") or {}

    def prompt_policy(self, name: str) -> dict:
        return self.get(name).get("prompt") or {}

    def negative_policy(self, name: str) -> dict:
        return self.get(name).get("negative") or {}

    def layout_policy(self, name: str) -> dict:
        return self.get(name).get("layout") or {}

    def edit_policy(self, name: str) -> dict:
        return self.get(name).get("edit") or {}

    def sampling(self, name: str, profile: str | None = None) -> dict:
        s: dict[str, Any] = dict(self.get(name).get("sampling") or {})
        profiles = s.pop("profiles", None) or {}
        if profile:
            if profile not in profiles:
                raise KeyError(
                    "目标 %s 没有采样档 %r。可选：%s"
                    % (name, profile, ", ".join(sorted(profiles)) or "（无）")
                )
            s.update(profiles[profile])
            s["profile"] = profile
        return s

    def max_tokens(self, name: str) -> int | None:
        v = self.encoder(name).get("max_tokens")
        return int(v) if isinstance(v, int) else None
