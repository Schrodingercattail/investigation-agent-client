# WP-M2-1 Implementation Plan — Candidate Semantic Metadata (rev. 3, PMO-directed — Implementation Baseline)

**Status**: Implementation Plan v1 rev. 3 — **final PMO-directed revision; this document is the Implementation Baseline for execution.** Awaiting PMO approval to start implementation (this stage's DoD = approved plan, not implementation).
**Date**: 2026-09-24 (rev. 3 same day)
**Supersedes**: rev. 1 / rev. 2 of this file (same path; revision, not a new plan).
**Contracts (unchanged baselines)**: `docs/baselines/2026-09-24-candidate-semantic-metadata-contract-v1.md` ("Metadata Contract") · `docs/baselines/2026-09-24-wp-m2-1-safe-target-refinement-contract-v1.md` ("WP-M2-1 Contract").
**Frozen constraints (reaffirmed)**: frozen refinement untouched (`target_refinement.py`, fp `9842ec834bdb`); `ENABLE_TARGET_GOAL_REFINEMENT` stays default OFF; no MCP; no second database; no production-behavior change until flags are explicitly enabled.

---

## 0. PMO Decision Enacted by This Revision (normative for the plan)

The following lifecycle decision is **adopted and no longer an open question**:

1. A newly discovered candidate MUST be automatically registered by the runtime.
2. Automatic registration MUST NOT imply Safe-Select eligibility.
3. Newly registered candidates start as INCOMPLETE unless already covered by an existing verified metadata record.
4. INCOMPLETE / UNVERIFIED candidates MUST resolve to UNKNOWN for autonomous Safe-Select and MUST NOT be selected by the autonomous admissibility gate.
5. **User Explicit Selection is a separate path from Safe-Select.**
6. A user may explicitly designate an ineligible candidate; the system may then set it as the investigation focus and continue investigation.
7. User designation MUST NOT change candidate metadata status and MUST NOT grant Safe-Select eligibility.
8. User clarification/designation is evidence of user intent, **not metadata verification**.
9. Metadata verification remains a governance concern; the runtime never performs it implicitly.
10. The system MUST NOT enter an infinite UNKNOWN → CLARIFY loop.
11. Clarification terminates in exactly one of three ways: (a) user explicitly designates a candidate → explicit focus; (b) metadata/governance state changes externally, making re-evaluation possible; (c) the same unresolved condition repeats without state change → bounded terminal non-selectable outcome.

**Product boundary (core safety boundary of the Agent):**

- **Safe-Select** = system-authorized *autonomous* candidate selection (gated by metadata eligibility + admissibility).
- **Explicit Selection** = *user-authorized* candidate focus (pre-existing explicit-selection path; never gated by metadata eligibility).

These two paths are intentionally separate and MUST NOT be conflated.

**PMO Decision — Existing Candidate Universe Coverage (normative for V1, enacted 2026-09-24):**

> At implementation-baseline time, all candidates currently exposed by the live RP candidate source must be inventoried and covered by the metadata lifecycle. Candidates with sufficient verified semantic evidence are seeded as COMPLETE; candidates without sufficient verified evidence are automatically registered as INCOMPLETE and remain ineligible for autonomous Safe-Select.

Consequences enacted throughout this plan:

1. F5/F6/F8 are examples of currently verified candidates / available seed evidence — **they are NOT the complete V1 candidate scope**.
2. The V1 implementation scope includes the **entire candidate universe currently exposed by the live RP candidate source** at implementation-baseline time.
3. Every currently exposed candidate MUST appear in the candidate metadata lifecycle after baseline reconciliation; no candidate is silently omitted.
4. A candidate may enter COMPLETE only when sufficient verified semantic evidence exists; candidates without it MUST still be automatically registered as INCOMPLETE.
5. INCOMPLETE / UNVERIFIED candidates remain ineligible for autonomous Safe-Select.
6. No candidate-specific code changes are allowed as part of onboarding — discovery and registration remain data-driven.
7. This is an inventory/lifecycle-coverage requirement, NOT a requirement to build a full metadata governance platform.

**Two scopes, never collapsed:**

- **A. Candidate lifecycle coverage**: all currently exposed RP candidates must be discovered, inventoried, and represented in `candidate_metadata`.
- **B. Safe-Select eligibility**: only candidates satisfying verified-metadata and admissibility requirements may participate in autonomous Safe-Select.

Coverage (A) is mandatory and universal; eligibility (B) is earned per candidate. A ⊄ B and B ⊆ A.

**Scope directive (job-search/portfolio optimization):** demonstrate the full agent chain — Context → Planning → Tool Calling → Bounded Loop → State → Deterministic Safety Boundary — at minimal reasonable complexity. No governance UI, no elaborate approval workflow, no general-purpose auto-approval, no second database, no MCP. V1 seeds/verifies the known candidates (F5/F6/F8) so the complete Safe-Select path is demonstrable; a future candidate (F12) demonstrates discovery → automatic registration → INCOMPLETE → UNKNOWN → bounded clarification → explicit designation OR terminal outcome. V1 does NOT need to turn F12 into COMPLETE automatically.

## 1. Executive Summary

Implement the Candidate Semantic Metadata layer as the deterministic safety boundary of the agent: a same-store `candidate_metadata` + `semantic_concepts` catalog, a Coverage Check that **inventories the entire live RP candidate universe** and auto-registers any newly discovered finding as INCOMPLETE, a Registry-constrained semantic normalization + support evaluator (LLM proposes, system disposes), a flag-gated admissibility gate that refuses autonomous SELECT on any INCOMPLETE/UNVERIFIED candidate (UNKNOWN propagates, never FALSE), and a clarification lifecycle that terminates — via explicit user designation (focus), external governance change, or a bounded terminal non-selectable outcome — instead of looping. Verified records are seeded for candidates with sufficient verified semantic evidence (F5/F6/F8 are the initial verified examples, **not the whole scope**); every other currently exposed candidate is inventoried as INCOMPLETE and stays ineligible for autonomous Safe-Select while remaining fully available to explicit user selection. A new F12-style candidate exercises the discovery/registration/clarification path with zero code changes. Two flags (default OFF) gate all new behavior; flags OFF is byte-for-byte today's behavior.

## 2. Current State / As-Is Architecture (verified code facts — unchanged from rev. 1)

| Fact | Location |
|---|---|
| Candidates (findings) fetched live per turn via `case_findings_provider` → `risk_case_fetch`; **no persistent candidate registry**; RP exposes no content-version field | `app/api/investigations.py` `_default_findings`; `app/domain_tools/risk_case_fetch.py`; Finding model fields: finding_id/title/description/type/severity/signal_refs/policy_refs/capabilities/ext |
| Persistence: TaskStoreV2 SQLite single store (`tasks_v2`/`investigations`/`investigation_sessions`/`agent_states`); documented "no second database" convention | `app/task_store_v2.py:56-106`; `app/api/investigations.py` header |
| Frozen-vocabulary LLM pattern already exists (LLM output validated against a fixed vocabulary; invented entries rejected) — direct precedent for Concept-Registry-constrained normalization | `app/goal_resolution.py:13-38,103-112,206` |
| Refinement trigger: `InvestigationService.run_turn` §1b (flag + resolver-presence gate); AMBIGUOUS branch produces per-turn clarification with **no per-request ledger** | `app/investigation_service.py`; `app/context_resolution.py` (existing focus is authoritative) |
| Flag convention: `Settings` singleton; `ENABLE_TARGET_GOAL_REFINEMENT=False` default | `app/config.py:50` |
| Telemetry: `telemetry.emit` + default Noop sink; `target_resolution` evidence exists (clarify path carries `candidates_considered`; select path does not — known asymmetry) | `app/telemetry.py`; `app/investigation_service.py`; `app/api/schemas.py` |
| Server-side registry precedent: `_FOLLOWUP_INDEX` (governed, client cannot inject) | `app/api/investigations.py:75-78` |
| Experiment assets (core tables, dual-criterion GT) live in `/tmp/spm21c3/` only — not production data | session records |

## 3. Contract → Runtime Mapping

| Contract concept | Runtime carrier | WI |
|---|---|---|
| Metadata Catalog (§15) | `candidate_metadata` table in the TaskStoreV2 store | WI-1 |
| Concept Registry (§7) | `semantic_concepts` table + startup conformance check (governed seed) | WI-1/WI-3 |
| Coverage Check (§21) | reconcile step on the findings-provider path (new-candidate / semantic-change / candidate-gone) | WI-2 |
| Onboarding states (§11/§16) | `candidate_metadata.status` + minimal transition utility (dev-facing) | WI-3 |
| UNKNOWN discipline (§13) | typed `SupportResult` + gate propagation | WI-4/WI-5 |
| Semantic normalization (§9) | Registry-constrained LLM proposal + deterministic validation | WI-4 |
| Admissibility (WP-M2-1 Contract §10) | gate in `InvestigationService` behind `ENABLE_SAFE_SELECT_ADMISSIBILITY` | WI-5 |
| Clarification termination (PMO decision 10/11) | clarify-ledger + user-designation events + terminal bounded outcome | WI-6 |
| Telemetry/observability | `candidate.metadata.*` / `support.evaluated` / `admissibility.decided` events + identity-endpoint coverage summary | WI-7 |
| **Explicit Selection path** | **pre-existing explicit-selection focus path — deliberately NOT part of the gate** | existing (no new code; designation event added by WI-6) |

## 4. Target Architecture / To-Be

```
RP (authoritative: id/title/description/type/signals — the WHOLE exposed candidate universe)
   ↓ per-turn fetch (unchanged)
Coverage Check (deterministic; WI-2)
   ├─ baseline reconciliation: EVERY currently exposed candidate inventoried (no silent omission)
   ├─ new candidate → auto-register INCOMPLETE draft (title/desc snapshot + content_hash)
   ├─ content_hash changed → INVALIDATE → UNVERIFIED (version++)
   └─ no longer returned → candidate_gone flag (telemetry; DEPRECATED only by governed action)
   ↓
Metadata Catalog (candidate_metadata)         Concept Registry (semantic_concepts)
   ↓                                              ↓
Frozen Refinement proposes target + confidence (UNCHANGED)
   ↓
SupportEvaluator (deterministic shell; WI-4):
   normalization (LLM proposal → Registry conformance) → per-candidate CORE mapping
   → set predicate (UNKNOWN first-class)
   ↓
Admissibility Gate (deterministic; WI-5; flag):
   STABLE ∧ SUFFICIENT ∧ UNIQUE(target==proposed) ∧ confidence≥threshold ∧ all candidates ELIGIBLE
   ├─ SELECT admitted → existing resolved path          ← Safe-Select path (scope B)
   └─ otherwise → CLARIFY / bounded terminal            ← Safe-Select refusal

── separate path (never gated by metadata) ──
User explicitly designates/selects a candidate (incl. INCOMPLETE ones)
   → existing explicit-selection focus path → investigation continues   ← Explicit Selection path
```

Ownership: the eligibility+admissibility gate inside `InvestigationService` is the final decision owner (system-only), reading the catalog fresh each turn via a `CatalogProvider` port. The frozen refinement is never modified.

## 5. Dynamic Candidate Onboarding Flow (PMO decision 1–4, 10–11 + universe coverage enacted)

- **Baseline reconciliation (implementation-baseline time)**: the first Coverage Check pass inventories the **complete current RP candidate universe** — every exposed finding gets a `candidate_metadata` row (COMPLETE where verified seed evidence exists, INCOMPLETE otherwise). No silent omission; no candidate-specific code.
- **Who/where/when**: the deterministic Coverage Check runs inside the findings-provider invocation of every turn/create; it reconciles RP findings against the catalog. Not LLM, not human.
- **Zero-code registration**: generic reconcile loop — RP finding id absent from catalog ⇒ INSERT INCOMPLETE (snapshot + content_hash, `provenance=auto_provisioned`). Any new RP finding (F12) self-registers.
- **Draft provisioning**: `candidate_id/type/title_snapshot/description_snapshot/content_hash/status=INCOMPLETE/metadata_version=0`; `core_concepts` empty (UNKNOWN semantics). LLM may write `suggested_core_concepts` (proposal-only, never judged).
- **V1 eligibility**: candidates with sufficient verified semantic evidence (F5/F6/F8 verified today; any others the inventory surfaces) are **seeded as METADATA_COMPLETE** via the governed seed (provenance `seed-2026-09-24-pmo`) so AC-1 can pass without an authoring workflow. All other currently exposed candidates are preserved as INCOMPLETE — never fabricated into COMPLETE. Authoring/verification of future candidates is a governance-side activity, out of V1 runtime scope (§12/§25).

## 6. Metadata Lifecycle / State Transition

```
(RP new candidate) ──Coverage Check──▶ INCOMPLETE ──governed authoring──▶ UNVERIFIED
     ▲                                    │                                  │ content change
     │                                    │  (user designation does NOT      ▼ move states here
     │                                    ▼   move states — ever)       METADATA_COMPLETE
(rp no longer returns + governed action) ────────────────────────▶ DEPRECATED
```

- The ONLY transition into COMPLETE is governed verification (post-V1 authoring workflow; V1 provides only the minimal transition utility + seed). **User designation and repeated user selection NEVER move status** (PMO decision 7/8/9).
- UNKNOWN rule: any non-COMPLETE candidate in the set ⇒ its Support=UNKNOWN ⇒ Unique Support=UNKNOWN ⇒ autonomous SELECT inadmissible ⇒ CLARIFY/terminal. UNKNOWN is never FALSE.

## 7. Semantic Resolution Architecture

`SupportEvaluator` (deterministic shell): inputs `request_text, candidates[], catalog_snapshot`; steps: normalization (LLM proposal → Concept-Registry conformance; failure ⇒ UNKNOWN) → per-candidate CORE mapping (signal ∈ candidate.core_concepts? competitor cores too?) → set predicate (exactly-one-TRUE + ≥1 FALSE; any UNKNOWN ⇒ UNKNOWN). Grounding check (fragment ∈ request) continues to validate proposals. Output: `SupportResult{per_candidate, unique, unique_target, reason_class}`.

## 8. LLM vs Deterministic System Responsibility

LLM proposes: normalization, draft suggestions, explanations. System decides: Registry conformance, grounding, CORE mapping, set predicate, eligibility, admissibility, UNKNOWN preservation, terminal outcomes. Any LLM failure ⇒ relevant support degrades to UNKNOWN ⇒ fail closed.

## 9. Registry / Data Model

```sql
semantic_concepts(concept_id PK, definition, status, version, created_at, created_by)
candidate_metadata(
  candidate_id PK, candidate_type, source_case,
  title_snapshot, description_snapshot, content_hash,
  core_concepts JSON, distinguishing_concepts JSON,
  potential_confusions JSON, mechanism_concepts JSON,
  suggested_core_concepts JSON,                     -- LLM proposal-only
  status TEXT CHECK(status IN ('INCOMPLETE','UNVERIFIED','COMPLETE','DEPRECATED')),
  metadata_version INTEGER, owner, authored_by, verified_by,
  last_verified_at, created_at, updated_at, provenance JSON)
clarify_ledger(
  id PK, investigation_id, request_fingerprint, candidate_set_hash,
  metadata_status_hash, outcome, created_at)
user_designations(
  id PK, investigation_id, candidate_id, turn_task_id,
  designation TEXT, provenance JSON, created_at)    -- intent evidence; never a status writer
```

## 10. API / Module / Integration Boundaries

- `app/candidate_metadata.py` (new): catalog/registry access (`CatalogProvider` port + TaskStoreV2 implementation), Coverage Check, state-transition utility (dev-facing; see WI-3).
- `app/semantic_resolution.py` (new): `SupportEvaluator`.
- `app/investigation_service.py`: metadata-gate step before refinement (flag), clarify-ledger/terminal logic on the AMBIGUOUS branch.
- `app/api/investigations.py`: identity-endpoint coverage summary.
- `app/config.py`: `ENABLE_SAFE_SELECT_ADMISSIBILITY=False`, `ENABLE_CANDIDATE_METADATA_COVERAGE=False`.
- `app/telemetry.py`: new event family fields.
- **Frontend: no required changes** (existing clarification rendering and explicit-selection controls carry the new flows; an optional "pending verification" hint is deferred).

## 11. Safe-Select Integration

Gate placement: after the frozen refinement proposal, before any resolution upgrade in §1b. Reads the catalog fresh each turn; enforces WP-M2-1 Contract §10 plus the eligibility precondition (all candidates COMPLETE; any non-COMPLETE ⇒ UNKNOWN ⇒ inadmissible). Reason classes: `metadata_incomplete` / `not_uniquely_supported` / `insufficient_evidence` / `unstable_candidates` / `low_confidence` / `invalid_target` / `fallback` / `non_selectable_terminal`. Flags OFF ⇒ gate and evaluator are fully bypassed (AC-10).

**Explicit Selection is not integrated into the gate by design**: it is the pre-existing explicit-selection focus path; the only new artifact is the `user_designations` event (WI-6) recorded as intent evidence.

## 12. UNKNOWN / CLARIFY / BLOCK Termination Semantics (PMO decision 10–11)

Concept separation: **A** = is user intent clear? **B** = is the candidate governed-verified? A never implies B.

- **(a) Designation terminates**: clarification → user explicitly designates an ineligible candidate → existing explicit-selection path sets focus → turn is served via focus; `user_designations` records intent evidence; metadata status unchanged; no Safe-Select evaluation re-entered while focus persists (existing-focus authority).
- **(b) Governance change re-enables**: any candidate-set change, content-hash invalidation, or status transition changes the ledger key (`request_fingerprint + candidate_set_hash + metadata_status_hash`) → fresh evaluation legitimate.
- **(c) Bounded terminal**: same ledger key repeats with no state change ⇒ bounded terminal non-selectable outcome — an honest bounded turn ("this target exists but is not verified for autonomous selection; you can select it yourself, or rephrase"), reason class `non_selectable_terminal`. Not a failed loop, not a pretended answer. **No infinite UNKNOWN → CLARIFY loop**: every branch terminates via (a), (b), or (c).

## 13. Versioning / Invalidation / Re-verification

`content_hash = SHA256(title + "\n" + description)` recomputed per turn by Coverage Check; mismatch ⇒ UNVERIFIED, `metadata_version+=1`, invalidation provenance recorded (RP has no native version — hash surrogate is a recorded implementation decision). Re-verification = governance-side action via the WI-3 utility (V1) / future authoring workflow.

## 14. Coverage / Drift Detection

Four coverage behaviors in one reconcile loop: **baseline inventory** (first pass at implementation-baseline time — every exposed candidate gets a row; scope A), new-candidate (auto-INCOMPLETE), semantic-change (invalidate), candidate-gone (flag + telemetry; DEPRECATED only by governed action). Exposed via `candidate.metadata.coverage` telemetry and the identity-endpoint `metadata_coverage` summary (counts by status + drift counters — scope A is continuously measurable, and scope B eligibility remains per-candidate).

## 15. Feature Flag / Rollout Strategy

| Flag | Default | Controls | Rollout |
|---|---|---|---|
| `ENABLE_CANDIDATE_METADATA_COVERAGE` | **OFF** | Coverage Check + baseline inventory + auto-registration + ledger records | Stage 1: validate baseline reconciliation (full universe) → then default ON (pure bookkeeping, no dialogue change) |
| `ENABLE_SAFE_SELECT_ADMISSIBILITY` | **OFF** | SupportEvaluator + gate + terminal outcome | Stage 2: after coverage validated AND F5/F6/F8 seeded verified → experimental → independent QA → default-value decision |

AC-10 = both OFF ⇒ behavior identical to the 2026-09-24 baseline, scenario-by-scenario. `ENABLE_TARGET_GOAL_REFINEMENT` untouched.

## 16. Observability / Telemetry

New events: `candidate.metadata.provisioned/invalidated/status_changed/coverage`, `support.evaluated` (per-candidate TRUE/FALSE/UNKNOWN counts + reason class; no CoT, no prompts), `admissibility.decided` (decision + reason class). Identity endpoint gains `metadata_coverage`. Evidence remains three-channel (API field + server log + telemetry) per SP-M2-1R.

## 17. Security / Safety Considerations

Metadata content is governed data but stays inside prompt-fencing discipline (request remains untrusted); no secrets exposed; LLM proposals never write status/concepts; all transitions carry actor + provenance; governance utility is dev-facing only in V1.

## 18. Backward Compatibility

Flags OFF ⇒ bypass, behavior identical to the 2026-09-24 baseline (AC-10). `target_resolution` additive-only; existing clarifications/selections/focus semantics unchanged; historical investigations are reconciled once on first flag-ON contact (bookkeeping).

## 19. Test Strategy

- **Unit**: set predicate (full UNKNOWN combinations), content-hash invalidation, legal status transitions, Registry conformance, ledger-key computation, flag bypass.
- **Module/Integration**: findings-provider reconcile (new/changed/gone), SupportEvaluator against labeled fixtures + real cases, gate decision table incl. AC-3/AC-9/AC-10, transition utility provenance enforcement.
- **API E2E**: real backend + full lifecycle (registration → clarify → designation → focus; seeded select path; terminal path).
- **Browser E2E** (CLAUDE.md quality gate): real UI + U00010 journeys for AC-1/2/4/6/7 with screenshots + runtime evidence.
- **Coverage tests**: baseline inventory completeness (every live RP candidate has a catalog row; COMPLETE only where verified evidence exists), and the F12 regression scenario (detect → register INCOMPLETE → no autonomous SELECT → designation available).
- **Regression**: existing suites + flag-OFF equivalence comparison.

## 20. E2E Validation Strategy

Every AC is asserted in three parts: user action → visible result → backend state change. API E2E on real HTTP with isolated stores; browser E2E via the existing CDP harness; DoD reports layers separately (unit / integration / API E2E / browser / regression). Independent QA remains the closer of Safe-Select risk (outside this plan).

## 21. Implementation Work Breakdown (8 Work Items)

> Files are "likely affected", to be confirmed at implementation time. E2E column: A = API E2E, B = browser E2E.

**WI-1 Metadata Catalog + Concept Registry (storage)**
Goal: carry §9. Scope: new `app/candidate_metadata.py` (CatalogProvider + DDL) + `task_store_v2.py` tables. Data: two tables. Runtime: tables lazily created; no behavior change. Deps: —. Risk: Low. AC: AC-2 (storage semantics). E2E: A. Frontend: no. Flag: none (activates with WI-2's flag).

**WI-2 Coverage Check + auto-registration + invalidation**
Goal: RP↔catalog reconcile; three drift classes. Scope: coverage functions + findings-path hook. Runtime: new finding auto-INCOMPLETE; hash change auto-UNVERIFIED; gone flagged. Deps: WI-1. Risk: Medium (per-turn overhead + failure isolation — coverage failure must not block the turn). AC: AC-2, AC-8(→AC-list: AC-2/AC-8 semantics; see §20 mapping). E2E: A. Frontend: no. Flag: `ENABLE_CANDIDATE_METADATA_COVERAGE`.

**WI-3 Concept Registry seed + full-universe inventory + verified seed records + minimal transition utility**
Goal: registry seed; **inventory the complete current RP candidate universe**; seed verified metadata ONLY for candidates with sufficient verified semantic evidence (F5/F6/F8 are the initial verified examples — not the entire inventory); all other currently exposed candidates are preserved as INCOMPLETE. **No concepts or metadata may be fabricated merely to make a candidate COMPLETE.** The implementation remains data-driven — no candidate-specific code changes, for onboarding or for future candidates. Also ships the dev-facing status-transition utility so the lifecycle (incl. AC-8-style invalidation → re-verify) is exercisable without an authoring workflow. Scope: inventory pass over live RP findings + seed data + utility function/script. Deps: WI-1; domain-owner review of the inventory/seed split. Risk: Low-Medium (the seed/INCOMPLETE split is a governance judgment per candidate; insufficient-evidence candidates must stay INCOMPLETE). AC: AC-11 (baseline universe coverage). E2E: A.

**WI-4 Semantic normalization + SupportEvaluator**
Goal: Registry-constrained normalization + CORE mapping + set predicate. Scope: new `app/semantic_resolution.py`. Runtime: flag-gated. Deps: WI-1/3; goal-vocabulary pattern. Risk: **Medium-High (CJK normalization quality; endpoint fragility → degrade to UNKNOWN)**. AC: AC-3, AC-9 support layer. E2E: A. Frontend: no.

**WI-5 Admissibility Gate + eligibility enforcement**
Goal: contract §10 + eligibility precondition in `InvestigationService`; reason classes; `target_resolution` extension. Scope: `investigation_service.py` gate step. Deps: WI-2/4. Risk: Medium (orchestration; flag equivalence). AC: AC-1, AC-3, AC-9, AC-10. E2E: A + B. Frontend: no.

**WI-6 Clarify ledger + user designation + bounded terminal outcome**
Goal: terminate the UNKNOWN→CLARIFY loop per PMO decision 11. Scope: ledger/designation tables + AMBIGUOUS-branch changes + `non_selectable_terminal` bounded outcome; designation events (intent evidence only — never status writes). Deps: WI-2. Risk: **Medium (user-visible wording; PMO decision already taken on semantics — only exact wording open)**. AC: AC-4, AC-5, AC-6, AC-7. E2E: A + B. Frontend: no required; optional hint deferred.

**WI-7 Telemetry + identity coverage summary**
Goal: three-channel evidence. Scope: telemetry events + identity endpoint. Deps: WI-2/4/5. Risk: Low. AC: AC-8 exposure surface. E2E: A.

**WI-8 Test & E2E bundle + DoD execution**
Goal: §19/§20 fully landed; AC-1..AC-12 verified one by one (incl. baseline universe coverage AC-11 and the F12 regression AC-12); flag-OFF equivalence report. Deps: all. Risk: Low. E2E: A + B.

## 22. Dependencies

RP read-only (no RP changes); **domain-owner review of the full-candidate-universe inventory and the seed/INCOMPLETE split** (one-time at baseline; the authority for "sufficient verified semantic evidence" is governance, not code); QA role for later independent verification; TaskStoreV2 migration mechanism; existing follow-up/clarification frontend rendering.

## 23. Risks

1. **CJK normalization quality** (WI-4): errors degrade safely to UNKNOWN→CLARIFY (experience cost, not safety cost). Mitigation: Registry-constrained proposals, failure⇒UNKNOWN, regression sample set.
2. **LLM endpoint fragility**: multi-call failure surface (measured ~40% in Item 2). Mitigation: fail-closed UNKNOWN, per-turn caching, deterministic layers carry zero LLM dependency (M-A ran 14/14).
3. **Seed authenticity / coverage judgment**: verified seed records are PMO-enacted; risk = (a) seed drift from future RP changes — mitigated by content-hash invalidation (AC-8); (b) an incorrect seed/INCOMPLETE split at inventory time — mitigated by domain-owner review + the coverage report making the split visible; an under-seeded candidate merely stays INCOMPLETE (fail-closed), an over-seeded one is the dangerous direction and requires verified evidence per candidate.
4. **Terminal-outcome wording**: semantics decided by PMO; exact user-facing copy remains open (Low).

## 24. Open Questions (implementation-time decisions only)

1. Exact terminal-outcome copy (semantics decided).
2. Identity-endpoint coverage-summary granularity.
3. Normalization call shape (dedicated call vs piggyback; decide on endpoint-fragility data).
4. `candidate_metadata.source_case` semantics if RP ever reuses a finding across cases (no current evidence — Unknown).
5. Per-candidate "sufficient verified semantic evidence" judgment at seed time: F5/F6/F8 have discovery-backed evidence (Item 2/2A concept tables); any additional inventory candidate without such evidence stays INCOMPLETE — the per-candidate calls are governance reviews during WI-3, not open design questions.

**Removed from open questions (resolved by PMO decision)**: "how can an auto-registered candidate ever become COMPLETE automatically?" — it does not, in V1: automatic registration and autonomous eligibility are intentionally separate; COMPLETE is a governance-side action outside the V1 runtime.

## 25. Definition of Done

**This stage (met)**: revised plan completed and filed for PMO approval; no code implemented; no behavior changed.

**Implementation DoD (per work item where applicable)**:
1. Unit + module/integration + API E2E + browser E2E (where user-visible) all pass, **reported separately by layer**;
2. AC-1..AC-10 each verified as user-action → visible result → backend-state assertions;
3. Flag-OFF equivalence report vs the 2026-09-24 baseline;
4. Three-channel runtime evidence reproducible (API field + server log + telemetry);
5. Independent QA (closer of Safe-Select risk) scheduled separately;
6. Any violation of UNKNOWN/eligibility discipline fails DoD regardless of test counts;
7. **Baseline universe coverage assertion passes**: after WI-2/WI-3, every candidate exposed by the live RP source has a `candidate_metadata` row, COMPLETE only where verified evidence exists, INCOMPLETE otherwise — no silent omission, and no unverified candidate made eligible merely by existing in RP.

## 26. Recommended Implementation Sequence

WI-1 → WI-3 (full-universe inventory + seed/INCOMPLETE split) → WI-2 → WI-4 → WI-5 → WI-6 → WI-7 → WI-8. Stage 1 (bookkeeping + governance substrate + baseline reconciliation, no dialogue change): WI-1/3/2 — exit gate for Stage 1 is AC-11 passing on the live RP universe. Stage 2 (Safe-Select demonstration, double-flag): WI-4/5/6/7. Stage 3: WI-8 (incl. AC-12 regression) + independent QA + flag-default decision.

## 26a. Implementation Baseline (V1 scope decisions of record)

1. The **full current RP candidate universe** is in scope for lifecycle coverage (scope A).
2. Auto-registration is mandatory and data-driven; no candidate-specific code.
3. Candidates with sufficient verified semantic evidence may be seeded COMPLETE (F5/F6/F8 are the initial verified examples).
4. All other currently exposed candidates remain INCOMPLETE — preserved, visible, never fabricated.
5. Explicit user selection is separate from Safe-Select and never gated by metadata.
6. No automatic metadata approval is introduced in V1 (COMPLETE is governance-side only).
7. No governance UI/workflow is introduced in V1 (dev-facing utility only).
8. Frozen refinement remains untouched.
9. MCP remains out of scope.
10. `ENABLE_CANDIDATE_METADATA_COVERAGE` and `ENABLE_SAFE_SELECT_ADMISSIBILITY` remain OFF by default until rollout approval.

## 27. Acceptance Scenarios (authoritative set for this plan)

| AC | Scenario | Primary validation |
|---|---|---|
| AC-1 | Known verified candidate (seeded F5/F6/F8) can be autonomously Safe-Selected | browser + API |
| AC-2 | Previously unknown candidate is automatically registered, zero code changes | API E2E (simulated new RP finding) |
| AC-3 | New candidate stays INCOMPLETE ⇒ cannot be autonomously Safe-Selected (Support UNKNOWN) | browser + API |
| AC-4 | The same new candidate CAN be explicitly selected by the user and investigated | browser |
| AC-5 | Explicit selection does not modify metadata status nor grant eligibility | API E2E (status assertions) |
| AC-6 | Clarification can produce a user designation and terminate the autonomous-selection loop | browser + API |
| AC-7 | Repeated unresolved clarification with no state change reaches the bounded terminal outcome | API E2E |
| AC-8 | Source semantic change on a verified candidate → UNVERIFIED (invalidation + re-verification path) | API E2E |
| AC-9 | LLM output cannot bypass metadata eligibility or the deterministic gate | component + API (adversarial proposal cases) |
| AC-10 | Flags OFF ⇒ behavior identical to current baseline | regression equivalence |
| AC-11 | **Baseline universe coverage**: at implementation-baseline time, every candidate exposed by the live RP source is present in `candidate_metadata`; verified-evidence candidates are COMPLETE; the rest are INCOMPLETE; no candidate is silently omitted; no unverified candidate is Safe-Select-eligible merely by existing in RP | API E2E + coverage report |
| AC-12 | **Future-candidate regression**: RP introduces a previously unseen F12 → Coverage Check detects it → auto-registered INCOMPLETE → zero code change → never autonomously Safe-Selected until eligibility exists → explicit user designation remains separately available | API E2E (simulated new RP finding) + browser |

## Appendix A — Contract Gap / Clarification Register (revised)

| # | Finding | Classification | Handling |
|---|---|---|---|
| G-1 | Metadata Contract §16 sketches onboarding but no runtime carrier | Implementation decision | V1: seed + minimal transition utility (WI-3); full authoring workflow deferred post-V1. Contract unchanged. |
| G-2 | Repeated-CLARIFY termination semantics | **RESOLVED — PMO-directed implementation decision** (§0 decision 10/11; §12). Only exact user-facing wording remains open. | WI-6 |
| G-3 | Explicit selection of an ineligible candidate | Implementation decision, consistent with Contract (eligibility governs SELECT admission, not user selection) | Explicit Selection path + designation events (WI-6) |
| G-4 | RP provides no content-version field | Implementation decision | content-hash surrogate (§13) |
| G-5 | Normalization call shape | Open implementation question | decide on endpoint-fragility data |
| G-6 (new) | Metadata Contract §16 onboarding steps could be misread as runtime-auto-completion | Clarified by PMO decision — Contract text unchanged; interpretation recorded here and in §0/§5 | automatic registration ≠ automatic verification/eligibility |
| G-7 (new) | Full RP candidate universe must be covered at baseline, but not every candidate can be seeded COMPLETE | PMO-directed scope decision (§0 universe coverage; §26a) — consistent with Metadata Contract §21 Coverage Check; no Contract change | WI-3 inventory + AC-11; insufficient-evidence candidates stay INCOMPLETE (fail-closed) |

## Appendix B — Verified Code/Docs Basis (unchanged from rev. 1)

`app/target_refinement.py` (frozen, fp `9842ec834bdb`) · `app/investigation_service.py` · `app/api/investigations.py` · `app/api/schemas.py` · `app/context_resolution.py` · `app/goal_resolution.py` · `app/task_store_v2.py` · `app/config.py` · `app/telemetry.py` · `app/models.py` · `app/domain_tools/risk_case_fetch.py` path · Metadata Contract · WP-M2-1 Contract · SP-M2-1C / Item 2 / Item 2A baselines · SP-M2-1 final QA · SP-M2-1R validation doc. Environment re-verified: backend default flag OFF, RP live with F5/F6/F8 titles unchanged.
