# Comfy Registry 版本状态 `NodeVersionStatusFlagged`：语义与安装影响（M4 前置调研）

> 2026-11-01。触发：M3 真机验证发现本包 Registry 上 `latest_version.status = NodeVersionStatusFlagged`
> （见 `docs/48` §4-⑤）。本文只**查清事实与影响、给处置建议**；不改代码、不重发版本、不执行任何修复动作。
>
> 证据分级：**已验证** = 有公开源码 / 官方文档 / 线上 API 实测或本机可复核证据；
> **未验证** = 缺环境或本轮未执行；**查不到** = 公开面没有答案，不猜。

> **2026-11-01 跟进**：状态复核与操作单见 `docs/60-Registry跟进操作单.md`——当日 14:14
> 实测快照：节点仍 `NodeStatusActive`、最新版 0.3.3 仍 `NodeVersionStatusFlagged`、
> 10/10 版本 `Flagged`、`status_reason` 仍空（本次实测响应里该键缺失）、install 端点仍 200；
> `python tools/registry_status.py check --expect-latest-active` 退出码 **2**（未通过发布闸）。
> **状态无变化**，本页 §1.1 的状态结论照旧成立；`downloads` 实测不稳定（§1.1 补注）。

---

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| 本包现在的状态？ | **节点 Active；最新版 0.3.3 Flagged；历史 10 个版本全部 Flagged**；`status_reason` 为空；publisher `zenhg` 为 `PublisherStatusActive`（已验证，API 原文见 §1） |
| `Flagged` 是什么？ | **自动安全扫描发现 issues、等待安全复核**（UI 文案就是 “Pending Security Review”）。扫描通过会变 `Active`；扫描发现问题才置 `Flagged`（已验证：registry-backend 源码 + registry-web 源码） |
| 对用户安装的影响？ | **Manager（服务器端）= 看监听地址**：loopback（如 `--listen 127.0.0.1`）→ 能；非 loopback 且未开 `allow_flagged_nodepack_install` → 不能（403）；`security_level=strong` → 一律不能。**`comfy node install` = 能**（源码不检查状态；`/nodes/{id}/install` 对本包返回 200 + 可用 downloadUrl）。**Registry 网页 Install = 只能复制命令**，等价 CLI（网页无独立安装器、无 flagged 拦截） |
| 具体扫描问题是什么？ | **查不到**。公开 API 不返回 issue 清单（`status_reason` 公开值为空；admin-only 的 `tags_admin` 公开响应省略），后端只把 issues 发到私有 Discord 安全频道 |
| 要不要阻塞发布？ | **不阻塞，但必须跟进**：loopback Manager 与 CLI 可装（语义已验证）；发布后 24h 内找 Registry 团队要 issue 清单，决定「误报 → 请求人工复核」还是「违规 → 修代码发新版」 |

一句话：**Flagged ≠ Banned，也 ≠ 安全无害**。它是「自动扫描有发现、等复核」的中间态；
具体问题不公开，所以不能自己判定是误报。

---

## 1. 事实（Registry API，可复跑）

### 1.1 本包节点与版本

`GET https://api.comfy.org/nodes/moss-storyboard`（2026-11-01 实测，HTTP 200）：

```json
{
  "id": "moss-storyboard",
  "status": "NodeStatusActive",
  "latest_version": {
    "version": "0.3.3",
    "status": "NodeVersionStatusFlagged",
    "status_reason": null,
    "createdAt": "2026-10-30T01:52:35.49355Z"
  },
  "publisher": {"id": "zenhg", "status": "PublisherStatusActive"},
  "downloads": 13
}
```

> **旧样例（历史响应）**：上面这份 JSON 是当日早些时候抓取的响应，其中
> `latest_version.status_reason: null` 是当时的表示。**2026-11-01 14:14 复核实测：node 的
> `latest_version` 与 install 响应里都没有 `status_reason` 这个键**（见 `docs/60` §0/§1.2）。
> 状态结论不受影响：`Flagged` 的语义不依赖该字段（本文 §2.2 从源码已解释为何为空）。

`GET https://api.comfy.org/nodes/moss-storyboard/versions`（HTTP 200，**共 10 个版本**）：

| version | status | status_reason |
|---|---|---|
| 0.3.3 | `NodeVersionStatusFlagged` | 空 |
| 0.3.2 | `NodeVersionStatusFlagged` | 空 |
| 0.3.1 | `NodeVersionStatusFlagged` | 空 |
| 0.3.0 | `NodeVersionStatusFlagged` | 空 |
| 0.2.2 | `NodeVersionStatusFlagged` | 空 |
| 0.2.1 | `NodeVersionStatusFlagged` | 空 |
| 0.2.0 | `NodeVersionStatusFlagged` | 空 |
| 0.1.2 | `NodeVersionStatusFlagged` | 空 |
| 0.1.1 | `NodeVersionStatusFlagged` | 空 |
| 0.1.0 | `NodeVersionStatusFlagged` | 空 |

> **没有任何一个历史版本是 Active** —— 这点与「只有最新版被 flag」的常见情况不同（见 §1.2 对比）。
> `downloads` 在调研过程中从 12 → 13 → 16 变动（动态计数，只作参考）。
> **2026-11-01 14:16 复核**：对同一端点连续 6 次相同请求（间隔 2s、期间不请求其他端点）返回
> 22/17/16/21/21/16 —— 该字段**秒级不稳定**，不能当安装量/热度证据（原因未验证）。

`GET https://api.comfy.org/nodes/moss-storyboard/install?version=0.3.3`（HTTP 200）——
**安装端点不按状态拦截**，它仍然返回可下载的版本信息：

```json
{
  "version": "0.3.3",
  "status": "NodeVersionStatusFlagged",
  "status_reason": null,
  "downloadUrl": "https://cdn.comfy.org/zenhg/moss-storyboard/0.3.3/node.zip",
  "dependencies": [],
  "deprecated": false
}
```

> 同上：`"status_reason": null` 是**旧样例（历史响应）**的表示；2026-11-01 14:14 复核实测当前
> install 响应里该键缺失（见上文注）。

CDN 也真的给包：下载 `node.zip` 实测 **2,405,602 bytes / 51 个文件**（落盘 `out/registry_status/node_0.3.3.zip`）。

### 1.2 横向对比（同一天 API 实测，证明 Flagged 是常见中间态）

| node id | node.status | latest_version.status | 版本状态集合（去重） |
|---|---|---|---|
| **moss-storyboard（本包）** | Active | **Flagged** | **仅 Flagged（10/10）** |
| comfyui-manager | — | Active | 仅 Active（56 个版本） |
| comfyui-impact-pack | Active | Active | Active + Banned（59 个版本） |
| rgthree-comfy | Active | **Flagged** | Active + Flagged（60 个版本） |
| comfyui-easy-use | Active | **Flagged** | Active + Flagged（22 个版本） |

结论（已验证）：Flagged 是**每个版本各自**的状态，节点本身仍可以是 Active；「最新版 Flagged、
旧版 Active」很常见。本包的差异是**所有版本都被 flag**——说明扫描对每个已发布版本都返回了
issues（或历史上被批量标记），不是某一次发布才触发。

---

## 2. 语义：Flagged 到底是什么（三档标注）

### 2.1 状态枚举（已验证，官方 OpenAPI + 后端代码一致）

`NodeVersionStatus`（官方 API 文档 / registry-backend `drip/api.gen.go`）：

```text
NodeVersionStatusActive | NodeVersionStatusDeleted | NodeVersionStatusBanned
NodeVersionStatusPending | NodeVersionStatusFlagged
```

`NodeStatus`（节点级）只有 `Active | Banned | Deleted`——**节点级没有 Flagged**。

### 2.2 后端怎么置状态（已验证，registry-backend 源码）

`services/registry/registry_svc.go` 的 `PerformSecurityCheck()`：

1. 取版本存档 URL（`StorageFile.FileURL`），调用**私有扫描服务**：
   `sendScanRequest(s.config.SecretScannerURL, fileURL)`；
2. 扫描返回 `issues == ""`（无问题）→ 版本置 `Active`，`status_reason = "Passed automated checks"`；
3. 扫描返回非空 issues → 版本置 **`Flagged`**，日志写
   `"Security issues found in node %s@%s. Updating to flagged."`，并把 issues 发到
   **私有 Discord 安全频道**（`SendSecurityCouncilMessage`，`SendSecurityCouncilMessage` 内容含
   `https://registry.comfy.org/nodes/<node>/versions/<version>` 链接）；
4. 存档 404 时置 `Deleted`，`status_reason = "Node zip file doesn't exist"`。

要点：**flagged 分支没有写 `status_reason`**（源码里只有 Active/Deleted 分支调用了
`SetStatusReason`）——这解释了 API 上 `status_reason` 为空的现象。**具体 issues 只进私有频道，
公开面不可见。**

### 2.3 Registry 网页怎么显示（已验证，registry-web 源码）

`components/nodes/NodeVersionStatusBadge.tsx`：

- `Active` → 绿色徽章 **“Live”**；
- `Pending` 或 `Flagged` → 黄色徽章 **“Pending Security Review”**；
- `Banned` → 红色徽章 **“Rejected”**。

即：**网页对 Flagged 的官方措辞是「待安全复核」**（不是「恶意软件」）。

### 2.4 Manager 的特判（已验证，ComfyUI-Manager 3.42 源码 + 其官方 e2e 测试）

`glob/manager_util.py`：

```python
FLAGGED_NODEPACK_INSTALL_GUIDANCE = (
  'Installation of this flagged CNR version is blocked. ...\n'
  '1. Run ComfyUI with loopback-only listeners, ... --listen 127.0.0.1 ...\n'
  '2. For a trusted private network, set allow_flagged_nodepack_install = true ...')

def is_cnr_install_allowed(status, allow_flagged, listen_address):
    """Only flagged versions require loopback or the private-network opt-in."""
    return status != 'NodeVersionStatusFlagged' or bool(allow_flagged) or is_loopback_listener(listen_address)
```

`glob/manager_core.py` 在安装前对 `node_info.status == 'NodeVersionStatusFlagged'` 抛
`PermissionError`（前端提示「This action is not allowed by the current security configuration.
See the terminal for details.」），终端打印上面的指引。

Manager 的 e2e 测试 `tests/e2e/test_e2e_flagged_nodepacks.py` 把这条策略钉成了矩阵
（listen / override / security_level × 安装结果）：

| `--listen` | `allow_flagged_nodepack_install` | security_level | flagged 版本安装 |
|---|---|---|---|
| `127.0.0.1` | false | normal | **允许** |
| `0.0.0.0` | false | normal | **拒绝**（终端打印指引） |
| `0.0.0.0` | true | normal | 允许 |
| `0.0.0.0` | true | strong | 拒绝（403，强安全级下所有 CNR 安装都不允许） |
| `127.0.0.1` | false | strong | 拒绝（同上） |

另外 Manager 的版本下拉只请求
`/nodes/{id}/versions?statuses=NodeVersionStatusActive&statuses=NodeVersionStatusPending`
（`glob/cnr_utils.py`）⇒ **Flagged 版本不会出现在版本选择列表里**；本包 10 个版本全 flagged，
因此 Manager 里会出现「没有可选的正常版本 / 安装被拦」的现象（具体 UI 表现**未验证**，因为没在活宿主重装）。

### 2.5 「会一直 flagged 吗 / 怎么变回 Active」

- 后端有 **Admin 复核页**（registry-web `pages/admin/nodeversions.tsx`：筛选 `flagged/pending/
  banned/active`，按钮 Approve / Reject / Undo；`AdminUpdateNodeVersion` PUT 端点存在），
  Approve 会把版本置回 `Active`（带 status_reason 审计历史）。**已验证（源码）**。
- 新发布版本默认 `Pending`（registry-backend `ent/schema/node_version.go`：status 默认
  `NodeVersionStatusPending`），等待扫描；扫描通过才 Active。**已验证（源码）**。
- 「发布新版本是否会自动排队扫描 / 多久扫一次」：**未验证**——公开代码里扫描由
  `GET /security-scan` 端点驱动，调度器不在公开仓内。
- 「flag 会不会自动过期」：**查不到**。

---

## 3. 三条安装路径的实际影响

| 路径 | 能否装 flagged 版本（本包 0.3.3） | 依据 | 状态 |
|---|---|---|---|
| **ComfyUI Manager（服务器端 UI）** | **看监听地址**：`127.0.0.1`/loopback → **能**；`0.0.0.0`/`::` 且未开 `allow_flagged_nodepack_install` → **不能**（403+终端指引）；`security_level=strong` → 不能 | Manager 3.42 `manager_util.is_cnr_install_allowed` / `manager_core._get_cnr_install_info` / 官方 e2e 矩阵（§2.4）；本机宿主当前就是 `--listen 127.0.0.1`（属允许场景） | 语义**已验证**（源码+官方测试）；**本机未真机执行安装**（不重装宿主） |
| **`comfy node install moss-storyboard`（comfy-cli）** | **能** | comfy-cli `registry/api.py::install_node` 只 GET `/nodes/{id}/install`；`command/custom_nodes/command.py::registry_install` 只检查 `download_url` 非空后下载解压——**全链路没有 status 检查**；本包 `/install` 端点 200 且 `downloadUrl` 可用；Manager 自己的 `cm-cli.py install` e2e 测试里，fixture flagged 版本在 `0.0.0.0 + 无 opt-in` 下仍被直装成功 | 源码+API/CDN **已验证**；**本机未安装 comfy-cli 真跑**（端到端未验证） |
| **Registry 网页 Install 按钮** | **能（等价 CLI）** | registry-web `components/nodes/NodeDetails.tsx`：非 Banned、已认领、有版本时只渲染一个可复制命令 `comfy node install <nodeId>`——**网页没有独立安装器、也没有针对 Flagged 的拦截**；版本历史里显示 “Pending Security Review” 徽章 | 源码**已验证**；**未真机浏览器点击验证** |

补充：Registry 网页对 **Banned** 节点才显示「Installation is not available for this node.」
（`NodeDetails.tsx` 的 banned 分支）；本包节点是 Active，因此页面仍给 `comfy node install` 命令。

---

## 4. 处置建议（只建议，按成本从低到高，均未执行）

1. **先要 issue 清单（成本最低，先做）**
   公开 API 不含 issues；后端把 issues 发到私有 Discord 安全频道。向 Registry/Comfy 团队
   （Comfy 支持入口：`https://docs.comfy.org/support/contact-support.md`；或 Comfy Discord 的
   Registry/安全相关频道）请求 `moss-storyboard@0.3.3`（及历史版本）的扫描发现。
   **判定依据**：在拿到清单前，「误报」与「真问题」都只是猜测。
2. **若确认是误报/历史遗留 → 请求人工复核**
   Registry 有 Admin 复核页（`Review Node Versions`，可 Approve → Active / Reject → Banned），
   后端有 `AdminUpdateNodeVersion` 端点。由 Registry 团队执行复核即可把版本置回 Active；
   我们侧**不自行改状态、不重发同版本号**。
3. **若确认是代码/打包问题 → 修代码后发布新版本**
   新版本默认 `Pending` 并等待扫描；**同一个版本号不能覆盖**，所以只能发新 patch（如 0.3.4）。
   依据：registry-backend `ent/schema/node_version.go` 默认 Pending + `PerformSecurityCheck` 置 Active/Flagged。
4. **短期无法解决时的用户侧措辞（仅文档，不推荐绕过）**
   README/Release notes 如实说明：Manager 首次安装该版本可能出现 “Pending Security Review”；
   在 **loopback（默认桌面/便携版 `--listen 127.0.0.1`）** 或自担风险的
   `allow_flagged_nodepack_install=true` 下可安装；`comfy node install` 不受该 UI 拦截。
   **不把 flagged 当「已证明安全」**，也不建议在公开文档里指导用户常开 opt-in。
5. **不要做的**
   不要对同一版本号反复重发/改元数据试图刷状态；不要伪造 `status`；不要用 git 直装来
   「绕过」并对外宣称安装问题已解决（git 安装本来就不经过 CNR 检查，无法解决 Registry 可见性问题）。

### 对 M4（发布硬化）的输入

- **发布前**：记录 `GET /nodes/{app}` 的 `status`/`latest_version.status` 作为发布快照；
  `Flagged` **不阻塞**发布（loopback Manager / CLI 可装，语义已验证），但必须进入跟进清单。
- **发布后 24h**：若仍 `Flagged` → 启动 §4-1（要 issues）与 §4-2/3（复核或修版），
  并在 README/Release notes 加一句如实说明。
- **公开宣传前**：尽量等到 `Active`；若必须带 Flagged 发布，文案只描述「待安全复核」，
  不要写「已通过安全审核」。

---

## 5. 未查到 / 未验证（不要当结论用）

| 项 | 状态 |
|---|---|
| 扫描器具体规则与本次 issues 清单 | **查不到**（私有扫描服务 + 私有 Discord 通知；公开 API 只有空 status_reason；`tags_admin` 为 admin-only 且公开响应省略） |
| 本包被 flag 的具体原因 | **查不到**。我方粗筛 0.3.3 发布包（51 个文件）未见官方文档列出的高危模式：无 `eval(`/`exec(`、无 `subprocess`、无 `os.system`、无 `shell=True`、无运行时 pip 安装、无网络下载/zip 解压/pickle/ctypes 命中；只有面向用户的错误提示里出现 `pip install insightface onnxruntime` 字样和一个 insightface 权重下载链接（仅提示文本，无执行）。**这不能证明扫描器误报** |
| flag 是否会自动过期 / 重扫频率 | 查不到 |
| 人工复核 SLA | 查不到 |
| 新版本是否自动触发扫描 | 未验证（公开代码里扫描由 `GET /security-scan` 端点驱动，调度器不在公开仓） |
| 本机 Manager 真机安装 flagged 版本 | 未验证（不在本机重装宿主） |
| 本机 `comfy node install` 端到端 | 未验证（本机无可用 comfy-cli 环境，且不执行安装） |
| Registry 网页 Install 点击 | 未验证（无浏览器自动化；源码已说明它只是复制命令） |
| Manager 版本下拉在「全部版本 flagged」时的确切 UI | 未验证（由「版本端点只查 Active+Pending」推断为无可选版本） |

---

## 6. 复现命令

```powershell
# 1) 节点事实（只读）
python -c "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('https://api.comfy.org/nodes/moss-storyboard')), ensure_ascii=False, indent=2))"
# 2) 全部版本与状态
python -c "import json,urllib.request; [print(v['version'], v['status'], v.get('status_reason')) for v in json.load(urllib.request.urlopen('https://api.comfy.org/nodes/moss-storyboard/versions'))]"
# 3) 安装端点（确认不被状态拦截、downloadUrl 可用）
python -c "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('https://api.comfy.org/nodes/moss-storyboard/install?version=0.3.3')), ensure_ascii=False, indent=2))"
# 4) 官方 API 枚举（NodeVersionStatus 定义见响应 schema）
#    https://docs.comfy.org/registry/api-reference/nodes/returns-a-node-version-to-be-installed
```

本地源码证据（Manager 3.42 与公开仓）：

- `<ComfyUI>/custom_nodes/comfyui-manager/glob/manager_util.py`（`is_cnr_install_allowed` / 指引文案）
- `<ComfyUI>/custom_nodes/comfyui-manager/glob/manager_core.py`（安装前 Flagged 拦截）
- `<ComfyUI>/custom_nodes/comfyui-manager/glob/cnr_utils.py`（版本列表只查 Active+Pending）
- `<ComfyUI>/custom_nodes/comfyui-manager/tests/e2e/test_e2e_flagged_nodepacks.py`（策略矩阵）
- registry-backend（公开仓）`services/registry/registry_svc.go::PerformSecurityCheck`、`ent/schema/node_version.go`
- registry-web（公开仓）`components/nodes/NodeVersionStatusBadge.tsx`、`components/nodes/NodeDetails.tsx`、`pages/admin/nodeversions.tsx`
- comfy-cli（公开仓）`comfy_cli/registry/api.py::install_node`、`comfy_cli/command/custom_nodes/command.py::registry_install`

---

## 7. 证据文件（`out/registry_status/`，out 不入库）

| 文件 | 内容 |
|---|---|
| `nodes_moss-storyboard` / `nodes_moss-storyboard_versions` / `..._0.3.3` / `..._0.3.2` | Registry API 原始响应 |
| `node_summary.json` / `versions_status.json` | 节点/10 个版本状态抽取 |
| `install_endpoint_response.json` | `/nodes/{id}/install` 200 响应（含 downloadUrl） |
| `node_0.3.3.zip` / `zip_filelist.txt` | 实际发布包与 51 文件清单 |
| `published_zip_static_scan.txt` | 对发布包的粗筛（未命中高危模式） |
| `backend_security_check.txt` | registry-backend `PerformSecurityCheck` 原文摘录 |
| `manager_flagged_guidance.txt` / `manager_install_gate.txt` / `manager_versions_filter.txt` / `manager_e2e_policy_matrix.txt` | Manager 3.42 判据与官方 e2e 矩阵摘录 |
| `registry_web_badge.txt` / `registry_web_install_snippet.txt` | 网页徽章文案与 Install 仅复制命令的证据 |
| `comfycli_install_no_status_check.txt` | comfy-cli 安装路径无状态检查的证据 |

> 所有「已验证」结论都能在上述文件/API 原文里找到对应行；「查不到」的项已单列，未用推断补齐。
