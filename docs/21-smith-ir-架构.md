# 提示词工匠 · 架构 v2.0

> **定位**：v1.0 是「一个模型的提示词手册」（MOSS-Image 单模型）。v2.0 是「编译器」：一次语义解析 → 一份 IR → N 个后端。
> **核验日期**：2026-10-31。所有跨模型事实来自实测抓取，未验证项在 §12 单列。
> **配套文件**：`schema/ir.schema.json`（IR 定义）、`moss_llm/smith/data/model-registry.yaml`（后端事实源，随包发）

---

## 0. 一句话结论

**不要做「一套通用提示词模板」。做「一次语义解析 + 一份 IR + N 个后端」。**

理由是一条实测事实：五大模型的提示词哲学**互相矛盾**，且矛盾是静默的——错了不会报错，只会出烂图。

| 模型 | 官方句式哲学 | 有效长度 | 负向 | 布局 | 编辑 | 文本编码器 |
|---|---|---|---|---|---|---|
| **Krea 2** | 越长越好（"long detailed prompts yield best results"） | **512 token 硬截断** | 无 | 无 | 开源版不支持 | Qwen3-VL-4B |
| **FLUX 3 Image** | 越短越好（模型自己扩写，只写要控制的点） | 8192 + 9B upsampler | 蒸馏版无 / Base 有 | 0–1000 网格 + `<silhouette_n>` | 支持，src/tgt bbox | Qwen3-VL-4B |
| **Qwen-Image 2.1** | 长散文 400–500 词，第三人称观察者 | 未声明（ctx 262k） | API 无 / 本地 true_cfg | 无 | 支持，`<imageN>` | Qwen3-VL-8B |
| **MOSS-Image** | 裸散文七维顺序 | ~4000 token | 有（SLG/uncond） | abs→centralized JSON | 不支持 | Qwen3.5-9B |
| **SDXL / FLUX.1** | tag 串 + 权重 | 77×N / 512 | 有 | 无 | inpaint | CLIP / CLIP+T5 |

**一个意外的工程红利**：4/5 个模型用 Qwen3-VL 家族编码器。**一个 tokenizer 就能覆盖大部分预算计算**，不必为每个模型接不同分词器。

---

## 1. v1.0 的 15 个缺口

| # | 缺口 | 后果 | v2.0 解法 |
|---|---|---|---|
| G1 | 没有模型无关的中间表示 | 加一个模型要重写全部模板，N×N 爆炸 | IR + 后端适配器（§3、§4） |
| G2 | 不查 token 预算 | Krea 2 静默砍掉 512 之后的全部内容 | 真实 tokenizer 计数 + 重要度排序（§7.2/7.3） |
| G3 | 假设「负向词」是通用能力 | Qwen API 静默忽略，FLUX 3 直接 422 | 负向三态编译（§7.4） |
| G4 | 只覆盖 t2i | 图生图/编辑/局部重绘全缺（用户核心需求） | edit 管线 + `must_keep`（§6.2） |
| G5 | 反推只有「七维清单」，无验证 | 反推对不对无法判定，只能靠人眼 | 多路融合 + 闭环重生成打分（§6.3/6.4） |
| G6 | 没有静态检查 | 低级错误（权重语法、质量词、超长）靠人抓 | 15 条 lint 规则，零 LLM（§8） |
| G7 | 没有评测基线 | 换模型/换模板无法判断变好还是变坏 | golden set + 五指标（§9） |
| G8 | 没有可复现记录 | 好图复现不出来（seed/模型哈希/LoRA 栈丢失） | manifest sidecar（§7.8） |
| G9 | 多主体属性串味无解 | 「红衣女孩牵着蓝衣男孩」→ 两人都变紫 | relations + 分区条件（§6.1） |
| G10 | 系统提示假设无限上下文 | 18KB preset 塞给 7B 本地模型，指令遵循率骤降 | 分级加载（§7.7） |
| G11 | 结构化输出靠模型自觉 | layout JSON 常崩，坐标算错 | GBNF/JSON Schema 约束解码（§7.6） |
| G12 | 没有版本化 | prompt 改动无法 diff、无法回滚 | prompt 当代码（§7.9） |
| G13 | 只有「扩写」没有「压缩」 | FLUX 3 上过度指定，Krea 2 上超预算 | 密度目标双向调节（§6.5） |
| G14 | 合规只做了年龄闸 | 肖像权/商标/在世画师/内容标识全缺 | 四层合规（§10） |
| G15 | 没有角色一致性机制 | 120 集短剧里主角每集换脸 | character bible + 锚定（§6.6） |

---

## 2. 三种静默失败（这是 v2 架构存在的理由）

跨模型直接搬运 prompt，不报错，只出烂图。以下六条可当回归用例：

| # | 搬运路径 | 发生什么 | 检测方式 |
|---|---|---|---|
| F1 | Qwen 长散文（500 词 ≈ 700+ token）→ Krea 2 | 512 截断，**尾部光照/媒介/图内文字全丢** | token 计数 > 512 即告警 |
| F2 | MOSS layout JSON → FLUX 3 | 坐标系不同（中心像素 vs 0–1000 网格），JSON 被当字面文本画出来 | 目标不支持该 dialect 时拒绝 |
| F3 | FLUX 3 短 prompt（60 词）→ Qwen 2.1 | Qwen 不做自动扩写，信息不足 → 画面简陋 | 词数 < 300 时告警 |
| F4 | SDXL `(word:1.2)` → MOSS / Krea 2 / Qwen | 括号权重被当字面字符，可能画出括号或语义漂移 | 目标 `weight_syntax: none` 时剥离 |
| F5 | 负向词写进 Qwen 2.1 正向 | API 静默忽略；本地 true_cfg 下耗时翻倍 | 负向词命中词表即告警 |
| F6 | MOSS 裸散文 → FLUX 3 | 模型再扩写一遍 → 过度指定，构图僵化 | verbosity 超目标上限即压缩 |

**推论**：prompt 不是字符串，是**有目标方言的中间产物**。所以先有 IR，再有 prompt。

---

## 3. 架构：三段式编译器

```
┌────────────── 前端（语义） ──────────────┐
  用户意图 / 参考图 / 模板
      ↓  LLM 抽取（小模型可胜任）
  草稿 IR（字段可能不全、可能矛盾）
      ↓  规则归一（确定性代码）
┌────────────── 中端（IR） ────────────────┐
  规范化 IR（Scene Packet）
  · 原子化事实 + importance
  · bbox 归一化 0–1
  · relations 绑定
  · 年龄闸 / IP 策略
      ↓  tokenizer 计数 + 预算分配 + 重要性裁剪
┌────────────── 后端（codegen） ───────────┐
  target adapter × N
  · realize(ir, budget) → positive / negative / layout / params / warnings
  · lint(prompt) → issues[]
  · manifest → 可复现记录
```

**三条硬边界**：

1. **LLM 只做语义，编译器做语法。** 坐标换算、token 计数、JSON 序列化、权重剥离，全部用代码。LLM 算数必错。
2. **IR 存事实，不存句子。** 存「28 岁 / 黄色雨衣 / 左侧前景」，不存「a 28-year-old woman in a yellow raincoat stands in the left third」。
3. **后端只读 IR。** 后端不允许读用户原话，否则各后端行为不可预测。

---

## 4. IR 设计（核心）

完整定义见 `schema/ir.schema.json`。设计要点四条：

### 4.1 原子化 + 重要度

每个实体带 `importance: 0..1`。预算不足时按重要度裁剪，而不是按句尾截断。

```yaml
entities:
  - id: e1
    kind: character
    label: "28-year-old woman in yellow raincoat"
    importance: 1.0          # 主体，不可删
    age_years: 28            # 硬闸字段
    descriptors: { wardrobe: ["yellow raincoat"], expression: "looking up" }
    spatial: { anchor: "left third, foreground", bbox_norm: [0.1,0.45,0.35,0.9] }
  - id: e2
    kind: object
    label: "lighthouse beam"
    importance: 0.4          # 氛围元素，预算紧时可丢
```

**为什么重要**：Krea 2 的 512 截断是**按位置**砍，砍掉的可能是主体。有了重要度，编译器可以先删 `importance<0.5` 的从句，保住主体。

### 4.2 bbox 归一化到 0–1

一处存储，三处编译：

| 目标 | 编译结果 |
|---|---|
| MOSS | `abs/xyxy` 像素（W/H 取 16 倍数）→ 再交官方 `compile_prompt.py` 转 `abs_centralized/yxyx` |
| FLUX 3 | `[top,left,bottom,right]` × 1000，整数，配 `<silhouette_n>` 行 |
| Krea 2 / Qwen | 丢弃 bbox，降级为 `anchor` 方位短语 |

**反例**：不要在 IR 里存中心原点坐标。中心原点是 MOSS 的方言，不是事实。

### 4.3 relations 解决属性串味

多主体场景必须显式写关系，不能靠句子顺序：

```yaml
relations:
  - { subject: e1, predicate: holding, object: e3 }
  - { subject: e2, predicate: left_of, object: e1 }
```

后端据此生成「分区子句」或调用区域条件节点（SDXL 路线）。无 relations 且 `entities.length > 1` → lint 报 L007。

### 4.4 provenance 支撑反推的可信度

`task=reverse` 时，每个描述必须挂 `evidence`（可见证据）。没有证据的描述 → 丢弃，不输出。

```yaml
evidence:
  - "左上角有一段深绿色中文标题，约 6 字，字重中等"
  - "杯身有细密斑点釉，接触阴影柔和"
```

---

## 5. 后端适配器接口

一个模型 = 一个目录。新增模型不改核心代码。

```
adapters/
  moss-image/
    capability.yaml      # 从 model-registry.yaml 继承 + 覆写
    realize.py           # IR → prompt（模板 + 规则）
    lint.yaml            # 该模型的专属规则
    fixtures/            # 回归用例（IR 输入 → 期望 prompt 快照）
  flux3/
  krea2/
  qwen-image-2_1/
```

```python
class TargetAdapter(Protocol):
    name: str
    def capabilities(self) -> Capability: ...
    def realize(self, ir: IR, budget: Budget) -> Realized: ...
    def lint(self, realized: Realized) -> list[Issue]: ...
    def params(self, ir: IR, profile: str | None) -> dict: ...
    def validate(self, payload: dict) -> list[Issue]: ...   # 调 API 前的本地校验
```

`Realized` 至少含：`positive`、`negative|None`、`layout|None`、`params`、`warnings`、`token_count`。

**这就是「又专又通用」的落点**：通用在 IR，专在 adapter。

---

## 6. 五条管线

### 6.1 T2I

```
意图 → 草稿 IR → 归一 → [预算分配] → adapter.realize → lint → 输出
```

多主体时的三级策略：

| 场景 | 策略 |
|---|---|
| 目标支持区域条件（SDXL + Regional / FLUX 3 bbox） | 走结构化布局，每主体独立 clause |
| 目标不支持（Krea 2 / Qwen 2.1） | 每主体独立从句 + 显式方位 + 显式互斥（"the woman in red; separately, the boy in blue"） |
| 仍串味 | 拆分生成 + 局部重绘合成（成本高，作为兜底） |

### 6.2 编辑 / 图生图（v1.0 完全缺失）

三种方言，编译期选一：

| 目标 | 句式骨架 | 铁律 |
|---|---|---|
| FLUX 3 | `Modify <ref_image_0> by replacing X with Y. Keep the rest of the image exactly unchanged, preserving ...` | 改色用 hex；增删要「指令 + bbox 行」说两遍 |
| Qwen-Image 2.1 | 动词开头、单段无换行；`保持[某类物体]不变` | **只按类型/位置点名，不描述被保留物的外观**（越描述越漂移） |
| Qwen 多图 | `<image1>` / `<image2>` 强制标签 | 单图禁用标签；禁写「图1/第一张」 |

`must_keep` 字段的编译规则：

```
must_keep: [face, hairstyle, background]
→ FLUX 3:  "preserving the subject's facial identity, hairstyle, and background"
→ Qwen:    "keep the person's face and hair unchanged; keep the background unchanged"
→ 禁止:    "keep the 28-year-old woman with long black hair and round glasses unchanged"  ← 会漂移
```

### 6.3 反推（image → IR）

四路并行，各自只干自己最擅长的事：

| 路 | 工具 | 产出 | 不可替代性 |
|---|---|---|---|
| 结构 | Florence-2 | dense caption + region caption + phrase grounding bbox | 一个模型同时给框、区域描述、OCR 定位 |
| 标签 | WD-EVA02 tagger | 角色/IP/风格 tag + 概率 | 动漫域的客观标签，VLM 常编 |
| 文字 | RapidOCR（PaddleOCR ONNX） | 文字内容 + 位置 | VLM 读中文小字会错字 |
| 语义 | Qwen3-VL 8B | 七维语义 + 实体拆分 + 光照/材质 | 只有 VLM 能写「光质/情绪」 |

**融合规则**（冲突消解，确定性代码）：

1. 图内文字：**OCR 优先**，VLM 只补排版描述。OCR 置信度 < 0.6 的字 → 不输出，标注「不可辨」。
2. 角色身份：**tagger 优先**于 VLM 猜测。tagger 概率 < 0.35 的角色名 → 丢弃（防幻觉）。
3. 场景语义：**VLM 优先**，但每条必须挂 evidence。
4. 空间关系：**bbox 优先**于文字描述。

**输出**：IR + `provenance.confidence`。confidence 由四路一致度加权，低于 0.5 时前端提示「反推不可靠，建议人工校正」。

### 6.4 洗图闭环（这是护城河）

反推不是终点，**可复现才是**。

```
参考图 → 反推 IR → 目标模型重生成 → 打分 → 差距定位 → 补丁 IR → 重生成（≤2 轮）
```

打分五指标（§9）。差距定位按维度拆：

| 差距维度 | 定位方法 | 补丁动作 |
|---|---|---|
| 语义缺失 | VQAScore 分项低 | 补 entity / descriptor |
| 结构错位 | bbox IoU < 0.5 | 调 spatial.anchor / bbox_norm |
| 文字错 | OCR 字符 F1 < 0.8 | 改 typography / 降字号 / 换目标模型 |
| 风格偏 | DINO 相似度低 | 调 style.medium / substyle |
| 光影偏 | 人工 | 调 lighting.key |

**关键**：闭环必须固定 seed 与采样参数，否则差异无法归因。这是用户反复强调的「测试要统一不变量」。

### 6.5 扩写 / 压缩（同一个操作的两端）

不要做「扩写器」，做**密度调节器**。

| 目标 | verbosity | 目标长度 | 动作 |
|---|---|---|---|
| FLUX 3 | 0.25 | 60–120 词 | **压缩**：只留控制点（位置/外观差异/光/文字） |
| SDXL | 0.4 | 30–60 词 | 压缩 + tag 化 |
| MOSS | 0.7 | 150–300 词 | 七维散文 |
| Qwen 2.1 | 1.0 | 400–500 词 | **扩写**：补方位短语、补光照句、补收尾构图句 |
| Krea 2 | 1.0 但 ≤512 token | 250–400 词 | 扩写后**强制压到 512 内**，按重要度砍 |

`verbosity` 写进 IR，adapter 按 registry 的 `prompt.verbosity` 做偏差校正。

### 6.6 角色一致性（对 120 集短剧直接相关）

把角色做成**资产**，不是每集重写。

```yaml
character_bible:
  id: ch_lin
  canonical: { age_years: 24, hair: "black, waist-length, straight", face: "...", build: "..." }
  anchor_phrases: ["24-year-old woman with waist-length straight black hair"]
  loras: [{ name: "lin_v3", weight: 0.6, targets: [sdxl, flux1] }]
  ref_images: ["./refs/lin_front.png", "./refs/lin_side.png"]
  negative: ["short hair", "blonde"]
```

生成时 IR 只引用 `ch_lin`，adapter 展开 canonical。**改一次，全剧生效。**

---

## 7. 关键工程决策

### 7.1 LLM 只做语义

| 任务 | 谁做 | 理由 |
|---|---|---|
| 意图解析、实体拆分、描述生成 | LLM | 需要语言理解 |
| 坐标换算、token 计数、JSON 序列化 | 代码 | LLM 算数必错 |
| 冲突检测、规则校验 | 代码 | 需要确定性 |
| 风格判断、审美取舍 | 人 | 模型给不出可信结论 |

### 7.2 token 预算：用真实 tokenizer

```python
# 4/5 个模型共用 Qwen tokenizer 家族
count = tokenizer(model_id).encode(prompt, add_special_tokens=False)
```

| 目标 | 预算 | 超预算行为 |
|---|---|---|
| Krea 2 | 512 | 硬截断 → 必须前端告警 |
| FLUX 3 | 8192 | 截断，但实际应远低于（verbosity 0.25） |
| Qwen 2.1 | 未声明 | 按 400–500 词软约束 |
| MOSS | 4000 | 截断 |
| SDXL | 77 × chunks | 分块，权重衰减 |

### 7.3 截断语义：重要度优先，不按位置

标准做法（错）：`prompt[:512]`
本方案：`emit(sorted(clauses, key=-importance))` 直到预算耗尽，再按维度顺序重排输出。

**为什么**：Krea 2 按位置截断会砍掉尾部的媒介/光照/文字——恰恰是决定画面质量的部分。

### 7.4 负向三态编译

```
IR.constraints.avoid
  ├─ 目标有真负向（MOSS / SDXL / FLUX.1）→ 写入 negative 字段
  ├─ 目标无负向但支持正向规避（Qwen 2.1 API）→ 转成 "with no X, avoiding Y" 并入正向
  └─ 目标两者都不稳（Krea 2 / FLUX 3 蒸馏）→ 丢弃 + warning，并强化正向描述
```

丢弃不是失败，是正确行为。硬塞负向词进不支持的目标，只会污染语义。

### 7.5 布局方言编译

```
IR.bbox_norm (0–1, 左上原点)
  ├─ MOSS  → ×(W,H) → floor/ceil → compile_prompt.py → abs_centralized/yxyx
  ├─ FLUX3 → ×1000 取整 → [top,left,bottom,right] + <silhouette_n> 行
  └─ 其他  → 降级为 anchor 文本
```

MOSS 的浮点边规则（左/上 floor，右/下 ceil）**必须调用官方编译器**，不要手写。

### 7.6 约束解码：本地 LLM 的结构化输出

本地 7–14B 模型自由生成 JSON，崩溃率不可接受。三条措施：

1. **GBNF / JSON Schema 约束解码**（llama.cpp grammar）——从解码层保证合法。
2. **两段式**：先出纯文本草稿，再用代码转 JSON。宁可多一步，不要一次成型的复杂 schema。
3. **schema 瘦身**：本地模型能稳定输出的字段 ≤ 12 个。IR 的 60 个字段靠默认值补全，不要求模型全填。

### 7.7 系统提示分级加载（本地模型上下文经济学）

Moeblack 的 SKILL.md 是 22k token，MaxKruse 的 preset 是 18KB。**这些不能塞给 7B 模型**——指令遵循率会塌。

| 层 | 内容 | 预算 | 何时加载 |
|---|---|---|---|
| L0 索引 | 能力清单 + 路由规则 | ≤200 token | 常驻 |
| L1 任务模块 | 当前管线所需规则 | ≤1500 token | 按 task 加载一个 |
| L2 目标方言 | 当前 adapter 的 realize 规则 | ≤800 token | 按 target 加载一个 |
| L3 参考示例 | few-shot 样例 | ≤600 token | 仅 confidence 低时 |

**总量控制在 3k token 内**。大模型（32B+）可加载全量，作为可选档。

### 7.8 可复现 manifest

每张图旁边存一个 sidecar：

```json
{
  "ir_hash": "sha256:...",
  "target": "moss-image",
  "prompt": "...",
  "negative": "...",
  "layout": null,
  "params": { "steps": 50, "cfg": 3.0, "shift": 5.0, "sampler": "euler", "seed": 12345 },
  "model": { "name": "moss-image", "hash": "sha256:..." },
  "loras": [{ "name": "lin_v3", "weight": 0.6, "hash": "..." }],
  "adapter_version": "0.3.1",
  "registry_version": "0.1",
  "created": "2026-10-31T13:28:00+08:00"
}
```

**规则**：没有 manifest 的图 = 不可复现 = 不可用于生产。

### 7.9 prompt 当代码

- IR 存 YAML，可 git diff、可 review、可回滚
- 语义化版本：`v1.2.0`（major=改结构，minor=加字段，patch=改词）
- 回归：adapter 的 `fixtures/` 存「IR → 期望 prompt 快照」，CI 跑 diff

---

## 8. Lint：15 条静态规则（零 LLM）

在送给模型之前拦截。全部确定性，可单测。

| ID | 规则 | 触发 | 处置 |
|---|---|---|---|
| L001 | 超预算 | token > 目标上限 × 0.95 | error，触发裁剪 |
| L002 | 质量词黑名单 | `masterpiece`/`8k`/`ultra detailed`/`best quality`/`highly detailed`/`award-winning` | error，删除 |
| L003 | 权重语法错配 | 目标 `weight_syntax: none` 但出现 `(x:1.2)`/`[x]`/`((x))` | error，剥离 |
| L004 | 负向写进正向 | 目标 `negative.supported: false` 且正向含负向词 | warn，转规避句或删 |
| L005 | 采样参数泄漏 | prompt 含 `steps`/`CFG`/`--ar`/`1024x1024`/`8k` | error，删除 |
| L006 | 人体无年龄 | `kind: character` 且缺 `age_years` | error，阻断 |
| L007 | 多主体未绑定 | `entities > 1` 且无 `relations` 且无 `spatial.anchor` | warn |
| L008 | 属性矛盾 | `day`+`night` / `indoor`+`outdoor` / `black hair`+`blonde` | error |
| L009 | 画师/IP 违规 | 目标策略 `blocked` 或 `ip_policy: strict` 命中 | error，删除 |
| L010 | 图内文字未加引号 | `kind: text` 但 positive 中无引号包裹 | error |
| L011 | 坐标非法 | bbox 越界 / 宽高非 16 倍数（MOSS）/ 框小于 40×25（FLUX 3） | error |
| L012 | 相机型号孤立 | 出现机型名但无整句词组支撑 | warn |
| L013 | 空泛副词过密 | `very`/`really`/`extremely`/`highly` 密度 > 3/100 词 | warn |
| L014 | 同义堆叠 | 同一概念重复 ≥3 次（beautiful/gorgeous/stunning） | warn |
| L015 | 能力越界 | 对 Krea 2 要求 edit / 对 Qwen 要求 bbox | error，阻断 |

**L001–L015 全部可单测。这是「工匠」与「聊天」的分界线。**

### 8.1 落地后新增三条（v0.1 真机跑批复现，v1.0 没覆盖）

| ID | 规则 | 怎么发现的 |
|---|---|---|
| L016 | 凭空生成图内文字 | IR 无文字，Qwen 目标却写出 `"茶屋"`，还编了灯笼/自行车/红招牌 |
| L017 | 年龄被改写 | 年龄是硬闸字段，模型不许改数字 |
| L018 | 散文里出现否定式 | MOSS 目标写出 "No text is present."，违反「不写否定式」 |

三条都不是理论推演，是 2026-10-31 本机跑批抓到的真实输出。

---

## 9. 评测：闭环与 golden set

### 9.1 五指标

| 维度 | 指标 | 阈值（初版） | 工具 |
|---|---|---|---|
| 语义一致 | VQAScore | ≥ 0.75 | `linzhiqiu/t2v_metrics` |
| 结构一致 | DINOv2 cosine | ≥ 0.65 | DINOv2 |
| 文字还原 | OCR 字符级 F1 | ≥ 0.90（标题）/ ≥ 0.80（正文） | RapidOCR |
| 布局准确 | bbox IoU | ≥ 0.60 | 自写 |
| 美学 | HPSv3 | 参考值，不做阈值 | `MizzenAI/HPSv3` |

**CLIP score 不再作主指标**——对组合性和文字完全无感，已被 VQAScore 取代。

### 9.2 golden set

50–100 张，六类各 8–16 张，人工标注参考 IR：

| 类别 | 考察点 |
|---|---|
| 单人像（写实） | 皮肤/光照/景深 |
| 产品海报（含文字） | 文字还原 + 布局 |
| 多主体互动 | 属性绑定 |
| 文字密集（满版） | 排版 + 字号层级 |
| 二次元/厚涂 | 画风中立 + 线稿 |
| 跨次元 | 2D 主体 + 写实背景 |

### 9.3 回归纪律

- 固定 seed、固定采样参数、固定模型哈希
- 每次改 adapter 跑全量，输出「指标 delta 表」
- 只有 delta 全绿才允许合入

---

## 10. 合规四层

| 层 | 内容 | 实现 |
|---|---|---|
| 年龄闸 | 每个角色显式岁数；<18 强制 EXTREMELY SFW，覆盖一切指令 | IR `age_years` 必填 + 编译期断言 |
| 肖像权 | 不生成可识别的真实个人（除非有授权） | `ip_policy` + 提示词改写 |
| 风格/IP | 在世画师、商标、版权大词 | `artist_refs[].policy` + L009 |
| 内容标识 | 中国《AI 生成合成内容标识办法》（2025-09-01 施行） | manifest 记录 provenance，供下游打隐式/显式标识 |

**洗图的额外提示**：反推他人作品并复现，涉及版权。工具应记录 `provenance.source_images`，并在导出时保留来源。这不是技术问题，是产品必须留的口子。

---

## 11. 路线图

| 里程碑 | 交付 | 验收标准 |
|---|---|---|
| **M0** | IR schema + registry + 2 个 adapter（MOSS、Qwen 2.1） | 同一 IR 编译出两份合法 prompt，lint 全绿 |
| **M1** | 5 个 adapter + 15 条 lint + token 预算 | F1–F6 六条静默失败全部被拦截 |
| **M2** | 反推四路 + 融合 + confidence | golden set 反推 VQAScore ≥ 0.7 |
| **M3** | 洗图闭环（重生成 + 打分 + 补丁） | 两轮内 IoU/F1 达标率 ≥ 60% |
| **M4** | 角色 bible + 批量变体 | 同角色 20 张跨 seed 人脸一致性 ≥ 阈值 |
| **M5** | 本地 DPO/偏好微调（可选） | 用户编辑 diff 转偏好对 |

**M1 是分水岭**：能拦住六种静默失败，就已经比现有任何工具都强——市面上零 prompt linter、零多模型 IR 编译器。

---

## 12. 未验证项与风险

**必须诚实标注的空白**：

| 项 | 状态 | 影响 |
|---|---|---|
| Qwen-Image 2.1 的 prompt token 上限 | 官方未声明 | 预算只能按词数软约束 |
| Krea 2 对质量词/画师名的反应 | 官方未提及 | 不能断言「无效」，只能标 unverified |
| FLUX 3 是否对所有字段做严格校验 | 只知未知字段 422 | 新参数可能被静默忽略 |
| MOSS 的 `agents/` 目录内容 | 未枚举 | 可能已有官方编排逻辑 |
| 五模型的「审美」差异 | 无法量化 | 只能人眼判，工具不给结论 |

**架构风险**：

| 风险 | 缓解 |
|---|---|
| 官方改格式，adapter 失效 | registry 带 `updated` + `confidence`；adapter 有 fixtures 回归 |
| 本地小模型填不满 IR | 字段默认值 + 分级加载 + 只要求填 12 个关键字段 |
| 区域控制方案多为 GPL/AGPL | 只借鉴思路（mask→cross-attention），不链接代码 |
| 反推幻觉 | evidence 强制 + tagger 概率阈值 + confidence 门限 |

---

## 附录 A：五个后端的最小可用模板

**MOSS-Image**（裸散文，七维，一个段落）
```
[Subject+action with explicit age]. [Setting with positional phrases]. [Camera: shot, angle, lens, DOF].
[Lighting: quality, direction, temp]. [Materials and texture]. [Medium and style]. ["Visible text" verbatim].
```
禁：质量词、权重语法、坐标、采样参数、markdown。

**Krea 2**（长自然语言，媒介开头，≤512 token）
```
[Medium-led opening sentence]. [Subject and action]. [Setting and spatial layout]. [Lighting]. [Texture]. [Style].
```
禁：过度指定、擅自换媒介、文字不加引号。超 512 从尾部砍，先砍 importance<0.5。

**FLUX 3 Image**（短，只写控制点）
```
[Subject + key appearance]. [Position of each element]. [Lighting intent]. ["Text" with weight/color/alignment].
```
若需布局，追加：
```
<silhouette_1> {"id":"s1","bbox":[top,left,bottom,right],"desc":"..."}
```
bbox 为 0–1000 整数；`aspect_ratio` 必须与设计框一致。

**Qwen-Image 2.1**（英文长散文，400–500 词，观察者语气）
```
[Opening: medium + style + subject + background]. [8–14 positional phrases]. [Lighting sentence].
[Composition summary]. ["Visible text" in original language with weight/color/size].
```
禁：质量词、比例/分辨率、tag 串、第二人称、"create"/"make sure"。

**SDXL**（tag 串 + 权重）
```
[quality tags], [subject tags], [wardrobe], [setting], [lighting], [style], [camera]
```
负向单独字段。>77 token 用 compel 分块。

---

## 附录 B：与 v1.0 的关系

v1.0 的 MOSS 部分**全部保留**，作为 `adapters/moss-image/` 的规则来源。v2.0 不改它的任何结论，只做两件事：

1. 把 MOSS 专属规则降级为「一个后端的方言」，不再冒充通用规范。
2. 补上 v1.0 缺失的跨模型层（IR、预算、lint、反推闭环、评测、合规）。

---

## 附录 C：反推与自审（v2.1 落地，2026-10-31）

v2.0 只写了「反推四路」这个方向，没有定架构。落地时发现四路（Florence-2 / WD-EVA02 / RapidOCR / VLM）里本机只有 VLM，于是先做单路，但把**两段式**这个结构固定下来——因为它是防幻觉的唯一结构性手段。

### C.1 两段式，不是一次调用

```
图片 ──①取证(VLM, T=0.2)──▶ 结构化 JSON ──②融合(纯 Python)──▶ IR
```

| 段 | 谁做 | 能不能编 | 能不能单测 |
|---|---|---|---|
| ① 取证 | VLM，一次带图调用 | 会 | 否 |
| ② 融合 | Python，零模型 | 不会 | 是 |

理由不是洁癖：直接让 VLM「看图写 prompt」时，模型会为了让 prompt 丰满而编造画面元素。本机实测，8B 在空 IR 上硬凑 450 词时凭空造出「茶屋」招牌、灯笼、自行车。把「看」和「组织」分开后，编造只可能发生在①，而①的输出是结构化字段，每个字段都能被规则审查。

### C.2 融合层的六条清洗规则（全部由跑批产生）

| # | 现象 | 规则 | 为什么不能靠提示词解决 |
|---|---|---|---|
| 1 | 模型把「没有」写成值：`fill: "none"`、`horizon: "not applicable"` | 递归剔除 `_JUNK` 词表，含挤在一条字符串里的 `"none, shadows softly diffused"` | 提示词已经写了「不要写没有的东西」，模型照写 |
| 2 | bbox 与同一响应的 `anchor` 矛盾（四格漫画 7 框错 3，anchor 全对） | 按方位区域裁剪；裁成薄片（<8%）则退化为方位区域本身 | 这是模型内部不一致，提示词管不了 |
| 3 | 中文气泡被填 `case: "upper"` | 无拉丁字母时丢弃大小写 | 模型不知道中文没有大小写 |
| 4 | 关系指向文字块：`the woman faces the 他还在楼下` | 丢弃指向 text 实体的关系 | 模型把排版当成了人物 |
| 5 | 同一角色在四格出现 4 次，4 段一模一样的描述 | 按「去方位后的描述」归并 + 一句方位补充 | 模型不知道 4 个框是同一个人 |
| 6 | 材质句复述物体描述 | 描述里已含该材质就不再单独成句 | 模型会为每个字段都写满 |

第 2 条是本次最重要的发现：**8B VLM 报方位比报坐标可靠一个数量级**。所以坐标不是真值，方位才是；坐标只用来细化方位。

### C.3 自审闭环的四个防退化机制

没有图像生成器时，唯一能闭环的办法是把 prompt 念给同一双眼睛听。但「模型审模型」会退化成点头，所以：

| 机制 | 撞到的真实故障 |
|---|---|
| 空审计 = `degraded`，不算通过 | 完全错配的 prompt 得到 `{"score":0,"missing":[],"wrong":[],"ok":[]}`，看着像「审完了没问题」 |
| 漏项扣分（12/条，上限 40） | claims 全对所以 100 分，但 UI 截图的标题和标签一个没提 |
| 分数不涨就停 | 第 2 轮补丁后分数仍 64，第 3 轮只会把 prompt 撑长 |
| 主角不因审计一句话被删 | 只删 importance < 0.7 的实体，高重要度的只在日志记一笔 |
| 元评论过滤 | 模型把「The prompt mentions X but the image shows no visible」塞进 `missing`，这条被当成实体后，画面描述里出现了评价提示词的话 |

### C.4 布局判定：`needs_layout()` 而不是 `has_layout`

反推必然产出 bbox，所以 `has_layout` 恒为真。但官方原文是：

> A detailed image does not automatically need Layout. An ordinary photograph never does.

判据改成「框里有没有相对位置要表达」：有文字框 → 是；≥2 个未盖满画布（面积 < 0.8）的框 → 是；一个盖满画布的框 → 否。
实测：768×768 头像第一次被编译成 `[-410,-410,410,410]` 的单 item Layout JSON，纯属废话；改判据后走散文，质量明显更好。

### C.5 未解决

**密集小字与 UI 截图不可用。** 1600×900 的 UI 截图，8B 把整张图当成「一张照片」，只描述了右侧的四张人物照，标题、副标题、按钮标签一个没提取。这不是提示词能修的——缩放到 1536 宽后小字像素不足。出路只有两条：接 OCR，或分块反推。已列入下一步优先级 1、2。

---

**文档版本**：v2.1 ｜ **核验**：2026-10-31 ｜ **未验证项**：见 §12 与附录 C.5
