# 跨家族 registry 验证（T7）：只加一份 YAML 接入新图像家族

> 任务：T7 · 跨家族 registry 验证（P2）。
> 要证明的不是「又多接了一个后端」，而是：**同一套 IR 换一个提示词哲学完全不同的
> 图像家族，只改 registry、不改一行 Python 代码就能编译**。
> 结论口径：**已验证** = 本仓可跑的字节/单测证据；**未验证** = 没有实跑证据。

---

## 0. 先看结论

| 项 | 状态 | 证据 |
|---|---|---|
| 新家族只靠 YAML 接入 | **已验证** | `ADAPTERS`（原生实现表）里没有它；`get_adapter()` 退到 `GenericAdapter` |
| 不改一行 Python | **已验证** | 本任务只改 `data/model-registry.yaml` + 新增测试/文档；无 `.py` 源码改动 |
| 4 份真机反推 IR 编译成功 | **已验证** | `tests/test_registry_family.py` 12 条全绿；4/4 编译出散文 prompt |
| 不变式全绿 | **已验证** | 4 份 IR × 6 条关系（M1–M6）全部通过；M6 对无 timeline 的 IR 报「不适用」 |
| 往返不丢固定项 | **已验证** | 4/4 `roundtrip.lost == []`（与五个原生图像后端同一判据） |
| lint 无 error | **已验证** | 4/4 `lint.check()` error 数 0 |
| `python -m moss_llm.smith targets` 列出新家族 | **已验证** | 输出 8 行（原 7 + 新 1）；新增行标「通用」 |
| 该家族的真实出图质量 / prompt 遵循度 | **未验证** | 没有 API 调用；`confidence: medium` = 文档级核验 |
| 中文 prompt 是否同样有效 | **未验证** | 官方示例全英文；registry 把 language 记成 `en` |

复现：

```
python tests/test_registry_family.py
python tests/test_smith_ir.py
python tools/freeze_smith_set.py --check
python tools/check_coverage.py --check
python -m moss_llm.smith targets
```

---

## 1. 选的是哪个家族，为什么选它

**Nano Banana（Gemini 原生图像生成/编辑）**，vendor = Google，registry target 名
`nano-banana`。

选它而不是候选里的 Z-Image / HunyuanImage 3.0 / Qwen-Image-Flash，理由按优先级：

1. **官方 prompting 文档是「写法指南」而不是「调用示例」**。它有明确的总原则
   （描述场景，不要罗列关键词）、分场景模板（写实/贴纸/文字/产品/极简/漫画）
   和 Best practices / Limitations 清单，正好能落成 registry 的
   `prompt` 方言声明与 `pitfalls`。
2. **提示词哲学与现有家族相反**，这才是「跨家族」该有的对照：现有后端要么是
   tag（SDXL）、要么是短散文 + 模型自扩写（FLUX 3）、要么是结构化 Layout JSON
   （MOSS）；Nano Banana 要的是**完整叙事段落**，且明确反对关键词表。同一份 IR
   换个方言，正好检验「IR 存事实、不存句子」这条设计是否成立。
3. **免费、可复核**：文档是公开网页；本机不需要权重。
4. Z-Image / HunyuanImage 3.0 本轮没有选：它们的公开材料里没有同等细度的
   官方「怎么写 prompt」指南（更多是模型卡 + 用法示例），拿来做「官方 prompting
   文档」这一条证据会偏弱。不是它们不能用，是**证据等级不够写实**。

来源（2026-11-01 抓取，均为 HTTP 200；文档级核验，非实测）：

- [How to prompt Gemini 2.5 Flash Image Generation for the best results · Google Developers Blog](https://developers.googleblog.com/en/how-to-prompt-gemini-2-5-flash-image-generation-for-the-best-results/) —— 官方 prompting 指南：总原则、六类场景模板、Best practices、Limitations
- [Gemini API · Image generation (Nano Banana)](https://ai.google.dev/gemini-api/docs/image-generation) —— 官方 API 文档：家族构成（Nano Banana Pro / 2 / 2 Lite / 2.1 等）、文生图 / 编辑 / 多图组合能力

> 证据分级沿用本仓口径：官方文档属 **C 级（作者自报）**，不是 A 级实跑。
> 所以 `confidence: medium`，不是 `high`。

---

## 2. registry 里加了什么（只加块，不动旧条目）

位置：`moss_llm/smith/data/model-registry.yaml`，`targets` 下新增 `nano-banana`
（文件头 `updated` 同步为当天）。字段与现有条目同构：

| 字段 | 值 | 依据 |
|---|---|---|
| `display` | Nano Banana (Gemini Image) | 官方文档标题 |
| `vendor` | Google | 官方文档 |
| `confidence` | `medium` | 文档级核验；未实跑 |
| `source` | 两个官方 URL（见 §1） | 本任务抓取核验 |
| `encoder` | Gemini 原生多模态；`max_tokens: null`；`family: gemini` | 官方：prompt 与图一起进模型，无独立 text encoder |
| `prompt.mode` | `prose` | 官方原则：叙事段落，不列关键词 |
| `prompt.verbosity` | `1.0` | 官方鼓励 detail |
| `prompt.language` | `en` | 官方示例全英文（其他语言未验证） |
| `prompt.length` | brief 60–120 / standard 120–250 / detailed 250–400 词 | **推导值**，官方没给词数带（注释已写明） |
| `prompt.quality_words` | `unverified` | 官方模板自己写 "Ultra-realistic"，不能封禁、也没说越高越好 |
| `prompt.weight_syntax` | `none` | 无权重语法，强调只能靠自然语言 |
| `prompt.structured_output` | `[caption, edit_instruction]` | 文生图 + 编辑指令两种交付形态 |
| `negative.supported` | `false`（`channel: none`） | API 无 negative 参数；官方要求把「不要 X」改写成正向场景 |
| `layout.supported` | `false` | 无 bbox / 区域提示词 |
| `edit.supported` | `true`（无占位标签语法） | 官方博文：编辑 / 多图组合 / 风格迁移 |
| `sampling` | 只有 `note`（服务端决定） | API 没有 steps/cfg/seed 可下发 |
| `pitfalls` | 6 条，全部对得上官方原文 | 见 §5 |

**没有改**的部分：`targets` 里原有 7 个条目、`minimax-h3.dialects`、`prompt.fields`
等一个字段都没动。

---

## 3. 纯 YAML 是否成立：成立，而且用真机 IR 验过

### 3.1 「没有改 Python」怎么判

不是口头声明，是三条机检：

1. `ADAPTERS`（`adapters/__init__.py` 里登记原生实现）**不含** `nano-banana`；
2. `get_adapter("nano-banana", reg)` 返回 `GenericAdapter`（通用渲染：散文/tag +
   负向 + 预算裁剪）；
3. 本任务改动文件只有 `data/model-registry.yaml`、新增测试与文档——`moss_llm/**`
   的 `.py` 一行未改。

### 3.2 用哪 4 份 IR

`tests/fixtures/smith/` 里的 4 份**真机反推** IR（仓库里没有单独的 `reverse/`
目录，反推 IR 就放在这里）：

- `comic-4panel-lineart-sample.ir.json`（多格漫画线稿，重复实体）
- `faceswap-demo-ref.ir.json`（换脸参考图，notes 是整段观察散文）
- `hero-storyboard.ir.json`（故事板 4 个角色 + relations）
- `ui-smoke.ir.json`（UI 截图，8 个文字块 → 布局判定为真）

选真机 IR 而不是手写示例，是因为手写示例太干净——真正会暴露「registry 声明接不上
渲染」的是脏 IR（重复实体、文字块、整段 notes）。

### 3.3 实测结果

| IR | prompt 词数 | 不变式 | roundtrip lost | lint error |
|---|---|---|---|---|
| comic-4panel-lineart-sample | 155 | 6/6 | 0 | 0 |
| faceswap-demo-ref | 135 | 6/6 | 0 | 0 |
| hero-storyboard | 255 | 6/6 | 0 | 0 |
| ui-smoke | 129 | 6/6 | 0 | 0 |

另外两条结构判据：

- **确定性**：同一 IR 两次编译逐字节相同（M1 自己也会查，这里单独钉一次）；
- **双解析器一致**：新 YAML 在 PyYAML 与内置 `miniyaml` 下解析结果逐字段相等
  （`tests/test_smith_ir.py` 的对照测试覆盖，改 YAML 后仍绿）。

### 3.4 `targets` 输出（实测）

```
target             实现       编码器        形态       负向     布局     编辑
...
nano-banana        通用       Gemini native mult prose    无      无      有
...
```

「通用」就是「没有原生 Python 实现、按 registry 声明渲染」——这正是 T7 要的证据。

---

## 4. 与现有家族的差异（同一份 IR，换方言）

| 家族 | prompt 形态 | 长度哲学 | 负向 | 结构化输出 | 提示词里「怎么写」的核心差异 |
|---|---|---|---|---|---|
| SDXL（tag） | 逗号 tag | 短，77 token 分块 | 有 | 无 | 关键词权重；本编译层剥权重 |
| FLUX.1 dev | 散文 | 中 | 有 | 无 | 自然语言即可 |
| FLUX 3 | 短散文 | **很短**，模型自带 upsampler | 无 | bbox 表 | 只写要控制的东西 |
| Krea 2 | 长散文 | 250–380 词（512 硬截断） | 无 | 无 | 官方反对过度指定 |
| Qwen-Image 2.1 | 长散文 | 400–500 词目标 | 无（API） | 无 | 第三人称现在时观察者 |
| MOSS-Image | 散文 + Layout JSON | 150–300 词 | 有 | caption/layout/tag | 结构化布局是另一条通道 |
| MiniMax-H3 | 时间线（+六段式 profile） | 150–520 词 | 无 | 无 | `[Shot N]` + `At` + `<d>` |
| **Nano Banana（本次新增）** | **叙事段落** | **越具体越好（无官方词数）** | **无（语义负向）** | caption / 编辑指令 | **Describe the scene, don't list keywords；编辑保留项要点名** |

这条差异本身是收获：IR 的七个维度（主体/场景/镜头/光/材质/媒介/文字）换到
「叙事段落」方言后，`describe_entity` 的属性罗列会被拼成一句一段的散文，事实
集合仍然不丢（roundtrip 0 lost），说明「存事实、不存句子」的设计在这个方向上也成立。

---

## 5. 官方文档 → pitfalls 的逐条对应

写进 registry 的 6 条不是猜的，每条都能指回官方原文：

| registry pitfalls | 官方原文 / 出处 |
|---|---|
| 关键词罗列不如叙事段落 | 总原则「Describe the scene, don't just list keywords. A narrative, descriptive paragraph will almost always produce a better, more coherent image」 |
| 「不要 X」否定式效果差 | Best practices「Use "semantic negative prompts": Instead of saying "no cars," describe the desired scene positively: "an empty, deserted street with no signs of traffic."」 |
| 多轮迭代后角色一致性会漂移 | Best practices「If you notice a character's features begin to drift after many iterative edits, you can restart a new conversation with a detailed description」 |
| 编辑时不写「其余保持不变」会改到别处 | Editing 模板「Keep everything else in the image exactly the same, preserving the original style, lighting, and composition」 |
| 多图输入采用最后一张的宽高比 | Best practices「If you upload multiple images with different aspect ratios, the model will adopt the aspect ratio of the last image provided」 |
| 复杂排版 / 小字要多轮 refine | Limitations「generating complex typography ... sometimes needs refinement through follow-up prompts」 |

这六条同时解释了为什么 `negative.supported: false`：这个家族不是「没有负向参数
所以不能写否定」，而是**官方明确说否定式效果差、要改正向重述**。

---

## 6. 抽象漏在哪（如实记录，不是本轮要修的 bug）

registry 驱动这次**够用**（纯 YAML 通过），但验证过程中看到四个边界，按「什么能靠
schema 表达、什么不能」记录：

1. **「能力声明」与「渲染能力」不在同一层。** registry 可以声明
   `edit.supported: true`，但 generic 只会渲染散文，不会生成图片引用占位符
   （`<imageN>` 之类）或结构化编辑块。要真做编辑方言，需要原生 adapter——
   这是有意的分层（`adapters/generic.py` 的能力边界原文就写了这一点），不是漏。

   **实测更要紧的是「能编译」不等于「可用」**：把
   `examples/04-edit-instruction.json`（edit 任务，`constraints.instruction` 是
   “Replace the grey coat with a deep red wool coat”）编译到本家族，输出只有
   3 个词 `A 30-year-old woman.`——主体句在，**编辑指令整段没进提示词**
   （generic 只渲染维度子句，不读 `constraints.instruction`）。所以编辑 IR
   在本家族上是「不报错、但基本不可用」；§3 的 4 份 IR 验证集也**不含** edit IR，
   12 条测试不足以证明编辑可用。要真编辑必须走原生 adapter（处理 edit 语义与
   图片引用占位），这与「纯 YAML 接入」不冲突，因为 T7 证明的是**文生图散文方言**
   可扩展，不是所有能力都能由 YAML 声明出来。
2. **置信度只有 target 级，没有字段级。** `length` 三档词数、`quality_words:
   unverified` 都是「有来源的推导」，与「官方明写」混在同一层 `confidence` 里。
   建议将来给 registry 加字段级置信度（或 `derived: true` 标记），否则下游
   分期不了「官方值」与「推算值」。本轮用 YAML 注释写清，但注释不进解析。
3. **`resolution` / `sampling` 是自由文本。** 图像家族之间语义差异太大
   （步数/CFG vs 服务端采样 vs 分辨率档），schema 里没有统一口径；这两个键
   目前只能当人读的说明，机检不校验。
4. **`encoder.max_tokens: null` 等于「不设预算」。** 对 hosted API 无法模拟
   截断行为；generic 会保留全部子句。若将来要按 token 裁剪，需要给这个家族
   一个可信上限，而那需要实跑证据。

**结论**：这四条都不需要「为了接这个家族改 Python」；它们是 registry 抽象本来
就只做到「散文/tag 方言 + 能力声明」这一层。要扩到结构化编辑方言，才需要新
adapter——那属于新任务，不属于 T7。

---

## 7. 未覆盖 / 未验证

- **没有 API 调用**：Nano Banana 是 hosted 模型，本机无 key；本轮不产生任何生成图。
- **prompt 遵循度 / 出图质量未验证**：文档级核验不等于效果证据。
- **中文 prompt 未验证**：官方示例全英文；registry 记 `language: en` 是保守选择。
- **家族内多档模型差异未验证**：官方文档列了 Nano Banana Pro / 2 / 2 Lite / 2.1
  等档位；registry 把「家族」当一个方言登记。若按具体档位下发，需要再核对该档
  的文档（尤其分辨率与多图输入上限）。
- **6 条 pitfalls 是「官方这么说」，不是「本机 A/B 验证过」**——后者要 GPU/API
  出图，是 T6 那类任务。

---

## 8. 改动文件

| 文件 | 性质 |
|---|---|
| `moss_llm/smith/data/model-registry.yaml` | 修改：新增 `nano-banana` target（+ 文件头 `updated` 日期）；未动其余条目 |
| `tests/test_registry_family.py` | 新增：12 条（注册 / 纯 YAML / 4 份 IR 编译 / 不变式 / 往返 / 确定性 / CLI 列出 / 来源 URL / 置信度写实） |
| `docs/29-跨家族registry验证.md` | 本文 |

本机实测数字（2026-11-01）：

- `python tests/test_registry_family.py` → **12 tests OK**；
- `python tests/test_smith_ir.py` → **365 tests OK**（既有基线不变）；
- `python tools/freeze_smith_set.py --check` → 基线一致：4 份 IR × 5 个后端；
- `python tools/check_coverage.py --check` → OK：28 个组合（0 跳过）全部不丢不编；
- `python -m moss_llm.smith targets` → 8 行（原 7 + `nano-banana`，实现列标「通用」）。

### 证据分级

- **已验证（本仓字节/单测）**：纯 YAML 接入、4 份真机 IR 编译、不变式 6/6、
  roundtrip 0 lost、lint 0 error、确定性、CLI 列出、双解析器一致。
- **未验证（没有实跑）**：该家族真实出图质量、中文 prompt、具体档位差异、
  pitfalls 的 A/B 结论。
