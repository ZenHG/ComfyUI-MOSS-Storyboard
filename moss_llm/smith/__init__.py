# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""`moss_llm.smith` —— 语义 IR 与多方言确定性编译层。

## 它解决什么

`MossPromptSmith` 走的是「一次 LLM 调用 → 成品提示词 + report」。那条路快，
但它**只对一种方言负责**，而且每次出词都要重新读一遍图/稿——同一份内容换个后端，
事实是否还在没人能保证。

这一层把「事实」与「措辞」拆开：

    prompt / image ──反推──▶ IR ──确定性编译──▶ 7 个后端原生方言
                              │
                              ├── 不变式（M1–M6）：不需要 ground truth 的自检
                              ├── 往返校验：IR → prompt → 事实差
                              └── H3：时间线是 IR 的一等维度

关键性质：**确定性编译不需要模型，毫秒级，可复现**。模型只用在两个地方——
反推（图→IR）与扩写（IR→更长的话），两者都可以没有。

## 与父包的关系

- 依赖方向**单向**：`smith` 可以 import 父包，父包只在 `task=compile` 时懒加载 `smith`。
  删掉 `moss_llm/smith/` 不影响节点的另外三个任务、不影响核心 7 节点。
- 2026-10-31 之前有五个模块（`reverse` / `expand` / `llm` / `runtime` / `paths`）与父包
  `nodes.py` / `server.py` / `runtime_install.py` **各实现了一遍**，靠 docstring 里一句
  「本版并存，下一版二选一」挂着。现在收敛完了：
  - `llm.py` / `vision.py` → 转发父包（侧车与图像预处理只剩一份）；
  - `runtime.py` → 删除（父包的装包器有镜像链、摘要校验、断点续传、原子替换）；
  - `paths.py` → 只有「模型目录」这一半留下，「可执行文件在哪」转发父包；
  - `reverse.py` / `expand.py` → 保留（它们做的是 IR 的事：取证提示词与可验证扩写，
    与节点的自由出词**不是同一个任务**，去掉任何一个都是能力损失）。
  决策与逐项优劣见 `docs/25-两份实现的合流.md`。

## 用法

    from moss_llm.smith.ir import IR
    from moss_llm.smith.registry import Registry
    from moss_llm.smith.adapters import get_adapter

    ir = IR.load("moss_llm/smith/examples/02-poster-text-layout.json")
    text = get_adapter("moss-image", Registry.load()).realize(ir).positive

    # 一行版（节点用的就是它）：
    from moss_llm.smith.api import compile_ir
    r = compile_ir('{"task":"t2i","entities":[...]}', "flux3")

CLI（调试壳，不进交付路径）：`python -m moss_llm.smith --help`

## 版本

`__version__` 随父包一起发；本层没有独立的发布节奏。
"""
__version__ = "0.6.0"
