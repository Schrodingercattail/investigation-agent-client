# AL-3 — Evaluation Design v2

**Date**: 2026-10-03
**Status**: DESIGN — supersedes v1
**Baseline**: git `4d00ea4` (AL-2 governed investigation agent track)
**Supersedes**: `2026-10-03-al3-evaluation-design-v1.md`

---

## Table of Contents

1. Purpose
2. Baseline
3. Design Review Findings (v1 → v2)
4. Evaluation Architecture (Three Layers)
5. Agent Quality Model (Revised)
6. Ground Truth / Evaluation Oracle
7. Scenario Matrix (Revised)
8. Evidence Correctness vs Answer Correctness
9. Premature Stopping vs Bounded Completion
10. Evaluation Isolation & Reproducibility
11. Evaluation Rubric
12. Metric Aggregation
13. Deterministic vs LLM Judge vs Human Review
14. Evaluation Harness Design
15. Failure Taxonomy
16. Unknown Unknowns / Risks
17. AL-3 Phase Plan
18. AL-3.1 Design Freeze Checklist
19. Definition of Done
20. Open Questions
21. Non-Goals

---

## 1. Purpose

AL-3 answers: **does the Agent help an analyst investigate a case?**

Not "do the tools work" (AL-2 proved that). "Does the user get a
correct, honest, complete, bounded, and useful answer?"

## 2. Baseline

Frozen AL-2 baseline at git `4d00ea4`. Working tree clean.

Runtime modules (~7,600 lines): context_resolution, goal_resolution,
semantic_resolution, candidate_metadata, planner_v2, executor_v2,
agent_loop, investigation_service, 7 domain tools, skills, M2 seeding.

Existing eval: 22 scenarios (17 scripted + 5 live), 11 planner
scenarios, 20 deterministic checkers, TurnView capture, basic report.
88 backend test files (15 AL-2 series).

## 3. Design Review Findings (v1 → v2)

The v1 design was reviewed against six criteria. Each finding and its
v2 resolution:

| # | v1 Gap | v2 Resolution |
|---|---|---|
| 1 | No formal Ground Truth / Oracle: LLM judge has no definition of "correct" beyond rubric prompts | §6 defines per-scenario Ground Truth (expected finding, evidence set, key facts, prohibited claims, boundedness behaviour). The LLM judge scores against this oracle, not against its own judgement. |
| 2 | No formal layered architecture: all scenarios implicitly target the same evaluation depth | §4 defines three layers (L1 deterministic, L2 real-agent execution, L3 browser journey) with mapping rules |
| 3 | Evidence correctness and answer correctness treated as one metric | §8 formalises six independent evidence-vs-answer correctness dimensions |
| 4 | Premature stopping mentioned as a risk but not designed into the rubric or scenario set | §9 defines a rubric dimension and a dedicated scenario |
| 5 | Evaluation isolation mentioned as a risk but no formal isolation strategy | §10 defines fresh-state guarantees, version tracking, and contamination prevention |
| 6 | Metric aggregation implied a single quality score | §12 defines hard gates vs diagnostic signals vs non-aggregatable observations |

## 4. Evaluation Architecture (Three Layers)

### 4.1 The Three Layers

```
Layer 1: Deterministic / Contract
  Runs against fixtures (ScriptedLLM, ScriptedToolProvider, known payloads)
  Checks: resolution, routing, planning, argument injection, outcome
  classification, boundary compliance, convergence, deduplication
  Cost: per-test < 1s, fully reproducible
  Tools: pytest + eval/scenarios.py + eval/checkers.py

Layer 2: Real-Agent Execution
  Runs against canonical backend (:8001) with real LLM + real RP (:8000)
  Checks: answer quality (deterministic key-fact checks + LLM-as-judge),
  tool-use efficiency, evidence completeness, honesty, boundedness,
  cross-tool integration
  Cost: per-scenario 30s–3min, non-deterministic (LLM weather)
  Tools: HTTP client → /api/v2/investigations/{id}/goal + /turn
         + trace capture from state/activity

Layer 3: Browser / User Journey
  Runs against canonical frontend (:3001) with real user
  Checks: visual rendering, Markdown table display, Activity/Artifact
  panel semantics, reload/reopen persistence, conversation isolation,
  perceived answer quality
  Cost: per-scenario 1–3min, browser overhead
  Tools: headless Chrome → real frontend → real backend → real RP
```

### 4.2 Scenario-to-Layer Mapping

| Scenario | L1 (Deterministic) | L2 (Real Agent) | L3 (Browser) | Rationale |
|---|---|---|---|---|
| S01 case-intake | ✓ | ✓ | ✓ | covers all layers; simple enough for browser |
| S02 case-intake-CJK | ✓ | — | — | AL-2 regression (CJK alias route); no quality signal beyond routing |
| S03 finding-explain | ✓ | ✓ | ✓ | core why-explanation; needs browser Markdown rendering |
| S04 ML explain | — | ✓ | ✓ | real RP data required (LightGBM field); needs browser |
| S05 finding-evidence | ✓ | ✓ | ✓ | core COMPLETE-scope table; needs browser rendering |
| S06 combined finding | ✓ | ✓ | ✓ | multi-intent; needs real LLM |
| S07 follow-up | — | ✓ | ✓ | multi-turn requires real state; browser tests coherence |
| S08 related-accounts | ✓ | ✓ | ✓ | 18-account completeness + why; needs browser |
| S09 cross-account | ✓ | ✓ | ✓ | control case; needs browser |
| S10 insufficient-data | ✓ | ✓ | — | boundary wording is backend text; browser optional |
| S11 unresolved | ✓ | — | — | pure routing/boundary test; deterministic |
| S12 multi-intent related | ✓ | ✓ | ✓ | composer must cover both intents |
| S13 degraded-mode | ✓ | ✓ | — | backend honesty test; browser optional |
| S14 persistence | — | — | ✓ | reload/reopen is browser-only |
| S15 boundedness stress | ✓ | — | — | pure deterministic policy test |
| S16 evidence-empty | ✓ | — | — | deterministic outcome semantics |

**L1 scenario count**: 11 (S02, S03, S05, S06, S10, S11, S12, S13, S15, S16)
**L2 scenario count**: 12 (S01, S03–S10, S12, S13)
**L3 scenario count**: 9 (S01, S03–S09, S14)

**Why not all browser E2E**: each browser scenario costs 1–3 min and
requires a running frontend. Layer 1 runs in < 1s and catches 80% of
regressions (routing, planning, boundary). Layer 2 catches quality and
integration issues. Layer 3 catches only what the browser uniquely
verifies: Markdown rendering, panel semantics, reload, and the user's
visual experience. Testing all 16 in a browser would cost 30–50 min
per run without adding proportional quality signal.

### 4.3 Layer progression rule

A scenario MUST pass L1 before running in L2.
A scenario MUST pass L2 before running in L3.
An L3 failure requires L2 investigation to determine whether the root
cause is backend (response text wrong) or frontend (rendering wrong).

## 5. Agent Quality Model (Revised)

v1 proposed 9 axes. After review:

| Axis | v1 Status | v2 Status | Change |
|---|---|---|---|
| Q1 Target Resolution | kept | **kept** | core; deterministic |
| Q2 Planning Quality | kept | **kept** | core; deterministic |
| Q3 Execution Quality | kept | **kept** | core; deterministic |
| Q4 Loop Boundedness | kept | **kept, expanded** | now explicitly covers premature-stopping (§9) |
| Q5 Evidence Completeness | kept | **split into Q5a + Q5b** | see below |
| Q6 Answer Fidelity | kept | **kept** | core; LLM-judged |
| Q7 Honesty & Boundary | kept | **kept** | core; deterministic + LLM |
| Q8 Conversation Coherence | kept | **kept** | core; deterministic |
| Q9 Investigation Usability | kept | **reflagged as UI-sensitive** | see below |

### Q5 split rationale

Evidence correctness and answer correctness are INDEPENDENT dimensions.
A response can have correct evidence but a wrong interpretation
(e.g. correct trigger_values but "the finding was triggered because of
shared devices" when the rule is withdrawal frequency). Conversely, an
answer can look correct but cite incomplete evidence (e.g. 3 of 7
records when the user asked for ALL).

| Sub-axis | Measures | Evaluation |
|---|---|---|
| **Q5a Evidence Retrieval Correctness** | Were the correct records fetched? Is the count right? Is the scope (withdrawals vs transactions) right? | deterministic (from ToolResult data) |
| **Q5b Evidence Completeness** | Does the evidence cover the FULL requested scope? Was it truncated? | deterministic (ToolResult.complete flag + count comparison) |
| **Q5c Evidence Interpretation** | Does the response correctly interpret the evidence? (e.g. trigger values stated correctly, threshold semantics right) | deterministic (key fact matching) + LLM judge (interpretation quality) |
| **Q5d Provenance / Grounding** | Is the evidence traceable to RP? Are citations genuine? | deterministic (evidence_refs present, citation_ids match RP) |

Q5a–Q5d replace the single Q5. Each can independently pass or fail.

### Q9 UI-sensitive flag

Q9 (Investigation Usability) is distinct from Q1–Q8 because:
- Q1–Q8 evaluate the BACKEND response text; Q9 requires observing the
  FRONTEND rendering (Markdown tables, Activity panel, Artifact tab).
- Q1–Q8 failures are fixed by changing backend code; Q9 failures may
  require frontend changes.
- Q9 is the ONLY axis that requires a real browser to evaluate fully.

Q9 should NOT be merged into Q1–Q8 because doing so would make it seem
like a backend-only concern. It is explicitly **UI-sensitive** and
**browser-required**.

### Evaluation method per axis

| Axis | Deterministic | LLM Judge | Human |
|---|---|---|---|
| Q1 | ✓ (target, routing, disambiguation) | — | — |
| Q2 | ✓ (skill, steps, args) | — | — |
| Q3 | ✓ (outcome, args injected) | — | — |
| Q4 | ✓ (iteration count, stop reason, convergence) | — | — |
| Q5a–Q5b | ✓ (count, scope, complete flag) | — | — |
| Q5c | ✓ (key facts) | ✓ (interpretation quality) | — |
| Q5d | ✓ (evidence_refs, citation_ids) | — | — |
| Q6 | ✓ (key facts, forbidden content) | ✓ (addresses question, explanation quality) | — |
| Q7 | ✓ (boundary compliance, degraded honesty) | ✓ (overclaim detection) | ✓ (Safe-Select audit) |
| Q8 | ✓ (followup target, persistence, isolation) | — | — |
| Q9 | ✓ (no runtime metadata lead, artifact title) | ✓ (usable without panels) | ✓ (browser review) |

## 6. Ground Truth / Evaluation Oracle

### 6.1 The problem

v1 relied on deterministic checkers + LLM judge but never defined WHAT
the judge should compare the answer against. An LLM judge without
ground truth can only assess "does this sound reasonable?" — not "is
this actually correct?"

### 6.2 Ground Truth definition

Each scenario MUST define a **GroundTruth** object containing:

```python
@dataclass
class GroundTruth:
    # Resolution
    expected_finding_id: str | None
    expected_case_id: str
    expected_capability: str | None      # e.g. "related_account_analysis"
    # Evidence
    expected_evidence_type: str           # "withdrawals" | "transactions" | "accounts" | "graph"
    expected_evidence_count: int | None   # exact count (e.g. 7, 64, 18)
    expected_evidence_ids: list[str] | None  # if known (e.g. withdrawal IDs)
    expected_key_facts: list[str]         # facts that MUST appear in the answer
                                          # e.g. ["Withdrawal frequency (24h) = 7",
                                          #       "DEV8Z1O4A35QU",
                                          #       "Trade frequency (24h) = 54"]
    # Answer properties
    expected_answer_elements: list[str]   # semantic elements that must be present
                                          # e.g. ["why explanation", "evidence table",
                                          #        "attribution boundary"]
    # Prohibited
    prohibited_claims: list[str]          # claims that must NOT appear
                                          # e.g. ["因为共享设备所以99.41",
                                          #        "全部共享设备明细已提供"]
    prohibited_content: list[str]         # content that must NOT appear
                                          # e.g. ["例如", "top 5", "showing 5 of 10",
                                          #        "Investigation_and_Action_SOP"]
    # Boundedness
    expected_stop_reason: str | None      # e.g. "goal_satisfied", "bounded_data",
                                          #        "semantic_resolution_unavailable"
    expected_max_iterations: int          # convergence budget
    expected_insufficiency: str | None    # e.g. "attribution not available",
                                          #        "record-level enumeration not provided"
```

### 6.3 Per-scenario Ground Truth (Phase 1 scenarios)

| Scenario | expected_finding | expected_capability | expected_evidence | expected_key_facts (≥ subset) | prohibited | stop_reason |
|---|---|---|---|---|---|---|
| S01 | None (case-level) | case_intake | 8 findings | "8 findings", "U00010" | wrong case | goal_satisfied |
| S02 | None (case-level) | case_intake | same as S01 | same as S01 | wrong case | goal_satisfied |
| S03 | F5 | signal_explain | graph facts | "Cluster_device_sharing_1", "DEV8Z1O4A35QU", "80.0" | "rule-based detection", "例如" | goal_satisfied |
| S04 | F1 | signal_explain | ML score + features | "99.41", "LightGBM", "not a calibrated probability", "attribution" | SOP/Network citations, "因为共享设备所以" | goal_satisfied |
| S05 | F3 | finding_drilldown | 7 withdrawal records | "7", "W000087", "W000089", "withdrawal" | "例如", "top 5", "证据视图", F7 content | goal_satisfied |
| S06 | F7 | signal_explain + finding_drilldown | Feature + 64 transactions | "Trade frequency (24h) = 54", "T00", F7 identity | F3 content, "withdrawal record" | goal_satisfied |
| S07 | F5 (via follow-up) | signal_explain | graph facts | same as S03 | wrong finding | goal_satisfied |
| S08 | None (case-level) | related_account_analysis | 18 accounts | "18", "U00004", "U00337", "DEV8Z1O4A35QU" | "10 个", "只包含 5 个" | goal_satisfied |
| S09 | None (case-level) | cross_account_trade_analysis | 6 candidates | "6", "CATA-001", "预算限制", "不构成" | 0 candidates, no boundedness | goal_satisfied |
| S10 | F5 | signal_explain + finding_drilldown (empty) | graph facts + EMPTY evidence | graph facts + "数据边界" + "不能" | "全部明细已提供", "conclusive empty" | bounded_data |
| S11 | None (unresolved) | none | none | honest unresolved message | fabricated finding, tool calls | context_unresolved |
| S12 | None (case-level) | related_account_analysis | 18 accounts + graph facts | same as S08 + why | same as S08 | goal_satisfied |
| S13 | None | none | none | "语义解析服务当前不可用" | tool calls, fabricated answer | semantic_resolution_unavailable |
| S14 | F3 | finding_drilldown | 7 records | same as S05 + persistence | response lost on reload | goal_satisfied |
| S15 | fixture | (fixture tool) | (fixture result) | convergence before bound | iteration ≥ max | goal_satisfied or bounded |
| S16 | fixture | finding_drilldown | empty | EMPTY ≠ GOAL_SATISFIED | "conclusive empty" when data exists | bounded_data |

### 6.4 How the LLM judge uses Ground Truth

The LLM judge prompt receives:
1. The user's question
2. The Agent's response text
3. The GroundTruth's `expected_key_facts` and `prohibited_claims`
4. The rubric dimensions to score

The judge verifies:
- Are all `expected_key_facts` present or semantically equivalent?
- Are any `prohibited_claims` present?
- Does the answer address the user's question (not a different question)?
- Is the explanation quality acceptable?

The judge does NOT need to know RP internals — it checks the answer
against the Ground Truth's declarative expectations. This means the
judge CAN verify correctness, not just plausibility.

**Key principle**: Ground Truth is authored by the developer (who knows
the RP data), not by the LLM judge (which only sees the response text).

## 7. Scenario Matrix (Revised)

### 7.1 v1 → v2 scenario changes

| v1 Scenario | v2 Status | Change | Reason |
|---|---|---|---|
| S01 case-intake | **kept** | — | core flow; all layers |
| S02 case-intake-CJK | **reclassified** | **AL-2 regression** (not AL-3 quality) | tests CJK alias routing only; no quality signal beyond "correct route selected" |
| S03 finding-explain | **kept** | GroundTruth added | core why-explanation |
| S04 ML explain | **kept** | GroundTruth added | tests attribution boundary + LightGBM semantics |
| S05 finding-evidence | **kept** | GroundTruth added | core COMPLETE-scope table |
| S06 combined finding | **kept** | GroundTruth added | multi-intent |
| S07 follow-up | **kept** | GroundTruth added | tests conversation coherence |
| S08 related-accounts | **kept** | GroundTruth added (18 accounts) | tests detail data source |
| S09 cross-account | **kept** | **CONTROL** (must not regress) | tests bounded analysis answer |
| S10 insufficient-data | **kept** | GroundTruth added + premature-stopping dimension | tests BOUNDED_DATA semantics |
| S11 unresolved | **kept** | — | boundary test |
| S12 multi-intent related | **kept** | GroundTruth added | composer dual-intent |
| S13 degraded-mode | **kept** | — | honesty test |
| S14 persistence | **kept** | — | reload/reopen |
| S15 boundedness stress | **kept** | redesigned: tests convergence guard | premature-stopping |
| S16 evidence-empty | **kept** | **AL-2 regression** (EMPTY ≠ SATISFIED already tested in AL-2.12) | confirms EMPTY semantics remain correct |
| **S17 (NEW)** evidence-vs-answer mismatch | **added** | tests evidence correct / answer wrong or vice versa | see §8 |
| **S18 (NEW)** premature-stopping | **added** | tests convergence guard fires only on genuine no-progress | see §9 |

### 7.2 AL-2 regression tests vs AL-3 quality scenarios

The existing `eval/scenarios.py` scenarios (E01–E17, L01–L05) serve as
**AL-2 functional regression tests**: they verify that tools are
called correctly, steps are planned correctly, and outcomes match
expectations. They are necessary but NOT sufficient for AL-3.

AL-3 quality scenarios differ because they:
1. Define Ground Truth (not just "tool called correctly")
2. Evaluate answer semantic quality (not just keyword presence)
3. Cover multi-turn conversation coherence
4. Test evidence-vs-answer alignment
5. Measure boundedness and convergence quality
6. Include degraded-mode and boundary-compliance dimensions

Scenarios S02 and S16 overlap heavily with AL-2 functional tests.
They are retained in the matrix as regression gates but are NOT
counted toward AL-3 quality evaluation.

## 8. Evidence Correctness vs Answer Correctness

### 8.1 The six independent dimensions

| Dimension | Measures | Evaluation Method |
|---|---|---|
| **E1: Evidence Retrieval Correctness** | Was the correct tool called with correct args? Did it return the correct data type? | deterministic (tool name + args + outcome) |
| **E2: Evidence Completeness** | Does the evidence cover the full requested scope? (7 of 7 records? 18 of 18 accounts? or truncated?) | deterministic (record_count vs expected, complete flag) |
| **E3: Evidence Accuracy** | Are the specific values (IDs, counts, scores, timestamps) correct? | deterministic (GroundTruth key_facts match against ToolResult data) |
| **A1: Evidence Interpretation** | Does the response correctly interpret the evidence? (right trigger values, right threshold semantics, right graph facts) | deterministic (key fact matching) + LLM judge |
| **A2: Conclusion Correctness** | Does the response's conclusion / explanation / answer follow logically from the evidence? | **LLM judge** (with GroundTruth context) |
| **A3: Answer Completeness / Directness** | Does the response directly address what the user asked? Is it self-contained? | **LLM judge** |

### 8.2 The independence principle

These dimensions can fail independently:

| Evidence | Answer | Scenario |
|---|---|---|
| ✓ correct | ✓ correct | normal pass |
| ✓ correct | ✗ wrong | **evidence-interpretation mismatch**: correct trigger values cited but wrong conclusion drawn |
| ✗ wrong | ✓ looks correct | **evidence-insufficient answer**: answer states a conclusion that looks right but the evidence doesn't support it |
| ✗ wrong | ✗ wrong | complete failure |
| ✗ incomplete | ✓ honest | correct bounded answer (acknowledges gap) |
| ✓ complete | ✗ incomplete | evidence has all 7 records but response only shows 3 |

### 8.3 Dedicated scenario: evidence-vs-answer mismatch (S17)

**S17 — Evidence-interpretation mismatch**

User: 「这个发现的风险信号是什么？为什么被标记？」

Expected: the response correctly cites the signal evidence (e.g.
trigger_values, cluster facts) AND correctly explains why it
constitutes a risk.

Failure mode being tested: the response cites correct evidence but
attributes it to the wrong cause (e.g. cites withdrawal_frequency
data but says "this was flagged by graph detection").

This requires the LLM judge to have the GroundTruth's
`expected_key_facts` and check that the response's causal explanation
matches the actual signal type — not just that evidence numbers appear
in the text.

### 8.4 Dedicated scenario: insufficient evidence for a seemingly correct answer

Covered by S10 (insufficient-data). The user asks for "all shared-device
details". The signal_explain tool correctly returns graph facts. The
finding_drilldown returns EMPTY (no record-level data). The correct
answer: state graph facts + acknowledge that record-level enumeration
is not available. The failure mode: claiming "all details provided"
when only summary facts exist.

## 9. Premature Stopping vs Bounded Completion

### 9.1 The distinction

| Outcome | Meaning | Correct? |
|---|---|---|
| **Successful bounded completion** | The loop stopped because the available data has been fully reported; no further query would yield new information. The response explains what IS known and what ISN'T. | ✓ |
| **Premature stopping** | The loop stopped (via convergence guard or iteration bound) BEFORE the user's question was fully addressed, when a different plan or query COULD have yielded more data. | ✗ |
| **Repeated no-progress convergence** | The convergence guard fires on an identical result — correct if the query genuinely yields no new data. | ✓ |
| **Correct RE_PLAN** | The loop re-planned and the new plan produced genuinely different (useful) data. | ✓ |
| **Unnecessary continuation** | The loop continued past a point where the goal was already satisfied, wasting iterations. | ✗ |

### 9.2 Rubric dimensions

| Dimension | Measures | Method |
|---|---|---|
| convergence_appropriate | Did the convergence guard fire on a genuine no-progress situation? | deterministic: check that the last N observations are identical AND no alternative tool could produce different data |
| no_premature_stop | Did the loop stop before the user's question was answered, when more data was available? | deterministic: check that a success observation exists AND evidence_missing is not claimed when data exists; LLM judge: does the response acknowledge what's missing rather than silently stopping? |
| bounded_completion_honest | Does the response state what IS known and what ISN'T when stopping on partial data? | deterministic: boundary statement present; LLM judge: overclaim detection |

### 9.3 Dedicated scenario: premature stopping (S18)

**S18 — Convergence guard must not fire prematurely**

Setup: a tool that returns DIFFERENT results on each call (e.g.
pagination: first call returns page 1, re-plan returns page 2).

Expected: the convergence guard must NOT fire (different fingerprints),
and the loop should CONTINUE to gather page 2.

Failure mode being tested: the convergence guard fires on different
results because the fingerprint is too coarse (e.g. same tool + same
outcome but different data).

This is a deterministic test using the existing fixture harness. It
regression-tests the AL-2.13 guard's fingerprint specificity.

## 10. Evaluation Isolation & Reproducibility

### 10.1 Isolation guarantees

| Scope | Guarantee | Implementation |
|---|---|---|
| **Scenario isolation** | Each scenario creates a fresh investigation. No shared state between scenarios. | harness: `POST /api/v2/investigations` per scenario |
| **Task isolation** | Each turn/goal creates a new task. Previous tasks are not re-executed. | runtime guarantee (already enforced) |
| **Conversation isolation** | A new conversation has no previous conversation's Activity, context, or goal. | frontend `newInvestigation()` clears activity binding; backend creates fresh investigation |
| **TaskStore isolation** | Each evaluation run uses a FRESH store (temp DB) OR explicitly names a store per run. | harness: `TaskStoreV2(tempfile.mkdtemp() + "/al3_run.db")` |
| **RP data isolation** | The evaluation uses the real RP (read-only). No RP data is modified. | RP is a read-only dependency for investigation queries |

### 10.2 Multi-turn / persistence scenarios

Scenarios S07 (follow-up) and S14 (persistence) intentionally share
state across turns within a single scenario. This is the system under
test (conversation coherence). They MUST NOT share state with other
scenarios.

The harness ensures this by creating a fresh investigation per
scenario. Multi-turn scenarios create multiple turns within that
investigation.

### 10.3 Version tracking

Every evaluation run records:

| Field | Source | Purpose |
|---|---|---|
| `run_id` | auto-generated UUID | identifies this evaluation run |
| `timestamp` | UTC ISO | when the run happened |
| `git_commit` | `git rev-parse HEAD` | code version |
| `model_name` | `settings.ANTHROPIC_MODEL` | which LLM was used |
| `prompt_version` | telemetry.AGENT_VERSION | prompt/planner version |
| `scenario_version` | scenario set hash | which scenarios were run |
| `rp_data_hash` | hash of key RP responses | RP data version (detect RP data changes) |
| `baseline_ref` | previous run_id | what this run is compared against |

### 10.4 Reproducibility

Real-LLM scenarios are NOT byte-reproducible. The harness handles this:
- Records all outputs for post-hoc review
- Runs each scenario 2–3 times; reports modal outcome
- Flags variance ≥ 1 score level as "LLM variance" (not regression)
- Deterministic scenarios (S15/S16) must be 100% reproducible

## 11. Evaluation Rubric

### 11.1 Rubric structure

Each scenario's rubric has three tiers:

```
Tier 1: HARD GATES (must ALL pass; any failure = scenario FAIL)
  - GroundTruth.key_facts: all present in response
  - GroundTruth.prohibited: none present in response
  - GroundTruth.expected_finding: correctly resolved
  - GroundTruth.expected_stop_reason: matches
  - GroundTruth.expected_max_iterations: not exceeded
  - Deterministic boundary checks (no lexicon, no fabrication, etc.)

Tier 2: QUALITY SCORES (LLM judge scores 0–2 each; avg ≥ 1.5 = pass)
  - answer_addresses_question
  - explanation_quality
  - answer_usable_without_panels (where applicable)

Tier 3: DIAGNOSTIC SIGNALS (reported, not gated)
  - iteration_count (compared to expected)
  - evidence_completeness (complete vs bounded)
  - re_plan_count (0 = clean, > 0 = investigate why)
  - LLM-judge consistency (two runs → same score?)
  - response_length (too short / too verbose)
```

### 11.2 Hard gates vs diagnostic signals

**Hard gates** (failure = scenario FAIL):
- Ground truth facts present
- Prohibited content absent
- Correct resolution
- Expected stop reason
- Boundary compliance (no fabrication, no lexicon fallback)
- Degraded-mode honesty

**Quality scores** (LLM judge; below threshold = PARTIAL):
- answer_addresses_question
- explanation_quality
- answer_usable_without_panels

**Diagnostic signals** (reported but never gated):
- iteration_count (report exact, compare to expected range)
- evidence_completeness (report exact count, compare to expected)
- re_plan_count (report, flag if > 0 for single-capability scenarios)
- response_length (report character/word count)
- LLM judge consistency (two runs, flag disagreement)
- latency (report, no threshold)

**What should NOT be aggregated**:
- LLM judge scores from different dimensions should NOT be averaged
  into a single "quality score" (a response that scores 2 on
  explanation but 0 on honesty is worse than one that scores 1 on
  both)
- Iteration count should NOT be normalised into an efficiency score
  (the "right" number depends on the scenario)
- Response length should NOT be scored (verbosity ≠ quality)

## 12. Metric Aggregation

### 12.1 The aggregation hierarchy

```
Per-scenario result:
  hard_gates: {gate_name: PASS|FAIL, detail}
  llm_judge: {dimension: 0|1|2, detail}
  diagnostics: {metric_name: value}
  overall: PASS | PARTIAL | FAIL

Per-axis aggregate:
  Q1: {scenarios_passed / scenarios_total, failed_scenario_ids}
  Q2–Q9: same structure

Per-run aggregate:
  total_scenarios, passed, partial, failed
  by_axis: {Q1: …, Q2: …, …}
  by_category: {case-intake: …, finding: …, …}
  failure_taxonomy: {RT-RESOLVE: …, RT-COMPOSE: …, …}
  false_successes: [scenario_ids]
  bounded_failures: [scenario_ids]
  llm_judge_summary: {dimension: avg_score, variance}
  hard_gate_failures: [scenario_ids with gate failures]
```

### 12.2 No single "Agent Quality Score"

The evaluation produces a **profile**, not a score. Reasons:
- A single score (e.g. 87.3) hides which specific quality axis failed
- A high score on easy scenarios can mask a low score on hard ones
- Different stakeholders care about different axes (PM cares about
  Q6 answer fidelity; engineering cares about Q4 boundedness; compliance
  cares about Q7 honesty)
- A "quality score" incentivizes metric gaming (optimize the score, not
  the behaviour)

Instead, the report presents:
- A pass/fail/partial count per axis
- A list of failed scenarios grouped by failure taxonomy
- LLM judge dimension scores as a table (not averaged)
- Diagnostic signals as observations (not scores)

## 13. Deterministic vs LLM Judge vs Human Review

(Same as v1 §7, unchanged. The three-tier logic is correct.)

| Type | Rationale | Risk if misused |
|---|---|---|
| Deterministic | Structurally encoded facts; non-biasable | Can be gamed by keyword stuffing; cannot judge semantic quality |
| LLM-as-judge | Requires semantic understanding of answer quality | Biased toward verbose; inconsistent across runs; cannot verify against RP data; prompt-injection from response text |
| Human review | Requires domain expertise, UI/UX judgment, real analyst workflow | Cannot scale; subjective; expensive |

Key principle: **deterministic checks gate; LLM judge scores; human
review accepts.** No single method is sufficient alone.

## 14. Evaluation Harness Design

### 14.1 Existing (reusable)

| Component | Reusable | Notes |
|---|---|---|
| `eval/scenarios.py` | partial | scenario definition pattern; needs AL-3 multi-turn + GroundTruth extension |
| `eval/checkers.py` | yes | 20 deterministic checkers; the foundation for hard gates |
| `eval/capture.py` | yes | TurnView captures tool calls, response, plan, args |
| `eval/runner.py` | partial | runs single-turn scenarios; needs multi-turn + loop extension |
| `eval/report.py` | extend | basic aggregate; needs rubric table + LLM judge + Markdown |
| `eval/planner_runner.py` | yes | planner-only evaluation |

### 14.2 New components

| Component | Purpose | Effort |
|---|---|---|
| `eval/scenarios_al3.py` | S01–S18 definitions with GroundTruth + rubric spec | 2 d |
| `eval/ground_truth.py` | GroundTruth dataclass + per-scenario instances | 0.5 d |
| `eval/trace_v3.py` | unified trace for turn + loop (captures observations, decisions, activity, response, artifacts) | 1 d |
| `eval/llm_judge.py` | judge prompt template + scoring + GroundTruth injection + consistency check | 1 d |
| `eval/report_v3.py` | Markdown + JSON report with rubric tables, axis aggregates, failure taxonomy | 1 d |
| `eval/baseline_store.py` | evaluation run store/load (run_id, version, results) | 0.5 d |
| `eval/run_al3.py` | entry point: run → evaluate → judge → report → compare | 0.5 d |
| `eval/isolation.py` | fresh-state helper: create investigation, reset bindings | 0.5 d |

Total new: ~7 d.

### 14.3 Harness data flow

```
Scenario Definition (S01–S18, with GroundTruth)
    ↓
Isolation Layer (fresh investigation per scenario)
    ↓
Agent Execution
  L1: InvestigationService with ScriptedLLM / fixtures
  L2: HTTP → canonical backend (real LLM + real RP)
  L3: Browser → canonical frontend (real user journey)
    ↓
Trace Capture (unified structured trace)
    ↓
┌─────────────────────────────────────────┐
│ Deterministic Evaluator                  │
│  Hard gates: GroundTruth facts/prohibited│
│  Routing: target, skill, steps, args     │
│  Boundedness: iteration, stop reason     │
│  Association: policy association contract│
│  Boundary: no lexicon, no fabrication    │
└─────────────────────────────────────────┘
    ↓ (all gates pass)
┌─────────────────────────────────────────┐
│ LLM Judge Evaluator                      │
│  Input: question + response + GroundTruth│
│  Dimensions: addresses_question,         │
│    explanation_quality, usable_standalone│
│  Output: 0/1/2 per dimension             │
└─────────────────────────────────────────┘
    ↓
Report Generator
  Per-scenario: gates + judge scores + diagnostics
  Per-axis: Q1–Q9 aggregate
  Per-run: pass/partial/fail + failure taxonomy
  Comparison: vs stored baseline
    ↓
Failure Artifacts (response text, tool calls, screenshots)
```

## 15. Failure Taxonomy

(Same as v1 §11, unchanged. Nine taxonomy codes RT-RESOLVE through
RT-PERSIST.)

## 16. Unknown Unknowns / Risks

(Expanded from v1 §12. All v1 risks retained, plus:)

### 16.1 Ground truth staleness

Ground truth facts (e.g. "7 withdrawals", "18 accounts") are tied to
current RP data. If the RP dataset changes, ground truths become stale
and scenarios fail despite correct Agent behavior. Mitigation:
- Ground truth key facts should reference STABLE facts (cluster name,
  detection type, device ID) rather than volatile counts
- volatile counts should be expressed as constraints (count ≥ 5) or
  checked against the actual RP response at run time, not against a
  hard-coded number
- RP data snapshot hash should be recorded for regression comparison

### 16.2 Composer regression masked by deterministic checks

If a composer change breaks the Markdown table rendering but keeps all
key facts in the text, deterministic checks still pass (facts present)
while the user experience degrades. Mitigation: browser E2E (Layer 3)
catches rendering regressions; LLM judge evaluates "is this readable?"

### 16.3 Over-reliance on LLM judge for honest scenarios

Honest scenarios (S10, S11, S13) test that the Agent says "I cannot"
or "insufficient data". An LLM judge might score these LOW because
they don't contain impressive data — penalising honesty. Mitigation:
the judge prompt must explicitly state that honest boundary statements
score HIGHER than overclaimed answers; deterministic checks verify
that the expected honest markers are present.

### 16.4 Cross-surface evaluation gap

The turn surface (compose_response) and the loop surface
(compose_loop_response) have different composers with different
wording. A scenario passing on one surface may fail on the other.
The harness must explicitly test BOTH surfaces for scenarios that
users access via both paths (Send vs Run Loop).

## 17. AL-3 Phase Plan

(Same as v1 §13, with updated estimates for v2 scope.)

| Phase | Scope | Effort | Output |
|---|---|---|---|
| AL-3.1 | Evaluation design v2 (this document) + design freeze | done | this doc |
| AL-3.2 | Harness: GroundTruth definitions, scenario implementations (S01–S18), trace v3, LLM judge, report v3, isolation, baseline store | 5–7 d | working harness |
| AL-3.3 | Baseline evaluation: execute all scenarios on canonical runtime, generate report | 1–2 d | baseline report |
| AL-3.4 | Findings / gap analysis: classify by failure taxonomy, identify top quality gaps | 1 d | gap analysis |
| AL-3.5 | UI redesign input (if findings indicate frontend issues) | 1 d | UI spec |

## 18. AL-3.1 Design Freeze Checklist

Before starting AL-3.2 (Harness implementation), ALL of the following
must be checked off:

- [ ] Ground Truth defined for every scenario (S01–S18)
      — expected finding / capability / evidence / key facts /
        prohibited claims / boundedness
- [ ] Three-layer evaluation architecture formalised (L1/L2/L3)
      — with scenario-to-layer mapping
- [ ] Evidence correctness (E1–E4) separated from answer
      correctness (A1–A3)
      — with at least one scenario testing the mismatch
- [ ] Premature stopping vs bounded completion formalised
      — with a dedicated scenario (S18) testing convergence-guard
        specificity
- [ ] Evaluation isolation strategy defined
      — scenario / task / conversation / TaskStore isolation
      — multi-turn contamination prevention
- [ ] Version tracking defined
      — run_id, git_commit, model, prompt, scenario, RP data hash
- [ ] Metric aggregation strategy defined
      — hard gates vs quality scores vs diagnostic signals
      — no single aggregate score
- [ ] Quality model finalised (9 axes, Q5 split into 4 sub-axes)
      — with per-axis evaluation method mapping
- [ ] Scenario matrix reviewed
      — each scenario marked as AL-2 regression vs AL-3 quality
      — coverage: multi-turn ✓, insufficient ✓, semantic ambiguity ✓,
        premature stopping ✓, evidence-vs-answer ✓, tool success but
        goal failure ✓, degraded mode ✓, state contamination ✓,
        browser journeys ✓
- [ ] LLM-as-judge role bounded
      — judges against GroundTruth, not against its own judgement
      — deterministic checks remain the gate
- [ ] Cross-surface evaluation strategy defined
      — both turn surface and loop surface tested where applicable
- [ ] Existing eval infrastructure reuse plan confirmed
      — which checkers / runners / capture components are reused
- [ ] Known evidence gaps documented
      — what current RP data cannot support (ML attribution, full
        related_accounts from preview path)

## 19. Definition of Done

AL-3 Phase 1 is COMPLETE when:

- [ ] Evaluation harness implemented with GroundTruth support
- [ ] All 18 scenarios implemented and runnable
- [ ] Baseline evaluation executed on canonical runtime (real browser
      for L3 scenarios)
- [ ] Report generated per-scenario + per-axis + per-run
- [ ] Each scenario: PASS / PARTIAL / FAIL + failure taxonomy code
- [ ] At least one "false success" or "evidence-answer mismatch"
      identified (proving the evaluation catches real quality issues)
- [ ] LLM-judge scores present with GroundTruth-backed verification
- [ ] Human review completed for S01, S03, S08 (minimum)
- [ ] AL-2 functional regression (E01–E17) still passing
- [ ] Gap analysis: top quality gaps classified by taxonomy and owner
- [ ] Design freeze checklist (§18) all checked

## 20. Open Questions

1. Multi-surface testing: should scenarios run on BOTH /turn and /goal
   paths? Current: test the surface the user's entry point uses.
2. Judge model: same as Agent or different? Current: same model
   (Phase 1); judge rubric is structural enough.
3. LLM variance in regression: run 2–3 times per scenario, report modal
   score, flag variance.
4. Frontend rendering: Phase 1 evaluates backend response text;
   browser E2E validates rendering (AL-3.5).

## 21. Non-Goals

(Same as v1 §16, unchanged.)

- NOT a new Agent capability project
- NOT a runtime / planner / executor / composer refactor
- NOT a frontend redesign
- NOT a prompt engineering pass
- NOT a replacement for AL-2 unit/integration tests
- NOT an MCP or multi-agent evaluation
- NOT a load/performance test
- NOT a security/penetration test
- NOT a user study or A/B test
