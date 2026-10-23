# CHANGES

## 0.1.2 — 2026-10-23

### 移除
- 🔴 **删掉图生图（模板初始化）通路。** MOSS 不以参考图条件化（官方 limitation 明写"不支持编辑"），
  此前三组实测（35 步 / 0.85 纯黑、12 步 / 0.6 与 4 步 / 0.5 残影）已证明只要 sigma 真截断，
  模型就不改动 latent。留着"能选但必然失败"的选项只会让用户白跑一遍，所以直接收掉：
  - `init` 从 `auto/layout/template/both/plain` 收成 `auto/layout/plain`
  - 采样器不再有 `template` 输入，起点恒为空 latent
  - 删除 `examples/moss-storyboard-8panels-template.json`（它是 35 步 / 0.85 / `init=both`，
    正是会出纯黑的那组）
  - `core/sampling.encode_image` 一并删除（无调用方）
- ⚠ **破坏性变更**：已经接了模板线的旧工作流，载入后该连线会失效（不报错，只是不再连上）。
  用 `tools/make_ui_workflow.py` 重新生成即可。

### 文档
- README / README_EN「这一版做不到的」改为直说"不做图生图"，并补一条实测：
  **官方非蒸馏配方（50 步 / cfg 3）在本机跑出来是崩坏扭曲的**，原因未确认、社区有能跑通的报告。
  `steps` / `cfg` 的 tooltip 同步这句，不再写成"可以试试更高保真"。
- `denoise` 的说明改写成"没有图生图，调 <1 只会少跑几步"，删掉原先"模板初始化时必须 <1"那句。

### 计数
`test_core 282` · `test_contract 48`（--no-host）· `test_sampling_contract 18` · `test_i18n 54` ·
`smoke_nodes 25` · `host_loader_check 16` · `check_ui_workflow 15/文件` · `check_frontend 9` ·
`check_release_ready 29`


## 0.1.1 — 2026-10-23

### 修复
- 🔴 **示例工作流没进 registry 包**。`examples/` 里那个带说明的工作流用了中文文件名，
  而 `comfy node pack` 按 latin-1 解路径 ⇒ 中文名被**静默跳过**（只在打包日志里留一行
  `File not found. Not including in zip`），README 却叫用户去开它。
  现已改名为 `moss-storyboard-4panels-ui.json`；`tools/make_ui_workflow.py` 写宿主目录仍用中文名
  （用户友好），写仓库副本强制 ASCII。
- `tools/check_release_ready.py` 的 S4 把「包内中文文件名」从警告改为**判红** ——
  这类缺陷两边都不报错，只能靠发布前拦。

### 计数
`check_release_ready 27/0` · 其余 8 条全绿


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
- 铁律：只改 `label`/`title`/`tooltip`/`placeholder`，绝不改 `name`/`value`/`options.values`；
  中文提示只在 `nodes.py` 写一份，`TIPS` 只存 `en`；自定义标题不覆盖、只换前缀。
- **多行文本框的标题是 `placeholder`，不是 `label`**：DOM `<textarea>` 父容器 `size-full`
  把画布自绘标题盖住了。只写 label ⇒ 界面上仍是 `positive` / `negative`（真机实证）。
  现在两者同写，`tests/test_i18n.mjs` 6.20–6.23 锁这条。
- **`negative` 默认收进「高级」**（`advanced: True`）：MOSS 实测 cfg=1.0，
  宿主在 cfg=1 时跳过负面分支（`comfy/samplers.py::sampling_function`），
  那段不进计算图。放普通区只会让人误以为"写了就生效"。
  `test_contract.py::test_sampler_shape` 锁这条。
- `WEB_DIRECTORY` 已接；宿主 `/extensions` 实测出现两个 JS（HTTP 200）。

### 测试（全部可复跑）
- `tests/test_core.py` 282 项：几何/提示词/像素（零 GPU）
- `tests/test_contract.py` 54 项：参数表自洽、错误路径、依赖边界、仓库卫生、词表完整性
- `tests/test_sampling_contract.py` 18 项：用桩锁住宿主采样 API 的入参类型
- `tests/test_i18n.mjs` 46 项：中英切换纯函数（node）
- `tools/smoke_nodes.py` 25 项 / `tools/host_loader_check.py` 16 项 / `tools/check_ui_workflow.py` 15 项/文件
- `tools/e2e_matrix.py` 14 用例真跑：3/4/6/8/9 格、横竖屏、无缝、粗缝、1 格、edge=256、plain

### 0.1.0 实测结论（详见 README「已知边界」）
- **模板 img2img 在 MOSS 上未生效**（下一版会重做并复测）：35步/0.85 纯黑、12步/0.6 与 4步/0.5 均模板残影
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
