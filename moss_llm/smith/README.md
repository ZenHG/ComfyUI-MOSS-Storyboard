# `moss_llm.smith` —— 语义 IR 与多方言确定性编译

`moss_llm` 的子包：把「事实」与「措辞」拆开的一层。**三个方向都通**：

- **正向**：一份语义 IR → N 个生图模型各自的原生 prompt
- **反向（洗图）**：一张图 → 取证 IR → N 个模型的原生 prompt，并可自审
  - 先走零模型路径：图里内嵌着当初的 prompt（A1111 / ComfyUI / InvokeAI / NovelAI），逐字精确
  - 没有元数据再走 VLM 三段式反推
- **读 prompt**：一段文字 prompt → IR，再编译成别的方言；附带往返校验
- **视频（MiniMax-H3）**：IR → MiniMax-H3 官方三字段终稿，以及反向解析与机检。
  H3 不是「第六个图像后端」——它的提示词是**一条时间线**，所以 `timeline` 进了 IR 本身

**已实现后端**：MOSS-Image、Qwen-Image 2.1（含编辑）、Krea 2、FLUX 3 Image（含编辑）、SDXL（tag + 77 token 分块）、**MiniMax-H3**（音视频三字段 + 时间线）。
**不做什么**：不做「一套通用模板」。几个模型的提示词哲学互相矛盾，统一模板必然在某个模型上静默失败。

**与父包的关系**（2026-10-31 合流后）：依赖是**单向**的 —— 本层可以 import 父包的运行时
（侧车 / 图像预处理 / 装包器），父包只在 `task=compile` 时懒加载 `smith.api`。
删掉 `moss_llm/smith/` 不影响节点的另外三个任务，也不影响核心 7 节点。
原本并存的五个模块已收敛：`llm.py` / `vision.py` 转发父包、`runtime.py` 删除、
`paths.py` 只留「模型目录」一半；`reverse.py` / `expand.py` 保留（任务不同）。
逐项取舍见 `docs/25-两份实现的合流.md`。

---

## 1. 快速开始

### 1.0 先解决「模型服务从哪来」

本工具**不绑定**任何特定部署方式。三条路，按你的机器选：

```bash
# ① 什么都没有：让它自己装一个 llama-server（官方 release，约 35MB）
python -m moss_llm.smith setup            # auto：有 GPU 用 vulkan，没有用 cpu
python -m moss_llm.smith setup --backend cuda   # 更快，但要额外拖 400MB cudart

# ② 已经有服务在跑：直接连，不需要可执行文件
#    适用于 LM Studio / llama-swap / Ollama 的 /v1 / docker / 另一台机器
python -m moss_llm.smith reverse photo.png --base-url http://127.0.0.1:8080

# ③ 自己拉起（本地权重）：给 --model + --mmproj + --server
python -m moss_llm.smith reverse photo.png \
  --model <多模态 gguf> --mmproj <mmproj-*.gguf> --server <llama-server 路径>
```

`--server` 的查找顺序是：显式路径 → `PROMPT_ARTISAN_SERVER` → 本包 `runtime/`、`bin/`
→ `LLAMA_CPP_HOME` → `PATH`。**不扫别的软件的安装目录**——那等于把「你装过某软件」
写成隐性依赖。

端口被占用时不会静默复用：先比对 `/v1/models` 的 alias 指纹，载的不是你要的模型就报错。
理由很实在——复用别人的模型会得到「看起来正常但内容不对」的输出，是最难查的一类错。

### 1.1 常用命令

```bash
# 看所有后端能力
python -m moss_llm.smith targets

# 确定性编译（不需要模型，毫秒级）
python -m moss_llm.smith compile examples/01-portrait-photoreal.json --target moss-image

# LLM 扩写（本地 llama-server 自动拉起/释放）
python -m moss_llm.smith compile examples/01-portrait-photoreal.json \
  --target qwen-image-2_1 --mode llm --length brief \
  --model ~/models/Qwen3-VL-8B-Instruct-Q4_K_M.gguf

# 扩写体检：IR 对得上这段 prompt 吗（零模型；人写的、别家生成的都能验）
python -m moss_llm.smith verify examples/02-poster-text-layout.json \
  --prompt out/reverse/poster__moss-image.txt --target moss-image

# 读懂 prompt：文字 → IR，并编译回去核对（零模型也能跑；A1111 parameters 整块也认）
python -m moss_llm.smith decompose examples/a1111-parameters.txt --target qwen-image-2_1

# 一条命令走完「读进来 → 写出去」：读 prompt 建 IR，再按目标方言扩写
python -m moss_llm.smith decompose examples/a1111-parameters.txt --mode llm --length brief \
  --target qwen-image-2_1 --model ~/models/Qwen3-VL-8B-Instruct-Q4_K_M.gguf

# SDXL（tag + 75 token 分块，永远不会因为超 77 而截断尾部）
python -m moss_llm.smith compile examples/03-multi-subject.json --target sdxl

# H3 音视频：IR → 官方三字段终稿（时间线在 IR 里，不在适配器里）
python -m moss_llm.smith compile examples/05-h3-t2va.json --target minimax-h3

# H3 终稿 → IR → 机检（零模型；--ir 才会判「铺满三线」）
python -m moss_llm.smith h3 parse examples/h3-t2va-sample.txt
python -m moss_llm.smith h3 check examples/h3-t2va-sample.txt \
  --ir examples/05-h3-t2va.json

# 清点 H3 改写器缺什么（适配器 / 基座 / 服务端）
python -m moss_llm.smith h3 doctor

# 往返校验：IR → prompt → 事实差（丢 / 多）
python -m moss_llm.smith roundtrip examples/02-poster-text-layout.json

# 洗图：图片 → IR → 四个后端的 prompt（--rounds 2 开启自审闭环）
python -m moss_llm.smith reverse photo.png \
  --model <多模态 gguf> --mmproj <mmproj-*.gguf> --server <llama-server.exe> \
  --target moss-image,qwen-image-2_1,krea2,flux3 --rounds 2

# 零模型洗图：图里本来就写着当初的 prompt（A1111/ComfyUI/InvokeAI/NovelAI）
python -m moss_llm.smith meta photo.png            # 只读，不做任何推断
python -m moss_llm.smith reverse photo.png --from-metadata   # 不起服务，逐字交出

# 只审一个已有 prompt 是否忠于图片
python -m moss_llm.smith audit photo.png --prompt out/reverse/photo__moss-image.txt \
  --model <gguf> --mmproj <mmproj> --server <llama-server.exe>

# 只跑检查
python -m moss_llm.smith lint examples/02-poster-text-layout.json --target moss-image

# 全量单测（零 GPU 零网络）
python -m unittest discover -s tests

# 真机跑批（正向，一次加载 10 个用例，落盘 out/live/）
python tools/run_live.py --model <gguf> --server <llama-server.exe>
```

**依赖：Python 3.9+，核心零第三方依赖。** 没有 PyYAML 也能读 registry（内置 YAML 子集
解析器，遇到不支持的语法会**报错而不是猜**）；没有 Pillow 也能反推（原图直传并告警，
只是不缩放、不转正 EXIF 方向、不合成透明通道）。想更省心：

```bash
pip install .            # 装成命令：prompt-artisan targets
pip install ".[full]"    # 额外带上 PyYAML + Pillow
```

---

## 2. 目录

```
moss_llm/smith/
  ir.py           IR 定义、加载、规范化（含 needs_layout 判定）
  registry.py     模型能力矩阵加载（PyYAML 可选）
  miniyaml.py     YAML 子集解析器：没装 PyYAML 也能读 registry，读不懂就报错不猜
  embedded.py     零模型洗图：读图内嵌的生成参数（纯标准库）
  expand.py       扩写：固定项抽取 + 体检 + 分层排序 + 账本
  invariants.py   6 条不变式（metamorphic）：不需要 ground truth 的一层验证
  clauses.py      IR → 子句计划（维度 + 重要度 + 观察散文去重）；prose / tag 两种渲染
  layout.py       MOSS Layout：draft(abs/xyxy) → compiled(abs_centralized/yxyx)
  tokens.py       token 计数：启发式 / 服务端 tokenize
  lint.py         18 条静态检查
  api.py          对外门面：一段 IR 文本 → 后端提示词 + 体检（节点用的就是它）
  llm.py          转发父包 `moss_llm.server.LlamaServer`（侧车与客户端只剩一份）
  vision.py       转发父包 `moss_llm.vision`（EXIF 转正 / 白底 / Lanczos 1536 / JPEG 444）
  reverse.py      图片 → 取证 JSON → 融合成 IR（含文字专线）
  decompose.py    文字 prompt → IR（字面量扫描 + 模型抽取 + 对账；IR 不许留不可渲染的事实）
  roundtrip.py    往返校验：IR → prompt → 事实差（丢 / 多）
  h3.py           H3 方言：时间线规范化 / 渲染 / 反向解析 / 28 条铁律的机检子集
  audit.py        忠实度审计 + 补丁 IR + 闭环控制
  adapters/       moss_image · qwen_image_21 · krea2 · flux3 · sdxl · h3 · generic（按 registry 驱动）
  data/           model-registry.yaml（随包发，`pip install` 后也在）
  cli.py          命令行（调试壳，不进交付路径）
schema/           ir.schema.json（IR 的机器可读定义，仅文档用）
examples/         5 个可跑 IR（含 05-h3-t2va）+ A1111 parameters 样例 + H3 终稿样例
  comfy_node_example.py  ComfyUI 参考节点（证明契约够用；不进包）
tests/            335 条单测（有 PyYAML 时多跑 2 条对照测试）
  fixtures/reverse/  4 份真机逆向 IR + expected.json（**图像**后端基线；H3 不在内，理由见 §4.1）
tools/run_live.py 真机跑批（正向）
tools/freeze_reverse_set.py 重建回归基线（测试不许自己改自己）
pyproject.toml    打包：`pip install .` 得到 prompt-artisan 命令
docs/             设计与交接文档（交接书 §9 有完整目录 + 「改 X 该动哪个文件」查找表）
out/live/         正向真机产物
out/reverse/      洗图产物（.ir.json / .report.json / 各后端 .txt）
```

---

## 2.5 通用性：哪些是「约定」，哪些是「假设」

这个项目是给人用的，不是给这一台机器用的。所以下面四件事都做成了可替换的，
而不是写死在代码里：

| 维度 | 不假设什么 | 怎么做到 |
|---|---|---|
| **服务端** | 不假设用户有 llama-server | 三条路：`setup` 自己装 / `--base-url` 连已有服务 / `--model` 自己拉。`--model-id` 适配 Ollama 的模型名、LM Studio 的注册 id |
| **服务端版本** | 不假设参数都存在 | 启动前问一次 `--help`，`--jinja` / `--image-max-tokens` 有才加。老版本不会因为多拼一个参数就「启动即退出」 |
| **响应能力** | 不假设它支持 `response_format` | 四级降级（json_schema → json_object+schema → json_object → 无约束），**任何 4xx 都触发**（网关有判 422 的），每降一档出声 |
| **语言** | 不假设模型用英文回 | kind / 年龄 / 方位 / 性别 / 谓词都收中英两套；数字用**数字边界**而非 `\b`（Python 的 `\w` 含 CJK，`\b\d+\b` 匹配不到「28岁」） |
| **目标模型** | 不假设只有这四个后端 | registry 里声明了 `mode`/`negative`/`weight_syntax`/预算，就能直接 `--target` 用（通用后端），**不用写 Python** |
| **依赖** | 不假设用户能装包 | 核心零第三方依赖：registry 用内置 YAML 子集解析器，图片元数据用标准库读。PyYAML / Pillow 都是「装了更好」而不是「不装不能跑」 |
| **输入图片** | 不假设是干净的 RGB PNG | EXIF 方向转正（不转，手机竖拍会被当横图，宽高和画面都错）；透明通道合成到**白底**（直接丢 alpha 会变黑底，VLM 于是如实描述「黑色背景」）；调色板 / CMYK / 16bit / 动图首帧 / webp / avif 都归一 |

新增一个后端，只需要在 `moss_llm/smith/data/model-registry.yaml` 里加一段：

```yaml
  my-model:
    display: "My Model"
    encoder: { name: "T5", max_tokens: 512 }
    prompt: { mode: tag, verbosity: 0.4, language: en, weight_syntax: none }
    negative: { supported: true, channel: true_cfg }
```

```bash
python -m moss_llm.smith targets                      # 会显示「通用」
python -m moss_llm.smith compile ir.json --target my-model
```

**通用后端不做的事**（诚实边界）：结构化输出（MOSS 的 Layout JSON）、编辑指令、
专属采样档。这些每个模型一套独立语法，猜不出来——要这些就写原生 adapter。

---

## 3. 五条设计原则

**① LLM 只做语义，编译器做语法。**
坐标换算、token 计数、JSON 序列化、权重剥离全用代码。模型算数必错。

**② IR 存事实，不存句子。**
存 `28 / 黄色雨衣 / 左侧前景 / importance 1.0`，不存英文散文。同一份 IR 才能编译成四种方言。

**③ 预算不足按重要度裁剪，不按位置截断。**
Krea 2 的 512 token 是按位置砍，砍掉的可能是主体。所以每条事实带 `importance`。
观察散文（`notes`）的重要度一律低于同维度的结构化事实：事实是身份，散文是修饰。

**④ 模型不许编。**
正向跑批复现过三种真实故障；反向跑批又抓到七种。现在有四道闸：
GROUNDING 规则 → 确定性清洗（否定式 / 指令回写 / 占位值 / 方位-坐标交叉校验）→ lint L016–L018 → 人工复核。

**⑤ 布局不是「有框就上」，是「有相对位置才上」。**
官方原文：*An ordinary photograph never does.*
反推必然产出 bbox，但一张全帧头像按 Layout JSON 编译只会得到 `[-410,-410,410,410]` 这种废话框。
`IR.needs_layout()` 的判据是：有文字框，或 ≥2 个未盖满画布的框。

---

## 4. 五个图像后端的方言差异（这是全部复杂度所在）

| | MOSS-Image | Qwen-Image 2.1 | Krea 2 | FLUX 3 Image | SDXL |
|---|---|---|---|---|---|
| 形态 | 裸散文七维 / Layout JSON | 长散文 / 编辑指令 | 自然语言，媒介打头 | 2–4 短句 + bbox 表 | tag，逗号分隔 |
| 长度 | 150–300 词 | 按 IR 动态 150–450 词 | 250–380 词，**512 硬截断** | 40–90 词（模型自己扩写） | **75 token / 块，分块（BREAK）** |
| 负向 | 有 | 无 | 无 | 无（Base 版才有） | 有（CFG） |
| 布局 | `abs/xyxy`→中心原点 `yxyx` | 不支持 | 不支持 | `0–1000` 网格 + `<silhouette_n>` | 不支持（要 Regional Prompter） |
| 编辑 | 不支持 | `<imageN>` | 不支持 | `<ref_image_0>` + Keep 句 | 不支持（inpaint 靠 mask） |
| 采样 | Euler/50/CFG3/Shift5/SLG22 | 40 步/true_cfg 1.0 | RAW 52/3.5；Turbo 8/0 | 蒸馏 16 步/cfg 0 | 28 步/CFG 7 |

**同一个 IR，五份产物完全不同，且都不报错。** 这就是为什么需要编译器，而不是模板。

SDXL 那列值得单独说一句：**77 是每块的上限，不是整段的上限**。通用后端会把子句按重要度
砍到 77 以内（不越界，但丢内容）；原生 adapter 改成按子句边界切块、块间插 `BREAK`，
一条不丢。同一条「不能越界」的规则，两种实现，代价完全不同——这就是「原生」与「通用」的分界。

---

## 4.1 H3：不是第六个图像后端，是第三种体裁

把 H3 塞进上面那张表会得到一行全是「不支持」的东西，那就说明**表格问错了问题**。
五个图像后端共用同一条流水线：把 IR 的七个维度摊成子句、按重要度装箱、渲染成散文或 tag。
H3 没有维度顺序、没有 token 预算、没有负向通道——它有的是**时间**：

```text
integrated_multimodal_description: [Shot 1] … [Shot 2] At 00:03.500, the camera cuts to …
overall_soundscape: …
non_diegetic_music: N/A
```

所以做法是：**把「时间」补进 IR，而不是给 H3 造一个私货容器**。

```json
"timeline": {
  "duration_s": 8.0, "mode": "t2va", "one_take": false,
  "shots": [{"index": 1, "framing": "a medium-wide shot", "entities": ["c1"],
             "action": "lifting the wooden shutters until the light spills in",
             "action_kind": "participle",
             "camera": {"move": "push in", "amplitude": "small", "speed": "slow"},
             "dialogue": [{"speaker": "S1", "lang": "zh", "text": "第一炉，刚出炉。",
                           "subject": "c1"}]}],
  "soundscape": ["…"], "music": []
}
```

三个关键决定：

1. **`timeline` 是 IR 的一等字段，不是 adapter 的私有参数。** 空 timeline 序列化时整个键
   省略，所以老 IR 的 JSON 逐字节不变（有用例钉住）。图像后端读不到它就当没这一维。
2. **`action_kind` 让「知道的事」不被二次猜。** 一句 `The baker lifts the shutters` 单看
   分不出是「名词短语 + 分词」还是「完整句」；`h3.parse` 在剥 `frames` 时**知道**答案，
   于是把它写进 IR。知道的事情不该让下游再猜一遍。
3. **`importance` 在 H3 上没有意义。** 镜头按时间排，不按重要度排。所以 H3 的
   `clause_pipeline = False`：它不走子句装箱，M4（预算只做减法）对它报「不适用」而不是
   「通过」——报通过就等于宣称验过了。

### 官方改写器（Rewriter LoRA）

`lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA` 是在**固定输入格式**上训的：system 一段说明，
user 三行 `resolution / duration / original_prompt`。所以 `adapters/h3.py` 里的常量是
**逐字**照抄它的 `prompt_template.py`，一个词都不改——改措辞等于让适配器失效。

好处是：**有 LoRA 时走 LoRA，没 LoRA 时走同一个接口的普通模型**。因为接口相同，换权重
不需要改代码。这正是「并入」而不是「新做一个工具」的理由。

```bash
# 清点：适配器 / 基座 / 服务端，缺什么明说
python -m moss_llm.smith h3 doctor

# 真改写（挂了 LoRA 就是官方那条路径）
python -m moss_llm.smith h3 rewrite idea.txt --duration 8 --resolution 16:9   --model <基座 gguf> --lora ~/models/MiniMax-H3-Prompt-Rewriter-LoRA-Q8_0.gguf
```

🔴 **最危险的一件事：同架构 ≠ 同权重。** 这个适配器的基座是 `Qwen/Qwen3.6-27B`。
盘上的 `Qwen3.8-27B-*` 有**全部同名张量**（`ssm_alpha` / `attn_qkv` / `attn_gate` …），
所以 llama.cpp 的 LoRA 加载器**不会报错**——它会安静地把 3.6 训出来的增量加到 3.8 的权重上，
然后产出一段语法正确、语义无效的提示词。`h3 doctor` 会点名这件事，因为这类静默失败
正是这个项目存在的理由。

### 机检：28 条铁律里可机检的那一部分

`h3.check()` 只做**能确定性判的**。每条都注明守的是哪条铁律：

| 码 | 守什么 | 铁律 |
|---|---|---|
| `H3-FIELD` / `H3-FIELD-ORDER` | 三字段齐全、顺序固定 | 官方输出契约 |
| `H3-SHOT1-TS` | Shot 1 不写时间戳 | 11（实测：开场时间戳会把首帧锁成置景图）|
| `H3-SHOTN-TS` / `H3-TS-ORDER` | Shot 2+ 必带时间戳、严格递增 | 11 / 16 |
| `H3-TS-HEAD` / `H3-TS-TAIL` / `H3-TS-GAP` | 铺满三线：前不空窗、后不定格、中不断档 | 16 |
| `H3-D-OUT` / `H3-D-LANG` | 台词只能整体在 `<d>` 内，且带语言标签 | 7（断言 A，永不摘除）|
| `H3-D-META` | `<d>` 外不写语速/口音/语言声明/人声锁 | 7 末条（ot13 实测的乱语主因）|
| `H3-MUS` | `non_diegetic_music` 严格等于 `N/A` | 8（`无。` 曾经 100A 过闸）|
| `H3-NEG` | 不用否定式 | 1 |
| `H3-COUNT` | 不承诺数量词 | 26 |
| `H3-GEO` | 不用地理方位/「一侧」 | 28 |
| `H3-PARAM` | 正文不写帧数/帧率/分辨率/步数 | 13 |
| `H3-LANG` | 有台词段正文英译 | 12·补（CER 实测差 11 倍）|

**判据自己踩自己的地方，都在代码里留了注释**：`无任何文字与符号` 是铁律 14 **要求**写的
正面声明，不能被铁律 1 判成否定式；`00:03.500` 长得像画幅 `3:500`，判参数泄漏前必须
先把时间戳挖掉。这两条都有「正例不报」的用例守着。

**没有判据的层不假装绿**：表演、情绪、构图好坏、音色对不对，`check()` 一条都不管。
全绿只代表「形式与自洽过关」。

### 三个方向都通

```bash
python -m moss_llm.smith compile   examples/05-h3-t2va.json --target minimax-h3
python -m moss_llm.smith h3 parse  examples/h3-t2va-sample.txt          # → IR
python -m moss_llm.smith h3 check  examples/h3-t2va-sample.txt --ir examples/05-h3-t2va.json
python -m moss_llm.smith roundtrip examples/05-h3-t2va.json --target minimax-h3
python -m moss_llm.smith invariants examples/05-h3-t2va.json --target minimax-h3
```

`decompose` 也会**自动认**：文本里行首出现两个以上字段名，就走确定性的 `h3.parse`，
不走 LLM 抽取——能确定性读的，不要用概率读。

---

## 5. 洗图：先看图上写没写，再决定要不要模型

### 5.1 零模型路径：图内嵌的生成参数

生成图里经常原封不动躺着当初的 prompt。读到它就不需要 VLM、不需要 GPU、不需要联网，
而且是**逐字精确**的——比任何反推都准。所以洗图的第一件事不是调模型，是问一句
「这张图自己招了吗」。

```bash
python -m moss_llm.smith meta photo.png                    # 只读，不做推断
python -m moss_llm.smith reverse photo.png --from-metadata  # 直接交出，不起服务
```

| 方言 | 触发块 | 说明 |
|---|---|---|
| A1111 / Forge / SD.Next / Fooocus | PNG `parameters` | `正向 / Negative prompt: / Steps: ...` 三段。参数行以 `Steps:` 为锚点（不是「最后一行」），值里的逗号用引号保护（`Lora hashes: "a: 1, b: 2"`） |
| ComfyUI | PNG `prompt`（API 图 JSON） | 顺着 KSampler 的 `positive`/`negative` 连线**回溯**到文本节点，能穿过 `ConditioningCombine` 这类中转。同时存在的 `workflow`（UI 图）忽略并出声 |
| InvokeAI | `invokeai_metadata` | `positive_prompt` / `negative_prompt` / model / seed |
| NovelAI | `Software` + `Comment` | `prompt` / `uc` |
| 各种写 JPEG 的工具 | EXIF `UserComment` | 内容若是 A1111 行格式就按 A1111 解，否则当纯 prompt |

实现只用标准库：PNG 的文本块就是「长度 + 类型 + 载荷」，不值得为它引入 Pillow。
两个坑值得记：

- **ComfyUI 的图是「连线」，不是「文本」**。文本散在 `CLIPTextEncode` 节点里，
  正负向靠 sampler 的输入边区分。不回溯连线就只能拿到一堆分不清正负的字符串。
- **EXIF UserComment 的字符集声明经常是错的**。没声明时不能直接当 UTF-8，也不能
  「试解码看哪个像」——UTF-16 用错字节序解出来是**合法汉字**，不会报错。
  可靠的判据是零字节落在奇数位还是偶数位（ASCII 文本在 UTF-16 里必有一半是 `0x00`）。

**边界（写清楚，免得被当成万能）**：平台会剥元数据（微博、推特、微信转发基本都没有）；
读出的是**当初写的** prompt，不等于**这张图现在长这样**——img2img、局部重绘、手改都会让它失真。
所以元数据只进 `provenance`，不覆盖反推结果；两条路互不依赖。

### 5.2 没有元数据时：三段式，不让模型边看边编

直接让 VLM「看图写 prompt」，模型会边看边编。本机实测：8B 在空 IR 上硬凑 450 词时凭空造出「茶屋」招牌、灯笼、自行车。所以拆开：

```
图片 ──①取证(VLM, T=0.1)──▶ 结构化 JSON ──┐
     └─②文字专线(VLM, 只转录)──▶ blocks ────┤
                                            ├─③融合(纯 Python)──▶ IR ──▶ 四个后端
                                  清洗 / 校验 / 去重 / 排框
```

第 ③ 段不调模型，因此**可单测、可复现、不会幻觉**。

**为什么要第二遍只看字**：一张 1600×900 的 UI 截图，第一遍取证只描述了四张照片，标题和
全部标签一个字没提——文字正是那张图的全部信息。原因是**任务竞争**：模型把预算花在
「图里有什么」上，文字的优先级被图片挤掉了。第二遍只问「把字抄下来」，一次额外调用
就把 9 条中文 UI 文案逐字拿回来了。`--no-text-pass` 可关。

清洗层做的八件事（每一件都是跑批撞出来的）：

| 现象 | 处理 |
|---|---|
| 模型把「没有」写成 `"none"` / `"not applicable"` / `"unknown"` | 递归剔除，含 `"none, shadows softly diffused"` 这种挤在一条字符串里的 |
| bbox 与同一响应的 `anchor` 互相矛盾（实测 7 个框错 3 个，anchor 全对） | **换通道**：位置按方位重建，宽高沿用模型。`spatial.bbox_source` 记录来源 |
| 坐标出现三种刻度（0-1 / 0-100 / 0-1000） | 三档都认；只按「>1 除 100」判会把 0-1000 算成 7.84 再夹成 1.0 |
| `text` 实体没有 `text_content` | 直接丢弃，不降级成 object（降级会变成 10 个半句文案的空壳） |
| UI 截图被判成 `multi_panel`（因为画面里写着「4 格故事板」） | 满屏文字且无角色 ⇒ `single`；prompt 里也写死了这条 |
| 中文气泡被写上 `case: "upper"` | 无拉丁字母时丢弃大小写 |
| 模型把关系指向文字块（`the woman faces the 他还在楼下`） | 丢弃指向 text 实体的关系 |
| 同一角色在四格里出现 4 次，给 4 段一模一样描述 | 按「去方位后的描述」归并成一句 + 一句方位补充 |
| 同一格里的多个文字块拿到**完全相同**的框 | 竖排等分，每段至少 6% 画高；否则 Layout 按框去重会直接丢掉它们 |

### 5.3 结构化输出：语法只管形状，不管内容

llama.cpp 官方文档原话：**"The JSON schema is only used to constrain the model output and is
not injected into the prompt. The model has no visibility into the schema."**

所以 `response_format` 只解决「能不能解析」，不解决「填得对不对」。两件事必须分开做：

- **形状**交给语法：`response_format: json_schema`，服务端不认就逐档降级
  （`json_schema` → `json_object+schema` → `json_object` → 无约束），**每降一档都出声**。
- **内容**交给 prompt：字段说明必须在提示词里再写一遍。只发 schema 会得到「形状对、内容空」的 JSON。

schema 里只 `required` 三个字段（`canvas` / `style` / `entities`）。全字段 required 会逼模型
给没看见的字段编值——那正是 `"fill": "none, shadows softly diffused"` 的来源。
语法严格用在校验，不用在逼供。

**JSON 模式与重复惩罚互斥**：键名在 JSON 里会合法地重复 N 次，DRY / presence_penalty /
frequency_penalty 全都按「重复即病」惩罚，会把键名惩罚成乱码。所以 JSON 调用里整族 DRY
强制摘掉，只在散文调用里开（`dry_multiplier=0.8`，`dry_allowed_length=3`）。

---

## 6. 扩写：不是写长，是降低欠定槽位的熵

「扩写」最容易被做成一件事：让模型把一句话写长。那是**填充**，不是扩写——
「masterpiece, 8k, best quality」对输出分布的约束接近零，写一百个也不如一句
「雨后的湿沥青把招牌倒映成竖条」。

所以这里的目标定义是：**把欠定的槽位定下来，同时一个都不许改动用户已经定下的东西。**

### 6.1 两种失败模式，方向相反

| 模式 | 症状 | 判据 |
|---|---|---|
| **欠约束** | 太短太泛，同 prompt 出十张十个样 | 词数低于该目标的长度带下界 |
| **过约束** | 用户没说的也写死了，模型没有自由度 | 出现了 IR 里没有的数字 / 引号文本 |

这两件事必须**分开判**。只盯着「写长点」会滑向过约束，只盯着「别乱写」会停在欠约束。

### 6.2 长度是方言参数，不是全局真理

| 目标 | 该多长 | 为什么 |
|---|---|---|
| FLUX 3 | **40–90 词** | 官方自带 9B prompt upsampler（`flux-3-image-klein-pup`），写长 = 过度指定 |
| Krea 2 | 250–380 词 | 官方要长，但 encoder 在 512 token 处**按位置硬截断** |
| MOSS-Image | 150–300 词 | 官方建议，质量词 banned |
| Qwen-Image 2.1 | 150–450 词 | 官方 20 句长散文，无硬上限 |

同一个 IR 扩到不同目标，长度目标不同。带子写在 `registry.prompt.length`（brief / standard / detailed），
`--length` 选档；代码里不再有魔法数字。

### 6.3 架构：账本 → N 次采样 → 体检择优 →（一次）回炉

与洗图的三段式对称，但**不调第二次模型做审计**：

```
IR ──①账本(纯 Python)──▶ FIXED / OPEN / FACTS / LENGTH ──②采样 ×N(VLM)──▶ N 份草稿
                                                              │
                                        ③体检(纯 Python) ◀────┘  按分层键排序取第一
                                        │ 仍 fail → 带着失败项回炉一次
                                        └ 仍不过 → 报告 fail，不静默
```

- **账本**是纯 Python 生成的：哪些是**不能动的**（图里的字、年龄、用户明说的 must_keep）、
  哪些是**空的槽位**（可以自由定，但只定一次）、长度目标。模型看不到 schema，也猜不到这些。
- **采样 N 份再挑**，因为本地 8B 模型的失败是**间歇性**的：同一份账本，这次改了年龄，
  下次没改。平均质量靠改 prompt，**方差只能靠搜索**。命中「硬错为零且在长度带内」立刻停。
- **体检**是纯 Python 的，零模型、零网络：漏了固定项、凭空出现数字、凭空出现引号文本、
  长度带外、质量词、否定式、比例写进散文。
- **排序是分层的，不是总分**：`(硬错数, 离长度带的距离, 警告数)`。三层代价不同，
  加权求和会让「3 条无关警告」看起来比「1 处造字」更糟。
- **回炉只一次**：第二遍还错，说明是事实层（IR）的问题，重跑模型不解决。

```python
# ComfyUI 节点里就是这样调的（llm 只需要一个 .chat(system, user, temperature=, max_tokens=) 方法）
ad = get_adapter("moss-image", reg, llm=llama)
ad.length = "standard"
ad.gen_candidates = 4          # 1 = 关掉搜索
out = ad.realize(ir, mode="llm")
out.positive, out.expand, out.warnings
```

### 6.4 判据：什么算错，什么只是差

| 项 | 级别 | 理由 |
|---|---|---|
| 图里的字没逐字出现（改标点也算） | **error** | 改了就是另一张图 |
| 年龄 / 用户 must_keep 丢失 | **error** | 同上 |
| 出现 IR 里没有的数字 | **error** | 凭空造细节，最高置信的幻觉信号 |
| 出现 IR 里没有的引号文本 | **error** | 图里没有这段字 |
| 方位/颜色没对上 | warn | 表达方式本来就自由，方向别丢即可 |
| 词数在长度带外 | warn | 欠约束或过约束，但不算错 |
| 质量词 / 否定式 / 比例进散文 | warn | 污染，不是错误 |

**故意不给总分。** 一个 0–100 的分数会被当成结论，而这些项根本不是同一个量纲。
报告给的是逐项事实：`fixed 4/4`、`extra_numbers []`、`198 词 / 带 150–300`。

### 6.5 真机一次（Qwen3-VL-8B，MOSS 目标，`--length brief`）

100 词（带 70–120）· 固定项 3/4 · 无造数 · 无造字 · 判定 warn

> A 28-year-old woman with damp shoulder-length black hair and fair skin stands in the left third
> of a narrow Kyoto backstreet, weight on one leg, looking up at the overcast sky; she wears a
> yellow raincoat and black ankle boots. Wet asphalt mirrors shop signs under soft blue-hour light
> from the left, mist hovering low. …

漏的那一项是方位里的 `foreground`（散文只写了 left third）——软项，warn 不是 error。
这正是分级想要的效果：**改字要停下来，方位含糊只提醒。**

### 6.6 不变式：不需要 ground truth 的那一层

扩写体检回答「这段 prompt 对不对得上这份 IR」。还有一类性质，**没有标准答案也能判真假**：

> 对输入做一个语义上不该改变结果的变换，输出就必须跟着不变。

6 条关系，零模型、毫秒级，图像后端 12 种「示例 × 后端」组合全绿；M6 只在有时间线时适用：

| 代号 | 关系 | 打破了会怎样 |
|---|---|---|
| M1 | 幂等：同输入两次编译逐字节相同 | 有随机源或全局状态泄漏 |
| M2 | 顺序无关：打乱 entities / relations / notes 数组 | 两个人写同一份 IR 得到两张图 |
| M3 | 规避隔离：追加 `avoid` 只动 negative | 「不要什么」渗进正向，反向激活 |
| M4 | 预算只做减法：不越界、不减用、不改写 | 加预算反而丢内容，或为省预算改写事实 |
| M5 | 提权不丢：`importance→1.0` 不丢描述 | 排序键写反，或 importance 被写进内容（字号） |
| M6 | 时间线往返：IR → prompt → 再解析，镜头/时间戳/台词不丢位 | 台词串到相邻镜头、切点写错，而 `diff` 全绿 |

```python
from prompt_artisan import invariants as INV
ok, line = INV.summarize(INV.run(adapter, ir))     # → (True, "6/6 条不变式通过")
```

**每条关系都有一个「破坏版」测试**：把实现故意改坏，关系必须变红。只有破坏版红了，
正常版绿才不是测空气。这层证明不了「写得好」，只证明「没有偷偷依赖不该依赖的东西」。

M5 曾经**两个洞叠在一起**：text 实体被排除（importance 兼任字号，见 §4 P3⑫），
加上「描述在不在输出里」的大小写敏感比对（首句被 `capitalize_sentences` 大写）。
结果是 02 海报在 qwen / krea2 上报「检查了 0 个实体」，而三条描述都在输出里。
现在两边都小写再比，M5 覆盖全部 3 个实体。**「检查了几个」是断言，不是日志**。

M6 是这一轮新加的，它和 `roundtrip.diff` **不等价**：`diff` 比的是「事实在不在文本里」，
M6 比的是「**读回来还是不是同一件事**」。文本里出现了台词，不代表它落在正确的镜头里、
带着正确的说话人。所以 `diff` 全绿而 M6 红，是可能的——那正是台词串镜。

M6 的破坏版也踩过一次坑：**不能靠改 IR 来做**。改了 IR 之后渲染和解析同步地少一句，
M6 仍然自洽通过。破坏版必须破坏**管线**（打桩让渲染器吞掉台词），不是破坏输入——
这是「测试必须能变红」的一个具体反例。

### 6.7 读懂 prompt：文字 → IR

编译器只管 IR → prompt。反方向的边以前是缺的，所以「把 A1111 的 prompt 换成 Qwen 方言」
只能靠人重写。`decompose` 补上这条边，**账本先行**：

```
prompt ──①确定性扫描──▶ 字面量（引号文字 / 年龄 / 数字 / 颜色 / 方位 / 比例）
                        │
        ──②若有 LLM───▶ 结构化实体（模型只做加值）
                        │
        ──③对账──────▶ 补回模型漏掉的硬事实；补不进去的撤回并出声
                        ▼
                        IR
```

三条纪律：

1. **模型的方差只影响「描述多细」，不影响「事实对不对」。** 模型漏掉的那行字，`reconcile()`
   会补成 text 实体；模型编的数字，`expand.verify()` 会抓出来。
2. **IR 里不许留没有渲染路径的事实。** 一条事实若 compile 不出来，记进 IR 等于让体检报
   假错——那是 IR 在撒谎。注入后立刻自证可渲染，渲不出来就撤回（`report.dropped_facts`）。
3. **没有 LLM 也能用，但如实降级。** `method="scan"` 只做字面量归位：引号成文字块、
   年龄成主体、`35mm` 成焦距、方位成 anchor，其余段按 object 收录并告警。
   **不猜维度**——「film grain」被当成物体，比「不知道它是什么」更糟。

```bash
# A1111 的整块 parameters 直接喂（negative 会自动切出来进 constraints.avoid，比例进 canvas）
python -m moss_llm.smith decompose examples/a1111-parameters.txt --target qwen-image-2_1
# 或从图内嵌元数据取 prompt（零模型、零 GPU）
python -m moss_llm.smith decompose --image photo.png --target moss-image
# 想要结构（style / camera / lighting）就给一个 LLM
python -m moss_llm.smith decompose examples/a1111-parameters.txt \
  --base-url http://127.0.0.1:8080
```

```python
from moss_llm.smith.decompose import decompose
ir, report = decompose(text, llm=None)        # llm=None → 字面量归位
report.method, report.injected, report.dropped_facts
report.coverage, report.missing               # 源内容词有多少进了 IR（None = 中文源）
```

**最后一道是「按源算」的覆盖面**，不是按 IR 算。为什么必须分开：`fixed_recall` 的分子分母
**都来自 IR**——IR 里没记的事实，它一个也抓不到。真机实测（Qwen3-VL-8B，同一份 A1111）：

| 轮次 | 发生了什么 | `fixed_recall` | 源覆盖 |
|---|---|---|---|
| 1 | 只留 2 个实体，整段环境句丢掉 | **4/4 满分** | — |
| 2 | 加 `coverage()` + 整段对账 | 4/4 | 77% → **88%** |
| 扫描路径（同输入） | 字面量归位 | 4/4 | **92%** |

所以 `reconcile` 会做一件看起来「不聪明」的事：源里某一段的内容词在 IR 里几乎找不到，
就按 object 原样补回来。**宁可粗，不可丢**——粗能看见，丢看不见。

### 6.8 往返校验：不需要生成器的最便宜一层

```
IR ──编译──▶ prompt ──读回事实──▶ 事实集合相减 ──▶ 丢（欠表达）/ 多（幻觉）
```

- **丢** = IR 有、prompt 没有。多半是预算砍掉了、渲染路径没接上、或方言吞了某个维度。
- **多** = prompt 有、IR 没有。编译器每句话都该能追溯回一条事实。

判据是「丢 = 0」；「多」只提示不判死（颜色/方位的同义改写会误报）。

```bash
python -m moss_llm.smith roundtrip examples/02-poster-text-layout.json
```

三种情况会明确标「不适用」，而不是假装通过：

| 情况 | 为什么 |
|---|---|
| `task="edit"` | edit 方言的规则是「保持项只点名、不描述外观」，年龄/颜色本来就不该写进指令 |
| JSON 方言（MOSS Layout / FLUX bbox） | 事实写在结构里，方位由坐标承载，不按词核对 |
| 多行文字块 | 按行核，不按整块核：拆成两句、被 JSON 转义成 `\n`，内容其实都在 |

这一层顺手抓出过两个真 bug（§9「这一轮测试发现的」）。

### 6.9 回归集：把「上次是这样的」变成断言

`examples/` 是设计时挑的四个干净样例。节点真正会拿到的输入是**真机反推出来**的 IR：
多格漫画的重复实体、UI 截图的 8 个文字块、storyboard 的 4 条观察散文。
所以四份真机 IR 进了 `tests/fixtures/reverse/`，× 四个后端的确定性输出钉成 sha256：

```bash
python -m unittest tests.test_core.TestRegressionSet -v   # 每次都跑
python tools/freeze_reverse_set.py --check                # 只对账，不写
python tools/freeze_reverse_set.py                        # 重建基线（要有理由）
```

规则只有一条：**输出变了，要么是 bug，要么是有意的改动，而后者只有人能判断。**
所以更新基线不写在测试里（测试不许自己改自己），跑脚本重建，并在提交信息里写清原因。
破坏版验证过：手改一个哈希，测试立刻红并指出是哪份 IR × 哪个后端。

钉住的不止输出：四份 IR × 四后端的**不变式全绿、往返零丢失、lint 零 error、结构摘要不变**。
结构摘要先变，说明 `normalize()` 悄悄改了排序或裁剪——那比文字变了更该警觉。

---

## 7. 自审闭环（没有图像生成器时的替代方案）

```
IR ──编译──▶ prompt ──审计(图+prompt)──▶ 逐条 claims + 漏项 ──▶ 补丁 IR ──▶ 重编译
                                                    │
                                          ≤2 轮，分数不涨就停
```

审计不是打分器，是**对照器**：让同一双眼睛把 prompt 念回来，逐条核对「这句在图里成立吗」。
三个防「假审计」的机制：

1. **空审计 = degraded，不算通过。** 实测给一个完全错配的 prompt，8B 直接返回 `{"score":0,"missing":[],"wrong":[],"ok":[]}`——看着像「审完了没问题」。
2. **漏项扣分。** 只算 claims 会自相矛盾：实测一张 UI 截图，claims 全对所以 100 分，但标题和标签一个没提。现在 `score = claims命中率 − 12×漏项数`（上限扣 40）。
3. **主角不因审计一句话被删。** 只删 importance < 0.7 的实体；高重要度实体只在日志里记一笔。

---

## 8. 静态检查 18 条

L001 超预算 · L002 质量词 · L003 权重语法错配 · L004 负向写进正向 · L005 采样参数泄漏 ·
L006 人体无年龄 · L007 多主体未绑定 · L008 属性矛盾 · L009 画师/IP 违规 · L010 图内文字未加引号 ·
L011 坐标非法 · L012 相机型号孤立 · L013 空泛副词过密 · L014 同义堆叠 · L015 能力越界 ·
**L016 凭空生成文字** · **L017 年龄被改写** · **L018 否定式**

L016–L018 不是设计时想到的，是 2026-10-31 本机跑批抓到的真实输出。
L008 在 2026-10-31 被改了作用域：全局属性（白天/夜晚）扫全文，主体属性（坐/站、发色、表情）只在同一实体内判——
四格漫画里「女坐男站」曾被误报成矛盾。

---

## 9. 真机实测（2026-10-31，RTX 5070 12GB）

模型：`Qwen3-VL-8B-Heretic-1.3.0.i1-Q4_K_M`（5.0 GB）+ `mmproj-Qwen3VL-8B-Instruct-Q8_0`，llama.cpp b9596，`-ngl 99`。

### 正向（10/10 通过）

| 用例 | 目标 | token | 耗时 | 结果 |
|---|---|---|---|---|
| 01 写实人像 | moss-image | 145 | 1.5s | 0 err |
| 01 写实人像 | qwen-image-2_1 | 328 | 2.8s | 0 err |
| 01 写实人像 | krea2 | 189 | 1.8s | 0 err |
| 01 写实人像 | flux3 | 102 | 1.0s | 0 err |
| 02 海报+文字 | moss-image | 286 | 0.0s | 0 err（Layout 走确定性路径） |
| 02 海报+文字 | flux3 | 245 | 0.7s | 0 err |
| 03 双主体 | qwen-image-2_1 | 219 | 1.8s | 0 err |
| 03 双主体 | krea2 | 235 | 1.9s | 0 err |
| 04 编辑指令 | qwen-image-2_1 | 24 | 0.0s | 0 err |
| 04 编辑指令 | flux3 | 41 | 0.0s | 0 err |

### 反向（洗图）

| 图 | 实体 | 产物 | 结果 |
|---|---|---|---|
| 768×768 写实头像 | 1 | MOSS 散文 236 token / Qwen 236 / Krea 235 / FLUX3 153 | 年龄 30 岁正确；自动判为「不需要布局」 |
| 1536×1536 四格漫画（中文气泡） | 6 | MOSS Layout 743 token，5 个 item | 3 条中文气泡逐字正确；8/8 坐标落在正确象限；`canvas: multi_panel` |
| 1680×1000 UI 截图（加文字专线前） | 4–6 | MOSS Layout 605 token | **失败**：模型只描述照片区，UI 文字一个没提取 |
| 1680×1000 UI 截图（加文字专线后） | 6–11 | MOSS Layout 511–987 token，20 个 item | **可用**：中文 UI 文案逐字正确，`canvas: single`，L010/L005 清零 |

耗时：加载 6.5s；取证一次 8–12s；文字专线一次 3–5s；审计一次 4–6s。

> 同一张 UI 截图连跑两次，T=0.2 时一次 11 个实体、一次 6 个，差的 5 条全是文字块。
> 取证任务上**方差就是缺陷**，所以默认温度已降到 0.1。

### 这一轮：往返校验顺手抓到的两个真 bug

| 现象 | 根因 | 修法 |
|---|---|---|
| Krea 2 把一块两行字写成 `""一杯温暖" "一刻从容""` | 兜底补引号时不认「整块已带引号」，按行各补一次，双引号把一块字拆成两块 | 先判整块（比对**空白折叠后**的正文），再退回按行 |
| MOSS Layout / FLUX bbox 的 JSON 被读成 24 条「多出」 | 词法扫描把 `":[-234,-480,286,26]}` 当成引号文字 | 认「结构化方言」直接不扫；方位改按坐标核对 |

还有一个**判据本身**的问题：`fixed_recall` 的分子分母都来自 IR，一份什么都不记的 IR
也能拿 1.0。所以 ④ 的验收除了「等于 1.0」，还要求那条事实确实出现在 `hits` 里
（`test_the_acceptance_is_not_vacuous`）。

---

## 10. 已知边界（不要当已验证）

| 项 | 状态 |
|---|---|
| 密集小字 / UI 截图 | **可用但不满分**。文字专线能逐字抄回来，但位置只到九宫格精度，不是像素级。真正要 OCR 还得接 RapidOCR/PaddleOCR（本机 pip 装不上，未验证） |
| 反推的 bbox 精度 | 差。实测四格漫画 7 个框错 3 个，靠 `anchor` 换通道救回来。**不要把它当分割用** |
| 洗图闭环打分 | 只有「审计分」，没有 VQAScore / DINO / OCR F1。审计分衡量的是「prompt 有没有说错说漏」，不是「重生成像不像」 |
| FLUX.1 / Illustrious 后端 | **未实现**。registry 有矩阵，走通用后端（tag/prose + 负向），缺原生方言 |
| token 计数 | 有 LLM 时用 `/tokenize` 真实计数；无 LLM 时启发式（±15%） |
| 审美质量 | **工具不判**。只给指标和路径，交人眼 |
| 长图内文字渲染 | Qwen 官方明说会错字漏字，只适合标题短语 |
| FLUX 3 bbox 最小尺寸 | 官方说 <40×25 px 常失败，本版未做像素级校验 |
| **提示词输出语言（图像后端）** | **只有英文**。Qwen-Image 原生吃中文 prompt，但确定性渲染器的模板全是英文，`--lang zh` 不是加个开关的事（要重写 `clauses.py` 的整句拼装）。中文输入→英文输出是当前的取舍 |
| 通用后端的负向 | 只搬运 `constraints.avoid` 和 registry 的 `default`，**不自己编**。sdxl 这类没写 default 的目标，负向就是空 |
| 元数据的覆盖范围 | 只认 A1111 系 / ComfyUI / InvokeAI / NovelAI / EXIF UserComment 五种。**平台转发基本会剥干净**（微博、推特、微信）；从这些地方存的图只能走 VLM 反推 |
| 元数据 ≠ 这张图 | 读到的是**当初写的** prompt。img2img、局部重绘、手改都会让它和画面不符，所以它只进 `provenance`，不覆盖反推结果 |
| 内置 YAML 子集 | 只够读**我们自己的** registry 与 IR。锚点、块标量、多文档都会**抛错**而不是猜——别人的花式 YAML 请装 PyYAML |
| 扩写的体检 | 只查**能确定性判的**：固定项、造数、造字、长度、质量词。**查不了「写得美不美」「光方向对不对」**——那些要靠重生成 + VQAScore，本版没有 |
| 扩写的长度带 | `standard` 有出处；`brief` / `detailed` 由 standard 推导，**不是官方值**。用 `--length` 前请自己确认一次该目标真的吃这个长度 |
| 扩写的候选 | 一次采 N=4 择优（命中「硬错为零且长度带内」提前停），**但真实 8B 模型的采样多样性未验证**：本机温度 0.4 下四次可能几乎一样，这时代码会明确报「搜索没有收益」 |
| 读 prompt 的无模型路径 | 只做字面量归位（引号/年龄/焦距/方位），**其余段一律按 object 收录**。它不判 style / camera / lighting——要结构就得给 LLM |
| 读 prompt 的颜色归属 | 只认「和引号同段」的颜色（那是招牌字色）。其余颜色落 `atmosphere`：一段 prompt 里的颜色可能属于招牌、道具或光，盲挂到人身上会得到「wearing white」 |
| 往返校验的「多」 | **只提示不判死**。颜色与方位的同义改写（"top-right" vs "in the upper right corner"）会误报 |
| FIXED 的抽取 | 从 IR 自动抽（文字/年龄/颜色/方位/must_keep，以及 H3 的台词/时间戳）。**用户在正文里随口说的「红色」如果没进 IR，就保护不到**——想固定什么，写进 IR 的 `must_keep` 或 `constraints.fixed` |
| H3 的 `non_diegetic_music` | 默认按「后期铺 BGM」的产线口径判：必须**严格等于** `N/A`。要写配乐散文用 `--music any`，否则会一直报错 |
| H3 的三线判据 | 需要 `--ir` 才有片长。**没有片长就不判**，提示里会写明「没判」——不假装通过 |
| H3 的六段式（全参考） | **未实现**。本版只做基础模式 T2VA / I2VA / FL2VA / L2VA 的三字段；六段式（`subject_definitions` / `summary` / `retention_analysis` / `detailed_description` / …）是另一套骨架，且官方改写 LoRA 也只覆盖 T2VA |
| H3 的改写质量 | **LoRA 未真跑过**：基座 `Qwen3.6-27B` 不在盘上，`h3 doctor` 会报 MISS。当前能跑的是「同一套官方模板 + 任意模型」，质量低于挂 LoRA |
| H3 的正则判据 | 数量词/否定式靠正则，会漏也会误报（`一个面包师` 会被当数量词 warn）。它们的作用是**分配注意力**，不是判决 |

---

## 11. 下一步

**唯一的任务清单在 `docs/VISION-2026-10-31-终极目标与证据阶梯.md`（交接书）§3。**
这里不再重复——两份清单必然分叉，所以只留一份。

交接书里每条都带**验收标准**（怎么算做完）、依赖与优先级。摘要：

- **P0**（无新依赖）：扩写改成搜索 ✅、不变式 + metamorphic ✅、度量元评估（未做，需要人 2 小时）
- **P1**：文字 prompt → IR ✅（`decompose`）、往返校验 ✅（`roundtrip`）
- **P2**（需生成器）：VQAScore 重生成闭环、分块反推、OCR
- **P3**：SDXL 原生 adapter ✅、`--lang zh`（未做，见上表）、回归集固化 ✅
- **P4（v0.5 新增）**：H3 基础模式 ✅（IR ⇄ 三字段终稿 + 机检 + M6）、六段式（未做）、
  Rewriter LoRA 真跑（卡基座）

### 权重选择的依据

选型口径：
- **能用性下限锚点是 Q4 级（≈4.8 bpw）**，IQ2/IQ1 只回答「装不装得下」。
- 12G 档推荐 `flux-3-image-klein-pup UD-Q4_K_XL`（7.92 GB，英文强，中文方言不可用）。
- 本次实测用 `Qwen3-VL-8B-Heretic Q4_K_M`：Q4 级、5.0 GB、与 Qwen-Image 2.1 的文本编码器同族。
- ⚠️ Heretic 是 abliterated 血统，**不可作为产品默认**。产品默认应换官方 Qwen3.5-9B IQ4_XS 或 Qwen3-VL-8B 官方版。
