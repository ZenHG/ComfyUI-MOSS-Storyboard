# 20 · 可选 LLM 子包 `moss_llm`：一个节点，只出成品提示词

> 状态：**P0 已落地并真机验通**（2026-10-30）。提案原文与调研在
> 维护者本地交接目录 `_handover/新节点-反推与扩写-设计与提示词框架-2026-10-30.md`（不随包分发）（含 6 路调研、十二条跨源共识、
> 学术证据与许可矩阵），本文只记**落地形态、与提案的差、实测证据、边界**。
>
> 🔴 **它不属于核心 7 个节点**（分类 `LLM`）。删掉 `moss_llm/` 整个目录，核心照常工作；
> 核心也不 import 它。`docs/14` §六「不做 LLM 自动补词、内容与节奏归作者」这条**没有作废**——
> 本子包是作者**主动伸手要一次词**的工具：一次排队、一次调用、一份可读报告，
> 不自动改写编辑器 state、不自动排队、不替作者决定节奏。

## 一、节点：`MossPromptSmith`（界面名 `LLM Prompt` / **LLM 提示词**）

提案里是 `MossLLMConfig` + `MossPromptSmith`（+ 可选 `MossPromptCheck`）三个。
落地按 2026-10-30 的设计决定收成**一个全功能节点，对外只出完整提示词**：

```
MossPromptSmith
  输出  提示词 : STRING → CLIPTextEncode.text / Sampler.positive
        report : STRING → 人读：通道、模型、pack 版本与 sha、tokens、耗时、机检计数、降级出声
```

配置进了同一个 `INPUT_TYPES()`，不再拆一个配置节点——多一个节点就多一层
「配置在哪、词从哪来」的追踪成本。`MossPromptCheck` 没做成节点，它的判据以纯函数形式
内建在每次跑完后（`_checks`），结果写进 report；闸本身是 `tests/test_llm_subpackage.py` 的对象。

**参数面 18 项**（0.5.2 起）：`task` `model` `text` `style_class` `style` `target` `style_note`
`length` `temperature` `panels` `language` `keep` `seed` `api_base` `api_model` `api_key`
`speed` `runtime`。39 项时代的引擎细项（ctx / ubatch / 显存目标 / MTP / 端口 / 释放）
不再出现在面板上，由 `speed`（省显存 / 均衡 / 尽量快）一档承担；内部仍按旧 39 个变量运行
（`smith()` 展开），能力没丢，只是不再要求用户先懂引擎。

任务旋钮 `task`：**反推 / 洗图 / 扩写 / 编辑 / 拼装**（`_TASK_ALIAS`；洗图 = 反推 + 忽略缺陷）。
写法由两级承担：`style_class` 选类（连贯描述 / 风格 / 自定义 / 标签串 / 分段结构），
`style` 选具体档。风格类只注入一行 `style_lock`、不新增方言，详见 §九。
`dialect` 是内部名（`moss_image` / `moss_storyboard` / `generic_natural` / `generic_tags` /
`cn_natural`），由 `style` 映射得到，不再是用户旋钮。

## 二、通用性（开源前提，提案的修订）

| 点 | 落地做法 |
|---|---|
| 模型 | 扫 `models/LLM` + `models/text_encoders` 的全部 GGUF，按相对路径显示。**不认架构**——llama.cpp 能载的就能跑，Qwen 全系列 / MOSS TE / 别的 arch 一视同仁 |
| mmproj | 0.5.2 起**只保留自动**：按**宽度硬配对**（模型 `<arch>.embedding_length` = 塔 `clip.vision.projection_dim`）。优先级 = 同名权重 > 同目录唯一 > 全局唯一；**多个候选都能配上就报错让你选，不猜**（手选塔那一格随 18 项合并下线）。配不上直接 raise，不静默退化成纯文本 |
| MTP | 读 GGUF 元数据 `*.nextn_predict_layers` 的**取值**（不看文件名）。默认 `draft-mtp d=2`；**DFlash 不用**（对 MOSS target 接受率实测 10–31%） |
| 运行时 | `runtime` 一个档位定来源：`auto` = 环境变量 `MOSS_LLM_SERVER` → `LLAMA_CPP_HOME` → 本包 `runtime/` → `PATH`；`runtime` = 只认本包 `runtime/`（`MOSS_LLM_RUNTIME_DIR` 可指到持久目录）；`path` = 只认 `PATH`。**不扫别的节点包的 runtime**（那是把别人的目录结构写成本包的隐性依赖） |
| 自动下载 | `runtime=自动下载`：只有**找不到**可执行文件时才联网装，装前先报落点与体积。按后端挑包（N 卡→CUDA 且不超驱动版本；AMD Windows→Vulkan、Linux→ROCm；Intel→Vulkan；Apple→Metal；认不出→CPU），再下主包 + 同版本 cudart；`.zip` / `.tar.gz` 都支持，带断点续传与慢速熔断。**直连失败自动走内置镜像链**（`gh-proxy.com` → `ghproxy.net`），每个包先用 GitHub API 的 `sha256` 校验、对不上就丢弃换源——镜像只影响速度，不影响可信度；可用 `MOSS_LLM_MIRRORS=前缀1,前缀2` 替换镜像链，`MOSS_LLM_MIRROR` 指定单个镜像。覆盖后端用 `MOSS_LLM_BACKEND`，覆盖地址用 `MOSS_LLM_RUNTIME_URL`。CLI 同义：`python -m moss_llm.runtime_install` |
| 外部 API | `channel=外部 API`：任何 OpenAI 兼容端点（OpenAI / 火山方舟 / 智谱 / Kimi / MiniMax / DeepSeek / Ollama / LM Studio…）。不起侧车、不占显存。Key 两处来源：直接填 `api_key`（会随 JSON 分享出去，report 会警告），或放在宿主进程环境变量 `MOSS_LLM_API_KEY` 里（界面不提供变量名那一栏，免得让用户背目录/环境知识；旧工作流里存过的自定义变量名仍然生效）。`api_base` 决定线格式（四种，都不用选旋钮）：`/anthropic` → `/v1/messages` + `x-api-key`；`generativelanguage.googleapis.com` 或 `/v1beta` → `models/<model>:generateContent` + `x-goog-api-key`；`/responses` → OpenAI Responses 线格式（`instructions` + `input` + `max_output_tokens`）；其余 → `/chat/completions`。Azure 主机（`*.openai.azure.com`）自动改用 `api-key` 头，带 `?api-version=` 的端点也不会被拼坏。出站带 `User-Agent` 并声明 `Accept-Encoding: identity` —— urllib 默认 UA 会被 Cloudflare 一类网关拦成 403/1010；opencode 的 Go 网关另外要求 `x-opencode-session`（缺了直接 400 `MissingSessionID`），已按主机自动带上稳定会话 id；其它网关要的自定义头用环境变量 `MOSS_LLM_EXTRA_HEADERS='{"x-foo":"bar"}'` 补（不进工作流、不进存档）；本地端点（含 `127.0.0.0/8`、`*.localhost`、`host.docker.internal`）显式绕开系统代理。面板的「↻」用当前端点 + Key 拉清单：从端点那层逐级上溯试 `/models` 与 `/v1/models`（并发、命中即停），所以清单挂在上一层也能找到；拉取中按钮转圈并在说明行计时，拉完自动展开下拉；换 `api_base` 会立刻清掉上一批模型名。`api_model` 是 combo，清单只来自实际拉取（进程重启即空），旧工作流里的自定义值由 `VALIDATE_INPUTS`（V1 出口）/ `validate_inputs`（V3 出口）放行、并自动补进选项 |
| 提示词包 | 16 个自写文件（MIT），方法与出处逐条记在 manifest。9 档风格只是注入的 `style_lock`，不新增文件 |

### 已实测的 `api_base`（2026-11-05，无 Key 探测状态码）

不是推荐列表，只是「这样填能连上」的证据。401 = 路径与线格式都对、只差凭据；403 若是地区/风控，
报错里会明说「不是 Key 错」。

| 服务商 | `api_base` | 探测结果 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | 401 |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 401 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | 401 |
| Kimi | `https://api.moonshot.cn/v1` | 401 |
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 401 |
| 腾讯混元 | `https://api.hunyuan.cloud.tencent.com/v1` | 401 |
| 百度千帆 | `https://qianfan.baidubce.com/v2` | 403（要 IAM Bearer token） |
| MiniMax | `https://api.minimax.chat/v1` | 401 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | 401 |
| 阶跃星辰 | `https://api.stepfun.com/v1` | 401 |
| 魔搭 ModelScope | `https://api-inference.modelscope.cn/v1` | **200（清单公开）** |
| OpenRouter | `https://openrouter.ai/api/v1` | **200（清单公开）** |
| OpenDesign CoRouter | `https://amr-link.open-design.ai/v1` | 401（标准 OpenAI 网关） |
| OpenCode | `https://opencode.ai/inference/go/openai/v1` | 401；自动带 `x-opencode-session` |
| Ollama 云 | `https://ollama.com/v1` | **200（清单公开）** |
| Ollama 本地 | `http://127.0.0.1:11434/v1` | 需本机 `ollama serve`；Key 留空 |
| LM Studio | `http://127.0.0.1:1234/v1` | 需在 LM Studio 里开 Server；Key 留空 |

⚠️ **`api_base` 要写到 `/v1` 这一层**：Ollama / LM Studio 若只填 `http://127.0.0.1:11434`，
请求会落到 `/chat/completions` 而不是 `/v1/chat/completions`（404）。
Ollama 的原生 `/api/chat` 与云端的 `/api/tags` 不在这条线格式内，本节点不走它们。

## 三、透明与不静默（本仓一贯口径）

- **report 回显** pack 版本 + 每个提示词文件的 sha256 前 8 位 + tokens + 耗时 + 是否降级。
- 改提示词一个字 → `load_pack` 直接判红并给出重新生成 manifest 的命令（不静默用改过的词）。
- 机检**只报数不裁决**：`keep` 哪几项没逐字出现、`[PANEL]` 段数 vs `panels`、
  `confidence.risks` / `omitted_low_confidence` / `uncertainties` / `open_choices` 的条数与内容。
  「好不好看 / 像不像」不交给自动判官。
- 主判据链的失败面全部 raise 带操作指引的中文错误：反推没接图、扩写没填字、mmproj 找不到、
  llama-server 起不来、panels 越界。`assemble` 首段直接返回、PE 分支跳过 `_checks`，这两条路径
  **不走主判据链**；PE 模式下被绕过的参数写进 `report`，接了参考图却没有视觉投影时也会出声，
  不静默忽略。
- 两处**出声的**降级，不假装成功：MTP 起不来 → 回退普通模式；服务端 400 拒
  `response_format` / `chat_template_kwargs` → 逐级摘掉重试。

## 四、实测坑（写死在这里，别再踩）

🔴 **思考模型必须关思考**。MOSS TE（qwen35）默认开着思考：内容全进 `reasoning_content`，
`content` 为空、`finish_reason=length` ⇒ 提示词直接空掉。三种传法逐个实测：

| 传法 | 结果 |
|---|---|
| `chat_template_kwargs: {"enable_thinking": false}` | ✅ 正文正常，1.4s / 41 tokens / finish=stop |
| `chat_template_kwargs: {"thinking": false}` | ❌ 仍空，思考 300 tokens 吃满 |
| 顶层 `enable_thinking: false` | ❌ 同上 |

所以 `thinking` 旋钮默认「关」，两个通道都发 `chat_template_kwargs`（被 400 拒则摘掉并重试 + 出声）。
**不把思考内容当答案偷偷端上桌**——正文为空就报错，让用户看见。

🔴 **不能按「端口开着」就复用侧车**。宿主重启后，先前遗留的孤儿侧车还占着 8091，
本节点当时按「外部占用 → 直接复用」走了，16s 里其实**没有载模型**，而且
`release_after_run=true` 名不副实（我们不杀不属于自己的进程，这是对的行为，
但结果是你以为显存还回去了、其实没有）。修法：**侧车的 alias 带实例指纹**
（模型名 + mmproj 名 + ctx 的 sha1 前 10 位），复用前问 `/models` 比指纹——
相符才复用，不符**硬报错**要用户释放或换端口。绝不拿别人的模型当自己的用。
实测：修完再跑同一臂，16.0s（这次真载了模型），跑完 `nvidia-smi` 从 10752 → 3972 MiB、
8091 无监听。

🔴 **外部 API 报 `SSLEOFError: EOF occurred in violation of protocol` 不是 Key 错，是链路被掐**。
签名是 TLS 握手成功、发请求时对端直接关连接 —— 代理或防火墙拦断某域名、或该域名本就不可达，
都是这个样。本节点用 `urllib` 发请求，它**只认自己进程的环境变量**（`HTTP(S)_PROXY` / `NO_PROXY`）：
宿主在哪个 shell 里启动就只拿到那个 shell 的代理，起完再设无效，必须重启宿主。
排查三步：① 报错已带主机名、请求体大小、当前进程的代理变量，先看这三项；
② 在**启动宿主那个 shell** 里跑 `curl -sS -o /dev/null -w '%{http_code}' https://<host>`，
200/401/404 都算链路通 —— 能通而节点不通，就是两个进程的网络环境不同；
③ 需要代理才到的域名，把 `HTTPS_PROXY` 设在启动宿主的环境里，或把它加进 `NO_PROXY` 走直连。
**本机与本网络的端点一律直连**，代码里显式绕开代理 —— 否则开了全局代理时连 Ollama 都会被塞进
隧道（实测 502，报错还看不出是被代理吞了）。判定范围：回环（`127.0.0.0/8`、`*.localhost`）、
私有网段（`10/8`、`172.16/12`、`192.168/16`）、链路本地（`169.254/16`）、
`.local` 域名，以及 `100.64/10`（docker / Tailscale 等组网）。
**公网端点仍走系统代理**（那是你刻意配的出口）；内网域名（如 `ollama.corp`）交给系统代理 ——
本地解析不出它指向哪，猜错更难查。

## 五、P0 取证（2026-10-30，本机 RTX 5070 12GB / b11998 / 宿主队列空）

脚本在维护者本地实验目录（不随包分发）：`p0_mmproj_verify.py`、`p0_qwen_and_storyboard.py`、
`p0_api_channel.py`、`p0_raw_probe.py`、`p0_thinking_probe.py`。

| 臂 | 模型 | 结果 |
|---|---|---|
| 反推·主臂 | `moss-te-heretic-dhq-K-6.6G.gguf` + `mradermacher/Qwen3.5-9B-GGUF` 的 `mmproj-Q8_0`（624 MB，apache-2.0） | ✅ **mmproj 可行**：prompt_tokens 16 → 1047（图真进了）；13.4s；165 词；三处中文「写不下去 / 他还在楼下 / 她还没睡」**逐字保真**；`confidence.overall=0.95` |
| MTP | 同上 | ✅ 元数据判据生效：`qwen35.nextn_predict_layers=1` 而**文件名不含 `-mtp`**，只看文件名的判据会漏；日志 `MTP=on(d2)`，早场对照 125.8 vs 76.3 tok/s（≈1.65×） |
| 反推·回退臂 | `Qwen3-VL-4B-Instruct-Q4_K_M` + 同目录 `mmproj-F16`（自动匹配） | ✅ 17.0s；133 词；中文逐字；`MTP=off`（无 nextn 元数据，判据双向正确） |
| 扩写·宫格臂 | MOSS TE，纯文本（无 mmproj） | ✅ 13.1s；`[STYLE]` 全局段声明「一张画布分为 2×2 网格，恰好 4 个面板」+ 4 段各以景别开头；`keep`（路上小心 / 关东煮）逐字命中；段数=格数 → 闸不出声 |
| 外部 API 臂 | 同机侧车当 OpenAI 兼容端点打（不外联网） | ✅ 3.7s；149 词；中文逐字；Key 来源在 report 里写明 |

**没测的**（不当已验证用）：金标集盲测、`confidence` 与真实错误率的校准、洗图三臂对照
（text-only vs `init=source` vs FaceSwap）、中文单图方言。
这些是 P1，见提案 §7。

### 五之补 · 真机（活宿主排队）一臂

| 臂 | 怎么跑 | 结果 |
|---|---|---|
| 宿主端到端 | 维护者本地实验 `p0_host_e2e.py`：`/prompt` 提交 `LoadImage → MossPromptSmith → SaveText×2` | ✅ `status=success`，16.0s，产物落 `output/moss_llm_e2e/`；report 里 pack 版本 / `MTP(draft-mtp d=2) 已启用` / 模型与 mmproj 名 / tokens 2786 / `confidence.overall=0.95` / 12 个文件的 sha8 全在 |

> 这一臂证明的是**宿主侧**：V3 注册、`image` 入参接线、19 格存档的 API 形态、
> 以及节点自己拉起并释放 llama-server。链尾必须挂输出节点，否则宿主直接
> `prompt_no_outputs`（第一次就栽在这）。

## 六、能力边界（必须先说清）

- **洗图 ≠ 保脸**。洗图 = 反推 → 改词 → 同比例重绘，只有文字约束、没有像素约束 ⇒
  身份与构图会漂，这是机制不是词没写好。`docs/07` 实测：vision-only 参考图保持不住其它格
  （其它格变化约 50/255 ≈ 整张重画）。要身份一致走 FaceSwap / 编辑链。
- **几何不归它**。宫格稿不写 bbox / 坐标 / 像素 / 时间戳；排版与格位由
  `MossStoryboardLayout` / `MossLayoutEditor` / `MossPromptCompiler` 负责。
- **12GB 不能与采样共存**：默认 `release_after_run=true` 跑完就还显存；
  关掉时要连续出词更快，但**采样前必须手动释放**（再跑一次本节点或重启）。
  本包不主动调 `unload_all_models()`——那会影响队列里别人的任务。
- **自动分不作放行闸**：`confidence` 是模型自报，未与真实错误率对齐。

## 七、闸与计数

`python tests/test_llm_subpackage.py` = **229/0**（纯函数，零 GPU 零网络）：
包 sha 篡改检测、JSON 围栏与容错、`_pick_prompt` 优先级、keep 对账、段数=格数、
MTP 元数据判定（含取值为 0 / 非 GGUF / 混 tokenizer 大表）、外部 API 端点拼接与 Key 来源、
任务边界报错、旧稿中文枚举归一、mmproj 宽度配对（同名 > 同目录唯一 > 全局唯一，多候选报错），
以及 2026-10-31 合流后新增的四组：侧车启动命令（fit / KV 量化 / ubatch / parallel /
MTP 判定 / **不设服务端口令** / `--help` 探测不到的开关一律不加）、图像预处理（张量入口
最长边 1536 + JPEG 444 + 透明合成白底）、`task=assemble`（零模型、确定性、换后端换方言、
坏 IR 与未知后端的报错话术、`target` 与 registry 不许漂移）、以及
`smith.llm.LlamaServer is server.LlamaServer` 这条**防再抄一份**的断言。

真机闸：`tools/check_ui_workflow.py examples/llm-*.json` = 62/0；
`node tools/ui_llm_panel.mjs` = 16/0（参数面板契约，见 §九）；
`tools/check_frontend.py` 21/0；`tests/test_i18n.mjs` 64/0；`ruff` 全绿。
`tools/check_release_ready.py` 在提交后应全绿（未提交时唯一红项是「未跟踪文件」）。

✅ **子包节点已与核心 7 节点共用同一套 i18n 表**（`web/moss_sb_i18n.js`）：
`NODES`（后端当前显示名 `LLM Prompt`；更早的 `MOSS Prompt Smith` 收进 legacy，老工作流标题也能换）、
`LABELS`（18 个参数槽全有中文）、`LABELS_NODE`（`image`=参考图、`panels`=面板数）、
`COMBOS`（任务/写法类/具体写法/篇幅/语言/运行时/开关/生成后控制等，F7 逐组对账）、
`TIPS`（18 条 en，F5b 对账；中文唯一源仍在 `moss_llm/nodes.py`，F10 对账）。
**存档值一律 ASCII id**（`caption` / `local` / `standard` …），中文只在显示层 ——
英文用户的工作流里不会出现中文枚举；初版已写进存档的中文由 `_LEGACY_VALUES` 在运行时就地
归一，并把「哪几格被改」写进 report（不静默迁移）。
「中/EN」按钮由核心 i18n 画进面板开关条的 `langSlot`，**不额外占一条 DOM 行**；
切语言时面板自己的分组标题/摘要/说明行一起换，悬停说明优先读 `w.tooltip`（en），
中文态撤掉覆盖、回落后端原文。
⚠ 初版（10-30 早场）存过中文枚举的旧稿：面板仍能正确分组（`channel` 两个旧值都认），
但下拉会先显示值域红框；跑一次由后端 `_LEGACY_VALUES` 归一，report 写明哪几格被改，
重存后即干净。三个示例已全部按 ASCII id 重新生成。

## 八、文件地图

| 用途 | 路径 |
|---|---|
| 节点（参数表唯一真相源） | `moss_llm/nodes.py` |
| llama-server 侧车 | `moss_llm/server.py` |
| 提示词包加载 + sha + manifest | `moss_llm/pack.py` · `moss_llm/prompts/`（12 个 txt + `manifest.json`） |
| 一键装/更新运行时 | `moss_llm/runtime_install.py` |
| V3 外壳 | `moss_llm/v3_shells.py`（+ `v3/entrypoint.py` 追加注册，装载失败不拖核心） |
| 纯函数测试 | `tests/test_llm_subpackage.py` |
| 示例生成器 | `tools/make_llm_examples.py`（槽位序取 `check_ui_workflow.expected_widgets`，与门禁同源） |
| 示例 | `examples/llm-prompt-workbench.json` · `llm-idea-to-image.json` · `llm-compile-zero-model.json` |
| 参数面板（前端） | `web/moss_llm_panel.js`（状态卡 / 决策区 / chip 行 / 嵌套预设 / 高级底栏五段 + 默认 4 项 + 单组视图 + 「已改」点；只改显隐，按内容调高度，不碰 `widgets_values`） |
| 面板真机闸 | `tools/ui_llm_panel.mjs`（要活宿主；截图落 `docs/_local/`） |
| 运行时落点 | `moss_llm/runtime/`（已进 `.gitignore`，几百 MB 二进制不入库、不进发布包） |

## 九、参数面板 `web/moss_llm_panel.js`

参数面 18 项一屏放得下，所以面板只做两件事：**顶部一行只读状态** + **把 18 项按语义排好**。
折叠、分组、chip、预设按钮、设置面板都是 39 项时代的补丁，合并后一并删掉。

- **状态行**：`反推 · 本机 ｜ 写法：英文详述`，缺项时换成「还接一张图 / 还填一句想写什么 /
  还填在线模型名」。悬停只写 `title`，**绝不替换文字、不改任何元素位置**
  （2026-10-30 的坑：hover 换文案会顶动下面的参数）。
- **18 项全显示**：`task` `model` `text` `style_class` `style` `target` `style_note`
  `length` `temperature` `panels` `language` `keep` `seed` `api_base` `api_model`
  `api_key` `speed` `runtime`（另有 9 个图片接线槽）。`widgets_values` **19 格**
  （18 参数 + seed 的「生成后控制」），顺序即存档契约。
- **两级级联**：`style_class`（连贯描述 / 风格 / 自定义 / 标签串 / 分段结构）决定 `style`
  的可选子档。`syncStyle()` 只改 `style.options.values` 与当前值；**后端 combo 是全部
  子档的并集** —— 只列 3 档的话，UI 点选能跑，但 `/prompt` 与工作流 JSON 会被
  400 `value_not_in_list` 拒（2026-11-08 实测）。
- **自定义风格**：`style_note` 一句自由文本，与预设 lock 叠加后作为硬 `# STYLE LOCK` 注入。
  它是单行 STRING —— 多行会让节点凭空多出一大块空白。
- **即时重画**：`hookAllRepaint()` 给所有 widget 装统一回调，改值 120ms 内出反馈；
  1.5s `guard` 轮询只作宿主重建的兜底。
- **不碰存档**：面板状态一律挂在 `node.__mossllm`（实例级、会话内），不进 `serialize()`。

真机闸：`node tools/ui_llm_panel.mjs` = **16/0**（19 格存档 / 18 行全显 / 级联 9 档与 1 档 /
缺项提示 / 存盘重开逐位一致 / 280px 不溢出）。

### 温度三档实测（2026-11-09，每档 5 次取中位数）

同 prompt、同风格（写实摄影）、同篇幅（中），seed 0/7/13/23/31。
「面部项」= 正文里 brow / nose / cheek / jaw / lip 命中的种类数（0–5）。

| 模型 | T=0.2 | T=0.5 | T=1.0 |
|---|---|---|---|
| 4B（Qwen3-VL-4B Q4） | 3 | **4** | 3 |
| 9B（DeepSeek-V4-Pro-Qwen3.5 NVFP4） | 3 | **5** | 4 |
| 35B（Hermes3.6-35B-A3B MXFP4） | **5** | 3 | 3 |

- **唯一跨模型稳定的规律：高温 = 更多样，不是更好。** 三模型档内相似度都在 T=1.0 最低
  （0.081 / 0.090 / 0.069）。
- **「最佳温度」因模型而异，且都不是 0.2 之外的同一个值**：4B 与 9B 的峰值在 **0.5**，
  35B 在 **0.2**。默认 `temperature=0.2` 对 4B / 9B 都不是最优点，保留它只因为
  对最强模型（35B）最优、且低温更稳。
- **4B 的旋钮范围最窄**（3/4/3，最多差 1 项）：改温度只换说法；9B / 35B 差 2 项（3↔5）。
- **篇幅档守不住**：中档声明 160–280 词，实测抽到过 118 与 291，极差最宽 145 词。

⚠️ 单 prompt、单风格、每档 5 次。换风格或题材最佳温度可能移动（未测）。
采样随之联动（`_sampling_for()`）：0.2→(top_p 0.90, min_p 0.06) / 0.5→(0.95, 0.03) /
1.0→(0.98, 0.0)，避免温度与 min_p 对冲。


## 十、同一层的另一件事：`moss_llm.smith`（IR 编译层）

本子包解决「要一次词」，`moss_llm.smith` 解决「同一份内容换后端时事实还在不在」。

**2026-10-31 合流后的依赖方向是单向的**：`smith` 可以 import 父包的运行时
（侧车 `server.py` / 图像预处理 `vision.py` / 装包器 `runtime_install.py`），
父包**只在 `task=assemble` 时懒加载** `smith.api`。所以删掉 `moss_llm/smith/`
不影响另外三个任务、也不影响核心 7 节点（`moss_llm.smith_available()` 用
`find_spec` 探测，平时不付加载成本）。哪一半归谁、为什么这么切，见
历史记录（git 历史）。

| | `MossPromptSmith` 节点 | `moss_llm.smith` |
|---|---|---|
| 形态 | 一次 LLM 调用 → 成品提示词 + report | 结构化 IR ⇄ N 个方言，确定性编译 |
| 用模型吗 | 必须（`assemble` 任务除外） | 可选（只有反推/扩写用，拼装不用）|
| 换写法 | 换 `style` 一档，重出一次词 | 同一份 IR 直接编译，事实集合不变 |
| 自检 | `_checks` 报数不裁决 | 6 条不变式 + 往返校验 + 覆盖率 |
| H3 | 不管 | 时间线进 IR 一等维度 |

**节点里的 `task=assemble` 就是这一层的入口**：把 IR 贴进「文字输入」，
`target` 选后端，零模型、毫秒级、可复现，
report 里带方言机检与 M1–M6 结论。没有权重也能用——这是合流带来的最大可用性变化。

**反推与扩写走同一个写法档，不新增方言**：`task=caption`（看图）或 `task=expand`（纯文字）+ `target=minimax-h3` 时，
system 换成 H3 音视频契约（三字段时间线）；在 `style_class=分段结构` 里选
「时间线六段式」＝ 内部再置 `profile=six-section`。理由是可理解性：用户面对的是
「写哪一种」，不是两张后端表；`target` 仍表示「输出到哪个后端」。
report 会写明本次用了哪套骨架，不静默改变输出。

`profile` 是 `smith` 的编译参数（节点上不直接露，由 `style` 决定），它是 STRING 而不是下拉，因为档位名的唯一事实源是
`smith/data/model-registry.yaml` 的 `dialects`（`target` 已有「与 registry 不许漂移」的对账闸，
这里再抄一份必然漂移）。目前只有 `minimax-h3` 有 `six-section`：官方全参考六段式，
字段固定为 `subject_definitions` / `summary` / `retention_analysis` /
`detailed_description` / `overall_soundscape` / `non_diegetic_music`，
并用 `<Subject N>` / `<Video N>` / `<Audio N>` / `<Picture N>` 参考标签锚定主体。
留空=三字段基础模式；填了后端不认的档会报错并列可选项（不静默降级），
report 会写明本次用了哪个档。
同一份 `05-h3-t2va.json` 实测：默认 272 token / 3 字段，`six-section` 418 token / 6 字段，
两者都是 32 ms 级、0 issues、6/6 不变式通过。

边界：`smith` 的几何/布局归核心编辑器（`core/prompt.py` 的 bbox 系列），它只写文字描述；
反过来，节点的 `_checks` 也不裁决「好不好看」。两边都不越界。

- 用法：`moss_llm/smith/README.md`
- 设计：历史记录见 git 历史
- 路线图与卡点：历史记录（git 历史）
- 合流决策（谁强、留谁、代价）：历史记录（git 历史）
- 闸：`python -m unittest tests.test_smith_ir`（365）、`python tools/check_coverage.py --check`（4 份夹具 × 7 后端：固定项不丢、数字与引号不编、覆盖率不低于基线）、`python tools/freeze_smith_set.py --check`（确定性 sha256）

## 十一、一次大概多少钱（量级，2026-11-05）

> 准确数字永远看 `report` 里的 `total_tokens`——每次执行都写，比任何估算都准。

| 场景 | 输出上限 | 大致耗时 | 大致费用（量级） |
|---|---|---|---|
| 反推 1 张图 | 1024 | 本地 25–60s / API 8–20s | 便宜档 ≈ 0.01 元；贵档（GPT-4o 级）≈ 0.1 元 |
| 扩写 | 1024 | 本地 15–40s / API 5–15s | 同上 |
| 洗图 | 1024 | 同反推（0.5.2 起单遍） | 同反推 |
| `thinking=on` | 1024 | +30–120s | 输出 token 可能翻倍 |

四条要点：

1. **准确值看 `report` 的 `total_tokens`**，不要拿本表当账单。
2. **单次不贵，批量才要算**：100 张图 × 贵档 ≈ 10 元级。
3. 篇幅是 `length` 三档：短 768 / 中 1024 / 长 2048 tokens（`_LENGTH_TOKENS`）；
   API 按输出计费，多数场景「中」就够。
4. **截断只在两种情况下发生**：自己选了更短的档，或输出畸形 JSON。
   「中」对提示词（200–500 token）绰绰有余；本地 ctx 8192 也装得下（输入约 2000 + 输出 1024）。
   ⇒ **不做按 ctx 的自动收口**：默认不撞，且「1 字符 ≈ 1 token」对英文高估约 5 倍，会误收窄用户设定。
## 十二、多模型工作流为什么每轮都慢（不是并发）

**先说不是并发**：ComfyUI 只有 **1 个 `prompt_worker` 线程**，队列里塞两个 prompt 也是串行执行；
`acquire()` 的 `_lock` 只保护「起停服务」这一小段，不涉及生成并发。

**真正的原因（代码事实）**：`release_after_run` **默认 `True`**（`nodes.py:362`），
执行结束时无条件 `stop_server()`（`nodes.py:556`/`:575`）。于是「A(模型 M1) → B(模型 M2)」的工作流，
**每跑一轮都要重新加载两个模型**，第二轮同样重来——用户感知就是「同样的工作流，第二次跑还是这么慢」。

| 观察到 | 结论 |
|---|---|
| 每轮日志出现 2 次 `启动 llama-server pid=` | 符合默认行为，属预期 |
| 想复用 | 把「跑完释放显存 / release_after_run」关掉；**采样前必须再跑一次本节点或重启来释放**，否则常驻显存会挤占采样 |

**不做什么**：不为「快」改默认值（常驻显存会让采样阶段 OOM 风险大增）；不加请求队列、不改 `--parallel`
（单 worker 下无意义，只会翻倍占显存）。

> ⚠️ **真机未跑**：本节结论由代码路径确定，未做两次不同 GGUF 的实跑计数。
> 复跑步骤：放两个 `MossPromptSmith`，都 `channel=local`、`task=expand`，模型分别选两个不同 GGUF，
> 各跑一次看日志里 `启动 llama-server pid=` 出现几次；再跑一轮看是否又是两次。
## 十三、连接方式与运行时（0.5.2）

**通道不是一个旋钮**：`channel` 由 `api_base` 是否为空推断 —— 填了走在线端点，留空走本机侧车；
本机模式下 `api_model` 会被清空，report 写明。39 项时代的「本地引擎 / 外部 API」chip
随面板重做一并下线（见 §九），存档里残留的两个中文旧值仍在 `_LEGACY_VALUES` 里归一。

**运行时一个档位定来源**（`runtime`）：

| 档位 | 找哪里 | 找不到时 |
|---|---|---|
| `auto`（默认） | `MOSS_LLM_SERVER` → `LLAMA_CPP_HOME` → 本包 `runtime/`（含 `MOSS_LLM_RUNTIME_DIR`）→ `PATH` | 报错，给三条可执行的下一步 |
| `runtime` | 只认本包 `runtime/` | 同上 |
| `path` | 只认 `PATH` 里的 `llama-server` | 同上 |
| `自动下载` | 先按 `auto` 找，**找不到才**联网装；装前报落点与体积 | 装完仍找不到 ⇒ 报错 |

- 旧存档的 `current` / `update` / `用现有` / `更新到最新版` 一律归一为 `auto`，
  所以**打开旧工作流不会触发下载**。
- `custom` 还留在枚举里（旧存档兼容），但它指向的「服务器程序」输入格已随 18 项合并下线；
  选到它只会得到「找不到可执行文件」，按报错里的 ①② 改档即可。

> 判据：`find_server_exe(path, "auto") != path`（auto 不认显式路径）；
> 同目录同时有 `llama-server.exe` 与 `-impl.dll` 时只选 exe；
> `test_llm_subpackage` 229 条覆盖。
