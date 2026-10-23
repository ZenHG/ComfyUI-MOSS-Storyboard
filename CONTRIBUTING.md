# CONTRIBUTING

## 一句话

改代码 → 跑下面 7 道闸 → 全绿再提 PR。**没有任何一道闸是可以"这次先跳过"的。**

```bash
python -m ruff check .                    # 静态检查（CI 第一道）
python tests/test_core.py                 # 282 项：几何 / 提示词 / 像素（零 GPU）
python tests/test_contract.py             # 58 项：参数表 / 错误路径 / 依赖 / 卫生 / 词表
python tests/test_sampling_contract.py    # 18 项：宿主采样 API 的入参类型
node   tests/test_i18n.mjs                # 46 项：中英切换纯函数
python tools/smoke_nodes.py               # 25 项：自加载 + 纯节点真跑
python tools/host_loader_check.py         # 16 项：走宿主 load_custom_node
python tools/check_ui_workflow.py         # 15 项/文件：工作流连线 + widget 槽位
python tools/check_frontend.py            # 9 项：前端词表/语法/接线/标题可换语言
python tools/check_release_ready.py       # 26 项：**发布前**开源卫生（凭据/打包/元数据/发布说明）
```

后三条需要宿主：`COMFYUI_PATH=/path/to/ComfyUI`（默认 `I:/ComfyUI`）。
端到端（要 GPU + 权重，不进 CI）：`python tools/e2e_matrix.py`。

> ⚠️ **别用 `| tail -n` 跑这些脚本** —— 管道会把退出码吃掉，你会看到"通过"却拿到 0。

## 五条硬规矩

1. **`nodes.py` 的 `INPUT_TYPES()` 是参数表唯一真相源**。V3 schema 由 `v3/_compat.py` 自动生成，
   不许在别处手写第二份。新 widget 只能**追加到末尾** —— `widgets_values` 按位置存，
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

## 改完要同步的三处数字

断言数写在这三个地方，改了测试就必须一起改，否则下一个人会被旧数字误导：

- 本文件上面的命令注释
- `.github/workflows/ci.yml` 文件头的期望值
- `README.md` §九 的命令清单

## PR 期望

- 一个 PR 只做一件事。修 bug 别顺手重构。
- 提交信息说清**为什么**（哪条判据错了、什么现象），不只是"改了什么"。
- 涉及行为变更的，请贴一条**可复跑的命令**或一张产物，而不是"我试过可以"。
- 不接受把断言改松（改判据可以，改数据不行）。真发现闸本身错了，请在 PR 里说明
  是"假红"还是"假绿"，并补一条新断言防回归。
