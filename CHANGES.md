# CHANGES

## 未发布 — 新增示例 `moss-pose-generate-ui.json`（姿态/动作 → 换脸）

「参考图 + 提示词 → 一张带指定动作的图」这条路径本包节点做不了（生成节点入参里没有 IMAGE），
所以没加第 8 个节点，而是把宿主原生链路搭成现成示例：

- `tools/make_pose_example.py` → `examples/moss-pose-generate-ui.json`（14 节点 / 17 连线）。
  第一段：`TextEncodeMOSSImageEditHiRes`（vision，ref 0.99/1.0）+ `EmptyFlux2LatentImage`
  1024×576 + `KSampler` 8 步/cfg1.0/euler/simple → 带动作的图；
  第二段：本包换脸节点把身份收紧一次。
- 九档动作与七档姿态的提示词原文、判据、两条备选复现路径 → `docs/17 §九`。
- 依赖：需要 ComfyUI-MOSS-RefHiRes 的 `TextEncodeMOSSImageEditHiRes`（GPL，独立安装）。
  宿主自带的 `TextEncodeMOSSImageEdit` 把参考图硬压到 0.147 MP，那个值下身份约 0.008。
- `tools/check_ui_workflow.py` 加 `THIRD_PARTY` 集合：第三方包节点不再被误报「未知」，
  但校验文案会写明「用到第三方包节点 X（需自行安装对应包）」，不掩盖依赖。
- 修示例生成器的真 bug：同一个输出被多处消费时（编码节点同时给 positive / negative），
  `wire()` 覆盖 `links` 而不是追加，导致「连线表自洽」翻红。

## 未发布 — 文档修正：姿态/肢体动作结论撤回

**撤回两条作废结论**（`docs/16` §八、README「做不到的」）：

- ~~姿态改不动~~ → **姿态能动，但跟参考图走**。参考图正脸时一句提示词把 yaw 从 `+0.05`
  推到 `+1.48`（近正侧 / 低头 / 仰头都成立）；参考图本身侧脸（yaw `+5.20`）时七档提示词
  全画成侧脸，**拉不回来**。
- 新增 **肢体动作 ≈ 无损**：九档（站立 / 插兜 / 抱胸 / 走 / 前伸 / 坐 / 转身 / 手抵下颌 /
  背身回看）净损失 `−0.086 … +0.112`，七档在 ±0.05 以内。伤脸的是**连带转头**
  （背身回看 yaw `0.76` → `−0.086`），躯干转侧但头朝镜头只有 `+0.036`。
- ~~不做蒙版局部重绘~~ → 现在**就是**做蒙版局部重画；不用肤色掩膜（ΔE 9–11 有接缝），
  用矩形 + 高斯羽化。

**README 补全**：蒙版几何写清（矩形不是轮廓、白=重画、整张宫格的蒙版也能给）、
手接蒙版的两个坑、自动蒙版的安装/权重路径/自查、本机五个检测轮子的对照表、
「没接线 ≠ 没有蒙版」（`auto_mask` 默认开）。

**`core/facemask.py`**：
- 权重路径可配：环境变量 `MOSS_INSIGHTFACE_ROOT`，默认 `~/.insightface`。
- 权重缺失**不再尝试联网下载**：先查 `det_10g.onnx`，缺了就报错并打印实际查找路径。
  只需要 `det_10g.onnx`（19 MB）；`w600k_r50.onnx` 用不到（只加载 `detection`）。
- 检测器加模块级缓存：`FaceAnalysis` 初始化要几百毫秒，每格重建会拖慢整张宫格。

## 0.3.0 — 2026-10-28 · 第 7 个节点：换脸

### 新增 `MossStoryboardFaceSwap`
- 参考图走 vision 通道注入身份，源图走图生图重画，一次一格。
- 接了 `spec` 就只换第 `index` 格再贴回：目标格之外**逐像素不变**（实测第 0 格 MAE 65.07，
  第 1/2/3 格与缝、外边距 MAE 0.000）。
- `vision_megapixels` 默认 1.05。宿主默认 0.147 那个值下身份余弦只有 0.008（等于没给参考图）；
  预算**只降不升**，源图本身不够大时 `report` 会明说「没缩放」，不让用户误以为调了没效果。
- `denoise` 名义值会被宿主按 `int(步数/降噪)` 截断：8 步下 0.875~0.99 **全部等于 1.0**。
  `report` 打印实际生效值，并在截断时给出「想保住源图要 ≤0.875」。
- `sample_edge` 默认 1024：单格先抬到这个长边再采样、采样完缩回格子，与「逐格」模式同一机制。
  4 格 496 档一格只有 0.135MP、脸约 50×27 像素，那个尺度认不出是谁。

### 换脸改成「蒙版局部重画」
- 新增可选 `mask` 输入：接了只重画蒙版内，蒙版外**逐像素保留**。实测同一张图：
  蒙版内 `mean|Δ| 41.33`、蒙版外 `0.40/255`；不接蒙版（整格重画）全图 R 通道偏移 +24~+53。
- 走宿主 `sample_custom(noise_mask=...)` 官方参数，与画布上 `SetLatentNoiseMask` 同一语义。
- 蒙版与图像走同一套几何：按 `spec` 裁到目标格，再跟着抬 `sample_edge`。
- ⚠ 坑：`LoadImage` 的 `MASK` 是 alpha（`1-alpha`），灰度图没有 alpha 时返回 64×64 全零
  ⇒ 蒙版等于没接。示例改用 `ImageToMask(channel="red")`。
- **自动蒙版（默认开）**：insightface 检脸 → 生成蒙版，不用手画。手画的椭圆只对一张图有效，
  换图就错位、宫格里还会偏到另一个人脸上，而且不报错。
- `face_pick`：`largest` / `left` / `right`，一格多个人时点名换哪张；`report` 写「检测到 N 张脸、用了哪张」。
  示例实测 `face_pick=right` 改动面积 3.38%（整格重画是 22.8%）。
- 修两个静默失败：V3 外壳把没接的可选入参传 `None`（不落 Python 默认值）⇒ `auto_mask` 曾被当 False；
  以及蒙版判据误用 `mask` 而不是生成出来的 `m`。
- 配方与判据写成 `docs/17-faceswap-recipe.md`（含 E26 成功配方的原文提示词与两个坑）。

### 接线断言抓到的真 bug
- 用户自己在提示词里写 `Picture 1:` 时，前缀会变成两条 ⇒ `<|image_pad|>` 数量与 `images=` 不符。
  现在开头的前缀（含视觉 token）**先剥再统一拼一条**。真机跑不出来这个错，桩测试一跑就红。

### 承诺改了（不是加功能，是改定位）
- README「已知边界」原来写「不做人物身份一致性，**也不做换脸**」。现在写
  「不承诺身份一致性；给的是换脸**工具**，不是保证」——一次一格、参考图驱动，
  不等于「先定角色卡再逐格派生」那条链。中英两版同步。

### 许可
- `core/refimage.py` / `core/faceswap.py` 为**独立实现（MIT）**：行为按宿主
  `TextEncodeMOSSImageEdit` 的公开契约，代码未复制任何 GPL 源码，也未 import
  `ComfyUI-MOSS-RefHiRes`。边界写进 `THIRD-PARTY-NOTICES.md` §四之二。

### 校验
- `tests/test_faceswap_wiring.py`：34 项，纯 CPU，3 秒（桩 clip / vae / 采样，
  不加载模型、不碰 GPU —— 重启宿主之前能做到的最强验证）。
- 示例 `examples/moss-faceswap-ui.json` 真跑 `success`；UI 闸 13/13。
- 端到端矩阵重跑 **16/16**（`--no-doc`，2m55s，退出码 0）。

## 0.2.3 — 2026-10-27 · 审计收口

### 旧工作流：`widgets_values` 错位自动归位
- 起因（2026-10-28 实测）：参数表变过之后，旧存档的值数组与当前参数表**对不上位**，
  而 ComfyUI 按位置赋值 —— 从错位处起整条偏一格：`panels` 拿到 `"auto"`、`panel_aspect`
  拿到 `8`、`init` 拿到 `0`，界面上五个下拉变红底。**ComfyUI 自己不报错**，看着像
  「默认值没设好」，其实点运行会带着错值跑。
- 认两种错位，判据都是「修完**每一个**值都合法」：
  1. **长度对不上**：补默认值 / 删多余值；
  2. **长度对得上但内容左移**（中间丢 k 个值、尾部补 k 个填充值 —— 真机稿就是这种，
     只按长度判"对齐"会永远不修）。
  值比 widget 多时先按 widget 数截断再判：i18n 语言条是 `configure` **之后**才挂上的，
  加载那一刻「值 23 项 / widget 22 个」是正常形态。
- **不动手的情形**：只坏一个值（更像用户手改）、差超过 3 格、怎么对齐都合法不了。
  修完写 `console.warn` 与 `node.properties.msb_widgets_repaired`，并提示重新保存。
- 钩子只挂在 `beforeRegisterNodeDef` 里本包节点上，外来节点不碰。**不必重建旧稿**。
- **根子上的那一格也补掉了**：i18n 语言条与编辑器面板都是 `addDOMWidget(..., {serialize:false})`，
  但这版 litegraph（前端 1.57.4）**不认**这个标记，`serialize()` 照样把它们写进 `widgets_values`；
  而它们是 `configure` **之后**才挂上的 ⇒「存档 n 项 / 载入时 n-1 个 widget」，
  **每保存一次就差一格，下次打开整条值右移**。现在存盘前自己把**所有虚拟组件**剔掉
  （`dropVirtual`：判据 = 名字是本包的，或「`serialize:false` **且** 有 DOM element」——
  🔴 不能只看 `serialize:false`，宿主的 `control_after_generate` 也带这个标记，但它的值**要进存档**），
  存出去的长度与载入时天然一致 —— 打开→保存→再打开→刷新，四步都不再需要"修"。
- 纯函数层 `web/moss_sb_widget_order.js` 由 `tests/test_compat.mjs` 直接跑（43 项）；
  `check_editor_ui.py` 新增 W22–W31，141 → 151 项。

### 前端真机测试：公共底座 `tools/ui_kit.mjs`
- 原先 `ui_smoke.mjs` / `ui_wheel_check.mjs` 各抄一份「找 playwright-core / 找 chromium /
  起浏览器 / 等就绪」，改一处漏两处。现在统一进 `ui_kit.mjs`，用例只写「验什么」。
- 三条纪律写进底座：缺依赖 **exit 2**（跳过 ≠ 通过）；等 `app.graph` **和** `app.canvas`
  都就绪再载工作流（早载会被随后的自动恢复覆盖成空图）；断言读 **`widget.value`**（界面值），
  不是 `widgets_values`（存档数组）——只读存档会得到假绿。
- 新增 `tools/ui_widget_compat.mjs`（14 项）：对齐稿不许被改 / 人为错位稿加载后归位 /
  **刷新后**仍不红 / 存档长度 = widget 数 − 语言条 / **存出去再打开**仍不红且不必修。
  刷新与"存完再开"是用户真实走的两条路径，只测 `loadGraphData` 会漏。

### 稿子与布局对账：不一致就停在采样前（只拦不改写）
- 布局模式下（`init=layout`/`auto` + 整张），`[PANEL n]` 段数与全局段的显式格数
  （「恰好 8 个面板」「8 panels」）都必须等于实际格数；不等直接 `ValueError`，
  报错写清两边数字、当前「列 × 行 / 画布」，并给两条出路：改稿，或 `init=plain` 原样送词。
- **不自动改写、不自动补段**：作者写什么就是什么，工具只拒绝自相矛盾的输入。
- 起因（2026-10-27 实测）：8 格画布配「恰好 4 个面板」+ 4 段稿，模型把 4 个节拍画了两遍
  （上下两条），旧版只在 report 里提示段数不足，没人看得到。
- 契约测试新增 2 条节点级断言（4 段 / 8 格 → 采样前报错；plain 不拦），139 → 141 项；
  逐格拼版测试新增 6 条对账断言，13 → 19 项。

### 布局：指定哪几格是人物（`char_boxes`）
- `MossStoryboardLayout` 新增可选输入 `char_boxes`：逗号分隔、**编号从 1 起**（如 `1, 3`，
  与宿主 `MOSSImageLayoutPrompt` 一致）。命中的格 `label` 变 `character`，其余仍用 `label`——
  一张板里既有主角格又有空镜时不用再拆成两个节点。
- 默认空串 ⇒ 输出**逐字节不变**。它挂在 widget 末尾，三个示例/夹具已补尾值；
  0.1.x 的老工作流由 `check_ui_workflow` 的 `LEGACY_TAIL` 容忍，缺的尾部按默认走。
- 契约测试新增 3 条（空串不变 / 编号从 1 起 / 只标命中格），136 → 139 项。

### 首跑档位提示（report 首行，只出声不改参数）
- report 第一行现在会说清这次跑的是哪一档：`SFT 模型卡档` / `蒸馏 LoRA 档` / `无官方口径`。
  步数与 cfg 分属不同档时直接写「混搭」，并点明代价 —— 包内的一致性与耗时数字都来自单一档位，
  混搭的产物对不上任何一条实测结论。
- 判据：步数 ≤8 归蒸馏档、9–24 无官方口径、≥25 归模型卡档；cfg ≤1.2 / 1.3–2.0 / ≥2 同理。
- **不动采样路径**，也不改任何默认值：混搭在 MOSS 上照跑，这里只是把「跑的哪一档」显式化。

### 参数上限与内核同源（审计 D-04 / D-05）
- Crop 的 `columns` / `rows` 从 20 收到 `core.layout.MAX_AXIS`（12）：UI 以前能选到 13–20，
  而布局一定拒绝——报错还发生在另一个节点里，很难对上。
- `index` 从 64 放到 `MAX_PANELS - 1`（143）：144 格布局的最后一格以前根本选不到。
  **只升不降**：降上限会拒绝既有的合法工作流。
- 三处 `shave` 统一到 `_SHAVE_MAX`（64）：Sampler 原来只到 32，与 Crop/Paste 的 64 不一致，
  照 UI 最大值裁、再贴回就会静默重采样。
- 契约测试新增 4 条（上限同源 / index = 格数上限 − 1 / 三处 shave 一致），132 → 136 项。

### 逐格 latent：batch，不是拼版
- `MossStoryboardSampler` 逐格模式**不再返回 `None`**：每格 `{"samples": …}` 拼成**一个**
  `{"samples": [N,C,h,w]}`（`core.sampling.stack_latents`，行主序 = 格号 1..N），report 写清语义。
- 一格一个 latent：异形、非 4 维、batch>1 一律当场报错。静默放过会让下游多解出图，
  而报错现场离真因很远。
- 回的必须是 **LATENT 字典**：宿主 `VAEDecode` 取 `samples["samples"]`，交裸张量是 TypeError；
  也不能是 list（V3 没有列表输出，下游会静默坏）。
- **输出值变了**（原来 None → 现在张量），**画面不变**；接 `VAEDecode` 会得到 N 张单格图。

### 文案对齐实现（审计 D-06 / D-08）
- `plain` 由「原样提示词」改名「纯提示词」：整张 = 整段原样送（等于普通 txt2img），
  逐格 = 全局段（去掉多格措辞）+ 本格描述。
- Paste 的 `shave` 提示不再说「整体偏移且不报错」：尺寸不符会 bicubic 重采样并写进 report，
  代价是画面变糊。

### 调度器与宿主同源
- `MossStoryboardSampler.scheduler` 的候选**改为读宿主** `comfy.samplers.SCHEDULER_NAMES`，
  不再手写七项（漏了 `linear_quadratic` / `kl_optimal`）。宿主不可导入时回落到九项冻结清单。
  判据 `tests/test_contract.py::test_scheduler` 直接问宿主，避免再漏。

### 外接提示词（描述不再挤在窄框里）
- `MossLayoutEditor` 新增可选输入 `prompt`（`forceInput`）：接上就接管逐格描述，
  编辑器里的描述框变只读；断开恢复手编。解析在 `core/intake.py`，规则写进
  `docs/10-contract.md` §六（段数不符写进 `report`；段首没写景别 ⇒ 该格不注入，不拿编辑器景别拼回去）。
- 编辑器画布下方的「最终提示词」改为**可编辑文本框**（150px、可拖高）：粘贴外部故事板稿后点
  **应用**按 `[PANEL n]` 反解析回格位，**还原**回到编译结果。前端 `parseStoryboard` 与
  `core/intake.py` 同规则，`tests/test_cross_language.py` 用 7 段样本文本差分。

### 景别：UI 中文、输出英文、可自定义
- 八档仍是预设，但 `size` 现在是自由文本（≤64 字）：下拉新增「自定义…」与「不指定」。
- 提示词里八档映射成**英文构图子句**（`SIZE_EN`，依据 MOSS USERGUIDE「默认推荐英文」），
  例：`中景` → `Medium shot, framed from the waist up.`；自定义文本原样输出。
- 新增只报警不改数据的冲突检测 `find_size_conflicts`：描述里自带「面部占满」却选了「大远景」时，
  `report` 点名（两处说法打架时模型常退回中景）。
- ⚠ 旧工作流重跑画面会变（景别从中文标签变英文子句）；state 存档格式未变。

### 滚轮不再缩放画布
- 最终提示词 / 描述框 / 属性面板内的滚轮归编辑器：有可滚容器就让它滚，滚到头也不外泄给画布缩放。
- 拦截点只有一个：`window` 的 **capture** 阶段（DOM 标准顺序 window → document → … → target，
  跟操作系统、浏览器引擎都无关）。原先写在节点里的 `stopPropagation()` 永远轮不到——宿主
  `GraphView` 把 wheel 转发给画布用的是外层 transform pane 的 capture。
- **只抢归属，不接管滚动**：命中可滚容器就 `stopPropagation` 后放手，滚动交给浏览器
  （惯性、行模式、小数 delta、Shift 轴映射都是它的事）；一个可滚容器都没有才 `preventDefault` 吃掉。
  自己算 `scrollTop +=` 的那版会把 macOS 触控板的惯性丢光，也会在两端把滚轮漏给画布。
- Safari 的**捏合**走 `gesture*` 事件、根本不发 wheel：编辑器内拦掉，否则整页缩放、DOM 与画布错位。
- `overscroll-behavior:contain` 盖住根 / 属性面板 / 提示词框：JS 万一失效，滚动链也不会传到画布。
- 真机验收 `tools/ui_wheel_check.mjs`：Chromium / Firefox / WebKit **每引擎 21 项**、合计 63 项。含
  「把 root 标记去掉，同一位置就该缩放」的负对照，以及读 `defaultPrevented` 的探针
  （证明我们确实没接管滚动）。同一套用例跑修复前代码是红的（文本不动 + 画布缩放）。
  取证边界：三个引擎都在 **Windows** 上跑，验的是**引擎**行为；**真 macOS 触控板与真 Safari 捏合没在本机验过**。


### 编辑器面板重做：铺满节点、标签对齐、密度（2026-10-28）
- 「布局框太小」的真因不是高度给得不够，而是**宿主 DOM 组件的排版模型**：这一版前端把
  `addDOMWidget` 当普通 widget 行来排，**行高由内容倒推** —— 根元素写 `height:100%` / `flex:1`
  都会被反推回内容高度（实测节点内容区 1040 时编辑器只有 884，剩下 150 多像素变成节点体留白）。
  真正生效的是两步：给根元素写**由节点高度算出的 px**，同时把 `flex` 从宿主的 `1 1 0%`
  改成 `0 0 auto`（不改的话 basis=0 又把高度吃回去）。
- 186px 是宿主自己占掉的（节点体 padding + widget 行包装 + 8 进 8 出的插槽条）。留够才不会
  在首帧排版时被反向撑高 —— 超出可用高度时节点会自己长 210px，底部留一条空带（"看着还是小"）。
- 节点默认尺寸 900×1040 → **900×1250**：编辑器 884 → **1064**，画布 ~419 → **~570**。
- `editor_state`（原始 JSON 那一行）以前靠 `computeSize = () => [0, -4]` 压不掉（Nodes 2.0
  每行照画）；改成 CSS `[data-widget-name="editor_state"]{display:none!important}`，实测 33px → 0。
- 顶栏不再换行：预设 + 按钮 + 语言条 + 撤销/重做/帮助 走一条流水线，说明与状态挪到第二行；
  参数行与属性面板的标签定宽（48px / 64px），`bbox`「层级 / 对齐」两行并一行，
  数字框 58px、预设 190px、比例 104px —— 控件不再被挤到第二行。
- 语言条**塞进编辑器顶栏的插槽**，不再单开一个 DOM 行：单开时挂载时机不同就有时在编辑器上方、
  有时在下方（同一份工作流两种样子）。
- **示例工作流**：编辑器 900×1250 居左当主角，其余节点按数据流向右成列（海报 4 列、故事板 4 组），
  分组包围盒按成员反算（左/右/下 26、上 56 边距）；`extra.ds` 按**真机量到的可见带**
  （左工具栏 56 / 顶栏 89 / 右侧面板 348，内缩 24）居中拟合 —— 打开即整图可见，
  不依赖右侧面板开合。尺寸写的是**包围盒**（`size[1] = 高 - 30`），不这么写每存一次就长 30。


### 新增
- **第六个节点 `MossPromptCompiler`（提示词 · 草稿编译，零模型）**：桥接 OpenMOSS 官方
  [MOSS-Image Prompt Enhancer skill](https://github.com/OpenMOSS/MOSS-Image/tree/main/skills/moss-image-prompt-enhancer)。
  贴官方 **draft**（caption 或 layout），输出可直接用的 `prompt`、`report`，布局草稿另出
  `state`（state v3，可导入布局编辑器）。按官方 schema 校验：`width`/`height` 为 16 的正整数倍、
  `bbox_coord_space=abs`、`bbox_format=xyxy`、`summary.caption` + 非空 `items`、文字元素必须带
  `text`；未知字段 / 重复键 / NaN / Infinity / 越界框一律拒绝。本包另加 256 元素与 1 MB 草稿上限。
  坐标换算与官方实现逐字对齐（左/上 floor、右/下 ceil、再减画布中心），并有官方期望值回归。
- **`core/skill_draft.py`**：草稿 schema 的独立实现（未复制官方代码），含 `loads` / `compile_draft` /
  `draft_to_state`；`tools/compile_skill_draft.py` 提供无 ComfyUI 的命令行入口（`--output` / `--state`）。
- **`tests/test_skill_draft.py`**：79 项离线检查，覆盖官方示例、分数边、非法几何 / 尺寸 / 字段 /
  重复键 / NaN / 越界、256/257 上限、state 映射、CLI、输入不被改写、编译上限 8192 vs state 上限 4096。
  state 画布还须 ≥64（编辑器下限）；超出时节点仍输出 prompt，并把「state 未生成」写进 report。
- **`docs/13-prompt-enhancer.md`**：官方 skill 的用法、草稿 schema、三条接入路径、置信度与未测项。

### 顶层设计与提示词透明（`docs/14-top-level-design.md`）
- **编辑器按模式分层**：宫格模式收起类型 / 文字 / 主色 / 框 / 层级 / 对齐这些**不参与宫格提示词**
  的控件，只留镜号 + 景别 + 描述，并就地说明为什么；画布标签也随模式换语义
  （宫格显示景别，单张显示元素类型），宫格不再用 palette 给格位上色。
- **占位守卫**：`core/editor.py` 新增 `find_placeholders` / `find_ignored_grid_fields` /
  `placeholder_summary` / `compile_final_prompt`。`标题文字`、`第 3 格画面` 这类预设文案原样排队时，
  `report` 会点出数量与元素 id；文字类占位在**排队前**弹一次确认（它会被逐字渲染进图），
  描述类占位只警告不拦截。
- **最终提示词可见**：
  - `MossStoryboardSampler` 新增第 7 路输出 `prompts_json`（整张 = 1 条、逐格 = 每格 1 条，
    就是真正送模型的那句话）；旧输出顺序与 `prompt_used` 语义不变，老工作流不受影响。
  - 编辑器画布下方（状态栏与属性面板之间）有只读**最终提示词**预览 + 复制按钮，与后端同一套编译口径。
  - 逐格模式的 `latent` 为 `None` 现在直接写在 `report` 里，不让人接到下游才发现。
- **纯调试开关归入高级**：Crop / Paste 的 `show_guides` 标 `advanced`（V1/V3 同口径）；
  会影响像素的参数一律不折叠。
- **新增机检**：`tests/test_editor.py` +17 项（占位与最终提示词）；`tools/check_editor_ui.py` +24 项
  （JS 编译与占位判据 + 界面文案中英齐全）；`tests/test_cross_language.py` +19 项 —— 含 `round()` 逢半取偶的平局坐标、
  键序、镜号乱序、空 palette、style 全空白等容易分叉的地方，JS ↔ Python 逐字节比对。
- 顺带修掉 `tools/smoke_nodes.py` 的 `check()`：detail 传元组时会 `TypeError` 崩掉整条闸，
  现在统一 `str()`。
- **真机验证后回改的两处**（`node tools/ui_smoke.mjs`，无头浏览器 + 活宿主，31 项全绿）：
  预览条原先挂在属性面板里，被 218px 的滚动区挤到看不见 ⇒ 改挂到编辑器根上，
  状态栏下方独立一条，选中与否都看得到；宫格模式状态栏把格位叫成「4 个元素」
  ⇒ 改叫「4 格」（`panelsCount`），免得让人以为格位是能自由增删的元素。
- **`tools/ui_smoke.mjs` 补两道闸**：等 `app.canvas` 就绪再载工作流（否则会被前端自动恢复覆盖成空图，
  实测出现过 `getCanvas: canvas is null` + 节点数 0 的假红）；新增编辑器段
  （宫格收起控件 / 预览在可见区内 / 预览 == JS 编译器输出 / 占位确认且**取消后不入队**）。

### 真机取证与两处实锤修复（2026-10-26）
- **`prompts_json` 真机跑通**（宿主 `MOSS 故事板宫格.json`，3 格 / `panel_edge=256` / 4 步，
  用 `SaveText` 直接把第 6/7 路输出落盘）：整张 **1 条**（23s）、逐格 **3 条**（41s），
  逐格 report 含「`latent` 为 None」。此前这条宣称只有静态 arity 闸撑着，没真跑过。
- 🔴 **修 `_mp_note` 的两处错**：① 逐格模式的单格告警按**最终格子**尺寸判——实测 `sample_edge=768`
  （单格采样 0.33MP）时仍照旧喊「单格只有 0.04MP，这才是画质瓶颈」，而逐格进模型的从来是
  `sample_edge` 那张，拼版后的整张从不进模型；② `per_panel` 分支用 `str.replace` 删掉「解法 ①」
  后留下残缺枚举「解法：① ② 减少格数」。现在逐格只看单格采样画布、杠杆写成 `sample_edge`，
  整张保留画布/单格两条并删掉与用户当前配置无关的举例。`smoke_nodes.py` +8 项（§5，
  含「枚举不残缺」，已变异测试：把 ① 抽空必红）。
- **`check_release_ready.py` 新增 S10**（31 → 33 项）：比对 `CONTRIBUTING.md` 与 `.github/workflows/ci.yml`
  两处门禁计数。上线即抓到 5 处漂移（`test_editor` 103→121、`test_contract` 121→127、
  `smoke_nodes` 50→58、`check_editor_ui` 109→133、`test_cross_language` 69→88），已全部同步。
- **`CONTRIBUTING.md` 的 `check_ui_workflow.py` 补上文件参数**：照旧抄那行命令只会查内建的
  一份夹具（15 项），与同一行声称的 88 项对不上——CI 是分 6 次传的，本地清单没写。
- **`ui_smoke.mjs` 的"不入队"判据改成队列 id 增量**：8188 是共用宿主，原先断言"队列 == 0"，
  被另一路跑批（`client_id=moss_targets`）顶出过一次假红。改成"我们点完之后队列没多出一条"——
  既不在别人排队时误报，也不放松"一条都不许多"的硬保证。

### 文档 / 分发
- **英文 README 成为主文档**（`README.md`），中文全文移至 `README_ZH.md`；`README_EN.md` 留跳转桩。
- **首屏重排**：真实 4 格 hero 图、与官方 MOSS 布局的差异对照、Manager / CLI / git 三种安装、
  3 分钟快速路径与「不适合谁」边界。
- `pyproject.toml` 增补 comic / manga / poster / layout 等检索词；
  DisplayName 改为 `MOSS Storyboard & Layout`。
- 发布工具链同步：版本行、`check_release_ready`、`release.py`、`make_dist`、`test_contract`
  与文档锚点。

### 示例与首跑
- **采样默认值对齐 MOSS-Image-SFT 官方模型卡**：`steps=50 / cfg=3.0 / euler`（`shift=5` 原生模型内置）；
  调度器模型卡未指定，默认取 OpenMOSS 官方 ComfyUI 插件工作流的 `sgm_uniform`。
  节点出厂默认、四份示例与相关 tooltip 同步；旧文档里的 `30–50 步 / cfg 3–5`、`35 / 3.5`、
  `25 / 2.5 / er_sde` 说法作废（`25/2.5/er_sde` 是 Comfy-Org 原生模板的另一套选择，不是模型卡口径）。
  模型卡另要求 SLG 负分支层 22；本包不内置 SLG，需在模型与采样器之间接宿主
  `SkipLayerGuidanceDiTSimple`（`single_layers=22`），示例未接。
- **交付示例改为「只有必需权重也能跑」的基准档**：Node 出厂默认即模型卡档，4-step 蒸馏 LoRA
  仍默认旁路；装 LoRA 时按工作流内说明取消旁路并改成 `4 步 / cfg 1.0`。
  （旧示例是 `4 步 + LoRA 旁路`，只有三个必需权重的用户会直接撞上混档。）
- **编辑示例**同样改为基准档：生成与编辑 `KSampler` 都用 `50 / 3 / euler / sgm_uniform`；装 LoRA 时生成改
  `4 / 1.0`、编辑改 `8 / 1.0`。工作流内 MarkdownNote 已写清两档。
- 示例里的负面提示词默认清空：模型卡就是空负面；默认 `cfg=3.0` 下非空值会真的进图，
  没有实测过的负面不随示例交付。
- README 的「打开就能跑」路径、LoRA 说明与示例默认值同步。

### 健壮性（深度审计）
- **JSON 数值口径两侧统一**：`1536.0` 这类整值浮点按整数接受并归一为 int；`NaN` / `Infinity` /
  `1e400` 一律拒绝。Python 之前会被 `json.loads` 放行 NaN，写出的 state 浏览器读不了。
- **原型链名字不再当 role**：`toString` / `constructor` / `__proto__` 直接报「不认识」。
- **零宽文本判空**：只有空白 / 零宽 / 方向控制字符的 `caption` / `text` 视为空（JS 与 Python 同口径）。
- **导出量化不塌缩**：bbox 量化后宽度 < 0.001 时向外补到 0.001，导出的 elements 保证能再导入。
- **撤销历史加字符预算** 8,000,000（仍最多 100 步），避免 1MB 级 state 把内存吃到 100MB。
- **编辑器上限前置**：画框 / 复制阻止第 257 个元素，导入超过 256 项的 state 直接拒绝；
  state > 1MB 在状态栏警告、排队直接拒绝；排队按钮在请求完成前禁用，防连点重复入队。
- **导入不再静默修正**：`readState` 的自动修正与警告会在提示条 / 状态栏显示。
- 新增 `tests/test_adversarial.py`（57 项恶意 / 极端输入）；`check_editor_ui` 96 → 109 项。

### 端到端矩阵：修掉「只剩 4 格能跑」的假绿
- 起因（2026-10-28 实测）：`tools/e2e_matrix.py` 首跑 **10/16**，且失败分布极整齐 ——
  **4 格全过、3/6/8/9/1 格全挂**。这个分布本身就排除了显存/权重/GPU 那类原因。
- 根因不在节点：10-27 加的 `board_mismatch` 守卫会拿「全局段写的格数」与布局对账，
  而脚本的 `pad_prompt` **只补 `[PANEL n]` 段、不改全局段那句「恰好 4 个面板」**。
  守卫上线那天起矩阵就只剩 4 格能跑，而端到端报告同期被移出公开树，
  `CONTRIBUTING.md` 那句"发布前全矩阵至少跑一次"成了没人验的空话。
- 修法（只改脚本，不动节点）：`pad_prompt` → `fit_prompt`，补/截 `[PANEL]` 段的同时
  用 `core.prompt.COUNT_RE` 同步全局段计数（保留原措辞）；n 相等时原文逐字节不动。
- 重跑 **16/16，退出码 0**（5m47s）。矩阵现在真覆盖 1/3/4/6/8/9 格 × 横竖屏 × 无缝/粗缝 ×
  小画布 × 整张/逐格 × layout/plain。
- 教训写进 `RELEASING.md`：**加了契约守卫，必须当天重跑端到端**；端到端别接 `| tail`
  （管道吃掉退出码），也别让它写 `docs/02-e2e-matrix.md`（该文件已移出公开树，写出来就是未跟踪文件）。

### 开源卫生：两个公开面、三份黑名单、一条铁律
- 审计口径原来只覆盖 **registry 包范围**（`core/ v3/ web/ examples/` + `docs/*.md`），
  而 `tests/` `tools/` `CHANGES.md` `.github/` `pyproject.toml` **在 GitHub 上人人可读**、
  从来没被扫过。S2 现在两个面都查，并加「本机用户名 / 主机名」这一维。
- **文件内容里的内部措辞**一直是空白：实测 5 处（协作者代号 4 处、直接引述对话 1 处），
  而当时 14 道闸全绿。新增 S15，与提交信息检查（S9）**共用同一份词表** `INTERNAL_VOICE`
  （词表用 `\u` 转义写 —— 门禁源码本身也在公开面上）。
- **黑名单只写在 `.git/info/exclude`** 是结构性漏洞：那文件不随克隆走。
  `docs/_local/`（26 MB 私有对照图 + 一次性脚本）就只被它挡着，换个克隆 `git add -A` 全带走，
  而任何门禁都看不出来（"本来就没被跟踪"）。新增 S16：工作树不许有「未跟踪且未被忽略」的文件，
  且 `info/exclude` 的每条规则必须在 `.gitignore` 里也有。规则已全部搬进 `.gitignore`，
  `tools/probe_*.py` 两个一次性探针移进协作面目录。
- **二进制也要查**：ComfyUI 存图会把整条 workflow JSON 塞进 PNG 的 `tEXt` 块（含本机路径、
  模型名、提示词原文）。新增 S17，只允许结构性块。
- **S9 分已推送/未推送**：未推送的判红（`rebase` 即可），已推送的只报数不判红 ——
  改它要 force push + 重建 tag，那是维护者的决定；永久红的闸等于没有闸。
- `check_release_ready` 42 → 48 项。

### 第 6 个节点的示例补齐（`MossPromptCompiler`）
- 起因（2026-10-28 实测）：6 个节点里 5 个有示例工作流，**只有草稿编译没有** ——
  而它恰恰是最需要说明「往哪填、输出拿去干嘛」的那个（零模型，用户不知道 state 有什么用）。
- 新增 `examples/moss-prompt-compiler.json`：一份 layout 草稿 → 编译 → 三个 `PreviewAny`
  分别显示 `prompt`（原生布局 JSON）/ `report`（编译时做了什么）/ `state`（粘进编辑器导入框）。
  只用宿主核心节点（`MarkdownNote` / `PreviewAny`）+ 本包，**不加载任何模型**，真跑 `success`。
- 生成器 `tools/make_compiler_example.py`：`widgets_values` 按位存，手写错一位就静默串位，
  所以槽位与连线在同一次定义里产生，再由 `check_ui_workflow.py` 拿 `INPUT_TYPES()` 反查。
- `check_ui_workflow` 88 → 98 项（7 份），`ci.yml` 与 `CONTRIBUTING` 同步；
  README 中英各补一行入口。

## 0.2.2 — 2026-10-24

### 新增
- **布局编辑器撤销 / 重做**：`Ctrl/⌘+Z`、`Ctrl/⌘+Shift+Z`（或 `Ctrl+Y`）与工具栏 `↶` `↷`。
  历史在编辑器内部（最多 100 步），记的是 `editor_state` 快照；同一元素连续输入 caption / text /
  palette 或连续方向键微调合并为一步；外部改了 widget（打开工作流、宿主级撤销）时清空历史，
  不与宿主的撤销互相串。输入框里的 `Ctrl+Z` 仍归浏览器。
  - 纯函数 `makeHistory` / `recordHistory` / `stepHistory` 入 `check_editor_ui.py`（58 → 68 项）；
    真实宿主里逐键验证（维护者本地记录，未随包分发）。

### 修复
- **逐格模式段数不足时明确报错**：`[PANEL n]` 段数少于格数，原来走到拼版才报「格数不符」，
  现在采样前就报「逐格模式要求每格都有一句描述」。负面提示词只编码一次。
- **逐格 `init=plain` 剥掉裸格数词**：「4 个面板的故事板」这类说法不再漏进单格提示词。
- **拼版几何改为直接按解码图尺寸求右 / 下补边**，布局宽高与解码图逐像素相等；解码图小于布局时明确报错。
- **编辑器前端与 Python 校验口径对齐**：1×1 空白宫格不再被前端判非法；读到 <64 或 >4096 的
  画布宽高时修回默认值，而不是保留后再报警告。

### 健壮性
- **`spec` 读入上限**：超长、单格越界、缝 / 边距越界、`w/h` 与字段自相矛盾的 spec 一律拒绝，
  防手改或恶意工作流按伪造尺寸申请超大张量。本包自产的全部 spec 均可回读（3 万组几何扫测）。
- **画布像素上限 64MP**：宫格模板与空 latent 超出即报错。

### 维护
- 三个发布工具补 SPDX 头；`docs/10-contract.md` 写明 spec / 画布上限与空白宫格例外；
  各处机检项数对齐实跑。
- 发布前 GPU 复验（4 格 16:9 / 4 步蒸馏 LoRA）：逐格 `init=layout` 62 s、逐格 `init=plain` 56 s、
  整张 17 s，尺寸 / 宫格线 / 各格互异全过；3 段提示词喂 4 格逐格在采样前即被拒（2 s）。

## 0.2.1 — 2026-10-24

### 修复
- **`.comfyignore` 不再排除 `THIRD-PARTY-NOTICES.md`**：0.2.0 的 registry 包里 README 指向该文件
  但文件缺失。`check_release_ready.py` 的 must_keep 与 `make_dist.py` 的必需文件表同步。
  0.2.0 的 registry changelog 不可改，本版 changelog 承担更正。
- **README / README_EN 校正**：整张 vs 逐格的默认行为、宫格线由节点重绘、布局遵循度不量化、
  1–2MP 只适用于 8/9 格、`init=plain` 只改提示词投递；删除已复核推翻的
  「官方 50 步 / cfg 3 崩坏」旧结论。
- 示例海报的自拟中文标题改为中性英文，便于直接分享；用户示例收敛为 4 份，
  旧版 8 格 / 0.1.x 20 槽工作流转为 `tests/fixtures/` 夹具。

### 新增
- **`tools/make_dist.py`**：按 `git ls-files` + `.comfyignore` 构建干净分发包（可选 `--zip`）；
  `--verify` 把产出当独立包加载，断言 5 节点注册、前端在位、编辑器 / 宫格 / 裁格可跑、
  无模块来自原仓。不读工作区临时件。
- **CI `release-preview` 作业**：发布卫生闸（31 项）→ 构建干净包 → 上传 zip artifact（7 天）；
  宿主作业增 `make_dist.py --verify`（14 项）。新增 `workflow_dispatch` 手动触发。

## 0.2.0 — 2026-10-24

### 新增
- 🆕 **新节点「🎨 布局编辑器」（`MossLayoutEditor`）**：画布上摆元素（人物 / 物体 / 文字），
  一键出 MOSS 布局提示词。两种模式：**单张**（多元素 layout JSON → `CLIPTextEncode` + 普通
  `KSampler`）与**宫格**（`[STYLE]` + `[PANEL n]` + `spec` → 生成节点）。
  state v3 存进一个 STRING widget；8 个预设、拖框 / 吸附 / 快捷键、中英双语。
  纯前端 + 确定性编译，**不加载模型**。
  - 示例：`examples/moss-layout-editor-poster.json` / `examples/moss-layout-editor-storyboard.json`。
  - 规格与前端实现：维护者本地记录（未随包分发）。
- 🆕 **生成节点新增 `mode` 参数（整张 / 逐格）**：逐格模式下每格**独立采样**再按几何坐标拼版。
  **生成节点本身不变**——逐格是"一个节点多一种模式"，不是"多一个节点"；0.2.0 的节点总数是 5。
  - **为什么**：整张生成时模型把全局描述摊薄到 N 个区域，每格各"想象"一遍角色 ⇒
    同一角色在不同格里帽子颜色、脸型会飘。逐格时每格独占一次注意力 ⇒ 角色特征明显统一。
  - **实测**（4 格 / 16:9 / 8 步 / cfg 1.0 / 4 步蒸馏 LoRA，人脸几何余弦）：
    整张 **93.1** → 逐格 **99.1**；兽脸场景（黑猫）目检：整张是"四只不同的猫"，
    逐格是"同一只猫"。
  - 逐格模式会**自动清理**全局描述里的多格措辞（"恰好 4 个面板"之类）——
    不清理的话模型照样画多格（实测：只给 1 个 bbox 也会画出 4 格）。
- 🆕 **生成节点新增 `sample_edge`（逐格采样边长，绝对像素，默认 1024）**：
  逐格模式下单格的**采样**分辨率不受整张宫格尺寸约束，采样完再缩回最终格子 ⇒ 每格更清晰。
  给绝对像素而不是倍数，与宿主 `Empty Flux 2 Latent` 的心智一致，
  也让"画质需求"与"排版需求"互不绑架。
  ⚠ **不是越大越好**：实测 496 最一致但偏糊，1024–1408 更清晰但色调更飘。
- 🆕 **宫格几何节点新增 `elements` 输出**：画布的元素格式
  （`{type, desc, bbox}`，bbox **0–1000 归一化**、`[y0, x0, y1, x1]`），
  坐标已验证与真实格子逐像素吻合。接给宿主 `Create Bounding Boxes` 的 `bboxes` 字符串输入，
  就能在画布上拖框微调构图。**新增输出追加在末尾，既有接线不受影响。**
  ⚠ `elements` 与 `layout_json` 不是同一套格式：后者是完整布局 JSON（绝对像素）。
- 🆕 `docs/07-consistency.md`：跨格一致性的三条机理（语义锚定 / 像素复制 / 随机性抑制）、
  两个场景的实测矩阵、提示词纪律、旋钮边界。
- 🆕 布局编辑器的开发路线（数据互通 / V3 声明 `io.BoundingBox` / 自写前端扩展）与取舍
  ——维护者本地路线稿，未随包分发。

- 🆕 **新节点「故事板 · 贴回」（`MossStoryboardPaste`）**：把改好的单格按 `spec` 坐标
  **逐像素**贴回宫格，输出改好的整张宫格。只动目标格的内容区，宫格线与其它格一个字节都不碰。
  用途是"只改一格、不重跑整张"——重跑会把所有格的种子一起动掉。
- 🆕 **裁格节点新增可选 `index` 输入与第 4 个输出**：填 -1（默认）= 输出全部格子，行为与之前一致；
  填 0 起的格号 = 只输出那一格，并把格号送到 `index` 输出。接给贴回节点就**不用手打格号**，
  不会和"裁的是哪一格"对不上。新输出追加在末尾，既有接线不受影响。
- 🆕 `paste_panel` / `paste_marker` 几何内核（`core/pixels.py`）：`crop_panels` 的逆运算，
  尺寸不符时重采样并明确告知，`shave` 与裁格不一致时内容会偏移且**不报错**——report 会写出
  实际写入的矩形，请核对。
- 🆕 `docs/06-vae-memory.md`：FLUX2 VAE 解码显存账（公式、宿主版本分水岭、各档画布复算表）。
- 🆕 `tools/make_edit_workflow.py`：MOSS 编辑示例工作流生成器（Moss-Image-Edit LoRA 链路）。

### 修正
- 🔴 **诊断口径修正：画质瓶颈是"单格有效分辨率"，不是"整图像素量"。**
  9 格 1:1 时每格只分到 **0.26MP**（远低于 MOSS 的 1–2MP 训练区间），
  而整图看着还挺正常。节点的 `report` 现在**同时**报这两个数字并给出处置建议。
- 逐格模式的单格缩放改用 `area` 插值（PyTorch 的抗锯齿下采样只对 float32 实现，
  fp16 + CUDA 会抛 `NotImplementedError`）。
- 逐格模式的缩放顺序改为**先每格缩到最终格子尺寸、再拼版**（反过来会让宫格线被缩细，
  且多一次全图重采样）。
- README 的节点数、机检项数对齐实跑（`test_core` 294 ·
  `test_contract` 113（`--no-host`）· `test_editor` 102 · `test_cross_language` 50 · `smoke_nodes` 41 · `test_i18n` 54）。

### 文档
- 📉 **README 新增「显存要多少」**：VAE 解码是这条链路真正的显存峰值（不是采样）。
  宿主 **v0.42.1 及更早的正式版**把解码显存预估成实际的 **4 倍**，2026-10-23 之后合入
  master 的 `d07dff4` 才修正——**该提交尚未进入任何正式版**。同一张 2032 × 576 画布，
  旧宿主约 4.8 GB、新宿主约 1.2 GB。附「你的安装方式落在哪一列」判定表，以及显存不足
  退回分块解码会留下接缝的排查路径（日志关键字 `retrying with tiled VAE decoding`）。
- README 与 README_EN 同步补：`--fp32-vae` 会让省显存路径**静默失效**（只在 fp16/bf16 生效）。
- README / README_EN 补 `mode` 与 `sample_edge` 的用法与实测数据；
  补 `elements` 输出说明与官方画布的接法。

---

## 0.1.2 — 2026-10-23

### 移除
- 🔴 **删掉图生图（模板初始化）通路。** MOSS 不以参考图条件化（官方 limitation 明写"不支持编辑"），
  此前三组实测（35 步 / 0.85 纯黑、12 步 / 0.6 与 4 步 / 0.5 残影）已证明只要 sigma 真截断，
  模型就不改动 latent。留着"能选但必然失败"的选项只会让用户白跑一遍，所以直接收掉：
  - `init` 从 `auto/layout/template/both/plain` 收成 `auto/layout/plain`
  - 采样器不再有 `template` 输入，起点恒为空 latent
  - 删除 `examples/moss-storyboard-8panels-template.json`（它是 35 步 / 0.85 / `init=both`，
    正是会出纯黑的那组）
  - `core/sampling.encode_image` 一并删除（无调用方）
- ⚠ **破坏性变更**：已经接了模板线的旧工作流，载入后该连线会失效（不报错，只是不再连上）。
  用 `tools/make_ui_workflow.py` 重新生成即可。

### 文档
- README / README_EN「这一版做不到的」改为直说"不做图生图"；`steps` / `cfg` 的 tooltip 按两档配方写
  （官方模型 30–50 步 / cfg 3–5；4 步蒸馏 LoRA 4–8 步 / cfg 1.0）。
- `denoise` 的说明改写成"没有图生图，调 <1 只会少跑几步"，删掉原先"模板初始化时必须 <1"那句。

### 计数
`test_core 294` · `test_editor 102` · `test_contract 129`（--no-host 113）· `test_sampling_contract 18` ·
`test_i18n 54` · `test_cross_language 50` · `test_panel_assembly 5` · `smoke_nodes 41` ·
`host_loader_check 19` · `check_ui_workflow 4 示例 + 2 夹具共 88` · `check_frontend 9` ·
`check_editor_ui 58` · `check_release_ready 31`


## 0.1.1 — 2026-10-23

### 修复
- 🔴 **示例工作流没进 registry 包**。`examples/` 里那个带说明的工作流用了中文文件名，
  而 `comfy node pack` 按 latin-1 解路径 ⇒ 中文名被**静默跳过**（只在打包日志里留一行
  `File not found. Not including in zip`），README 却叫用户去开它。
  现已改名为 `moss-storyboard-4panels-ui.json`；`tools/make_ui_workflow.py` 写宿主目录仍用中文名
  （用户友好），写仓库副本强制 ASCII。
- `tools/check_release_ready.py` 的 S4 把「包内中文文件名」从警告改为**判红** ——
  这类缺陷两边都不报错，只能靠发布前拦。

### 计数
`check_release_ready 27/0` · 其余 8 条全绿


## 0.1.0 — 2026-10-23

首个版本：三个节点、两条出口、八道机检、中英双语界面。

### 节点与核心
- `MossStoryboardLayout`：格数 → 整数分解 → 按画布比例挑排法（4:1 容忍线 + 近打平按横条惯例）；
  单格/画布对齐 16；输出 `spec`（几何单线传递）、`layout_json`（MOSS 原生 bbox 格式）、`template`、`report`。
- `MossStoryboardSampler`：段落拆分 → 布局 JSON → 采样 → 解码 → 按坐标复绘宫格线 → 裁格。
- `MossStoryboardCrop`：按 `spec` 或 `columns×rows` 精确裁格，附裁切线预览；不整除直接报错。
- `core/{layout,prompt,pixels,sampling}.py`：几何/提示词纯 Python，像素只依赖 torch，采样封装宿主 API。
- `v3/_compat.py`：V3 schema 由 `INPUT_TYPES()` 自动生成（参数表唯一真相源）。

### 中英双语与悬浮提示
- `web/moss_sb_i18n.js`（纯函数，可 node 单测）+ `moss_sb_i18n_ui.js`（挂钩、中/EN 按钮、抗覆盖兜底）。
- 铁律：只改 `label`/`title`/`tooltip`/`placeholder`，绝不改 `name`/`value`/`options.values`；
  中文提示只在 `nodes.py` 写一份，`TIPS` 只存 `en`；自定义标题不覆盖、只换前缀。
- **多行文本框的标题是 `placeholder`，不是 `label`**：DOM `<textarea>` 父容器 `size-full`
  把画布自绘标题盖住了。只写 label ⇒ 界面上仍是 `positive` / `negative`（真机实证）。
  现在两者同写，`tests/test_i18n.mjs` 6.20–6.23 锁这条。
- **`negative` 默认收进「高级」**（`advanced: True`）：MOSS 实测 cfg=1.0，
  宿主在 cfg=1 时跳过负面分支（`comfy/samplers.py::sampling_function`），
  那段不进计算图。放普通区只会让人误以为"写了就生效"。
  `test_contract.py::test_sampler_shape` 锁这条。
- `WEB_DIRECTORY` 已接；宿主 `/extensions` 实测出现两个 JS（HTTP 200）。

### 测试（全部可复跑）
- `tests/test_core.py` 282 项：几何/提示词/像素（零 GPU）
- `tests/test_contract.py` 54 项：参数表自洽、错误路径、依赖边界、仓库卫生、词表完整性
- `tests/test_sampling_contract.py` 18 项：用桩锁住宿主采样 API 的入参类型
- `tests/test_i18n.mjs` 46 项：中英切换纯函数（node）
- `tools/smoke_nodes.py` 25 项 / `tools/host_loader_check.py` 16 项 / `tools/check_ui_workflow.py` 15 项/文件
- `tools/e2e_matrix.py` 14 用例真跑：3/4/6/8/9 格、横竖屏、无缝、粗缝、1 格、edge=256、plain

### 0.1.0 实测结论（详见 README「已知边界」）
- **模板 img2img 在 MOSS 上未生效**（下一版会重做并复测）：35步/0.85 纯黑、12步/0.6 与 4步/0.5 均模板残影
  ⇒ 只要 sigma 真截断，模型就不改动 latent。默认改纯布局路线。
- **低步数 denoise 静默失效**：宿主按 `int(steps/denoise)` 重算 sigma，4 步时 >0.8 一段都不截；
  `report` 现在打印「名义 / 实际」。

### 开发期修掉的真 bug（记下来防回归）
1. `sample_custom` / `prepare_noise` 要 latent **张量**，传 `{"samples":...}` ⇒ `'dict' object has no attribute 'is_nested'`。
2. UI→API 转换：前端会把 `forceInput` 输入挪到 `inputs` 数组前面，按服务端 `input_order` 硬套会把
   `positive` 接成 IMAGE。改为按节点自身 `inputs` 顺序解析。
3. V1/V3 槽位差一格：V1 下前端自动给 `seed` 加"生成后控制"，V3 不声明就没有 ⇒ 同一工作流参数整体错位。
4. 排法在单格长边 512↔496 之间翻转（6/8 格对 16:9 的 log 距离几乎打平）。

### 已知欠账
- 布局框对"每格构图是否照办"**未做量化评分**（MOSS 布局能力官方标 experimental）。
- `README_EN.md` 为节选翻译，**未**做节级 hash 机检。
- CI 只跑 CPU 单元测试与宿主装载；**成片质量**不在 CI 覆盖范围内（需要 GPU + 权重）。
