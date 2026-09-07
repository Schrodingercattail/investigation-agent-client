# Week 1 Checkpoint

**Status**: BASELINE RECORD — Week 1 freeze
**Date**: 2026-09-07
**Runtime baseline**: commit `444d94e` ("fix: finalize Docker release packaging"), which includes the runtime/evaluation freeze at `1626df2` ("feat: freeze week 1 agent runtime and evaluation")

---

## 1. Purpose

This document freezes the Week 1 implementation baseline of the
Investigation Agent Client and defines the boundary that subsequent
development starts from. It records what is actually implemented in the
repository at this commit, what was validated during the Week 1
validation process, and which limitations are knowingly carried forward.

It is a baseline record, not a design proposal: nothing in this document
describes planned or aspirational capabilities as if they were present.
Where a capability is absent, it is listed as a limitation.

---

## 2. Product Baseline

The Investigation Agent Client is a **case-centered investigation
assistant for risk analysts**. It runs on top of an existing Risk
Platform, which remains the authoritative source for risk signals,
evidence, policy citations, and case explanations. The Agent Client adds
the investigation layer on top of that data.

The core Week 1 workflow is:

```
Case Intake → Focus Mode → Artifacts
```

1. **Case Intake** — the analyst opens a case ("Investigate U00299");
   the Agent fetches the authoritative case context from the Risk
   Platform, identifies the findings, and produces a case-scoped
   investigation bundle.
2. **Focus Mode** — the analyst selects a finding (F1, F2, …); the
   investigation conversation and suggested next steps become
   finding-scoped.
3. **Artifacts** — the analyst generates investigation bundles
   (case-scoped or finding-scoped) that are composed deterministically
   from executed tool results and carry provenance.

---

## 3. Implemented Runtime

The runtime is implemented in `backend/app/`:

| Component | Implementation | Role |
|---|---|---|
| Domain models | `app/models.py` | `Investigation`, `InvestigationContext`, `Finding` (+ `FindingCapability`), `TimelineEvent`, `Plan`/`PlanStep`, `ToolCallV2`/`ToolResult`, `ArtifactV2`, `TaskV2`. A Task references a Plan; a Plan contains PlanSteps; executed steps produce ToolCalls and Artifacts. |
| Context resolution | `app/context_resolution.py` | Deterministic-first resolution of what a request refers to (focus state, case references, capability questions) before planning. LLM-assisted resolution only among known candidates when deterministic matching cannot safely resolve. |
| Skill registry / contract checking | `app/skills.py` | Declarative skill definitions (`case_intake`, `timeline_investigation`, `trade_investigation`), planning vocabularies, step→tool bindings with parameter locks, capability gating, and the deterministic `check_plan` contract checker (used planner-side and again executor-side). |
| Planner | `app/planner_v2.py` | **LLM-assisted planning within runtime contracts**: the LLM selects one skill and orders step types with reasons; tool names and parameters are resolved deterministically from the registry, and the plan is validated by the contract checker before use. Planning is not fully deterministic — the LLM output is validated, and bounded `PlanningFailure` outcomes are preserved. |
| Executor | `app/executor_v2.py` | Validates the plan again at runtime (defense in depth), injects structural arguments (case/finding/stream/detector identity) from explicit context, and executes steps **only through the ToolProvider boundary** — no unrestricted tool selection, no autonomous retries. Emits optional telemetry events (observational only). |
| Risk Platform adapter | `app/adapters/risk_platform.py` | Single boundary to the Risk Platform: `GET /api/risk/cases/{id}/evidence` and `POST /api/risk/explain`. Normalizes RP responses into domain structures; distinguishes valid-empty from upstream failure; never fabricates evidence or citations. |
| Domain tools | `app/domain_tools/` (package: `risk_case_fetch`, `finding_drilldown`, `signal_explain`, `policy_lookup`, `artifact_bundle`) | The five Week 1 tools, each returning a normalized `ToolResult` with the outcome set (`success` / `empty` / `unsupported` / `integration_error` / `validation_error`). `finding_drilldown` supports `timeline` and `evidence` views with explicit evidence-stream scopes (`withdrawals` / `transactions`); opposite-trade requests are a distinct, capability-bounded operation. |
| Task Store V2 | `app/task_store_v2.py` | SQLite-backed persistence for tasks, plans, tool calls, artifacts, and investigation sessions (`data/investigation_agent_v2.db`). |
| Response composition | `app/investigation_service.py` | Deterministic composition of tool results into user-facing responses and the orchestration of one investigation turn (resolve → plan → execute → compose → follow-ups → persist). |
| Telemetry extension point | `app/telemetry.py` | Thin, observational, non-authoritative event contract with a no-op default sink (see the Evaluation section). |

---

## 4. Frontend Baseline

The frontend (`frontend/`) is a React 18 + TypeScript + Vite workspace:

- **Investigation workspace** (`InvestigationPage`): three-panel layout
  combining investigation navigation/history and findings, the
  chat-style Agent conversation, and the current case/finding context
  (Context and Artifacts are separate tabs).
- **Chat-style conversation**: user and Agent turns; Agent turns carry
  the structured execution state (Plan, Task, skill, structured result,
  artifact, suggested next steps) rather than hiding execution behind an
  opaque reply.
- **Artifact rendering** (`ArtifactPanel`): renders backend-produced
  Markdown bundles with artifact scope/type metadata, source-call
  counts, and a type-safe narrative (risk-summary) rendering.
- **Focus acknowledgment**: finding/event selections are persisted as
  conversation turns and survive follow-up clicks, free-form turns,
  refresh, and restore; the intake "Select a finding…" guidance is
  driven by the current focus state and is no longer shown once a
  finding is focused.
- Conversation auto-scroll is keyed to rendered turn changes.
- **Frontend TypeScript build (`npm run build`) passes with zero
  TypeScript errors** after resolving the pre-existing type/export
  issues (`api.ts` redundant type re-export; `ArtifactPanel` narrative
  narrowing via the existing `RiskSummary` type).

---

## 5. Docker / Deployment Baseline

Docker packaging provides a **reproducible local deployment
environment**:

```
Browser
  ↓
Frontend (nginx: static build + /api reverse proxy)   port 3001
  ↓ /api
Backend (FastAPI / Uvicorn)                            port 8001
  ↓
Risk Platform (external, host-run)                     port 8000
```

- `frontend/Dockerfile`: two-stage build — `node:20-alpine` runs the Vite
  production build, `nginx:1.27-alpine` serves `dist/` and reverse-proxies
  `/api` to the backend service (same-origin routing).
- `backend/Dockerfile`: `python:3.12-slim`, installs
  `requirements.txt`, runs `uvicorn app.main:app --host 0.0.0.0
  --port 8001` (mirroring `backend/start.sh`).
- `docker-compose.yml` deploys **frontend and backend only**. The
  **Risk Platform is an external dependency and is NOT included** in
  Compose; it must be running and reachable, with
  `RISK_PLATFORM_BASE_URL` (default
  `http://host.docker.internal:8000`) pointing at it via
  `host.docker.internal` / `host-gateway`.
- The backend exposes a `/health` endpoint used as the Compose
  healthcheck; the frontend waits for the backend to become healthy.
- `backend/requirements.txt` includes `anthropic==0.125.0` — added for
  dependency completeness, because the runtime imports the Anthropic
  client (`app/llm_provider.py`). This is packaging correctness, not an
  architecture change.
- `.dockerignore` files exclude local environment files, virtual
  environments, caches, local databases, and tests from the images.

---

## 6. Validation Status

| Area | Validation | Status |
|---|---|---|
| V2 runtime/domain/skill/planner/executor/task-store tests | `pytest tests/` (757 passed, 1 skipped, 1 xfailed for the known P09 planner limitation, 0 failed) | PASSED |
| Frontend TypeScript build | `npm run build` (tsc + vite) — zero TypeScript errors | PASSED |
| Backend Docker image build | `docker compose build backend` (`python:3.12-slim`) | PASSED |
| Frontend Docker image build | `docker compose build frontend` (`node:20-alpine` → `nginx:1.27-alpine`) | PASSED |
| Docker Compose startup | `docker compose up` — frontend and backend containers running | PASSED |
| Backend healthcheck | `/health` returned healthy under Compose | PASSED |
| Frontend HTTP availability | frontend endpoint returned HTTP 200 under Compose | PASSED |
| Dockerized agent conversation | a real agent conversation (case intake → investigation) completed successfully through the Dockerized deployment | PASSED |

In addition, the Week 1 evaluation framework (`backend/eval/`) is
implemented and was run:

- Runtime Semantic Evaluation: 15/15 deterministic scenarios — 100%
  semantic success.
- Planner Evaluation (real configured LLM, 3 runs per scenario):
  27/30 = 90.0% run-level; 9/10 scenarios perfectly stable; P09 0/3
  (see Known Limitations).
- Live E2E Evaluation: 5/5 — 100% (real LLM + real Risk Platform,
  including a cold-start case where the Risk Platform generated and
  persisted the explanation on demand, `explanation_source=LLM`).

See `docs/architecture/AGENT_EVALUATION_V1.md` for the evaluation
model.

---

## 7. Known Limitations

- **Container-local task history (persistence limitation):** task and
  investigation history is stored in SQLite at
  `/app/data/investigation_agent_v2.db` inside the backend container.
  The current Docker Compose configuration does **not** mount a
  persistent volume for this database, so task/investigation history is
  not preserved across backend container recreation. This is a known
  persistence limitation carried into the next phase, not a defect that
  has been fixed.
- **P09 (known Planner limitation):** the compound natural-language
  request "Switch to finding F2 and show me its timeline." produced
  0/3 valid plans (`INVALID_LLM_OUTPUT`) in the Planner Evaluation.
  The focus/context mechanism itself works; the limitation is in the
  Planner's handling of compound requests. Intentionally retained at
  freeze and tracked for later improvement.
- The opposite-trade evidence view is registered but not implemented;
  opposite-trade requests are recognized as a distinct semantic request
  and bounded as unsupported instead of silently substituting other
  evidence.
- No persistent production telemetry store exists; the telemetry
  extension point ships with a no-op default sink (in-memory sink for
  tests) and is not yet wired to any backend.
- The Risk Platform is required and external; the Agent cannot produce
  investigation results without it.

---

## 8. Week 1 Scope Boundary

Week 1 **establishes**:

- the first functional agent runtime (resolve → plan → execute →
  compose → persist) over Risk Platform data,
- explicit tool / skill / plan / executor boundaries with contract
  checking and capability gating,
- the five Week 1 domain tools and the scoped artifact composition
  contract,
- the investigation workspace UI with conversation, focus handling, and
  artifact rendering,
- Dockerized local deployment (frontend + backend) with the Risk
  Platform as an external dependency,
- the evaluation framework and the Week 1 validation baseline.

Week 1 does **not** attempt to solve:

- persistent, production-grade task storage (container-local SQLite
  only; no Compose volume),
- unrestricted or autonomous agent behavior — planning is constrained
  to the skill registry and execution is bounded by explicit contracts,
- inclusion of the Risk Platform inside this repository (it remains a
  separate, external system),
- production telemetry storage, LLM-as-a-judge evaluation, or
  production-derived evaluation datasets (the telemetry contract and
  evaluation layers are the extension points for these),
- the P09 compound-request planner limitation.

---

## 9. Baseline Summary

This document represents the Week 1 baseline of the Investigation Agent
Client: a working, Docker-deployable, case-centered investigation agent
with an LLM-assisted (constrained) planner, contract-checked execution,
five domain tools over the Risk Platform, scoped artifact generation,
a React investigation workspace, an implemented three-layer evaluation
framework with the recorded Week 1 results, and the known limitations
listed above. Subsequent development starts from this state and should
treat this document as the reference point for what existed at the Week 1
freeze.
