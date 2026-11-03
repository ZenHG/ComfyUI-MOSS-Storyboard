# H3 Rewriter LoRA 基座决策备忘（T12 前置）

> 2026-11-01。给用户拍板用，不是实施报告。
> **本任务不下载、不改 registry/代码/权重、不跑 LoRA**；只把「要么批、要么不批」所需的事实摆齐。
> 证据分级沿用仓库口径：**A**=本机字节/实跑 ｜ **B**=HF API 元数据 ｜ **C**=作者自报 ｜ **D**=推算。
>
> **拍板结果（2026-11-01）：C 档（8B 契约）；27B 放弃。** 实施、sha 与真机证据见
> [`docs/61-T12-8B契约验证.md`](61-T12-8B契约验证.md)。

---

## 0. 一行式拍板问题

> **是否批准为 T12 下载一套「Qwen3.6-27B GGUF 基座 + H3 Rewriter LoRA GGUF」（最小档约
> 10.6 GiB / 质量档约 15.3 GiB）到模型目录，并在 12 GB 卡上做一次真跑（对照「挂/不挂 LoRA」
> 各一次）？** 若批准，请选档：**A 最小可行** / **B 质量档** / **C 换 8B 契约（需另开改 registry
> 的任务）** / **D 不下载**（继续用 `--base-url` 降级路径）。

不批准也不影响现有能力：`h3 rewrite --base-url <OpenAI 兼容端点>` 现在就能跑，只是没有 LoRA。

---

## 1. 结论速览

| 档 | 基座（文件） | LoRA（文件） | 下载合计 | 权重显存（估算） | 12 GB 卡 | 结论 |
|---|---|---|---|---|---|---|
| **A 最小可行** | `unsloth/Qwen3.6-27B-GGUF` · `Qwen3.6-27B-UD-IQ2_XXS.gguf` | `pytraveler/...-GGUF` · `...-LoRA-Q8_0.gguf` | **10.59 GiB** | 8.74 GiB + LoRA 1.85 GiB + 开销 ≈ **12.3 GiB**（D） | ⚠️ 边界：需 `--fit` 自动 offload 几层 | 唯一能在 12 GB 上尝试的 27B 档；质量与速度**未验证** |
| **B 质量档** | 同上 · `Qwen3.6-27B-UD-Q3_K_XL.gguf`（或 `Q4_K_M`） | 同上 · `Q8_0`（或 `F16`） | **15.33 GiB**（Q4_K_M 为 17.51） | ≈ **17 GiB**（UD-Q3_K_XL，D）/ 作者报 Q4_K_M ≈19 GB（C） | ❌ 不适配 12 GB（要大量 CPU offload，速度未验证） | 有 16–24 GB 卡再选；**不建议在 12 GB 上首跑** |
| **C 换契约（8B）** | `lightx2v/...-LoRA-8B` 的基座 `Qwen/Qwen3-VL-8B-Instruct` → `Qwen/Qwen3-VL-8B-Instruct-GGUF` · `Q4_K_M` | `pytraveler/...-LoRA-8B-GGUF` · `...-8B-Q8_0.gguf` | **5.37 GiB** | ≈ 6–8 GiB（D，未逐项算 KV） | ✅ 舒适 | 最省，但**不是 T12 原定契约**：要改 registry 的 `base_model/file/arch`，另开任务 |
| **D 不下载** | 现有 4B / 9B 文本模型 + `--base-url` | 无 | 0 | — | — | 现在就能跑；已知无 LoRA 改写质量（作者 README：基座模型不产出切点格式） |

一句话：**T12 的「零代码改动」结论成立**（`h3 rewrite` 已支持 `--model/--lora/--lora-scale`，
`h3 doctor` 已能判三件套），卡点只有两个：**下载决策**与**12 GB 卡的边界档**。

---

## 2. LoRA 契约（A 级：读代码；B 级：HF 元数据）

`moss_llm/smith/data/model-registry.yaml` 的 `minimax-h3.rewriter`：

| 字段 | 值 | 证据 |
|---|---|---|
| repo | `lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA` | B（HF API 存在，下载 265 / likes 192） |
| base_model | `Qwen/Qwen3.6-27B` | B（HF `base_model` 元数据 + `adapter_config.json`） |
| file（registry 期望） | `MiniMax-H3-Prompt-Rewriter-LoRA-Q8_0.gguf` | A（代码） |
| arch | `qwen35` | A（代码）＋ B（GGUF 转换仓元数据 `architecture: qwen35`） |
| rank / alpha | 256 / 512 | B（`adapter_config.json`：`r=256, lora_alpha=512`） |
| tasks | `t2va`（只覆盖 T2VA） | A（registry）＋ C（作者 README：FL2VA/Ref2VA 未实现） |

**官方仓只有 safetensors，没有 GGUF**：`adapter_model.safetensors` 3.48 GiB（B）。
registry 要的 GGUF 来自第三方转换仓 `pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-GGUF`
（下载 3519 / likes 26，B）；另有 `sandeshrajx/...-gguf`（同名同大小）与
`solphor/...-Q8-GGUF`（同大小、不同文件名）两个等价转换，**不推荐重复下载**。

**llama.cpp 侧参数（A：本机两个构建的 `--help` 实测）**：

- `--lora FNAME` / `--lora-scaled FNAME:SCALE` 都存在；还有 `--lora-init-without-apply`。
- 本地两个构建（b9596 / b11998）都支持 `--lora`（`h3 doctor` 也把这一条作为 OK 闸）。
- `--fit on --fit-target <MiB>` 存在；代码里的 `LlamaServer` 默认会传（显存自动适配）。
- **没有** `--no-lora-offload` 之类的开关（A：help 只有上面三条与 lora 相关）——
  adapter 张量是否随层 offload、显存增量多少，**未验证**。

**请求层（A：代码）**：`moss_llm/server.py` 已对每个请求发
`chat_template_kwargs: {"enable_thinking": false}`，等价于作者要求的 `--reasoning off`
（作者 README：`enable_thinking=False`；B：Qwen3.6 的 chat template 认这个字段）。

### 2.1 契约差异（真跑前必须知道，不改代码也能跑）

1. **system prompt 不是逐字官方**：`moss_llm/smith/adapters/h3.py` 在官方
   `SYSTEM_PROMPT`（逐字照抄 `prompt_template.py`）之后**追加了 `LOCAL_ADDENDUM`**（本地铁律）。
   作者 README 明确警告「system prompt 必须逐字，改了会降质」（C）。T12 首跑可选：
   （a）接受偏差、按现状跑；（b）另开一个小任务加「不追加」开关。**本任务不改代码。**
2. **解码策略不同**：作者推荐 greedy（`--greedy` / `--temp 0`）做可复现对比（C）；
   代码里 H3 适配器是 `temperature=0.7, gen_candidates=3`（A），即一次 rewrite 最多 3 次调用、
   结果有方差。首次真跑建议按代码原样跑并记录方差，不要拿单次输出当结论。
3. **输出 token 预算**：代码 `gen_max_tokens=2048`（A）；作者示例 `-n 1400`（C），够用。

---

## 3. 本机现状盘点（A 级，2026-11-01 实测；不写路径）

| 项 | 结论 |
|---|---|
| Rewriter LoRA（GGUF） | **不在盘上**：模型目录全树按 `Rewriter` 搜索 0 命中；`h3 doctor` 报 MISS。**无本地 sha（文件不存在，不是没算）** |
| 基座 Qwen3.6-27B | **不在盘上**（`h3 doctor` MISS） |
| 盘上同架构「异权重」 | `Qwen3.8-27B-GSQ-RCO-IQ2_S.gguf` 8.62 GiB、`-mtp` 版 8.95 GiB、`mmproj` 0.87 GiB；GGUF 头 `general.architecture=qwen35`（与 LoRA 的 arch 字段相同）→ **不能替代**，见 §3.1 |
| llama-server | 两个构建都 ✅ 支持 `--lora` / `--lora-scaled`（§2） |
| 本机 GPU | **12 GB 级（12,227 MiB）**；盘点时空闲只有 **2,009 MiB**（被桌面/其它进程占用约 9.9 GiB）→ **T12 真跑前必须等 GPU 空闲**，否则边界档必 OOM |
| 内存 | 31.9 GiB → 允许少量 CPU offload（A） |
| 模型盘剩余 | 两处分别 157 GiB / 429 GiB → 两种档位都够（A） |
| 默认模型目录 | **不覆盖本机布局**：`paths.model_dirs()` 的约定目录只 glob 一层 `*.gguf`，而本机 GGUF 在子目录里 → 必须设 `MOSS_LLM_MODEL_DIRS` 到**叶子目录**（A：`h3 doctor` 两种设置下输出不同，见 §6） |

`h3 doctor` 当前实测（省略路径）：

```
[MISS] Rewriter LoRA    MiniMax-H3-Prompt-Rewriter-LoRA-Q8_0.gguf
[MISS] 基座权重          （未找到）
       适配器是在 Qwen/Qwen3.6-27B 上训的。盘上没有同款基座。
       同架构的其他基座 2 个（Qwen3.8-27B-...gguf）——llama.cpp 会把适配器
       加载成功而不报错，但那是另一套权重，输出无意义。不要用。
[OK  ] llama-server     支持 --lora
```

这条「同架构的其他基座」是**故意**报的红旗：判据在工作，等基座到位后会变成「与适配器同款」。

### 3.1 为什么不能用盘上的 Qwen3.8-27B 替代（引用既有记录，不重测）

同架构（`qwen35`）、同尺寸（27B）**不等于同一套权重**：Qwen3.8 与 Qwen3.6 的张量名全部同名，
llama.cpp 的 LoRA 加载器**不会报错**——它会把 3.6 训出来的增量加到 3.8 的权重上，输出一段
语法正确、语义无效的提示词。这条静默失败不是推测，是已记录的**实测**结论：

- 历史记录（git 历史） §4 P4⑮：「同架构的 Qwen3.8-27B 会被 llama.cpp 静默加载成功
  （实测张量名全部同名）——已写进 doctor 当红旗」。
- 代码侧：`moss_llm/smith/cli.py` 的 `_name_matches()` / `cmd_h3_doctor()` 注释（“同架构 ≠ 同权重”），
  判据取模型名里的每个版本号片段，要求全部出现在文件名里。
- 本次实测（A）：`h3 doctor` 在模型目录可见时，主动列出「同架构的其他基座 2 个 … 不要用」。

所以 T12 的基座只能是 **Qwen3.6-27B 本尊**；盘上 3.8 只能当“危险样本”，不能当替代品。

---

## 4. 显存/内存预算（对照 12 GB 卡）

Qwen3.6-27B 的 KV 口径（B：官方 `config.json`；D：算式）：

- 64 层，其中 **每 4 层一个 full-attention** → 16 层有 KV；`num_key_value_heads=4`，
  `head_dim=256` → **f16 KV = 64 KiB/token**。
- 8K ctx：**512 MiB**（f16）/ ≈**272 MiB**（`q8_0` KV，代码默认 `cache_type_k/v=q8_0`）。
  16K ctx 翻倍；作者建议跑 8192。
- 线性注意力层的 recurrent state 另计，量级 ≈0.15 GiB（D 估算，未实测）。

固定开销（D，沿用仓库既有 G2 校准：CUDA 上下文 ≈1.05 GiB + compute buffer ≈0.2 GiB）：

| 项 | A 最小档（UD-IQ2_XXS） | B 质量档（UD-Q3_K_XL） |
|---|---|---|
| 基座权重 | 8.74 GiB | 13.48 GiB |
| LoRA Q8_0 | 1.85 GiB（若驻 GPU；**未验证**） | 1.85 GiB |
| CUDA + compute | 1.25 GiB | 1.25 GiB |
| KV（8K，q8_0） | 0.27 GiB | 0.27 GiB |
| 线性层 state | 0.15 GiB | 0.15 GiB |
| **合计** | **≈12.3 GiB** | **≈17.0 GiB** |
| 12 GB 卡（11.94 GiB 总量） | 超 ≈0.3–0.4 GiB → 靠 `--fit` offload 几层，速度未知 | 严重超出 → 不建议 |

作者自报的对照（C，RTX 5090）：Q4_K_M ≈19 GB、IQ4_XS ≈18 GB、UD-Q3_K_XL ≈17 GB、
UD-IQ2_M ≈13 GB；挂 adapter 后 50 tok/s（不挂 78 tok/s，−35%）。**与上表同量级**，
但作者没在 12 GB 卡上测过。

**关键未验证**：LoRA 张量在 llama.cpp 里是否随层 offload、offload 后 tok/s 掉多少、
边界档（IQ2_XXS 2.3 bpw）上 LoRA 是否还“有劲”。这些只能真跑。

另一个从 `--help` 读到的边界（A）：`--fit` **只调整未显式指定的参数**（help 原文
“whether to adjust unset arguments to fit in device memory”）。代码默认会显式传
`--n-gpu-layers 99`，此时 `--fit` 不会帮你降层数。→ 边界档首跑应传 `--ngl auto`
（`LlamaServer` 认这个值，会原样交给 `--n-gpu-layers auto`），让 `--fit` 自己决定层数。

---

## 5. 下载决策表（HF 精确 repo/文件 + 体积 + sha256 + 许可）

体积按 1 GiB = 2³⁰ B；sha256 取 HF 的 LFS `oid`（= 文件内容的 sha256）。

### 5.1 基座（`unsloth/Qwen3.6-27B-GGUF`，许可 **apache-2.0**，B）

| 量化档 | 文件 | 体积 | sha256（LFS oid） | 用途 |
|---|---|---|---|---|
| **UD-IQ2_XXS** | `Qwen3.6-27B-UD-IQ2_XXS.gguf` | 8.74 GiB | `968bfc712832031afebec339da3ae61c6822ab9a118e1d72b6be2a7781a96e30` | **A 最小可行档** |
| UD-IQ2_M | `Qwen3.6-27B-UD-IQ2_M.gguf` | 10.10 GiB | `24dfb0786e3b6b32e16082816a8245f4cf2fbaea71345062943c4323763ce5c6` | 12 GB 上更紧，不推荐首跑 |
| Q3_K_S | `Qwen3.6-27B-Q3_K_S.gguf` | 11.51 GiB | `4afb4abcf0207a484b0d7e92c0421b74e8ce1c7a7250bb9d824b79288da68f20` | 12 GB 放不下 |
| **UD-Q3_K_XL** | `Qwen3.6-27B-UD-Q3_K_XL.gguf` | 13.48 GiB | `cff4a2da6b5350a53f57dc6f798516e0195816f90c196c3f7bd3fd55556632dd` | **B 质量档（入口）** |
| IQ4_XS | `Qwen3.6-27B-IQ4_XS.gguf` | 14.38 GiB | `8a3365759dc1b33b52c4e7d91d5a67d5ee1418e8408aa54196f04a98da53e5dc` | 16 GB 卡档 |
| Q4_K_M | `Qwen3.6-27B-Q4_K_M.gguf` | 15.66 GiB | `5ed60d0af4650a854b1755bd392f9aef4872643dc25a254bc68043fa638392a0` | 作者基准档；12 GB 不宜 |

说明：基座是**多模态**模型，但文本改写**不需要 mmproj**（不传 `--mmproj` 即可，视觉塔不参与）；
不要为了“完整”去下 0.9 GiB 的 mmproj。官方 BF16 仓是 15 片 / 55.6 GB（B），**不选**——
GGUF 是唯一现实路径。

### 5.2 LoRA（`pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-GGUF`，许可 **未声明**，B）

| 文件 | 体积 | sha256（LFS oid） | 备注 |
|---|---|---|---|
| `MiniMax-H3-Prompt-Rewriter-LoRA-Q8_0.gguf` | 1.85 GiB | `ad0f5b2fd4c578fba22a61418859c4efc3a7a13604c9abd493824d3411eef194` | **推荐**：文件名与 registry 期望一致，Q8 相对 F16 平均误差 0.001（C） |
| `MiniMax-H3-Prompt-Rewriter-LoRA-F16.gguf` | 3.48 GiB | `208f362cc0bc2c66108b01fdcb8276a92cbb84a1f33cd8c9d7c1630cfb146def` | 无损但更大；12 GB 边界档不建议 |

上游 `lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA`（safetensors 3.48 GiB）与转换仓**都没有
声明 license**（B：HF 元数据无 license 字段；README 只说“跟随上游、不额外主张”）。
→ **内部验证可用；对外发行/商用前需要用户确认授权**，这是本备忘里唯一“法律”而非“技术”的风险。

### 5.3 下载耗时（D：按带宽推算，无法预知实际带宽）

| 档 | 合计 | @20 MB/s | @50 MB/s |
|---|---|---|---|
| A 最小 | 10.59 GiB | ≈9.5 min | ≈3.8 min |
| B 质量（UD-Q3_K_XL + Q8） | 15.33 GiB | ≈13.7 min | ≈5.5 min |
| C 8B 路线 | 5.37 GiB | ≈4.8 min | ≈1.9 min |

### 5.4 替代路线 C 的精确坐标（供对比，不在 T12 契约内）

- 官方 8B LoRA：`lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B`，基座 `Qwen/Qwen3-VL-8B-Instruct`（B）。
- GGUF LoRA：`pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-8B-GGUF` ·
  `MiniMax-H3-Prompt-Rewriter-LoRA-8B-Q8_0.gguf` 0.69 GiB
  （sha256 `736a29838f89e63f3585f4f00fe35bb2a91484a73b366460bb6e854ec6b82bcc`）。
- 基座 GGUF：`Qwen/Qwen3-VL-8B-Instruct-GGUF` · `Qwen3VL-8B-Instruct-Q4_K_M.gguf` 4.68 GiB
  （sha256 `67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2`，apache-2.0，B）。
- 本机已有先例：ComfyUI 的 `minimax-h3-audio-T8` 节点里有一份 `prompt_rewriter_8b.py`
  （pinned 上游 revision + 8B 任务契约，A）——但它走 transformers/peft（需第三方包），
  与 `moss_llm.smith` 的「零第三方 + llama.cpp」路线不同。
- **要接进 smith 必须改 registry 的 `base_model/file/arch`**（另开任务），本任务不动。

---

## 6. 预检命令清单（**只读**，不改任何状态；现在就能跑）

> 占位符：`<ComfyUI>` = 模型根（如 `<ComfyUI>/models`）、`<llama-server>` = 可执行文件路径、
> `<base.gguf>` / `<lora.gguf>` = 下载后的文件。所有命令都不下载、不启动大模型。

```powershell
# 0) 环境：模型目录必须指到「叶子目录」（约定目录只 glob 一层，见 §3）
$models = @("<ComfyUI>/LLM", "<ComfyUI>/LLM/Qwen3.8", "<ComfyUI>/loras")
$env:MOSS_LLM_MODEL_DIRS = ($models -join ';')
$env:MOSS_LLM_SERVER    = "<llama-server>"

# 1) 服务端能力：应看到 --lora / --lora-scaled / --fit / --reasoning
& $env:MOSS_LLM_SERVER --help | Select-String -Pattern 'lora|--fit|--reasoning'

# 2) 三件套现状：现在是 2 MISS + 1 OK；同架构异权重那条红旗应出现
python -m moss_llm.smith h3 doctor --json

# 3) HF 侧体积与 sha256（HEAD，不下载）：content-length 应等于上表，x-linked-etag 应等于 sha256
curl.exe -sIL "https://huggingface.co/unsloth/Qwen3.6-27B-GGUF/resolve/main/Qwen3.6-27B-UD-IQ2_XXS.gguf" |
  Select-String -Pattern 'HTTP/|content-length|x-linked-etag'
curl.exe -sIL "https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-GGUF/resolve/main/MiniMax-H3-Prompt-Rewriter-LoRA-Q8_0.gguf" |
  Select-String -Pattern 'HTTP/|content-length|x-linked-etag'

# 4) 磁盘与内存：目标盘剩余 ≥ 12 GiB（质量档 ≥ 18 GiB）；内存 ≥ 24 GiB 才能少量 offload
Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{n='FreeGiB';e={[math]::Round($_.Free/1GB,1)}}
[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB, 1)

# 5) GPU：真跑前 free 必须 ≥ 13 GiB（边界档），否则先等占用方释放
nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv,noheader

# 6) 下载完成后：sha256 必须与 §5 表一致（不计 hash 速度，9–16 GiB 需几十秒）
Get-FileHash -Algorithm SHA256 "<base.gguf>" | Select-Object Hash
Get-FileHash -Algorithm SHA256 "<lora.gguf>" | Select-Object Hash

# 7) 下载完成后：doctor 应变成 3 OK，且基座一项写「与适配器同款」
python -m moss_llm.smith h3 doctor
```

判定口径（写死，避免“看起来能跑”）：

- 第 2 条出现「同架构的其他基座 …不要用」= 红旗判据工作正常（不是故障）。
- 第 3 条 `content-length` 与 `x-linked-etag` 必须与 §5 表**逐字节**一致。
- 第 7 条三行全 OK，且基座行含「与适配器同款」；否则不做 T12。

---

## 7. 点头后的执行序列（**会下载、会占 GPU**；仅在批准后执行）

```powershell
# 1) 下载（以 A 最小档为例；没有 hf CLI 就用 curl -L -o 替代）
hf download unsloth/Qwen3.6-27B-GGUF --include "Qwen3.6-27B-UD-IQ2_XXS.gguf" --local-dir "<ComfyUI>/LLM"
hf download pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-GGUF --include "MiniMax-H3-Prompt-Rewriter-LoRA-Q8_0.gguf" --local-dir "<ComfyUI>/loras"

# 2) 校验 sha256（§6 第 6 条），不进则停止并报 Lead
# 3) doctor 三件套全 OK（§6 第 7 条）
# 4) 真跑一次（按代码原样：3 候选 × temp 0.7；输出落 out/h3/）
#    --ngl auto 让 --fit 自己决定层数（边界档必用；若 auto 仍 OOM，手降到如 55 再试）
python -m moss_llm.smith h3 rewrite "A red fox walks through a snowy forest at dawn." `
  --duration 8 --resolution 16:9 --ctx 8192 --ngl auto --startup-timeout 600 `
  --model "<ComfyUI>/LLM/Qwen3.6-27B-UD-IQ2_XXS.gguf" `
  --lora "<ComfyUI>/loras/MiniMax-H3-Prompt-Rewriter-LoRA-Q8_0.gguf"

# 5) 同 prompt 不挂 --lora 再跑一次（对照臂）
python -m moss_llm.smith h3 rewrite "A red fox walks through a snowy forest at dawn." `
  --duration 8 --resolution 16:9 --ctx 8192 --ngl auto --startup-timeout 600 `
  --model "<ComfyUI>/LLM/Qwen3.6-27B-UD-IQ2_XXS.gguf"

# 6) 收尾：确认 llama-server 退出、显存回落
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
```

**T12 的“跑成了”判据**（可判死，不靠“看起来能跑”）：

1. `h3 doctor` 三件套 OK，基座「与适配器同款」（A）。
2. 挂 adapter 的输出出现**切点格式** `[Shot 2] At 00:06.500`；不挂时是范围式 `(0s-4s)`
   ——作者 README 明确说这是 adapter 生效的标志（**C 级，本机未验证**）。
3. 同 prompt 挂/不挂的 tok/s 有可测差异（作者报 −35%，C）。
4. 输出过 `h3 check` 机检（A：仓库已有）。
5. 记录耗时/tok/s/输出原文，落 `out/h3/`（T12 实施任务负责，不在本备忘范围）。

---

## 8. 风险与未验证清单

1. **一切性能/质量数字未在本机验证**：本文所有 VRAM/速度结论要么是 D（推算），要么是 C（作者
   在 RTX 5090 上自报）。**12 GB 卡上能不能跑、跑多快、改写质量如何，全部未验证。**
2. **LoRA 许可未声明**（B）：上游与 GGUF 转换仓都无 license 字段 → 内部验证可用，发行/商用前
   需用户确认；`MiniMax-H3` 本身的生成物另受官方条款约束（C）。
3. **转换链是第三方**（C：作者自报用 `convert_lora_to_gguf.py` + 一处 `qwen.py` 补丁，
   因 Qwen3.5 线性注意力层的 V-head reorder 在 `out_proj` 上落在输入维）。我们只能核对
   sha256 与转换说明，**没有独立验证数值等价**。若转换有误差，失败形态会是“加载成功、
   输出语法像但语义乱”——与 §3.1 的静默失败同类，所以 §7 判据 2 必须做。
4. **`LOCAL_ADDENDUM` 追加**与 `temperature=0.7×3 候选`（§2.1）：与作者 greedy/逐字 system 的
   契约不同，可能压低 LoRA 效果。首跑接受偏差；要追求作者效果需另开小改任务。
5. **边界档质量**：IQ2_XXS（2.3 bpw）在 27B 上会掉多少、LoRA 低比特基座上是否还“有劲”未验证；
   作者只报了 Q4_K_M 的结果（C）。
6. **`--fit` 与 `--lora` 的交互未验证**：代码默认传 `--fit on`，若它把 LoRA 张量算漏/算重，
   可能加载成功但显存超；首跑盯 `nvidia-smi`。
7. **GPU 现在不空闲**：盘点时空闲仅约 2.0 GiB（A）；T12 需与当前占用方（桌面/其它任务）
   协调后再跑，禁止抢占。
8. **`h3 rewrite` 一次 = 最多 3 次生成**（候选搜索，A）：边界档上单次可能 1–5 分钟量级（D），
   首跑要给足 `--startup-timeout` 并盯日志。

---

## 9. 需用户拍板的事

1. **要不要做 T12？**（批准下载 ↔ 保持现状用 `--base-url` 降级）
2. 若批准：**选档 A（27B 最小可行，10.6 GiB，12 GB 边界）还是 B（27B 质量，15.3 GiB，需更大卡）？**
3. 若都不想要 27B：**是否改为开一个「8B 契约」任务**（C 档：5.4 GiB、12 GB 舒适，但要改
   registry 的 `base_model/file/arch` 并接受它是另一套适配器）？
4. 若批准：**LoRA 许可无声明，是否接受仅作内部验证**？（发行/商用需另确认）

拍板后可回复一行，例如：**「批准 T12，选 A，内部验证用」** 或 **「先做 8B 契约任务」**。
