# SECURITY

## 本包的攻击面（如实披露）

本包是本地 ComfyUI 自定义节点，运行在用户自己的进程里。**核心故事板节点**与**可选子包**的能力不同，分开写：

### 核心故事板 / 布局 / 裁切 / 贴回 / 草稿编译

- **不联网**：没有遥测、自动更新、远程配置。
- **不下载权重**：核心节点不读任何远程文件。
- **不执行外部命令**：核心库代码里没有 `subprocess` / `eval` / `exec`。
- **不读用户目录**：除 ComfyUI 自己的模型 / 输出目录外不访问文件系统。
- 前端脚本只操作画布节点对象与语言偏好（`localStorage`），不发起网络请求。

### 可选 LLM 子包（`moss_llm/`，未使用 LLM 节点时不会触发）

- `local` 通道：在本机 loopback 启动 `llama-server` 侧车，用你选的 GGUF；**GGUF 由你自备，包内不含权重**。
- `runtime = 更新到最新版`：会从 **GitHub 官方 llama.cpp release** 下载预编译包，校验 GitHub API 给出的
  sha256 后解压到 `moss_llm/runtime/`，再通过 `subprocess` 启动它；也支持用 `server_exe` 指向你已有的程序。
- `api` 通道：把你输入的文字与图片（base64）发到**你自己填的** OpenAI 兼容端点；不经过本包的中转。
- 这些行为只在用户主动使用 LLM 节点时发生，核心故事板节点不会触发。

### 可选换脸自动蒙版（`insightface` + `buffalo_l`）

- **不会自动联网下载**：只在检测不到权重时，在错误信息里给出 InsightFace 官方 `buffalo_l.zip` 地址。
- 权重默认从 `~/.insightface/models/buffalo_l/det_10g.onnx` 读取，可用 `MOSS_INSIGHTFACE_ROOT` 指向离线目录。
- 不装 `insightface` / 没有权重时，节点明确报错或退回几何蒙版，不静默降级。

### 通用

- **无遥测、无自动更新、无远程日志**。
- 前端脚本不发起网络请求；只写语言偏好。
- 包内不含任何模型权重、可执行文件或 pip 包。

## 报告漏洞

**请不要开公开 issue。** 用 GitHub 的 Private vulnerability reporting
（仓库 → Security → Report a vulnerability），或在 issue 里只写“我有安全问题，请给私下渠道”。

请在报告里给出：ComfyUI 版本、本包版本、复现步骤、影响范围。
我们会在 7 天内确认收到，30 天内给出结论或修复计划。

## 支持范围

只支持**最近两个小版本**内的本包代码 + `requires-comfyui` 声明范围内的宿主。
更早的版本请先升级再报。
