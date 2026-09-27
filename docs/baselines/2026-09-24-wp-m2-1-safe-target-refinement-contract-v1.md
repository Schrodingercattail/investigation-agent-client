# WP-M2-1 Contract — Safe Target Refinement (Candidate Separability → Admissibility)

**Status**: **CONTRACT DESIGN COMPLETE** — architecture/contract document.
**Implementation NOT STARTED**; nothing in this document is implemented,
and no code, prompt, threshold, or schema was changed to produce it.
**Date**: 2026-09-24
**Parent**: `2026-09-21-target-goal-refinement-and-m2-rescope-v1.md`
(WP-M2-1; §5 R7; §8 amendment gating WP-M2-1 on SP-M2-1C)
**Evidence inputs**:
`2026-09-24-sp-m2-1c-candidate-separability-v1.md` (Discovery Complete,
Outcome A candidate — the empirical basis of every clause below) ·
`docs/qa/2026-09-24-sp-m2-1-final-independent-qa-v1.md` ·
`docs/qa/2026-09-24-sp-m2-1r-developer-validation-v1.md` ·
`2026-09-21-sp-m2-1-target-goal-refinement-spike-v1.md` (Final Discovery
Status: Current Contract Rejected)
**Frozen inputs preserved by this contract**: the refinement remains a
bounded, candidate-restricted, single-attempt, no-tool, no-recursion
mechanism; candidate-ID enforcement and safe degradation carry forward
unchanged. What this contract REPLACES is the rejected Safe Select
Policy ("confidence ≥ 0.7 → SELECT").

---

## 1. Purpose

Formally answer, as an implementable contract:

> When multiple plausible target candidates exist, under what conditions
> does the system allow an LLM's SELECT proposal to take effect — and
> when must the system require CLARIFY?

The design principle, carried from every evidence round:

> **The LLM may propose; only the system may admit.**
> LLM proposals produce no behavior until a system-controlled
> admissibility evaluation accepts them.

This document defines WHAT must be true for SELECT — not HOW to compute
it. Wherever an implementation mechanism is undetermined, it is recorded
as an **open implementation question** (§18), not silently chosen.

## 2. Evidence / Inputs (accepted, not re-litigated)

From SP-M2-1C (60 frozen runs, 12 pre-annotated cases, offline margin
probe, runtime Variable-G series — sample-level observations):

1. Goal-aware refinement has real value on distinguishable
   multi-candidate requests (stable correct SELECTs: R-C/R-H/FX-1/FX-3).
2. Confidence alone cannot safely decide SELECT: correct (0.72–0.98),
   unsafe (0.72–0.88), and wrong-target (0.78–0.88) selections fully
   overlap.
3. Unsafe selection was observed (12/60 runs; e.g. R-E/R-G 10/10 in the
   mid-specificity band).
4. Goal specificity has explanatory power but is not sufficient (its
   middle band was the most dangerous).
5. Candidate semantic overlap amplifies risk (R-A stable-wrong vs FX-3
   stable-correct on the same request after overlap reduction).
6. Discriminating evidence is the more critical signal: all four
   counterexample classes are explained by evidence structure, not
   scores.
7. Candidate-set stability is a precondition for any refinement-stability
   claim (upstream self-resolution observed 2/5 in one session, 0/9 in
   another).
8. Candidate preference ≠ candidate separability (R-A: 5/5 confident
   selection of the contender while cross-examined support was
   non-unique, margin 0.1).
9. Confidence ≠ candidate separability.
10. candidate_count > 1 ≠ user-intent ambiguity.
11. Genuine ambiguity must CLARIFY — it must not SELECT because the
    model slightly prefers a candidate.
12. The candidate boundary (§10 here) was formed from that evidence:
    stable set + unique support + discriminating evidence + acceptable
    confidence, otherwise CLARIFY.

## 3. Non-goals

No production code, tests, prompt, threshold, `target_refinement.py`,
ContextResolver, GoalRepresentation, Planner, Agent Loop, frontend, new
capability/tool/regex, MCP, memory, multi-agent, or architecture-doc
sync changes are made by this contract. No scoring formula, threshold
value, embedding rule, or number of LLM calls is fixed here (§18, §21).
Downstream runtime-semantics defects (clarification→FAILED,
INSUFFICIENT_DATA→SUCCESS) are recorded as dependencies, not solved.

## 4. Terminology

- **Request evidence** — information the user actually expressed.
- **Candidate semantics** — a candidate's authoritative title and
  description as supplied by initial Target Resolution.
- **External evidence** — Risk Platform case evidence beyond candidate
  descriptions. **Out of scope for refinement** (the refinement input
  surface remains request + goal projection + candidate descriptions;
  widening it would break the frozen bounded-input property).
- **Supported** — a candidate is supported when the request evidence,
  evaluated against candidate semantics, supports that candidate's core.
- **Uniquely supported** — exactly one candidate is Supported and the
  others are Not Supported under the same evidence. A set-level logical
  predicate, never a per-candidate score.
- **Admissible** — a system verdict: a SELECT proposal that satisfies
  every admissibility condition. Inadmissible proposals never select.

## 5. The Five Signals — contract table

| Signal | Definition | Inputs | Producer | LLM Role | System Role | Observable? | Failure Mode | Current Status |
|---|---|---|---|---|---|---|---|---|
| 1. Candidate stability | the authoritative candidate set was captured once post-resolution and used unchanged through goal→refinement→admissibility; cross-run variation is recorded, not silently absorbed | resolution outcome (IDs, count, order if semantic, description hash) | **System** | none | capture, freeze, compare, record | Partial — `candidates_considered` exists on the clarify path only | upstream self-resolution bypasses refinement (E5 evidence) and gets misattributed | **Partial** — implicit single-turn freeze only; no explicit snapshot; select-path count missing |
| 2. Discriminating evidence | request evidence maps to exactly one candidate's core semantics and not equivalently to competitors | request text + candidate semantics (§7) | **LLM proposes, system validates** (grounding + discrimination) | propose evidence mapping | validate grounded in supplied texts; validate non-equivalence | to be (new evidence record) | ungrounded rationale ("model feels…") treated as evidence — the rejected contract's core defect | **Missing** — no evidence representation exists |
| 3. Unique support | set-level predicate: exactly one candidate Supported, others Not Supported | validated per-candidate support judgments | **LLM proposes per-candidate support; system computes uniqueness as a logical rule** | judge each candidate's support | compute the set predicate; A=Supported ∧ B=Supported ⇒ NOT unique, no exception | to be (new) | model declares uniqueness by assertion (SP-M2-1C R-A) | **Missing** |
| 4. Confidence | LLM self-reported confidence for the proposal — a NECESSARY supporting signal, never the decision | frozen refinement output | **LLM**, clamped by system | reports | clamps; applies the threshold as a necessary filter; never lets it bypass 1–3/5 | yes (existing) | treated as sufficient — the rejected contract | **Existing** (source: self-reported; calibration Unknown) |
| 5. Final admissibility | the system verdict admitting or rejecting a SELECT proposal | signals 1–4 + proposal target identity | **System only** | none | evaluate the logical condition (§10); emit outcome + reason code | to be (extend `target_resolution`) | any bypass route re-opens the rejected contract's failure | **Missing** — current gate is flag+resolver-presence only |

## 6. Candidate Stability Contract

**Definition.** A refinement evaluation is **STABLE** when: (a) the
candidate set C was captured exactly once at the end of initial Target
Resolution; (b) goal understanding, refinement proposal, and
admissibility all consumed that same C (same IDs, same count, same
description content); (c) the upstream path taken is recorded
(deterministic / LLM-assisted single target / retained-candidates). A
comparison across runs is STABLE only if IDs, count, description
content, and upstream path all match; anything else is UNSTABLE and is
**attributed to upstream variance, never to refinement variance**.

**Contract clauses.**
- CS-1: the candidate set is captured and frozen before goal
  understanding begins ("snapshot before consumption").
- CS-2: refinement never regenerates, re-queries, or widens candidates.
- CS-3: the upstream path (self-resolution vs refinement entry) is
  recorded so the two variance sources are separable (AC9).
- CS-4: stability is an input precondition to admissibility — an
  UNSTABLE set cannot yield an admissible SELECT in that evaluation.
- CS-5 (measurement): stability across runs is a QA instrument, not an
  LLM signal.

**Responsibility**: system-only. The LLM never sees or judges
stability. Rationale: SP-M2-1C Variable G — upstream self-resolution
(2/5 in one session, 0/9 in another) must not be booked as refinement
behavior.

**Implementation dependency**: explicit candidate snapshot artifact +
select-path `candidates_considered` (see §17 item 1). Not implemented.

## 7. Discriminating Evidence Contract

**Definition.** Discriminating evidence exists when the request
evidence contains information that supports exactly one candidate's
core semantics **and does not equivalently support the competing
candidates**. All three information layers are distinguished:

- **Request evidence** — the only evidence this contract admits.
  ("共享了设备" is request evidence; the model's preference is not.)
- **Candidate semantics** — the passive yardstick: candidates' own
  titles/descriptions define what would count as mapping to their core.
- **External evidence** — excluded from refinement (frozen bounded
  input; also prevents "the tool found X" from laundering into target
  selection).

**Evidence record (contractual shape, not implementation).** For a
proposal to be evaluable, the proposer must emit, per candidate: the
request fragment(s) it relies on, the candidate core term(s) those
fragments map to, and a support judgment (Supported / Not Supported /
Unknown). "Evidence strength" is expressed at the logical level
(SUFFICIENT / INSUFFICIENT / UNKNOWN) — **no numeric evidence score is
mandated**. Whether the mapping is checked by deterministic matching,
model judgment, or pairwise comparison is an **open implementation
question** (§18).

**LLM proposes, system validates.** The system must reject an evidence
proposal that is (a) ungrounded — cites fragments absent from the
request or semantics not present in the supplied descriptions; or
(b) non-discriminating — the cited mapping applies to more than one
candidate. Validation method: open implementation question.

**Failure mode addressed.** The rejected contract allowed "the model
finds this candidate more plausible" to masquerade as evidence
(SP-M2-1C R-A: contender selected 5/5 at 0.78–0.88 while cross-examined
support was non-unique, margin 0.1). Under this contract that
observation becomes: support proposals for both candidates → Not
Uniquely Supported → inadmissible.

## 8. Unique Support Contract

**Logical hierarchy** (per candidate, then per set):

```
Per candidate:  Supported | Not Supported | Unknown
Per set:        UNIQUELY_SUPPORTED      — exactly one Supported, ≥1 other Not Supported
                NOT_UNIQUELY_SUPPORTED  — two or more Supported, OR zero Supported
                UNKNOWN                 — any candidate Unknown and uniqueness undecidable
```

**Contract clauses.**
- US-1: uniqueness is a **set predicate computed by the system** over
  validated per-candidate support — the LLM cannot assert it into
  existence.
- US-2: A Supported ∧ B Supported ⇒ **NOT** Uniquely Supported. No
  confidence value, margin, or phrasing promotes a preference to
  uniqueness. (Direct codification of SP-M2-1C finding 8.)
- US-3: Unknown is treated as NOT_UNIQUELY_SUPPORTED for admissibility
  (an undecidable set must CLARIFY, never select) and is recorded
  distinctly for telemetry.
- US-4: the uniquely-supported candidate must be the proposed target;
  a proposal for any other candidate is inadmissible.

## 9. Confidence Contract

- **Source**: LLM self-reported (frozen behavior), system-clamped.
  Calibration status: Unknown — recorded as an open question.
- **Role**: a NECESSARY supporting signal. The threshold (current 0.7)
  is retained as a **necessary filter only**.
- **Explicit non-bypass clauses** (the rejected policy's inversion):
  - confidence ≥ threshold does NOT bypass unstable candidate sets
    (CS-4), insufficient discriminating evidence (§7), non-unique
    support (US-2/US-3), or identity invalidity.
  - confidence < threshold ⇒ SELECT not admissible, regardless of
    evidence quality (AC5: low confidence + strong evidence → CLARIFY
    under this contract; recorded as deliberately conservative — the
    missed-SELECT cost observed in SP-M2-1C was 4/60 runs + 1 fallback,
    vs 12/60 unsafe selections).
- Threshold VALUE and any calibration remain implementation parameters,
  not contract content.

## 10. Final Admissibility Contract

The core clause. After the LLM returns `SELECT(target=X, confidence=c,
evidence=…)`:

```
SELECT is ADMITTED if and only if ALL of:
  A1  Candidate set STABLE (CS-4)
  A2  Discriminating evidence SUFFICIENT and validated (grounded + non-equivalent)
  A3  Set predicate = UNIQUELY_SUPPORTED, and X is that candidate (US-1/US-4)
  A4  confidence ≥ threshold (necessary filter only)
Otherwise the proposal is REJECTED and the turn CLARIFIES.
```

Outcome disambiguation:

| Situation | Outcome | Reason class |
|---|---|---|
| All A1–A4 hold | SELECT admitted → existing resolved path | `admitted` |
| Two+ candidates Supported, or zero | CLARIFY (normal bounded outcome) | `not_uniquely_supported` |
| Evidence insufficient / ungrounded / non-discriminating | CLARIFY | `insufficient_evidence` |
| Candidate set unstable | CLARIFY + upstream-variance record | `unstable_candidates` |
| confidence < threshold | CLARIFY | `low_confidence` |
| Proposal malformed / unusable | CLARIFY via safe fallback (existing frozen degradation) | `fallback` |
| Infrastructure error (endpoint etc.) | bounded CLARIFY + error telemetry — a **system error**, never booked as a semantic CLARIFY | `system_error` |
| Fabricated / out-of-set target id | rejected (existing frozen enforcement), CLARIFY | `invalid_target` |

**CLARIFY must** happen (normative) for: not-uniquely-supported,
insufficient evidence, unstable set, low confidence, invalid target,
fallback. **Safe fallback** is the frozen unusable-output path — counted
separately from semantic CLARIFY in all telemetry (the
`target_resolution.reason` distinction already enables this).
**System error** and **insufficient evidence** are recorded as distinct
reason classes so clarification-rate metrics can exclude them.

## 11. LLM vs System Responsibility

| Decision / Signal | LLM | System | Both |
|---|---|---|---|
| Goal understanding | proposes (existing Stage 2.1) | validates vocabulary/boundedness | ✓ |
| Candidate interpretation | proposes support per candidate | — | |
| Discriminating evidence proposal | proposes mapping | validates grounding + discrimination | ✓ |
| Candidate preference | may hold one — carries NO authority | strips it from the decision | |
| Confidence | reports | clamps; applies threshold filter | ✓ |
| Candidate stability | — | captures, freezes, judges | |
| Unique support | — | computes set predicate | |
| Final SELECT admissibility | — | **decides** | |
| CLARIFY enforcement | — | **decides and emits** | |
| Candidate ID validity | constrained (frozen) | enforces (existing) | |

Boundary model (§12 of the brief): **Semantic Evaluation** — what the
user means, which candidate reads as relevant, what the goal is — is
LLM work (Target Resolution assist, Goal Understanding, refinement
proposal). **Control Evaluation** — whether a judgment may produce
behavior — is system work (candidate scope, identity, stability,
uniqueness predicate, admissibility, final action state). Placed on the
pipeline: Target Resolution = semantic (system-bounded); Goal
Understanding = semantic; Candidate Refinement = semantic proposal;
Safe Select = control. The LLM proposes a target; the system decides
whether a target exists.

## 12. Runtime Result Model

Reusing existing terminology where it exists (ResolutionStatus,
resolution_kind, task states):

| Contract state | Maps to today | Meaning |
|---|---|---|
| FAST_PATH_RESOLVED | RESOLVED (deterministic/LLM-assisted, no refinement) | target resolved without refinement |
| CANDIDATES_READY | AMBIGUOUS + retained candidates (pre-refinement) | set captured & frozen; refinement may run |
| REFINEMENT_SELECT_PROPOSED | **NEW** | LLM proposed; admissibility pending |
| SELECT_ADMITTED | RESOLVED / kind=inferred (via refinement) | proposal passed A1–A4 |
| SELECT_REJECTED | **NEW** (reason class recorded) | proposal exists, inadmissible → CLARIFY |
| CLARIFICATION_REQUIRED | clarification turn | normal bounded outcome (§14) |
| FALLBACK | unusable-output degradation | counted separately from semantic CLARIFY |
| ERROR | error telemetry | system error, never a semantic outcome |

**Non-equivalence entering the contract verbatim**:
`REFINEMENT_SELECT_PROPOSED ≠ SELECT_ADMITTED`. No UI, log, or metric
may report a proposal as a resolution.

## 13. Clarification Contract (future implementation contract)

Clarification is a **normal bounded conversational outcome**, not an
execution failure. Contract:

- **State**: non-terminal for the investigation; the turn ends, the
  conversation continues.
- **Reason code**: one of the §10 classes (user-safe projection only).
- **Preserved candidates**: the frozen candidate set is surfaced.
- **Preserved goal**: the goal projection is retained so the next turn
  can reuse it (today it is discarded — observed gap).
- **Resumability**: the next turn may resolve by user selection, exact
  restatement, or a refined request; no hidden state is required.
- **Explicitly recorded non-equivalence**: `clarification_needed ≠ task
  FAILED` — known downstream runtime-semantics defect (observed in
  every QA round). Fixing it is a downstream implementation dependency,
  out of WP-M2-1's contract scope here.

## 14. Insufficient Data Boundary

`INSUFFICIENT_DATA ≠ SUCCESS`. When a tool returns insufficient data,
the Target Resolution / Goal Understanding stages are not thereby
"successful", and task completion must not be reported as goal-aligned
success. Recorded as a **known defect / downstream implementation
dependency** (B12 / WP-M2-5 territory; re-observed in the final
SP-M2-1 QA E5 r3/r4). No runtime change in this contract.

## 15. Candidate Snapshot / Upstream Boundary

Contract requirement: `Initial Resolution → Candidate Set C frozen →
Goal Understanding → Refinement Proposal → Admissibility` — never
regeneration of candidates between these stages. Current code check:
within one turn, §1b builds the candidate list inline at refinement
time and nothing regenerates it, so a single turn is *de facto* frozen;
however there is **no explicit snapshot artifact, no description-content
hash, no cross-run stability record, and the select path does not
record the candidate count**. ⇒ **Implementation dependency**: "stable
candidate snapshot must be captured, persisted on the evidence record,
and reused" — status Partial (implicit), to be made explicit. Not
implemented.

## 16. Acceptance Contract

Every case: pre-registered annotation first (SP-M2-1C method), frozen
behavior observed after. Layer mapping (§19): **Component** = contract
logic with fixtures · **Integration** = real `/turn` + evidence fields ·
**Browser** = real journeys (acceptance authority) · **Independent QA**
= the only layer that can CLOSE Safe Select risk. Contract tests passing
never alone prove Safe Select valid.

| AC | Case | Expected | Primary layer |
|---|---|---|---|
| AC1 | single candidate | fast path, no refinement | integration |
| AC2 | multi-candidate + strong discriminating evidence | proposal → admitted → correct target | browser |
| AC3 | multi-candidate + genuine ambiguity | CLARIFY, no forced target | browser |
| AC4 | high confidence + no unique support | CLARIFY even at confidence ≥ threshold | component + browser |
| AC5 | low confidence + strong evidence | NOT admitted (threshold is a necessary gate) → CLARIFY; recorded as conservative | component |
| AC6 | malformed / unsupported target | safe fallback, no fabricated target | component (existing) |
| AC7 | unstable candidate set | inadmissible; result attributed to upstream variance, per CS policy | integration |
| AC8 | exact title | refinement bypassed, OFF==ON | integration + browser (existing) |
| AC9 | upstream resolution variance | upstream vs refinement variance distinguishable in evidence | integration |
| AC10 | clarification | not an execution failure; reason class + preserved candidates/goal | integration (dependency: semantics fix) |
| AC11 | insufficient data | not successful completion | downstream dependency (WP-M2-5) |

## 17. Implementation Dependencies

| # | Dependency | Status | Note |
|---|---|---|---|
| 1 | Candidate snapshot / stability record | **Partial** | implicit within-turn; no artifact, no content hash; select-path count missing |
| 2 | Discriminating-evidence representation (evidence record + validation) | **Missing** | new schema + validation method (open question) |
| 3 | Unique-support representation (set predicate) | **Missing** | pure logic once #2 exists |
| 4 | Confidence source | **Existing** (self-reported; calibration Unknown) | reused as necessary filter |
| 5 | Final admissibility gate | **Missing** | replaces the flag+presence-only gate; system-only |
| 6 | Clarification state (reason class, preserved goal, non-failed semantics) | **Partial / Missing** | terminal-semantics fix is a downstream defect |
| 7 | Telemetry / evidence (`target_resolution` extension: states, reason classes, select-path count) | **Partial** | field exists; asymmetry + new states needed |
| 8 | Upstream target-resolution consistency recording | **Missing** | CS-3 path attribution |

## 18. Open Implementation Questions

1. Evidence-validation method: deterministic matching, model-judged
   grounding check, pairwise comparison, or structured rationale —
   undecided.
2. Whether unique-support evaluation needs its own bounded call or can
   ride the proposal call — undecided (call count is deliberately NOT
   fixed by this contract).
3. Confidence calibration (is self-reported confidence monotone in
   anything?) — Unknown; threshold value stays an implementation
   parameter.
4. Evidence-strength representation details (logical levels here;
   numeric scoring neither mandated nor banned).
5. Description-hash stability semantics (exact match vs normalized).

## 19. WP-M2-1 Work Breakdown (re-estimate per this contract)

| # | Work item | Scope | Dependency | Impl effort | Validation effort | Discovery risk | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | Contract implementation: result states, evidence schema, reason classes | §12/§17 schema; frozen inputs untouched | this contract | 1–2 d | component tests | Low (known implementation) | High |
| 2 | Signal production: evidence proposal + system validation + unique-support predicate | §7/§8; **open questions 1–2 live here** | #1 | 2–4 d | 1–2 d | **High — the calibration/validation-method boundary is the residual discovery item** | Medium |
| 3 | Admissibility gate in the service (replaces flag+presence gate; flag semantics retained) | §10 | #1, #2 | 0.5–1 d | component | Low | High |
| 4 | Integration: `/turn` evidence fields, snapshot capture, select-path count, upstream-path record | §17 items 1/7/8 | #3 | 1–2 d | integration tests | Low–Medium | High |
| 5 | Evaluation: re-run the annotated SP-M2-1C matrix against the implemented contract (≥5 runs/case, stability controls, fixtures labeled) | §16 | #4 | — | 1–2 d | Medium (endpoint fragility pollutes samples) | Medium |
| 6 | Browser acceptance: AC2/AC3/AC4/AC8 journeys on U00010 | §16, §19 | #5 | — | 1 d | Low | High |
| 7 | Independent QA (separate phase, closes Safe Select risk) | per repo quality gate | #6 | excluded | excluded | — | — |

No dates beyond ranges; ranges are estimates, not schedules.

## 20. Effort / Confidence / Risk Summary

Implementation ≈ 4.5–9 d + validation ≈ 3–5 d (excluding independent
QA), dominated by work item 2 — the only item with genuine discovery
risk: how strictly evidence validation can separate "grounded and
discriminating" from "plausible-sounding" without tipping into
over-refusal (the missed-SELECT side is small in-sample: 4/60 + 1
fallback, but unmeasured at scale). Everything else is known
engineering on a proven-wired runtime.

## 21. M2 Dependency

WP-M2-1 remains M2's upstream architecture gate. Capability Metadata,
Suitability, Gap, and Composition remain unstarted. This contract is
the WP-M2-1 entry artifact; implementation begins only on PMO approval
of it.

## 22. Decision Status

- **SP-M2-1C** → Discovery Complete / Result A (baseline doc).
- **WP-M2-1** → **Contract Design Complete; Implementation NOT
  STARTED.**
- **M2** → the Safe Target Refinement implementation gate is now
  defined (this document), subject to implementation + independent QA.
  Nothing here claims M2 complete, Safe Select implemented/validated,
  or production-readiness.

---

## Appendix A — The Twelve Contract Questions (§26), answered

1. **Candidate stability?** A system-owned precondition: the candidate
   set captured once post-resolution, consumed unchanged (IDs, count,
   content) through goal→proposal→admissibility, with the upstream path
   recorded; cross-run differences are upstream variance, never
   refinement variance.
2. **Discriminating evidence?** Request information that supports one
   candidate's core semantics without equivalently supporting
   competitors — proposed by the LLM as a grounded mapping, validated
   by the system. Model preference is not evidence.
3. **Unique support?** A system-computed set predicate: exactly one
   candidate Supported, others Not Supported. A∧B Supported ⇒ not
   unique; Unknown ⇒ clarify.
4. **Where does confidence act?** As a necessary filter (below
   threshold → inadmissible) and nothing more; it can never bypass
   stability, evidence, or uniqueness.
5. **Who decides final admissibility?** The system, alone, via the
   A1–A4 condition over the five signals.
6. **What may the LLM decide?** Understanding, interpretation, per-
   candidate support proposals, evidence mapping, preference
   (powerless), confidence (self-reported), explanation.
7. **What must the system decide?** Candidate scope and identity,
   stability, uniqueness predicate, admissibility, CLARIFY enforcement,
   final action state.
8. **When must the system CLARIFY?** Not-uniquely-supported,
   insufficient/ungrounded evidence, unstable set, below-threshold
   confidence, invalid target, unusable proposal, system error — every
   non-admitted path clarifies; clarification is a normal outcome with
   a reason class.
9. **When is SELECT admissible?** Exactly A1–A4 (§10), with the
   proposed target equal to the uniquely-supported candidate.
10. **What signals does production lack today?** Evidence record,
    unique-support predicate, admissibility gate/state, candidate
    snapshot, upstream-path record, clarification reason/goal
    preservation, select-path candidate count (§17).
11. **What is implementation choice, undecided?** Evidence-validation
    method, call structure, calibration/threshold values, evidence
    scoring representation, hash semantics (§18).
12. **Can this convert directly into code and QA?** The state model
    (§12), gate logic (§10), and AC1–AC11 (§16) are written to convert
    directly; the single deliberate gap is the §18 open questions,
    which WP-M2-1 work item 2 must close first.

## Appendix B — Architecture Responsibility Matrix (§23)

| Layer | Responsibility | LLM or System | Mutable by LLM? | Evidence |
|---|---|---|---|---|
| Context | case/focus state, candidate capture | System | no | InvestigationContext, snapshot (to be) |
| Goal | goal understanding | LLM (system-bounded vocabulary) | proposal only | GoalRepresentation (existing) |
| Candidate | set identity, scope, count | System | no | frozen C (to be explicit) |
| Refinement | bounded proposal | LLM (frozen module semantics) | proposal only | RefinementDecision (existing) |
| Separability | evidence validation + unique-support predicate | System over LLM proposals | no | evidence record (to be) |
| Admissibility | A1–A4 verdict, reason class | System | no | target_resolution extension (to be) |
| Clarification | normal bounded outcome, preserved candidates/goal | System | no | turn response (extends existing) |
| Execution | plan/tool run, completion semantics | System (planner/executor) | no | existing task/execution records |
