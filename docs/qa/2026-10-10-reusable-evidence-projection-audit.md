# Reusable Evidence Projection — Read-Only Architecture Audit

**Date**: 2026-10-10
**Type**: Read-only architecture audit. No source, test, config, flag,
runtime, or data changes were made. The only file written is this
report. No investigation was run or replayed.
**Trigger**: WP-GS-B confirmed that the withdrawal destination address
existed upstream (RP payload + adapter records) but was dropped at the
Observation projection and the composer evidence table. PMO question:
was that an isolated defect, or does the per-tool evidence-projection
architecture create the same risk across other tools and evidence
types — and is a minimal reusable evidence-preservation capability
justified?

---

## 1. Executive Summary

The evidence projection is a **per-tool bounded whitelist** in
`project_observation` (`app/agent_loop.py`): every tool's ToolResult
payload is hand-mapped to a curated summary dict. This is a deliberate
grounding boundary (no raw payload passthrough), and it works well for
every tool whose summary carries the semantically critical fields.

The audit found:

- **One confirmed same-class loss besides the withdrawal address**:
  the **timeline** path. `finding_drilldown` view="timeline" returns
  complete timestamped events (event_id, timestamp, event_type,
  summary, importance, evidence/signal refs), but the observation
  projects ONLY `total_events` / `event_ids` / `truncated`, and the
  loop composer renders the count-only line 「Timeline reviewed: N
  events.」 — per-event content is never delivered to the user.
- **One confirmed structured-projection thinness (lower severity)**:
  transaction records in the evidence view carry structured fields
  (symbol/side/quantity/value/timestamp/risk_reason) in the adapter
  records, but the projected `record_rows` keep only
  record_id/timestamp/summary — the material facts survive only inside
  the summary PROSE string. Currently benign (the prose is readable
  and no claim consumes transaction columns), but it is the same shape
  as the withdrawal loss and would block any future claim-specific
  evaluation over transaction columns.
- **No material loss found** in: cross-account trade analysis
  (candidates carried in full with per-candidate supporting
  transactions + retrieval metadata), related-account/device-sharing
  analysis (ids/count/shared devices/cluster fields/shared-IP
  attributions all projected), signal_explain (rule
  name/threshold/trigger values/description/contribution; ML
  score/method/interpretation/features/attribution boundary; graph
  cluster fields — all projected), policy_lookup (match count,
  associated citations with document/section, finding-policy status),
  artifact (id/title/scope; body served via the session read model).

**Recommendation: Option 2 (minimal), narrowly scoped.** Codify the
pattern WP-GS-B established — evidence-producing tools MAY compute a
bounded, deterministic **named fact block** over their own records,
projected into the observation summary through one uniform helper —
and apply it to the timeline path as the first (and currently only
confirmed) candidate. Do NOT build a generic evidence serializer: the
whitelist remains the grounding boundary, fact blocks stay
domain-specific, and GS-D-style evaluators consume blocks per claim.
Option 1 (isolated fixes only) is defensible if product confirms the
timeline count-only answer is acceptable; the audit itself cannot make
that product judgment.

---

## 2. Scope and Evidence Limitations

- **In scope**: read-only inspection of the evidence representation
  and projection chain for seven representative paths; cross-path
  retention/provenance comparison; reuse recommendation.
- **Out of scope**: any code/test/flag/runtime change; re-running
  investigations; WP-FR/ES/CT and GS-C/D scope.
- **Evidence kinds**: static code inspection (current worktree, HEAD
  `58881a9` + CG/GSP working tree); preserved canonical-run store
  documents (U00020 manual-acceptance runs; U00010 GS rounds);
  recorded browser/E2E artifacts from WP-CG-4/5 and GS-A/B rounds.
- **Limitations**: runtime behavior is evidenced only where preserved
  runs exist; static reading cannot prove how the composer renders
  every observation shape. Where only static inspection supports a
  statement, it is labeled "static". No claim is made that data is
  absent merely because one adapter or projection lacked it.

## 3. Current Evidence-Flow Architecture Map

```
RP payload
  → adapter normalization (risk_platform.py):
      normalize_evidence_records (:788)  → withdrawal/transaction RECORDS
                                           (address ✓ preserved)
      normalize_timeline_events (:622)   → EVENTS (timestamp/type/summary/
                                           importance/refs)
      normalize_rule_signal / normalize_ml_signal / normalize_graph_signal
      network attribution (related/shared_device/cluster/shared_ip)
  → domain tools (finding_drilldown, cross_account_trade_analysis,
      related_account_analysis, policy_lookup, signal_explain,
      artifact_bundle) → ToolResult.data
  → project_observation (agent_loop.py:187+)  ← the central PER-TOOL
      BOUNDED WHITELIST                        (one branch per payload shape)
  → state.observations (persisted, whole-model JSON)
  → GSP-3 association (_actual_evidence_types → for_outputs)
  → GSP-4 evaluate_output (per-category postconditions over combined
      summary facts)
  → GSP-5 compose (per-output sections; composer tables/lists)
  → user-visible response + Activity projection
```

The whitelist branches verified in the current worktree:
risk_case_fetch, self_opposite_trades view, evidence view (records +
GS-B destination_identity block), timeline view, cross-account
analysis, related-account analysis, signal_explain (Rule/ML/Graph
payload shapes), policy_matches, artifact, plus the unrecognized-shape
marker (`payload_kind: "unrecognized"`).

## 4. Cross-Path Evidence-Retention Matrix

Legend: ✔ projected; ◐ partial/bounded; ✗ dropped at this layer; "—"
field not applicable. "Composer" = what the user-visible response
actually contains (verified in code and, where noted, in preserved
run responses).

| Path | Key source fields | Tool result | Observation | GSP-4 consumption | Composer | Assessment |
|---|---|---|---|---|---|---|
| Withdrawal records (evidence view) | record_id, timestamp, amount, asset, **address**, is_new_address, risk_reason | all preserved (+ GS-B destination_identity block) | record_rows {record_id,timestamp,summary,has_destination_identity} + **destination_identity block** (counts + groups + record-ID provenance) | (GS-D will evaluate the facet; today retrieval-only verdict) | **目的地 column** ✓ (GS-B) + summary prose | F — preserved post-GS-B |
| Transaction records (evidence view) | record_id, timestamp, side, quantity, symbol, value, risk_reason | all preserved (symbol/side/quantity/value as structured fields) | record_rows {record_id,timestamp,summary} — **structured tx fields dropped** (survive only inside summary prose) | same retrieval-only verdict as withdrawals | table renders record_id/timestamp/summary prose | E→C boundary: presentation-thin; no current claim consumes tx columns |
| Timeline events | event_id, timestamp, event_type, summary, importance, refs | all preserved | **ONLY total_events/event_ids/truncated** — per-event content dropped | none (timeline has no GSP-4 branch) | 「Timeline reviewed: N events.」 count-only | C→E: confirmed projection loss; per-event content never delivered |
| Self-opposite trades | ratio, rule echo, same-account tx records | preserved | semantics/rule_name/records via evidence-style rows | (withdrawal/tx branch) | ratio statement; data-absence honest when empty | F |
| Cross-account trade analysis | candidates {account_a/b, sides, symbol, tx id lists, timestamps, gaps, values, match_type, supporting tx details}; retrieval {truncated, total_matched, pages} | preserved in full | candidates ✓ (bounded), retrieval metadata ✓, criteria ✓ | OPPOSITE_TRADE_RELATIONS verdict (subject-membership over candidates; truncation gate) | full candidates table + boundary line | F |
| Related accounts / device sharing | related_accounts ids/count, shared_devices, cluster {name/type/members/risk score}, shared_ip_attributions | preserved | all projected (devices/count capped [:5]) | NETWORK_RELATIONS / SHARED_DEVICE_EVIDENCE verdicts (presence-based + count-consistency gate) | 18-account list + shared device + cluster 「为什么是风险信号」 section | F |
| Policy lookup | matches {citation_id, document, section, snippet, relevance}, associated_policy_refs, finding_policy_status | preserved | match_count, associated_count, associated_citations[:5] {citation_id, document, section}, finding_policy_status | POLICY_REFERENCES verdict (matches present) | 「政策依据」 with finding-level vs investigation-level separation | F (snippet not projected — policy refs render doc/section; acceptable for the supported purpose) |
| signal_explain (Rule) | rule name, description, threshold, trigger_values, contribution | preserved | rule_name/threshold/trigger_values/description[:200]/contribution | RULE_EXPLANATION verdict | observed-vs-threshold semantics (threshold may be null — source limitation) | F (A-class source gap for F3: RP threshold=None) |
| signal_explain (ML) | ml_score, detection_method, score_interpretation, available_feature_values (13), attribution_available | preserved | all projected (features [:12]) | ML_SIGNAL_EXPLANATION verdict | score semantics + feature context + attribution boundary | F |
| signal_explain (Graph) | cluster fields | preserved | cluster fields | GRAPH_CLUSTER_FACTS verdict | cluster attribution | F |
| Artifact | artifact_id, title, scope; body | body preserved in the session read model | id/title/scope only | CASE_SUMMARY verdict | bundle reference; full body via read model | F (different surface by design) |

## 5. Confirmed Field-Loss Points and Alternative Explanations

1. **Withdrawal destination address** — Confirmed (C-class; the audited
   defect). Dropped at `project_observation` record_rows and the
   composer table; FIXED by WP-GS-B (destination_identity block +
   目的地 column). Alternative explanation (intentional bounded display)
   rejected: the 20-row cap bounds DISPLAY, yet the address was absent
   even from the full block — an omission, not a cap.
2. **Timeline per-event content** — Confirmed (C-class + E-class).
   `project_observation` timeline branch keeps only
   total_events/event_ids/truncated; the loop composer renders a
   count-only line. Static inspection further shows the timeline
   answer's per-event content was never rendered on the loop path.
   Alternative explanation: product intent may treat the timeline as
   count-only — the audit cannot confirm product intent; labeled a
   confirmed projection loss with product impact unconfirmed.
3. **Transaction structured columns** — Confirmed (E-class today, C-class
   risk for future claims): structured tx fields survive only inside
   summary prose; same shape as the withdrawal loss but no current
   consumer needs the columns. Not a defect today; flagged as the
   pattern to avoid repeating.
4. **Policy match snippets** — E-class, justified: the conversational
   surface renders document/section (the full set rides in artifacts'
   Policy References per the association contract); the snippet cap is
   a display bound consistent with the observed runs.
5. **Not losses (verified)**: cross-account candidates, related-account
   cluster/shared-IP facts, signal_explain Rule/ML/Graph payloads, and
   artifact references are projected in structured form sufficient for
   their GSP-4/GSP-5 consumers and the user-visible answers observed in
   preserved runs (U00010 J1-J4, U00020 manual runs).

## 6. Provenance and Downstream-Consumer Analysis

- **GS-B destination_identity**: groups carry `destination_ref`,
  `address`, `shape`, `record_ids` — full bidirectional provenance
  (record → group, group → records). The observation's bounded copy
  keeps groups with record_ids; raw values remain in the tool-result
  records.
- **Cross-account candidates**: each candidate embeds its supporting
  transactions (id/account/symbol/side/price/quantity/value/timestamp)
  — the strongest provenance in the system; GSP-4's
  OPPOSITE_TRADE_RELATIONS branch consumes exactly these facts.
- **Related accounts**: `shared_ip_attributions` keyed per account with
  the count-consistency gate (related_accounts_count vs list length) —
  provenance + completeness gate in one.
- **Policy**: `associated_policy_refs` drive the associated/other
  separation — the association contract is explicit.
- **Timeline**: event_ids give id-level provenance only; content
  provenance (which event says what) exists in the tool result but is
  unreachable downstream — the same shape as the pre-GS-B withdrawal
  loss.
- **signal_explain Rule**: `threshold`/`trigger_values` are projected
  but the RP source itself supplies `threshold=None, trigger_values={}`
  for U00020's rules (A-class source limitation — the F3/F6 threshold
  question cannot be answered from current RP data; confirmed in the
  read-only manual-acceptance audit).

## 7. Comparison of the Three Architecture Options

| Criterion | Option 1: targeted fixes only | Option 2: minimal named-fact-block convention | Option 3: generic evidence framework |
|---|---|---|---|
| Reuse across confirmed paths | none; per-site edits | GS-B block pattern generalizes (timeline facts, future facets) | high in theory |
| Source provenance preserved | yes (per fix) | yes (blocks computed from tool's own records) | at risk (passthrough tends to blur source vs derived) |
| Prevents accidental field loss | only where noticed | prevents the class for block-covered facts; whitelist still governs the rest | claims to; historically such frameworks over-promise |
| Observation/GSP schema compatibility | full | additive named blocks; GSP-4 reads blocks per claim facet; whitelist untouched elsewhere | large migration |
| Over-retention risk | none | bounded blocks (counts + provenance; raw values stay put) | high (untyped field bags) |
| Scope/regression impact | minimal per fix | small (one helper + projections per evidence type) | large, cross-cutting |
| Justified by confirmed paths? | yes only if timeline is deemed acceptable as-is | **yes: two confirmed C-class projection losses of the same shape** (withdrawal address — fixed; timeline per-event content) | no |

## 8. Recommendation and Evidence-Based Rationale

**Option 2, minimal and convention-first.** Rationale:

- Exactly TWO confirmed same-shape projection losses exist
  (withdrawal address — fixed by GS-B; timeline per-event content —
  confirmed thin). That is enough to justify codifying the GS-B block
  pattern as the standard way evidence-producing tools expose
  record-level facts; it is not enough to justify a generic framework.
- The GS-B pattern already demonstrates the right separations
  (source records / deterministic derived facts / provenance /
  presentation) and composes with the whitelist rather than replacing
  it.
- The timeline is the single concrete next application: a
  `timeline_facts` bounded block (per-event
  timestamp/type/summary/importance, first-occurrence ordered) would
  let the composer render actual events instead of a count, and let
  future claim-specific evaluators consume per-event facts — same
  shape, same rules as GS-B.
- Option 1 remains valid for everything else: the audit found no other
  material loss, and the whitelist's per-tool curation is a grounding
  strength worth keeping.

## 9. Proposed Minimum Design Boundary (if reuse is pursued)

- Convention: "evidence tools MAY attach bounded, deterministic NAMED
  FACT BLOCKS to their success payloads; `project_observation` projects
  them through one uniform bounded helper; claim facets (GS-C/D) and
  composers consume blocks; the whitelist remains the default."
- Block contract: domain-specific fields; deterministic ordering;
  record/event-ID provenance; completeness/truncation flags; no
  conclusions; no raw-payload passthrough.
- First candidate: timeline facts (per-event
  timestamp/type/summary/importance, bounded like record_rows).
- Explicit non-extension: no auto-projection of unrecognized payload
  keys; the `payload_kind: "unrecognized"` marker stays.
- Dependency: the timeline application needs a product decision on the
  timeline answer's expected content (count-only vs rendered events) —
  outside this audit's authority.

## 10. Compatibility and Regression Risks

- Additive observation keys are backward-compatible (whole-model JSON;
  legacy documents load with fields absent — established pattern).
- Composer wording changes (timeline) would be user-visible → browser
  E2E required per project discipline.
- The 20-row display cap and group caps must stay explicit
  (`rows_truncated` discipline) so bounded blocks are never mistaken
  for complete sets.
- Regression surface: existing timeline/evidence tests assert
  count-only lines — those expectations would change with any timeline
  rendering work (flag-scoped if needed).

## 11. Unresolved Questions

1. Product intent for the timeline answer (count-only vs rendered
   events) — determines whether the timeline projection loss is a
   defect or the supported behavior. (Unconfirmed; needs product
   decision.)
2. RP-side uniqueness guarantees for `withdrawal_id` (documented in
   the GS-B close-out as not established in this repo).
3. RP rule/threshold configuration surface for feature-detected
   findings (F6-class) — WP-ES boundary, confirmed absent in current
   RP data by the manual-acceptance audit.
4. Whether any future claim will need structured transaction columns —
   flagged as the pattern to avoid repeating; no current consumer.

## 12. Statement

This was a read-only audit. No source code, tests, configuration,
flags, runtime state, data, or existing documents were modified; no
tests were executed; no services were started or stopped; no
investigation was submitted. The only file written is this report
(`docs/qa/2026-10-10-reusable-evidence-projection-audit.md`), which
did not previously exist.
