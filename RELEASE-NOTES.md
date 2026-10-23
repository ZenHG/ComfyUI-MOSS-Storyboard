# 发布说明（Release Notes）

> **这份文件写给用户，不是写给开发者。**
> **两个渠道的用户可见说明都取这里**（唯一真相源）：Comfy Registry 的 changelog 与 GitHub Release 的正文。
> 开发者细节（实现方案、实测证据、发布过程）留在 [`CHANGES.md`](CHANGES.md)，**不要往这里搬**。
>
> 格式规矩（正文见 [`RELEASING.md`](RELEASING.md) §6）：
>
> - 一版一节，**新的在最上面**，标题固定 `## x.y.z — YYYY-MM-DD`。
> - 每节**必须中英双语**：中文在前，一行 `<!-- EN -->` 分隔，英文在后。
>   英文**独立成文**（不是逐句直译 —— registry 页面上大部分读者只看英文）。
> - 只写**用户能感知的变化**。有行为变化必须用 🔴 点明「要不要动配置」。
> - 节内长度**分段算**：**中文段 ≤700 字符 · 英文段 ≤1700 字符**
>   （`tools/check_release_ready.py` 与 `tools/release.py` 都按这个口径拦）。
> - 每节结尾固定两行：中文 `**升级动作**：…` / 英文 `**Action required**: …`。
>
> 为什么要有它：registry 的 changelog **发布后不可改**（版本接口只收 `GET`/`OPTIONS`），
> 所以唯一的防线是**发布前把它写对**。

---

## 0.1.2 — 2026-10-23

**去掉了一个选了必然失败的选项：图生图。**

- 🔴 「初始化方式」从五个选项收成三个：`自动（纯布局）/ 纯布局 / 原样提示词`。
  原来的「仅模板」「布局 + 模板」已删除——MOSS 不以参考图条件化（官方已声明不支持编辑），
  实测这三组参数只会得到纯黑或模板残影，留着只会让你白跑一次。
- 采样器不再有「模板图」输入。起点永远从纯噪声开始。
- 如果你之前接过模板线：工作流还能打开，但那根线会失效，重新生成一次工作流即可。
- 另外补一条实测：官方非蒸馏配方（50 步 / cfg 3）在我们机器上跑出来是崩坏扭曲的画面，
  原因未确认，有人能跑通。想试请先出单张小样，别直接批量跑。

**升级动作**：从 Manager 更新到 0.1.2。建议重新生成一次示例工作流，避免旧的模板线残留。

<!-- EN -->

**Removed an option that could only fail: img2img.**

- 🔴 The `init` menu went from five choices to three: `auto (layout) / layout / plain`.
  "Template only" and "layout + template" are gone. MOSS is not conditioned on reference images
  (the official limitation says no editing), and every measured parameter set produced either a
  black frame or template ghosting — keeping it only cost you a wasted run.
- The sampler no longer has a template input; the latent always starts from pure noise.
- If you had a template wire connected: the workflow still opens, but that wire becomes inert.
  Regenerate the workflow once to clean it up.
- Also documented: the official non-distilled recipe (50 steps / cfg 3) produced corrupted frames
  on our machine. Cause unconfirmed, others report it works — sample one image before running a batch.

**Action required**: Update to 0.1.2 from the Manager. Regenerating the example workflow is
recommended so the old template wire does not linger.


## 0.1.1 — 2026-10-23

**修一个只有装完才会发现的问题：从 Manager 安装的包里，示例工作流不在。**

- 0.1.0 的示例工作流用了中文文件名，发布打包时被跳过 —— 于是 `examples/` 下那个带分组和说明的工作流装完找不到。已改成英文文件名。
- 功能没有任何变化。

**升级动作**：从 Manager 更新到 0.1.1 即可；已手动放过示例的不用动。

<!-- EN -->

**Fixed an issue only visible after installing: the example workflow was missing from the Manager package.**

- The 0.1.0 example used a non-ASCII filename, which the packer silently skipped — so the annotated workflow under `examples/` never arrived. Renamed to ASCII.
- No functional changes.

**Action required**: update to 0.1.1 from the Manager. If you already placed the file manually, nothing to do.

## 0.1.0 — 2026-10-23

**MOSS-Image 的故事板宫格节点，首个版本。**

- 一次生成**一张画布**，分成 3 / 4 / 6 / 8 / 9 格，同时把每一格单独裁出来。
- 每格写一句描述（`[PANEL 1]` …），节点会转成 MOSS 原生的布局提示词，锁住每格画什么。
- 宫格线由节点在生成后按坐标重绘，不会歪、不会断口。
- 每个本包节点上有「中 / EN」按钮，可切换中英文；只改显示，**不动你已保存的工作流**。
- 支持 10 种单格比例（16:9、9:16、1:1 等），画布尺寸自动算，宽高自动对齐 16。

**这一版的两条限制**

- 用模板图做初始化（`init=template` / `both`）得不到有效重绘：会得到模板残影或纯黑。请用默认的纯布局方式。
- 步数很低时「降噪强度」可能不生效：4 步时调到 0.85 实际仍是 1.0。节点会在 report 里打印实际值。

**升级动作**：首次安装 —— 重启 ComfyUI 后刷新浏览器页面；`CLIPLoader` 的 `type` 必须选 `moss_image`。

<!-- EN -->

**Storyboard grid nodes for MOSS-Image — first release.**

- One run produces a **single canvas** split into 3, 4, 6, 8 or 9 panels, plus each panel cropped out.
- Write one line per panel (`[PANEL 1]` …); the node turns them into MOSS's native layout prompt so each panel knows what to draw.
- Grid lines are redrawn by the node after generation — they never wobble or break.
- Every node in this pack has a Chinese / EN toggle; display-only, **your saved workflow is untouched**.
- Ten panel ratios (16:9, 9:16, 1:1 and more); the canvas size is computed and 16-aligned for you.

**Two limits in this version**

- Template initialisation (`init=template` / `both`) does not produce a usable repaint — you get ghosting or a black image. Use the default layout mode.
- At very low step counts the denoise strength may be ignored: at 4 steps, 0.85 is effectively 1.0. The node prints the effective value in its report.

**Action required**: first install — restart ComfyUI and hard-refresh the browser; the `CLIPLoader` type must be `moss_image`.
