# AL-2 Tool Orchestration — Final QA Report (v2, evidence-based)

**Date**: 2026-09-28 (supersedes the same-day v1 of this report)
**Status**: **AL-2 CLOSED** — all previously-open E2E items now PASS with
real evidence; no new failures; pre-existing baseline reproduced as-is.

**Baseline**: plan rev. 3 §21 AL-2; frozen refinement `9842ec834bdb`
untouched ✓; flags default OFF in code ✓ (tests set them ON explicitly via
monkeypatch); no MCP ✓; RP reachable at `http://localhost:8000` during all
runs; LLM boundaries scripted (no real-LLM claims).

## Root cause of the earlier E2E blocker (fixture, not runtime)

The previous E2E attempts misdirected the fixture: they wired
resolver/planner into `InvestigationService` (the `/turn` path) while the
goal loop actually runs through `POST /goal` →
`InvestigationAPI.loop_runner_factory(store)` — which fell through to the
DEFAULT runner (real LLM resolver/planner). Additional defects: non-existent
`InvestigationService(store=…/findings_provider=…)` kwargs (real kwarg is
`task_store`), no proposer injection, no `rp_patches` window covering the
background loop thread, and a leaked `inv_api.api` singleton.

**Fix** (`tests/test_tool_orchestration_e2e_al2_final.py`, rewritten):
deterministic runners injected through the documented
`loop_runner_factory` seam (scripted resolver LLM + scripted planner +
scripted proposer + REAL `ExecutorV2(default_tool_provider())`), fresh
`TaskStoreV2` + `InvestigationAPI` per test, `inv_api.api` saved and
restored in `finally`, `rp_patches` held across start+poll (patching is
process-global, so the background loop thread is covered). Each test
verified order-independent (passes alone and in sequence).

## What changed in this closure

- Rewrote `tests/test_tool_orchestration_e2e_al2_final.py` (5 scenarios, below).
- Deleted the superseded broken attempts `test_tool_orchestration_e2e_al2_v1.py`,
  `test_tool_orchestration_e2e_al2_v2.py` and the scratch script
  `tests/dbg_replan.py` (their unique scenarios — retry cap, iteration
  bound, persistence — are covered by the AL-1 suites, which pass).
- No production code changed in this session; the AL-2 runtime diff
  (argument contract, `argument_sources`, `arg_provenance`, `tool.selected`,
  resume) was already in the working tree and is validated here.

## E2E scenarios — PASS (5/5, over real HTTP)

Run: `pytest tests/test_tool_orchestration_e2e_al2_final.py` → 5 passed;
each also passes in isolation; the file also passes inside the full suite.

1. **S1 Multi-tool orchestration** — `inspect_timeline` (finding_drilldown)
   → `explain_signal` (signal_explain), two DIFFERENT real tools in one
   bounded loop. Verified completed/goal_satisfied; ordered
   `tool.completed` events `["finding_drilldown", "signal_explain"]`;
   2 completed steps; `arg_provenance` trajectory persisted per step.
2. **S2 Argument injection + provenance** — the arguments that reach the
   REAL tool boundary are the executor's context-derived values
   (`finding_id=F3`, `case_id=U00299`, captured at the provider seam);
   focus provenance `agent_resolved` (Stage-1 resolution, not
   planner-invented); per-step `arg_provenance` entries persisted with
   registry parameter-lock arguments.
3. **S3 Failure-driven RE_PLAN** — first plan's step fails non-retryably
   (injected `validation_error`; verified NOT retried: `retries == {}`,
   tool invoked exactly once); scripted RE_PLAN honored; genuinely
   different plan adopted (`plan_revisions == 2`) whose step uses a
   DIFFERENT tool (`inspect_timeline` → `explain_signal`), then converges
   completed, bounded.
4. **S4 Unknown step type** — planner vocabulary violation
   (`teleport_to_answer`) → bounded `failed`/`planning_failure` BEFORE any
   tool invocation (no `tool.called`/`tool.completed` events, zero
   iterations).
5. **S5 Unresolvable goal** — no safely resolvable target → `blocked`/
   `context_unresolved` BEFORE planning; zero tool events; focus never
   fabricated.

## Results by layer (explicit separation)

- **Unit/component**: argument contract validation 3/3 PASS
  (`tests/test_tool_orchestration_al2_v1.py`); AL-1 runtime units PASS
  (`test_agent_loop_al1_v1.py`, `test_agent_loop_resume_al1_v1.py`).
- **Integration/E2E (real HTTP)**: the 5 scenarios above PASS;
  `test_api_investigations_v1.py` PASS (91 passed across the five
  targeted suites together).
- **Browser-validated**: NOT TESTED — backend-only diff; no frontend
  surface changed (no frontend files in the diff; `arg_provenance`/
  `argument_sources` are not projected to the UI). The user-visible Agent
  Activity surface is exercised at the HTTP contract level (`/goal` →
  `/activity`).
- **Exploratory**: scenario coverage includes failure/rejection/blocked
  semantics (S3–S5). Not covered: double-start of `/goal`, unicode/case-ref
  variations at the HTTP boundary — covered for the `/turn` path by the
  API suite, not re-derived here for `/goal`.

## Full-suite regression check

`pytest tests/` (RP up): **1198 passed, 20 failed, 1 skipped, 1 xfailed**.

- 8 of the 20 failures were the since-deleted broken `al2_v1`/`al2_v2`
  attempts (collected before deletion).
- The remaining **12 are pre-existing**: reproduced identically with the
  working-tree diff stashed (`test_error_handling` ×3 — note one of the
  two `TestAgentExecutionErrors` cases flip-flops between runs;
  `test_evidence_integration` ×3; `test_provenance_model` ×6). They target
  `app.agent.InvestigationAgent` (legacy pre-V2, untouched) and the
  provenance composer — matches the documented ~12-failure RP-up baseline.
- **Regressions: none.**

## Known limitations / unverified areas

- The `prior_observation` provenance category fires only for steps whose
  step-level arguments match ids surfaced by the previous observation;
  PlannerV2-emitted steps carry registry parameter locks, so in scripted
  E2E plans the loop-level `arg_provenance.sources` is empty (context
  injection proven at the tool boundary instead, S2). Category remains
  unit-level logic.
- The INVALID_ARGUMENT contract is unreachable via the real `/goal` path
  by design (a focus-less finding-level request BLOCKs before planning);
  it is unit-tested and guards direct executor use.
- Rejected (missing-argument) calls construct a FAILED `ToolCallV2` that
  is intentionally not persisted (no invocation occurred); the audit
  evidence lives on the step status/error, the `ExecutorError`, and the
  observation. Minor audit-completeness nit for a future iteration.
- Real-LLM planner/resolver quality is an evaluation question (AL-3
  harness), explicitly out of scope here.

## Acceptance status

- AL-2 implementation: **Validated** (units + real-HTTP integration E2E).
- AL-2 closure criteria from the previous QA report: multi-tool E2E ✓,
  failure-driven RE_PLAN E2E ✓, argument provenance E2E ✓, fixture
  isolation ✓.
- Gate for AL-3 (per prior report): satisfied — persisted trajectories
  (`plan_steps` + `observations` + `decisions` + `arg_provenance`) are
  available for the evaluation harness.
