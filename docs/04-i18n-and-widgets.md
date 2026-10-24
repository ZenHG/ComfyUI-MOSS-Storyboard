# 中英双语与控件机理（开发向）

> 这是**实现文档**。用户只需要知道「每个本包节点上有中/EN 按钮」——见
> [`README.md` §节点中英切换](../README.md#节点中英切换)。
> 本文写的是：词表分几层、为什么这么分、哪几处是真机踩出来的坑。

---

## 一、分四层，各管一件事

| 层 | 谁负责 | 规则 |
|---|---|---|
| 节点标题 | `web/moss_sb_i18n.js::NODES` | 后端 `NODE_DISPLAY_NAME_MAPPINGS` 存**英文**；中文由前端补。只换**前缀**，用户自定义标题与后缀一律保留 |
| 参数标签 | `LABELS` / `LABELS_EN` | 中文只在 `LABELS`；英文优先 `LABELS_EN`，再回落 widget 原名 |
| 多行文本框标题 | `LABELS` → `element.placeholder` | ⚠ **不是 `label`**，见 §三 |
| 枚举显示 | `COMBOS` + `options.getOptionLabel` | **只改显示**，存档值不变；白名单外不翻 |
| 悬浮提示 | `TIPS`（**只存 `en`**） | 中文唯一源是 `nodes.py` 的 `tooltip`；切中文时**一个字节都不写**，前端回落后端原文 |

`LABELS_EN` 只放「英文原名比技术名更难读」的少数几个（`control_after_generate` →
`after generate`、`panel_edge` → `panel long edge`）。其余英文直接用参数名，不另造词——
多一份词表就多一处漂移。

---

## 二、四条铁律（每条对应一次事故）

1. **只改 `label` / `title` / `tooltip` / `placeholder`，绝不改 `name`**
   —— `name` 是后端取参与序列化的依据，改了就是另一个参数。
2. **绝不改 `widget.options.values`**
   —— 那是存档值（模型文件名、枚举值），翻了就是改图。
3. **用户的自定义标题不许覆盖**，且只替换默认名前缀
   —— 与其它模块追加在标题后的提示共存。默认名有三种形态：`zh` / `en` / `legacy`
   （后端显示名，老工作流里存的就是它），三者都算"默认"。
4. **中文只写一份**（后端）。词表里多一份 `zh` 必然与后端漂开——历史上就是这样漂的。

第 4 条的原理：`w.tooltip` 只是一次性覆盖，序列化只写 `widget.value`，
所以提示翻不了图、分享出去也不污染别人的工作流 JSON。反过来，如果把中文也存进 JS，
`nodes.py` 改了提示而 JS 忘了改，界面上就是旧文案，且没有任何机检能发现。

---

## 三、🔴 多行文本框的标题是 `placeholder`，不是 `label`

**真机实证（前端 1.57.1）**：`widget.label` 已经是「正面提示词」，界面上仍显示
`placeholder="positive"`。dump DOM 才看清：

```html
<textarea class="comfy-multiline-input h-full w-full"
          data-testid="dom-widget-textarea" placeholder="positive"></textarea>
```

父容器是 `div.dom-widget.size-full`，把画布自绘的标题整个盖住 ⇒
**前端根本不画这两个框的 label，用户看到的标题就是 `placeholder`**。

所以提示词框必须**同时**写 `label` 与 `placeholder`；只写 label 的改法对它们永远无效。
切英文时要能回滚——原始 `placeholder` 存在 `w.__mosssbPlaceholder`（内存标记，不落盘）。

`tests/test_i18n.mjs` 6.20–6.23 锁这条（含"外来节点零改动"）。

---

## 四、只作用于本包节点

`applyLang()` 第一行就是 `if (!NODES[node.type]) return`；`mountBtn()` 同样判类型。
两处都在**库内部**拦，不写在调用方——因为 `nodeCreated` 对所有节点都触发，调用方漏判就会
把别人的 `steps` / `cfg` 的 label 改成我们词表里的中文（同名参数在别人那里语义可能不同）。

单测 6.16–6.19 用 `KSampler` 做靶子：切中/英后标题、label、tooltip、枚举钩子**全部原样**。

---

## 五、为什么 `negative` 默认折叠

`negative` 在 `INPUT_TYPES` 里标了 `advanced: True`，界面上收进节点的「高级」折叠区。

- MOSS 实测配方是 `cfg = 1.0`；
- 宿主在 cfg=1 时**直接跳过负面分支**：
  `comfy/samplers.py::sampling_function` →
  `if math.isclose(cond_scale, 1.0) and not disable_cfg1_optimization: uncond_ = None`；
- ⇒ 那段 conditioning **根本不进计算图**。

放在普通区只有一个后果：让人以为"写了就会生效"。所以折叠，但**不删**——
`cfg > 1` 时它才进计算图，而官方模型那一档（30–50 步 / cfg 3–5）正是这一档；
默认交付档是叠 LoRA 的 4–8 步 / cfg 1.0，所以默认永远用不到它，但通路留着
（见 README「这一版做不到的」里那张两档表）。

`tests/test_contract.py::test_sampler_shape` 锁这条：`negative.advanced` 必须是 `True`，
`positive` 必须可见。别"好心"挪回去。

---

## 六、抗覆盖：与界面汉化插件抢字段

装了 ComfyUI-Chinese-Translation / Global Translation 一类插件时，它有一个**每 1 秒的守护轮询**，
会按自己的词典把节点标签/提示改回去，且不对英文界面做屏蔽。

本包对策：写值时记下期望值（`__mosssbWant` / `__mosssbWantTip` / `__mosssbWantPh`），
`guard()` **每 1.5 秒**比对写回。三个字段都覆盖：`label`、`tooltip`、`placeholder`。

⚠ 这些标记只是内存属性，不参与序列化。

---

## 七、启动路径：不能只靠 `setup()`

真机踩到的两件事：

1. **`setup()` 可能不被调用**（前端 1.57.1）。模块能执行、`registerExtension` 也调了，
   但 `setup` 不下发 ⇒ 按钮永远不出现。改为模块自启动 `bootstrap()`：
   轮询到 `document.body` + `app.graph` 就启动，`setup` / `nodeCreated` 只作加速。
2. **载入工作流不走 `nodeCreated`**。载入后 10 个节点里本包 2 个 `hasBar=false`、标题还是空串。
   ⇒ `guard()` 里补 `applyAll()` + `mountAllBtns()`（幂等，靠 `__mosssbBar` 去重）。

---

## 八、排障：三行控制台

```
[MOSS SB] i18n: 模块已加载（v0.1.0）        ← 模块执行了
[MOSS SB] i18n: 就绪（lang=zh，设置项=有，节点=3）  ← bootstrap 成功
```

| 现象 | 判据 |
|---|---|
| 只有第一行 | `bootstrap()` 抛错，看控制台的 `启动异常` 与堆栈 |
| 两行都没有 | 前端扩展没被加载：`curl -s http://127.0.0.1:8188/extensions \| grep MOSS-Storyboard` |
| 都有但看不到按钮 | 控制台敲 `MOSS_SB_LANG.where()`：`button:true` 是遮挡，`false` 是挂载失败 |
| 改了代码没效果 | **先确认 8188 服务的是哪一份安装**，再谈生效 |

`MOSS_SB_LANG` 是排障入口（`get/set/apply/nodes/graph/loadGraph/where`），
只读用途，业务代码别依赖。

**升级后必须刷新浏览器页面（F5）**。重启 ComfyUI 进程不算：前端是单页应用，
页面打开后扩展 JS 就常驻内存，不刷新 = 还在跑旧版。

---

## 九、没有前端也能用

所有文案的后端版本是**英文**（`NODE_DISPLAY_NAME_MAPPINGS` + `DESCRIPTION`），
JS 加载失败只是没有中文，不会出现空标题或报错。
API 提交（`/prompt`）不经过画布 ⇒ 拿不到按钮，也不影响任何功能。
