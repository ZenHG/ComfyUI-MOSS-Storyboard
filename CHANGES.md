# CHANGES

## 0.2.0 — 2026-10-23

### 新增
- **中英双语 UI + 悬浮提示**（`web/moss_sb_i18n.js` 纯函数层 + `moss_sb_i18n_ui.js` 挂钩）：
  节点标题 / 参数标签 / 枚举显示 / 悬浮提示跟随宿主界面语言，右下角「中 / EN」可覆盖。
  `WEB_DIRECTORY` 已接，宿主 `/extensions` 列表实测出现两个 JS。
- `tools/e2e_matrix.py`：端到端矩阵，14 用例 + 2 次随机种对照，逐条断言
  「任务成功 / 整图尺寸 == 理论值 / 逐格数量与尺寸 / 宫格线为白 / 格内非白 / 各格不雷同」。
- `tests/test_contract.py`（54 项）：参数表自洽、错误路径、依赖边界、仓库卫生、
  **词表完整性**（每个参数都要有英文提示）。
- `tests/test_sampling_contract.py`（18 项）：用桩替身锁住宿主采样 API 的入参类型。
- `tests/test_i18n.mjs`（46 项）：中英切换纯函数，含"只改显示不改存档"的四条铁律。
- `tools/ui_to_api.py`：UI 工作流 → API prompt（用服务端 `/object_info` 反推 widget 顺序）。
- `tools/patch_workflow_4panels.py`：把交付工作流改成 4 格 / 1024×576。

### 修复
- 🔴 **采样必崩**：`sample_custom` / `prepare_noise` 要的是 latent **张量**，之前传的是
  `{"samples": ...}` 字典 ⇒ `'dict' object has no attribute 'is_nested'`。已在边界处摊平。
- 🔴 **UI→API 转换槽位错位**：前端会把**只接线的输入**（spec/template）挪到 `inputs` 数组前面，
  按服务端 `input_order` 硬套结果是 `positive ← 一张 IMAGE`。改为按节点自身 `inputs` 顺序解析。
- **排法在 512/496 之间翻转**：6 格与 8 格对 16:9 的 log 距离几乎打平，单格长边差 16px 就换排法。
  加「近打平（0.08 log）按惯例优先横条」的判据。
- **机检假红**：`last_link_id` 是高水位不是条数（删过连线就会不等于 len）；`1 格`用例的
  「多格不雷同」断言在 N=1 时无意义。两处都已改判据，不是改数据。

### 已实测为「不可用」（写进 README §八）
- **模板 img2img 路线在 MOSS 上不成立**。三组参数（35步/0.85、12步/0.6、4步/0.5）
  分别得到纯黑与模板残影；只要 sigma 真截断，模型就不改动 latent。
  ⇒ `init=auto` 改为纯布局路线，模板连线在交付工作流里已断开。
- **低步数下 denoise 静默失效**：4 步时 `int(4/denoise)=4`，>0.8 的降噪一段都不截。
  `report` 现在打印「名义 / 实际」，并给出可用的阈值提示。

### 计数
`test_core 282/0` · `test_contract 54/0` · `test_sampling_contract 18/0` · `test_i18n 46/0`
· `smoke_nodes 25/0` · `host_loader_check 16/0` · `check_ui_workflow 15/0` · `e2e_matrix 14/14`


## 0.1.0 — 2026-10-23

首个可用版本。三个节点、两条出口、三套机检。

### 新增
- `MossStoryboardLayout`：格数 → 整数分解 → 按目标画布比例挑排法；16 对齐；
  输出 `spec`（几何单线传递）、`layout_json`（MOSS 原生 bbox 布局格式）、`template`（模板图）、`report`。
- `MossStoryboardSampler`：段落拆分 → 布局 JSON → 可选模板 latent → 采样 → 解码 →
  按坐标复绘宫格线 → 裁格。输出 `grid` / `panels` / `latent` / `spec` / `prompt_used` / `report`。
- `MossStoryboardCrop`：按 `spec` 或 `columns×rows` 精确裁格，附裁切线预览；不整除直接报错。
- `core/layout.py` / `core/prompt.py` / `core/pixels.py` / `core/sampling.py`
- `v3/_compat.py`：V3 schema 由 `INPUT_TYPES()` 自动生成（参数表唯一真相源）
- `examples/`：8 格布局路线与 8 格模板路线两个工作流
- `tools/`：`smoke_nodes.py`（自加载）、`check_ui_workflow.py`（连线 + widget 槽位）、
  `make_example.py`（生成示例）、`host_loader_check.py`（走宿主 `load_custom_node`，16 项）

### 两个不显眼但会咬人的点（已修，写下防回归）
- **V1/V3 槽位差一格**：V1 下前端自动给 `seed` 加"生成后控制"下拉，V3 下不声明就没有 ⇒
  同一工作流在两条出口上 `widgets_values` 整体错位且不报错。
  `v3/_compat.py` 对 `seed` / `noise_seed` 显式补 `control_after_generate=True`，`smoke_nodes.py` 有断言。
- **对齐余量污染格坐标**：16 对齐差额若平摊进单格尺寸，会让格坐标随余量漂移。
  改为只加到右/下外边距（与边框同色），格坐标与 `pad_*` 完全解耦。

### 已知欠账
- 模板 img2img 路线在 MOSS 上的成片效果**未测**（MOSS 无原生 img2img，属分布外用法）。
- 布局框对"每格构图是否照办"**未做量化评测**（MOSS 布局能力官方标 experimental）。
- `README_EN.md` 为节选翻译，**未**做节级 hash 机检（H 仓那套 `en_sync.py` 尚未移植）。
- `pyproject.toml` 的 `PublisherId` 是占位值，首次发布前必须替换。
