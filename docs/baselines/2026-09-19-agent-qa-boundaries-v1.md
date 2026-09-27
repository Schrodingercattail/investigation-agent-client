# QA Boundary Document V1 — Agent Activity / Agent Loop

**Status**: ACTIVE — evidence-based boundary findings from the
2026-09-19 E2E QA pass (`docs/qa/2026-09-19-agent-activity-v1-e2e-qa.md`).
**Rule**: a boundary is recorded here only when concrete reproduced
evidence exists from testing. Each entry names the recurring failure
class, the evidence, and the contract rule that prevents recurrence.

---

## B1 — New API surfaces vs pending UI interaction state

**Failure class**: an interaction works through one API surface but a
NEW parallel surface silently ignores the interaction state the UI
treats as active. Module tests green (the new endpoint tested
standalone), E2E broken (the user journey fails).

**Evidence (QA-01, P1)**: finding selection is carried as
`context_action` on turn requests; the new `POST /goal` endpoint
accepted only `{goal}` and the UI's "Run loop" sent only that — so the
loop ran case-level while the UI showed the finding focused, failing
with a planning error on the primary journey (reproduced 2×).

**Contract rule**: every new execution endpoint must explicitly declare
which pending UI state it honors (context_action, focus, scope) — and
the UI must send it. Endpoint tests must include the state-carrying
variant, not just the bare one.

## B2 — Derived snapshots are not re-synced at terminal transitions

**Failure class**: the runtime keeps a derived snapshot (plan steps and
statuses) that is synchronized only inside the loop; the terminal path
returns without a final sync, so a completed run renders its executed
step as "pending".

**Evidence (QA-02, P2)**: both real terminal runs (cross-account,
self-opposite) showed the executed step as pending in the Activity plan
block while the timeline showed "Step completed".

**Contract rule**: any persisted projection that feeds the UI must be
re-synced as part of the terminal transition (emit → flip → re-sync →
persist), verified by an API-level terminal-state test that asserts
snapshot consistency.

## B3 — Component state outlives the entity it belongs to

**Failure class**: page-level component state (a task id, a selection)
is set while viewing entity A and never cleared when the user switches
to entity B; B's view then renders A's data.

**Evidence (QA-03, P2)**: opening a pristine investigation after viewing
a loop run kept rendering the previous investigation's Agent Activity
trace (wrong case, wrong goal, failed run) — `activityTaskId` was never
cleared when the newly opened investigation had no loop task.

**Contract rule**: investigation-scoped UI state must reset on
investigation switch (single effect keyed by investigation id); a
"restore" helper may set state only for the current entity and must
explicitly clear otherwise. Tested by an entity-switch component test.

## B4 — Retry policy covers one dependency layer; availability failures happen at another

**Failure class**: the loop's bounded retry covers tool-level
integration errors, but planning itself depends on live upstream data
(finding capabilities come from RP through the provider). An upstream
outage therefore surfaces as a planning failure at iteration 0 — before
any retryable boundary is reached.

**Evidence (QA-04, P2)**: RP down → loop goal fails
`planning_failure` at iteration 0 even for a focused investigation; RP
restored → identical goal completes. Tool-level retry/blocked was not
reachable end-to-end during the outage.

**Contract rule**: capability/eligibility inputs needed for planning
should tolerate upstream outages (cached/context-carried capabilities)
or the product must state the dependency; loop failure taxonomy should
distinguish "could not plan (upstream unavailable)" from "plan invalid".

## B5 — One digest string serving two audiences (LLM context vs UI)

**Failure class**: the compact observation summary written for the
decision/planner context is reused verbatim as the user-facing timeline
line, producing semi-technical UI text
(`finding_drilldown:success self_opposite_trades record_count=42
ratio=0.4524`).

**Evidence (QA-05, P3)**: reproduced on every real run's
observation.recorded row.

**Contract rule**: the observation projection carries (a) a
machine-facing digest for decisions/planning and (b) a user-facing
phrase for the UI. They are separate fields with separate wording rules;
the UI never renders (a).

## B6 — Deterministic text hooks vs language coverage

**Failure class**: deterministic intent routing (regex hooks) covers a
subset of real user language; unmatched phrasings silently fall to LLM
vocabulary where eligibility may make them fail.

**Evidence (QA-06, P3)**: deterministic cross-account hook matches
simplified 跨账户 only; traditional 跨賬戶 phrasing is not matched
(its runtime failure was additionally masked by QA-01).

**Contract rule**: every deterministic hook lists its covered phrasings
in the baseline; CJK variants (simplified/traditional) are test cases of
the routing suite.

## B7 — "Module tests green" ≠ "journey works"

**Failure class**: the shipped feature had 40 passing loop/API tests and
14 passing component tests, yet the primary user journey failed on
first real use (QA-01) and a cross-entity leak shipped (QA-03). The API
seam tests exercised the endpoint without the UI's interaction state;
the component tests exercised rendering without the investigation-switch
flow.

**Contract rule**: each feature's acceptance gate must include at least
one full user journey through the real UI (browser-level) plus one
entity-switch/state-hygiene scenario — both against the real running
stack, per the QA report's method.

---

Standing pre-existing boundary note: module suites staying green while
43 environmental/legacy suite failures persist is documented in the QA
report §17 and must not be read as either proof or disproof of feature
health.

---

## B8 — Target resolution covers titles + explicit selection, not authoritative descriptions

**Failure class**: the user references evidence/fact language that the
Risk Platform itself states inside a finding's DESCRIPTION (e.g. the
"Opposite Trade Ratio" evidence, which the authoritative finding text
describes as "An opposite-trade ratio of 45.24% exceeded the 40%
threshold…"), but target resolution only matches finding TITLES by
token-majority plus explicit UI selection. The reference is
unresolvable even though it is grounded in authoritative case data.

**Evidence (observed repro, 2026-09-20)**: user request
"具体展开一下Opposite Trade Ratio的证据" with no manual selection →
"No specific target could be resolved from this request." Code:
`ContextResolver._match_findings_by_name` matches titles only;
description/signal/evidence-level references have no resolution path;
the built-in LLM-assisted resolution (`_llm_resolve`,
candidate-restricted to real finding ids) is disabled by default
(`allow_llm_resolution=False`).

**Contract rule**: target resolution must operate over the
authoritative candidate set INCLUDING descriptions/signal/evidence
names (LLM-assisted, candidate-restricted — never free-form), record
whether the target was explicit or inferred, and distinguish
unresolved-target from ambiguous-target from unsupported-operation.
Contracted for the open-ended phase:
`2026-09-20-open-ended-investigation-conversation-ux-v1.md` §4/§11.

## B9 — Phrase-shaped intent routing vs semantic investigation objectives

**Failure class**: investigation routing depends on (a) deterministic
regex hooks and (b) fixed intent→step guidance lines shaped like
commands. Natural investigative language that is vague, mixed-language,
or describes an OBJECTIVE rather than a command falls outside both, and
there is no objective-level understanding layer to fall back on.

**Evidence (observed repro + code)**: the planner's understanding is
the SYSTEM_PROMPT intent list plus hooks (`_OPPOSITE_TRADE_RE`,
`_CROSS_ACCOUNT_TRADE_RE`, stream regexes); the loop's decision layer
understands CONTINUE/RE_PLAN/STOP/BLOCK but not open-ended objectives.
Vague CJK requests ("再深入看看", "把这个结论的证据讲清楚") and
description-level references therefore have no reliable path.

**Contract rule**: semantic objective understanding (a frozen,
extensible objective vocabulary inferred by the LLM over authoritative
context) is the primary mechanism; regex hooks are demoted to fast
paths and safety guards. Acceptance is semantic (objective + target
resolved), never phrase-list matching. Contracted for the open-ended
phase: `2026-09-20-open-ended-investigation-conversation-ux-v1.md`
§5/§16.

## B10 — Target resolution gated behind operation-keyword pre-filter

**Failure class**: deterministic target resolution (title matching) is
placed AFTER an operation/intent keyword pre-filter, so a request that
names an authoritative finding title but uses none of the
investigation keywords never reaches the matcher — the target is lost
before resolution is even attempted.

**Evidence (observed repro, 2026-09-20)**: real browser, case U00010
(finding F8 "Opposite Trade Ratio.", capabilities
self_opposite_trades+timeline), request
"Opposite Trade Ratio展开细说一下具体情况" with no manual selection →
unresolved_reference. With the LLM assist disabled, the same request
against the real candidate set stayed unresolved because
`resolve()` gated `_match_findings_by_name` behind `looks_findingish`
(flagged/finding/pattern/frequency/relationship — none present), while
the request contains the exact title. A stale long-running backend also
masked the Stage-1 fix during first reproduction (environment finding,
see QA report §3).

**Contract rule**: target resolution must precede operation
understanding — deterministic title/id matching runs for every request
that reaches the resolution stage; operation/intent vocabulary may add
candidates but must never gate the matcher. Fixed in
`ContextResolver.resolve()` (gate removed; event-intent precedence
kept); API-level regression test:
`TestAPIWiring::test_browser_shaped_request_resolves_f8_through_turn_api`.
Related Stage-2 note: after the target is resolved, planner operation
selection may still pick unsupported operations (real-LLM variance) —
that is the contracted Stage-2 objective-understanding boundary, not a
target-resolution defect.

## B11 — Related capability ≠ suitable capability

**Failure class**: capability selection driven by lexical/topical
overlap ("both involve other accounts / 关联") instead of alignment
between the user's goal and the capability's documented output
semantics. A related capability gets executed and its result is
treated as an answer to a question it cannot address.

**Evidence (observed real-browser experiments, 2026-09-21)**: three
distinct user goals all routed to `cross_account_trade_analysis`:
(1) "那这个账号有与其他账号做关联对冲交易的情况吗" (opposite-trade
check — genuinely suitable); (2) "那这个账户都和哪些账户有关联关系"
(relationship/network discovery — broader than the tool); (3) "这个
账户的 Linked Account Network 具体是什么？" (inspect the linked-account
network — different goal entirely). All three runs returned
`INSUFFICIENT_DATA` and were shown as completed.

**Contract rule**: capability selection must compare the understood
user goal against the capability's documented purpose/output contract.
Topical relatedness never establishes suitability. Contracted as
Stage-2 "Capability Suitability" + "Capability Gap Detection"
(`2026-09-20-open-ended-investigation-conversation-ux-v1.md` §35b).

## B12 — Tool execution / semantic result ≠ user-task completion

**Failure class**: a completed (non-error) tool execution — even with a
bounded semantic result such as `INSUFFICIENT_DATA` — is surfaced to
the user as task COMPLETED, implying the user's goal was addressed.

**Evidence (observed, 2026-09-21)**: all three experiments above ended
with the task shown as completed after `cross_account_trade_analysis`
returned `INSUFFICIENT_DATA` — including E2/E3, where the selected
capability could not have answered the user's question in the first
place.

**Contract rule**: task completion requires alignment between user
goal + appropriate capability path + sufficient evidence + the recorded
completion criteria. `INSUFFICIENT_DATA` on an unsuitable or
insufficiently-scoped capability must surface as a capability-gap /
insufficient-evidence outcome — never as ordinary task completion.
Contracted: `2026-09-20-open-ended-investigation-conversation-ux-v1.md`
§35b (task-completion refinement; first-class evaluation dimension).

## B13 — User goal must be understood before capability selection

**Failure class**: the runtime selects a capability before
understanding what the user is actually asking, so selection degrades
to lexical matching and genuine capability gaps stay invisible (no gap
response, no UNSUPPORTED/BLOCKED distinction).

**Evidence (observed, 2026-09-21)**: E2 ("哪些账户有关联关系") and E3
("Linked Account Network 具体是什么") express goals the current
capability set cannot satisfy (network/relationship discovery), yet the
system neither detected the gap nor told the user — it executed the
lexically-nearest tool and reported completion (see B11/B12).

**Contract rule**: goal understanding precedes capability selection;
when the understood goal has no suitable capability, the system
responds with a bounded capability-gap explanation (distinct from
BLOCKED and from tool ERROR). Contracted:
`2026-09-20-open-ended-investigation-conversation-ux-v1.md` §35b
(Capability Gap Detection; capability-gap response).

**Evidence addendum (2026-09-21, real-browser E1–E5 reproduction)**:
the boundary is now reproduced end-to-end — requests expressing
account-relationship / network-discovery goals
("哪些账户有关联关系", "Linked Account Network 具体是什么") all routed
to `cross_account_trade_analysis` (lexically related, materially
unsuitable), returned `INSUFFICIENT_DATA`, and were shown as task
COMPLETED. Candidates were retained at the clarification boundary in
related runs but goal understanding never consulted them. This
confirms B11–B13 as a single connected failure chain: selection
without goal understanding (B13) → related-but-unsuitable capability
(B11) → false completion (B12). Contracted fix: M2 Stage 2
(`2026-09-20-open-ended-investigation-conversation-ux-v1.md` §35b).
