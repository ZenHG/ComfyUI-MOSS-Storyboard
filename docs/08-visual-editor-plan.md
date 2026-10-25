# 08 · 可视化编辑器与 state v3（规格）

> **状态**：0.2.0 已落地（`MossLayoutEditor` + `web/moss_sb_editor.js`）。
> **契约（冻结）** = [`docs/10`](10-contract.md)；**前端实现** = [`docs/11`](11-editor-ui.md)。
> 本文写：为什么这么设计、编译规则、节点 IO、验收判据。

---

## 〇、一句话

**一个 STRING widget 装整份 state**：宫格模式出故事板稿，单张模式出海报 / 封面。
编辑器只改 state，编译是确定性的纯函数。

---

## 一、bbox 是什么：三层性质

| 层 | 结论 | 证据 |
|---|---|---|
| **硬几何约束**（mask / 裁切） | **不是**。不裁图、不遮罩 | 宿主只把 bbox 拼进 layout JSON 的文本，不进 latent |
| **空间分区提示** | **是，而且强** | 只给 1/4 区域 ⇒ 模型把主体塞进那一块、周围留白 |
| **与语义冲突时** | **语义赢** | 1 个 bbox + 全局段「恰好 4 个面板」⇒ 照样画 4 格 |

⇒ bbox 的正确用法是**元素分区**：在一格内部，给每个元素划一块区域，
告诉模型「自行车在这里、前轮在左下」。**这是减少偶然性的主要手段。**
⇒ 不是「拖格子调位置」—— 格子位置由几何算死，改了没用。

**这解释了 NewBie 那个画布为什么有价值**：它不是让你摆格子，是让你**限定构图**。

---

## 二、参考图路线不在本包范围（决策记录）

| 旧路线 | 现路线（0.2） |
|---|---|
| `reference_latents` / 编辑 LoRA 传参考图维持跨格一致性 | 单格 layout JSON + 清理多格措辞 + 每格独立采样 |
| 依赖模型侧 LoRA 权重加载 | 纯提示词语义 + 训练时学到的 JSON 格式 |
| 视觉相似度高但景别趋同 | 逐格独立采样，可控景别与主体 |

**移除理由（两条，各自独立成立）**：

1. `vision-only`（`TextEncodeMOSSImageEdit` 不接 vae）保持不住其它格 —— 实测其它格变化约 50/255，
   等于整张重画。
2. `reference_latents`（接 vae）绑定编辑 LoRA，收益是像素级相似，
   代价是权重加载 + 景别趋同 + 每次改动都要重走参考链。**低收益、高复杂度。**

⇒ 本仓**不提供**参考图输入。这条是终态决策，不是"暂缓"。

**一致性的真实来源**（按强度排序，全部在提示词层）：

| 机制 | 实现位置 | 强度 |
|---|---|---|
| 全局段完整读一遍（每格独占注意力） | 逐格采样时每格都传 `head` | 强（人脸余弦 99.1） |
| **元素级 bbox 分区** | layout JSON 的 `items[].bbox` | **强（已实测，见 §八）** |
| 全局段的具体度（颜色/材质/道具写死） | 用户写法 | 强 |
| 每格只写差异，不重复全套角色 | `pair_captions` + 写作纪律 | 中 |
| 每格 seed 不同（防画面重复） | `seed + 格号` | 必须 |
| 单格整幅 bbox（无元素框） | `single_panel_json` | 弱（等于没分区） |

---

## 三、state v3（唯一真相源 = docs/10 §一）

整份编辑状态序列化成一个**单行 JSON 字符串**，存进节点上唯一的 STRING widget `editor_state`。

```json
// single（海报 / 封面 / 概念图）
{"version":3,"preset":"title-body","style":"暗色雪原，飘雪，胶片颗粒。",
 "canvas":{"width":1536,"height":864},
 "items":[
   {"id":"t1","label":"text","caption":"粗体无衬线标题，浅金色","text":"MOSS Storyboard",
    "palette":["#c8a15a"],"bbox":[0.08,0.10,0.92,0.19]},
   {"id":"c1","label":"character","caption":"主角半身，侧对镜头","bbox":[0.10,0.24,0.90,0.80]}
 ]}
```

```json
// grid（故事板）：items 是格位，bbox 是整张宫格内归一化
{"version":3,"preset":"grid","style":"…",
 "grid":{"cols":4,"rows":2,"panel_aspect":"16:9","panel_edge":512,"gutter":8,"margin":8},
 "items":[{"id":"s1","label":"object","no":1,"size":"中景","caption":"…","bbox":[0,0,0.25,0.5]}]}
```

| 字段 | 约束 |
|---|---|
| `version` | 必须 `3` |
| `preset` | `grid` / `split-v` / `split-h` / `title-body` / `three-col` / `center` / `stack` / `free` |
| `style` | 全局段，所有元素共用 |
| `canvas` | single 必填；宽高为 16 的倍数 |
| `grid` | grid 必填；`cols × rows == len(items)`，`panel_edge` 为 16 的倍数 |
| `items[].label` | `object` / `character` / `text` |
| `items[].caption` | 这个元素**长什么样** |
| `items[].text` | 仅 text：**实际渲染的字**，必填；**不许拿 caption 兜底** |
| `items[].bbox` | `[x0,y0,x1,y1]` 归一化 0–1；`x0<x1`、`y0<y1` |
| `items[].palette` | 可选，≤5 个 `#rrggbb` |
| `items[].no` / `.size` | 仅 grid：镜号唯一 / 八档景别 |

**编辑器读入策略**：JSON 坏 / 版本不对 / preset 不认识 ⇒ 只读 + 提示 + 「重置为空白」；
缺 id / 缺 canvas / 缺 grid ⇒ 自动补；caption 空、text 空、bbox 越界 ⇒ 警告但不静默改内容。
详见 [`docs/11` §三](11-editor-ui.md)。

---

## 四、编译规则（`core/editor.py`，确定性纯函数）

### 4.1 grid

- `prompt` = `[STYLE] {style}` + 每格 `[PANEL n] {size}，{caption}`（按 `no` 升序，编号连续 1..N）。
- 几何 = `core.layout.build_layout()`（铁律 4：几何只算一次）。
- `spec` = `MOSS-SB1 panels=N w=… h=… cols=… rows=… pw=… ph=… gutter=… margin=… …`。
- `template` = 按几何渲染的宫格模板图。
- 下游：`prompt` → 采样器 `positive`；`spec` → 采样器 `spec`；`template` → 预览。

### 4.2 single

- `prompt` = 一个**多元素 layout JSON**：

```json
{"summary":{"caption":"<style>"},
 "items":[{"label":"character","caption":"…","bbox":[y0,x0,y1,x1]}, …],
 "bbox_coord_space":"abs_centralized","bbox_format":"yxyx"}
```

- `bbox` 从**画布归一化**换算成**采样画布中心原点像素**；`width`/`height` 取 `canvas`。
- `spec` 为空串；`template` 是一张空白占位图（单张没有格子可画）。
- 下游：`prompt` → `CLIPTextEncode.text`；`width/height` → 空 latent 的宽高；正常采样。

### 4.3 两条纪律

1. **caption 与 text 严格分开**：caption 是外观描述，text 是要渲染的字。缺 text 直接报错。
2. **grid 的 bbox 不是逐格元素框**：v3 的 grid item = 格位（整张宫格内归一化）。
   单格内部的元素分区不在 v3 契约里；要做得先改契约。

---

## 五、节点 IO（`MossLayoutEditor`）

```
MossLayoutEditor
  输入  editor_state : STRING（第 0 个 widget，单行 JSON）
  输出  prompt      : STRING   → CLIPTextEncode.text（single）/ 采样器 positive（grid）
        width, height: INT      → 空 latent（single）
        spec         : STRING   → 采样器 spec（grid；single 为空串）
        template     : IMAGE    → 预览（grid 宫格模板；single 空白占位）
        report       : STRING   → 人读：格数/景别/元素统计/警告
```

- **`editor_state` 必须是第 0 个 widget**：前端按位置读；插在别的参数后面会读错。
  新增 widget 只能追加到末尾（`widgets_values` 按位置存）。
- **前端形态**：[`docs/11`](11-editor-ui.md)。UI 是 `serialize:false` 的 DOM widget，
  只读写 `editor_state`，不 import Python、不算几何。
- **单张不需要采样器节点**：编辑器输出 prompt + width/height + 空白 template，
  用标准 `CLIPTextEncode` / 空 latent / `KSampler` 即可。

---

## 六、预设

- **8 个预设**：`grid` / `split-v` / `split-h` / `title-body` / `three-col` / `center` / `stack` / `free`。
- **预设表（精确 bbox、占位 caption/text）的唯一真相源 = [`docs/11` §五](11-editor-ui.md)**；
  `core/presets.py` 与前端 `PRESET_SPECS` 必须逐项对齐。
- **预设只是起点**：选完可以随便改框、改描述、删元素。
- `stack` 刻意重叠（数组顺序 = z 序）；其余预设不重叠。
- `title-body` 标题 `y0=0.10`、副标 `y1=0.90`：**已测** 0.05 会被裁；0.10 是否完全安全**未验证**。
- grid 默认 4×2；格位 `bbox = [c/cols, r/rows, (c+1)/cols, (r+1)/rows]`；
  景别按 8 档均匀铺开（n=1 用中景）。

---

## 七、前端形态（摘要）

完整实现与交互见 [`docs/11`](11-editor-ui.md)。要点：

- 首屏 8 个预设 chip；每项带一句话用途。
- grid 只读（格位由 cols/rows 算死）；single 可画框、拖动、8 向缩放。
- 吸附：其它框边 / 画布中线 / 三分线 / 16px 网格；阈值按屏幕像素换算。
- 属性面板：label / caption / text / palette / bbox / 景别；grid 下 bbox 只读。
- 快捷键：删除 / 方向键微调 / 复制 / Esc；帮助浮层。
- 性能：拖动增量绘制 + rAF；选中/增删/预设才整树重建。
- 中英双语：跟随 `mosssb_lang` 或宿主语言；只改显示，不改 state。

---

## 八、实测

### 8.1 元素 bbox 是强约束（36 张，1 分 25 秒）

不变量：1024×576 / cfg 1.0 / euler+simple / denoise 1.0 / 同 style 同 caption；每组 6 个 seed。

| 组 | 底模 | 蒸馏 LoRA | 步数 | 单格 layout | 平均 MAE | 目检 |
|---|---|---|---|---|---|---|
| A1 | 官模 bf16 | 4 步 | 4 | 整幅 | 0.0972 | 写实；**自行车几乎全被裁掉** |
| A2 | 官模 bf16 | 4 步 | 4 | **两元素** | 0.0939 | **自行车完整，前轮精确在左下** |
| B1 | kromoss int8 | 4 步 | 4 | 整幅 | 0.1068 | 动画风；构图偏 |
| B2 | kromoss int8 | 4 步 | 4 | 两元素 | 0.1049 | 命中，但**3 张里 2 张崩** |
| C1 | 官模 bf16 | 8 步 | 8 | 整幅 | 0.1320 | 写实；自行车只露车筐 |
| C2 | 官模 bf16 | 8 步 | 8 | 两元素 | 0.1156 | **自行车左下，稳定命中** |

**四条结论**：

1. **元素 bbox 是强约束，不是弱提示。** 同 seed 同 caption，A1→A2、C1→C2 构图完全改变；
   seed 11 / 22 / 33 全部复现。**本规格的核心假设成立。**
2. **bbox 效果与 LoRA 无关。** 4 步与 8 步蒸馏 LoRA 下都生效 ⇒ 这是 layout JSON 的机制。
3. **kromoss int8 不进产线。** 布局能力在，但默认风格漂到动画插画、质量极不稳。
4. **8 步 LoRA 的 seed 敏感度更高**（MAE 0.1320 vs 0.0972）：差异更多，也更容易飘。

> ⚠ MAE 口径：这里是**同 caption、不同 seed** 的两两距离，量的是「seed 能带来多少变化」，
> 与「格间 MAE」不是同一个东西，不可直接比阈值。

### 8.2 grid 端到端（编辑器 → 采样器 → 整张 + 逐格）

- 条件：2×2 / 16:9 / `panel_edge=496` / gutter 8 / margin 8 / 4 步蒸馏 LoRA / seed 555 / 整张模式。
- 结果：整张 **1024×576**；逐格 **4 × 496×272**；4 格内容互不相同（两两 MAE 56.8–66.6）；
  耗时 **18.0 s**（单卡，4 步）。
- 编辑器产出的 `spec` 与旧 `MossStoryboardLayout` 对同一几何**逐字相同**：
  `MOSS-SB1 panels=4 w=1024 h=576 cols=2 rows=2 pw=496 ph=272 gutter=8 margin=8 padx=8 pady=8`。

### 8.3 单张海报

- `title-body` → 1536×864；标题 / 副标 / 人物各在其位（节点真机出图，见 `docs/11` §八）。
- 文字渲染的准确率 / 字号下限 / 中英混排等，见 `docs/09` §4.3 的实测矩阵；**未量化**的部分不写成已证。

---

## 九、验收

零 GPU（应全绿）：

```bash
python tests/test_core.py              # 几何 / 提示词 / 像素，302 项
python tests/test_contract.py          # 参数表 / 错误路径 / 词表，130 项（--no-host 114 项）
python tests/test_editor.py            # state v3 编译层，102 项
python tests/test_cross_language.py    # JS ↔ Python 契约差分，66 项
python tests/test_panel_assembly.py    # 逐格拼版 reborder 语义，5 项
python tests/test_sampling_contract.py # 采样 API 契约，19 项
node   tests/test_i18n.mjs             # 中英切换纯函数，54 项
python tools/smoke_nodes.py            # 节点注册 + 纯节点真跑，41 项
python tools/host_loader_check.py      # 宿主真实加载器，19 项
python tools/check_ui_workflow.py examples/*.json tests/fixtures/*.json   # 6 份共 88 项
python tools/check_editor_ui.py        # 编辑器词表/语法/接线/互操作，80 项
```

示例工作流（全部含本包节点、槽位逐位值域、接线完整）：

| 文件 | 用途 |
|---|---|
| `examples/moss-layout-editor-poster.json` | 单张海报（编辑器 → CLIPTextEncode + latent 宽高） |
| `examples/moss-layout-editor-storyboard.json` | 4 格故事板（编辑器 → 采样器 positive/spec） |
| `examples/moss-storyboard-4panels-ui.json` | 旧链路 4 格 UI 示例 |
| `examples/moss-storyboard-4panels-edit.json` | 只改一格（需要 Moss-Image-Edit LoRA / 编辑节点） |
| `tests/fixtures/` | 机检夹具：8 格最小图、0.1.x 20 槽工作流 |

要 GPU 的端到端（人工跑，不进 CI）：

```bash
# 宿主重启加载本版后（widget 顺序以当前源码为准）
python tools/ui_to_api.py examples/moss-layout-editor-storyboard.json --run
```

---

## 十、明确不做

| 不做 | 原因 |
|---|---|
| 在宫格总览上拖格子 | 格位由几何算死，拖了没用（§一） |
| 参考图输入 | 不在本包范围（§二） |
| grid 的逐格元素分区 | v3 契约里 grid item = 格位；要做得先改契约 |
| z 序 / 对齐均布 | P2（docs/09 §六）；撤销/重做已在 0.2.2 落地 |
| 编辑器内编译 prompt | 编译是后端的活；前端只维护 state |
| 在编辑器里算 width/height | 几何归后端（铁律 4）；前端只显示/输入 |

---

## 十一、相关文件

| 文件 | 内容 |
|---|---|
| `docs/10-contract.md` | 冻结契约（state v3 / 节点 IO / 前端硬约束） |
| `docs/11-editor-ui.md` | 前端实现、预设表、吸附、快捷键、自检 |
| `core/editor.py` | state 解析 / 校验 / 编译（grid + single） |
| `core/presets.py` | 8 预设生成器（与 docs/11 §五 对齐） |
| `nodes.py::MossLayoutEditor` | 节点入口（调 `core/editor.py`） |
| `web/moss_sb_editor.js` | 编辑器 UI（单文件 DOM + SVG） |
| `tests/test_editor.py` | 编译层单测 |
| `tools/check_editor_ui.py` | 前端自检 |
