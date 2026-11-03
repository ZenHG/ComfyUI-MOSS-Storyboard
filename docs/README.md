# 文档索引（12 篇）

> 只列仍在 `docs/` 的文档；历史过程文档已归档，见 git 历史。

| 文档 | 一句话 |
|---|---|
| [06 · VAE 解码显存账（进阶）](06-vae-memory.md) | --- |
| [跨格一致性：机理、实测口径与旋钮边界](07-consistency.md) | --- |
| [10 · state v3 冻结契约](10-contract.md) | --- |
| [12 · 互操作：elements-v1 ⇄ state v3](12-interop.md) | 编辑器的 `editor_state` 是 v3 契约（`docs/10`），它解决「同一份状态怎么确定性编译」。 |
| [官方 prompt-enhancer 草稿：接入与置信度](13-prompt-enhancer.md) | OpenMOSS 在 `MOSS-Image` 仓库里发布了一个给 LLM agent 用的 skill： |
| [顶层设计与节点职责：这七个节点各自对什么负责](14-top-level-design.md) | 本包的承诺是： |
| [故事板配方契约：验证过的配方里，哪些项是必需的](15-recipe-contract.md) | 三份的全局段**都**含同一串网格声明，每一格的主句都以「**画面为**…」起句。 |
| [换脸节点：重构前 → 重构后](16-faceswap-refactor.md) | **前**：换脸 = 另一个包的一个编码节点 + 手接 `VAEEncode` + `KSampler` + 手工裁格贴回， |
| [换脸配方：哪条路真的成过](17-faceswap-recipe.md) | **原理**：MOSS 自己没有蒙版通道（`TextEncodeMOSSImageEdit` 的输入里没有 mask， |
| [蒙版潜空间编辑：MOSS 没做的那半条路](18-masked-latent-edit.md) | 三条硬证据（2026-10-29 读代码 + 工作流得到）： |
| [节点手册 · 参数全表](19-node-manual.md) | 同一个东西在不同地方叫不同名字，是这类文档最常见的误解来源。本包统一如下： |
| [20 · 可选 LLM 子包 `moss_llm`：一个节点，只出成品提示词](20-llm-prompt-nodes.md) | 提案里是 `MossLLMConfig` + `MossPromptSmith`（+ 可选 `MossPromptCheck`）三个。 |
