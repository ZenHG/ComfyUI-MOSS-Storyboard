# 10 · state v3 冻结契约

> 本文是 `MossLayoutEditor` 的**公开冻结契约**：前端、编译层、示例和测试都以它为准。
> 实现细节由维护者在本地文档中维护；本文只固定 state 字段、编译口径与兼容性。
> 预设表的唯一真相源 = `core/presets.py`。

---

## 一、state v3

整份编辑状态是一个**单行 JSON 字符串**，存在节点唯一的 STRING widget `editor_state` 里。

单张示例（海报 / 封面 / 概念图）：

```json
{"version":3,"preset":"title-body","style":"暗色雪原，飘雪，胶片颗粒。",
 "canvas":{"width":1536,"height":864},
 "items":[
   {"id":"t1","label":"text","caption":"粗体无衬线标题，浅金色","text":"MOSS Storyboard",
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
| `grid` | obj | grid 必填 | `cols`/`rows` ∈ [1,12]；`cols×rows == len(items)`（唯一例外：1×1 且 `items` 为空 = 空白占位）；`panel_edge` ≥64、≤4096、16 的倍数；`gutter`/`margin` ∈ [0,256]；`panel_aspect` ∈ `core.layout.ASPECTS` |
| `items` | list | ✅ | 最多 256 个 |
| `items[].id` | str | ✅ | 非空、唯一 |
| `items[].label` | str | ✅ | `object` / `character` / `text` |
| `items[].caption` | str | ✅ | 非空；这个元素**长什么样** |
| `items[].text` | str | text 必填 | **实际渲染的字**；非空；**不许拿 caption 兜底** |
| `items[].palette` | list[str] | ❌ | ≤5 个；每项必须是 `#rrggbb` |
| `items[].bbox` | list[4] | ✅ | 4 个实数，归一化 0–1；`x0<x1`、`y0<y1` |
| `items[].no` | int | grid 必填 | 唯一 |
| `items[].size` | str | grid 必填 | 八档是**预设**，可写自定义镜头语言（≤64 字）；空串 = 不注入 |

**默认值**：单张空白画布 `1536×864`；宫格预设默认 `4×2`。
**输入上限**：`editor_state` 字符串 ≤ 1,000,000 字符（防恶意工作流冻结宿主）。
**数值口径**：JSON 没有 int/float 之分，`3` / `1536.0` 这类**整值浮点**按整数接受并归一为 int；
`1536.5` 拒绝；`NaN` / `Infinity`（含 `1e400` 溢出）一律拒绝 —— 它们不是合法 JSON。
**文本口径**：`caption` / `text` 去掉空白与零宽 / 方向控制字符后必须还有可见内容。
**导出量化**：单张模式导出的 elements bbox 乘 1000、保留 3 位小数；量化后宽度 < 0.001 的框
向外补到 0.001，保证导出文件能原样再导入。

**版本策略**：只认 `version=3`。读到别的版本必须**显式报错**并提示重存/迁移；
未来改结构时新增 `version` 与迁移器，不允许静默猜测。

---

## 二、节点 IO

```
MossLayoutEditor
  输入  editor_state : STRING（第 0 个 widget，单行 JSON）
        prompt       : STRING（可选，forceInput；接上则接管逐格描述，见 §六）
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
- `MossStoryboardLayout.char_boxes`（可选 widget）：逗号分隔、**编号从 1 起**（如 `1, 3`，
  与宿主 `MOSSImageLayoutPrompt` 一致），命中的格 `label` 变 `character`，其余用 `label`。
  默认空串 ⇒ 输出逐字节不变；只有它挂在 widget 末尾，追加不影响旧工作流。
- `MossStoryboardSampler.latent`：**逐格模式返回 N 个单格 latent 的 batch**
  （`[N,C,h,w]`，行主序 = 格号 1..N），整张模式返回整张画布 latent。
  两者都不是拼版（拼版在 `grid` 输出里），`VAEDecode` 按 batch 逐张解码。
  形态是 LATENT 字典 `{"samples": [N,C,h,w]}`——宿主 `VAEDecode` 取的就是这个键，
  所以不能回裸张量；也不能回 list（V3 没有列表输出，下游会静默坏）。

---

## 三、前端硬约束

| # | 约束 |
|---|---|
| 1 | 只读写 `editor_state` 这一个 widget；其它都是内部实现 |
| 2 | bbox 一律归一化 0–1；显示尺寸只在渲染时乘 |
| 3 | 不自己算 `width/height`（几何归后端；前端只显示/接收用户输入） |
| 4 | 不改 `widgets_values` 顺序；不重排 widget |
| 5 | 单文件 `web/moss_sb_editor.js`，不拆本地 ES module |
| 6 | 撤销/重做历史只在内存，不写进 widget；外部改 `editor_state`（打开工作流 / 宿主级撤销）必须清空历史；输入框内不拦截 `Ctrl+Z` |
| 7 | 画布下方独立一条「最终提示词」预览（不放进属性面板：面板自带滚动会把预览挤到看不见），走 `compileFinalPrompt`，**必须与后端 `compile_final_prompt` 逐字节相同**（含坐标取整的逢半向偶、JSON 键序、分隔符）；不许做成"简化说明版" |
| 8 | 宫格模式收起 `label` / `text` / `palette` / `bbox` / 层级对齐控件（它们不进宫格提示词），但**不删用户 state 里的这些字段** |

---

## 四、编译规则（摘要）

- **grid**：`[STYLE] {style}` + 每格 `[PANEL n] {景别子句}. {caption}`（按 `no` 升序）。
  八档映射成英文子句（`core/editor.py::SIZE_EN`，依据 MOSS USERGUIDE「默认推荐英文」）；
  自定义景别原样输出（英文用 `. ` 分隔、中文用 `，`）；`size` 为空则只发 caption；
  几何只走 `core.layout.build_layout()`；`spec` 为 `MOSS-SB1 …`。
- **spec 读入上限**（`core.layout.parse_spec`，防手改/恶意工作流）：≤4096 字符；单格边长 64..4096；
  `gutter`/`margin` 0..256；补边 0..15；`w`/`h` 必须等于按其余字段重算的结果，否则报「自相矛盾」。
- **画布像素上限**：模板与空 latent 都 ≤ 64MP（`core.layout.MAX_CANVAS_PIXELS`），超出直接报错。
- **single**：一个多元素 layout JSON（`summary` + `items`，bbox 换算成采样画布中心原点像素）；
  `spec` 为空；`template` 为空白占位图。
- `caption` 是外观描述，`text` 是实际渲染的字；缺 `text` 直接报错。
- grid 的 item = 格位；单格内部的元素分区不在 v3 契约内（要做得先改契约）。
- **占位判据**（`第 n 格画面` / `Panel n` / 预设 caption / 预设 text）在两处各一份实现：
  `core/editor.py::find_placeholders` 与 `web/moss_sb_editor.js::findPlaceholders`。
  它只出声不改数据；清单改动必须两边同改，由 `tests/test_cross_language.py` 差分。

---

---

## 五、兼容性

- **0.1.x 工作流**：`MossStoryboardSampler` 旧版只有 20 个 widget 值；当前节点在末尾追加了
  `mode` / `sample_edge`，旧值必须按原顺序映射到前 20 个参数，尾部用默认值。
  `tests/fixtures/moss-storyboard-0.1-20slots.json` 是 0.1.x 20 槽夹具，`tools/check_ui_workflow.py` 专门锁这条。
- **前端最低版本**：ComfyUI 前端 1.57.1（`addDOMWidget` + DOM/SVG 覆盖层）；
  Nodes 2.0 开关不影响 state 字段，编辑器在其下仍只读写 `editor_state`。
- **不做网络/遥测/API key**：编辑器与编译器纯本地、零额外依赖。

---

## 六、外接提示词（可选输入 `prompt`）

- 接线即接管：外部稿按 `[PANEL n]` 分段，第 i 段 → 第 i 格（按镜号排序）。
- 段首写景别 ⇒ 用外接的；没写 ⇒ **该格景别清空（不注入）**——外接文本是唯一真相源。
- 段数少于格数 ⇒ 后几格沿用编辑器；多于格数 ⇒ 尾部丢弃。**两种情况都写进 `report`**。
- 无 `[PANEL n]` 标记 ⇒ 整段当全局描述。
- 前端提示词区同一套解析（`parseStoryboard`），两边由 `tests/test_cross_language.py` 差分。

---
