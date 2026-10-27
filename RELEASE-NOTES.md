# 发布说明（Release Notes）

> **这份文件写给用户，不是写给开发者。**
> **两个渠道的用户可见说明都取这里**（唯一真相源）：Comfy Registry 的 changelog 与 GitHub Release 的正文。
> 开发者细节（实现方案、实测证据、发布过程）留在 [`CHANGES.md`](CHANGES.md)，**不要往这里搬**。
>
> 格式规矩：
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

## 0.2.3 — 2026-10-27

**逐格模式不再交出空的 latent；稿子与宫格对不上时，采样前就报错。**

- 🔴 逐格模式的 `latent` 从空改为「每格一个 latent 的 batch」（`{"samples": [N,C,H,W]}`，行主序）。
  接 `VAEDecode` 会一次解出 N 张单格图；整张模式与画面均不变。
- 🔴 稿子与布局对账：`[PANEL n]` 段数、全局段里写的「N 个面板」都必须等于实际格数，否则采样前报错，
  并给三条出路（改稿 / 改布局 / `init=plain`）。以前照跑，会把 4 个节拍在 8 格里画两遍。
- 🆕 布局节点新增可选 `char_boxes`（逗号分隔、编号从 1 起，如 `1, 3`）：把指定格标成人物框。
- `index` 上限 64 → 143；`columns`/`rows` 统一为 12（以前能选 20，但必定报错）；三处 `shave` 统一 64。
- report 首行说明本次属哪一档口径（SFT 模型卡 / 蒸馏 LoRA / 混搭），混搭不再静默。

**升级动作**：从 Manager 更新到 0.2.3，重启 ComfyUI 并刷新浏览器（F5）。已有工作流无需改动；
只有依赖「逐格 latent 为空」的自定义接线要改，稿子段数与格数不符的工作流会被拦下（按报错任选一条出路）。

<!-- EN -->

**Per-panel mode no longer returns an empty latent; a prompt that disagrees with the grid now fails before sampling.**

- 🔴 Per-panel `latent` changed from empty to a batch of one latent per panel
  (`{"samples": [N,C,H,W]}`, row-major). `VAEDecode` then yields N single-panel images.
  Whole-canvas mode and the images themselves are unchanged.
- 🔴 Prompt/layout reconciliation: the `[PANEL n]` section count and any "N panels" phrase in
  the global section must both match the panel count, otherwise the node raises before sampling
  with three ways out (fix the script / fix the layout / `init=plain`). It used to run anyway and
  could paint four beats twice across eight panels.
- NEW optional `char_boxes` on the layout node: comma separated, 1-based (e.g. `1, 3`).
- `index` max 64 to 143; `columns`/`rows` capped at 12; the three `shave` inputs share one cap of 64.
- The report's first line names the sampling profile (SFT model card / distilled LoRA / mixed).

**Action required**: Update to 0.2.3 from the Manager, restart ComfyUI and hard-refresh the
browser (F5). Existing workflows need no changes; only custom wiring that relied on the empty
per-panel latent must be updated, and scripts whose section count disagrees with the panel count
are now stopped — pick any of the three options in the error message.

---

## 0.2.2 — 2026-10-24

**布局编辑器支持撤销 / 重做；生成节点对写错的参数报得更早、更清楚。**

- 🆕 布局编辑器可撤销 / 重做：`Ctrl+Z` / `Ctrl+Shift+Z`（或 `Ctrl+Y`），工具栏也有 ↶ ↷ 按钮。
  最多 100 步；连续打字、连续用方向键微调各算一步。打开别的工作流后历史清空，不会和 ComfyUI 自己的撤销混在一起。
- 🔴 逐格模式下描述段数少于格数时，现在采样前就报错并说明怎么补，不再跑完才在拼版时失败。
- 🔴 手改过、前后矛盾或尺寸超限的 `spec` 现在直接拒绝；本包节点生成的 `spec` 不受影响。整张画布上限 64MP。
- 逐格模式不再把「4 个面板」这类格数说法漏进单格描述；拼版尺寸与解码图逐像素对齐。
- 编辑器里 1×1 空白宫格不再被误判非法；过小或过大的画布宽高会自动修回默认值。

**升级动作**：从 Manager 更新到 0.2.2，重启 ComfyUI 并刷新浏览器（F5）。已有工作流无需改动；
只有手改过 `spec` 且数值前后不一致时会报错——改回由布局节点或布局编辑器输出的 `spec` 即可。

<!-- EN -->

**Undo / redo in the Layout Editor; the Generate node now rejects bad inputs earlier and more clearly.**

- NEW undo / redo in the Layout Editor: `Ctrl+Z` / `Ctrl+Shift+Z` (or `Ctrl+Y`), plus ↶ ↷ toolbar buttons.
  Up to 100 steps; a run of typing or a run of arrow-key nudges counts as one step. History is cleared when
  another workflow is loaded, so it never mixes with ComfyUI's own undo.
- 🔴 In per-panel mode, a prompt with fewer panel lines than panels now fails before sampling, with a message
  saying how to fix it, instead of failing at assembly after all the sampling time was spent.
- 🔴 A hand-edited `spec` that contradicts itself or exceeds the size limits is now rejected; every `spec`
  produced by this pack's nodes still loads. The whole canvas is capped at 64MP.
- Per-panel mode no longer leaks phrases like "4 panels" into each panel's own description, and the assembled
  board matches the decoded image pixel for pixel.
- The editor no longer flags a blank 1×1 grid as invalid, and a canvas width or height outside the allowed range
  is reset to the default instead of being kept with a warning.

**Action required**: Update to 0.2.2 from the Manager, restart ComfyUI and hard-refresh the browser (F5).
Existing workflows need no changes; only a hand-edited, inconsistent `spec` now errors — reconnect the `spec`
output of the Layout node or the Layout Editor.

---

## 0.2.1 — 2026-10-24

**打包与文档修复：0.2.0 的安装包里缺了一份声明文件，README 里也有几处过时表述。**

- Manager 安装包补齐「第三方声明」，包内 README 指向它时不再落空。
- README 校正：整张 / 逐格两种模式的默认行为、宫格线由节点重绘、布局遵循度不作数量承诺，
  以及文字能力的实测边界；删掉一条已被复核推翻的旧结论。
- 示例工作流精简为 4 份，示例海报里的自拟中文标题换成中性英文，方便直接分享。
- 节点功能与上一个版本相同，工作流和参数都不用动。

**升级动作**：从 Manager 更新到 0.2.1；已有工作流无需改动，重启 ComfyUI 即可。

<!-- EN -->

**Packaging and documentation fixes: the 0.2.0 install package was missing one notice file, and the README carried a few outdated statements.**

- The Manager package now includes the third-party notices, so the README link resolves inside the package.
- README corrected: default whole-board vs per-panel behaviour, that grid lines are redrawn by the node,
  that per-panel layout adherence is not promised, and the measured limits of text rendering; one claim
  that a re-check disproved has been removed.
- The example set is down to four workflows, and the poster example uses neutral English titles so it
  can be shared as-is.
- Node behaviour is unchanged; existing workflows and parameters need no edits.

**Action required**: Update to 0.2.1 from the Manager; restart ComfyUI. Existing workflows need no changes.

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
