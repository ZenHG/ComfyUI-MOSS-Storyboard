# ComfyUI-MOSS-Storyboard

🌐 [English](README.md) · **中文**

![MOSS Storyboard：可视化布局编辑器、一次生成 4 格故事板、逐格精确裁切](assets/hero-storyboard.png)

**给 ComfyUI 用的故事板、局部编辑与 LLM 提示词节点。** 三个用途：

- **故事板** —— 一张画布分成 3 / 4 / 6 / 8 / 9 格，每格一段描述，同时把每格裁出来。
- **局部编辑** —— 只改一格、或把一张脸换进一格；改完贴回，其它格逐像素不动。
- **提示词** —— 可选 LLM 节点做反推 / 扩写 / 编辑 / 拼装；**不绑定 MOSS**，也支持 Flux、SDXL、Qwen-Image。
  参考图最多 9 张（接满一槽自动长出下一槽）；`target=minimax-h3` 时反推 / 扩写直接出音视频 H3 格式。

> 🔴 **先记这一句：画布上的框是参考，不是坐标。** 框会变成一句带坐标的文字进提示词，模型自己决定画在哪。
> 会画偏、会揉框、也可能完全不理。细节见[节点手册 §1](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/19-node-manual.md)。

| 项 | 值 |
|---|---|
| 版本 | **0.5.2** |
| 许可 | **MIT**（第三方出处见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)） |
| 宿主 | **ComfyUI ≥ 0.42.0**（原生支持 MOSS-Image） |
| 依赖 | 核心**零**额外 pip 依赖。换脸自动蒙版**默认开**，需 `insightface` + `onnxruntime` **以及** `det_10g.onnx` 权重（默认 `~/.insightface/models/buffalo_l/`，可用 `MOSS_INSIGHTFACE_ROOT` 改目录）；缺任一项请手接 `mask` 或关掉 `auto_mask`，节点会明确报错且不会联网下载 |

**本包保证**：画布尺寸、每格坐标、裁切位置、宫格线位置 —— 算术，逐像素对齐。
**本包不保证**：画面内容、元素落点、跨格是不是同一张脸 —— 由模型采样决定。

---

## 1 · 快速开始

### 1.1 安装

| 路线 | 怎么做 |
|---|---|
| **Manager**（推荐） | 搜 **MOSS Storyboard** → 安装 → 重启 ComfyUI |
| **Registry** | 打开 `https://registry.comfy.org/nodes/moss-storyboard` → Install |
| **手动** | `cd <ComfyUI>/custom_nodes` → `git clone https://github.com/ZenHG/ComfyUI-MOSS-Storyboard` → 重启 |

成功的标志是启动日志里这一行：

```
[MOSS SB] v<版本号> 已加载｜节点 7 个（出口 v3）｜3/4/6/8/9 宫格
```

没有这一行 = 包没加载。手动装最常见的坑是**多套了一层目录**
（`custom_nodes/ComfyUI-MOSS-Storyboard/ComfyUI-MOSS-Storyboard/`）。

### 1.2 权重（不随包分发）

从官方教程下载三个文件：<https://docs.comfy.org/tutorials/image/moss-image/moss-image>，
按下面目录放好，然后**重启 ComfyUI**：

| 文件 | 放哪 |
|---|---|
| `moss_image_sft_bf16.safetensors` | `<ComfyUI>/models/diffusion_models/`（旧版宿主：`models/unet/`） |
| `qwen3.5_9b_int8_convrot.safetensors` | `<ComfyUI>/models/text_encoders/` |
| `flux2-vae.safetensors` | `<ComfyUI>/models/vae/` |

- 可选蒸馏 LoRA：任意 `*.safetensors` → `<ComfyUI>/models/loras/`。不叠它用 **50 步 / cfg 3 / euler / sgm_uniform**；
  叠 4 步 LoRA 用 **4 步 / cfg 1.0**。
- **LLM 节点**：`assemble` **不需要任何模型**，`api` 通道也不需要本地权重。走 `local` 时，GGUF 放
  `<ComfyUI>/models/LLM/` 或 `<ComfyUI>/models/text_encoders/` 下任意子目录（两处都会递归扫描）。`caption` / `edit`
  要视觉模型 **+** 同目录的 `mmproj`；`expand` 只要文本模型。

### 1.3 第一次运行

- **最快自检（零权重）**：把 [`examples/llm-compile-zero-model.json`](examples/llm-compile-zero-model.json) 拖进 ComfyUI，按 Run。
  毫秒级把 IR 拼装成提示词，能跑通就说明包装对了。
  想看 H3 全参考六段式：把 `target` 设成 `minimax-h3`、`profile` 填 `six-section`，
  同一份 IR 就从三字段基础模式切到六段式；两者都是零模型、毫秒级、可复现。
- **让模型把想法扩写成 H3 格式**：任务选「扩写」，`target` 选 `minimax-h3` 即出音视频
  三字段时间线；再填 `profile=six-section` 出全参考六段式。方言表不用动。
- **多图参考（最多 9 张）**：`image` 之后接满一个槽就自动多出一个。`target=minimax-h3` 时
  多图**融合**成一条 H3 稿（每张图各定义一个 `<Subject N>`）；其它目标则**逐张**反推，
  输出用 `--- image N ---` 分段，各条互不影响。多图会按 token 预算自动缩小长边；
  上下文或 `max_tokens` 不够时，节点在**加载权重之前**就报错，并给出该改哪个值。
- **第一张故事板**：拖 [`examples/moss-storyboard-4panels-ui.json`](examples/moss-storyboard-4panels-ui.json)，按 Run。
  产物在 `output/moss/storyboard/`（`grid_*.png` 与 `panel_*.png`）。
- **第一段提示词**：拖 [`examples/llm-prompt-workbench.json`](examples/llm-prompt-workbench.json)。默认只开反推组，
  其余组已旁路（`Ctrl+B` 可启用）。
- **LLM 面板（0.5.2）**：顶部一张状态卡写明当前在做什么、还缺什么；「做什么」「用哪个」
  是分段按钮。**参数 18 项**摆在明面，其余收进「高级」，老工作流存档格数与顺序不变。
  **风格 16 档**分四类：连贯描述、9 种风格（写实摄影 / 电影剧照 / 胶片复古 / 暗调氛围 /
  商业广告 / 二次元插画 / 水彩 / 赛博朋克 / 黑白纪实）、自定义（配一行风格备注）、标签串、
  分段结构（宫格稿 / 时间线六段式）。预设仍是**嵌套**：展开「预设」后先选「连接」
  （DeepSeek / OpenCode / CoRouter / Ollama / LM Studio）或「采样」（草稿 / 精修 / 恢复默认）。
  温度一个旋钮管一组采样，`top_p` / `min_p` 不用再单独调。

### 1.4 下一步

- 全部入参 / 输出：[`docs/19-node-manual.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/19-node-manual.md)。
- LLM 节点（通道、方言）：[`docs/20-llm-prompt-nodes.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/20-llm-prompt-nodes.md)。PE 改写器见 [`docs/19-node-manual.md` §9](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/19-node-manual.md)；怎么跑起来见下面 1.6。
- 更多示例：见下面 3.1。

### 1.5 怎么接线

| 加载节点 | 填哪个文件 | 插到「故事板 · 生成」的哪个口 |
|---|---|---|
| `UNETLoader` | `moss_image_sft_bf16.safetensors` | `model` |
| `CLIPLoader` | `qwen3.5_9b_int8_convrot.safetensors`（`type=moss_image`） | `clip` |
| `VAELoader` | `flux2-vae.safetensors` | `vae` |

> **换脸节点另有一套配方。** 它默认 `8 步 / cfg 1.0 / simple`，属 8 步蒸馏档。要在模型前串上 8 步 DMD2 蒸馏 LoRA **与** Moss-Image-Edit 编辑 LoRA。照上表接裸 `UNETLoader` 又用默认步数，出图会糊、会变成另一张脸；节点不检测 LoRA。接线见 `examples/moss-faceswap-ui.json`。

### 1.6 PE 改写器（Qwen-Image 2.1，非商用）

可选 LLM 提示词节点可以把提示词交给 Qwen-Image 2.1 的**原生** PE system prompt，而不是用本包的提示词。
只有所选 GGUF 同目录存在匹配的 `system_prompt.txt` 时才启用 —— 没有 PE 开关、不下载、不改默认行为。

**先说许可。** PE 权重（官方 `Qwen/Qwen-Image-2.1-PE-T2I` / `-PE-I2I` 与社区 GGUF）按
`qwen-research` 发布：**非商用、仅内部使用**。本包是 MIT，**不含任何权重，也不含 `system_prompt.txt`**；
请自备，商用前自行核对上游许可，不要提交或随包分发。

跑通 PE-I2I（参考图 + 编辑指令 → 精确编辑提示词）：

1. 把 PE-I2I GGUF、配套的 `mmproj` 视觉塔、原生 `system_prompt.txt`（须含 `ratio_follow`
   或 `rewritten editing instruction`）放在 `<ComfyUI>/models/LLM/` 下同一个目录里，三个文件都自备。
2. 把 [`examples/llm-prompt-workbench.json`](examples/llm-prompt-workbench.json) 拖进 ComfyUI，
   按 Ctrl+B 取消第 ④ 组旁路，在该组节点的 `model` 下拉里选这个 GGUF，`mmproj` 保持自动
   （或手选配对的视觉塔）。其余三组保持旁路。
3. `LoadImage` 选你的图，`text` 写编辑指令，`task` 保持 `edit`，按 Run。
4. 第二个输出 `report` 出现 `PE 改写器模式（编辑）` 即已命中；`dialect`、`length`、`must_keep`、
   `panels`、`style_lock` 本次被绕过，report 会逐一点名。

PE-T2I（`task=expand`）另需 T2I 提示词与权重：代码分支在，但本包既不随包分发、也不安装任何 T2I 权重。
`caption` / `assemble` 与 `api` 通道永不进入 PE。详见 [`docs/19-node-manual.md` §9](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/19-node-manual.md)。

## 2 · 常见问题

### 2.1 权重不在下拉里

文件放错目录，或 ComfyUI 没重启。对照 1.2 的路径检查，然后重启。

### 2.2 「找不到 llama-server 可执行文件」（只影响 LLM 节点）

核心故事板节点**不需要**它；启动日志那行警告不影响使用。三条路任选：

1. **一键装**：展开节点 **本地引擎** 组，点参数条上的 **安装/更新**。下载与解压都走 ComfyUI 自己的进度条。
   落在 `<custom_nodes>/ComfyUI-MOSS-Storyboard/moss_llm/runtime/`；设 `MOSS_LLM_RUNTIME_DIR` 可改到持久目录。
   旁边的 **测试** 只跑 `--version`，不加载模型就能确认这个 exe 能不能用。
2. **已有二进制**：把 `runtime` 选 **自定义路径**，在 `server_exe` 填路径；或设 `MOSS_LLM_SERVER`。
   其余 `runtime` 取值下 `server_exe` 不生效——一个决定只由一个控件说。
3. **已有 OpenAI 兼容服务**（LM Studio / Ollama / llama-swap / 远端）：`channel` 选 `api`，
   填 `api_base`（要带 `/v1`）和 `api_model`。

端口只是首选值：被占用会自动往后找空闲端口。只有同一模型实例才复用；端口上是别的模型时不会借用。

**Key 不随工作流保存**：保存时会被清空，分享出去的图不带密钥。重开要重填，或设宿主环境变量 `MOSS_LLM_API_KEY`。

### 2.3 显存与速度

峰值在 VAE 解码，不在采样。[显存笔记](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/06-vae-memory.md)里的数字是**按公式复算的预估**，不是本包实测峰值：旧宿主（VAE 融合优化未生效）下 3120x896 / 8 格预估 11.34 GB；当前宿主走融合路径，同尺寸约 2.84 GB。两组数字都别读成「12 GB 够用」。

### 2.4 已知边界

- 框只是提示，布局遵循度不做数量承诺；MOSS 官方把布局能力标为 *experimental*。
- 不承诺跨格是同一张脸。换脸是工具，不是保证。
- 生成节点没有图像输入，从空 latent 开始；要改已有图，走裁 → 改 → 贴回，或用换脸节点（蒙版图生图）。
- 8/9 格每格像素少，小字和细纹最先糊 —— 这是分辨率账，不是节点 bug。

### 2.5 报错对照表

下表每一行都对应源码里的一条 `ValueError`。格式串里的 `%d`/`%s` 换成示例数字，标点与反引号按表格排版归一化；`Ctrl+F` 报错里的几个字就能对上。

| 你看到的报错 | 原因 | 改法 |
|---|---|---|
| `提示词有 4 段 [PANEL]，但布局是 8 格` | 稿子段数 ≠ 格数 | 补段数 / 改格数 / `init` 改 `plain` |
| `全局段写「恰好 4 个面板」，但布局是 8 格` | 全局段里的计数与实际格数不一致 | 改成实际格数，或删掉这句 |
| `逐格模式需要 [PANEL n] 分段描述（现在只有全局描述）。` | `mode=逐格` 但只有全局段 | 每格补一段 |
| `逐格模式要求每格都有一句描述：提示词只有 4 段，布局有 8 格。` | `mode=逐格` 但段数少于格数 | 补齐；逐格模式不会给空格子编词 |
| `没接 spec 时必须填 columns 和 rows（都 >0），否则无法确定裁切框` | 裁格 / 贴回没接 `spec`，`columns`/`rows` 还是 0 | 接 `spec`，或填列数 × 行数 |
| `图像 1024x576 不能被 3x2 整除：会得到差 1px 的格子，下游拼接必崩。…` | 裁格没接 `spec`，画布又除不尽 | 接 `spec`，或换成能整除的画布 |
| `index=5 越界：共 4 格（0 起）` | 裁格的格号超过最后一格 | 用 0…3，或接裁格的 `index` 输出 |
| `index=5 越界：本布局共 4 格（0 起）` | 同上，但来自**贴回**（措辞不同） | 同上；想改整图就别接 `spec` |
| `宫格尺寸 1024x1152 与布局 1024x576 不一致` | 贴回拿到的整图不是 `spec` 那张画布 | 喂采样器用同一根 `spec` 出的图 |
| `draft 为空：请粘贴官方 prompt-enhancer 的 caption/layout 草稿 JSON。` | 草稿编译输入为空 | 贴的是**草稿**，不是编译后的结果 |
| `元素 3 没有描述（caption 不能为空）` | 画布上有框没填描述 | 填一句描述，或删掉它 |
| `label 只能是 object/character/text（收到 'xxx'）` | 手改 state / 草稿时写了别的类型 | 只用这三个值 |
| `解码尺寸 1024x576 小于布局下限 2048x1152；无法重建裁切几何` | 换脸：输入图比要填的 `spec` 画布小 | 喂一张不小于布局的图，或改接更小的 `spec` |
| `init=source 需要 image（要换脸的那张图）。` | 换脸节点 `init=source` 但没接线（或接错口） | 接上图像；只是文生图就把 `init` 改成 `empty` |

### 2.6 没报错但结果不对

| 现象 | 先看哪 |
|---|---|
| 一片黑 | 配方混搭（50/3 与 4 步 LoRA 混用） |
| 格数对，内容全挤在一格 | 框只是参考；把每格描述写短写具体 |
| 画布形状和预览不一样 | 接了 `spec` 就以 `spec` 为准 |
| 找不到节点 | 分类 `moss/storyboard`；搜 `storyboard` / `宫格` |
| VAE 解码 OOM，或出图有淡淡接缝 | 分块解码 / 调小 `panel_edge` |
| 没有「中 / EN」按钮 | 刷新浏览器（F5）；控制台看 `[MOSS SB] i18n: 就绪` |

### 2.7 还是不通

把三样东西贴出来：`[MOSS SB]` 启动日志那行、节点 `report`（几何来源 / 实际步数 / 蒙版覆盖率）、ComfyUI 版本。
安装或工作流问题按这个开 issue；安全问题走 [`SECURITY.md`](SECURITY.md) 的私下渠道。

## 3 · 示例与文档

### 3.1 示例

示例都是 UI 格式（直接拖进画布）。换脸示例要自备 `moss_faceswap_src.png` 与 `moss_faceswap_ref.png`；
单图换脸示例自带 `assets/` 下两张演示图；`llm-prompt-workbench.json` 第 ④ 组要自备 PE-I2I GGUF + `mmproj` + `system_prompt.txt`（见 1.6，非商用）；其余示例不需要图。

| 文件 | 干什么 |
|---|---|
| `moss-storyboard-4panels-ui.json` | 4 格故事板，最朴素的接线 |
| `moss-layout-editor-storyboard.json` | 4 格故事板，带画布内说明（首选起点） |
| `moss-layout-editor-poster.json` | 单图海报，编辑器摆框 |
| `moss-prompt-compiler.json` | 官方草稿 → 提示词 + 编辑器 state（零模型） |
| `moss-faceswap-ui.json` | 4 格宫格 → 换第 0 格 |
| `moss-faceswap-single-ui.json` | 单张照片 → 换脸 → 整张出 |
| `moss-pose-generate-ui.json` | 参考脸 + 提示词 → 一张带动作的图 |
| `moss-storyboard-4panels-edit.json` | 裁一格 → 指令编辑 → 贴回 |
| `llm-compile-zero-model.json` | IR → 拼装提示词，零模型（装机自检） |
| `llm-prompt-workbench.json` | 反推 / 洗图 / 通用扩写 / PE-I2I 四组，按需旁路（PE 组权重自备） |
| `llm-idea-to-image.json` | 一句话 → 单图或 4 格故事板 |

### 3.2 文档

- 全部参数、术语与实测边界：[`docs/19-node-manual.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/19-node-manual.md)。
- LLM 节点（18 个参数 / 4 类 16 档写法）：[`docs/20-llm-prompt-nodes.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/20-llm-prompt-nodes.md)。PE 改写器见 [`docs/19-node-manual.md` §9](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/19-node-manual.md)；怎么跑起来见上面 1.6。
- 文档索引：[`docs/README.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/README.md)。

> 手册只在 GitHub 仓库里，发布包不含 `docs/`；上面的链接指向仓库。

### 3.3 贡献

- 自检、本地 CI 与 PR 规则：[`CONTRIBUTING.md`](CONTRIBUTING.md)。
- 变更记录：[`CHANGES.md`](CHANGES.md)。
- 报 bug：附 `[MOSS SB]` 启动行、节点 `report`、ComfyUI 版本。

## 4 · 许可

MIT。第三方出处与上游许可：[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)。

> 可选的 Qwen-Image 2.1 PE 权重**不在**本 MIT 许可范围内。它们按 `qwen-research` 发布，**非商用 / 仅内部使用**；本包不分发权重，也不含它们的 `system_prompt.txt`。
> 请自备，商用前自行核对上游许可（见 1.6）。
