# FINAL COMPOSITION-LEVEL AUDIT — Response & Artifact Semantic Integrity

**Status**: SUPERSEDED — P0-1, P0-2, and P0-4 were subsequently fixed.
Current implementation is covered by:
- `backend/app/composition.py`
- `backend/tests/test_p0_composition_fixes_v1.py`
- subsequent detector/evidence-stream regression tests

The findings and verdict below are preserved verbatim as a historical audit
record of the state at that time; they no longer describe the current
runtime.

**Status (historical)**: AUDIT RECORD — V1 (read-only audit; no code, tests, or docs modified)
**Date**: 2026-09-04
**Scope**: The composition layer only — where authoritative ToolResults and persisted context are transformed into user-visible conversation text (pipeline A) and artifact content (pipeline B). Infrastructure excluded.
**Method**: Every claim empirically reproduced by direct execution against the frozen runtime; nothing inferred from principles alone.

---

## A. Composition Model

```
ToolResult payload (structured, tool-owned truth)
  │
  ▼ CONVERSATION COMPOSER (compose_response — backend, deterministic)
  │  inputs: ONLY current turn's execution.tool_calls + user_request
  │  transforms: payload→prose branch per shape; threshold-label .replace();
  │             citation-marker .sub(); guidance sentence appended;
  │             evidence_missing branch (policy/artifact exempted);
  │             next_data_needed join
  │
  ▼ PERSISTED turn response (task_response_text)
  │
  ▼ FRONTEND PRESENTATION (ConversationPanel)
     transforms: renderTurnText() — deletes the "Select a finding…"
     sentence from DISPLAYED text when focusedFindingId is set;
     navigationHint keyed on skill + focus
```

```
ToolCallV2 pool (current task + prior-turn session pool)
  │
  ▼ ARTIFACT COMPOSER (artifact_bundle — backend, deterministic)
     per-section predicates (Part 5); render-time contributor accumulation;
     _render_gaps() names the actual gap
```

---

## B. Conversation Response Lineage Table

| Branch | Source fields | Transform | Filter | Defect found |
|---|---|---|---|---|
| Intake prose | `findings[]`, `case_id` | count sentence + "Select a finding… to continue the investigation." + per-finding lines | citation-marker sub | ⚠️ sentence is grammatically fused to "to continue the investigation." — see P0-1 |
| Artifact confirmation | `artifact.artifact_id` | appended line | — | OK |
| Timeline | `events[]`, `total_events`, `truncated` | count prose + event lines | — | OK (1-event grammar correct) |
| Evidence complete | `record_count`, `streams` | "returned N withdrawal and transaction records" | — | OK |
| Signal Rule (structured) | `rule.trigger_values/threshold` | feature-label `.replace()` on threshold string | — | OK (labels verified RP-faithful) |
| Signal Rule (desc only) | `rule.description` | citation-marker sub | — | OK |
| Signal ML/Graph | `explanation.ml_score` | — | — | **P0-2** (gap widening below) |
| Policy associated | `matches`, `associated_policy_refs` | max-2 + "apply to this finding" | — | OK |
| Policy no-basis | `finding_policy_status` | "No finding-level policy basis…" + COMPLETE case-level set labeled case-level | — | OK |
| Policy EMPTY | — | "No directly applicable policy…" | — | OK |
| evidence_missing branch | `evidence_missing`, `next_data_needed`, payload shape | **one generic transaction sentence for ALL non-policy/non-artifact payloads** | policy/artifact exemptions | **P0-2** |
| next_data_needed line | `next_data_needed` | "; ".join | artifact exempt | ⚠️ P1-3 |
| Cross-case / unsupported / empty / integration | error detail | bounded per-state sentences | — | OK (previously fixed) |

---

## C. Evidence-Gap Semantic Matrix (producers → rendered claim)

| Producer | Trigger | `next_data_needed` | Conversation renders | Artifact renders | Widening? |
|---|---|---|---|---|---|
| `policy_lookup` | `not policy_refs` | "finding-level policy association…" | policy branch (exact) | "No finding-level policy association…" (exact) | ✅ none |
| `signal_explain` Rule-no-backing | no matching rule | "triggered-rule evidence associated with this finding" | **"complete transaction-level evidence is not available"** ❌ | names the triggered-rule gap (acceptable) | ❌ conversation |
| `signal_explain` ML | attribution absent (ALWAYS on live RP) | "transaction-level feature attribution" | **generic transaction sentence** — reads as records missing while 67 timeline events exist ❌ | exact-named | ❌ conversation |
| `signal_explain` Graph | no network / not graph finding | "network/cluster evidence…" | generic transaction sentence ❌ | exact-named | ❌ conversation |
| `finding_drilldown` timeline | unlinked evidence stream kinds | "evidence stream: k" | generic transaction sentence ❌ (same branch) | exact-named | ❌ conversation |
| `finding_drilldown` evidence-empty | 0 records | — | dedicated EMPTY branch (exact: "holds no concrete records…") | n/a | ✅ |
| `artifact_bundle` | inherited | — | exempted | "No finding-level policy association…" (exact) | ✅ |

**Root of the widening**: the composer's else-branch (`investigation_service.py` evidence_missing handling) renders ONE hard-coded transaction sentence for every non-policy/non-artifact `evidence_missing` payload. The live "67 events vs. evidence unavailable" case is the ML path: RP never provides attribution, so `evidence_missing` is *always* true there, and the sentence is *always* wrong.

Required mapping (already implemented in `_render_gaps` for artifacts — the conversation composer must reuse the same per-producer semantics):

```
policy association missing        → policy association missing
triggered-rule evidence missing   → triggered-rule evidence missing
transaction records missing       → transaction records missing
feature attribution missing       → feature attribution missing (NOT records)
signal backing evidence missing   → signal backing evidence missing
network/cluster evidence missing  → network/cluster evidence missing
```

---

## D. Focus/Guidance Matrix

| State | Intake sentence (backend) | Frontend display | navigationHint | Correct? |
|---|---|---|---|---|
| No focus | contains "Select a finding… to continue the investigation." | shown as-is | shown | ✅ |
| Focused (historical intake message) | same stored text | **filter deletes "Select a finding… left\.?" leaving "to continue the investigation." fragment** ❌ (reproduced: `"7 findings were identified for case U00047. \n to continue the investigation."`) | suppressed | ❌ P0-1 |
| Focused (restored conversation) | synthesized ack reflects focus | — | — | ✅ |

**Part 2 verdict — the correct owner is structured response generation.** The backend composer emits the guidance sentence *fused* into a two-clause paragraph. Any post-hoc substring deletion on a fused sentence risks fragments (observed live). Root design flaw: guidance is not a separate structured field of the response (e.g. `guidance: {kind: "select_finding"}` the frontend renders/clears by state), forcing the frontend into text surgery. Minimal correct change: the frontend filter must delete the WHOLE sentence *including* its continuation ("Select a finding from the Findings panel on the left to continue the investigation\.?"). Proper long-term change: composer emits guidance as a structured field.

---

## E. Policy Scope Matrix (verified with live-shaped payloads)

| Context | Input | Output | Leak into finding artifact? |
|---|---|---|---|
| A. conversation, associated | F2 cited [1], matches {1,2} | "2 policy references apply… [1] + one more" (max-2) | — |
| B. conversation, no-basis | F2 unmarked, matches {1,2,3} | "No finding-level policy basis…" + all 3 labeled case-level | — |
| C. finding artifact, associated | same as A | Finding block "Policy citations: [1]" **+ full matches table incl. [2] (not F2's)** | **❌ LEAK — P0-4** |
| C. finding artifact, no-basis | — | exact no-basis statement, no table | ✅ |
| D. case artifact | — | complete case table | ✅ |

Presentation rules from A/B must not leak into C — currently they do, via the associated branch (P0-4).

---

## F. Artifact Section Lineage Matrix

| Section | Source | Scope | Inclusion predicate | Exclusion | Contributor rule | Leakage path |
|---|---|---|---|---|---|---|
| Finding | latest `risk_case_fetch` success | both | target finding (once) | other findings; superseded fetches | canonical fetch | none found ✅ |
| Timeline | drilldown `events` | finding | payload finding_id == target | — | rows rendered | none (tested F3→F2 ✅) |
| Evidence | drilldown evidence view `complete=true` | any | complete sets only | bounded previews | records rendered | none ✅ |
| Signal | signal_explain | finding | rule/ML/graph payload | no backfill | line rendered | none ✅ |
| Policy | policy_lookup + fetch | **scope-branched** | associated→table; no-basis→statement; case→full | other findings | content rendered | **associated branch: unfiltered matches table ❌ P0-4** |
| Evidence Gaps | per-payload `next_data_needed` | both | exact-named items | "insufficient" generic | rows rendered | none ✅ |
| Provenance | render-time accumulation | =content | once per contributing call | failed/prior/non-contributing | = rendering | none (verified: F1→F2 pool probe excluded the other finding's timeline call ✅) |

---

## G. Multi-Turn Contamination Matrix (empirically probed)

| Transition | Content leak | Provenance leak | Verdict |
|---|---|---|---|
| F1→F2 (timeline+signal+policy in pool) | none | other finding's timeline call excluded | ✅ |
| F2→F1 | symmetric by same predicates | same | ✅ |
| intake → finding artifact | prior fetch superseded by canonical; no duplicate blocks | prior fetch excluded | ✅ |
| finding → case artifact | case scope legitimately broadens; canonical fetch owns citations | all contributors listed | ✅ |
| finding → finding artifact (same finding) | no duplicate finding block | fetch listed once | ✅ |
| conversation composer | receives ONLY current `execution.tool_calls` (verified at the call site) — no session pool | n/a | ✅ |

---

## H. Test-Gap Matrix

| Invariant | Single-branch | State-combination | Multi-turn | Browser/E2E |
|---|---|---|---|---|
| Gap naming per producer | ✅ policy only | ❌ ML/graph/rule-no-backing never composed | ❌ | ❌ |
| Finding-artifact policy table = associated only | ❌ (fixtures use 1-match payloads) | ❌ | ❌ | ❌ |
| Guidance sentence integrity under focus filter | ❌ no frontend rendered-text tests | ❌ | ❌ | partial (the fragment shipped) |
| Threshold-label replace | label map pinned | compound thresholds untested | — | — |
| Provenance / containment | ✅ | ✅ | ✅ | ✅ |
| Policy conversation contracts | ✅ | ✅ | ✅ | ✅ |

Systemic gap: **composition invariants are tested at the tool layer, not at the rendered-text layer** — exactly where all three observed defects live (two backend composer/branch bugs, one frontend render). No test composes a multi-producer response and asserts the final sentences.

---

## I. Findings

### P0 — must fix before release

**P0-1 — Focus filter produces grammatical fragments.**
- Concrete example (live): "7 findings were identified for case U00047. to continue the investigation."
- Exact source field: backend intake text (`compose_response` intake prose: sentence fused as "…panel on the left to continue the investigation.").
- Transformation path: persisted turn text → `ConversationPanel.renderTurnText` → regex `SELECT_FINDING_SENTENCE_RE` deletes "Select a finding from the Findings panel on the left\.?" only — the continuation "to continue the investigation." survives.
- Root cause: guidance embedded as prose, filtered post-hoc.
- Owning boundary: frontend `SELECT_FINDING_SENTENCE_RE` must match the full two-clause sentence; correct long-term owner is structured guidance (backend emits a `guidance` field; frontend renders by state).
- Why the previous audit missed it: no frontend rendered-text tests exist; browser checks asserted the sentence's *absence*, never the grammar of what remained.

**P0-2 — Generic transaction-evidence sentence widens ML/Graph/rule-no-backing gaps.**
- Concrete example (reproduced): ML explain returns "transaction-level feature attribution" missing (always true on live RP), yet the response says "complete transaction-level evidence is not available" — while 67 timeline events were successfully retrieved. Same widening for "triggered-rule evidence…" and graph cases.
- Exact source field: `evidence_missing` + `next_data_needed` from the ML/Graph/Rule-no-backing normalizers.
- Transformation path: `compose_response` evidence_missing else-branch (lines ~370-375) — one hard-coded transaction sentence for all remaining payloads.
- Root cause: payload-shape-keyed branch instead of producer-semantics-keyed wording.
- Owning boundary: composer must key the sentence on the payload's own gap semantics — reuse the per-producer mapping `_render_gaps` already implements for artifacts.
- Why the previous audit missed it: policy and artifact exemptions were added and tested; the remaining producers were never composed-and-asserted (state-combination tests absent).

**P0-4 — Finding-scoped artifact Policy table includes non-associated case-level citations.**
- Concrete example (reproduced): F2 artifact shows "Policy citations: [1]" AND a table listing citation [2] (AML 2.2 Rapid Fund Movement — associated with F3, not F2).
- Exact source field: `policy_lookup.data.matches` (full ranked list) vs `associated_policy_refs`.
- Transformation path: `artifact_bundle` finding-scope associated-branch renders `_render_policy(data)` unfiltered over `matches`.
- Root cause: the no-basis branch was added with strict filtering, but the associated branch kept rendering the full table; containment fixtures used single-match payloads so the two never diverged.
- Owning boundary: `artifact_bundle` finding-scope policy branch must render only matches whose `citation_id ∈ associated_policy_refs` (or rely solely on the Finding block's authoritative citation line and drop the duplicate table).
- Why the previous audit missed it: single-match fixtures made the filter-vs-table distinction unobservable; principle-level check ("P11 satisfied") was not verified at the rendered-table level.

### P1 — should fix before release

**P1-3 — "Additional data needed" line joins raw `next_data_needed` verbatim** for signal payloads. Grammatically fine in isolation, but combined with P0-2's sentence it compounds the wrong scope. Fixing P0-2 via the `_render_gaps` mapping subsumes this.

### P2 — accepted / documented

- Threshold humanization by substring `.replace()` — labels verified RP-faithful; compound-threshold strings untested but low risk.
- Evidence-EMPTY prose (dedicated branch) — exact.
- Plan/task audit cards showing skill/step ids — documented trade-off.

### P3 — future

- Structured guidance field (backend) to retire frontend text filtering entirely.
- Frontend component tests so composition bugs cannot ship without rendered-text assertions.

---

## Verdict

**NOT READY FOR PACKAGING**

Three P0 composition defects — all empirically reproduced in the frozen runtime:

1. P0-1: the focus filter's grammatical fragment.
2. P0-2: the conversation gap-sentence widening across ML/Graph/rule-no-backing producers.
3. P0-4: the finding-artifact policy table rendering non-associated case-level citations.

All three live in exactly the layer this audit targeted — the ToolResult → user-visible transformation — and all three were invisible to principle-level audits because no tests assert the final rendered sentences. They require targeted composition-boundary fixes (frontend full-sentence filter or structured guidance; composer per-producer gap mapping; artifact policy-table filter) before packaging can proceed.
