# AGENTS.md — MOSS Storyboard 的作业入口

> 给**改这个仓库的 agent** 读。规范源是 [`CONTRIBUTING.md`](CONTRIBUTING.md)；本文件只做**路由与判据映射**，冲突时以 `CONTRIBUTING.md` 为准。
> 🔴 **本文件不写任何项数/篇数/job 数**——写在这里的数字没有任何闸能抓，必然漂。要数字就去 `CONTRIBUTING.md` 的命令注释、`ci.yml` 文件头，或现场 `grep -c`。

---

## 0 · 30 秒版（只读这 5 条也能做对第一件事）

1. **先跑基线，别先读代码**：`python -m ruff check .` + `python tools/check_release_ready.py`。
   后者红**不一定是你的错**——未跟踪文件会让 S16 判红，处置见 §6。
2. **改参数 = 四处一起动**：`nodes.py` 的 `INPUT_TYPES()` · `docs/19-node-manual.md`（键名要用反引号包成 ``name``）·
   `web/moss_sb_i18n.js` 的 `LABELS`/`TIPS`（`TIPS` 只存 en）· 重跑 `tools/make_*.py` 重新生成示例。新 widget **只能加在末尾**。
3. **别手改 `examples/*.json`**：`widgets_values` 按位置存，错一位就静默串位。用生成器。
4. **交 PR 前必须绿**：ruff + 本层测试 + `tools/check_ui_workflow.py`（带文件参数）+ `python tools/make_dist.py --out dist/moss --verify` + `python tools/check_release_ready.py`。
5. **三条禁令**：别给闸接管道（`| tail` 会吃掉退出码）；别把「SKIP / 没跑」写成「通过」；别在仓库里留未跟踪文件。

---

## 1 · 三档检查（三个"通过"互不相等）

| 档 | 怎么跑 | 覆盖什么 | 跑不了时 |
|---|---|---|---|
| **本地零 GPU** | `CONTRIBUTING.md` 的命令清单 | 几何 / 契约 / 词表 / 编译层 / 前端纯函数 | —— |
| **CI（六个 job）** | `lint`（ruff）· `unit`（Python 3.10 · 3.11 · 3.12 矩阵）· `frontend`（node i18n）· `release-preview`（只构建不发布）· `host-matrix`（钉两个宿主 tag）· `windows` | 干净宿主、双版本兼容、发布卫生、打包面、跨平台 | 本机无法等价复现 GitHub Actions ⇒ PR 以 runner 实跑为准 |
| **真机** | `python tools/ci_preflight.py`（有环境跑、缺环境 SKIP）<br>四个 `tools/ui_*.mjs`（活宿主 + playwright）<br>GPU 端到端 `tools/e2e_matrix.py` | 宿主装载、无头浏览器、滚轮归属、成片 | **点名说没跑**，不许算通过 |

🔴 **退出码 2 = "没跑全"，但含义按脚本不同**：`ci_preflight.py` 缺环境**默认 exit 0 并输出 SKIP**，只有加 `--require-all` 才把 SKIP 判 2；
`ui_*.mjs` 是缺依赖 2 / **宿主连不上 2** / **`app.graph` 或 `app.canvas` 未就绪 2** / **一项断言都没跑成 2** / 断言失败 1；`make_dist.py --verify` 是缺宿主 2（构建本身可能已成功）；`check_editor_ui.py` 是缺 node 2（词表/语法跳过，**接线仍然查了**）。
报"未覆盖"时要写清是哪一部分。
🔴 **发布前**：`python tools/ci_preflight.py --require-all`（全量再加 `--with-gpu`）。

---

## 2 · 改什么 → 至少跑什么

| 你改了什么 | 至少跑 | 为什么 |
|---|---|---|
| 任意 Python | `python -m ruff check .` | CI 第一道；`审计报告/` 已被 pyproject 排除 |
| `core/layout.py` 几何 | `tests/test_core.py` → `tests/test_editor.py` → `tests/test_panel_assembly.py` | 几何只算一次，会穿透到拼版 |
| `nodes.py` 的 `INPUT_TYPES` | `tests/test_contract.py --no-host` + `tools/check_ui_workflow.py examples/*.json tests/fixtures/moss-storyboard-*.json` | 参数表唯一真相源；新 widget 只能追加末尾。⚠️ 这条命令的通配符由 shell 展开：**bash 可用**；PowerShell 要先 `Get-ChildItem examples\*.json …` 展开再传 |
| `core/editor.py`（state v3 编译层） | `tests/test_editor.py` + `tests/test_interop.py` + `tests/test_adversarial.py` | state v3 是冻结契约（`docs/10-contract.md`） |
| `v3/`（V1→V3 节点外壳） | `tests/test_contract.py --no-host` + `tools/smoke_nodes.py` | 外壳只转发，schema 由 `v3/_compat.py` 从 V1 生成 |
| `web/` 前端（预设/校验/elements） | `tests/test_cross_language.py` + `tools/check_frontend.py` + `tools/check_editor_ui.py` + `tests/test_i18n.mjs` | JS↔Python 差分；**新增 runner 必须断言两侧结果条数守恒**（`zip` 会静默截断成假绿） |
| 用户可见文字 | `tools/check_frontend.py` + `tests/test_docs_sync.py` + `tests/test_i18n.mjs` | 中英成对 + 词表纯净 |
| 旧工作流兼容 | `node tests/test_compat.mjs` + 真机 `tools/ui_widget_compat.mjs` | `widgets_values` 错位是静默坏 |
| `moss_llm` 子包 | `tests/test_llm_subpackage.py` + `tests/test_caption_quality.py` + `tests/test_node_caption_guard.py` | 包 sha / 反推护栏 / 四级降级 |
| smith IR / 扩写 | `python -m unittest tests.test_smith_ir` + `tests/test_expand_*.py` + `tools/check_coverage.py --check` + `python tools/freeze_smith_set.py --check` | 两道基线闸：覆盖率 + 4 份 IR × 5 后端 sha256。⚠️ 该脚本**无参数运行 = 重写基线**（不是「只看看」），且 docstring 里的文件名是旧的，别照抄 |
| H3 六段式 | `tests/test_h3_six.py` + `tests/test_h3_contract.py` + `tests/test_lang_zh.py` | 契约冻结六字段 |
| G7 / 评分 / 洗图 | `tests/test_g7_*.py` + `tests/test_pitfalls_ab.py` + `tests/test_wash_*.py` | 全是离线判据函数 |
| `examples/` 或打包面 | `tools/check_ui_workflow.py …` + `python tools/make_dist.py --out dist/moss --verify` | 干净副本独立装载最容易静默坏 |
| 报错文案（`raise` 的中文原文） | 三处同步 + `python tools/check_release_ready.py` | README_ZH §2.5 报错表**逐字**核 |
| 文档 / README | `tests/test_docs_sync.py` + `tools/check_release_ready.py` | 中英节级同步 + 文档清单与实物一致 |
| 发布相关 | `python tools/check_release_ready.py` + `python tools/ci_preflight.py --require-all` | 开源卫生 + 打包面 |

**改 YAML（新增家族/方言）也算改代码**：示例槽位与干净装载必跑。

---

## 3 · 同步链：改一处，要跟着改的

> 这张表是本文件的核心。agent 翻车不在"不知道跑哪条闸"，在"不知道还要改哪儿"。

| 起点 | 连带 | 谁核 |
|---|---|---|
| **版本号** | `__init__.py` 的 `__version__` · `pyproject.toml` 的 `version` 行 · `CHANGES.md` 首个 `## x.y.z` · `README.md` 的 `| Version | **x** |` · `README_ZH.md` 的 `| 版本 | **x** |` | **机检**（S5，五处格式各不相同，写成别的样子就抓不到） |
| **要发布** | `RELEASE-NOTES.md` 有 `## <version>` 一节，用 `<!-- EN -->` 分中英；中文段含「升级动作」、英文段含 `Action required`；**不许出现** `CHANGES.md`/`tests/`/`tools/`/`docs/`/「机检」「断言」这类内部记号 | **机检**（S8，含字数上限）+ `tools/release.py` |
| **`INPUT_TYPES` 增删参数** | `nodes.py` · `docs/19-node-manual.md`（反引号包键名）· `web/moss_sb_i18n.js` 的 `LABELS` 与 `TIPS` · 重跑 `tools/make_*.py` 重生成示例 | 机检（S18 / `test_contract.py`） |
| **新增节点** | 注册 · `v3` schema · `web/moss_sb_i18n.js` 的 `NODES`/`LABELS`/`TIPS` · `README.md`+`README_ZH.md` 的「七个节点」标题与表格 · `docs/19` 新增一节（README 锚点要跟着改） | 机检（`check_frontend.py` 核节点总数）+ **锚点无闸** |
| **新增示例** | 文件本身由 `tools/make_*.py` 生成 · 两份 README 画廊表各补一行 · `CONTRIBUTING.md` 与 `ci.yml` 的项数/文件数 · README 里的「十二份」类字样 | 半机检（S18 **只认中文 README**） |
| **新增 .py/.js/.mjs** | 前 5 行必须有 `SPDX-License-Identifier: MIT` | **机检**（S14 只读前 5 行） |
| **新增 tools 脚本** | `CONTRIBUTING.md` 命令清单加一行 + `ci.yml` 加一步（两处计数必须一致） | **机检**（S10 比对两处） |
| **报错文案** | `README_ZH.md` §2.5 报错表（逐字）· `README.md` 英文表 · `docs/19` | 机检（S18 归一化 `%s/%d` 与数字，但**不归一化符号**：`×` 与 ASCII `x` 是两回事） |
| **宿主下限 `requires-comfyui`** | `pyproject.toml` · `ci.yml` 的 `host-matrix` 矩阵 · `ci.yml` 里那条写死宿主版本前缀的内联断言 | 机检 |
| **断言数** | `CONTRIBUTING.md` 命令注释 + `ci.yml` 文件头 | **人**（没有第三条闸，所以才容易漂） |
| **pyproject 元数据** | `keywords`/`classifiers`/`dependencies` 必须在 `[project.urls]` **之前**，否则会被 TOML 归成 urls 表 ⇒ 宿主读不到、**静默失效** | 机检（CI 有一步）+ 本地看不出 |
| **新增图片** | 正式图进 `assets/`（根目录 `_*.png` 被 gitignore）；截图用 Playwright 生成 | **机检**（S17：ComfyUI 存的 PNG 带 `tEXt` 元数据，直接判红） |

---

## 4 · 文档路由（改什么读哪篇）

| 主题 | 先读 | 一句话 |
|---|---|---|
| 契约 / 互操作 / 参数 | `docs/10-contract.md` · `docs/12-interop.md` · `docs/15-recipe-contract.md` · `docs/19-node-manual.md` | `10` 是 state v3 冻结契约；`19` 是参数全表 |
| 几何 / 跨格一致 / 顶层 | `docs/14-top-level-design.md` · `docs/07-consistency.md` · `docs/06-vae-memory.md` | `14` 讲七个节点各自对什么负责 |
| 编辑器 / 前端 / 双语 | `docs/32-面板闸SKIP转真机.md` · `docs/45-用户文档与双语同步.md` · `docs/48-用户路径真机验证.md` | `48` 是用户路径真机验证口径 |
| LLM 子包 / 提示词工匠 | `docs/20-llm-prompt-nodes.md` · `docs/21-smith-ir-架构.md` · `docs/24-smith-ir-扩写判据.md` · `docs/26-扩写三拆.md` | `24` 讲"为什么写长点是错的" |
| H3 六段式 / LoRA | `docs/27-H3六段式.md` · `docs/35-H3-LoRA基座决策备忘.md` · `docs/61-T12-8B契约验证.md` | |
| 换脸 / 局部编辑 | `docs/16-faceswap-refactor.md` · `docs/17-faceswap-recipe.md` · `docs/18-masked-latent-edit.md` | 配方只有 `17` 里那几条真成过 |
| G7 / 评分 / 双盲 | `docs/43-双盲判读与评分校准.md` · `docs/53-双盲判读-第一轮.md` · `docs/58-不可判判例与预校准.md` | 判据 v2 在 `58` |
| CI / 兼容矩阵 / 发布 | `docs/44-CI第二矩与兼容矩阵.md` · `docs/29-跨家族registry验证.md` · `docs/60-Registry跟进操作单.md` | `44` 解释 host-matrix 为什么钉两个 tag |
| 路线图 / 验收 | `docs/34` · `docs/41` · `docs/55` · `docs/65` | 里程碑口径 |
| 交叉复核系列（36–38、40、47、49–51、54、62–64、66、69–71） | 按编号查 | **历史证据，不是规范**；别拿它当契约 |

> 读法：先 `grep` 定位，再 `read` 片段。**不要通读**——上表就是用来替代通读的。

---

## 5 · 六条硬规矩 × 谁在盯

| # | 规矩 | 盯着它 | 强度 |
|---|---|---|---|
| 1 | `INPUT_TYPES()` 是参数表唯一真相源，V3 schema 由 `v3/_compat.py` 生成；新 widget **只能追加到末尾** | 真相源：`test_contract.py` / S18；**位置**：`tools/check_ui_workflow.py` + `tests/test_compat.mjs`（插在中间 = 旧工作流串位） | 机检 |
| 2 | `seed` 类 INT 必须带 `control_after_generate` | `tools/smoke_nodes.py` | 机检 |
| 3 | 几何只算一次（`core/layout.Layout`），通过 `spec` 单线传下游 | `test_core.py` / `test_panel_assembly.py` | 机检 |
| 4 | **不静默兜底**：尺寸/spec/整除失败一律 raise 带操作指引的中文错误；降级必须出声 | `test_adversarial.py` / `test_caption_quality.py` | 机检 |
| 5 | i18n 只改显示不改存档：只碰 `label/title/tooltip`，不碰 `name/value/options.values` | `test_i18n.mjs` / `check_frontend.py` | 机检 |
| 6 | 用户可见同一句话有三份（`nodes.py` 中文 / `web/moss_sb_i18n.js` 的 `en` / README） | 无闸 | **人** |

---

## 6 · 五类假绿（看起来通过了，其实没验）

1. **卫生闸扫的是 `git ls-files`，不是磁盘**。新写的、还没 `git add` 的文件**根本不在扫描范围**。本地全绿 ≠ 提交后全绿。
2. **S18 只读 `README_ZH.md`**：英文 README 的漏行没有任何闸能抓。
3. **跨语言差分做过警告**：JS 少返回结果时 `zip` 会静默截断成"全绿"；新增 runner 必须断言**两侧结果条数守恒**。
4. **真机断言要读 `widget.value`**（界面真正显示的值），不是 `widgets_values`（存档数组）——两者可能一个对一个错。
   另：载工作流前必须等 `app.graph` **和** `app.canvas` 都就绪，早载会被随后自动恢复冲成空图。
5. **SKIP ≠ PASS**，且退出码 2 的含义按脚本不同（见 §1）。

---

## 7 · 常见失败与处置

| 现象 | 先查什么 | 怎么证 |
|---|---|---|
| 卫生闸报"有未跟踪文件" | 这不是你改坏的 | **别删别人的文件、别改 .gitignore**；用临时 clone 只拷本轮文件复跑 |
| 节点在画布上不出现 | 宿主 ≥ 0.42.0？`requires-comfyui` 命中？ | `python tools/host_loader_check.py` |
| 节点出现但出图不对 | 宿主可能 < 0.42.0：装得上、节点在，但 `CLIPLoader` 没有 `moss_image` ⇒ 提示词编码**静默错** | 看启动日志与节点 `report`，不是看"节点在不在" |
| 载入后参数错位 | 新 widget 是不是插到了中间 | `node tests/test_compat.mjs` + 真机 `ui_widget_compat.mjs` |
| 改了前端但界面没变 | 浏览器缓存？服务端真发新字节？ | `curl` 取文件比对内容，不看 mtime |
| 中文对、英文不对 | `en` 覆盖补了吗 | `tools/check_frontend.py` |
| 本地绿、CI 红 | Python 版本（CI 跑 3.10 · 3.11 · 3.12）或宿主 requirements 漂移 | 看 `ci.yml` 的 job 定义；注意仓库根的 `requirements.txt` 只是零依赖占位，宿主依赖在宿主自己的 `requirements.txt` |
| 只有 `windows` job 红 | 路径分隔符 / 大小写敏感 / CRLF / POSIX-only 命令 | 本机 Windows 上逐条复跑该 job 的脚本 |
| 断言数对不上 | CONTRIBUTING 与 ci.yml 两处一起改了吗 | `grep` 断言数 |

---

## 8 · 禁区（⛔ 一律先问人）

- ⛔ **不改 `[project].name`**：它是 registry 的安装 id，**发布后不可改**。
- ⛔ **不把凭据写进任何受控文件或提交信息**：`.comfy_registry_token` 只存在于本机（已 gitignore）。
- ⛔ **不 `git add -A`**；不替人提交、不替人推送、不重写历史。
- ⛔ **不在 `custom_nodes/` 下留本包的第二份副本**（会静默覆盖节点定义）。
- ⛔ **不手写第二份参数表 / 第二份断言数**。
- ⛔ **不静默降级**：`MOSSSB_NODE_API` 回退要出声（`__init__.py` 已经这么做，别改成静默）。
- ⛔ **不为了变绿去改闸**：改判据可以，改数据不行；闸错了要说明是"假红"还是"假绿"，并补一条新断言。

---

## 9 · 公开面纪律（仓库是公开的，读者只有代码）

- 不写**内部沟通**：谁提的要求、什么时候讨论的——要溯源就写「条件 + 结论」。
- 不写**本机环境**：盘符、用户名、家目录。举例用 `<ComfyUI>` / `/path/to/...`。
- **本文件也在扫描面上**：`check_release_ready.py` 的 S2/S15 会遍历**全部 git 跟踪的文本文件**，所以这里不许出现盘符路径与本机用户名。
- **新文件要先 `git add`**：卫生闸扫的是 `git ls-files`，未跟踪的新文件既不被核对、又会让 S16 判红。
- **提交信息本身进闸**：subject 形如 `type(scope): 描述`、长度有限制；不许出现盘符路径、主机名、协作者代号，以及转述谁提了什么要求的会话措辞。
- 已推送的历史改不动，闸也不假装能改。
- 两个公开面口径不同：**仓库**（含 `docs/`）与 **registry 发布包**（`.comfyignore` 之后的部分）。改文件前先问：这行会不会被发出去？
- **运行期文件别用中文名**：registry 打包按 latin-1 解路径，中文名会被**静默丢弃**——不报错，用户装完找不到。

---

## 10 · 路径与产物

| 项 | 值 | 说明 |
|---|---|---|
| 宿主 ComfyUI 根 | `COMFYUI_PATH`，或仓库根 `.comfyui_path` | 已 gitignore |
| 活宿主地址 | 默认 `http://127.0.0.1:8188`，`--url` 或 `COMFY_API` 覆盖 | 四个 `ui_*.mjs` 共用 |
| 节点 API 出口 | 默认 v3；`MOSSSB_NODE_API=v1` 回退（**回退路径无机检**，`docs/48` 记为未验证） | 改 `__init__` 或 `v3/` 时两个出口都要手验 |
| 一次性产物 | `out/` | 判读包、中间 JSON（已 gitignore） |
| 打包产物 | `dist/` | `make_dist.py` 生成；`.comfyignore` 决定分发面 |
| 审计报告 | `审计报告/` · `docs/_local/` | **不在仓库里**（被 gitignore）。README/docs 引用它 = 死链，S11 判红 |
| 本文件 | `AGENTS.md` | git 跟踪 ⇒ **默认进发布包**；不想发就把 `/AGENTS.md` 加进 `.comfyignore` 的开发设施段 |

---

## 11 · agent 作业纪律

1. **不要给闸命令接管道**：`… | tail` / `… | Select-Object -First 5` 会让退出码失去可见性，你会看到"通过"其实拿到 0。
   PowerShell 里显式收尾：`& $cmd; exit $LASTEXITCODE`。
2. **一次性逻辑不写内联 `python -c`**：任何会被跑第二次的东西落到 `tools/` 并配一条测试。
3. **等待用阻塞，不用轮询**：用 `tools/wait_for.py`——条件不成立就不返回，**一次调用换十次调用**。
   `python tools/wait_for.py --until file-absent:out/gpu.lock --timeout 1800`
   `python tools/wait_for.py --until dir-min-count:out/g7/round2/json --min 40 --pattern '*.json'`
   `python tools/wait_for.py --until gpu-free --min-mb 2048`
   退出码：`0` 成立 / `1` 超时（不是失败，是没结论）/ `2` 无法判定。别写成每秒一次 `Test-Path`。
4. **按阶段切会话**：长会话里每一步都在为前面所有内容重复付费；一个 phase 结束就换会话，大输出先落盘再按需读片段。
5. **先 `grep` 再 `read`**：docs 与 tools 都够多，通读是纯浪费。

---

## 12 · 交付口径

- **每条结论带一条可复跑的命令**，不写"我试过可以"。
- **没跑的档点名**：`真机未跑` / `GPU 未跑` / `Windows 未跑`。
- 提交信息写**为什么**（哪条判据错了、什么现象），不只写改了什么。

> 本文件非规范源。若与 `CONTRIBUTING.md` 冲突，**以 `CONTRIBUTING.md` 为准**并回来修本文件。
