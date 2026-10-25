# AGENTS.md — 本仓作业入口

给 AI / 新人的一页纸。改动前读完，改完按 §三 自检。

## 一、这个仓是什么

给 MOSS-Image 用的**布局**节点包。五个节点（LayoutEditor / Layout / Sampler / Crop / Paste），
两条 API 出口（V3 默认、V1 回退），零第三方依赖。

两种用法同一套机制：**宫格**（N 格 + 每格一段描述）与**单张**（元素框 + 文字块）。
几何永远从 `core/layout.py` 出；`core/editor.py` 只做 state ↔ 提示词的确定性编译。

## 二、铁律（每条都有事故来源）

1. **`nodes.py` 的 `INPUT_TYPES()` 是参数表的唯一真相源**。V3 schema 由 `v3/_compat.py`
   自动生成，**不许**在别处手写第二份。新 widget 只能**追加到末尾**——`widgets_values`
   是按位置存的，插在中间会让用户已有工作流的参数静默串位。
2. **V1 与 V3 必须互斥**。V3 模式下 `NODE_CLASS_MAPPINGS = None`（宿主判据含 `is not None`）；
   同时导出两者时 V1 先生效，V3 永不生效且完全静默。
3. **`seed` 类 INT 必须带 `control_after_generate`**。V1 下前端自动加，V3 下不声明就没有
   ⇒ 同一工作流在两条出口上差一个槽，后续参数整体错位。`smoke_nodes.py` 有断言。
4. **几何只算一次**。所有坐标从 `core.layout.Layout` 出，通过 `spec` 单线传递。
   不许任何节点自己再 `round()` 一遍坐标。
5. **不静默兜底**。尺寸不符、spec 不合、整除失败 —— 一律 raise 带操作指引的中文错误。
   唯一允许的"降级"是带出声的成功路径（例如解码尺寸不符时按解码结果重建几何，并写进 report）。
6. **画布与单格必须 16 的倍数**。对齐余量只加到右/下外边距，绝不改单格尺寸。
7. **i18n 只改显示，不改存档**：
   - 只碰 `label` / `title` / `tooltip` / `placeholder`，**绝不碰 `name` / `value` / `options.values`**；
   - 中文提示**只在 `nodes.py` 写一份**，`web/moss_sb_i18n.js` 的 `TIPS` 只存 `en`；
   - 用户自定义标题不许覆盖；只换默认名前缀、后缀保留；
   - 新增参数必须同时补 `TIPS` 英文条，否则 `test_contract.py` 会红；
   - 🔴 **多行文本框的标题是 `placeholder`，不是 `label`** —— DOM `<textarea>` 的父容器
     `size-full` 把画布自绘标题盖住了，只改 label 在界面上**看不到**（已真机踩到）。
     两个提示词框必须同时写 label + placeholder，`tests/test_i18n.mjs` 6.20–6.23 锁这条；
   - 🔴 **`negative` 必须保持 `advanced: True`** —— cfg=1 时宿主跳过负面分支
     （`comfy/samplers.py::sampling_function`），放普通区等于骗用户"写了会生效"。
     `test_contract.py::test_sampler_shape` 锁这条，别"好心"挪回去。
8. **文档分工不许混**：

   | 文件 | 写给谁 | 只许写 |
   |---|---|---|
   | `README.md` / `README_EN.md` | **开源用户** | 这是什么、怎么装、怎么用、参数表、已知边界、许可 |
   | `AGENTS.md`（本文） | AI / 维护者 | 铁律、改动闭环、命令、禁区、薄弱点 |
   | `CONTRIBUTING.md` | 外部贡献者 | 怎么跑机检、提交前必须过哪几条 |
   | `docs/NN-*.md` | 想深挖的人 | 机理、实测记录、几何数学 |

   **不许**把「四条铁律」「placeholder 为什么不是 label」「机检命令」「目录里每个文件干吗」
   这类开发内容写进 README —— README 里出现的每一段都必须是用户会做的动作或会遇到的边界。
   反过来，用户操作步骤也不许只写在 AGENTS 里。判据：**一个只用画布的用户，读完 README 能不能跑通**。
9. **仓库是公开的，读者只有代码，没有人**。文档、注释、提交信息里：
   - 不写内部沟通（谁提的、什么时候提的、讨论过程）；
   - 不写本地环境（盘符、用户名、家目录、其它机器的路径）—— 要举例就 `<ComfyUI>` / `/path/to/...`；
   - 不写情绪化的过程叙述；需要溯源时给**条件 + 结论**，不写叙事；
   - 结论必须区分「已验证 / 未验证」。
   判据：**交给一个陌生人，他能否只凭这些文字把事做对，且看不出这是谁的机器。**
   细则见 [`docs/05`](docs/05-local-development.md) §七。


## 三、一次改动的最小闭环

```bash
python tests/test_core.py              # 几何/提示词/像素，零 GPU，302 项
python tests/test_editor.py            # state v3 编译层 / 预设 / 错误路径 / 上限，零 GPU，102 项
python tests/test_interop.py           # elements-v1 ⇄ state v3 映射 / 往返 / CLI，零 GPU，83 项
python tests/test_contract.py          # 参数表/错误路径/依赖/卫生/词表/输入形状，零 GPU，130 项（--no-host 114 项）
python tests/test_cross_language.py     # JS ↔ Python 预设/校验/elements 差分，零 GPU，66 项
python tests/test_panel_assembly.py     # 逐格拼版 reborder 语义，零 GPU，5 项
python tests/test_sampling_contract.py # 采样 API 契约（latent 必须是张量），零 GPU，19 项
node   tests/test_i18n.mjs             # 中英切换纯函数，54 项（零浏览器）
python tools/smoke_nodes.py            # 自加载：注册 + schema + 纯节点真跑，41 项
python tools/host_loader_check.py      # 走宿主 load_custom_node，19 项
python tools/check_ui_workflow.py      # 示例连线 + widget 槽位逐位值域，4 示例 + 2 夹具共 88 项
python tools/check_frontend.py         # 前端词表/语法/接线/标题可换语言，9 项
python tools/check_editor_ui.py        # 编辑器前端：接线/语法/词表/互操作/层级对齐，93 项
```

CI 另跑 `release-preview`（`check_release_ready.py` + `make_dist.py --zip`，只构建/上传 artifact，不发布）；
`host` job 用 `make_dist.py --verify` 从干净副本独立加载并跑编辑器/宫格/裁格。

改了参数表还要重生成示例（槽位会变）：

```bash
python tools/make_example.py                                   # 机检用的最小图
python tools/make_ui_workflow.py                               # 交付用 UI 版（写宿主目录）
python tools/make_ui_workflow.py --out examples --bypass-lora   # 仓库内也存一份
python tools/make_edit_workflow.py --out examples               # 「只改一格」端到端示例
```

要 GPU + 权重的两条（**人工跑，不进 CI**）：

```bash
python tools/e2e_matrix.py             # 端到端 14 用例真跑（整张），逐条断言尺寸/缝色/不雷同
python tools/ui_smoke.mjs              # 可选诊断：当前环境无头浏览器无 WebGL，结论不可信
```

全部应是「失败 0」。**不要用 `| tail -n` 看这些脚本**——退出码会被吃掉。

三条不可替代：
- `host_loader_check.py` 调的是 ComfyUI 启动时扫 `custom_nodes` 的**同一个函数**，
  查的是 `nodes.NODE_CLASS_MAPPINGS` ——「配置写对 ≠ 宿主调你」的唯一证据。
- `e2e_matrix.py` 是唯一能证明"别人机器上也能出图且尺寸对"的东西；`--dry` 只能证明图能被提交。
- `test_contract.py` 的「每个参数都有英文提示」与「negative 必须 advanced」，
  是**新增参数漏翻译**和**把死参数挪回普通区**的唯一拦截点。

前端扩展是否真的被宿主登记，只能这样验（`WEB_DIRECTORY` 写错是**静默失效**）：

```bash
curl -s http://127.0.0.1:8188/extensions | tr ',' '\n' | grep MOSS-Storyboard
# 必须看到 /extensions/ComfyUI-MOSS-Storyboard/moss_sb_i18n.js 与 _ui.js
```

## 四、命令速查

| 想干什么 | 命令 |
|---|---|
| 只验几何数学 | `python tests/test_core.py` |
| 验 V1/V3 双出口 | `python tools/smoke_nodes.py` |
| 换宿主路径 | `COMFYUI_PATH=/path/to/ComfyUI python tools/smoke_nodes.py` |
| 强制用 V1 出口 | `MOSSSB_NODE_API=v1 python tools/smoke_nodes.py` |
| 打印某种格数的几何 | `python -c "from core import layout as L; print(L.build_layout(panels=9,panel_edge=512,panel_aspect='16:9',canvas_aspect='auto').spec())"` |
| 起宿主并等就绪 | `python /path/to/ComfyUI/main.py`（另开终端，等端口 8188 就绪） |
| 一次真跑交付工作流 | `python tools/ui_to_api.py "<workflow>.json" --run` |

## 五、禁区

- 不要在 `custom_nodes/` 里放第二份副本（会静默覆盖节点定义）。开发态请用目录联接。
- 不要把 `torch` 写进 `dependencies`（宿主按 CUDA 版本管它）。
- 不要在 `__init__.py` 的加载摘要外面加可能抛异常的初始化（摘要本身不许把包搞挂）。
- 不要凭感觉改 `requires-comfyui` 下限：MOSS 原生支持自 0.42.0 起，更早宿主会静默拿不到 `moss_image` 类型。

## 六、已知薄弱点

- 模板 img2img 与布局框的**成片效果都未量化评测**；本仓只保证几何与接线正确。
- `README_EN.md` 是**节选翻译**，没有节级 hash 同步闸（`en_sync.py` 那类工具未移植）
  ⇒ 改了中文 README 必须人肉看一眼英文版。这条是当前最大的文档漂移风险。
- CI（`.github/workflows/ci.yml`）只跑 ruff + CPU 单测 + 前端一致性；
  **`e2e_matrix.py` 要 GPU + 权重，不在 CI**，靠人跑。
- 前端"看得见"没有自动化闸：当前环境无头 chromium 无 WebGL，画布不渲染，`ui_smoke.mjs`
  的结论不可信（已降级为诊断）。现在靠控制台两行日志 + `MOSS_SB_LANG.where()` + 人眼。
