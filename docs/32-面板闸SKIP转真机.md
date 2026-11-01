# 32 · 面板闸 SKIP 转真机断言（接了线的参数不会被藏）

> 状态：**P3 已完成**（2026-11-01）。`node tools/ui_llm_panel.mjs` → **通过 43 / 失败 0 / SKIP 0**。
> 改前基线：36 通过 / 0 失败 / **1 SKIP**（同一项）。
> 本文记录「怎么造出真机条件」与逐条证据；不写本机路径。

---

## 1. 原来的 SKIP 与它错在哪

改前那一条：

```js
c.skip("接了线的参数不会被藏",
  "造不出真机条件：直接写 slot.link 被宿主立刻归零（seen=[null]）；"
  + "宿主自己会把已提升的 widget 行按 byConnection 抑制掉，代码里的 isLinked 只是防一手");
```

造条件的手法是把 `slot.link = 999001` 直接写进节点输入槽。实测 `seen=[null]`——
**这不是「宿主不允许接线」，是这个写法本身不合法**：`slot.link` 只是 litegraph slot 上
的缓存字段，真正的链接由图的 `links` 表 + 连接 API 维护；手写的 link id 不是一个链接
对象，宿主的图一致性检查（以及 `onConnectionsChange`）下一帧就把它清掉。

所以 SKIP 描述的是**造法失败**，不是能力不存在。

## 2. 根因：新前端里 widget 与输入槽是**共存**的

在活宿主（ComfyUI 0.43.0 / 前端 1.57.6）里实测：

- 节点的每个控件在 `node.inputs` 里**都已经有对应的输入槽**，
  槽上带 `widget: { name: "ctx_size" }`（`getSlotFromWidget(widget)` 能反查）；
- 输入槽上的 `link` 为 `null` 时控件正常显示；一旦有**真链**，宿主自己把控件行按
  `byConnection` 抑制（只保留接线点），而面板的 `widget.options.hidden` 是另一个旗标；
- 前端在 `convertToInput()` 处明确写着 *“Widget to socket conversion is no longer
  necessary, as they co-exist now.”*——也就是说不需要「把 widget 转成输入」，
  直接连到那个共存槽即可。

三件事因此必须分开判：

| 层 | 字段 | 谁负责 | 接线后的预期 |
|---|---|---|---|
| **面板隐藏** | `widget.options.hidden`（= `suppression.byExtension`） | 本仓面板 `plan()/syncWidgets()` | **必须 false**（`isLinked` 让它保持可见） |
| **宿主抑制** | `widget.connectionSuppressed`（= `suppression.byConnection`） | ComfyUI 前端 | true（控件被链接替代，这是宿主行为） |
| **存档** | `widgets_values` | 宿主 serialize | **逐位不变**（39 格） |

把这三层混成一个「不见了」的断言，就会得出「宿主不让测」的假结论。

## 3. 造条件：PrimitiveNode + 真链（思路 A）

`LiteGraph.createNode("PrimitiveNode")` 是前端内置节点，输出一个可编辑的原始值；
它的输出端可以直接连任意 widget 输入槽：

```js
const slot = node.inputs.find((i) => i.name === "ctx_size");
const p = LiteGraph.createNode("PrimitiveNode");
app.graph.add(p);
const link = p.connect(0, node, node.inputs.indexOf(slot));   // 走宿主连接路径
// 结果：slot.link 变成数字 link id，graph.links 增加一条，serialize() 能往返
```

实测这条链：

- `slot.link = 5`（数字，不是手写 999001）；
- `graph.links` 3 → 4；
- `serialize()` 的节点输入里带 `{"name":"ctx_size","widget":{"name":"ctx_size"},"link":5}`，
  链接表里有 `[5, primitiveId, 0, nodeId, 23, "INT"]`；
- `app.loadGraphData(serialize())` 之后 `slot.link` 仍是 5 —— **真链过存盘往返**。

`examples/llm-wash.json` 里的 MossPromptSmith 首次载入时 `ctx_size` 槽的 `link` 为
`null`，所以「接线前该被藏」这个对照条件是天然成立的，不需要造假。

## 4. 断言清单（7 条，替换原来的 1 条 SKIP）

| # | 断言 | 判定口径 | 实测 |
|---|---|---|---|
| 1 | 造出真机条件 | `primitive.connect` 返回链接对象、`slot.link != null`、`graph.links` +1 | `slot=5`，links `3→4` |
| 2 | 对照组成立（条件非空转） | 接线前默认视图里 `ctx_size` 与 `ubatch` 的 `options.hidden == true` | `{hidden:true, ubatchHidden:true}` |
| 3 | 接线后切「常用调优」不被藏 | 面板焦点换到 tune：`ctx_size.hidden == false`，未接线的 `ubatch.hidden == true` | `{hidden:false, ubatchHidden:true, suppressed:true}` |
| 4 | 切「外部 API」再折回默认仍不被藏 | 每次重算都尊重 `isLinked` | api / folded 两组均为 `hidden:false, ubatchHidden:true` |
| 5 | 重开工作流后真链还在 | `loadGraphData` 后 `ctx_size` 槽 `slot.link != null` | `slot.link=5` |
| 6 | 重开后 `widgets_values` 逐位一致 | 39 格逐位 JSON 比较，diff 为空 | `len=39 diff=[]` |
| 7 | 重开后已接线参数仍不被面板藏 | `hidden == false` 且 `connectionSuppressed == true`（宿主抑制如实记录）、面板挂回 | `{hidden:false, suppressed:true, mounted:true}` |

对应的「藏」既有方向也被钉住：对照参数 `ubatch` 在 tune/api/默认三种视图里都被面板
藏起来（`hidden:true`），说明第 3/4 条不是「面板干脆什么都不藏」的假绿。

失败时的真机证据打印：块内 `console.log("  真链证据 =", ...)` /
`console.log("  真链重开证据 =", ...)` 输出接线前后、三种视图、重开后的
`hidden / connectionSuppressed / slotLink / widgets_values diff`。

## 5. 证据（可复跑）

环境：ComfyUI 0.43.0（前端 1.57.6）在 `127.0.0.1:8188`；
Playwright 由 `tools/ui_kit.mjs` 自找（PWCORE / npm 全局 / npx 缓存 + 本机浏览器）。
依赖缺失时按 ui_kit 规矩 **exit 2**，不会静默跳过。

```bash
node tools/ui_llm_panel.mjs
```

实测结果（2026-11-01）：

```text
  [OK]   造出真机条件：PrimitiveNode 连到 ctx_size 的真链（走宿主连接路径，不是手写 slot.link）  {"slot":5,"links":[3,4]}
  [OK]   对照组成立：接线前默认视图里 ctx_size 与 ubatch 都被面板隐藏（条件非空转）  {"hidden":true,"ubatchHidden":true,"suppressed":false,"slotLink":null}
  [OK]   接线后切到「常用调优」：ctx_size 不被藏（hidden=false），未接线的 ubatch 照常藏  {"hidden":false,"ubatchHidden":true,"suppressed":true,"slotLink":5}
  [OK]   切到「外部 API」再折回默认：已接线参数仍然不藏（每次重算都尊重 isLinked）  {"api":{"hidden":false,...},"folded":{"hidden":false,...}}
  [OK]   重开工作流后真链还在（ctx_size 输入槽 slot.link 非空）  slot.link=5
  [OK]   重开后 widgets_values 逐位一致（39 格；接线与折叠都不写存档）  len=39 diff=[]
  [OK]   重开后已接线参数仍不被面板藏（hidden=false；宿主 byConnection 抑制控件本身是宿主行为，如实记录）
          {"slotLink":5,"hidden":false,"suppressed":true,"ubatchHidden":true,"len":39,"diff":[],"mounted":true}

结果：通过 43 / 失败 0
```

- 零 SKIP：结果行不再有 `[SKIP]`；改前是 36 通过 + 1 SKIP，本次 +7 条断言、少 1 条 SKIP；
- 证据文件（`docs/_local/` 已 ignore，不入库）：
  - 完整日志：`docs/_local/ui_llm_panel_linked.log`
  - 面板截图（本次运行刷新）：`docs/_local/panel-1-default.png` … `panel-6-reloaded.png`
- 其余 36 条断言逐条保持 `[OK]`（没有回归）。

## 6. 边界与未验证

1. **宿主行为不在本仓可控范围**：接线后控件行被 `byConnection` 抑制是 ComfyUI 前端
   设计（链接本身就是值）。本仓面板只保证「不额外改 `options.hidden`」，
   不保证「控件还能点」——那会和宿主的链接语义冲突。断言里把 `suppressed:true`
   写出来是为了让这条预期可见，不是把它当面板的功劳。
2. **只覆盖当前实测前端版本**（0.43.0 / frontend 1.57.6）。更老的前端若没有
   `PrimitiveNode` 或 widget/输入槽不共存，本用例会红（红在「造条件」那条），
   不会静默跳过——这正是要的效果。
3. **未覆盖**：子图（subgraph）promoted widget 的接线路径、组节点（group node）提升、
   多链扇入同一槽；这些是另一套 widget 提升机制，本轮没有造对应真机条件。
4. **未覆盖**：跨会话持久化（保存到用户工作流目录再开），本用例走的是
   `serialize()` → `loadGraphData()` 的内存往返；两者在这条路径上共用同一个配置实现，
   但「落盘到工作流文件」不是本轮验收项。
