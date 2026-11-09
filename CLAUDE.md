# Investigation Agent Client — Development Context

Context for Claude Code / AI-assisted development. This is not product
documentation — see `README.md` for the project overview.

## Project Context

- Case/Context-centered **Investigation Agent Client**: an analyst opens
  a case, investigates its findings through conversation, and produces
  provenance-traceable investigation artifacts.
- The runtime combines planning, context resolution, tool use,
  constrained execution, and scope-controlled artifacts, running on top
  of an external, authoritative Risk Platform.
- Risk investigation is the reference domain; the architecture is
  intended to be reusable for other enterprise investigation /
  decision-support workflows.

## Core Architecture

- **Planner decides WHAT** capability/task is needed: it maps natural
  language to a structured plan (`Plan` / `PlanSteps`) and does not
  invent authoritative attributes.
- **Executor controls constrained execution**: contract checks,
  argument injection, bounded outcomes. It consumes
  `arguments -> ToolResult` and is access-path agnostic.
- **Provider boundary determines HOW a capability is accessed**:
  in-process Function Calling (default) or MCP.
- **Domain capabilities are the single source of truth**
  (`backend/app/domain_tools/`).
- Do not duplicate business logic in access adapters (MCP server,
  providers, protocol bridges).
- The Risk Platform remains authoritative for findings, evidence,
  policy citations, and explanations.

### Shared Evidence and Answer Synthesis

When multiple output goals are supported by the same observation or evidence,
render shared explanatory content only once and reference it from subsequent
sections. Preserve each goal's distinct informational requirements and answer
every requested question substantively. Do not deduplicate distinct evidence
or goal-specific content based solely on textual similarity. Completion
requires both non-redundant presentation and complete goal coverage.

## MCP

- `risk_case_fetch` is currently the **only** MCP-exposed capability.
- MCP is an additional capability-access path, **not** a replacement for
  Function Calling, which remains the default execution mode.
- Current MCP transport is **local stdio** only. Remote MCP, HTTP
  transport, MCP Gateway, and multi-agent MCP are not implemented.
- Agent Runtime MCP path:
  `Executor → McpToolProvider → McpCapabilityClient → MCP Server`
- External Host validation:
  `Claude Code → MCP Server` (registered via project-level `.mcp.json`)
- The same MCP Server and the same domain capability are reused across
  hosts; the MCP server is a thin adapter, not a second implementation.
- **No silent fallback** between MCP and Function Calling: MCP-path
  failures surface as bounded `integration_error` results.
- The Planner must not contain MCP-specific vocabulary or transport
  logic; plans name capabilities regardless of access path.
- Do not add MCP-related abstractions unless justified by an actual
  capability / access requirement.

## Important Files

- `backend/app/planner_v2.py` — LLM planner (natural language → plan)
- `backend/app/executor_v2.py` — constrained plan execution
- `backend/app/mcp_client.py` — `McpCapabilityClient` (MCP client adapter)
- `backend/app/mcp_provider.py` — `McpToolProvider` and execution-mode switch
- `backend/app/domain_tools/risk_case_fetch.py` — domain capability
  (single source of truth for the case-fetch capability)
- `mcp_server/server.py` — MCP Server (stdio adapter over the domain capability)
- `mcp_server/test_client.py` — standalone MCP protocol test client
- `qa/preflight_runtime.py` — runtime/code consistency preflight
  (mandatory before real API/browser E2E; see the QA Quality Gate
  section)
- `.mcp.json` — project-level MCP Server registration for external hosts
- `docs/architecture/MCP_ARCHITECTURE_V1.md` — MCP architecture and
  validation record
- `frontend/src/components/investigation/AgentActivityBlock.tsx` —
  conversation-inline Agent Activity block (Activity-in-conversation;
  see `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md` §9a)
- `frontend/src/components/investigation/activityPresentation.ts` —
  the single agent_status × stop_reason × goal_outcome → user-visible
  status mapping (bounded/partial never renders as success); changes
  here require mapping tests and real-browser verification
- `frontend/src/pages/InvestigationPage.tsx` + `POST
  /investigations/{id}/submit` — unified submission entry: EVERY user
  request (simple, compound, follow-up, Case Intake) executes on the
  bounded Agent Loop; there is no turn/loop routing and no Run Loop
  button (see `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md` §9a-1)

## Development Rules

- Avoid unrelated changes; keep diffs scoped to the task.
- Preserve existing Function Calling behavior unless a change explicitly
  requires otherwise.
- Do not silently fall back from MCP to Function Calling.
- Keep domain logic out of protocol / access adapters.
- Prefer small, scoped changes with tests (`backend/tests/`).
- Do not modify the older pre-V2 `backend/app/agent.py` unless
  explicitly required.

## Documentation Sources of Truth

- `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md` — runtime
  architecture (resolution → planning → execution → composition)
- `docs/architecture/MCP_ARCHITECTURE_V1.md` — MCP access path and
  three-level validation
- Other docs in `docs/architecture/` (domain models, skill model, API
  contract, evaluation, evidence principles) — consult before changing
  the corresponding architecture.

## Development / QA Quality Gate

This project has repeatedly seen features ship with green isolated
tests while the real browser journey was broken (lost context/focus
between UI and backend, stale frontend state across investigations,
new APIs ignoring pending UI state, wrong terminal plan rendering).
Therefore:

> **"Tests passing" and "feature accepted" are different states.**
> A passing unit/integration suite is NOT evidence that a user-facing
> feature is complete. Completion evidence must match the feature's
> integration surface.

### Required testing levels

- **LEVEL 1 — Component correctness**: unit / component / deterministic
  logic / schema-contract tests.
- **LEVEL 2 — Integration/E2E**: real application boundaries where
  applicable (UI → API → service → runtime → persistence → dependency
  → response/state → UI). Mocks are fine for deterministic coverage but
  do not constitute integration acceptance.
- **LEVEL 3 — Real browser/user acceptance**: REQUIRED when a change
  affects frontend behavior, conversation, Agent responses, planner
  behavior visible to users, the Agent Loop, tool routing,
  context/focus, state synchronization, polling, evidence/artifact
  navigation, user-visible status, or any cross-module workflow. The
  real browser journey must be exercised against the real running
  stack. Backend-only changes with no user-visible impact may skip it,
  but still need API/integration tests. When uncertain, prefer real E2E.

### Feature-scoped Definition of Done

Every non-trivial feature defines its acceptance surface up front and
names at least one **Golden User Journey**: a repeatable scenario from
the real user entry point that crosses all relevant boundaries and
verifies both the visible outcome and the backend/state consequences.
Re-run it after changes that affect its dependencies. Journeys are
registered in `docs/baselines/golden-user-journeys-v1.md` with their
long-term regression pointer; when the verified outcome is a persisted
deliverable (artifact/export), the journey's regression MUST assert the
persisted content and its data scope, not the artifact's existence.
Example shape
(Finding-focused): Case → select finding → request → API → target
resolution → Planner → Tool → Agent State → Activity → response.

The implementation report MUST separate: unit-tested ·
integration-tested · browser-validated · exploratory-tested, and
explicitly list what remains unverified and any pre-existing
failures (backend baseline: environment-dependent legacy failures —
~43 with RP down, ~12 with RP up; compare like-for-like). Never
summarize as "tested". Never use test counts as the primary evidence
of product correctness.

### Browser is the acceptance authority

When automated tests and real browser behavior disagree, the browser
result wins for product acceptance. Do not report "complete" while the
primary browser journey is broken; a browser failure triggers
reproduce → failing-boundary analysis → fix → browser re-test.

### Test the system, not just the module

For cross-module features, trace: user action → frontend state → API →
route → service → runtime → tool/dependency → persistence → response →
frontend → rendered result, and identify where semantic state can be
lost. Pay special attention to: context/focus propagation, task and
investigation identity, state ownership, terminal states, retries,
polling, stale state, event ordering, API/frontend contracts. Then run
lightweight exploratory testing (double-clicks, reload during/after
execution, entity switching mid-run, empty/long/Unicode/CJK inputs,
unknown IDs, RP/backend unavailable, unexpected-but-reasonable
phrasings) — find what the plan did not predict. Semantic edge cases
(no data, partial data, unsupported, blocked, error, retryable,
terminal, loop limit, ambiguous, unresolved, stale context, empty
result) are part of acceptance; do not collapse them into one
success/failure check.

### Agent-specific gate

Agent features are evaluated AS A SYSTEM: Context → Target Resolution →
Planning → Tool selection/execution → Observation → Decision → State →
Loop bound → Retry → Provenance → user-facing response → Activity.
Include natural-language, paraphrased, ambiguous, and unanticipated
requests — not only exact command phrases. Always verify the boundary:
LLM proposes, system enforces; state is authoritative; the UI does not
infer backend decisions; the backend does not rely on frontend-only
state; language never overclaims evidence; internal reasoning is never
exposed.

### Environment honesty

Before declaring completion, verify the tested processes actually run
the current implementation (stale long-running services are a real QA
finding — restart and re-verify). Mocks/fault injection are encouraged
for determinism, but when a real dependency (RP, LLM, browser) was
unavailable, document the limitation and do not claim the real path
was validated.

### Canonical runtime

The canonical development/acceptance runtime is the committed
configuration: **frontend :3001 (`frontend/vite.config.ts`) → backend
:8001 (uvicorn) → Risk Platform :8000**, sharing
`backend/data/investigation_agent_v2.db` (the default store path is
derived from `__file__` and is cwd-independent). Every work item's
FINAL acceptance journey must run against this stack AFTER restarting
its processes onto the current working tree — passing on an isolated
QA stack does not satisfy acceptance. Isolated QA stacks (e.g.
`frontend/vite.qa.config.ts` → :8010) are temporary scaffolding for
deterministic/no-key/parallel runs: never the acceptance surface, and
torn down after use. Before diagnosing "it fails in my browser",
check which code the target stack actually runs
(`GET /api/v2/runtime/identity` + process start time; note the
identity fingerprints are computed from disk at request time and do
NOT prove what a long-running process has loaded — use a behavioral
marker instead).

### Runtime/code consistency preflight (mandatory before real E2E)

Before EVERY real API E2E or browser E2E run against a live service:

1. Record which source changes the run is meant to exercise, and the
   expected runtime (watch paths + the service's reload model).
2. Inspect the ACTUAL listener process: PID, command, working
   directory, port, and safely observable configuration (feature
   flags — never secrets or env dumps).
   `qa/preflight_runtime.py` performs this inspection read-only and
   emits the preflight record for the run log.
3. Establish whether the service reliably loads the changed files:
   `restart`-model services (uvicorn without `--reload`) must have a
   process-tree start AT/AFTER the newest watched source mtime;
   `hot`-model servers (vite dev) transform source from disk at
   request time, so a stale process start alone is not a verdict —
   the behavioral probe carries the proof.
4. If loading the new code cannot be established, restart ONLY the
   affected service using the documented startup command (backend:
   `python -m uvicorn app.main:app --host 127.0.0.1 --port 8001` from
   `backend/` with its required env, relayed without writing secrets
   to disk), then re-run the preflight.
5. Re-check readiness and execute a CHANGE-SPECIFIC behavioral probe:
   a URL whose response must CONTAIN a marker only the new code
   produces. HTTP 200 alone never establishes consistency, and
   `GET /api/v2/runtime/identity` fingerprints are computed from disk
   at request time — they do NOT prove what a long-running process
   has loaded.
6. Record in the run log: working-tree identity (HEAD **plus** a hash
   of `git status --porcelain` — HEAD alone is insufficient when
   uncommitted files affect runtime), service identity (pid/cmd/cwd/
   start), restart evidence, probe result, and test start time.
7. Formal E2E execution is BLOCKED until the preflight verdict is
   FRESH. STALE (restart required) and UNVERIFIED (consistency not
   establishable) are reported as such and never downgraded by a
   later green test result.

   This preflight and the Runtime Verification Gate below are
   complementary, sequential checks: this preflight establishes that
   the running service loads the intended code and configuration; the
   Runtime Verification Gate defines the runtime verification and
   record-keeping an acceptance run must satisfy before its
   conclusions are accepted. Passing this preflight does not by
   itself satisfy the Runtime Verification Gate.

### Runtime Verification Gate (mandatory for acceptance runs)

Before ANY acceptance whose verdict depends on the running process —
real API E2E, browser E2E, or an ad-hoc runtime probe — run
`qa/preflight_runtime.py` with the acceptance's TARGET FEATURE declared
(`--expect-flag NAME=VALUE`; `--target-feature`). When the run claims
the feature path executed, declare it BEFORE the run
(`--require-feature-evidence INVESTIGATION_ID`) and verify at report
time (`--verify-feature-evidence <that gate record>`): the verification
is bound to the declaration record — evidence must come from a task in
the declared investigation, created strictly AFTER the declaration, with
a valid non-flag_off outcome; arbitrary task ids cannot satisfy it. The
gate verdict and record path are part of the acceptance evidence.

- Gate `PASS` (exit 0) → the run may start.
- Gate `FAIL` (exit 3) or `BLOCKED` (exit 2, incl. STALE/UNVERIFIED) →
  the acceptance is BLOCKED: report the verdict and record path, do not
  run the journey, and never report its results as valid acceptance.
- Pure unit/component/integration tests that do not depend on the
  running process do NOT require the gate.
- Every acceptance report MUST cite the gate record (path + verdict +
  timestamp at/after the run's start). A report without a valid gate
  citation is not a valid acceptance report.
- States the gate cannot directly observe (e.g. the listener's
  in-memory Settings — verified via the process-equivalence chain only)
  stay NOT VERIFIED and must be labeled as such; they are never reported
  as verified.
- Declaring the target feature is the caller's honest obligation: the
  gate verifies what is declared and cannot infer intent.

### Change-impact regression selection (mandatory before testing)

Before testing after a code change: (1) inspect the ACTUAL diff;
(2) identify changed modules, affected contracts, user-visible
behaviors, and plausible regression paths; (3) classify impact as
local / cross-module / critical-shared-contract; (4) select the
smallest sufficient tests for those risks; (5) state why each
selected test is necessary and why unrelated suites are excluded;
(6) define the time budget and stop condition BEFORE starting.
Default policy (deviate only with a stated reason):

- Text/local styling → focused component checks + precise browser
  assertions on the changed rendering.
- Tool parsing/mapping → focused unit + integration tests of the
  mapping table.
- Planner/Finding/RE_PLAN → relevant planner regressions + the
  affected real API journeys.
- Artifact authorization / source binding / content scope → relevant
  unit tests + real API E2E + persisted content verification, plus
  browser E2E when the UI path is affected.
- Shared context/state/cross-module contracts → affected module tests
  + the relevant end-to-end journeys.
- Build/dependency/runtime configuration → affected build/startup
  checks + the relevant runtime paths.

A shared-contract change may justify broader regression — the REASON
must be stated; it never auto-justifies running everything.
Acceptance suites are never waived by this policy: it scopes
intermediate regression only (e.g. B1–B7 remain mandatory for
Phase-2 closure).

### Bounded execution and truthful reporting (mandatory)

- Every nontrivial test run states its scope, expected duration, and
  stop condition up front. On timeout, report BLOCKED or NOT TESTED —
  never silently expand the time budget.
- Never retry automatically: first classify the previous failure
  (product defect / test-harness defect / environment failure /
  assertion-or-design error) and state why another attempt could
  produce NEW evidence.
- A test-harness failure is neither a product FAIL nor a product
  PASS — the affected journey stays unaccepted until re-measured.
- PASS / FAIL / BLOCKED / NOT TESTED are distinct statuses; never
  claim tests passed without recorded evidence paths.
- User-visible or cross-module changes still require relevant real
  API E2E; real browser E2E whenever the changed behavior affects the
  frontend/user journey. Unit-test success is never implementation
  completion.

### Defect handling

On an E2E/browser failure: reproduce → identify the failing boundary
(UI→API, API→service, service→runtime, runtime→tool, tool→dependency,
state→UI, context→planner, finding/focus→execution, task→polling,
plan→terminal) → root cause → smallest correct fix at that boundary →
browser re-test. Do not patch symptoms. Preserve Developer
Verification vs Acceptance QA separation where the project defines a
QA phase.

### Regression & evidence discipline (mandatory)

These rules are binding for every defect fix and every change to
user-visible behavior. They exist because previously-fixed,
previously-"tested" defects recurred in manual acceptance.

1. **Verify the real user input through the full intent path.** For any
   fix to user-visible behavior, prove the ACTUAL user request (the
   real phrasing, real language) routes correctly → plans/executes the
   expected steps → achieves the user's goal. A passing predicate,
   planner branch, tool, or helper test is necessary but never
   sufficient: the deterministic layer can pass while the real phrasing
   never reaches it (vocabulary drift), and a later planning stage can
   bypass an earlier fix (see `docs/baselines/2026-09-19-agent-qa-boundaries-v1.md`
   B6/B9/B14).
2. **Assert persisted results and their provenance — never just
   execution success.** "Tool succeeded", "HTTP 200", "Task completed",
   and "artifact_id exists" are NOT user-goal evidence. For artifacts,
   read the persisted content and verify it matches the user's goal:
   type/section structure, the substantive deliverable (e.g. hedge
   candidates with both accounts and transaction ids — not a plain
   transaction table), source references, and data scope. For
   partially-answered runs, the response must name the unfinished items.
   Technical execution success must never be reported as user-goal
   completion.
3. **Trace historical fixes when re-fixing a known defect.** Before
   changing code, locate the prior fix, its tests, and the code changes
   since; state (a) what the original fix implemented, (b) why its
   tests did not catch the current failure (coverage layer, vocabulary,
   plan shape, mock path), (c) how the new test reproduces the real
   failure, (d) whether other paths bypass the original fix. Prefer
   repairing or re-arming the existing mechanism over adding a
   competing one.
4. **Real failure inputs become permanent regressions.** When a
   previously-fixed user-visible defect reappears, the exact real
   failing input(s) enter the long-term regression suite for the
   corresponding languages and key paths, asserting actual behavior and
   persisted results. Never hardcode case-specific user ids, transaction
   ids, or one-off phrasings as production routing rules, and never
   tailor a regression to a single fixture so narrowly that the next
   paraphrase slips through. Register the journey in
   `docs/baselines/golden-user-journeys-v1.md`.
5. **Stop conditions and scope control.** Every task states its DoD,
   target test scope, real-acceptance requirements, and stop conditions
   up front. Unrelated failures discovered en route are recorded and
   classified (baseline / isolation / variance / new) — they neither
   auto-expand the current fix nor may be used to mask the targeted
   acceptance result. Never describe a targeted pass as a full-suite
   green.

### Reporting and language

End substantive tasks with: Changed · unit/component results ·
integration/API E2E results · real browser results · exploratory
results · known unverified areas · pre-existing failures · regressions
· acceptance status. Use explicit statuses: NOT TESTED / TESTED /
PASS / FAIL / BLOCKED — never turn "not tested" into "pass". Reserve
**"Implemented"** for code passing developer verification,
**"Validated"** for behavior exercised at the relevant integration
level, and **"Accepted/Ready"** for features past the acceptance gate.
Never say "feature complete" when the required browser/E2E gate has
not run. Layer evidence must be reported separately: a passing
unit/component suite never substitutes for the required real
integration/API E2E or browser acceptance, and tests that did not run
are never counted as passing.

### Pre-completion self-check

1. Did I test the actual user entry point?
2. Did I cross the real integration boundaries?
3. Did I run the primary Golden User Journey?
4. If frontend behavior changed, did I open the real browser?
5. Did I test at least one meaningful edge case?
6. Could stale state, wrong context, or races exist?
7. Does the UI match backend/runtime truth?
8. Did I distinguish tests from acceptance?
9. Did I state what remains unverified?
10. Could a real user complete the task I claim is complete?

If an applicable answer is "no", do not report the feature as fully
validated.

## Feature Flag / 验收治理三原则(2026-11-02,项目级最终验收轮固化)

1. **不能因为缺少 E2E 证据就永久关闭重要护栏。** 缺 E2E 证据的安全护栏/正常能力
   必须登记为验收缺口(见 docs/architecture/FEATURE_FLAG_INVENTORY.md),并安排
   ON 验证轮(单测 → 真实 API E2E → Browser E2E + 已验收旅程回归);全绿后翻 ON。
   单测不能替代真实 E2E;E2E 通过前不得盲目开启影响运行的护栏。
2. **不能因为 ON 造成一个已知回归就直接接受 OFF。** 必须调查 OFF 的功能损失、
   核对是否破坏其他已实现能力或安全保证;ON/OFF 各有场景问题时寻找两全修复;
   关闭 Flag 只能是有记录的临时止损,不能自动成为最终产品设计。
3. **不能用一个场景的通过换取另一个场景的退化。** 相关正向与反向场景必须同时
   纳入验收;一个通过另一个失败,整体不得 PASS;Flag 组合与依赖必须做影响分析;
   不得通过减少功能覆盖制造绿色结果。

Feature Flag 的完整台账、默认值依据与翻转条件见
docs/architecture/FEATURE_FLAG_INVENTORY.md(变更默认值须先更新该文件并获批)。
Feature Flag 的**状态、决策和验收进展必须同步更新到该文件**——不得只在对话、
任务报告或临时文档中更新状态;每次状态变化记录:日期/Flag/原状态/新状态/
决策依据/证据/审批来源/验收状态。

## Secret 安全规范

本章节是项目开发过程中必须遵守的长期安全约束，适用于 Claude Code 及其他
AI 编程工具在本项目中的所有工作。本规范是 AI 行为约束，不是操作系统级强
制访问控制——它能约束 AI 助手的行为建议，但不能从操作系统层面阻止所有
Secret 访问。

### 永久禁止事项

1. 永远不得将任何真实 API Key、Token、密码、私钥、Cookie、Authorization
   Header 或其他凭证原值输出到对话内容、终端展示、日志文件、报告文档、
   截图或其他任何非必要载体。

2. 永远不得执行会完整输出真实环境变量的命令，包括但不限于：
   `env`、`printenv`、未指定变量名的环境变量全量转储，以及其他具有
   相同效果的命令或脚本。

3. 永远不得执行未经脱敏的 `docker compose config`、`docker inspect`
   全量配置或 Env 输出，以及其他可能展开或展示 Secret 的配置检查命令。

4. 不得使用 `cat`、`grep`、`sed`、`awk`、Python、Node 或其他工具，以
   任何形式直接展示 `.env`、认证配置文件或 Secret 文件中的凭证值。
   此规则禁止的是展示真实凭证原值，不是禁止一切安全状态检查——允许使用
   经过设计的安全检查方式，只返回状态或脱敏结果。

5. 不得将 Secret 写入临时文件、调试文件、日志文件、测试报告、Git 文件、
   对话消息、命令输出或其他任何非必要的存储位置。

6. 不得为了调试、排障、验证配置或证明变量存在而输出真实 Secret。

### Secret 状态检查规范

任何 Secret 存在性检查，只允许返回以下三个固定状态之一：

- **ABSENT**：变量不存在。
- **EMPTY**：变量存在，但值为空字符串。
- **PRESENT**：变量存在，且值非空。

严禁输出原值、前缀、后缀、长度、哈希、Base64 或其他编码结果、或任何
可用于识别或还原凭证的派生信息。

可以输出：变量名、ABSENT / EMPTY / PRESENT 状态、脱敏后的 endpoint
（仅包含 scheme、hostname、port，不含用户名、密码、path、query、fragment）。

检查 endpoint 时，只报告 scheme、hostname、port，不得输出 URL 中的认证
信息、查询参数或其他敏感内容。

### 安全检查替代方式

优先使用安全的状态检查，而不是直接展示配置内容：

- 检查环境变量：只输出变量名和 ABSENT / EMPTY / PRESENT 状态。
- 检查 Compose 注入：只输出服务名、变量名和状态，不输出完整解析配置。
- 检查 Docker 容器环境：只查询指定变量的存在状态，不输出完整 Env 数组。
- 检查 Git：区分当前工作区、当前提交和历史提交；不得以 `.gitignore` 排除
  状态证明历史提交中没有 Secret。
- 检查应用日志：只报告是否存在匹配结果及必要的文件位置，不展示命中内容。
- 检查 endpoint：只输出脱敏后的 hostname。

如果现有工具无法在不暴露 Secret 的前提下完成检查，应停止该检查，说明
限制，并请求用户批准安全替代方案。不得为了完成检查而临时创建包含真实
Secret 的文件。

安全检查必须在数据源内部完成状态判断，只将允许输出的状态或脱敏字段
返回给调用者；不得先输出完整配置，再依赖人工或后续步骤脱敏。

### docker compose config 特别约束

本项目曾发生过 `docker compose config` 展开 `.env` 后，将真实 API Key
输出到终端的安全事件。因此：

- 禁止未经脱敏直接执行 `docker compose config`。
- 禁止将完整配置输出到对话或日志。
- 禁止通过重定向、`tee` 或其他方式将未脱敏输出保存到文件。
- 不得将"命令本身合法"理解为"其输出可以安全展示"。
- 需要检查 Compose 配置时，应使用只提取必要非敏感字段的安全方式。
- 如果无法保证输出脱敏，则不执行该检查。
- 安全的容器状态查询、服务状态查询和非敏感配置检查仍然允许——本条不是
  禁止一切 Docker Compose 检查。

### 发现 Secret 输出时的处置

如果在任何命令输出、日志、文件或对话中发现真实 Secret：

1. 立即停止继续展示、复制、传播或分析该 Secret 原值。
2. 不得在后续消息中复述 Secret。
3. 只报告凭证变量名、发现位置和暴露类型。
4. 根据实际发现的载体和传播范围，标记该凭证为可能已暴露；不得将仅存在
   于受控环境中的 PRESENT 状态直接判定为泄露。
5. 告知用户需要评估凭证轮换。
6. 未经用户授权，不得自行轮换、删除或修改凭证。
7. 不得声称已经完成轮换或泄露处置，除非有实际证据。

### Secret 与项目配置的区分

必须区分以下不同凭证，不得因为变量名称相似或 endpoint 属于同一供应商
就将其混为一谈：

- Claude Code 自身访问模型服务的认证 Token（`ANTHROPIC_AUTH_TOKEN`）。
- Investigation Agent 项目后端访问 BigModel 的 API Key
  （`ANTHROPIC_API_KEY`）。
- 其他第三方服务凭证。

环境变量 PRESENT 不等于已经泄露。容器中 ABSENT 不等于历史泄露已经解决。
`.gitignore` 排除文件不等于 Git 历史中从未出现过 Secret。

### 违规即停

当某项操作可能违反本规范时：

- 不得为了完成任务而冒险执行。
- 先寻找安全替代方案。
- 无法找到安全替代方案时，暂停该操作并向用户说明。
- 不得自行降低安全标准或扩大授权。
