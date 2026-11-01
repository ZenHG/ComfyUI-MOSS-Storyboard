# 节点 caption 护栏（M1a-2）

> 2026-11-01。M1 第一梯队第二阶段：把 M1a（`docs/42`）在 **smith 反推管线**上修好的
> 两类护栏，接到**节点 `MossPromptSmith` 的 caption 路径**上——ComfyUI 用户真正的入口。
> 证据分级：**A**=本机实跑 ｜ D=推算。本轮零 GPU、零网络（桩服务端单测）。

---

## 0. 一句话

用户用节点，不用 CLI：M1a 的两条护栏（类型闸、重复保护）原本只覆盖
`moss_llm/smith/reverse.py`（CLI reverse / G7 工具 / 洗图），节点 caption 走的是自己
的 prompt pack + `nodes.py::_chat`，**完全没接线**。本任务把同一份判据函数接进节点，
并把两遍式里的「盘点 JSON」变成**护栏后的盘点**，再做 pass2 写稿。

| 项 | 状态 |
|---|---|
| 类型闸进节点 caption（`kind_downgrades` / `age_dropped` 进 report） | **已完成**（复用 `gate_character_kind`，字段名映射，不是另写一份） |
| caption JSON 实体上限（schema `maxItems` + prompt 规则 + 后处理截断并出声） | **已完成** |
| `_chat` 的 `repeat_penalty` 核对与补齐 | **已完成**（JSON 请求显式发送；被拒时降级并出声） |
| 两遍式 / `_JSON_RETRY` / 结构化降级 / thinking 关闭不回归 | **已验证（本机单测）**：21 条，零 GPU |
| 真机效果（schema 档实际走通、caption 质量变化） | **未验证**（本轮无 GPU；见 §5） |

---

## 1. 两条路径的差异（为什么不能只修 smith）

| | smith 路径（M1a 已修） | 节点路径（本任务） |
|---|---|---|
| 入口 | `moss_llm/smith/reverse.py::reverse()` / CLI `reverse` / G7 工具 / 洗图 LLM 段 | ComfyUI 节点 `MossPromptSmith`，`task=caption` |
| 提示词 | `reverse.SYSTEM`（12KB 内联常量） | `moss_llm/prompts/caption.*.txt`（prompt pack，manifest sha 校验） |
| 请求 | `LlamaServer.build_body`（`server.py`：DRY 摘除/`repeat_penalty`/四级结构化降级） | `nodes.py::_chat`（自建 urllib payload；原来只有 `response_format: json_object`） |
| 产出 | **IR**（entities/relations/timeline → 可编译） | **caption 文本**（两遍式：pass1 盘点 → pass2 写稿；单遍式直接写） |
| 实体结构 | 扁平 `entities[]`（kind 是 character/object/text/background） | `subjects[]` + `environment.props[]` + `materials[]` + `text[]`；**没有 kind 字段**（本轮在 prompt/schema 里补了 `kind: person|object`） |
| 类型闸落点 | `coerce_ir`：character→object（IR 层） | `guard_caption_json`：subject→`environment.props`（**inventory 层**，让 pass2 不再把物体当人写） |
| 实体上限 | `MAX_ENTITIES=40`（schema + coerce 截断） | inventory 各数组：subjects 12 / props 32 / materials 16 / text 24（schema `maxItems` + 后处理截断） |
| 防重复 | schema + prompt + coerce + `server.py` 的 `repeat_penalty` | schema + prompt + 后处理 + `_chat` 的 `repeat_penalty` |

**同一个判据，两个字段口径**：`gate_character_kind(kind, desc, person_fields=…,
object_fields=…)` 已从私有函数提为公共函数（`moss_llm/smith/reverse.py`，保留
`_gate_character_kind` 旧名别名）。节点传自己的字段名：

- 人物线索（任一非空即保留 person）：`gaze / expression / hands / feet / pose / wardrobe`
- 物体字段（判据要求非空）：`appearance`

这样「没有人物线索、却有一身外观描述」的 subject 被判为物体，从 `subjects` 挪到
`environment.props`，同时**丢掉** `age_years/age/age_confidence/gender/hair/skin/face/
expression`（非人物不允许这些字段），并逐条写进节点 report。

`kind` 缺失或**未知**（例如降级档模型自由写成 `"animal"` / `"people"` / `"vehicle"`）也走
同一份字段闸：有任一 person cue 才保留 person，否则按 object 进 props 并出声——不能因为
`kind` 不在 schema enum 里就直接放行（那正是物体留在 subjects 的漏洞）。

---

## 2. 接线细节（最小改动、可单测）

### 2.1 prompt pack（`moss_llm/prompts/`，manifest sha 已重生成）

- `caption.pass1.en.txt`
  - subject 模板加 `"kind":"person"`；
  - 规则加：`kind=person` 仅限人/拟人；杯窗灯车食物工具墙布动物雕像 → `object`，放
    `environment.props`/`materials`；非人物禁 age/gender/hair/skin/expression；
  - 数量上限写进 prompt：subjects ≤12、props ≤32、materials ≤16、text ≤24；
    每个 subject 只写一次、不要换 position 重复。
- `caption.pass2.en.txt`
  - 加一条：inventory 里 `environment.props`/`materials` 的东西是**物体**，不许写成人。
- `manifest.json` 由 `python -m moss_llm.pack --write` 重新生成（12 个条目全部重算）；
  **实际只有 `caption.pass1.en`、`caption.pass2.en` 两条 sha 变化**，其余 10 条重算后与
  原值相同。**版本号仍是 1.0.0**：`pack.py::write_manifest` 里写死，本任务写域不含
  `pack.py`；改动通过每次 report 里的 pack sha 可追溯。若要升版本号需改 `pack.py`
  （列为 follow-up）。

### 2.2 节点侧护栏（`nodes.py`）

- 常量：`_CAPTION_MAX_SUBJECTS/PROPS/MATERIALS/TEXT/KEEP`、
  `_CAPTION_PERSON_CUES`、`_CAPTION_REPEAT_PENALTY=1.05`。
- schema：`CAPTION_INVENTORY_SCHEMA` / `CAPTION_ONESHOT_SCHEMA` / `CAPTION_PASS2_SCHEMA`
  （只锁形状与数组上限，**不 required 字段**——全字段 required 会逼模型编值）。
- `guard_caption_json(obj)`：类型闸 + 各数组截断，返回 `(obj, notes)`；`notes` 就是
  report 里的用户可见说明：
  - `反推类型闸：N 个物体被误标为 subject，已降级为 props（否则会被写成人、编年龄）：…`
    （未知 `kind` 会写明「未知 kind='animal'，按字段闸判为物体：…」）
  - `反推年龄闸：从非人物体上丢弃年龄字段 N 处：…（age_years=80）`
  - `反推实体上限（capped）：subjects 12（达到上限 12；疑似重复/截断，请人工确认）；…`
    —— **达到上限就要出声**（不只是超过）：`json_schema` 档的 `maxItems` 可能在语法层
    先把重复循环静默截停，后处理看到恰好 N 条会误以为成功；恰好 N 条也可能是合法画面，
    所以措辞是「疑似重复/截断，请人工确认」，不做断言。
  - 降级进 props 时**只保留** `role / appearance / position`，以及 `count>1`（写成
    `2 × mug — ceramic` 并在 report 点名「count=2 已保留在 props 文案」）；
    `wardrobe/pose/gaze/hands/feet` 属人物专属字段，物体语义下不写入 props（不假装它们
    对物体有意义）；被丢弃的年龄/人物字段逐项进 report。
- 两遍式：`_run_caption_two_pass()` 在 pass1 解析后立刻 `guard_caption_json(inv)`，
  **再**把护栏后的 inventory 交给 pass2；单遍式：解析成功后也过一遍。
  **单遍式的口径要注意**：guard 在 `_pick_prompt(obj)` 之后调用，只影响 report 与机检
  用的 obj，**不改写已经取出的 caption 文本**——单遍式的数组上限是 report 口径，不是
  输出截断。
- `_chat` 结构化**四级降级**：`json_schema → json_object → 无 response_format →
  再摘 chat_template_kwargs`；每一级失败（HTTP 400）才往下走，并在 report 里说明。
  JSON 请求显式带 `repeat_penalty`；严格 OpenAI 不认它时先摘它（比先降结构化档少一次
  语义降级），摘除也出声。
- `_run_once()`：把原来写在 `smith()` 里的「一次请求 + JSON 容错 + 解析失败重试一次」
  抽成方法（行为逐字保留，方便桩测试）；两遍式抽成 `_run_caption_two_pass()`。

### 2.3 report 可见性

`guard_caption_json` 的 notes、`_chat` 的降级 notes、`_JSON_RETRY` 的重试说明都进
节点第二路输出 `report`（用户复盘用），不是只写日志。

---

## 3. 测试（`tests/test_node_caption_guard.py`，21 条，零 GPU）

| 组 | 条数 | 钉住什么 |
|---|---|---|
| `TestCaptionGuard` | 12 | 真人物不误降级；显式 `kind=object` 降级+年龄丢弃+report 出声；无 kind 时按字段判；**未知 kind（animal/people）走字段闸降级且出声**；未知 kind + person cue 保留 person；显式 `kind=person` 只有 appearance 也被字段闸降级（行为有意，见 §4.3）；降级 `count>1` 保留进 props 文案并 report 点名；subjects/props/materials/text 上限截断并报告；**数组恰好达到上限也报 `capped` 疑似截断**；schema 的 `maxItems` 与常量一致；**复用同一份 `gate_character_kind`**；旧行为（物体留 subjects）必红 |
| `TestCaptionChat` | 5 | JSON 请求带 schema + `repeat_penalty` + `enable_thinking=false`；schema→json_object 降级；四级降级（含 flag）；`repeat_penalty` 被拒时降级出声；非 JSON 请求不变 |
| `TestCaptionPaths` | 4 | 两遍式顺序 pass1→pass2、pass2 收到**护栏后**的 inventory（物体进了 props）；pass1 非法 JSON 时退回单遍（不跑 pass2）；单遍解析失败重试一次（`_JSON_RETRY`、温度 −0.1）；重试仍失败退回纯文本 |

「旧行为必红」同样是手工复现修复前的形态（物体留在 subjects、违反护栏判据），断言它
会红——不是永远绿的测试。

实跑（2026-11-01）：

```
python tests/test_node_caption_guard.py  → Ran 21 tests — OK
python tests/test_llm_subpackage.py      → 通过 98 / 失败 0
python tests/test_caption_quality.py     → Ran 25 tests — OK
python tests/test_smith_ir.py            → Ran 365 tests — OK
python tools/smoke_nodes.py              → 结果：通过 63 / 失败 0
ruff check .                             → All checks passed
```

prompt 文本改动后 `python -m moss_llm.pack --write` 已重生成 manifest，`load_pack`
sha 全部相符（`test_llm_subpackage` 的包 sha 校验仍绿）。

---

## 4. 已知限制

1. **节点 caption 不产出 IR**：护栏只能作用在 inventory JSON 的 `subjects` 上，
   「实体上限」是各数组上限，不是 smith 的扁平 `entities` 计数；两套数字不能直接互比。
2. **`age_dropped` 在节点路径通常为空**：节点 prompt 本来就不让写数字年龄（用 life
   stage），只有模型额外编造 `age_years/age/age_confidence` 字段时才会触发丢弃。
   这是如实反映，不为了「有输出」去编。
3. **误降级边界（两条）**：① 背影/遮脸人物如果 `gaze/expression/hands/feet/pose/wardrobe`
   全空、只有 `appearance`，会被判成物体挪进 props；② **显式 `kind:"person"` 也受同一
   字段闸**（模型也会把物体标成 person；判据用字段、不猜身份），只有 appearance、无任何
   person cue 时同样降级进 props 并在 report 说明原因。判据刻意不猜语义（与 M1a 同一条
   纪律），但会把这类降级逐条写进 report 供人复核；误伤率无真机数据。
   **kind 未知/缺失**不会绕过判据：未知 kind 也走字段闸，有 person cue 才保留 person。 
4. **schema 强制程度依赖服务端**：llama-server 认 `json_schema` 时 `maxItems` 是语法级
   硬闸；不认时降级到 `json_object`/无约束，上限只剩 prompt + 后处理。四级降级路径已
   单测模拟，但没有对每个真实端点逐一验证。
5. **`repeat_penalty` 仍是 1.05（未调参）**：它只是次级防线；主闸是 schema/prompt/后处理。
   外部 API 不认时会被摘掉（report 说明），此时重复保护只剩 prompt + 后处理。
6. **prompt pack 版本号未升**：manifest 的 `version` 由 `pack.py` 写死 1.0.0，本任务写域
   不含它；sha 已更新、report 可追溯。升版本号作为 follow-up。

---

## 5. 未验证项（诚实清单）

- **真机 caption 质量**：本轮零 GPU，没有跑真实模型。schema 档在本地 llama-server 上
  是否真的被接受（还是走 json_object 降级）、prompt 改动后 caption 是否更少把物体写成
  人、材质描述是否更贴实体，都**未验证**——需要一次节点真跑（或下一轮 G7 用节点路径）。
- **外部 API 端点的降级行为**：严格 OpenAI 兼容端点对 `json_schema`/`repeat_penalty`
  的支持与拒绝顺序未逐家核对；降级路径按 400 逐级试，最坏多几次 400（不计费但不为零）。
- **两遍式护栏对最终 caption 的影响幅度**：单测只证明 pass2 收到的 inventory 变了，
  没证明最终文案质量变化。
- **性能**：带 schema 时「服务端全不认」最坏 **9 次请求 / 9 次 400**（顺序：
  `schema+repeat → schema 无 repeat → json_object+repeat → json_object 无 repeat →
  无 response_format+repeat → 无 response_format 无 repeat →（摘 thinking 后重来）
  schema → json_object → 无 response_format`）；不带 schema 的路径最坏 6 次。本地毫秒级，
  外部端点的延迟未测。

---

## 6. 改动文件

| 文件 | 性质 |
|---|---|
| `moss_llm/nodes.py` | 修改：caption 护栏常量/schema/`guard_caption_json`；`_chat` 四级降级 + `repeat_penalty`；`_run_once` / `_run_caption_two_pass` 抽出（行为不变）；report 接 notes |
| `moss_llm/prompts/caption.pass1.en.txt` | 修改：`kind` 字段 + 类型/数量/不重复规则 |
| `moss_llm/prompts/caption.pass2.en.txt` | 修改：物体不许写成人 |
| `moss_llm/prompts/manifest.json` | 重新生成（sha 更新） |
| `moss_llm/smith/reverse.py` | **最小改动**：`_gate_character_kind` → 公共 `gate_character_kind`（可传字段名），保留 `_gate_character_kind` 别名；逻辑与行为不变（M1a 测试仍绿） |
| `tests/test_node_caption_guard.py` | 新增：21 条 |
| `docs/46-节点caption护栏.md` | 本文 |

不 `git add` / 不 commit。回滚 = 还原上述文件 + `python -m moss_llm.pack --write`
重新生成 manifest。

---

## 7. 复现

```bash
python tests/test_node_caption_guard.py
python tests/test_llm_subpackage.py
python tests/test_caption_quality.py
python tests/test_smith_ir.py
python tools/smoke_nodes.py
ruff check .
```

`test_llm_subpackage` 会验证 prompt pack 的 manifest sha：改完 prompts 必须
`python -m moss_llm.pack --write`，否则加载直接报错（这是设计：提示词是看不见的代码）。
