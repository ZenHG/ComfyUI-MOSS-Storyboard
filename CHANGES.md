# CHANGES

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
- `tools/`：`smoke_nodes.py`（真宿主）、`check_ui_workflow.py`（连线 + widget 槽位）、`make_example.py`

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
