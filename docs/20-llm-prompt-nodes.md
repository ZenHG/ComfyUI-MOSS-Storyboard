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

配置（模型 / mmproj / ctx / 显存 / MTP / 端口 / 释放）进了同一个 `INPUT_TYPES()`，
不再拆一个配置节点——多一个节点就多一层「配置在哪、词从哪来」的追踪成本。
`MossPromptCheck` 没做成节点，它的判据以纯函数形式内建在每次跑完后（`_checks`），
结果写进 report；闸本身是 `tests/test_llm_subpackage.py` 的对象。

任务旋钮 `task`：**反推 / 扩写 / 编辑指令**。方言旋钮 `dialect`：
`moss_image`（单图英文散文，官方默认）· `moss_storyboard`（宫格中文 `[STYLE]+[PANEL n]`，
本仓 `docs/15` 验证过的格式）· `generic_natural` · `generic_tags` · `cinematic` · `cn_natural`。

## 二、通用性（开源前提，提案的修订）

| 点 | 落地做法 |
|---|---|
| 模型 | 扫 `models/LLM` + `models/text_encoders` 的全部 GGUF，按相对路径显示。**不认架构**——llama.cpp 能载的就能跑，Qwen 全系列 / MOSS TE / 别的 arch 一视同仁 |
| mmproj | `自动匹配` 按**宽度硬配对**：模型 `<arch>.embedding_length` 必须等于塔 `clip.vision.projection_dim`。优先级 = 同名权重 > 同目录唯一 > 全局唯一；**多个候选都能配上就报错让你选，不猜**。显式选塔也走同一道宽度校验，不符直接 raise。也接受直接填绝对路径 |
| MTP | 读 GGUF 元数据 `*.nextn_predict_layers` 的**取值**（不看文件名）。默认 `draft-mtp d=2`；**DFlash 不用**（对 MOSS target 接受率实测 10–31%） |
| 运行时 | 找的顺序：`server_exe` 参数 > 环境变量 `MOSS_LLM_SERVER` > `moss_llm/runtime/` > PATH。**不扫别的节点包的 runtime**（那是把别人的目录结构写成本包的隐性依赖） |
| 一键更新 | `runtime` 选「更新到最新版」→ 从官方 release 的 `b<build>` 里挑本机可用的包：先探后端（N 卡→CUDA 且不超驱动版本；AMD Windows→Vulkan、Linux→ROCm；Intel→Vulkan；Apple→Metal；认不出→CPU），再下主包 + 同版本 cudart；`.zip` / `.tar.gz` 都支持，带断点续传与慢速熔断。**直连失败会自动走内置镜像链**（`gh-proxy.com` → `ghproxy.net`），每个包都先用 GitHub API 的 `sha256` 校验、对不上就丢弃换源——镜像只影响下载速度，不影响可信度；可用 `MOSS_LLM_MIRRORS=前缀1,前缀2` 替换镜像链，`MOSS_LLM_MIRROR` 指定单个镜像。覆盖用 `MOSS_LLM_BACKEND` / `MOSS_LLM_RUNTIME_URL`。CLI 同义：`python -m moss_llm.runtime_install` |
| 外部 API | `channel=外部 API`：任何 OpenAI 兼容端点（OpenAI / 火山方舟 / 智谱 / Kimi / MiniMax / DeepSeek / Ollama / LM Studio…）。不起侧车、不占显存。Key 两处来源：直接填 `api_key`（会随 JSON 分享出去，report 会警告），或放在宿主进程环境变量 `MOSS_LLM_API_KEY` 里（界面不提供变量名那一栏，免得让用户背目录/环境知识；旧工作流里存过的自定义变量名仍然生效）。`api_base` 决定线格式（四种，都不用选旋钮）：`/anthropic` → `/v1/messages` + `x-api-key`；`generativelanguage.googleapis.com` 或 `/v1beta` → `models/<model>:generateContent` + `x-goog-api-key`；`/responses` → OpenAI Responses 线格式（`instructions` + `input` + `max_output_tokens`）；其余 → `/chat/completions`。Azure 主机（`*.openai.azure.com`）自动改用 `api-key` 头，带 `?api-version=` 的端点也不会被拼坏。出站带 `User-Agent` 并声明 `Accept-Encoding: identity` —— urllib 默认 UA 会被 Cloudflare 一类网关拦成 403/1010；opencode 的 Go 网关另外要求 `x-opencode-session`（缺了直接 400 `MissingSessionID`），已按主机自动带上稳定会话 id；其它网关要的自定义头用环境变量 `MOSS_LLM_EXTRA_HEADERS='{"x-foo":"bar"}'` 补（不进工作流、不进存档）；本地端点（含 `127.0.0.0/8`、`*.localhost`、`host.docker.internal`）显式绕开系统代理。面板的「↻」用当前端点 + Key 拉清单：从端点那层逐级上溯试 `/models` 与 `/v1/models`（并发、命中即停），所以清单挂在上一层也能找到；拉取中按钮转圈并在说明行计时，拉完自动展开下拉；换 `api_base` 会立刻清掉上一批模型名。`api_model` 是 combo，清单只来自实际拉取（进程重启即空），旧工作流里的自定义值由 `VALIDATE_INPUTS`（V1 出口）/ `validate_inputs`（V3 出口）放行、并自动补进选项 |
| 提示词包 | 12 个自写文件（MIT），方法与出处逐条记在 manifest。MOSS 两个方言只是其中两项 |

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
- 机检**只报数不裁决**：`must_keep` 哪几项没逐字出现、`[PANEL]` 段数 vs `panels`、
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
| 扩写·宫格臂 | MOSS TE，纯文本（无 mmproj） | ✅ 13.1s；`[STYLE]` 全局段声明「一张画布分为 2×2 网格，恰好 4 个面板」+ 4 段各以景别开头；`must_keep`（路上小心 / 关东煮）逐字命中；段数=格数 → 闸不出声 |
| 外部 API 臂 | 同机侧车当 OpenAI 兼容端点打（不外联网） | ✅ 3.7s；149 词；中文逐字；Key 来源在 report 里写明 |

**没测的**（不当已验证用）：金标集盲测、`confidence` 与真实错误率的校准、洗图三臂对照
（text-only vs `init=source` vs FaceSwap）、中文单图方言、`two_pass` 的实测质量差。
这些是 P1，见提案 §7。

### 五之补 · 真机（活宿主排队）一臂

| 臂 | 怎么跑 | 结果 |
|---|---|---|
| 宿主端到端 | 维护者本地实验 `p0_host_e2e.py`：`/prompt` 提交 `LoadImage → MossPromptSmith → SaveText×2` | ✅ `status=success`，16.0s，产物落 `output/moss_llm_e2e/`；report 里 pack 版本 / `MTP(draft-mtp d=2) 已启用` / 模型与 mmproj 名 / tokens 2786 / `confidence.overall=0.95` / 12 个文件的 sha8 全在 |

> 这一臂证明的是**宿主侧**：V3 注册、`image` 入参接线、38 个 widget 的 API 形态、
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

`python tests/test_llm_subpackage.py` = **148/0**（纯函数，零 GPU 零网络）：
包 sha 篡改检测、JSON 围栏与容错、`_pick_prompt` 优先级、must_keep 对账、段数=格数、
MTP 元数据判定（含取值为 0 / 非 GGUF / 混 tokenizer 大表）、外部 API 端点拼接与 Key 来源、
任务边界报错、旧稿中文枚举归一、mmproj 宽度配对（同名 > 同目录唯一 > 全局唯一，多候选报错），
以及 2026-10-31 合流后新增的四组：侧车启动命令（fit / KV 量化 / ubatch / parallel /
MTP 判定 / **不设服务端口令** / `--help` 探测不到的开关一律不加）、图像预处理（张量入口
最长边 1536 + JPEG 444 + 透明合成白底）、`task=assemble`（零模型、确定性、换后端换方言、
坏 IR 与未知后端的报错话术、`target` 与 registry 不许漂移）、以及
`smith.llm.LlamaServer is server.LlamaServer` 这条**防再抄一份**的断言。

真机闸：`tools/check_ui_workflow.py examples/llm-*.json` = 46/0；
`node tools/ui_llm_panel.mjs` = 60/0（参数面板，见 §九）；
`tools/check_frontend.py` 21/0；`tests/test_i18n.mjs` 64/0；`ruff` 全绿。
`tools/check_release_ready.py` 在提交后应全绿（未提交时唯一红项是「未跟踪文件」）。

✅ **子包节点已与核心 7 节点共用同一套 i18n 表**（`web/moss_sb_i18n.js`）：
`NODES`（后端当前显示名 `LLM Prompt`；更早的 `MOSS Prompt Smith` 收进 legacy，老工作流标题也能换）、
`LABELS`（38 个参数全有中文）、`LABELS_NODE`（`image`=参考图、`panels`=面板数）、
`COMBOS`（任务/通道/方言/拼装目标/篇幅/语言/思考/运行时/mmproj/KV 精度/开关，共 17 组）、
`TIPS`（38 条 en，F5b 对账；中文唯一源仍在 `moss_llm/nodes.py`，F10 对账）。
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
| 参数面板（前端） | `web/moss_llm_panel.js`（开关条置顶 + 默认只露常驻 5 项 + 单组视图 + 「已改」点；只改显隐，按内容调高度，不碰 `widgets_values`） |
| 面板真机闸 | `tools/ui_llm_panel.mjs`（要活宿主；截图落 `docs/_local/`） |
| 运行时落点 | `moss_llm/runtime/`（已进 `.gitignore`，几百 MB 二进制不入库、不进发布包） |

## 九、参数面板 `web/moss_llm_panel.js`

38 个参数平铺是一堵墙。面板只做三件事：**默认只露常驻**、**一次只看一组**、**把改动标出来**。

- **默认 5 项**（`task` `channel` `dialect` `model` `text`）。这个节点的日常动作是
  「贴图/贴文本 → 跑」，其余参数都是配置不是操作。默认状态节点 `size` 高 **353 画布单位**；
  80% 缩放下盒子 **306 px**（100% 缩放下 383 px；上一版默认 17 行 / 978 px）。
- **开关条钉在节点顶部**（`node.widgets[0]`），展开的组都在它**下方**；宿主重建 widget 数组后
  位置会退回末尾，`apply()` 每次自愈。说明行高度恒定 `2.7em` —— 悬停换 tooltip 时行数变化
  不会顶动下面的参数（2026-10-30 实测：旧版鼠标定位失败的直接原因）。
- **单组视图**：点「常用调优」只看调优、点「本地引擎」只看引擎，不叠。参数顺序是存档契约
  （`widgets_values` 按位置存），改不了；`mmproj`/`runtime` 又夹在别的组中间 ⇒ 多组同时展开时
  标题只能来回重画（上一版全展开里「本地引擎」出现 3 次）。单组视图让同组行在视觉上连成一片。
- **「已改」小点**：折起来的组里只要有一项与 nodeDef 的 `default` 不同，chip 上亮一个蓝点，
  悬停看条数（`seed` 与它的「生成后控制」不计 —— 每次跑都会变）。
- **说明行 + 语言按钮**：悬停任一参数时显示 tooltip（英文态读核心 i18n 写进 `w.tooltip` 的
  en 提示，中文态回落后端 `INPUT_TYPES` 原文）；同一行右端是核心 i18n 画的「中/EN」按钮
  （`langSlot`），切语言时面板的分组标题/摘要/说明行与节点标签/枚举一起换。
  指针离开就回到「N 个参数 · M 个可见」。
- 视觉只用宿主 CSS 变量（明/暗主题一起对）；置顶 `order` 与临时网格样式需 `!important`（宿主特异性/内联优先）；面板自己的标签按宿主语言 zh/en 二选一。

| 组 | 内容 | 默认 |
|---|---|---|
| 常驻 (`core`) | `task` `channel` `dialect` `model` `text`（+ `image` 口与两条输出） | 常显，不给折叠 |
| 常用调优 (`tune`) | `length` `temperature` `max_tokens` `seed`(+控制项) `language` `thinking` `panels` `style_lock` `must_keep` `wash` `two_pass` | 收起，一次点开 |
| 本地引擎 · llama.cpp (`local`) | `mmproj` `runtime` `ctx_size` `gpu_layers` `cache_type_k/v` `flash_attn` `image_max_tokens` `ubatch` `parallel` `fit_on` `fit_target_mib` `mtp` `mtp_draft_n` `server_exe` `port` `release_after_run` | 收起；`channel` 不是「本地 GGUF」时 chip 标「通道外」 |
| 外部 API (`api`) | `api_base` `api_model` `api_key` | 收起；切到外部通道时自动展开 |

> **Key 不落盘**：`api_key` 在保存工作流时被清成空串（通用存盘钩子按 `__mosssbOmitOnSave` 声明的字段名清，不认业务字段）。
> 节点内存里的值本次照常可用；**重开工作流需要重填**——这是有意的，不是 bug。想完全免填就设宿主进程的 `MOSS_LLM_API_KEY`。

四条实测依据（前端 1.57.6 / 本机宿主 **Vue 节点模式**，都写进代码头注）：

1. 折叠只走 `widget.options.hidden = true` + `syncVisibilityFromOptions()`；存档按
   `serialize !== false` 收值 ⇒ **隐藏不改变 `widgets_values` 的格数与位置**。折叠/聚焦状态
   只活在节点实例的 `__mossllm` 上，不序列化 ⇒ 存盘/重开恒为默认。
2. 宿主**不会**随隐藏行自动收缩，DOM 组件那一格还被 `*:flex-1` 拉满（实测开关条被撑到
   434 px、字被摊成一列）⇒ 面板自己做两件事：开关条 `flex:0 0 auto`（**内联**，那条
   `*:flex-1` 在级联里压过普通类规则），以及按内容高度 `node.setSize`。量高度时临时把网格行
   压成 `min-content`（宿主会把多余高度平摊进每一行，直接量到的是"摊平后"的值，拿它当内容
   高度会越调越高），量完立刻还原。**只调高度、不调宽度**；`size` 会随视图变（用户看得见），
   `widgets_values` 一个字节不动。
3. 组标题不往 `node.widgets` 中间插行（那是重排 = 改存档顺序），改由 CSS 画在每组第一行上方
   （`.mossllm-head::before`，行元素用宿主自己打的 `data-widget-name` 定位）。
4. 宿主给每行都建了真 DOM（`[data-widgets-grid-node-id]` / `[data-widget-name]`），CSS 才定位得到；
   **画布模式没有这些行** ⇒ 一律不猜：标题不画（`console.warn` 一次）、高度不调，显隐照常。

不静默兜底：拿不到显隐 API 或挂不上那一行 ⇒ **什么都不藏**并 `console.warn`；表里没写的新参数
一律归「其它」常显；`channel` 取值认不出来 ⇒ 停用通道标记（宁可多显示，不可藏错）。

**通道只有一个事实（`channel`），但不需要人肉同步**（2026-11-05 实测事故：填好端点与模型、
通道还停在本地，运行时报「找不到 llama-server」）：

- 填 `api_base` / 手填 `api_model` / 点 `↻` 拉到清单 ⇒ 面板把 `channel` **拨到「外部 API」**并说一句；
  打开「端点已填、通道还写本地」的旧存档 ⇒ 对齐一次（`adoptApiIntent`，每节点只做一次）。
- 之后手动切回「本地 GGUF」= 显式选择，**不再抢**；此时开关条上常驻一条琥珀色状态块
  「⚠ 端点已填，但通道=本地 —— 本次走本地模型」，点它一键改道（端点与模型名原样保留）。
- 运行期不猜：仍然只认 `channel`；本地分支若发现 `api_base` 已填，在 report 里点名，
  且本机没有 llama-server 时把「你想用的应该是 API」放在报错第一句。

**首屏说人话**（不新增控件、不动存档，2026-11-05）：

- 说明行开头是**模式摘要**：`读图说话 · 本地 GGUF` / `扩写提示词 · 外部 API` —— 回答「这个节点现在
  在干什么」。完整说明（要不要视觉塔、要不要图）挂在悬停 `title` 上，不占视觉宽度。
- 状态块第二个用途：**执行前就指出还缺什么** ——「⚠ 本机没有 GGUF 模型」/「⚠ 还没填 api_base」/
  「⚠ 还没选模型名」。点它只把对应分组展开，**不替用户改值**（修复动作是填一个值，不是按一个按钮）。
  **Key 留空不算缺**：Ollama / LM Studio / vLLM 本来就不要 Key；`assemble` 任务不开模型，也不报缺。
- 两者与「通道矛盾」共用同一个琥珀色块，且天然互斥：通道矛盾要求 `api_base` 非空，缺端点要求它为空。

已知代价（不装成优点）：点「全部展开」时，参数顺序决定标题仍会出现 3 次「本地引擎」（组被切断），
这是存档契约换来的；单组视图下不会出现。

另一条已知代价：展开「常用调优」时，开关条会因三个一次性预设按钮（草稿 / 精修 / 恢复默认）高出 **21px**，
收起即恢复。这是有意的——预设不常驻占位，但一展开就在手边。

真机闸：`node tools/ui_llm_panel.mjs` = **73/0**（只读界面、不排队）。断言含：默认 5 行 /
开关条在参数上方且是 `widgets[0]`（**重开后也自愈回首位**）/ 悬停换说明时开关条高度与参数 top 都不动（不抖）/
节点按内容变矮 / 单组切换 / 「已改」计数 / 切通道自动聚焦 / 全展开 38 个 `isWidgetVisible` /
**存盘→重开 `widgets_values` 逐位一致** / 折叠状态不进存档 / 两个同型节点各自独立 /
只作用于本节点 / 页面无 JS 异常 / **按 `task` 动态常驻**（assemble 提 `target`、给 3 个 inert
控件加文字标注）/ api 工作流冷载入自动聚焦 / 切组焦点保护 / **拉取模型有可见进度、拉完自己展开下拉、换端点不留旧模型名** /
**填端点即切通道、通道=本地时状态块显眼且可一键改道、旧存档打开自动对齐** /
**模式摘要随 task·channel 变、执行前指出缺项且点开对应组、Key 空不算缺**。
「已连线的 widget 槽」已用 PrimitiveNode 真拖线覆盖（4 条断言，无 skip），`isLinked` 不再是「防一手」。

## 八、同一层的另一件事：`moss_llm.smith`（IR 编译层）

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
| 换方言 | 换 `dialect` 旋钮，重出一次词 | 同一份 IR 直接编译，事实集合不变 |
| 自检 | `_checks` 报数不裁决 | 6 条不变式 + 往返校验 + 覆盖率 |
| H3 | 不管 | 时间线进 IR 一等维度 |

**节点里的 `task=assemble` 就是这一层的入口**：把 IR 贴进「文字输入」，
`target` 选后端，零模型、毫秒级、可复现，report 里带方言机检与 M1–M6 结论。
没有权重也能用——这是合流带来的最大可用性变化。

边界：`smith` 的几何/布局归核心编辑器（`core/prompt.py` 的 bbox 系列），它只写文字描述；
反过来，节点的 `_checks` 也不裁决「好不好看」。两边都不越界。

- 用法：`moss_llm/smith/README.md`
- 设计：历史记录见 git 历史
- 路线图与卡点：历史记录（git 历史）
- 合流决策（谁强、留谁、代价）：历史记录（git 历史）
- 闸：`python -m unittest tests.test_smith_ir`（365）、`python tools/check_coverage.py --check`（4 份夹具 × 7 后端：固定项不丢、数字与引号不编、覆盖率不低于基线）、`python tools/freeze_smith_set.py --check`（确定性 sha256）

