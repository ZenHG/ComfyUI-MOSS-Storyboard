# THIRD-PARTY-NOTICES

本包（ComfyUI-MOSS-Storyboard）自身代码为 MIT。以下是与本包**相关但未随包分发**的第三方内容，
以及本包与其关系的准确表述。

## 一、运行时宿主：ComfyUI（GPL-3.0）

- 本包**不复制** ComfyUI 的任何源码，但在运行时 `import comfy.*`、`comfy_api.*`、`latent_preview`
  并且以自定义节点形式运行在宿主进程内。
- ComfyUI 本体许可见 <https://github.com/comfyanonymous/ComfyUI/blob/master/LICENSE>（GPL-3.0）。
- 本包调用的宿主 API 属**公开内核**：`comfy.sample.sample_custom` / `prepare_noise` /
  `fix_empty_latent_channels`、`comfy.samplers.KSampler` / `sampler_object`、`comfy.utils.PROGRESS_BAR_ENABLED`、
  `comfy.model_management.intermediate_device`、`latent_preview.prepare_callback`、
  `comfy_api.latest.io`。这些都是宿主节点自己也在用的同一批入口，不涉及私有结构。

## 二、模型与权重：均**未**随包分发

| 名称 | 用途 | 许可 | 说明 |
|---|---|---|---|
| MOSS-Image（SFT / BF16） | 图像主干（5B 单流 DiT，FLUX.2 latent 空间） | Apache-2.0（据 OpenMOSS 发布页） | 本包不打包、不下载权重；用户自备 |
| Qwen3.5-9B（`moss_image` 文本编码器） | 文本编码 | 见上游模型页 | 同上 |
| FLUX.2 VAE（`flux2-vae`） | latent 编解码（128 通道 / 1:16） | 见上游发布页 | 同上 |

⚠ **本包不对上述权重的许可做任何担保或转授**。商用前请自行核对各模型页的当次许可文本。

## 三、思路来源（无代码复制）

- **宫格模板 + img2img 高降噪锁定布局**：这套思路在 ComfyUI 社区已流通（例如
  `ComfyUI-Storyboard-Tools` 的 `StoryboardGridGenerator` / `StoryboardPanelCrop`，以及
  Krea2 的"4/6/8 宫格故事板"工作流）。本包**未复制其代码**；相反，本包的几何与裁切层
  是针对该实现的一组已复现缺陷重写的（缺陷表：
  3 列画布必崩、`offset_x ≠ 0` 必崩、`border_thickness=1` 边框消失、`bg=255` 网格消失、
  `letterbox` 分支越界等）。此处列名是为致意思路，不构成代码归属。
- **MOSS-Image 原生 JSON 布局提示词**：格式（`summary` / `items` / `bbox_coord_space=abs_centralized`
  / `bbox_format=yxyx`）来自宿主 `comfy_extras/nodes_moss_image.py::MOSSImageLayoutPrompt`。
  本包按其**产出格式**生成同样的字符串，使本节点可以直接替换该节点的下游接线。
- **编辑器 state-in-widget 架构**：`web/moss_sb_editor.js` 的「整份 state 序列化进一个 STRING widget，
  再在节点内挂 DOM 覆盖层」做法参考 `ComfyUI-NewBie-LLM-Formatter`（MIT，许可见其仓库 LICENSE）。
  本包**未复制其源码**；CSS、交互、state v3 字段与编译规则均为本包自写。
- **MOSS-Image Prompt Enhancer skill（Apache-2.0）**：`MossPromptCompiler` 按
  <https://github.com/OpenMOSS/MOSS-Image/tree/main/skills/moss-image-prompt-enhancer>
  的**公开文档格式**独立实现草稿解析与编译。运行时代码为本包自写，**未复制该 skill 的代码**；
  坐标换算（左/上 floor、右/下 ceil、减中心）与字段校验口径按官方文档与公开实现对齐，
  并用官方示例做了离线回归。skill 的版权归 OpenMOSS 作者所有。
  - 为便于离线核对，仓库内 `docs/reference/moss-image-prompt-enhancer/` 存了一份**上游原文照抄**
    （`SKILL.md` + `references/` + `scripts/` + `agents/`，随附 Apache-2.0 全文，见该目录 `PROVENANCE.md`，
    记录了提交 `20f559207791`）。该目录**只进仓库、不进发行包**（`tools/make_dist.py` 的
    `DEV_PREFIXES` 已排除 `docs/`），也不参与运行时；引用请以上游为准。

## 四、本包依赖

- **无**额外 pip 依赖。`torch` 与 `comfy*` 由宿主提供。
- 因此 `requirements.txt` 只有注释，ComfyUI-Manager 安装本包不会拉任何新包。

## 五、复核状态

- 缺口表与"未实测项"（模板 img2img 在 MOSS 上的实际效果）见 README「已知边界」。凡未实测的结论
  本包一律标注，不写成已证。
