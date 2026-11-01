# 交叉复核 · Registry 跟进工具与操作单（task-48）

> 2026-11-01。独立交叉复核（作者不是本复核人）；对象：task-47 的 `tools/registry_status.py`、
> `tests/test_registry_status.py`、`docs/60-Registry跟进操作单.md`、`docs/52` 的跟进改动。
> 写入范围只有本文件与 `out/` 临时脚本；**未修改任何作者文件**，未 commit。
> 证据分级：**A**=本机/真机实测 ｜ B=静态复核 ｜ D=未验证。

---

## 0. 结论

**FAIL（1 个必修：docs/60 对工具失败落盘行为的陈述与实测不符；工具核心行为其余全部 PASS）**

| 面 | 结论 |
|---|---|
| 作者单测 / ruff | **PASS**：`tests/test_registry_status.py` **41/41 OK**；`ruff` 干净 |
| 退出码语义（Active=0 / Flagged=Pending=2 / Banned=3 / 未知或取数失败=4） | **PASS**（注入 26 项断言全过，见 §2） |
| 注入错误路径（坏 JSON / 网络错 / 非 200 / 节点级 Banned / 未知状态） | **PASS** |
| 真跑 `snapshot` + `check --expect-latest-active` | **PASS**：快照落盘 5 文件；当前真实状态 Flagged → **exit 2**；`meta/summary` 无本机路径 |
| 与 docs/60 数字对账 | **PASS**（节点 Active、0.3.3 Flagged、10/10 Flagged、status_reason 键缺失、install 200、exit 2、attempts 1/1/1；downloads 不稳定如实标注） |
| 零第三方依赖 | **PASS**（AST 扫 import 仅标准库） |
| docs/60 英文联络稿 | **PASS**：无本机路径/用户名/凭据；占位符保留 |
| docs/60 外部事实来源 | **PASS**：每条指向 `docs/52` 小节 / `out/registry_status/` 快照 / 官方 API；support 与 Registry 页面 HEAD 200 |
| docs/52 改动自洽 | **PASS（含 1 条精度观察）**：顶部跟进与 `docs/60` 一致；§1.1 仍保留旧样例 `status_reason: null`（见 F2） |
| **docs/60 §7 的「失败不留半截文件/目录」** | **FAIL**：IO 中途失败会留下已写好的文件与快照目录（见 F1，可复现） |

---

## 1. 作者测试 / 静态（A）

```
python tests/test_registry_status.py   → Ran 41 tests — OK（复跑，不是引用作者输出）
ruff check tools/registry_status.py tests/test_registry_status.py → All checks passed
```

- 工具只 import 标准库（AST 扫描：`argparse/dataclasses/datetime/json/os/pathlib/sys/time/typing/urllib`），
  **零第三方依赖**；UA/端点常量与 docs/60 §7 一致。
- 无 `--expect-latest-active` 时为**探测模式**：Flagged 也 exit 0；加断言才按状态判 2/3/4（实测见 §2）。

---

## 2. 注入错误路径（A，独立脚本 `out/review_registry_inject.py`，未改作者测试）

用 `tools.registry_status.main(argv, opener=..., sleep=...)` 注入假取回器（作者明确支持的注入点），
**26 项断言全部 PASS**：

| 注入 | 期望 | 实测 |
|---|---|---|
| 最新版本 Active / Flagged / Pending / Banned | 0 / 2 / 2 / 3 | ✅ 全中 |
| 节点级 `NodeStatusBanned`（最新版本 Active） | 3（节点级优先） | ✅ 3 |
| 最新版本状态未知（`NodeVersionStatusDeleted`） | 4 | ✅ 4 |
| 缺 `latest_version.version` | 4 + 跳过 install + 警告 | ✅ 4；只请求 node/versions 两轮 |
| 坏 JSON（持续 3 次） | 4，重试 3 次 | ✅ 4；calls=3（默认 retries=2） |
| 网络错误（`URLError`，持续） | 4，重试 3 次 | ✅ 4；calls=3 |
| HTTP 404 | 4，**不重试** | ✅ 4；calls=1 |
| HTTP 500 | 4，重试后失败 | ✅ 4；calls=3 |
| 探测模式（无 `--expect`）Flagged | 0，结论「不判定发布门槛」 | ✅ 0 |
| `snapshot` 成功 | exit 0；5 文件；meta `exit_code_if_checked=2`；无盘符 | ✅ 全部 |
| `snapshot` 取数失败（持续网络错） | 4；**不创建快照目录** | ✅ 目录不存在 |
| `snapshot` 中途 IO 失败（第 2 次写盘抛 OSError） | 4；错误明确「io / 不是 Registry 状态结论」 | ✅ exit 4；**但留下 `node.json` + 快照目录（F1）** |

---

## 3. 真跑（联网，A）

```
python tools/registry_status.py snapshot --out-root out/registry_status/review
  → 快照已落盘：out/registry_status/review/20261101-142152/
    node.json / versions.json / install.json / meta.json / summary.md（exit 0）
python tools/registry_status.py check --expect-latest-active
  → 结论：未通过：最新版本处于待安全复核（Flagged / Pending）… 退出码：2
python tools/registry_status.py check（无断言）
  → 结论：取数成功（不判定发布门槛）… 退出码：0
```

与 docs/60 的对账（本次实测 vs 文档 14:14 快照）：

| 项 | docs/60 | 本次实测（14:21/14:23） | 结论 |
|---|---|---|---|
| 节点状态 | `NodeStatusActive` | `NodeStatusActive` | 一致 |
| 最新版本 | 0.3.3 / `NodeVersionStatusFlagged` | 同 | 一致 |
| 版本数 / 分布 | 10 / Flagged=10 | 10 / Flagged=10 | 一致 |
| `status_reason` | 响应里没有该键 | node/install 响应的 latest_version 均**无该键** | 一致 |
| install | HTTP 200，CDN downloadUrl | 同 URL | 一致 |
| `check --expect-latest-active` | exit 2 | **exit 2** | 一致 |
| `meta.attempts` | 三端点各 1 次 | 1/1/1 | 一致 |
| `downloads` | 快照 17，但字段不稳定 | 快照 22、随后 check 23；独立采样 25/21/25 | 一致（「不稳定」为真；作者 6 次样本数字未逐项复现，但结论方向一致） |

`meta.json` / `summary.md` 内**无本机盘符/用户名**；summary 明确写 Flagged「不是安全证明」，
且**没有**把 Flagged 写成安全/不安全结论——与 docs/60 §7 的设计约束一致。

---

## 4. 文档 / 公开面（A/B）

- **docs/60 每条外部事实有来源**：`docs/52` 小节（语义/安装路径/后端源码证据）、`out/registry_status/`
  快照（API 原文）、官方 Registry/API 链接；本复核用真实 API 复核了其中可复核者（§3）。
  外部链接可达性：`registry.comfy.org` 与 `docs.comfy.org/support/contact-support` HEAD **200**；
  API 三端点由工具/独立请求实际取回。
- **英文联络稿**：全文件扫描 0 处盘符、0 处用户名、0 处凭据字面量；Draft A/B 仅含公开仓库/API 链接
  与 `<your name>/<your Comfy account>` 占位符；没有任何未验证的「安全/恶意」断言。
- **docs/52 跟进改动**：顶部 14:14 跟进段与 docs/60 §0/§1.2 数字一致；`status_reason` 的
  「键缺失」（新）与 §1.1 旧样例 `"status_reason": null` 并存（F2，见 §5）。
- **不会把 Flagged 解释成安全/不安全**：`summary`/`check` 文案均为「待安全复核、不是安全证明」，
  未出现「已证明安全」「恶意软件」等断言；`next_steps` 明确「不要 git 直装对外宣称已解决」。

---

## 5. 发现（FAIL 项 + 观察）

### F1【必修｜FAIL 触发点】docs/60 §7「失败不留半截文件/目录」与实测不符

- 文档原文（`docs/60` §7）：`snapshot` 写入用「同目录 `.tmp` + `os.replace`」，**失败不留半截文件/目录**。
- 实测（A）：把 `_write_atomic` 注入为「第 2 次写盘抛 OSError」后：
  - exit 正确 = 4，错误正确说「io / 不是 Registry 状态结论」；
  - **但快照目录已创建，且留下已成功写出的 `node.json`（无 `versions.json/meta.json/summary.md`）**。
- 影响：操作者按文档会以为「失败了不会有残留」，不会清理；残留目录/文件可能被误当成证据。
  工具没有数据损坏（单文件是完整写入的），但文档陈述是**事实错误**。
- 建议（二选一，作者决定）：
  1. 改文档：「每个文件原子写；若中途失败，可能留下已写好的完整文件与快照目录，需参照错误输出清理」；
  2. 改代码：`_run_snapshot` 失败时清理本次 `out_dir`（或直接写临时目录、成功后 rename 整目录），
     并补 1 条「中途写盘失败不留残骸」测试。
- 本复核未改作者文件；FAIL 以此项为准。

### F2【观察·低】docs/52 §1.1 旧样例 `status_reason: null` 与「键缺失」并存

- `docs/52` 顶部跟进已写「本次实测响应里该键缺失」，docs/60 §0 也点明「当时记为 null，本次实测该键缺失」；
  但 §1.1 的 JSON 样例仍写 `"status_reason": null`（node/install 两处），且标注「2026-11-01 实测」。
- 实测（A）：当前 API 的 node `latest_version` 与 install 响应**都没有 `status_reason` 键**。
- 影响：读者可能以为现在仍返回 `null`。建议给 §1.1 样例加一句「该样例为当日早先响应，现在该键缺失」，
  或删掉两行 `status_reason`。docs/60 已解释历史差异，故不单独判 FAIL。

### F3【观察·低】探测模式 + 节点级 Banned 的退出码口径

- 实测：`check`（**不带** `--expect-latest-active`）+ 节点 `NodeStatusBanned` → exit **3**；
  而不带断言 + `Flagged` → exit 0。docs/60 §7 表「0 = Active（不加断言标志时 = 取数成功）」与
  「3 = Banned（节点级优先）」并存，对「探测模式是否应为纯取数 0」表述不够清楚。
- 建议：文档注明「节点级 Banned 即使探测模式也返回 3」；或代码仅在 `--expect` 下判 Banned（二选一）。

### F4【观察·低】docs/60 §7「每次 snapshot/check 都会请求一次 install 端点」

- 实际：节点响应没有 `latest_version.version` 时会**跳过** install 端点（有 warning；测试覆盖）。
  正常路径下成立，建议加「（无 latest_version.version 时跳过）」限定。

### F5【观察·低】docs/60 §1.3 的「几乎肯定」措辞

- 「几乎肯定是多副本/缓存或滑动窗口口径」是未验证推断，同段已标注「原因未验证」；
  建议改成「可能」以免被引用为事实。（不影响结论：不稳定本身已验证。）

---

## 6. 残余风险 / 未验证（如实）

1. **无真人发送**：本复核不代发联络稿；Draft A/B 的回复率、Registry 复核 SLA 均未验证。
2. **扫描 issues 清单查不到**：公开 API 无 issue 列表，这是 docs/52/docs/60 已声明的边界；本复核无法补。
3. **`downloads` 不稳定**：本次独立采样 25/21/25、快照 22/23 与作者 6 次样本 22/17/16/21/21/16 方向一致，
   但**未逐项复现**；工具如实标注「动态计数，只作参考」，没有当证据。
4. **真实网络偶发失败**：本次真跑成功（attempts 均 1）；重试/超时只在注入下验证，未制造公网故障。
5. **docs/52 §1.2 横向对比、backend 源码结论**：本轮未逐条重查（属 docs/52 既有证据）；
   本次只复核了「当前状态/数量/退出码/来源可达」这些跟进单依赖的事实。
6. **节点 caption 路径无关**：本工具只读 Registry API，不涉及 MossPromptSmith 通道。

---

## 7. 复现

```bash
# 作者单测 / 静态
python tests/test_registry_status.py        # 41/0
ruff check tools/registry_status.py tests/test_registry_status.py

# 独立注入（out/ 临时脚本，不入库）
python out/review_registry_inject.py        # 26 项断言；含 F1 的 IO 中途失败场景

# 真机（只读公开 API）
python tools/registry_status.py snapshot --out-root out/registry_status/review
python tools/registry_status.py check --expect-latest-active   # 当前应 exit 2
python tools/registry_status.py check                          # 探测应 exit 0
```

**一句话**：工具本身（退出码、错误路径、重试、落盘字段、零依赖、不把 Flagged 当安全结论）**通过**；
但 docs/60 §7 对「失败不留半截文件/目录」的陈述与实测不符（IO 中途失败会留已写文件+目录），
在作者修正该陈述（或加清理逻辑）之前，本复核结论为 **FAIL**。

---

## 8. 修复复审（task-54，2026-11-01）

**结论：PASS —— F1 已闭合，整体 PASS。** 作者采用方案 A（临时目录 + 整体 rename），并修了 F2–F5。

### 8.1 F1 复核（代码方案；A）

修复点（`tools/registry_status.py`）：新增 `_new_tmp_dir` / `_discard_tmp` / `_write_snapshot`；
写快照先写 `<最终目录>.tmp-<随机>`，全部文件成功后 `os.replace(tmp_dir, final_dir)`；失败时删临时目录，
若 out-root 是本次新建且已空也清理。`tests/test_registry_status.py` 41→**43**（新增「中途写盘失败无残留」
与「预建 out-root 不被误删且为空」两条）。

独立注入（`out/review_registry_recheck.py`，`mock.patch.object(R, "_write_atomic", 第2次 OSError)`）：

| 断言 | 实测 |
|---|---|
| 第 2 次写盘失败 | exit=4，注入点命中（calls=2） |
| out-root（本次新建） | **整体消失**（无目录/文件残留） |
| 预建 out-root | **保留且为空**（无残骸、不误删用户目录） |
| 任意 `*.tmp-*` | `rglob`/`iterdir` 均为空 |
| 成功路径 | 只有 1 个最终目录 + 5 个完整文件，无 tmp 兄弟 |
| 失败错误文案 | 含「清理：已删除本次临时目录；不会留下半截快照目录」 |

真机复跑（修复后，A）：`snapshot --out-root out/registry_status/review_recheck` →
`20261101-143639/` 5 文件齐全、`tmp-*` 残留 **0**；`check --expect-latest-active` → **exit 2**；
`check`（探测）→ **exit 0**；状态与 14:14 快照一致（节点 Active、10/10 Flagged）。
作者 14:32 的修复后快照 `out/registry_status/20261101-143222/` 也确认 5 文件齐全、无 tmp 残留。

### 8.2 F2–F5 复核（B）

| # | 原观察 | 复核 |
|---|---|---|
| F2 | docs/52 §1.1 旧样例 `status_reason: null` 与「键缺失」并存 | **PASS**：§1.1 两处样例（node/install）下方均加「旧样例（历史响应）」注，写明 14:14 复核实测当前响应里该键缺失；与 docs/60 一致 |
| F3 | 探测模式 + 节点级 Banned 口径 | **PASS**：docs/60 §7 退出码表明确「探测模式下 Flagged/Pending/未知版本状态=0；节点级 Banned 探测也返回 3」；注入实测 0/0/3 全对 |
| F4 | 「每次 snapshot/check 都请求 install 端点」 | **PASS**：docs/60 §7 改为「只有节点响应存在 `latest_version.version` 时才请求 install」（与代码/警告一致） |
| F5 | §1.3「几乎肯定」未验证推断 | **PASS**：改为「**可能**是…——这是推断」，并保留「原因未验证」标注 |

### 8.3 回归 / 残余风险

- `python tests/test_registry_status.py` → **Ran 43 tests — OK**；`ruff` → **All checks passed**；
  零第三方依赖不变；真实 `snapshot`/`check` 正常（见 8.1）。
- 残余风险（不因修复改变）：`downloads` 仍秒级不稳定（只记录、不当证据）；Registry 扫描 issues
  仍查不到（公开 API 无清单）；真人联络稿未发送/回复率未验证；公网故障重试仅注入验证；
  docs/52 的其他既有证据本轮未逐条重查。
- 本回复审只改 `docs/64` 与 `out/` 临时脚本（`out/review_registry_recheck.py`），未改作者文件、未 commit。

**修复复审一句话**：F1（失败残留）已按方案 A 修复并用独立注入 + 真机复跑验证；F2–F5 文档观察均已改好；
**F1 已闭合，整体 PASS**。
