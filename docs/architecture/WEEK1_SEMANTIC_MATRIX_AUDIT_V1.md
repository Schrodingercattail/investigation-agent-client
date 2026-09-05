# WEEK 1 SYSTEM-LEVEL SEMANTIC MATRIX AUDIT

**Status**: AUDIT RECORD — V1
**Date**: 2026-09-03
**Scope**: Full Week 1 runtime semantic consistency check across P1–P20 principles, capability, scope, outcome, UI action, context, planner, executor, tool, response composer, artifact composer, persistence/recovery.
**Nature**: Read-only audit. No code modified. No audit steps rerun after production of this document.

## Primary Audit Question

> "Can the current Week 1 Agent handle every normal combination of
> user intent, context, capability, scope, execution outcome and
> response surface without semantic contradiction, silent fallback,
> scope widening, or internal-state leakage?"

---

# A. Semantic Model (One Page)

```
USER UTTERANCE
  │ intent (she wants X)          ← classified: investigation / artifact / capability-question / UI-action
  │ mentioned case refs           ← data, not targets
  ▼
ContextResolver (deterministic, pre-priority)
  │ case identity      = investigation binding (ctx.case_id) ≠ mentioned cases
  │ cross-case guard   → UNRESOLVED + guidance (no planner/execution/mutation)
  │ finding focus      = ctx.focused_finding_id + focus_source (explicit user > agent-resolved)
  ▼
PlannerV2 (LLM)  — sees eligible skills ONLY (registry × capability)
  │ skill selection + ordered steps (types+reasons only; no tool names, no params)
  │ PlanningFailure: NO_ELIGIBLE_SKILL / UNSUPPORTED_REQUEST / SKILL_NOT_ELIGIBLE /
  │                  LLM_OUTPUT_INVALID / LLM_UNAVAILABLE / PLAN_CONTRACT_VIOLATION
  ▼
Contract Checker (check_plan) — registry truth; defense in depth vs planner
  ▼
ExecutorV2 — executes; never plans/retries. Runtime argument injection (case_id/finding_id/top_n)
  │
  ▼ ToolProvider → domain tool → RiskPlatformAdapter → RP
  │
  │ ToolResultOutcome: success | empty | unsupported | integration_error | validation_error
  │   capability ≠ data availability: gates answer "can this finding support this action?"
  │   (evidence-derived, timeline/signal/opposite only); data states answer "what does RP hold?"
  │   empty ≠ failure; integration_error ≠ empty; unsupported ≠ unresolved
  ▼
Response Composer (deterministic) — ToolResults → bounded user wording;
  │ no internal identifiers, no [n] unless resolvable in the same response; scope: conversation
  ▼
Artifact Composer (deterministic) — markdown from provenance pool;
  │ scope containment (finding ≠ case); provenance = per-call rendered contributors
  ▼
Persistence (TaskStoreV2 sessions) — tasks, plans, calls, artifacts, turn responses,
  follow-ups; refresh-recovery; ONE investigation = ONE case (inv.case_id immutable)
```

**Key distinctions (non-interchangeable):** intent≠target; capability≠data availability; execution result≠semantic result; semantic result≠presentation scope; tool execution≠response composition; UI navigation≠agent request; empty≠failure; unsupported≠unresolved; integration error≠empty; case identity≠mentioned case.

---

# B. P1–P20 Traceability

| P | Invariant | Implementation boundary | Test coverage | Live/browser | Status |
|---|---|---|---|---|---|
| P1 | RP owns facts; normalize never manufactures | adapter normalization; marker-only citation association | adapter, provenance-model suites | ✅ live U00299/U90001 | ✅ |
| P2 | finding≠feature≠record≠event | normalization splits risk_features/records; feature labels | evidence-completeness, artifact suites | ✅ live (U90001 artifact separation) | ✅ |
| P3 | depth levels; evidence on request | intent→step (explain_signal/inspect_*) | planner, ux-routing | ✅ live | ✅ |
| P4 | requested evidence complete; never top-N-as-complete | `expose_complete_records`, complete=true contract, top_n only explicit | evidence-completeness, planner, signal suites | ✅ live U90001 (60 rows) | ✅ |
| P5 | answer the question, not everything known | composer branches per payload; artifact scope | ux-routing, semantic-cleanup | ✅ live | ✅ |
| P6 | answer first; no internal architecture | composer bounded wording | identifier-leak pins, autonomous-eval | ⚠️ live: Plan audit card + task card show skill/step ids (documented audit surfaces — accepted trade-off) | ⚠️(accepted) |
| P7 | readable + grounded | composer grounds per content; signal description fallback | signal-consistency pins | ✅ live | ✅ |
| P8 | [n] only resolvable in same surface | `_strip_citation_markers`; artifacts keep mapping | artifact/citation pins | ✅ live | ✅ |
| P9 | association → applicability; case-level ≠ finding-level | split_policy_refs; composer two contracts; artifact containment | semantic-cleanup (max2/complete/no-basis), policy suite | ✅ live F2/F3 | ✅ |
| P10 | provenance = actual content dependencies | render-time contributor accumulation | provenance pins (failed/prior/exact) | ✅ live audit (4-call case artifact) | ✅ |
| P11 | artifact scope = containment boundary | artifact_bundle scope branches (canonical fetch, no-basis statement) | scope-integrity, containment, cleanup | ✅ live U90001 | ✅ |
| P12 | one investigation = one case; conversation UI guidance | pre-priority cross-case guard; navigateTo no-task | cross-case suite (44), live | ✅ live incl. refresh | ✅ |
| P13 | follow-ups executable, no dead buttons, attached | registry select_followups; action_kind | followups suite, live | ❌ **dead chips when capability<full** (P0-1) | ❌ |
| P14 | distinct not-knowing states; wording=meaning not mechanism | composer outcome branches; tool error detail | cleanup suites (multi-state distinctness) | ✅ live | ✅ |
| P15 | LLM reasons, never owns facts | planner vocabulary-only; tools echo-only | planner safety pins | ✅ | ✅ |
| P16 | deterministic owns invariants | resolver/checker/executor/composer all deterministic | full suite | ✅ | ✅ |
| P17 | agentic without fabrication | grounded composer; no backfill | signal-consistency, artifact pins | ✅ | ✅ |
| P18 | failure-discovery loop | principles doc updated 2×; fixes at shared boundaries | regression suites | ✅ process | ✅ |
| P19 | capability transparency, internal + UI | skill_path_executable guard; chips | capability-matrix | ❌ same P0-1: chips imply support the planner cannot plan | ❌ |
| P20 | empty is valid | EMPTY outcome; composer per-meaning branches | policy/empty pins | ✅ live | ✅ |

---

# C. Capability Matrix

| capability | granted by | basis | runtime-executable | planner visibility | follow-up visibility | tool/binding | response semantics | failure semantics |
|---|---|---|---|---|---|---|---|---|
| timeline | derive_capabilities | >1 timestamped evidence item | yes | in timeline_investigation vocabulary | show_timeline | finding_drilldown (locked view) | complete timeline; bounded only explicit | EMPTY (no events) / INTEGRATION / UNSUPPORTED (timeline gate) |
| opposite_trades | derive | opposite-trade finding + ratio data | **no** (view unimplemented) | registered but non-executable (P19 guard) | no template (correct) | binding exists, `step_path_executable`=False | — | planner: non-executable path never offered; manual step → TOOL_NOT_IMPLEMENTED |
| signal_explain | derive | rule trigger OR detector naming | yes | explain_signal | explain_finding | signal_explain | grounded per RP rule/ML/graph | SUCCESS+evidence_missing (no backing rule) |
| policy_lookup | **none (case-wide)** | — (formerly: citation; removed in semantic correction) | yes | retrieve_policy | check_policy | policy_lookup | associated → max-2 applies-to-finding; no basis → no-basis data state | EMPTY (zero case citations) / INTEGRATION |
| fetch_case, generate_artifact | case-level (no caps) | — | yes | case_intake vocabulary | check_artifact (UI nav) | wired | intake summary / artifact confirmation | as above |

Checks: dead buttons — **found ❌ (P0-1)**. Non-executable capability entering planner — blocked ✅ (skill_path_executable). Inference from unrelated data — none (citation grant removed) ✅. Capability as domain-state proxy — fixed (policy) ✅; remaining timeline gate is evidence-derived, legitimate. Capability absence ≠ no data ✅ (cross-case guard tests; policy-as-data). Capability state ≠ execution state ✅ (gates inside tools, not executor).

Policy distinctions verified: retrieval capability (case-wide) ≠ finding-level association (`policy_refs`/`finding_policy_status`) ≠ case-level availability (citations[]) ≠ query result (matches). ✅

---

# D. Scope Matrix

| Action | allowed scope | context required | scope resolution source | prohibited | output containment | provenance containment |
|---|---|---|---|---|---|---|
| artifact | case/finding | case_id; finding_id when finding | executor `_artifact_arguments`: explicit wording > focus > case default (verified live) | other findings' content; case table in finding; no-basis list as finding basis | render-time contributor accumulation | = content scope ✅ |
| policy response (conversation, finding-associated) | finding | focus | composer `has_finding_basis` | unassociated case-level "applies" claim | max-2 | n/a |
| case-level fallback (conversation) | case | any | same | presenting as finding support | **complete set** | n/a |
| timeline/evidence/signal | finding | focus | executor injection | other findings' content | payload scope | via contributors |

Finding→case widening: blocked ✅ (no-basis statement, case-table exclusion — verified live). Previous-finding leakage: blocked ✅. Provenance=content scope ✅.

---

# E. Outcome State Matrix

| outcome | meaning | executed? | task | tool call | artifact | context mutation | response | retry? | coexist with empty? |
|---|---|---|---|---|---|---|---|---|---|
| SUCCESS | authoritative result | yes | completed | yes | artifact tool only | never | grounded answer | n/a | payload-level evidence_missing coexists ✅ |
| EMPTY | valid query, nothing there | yes | completed (step SUCCESS) | yes (recorded) | never | never | "no matching data"/per payload | n/a | — |
| UNSUPPORTED | finding gate | yes (gate ran) | failed | yes (recorded) | never | never | scoped capability wording | n/a (data state) | no |
| INTEGRATION_ERROR | RP failure | attempted | failed | yes | never | never | "cannot be reached, retry" | **yes** | no (different worlds) |
| TOOL_NOT_IMPLEMENTED | platform gap | no | failed | no | no | never | "not available in current system" | no | no |
| UNRESOLVED_REFERENCE | no safe target (incl. cross-case) | no | failed+error slug | no | no | never | guidance; cross-case names both cases | yes (rephrase) | no |
| AMBIGUOUS/INVALID | multiple/malformed candidates | no | failed (clarification_needed) | no | no | never | clarification | n/a | no |
| PLANNER FAILURE | no valid plan | no | failed | no | no | never | bounded per-code wording | yes (LLM_UNAVAILABLE) | no |
| EXECUTION FAILURE | step failed | partial | failed | partial | never | never | semantic outcome | yes | no |

Collisions searched: no-data→unsupported fixed (policy) ✅; no-finding-policy→failed fixed (data state) ✅; policy gap→transaction gap fixed ✅; planner failure→case-not-found none ✅; cross-case→current-case fallback fixed ✅; UI action→planner request blocked ✅; empty→outage none ✅. **One real collision: UNRESOLVED/AMBIGUOUS task status is `failed` with the internal error slug (`unresolved_reference`) visible on the task-card UI** — P2 (bounded audit surface; conversational wording correct; UI-side classification could render it, but is documented rather than hidden).

---

# F. Intent × Context Matrix (condensed; full 15×3 analyzed)

| request | A: no investigation | B: case, no focus | C: case + focus |
|---|---|---|---|
| case reference (new) | creates investigation via CRR | — | — |
| same-case reference | creates | intake runs (fetch+optional artifact) | intake runs; focus kept |
| cross-case reference | normal new investigation (CRR) | **UNRESOLVED guidance, no execution** | **same — guard pre-route** ✅ |
| finding selection | guidance/unresolved | sets focus (context_action) | refocus; ack turn, no task |
| timeline/evidence/signal | guidance needs focus | guidance/case-level only | executes; complete evidence |
| policy question | unroutable | via capability | **executes on any finding**; two presentation contracts |
| case artifact request | — | fetch+artifact, case | case scope (explicit wins) ✅ |
| finding artifact request | guidance needs focus | guidance needs focus | artifact, strict containment |
| unsupported investigation | bounded LLM-output-invalid | NO_ELIGIBLE_SKILL bounded | UNSUPPORTED (gate) or bounded |
| capability question | guidance turn, no task | same | same |
| UI navigation | — | chip only, setRightTab only | same ✅ |
| ambiguous target | — | clarification, no mutation | same |
| empty-result query | — | EMPTY=completed, "no matching data" | per-payload empty semantics |

---

# G. UI Action Classification

| follow-up | classification | verification |
|---|---|---|
| explain_finding | AGENT_ACTION (intent→planner) | ✅ |
| show_timeline | AGENT_ACTION | ✅ |
| check_policy | AGENT_ACTION | ⚠️ see P0-1 |
| "Select a finding from the Findings panel…" | INSTRUCTION (navigationHint, non-clickable guidance) | ✅ no payload submitted |
| check_artifact | UI_NAVIGATION_ACTION | ✅ `navigateTo` only `setRightTab('artifacts')`; no turn/Task/planner — verified in code; display text matches action |

Display/payload/routing agreement, except P0-1 ✅.

---

# H. LLM Responsibility Map

Invocations: 1) PlannerV2 — intent→{skill, goal, ordered steps+reasons} within registry vocabulary (2048 tok, 0.1); downstream executor. 2) ContextResolver LLM assist — only after deterministic ambiguity, candidate-ID selection (64 tok, 0.0), invented→NONE. Deterministic owns: identity/cross-case/ambiguity, contracts, scope containment, provenance, bounded honesty, empty semantics, artifacts. **No phrase→answer map**: composer branches key on ToolResult outcome/payload shape, not user text; artifacts are pure functions. Verdict: **the LLM retains meaningful investigation-planning responsibility**; deterministic routing owns only non-agent operations — desired and correct. ✅

---

# I. Planner → Executor Responsibility Map

Single owners verified: case identity/cross-case→resolver; intent class→resolver priority 0; plan→planner; plan validity→check_plan (planner + executor defense in depth — intentional double-check, single truth registry); tool binding/locks→registry; execution→executor (zero retry); injected args (case/finding/top_n)→executor runtime; authoritative data→tool/RP; artifact containment→artifact composer; provenance→render-time accumulation; user wording→composer. Double-decision risk points: **capability is evaluated independently by followups (`_is_executable`) and skill eligibility (planner) — inconsistent (root cause of P0-1).** All other surfaces single-owner ✅.

---

# J. Response Composition Matrix

| source | allowed claims | forbidden | verified |
|---|---|---|---|
| generic evidence_missing | actual transaction/record gap | gaps from unrelated payloads | ✅ fixed post artifact/policy exemptions |
| policy evidence_missing | no-basis statement + case-level list | "applies to this finding" | ✅ |
| unsupported capability | finding-scoped phrase | platform-level, no-policy-found | ✅ fixed |
| unresolved/cross-case | guidance naming both cases | execution fallback | ✅ fixed |
| integration_error | unreachable+retry | "nothing there" | ✅ |
| empty | per-payload meaning | padded success | ✅ |
| artifact success | created+location; only its own gaps | inherited gap sentences | ✅ fixed |
| identifiers | — | any internal id in chat | ✅ pinned; Plan card = audit surface (P2) |

---

# K. Artifact Composition Matrix

| section | scope | source | inclusion | exclusion | contributor rule |
|---|---|---|---|---|---|
| Finding | both | canonical fetch (latest success) | target finding (once) | other findings; per-fetch duplicates | canonical fetch |
| Timeline | finding (+case rows) | drilldown events | this-scope events | no importance labels | calls whose rows render |
| Evidence | any (only complete) | view=evidence complete=true | verbatim complete set | bounded previews | calls whose records render |
| Signal | finding | signal_explain | description fallback; structured echo | inversion/no backfill | calls whose lines render |
| Policy | scope-branched | per new boundary | associated table / no-basis statement / case table (case) | case lists in finding | calls rendering content |
| Evidence Gaps | both | per-payload gaps | named items | "insufficient" header | calls whose rows render |
| Provenance | =content | render-time accumulation | once per call | failed/prior/non-contributing | — |

All verified live (U90001) ✅.

---

# L. Persistence / Recovery Matrix

| turn type | persisted | restored | context/focus | artifacts | no case switch |
|---|---|---|---|---|---|
| successful task | ✅ | ✅ (plan present) | ✅ | ✅ | ✅ |
| empty task | ✅ | ✅ | ✅ | n/a | ✅ |
| failed task (planning/execution) | ✅ | ✅ (post-fix `detail.plan||task_response_text`) | ✅ | n/a | ✅ |
| cross-case rejection | ✅ response | ✅ (post-render fix) | unchanged | none | ✅ live |
| UI navigation | n/a no task | n/a | n/a | n/a | n/a |
| new investigation | history kept | resets current id | fresh context | empty | ✅ live |

Note (P2): failed follow-up turns persist `task_response_text` + chips; restored failed turns also render the raw `task.error` slug on the task card (same E caveat).

---

# M. Test Coverage Matrix

| invariant | unit | integration | E2E/API | browser |
|---|---|---|---|---|
| P9 two contracts + no-basis | ✅ | ✅ | ✅ | ✅ |
| P10 provenance=contributors | ✅ | ✅ | ✅ | ✅ |
| P11 containment | ✅ | ✅ | ✅ | ✅ |
| P12 cross-case | ✅ | ✅ (focused+not) | ✅ | ✅ |
| P14 state distinctness | ✅ | ✅ | ✅ | ✅ |
| P13/P19 chip executability | ⚠️ partial | ❌ **combination gap** | ❌ | ❌ |
| EMPTY semantics | ✅ | ✅ | ✅ | ✅ |
| persistence/recovery | ✅ | ✅ | ✅ | ✅ |
| rejected-turn persistence post-refresh | — | — | — | ✅ |

Blind-spot pattern: invariants were tested per-branch with full-capability findings plus "chips are data" — **the chip-click×planner-eligibility combination was never tested under partial capability** (exactly the cross-case failure class: branch tested, combination not). Second instance: the ui_navigation chip is never routed through the planner (correct, but never negatively tested). The explicit-case wording regex in `_artifact_arguments` is unit-pinned but was not live-tested against every entry path (verified directly — correct).

---

# N. Documentation Consistency

- AGENT_RESPONSE_EVIDENCE_PRINCIPLES_V1: current (P14 note covers the data-vs-capability correction). ✅
- AGENT_CLIENT_ARCHITECTURE_V2 §4.x: matches runtime; the cross-case guard description in the resolver section says "case-level-branch timing" — **stale by one line** (P3 doc update).
- SKILL_MODEL_V1: timeline_investigation required_capabilities=["timeline"] authoritative — runtime matches; **FOLLOW_UP_MODEL examples show unscoped capability requirements**, and its interaction with P0-1 is undiscussed (P3 note).
- FOLLOW_UP_MODEL_V1: the "executable-only rule" lists 4 conditions but omits "target skill plannable for the current finding" — the documentation gap behind P0-1 (P3 doc fix; the post-fix test will encode it).
- DOMAIN_MODELS_V1: FindingCapability doc lists policy_lookup as a Week-1 vocabulary example without the grant removal — stale (P3).
- API_CONTRACT_V1: turn status enum documents `clarification_needed`, but the API returns `failed` for AMBIGUOUS/UNRESOLVED — schema comment stale (P2: bounded; frontend switches on status text).
- README: high-level content matches runtime.

---

# O. Findings

## P0 (freeze blockers)

**P0-1 — Follow-up chips are dead for timeline-ineligible findings.**
Example: focus a finding with capabilities `["signal_explain"]` (no timeline) → the `explain_finding`, `check_policy` chips are offered → clicking either → PlanningFailure `SKILL_NOT_ELIGIBLE`; the task fails; the user gets a failed turn from a product chip they were correctly told is supported ("Why is this finding flagged?" as free text on that same finding fails eligibility identically). Reproduced live.
Violates: P13 (executable-only, no dead buttons), P19 (transparency must not imply an unsupported path — planner-side and UI-side), plus the Part 8 finding of followups/skill-eligibility double ownership.
Root cause: `followups._is_executable` checks that the target *tool* is implemented but never checks that the target *skill* is plannable for the current finding's capabilities; `eligible_skills_for_finding` owns that decision.
Owning boundary: `followups._is_executable` must consult skill eligibility (single truth: `eligible_skills_for_finding`/`check_skill_eligibility`); equivalently, gate chips on SKILLS' required_capabilities directly.
Why tests missed it: the followups suite pins chip sets on full-capability findings and per-capability removal, but never the combination "chip's required_caps ⊆ finding caps ≠ chip's target skill plannable". Same branch-vs-combination blind spot as the cross-case defect.
Fix is shared (one check of each chip's `target_skill` against finding caps) — not per-chip.

## P1 (should fix before freeze)

- None.

## P2 (accepted trade-offs / bounded)

- Plan audit card + task card show skill/step ids and error slugs (`timeline_investigation`, `unresolved_reference`) — documented audit surfaces (P6 secondary; Task Center contract pinned).
- API `TurnResponse.status` documents `clarification_needed`, while AMBIGUOUS/UNRESOLVED render `failed` — stale schema comment; frontend switches on status text; conversational wording correct.
- The CJK-adjacency case-reference regex reads bare 3-6 digit numbers ("within 300 days" → U300) — documented shared-parser trade-off; affects only cross-case guidance wording, never execution.
- Legacy V1 suites (provenance_model, evidence_integration, error_handling) fail against dead pre-V2 `app/tools.py`/`app/agent.py` code — outside V2, verified by stash each report.

## P3 (future)

- Documentation syncs (N above).
- "New Account with High Activity" detector-naming grant for `signal_explain` edge cases — keyword mirrors RP vocabulary; verified in practice; revisit as explicit RP metadata in Week 2.
- `_artifact_arguments`'s explicit-scope regexes are English-only; CJK artifact-scope wording falls back to focus-based defaults (bounded, deferrable).
- The frontend task card could render a bounded failure reason instead of the raw slug without lossy semantics.

---

# Verdict

**NOT READY TO FREEZE** — one P0 remains: follow-up chips can violate P13/P19 by submitting turns that are guaranteed to fail on any finding that lacks the `timeline` capability required by `timeline_investigation`. The fix is a single-ownership correction (followups must consult skill eligibility) plus the combination test class that previously missed the cross-case defect. Once that fix lands — with live verification that a signal_explain-only finding either suppresses the dead chips or their clicks succeed — every P0 is resolved and all other surfaces above show **No defect found** or documented trade-offs.

---

## SUPPLEMENT (post-audit resolution)

The follow-up-chip P0 identified above has since been **resolved**:

- Follow-up displayability now consults the Skill Registry's actual skill
  eligibility (`check_skill_eligibility`) — the same eligibility truth the
  planner uses — instead of only checking tool implementation
  (`backend/app/followups.py`).
- Skill eligibility and follow-up displayability therefore share a single
  runtime eligibility truth; dead chips are excluded by construction and
  pinned by `backend/tests/test_followup_eligibility_parity_v1.py`.
- The current runtime has been revalidated after subsequent Week 1 fixes
  (detector identity, explicit evidence streams, policy-scope clarity,
  Evidence Gaps removal).

The historical audit body above is preserved unchanged.
