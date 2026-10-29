# ComfyUI-MOSS-Storyboard

🌐 **English** · [中文](README_ZH.md)

![MOSS Storyboard for ComfyUI: the visual layout editor, one generated 4-panel grid, and the four panel crops](assets/hero-storyboard.png)

**Layout and edit nodes for MOSS-Image.** Two uses:

- **Storyboard** — one canvas split into 3 / 4 / 6 / 8 / 9 panels, one caption per panel, one crop per panel.
- **Local edit** — fix a single panel, or put a reference face into one panel, then paste it back. Every other panel stays pixel-identical.

> 🔴 **Remember one thing: a box on the canvas is a hint, not a coordinate.**
> A box becomes one line of text holding coordinates and goes into the prompt; the model decides where to draw.
> It can overshoot, drift, merge two boxes into one, or ignore them entirely.
> Details and measurements: [1.6](#16-boxes-are-a-hint-what-they-do-change-and-what-they-do-not).

| Item | Value |
|---|---|
| Version | **0.3.2** |
| License | **MIT** (credits in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)) |
| Host | **ComfyUI ≥ 0.42.0** (native MOSS-Image support landed in PR #17095) |
| Dependencies | **Zero** extra pip dependencies beyond the host-provided `torch` / `comfy*`. Face Swap's auto mask optionally uses `insightface` + `onnxruntime` — the pack runs without them |

**What this pack guarantees:** canvas size, per-panel coordinates, crop rectangles, grid-line position — arithmetic, aligned to the pixel.
**What it does not guarantee:** what ends up inside the panels, where elements land, or that the same face survives across panels. Sampling decides those.

---

## Four readers, four paths

| Tier | Who you are | Start at | Roughly |
|---|---|---|---|
| **1 · First image** | You just want it installed and one storyboard out | [§1](#1--install-get-the-first-image) | 10 min |
| **2 · Tuning** | It runs; you want control over panels, size, quality, VRAM | [§2](#2--tuning) | look up as needed |
| **3 · Pipeline** | You are wiring the nodes into your own graph, or doing face swap / local edits | [§3](#3--into-your-pipeline) | look up as needed |
| **4 · Code** | You want to run the checks, send a PR, or file a bug | [§4](#4--code--bugs) | — |

No need to read top to bottom. Every section after tier 1 stands alone.

## Terms (one concept, one name)

Different names for the same thing is the most common source of misreading. This pack uses:

| Term | Means | Do not confuse with |
|---|---|---|
| **canvas** | the full sampled image (width and height are multiples of 16) | not "the big image" |
| **grid / storyboard** | one canvas cut into N panels | also called a comic page or shot sheet |
| **panel** | one cell; "panel long edge" = the long edge of one cell | "per panel" is only used for allocation (how many pixels each gets) |
| **`整张`** (whole canvas) | Generate's mode: **one sampling pass** for all panels | ≠ the editor's single mode |
| **`逐格`** (per panel) | Generate's mode: **each panel sampled on its own**, then assembled | N times slower, but reworkable per panel |
| **single mode** | the Layout Editor's **no-panel** mode (posters) | was once called 单张 in Chinese docs — that read like `整张` |
| **layout box / box** | a rectangle on the canvas that becomes **one line of text with coordinates** | not a mask, not a selection |
| **`spec`** | one string carrying all geometry (11 fields) | not a file, not JSON |
| **reference / source** | face swap: `ref` = who it looks like; `image` = what gets edited | do not call both "the reference" |
| **`init`** | Generate: `auto` / `layout` / `plain` | ⚠ Face Swap also has `init`: `source` / `empty` — **same name, different meaning** |

Full table and finer usage: [manual §0](docs/19-node-manual.md#0-术语一个概念一个名字) (Chinese).

---

## 1 · Install, get the first image

### 1.1 What to install

| Dependency | Required | Where |
|---|---|---|
| ComfyUI **≥ 0.42.0** | ✅ | older versions lack the `moss_image` text-encoder type |
| `moss_image_sft_bf16.safetensors` | ✅ | `models/unet/` or `models/diffusion_models/` |
| `qwen3.5_9b_int8_convrot.safetensors` | ✅ | `models/clip/` |
| `flux2-vae.safetensors` | ✅ | `models/vae/` |
| pip packages | ❌ | none (optional `insightface` + `onnxruntime` for Face Swap's auto mask) |

Weights are **not** bundled. For download links and exact folder names, follow the official tutorial:
<https://docs.comfy.org/tutorials/image/moss-image/moss-image>.

**The 4-step distilled LoRA is optional and not part of this pack.** The three storyboard examples ship with
it **bypassed**; the poster example has no LoRA; **the face-swap and pose examples have both LoRAs active** —
they are part of those two recipes ([3.5](#35-face-swap--masked-local-edit)).
Without the LoRA use **50 steps / cfg 3 / euler / sgm_uniform** (MOSS-Image-SFT model card);
with it set `steps` to **4** and `cfg` to **1.0** (un-bypass with `Ctrl+B` first if it ships bypassed).

### 1.2 Install

**ComfyUI Manager (recommended)** — search **MOSS Storyboard** → Install → restart ComfyUI.

**ComfyUI CLI** (if your install ships the `comfy` command):

```bash
comfy node install moss-storyboard
```

**git**:

```bash
cd <your ComfyUI>/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-MOSS-Storyboard
```

After the restart, seven nodes appear under **`moss/storyboard`**, and the startup log shows a line
(`<version>` is whatever you installed):

```
[MOSS SB] v<version> 已加载｜节点 7 个（出口 v3）｜3/4/6/8/9 宫格
```

That is Chinese for "v`<version>` loaded | 7 nodes (V3 node API) | 3/4/6/8/9 grids".

**No such line = the pack did not load.** That check beats any settings inspection.
To fall back to the legacy V1 node API, set `MOSSSB_NODE_API=v1`.

**Restart ComfyUI once more after dropping the model files in** — otherwise the loaders' dropdowns do not list them.
Host version: the startup log starts with `ComfyUI Version:`; `git log -1 --format=%h` gives the commit.

### 1.3 Run the first one

1. Install the pack and the three model files above.
2. **Drag** [`examples/moss-layout-editor-storyboard.json`](examples/moss-layout-editor-storyboard.json)
   **into the ComfyUI window** (or `Workflow → Open`). Everything is already wired.
   The file lives in `<ComfyUI>/custom_nodes/ComfyUI-MOSS-Storyboard/examples/`; you can also open it on GitHub.
3. Queue.

Outputs: a **1024×576** board and four **496×272** panels.

> **Where do the images go?** This pack saves nothing itself. The example wires `SaveImage`, so files land in
> `output/moss/storyboard/` under your ComfyUI root — `grid_*.png` and `panel_*.png`.
> **With no save node attached, the image only exists in memory and is gone when the run ends.**

**Eight examples, one job each**

> ⚠ **The face-swap and pose examples reference input images** (`moss_faceswap_src.png` board /
> `moss_faceswap_single_src.png` single photo / `moss_faceswap_ref.png` face close-up) and **the repo does not
> ship them**. Put your own into `<ComfyUI>/input/`, or point `LoadImage` at your files.
> **src and ref must be different people** — swapping one person onto themselves looks like "nothing happened".

| File | What it does | Model | Third-party nodes |
|---|---|---|---|
| `moss-layout-editor-storyboard.json` | 4-panel storyboard with in-graph notes (**the starting point**) | ✅ | — |
| `moss-storyboard-4panels-ui.json` | 4-panel storyboard, plainest wiring | ✅ | — |
| `moss-layout-editor-poster.json` | single poster, boxes on the editor canvas | ✅ | — |
| `moss-prompt-compiler.json` | official draft → prompt + editor state | ❌ no VRAM | — |
| `moss-faceswap-ui.json` | 4-panel board → swap panel 0 → board out | ✅ | needs DMD2 + edit LoRA |
| `moss-faceswap-single-ui.json` | **one photo** → face swap → image out (no `spec`) | ✅ | needs DMD2 + edit LoRA |
| `moss-pose-generate-ui.json` | reference face + prompt → one posed image (`init=empty`) | ✅ | none |
| `moss-storyboard-4panels-edit.json` | crop one panel → instruction edit → paste back | ✅ | needs edit LoRA |

> ⚠ **Four of the eight examples carry the host's `LazyCache` node** (`editor-storyboard` / `4panels-ui` /
> `4panels-edit` / `poster`; the face-swap and pose examples have none) between the LoRA and the sampler. It is **not part of this pack** — it is ComfyUI core
> (`comfy_extras/nodes_easycache.py`, experimental, filed under `advanced/debug`) and lets the sampler reuse steps.
> **Bypass it (`Ctrl+B`) or delete it before comparing anything against the numbers on this page** — with it on,
> the sampler is not doing the work those numbers were measured on. On a host build without the node, ComfyUI
> reports a missing node type; delete it and the graph runs. **Ignore it for your first run** — bypass it only
> when you want your numbers to match this page.

### 1.4 Wire it yourself (one node is enough)

| Loader | File | Plugs into 🎬 Storyboard · Generate |
|---|---|---|
| `UNETLoader` | `moss_image_sft_bf16.safetensors` | `model` |
| `CLIPLoader` | `qwen3.5_9b_int8_convrot.safetensors`, **`type` must be `moss_image`** | `clip` |
| `VAELoader` | `flux2-vae.safetensors` | `vae` |

Then paste this into **`positive`** on 🎬 Storyboard · Generate:

```
Rainy-night cafe, one canvas divided into a strict grid of 4 panels, thin white dividers;
35mm film, real actors, natural skin texture, shallow depth of field

[PANEL 1]: medium shot, two people across a table, she pushes the lemonade glass to center
[PANEL 2]: close-up, her lashes against rain-streaked glass
[PANEL 3]: over-the-shoulder, he looks out the window
[PANEL 4]: tabletop close-up, two hands resting on the rim
```

> 🔴 **The step most people trip on: the panel count and the number of blocks must match.**
> The factory default is **8** panels and the script above has **4** blocks — **set `panels` to 4 first**,
> or the run stops before sampling (`提示词有 4 段 [PANEL]，但布局是 8 格`). The reverse — 8 blocks on a
> 4-panel layout — fails the same way. Canvas size is derived automatically; never type width / height.
> (`moss-layout-editor-storyboard.json` already has 4 panels — no change needed; this only matters when you wire
> your own graph.)

Both outputs are images and **neither is saved for you**:

| Output | What it is | Wire to |
|---|---|---|
| `grid` | the whole board | `SaveImage` (write to disk) or `PreviewImage` (look only) |
| `panels` | one image per panel | `SaveImage` — writes N files in one go (N = panel count) |

> To check the grid shape before spending a run, wire `template` from 🎬 Storyboard · Grid Layout into a preview:
> it is a picture of flat cells and gutters, showing where each panel sits and how wide the lines are.

### 1.5 Five traps

(`spec` is explained in [3.2](#32-spec-is-the-single-source-of-geometry); the three `init` values are in
[manual §1](docs/19-node-manual.md#1--生成节点). Here you only need to know the two knobs exist.)

| Trap | Symptom | Fix |
|---|---|---|
| panel count ≠ block count | error before sampling | change the panel count, add blocks, or set `init` to `plain` |
| `spec` wired, panel widgets edited | canvas shape does not change | **`spec` wins**; the node's `panels` / `panel_edge` / `gutter` / `margin` are ignored (`report` says so in line 1) |
| no save node | nothing on disk after the run | wire `SaveImage` |
| mixed recipe bands | all black | see [2.3](#23-recipes-two-bands-do-not-mix) |
| `LazyCache` in the chain | numbers do not match this page | `Ctrl+B` bypass, or delete it |

### 1.6 Boxes are a hint: what they do change, and what they do not

This is the most over-estimated part of the pack. **If your requirement is "draw exactly inside the box",
this pack cannot do it — and neither can any other node built on the same MOSS mechanism.**

**How a box reaches the model.** You draw a box; the pack turns it into one line of text with coordinates,
which enters `CLIPTextEncode` along with everything else and becomes an ordinary text condition.
No mask, no regional conditioning, no separate layout channel, and nothing crops attention to those
coordinates while sampling. The official MOSS template, KJ's layout node and this pack all work this way.

**What that means in practice.** Elements come out larger or smaller than the box and slightly off;
two boxes can merge (you place the person and the table apart, the model puts the person on the table);
the model can ignore the boxes and cram everything into one panel; **small boxes are ignored first**,
so small text and small props disappear before anything else; a different seed follows the boxes differently.

**What boxes do buy you.** They shift roughly where things go and roughly how big they are; they do not place
anything precisely. In one controlled comparison a box was moved from outside the frame to the lower-left corner
with nothing else changed: the bicycle went from "cropped out" to "fully visible, lower left", reproducible
across 3 seeds (36 images).

**Do not use boxes for:**

| Not possible | Why |
|---|---|
| pinning eyes, fingers or a small prop to a spot | a box is coordinates inside text; nothing crops attention to it |
| guaranteeing a text box never overflows by a pixel | box height does not control type size — a 43 px box rendered ~130 px text (see [2.6](#26-known-limits)) |
| keeping two elements from overlapping | boxes carry no mutual constraint |
| keeping the same face across panels | identity is not a box problem; this pack does not promise it (see [2.6](#26-known-limits)) |

**In grid mode each panel's box and its crop rectangle come from the same arithmetic.** That is *geometric*
precision — crops line up to the pixel. It is not *pictorial* precision. The two are constantly confused.

For pixel-level control you need a model or a ControlNet with masks or regional conditioning — outside this pack.
What this pack does: write the prompt correctly, compute the canvas, cut the panels, paste an edited panel back.

---

## 2 · Tuning

### 2.1 Prompt rules

**Three rules**

1. Everything **before the first `[PANEL …]`** is the **global description** (character look, scene, style, camera).
   Writing `[STYLE]:` is optional — it is only a readable label; the parser goes by position.
2. Each `[PANEL n]` block describes **that panel**: shot size + subject + action.
3. **Panel numbers are an ordering key, not a cell slot.** Captions are sorted by number and paired with cells in
   that order: first caption → first cell, second → second, and so on. Gaps and out-of-order blocks are accepted
   (they are sorted first); the number itself picks no cell (a lone `[PANEL 6]` still lands in cell 1).

| You write | Result |
|---|---|
| `[PANEL 1]: …` `[PANEL 2]: …` | two captions → panels 1 and 2 |
| `[画面 1]: …` `[分镜 1]: …` | also valid (Chinese markers are recognised) |
| no `[PANEL]` at all + `init=plain` | the whole text is a plain prompt; **no layout boxes** are compiled (`report` says so). The canvas is still the grid canvas |
| 8 panels, 4 blocks | **error by default**, before sampling. Three ways out: write 8 blocks / set `panels` to 4 / set `init` to `plain` |
| one caption spread over several lines | merged into one caption; no effect |

**Two rules of thumb**

- If a character drifts between panels, **fix the global description first** — it carries the most weight.
- One shot per panel. A compound caption such as "medium shot + close-up" makes the model pick one.

### 2.2 Panels → canvas

Numbers below: panel 16:9, long edge 496, gutter 8, margin 8 — the shipped examples' settings
(4 panels come out 1024 wide, hence "the 1024 tier").
**The factory default is a 512 px panel edge** — one step larger (496 → 512), with a correspondingly larger canvas.
Any table using one of the two says which.

| Panels | Shape | Canvas | MP | Panel |
|---|---|---|---|---|
| 3 | **vertical strip** (3 stacked) | 512 × 848 | 0.43 | 496 × 272 |
| 4 | **quad** (2 × 2) | **1024 × 576** | 0.59 | 496 × 272 |
| 6 | **wide** (2 rows × 3) | 1520 × 576 | 0.88 | 496 × 272 |
| 8 | **wide** (2 rows × 4) | 2032 × 576 | 1.17 | 496 × 272 |
| 9 | **3 × 3** | 1520 × 848 | 1.29 | 496 × 272 |

**Panel size does not change with the panel count** — only the canvas grows. 8 and 9 panels land in MOSS's
1–2 MP training range; 3 / 4 / 6 are below it — usable, but outside the range the model was trained on.

**Portrait panels (9:16)** rotate the whole layout by 90°: 3 panels become a horizontal strip (848 × 512),
6 become 2 columns × 3 rows (576 × 1520), 8 become 2 columns × 4 rows (576 × 2032); 4 and 9 are unchanged.

| Want | Knob | Notes |
|---|---|---|
| bigger panels | **panel long edge** `panel_edge` | 128–2048, step 16. The canvas follows |
| force a layout | **columns** `columns` | set the column count, leave `rows = 0` to fill it in; `columns × rows` must equal the panel count |
| three panels in a row | `columns = 3` | 3 panels default to a vertical strip: three 16:9 panels in a row make a 1520 × 288 canvas (≈5.3:1), past this pack's 4:1 tolerance |

**Why multiples of 16**: MOSS's VAE downscales by 16, so a width or height that is not a multiple of 16 misaligns.
The node aligns everything; the extra 0–15 px goes **only into the right and bottom margin** (same colour as the
grid lines, invisible). Panel sizes are never touched — otherwise every panel coordinate would drift.

### 2.3 Recipes: two bands, do not mix

| What you loaded | steps / cfg | Sampler / scheduler |
|---|---|---|
| **Base model only** (the examples' default) | **50 / 3.0** | `euler` / `sgm_uniform` |
| **4-step distilled LoRA stacked** | **4–8 / 1.0** | same |

- **Do not mix the bands.** LoRA at 50 steps / cfg 3, or no LoRA at 4 steps / cfg 1.0: no error, worse output,
  sometimes fully black.
- The distilled LoRA was distilled at cfg 1; raising cfg breaks the distillation.
- `euler` is the model card sampler; `sgm_uniform` comes from the official OpenMOSS ComfyUI plugin workflow
  (the model card names no scheduler). The scheduler list is read from the host
  (`comfy.samplers.SCHEDULER_NAMES`), so new host schedulers appear automatically.
- **Keep `denoise` at 1.0.** The Generate node has no image input, so lowering it references nothing — it only
  makes the host truncate sigma by `int(steps/denoise)` (fewer steps). `report` prints nominal vs. effective;
  trust the effective value.
- The model card also specifies shift 5 (built into the native ComfyUI model) and negative-branch skipping at
  layer 22 (wire the host's `SkipLayerGuidanceDiTSimple`, `single_layers` `22`; the shipped examples do not).
  The native ComfyUI template uses 25 steps / cfg 2.5 / er_sde (Comfy-Org's choice) — **not** the model-card recipe.

### 2.4 How much VRAM

VAE decode — not sampling — is the peak. The same canvas can differ by up to **4×** depending on host version.

| Canvas | Panels | New host | Old host |
|---|---|---|---|
| 1024 × 576 | 4 | ~0.6 GB | ~2.4 GB |
| 2032 × 576 | 8 | ~1.2 GB | ~4.8 GB |
| 1520 × 848 | 9 | ~1.3 GB | ~5.2 GB |
| 2096 × 608 | 8 (factory 512 edge) | ~1.3 GB | ~5.2 GB |

The first three rows use the shipped 496 px edge; the last row is the factory 512 px default.
**Which column are you in?** The fix is commit `d07dff4` (2026-10-23): if your build contains it, use **New**,
otherwise **Old**. Desktop / portable / pip builds depend on the version — **if unsure, budget for Old**.

**When memory runs short**, the host tries the full-image decode first, then falls back to **tiled decode**.
A tile border may land inside a panel and leave a seam there, at an unpredictable position.
Look for `retrying with tiled VAE decoding` in the log.

**Fixes, cheapest first**: ① `git pull` the host (the old build reserved 4× what it needs);
② lower `panel_edge` (half the edge → ¼ the decode memory); ③ use fewer panels (8 → 4 halves the canvas area).

> ⚠ The saving only applies while the VAE runs in fp16 or bf16. `--fp32-vae`, or a host that falls back to fp32,
> silently disables it (no error — it just reverts to the old behaviour).

**Measured tiers on a single 12 GB card, whole-canvas mode** (50 steps / cfg 3.0 / euler + sgm_uniform / 16:9).
⚠ This set ran **with the 4-step distilled LoRA attached**, so it is not the same operating point as the 50/3
rows above — read it for headroom, not quality. The raw data records peak VRAM and OOM only, **nobody graded the
images**; and "4-step LoRA at 50 steps" is one of the mismatched recipes listed as a common cause of black output.
Do not read this table as a recommended recipe.

| panels × `panel_edge` | canvas | MP | peak MB |
|---|---|---|---|
| 4 × 512 | 1056×608 | 0.64 | 11359 |
| 4 × 544 | 1120×640 | 0.72 | 11176 |
| 6 × 512 | 1568×608 | 0.95 | 11250 |
| 6 × 544 | 1664×640 | 1.07 | 11267 |
| 8 × 512 | 2096×608 | 1.27 | 11269 |
| 8 × 544 | 2224×640 | 1.42 | 11298 |

> Across 0.64–1.42 MP the peak sits at 11.2–11.4 GB (**≈0.9 GB headroom for the whole canvas**); the worst point
> measured anywhere — per-panel, 8 × 544 — leaves 0.7–0.8 GB. A 12 GB card runs these tiers, **but it can still
> OOM when the desktop already holds VRAM** (the peak itself carries ±200 MB of desktop noise).
> Do not read this as "12 GB is enough". Data [`docs/data/g1-results.json`](docs/data/g1-results.json),
> per-run log [`docs/data/g1-report.md`](docs/data/g1-report.md).

### 2.5 Error table

Every row below is copied verbatim from a `ValueError` in the source. **The messages are Chinese** — paste a few
characters into a search here to find your row.

| Error text (verbatim) | Cause | Fix |
|---|---|---|
| `提示词有 4 段 [PANEL]，但布局是 8 格` | block count ≠ panel count | add blocks / change the count / set `init` to `plain` |
| `全局段写「恰好 4 个面板」，但布局是 8 格` | the count inside the global paragraph disagrees with the real one | change that number, or delete the phrase |
| `逐格模式需要 [PANEL n] 分段描述（现在只有全局描述）。` | `mode=逐格` with only a global paragraph | add one block per panel |
| `逐格模式要求每格都有一句描述：提示词只有 4 段，布局有 8 格。` | `mode=逐格` with fewer blocks than panels | match the counts; per-panel never invents captions |
| `没接 spec 时必须填 columns 和 rows（都 >0），否则无法确定裁切框` | Crop / Paste with no `spec` and `columns`/`rows` at 0 | wire `spec`, or set columns × rows |
| `图像 1024x576 不能被 3x2 整除：会得到差 1px 的格子，下游拼接必崩。…` | Crop without `spec` on a size that does not divide | wire `spec`, or feed a canvas that divides |
| `index=5 越界：共 4 格（0 起）` | Crop: panel number past the last panel | use 0…3, or wire Crop's `index` output |
| `index=5 越界：本布局共 4 格（0 起）` | same, raised by **Paste** (different wording) | same fix; to repaint the whole image, do not wire `spec` |
| `宫格尺寸 1024x1152 与布局 1024x576 不一致` | Paste got a board that is not the `spec` canvas | feed the board the Sampler produced with that `spec` |
| `draft 为空：请粘贴官方 prompt-enhancer 的 caption/layout 草稿 JSON。` | Draft Compiler run with an empty draft | paste the **draft**, not the compiler's own output |
| `元素 3 没有描述（caption 不能为空）` | a box on the canvas has no caption | give it a caption, or delete it |
| `label 只能是 object/character/text（收到 'xxx'）` | a hand-edited state or draft used another type | only those three values are allowed |

**No error, wrong result:**

| Symptom | Check first |
|---|---|
| all black | mixed recipe bands ([2.3](#23-recipes-two-bands-do-not-mix)) |
| right panel count, everything in one panel | boxes are hints ([1.6](#16-boxes-are-a-hint-what-they-do-change-and-what-they-do-not)); shorten each caption to one shot |
| canvas shape differs from the preview | is `spec` wired? When it is, the Sampler's panel widgets are ignored |
| can't find the nodes | category `moss/storyboard`; searching `故事板` / `storyboard` / `宫格` also works |
| OOM at VAE decode, or faint horizontal seams | not enough memory → tiled decode ([2.4](#24-how-much-vram)) |
| no 中 / EN button | hard-refresh the browser (F5); check the console for `[MOSS SB] i18n: 就绪` |

### 2.6 Known limits

**Four lines, then the detail**:

| Area | Verdict |
|---|---|
| Sizes and crops | exact arithmetic, pixel-aligned; 3/4/6/8/9 panels, landscape/portrait, no gutter and thick gutter all verified |
| Text | four Chinese characters are correct at 1024×576; 2048 only sharpens strokes. **Box height does not control type size** — a 43 px box rendered ~130 px glyphs |
| Boxes | decide *roughly where* and *roughly how big*, never exact placement |
| Identity | no promise of the same face across panels. You get a face-swap tool, not a guarantee |

Per-group configurations, sample sizes and exceptions (which group had only 2 seeds, which one only tested red) are in
the [manual §8](docs/19-node-manual.md#8--实测与边界) (Chinese) — read the sample sizes there before quoting these numbers.

#### Not possible in this version

- **The Generate node has no image input, so it does no img2img.** The latent always starts empty; keep `denoise`
  at 1.0. Template-latent initialisation was measured and does not work: 35 steps / denoise 0.85 → fully black;
  12 steps / 0.6 and 4 steps / 0.5 → template ghosting.
  **To change an existing image**, use [3.4 fix one panel](#34-fix-one-panel-crop--edit--paste), or the
  [3.8 face swap node](#35-face-swap--masked-local-edit) (it is masked img2img: only the mask is redrawn).
- **No promise that the same face survives across panels.** Identity is decided by sampling. What this pack ships is
  a **tool**: 🎬 Storyboard · Face Swap puts a reference face into **one panel** at a time, driven by that reference.
  That is **not** the "pin one character card, then derive every panel from it" workflow — that one needs a separate
  node pack, which is not part of this one and is not installed with it.
- **Per-panel composition is only approximate.** Boxes are a hint, not a selection
  ([1.6](#16-boxes-are-a-hint-what-they-do-change-and-what-they-do-not)). MOSS's layout capability is marked
  *experimental*; this pack does no quality scoring.
- **At 8/9 panels each panel is small** — about 496×272 with the shipped 496 px edge (512×288 at the factory
  default). Small text and fine texture degrade first — a resolution budget, not a node bug.

#### Hard constraints

- Canvas and panel dimensions must be multiples of **16**. Geometry the nodes compute is always snapped to 16;
  `spec` is validated field by field as external input: missing fields, out-of-range values, `pw` / `ph` / `w` / `h`
  that are not multiples of 16, and "the fields do not add up to `w` / `h`" all raise.
  ⇒ **Do not hand-write `spec`**; use the wire the node produces.
- >2 MP warns, >4 MP warns loudly: MOSS trains at 1–2 MP, official extrapolation ≈4 MP.
- **VAE decode precision is decided by ComfyUI, not by the weight file.** The host casts the VAE to `fp16` at load
  (falling back to `bf16` / `fp32` only if your GPU cannot do fp16), so `flux2-vae.safetensors` (F32) and
  `flux2-vae_BF16.safetensors` (BF16) run at the **same precision** (speed not measured) — the file's dtype is
  normalised away on load, leaving only disk usage and load-time RAM as the difference. For true fp32 decoding use
  the host flag `--fp32-vae`; this pack deliberately implements no second precision path, which would diverge from
  the host's own `VAEDecode`. An escape hatch exists: the Sampler has a `latent` output, so you can wire your own
  `VAEDecode` with whatever VAE and precision you like.

---

## 3 · Into your pipeline

### 3.1 The seven nodes

| Node (English UI) | Type | Required? | Role | Full table |
|---|---|---|---|---|
| 🎬 Storyboard · Generate | `MossStoryboardSampler` | **Yes** (storyboards) | model + clip + vae + prompt → grid + panels | [§1](docs/19-node-manual.md#1--生成节点) |
| 🎬 Storyboard · Grid Layout | `MossStoryboardLayout` | Optional (recommended) | geometry + `spec` + layout JSON + template. **No model loaded** | [§2](docs/19-node-manual.md#2--宫格几何) |
| 🎬 Storyboard · Split Panels | `MossStoryboardCrop` | Optional | crops only; with `spec` it cuts exactly where the board was generated | [§3](docs/19-node-manual.md#3--裁格--改--贴回) |
| 🎬 Storyboard · Paste Panel | `MossStoryboardPaste` | Optional | pastes a corrected panel back at the `spec` coordinates | [§3](docs/19-node-manual.md#3--裁格--改--贴回) |
| 🎬 Layout Editor | `MossLayoutEditor` | Optional (posters / free layouts) | canvas + elements → layout prompt + sampling size. **No model loaded** | [§4](docs/19-node-manual.md#4--布局编辑器) |
| 🎬 Prompt · Draft Compiler | `MossPromptCompiler` | Optional (official skill bridge) | official draft → MOSS prompt + editor state. **No model loaded** | [§5](docs/19-node-manual.md#5--草稿编译) |
| 🎬 Storyboard · Face Swap | `MossStoryboardFaceSwap` | Optional | reference face → one panel (or a single image). **Loads a model** | [§6](docs/19-node-manual.md#6--换脸--蒙版局部编辑) |

**A minimal graph needs only Generate** — it carries every geometry parameter. The other six are optional:
Grid Layout freezes geometry into one `spec` for several downstream nodes; Crop / Paste fix a single panel;
the Layout Editor makes posters; the Draft Compiler bridges the official prompt skill; Face Swap handles identity
or a masked redraw.

> **Every example is UI format** (drag it into the canvas). To drive `/prompt` you must convert to API format first
> (ComfyUI's Export (API) / Save (API Format) — the menu name depends on the frontend version).
> No converter ships with the pack: `tools/` exists only in the repo, not in the installed package.

### 3.2 `spec` is the single source of geometry

`spec` is one string carrying the whole geometry — 11 fields:
`panels` `w` `h` `cols` `rows` `pw` `ph` `gutter` `margin` `padx` `pady`
(`pw` / `ph` are the panel size; `padx` / `pady` are the few pixels that round the canvas up to a multiple of 16).

It is a **forceInput**: wire only, **you cannot type it on the node panel**. Parsing validates it field by field —
missing fields, out-of-range values, `pw` / `ph` / `w` / `h` that are not multiples of 16, and "the fields do not
add up to `w` / `h`" all raise — so hand-copying a `spec` breaks easily. Do not hand-copy it. A real one looks like `MOSS-SB1 panels=4 w=1024 h=576 cols=2 rows=2 pw=496 ph=272
gutter=8 margin=8 padx=8 pady=8` — and `padx` / `pady` are exactly what makes the arithmetic close.

| Produced by | Consumed by |
|---|---|
| Grid Layout · `spec` | Generate · `spec` (geometry only, no prompt) |
| Generate · `spec` | Split Panels · `spec`, Paste Panel · `spec` |
| Layout Editor · `spec` (single mode = 1 panel) | Generate / Split Panels / Paste Panel |

**One rule: when `spec` is wired, `spec` wins.** The Generate node's `panels` / `panel_edge` / `gutter` / `margin`
widgets are still on the panel but take no effect, and `report` says "geometry source: spec wiring" on its
second line (the first line is the recipe hint).
That is the most common trap.

Without `spec`, Split Panels and Paste Panel need `columns` / `rows` filled in by hand (0 raises instead of guessing).

### 3.3 Three knobs are enough

Day to day you touch three: **panel count** `panels`, **panel long edge** `panel_edge`, **steps / cfg**.
The other nine all have defaults; the factory values are in the
[manual §1](docs/19-node-manual.md#1--生成节点) (Chinese).

| Want | Knob | Remember |
|---|---|---|
| a different panel count | `panels` | **must equal the number of `[PANEL n]` blocks**, or the run stops before sampling |
| bigger panels | `panel_edge` | the canvas follows; never type width / height |
| different quality | `steps` / `cfg` | two bands, do not mix: no LoRA **50 / 3**; distilled LoRA **4–8 / 1.0** ([2.3](#23-recipes-two-bands-do-not-mix)) |
| per-panel rework | `mode` | `整张` is fast and keeps one face more stable; `逐格` is slower, more pixels per panel, reworkable |

Seven outputs; you need two: `grid` (whole board) and `panels` (per panel).
`report` is the self-check text — geometry source, effective denoise, mask coverage. Read it first when something looks wrong.

### 3.4 Fix one panel: crop → edit → paste

When one panel is wrong, **do not re-run the board** (that moves every panel's seed). Cut it out, fix it, paste it back.

```
Crop (index set) ─panel─→ your edit chain ─→ Paste
Crop ──index───────────────────────────────→ Paste
Generate ──grid────────────────────────────→ Paste
Generate ──spec────────────────────────────→ Paste / Crop
```

Three things to remember:

- **No hand-typed panel numbers**: wire Crop's `index` output and Paste follows it (0-based, row-major).
- **`shave` must match the crop**, otherwise the panel is bicubically resampled (content scaled); `report` says so.
- **To fix several panels, chain Paste nodes**: the first `grid` output feeds the next `grid` input.

The middle step is **up to you**: another node pack, external retouching, or hand-editing and reloading.
For MOSS's own instruction editing the host nodes are enough (needs the `Moss-Image-Edit` LoRA) — wiring and
parameters are in the [manual §3](docs/19-node-manual.md#3--裁格--改--贴回) (Chinese).

### 3.5 Face swap / masked local edit

A reference face → **one panel** (or a single image). Four things matter:

| | |
|---|---|
| Give `ref` a **face close-up** | it decides *who*. A full-scene reference cancels anything you add (measured while adding a chair: 14% vs 51%) |
| `init` has two values | `source` (default) takes `image`, and with a mask only the mask is redrawn; `empty` starts from an empty latent — reference + prompt → one new image |
| **White in the mask = repaint** | wire `ImageToMask(red)`; do **not** use `LoadImage`'s `MASK` output (that is alpha, silently all zeros) |
| Auto mask needs insightface | `pip install insightface onnxruntime` + `det_10g.onnx`; a missing dependency **raises**, never silently falls back |

`report` prints the mask coverage, **how much changed inside the mask**, and how much changed outside it —
no extra script needed. **An inside change near 0% is exactly "nothing visibly happened"**: either `ref` and
the source are the same person, or the mask is too small / picked the wrong face.

**What a single image (no `spec`) can and cannot do** — the table worth remembering:

| Want | Works | How |
|---|---|---|
| **face swap** | ✅ | leave `spec` unwired (whole image = one panel), give `ref` a face close-up. Example `moss-faceswap-single-ui.json` |
| **change one region** (clothes / expression / background) | ✅ | wire **the source image itself** into `ref` (`ref` is required), turn `auto_mask` off, wire `ImageToMask(red)`. Measured on clothes: 97% changed inside the mask, 0.01% visible outside; **background does not work** |
| **change the whole body pose** | ❌ no reliable path | in `source` mode the body sits outside the mask and does not move; in `empty` mode the source is **ignored**, so it regenerates from the reference. Masking the whole body is a large-area replacement — the background case (76.8% coverage) failed exactly that way |

> The pose and body-motion measurements (seven / nine tiers) are on the **generate** path:
> reference + prompt → a new image, **not** an edit of an existing photo. To give "the person in this
> photo a different pose" you regenerate, then swap the face onto the result.

⚠ With `spec` wired the source must be the same board, otherwise it raises "source W×H does not match the spec canvas".
Full parameter table, mask geometry, the pose/body measurements and the working scope (clothes work, background does
not): [manual §6](docs/19-node-manual.md#6--换脸--蒙版局部编辑) (Chinese).

### 3.6 Bilingual UI

Every node of ours has a **中 / EN** button on its title bar. Display only — `name`s and values never change.

- **Hard-refresh the browser (F5) after upgrading** — restarting the process is not enough.
- **It runs without the frontend**: if the JS fails to load you lose the button and the editor, nothing else.
- No button? Check the console for `[MOSS SB] i18n: 就绪`.

## 4 · Code / bugs

### 4.1 Before filing a bug

1. The startup log has the `[MOSS SB]` line (the pack loaded).
2. Read `report` — geometry source, effective denoise and mask coverage are all in it.
3. The error text is in the table at [2.5](#25-error-table).

The check list and what must pass before a commit: [`CONTRIBUTING.md`](CONTRIBUTING.md).

### 4.2 Repository layout (the install package ships runtime files only)

```
ComfyUI-MOSS-Storyboard/
├── __init__.py            loader entry (V3 by default, one line to fall back to V1)
├── nodes.py               node definitions (the single source for the parameter tables)
├── core/                  pure-Python kernel: geometry / prompt / pixels / sampling
├── v3/                    V3 schema shell
├── web/                   frontend extension (zh/en switch + layout editor)
├── tests/ tools/          automated checks and dev scripts
├── examples/              7 user examples (test fixtures live in tests/fixtures/)
├── docs/                  mechanisms and measurement records
└── pyproject.toml         metadata + host version requirement
```

### 4.3 Documentation

| Doc | Content |
|---|---|
| [`docs/06`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/06-vae-memory.md) | VAE decode memory: formula, host-version split, per-canvas recomputation |
| [`docs/07`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/07-consistency.md) | cross-panel consistency: three mechanisms, measured matrix, prompt discipline, knob limits |
| [`docs/10`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/10-contract.md) | state v3 frozen contract |
| [`docs/12`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/12-interop.md) | interop: elements-v1 ⇄ state v3, CLI validator |
| [`docs/13`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/13-prompt-enhancer.md) | official prompt-skill draft format, compile rules, untested items |
| [`docs/14`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/14-top-level-design.md) | top-level design: what each of the seven nodes owns, three semantics, prompt transparency and the placeholder guard |
| [`docs/15`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/15-recipe-contract.md) | recipe contract: what a verified recipe must contain |
| [`docs/16`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/16-faceswap-refactor.md) | face swap, before and after: what changed and what deliberately did not |
| [`docs/17`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/17-faceswap-recipe.md) | face-swap recipe: which path actually worked, how the mask is computed, pass criteria |
| [`docs/18`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/18-masked-latent-edit.md) | masked latent edit: what the official format does *not* do, the four orthogonal conditions, where it stops |
| [`docs/19`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/19-node-manual.md) | **node manual**: every input / output / caliber for all seven nodes, the glossary, and the measured limits |
| [`CHANGES.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/CHANGES.md) | release history |

`docs/`, `CHANGES.md` and `RELEASE-NOTES.md` are not shipped in the install package (see `.comfyignore`); the links
above point to the GitHub repo.

### 4.4 License

- Pack code: **MIT**.
- Credits, ideas and provenance: [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) — **no third-party code was copied**.
- Model weights (MOSS-Image / Qwen3.5 / FLUX.2 VAE) are not distributed; follow their own licences.
- The host ComfyUI is GPL-3.0 and is only `import`ed at runtime, never copied.
