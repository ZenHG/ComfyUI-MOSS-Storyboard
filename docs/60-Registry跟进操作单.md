# Registry 跟进操作单：moss-storyboard 版本状态

> 2026-11-01。前置：`docs/52-Registry版本状态-flagged.md`（语义、三条安装路径、后端源码证据已查清）。
> 本文只做**可持续跟进**：复核当前状态、给决策树、给可直接发送的英文联络稿、给发布前后 24h
> 清单、给「不要做」清单、把**需要真人账号**的动作单列。**不改 Registry、不发版、不代用户对外
> 提交任何东西。**
>
> 证据分级沿用 `docs/52`：**已验证** = 有公开源码 / 官方文档 / 线上 API 实测或本机可复核证据；
> **未验证** = 缺环境或本轮未执行；**查不到** = 公开面没有答案，不猜。

---

## 0. 结论先行

| 问题 | 2026-11-01 14:14（+08:00）复核结论 | 证据 |
|---|---|---|
| 节点状态 | `NodeStatusActive` | `out/registry_status/20261101-141400/node.json`（已验证，本次实测） |
| 最新版本 | `0.3.3`，`NodeVersionStatusFlagged` | 同上（已验证） |
| 历史版本 | 10 个，**10/10 全部 `Flagged`**，没有一个是 `Active` | `versions.json`（已验证） |
| `status_reason` | 空：**响应里没有这个键**（`docs/52` §1.1 当时记为 `null`，本次实测该键缺失） | `versions.json` / `node.json`（已验证） |
| 安装端点 | `GET /nodes/moss-storyboard/install?version=0.3.3` → HTTP 200，`downloadUrl` 可用 | `install.json`（已验证） |
| `downloads` | 快照值 17；但**该字段不稳定**：同一节点在 12 秒内 6 次相同请求返回 22/17/16/21/21/16 | 本页 §1.3（已验证「不稳定」；原因**未验证**） |
| 状态有没有变化 | **没有**：与 `docs/52` 的结论一致，仍是「扫描有发现、等待安全复核」 | 对比 `docs/52` §1.1 |
| 发布闸判定 | `check --expect-latest-active` 退出码 **2（未通过）** | 本页 §1.2（已验证） |

一句话：**状态没变，跟进动作还没开始做**——第一件要人做的事是把 §3 的联络稿发出去
（需要真人 Comfy 账号 / 邮箱，本仓库不代发）。

---

## 1. 当前状态复核（真跑一次 snapshot）

### 1.1 命令

```powershell
python tools/registry_status.py snapshot
python tools/registry_status.py check --expect-latest-active
```

### 1.2 结果（2026-11-01 14:14 +08:00 实测）

| 项 | 值 |
|---|---|
| 快照目录 | `out/registry_status/20261101-141400/`（`out/` 已 gitignore，不入库） |
| 落盘文件 | `node.json` / `versions.json` / `install.json` / `meta.json` / `summary.md` |
| 节点状态 | `NodeStatusActive`；publisher `zenhg` / `PublisherStatusActive` |
| 最新版本 | `0.3.3` → `NodeVersionStatusFlagged`，`createdAt 2026-10-30T01:52:35.49355Z` |
| 版本总数 / 状态分布 | 10 / `NodeVersionStatusFlagged=10` |
| 安装端点 | HTTP 200；`downloadUrl=https://cdn.comfy.org/zenhg/moss-storyboard/0.3.3/node.zip` |
| `check --expect-latest-active` | **退出码 2**（Flagged/Pending → 2；不是 0，也不是 3/4） |
| 网络请求 | 三个端点各命中 1 次，无重试（`meta.json.attempts`） |

`check` 的人读输出明确写「结论：未通过：最新版本处于待安全复核（Flagged / Pending），不是
Active」，并且**没有**把它写成「安全」或「不安全」——这是本工具的设计约束，见 §7。

### 1.3 复核时发现：`downloads` 字段不稳定（不要把当证据用）

`docs/52` §1.1 只写了「`downloads` 是动态计数」。本次复核做了更严格的观测：**对同一个节点
端点连续发相同请求、不碰 install 端点**，2 秒间隔取 6 次：

```text
22, 17, 16, 21, 21, 16        # 2026-11-01 14:16 +08:00 实测
```

- **已验证**：`downloads` 在秒级就会来回跳（**可能**是多副本/缓存或滑动窗口口径——这是推断，
  **未验证**），**不能**当安装量、热度或「有没有人装」的证据。
- **未验证**：具体口径（多副本缓存？时间窗口？去重？）——公开面没有说明，不猜。
- 工具输出里照样记录 `downloads`，但都带「动态计数，只作参考」；`docs/52` §1.1 的那句
  本次也补了实测数字（见 `docs/52` 顶部跟进行）。

> 影响：§4 清单里**不要**用 `downloads` 增长当作「发布成功」的信号。

---

## 2. 决策树（拿到 `check` 结果后怎么走）

```text
python tools/registry_status.py check --expect-latest-active
│
├─ 0  Active
│   └─ 发布闸放行；仍按 §5 做发布后 24h 复验（状态可能被后续扫描/复核改动）
│
├─ 2  Flagged / Pending（当前状态）
│   ├─ 还没有扫描 issues 清单  → 发 §3 的英文联络稿（人工账号执行，见 §6）
│   ├─ 拿到清单，全部是误报 / 历史遗留
│   │   └─ 请求 Registry 人工复核（Review Node Versions → Approve → Active）
│   │      我们侧不改状态、不伪造 status、不重发同版本号（依据 docs/52 §2.5）
│   ├─ 拿到清单，是真问题（代码 / 打包）
│   │   └─ 修代码 → 发**新的 patch 版本**（如 0.3.4；同版本号不能覆盖）
│   └─ 拿到清单但看不懂（只给规则名 / 没有文件行号）
│       └─ 回信要求具体文件 + 规则版本 + 复现方法；别自己脑补
│
├─ 3  Banned
│   └─ 最高优先级：立刻联系 Comfy 支持 + 评估下架影响（页面不可装、命令不可用）
│      不要用 git / link 安装对外宣称「问题已解决」（那条路径本来就不经过 CNR）
│
└─ 4  取数失败（网络 / HTTP / 坏 JSON / schema）
    └─ 这是**工具/网络**问题，不是状态结论：重试、查代理/网络；
       别据此发任何对外说法，也别据此判断「状态变坏了」
```

补充判定边界（都来自 `docs/52`，此处不重复论证）：

- `Pending` 与 `Flagged` 在网页上是同一个黄色徽章「Pending Security Review」；但 `Pending`
  通常意味着「刚上传、还没跑完扫描」，`Flagged` 意味着「扫描返回了 issues」——**语义不同**，
  别混着说（`docs/52` §2.1–2.3 已验证）。
- 「新发布版本是否自动排队扫描 / 多久扫一次 / 人工复核 SLA」——**查不到**（`docs/52` §5）。
  所以 §5 的 24h 时效是**工程约定**，不是官方承诺。

---

## 3. 可发送的英文联络稿

**发送前须知（必须遵守）**：

- 需要**真人账号**：Comfy 支持表单（<https://docs.comfy.org/support/contact-support>）或
  Comfy Discord 的 Registry / 安全相关频道。本仓库/本工具**不代发**、也没有对外发帖权限。
- 用发布者身份发；**不要**贴本机路径、token、私有 Discord 截图、内部沟通记录。
  只引用**公开**页面与公开 API 响应。
- 措辞边界：我们是**请求扫描清单 + 问复核流程**，不是「请求免审放行」，也不预设「这是误报」。
- 一次只走一个渠道；没回音等 5–7 个工作日再用 Draft B 跟进，不要同时到处开公开 issue 施压。
- 英文是支持团队的沟通语言；保持单份草稿，不附中文长文，避免翻译歧义。

### Draft A — 首次索取扫描发现（可直接复制，`<...>` 处按需替换）

```text
Subject: Request for scan findings on moss-storyboard versions (all currently NodeVersionStatusFlagged)

To: Comfy Registry team / Comfy support

Hello,

I am the publisher of the Comfy Registry node "moss-storyboard" (publisher id "zenhg",
repository https://github.com/ZenHG/ComfyUI-MOSS-Storyboard).

As of 2026-11-01, the public Registry API shows the node itself as NodeStatusActive, while
its latest version (0.3.3) and all ten published versions (0.1.0 through 0.3.3) are
NodeVersionStatusFlagged. The versions endpoint does not return an issue list, and no
status_reason is present on any version entry, so as the publisher I cannot see what the
automated scan found. The Registry web UI shows these versions as "Pending Security Review".

Could you share, for the flagged versions:
1. the scan findings (rule/check name, affected file and line if applicable, severity), or
2. confirmation that these flags are historical/bulk flags with no outstanding findings, or
3. the recommended remediation path if any finding is genuine.

To be clear: I am not asking for an unreviewed approval or for the flags to be removed
without review. If there are real issues I want to fix them and publish a new patch version.
If the findings are false positives, I would like to know how the publisher-side appeal or
manual review process works (what evidence would help, and who reviews it).

Public evidence pointers (all publicly accessible):
- Registry page: https://registry.comfy.org/nodes/moss-storyboard
- Node payload: https://api.comfy.org/nodes/moss-storyboard
  (status: NodeStatusActive; latest_version: version 0.3.3, status NodeVersionStatusFlagged)
- Versions payload: https://api.comfy.org/nodes/moss-storyboard/versions
  (10 versions, all NodeVersionStatusFlagged)
- Install endpoint: https://api.comfy.org/nodes/moss-storyboard/install?version=0.3.3
  (HTTP 200, returns a download URL)
- The repository above contains a public write-up of the observed semantics in
  docs/52-Registry版本状态-flagged.md

I have not modified any Registry state in connection with this request.

Thanks,
<your name> (@<your Comfy account>), publisher "zenhg"
```

### Draft B — 无回音后跟进（5–7 个工作日后再用）

```text
Subject: Follow-up: scan findings request for moss-storyboard (all versions NodeVersionStatusFlagged)

Hello,

A short follow-up on my request from <original date> about the Registry node
"moss-storyboard" (publisher id "zenhg").

As of <check date>, all ten published versions remain NodeVersionStatusFlagged and the
public API still exposes no status_reason or issue list, so I still cannot tell whether the
findings are false positives or real problems I should fix.

Could you confirm the request was received and, if possible, share the findings or an
expected timeline? If a different channel is preferred for this (for example a specific
Registry/security channel in the Comfy Discord), please point me there and I will repost it.

No Registry state has been changed by me, and I am not asking for an unreviewed approval.

Thanks,
<your name> (@<your Comfy account>), publisher "zenhg"
```

> 判断：这两个草稿**不含**本机路径、用户名、token、内部叙事，也不预设结论；事实部分都能在
> 公开 API 或本仓 `docs/52` 找到对应行。发送前把中文全角标点替换成英文标点即可。

---

## 4. 发布前后 24h 检查清单

### 发布前（提交 Registry 之前）

- [ ] CI 口径全绿（`docs/44` 的清单；不要只跑单测）。
- [ ] `python tools/registry_status.py snapshot` 留**发布前基线**，记下 latest 版本与状态。
- [ ] 新版本号在 `versions.json` 里**不存在**（同版本号发布会被拒；`docs/52` §4-3）。
- [ ] `pyproject.toml` / `CHANGES.md` / `RELEASE-NOTES.md` 版本号一致。
- [ ] README / Release notes 里**没有**「已通过安全审核」这类措辞；有 flagged 历史时只写
      「Registry 显示 Pending Security Review」这种事实描述。
- [ ] 明确本轮**不**承诺「发布后一定变 Active」（扫描调度与 SLA 查不到，见 §8）。

### 发布后 0–24h

- [ ] 发布完立刻 `snapshot`，新版本应先是 `Pending`（源码结论：新版本默认 Pending，
      `docs/52` §2.5 已验证；**是否自动触发扫描未验证**）。
- [ ] 24h 后仍 `Flagged` / `Pending` → 启动 §3 联络稿 + 按 §2 决策树走。
- [ ] 期间**不要**反复重发同版本号、改元数据或刷状态（见 §5）。
- [ ] 若 24h 内变 `Active` → 记录快照目录收尾；仍等下一次发布再复用本清单。
- [ ] 用 `check` 而不是肉眼记数字：记录退出码（0/2/3/4 的含义见 `docs/52` §4 与本文 §7）。

### 用户可见文案（发布公告 / README / Release notes）

- 允许：「Registry 上该版本状态为 Pending Security Review / NodeVersionStatusFlagged」。
- 允许：说明 loopback 桌面/便携版（`--listen 127.0.0.1`）按 Manager 指引可安装
  （`docs/52` §3 已验证语义，但**本机未真机复验**）。
- 禁止：「已通过安全审核」「已证明安全」「恶意软件」；也禁止推荐用户常开
  `allow_flagged_nodepack_install`（见 §5）。

---

## 5. 「不要做」清单

1. **不要**对同一版本号重发 / 改元数据 / 删了重发试图刷状态（Registry 不接受覆盖，
   且这属于骚扰式操作）。
2. **不要**伪造 `status`、改 Registry 数据、调用 admin 端点；Approve/Reject 只能由
   Registry 团队执行（`docs/52` §2.5 已验证：admin 复核页是团队侧工具）。
3. **不要**用 git 安装 / 直接给 zip 链接来「绕过」，并对外宣称安装问题已解决——那条路径
   不经过 CNR 检查，跟 Registry 可见性无关（`docs/52` §4-5）。
4. **不要**把 `Flagged` 写成「已通过」「已证明安全」，也**不要**写成「恶意软件」——
   网页官方措辞是「Pending Security Review」。
5. **不要**在 README / 公告里推荐用户常开 `allow_flagged_nodepack_install`，或教用户绕过
   Manager 的拦截。
6. **不要**把 `downloads` 当热度/安装量证据（§1.3：秒级波动）。
7. **不要**用本工具高频轮询（公开共享 API）：每个发布周期 1 次 snapshot + 需要时 1 次
   check 足够；24h 内不要每十分钟跑一次。
8. **不要**在公开 issue / 社媒里贴私有 Discord 截图、内部沟通记录、本机路径、token。
9. **不要**在拿到扫描清单前对外断言「这是误报」或「这是扫描器 bug」。

---

## 6. 需要人工账号执行的动作（本仓库不代做）

| # | 动作 | 需要什么 | 谁来执行 | 状态 |
|---|---|---|---|---|
| 1 | 发送 §3 Draft A 联络稿 | 真人 Comfy 账号或邮箱 | 项目维护者（发布者 `zenhg` 对应的人） | **待执行** |
| 2 | 5–7 个工作日无回音 → 发 Draft B | 同上 | 同上 | 待执行 |
| 3 | 若确认误报：请 Registry 人工复核 Approve | Registry 团队内部 admin 页 | **Registry 团队**（我们只能请求，不能自己点） | 待触发 |
| 4 | 若确认真问题：修代码并发新 patch 版本 | 发布凭据 + 发布流程 | 维护者（按 `RELEASING.md`；本轮不在本任务范围） | 待触发 |
| 5 | 更新 README / Release notes 的安装说明措辞 | 仓库写权限 | 维护者（可离线做，但**发布**动作需账号） | 待触发 |
| 6 | 对外公告里说明状态 | 社媒/公告渠道账号 | 维护者 | 待触发 |

> 本仓库里的 `.comfy_registry_token` 与本任务**无关**：本工具不读它、不打印它、不发任何
> 带凭据的请求。发布凭据的使用属于 `RELEASING.md` 的流程，不在本操作单内。

---

## 7. 工具说明：`tools/registry_status.py`

标准库（`urllib`）实现，**零第三方依赖**，只打公开只读端点：

```powershell
python tools/registry_status.py snapshot                      # 取证：落盘 raw JSON + summary.md
python tools/registry_status.py check                         # 探测：取数成功即 0，不断言
python tools/registry_status.py check --expect-latest-active  # 发布闸：按状态断言
python tools/registry_status.py check --expect-latest-active --json   # 机器可读
```

| 退出码 | 含义 |
|---|---|
| 0 | `Active`；**探测模式**（不加 `--expect-latest-active`）下 = 取数成功，`Flagged` / `Pending` / 未知版本状态也返回 0（只报告、不断言） |
| 2 | `Flagged` / `Pending`（待安全复核；仅断言模式） |
| 3 | `Banned`；节点级 `NodeStatusBanned` 优先判 3，**探测模式也返回 3**（节点级 Banned 不被探测模式掩盖） |
| 4 | 取数失败（网络 / HTTP / 坏 JSON / schema）；断言模式下「版本状态不在预期枚举内」（如 `Deleted`）也是 4 |

- **4 不是状态结论**：只说明「没取到可判定的数据」，别据此发对外说法。
- `snapshot` 落盘目录 `out/registry_status/<YYYYMMDD-HHMMSS>/`（`out/` 不入库）：
  `node.json` / `versions.json` / `install.json` / `meta.json` / `summary.md`。
  写入用**两阶段**：先写 `<最终目录>.tmp-<随机>`，全部文件成功后整体 `os.replace` 成最终目录；
  中途失败删除临时目录（若 out-root 是本次新建且已空也一并清理）。因此**「快照目录存在」= 「快照完整」**，
  失败路径不会留下半截目录/文件（单测注入「第 2 次写盘失败」断言 out-root 无残留）。
- 重试参数明确：`--timeout`（默认 20s）、`--retries`（默认 2）、`--backoff`（默认 2.0s，
  第 n 次重试等 `backoff*n`）；4xx 不重试，5xx/429/坏 JSON 重试。
- 离线单测：`python tests/test_registry_status.py`（43 条，全绿；`ruff` 干净）。
  测试用注入的假取回器切换 Active/Flagged/Pending/Banned/网络错/坏 JSON，**不真联网**。
- 只有节点响应存在 `latest_version.version` 时，`snapshot` / `check` 才会请求 install 端点；
  缺失时跳过并留警告（此时 `check --expect-latest-active` 以 4 退出）。
  `downloads` 字段不稳定（§1.3），记录它但不据此下结论。

---

## 8. 未验证 / 查不到（不要当结论用）

| 项 | 状态 |
|---|---|
| 本次 10 个版本被 flag 的**具体 issues** | **查不到**（公开 API 无 issue 清单；后端只发私有 Discord 安全频道，`docs/52` §2.2） |
| flag 是否会自动过期 / 重扫频率 | 查不到 |
| 人工复核 SLA / 是否接受发布者申诉 | 查不到（`docs/52` §5 同样结论） |
| 新发布版本是否自动触发扫描 | 未验证（公开代码里扫描由 `GET /security-scan` 驱动，调度器不在公开仓） |
| Manager 在「全部版本 Flagged」时版本下拉的确切 UI | 未验证（由「版本端点只查 Active+Pending」推断为无可选版本，`docs/52` §2.4） |
| `downloads` 波动的真实原因 | **未验证**（只确认不稳定：12 秒内 22/17/16/21/21/16） |
| 本机 `comfy node install` 端到端 | 未验证（与 `docs/52` §5 一致，本任务不安装） |
| Registry 网页 Install 点击行为 | 未验证（源码已说明只是复制命令，`docs/52` §3） |

---

## 9. 跟进记录

| 日期（+08:00） | 动作 | 结果 | 证据 |
|---|---|---|---|
| 2026-11-01 14:14 | 首轮 `snapshot` + `check --expect-latest-active` | 节点 Active；10/10 版本 `NodeVersionStatusFlagged`；install 200；check 退出码 **2** | `out/registry_status/20261101-141400/` |
| 2026-11-01 14:16 | `downloads` 稳定性观测（6 次相同请求，无 install 请求） | 22/17/16/21/21/16 → 字段不稳定，不作证据 | 本文 §1.3 |
| 2026-11-01 14:32 | snapshot 写入改为两阶段后复跑 `snapshot` + `check` | 快照落盘完整；节点 Active、10/10 Flagged；`check --expect-latest-active` 退出码 **2**（状态无变化） | `out/registry_status/20261101-143222/` |
| 待填 | 发送 §3 Draft A | 待执行（需真人账号） | — |
| 待填 | 收到 Registry 回复 → 按 §2 决策树 | 待触发 | — |

> 每次复跑后在此表追加一行（日期 / 动作 / 结果 / 证据目录），`out/` 目录不入库，
> 但目录名可写进本表作为可复核指针。
