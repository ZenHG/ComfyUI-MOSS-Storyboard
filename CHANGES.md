# CHANGES

## 0.1.0 — 2026-10-23

首个版本：三个节点、两条出口、八道机检、中英双语界面。

### 节点与核心
- `MossStoryboardLayout`：格数 → 整数分解 → 按画布比例挑排法（4:1 容忍线 + 近打平按横条惯例）；
  单格/画布对齐 16；输出 `spec`（几何单线传递）、`layout_json`（MOSS 原生 bbox 格式）、`template`、`report`。
- `MossStoryboardSampler`：段落拆分 → 布局 JSON → 采样 → 解码 → 按坐标复绘宫格线 → 裁格。
- `MossStoryboardCrop`：按 `spec` 或 `columns×rows` 精确裁格，附裁切线预览；不整除直接报错。
- `core/{layout,prompt,pixels,sampling}.py`：几何/提示词纯 Python，像素只依赖 torch，采样封装宿主 API。
- `v3/_compat.py`：V3 schema 由 `INPUT_TYPES()` 自动生成（参数表唯一真相源）。

### 中英双语与悬浮提示
- `web/moss_sb_i18n.js`（纯函数，可 node 单测）+ `moss_sb_i18n_ui.js`（挂钩、中/EN 按钮、抗覆盖兜底）。
- 铁律：只改 `label`/`title`/`tooltip`，绝不改 `name`/`value`/`options.values`；
  中文提示只在 `nodes.py` 写一份，`TIPS` 只存 `en`；自定义标题不覆盖、只换前缀。
- `WEB_DIRECTORY` 已接；宿主 `/extensions` 实测出现两个 JS（HTTP 200）。

### 测试（全部可复跑）
- `tests/test_core.py` 282 项：几何/提示词/像素（零 GPU）
- `tests/test_contract.py` 54 项：参数表自洽、错误路径、依赖边界、仓库卫生、词表完整性
- `tests/test_sampling_contract.py` 18 项：用桩锁住宿主采样 API 的入参类型
- `tests/test_i18n.mjs` 46 项：中英切换纯函数（node）
- `tools/smoke_nodes.py` 25 项 / `tools/host_loader_check.py` 16 项 / `tools/check_ui_workflow.py` 15 项/文件
- `tools/e2e_matrix.py` 14 用例真跑：3/4/6/8/9 格、横竖屏、无缝、粗缝、1 格、edge=256、plain

### 已实测为「不可用」（详见 README §八）
- **模板 img2img 在 MOSS 上不成立**：35步/0.85 纯黑、12步/0.6 与 4步/0.5 均模板残影
  ⇒ 只要 sigma 真截断，模型就不改动 latent。默认改纯布局路线。
- **低步数 denoise 静默失效**：宿主按 `int(steps/denoise)` 重算 sigma，4 步时 >0.8 一段都不截；
  `report` 现在打印「名义 / 实际」。

### 开发期修掉的真 bug（记下来防回归）
1. `sample_custom` / `prepare_noise` 要 latent **张量**，传 `{"samples":...}` ⇒ `'dict' object has no attribute 'is_nested'`。
2. UI→API 转换：前端会把 `forceInput` 输入挪到 `inputs` 数组前面，按服务端 `input_order` 硬套会把
   `positive` 接成 IMAGE。改为按节点自身 `inputs` 顺序解析。
3. V1/V3 槽位差一格：V1 下前端自动给 `seed` 加"生成后控制"，V3 不声明就没有 ⇒ 同一工作流参数整体错位。
4. 排法在单格长边 512↔496 之间翻转（6/8 格对 16:9 的 log 距离几乎打平）。

### 已知欠账
- 布局框对"每格构图是否照办"**未做量化评分**（MOSS 布局能力官方标 experimental）。
- `README_EN.md` 为节选翻译，**未**做节级 hash 机检。
- CI 只跑 CPU 单元测试与宿主装载；**成片质量**不在 CI 覆盖范围内（需要 GPU + 权重）。
