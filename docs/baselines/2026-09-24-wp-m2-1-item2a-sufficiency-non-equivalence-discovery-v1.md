# WP-M2-1 Item 2A — Sufficiency & Non-Equivalence Validator Discovery

**Status**: **DISCOVERY COMPLETE — Decision C** (the validator mechanism
shape is settled; implementation is gated on a **candidate semantic
metadata contract**, because today's candidate metadata cannot feed the
mechanism safely — proven by an unsafe selection caused purely by a
vocabulary gap)
**Date**: 2026-09-24
**Type**: bounded architecture/implementation discovery (offline; NO
production code, prompt, threshold, or schema changes)
**Parent**: `2026-09-24-wp-m2-1-safe-target-refinement-contract-v1.md`
§19 item 2 · `2026-09-24-wp-m2-1-item2-evidence-validation-discovery-v1.md`
(Decision B designated this discovery)
**Experiment artifacts**: `/tmp/spm21c3/cases_item2a.json`
(pre-registered dual-criterion ground truth), `results_mechanisms.json`.
All numbers are **observations in this experiment sample**.

---

## 1. Objective

Grounding is solved. This discovery answers the remaining question:
how does the system judge that a candidate's support is **SUFFICIENT**
(strong enough to be the answer) and **NON-EQUIVALENT** (competitors
do not get equal support from the same request evidence) — so that
Unique Support = TRUE reflects user evidence, not model preference?

## 2. Plain-language Definitions (used throughout)

- **Grounding**: "what the user said really relates to candidate A."
- **Sufficiency**: "that relation is strong enough to make A *the*
  answer."
- **Non-equivalence**: "the same user words do not equally support
  B/C."
- **Unique Support**: "A is supported; competitors are not equally
  supported."
- **Preference**: "the model likes A better." **Confidence**: "how sure
  the model is that it likes A."
- Non-equivalences carried as law: Preference ≠ Unique Support ·
  Confidence ≠ Unique Support · Similarity ≠ Discrimination ·
  Grounding ≠ Sufficiency.

## 3. Existing Contract (fixed inputs)

The WP-M2-1 contract's admissibility condition, frozen refinement
boundaries, candidate-ID enforcement, single attempt, confidence-as-
necessary-filter, and the Item-2 evidence/provenance model. This
discovery designs only the sufficiency/non-equivalence layer.

## 4. Research Questions

Q1 Can Grounded be reliably promoted to Sufficient? Q2 Can "A
supported, B not equivalently supported" be reliably judged? Q3 Can
Unique Support therefore be reliably produced?

## 5. Ground Truth Method

**Dual-criterion pre-registration** — the discovery's central
methodological act. 14 cases annotated BEFORE any mechanism run under
TWO support criteria:

- **CORE criterion** (primary, per the brief's Case 1): a candidate is
  Supported only when request evidence maps to its *central claim*;
  incidental mentions of the same signal in a competitor's description
  do not count.
- **MENTION criterion** (Item 2's conservative rule): any stated
  content counts; mechanism-mentions included.

The criteria disagree on exactly the interesting cases (C1/R-A:
SELECT F5 under CORE vs CLARIFY under MENTION; C5/R-E likewise). **The
choice of criterion is a policy decision the contract must pin — a
validator cannot be designed without it.** The brief's Case 1 adopts
CORE; this discovery evaluates under CORE as primary and records
MENTION as the alternative. UNKNOWN is first-class: any UNKNOWN ⇒ not
admissible ⇒ CLARIFY (never auto-FALSE).

## 6. Case Matrix (14 cases; full detail in `cases_item2a.json`)

| ID | Src | Request (abridged) | CORE GT | MENTION GT | Expected (CORE/MENTION) |
|---|---|---|---|---|---|
| C1 | R-A | 哪些账户…共享了设备 | F5=T, F6=F → uniq F5 | both T | SELECT / CLARIFY |
| C2 | R-B | 有哪些关联情况 | both T | both T | CLARIFY / CLARIFY |
| C3 | R-C | Linked Account Network 是什么 | uniq F6 | uniq F6 | SELECT / SELECT |
| C4 | R-D | …关联对冲交易 | uniq F8 (N=3) | uniq F8 | SELECT / SELECT |
| C5 | R-E | 有哪些相关账户 | uniq F6 | both T | SELECT / CLARIFY |
| C6 | R-F | 用了同一台设备吗 | uniq F5 | both T | SELECT / CLARIFY |
| C7 | R-G | 交易行为…异常 | F8=UNKNOWN | F8=UNKNOWN | CLARIFY / CLARIFY |
| C8 | R-H | 关联账户网络规模 | uniq F6 | uniq F6 | SELECT / SELECT |
| C9 | FX-1 | 共用同一台设备吗 | uniq FIX-A | uniq FIX-A | SELECT / SELECT |
| C10 | FX-2 | 需要关注的吗 | both T | both T | CLARIFY / CLARIFY |
| C11 | FX-3 | 共享了设备 (overlap-reduced) | uniq FIX-D | uniq FIX-D | SELECT / SELECT |
| C12 | FX-4 | 特别之处 | both T | both T | CLARIFY / CLARIFY |
| C13 | B2b | 共享了设备 (overlap-maximized) | both T (equivalent) | both T | CLARIFY / CLARIFY |
| C14 | U1 | 和设备有关的发现？ | FIX-Alert=UNKNOWN | UNKNOWN | CLARIFY / CLARIFY |

## 7. Evidence Provenance

Every support declaration must carry: a verbatim `USER_REQUEST`
fragment + a verbatim `CANDIDATE_METADATA` concept + the
`SYSTEM_VALIDATION` result. `LLM_INTERPRETATION` alone never
constitutes validated evidence (proven again this round: the only
unsafe selection in the sample came from accepting a mapping the
metadata could not linguistically support). `CASE_EVIDENCE` remains
excluded from the refinement surface.

## 8. Grounding

Grounding = verbatim fragment ∈ request ∧ verbatim concept ∈ candidate
description. It is cheap, deterministic, and necessary — and it is
where its usefulness ends (§9).

## 9. Sufficiency

Grounded-but-insufficient is real and structural: (a) T6-class — the
candidate's semantics are *generic* ("anomalous behavior") relative to
the request's specificity ("对公转账异常"); (b) C13-class — the
competitor's description *mentions* the signal without its core being
the signal. Sufficiency is operationalizable as **core mapping**: the
request signal must hit the candidate's declared CORE concept, not
merely any description content. This requires core concepts to exist
as data — the metadata contract.

## 10. Non-Equivalence

Non-equivalence = for the supported candidate A, every competitor B
fails core-mapping for the same request evidence. It is computed, not
judged: with complete core tables, C6 resolves correctly (F6's
mechanism-mention is not core → F6 FALSE → unique F5 — Item 2's
false-negative *eliminated*). But non-equivalence inherits the
metadata's completeness — C13 shows the failure mode (§16).

## 11. Unique Support

The predicate (exactly one TRUE + ≥1 FALSE; any UNKNOWN ⇒ UNKNOWN ⇒
CLARIFY) held on every case where its inputs were correct. Two input
errors produced both remaining misses — the predicate itself never
failed.

## 12. Candidate Metadata

The prerequisite finding, restated with this round's evidence: F5/F6
structured fields are identical (type, signal_refs, capabilities);
core concepts exist only implicitly in free text; and core tables must
be **bilingual** — C13's unsafe SELECT was caused by a single missing
Chinese synonym ("共享了设备") in a competitor's table while its
English form ("device sharing") was present. Vocabulary asymmetry =
false competitor-negation = unsafe SELECT.

## 13. Semantic Equivalence Judge (M-B)

Rubric: judge each candidate's CORE support + direct non-equivalence
question. Result: **6/42 runs valid** (endpoint degradation); valid
samples matched GT except **C14 — the Unknown Trap fired** (judge
treated FIX-Alert's UNKNOWN as FALSE → SELECT). Stability unmeasurable;
single-judge designs inherit both the endpoint fragility and the
UNKNOWN-discipline problem.

## 14. Hybrid (M-C)

Metadata primary + judge arbitration on non-unique/unknown:
11/14 — inherits M-A's C13 unsafe (metadata-primary passed it through
without arbitration) and added a C14 arbitration failure (judge
UNKNOWN→FALSE trap). In this sample the judge's net contribution was
**negative**; it may still serve as a secondary check, but only with
UNKNOWN-disciplined output contracts.

## 15. Pairwise Experiments

- **Pair A (goal specificity, same set)**: C2 (关联情况) → both
  supported, CLARIFY; C1 (共享设备) → core-unique, SELECT. Goal
  specificity creates unique support only under the CORE criterion —
  under MENTION even C1 stays ambiguous.
- **Pair B (metadata variation, same request)**: C1 (competitor
  mentions signal) vs C11 (mention removed) vs C13 (competitor reduced
  TO the signal): identical requests, three different evidence
  structures — candidate metadata, not request wording, decided every
  outcome.
- **Pair C (similar confidence, different uniqueness)**: carried from
  SP-M2-1C — R-F correct SELECTs 0.72–0.82 vs R-G unsafe 0.72–0.88.
  Confidence does not separate them; core-mapping does.
- **Pair D (same grounding, different sufficiency)**: C3/C8
  (grounded+sufficient) vs T6/C13 (grounded+insufficient): the
  observable structural difference is whether the request signal hits
  the candidate's *core* or merely *content* — and whether the
  competitor's core is genuinely disjoint.

## 16. Observations (valid samples only)

- **M_A** (deterministic, full bilingual tables): **12/14 match** the
  CORE GT. Misses: C5 (synonym coverage — “相关账户” absent from F6's
  table → over-cautious CLARIFY, safe direction) and **C13 (unsafe)** —
  FIX-N2's table held the English form ("device sharing") but not the
  request's Chinese form ("共享了设备") → competitor falsely FALSE →
  false UNIQUE → SELECT. UNKNOWN representation: M_A cannot emit
  UNKNOWN (C7/C14 outcome-safe by conflation, wrong by representation).
  Zero LLM dependency: 14/14 executed despite endpoint degradation.
- **M_B**: 6/42 valid; the C14 Unknown Trap fired on its one
  informative sample; stability NOT MEASURABLE this sample.
- **M_C**: 11/14 — C13 unsafe inherited via metadata-primary; C14 unsafe
  introduced by arbitration; C5 over-cautious. 20/42 runs lost to
  endpoint.
- Endpoint condition: severe degradation throughout (documented
  environmental limitation; also a design input — deterministic layers
  never failed).

## 17. Counterexamples (all five brief-specified classes found)

1. **False Positive (grounding=TRUE, sufficiency=FALSE)**: T6 (Item 2)
   and C13 (this round — via vocabulary asymmetry).
2. **False Negative (unique support exists, validator says no)**: C5 —
   synonym-coverage gap → CLARIFY where CORE GT expects SELECT F6.
3. **Equivalence failure (both supported, validator calls one unique)**:
   C13 — the exact case, caused by a missing Chinese synonym.
4. **Preference trap**: carried from SP-M2-1C (R-A frozen 5/5 SELECT
   F6 0.78–0.88 under both-criteria disagreement).
5. **Unknown trap**: C14 — both LLM mechanisms treated UNKNOWN as FALSE
   and selected; the deterministic layer concealed it instead of
   representing it.

## 18. False Positives

One this sample (C13), root cause = metadata vocabulary asymmetry, not
predicate logic. Consequence if unaddressed: unsafe SELECT. Directly
fixable only at the metadata layer (complete bilingual core tables) —
which is why the decision is C.

## 19. False Negatives

One this sample (C5), root cause = synonym coverage. Consequence:
over-cautious CLARIFY (safe direction). Also C14-as-designed (UNKNOWN →
CLARIFY is correct behavior; M_B/M_C violated it).

## 20. Production Observability

EXISTING: candidate IDs/titles/descriptions, GoalRepresentation,
self-reported confidence, `target_resolution` evidence field.
PARTIAL: candidate snapshot. MISSING: core-concept metadata (bilingual),
evidence record, unique-support predicate, admissibility gate/states,
upstream-path attribution, UNKNOWN-capable support representation,
select-path candidate count. UNKNOWN: confidence calibration.

## 21. Evidence Model (research floor; schema finalization = implementation)

REQUIRED: source (USER_REQUEST + CANDIDATE_METADATA), request_fragment
(verbatim), candidate_id, candidate_core_concept (from the metadata
contract — NOT free description text), support TRUE/FALSE/UNKNOWN,
validation_status. DERIVED: unique_support. OPTIONAL:
competitor_refs, E-level, relation_type. The decisive change from
Item 2's model: `candidate_core_concept` must come from a **maintained
core-concept metadata contract**, not from arbitrary description
substrings — C13 is the proof.

## 22. Responsibility Boundary

LLM: understand, propose mappings, explain. System: own the core-concept
tables' role as the yardstick, run deterministic core-mapping, compute
the set predicate with first-class UNKNOWN, decide admissibility, emit
reason classes. If an LLM judge is ever added, it must be
UNKNOWN-disciplined by contract (C14 shows the failure) and its
verdicts remain proposals to the predicate.

## 23. Options Comparison (HIGH/MEDIUM/LOW with facts)

| Dimension | A Metadata (core tables) | B Equivalence Judge | C Hybrid |
|---|---|---|---|
| Grounding | deterministic | LLM | deterministic + LLM |
| Sufficiency | **core-mapping: works when tables complete** | unproven at scale (endpoint) | works via A |
| Non-equivalence | **works; unsafe on vocabulary gaps (C13)** | UNKNOWN trap (C14); unmeasurable | inherited C13 + C14 |
| Stability | deterministic (14/14 ran) | 6/42 valid | 22/42 valid |
| Safety | 1 unsafe (metadata gap) | 1 unsafe (unknown trap) | 2 unsafe |
| Explainability | HIGH (term-level provenance) | MEDIUM | HIGH |
| CJK | requires bilingual tables (the gap IS the risk) | native | requires tables |
| Maintenance | table authoring + synonyms | prompt/rubric | both |
| LLM dependency | none | high | medium |
| Runtime cost | zero LLM | 1+ call | 1 call when arbitrated |
| Auditability | term-level, auditable | rationale-level | term-level |

## 24. Findings

1. The support **criterion** (CORE vs MENTION) is the load-bearing
   policy choice — it flips C1/C5/C6 verdicts; the brief's Case 1
   adopts CORE, and this discovery's mechanism operationalizes CORE.
   PMO must confirm that adoption (it is more permissive than Item 2's
   conservative readings).
2. Sufficiency and non-equivalence are both operationalizable as
   **declared core-concept mapping** — the shape is settled.
3. Safety of the deterministic mechanism is exactly as good as the
   bilingual metadata completeness (C13: one missing synonym → unsafe
   SELECT). UNKNOWN must be represented, never conflated (C14).
4. LLM judge layers added no measured value this sample and imported
   the UNKNOWN trap; they remain optional future enhancements with a
   required output contract.

## 25. Open Questions (implementation choices, not research)

Core-table authoring/maintenance process and synonym-coverage policy;
normalized matching mechanics; whether a disciplined judge is later
added as a secondary check; threshold values; evidence-record schema
finalization.

## 26. Decision

**C — Candidate metadata is insufficient; a candidate semantic metadata
contract (bilingual core concepts per finding, synonym policy,
maintenance ownership) must be established before Safe Select
implementation.** The validator mechanism itself is settled: deterministic
core-mapping + set predicate + first-class UNKNOWN, inside the WP-M2-1
contract's admissibility condition. Item 2's Decision B is thereby
resolved into this C — the "one more discovery" it anticipated turns
out to be a contract/authoring prerequisite, not further research.

## 27. WP-M2-1 Implementation Dependency

- **Prerequisite (new, blocking)**: Candidate Semantic Metadata
  Contract — per-finding core concepts (bilingual), synonym policy,
  UNKNOWN policy, maintenance owner. Scope hint from this sample:
  F5/F6 prove structured RP fields cannot be derived from; tables must
  be authored per finding (8 findings on U00010 today).
- Then: evidence record + predicate + gate (contract §17 items 2/3/5)
  proceed as designed; work item 2's discovery risk is discharged into
  the metadata contract.
- Downstream M2 WPs remain unstarted. No production code was changed
  in this discovery; no sub-spikes created.

### Final one-sentence answer (brief §23)

> **部分已知，但边界清晰**：我们已知如何把“LLM 认为 A 更相关”转换为系统可独立验证的判定 —— 用**声明式双语核心概念映射 + 集合谓词 + 一等 UNKNOWN**（本样本 12/14 与 ground truth 一致，且该机制是唯一在 C1 上给出 SELECT F5 的方案）；但我们也证明了其安全性完全取决于核心概念表的完备性（缺一个中文同义词即产生一次 unsafe SELECT），而当前生产系统不存在这样的元数据 —— 因此必须先建立 Candidate Semantic Metadata Contract，才能进入实现。
