# Agent Response & Evidence Principles V1

**Status**: ACTIVE — high-level product/architecture principles for the Investigation Agent
**Date**: 2026-08-31
**Parent**: `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md`
**Related**: `DOMAIN_MODELS_V1.md`, `SKILL_MODEL_V1.md`, `FOLLOW_UP_MODEL_V1.md`, `API_CONTRACT_V1.md`

> This document consolidates the lessons learned from validating the
> Investigation Agent against a real Risk Platform in real browser sessions.
> It defines what the Agent is responsible for, what the authoritative source
> is responsible for, and what the system must never do. It is deliberately
> NOT an implementation guide: lower-level contracts remain normative in
> their own documents.

---

## 0. Core Design Philosophy

The Agent is an **investigation layer over authoritative business systems**.

It is NOT:

- a replacement for the Risk Platform
- a second risk engine
- an independent evidence database
- an LLM-generated source of truth

The architectural relationship is fixed:

```
Authoritative system (Risk Platform)
    → facts / findings / evidence / policy
→ Agent tools (bounded, capability-gated)
→ reasoning / planning / orchestration
→ grounded response
→ investigation artifact
```

The Agent may interpret, organize, explain, and guide. The Agent must not
silently alter authoritative facts. Everything else in this document follows
from this position.

---

## P1 — Source-of-Truth Principle

**Normalize facts, do not manufacture facts.**

The Risk Platform owns:

- finding identity, finding count, and finding definitions
- authoritative evidence records
- policy citations and their association to findings
- risk features, rule values, and model outputs

The Agent Client may:

- normalize schema and representation
- attach capabilities and references that the source actually returned
- organize, order, and label information for presentation

The Agent Client must not:

- create additional findings
- silently merge distinct findings
- split one authoritative finding into multiple findings
- create evidence records from aggregate features
- invent policy associations, signal values, or record IDs
- reinterpret missing evidence as present evidence

*Generalized lesson*: a punctuation difference between two sections of the
authoritative payload was enough for exact-string matching to duplicate the
same finding — the fix belonged at the normalization boundary, never as a
UI filter. Fidelity problems are boundary problems.

---

## P2 — Concept Discipline (Findings ≠ Features ≠ Records)

These concepts are distinct and must never be conflated:

| Concept | What it is | What it is not |
|---|---|---|
| **Finding** | A unit of investigation focus defined by the source | Not a feature, not a record |
| **Risk Feature** | An aggregate model/rule input (e.g. a frequency, a ratio, a score) | Not a finding; carries no record identity |
| **Concrete Evidence Record** | An individual authoritative record with an identity (ID, timestamp) | Not derivable from an aggregate count |
| **Timeline Event** | A chronological projection of records/detection points | Not a record dump; a bounded or complete view of one |
| **Policy Reference** | A citation owned by the source, associated to specific findings | Not a finding attribute by default; not case-decorations |
| **Agent Response** | A transient, request-scoped presentation of results | Not a database; not an audit artifact |
| **Artifact** | A persisted, provenance-traceable investigation output | Not a transcript; not a replacement for the source |

In particular:

- `Finding ≠ Risk Feature`
- `Finding ≠ Evidence Record`
- `Risk Feature ≠ Evidence Record`
- `Policy Reference ≠ Finding attribute`
- **Aggregate count ≠ concrete record list**

Example: `withdrawal_frequency_24h = 7` is an aggregate feature fact. It
does NOT mean the Agent holds seven concrete withdrawal records. Concrete
records must come from an authoritative record source. An aggregate count
must never be used to reconstruct, pad, or justify a record list.

---

## P3 — Investigation Depth Principle

Investigation progresses through levels, and each surface should show the
depth appropriate to it:

```
Level 1 — Finding          (compact navigation: identity + essence)
Level 2 — Explanation      (why this finding exists: rules, signals, features)
Level 3 — Concrete Evidence (authoritative records, complete for scope)
Level 4 — Artifact          (persisted results + provenance)
```

- Do not dump Level 3 evidence into Level 1 surfaces.
- Do not hide Level 3 evidence when the user explicitly asks for it.
- An artifact's content should reflect the deepest level actually
  investigated, plus the authoritative context required to understand it.

Progressive disclosure is a scope mechanism, not a secrecy mechanism: the
deeper level is always available on request, never withheld once requested.

---

## P4 — Complete Evidence Principle

**If concrete evidence is requested, the result must be complete for the
requested scope.** Exactly three legal states exist:

1. **Concrete evidence not requested** → do not list record IDs.
2. **Requested and available** → present ALL authoritative records.
3. **Requested but unobtainable** → explicitly report the evidence gap.

Forbidden everywhere:

- top-N records presented as the complete evidence
- representative records presented without labeling
- mixing aggregate counts with partial record lists
- silently truncating concrete evidence

A bounded result is valid only when the user explicitly requested a subset,
or when it is clearly labeled as a preview/navigation result. The rule
applies globally — withdrawals, trades, transactions, timeline events, and
any other concrete record collection. If the complete set cannot be
obtained, the gap itself becomes the honest answer.

---

## P5 — Response Scope Principle

**Answer the user's question, not everything the system knows.**

Every response optimizes for: *requested scope + minimal supporting
context*. Each investigation action has a natural scope:

| Request | In scope | Out of scope |
|---|---|---|
| "Why is this finding flagged?" | explanation; relevant rule/feature evidence | unrelated policies; full timeline; unrelated features |
| "Show all withdrawals supporting this finding" | complete withdrawals | unrelated ML features; unrelated policies |
| "Show the timeline" | complete timeline for the scope | unrelated feature dumps |
| "Which policy requirements apply?" | finding-level basis: directly relevant references only (max 2); no finding-level basis: "no basis" statement + complete case-level set, labeled case-level | unrelated policies presented as finding support |
| "Generate the investigation bundle" | artifact creation + where to find it | re-running unrelated investigation steps |

Scope discipline is enforced at composition, not by hoping the model
"behaves": the composer includes only what the request implies.

---

## P6 — Response Composition Principle

A good Agent response is structured in this order:

1. **Human-readable answer** — first, always.
2. Supporting structured detail when useful.
3. Execution metadata when useful (Plan/Task/skill are secondary).
4. **Suggested next actions.**

The user must not need to understand internal Agent architecture (skill
ids, step names, tool names) to understand the answer. A response should
make clear: what happened, why it happened (when supported), what evidence
supports it, and what the user can do next.

---

## P7 — Human-Readability with Grounding Principle

The Agent is not successful merely because a tool executed successfully. A
tool result must be transformed into a user-understandable answer.

> Bad: `signal_explain completed`
> Good: "The finding was flagged because withdrawal frequency over 24 hours
> was 7, exceeding the configured threshold."

Grounding rules: every statement traces to ToolResult data; no unsupported
causality; no invented interpretation; data gaps become explicit statements
about the gap, not plausible-sounding filler.

---

## P8 — Citation Integrity Principle

**A citation marker is useful only if the user can resolve it.**

- A conversational response must not contain `[n]` markers unless the same
  response provides enough information to resolve them.
- If a response shows no policy mapping, inline markers are omitted.
- Artifacts may preserve authoritative markers **because** they carry a
  Policy References section.

Never: manufacture citation numbers; inherit citations merely because they
exist elsewhere in the case; attach every case-level citation to every
finding. Distinguish **finding-level citation association** (authoritative,
source-attached) from the **case-level policy reference collection**
(available context). The former may justify an inline marker; the latter
lives in its own section.

---

## P9 — Policy Relevance Principle

Policy lookup answers: *which policies apply to THIS investigation target?*
— never *which policies exist anywhere in the case?*

Priority order:

1. authoritative finding-level citations (source-attached to the target)
2. explicitly relevant case-level next-step policy references

Relevance must derive from authoritative association or explicitly
supported applicability — not loose keyword overlap. An unrelated policy
must not surface merely because its text shares words with the request.

---

## P10 — Provenance Principle

An Artifact is an auditable representation of investigated information:

```
Artifact → source ToolCallV2 → ToolResult → underlying evidence/citations
```

`source_tool_calls` must represent **actual content dependencies**:

```
Findings            → case fetch tool
Timeline            → timeline tool
Concrete Evidence   → evidence tool
Signal Explanation  → signal explanation tool
Policy References   → policy lookup tool
Artifact generation → never its own source
```

Do not include unrelated tool calls merely because they occurred in the
same investigation. Provenance that over- or under-states content
dependencies is a correctness defect, not a style issue.

---

## P11 — Artifact Fidelity Principle

**Artifact scope is a containment boundary, not merely a display label.**
Scope controls what the composition may read — not just the title or
metadata:

- A **case-scoped** artifact may contain information legitimately belonging
  to the investigated case, provided it was actually obtained/investigated
  under the existing artifact rules.
- A **finding-scoped** artifact may contain only information relevant to
  that finding and actually obtained/produced for that finding during the
  investigation.
- Nothing belonging exclusively to another finding or an unrelated
  investigation branch may leak into a narrower artifact — this applies
  globally to findings, evidence, transactions, withdrawals, timeline
  events, signal explanations, risk features, policy references,
  investigation notes, source tool calls, and any future section.
- Provenance (`source_tool_calls`) must reflect the same containment: a
  call whose results could not contribute to the artifact's scoped content
  is not a source.

Artifact scope must reflect investigation depth:

- If concrete evidence was investigated → the artifact must preserve the
  **complete** evidence set for the investigated scope.
- If it was not investigated → the artifact must not dump representative
  record IDs.
- Artifacts contain only what was actually investigated, plus the
  authoritative context required to understand it.
- Artifacts must never convert partial evidence into a complete-looking
  record set.

Artifact request scope must also be semantically explicit: an explicit
case-level request resolves to case scope and an explicit finding-level
request resolves to the focused finding's scope. Absent explicit language,
the current focus decides. Scope is never silently converted from what the
user asked for.

---

## P12 — Context & UI Guidance Principle

The Agent must help users understand how to continue. Guidance appears
where the user is interacting — the conversation:

> "9 findings were identified. Select a finding from the Findings panel on
> the left to continue."
> "The investigation bundle is available in the Artifacts panel on the
> right."

Do not rely on users discovering instructions in secondary panels. Context
panels primarily represent **current state**; the conversation is the
primary guidance surface.

**One investigation = one case.** An investigation session represents
exactly one case, and a later conversational message can never silently
re-target it. A request that names a different case is not executed — the
Agent states which case is under investigation and guides the user to start
a new investigation for the other case. Requests naming the current case
remain valid. Navigation between cases happens through the explicit
"new investigation" action, never through an implicit conversational switch.

---

## P13 — Suggested Follow-up Principle

Suggested Follow-ups are recommended next actions that are:

- constrained to executable investigation paths (no dead buttons),
- attached to the Agent response that created the context,

rendered at the bottom of each eligible Agent message. They must not become
a global duplicate control and must never replace free-form questions.
Selecting a target, choosing a suggested action, and asking one's own
question are complementary paths — the system should make all three feel
natural without forcing any of them.

---

## P14 — Bounded Honesty Principle

When the system does not know something, it says so — and says *which kind*
of not-knowing it is. These states are distinct and must remain distinct:

| State | Meaning |
|---|---|
| no data | the source responded; nothing exists for the request |
| unsupported capability | the target does not support this action |
| integration failure | the source could not be reached or failed |
| evidence gap | data exists but is insufficient for completeness |
| unresolved reference | the request's target could not be determined |
| planning failure | no valid plan could be produced |

Never collapse these into "no evidence". Never turn "could not retrieve"
into "there is nothing". Never fabricate a fallback answer merely to make
the UI look successful.

**User-visible failure communication must preserve the same semantic
distinction.** Distinct outcomes (empty, unsupported, validation error,
integration failure, unresolved reference, planning failure) must not be
rendered as one generic failure message, and the wording must communicate
the *meaning* of the failure — never the internal mechanism (step IDs, tool
names, skill names, exception text).

**Finding-level policy basis is data, not capability (P18 correction).**
Whether a finding carries an authoritative finding-level policy basis
(`policy_refs`) is a *data* property of the authoritative payload — its
absence is the valid, completed answer "no finding-level policy basis is
attached to this finding" (with any case-level references explicitly
labeled case-level). It must never be rendered as an unsupported
capability, an execution failure, or "no policy exists". Policy
*retrieval* itself is case-wide: RP exposes its single validated-citation
surface to every finding, so no finding type is policy-unsupported.

---

## P15 — LLM Responsibility Principle

**The LLM may reason over facts; it does not own the facts.**

The LLM provides: intent interpretation; planning within a constrained
vocabulary; natural-language organization/explanation where appropriate.

The LLM never provides: findings, evidence, policy associations, record
counts, IDs, or authoritative scores.

---

## P16 — Deterministic/Probabilistic Boundary Principle

Keep deterministic (code-owned):

- source-of-truth mapping and finding identity
- evidence retrieval and completeness
- citation identity and association
- provenance
- capability checks and contract validation
- artifact inclusion rules
- response scope enforcement
- failure semantics

Use the LLM for: intent interpretation, constrained planning, and
explanation. This allocation is what reduces hallucination while keeping
the system genuinely agentic.

---

## P17 — "Agentic, Not Autonomous Fabrication" Principle

The system is agentic because it can: understand requests, resolve context,
choose an investigation capability, plan tool usage, execute tools,
synthesize results, suggest next actions, and maintain investigation state.

It is NOT agentic because it invents facts.

**Agent freedom exists inside a bounded factual and operational space.**

---

## P18 — Failure Discovery Principle

Real user testing is part of specification discovery. Architecture
documents are never complete. The working loop is:

```
design → implement → run against real systems
→ observe unexpected behavior
→ classify: source-of-truth violation | contract gap | response-scope
   problem | UX issue | integration issue
→ if the lesson generalizes, update the principle/contract
→ fix the implementation at the boundary that owns the defect
```

Do not convert every one-off observation into a special-case rule; only
generalizable lessons belong in this document. Equally: do not fix
boundary defects with presentation-layer workarounds (see Anti-pattern K).

---

## P19 — Capability Transparency Principle

Users must be able to understand the Agent's currently supported
investigation surface — which capabilities exist, what they do, and for
which investigation targets they are available. The Agent must not imply
unsupported capabilities: it never offers, suggests, or hints at an
investigation action the runtime cannot execute, and where a capability is
absent it says so rather than staying silent.

This is a transparency obligation, not a promise of completeness: the
supported surface is defined by the runtime (capabilities, skills, tools)
and legitimately evolves release to release. The principle governs honesty
about that surface, not its size; its concrete documentation per release
belongs to product/status documentation, not to this document.

**Capability transparency applies internally as well as at the UI.** A
capability that is not executable in the current runtime must not enter the
planner's candidate set merely because it is represented in metadata,
documentation, or a domain capability field. The planner must not imply
support for an unavailable execution path — otherwise the system silently
selects an investigation it cannot perform.

---

## P20 — Empty Is a Valid Result Principle

A valid investigation may legitimately produce no findings, no matching
records, or no applicable policy. **An empty result is not itself an
execution failure.**

The system must distinguish:

- nothing was found (the source responded; the answer is "none")
- no concrete records exist for this stream (aggregates may still exist)
- the requested capability is unsupported for this target
- the external system failed
- the request was invalid

A valid empty result must never be rendered as an integration failure or
system unavailability, and must never be padded into fabricated success or
fabricated evidence. "The Risk Platform returned no risk findings for this
case" is a complete, valuable investigation conclusion — treat it as one.

---

## Anti-Patterns

Each of these was observed during real validation. They are named so they
are easy to reject in review:

| # | Anti-pattern | Why it fails |
|---|---|---|
| A | Client-generated findings | violates P1; inflates the authoritative set |
| B | Partial evidence presented as complete | violates P4; misleads investigators |
| C | Aggregate counts treated as record lists | violates P2; fabricates record identity |
| D | Orphan citation markers | violates P8; unresolvable `[n]` erodes trust |
| E | Global follow-up duplication | violates P13; chips lose their context |
| F | Raw tool dumps as Agent answers | violates P6/P7; transfers interpretation work to the user |
| G | Unrelated policy dumping | violates P9; noise displaces relevant guidance |
| H | Hidden UI guidance | violates P12; users stall after success |
| I | Integration failure treated as empty data | violates P14; "nothing" vs "unreachable" are different worlds |
| J | LLM-generated facts | violates P15/P16; hallucination becomes truth |
| K | UI deduplication hiding source-boundary errors | masks P1 violations; duplicates persist in the domain |
| L | Provenance not matching artifact content | violates P10; audit trail becomes fiction |
| M | Empty result masquerading as system failure | violates P20; a legitimate "none found" reads as an outage |

---

## Decision Heuristic — "Should this information be shown?"

Ask, in order:

1. Did the user request it?
2. Is it needed to understand the answer?
3. Is it authoritative?
4. Is it complete for the requested scope?
5. Can the user resolve its source (citation, ID, panel location)?
6. Is it part of the current investigation depth?

If any answer is no → omit it, or explicitly mark the limitation.

---

## Design Review Checklist

Before shipping any new Agent capability:

**Source**
- [ ] What is the authoritative source for each fact shown?
- [ ] Are facts copied/normalized, or fabricated?

**Scope**
- [ ] What exact user request does this answer?
- [ ] Is everything included necessary for that request?

**Evidence**
- [ ] Is evidence complete for the requested scope?
- [ ] Is aggregate data separated from concrete records?

**LLM**
- [ ] Is the LLM reasoning/organizing only — never deciding facts?

**Response**
- [ ] Is there a human-readable answer first?
- [ ] Is irrelevant data omitted?

**Citation**
- [ ] Is every `[n]` resolvable in the same surface?

**Provenance**
- [ ] Can the artifact be traced to actual contributing tool calls?

**UX**
- [ ] Does the user know what to do next, from the conversation?

**Failure**
- [ ] Are unsupported / empty / unavailable / ambiguous states distinct?

---

## Versioning & Relationship to Other Documents

This is a **high-level principles document**. It does not replace:

- `DOMAIN_MODELS_V1.md` — object contracts
- `SKILL_MODEL_V1.md` — capability/skill contracts
- `FOLLOW_UP_MODEL_V1.md` — follow-up contracts
- `AGENT_CLIENT_ARCHITECTURE_V2.md` — runtime architecture
- `API_CONTRACT_V1.md` — HTTP boundary contracts

Relationship:

```
Principles (this document)
    → Architecture (AGENT_CLIENT_ARCHITECTURE_V2)
        → Domain contracts (DOMAIN_MODELS / SKILL_MODEL / FOLLOW_UP_MODEL)
            → Component implementation
```

When a lower-level rule conflicts with these principles, document the
reason for the exception explicitly rather than silently violating the
principle. Principles change only through the failure-discovery loop
(P18) — when real-system validation shows a generalizable lesson.
