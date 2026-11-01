# tools/workflows · 可提交的 API 工作流（t2i）

这里是**API 格式**的 ComfyUI 图（`POST /prompt` 的 `prompt` 字段），不是前端 UI JSON。
两份脚本共用：`tools/pitfalls_ab.py`（T6 A/B）与 `tools/wash_benchmark.py`（T8 洗图基准）。

## 为什么手抄而不是转前端图

前端图里有 `Fast Groups Bypasser`、`MarkdownNote`、自定义分辨率节点等**纯前端/skipped** 节点，
`tools/ui_to_api.py` 转出来的图不一定只剩一条可跑的链（本仓的 `Moss 文生图.json` 是 7 条并行
实验链）。所以这里从每条真机工作流里抄**最小可跑子图**，节点名与 widget 值逐字来自真机。

## 文件

| 文件 | 后端 | 关键节点 |
|---|---|---|
| `moss_image_t2i.api.json` | MOSS-Image SFT 4steps turbo merged | `UNETLoader` + `CLIPLoader(type=moss_image)` + `EmptyFlux2LatentImage` + `KSampler(4步/cfg1)` |
| `krea2_t2i.api.json` | Krea 2 Turbo HD V1 NVFP4 | `UNETLoader` + `CLIPLoader(type=krea2)` + `EmptySD3LatentImage` + `KSampler(8步/cfg1)` |
| `qwen_image_flash_t2i.api.json` | Qwen-Image-Flash Q4_K_M | `UnetLoaderGGUF` + `ModelSamplingAuraFlow(3)` + `EmptySD3LatentImage` + `KSampler(4步/cfg1)` |

节点 id 固定：`1`=模型加载，`2`=文本编码器，`3`=正向 CLIPTextEncode，`4`=负向
（ConditioningZeroOut 或空 CLIPTextEncode），`5`=KSampler，`6`=空 latent，`7`=VAEDecode，
`8`=VAELoader，`9`=SaveImage，`10`=ModelSamplingAuraFlow（仅 Qwen）。脚本按
`_index.json` 的 `nodes` 映射改 prompt / seed / 尺寸 / 文件名前缀。

## 来源与取舍

| 后端 | 来源（ComfyUI user workflows） | 取舍 |
|---|---|---|
| MOSS-Image | `Moss 文生图.json` nodes 96–104 子图 | 4 步 turbo 档直接来自真机；negative 用 `ConditioningZeroOut(positive)`；未接 SLG/SkipLayerGuidance |
| Krea2 | `⨝Krea2 turbo 生成4_6_8宫格故事板.json`（NVFP4 + type=krea2 + 8 步/cfg 1.1）与 `QuadView_krea2_v1.json`（int4 + EmptySD3LatentImage） | 8 步/cfg 1 是 Turbo 档；latent 取 QuadView 的 16 通道版本（`K2白玉AIO` 用 4 通道 `EmptyLatentImage`，两份真机稿不一致，首次真跑会核） |
| Qwen-Image-Flash | `QWEN IMAGE FLASH.json` | 去掉 `SolAttnPatch` / 自定义分辨率节点（可选加速件）；保留 `UnetLoaderGGUF` + `ModelSamplingAuraFlow(3)`；空负向 + cfg=1 |

**缺失如实记录**：`flux3` / `flux1_dev` 本机**没有生成权重**（`models/diffusion_models` 里只有
metadata 与历史产物），所以不构造假 workflow。`_index.json` 的 `unavailable` 段写明原因，
`tools/wash_benchmark.py` 会把它们记为 `skipped`，不进分数。

## 怎么校验（不占 GPU）

只读 `/object_info`，不提交任务：

```powershell
python tools\pitfalls_ab.py --check-workflows
python tools\wash_benchmark.py --check-workflows
```

校验内容：① 每个 `class_type` 服务端存在；② 必填连线型输入已连、必填 widget 输入有值；
③ 所有连线指向存在的节点与输出槽；④ `_index.json` 里登记的 node id 都存在且类型对得上。
`--dry` 模式下两份脚本还会把要提交的图写到 `out/**/workflows/`，便于先人工看一遍再排队。
