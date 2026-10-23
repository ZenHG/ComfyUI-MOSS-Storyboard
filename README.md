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
极端比例（>4:1）优先排除；比例近打平时按故事板惯例优先「横条」（列 > 行）。
下表是单格 16:9、长边 **496**（交付工作流的 1024 档）、缝 8、外边距 8：

| 格数 | 单格 16:9 排法 | 画布 | MP | 单格 9:16 排法 | 画布 | MP |
|---|---|---|---|---|---|---|
| 3 | 3 行 × 1 列 | 512 × 848 | 0.43 | 1 行 × 3 列 | 848 × 512 | 0.43 |
| 4 | 2 行 × 2 列 | **1024 × 576** | 0.59 | 2 行 × 2 列 | 576 × 1024 | 0.59 |
| 6 | 2 行 × 3 列 | 1520 × 576 | 0.88 | 3 行 × 2 列 | 576 × 1520 | 0.88 |
| 8 | 2 行 × 4 列 | 2032 × 576 | 1.17 | 4 行 × 2 列 | 576 × 2032 | 1.17 |
| 9 | 3 行 × 3 列 | 1520 × 848 | 1.29 | 3 行 × 3 列 | 848 × 1520 | 1.29 |

（以上由 `make_layout` 实测输出，全部落在 MOSS 的 1–2MP 训练区间内。
节点默认 `panel_edge=512`，4 格 → 1056×608；交付工作流用 496 是为了让 4 格正好 1024×576。）

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

## 七、中英双语与悬浮提示

节点标题、参数标签、枚举显示、悬浮提示都随**宿主界面语言**切换，右下角有个「中 / EN」按钮可手动覆盖（存 localStorage）。

| 层 | 谁负责 | 规则 |
|---|---|---|
| 节点标题 | `web/moss_sb_i18n.js::NODES` | 后端 `NODE_DISPLAY_NAME_MAPPINGS` 存**英文**；中文由前端补。只换**前缀**，用户自定义标题与后缀一律保留 |
| 参数标签 | `LABELS` | 只存中文；英文回落到 widget 原名（原名本身是英文） |
| 多行文本框标题 | `LABELS` → `element.placeholder` | ⚠ **不是 `label`**。DOM `<textarea>` 撑满控件，前端不画 label（见下） |
| 枚举显示 | `COMBOS` + `options.getOptionLabel` | **只改显示**，存档值不变；白名单外不翻 |
| 悬浮提示 | `TIPS`（**只存 `en`**） | 中文唯一源是 `nodes.py` 的 `tooltip`；切中文时**一个字节都不写**，前端回落后端原文 |

**多行文本框（提示词框）的标题是 `placeholder`，不是 `label`。**
真机实证（前端 1.57.1）：`widget.label` 已写成「正面提示词」，界面上仍显示 `placeholder="positive"`，
因为 DOM `<textarea class="comfy-multiline-input">` 的父容器是 `size-full`，把画布自绘的标题整个盖住了。
所以这两个框必须**同时**写 `label` 与 `placeholder`；只写 label 的改法对它们永远无效。
（`tests/test_i18n.mjs` 6.20–6.23 锁这条。）

**负面提示词默认收在「高级」里**，不是忘了写。MOSS 实测配方是 `cfg=1.0`，而宿主在 cfg=1 时
直接跳过负面分支（`comfy/samplers.py::sampling_function`：`if math.isclose(cond_scale, 1.0): uncond_ = None`），
那段**不进计算图**。放在普通区只会让人以为"写了就生效"。
要让它出现：展开节点上的「高级」；要让它生效：`cfg` 调到 >1。

四条铁律（与 H 仓 `ComfyUI-H3-Latent-Relay` 同一套，每条对应一次事故）：

1. **只改 `label` / `title` / `tooltip` / `placeholder`，绝不改 `name`** —— `name` 是后端取参与序列化的依据。
2. **绝不改 `widget.options.values`** —— 那是存档值（模型文件名、枚举值），翻了就是改图。
3. **用户的自定义标题不许覆盖**，且只替换默认名前缀（与其它模块追加的提示共存）。
4. **中文只写一份**（后端）。词表里多一份 `zh` 必然与后端漂开——历史上就是这样漂的。

为什么中文提示要放在后端：`w.tooltip` 只是一次性覆盖，序列化只写 `widget.value`，
所以提示翻不了图、分享出去也不污染别人的工作流 JSON。

**抗覆盖**：装了界面汉化插件（ComfyUI-Chinese-Translation 等）时，它有守护轮询会按自己的
词典把我们的 label/tooltip 改回中文。所以本包给每个改过的 widget 打内存标记
（`__mosssbWant` / `__mosssbWantTip`），每 1.5 秒比对一次，不一致就写回。

**两个切换入口**（任一改动，另一处立即跟随）：

1. **设置面板**（主入口）：`Settings` → `MOSS Storyboard · 节点语言 / Node language` → 跟随界面 / 中文 / English。
2. **右下角悬浮按钮**：`中` ↔ `EN`，z-index 已压过画布浮层。

**看不到入口时先看控制台**（这两行能区分"没加载"和"看不见"）：

```
[MOSS SB] i18n: 模块已加载（v0.1.0）
[MOSS SB] i18n: 就绪（lang=zh，设置项=有，节点=3）
```

只有第一行没有第二行 ⇒ `setup()` 抛错（控制台会有 `setup 异常` 与堆栈）。
两行都没有 ⇒ 前端扩展没被加载，查 `curl -s http://127.0.0.1:8188/extensions | grep MOSS-Storyboard`。
两行都有但看不到按钮 ⇒ 在控制台敲 `MOSS_SB_LANG.where()`，返回 `button: true` 说明是遮挡问题，
`button: false` 说明挂载失败（会有 `按钮挂载失败` 警告）。

**没有前端也能用**：所有文案的后端版本是**英文**（`NODE_DISPLAY_NAME_MAPPINGS` +
`DESCRIPTION`），JS 加载失败只是没有中文，不会出现空标题或报错。

---

## 八、已知边界（读这一段再报 bug）

**已实测（有机器断言，证据见 `docs/02-e2e-matrix.md`）**

| 项 | 结论 | 量 |
|---|---|---|
| 3/4/6/8/9 格 · 横竖屏 · 无缝 · 粗缝 · 小画布 · plain 路线 | 全部真跑成功，尺寸与理论值逐像素一致 | 15 次真跑 |
| 宫格线 | 复绘后缝/外框为纯白（采样点 ≥235） | 每用例 |
| 随机种子 | 同参数换种子 → 整图 md5 不同 | 2 次对照 |
| 几何/提示词/像素 | 282 项断言 | 零 GPU |
| 参数表 · 双出口 · 错误路径 · 依赖边界 · 仓库卫生 | 50 项断言 | 零 GPU |
| 采样 API 契约（latent 必须是张量） | 18 项断言 | 零 GPU |
| 宿主真加载 | `nodes.load_custom_node()` 注册出 3 个 node_id | 16 项 |

**0.1.0 实测：模板 img2img 路线不生效（后续版本会再验证）**
- 🔴 **模板 img2img 路线（`init=template` / `both`）在 MOSS 上不成立。**
  三组独立复现：35 步 / denoise 0.85 → 纯黑；12 步 / 0.6 → 模板残影；4 步 / 0.5 → 模板残影。
  只要 sigma 真截断（真 img2img），模型就不改动 latent 或直接崩成黑。这与官方
  limitation「不直接以参考图条件化、不支持编辑」一致。**所以默认 `init=auto = 纯布局路线`**。
- ⚠ **低步数下 `denoise` 会被静默吃掉**：宿主按 `int(steps/denoise)` 重算 sigma，
  4 步时 `int(4/0.85)=4` ⇒ 一段都没截，等价 denoise=1.0。`report` 会打印
  「denoise 名义 x / 实际 y」，以实际值为准。

**未实测（本包不声称）**
- 布局框能否**稳定**换来"每格按要求构图"：MOSS 的布局能力官方标 *experimental*，
  本项目只验证了"能出、尺寸对、格子和描述大体对应"，**没有做成片评分**。
- 8/9 格每格有效像素只有 496×272（交付档），小字与细纹理最先崩——这是分辨率账，不是节点 bug。

**硬约束**
- 画布与单格必须 16 的倍数（`Layout` 已强制；手改 spec 会被 `parse_spec` 拒绝）。
- 整图 >2MP 会 warn，>4MP 会强 warn：MOSS 训练区间 1–2MP，官方外推上限 ≈4MP。

---

## 九、机检七条命令

```bash
python tests/test_core.py              # 几何/提示词/像素，零 GPU，282 项
python tests/test_contract.py          # 参数表/错误路径/依赖/卫生/词表完整性，零 GPU，54 项
python tests/test_sampling_contract.py # 采样 API 契约（latent 必须是张量），零 GPU，18 项
node   tests/test_i18n.mjs             # 中英切换纯函数，46 项
python tools/smoke_nodes.py            # 自加载 + 纯节点真跑，25 项
python tools/host_loader_check.py      # 走宿主自己的 load_custom_node，16 项
python tools/check_ui_workflow.py      # 工作流连线 + widget 槽位（15 项/文件）
python tools/check_frontend.py         # 前端词表纯净度/语法/接线/标题可换语言

# 可选诊断（**不作为闸**）：本机无头浏览器没有 WebGL，画布渲染不全，结果仅供参考
node   tools/ui_smoke.mjs
python tools/e2e_matrix.py             # 端到端 15 次真跑（要 GPU + 模型）
```

全部应是「失败 0」。**不要用 `| tail -n` 看这些脚本**——退出码会被吃掉。

三条不可替代：
- `host_loader_check.py` 调的是 ComfyUI 启动时扫 `custom_nodes` 的**同一个函数**，
  查的是 `nodes.NODE_CLASS_MAPPINGS` ——「配置写对 ≠ 宿主调你」的唯一证据。
- `e2e_matrix.py` 是唯一能证明"别人机器上也能出图且尺寸对"的东西；`--dry` 只能证明图能被提交。
- `test_contract.py` 的「每个参数都有英文提示」那一项，是**新增参数忘了翻译**的唯一拦截点。

---

## 十、目录结构

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
├── web/                     前端扩展（中英切换）
│   ├── moss_sb_i18n.js      词表 + 纯函数（可 node 单测，零 DOM）
│   └── moss_sb_i18n_ui.js   挂钩：读宿主语言 / 按钮 / 抗覆盖兜底
├── tests/test_core.py       零 GPU 回归
├── tests/test_i18n.mjs      中英切换纯函数单测（node）
├── tools/                   smoke_nodes / check_ui_workflow / make_example
├── examples/                两个可直接载入的工作流
├── docs/01-layout-and-panels.md   几何数学、spec 格式、旧实现缺陷表
├── pyproject.toml           metadata + requires-comfyui
├── LICENSE / THIRD-PARTY-NOTICES.md / CHANGES.md
└── requirements.txt         只有注释（零依赖）
```

---

## 十一、出处与许可

- 本包代码 MIT。
- 思路参考与代码归属说明见 `THIRD-PARTY-NOTICES.md`（**未复制**任何第三方代码）。
- 模型权重（MOSS-Image / Qwen3.5 / FLUX.2 VAE）不随包分发，使用者自行遵守其许可。
- 运行时宿主 ComfyUI 为 GPL-3.0，本包只在进程内 `import`，不复制其源码。
