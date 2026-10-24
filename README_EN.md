# ComfyUI-MOSS-Storyboard

🌐 [中文](README.md) · **English (condensed)**

> Condensed English version of [README.md](README.md) (Chinese is the source of truth).
> No section-level hash sync gate yet — see `AGENTS.md` §六.

**Layout** nodes for **MOSS-Image**: place elements on a canvas (people / objects / text) and output one image (grid or single).

Two uses, one mechanism:

- **Storyboard grid** — one canvas divided into **3, 4, 6, 8 or 9 panels**, each panel with its own
  caption, every panel cropped out.
- **Poster / cover / product shot** — subject and text blocks each own a region; text is given
  verbatim.

| Item | Value |
|---|---|
| Version | **0.2.2** |
| License | **MIT** (credits in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)) |
| Host | **ComfyUI ≥ 0.42.0** (native MOSS-Image support landed in PR #17095) |
| Dependencies | **None** beyond host-provided `torch` / `comfy*` |

---

## Three things that prevent most misunderstandings

1. **By default one canvas is generated in one pass; the optional Per-panel mode samples every
   panel separately and assembles the board.** Default is fast; per-panel is more consistent across
   panels and gives each panel a higher sampling resolution, at about N× the time. More panels means
   fewer pixels per panel in the default mode (≈496×272 each at 8 panels).
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
| **8 layout presets** | grid / split-v / split-h / title-body / three-col / center / stack / blank |
| **Text posters** | Chinese, English, mixed, and two-line text have small-sample measurements (see Known limits) |
| **Deterministic grid lines** | Redrawn from coordinates after decoding (`reborder` on by default) |
| **Per-panel crops** | Row-major, pixel-aligned to the canvas; `spec` carries all geometry in one wire |
| **Bilingual UI (zh / en)** | A 中 / EN button on each of our nodes; display-only, never touches saved values |

**Five nodes — a minimal storyboard graph needs only the Sampler**, which carries the full set of
geometry parameters. The Layout Editor is the other entry point: no `[PANEL n]` blocks, you place
boxes instead — for posters, covers and product shots.

| Node (Chinese UI) | Type | Required? | Role |
|---|---|---|---|
| 🎨 布局编辑器 | `MossLayoutEditor` | Optional (posters / custom layouts) | Canvas + elements → layout prompt + sampling size. **No model loaded**, instant |
| 🎬 故事板 · 生成 | `MossStoryboardSampler` | **Yes** (storyboards) | `model + clip + vae + positive` → canvas + panels. Computes geometry from its own parameters when `spec` is absent |
| 🎬 故事板 · 宫格几何 | `MossStoryboardLayout` | Optional (recommended) | Geometry + layout JSON + grid template. **No model loaded**, instant |
| 🎬 故事板 · 裁格 | `MossStoryboardCrop` | Optional | Crops only; feed `spec` for pixel-exact panels |
| 🎬 故事板 · 贴回 | `MossStoryboardPaste` | Optional | Pastes a corrected panel back at the `spec` coordinates, returning the full corrected board |

> In an English UI the nodes read `MOSS Layout Editor` / `MOSS Storyboard Layout` / `Sampler` /
> `Crop` / `Paste`.
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

Weights are **not** bundled. The optional 4-step distilled LoRA is **not** part of this pack:
the shipped workflow includes a `LoraLoaderModelOnly` node **bypassed by default** —
un-bypass it (`Ctrl+B`) once you have the file.

> **Host version also changes VRAM.** The fix is commit `d07dff4` (2026-10-23). Hosts that contain
> it use the **New** column; older hosts (including **v0.42.1 and earlier**) use the **Old** column.
> `git pull` once if you installed via git; if unsure, budget for the Old column.
> Numbers, and how to tell which column you are in → [How much VRAM](#how-much-vram).

---

## Install

```bash
cd <your ComfyUI>/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-MOSS-Storyboard
```

Restart ComfyUI. Nodes appear under **`moss/storyboard`**, and the startup log shows:

```
[MOSS SB] v0.2.2 已加载｜节点 5 个（出口 v3）｜3/4/6/8/9 宫格
```

No such line = the pack did not load. Force the legacy V1 exit with `MOSSSB_NODE_API=v1`.

---

## Quick start

1. `UNETLoader` → `moss_image_sft_bf16.safetensors`
2. `CLIPLoader` → `qwen3.5_9b_int8_convrot.safetensors` with **`type = moss_image`**
   ; `VAELoader` → `flux2-vae.safetensors`
3. Set 格数 (`panels`) on the Sampler — factory default 8, the shipped workflow uses 4.
   Canvas size follows automatically; never type width/height by hand.
4. Paste the script into **正面提示词 (`positive`)** on 故事板 · 生成:

```
Rainy-night cafe, one canvas divided into a strict grid of 4 panels, thin white dividers;
35mm film, real actors, natural skin texture, shallow depth of field

[PANEL 1]: medium shot, two people across a table, she pushes the lemonade glass to center
[PANEL 2]: close-up, her lashes against rain-streaked glass
[PANEL 3]: over-the-shoulder, he looks out the window
[PANEL 4]: tabletop close-up, two hands resting on the rim
```

5. Run. Outputs: `grid` (the canvas) + `panels` (one image per panel).

A ready-made graph is in `examples/moss-storyboard-4panels-ui.json`.
Editor examples: `examples/moss-layout-editor-poster.json` (poster) and
`examples/moss-layout-editor-storyboard.json` (4-panel storyboard).

**The other path: poster / cover / product shot**

No `[PANEL n]` blocks — place boxes instead:

1. Add 🎨 Layout Editor and pick a preset (e.g. `title-body`).
2. Drag boxes on the canvas: one for the title, one for the subject, one for the subtitle.
3. Fill each box's **caption**; text boxes also get the **literal string**.
4. Wire `prompt` → `CLIPTextEncode.text`, `width`/`height` → `EmptyFlux2LatentImage`.
5. Follow with a plain `KSampler` (4 steps + 4-step distilled LoRA, or base model 30–50 steps / cfg 3–5).

> ⚠ In single mode do **not** chain the storyboard Sampler — it would re-parse the layout JSON as
> caption blocks. Use a plain `KSampler`.

### Prompt rules (three, no more)

1. Everything **before the first `[PANEL …]`** is the **global description** (character look,
   scene, style, camera). Writing `[STYLE]:` is optional — it is just a readable label; the parser
   goes by position.
2. Each `[PANEL n]` block describes **that panel**: shot size + subject + action.
3. **Numbers decide the order: captions are sorted by panel number and paired with cells in that
   order.** Gaps are allowed, but do not write out of order; keep 1..N.

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

- **Bigger panels**: raise 单格长边 (`panel_edge`, 128–2048, step 16); the canvas follows.
  `panel_aspect` accepts 10 ratios: `16:9` `3:2` `4:3` `1:1` `4:5` `3:4` `2:3` `9:16` `21:9` `2.39:1`.
- **Force a layout**: set 列数 (`columns`); leave `rows = 0` to fill in (`columns × rows == panels`).
  3 panels default to a vertical strip because three 16:9 panels in a row would make a 5.2:1 canvas.

### How much VRAM

VAE decode — not sampling — is the memory peak, and the same canvas can cost **4×** more or less
depending on your host version.

| Canvas | Panels | New host | Old host |
|---|---|---|---|
| 1024 × 576 | 4 | ~0.6 GB | ~2.4 GB |
| 2032 × 576 | 8 | ~1.2 GB | ~4.8 GB |
| 1520 × 848 | 9 | ~1.3 GB | ~5.2 GB |
| 2096 × 608 | 8 (512 band) | ~1.3 GB | ~5.2 GB |

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
  `name` and combo values never do, so the saved workflow JSON is byte-identical.
- **Hard-refresh the browser (F5) after upgrading** — restarting the ComfyUI process is not enough.
- API submissions (`/prompt`) bypass the canvas: no button, no functional loss.

---

## Node reference

### 🎨 Layout Editor (`MossLayoutEditor`, no model)

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
| **Single** | split-v / split-h / title-body / three-col / center / stack / blank | One multi-element layout JSON, **one sampling pass** | `CLIPTextEncode.text`, then a plain `KSampler` |
| **Grid** | grid | `[STYLE]` + `[PANEL n]` | the Sampler's `positive` + `spec`; default `mode=whole grid`, switch to `per panel` for cross-panel consistency |

> In single mode the `prompt` output is **already layout JSON** — feed it straight to
> `CLIPTextEncode`. Do **not** chain the storyboard Sampler there; it would parse the JSON as
> caption blocks again.

**Canvas controls**

| Action | Result |
|---|---|
| Drag on empty space | Draw a new box (a new element) |
| Drag a box / its corners | Move / resize |
| Click a box | Edit type (object / character / text), description, coordinates on the right |
| Drag near another box's edge, the canvas centre line, or the rule-of-thirds lines | Snaps |
| `Delete` / arrow keys / `Ctrl+D` | Delete / nudge / duplicate |
| `Ctrl+Z` / `Ctrl+Shift+Z` (or `Ctrl+Y`) | Undo / redo (same as the `↶` `↷` toolbar buttons) |

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

### 🎬 故事板 · 宫格几何 (`MossStoryboardLayout`, no model)

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
  layout. Only `整张` (whole-canvas) mode lets boxes affect composition. See `docs/07`.
- **`report`** — human-readable summary (panels, canvas, MP, caption alignment, warnings).
- Its `prompt` box **is intentionally empty**: this node only computes geometry.

### 🎬 故事板 · 生成 (`MossStoryboardSampler`)

Outputs `grid` `panels` `latent` `spec` `prompt_used` `report`.

Factory defaults below. The shipped workflow is already tuned to **4 panels / 4 steps / panel
edge 496**, so you can run it as-is.

| Parameter | Factory default | Notes |
|---|---|---|
| 正面提示词 `positive` | empty | See prompt rules above |
| 生成方式 `mode` | 整张 | `整张` = one sampling pass for the whole board; `逐格` = sample every panel on its own and assemble. See below |
| 格数 `panels` | 8 | used when `spec` is absent |
| 单格比例 `panel_aspect` | `16:9` | used when `spec` is absent (10 ratios) |
| 单格长边 `panel_edge` | 512 | used when `spec` is absent |
| 画布比例 `canvas_aspect` | `auto` | used when `spec` is absent |
| 缝宽 `gutter` / 外边距 `margin` | 8 / 8 | used when `spec` is absent |
| 步数 `steps` | 35 | set to **4 only** with the 4-step distilled LoRA; either mismatch gives blur or black |
| CFG `cfg` | 1.0 | measured config. Only `>1` makes the negative prompt matter |
| 锁定方式 `init` | `auto` | see below |
| 降噪强度 `denoise` | 1.0 | Keep 1.0. The sampler has **no image input**: below 1 references no image, it only makes the host truncate sigma by `int(steps/denoise)` (equivalent to fewer steps); `report` prints nominal vs. effective |
| 复绘宫格线 `reborder` | on | redraw grid lines after decode |
| 采样边长 `sample_edge` | 1024 | per-panel mode only, see below |
| 负面提示词 `negative` | empty | in the **Advanced** fold. ⚠ at `cfg=1.0` the host skips the negative branch entirely |

**`mode` (how the panels are produced)**

| Option | What it does | When to use it |
|---|---|---|
| `整张` (whole grid) | **Default.** One sampling pass for the whole board: fast | Exploring ideas, or many panels (8/9) |
| `逐格` (per panel) | Every panel is **sampled on its own**, then assembled by coordinates: N times slower | When **the same character must stay consistent** across panels |

> **Why per-panel is more consistent**: in one pass the model spreads the global description
> over N regions and imagines each one separately, so the same character drifts (hat colour,
> face shape). Per panel, every panel reads the global description in full, and the character
> stays visibly the same.
>
> **Measured here** (4 panels / 16:9 / 8 steps / cfg 1.0 / same prompt): the variance of how
> much of the red beret appears in each of the four panels drops from `0.0036` (whole grid)
> to `0.00004` (per panel) — roughly 90× more uniform; the beret itself also comes out more solid.
>
> **Cost**: about N times the time (4 panels 27s → 60s). Peak VRAM is set by the per-panel
> `sample_edge` and the board size.

**`sample_edge` (sampling long edge)**

The big advantage of per-panel mode: a panel's **sampled** resolution is not bound by the
board size. `1024` samples each panel at 1024×N and downscales it into its final cell.

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

### 🎬 故事板 · 裁格 (`MossStoryboardCrop`)

Outputs `panels` (per-panel images), `preview` (crop lines drawn on the full canvas), `report`
and `index` (the panel number).
With `spec` → pixel-identical to generation. Without `spec` → requires `columns × rows` to divide
the image evenly and **raises** otherwise.
`index` left at -1 (the default) outputs every panel, exactly as before. A 0-based panel number
outputs just that one and emits it on the `index` output — wire that into Paste and you never
type a panel number by hand.

### 🎬 故事板 · 贴回 (`MossStoryboardPaste`)

Inputs `grid` (the full board), `panel` (the corrected panel) and `index` (which panel to write).
Outputs `grid` (the corrected board), `marked` (debug: frames the pasted region in red) and `report`.

**Purpose: fix one panel without re-running the whole board.** Sampling a board takes tens of
seconds to minutes, and re-running moves the seed of every panel at once. Instead: cut the panel
out, correct it however you like, then paste it back at the exact `spec` coordinates.

- `index` is 0-based, row-major (left to right, top to bottom). Wiring Crop's `index` output
  overrides the widget, so it can never disagree with the panel you actually cut.
- Only the target panel's content area is touched: gutters, margins and every other panel stay
  byte-identical, and the grid lines are preserved.
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
4. Follow it with a plain `KSampler` (30 steps / cfg 4 works) → `VAEDecode` → this pack's Paste.

**What it can and cannot do** (measured locally): colour, outfit, lighting, season and style
instructions are followed well. Composition preservation comes from the reference image itself and
weakens when `vae` is unwired. **It is not good at "re-shoot from another angle"** — that still
belongs to the layout-prompt route.

⚠ **It is not a cross-panel consistency solution.** Pushing one panel toward the others makes the
shot sizes converge — every panel drifts toward the same image, while a storyboard needs
"same character, different shot sizes". Cross-panel consistency comes only from
**per-panel sampling + global-section anchoring** (see [`docs/07`](docs/07-consistency.md)).
This LoRA is a **single-panel touch-up** tool: fix one panel's colour, outfit or light, then paste back.

> **Licensing and scope**: that LoRA is third-party community weights, **not distributed with this
> pack** and not maintained by it. Its model card marks the code portion as GPLv3; the weight
> licence is whatever the card states. This pack only links to it — it does not copy, modify or
> redistribute any weight file. This pack itself (MIT) does not depend on it: storyboard generation
> and Paste work fully without downloading anything.

---

## Known limits (read before filing a bug)

**Verified** — every row below except the first uses a distilled LoRA. Config is stated per row.

| Item | Config | Result | Sample |
|---|---|---|---|
| 3/4/6/8/9 panels · landscape/portrait · no gutter · thick gutter · small canvas · `plain` | base + 4-step LoRA / **4 steps** | sizes pixel-exact vs. theory | 15 runs |
| Grid lines | same | pure white after redraw | per case |
| Random seed | same | different seed → different md5 | 2 controls |
| **Element boxes constrain composition** | base + 4-step LoRA / **4 steps**; base + 8-step LoRA / **8 steps** | boxes only changed: the bicycle went from cropped out to fully visible with its front wheel in the lower-left. Reproduced on 3 seeds | 36 images (docs/08 §8) |
| **Four Chinese characters** (1024×576) | base + 8-step LoRA / **8 steps** | all correct | 1 image |
| **Four Chinese characters** (2048×1152) | base + 4-step LoRA / **4 steps** | all correct, softer strokes | 1 image |
| **Four Chinese characters** (2048×1152) | base + 8-step LoRA / **8 steps** | all correct, sharp strokes | 2 images |
| **Mixed zh/en · two lines · explicit colour** | base + 8-step LoRA / **8 steps** | all correct; line break honoured; colour applied | 3 images |

> **Base model only, no LoRA**: one control run at 2048×1152 / 30 steps / cfg 3.5 produced a normal
> image, but its text was **not checked character by character**, so it is not a result. Verify it yourself.

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
- ⚠ **Steps and cfg come in two bands, split by whether a LoRA is loaded.**
  Base model, no LoRA: 30–50 steps with cfg 3–5. With a 4-step distilled LoRA: 4–8 steps with
  cfg 1.0 — the distill LoRA was trained at cfg 1, so raising it breaks the distillation.
  Mixing the bands (a LoRA loaded but 35 steps) will not error, it just wastes the run.
- ⚠ **Keep `denoise` at 1.0.** There is no image to reference; lowering it only truncates sigma
  (fewer steps). `report` prints nominal vs. effective — trust the effective value.
- **Per-panel composition is only approximate.** MOSS's layout capability is *experimental*; this
  pack does no quality scoring.
- **At 8/9 panels each panel is ≈496×272.** Small text and fine texture degrade first — a
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
common cause is **mismatched bands**: a 4-step distilled LoRA loaded but 35 steps / cfg 3 used,
or no LoRA but 4 steps / cfg 1.0. Match the two bands listed under "Not possible".

**Right panel count, wrong content / everything crammed into one panel.** Layout boxes are
advisory. Shorten each caption to one shot, and check that `report` parsed as many captions as
there are panels.

**The canvas shape differs from the preview.** Check whether `spec` is connected: when it is, the
Sampler's own geometry parameters are ignored.

**Can't find the nodes.** Look under `moss/storyboard`. Searching `故事板`, `storyboard` or `宫格`
also works (search aliases are registered).

**No 中 / EN button.** Refresh the browser (F5). The button only exists on our nodes.
Console diagnostics: [`docs/04`](docs/04-i18n-and-widgets.md).

**Can I use my own negative prompt?** Yes — raise `CFG` above 1, then expand **Advanced** on the
node. At `cfg=1.0` the negative branch never enters the graph.

**Can I generate a plain (non-grid) image?** Use the Layout Editor's single mode (recommended), or set
`panels = 1` on the Sampler. `init = plain` only changes how the prompt is sent; it does not remove
the grid geometry.

---

## License

MIT for this pack. See [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) — no third-party code was
copied. Model weights are not distributed. The host ComfyUI is GPL-3.0 and is only `import`ed at
runtime.

## Documentation

Deep dives live in [`docs/`](docs/) (**written in Chinese**):
[`01`](docs/01-layout-and-panels.md) geometry · [`02`](docs/02-e2e-matrix.md) e2e matrix ·
[`03`](docs/03-open-source-readiness.md) readiness audit · [`04`](docs/04-i18n-and-widgets.md) i18n internals ·
[`05`](docs/05-local-development.md) local development · [`06`](docs/06-vae-memory.md) VAE memory budget ·
[`07`](docs/07-consistency.md) cross-panel consistency · [`08`](docs/08-visual-editor-plan.md) editor design
and element-box measurements · [`09`](docs/09-layout-breadth.md) use cases, layout paradigms, text measurements ·
[`10`](docs/10-contract.md) state v3 frozen contract · [`11`](docs/11-editor-ui.md) editor front-end internals.
Developer rules: [`AGENTS.md`](AGENTS.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md).
