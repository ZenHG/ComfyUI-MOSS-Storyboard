# ComfyUI-MOSS-Storyboard

🌐 [中文](README.md) · **English (condensed)**

> Condensed English version of [README.md](README.md) (Chinese is the source of truth).
> No section-level hash sync gate yet — see `AGENTS.md` §六.

**Storyboard grid** nodes for **MOSS-Image**: one run produces **one canvas** divided into
**3, 4, 6, 8 or 9 panels**, plus each panel cropped out separately.

| Item | Value |
|---|---|
| Version | **0.1.0** |
| License | **MIT** (credits in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)) |
| Host | **ComfyUI ≥ 0.42.0** (native MOSS-Image support landed in PR #17095) |
| Dependencies | **None** beyond host-provided `torch` / `comfy*` |

---

## Three things that prevent most misunderstandings

1. **It does not generate n images and stitch them.** It generates **one canvas** split into n
   panels — so more panels means fewer pixels per panel (≈496×272 each at 8 panels).
2. **The grid lines are not drawn by the model.** After decoding, the node **redraws** them from
   the same coordinates — width and position are exact, so they never wobble or break.
3. **What goes in each panel is up to MOSS's own layout capability.** Panel captions are converted
   into MOSS's native JSON layout boxes. That capability is officially marked *experimental*;
   **strict per-panel compliance is not guaranteed**. This is the one thing this pack does not
   score quantitatively.

---

## Features

| Capability | Notes |
|---|---|
| **3 / 4 / 6 / 8 / 9 panels** | Panel count is an input (`panels`); layout and canvas size are derived, 16-aligned |
| **Per-panel captions** | `[PANEL 1]`…`[PANEL n]` blocks become native MOSS layout boxes |
| **Deterministic grid lines** | Redrawn from coordinates after decoding |
| **Per-panel crops** | Row-major, pixel-aligned to the canvas; `spec` carries all geometry in one wire |
| **Bilingual UI (zh / en)** | A 中 / EN button on each of our nodes; display-only, never touches saved values |

**Three nodes — a minimal graph needs only the Sampler**, which carries the full set of geometry
parameters. The Layout node is an optional front-end: use it to preview the grid shape, or to
freeze the geometry into one `spec` wire for several downstream nodes.

| Node (Chinese UI) | Type | Required? | Role |
|---|---|---|---|
| 🎬 故事板 · 生成 | `MossStoryboardSampler` | **Yes** | `model + clip + vae + positive` → canvas + panels. Computes geometry from its own parameters when `spec` is absent |
| 🎬 故事板 · 宫格几何 | `MossStoryboardLayout` | Optional (recommended) | Geometry + layout JSON + grid template. **No model loaded**, instant |
| 🎬 故事板 · 裁格 | `MossStoryboardCrop` | Optional | Crops only; feed `spec` for pixel-exact panels |

> In an English UI the nodes read `MOSS Storyboard Layout` / `Sampler` / `Crop`.
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

---

## Install

```bash
cd <your ComfyUI>/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-MOSS-Storyboard
```

Restart ComfyUI. Nodes appear under **`moss/storyboard`**, and the startup log shows:

```
[MOSS SB] v0.1.0 已加载｜节点 3 个（出口 v3）｜3/4/6/8/9 宫格
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

A ready-made graph is in `examples/MOSS 故事板宫格.json`.

### Prompt rules (three, no more)

1. Everything **before the first `[PANEL …]`** is the **global description** (character look,
   scene, style, camera). Writing `[STYLE]:` is optional — it is just a readable label; the parser
   goes by position.
2. Each `[PANEL n]` block describes **that panel**: shot size + subject + action.
3. **Numbers need not be consecutive** and need not start at 1; the **order of appearance** decides
   the mapping (row-major: left→right, then top→bottom).

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

All within MOSS's 1–2 MP training range. Portrait panels (`panel_aspect = 9:16`) rotate the whole
layout by 90°: 3 panels become a horizontal strip (848 × 512), 6 become 2 × 3 (576 × 1520),
8 become 2 × 4 (576 × 2032); 4 and 9 are unchanged.

- **Bigger panels**: raise 单格长边 (`panel_edge`, 128–2048, step 16); the canvas follows.
  `panel_aspect` accepts 10 ratios: `16:9` `3:2` `4:3` `1:1` `4:5` `3:4` `2:3` `9:16` `21:9` `2.39:1`.
- **Force a layout**: set 列数 (`columns`); leave `rows = 0` to fill in (`columns × rows == panels`).
  3 panels default to a vertical strip because three 16:9 panels in a row would make a 5.2:1 canvas.

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

### 🎬 故事板 · 宫格几何 (`MossStoryboardLayout`, no model)

Outputs `width` `height` `columns` `rows` `spec` `layout_json` `template` `report`.

- **`spec`** — one string carrying all geometry. Feed it downstream and nothing can drift.
- **`layout_json`** — same format as the host's native `MOSS-Image Layout Prompt`.
- **`report`** — human-readable summary (panels, canvas, MP, caption alignment, warnings).
- Its `prompt` box **is intentionally empty**: this node only computes geometry.

### 🎬 故事板 · 生成 (`MossStoryboardSampler`)

Outputs `grid` `panels` `latent` `spec` `prompt_used` `report`.

Factory defaults below. The shipped workflow is already tuned to **4 panels / 4 steps / panel
edge 496**, so you can run it as-is.

| Parameter | Factory default | Notes |
|---|---|---|
| 正面提示词 `positive` | empty | See prompt rules above |
| 格数 `panels` | 8 | used when `spec` is absent |
| 单格比例 `panel_aspect` | `16:9` | used when `spec` is absent (10 ratios) |
| 单格长边 `panel_edge` | 512 | used when `spec` is absent |
| 画布比例 `canvas_aspect` | `auto` | used when `spec` is absent |
| 缝宽 `gutter` / 外边距 `margin` | 8 / 8 | used when `spec` is absent |
| 步数 `steps` | 35 | set to **4 only** with the 4-step distilled LoRA; either mismatch gives blur or black |
| CFG `cfg` | 1.0 | measured config. Only `>1` makes the negative prompt matter |
| 锁定方式 `init` | `auto` | see below |
| 降噪强度 `denoise` | 1.0 | 1.0 = full redraw. Below 1 keeps part of the starting image |
| 复绘宫格线 `reborder` | on | redraw grid lines after decode |
| 负面提示词 `negative` | empty | in the **Advanced** fold. ⚠ at `cfg=1.0` the host skips the negative branch entirely |

`init` values: `auto` / `layout` = layout boxes only (default); `template` = template image as the
repaint starting point (**does not produce a usable repaint in this version**); `both` = both
(template part equally inactive); `plain` = raw prompt, no layout boxes.

### 🎬 故事板 · 裁格 (`MossStoryboardCrop`)

Outputs `panels` (per-panel images), `preview` (crop lines drawn on the full canvas) and `report`.
With `spec` → pixel-identical to generation. Without `spec` → requires `columns × rows` to divide
the image evenly and **raises** otherwise.

---

## Known limits (read before filing a bug)

**Verified** (machine-asserted; evidence in [`docs/02`](docs/02-e2e-matrix.md))

| Item | Result | Runs |
|---|---|---|
| 3/4/6/8/9 panels · landscape/portrait · no gutter · thick gutter · small canvas · `plain` | all rendered; sizes pixel-exact vs. theory | 15 real runs |
| Grid lines | pure white after redraw | sampled per case |
| Random seed | different seed → different canvas md5 | 2 controls |

**Not possible in this version**

- 🔴 **`init=template` / `both` produce no usable repaint.** Three runs: 35 steps / denoise 0.85 →
  fully black; 12 steps / 0.6 and 4 steps / 0.5 → template ghosting. Whenever sigma is actually
  truncated, the model leaves the image untouched — consistent with the official limitation
  ("not conditioned on reference images, no editing"). **Hence the default is the layout route.**
- ⚠ **At low step counts 降噪强度 is silently clamped.** The host recomputes sigmas as
  `int(steps / denoise)`, so at 4 steps `int(4/0.85)=4` truncates nothing (equivalent to 1.0).
  `report` prints nominal vs. effective — trust the effective value.
- **Per-panel composition is only approximate.** MOSS's layout capability is *experimental*; this
  pack does no quality scoring.
- **At 8/9 panels each panel is ≈496×272.** Small text and fine texture degrade first — a
  resolution budget, not a node bug.

**Hard constraints**

- Canvas and panel dimensions must be multiples of **16** (enforced; hand-edited `spec` is rejected).
- >2 MP warns, >4 MP warns loudly: MOSS trains at 1–2 MP, official extrapolation ≈4 MP.

---

## FAQ

**All black.** Check `report` for nominal vs. effective denoise, and the step count. Usual causes:
① `init=template` (inactive in this version); ② 4-step LoRA with 35 steps, or no LoRA with 4 steps.

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

**Can I generate a plain (non-grid) image?** Yes: set panels to 1, or `init = plain`.

---

## License

MIT for this pack. See [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) — no third-party code was
copied. Model weights are not distributed. The host ComfyUI is GPL-3.0 and is only `import`ed at
runtime.

## Documentation

Deep dives live in [`docs/`](docs/) (**written in Chinese**):
[`01`](docs/01-layout-and-panels.md) geometry · [`02`](docs/02-e2e-matrix.md) e2e matrix ·
[`03`](docs/03-open-source-readiness.md) readiness audit · [`04`](docs/04-i18n-and-widgets.md) i18n internals.
Developer rules: [`AGENTS.md`](AGENTS.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md).
