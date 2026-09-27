# WP-M2-1 Item 2 — Evidence Validation Discovery (Discriminating Evidence → Unique Support)

**Status**: **DISCOVERY COMPLETE — Decision B** (evidence is definable
and its safety direction is validated in-sample; the
sufficiency/non-equivalence validation mechanism requires one more
bounded discovery before implementation)
**Date**: 2026-09-24
**Type**: controlled architecture/implementation discovery (offline; NO
production code, prompt, threshold, or schema changes)
**Parent**: `2026-09-24-wp-m2-1-safe-target-refinement-contract-v1.md`
(§19 work item 2 — the contract's designated residual discovery item)
**Evidence inputs**: `2026-09-24-sp-m2-1c-candidate-separability-v1.md`
· SP-M2-1 final discovery result · the two SP-M2-1 QA/validation docs
**Experiment artifacts**: `/tmp/spm21c2/cases_item2.json`
(pre-registered support-level ground truth),
`results_approaches.json`, `results_retry.json`. All numbers are
**observations in this experiment sample**.

---

## 1. Objective

Find an implementable, verifiable, auditable **Discriminating Evidence
Validation Mechanism**: what request information may be formally
represented as discriminating evidence, how the system validates it,
and how Unique Support is computed safely from it.

## 2. Contract Inputs

The WP-M2-1 contract's five signals and admissibility condition
(§10 there) are taken as fixed: this discovery designs the
evidence→unique-support layer only. Confidence remains a necessary
filter; candidate-ID enforcement and single-attempt bounds are frozen.

## 3. Evidence Definition

- **Request evidence**: what the user verbatimly expressed
  (fragments of the request text). User-stated information is
  distinguishable from LLM-inferred meaning — and only the former may
  ground evidence.
- **Candidate semantic evidence**: the candidate's own title and
  description (the yardstick a request fragment is mapped against).
- **External case evidence** (RP transactions/devices/networks):
  **excluded** from the Safe Target Refinement evidence surface. It
  would widen the frozen bounded input, cannot be validated from what
  the refinement sees, and would let tool-side data launder target
  selection. Recorded as the contract answer: **option A
  (request ↔ candidate semantics) only.**

## 4. Evidence Taxonomy (levels used in experiments)

| Level | Definition | Example | Counterexample | How an evaluator verifies |
|---|---|---|---|---|
| E0 none | request carries no candidate-mapping information | “有什么需要关注的吗？” | a vague request plus an eager model | no fragment maps to any candidate core |
| E1 relevant, non-discriminating | fragments map to ≥2 candidates' cores | “关联情况” → F5 and F6 | mistaking relevance for uniqueness | count mapped candidates: >1 ⇒ E1 |
| E2 weak discriminating | one candidate favored but a competitor's semantics still contain the signal | R-F run where F6's "through shared devices" grounded support | accepting the mapping without checking the competitor | competitor-description check |
| E3 strong discriminating | fragment maps to one candidate's core AND no competitor's description contains an equivalent signal | R-C "Linked Account Network" verbatim; FX-3 after device-mention removal | FX-1-style toy cases that only prove lexical match | mapping + non-equivalence check |

E-level is evidence strength; SELECT still requires the full §10
admissibility condition.

## 5. Evidence Provenance

Provenance classes observed: `USER_REQUEST` (fragments),
`CANDIDATE_METADATA` (core concepts), `LLM_INTERPRETATION`
(the mapping/judgment), `SYSTEM_VALIDATION` (grounding + set logic),
`GOAL_REPRESENTATION` (context only — no support may rest on it alone),
`CASE_EVIDENCE` (excluded, §3). **Rule validated by experiment**: an
evidence item whose only provenance is `LLM_INTERPRETATION` must never
enter unique support; it must carry a `USER_REQUEST` fragment AND a
`CANDIDATE_METADATA` concept that the system can independently check.

## 6. Candidate Semantics (live inventory — decisive)

Live U00010 metadata (verified same day): **F5 and F6 carry IDENTICAL
structured fields** — `type=graph_signal`, `signal_refs=[shared_device_count,
linked_account_count]`, same capabilities, same policy_refs count. F8
differs (`rule_signal`, no signal_refs, 5 evidence_refs). The ONLY
fields distinguishing F5 from F6 are title and description.
⇒ Deterministic validation over *structured* metadata cannot separate
F5 from F6 today; free-text title/description is the only signal
carrier. This makes a **candidate semantic-metadata contract**
(core concepts per candidate) a prerequisite for any deterministic
evidence mechanism.

## 7. Discrimination vs Similarity

Similarity is symmetric co-occurrence; discrimination is asymmetric
support. Demonstrated: R-A — “共享设备” appears verbatim in F6's
description (similarity high) yet supports F6 only as derivation
mechanism; both-supported ⇒ non-unique. The naive lexical matcher
(ANY token overlap) matched everything to nothing usable and, across
CJK requests vs English metadata, matched **nothing at all** (blind,
not merely noisy). Similarity ≠ discrimination, both directions.

## 8. Discrimination vs Preference

The frozen refinement's 5/5 SELECT F6 on R-A (SP-M2-1C) is preference
behavior; under support-level ground truth (F5=TRUE, F6=TRUE) the
correct action is CLARIFY. Approach B's structured proposal + set logic
returned F5=TRUE/F6=TRUE → CLARIFY on 3/3 valid runs: **the preference
was stripped out and safety emerged**. A model saying "F6 is more
likely" is not evidence; only a fragment→core mapping that fails
equivalence for competitors counts.

## 9. Support vs Unique Support

Support is per-candidate (TRUE/FALSE/UNKNOWN); unique support is the
set predicate. Validated logic (in-sample): exactly-one-TRUE + ≥1 FALSE
⇒ UNIQUE (R-C, R-D, R-F*, R-H, FX-1, FX-3, T7); any two TRUE ⇒ NOT
unique (R-A, R-B, R-E, A2, B2b, FX-2, FX-4); any UNKNOWN ⇒ not
admissible (R-G, T6). (*R-F: see §16 — the one support-level mismatch.)

## 10. Non-Equivalence

Proving “B is NOT supported” proved harder than proving “A is
supported” — exactly as anticipated. Substring grounding accepts
mechanism-mentions as support (F6's description literally contains
"shared devices"), so grounding cannot reject a competitor whose
description merely mentions the signal. Two mechanisms compensate:
(a) the set predicate (both TRUE ⇒ CLARIFY — the safe direction, cost:
occasional missed SELECT as on R-F); (b) semantic equivalence judgment
(a judge that asks "is the competitor's support equivalent?").
Mechanism (b) is precisely the part that could not be validated at
scale in this sample (endpoint failures) — the residual gap, Decision B.

## 11. Ground Truth Method

Support-level ground truth pre-registered before any approach run
(`cases_item2.json`), including two honest re-interpretations decided
up front: R-A → both-supported (non-unique) under live metadata, and
R-G → F8=UNKNOWN (its own description denies anomaly; "only trading
candidate" is exhaustiveness preference). 16 cases: 8 real RP
(pre-registered in SP-M2-1C, now with per-candidate support) + 8
labeled controlled fixtures (A2 goal-sharpened probe; B2b
overlap-maximized; T6 metadata-insufficient; T7 N=3 generalization;
FX-1..4 carried over).

## 12. Real RP Cases

R-A..R-H on live U00010 (see §6 inventory and the table in §16).

## 13. Controlled Fixtures

A2, B2b, T6, T7 (new) + FX-1..FX-4 (carried) — all labeled
CONTROLLED FIXTURE; none reported as real-RP evidence.

## 14. Experimental Matrix

Four validation approaches × 16 cases: **A** deterministic core-concept
matching (bilingual term table declared in GT — best-case test of the
metadata-contract idea; plus a NAIVE lexical variant) · **B** LLM
structured evidence proposal + deterministic grounding validation +
set logic · **C** two-stage LLM (independent judge) · **E** pairwise
candidate comparison. LLM approaches: 3 runs/case (B) and presence-
check samples (C/E) under a degrading endpoint. Option D
(embeddings): **not testable in this environment** — no embedding
infrastructure exists in the repo; recorded, not assumed.

## 15. Pairwise Experiments (§16 of the brief)

- **Exp A (goal variation, {F5,F6})**: R-B (“关联情况”) and A2
  (“共享设备的关联账户”) both → both-supported → CLARIFY. Sharpening the
  goal did NOT create discrimination under live metadata: the compound
  phrase still names F5's core and F6's derivation+enumeration. The
  intuition "obviously F6" is answer-shape preference.
- **Exp B (semantics variation, same request)**: R-A (F6 mentions
  devices) non-unique vs FX-3 (device mention removed) uniquely FIX-D —
  candidate semantics, not the request, decided separability.
  B2b (competitor collapsed to device-sharing only) → non-unique →
  CLARIFY: when both candidates' semantics coincide, discrimination is
  impossible and must not be manufactured.
- **Exp C (overlap)**: subsumed by B — overlap is the competitor-side
  evidence condition.
- **Exp D (confidence vs evidence)**: reused SP-M2-1C bands (correct
  0.72–0.98 vs unsafe 0.72–0.88, overlapping) — and in this discovery,
  approach B reached the safe verdict on every valid real-case run
  *without consulting confidence at all*.

## 16. Observations (valid runs only; endpoint failures counted)

Endpoint degradation was severe: ~40% of LLM calls in the main pass
and most of the retry returned "no text block"; several cases (R-C,
R-D, R-G) produced **zero valid runs for every LLM approach** and are
NOT TESTED at support level.

- **Approach B** (valid real-case runs): R-A 3/3 correct CLARIFY —
  the case the frozen contract failed 5/5; R-B 2/2 ✓; R-E 2/2 ✓; A2
  2/2 ✓. **R-F 1 valid run: support mismatch** — F6 grounded via its
  verbatim "shared devices" mechanism-mention → both-supported →
  CLARIFY where GT expects SELECT F5 (a false-negative support, failing
  SAFE: over-cautious, not unsafe). Fixtures: FX-1 3/3, FX-3 3/3, T7
  3/3 unique-correct; FX-2/FX-4/B2b CLARIFY-correct; **T6 1 unsafe
  unique** (generic "anomalous behavior" grounded → false-positive
  evidence; GT UNKNOWN).
- **Approach A-core**: correct on all real cases where the bilingual
  term table was declared (R-C/R-D/R-F/R-H unique-correct; R-A/R-B/R-E
  non-unique) — **by construction**: it inherits the annotator's
  concept table. Its real cost: tables must exist per candidate and per
  language; today's production carries no such metadata (§6), and the
  naive lexical variant (no table) was totally blind on CJK→English.
- **Approach C**: valid only on FX-3 (3/3 ✓), T7 (1/1 ✓), R-H (1/1 ✓)
  — every valid sample matched GT, but the sample is too thin to
  support stability claims.
- **Approach E**: **zero valid runs — NOT TESTED** (endpoint).
- Frozen-refinement baseline (preference, for contrast): R-A 5/5
  SELECT F6 0.78–0.88; R-G 5/5 SELECT F8 up to 0.88; R-E 5/5 SELECT F6
  — all unsafe under support-level GT.

## 17. Counterexamples

1. **Grounding-passes-but-insufficient (FP evidence)**: T6 —
   “对公转账有异常” grounded onto generic "anomalous behavior" →
   unique=TRUE, but GT UNKNOWN (metadata cannot establish
   transfer-specificity). Grounding ≠ sufficiency.
2. **Grounding-forces-over-caution (FN evidence)**: R-F — F6's
   mechanism-mention grounds equally well → non-unique → CLARIFY where
   GT expects SELECT F5. Non-equivalence cannot be validated by
   substring grounding.
3. **Exhaustiveness-preference**: R-G — “only trading candidate” is a
   preference over an empty set, not support; F8's own description
   denies the anomaly (UNKNOWN → CLARIFY).
4. **Stable-wrong preference vs validated safety**: R-A frozen 5/5
   SELECT (conf 0.78–0.88) vs approach-B 3/3 CLARIFY on identical
   inputs — the same model, asked for *evidence structure* instead of a
   verdict, stops preferring.

## 18. Evidence Validation Options — comparison (observed, this sample)

| Criterion | A deterministic metadata | B proposal + deterministic validation | C two-stage LLM | D embeddings | E pairwise | F hybrid (B + core-concept tables) |
|---|---|---|---|---|---|---|
| Explainable/auditable | high | high (fragment+concept provenance) | medium | low | medium | high |
| Tested here | yes (real cases ✓ by construction; blind w/o table) | yes — **safest observed**; 1 FP (T6), 1 FN (R-F) | partial only (endpoint) | not testable | not tested | not run (combines tested parts) |
| Stability | deterministic | set logic stable; proposals vary but verdicts held where valid | unmeasured (endpoint) | — | — | — |
| Safety direction | fails closed w/ table; blind w/o | fails closed (both-TRUE ⇒ CLARIFY) | — | — | — | — |
| Complexity / latency | table maintenance | 1 call + cheap checks | 2 calls (2× failure surface — observed) | new infra | N−1…N² calls (observed fragility) | table + 1 call |
| Metadata dependency | **high — none exists today (§6)** | low | low | low | low | medium |
| Non-equivalence | only as good as the table | **not validated by grounding** (R-F) | promising but unproven here | unknown | designed for it | table-assisted |
| Suitability | prerequisite layer, not standalone | **leading candidate** | candidate judge layer | out of scope | judge-layer alternative | likely final shape |

## 19. LLM vs System Responsibility (validated division)

| Activity | LLM | System | Both |
|---|---|---|---|
| Extract request signal | ✓ (fragment proposal) | verifies verbatim grounding | ✓ |
| Normalize semantic concept | proposes | checks presence in candidate description | ✓ |
| Map signal → candidate | proposes | validates | ✓ |
| Identify competing candidate | n/a (system supplies the set) | owns | |
| State discriminating relationship | proposes | validates non-equivalence **(mechanism open — Decision B)** | ✓ |
| Validate candidate support | proposes | set predicate | ✓ |
| Compute unique support | — | **owns** | |
| Final SELECT admissibility | — | **owns** | |

## 20. Production Observability Gap

EXISTING: candidate IDs, titles, descriptions, type/severity,
signal_refs/policy_refs/evidence_refs/capabilities (F5/F6 structurally
identical), GoalRepresentation, LLM self-reported confidence,
`target_resolution` evidence field. PARTIAL: candidate snapshot (implicit
within-turn). MISSING: second-best score/ranking, evidence structure,
evidence provenance, validation status, unique-support record,
core-concept metadata, upstream-path attribution. UNKNOWN: confidence
calibration.

## 21. Candidate Evidence Representation (minimal research model)

```
EvidenceItem (research model — final schema is an implementation decision)
  REQUIRED:  source ∈ {USER_REQUEST, CANDIDATE_METADATA},
             request_fragment (verbatim), candidate_id,
             candidate_concept (verbatim from description),
             support ∈ {TRUE, FALSE, UNKNOWN},
             validation_status ∈ {grounded, ungrounded}
  DERIVED:   unique_support (set predicate over validated items)
  OPTIONAL:  relation_type, competitor_refs, strength (E0–E3)
  NOT NEEDED for safety: numeric evidence score
```

## 22. Unique Support Model

`UNIQUE ⟺ exactly one candidate Support=TRUE ∧ ≥1 other FALSE`;
any UNKNOWN (or zero TRUE) ⇒ NOT admissible → CLARIFY; the proposed
target must equal the unique candidate. Margin (SP-M2-1C's offline
probe) is **not required** by this logic — discrete validated support
sufficed in every valid run here; margin remains an auxiliary
model-scoring signal, not a safety gate (§20 of the brief answered:
yes-without-margin in-sample; margin = auxiliary).

## 23. Safe Select Input Model

```
CandidateSnapshot (frozen, stability-stamped)   [dependency: PARTIAL]
+ GoalRepresentation (existing)
+ EvidenceSet (validated EvidenceItems)          [dependency: to design — this discovery]
+ UniqueSupportResult (set predicate)            [dependency: pure logic]
+ Confidence (existing, necessary filter)
```
Nothing else is required; external case evidence is excluded (§3).

## 24. Open Implementation Questions

1. Non-equivalence/sufficiency validator: semantic equivalence judging
   (approach C showed promise; unproven at scale here) vs a candidate
   core-concept metadata contract (approach A's prerequisite — today
   F5/F6 metadata is identical) vs a hybrid (F). **This is the one
   remaining discovery item.**
2. Evidence-item schema finalization (§21 REQUIRED set is the floor).
3. Whether the proposal call can carry the evidence structure without
   prompt changes to the frozen refinement — likely NO (frozen module):
   the evidence proposal probably needs a NEW bounded call beside the
   frozen one, or a contract revision — recorded, not decided.
4. Endpoint fragility: multi-call validation doubles the failure
   surface (observed); degradation behavior must be part of the design
   (fail closed to CLARIFY/fallback).

## 25. Decision

**B — Discriminating Evidence can be defined (definition validated in
-sample, safety direction proven: zero unsafe SELECTs on valid
real-case runs under approach B, including on R-A where the frozen
contract failed 5/5), but the validation mechanism for
sufficiency/non-equivalence still needs one bounded discovery**
(options: semantic equivalence judging vs candidate core-concept
metadata contract vs hybrid; the T6 false-positive and R-F
false-negative are its exact test cases). Not A: the R-F/T6 failure
modes are structural, not noise. Not C: metadata is insufficient only
for the deterministic path — the B-path works without it. Not D: the
architecture demonstrably supports the safety direction.

## 26. WP-M2-1 Implementation Dependencies (updated)

1. Candidate snapshot/stability — PARTIAL (unchanged).
2. Evidence representation — designed (§21 floor); final schema pending
   question 1.
3. Unique-support predicate — designed (§22); pure logic, ready.
4. Confidence source — existing (unchanged).
5. Admissibility gate — designed (contract §10); ready behind #2.
6. Clarification state — unchanged (downstream).
7. Telemetry — extend `target_resolution` with states/reason classes/
   evidence records + select-path candidate count.
8. Upstream-path attribution — missing (unchanged).
9. **NEW — Non-equivalence/sufficiency validator** (bounded discovery
   per §24.1; test cases: R-F false-negative, T6 false-positive,
   B2b overlap-maximized, R-A contested).
10. **NEW — Candidate core-concept metadata contract** (only if the
    metadata path wins question 1; F5/F6 identical-metadata is the
    blocking fact).

## 27. Exit Status

All twelve exit questions (brief §26) are answered above with sample
evidence; the one honest boundary is stated: the sufficiency/
non-equivalence validator is designed-in-shape but not yet validated
at scale. WP-M2-1 implementation remains **gated on that single
bounded discovery** (Decision B). No production code was changed; no
SP-M2-1C.x spikes created; downstream M2 WPs remain unstarted.

### Final one-sentence answer (brief §30)

> **定义上：是 —— “LLM 认为 A 更相关”与“请求证据唯一支持 A”现在可以被
> 明确区分并已在本样本中实证（per-candidate support + 系统集合谓词，
> 有效真实案例运行中 0 次 unsafe SELECT，包括 frozen contract 5/5 选错
> 的 R-A）；机制上：还差最后一步 —— 充分性/非等价性的自动验证
> （R-F 假负例、T6 假正例为其精确测试用例），因此完整机制尚未成立，
> 不以猜测填补。**
