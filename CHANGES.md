# CHANGES

## 未发布

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
  - 规格：`docs/08`；前端实现：`docs/11`。
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
- 🆕 `docs/08-visual-editor-plan.md`：宫格可视化编辑的后续开发路线（数据互通 / V3 声明
  `io.BoundingBox` / 自写前端扩展）与取舍。

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
- `docs/01` 与 README 的节点数、机检项数对齐实跑（`test_core` 294 ·
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
