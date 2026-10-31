# 业界怎么处理我们撞到的问题（2026-10-31）

五个方向并行调研（文字提取 / 坐标 grounding / 幻觉与结构化输出 / 评测 / 编译器先例），
下面是**结论 + 采纳情况**。原则：能落到本机、能落到代码的才算数，其余记成「知道但不做」。

---

## 0. 一页结论

| 我们撞到的问题 | 业界做法 | 我们的处置 |
|---|---|---|
| UI 截图里文字一个没抄 | 专用 OCR（PP-OCRv5 / RapidOCR / OmniParser）+ 把结果注入 VLM | **部分采纳**：本机 pip 装不上 OCR，改为「VLM 文字专线」（第二遍只转录）。接口留给 OCR |
| bbox 7 个错 3 个，anchor 全对 | 专用 detector（Florence-2 / GroundingDINO）出框，VLM 只出语义 | **采纳其结论**：不再「修坐标」，改「换通道」——位置用方位，尺寸用模型 |
| 模型给不存在的字段编值（`"fill": "none, ..."`） | 语法约束 + prompt 里重述 schema | **采纳**：schema 只 required 三项 + prompt 明写「没内容就省略键」 |
| note 反复说同一句 | DRY 采样（llama.cpp 原生支持） | **采纳**：散文调用开 DRY，JSON 调用必须关 |
| 空审计被当成「通过」 | AMBER 类基准 + 应用层拦截 | **采纳**：`minItems: 1` 语法兜底 + `degraded` 判定 |
| 「prompt 写得好不好」没有客观分 | VQAScore（算 "Yes" 的 logprob，不让模型自由生成） | **不采纳（本版）**：要再跑一次视觉模型，且需要生成图。记进下一步 |
| 有没有现成的「IR → prompt」编译器 | 无成熟开源实现；DSPy 的 Signature/Module 是最接近的设计 | **确认自研合理**，但照抄 DSPy 的分层与 LiteLLM 的能力矩阵写法 |

---

## 1. 文字提取（Agent A）

**业界做法**
- **PP-OCRv5**：Apache-2.0，CPU 1.75s/图，中英混排稳。
- **RapidOCR**：ONNX 封装，纯 CPU 可跑，最省事。
- **OmniParser**（~25k★）：给 UI 元素**归一化 bbox**，是「截图 → 结构化」这条路的现成件。

**关键发现（决定我们怎么做）**
- **没有一个本地 OCR 会输出字重/字号/颜色**。唯一成熟实现是 Azure DI 的 `styleFont`（云、付费附加）。
  ⇒ 所以「排版细节」这一维，短期只能靠 VLM 猜，不要假装能算准。
- 别把 VLM 喂到 1536 宽再让它读小字：正确姿势是**按 1:1 切块**，逐块 OCR，再把「文本 + 框」注入 VLM prompt。

**我们做了什么**
本机 `pip install rapidocr-onnxruntime` 失败（索引里没有该包），OCR 路线当前不可验证。
于是用**任务拆分**替代模型替换：加一遍「只转录」的 VLM 调用（`transcribe()`），
把「这图里有什么」和「把字抄下来」拆成两个请求。
实测 1680×1000 的 ComfyUI 截图：改前 0 条文字，改后 9–20 条中文逐字正确。

**没做**：OCR 适配器。等能装上依赖再补，接口已按「blocks: text + where」的形状定好。

---

## 2. 坐标 / grounding（Agent B）

**业界做法**
- **Florence-2-large-ft**（MIT，2–3GB，1000 bins 编码坐标）
- **GroundingDINO-T**（Apache-2.0，48.4 AP）
- GroundingDINO 1.5 **不开源**；YOLO-World 是 **GPL-3.0**（传染性，开源项目要避开）
- Molmo 的经验：**纯文本坐标比专用 location token 更准**（89.4 vs 85.8）

**关键判断**
> 方位 7/7 对 = 语义与空间关系没看错；3/7 落错象限 = 坐标输出通道坏了。
> 所以不该修坐标，该换通道。

这句话直接改掉了我们的实现。原先 `_reconcile_bbox` 是「按方位裁剪坐标」——
裁剪后**尺寸仍来自坏通道**，只能算半修。现在是 `_resolve_bbox`：
冲突时位置取方位、宽高留模型，`spatial.bbox_source` 记录来源（`model` / `anchor`）。

**没做**：接 Florence-2 / GroundingDINO。理由：多一个 2–3GB 权重、多一套依赖，
而当前收益（文字块排布）用九宫格 + 竖排等分已经够。真要做分割时再说。

---

## 3. 幻觉与结构化输出（Agent C）

**业界做法**
- AMBER 是长描述幻觉的合适基准。
- VCD / M3ID / OPERA / DoLa 这些「解码层去幻觉」方法**在 llama-server 上全部不可行**——
  它们要 logits processor 钩子，服务端没暴露。

**关键发现（最有用的一条）**
> "The JSON schema is only used to constrain the model output and is not injected into the prompt.
> The model has no visibility into the schema."

这解释了我们见过的 `"fill": "none, shadows softly diffused"`：语法只保证形状，
模型根本不知道有哪些字段该填什么。⇒ **schema 与 prompt 必须各写一遍**，缺一不可。

另外：llama-server **原生支持 DRY**（`--dry-multiplier` 及请求体字段）与 `repeat_penalty`。

**我们做了什么**
1. `EVIDENCE_SCHEMA` 只 required `canvas` / `style` / `entities`；全 required 会逼模型编值。
2. prompt 加规则 10：`Never write none / null / unknown / n/a`，没内容就**省略键**。
3. `response_format` 四级降级梯，每降一档出声。
4. DRY 只在散文调用开；JSON 调用整族摘掉（键名重复是 JSON 的常态）。
5. 空取证硬闸：`entities` 为空且无 notes 无 medium ⇒ 直接抛，不许下游兜底。

**没做**：VCD / OPERA / DoLa。解码层无钩子，做不了。

---

## 4. 评测（Agent D）

**业界做法**
- CLIP-Score **不可靠**（本质是词袋），别用。
- **VQAScore**：问「这张图符合这句话吗」，取 **"Yes" 的 logprob**，不让模型自由生成。
  2B/4B 模型在 12GB 上跑得动（`t2v_metrics`）。
- 偏好类指标（ImageReward / PickScore / HPSv2）测的是「好不好看」，不是「像不像」。
- LLM-as-judge：成对比较翻转率 13.6%，**要 ~11 次重复投票才有 95% 置信度**；
  风格偏好带来的偏差（0.10–0.76）远大于位置偏差（≤0.04）。
- 「VQAScore2」**不存在**（arXiv 零命中），别被名字骗。
- VI-Bench 是唯一 round-trip 基准，且是视频；最强模型只有 0.632。

**我们的处置**
本版不接 VQAScore（要生成图，我们没有生成器）。但两条纪律写进了设计：
- 审计分**不是**质量分，只是「说错说漏」分，README 里写明。
- 单次审计结果不当作定论——`degraded` 判空、分数不涨就停、主角不因一句话被删。

---

## 5. 编译器先例（Agent E）

**结论**：没有成熟的「IR → 各模型 prompt」开源项目，只有玩具仓。自研是对的。

值得抄的两个设计：
- **DSPy** 的 Signature / Module / Optimizer 分层——IR 与渲染文本解耦，可换优化器。
  我们的对应物是 `clauses.build_clauses`（子句计划）与 adapter 分离。
- **LiteLLM** 的**显式能力矩阵**——不靠猜，每个模型写清支持什么。
  我们的对应物是 `model-registry.yaml` + `targets` 命令打印矩阵。

**promptfoo**（27k★，可全本地跑）是回归骨架的合适参考，但**图像 prompt 需要的结构性断言
（bbox IoU、文字渲染准确率）没有任何开源实现**——这部分得自己写。

---

## 6. 这次真正落地的改动

| 文件 | 改动 |
|---|---|
| `moss_llm/smith/llm.py` | `response_format` 四级降级；DRY 与 JSON 模式互斥；`build_body()` 可离线单测；attach 模式（`--base-url`）；alias 指纹防「借用别人的模型」；`/v1/models` 兜底探活 |
| ~~`moss_llm/smith/runtime.py`~~ | 已**删除**（2026-10-31 合流）：父包 `runtime_install.py` 有镜像链 / 摘要校验 / 续传 / 原子替换，比它强；cuda 主包与 cudart 的版本对齐也搬过去了 |
| `moss_llm/smith/reverse.py` | `EVIDENCE_SCHEMA`；文字专线 `transcribe()` + `merge_text_blocks()`；`_resolve_bbox` 换通道；0-1000 刻度；空取证硬闸；`spread_anchored()`；UI 截图不判分格 |
| `moss_llm/smith/audit.py` | `AUDIT_SCHEMA`（`claims.minItems=1` 堵空审计） |
| `moss_llm/smith/lint.py` | L005 跳过引号内内容（图内文字本来就写着 "cfg"）；L010 按空白归一化比对 |
| `moss_llm/smith/vision.py` | 比例收敛改用 `Fraction.limit_denominator(20)`，不再产出 `15:9` |
| `moss_llm/smith/cli.py` | `setup` 子命令；`--base-url` / `--no-schema` / `--no-text-pass` / `--vision-tokens`；请求期间的 server notes 当场回显 |
| `tests/test_core.py` | 116 → 156 条，全绿 |

---

## 7. 一条教训（写给以后的自己）

这次最贵的错不是代码，是**把本机环境当成了世界**：
看到一个停止服务的 `.bat` 里写着 `wsl -d Ubuntu -e pkill llama-server`，
就断定服务跑在 WSL 里。实际上 llama-server 一直在 Windows 侧原生跑，权重在本机的模型目录里（本文不记具体路径）。

对开源项目来说，任何「我这台机器是这样」的推断都必须降级成「这是一种可能」。
所以现在 `find_exe()` 有五条路、`--base-url` 排第一、`setup` 能自己装。
