# 11 · Layout Editor 前端（state v3）

> **状态**：F1–F6 前端完成，真实节点、grid 端到端、真节点海报示例均已验证；
> `python tools/check_editor_ui.py` 68 项全过。
> **剩余**：宿主重启后复验修好的 widget 顺序。**Nodes 2.0 已验证**（Vue 模式下编辑器挂载、渲染、state 正确；无页面错误）。
>
> 契约以 [`docs/10`](10-contract.md) 为准；本文只写前端**怎么实现**、**怎么测**、**和后端约定了什么**。

---

## 一、文件与边界

| 文件 | 内容 |
|---|---|
| `web/moss_sb_editor.js` | 编辑器全部实现（单文件：DOM + SVG + 纯函数 + 挂载） |
| `tools/check_editor_ui.py` | F6 自检：词表 / 语法 / 接线 |
| `docs/11-editor-ui.md` | 本文 |

**不碰**：`core/*.py`、`nodes.py`、`web/moss_sb_i18n*.js`。

### 五条硬约束怎么满足

| # | 约束 | 实现 |
|---|---|---|
| 1 | 只读写 `editor_state` | `mount()` 只找这一个 widget；UI 是 `serialize:false` 的 DOM widget，不写别的值 |
| 2 | bbox 归一化 0–1 | state 里只存归一化；像素只在 `render()` 的 `viewBox` 映射时乘一次 |
| 3 | 不算几何 | 不产出/不修改 `width/height`；单张模式的 `canvas.width/height` 是用户输入（不是推导），网格模式只按 `cols/rows/panel_aspect` 算**显示**宽高比 |
| 4 | 不动 `widgets_values` 顺序 | 不重排 `node.widgets`，不写 `widgets_values`；`editor_state` 仍是第 0 个 widget，DOM widget 追加在末尾且不序列化 |
| 5 | 单文件 | 只 `import { app } from "../../scripts/app.js"`（宿主模块），无本地 import |

---

## 二、挂载

```js
// beforeRegisterNodeDef / nodeCreated + 1.5s guard 双通道
app.registerExtension({
  name: "mosssb.editor",
  nodeCreated(node) { if (node?.type === "MossLayoutEditor") mount(node); },
});
```

- **为什么还要 guard**：工作流载入路径不保证触发 `nodeCreated`（前端 1.57.1 实测，和 i18n 扩展同一坑）。
  guard 每 1.5s 补挂 + 同步外部改动过的 widget；幂等靠 `node.__mossedUI` 去重。
- **藏 widget**：`editor_state.computeSize = () => [0, -4]` —— 仍然是普通 widget，
  照常进 `widgets_values`；界面高度为 0。
- **DOM 容器**：`node.addDOMWidget("mosssb_editor_ui", "moss-layout-editor", root, {serialize:false, getHeight:()=>700})`。
  节点默认拉到 `620 × 800`（只增不减，不覆盖用户已保存的尺寸）。
- **载入时序**：`nodeCreated` 在 `configure` 之前触发 ⇒ 包一层 `onConfigure`，等 widget 值恢复后再读一次。

### 节点契约前提

节点必须是：

```
MossLayoutEditor
  输入（第 0 个 widget）: editor_state : STRING（单行 JSON）
```

顺序错了，state 会读到别的参数上；`check_editor_ui.py` 的 W4/W5/W11 只保证 JS 端字符串对，
**第 0 个位置只能由 `smoke_nodes.py` 或真机确认**。

---

## 三、state 映射

```json
single: { "version":3, "preset":"title-body", "style":"…",
          "canvas":{"width":1536,"height":864}, "items":[…] }

grid:   { "version":3, "preset":"grid", "style":"…",
          "grid":{"cols":4,"rows":2,"panel_aspect":"16:9","panel_edge":512,"gutter":8,"margin":8},
          "items":[…] }
```

- **single 的 items**：元素，`bbox` 是**画布内归一化**。
- **grid 的 items**：格位，`bbox` 是**整张宫格内归一化**（4×2 时第 1 格 = `[0,0,0.25,0.5]`）；
  `no` / `size` 由编辑器维护。
- 两种模式都允许额外字段，编辑器**原样保留**（不丢后端未来加的字段）。
- `editor_state` 始终写单行 JSON（`JSON.stringify`）。

### 读入策略

| 情况 | 处理 |
|---|---|
| 空字符串 | 初始化空白 state（`free` + 1536×864 + `items: []`），并写回 |
| JSON 坏 / `version != 3` / `preset` 不认识 | **fatal**：只读 + 提示 + 「重置为空白」（带 confirm），绝不覆盖原 state |
| 缺 `id` / 重复 `id` | 自动补（`t1/c1/o1…`），写回 |
| 单张缺 `canvas` / 网格缺 `grid` | 补默认值，写回 |
| 其余（caption 空、text 空、bbox 越界、palette 非法） | 警告，不静默改内容；越界 bbox 的 item 不渲染但保留 |

---

## 四、界面

```
┌ 预设 [▼] [生成初始框]        模式 chip  [?] ┐
├ 预设说明（一句话用途）                       ┤
├ grid: 列/行/单格比例/高级  single: 画布预设/宽/高 ┤
├──────────────────────────────────────────┤
│  SVG 画布（grid 只读 / single 可画框）      │
│   · 三分线 + 中线参考线（只显示）           │
│   · 元素按类型上色；选中框 8 个把手          │
│   · 吸附时画黄色虚线；拖动中隐藏把手         │
├──────────────────────────────────────────┤
│ 状态行：模式 · items 数 · ⚠ 警告            │
├──────────────────────────────────────────┤
│ 属性面板（固定 218px，不随选中项变高）       │
│  类型点 · id · 类型 · 复制 · 删除            │
│  描述 / 文字 / 主色 / bbox / 居中 / 景别     │
└──────────────────────────────────────────┘
```

- **首屏欢迎面板**：`items` 为空时在画布中央显示 8 个预设 chip，点一下即生成初始框。
  卡片 `pointer-events:none`（仅 chip 可点），所以空画布任意位置仍可直接拖框。
- **属性面板固定高度**：否则选中 text（多一行）时面板变高、画布被挤动，
  拖动的屏幕→归一化映射会在按下与移动之间变化（实测：面板高度变化会让拖动跳位）。
- **grid 只读**：可点选、可改属性，不能拖框（格位由 `cols/rows` 算死）。
- **single**：点选 / 拖动 / 8 向缩放 / 空白处拖出新框 / 属性面板复制·居中·删除。
- **文字**：`label=text` 才显示 `text` 行；`caption`（长什么样）与 `text`（渲染什么）严格分开。
  切到非 text 会删除 `text` 字段（避免把旧字带进 layout JSON）。
- **预设切换不会自动重排 items**（防误删）；点「生成初始框」才重排，已有 items 时 confirm。
- **grid 改列/行**：按新格数重排 items，保留按位置对应的 caption / label / size。
- **grid 高级项**（`Advanced`）：单格长边 / 缝宽 / 外边距写回 `grid`；预览只画格位，
  不模拟缝宽与外边距（真实几何由节点算）。
- **中英双语**：编辑器文案跟随本包语言（`localStorage.mosssb_lang`，缺省跟随宿主 `<html lang>`）；
  切换语言只改显示，不改 state。

### 4.1 性能

- **拖动不再重建整棵 SVG**：`render()` 只在结构变化（选中、增删、预设、载入）时跑；
  拖动时 `paintDrag()` 只更新被拖的 rect / 标签 / 类型点 / 吸附线。
- **pointermove 用 `requestAnimationFrame` 合并**：一帧最多算一次，pointerup 前 `flush` 最后一帧。
- **拖动期间不重刷属性面板**：只更新 4 个 bbox 读数，避免每次移动都重建输入控件/跑校验。
- **素材规模**：常见 1–20 个框；优化后单帧只改 2–4 个 SVG 属性 + 0–2 条吸附线。
- **结构性断言**：无头冒烟在一次 10 步拖动中记录到 `paints +10`、`renders +0`
  （`MOSS_SB_EDITOR.stats(node)`）；证明拖动路径不再整树重建。
- ⚠ 未做帧率基准；上述是结构性优化 + 功能回归（38 项无头冒烟），不是 fps 数据。

### 4.2 易用性（面向第一次用的人）

| 入口 | 行为 |
|---|---|
| 首屏预设 chip | 一键生成初始框；每项带一句话用途（`PRESET_HINTS`） |
| 预设说明行 | 选中预设时显示"它服务什么"，不用猜 |
| 画布预设 | 16:9 / 9:16 / 1:1 / 4:3 / 3:4 常用尺寸；也可手改宽高（必须 16 的倍数） |
| `↶` / `↷` | 撤销 / 重做（编辑器内部历史，最多 100 步；同一元素连续输入或微调合并为一步；外部改了 `editor_state`——打开工作流、宿主级撤销——时清空） |
| `?` 帮助 | 快捷键与字段含义（`caption` vs `text`） |
| 快捷键 | `Delete/Backspace` 删除 · 方向键微调 1px（`Shift` ×10）· `Ctrl/⌘+D` 复制 · `Esc` 取消选择 · `Ctrl/⌘+Z` 撤销 · `Ctrl/⌘+Shift+Z`（或 `Ctrl+Y`）重做 |
| 复制 / 居中 | 属性面板按钮；grid 模式禁用（格位由几何算死） |
| 悬浮提示 | 画布上的框带 `<title>`（类型 + 描述 + text） |

> 所有快捷键只在编辑器有焦点时生效；在输入框内不会抢 `Delete` / 方向键 / `Ctrl+Z`（输入框里的撤销归浏览器）。

---

## 五、预设表（前端与 `core/presets.py` 的唯一真相源）

`grid` 由 `cols × rows` 生成格位；默认 4×2。格位 bbox 用精确等分：

```
第 i 格（0-based，row-major）:
bbox = [c/cols, r/rows, (c+1)/cols, (r+1)/rows]，c = i % cols，r = i // cols
景别：8 档按 i 均匀铺开（n>1），n=1 用「中景」
```

其余预设（归一化 `[x0,y0,x1,y1]`，边距 0.03）：

| 预设 | items（label · caption 占位 · bbox） |
|---|---|
| `split-v` | object「主体」`[.03,.03,.485,.97]`；text「标题文字」`[.515,.03,.97,.97]` |
| `split-h` | object「主体」`[.03,.03,.97,.485]`；text「正文文字」`[.03,.515,.97,.97]` |
| `title-body` | text「标题文字」`[.08,.10,.92,.19]`；character「主角半身，侧对镜头」`[.10,.24,.90,.80]`；text「副标题文字」`[.08,.84,.92,.90]` |
| `three-col` | object 左 `[.03,.03,.33,.97]`；中 `[.35,.03,.65,.97]`；右 `[.67,.03,.97,.97]` |
| `center` | character「主体，居中，四周留白」`[.30,.18,.70,.82]` |
| `stack` | object「背景元素」`[.05,.06,.95,.72]`；character「前景主体」`[.28,.34,.72,.95]` |
| `free` | 空 |

- `stack` **刻意重叠**：数组顺序 = z 序（后面的在上）；「不重叠」校验应把 `stack` 排除。
- `title-body` 的标题带 `palette: ["#c8a15a"]`，副标带 `["#ffffff"]` —— 给 palette 一个可见起点。
- `title-body` 的标题 `y0` 从 0.05 下移到 0.10、副标 `y1` 收到 0.90：**已测** 0.05 会被裁；
  0.10 是否完全安全**未验证**，只是保守起点。
- 占位 caption/text 只是起点，用户必须改；**不要**把 `caption` 当 `text` 兜底。

> 这份表是前端与 `core/presets.py` 生成初始框的唯一真相源；两边改动必须同步。

---

## 六、吸附（F5）

拖动/缩放时，对每个「正在动的边」找最近目标：

| 目标 | 值 |
|---|---|
| 其它框的边 | 各自 `x0/x1/y0/y1` |
| 画布边 / 中线 / 三分线 | `0`, `1`, `0.5`, `1/3`, `2/3`（两轴） |
| 16px 网格 | 兜底：归一化步长 `16/canvas.width`、`16/canvas.height` |

- 阈值 **7 屏幕像素**（按当前 SVG 显示尺寸换算成归一化，缩放画布不会改变手感）。
- 移动：两条边都参与，取**位移绝对值最小**的那个目标；缩放：只有被拖的边参与。
- 命中时画黄色虚线；未命中任何目标才退 16px 网格。
- 纯函数 `snapAxis(values, targets, threshold, step)` 可单测（F6 覆盖中线/三分线/16px/远离不吸）。

---

## 七、调试入口

控制台（只读为主）：

```js
MOSS_SB_EDITOR.where()                 // {booted, nodes, mounted, ui:[{id,sel,items,fatal}]}
MOSS_SB_EDITOR.stateOf(node)           // 深拷贝 state
MOSS_SB_EDITOR.setState(node, obj)     // 写回并立即重绘（排障/造场景用）
MOSS_SB_EDITOR.presets_json("grid", {cols:3, rows:2})
MOSS_SB_EDITOR.graph()                 // app.graph（页面没有全局 app）
MOSS_SB_EDITOR.validate(obj)           // {errors:[…]}
MOSS_SB_EDITOR.stats(node)             // {renders, paints, items}（性能/回归用）
```

`MOSS_SB_EDITOR.setState` 会写 `editor_state`，只用于排障。

---

## 八、自检

### F6：静态 + 纯函数（入仓）

```bash
python tools/check_editor_ui.py
```

- **W 接线**：契约解析出的节点名 / widget 名 / 8 个 preset 与 JS 一致；
  `WEB_DIRECTORY` 存在；只 import 宿主 `app.js`；不写 `widgets_values`。
- **S 语法**：ESM 解析（Node ≥22 已移除 `--experimental-default-type` ⇒ 自动退 `.mjs` 副本）。
- **T 词表/纯函数**：把 `presetItems` / `validateState` / `readState` / `snapAxis` **真正执行**，
  覆盖 8 预设生成、数量、非重叠、格位、id 唯一、text 必带 text、7 条拒绝路径、宽容读取、吸附；
  另查预设说明 8 条中英齐全、类型色覆盖三类、画布预设都是 16 的倍数、英文校验/致命文案无 CJK；
  撤销栈（`makeHistory` / `recordHistory` / `stepHistory`）：撤销/重做顺序、新改动清空重做、
  同元素连续输入/微调合并为一步、不同元素与撤销后不合并、上限 100 步丢最旧。
- 总计 **68 项**。退出码：0 全过；1 有失败；2 没有 node（S/T 跳过，W 仍查）。

### 无头浏览器冒烟（未入仓，实测 38/38）

用运行时 stub 节点（不碰 Python 侧）在无头 Chromium 里验证：

- 挂载 / 藏 widget / state 读回 / DOM 容器连接
- 点选、8 把手、拖动改 bbox、缩放改 bbox、画新框
- 吸附到中线（实测 `x0 0.494 → 0.5`）
- 8 预设生成数量 `8/2/2/3/3/1/2/0`
- grid 只读（选中不给把手、拖不动）
- caption / label / text / palette / size 写入 `editor_state`
- 首屏欢迎 chip 生成、画布预设 9:16、grid 高级项 `panel_edge`、帮助开合
- 快捷键 `Delete` / 方向键 1px / `Ctrl+D` 复制
- 拖动性能：10 步移动期间 `paints +10`、`renders +0`（增量绘制，不重建整棵树）
- `serialize → 刷新页面 → 重新注册 stub → node.configure(widgets_values)` 后 state 逐字段相同
  （`configure` 就是宿主按 `widgets_values` 恢复 widget 的真实路径）

&#9888; 测试侧限制（不是编辑器缺陷）：
1. ComfyUI 会在 boot 之后异步载入默认工作流，晚到会冲掉测试节点；冒烟先等默认图载入再清空。
2. 节点已注册时 `createNode` 用的是真节点（默认 state=free），冒烟用 `setState()` 注入测试 state。

真实节点（0.2.0）已验证：`examples/moss-layout-editor-poster.json` 从文件加载后，
编辑器 UI 正常挂载、`editor_state` 被隐藏、`prompt/width/height` 三条连线正确、无页面错误。
真节点的 `widgets_values` 会额外带两个空串（DOM widget 的序列化占位），`editor_state` 仍是第 0 个。

### 真实宿主 grid e2e（2026-10-24）

`MossLayoutEditor`（2×2 / 16:9 / 496 / gutter 8 / margin 8）→ `MossStoryboardSampler`
（4 步蒸馏 LoRA / seed 555）→ 整张 **1024×576** + 逐格 **4 × 496×272**，18.0 s，
4 格内容互异；编辑器 `spec` 与旧 `MossStoryboardLayout` 逐字相同。
详见 `docs/08` §8.2。

### 撤销 / 重做真机验证（2026-10-24）

真实宿主里新建 `MossLayoutEditor`，title-body 预设 3 个元素：`Delete` → 2，`Ctrl+Z` → 3，
`Ctrl+Shift+Z` → 2，`↶` 按钮 → 3，`Ctrl+Y` → 2；caption 连打 3 次 = 1 步；方向键 3 次 = 1 步且撤销后
`bbox` 逐字还原；在 caption 输入框里按 `Ctrl+Z` 不动编辑器历史；所有按键都不冒泡到 `window`。

---

## 九、已知问题 / 待联调

| # | 事项 | 归属 |
|---|---|---|
| 1 | 宿主重启后复验 widget 顺序 | poster 示例已验（从文件加载 → 真节点挂载）；**宿主重启后**重跑 storyboard 示例 |
| 2 | Nodes 2.0 开关的兼容性 | 已验证：`Comfy.VueNodes.Enabled=true` 下编辑器挂载、3 个元素渲染、state 正确、无页面错误 |
| 3 | grid 模式没有「单格内部的元素框」 | 契约 v3 的 grid item = 格位；如需单格内分区，先改契约 |
| 4 | z 序 / 对齐均布 | P2（docs/09 §六），本版不做（撤销/重做已落地，见 §八） |
| 5 | 拖动性能只做了结构性优化，未测帧率/长列表 | 如遇大图卡顿，先量 `paintDrag` 的单帧耗时再优化 |
| 6 | `tools/check_frontend.py` 的 F2 在 Node 24 下的语法误报 | 已修：Node ≥22 自动退 `.mjs` 副本；当前 9/9 |

---

## 十、不做的事

- 不编译 prompt（state 是编译层的输入）。
- 不计算 `width/height`（单张的 canvas 是用户输入；网格只算显示比例）。
- 不写 `widgets_values`、不重排 widget。
- 不在 grid 模式拖格位（格位由几何算死）。
- 不引框架、不拆本地 module。
