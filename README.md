# ComfyUI-MOSS-Storyboard

🌐 **English** · [中文](README_ZH.md)

![MOSS Storyboard for ComfyUI: visual layout editor, one generated 4-panel grid, and exact panel crops](assets/hero-storyboard.png)

**Layout nodes for MOSS-Image.** Arrange **3, 4, 6, 8 or 9 panels** on a visual canvas, write one
caption per panel, queue once, and get the full storyboard plus **pixel-exact panel crops**.
(`panels` also accepts other counts 1–12 and factors them automatically; 3/4/6/8/9 are the
shapes used in the examples and measured tests.)

- **Storyboard / comic page** — one canvas divided into equal panels; each panel has its own
  caption; every panel is cropped out.
- **Poster / cover / product shot** — subject and text blocks each own a region; text is rendered
  verbatim.

| Item | Value |
|---|---|
| Version | **0.2.2** |
| License | **MIT** (credits in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)) |
| Host | **ComfyUI ≥ 0.42.0** (native MOSS-Image support landed in PR #17095) |
| Dependencies | **None** beyond host-provided `torch` / `comfy*` |

---

## What this adds over the official MOSS-Image layout

The official ComfyUI template already supports **single-canvas bounding boxes**. This pack builds
the repeatable multi-panel workflow around that capability:

| | Official MOSS layout | This pack |
|---|---|---|
| Single image with bbox / text | ✅ native | ✅ visual editor + 8 presets |
| Multi-panel storyboard in one canvas | — | ✅ 3 / 4 / 6 / 8 / 9 |
| Per-panel captions → layout boxes | draw each box manually | ✅ `[PANEL n]` blocks |
| Exact panel crops | manual crop math | ✅ `spec`-driven, pixel-aligned |
| Reusable layout state | per-node fields | ✅ v3 state + undo/redo |
| Queue from the editor | — | ✅ one click |
| External planner JSON | — | ✅ elements-v1 + CLI |
| Extra pip dependencies | — | none |

**Not a fit if** you only make single-subject images with one or two boxes — the official template is
simpler. **A good fit if** you need repeatable multi-panel pages, per-panel captions, or exact crops
for downstream editing.

> **Start here:** [`examples/moss-layout-editor-storyboard.json`](examples/moss-layout-editor-storyboard.json)
> is the complete 4-panel storyboard graph. Install the three model files, open it, queue.

---

## Three things that prevent most misunderstandings

1. **By default one canvas is generated in one pass; the optional Per-panel mode samples every
   panel separately and assembles the board.** Default is fast; per-panel is more consistent across
   panels and gives each panel a higher sampling resolution, at extra time (4 panels measured
   27 s → 60 s; cost grows with the panel count). More panels means fewer pixels per panel in the
   default mode (8 panels are 512×288 each at the factory edge, 496×272 in the shipped example).
2. **Grid lines are redrawn by default (`reborder` on) from the same coordinates after decoding —
   position and width are exact.** Turn it off only if you want the model to draw the lines itself
   (they may wobble or break).
3. **There is no quantitative score for layout-box adherence.** MOSS's layout capability is
   officially marked *experimental*; strict per-panel compliance is not guaranteed. This pack
   guarantees geometry, wiring and crops; composition needs visual review. Text rendering has a
   small-sample measurement (see Known limits).

---

## Features

| Capability | Notes |
|---|---|
| **3 / 4 / 6 / 8 / 9 panels** | Panel count is an input (`panels`); layout and canvas size are derived, 16-aligned |
| **Per-panel captions** | `[PANEL 1]`…`[PANEL n]` blocks become native MOSS layout boxes |
| **Visual layout** | A canvas inside the node: drag boxes to decide which region each element occupies, then fill in its description. Text elements take the literal string to render |
| **8 layout presets** | grid / split vertical / split horizontal / title + subject + body / three columns / centered subject / stacked / free (blank canvas) |
| **Text posters** | Chinese, English, mixed, and two-line text have small-sample measurements (see Known limits) |
| **Deterministic grid lines** | Redrawn from coordinates after decoding (`reborder` on by default) |
| **Per-panel crops** | Row-major, pixel-aligned to the canvas; `spec` carries all geometry in one wire |
| **Official prompt-enhancer bridge** | Paste a draft from OpenMOSS's MOSS-Image Prompt Enhancer skill: get a validated MOSS prompt, and for layout drafts a state v3 for the Layout Editor |
| **Bilingual UI (zh / en)** | A 中 / EN button on each of our nodes; display-only, never touches saved values |

**Six nodes, one entry point.** The shipped example wires the full storyboard path. A minimal
graph needs only the Sampler, which carries every geometry parameter. The Layout Editor is the
other entry point: no `[PANEL n]` blocks — place boxes instead, for posters, covers and product shots.

| Node (English / 中文) | Type | Required? | Role |
|---|---|---|---|
| 🎬 Layout Editor / 布局编辑器 | `MossLayoutEditor` | Optional (posters / custom layouts) | Canvas + elements → layout prompt + sampling size. **No model loaded**, instant |
| 🎬 Storyboard Generate / 故事板 · 生成 | `MossStoryboardSampler` | **Yes** (storyboards) | `model + clip + vae + positive` → canvas + panels. Computes geometry from its own parameters when `spec` is absent |
| 🎬 Storyboard Layout / 故事板 · 宫格几何 | `MossStoryboardLayout` | Optional (recommended) | Geometry + layout JSON + grid template. **No model loaded**, instant |
| 🎬 Storyboard Crop / 故事板 · 裁格 | `MossStoryboardCrop` | Optional | Crops only; feed `spec` for pixel-exact panels |
| 🎬 Storyboard Paste / 故事板 · 贴回 | `MossStoryboardPaste` | Optional | Pastes a corrected panel back at the `spec` coordinates, returning the full corrected board |
| 🎬 Prompt Compiler / 提示词 · 草稿编译 | `MossPromptCompiler` | Optional (official skill bridge) | Prompt-enhancer draft → MOSS prompt + editor state. **No model loaded** |

> Node labels switch with the UI language.
> **When `spec` is connected it wins** — the Sampler's own geometry parameters are ignored.
> That is the most common trap.

---

## Requirements

| Dependency | Required | Where |
|---|---|---|
| ComfyUI **≥ 0.42.0** | ✅ | older versions lack the `moss_image` text-encoder type |
| `moss_image_sft_bf16.safetensors` | ✅ | `models/unet/` or `models/diffusion_models/` |
| `qwen3.5_9b_int8_convrot.safetensors` | ✅ | `models/clip/` |
| `flux2-vae.safetensors` | ✅ | `models/vae/` |
| pip packages | ❌ | none |

Weights are **not** bundled. For download links and the exact folder names, follow the official
ComfyUI MOSS-Image tutorial: <https://docs.comfy.org/tutorials/image/moss-image/moss-image>.

The optional 4-step distilled LoRA is **not** part of this pack: the shipped workflow includes a
`LoraLoaderModelOnly` node **bypassed by default**. The shipped graph uses the **MOSS-Image-SFT
model card settings — 50 steps / cfg 3 / euler / sgm_uniform**. Un-bypass the node
(`Ctrl+B`) once you have the file, and change `steps` to **4** and `cfg` to **1.0**.

> **Host version also changes VRAM.** The fix is commit `d07dff4` (2026-10-23). As of 2026-10-24
> it is **not in any tagged release** — so pip / desktop / portable builds, and `git clone`s that
> have not pulled since 2026-10-23, use the **Old** column. A recent `git pull` uses the **New**
> column. If unsure, budget for Old.
> Numbers, and how to tell which column you are in → [How much VRAM](#how-much-vram).

---

## Install

**ComfyUI Manager (recommended)**
Search for **MOSS Storyboard** → Install → restart ComfyUI.

**ComfyUI CLI** (if your install ships the `comfy` command)

```bash
comfy node install moss-storyboard
```

**git**

```bash
cd <your ComfyUI>/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-MOSS-Storyboard
```

Restart ComfyUI. Nodes appear under **`moss/storyboard`**, and the startup log shows a line like
this (`<version>` is whatever you installed):

```
[MOSS SB] v<version> 已加载｜节点 6 个（出口 v3）｜3/4/6/8/9 宫格
```

No such line = the pack did not load. Force the legacy V1 exit with `MOSSSB_NODE_API=v1`.

---

## Quick start

**Fastest path — open the shipped graph**

1. Install the pack and the three model files above.
2. Open [`examples/moss-layout-editor-storyboard.json`](examples/moss-layout-editor-storyboard.json).
3. Queue. Outputs: a **1024×576** grid and four **496×272** panels.

**Text-only path — one Sampler node**

1. `UNETLoader` → `moss_image_sft_bf16.safetensors`
2. `CLIPLoader` → `qwen3.5_9b_int8_convrot.safetensors` with **`type = moss_image`**
   ; `VAELoader` → `flux2-vae.safetensors`
3. Set `panels` on the Sampler — factory default 8, the shipped workflow uses 4.
   Canvas size follows automatically; never type width/height by hand.
4. Paste the script into **`positive`** on Storyboard Generate:

```
Rainy-night cafe, one canvas divided into a strict grid of 4 panels, thin white dividers;
35mm film, real actors, natural skin texture, shallow depth of field

[PANEL 1]: medium shot, two people across a table, she pushes the lemonade glass to center
[PANEL 2]: close-up, her lashes against rain-streaked glass
[PANEL 3]: over-the-shoulder, he looks out the window
[PANEL 4]: tabletop close-up, two hands resting on the rim
```

5. Run. Outputs: `grid` (the canvas) + `panels` (one image per panel).

A ready-made plain graph is in `examples/moss-storyboard-4panels-ui.json`.
Editor examples: `examples/moss-layout-editor-poster.json` (poster) and
`examples/moss-layout-editor-storyboard.json` (4-panel storyboard).

**The other path: poster / cover / product shot**

No `[PANEL n]` blocks — place boxes instead:

1. Add 🎬 Layout Editor and pick a preset (e.g. `title-body`).
2. Drag boxes on the canvas: one for the title, one for the subject, one for the subtitle.
3. Fill each box's **caption**; text boxes also get the **literal string**.
4. Wire `prompt` → `CLIPTextEncode.text`, `width`/`height` → `EmptyFlux2LatentImage`.
5. Follow with a plain `KSampler` — MOSS-Image-SFT model card settings are 50 steps / cfg 3 / euler;
   with the 4-step distilled LoRA, 4 steps / cfg 1.0.

> ⚠ In single mode do **not** chain the storyboard Sampler — it would re-parse the layout JSON as
> caption blocks. Use a plain `KSampler`.

### Prompt rules (three, no more)

1. Everything **before the first `[PANEL …]`** is the **global description** (character look,
   scene, style, camera). Writing `[STYLE]:` is optional — it is just a readable label; the parser
   goes by position.
2. Each `[PANEL n]` block describes **that panel**: shot size + subject + action.
3. **Panel numbers are an ordering key, not a cell slot.** Captions are sorted by number and paired
   with cells in that order: the first caption goes to the first cell, the second to the second,
   and so on. Gaps and out-of-order blocks are accepted; the numbers do not pick a cell
   (a lone `[PANEL 6]` still lands in cell 1).

| You write | Result |
|---|---|
| `[PANEL 1]: …` `[PANEL 2]: …` | 2 captions → the first 2 panels get them |
| `[画面 1]: …` `[分镜 1]: …` | Also valid (Chinese markers are recognised) |
| No `[PANEL]` at all | The whole text is a plain prompt; **no layout boxes** (pure txt2img; `report` says so) |
| 8 panels but only 4 captions | First 4 panels captioned, the rest not (not an error) |

### Panels → canvas (panel 16:9, long edge 496, gutter 8, margin 8)

| Panels | Shape | Canvas | MP | Panel size |
|---|---|---|---|---|
| 3 | **vertical strip** (3 stacked) | 512 × 848 | 0.43 | 496 × 272 |
| 4 | **quad** (2 × 2) | **1024 × 576** | 0.59 | 496 × 272 |
| 6 | **wide** (2 rows of 3) | 1520 × 576 | 0.88 | 496 × 272 |
| 8 | **wide** (2 rows of 4) | 2032 × 576 | 1.17 | 496 × 272 |
| 9 | **3 × 3** | 1520 × 848 | 1.29 | 496 × 272 |

8/9 panels land in MOSS's 1–2 MP training range; 3/4/6 are below it (usable, but outside the trained range). Portrait panels (`panel_aspect = 9:16`) rotate the whole
layout by 90°: 3 panels become a horizontal strip (848 × 512), 6 become 2 × 3 (576 × 1520),
8 become 2 × 4 (576 × 2032); 4 and 9 are unchanged.

- **Bigger panels**: raise the panel long edge (`panel_edge`, 128–2048, step 16); the canvas follows.
  `panel_aspect` accepts 10 ratios: `16:9` `3:2` `4:3` `1:1` `4:5` `3:4` `2:3` `9:16` `21:9` `2.39:1`.
- **Force a layout**: set columns (`columns`); leave `rows = 0` to fill in (`columns × rows == panels`).
  3 panels default to a vertical strip because three 16:9 panels in a row would make a 5.2:1 canvas.

### How much VRAM

VAE decode — not sampling — is the memory peak, and the same canvas can cost **4×** more or less
depending on your host version.

| Canvas | Panels | New host | Old host |
|---|---|---|---|
| 1024 × 576 | 4 | ~0.6 GB | ~2.4 GB |
| 2032 × 576 | 8 | ~1.2 GB | ~4.8 GB |
| 1520 × 848 | 9 | ~1.3 GB | ~5.2 GB |
| 2096 × 608 | 8 (factory 512 px edge) | ~1.3 GB | ~5.2 GB |

The first three rows use the shipped workflow's 496 px panel edge; the last row is the factory
512 px default.

**Which column are you in?** The fix is commit `d07dff4` (2026-10-23). Check whether your host
build contains it; if unsure, use the **Old** column.

| How you installed | Column |
|---|---|
| Desktop / portable / pip | Depends on the build; if unsure, **Old** |
| `git clone` + `git pull` after 2026-10-23 | **New** |
| `git clone` but not pulled recently | **Old** |

**When memory runs short**, the host tries the full-image decode first, then falls back to
**tiled decode** — and tile seams can cut straight through your panels. Look for
`retrying with tiled VAE decoding` in the log.

**Fixes, cheapest first**: ① `git pull` the host; ② lower `panel_edge` (half the edge → ¼ the
decode memory); ③ use fewer panels (8 → 4 halves the canvas area).

> ⚠ The saving only applies when the VAE runs in fp16 or bf16. `--fp32-vae`, or a host that
> falls back to fp32, silently disables it.

### Bilingual UI

Each of **our** nodes has a **中 / EN** button switching titles, labels, combo display and tooltips.
Default follows the host locale.

- **Only our nodes** are touched — never official or third-party nodes.
- **Display only, never the graph**: only `label` / `title` / `tooltip` / `placeholder` change;
  widget `name`s and values never do, so execution and saved widget values are unaffected.
- **Hard-refresh the browser (F5) after upgrading** — restarting the ComfyUI process is not enough.
- API submissions (`/prompt`) bypass the canvas: no button, no functional loss.

---

## Node reference

### 🎬 Layout Editor (`MossLayoutEditor`, no model)

**Start here for posters, covers, product shots, or any free-form layout.**

The node is a canvas: place boxes on the left, fill in descriptions on the right.
Where the box sits is where the element is drawn.

Outputs: `prompt` `width` `height` `spec` `template` `report`

| Input | Notes |
|---|---|
| `editor_state` | **Do not type here.** This is the canvas save file and updates itself. Hand-editing works but must match schema v3; a bad field raises with the field name |

Two modes, switched by preset:

| Mode | Presets | Output | Wire to |
|---|---|---|---|
| **Single** | split vertical / split horizontal / title + subject + body / three columns / centered subject / stacked / free | One multi-element layout JSON, **one sampling pass** | `CLIPTextEncode.text`, then a plain `KSampler` |
| **Grid** | grid | `[STYLE]` + `[PANEL n]` | the Sampler's `positive` + `spec`; default `mode=whole grid`, switch to `per panel` for cross-panel consistency |

> In single mode the `prompt` output is **already layout JSON** — feed it straight to
> `CLIPTextEncode`. Do **not** chain the storyboard Sampler there; it would parse the JSON as
> caption blocks again.

**Canvas controls**

| Action | Result |
|---|---|
| Drag on empty space | Draw a new box (a new element) |
| Drag a box / its corners | Move / resize |
| Click a box | Edit type (object / character / text), description, coordinates on the right. In grid mode only **shot size + caption** are shown — type, text and palette are not compiled into a grid prompt |
| Drag near another box's edge, the canvas centre line, or the rule-of-thirds lines | Snaps |
| `Delete` / arrow keys / `Ctrl+D` | Delete / nudge / duplicate |
| `Ctrl+Z` / `Ctrl+Shift+Z` (or `Ctrl+Y`) | Undo / redo (same as the `↶` `↷` toolbar buttons) |
| Import / Export | Import elements-v1 (0–1000 bbox) or state v3 JSON; export elements-v1 (grid cannot be exported) |
| Raise / Lower + 6-way align | Change stacking (array order = z) or align to canvas edges/center; size is kept |
| Queue | Queue the current workflow; changes no values |

> An external planner's element JSON imports directly as editable items; the CLI validator/converter is in
> [`docs/12`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/12-interop.md) and does not need ComfyUI.

**The bottom of the panel shows the final prompt** — the exact text handed to the model for the
current state: `[STYLE]` + `[PANEL n]` per panel in grid mode, native layout JSON in single mode.
Read-only, copyable, and it updates as you edit. It is compiled with the same rules as the backend
(a differential test compares them byte by byte), so it is not a paraphrase.

**Preset placeholders get flagged.** If you queue without editing preset copy such as
`Title` or `Panel 3`, the text fields get rendered letter by letter and the panels come out alike.
The status bar counts them, the Queue button asks once, and the node's `report` says the same
thing for API-only users.

**Text elements have two separate fields — do not mix them**

| Field | What goes in | Example |
|---|---|---|
| **caption** | what the text **looks like** | bold sans-serif title, pale gold, slight outline |
| **text** | the **literal characters to render** | LEGEND |

Swap them and the model will try to draw the words "bold pale gold". Only `text` is rendered verbatim.

> ⚠ **Keep text boxes away from the canvas edge.** Measured: with the box top at 5% of the canvas
> height, the glyphs overflow upward and get clipped. Presets use 10%, but that is only a more
> conservative start than 5% — **10% is not verified as safe**.

---

### 🎬 Prompt Compiler (`MossPromptCompiler`, no model)

Bridges OpenMOSS's [MOSS-Image Prompt Enhancer skill](https://github.com/OpenMOSS/MOSS-Image/tree/main/skills/moss-image-prompt-enhancer)
to this pack. Paste the skill's **draft** (not its compiled output) and get:

- **`prompt`** — a ready-to-paste MOSS prompt (plain caption, or one-line native layout JSON).
  Wire it into `CLIPTextEncode.text` or the Sampler's `positive`.
- **`state`** — for layout drafts, a state v3 JSON you can paste into the Layout Editor import box.
  The canvas must be 64–4096 px; larger or smaller drafts still compile to `prompt`, and `report`
  explains why `state` was skipped.
- **`report`** — type, canvas, item counts, and any field that could not be represented in state.

Draft requirements (the official schema): `width` / `height` positive multiples of 16;
`bbox_coord_space="abs"`; `bbox_format="xyxy"`; `summary.caption` and a non-empty `items` array.
Each item is `object` / `character` / `text`; text items must carry the literal `text`.
Unknown fields, duplicate keys, NaN/Infinity and out-of-bounds boxes are rejected.
The pack adds a **256-item** cap and a 1 MB draft cap.

The skill itself is external Apache-2.0 content and is **not bundled**; the compiler is an
independent implementation of the documented schema. Details, limits and the offline confidence
check: [prompt-enhancer doc](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/13-prompt-enhancer.md).

---

### 🎬 Storyboard Layout (`MossStoryboardLayout`, no model)

Outputs `width` `height` `columns` `rows` `spec` `layout_json` `template` `report` `elements`.

- **`spec`** — one string carrying all geometry. Feed it downstream and nothing can drift.
- **`layout_json`** — same format as the host's native `MOSS-Image Layout Prompt`
  (`summary` + `items`; bboxes are absolute pixels measured from the canvas centre).
- **`elements`** — the canvas **element** format (`{type, desc, bbox}`, bbox
  **normalized to 0–1000**, `[y0, x0, y1, x1]`). Feed it to the `bboxes` string input of the
  host's `Create Bounding Boxes` to see the cells and edit each cell's description.
  Verified to match the real cells pixel for pixel.

  Wire **all three** lines. Missing one silently corrupts every box:

  ```
  width    → Create Bounding Boxes · width
  height   → Create Bounding Boxes · height
  elements → Create Bounding Boxes · bboxes
  ```

  Why: the bbox is normalized 0–1000, and the canvas node needs `width`/`height` to
  convert it to pixels. The canvas defaults to 1024×1024; an 8-panel strip is
  2032×576, so leaving them unwired misplaces every box without any error.

  ⚠ **The two formats are not interchangeable.** `elements` is canvas elements (normalized
  0–1000); `layout_json` is the full layout JSON (`summary` + `items`, absolute pixels).

  ⚠ **Dragging boxes on the canvas does not change the output image.** In `逐格`
  (per-panel) mode each cell is sampled on its own, so boxes only decide the assembly
  layout. Only `整张` (whole-canvas) mode lets boxes affect composition. See the
  [cross-panel consistency doc](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/07-consistency.md).
- **`report`** — human-readable summary (panels, canvas, MP, caption alignment, warnings).
- Its `prompt` box **is intentionally empty**: this node only computes geometry.

### 🎬 Storyboard Generate (`MossStoryboardSampler`)

Outputs `grid` `panels` `latent` `spec` `prompt_used` `report` `prompts_json`.

- **`prompts_json`** — the exact text handed to the model, as a JSON array: one entry in
  `整张` mode, one entry per panel in `逐格` mode (global paragraph included, multi-panel
  wording stripped). Wire this to a text file if you want to audit prompts later.

Factory defaults below. The shipped workflow uses **4 panels / panel edge 496** and ships at the
**MOSS-Image-SFT model card settings — 50 steps / cfg 3 / euler / sgm_uniform**
(with the optional 4-step LoRA: 4 steps / cfg 1.0).

| Parameter | Factory default | Notes |
|---|---|---|
| Positive prompt `positive` | empty | See prompt rules above |
| Generation mode `mode` | Whole canvas | `Whole canvas` (整张) = one sampling pass for the whole board; `Per-panel` (逐格) = sample every panel on its own and assemble. See below |
| Panel count `panels` | 8 | used when `spec` is absent |
| Panel aspect `panel_aspect` | `16:9` | used when `spec` is absent (10 ratios) |
| Panel long edge `panel_edge` | 512 | used when `spec` is absent |
| Canvas aspect `canvas_aspect` | `auto` | used when `spec` is absent |
| Gutter `gutter` / margin `margin` | 8 / 8 | used when `spec` is absent |
| Steps `steps` | 50 | MOSS-Image-SFT model card value (the Base card gives the same 50). Set **4–8 only** with the 4-step distilled LoRA. Mixing bands does not raise an error, but results degrade (sometimes fully black) — see Known limits and the FAQ |
| CFG `cfg` | 3.0 | MOSS-Image-SFT model card value (official Base range: 3–6). Only `>1` makes the negative prompt matter |
| Sampler / scheduler | `euler` / `sgm_uniform` | `euler` is the model card sampler; `sgm_uniform` comes from the official OpenMOSS ComfyUI plugin workflow (the model card does not specify a scheduler) |
| Init mode `init` | `auto` | see below |
| Denoise `denoise` | 1.0 | Keep 1.0. The sampler has **no image input**: below 1 references no image, it only makes the host truncate sigma by `int(steps/denoise)` (equivalent to fewer steps); `report` prints nominal vs. effective |
| Redraw grid lines `reborder` | on | redraw grid lines after decode |
| Sample edge `sample_edge` | 1024 | per-panel mode only, see below |
| Negative prompt `negative` | empty | in the **Advanced** fold, optional. At `cfg=1.0` the host skips the negative branch entirely; the default `cfg=3.0` does evaluate a non-empty value |

**`mode` (how the panels are produced)**

| Option | What it does | When to use it |
|---|---|---|
| `Whole canvas` (整张) | **Default.** One sampling pass for the whole board: fast | Exploring ideas, or many panels (8/9) |
| `Per-panel` (逐格) | Every panel is **sampled on its own**, then assembled by coordinates: N times slower | When **the same character must stay consistent** across panels |

> **Why per-panel is more consistent**: in one pass the model spreads the global description
> over N regions and imagines each one separately, so the same character drifts (hat colour,
> face shape). Per panel, every panel reads the global description in full, and the character
> stays visibly the same.
>
> **Measured here** (4 panels / 16:9 / 8 steps / cfg 1.0 / same prompt): the variance of how
> much of the red beret appears in each of the four panels drops from `0.0036` (whole grid)
> to `0.00004` (per panel) — roughly 90× more uniform; the beret itself also comes out more solid.
>
> **Cost**: extra time, measured at 4 panels 27 s → 60 s; it grows with the panel count.
> Peak VRAM is set by the per-panel `sample_edge` and the board size.

**`sample_edge` (sampling long edge)**

The big advantage of per-panel mode: a panel's **sampled** resolution is not bound by the
board size. `1024` samples each panel at a 1024-pixel long edge and downscales it into its final cell.

It is an **absolute pixel count**, not a multiplier — matching the host's
`Empty Flux 2 Latent`, and keeping the quality knob independent of the layout knob.

> Measured here (4 panels / 16:9 / 8 steps): `496` looks soft → `992` is the sweet spot
> (+45% sharpness) → `1984` actually goes **sloppy** (composition loosens).
> So bigger is not better; `1024` is a safe default.

Cost: VRAM and time scale with the pixel count. Ignored in whole-grid mode.

> ⚠ **Per-panel resolution is the real quality bottleneck.** MOSS trains at 1–2MP, while a
> 1:1 nine-panel board gives each panel only **0.26MP** — the canvas looks fine, every panel
> is visibly soft. The node's `report` prints both numbers. With many panels and a near-square
> ratio, prefer `逐格`.

`init` values: `auto` / `layout` = panel captions become layout boxes (default);
`plain` = raw prompt, no layout boxes.

> **No img2img in this pack.** The latent always starts empty; keep `denoise` at 1.0.
> Template-latent initialisation was measured and does not work: 35 steps / denoise 0.85 → fully
> black; 12 steps / 0.6 and 4 steps / 0.5 → template ghosting. Whenever sigma is actually
> truncated, the model leaves the latent untouched. The template image is only an `IMAGE` output
> of `MossStoryboardLayout` for eyeballing the grid geometry — it never enters the sampler.
> To rework an **existing** image, see "Fixing one panel" below — that path does work, and it
> does not require re-running the whole board.

### 🎬 Storyboard Crop (`MossStoryboardCrop`)

Outputs `panels` (per-panel images), `preview` (crop lines drawn on the full canvas), `report`
and `index` (the panel number).
With `spec` → pixel-identical to generation. Without `spec` → requires `columns × rows` to divide
the image evenly and **raises** otherwise.
`index` left at -1 (the default) outputs every panel, exactly as before. A 0-based panel number
outputs just that one and emits it on the `index` output — wire that into Paste and you never
type a panel number by hand.

### 🎬 Storyboard Paste (`MossStoryboardPaste`)

Inputs `grid` (the full board), `panel` (the corrected panel) and `index` (which panel to write).
Outputs `grid` (the corrected board), `marked` (debug: frames the pasted region in red) and `report`.

**Purpose: fix one panel without re-running the whole board.** Sampling a board takes tens of
seconds to minutes, and re-running moves the seed of every panel at once. Instead: cut the panel
out, correct it however you like, then paste it back at the exact `spec` coordinates.

- `index` is 0-based, row-major (left to right, top to bottom). Wiring Crop's `index` output
  overrides the widget, so it can never disagree with the panel you actually cut.
- Only the target panel's content area is touched: gutters, margins and every other panel stay
  pixel-identical, and the grid lines are preserved.
- `shave` must match what you used on Crop. Cutting inset s pixels, so pasting writes into the
  inset rectangle; a mismatch shifts content by s pixels **without erroring** — check `report`.
- If the input size differs from the cell it is resampled, and `report` says so. For a
  pixel-exact round trip use Crop with `resize_edge=0`, `target_aspect=keep`, and the same `shave`.
- To fix several panels, chain Paste nodes: the first `grid` output feeds the next `grid` input.

> Ready-made graph: `examples/moss-storyboard-4panels-edit.json` (needs the `Moss-Image-Edit` LoRA /
edit node; the node contains instructions).

Recommended wiring (no manual panel numbers anywhere):

```
Crop (index set) ──panel──→ your edit chain ──→ Paste
Crop ──index──────────────────────────────────→ Paste
Sampler ──grid────────────────────────────────→ Paste
Sampler ──spec────────────────────────────────→ Paste / Crop
```

#### How to build "your edit chain" (optional — this pack needs no LoRA at all)

This pack only does geometry: cut a panel out, paste it back. **What you edit with is up to you** —
any node pack you already like, external retouching, or even hand-editing the file and reloading it.

If you want MOSS's own instruction editing, the host's official nodes are enough — no third-party
node pack required:

```
Crop (index) ──→ Text Encode MOSS-Image Edit ──→ KSampler ──→ VAEDecode ──→ Paste
                     ↑ needs the LoRA below
```

Steps:

1. Download the community edit LoRA **Moss-Image-Edit** (~285 MB, bf16, rank 64) — either source,
   same files:
   - Hugging Face: <https://huggingface.co/chinoll/Moss-Image-Edit>
   - ModelScope: <https://www.modelscope.cn/models/ChenkinLab/Moss-Image-Edit>
2. Put `adapter_model.safetensors` and `adapter_config.json` into
   **`models/loras/Moss-Image-Edit/`** (create the subdirectory, both files together).
3. Use the host's **`Text Encode MOSS-Image Edit`** node:
   - `clip` → your CLIPLoader (`type=moss_image`)
   - `images` → the panel you cut out
   - `edit_format` → `Moss-Image-Edit`
   - **`vae` may be left unwired**: then the reference only reaches the text encoder, which costs
     far less VRAM and does not force the reference's spatial structure onto the result. Wiring it
     adds reference latents — stronger composition preservation, more memory.
4. Follow it with a plain `KSampler` → `VAEDecode` → this pack's Paste. This path is driven by a
   community LoRA, so the official base defaults do not apply: **30 steps / cfg 4** was measured here;
   with the 4-step distilled LoRA stacked, **8 steps / cfg 1.0** was more stable (see below).

**What it can and cannot do** (measured locally): colour, outfit, lighting, season and style
instructions are followed well. Composition preservation comes from the reference image itself and
weakens when `vae` is unwired. **It is not good at "re-shoot from another angle"** — that still
belongs to the layout-prompt route.

⚠ **It is not a cross-panel consistency solution.** Pushing one panel toward the others makes the
shot sizes converge — every panel drifts toward the same image, while a storyboard needs
"same character, different shot sizes". Cross-panel consistency comes only from
**per-panel sampling + global-section anchoring** (see the
[cross-panel consistency doc](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/07-consistency.md)).
This LoRA is a **single-panel touch-up** tool: fix one panel's colour, outfit or light, then paste back.

> **Licensing and scope**: that LoRA is third-party community weights, **not distributed with this
> pack** and not maintained by it. Its model card marks the code portion as GPLv3; the weight
> licence is whatever the card states. This pack only links to it — it does not copy, modify or
> redistribute any weight file. This pack itself (MIT) does not depend on it: storyboard generation
> and Paste work fully without downloading anything.

---

## Known limits (read before filing a bug)

**Verified** — every row below states its exact LoRA and step configuration.

| Item | Config | Result | Sample |
|---|---|---|---|
| 3/4/6/8/9 panels · landscape/portrait · no gutter · thick gutter · small canvas · `plain` | base + 4-step LoRA / **4 steps** | sizes pixel-exact vs. theory | 15 runs |
| Grid lines | same | pure white after redraw | per case |
| Random seed | same | different seed → different md5 | 2 controls |
| **Element boxes constrain composition** | base + 4-step LoRA / **4 steps**; base + 8-step LoRA / **8 steps** | boxes only changed: the bicycle went from cropped out to fully visible with its front wheel in the lower-left. Reproduced on 3 seeds | 36 images |
| **Four Chinese characters** (1024×576) | base + 8-step LoRA / **8 steps** | all correct | 1 image |
| **Four Chinese characters** (2048×1152) | base + 4-step LoRA / **4 steps** | all correct, softer strokes | 1 image |
| **Four Chinese characters** (2048×1152) | base + 8-step LoRA / **8 steps** | all correct, sharp strokes | 2 images |
| **Mixed zh/en · two lines · explicit colour** | base + 8-step LoRA / **8 steps** | all correct; line break honoured; colour applied | 3 images |

> **Base model only, no LoRA**: one control run at 2048×1152 / 30 steps / cfg 3.5 (before the
> defaults were aligned with the official template) produced a normal image, but its text was
> **not checked character by character**, so it is not a result. Verify it yourself.

**Text: what works, what does not**

| Item | Result | Config |
|---|---|---|
| Resolution | 1024×576 is enough; 2048 only sharpens strokes | 8-step LoRA / 8 steps |
| Steps | 4 steps: correct but soft. 8 steps: visibly sharper. 16 steps: no further gain | matching LoRA |
| Size | Still correct with the box at 1/20 of canvas height (≈43 px) | 1536×864 and 2048×1152 |
| Line breaks | `\n` inside `text` is honoured; two lines form separately | 2048×1152 / 8 steps |
| Colour | `palette: ["#d92b2b"]` takes effect | 2048×1152 / 8 steps |
| **Box height does not control size** | A 43 px box produced ≈130 px glyphs — **text overflows its box**. Control size with box **width** and caption length | — |
| **Edge-adjacent boxes get clipped** | Box top at 5% → glyphs overflowed and the canvas cut them, both seeds | 2048×1152 / 8 steps |

> Sample size is small (1–2 images per item). "Four characters correct" repeated across several
> configs, but it is **not a guarantee**. Clipping was only reproduced in the red-colour group — **other colours
> were not tested**. Presets keep text boxes at 10%, but **10% is not verified as safe** either; it is
> just a more conservative starting point than 5%.

**Not possible in this version**

- 🔴 **No img2img.** This pack always starts from an empty canvas. To edit an existing image with a
  reference, use the host's `Text Encode MOSS-Image Edit` (see "Fixing one panel") — a separate path.
- ⚠ **Steps and cfg follow the official model card for the checkpoint you load.**
  SFT (the shipped checkpoint) and Base both use **50 steps / cfg 3 / euler** on the model card
  (SFT: [ModelScope card](https://www.modelscope.cn/models/openmoss/MOSS-Image-SFT); Base: official
  CFG range 3–6). The card also specifies shift 5 (built into the native ComfyUI model) and
  negative-branch skipping at layer 22 (wire the host's `SkipLayerGuidanceDiTSimple`, `single_layers`
  `22`; the shipped examples do not include it). The native ComfyUI template uses different values
  (25 steps / cfg 2.5 / er_sde, Comfy-Org's choice) — it is not the model-card recipe.
  With a 4-step distilled LoRA: **4–8 steps / cfg 1.0** — the LoRA was distilled at cfg 1,
  so raising it breaks the distillation. Mixing a distilled LoRA with the base recipe
  (e.g. LoRA loaded but 50 steps / cfg 3) will not error, it just wastes the run.
- ⚠ **Keep `denoise` at 1.0.** There is no image to reference; lowering it only truncates sigma
  (fewer steps). `report` prints nominal vs. effective — trust the effective value.
- **Per-panel composition is only approximate.** MOSS's layout capability is *experimental*; this
  pack does no quality scoring.
- **At 8/9 panels each panel is small** — about 496×272 with the shipped 496 px edge
  (512×288 at the factory default). Small text and fine texture degrade first — a
  resolution budget, not a node bug.

**Hard constraints**

- Canvas and panel dimensions must be multiples of **16** (enforced; hand-edited `spec` is rejected).
- >2 MP warns, >4 MP warns loudly: MOSS trains at 1–2 MP, official extrapolation ≈4 MP.
- **VAE decode precision is decided by ComfyUI, not by the weight file.** The host casts the VAE
  to `fp16` at load time (falling back to `bf16`/`fp32` only if your GPU cannot do fp16), so
  `flux2-vae.safetensors` (F32) and `flux2-vae_BF16.safetensors` (BF16) run at the **same
  precision with near-identical speed** — the file's dtype is normalised away on load, leaving
  only disk usage and load-time RAM as the difference. For true fp32 decoding use the host flag
  `--fp32-vae`; this pack deliberately does not implement a second precision path, which would
  diverge from the host's own `VAEDecode`. A general escape hatch also exists: the Sampler node
  has a `latent` output, so you can wire your own `VAEDecode` and use whatever VAE and precision
  you like.

---

## FAQ

**OOM during VAE decode, or faint horizontal seams in the output.** Same cause: not enough
memory → the host falls back to tiled decode → seams at the tile borders. Check the log for
`retrying with tiled VAE decoding`, then follow [How much VRAM](#how-much-vram). The cheapest
fix is `git pull` on the host.

**All black.** Check `report` for nominal vs. effective denoise, and the step count. The most
common cause is **mismatched bands**: a 4-step distilled LoRA loaded but 50 steps / cfg 3 used,
or no LoRA but 4 steps / cfg 1.0. Match the recipes listed under "Not possible".

**Right panel count, wrong content / everything crammed into one panel.** Layout boxes are
advisory. Shorten each caption to one shot, and check that `report` parsed as many captions as
there are panels.

**The canvas shape differs from the preview.** Check whether `spec` is connected: when it is, the
Sampler's own geometry parameters are ignored.

**Can't find the nodes.** Look under `moss/storyboard`. Searching `故事板`, `storyboard` or `宫格`
also works (search aliases are registered).

**No 中 / EN button.** Refresh the browser (F5). The button only exists on our nodes.
Console diagnostics: look for the `[MOSS SB] i18n: 就绪` line; if it is missing, confirm the
`web/` files (especially `moss_sb_i18n_ui.js`) are served under `/extensions/ComfyUI-MOSS-Storyboard/`.

**Can I use my own negative prompt?** Yes. The default `cfg` 3.0 is already above 1 — expand
**Advanced** and fill it in. Only at `cfg=1.0` does the host skip the negative branch.

**Can I generate a plain (non-grid) image?** Use the Layout Editor's single mode (recommended), or set
`panels = 1` on the Sampler. `init = plain` only changes how the prompt is sent; it does not remove
the grid geometry.

---

## License

MIT for this pack. See [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) — no third-party code was
copied. Model weights are not distributed. The host ComfyUI is GPL-3.0 and is only `import`ed at
runtime.

## Documentation

Selected deep dives live in the GitHub repo's
[`docs/`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/tree/main/docs) (**written in Chinese**):
[`06`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/06-vae-memory.md) VAE memory budget ·
[`07`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/07-consistency.md) cross-panel consistency ·
[`10`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/10-contract.md) state v3 frozen contract ·
[`12`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/12-interop.md) elements-v1 interchange ·
[`13`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/13-prompt-enhancer.md) official prompt-enhancer bridge ·
[`14`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/14-top-level-design.md) top-level design: what each of the six nodes owns.
Contributor guide: [`CONTRIBUTING.md`](CONTRIBUTING.md) · release history:
[`CHANGES.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/CHANGES.md).

`docs/`, `CHANGES.md` and `RELEASE-NOTES.md` are not shipped in the install package
(see `.comfyignore`); the links above point to the GitHub repo.
