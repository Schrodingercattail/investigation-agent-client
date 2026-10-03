# AL-2.5 — Real RP Data + Browser E2E (Minimal Real Closure)

**Date**: 2026-09-28
**Final status**: **PASS WITH LIMITATIONS** (see §14)

---

## 1. Objective

Prove, over the REAL running stack with REAL Risk Platform data, the full
chain:

Browser → Investigation Agent Client UI → Investigation API → Agent Loop
Runtime → Planner → governed STEP_TYPE → Executor / Tool Registry →
actual RP API → actual RP data → ToolResult → Observation → Agent
continuation / STOP → persisted investigation state / result → Browser
displays the resulting user-visible investigation state.

This is integration acceptance, not new feature development. No AL-3 work
was started.

## 2. Scope

IN: one minimal golden journey (below), API-level real-RP E2E, real
browser E2E, persistence verification, anti-mocking verification, minimal
fixes for defects the journey exposed (each with a regression test).
OUT: trajectory evaluation, metrics, dashboards, loop/orchestration
redesign, candidate-metadata/Safe-Select changes, re-opening AL-2.

## 3. Environment

| Component | Value |
|---|---|
| Risk Platform (external repo, untouched) | `http://localhost:8000` — `GET /api/risk/cases/{id}/evidence`, `POST /api/risk/explain` verified 200 |
| Agent Client backend | `uvicorn app.main:app` on `127.0.0.1:8001`, current working-tree code; restarted for this work item (the previously running process predated AL-2 changes — stale-service finding) |
| Backend LLM configuration (GATE config) | `ANTHROPIC_API_KEY=""` — documented deterministic-fallback configuration (docker-compose default). Rationale in §11 |
| Frontend | Vite dev server `:3001`, proxies `/api` → `:8001` (`frontend/vite.config.ts`) |
| Browser | Real headless Chrome via the project harness `qa/browser.py` (Chrome DevTools Protocol) |
| Store | TaskStoreV2 (SQLite) — real persistence, real reads |

## 4. Real RP Dataset / Case Used

Case **`U00299`** — stable seeded case in the local RP instance, probed
live (values below fetched directly from RP during this session):

- `risk_summary`: HIGH, risk_score 72.12, ml_score 96.24, detected 2026-09-01
- `rule_evidence`: "Coordinated Trading Pattern", "High withdrawal
  frequency", "First withdrawal to new address"
- `transaction_evidence`: 5 records (e.g. T024429, BTC SELL …),
  `withdrawal_evidence`: 5 records (e.g. W002214, USDT 7.93 …)
- `feature_evidence`: withdrawal_frequency_24h=14, opposite_trade_ratio=0.4524 …
- `POST /api/risk/explain` → 4 `key_findings` (normalized F1–F4) + 3
  validated citations (AML_Suspicious_Indicators.md, …)

Why it fits the golden path: stable, finding-complete, both golden-path
tools (`risk_case_fetch`, `artifact_bundle`) read it through the real
adapter, and the UI renders its findings/bundles. RP production dataset
was NOT modified.

## 5. Actual End-to-End Path (Golden Journey)

1. Browser `:3001` → "New Investigation" → `POST /api/v2/investigations`
   `{case_id: U00299}`.
2. Conversation turn "Investigate U00299" → `/turn` → real
   `risk_case_fetch` (2 real RP HTTP calls) → the UI renders the four
   REAL findings (asserted against a live RP oracle, not fixtures).
3. Bounded Agent Loop: goal "Generate the investigation bundle for
   U00299" → `POST /api/v2/investigations/{id}/goal` → AgentLoopRunner
   (production default factory) → deterministic ARTIFACT_CASE intent →
   plan `[fetch_case, generate_artifact]` (governed STEP_TOOL_MAP) →
   ExecutorV2 → real tools → real RP HTTP → ToolResult → Observation →
   heuristic decision (CONTINUE → STOP, goal_satisfied) → TaskStoreV2
   persistence (task + agent state + session artifact read model).
4. Browser shows the terminal Activity (goal, plan ✓✓, per-tool events,
   artifact ref) and the conversation shows the real-data response; after
   reload + reopen, the Artifacts tab and the Activity history are
   restored from persisted state.

## 6. API E2E Evidence

`backend/tests/test_al25_real_rp_api_e2e_v1.py` — real HTTP only (httpx
→ :8001; RP oracle fetch → :8000). **PASS, run twice (2.15s / 2.12s —
repeatable).** Asserted: terminal `completed`/`goal_satisfied`;
`fetch_case` step success; `risk_case_fetch` in completed tools;
iteration ≥ 2; ≥1 persisted artifact whose body contains EVERY live-RP
oracle value (case id + all four finding names — dynamic comparison, no
hardcoded expectations); task-detail (reopen surface) serves the same
artifact.

## 7. Browser E2E Evidence

`qa/journey_al25_real_rp_loop.py` (real headless Chrome, established
project harness). **PASS, run twice.** Verified in the real UI:

- Real RP finding title (fetched live from the RP oracle by the script)
  rendered in the findings panel.
- Loop terminal: "Investigation completed (goal satisfied)", iteration
  2/8, plan ✓ fetch case ✓ generate artifact, artifact ref visible
  (`data-testid=agent-artifact-ref`), full event trace rendered.
- 50 network requests from the page, ZERO network failures.
- After reload + reopen: `Artifacts (2)` (the turn bundle AND the
  loop-produced bundle), and the Activity tab restores the completed run
  read-only (goal, status, iteration, plan, 16 activity items).
- Screenshots: `qa/screenshots/al25-01-real-findings.png`,
  `al25-02-loop-terminal.png`, `al25-03-reload-restored.png`.

## 8. Anti-Mocking / Authenticity Checks

- No RP mocks/patches: the E2E API test and the browser journey import NO
  repo fixtures and apply NO patches (grep-verified; only docstrings
  mention mocks, to prohibit them). The runtime under test is the real
  uvicorn process; the loop ran through the production default
  `loop_runner_factory` (no scripted planner/proposer injection anywhere).
- Real tool registry/executor: the plan executed the governed
  `STEP_TOOL_MAP` bindings (`fetch_case`→`risk_case_fetch`,
  `generate_artifact`→`artifact_bundle`) through the default
  `ExecutorV2`/`ToolProvider`; no test provider was registered.
- Real RP HTTP: the backend log records the adapter's live calls
  (`GET /api/risk/cases/U00299/evidence`, `POST /api/risk/explain` 200)
  during the E2E runs; the browser captured 50 successful `/api/`
  requests with zero failures.
- Real RP data: assertions compare against a LIVE RP oracle fetched by
  the test itself; nothing is hardcoded from repo fixtures. (Note, in the
  interest of full honesty: repo unit-test fixtures contain RP-shaped
  seeded values from the same data generator family — but the AL-2.5 E2E
  path never imports them, and the runtime receives data only via real
  HTTP.)
- `make_findings()` / `rp_patches` were NOT used anywhere in AL-2.5 code.

## 9. Persistence Verification

- Task + agent state: persisted by the loop and served by
  `/tasks/{id}/activity` (terminal status, plan snapshot, artifact ids,
  full trajectory).
- Artifact BODIES: mirrored into the investigation session read model
  (fix #1, §11) and served by `GET /tasks/{id}/artifacts` and the task
  detail (reopen) surface — verified by API E2E and by the browser reopen
  (`Artifacts (2)`).
- Browser reopen restored the completed Activity history read-only.

## 10. Results

| Layer | Result |
|---|---|
| API E2E (real RP, no mocks) | PASS ×2 |
| Real browser E2E (real RP, no mocks) | PASS ×2 |
| Frontend unit tests (incl. new polling regression) | 20/20 PASS |
| Backend new regressions (`test_loop_artifact_persistence_v1.py`, `test_nokey_startup_v1.py`) | PASS |
| Full backend suite | see §15 addendum |

## 11. Failures and Classification (observed during this work item)

1. **Loop artifacts not persisted** (boundary: runtime→read model). The
   loop persisted artifact IDS only; bodies existed solely inside the run
   thread, so `GET /tasks/{id}/artifacts` and reopen served nothing for
   loop runs. **Product bug — FIXED minimally**: `AgentLoopResult`
   now carries produced artifact bodies; the API goal-run/resume path
   mirrors them into the session read model (same surface the turn path
   records). Regression: `tests/test_loop_artifact_persistence_v1.py`.
2. **Backend could not start without an LLM key** (boundary: startup
   wiring). `api = InvestigationAPI()` eagerly constructed
   `ClaudeProvider()` inside the default refinement/admissibility
   factories → `LLMConfigurationError` crashed startup, although every
   consumer has a documented deterministic fallback. **Product bug —
   FIXED minimally** (`_default_llm_or_none()`); regression:
   `tests/test_nokey_startup_v1.py`.
3. **One transient 404 permanently killed the live Activity polling**
   (boundary: frontend polling). The first poll can precede the loop
   thread's first persistence; `useAgentActivity` then stopped scheduling
   ticks forever ("No Agent Loop run yet" while the run was executing).
   **Product bug — FIXED minimally** (bounded retry: 5 consecutive empty
   polls before settling into notFound). Regression: new vitest case
   (20/20 PASS).
4. **External LLM (api.z.ai / glm-5.3) unstable during the session** —
   environment problem. Two manual full-LLM runs completed earlier
   (16:33 and ~18:45, real LLM decisions, real artifacts); afterwards the
   endpoint timed out and the anthropic SDK retried for minutes. The gate
   therefore runs in the documented no-key deterministic configuration
   (docker-compose default; every fallback is production code). NOT
   hidden, NOT worked around with a fake LLM.
5. **QA harness (not product): Radix TabsTrigger ignores synthetic
   `click()`** — the CDP harness now dispatches `mousedown` first
   (`qa/journeys_common.py`). Test-harness fix only.

### Open Issues recorded (deliberately NOT fixed — scope discipline)

- **OI-1 (product decision needed)**: with the deterministic proposer, an
  intake goal ("Investigate U00299") stops at iteration 1 —
  `question_answered` criteria are satisfied by the first success — so
  the deterministic intake plan's `generate_artifact` step never runs,
  contradicting the planner's documented intake contract ("the UI's
  'Check investigation bundle in the Artifacts' action always has an
  artifact to show"). With the LLM proposer it continues (proposes
  CONTINUE). Fixing requires a criteria-semantics decision (e.g.
  `artifact_produced` when the adopted plan contains `generate_artifact`)
  — out of AL-2.5 scope. The golden path avoids it by using the explicit
  bundle request (ARTIFACT_CASE intent → `artifact_produced` criteria).
- **OI-2**: LLM-planner-only surfaces (free-text finding-level
  investigations, follow-up-driven drilldowns) were not validated in this
  gate because the external LLM was unavailable; they remain covered by
  AL-2's scripted-LLM suites and belong to real-LLM evaluation (AL-3).
- **OI-3**: the LIVE Artifacts tab does not re-fetch after a loop run
  completes (the UI reads artifacts from turn responses and reopen); the
  loop artifact is user-visible in-loop via the Activity artifact
  reference and fully visible after reopen. Possible UI enhancement, not
  required by the DoD ("real investigation result OR Agent Activity").

## 12. Known Limitations

- The gate configuration runs without an LLM (deterministic fallbacks).
  Full-LLM behavior is evidenced only by the two manual smoke runs before
  the endpoint went down.
- The golden journey is case-level (fetch_case → generate_artifact);
  finding-level drilldown over real RP requires the LLM planner (see
  OI-2) and is NOT part of this closure.
- RP is a local seeded instance; findings/evidence values are stable but
  the assertions are oracle-dynamic, so re-seeding RP does not break the
  tests.

## 13. Definition of Done

| Requirement | Status |
|---|---|
| Real RP instance running | PASS (:8000 verified) |
| Agent Client running (current code) | PASS (restarted for this item) |
| API E2E | PASS ×2 |
| RP request is a real HTTP request | PASS (backend log + zero failed page requests) |
| Real RP data used | PASS (dynamic oracle correspondence) |
| Real Agent Loop runtime | PASS (production default factory, no injection) |
| Real ExecutorV2 | PASS |
| Real tool registry / governed mapping | PASS (STEP_TOOL_MAP bindings executed) |
| Task / investigation state persisted | PASS (task, agent state, artifact read model) |
| Browser real user journey completed | PASS ×2 |
| Browser shows this run's user-visible result | PASS (terminal Activity + artifact ref; restored after reload) |
| No mock / patch / fake RP made the tests pass | PASS (§8) |
| Repeatable | PASS (both E2Es run twice) |
| No production logic expanded for the test | PASS (fixes are defect fixes at failing boundaries, each with a regression test) |
| Existing regression tests accounted for | PASS (§10/§15; pre-existing 12 legacy failures reproduced unchanged) |
| Formal QA artifact | this document |

## 14. Final Status

**PASS WITH LIMITATIONS** — the minimal real closure (real RP data +
Agent runtime + persistence + real browser journey) is demonstrated and
repeatable; the limitations are the external-LLM dependency (gate ran in
the documented no-key configuration; OI-2) and the recorded Open Issues
OI-1/OI-3, none of which blocks the demonstrated chain.

## 15. Addendum — Full backend suite after AL-2.5 changes

**1199 passed, 12 failed, 1 skipped, 1 xfailed** (16m43s, RP up). The 12
failures are exactly the pre-existing environment-dependent legacy
baseline (reproduced identically with the AL-2 working tree stashed
earlier the same day): `test_error_handling` ×3, `test_evidence_integration`
×3, `test_provenance_model` ×6 — all targeting `app.agent.InvestigationAgent`
(legacy pre-V2, untouched) and the provenance composer. **No new
failures; no regressions from the AL-2.5 changes.**
