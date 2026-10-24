# 10 · state v3 冻结契约

> 本文是 `MossLayoutEditor` 的**公开冻结契约**：前端、编译层、示例和测试都以它为准。
> 实现细节见 [`docs/08`](08-visual-editor-plan.md)（编译与实测）和 [`docs/11`](11-editor-ui.md)（前端）。
> 预设表的唯一真相源 = [`docs/11` §五](11-editor-ui.md)。

---

## 一、state v3

整份编辑状态是一个**单行 JSON 字符串**，存在节点唯一的 STRING widget `editor_state` 里。

单张示例（海报 / 封面 / 概念图）：

```json
{"version":3,"preset":"title-body","style":"暗色雪原，飘雪，胶片颗粒。",
 "canvas":{"width":1536,"height":864},
 "items":[
   {"id":"t1","label":"text","caption":"粗体无衬线标题，浅金色","text":"永兵传说",
    "palette":["#c8a15a"],"bbox":[0.08,0.10,0.92,0.19]},
   {"id":"c1","label":"character","caption":"主角半身，侧对镜头","bbox":[0.10,0.24,0.90,0.80]}
 ]}
```

宫格示例（故事板）：items 是格位，bbox 是整张宫格内归一化。

```json
{"version":3,"preset":"grid","style":"…",
 "grid":{"cols":4,"rows":2,"panel_aspect":"16:9","panel_edge":512,"gutter":8,"margin":8},
 "items":[{"id":"s1","label":"object","no":1,"size":"中景","caption":"…","bbox":[0,0,0.25,0.5]}]}
```

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `version` | int | ✅ | 必须 `3` |
| `preset` | str | ✅ | `grid` / `split-v` / `split-h` / `title-body` / `three-col` / `center` / `stack` / `free` |
| `style` | str | ✅ | 全局段，所有元素共用 |
| `canvas` | obj | single 必填 | `{width,height}`；≥64、≤4096、16 的倍数 |
| `grid` | obj | grid 必填 | `cols`/`rows` ∈ [1,12]；`cols×rows == len(items)`；`panel_edge` ≥64、≤4096、16 的倍数；`gutter`/`margin` ∈ [0,256]；`panel_aspect` ∈ `core.layout.ASPECTS` |
| `items` | list | ✅ | 最多 256 个 |
| `items[].id` | str | ✅ | 非空、唯一 |
| `items[].label` | str | ✅ | `object` / `character` / `text` |
| `items[].caption` | str | ✅ | 非空；这个元素**长什么样** |
| `items[].text` | str | text 必填 | **实际渲染的字**；非空；**不许拿 caption 兜底** |
| `items[].palette` | list[str] | ❌ | ≤5 个；每项必须是 `#rrggbb` |
| `items[].bbox` | list[4] | ✅ | 4 个实数，归一化 0–1；`x0<x1`、`y0<y1` |
| `items[].no` | int | grid 必填 | 唯一 |
| `items[].size` | str | grid 必填 | 八档景别之一 |

**默认值**：单张空白画布 `1536×864`；宫格预设默认 `4×2`。
**输入上限**：`editor_state` 字符串 ≤ 1,000,000 字符（防恶意工作流冻结宿主）。

**版本策略**：只认 `version=3`。读到别的版本必须**显式报错**并提示重存/迁移；
未来改结构时新增 `version` 与迁移器，不允许静默猜测。

---

## 二、节点 IO

```
MossLayoutEditor
  输入  editor_state : STRING（第 0 个 widget，单行 JSON）
  输出  prompt      : STRING   → CLIPTextEncode.text（single）/ 采样器 positive（grid）
        width,height: INT      → 空 latent（single）
        spec         : STRING   → 采样器 spec（grid；single 为空串）
        template     : IMAGE    → 预览（grid 宫格模板；single 空白占位）
        report       : STRING   → 人读：格数/景别/元素统计/警告
```

- **`editor_state` 必须是第 0 个 widget**；新增 widget 只能追加到末尾
  （`widgets_values` 按位置存，插在中间会让旧工作流整体串位）。
- 单张模式不需要故事板采样器：`prompt` 已是 layout JSON，直接接 `CLIPTextEncode`；
  `width/height` 接空 latent 的宽高；后接普通 `KSampler`。
- 宫格模式接 `MossStoryboardSampler`：`prompt → positive`，`spec → spec`。

---

## 三、前端硬约束

| # | 约束 |
|---|---|
| 1 | 只读写 `editor_state` 这一个 widget；其它都是内部实现 |
| 2 | bbox 一律归一化 0–1；显示尺寸只在渲染时乘 |
| 3 | 不自己算 `width/height`（几何归后端；前端只显示/接收用户输入） |
| 4 | 不改 `widgets_values` 顺序；不重排 widget |
| 5 | 单文件 `web/moss_sb_editor.js`，不拆本地 ES module |

---

## 四、编译规则（摘要）

- **grid**：`[STYLE] {style}` + 每格 `[PANEL n] {size}，{caption}`（按 `no` 升序）；
  几何只走 `core.layout.build_layout()`；`spec` 为 `MOSS-SB1 …`。
- **single**：一个多元素 layout JSON（`summary` + `items`，bbox 换算成采样画布中心原点像素）；
  `spec` 为空；`template` 为空白占位图。
- `caption` 是外观描述，`text` 是实际渲染的字；缺 `text` 直接报错。
- grid 的 item = 格位；单格内部的元素分区不在 v3 契约内（要做得先改契约）。

---

## 五、兼容性

- **0.1.x 工作流**：`MossStoryboardSampler` 旧版只有 20 个 widget 值；当前节点在末尾追加了
  `mode` / `sample_edge`，旧值必须按原顺序映射到前 20 个参数，尾部用默认值。
  `tools/check_ui_workflow.py` 有一个 0.1.x 20 槽 fixtures 专门锁这条。
- **前端最低版本**：ComfyUI 前端 1.57.1（`addDOMWidget` + DOM/SVG 覆盖层）；
  Nodes 2.0 开关的兼容性见 `docs/11` 的验证记录。
- **不做网络/遥测/API key**：编辑器与编译器纯本地、零额外依赖。
