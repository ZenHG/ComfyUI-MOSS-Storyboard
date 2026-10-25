# 12 · 互操作：elements-v1 ⇄ state v3

> **状态**：v1 已冻结。外部 planner / 脚本按本文产出 JSON，即可驱动本仓编辑器与节点；
> 实现 = `core/interop.py`，命令行 = `tools/check_elements.py`。

## 一、为什么有这一层

编辑器的 `editor_state` 是 v3 契约（`docs/10`），它解决「同一份状态怎么确定性编译」。
但外部工具（分镜 planner、排版脚本、别的编辑器）不该被迫复刻 v3 的全部字段。
这一层只回答一件事：**谁在哪个框里**。

- 几何仍然只走 `core.layout.build_layout()`；本层不产 `spec`。
- 本层**不调用模型、不联网、不依赖宿主**，可以单独在命令行跑。
- 宫格（`grid`）**不支持**导入：宫格的 item 是格位，不是元素；硬映射会误导。

## 二、文档格式

```json
{
  "version": 1,
  "canvas": { "width": 1536, "height": 864 },
  "style": "电影感，暖黄吊灯，浅景深",
  "elements": [
    {
      "id": "title",
      "role": "text",
      "desc": "粗体无衬线标题，浅金色",
      "text": "夜雨咖啡馆",
      "bbox": [80, 100, 920, 190],
      "palette": ["#c8a15a"]
    },
    {
      "role": "character",
      "desc": "主角半身，侧对镜头",
      "bbox": [100, 240, 900, 800]
    }
  ]
}
```

### 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `version` | ✅ | 固定 `1`；读到别的值直接报错 |
| `elements` | ✅ | 数组，最多 256 个 |
| `canvas` | ❌ | `{width,height}`；默认 `1536×864`；64..4096 且为 16 的倍数 |
| `style` | ❌ | 全局风格段（映射到 `state.style`）；命令行 `--style` 可覆盖 |
| `x_ext` | ❌ | 透传容器；不认识的**文档级**字段会自动收进这里；`__proto__` / `constructor` / `prototype` 会被忽略（防原型污染） |
| `elements[].role` | ✅ | `text` / `object` / `character`；别名 `obj`→object、`person`→character；可用 `type` 代替 |
| `elements[].desc` | ✅ | 这个元素**长什么样**；映射到 `caption`；`caption` 可作别名 |
| `elements[].text` | text 必填 | **实际渲染的字**；`desc` 不能当 `text` 用；非 text 元素带 `text` 直接报错 |
| `elements[].bbox` | ✅ | `[x0,y0,x1,y1]`，单位 **0–1000**，要求 `x0<x1`、`y0<y1` |
| `elements[].palette` | ❌ | ≤5 个 `#rrggbb` |
| `elements[].id` | ❌ | 非空字符串；缺省按 role 生成（`t1/o1/c1…`）；重复或类型错直接报错 |
| `elements[].z` | ❌ | 数字；升序排列，同值保持数组顺序；缺省按数组下标 |
| `elements[].x_ext` | ❌ | 透传容器；不认识的**元素级**字段会自动收进这里；危险键同上会被忽略 |

### 单位与精度

- bbox 只认 **0–1000**，不做 0–1 自动识别：`[0,0,1,1]` 在两种单位下有歧义。
  0–1 的坐标请先乘 1000。
- 导入：`v / 1000`，不做额外取整。
- 导出：`v × 1000` 后保留 **3 位小数**（约 0.001 精度，1024 宽画布上约 1 px）。
  因此 state 里超过 3 位小数的 bbox 在往返后会轻微量化，这是**有文档的损失**。

## 三、映射到 state v3

| elements-v1 | state v3 | 说明 |
|---|---|---|
| `role` / `type` | `items[].label` | 别名归一后写入 |
| `desc` / `caption` | `items[].caption` | 原样保留 |
| `text` | `items[].text` | 仅 text 元素 |
| `palette` | `items[].palette` | 原样保留 |
| `bbox` / 1000 | `items[].bbox` | 归一化 0–1 |
| `id` | `items[].id` | 合法且唯一则保留 |
| `z` / 数组顺序 | `items[]` 顺序 | 状态里数组顺序即层叠顺序 |
| `canvas` | `canvas` | 单张模式 |
| `style` | `style` | 全局段 |
| `x_ext` | `x_ext` | 透传，编译层忽略 |

映射结果**必须**再过 `core.editor.parse_state()`：本文不另立一套校验。
因此 `items` 的 `label/caption/bbox` 等仍以 `docs/10` 为准。

### 往返保证

| 方向 | 保证 |
|---|---|
| elements → state → elements | 角色 / 描述 / 文本 / 颜色 / 顺序不变；生成的 id 保留；未知字段进 `x_ext` 不丢；bbox 输入为整数时逐字还原 |
| state → elements → state | id / label / caption / text / palette / 顺序 / `x_ext` 不变；bbox 为 0.001 的整数倍时逐字还原 |
| 明确损失 | 导出不写 `z`（顺序即 z）；canvas 上的未知键不保留；宫格不能导出 |

## 四、命令行

外部工具有两种用法：先验一遍，或直接转成 `state` 灌给节点。
编辑器工具栏也有「导入 / 导出」（只支持单张模式；导入后是一次可撤销的提交）。

```bash
python tools/check_elements.py plan.json                  # 校验 + 摘要（人读）
python tools/check_elements.py plan.json --emit state     # 输出 state v3 JSON（可管道）
python tools/check_elements.py state.json --in-format state --emit elements
cat plan.json | python tools/check_elements.py -          # 从 stdin 读
python tools/check_elements.py planner.md --extract       # 从散文/代码块里取 JSON
python tools/check_elements.py a.json b.json c.json       # 批量校验
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--preset` | `free` | 单张模式预设；`grid` 会被拒绝 |
| `--style` | 文档里的值 | 覆盖全局风格段 |
| `--emit` | 无（只打印摘要） | `state` / `elements`；一次只能处理一个文件 |
| `--in-format` | `auto` | `auto` / `elements` / `state` |
| `--extract` | 关 | 输入不是纯 JSON 时，从散文/代码块里提取第一个 JSON 对象/数组；默认严格 |

退出码：`0` 全过 · `1` 内容不合法 · `2` 读不到文件或参数错误。

## 五、已冻结的取舍

1. **不接受 0–1 自动识别**：歧义比方便更危险；换算是一次乘法。
2. **非 text 元素带 `text` 直接报错**：不静默丢字段；要渲染文字请用 `role: text`。
3. **id 重复直接报错**：不自动改名，避免 planner 的引用悄悄失效。
4. **宫格不导入**：格位不是元素；等有真实需求再设计「元素 → 格位」规则。
5. **状态里保留 `x_ext`**：v3 编译层与前端渲染都会忽略它，仅用于往返不丢数据。
   它不是 v3 契约字段，不承诺被其它消费者使用。

## 六、brief → elements（确定性模板）

不想手写 elements 时，可以只写一张「说什么」的 brief，由模板落到 `title-body` 版式。
**不接 LLM、不联网**；几何只来自 `core/presets.py`，与编辑器预设同一真相源。

```json
{
  "title": "夜雨咖啡馆",
  "subtitle": "第一集",
  "subject": "主角半身，侧对镜头",
  "style": "电影感，暖黄吊灯",
  "canvas": "16:9",
  "palette": ["#c8a15a"],
  "title_desc": "粗体无衬线标题",
  "bottom_desc": "细体副标题"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | ✅ | 标题文字 |
| `subtitle` / `body` | ❌ | 底部文字块；**二选一**（只有一个框） |
| `subject` | ❌ | 主体外观描述；给了才生成人物框 |
| `style` | ❌ | 全局风格段 |
| `canvas` | ❌ | `16:9` / `9:16` / `1:1` / `4:3` / `3:4`，或 `{width,height}`；默认 16:9 |
| `palette` | ❌ | 覆盖文字颜色（≤5 个 `#rrggbb`） |
| `title_desc` / `bottom_desc` | ❌ | 覆盖文字的外观描述（默认取预设） |
| `layout` | ❌ | v1 只接受 `title-body`；其它版式请在编辑器里选预设 |

```bash
python tools/brief_to_elements.py brief.json            # 输出 elements
python tools/brief_to_elements.py brief.json --state    # 输出 state v3
```

退出码与 `check_elements.py` 一致（0 / 1 / 2）。同一份 brief 永远产出同一份 elements。

## 七、版本策略

- `elements.version` 与 `state.version` **各自独立**；本版固定为 `1`。
- 未来改格式时新增 `version` 与迁移器，**不允许静默猜测**（与 `docs/10` 的 state 策略一致）。
- 新增可选字段不算破坏；改字段含义 / 改单位 / 改 role 集合算破坏，必须升版本。
