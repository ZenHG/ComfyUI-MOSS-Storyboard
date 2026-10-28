# 来源与许可（第三方，非本包产物）

> 本目录是 **上游原文照抄**，未做任何修改；本包的 MIT 许可**不覆盖**这里的内容。
> 引用/再分发请以上游为准。

| 项 | 值 |
|---|---|
| 上游仓库 | <https://github.com/OpenMOSS/MOSS-Image> |
| 上游路径 | `skills/moss-image-prompt-enhancer/` |
| 抓取提交 | `20f559207791`（2026-10-16T03:00:54Z） |
| 抓取日期 | 2026-10-27 |
| 许可 | **Apache-2.0**（全文见本目录 `LICENSE-APACHE-2.0.txt`；上游仓 `LICENSE`） |

## 文件清单（大小 / sha256 前 16 位）

| 文件 | 字节 | sha256 |
|---|---|---|
| `SKILL.md` | 5909 | `72cd39883af0b6c6` |
| `references/layout.md` | 4072 | `e3d74ded6ab402ef` |
| `scripts/compile_prompt.py` | 5779 | `1d75780cf9f22f9e` |
| `scripts/test_compile_prompt.py` | 6172 | `2ef6ec435575bfaa` |
| `agents/openai.yaml` | 257 | `d215006ac6563bd6` |
| `LICENSE-APACHE-2.0.txt` | 11358 | `cfc7749b96f63bd3` |

## 为什么要留一份

`docs/07-consistency.md` §三之二 的「UI 中文、输出英文可见范围」与「Describe visible choices」
原来只是转引，本机没有原文。这里存一份可离线核对的上游原文，用于：

- 核对我们的英文景别子句（`core/editor.py::SIZE_EN`）是否属于 skill 说的 **visible choices**；
- 核对我们的布局 JSON（`core/prompt.py::layout_json_text`）是否与官方 **编译后**形态同源
  （`abs_centralized` / `yxyx`，一行、无 width/height）；
- 记录 skill 明确**不建议**放进画面描述的东西（冗余质量词、网格/边框元语言、流程说明）。
