# Goal Satisfaction & Claim-Specific Postconditions — Design Proposal v1

**Status: Draft for PMO Review — Design Only, Not Approved for Implementation**

**Date**: 2026-10-09
**Revision 3**: 2026-10-09 — second PMO review incorporated, three
focused changes. (1) BLOCKING ISSUE A resolved: a deterministic
request-coverage assessment (§7.3) closes the omitted-facet false-
completion path (former OQ-5) — a detected comparison intent that the
accepted contract does not cover marks the run coverage-incomplete and
the completion claim is basis-scoped; it can never be satisfied by
record retrieval alone. (2) BLOCKING ISSUE B resolved: the universal
completion-safety guard is defined across ALL completion-bearing
user-visible surfaces (loop closure, turn responses, Activity,
task-card status), with the execution-terminal vs semantic-completion
distinction made explicit (§9); legacy-baseline isolation moved from
an environment variable to a TEST-ONLY dependency-injection seam
(§9.3) — no unsafe completion wording is reachable from the canonical
service. (3) Address-identity semantics bounded by verified payload
facts (§8): `asset` is an asset identifier, not a chain identifier;
comparison is over recorded identifiers by exact trimmed-string
equality; the design claims record-identifier equality only — not
on-chain destination proof — and states so in user-facing wording.
(4) PARTIALLY_SUPPORTED mapping through existing GSP consumers
verified and documented (§7.4). Revision 2's valid decisions are
retained (§5 of the PMO review letter).
**Revision 2**: 2026-10-09 — PMO review incorporated. (1) The flag-OFF
completion-safety boundary is RESOLVED by a universal, flag-independent
completion-claim guard (§9): a run without an accepted contract can
state its execution/evidence basis but can never assert that the
user's request was answered; exact legacy closure wording is isolated
behind a regression-only escape hatch and is no longer the canonical
user-facing behavior in any flag mode. (2) The comparison facet is
specified as a FACET-LEVEL EVIDENCE ASSESSMENT mapped onto the EXISTING
satisfaction statuses — no new enum value; missing destination
identities yield PARTIALLY_SUPPORTED with partial facts and never a
complete-comparison claim (§7). (3) Address identity and normalization
are specified from verified field semantics: scheme detection from the
value itself, asset composition recorded per group, unknown formats
compared case-sensitively and annotated (§8). (4) Facet admission is
explicit: frozen vocabulary, proposal validation, anti-fallback on
rejection, immutability across RE_PLAN, GSP-6 separation (§6/§7).
Sections marked [R2] contain revised content. No direction change: the
facet + observation-projection architecture, audit facts, scope
boundaries and alternatives remain as approved for review.

**Audit basis**: `docs/qa/2026-10-09-manual-acceptance-read-only-audit.md`
(read-only evidence audit of the 2026-10-09 manual-acceptance runs on
case U00020). Implementation has NOT begun; no source, test, flag,
runtime, or data changes were made in producing this document.

---

## 1. Status and Approval Boundary

This is a design proposal for **WP-GS — Goal Satisfaction and
Claim-Specific Postconditions**. It proposes the minimal, coherent
change that prevents the agent from declaring a user request satisfied
merely because relevant records were successfully retrieved. PMO
approval of this document does not authorize implementation; an
implementation plan and execution require a separate approval.

Frozen by prior approvals and untouched here: GSP-1…GSP-7 semantics,
CG-1/2/3/4/5 accepted behaviors, the COMPOUND/AMBIGUOUS distinction,
CG-2 all-or-nothing acceptance and canonical dedup, GSP-6 ANY-set
coverage, GSP-7 contract preservation, O2 ANY-contract boundary, the
`ENABLE_GOAL_SEMANTIC_CONTRACT` default value.

## 2. Problem Statement and Audit Evidence

The audited request (TASK-cfc7e59066f1, case U00020, flag OFF):

> 「9笔提现都提向同一个地址，还是每笔都提向一个新地址？」

Confirmed audit facts (re-verified against the current worktree):

- AF-1 The RP withdrawal payload carries a per-record destination
  `address` (plus `is_new_address`); the adapter preserves both in the
  evidence records (`adapters/risk_platform.py:840-841`, and the same
  fields survive in `normalize_evidence_records`, used by
  `finding_drilldown`'s evidence view).
- AF-2 The observation projection for `view="evidence"` builds
  per-record rows of `{record_id, timestamp, summary}` ONLY
  (`agent_loop.py:266-280` region) — the address is dropped here.
- AF-3 The composer's evidence table renders
  `| # | 记录 ID | 时间 | 明细 |` with the cell = the record `summary`
  string (`agent_loop.py:2438-2446`) — the address value never reaches
  the user; the summary wording ("…to a newly encountered address")
  cannot distinguish "same address" from "nine different addresses".
- AF-4 No grouping / unique-count / comparison logic exists anywhere in
  the audited path.
- AF-5 The GSP-4 withdrawal postcondition is pure retrieval:
  `record_count` present and `complete != False` → SATISFIED
  (`goal_postconditions.py`, WITHDRAWAL_RECORDS branch,
  `records_retrieved_complete`) — record retrieval alone satisfies the
  output.
- AF-6 The audited run ended `completed / goal_satisfied` via the
  LEGACY path: `goal_contract_decision=None`, `goal_contract=None`
  (flag OFF), `criteria_kind=question_answered`, legacy
  `criteria_satisfied()` = ANY-success (`agent_loop.py:585-606`),
  convergence STOP after an identical re-plan observation
  (decision trail preserved in the store, including the proposer's own
  RE_PLAN reason: 「…输出被截断且缺少具体目标地址…」).
- AF-7 Distinguishing fact: this is NOT evidence that the flag-ON
  GSP-4/GSP-5 path behaves identically — it was not active. Under the
  contract path the same run would still have satisfied a plain
  retrieval output (AF-5), but the per-output accounting, honest
  outcome derivation and disclosure machinery exist only there.

Root problem in one sentence: **the system's only completion test is
"relevant records were retrieved", while the user's deliverable was
"the destinations were compared" — no layer represents or checks the
second half.**

## 3. Goals and Non-goals

**Goals**

1. Preserve destination-address identity from RP through the tool
   result, observation, evaluator, composer and final response.
2. A deterministic, grounded destination-identity comparison
   (grouping + distinct count) produced where the data is, with full
   provenance (which record IDs share which destination).
3. Claim-specific evidence sufficiency: a RequiredOutput whose claim
   requires an address-identity comparison is SATISFIED only by the
   comparison facts, never by record retrieval alone.
4. Correct per-output and overall semantics when identity data is
   partially or wholly unavailable (never upgraded to a positive
   conclusion).
4b. [R3] A detected comparison intent that the accepted contract does
   not cover marks the run coverage-incomplete and is disclosed —
   never silently absorbed into a retrieval-satisfied goal (§7.3).
5. Flag ON / OFF behavior defined with no mode that silently restores
   "tool success ⇒ goal satisfied" for claim-bearing requests.
6. RE_PLAN convergence keeps loop-stop reasons, per-output
   satisfaction and GoalOutcome separate (no new mechanism; scoped
   clarification only).

**Non-goals**

- WP-FR (focus/finding resolution), WP-ES (signal_explain authority,
  RP rule-config APIs), WP-CT (compound composition ordering,
  general replan_failure terminal remediation).
- MCP, new databases/stores, new evidence-type values, ALL semantics
  or requirement groups, a general claim/rule DSL, composer redesign.
- Any natural-language question-answering beyond the frozen comparison
  facet introduced here.

## 4. Current-State Architecture and Evidence-Flow Map

Verified flow (all names current; line numbers as of this worktree):

```
RP withdrawal_evidence[i] (address, is_new_address, …)
  → RiskPlatformAdapter.fetch_case_evidence(expose_complete_records=True)
      finding_drilldown.py:214-224
  → normalize_evidence_records()                     adapters/risk_platform.py:788+
      records[i] = {record_kind, record_id, summary, amount, asset,
                    address ✓, is_new_address ✓, timestamp, risk_reason}
  → finding_drilldown(view="evidence") ToolResult.data.records
      (address ✓ PRESENT in the tool result)
  → ExecutorV2 → project_observation(view="evidence")   agent_loop.py:266-280
      summary = {record_count, record_ids, streams,
                 record_rows: [{record_id, timestamp, summary}]}   ✗ address LOST
  → state.observations
  → GSP-4 evaluate_output (goal_postconditions.py)
      WITHDRAWAL_RECORDS branch: record_count+complete → SATISFIED   ✗ retrieval-only
  → GSP-5 compose (per-output; evidence table agent_loop.py:2438-2446)
      rows = record_id/timestamp/summary                              ✗ address LOST
  → criteria_satisfied ANY-success (legacy) / per-output (contract)
  → AgentStatus.COMPLETED + StopReason.GOAL_SATISFIED (legacy)
```

Loss points (exact): **L1** `project_observation` evidence-branch
record_rows; **L2** the composer evidence table; **L3** the absence of
any comparison/aggregation consumer; **L4** (flag OFF only) the
completion test itself.

## 5. Root Causes — Confirmed Facts vs Design Assumptions

- **RC-1 (Confirmed)**: destination identity exists through the tool
  result and is dropped at projection (AF-1/AF-2).
- **RC-2 (Confirmed)**: the composer renders the summary prose only
  (AF-3).
- **RC-3 (Confirmed)**: GSP-4's withdrawal verdict is retrieval-only
  (AF-5); there is no representation for "the claim requires a
  comparison over the records".
- **RC-4 (Confirmed, scoped)**: the audited false completion ran on the
  legacy path (AF-6); under flag ON the per-output machinery exists but
  would produce the same retrieval-only SATISFIED for an un-faceted
  output — the facet proposed below is required in BOTH paths.
- **RC-5 (Confirmed design gap, added by revision 3)**: the audited
  question is comparison-shaped, but nothing requires the accepted
  contract to contain a comparison-faceted output. If the model
  proposes only a generic withdrawal-record retrieval output, the
  accepted contract is retrieval-only, GSP-4's retrieval verdict
  satisfies it (AF-5), and — under revision 2's guard — the run would
  complete with a basis-scoped closure while the user's actual
  comparison was never attempted or reported. Bounded-scope honesty is
  necessary but NOT sufficient: the coverage gap itself must be
  detected and disclosed. → §7.3 request-coverage assessment.
- **Design assumption (labeled)**: RP `address` values for one
  withdrawal stream are directly comparable after trimming (exact
  string equality), with 0x-prefixed values compared case-insensitively
  (EVM checksum mixing). Cross-chain normalization is NOT assumed
  (open question OQ-2).

## 6. Proposed Architecture and Responsibilities by Layer

Principle: **facts are computed once, deterministically, where the
records are; satisfaction is decided once, deterministically, where
satisfaction lives (GSP-4); presentation renders verdicts.**

| Layer | Responsibility (proposed) |
|---|---|
| Adapter (`normalize_evidence_records`) | unchanged — already preserves `address`/`is_new_address` |
| Tool (`finding_drilldown` view=evidence, withdrawals included) | NEW deterministic post-processing over its own records: a `destination_identity` fact block (counts + per-record identity presence + groups with record-ID provenance). Pure function of the records; no LLM; no new tool |
| Observation (`project_observation` view=evidence) | project the `destination_identity` block (bounded) alongside the existing summary; keep record_rows unchanged except adding `has_destination_identity` per row |
| GSP-4 (`goal_postconditions.py`) | NEW facet-aware branch: when the output declares the `destination_address_identity` facet AND the combined summary carries the block → verdict from the block (below). WITHOUT the block → the existing retrieval verdict applies for non-faceted outputs; a FACETED output without the block can NEVER be satisfied by retrieval alone |
| GSP-5 composer | for a faceted output, render the comparison conclusion + per-group record-ID provenance + the evidence table augmented with the destination column; for missing identities render the partial/unresolved boundary |
| Loop / terminal | NO change — stop reasons, convergence and REPLAN_FAILURE semantics untouched (§10) |
| Composer (universal guard) [R2] | flag-independent completion-claim guard at the existing legacy-closure branch (§9.2): contract-less closures state the execution basis, never goal completion — BOTH flag modes |
| Activity projection | additive fields only (facet, comparison verdict summary) |

### The `destination_identity` fact block (tool-layer contract)

Computed only over records of `record_kind="withdrawal"` that carry a
non-empty destination value after normalization (trim; for `0x`-prefixed
values case-insensitive comparison — see OQ-2):

```json
"destination_identity": {
  "basis": "withdrawal.address",
  "records_total": 9,
  "records_with_identity": 9,
  "records_without_identity": 0,
  "unique_destinations": 5,
  "groups": [
    {"destination_ref": "dest-1", "address": "0x…", "record_ids": ["W000164", "W000161"]}
  ],
  "normalization": "trim; 0x-case-insensitive"
}
```

- `destination_ref`: a stable short alias (dest-1…) assigned in first-
  occurrence order over the timestamp-sorted records — deterministic,
  presentation-friendly, and it keeps full addresses available in the
  same block for verification.
- The block is FACTS ONLY (counts, membership, provenance). The
  same-vs-different CONCLUSION is drawn by GSP-4, not by the tool.

### The claim facet (RequiredOutput extension, closed vocabulary)

- `PostconditionFacet` enum, initial member:
  `DESTINATION_ADDRESS_IDENTITY = "destination_address_identity"`.
- `RequiredOutput.postcondition_facets: list[PostconditionFacet] = []`
  (optional, validated, default empty — existing outputs unchanged).
- Proposal schema: an output MAY propose
  `"postcondition_facets": ["destination_address_identity"]`;
  `build_goal_contract` validates members against the frozen enum
  (unknown → whole-proposal rejection, `invalid_postcondition_facet` —
  all-or-nothing unchanged).
- Semantics: a facet SHARPENS the postcondition for that output; it
  does NOT change evidence_requirements, coverage (GSP-6 evaluates
  evidence requirements exactly as before) or any other output.
- Extensibility: new facets join only with (a) an enum value, (b) a
  deterministic tool-computed fact block, (c) a GSP-4 branch with
  payload tests — the same "closed vocabulary + executable backing"
  rule the project applies to EvidenceType. Not an open DSL.

## 7. Goal Satisfaction and Evidence Sufficiency Semantics

### 7.1 Facet assessment vs satisfaction statuses [R2]

The comparison facet performs a **facet-level evidence assessment**
inside GSP-4. It is expressed through the EXISTING
`OutputSatisfaction` values — **no new enum member is introduced**:

- The complete comparison is SATISFIED (positive or negative polarity)
  using the existing value.
- A partially comparable record set is PARTIALLY_SUPPORTED — partial
  destination facts ARE reported (groups over the comparable subset,
  M/N identity coverage), but the COMPLETE nine-record comparison is
  never claimed established.
- Missing identities / absent comparison facts are UNRESOLVED —
  never upgraded to a positive conclusion.

Consequences verified against the existing consumers (no changes
required in any of them):

- **GSP-5 `derive_goal_outcome`**: consumes the existing statuses
  unchanged — e.g. [satisfied, pending] → partial; [satisfied,
  partially_supported] → partial; [pending, pending] → unanswerable.
  No mapping edits.
- **Composition**: renders through the existing per-output branches
  (satisfied / partially_supported / pending boundaries) plus a NEW
  facet conclusion line carrying the comparison result and provenance.
- **Activity projection**: additive fields only (facet verdict summary);
  no consumer changes.

The facet assessment is therefore NOT a new satisfaction state and NOT
a parallel status machine — it is an additional deterministic
evaluation path that writes the same existing statuses for faceted
outputs.

### 7.2 Facet evaluation order (deterministic)

For an output with facet `destination_address_identity` over
withdrawal records, GSP-4 evaluates IN THIS ORDER:

| Condition | Verdict | Reason code |
|---|---|---|
| Block present, `records_with_identity == records_total > 0`, `unique_destinations == 1` | SATISFIED, negative polarity — 「全部 N 笔提现指向同一目的地（dest-1；记录 …）」 | `destination_comparison_complete` |
| Block present, all identities present, `unique_destinations >= 2` | SATISFIED, positive polarity — 「N 笔提现共指向 K 个不同目的地」+ group provenance | `destination_comparison_complete` |
| Block present, `records_without_identity > 0` | PARTIALLY_SUPPORTED — 「M/N 笔记录缺少目的地标识，仅能对可比子集给出分组结论；九笔整体的比较尚未建立」 | `destination_identity_partial` |
| Block present but empty/inadequate (no withdrawal records at all) | fall through to the existing records postcondition (empty-complete negative / evidence_missing UNRESOLVED) — a facet never fabricates a comparison over no records | inherited |
| Facet declared but block ABSENT (e.g. non-evidence tool ran) | **NOT satisfied by retrieval**: UNRESOLVED — 「未获得完成目的地比较所需的地址事实」 | `comparison_facts_absent` |

This is the exact separation demanded by the audit: retrieval
completeness (existing branch, still reported for non-faceted
outputs), identity availability (block counts), comparison completion
(facet verdict), and per-output satisfaction are four distinct,
structured facts. The conclusion (polarity + detail) is derived only
from the block; `claim_description`/prose are never consulted.

Persistence: verdicts persist via the existing mechanism
(`satisfaction`/`polarity`/`detail` on RequiredOutput; reason codes in
the evaluator result → loop application); the fact block persists in
`state.observations` (whole-model JSON — no migration); GoalOutcome
continues to derive from per-output satisfactions via
`derive_goal_outcome` (unchanged).

### 7.3 Request-coverage assessment — omitted-facet disposition [R3,
resolves former OQ-5]

The extraction-completeness boundary (design §1/§21 of the GSP plan)
means the model MAY propose a retrieval-only output for a
comparison-shaped request. Revision 2 accepted that residual; Revision
3 closes it with a deterministic REQUEST-COVERAGE ASSESSMENT at the
goal checkpoint — the same LLM-proposes / system-disposes split, with
no second semantic LLM call and no claim DSL:

**Coverage signal (deterministic, bounded, unit-testable).** A new
pure matcher in the goal layer
(`assess_comparison_intent(request_text) -> ComparisonIntent`), built
exactly like the project's existing bounded matchers
(`investigation_concepts`, `_HEURISTIC_RULES`): it fires only when the
request contains BOTH (a) a COMPARISON-ALTERNATIVE connective over one
object (同一…还是…不同 / 是否相同 / 分别比较 / same-or-different
patterns) and (b) an identity OBJECT concept (目的地地址 / destination
address / destination identity — the withdrawal-address class). The
matcher is NOT the audited sentence hard-coded; it is a frozen
concept vocabulary whose members are added under the same governance
as EvidenceType (OQ-4). Uncertain requests produce NO signal — the
detector never guesses.

**Coverage rule at the checkpoint (after contract acceptance, flag
ON):**

| Detected intent | Accepted contract | Disposition |
|---|---|---|
| none | any | normal path (retrieval requests stay retrieval) |
| comparison | ≥1 faceted output covers the detected object | full facet path (§7.2) |
| comparison | contract has NO comparison-faceted output | **coverage-incomplete**: contract still accepted (the covered deliverables are real work); decision record gains
`coverage="comparison_intent_uncovered"`; the composer outcome line is
BASIS-SCOPED (covered deliverables reported; the uncovered comparison
intent is disclosed as NOT established — 「系统未能将您的比较需求建立为
结构化交付目标，以下结果不构成"同一/不同"的结论」); loop completion is
never presented as the comparison being answered |
| comparison, but the proposed facet is invalid/unsupported | rejected → existing `invalid_proposal:*` / unsupported handling | contract-less guard path (CG-3) |

**Why accept-and-disclose rather than reject for case 3**: the covered
deliverables may still be genuine user work (e.g. a compound request
pairing the comparison with a retrieval); rejecting the whole contract
would discard real, grounded results the audit shows users value
(Audit D). The disclosure keeps the honesty invariant while preserving
useful evidence — the same trade-off the CG-3 guard made for
contract-less runs.

**Four-case disposition required by the PMO review**:

| Request class | Signal | Disposition |
|---|---|---|
| genuinely record retrieval | no comparison signal | normal retrieval contract; completion claim legitimate FOR THAT CLAIM |
| comparison required, facet omitted by the proposal | comparison signal | coverage-incomplete (above): partial results + explicit not-established disclosure; NEVER "已完整回答" |
| comparison intended but not confidently identifiable | no signal (detector is bounded) | normal path; residual boundary documented below (§15) |
| facet explicitly unsupported/invalid | proposal rejected | `invalid_proposal:*` → CG-3 guarded path; never a narrower silent contract |

**Residual boundary (exact, stated)**: paraphrases whose comparison
intent is outside the frozen detector vocabulary are NOT detected;
they degrade to the normal path and — if the model also omits the
facet — to a retrieval-satisfied contract with the STANDARD GSP-5
wording (which, lacking the coverage mark, renders
"已完整回答"). This residual does NOT block the original
false-completion fix: the audited request is inside the detector
vocabulary by construction, and the layered defense is (1) model
facet proposal (prompt guidance, WP-CG-5 evidenced 4/4 COMPOUND),
(2) the deterministic detector backstop, (3) real-model regression
(A–D) measuring actual coverage. Boundary measurement is a WP-CG-5/
later concern (OQ-6).

**Flag OFF**: no contract exists to assess; the universal guard
(§9.2) is the only protection and applies unchanged. The coverage
assessment is a flag-ON mechanism by construction (it evaluates the
accepted contract).

### 7.4 PARTIALLY_SUPPORTED mapping through existing consumers [R3,
verified against current code]

All consumers of `OutputSatisfaction.PARTIALLY_SUPPORTED` were
inspected in the current worktree; the facet's partial verdict reuses
the existing value and every consumer already handles it:

| Consumer | Location | Existing handling of PARTIALLY_SUPPORTED |
|---|---|---|
| Producer precedence | `goal_postconditions.py:265` (`_VERDICT_PRECEDENCE`) | rank 1 — between SATISFIED and UNRESOLVED; already produced by 4 category branches (:172/:189/:220/:230) |
| Overall GoalOutcome | `derive_goal_outcome` (`goal_postconditions.py:449`) | any PARTIALLY_SUPPORTED (without full SATISFIED) → GoalOutcome.PARTIAL; with SATISFIED → still PARTIAL |
| Loop composer, per-output section | `agent_loop.py:2756` | renders 「已获取部分证据，但覆盖范围不完整；不能据此得出完整结论（详见上方边界说明）」 — the canonical partial boundary (observed verbatim in the RM_C/RM_D and J4 canonical runs) |
| Loop composer, overall line | `_OUTCOME_ZH` / `derive_goal_outcome` | 「本次调查目标已部分回答（部分项目未能完整回答，见各项说明）」 |
| Activity projection | `project_task_activity` goal_contract outputs | satisfaction served verbatim per output — additive, frontend consumes none yet |

Per-case semantic mapping (the four PMO-required rows):

| Case | Output satisfaction | GoalOutcome | User-visible section |
|---|---|---|---|
| fully supported comparison (all identities, groups derived) | SATISFIED (positive k≥2 / negative k=1) | COMPLETE | comparison conclusion + group provenance + evidence table with destination column |
| partially available identities | PARTIALLY_SUPPORTED | PARTIAL | comparable-subset groups + 「M/N 笔缺少目的地标识…九笔整体的比较尚未建立」+ partial boundary line |
| comparison facts absent | UNRESOLVED (`comparison_facts_absent`) | UNANSWERABLE (or PARTIAL alongside other satisfied outputs) | 「未获得完成目的地比较所需的地址事实」+ retrieval results shown as evidence only |
| compound: one output satisfied, another partially/unresolved | per-output values | PARTIAL | satisfied output rendered; the other renders its own honest boundary; overall 已部分回答 |

No new satisfaction state is introduced; no existing consumer requires
modification. Verified, not assumed: each location above was read in
the current worktree.

## 8. Address Identity Preservation and Comparison Behavior

- **Authoritative identity field** (verified): RP
  `withdrawal_evidence[].address` is the ONLY destination-identity
  field the payload provides (`is_new_address` is a
  VERIFICATION-CONTEXT flag, never an identity — two withdrawals may
  both be new to the account yet share one destination, so it is never
  used for equality).
- **Comparison key** [R3-refined]: the recorded destination
  identifier, compared by EXACT string equality after trimming. RP
  provides `asset` (an ASSET label — BTC/ETH/DOGE…) and NO
  chain/network identifier; asset is NOT treated as a chain identity
  (a multi-asset account can withdraw on several networks). Groups
  record their asset composition for the analyst; cross-asset groups
  are annotated, never split or merged on an asset assumption.
- **Normalization is intentionally minimal** [R3-refined]: exact
  string equality after trimming is the ONLY universally valid
  equality for recorded identifiers. Revision 2's `0x` casefolding is
  WITHDRAWN: whether case matters cannot be established without the
  network/encoding context RP does not supply, and a wrong fold could
  merge genuinely different recorded identifiers (or split identical
  ones). Case-variant strings are therefore DIFFERENT recorded
  identifiers for comparison purposes; the comparison conclusion is
  worded over 记录中的目的地标识 (recorded identifiers), never over
  on-chain destinations — the system claims record-identifier
  equality only, explicitly NOT proof that two withdrawals targeted
  the same on-chain destination (that would require chain context RP
  does not provide).
- **Format recognition is descriptive, not decisive** [R3-refined]:
  the block MAY annotate a detected shape (0x / bech32-family /
  base58-charset) for the analyst, but the comparison itself never
  depends on the detected shape, and ambiguous shapes are annotated
  `shape=ambiguous`. `records_without_identity` counts empty/missing
  values; those records remain in the totals and degrade the verdict
  per §7.2.
- **Group membership → provenance** (unchanged): every group carries
  the authoritative withdrawal record IDs (W000164…), so each
  conclusion is traceable to the authoritative records.
- **Missing identities** (unchanged): counted and surfaced
  (`records_without_identity`); verdict degrades to
  PARTIALLY_SUPPORTED; no imputation, no inference from
  `is_new_address`.
- **Equality vs representation (separated)** [R2]: equality is decided
  on the normalized identity internally; user-facing representation is
  independent — the evidence table exposes the full destination value
  (a new 目的地 column; these are pseudonymous on-chain identifiers RP
  already reports, and exposing them keeps equality verifiable by the
  user), while the prose conclusion uses the stable `dest-N` alias.
  The alias↔value mapping lives in the fact block. OQ-1 (full value
  vs masked) remains open and presentational only — it cannot block
  the evidence semantics.
- **Group membership → provenance** (unchanged): every group carries
  the authoritative withdrawal record IDs (W000164…), so each
  conclusion is traceable to the authoritative records.
- **Missing identities** (unchanged): counted and surfaced
  (`records_without_identity`); verdict degrades to
  PARTIALLY_SUPPORTED; no imputation, no inference from
  `is_new_address`.

## 9. Flag Strategy, Universal Completion-Safety, and Legacy Isolation [R2]

### 9.1 Four distinct concerns (PMO revision — do not conflate)

| Concern | Scope | Flag dependence |
|---|---|---|
| U1 Structured semantic-contract functionality | COMPOUND/facet extraction, contract acceptance, per-output postconditions, per-output compose | `ENABLE_GOAL_SEMANTIC_CONTRACT=ON` only |
| U2 **Universal completion-safety invariants** | The user-facing completion claim must state its BASIS deterministically in EVERY user-facing runtime, both flag modes | flag-independent |
| U3 Isolated legacy-baseline regression behavior | Byte-exact pre-CG closure wording, available ONLY to the baseline regression suite via an explicit escape hatch | escape-hatch env (regression-only) |
| U4 Canonical user-facing / manual-acceptance runtime | Flags ON + universal guard active + session seam per scenario | runbook-specified |

### 9.2 The universal completion-safety invariant (U2) — ALL
completion-bearing surfaces [R3]

**INV-C (flag-independent)**: a user-visible surface may state that
the user's request/goal was ANSWERED only when a structured basis
exists. Two distinct structured bases and one non-basis are defined:

- SEMANTIC BASIS — an accepted GoalContract + per-output satisfactions
  → the GSP-5 outcome line (existing). Coverage-incomplete runs
  (§7.3) declare the uncovered intent explicitly and their outcome
  line is basis-scoped to the covered deliverables.
- EXECUTION BASIS — no contract: the surface states execution /
  evidence-retrieval completion and explicitly withholds any
  "request answered" judgment (guard wording, §9.2.1).
- NON-BASIS — AgentStatus / StopReason / task-card coarse status /
  tool-success counts are execution-terminal facts and NEVER
  constitute or support a semantic completion claim.

### 9.2.1 Surface-by-surface application (verified against the current
architecture)

| Surface | Completion-bearing content today | Guard application |
|---|---|---|
| Agent-loop response closure | `compose_loop_response` legacy branch emits "The investigation completed." for contract-less COMPLETED runs | guard branch (revision 2 site), now FLAG-INDEPENDENT: contract absent → basis-scoped closure in both flag modes; contract present → GSP-5 outcome line; coverage-incomplete (§7.3) → covered-deliverables + uncovered-intent disclosure |
| Turn responses | `compose_response` (`investigation_service.py:148`) renders strictly from ToolResult data (its own docstring: "never invent facts… no new domain reasoning"); it emits NO goal-completion claim — the wording is per-step/per-evidence | no guard needed for goal claims (verified: no such claim exists); the design constrains future turn wording to the same rule |
| Activity projection | serves additive semantic fields (goal_outcome, goal_contract_decision, semantic_basis) + coarse rows | the semantic interpretation surface; coarse rows (status/stop_reason) remain execution-terminal facts; consumers must derive completion claims only from the served semantic fields |
| Task-card / terminal status | tasks_v2 coarse status + agent_status/stop_reason ("completed (goal satisfied)") | defined as EXECUTION-terminal display; the canonical semantic surface is goal_outcome + decision (already served additively); frontend semantic interpretation is out of WP-GS implementation scope but the boundary is now explicit |

The audited false completion lived on the FIRST surface only; the
other three are defined-boundary surfaces (verified: the turn composer
never emits goal claims; the Activity/task-card fields are execution
facts).

### 9.2.2 Guard wording (contract absent — both flag modes)

「本次调查已完成证据检索与执行；系统未建立目标语义契约，不对“您的请求
已得到完整回答”作出判定。以下结果可能未覆盖请求的全部内容。」

Coverage-incomplete runs (§7.3) add: 「系统未能将您的比较需求建立为结
构化交付目标，以下结果不构成"同一/不同"的结论。」

### 9.2.3 Guard enforcement example (contract absent — flag ON)

「本次调查已完成证据检索与执行；系统未建立目标语义契约，不对“您的请求
已得到完整回答”作出判定。以下结果可能未覆盖请求的全部内容。」

The guard is pure presentation-branch logic at a single write site:
tools still run, evidence is still kept and rendered, loop terminal
semantics are unchanged — only the unsupported completion CLAIM is
replaced by the honest basis statement. No prose parsing, no NLP, no
new flag.

**Why this is required in the OFF mode too**: the audited false
completion (AF-6) occurred with the flag OFF, and v1's Option B
(documented OFF baseline) left that exact false completion reproducible
in the default user-facing mode. Documenting the residual risk does not
make it safe — the PMO mandate is that no user-facing runtime may
silently equate tool success with goal satisfaction.

### 9.3 Legacy isolation (U3) — test-only injection seam [R3]

Revision 2's environment switch is REPLACED by a dependency-injection
seam, per the PMO requirement that isolation must be technical, not
procedural:

- The guard branch reads ONE module-level policy hook in
  `app/agent_loop.py` (e.g. `_completion_closure_policy()` returning
  GUARD | LEGACY). Production code always returns GUARD; there is no
  environment variable, no flag, and no runtime path that flips it to
  LEGACY.
- The legacy-baseline regression suite injects LEGACY through the
  existing test seam pattern (monkeypatch / fixture) exactly as today's
  suites already monkeypatch planners, resolvers and settings. The
  canonical service cannot reach LEGACY: the injection exists only in
  test code.
- Isolation boundary: LEGACY wording is unreachable from any HTTP,
  UI, or canonical runtime path; only the regression harness can select
  it, and only for the duration of a test.

### 9.4 Options comparison (compatibility impact)

| Option | OFF-mode completion claim | Compatibility impact | Guarantees |
|---|---|---|---|
| v1 Option B (documented OFF baseline) | unsafe claim retained verbatim; env-var escape hatch exposed to the runtime | zero test churn | none for OFF-mode claim-bearing requests — REJECTED by the PMO mandate |
| **Revised (R3): universal guard (U2) + test-only DI seam (U3)** | honest basis statement in both flag modes on ALL completion-bearing surfaces | OFF-mode closure wording changes (one line); legacy-text tests migrate to the DI seam; loop/execution semantics unchanged | no unsupported completion claim in ANY user-facing mode; legacy wording unreachable from the canonical service |
| Regression-only DI seam (U3) | byte-exact legacy wording, isolated to the regression harness | none in user-facing runtimes | preserves the historical baseline as a test fixture |

### 9.5 Facet option (unchanged from v1, restated)

- **Option C — a second flag for the facet**: rejected; the contract
  flag already governs the chain; overlapping flags double the
  interaction matrix.
- The facet rides the SAME `ENABLE_GOAL_SEMANTIC_CONTRACT` flag; under
  flag OFF the facet does not exist (no proposal schema, no block) and
  the universal guard (U2) is what bounds the legacy completion claim.

### 9.6 Recommended configuration (U4)

Canonical / manual-acceptance / demo runtimes: flags ON + universal
guard active + seam per scenario. Legacy-baseline regression runs:
flags OFF + `AGENT_COMPLETION_BASELINE=1`, executed only by the
regression harness. No flag default is changed by this design; the
guard is not a flag — it is an invariant with a test-only escape
hatch.

## 10. RE_PLAN Convergence and Terminal-State Semantics (in scope)

Confirmed separation that already works in flag-ON runs (J3 canonical
evidence: completed/goal_satisfied with one output PENDING and the
outcome rendered 已部分回答):

- Loop stop reason ("re-planned query reproduced an identical
  result…") = EXECUTION convergence only. It never writes satisfaction.
- Per-output satisfaction = GSP-4 only, from facts. A convergence stop
  with an unresolved facet output leaves it PENDING/UNRESOLVED —
  convergence may stop the loop; it can never complete the claim.
- GoalOutcome = `derive_goal_outcome` over the per-output states
  (partial/unanswerable) — unchanged.
- Composer/Activity: the pending boundary renders per output
  (already proven in the canonical J3 run); this design adds the
  facet's comparison-specific wording. Activity additive fields only.

The WP-CT problem (replan_failure terminal with delivered content) is
explicitly out of scope; nothing here changes
`replan_failure`/terminal semantics.

## 11. Invariants and Compatibility Constraints

| Invariant | How preserved |
|---|---|
| LLM proposes / system disposes | facets enter only via validated proposals; the enum is frozen; violations reject the whole proposal |
| Contract immutability across RE_PLAN | facets live on the accepted contract; GSP-7 untouched |
| Evidence grounded in observations, not capability declarations | GSP-4 reads only the projected observation block; capability claims never satisfy |
| Satisfaction ≠ tool success / loop terminal | the facet output is satisfied ONLY by the comparison block; convergence stop cannot complete it |
| GoalOutcome follows per-output statuses | `derive_goal_outcome` unchanged |
| COMPLETED/GOAL_SATISFIED ⇏ COMPLETE | flag-ON derivation unchanged; flag-OFF legacy unchanged and documented as baseline-only (§9) |
| GSP-6 ANY-set semantics | untouched — the facet sharpens postconditions, not coverage |
| GSP-7 preservation | untouched |
| Single/compound compatibility | facet is optional; empty default; INFERRED outputs may declare it too (a single-goal comparison question gets the same protection) |
| Missing evidence never upgraded | partial identity → PARTIALLY_SUPPORTED (complete comparison never claimed); absent → UNRESOLVED; no inference from `is_new_address` |
| Works in the manual-acceptance runtime | universal guard active in BOTH flag modes; recommended canonical configuration = flags ON (real-model COMPOUND evidence: WP-CG-5 RM runs) |
| INV-C [R3] Universal completion-safety across ALL surfaces | loop closure, turn responses, Activity, task-card status — no surface presents execution-terminal facts as semantic completion without a structured basis; enforced at the single composer closure site in BOTH flag modes; legacy wording isolated behind the test-only DI seam (never env/flag) |
| INV-D [R3] Detected-intent coverage | a deterministically detected comparison intent that the accepted contract does not cover marks the run coverage-incomplete; completion claims are basis-scoped and the uncovered intent is disclosed — never silently dropped |

## 12. Alternatives Considered

1. **GSP-4 computes grouping from raw records** — rejected: GSP-4's
   contract is observation-facts-only; the addresses would need to be
   fully projected anyway, and domain aggregation inside the evaluator
   breaks the facts-vs-verdict split.
2. **Detect comparison intent from claim text (NLP)** — rejected:
   fragile, unverifiable, and violates system-disposes.
3. **Generic postcondition plugin registry** — rejected for this WP:
   one facet with executable backing needs no framework; the frozen-
   enum extension is the smaller adequate design (revisit only when a
   second facet lands).
4. **Separate destination-comparison tool** — rejected: the records
   and the address already flow through `finding_drilldown`; a second
   tool would re-fetch the same evidence and add a coverage surface
   for no informational gain.
5. **Legacy-path prose hardening under flag OFF** — superseded by the
   revised §9: v1 treated OFF-mode byte-identity as inviolable, which
   preserved the audited false completion in the default runtime. The
   PMO revision requires the universal guard instead; byte-exact
   legacy wording survives only behind the regression-only escape
   hatch (U3). This v1 position is recorded as REJECTED by revision 2.
6. **Document-only mitigation (keep OFF byte-identical + runbook note)
   [R2]** — rejected: documentation does not constrain behavior; the
   audited false completion would remain reproducible verbatim in the
   canonical user-facing runtime, violating the PMO mandate that no
   mode silently restores "tool success ⇒ goal satisfied".
7. **Env-var legacy escape hatch** (revision 2's U3) — superseded by
   the test-only DI seam [R3]: an environment variable is reachable
   from the canonical service process and is exactly the "procedural
   isolation" the PMO review rejected; the DI seam keeps the legacy
   wording unreachable from production code paths.
8. **LLM self-assessment of request/contract coverage** (a second LLM
   call asking the model whether its proposal covers the request) —
   rejected: violates the single-call constraint, adds a failure mode,
   and the assessment must be deterministic to be an invariant.
9. **Broad claim parser / general comparison DSL** — rejected per the
   design's non-goals; the frozen facet vocabulary plus the bounded
   intent detector is the adequate scope.

## 13. Risks, Dependencies, and Out-of-scope Issues

| Risk | Mitigation |
|---|---|
| Address normalization across chains/case (OQ-2) | conservative exact-trim + 0x-case-insensitive default; normalization string recorded in the block; open question |
| Model proposes facets that do not match the claim semantics | facets are validated against the frozen enum; mis-facetted proposals degrade to the honest verdicts, never to fake completion |
| Renderer regression on non-faceted outputs | facet branch is additive; flag-OFF byte-identity tests |
| RP `address` population gaps | counted explicitly (`records_without_identity`), partial verdicts |
| Proposal-schema addition interacts with CG-2 acceptance | additive optional field; unknown members already rejected; CG-2 tests extended, not changed |
| OFF-mode user-visible closure text changes (guard) [R2] | intended behavior per PMO mandate; legacy wording preserved ONLY via the regression escape hatch; baseline-identity tests migrate to the hatch + new guarded-text assertions |
| Escape hatch accidentally set in a user-facing runtime | DI seam is test-code-only; production has no path to LEGACY; guarded wording is the canonical assertion |
| Coverage-detector vocabulary misses a paraphrase [R3 residual, stated] | degradation = normal retrieval path with the STANDARD wording (no coverage mark) — the documented extraction-completeness boundary; layered mitigation: model facet proposal + detector + real-model regression measuring actual coverage |

Dependencies: none on WP-FR/ES; shares the observation projection with
WP-CT (additive only). Out of scope: everything listed in the charter
§4 plus the WP-FR/ES/CT scope items.

## 14. User-Journey-Level Acceptance Criteria (for later implementation)

Canonical topology :3001→:8001→:8000; API E2E mandatory for all;
browser journeys mandatory wherever the response text is asserted
(items 1–8); flag OFF covered by baseline identity. The nine
withdrawals of the audited shape (W000164…) are the fixture basis;
identity fields are fixture-controlled:

1. **Nine distinct destinations** → response states 「9 笔提现指向 9 个
   不同目的地」 with per-group record IDs and the destination column;
   goal_outcome derives from the satisfied comparison; NOT
   "records retrieved".
2. **All nine identical** → negative-polarity conclusion 「全部 9 笔提
   现指向同一目的地」+ provenance; satisfied (negative).
3. **Mixed repeats** → groups and counts reflect the actual partition;
   conclusion matches the grouping.
4. **Some records missing identity** → PARTIALLY_SUPPORTED with
   「M/N 笔缺少目的地标识」; no same/different conclusion.
5. **Retrieval ok, comparison facts absent** → UNRESOLVED/
   not-determined boundary; `goal_satisfied` may appear as loop
   terminal ONLY alongside a non-complete GoalOutcome and the
   not-determined section.
6. **RE_PLAN returns identical evidence** → loop stops on convergence;
   facet output remains not-satisfied; overall outcome honest; Activity
   shows the convergence stop reason and the per-output states without
   claiming completion.
7. **Flag ON canonical run** (the audited configuration): the audited
   request produces the faceted contract and the honest semantics —
   the exact false-completion cannot reproduce.
8. **Flag OFF baseline run**: legacy behavior byte-identical
   (documented limitation: the legacy completion semantics are the
   known residual risk of this mode — manual acceptance runs use flag
   ON per §9).
9. **Existing single-output retrieval** (non-faceted) unchanged:
   satisfied-by-retrieval semantics preserved with the existing
   rendering.
10. **Compound/GSP invariants**: an existing two-output COMPOUND run
    and a GSP-7 RE_PLAN run behave as before (regression suites green
    like-for-like).
11. **Flag ON, valid contract** [R2]: the faceted comparison output
    follows §7.2 exactly (complete → polarity conclusion; partial →
    M/N boundary; absent facts → UNRESOLVED); API E2E mandatory.
12. **Flag ON, contract missing** [R2]: guarded closure in both the
    legacy and contract-less completion paths; the response never
    claims whole-request completion; API E2E + browser journey
    (user-visible wording asserted).
13. **Flag OFF canonical user-facing runtime** [R2]: the universal
    guard is ACTIVE (the completion claim is basis-scoped); legacy
    execution is preserved; API E2E + browser journey.
14. **Isolated legacy-baseline mode** [R2]: with the regression escape
    hatch set, the byte-exact legacy wording is reproduced and this
    mode is reachable ONLY from the regression harness (asserted by
    configuration, never in user-facing runtimes).
15. **Unknown normalization semantics** [R2]: records whose address
    format cannot be classified are compared case-sensitively,
    annotated `exact_unclassified`, never dropped; the conclusion
    states the applied normalization.
16. **No-new-information RE_PLAN with the comparison unresolved** [R2]:
    convergence stops the loop; the facet output remains
    pending/unresolved; overall outcome partial/unanswerable; the
    response renders the not-determined boundary — never a completion
    claim (API E2E; extends the canonical J3 evidence shape).
17. **Coverage-incomplete run** [R3]: a comparison-shaped request
    whose proposal omits the facet → contract accepted,
    `coverage="comparison_intent_uncovered"` recorded, response =
    covered deliverables + uncovered-intent disclosure; never
    "已完整回答" (API E2E + browser journey).
18. **Turn-path parity** [R3]: turn responses never emit a
    whole-request completion claim without a structured basis
    (asserted against the preserved turn wording suite).
19. **DI-seam isolation** [R3]: with no test injection, LEGACY closure
    wording is unreachable from any HTTP/UI path (negative probe);
    with the seam injected in the regression harness, the byte-exact
    legacy wording is reproduced.

## 15. Open Questions Requiring Product Decisions

- OQ-1 Destination exposure: full address vs masked vs alias-only in
  the user-visible table/conclusion (design recommends full value +
  alias-in-prose; needs product sign-off).
- OQ-2 [REFINED by revision 2] RP provides `asset` per withdrawal but
  no chain/network identifier. Current design: compare on the
  normalized address, record asset composition per group, annotate
  cross-asset groups. Open sub-questions: (a) can one withdrawal
  stream legitimately contain addresses from different chains (then
  cross-asset groups are genuine same-destination cases), and (b) does
  RP guarantee the `address` field is populated for every withdrawal
  (observed populated in U00010/U00020 data; not contractually
  documented — the block counts gaps explicitly).
- OQ-3 [SUPERSEDED by revision 2] v1 asked whether the OFF mode
  should carry a disclosure line. Resolved: YES — the universal guard
  (§9.2) applies in both flag modes; the residual decision is only the
  escape-hatch governance for the isolated baseline suite (U3).
- OQ-4 Facet vocabulary governance: who approves new facet values
  (proposed: same review as EvidenceType additions — and the same
  governance path applies to the coverage-detector vocabulary).
- OQ-6 [R3] Measure real-model comparison-coverage over an expanding
  paraphrase set (extends WP-CG-5's A–D measurement) before relying
  on the detector alone.
- OQ-5 [RESOLVED by revision 3, §7.3] The omitted-facet path is
  closed by the deterministic request-coverage assessment: a detected
  comparison intent that the accepted contract does not cover marks
  the run coverage-incomplete with a basis-scoped completion claim.
  Residual boundary (exact): paraphrases outside the frozen detector
  vocabulary are undetected and degrade to the normal retrieval path
  with the STANDARD outcome wording — this residual is real but does
  NOT block the original false-completion fix (the audited request is
  inside the vocabulary; layered defense = model facet proposal +
  detector + measured real-model coverage per OQ-6).

## 16. Implementation Status

**Implementation has not begun.** This document is a proposal for PMO
review; no source code, tests, flags, runtime configuration, or data
were modified in its preparation. The only worktree addition from this
engagement is this file.

---

*End of design proposal. Not approved for implementation.*
