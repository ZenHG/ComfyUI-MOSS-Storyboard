# 发布说明（Release Notes）

> **这份文件写给用户，不是写给开发者。**
> **两个渠道的用户可见说明都取这里**（唯一真相源）：Comfy Registry 的 changelog 与 GitHub Release 的正文。
> 开发者细节（实现方案、实测证据、发布过程）留在 [`CHANGES.md`](CHANGES.md)，**不要往这里搬**。
>
> 格式规矩（正文见 [`RELEASING.md`](RELEASING.md) §6）：
>
> - 一版一节，**新的在最上面**，标题固定 `## x.y.z — YYYY-MM-DD`。
> - 每节**必须中英双语**：中文在前，一行 `<!-- EN -->` 分隔，英文在后。
>   英文**独立成文**（不是逐句直译 —— registry 页面上大部分读者只看英文）。
> - 只写**用户能感知的变化**。有行为变化必须用 🔴 点明「要不要动配置」。
> - 节内长度**分段算**：**中文段 ≤700 字符 · 英文段 ≤1700 字符**
>   （`tools/check_release_ready.py` 与 `tools/release.py` 都按这个口径拦）。
> - 每节结尾固定两行：中文 `**升级动作**：…` / 英文 `**Action required**: …`。
>
> 为什么要有它：registry 的 changelog **发布后不可改**（版本接口只收 `GET`/`OPTIONS`），
> 所以唯一的防线是**发布前把它写对**。

---

## 0.2.0 — 2026-10-24

**一句话变版式，也能精确出故事板。**

- 🆕 新节点「🎨 布局编辑器」（`MossLayoutEditor`）：在画布上摆人物/物体/文字，8 个预设；
  单张出海报/封面，宫格出故事板。编辑状态存进节点，改完直接重跑；不加载模型，零额外依赖。
  示例：`examples/moss-layout-editor-poster.json` / `-storyboard.json`。
- 🆕 生成节点多一个 `mode`：`逐格` = 每格独立采样再拼版。整张生成时角色描述被摊薄到每格，
  帽子颜色/脸型会飘；逐格每格完整读一遍。**实测（`sample_edge=512`）人脸一致度 93.1 → 99.1**；
  黑猫场景整张是"四只不同的猫"、逐格是"同一只猫"。代价：耗时约 N 倍。
- 🆕 生成节点多一个 `sample_edge`（逐格时每格的采样长边，默认 1024）⇒ 每格更清晰。
  ⚠ 越大不一定越好：496 最统一但偏糊。
- 🆕 新节点「🎬 故事板 · 贴回」：把改好的那一格按 `spec` 坐标逐像素贴回，宫格线与其它格不动。
- 🆕 裁格节点多可选 `index` 与输出 `index`：接给贴回，格号全程不用手打。
- 🔴 画质瓶颈是**每格自己的像素量**，不是整图：9 格 1:1 每格只有 0.26MP。`report` 同时报两个数。

**升级动作**：重启 ComfyUI 后刷新浏览器页面（F5）。已有工作流无需改动；新参数/新输出都追加在末尾。

<!-- EN -->

**One sentence to a layout; storyboards stay exact.**

- NEW 🎨 Layout Editor (`MossLayoutEditor`): place people, objects and text on a canvas with 8 presets;
  single mode makes posters/covers, grid mode makes storyboards. The state lives in the node and stays
  editable; no model is loaded and there are no extra dependencies. Examples:
  `examples/moss-layout-editor-poster.json` / `-storyboard.json`.
- NEW `mode` on the Generate node: `Per panel` samples every panel on its own, then assembles the board.
  In one pass the description is spread thin and hat colour / face shape drift; per panel each reads it
  in full. **Measured (`sample_edge=512`): face identity 93.1 → 99.1**; a black-cat scene gives four
  different cats in one pass and the same cat per panel. Cost: about N times the time.
- NEW `sample_edge` on the Generate node (each panel's sampling long edge, default 1024) ⇒ sharper panels.
  Not always better: 496 is more uniform but soft.
- NEW `MossStoryboardPaste`: writes a corrected panel back at its exact `spec` coordinates, pixel for
  pixel; grid lines and other panels stay untouched.
- NEW optional `index` parameter and output on Crop: wire it into Paste and never type a panel number.
- 🔴 The quality bottleneck is the pixels **each panel** gets, not the board total: a 1:1 nine-panel board
  gives every panel 0.26MP. `report` prints both.

**Action required**: restart ComfyUI, then hard-refresh the browser (F5). Existing graphs need no changes;
new parameters and outputs are appended.

---

## 0.1.2 — 2026-10-23

**去掉了一个选了必然失败的选项：图生图。**

- 🔴 「初始化方式」从五个选项收成三个：`自动（纯布局）/ 纯布局 / 原样提示词`。
  原来的「仅模板」「布局 + 模板」已删除——MOSS 不以参考图条件化（官方已声明不支持编辑），
  实测这三组参数只会得到纯黑或模板残影，留着只会让你白跑一次。
- 采样器不再有「模板图」输入。起点永远从纯噪声开始。
- 如果你之前接过模板线：工作流还能打开，但那根线会失效，重新生成一次工作流即可。
- **更正（2026-10-24）**：此前关于官方非蒸馏配方（50 步 / cfg 3）“崩坏扭曲”的结论已过时；
  复核可正常跑。当前配方见 0.2.0 的两档建议（官方模型 30–50 步 / cfg 3–5）。

**升级动作**：从 Manager 更新到 0.1.2。建议重新生成一次示例工作流，避免旧的模板线残留。

<!-- EN -->

**Removed an option that could only fail: img2img.**

- 🔴 The `init` menu went from five choices to three: `auto (layout) / layout / plain`.
  "Template only" and "layout + template" are gone. MOSS is not conditioned on reference images
  (the official limitation says no editing), and every measured parameter set produced either a
  black frame or template ghosting — keeping it only cost you a wasted run.
- The sampler no longer has a template input; the latent always starts from pure noise.
- If you had a template wire connected: the workflow still opens, but that wire becomes inert.
  Regenerate the workflow once to clean it up.
- **Correction (2026-10-24)**: the earlier claim that the official non-distilled recipe
  (50 steps / cfg 3) produced corrupted frames is outdated; a re-check runs normally.
  See the two-band recipe in 0.2.0 (base model: 30–50 steps / cfg 3–5).

**Action required**: Update to 0.1.2 from the Manager. Regenerating the example workflow is
recommended so the old template wire does not linger.


## 0.1.1 — 2026-10-23

**修一个只有装完才会发现的问题：从 Manager 安装的包里，示例工作流不在。**

- 0.1.0 的示例工作流用了中文文件名，发布打包时被跳过 —— 于是 `examples/` 下那个带分组和说明的工作流装完找不到。已改成英文文件名。
- 功能没有任何变化。

**升级动作**：从 Manager 更新到 0.1.1 即可；已手动放过示例的不用动。

<!-- EN -->

**Fixed an issue only visible after installing: the example workflow was missing from the Manager package.**

- The 0.1.0 example used a non-ASCII filename, which the packer silently skipped — so the annotated workflow under `examples/` never arrived. Renamed to ASCII.
- No functional changes.

**Action required**: update to 0.1.1 from the Manager. If you already placed the file manually, nothing to do.

## 0.1.0 — 2026-10-23

**MOSS-Image 的故事板宫格节点，首个版本。**

- 一次生成**一张画布**，分成 3 / 4 / 6 / 8 / 9 格，同时把每一格单独裁出来。
- 每格写一句描述（`[PANEL 1]` …），节点会转成 MOSS 原生的布局提示词，锁住每格画什么。
- 宫格线由节点在生成后按坐标重绘，不会歪、不会断口。
- 每个本包节点上有「中 / EN」按钮，可切换中英文；只改显示，**不动你已保存的工作流**。
- 支持 10 种单格比例（16:9、9:16、1:1 等），画布尺寸自动算，宽高自动对齐 16。

**这一版的两条限制**

- 用模板图做初始化（`init=template` / `both`）得不到有效重绘：会得到模板残影或纯黑。请用默认的纯布局方式。
- 步数很低时「降噪强度」可能不生效：4 步时调到 0.85 实际仍是 1.0。节点会在 report 里打印实际值。

**升级动作**：首次安装 —— 重启 ComfyUI 后刷新浏览器页面；`CLIPLoader` 的 `type` 必须选 `moss_image`。

<!-- EN -->

**Storyboard grid nodes for MOSS-Image — first release.**

- One run produces a **single canvas** split into 3, 4, 6, 8 or 9 panels, plus each panel cropped out.
- Write one line per panel (`[PANEL 1]` …); the node turns them into MOSS's native layout prompt so each panel knows what to draw.
- Grid lines are redrawn by the node after generation — they never wobble or break.
- Every node in this pack has a Chinese / EN toggle; display-only, **your saved workflow is untouched**.
- Ten panel ratios (16:9, 9:16, 1:1 and more); the canvas size is computed and 16-aligned for you.

**Two limits in this version**

- Template initialisation (`init=template` / `both`) does not produce a usable repaint — you get ghosting or a black image. Use the default layout mode.
- At very low step counts the denoise strength may be ignored: at 4 steps, 0.85 is effectively 1.0. The node prints the effective value in its report.

**Action required**: first install — restart ComfyUI and hard-refresh the browser; the `CLIPLoader` type must be `moss_image`.
