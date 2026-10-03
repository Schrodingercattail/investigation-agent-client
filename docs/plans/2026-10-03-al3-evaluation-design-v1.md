# AL-3 — Evaluation Design

**Date**: 2026-10-03
**Status**: DESIGN — not implemented
**Baseline**: git `4d00ea4` (AL-2 governed investigation agent track, working tree clean)
**Parent**: AL-2 governed investigation agent track (AL-2.0 – AL-2.14)

---

## 1. Purpose

AL-3 answers the question: **does the Agent actually help an analyst
investigate a case?** Not "do the tools work" (AL-2 proved that), but
"does the user get a correct, honest, complete, bounded, and useful
answer to their investigation question?"

This requires moving beyond binary pass/fail deterministic checks
(which AL-2's eval/ already provides) into a multi-dimensional quality
model that distinguishes:

- **tool execution success** ≠ **meaningful result** ≠ **goal
  satisfaction** ≠ **correct final answer**
- **policy match** ≠ **finding-level policy basis**
- **artifact produced** ≠ **user question answered**
- **bounded termination** ≠ **premature stopping**

## 2. Baseline

The frozen AL-2 baseline comprises:

| Layer | Module(s) | Lines (approx) |
|---|---|---|
| Context Resolution | `context_resolution.py` | 949 |
| Goal Understanding | `goal_resolution.py` | 364 |
| Semantic Foundation | `semantic_resolution.py` + `candidate_metadata.py` + seed | 948 |
| Planner | `planner_v2.py` | 947 |
| Executor | `executor_v2.py` | 732 |
| Agent Loop | `agent_loop.py` | 2060 |
| Investigation Service | `investigation_service.py` | 1472 |
| Domain Tools | 7 tools in `domain_tools/` | 2752 |
| Skill Registry | `skills.py` | 538 |
| LLM Provider | `llm_provider.py` | 159 |
| M2 Seeding | `metadata_seeding.py` | — |

Existing eval infrastructure: 22 scenarios (17 scripted + 5 live),
11 planner scenarios, deterministic checkers, TurnView capture, basic
aggregation report. 88 backend test files, 15 AL-2 series.

## 3. Evaluation Objectives

AL-3 Phase 1 answers:

1. Does the Agent, given an analyst's natural-language investigation
   question, resolve the correct target, execute the correct tools, and
   produce a correct and honest answer?
2. Where does it fail, and is the failure a runtime bug, a data
   limitation, a routing gap, a composer gap, or an inherent capability
   boundary?
3. Does it handle uncertainty, insufficiency, and boundary conditions
   honestly?
4. Does it maintain conversation coherence and state correctness across
   turns and reloads?
5. Is it efficient (bounded iterations, no wasted calls) and safe (no
   fabrication, no silent UNKNOWN→FALSE, no unauthorized autonomous
   action)?

These questions cannot be answered by deterministic unit tests alone,
nor by LLM-as-judge alone. AL-3 uses a **layered rubric** with
deterministic, LLM-judge, and human-review tiers.

## 4. Agent Quality Model

Seventeen dimensions were considered. They are consolidated into **nine
measurable quality axes** with the rationale for each consolidation.

| # | Quality Axis | What It Measures | Subsumes |
|---|---|---|---|
| Q1 | **Target Resolution** | Does the Agent resolve the correct finding / case / capability from natural language? Includes paraphrase robustness, follow-up pronoun resolution, CJK phrasing, disambiguation, and cross-case boundary enforcement. | Goal understanding; context / target resolution; semantic routing |
| Q2 | **Planning Quality** | Is the selected skill and plan appropriate for the resolved intent? Are steps ordered correctly? Are arguments correct? Does the planner avoid padding steps or inventing unsupported operations? | Planning quality; tool selection (pre-execution) |
| Q3 | **Execution Quality** | Did the tools execute successfully, return the expected outcome class, and produce the expected data shape? Includes argument-injection correctness and outcome classification. | Tool selection (post-execution); tool argument quality |
| Q4 | **Loop Boundedness** | Does the loop terminate within bounds? Does it converge on repeated no-progress? Does it avoid burning iterations on an operation that cannot succeed? Is BOUNDED_DATA / CONCLUSIVE_EMPTY / GOAL_SATISFIED correctly labelled? | Re-planning quality; loop convergence; boundedness; efficiency |
| Q5 | **Evidence Completeness & Grounding** | Does the response carry the evidence the user asked for? Is it complete (not truncated when the user asked for ALL)? Is it grounded in RP-authoritative data (provenance-traceable, no fabrication)? | Evidence completeness; evidence grounding; provenance |
| Q6 | **Answer Fidelity** | Does the final natural-language response correctly answer the user's question? Is the explanation causal/threshold semantics (not just a rule name)? Is the scope (SUMMARY vs COMPLETE) respected? Is the finding identity explicit? Is it free of internal runtime metadata? | Answer fidelity; answer correctness; response composition quality |
| Q7 | **Honesty & Boundary Compliance** | Does the Agent admit insufficiency / uncertainty rather than overclaiming? Does it keep UNKNOWN ≠ FALSE? Does it maintain Safe-Select / authorization boundaries? Does it degrade honestly when the LLM is unavailable? | Uncertainty / insufficiency handling; boundary compliance; degraded mode; Safe-Select compliance |
| Q8 | **Conversation Coherence** | Does follow-up referencing work correctly? Does state persist across reload/reopen? Is context inherited correctly from prior turns? Does the conversation avoid contamination from previous investigations? | Follow-up coherence; state / persistence; conversation isolation |
| Q9 | **Investigation Usability** | Does the user get an answer they can act on without opening Activity or Artifact? Is the answer understandable in ordinary language? Is the artifact supporting (not substituting)? Is the Activity diagnostic (not the answer)? | User-visible investigation usability; artifact usefulness; Activity vs Conversation boundary |

**Consolidation rationale**: "Goal understanding" and "context/target
resolution" are one decision in the runtime (ContextResolver +
SemanticNormalizer) and are evaluated by the same checkers (target id,
resolution kind). "Tool selection" is split into pre-execution (Q2) and
post-execution (Q3) because a correct plan can fail at execution (RP
down, empty data) and vice versa. "Artifact usefulness" and "Activity
boundary" are folded into Q9 because the user-visible question is "can
I understand the answer without opening other panels?" not "does the
artifact exist?"

## 5. Scenario Matrix

Scenarios are designed from the analyst's investigation workflow, not
from the tool registry. Each scenario tests at least two quality axes.

### 5.1 Scenario Set (Phase 1: 16 scenarios)

| ID | Category | User Prompt(s) | Case / Context | Quality Axes | Mode |
|---|---|---|---|---|---|
| S01 | case-intake | 「给我输出一下00010案例情况」 | fresh, U00010 | Q1, Q6, Q9 | canonical |
| S02 | case-intake-CJK | 「调查00010」 | fresh, U00010 | Q1 (CJK alias) | canonical |
| S03 | finding-explain | 「为什么共享设备是风险项」 | U00010, no focus | Q1, Q6 | canonical |
| S04 | finding-explain-ML | 「ML Pattern Detection什么意思，为什么是风险」 | U00010, no focus | Q1, Q6, Q7 | canonical |
| S05 | finding-evidence | 「把所有高频提现证据发给我」 | U00010, no focus | Q1, Q5, Q6 | canonical |
| S06 | finding-detail-combined | 「为什么说高频交易是风险项，给我输出高频交易的具体交易明细」 | U00010, no focus | Q1, Q2, Q5, Q6 | canonical |
| S07 | follow-up-finding | turn 1: 「展开一下00010案例情况」→ turn 2: 「为什么说共享设备是风险项」 | U00010, 2-turn | Q1, Q8 | canonical |
| S08 | related-accounts | turn 1: intake → turn 2: 「能告诉我哪些账号是这个账号的关联账号吗」 | U00010, 2-turn | Q1, Q5, Q9 | canonical |
| S09 | cross-account | turn 1: intake → turn 2: 「这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我」 | U00010, 2-turn | Q1, Q3, Q5, Q6, Q7 | canonical |
| S10 | insufficient-data | 「共享设备为什么是风险项，给我提供一下所有与这个账号共享设备的明细」 | U00010, no focus | Q4, Q6, Q7 | canonical |
| S11 | unresolved | 「谈谈虚拟币洗钱这个风险项」 | U00010, no focus | Q1, Q7 | canonical |
| S12 | multi-intent | 「关联账户关系为什么是风险项，这个账户有哪些关联账户」 | U00010, no focus | Q2, Q6 | canonical |
| S13 | degraded-mode | same as S03 but LLM unavailable | U00010, no-key | Q7 | canonical no-key |
| S14 | persistence | S05 + reload/reopen | U00010 | Q8 | canonical |
| S15 | boundedness | repeated-same-query stress (tool returns same success N times) | synthetic or U00299 fixture | Q4 | deterministic |
| S16 | evidence-empty | 「show me all the transactions for this finding」 (finding has no transaction records) | fixture, focused | Q5, Q7 | deterministic |

### 5.2 Coverage notes

- Scenarios S01–S06 use the real RP case U00010 and real LLM. These are
  NOT reproducible byte-for-byte; they measure quality, not
  determinism.
- Scenarios S10–S12 test boundary compliance: insufficient data,
  unresolved references, multi-intent handling.
- Scenario S13 tests the degraded mode (no API key): every
  finding-level NL request must produce `SEMANTIC_RESOLUTION_UNAVAILABLE`
  with the honest message; case-level capability routes must still work.
- Scenario S15 uses the deterministic fixture harness (existing
  `eval/scenarios.py` pattern) to stress boundedness without LLM/RP
  dependency.
- Scenario S16 tests evidence-empty semantics: EMPTY outcome with
  supported query ≠ GOAL_SATISFIED.

### 5.3 Phase 2 extension candidates (NOT Phase 1)

- Multi-case investigation (currently one conversation = one case)
- Artifact quality evaluation (beyond scope check)
- Timeline navigation and temporal reasoning
- Analyst correction / feedback loop ("that's wrong, show me X instead")
- Cross-conversation context isolation stress
- Performance/latency measurement

## 6. Evaluation Rubric

### 6.1 Rubric Design Principles

1. **Deterministic first**: if a check can be computed from structured
   data (tool calls, state, plan, response string), it MUST be
   deterministic.
2. **LLM-as-judge only for semantic quality**: answer correctness,
   explanation quality, and coherence require semantic understanding
   and cannot be reduced to string matching.
3. **Human review for usability**: the final "would an analyst find
   this useful?" judgment requires a human.
4. **No hidden chain-of-thought**: all evaluation inputs must come from
   observable behavior: ToolResults, plan steps, state, response text,
   artifacts, UI-visible content.

### 6.2 Metrics

| Metric | Axis | Source | Method | Pass Criteria |
|---|---|---|---|---|
| target_resolved | Q1 | context.focus / context.case_id | deterministic | matches expected target |
| resolution_kind | Q1 | context.focus_source / resolution_kind | deterministic | expected kind (explicit / inferred / case-level) |
| no_false_target | Q1 | context.focus (negative) | deterministic | wrong target NOT selected |
| plan_skill | Q2 | plan.selected_skill | deterministic | expected skill selected |
| plan_steps | Q2 | plan.steps[].type | deterministic | expected step types in order |
| plan_args | Q2 | plan.steps[].arguments | deterministic | locked args match (e.g. stream=withdrawals) |
| tool_executed | Q3 | tool_calls[].name + outcome | deterministic | expected tool called, expected outcome |
| tool_args_injected | Q3 | tool_calls[].arguments | deterministic | executor-injected values correct |
| iteration_count | Q4 | state.iteration | deterministic | ≤ expected max (e.g. ≤ 4 for single-capability runs) |
| stop_reason | Q4 | state.stop_reason | deterministic | expected stop reason (not premature, not mislabeled) |
| evidence_count | Q5 | ToolResult.data.record_count / evidence_refs | deterministic | expected count (e.g. 7 withdrawals, 64 transactions, 18 accounts) |
| evidence_complete | Q5 | ToolResult.data.complete / evidence_missing | deterministic | complete=true for evidence views; false for graph-only findings |
| response_contains | Q6 | response text | deterministic | key facts / entities / numbers present |
| response_not_contains | Q6 | response text | deterministic | forbidden content absent (e.g. "例如", "top 5", wrong finding, internal metadata) |
| answer_addresses_question | Q6 | response text + user prompt | **LLM judge** | judge scores 0–2: 0 = does not answer; 1 = partial / off-target; 2 = directly answers |
| explanation_quality | Q6 | response text | **LLM judge** | judge scores 0–2: 0 = bare label / runtime metadata; 1 = factual but shallow; 2 = causal / threshold semantics in user language |
| honesty_insufficiency | Q7 | response text + ToolResult | deterministic + LLM judge | deterministic: evidence_missing → boundary statement must be present. LLM judge: no overclaim detected |
| boundary_compliance | Q7 | response text + code path | deterministic | no lexicon fallback, no fabricated facts, no UNKNOWN→FALSE, Safe-Select untouched |
| degraded_mode_honesty | Q7 | response text (no-key mode) | deterministic | SEMANTIC_RESOLUTION_UNAVAILABLE message present; zero tool calls |
| followup_target_correct | Q8 | context.focus after turn 2 | deterministic | follow-up resolves to expected finding |
| persistence_retained | Q8 | response text after reload | deterministic | answer markers present after reload/reopen |
| conversation_isolation | Q8 | response text + Activity (new conv) | deterministic | no previous conversation's goal / activity in current |
| no_runtime_metadata_lead | Q9 | response text | deterministic | response does NOT start with "Steps completed" / "tool: success" / artifact ID |
| artifact_title_purposeful | Q9 | artifact.title | deterministic | title names the purpose (not generic "Evidence Bundle") |
| answer_usable_without_panels | Q9 | response text alone | **LLM judge** + human spot-check | judge scores 0–2: 0 = must open Activity/Artifact; 1 = partially self-contained; 2 = fully self-contained |

### 6.3 Rubric summary per scenario

Each scenario defines:
- `required_checks`: list of (metric, expected_value) — deterministic
- `forbidden_checks`: list of (metric, forbidden_value) — deterministic
- `llm_judge_dimensions`: list of (dimension, rubric) — LLM scored
- `human_review`: boolean — requires manual browser inspection

A scenario PASSES if all required_checks pass AND no forbidden_check
fails AND the average LLM-judge score ≥ threshold (e.g. ≥ 1.5).
A scenario is PARTIAL if all deterministic checks pass but LLM-judge
scores < threshold. A scenario FAILS if any deterministic check fails.

## 7. Deterministic vs LLM Judge vs Human Review

| Evaluation type | Why this method | Examples |
|---|---|---|
| **Deterministic** | The fact is structurally encoded in the ToolResult / state / plan / response string. String matching or field comparison is sufficient and non-biasable. | target resolution, plan steps, tool args, iteration count, evidence count, forbidden content, stop reason, persistence markers, degraded-mode honesty, boundary compliance |
| **LLM-as-judge** | The fact requires semantic understanding of natural-language quality (does the answer explain WHY? is it in user language? is it complete for the question asked?). No deterministic check can judge "is this a good explanation?" | answer_addresses_question, explanation_quality, answer_usable_without_panels |
| **Human review** | The fact requires domain expertise, UI/UX judgment, or real analyst workflow validation. Cannot be automated without losing fidelity. | overall investigation usability, Safe-Select governance audit, perceived answer trustworthiness |

**Why not all LLM-as-judge**: LLM judges are susceptible to:
- bias toward verbose/confident-sounding answers even when wrong
- inconsistency across runs (same input, different score)
- prompt injection from the response text being judged
- inability to verify factual claims against RP data

**Why not all deterministic**: binary string matching cannot judge
"does this explanation actually explain why?" or "is this answer
understandable to a non-technical analyst?" A response can contain all
the right keywords and still be unintelligible. Conversely, a response
that paraphrases correctly but uses different wording would fail a
keyword check.

**Recommendation**: deterministic checks gate the run (must pass).
LLM-judge scores are advisory (report + flag for human review). Human
review is the acceptance authority for AL-3 Phase 1.

## 8. Evaluation Harness Design

### 8.1 What exists (reusable)

| Component | Reusable? | Notes |
|---|---|---|
| `eval/scenarios.py` | partial | 22 scenarios cover AL-2 functionality; AL-3 needs new scenario definitions for the 16 scenarios above |
| `eval/checkers.py` | yes | deterministic checkers (expected_focus, tool_called, response_contains, etc.) |
| `eval/runner.py` | partial | runs scripted + live scenarios against InvestigationService; needs extension for multi-turn and loop |
| `eval/capture.py` | yes | TurnView captures structured execution trace |
| `eval/report.py` | extend | basic aggregation; needs LLM-judge score integration + markdown output |
| `eval/planner_runner.py` | yes | planner evaluation |

### 8.2 What AL-3 needs (new)

```
Scenario definition (YAML or Python, versioned)
    ↓
Scenario Runner (agent execution: turn / goal / loop)
    ↓
Trace Capture (structured: tool calls, state, plan, observations,
               response, artifacts, activity, timing)
    ↓
Deterministic Evaluator (existing checkers + new metrics)
    ↓
LLM Judge Evaluator (answer quality scoring, with rubric prompt)
    ↓
Report Generator (JSON + Markdown + per-scenario detail + aggregate)
    ↓
Baseline Comparison (current run vs stored baseline)
    ↓
Failure Artifacts (screenshot, response text, tool calls, state dump)
```

### 8.3 Minimal new infrastructure

| Component | Purpose | Effort |
|---|---|---|
| `eval/scenarios_v3.py` | AL-3 scenario definitions (S01–S16) with multi-turn support, quality-axis mapping, rubric spec | 1–2 d |
| `eval/trace.py` | unified structured trace from turn / goal / loop paths (supersedes TurnView for loop runs) | 1 d |
| `eval/llm_judge.py` | LLM-as-judge: prompt template, scoring, consistency check (same prompt twice → same score?) | 1 d |
| `eval/report_v3.py` | Markdown report: per-scenario rubric table + aggregate + LLM-judge scores + failure artifacts | 1 d |
| `eval/baseline.py` | store/load evaluation run baseline (JSON with run_id, model, prompt version, scenario version, results) | 0.5 d |
| `eval/run_al3.py` | AL-3 entry point: run scenarios → evaluate → report → compare baseline | 0.5 d |

Total new infrastructure: ~4–6 d effort.

### 8.4 Key design decisions

**real LLM vs mock**: Phase 1 runs S01–S12 with real LLM + real RP
(canonical runtime). S13 runs no-key. S15–S16 use deterministic
fixtures. This is deliberate: the primary question is "does the system
work with real dependencies?" not "is the logic correct in isolation?"
(the latter is AL-2's unit/integration test coverage).

**deterministic reproducibility**: real-LLM scenarios are NOT
deterministic. The harness must record: model name, prompt version,
scenario version, run timestamp, and RP data snapshot hash (if
available). Regression comparison must account for LLM variance (same
input may produce different-but-valid outputs).

**evidence lineage**: each scenario's trace must preserve: which
ToolResults were produced, which fields the composer consumed, and
which response text was generated. This allows post-hoc debugging of
answer-quality failures.

## 9. Metrics Summary

| Category | Metrics | Method | Phase 1 |
|---|---|---|---|
| Resolution | target_resolved, resolution_kind, no_false_target | deterministic | all scenarios |
| Planning | plan_skill, plan_steps, plan_args | deterministic | all scenarios |
| Execution | tool_executed, tool_args_injected | deterministic | all scenarios |
| Boundedness | iteration_count, stop_reason | deterministic | loop scenarios |
| Evidence | evidence_count, evidence_complete | deterministic | evidence scenarios |
| Answer (facts) | response_contains, response_not_contains | deterministic | all scenarios |
| Answer (quality) | answer_addresses_question, explanation_quality, answer_usable_without_panels | LLM judge | S03–S06, S08–S10, S12 |
| Honesty | honesty_insufficiency, boundary_compliance, degraded_mode_honesty | deterministic + LLM judge | S10, S11, S13 |
| Coherence | followup_target_correct, persistence_retained, conversation_isolation | deterministic | S07, S08, S09, S14 |
| Usability | no_runtime_metadata_lead, artifact_title_purposeful, answer_usable_without_panels | deterministic + LLM judge + human | all scenarios |

## 10. Baseline Evaluation Procedure

1. Freeze the scenario set (S01–S16) and rubric.
2. Ensure canonical runtime is running current code.
3. Run `eval/run_al3.py --all` → executes all scenarios, captures
   traces, runs deterministic evaluators, runs LLM judge on eligible
   scenarios, generates report.
4. Store the run as baseline (run_id, timestamp, model, results).
5. Review the report:
   - Which scenarios pass fully?
   - Which have deterministic failures?
   - Which have low LLM-judge scores?
   - Are there false successes (task completed but answer wrong)?
6. Classify each failure: runtime bug / data limitation / routing gap /
   composer gap / capability boundary.
7. Write gap analysis and prioritized fix list.

## 11. Failure Taxonomy

| Code | Meaning | Example |
|---|---|---|
| RT-RESOLVE | target resolution failure (wrong finding / case / unresolved) | followed-up on F3, resolved F5 |
| RT-PLAN | planning failure (wrong skill, wrong steps, missing steps) | evidence request planned explain_signal |
| RT-EXEC | execution failure (tool error, RP unavailable, wrong outcome) | finding_drilldown returned integration_error |
| RT-LOOP | loop semantics failure (churn, premature stop, mislabelled terminal) | 8 iterations on repeated empty; GOAL_SATISFIED on unsupported |
| RT-COMPOSE | response composition failure (missing facts, examples instead of complete, runtime metadata leading, internal jargon) | "specific evidence: RP records 7…for example:…" instead of full table |
| RT-DATA | data limitation (RP doesn't provide the requested data) | no feature-level ML attribution; related_accounts list truncated at 5 of 10 |
| RT-BOUNDARY | boundary compliance failure (fabrication, UNKNOWN→FALSE, Safe-Select breach) | claimed "all shared-device details" when RP only has summary |
| RT-HONESTY | honesty failure (overclaimed certainty, hid insufficiency, fake semantic resolution) | scored 99.41 explained as "because of shared devices + trades" without attribution evidence |
| RT-PERSIST | persistence failure (response lost on reload, state contaminated) | old conversation's activity shown in new conversation |

## 12. Unknown Unknowns / Risks

These are issues the AL-3 design must actively test for, not just list
as risks.

### 12.1 Metric gaming

An agent could pass deterministic checks by keyword-stuffing the
response with expected fragments while providing a semantically wrong
answer. Mitigation: LLM judge on answer_addresses_question catches
this; deterministic checks alone are insufficient for answer quality.

### 12.2 Evaluator bias

An LLM judge may prefer verbose answers, penalize honest "I cannot
answer X" responses, or exhibit inconsistency across runs. Mitigation:
the rubric prompt must explicitly reward honesty and penalize
overclaiming; run the judge twice per scenario and check consistency;
deterministic checks remain the gate.

### 12.3 Scenario leakage

If the same scenarios are used for development debugging and
evaluation, the agent may be (unintentionally) tuned to pass them.
Mitigation: keep a held-out set of scenarios not used during
development; rotate scenarios between AL phases; version scenarios.

### 12.4 Overfitting to U00010

All current scenarios use U00010. The Agent may perform well on this
case but fail on others (different cluster sizes, different finding
types, different data availability). Mitigation: Phase 2 should add
U00299 and synthetic cases; Phase 1 acknowledges this limitation.

### 12.5 Answer correctness vs evidence correctness

A response can contain correct evidence but draw a wrong conclusion,
or vice versa. The rubric must evaluate them independently:
- evidence correctness: are the cited facts, counts, and IDs correct?
- answer correctness: does the conclusion / explanation / answer
  correctly interpret the evidence?

### 12.6 Tool-use efficiency vs answer quality

An agent that reaches the correct answer in 8 iterations is less
efficient than one that does it in 2, but may still be correct.
Conversely, a 1-iteration answer that misses the user's actual question
is worse than a 3-iteration thorough investigation. The rubric must
not optimize efficiency at the cost of answer quality.

### 12.7 Successful bounded termination vs premature stopping

AL-2.13's convergence guard correctly stops no-progress loops, but
could also stop a loop that would have found new data with a different
plan. The evaluation must distinguish:
- correct convergence (the query genuinely yields no new data)
- premature convergence (a different plan would have succeeded)

### 12.8 Semantic routing correctness vs investigation usefulness

The Agent may route to the "correct" tool but produce a technically
correct yet unhelpful answer (e.g. "the finding was flagged by
graph-based risk detection" without explaining WHY graph detection is
relevant to shared devices). The rubric must evaluate usefulness, not
just routing correctness.

### 12.9 Degraded mode confidence

In no-key mode, the Agent's honest degradation (SEMANTIC_RESOLUTION_UNAVAILABLE)
is correct, but the test must verify that the system does not
accidentally appear to work (e.g. by matching a deterministic route on
a finding-level question, giving a false sense of semantic capability).

### 12.10 Evaluation contamination

If evaluation scenarios share state (same investigation, same case
context), earlier scenarios can affect later ones. The harness must
create a fresh investigation per scenario (or explicitly manage state).

### 12.11 False confidence in "correct-looking" answers

A response that contains the right finding ID, correct tool name, and
authoritative-looking numbers may still be answering the wrong question
or omitting the user's actual ask. LLM-as-judge must evaluate "does
this answer what the user asked?" not "does this look like a valid
investigation response?"

## 13. AL-3 Phase Plan

| Phase | Scope | Effort | Output |
|---|---|---|---|
| AL-3.1 | Evaluation design (this document) | done | this doc |
| AL-3.2 | Harness: new scenario definitions (S01–S16), trace capture for loop path, LLM-judge module, report v3, baseline store | 3–5 d | working eval harness |
| AL-3.3 | Baseline evaluation: run all scenarios against canonical runtime, capture results, generate report | 1–2 d | baseline evaluation report |
| AL-3.4 | Findings / gap analysis: classify failures by taxonomy, identify top quality gaps, distinguish agent-quality vs UI-IA problems | 1 d | gap analysis + prioritized fix list |
| AL-3.5 | UI redesign input (if needed): translate findings into UI/UX requirements | 1 d | UI improvement spec |

AL-3.2 and AL-3.3 can be parallelized for deterministic scenarios
(S15/S16) and sequential for real-LLM scenarios (S01–S14).

## 14. Definition of Done

AL-3 Phase 1 is COMPLETE when:

- [ ] Scenario matrix defined (S01–S16) with quality-axis mapping
- [ ] Rubric defined with deterministic / LLM-judge / human-review tiering
- [ ] Evaluation harness implemented (scenarios + runner + judge + report)
- [ ] Baseline evaluation EXECUTED against canonical runtime
- [ ] Evaluation report generated (per-scenario + aggregate)
- [ ] Each scenario has: pass/partial/fail + failure taxonomy code
- [ ] Top quality gaps identified and classified (agent vs UI vs data)
- [ ] At least one "false success" or "bounded failure" identified
      (proving the evaluation is not just checking green checkmarks)
- [ ] LLM-judge scores present for eligible scenarios
- [ ] Human review completed for at least S01 (case intake), S03
      (finding explain), and S08 (related accounts)
- [ ] Evidence lineage preserved for each evaluated scenario
- [ ] "pytest all passed" NOT used as the completion criterion

## 15. Open Questions

1. Should AL-3 evaluate the Conversation (turn) surface and the Agent
   Loop (goal) surface separately, or only the loop surface (which the
   browser uses for all scenarios)? Current thinking: evaluate the
   surface the user actually uses (loop for S07–S10; turn for S03–S06).
2. Should the LLM judge use the same model as the Agent (self-judging
   risk) or a different model? Current thinking: same model is
   acceptable for Phase 1; the judge's rubric is structural enough
   (score 0/1/2 with explicit criteria) that self-judging bias is
   manageable.
3. How should real-LLM variance be handled in regression comparison?
   A scenario that passes today may score lower tomorrow due to LLM
   weather. Current thinking: run each scenario 2–3 times, report the
   modal score, flag variance ≥ 1 score level.
4. Should the evaluation include the frontend Conversation rendering
   (Markdown table rendering, evidence card display) or only the
   backend response text? Current thinking: Phase 1 uses backend
   response text (deterministic); browser E2E validates rendering in
   AL-3.5.

## 16. Non-Goals

- NOT a new Agent capability project
- NOT a refactor of the runtime, planner, executor, or composer
- NOT a frontend redesign
- NOT a prompt engineering pass
- NOT a replacement for existing AL-2 unit/integration tests
- NOT an MCP integration or multi-agent evaluation
- NOT a load/performance test
- NOT a security/penetration test
- NOT a user study or A/B test (that is post-AL-3)
