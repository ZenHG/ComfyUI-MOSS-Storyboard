# ComfyUI-MOSS-Storyboard

> Condensed English version of [README.md](README.md) (Chinese is the source of truth).
> No section-level hash sync gate yet — see `CHANGES.md` §欠账.

Storyboard grid nodes for **MOSS-Image**: 3 / 4 / 6 / 8 / 9 panels in a single sampling pass,
plus per-panel crops. Zero extra pip dependencies. MIT.

## Why a new node instead of reusing the Krea2 storyboard graph

The Krea2 flow is: grid template image → `VAEEncode` → `KSampler(denoise 0.8)` → crop.
The mechanism survives on MOSS, but four wires must change:

| Stage | Krea2 | MOSS |
|---|---|---|
| Text encoder | `CLIPLoader(type=krea2)` | `CLIPLoader(type=moss_image)` + Qwen3.5-9B |
| VAE | `Krea2-HD-vae-bf16` | `flux2-vae` (128ch, 1/16) |
| Canvas | any size | **must be divisible by 16** (silent truncation otherwise) |
| Sampling | 8 steps / cfg 1.1 | 35 steps / cfg 1.0 (verified locally) / denoise 1.0 or 0.85 |

Structural fact: **MOSS-Image has no native img2img / image conditioning.** The template route
is latent-init img2img, which is out-of-distribution for MOSS. So the node offers both routes
and treats the native JSON-bbox layout route as the primary one.

## Three layers, three locks

1. **Semantics — native JSON layout boxes.** `{"summary": …, "items":[{"label","caption","bbox"}]}`
   in `abs_centralized` / `yxyx`, byte-compatible with the host's `MOSS-Image Layout Prompt`.
2. **Geometry — optional grid template.** White gutters survive low-frequency re-drawing; a prior, not a guarantee.
3. **Determinism — post-composite + crop.** After decode, gutters are re-drawn from the same
   `Layout` coordinates and panels are cropped exactly. No model involvement, no silent fallback.

## Nodes (`moss/storyboard`)

- **MOSS Storyboard Layout** — geometry + `layout_json` + `template`, no model. Outputs a `spec`
  string that carries every coordinate downstream in one wire.
- **MOSS Storyboard Sampler** — `model + clip + vae + positive` is enough. `init` =
  `layout` / `template` / `both` / `plain` / `auto`.
- **MOSS Storyboard Crop** — exact crop by `spec`, with a red-line alignment preview.

## Layout table (panel 16:9, edge 512, gutter 8, margin 8)

| Panels | Grid | Canvas | MP |
|---|---|---|---|
| 3 | 3×1 | 528×896 | 0.47 |
| 4 | 2×2 | 1056×608 | 0.64 |
| 6 | 2×3 | 1568×608 | 0.95 |
| 8 | 2×4 | 2096×608 | 1.27 |
| 9 | 3×3 | 1568×896 | 1.40 |

Grid choice is computed (integer factorisation + closest canvas aspect, 4:1 sanity bound),
not hard-coded. `canvas_aspect="9:16"` flips the same counts to vertical.

## Gates

```bash
python tests/test_core.py          # geometry / prompt / pixels, no GPU, 282 checks
python tools/smoke_nodes.py        # real host: V1+V3 registration, schemas, live nodes, 25 checks
python tools/check_ui_workflow.py  # example graph links + widget slots, 24 checks
```

All three must report 0 failures. Do not pipe them through `tail` — the exit code matters.

## Not verified

- Template img2img quality on MOSS (out-of-distribution usage).
- Whether bbox layout reliably dictates per-panel composition. Run a 4-panel sample first.
- English README is a condensed translation without a hash sync gate.

## License

MIT for this package. Model weights are not redistributed. Host ComfyUI is GPL-3.0 and is only
imported at runtime. See `THIRD-PARTY-NOTICES.md`.
