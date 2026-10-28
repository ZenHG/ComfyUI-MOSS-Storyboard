# RELEASING · 维护者发布手册

> 两个渠道：**GitHub**（仓库 + Release）与 **Comfy Registry**（节点市场）。
> 发布器 `tools/release.py` 是本文的**执行体**；本文只写"为什么"和"失败怎么处置"。
> 判据（版本五处一致、发布说明字数、卫生闸……）都在脚本里，不在这里重抄一遍。

---

## 0. 一次发布要满足的前提

| 项 | 怎么满足 |
|---|---|
| 工作树干净 | 发出去的东西必须是**提交态**，否则包里混着未提交的改动 |
| 版本号五处一致 | `__init__.py` / `pyproject.toml` / `CHANGES.md` / `README.md` / `README_ZH.md`（S5） |
| `RELEASE-NOTES.md` 有本版一节 | 中英双语，中文 ≤700 字符、英文 ≤1700 字符（S8） |
| 发布卫生闸绿 | `python tools/check_release_ready.py` |
| 契约测试绿 | `python tests/test_contract.py --no-host` |
| `gh` 可用 | 用来等 CI、建 Release、做交叉验证 |
| `comfy`（comfy-cli）**真能跑** | 判据是 `comfy --version` 能返回 0，不是"文件在不在" |
| registry PAT | 只放 `<仓根>/.comfy_registry_token`，见 §4 |

全量回归（`CONTRIBUTING.md` 那一整页）在 `--go` 之前应当已经跑过；发布器只拦最会翻车的两条
（卫生 + 契约），**它不替你做全量回归**。

---

## 1. 先预演，再真发

```bash
python tools/release.py            # 预演：① 前置 + 打出改动面，不推不发
python tools/release.py --go       # 真发：① → ②push → ③等 CI → ④Release → ⑤registry → ⑥验证
```

预演和 `--verify-only` **不是一回事**，跑的人容易理解错，所以脚本会自己把模式打出来：

- `--verify-only`：**只**跑 ⑥ 交叉验证（纯只读），不碰 ① 前置；
- `--release-only`：只补建/检查 GitHub Release（幂等）；
- `--skip-ci-wait`：跳过等 CI —— **除非你明确知道在做什么，别用**。

---

## 2. 六个步骤，以及哪一步可以重来

| 步 | 做什么 | 失败了怎么办 |
|---|---|---|
| ① 前置 | 干净工作树 / 版本五处 / 卫生闸 / 契约闸 / 发布说明 | 直接修，**什么都没有发生** |
| ② push | 推 `HEAD:main`，四次重试（普通 → HTTP/1.1 → schannel → 去代理直连） | 网络层问题。**先 `git ls-remote` 核对**——报错不等于没推上去 |
| ③ 等 CI | 按 `headSha` 找**这一次**的运行，`gh run watch --exit-status` | CI 红 ⇒ 已经推上去了，但**不要**发 registry；修完重跑，registry 那步没做，版本还是旧的，安全 |
| ④ GitHub Release | tag `vX.Y.Z` 指到本次 HEAD，正文取 `RELEASE-NOTES.md`；中间漏掉的版本会一起补 | 幂等，可单独 `--release-only` 重跑 |
| ⑤ registry publish | `comfy node validate` → 打包预览 → `publish` | 🔴 **这一步不可逆**：版本号发上去就不能复用，只能发新版本 |
| ⑥ 交叉验证 | 远端 main 包含本地 HEAD / registry 有本版 / Release 在位 | 只读，随便重跑：`--verify-only` |

**顺序不能换**：发布说明（registry 的 changelog）必须在 push 之前校验 ——
它发出去就改不回来，而 push 过了再卡住会留下「GitHub 有了、registry 没有」的半成品。

---

## 3. 打包面

- `.comfyignore` 决定**发出去什么**：默认打包全部 git 跟踪文件，排除开发设施。
  文件末尾有一份「**绝不能排**」清单（运行期 import 的模块、`pyproject.toml`、`LICENSE`、`examples/`），
  排掉就是 ImportError 或"叫用户去开一个不存在的文件"。
- 发布器会先把包解开看一遍：`docs/`、`tests/`、`tools/`、`.github/` 混进去就**拒绝发布**。
- 路径**必须是 ASCII**：registry 打包按 latin-1 解路径，中文名会被**静默丢弃**（闸里判红）。

---

## 4. 凭据纪律（唯一一节不能跳过的）

registry 的 PAT **只存一个地方**：`<仓根>/.comfy_registry_token`（`.gitignore` + `.comfyignore` 双重忽略）。

**写它只有一种方式**：

```bash
python tools/release.py --set-token     # 从 stdin 读，回显不显示，不进 shell 历史
```

🔴 **不要** `echo <PAT> > .comfy_registry_token`，也**不要** `export COMFY_REGISTRY_PAT=…` ——
两者都会把明文留在 shell 历史 / 其它进程的环境里。

`--set-token` 写完会立刻验三件事：忽略规则命中、未被 git 跟踪、长度像 PAT；**不做任何网络动作**。

其余纪律：

- **回显一律脱敏**：发布器把所有外部命令的输出过一遍掩码，`die()` 也不例外。
- **提交信息里也不能有**：一旦进 git 历史就要 rewrite + force push，而 force push **清不掉
  GitHub 上的旧对象**（旧 sha 仍可访问）。判据只能是"别让它进来"。
- 轮换 Key 之后，**去 registry 网页把旧 Key 作废** —— 本地删文件 ≠ 远端失效。
- 别用"干脆不回显命令"来避险：那会丢掉"我到底发了什么"的复核能力。

---

## 5. 发布后

```bash
python tools/release.py --verify-only   # 只读：两个渠道 + 本机 交叉核对
```

通过时脚本会打出三个坐标：GitHub 的提交、Release 的 tag、registry 的版本。
**三个都要对得上**才算发完——命令的退出码不是证据，回头查才是。

---

## 6. 常见故障

| 现象 | 真因 | 处置 |
|---|---|---|
| push 报 `CONNECT tunnel failed 502` / `server closed abruptly`，但 `gh api` 正常 | 代理对 CONNECT/POST 打回 502；Windows 上偶尔是 TLS 重协商 | 脚本已内置四次重试；仍失败就 `git ls-remote` 核对，常常已经推上去了 |
| `comfy node validate` 失败但 stdout 是空的 | 多半不是仓库校验没过，而是 **comfy-cli 自身没起来**（真因在 stderr，例如 hermes wrapper 报 `uv.lock needs to be updated`） | `COMFY_CLI=<能跑的 comfy>` 显式指定；或重建隔离 venv |
| 包里有 `docs/` `tests/` | `.comfyignore` 被改坏 | 修 `.comfyignore`，别绕过 |
| 发布后 registry 页面文字要改 | changelog 是不可变的历史 | 只能发新版本，在下一版说明里更正 |

---

## 7. 想回退怎么办

- **GitHub Release**：可以编辑正文、删除重建；tag 也能移动，但对已经拉过代码的人无效，别指望它撤回内容。
- **registry**：版本**不能改**。发现发错了就立刻发一个补丁版本，并在它的说明里写清楚上一版错在哪。
- **已经推上去的错误提交**：优先用**新提交**修正，而不是改写历史 —— 除非里面真的有凭据，
  那种情况按 §4 立刻作废 Key，然后再谈改写。
