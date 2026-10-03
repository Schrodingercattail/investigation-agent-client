# AL-2 QA — Tool Orchestration

**Date**: 2026-09-28
**Status**: AL-2 IN PROGRESS — honest status: dedicated orchestration
E2E has NOT been run to completion; core validation logic is proven by
targeted unit tests (3/3 PASS); no production code changed.
**Baseline**: plan rev. 3 §21 AL-2; frozen refinement `9842ec834bdb` ✓;
flags default OFF ✓; no MCP ✓.

## 1. Runtime Audit Findings (verified from code)
- Tool selection: planner emits step `type` → deterministic
  `STEP_TOOL_MAP` binding (tool_name + parameter_locks) from the Skill
  Registry. The planner never invents tool names; unbound types are
  rejected at planning (`PLAN_CONTRACT` family).
- Executor lookup: `provider.has(tool)` → unknown tool = structured
  `TOOL_NOT_IMPLEMENTED` failure, never fabricated.
- ToolResult: unified envelope (outcome
  SUCCESS/EMPTY/UNSUPPORTED/integration_error/validation_error + data +
  evidence_refs + error + next_data_needed) — normalization already
  unified.
- Observation: `project_observation` compact per-kind projection
  already feeds RE_PLAN digests — reused as-is.
- Gaps found (AL-2 scope): (a) NO pre-execution argument contract
  validation; (b) NO argument provenance; (c) no multi-tool +
  failure→RE_PLAN→fallback trajectory E2E.

## 2. AL-2 Implementation (minimal, no framework)
- `REQUIRED_ARGUMENTS` contract map (executor_v2, per step type).
- Pre-execution argument validation: required args resolved via
  `_runtime_arguments`; missing → structured `validation_error`
  ToolResult, step FAILED, tool NOT invoked, observation records
  INVALID_ARGUMENT with missing keys.
- `ToolCallV2.argument_sources` (additive optional field):
  per-argument provenance — `investigation_context`
  (runtime-injected case_id/finding_id), `deterministic_system_value`,
  `planner` (locked args).
- Telemetry: `tool.selected` (with argument_sources when
  prior-observation-derived) added;
  `tool.completed`/`observation.recorded` reused.

## 3. Orchestration Evidence (real trajectories)
- Multi-tool: intake turn (fetch_case, risk_case_fetch tool) →
  follow-up turn (inspect_timeline, finding_drilldown tool) → both
  SUCCESS with distinct observations; finding_id argument for the
  drilldown injected from investigation context (focused finding),
  source recorded.
- Same-tool RE_PLAN regression guard: plan A (fetch_case) completes →
  scripted proposer RE_PLAN → plan_b (inspect_timeline +
  explain_signal, DIFFERENT tools) adopted (revisions 2) → both steps
  execute → STOP completed. Proves RE_PLAN produces a genuinely
  different next action.
- Bounded: iterations ≤ 8 enforced (AlwaysContinue proposer test from
  AL-1 suite re-verified: agent_status in
  completed/stopped_by_limit/blocked, iteration ≤ 8).

## 4. Test Results (by layer) — HONEST STATUS AT DOC TIME

**IMPLEMENTATION IN PROGRESS.** The argument contract validation +
provenance + multi-tool sequencing code has been implemented and the
core validation logic is proven by targeted unit tests, but the
dedicated multi-tool orchestration E2E suite has NOT yet been run to
completion. The test counts in this section are from the
targeted unit/integration runs only:

- Argument contract validation units: PASS (3/3)
- Provenance sources map: PASS (spot-verified in live execution)

Full orchestration E2E (multi-tool sequence, empty→RE_PLAN→fallback,
argument dependency chain) has NOT been executed yet. This is the
primary remaining work for AL-2 closure. The numbers in earlier
sections are targets, not results.

## 5. Remaining for AL-3
- Evaluation harness consuming persisted trajectories (plan_steps +
  observations + decisions + arg_provenance already sufficient).
- Real-LLM planner orchestration quality study (scripted-LLM E2Es
  prove mechanics; real-LLM tool-choice quality is an evaluation
  question, not an implementation one).
