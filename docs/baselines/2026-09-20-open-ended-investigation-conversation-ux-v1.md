# Baseline: Open-ended Investigation / Conversation UX V1

**Status**: PROPOSED BASELINE — product/architecture contract for the
next implementation phase. No application code changes in this phase.
**Date**: 2026-09-20
**Related baselines**:
- `2026-09-18-cross-account-trade-analysis-v1.md` — historical feature baseline
- `2026-09-19-bounded-agent-loop-v1.md` — Agent Runtime baseline
  (authoritative for loop/state/telemetry; THIS baseline extends, not
  replaces, its execution model)
- `2026-09-19-agent-activity-v1.md` — Activity V1 (its right-column-tab
  UX direction is **superseded for the next phase** by §18 here:
  conversation-primary with inline activity)
- `2026-09-19-agent-qa-boundaries-v1.md` — QA boundary findings
  (extended with B8/B9 in this phase, see §29)

---

## 1. Triggering problem (observed, not hypothetical)

Independent QA observed this real exchange:

> User: **"具体展开一下Opposite Trade Ratio的证据"** (no finding manually selected)
> Agent: **"No specific target could be resolved from this request."**

This is not acceptable product behavior. The user named evidence that
exists verbatim inside the case's authoritative data (the finding
description states "An opposite-trade ratio of 45.24% exceeded the 40%
threshold…"), yet the Agent failed because the request did not match a
predefined command shape and no finding was manually selected.

This baseline defines how the Agent handles open-ended investigation
requests over known, governed capabilities — without becoming an
unrestricted autonomous system.

## 2. Inspected current state (code-level facts)

### A. What the system can already do

- Full focused-investigation flows: turn-local and Agent-Loop paths,
  capability-gated skills, seven governed domain capabilities
  (`risk_case_fetch`, `finding_drilldown{timeline, evidence,
  self_opposite_trades}`, `signal_explain`, `policy_lookup`,
  `cross_account_trade_analysis`, `artifact_bundle`).
- Bounded Agent Loop with re-plan, retry, distinct terminal states,
  state-borne activity records, inline-capable UI data.
- Finding-name matching in `ContextResolver._match_findings_by_name`:
  token-majority match against finding TITLES (min 2 significant
  tokens).
- An LLM-assisted target-resolution path EXISTS in `ContextResolver
  ._llm_resolve` (candidate-restricted to real finding ids, JSON
  output) — but `InvestigationService` defaults
  `allow_llm_resolution=False` and patches `_llm_configured → False`.
- Deterministic routing hooks (EN/CJK simplified+traditional) for
  opposite-trade / cross-account / stream intents.
- Three-layer evaluation harness; 43/12 pre-existing environment-
  dependent legacy failures (RP down/up) documented in QA.

### B–G. Where open-ended requests fail (classified)

| Class | Finding (evidence) |
|---|---|
| C. Target resolution | The observed failure: "Opposite Trade Ratio" appears in the finding's DESCRIPTION, not its title; title-token matching cannot resolve it. No description/alias/signal-name resolution. LLM-assisted resolution exists but is disabled by default. Finding rows render `finding_id` + title only — no aliases. |
| D. Question understanding | `PlannerV2` routes via (a) fixed intent→step guidance lines in the system prompt and (b) deterministic regex hooks. There is no objective-level understanding layer ("user wants evidence expansion" vs "user wants explain"); vague CJK requests ("再深入看看") fall outside both. |
| E. Capability selection | The planner sees per-skill CLOSED step vocabularies but no per-capability metadata (what it does / when useful / required context). Skill gating is binary per capability. Keyword-hook misses degrade to generic steps or bounded failure. |
| F. Execution/runtime | Sound: loop bounds, retry, blocked/dependency states, activity persistence all verified in QA + Layer A. Not the problem. |
| G. Conversation UX | Execution activity lives in a right-column tab, disjoint from the assistant turn that produced it. Follow-ups are fixed server templates. `context_action` (pending selection) rides only turns/goals explicitly. Conversation turns carry no execution trace. |

**Conclusion**: the failure is NOT a runtime problem. It is a
target-resolution gap (C) compounded by phrase-shaped understanding (D)
and thin capability metadata (E), surfaced through a non-conversational
UX (G).

## 3. Core product problem (frozen)

> The Agent must not behave like a finite menu of predefined user
> commands. The user must be able to express an investigation objective
> in natural language even when: no finding was manually selected; the
> exact tool name is unknown; the internal "view" name is not used; the
> language is vague but understandable; multiple capabilities could
> contribute; the request needs more than one tool.

The Agent uses available context and governed capabilities to determine
a reasonable investigation path. **Open-ended describes the USER'S
LANGUAGE and investigative question — never unlimited system authority**
(§12).

## 4. Intent ≠ Target

These are separate resolutions and MUST be tracked separately:

- **Intent** (objective): investigate / expand evidence / explain /
  find supporting transactions / check timeline / cross-account check.
- **Target** (subject): the finding, evidence set, account, or case the
  objective applies to.

For "具体展开一下Opposite Trade Ratio的证据": intent = evidence
expansion; target = the finding whose description/evidence names
"Opposite Trade Ratio" (in U00299: `Coordinated Trading Pattern`, whose
authoritative description states the ratio).

### 4.1 Target resolution sources (precedence, frozen)

1. Explicit structured focus supplied by the UI for THIS request
   (`context_action` — user_selected; strongest; never overwritten by
   weaker inference).
2. Explicit entity reference in the user text (finding id, exact or
   alias/partial name, evidence name, account id).
3. Current conversation context (§24: the finding the conversation is
   currently about; "这个发现/这个比例").
4. Current case (single-case scope is an invariant).
5. Authoritative case findings and metadata (titles, descriptions,
   signal/rule names, evidence references) — LLM-assisted matching
   restricted to the real candidate set.
6. Evidence context from prior observations in this investigation.

Rules:
- An explicit user selection is NEVER overwritten by inference within
  the same request; a NEW explicit selection supersedes older state.
- Stale UI state must not override the current request (pending
  selection expires when superseded or cleared).
- The resolved target is recorded as **explicit** or **inferred**
  (with source) in Agent State, telemetry, and the activity trace —
  for debugging, telemetry, and future UI display.

### 4.2 Resolution outcomes (four states, frozen)

| State | Meaning | Behavior |
|---|---|---|
| RESOLVED (explicit) | user-selected/explicitly named | proceed |
| RESOLVED (inferred) | one target strongly supported by authoritative context | proceed; label as inferred |
| AMBIGUOUS | ≥2 materially different targets plausible | ONE concise clarification question (§10) |
| UNRESOLVED | no target can be found in authoritative context | say so honestly; offer case-level options |

And the operation dimension is classified independently (§8): resolved
target + unsupported operation ≠ unresolved target. The current message
"No specific target could be resolved" must NEVER be returned when the
actual situation is an ambiguous target, an unsupported operation, or a
merely vague request.

## 5. Question / task understanding (second layer)

After target resolution, the system determines WHAT the user wants done
with the target. Frozen objective vocabulary (extensible, not
per-phrasing):

`expand_evidence` · `explain_finding` · `identify_supporting_records` ·
`timeline_investigation` · `cross_account_check` · `policy_check` ·
`summarize` · `artifact_export`.

Acceptance mappings (illustrative, not regex rules):

| User text (same finding) | Objective |
|---|---|
| "展开一下 Opposite Trade Ratio 的证据" | expand_evidence |
| "这个比例为什么这么高？" | explain_finding |
| "这个比例对应哪些交易？" | identify_supporting_records |
| "这个发现是怎么来的？" | explain_finding (contributing signals/rules) |
| "有没有时间上的异常？" | timeline_investigation |
| "有没有其他账户参与？" | cross_account_check |

Understanding is a **semantic** task performed by the LLM layer over
authoritative context (§14) — regex hooks remain only as fast paths and
guards (§16). One intent class per wording variation is explicitly
rejected.

## 6. Capability discovery / selection

The Agent reasons over governed capabilities exposed through a
capability metadata model. V1 minimum per capability (product
requirement):

- `id`, human-readable `name`, `description`
- `purpose` (what it produces)
- `suitable_when` (investigation situations it serves)
- `required_context` (finding focus? case? evidence refs?)
- `input_requirements` (governed inputs only)
- `output_summary` semantics
- `limitations / unsupported cases`

Implementation decision (stage-gated, §32): metadata lives beside the
capability registry (`skills.py` / domain tools) as data — no separate
Skill per capability, no per-capability planner rewrite. Future
enhancement (not V1): cost/weight, output schema for machine
composition. The planner/decision layer receives this metadata so
selection is grounded in what capabilities DO, not in keyword tables.

## 7. Capability-driven open-ended flow (target)

```
NL request → target resolution (§4) → objective understanding (§5)
→ capability discovery (§6) → initial bounded plan
→ Agent Loop: execute → observe → CONTINUE/RE_PLAN/STOP/BLOCK/CLARIFY
→ user-facing response (§17)
```

Illustrative path for the triggering request (NOT a frozen workflow):
resolve finding → inspect evidence → (if useful) timeline or backing
rule → summarize → offer next relevant investigation. The Agent
selects the path from context and observations; combining capabilities
is expected behavior, enabled through the EXISTING loop's re-plan.

## 8. Supported / unsupported / ambiguous taxonomy (frozen)

| Class | Meaning | Behavior |
|---|---|---|
| SUPPORTED | request satisfiable with available capabilities | execute |
| SUPPORTED WITH INTERPRETATION | informal/vague language, one reasonable interpretation inferable from context | execute; state the interpretation in the response ("我理解你要看的是…") |
| AMBIGUOUS | ≥2 materially different interpretations remain | ask ONE concise clarification (§10) |
| UNSUPPORTED | goal clear, no capability can satisfy it | honest refusal + closest available alternatives |
| BLOCKED | capability exists, dependency/scope unavailable | distinct blocked state (existing QA-04 semantics) |
| ERROR | execution/integration failure | existing error semantics |

An ambiguous request must never surface as "unresolved reference"; a
partially answerable request must never surface as "Unsupported
request".

## 9. Vague investigation requests (product behavior)

For phrases like "展开一下这个发现" / "具体看看这个问题" / "把这个结论的
证据讲清楚" / "为什么会这样" / "再查一下" / "帮我深入看看" / "看看还有没有
其他异常":

- inspect available context (current finding, recent observations,
  recent result);
- infer a reasonable next investigative action when one is strongly
  supported (e.g. after a cross-account run, "再查一下" most plausibly
  continues that direction; with a finding focused and nothing done yet,
  "展开一下" → expand its evidence);
- ask only when genuinely ambiguous (§10);
- use existing capabilities.

These are PRODUCT ACCEPTANCE EXAMPLES, not implementation rules — no
per-phrase regex is defined for them (§16).

## 10. Clarification policy (frozen)

ASK when: multiple materially different targets/objectives are equally
plausible; executing the wrong interpretation would waste significant
work or create a misleading conclusion; required scope cannot be safely
inferred.

DO NOT ask when: one target is identified by case context; one action
is an obvious first step; ambiguity is resolvable by inspecting
existing evidence first.

Clarification is concise and actionable, offers the plausible options,
and is a normal assistant turn (never a dead end):
"我找到了 Opposite Trade Ratio 对应的发现。我可以先展开它的交易证据、
时间线或触发规则，你想先看哪一个？"

## 11–12. Precedence & bounded-ness

See §4.1 (precedence) — frozen. Open-ended does NOT relax: capability
registry/permissions, case/finding scope, data availability, retrieval
budgets, matching thresholds, iteration/retry limits, loop policy,
provenance/artifact integrity. The LLM never invents capabilities,
fabricates evidence, or escapes case scope (one-investigation-one-case
boundary stands).

## 13. Agent Loop relationship

The existing Agent Loop (2026-09-19 baseline) REMAINS the sole runtime
execution mechanism. No second loop. Open-ended understanding happens
BEFORE the loop (initial plan) and DURING it (re-plan). Re-planning may
consume: newly resolved targets, observations, discovered evidence,
capability metadata, prior actions — all through the existing
`PlannerV2` + `LoopPolicy` + budget machinery. `CLARIFY` is realized
with existing bounded semantics (a clarification turn is a normal
bounded terminal, not a new decision verb — or as a policy-guided STOP
with a clarification response; exact wiring is an implementation
decision).

## 14. LLM vs system control (frozen)

| LLM / probabilistic layer MAY | System MUST control |
|---|---|
| interpret natural language | authoritative target data (findings/evidence from RP) |
| resolve a likely target from provided authoritative context | scope (case/finding), permissions |
| infer the investigation objective | capability registry + eligibility |
| select among governed capabilities | input/schema validation |
| propose plans and re-plans | budgets, thresholds, tool execution |
| propose clarifying questions | state persistence, retry, loop limits |
| interpret observations | stop enforcement, provenance |
| propose next actions | artifact integrity |

The LLM never invents capabilities, fabricates unavailable evidence,
escapes case scope, or overrides `LoopPolicy`.

## 15. Capability metadata model

Conceptual model (§6); fields are phased — see §32 stages:

| Field | V1 phase |
|---|---|
| id / name / description | product requirement (Stage 3) |
| purpose / suitable_when | product requirement (Stage 3) |
| required_context / input_requirements | product requirement (Stage 3) |
| output_summary / limitations | product requirement (Stage 3) |
| evidence/provenance behavior | implementation decision |
| scope requirements | implementation decision |
| cost/weight | future enhancement |

Goal: capability selection grounded in metadata so user language never
needs enumerating (§16).

## 16. NO "100 commands" design (frozen)

A strategy based primarily on `if phrase A → tool A` is REJECTED.
Regex/routing hooks (opposite-trade, cross-account, stream scopes,
CJK variants) REMAIN as: deterministic shortcuts, high-confidence fast
paths, safety-critical routing guards (e.g. cross-account-before-self
ordering), compatibility behavior. Open-ended investigation must not
DEPEND on enumerating phrasings; hooks are an optimization layer over
semantic understanding, never its substitute.

## 17. Response composition (open-ended)

A response distinguishes: (A) what the Agent understood (stated
interpretation), (B) what it investigated (steps/capabilities), (C)
what evidence it found (with references), (D) what remains uncertain
(gaps, next_data_needed), (E) suggested next investigation when useful.

Never: internal reasoning, planner prompts, certainty beyond evidence,
robotic refusals for partially answerable requests. Prefer bounded
helpfulness: "我理解你想查看这个发现的依据。我先展开它对应的交易证据……"
Architecture is language-independent; Chinese examples are acceptance
examples.

## 18. Conversation-native Activity UX (product direction, frozen)

**Conversation is the primary interaction surface.** The right-column
Activity tab (Activity V1) is superseded as the PRIMARY experience:
execution activity renders INLINE within the assistant turn that
produced it.

```
User message
Assistant:
  [inline activity block]  Goal · current step · tool ·
                           result summary · decision · re-plan
  [result / explanation]
  [evidence / artifact references]
User continues…
```

The right-side context/evidence/artifacts column may remain as a
secondary surface (do not blindly delete; final layout is an
implementation decision). The existing `useAgentActivity` /
`AgentActivityPanel` data flow is reused — the change is placement and
association with the producing turn (ConversationTurn gains an activity
attachment), not new backend machinery.

## 19. Activity display design (inline block)

Compact by default, expandable; chronological; clearly associated with
its producing turn; familiar patterns: collapsible execution cards,
timeline rows, status badges, step indicators, disclosure controls.
Prefer existing primitives (Card, Collapsible, Accordion, Badge,
Alert, Separator, ScrollArea, Skeleton, Tooltip); reuse
`AgentActivityPanel` subcomponents where they fit.

## 20. User-safe activity content (four-layer separation, frozen)

```
tool-internal data ≠ LLM observation ≠ activity display ≠ final answer
```

Inline activity MAY show: understood goal, current/completed steps,
capability name, concise result summary, decision summary, re-plan,
retry, evidence/artifact refs, iteration. MUST NOT show: CoT, hidden
reasoning, system/planner prompts, raw model completions, raw tool
payloads, raw transaction datasets.

## 21. Conversation + activity + artifact relationship

Conversation → inline activity → result/explanation → evidence
references → artifact. Artifacts remain on the existing
artifact/evidence surfaces (no new store, no duplicate evidence). The
right column is secondary; Activity and Artifacts do not compete as
peer tabs for primary attention.

## 22. Main conversation product model

A conversational turn = user request → agent interpretation → optional
inline activity → investigation result → evidence/artifact references.
The Agent continues conversations using prior context rather than
treating each message as isolated. Cross-turn context (carried via the
existing persisted context/session model — InvestigationContext,
agent state, session documents; NO long-term memory in this phase):
current case, current finding/target, recent observations/result,
recent investigative direction, recent artifacts. The assistant's
previous TEXTUAL RESPONSE is presentation, not authoritative state —
authoritative underlying data remains RP + persisted state.

## 23. Conversational follow-ups (acceptance examples)

| Turn 1 | Turn 2 (must resolve relative to context) |
|---|---|
| "展开一下 Opposite Trade Ratio 的证据" | "再看看这些交易的时间线" (these trades → the evidence just expanded) |
| (same) | "那其他账户呢？" (→ cross-account direction) |
| (same) | "这个规则为什么触发？" (→ the backing rule) |
| (same) | "再具体一点" / "把相关交易列出来" (→ deepen/expand last result) |

Required context behavior: last resolved target stays current until
superseded; "这些/它们/刚才" bind to the previous result's evidence set;
follow-up objectives route through the same §5/§7 machinery. Follow-ups
may still arrive as structured chips (existing mechanism, unchanged).

## 24. Same-turn context vs authoritative data

Conversation context (current target, recent direction, recent result
references) is CONVERSATION STATE. Authoritative data (findings,
transactions, ratios, rules, artifacts) remains RP-owned and
state-owned. The assistant's previous prose is never parsed as the
source of truth; structured references (evidence ids, finding ids,
artifact ids) are what carry across turns.

## 25–27. Evaluation

**Model** (extends the three-layer philosophy): open-ended scenarios
run at Layer A with scripted LLMs where determinism is needed (policy,
boundaries, projection) and at Layer B with the real LLM where semantic
quality is the subject (target resolution, objective understanding,
capability selection, clarification quality). Layer C (live) covers the
full browser journey. Component tests cover inline activity UX.
Assertion style stays semantic (required content, forbidden content,
capability chosen, target resolved) — never exact phrase matching.

**Case matrix** (minimum, from the instruction):

- TARGET RESOLUTION: (1) "具体展开一下Opposite Trade Ratio的证据"
  without manual selection → resolves the coordinated-trading finding;
  (2) "看看这个发现为什么出现" with current focus → current finding;
  (3) partial-name mention → resolves when unambiguous; (4) two similar
  names → clarification.
- QUESTION UNDERSTANDING: (5)–(9) per §5 mappings.
- CAPABILITY SELECTION: (10) evidence, (11) timeline, (12)
  self-opposite, (13) cross-account, (14) policy, (15) multi-capability.
- FOLLOW-UP: (16)–(19) per §23.
- AMBIGUITY: (20) one clear target proceeds; (21) multiple plausible →
  one clarification; (22) vague-but-safe → proceeds with stated
  interpretation; (23) vague-and-unsafe → clarification.
- UNSUPPORTED: (24) clear request, no capability → honest refusal +
  alternatives; (25) partially answerable → bounded partial answer.
- FAILURE/BLOCK: (26) required data unavailable; (27) RP unavailable →
  `dependency_unavailable` semantics; (28) capability exists but scope
  blocks → bounded unsupported.
- UX: (29) inline activity within assistant turn; (30) re-plan inline;
  (31) follow-up preserves context; (32) collapsed/expanded;
  (33) evidence navigation; (34) artifact navigation; (35)
  reload/reopen preserves appropriate history.
- No-CoT / no-raw-data boundary asserted in every category.

## 27. Language coverage

English · Simplified Chinese · Traditional Chinese · mixed CJK/EN
(e.g. "具体展开一下Opposite Trade Ratio的证据"). Tests verify SEMANTIC
understanding (same objective expressed across languages/routes) — the
capability model must be language-independent; phrase lists are not the
assertion target.

## 28. Exploratory product questions (evaluation themes)

Unanticipated requests → useful progress? Clarification frequency
(too often / too rarely)? Overconfidence (acting on weak inference
without stating it)? Unnecessary tool calls? Keyword-triggered wrong
capability? Two-capability composition? Recovery from a wrong initial
interpretation (re-plan/clarify)? Natural continuation? Subject
retention across turns? Context survival across re-plan? Does inline
activity aid understanding or add noise? Does it read as conversation,
not a debug console?

## 29. Boundary document updates (evidence-backed, appended)

Added to `2026-09-19-agent-qa-boundaries-v1.md` as **B8** and **B9**
(append-only; existing B1–B7 unchanged) — see that file for full text:
- **B8 — target resolution vs authoritative descriptions**: target
  matching over titles + explicit selection only; references that exist
  in finding/evidence DESCRIPTIONS (where RP states its evidence, e.g.
  "opposite-trade ratio") are unresolvable. Evidence: the triggering
  repro + `_match_findings_by_name` title-token logic.
- **B9 — phrase-shaped intent routing vs semantic objectives**:
  regex/prompt-guidance routing is command-shaped; vague/CJK
  investigative language falls outside it. Evidence: routing hooks and
  prompt guidance structure + the observed failure class.

## 30. Agent Activity design principle (frozen)

The primary UX is the conversation. Execution activity is an inline,
user-safe explanation of what the Agent is doing. Artifacts and
detailed evidence are supporting surfaces. NO separate "Agent
dashboard" as the main product.

## 31. Non-goals (frozen)

Unrestricted autonomous agent · arbitrary capability invention ·
multi-agent orchestration · long-term user memory · general web
research · MCP · distributed runtime · advanced observability · CoT UI
· token-level tracing · autonomous hypothesis generation beyond current
capabilities · domain expansion beyond the investigation domain ·
immediately replacing ALL deterministic routing with an LLM.

## 32. Staged implementation strategy (incremental, no big rewrite)

| Stage | Content | Notes |
|---|---|---|
| 1 | Target/reference resolution: enable LLM-assisted resolution by default (candidate-restricted, already built); add description/alias matching; record explicit-vs-inferred | smallest unlock for the triggering repro |
| 2 | Question/objective understanding: objective vocabulary + LLM inference over resolved target/context; planner guidance consumes the objective | Layer-B evaluated |
| 3 | Capability metadata (§15 V1 fields) exposed to planner/decision layer | data beside the registry |
| 4 | Reduce hard-coded routing dependence: hooks demoted to fast paths/guards; semantic path becomes primary | hooks stay for safety ordering |
| 5 | Capability composition through the existing loop (multi-step plans, re-plan driven by observations) | no new runtime |
| 6 | Conversation-primary UX: inline activity in assistant turns; right column secondary | reuses activity data flow |
| 7 | Open-ended evaluation + exploratory pass (§25–28) | gate for "done" |

Each stage ships independently with its own evaluation slice; stages
1–2 alone fix the triggering repro class. Stage 2's internal order is
refined by §35b (goal understanding → suitability → gap detection
first; hook demotion later).

## 33. Architecture sync policy

`docs/architecture/` unchanged in this phase. Synchronization happens
only after implementation + independent QA of this phase, reconciling
AGENT_CLIENT_ARCHITECTURE_V2, SKILL_MODEL_V1, ContextResolver/planner
descriptions, and frontend/backend boundaries. Baselines remain
historical phase contracts.

## 34. Definition of Done (baseline)

The baseline defines: target resolution (sources + precedence + four
states) · question understanding (objective vocabulary) · capability
discovery/selection (metadata model) · ambiguity handling (§10 policy)
· supported/unsupported/blocked semantics (§8) · capability metadata
requirements · Agent Loop relationship (§13) · LLM/system boundary
(§14) · conversational context (§22/24) · follow-up behavior (§23) ·
inline Activity UX (§18–20) · evidence/artifact relationship (§21) ·
evaluation framework + 35-case matrix (§25–27) · exploratory themes
(§28) · non-goals (§31) · staged strategy (§32). No code changed.

---

## 35. Stage 1 Implementation Record — Semantic Target Resolution
**(added 2026-09-20; Stage 1 IMPLEMENTED — Stages 2–7 remain)**
**(corrected 2026-09-20 after real-browser QA: see "Stage 1 discrepancy
and fix" below — the original "DONE" claim was based on resolver/module
tests only and missed two integration defects)**

### Status: FIXED IN CODE AND BROWSER-VERIFIED (within Stage-1 scope)

The triggering request class now resolves: the finding referred to by
description-level evidence language is resolved from the authoritative
candidate set without manual selection — verified through the REAL
browser against real Risk Platform data (case U00010, finding F8
"Opposite Trade Ratio.").

### Files changed

- `app/context_resolution.py` — `ResolutionOutcome` gains
  `resolution_kind` (explicit|inferred) and `resolution_reason`
  (user-safe); `ContextResolver` gains `llm_assist` flag
  (None=auto/False=deterministic-only) and `_llm_assist_enabled()`;
  the vague "this/that" early-unresolved guard moved BELOW the semantic
  assist (conversational references now resolvable, bounded-unresolved
  otherwise); `_llm_resolve` upgraded to the §8 structured contract:
  richer candidates (id/title/description/signals — descriptions are
  the B8 fix), three-state output (RESOLVED/AMBIGUOUS/UNRESOLVED),
  confidence policy (non-high → ambiguous, never a fabricated target),
  invented-id rejection, AMBIGUOUS with validated candidate list.
- `app/investigation_service.py` — `allow_llm_resolution` now defaults
  to **True** (semantic assist enabled on the product path); when False
  the resolver is constructed deterministic-only (prior hard-disable
  behavior preserved).
- `app/planner_v2.py` — no change this phase (deterministic hooks and
  vocabulary untouched; the resolved focus flows through existing
  context injection).
- Tests: `tests/test_target_resolution_stage1_v1.py` (28 — the §21
  matrix A–J), `tests/test_target_resolution_llm_v1.py` (10 — Layer B
  real-LLM, real RP case U00010), plus updates to
  `test_context_resolution.py` (new-schema assist tests; deterministic
  default helper) and explicit deterministic-mode flags in
  `test_investigation_service.py` / `test_ux_routing_v1.py` /
  `test_investigation_e2e_v1.py` fixtures that pin the deterministic
  gating path.

### Behavior now

- Deterministic first: explicit focus (RESOLVED-explicit), finding id,
  exact/normalized title token match (RESOLVED-inferred, no LLM).
- Semantic assist fallback: bounded LLM classification over
  id/title/description/signals of the authoritative candidates only;
  high-confidence unique → RESOLVED-INFERRED; multiple plausible →
  AMBIGUOUS with listed candidates; no match / invented ids / low
  confidence → AMBIGUOUS-or-UNRESOLVED, never a fabricated target.
- Assist disabled (no provider configured or `allow_llm_resolution=
  False`) → prior deterministic behavior verbatim.
- The triggering request "具体展开一下Opposite Trade Ratio的证据"
  resolves RESOLVED-INFERRED to the coordinated-trading finding (unit +
  live Layer B on real RP case U00010).

### Test / evaluation results

- Stage-1 matrix: 28/28; Layer B real-LLM: 10/10 (single-run variance
  observed on the ambiguous candidate case — model occasionally returns
  UNRESOLVED instead of AMBIGUOUS; bounded by policy, tracked as
  model-quality variation per Layer-B philosophy, prompt strengthened
  to prefer AMBIGUOUS when candidates share the key noun).
- Full backend suite (RP up): 1014 passed; 16 failures = legacy pre-V2
  environment set (error_handling/evidence_integration/provenance_model
  — RP/LLM-availability sensitive, subset varies run-to-run, identical
  to the pre-phase baseline under matching RP state) + 1 Layer-B LLM
  variance run. **No V2-runtime failures; no regressions.**
- QA boundaries: B8 covered (description matching — the assist
  candidates include descriptions; test asserts description content
  reaches the LLM and resolution follows it); B9 addressed in scope
  (target resolution is now a separate semantic mechanism from phrase
  routing; no new regexes added).

### Deviations

- `ResolutionOutcome` also carries `resolution_reason` (user-safe)
  beyond the baseline's minimum metadata — needed for §17 response
  composition and §18 diagnostics.
- The assist's LLM budget raised to 2048 tokens (thinking-style models
  emit no text below that — same captured lesson as the planner).
- Stage-1 scope guard: the Loop path still receives focus exclusively
  via `context_action`; open-ended goals in the LOOP path are Stage 2.

---

## 35a. Stage 1 Discrepancy and Fix (2026-09-20, post-claim browser QA)

### Original claim vs actual browser behavior

The Stage-1 record above claimed semantic target resolution working.
Independent real-browser testing then showed, for case U00010 with NO
manual selection:

- `"Opposite Trade Ratio展开细说一下具体情况"` and
  `"具体展开一下Opposite Trade Ratio的证据"` → **"No specific target
  could be resolved from this request."** (`unresolved_reference`).

### Root causes (two, both confirmed by reproduction)

1. **Stale runtime process (environment boundary, per CLAUDE.md
   environment honesty)**: the :8001 backend process predated the
   Stage-1 implementation (`context_resolution.py` modified hours after
   the process started). The browser was exercising pre-Stage-1 code.
   Restart → the unresolved_reference symptom disappeared.
2. **Deterministic title matching was gated behind an operation-keyword
   pre-filter (the real product defect, now fixed)**:
   `ContextResolver.resolve()` ran title-token matching only when the
   request contained investigation-keyword vocabulary
   (`looks_findingish`: flagged/finding/pattern/frequency/relationship).
   The triggering request contains the EXACT authoritative title
   ("Opposite Trade Ratio") but none of those keywords, so the
   deterministic matcher — which needs NO LLM — was skipped entirely.
   Target resolution was ordered behind operation understanding,
   violating baseline §4 (B8/B9 class). Fix: title matching now applies
   to EVERY request reaching that resolution stage (event intents keep
   precedence); the conservative token-majority rule still gates the
   match itself. No phrase lists, no special cases.

### Broken integration boundary

`looks_findingish gate → deterministic title matcher` inside
`ContextResolver.resolve()` — and, at the process level, the stale
backend serving old source. Both documented in
`2026-09-19-agent-qa-boundaries-v1.md` (B10) and QA report §3/§15.

### Why earlier Stage-1 tests missed it

- Resolver tests called `ContextResolver.resolve` directly with texts
  that happened to contain investigation keywords, or with a FakeLLM —
  never the browser-shaped request with assist DISABLED against
  real-shaped candidates (the deterministic gate was invisible).
- The service/API tests that pinned unresolved behavior ran with the
  assist explicitly disabled or focused fixtures, and were later made
  explicitly deterministic (`allow_llm_resolution=False`) — correct for
  their purpose, but no test covered the browser-shaped
  title-reference request through the service.
- The Layer-B real-LLM suite passed because the assist (when it runs)
  resolves correctly — it never exercised the deterministic-only path.
- Missing layer: an API-level test posting the browser-shaped request
  through `/turn` with assist disabled. **Added**
  (`TestAPIWiring::test_browser_shaped_request_resolves_f8_through_turn_api`).

### Fix and acceptance evidence

- Fix: gate removed (title matching applies to every request at that
  stage) — verified: both requests resolve
  `RESOLVED-INFERRED → F8` with `llm_assist=False` and with the assist
  enabled.
- Browser (real stack, RP up, real LLM): no unresolved_reference;
  context focus = F8 (agent_resolved/inferred); visible bounded
  outcome. Live-LLM planner variance observed and documented: the
  planner may select `explain_signal` (unsupported on the contextual
  Opposite Trade Ratio finding → honest bounded failure, **Stage-2
  objective-understanding boundary**, out of scope here) or
  `inspect_self_opposite_trades` (succeeds, showing the ratio) — focus
  F8 correct in every observed run.
- Regression coverage: API-level browser-shaped test (deterministic,
  assist disabled) + resolver matrix + Layer B suite.

### Remaining Stage-1 limitations (honest)

- Operation selection for a resolved target remains real-LLM-driven and
  may pick an unsupported operation until Stage 2 (objective
  understanding) — bounded, honest, non-fabricated, target correct.
- The assist requires a configured LLM; with no LLM, only
  deterministic title/id resolution is available (by design).

---

## 35b. Stage 2 Refinement — from real browser experiments
**(added 2026-09-21; doc-only revision — nothing here is implemented.
Three real browser experiments on the current build exposed a Stage-2
boundary deeper than target resolution.)**

### Observed evidence (real browser, real RP, no manual selection)

| # | User request | Observed behavior |
|---|---|---|
| E1 | "那这个账号有与其他账号做关联对冲交易的情况吗" | `cross_account_trade_analysis` selected (semantically plausible); tool returned `INSUFFICIENT_DATA`; task shown as **completed** |
| E2 | "那这个账户都和哪些账户有关联关系" | same tool selected; `INSUFFICIENT_DATA`; task shown as **completed** |
| E3 | "这个账户的 Linked Account Network 具体是什么？" | same tool selected; `INSUFFICIENT_DATA`; task shown as **completed** |

Findings, stated conservatively: E1's capability selection was at least
directionally correct, but task-completion handling was semantically
incorrect (INSUFFICIENT_DATA shown as completed). E2's goal is broader
than opposite-trade analysis (related-account/network discovery), yet
routing selected the same tool — consistent with lexical overlap
("关联账户 / related accounts") rather than goal understanding. E3's
target appears to be the existing Linked Account Network finding and the
goal is to inspect that network; the governed capability set has no
network-cluster exploration capability, the gap was not surfaced, and a
related-but-not-sufficient capability was executed and reported as
completed.

### Distinct Stage 2 concepts (frozen — not collapsed into "intent classification")

1. **Question / Goal Understanding** — "What does the user actually
   want to know, inspect, explain, compare, or investigate about the
   resolved target?" Separate from Target Resolution ("What object is
   the user referring to?"). Examples: E3's target = Linked Account
   Network finding; goal = understand/inspect the network structure —
   NOT opposite-trade analysis. E1's goal = determine whether
   cross-account opposite-side trade relationships exist — for which
   `cross_account_trade_analysis` IS plausibly suitable.
2. **Capability Suitability** — a capability is suitable only when its
   documented purpose and output semantics can MATERIALLY satisfy the
   user's current goal. Suitability is NOT keyword overlap, name
   similarity, phrase match, or "the tool sounds related": E2/E3 show
   "involves other accounts" does not make the trade-analysis tool
   sufficient for network-relationship questions, while E1 shows it is
   suitable for an actual opposite-trade question.
3. **Capability Gap Detection** — after goal understanding, determine
   whether the governed set contains a capability whose output can
   satisfy the goal: `CAPABILITY_AVAILABLE` / `CAPABILITY_PARTIAL` /
   `CAPABILITY_UNAVAILABLE`.

Related-but-not-sufficient is a NEW failure mode (E2/E3): a capability
may share the topic (accounts / cross-account relationships) without
being able to answer the goal. Sharing "multiple accounts" does not
establish suitability. This is now a Stage-2 evaluation boundary
(boundary doc B11).

### Task-completion refinement (strengthens the loop baseline's
### task-level completion boundary)

Frozen from E1–E3: a tool result does not prove the selected capability
was appropriate; a tool result does not prove the user's goal was
satisfied; a non-error execution does not imply task completion.
Specifically: `INSUFFICIENT_DATA` must not by itself mark the task
completed; `NO_MATCH` must not be reported as having answered a
different network-relationship question. Completion requires alignment
between user goal + appropriate capability path + sufficient evidence +
the recorded completion criteria (Agent Loop baseline §3). This becomes
a first-class evaluation dimension (§19 of the matrix additions below).

### Capability-gap response (expected behavior)

Target resolved + goal understood + no suitable capability → the Agent
must NOT select a merely related capability to appear productive,
fabricate an answer, or declare completion. It gives a bounded
capability-gap response — conceptually: "我理解你在问这个账户的 Linked
Account Network。目前的调查工具可以检查跨账户交易关系，但还无法展开
网络/关联簇本身。" Exact wording is not required; the product behavior
is understand → identify gap → explain the boundary honestly.

Distinguish: AMBIGUOUS user goal → clarification; CLEAR goal + no
suitable capability → capability-gap response; CLEAR goal + suitable
capability + data unavailable → BLOCKED / `INSUFFICIENT_DATA` per
runtime semantics. Do not ask clarification merely because the
capability is missing; do not report a capability gap as a tool error;
partial capability support may be offered but must be labeled partial.

### Goal-directed, not tool-directed (reaffirms §16)

Open-ended means the user can express goals without knowing internal
tool names — it does NOT mean "find any vaguely related tool."

### LLM vs system (unchanged boundary, applied to Stage 2)

The LLM may infer the goal, compare it against governed capability
descriptions, propose a suitable capability, identify potential gaps,
and propose clarification. The system controls the actual capability
set, registry, required context, permissions, scope, inputs, execution,
deterministic results, and completion enforcement. The LLM cannot
invent a capability or declare an unsuitable capability satisfying.

### Stage 2 golden scenarios (added to the evaluation matrix)

- **GJ-ST2-01**: "那这个账号有与其他账号做关联对冲交易的情况吗" →
  goal = opposite-trade relationship check; capability suitable →
  execute per data availability; final task status must reflect actual
  evidence sufficiency (E1 must no longer complete on
  `INSUFFICIENT_DATA`).
- **GJ-ST2-02**: "那这个账户都和哪些账户有关联关系" → do NOT equate to
  cross-account trade analysis; distinguish relationship/network
  discovery from opposite-trade analysis; surface the capability gap if
  the governed set cannot satisfy it.
- **GJ-ST2-03**: "这个账户的 Linked Account Network 具体是什么？" →
  target = Linked Account Network; goal = inspect network structure;
  explicit suitability evaluation; if no suitable network-exploration
  capability exists → capability-gap response, never an executed
  unsuitable trade analysis marked completed.

### Additional Stage-2 evaluation cases

Suitable capability · related-but-unsuitable · no capability · partial
capability · multiple suitable capabilities · ambiguous goal · target
resolved but goal unsupported · target unresolved · goal supported but
data blocked · tool error after correct selection — each in EN /
简体 / 繁體 / mixed, semantically asserted. Task-completion cases:
unsuitable+successful-execution ≠ completion; `INSUFFICIENT_DATA` ≠
completion; capability gap ≠ tool error; blocked ≠ unsupported;
partial ≠ full; completion requires goal alignment.

### Current vs target (honest)

Current: deterministic routing hooks, limited semantic target
resolution (Stage 1), existing capability set, Agent Loop — no goal
understanding, no suitability evaluation, no gap detection. Target
Stage 2: structured question/goal understanding, suitability
evaluation, gap detection, capability-aware planning, semantic rather
than primarily lexical selection. **Stage 2 is NOT implemented.**

### Refined Stage 2 implementation sequence (supersedes §32 row 2+
### granularity for this stage)

2.1 Question/goal representation · 2.2 Capability metadata made
planner-visible · 2.3 Capability suitability evaluation · 2.4
Capability gap detection · 2.5 Regex/hooks demoted to fast paths ·
2.6 Capability composition via the existing loop · 2.7
Conversation-native Activity UX · 2.8 Open-ended QA/exploratory
evaluation. None implemented yet.

### Non-goals (unchanged)

No new network/cluster tool; no tool added merely to satisfy these
cases; no multi-agent; no web research; no long-term memory; no MCP;
no architecture sync; no wholesale replacement of deterministic
routing; no unrestricted autonomy.