# Goal Understanding Decision Observability & Runtime Verification Gate — Design V1

Status: **ACCEPTED / PASS (2026-10-24 final delivery audit; see §23
Acceptance closure).** Implemented 2026-10-24 under user-approved
authorization; live-verified (§21, §22). Original design date: 2026-10-24

**Overall program status: SCOPE COMPLETE (residuals accepted)** —
2026-10-24, by explicit user decision (residual acceptances recorded in
§25). B1, P1-1 and P1-2 are ACCEPTED / PASS (§24 Overall status). This
status means the approved scope is complete with formally accepted
residuals — it does NOT mean all risks are eliminated and must not be
read as COMPLETE. §24 remains the factual record of the residuals; §25
records the user's acceptance decisions.
Scope: backend/app/{api,agent_loop,goal_resolution,investigation_service,telemetry}.py
(analysis) + qa/preflight_runtime.py (extension design). No code changed in
this document's preparation.
Related norms: `CLAUDE.md` (runtime/code consistency preflight §, bounded
execution & truthful reporting §, agent-specific gate §),
`docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md` (runtime architecture),
`qa/preflight_runtime.py` (existing preflight implementation).

---

## 1. Background

Two field incidents motivated this design:

1. **2026-10-23 live defect (TASK-3a6c011cf659)**: a contractless RE_PLAN
   crashed on an unguarded `state.goal_contract.outputs` dereference
   (fixed separately). Forensics were possible only because
   `goal_contract_decision` happened to be persisted.
2. **2026-10-24 runtime configuration loss**: the backend restart at
   14:08 inherited only secret-bearing env vars; the
   `ENABLE_GOAL_SEMANTIC_CONTRACT` flag (config.py:102, default False
   **as of 2026-10-24** — current code has it at config.py:115 with
   default True since the 2026-11-02 calibration; see
   `FEATURE_FLAG_INVENTORY.md` §1)
   was enabled in the previous process by an ephemeral shell export.
   Three browser journeys executed afterwards ran with the contract
   system silently disabled; `goal_contract_decision` was `null` and the
   runs were initially mis-attributed to "LLM variance". Recovery (P0)
   restored the flag via `backend/.env`; a single real turn (P0.1,
   TASK-eba0b3c297c7) re-confirmed `contract_accepted` end-to-end.

Both incidents reduce to the same deficiency: **goal-understanding and
contract decisions are not durably observable, and no gate verifies that
the feature under test was actually enabled in the running process.**

## 2. Verified Current Call Chain

All anchors re-verified against the working tree on 2026-10-24
(git a52cb89 + dirty tree; HEAD is NOT the source baseline — see §6.1).

```
POST /api/v2/investigations/{id}/submit          api/investigations.py:1189
  → InvestigationService.run_turn()              investigation_service.py:2223
      goal = self.goal_resolver(                 # 3-ARG CALL — GAP G1
          user_request, focused_finding_id, case_id)
      → goal_resolution.understand_goal(         goal_resolution.py:1470
            ..., diagnostics=None)               # provenance discarded
          ├ candidate = deterministic_contract_proposal(...)   :1235
          │   (only when _gsp_contract_enabled(); closed-vocab
          │    aspect predicates, investigation_concepts.py)
          ├ LLM classification + review of the ORIGINAL request
          │   against the candidate (candidate is a PRIOR, never
          │   the contract)                     [LLM decides the set]
          ├ closed-vocabulary tag → canonical output
          │   substitution ("reverse_trade_metric_semantics" →
          │   _metric_semantics_output)          [system substitution]
          └ bounded heuristic fallback (llm_empty path) [system]
  → api/investigations._start_loop(
        goal_representation=precomputed rep)     agent_loop.py:1854
      runner.goal_resolver = lambda ...: rep     # loop-internal resolver
                                                 # call skipped (G1 effect)
  → agent loop run
      if self.goal_resolver is not None:         agent_loop.py:1845
          produced = _call_goal_resolver(..., goal_diag, ...)  # loop path
          if goal_diag: state.goal_understanding_diagnostics = ... :1862
      if ENABLE_GOAL_SEMANTIC_CONTRACT:          # FLAG GATE  :1869
          contract, reason = build_goal_contract(produced, case_id)
          state.goal_contract = contract          # accept-or-reject
          _telemetry.emit_event("goal_contract.accepted|rejected", ...)
          decision = {...}  # contract_accepted / no_proposal /
          state.goal_contract_decision = decision  # invalid_proposal:<code>
                            # / llm_empty / heuristic_single_goal
                            # / ambiguous_not_executed     :1918-1965
      composer may read state.goal_understanding_diagnostics  :6330
      facet rendering consumes contract outputs  :5939 → :5696
```

Second instance of G1: the refinement path calls the resolver with the
same 3-arg convention (investigation_service.py:1935).

### LLM vs system boundary

| Step | Decider |
|---|---|
| Whether the request demands a deliverable aspect | LLM (tag) or deterministic predicate (candidate) — candidate is only a prior; the LLM review may override |
| Canonical output wording / scope / evidence binding | System (closed-vocabulary substitution) |
| Contract acceptance | System (`build_goal_contract`, accept-or-reject, no repair) |
| Facet mounting | System (part of the accepted contract output) |
| Facet rendering | System (lang-parameterised renderer, agent_loop.py:5696) |

## 3. Observability Gaps

| ID | Gap | Evidence |
|---|---|---|
| G1 | `run_turn` (and the refinement site) call the resolver WITHOUT the `diagnostics` out-param; `understand_goal` therefore records nothing; the loop receives a precomputed representation and its own diag capture never fires | investigation_service.py:2223, :1935; agent_loop.py:1861-1862 |
| G2 | The whole decision block (and candidate generation) is flag-gated; flag-OFF produces `goal_contract_decision = None`, which is indistinguishable from "path not run" | agent_loop.py:1869; live incident §1.2 |
| G3 | Candidate fired / candidate overridden by the LLM review / canonical substitution are computed inside `understand_goal` but never exposed | goal_resolution.py:1235-1260 |
| G4 | Telemetry `goal_contract.accepted/rejected` events exist but the default sink is a Noop (production) and sinks are in-memory only — no post-hoc forensics | telemetry.py:156 (`_sink = NoopTelemetrySink()`), :169 |
| G5 | The composer's honest failure-class disclosure reads `goal_understanding_diagnostics`; on the production path it is always null, so the disclosure degrades to a generic face | agent_loop.py:6330 |

Persisted today (via `TaskStoreV2` agent_states documents, INSERT OR
REPLACE — task_store_v2.py:244): `goal_representation` (7-key projection),
`goal_contract_decision` (flag ON only), `goal_contract`, 
`goal_understanding_diagnostics` (null on the production path).
NOT recoverable post-hoc: candidate generation/override, canonical
substitution, LLM attempt detail, effective flag state at turn time.

## 4. B1 — Minimal Decision Observability (design)

### 4.1 Carrier: extend the EXISTING record — no new tables

`goal_contract_decision` is already the persisted decision record. B1
attaches one `understanding` sub-object to it, and — critically — the
record is written EVEN WHEN the flag is OFF (today flag-OFF writes
nothing at all). No new database, no second state system, no global
tracing. `goal_understanding_diagnostics` keeps its existing role
(loop-internal full provenance, including llm attempts) and gains the
production path via G1's fix.

### 4.2 Proposed schema `gu-decision/1` (additive; older readers unaffected)

```jsonc
"goal_contract_decision": {
  "outcome": "contract_accepted | rejected:<code> | no_proposal | "
           | "llm_empty | heuristic_single_goal | ambiguous_not_executed "
           | "flag_off",                       // NEW enum value
  "reason": null | "<code>",
  "understanding": {                            // NEW sub-object
    "schema": "gu-decision/1",
    "flag_enabled": true,                       // effective value
    "flag_source": "env_file:backend/.env",     // declared provenance
    "path": "run_turn | loop_internal | not_run",
    "llm_attempted": true,
    "llm_error": null,
    "candidate": {"stage_ran": true, "fired": true,
                  "kinds": ["reverse_trade_metric_semantics"]},
    "canonical_substitution": {"occurred": true,
                               "tags": ["reverse_trade_metric_semantics"]},
    "contract": {"decision": "accepted", "contract_id": "GC-…"},
    "facets_mounted": ["reverse_trade_metric_semantics"],
    "record_complete": true
  }
}
```

Field dispositions (new / reuse / not added):

| Field | Disposition | Rationale |
|---|---|---|
| `outcome` | REUSE + one new enum `flag_off` | today flag-OFF = absent; an explicit value removes the None ambiguity |
| `understanding.flag_enabled` / `flag_source` | NEW | G2; turns the incident class detectable |
| `understanding.path` | NEW (derived) | distinguishes run_turn vs loop-internal resolver invocation |
| `understanding.llm_attempted` / `llm_error` | REUSE — already produced by `understand_goal` (goal_resolution.py:1533-1535), only never plumbed | zero new logic |
| `candidate.stage_ran` / `fired` / `kinds` | NEW but locally computed | `deterministic_contract_proposal` already returns the structured candidate; kinds are its own closed vocabulary — no new vocabulary invented |
| `canonical_substitution` | NEW but locally computed | the substitution loop already knows which tags replaced |
| `contract` / `facets_mounted` | REUSE (mirror of existing decision + contract) | correlation convenience |
| `record_complete` | NEW | distinguishes partial writes; never back-filled with speculation |
| Full prompt / raw LLM output / CoT | NOT ADDED | privacy & storage discipline (mirrors telemetry.py:170-172) |
| New telemetry sink / event store | NOT IN B1 (→ B2, future) | not needed for the current requirement |

### 4.3 State semantics — admission rules (no inference from outcomes)

| State | Required evidence (all recorded at the moment it happens) |
|---|---|
| `flag_off` | effective flag false at decision time; candidate stage skipped; decision recorded with outcome `flag_off` |
| `path_not_run` | resolver invocation skipped (e.g. no resolver configured); `path=not_run`; nothing inferred about candidates |
| `no_candidate` | candidate stage RAN and returned None (`stage_ran=true, fired=false`) |
| `overridden` | candidate fired AND its kinds absent from the final demanded set — computed by comparing the candidate kinds to the post-review outputs inside `understand_goal` (deterministic comparison, no LLM-output storage) |
| `rejected` | `build_goal_contract` returned None with a reason code (existing `invalid_proposal:<code>`) |
| `accepted` | existing `contract_accepted` |
| `diagnostics_missing` / write failure | carrier record written with `record_complete=false` (+ reason); a thrown diagnostic-write error MUST be swallowed — the business result is computed exactly as before |

Explicitly NOT inferable (and never fabricated): "the LLM considered and
rejected a candidate" as a mental event — B1 records only the
deterministic comparison result (kinds present vs absent).

### 4.4 Change surface (B1)

| File | Change | Size |
|---|---|---|
| investigation_service.py | pass a diagnostics dict to BOTH resolver calls (the resolver convention `_call_goal_resolver` already supports it); attach the summary to the loop handoff | ~6 lines |
| agent_loop.py | merge `understanding` into `goal_contract_decision`; write the record (with `flag_off`) also when the flag is OFF; best-effort try/except around the write | ~25 lines |
| goal_resolution.py | emit `candidate`/`canonical_substitution` into the existing diagnostics dict | ~10 lines |
| tests | new deterministic tests (§7); no production reads of the new fields | ~150 lines |

### 4.5 Behavioral-impact statement (audit boundary)

- The ONLY user-visible delta: when a heuristic-intent contract is
  composed, the failure-class disclosure (agent_loop.py:6330) can now use
  the REAL diagnostics instead of the generic face — an improvement in
  disclosure accuracy, never in decision outcome.
- Diagnostics are write-only observations: no business code reads
  `candidate`/`canonical_substitution`/`flag_enabled` for decisions.
- Write failures are swallowed; business results are byte-identical.
- No facet is force-mounted; contractless remains a legitimate outcome.

## 5. Runtime Verification Gate — design

Extension of the EXISTING `qa/preflight_runtime.py` (verdicts FRESH /
STALE / UNVERIFIED already implemented), NOT a new tool.

### 5.1 Four dimensions

1. **Source consistency** (exists): listener PID/cwd/command, process
   start vs newest watched-source mtime, restart/hot reload models,
   tree identity (HEAD + dirty-tree hash), change-specific probe URL.
   Assessed-sufficient for mtime (documented residual: mtime proves
   recency, not content; the change-specific behavioral probe carries
   content proof — already the script's stated model). A source
   fingerprint (per-file hash manifest under watch paths, dirty-tree
   inclusive) is an OPTIONAL future hardening; not required now.
2. **Effective configuration** (NEW `--expect-flag NAME=VALUE…`):
   verifies the effective flag via the **process-equivalence chain**
   established in P0 — (a) listener cwd resolved (env_file is
   cwd-relative), (b) `ps eww` of the ACTUAL listener shows no
   overriding env var for the flag, (c) a Settings load in the same cwd
   (same env_file mechanism) yields the expected value. The chain proves
   config provenance end-to-end without exposing secrets; the residual —
   the listener's in-memory Settings object — stays NOT VERIFIED until
   an opt-in diagnostic (e.g. boolean flag names on
   `/runtime/identity`) is approved as a separate change.
3. **Actual running process** (exists, plus): PID/cwd/exe/port identity,
   old-instance detection (port ownership change), and the behavioral
   probe as proof that THIS process served the request. Explicitly NOT
   sufficient: HTTP 200 or a listening port.
4. **Target feature path** (IMPLEMENTED as the two-stage contract —
   stage A `--require-feature-evidence INVESTIGATION_ID` records the
   requirement (kind: goal_contract_decision, KIND taxonomy deferred)
   bound to the record's generated_at; stage B
   `--verify-feature-evidence <gate record>` demands persisted evidence
   that the path executed — a `goal_contract_decision` with a valid
   non-flag_off outcome, on a task of the DECLARED investigation, whose
   business creation time (tasks_v2.started_at) is STRICTLY AFTER the
   declaration. The gate codifies the four states and refuses to
   conflate them: **config enabled ≠ path executable ≠ path executed ≠
   E2E accepted.** The declaration scope is the INVESTIGATION because a
   future task id is unknowable pre-run; time + investigation binding
   make old-task evidence unsatisfiable by construction. A
   time-unverifiable bound candidate BLOCKs (exit 2); it is never
   downgraded. The earlier `--check-feature-evidence TASK_ID` sketch was
   REMOVED before release — it could not bind evidence to a declaration
   and allowed arbitrary task ids.)

### 5.2 Verdict protocol (admission rules)

| Verdict | Admission | Consequence for the acceptance run |
|---|---|---|
| PASS | all requested dimensions verified, probes green | run may start |
| FAIL | a checked condition is deterministically wrong | run BLOCKED until fixed |
| BLOCKED | a required dimension cannot be verified (no mechanism / evidence absent) | run BLOCKED — NOT downgraded by later green tests |
| NOT VERIFIED | partial verification (e.g. in-process memory) — recorded explicitly, may coexist with PASS only when the residual is declared non-blocking for the feature under test | documented residual |
| NOT TESTED | the journey never ran | never reported as anything else |

Blocking rules: source STALE → BLOCKED; expected flag missing or
unverifiable → BLOCKED; listener identity mismatch → BLOCKED; target
path evidence unavailable → the acceptance may still run ONLY if its
declared scope does not claim that path, otherwise BLOCKED. No skip
flags, no verbal overrides, no "unknown = default".

### 5.3 Scope of applicability

Doc-only / static-analysis tasks: gate NOT required. Any acceptance that
asserts runtime behavior of a feature: the gate MUST run with that
feature declared as the target and its flag asserted. The gate is
invoked with the feature list, so unrelated checks are not forced.

## 6. Deterministic test matrix (design; all pytest, no real LLM/browser)

| # | Scenario | Layer | Input | Expected | Evidence |
|---|---|---|---|---|---|
| 1 | candidate fired → contract accepted | unit (existing gsp2/gsp7 shapes) | metric-vocabulary request, flag ON | decision=accepted; understanding.candidate.fired=true; facets_mounted non-empty | persisted state doc |
| 2 | candidate fired → contractless final | unit | request where the reviewed set drops the candidate aspect | candidate.fired=true AND contract decision ≠ accepted; override recorded | state doc |
| 3 | candidate overridden by review | unit — ONLY if constructible via the existing scripted-LLM seam (goal_llm scripts); else NOT VERIFIED and dropped from the matrix | candidate-matching request + scripted LLM output without the tag | understanding can distinguish fired→overridden | state doc |
| 4 | invalid proposal / rejection | unit (cg3 BAD_PROPOSAL shape) | proposal with non-vocabulary evidence | decision starts with `invalid_proposal:`; record_complete=true | state doc |
| 5 | flag OFF | unit | flag monkeypatched False | outcome=`flag_off` recorded (not None); candidate.stage_ran=false | state doc |
| 6 | process/config mismatch | gate-level (preflight unit test with a stubbed listener) | expect-flag contradicts env | verdict BLOCKED/FAIL, exit non-zero | preflight record |
| 7 | diagnostics partial/failed | unit | injected write failure | business decision byte-identical; record_complete=false + reason | state doc diff |
| 8 | write-failure isolation | unit | exception raised inside the diagnostics merge | no propagation; loop outcome unchanged | test assertion |
| 9 | regression compatibility | existing suites: gsp2/gsp7/cg3/u00010_file_stream/language-consistency | unchanged inputs | byte-identical decisions/facets/rendering except §4.5 disclosure | suite green |

## 7. Options comparison

| Option | Complexity | Observability | Persistence cost | Privacy risk | Intrusiveness | Verdict |
|---|---|---|---|---|---|---|
| A: docs only | none | none — G1-G5 remain; incidents stay unexplainable | none | none | none | insufficient |
| **B1 + minimal gate (recommended)** | low (~40 LOC prod + gate flags) | distinguishes all seven states §4.3; config-provenance verified per run | one JSON sub-object per turn | minimal (states + codes + ids only) | write-only, failure-isolated | **recommended** |
| B2: durable telemetry/event store | medium-high (sink, retention, schema evolution) | richest, queryable | real storage + retention policy | higher (event volume) | new infrastructure | future, not now |

Boundaries: the startup-mechanism improvement (durable required-env
manifest + post-start self-check) is a SEPARATE follow-up engineering
item (P0 finding), not part of B1. B2 remains future.

## 8. DoD (for the future implementation phase)

1. All nine matrix rows green at their declared layers (row 3 only if
   constructible; otherwise documented NOT VERIFIED and excluded).
2. A real API turn (existing P0.1 procedure) shows the new record with
   `flag_enabled=true` and a non-null decision — P0.1 rerun as the live
   proof.
3. Byte-identical business results on all existing contract/facet/
   renderer suites (row 9).
4. Preflight with `--expect-flag` blocks a deliberately mis-configured
   listener (row 6).
5. Docs: this document updated from DESIGN → IMPLEMENTED with the
   verified anchors and evidence paths.

## 9. Risks & open questions

- R1: `outcome` gains a new enum value (`flag_off`) — any consumer that
  treats the field as a closed set today must be enumerated (known
  readers: activity mapping tests, composer disclosure). 
- R2: dual write sites (run_turn summary vs loop-internal full
  provenance) must not overwrite each other; the design assigns the
  loop the full dict, run_turn the summary — merge order documented at
  implementation time.
- Q1 (user decision): approve adding `flag_off` to the outcome
  vocabulary, or keep flag-OFF as an absent decision (weaker)?
- Q2 (user decision): approve the `--expect-flag` gate flags for
  `qa/preflight_runtime.py` (script extension, ~40 lines)?
- Q3: should the refinement-path resolver call (investigation_service.py:1935)
  receive diagnostics too (recommended: yes, same one-line pattern)?

## 10. Implementation plan (upon approval — NOT started)

Order: (1) goal_resolution diagnostics emission + unit tests; 
(2) run_turn/plumb + agent_loop merge + flag_off record + tests;
(3) gate flags in qa/preflight_runtime.py + row-6 test; (4) regression
sweep (row 9); (5) P0.1-style live rerun + doc status flip.
Estimated verification cost: full deterministic suite < 2 min; live
rerun one API turn.

---

# Part II — Runtime Verification Gate Enforcement Review (Addendum, 2026-10-24)

Status: **ACCEPTED / PASS for the implemented scope (expect-flag,
two-stage feature-evidence contract, G1–G7 self-tests, E3 harness
bootstrap, CLAUDE.md rule — final delivery audit 2026-10-24, §23).
P1-2 IMPLEMENTED (2026-10-24): THREE API E2E harnesses enforce the gate
mechanically — `qa/journey_bchain_u00005_coverage_api.py` (step0 →
stage-A declaration → real goal run → stage-B verification; live
happy-path + live missing-evidence negative),
`qa/journey_p0b_contract_api_e2e_u00047.py` (same contract via the
`_run_goal` chokepoint: live 2026-10-24T12:25Z run — step0 FRESH, 5/5
scenarios gated PASS; records `gate-20261024T122516Z-p0b-e3-*.json`), and
`qa/journey_u00033_fixes_api.py` (live 2026-10-24T13:05Z run — step0
FRESH, 3/3 scenarios stage-A/B exit 0 gated PASS with qualifying tasks
TASK-a421c9e78404 / TASK-89c5ac27e518 / TASK-3cfb56543b65, all 27
business assertions True, final exit 0 under the fixed semantics —
records `gate-20261024T130525Z-u00033fx-e3-*.json`, results
`/tmp/u00033_fixes_api.json`). Deterministic control-flow + structural
wiring tests in `qa/tests/test_e3_api_gate_flow_v1.py`. Fail-closed:
gate BLOCKED(2)/evidence FAIL(3) exit verbatim; later scenarios never
run; business-assertion failure exits 1 in the u00033fx and bchain
harnesses (p0b retains its pre-existing implicit-0 convention —
recorded limitation). Other API E2E harnesses remain rule-bound only
(CLAUDE.md) — unchanged residual.** This
addendum answers: what makes the gate BINDING, who must call it, how
bypasses are prevented, and what evidence standard the gate itself must
meet. It adds no second state system; the preflight record JSON (already
emitted by `qa/preflight_runtime.py`) remains the single gate artifact.

## 11. Acceptance entry-point inventory (ACTUAL repo, no assumed runner)

There is NO unified test runner and NO CI in this repository (verified:
no `.github/`, no CI config). Real entry points, by class:

| Class | Actual form | Runtime-dependent? |
|---|---|---|
| Unit / component / integration | ad-hoc `cd backend && python3 -m pytest tests/…` | No (monkeypatched flags, scripted LLM, temp stores) |
| API E2E | standalone `qa/journey_*_api.py`, `qa/*_e2e.py` (~20 scripts) AND ad-hoc `httpx`/`curl` commands (e.g. the P0.1 procedure) | **Yes** |
| Browser E2E | standalone `qa/journey_*_browser.py`, `qa/u00010_phase2_browser.py`, … (~40 scripts), each with its own `main()` | **Yes** |
| Gate itself | `qa/preflight_runtime.py` (`--port/--service/--watch/--reload-model/--probe-url/--probe-contains/--record/--self-check`) | observes |
| Ad-hoc forensic probes | inline scripts (P0/P0.1 style) | case-by-case |

Consequence: enforcement CANNOT rely on intercepting one runner — it
must combine (a) a behavioral rule for agent-driven flows and (b)
deterministic gate invocation inside reused harnesses.

## 12. CLAUDE.md mandatory rule — PROPOSED TEXT (not applied)

> **[APPLIED — 2026-10-24]** The rule below was subsequently applied and
> now lives in `CLAUDE.md` as the "Runtime Verification Gate (mandatory
> for acceptance runs)" subsection (see §21 item (f)). The heading above
> and the wording note below are preserved as the historical
> proposal-time record.

To be added as a new subsection under the existing "Runtime/code
consistency preflight" section (wording proposed; application requires
user approval):

> **Runtime Verification Gate (mandatory for acceptance runs).**
> Before ANY acceptance whose verdict depends on the running process —
> real API E2E, browser E2E, or an ad-hoc runtime probe — run
> `qa/preflight_runtime.py` with the acceptance's TARGET FEATURE
> declared (`--expect-flag NAME=VALUE`, and — when the run claims the
> feature path executed — `--require-feature-evidence INVESTIGATION_ID`
> before the run, `--verify-feature-evidence <gate record>` at report
> time). The gate verdict and record path are part of the acceptance
> evidence.
> - PASS → the run may start.
> - FAIL or BLOCKED → the acceptance is BLOCKED: report the verdict and
>   record path, do not run the journey, and never report its results
>   as valid acceptance.
> - Pure unit/component/integration tests that do not depend on the
>   running process do NOT require the gate.
> - Every acceptance report MUST cite the gate record (path + verdict +
>   timestamp at/after the run's start). A report without a valid gate
>   citation is not a valid acceptance report.
> - States the gate cannot directly observe (e.g. the listener's
>   in-memory Settings) stay NOT VERIFIED and must be labeled as such;
>   they are never reported as verified.
> - Declaring a target feature is the caller's honest obligation: the
>   gate verifies what is declared and cannot infer intent.

## 13. Gate tool responsibility & status protocol

`qa/preflight_runtime.py` remains the single gate tool. Extension
(design):

| Addition | Semantics |
|---|---|
| `--expect-flag NAME=VALUE` (repeatable) | effective-config verification via the process-equivalence chain: (a) listener cwd resolved, (b) no overriding env var for NAME in the listener's `ps eww`, (c) Settings load in the same cwd (same env_file mechanism) yields VALUE. Provenance chain, not in-process memory. |
| `--require-feature-evidence INVESTIGATION_ID` (stage A) + `--verify-feature-evidence GATE_RECORD` (stage B, report time) | stage A records the evidence requirement (kind: goal_contract_decision) bound to the record's generated_at; stage B verifies, against the DECLARED record only: a qualifying task in the declared investigation, business creation time strictly after declared_at, outcome present and ≠ flag_off. Old evidence, other investigations, equal timestamps, missing outcomes and non-declaration records FAIL; store/time unverifiable BLOCKs. Arbitrary task ids cannot be passed via CLI. |
| `--target-feature NAME` (repeatable) | declares what this acceptance tests; scopes which checks are blocking |

Exit codes (backward-compatible: existing 0/2 keep their meaning):

| Exit | Record verdict | Meaning |
|---|---|---|
| 0 | `PASS` (per-dimension details may include `NOT VERIFIED` residuals that are declared non-blocking) | run may start |
| 2 | `BLOCKED` | a required dimension is unverifiable or the run must stop (superset of today's STALE/UNVERIFIED mapping) |
| 3 | `FAIL` | a required dimension is deterministically WRONG (e.g. expected flag true, effective false) |

The record JSON stays the single artifact: tree identity, listener
identity, probe result, per-flag provenance, per-dimension verdicts,
timestamp. No new state store.

## 14. Mandatory-call binding (who invokes the gate)

| Entry point | Binding mechanism |
|---|---|
| Ad-hoc runtime probes / API E2E one-offs | CLAUDE.md rule (§12) + evidence coupling (§15-E4): the report cites the gate record |
| Browser / API harness scripts that are REUSED (e.g. `qa/u00010_phase2_browser.py`) | harness-embedded gate: `main()` step 0 invokes `preflight_runtime` with the declared target flags and aborts on exit ≠ 0 (design ~10 lines per harness; applied only to harnesses actually reused — no big-bang conversion of ~60 scripts) |
| Pure pytest runs | NOT bound (no gate requirement) |
| Future CI | out of scope (none exists) |

## 15. Bypass-prevention comparison

| Option | Mechanism | Bypass surface | Fit |
|---|---|---|---|
| E1 unified wrapper runner | one `qa/run_acceptance.py` wraps every entry | agent still runs raw commands; ~60 scripts to convert | heavy, low marginal value — rejected |
| E2 CI enforcement | pipeline refuses reports without gate records | strong but N/A — no CI exists | future if CI appears |
| E3 harness-embedded gate | reused harnesses self-check at startup | editing the harness skips it — visible in diff | deterministic second layer for reused harnesses |
| **E4 rule + evidence coupling (recommended core)** | CLAUDE.md rule: acceptance report MUST cite a fresh gate record bound to the tree hash + PID + declared feature; reports without it are invalid | a skipped gate produces a report with NO valid citation — the bypass is itself visible | minimal, works today, matches existing evidence-path discipline |

**Recommendation: E4 now (CLAUDE.md proposal §12) + E3 for the small set
of reused harnesses.** Residual risk: an agent that both skips the gate
AND fabricates a citation — mitigated only by review; the record's tree
hash + PID + timestamp make fabrication non-trivial (must match a real
listener state). Ad-hoc probes are covered because P0.1-style evidence
(already the norm) must cite the gate record.

## 16. Gate self-test matrix (deterministic, pytest, stubbed listener/process tables)

| # | Scenario | Expected |
|---|---|---|
| G1 | expected flag satisfied (env_file + no override) | exit 0, verdict PASS, flag provenance recorded |
| G2 | expected flag contradicted (overriding env) | exit 3, verdict FAIL, per-flag reason |
| G3 | expected flag set but mechanism unverifiable | exit 2, verdict BLOCKED, dimension `NOT VERIFIED` |
| G4 | listener PID/cwd mismatch vs declared | exit 2, BLOCKED (identity dimension) |
| G5 | source newer than restart-model process start | verdict STALE → exit 2, BLOCKED |
| G6 | preflight itself raises | non-zero exit (never 0), BLOCKED, error in record |
| G7 | pure unit-test run | no gate invocation anywhere (asserted by absence — no coupling added to pytest paths) |

## 17. Source-consistency evidence standard (what "ready" requires)

ALL of the following, recorded in one gate record:
(a) listener cwd == expected backend directory; (b) command/interpreter
match the documented startup; (c) process start ≥ newest watched-source
mtime (restart model) or hot model with (d); (d) a behavioral probe
whose response CONTAINS a marker only the current source produces;
(e) tree identity (HEAD + dirty-tree hash) recorded; (f) module-origin
evidence (startup log import line or import-path check).
Explicitly INSUFFICIENT alone: git HEAD, source mtime, HTTP 200, a
listening port, `runtime/identity` fingerprints (disk-computed).

## 18. Effective-config vs process-identity boundary

- **Process identity**: directly observable (`lsof`/`ps`) — cwd, exe,
  command, port, start time, env-var NAMES (never values). Verifiable.
- **Effective configuration**: verified as a PROVENANCE CHAIN
  (cwd-relative env_file → no overriding process env → same-mechanism
  Settings load yields the expected value). This is an equivalence
  argument, NOT direct introspection of the listener's in-memory
  Settings — that residual stays **NOT VERIFIED** and must be labeled.
- Minimal supplement (separate approval, not in this design's scope):
  expose boolean feature-flag NAMES (no values beyond booleans, no
  secrets) on the existing `/runtime/identity` endpoint, making the
  in-process value directly assertable.

## 19. Responsibility matrix

| Artifact | Responsibility |
|---|---|
| CLAUDE.md (proposed §12) | WHEN the gate is mandatory + reporting obligations (rule, not mechanism) |
| `qa/preflight_runtime.py` | mechanical checks, exit codes, single record artifact |
| Reused acceptance harnesses | invoke the gate as step 0; embed record path + verdict in their output |
| This design document | specification; status flips DESIGN → IMPLEMENTED only with evidence |
| Acceptance test reports | cite gate record (path + verdict + timestamp + declared feature); label NOT VERIFIED residuals |

## 20. Implementation scope (upon approval), DoD, bypass risks, evidence

Scope & files: `qa/preflight_runtime.py` (+~80 lines: three new flags,
exit-code 3, per-flag provenance, self-test hooks); CLAUDE.md one
subsection (§12 text, applied only after approval); gate self-tests
`backend/tests/…/test_preflight_gate_v1.py` or `qa/tests/…` (new, ~150
lines); optionally the gate bootstrap in 1–2 reused harnesses (~10 lines
each). No production backend code in this part.

DoD: G1–G7 green; a deliberately mis-configured listener is BLOCKED;
a doc-only task runs with no backend and no gate; a sample acceptance
report demonstrates the required citation; existing preflight records
remain backward-compatible.

Bypass risks & mitigations: harness edit to skip gate → visible in
diff + E4 citation requirement; ad-hoc command without gate → report
invalid by rule; stale record reuse → record binds tree hash + PID +
timestamp ≥ run start; fabricated citation → must match a real listener
state (non-trivial to fake, detectable by review).

Acceptance evidence for the gate itself: the self-test matrix (§16) is
the gate's own acceptance surface; live validation = one deliberately
broken-flag listener run demonstrating BLOCKED (P0-style procedure).

## 21. Implementation log (2026-10-24, authorized implementation)

Plan: (a) goal_resolution — candidate-kind annotation
(`deterministic_contract_proposal(fired_aspects=…)` out-param),
canonical-substitution provenance (`_canonicalize_llm_outputs`
diagnostics out-param), diagnostics emission in `understand_goal`;
(b) investigation_service — diagnostics dict passed at BOTH resolver
sites via the signature-aware `_call_goal_resolver`; (c) agent_loop —
handoff param → state, decision merge (`understanding` sub-object),
flag-off record, write-failure isolation; (d) consumers — exact-dict
test assertions updated to outcome+schema checks; (e) gate — preflight
`--expect-flag`/`--require-feature-evidence`/`--verify-feature-evidence`/`--target-feature`,
exit 3, provenance chain; (f) CLAUDE.md gate rule; (g) G1–G7 self-tests;
(h) E3 bootstrap in `qa/u00010_phase2_browser.py`; (i) live rerun.
Rollback boundary: every change is additive and reversible per-file
(`git diff` review); no destructive git ops; `backend/.env` and user
dirty-tree assets untouched. Merge-order ownership: run_turn handoff
seeds `state.goal_understanding_diagnostics`; the loop-internal resolver
may REPLACE it with its own (loop owns the field after its call);
decision merge is read-only over both. Verification: targeted suites
(gsp2/gsp7/cg3/cg1/cg2/u00010_file_stream/response_language/
three_constraints), gate self-tests G1–G7, deliberate-misconfig live
BLOCKED proof, P0.1-style live API turn with `--expect-flag` gate record.

## 22. Verification evidence (implementation complete)

- Deterministic sweep (9 contract/facet/renderer suites): **203 passed**
  (cg1/cg2/cg3/gsp2/gsp7-preservation/u00010-file-stream/
  response-language/r2a/replan-contractless — post-B1, cwd backend/).
- Gate self-tests G1–G7 (+feature-evidence modes): **9 passed**
  (`qa/tests/test_preflight_gate_v1.py`, stubbed process tables).
- Known pre-existing failures (attribution-proven, NOT B1 regressions):
  `test_gsp7_replan_api_e2e_v1.py` 3 (stale `failed`/`goal_satisfied`
  expectations vs the U00047 no-progress guard — a reversal-copy run
  reproduces them identically); `test_three_constraints_closure_v1.py` 1
  (attribution-proven earlier via a pre-fix copy).
- E3 live blocking proof: harness `qa/u00010_phase2_browser.py` against
  a STALE listener → `GATE BLOCKED (backend, exit 2)`, harness exit 2,
  browser never started (record
  `docs/evaluations/gate-20261024T102355Z-backend.json`).
- Gate PASS proof on the recovered listener:
  `docs/evaluations/gate-20261024T102519Z-{backend,frontend}.json`.
- Real browser journey (B1 code, gate PASS): lang_switch run
  `docs/evaluations/u00010-phase2-browser-20261024T103251Z.json` —
  language behavior intact (turn1 en / turn2 zh measured correctly);
  the 3 FAIL checks are the DOCUMENTED harness defects (turn3 prior-
  list never appended; composer-marker variance), not B1 regressions.
- Real API turn on the B1 backend: `TASK-5e7c56585e74`
  (INV-88898be550b9) → decision record with `gu-decision/1`:
  outcome=contract_accepted, path=run_turn, candidate fired
  kinds=['reverse_trade_metric_semantics'],
  facets_mounted=['reverse_trade_metric_semantics'],
  record_complete=true. Feature-evidence gate PASS:
  `docs/evaluations/feature-evidence-TASK-5e7c56585e74.json`.
- Known nuance: `flag_source` may read "env" on the live server because
  `llm_provider.load_dotenv()` copies `.env` into os.environ at import —
  the provenance is accurate post-load; the file origin is
  "env_file:.env" in processes that have not imported llm_provider.
- Residuals (documented, by design): in-process Settings memory not
  directly introspected (no diagnostic endpoint); the legacy turn route
  and the refinement path plumb diagnostics without a decision sink;
  non-E3 harness scripts remain bypass-capable (mitigated by the
  CLAUDE.md evidence-citation rule).

## 23. Acceptance closure (B-lite two-stage feature-evidence contract)

**ACCEPTED / PASS — 2026-10-24, on the final read-only delivery audit.**
P1-1 (parameter naming/design-contract divergence) is **CLOSED**: the
implemented two-stage contract supersedes the `--require-feature-evidence
KIND` sketch; §4.4/§12/§13 describe the shipped semantics.

Direct evidence chain (all read-verified in the final audit):
declaration `docs/evaluations/gate-declare-110631Z.json` (declared_at
2026-10-24T11:06:31.211094Z, scope INV-88898be550b9, PID 8173) → pre-turn
verification `verify-before-turn.json` (FAIL/exit 3 — six bound
candidates all pre-declaration, none qualifying) → real turn
`TASK-6e4fd8c3bf84` (started_at 2026-10-24T11:08:40.091832Z,
contract_accepted, gu-decision/1) → post-turn verification
`verify-post-turn.json` (PASS/exit 0, qualifying task matches exactly).

Accepted limitations (preserved from the audit; none blocks this
acceptance):
1. Investigation-level binding — any qualifying turn inside the declared
   investigation after the declaration satisfies the evidence check
   (task-level scope is unimplementable pre-run and out of approved
   scope).
2. Declaration records are plain JSON without tamper protection —
   validated structurally, not cryptographically (outside the current
   threat model; a signing mechanism would be a separate approval).
3. Evidence kind limited to `goal_contract_decision` (KIND taxonomy
   deferred by approved scope).
4. Listener in-memory Settings remain NOT VERIFIED (process-equivalence
   chain only).

Evidence dating: the 203/197-item deterministic sweeps were executed in
the implementation batch (2026-10-24); the final audit re-executed only
the 19 gate self-tests and the record reads above — no regression sweep
was re-run in the closure round. Remaining open work AT CLOSURE TIME:
P1-2 (API E2E harness E3 gating); P2 candidates (record signing; runtime
flag introspection endpoint). [SUPERSEDED 2026-10-24, later the same day:
P1-2 was implemented, live-verified and ACCEPTED — see §24; this line is
preserved as a historical closure-time record.]

## 24. Overall status & remaining work (2026-10-24, PMO calibration)

**Overall goal: IN PROGRESS.** [STATUS AT CALIBRATION TIME — superseded
2026-10-24 by SCOPE COMPLETE (residuals accepted), see §25.] B1, P1-1
and P1-2 are accepted; the
program is not complete — the gaps in the table below remain open.

| Work item | Status | Evidence | Known limitation | Next action |
|---|---|---|---|---|
| B1 minimal decision observability | ACCEPTED / PASS | §22; sweep 203; live turn TASK-5e7c56585e74 carries gu-decision/1 | `flag_source` time-frame nuance (load_dotenv); diagnostics plumbed but unsunk on the legacy turn route and refinement path | — |
| P1-1 parameter-contract divergence | CLOSED | §23; the two-stage contract supersedes the `KIND` sketch | — | — |
| Runtime Verification Gate (expect-flag provenance chain, G1–G7, exit protocol 0/2/3) | ACCEPTED / PASS | `qa/tests/test_preflight_gate_v1.py` (19); live STALE block `docs/evaluations/gate-20261024T102355Z-backend.json`; live PASS `gate-103443Z-api-e2e.json` | in-process Settings NOT VERIFIED (process-equivalence chain only); `ps -E` exposes exec-time env only | P2 candidate: boolean flag names on /runtime/identity |
| P1-2 E3 integration into designated API E2E harnesses | ACCEPTED / PASS (approved scope; final batch closed) | journey_bchain: live exit 0 + PASS chain + live negative FAIL/3 (`gate-20261024T114434Z-e3-*.json`); journey_p0b_contract: live 5/5 scenarios gated PASS (`gate-20261024T122516Z-p0b-e3-*.json`); journey_u00033_fixes: live 3/3 scenarios gated PASS + 27/27 business assertions + exit 0 (`gate-20261024T130525Z-u00033fx-e3-*.json`, `/tmp/u00033_fixes_api.json`); tests `qa/tests/test_e3_api_gate_flow_v1.py` (27 passed incl. 6 new) | mechanically gated harnesses = 3; live stage-B BLOCKED NOT VERIFIED (unit-covered); p0b retains implicit-0 business-exit convention; p0b scenario-5 has 1 failed U00005 business check (independent dimension) | harness expansion FROZEN per stop rule — new batches only with A/B-level evidence |
| U00005 business assertions (5 of 16 failed) | FAIL — independent dimension from the Runtime Gate result | `/tmp/bchain_u00005_api_e2e.json` (assertions block) | attribution vs the accepted checkpoint NOT VERIFIED; a comparative baseline-copy rerun costs one real-LLM journey (~5–8 min) | separate decision item — do not fold into gate acceptance |
| Exit-code semantics gap (business failure exited 0) | RESOLVED for journey_bchain and journey_u00033_fixes (mapped to non-zero behind deterministic tests); p0b implicit-0 and non-gated harness conventions = USER-ACCEPTED residual (2026-10-24, §25) | deterministic matrix + real-saved-run mapping tests (`qa/tests/test_e3_api_gate_flow_v1.py`); pre-existing harness convention, NOT a P1-2 regression | exception path and business failure share exit 1 by design | closed for the accepted scope |

Distinct failure dimensions (do not conflate): the Runtime Gate verdict
(PASS this run), the U00005 business assertions (5 failed this run), and
the harness process exit code (0 — pre-existing convention: business
failures are recorded in the result JSON but did not affect the exit
code; being fixed this round). Other API E2E / browser harnesses are NOT
mechanically gated — they rely on the CLAUDE.md rule and evidence
citation; live stage-B BLOCKED has control-flow and gate-level coverage
but NO live verification.

## 25. Final acceptance & residual acceptance record (2026-10-24)

Accepted by explicit user decision on 2026-10-24, after the V1 Acceptance
Readiness Audit confirmed all mandatory DoD items VERIFIED with
BLOCKERS FOUND: 0. The following residuals are formally ACCEPTED — they
remain documented facts and are NOT eliminated:

1. U00005 baseline attribution: the five failed business assertions are
   NOT attributed to a checkpoint baseline (no comparative real-LLM
   rerun performed — cost/authorization declined). The historical
   variance must not be described as fully root-caused.
2. Live Stage B BLOCKED remains NOT VERIFIED (live): no destructive
   canonical-store scenario will be constructed. Coverage: gate-level
   BLOCKED test + e3_gate propagation tests (distinct from live).
3. `journey_p0b_contract_api_e2e_u00047.py` retains its implicit-0
   business-exit convention — disclosed, not fixed in V1.
4. In-process Settings NOT VERIFIED (no introspection endpoint); KIND
   taxonomy deferred; legacy turn route and refinement path diagnostics
   plumbed without a sink; declaration records are unsigned JSON.
5. The five /submit-driven API E2E harnesses and the ~85 non-enumerated
   scripts remain OUTSIDE mechanical gating — rule-bound via CLAUDE.md
   evidence citation only. Harness expansion stays FROZEN per the stop
   rule; new batches require A/B-level evidence and separate
   authorization.

Closing condition met per §I of the V1 Acceptance Readiness Report:
status updated from IN PROGRESS to **SCOPE COMPLETE (residuals
accepted)**. Any future work on the residuals is a separate,
individually authorized program.
