# 顶层设计与节点职责：这七个节点各自对什么负责

> 状态：本轮已按这份判据落地。本文只记录**职责边界**和**已经实现的行为**；
> 没有 A/B 跑过的审美判断一律标成假设，不写成结论。

## 一、一句话框架

本包的承诺是：

> **几何确定、提示词透明、返工便宜。**

不是“替用户想镜头”，也不是“保证每格照办”。MOSS 的布局能力官方仍标实验，
所以工具负责的是**可复现的排版与接线**，构图是否到位由人看。

## 二、七个节点的职责

| 节点 | 对什么负责 | 不负责什么 |
|---|---|---|
| `MossLayoutEditor` | 创作面：网格=分镜（镜号/景别/画面），单张=元素（类型/描述/文字/主色） | 不做采样，不写内容 |
| `MossStoryboardLayout` | 零模型规划与互操作：几何、`spec`、模板、原生布局 JSON、elements | 不做像素判断，不保证遵循度 |
| `MossStoryboardSampler` | 执行面：布局约束 + 采样 + 复绘宫格线 + 裁格；整张或逐格 | 不做图生图，不替用户决定耗时档 |
| `MossStoryboardCrop` | 按 `spec` 精确取格（含外部宫格图） | 不猜列行，不静默缩放 |
| `MossStoryboardPaste` | 按同一几何把单格贴回；只动目标格内容区 | 不重画宫格线，不碰其他格 |
| `MossPromptCompiler` | 官方 prompt-enhancer **草稿**适配器：严格校验并编译 | 不自动猜测输入类型，不替用户改写草稿 |
| `MossStoryboardFaceSwap` | 参考图编辑：`init=source` 吃源图做图生图（有蒙版就只重画蒙版内 = 换脸）；`init=empty` 从空 latent 生成 | 不承诺跨格身份一致；蒙版外有解码舍入（实测可见改动 <0.01%）；大范围替换会被上下文传染 |

**节点数控制在 7 个，加功能先问「能不能用一个旋钮表达」。**
三个判断不冲突，要分清：

- **撤回的是角色混淆**：「Editor 吸收 Layout 输出」会让一个节点同时管画布与几何。
- **保留的是同一职责内的补齐**：「单张模式也给 `spec`」——几何只能有一个 owner，
  单张只是 `panels=1`，仍归几何管。
- **换脸节点加 `init` 而不是加节点**：换脸与「参考图 → 一张带指定动作的图」
  是同一件事的两个特例，差别只有「从哪开始」。判据是「是不是同一件事」——
  是就加参数，不是才加节点。

## 三、三种语义必须分开：面板 / 元素 / 角色

这是本包最容易混的地方：

- **面板（panel）**：宫格里的一格，属性是 `no`（镜号）、`size`（景别）、`caption`（画面描述）。
- **元素（element）**：单张画布上的一个框，属性是 `label`（物体/人物/文字）、`bbox`、`caption`、`text`、`palette`。
- **角色一致性**：靠全局 `style` 里的角色/服装/环境块，加上逐格描述；
  **不是**把某格标成 `character` 就能保证同一个人。

state v3 用一个 `items` 数组同时承载前两种，所以**模式决定哪些字段有效**：

| 模式 | 有效的字段 | 网格模式下无效（但可存在于旧 state） |
|---|---|---|
| `preset=grid` | `no` `size` `caption` | `label` `text` `palette` |
| 其他（单张） | `label` `caption` `bbox` `text` `palette` | `no` `size` |

实现上的三处一致动作：

1. **编辑器不再在网格模式显示元素控件**：网格属性面板只给镜号、景别、画面描述。
   旧 state 里残留的 `label` / `text` / `palette` 不会消失（不偷偷改用户存档），
   但会在 `report` 里明确说明“网格模式不消费”。
2. **画布标签随模式换语义**：网格显示景别，单张显示元素类型；格位一律画成中性色（类型色与主色填充只在单张生效）。
3. **`report` 出声**：忽略字段、预设占位、段数与格数不符都会写进报告，不静默。

## 四、提示词透明

用户必须知道模型到底收到了什么。已落地：

- `MossStoryboardSampler` 多一路输出 `prompts_json`：
  整张模式是 `["<实际编码的提示词>"]`；
  逐格模式是每格实际发送的提示词数组（含被清掉多格措辞后的全局段）。
  `prompt_used` 保持原义（逐格模式仍是原始故事板稿），以免打断旧工作流。
- `MossLayoutEditor` 的画布下方（状态栏与属性面板之间，**不进面板**，否则会被 218px 滚动区挤到看不见）
  有**最终提示词**只读预览：网格模式显示
  `[STYLE] + [PANEL n]`，单张模式显示原生布局 JSON。它不是简化版说明文字，
  而是与后端逐字节同口径的编译结果（`tests/test_cross_language.py` 盯 100 组随机 state）。
- 逐格模式的 `latent` 是 **N 个单格 latent 的 batch**（`{"samples": [N,C,h,w]}`，行主序 = 格号 1..N，**不是拼版**）；接 `VAEDecode` 会逐张解码成 N 张单格图。整张模式返回整张画布 latent。

## 五、占位文案守卫

预设给的是**起点**，不是内容。`标题文字` / `正文文字` / `第 3 格画面` 这类预设文案
如果不改就排队，会被当成真实内容处理。

- **caption 占位**：只警告，不拦截（它描述画面，用户可以真的只想先跑一版）。
- **text 占位**：拦一次。点「排队」时如果还有预设文字，弹一次确认写明是哪几个元素 id；
  **取消 = 不排队**，确认才继续。前端没有 `window.confirm` 时不臆造拦截，退回状态栏警告。
  警告随编辑自动消失（占位判据看的是当前值，不是"有没有改过"的状态位）。
- 后端 `report` 同步告警，所以纯 API 用户也能看到，不只是前端提示。

判据在 `core/editor.py`（`find_placeholders`）与 `web/moss_sb_editor.js` 各一份，
由 `tools/check_editor_ui.py` 与 `tests/test_cross_language.py` 封住，避免两边漂移。

## 六、刻意不做的事

- 不做 LLM 自动补词、不自动定景别节奏：**内容与节奏归作者**，工具只提醒“这里还是占位”。
- 不做“看起来更聪明”的自动格式猜测（Compiler 收到已编译 JSON / state 时给明确错误，
  而不是悄悄转换）。
- 不把面板标成 `character` 当角色一致性方案。
- 不在本包里做图像理解（穿模、伪文字、遵循度评分都不属于这里）。

## 七、待验证的假设（不当结论用）

- 示例里的布局元语言（“严格对称网格”“恰好 4 个面板”“细白边框，没有粗框/黑边/跨格溢出”）
  与原生布局 JSON 表达的是同一件事，**可能**过度指挥模型、挤压构图。
  验证方法：同素材同 seed，仅换 `style` 为“只讲故事/世界/角色”的一版，逐格目检构图自然度
  与格数遵循度。**未跑之前，本包不声称哪一种更好。**
- 宫格预设的景别斜坡（4 格：大远景 → 全景 → 近景 → 大特写）是**教学脚手架**，
  不是叙事建议。它是否比“全部中景 + 用户自己改”更有帮助，同样需要人看。

## 八、本轮落地清单（每条都有机检对着）

| 改了什么 | 在哪 | 谁盯着 |
|---|---|---|
| 宫格模式收起元素控件（类型/文字/主色/框/层级/对齐），只留镜号+景别+描述，并说明为什么 | `web/moss_sb_editor.js::syncPanel` | 目检（前端无机检） |
| 画布标签随模式换语义；宫格不再用 palette 上色 | `itemCaption(item, isGrid)` / `render` | 目检 |
| 最终提示词只读预览 + 复制 | `compileFinalPrompt` 与 `syncPrompt` | `test_cross_language.py`（逐字节 + 100 组随机） |
| 占位守卫（text 拦一次、caption 只警告） | `core/editor.py::find_placeholders` / JS 同名 | `test_editor.py` §10、`check_editor_ui.py`、`test_cross_language.py` |
| 宫格忽略字段写进 `report` | `find_ignored_grid_fields` | `test_editor.py` §10 |
| `prompts_json` 输出（整张 1 条 / 逐格 N 条） | `MossStoryboardSampler` | `test_contract.py` 输出 arity、`check_ui_workflow.py` 槽位、**真机 2 跑**（见下） |
| 逐格模式 `latent=None` 在 `report` 写明（0.2.3 起改为 batch，见 `docs/10-contract.md` §二） | `_run_per_panel` | 真机 2 跑（report 里那一行是产物文本，不是文档承诺） |
| 分辨率告警按模式判：整张看画布/单格 MP，逐格只看**单格采样画布**（拼版后的整张从不进模型） | `_mp_note(layout, per_panel, sample_px)` | `smoke_nodes.py` §5（含"枚举不残缺"）+ 真机两档 `sample_edge` |
| 纯调试项 `show_guides` 归入高级（V1/V3 同一口径） | Crop / Paste | `test_contract.py` tooltip 检查 |
| 真机（无头浏览器 + 活宿主）验：控件随模式收起、预览在可见区内、预览 == JS 编译器、占位确认且取消不入队、删除与分布按钮接线 | `tools/ui_smoke.mjs` 编辑器段（38 项） | 本机跑，CI 无宿主 |
| 节点内滚轮只滚编辑器、绝不缩放画布（只抢归属、不接管滚动；Safari 捏合另拦） | `web/moss_sb_editor.js::installWheelGuard` / `claimWheel` | `check_editor_ui.py` W15–W21 + `tools/ui_wheel_check.mjs`（Chromium/Firefox/WebKit 各 20 项，含负对照与 defaultPrevented 探针） |

**`prompts_json` 的真机取证**（2026-10-26，宿主 `MOSS 故事板宫格.json`，3 格 / `panel_edge=256` / 4 步，
`SaveText` 直接落盘第 6/7 号输出）：整张 **1 条**（23s）、逐格 **3 条**（41s），
逐格 report 含「`latent` 为 None」与「`prompts_json`（3 条）」（0.2.3 起 `latent` 改为 batch，此处保留当时的取证原文）。
同一趟还实锤了告警的错：旧版在 `sample_edge=768`（单格采样 0.33MP）时仍按**最终格子** 0.04MP
喊"这才是画质瓶颈"，且把解法 ① 删剩「解法：① ②」—— 修后 `sample_edge=256` 报采样画布 0.04MP 并指向
`sample_edge`，`sample_edge=768` 不再报。（脚本 `docs/_local/proof_prompts_json.py`，本机专用不进包）


**刻意没有做的渐进披露**：只收起 `show_guides` 这类"改了不影响出图"的调试开关。
`shave` / `border_px` / `sample_edge` / `label` 都会改像素，把它们塞进折叠区
只会让人以为节点没有这个能力 —— 本包所有参数都带 tooltip，展开比折叠更诚实。

