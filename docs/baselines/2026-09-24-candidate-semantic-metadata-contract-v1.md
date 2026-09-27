# Candidate Semantic Metadata Contract — Safe Target Refinement

**Status**: **CONTRACT DESIGN COMPLETE** — governance/contract document.
**Implementation NOT STARTED**; no production code, prompt, threshold,
or schema was changed to produce this document.
**Date**: 2026-09-24
**Parent**: `2026-09-24-wp-m2-1-safe-target-refinement-contract-v1.md`
(§17 implementation dependency: candidate semantic metadata contract)
**Discharge of**: `2026-09-24-wp-m2-1-item2a-sufficiency-non-equivalence-discovery-v1.md`
**Decision C** (candidate metadata insufficient; metadata contract
required before Safe Select implementation)
**Evidence inputs**: Item 2 / Item 2A discoveries · SP-M2-1 final QA ·
live U00010 metadata (re-verified 2026-09-24; F5 "Shared Device
Relationships." / F6 "Linked Account Network." / F8 "Opposite Trade
Ratio."; F5 and F6 carry IDENTICAL structured fields — the founding
fact of this contract).

---

## 1. Purpose

Define the Candidate Semantic Metadata Contract that Safe Target
Refinement consumes: the schema every Safe-Select-eligible candidate
must satisfy, the status model that keeps incomplete metadata from
becoming wrong SELECTs, the onboarding/governance process for future
candidates, and the fail-closed behavior that binds them together.

**Core governance principle** (the contract's reason to exist):

> We cannot guarantee that every future candidate will already exist in
> the metadata catalog. We CAN guarantee that a candidate without
> complete, verified metadata never becomes Safe-Select-eligible, and
> that missing metadata is evaluated as UNKNOWN — never FALSE — so
> incomplete metadata can never manufacture a wrong Unique Support.

## 2. Problem Statement

Safe Target Refinement's admissibility condition requires
Discriminating Evidence and Unique Support, which require per-candidate
semantic metadata: what each candidate is *centrally about* (core), and
where its boundaries are. Today's production carries only title +
free-text description + structured signal fields — and on live U00010,
F5 and F6 are structurally identical (`graph_signal`,
`[shared_device_count, linked_account_count]`), distinguishable only by
prose. Item 2A proved the failure mode concretely: one missing Chinese
synonym in a competitor's concept table produced a false competitor
negation and an unsafe SELECT (C13). The missing piece is not more
logic — it is a governed semantic metadata layer.

## 3. Plain-language Definitions

- **Candidate**: a possible answer to "which finding does this request
  mean?" (today: a Risk Platform finding such as F5/F6/F8).
- **Core concept**: what the candidate is *centrally about*, expressed
  as a canonical concept ID (e.g. F5 → `SHARED_DEVICE_RELATIONSHIP`).
- **Distinguishing concept**: the concept(s) that separate this
  candidate from its known confusables.
- **Metadata**: the governed record of (1)–(2) plus status, version,
  and provenance. **It is not a keyword dictionary.**
- **Safe-Select-eligible**: a candidate whose metadata is COMPLETE and
  verified, and which may therefore be admitted by a SELECT.
- **UNKNOWN**: the honest evaluation result whenever metadata is not
  complete/verified or the semantics are undecidable. UNKNOWN must be
  *represented*, never silently treated as FALSE.

## 4. Non-goals

No production code/tests/prompt/threshold/runtime changes; no metadata
runtime loader implementation; no exhaustive keyword dictionary; no
coverage of all future languages or phrasings; no claim that all
current or future candidates are already authored; no downstream M2
work packages started.

## 5. Design Principles

1. **Metadata ≠ keyword dictionary.** Metadata declares *core
   semantics* as canonical concept IDs. Mapping arbitrary user phrasing
   onto canonical concepts is the normalization layer's job (semantic,
   LLM-assisted at runtime) — not the metadata's.
2. **Minimal sufficient semantics.** A candidate needs enough concepts
   to judge core-vs-not and separable-vs-confusable among *known*
   candidates — nothing more. Anything undecidable → UNKNOWN → CLARIFY.
3. **Fail closed.** Missing/incomplete/unverifiable metadata ⇒ UNKNOWN
   support ⇒ not admissible ⇒ CLARIFY.
4. **Existence ≠ eligibility.** A candidate may exist in the Risk
   Platform and be visible to users without being Safe-Select-eligible.
5. **Governed authorship.** Metadata is authored and verified by
   accountable humans through a defined process; the LLM assists and
   proposes but never confers eligibility.

## 6. Candidate Metadata Schema

Every field is marked REQUIRED / OPTIONAL / DERIVED / NOT REQUIRED.

| Field | Req | Content |
|---|---|---|
| `candidate_id` | REQUIRED | authoritative RP finding id (F5…) |
| `candidate_type` | REQUIRED | e.g. `finding` (open vocabulary) |
| `metadata_version` | REQUIRED | increments on any semantic change |
| `metadata_status` | REQUIRED | §11 status enum |
| `owner` | REQUIRED | accountable author/owner |
| `last_verified_at` | REQUIRED | last independent verification time |
| `core_concepts` | REQUIRED | 1..n canonical concept IDs (§7) |
| `distinguishing_concepts` | REQUIRED | concepts that separate this candidate from its `potential_confusions` |
| `potential_confusions` | REQUIRED | candidate/concept pairs known to be confusable (e.g. F6 ↔ F5 via device-derivation) |
| `mechanism_concepts` | OPTIONAL | derivation mechanisms explicitly NOT core (e.g. F6: "detected *through* shared devices") |
| `language_variants` | OPTIONAL (non-authoritative hints) | alias hints for the normalization layer; never matched directly |
| `subject_scope` | OPTIONAL | domain/subject semantics |
| `source` | DERIVED | RP finding reference |
| `verification_note` | DERIVED | why the verifier judged it complete |

Deliberately NOT REQUIRED: exhaustive alias tables, per-language concept
tables, negative-concept dictionaries (§17), numeric weights.

## 7. Semantic Core

`core_concepts` are canonical concept IDs (UPPER_SNAKE), each defined
once in a governed **Concept Registry** (concept id → human definition;
the registry, not the finding, is what normalization maps into).
Rules:
- 1..n core concepts per candidate; a candidate genuinely centered on
  two things legitimately competes on both (and then confusable requests
  correctly CLARIFY).
- The CORE criterion from Item 2A is the support rule: request evidence
  maps to a candidate's **core**, not merely to content that appears in
  its description. `mechanism_concepts` may explicitly record known
  non-core mentions (e.g. F6's "detected through shared devices") to
  help authoring, but the predicate only reads `core_concepts`.

## 8. Distinguishing Semantics

`distinguishing_concepts`: the subset of core concepts that answers
"what separates this candidate from its potential confusables?" —
REQUIRED so that reviewers confront the boundary explicitly (F5:
`SHARED_DEVICE` against confusable F6; F6: `ACCOUNT_NETWORK_ENUMERATION`
against confusable F5). `potential_confusions`: REQUIRED — the known
confusable pairs; this is the field that would have predicted the C13
and R-A collisions at authoring time instead of at runtime.

## 9. Language Handling

Metadata stores canonical concept IDs — one language-independent layer.
User-language → canonical-concept mapping is performed at runtime by
semantic normalization (goal-understanding-class LLM work, bounded,
proposal-only). Language variants/aliases in metadata are OPTIONAL
hints for that layer and are never matched directly. This is the
structural fix for Item 2A's C13 failure: no user phrase is ever
lexically matched against metadata, so a missing synonym can no longer
flip a verdict.

## 10. Provenance

Every metadata record carries `owner`, `authored_by`, `verified_by`,
`metadata_version`, `last_verified_at`, `source`. Audit question
—"why does F5 support SHARED_DEVICE?"— must be answerable from the
record: owner + version + concept registry definition + verification
note.

## 11. Metadata Status

| Status | In candidate set? | Participates in refinement? | Support evaluation | Unique Support | Safe SELECT |
|---|---|---|---|---|---|
| METADATA_COMPLETE | yes | yes | normal (TRUE/FALSE/UNKNOWN) | allowed | allowed |
| METADATA_INCOMPLETE | yes | yes (visible in clarifications) | **UNKNOWN** | **UNKNOWN** | never |
| METADATA_UNVERIFIED | yes | yes | **UNKNOWN** | **UNKNOWN** | never |
| METADATA_DEPRECATED | no (excluded from new sets; historical records keep provenance) | no | not evaluated | — | never |

(METADATA_INVALID is folded into INCOMPLETE + verification note — no
status proliferation.)

**Normative rule (the UNKNOWN discipline):** INCOMPLETE/UNVERIFIED
means Support = **UNKNOWN**. It must never be interpreted as "does not
support the concept" (FALSE).

## 12. Completeness / Eligibility

A candidate is **SAFE-SELECT-ELIGIBLE** iff: `core_concepts` non-empty ·
`distinguishing_concepts` non-empty · `potential_confusions` reviewed ·
provenance complete · status = METADATA_COMPLETE · version valid.
"title + description exist" is explicitly NOT completeness.

## 13. UNKNOWN Semantics

UNKNOWN is produced by: ineligible metadata · concepts absent from the
Concept Registry · conflicting authored semantics · normalization
failing to map. UNKNOWN propagates: Support=UNKNOWN ⇒ Unique Support =
UNKNOWN ⇒ SELECT not admissible ⇒ CLARIFY (reason class
`insufficient_evidence` / `metadata_incomplete`). UNKNOWN must remain
distinguishable from FALSE in every record and telemetry surface.

## 14. Safe Fail-Closed Behavior

Complete(C) ∧ complete(E) ⇒ normal evaluation. Complete(A) +
incomplete(B): Support(B)=UNKNOWN ⇒ Unique=UNKNOWN ⇒ **CLARIFY** — even
if A is confidently supported and the model prefers it. This clause is
the formal Safe Select dependency introduced by this contract.

## 15. Candidate Catalog vs Metadata Catalog

The RP candidate catalog (findings per case, fetched live) and the
Safe Select metadata catalog need **not** be 1:1 at every moment —
partial coverage is a normal, temporary state. The invariant is:
every candidate visible to refinement has a metadata status, and
non-COMPLETE ⇒ ineligible. Coverage Check (§21) keeps the delta
visible; the system's behavior on the delta is always fail-closed.

## 16. New Candidate Onboarding Contract

```
Step 1  Candidate created in RP
Step 2  Coverage Check detects it -> metadata_status = INCOMPLETE
        (automatic; absence of a record is INCOMPLETE, never FALSE)
Step 3  Domain owner authors: core concepts, distinguishing concepts,
        potential confusions, provenance
Step 4  Semantic review (concept registry conformance; boundary check
        against existing candidates)
Step 5  Independent verification (QA role)
Step 6  status = METADATA_COMPLETE (version stamped)
Step 7  Candidate becomes SAFE-SELECT-ELIGIBLE
```

Until Step 6: the candidate remains visible to users (selectable
manually, listed in clarifications) but can never be admitted by a
SELECT.

## 17. Negative Semantics — deliberately NOT required

An explicit NOT_CORE dictionary is not required: non-equivalence is
computed by core-mapping (a competitor is FALSE only when the signal
maps to the supported candidate's core and not to the competitor's
core) plus the UNKNOWN rule. `mechanism_concepts` (optional) covers the
one useful negative case — recording that a known mention is a
derivation mechanism, not core. No further negative fields.

## 18. Metadata Examples — live RP candidates (contract drafts, not production metadata; live titles verified 2026-09-24)

**F5 — "Shared Device Relationships."**
core: `SHARED_DEVICE_RELATIONSHIP` · distinguishing: `SHARED_DEVICE` ·
potential_confusions: F6 (network derived from device sharing) ·
mechanism: — · owner/verification: (to be assigned at authoring).

**F6 — "Linked Account Network."**
core: `LINKED_ACCOUNT_NETWORK` · distinguishing:
`ACCOUNT_NETWORK_ENUMERATION` · potential_confusions: F5 (mechanism
mention: "detected through shared devices" → record under
mechanism_concepts) · notes: title/description prose must not be read
as core.

**F8 — "Opposite Trade Ratio."**
core: `OPPOSITE_TRADE_RATIO` · distinguishing: `OPPOSITE_SIDE_TRADING` ·
potential_confusions: trading-frequency findings (F7 family) · notes:
description explicitly disclaims anomaly ("does not by itself
demonstrate coordination") — anomaly questions are UNKNOWN, not
support.

## 19. Future Candidate Example (F12, hypothetical)

F12 appears in RP ("Unusual Login Pattern."). Coverage Check creates
its record: INCOMPLETE. Case set = {F5, F6, F12}; request “这个账户有哪些关联情况？”:
Support(F5)=TRUE, Support(F6)=TRUE, Support(F12)=**UNKNOWN** ⇒ Unique =
UNKNOWN ⇒ CLARIFY. F12's presence can neither be selected nor demote
F5/F6's support to FALSE — it can only keep the turn honest.

## 20. Acceptance Cases

| AC | Case | Expected |
|---|---|---|
| AC-M1 | complete candidate | eligible |
| AC-M2 | new candidate, no metadata record | INCOMPLETE (not FALSE/hidden) |
| AC-M3 | incomplete candidate in competition | Support UNKNOWN |
| AC-M4 | UNKNOWN must not become FALSE | predicate propagates UNKNOWN |
| AC-M5 | two complete candidates both supported | no Unique Support |
| AC-M6 | one complete candidate uniquely supported | may be admitted (Safe-Select-eligible) |
| AC-M7 | deprecated candidate | not eligible; excluded from new sets |
| AC-M8 | title/description/core semantics change | version invalidated → UNVERIFIED → re-verification |
| AC-M9 | new candidate appears post-release | detected by Coverage Check → INCOMPLETE |
| AC-M10 | incomplete metadata cannot bypass final admissibility | SELECT not admissible → CLARIFY |
| AC-M11 | CJK request normalization maps to canonical concept without exhaustive phrase tables | normalization-layer contract |

## 21. Coverage Check (contract, not implementation)

Input: the candidate catalog actually visible to refinement. Output:
per-candidate metadata status. Rules: any candidate without a metadata
record ⇒ INCOMPLETE (automatically created on detection); every
Safe-SELECT-admissible candidate must have passed Coverage Check as
COMPLETE at its metadata_version; the completeness delta is reported
(never hidden) so authors see the outstanding candidates.

## 22. Governance

| Activity | Product/Domain Owner | AI/LLM | System | QA |
|---|---|---|---|---|
| Define core concepts | **owns** | suggests | enforces schema | reviews |
| Define distinguishing semantics / confusions | **owns** | suggests | enforces schema | reviews |
| Suggest aliases/variants | approves | proposes | stores as hints | — |
| Validate semantics | reviews | — | schema validation | **independent verification** |
| Mark metadata complete | requests | never | records on verification only | decides |
| Detect missing metadata | notified | — | Coverage Check (automatic) | audits |
| Block unsafe SELECT | — | never | admissibility gate | audits |

## 23. Versioning

Any change to a finding's title, description, or authored semantics ⇒
`metadata_version` invalidated ⇒ status UNVERIFIED ⇒ re-verification
required before eligibility. Telemetry/evidence records cite the
metadata_version they were evaluated against.

## 24. Deprecation

Deleted/retired candidates: status DEPRECATED; excluded from new
candidate sets; historical evidence keeps provenance (no retroactive
rewrites). A deprecated candidate can never be selected.

## 25. LLM Boundary

LLM MAY: normalize user language to canonical concepts (proposal),
suggest candidate mappings and aliases, explain relations.
LLM MUST NOT: declare metadata complete, declare unique support,
convert UNKNOWN to FALSE, confer eligibility, decide final
admissibility.

## 26. System Boundary

System MUST: check status/version/completeness before evaluation,
preserve UNKNOWN through the predicate, run coverage detection,
enforce eligibility at the admissibility gate, record
provenance/version in evidence, and fail closed on every gap.

## 27. Production Gap (2026-09-24 inventory)

EXISTING: candidate ids/titles/descriptions per fetch; structured
fields (identical for F5/F6); GoalRepresentation; confidence;
`target_resolution` evidence. PARTIAL: within-turn candidate capture.
MISSING: candidate registry/catalog (candidates are re-derived per
fetch), metadata schema, metadata status, versioning, core concepts,
distinguishing concepts, multilingual normalization layer, provenance,
coverage check, onboarding workflow, re-verification process,
Safe-Select-eligibility flag.

## 28. Implementation Work Breakdown (planning only)

| # | Item | Dependency | Effort | Confidence | Discovery risk |
|---|---|---|---|---|---|
| 1 | Concept Registry + metadata schema (storage) | this contract | 1–2 d | High | Low |
| 2 | Author U00010 metadata (8 findings) + process template | #1, domain owner | 0.5–1 d | High | Low |
| 3 | Coverage Check + status/onboarding states | #1 | 1–2 d | High | Low |
| 4 | Semantic normalization layer (user language → concept; bounded LLM proposal + registry conformance check) | #1; design within WP-M2-1 | 2–3 d | Medium | Medium (CJK normalization quality) |
| 5 | Evidence mapping + unique-support predicate + admissibility integration | contract §10/§17 + #4 | 2–3 d | High | Low–Medium |
| 6 | QA/browser validation + independent QA | #5 | 1–2 d + QA phase | High | Low |

Ranges are estimates, not schedules. Item 4 holds the remaining
uncertainty; it is implementation design within an approved contract,
not further discovery.

## 29. Future Candidate Safety

**No guarantee is made that future candidates will pre-exist in the
metadata catalog.** The guarantee this contract makes is stronger where
it matters: *a future candidate may enter the system at any time, but it
cannot become Safe-Select-eligible until its metadata is complete and
verified; until then its support is UNKNOWN, UNKNOWN is never FALSE,
and incomplete metadata can never manufacture a wrong Unique Support.*
The system's answer to an ineligible candidate in competition is
CLARIFY — never a guess.

---

## 30. Decision

**A — the Candidate Semantic Metadata Contract is now sufficiently
defined to enter implementation planning.** Item 2A's Decision C
prerequisite is discharged by this document. Residual items are
implementation design (normalization layer, item 4) and authoring work
(item 2), not further discovery. Implementation of WP-M2-1 remains
gated on PMO approval to start, per the contract document.

### Final report — the twelve questions, plainly

1. **What is this metadata for?** It records, per candidate, the
   canonical concepts the candidate is *centrally about* and where its
   boundaries are — the data Safe Select needs to tell "supported by
   the user's words" apart from "preferred by the model".
2. **Why not title/description?** Because prose mentions ≠ core: F6's
   description contains "shared devices" without device-sharing being
   its core, and F5/F6's structured fields are identical — Item 2A
   showed one missing synonym in prose-based matching flips a safe
   verdict into an unsafe SELECT.
3. **Why not confidence?** It measures model preference, which every
   evidence round showed overlapping between correct and unsafe
   selections.
4. **Why not a keyword dictionary?** Phrasing is open-ended and
   multilingual; dictionaries silently go stale and their gaps become
   unsafe selections. Canonical concepts + runtime normalization bound
   the maintenance.
5. **New candidate appears?** Detected, marked INCOMPLETE, visible but
   ineligible; in competition it evaluates as UNKNOWN → CLARIFY.
6. **How do we know metadata is complete?** A defined checklist (core +
   distinguishing + confusions + provenance) plus independent
   verification; status and version record it.
7. **Metadata missing?** UNKNOWN everywhere; fail closed to CLARIFY.
8. **Who maintains it?** Domain owner authors, QA independently
   verifies, system enforces, LLM only suggests.
9. **What can the LLM do?** Normalize, propose mappings, suggest
   aliases, explain — never confer eligibility or uniqueness.
10. **What must the system control?** Status/version/completeness
    checks, UNKNOWN preservation, set predicate, eligibility, final
    admissibility.
11. **How do we avoid "one forgotten candidate → wrong SELECT"?** The
    forgotten candidate is INCOMPLETE by default; UNKNOWN propagates;
    the turn clarifies. A miss degrades to a clarification, never to a
    wrong selection.
12. **Ready for implementation?** The contract is; WP-M2-1
    implementation planning may start on PMO approval — the only
    medium-risk engineering item is the normalization layer.
