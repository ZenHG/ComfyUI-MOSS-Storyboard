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

## 0.3.3 — 2026-10-30

**画布手感 + 导入导出**，节点数仍是 7，模型侧只改两个默认值。

- **四个对齐按钮**：水平/垂直等距、等宽、等高。等距按相等间隙、首尾不动；等大取最大值、贴边回夹。
- **工具栏加删除**（撤销/重做右边）；宫格模式禁用 —— 删一个会让格数与列×行对不上，几何算不出来。
- **宫格不再是死胡同**：导入/导出都转成 N 个框（单图），并提示丢了 spec 与逐格裁切。
- **preset / blend 不再丢**：以前导出再导入变「自由」+ 关融合，点「生成初始框」会按空框重来。
- **单图换脸修好**：默认 `face_pick` 改 `largest`（`right` 会挑到背景侧脸，蒙版只盖 0.7%，
  看着像没变）；`report` 新增蒙版内改动率，低于 2% 直接说原因。
- 新示例：单图换脸（自带演示图）、4 格黑白线稿漫画 JSON。
- 文档：README 拆成导览 + 节点手册；前端闸补 8 项汉化检查。

**升级动作**：不需要。旧工作流照常打开。

<!-- EN -->

### 0.3.3 — 2026-10-30

**Canvas ergonomics and import/export.** Still 7 nodes; two defaults changed on the model side.

- **Four align buttons**: distribute horizontally / vertically, equal width, equal height.
  Distribution equalises the gaps (not centres) and keeps the first and last box in place.
- **Delete on the toolbar** (next to undo / redo). It is disabled in grid mode: removing a panel
  would leave the panel count out of step with cols × rows, and the geometry fails outright.
- **Grid is no longer a dead end**: importing or exporting a grid state converts it to N boxes
  and says what was lost — same layout, no `spec`, no per-panel crops.
- **`preset` and `blend` survive a round trip.** They used to return as "free" with blending off,
  and "Generate boxes" under "free" wipes the layout.
- **Single-photo face swap fixed**: the default `face_pick` moved from `right` to `largest`
  (on a multi-face image `right` picked a small background face, 0.7% mask, looked like nothing
  happened). `report` now prints the inside-mask change rate and explains itself below 2%.
- **New samples**: one-photo face swap with demo images, and a 4-panel line-art comic JSON.
- Docs: the README is now a guide plus a node manual; the frontend gate gained 8 checks.

**Action required**: none. Existing workflows open unchanged.

## 0.3.2 — 2026-10-30

**整理版，功能没有任何变化。** 这一版只清掉文件列表里的残留、补上漏写的文档。

- 删掉 `README_EN.md` —— 它只有 132 字节，内容是「英文 README 已移到 README.md」。
  英文正文早就在主 README 里，这个页面点进去没有内容。
- 「顶层设计」那一页补上第 7 个节点（换脸）。它原来只写了 6 个，读的人会以为包只有 6 个节点。
- 贡献者清单补上两个测试、校正四处项数（测试项数变了但清单没跟着改）。

**升级动作**：不需要。代码、节点、参数、示例一个都没动。

<!-- EN -->

### 0.3.2 — 2026-10-30

**Housekeeping only — no functional change.** This release removes a leftover file and fills in
documentation that had fallen behind.

- Removed `README_EN.md` — a 132-byte stub saying "the English README moved to README.md".
  The English text has lived in the main README for a while; the page had nothing in it.
- The top-level design page now lists the 7th node (face swap). It only described 6, which made
  the pack look smaller than it is.
- The contributor checklist gained two tests and four corrected counts.

**Action required**: none. Code, nodes, inputs, examples — all unchanged.

## 0.3.1 — 2026-10-30

**「故事板 · 换脸」变成了一个蒙版局部编辑器** —— 节点数还是 7 个，没有新增。

- **`init` 多了一个取值 `empty`。** `source`（默认）吃源图做图生图；`empty` 不吃源图，
  从空画布生成 —— 参考图 + 提示词 → 一张带指定动作/姿态的图。换脸和生成是同一件事的两个特例，
  差别只有「从哪开始」。
- **蒙版换个位置就是另一种编辑。** 实测：蒙版给衣服 → 换衣服成立（针织纹理自然）；
  给头发 → 成立；**给背景 → 不成立**（未遮的人物会把背景拉回原样）。节点会在 `report` 里
  直接告诉你「蒙版覆盖多少、蒙版外改了多少」。
- **两档参数。** 换脸用默认的 8 步 / CFG 1.0 够快；改衣服、头发这类**材质**要 **30 步 / CFG 5**
  （`MOSS-Image-Edit-HQ/armV5` 用 CFG 3），同一张图质感差别明显。`report` 在步数 < 20 时会提醒。
- **`ref` 只给脸。** 给整张场景当参考会压掉新加的东西 —— 实测加一把椅子：给场景只画出一条红条，
  给脸部特写就出来了。
- 中文界面下 `init`、`face_pick` 两个下拉以前显示英文，现在有中文。

**升级动作**：不需要。新参数（`init` / `width` / `height`）排在参数表末尾、有默认值，行为与 0.3.0 一致；只有想用新能力（生成、改衣服/头发）时才需要动。

<!-- EN -->

### 0.3.1 — 2026-10-30

**"Storyboard · Face Swap" is now a masked local editor** — still 7 nodes, nothing added.

- **`init` gained a value: `empty`.** `source` (default) takes the source image and does img2img;
  `empty` does not take it and starts from an empty canvas — reference face + prompt → one posed image.
  Face swap and generation are the same operation with one knob different: where you start.
- **Move the mask, get a different edit.** Measured: mask the clothes → swapping clothes works
  (the knit texture comes out); mask the hair → works; **mask the background → does not work**
  (the unmasked person pulls it back). `report` now prints how much of the frame the mask covers
  and how much changed outside it.
- **Two parameter regimes.** The default 8 steps / CFG 1.0 is fine for face swaps; changing
  **material** (clothes, hair) needs **30 steps / CFG 5** (CFG 3 for `MOSS-Image-Edit-HQ/armV5`) —
  visibly different on the same image. `report` reminds you when steps < 20.
- **`ref` should be a face close-up.** A full-scene reference cancels anything you add — measured
  while adding a chair: scene reference drew one red stripe, face close-up produced the chair.
- The `init` and `face_pick` dropdowns now show Chinese labels in the Chinese UI.

**Action required**: none. The new inputs (`init` / `width` / `height`) sit at the end of the parameter list with defaults; behaviour matches 0.3.0. You only touch them to use the new capability.

## 0.3.0 — 2026-10-28

**第 7 个节点：换脸。**

- 🆕 「🎬 故事板 · 换脸」把参考图的脸换到**一格**（或一张单图）。参考图决定「是谁」，源图加提示词决定「画什么」。
- 🆕 接了 `spec` 就只换那一格：其余格、缝、外边距**一个像素都不动**（实测目标格之外 `max|Δ| = 0`）。
- 🔴 参考图分辨率预算默认 `1.05`（约 1024²）。宿主默认的 `0.147` 那个值下参考图几乎不起作用（实测身份余弦 `0.008`）。
- 🔴 `denoise` 在 8 步下 `0.875~0.99` **全部等于 `1.0`**：源图被丢弃、构图来自提示词。想保住原构图要 `≤0.875`。`report` 打印实际生效值。
- ⚠ 「已知边界」那条改了：以前写「也不做换脸」，现在写「不承诺身份一致性；给的是工具，不是保证」。

**升级动作**：老工作流不用动。要换脸就把两个 LoRA（8 步 DMD2 → Moss-Image-Edit）串在模型前面，照 `examples/moss-faceswap-ui.json` 接。

<!-- EN -->

**Seventh node: face swap.**

- 🆕 "🎬 Storyboard · Face Swap" puts the face from a reference image into **one panel** (or a single image). The reference decides **who**; the source image plus the prompt decide **what is drawn**.
- 🆕 With `spec` connected only that panel changes: every other panel, the gutters and the margins stay **pixel-identical** (measured `max|Δ| = 0` outside the target cell).
- 🔴 The reference-image budget defaults to `1.05` (about 1024 squared). At the host default of `0.147` the reference barely registers (measured identity cosine `0.008`).
- 🔴 `denoise` between `0.875` and `0.99` **all behaves as `1.0` at 8 steps**: the source image is discarded and composition comes from the prompt. Use `≤0.875` to keep the source composition. `report` prints the value that really took effect.
- ⚠ One "known limits" line changed: it used to say "no face swap"; it now says "no identity guarantee — a tool, not a promise".

**Action required**: existing workflows need no change. To swap faces, chain the two LoRAs (8-step DMD2, then Moss-Image-Edit) in front of the model and follow `examples/moss-faceswap-ui.json`.

---

## 0.2.3 — 2026-10-28

**逐格不再交出空 latent；稿子与宫格对不上时，采样前就报错。**

- 🔴 逐格 `latent` 改为「每格一个」的 batch（`{"samples": [N,C,H,W]}`），接 `VAEDECode` 一次解出 N 张；整张模式出图不变。
- 🔴 对账：`[PANEL n]` 段数、全局段的「N 个面板」都必须等于格数，否则采样前报错并给三条出路（改稿 / 改格数 / `init=plain`）。以前照跑，会把 4 个节拍在 8 格里画两遍。
- 🔴 布局编辑器多一个输出 `negative`：只在单张模式且开了「融合成一张」时非空，宫格模式恒为空串；老接线按位置接。
- 🆕 编辑器加「融合成一张」（单张模式）：相邻框自动并缝、小边距贴边，summary 追加「整张连续画面」。
- 🆕 布局节点可选 `char_boxes`（如 `1, 3`）：把指定格标成人物框。
- 🆕 编辑器：描述框随内容长高；画布与下半区可拖拽分栏（双击复位）；接了外接提示词时「最终提示词」显示合并结果。
- 🆕 存盘不再让参数错位：编辑器面板与语言条这类纯界面组件不再写进存档，保存后重开参数保持原位（以前每存一次整条右移一格、下拉变红）。
- 修：在编辑器内滚轮不再缩放画布。
- 接 `spec` 后本节点的格数/单格长边/缝宽/外边距不生效，report 首行写明几何来源。

**升级动作**：从 Manager 更新到 0.2.3，重启并刷新浏览器（F5）。已有工作流无需改动；只依赖「逐格 latent 为空」的自定义接线要改。

<!-- EN -->

**Per-panel mode no longer returns an empty latent; a prompt that disagrees with the grid fails before sampling.**

- 🔴 Per-panel `latent` is now one latent per panel (`{"samples": [N,C,H,W]}`), so `VAEDECode` yields N images at once. Whole-canvas output is unchanged.
- 🔴 Reconciliation: the `[PANEL n]` count and any "N panels" phrase must both match the panel count, or the node stops before sampling with three ways out (fix the script / change the count / `init=plain`). It used to run anyway, painting four beats twice across eight panels.
- 🔴 The layout editor gains a 7th output, `negative`: non-empty only in single-canvas mode with blending, always empty in grid mode; existing positional links are unaffected.
- 🆕 "Blend into one image" switch (single-canvas): adjacent boxes close their gaps, small margins snap to the edges, the summary gains a "one continuous image" clause.
- 🆕 Optional `char_boxes` on the layout node (e.g. `1, 3`) marks those panels as character boxes.
- 🆕 Editor: caption box grows with text; drag the divider to resize (double-click resets); with a wired prompt, Final prompt shows the merged text.
- 🆕 Saving no longer shifts values: UI-only widgets (editor panel, language bar) are not serialised, so values stay put after save and reopen.
- Fixed: the wheel inside the editor no longer zooms the canvas.
- With `spec` wired in, this node's panel count, edge, gutter and margin do not apply; the report's first line names the geometry source.

**Action required**: Update to 0.2.3 from the Manager, restart ComfyUI and hard-refresh (F5). Existing workflows need no changes; only wiring that relied on the empty per-panel latent must be updated.

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
