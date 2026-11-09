# External Browser E2E Acceptance — GitHub 发布前真实浏览器验收 (2026-11-08)

**Final status: PASS** — External RP 模式完整浏览器用户旅程通过。
执行方式：真实 Chrome（CDP 驱动，`qa/browser.py`）操作当前运行中的
Docker 栈（agent-frontend :3001 → agent-backend :8001 → Risk Platform
:8000），真实 BigModel LLM，真实 Risk Platform 数据，零 mock。本文即
实际执行报告，非计划/设计文档。

## 1. 运行栈与环境一致性

| 项 | 值 |
|---|---|
| frontend | Docker `agent-frontend` :3001→80（镜像 2026-11-06 18:15 构建，晚于全部 src mtime 最新值 11-03 16:37） |
| backend | Docker `agent-backend` :8001 healthy（镜像 2026-11-07 18:15 构建 > backend/app 最新 mtime 11-07 17:48；容器 2026-11-08 15:49 启动） |
| Risk Platform | 本地 Python :8000 存活（972 cases） |
| 模式 | `AGENT_SYNTHETIC_RP_FIXTURE=EMPTY` → **External RP 模式**；`ANTHROPIC_API_KEY=PRESENT`（真实 LLM） |
| LLM | 真实 BigModel（glm）经 z.ai —— 本次 intake + goal 两次真实调用，无 mock、无降级 |
| 容器↔工作树 | backend/app 54 个 .py + 1 个非 .py 逐文件 md5 **全部一致**（backend/docs/evaluations 之外的唯一差异为排序） |

- Preflight gate record：`docs/evaluations/external-browser-e2e-preflight-20261108.json`
  — verdict **STALE**（原因：其进程模型把 46h 前启动的
  com.docker.backend 端口代理当 :8001 listener，宿主 mtime watermark
  对 Docker 拓扑不适用；如实记录，不以后续绿色结果降级）。
- 补充一致性证据：`docs/evaluations/external-browser-e2e-stack-consistency-20261108.json`
  — 镜像构建时间晚于全部源码修改 + 容器内文件与工作树逐字节一致 +
  行为证据（store 中 2026-11-08 早间的 U00043 external 调查为上一轮
  D3 后端验收产物）。
- 用户本轮约束（不得重启/重建容器/改代码）下无法执行"重启后再跑
  preflight"的标准补救；以上述镜像内容一致性链作为本次运行的代码
  一致性依据，并如实保留 STALE verdict 于记录中。

## 2. 执行过程（按实际操作顺序）

Harness：`qa/external_browser_e2e_20261108.py`（单次运行，
JOURNEY_OK，2026-11-08 09:34–09:43 UTC，10 张 fresh 截图）。
补充探针：`qa/external_browser_e2e_isolation_followup2_20261108.py`。

1. **打开前端**：真实 Chrome 访问 `http://127.0.0.1:3001/`；页面标题
   断言 `Investigation Agent`；表单挂载。截图 01。
2. **创建调查（浏览器操作）**：点击 `New Investigation` → 输入
   `External Browser E2E Acceptance — investigate U00299` → 提交。
   服务器解析 case reference → `U00299`；调查
   **INV-cd791b211660** 持久化（case_id=U00299，
   **data_source=external**，创建时间晚于点击时刻，页面显示 iid）。
   截图 02。产品无标题字段（调查模型只有 case 绑定），标题以 intake
   消息承载并显示在会话首条。
3. **Case Intake（真实闭环）**：页面轮询到新任务
   TASK-ef416f2ca14b → 终态 `completed`（真实 LLM + 真实 RP）。截图 03。
4. **Findings（intake 后）**：Findings 面板 4 项，F1–F4 标题与本次
   会话经 MCP `risk_case_fetch(U00299)` 实时获取的 live oracle 完全
   一致（零缺失）。截图 04。
5. **提交调查目标（浏览器）**：输入
   `Investigate this case's risk findings — the coordinated trading
   pattern and the high withdrawal frequency — and generate the
   investigation bundle artifact documenting the case evidence.`
   → 页面轮询到 TASK-5de79f4510de，investigation 绑定不变。
   运行态捕获：busy 文本 + 2 个 task block 可见。截图 05。
6. **等待完成（真实模型，有界 660s 内）**：终态 `completed`
   （"Investigation completed · 8 steps · 2 artifacts"）。截图 06。
7. **Artifact**：Artifacts 侧栏计数 2，内容
   `Investigation Evidence Bundle — U00299`（Case ID U00299；F2 交易
   记录表含 T024429、F3 提现记录表含 W002214 —— 均为本次 live
   oracle 的真实 U00299 证据 id）；Sources 行在；
   `GET /api/v2/tasks/TASK-5de79f4510de/artifacts` 返回
   ART-0a16903bc2（2683 字符）/ ART-b92da1d3a8（4963 字符），
   两者均绑定本调查、内容含 U00299 真实证据、无 U99001。截图 07。
8. **刷新页面**：`location.reload()` → 应用重挂载 → 页面仍显示
   INV-cd791b211660（localStorage 恢复路径）。截图 08。
9. **刷新后恢复断言**：Findings 4/4 oracle 标题仍在；Artifacts
   计数 2 仍在。
10. **从 History 重新打开（补充探针，全新浏览器 profile、无
    localStorage）**：点击 History 面板 U00299 行主按钮 → Findings
    4/4 渲染（F1 "96.24/100"、F2 "45.24%…exceeded the 40"、F3
    "14 withdrawals…24 hours"、F4 标题齐全）、Artifacts 2 恢复、
    goal 任务回答卡 3508 字符完整恢复。截图 09。
    （探针第一版点击命中 `<li>`（无 handler）造成两次
    NOT-RESTORED 假象——harness 点击目标缺陷，修正为行内主
    Button 后通过；非产品缺陷，如实记录。）
11. **隔离检查**：U99001 在页面上的全部出现位置 = History 面板
    2 条**其他历史调查**行（2026-11-07 的 demo 调查历史，合法显示
    既有历史）；本次调查的 Findings 列表、Artifacts、回答卡、
    持久化 artifact 内容零 demo 混入。
12. Console 零 error、网络零失败（两次浏览器会话均如此）。

## 3. 测试对象关联关系

| 项 | 值 |
|---|---|
| Investigation | **INV-cd791b211660**（case U00299，2026-11-08T09:35:20Z） |
| data_source | **external**（store document + `GET /api/v2/investigations/{id}` 双重确认） |
| Task（intake） | TASK-ef416f2ca14b → completed |
| Task（goal） | TASK-5de79f4510de → completed（8 steps · 2 artifacts） |
| Artifacts | ART-0a16903bc2（F3 提现记录集）、ART-b92da1d3a8（F2 交易记录集），kind=findings_summary，绑定 TASK-5de79f4510de |
| 调查对象 | RP case **U00299**（HIGH，risk 72.12，ml 96.24；F1–F4 完整） |
| 模式确认依据 | `AGENT_SYNTHETIC_RP_FIXTURE=EMPTY` + 持久化 data_source=external + 渲染数据与 live RP oracle 逐项一致（≠ U99001 fixture） |

## 4. 验收矩阵

| 验收项目 | 结果 |
|---|---|
| 真实浏览器打开前端 | PASS |
| External 模式确认 | PASS |
| 浏览器创建 Investigation | PASS |
| 浏览器提交调查 | PASS |
| Agent 执行状态正常展示 | PASS |
| 调查完成状态正常展示 | PASS |
| Findings 正常展示 | PASS |
| Finding 与实际调查结果对应 | PASS |
| Artifact 正常展示 | PASS |
| Artifact 来源正确 | PASS |
| 页面刷新后状态恢复 | PASS |
| 刷新后 Findings 恢复 | PASS |
| 刷新后 Artifact 恢复 | PASS |
| Demo / External 数据隔离 | PASS |
| 完整浏览器用户旅程 | PASS |

## 5. 截图（真实 Chrome 界面，fresh-proof mtime 校验）

| 文件 | 说明 |
|---|---|
| `qa/screenshots/exte2e-20261108-01-frontend-home.png` | 前端首页/调查入口 |
| `qa/screenshots/exte2e-20261108-02-created.png` | 浏览器创建 Investigation（含标题消息） |
| `qa/screenshots/exte2e-20261108-03-intake-complete.png` | Case Intake 完成（真实 LLM+RP） |
| `qa/screenshots/exte2e-20261108-04-findings.png` | Findings 面板（F1–F4） |
| `qa/screenshots/exte2e-20261108-05-running.png` | 调查运行中状态 |
| `qa/screenshots/exte2e-20261108-06-completed.png` | 调查完成状态（8 steps · 2 artifacts） |
| `qa/screenshots/exte2e-20261108-07-artifact.png` | Artifact 渲染 |
| `qa/screenshots/exte2e-20261108-08-after-reload.png` | 页面刷新后 |
| `qa/screenshots/exte2e-20261108-09-reopened.png` | 重新打开 Investigation 后 |
| `qa/screenshots/exte2e-20261108-99-end-state.png` | 终态留档 |

证据 JSON：
`backend/docs/evaluations/external-browser-e2e-20261108.json`（主旅程）、
`…-isolation-followup2-20261108.json`（隔离+History 重开探针）。

## 6. 本次实际发现的问题/观察（非阻塞）

1. **UI 无数据源模式指示器**：External/Demo 由后端配置决定，前端
   不显示当前模式；用户只能通过调查数据内容判断。产品观察，非本次
   缺陷（ops 文档如实记载该设计）。
2. **History 面板会列出其他 data_source 的历史调查**（含 demo 调查
   行）。显示历史是允许行为；本次验证未触发跨源继续操作（未测
   DATA_SOURCE_MISMATCH 409 路径，NOT TESTED）。
3. Preflight 工具对 Docker 拓扑给出 STALE（进程模型限制）；已用
   镜像内容一致性证据补充并如实双记录。
4. 探针 v1 的两处 harness 缺陷（panel 归因 querySelector 文档序、
   历史行点击命中无 handler 的 li）——均已修正并复测通过。

## 7. GitHub 发布判断

1. **External Browser E2E 通过**：完整用户旅程（打开→创建→提交→
   运行→完成→Findings→Artifact→刷新→重开→隔离）在真实浏览器 ×
   真实 LLM × External RP 下全项 PASS。
2. **已真实验证的用户操作**：打开应用、创建调查、提交复合调查目标、
   观察运行/完成状态、查看 Findings 并与真实 RP 数据核对、查看/读取
   Artifact 持久化内容、刷新页面、从 History 重新打开既有调查。
3. **尚未验证**：跨 data_source 继续调查的 409 拦截行为；headful
   浏览器下的交互（本次为 headless Chrome 真实引擎+CDP 截图）；
   Docker 栈的 Demo 模式浏览器旅程（历史上有 Phase 3B-3 记录）。
4. **无阻止 GitHub 发布的核心问题**（以本次 External 浏览器旅程
   范围为准）。
