# 官方 prompt-enhancer 草稿：接入与置信度

> 对应提交：`MossPromptCompiler`（0.2.x 未发布）+ `core/skill_draft.py` + `tools/compile_skill_draft.py`。
> 这里只写**本包怎么接**；官方 skill 的创作方法以其 SKILL.md 为准。

## 一、它是什么

OpenMOSS 在 `MOSS-Image` 仓库里发布了一个给 LLM agent 用的 skill：

- 路径：`skills/moss-image-prompt-enhancer/SKILL.md`
- 许可：仓库 Apache-2.0
- 作用：让 agent 把一句想法扩成 **caption**，或写成带 bbox 的 **layout 草稿**；
  再用它自带的 `scripts/compile_prompt.py` 编译成 MOSS 原生布局提示词。
- 它**不生成图片**，也不调用模型 API；只负责“把想法写成结构化描述 + 算坐标”。

草稿 schema（官方）：

```json
{
  "width": 1536,
  "height": 1024,
  "bbox_coord_space": "abs",
  "bbox_format": "xyxy",
  "summary": {"caption": "整体画面", "meta": ["poster"], "canvas": "single"},
  "items": [
    {"label": "object", "caption": "元素长什么样", "bbox": [176, 224, 800, 864]},
    {"label": "text", "caption": "字体/颜色/材质", "text": "实际渲染的字", "bbox": [928, 272, 1408, 448]}
  ]
}
```

- 草稿 bbox 是**左上原点、绝对像素、xyxy**；`width/height` 必须是 16 的倍数。
- 编译后变成 `abs_centralized` / `yxyx`，并去掉 `width/height`。
- 小数边：左边/上边 floor，右边/下边 ceil，再减画布中心。

## 二、本包怎么接

### 方式 A：直接粘贴官方编译结果

用官方 skill 得到 **编译后的 prompt**（一行 JSON 或纯 caption），直接接
`CLIPTextEncode.text` 或 `MossStoryboardSampler.positive`。本包不介入，零风险。

### 方式 B：用 `MOSS Prompt Compiler` 节点

把**草稿**（不是编译结果）粘进节点的 `draft`：

- `prompt` → `CLIPTextEncode.text` / 采样器 `positive`
- `state` → 布局编辑器的「导入」框（layout 草稿才有；可直接继续拖框/改字）
- `report` → 类型、画布、元素数、丢弃字段说明

相对官方脚本，本包多做了三件事：

1. **state v3 映射**：layout 草稿 → 编辑器 state（`preset=free`，bbox 归一化；
   画布须在编辑器范围 64–4096 内）。超出范围时节点仍输出 `prompt`，只把
   「state 未生成」和原因写进 `report`。
2. **上限**：最多 256 个元素、草稿文本最多 1,000,000 字符。
3. **中文报错**：未知字段、越界、非 16 倍数、重复键、NaN/Infinity 都指名道姓。

### 方式 C：命令行（批量 / 仓库内）

```bash
python tools/compile_skill_draft.py draft.json
python tools/compile_skill_draft.py draft.json --output prompt.txt
python tools/compile_skill_draft.py draft.json --output prompt.txt --state state.json
```

- 不带 `--output` 时 prompt 打到 stdout；报告打到 stderr。
- `--state` 只对 layout 草稿有效。
- 错误退出码 2；不覆盖输入文件。

## 三、置信度与边界

**已核（2026-10-26，离线）**：

- 官方 `compile_prompt.py` 的 8 项单元测试**全过**（`python -m unittest`）。
- 本包 `tests/test_skill_draft.py` **79 项全过**，含官方给的三个期望值：
  - 竖版 1536×1024 文字框 `[928,272,1408,448]` → `[-240,160,-64,640]`；
  - 物体框 `[176,224,800,864]` → `[-288,-592,352,32]`；
  - 小数边 `[10.9,20.1,100.01,200.99]`（768×1344）→ `[-652,-374,-471,-283]`。
- 本包实现与官方脚本**独立编写**（未复制代码）；schema 与转换规则按官方文档/测试对齐。

**未证 / 不承诺**：

- **没有在 GPU 上跑过**“用 skill 草稿生成的图是否更好”。本页只证明格式/坐标/状态转换正确，
  不证明模型质量。
- 不保证任意 LLM 都会按 skill 输出合法草稿；非法草稿会被本包拒绝，不会静默修正。
- state v3 表达不了的字段（`summary.meta` / `summary.canvas` / `summary.palette`、
  item `tags`、非 `#rrggbb` 的 palette）在 **state 输出**里会被丢弃并在 `report` 说明；
  **prompt 输出会完整保留**。
- 画布 <64 或 >4096 仍可 `compile` 出 prompt，但不能转 state（布局编辑器范围 64–4096）；
  节点会把原因写进 `report`，CLI 带 `--state` 时退出码 2。
- 256 元素上限是本包的保护值，不是官方限制；超过请拆画布。

## 四、出处

- 官方 skill：<https://github.com/OpenMOSS/MOSS-Image/tree/main/skills/moss-image-prompt-enhancer>
- 官方模型卡（采样口径）：<https://www.modelscope.cn/models/openmoss/MOSS-Image-SFT>
- 许可：Apache-2.0；本包只参考 schema 与转换规则，未复制其代码。
