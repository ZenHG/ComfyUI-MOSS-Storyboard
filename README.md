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
they are part of those two recipes ([3.8](#38-face-swap--masked-local-edit)).
Without the LoRA use **50 steps / cfg 3 / euler / sgm_uniform** (MOSS-Image-SFT model card);
with it set `steps` to **4** and `cfg` to **1.0** (un-bypass with `Ctrl+B` first if it ships bypassed).

> ⚠ **Do not mix the two bands.** A distilled LoRA at 50 steps / cfg 3, or no LoRA at 4 steps / cfg 1.0,
> raises no error — it just degrades (sometimes to a fully black image). See [2.3](#23-recipes-two-bands-do-not-mix).

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

**Seven examples, one job each**

> ⚠ **The face-swap and pose examples reference two input images** (`moss_faceswap_src.png` /
> `moss_faceswap_ref.png`) and **the repo does not ship them**. Put your own face close-up and board into
> `<ComfyUI>/input/`, or point `LoadImage` at your files.

| File | What it does | Model | Third-party nodes |
|---|---|---|---|
| `moss-layout-editor-storyboard.json` | 4-panel storyboard with in-graph notes (**the starting point**) | ✅ | — |
| `moss-storyboard-4panels-ui.json` | 4-panel storyboard, plainest wiring | ✅ | — |
| `moss-layout-editor-poster.json` | single poster, boxes on the editor canvas | ✅ | — |
| `moss-prompt-compiler.json` | official draft → prompt + editor state | ❌ no VRAM | — |
| `moss-faceswap-ui.json` | 4-panel board → swap panel 0 → board out | ✅ | needs DMD2 + edit LoRA |
| `moss-pose-generate-ui.json` | reference face + prompt → one posed image (`init=empty`) | ✅ | none |
| `moss-storyboard-4panels-edit.json` | crop one panel → instruction edit → paste back | ✅ | needs edit LoRA |

> ⚠ **Four of the seven examples carry the host's `LazyCache` node** (`editor-storyboard` / `4panels-ui` /
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

(`spec` and `init` are explained in [3.2](#32-spec-is-the-single-source-of-geometry) / [3.3](#33-generate);
here you only need to know the two knobs exist.)

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

#### Verified

**Every row states its LoRA and step configuration.**

| Item | Config | Result | Sample |
|---|---|---|---|
| 3/4/6/8/9 panels · landscape/portrait · no gutter · thick gutter · small canvas · `plain` | base + 4-step LoRA / **4 steps** | sizes match the canvas formula exactly | 16 cases (the end-to-end matrix) |
| Grid lines | same | pure white after redraw | per case |
| Random seed | same | different seed → different md5 | 2 controls |
| **Element boxes steer composition** | base + 4-step LoRA / **4 steps**; base + 8-step LoRA / **8 steps** | boxes only changed: the bicycle went from cropped out to fully visible, lower-left. Reproduced on 3 seeds | 36 images |
| **Four Chinese characters** (1024×576) | base + 8-step LoRA / **8 steps** | all correct | 2 seeds |
| **Four Chinese characters** (2048×1152) | base + 4-step LoRA / **4 steps** | all correct, softer strokes | 2 seeds |
| **Four Chinese characters** (2048×1152) | base + 8-step LoRA / **8 steps** | all correct, sharp strokes | 2 seeds |
| **Mixed zh/en · two lines · explicit colour** | base + 8-step LoRA / **8 steps** | all correct; line break honoured; colour applied | 2 seeds each |

> **Base model only, no LoRA**: one control run at 2048×1152 / 30 steps / cfg 3.5 (before the defaults were aligned
> with the official template) produced a normal image, but its text was **not checked character by character**,
> so it is not a result. Verify it yourself.

#### Text: what works, what does not

| Item | Result | Config |
|---|---|---|
| Resolution | 1024×576 is enough; 2048 only sharpens strokes | 8-step LoRA / 8 steps |
| Steps | 4 steps: correct but soft. 8 steps: visibly sharper. 16 steps: close to 8, diminishing returns | matching LoRA |
| Glyph size | still correct with the box at 1/20 of canvas height (≈43 px at 1536×864) | 1536×864 / 2048×1152 |
| Line breaks | `\n` inside `text` is honoured; two lines form separately | 2048×1152 / 8 steps |
| Colour | `palette: ["#d92b2b"]` takes effect | 2048×1152 / 8 steps |
| **Box height does not control size** | a 43 px box produced ≈130 px glyphs — **text overflows its box**. ⚠ We cannot give a "how wide must the box be" threshold: two text boxes with **identical** width, height and character count (169×60, 4 characters) came out one sharp, one faint — **width is not the predictor**. The only certain difference was **distance from the top edge** (47% vs 6%, the latter clipped) | — |
| **Edge-adjacent boxes get clipped** | box top at 5% → glyphs overflowed and the canvas cut them, both seeds | 2048×1152 / 8 steps |

> Sample size is small (2 seeds per item). "Four characters correct" repeated across several configs, but it is
> **not a guarantee**. Clipping was only reproduced in the red-colour group — **other colours were not tested**,
> so keep every text box away from the edge. Presets keep them at 10%, but **10% is not verified as safe** either;
> it is only a more conservative starting point than 5%.

#### Not possible in this version

- **The Generate node has no image input, so it does no img2img.** The latent always starts empty; keep `denoise`
  at 1.0. Template-latent initialisation was measured and does not work: 35 steps / denoise 0.85 → fully black;
  12 steps / 0.6 and 4 steps / 0.5 → template ghosting.
  **To change an existing image**, use [3.5 crop → edit → paste](#35-crop--edit--paste), or the
  [3.8 face swap node](#38-face-swap--masked-local-edit) (it is masked img2img: only the mask is redrawn).
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

| Node (English UI) | Type | Required? | Role |
|---|---|---|---|
| 🎬 Storyboard · Generate | `MossStoryboardSampler` | **Yes** (storyboards) | model + clip + vae + prompt → grid + panels. Computes geometry from its own parameters when `spec` is absent |
| 🎬 Storyboard · Grid Layout | `MossStoryboardLayout` | Optional (recommended) | geometry + `spec` + layout JSON + template image. **No model loaded** |
| 🎬 Storyboard · Split Panels | `MossStoryboardCrop` | Optional | crops only; with `spec` it cuts exactly where the board was generated |
| 🎬 Storyboard · Paste Panel | `MossStoryboardPaste` | Optional | pastes a corrected panel back at the `spec` coordinates |
| 🎬 Layout Editor | `MossLayoutEditor` | Optional (posters / free layouts) | canvas + elements → layout prompt + sampling size. **No model loaded** |
| 🎬 Prompt · Draft Compiler | `MossPromptCompiler` | Optional (official skill bridge) | official draft → MOSS prompt + editor state. **No model loaded** |
| 🎬 Storyboard · Face Swap | `MossStoryboardFaceSwap` | Optional | reference face → one panel (or a single image). **Loads a model** |

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

### 3.3 Generate

`MossStoryboardSampler`, seven outputs: `grid` `panels` `latent` `spec` `prompt_used` `report` `prompts_json`.

- **`prompt_used`** — the script you typed. In **whole-canvas** mode it is the compiled prompt (same content as
  `prompts_json`, one as plain text and one as a JSON array); in **per-panel** mode it stays your original script
  while the model sees the N rewritten per-panel texts.
- **`prompts_json`** — **the text actually handed to the model**, as a JSON array: one entry in `整张` mode,
  one per panel in `逐格` mode. Use it to audit "did the words produce the picture".
- **`latent`** — `整张` = the full-canvas latent; `逐格` = a batch of N single-panel latents
  (`{"samples": [N,C,h,w]}`, row-major = panels 1..N, **not** a spatial stitch). Wire `VAEDecode` to decode the batch.

Factory defaults below. The shipped examples use **4 panels / panel edge 496** and the
50 steps / cfg 3 / euler / sgm_uniform recipe.

| Parameter | Default | Notes |
|---|---|---|
| Positive prompt `positive` | empty | see [2.1](#21-prompt-rules) |
| Generation mode `mode` | `整张` | `整张` = one pass; `逐格` = per-panel sampling then assembly, see below |
| Panel count `panels` | 8 | used when `spec` is absent |
| Panel aspect `panel_aspect` | `16:9` | used when `spec` is absent (10 ratios: `16:9` `3:2` `4:3` `1:1` `4:5` `3:4` `2:3` `9:16` `21:9` `2.39:1`) |
| Panel long edge `panel_edge` | 512 | used when `spec` is absent |
| Canvas aspect `canvas_aspect` | `auto` | used when `spec` is absent; picks the closest layout |
| Gutter / margin `gutter` / `margin` | 8 / 8 | used when `spec` is absent |
| Steps `steps` | 50 | model card value. With the 4-step distilled LoRA set **4–8**, see [2.3](#23-recipes-two-bands-do-not-mix) |
| CFG `cfg` | 3.0 | model card value (official Base range 3–6) |
| Sampler / scheduler `sampler_name` / `scheduler` | `euler` / `sgm_uniform` | see [2.3](#23-recipes-two-bands-do-not-mix) |
| Prompt handling `init` | `auto` | `auto` / `layout` = panel captions become layout boxes; `plain` = no layout boxes. ⚠ Face Swap also has an `init`, but with `source` / `empty` — **same name, different meaning** |
| Box type `label` | `object` | advanced. `object` / `character` / `text`, same as Grid Layout |
| Denoise `denoise` | 1.0 | **keep 1.0** — no image input; lowering it only truncates sigma |
| Redraw grid lines `reborder` | on | redraws the lines from coordinates after decode; off means hoping the model draws them straight |
| Border width `border_px` | 0 | advanced. 0 = same as the gutter (fills the whole line) |
| Crop inset `shave` | 0 | advanced. pixels trimmed from each side when emitting `panels`. The lines are already redrawn inside the gutter, so 0 is normal |
| Sample edge `sample_edge` | 1024 | per-panel mode only |
| Negative prompt `negative` | empty | in the **Advanced** fold. Only **exactly** `cfg = 1.0` skips the negative branch; 1.01 already evaluates it |
| Random seed `seed` | 0 | 0–4294967295. **Same seed plus same settings** is the only way to reproduce an image; a new seed changes composition and how closely the boxes are followed |

> Why `negative` is folded away: it has a default (empty). Folding it under **Advanced** only stops it from looking
> required — it does **not** mean it has no effect. **Your own negative prompt works** — the default `cfg = 3.0` is above 1,
> so anything you type takes effect.

**`mode`: whole canvas vs per panel**

> ⚠ Two similar terms, opposite meanings. **Single mode** = the Layout Editor's one-image, no-panel layout (posters).
> **`整张`** = this node's whole-canvas option: one canvas split into N panels, generated in one pass.

| Option | What it does | Pick it when |
|---|---|---|
| `整张` | **default.** one sampling pass for the board: faster, and the same face is more stable | you want the board in one go, or many panels (8/9) |
| `逐格` | every panel is **sampled on its own**, then assembled by coordinates: slower (one pass per panel) | you need to **rework one panel**, or want more pixels per panel |

- **Same face → whole canvas wins.** ArcFace cosine: whole canvas `0.553` > per panel `0.479`
  (one scene, one seed; the same ruler, so only the relative order means anything).
  ⚠ That ruler **failed calibration in this domain**: 18 of 52 positive pairs had no detectable face (35%),
  so **we report the number, we do not use it as a pass/fail gate, and we set no threshold**. Absolute cut-offs
  do not transfer between domains either (different people produced by this model already score around `0.52`).
- **Per-panel detail / rework → per panel wins.** Each panel gets its own pass, more pixels each, and one panel
  can be redone alone.
- **Other proxy metrics change direction with the scene** (e.g. the **variance** of the red hat's per-panel
  appearance ratio favours per-panel in the two-person scene and whole-canvas in the single-person scene), so they
  are not used to recommend a route. Readings live in [`docs/07`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/07-consistency.md).
- **Caliber**: the numbers above come from the current **50 steps / cfg 3.0** run
  ([`docs/data/g2-50-3.json`](docs/data/g2-50-3.json), 2026-10-27). The older 8-step / cfg 1.0 / distilled-LoRA
  set is **not this caliber and cannot be compared across** — kept as history in `docs/07` §2.3.
- **For whole-canvas mode the global caption must declare the grid** — see
  [`docs/15`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/15-recipe-contract.md).

**`sample_edge` (per-panel sampling long edge)**

`1024` samples each panel at a 1024-pixel long edge, then downscales it into its final cell. It is an **absolute
pixel count**, not a multiplier — keeping the quality knob independent of the layout knob. VRAM and time scale with
the pixel count. Ignored in whole-canvas mode.

> One local scan, 4 panels / 16:9: `496` came out soft, `992` sharper, `1984` looser (composition drifts apart).
> So bigger is not better, and `1024` is only a safe starting point — ⚠ the scan **did not include `1024`** and the
> sample size is not recorded, so the factory value is a **starting point, not a measured optimum**.
> `docs/07` records "1024–1408 sharp but tone drifts", also only a direction.

> ⚠ **Per-panel resolution is where quality usually breaks first.** MOSS trains at 1–2 MP, while a 1:1 nine-panel
> board gives each panel only **0.26 MP** — the canvas looks fine, every panel is visibly soft. `report` prints both
> numbers. With many panels and a near-square ratio, try `逐格`.
> In `逐格` that warning reads the **per-panel sampling canvas** (`sample_edge`) instead of the final cell size.

### 3.4 Grid Layout

`MossStoryboardLayout`, no model loaded. Nine outputs: `width` `height` `columns` `rows` `spec` `layout_json`
`template` `report` `elements`.

| Input | Notes |
|---|---|
| `panels` | 1–12 (3 / 4 / 6 / 8 / 9 are the usual ones; other values are factored automatically) |
| `panel_aspect` / `panel_edge` | panel ratio (10 options) / long edge in pixels, default 512 |
| `canvas_aspect` | target canvas ratio, default `auto` (16:9 or 9:16, following the panel orientation). With 6 panels, `9:16` gives 2 columns × 3 rows and `16:9` gives 3 × 2 |
| `columns` / `rows` | leave 0 for automatic; filling them forces the layout, and both together must satisfy columns × rows = panel count |
| `gutter` / `margin` | grid-line width / distance from the canvas edge to the first panel |
| `cell_gray` / `invert` | advanced: template cell grey (default 25; near-black is safest) / inverted template. Affects `template` only, never the render |
| `label` / `char_boxes` | advanced: box type (`object` / `character` / `text`); `char_boxes` names the character panels, comma-separated, **1-based** |
| `prompt` | **leaving it empty is correct** — this node only computes geometry; filling it only affects `layout_json` and `elements` |

| Output | Wire to |
|---|---|
| `width` / `height` | `EmptyFlux2LatentImage` |
| `columns` / `rows` | Split Panels / Paste Panel same-name inputs (so they need no `spec`) |
| `template` | a picture of flat cells + gutters for eyeballing geometry. **Never enters the sampler** |
| `spec` | downstream geometry |
| `layout_json` | same format as the host's native `MOSS-Image Layout Prompt` (`summary` + `items`; absolute pixels from the canvas centre). Wire to `CLIPTextEncode` |
| `elements` | canvas **element** format (`{type, desc, bbox}`, bbox **normalized 0–1000**, `[y0,x0,y1,x1]`) |
| `report` | one-line summary: panels, canvas, MP, caption alignment, warnings |

**`elements` into the host's `Create Bounding Boxes` needs all three wires** — missing one silently corrupts every box:

```
width    → Create Bounding Boxes · width
height   → Create Bounding Boxes · height
elements → Create Bounding Boxes · bboxes
```

Why: the bbox is normalized 0–1000 and the canvas node needs `width` / `height` to convert it to pixels. That canvas
defaults to 1024×1024; an 8-panel strip is 2032×576, so leaving them unwired misplaces every box without an error.

⚠ **`elements` is a one-way output.** Descriptions edited in the canvas node **do not come back** — the Sampler has
no `elements` input. Its purpose is to let you see the cells and edit descriptions for humans; sampling geometry
still comes from `spec` and the `[PANEL n]` blocks.
⚠ **The two formats are not interchangeable**: `elements` is canvas elements (0–1000), `layout_json` is the full
layout JSON (absolute pixels).
⚠ The grid's `elements` **describes panel slots only**: one `{"type":"obj"}` per cell with that cell's caption as
`desc`; no text elements, no font size, no colour. For in-image text use the editor's text elements or the Draft
Compiler.

### 3.5 Crop → edit → paste

**Split Panels (`MossStoryboardCrop`)** — only `image` is required.

| Input | Default | Notes |
|---|---|---|
| `image` | — | **required.** the full board |
| `spec` | empty | from Generate or Grid Layout ⇒ crops land exactly where the board was generated. Without it you must fill `columns` / `rows` |
| `columns` / `rows` | 0 / 0 | **required without `spec`**; 0 raises instead of guessing |
| `index` | -1 | `-1` outputs every panel; a 0-based number outputs that one panel and emits it on the `index` output |
| `shave` | 0 | pixels trimmed from each side of a panel, to eat an overflowing gutter. Use the same value in Paste |
| `resize_edge` | 0 | `0` keeps the original size (recommended); `>0` scales each panel's long edge |
| `target_aspect` | `keep` | keeps the panel's own ratio (no resampling); a specific ratio centre-crops |
| `padding_mode` | `crop` | `crop` trims the excess; `pad` adds black bars (loses nothing) |
| `show_guides` | off | draws red crop lines on `preview` for eyeballing |

Outputs: `panels` `preview` `report` `index`.

**Paste Panel (`MossStoryboardPaste`)** — both `grid` and `panel` are required.

| Input | Default | Notes |
|---|---|---|
| `grid` | — | **required.** the full board (Generate's `grid`, or the previous Paste's output) |
| `panel` | — | **required.** the corrected panel |
| `index` | 0 | which panel to write, **0-based, row-major**. Wiring Crop's `index` output overrides it |
| `shave` | 0 | **must match the value used when cropping** |
| `spec` | empty | exact placement needs it; without it, fill `columns` / `rows` |
| `show_guides` | off | frames the pasted region in pure red on `marked` |

Outputs: `grid` (the corrected board), `marked` (debug), `report`.

- **Only the target panel's content area is touched**: gutters, margins and every other panel stay identical.
- **A `shave` mismatch triggers bicubic resampling** (content scaled, border gained or lost) — not a shift.
  `report` states the resample explicitly.
- **For a no-resample round trip**: Crop with `resize_edge=0`, `target_aspect=keep`, and the same `shave`.
- **To fix several panels, chain Paste nodes**: the first `grid` output feeds the next `grid` input.

**Recommended wiring** (no manual panel numbers anywhere):

```
Crop (index set) ─panel─→ your edit chain ─→ Paste
Crop ──index───────────────────────────────→ Paste
Generate ──grid────────────────────────────→ Paste
Generate ──spec────────────────────────────→ Paste / Crop
```

#### How to build "your edit chain" (optional — this pack needs no LoRA at all)

This pack only does geometry: cut a panel out, paste it back. **What you edit with is up to you** — another node pack,
external retouching, or hand-editing the file and reloading it.

For MOSS's own instruction editing, the host's official nodes are enough — no third-party node pack required:

```
Crop (index) ──→ Text Encode MOSS-Image Edit ──→ KSampler ──→ VAEDecode ──→ Paste
                     ↑ needs the LoRA below
```

1. Download the community edit LoRA **Moss-Image-Edit** (~285 MB, bf16, rank 64) — either source, same files:
   [Hugging Face](https://huggingface.co/chinoll/Moss-Image-Edit) ·
   [ModelScope](https://www.modelscope.cn/models/ChenkinLab/Moss-Image-Edit).
2. Put `adapter_model.safetensors` and `adapter_config.json` into **`models/loras/Moss-Image-Edit/`**
   (create the subdirectory, both files together).
3. Use the host's **`Text Encode MOSS-Image Edit`**: `clip` → your CLIPLoader (`type=moss_image`);
   `images` → the panel you cut out; `edit_format` → `Moss-Image-Edit`;
   **`vae` may be left unwired** — then the reference only reaches the text encoder, which costs less VRAM and does
   not force the reference's spatial structure onto the result. Wiring it adds reference latents: stronger
   composition retention, more memory.
4. Follow it with a plain `KSampler` → `VAEDecode` → Paste. This path is driven by a community LoRA, so the official
   base defaults do not apply: **30 steps / cfg 5** (the edit LoRA's own recipe, same regime as the edit band in
   [3.8](#38-face-swap--masked-local-edit)); with the 4-step distilled LoRA stacked, **8 steps / cfg 1.0**.

**What it can and cannot do** (measured): colour, outfit, lighting and style instructions are followed well.
**Season changes were not tested — no promise.** With `vae` unwired, composition retention is weaker
(**mechanism, not measured**). **"Re-shoot from another angle" is a documented weak spot** — that still belongs to
the layout-prompt route.

⚠ **It is not a cross-panel consistency solution.** Its job is **single-panel touch-up**: fix one panel's colour,
outfit or light, paste back. Using it to pull one panel toward the others makes every panel converge on the same
image (shot sizes flatten), while a storyboard needs "same character, different shot sizes".

> **Licensing and scope**: that LoRA is third-party community weights, **not distributed with this pack** and not
> maintained by it. Its model card marks the code portion as GPLv3; the weight licence is whatever the card states.
> This pack only links to it — it does not copy, modify or redistribute any weight file. This pack itself (MIT)
> does not depend on it: storyboard generation and Paste work fully without downloading anything.

### 3.6 Layout Editor

`MossLayoutEditor`, no model loaded. **Start here for posters, covers, product shots, or any free-form layout.**
The node is a canvas: place boxes on the left, fill in descriptions on the right. A box is a hint
([1.6](#16-boxes-are-a-hint-what-they-do-change-and-what-they-do-not)).

Seven outputs: `prompt` `width` `height` `spec` `template` `report` `negative`.

| Mode | Presets | Output | Wire to |
|---|---|---|---|
| **Single** | split vertical / split horizontal / title + subject + body / three columns / centered subject / stacked / free | one multi-element layout JSON, **one sampling pass** | `CLIPTextEncode.text`, then a plain `KSampler` |
| **Grid** | grid | `[STYLE]` + `[PANEL n]` | Generate's `positive` + `spec` |

> In single mode the `prompt` output is **already layout JSON** — feed it straight to `CLIPTextEncode`.
> Wiring it into Generate also works: `spec` carries **1 panel**, and the prompt is sent verbatim instead of being
> parsed as caption blocks. `width` / `height` go to `EmptyFlux2LatentImage`.
> **For one non-grid image**: use single mode, or set `panels = 1` on Generate (`init=plain` only changes how the
> prompt is sent — it does **not** remove the grid geometry).
> `negative` is non-empty only in single mode with *Blend into one* on (a few anti-collage words); grid mode always
> returns an empty string.

| Input | Notes |
|---|---|
| `editor_state` | **do not type here** — the canvas save file, updated as you edit. Hand-editing works but must be valid v3 JSON; a bad field is rejected and named |
| `prompt` | optional, wired. Takes over the **text**: global description plus each panel's caption and shot size |

**When an external prompt is wired**

- Panels it covers lose the editor caption (including preset copy) and the caption field becomes read-only;
  unplug to edit by hand again.
- Fewer segments than panels: the remaining panels keep the editor caption. More segments: extras are dropped.
  No `[PANEL n]` at all: only the global description changes.
- A segment without a shot size does not inject one for that panel.
- **Canvas, panel count and panel coordinates always come from the editor** — the wired script cannot change geometry.
  Single mode has no panels, so it only takes the global description.

**Canvas controls**

| Action | Result |
|---|---|
| drag on empty space | draw a new box (a new element) |
| drag a box / its corners | move / resize |
| click a box | edit type (object / character / text), caption, coordinates on the right; grid mode shows **shot size + caption** only |
| grid shot-size dropdown | eight presets; Custom… for your own framing; Unspecified sends the caption alone. **The UI shows Chinese, the prompt emits an English framing clause** (MOSS is trained mostly on English) |
| Final prompt box under the canvas | paste an external storyboard script and hit **Apply** to parse `[PANEL n]` back into panels; **Revert** restores the compiled text. The wheel scrolls the text instead of zooming the graph |
| drag near another box's edge, the canvas centre line, or the rule-of-thirds lines | snaps |
| `Delete` / arrow keys / `Ctrl+D` | delete / nudge / duplicate |
| `Ctrl+Z` / `Ctrl+Shift+Z` (or `Ctrl+Y`) | undo / redo |
| Import / Export | import elements-v1 (0–1000 bbox) or state v3; export elements-v1 (a grid cannot be exported) |
| Raise / Lower + 6-way align | change stacking (array order = z) or align to edges / centre; size is kept |
| Queue | queue the current workflow; changes no values |

**The Final prompt strip is the text actually sent** (`[STYLE]` + `[PANEL n]` per panel in grid mode, native layout
JSON in single mode). Read-only, copyable, updates as you edit, and compiled with the same rules as the backend
(a differential test compares them byte for byte) — it is not a paraphrase. If an upstream node computes its text at
runtime, the strip says so instead of pretending old content is final.

**Preset placeholders get flagged.** Queueing with preset copy such as `标题文字` ("Title") or `第 3 格画面`
("Panel 3") renders those words letter by letter and produces a board of near-identical panels. The status bar counts
them, the Queue button asks once, and `report` says the same for API-only users.

**Text elements have two fields — do not mix them**

| Field | What goes in | Example |
|---|---|---|
| **caption** | what the text **looks like** | bold sans-serif title, pale gold, slight outline |
| **text** | the **literal characters to render** | LEGEND |

Swap them and the model tries to draw the words "bold pale gold". Only `text` is rendered verbatim.

> ⚠ **Keep text boxes away from the canvas edge.** Measured: with the box top at 5% of canvas height the glyphs
> overflow upward and get clipped. Presets use 10%, but that is only more conservative than 5% —
> **10% is not verified as safe**.

> An external planner's element JSON imports directly as editable items; the CLI validator / converter is in
> [`docs/12`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/12-interop.md) (no ComfyUI needed).

### 3.7 Draft Compiler

`MossPromptCompiler`, no model loaded. Bridges OpenMOSS's
[MOSS-Image Prompt Enhancer skill](https://github.com/OpenMOSS/MOSS-Image/tree/main/skills/moss-image-prompt-enhancer)
to this pack. Paste the skill's **draft** (not its compiled output).

**Input**: one multiline box, `draft`. A **caption** draft (`{"caption": "…"}`) compiles to plain text; a **layout**
draft compiles to one-line native layout JSON. An empty draft raises instead of emitting an empty prompt.

| Output (in order) | Content |
|---|---|
| `prompt` | a ready-to-use MOSS prompt (plain caption, or one-line native layout JSON) |
| `report` | type, canvas, item counts, and any field that could not be represented in state |
| `state` | for layout drafts, a state v3 JSON to paste into the Layout Editor's import box |

> The three sockets are wired **by position** — check the order before dragging. The canvas must be 64–4096 px;
> outside that, `prompt` still compiles and `report` explains why `state` was skipped.

Draft requirements (the official schema): `width` / `height` positive multiples of 16; `bbox_coord_space="abs"`;
`bbox_format="xyxy"`; `summary.caption` and a non-empty `items` array. Each item is `object` / `character` / `text`;
text items must carry the literal `text`. Unknown fields, duplicate keys, NaN/Infinity and out-of-bounds boxes are
rejected. The pack adds a **256-item** cap and a 1 MB draft cap.

The skill itself is external Apache-2.0 content and is **not bundled**; the compiler is an independent implementation
of the documented schema. Details: [`docs/13`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/13-prompt-enhancer.md).

### 3.8 Face swap / masked local edit

`MossStoryboardFaceSwap`. Reference face → **one panel** (or a single image). MIT, like the rest of the pack.

| Parameter | Role |
|---|---|
| `ref` → vision channel | decides **who**. `vision_megapixels` defaults to `1.05` (at the host default `0.147` the reference scores about `0.008`, i.e. nothing) |
| `image` + `prompt` → img2img | decides **what is drawn**. The prompt must mention the reference (`in Picture 1`); the node adds the prefix |
| `init` | **`source`** (default) = img2img from `image`, with a mask only the mask is redrawn. **`empty`** = ignore `image`, start from an empty latent — reference face + prompt → one new image |
| `spec` + `index` | only that panel changes — gutters, margins and the other panels stay pixel-identical (measured `max&#124;Δ&#124; = 0`) |
| `auto_mask` | **on by default.** insightface finds the face and builds the mask. A missing dependency is a hard error, never a silent fallback |
| `face_pick` | `largest` / `left` / `right`. For panels with several people; `report` prints how many faces were found and which one won |
| `mask` | optional manual override. A wired mask beats `auto_mask` |

**Why this is not just "img2img".** The official MOSS edit format is **in-context**: the reference image rides along
as clean tokens at `t = 0` and `x` starts from pure noise, so composition is only *asked for* in the prompt. This node
lets the source image be the **starting point** instead (`vae.encode` + `denoise`), so composition is kept
*mechanically*. Four conditions, each doing one job: prompt = what changes, `ref` = who, `image` = what is kept,
`mask` = where. Background: [`docs/18`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/18-masked-latent-edit.md).

**The mask is the difference between a retouch and a re-render.** With a mask only the mask is redrawn; outside it
there is nothing but decode rounding (`mean&#124;Δ&#124; 0.40/255`, visible change <0.01%). Without one — no wired
mask **and** `auto_mask` off — the whole panel is repainted and the grade always shifts (`R +24 … +53`). `report`
says which path ran and prints the coverage and the outside-mask change.

**What the mask is.** A feathered **rectangle**, not a face outline: top edge at `eye_y + 0.30 × (nose_y − eye_y)`,
bottom at `y2 + 0.06 × h`, sides at `±0.05 × w` around the detected box, blur `max(3, 0.02 × w)`.
**White = repaint.** A board-sized mask is fine — it is cropped to `index` with the same integers used for the image.

**Wiring your own mask.** `ImageToMask(channel="red")` → `mask`. Do **not** use `LoadImage`'s `MASK` output: that is
the alpha channel, and a grey PNG without alpha comes back as 64×64 zeros — the mask silently does nothing and the
panel gets fully repainted, with no error. Sample: `assets/faceswap-mask-example.png`.

**Auto mask — what to install, where the weights go**

| | |
|---|---|
| Install | `pip install insightface onnxruntime` — into the **same** Python that runs ComfyUI |
| Weights | `~/.insightface/models/buffalo_l/det_10g.onnx` (19 MB) |
| Only this one | the node loads `detection` only, so `w600k_r50.onnx` (174 MB, recognition) is **not** needed |
| Another drive | set `MOSS_INSIGHTFACE_ROOT=<root>` → weights go to `<root>/models/buffalo_l/` |
| Download | <https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip> (unzip `buffalo_l/` into `models/`) |
| Check | the node **never downloads anything by itself**: missing weights raise an error printing the exact path it looked at |

Other detectors on this machine, and why this node does not use them:

| Wheel | Gives you | Weights live in | Used here |
|---|---|---|---|
| insightface `buffalo_l` | box + 5 keypoints | `~/.insightface/models/buffalo_l/` | **yes** — same source as the measured recipe |
| mediapipe face mesh | 478-point outline | ships with the package, `site-packages/mediapipe/modules/` (55 MB) | no — a tighter outline is not the bottleneck |
| facexlib BiSeNet | per-region parse (skin/brow/eye/nose/mouth/hair) | `site-packages/facexlib/weights/parsing_bisenet.pth` | no — best semantics, but a second dependency for an unmeasured gain |
| SAM | precise region, needs a point or box prompt | manual `sam_vit_*.pth` download | no — prompt + weight cost, unmeasured gain |
| ultralytics YOLO | box only | manual download | no — no keypoints, would have to re-derive them |

**Pose and body motion both work — set `init` to `empty`.** Seven head poses and nine body actions, measured on the
**generate** path (empty latent + reference in the vision channel). With the default `init=source` the body sits
outside the mask and the prompt cannot move it.

| Tested | Result |
|---|---|
| Head yaw, **frontal** reference | works. One prompt line moves yaw `+0.05 → +1.48`; near-profile, chin-down and chin-up all came out |
| Head yaw, **profile** reference | fails. Reference `yaw +5.20`; all seven prompts came back as profiles ⇒ **pose follows the reference image, not the prompt** |
| Body actions (stand / pockets / arms crossed / walk / reach / sit / torso turn / hand to chin / back-and-look) | all nine produced. Net identity loss `−0.086 … +0.112`, **7 of 9 within ±0.05** of the sampling floor |
| Torso turned, head still at camera | `+0.036` — no damage |
| Back to camera, head over shoulder (`yaw 0.76`) | `−0.086` ⇒ what hurts the face is the **head turn**, not the body |

⇒ **To change pose: feed a frontal reference, set `init=empty`, and put the pose in the prompt.** A profile reference
locks the output to profile and no wording pulls it back. When only the body should move, keep the head facing the
camera. Prompts and pass criteria for both gradients:
[`docs/17`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/17-faceswap-recipe.md) §9.
Working example: `examples/moss-pose-generate-ui.json` (10 nodes, `init=empty`, **zero third-party nodes**).

**Two parameter regimes** (same image, same mask):

| Regime | steps / cfg | Use for |
|---|---|---|
| Fast (default) | 8 / 1.0 | face swap — at face scale the material barely matters |
| Edit | **30 / 5** | clothing, hair, larger areas — the edit LoRA's own recipe; visibly better texture |
| Edit + HQ LoRA | 30 / **3** | `MOSS-Image-Edit-HQ/armV5` (rank 64/192, 495 MB). Best coverage and blending in our test |

**`ref` decides "who" — give it a face close-up, not the whole scene.** Measured while adding a chair: a full-scene
reference drew one red stripe (14% of the mask changed); a face close-up produced the chair (51%). A scene reference
also says "nothing else is here", which cancels the new object.

**What still does not work**: two faces in one panel (each ≈68 px, both get swapped and drift apart), chaining two
swaps (the second is a pixel-identical no-op), and pulling a profile reference back to frontal with wording. No
identity score is published — the ruler has no discriminating power in this domain. Knobs tried and why they stay at
their defaults: [`docs/16`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/docs/16-faceswap-refactor.md).

**Scope** (one mid-shot single-person image, masks from BiSeNet parsing):

| Mask | Coverage | Inside change | Outside **visible** change | Verdict |
|---|---|---|---|---|
| hair | 1.3% | 85% | 0.00% | works (limited by mask precision) |
| clothes | 16.4% | 97% | **0.01%** | **works** |
| background | 76.8% | 70% (inside mean only 12) | — | **does not work**: the unmasked person pulls it back |

> The outside-mask criterion is **>32/255**, not >8: VAE decode plus latent blending produces 8–16 differences that
> are invisible. In the clothes run, >8 covered 5.79% while >32 covered 0.01% (p99 = 17). `report` prints both bands.

Example: `examples/moss-faceswap-ui.json`.

### 3.9 Bilingual UI

Each of **our** nodes has a **中 / EN** button on its title bar, switching titles, labels, combo display and tooltips
for all seven. The default follows the host locale.

- **Only our nodes** are touched — never official or third-party nodes.
- **Display only, never the graph**: only `label` / `title` / `tooltip` / `placeholder` change; widget `name`s and
  values never do, so execution and saved values are unaffected. Your own renames survive.
- **Hard-refresh the browser (F5) after upgrading.** Restarting the ComfyUI process is not enough — the frontend is a
  single-page app and keeps running the old JS while the page stays open.
- **It works without the frontend too**: the backend's default readable strings are Chinese. If the JS fails to load
  you lose the switch and the editor, but the nodes still run on their defaults. API submissions (`/prompt`) never
  needed the button.

No button? Check the browser console for `[MOSS SB] i18n: 就绪`; if missing, confirm the `web/` files (especially
`moss_sb_i18n_ui.js`) are served under `/extensions/ComfyUI-MOSS-Storyboard/`.

---

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
| [`CHANGES.md`](https://github.com/ZenHG/ComfyUI-MOSS-Storyboard/blob/main/CHANGES.md) | release history |

`docs/`, `CHANGES.md` and `RELEASE-NOTES.md` are not shipped in the install package (see `.comfyignore`); the links
above point to the GitHub repo.

### 4.4 License

- Pack code: **MIT**.
- Credits, ideas and provenance: [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) — **no third-party code was copied**.
- Model weights (MOSS-Image / Qwen3.5 / FLUX.2 VAE) are not distributed; follow their own licences.
- The host ComfyUI is GPL-3.0 and is only `import`ed at runtime, never copied.
