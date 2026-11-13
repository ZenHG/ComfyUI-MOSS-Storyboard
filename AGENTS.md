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
4. **交 PR 前的最小集合**（不是全部，别照抄）：ruff + 本层测试 + `tools/check_ui_workflow.py`（带文件参数）+
   `python tools/make_dist.py --out dist/moss --verify` + `python tools/check_release_ready.py`。
5. **三条禁令**：别给闸接管道（`| tail` 会吃掉退出码）；别把「SKIP / 没跑」写成「通过」；别在仓库里留未跟踪文件。

> **推送前另有一条硬闸**：按 `ci.yml` 的 unit / host / windows job 命令逐条本地跑（见 §2）；
> 缺 `numpy`/`Pillow`、没模拟 Windows 编码、没跑 `test_smith_ir` 的「本地绿」不算绿。

---

## 1 · 三档检查（三个"通过"互不相等）

| 档 | 怎么跑 | 覆盖什么 | 跑不了时 |
|---|---|---|---|
| **本地零 GPU** | `CONTRIBUTING.md` 的命令清单 | 几何 / 契约 / 词表 / 编译层 / 前端纯函数 | —— |
| **CI（`ci.yml` 的全部 job）** | `lint`（ruff）· `unit`（Python 3.10 · 3.11 · 3.12 矩阵）· `frontend`（node i18n）· `release-preview`（只构建不发布）· `host-matrix`（钉两个宿主 tag）· `windows` | 干净宿主、双版本兼容、发布卫生、打包面、跨平台 | 本机无法等价复现 GitHub Actions ⇒ PR 以 runner 实跑为准 |
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
| `nodes.py` 的 `INPUT_TYPES` | `tests/test_contract.py --no-host` + `tools/check_frontend.py` + `tools/check_ui_workflow.py examples/*.json tests/fixtures/moss-storyboard-*.json` | 参数表唯一真相源；新 widget 只能追加末尾；新参数缺「英文提示 / 中文标签 / 取值中文」= F5b·F6a·F7 红（2026-11-13 连红一次）。⚠️ 这条命令的通配符由 shell 展开：**bash 可用**；PowerShell 要先 `Get-ChildItem examples\*.json …` 展开再传 |
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
| **推送前（任意改动）** | `ci.yml` 的 unit job 全部 `run:` 命令 + `host-matrix` 三项 + windows 子集；缺依赖按 job 装（`torch(cpu)` / `numpy` / `Pillow`） | 单 OS / 单 Python 的自选测试 ≠ CI；矩阵结论以 runner 为准 |
| **workflow 改动** | `actionlint .github/workflows/ci.yml` + 本地 YAML 解析 | job 级 `env` 不能用 `${{ runner.temp }}`；宿主路径统一 `COMFYUI_PATH` |
| **`pyproject.toml` 的 description / keywords** | 两份 README 首屏 + GitHub About/topics 同步；registry 元数据随**新版本发布**才生效 | 搜索词漂移；`description` 要覆盖实际能力（含 LLM/Flux/SDXL 等） |
| **`SECURITY.md` / 新增联网、下载、子进程、可选依赖** | 按「核心 / 可选 LLM / 换脸」分段如实披露，并与代码逐项对照 | 披露与代码不符 = 复核风险，比不写更糟 |

**改 YAML（新增家族/方言）也算改代码**：示例槽位与干净装载必跑。

---

## 3 · 同步链：改一处，要跟着改的

> 这张表是本文件的核心。agent 翻车不在"不知道跑哪条闸"，在"不知道还要改哪儿"。

| 起点 | 连带 | 谁核 |
|---|---|---|
| **版本号** | `__init__.py` 的 `__version__` · `pyproject.toml` 的 `version` 行 · `CHANGES.md` 首个 `## x.y.z` · `README.md` 的 `| Version | **x** |` · `README_ZH.md` 的 `| 版本 | **x** |` | **机检**（S5，五处格式各不相同，写成别的样子就抓不到） |
| **要发布** | `RELEASE-NOTES.md` 有 `## <version>` 一节，用 `<!-- EN -->` 分中英；中文段含「升级动作」、英文段含 `Action required`；**不许出现** `CHANGES.md`/`tests/`/`tools/`/`docs/`/「机检」「断言」这类内部记号 | **机检**（S8，含字数上限）+ `tools/release.py` |
| **`INPUT_TYPES` 增删参数** | `nodes.py`（**追加项一律进 `optional`**：进 `required` 会让存量工作流报 `Required input is missing`）· `docs/19-node-manual.md`（反引号包键名）· `web/moss_sb_i18n.js` 的 `LABELS`+`LABELS_EN` / `TIPS` / **`COMBOS`**（ASCII 取值必须有中文）· 重跑 `tools/make_*.py` 重生成示例；**手写示例**（`examples/llm-*.json`）要自己补 `widgets_values` 末项 · `tools/check_ui_workflow.py` 的 `LEGACY_TAIL` 加上这一项（存量 N-1 格才合法） | 机检（S18 / `test_contract.py` / `check_frontend.py` / 示例闸） |
| **新增节点** | 注册 · `v3` schema · `web/moss_sb_i18n.js` 的 `NODES`/`LABELS`/`TIPS` · `README.md`+`README_ZH.md` 的「七个节点」标题与表格 · `docs/19` 新增一节（README 锚点要跟着改） | 机检（`check_frontend.py` 核节点总数）+ **锚点无闸** |
| **新增示例** | 文件本身由 `tools/make_*.py` 生成 · 两份 README 画廊表各补一行 · `CONTRIBUTING.md` 与 `ci.yml` 的项数/文件数 · README 里的「十二份」类字样 | 半机检（S18 **只认中文 README**） |
| **新增 .py/.js/.mjs** | 前 5 行必须有 `SPDX-License-Identifier: MIT` | **机检**（S14 只读前 5 行） |
| **新增 tools 脚本** | `CONTRIBUTING.md` 命令清单加一行 + `ci.yml` 加一步（两处计数必须一致） | **机检**（S10 比对两处） |
| **报错文案** | `README_ZH.md` §2.5 报错表（逐字）· `README.md` 英文表 · `docs/19` | 机检（S18 归一化 `%s/%d` 与数字，但**不归一化符号**：`×` 与 ASCII `x` 是两回事） |
| **宿主下限 `requires-comfyui`** | `pyproject.toml` · `ci.yml` 的 `host-matrix` 矩阵 · `ci.yml` 里那条写死宿主版本前缀的内联断言 | 机检 |
| **断言数** | `CONTRIBUTING.md` 命令注释 + `ci.yml` 文件头 | **人**（没有第三条闸，所以才容易漂） |
| **pyproject 元数据** | `keywords`/`classifiers`/`dependencies` 必须在 `[project.urls]` **之前**，否则会被 TOML 归成 urls 表 ⇒ 宿主读不到、**静默失效** | 机检（CI 有一步）+ 本地看不出 |
| **About / 搜索词** | `pyproject.toml` 的 `description`/`keywords` · README 首屏 · GitHub About/topics；registry 页面要等**下一个版本** | 半机检（S6 元数据）+ 人核搜索词 |
| **归档文档** | 先 `grep` 代码/测试/README 的引用；代码注释与测试断言里的 `docs/NN` 可先改成「历史记录见 git 历史」再归档；README 链着的契约文档保留 | 无闸；漏改 = 死指针 |
| **新增测试文件** | `ci.yml` unit job 加一步；跨平台行为同时加进 windows 子集；`CONTRIBUTING.md` 命令清单加一行 | S10 比对两处清单 |
| **示例合并/删除** | 生成器 · 两份 README 表与计数 · `ci.yml`/`CONTRIBUTING` 项数 · **加载该示例的 UI 测试**（如 `ui_llm_panel.mjs`） | 半机检（S18 只认中文 README）+ 真机 UI |
| **新增图片** | 正式图进 `assets/`（根目录 `_*.png` 被 gitignore）；截图用 Playwright 生成 | **机检**（S17：ComfyUI 存的 PNG 带 `tEXt` 元数据，直接判红） |

---

## 4 · 文档路由（改什么读哪篇）

| 主题 | 先读 | 一句话 |
|---|---|---|
| 契约 / 互操作 / 参数 | `docs/10-contract.md` · `docs/12-interop.md` · `docs/15-recipe-contract.md` · `docs/19-node-manual.md` | `10` 是 state v3 冻结契约；`19` 是参数全表 |
| 几何 / 跨格一致 / 顶层 | `docs/14-top-level-design.md` · `docs/07-consistency.md` · `docs/06-vae-memory.md` | `14` 讲七个节点各自对什么负责 |
| 编辑器 / 前端 / 双语 | 已归档（历史记录见 git 历史） | — |
| LLM 子包 / LLM 提示词 | `docs/20-llm-prompt-nodes.md` | — |
| H3 六段式 / LoRA | 已归档（历史记录见 git 历史）；现行契约见 `CHANGES.md` | — |
| 换脸 / 局部编辑 | `docs/16-faceswap-refactor.md` · `docs/17-faceswap-recipe.md` · `docs/18-masked-latent-edit.md` | 配方只有 `17` 里那几条真成过 |
| G7 / 评分 / 双盲 | 已归档（历史记录见 git 历史） | — |
| CI / 兼容矩阵 / 发布 | `CONTRIBUTING.md` + `.github/workflows/ci.yml` 注释 | — |
| 路线图 / 验收 | 已归档（历史记录见 git 历史）；现行验收见 `CHANGES.md` | — |
| 交叉复核系列 | 已归档（历史记录见 git 历史） | **历史证据，不是规范**；别拿它当契约 |

> 读法：先 `grep` 定位，再 `read` 片段。**不要通读**——上表就是用来替代通读的。

---

## 5 · 硬规矩 × 谁在盯

| # | 规矩 | 盯着它 | 强度 |
|---|---|---|---|
| 1 | `INPUT_TYPES()` 是参数表唯一真相源，V3 schema 由 `v3/_compat.py` 生成；新 widget **只能追加到末尾** | 真相源：`test_contract.py` / S18；**位置**：`tools/check_ui_workflow.py` + `tests/test_compat.mjs`（插在中间 = 旧工作流串位） | 机检 |
| 2 | `seed` 类 INT 必须带 `control_after_generate` | `tools/smoke_nodes.py` | 机检 |
| 3 | 几何只算一次（`core/layout.Layout`），通过 `spec` 单线传下游 | `test_core.py` / `test_panel_assembly.py` | 机检 |
| 4 | **不静默兜底**：尺寸/spec/整除失败一律 raise 带操作指引的中文错误；降级必须出声 | `test_adversarial.py` / `test_caption_quality.py` | 机检 |
| 5 | i18n 只改显示不改存档：只碰 `label/title/tooltip`，不碰 `name/value/options.values` | `test_i18n.mjs` / `check_frontend.py` | 机检 |
| 6 | 用户可见同一句话有三份（`nodes.py` 中文 / `web/moss_sb_i18n.js` 的 `en` / README） | 无闸 | **人** |

---

## 6 · 假绿清单（看起来通过了，其实没验）

1. **卫生闸扫的是 `git ls-files`，不是磁盘**。新写的、还没 `git add` 的文件**根本不在扫描范围**。本地全绿 ≠ 提交后全绿。
2. **S18 只读 `README_ZH.md`**：英文 README 的漏行没有任何闸能抓。
3. **跨语言差分做过警告**：JS 少返回结果时 `zip` 会静默截断成"全绿"；新增 runner 必须断言**两侧结果条数守恒**。
4. **真机断言要读 `widget.value`**（界面真正显示的值），不是 `widgets_values`（存档数组）——两者可能一个对一个错。
   另：载工作流前必须等 `app.graph` **和** `app.canvas` 都就绪，早载会被随后自动恢复冲成空图。
5. **SKIP ≠ PASS**，且退出码 2 的含义按脚本不同（见 §1）。
6. **自选测试 + 缺依赖的「本地绿」**：只跑改动相邻的几个文件、本机又恰好装了 `numpy`/`Pillow`，
   CI 会在 `test_smith_ir` / `test_wash_benchmark` / Windows 编码上翻红。推送前按 `ci.yml` 逐条跑。
7. **registry「版本历史可见」当「已发布」**：页面能看到 `0.4.x` 不等于 latest 已切换；
   只有 `status=Active` 且 `latest_version=本版` 才算。`Pending`/`Flagged` 只是已提交待复核。
8. **过拟合的绿灯**：只测紧贴本次实现的形态、或为了让绿灯成立去改判据 / 补豁免，绿灯只证明
   想象力不够。反向验证（故意改错实现必须判红）才算证据；跑一堆改动面外的闸凑「全绿」同样不算。
   判据与范围见 §8 的禁区。
9. **单测绿 ≠ 宿主契约绿**：测的是「我直接调函数」，宿主走的是另一条路（schema 生成、入参校验、
   外壳转发、前端序列化）。**改宿主契约面（schema / 校验 / widget 序列 / 前端 DOM）时，
   必须有一条经宿主入口的验证**：`/prompt` 提交、`/object_info`、真页面。
   实证（2026-11-05）：LLM 单测 153/0 全绿，手填的 `api_model` 仍被宿主判 `value_not_in_list` ——
   V3 外壳不继承 V1 的 `VALIDATE_INPUTS`，单测根本不经那条路。
10. **错误分支只测了「自己设想的失败」**：把 A 的报错当 B 治，比不处理更贵（多打五六次请求 + 误导信息）。
   每个错误分支至少有一条「报错原文不含本分支关键词」的用例，判据见 §12。
   实证（2026-11-05）：400 一律当「参数不支持」去摘 `response_format`，而真因是网关缺
   `x-opencode-session`；加了 `_FATAL_400` / `_KNOB_400` 分流后同一次请求 5~6 次往返降到 1 次，
   报错第一句也对得上了。

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
- ⛔ **不照单全跑发布前置清单**：`docs/21` §7 的命令块是**发布前**清单，不是每次改动清单。
  按 §2 与 §7 的影响面表只跑改动面内的闸。实证（2026-11-04）：只改 README / 示例却跑 E2E
  全矩阵（16 条真出图、5m25s GPU），与改动零交集；改 `web/*.js` 只跑真机 UI + `check_frontend.py`。
- ⛔ **不把推测写成已批准的任务卡**：从调研缺口推出来的卡要标「未核实 / 待定」，不排进
  "下一步计划"当既定项。实证（2026-11-04）：T16 未核实未批准，却排进 §12.5 排序结论。
- ⛔ **不写没有证据的兼容 / 防御**：兼容分支必须写清「谁在什么情况下需要它」，写不出就删。
  实证（2026-11-05）：为 Python 3.9 写 `enumerate` 兜底 —— 而 `requires-python` 早就是 `>=3.10`、
  CI 矩阵也是 3.10/3.11/3.12，纯属想象；`api_key_env` 一栏无人使用，却占一格存档、还逼出迁移逻辑。
  代价不对称：删掉是零成本，留着要一直为它写测试与文档。
- ⛔ **不为了变绿去改闸**：禁止的是「为了让本次实现不命中证伪线，把判据改成刚好放自己过去」。
  判据**本身不合理**时（例如把「新增功能」和「堆代码换零改进」一刀切），可以说清理由、经人裁定后修正，
  并在卡里写明改善证据；这不是过拟合。实证（2026-11-04）：`docs/21` §4.4 第 4 条原为「净增 > 0 即失败」，
  维护者裁定改为「净增须换来可判定的正确度/效率改善」，T14 以 6 条真机断言 + 53/0 举证，不命中。
  **未经裁定、只为了让绿灯成立而改判据，仍然禁止**。

---

## 9 · 公开面纪律（仓库是公开的，读者只有代码）

- 不写**内部沟通**：谁提的要求、什么时候讨论的——要溯源就写「条件 + 结论」。
- 不写**本机环境**：盘符、用户名、家目录。举例用 `<ComfyUI>` / `/path/to/...`。
- **提交身份**：维护者提交必须用 GitHub noreply（`<id>+<login>@users.noreply.github.com`），
  个人邮箱不得进入任何提交；远端历史若含个人邮箱，先整体改写再继续推送。
  检查：`git log --format='%ae' | sort -u` 只应出现 noreply 地址。
- **本文件也在扫描面上**：`check_release_ready.py` 的 S2/S15 会遍历**全部 git 跟踪的文本文件**，所以这里不许出现盘符路径与本机用户名。
- **新文件要先 `git add`**：卫生闸扫的是 `git ls-files`，未跟踪的新文件既不被核对、又会让 S16 判红。
- **提交信息本身进闸**：subject 形如 `type(scope): 描述`、长度有限制；不许出现盘符路径、主机名、协作者代号，以及转述谁提了什么要求的会话措辞。
- 已推送的历史默认不改；唯一例外是隐私清理（个人邮箱等），改写后必须 force-push，并明确告知影响。
- 两个公开面口径不同：**仓库**（含 `docs/`）与 **registry 发布包**（`.comfyignore` 之后的部分）。改文件前先问：这行会不会被发出去？
- **运行期文件别用中文名**：registry 打包按 latin-1 解路径，中文名会被**静默丢弃**——不报错，用户装完找不到。
- **SECURITY.md 是公开承诺**：新增联网/下载/子进程/可选依赖时必须同步改；核心与可选子包分开写，
  不要让「核心不联网」被读成「整个包不联网」。

---

## 10 · 路径与产物

| 项 | 值 | 说明 |
|---|---|---|
| 宿主 ComfyUI 根 | `COMFYUI_PATH`，或仓库根 `.comfyui_path` | 已 gitignore |
| 活宿主地址 | 默认 `http://127.0.0.1:8188`，`--url` 或 `COMFY_API` 覆盖 | 四个 `ui_*.mjs` 共用 |
| 节点 API 出口 | 默认 v3；`MOSSSB_NODE_API=v1` 回退（**回退路径无机检**，历史记录见 git 历史） | 改 `__init__` 或 `v3/` 时两个出口都要手验 |
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
6. **review 的交付物是断言，不是意见**：每条意见写成「断言 + 怎么验 + 错了会怎样」，写不成就不发。
   这既挡住 nitpick，也挡住「我觉得应该抽个基类」式建议。实证（2026-11-05 一整轮 LLM 改造）：
   净收益全部来自新增断言与假服务端到端，没有一条来自措辞建议；最贵的三次返工都是「没想出该测什么」。
7. **外部世界只信探针**：宿主 / 前端 / 网关 / 代理 / CI runner 的行为，不许靠读码或「应该会」下结论。
   用 `/history`（记录的是**实际提交的入参**）、`/object_info`、真页面、本地假服务。
   假服务端到端最便宜：几十行、零 token、几十秒，本轮靠它抓到两个真 bug（V3 校验缺失、
   通道误判），而 153 项单测与两轮读码都没抓到。

---

## 12 · 交付口径

- **每条结论带一条可复跑的命令**，不写"我试过可以"。
- **没跑的档点名**：`真机未跑` / `GPU 未跑` / `Windows 未跑`。
- **「已发布」只用于 registry `Active` + `latest_version=本版` + GitHub Release 在位**；
  `Pending`/`Flagged` 一律写「已提交，待安全复核」。
- 提交信息写**为什么**（哪条判据错了、什么现象），不只写改了什么。

> 本文件非规范源。若与 `CONTRIBUTING.md` 冲突，**以 `CONTRIBUTING.md` 为准**并回来修本文件。
