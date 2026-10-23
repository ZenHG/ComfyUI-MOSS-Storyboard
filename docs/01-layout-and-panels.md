# 01 · 宫格几何与裁切

本文件解释 `core/layout.py` 与 `core/pixels.py` 的每条规则**为什么是这样**。
所有数字都可在 `tests/test_core.py` 里找到对应断言。

---

## 一、为什么要 16 对齐

MOSS-Image 的 latent 空间取自 FLUX.2 VAE：

- `comfy/latent_formats.py::Flux2` → `latent_channels = 128`，`spacial_downscale_ratio = 16`
- `comfy_extras/nodes_flux.py::EmptyFlux2LatentImage` → `torch.zeros([B, 128, H // 16, W // 16])`

也就是说 latent 高度 = `H // 16`（整除，**向下取整，不报错**）。若画布是 2090 高，
latent 只有 130 行（=2080px），解码回来差 10px；此时任何按 2090 算出来的裁切框都会
**逐渐偏移**，越靠右下偏得越多。

本包的处理：`snap16()` 把单格长边/短边都对齐 16；画布对齐的差额（0–15px）只加到
**右/下外边距**，不改单格尺寸。于是：

```
width  = cols*panel_w + (cols-1)*gutter + 2*margin + pad_x    (pad_x ∈ [0,15])
height = rows*panel_h + (rows-1)*gutter + 2*margin + pad_y
```

`pad_x` / `pad_y` 落在边框色区域（外边距与宫格线同色），肉眼无法分辨；
而 `cell.x/y` 全部由 `margin / panel_w / gutter` 推出，与 `pad_*` 无关 ⇒
**对齐余量永远不会污染格坐标**。

---

## 二、格数与排法

格数是输入，排法由"候选 + 打分"决定，不是查表：

```python
candidate_grids(N) = [(rows, cols) for rows in 1..N if N % rows == 0]   # (rows, cols) 全整数分解
```

对每个候选算画布宽高比 `ar`，取 `|log(ar / target)|` 最小者。
`target` = `canvas_aspect`（`auto` 时按单格朝向取 16:9 或 9:16）。
候选先过一道 `1/4 ≤ ar ≤ 4` 的容忍线，全被否掉才退让并在 `report` 里出声。

实测（单格 16:9、长边 512、缝 8、外边距 8）：

| 格数 | 选中 | 画布 | 比例 | 落选原因 |
|---|---|---|---|---|
| 3 | 3 行 × 1 列 | 528×896 | 0.59 | 1×3 横排 = 5.2:1，超 4:1 |
| 4 | 2×2 | 1056×608 | 1.74 | — |
| 6 | 2 行 × 3 列 | 1568×608 | 2.58 | 3×2 竖排 = 1.17，离 16:9 更远 |
| 8 | 2 行 × 4 列 | 2096×608 | 3.45 | 4×2 竖排 = 0.88，离 16:9 更远 |
| 9 | 3×3 | 1568×896 | 1.75 | — |

`canvas_aspect="9:16"` 时同一批格数会翻成竖向（8 格 → 4 行 × 2 列，608×2096）。
`columns` / `rows` 给具体值则跳过打分，只校验 `columns × rows == panels`。

> 为什么容忍线是 4:1 而不是 3:1：4×2 的 16:9 单格画布本身就是 3.45:1，
> 3:1 会把故事板最常用的横条排法否掉，于是 8 格被迫竖排——这不是用户想要的。

---

## 三、`spec` 字符串

一根线传完所有几何，避免"画布按 8 格算、裁切按 4 格填"这种静默串位。

```
MOSS-SB1 panels=8 w=2096 h=608 cols=4 rows=2 pw=512 ph=288 gutter=8 margin=8 padx=8 pady=8
```

- `parse_spec()` 只认前缀 `MOSS-SB1`，缺字段直接 `ValueError`（不猜、不兜底）。
- 输出侧 `Layout.spec()` 与输入侧 `parse_spec()` 是一对；`tests` 里有往返一致性断言。
- 三个节点（Layout / Sampler / Crop）都认它；Sampler 与 Crop 的 `spec` 是 `forceInput` 槽，
  接了就以它为准，并在 `report` 里写明"几何来源 = spec 接线"。

---

## 四、宫格线为什么要在解码后重画

模型不是排版软件。即使模板在前段把白缝钉进了 latent，高 denoise 段仍会让缝：
变细、断开、被内容覆盖、或两侧亮度不均。

`composite_borders()` 的做法是**按坐标重画**：

```
mask = 外框四边外边距 ∪ 每条内缝（宽度 = min(border_px or gutter, gutter)，居中）
out  = image * (1 - mask) + level * mask
```

代价是零（一次切片赋值），收益是线宽、直度、位置 100% 确定，且与 `Crop` 用的是同一份
`Layout` ⇒ 裁出来的每格边缘一定干净。`border_px=0` 表示铺满整条缝。

---

## 五、旧实现（ComfyUI-Storyboard-Tools）的缺陷表

下表是对本机安装包**逐行阅读 + 实际运行**得到的复现结果（不是推测）。
本包的几何层就是照这张表重写的。

| # | 缺陷 | 复现 | 本包如何避免 |
|---|---|---|---|
| 1 | 3 列画布必崩：`round()` 逐格取整 → 683/682/683，`torch.cat` 报 `Sizes of tensors must match` | 2048 宽 + `columns=3`（即 6 格 / 9 格默认） | 单格尺寸只有一份 `panel_w`，全格同尺寸 |
| 2 | `offset_x ≠ 0` 必崩：末列 `end_x` 被强制改回 `width`，宽度与其它列不同 | `offset_x=10` + 2×2 | 不提供 offset；几何由 `spec` 唯一确定 |
| 3 | `border_thickness=1` 边框**消失**（`round(1023.5)=1024`，两端各让 0px） | 实测缝宽 0px | 缝宽是画布参数本身，不是"单格各退一半"的副作用 |
| 4 | `border_thickness=0` 得到纯色板（不是"无边框"） | 实测 `unique` 只有 1 值 | `gutter=0` 合法，此时无内缝、有外框 |
| 5 | `background_gray_value=255` 时网格整体消失（用像素值当掩码） | `bg=255` → 全 1.0 | 模板用独立几何构造，不靠像素值反推掩码 |
| 6 | 没有外框：首/末行末列强制贴边 ⇒ 最外圈 shave 切掉的是**内容** | 代码 `if c==0: x_start=0` | 一定有 `margin`，且 `pad_*` 并入外边距 |
| 7 | `letterbox_black` 分支从不裁剪 ⇒ 目标比例不达标 | 21:9 目标 1024×439，实得 1024×569 | `padding_mode="pad"` 只在不足时补边；要改比例用 `crop` |
| 8 | `letterbox_black` 不做 clamp ⇒ 越界 [-0.28, 1.25] | 实测 | 重采样后统一 `clamp(0,1)` |
| 9 | 9:16 在横向画布上会 3.25× 暴力放大（315→1024） | 实测 4 格 → 1820 高 | 默认不缩放（`resize_edge=0`）；要缩放由用户显式给值 |
| 10 | Grid 出的 `cols/rows` 与 Crop 的手填值无联动，填错就静默错格 | 阶梯表在 UI 上不可见 | `spec` 单线传递，机器对账 |
| 11 | 全格被 `continue` 丢掉时**静默返回原图** | 代码 `if final_slices else image` | 任何异常尺寸直接 raise，不返回"看起来像面板"的原图 |

---

## 六、像素层的三条不变量

1. **顺序**：`crop_panels` 行优先（`index = row*cols + col`），即叙事顺序 = 输出 batch 顺序。
2. **等大**：所有格尺寸恒等于 `(panel_h, panel_w)`；不等就 `RuntimeError`（说明 `Layout` 与图像不同源）。
3. **不动内容**：`crop` 只切片；只有显式给 `target_aspect` / `resize_edge` 才重采样，且都 `clamp(0,1)`。

`guide_preview()` 与 `crop_panels()` 共用 `_border_mask()`，所以预览红线位置 == 实际裁切线位置。
旧实现里预览用未 clamp 坐标、裁切用已 clamp 坐标，两者在负 offset 时是分叉的。
