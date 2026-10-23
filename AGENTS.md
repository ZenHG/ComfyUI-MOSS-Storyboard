# AGENTS.md — 本仓作业入口

给 AI / 新人的一页纸。改动前读完，改完按 §三 自检。

## 一、这个仓是什么

给 MOSS-Image 用的故事板宫格节点包。三个节点（Layout / Sampler / Crop），
两条 API 出口（V3 默认、V1 回退），零第三方依赖。

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
7. **i18n 只改显示，不改存档**（抄 H 仓铁律）：
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

## 三、一次改动的最小闭环

```bash
python tests/test_core.py           # 几何/提示词/像素（零 GPU）
python tools/smoke_nodes.py         # 自加载：注册 + schema + 纯节点真跑
python tools/make_example.py        # 改了参数表就重生成示例（槽位会变）
python tools/check_ui_workflow.py   # 示例连线与 widget 槽位
python tools/host_loader_check.py   # 走宿主 load_custom_node —— "宿主真的调你"的唯一证据
node   tests/test_i18n.mjs          # 中英切换纯函数（零浏览器）
```

前端扩展是否真的被宿主登记，只能这样验（`WEB_DIRECTORY` 写错是**静默失效**）：

```bash
curl -s http://127.0.0.1:8188/extensions | tr ',' '\n' | grep MOSS-Storyboard
# 必须看到 /extensions/ComfyUI-MOSS-Storyboard/moss_sb_i18n.js 与 _ui.js
```

五条都必须「失败 0」。**改了 `INPUT_TYPES()` 必须跑中间两条**，否则示例会静默错位。
**任何"部署完就好"的结论必须由最后一条背书**：它查的是 `nodes.NODE_CLASS_MAPPINGS`。

## 四、命令速查

| 想干什么 | 命令 |
|---|---|
| 只验几何数学 | `python tests/test_core.py` |
| 验 V1/V3 双出口 | `python tools/smoke_nodes.py` |
| 换宿主路径 | `COMFYUI_PATH=D:/ComfyUI python tools/smoke_nodes.py` |
| 强制用 V1 出口 | `MOSSSB_NODE_API=v1 python tools/smoke_nodes.py` |
| 打印某种格数的几何 | `python -c "from core import layout as L; print(L.build_layout(panels=9,panel_edge=512,panel_aspect='16:9',canvas_aspect='auto').spec())"` |

## 五、禁区

- 不要在 `custom_nodes/` 里放第二份副本（会静默覆盖节点定义）。开发态请用目录联接。
- 不要把 `torch` 写进 `dependencies`（宿主按 CUDA 版本管它）。
- 不要在 `__init__.py` 的加载摘要外面加可能抛异常的初始化（摘要本身不许把包搞挂）。
- 不要凭感觉改 `requires-comfyui` 下限：MOSS 原生支持自 0.42.0 起，更早宿主会静默拿不到 `moss_image` 类型。

## 六、已知薄弱点

- 模板 img2img 与布局框的**成片效果都未量化评测**；本仓只保证几何与接线正确。
- `README_EN.md` 没有节级 hash 同步闸（H 仓那套 `en_sync.py` 未移植）。
- 没有 CI；四条命令目前靠人跑。
