# Goal Satisfaction & Claim-Specific Postconditions — Implementation Plan v1

**Status: Draft for PMO Review — Implementation Plan Only; Not Approved for Execution**
**Execution record**: WP-GS-A (universal completion-claim guard + legacy DI seam) was explicitly authorized by the PMO and has been implemented and validated (QA record: `docs/qa/2026-10-10-wp-cgsa-completion-guard-qa.md`). WP-GS-B (destination-identity evidence path) was subsequently authorized and implemented and validated (QA record: `docs/qa/2026-10-10-wp-cgsb-destination-identity-qa.md`; browser journey `qa/journey_cgsb_l3.py`). WP-GS-C/D/E remain gated pending separate approval and per-WP review.

**Date**: 2026-10-09
**Approved design basis**: `docs/plans/2026-10-09-goal-satisfaction-claim-specific-postconditions-design-v1.md`
(Revision 3 — approved for implementation planning only).
**Audit basis**: `docs/qa/2026-10-09-manual-acceptance-read-only-audit.md`.
**Execution gate**: NO source-code changes may begin until this plan is
explicitly approved by the PMO (§16). This document implements nothing.

---

## 1. Executive Summary

WP-GS closes the audited false-completion path — "relevant records were
retrieved ⇒ the user's request was answered" — with five small work
packages:

- **WP-GS-A** (first, safety): a flag-independent universal
  completion-claim guard at the loop closure + a test-only DI seam that
  isolates byte-exact legacy wording to the baseline regression suite.
- **WP-GS-B**: destination-identity preservation and a deterministic
  `destination_identity` fact block in the evidence tool result,
  projected into the observation and rendered with a 目的地 column.
- **WP-GS-C**: the `PostconditionFacet` vocabulary, proposal/contract
  admission, and the deterministic request-coverage assessment with the
  `comparison_intent_uncovered` disclosure.
- **WP-GS-D**: the GSP-4 facet evaluation branch producing existing
  satisfaction statuses (no new enum).
- **WP-GS-E**: canonical-stack API E2E (the 21 acceptance scenarios of §10.1),
  real-browser journeys, and full regression.

Recommended order: **GS-A → (GS-B ∥ GS-C) → GS-D → GS-E**. GS-A lands
user-facing safety first and is independent; GS-B and GS-C touch
disjoint files and can proceed in parallel; GS-D consumes both.

## 2. References and Source-of-Truth Hierarchy

1. **Approved design (architectural truth)**:
   `docs/plans/2026-10-09-goal-satisfaction-claim-specific-postconditions-design-v1.md`
   (Revision 3).
2. **Audit (evidence truth)**:
   `docs/qa/2026-10-09-manual-acceptance-read-only-audit.md`.
3. GSP baseline/design/implementation plans (GSP-4 evaluator contract,
   GSP-5 compose, GSP-6 coverage, GSP-7 preservation).
4. CG-1..CG-5 design/implementation/QA records (extraction, acceptance,
   contract-less safety, API E2E, browser/exploratory).
5. `CLAUDE.md` (DoD, runtime freshness, browser E2E mandates).

Conflicts: the approved design wins on architecture; the current
worktree wins on code facts; discrepancies are recorded in §3 and
escalated as blockers — never silently resolved.

## 3. Verified Current-State Architecture and Entry Points

All anchors re-verified against the current worktree immediately before
writing this plan (HEAD `58881a9` + the CG/GSP working tree; 126
uncommitted paths). Line numbers are current-tree references.

| # | Verified fact | Location |
|---|---|---|
| V-1 | `PostconditionFacet` does not exist yet; no facet/coverage code anywhere | `grep` over `app/` — zero hits |
| V-2 | `ClaimKind` enum placement (the pattern `PostconditionFacet` will follow) | `app/goal_resolution.py:122` |
| V-3 | `RequiredOutput` fields (output_id, claim, claim_kind, claim_description, subject_ref, scope, evidence_requirements, satisfaction, polarity, detail) — no facet field | `app/goal_resolution.py:137-166` |
| V-4 | `build_goal_contract` per-item all-or-nothing validation loop; ids assigned in-loop; resolution passed via `goal_representation.goal_resolution` | `app/goal_resolution.py:285-370` (COMPOUND dedup block added by CG-2 near the end) |
| V-5 | `understand_goal` flag-gated GSP prompt block (where facet proposal guidance will be added) | `app/goal_resolution.py:487-528` |
| V-6 | Adapter preserves `address`/`is_new_address` per withdrawal record; `normalize_evidence_records(case_id, evidence, finding, stream)` returns `{records, record_count, streams, complete}` | `app/adapters/risk_platform.py:788+` (address at :840) |
| V-7 | `finding_drilldown` view="evidence" assembles `{records, record_count, streams, complete}` into ToolResult.data | `app/domain_tools/finding_drilldown.py:314+` (payload assembly after :360) |
| V-8 | **Loss point L1**: `project_observation` view="evidence" builds `record_rows` of `{record_id, timestamp, summary}` only | `app/agent_loop.py:266-280` |
| V-9 | **Loss point L2**: composer evidence table renders `| # | 记录 ID | 时间 | 明细 |` from `record_rows` | `app/agent_loop.py:2423-2446` |
| V-10 | GSP-4 evaluator: `_category_postcondition(req, output, summary, case_id)` per requirement; WITHDRAWAL_RECORDS branch = retrieval-only SATISFIED; ANY aggregation by `_VERDICT_PRECEDENCE`; `evaluate_output(output, case_id, observations)` | `app/goal_postconditions.py:148, 278, 295` |
| V-11 | Legacy completion: `criteria_satisfied()` ANY-success; StopReason.GOAL_SATISFIED | `app/agent_loop.py:585-606` |
| V-12 | Composer legacy-closure site (the universal-guard branch point): `compose_loop_response` contract-absent path emitting "The investigation completed." | `app/agent_loop.py` (compose_loop_response; closure lines ~2448-2465 after CG-3 edits) |
| V-13 | CG-3 decision provenance `state.goal_contract_decision` written at the goal checkpoint | `app/agent_loop.py:~1195-1230` |
| V-14 | Turn composer `compose_response` — renders strictly from ToolResult data; emits NO goal-completion claim (verified wording audit) | `app/investigation_service.py:148+` |
| V-15 | Activity projection serves goal_contract/goal_outcome/goal_contract_decision additively | `app/agent_loop.py:project_task_activity` |
| V-16 | Existing matcher pattern for the coverage detector (bounded, deterministic, unit-tested vocabulary matchers) | `app/investigation_concepts.py`; `app/goal_resolution.py:_HEURISTIC_RULES` |
| V-17 | GSP-6 admission + RE_PLAN re-admission recompute `planning_coverage`/`step_output_map` (untouched by WP-GS) | `app/agent_loop.py` (adoption sites), `app/planning_coverage.py` |
| V-18 | CG-3 universal-guard precedent at the composer closure (flag ON + contract absent) — GS-A generalizes it to flag-independent | CG-3 QA record; `app/agent_loop.py` composer branch |

Confidence: all rows read in the current worktree during planning.
Item V-7's exact payload-assembly line for the `data` dict is within
±20 lines (the assembly follows the view dispatch); implementers re-
confirm at edit time.

## 4. Work-Package Decomposition and Dependency Graph

```
WP-GS-A (universal completion guard + legacy DI seam) ──┐ (independent; FIRST)
                                                        │
WP-GS-B (destination_identity evidence path) ──────────┤
                                                        ├──► WP-GS-E (E2E + regression validation)
WP-GS-C (facet vocabulary + admission + coverage) ──────┘        ▲
        └──► WP-GS-D (GSP-4 facet evaluation) ────────────────────┘
```

- **GS-A** depends on nothing (composer closure + a test-only seam).
- **GS-B** depends on nothing (tool/adapter/observation layers).
- **GS-C** depends on nothing for the detector; the coverage MARKING
  reads the facet field introduced in GS-C itself (enum + validation
  live in GS-C; see the decomposition note below — the facet vocabulary
  and the coverage check are deliberately co-located in one WP because
  the coverage rule is "detected intent without a faceted output").
- **GS-D** depends on GS-C (facet field on RequiredOutput) and GS-B
  (the fact block it evaluates). Its pure-rule L1s can be written in
  parallel against fixtures.
- **GS-E** depends on GS-A+GS-B+GS-C+GS-D all merged.

Parallelization: GS-B ∥ GS-C are file-disjoint
(`finding_drilldown.py` + `agent_loop.py` observation branch vs
`goal_resolution.py` + `investigation_concepts.py` + checkpoint
marking); GS-A is a third disjoint region (composer closure + seam).
After GS-D merges, GS-E runs the full validation.

### 4.1 WP-GS-A — Universal completion-claim guard + legacy DI seam

1. **Objective**: no user-visible runtime may present execution
   completion as whole-request goal completion without an accepted
   contract's derived outcome; byte-exact legacy wording survives only
   behind a test-only injection seam.
2. **Scope**: the `compose_loop_response` contract-absent closure;
   a module-level completion-closure policy hook; the DI seam.
3. **Out of scope**: turn-path wording (verified to emit no goal
   claims — V-14; a future turn contract path is out of scope per the
   design), GSP-5 contract-path compose, flags.
4. **Dependencies**: none. Entry: approved plan.
5. **Tasks**:
   a. Add `_completion_closure_policy() -> "GUARD" | "LEGACY"` in
      `app/agent_loop.py` — production always returns GUARD; no env,
      no flag, no runtime path to LEGACY.
   b. In the `compose_loop_response` contract-absent branch: when the
      policy is GUARD and `agent_status == COMPLETED`, the closure is
      the basis-scoped statement (「本次调查已完成证据检索与执行；系统未
      建立目标语义契约，不对"您的请求已得到完整回答"作出判定。以下结果
      可能未覆盖请求的全部内容。」) instead of the bare completion
      phrase; FAILED/BLOCKED terminals keep their existing honest
      wording.
   c. DI seam: the policy function reads a module-level hook
      (`_completion_closure_policy_override`) that ONLY test code
      sets (monkeypatch/fixture); production code never imports or
      sets it outside `agent_loop.py`.
   d. Coverage-incomplete disclosure (delivered in GS-C, wired here):
      when `goal_contract_decision.coverage ==
      "comparison_intent_uncovered"`, append the uncovered-intent
      disclosure line after the covered-deliverable outcome.
6. **Files (verified)**: `app/agent_loop.py` (closure branch +
   policy hook). **New**: `tests/test_cg_gs_a_completion_guard_v1.py`.
7. **Tests**: guard table (flag ON/OFF × contract present/absent ×
   terminal status → exact closure text); LEGACY seam reproduces the
   byte-exact baseline wording; negative probe — LEGACY unreachable
   without the test injection (assert via reading the module hook
   default); Activity/terminal consistency unchanged.
8. **DoD**: L1 green; existing suites that assert the legacy closure
   text migrate to the DI seam (isolated regression) or the guarded
   wording (canonical); no other suite regressions; QA record updated.
9. **Exit**: guard merged; legacy wording unreachable in production.
10. **Unlocks**: GS-E (partial), GS-B/GS-C/GS-D (parallel start).

### 4.2 WP-GS-B — Destination-identity evidence path

1. **Objective**: destination identity survives to the observation and
   the user-visible table; a deterministic comparison fact block is
   computed where the records are.
2. **Scope**: `finding_drilldown` evidence-view payload;
   `project_observation` evidence branch; composer evidence table.
3. **Out of scope**: facet evaluation (GS-D); claim semantics; any
   address normalization beyond the verified minimal rule (§4.2 of the
   design: trim; `0x` case-insensitive — see the normalization note
   below).
4. **Dependencies**: none. Entry: approved plan.
5. **Tasks**:
   a. Pure helper `_destination_identity_block(records)` in
      `app/adapters/risk_platform.py` (or a small new
      `app/evidence_facts.py` if placing it with the adapter creates
      import awkwardness — implementer's choice, single location):
      groups withdrawal records by trimmed destination address (case-
      insensitive ONLY for `0x`-prefixed values), producing
      `{basis, records_total, records_with_identity,
      records_without_identity, unique_destinations, groups:
      [{destination_ref, address, record_ids, assets}],
      normalization}` — counts only; NO conclusion.
   b. `finding_drilldown` evidence view: when the payload includes
      withdrawal records, attach the block to ToolResult.data.
   c. `project_observation` evidence branch: project a bounded
      `destination_identity` summary (counts + groups capped at the
      existing evidence-rows bound) + per-row
      `has_destination_identity`.
   d. Composer evidence table: add the 目的地 column from the
      per-record address; conclusion-adjacent rendering stays with
      GS-D's outcome line.
   e. Empty-address records: counted in `records_without_identity`;
      the 目的地 cell renders the explicit missing marker.
6. **Files (verified)**: `app/adapters/risk_platform.py` (helper),
      `app/domain_tools/finding_drilldown.py` (attach block),
      `app/agent_loop.py` (projection :266-280; composer :2423-2446).
   **New**: `tests/test_cg_gs_b_destination_identity_v1.py`.
7. **Tests**: block construction over 9-distinct / 9-identical /
   mixed / missing-address / malformed-address / duplicate-record
   fixtures; `0x` case-insensitivity; per-group record-ID provenance;
   projection cap behavior; composer column rendering.
8. **DoD**: L1 green; the block verified end-to-end in one manual
   canonical run (observation JSON contains it); no other view
   affected.
9. **Exit**: block available to GS-D.
10. **Unlocks**: GS-D (evaluation consumes the projected block).

### 4.3 WP-GS-C — Facet vocabulary, admission, request-coverage assessment

1. **Objective**: comparison intent is represented when proposed,
   validated when admitted, and disclosed when detected-but-uncovered.
2. **Scope**: `PostconditionFacet` enum; `RequiredOutput.
   postcondition_facets`; proposal schema + `build_goal_contract`
   validation; the deterministic request-coverage detector; the goal-
   checkpoint coverage marking; composer disclosure line; prompt
   guidance (flag-gated block).
3. **Out of scope**: facet evaluation semantics (GS-D); any new
   evidence type; extraction completeness beyond the frozen detector
   vocabulary (documented residual, §15).
4. **Dependencies**: none hard (co-locates with GS-D for review).
5. **Tasks**:
   a. `PostconditionFacet` enum (single member
      `DESTINATION_ADDRESS_IDENTITY = "destination_address_identity"`)
      in `app/goal_resolution.py` beside `ClaimKind`; governance =
      EvidenceType rule (enum + executable backing + tests).
   b. `RequiredOutput.postcondition_facets: list[PostconditionFacet] =
      []` (validated); proposal items MAY carry
      `"postcondition_facets": [...]`; `build_goal_contract` rejects
      unknown members with `invalid_postcondition_facet` (whole-
      proposal, all-or-nothing unchanged).
   c. Prompt guidance (flag-gated GSP block): comparison-shaped
      requests → propose the facet on the comparison deliverable;
      single-request wording unchanged (flag OFF byte-identity).
   d. `assess_comparison_intent(request_text) -> ComparisonIntent`
      matcher in `app/investigation_concepts.py` (frozen comparison
      connectives + destination-identity object concepts; bounded,
      deterministic, unit-tested; NOT the audited sentence hard-coded).
   e. Goal-checkpoint coverage marking: flag ON + intent detected +
      no faceted output in the accepted contract →
      `goal_contract_decision.coverage =
      "comparison_intent_uncovered"` (additive field on the decision
      dict; CG-3 schema unchanged); contract present with the facet →
      `coverage = "covered_by_facet"`.
   f. Composer disclosure: coverage-incomplete runs append the
      uncovered-intent line (「系统未能将您的比较需求建立为结构化交付目
      标，以下结果不构成"同一/不同"的结论」) via the GS-A guard
      branch.
6. **Files (verified)**: `app/goal_resolution.py` (enum :122 region,
   RequiredOutput :137-166, build_goal_contract :285+, prompt :487+,
   understand_goal), `app/investigation_concepts.py` (matcher),
   `app/agent_loop.py` (checkpoint marking ~:1195-1230 + closure
   disclosure from GS-A).
   **New**: `tests/test_cg_gs_c_facet_admission_v1.py`.
7. **Tests**: detector positives (the audited sentence + paraphrase
   fixtures), near-negatives (pure retrieval phrasings), ambiguity;
   admission matrix (valid facet / unknown facet / facet on
   non-comparison output / missing facet with detected intent);
   immutability across a scripted RE_PLAN; flag OFF → no facet schema
   path; whole-proposal rejection on invalid member.
8. **DoD**: L1 green; labeled-detector coverage report (positives
   detected X/Y, near-negatives not detected Z/Z) committed as a
   fixture table; bridge smoke (existing CG suites) green.
9. **Exit**: GS-D can consume the facet; coverage disclosure renders.
10. **Unlocks**: GS-D; GS-E (combined validation).

### 4.4 WP-GS-D — GSP-4 facet evaluation

1. **Objective**: the comparison output's satisfaction is decided by
   the comparison facts, never by retrieval.
2. **Scope**: `evaluate_output`/`_category_postcondition` facet branch;
   reason codes; `detail` conclusion text; composer facet conclusion
   line (GSP-5 render side of the faceted output).
3. **Out of scope**: GoalOutcome derivation (unchanged); coverage
   (unchanged); non-faceted outputs (unchanged retrieval semantics).
4. **Dependencies**: GS-C (facet field) + GS-B (projected block).
5. **Tasks**:
   a. Facet branch per design §7.2 (five-row verdict table: complete
      negative / complete positive / partial / empty fall-through /
      facts-absent UNRESOLVED), with new reason codes
      (`destination_comparison_complete`,
      `destination_identity_partial`, `comparison_facts_absent`).
   b. A faceted output WITHOUT the block is never satisfied by
      retrieval: the retrieval verdict is suppressed for that output
      when the facet is declared (the output falls to UNRESOLVED
      unless the block yields a verdict) — the exact anti-false-
      completion rule.
   c. Composer: the faceted output renders the comparison conclusion
      (group provenance; destination column from GS-B) through the
      existing per-output section branches.
6. **Files (verified)**: `app/goal_postconditions.py` (new branch +
   reason codes), `app/agent_loop.py` (per-output render section).
   **New**: `tests/test_cg_gs_d_facet_evaluation_v1.py`.
7. **Tests**: §7.2 verdict table row by row (complete positive k≥2 /
   complete negative k=1 / partial M<N / facts-absent UNRESOLVED /
   empty fall-through); suppression of the retrieval verdict for
   faceted outputs; non-faceted outputs unchanged (regression);
   GoalOutcome derivation over faceted mixes (partial/unanswerable).
8. **DoD**: L1 green; no new satisfaction enum; existing consumers
   verified (§7.4 mapping table re-asserted as tests).
9. **Exit**: GS-E final validation.
10. **Unlocks**: GS-E.

**Execution record (2026-10-10 — IMPLEMENTED + VALIDATED).** Scope
delivered as specified: the §7.2 five-row verdict table (rows 4/5
implemented as fall-through with the retrieval-suppression rule; row
4's "empty/inadequate" mapped onto the real tool contract as "block
absent + outcome empty" — GS-B returns no block for zero-withdrawal
payloads), the three reason codes, and the composer facet section
(conclusion + per-group record-ID provenance + the 目的地 evidence
table; comparison-facts-absent renders records as evidence only).
Files: `app/goal_postconditions.py`, `app/agent_loop.py`
(`_destination_identity_summary` now projects group `address` values —
design §8 equality-verifiability), plus the two new test files and
`qa/journey_cgsd_l3.py`. Results: L1 25/25; API E2E 6/6 (full facts
LIVE, partial 3/9, all-missing 9/9, authoritative zero, RE_PLAN
repeat convergence, retrieval-only intact); browser J1/J2 PASS
(partial-identity browser NOT RUN — no live data, API-layer coverage).
No new satisfaction enum; `derive_goal_outcome` untouched; GSP-6/GSP-7
behavior re-verified (CG-4 11/11, CG-5 6/6, GSP suites green in their
own configs). QA record:
`docs/qa/2026-10-10-wp-gs-d-facet-evaluation-qa.md`. GS-E may start.

### 4.5 WP-GS-E — API E2E + Browser E2E + regression validation

1. **Objective**: canonical-stack validation of the 21 acceptance
   scenarios (§10.1) across API and browser surfaces, plus
   full regression.
2. **Scope**: new API E2E file + browser journey file; NO production
   changes.
3. **Out of scope**: real-model paraphrase coverage expansion beyond
   the design's measurement plan (that evidence feeds OQ-6, not a
   pass/fail gate for this WP).
4. **Dependencies**: GS-A..GS-D merged.
5. **Tasks**:
   a. `tests/test_cg_gs_e2e_v1.py` — canonical-stack API E2E
      (real HTTP against the freshly restarted backend; model boundary
      via the approved `goal_llm` seam; RP live). Scenarios 1–21 from
      §10.1, each asserting: contract/facet shape, per-output
      satisfactions, GoalOutcome, closure wording (guard/gsp outcome),
      Activity consistency.
   b. `qa/journey_cg_gs_l3.py` — real-browser journeys for the
      user-visible scenarios (§10.1 items with response-text asserts),
      using the existing harness (`qa/browser.py`,
      `qa/journeys_common.py`) with per-scenario session swap.
   c. Regression: the non-CG battery (299 tests) + CG-1..CG-4 suites +
      the legacy-baseline DI-seam suite.
   d. Runtime freshness preflight + post-run restoration to defaults
      (flags OFF, no seam) — recorded.
6. **Files (verified)**: harness files exist (`qa/browser.py`,
   `qa/journeys_common.py`; the CG-4/CG-5 session-swap pattern).
   **New**: the two test/journey files above; results JSON under
   `docs/evaluations/`.
7. **Tests**: see (a)-(c).
8. **DoD**: all scenarios PASS in their required modes; skips only for
   documented environment preconditions; QA record written.
9. **Exit**: WP-GS validation complete → manual acceptance.
10. **Unlocks**: final manual acceptance (PMO).


**Execution record (2026-10-11 — COMPLETE).** The §10.1 matrix was
executed 21/21 on the current worktree with per-scenario evidence
(`docs/qa/2026-10-11-wp-gs-e-aggregate-validation-qa.md` §2): gap
scenarios added as `tests/test_cg_gs_e_matrix_v1.py` (S1 exact
9-distinct, S2 identical, S3 mixed, S5 unclassified shapes, S11
invalid-facet rejection, S20 parity); GS-C browser journeys re-run on
the current tree (S8/S9/S10); all other rows cite this round's GS-D
batches or same-tree artifacts. Open exploratory program: 9 charters
(`tests/test_cg_gs_e_exploratory_v1.py` + real-model/browser
artifacts), findings register F-01..F-08 — two in-scope defects FIXED
and regression-verified (F-01 harness flag-leak: cg3_safety / GS-A /
GS-C harnesses now set AND restore the flag; F-08 duplicate projection
key), six documented for PMO decisions/awareness (F-02 is_new_address
boundary, F-03 case-level comparison gap, F-04 conflict-norm
conservatism, F-05 turn-path boundary, F-06 gateway llm_empty
variance, F-07 detector vocabulary gaps REPORTED with real-model
mitigation evidence). Scenario 19's negative probe re-confirmed:
LEGACY closure is unreachable from canonical paths without test
injection. Runtime restored (flags OFF, no seam, health 200).
GS-E validated; the documented product decisions (F-02/F-03/F-05,
F-07 governance) are ready for PMO review.

## 5. Exact In-scope and Out-of-scope Boundaries

**In scope**: the five WPs above; the `destination_identity` fact
block; the `PostconditionFacet` enum + `postcondition_facets` field;
the comparison-intent detector; the completion-claim guard + DI seam;
observation/composer projection updates for the above; test/journey
files; QA records.

**Out of scope**: WP-FR (focus/NL-target consistency), WP-ES
(signal_explain authority, RP rule-config APIs), WP-CT (compound
composition ordering; general replan_failure terminal remediation —
except that GS-A's guard and GS-D's verdicts must not regress the
shared closure site); MCP/stores/tools/evidence-type values; ALL
semantics/requirement groups; UI redesign beyond the destination
column and disclosure lines; flag default changes; turn-path contract
support (documented deferral stands).

## 6. Per-WP Files/Modules with Confidence

See §4 per-WP "Files" rows. Verified-existing files are cited with
their anchors (§3). Uncertain items (labeled): the exact insertion
line inside `finding_drilldown`'s payload assembly (V-7, ±20 lines);
whether the destination helper lands in `risk_platform.py` or a new
`app/evidence_facts.py` (implementer's choice, single location). All
other paths are confirmed.

## 7. Data/Schema and Contract Compatibility

- `AgentLoopState`: additive fields only (none new beyond GS-C's
  decision-dict key — whole-model JSON, no migration).
- `RequiredOutput.postcondition_facets`: additive optional field;
  legacy documents load with it absent; proposals without it are
  unchanged.
- Proposal schema: additive optional `postcondition_facets` per output;
  unknown members → whole rejection (existing all-or-nothing mechanism,
  new code).
- Activity projection: additive keys (`destination_identity` summary,
  facet verdict) — frontend consumes none today.
- GoalContract semantics: immutable content includes the facets;
  RE_PLAN cannot add/remove them (GSP-7 untouched).
- Persisted legacy runs: load unchanged; facet-less outputs behave
  exactly as today under both flag states.

## 8. Detailed Implementation Approach

**Request coverage (GS-C)**: the detector is a pure function over the
request text with a frozen two-part vocabulary — comparison
connectives (同一…还是…不同 / 是否相同 / 分别 / same-or-different) AND
the destination-identity object concept (目的地地址 / destination
address) — both required to fire (AND semantics prevent "不同账户" from
falsely firing on non-comparison requests). It runs at the goal
checkpoint AFTER `build_goal_contract`, over the ACCEPTED contract:
`covered = any(f.DESTINATION_ADDRESS_IDENTITY in o.postcondition_facets
for o in contract.outputs)`. Disposition per design §7.3. The detector
never consults the model and never fires on uncertainty.

**Address identity (GS-B)**: identity = the record's `address` string;
normalization = trim, plus case-insensitive comparison ONLY for `0x`-
prefixed values (the one rule verifiable from the value's own
convention); all other values compare case-sensitively; unknown shapes
are annotated `exact_unclassified` and never dropped. `asset` is
recorded per group as composition (NOT a chain identity — audit
verified no chain field exists). Groups carry authoritative record IDs
(provenance). Equality is over RECORDED identifiers; the design and
wording explicitly do not claim on-chain destination identity.

**Facet evaluation (GS-D)**: in `_category_postcondition`, a facet
gate precedes the WITHDRAWAL_RECORDS/TRANSACTION_RECORDS retrieval
branch: when the output declares `destination_address_identity` and
the combined summary carries the `destination_identity` block, the
verdict comes from §7.2's table; when the facet is declared and the
block is absent, the output evaluates UNRESOLVED
(`comparison_facts_absent`) — the retrieval branch is skipped for that
output. Non-faceted outputs keep the existing branch untouched.

**Completion safety (GS-A)**: the universal guard lives in the
`compose_loop_response` contract-absent branch (single write site),
keyed on the policy hook; the coverage-incomplete disclosure rides the
same branch keyed on the decision record. Loop terminal semantics,
`criteria_satisfied`, GSP-4/5/6/7 untouched.

## 9. Per-WP Definition of Done

Per WP: all listed tests green; canonical-stack validation for
cross-module WPs (GS-E is the consolidated pass, but each WP lands
with a bridge smoke over the existing CG suites); QA record section
written separating unit/API/browser evidence; no new failures in the
299-test non-CG battery or the CG-4-mode suites; flag-OFF identity
proven where the WP touches OFF-mode behavior; runtime restored to
defaults after any flag-ON validation round.

## 10. Layered Test and Acceptance Plan

| Layer | Coverage | Files (planned) |
|---|---|---|
| L1 unit/module | GS-A guard table + seam; GS-B block matrix; GS-C admission/detector tables; GS-D verdict table | `tests/test_cg_gs_{a,b,c,d}_v1.py` |
| API E2E (canonical stack, seam-deterministic) | the 21 scenarios of §10.1 | `tests/test_cg_gs_e2e_v1.py` |
| Browser E2E (real Chrome, canonical) | user-visible journeys for scenarios with response wording (1-8, 13-17, 20) | `qa/journey_cg_gs_l3.py` |
| Real-model exploratory | comparison-coverage measurement over the paraphrase set (OQ-6) | extends `qa/journey_cg5_realmodel_l3.py` pattern |
| Regression | 299-test non-CG battery + CG-4-mode suites + legacy-baseline DI-seam suite | existing files |

Evidence rules: runtime freshness preflight per E2E round (current
worktree restart, flags per scenario, post-run restoration recorded);
deterministic seam and real-model evidence reported separately;
environment blocks marked, never counted as passes.

### 10.1 Required user-journey scenarios (acceptance matrix)

All scenarios run on the canonical topology (:3001→:8001→:8000) with
runtime-freshness preflight. Evidence source per scenario: preserved
store documents (read-only) + activity projection + response text.
"Satisfaction" always means per-RequiredOutput satisfaction from GSP-4.

| # | Scenario | Setup / evidence | Action | Expected semantics | GoalOutcome / stop | Rendering | Regression |
|---|---|---|---|---|---|---|---|
| 1 | 9 withdrawals, 9 distinct recorded destinations | destination_identity block: 9/9 identity, unique=9 | comparison-shaped request (seam goal_llm, faceted output) | O=facet SATISFIED positive; 「9 笔提现记录指向 9 个不同目的地标识」 | COMPLETE (contract-relative) | conclusion + per-group record IDs + 目的地 column | facet path new |
| 2 | 9 withdrawals, 1 shared destination | block: 9/9, unique=1 | same | O=facet SATISFIED negative; 「全部 9 笔提现记录指向同一目的地标识」 | COMPLETE | conclusion + group provenance | facet path new |
| 3 | mixed repeats | block: 9/9, unique=k (1<k<9) | same | O=facet SATISFIED positive; 「9 笔记录共指向 K 个不同目的地标识」+ groups | COMPLETE | groups + 目的地 column | facet path new |
| 4 | some records missing destination identity | block: M/9 identity, unique over comparable subset | same | O=facet PARTIALLY_SUPPORTED; 「M/9 笔记录缺少目的地标识，九笔整体的比较尚未建立」+ comparable-subset groups | PARTIAL | partial boundary + subset groups | facet path new |
| 5 | ambiguous/unclassified address values | block annotated shape=ambiguous / exact_unclassified | same | comparison over recorded identifiers; normalization annotated; no on-chain claim | per §7.2 | normalization annotation in the block/facts | new |
| 6 | identity grouping provenance | any of 1-4 | inspect groups | every group lists its authoritative withdrawal record IDs (W…) | — | group provenance rendered with the conclusion | new |
| 7 | retrieval ok, comparison facts absent | faceted output + plan WITHOUT an evidence-withdrawals step | same | O=UNRESOLVED (`comparison_facts_absent`); retrieval results shown as evidence only | UNANSWERABLE (or PARTIAL with other outputs) | 「未获得完成目的地比较所需的地址事实」 | facet path new |
| 8 | LLM proposal omits the facet on a comparison-shaped request | proposal = plain retrieval output; detector fires | same | contract accepted; coverage=comparison_intent_uncovered; O1 retrieval SATISFIED; **original-request completion NOT claimed** (see 10.2) | COMPLETE (contract-relative) + mandatory coverage qualification | partial/retrieval answer + coverage disclosure | coverage-incomplete mapping (10.2) |
| 9 | detected intent uncovered must not become a retrieval-only complete goal | same as 8, asserted negatively | same | unqualified whole-request completion claim ("本次调查目标已完整回答" WITHOUT the coverage qualification) is a test FAILURE | as 8 | outcome line + coverage disclosure paired | new negative |
| 10 | genuine retrieval-only request stays supported | no comparison signal; retrieval contract | same | normal retrieval contract + completion (no coverage mark, no disclosure) | COMPLETE | unchanged legacy-contract rendering | single-output regression |
| 11 | unsupported/invalid facet rejected | proposal with unknown/invalid facet member | same | whole-proposal rejection `invalid_postcondition_facet` → CG-3 guarded path (disclosure, no contract) | guarded | disclosure rendered | CG-2 mechanism reused |
| 12 | RE_PLAN no-new-information, comparison unresolved | faceted contract; plan 2 returns identical evidence | RE_PLAN → convergence STOP | loop stop = convergence; facet output stays pending/unresolved (no upgrade); overall partial/unanswerable | PARTIAL/UNANSWERABLE | not-determined boundary per output | J3-shape extension |
| 13 | flag ON, valid contract | any of 1-9 | — | full facet semantics | per outputs | canonical | regression |
| 14 | flag ON, contract missing (contract-less run) | seam/LLM_EMPTY → no contract | — | EXECUTION-basis closure (universal guard); no goal claim | None | guarded closure | INV-C |
| 15 | flag OFF canonical user-facing safety | flags OFF, same request | — | EXECUTION-basis closure (universal guard, flag-independent); legacy execution preserved | None | guarded closure (not byte-legacy) | INV-C |
| 16 | isolated legacy-baseline path | regression harness only (DI seam injected) | — | byte-exact legacy closure; UNREACHABLE from canonical HTTP/UI paths (negative probe) | legacy | regression-only | isolation test |
| 17 | every completion-bearing surface consistent | any of 8/9/14/15 | cross-surface check | response closure, Activity semantic fields, task-card coarse status mutually consistent; coarse status never presented as semantic completion | as scenario | projection + task card | cross-surface |
| 18 | single-output regression | existing single-goal suites | — | unchanged behavior | unchanged | unchanged | existing suites green |
| 19 | compound / GSP-6 / GSP-7 regression | existing compound + RE_PLAN suites | — | unchanged coverage/preservation behavior | unchanged | unchanged | existing suites green |
| 20 | partial/unresolved rendering parity | scenarios 4, 7, 8, 12 | check API response, Activity, Composer, browser UI render the SAME semantic state | per-output boundary + overall outcome consistent across all four surfaces | as scenario | parity assertions | cross-surface |
| 21 | detector positives / near-negatives / paraphrases | labeled request set (Session-01 + RM + new paraphrases + near-negative retrieval phrasings) | detector-level checks | positives detected; near-negatives NOT detected; paraphrase coverage REPORTED (not asserted complete) | — | coverage report artifact | detector governance (OQ-4/OQ-6) |

### 10.2 Coverage-incomplete semantic mapping (the seven PMO dimensions)

For the required scenario — comparison intent detected
(`comparison_intent_uncovered`) with a retrieval-only contract whose
retrieval output is fully satisfied:

1. **Per-output satisfaction**: the represented retrieval output is
   SATISFIED by the existing GSP-4 retrieval verdict
   (`records_retrieved_complete`) — stated explicitly as acceptable.
   No output exists for the comparison intent; none is fabricated.
2. **Contract-relative GoalOutcome**: **COMPLETE — deliberately NOT
   overridden.** GoalOutcome is CONTRACT-RELATIVE by existing
   semantics (`derive_goal_outcome` over the accepted outputs): it
   means "the accepted contract is fulfilled", which is true. The
   uncovered comparison intent is carried by the decision record
   (`coverage=comparison_intent_uncovered`), not by falsifying the
   contract-relative outcome.
3. **Original-request completion claim**: NOT asserted. Because the
   coverage mark exists, the composer MUST pair the contract-relative
   outcome with the mandatory coverage disclosure (GS-A/GS-C wiring):
   「已接受的调查目标已完整回答；但系统未能将您的比较需求建立为结构化
   交付目标——原始请求中的比较问题未得到回答，以下结果不构成
   "同一/不同"的结论。」 An UNQUALIFIED whole-request completion claim
   is prohibited (scenario 9 negative assertion).
4. **Agent-loop closure wording**: the GSP-5 outcome line (contract-
   relative) + the coverage disclosure line, deterministically paired.
   The bare "The investigation completed." is replaced by the
   basis-scoped form per INV-C whenever the coverage mark is present.
5. **Activity projection**: serves `goal_outcome=complete` (contract-
   relative), `goal_contract_decision.coverage=
   comparison_intent_uncovered`, per-output satisfactions, and the
   coarse execution rows — each independently true; no row may carry
   an unqualified whole-request claim.
6. **Task-card / terminal status**: tasks_v2 coarse status and
   agent_status are EXECUTION-terminal display ("the loop finished"),
   defined as never constituting semantic completion; the canonical
   semantic interpretation surface is the served goal_outcome +
   decision fields (already additive; frontend consumption remains
   future scope per the design).
7. **Relationship**: execution completion (loop), output satisfaction
   (GSP-4), and request coverage (decision record) are three
   independent structured facts; the user-visible state is the
   PAIRED rendering (outcome + coverage qualification). The
   implementation must assert the pairing, not either fact alone.

**WP responsibility for the mapping**: GS-C owns the decision-record
coverage marking; GS-A owns the composer closure/outcome scoping and
disclosure rendering; GS-D owns per-output satisfaction (existing
GSP-4, unchanged); GS-E owns the cross-surface consistency assertions
(scenarios 8, 9, 17, 20).

## 11. Feature-Flag ON/OFF Coverage and Canonical-Runtime Safety

- Flag ON: full facet path (§7.2) + universal guard for contract-less
  runs + coverage disclosure for coverage-incomplete runs.
- Flag OFF: byte-identical legacy EXECUTION and per-step semantics;
  the ONLY user-visible delta is the guarded closure wording replacing
  the bare completion phrase (INV-C is flag-independent by PMO
  mandate) — covered by the GS-A guard table's OFF rows and the
  isolated baseline suite via the DI seam.
- Canonical-runtime safety validation: a dedicated negative probe
  (scenario 19) proves LEGACY wording is unreachable without test
  injection.

## 12. Test-Only Legacy-Baseline Isolation

- Mechanism: module-level policy hook `_completion_closure_policy()`
  with a test-only override attribute; no env var, no flag, no runtime
  configuration.
- Reachability: LEGACY is returned only when the override is set by
  test code (`monkeypatch`); production import paths never set it.
- Baseline suite: the historical wording assertions run under the
  override and are thereby isolated; a negative test asserts the
  default (GUARD) in a fresh interpreter without injection.

## 13. Rollout, Manual Acceptance, Rollback, Runtime Restoration

- Rollout order: GS-A (safety) → GS-B ∥ GS-C → GS-D → GS-E →
  canonical flag-ON validation round → manual acceptance.
- Manual acceptance runs with flags ON per the design's §9.6
  recommendation; runbooks updated to list
  `AGENT_COMPLETION_BASELINE` as forbidden in user-facing runtimes.
- Rollback: each WP is flag-scoped or additive; the global rollback is
  `ENABLE_GOAL_SEMANTIC_CONTRACT=false` (+ the DI seam is inert
  without test injection). No data cleanup required (additive JSON
  fields only).
- Runtime restoration after every validation round: flags OFF, no
  seam env, health verified, recorded.

## 14. Product Decisions, Dependencies, Unresolved Gaps

| # | Decision/Gap | Blocks | Conservative behavior meanwhile |
|---|---|---|---|
| OQ-1 | Full address vs masked exposure in the evidence table/conclusion | presentation only | design default: full value in the table, `dest-N` alias in prose |
| OQ-2 | Address normalization across chains/case (RP provides no chain id) | the comparison's precision only | exact-trim; 0x case-insensitive; unknown shapes case-sensitive + annotated |
| OQ-3 | (superseded) legacy closure disclosure | — | resolved by INV-C |
| OQ-4 | Facet/detector vocabulary governance path | process only | single-member enum ships; additions follow the EvidenceType rule |
| OQ-6 | Real-model comparison-coverage measurement | not blocking; informs detector expansion | WP-CG-5-style real-model round after GS-E |

Evidence gaps carried from the audit: RP `address` population is not
contractually guaranteed (the block counts gaps); no chain identifier
exists (comparison is over recorded identifiers only); the resolver
assist's rationale is unlogged (WP-FR scope).

## 15. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Guard changes OFF-mode wording → baseline test churn | isolated DI seam; churn bounded to closure-text assertions |
| Detector false positives (retrieval request misread as comparison) | AND-semantic two-part matcher; near-negative test set; false positives disclose rather than fabricate |
| Detector false negatives (comparison missed) | layered defense with the model facet proposal; measured coverage (OQ-6); documented residual |
| Facet misuse by the model (wrong output facetted) | deterministic validation; wrong-facet outputs degrade to honest verdicts, never fake completion |
| Observation payload growth (destination block) | bounded projection (counts + capped groups) consistent with existing evidence-rows caps |
| Regression in 299-test battery / CG suites | per-WP bridge smokes; like-for-like reporting; baselines never re-attributed |

## 16. Final Authorization Gate

**No source-code changes may begin until the PMO explicitly approves
this Implementation Plan.** Upon approval, implementation proceeds WP
by WP with per-WP review checkpoints; any deviation from the approved
design discovered during implementation is recorded and escalated —
never silently resolved.

---

*End of implementation plan. Not approved for execution; no code,
tests, flags, runtime state, or other files were changed in producing
it.*
