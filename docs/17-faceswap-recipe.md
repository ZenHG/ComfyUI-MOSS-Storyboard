# 换脸配方：哪条路真的成过

> 这一页只回答一件事：**要复现「效果不错」的那个结果，照什么抄。**
> 结论先行：**只有蒙版局部重绘成过；整格重画不成。**

## 一、成功的那条：蒙版局部重绘

**原理**：MOSS 自己没有蒙版通道（`TextEncodeMOSSImageEdit` 的输入里没有 mask，
`[:, :, :, :3]` 还会把 alpha 丢掉），但蒙版是**宿主**的能力 ——
`SetLatentNoiseMask` 之后，`comfy/samplers.py` 每一步都做
`out = out * mask + latent_image * (1 - mask)`，蒙版外逐像素留在原 latent 上。

⇒ 所以「换脸」必须做成**只重画脸框**，而不是「重画这一格」。

## 二、完整配方（可照抄）

| 环节 | 取值 |
|---|---|
| UNET | `moss_image_sft_bf16.safetensors` |
| LoRA 1 | `8-steps-dmd2-lora.safetensors`（先加） |
| LoRA 2 | `Moss-Image-Edit\adapter_model.safetensors`（后加） |
| CLIP | `qwen3.5_9b_int8_convrot.safetensors`，`type = moss_image` |
| VAE | `flux2-vae.safetensors` |
| 编码 | `TextEncodeMOSSImageEditHiRes`：`edit_format = Moss-Image-Edit (vision)`、`vision_megapixels = 1.05`、`image_strength = 1.0`、`ref_start/end = 0.99 / 1.0`（= 关 ref_kv）、**不接 vae** |
| 采样 | 源图 → `VAEEncode` → `SetLatentNoiseMask` → `KSampler`：8 步 / cfg 1.0 / `euler` + `simple` / `denoise 0.95`（有蒙版时给到 1.0 也行） |
| 蒙版 | **白 = 重画**。按关键点定上下沿（见 §四），羽化 `radius = max(3, 0.02 × 脸宽)` |
| 参考图 | 身份参考，**必须不含**你要修掉的东西（E23：戴口罩的图当参考，口罩会被画回来） |

**提示词原文（照抄，重点在最后一句）**

```
A close-up photograph of the same person with a bare face: natural mouth,
lips and chin, bare nose, clear skin, no mask, no covering.
Keep the eyes, the glasses, the hair, the lighting and the background
exactly the same as the source image.
```

> 最后那句 **`Keep the ... lighting and the background exactly the same as the source image`**
> 是必须的。我先前那版提示词没有这句，反而写了 `warm cafe` / `35mm film`，等于主动命令模型换画风。

## 三、两个坑（都踩过）

1. **`LoadImage` 的 `MASK` 输出是 alpha**（源码里 `mask = 1. - alpha`）。灰度 PNG 没有 alpha 时
   它返回 **64×64 全零** ⇒ 蒙版恒等于「全不重画」，`denoise=1.0` 看着也像没遮。
   **必须走 `ImageToMask(channel="red")`。**
2. **蒙版几何不能按 bbox 比例算**。脸框被画幅裁掉时 `y1` 是负数，`y1 + 0.55h` 会落到下巴以下，
   蒙版只盖住半截 —— 不报错，只是结果不对。改用关键点（见下）。

## 四、蒙版怎么算（关键点法）

- 上沿 = 两眼中点 → 鼻尖之间 **30%** 处（`eye_y + 0.30 × (nose_y - eye_y)`）
- 下沿 = `y2 + 0.06 × h`
- 左右 = 脸框各外扩 `0.05 × w`
- 羽化 = `max(3, 0.02 × w)`

## 五、判据：余弦看不见「有没有重画」

只报余弦会误判 —— 蒙版没生效时模型会把原图重画一遍，余弦照样「看着对」。必须同时报**逐像素改动率**：

| 指标 | 通过线 | 含义 |
|---|---|---|
| 蒙版**外** mean&#124;Δ&#124; | **< 1.0**（0–255 尺度） | 画风/色调没被动 |
| 蒙版**内** mean&#124;Δ&#124; | **> 30** | 那块真的重画了 |
| 蒙版内改动率 | > 50% | 低于 50% 判该档无效 |

## 六、实测记录（同一张 4 格宫格、同 seed、同参考图）

| 路径 | 蒙版内 mean&#124;Δ&#124; | 蒙版外 mean&#124;Δ&#124; | 全图 mean RGB 偏移 |
|---|---|---|---|
| 整格重画 `denoise 0.95`（我最初的写法） | — | — | **+53 / −14**（强暖） |
| 整格重画 `denoise 0.85` + 中性提示词 | — | — | +24 / −13 |
| 整格重画 `denoise 0.85` + 最小提示词 | — | — | **+43 / +38 / +37** |
| **蒙版局部重绘（本节点 + mask）** | **42.26** | **0.40** | ≈ 0 |

另一组（E26，灰棚特写、单人）：对本人余弦 `0.497–0.529`，蒙版外改动 `0.018–0.042%`，
蒙版内改动 `57–64%`。四个档位与旧实现逐像素 `max|Δ| = 0`。

## 七、本节点怎么接

```
LoadImage(灰度蒙版，白=重画)  →  ImageToMask(red)  →  换脸.mask
```

- 蒙版给**整张宫格**的也行：节点会按 `spec` 裁到目标格，与图像用同一套整数坐标。
- **不接 mask = 整格重画**，画风与色调一定变，`report` 会明确写出来。
- 示例：`examples/moss-faceswap-ui.json`；蒙版样张：`assets/faceswap-mask-example.png`。
