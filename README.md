# ComfyUI-MOSS-Storyboard

给 **MOSS-Image** 用的故事板宫格节点包：一次采样出 3 / 4 / 6 / 8 / 9 宫格故事板，并直接输出逐格切片。

- 宿主：ComfyUI ≥ 0.42.0（MOSS-Image 原生支持自 PR #17095 起）
- 依赖：**零**额外 pip 依赖（`torch` / `comfy*` 由宿主提供）
- 许可：MIT（本包代码）。模型权重不随包分发，见 `THIRD-PARTY-NOTICES.md`

---

## 一、为什么不能直接把 Krea2 故事板工作流套到 MOSS 上

`⨝Krea2 turbo 生成4_6_8宫格故事板.json` 的机制是**两段式**：
`StoryboardGridGenerator` 画一张"黑格 + 白缝"的模板图 → `VAEEncode` → `KSampler(denoise 0.8)` 重绘 →
`StoryboardPanelCrop` 裁格。它成立的前提只有两个：模板能编码进 latent、denoise 留得住白缝。

这两条在 MOSS 上仍然成立，但**四处接线必须换**：

| 环节 | Krea2 工作流 | MOSS 必须改成 | 为什么 |
|---|---|---|---|
| 文本编码器 | `CLIPLoader(type=krea2)` + Qwen3-VL-4B | `CLIPLoader(type=moss_image)` + `qwen3.5_9b_int8_convrot` | 宿主按 `CLIPType.MOSS_IMAGE` 选 `comfy/text_encoders/moss_image.py`；type 写错不报错，只是嵌入错 |
| VAE | `Krea2-HD-vae-bf16` | `flux2-vae` | MOSS 的 latent 空间就是 FLUX.2 VAE（128 通道 / 1:16） |
| latent | `VAEEncode` 模板 + denoise 0.8 | 同左，**但画布必须被 16 整除** | 1/16 下采样，非整倍数会被 VAE 静默截断 ⇒ 裁切框与内容错位 |
| 采样参数 | 8 步 / cfg 1.1 / denoise 0.8 | 35 步 / cfg 1.0 / denoise 1.0（纯布局）或 0.85（混合） | 本机 `Moss BF16 官方节点.json` 实测可用的配置是 35 步 / cfg 1.0 |
| 布局约束 | 只有模板图 | 模板图 **或** MOSS 原生 JSON 布局框（bbox） | MOSS 有内建区域布局能力，比"靠图钉缝"更直接 |
| 宫格线 | 指望模型画直 | 解码后按几何坐标**重绘**一遍 | 模型画的线常歪、常断口；重绘是确定性的 |

还有一处**结构性事实**必须说明：**MOSS-Image 没有原生 img2img / 参考图条件化能力**
（官方技术报告 limitation）。本包的模板路线属于"latent 初始化式 img2img"——它在
flow-matching 模型上普遍可用，但对 MOSS 属**分布外用法，成功率和保真度尚无公开证据**。
所以本包把两条路线都做成可选，并把**原生布局框路线设为更默认的那条**。

---

## 二、整体思路（三段锁定）

```
              ┌──────────── 提示词层 ────────────┐
[STYLE] + [PANEL 1..n]  ──split_panels──► 全局描述 + n 段面板描述
                                              │
              ┌──────────── 几何层 ────────────┐│
格数 → 整数分解 → 挑最接近目标画布的 rows×cols → 每格坐标（16 对齐）
                                              ││
        ┌──── 前段：MOSS 原生 JSON 布局框 ─────┤│  summary + items[bbox]
        └──── 前段：宫格模板图（可选） ────────┘│  白缝 + 灰格，VAEEncode 当起点
                                              ▼
                                    KSampler / sample_custom
                                              │
        ┌──── 后段：按坐标复绘宫格线 ───────────┤  线宽/位置 100% 确定
        └──── 后段：按坐标裁格 ────────────────┘  行优先，顺序 = 叙事顺序
```

三层的分工是**故意分开的**：

1. **语义靠布局框**：`{"summary": …, "items": [{"label","caption","bbox"}]}` 直接告诉模型
   "第 3 格画特写"。这是 MOSS 的原生能力，不需要 ControlNet，不需要 img2img。
2. **几何靠模板（可选）**：白缝在 latent 里是强低频结构，低步数重绘也会被保住；但它只是
   *先验*，不是保证。
3. **确定性靠后段**：解码后按同一份 `Layout` 重绘线 + 裁格。**这一步不允许失败**，
   所以它不依赖模型、不依赖随机性、不做无提示的回退。

---

## 三、安装

```bash
# 方式 A：直接 clone（推荐，保持单一副本）
cd <ComfyUI>/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-MOSS-Storyboard

# 方式 B：本机开发态（同一份文件，不做副本 —— 副本会静默覆盖节点定义）
#   Windows: mklink /J <ComfyUI>\custom_nodes\ComfyUI-MOSS-Storyboard H:\ComfyUI-MOSS-Storyboard
```

重启后在节点菜单 `moss/storyboard` 下出现三个节点。启动日志应有一行：

```
[MOSS SB] v0.1.0 已加载｜节点 3 个（出口 v3）｜3/4/6/8/9 宫格
```

**这行没出现 = 包没加载**（比任何配置检查都直接）。要退回旧版 V1 出口：设环境变量 `MOSSSB_NODE_API=v1`。

---

## 四、三步跑通

1. `UNETLoader` → `moss_image_sft_bf16.safetensors`
2. `CLIPLoader` → `qwen3.5_9b_int8_convrot.safetensors`，**type = `moss_image`**
   ；`VAELoader` → `flux2-vae.safetensors`
3. 把故事板稿贴进 `MOSS Storyboard Sampler` 的 `positive`：

```
[STYLE]: 单一故事板画布分割为严格对称网格，恰好 8 个面板，…（全局风格/角色/环境/相机）
[PANEL 1]: 中景，两人对坐，她把柠檬水杯推向正中
[PANEL 2]: 特写，她的眼睫与玻璃雨痕
…
[PANEL 8]: 桌面特写，两只手停在杯沿
```

输出两路：`grid`（整张宫格）+ `panels`（8 张逐格）。示例工作流见 `examples/`：

- `moss-storyboard-8panels-layout.json` — 纯布局框，`denoise 1.0`
- `moss-storyboard-8panels-template.json` — 布局框 + 模板，`denoise 0.85`

---

## 五、宫格兼容方案

格数是输入，**排法不是写死的**：先做整数分解，再按目标画布比例挑最接近的一档，
极端比例（>4:1）优先排除。默认单格 16:9、长边 512、缝 8、外边距 8：

| 格数 | 单格 16:9 | 画布 | MP | 单格 9:16 | 画布 | MP |
|---|---|---|---|---|---|---|
| 3 | 3 行 × 1 列 | 528 × 896 | 0.47 | 1 行 × 3 列 | 896 × 528 | 0.47 |
| 4 | 2 × 2 | 1056 × 608 | 0.64 | 2 × 2 | 608 × 1056 | 0.64 |
| 6 | 2 行 × 3 列 | 1568 × 608 | 0.95 | 3 行 × 2 列 | 608 × 1568 | 0.95 |
| 8 | 2 行 × 4 列 | 2096 × 608 | 1.27 | 4 行 × 2 列 | 608 × 2096 | 1.27 |
| 9 | 3 × 3 | 1568 × 896 | 1.40 | 3 × 3 | 896 × 1568 | 1.40 |

（以上为 `make_layout` 实测输出，全部落在 MOSS 的 1–2MP 训练区间内。）

想强行换排法：把 `columns` 填成想要的列数（`rows` 留 0 自动补齐）。
3 格默认是竖条，是因为横排 3×16:9 会让画布到 5.2:1 —— 超过 4:1 的容忍线；
要横排就填 `columns=3`。

**为什么所有这些尺寸都能被 16 整除**：单格长边与短边都对齐 16，缝与外边距按 2 递增，
画布对齐差额（0–15px）**只塞进右/下外边距**——那里与宫格线同色，肉眼不可见，
单格尺寸因此永不被改动，`torch.cat` 也不会因为差 1px 崩掉。

---

## 六、节点

### MOSS Storyboard Layout（零模型）
算几何、出布局 JSON、出模板图。输出 `width / height / columns / rows / spec / layout_json / template / report`。

- `spec` 是**一根线传完所有几何**的字符串（`MOSS-SB1 panels=8 w=2096 h=608 cols=4 …`）。
  下游拿到它就绝不会和生成时错位。
- `layout_json` 与宿主 `MOSS-Image Layout Prompt` **同格式**，可直接接 `CLIPTextEncode`，
  也可以和 `Create Bounding Boxes` 混用。
- `report` 是人看的：格数/画布/MP/提示词对齐情况/超 MP 警告。

### MOSS Storyboard Sampler（主力）
`model + clip + vae + positive` 即可。内部依次做：段落拆分 → 布局 JSON → （可选）模板编码 →
采样 → 解码 → 复绘宫格线 → 裁格。输出 `grid / panels / latent / spec / prompt_used / report`。

关键参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `steps` / `cfg` | 35 / 1.0 | 本机 `Moss BF16 官方节点.json` **实测可用**的配置。官方文档的非蒸馏配方是 50 步 / cfg 3；要更高保真可试，代价是慢 |
| `init` | `auto` | `layout`（只布局框，最干净）/ `template`（只模板，仿 Krea2）/ `both` / `plain`（纯 txt2img）/ `auto`（接了模板就 both） |
| `denoise` | 1.0 | `init` 含 template 时必须 <1；0.75–0.90 是保宫格线的常用区间。1.0 + 模板 = 模板被完全覆盖（report 会出声） |
| `reborder` | True | 解码后重绘宫格线。关掉就只能指望模型把线画直 |
| `panel_edge` | 512 | 单格长边。整图 MP 随格数涨，8 格 512 ≈ 1.27MP |
| `negative` | 空 | ⚠ cfg=1.0 时负面分支不参与计算，这段等于没写 |

### MOSS Storyboard Crop（可选）
只裁格。接 `spec` 则与生成时逐像素一致；不接则要求 `columns × rows` 整除图像，否则**直接报错**
（不会像旧实现那样凑出差 1px 的格子再崩在 `torch.cat`）。

---

## 七、已知边界（读这一段再报 bug）

**已实测（有机器断言）**
- 3/4/6/8/9 格的几何、16 对齐、缝宽恒定、裁格顺序 —— `tests/test_core.py` 282 项
- 两条出口（V1/V3）都能注册、schema 入参名与顺序逐位一致、seed 控制槽一致 —— `tools/smoke_nodes.py` 25 项
- 示例工作流的连线自洽与 widget 槽位 —— `tools/check_ui_workflow.py` 24 项
- `Moss BF16 官方节点.json` 可正常出图（GG 实测）

**未实测（本包不声称）**
- **模板 img2img（`init=template` / `both`）在 MOSS 上的实际效果**。MOSS 无原生 img2img，
  这是分布外用法。已知的是这条路线在 Krea2 上成立。
- **布局框能否稳定换来"每格按要求构图"**。MOSS 的布局能力官方标为 *experimental*，
  且本项目**没有做过成片评测**。建议先跑 4 格小样，再用 8/9 格。
- 8 格以上每格有效像素只有 512×288，小字与细纹理最先崩——这是分辨率账，不是节点 bug。

**硬约束**
- 画布与单格必须 16 的倍数（`Layout` 已强制；手改 spec 会被 `parse_spec` 拒绝）。
- 整图 >2MP 会 warn，>4MP 会强 warn：MOSS 训练区间是 1–2MP，官方外推上限 ≈4MP。
- 4.7MP（旧 Krea2 工作流的 4096×1152）在 MOSS 上属于外推区之外，本包不设为默认。

---

## 八、机检四条命令

```bash
python tests/test_core.py          # 几何/提示词/像素，零 GPU，282 项
python tools/smoke_nodes.py        # 自加载：V1+V3 注册、schema、纯节点真跑，25 项
python tools/check_ui_workflow.py  # 示例工作流连线 + widget 槽位，24 项
python tools/host_loader_check.py  # 走宿主自己的 load_custom_node，16 项
```

四条都应是「失败 0」。**不要用 `| tail -n` 看这些脚本**——退出码会被吃掉。

第四条是**最关键的一条**：它调用 ComfyUI 启动时扫 `custom_nodes` 用的同一个函数
`nodes.load_custom_node()`，再查 `nodes.NODE_CLASS_MAPPINGS` 里是否真的注册了三个 node_id。
前三条只能证明"包能 import"，证明不了"宿主真的调你"。

---

## 九、目录结构

```
ComfyUI-MOSS-Storyboard/
├── __init__.py              双出口（V3 默认，V1 一行回退）+ 加载摘要
├── nodes.py                 V1 节点契约（唯一真相源）+ _node_errors
├── core/
│   ├── layout.py            宫格几何：整数分解、16 对齐、spec 编解码   （纯 Python）
│   ├── prompt.py            [STYLE]/[PANEL n] 拆分 + MOSS 布局 JSON    （纯 Python）
│   ├── pixels.py            模板渲染、边框复绘、精确裁格、校准预览      （仅 torch）
│   └── sampling.py          空 latent / 模板编码 / 文本编码 / 单次采样   （宿主 API）
├── v3/                      V3 外壳：schema 由 INPUT_TYPES 自动生成
│   ├── _compat.py           全包唯一 import comfy_api 的地方
│   ├── nodes_v3.py          三个空壳类 + NODES 清单
│   └── entrypoint.py        comfy_entrypoint（逐节点 schema 预检）
├── tests/test_core.py       零 GPU 回归
├── tools/                   smoke_nodes / check_ui_workflow / make_example
├── examples/                两个可直接载入的工作流
├── docs/01-layout-and-panels.md   几何数学、spec 格式、旧实现缺陷表
├── pyproject.toml           metadata + requires-comfyui
├── LICENSE / THIRD-PARTY-NOTICES.md / CHANGES.md
└── requirements.txt         只有注释（零依赖）
```

---

## 十、出处与许可

- 本包代码 MIT。
- 思路参考与代码归属说明见 `THIRD-PARTY-NOTICES.md`（**未复制**任何第三方代码）。
- 模型权重（MOSS-Image / Qwen3.5 / FLUX.2 VAE）不随包分发，使用者自行遵守其许可。
- 运行时宿主 ComfyUI 为 GPL-3.0，本包只在进程内 `import`，不复制其源码。
