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

## 七、自动蒙版（默认开）

手画的椭圆只对一张图有效 —— 换图就错位，宫格里还会偏到另一格的人脸上，**而且不报错**，
表现就是「换了另一个人」。所以节点默认用 insightface 自动找脸：

| 入参 | 作用 |
|---|---|
| `auto_mask`（默认开） | 检脸 → 生成蒙版。需要 `pip install insightface onnxruntime`；没装会明确报错 |
| `face_pick` | `largest` / `left` / `right` —— 一格里有好几个人时点名要换哪张 |
| `mask` | 手接蒙版，优先级高于自动 |

蒙版在**裁格之后**算，尺寸与那一格一致。`report` 会写「检测到 N 张脸，用了哪张」。

实测（示例工作流，4 格宫格里第 0 格的两个人）：`face_pick=right` → 改动面积 `3.38%`，
`report` 写「2 张脸，用了最右那张」；不接蒙版时是 `22.8%`（整格重画）。

### 7.1 权重放哪

```
~/.insightface/
└── models/
    └── buffalo_l/
        └── det_10g.onnx          ← 只需要这一个（19 MB）
```

`w600k_r50.onnx`（174 MB，识别用）**不需要**：本节点 `allowed_modules=["detection"]`，
只加载检测器。

**离线部署**（不改用户主目录）：

```bash
# 1) 解压 buffalo_l.zip，把 buffalo_l/ 放到任意目录（下称 <root>）
# 2) 设环境变量（Windows 系统属性 → 环境变量，或启动 ComfyUI 前设置）
#      Windows:  set MOSS_INSIGHTFACE_ROOT=<root>
#      Linux/macOS:  export MOSS_INSIGHTFACE_ROOT=<root>
# 3) 目录结构变成 <root>/models/buffalo_l/det_10g.onnx
```

**自查**（不用起 ComfyUI）：

```bash
python -c "import sys; sys.path.insert(0,'.'); from core import facemask as FM; \
import os; print(FM.weight_path(), os.path.exists(FM.weight_path()))"
```

节点**不会自己联网下载**。`FaceAnalysis` 原本在目录缺失时会去 GitHub 拉 zip；
本节点在调它之前先查文件，缺了就报错并打印**它实际找的那个路径** —— 这样
「权重放错了地方」会立刻暴露，而不是卡在下载或静默降级成整格重画。

### 7.2 其他检测轮子（本机都有，本节点不用）

| 轮子 | 给什么 | 权重位置 | 为什么不用 |
|---|---|---|---|
| insightface `buffalo_l` | 框 + 5 关键点 | `~/.insightface/models/buffalo_l/` | **在用**：与 E26 实测配方同源，且逐像素复现过 |
| mediapipe face mesh | 478 点轮廓 | 随包，`site-packages/mediapipe/modules/`（55 MB） | 轮廓更贴不是瓶颈——矩形+羽化已经够，换轮廓要重测一遍判据 |
| facexlib BiSeNet | 分区解析（皮肤/眉/眼/鼻/嘴/发/耳） | `site-packages/facexlib/weights/parsing_bisenet.pth` | 语义最好，但多一个依赖，增益未测 |
| SAM | 精分割，要提示点/框 | 手动下 `sam_vit_*.pth` | 要提示 + 权重大，增益未测 |
| ultralytics YOLO | 只有框 | 手动下载 | 没关键点，上沿还得自己估 |

## 八、本节点怎么接

```
LoadImage(灰度蒙版，白=重画)  →  ImageToMask(red)  →  换脸.mask
```

- 蒙版给**整张宫格**的也行：节点会按 `spec` 裁到目标格，与图像用同一套整数坐标。
- **不接 mask 且关掉 `auto_mask` = 整格重画**，画风与色调一定变，`report` 会明确写出来。
  默认 `auto_mask` 开着，所以「没接线」不等于「没有蒙版」。
- 示例：`examples/moss-faceswap-ui.json`；蒙版样张：`assets/faceswap-mask-example.png`。

## 九、姿态与肢体动作：配方在这，但**不是本节点的能力**

> 这一节的数（README 里「姿态能动」「肢体动作几乎无损」）来自**生成**路径 ——
> 空 latent + 参考图进 vision 通道 + 提示词。
> **换脸节点复现不了这两条**：它是 i2i + 蒙版，动作来自源图，蒙版外逐像素不动。
> 要在换脸之后改动作，得先走生成路径出带动作的图，再对它换脸。

### 9.1 为什么本包节点复现不了

`MossStoryboardSampler` 的入参里**没有任何 IMAGE / MASK**（只有 `model / clip / vae /
positive / negative / steps / ...`，输出才是 IMAGE）。本包唯一带 vision 通道的节点是换脸，
而它必须吃源图、且默认蒙版只重画脸框 —— 身体在蒙版外，逐像素保留。

⇒ **本包没有「参考图 → 一张带指定动作的图」这个入口。** 这是缺口，不是配置问题。

### 9.2 配方（照抄）

模型栈与 §二 相同，**两处不同**：

| 环节 | 取值 |
|---|---|
| latent | `EmptyFlux2LatentImage`，**1024 × 576**（不是源图编码） |
| 采样 | `denoise 0.92`（空 latent 上这个值不影响结果，照抄即可） |
| 编码 | `TextEncodeMOSSImageEditHiRes`：`edit_format = Moss-Image-Edit (vision)`、`vision_megapixels = 1.05`、`image_strength = 1.0`、`ref_start/end = 0.99 / 1.0`（关 ref_kv）、`images.image_0` = **正脸参考图** |
| 其余 | UNET `moss_image_sft_bf16` · LoRA 先 `8-steps-dmd2-lora` 再 `Moss-Image-Edit\adapter_model` · CLIP `qwen3.5_9b_int8_convrot`（`moss_image`）· VAE `flux2-vae` · 8 步 / cfg 1.0 / `euler` + `simple` |

**姿态（七档，E20）** —— 只改最后一句：

```
An extreme close-up portrait photograph of a person, the face filling most of the frame,
head and shoulders, plain grey studio wall behind, soft key light from the left,
calm neutral expression, <档位句>
```

| 档 | 档位句 |
|---|---|
| p00_front | `looking straight at the camera, head perfectly frontal, both ears visible.` |
| p15_soft | `head turned slightly, a little more of the right cheek visible than the left, eyes still toward the camera.` |
| p30_three | `head turned in a three-quarter view, clearly more of the right cheek visible, the right ear half hidden.` |
| p50_strong | `head turned far in a strong three-quarter view, the right ear is hidden behind the cheek, the left eye near the silhouette edge.` |
| p75_profile | `head turned almost in profile, only a sliver of the far eye visible, the nose breaking the silhouette.` |
| pd_down | `head tilted down, chin lowered toward the chest, eyes looking down.` |
| pu_up | `head tilted up, chin raised, eyes looking up at the ceiling.` |

**肢体动作（九档，E21）** —— 只改最后一句：

```
A medium shot photograph of the person in Picture 1, framed from the knees up,
standing in a plain grey studio, soft key light from the left,
calm neutral expression, <档位句>
```

| 档 | 档位句 |
|---|---|
| a00_still | `standing still with both arms relaxed at the sides, looking at the camera.` |
| a01_pocket | `standing with both hands in the pockets, looking at the camera.` |
| a02_cross | `standing with both arms crossed over the chest, looking at the camera.` |
| a03_walk | `walking forward toward the camera, one leg in front of the other, arms swinging naturally.` |
| a04_reach | `standing and reaching forward with the right hand as if picking something up, the head still facing the camera.` |
| a05_sit | `sitting on a simple wooden stool, hands resting on the knees, looking at the camera.` |
| a06_turn | `standing with the torso turned sideways, but the head still turned toward the camera.` |
| a07_handface | `standing with the right hand raised to the chin, fingers touching the jaw, thinking.` |
| a08_back | `standing with the back to the camera, head turned back over the shoulder.` |

> 左右方向靠「**哪边脸颊可见**」描述，不用 `left` / `right` —— 后者在模型里歧义。

### 9.3 两条复现路径

```text
A. 现成示例（推荐）：examples/moss-pose-generate-ui.json
   pose 生成 → 预览 → 换脸 → 落盘，14 节点；参数已按 9.2 填好。
   ⚠ 需要 ComfyUI-MOSS-RefHiRes（GPL，独立安装）的 TextEncodeMOSSImageEditHiRes。
     宿主自带的 TextEncodeMOSSImageEdit 把参考图硬压到 0.147 MP，身份约 0.008 = 等于没给。

B. 实验脚本（内部实验目录，不随包分发）：e21_body_action.py
   --ref <正脸参考图> --out <输出目录> --repeat 3
   自带采样地板对照（基线动作跑 3 个 seed）

C. 自己搭三节点：TextEncodeMOSSImageEditHiRes + EmptyFlux2LatentImage + KSampler
   参数见 9.2；不需要本包任何节点
```

### 9.4 判据

| 指标 | 通过线 | 为什么 |
|---|---|---|
| 相邻档像素差 | **> 15** | 低于 15 说明画面几乎没动（`a01_pocket` 只有 8.05，「手插兜」不能算动作生效） |
| 脸占宽 | 0.08 – 0.20 | 中景；脸再小检测就不可靠 |
| 净损失 | 对基线余弦 **− 采样地板** | 中景地板 `0.437`、特写 `0.518`；不减地板会把纯噪声判成身份漂移 |
| 姿态读数 | yaw 代理量 | 鼻尖相对两眼中点的水平偏移 / 半眼距；**没有绝对角度**，只有相对 |

**已知产物**（可对照）：`output/moss-chain/_body/_full_male.png`（九档全景）、
`_body/_sheet_male.png`（九档脸框）；姿态七档在 `_pose/`。
