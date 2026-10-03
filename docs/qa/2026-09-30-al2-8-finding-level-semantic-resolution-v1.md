# AL-2.8 — Finding-Level Natural Language Investigation (M2 Semantic Foundation) — QA v1

**Date**: 2026-09-30
**Status**: **PASS WITH LIMITATIONS** (see §18/§20)
**Scope**: Conversational finding resolution re-grounded on the M2
semantic foundation (Concept Registry + Candidate Metadata +
SemanticNormalizer), with honest LLM-unavailable degradation. No
Safe-Select/authorization change.

---

## 1. Objective

Open the real finding-level conversational path:

```
Raw User Language → Semantic Resolution → Canonical Concept
→ Candidate/Finding Grounding → Intent → Governed Planning
→ Existing Tool → Evidence → Natural-Language Response
```

and make `LLM unavailable ≠ 假装正常` an implemented, tested product
behavior.

## 2. Architecture Decision

**B — reuse the M2 semantic foundation WITH explicit boundaries**
(architecture audit, this session):

- M2 Concept Registry / Candidate Metadata / SemanticNormalizer ARE the
  semantic foundation for conversational finding resolution.
- **Resolution ≠ Authorization**: the resolution stage grounds targets
  conversationally; it NEVER confers Safe-Select eligibility. The WI-5
  `SafeSelectGate` remains the only authorization point. Nothing in
  `admissibility.py` / `semantic_resolution.py` (SupportEvaluator) was
  modified.
- `finding_lexicon` remains as a deterministic exact-match fast path for
  its two canonical phrases (compat value) and is explicitly documented
  as **not** the canonical semantic authority; it was not extended.
- No new ontology / registry / routing dictionary was created.

## 3. M2 Semantic Foundation Reuse

| M2 component | Role in AL-2.8 | Modified? |
|---|---|---|
| Concept Registry (`semantic_concepts`) | normalized concept source of truth | +2 concepts (F3 authoring, §6) |
| Candidate Metadata (`candidate_metadata`) | grounding target facts (core/distinguishing/mechanism) | +F3 authoring; re-author semantics in seeding (§6) |
| `SemanticNormalizer` (WI-4) | user NL → ONE governed concept proposal (registry-conformed, verbatim-grounded) | unmodified; **new consumer** |
| `SupportEvaluator` (WI-4) | Safe-Select support evidence only | unmodified, untouched |
| `SafeSelectGate` (WI-5) | authorization (COMPLETE + unique + non-equivalence + confidence + stability) | unmodified, untouched |
| Coverage Check (WI-2) / seeding (WI-3) | governed provisioning | seeding gained placeholder re-author semantics (§6) |

Wiring (the only new runtime path):

```
ContextResolver.resolve(...)            # existing deterministic stages
  ... explicit focus → case-level → title tokens → finding_lexicon
  → [NEW] Priority 3.5: semantic stage (when catalog wired + flag on)
      SemanticNormalizer.normalize(request, ACTIVE concepts)   # LLM proposes
      → deterministic grounding: concept ∈ core_concepts of
        CURRENT-CASE candidates (DEPRECATED excluded; §5)
      → UNIQUE → RESOLVED (focus + provenance)
        | AMBIGUOUS → clarification with grounded candidates
        | UNKNOWN (no concept / ungrounded / invented id) → fall through
        | provider outage (llm_unavailable / malformed) →
          SEMANTIC_RESOLUTION_UNAVAILABLE (§9)
  → Stage-1 generic LLM assist (unchanged) → unresolved
```

Cross-account scope wording is excluded from the semantic stage
(case-level analytical universe, never a single finding).

## 4. Resolution vs Authorization Boundary

Implemented and tested:

- Grounding reads `core_concepts` **regardless of metadata status**:
  `INCOMPLETE`/`UNVERIFIED` candidates stay conversationally reachable
  (F3 is UNVERIFIED and grounds; test
  `test_unverified_status_does_not_block_reachability_f3`).
- Only `DEPRECATED` is excluded (Metadata Contract §24; test
  `test_deprecated_record_is_excluded_from_grounding`).
- The grounding result is provenance (`semantic_evidence`: concept,
  verbatim fragment, grounded ids, metadata status in the reason), never
  an authorization fact. `target_resolution.semantic_grounding` is
  observational API evidence.
- Mechanism concepts do not ground (F6 records
  `SHARED_DEVICE_RELATIONSHIP` as mechanism → device wording grounds F5
  alone; test `test_mechanism_concept_does_not_ground_the_competitor`).

## 5. Semantic Resolution Runtime

- Stage lives in `backend/app/context_resolution.py`
  (`_semantic_resolve`), active when a governed catalog is wired AND
  `ENABLE_SEMANTIC_FINDING_RESOLUTION=true` (default on; OFF = legacy
  chain byte-for-byte).
- Catalog port: `TaskStoreCatalogProvider` (same SQLite store — no
  second database). Wired in `InvestigationService(semantic_catalog=…)`,
  the default API construction, and `_default_loop_runner`.
- LLM: the project's `ClaudeProvider` convention; all validation is
  system-side (registry conformance, verbatim fragment). Confidence is
  recorded as provenance and never decides.
- Grounding universe = the caller-supplied authoritative findings of the
  CURRENT case (test
  `test_grounding_restricted_to_the_current_case`).

## 6. F5 / F3 Metadata (governed authoring)

Seed v2 (`backend/seed/candidate_metadata_seed.json`, seed_version 2):

- **F5/F6/F8**: unchanged (UNVERIFIED; pre-registered concepts).
- **F3 (new authoring, minimal)**:
  - core: `HIGH_WITHDRAWAL_FREQUENCY`
  - distinguishing: `WITHDRAWAL_FREQUENCY` (vs F4's one-time
    new-address semantics)
  - potential_confusions: `["F4"]` (already recorded at provisioning)
  - status: **UNVERIFIED** (authored pending independent verification —
    Safe-Select ineligible; conversational reachability unaffected)
  - provenance: authored during AL-2.8 from the live RP description
    (7 withdrawals / 24 h above threshold); sources cite this QA doc +
    the metadata contract.
- **Re-author semantics** (`metadata_seeding.seed_from_document`): an
  empty `INCOMPLETE` PLACEHOLDER (Coverage-Check auto-provision, or a
  seed-sourced declaration completed by a NEWER seed version) may be
  completed by an authored seed entry — onboarding Step 2 → Step 3.
  Authored records / non-empty concepts / DEPRECATED / manual records
  are never overwritten (tests:
  `test_reauthor_semantics_completes_*`).
- M2 contract: **not modified**.

## 7. Scenario A — 「共享设备为什么是风险项」 → F5 explain

Real stack (QA backend :8010, real RP :8000, real LLM):

- Resolution: lexicon exact hit → F5 (deterministic; the paraphrase
  below exercises the M2 path).
- Plan (deterministic explain branch, contract-checked):
  `explain_signal → retrieve_policy`.
- Tools (real): `signal_explain` SUCCESS (after the graph-derivation
  fix, §16) + `policy_lookup` SUCCESS.
- Assistant answer (real): "This finding was flagged by graph-based
  risk detection. Risk Platform graph data shows: detection type
  “device_sharing”, 19 accounts in the cluster, cluster risk score 80.0.
  Shared devices linking these accounts: DEV8Z1O4A35QU. Related accounts
  … For this finding, 1 policy reference applies directly:
  AML_Suspicious_Indicators.md / 5.1 Links to Known Risky Clusters [3] …"
  — identity + real evidence + why-it-is-risk + real citations.

**A′ paraphrase (the new M2 path, real LLM)**:
「为什么会标记出设备共享的关系」 (no lexicon hit) →
`SHARED_DEVICE_RELATIONSHIP` (fragment 「设备共享的关系」, registry 8
concepts) → grounded `["F5"]` → same governed plan → same substantive
graph answer.

## 8. Scenario B — 「给我输出一下高频提现的具体证据」 → F3 evidence

Real stack:

- Paraphrase 「给我输出一下提现频繁的具体证据」 (no lexicon hit) →
  real LLM → `HIGH_WITHDRAWAL_FREQUENCY` (fragment 「提现频繁」) →
  grounded `["F3"]` (metadata UNVERIFIED — reachable).
- Plan: `inspect_withdrawals` (locked `view=evidence`,
  `stream=withdrawals`).
- Tool: `finding_drilldown` SUCCESS — **7 real withdrawal records**
  (W000087/W000084/W000088/…, real RP data with timestamps, assets,
  addresses).
- Assistant answer (natural language, carries the records):
  "The Risk Platform returned 7 withdrawal records supporting this
  finding. All records are shown below. - 2026-07-21T01:31:49…:
  Withdrawal of 4.141117 USDT to a newly encountered address [W000087] …"

## 9. LLM Unavailable Behavior

Triggers (implemented): no API key, provider construction failure,
provider exception (timeout/connection/HTTP), malformed structured
output — each after the deterministic stages already failed.

Behavior (verified live on a no-key instance :8011):

- outcome `SEMANTIC_RESOLUTION_UNAVAILABLE` (conversation:
  `task.error="semantic_resolution_unavailable"`, response carries the
  governed message 「我暂时无法可靠判断你指的是哪个风险发现，因为语义解析服务当前不可用。请直接在
  Findings 面板指定风险项/发现，或稍后重试。」; agent loop: BLOCKED +
  `StopReason.SEMANTIC_RESOLUTION_UNAVAILABLE`).
- No plan, zero tool execution, no fabricated resolution, **no keyword
  fallback** for the semantic step.
- Distinct from `unresolved_reference` at every surface (task error
  code, stop reason, `target_resolution.kind`).
- Deterministic exact resolution still works without an LLM (verified:
  canonical phrase → F5 → real tools on the no-key instance).
- Retry/fallback: NO provider fallback exists in this runtime; single
  attempt, honest terminal. No failover framework was built (per scope).
  Observed once in live validation: a transient provider hiccup on one
  turn produced the honest unavailable outcome; an immediate retry
  succeeded.

## 10. Negative / Ambiguous Cases

- **Nonexistent finding** 「谈谈虚拟币洗钱这个风险项」 (fresh
  investigation, real LLM): semantic stage → no governed concept →
  fall-through → Stage-1 assist → bounded outcome; **zero tool
  execution** (API test: `task.error="unresolved_reference"`,
  `execution=None`; browser: bounded clarification card
  `clarification_needed`, no fabricated answer).
- **Shared-core concept ambiguity**: a concept in two candidates' cores
  → AMBIGUOUS with both candidates, context NOT mutated (unit test).
- **Invented concept id**: rejected → fall-through (unit test).
- **Cross-account scope wording**: 「比较不同账户的提现频率」 stays out
  of the semantic stage → unresolved (existing behavior preserved).

## 11. API E2E (`backend/tests/test_al28_semantic_foundation_v1.py`, 20 tests)

Real HTTP `/turn` + real `TaskStoreV2` + real repo seed + real planner
contract gate + real tools with the RP boundary controlled (repo E2E
convention); the normalizer/planner LLMs are scripted — deterministic
boundary validation, NOT real-LLM claims:

- Scenario A full chain: paraphrase → concept → F5 → plan
  `[explain_signal, retrieve_policy]` → tools success → NL response →
  `semantic_grounding` evidence on the turn.
- Scenario B full chain: → `HIGH_WITHDRAWAL_FREQUENCY` → F3 →
  `inspect_withdrawals` (locked args) → normalized evidence view with
  records via `/tasks/{id}/results` → NL response.
- LLM unavailable: honest outcome, no plan, no execution, distinct task
  error (§9).
- Negative/ambiguous/flag-off/no-catalog/deprecated/re-author: §5/§6/§10.

**All 20 PASS.**

## 12. Browser E2E (`qa/journey_al28_finding_semantic.py`)

Real headless Chrome → real frontend (:3011 QA vite → proxy :8010) →
real QA backend (current code) → real RP → real LLM. Each scenario on a
FRESH investigation through the real UI; ONE scenario per Chrome
instance (long headless sessions proved unstable in this environment —
a harness limitation, not a product one). Screenshots
`qa/screenshots/al28-01…04`:

| Leg | Raw input | Oracle | Result |
|---|---|---|---|
| A canonical | 「共享设备为什么是风险项」 | live reply: graph-based detection + real shared-device evidence + policy citation | **PASS** |
| A′ paraphrase (real-LLM semantic) | 「为什么会标记出设备共享的关系」 | same, via M2 grounding | **PASS** |
| B evidence | 「给我输出一下高频提现的具体证据」 | live reply with real withdrawal records (W00…) | **PASS** |
| N negative | 「谈谈虚拟币洗钱这个风险项」 | bounded honest terminal (observed live: both the bounded-clarification variant and the "couldn't safely determine" unresolved variant), no fabricated finding/answer, zero tools | **PASS** |
| Persistence ×3 | page reload + investigation reopen | assistant responses retained | **PASS** |
| Transport | — | page API calls ok, no network failures | **PASS** |

Harness notes (not product findings): long headless Chrome sessions
proved unstable in this environment → one scenario per Chrome instance;
the real provider intermittently fails single calls → the journey
honors the product's own 「稍后重试」 guidance with at most ONE re-send;
reply waits require the pending skeleton to clear (real-LLM turns are
slow). Earlier failing runs were harness issues (typographic apostrophe
in DOM text, premature assertion on the pending skeleton) — each was
reproduced, root-caused, and the scenario re-run to PASS.

**Browser journey: PASS.**

## 13. Real LLM Validation

- Provider: glm-5.3 via the configured Anthropic-compatible endpoint
  (real network calls; no scripts).
- Proven semantic normalizations: 「提现频繁」→`HIGH_WITHDRAWAL_FREQUENCY`;
  「设备共享的关系」→`SHARED_DEVICE_RELATIONSHIP` (verbatim fragments
  validated system-side).
- **Provider flakiness is real and was observed live**: the endpoint
  intermittently fails individual calls (observed at API level and once
  in the browser A′ leg). The product answered every failure with the
  honest `SEMANTIC_RESOLUTION_UNAVAILABLE` outcome — which is exactly
  the designed behavior (§9). No silent retry, no fallback masquerade.
  The browser journey honors the product's own user guidance
  ("或稍后重试") with ONE harness-level re-send; retries succeeded.
  A production bounded-retry for transient provider errors remains a
  recommended follow-up (§18.5).
- Both scenarios were validated with the real LLM at API level AND in
  the real browser (A′ leg).

## 14. Conversation Response Evidence

- Conversation surface carries the natural-language answer (§7/§8
  excerpts); runtime metadata (`Steps completed`, tool digests) is not
  the answer on the finding-level path (loop-path composer keeps the
  answer-first ordering from the earlier AL-2.8 work, pinned by unit
  tests).
- Activity = trajectory observability; Artifacts unchanged; no
  Activity UI redesign.

## 15. Persistence

Reload + reopen verified for A, A′, B in the real browser (§12) —
responses restored from the persisted investigation read model.

## 16. Minimal Necessary Changes (files)

| File | Change | Why minimal-necessary |
|---|---|---|
| `app/context_resolution.py` | semantic stage + `SEMANTIC_RESOLUTION_UNAVAILABLE` + `semantic_evidence` | THE feature |
| `app/investigation_service.py` | consume the new status; expose grounding evidence; Graph-fact sentences in the answer composer | honest terminal + §7 answer substance |
| `app/agent_loop.py` | `StopReason.SEMANTIC_RESOLUTION_UNAVAILABLE` + BLOCKED branch | loop-boundary honesty |
| `app/api/investigations.py` | wire `semantic_catalog` (service + loop runner) | runtime connection |
| `app/main.py` | startup governed-metadata seeding | the catalog was never provisioned at runtime (audit finding) |
| `app/metadata_seeding.py` | placeholder re-author semantics | governed onboarding Step 2→3 convergence |
| `app/config.py` | `ENABLE_SEMANTIC_FINDING_RESOLUTION` (default on) | kill switch; OFF = legacy |
| `app/domain_tools/signal_explain.py` | Feature-only + graph-relevant title → derive `Graph` | §7: F5's "why" was unreachable (Rule default → missing-evidence only); the Graph branch already existed and stays evidence-honest |
| `seed/candidate_metadata_seed.json` | F3 authoring (§6) | Scenario B depends on the M2 foundation |
| `frontend/vite.qa.config.ts`, `qa/journey_al28_finding_semantic.py` | QA-only harness | isolated QA stack (did not touch standing dev processes) |
| tests | new `test_al28_semantic_foundation_v1.py` (20) + WI-3/WI-2 expectation updates to seed v2 + two script-offset reconciliations (§17) | coverage + regression truth |

Not modified: SafeSelectGate, SupportEvaluator, ClarificationLedger
core semantics, RP, tool contracts (beyond §16's derivation), planner
architecture, Activity UI, frontend product code.

## 17. Regression

- AL-1/AL-2/AL-2.5/AL-2.6/AL-2.7 suites + M2 WI-2/3/4/5/6/7 suites:
  **PASS** (81 + 60 tests; post-change re-runs after every later edit).
- Signal/composition suites after the graph-derivation change: 87 PASS
  (`test_signal_explain_v1`, week1 matrix, both E2E files, AL-2.8).
- Two deterministic test-script offsets FIXED (inherited, pre-session):
  the prior AL-2.8 planner explain branch hijacked scripted turns
  (`test_api_investigations_v1::test_turn_sequence_matches_script`,
  `test_investigation_e2e_v1` fixture) — verified pre-existing via
  stash-run, reconciled to the governed branch's intended behavior.
- **Pre-existing failures, inherited unchanged (verified by stash-run
  without this session's changes)**: `test_error_handling` 4 (legacy
  `app/agent.py` path), `test_agent_loop_v1` re-plan ×2,
  `test_tool_orchestration_e2e_al2_final` ×4 — inside the documented
  environment baseline (~12 with RP up).

## 18. Known Limitations

1. **Focus priority over new NL references (pre-existing Stage-1
   invariant)**: within one conversation, an established
   explicit/agent-resolved focus wins over a NEW natural-language
   finding reference ("F5 之后再问提现频繁" stays on F5). Switching
   requires explicit selection/clarification. Unchanged by AL-2.8;
   browser scenarios therefore run on fresh investigations. Candidate
   future work (needs a Stage-1 contract decision, out of scope here).
2. **F3 Safe-Select stays ineligible** (UNVERIFIED): intentional
   (§4). Independent verification is a QA-role governance step.
3. Registry coverage beyond F3/F5/F6/F8 remains INCOMPLETE
   (F1/F2/F4/F7) — those findings ground only via deterministic paths
   (title/lexicon/generic assist), exactly as before.
4. `language_variants` remain unimplemented (contract-OPTIONAL,
   unused); the M2 path does not depend on them.
5. No LLM provider failover exists; single-attempt + honest terminal
   (per scope: no new failover framework).
6. Legacy `test_error_handling` failures are untouched (different
   subsystem, pre-existing).

## 19. Definition of Done

| Item | Status |
|---|---|
| M2 Registry/Metadata = finding-resolution semantic foundation | **DONE** (§3/§5) |
| SemanticNormalizer wired into conversational resolution | **DONE** |
| No new long-term semantic authority | **DONE** (lexicon demoted to fast path, not extended) |
| F5 conversationally resolvable | **DONE** (browser A + A′) |
| F3 conversationally resolvable | **DONE** (browser B) |
| explain_finding intent → governed planning | **DONE** (deterministic branch + contract check) |
| evidence request → governed planning | **DONE** (`inspect_withdrawals` locked) |
| Existing tools execute | **DONE** (real tools, real RP) |
| Real RP data in responses | **DONE** (§7/§8) |
| Natural-language assistant response | **DONE** |
| UNKNOWN/AMBIGUOUS/unsupported boundaries | **DONE** (§10) |
| Resolution ≠ authorization; INCOMPLETE doesn't block reachability | **DONE** (§4) |
| LLM unavailable: explicit degraded behavior; no fake; distinct from unresolved_reference; no keyword-agent fallback; safe terminal | **DONE** (§9) |
| API E2E from raw NL | **DONE** |
| Browser E2E from raw NL | **DONE** |
| Real-LLM semantic validation | **DONE** |
| LLM-unavailable scenario | **DONE** |
| Negative/ambiguous scenario | **DONE** |
| Response live + reload + reopen | **DONE** |
| Conversation/Activity/Artifact boundaries | **DONE** |
| Regression accounted (AL-1..2.7, M2/Safe-Select) | **DONE** (§17) |

## 20. Final Status

**PASS WITH LIMITATIONS** → **PASS** after canonical-runtime validation
(addendum below): the remaining limitation class attributed to the
validation surface (§18.5 aside) is closed by the canonical re-run.

- Real-LLM semantic path, governed planning, real RP evidence,
  natural-language responses, honest degradation, negative boundaries,
  persistence: **validated at API + real-browser level**.
- Limitations are §18 (focus-priority semantics, F3 verification
  pending, partial registry coverage, no provider failover) — all
  documented, none blocking the AL-2.8 objective.
- Pre-existing failures (§17) are inherited, verified not introduced by
  this work item.

---

## Addendum — Canonical Runtime Validation (2026-09-30, post-AL-2.9)

The original acceptance journeys ran on the isolated QA stack
(:3011 → :8010) because the canonical backend (:8001) was a stale
process running `cc56544` and its restart was (correctly) not performed
without explicit user authorization. The canonical validation has now
been completed with user approval:

- **Canonical runtime** (codified in CLAUDE.md "Canonical runtime"):
  frontend :3001 (`frontend/vite.config.ts`) → backend :8001 (uvicorn)
  → RP :8000, sharing `backend/data/investigation_agent_v2.db`.
- **Pre-restart evidence**: `:8001/api/v2/runtime/identity` →
  `git_commit: cc56544` (the AL-2.8/2.9 work is uncommitted working
  tree; cc56544 is HEAD both before and after — commit identity alone
  does NOT date a process).
- **Restart**: :8001 and :3001 restarted onto the current working tree
  with user authorization. Startup log shows the governed-metadata
  seeding line — a new-code behavioral marker (the identity endpoint's
  implementation fingerprints are computed from disk at request time
  and cannot prove what a long-running process loaded).
- **Canonical API smoke** (real LLM + real RP): A 「共享设备为什么是风险项」
  → F5 graph answer ✓; B 「为什么说高频交易是风险项，给我输出高频交易的
  具体交易明细」→ `HIGH_TRADING_FREQUENCY` → F7 →
  `explain_signal + inspect_transactions` ✓; negative → bounded
  honest unresolved, zero tools ✓.
- **Canonical browser journeys** (`FRONTEND_BASE=http://localhost:3001/`,
  journey scripts now parameterized): al28 a/a2/b/n — **4/4 PASS**;
  al29 a/b/cmp/n — **4/4 PASS** (including reload/reopen persistence).
- **QA stack torn down** (:8010/:8011/:3011). Only the canonical stack
  and the RP remain running.
