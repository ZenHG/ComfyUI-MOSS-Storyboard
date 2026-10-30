# CONTRIBUTING

## 一句话

改代码 → 跑下面这些闸 → 全绿再提 PR。**没有任何一道闸是可以"这次先跳过"的。**

```bash
python -m ruff check .                    # 静态检查（CI 第一道）
python tests/test_core.py                 # 313 项：几何 / 提示词 / 像素（零 GPU）
python tests/test_editor.py               # 149 项：state v3 编译层 / 预设 / 错误路径 / 上限 / 占位守卫（零 GPU）
python tests/test_interop.py              # 95 项：elements-v1 互操作 / 往返 / CLI / 宫格转单图（零 GPU）
python tests/test_extract.py              # 22 项：从散文/代码块提取 JSON、上限/扫描预算（零 GPU）
python tests/test_adversarial.py          # 63 项：恶意/极端输入（JSON 常量 / 原型链 / 量化 / 扫描预算 / spec 的 16 倍数与回读）
python tests/test_planner.py              # 27 项：brief → elements 模板 / 错误路径 / CLI（零 GPU）
python tests/test_skill_draft.py          # 79 项：官方 prompt-enhancer 草稿 / 编译 / state / CLI（零 GPU）
python tests/test_contract.py             # 149 项（--no-host，CI 口径）：参数表 / 错误路径 / 依赖 / 卫生 / 词表
python tests/test_cross_language.py       # 95 项：JS ↔ Python 预设/默认值/校验/elements/canvas/最终提示词/外接稿差分
python tests/test_panel_assembly.py       # 22 项：逐格拼版 reborder 语义 / 逐格 latent 契约 / 档位提示
python tests/test_sampling_contract.py    # 19 项：宿主采样 API 的入参类型
python tests/test_faceswap_wiring.py    # 36 项：换脸接线（桩 clip/vae/采样）—— 前缀 / 预算 / 抬格 / 裁贴 / denoise 语义
python tests/test_init_empty.py         # 18 项：init 两个分支 —— 空 latent 不碰源图 / 画布来源 / 忽略项写明 / 缺 image 报错
node   tests/test_i18n.mjs                # 54 项：中英切换纯函数
node   tests/test_compat.mjs              # 43 项：旧工作流 widgets_values 错位修复（长度/内容两种错位 + 时序）
python tools/smoke_nodes.py               # 63 项：自加载 + 纯节点真跑 + 分辨率告警按模式给对杠杆
python tools/host_loader_check.py         # 19 项：走宿主 load_custom_node
python tools/check_ui_workflow.py examples/*.json tests/fixtures/moss-storyboard-*.json   # 7 份共 105 项：连线 + 槽位（不带文件参数只查内建那一份 = 15 项）
python tools/check_frontend.py            # 19 项：前端词表/语法/接线/标题可换语言/**汉化完整**（参数标签 / 枚举取值 / 编辑器文案 / 节点显示名 / 中文 tooltip）
python tools/check_editor_ui.py           # 151 项：编辑器接线 / 语法 / 词表 / 互操作 / 层级对齐 / 最终提示词 / 占位守卫 / 滚轮跨平台守卫 / 旧稿错位守卫 / 界面文案中英齐全
python tools/check_release_ready.py       # 53 项：**发布前**开源卫生（凭据 / 两个公开面的路径 / 打包口径 / 元数据 / 发布说明 / 提交信息 / 内部称呼 / 黑名单是否随仓库走 / 二进制元数据 / 临时产物 / 提交格式 / SPDX / **文档清单与实物一致**：参数表覆盖全部入参、每个示例都被点名、示例引用的图要么在仓库要么写明自备、报错表原文逐字可查）
node   tools/ui_smoke.mjs                 # 31 项：**无头浏览器真机**（扩展是否执行 / 只动本包节点 / 编辑器语义分层 / 预览可见 / 占位确认）
node   tools/ui_widget_compat.mjs         # 14 项：**真机**旧稿错位修复（对齐稿不动 / 错位稿归位 / **刷新后**仍不红 / 存出去再打开）
node   tools/ui_wheel_check.mjs           # 每引擎 21 项：滚轮归属（节点内只滚编辑器、画布纹丝不动、不接管原生滚动），Chromium/Firefox/WebKit 各跑一遍
```

三个 `ui_*.mjs` 都是**真机**（要跑着的 ComfyUI，默认 `http://127.0.0.1:8188`，`--url` 可改）+ 本机 playwright-core 与 chromium。
公共底座是 `tools/ui_kit.mjs`（找依赖 / 起浏览器 / 等 `graph`+`canvas` 就绪 / 载入 / **刷新** / 快照 / 截图 / 计数），
用例脚本只写"验什么"——新增用例照 `tools/ui_widget_compat.mjs` 抄即可。
`PWCORE=`、`CHROME=` 可显式指定依赖；**缺依赖返回 2、断言失败返回 1**（"跳过了"不能混进"通过了"）。
`ui_smoke` 只读界面、**不排 GPU 任务**（排队确认框一律 stub 成"取消"）。CI 里没有宿主，所以这三道闸只在本地/发布前跑。

`ui_wheel_check.mjs` 同样需要活宿主，另加三个引擎的浏览器（`playwright install chromium firefox webkit`）。
它自带**负对照**：临时摘掉 `.mossed-root` 标记后，同一位置必须变成缩放画布——否则说明"画布没动"只是
滚轮压根没送到（假绿）。`--engine all` 一把跑三引擎，`--json` 记账。

CI 的 `release-preview` job 会构建干净分发包（`tools/make_dist.py --zip`，只上传 artifact，不发布）；
`host` job 会从干净副本 `--verify` 独立加载并跑编辑器/宫格/裁格。

`smoke_nodes.py` / `host_loader_check.py` / `check_ui_workflow.py` 需要宿主：
设 `COMFYUI_PATH=/path/to/ComfyUI`，或写进仓库根的 `.comfyui_path`（本地文件，已忽略）。

端到端（要 GPU + 权重，不进 CI）：

```bash
python tools/make_gallery.py   # 8 组预设提示词（换机器复现用）
python tools/e2e_matrix.py     # 16 用例真跑（含逐格 layout / plain）
```

> 发布前：全矩阵（16 例）至少跑一次；只改编辑器时可 `--only 逐格` 或 `--only 4格` 抽查，
> 部分运行不重写端到端报告（要更新报告请跑全矩阵）。

> ⚠️ **别用 `| tail -n` 跑这些脚本** —— 管道会把退出码吃掉，你会看到"通过"却拿到 0。

## 六条硬规矩

1. **`nodes.py` 的 `INPUT_TYPES()` 是参数表唯一真相源**。V3 schema 由 `v3/_compat.py` 自动生成，
   不许在别处手写第二份。**公开面有两处引用它**：`docs/19-node-manual.md`（全表）与 README 的摘要 ——
   改参数要两处一起改，`S18` 会核「每个键都在文档里出现过」。新 widget 只能**追加到末尾** —— `widgets_values` 按位置存，
   插在中间会让用户已有工作流的参数静默错位。
2. **`seed` 类 INT 必须带 `control_after_generate`**。V1 下前端自动加、V3 下不声明就没有
   ⇒ 同一工作流在两条出口上差一个槽，后面全部串位。`smoke_nodes.py` 盯着这条。
3. **几何只算一次**（`core/layout.Layout`），通过 `spec` 单线传给下游。
   任何节点都不许自己再 `round()` 一遍坐标。
4. **不静默兜底**。尺寸不符 / spec 不合 / 整除失败一律 raise 带操作指引的中文错误。
   唯一允许的"降级"是出声的（写进 `report`）。
5. **i18n 只改显示，不改存档**：只碰 `label`/`title`/`tooltip`，
   绝不碰 `name`/`value`/`options.values`；中文提示只在 `nodes.py` 写一份，
   `web/moss_sb_i18n.js` 的 `TIPS` 只存 `en`。新增参数忘了补英文 → `test_contract.py` 红。
6. **用户可见的同一句话有三份，改一处必须改三处**：`nodes.py` 的中文 tooltip、
   `web/moss_sb_i18n.js` 的 `en` 覆盖、README 的对应段落。
   0.3.2 里 `mode`（说「逐格更一致」）与 `sample_edge`（说「2.0 = 2 倍」）的英文
   与实测和中文**相反**，就是只改了中文留下的 —— 闸查不到语义冲突，只能靠这条纪律。

## 文档里的数字与清单不要手写

能被脚本核对的东西，就别靠人读：

- **参数表**必须覆盖 `INPUT_TYPES` 的每个键（README 里以 `名字` 出现）；
- **示例清单**：`examples/*.json` 每一个都要在 README 里被点名；
- **示例依赖图**：仓库里没有的图，README 必须写明「自备」；
- **报错原文**：README 声称逐字抄，`S18` 就按源码逐字核（占位符归一，符号不归一 ——
  `×` 和 ASCII `x` 是两回事）。

这四条都在 `tools/check_release_ready.py` 的 S18 里。写文档前先跑一遍，比事后人眼找快得多。

## 改完要同步的项数

断言数散在 2 个公开文件里，改测试就必须一起改，否则下一个人会被旧数字误导：

- 本文件上面的命令注释
- 仓库里的 CI 配置（`.github/workflows/ci.yml`，不随发布包分发）文件头的期望值

`CHANGES.md` 里已发布版本的历史数字不动。

## 测试纪律

- **跨语言差分必须断言条数守恒**：JS runner 少返回结果时，`zip` 会把两侧静默截断成"全绿"。
  `test_cross_language.py` 已用「readState 结果数量」封住；新增 runner 照此办理。

## PR 期望

- 一个 PR 只做一件事。修 bug 别顺手重构。
- 提交信息说清**为什么**（哪条判据错了、什么现象），不只是"改了什么"。
- 涉及行为变更的，请贴一条**可复跑的命令**或一张产物，而不是"我试过可以"。
- 不接受把断言改松（改判据可以，改数据不行）。真发现闸本身错了，请在 PR 里说明
  是"假红"还是"假绿"，并补一条新断言防回归。

## 发布

维护者发布走 `tools/release.py`（GitHub + Comfy Registry 双渠道，带交叉验证）。
步骤、凭据纪律与失败处置见 [`RELEASING.md`](RELEASING.md)。
