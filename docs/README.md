# 文档索引（27 篇）

> 只列仍在 `docs/` 的文档；历史过程文档已归档，见 git 历史。

| 文档 | 一句话 |
|---|---|
| [06 · VAE 解码显存账（进阶）](06-vae-memory.md) | 这条链路里显存峰值出现在 VAE 解码：Flux2 VAE 的 decoder 有 4 个上采样级 |
| [跨格一致性：机理、实测口径与旋钮边界](07-consistency.md) | 一致性不是"一个东西"，是三条独立机制的总和。 |
| [10 · state v3 冻结契约](10-contract.md) | 整份编辑状态是一个单行 JSON 字符串，存在节点唯一的 STRING widget editor_state 里。 |
| [12 · 互操作：elements-v1 ⇄ state v3](12-interop.md) | 编辑器的 editor_state 是 v3 契约（docs/10），它解决「同一份状态怎么确定性编译」。 |
| [官方 prompt-enhancer 草稿：接入与置信度](13-prompt-enhancer.md) | OpenMOSS 在 MOSS-Image 仓库里发布了一个给 LLM agent 用的 skill： |
| [顶层设计与节点职责：这七个节点各自对什么负责](14-top-level-design.md) | 不是“替用户想镜头”，也不是“保证每格照办”。 |
| [故事板配方契约：验证过的配方里，哪些项是必需的](15-recipe-contract.md) | 三份的全局段都含同一串网格声明，每一格的主句都以「画面为…」起句。 |
| [换脸节点：重构前 → 重构后](16-faceswap-refactor.md) | 前：换脸 = 另一个包的一个编码节点 + 手接 VAEEncode + KSampler + 手工裁格贴回， |
| [换脸配方：哪条路真的成过](17-faceswap-recipe.md) | 原理：MOSS 自己没有蒙版通道（TextEncodeMOSSImageEdit 的输入里没有 mask， |
| [蒙版潜空间编辑：MOSS 没做的那半条路](18-masked-latent-edit.md) | 三条硬证据（2026-10-29 读代码 + 工作流得到）： |
| [节点手册 · 参数全表](19-node-manual.md) | 同一个东西在不同地方叫不同名字，是这类文档最常见的误解来源。 |
| [20 · 可选 LLM 子包 `moss_llm`：一个节点，只出成品提示词](20-llm-prompt-nodes.md) | 提案里是 MossLLMConfig + MossPromptSmith（+ 可选 MossPromptCheck）三个。 |
| [提示词工匠 · 架构 v2.0](21-smith-ir-架构.md) | 不要做「一套通用提示词模板」。 |
| [扩写：目标、判据、以及为什么「写长点」是错的](24-smith-ir-扩写判据.md) | 这一条完全客观、可自动化，且不需要图像生成器。 |
| [扩写三拆：补全 / 翻译 / 改写](26-扩写三拆.md) | 改写查「事实集合不变」——三条判据全是纯 Python，零模型、零网络。 |
| [H3 六段式方言（h3-six-section）](27-H3六段式.md) | python tests/test_h3_six.py |
| [跨家族 registry 验证（T7）：只加一份 YAML 接入新图像家族](29-跨家族registry验证.md) | python tests/test_registry_family.py |
| [32 · 面板闸 SKIP 转真机断言（接了线的参数不会被藏）](32-面板闸SKIP转真机.md) | c.skip("接了线的参数不会被藏", |
| [H3 Rewriter LoRA 基座决策备忘（T12 前置）](35-H3-LoRA基座决策备忘.md) | 不批准也不影响现有能力：h3 rewrite --base-url <OpenAI 兼容端点> 现在就能跑，只是没有 LoRA。 |
| [43 · 双盲判读与评分校准（G7 的 L4 层）](43-双盲判读与评分校准.md) | 先证明判读本身可靠，再拿判读比模型。 |
| [CI 第二矩与兼容矩阵（M2）](44-CI第二矩与兼容矩阵.md) | 这是 ComfyUI 自定义节点包：用户通过 Manager/Registry 安装、拖示例工作流、看节点 report。 |
| [用户文档与双语同步（M3）](45-用户文档与双语同步.md) | 结构（标题层级/编号/顺序）交给 tools/check_docs_sync.py 机检（发布闸 S19）； |
| [M3 收尾 · ComfyUI 用户路径真机验证](48-用户路径真机验证.md) | 一句话：用户路径的零权重第一步真的能跑通（有 prompt/report 文本为证）；发现的 3 个问题都在 |
| [双盲判读 · 第一轮（G7 第二轮 20 图 × 2 模型）](53-双盲判读-第一轮.md) | 一句话：判读本身的一致性不够（κ 远低于 0.6），所以这一轮只能作为「协议校准」——不能拿去做模型优劣结论； |
| [双盲判读协议 v2：不可判硬判据、判例与预校准（M1c 第二轮前置）](58-不可判判例与预校准.md) | 只有「图里根本没有呈现相关视觉证据」才允许判 unverifiable。 |
| [Registry 跟进操作单：moss-storyboard 版本状态](60-Registry跟进操作单.md) | 一句话：状态没变，跟进动作还没开始做——第一件要人做的事是把 §3 的联络稿发出去 |
| [T12 · 8B 契约验证（H3 Rewriter LoRA 真机）](61-T12-8B契约验证.md) | 8B 基座 + LoRA 已下载并逐字节核对（两个 sha 均 MATCH）；registry 已切 8B 契约（arch 用 GGUF |
