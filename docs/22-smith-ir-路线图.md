# 提示词工匠 · 交接书

> 2026-10-31 定稿。这份文档替代此前的《终极目标与证据阶梯》，并吸收其全部结论。
> 它只回答四件事：**要去哪、现在在哪、接下来做什么、凭什么说做成了**。
>
> **消费方是 ComfyUI 节点，不是命令行。** 工具以纯 Python 包的形式被节点 `import`，
> LLM 由本地 llama-server 提供。命令行只是调试壳，不进交付路径（§2.5）。
>
> 现状细节看 `README.md`；扩写专项看 `docs/EXPAND-2026-10-31-扩写的目标与判据.md`；
> 业界对照看 `docs/RESEARCH-2026-10-31-业界对照.md`。
> **代码在哪、想改 X 该动哪个文件**：§9（含契约面与查找表）。

---

## 0. 一页交接摘要

**这是什么**：一个本地离线、零云依赖的提示词编译器。一份模型无关的场景 IR ↔ 各开源
文生图模型的原生方言，三个方向：洗图（图→prompt）、扩写（IR→prompt）、读 prompt（prompt→IR）。

**现在能做什么**（都能跑，见 §3）：五个**图像**后端确定性编译、**一个视频后端（H3）的三字段
终稿**、VLM 三段式洗图、零模型读图内元数据、**带搜索与体检的扩写**、**6 条不变式体检**、
通用后端（加 YAML 即用）、**读 prompt（文字 → IR）**、**往返校验（IR → prompt → 事实差）**、
**H3 机检闸（28 条铁律里可机检的子集）**。

H3 不是「第六个图像后端」：它的提示词是**一条时间线**，所以 `timeline` 成了 IR 的一等
字段（§4 P4）。这也顺手暴露出一个判据设计问题——**「同架构」不等于「同权重」**：
盘上的 Qwen3.8-27B 与 LoRA 基座 Qwen3.6-27B 张量名全部一致，llama.cpp 会**静默加载成功**
并产出语法正确、语义无效的输出。这类静默失败正是这个项目存在的理由，所以写进了 `h3 doctor`。

**先跑这两条确认环境没坏**：

```bash
python -m unittest discover -s tests          # 335 条，零 GPU 零网络
```

```python
from prompt_artisan import invariants as INV
from moss_llm.smith.adapters import get_adapter
from moss_llm.smith.ir import IR
from moss_llm.smith.registry import Registry

reg = Registry.load()
ir = IR.load("examples/02-poster-text-layout.json")
ok, line = INV.summarize(INV.run(get_adapter("moss-image", reg), ir))
assert ok, line          # 6 条关系（M6 只在有 timeline 时适用）
```

**三条必须带走的结论**：

1. 终极目标是**可证伪的编译**——能事前预测每个后端会怎样失败，并用实验验证预测。
   前两层（编译器、IR 资产）只是手段。
2. 结构上最缺的那条边 **prompt → IR 已补**（`decompose.py`，§4 P1④），往返校验因此有了
   第二次的 IR 可比。但它现在的天花板是**抽取质量**，不是架构：LLM 路径真机未验。
3. **不要做「一套通用模板」**。四个模型的提示词哲学互相矛盾，统一模板必然在某个模型上
   静默失败。差异全部下沉到 `registry`，代码里不许有模型参数。

---

## 1. 终极目标

**把「提示词」从靠手感的字符串，变成一份可移植、可裁剪、可证伪的中间表示。**

拆成三句：

- 让「像不像」**可测**（有 ground truth，有指标，有显著性）
- 让「好不好」**可比**（成对比较 + 一致性系数，而不是一句「感觉更好」）
- 让模型换代时，我们的资产**不贬值**（IR 不动，只换 target）

| 层 | 目标 | 判据 | 状态 |
|---|---|---|---|
| 表层 | 一个提示词编译器 | 同一 IR → 四个方言，确定性、可单测、毫秒级 | **已达成** |
| 中层 | IR 成为**资产** | 换新模型：加一段 registry 就能用；换模型家族：IR 一个字不用改 | **已达成（2026-11-01）**：8 个 target；新家族 Nano Banana 纯 YAML 接入、零 Python 改动（docs/29） |
| 终极 | **可证伪的编译** | 对每个后端能**事前预测**它会怎样失败，并用实验验证预测 | **部分（2026-11-01）**：3 条 pitfalls 已成为可证伪假设并做了 A/B（docs/30）；其余仍是断言 |

第三层是分水岭。前两层只证明「我们能把 A 写成 B」，第三层才证明「我们真的理解 B」。
判据很硬：**预测错了，就是理解错了**，得改 registry 而不是改说辞。

`registry.pitfalls` 已经是预测清单的雏形（「相机型号写无效」「SLG 不是画风开关」
「正文残留写实词会压制开头画风词」）。现在它们是**断言**，还不是**可证伪的假设**——
差别在于有没有做 A/B 去验证。

### 结构：三个方向，一个中枢

```
        image  ←──────────  反推（三段式，已有）
          │  ╲
          │    ╲  洗图 / 零模型元数据
          │      ╲
          ▼        ▼
         IR  ────────▶  prompt   编译 / 扩写（已有）
          ▲              │
          └──────────────┘
          prompt → IR（已补，单测）
```

六条边，现在六条都在。缺的那条 **prompt → IR** 曾同时卡住三件事（转方言、扩写闭环、
往返校验）——补上之后三件事都能跑，但**能跑不等于跑得准**：抽取质量要真机验（§3）。

---

## 2. 在 ComfyUI 里怎么接

### 2.1 依赖面

| 项 | 要求 |
|---|---|
| Python | 3.9+，只用标准库 |
| 第三方包 | **零**。没有 PyYAML 时走内置子集解析器（`miniyaml.py`），输出逐字节相同 |
| GPU | 编译与体检路径**不需要**。只有 `mode="llm"` 和洗图需要 |
| 网络 | 不需要。llama-server 跑在本机 |

ComfyUI 的 Python 环境装第三方包经常炸（版本冲突、离线 index），所以这条不是洁癖：
**把依赖降到零，节点才能被任何人直接放进去用。**

### 2.2 唯一的接口契约：一个 `chat` 方法

适配器不关心你用什么后端。它只要求传进来的 `llm` 对象有：

```python
def chat(self, system: str, user: str, temperature: float = 0.3,
         max_tokens: int = 1200) -> str
```

就这一个方法。仓库自带的 `LlamaServer` 满足它，你自己写的 HTTP 封装也满足它。
（洗图另需 `chat_vision(system, user, data_url, ...)`，扩写不需要。）

### 2.3 节点里的最小用法

```python
from moss_llm.smith.registry import Registry
from moss_llm.smith.ir import IR
from moss_llm.smith.adapters import get_adapter
from moss_llm.smith.llm import LlamaServer

REG = Registry.load()                       # 进程级单例，别每次节点执行都重读

# 连已经跑着的端点（推荐：ComfyUI 里本来就有一个常驻 llama-server）
llama = LlamaServer(base_url="http://127.0.0.1:8080")
llama.start()                                # 有 base_url 时只做健康检查，不拉进程

ir = IR.from_dict(node_input_dict)           # 或 IR.load("scene.json")
ad = get_adapter("moss-image", REG, llm=llama)
ad.length = "standard"                       # brief / standard / detailed
ad.gen_candidates = 4                        # 采样数；1 = 关掉搜索
out = ad.realize(ir, mode="llm")

out.positive      # 交付给采样器的 prompt
out.negative      # 没有负向通道的后端这里是 None
out.params        # 该后端的采样参数（steps/cfg/…），已按 registry 填好
out.expand        # 扩写体检报告 dict（固定项命中、造数、造字、长度带）
out.warnings      # 人话警告，直接抛到节点 UI 上
```

三种模式，按节点需要选：

| 调用 | 用不用 LLM | 耗时 | 用途 |
|---|---|---|---|
| `realize(ir)` | 不用 | 毫秒 | 确定性编译，可复现，适合当默认 |
| `realize(ir, mode="llm")` | 用 | 秒级 × 采样数 | 扩写，方差靠搜索压 |
| `INV.run(ad, ir)` | 不用 | 毫秒 | 不变式体检，适合放在节点执行前做自检 |
| `expand.verify(ir, text, target, REG)` | 不用 | 毫秒 | 体检任何来源的 prompt（人写的也算） |
| `decompose(text, llm=None)` | 可选 | 毫秒 / 一次调用 | 文字 prompt → IR；不给 LLM 就走字面量归位 |
| `roundtrip(ir, ad)` | 不用 | 毫秒 | IR → prompt → 事实差（丢 / 多） |

### 2.4 三个容易踩的点

1. **不要在节点里 `start()` 一个 server。** ComfyUI 执行是热路径，起进程会把队列卡住。
   要么连常驻端点，要么把 `LlamaServer` 在节点类初始化时建一次、复用。
2. **`adapter.length` 必须显式设。** 默认 `standard`；FLUX 3 的 `standard` 只有 40–90 词，
   MOSS 是 150–300。同一个 IR 换目标，长度目标本来就不同——这是方言参数，不是全局真理。
3. **`out.expand["verdict"] == "fail"` 要让人看见。** 它意味着模型改了年龄、编了数字或编了
   图里的字。静默通过 = 用户拿到一张错图却不知道为什么。把 `warnings` 直接接到节点输出。

### 2.5 命令行是什么定位

`python -m moss_llm.smith ...` 保留着，但**只是调试壳**：复现某个 IR 的输出、批量跑
`out/live/`、在没有 ComfyUI 的机器上验证环境。它不是交付面，也不该出现在用户文档的主路径里。

---

## 3. 现状快照（只写验证过的）

| 能力 | 状态 | 证据 |
|---|---|---|
| 四后端确定性编译 | **实测** | `out/live/` 10/10；token 与官方上限一致 |
| 反推三段式（含文字专线） | **实测** | 1680×1000 UI 截图：中文文案 9–20 条逐字正确；四格漫画 3 条气泡逐字正确 |
| 零模型洗图（图内元数据） | **单测 + CLI** | A1111 / ComfyUI / InvokeAI / NovelAI / JPEG EXIF 五种方言；合成图跑通，**真实生成图未批量验** |
| 扩写（账本 + 采样 + 体检 + 回炉） | **真机 1 次 + 单测 6 条** | Qwen3-VL-8B / MOSS / `brief`：100 词（带 70–120）、固定项 3/4、零造数零造字 |
| 扩写搜索（N=4 择优） | **单测（固定牌堆，24 次）** | `fixed_recall` 方差 0.0312 → 0.0000；离带距离 18.6 → 0.0；词数方差 6994 → 2600（−63%） |
| 扩写体检 `verify` | **单测** | 12 条用例：改字/造数/造字必 error，长度带外必 warn |
| 不变式体检（6 条关系） | **单测** | 图像后端 5 条 × 4 后端 × 3 示例 = 12 组合全绿；M6（时间线往返）只在有 timeline 时适用，H3 上 3 镜全对位。**每条都有破坏版测试**（§6） |
| H3 基础模式（IR ⇄ 三字段终稿） | **单测 + 冒烟** | 渲染/解析/M6 三向闭环；解析回来的稿再渲染**收敛**（二次往返逐字节相同）；18 条机检判据每条都有「正例不报 + 破坏版必红」 |
| H3 全参考六段式（profile `six-section`） | **单测（3 段真稿逐字节往返）** | 六段固定顺序 + 参考标签 + `[Shot N]`/`At MM:SS.mmm`；17 个独立错误码（缺段/段序/时间戳越界…）；与三字段并存不替换；见 docs/27 |
| 中文整句（`--lang zh`） | **单测 + 人读签收** | 中英事实集合相等；默认 en 输出逐字节不变；10 条中文输出经 Lead 人读「无英文语序」；见 docs/28 |
| 跨模型家族 registry（Nano Banana） | **单测（纯 YAML）** | 不改一行 Python 就编译 4 份真机 IR，invariants 与 roundtrip 全绿；抽象漏项如实登记；见 docs/29 |
| G7 第一轮（20 图 × 2 模型，binding 账本） | **实测（AI 代理目检）** | 五类绑定覆盖率 + 40 行逐图矩阵；结论「样本不足」；两类共同缺陷（character 误标 53%、JSON 重复循环 4/40）已定位；见 docs/G7-反推质量-第一轮.md |
| pitfalls A/B（3 条） | **实测（AI 代理目检）** | 3 条断言在本轮条件下全部被推翻，registry 已按实测改写（保留原说辞 + 标注）；见 docs/30 |
| 洗图基准（caption → 生成 → triage） | **实测（2/3 可用后端端到端）** | moss-image / krea2 各 4 图跑通；qwen-image 加载阶段未成（与宿主看门狗重启时间线一致，未独立复核）、flux 系无生成权重（如实登记）；triage 为未校准代理指标、只作诊断；见 docs/31 |
| H3 的机检子集 | **单测** | 判据逐条对应铁律编号（§4.1 表）；两处「判据自己踩自己」有专门用例：铁律 14 要求的「无任何文字与符号」不算否定式、时间戳不算画幅参数 |
| H3 改写 LoRA | **未验证** | 权重在盘、`llama-server --lora` 支持、输入模板逐字照抄官方；**基座 Qwen3.6-27B 不在盘上**，`h3 doctor` 报 MISS。同架构的 Qwen3.8-27B 会被 llama.cpp **静默加载成功**（实测张量名全部同名）——已写进 doctor 当红旗 |
| 读 prompt（文字 → IR） | **实测 + 单测** | A1111 整块 parameters → Qwen：`fixed_recall = 1.0`；源内容词覆盖 **92%（scan）/ 88%（llm）**。LLM 路径真机跑过 Qwen3-VL-8B（单次调用、4.5s 加载）；空抽取**报错不兜底** |
| 读 prompt 的漏抽兜底 | **实测** | 8B 首轮只留 2 个实体、整段环境句全丢（覆盖率 51%）；`reconcile` 把 4 段补回后升到 88% |
| 往返校验（IR → prompt → 事实差） | **单测** | 3 示例 × 4 后端，丢失 **0**；故意删一句必报；edit / JSON 方言标「不适用」 |
| 通用后端（加 YAML 即用） | **实测** | `--target sdxl` 走 tag 形态，预算 77 token 填满 |
| SDXL 原生后端（tag + 分块） | **单测 + 回归基线** | 前三示例切成 2–3 块、每块 ≤75 token、`dropped` 为空、往返零丢失；**对照**通用后端在同一份 storyboard IR 上会砍子句 |
| 打包 / 零依赖 | **实测** | `pip install . --no-deps` 后异地可用；无 PyYAML 时走内置解析器，输出逐字节相同 |
| 静态检查 | 单测 | 18 条，零 LLM |

**没有验证过的，一律写「未验证」**：真实 A1111 图（手里没有）、OCR、VQAScore、中文输出、
跨模型家族（Flux 系 vs Qwen 系）的 IR 复用、扩写在**真实 8B 模型上的方差**（现在只有固定
牌堆的模拟数字）。SDXL 分块只在单测与回归基线上验过，**没有真跑过 SD 采样器**——
token 计数用的是启发式（±15%），真机可能切得比预期碎。

---

## 4. 后续改进方向

按「性价比 × 依赖」排。每条都给**可判死的验收标准**——没有验收标准的任务不许开工。

### P0 · 不引入新依赖 —— **已完成两项**

**① 扩写改成搜索** ✅
- 做了什么：一次采 N=4 个候选，用 `expand.Report.score()` 的**分层键**排序取第一；
  命中「硬错为零且在长度带内」提前停；采样去重；模型返回空按失败处理。
- 实测（固定牌堆 24 次，隔离回炉变量）：
  `fixed_recall` 方差 **0.0312 → 0.0000**，均值 0.375 → 0.500；
  离带距离均值 **18.6 → 0.0**、方差 743.5 → 0.0；词数方差 6994 → 2600（−63%）。
- 成本：上界 4×，命中即停，常见 1–2×。
- 未验证：真实 8B 模型的采样多样性（本地温度 0.4 时可能四次几乎一样——
  这种情况下代码会明确报「搜索没有收益」，而不是假装做了搜索）。

**② 不变式 + metamorphic 关系** ✅
- 做了什么：`moss_llm/smith/invariants.py`，5 条关系 + `run()` + `summarize()`；
  每条关系配一个**破坏版测试**（把实现改坏，关系必须变红）。
- 实测：12 个「示例 × 后端」组合全绿；顺带抓出 3 个真问题（§6）。
- 关键取舍：原本要写的「加预算 ⇒ 保留集合变大」**是假的**，改成了三条真能保证的性质。

**③ 度量元评估**（未做）
- 做什么：取 30 个样本，人工排序 + 自动分，算 Spearman。
- 为什么：现在所有分数都没证明过与人类判断相关。
- 验收：报告 ρ；**ρ < 0.5 就把这个分数从 README 删掉**。宁可没有分数，也不要没人验证过的分数。
- 依赖：需要人（30 个样本，约 2 小时）。

### P1 · 补 prompt → IR —— **已完成**

**④ 文字 prompt → IR** ✅
- 做了什么：`decompose.py`。三步——① 确定性扫字面量（引号文字 / 年龄 / 数字 / 颜色 /
  方位 / 比例 / `Size` 推画幅）；② 有 LLM 才做结构化抽取；③ **对账**：模型漏掉的硬事实
  补回来，补不进去的撤回。
- 为什么这么设计：抽取的真假不能取决于模型这一次写得全不全。扫描是**事实来源**，
  模型只做加值——它的方差只影响「描述多细」，不影响「事实对不对」。
- 附带一条硬规则：**IR 里不许留没有渲染路径的事实**。记进去却 compile 不出来，
  `verify` 就会报一条永远修不好的假错——那是 IR 在撒谎。注入后立刻自证可渲染，
  渲不出来就撤回并出声（`report.dropped_facts`）。
- 验收：A1111 的 `parameters` → Qwen 方言，`fixed_recall = 1.0` ✅（两条测试：
  验收本身 + 证明验收不是空的——那份 IR 什么都不记也能拿 1.0，所以还要求那条事实
  真的出现在 `hits` 里）。
- **真机三轮（Qwen3-VL-8B）**，每一轮都是上一轮暴露出来的：
  1. 首轮：`a 28-year-old **person**`（模型把性别写在 label 上、gender 留空）、
     雨衣被抽成独立物体、三个实体盖同一个 anchor、`35mm` 整条丢掉。
     → 修：`_age_phrase` 退回 label；`descriptors.color` 加渲染路径（不渲染就是 IR 撒谎）；
     提示词加「一个人是一个实体」「别给每个实体盖同一个 anchor」「焦距进 camera.lens_mm」。
  2. 次轮：改过头——只留 2 个实体，把「standing on wet asphalt at night」「neon signs
     reflection」「film grain」整段丢了，而 `fixed_recall` 仍是 **4/4 满分**、往返校验报
     「零丢失」。**这是本层最大的盲点**：所有指标的分母都来自 IR。
     → 修：加 `coverage()`（按**源**算内容词覆盖）＋ `reconcile` 的整段对账。
  3. 三轮：覆盖 77% → **88%**，四段全部补回，`bold` 与 `35mm` 也补回。
     扫描路径同口径 92%（差异只在 `frame` 这个词有没有保留）。
- 教训：**「IR 自洽」和「IR 完整」是两件事**。前者靠 `fixed_recall`，后者只能按源算。

**⑤ 往返校验** ✅
- 做了什么：`roundtrip.py`。IR → prompt → 事实集合相减：**丢**（欠表达）判死，
  **多**（幻觉）只提示。
- 实测：3 示例 × 4 后端 = 12 组合，丢失全 0。三种情况明确标「不适用」而不是假装通过：
  `task="edit"`（方言规则本就不写外观）、JSON 方言（方位由坐标承载）、多行文字块
  （按行核，不按整块核）。
- 顺手抓到两个真 bug（README §9）：Krea 2 给多行文字补引号时把一块字拆成两块；
  MOSS Layout / FLUX bbox 的 JSON 被词法扫描读成 24 条假「多出」。
- 依赖：④。

### P2 · 需要图像生成器

**⑥ 重生成闭环（VQAScore）**：验收——同 IR 的两版 prompt，VQAScore 差异显著（配对检验 p<0.05）。依赖 ComfyUI API（正好是消费方，接口成本低）。
> 2026-11-01 进展：洗图基准第一轮流水线已跑通 caption → 生成 → triage（`tools/wash_benchmark.py`），
> 但 triage 用的是**未校准的代理指标**，只作诊断、不作放行闸（见 `docs/31`）。VQAScore 本身仍未接。
**⑦ 分块反推**：2×2 / 3×3 切块分别取证再合并。验收——密集 UI 截图的文字召回提升 ≥20%。
**⑧ OCR 路**：接 RapidOCR/PaddleOCR。验收——文字框与人工标注 IoU ≥0.5。
（本机 pip 装不上，**接手前先确认能否装**，否则这条永远停在「未验证」。）

### P3 · 有价值但不急

**⑨ SDXL 原生 adapter**（tag 方言 + compel 分块）✅
- 做了什么：`adapters/sdxl.py`。**77 是每块上限，不是整段上限**——按子句边界切块，
  块间插 `BREAK`，一条不丢。`token_limit()` 返回 None，让基类**不要**按 77 装箱
  （装箱是「装不下就丢」，对分块后端是错的取舍）。
- 对照证据：同一份 321 词的 storyboard IR，通用后端 `dropped` 非空，原生后端 `dropped == []`。
  这就是「原生」与「通用」的分界。
- 顺手纠正 registry：`sdxl.edit.supported` 从 true 改成 false——inpaint 靠 mask 与区域提示词，
  **没有指令式编辑形态**。`_guard` 现在会明确拒绝，而不是静默渲染成 t2i。
**⑩ `--lang zh`**：要重写 `clauses.py` 的整句拼装，不是加开关。
> ✅ 2026-11-01 已做：中文整句走独立模块 `moss_llm/smith/zh.py`（英文路径一字未改，默认输出逐字节不变），
> CLI/API 加 `--lang zh`；10 条人读抽检由 Lead 签收。见 `docs/28-中文整句.md`。
**⑪ 回归集固化** ✅
- 做了什么：把四份**真机逆向**出来的 IR（多格漫画 / faceswap 参考 / storyboard / UI 截图）
  收进 `tests/fixtures/reverse/`，× 四个后端的确定性输出钉成 sha256（`expected.json`）。
- 为什么不能只靠 `examples/`：那四份是设计时挑的干净样例；真机 IR 带着模型给的脏结构
  （多格漫画的重复实体、UI 截图的 8 个文字块、4 条观察散文）。这类输入才代表节点实际拿到的。
- 纪律：基线用 `tools/freeze_reverse_set.py` 重建，**测试不许自己改自己**；
  输出变了必须说清「为什么变」，说不清就是回归。破坏版验证过：手改一个哈希，测试立刻红。
- 顺带钉住的四条：四份 IR × 四后端的不变式全绿、往返零丢失、lint 零 error、结构摘要不变。
**⑫ `importance` 一字段两职** ✅
- 拆成 `importance` + `typography.visual_weight`：后者在 `normalize()` 时由前者派生并
  **冻结**，此后 importance 只决定「谁先被砍」，不再改字号。
- 为什么必须拆：M5（提权不丢）原本把 text 实体整个排除——把一个**字段职责不清**的问题
  变成了验证层的洞：最不能丢的图内文字，恰好是这条关系唯一不检查的东西。拆完 M5 覆盖
  02 海报的全部 3 个实体（原来 0–1 个）。
- 向后兼容：老 IR 没有该字段时退回旧算法；四个示例的确定性输出逐字节不变。

---

### P4 · H3（v0.5 新增）

**⑬ H3 基础模式**（已做）
- 做什么：`timeline` 进 IR；`h3.render` / `h3.parse` / `h3.check`；`minimax-h3` 适配器；
  M6 时间线往返。
- 验收：IR → 三字段 → 再解析 → 再渲染**收敛**；3 镜的台词/时间戳/运镜全部对位。
- 证据：单测 49 条（渲染 6 / 规范化 4 / 解析 4 / 机检 18 / 适配器 4 / M6 4 / 事实 3 / 语法 3 /
  方言识别 1 …），`roundtrip` 零丢失，`invariants` 6/6。

**⑭ H3 六段式（全参考）**（✅ 2026-11-01 已做，profile `six-section`）
- 为什么不做：官方改写 LoRA 只覆盖 T2VA；六段式（`subject_definitions` / `summary` /
  `retention_analysis` / `detailed_description` / …）是另一套骨架，参考标签语义
  （`<Subject N>` / `<Picture N>` / `<Video N>` / `<Audio N>`）需要 IR 再长一维。
  先把基础模式的三字段打磨到能用，比同时铺两套骨架更值。

**⑮ Rewriter LoRA 真跑**（卡基座）
> 2026-11-01 决策备忘已备好（`docs/35-H3-LoRA基座决策备忘.md`）：两个 27B 候选档的体积/许可/sha、
> 12GB 卡的边界风险、预检与执行序列、一行式拍板问题。**T12 本体等用户拍板下载，未开工。**
- 卡在哪：基座是 `Qwen/Qwen3.6-27B`，盘上没有；同架构的 Qwen3.8-27B **能加载但不该用**。
- 现在的替代：同一套官方模板 + 任意 OpenAI 兼容端点（质量低一档，接口一致）。
- 一旦有了基座：`h3 rewrite --model <基座> --lora <适配器>`，**代码零改动**——
  这正是「接口相同」换来的。

**⑯ `--lang zh`（图像后端）**（✅ 2026-11-01 已做，与 P3⑩ 同一实现）
- 为什么一直不动：要重写 `clauses.py` 的整句拼装 + CJK token 计数口径 + 母语审校。
  加个开关做出「英文片段 + 中文标点」比不做更糟。
- 注意 H3 那条线**已经**是中文内容（台词逐字、声景可中文），但它走的是另一套骨架，
  不能反证图像后端的 `--lang zh` 变简单了。

## 5. 判据：凭什么说「改进了」

不同层级能证明的东西**不同，不能混用**。L0 全绿不代表 L2 好；L2 高也不代表人更喜欢。

| 层 | 问的问题 | 靠什么 | 客观性 | 成本 |
|---|---|---|---|---|
| **L0 形式** | IR 自洽吗 | schema + 不变式（M1–M5） | 完全客观 | 毫秒 |
| **L1 稳定** | 同输入同输出吗 | k 次重复的方差 | 完全客观 | 分钟 |
| **L2 保真** | 说的和图上一致吗 | 实体召回 / 文字 F1 / 固定项命中 | 客观（需标注） | 分钟 |
| **L3 生成** | 重生成后像不像 | VQAScore / GenEval | 客观但需元评估 | 小时 |
| **L4 偏好** | 人觉得哪个好 | 成对比较 + Krippendorff α | 主观但有统计纪律 | 天 |

- **L0/L1 已经能自动化**，而且是**唯一不需要相信任何人**的两层：不变式不需要 ground truth，
  方差不需要标注。这一轮把 L1 的方差从「感觉在抖」变成了具体数字。
- **L2 的 ground truth 来自图片本身**，是保真度的底线。
- **L4 是唯一能证明「优雅」的层**，但必须带一致性系数。α < 0.6 是噪声，不是结论。
- 自家调研已算出：LLM-as-judge 成对比较**翻转率 13.6%**，要 ~11 票才 95% 置信。
  所以**不能用 1 票下结论**。

### 「优雅」的可操作化

冗余度、信息密度（facts/100 tokens）、未支撑断言比、方差、裁剪损失。

**断言纪律**：任何「更优雅」必须写成「在 X 指标上比 Y 好 Δ，p<0.05，该指标与人类偏好
相关性 ρ=Z」。**写不出这三个数，就只说「我认为」。**

### 最强的客观标准是预测，不是解释

把 `pitfalls` 变成可证伪假设：「MOSS 写相机型号无效」→ A/B 两组生成 → VQAScore 配对检验
→ 无显著差异则假设成立。**不能被推翻的说法，在工程上等于零。**

---

## 6. 测试比人先发现的事

记录在这里，因为它们是「metamorphic 测试值不值」的唯一证据。

**① 关系数组的顺序会漏进输出。** 打乱 `relations` 数组，6 次里有 3 次输出不同
（所有后端都中）。关系是集合，数组顺序没有语义，但 `build_clauses` 按输入顺序追加子句。
修法：按 `(subject, predicate, object)` 排序后再追加。**两个人写同一份 IR 现在必然得到同一段 prompt。**

**② FLUX 3 的 `<silhouette_N>` 会引用不存在的 id。** 编号用 `enumerate(ir.entities)`，
中间夹一个没有 bbox 的实体，id 就变成 `s1, s3`，而 caption 仍写
`<silhouette_1>, <silhouette_2>`——`silhouette_2` 根本不存在，模型只会忽略它。
修法：先收集再按 `(top, left, desc)` 排序，连续编号。
（这个 bug 靠读代码很难发现，是写 M2 时顺带撞出来的。）

**③ 原本要写的 M4「预算集合单调」是假关系。** 反例：成本 4 的子句在预算 3 时被跳过、
短的那条补上；预算 4 时长的装下了，短的反被挤掉。穷举成本 3–12、长度 ≤6 的序列，
集合单调与条数单调都有反例；**只有「用掉的 token 不减」零反例**。
不改算法（前缀式装箱虽然单调，但实测 40 token 预算下一条都装不下，首条主体子句就 61 token），
改关系的表述。**测试发现关系本身是错的，这比测试通过更有价值。**

**④ `importance` 兼任字号。** M5 在 02 海报上失败，追下去发现 `describe_text` 用
`importance >= 0.95` 决定 "Large"/"Small"。提权合法地改写了描述——不是 bug，是字段职责不清。
**已修（§4 P3⑫）**：拆成 `importance` + `typography.visual_weight`。

**⑤ M5 一直在「检查 0 个实体」还说不出哪里不对。** 修 ④ 时发现的：M5 的「描述在不在
基准输出里」是**大小写敏感**的全文匹配，而 `capitalize_sentences` 会把每句首字母大写，
于是「恰好是首句」的实体会被整条跳过，报告还写着「描述未出现在基准输出里」——
读起来像结论，其实只是没比。02 海报在 qwen / krea2 上报的就是「检查了 0 个实体」，
而三条描述都在输出里。修法：两边都小写再比。
**这两个 bug 叠在一起**：一个让 text 被排除，一个让首句实体被排除，而它们恰好是
「最不能丢」的那批。**绿是结果，不是证据**——只有把「检查了几个」当断言来写，才看得见。

---

## 7. 不要做什么（防走回头路）

| 不要 | 因为 |
|---|---|
| 做「一套通用模板」 | 四个模型的哲学互相矛盾，统一模板必然在某个模型上静默失败 |
| 把长度当全局真理 | FLUX 3 要 40–90 词（自带 upsampler），Krea 2 要 250–380（512 硬截断）。写进 registry |
| 给扩写加总分 | 各项量纲不同、代价不同。一个 0–100 分会被当成结论 |
| 用 CLIPScore | 与人类判断弱相关。用 VQAScore |
| 假设用户能装包 / 有 WSL / 有 GPU | 本机 `pip install rapidocr` 就失败过。核心零第三方依赖 |
| 把「单测通过」说成「实测通过」 | §3 的表把两者分开写，就是为了这个 |
| 让 LLM 自由扩写 | 任务竞争 + 无账本 + 无校验 = 必造细节。必须账本 + 体检 |
| 在代码里写模型参数 | registry 是唯一事实源 |
| 用**集合单调**断言预算 | 它是假的（§6③）。断言它 = 制造一条永远红的噪声测试 |
| 把不变式当质量分 | 全绿只说明「没有不该有的依赖」，不说明「写得好」 |

---

## 8. 接手须知

### 环境

| 项 | 值 |
|---|---|
| LLM 权重 | Qwen3-VL-8B 级别的 Q4_K_M GGUF（位置由使用者决定，见 `paths.py`）|
| llama-server | 任意支持 `--lora` 的 llama.cpp 构建（放 PATH 或设 `MOSS_LLM_SERVER`）|
| Python | 3.13.12（managed） / 3.12.10（系统） |

### 四个踩过的坑

1. **`/tmp` 在 Windows 的 git-bash 里不是 Python 的 `/tmp`**——写临时文件用
   `tempfile.gettempdir()`，别用字面量 `/tmp`。
2. **`pip install .` 在离线 index 下会卡在 build isolation**——用 `--no-build-isolation`，
   而且**不能假设用户装得上任何包**。
3. **registry 必须随包发布**。它曾在仓库根的 `schema/` 里，`pip install` 之后就不在了。
   现在在 `moss_llm/smith/data/`，走 package-data。
4. **EXIF UserComment 的字符集声明经常是错的**。未声明时按零字节奇偶投票判断 UTF-16 字节序，
   不要信声明。

### 改动前的自检顺序

```
unittest → invariants（12 组合）→ 目标示例的 deterministic 输出 diff
```

确定性输出一旦变化，必须能说清**为什么变**。说不清就是回归。

---

## 9. 文件目录

### 9.1 代码

```
moss_llm/smith/                     核心包，零第三方依赖
  __init__.py             3    版本号
  ir.py                 218    IR 定义 / 加载 / 规范化；needs_layout() 的判定在这里；timeline 是一等字段
  clauses.py            497    IR → 子句计划（维度 + 重要度）；prose / tag 两种渲染
  layout.py             104    MOSS Layout：draft(abs/xyxy) → compiled(abs_centralized/yxyx)
  adapters/                   方言层，一个后端一个文件
    base.py             317    流水线骨架：装箱 → 渲染 → 消毒 → 扩写搜索 → 体检
    moss_image.py        89    MOSS：散文 + Layout JSON 双形态
    qwen_image_21.py    116    Qwen 2.1：长散文 + 结构化块
    krea2.py             75    Krea 2：512 token 硬截断，目标词数按信息量取位
    flux3.py            169    FLUX 3：短描述 + `<silhouette_n>` bbox 表 + 编辑指令
    sdxl.py             109    SDXL：tag + 75 token 分块（BREAK），切不砍
    h3.py               193    H3：整个换掉 realize（不走子句装箱）；官方模板逐字照抄
    generic.py           82    通用后端：只读 registry，加 YAML 即用
  registry.py            74    registry 加载与查询（prompt_policy / sampling / max_tokens）
  data/model-registry.yaml 225 唯一事实源：能力矩阵、长度带、采样档、pitfalls
  miniyaml.py           369    YAML 子集解析器（没装 PyYAML 也能读；读不懂就报错，不猜）
  tokens.py              64    token 计数：启发式 / 服务端 tokenize
  llm.py                477    OpenAI 兼容客户端；结构化输出逐档降级；chat / chat_vision
  api.py                 对外门面：IR 文本 → 提示词 + 体检（节点走这条）
  vision.py             124    图片预处理：EXIF 转正 / 透明合成白底 / 缩放 / JPEG 质量
  embedded.py           468    零模型洗图：读图内嵌生成参数（PNG chunks / JPEG EXIF）
  reverse.py           1096    图片 → 取证 JSON → 融合成 IR（三段式，含文字专线）
  decompose.py          640    文字 prompt → IR（字面量扫描 → 模型加值 → 对账；IR 不许撒谎）
  roundtrip.py          147    IR → prompt → 事实差（丢 = 欠表达，多 = 幻觉）
  audit.py              354    忠实度审计 + 补丁 IR + 闭环控制
  expand.py             502    扩写：固定项抽取 / 长度带 / verify / 分层排序 / 账本（含台词/时间戳事实）
  invariants.py         335    6 条 metamorphic 关系（M6 只在有时间线时适用）
  h3.py                1048    H3 方言：时间线规范化 / 渲染 / 反向解析 / 28 条铁律的机检子集
  cli.py               1034    调试壳（不进交付路径）；h3 check/parse/rewrite/doctor

schema/ir.schema.json         IR 的机器可读定义（仅文档用，运行时不用）
examples/                     5 个可跑 IR（人像/海报文字/多主体/编辑/H3 时间线）
  + a1111-parameters.txt + h3-t2va-sample.txt（H3 终稿样例）
  comfy_node_example.py       参考节点：契约面（§9.2）的可执行证据，不依赖 torch
tests/test_core.py          3273 行 / 335 条单测，零 GPU 零网络
tests/fixtures/reverse/      4 份真机逆向 IR + expected.json（**图像**后端基线；H3 不谈布局）
tools/run_live.py            107 真机跑批（正向 10 条）
tools/freeze_reverse_set.py   86 重建回归基线（不许测试自己改自己）
pyproject.toml                 打包：默认零依赖，[full] 才装 PyYAML / Pillow
docs/                          设计与交接文档（见 9.4）
out/live/  out/reverse/        真机产物，可删可重建（已 gitignore）
```

行数是给你判断**复杂度在哪**用的：`reverse.py`（1096）和 `clauses.py`（489）是两个真正
复杂的文件；五个后端文件平均 106 行（不含 `base.py` 的 317 行骨架），说明方言差异确实
被压进了 registry，而不是散在代码里。

### 9.2 契约面：改这些会波及 ComfyUI 节点

节点只依赖下面四样，其余都是内部实现：

| 契约 | 位置 | 承诺 |
|---|---|---|
| `get_adapter(name, registry, counter=, llm=)` | `adapters/__init__.py` | 名字与 registry 的 key 一致；未知名字报错而不是兜底 |
| `adapter.realize(ir, profile=, mode=)` | `adapters/base.py` | 返回 `Realized`；deterministic 模式必须幂等（M1 钉住） |
| `IR.from_dict` / `IR.to_dict` | `ir.py` | 节点的输入输出格式；`from_dict` 会自动 normalize |
| `llm.chat(system, user, temperature, max_tokens)` | 你的客户端 | 适配器只调这一个方法 |
| `decompose(text, llm=, negative=, params=)` | `decompose.py` | 返回 `(IR, Report)`；`llm=None` 走字面量归位；空抽取**抛错**不兜底 |

`Realized` 的字段：`positive`（交付物）· `negative` · `params` · `layout` ·
`warnings` · `token_count` / `token_limit` · `mode` · `dropped` · `expand`。

**可以随便改的**：内部函数、`cli.py`、lint 的编号与措辞、文档——只要 registry 的能力字段
语义不变。**改 `Realized` 的字段名要同步改节点。**

### 9.3 「我想改 X，该动哪个文件」

| 想改什么 | 动哪 | 改完必须跑 |
|---|---|---|
| 加一个后端（只是方言常量不同） | `data/model-registry.yaml` 加一段 | `targets` + `invariants` |
| 加一个后端（有专属逻辑） | 新建 `adapters/xxx.py`，在 `adapters/__init__.py` 注册 | 同上 + 该目标的确定性输出 diff |
| 改某后端的长度带 | `model-registry.yaml` 的 `prompt.length` | `verify`（长度带来自 registry） |
| 改「什么算错」 | `expand.py::_judge` | `TestExpandVerify` |
| 改「什么算硬事实 / 怎么算命中」 | `expand.py::hit`（verify 与 decompose 共用这一份口径） | `TestExpandVerify` + `TestDecompose*` |
| 改对账（补哪些事实、撤哪些） | `decompose.py::reconcile` | `TestDecomposeReconcile` + `unrendered()` 必须为空 |
| 改抽取提示词 | `decompose.py::SYSTEM` / `SCHEMA` | `TestDecomposeReconcile`（假模型） |
| 改往返判据 | `roundtrip.py::diff` | `TestRoundtrip` |
| 改了编译器，怎么知道有没有回归 | — | `python tools/freeze_reverse_set.py --check`（4 份真机 IR × 4 后端） |
| 改扩写择优的优先级 | `expand.py::Report.score` | `TestExpansionSearch` |
| 加一条静态检查 | `lint.py::check`，编号接 L018 | `TestLint*` |
| 加一条不变式 | `invariants.py::mN_*` + 注册进 `RELATIONS` | **破坏版测试必须一起加** |
| 改预算装箱 | `clauses.py::pack` | M4 + `TestClauses`（注意：集合单调是假的，别加回去） |
| 改 MOSS 布局编译 | `layout.py::compile_draft` | `TestLayout`（官方附录 B 算例逐位对上） |
| 换默认 registry 路径 | `registry.py::_DEFAULT` + `pyproject.toml` 的 package-data | `pip install . --no-build-isolation` 后异地跑 `targets` |

### 9.4 文档地图

| 文件 | 回答什么 |
|---|---|
| `README.md` | 怎么用、四个后端的方言差异、扩写与不变式的设计 |
| **本文** | 要去哪、现在在哪、接下来做什么、凭什么说做成了、代码在哪 |
| `docs/EXPAND-2026-10-31-扩写的目标与判据.md` | 扩写为什么不是「写长」、业界怎么做、判据表 |
| `docs/RESEARCH-2026-10-31-业界对照.md` | 与 DALL·E 3 recaptioner / FLUX 3 upsampler / Promptist / DSPy 的对照 |
| `docs/ARCH-v2.md` | IR 的字段与分层 |

**只有本文有路线图。** README 里不重复待办清单——两份清单必然分叉，所以只留一份。

---

## 10. 一句话

**六条边都通了：编译器能用，也能读懂 prompt 了，往返零丢失。缺的是第三层证据——
「重生成后像不像」。在此之前，所有关于「更好」的说法都只能写成「我认为」。**
