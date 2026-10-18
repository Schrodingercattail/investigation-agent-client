# AL-3.5.1 Design — G1 Target Validity Guard — v1

**Date**: 2026-10-05
**Status**: DESIGN ONLY — no code, prompts, thresholds, GroundTruth,
scenarios, UI, or evaluation changed by this document.
**Input evidence**: AL-3.3 baseline `al3-ca2ec15c` @ `58881a9` (S11
wrong-target execution), AL-3.4 gap analysis
(`docs/evaluations/al34-gap-analysis-58881a9-v1.md`), code forensics
cited inline, offline resolver probes (2026-10-05), and the frozen M2
evidence base (SP-M2-1C, 60 frozen runs).

---

## 1. Problem Statement

In baseline S11, the user asked about a topic that does not exist in
the case (「虚拟币洗钱」). The system resolved the request to finding F1
(ML Pattern Detection), adopted F1 as the investigation focus, executed
finding-scoped tools against it four times, and answered confidently
with F1's evidence. The failure is not answer quality — every fact
about F1 was accurate. The failure is that **an LLM-proposed target
binding with no deterministic supporting evidence was admitted into
investigation**, producing the chain:

```
wrong target  →  wrong evidence  →  confident wrong-topic answer
```

The precise problem definition (deliberately NOT "the normalizer often
misidentifies"):

> **The system lacks target-validity admission control: a semantic
> proposal that cannot show deterministic evidence linking the user's
> named subject to the proposed finding can still be adopted as the
> investigation target and drive finding-scoped execution.**

## 2. Root Cause — where the wrong finding is accepted (code-traced)

The full chain for a `/goal` request (production wiring verified):

```
POST /goal (api/investigations.py:404)
  → context loaded (+ optional client context_action = explicit selection)
  → AgentLoop.run  (findings resolve INSIDE the loop, QA-04;
                    api/investigations.py:435-439)
    → findings_provider()                      [RP dependency gate]
    → self.resolver.resolve(goal, ctx, findings)
       (agent_loop.py:1064)  ← ContextResolver.resolve:
         P0  intent routing                    (deterministic)
         P1/2 existing/explicit focus → _keep  (deterministic; authoritative)
         case-level matchers                   (deterministic)
         P3  authoritative-title token match   (deterministic, EN titles)
         P3.5 M2 semantic stage                (LLM proposes concept;
                                               system grounds: concept ∈
                                               candidate.core_concepts,
                                               unique→RESOLVED, multi→AMBIGUOUS,
                                               zero/non-OK→None→fall-through)
         P4  candidate-restricted LLM assist   (_llm_resolve, ctx res:
                                               LLM returns RESOLVED+id+
                                               confidence; the ONLY gate is
                                               the LLM's own "confidence
                                               == high" self-report;
                                               ctx res:511-527, 775-870)
         P5  UNRESOLVED
    → RESOLVED ⇒ state.focused_finding_id adopted UNCONDITIONALLY
      (agent_loop.py:1123; no validity question is ever asked)
    → planner (explain_signal on focus) → executor (finding-scoped tools)
    → composer answers "关于发现 F1…"
```

**The acceptance happens at the P4→adopt boundary.** The M2 semantic
stage (P3.5) already admits only grounded proposals (governed concept
equality + verbatim `matched_fragment`, evidence recorded in
`semantic_evidence`). The assist stage (P4) is the only resolution
route that can bind a finding with **zero deterministic evidence** —
its admission signal is the same model's self-reported confidence
string. On the baseline day the assist returned
`RESOLVED / F1 / high`; the loop adopted it. Nothing downstream — loop
target adoption (`agent_loop.py:1123`), planner, executor, composer —
re-asks whether the target corresponds to what the user actually
named. Offline probe (2026-10-05, production wiring
`ContextResolver(llm_assist=True, semantic_catalog=TaskStoreCatalogProvider(store))`):

| Probe request | Outcome | Route | Evidence |
|---|---|---|---|
| 「谈谈虚拟币洗钱这个风险项」 (S11) | AMBIGUOUS (today; baseline day: RESOLVED→F1) | P4 assist | none — self-report only |
| 「ML Pattern Detection什么意思…」 (S04) | RESOLVED→F1 | P3 title match | verbatim EN title (deterministic) |
| 「高频提现…证据…」 (S05) | RESOLVED→F3 | P3.5 semantic | concept `HIGH_WITHDRAWAL_FREQUENCY`, fragment 「高频提现」, grounded `core_concepts` |
| 「高频交易…明细」 (S06) | RESOLVED→F7 | P3.5 semantic | concept `HIGH_TRADING_FREQUENCY`, fragment 「高频交易」 |
| 「为什么说共享设备是风险项」 fresh (S03/S07) | AMBIGUOUS | P4 assist (ambiguous branch) | none |

Layer responsibilities today:

| Stage | Role | Guard present? |
|---|---|---|
| A. Candidate resolution (P3/P3.5/P4) | propose + (P3.5) ground | P3/P3.5: deterministic evidence. **P4: none** |
| B. Target selection (RESOLVED vs AMBIGUOUS vs UNRESOLVED) | choose | LLM self-report at P4 |
| C. **Target authorization / admissibility** | system-side admission | **MISSING on the initial-resolution path.** The implemented WI-5 gate (`app/admissibility.py`) covers only the *refinement* path (/turn, flag OFF) and its axis is *governance eligibility* (metadata status), not *subject validity*; its own header notes "the loop path has no refinement at all" |
| D. Execution | planner/executor | honors `state.focused_finding_id` blindly (correct — it must be able to trust admission) |

Prior-art alignment (frozen, respected): the WP-M2-1 Safe-Target
Refinement contract (`docs/baselines/2026-09-24-wp-m2-1-...-v1.md`)
already establishes "the LLM may propose; only the system may admit"
with signals CS / discriminating evidence / unique support /
confidence-as-necessary-filter / final admissibility (A1–A4) — designed
for the *refinement* path, **never implemented**. SP-M2-1C (60 frozen
runs) empirically established that confidence thresholds cannot do this
job (correct 0.72–0.98 / unsafe 0.72–0.88 / wrong-target 0.78–0.88
fully overlap) — **"no threshold-only fixes" is a binding constraint on
this design.**

## 3. Proposed Architecture

Two independent, composing layers; both system-controlled; no new
semantic authority (AL-2.11 single-entry constraint preserved — the
guard validates evidence against the SAME governed candidate metadata
and concept registry; no lexicon, no keyword table, no
`finding_lexicon` restoration).

```
                    ┌──────────────────────────────────────────────┐
 user request ─────►│ resolution (existing, unchanged priorities)  │
                    │  P1/2 explicit context   → evidence: context │
                    │  P3  title match         → evidence: verbatim│
                    │  P3.5 semantic grounding → evidence: concept │
                    │  P4  assist              → evidence: REQUIRED│
                    │      (Layer A)               (new, validated)│
                    └───────────────┬──────────────────────────────┘
                                    │ ResolutionOutcome (+ evidence record)
                    ┌───────────────▼──────────────────────────────┐
                    │ Layer B — TargetValidityGuard (loop-side,    │
                    │ pre-planning, pre-execution choke point)     │
                    │ system-only predicate over: goal text,       │
                    │ resolution evidence, governed metadata,      │
                    │ GoalRepresentation                           │
                    │ → TARGET_VALID / AMBIGUOUS / UNGROUNDED /    │
                    │   INVALID                                    │
                    └───────────────┬──────────────────────────────┘
                                    │ VALID only
                          planner → executor (unchanged)
```

### 3.1 Layer A — semantic grounding / candidate validation (resolution layer)

**Placement**: `context_resolution.py::_llm_resolve`, RESOLVED branch
only. Priorities P1–P3.5 are untouched.

**Change**: the assist's proposal must carry an **evidence record**
(WP-M2-1 contract §7 shape, applied to the initial path):

```json
{"resolution": "RESOLVED|AMBIGUOUS|UNRESOLVED",
 "finding_id": "...",
 "candidate_finding_ids": [...],
 "confidence": "high|medium|low",
 "evidence": {
    "request_fragments": ["虚拟币洗钱"],      // verbatim substrings of the request
    "target_terms": ["..."],                 // terms the fragments map to
    "per_candidate_support": [{"finding_id":"F1","support":"Supported|Not Supported|Unknown"}]
 },
 "reason": "..."}
```

**System validation (deterministic, no LLM)**:
- V1 grounding: every `request_fragments` entry is a verbatim substring
  of the request (mechanical check), and every `target_terms` entry
  occurs in the proposed target's governed semantics — title tokens,
  description text, or the candidate's `core_concepts` (via the
  registry, matching the P3.5 grounding rule exactly).
- V2 non-equivalence: the same fragment→term mapping must NOT hold
  verbatim for a competing candidate whose governed semantics contain
  those terms equally (else the set is not uniquely supported).
- V3 support predicate: computed by the system over
  `per_candidate_support` (US-1..US-4 of the WP-M2-1 contract):
  `Supported ∧ Supported ⇒ NOT unique`; Unknown counts as not-unique
  for admissibility and is recorded distinctly (UNKNOWN ≠ FALSE).
- V4 confidence remains a **necessary filter only** (existing
  `!= "high" → AMBIGUOUS` behavior unchanged; it can never bypass V1–V3).

If validation fails, the RESOLVED proposal **downgrades** —
AMBIGUOUS (clarify with the candidate list) when any competitor is
co-supported, otherwise UNRESOLVED. It never upgrades to execution.

**Effect on today's live behavior**: none of P1–P3.5 changes; P4
resolutions survive only with validating evidence (e.g.
description-level references like "6 天…54 笔" → F2 description terms).
Unevidenced preference-bindings — the S11 corner — become clarifications.

### 3.2 Layer B — pre-execution target guard (goal/target layer + loop)

**Placement**: new module `app/target_validity.py` (pure, deterministic,
no LLM). Consumed once in `agent_loop.py` immediately after the
resolution block and before collaborators/planning/any tool call —
the single choke point through which every `/goal` run passes (this
also covers the resume path). The `/turn` path gains the same protection
via Layer A inside the resolver (turn and loop share `ContextResolver`).

**Inputs (all system-visible, no hidden chain-of-thought)**: goal text;
`ResolutionOutcome` (status, focus source, evidence kind,
`semantic_evidence`, candidates); adopted `focused_finding_id`;
governed candidate metadata (title/description/`core_concepts`/status)
via the existing catalog; `GoalRepresentation` (goal_type/subject —
auxiliary only; it is itself LLM-proposed and never load-bearing).

**Decide (state machine, §6)** — verdicts:

- **TARGET_VALID** (execute) iff any one system-evidence kind holds:
  - E1 explicit selection: `focus_source == USER_SELECTED`
    (PMO decision: explicit selection is a separate path, never
    guard-gated);
  - E2 verbatim authoritative title match (P3 outcome);
  - E3 governed concept grounding (P3.5 outcome: proposed concept ∈
    target's `core_concepts`, recorded in `semantic_evidence`);
  - E4 context continuation: existing focus preserved via `_keep`
    (P1/2 — the conversation established the target; follow-ups never
    re-litigate);
  - E5 Layer-A-validated assist proposal (evidence record passed V1–V3).
- **TARGET_AMBIGUOUS** → clarify: ≥2 candidates co-supported
  (existing AMBIGUOUS boundary, unchanged shape).
- **TARGET_UNGROUNDED** → clarify: a RESOLVED binding with **no**
  evidence kind E1–E5 (the S11 corner). The response must be honest
  non-claims: "I couldn't reliably determine which finding you mean"
  + candidate list / selection guidance. **It must NOT assert the topic
  does not exist** (cannot-prove ≠ prove-absent; UNKNOWN ≠ FALSE).
- **TARGET_INVALID** → blocked-with-reason: positive contradiction only
  — the goal's named subject verbatim grounds to a *different*
  candidate's governed semantics (or names a different finding id).
  Rare by construction; v1 may log-and-clarify instead if the
  contradiction check is not deterministic enough (open question §15-Q3).

**Deliberate non-goals for the state machine**: RE_PLAN is not a target
validity outcome (target admission is not a planning matter; RE_PLAN
keeps its execution-scope meaning). No new stop_reason vocabulary:
UNGROUNDED maps to the existing `CONTEXT_UNRESOLVED` boundary (stop
semantics, GT expectations, and dashboards unchanged); AMBIGUOUS maps
to `CLARIFICATION_NEEDED` as today.

### 3.3 Flag and composition

- New setting `ENABLE_TARGET_VALIDITY_GUARD` (default **OFF**; OFF =
  byte-for-byte today's behavior, same convention as
  `ENABLE_SAFE_SELECT_ADMISSIBILITY` / `ENABLE_TARGET_GOAL_REFINEMENT`).
- Independence: the WP-M2-1 admissibility gate (governance eligibility:
  COMPLETE-only autonomous selection) and this guard (subject validity:
  does the binding correspond to the request) are **orthogonal axes on
  the same admission point**. They compose (both must pass when both
  enabled); neither substitutes for the other. F1 was
  governance-eligible, so S11 would have passed a governance-only gate —
  only subject validity catches it.

## 4. Critical design question — is "matched fragment / title–summary
overlap threshold" sufficient as the deterministic guard?

**No — and the project's own frozen evidence already rejects it.**
Comparison of candidate mechanisms:

| Mechanism | Recall risk | False-positive risk (blocks valid) | False-negative risk (admits S11) | Paraphrase robustness | CJK robustness | Complexity |
|---|---|---|---|---|---|---|
| **A. Lexical overlap** (fragment vs title/summary) | High — valid CJK requests share zero surface tokens with EN titles | **High**: 「高频提现」 vs "High withdrawal frequency" = 0 overlap; a threshold low enough to pass S05 blocks nothing | **High**: any accidental surface token sharing admits; threshold tuning is weather-dependent | Poor | **Poor** (no surface overlap by construction) | Low |
| **B. Governed concept equality** (normalizer concept ∈ candidate `core_concepts`) | Medium — bounded by registry coverage (10 concepts / 8 candidates today) | **Low** — exact set membership, no tuning | **Low** — a nonexistent topic has no registry concept; force-matched concepts ground nowhere or ambiguously | **Good** — concepts carry the semantics (「高频提现」→HIGH_WITHDRAWAL_FREQUENCY→F3, probe-verified) | **Good** (same mechanism) | Low (mechanism exists at P3.5) |
| **C. Registry compatibility** (hierarchy / broader concept relations) | Improves B's recall | Low-Med — depends on relation curation | Low | Good | Good | Med (registry curation; new relation data) |
| **D. Candidate uniqueness** alone | n/a | n/a | **Total** — S11's binding WAS unique; uniqueness ≠ validity (SP-M2-1C finding 8: preference ≠ separability) | n/a | n/a | Low |
| **E. Semantic proposal + deterministic grounding evidence** (B ⊕ validated evidence record ⊕ uniqueness ⊕ confidence-as-filter) | Medium (evidence-linkable references pass; pure-paraphrase falls back to B or clarifies) | Low-Med — only unevidenced bindings blocked | **Low** — admission requires evidence by construction | Good | Good | Med |

**Verdict**: the minimal robust combination is **E**, assembled almost
entirely from existing, already-proven parts: governed concept equality
(B — live at P3.5 today), verbatim title match (P3), the WP-M2-1 §7
evidence record + system validation for the assist path, uniqueness as
a system-computed set predicate, and confidence as a necessary filter
only. A bare overlap threshold (A) is **rejected** — it fails the
baseline regression set in both directions and re-opens the exact
failure ("threshold-only fixes") that SP-M2-1C closed. Mechanism (C)
is deferred registry curation, not part of v1. Mechanism (D) is
necessary but never sufficient and is folded into E as the set
predicate.

## 5. LLM Responsibilities vs System Responsibilities

| Responsibility | Owner |
|---|---|
| Interpret the request; propose target concept / finding | LLM (normalizer / assist) — unchanged role |
| Propose the evidence record (fragments, term mapping, per-candidate support) | LLM (assist schema extension) |
| Validate fragments verbatim-in-request | System (substring check) |
| Validate term membership in governed candidate semantics / `core_concepts` | System (registry + candidate metadata) |
| Compute unique-support set predicate; treat Unknown as not-unique | System (reuses `SupportEvaluator`/`AdmissibilityGate` logic shapes) |
| Decide TARGET_VALID / AMBIGUOUS / UNGROUNDED / INVALID | System (`TargetValidityGuard`, pure function) |
| Confidence | LLM self-report; necessary filter only; can never bypass evidence |
| Execute / clarify / block | System (loop boundary) |
| Any hidden chain-of-thought as decision input | **Prohibited** — guard inputs are the goal text, structured evidence, and governed metadata only |

## 6. State Transitions

```
request ──► resolution ──► Layer A (assist evidence validation, P4 only)
                              │ fail → AMBIGUOUS / UNRESOLVED (clarify path)
                              ▼
            Layer B TargetValidityGuard
              E1..E5 any hold ────────────► TARGET_VALID      → execute
              ≥2 co-supported ────────────► TARGET_AMBIGUOUS  → clarify (existing boundary)
              RESOLVED, no evidence kind ─► TARGET_UNGROUNDED → clarify (CONTEXT_UNRESOLVED)
              positive contradiction ─────► TARGET_INVALID    → blocked-with-reason
              any UNKNOWN ────────────────► treated as UNGROUNDED (UNKNOWN ≠ FALSE;
                                              cannot-prove ≠ prove-absent;
                                              insufficient evidence ⇒ no execution)
```

Failure semantics hierarchy (binding):

```
wrong-target execution  ≫  unnecessary clarification  ≫  slightly incomplete answer
```

Clarify-on-uncertainty is correct behavior; over-blocking every
ambiguity is a defect. The guard is designed to add friction at
exactly one point — unevidenced preference-bindings — and nowhere
else (E1–E5 pass the entire existing regression set without new
interaction).

## 7. Regression Matrix (designed before implementation)

| # | Case | Input class | Expected after guard | Protected by |
|---|---|---|---|---|
| A | Wrong-topic refusal (S11-style) | subject-naming, no governed linkage | TARGET_UNGROUNDED → clarify; **0 finding-scoped tool calls; 0 F1-content answers** | Layer A V1–V3 + Layer B |
| B | Valid canonical finding (S04) | verbatim EN title | TARGET_VALID (E2) → execute | E2 |
| C | Valid CJK paraphrase (S05/S06) | subject maps via governed concept | TARGET_VALID (E3) → execute | E3 / P3.5 |
| D | Ambiguous candidates (fresh S03 shape) | concept grounds to F5+F6 | TARGET_AMBIGUOUS → clarify (unchanged) | existing + V2 |
| E | Follow-up with established context (S07) | focus already authoritative | E4 → execute, **no new clarification** | E4 (P1/2 precede guard) |
| F | Finding + Loop (S10/S14 loop entry) | valid target enters loop | TARGET_VALID → loop; bounded empty handling unchanged | E3/E2 + guard non-interference after VALID |
| G | Multi-intent (S12) | case-level related-account intent | case-level matcher → no finding target → guard not engaged | P0/case-level route |
| H | Related-account case-level (S08) | case-level | unchanged | same |
| I | Insufficient evidence (S10) | valid target, empty drilldown | execution + bounded honest answer unchanged (guard never touches post-admission behavior) | guard scope |
| J | Similar-but-not-unique candidates | ≥2 co-supported | no unsafe selection → clarify | V2/V3 set predicate |

Additional guard-native cases (new unit tests, no LLM): explicit
selection (E1) never blocked; assist proposal with fabricated fragments
(fails V1); assist proposal mapping fragments to terms absent from
target metadata (fails V1); two candidates equally supported (V2/V3 →
AMBIGUOUS); Unknown support (V3 → not unique → clarify);
resume-path run re-checked at Layer B.

## 8. Quantitative Acceptance Proposal

- **S11 request × N ≥ 5** (fresh investigations, real LLM, real RP):
  - 0 unsafe wrong-target executions (no finding-scoped tool call on a
    non-corresponding target);
  - 0 confident wrong-target answers (no F1-content answer to the
    虚拟币洗钱 question);
  - expected terminal: BLOCKED / clarify-family with honest
    non-claiming message; per-run variance allowed only among
    safe outcomes (ambiguous vs unresolved is weather; unsafe is not).
- **Regression set S04/S05/S06/S07/S08/S10/S12/S14 × N ≥ 3**:
  - 0 meaningful false BLOCKs (no VALID target denied; S04/S05/S06/S14
    terminal statuses unchanged);
  - no NEW necessary clarifications beyond scenario D's agreed
    ambiguity (S03-shape) and baseline behavior;
  - evidence correctness unchanged (hard gates: withdrawals 7/7,
    transactions 64/64, accounts 18/18, candidates 6, no prohibited
    content);
  - S10/S14 stop/iteration behavior unchanged.
- **Deterministic layer**: guard state-machine unit/contract tests
  (table §7 + guard-native cases) — 100% branch coverage of the
  predicate; flags-OFF identity test (responses byte-equal with guard
  disabled).
- **Not "pytest passes" alone** — required end surfaces:
  - API E2E on the canonical stack for scenarios A/B/C/E/F/I/J above;
  - representative browser E2E where user-visible behavior changes:
    one journey for the S11-style clarify card (honest message renders,
    no execution, candidate guidance visible) and one regression journey
    (S04 or S06 answer renders as before; Activity panel unchanged for
    valid runs).

## 9. Implementation Proposal Summary

| Concern | Placement |
|---|---|
| Assist evidence-record schema + system validation (V1–V4) | `context_resolution.py::_llm_resolve` (Layer A) |
| TargetValidityGuard state machine (pure, deterministic) | new `app/target_validity.py` (Layer B) |
| Guard invocation choke point | `agent_loop.py` after resolution, before collaborators/planning (also covers resume) |
| Governed metadata / concept access | existing `TaskStoreCatalogProvider` (no new store; "no second database" respected) |
| Flag | `ENABLE_TARGET_VALIDITY_GUARD` (default OFF; OFF = byte-for-byte today) |
| Telemetry | `target_validity.decided` events + evidence in `target_resolution` record (closes the known select-path evidence asymmetry) |
| Stop vocabulary | unchanged (UNGROUNDED → `CONTEXT_UNRESOLVED`; AMBIGUOUS → `CLARIFICATION_NEEDED`) |
| Settings/identity | expose flag + coverage in `/api/v2/runtime/identity` as with sibling flags |

Definition of Done (AL-3.5.1 implementation cycle):

1. Flags OFF: byte-identical behavior (identity + spot E2E).
2. Guard unit/contract tests green (§7 matrix + native cases).
3. S11 ×N≥5 acceptance met (0 unsafe / 0 confident-wrong).
4. Regression set ×N≥3 acceptance met (0 false blocks; hard gates unchanged).
5. Browser journeys: S11-style clarify renders honestly; one valid
   scenario unchanged in the UI.
6. Docs: AL-2.11 semantic-entry note updated (guard validates against
   the same governed metadata; no new semantic authority), gap-analysis
   G1 marked designed/implemented.
7. Report separates: unit-tested · integration/API E2E · browser-validated ·
   what remains unverified.

## 10. Risks

1. **Over-blocking valid assist resolutions** (description-level
   references whose evidence is real but phrased loosely): mitigated by
   evidence-record schema (LLM supplies the mapping; system checks
   membership), and the failure mode is a clarification (safe side),
   with telemetry to find over-blocks; N≥3 regression run required.
2. **Registry coverage gaps**: a currently-resolving paraphrase whose
   concept is missing would now clarify (today the assist would have
   guessed — sometimes wrongly). Mitigation: measure on the regression
   set; governed-metadata completion is the standing M2 track, not a
   guard change.
3. **Weather in assist evidence quality**: variance can only move
   outcomes toward clarification (system validation is deterministic);
   it cannot reintroduce unsafe execution while the guard is on.
4. **Interaction with future WP-M2-1 enablement**: orthogonal axes
   designed to compose; rollout order and combined telemetry need a
   decision (§11-Q5).
5. **Latency**: Layer A adds validation only (no extra LLM call if the
   evidence record rides the existing assist response — preferred).

## 11. Open Questions

1. Exact assist JSON schema (single call carrying the evidence record —
   preferred — vs a second validation call; single-call keeps latency
   and budget flat but must preserve the 2048-token lesson).
2. `target_terms` validation surface: title + description + core_concepts
   verbatim only, or token-normalized? (v1 proposal: verbatim substring
   OR exact concept id; token normalization only if regression shows
   over-blocking.)
3. TARGET_INVALID v1 scope: implement positive-contradiction detection
   now, or log-and-clarify first? (Recommend: clarify-first, add INVALID
   when a deterministic contradiction rule proves itself in telemetry.)
4. Should Layer B also cover `/turn` (its own post-resolution choke
   point) or is Layer A sufficient for the turn path? (Turn and loop
   share the resolver; Layer A covers both for assist-origin bindings;
   Layer B on the loop covers resume/future callers.)
5. Rollout ordering vs `ENABLE_SAFE_SELECT_ADMISSIBILITY`, and whether
   `target_validity.decided` telemetry feeds the WP-M2-1 clarification
   ledger when both flags are on.

## 12. Non-Goals

- No UI changes (clarify/blocked cards already render).
- No GroundTruth, scenario, classification, or evaluator changes; the
  S11 baseline result stands as recorded.
- No restoration of `finding_lexicon`; no keyword/alias hack for
  「虚拟币洗钱」 or any finding; no threshold-only fix (rejected by
  SP-M2-1C evidence).
- No new semantic authority: the guard validates against the existing
  governed candidate metadata and concept registry (AL-2.11 single
  entry preserved).
- No changes to P1–P3.5 resolution behavior, RE_PLAN semantics,
  stop-reason vocabulary, or post-admission loop behavior.
- No implementation in AL-3.5.1 (this document).
- No MCP, no second database, no hidden-CoT dependence.

---

*Forensic probes: production-wired offline resolver runs, 2026-10-05
(requests: S11 ×1 completing, S04/S05/S06/S07/S12 shapes; findings via
`risk_case_fetch('U00010')`; catalog via `TaskStoreCatalogProvider`);
baseline evidence: `al3-ca2ec15c` responses/activities attachments.*
