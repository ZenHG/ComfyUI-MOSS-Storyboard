# RELEASING

两条发布渠道，**顺序不能反**：先 GitHub（提供 raw 直链与 tag），再 Registry（读 pyproject 的 Icon/Banner 直链）。

## 0. 发布前自检（缺一条就别发）

```bash
python -m ruff check .                       # 必须 All checks passed
python tests/test_core.py                    # 282/0
python tests/test_contract.py                #  54/0
python tests/test_sampling_contract.py       #  18/0
node   tests/test_i18n.mjs                   #  46/0
python tools/smoke_nodes.py                  #  25/0
python tools/host_loader_check.py            #  16/0
python tools/check_frontend.py               #   9/0
python tools/check_ui_workflow.py            #  30/0（2 个示例）
python tools/e2e_matrix.py                   #  14/14（要 GPU + 权重，人工跑）
```

还要人工确认三件机检看不到的事：

1. **打开工作流看界面**：中/EN 按钮能切、悬浮提示跟着切、格数改了画布跟着变。
2. **英文界面下截图**一张，确认没有中文残留（`check_frontend` F1 只查词表，查不到渲染）。
3. `pyproject.toml` 的 `PublisherId` 已填真值（当前是 `TODO-set-before-publish`）。

## 1. 渠道一：GitHub

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin main --tags
gh release create v0.1.0 --title v0.1.0 --notes-file <(sed -n '/## 0.1.0/,/^## /p' CHANGES.md | head -n -1)
```

要点：

- **tag 必须在 main 上**。Registry 的 Install 走 git，tag 指向别的分支会让用户拿到半成品。
- 推送后**回到 registry 页面确认 Icon/Banner 不是空图** —— 直链要等文件真到 main 才有效。
- GitHub 仓库需开 **Private vulnerability reporting**（见 `SECURITY.md`），否则安全报告没有私密渠道。

## 2. 渠道二：Comfy Registry

```bash
# 令牌只放一个文件，别进 shell 历史、别进提交（见用户级 MEMORY 的凭据纪律）
comfy --skip-prompt --no-enable-telemetry env
comfy node publish
```

要点：

- `name`（= `moss-storyboard`）**发布后不可改**，它同时是安装 URL 的一部分。
- `PublisherId` 与 H 仓同属一个发布者时可以复用；不同发布者必须各自注册。
- 首发后 registry 会保留所有版本快照；**改版本号才叫新版本**，同一版本号重复发布会被拒。
- 发完在网页上验三件：节点清单有 3 个、分类是 `moss/storyboard`、`requires-comfyui` 显示 `>=0.42.0`。

## 3. 回滚

| 场景 | 动作 |
|---|---|
| 只是描述/文案错 | 改文件 → 补丁版本（0.1.1）→ 重发，**不要改已发布版本** |
| 有功能性错误 | 立刻发 0.1.x 修复；README 顶部加醒目提示 |
| 包本身有问题（装载崩） | 在 registry 上把该版本标记为不可安装（或发 0.1.x 覆盖提示），GitHub 侧把 tag 保留但 Release 标注 breaking |
