# 发布规范（RELEASING）

> 本文件是**发布流程的唯一真相源**。执行体 = [`tools/release.py`](tools/release.py)
> （两边必须一致；改了流程先改这里）。

## 0. 一句话

**一次发布 = 两个渠道同时更新，且回头验证过。**

```bash
python tools/release.py                 # 预演（前置 + 改动面，不推不发）
python tools/release.py --go            # 真发布：push → 等 CI 绿 → GitHub Release → registry publish → 交叉验证
python tools/release.py --verify-only   # 只回答「两边现在同步吗」（纯只读，不跑前置）
python tools/release.py --release-only  # 只补建 GitHub Release（幂等；不 push、不发 registry）
python tools/release.py --go --skip-ci-wait   # 🔴 跳过「等 CI 绿」（默认不跳）
python tools/release.py --set-token     # 轮换 registry PAT（从 stdin 读，不发布）
```

> 🔴 **`--verify-only` 不是「预演」**：它**只跑 ⑥ 交叉验证**（纯只读），不跑 ① 前置。
> 要看前置预演，**不带参数**跑。
>
> 🔴 **`--skip-ci-wait` 是逃生门**：跳过它 = 在没验证过的提交上发 registry，
> 而 registry 的 changelog **发出去就改不回来**。只在 CI 本身故障、且你另行确认过门槛全绿时才用。
>
> 🔴 **顺序是故意的**：唯一"改不回来"的动作（registry changelog）排在最后，
> 中间任何一步失败都停在「什么都没弄脏」的状态上。

## 1. 两个渠道各是什么

| 渠道 | 发布物 | "完成"的判据 |
|---|---|---|
| **GitHub**（`ZenHG/ComfyUI-MOSS-Storyboard`） | ① 提交历史 + 源码 ② **Release**（`vX.Y.Z` tag + 正文） | 远端 `main` 含本地 HEAD，**那一轮 CI 是绿的**，且 Releases 页面上**有本版** |
| **Comfy Registry**（`zenhg/moss-storyboard`） | `.comfyignore` 筛出的 zip（运行期文件，**不含 tests/tools/docs**） | `api.comfy.org/nodes/moss-storyboard/versions` 里**能查到本版版本号** |

🔴 **为什么必须成对**：任何一个单独成功都是**半成品**，而且两边都不报错 ——

- 只 push 不 publish ⇒ GitHub 是新版、Manager 里还是旧版（用户装到的和文档写的不是同一个东西）
- 只 publish 不 push ⇒ registry 那版指向的代码在远端不存在（无法复核、issue 无从查起）
- push 了但 CI 红 ⇒ 已公开的提交没过自检

## 2. 发布前置（缺一项就别开始）

1. **工作树干净** —— 发出去的必须是**提交态**（`release.py` 会拦）。
2. **版本号五处一致**（`check_release_ready.py` 的 S5 盯着）：
   `__init__.py` 的 `__version__` · `pyproject.toml` 的 `version` · `CHANGES.md` 顶部 `## x.y.z` ·
   `README.md` 的 `| 版本 | **x.y.z** |` · `README_EN.md` 的 `| Version | **x.y.z** |`。
3. **两份记录都写了本版**：
   - `CHANGES.md` 顶部那一节（面向开发者）
   - `RELEASE-NOTES.md` 有本版一节、**中英双语**（面向用户；registry changelog 与 GitHub Release 正文都取它）。
     ⚠️ **字符分段算**：中文 ≤700 · 英文 ≤1700（不是"总共 2000"）。格式见 §6。
4. **开源卫生门全绿**：`python tools/check_release_ready.py`（31 项，见 §3 ①）。
   ⚠️ 其中 **S9 扫的是提交信息**（最近 50 条）：内部措辞或当前环境路径一旦推上去，
   就只能改写历史（force push + 重建 tag），代价远高于改文件 —— 所以**推之前**跑这一遍别省。
5. **全量回归全绿**：`CONTRIBUTING.md` 列的全部命令。`release.py` 只跑其中 2 条，
   全量请你手跑——**发布前的那次别省**。
   CI 另有 `release-preview`：`check_release_ready.py` + `make_dist.py --zip`（只构建分发包、上传 artifact，
   **不发布**）；`host` job 再用 `make_dist.py --verify` 从干净副本独立加载并跑编辑器/宫格/裁格。
6. **registry 元数据合规**（S6 盯着）：`name` 不带 `ComfyUI`、`license` 为 `{ file = … }`、
   `PublisherId` 非占位（本仓 = `zenhg`）、`Icon`/`Banner` 是 https 直链。
7. **两个 CLI 能跑**（判据是**真跑一次 `--version`**，不是"文件在不在"）：

   ```bash
   gh --version                                                            # 已登录 GitHub 账号
   <comfy-cli 安装目录>/Scripts/comfy.exe --version                        # → 1.22.0
   ```

   ⚠️ **`comfy` 可能不在 PATH 上**（常见于装在隔离 venv 里）。
   用 `COMFY_CLI=<comfy 可执行文件>` 指给 `release.py`；`_find_comfy()` 也会自己试候选，
   但显式给最稳。
   ⚠️ 重装时：**清华源没有 `comfy-cli`**，要 `-i https://pypi.org/simple`；
   隔离 venv 用**系统 Python 3.12** 建（3.13 建的在清华源上装不上）。

## 3. 执行体做的六步（`tools/release.py --go`）

| 步 | 动作 | 失败时的状态 |
|---|---|---|
| ① 前置 | 干净工作树 · 版本五处 · **`check_release_ready.py`（31 项）** · **`test_contract.py --no-host`（113 项）** · `RELEASE-NOTES.md` 本版一节 | 什么都没发生 |
| ② GitHub push | `git push origin HEAD:refs/heads/main`（失败自动换 HTTP/1.1 / schannel / 去代理直连重试） | 什么都没发生 |
| ③ 等 CI | 找 `headSha == 本地 HEAD` 那一轮，`gh run watch --exit-status` | **已 push、未 publish** ⇒ 修完重跑，安全 |
| ④ GitHub Release | `gh release create vX.Y.Z --target <HEAD> --notes-file <RELEASE-NOTES 生成>`。**幂等**：已存在就不动 | 已 push、未 Release ⇒ 重跑 |
| ⑤ Registry | `comfy node validate` → `node pack` 预览（开发件混入即拒）→ `publish --token --changelog` | 已 push、未 publish ⇒ 重跑 |
| ⑥ 交叉验证 | 远端 `main` 含 HEAD？registry 版本列表含本版？Releases 有本版？ | **点名**哪一项没过 |

> ⚠️ **③ 的失败是本流程唯一的"半成品"状态**（GitHub 有了、registry 没有）。
> 这是**有意如此**：宁可在 registry 上少一版，也不要在 CI 红的提交上发版。
> 修完再跑一遍即可，registry 那边不会被弄脏。

## 4. 失败处置速查

| 症状 | 含义 | 怎么办 |
|---|---|---|
| `git: 'remote-https' is not a git command` | 便携版 git 缺助手 | 设 `MOSSSB_GIT` 指向完整版 git |
| `CONNECT tunnel failed` | 当前环境代理对 git 打回 | 脚本会自动去代理直连重试一次 |
| `comfy node publish` 报 publisher 不匹配 | `PublisherId` 与网站上不一致 | 改成网站上 `@` 后面那串（**两边都不可改，别猜**） |
| `comfy node validate` 报警 | 元数据或打包口径问题 | 看具体行；**别为消警告换拼法** |
| registry 说版本已存在 | 同版本不能重发 | bump 一版（**已发布版本与 changelog 都不可改**） |
| `gh` 找不到 / 建 Release 失败 | 没装或没登录 | 装/登录 gh。这一步**在 registry 之前** ⇒ 停在这里 = 什么都没弄脏 |
| `Release vX.Y.Z 已存在` | tag 上已有 Release | 脚本**幂等跳过**（不擅自覆盖正文）；要改去网页上改 |

## 5. 密钥（Registry PAT）

- 取得方式**只能在网页上做**：registry.comfy.org 用 GitHub 登录 → 建 Publisher → 生成 API Key。
  没有任何 API/CLI 路径。`PublisherId` **发布后不可改**。
- 本仓**不复制**凭据文件；凭据只放在仓库外的单一位置：

  ```bash
  COMFY_REGISTRY_PAT_FILE=/path/to/.comfy_registry_token python tools/release.py
  ```

  为什么不复制第二份：凭据的副本越多，进 git / 进日志 / 进截图的面就越大，
  而**一旦进了 git 历史就擦不干净**（要 rewrite + force push）⇒ 判据只能是"别让它进来"。
- 轮换用 `--set-token`：**从 stdin 读**（不留在 shell 历史），不发布。
- `release.py` 会把 Token 记进 `SECRETS`，之后**所有命令回显都脱敏**。
- 发布前会**真查**一遍它不会被上传：既不能在 `git ls-files` 里，也必须命中忽略规则。

## 6. 版本与发布说明

### 6.1 版本号

`0.1.0` 起。级别由维护者按**发布节奏**决定，不机械跟随改动类型：

- 修 bug → PATCH；
- 新增节点 / 改输入输出 / 改 v3 状态格式 → MINOR；
- 仅新增编辑器交互（不改节点契约、不破坏已有工作流）→ 可由维护者决定走 PATCH
  （先例：0.2.2 的撤销/重做，维护者按节奏定为 PATCH）；
- 破坏兼容 → MAJOR。

版本号**不宜频繁跳**：同一迭代内的功能尽量并入当前 PATCH/MINOR，攒到下一次发布；
registry 版本不可重发，所以定级只发生在发布前、由维护者拍板。

### 6.2 两份记录、两个读者（**别混**）

| 文件 | 读者 | 写什么 |
|---|---|---|
| `RELEASE-NOTES.md` | **用户** | 能感知的变化、要不要动配置。**两个渠道的用户可见文案都取这里** |
| `CHANGES.md` | 开发者 | 实现方案、实测证据、修了哪些坑 |

用户可见文案里**不许出现内部记号**（文件名、目录名、"机检"、"断言"之类）——
`check_release_ready.py` 的 S8 会拦。

### 6.3 每节的格式

```md
## X.Y.Z — YYYY-MM-DD

**一句话说这一版是什么。**

- 用户能感知的变化
- 🔴 有行为变化时，点明「要不要动配置」

**升级动作**：…

<!-- EN -->

**One line saying what this release is.**

- What the user can perceive

**Action required**: …
```

`check_release_ready.py` S8 **硬校验**：本版一节存在、含 `<!-- EN -->`、中 ≤700 / 英 ≤1700 字符、
两端各有「升级动作」/「Action required」、无内部记号。不符即拒绝发布（在 push 之前）。

## 7. 明确不做的事

- **不做自动 bump**：版本号是人定的，脚本只校验一致性。
- **不覆盖已存在的 GitHub Release**：正文可以改，但改不改是人的决定。
- **不重发同版本到 registry**：同版本会被拒，而且 changelog 不可改 ⇒ 只能 bump。
- **不在 CI 里发布**：发布要 PAT 与人工判断，CI 只做验证。
