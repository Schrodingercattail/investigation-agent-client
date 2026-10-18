# GSP — Goal Semantic Preservation — Design v1 (Tightened)

**Date**: 2026-10-07
**Status**: DESIGN REVISION v1 — supersedes the GSP draft circulated in the
AL-3.5.2 diagnosis discussion (chat, 2026-10-05/07), incorporating six
semantic corrections agreed in review. No code changed by this document.
**Design-only predecessor**: AL-3.5.2 root-cause diagnosis (Issue 3/4/5
lineages, `TASK-5e552a72d960` et al.). **Related, unchanged**:
`docs/plans/2026-10-05-al3-5-1-target-validity-guard-design-v1.md` (G1).
**Aligned with**: `AGENT_CLIENT_ARCHITECTURE_V2`,
`WEEK1_SEMANTIC_MATRIX_AUDIT_V1` (semantic distinctions + P1–P20),
`SKILL_MODEL_V1` (Skill/Capability/Tool/Plan responsibility table),
`AGENT_RESPONSE_EVIDENCE_PRINCIPLES_V1` (P5/P14).

---

## 0. Scope of This Document

Design diagnosis → tightened design, to Implementation-Plan readiness.
GSP's single sentence:

> Once a user's goal has been understood and **accepted** as a Goal
> Semantic Contract, no downstream component (Planner, Capability
> routing, Observation handling, Loop/Re-plan, Completion, Response
> Composition) may silently alter, narrow, substitute, or prematurely
> declare satisfaction of that contract. Everything the user asked for
> must remain traceable to an explicit, per-item state until the turn
> ends with a deterministic, per-item honest answer.

## 1. The Three-Phase Model — and the Boundary This Design Does NOT Claim

GSP strictly separates three phases that the earlier draft conflated:

| Phase | Question | Owner | GSP's role |
|---|---|---|---|
| **1. Requirement Extraction** | "What did the user actually ask for?" | LLM goal understanding (existing `understand_goal`), producing a *proposed* contract | **Producer only** — its completeness is NOT guaranteed by GSP |
| **2. Contract Preservation** | "Once accepted, is the contract's semantics kept end-to-end?" | Deterministic boundaries (planner validation, capability admission, postcondition checks, composer) | **GSP's entire scope** |
| **3. Goal Satisfaction** | "Is there grounded material to actually answer each requirement?" | Deterministic postcondition evaluation over observations | **GSP houses the bookkeeping; domain truth stays with RP** |

**Boundary / limitation (normative)**:

- A downstream coverage check can prove only *"the system faithfully
  executed the accepted contract"* — it can never prove *"the accepted
  contract completely expressed the original request."* Requirement
  extraction remains an upstream LLM product whose completeness is a
  separate problem (see Future Hook, §11).
- GSP therefore must not be cited as solving request omission in general.
  It converts silent omission into **visible per-item state** (including
  "missing from the contract" remaining invisible — the honest residual).

## 2. Confirmed Common Failure Pattern (Issue 3 / 4 / 5, one causal chain)

All three lineages show the same shape: the system's richest
understanding of the request (GoalRepresentation) is consumed only by
the UI; every downstream decision re-derives semantics from raw text or
payload shape, and "a tool succeeded" is the only completion currency.

- **Issue 3**: goal layer held BOTH requirements (goal_repr reason names
  the overlap question); planner produced Part A only; completion fired
  on the cross-account SUCCESS though none of the 6 candidates involved
  U00010; composer answered "有" (subject-scope substitution + requirement
  loss).
- **Issue 4**: goal layer identified shared-IP; planner substituted the
  nearest capability (related-account) because nothing declares evidence
  types; the ToolResult *contained* `shared_ips: []` — the grounded
  answer — and the composer discarded it in favor of the device
  narrative (evidence-type substitution).
- **Issue 5** (`TASK-5e552a72d960`): goal layer said `policy_check`
  (correct); target resolution bound 洗钱→F1 (ML collision, G1 OFF);
  `signal_explain(F1)` returned evidence_missing; the re-plan decision
  *knew* "尚无交易与资金流证据来判定是否洗钱" — yet selected
  related-account analysis; its SUCCESS observation satisfied
  `criteria_satisfied()` (ANY-success rule, `agent_loop.py:524-543`) and
  became the answer body. No stale state: every artifact of the wrong
  answer was produced *this turn* (unrelated-successful-evidence
  substitution).

## 3. Current Architecture Gap (code-level)

| Boundary | Today | Gap |
|---|---|---|
| GoalRepresentation | Frozen 7-entry `goal_type` vocabulary; `subject_type` ∈ {finding, case}; prose `reason`; immutable snapshot on `AgentLoopState`; consumed **only** by the Activity projection | No multi-requirement structure, no evidence dimension, no per-item state; not an input to any decision |
| Planner | `plan(goal_text, ctx, eligible_ids, caps)` (`planner_v2.py:176`); skills presented by `description` prose | Coverage of requirements is unchecked; requirements beyond the matched skill's `planning_steps` vanish |
| Capability routing | Eligibility = finding-capability gating + registry checks; no evidence-type declaration anywhere | Nearest-prose-match substitution is structurally unpreventable; `NO_ELIGIBLE_SKILL` exists but cannot fire for "right skill family, wrong evidence type" |
| Observation | `ToolResult.outcome` + free-form `data`; `evidence_missing`/`next_data_needed` convention exists | No association to a requirement; no postcondition; SUCCESS is the only currency that matters downstream |
| Completion | `criteria_satisfied()` = ANY non-gap SUCCESS observation; **policy-enforced STOP dominance** (`agent_loop.py:770`) | "One successful tool = goal satisfied" is deterministic system behavior |
| Composer | `answer = _case_level_answer_lines(state) or _finding_answer_lines(state)`; selection by payload kind + outcome/insufficiency markers | No goal-relevance check; decision-layer boundary knowledge (`state.decisions`) is not a composer input |

## 4. Goal Semantic Contract (revised schema)

Carrier: a field on `AgentLoopState` (existing store; no new DB), created
once per goal-run at goal understanding, **content immutable**; only
per-output `satisfaction` state is mutable (by the deterministic
postcondition evaluator, never by the LLM).

```python
class RequiredOutput(BaseModel):
    output_id: str                      # O1, O2, ... (run-scoped)
    claim: ClaimRef                     # WHAT the user wants judged/answered (A)
    evidence_requirements: list[EvidenceType]   # WHAT evidence supports it (B)
    subject_ref: str                    # WHO/WHAT is investigated (U00010, case)
    scope: ScopeLevel                   # WHERE evidence may be collected (see §5)
    status: OutputSatisfaction = OUTPUT_PENDING
    detail: str | None = None           # user-safe bounded reason (boundary text source)

class GoalSemanticContract(BaseModel):
    goal_type: str                      # existing frozen vocabulary (unchanged)
    claim: ClaimRef | None              # contract-level claim when single-assessment
    subject_ref: str
    outputs: list[RequiredOutput]       # >= 1; flat list, no sub-goal trees
```

### 4.1 Claim / Assessment vs Evidence Requirement (Correction #2)

Two **separate dimensions** — never one field wearing both hats:

- **Claim (assessment)** — *what the user ultimately wants judged or
  answered.* Open-ended by nature (users can ask anything):
  `money_laundering_assessment`, "does the account have hedge trades",
  "which accounts are related", "why was F1 flagged". Claims are
  short LLM-proposed labels + bounded description, **not** a frozen
  vocabulary (a frozen claim vocabulary would pretend the system can
  assess everything — the opposite of honesty).
- **Evidence requirement** — *what kind of evidence would support that
  claim.* A **frozen, closed vocabulary** aligned 1:1 with what existing
  executable tools actually return (initial set, extensible only when a
  binding declares new support):

  `network_relations · shared_device_evidence · shared_ip_evidence ·
  opposite_trade_relations · withdrawal_records · transaction_records ·
  ml_signal_explanation · rule_explanation · policy_references ·
  graph_cluster_facts · case_summary`

  Example decomposition (Issue 5): claim = `money_laundering_assessment`;
  evidence_requirements = [transaction_activity, fund_flow/withdrawal,
  account_relationship, policy_context, risk_signal] — the assessment
  may *draw on* several evidence types and is **not itself** an evidence
  type. The earlier draft's `evidence_type: policy` for a laundering
  question was exactly this conflation and is corrected here.
- **Responsibility boundary**: `goal_type` = coarse intent class
  (routing-era field, unchanged); `claim` = the specific judgment
  requested; `evidence_requirements` = admissible support; `subject_ref`
  = who; `scope` = where; `target` = the (G1-admitted) investigation
  object, when a finding focus exists. G1's `evidence_kind`
  (explicit_selection / concept_grounding / …) answers *"how was the
  target established"* and is deliberately **distinct** from evidence
  requirements (*what kind of proof the answer needs*).

### 4.1a Evidence Requirement Semantics — MVP = ANY (normative contract refinement, 2026-10-08)

> **CLARIFICATION**: this section locks down semantics that the runtime
> already implements but that earlier revisions left implicit. It is a
> contract refinement, not a claim that ANY was formally specified from
> the start.

**GSP-MVP Evidence Requirement Rule (canonical):**
`RequiredOutput.evidence_requirements` is an ANY-set of alternative
evidence categories. Matching any one category makes an observation
eligible for that output's postcondition evaluation; it does not by
itself satisfy the output. Output satisfaction is determined exclusively
by the deterministic postcondition evaluator (WP-GSP-4).

Normative rules for the MVP:

1. `evidence_requirements` is syntactically an ordered list, but its
   semantic meaning is an unordered ANY-set of alternatives.
2. Any ONE evidence type may provide a valid evaluation path for the
   output.
3. Evidence types are alternatives, not mandatory cumulative
   requirements.
4. The list does not express ALL semantics.
5. The list does not itself encode postconditions.
6. Matching an evidence type does not imply the output is satisfied.
7. Satisfaction remains determined exclusively by the deterministic
   postcondition evaluator.
8. The MVP does not support arbitrary AND / OR / conditional requirement
   expressions.
9. A future requirement-group model may introduce explicit
   ALL / ANY / CONDITIONAL semantics; that is outside the MVP.

**ANY ≠ ANYTHING-GOES — worked example**:

```text
evidence_requirements = [transaction_records,
                         account_relationship,
                         policy_references]
```

This is NOT "any related tool succeeded → output satisfied". It IS:

```text
actual observation provides transaction_records
  → transaction_records ∈ evidence_requirements
  → this evidence category enters postcondition evaluation
postcondition evaluator
  → decides whether the evidence really supports the output
```

`execution success ≠ semantic success` and
`evidence-category match ≠ satisfaction` remain in force. Example B
(below) shows the laundering case: even when `transaction_records` is
delivered, an `assessment` claim evaluates UNRESOLVED — the categories
are candidate paths, not proof.

**Examples**:

- **Example A — risk level retrieval**: claim = risk_level,
  requirements = [case_summary, risk_signal]. Semantics:
  case_summary OR risk_signal. If case_summary authoritatively returns
  the risk level, no additional risk_signal retrieval is required.
- **Example B — money laundering assessment**: claim =
  money_laundering_assessment, requirements = [transaction_records,
  fund_flow, account_relationship, policy_context, risk_signal]. ANY
  means these are all POTENTIALLY legal evidence categories. It does
  NOT mean "transaction_records delivered → laundering established".
  With no deterministic adjudication, the output evaluates UNRESOLVED
  (implemented: `ClaimKind.ASSESSMENT` short-circuit, WP-GSP-4).
- **Example C — shared IP assessment**: claim = shared_ip_assessment,
  requirements = [shared_ip_evidence]. A single requirement: ANY
  degenerates naturally to that one candidate. `shared_device_evidence`
  is NOT its alternative — no nearest-capability substitution.

**Consumer semantics under ANY**:

| Stage | Consumption |
|---|---|
| GSP-3 association | `actual_evidence_types ∩ evidence_requirements` → CANDIDATE association only; a match against one ANY-item never implies satisfaction |
| GSP-4 postcondition | first determinate category verdict per the requirement set → output satisfaction; subject/scope/completeness/conflict gates all still apply |
| GSP-5 composer | consumes `RequiredOutput.satisfaction` ONLY — never re-judges ANY/ALL over requirements |
| GSP-6 planner coverage (future) | coverage = at least one legal executable capability covering the requirement set (A OR B OR C). **Planner coverage ≠ postcondition satisfaction.** |

**Decision record**:

- **Decision**: `RequiredOutput.evidence_requirements` semantics for
  GSP-MVP = ANY.
- **Alternatives considered**: ALL semantics; an explicit
  ANY/ALL/CONDITIONAL operator field; requirement groups / conditional
  DSL.
- **Decision rationale**: the field represents evidence ALTERNATIVES;
  ALL would wrongly force outputs answerable via any single path to
  collect every category; no second DSL in the MVP; cases genuinely
  requiring multiple categories to hold together must be expressed by
  explicit postcondition semantics or a future requirement-group
  contract — never by silently reinterpreting the list as ALL.
- **Known flagged deviation (reported, minimal fix deferred)**:
  `evaluate_output` (`app/goal_postconditions.py:308`) returns the
  FIRST determinate category verdict in LIST order. When one category
  yields PARTIAL and a later-listed category would yield SATISFIED,
  the result is order-sensitive — in tension with the unordered-set
  semantics. Minimal fix (follow-up, not in this clarification round):
  collect all category verdicts and apply a fixed determinate
  precedence (SATISFIED > NOT_SATISFIED > PARTIALLY_SUPPORTED >
  UNRESOLVED) instead of first-wins. Current live impact is bounded:
  assessment claims short-circuit before the loop, and most accepted
  outputs carry a single requirement.
- **Future evolution**: if real requirement semantics like
  `(A AND B) OR (C AND D)` emerge, they MUST be expressed through a new
  explicit requirement-group / operator contract — not by extending the
  implicit interpretation of the current list.

**Claim-kind boundary (assessment)**: `claim_kind = assessment` marks
the output as a domain JUDGMENT. It is orthogonal to capability
coverage: an assessment output is planning-COVERED when its evidence
requirements map to declared executable categories (normal rules), and
it evaluates UNRESOLVED downstream (`judgment_not_adjudicable`) because
no MVP binding adjudicates a judgment. An earlier draft's blanket
assessment gate (`assessment → NO_ELIGIBLE_CAPABILITY`) was removed —
coverage is decided by evidence requirements, never by claim kind.

**Execution status ≠ semantic GoalOutcome (semantic clarification,
2026-10-08)**: `AgentStatus.COMPLETED` is the loop EXECUTION terminal
status and `StopReason.GOAL_SATISFIED` is the loop STOP reason (which in
this runtime also fires on convergence / no-new-information). Neither is
GSP semantic completion: a run may terminate `completed/goal_satisfied`
while its semantic outcome is UNANSWERABLE (verified live, laundering
attempt: assessment output → UNRESOLVED(judgment_not_adjudicable) →
GoalOutcome.UNANSWERABLE). Semantic completion is determined exclusively
by per-output `RequiredOutput.satisfaction` and the deterministic
`derive_goal_outcome()`; deriving GoalOutcome from agent_status or
stop_reason alone is prohibited.

**Conceptual errors explicitly excluded by this contract**:

1. requirements = [A, B] → "must collect A and B" — WRONG (MVP = ANY).
2. actual A matches requirement A → SATISFIED — WRONG (postcondition
   decides).
3. planner can execute A → output satisfied — WRONG (coverage ≠
   satisfaction).
4. laundering requirements = [transaction, network, policy] → any one
   evidence proves laundering — WRONG (claim semantics and evidence
   semantics are orthogonal; assessments stay UNRESOLVED).
5. composer sees any supporting evidence → render as answer — WRONG
   (composer consumes the determined satisfaction state only).

### 4.2 Subject vs Scope (Correction #4)

Two dimensions, both present, never merged into `subject_type`:

- **Subject** — *who/what is investigated*: `U00010`, finding F5, the
  case. Existing `subject_ref` retained.
- **Scope** — *how far evidence collection may reach*. Reuses the level
  names the system already embodies (case-level vs finding-level skills;
  `InvestigationContext` focus fields; binding parameter locks such as
  `analysis_type=related_account_analysis` / `cross_account_opposite_trade`
  and `view=` locks — no new store, no duplicate abstraction):

  `case_level · finding_level · event_level · account_level ·
  relationship_graph`

  Examples: "这个账号和其他账号是否有对冲交易" → subject=U00010,
  scope=`relationship_graph` (subject-inclusion constraint applies);
  "这个案例有哪些关联账号" → subject=case U00010,
  scope=`relationship_graph`. The executable-level scope carriers remain
  the parameter locks; the contract's `scope` is their admission-level
  mirror, checked at plan validation.

### 4.3 Output satisfaction state machine (Correction #3)

**Evidence association ≠ output satisfaction.** Writing
`required_output_id` on an observation says only *"this observation was
collected for that output"* (association). Whether it *satisfies* the
output is a separate, **system-controlled postcondition evaluation**:

```
Observation → Postcondition Check (deterministic) → Output.satisfaction
```

Postconditions are small deterministic evaluators keyed by evidence type
(never LLM-asserted). Initial definitions:

| Postcondition (per evidence type) | Deterministic check |
|---|---|
| subject inclusion | payload's relevant records/candidates contain `subject_ref` where the output's constraint requires it |
| scope completeness | retrieval boundary flags (e.g. `truncated`, budget markers) absent, or downgrade per §5 |
| gap discipline | `evidence_missing` / `next_data_needed` honored (existing convention) |
| negativity is determinate | a complete-scope search with zero subject-matching records **is a grounded answer** (satisfied, negative polarity) — prevents the opposite failure of looping on a true negative |

```python
OUTPUT_SATISFIED            # grounded determinate answer produced in scope
OUTPUT_PARTIALLY_SUPPORTED  # grounded but scope-truncated / partial coverage
OUTPUT_NOT_SATISFIED        # attempted, no grounded determinate answer
OUTPUT_UNRESOLVED           # data ambiguous / cannot determine (UNKNOWN ≠ FALSE)
OUTPUT_UNSUPPORTED          # no eligible capability declares the evidence requirement
OUTPUT_PENDING              # not yet attempted
```

**Invariant I-1 (correction #3)**: `execution success ≠ semantic
satisfaction`. A SUCCESS observation with `for_output=O1` never advances
`O1.status` past PENDING by itself; only the postcondition evaluator
writes satisfaction. (Issue 3's six successful candidates did not answer
"U00010 是否有对冲交易"; the evaluator's subject-inclusion check yields
SATISFIED with negative polarity — an honest negative answer — while the
composer must render exactly that polarity.)

## 5. Three-Layer State Model (Correction #5)

Three layers with distinct vocabularies; no layer's terminal state may
be reported as another layer's success:

| Layer | States | Owner |
|---|---|---|
| **Execution termination** (per output / run) | `RUNNING · CONVERGED · BLOCKED · NO_ELIGIBLE_CAPABILITY` | Loop + plan validation |
| **Output state** | `SATISFIED · PARTIALLY_SUPPORTED · NOT_SATISFIED · UNRESOLVED · UNSUPPORTED` (§4.3) | Postcondition evaluator |
| **Overall goal outcome** (derived, deterministic) | `COMPLETE` (all SATISFIED) · `PARTIAL` (mixed SATISFIED with UNSUPPORTED/UNRESOLVED/PARTIAL) · `UNANSWERABLE` (all outputs UNSUPPORTED/UNRESOLVED) · `BLOCKED` (dependency/execution outage) | Derived — never LLM-set |

**Invariant I-2**: `NO_ELIGIBLE_CAPABILITY` / `UNSUPPORTED` is a
*terminal execution condition* for that output — it may STOP planning and
produce an honest capability-boundary statement, but it can never write
`SATISFIED`, and it can never let an unrelated tool's success mark the
overall goal `COMPLETE`.

## 6. Capability Declaration at the Executable Level (Correction #6)

- **Authoritative declaration lives on `StepToolBinding`** (the
  executable contract: tool + parameter locks — `skills.py:84-96`),
  extended with:

  ```python
  supported_evidence_types: list[EvidenceType]   # frozen §4.1 vocabulary
  ```

  Initial declarations mirror what each binding's runtime contract
  *actually returns today* (zero new capability):
  `related_account_analysis` → [network_relations, shared_device_evidence,
  shared_ip_evidence(availability)] · `cross_account_trade_analysis` →
  [opposite_trade_relations] · `signal_explain` →
  [ml_signal_explanation, rule_explanation, graph_cluster_facts] ·
  `finding_drilldown(view=withdrawals/transactions/timeline)` →
  [withdrawal_records / transaction_records / event-level] ·
  `policy_lookup` → [policy_references] · `risk_case_fetch` →
  [case_summary] · `artifact_bundle` → [case_summary].
- **`SkillDefinition` remains discoverability/planning guidance**
  (SKILL_MODEL_V1 responsibility table: Skill = reusable workflow
  definition; Tool = execution primitive). Its `description` prose must
  never be the basis of an eligibility decision.
- Planner eligibility check (plan validation) binds an output's
  evidence requirements to binding declarations: **no declaring binding
  → `NO_ELIGIBLE_SKILL` → output UNSUPPORTED** (existing honest path).

**Invariant I-3 (nearest-capability fallback prohibited)**: when no
binding declares the required evidence type, the system MUST NOT select
a semantically adjacent capability as a substitute. `shared_ip`
unavailable → no `shared_device` fallback; `money_laundering_assessment`
unavailable → no `related_account_analysis` fallback. This is the
goal-level extension of the WEEK1 matrix's existing distinctions
(`unsupported ≠ unresolved`; `intent ≠ target`; `capability ≠ data
availability`) and of P19 (transparency must not imply unsupported
paths). **Invariant I-4 (capability availability ≠ data availability)**:
a declaration admits routing; whether RP actually holds the data is
answered by the observation/EMPTY semantics — a declared-and-empty
shared-ip check (Issue 4: `shared_ips: []`) is a SATISFIED negative
answer, not a routing failure and not a device answer.

## 7. The GSP Semantic Chain (normative)

```
User Request
 → Requirement Extraction        "用户到底要求了什么？"            (LLM proposes; NOT guaranteed complete)
 → Accepted Goal Semantic Contract "接受后，语义是否被保持？"       (deterministic preservation)
 → Planning Coverage             "每个 output 有没有合法执行路径？" (coverage check; else declared UNSUPPORTED)
 → Executable Capability Admission "这个绑定是否具备回答该 output 的能力？" (binding declarations)
 → Tool Execution                "工具有没有成功运行？"             (unchanged)
 → Observation                   "工具返回了什么事实？"             (association: for_output)
 → Evidence / Postcondition Evaluation "这些事实是否真的支持该 output？" (deterministic evaluators)
 → Required Output Satisfaction  "该 output 已回答/部分/未回答/不可回答？" (§4.3 states)
 → Overall Goal Outcome          "整个请求最终是什么状态？"         (COMPLETE/PARTIAL/UNANSWERABLE/BLOCKED)
 → Deterministic Response Composition "只渲染与各 output/goal scope 相关的内容" (per-output; boundary statements included)
```

**Strengthened invariants** (each extends an existing WEEK1 distinction
to the goal level — not new philosophy, new enforcement points):

| Invariant | Extends | Enforced at |
|---|---|---|
| I-1 execution success ≠ semantic success | "execution result ≠ semantic result" | postcondition evaluator + completion |
| I-2 evidence existence ≠ evidence relevance | P5 (answer the question) | observation↔output association + composer |
| I-3 capability availability ≠ data availability | WEEK1 verbatim | binding declarations + EMPTY semantics |
| I-4 unrelated successful evidence ≠ user-request answer | P5 goal-level | per-output completion (replaces ANY-SUCCESS) |

## 8. Boundary Enforcement Points (where each correction lands)

| Correction | Enforcement point | Mechanism |
|---|---|---|
| #1 extraction vs preservation | §1 boundary + Non-goals | documentation + composer never claims completeness |
| #2 claim vs evidence | contract schema (§4.1) | two fields, two vocabularies (open claims / frozen evidence) |
| #3 association vs satisfaction | postcondition evaluator (§4.3) | system-only status writes; I-1 |
| #4 subject vs scope | contract schema (§4.2) | two fields; reuse of existing scope carriers |
| #5 UNSATISFIABLE ≠ satisfied | three-layer model (§5) | I-2; derived goal outcome |
| #6 binding-level capability | `StepToolBinding.supported_evidence_types` (§6) | I-3; NO_ELIGIBLE_SKILL reuse |

## 9. GSP-MVP (minimal viable slice, unchanged carrier decisions)

1. Contract = `RequiredOutput[]` + contract-level claim (§4); LLM
   proposes outputs **in the existing `understand_goal` call** (single
   call, frozen evidence vocabulary validated pydantically — same
   pattern as GoalRepresentation's validator).
2. Planner: `plan(..., goal_contract=...)`; **coverage check** at plan
   adoption — every output mapped to a step OR explicitly declared
   UNSUPPORTED; uncovered+undeclared → plan rejected (existing
   planning_failure path).
3. Capability admission: binding-level `supported_evidence_types`
   (declarations only; zero new tools).
4. Observation: `for_output` association written by the loop (step↔output
   map is already known at adoption; **no ToolResult schema change**).
5. Postcondition evaluator: deterministic, per-evidence-type, system-only
   (§4.3); initial evaluators: subject-inclusion, scope/truncation, gap
   discipline.
6. Completion: flag-gated `criteria_satisfied` — no PENDING outputs (all
   SATISFIED/UNSUPPORTED/etc.); `criteria_kind` retained for
   compatibility; STOP-dominance applies only to the per-output regime.
7. Composer: per-output rendering — satisfied (with polarity: positive
   findings OR determinate negatives like "未发现涉及 U00010 的候选"),
   UNSUPPORTED/UNRESOLVED → boundary statements; unrelated observations
   invisible to the answer body; runtime error-code lines removed from
   user-visible closures (AL-3.5.2 finding).
8. G1 compatibility: G1 unchanged; its admitted `target` is consumed by
   the contract; flags independent
   (`ENABLE_GOAL_SEMANTIC_CONTRACT`, default OFF).
9. Capability surface: case-level/finding-level/turn/loop unchanged;
   MVP wires loop first; turn path follows (composer + postcondition
   first, planner validation second slice).
10. Backward compatibility: additive fields only; flag OFF = current
    runtime behavior; re-plan digest additionally carries machine facts
    (unsatisfied outputs; this-run unsupported (tool, focus) pairs —
    re-plan filter blocks repeats, closing the Issue-5 4×-retry corner).

## 10. Validation Against the Three Issues (design walk-through)

**Case A — Issue 3**: outputs O1 (subject=U00010, scope=relationship_graph,
evidence=opposite_trade_relations, subject-inclusion), O2 (counterpart
range), O3 (overlap; evidence=set_intersection → **no binding declares
it** → plan validation marks O3 UNSUPPORTED at adoption: "集合重叠分析当前不支持"
renders verbatim). O1's postcondition on the 6-candidate observation:
subject-inclusion fails to find U00010 → **SATISFIED (negative polarity)**
→ composer renders "在检索范围内未发现涉及 U00010 的候选对；6 组候选均为其关联账号之间"
instead of "有". Requirement loss impossible (O2/O3 are first-class states).

**Case B — Issue 4**: O(shared_ip, subject=U00010, scope=relationship_graph).
Routing: `related_account_analysis` **declares** `shared_ip_evidence`
(availability) → routing legal; postcondition reads `shared_ips: []` →
SATISFIED (negative) → "未发现共享 IP 关系；这些账号经由共享设备关联"。
If a future tool returned no shared-ip field at all → binding loses the
declaration → UNSUPPORTED → honest capability boundary. Neither path can
produce the device narrative as the answer to the IP question (I-2/I-4).

**Case C — Issue 5**: claim = `money_laundering_assessment`; O1 evidence
requirements = [policy_context, fund_flow, transaction_activity,
account_relationship, risk_signal] — **not** `policy` alone. F1's
binding declares `ml_signal_explanation` only: even with G1's residual
"ML" collision admitted at target level, **plan validation cannot bind
O1 to any step** → O1 UNSUPPORTED ("当前证据与能力不足以作出洗钱认定")，
composer renders that boundary + may list F1's ML signal as explicitly
non-conclusive reference. The related-account SUCCESS observation,
associated to no output, is invisible to completion and answer body.
Re-plan filter blocks the repeated unsupported withdrawal drilldown.
`criteria_satisfied` (per-output) cannot fire on unrelated success.

## 11. Relationship to G1 / Trade-offs / Open Questions

**G1 vs GSP**: G1 = *target admission* (input side, pre-planning, single
decision; unchanged). GSP = *downstream semantic continuity* (planning →
completion → composition). Shared carrier: the contract's `target`
consumes G1's admitted decision; shared vocabulary discipline; no
duplicate governance — GSP never re-judges target validity, G1 never
judges output satisfaction.

**Trade-offs / Open Questions** (carried from the design discussion,
updated):

1. Contract content immutable, per-output satisfaction mutable — settled.
2. Follow-ups create a new per-turn contract; cross-turn referenced sets
   (e.g. the 18-account set) remain **open** (MVP: prose + honest
   UNSUPPORTED; structured `known_context` is a future extension).
3. Multi-intent = flat `outputs` list — settled (no sub-goal trees).
4. Contract vs AgentLoopState boundary: contract = What/Why; state =
   How-it-went; contract embedded in state, single write-channel for
   satisfaction — settled.
5. ToolResult metadata: **not needed in MVP** (loop-level association);
   revisit only if the tool ecosystem grows — settled for MVP.
6. Relevance: deterministic first (vocabulary/declaration/subject
   checks); LLM-assisted relevance is a future, never-sole-authority
   extension — settled for MVP, boundary cases (semantic relevance
   beyond declarations) remain open.
7. Ambiguous goals → existing resolver clarification paths; contract
   carries `goal_resolution` — settled.
8. Missing capability → decided at plan validation (UNSUPPORTED),
   expressed at composer (boundary text) — settled.
9. PARTIAL = per-output states; composition renders each — settled.
10. DSL risk: bounded by "frozen enums + short prose lists + one state
    machine; only membership/equality checks" — settled; vigilance note:
    any future field that needs a rule language is a design smell.

**Future hook (explicitly out of MVP)**: *Extraction completeness
check* — after the contract is proposed, an optional validation step
(could be LLM self-check against the request, or explicit user
confirmation of parsed requirements in the UI) that audits
contract↔request coverage. GSP's machinery gives this hook a place to
land (it edits the contract **before acceptance**), but MVP does not
build it.

## 12. Non-goals / Known Limitations

- **GSP does not guarantee extraction completeness** (§1): a requirement
  the extractor missed is invisible to every downstream guarantee.
  GSP's honest contribution is per-item visibility for what *was*
  extracted — and structured status that makes future extraction
  auditing possible.
- **GSP is not capability completeness**: UNSUPPORTED outputs are
  honest capability boundaries, not product gaps being papered over;
  adding capabilities is a separate track (and must come with binding
  declarations, never prose-only).
- **GSP is not domain truth adjudication**: satisfaction means "grounded
  determinate material exists in scope", never "the claim is true";
  laundering/AML judgments remain Risk Platform / analyst territory.
- **GSP does not fix extraction-phase mis-binding by itself** (e.g. the
  洗钱→F1 collision is G1's target admission; GSP adds the second net —
  evidence-requirement mismatch at plan validation — but does not replace
  G1).
- **No new databases / ToolResult schema / second state store / lexicon
  / MCP / keyword tables** — carrier decisions fixed at AgentLoopState +
  binding declarations.

## 13. Implementation-Plan Readiness

**Ready.** All six review corrections are normatively specified with
enforcement points (§8); schemas are minimal and carrier-decided;
vocabularies are frozen and bounded; postcondition evaluators are
enumerated; the three Issue lineages serve as acceptance baselines
(alongside the AL-3.3 regression subset and S11×N under the G1-style
flag-OFF-identity + targeted-probe discipline). Recommended first slice
(composer + completion + postconditions + declarations), second slice
(planner coverage + re-plan filter) — per §9.
