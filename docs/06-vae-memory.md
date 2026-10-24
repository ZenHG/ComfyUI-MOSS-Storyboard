# 06 · VAE 解码显存账（维护者向）

> 为什么同一张画布在不同人的机器上「能跑」和「跑不动」会差一个档位。
> 用户向的说法在 [README「显存要多少」](../README.md#显存要多少)，本文只讲机理与复算方法。

---

## 一、为什么是 VAE 解码，不是采样

这条链路里显存峰值出现在 **VAE 解码**：Flux2 VAE 的 decoder 有 4 个上采样级
（`decoder.up_blocks.0`–`3`），每级把特征图放大 2 倍，通道随之收窄
（384 → 384 → 192 → 96）。峰值激活出在**空间最大**的那几级，所以它跟画布面积走。

采样阶段的显存由主模型决定（`moss_image_sft_bf16` 约 10 GB 量级），是**固定开销**；
解码阶段的开销**随画布面积变化**，所以调 `panel_edge` / 格数时，真正被推动的是这一段。

---

## 二、宿主怎么估这笔账

ComfyUI 在 `comfy/sd.py` 的 `VAE.__init__` 里给每种 VAE 挂一个 `memory_used_decode`
函数，解码前用它算「这次要多少显存」，然后拿这个数去：

1. `load_models_gpu(memory_required=...)` —— 决定要不要把主模型挤出显存；
2. `batch_number = free_memory / memory_used` —— 决定一次解几张。

Flux2 VAE 走的是 `batch_norm_latent` 分支（判据：state_dict 里有 `bn.running_mean`），
它的基础公式来自 AutoencoderKL：

```
memory_used_decode = 2178 × latent_H × latent_W × 64 × dtype_size × VAE_KL_MEM_RATIO
```

- `latent_H / latent_W` = 画布尺寸 ÷ **16**（Flux2 VAE 的空间压缩比）
- `dtype_size`：fp16/bf16 = 2，fp32 = 4
- `VAE_KL_MEM_RATIO`：AMD 平台 2.73，其余 1.0

---

## 三、分水岭：一个 `× 4.0`

### v0.42.1 及更早（含所有正式版）

```python
if 'bn.running_mean' in sd:
    ddconfig["batch_norm_latent"] = True
    self.latent_channels *= 4
    old_memory_used_decode = self.memory_used_decode
    self.memory_used_decode = lambda shape, dtype: old_memory_used_decode(shape, dtype) * 4.0
```

预估被无条件放大 4 倍。

### master，提交 `d07dff4`（2026-10-23，PR #17196）

```python
    ddconfig["fused_norm"] = True
    self.working_dtypes = [torch.float16, torch.bfloat16, torch.float32]
    self.memory_used_decode = lambda shape, dtype: old_memory_used_decode(shape, dtype) * (
        1.0 if comfy.ldm.modules.diffusionmodules.model.fused_norm_served(self.device, dtype) else 4.0)
```

预估改成**条件**：走融合路径时乘 1.0，否则仍是 4.0。

**核实方法**（不要靠 release notes，直接看代码）：

```bash
cd <ComfyUI>
git tag --contains d07dff4          # 有输出 = 这个提交已进入某个 tag
grep -c fused_norm comfy/ldm/modules/diffusionmodules/model.py
grep -n "memory_used_decode.*4.0" comfy/sd.py
```

> **本机（2026-10-24 实测，`v0.42.0-44-gfb3befd16`）已包含该提交**：
> `git merge-base --is-ancestor d07dff4 HEAD` 通过；`model.py` 里 `fused_norm` 命中 **25** 处；
> `sd.py:698` 已是条件式 `* (1.0 if fused_norm_served(...) else 4.0)`。
> 运行时判据同样通过：`comfy.quant_ops.ck` 可用、设备 cuda:0、
> `fused_norm_served(fp16)=True` / `(bf16)=True` / `(fp32)=False`
> ⇒ **本机走 ×1 那一列**，下面复算表的「新宿主」列就是本机实际值。
>
> 但 `git tag --contains d07dff4` **无输出** —— 这个优化还没进任何正式 tag。
> ⇒ 用 pip / release 包装的用户仍在 ×4 那一列；只有 `git clone` 且 pull 到 10-23 之后的才有。

---

## 四、融合路径的启用条件

`comfy/ldm/modules/diffusionmodules/model.py`：

```python
def fused_norm_served(device, dtype):
    ck = getattr(comfy.quant_ops, "ck", None)
    if ck is None or device.type != "cuda":
        return False
    if torch.version.hip:
        return dtype == torch.float16 and ck.registry.get_constraints("hip", "fp16_conv3d") is not None
    return dtype in (torch.float16, torch.bfloat16)
```

| 条件 | 不满足时 |
|---|---|
| `comfy.quant_ops.ck` 可用（comfy-kitchen 已装） | 回落旧路径，预估仍 ×4 |
| 设备是 CUDA（ROCm 走另一支，要求 fp16 + RDNA3+） | CPU / MPS 不走 |
| VAE dtype 是 **fp16 或 bf16** | **fp32 静默失效**（`--fp32-vae` 就会踩到） |

⚠ 最后一条没有任何提示：用户以为升级了宿主就省显存，但精度设成 fp32 就白升。

---

## 五、复算表

公式套进去，`dtype = fp16`（本机实测 `vae_dtype = torch.float16`，与 bf16 同占 2 字节，数值相同）。
**「新宿主 ×1」列已用本机 VAE 实例逐格复算吻合**（1024×576→0.60、2096×608→1.29、
2336×1328→3.15、3120×896→2.84 GB）。

| 画布 | 格数（496 档） | 旧宿主 ×4 | 新宿主 ×1 |
|---|---|---|---|
| 512 × 848 | 3 | 1.76 GB | 0.44 GB |
| 1024 × 576 | 4 | 2.39 GB | 0.60 GB |
| 1520 × 576 | 6 | 3.55 GB | 0.89 GB |
| 2032 × 576 | 8 | 4.75 GB | 1.19 GB |
| 1520 × 848 | 9 | 5.23 GB | 1.31 GB |

| 画布 | 格数（512 档） | 旧宿主 ×4 | 新宿主 ×1 |
|---|---|---|---|
| 1056 × 608 | 4 | 2.60 GB | 0.65 GB |
| 1568 × 608 | 6 | 3.87 GB | 0.97 GB |
| 2096 × 608 | 8 | 5.17 GB | 1.29 GB |
| 1568 × 896 | 9 | 5.70 GB | 1.42 GB |

更大档位（`panel_edge = 768`）：

| 画布 | 格数 | 旧宿主 ×4 | 新宿主 ×1 |
|---|---|---|---|
| 2336 × 896 | 6 | 8.49 GB | 2.12 GB |
| 3120 × 896 | 8 | 11.34 GB | 2.84 GB |
| 2336 × 1328 | 9 | 12.59 GB | 3.15 GB |

**读法**：旧宿主下 9 格 768 的预估 **12.59 GB 已超过 12 GB 整卡显存** ⇒ 无论实际够不够，
预检就先判死刑，直接退分块解码。这就是「768 档以前跑不动」的完整机理。

---

## 六、显存不足时会发生什么

`comfy/sd.py` 的 `VAE.decode`：

1. 先按整图解码试一次；
2. 抛 `OOM_EXCEPTION` → 捕获 → `logging.warning("...retrying with tiled VAE decoding.")`；
3. 走 `decode_tiled_`，默认按 **64×64 的 latent 瓦片**切（latent 每像素 = 图像 16 像素，
   即图像 1024×1024），重叠 16 latent（= 图像 256 像素，正好 25%）。

**对本包的含义**：瓦片边界会落在画布中间，而画布中间正是**宫格线与格子内部**。
所以分块解码的接缝对故事板是双重损伤——既难看，又会被逐格裁切放大到每一格里。

⚠ 这一步**没有实测量化**（本包不内置 tiled decode，见 `docs/03`），
「接缝会穿过格子」是机制推断，不是本仓实测结论。若要做成断言，需要一组对照实验。

---

## 七、待验证

| 项 | 状态 | 怎么验 |
|---|---|---|
| 融合路径的实际解码提速 | **未量化** | 官方提交无 benchmark；需同画布 A/B |
| 融合路径（fp16 累加）与旧路径的像素差异 | **未验** | 同 seed 同 latent，比 md5 / 逐像素差 |
| 分块接缝对宫格的可见影响 | **未验** | 强制小 tile 解码 vs 整图，逐格对比 |
| `--lowvram` 下是否真的不再卸载主模型 | **推断** | 看日志的模型加载/卸载行 |

---

## 八、给本包的约束（不要越界）

- **不改 `panel_edge` 默认值**：默认 512 是按「落在 MOSS 1–2 MP 训练区间」定的，
  和显存无关。显存问题用文档指引解决，不用调参兜底。
- **不内置 tiled decode**：宿主已有 `VAEDecode (Tiled)`，内建会重复实现（`docs/03` 已决策）。
- **不硬拦大画布**：只 warn + 写 `report`（`docs/03` 已决策）。
- **不做宿主版本探测后自动降级**：节点包不该猜用户的宿主状态；给判据让用户自己看。
