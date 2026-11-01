# T6 · `registry.pitfalls` → 可证伪 A/B（第一轮）

> 2026-11-01。把 `moss_llm/smith/data/model-registry.yaml` 里 3 条「物理事实」断言做成
> **先注册判据、再出图**的成对实验。每条结论落「支持 / 推翻 / 不显著」。
>
> **目检声明**：本轮逐对看图由 **AI 目检完成，不是人类目检**。所有结论按此口径打折。
> 结论区分「已验证（本机实跑 + 本判据）」与「未验证（不在本实验覆盖内）」。

---

## 0. 结论先行

| # | registry 原文 | 后端 | 结论 | 关键证据 |
|---|---|---|---|---|
| P1 | 相机型号/渲染引擎/画师名单写无效（须整句词组化） | MOSS-Image | **推翻** | 3 对中 2 对显示 token 有效：`85mm f/1.2` → A 浅景深/B 深景深；`Hayao Miyazaki` → A 动画/B 写实；只有 `Unreal Engine 5` 一对未出现预期 3D 质感（1/3 支持） |
| P2 | 正文残留写实词会压制开头画风词 | MOSS-Image | **推翻** | 仅 1/3 对出现整体压制（雨夜场景被拉成黑白写实照片）；另 2/3 对维持动画 ⇒ 「会压制」不是普遍规律，是条件性的 |
| P3 | >512 token 静默截断，尾部（光照/媒介/文字）直接丢失 | Krea 2 | **推翻** | 3/3 对的 B 段真实 token **786–797（>512）**，尾部两个 cue 全部保留 ⇒ 本机 ComfyUI 节点未复现 512 位置截断 |

**一句话**：3 条断言里 **0 条**在本轮条件下成立；但 P1 的「渲染引擎名」子项确实无效、
P2 在特定场景会真的被压制、P3 只证明「本机实现没有 512 位置截断」，不能外推到官方
`inference.py`。样本量与目检口径见 §6，**不把 2-of-3 的计数差写成统计显著**。

**对 registry 的改动**：3 条都在 `moss_llm/smith/data/model-registry.yaml` 里。
实验期间该文件由 h3-smith 的 T4/T7 任务占用，本轮**没有直接改**；Lead 已于 2026-11-01
按 §5 的 patch **代落 registry**（原说辞保留在 YAML 注释里，被推翻的条目改写为实测事实）。

---

## 1. 为什么做这个

`docs/22-smith-ir-路线图.md` §5 把「最强的客观标准是预测」写死：把 `pitfalls` 变成可证伪假设
——「MOSS 写相机型号无效」→ A/B 两组生成 → 有/无差异 → 无差异则假设成立。
**不能被推翻的说法，在工程上等于零**（同段原话）。

总账 §8「仍未证」与 §9「判据坑」的共同点：过去所有关于模型行为的结论都是
「读文档 / 读代码 / 看一条样例」得来的，没有一条来自成对出图。这一轮补的就是这一层。

## 2. 选了哪 3 条、为什么、没选哪条

| id | 后端 | registry 原文 | 选择理由 |
|---|---|---|---|
| P1 | moss-image | 相机型号/渲染引擎/画师名单写无效（须整句词组化） | 纯「写了等于没写」的断言，可以直接用「删掉这段 token」做对照 |
| P2 | moss-image | 正文残留写实词会压制开头画风词 | 有方向性（A 应更写实、B 应更动画），可用序关系判定，不需要人判「像不像某画师」 |
| P3 | krea2 | >512 token 静默截断，尾部（光照/媒介/文字）直接丢失 | 按位置丢 ⇒ 同一串词换顺序即可对照；不需要主观风格判断 |

**没选：`minimax-h3` 的「否定式封锁无效：扩散架构没有否定算子，仍会激活被否概念」。**
理由是成本与编排，不是它不重要：

1. 它挂在 H3（视频 + 音轨）上，一条 8 秒视频的 GPU 成本远高于 18 张 1024² 图，
   与本轮「GPU 与 T1 串行」的纪律冲突；
2. 视频的「被否概念是否仍出现」需要逐帧/多帧目检，本工具链目前只有单图目检能力；
3. 若把它改到图像后端上测，测的就不是 H3 这条断言了（后端不同，否定通道不同：
   MOSS 有 `uncond_slg` 真负向通道，H3 没有）。

所以这条**保持原样标「未做 A/B」**，不写「支持」也不写「推翻」。

### 2.1 每条的操作化定义与判定规则（先注册，后看图）

#### P1 · token 名字写无效

- **H0**：直接写相机型号 / 渲染引擎名 / 画师名**不会**改变画面在对应属性上的表现（写了等于没写）。
- **H1**：会改变：A 图出现该 token 指向的可辨识属性，B 图没有。
- **操作化**：每对 A = 基础散文 + 该 token（相机：`shot on a Canon EOS R5 with an RF 85mm
  f/1.2 lens, shallow depth of field`；引擎：`rendered in Unreal Engine 5 with ray-traced
  lighting`；画师：`in the style of Hayao Miyazaki`），B = 同一基础散文不加。
  预期属性**预先写死**（浅景深/3D 渲染质感/宫崎骏式动画），不许看图后补。
- **逐对判定**：A 有、B 无 ⇒ **推翻**（token 有效）；A 无、B 无 ⇒ **支持**（token 无效）；
  其余 ⇒ **本对无法归因**。
- **汇总**：support ≥2 且 > refute ⇒ 支持；refute ≥2 且 > support ⇒ 推翻；否则不显著。

#### P2 · 写实词压制开头画风词

- **H0**：开头已写动画/赛璐璐画风词时，正文后段的写实词**会压制**它：A 比 B 更写实。
- **H1**：不压制：A 与 B 画风档相同，或 A 更动画。
- **操作化**：每对只差尾段——A 尾段是写实词（`photorealistic skin / DSLR / film grain`），
  B 尾段是动画一致的词（`matte shading / flat colors / ink contours`）。整图判一个画风档：
  `anime` / `hybrid` / `photoreal`。
- **逐对判定**：rank(A) > rank(B) ⇒ **支持**；rank(A) ≤ rank(B) ⇒ **推翻**；
  任一张判不出档 ⇒ **无法归因**。

#### P3 · Krea 2 的 512 token 静默截断

- **H0**：encoder 在 512 token 处按位置静默截断，>512 token 处的尾部内容丢失。
- **H1**：不按位置丢，尾部内容同样出现。
- **操作化**：同一对里 A/B 的**词与总长完全相同**，只有顺序不同：
  A = 基础句 + 关键内容 + 长填充；B = 基础句 + 长填充 + 关键内容。
  关键内容是两个可目检的实体（如「右上角红色横幅 + 白色 OPEN 字样」+「桌上黄铜钥匙」），
  在 A 里位于前 ~60 token，在 B 里位于 ~700 token 之后。
- **真实 token 数**（不是启发式）：用 llama-server `/tokenize` 实测 B = **797 / 792 / 786**，
  三对都 >512 ⇒ 这是**有效的截断测试**，不是「没超 512 所以看不出」。
  （tokenizer 是 Qwen3.5-4B，与 Krea2 的 Qwen3-VL-4B 同族，只能算近似真实值；见 §6。）
- **逐对判定**：A 两个 cue 都在、B 至少丢一个 ⇒ **支持**；B 两个 cue 都在 ⇒ **推翻**；
  A 自己也丢 ⇒ **无法归因**。若 `b_tokens ≤ 512` 一律判「无法归因」（不构成测试）。

## 3. 实验装置

### 3.1 后端与采样参数

| 后端 | 权重 | 采样 | 分辨率 | 说明 |
|---|---|---|---|---|
| MOSS-Image | `BSAI-MOSS-Image-SFT-4steps-turbo-merged`（4 步蒸馏 merged） | euler / simple / steps 4 / cfg 1.0 | 1024×1024 | 4 步档直接来自真机工作流子图，不是 registry 的 50 步通用档 |
| Krea 2 | `Krea2-Turbo-HD-V1-NVFP4` | euler / simple / steps 8 / cfg 1.0 | 1024×1024 | Turbo 档；negative 用 `ConditioningZeroOut(positive)` |

API 工作流模板在 `tools/workflows/`（`_index.json` 记录 node id 映射与来源），
提交前用只读 `/object_info` 校验（脚本 `--check-workflows`）；18 张全部一次跑通，
没有模型加载/节点错误。

**种子表**（同一对 A/B 同一个 seed；不同对之间换 seed）：

| 实验 | 对 | seed | 分辨率 |
|---|---|---|---|
| P1 | `P1a-camera` / `P1b-engine` / `P1c-artist` | 11011 / 11012 / 11013 | 1024×1024 |
| P2 | `P2a-cafe` / `P2b-rain` / `P2c-hill` | 12021 / 12022 / 12023 | 1024×1024 |
| P3 | `P3a-cafe` / `P3b-street` / `P3c-market` | 13031 / 13032 / 13033 | 1024×1024 |

P1/P2 的 A/B 真实 token 数（`/tokenize`）16–50；P3 每对 A/B 各 786–797。
这组数字同时在 `out/pitfalls_ab/tokens.json`。

### 3.2 成对纪律（代码钉死）

- 同一对 A/B：**同 seed、同分辨率、同 steps/cfg/sampler/scheduler**；
- P1/P2：A/B 从**同一基础句**起笔，A 只在句后追加被测 token，B 不加；
- P3：A/B **同词同长**，只有顺序不同（单测断言 `sorted(A.split()) == sorted(B.split())`
  且启发式 token 数相等）；
- 以上由 `tests/test_pitfalls_ab.py` 的 `TestPairValidity` / `TestTruncationPair` 钉住，
  `--check` 一有偏差即红。

### 3.3 目检方式

- 每对出图后拼一张 A|B 并排图（`out/pitfalls_ab/<exp>/<pair>/compare.png`），
  逐对 `read_image` 看图，按 §2.1 的观测字段填 `observations.json`（9 对、18 张图全看）；
- `--judge observations.json` 按预注册规则机械求三档，**判定函数不看图**；
- **本轮目检是 AI 读图，不是人类目检**。

## 4. 逐对证据

| 实验 | 对 | 观测 | 逐对分类 |
|---|---|---|---|
| P1 相机 | `P1a-camera` | A 背景明显浅景深虚化、有光学散景；B 景深更深、背景细节更多 | 推翻（token 有效） |
| P1 引擎 | `P1b-engine` | A/B 都仍是写实摄影，未出现 3D 游戏引擎质感（无 CGI/PBR/塑料皮肤） | 支持（token 无效） |
| P1 画师 | `P1c-artist` | A 是宫崎骏式动画（赛璐璐平涂/手绘云/动画五官）；B 是写实照片 | 推翻（token 有效） |
| P2 咖啡店 | `P2a-cafe` | A/B 都是赛璐璐动画风；尾段写实词没把 A 拉成写实 | 推翻（未见压制） |
| P2 雨夜 | `P2b-rain` | A 被尾段写实词整体拉成写实/电影感黑白雨夜照片；B 是漫画线稿+网点 | 支持（出现压制） |
| P2 山头 | `P2c-hill` | A/B 都是动画/水彩风；A 的云仍是手绘质感 | 推翻（未见压制） |
| P3 咖啡店 | `P3a-cafe` | cue=[红横幅+OPEN, 黄铜钥匙]；A/B 都保住两个 | 推翻（未丢尾部） |
| P3 街道 | `P3b-street` | cue=[绿灯笼, 白纸袋]；A/B 都保住两个（B 人物头部被裁，物体都在） | 推翻（未丢尾部） |
| P3 市场 | `P3c-market` | cue=[黄伞, SALE 招牌]；A/B 都保住两个（B 的伞收拢/被裁切，物体在） | 推翻（未丢尾部） |

逐对的图片与完整 observation：`out/pitfalls_ab/<exp>/<pair>/compare.png`、
`out/pitfalls_ab/verdicts.json`、`out/pitfalls_ab/pair_table.md`。

## 5. 对 registry 的改动（Lead 已于 2026-11-01 代落）

`moss_llm/smith/data/model-registry.yaml` 实验期间被 task-2/task-5 占用，本轮**未直接改**；
Lead 已于 2026-11-01 代落 registry。**落盘措辞与本 §5 的 patch 语义一致，但不是逐字相同
（krea2 是合并写法；moss-image 两条逐字一致）**——本节 yaml 块是**建议写法/证据说明**，
以 registry 落盘措辞为准，两边不要求逐字对齐，也不要在任何一边再加第三种说法。

```yaml
# ── moss-image.pitfalls（与落盘逐字一致）──
# 2026-11-01 本机 A/B 推翻原说法「相机型号/渲染引擎/画师名单写无效」（证据见 docs/30 §4）。
- "「相机型号 + 镜头」与「画师名」会改变画面（实测 2/3 对：85mm f/1.2 → 浅景深、宫崎骏 → 动画风）；只有单独写渲染引擎名（Unreal Engine 5）没出现预期 3D 质感 ⇒ 引擎名别当风格开关，改用视觉描述"
- "SLG 不是画风开关"
# 2026-11-01 本机 A/B 推翻原说法「正文残留写实词会压制开头画风词」的普遍性（3 对里 1 对成立）。
- "正文尾段的写实词是否压过开头画风词是**条件性**的：3 对里 1 对整体翻成写实、2 对维持动画 ⇒ 要保画风就把写实词从正文清掉，不能指望开头那句画风词兜底"
```

```yaml
# ── krea2.pitfalls（落盘版：原句在前 + 括号合并实测说明；与 patch 语义一致、非逐字）──
# 2026-11-01：官方 inference.py 的 512 位置截断风险保留；但本机 ComfyUI 节点实测未复现
# （真实 token 786–797 仍保住尾部 cue 3/3，见 docs/30 §4），别把本机节点尾部内容当会丢。
- ">512 token 静默截断，尾部（光照/媒介/文字）直接丢失（官方 inference.py 行为；本机节点实测未复现，见 docs/30）"
```

最终选择的是「**保留原句 + 括号合并说明**」这一写法（即上面的落盘版），
不是把两句拆成两条 pitfall。

**已落盘**：Lead 于 2026-11-01 代落 registry（moss-image 两条、krea2 一条，原说辞保留在
YAML 注释里）；落盘后 `python -m moss_llm.smith targets` 正常列出 8 个 target，
`tests/test_smith_ir.py` 的 365 条基线仍绿（复验记录见本任务汇报）。

## 6. 局限性（必须连同结论一起读）

1. **AI 目检不是人类目检**：风格档、属性有无都是读图判断，没有人类复核。
2. **样本量 3 对/条**：2-of-3 规则下，3 对全一致的最小单侧二项 p≈0.125，
   这只能算**方向性证据**，不是统计显著。本轮 3 条都落「推翻」，其中 P1 是 2:1、
   P2 是 2:1，都处在 2-of-3 的边界上。
3. **单后端 + 特定量化/蒸馏档**：P1/P2 在 MOSS-Image **4 步 turbo merged** 上测；
   P3 在 Krea 2 **Turbo HD V1 NVFP4** 上测。结论不外推到 bf16 / 50 步档或其他量化。
4. **英语 prompt**：registry 的 prompt.language 是 en，所以用英文测；中文使用习惯下的行为未覆盖。
5. **P3 测的是本机 ComfyUI 的 Krea 2 节点实现**，不是官方 `inference.py`。
   真实 token 数用 Qwen3.5-4B tokenizer 近似（同族，非 Krea2 encoder 本身）；
   结论「本机节点未按位置截断」成立，「官方实现不会截断」不成立。
6. **P3 的 cue 是常见场景元素**（红横幅/灯笼/伞/纸袋/钥匙/SALE 招牌）：B 保住 cue 可能部分
   来自常识先验而非 prompt 生效。三对、两 cue 全中降低了这种可能，但没排除。
7. **单 seed 单图/对**：同 seed 下换一个 seed 结论可能不同；本轮不做多种子重复。
8. P2 的「压制」在雨夜场景真的发生了 ⇒ 该条的正确用法是「条件性风险」而不是「总会/总不会」。

## 7. 复现

```powershell
# 只读校验（不占 GPU）
python tools/pitfalls_ab.py --check
python tools/pitfalls_ab.py --check-workflows
# 落计划 + 生成（生成会占 GPU；本机实测 18 张约 5 分钟）
python tools/pitfalls_ab.py --plan
python tools/pitfalls_ab.py --run
# 目检后：填 out/pitfalls_ab/observations.json 再机械判定
python tools/pitfalls_ab.py --observations-draft --force
python tools/pitfalls_ab.py --tokenize          # 真实 token 数（占 GPU 约 1 分钟）
python tools/pitfalls_ab.py --inject-tokens
python tools/pitfalls_ab.py --judge out\pitfalls_ab\observations.json
python tests/test_pitfalls_ab.py
```

产物：`out/pitfalls_ab/plan.json`、`manifest.json`（每张图的 prompt/seed/参数/prompt_id）、
`<exp>/<pair>/{A,B}.png`、`compare.png`、`observations.json`、`tokens.json`、
`verdicts.json`、`pair_table.md`。
