# Baseline: Cross-Account Trade Analysis V1

**Status**: ACTIVE BASELINE — product/architecture contract for the
cross-account trade analysis feature. §12a records the implemented OQ-4
resolution (data contract / retrieval capability). §12b is the frozen
matching-phase contract. §12c records the **implemented** matching phase.
§12d records the **completed** self-opposite view.
**Date**: 2026-09-18 (**updated** 2026-09-18: OQ-4 resolved — bounded
account-scoped transaction retrieval, see §12a; **updated** 2026-09-18:
matching-phase contract frozen, see §12b; **updated** 2026-09-18:
matching phase IMPLEMENTED, see §12c; **updated** 2026-09-19:
`self_opposite_trades` view COMPLETE, see §12d)
**Parent docs**: `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md`,
`DOMAIN_MODELS_V1.md`, `SKILL_MODEL_V1.md`,
`AGENT_RESPONSE_EVIDENCE_PRINCIPLES_V1.md`, `AGENT_EVALUATION_V1.md`
**Codebase state inspected**: `main` @ `746d4a9` (MCP capability integration)

> This is a lightweight product + architecture baseline, not a design essay.
> It defines WHAT the capability is, the boundaries it must respect, the
> observation contract, and what is already supported vs. what must be built.
> Items the repository cannot answer are marked **Open Question (OQ-n)** and
> are deliberately not invented.

---

## 1. Objective

Enable the Investigation Agent to identify **CANDIDATE cross-account
opposite-trade relationships** from authoritative Risk Platform transaction
evidence, inside a case investigation.

Hard framing rules:

- Results are **candidates for investigation** — never confirmed coordinated
  trading, never a claim of intent, never a risk verdict.
- All inputs are authoritative Risk Platform evidence retrieved inside the
  tool; the Agent composes and presents, it does not compute the matching.
- The new capability is **analytically distinct** from the existing
  opposite-trade signal (§8): the existing signal is same-account
  opposite-side trading; this feature is **cross-account** opposite-side
  trading.

---

## 2. Problem / User Goal

An analyst investigating a case (typically one already flagged for
coordinated-trading-like behavior) asks the Agent to find candidate cases
where:

| # | Criterion | Value |
|---|---|---|
| 1 | Accounts are **different** | `account_a != account_b` |
| 2 | Instrument is the **same** | same `symbol` |
| 3 | Trade directions are **opposite** | `buy` vs `sell` |
| 4 | Timestamps within a configured tolerance | `\|t_a − t_b\| ≤ max_time_delta` |
| 5 | Transaction value/notional approximately matched | within configured tolerance |
| 6 | Matching may involve **bounded aggregation** of multiple trades on one side | bounded grouping, not free combinatorics |

Example user requests the capability must serve:
"Check whether there are cross-account opposite trades behind this
finding", "Find matching buy/sell pairs across accounts within a short
time window".

---

## 3. Scope

The capability is an **Agent-owned derived analytical tool**:

```
cross_account_trade_analysis
```

- It lives with the other domain capabilities in
  `backend/app/domain_tools/` and follows the same boundary rules as
  `finding_drilldown` / `signal_explain` / `policy_lookup`: validate
  inputs → retrieve authoritative evidence via the RiskPlatformAdapter →
  normalize to a `ToolResult` — never fabricate, never convert upstream
  failure into empty data.
- It is **derived analysis over authoritative evidence** (like the
  timeline is a derived view): the matching computation is the tool's own
  deterministic logic; every *record* it cites remains RP-owned.
- The tool **retrieves authoritative transaction evidence from the Risk
  Platform internally** (adapter call inside tool execution).
- **The Agent must NOT pass the entire raw transaction dataset to the
  tool.** The tool's input contract accepts investigation-scope
  identifiers only (§4); there is no parameter through which the planner,
  the LLM, or a prior tool payload can submit a transaction list.
- Access path: **Function Calling only in V1** (the default path). The
  tool is NOT exposed through MCP in V1 (MCP currently exposes exactly
  `risk_case_fetch`; adding tools to the MCP server is a documented future
  extension, not part of this baseline).
- The **Planner names the capability regardless of access path** — no MCP
  vocabulary enters the planner (standing CLAUDE.md / MCP_ARCHITECTURE_V1
  rule; trivially satisfied here because V1 adds no second access path).

Out of scope (V1): remote MCP transport, multi-instrument portfolio
matching, sub-account/hierarchy resolution, cross-case analysis (the
one-investigation-one-case boundary in `context_resolution.py` stands),
live alerting, any write-back to the Risk Platform.

---

## 4. Data Retrieval Boundaries

### 4.1 Input contract

Input **primarily identifies investigation scope** — it never carries
records:

| Argument | Source | Notes |
|---|---|---|
| `case_id` | executor runtime injection (precedent: `_runtime_arguments` in `executor_v2.py`) | never planner/LLM-supplied |
| `finding_id` | executor runtime injection when a finding is focused | optional; anchors instruments/account scope when present |
| `analysis_type` | closed registry value bound by the step's parameter lock (e.g. the V1 cross-account opposite-trade match) | planner may only emit the registered value; exact vocabulary value decided at implementation |

All analytical knobs (time tolerance, amount tolerance, aggregation
bounds, retrieval limits) are **system-controlled configuration** — never
LLM-chosen arguments. This mirrors the established rule that top_n exists
only as an explicit-user-subset mechanism and locked parameters are
registry-owned.

### 4.2 Retrieval rules

- **Raw transactions stay inside tool execution.** The tool fetches
  evidence through the existing adapter boundary
  (`RiskPlatformAdapter`, `GET /api/risk/cases/{user_id}/evidence`,
  `expose_complete_records=true` for complete records — the same surface
  `finding_drilldown(view="evidence")` uses today). No new RP endpoint is
  invented in V1.
- **Retrieval is bounded by system-controlled limits** (max records per
  stream, max accounts, max window). When a limit truncates retrieval,
  the result payload and `warnings` say so explicitly — a bounded result
  is never presented as the complete population (same honesty rule as the
  timeline `truncated` flag).
- **Pagination/filtering is used where supported.** The RP evidence API
  as consumed by this repository exposes a boolean
  `expose_complete_records` opt-in and no pagination parameters.
  **OQ-1**: does the Risk Platform support any pagination / server-side
  filter for transaction evidence? If not, client-side bounding applies
  and must be labeled.
- **The LLM receives compact observations, not raw transaction dumps.**
  The observation payload (§6) carries candidate summaries, counts,
  bounded ID lists, and data-quality flags — consistent with the
  investigation-depth principle (P3) and the rule that concrete-record
  dumps appear only in evidence-mode surfaces.
- **Candidate results include source transaction IDs / evidence refs.**
  Every candidate carries `EvidenceRef(kind="transaction", id=<RP
  transaction_id>)`-style references for each supporting leg, so the
  result is traceable to authoritative records (provenance invariant).
- **Configurable limits prevent unbounded queries** (limit values are
  **OQ-2**; the enforcement points are named in §5/§11).

### 4.3 Known data-shape facts (from the repository, not invented)

Confirmed transaction evidence fields consumed by the adapter today
(`adapters/risk_platform.py`, test fixtures): `transaction_id`, `symbol`,
`side` (`buy`/`sell`), `price`, `quantity`, `value`, `timestamp`,
`risk_reason`.

- **Instrument** → `symbol` ✅
- **Direction** → `side` ✅ (RP stores `BUY`/`SELL`; matching must
  compare case-insensitively — projection is verbatim)
- **Timestamp** → `timestamp` (ISO-8601 string, optional in RP) ✅
- **Value/notional** → `value` ✅ **RESOLVED BY EXISTING SYSTEM**: RP has
  no stored value column; `value` is deterministically computed as
  `price × quantity` (notional in the instrument's quote currency) at
  three independent sites — the evidence projection
  (`evidence_service._get_transaction_evidence`), the feature aggregates
  (`avg_trade_size`, `trade_volume_24h` in `feature_engineering.py`), and
  the §12a retrieval endpoint. RP has no currency normalization, but
  matching always compares within one `symbol`, so the quote convention
  is consistent per candidate — `value` is safe as the **primary
  matching quantity** (§12b.1); `price`/`quantity` are retained as
  supporting evidence.
- **Account attribution on a transaction** → **not present in the legacy
  `/evidence` projection**, but present in the RP Trade store
  (`Trade.user_id`); a bounded, account-scoped retrieval surface exposing
  it is now **implemented** — see §12a. The account universe itself is
  authoritative case context: the case account plus cluster-connected
  accounts (`network_evidence.related_accounts`,
  `GET /api/risk/cases/{user_id}/network-signals`).

**OQ-4 (gating)** — RESOLVED (2026-09-18): counterparty-account
transactions are obtained through the new bounded account-scoped
retrieval surface (§12a). The unresolved remainder (tolerance values,
aggregation strategy, candidate contract) is unchanged.

---

## 5. Deterministic Analysis (V1 matching logic — frozen, see §12b)

All matching is **computed deterministically inside the tool**. The LLM
decides WHEN to use the capability; it never performs financial matching
calculations itself (the architecture's standing rule: the Agent may
compose/order/present — never compute risk-domain facts). Parameters are
frozen in §12b.3; this section states the rules.

V1 rules, in evaluation order over the retrieved, bounded transaction
set:

1. **Distinct accounts**: `account_a != account_b` (authoritative account
   attribution per §12a; a pair failing this is *same-account* and belongs
   to the `self_opposite_trades` semantics of §8 — it is never emitted as
   a cross-account candidate).
2. **Same instrument**: `symbol` equality (exact string equality on the
   RP-stored symbol; any normalization is recorded in the payload's
   config echo).
3. **Opposite direction**: `side` values are opposite, compared
   case-insensitively (RP stores `BUY`/`SELL`); projection stays verbatim.
4. **Time tolerance**: `|t_a − t_b| ≤ TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS`
   (V1 default 30 s — §12b.3) on parsed UTC instants. Comparisons are
   **inclusive** (a match exactly at the boundary counts). Records with
   absent/unparseable timestamps are excluded from matching and surfaced
   as data-quality information (never silently dropped, never given a
   synthetic timestamp — same rule as timeline normalization).
5. **Value matching (primary dimension)**: `value` per §4.3. A pair
   matches when
   `|v_a − v_b| ≤ max(TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR,
   TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE × max(v_a, v_b))`
   (defaults §12b.3; inclusive). Price and quantity are **not matched**
   in V1 — they travel as supporting evidence on the candidate record.
6. **Bounded aggregation**: `1_to_1`, `1_to_N`, `N_to_1` only
   (definitions §12b.4). One side of every candidate is always a single
   **anchor** trade; the opposite side accumulates legs greedily in
   timestamp order within the anchor's time window, up to
   `TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE`; no backtracking.
7. **No unrestricted combinatorics**: no arbitrary subset-sum, no
   unrestricted N×M enumeration. Rationale and the accepted conservative
   trade-offs: §12b.4.
8. **No transaction reuse**: every transaction id appears in at most one
   emitted candidate (anchor-first, timestamp-order consumption) —
   duplicate/overlapping candidates are structurally excluded (§12b.5).

Determinism requirements: sorted inputs with stable tie-breaking;
identical input + identical config ⇒ identical candidates, IDs, ranking,
and ordering. Side naming (`account_a`/`account_b`), ranking sort, and
tie-breakers are specified in §12b.5; boundary comparisons are inclusive
(rule 4/5); the exact arithmetic (float vs decimal) is an implementation
decision but must be pinned and covered by a repeatability test.

---

## 6. Observation Contract

The tool returns a structured observation payload whose top-level
`analysis_status` is exactly one of:

| Status | Meaning | ToolResult mapping |
|---|---|---|
| `MATCH_FOUND` | ≥1 candidate cross-account opposite-trade relationship satisfies all §5 criteria within the retrieved data | `outcome="success"` |
| `NO_MATCH` | Retrieval succeeded with analyzable data, but zero candidates satisfy the criteria | `outcome="success"` (analytical zero; distinct from retrieval emptiness) |
| `INSUFFICIENT_DATA` | The authoritative evidence needed to run the analysis is absent or too incomplete to analyze: no transaction records in scope, unusable required fields, or retrieval truncated below the analyzable floor (see the retrieval-status mapping below) | `outcome="success"` with `evidence_missing: true` + `next_data_needed` (established success-with-gap pattern) |
| `ERROR` | The tool could not execute its analysis: RP transport/auth/HTTP/malformed (integration) or invalid local input (validation) | `outcome="integration_error"` / `outcome="validation_error"` respectively — never disguised as absence of data |

Retrieval-to-analysis status mapping (**decided**, §12b.6): the tool
consumes the §12a retrieval contract, whose own statuses are
`complete / no_data / truncated / error`:

| Retrieval status | Candidates | Tool `analysis_status` |
|---|---|---|
| `complete` | ≥1 | `MATCH_FOUND` |
| `complete` | 0 | `NO_MATCH` |
| `no_data` (complete and empty) | — | `INSUFFICIENT_DATA` |
| `truncated` | ≥1 | `MATCH_FOUND` (+ truncation flags; the negative space is never claimed) |
| `truncated` | 0 | `INSUFFICIENT_DATA` — **never** `NO_MATCH` |
| `error` | — | `ERROR` |

ToolResult mapping (**resolves OQ-8**): `MATCH_FOUND`/`NO_MATCH` ride
`outcome="success"`; `INSUFFICIENT_DATA` rides `outcome="success"` with
`evidence_missing: true` + `next_data_needed`; `ERROR` maps to
`integration_error`/`validation_error` per cause. The tool **never
emits `EMPTY`** — its "no data" case is `INSUFFICIENT_DATA`. `NO_MATCH`
is reserved for "sufficiently complete evidence was analyzed and nothing
matched".

A `MATCH_FOUND` payload contains one entry per candidate relationship
(**frozen field list**):

- `candidate_id` (deterministic), `match_type` (`1_to_1` | `1_to_N` |
  `N_to_1`), `rank` (position in the deterministic ranking, §12b.5)
- `account_a`, `account_b` (naming rule §12b.5), `symbol`
- `side_a`, `side_b` (verbatim `BUY`/`SELL`)
- `transaction_ids_a`, `transaction_ids_b` + `evidence_refs` for every
  supporting leg
- time information: per-leg timestamps and the candidate's max time gap
- value/notional information: per-side matched totals (`value_a`,
  `value_b`), absolute difference, relative difference
- supporting evidence where useful: per-leg `price`/`quantity` (not
  matched in V1 — interpretation aid only)
- the matching criteria/config actually applied (tolerances echoed)
- data quality information where applicable: excluded-untimestamped
  counts, truncated-retrieval flags, bound-capped aggregation flags

Plus payload-level facts: `analysis_status`, totals
(transactions retrieved per account, candidates found vs emitted — the
cap may bind), retrieval completeness echo, config echo, warnings.
Raw transaction dumps MUST NOT be the normal Agent/LLM observation —
the observation carries candidate records and counts, never the
unprocessed retrieval rows. No narrative — text composition stays with
the deterministic response composer, which must render the four statuses
as **distinct user-visible states** (extending the existing composer's
per-payload branches).

---

## 7. Agent Loop Semantics

Grounded in the current runtime: `InvestigationService.run_turn` is
turn-local (resolve → plan → execute → compose); `ExecutorV2` runs one
plan's steps in order, treats `EMPTY` as a valid step answer (P20),
performs **no autonomous retries**, and has no multi-iteration loop (the
`max_steps=8` loop exists only in the superseded `agent.py`, which is
not modified). The contract below is therefore partly normative-for-
implementation, not a description of today's loop:

- **`MATCH_FOUND` does not automatically mean STOP.** A match is
  *step-level evidence sufficiency* — it satisfies this step's question,
  not the investigation's goal. Step-level sufficiency and task-level
  completion are different layers and must never be collapsed.
- **The Agent continues / re-plans / stops based on the overall
  investigation goal**, expressed through the normal turn pipeline
  (follow-ups, next user request, planner re-planning) — not through a
  tool result silently terminating work. In V1 no autonomous loop is
  added; if/when one is, its stop conditions must be goal-level, with
  `MATCH_FOUND` as input — never a trigger.
- **`NO_MATCH` does not mean "nothing suspicious."** It means no candidate
  satisfied the V1 criteria within the bounded retrieved data. It must
  never be composed as exoneration; the composer wording stays scoped
  ("no matching pairs within the analyzed window/tolerances").
- **`INSUFFICIENT_DATA` is different from `NO_MATCH`** — and from
  `EMPTY`: missing/unattributable data must never be composed as a
  completed negative finding (existing bounded-honesty rule).
- **Persistent tool errors may trigger retry and/or re-planning.** Today
  a failed required step fails the task and retry is UI/task-driven; no
  automatic retry exists. Any retry/re-plan behavior added for this
  capability must be bounded (attempt cap, backoff policy — **OQ-9**)
  and visible in the task log; silent repetition is prohibited.
- **Maximum iteration/step limits must prevent infinite loops.** Any
  loop added for this capability carries a hard bound. Reaching the
  bound is a **bounded-stop outcome**: reported explicitly (distinct
  from normal success — precedent: the legacy loop's
  `max_steps_exceeded` status), with partial results and their status
  preserved, never presented as task completion.

---

## 8. Existing Opposite-Trade View — Canonical Naming

The repository today contains an **opposite-trade surface family** named
`opposite_trades`: the `finding_drilldown` view
(`view="opposite_trades"`, registered but non-executable in Week 1 —
`OPPOSITE_TRADES_NOT_SUPPORTED`), the `inspect_opposite_trades` step
type, the `opposite_trades` finding capability, the
`trade_investigation` skill, and the planner's opposite-trade request
routing (`_OPPOSITE_TRADE_RE`).

Its actual data basis is the case account's **own** trade stream:
`opposite_trade_ratio` is a single aggregate over the focused account's
trades (RP threshold semantics, 0.4). Opposite-side legs of that signal
are trades of the **same account**. The canonical meaning is therefore:

> **`self_opposite_trades`** — opposite-side trading by the SAME account.

The new capability is categorically different:

| | `self_opposite_trades` (existing, to be renamed) | `cross_account_trade_analysis` (this baseline) |
|---|---|---|
| Accounts involved | one (the case account) | two or more, `account_a != account_b` |
| Data basis | `feature_evidence.opposite_trade_ratio` + own trade stream | account-attributed transactions of ≥2 accounts (OQ-4) |
| Nature | aggregate signal view (existing detection surface) | derived pair/group analysis over concrete records |
| Result type | signal composition of a finding | candidate relationships with per-leg evidence refs |
| Claim strength | describes the RP-flagged signal | **candidates only** — never confirmed coordination |

Rules:

- This distinction is kept **explicit throughout the architecture**:
  capability vocabulary, view names, step types, skill descriptions,
  planner guidance, composer phrases, UI labels, docs. The two names are
  never interchangeable and never abbreviated to shared wording
  ("opposite trades" alone is ambiguous and must not label either
  surface).
- The existing identifiers (`opposite_trades` capability/view/step) are
  renamed or canonically relabeled to `self_opposite_trades` as part of
  implementation. Full touch-point list is in §13 "must be implemented";
  the rename is behavior-preserving (the view remained
  registered-but-unimplemented through the matching phase — implemented
  as of §12d) and
  must not silently change planner routing for existing opposite-trade
  requests (a regression-gated change).
- Planner routing must distinguish the two intents deterministically:
  same-account wording ("show the opposite trades of this finding") →
  the self step; cross-account wording ("cross-account opposite
  trades", "between accounts", 跨账户对倒/对向交易 across accounts) → the
  new step. Implementation note: the existing `_OPPOSITE_TRADE_RE` also
  matches inside cross-account phrasing, so cross-account matching must
  be evaluated first — an ordering rule, verified by tests.

---

## 9. Artifact Output

When an investigation turn uses this capability, `generate_artifact`
composes from the executed tool result (provenance closure via
`source_tool_calls` — existing machinery, unchanged). The analysis
section of an artifact must be **evidence-based** and contain:

- **Candidate relationship(s)**: accounts, instrument, directions, and
  the candidate's deterministic id.
- **Supporting transactions**: per-side transaction IDs (and timestamps /
  values as recorded), one row per leg.
- **Matching criteria/metrics**: tolerances actually applied, time gap,
  value difference / ratio per candidate.
- **Evidence references**: `EvidenceRef`s for every supporting leg, plus
  the tool-call provenance (automatic via `ArtifactV2`).
- **Evidence gaps / limitations**: `INSUFFICIENT_DATA` facts, truncation
  flags, excluded-records counts, bound-capped aggregation — rendered in
  the artifact's gaps/limitations section (the existing Evidence-Gaps
  rendering pattern).

Language rule: artifacts and responses describe **candidate
relationships that match quantitatively**. They must not claim
coordinated intent, coordination, or wrongdoing merely from matching
trades; wording stays at "matching opposite-side trades across accounts
within the configured tolerances". This applies to composer prose,
artifact content, and UI labels alike.

Artifact type: extension of `ArtifactTypeV2` (the enum is open by
design) or a section within existing bundle types — decided at
implementation (**OQ-10**), with no change to the provenance model.

---

## 10. Evaluation

Added to the existing three-layer evaluation (AGENT_EVALUATION_V1)
without merging layers. Matching-phase matrix (unit layer,
`backend/tests/`) — each row is a named test. Where exact numeric
fixtures are not yet justified, the required **behavior** is specified;
fixtures may use any values satisfying it:

| # | Case | Required behavior |
|---|---|---|
| 1 | Clear 1:1 match | `MATCH_FOUND`; one `1_to_1` candidate with correct accounts/sides/ids/refs/metrics |
| 2 | Clear 1:N match | one anchor vs ≥2 legs within window/leg-cap → `1_to_N` candidate listing all legs |
| 3 | Clear N:1 match | mirror of case 2 → `N_to_1` candidate |
| 4 | Same account only | opposite-side trades on one account → zero cross-account candidates (§8 boundary) |
| 5 | Different accounts, same side | both BUY → no candidate |
| 6 | Different instruments | symbols differ → no candidate |
| 7 | Time outside tolerance | values match, Δt > tolerance → no candidate |
| 8 | Value outside tolerance | time/side/instrument match, \|Δv\| > tolerance → no candidate |
| 9 | Exactly at boundary | Δt == tolerance and \|Δv\| == tolerance → candidate (comparisons inclusive) |
| 10 | No match, complete evidence | retrieval `complete`, criteria satisfied by nothing → `NO_MATCH` (success), zero candidates |
| 11 | Truncated/incomplete evidence | retrieval `truncated` + zero candidates → `INSUFFICIENT_DATA`, never `NO_MATCH`; + candidates → `MATCH_FOUND` with truncation flags |
| 12 | No data | retrieval `no_data` → `INSUFFICIENT_DATA` + `next_data_needed` |
| 13 | Insufficient required fields | missing/unparseable timestamps or identity fields → excluded from matching, surfaced in data quality (malformed identity → `ERROR` per §12a) |
| 14 | Multiple candidates, deterministic ranking | same input twice ⇒ identical candidate set, ranks, and order; ranking follows §12b.5 dimensions |
| 15 | Candidate result limit | candidates found > `TRADE_ANALYSIS_MAX_CANDIDATES` → top-K emitted after ranking; found-vs-emitted counts visible |
| 16 | Duplicate/overlapping candidates | a transaction id appears in at most one emitted candidate (§5 rule 8) — shared-leg second matches are suppressed, not duplicated |
| 17 | Deterministic repeatability | identical input + config ⇒ byte-identical observation payload |

Tool-boundary cases (error/empty/scope): retrieval `error` → tool
`ERROR` (integration/validation wording preserved); out-of-scope
accounts rejected upstream (§12a contract tests already cover).

**Runtime semantic layer (Layer A, `eval/scenarios.py`)** — scripted
plans over stubbed RP payloads: match found → composer renders the
candidate summary with IDs, candidate terminology, and no intent
language; `MATCH_FOUND`/`NO_MATCH`/`INSUFFICIENT_DATA` produce distinct
bounded wordings; error wording preserved; artifact contains the
analysis section + gaps; existing scenarios (E01–E15) unchanged and
green.

**Planner layer (Layer B)** — natural-language routing: cross-account
phrasing plans the new step; existing same-account opposite-trade
phrasing still plans the `self_opposite_trades` step — no regression;
wrong-step/generic-fallback taxonomy applies unchanged. Repeated-run
stability per the existing 3-run protocol.

**Live layer (Layer C)** — unblocked by §12a; a live scenario is added
when a seeded multi-account case exists in the demo data (fixture
dependency, not a contract dependency).

Loop-behavior cases (max-iteration bounded-stop, bounded retry) are
testable only if/when the corresponding loop exists (§7, OQ-9); until
then they are documented expectations, not automatable scenarios — and
are explicitly OUT of this phase's DoD.

---

## 11. Definition of Done (matching-analysis phase)

Concrete and testable for the `cross_account_trade_analysis` tool.
**Explicitly excluded from this DoD**: Agent Loop completion (autonomous
continue/re-plan/stop, bounded-stop outcomes) — that is the next phase
(§12b.9); the tool only has to PROVIDE the structured observation that
future phase consumes.

1. **Capability exists**: `cross_account_trade_analysis` implemented in
   `backend/app/domain_tools/`, registered in `default_tool_provider()`;
   its step type exists in `STEP_TOOL_MAP` with a parameter-locked
   `analysis_type`; `step_path_executable` returns true only for the
   implemented path (P19 rule holds); capability gating per OQ-11
   decision recorded at registry time.
2. **Scope-only input**: the tool signature accepts investigation-scope
   identifiers only; no parameter accepts raw transaction/record lists;
   account ids reach the §12a retrieval layer, which enforces scope.
3. **Deterministic behavior**: matching implemented exactly per §5/
   §12b; no LLM call anywhere in the tool's execution path (structure +
   test assertion); identical input + config ⇒ byte-identical output
   (eval case 17).
4. **Bounded runtime/data consumption**: all retrieval flows through the
   §12a budgeted contract; matching cost bounded by the §12b.4
   aggregation boundary (no N×M/subset-sum code paths exist); result
   count capped by `TRADE_ANALYSIS_MAX_CANDIDATES`; caps/binding visible
   in the payload.
5. **Complete-vs-partial semantics**: the §6 status mapping table
   implemented verbatim — truncated+zero-candidates is
   `INSUFFICIENT_DATA`, never `NO_MATCH`; `no_data` is
   `INSUFFICIENT_DATA`; the tool never emits `EMPTY`; truncation flags
   ride every `MATCH_FOUND` on partial evidence (tests per §10 cases
   10–12).
6. **Provenance preservation**: every candidate carries per-leg
   transaction ids + `evidence_refs` resolvable to authoritative RP
   records; artifact provenance closure holds when the result feeds
   `generate_artifact` (existing provenance tests extended).
7. **Explainable candidate evidence**: each candidate renders its own
   accounts, sides, legs, time gap, values, difference metrics,
   `match_type`, and applied tolerances — an analyst can re-derive the
   match from the record alone (asserted in tests).
8. **No raw dump to LLM observation**: the composer-visible observation
   contains candidate records and counts only; a test asserts no raw
   retrieval row and no non-candidate transaction id appears in the
   observation payload.
9. **Aggregation shapes**: `1_to_1` / `1_to_N` / `N_to_1` all produce
   correct candidates (cases 1–3); leg-cap overflow is labeled, never
   silently truncated; no transaction id appears in two candidates
   (case 16).
10. **Negative + boundary cases**: cases 4–9 green (same-account,
    same-side, different-instrument, time/value outside tolerance,
    inclusive boundary).
11. **Deterministic ranking**: ranking dimensions per §12b.5 implemented
    with stable tie-breakers; not exposed or worded as a risk score
    (forbidden-fragment checker).
12. **Naming distinction**: `self_opposite_trades` canonical
    rename/relabel applied across capability id / view / step / skill /
    planner guidance / composer phrases / docs, regression-gated
    (existing routing keeps working; Layer B scenario).
13. **Evaluation green**: all §10 unit cases pass at 100%; Layer A
    scenarios green; Layer B planner scenario stable; existing suites
    unchanged (no FC-path behavior change).
14. **Boundaries intact**: no MCP exposure in V1; no silent fallback
    anywhere in the new path; planner carries no transport vocabulary
    and no threshold values; domain logic stays out of adapters.

---

## 12. Risks / Open Questions

**OQ-4 is the gating item** — everything else can be built against the
contract with stubbed data, but the feature is not shippable until
counterparty transaction retrieval is answered.

Status labels: **RESOLVED BY EXISTING SYSTEM** (verified in code) ·
**RESOLVED** (implemented in a prior phase) · **DECIDED** (contract
decision of this baseline; provisional where marked) · **IMPLEMENTATION
DECISION** (owned by the implementation step, within stated bounds) ·
**OPEN** (genuinely unresolved).

| # | Question | Status | Notes |
|---|---|---|---|
| OQ-1 | RP pagination / server-side filtering for transaction evidence? | RESOLVED | For the analytical surface (§12a: account/time/symbol filters + limit/offset). Legacy `/evidence` unchanged |
| OQ-2 | Retrieval limits + max candidate count (values) | DECIDED (provisional values) | Retrieval budgets implemented (§12a); matching knobs frozen in §12b.3. Calibration remains OPEN |
| OQ-3 | Authoritative value field: `value` vs `price × quantity` | RESOLVED BY EXISTING SYSTEM | `value = price × quantity` (quote-currency notional), confirmed at three RP code sites; safe as primary matching quantity (§12b.1) |
| OQ-4 | Account attribution & counterparty transaction retrieval on RP | RESOLVED (§12a) | Account-attributed bounded retrieval implemented end-to-end |
| OQ-5 | Amount tolerance form and value | DECIDED (§12b.3; provisional) | Relative + absolute-floor semantics; V1 default 5% / floor 1.0. Numeric prior art is the DECLARED-but-unused `COORDINATED_TRADE_*` config (§12b.1) — not blindly copied as calibrated behavior |
| OQ-6 | Aggregation strategy details | DECIDED (§12b.4) | 1:1 / 1:N / N:1, single anchor, greedy timestamp-order accumulation, leg cap, no backtracking; N:M/subset-sum excluded |
| OQ-7 | Exact time tolerance + timestamp handling | DECIDED (§12b.3; provisional) | V1 default 30 s (declared prior art); UTC instants, inclusive comparison; unparseable timestamps excluded + flagged |
| OQ-8 | `NO_MATCH` rides `success` vs `EMPTY` | DECIDED (§12b.6) | Four analysis statuses; tool never emits `EMPTY`; mapping table in §6 |
| OQ-9 | Retry / re-plan policy; bounded autonomous loop | RESOLVED BY BASELINE (2026-09-19) | The Agent Loop phase contract is defined in `docs/baselines/2026-09-19-bounded-agent-loop-v1.md` (bounded iterations, four-decision model, retry policy); not yet implemented |
| OQ-10 | Artifact representation: new `ArtifactTypeV2` vs section in existing bundles | IMPLEMENTATION DECISION | Provenance model unchanged either way |
| OQ-11 | Capability gating: new capability id vs reuse of `self_opposite_trades` | RESOLVED (registry-time decision, §12c) | The cross-account step lives in `trade_investigation` (gated on `self_opposite_trades` — no new skill); the tool itself adds no finding-capability gate |

Risks:

- **Data availability (high)**: ~~without OQ-4 the capability can only
  return `INSUFFICIENT_DATA`~~ OQ-4 resolved (§12a) — the risk becomes:
  cases whose connected accounts hold no trades still yield
  `INSUFFICIENT_DATA`/`NO_DATA`; expectations must be set accordingly.
- **Semantic creep (high)**: candidates misread as confirmed coordinated
  trading. Mitigated by §1/§9 language rules + forbidden-fragment
  checkers.
- **Combinatorial blow-up (medium)**: bounded by §5 caps; overflow must
  stay visible.
- **Money math (medium)**: float tolerance bugs; mitigated by a pinned
  rounding/comparison rule + determinism tests (OQ-7).
- **Rename churn (medium)**: `self_opposite_trades` rename touches
  planner regexes, capability ids, tests, docs; must be
  regression-gated (§11 item 6).
- **Performance on complete-record retrieval (low-medium)**: mirrors the
  existing `expose_complete_records` evidence view; bounded by retrieval
  limits.

---

## 12a. OQ-4 Resolution — Bounded Account-Scoped Transaction Retrieval
**(implemented 2026-09-18; this section records the decision, the
implemented capability, and what remains open)**

### Decision

Cross-account analysis retrieves account-attributed transaction evidence
through a **dedicated bounded, server-side-filtered Risk Platform
retrieval surface**. The account universe is resolved from AUTHORITATIVE
case context (the case account plus its cluster-connected accounts) and
**cannot be expanded by the LLM or the planner**: scope violations are
rejected at both the platform boundary and the client contract layer.
Raw transaction rows never leave tool execution as LLM observation —
only a compact, row-free observation does.

### What exists today (unchanged)

- The legacy `GET /api/risk/cases/{user_id}/evidence` surface and its
  unattributed `TransactionEvidence` projection are untouched; they keep
  serving the concrete-evidence views (`finding_drilldown`).
- `GET /api/risk/cases/{user_id}/network-signals` (pre-existing) is the
  authoritative source of case-connected accounts (`user_id`,
  relationship type, risk level/score, full `connected_account_count`).

### What was implemented

**Risk Platform** (`risk-platform-demo`, read-only):

- New endpoint `GET /api/risk/cases/{user_id}/transactions`
  (`backend/app/api/routes/risk.py`):
  - Query: `account_ids` (comma-separated; **default = the case account
    only**), `start_time` / `end_time` (ISO-8601), `symbols`
    (comma-separated), `limit` (default 50, clamped to
    `TRADE_EVIDENCE_MAX_PAGE_SIZE`=200), `offset` (≥ 0).
  - **Server-side scope enforcement** (`EvidenceService.
    resolve_transaction_scope`): every requested account must be the case
    account or cluster-connected to it; violations → `422
    ACCOUNTS_OUT_OF_CASE_SCOPE` listing the offending ids (no data is
    read). More than `TRADE_EVIDENCE_MAX_ACCOUNTS`=50 accounts → `422
    TOO_MANY_ACCOUNTS`.
  - **Server-side filtering** over the indexed trade columns
    (`idx_trades_user_timestamp`, `idx_trades_symbol_time`); ordering is
    deterministic (`timestamp, trade_id` ASC) so offset paging is stable.
  - **Completeness semantics**: `total_matched` is the full server-side
    count under the filters; `truncated` marks a partial view;
    `has_more`/`next_offset` drive paging. Zero matches is `total_matched
    = 0` with an empty list — never an error shape.
- Response schema `AccountTransactionsResponse` with row type
  `AccountTransactionRecord` — exactly the minimal analytical projection
  (`backend/app/models/schemas.py`).
- Tests: `backend/tests/test_account_transactions_evidence.py` (7 tests
  against the live DB: default scope, multi-account retrieval,
  out-of-scope rejection, unknown account, server-side symbol/time
  filters, pagination completeness, empty-not-error).

**Agent Client** (`investigation-agent-client`):

- Adapter (`backend/app/adapters/risk_platform.py`):
  `fetch_network_signals` / `fetch_account_transactions` (+ async raw
  variants), following the existing async-raw + sync-wrapper convention.
- **Logical data contract** (`backend/app/trade_evidence.py` — new):
  - `TradeEvidenceQuery(case_id, account_ids, start_time, end_time,
    symbols)` — investigation **scope only**; deliberately no
    limit/cursor fields (budgets and pagination are system-controlled).
  - `AccountTransaction` — the 8-field projection (`transaction_id`,
    `account_id`, `symbol`, `side`, `price`, `quantity`, `value`,
    `timestamp`); source extras (e.g. `risk_reason`) are deliberately
    not projected; rows missing identity fields are malformed, never
    skipped.
  - `RetrievalStatus`: `complete` / `no_data` / `truncated` / `error`.
  - `TradeEvidenceRetrieval`: `records` (**tool-internal** — deterministic
    analysis input), `observation` (**the only LLM-facing payload** —
    counts, per-account totals, symbol set, time range, flags; row-free
    by construction), `total_matched`, `pages_fetched`,
    `truncated_reasons`, `warnings`, `error`.
  - Scope resolution from network signals + **client-side scope
    validation** duplicating the RP check (defense in depth);
    out-of-scope requests are `ACCOUNT_OUT_OF_SCOPE` errors listing the
    offenders — never narrowed, never fetched.
  - Budgeted pagination: page size clamped to the remaining budget;
    a mid-pagination failure discards the partial accumulation and
    returns `error` with a warning (an error is never composed from a
    half-fetched dataset, and a partial dataset is never presented as
    complete).
  - Truncation reasons (stable identifiers): `retrieval_budget_exhausted`,
    `max_pages_reached`, `account_budget_exhausted`,
    `account_scope_listing_bounded`.
- Retrieval budgets (`backend/app/config.py`, system-controlled, never
  LLM-chosen): `TRADE_ANALYSIS_MAX_ACCOUNTS=5`,
  `TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS=500`,
  `TRADE_ANALYSIS_MAX_PAGE_SIZE=100`, `TRADE_ANALYSIS_MAX_PAGES=10`,
  `TRADE_ANALYSIS_MAX_CANDIDATES=20` (reserved for the matching phase).
  **These defaults are provisional, not product-final values** (OQ-2).
- Tests: `backend/tests/test_trade_evidence_retrieval_v1.py` (20 tests:
  multi-account retrieval, scope rejection, budget truncation, pagination
  follow-through, complete/truncated/no-data/error distinguishability,
  provenance preservation, minimal projection, row-free observation,
  adapter parameter encoding).

### What remains unresolved

*(Historical record of the retrieval phase. The matching-related items
below were subsequently decided in §12b — the OQ table in §12 carries
the current state.)*

- All matching-phase items are untouched by this phase: tolerances
  (OQ-5/7), aggregation strategy (OQ-6), candidate contract (OQ-10/11),
  tool/skill/planner integration (§13 "must be implemented").
- Budget VALUES (OQ-2) are provisional; confirm before production use.
- The `no_data`-vs-`empty` ToolResult mapping for the future tool
  (OQ-8) and legacy-surface pagination remain as before.

---

## 12b. Matching-Analysis Phase — Implementation Contract
**(added 2026-09-18; frozen contract for the `cross_account_trade_analysis`
implementation phase — no tool code exists yet)**

### 12b.0 A/B — two distinct semantics, never merged

**A. Existing self-opposite-trade detection (Risk Platform capability):**
one account (the case account); active semantics is the feature
`opposite_trade_ratio = min(BUY count, SELL count) / total trades` over
that account's own trade stream (`feature_engineering._trading_features`),
with the rule "Coordinated Trading Pattern" firing at ratio > 0.4
(contribution 35). **Count-based only — no time proximity, no pairing,
no value matching.** Canonically named `self_opposite_trades` (§8).

**B. New cross-account candidate relationship analysis (this phase):**
two or more DISTINCT accounts; same instrument; opposite direction;
near timestamps within a configured tolerance; approximately matched
transaction value/notional; bounded 1:1 / 1:N / N:1 aggregation;
deterministic candidate ranking. Output is CANDIDATE relationships —
never a detection verdict.

**Do not blindly reuse the self-opposite numbers as cross-account
rules.** The only numeric prior art in the repository is the
`COORDINATED_TRADE_*` config block (window 30 s, price tolerance 1%,
quantity tolerance 5%, min occurrences 3) — and it is **declared config
with zero code references** (verified across the RP repo). It is
intent-level prior art, adopted below only as *starting defaults*,
explicitly provisional; the active self-opposite ratio contributes no
matching semantics.

### 12b.1 Value/notional definition (decided)

Primary matching quantity = `value` per §4.3 (RESOLVED BY EXISTING
SYSTEM): deterministic `price × quantity`, quote-currency notional;
safe because candidates are always same-symbol. `price`/`quantity`
travel as supporting evidence, never matched in V1.

### 12b.2 Time matching (decided)

- `TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS`, V1 default **30** — the
  declared prior-art window (§12b.0); **provisional**, calibration OPEN.
- System configuration (client `Settings`), **never LLM-chosen**; the
  planner never sees threshold values.
- Comparison on parsed UTC instants, **inclusive** at the boundary;
  absent/unparseable timestamps exclude the record from matching and are
  surfaced in data quality.

### 12b.3 Amount matching + frozen V1 parameters (decided; provisional values)

Match predicate: `|Δv| ≤ max(absolute_floor, relative × max(v_a, v_b))` —
relative + absolute-floor semantics; inclusive.

| Parameter (client `Settings`) | V1 default | Basis | Status |
|---|---|---|---|
| `TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS` | 30 | declared prior art (config-only) | DECIDED, provisional |
| `TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE` | 0.05 (5%) | analogue of the declared quantity tolerance (notional is size-like; the 1% price tolerance prices a different dimension) | DECIDED, provisional |
| `TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR` | 1.0 (quote ccy) | float-noise floor for near-zero notionals | DECIDED, provisional |
| `TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE` | 5 | bounded aggregation cap | DECIDED, provisional |
| `TRADE_ANALYSIS_MAX_CANDIDATES` | 20 (existing key) | top-K after ranking | DECIDED |

All values are calibration input for the next phase, not validated
product constants; every payload echoes the tolerances actually applied.
The LLM must not choose arbitrary matching thresholds — none of these
are planner/LLM-visible parameters.

### 12b.4 Multi-trade aggregation boundary (decided)

Supported in V1 — exactly three shapes, one anchor per candidate:

- `1_to_1`: one trade per side.
- `1_to_N`: anchor on side A vs ≥2 legs on side B.
- `N_to_1`: ≥2 legs on side A vs anchor on side B.

Algorithm shape: within a symbol, candidate pairs start from
opposite-side, different-account, within-tolerance trades; when a
1:1 value match fails, the anchor accumulates opposite-side legs
greedily in timestamp order within its time window until the running
sum matches within tolerance or the leg cap is hit; no backtracking.

Explicitly excluded: unrestricted N:M matching, arbitrary subset-sum,
unrestricted combinatorial search. **Why this boundary exists**: bounded
complexity (O(n log n + n·k) instead of exponential), predictable
runtime under the retrieval budgets, explainability (each candidate
names its exact legs so an analyst can re-derive the match), and
controlled false-matching risk (greedy accumulation cannot cherry-pick
convenient subsets). Accepted trade-off: greedy + no-reuse may suppress
a true second match that shares legs — a conservative bias, documented
rather than hidden.

### 12b.5 Candidate identity, ranking, limits (decided)

- **Side naming**: `account_a` = the candidate account whose earliest leg
  timestamp is earlier; tie → lexicographically smaller account id.
- **No reuse**: each transaction id appears in at most one candidate
  (anchor-first, timestamp-order consumption) — duplicates/overlaps are
  structurally excluded, and the suppression is observable in the payload.
- **Ranking**: deterministic ordering by matching-strength dimensions —
  relative value difference, max time gap, leg count — ascending, with
  stable tie-breakers. The exact composite weighting is an
  **IMPLEMENTATION DECISION**; the dimensions, determinism, and
  tie-breaker requirements are frozen. The rank is an **ordering
  device only** — NOT a risk score, never rendered as an authoritative
  risk judgment.
- **Result limit**: top-K = `TRADE_ANALYSIS_MAX_CANDIDATES` after
  ranking; candidates-found vs candidates-emitted both reported.

### 12b.6 Tool output contract (frozen)

The four analysis statuses and their exact meanings (§6), the
retrieval→analysis status mapping, the frozen candidate field list, and
the ToolResult mapping (tool never emits `EMPTY`; resolves OQ-8) are
frozen in §6 and normative for implementation. Raw transaction dumps
MUST NOT be the normal Agent/LLM observation — the observation carries
candidate records, counts, completeness echo, and config echo only.

### 12b.7 Deterministic / probabilistic boundary

| Deterministic system/tool responsibilities | LLM responsibilities |
|---|---|
| retrieval (§12a), scope enforcement, filtering | decide whether this capability is relevant to the request |
| time comparison, value calculation/comparison | decide when to invoke it |
| aggregation, candidate ranking, result limits | interpret the compact observation |
| provenance (ids + evidence refs) | decide the next investigation action |

The LLM must NOT: perform the financial matching calculation itself ·
invent candidate relationships · choose arbitrary thresholds · treat a
candidate relationship as proof of coordinated intent.

### 12b.8 Evidence interpretation

Terminology is normative: **"candidate relationship" / "candidate
pattern"**. A matching result must NOT be described as confirmed
coordinated trading, confirmed intent, or confirmed collusion — in
composer prose, artifact content, UI labels, and tests (forbidden-
fragment checkers). Standing caveat for every presentation surface:

> Candidate relationships are quantitative matches under configured
> analytical tolerances within the analyzed evidence window. They are
> leads for analyst judgment, not findings of coordinated behavior.

### 12b.9 Future Agent Loop boundary (explicitly out of scope here)

This phase implements the **capability**, not autonomous Agent
behavior. The current Planner/Executor turn loop is NOT redesigned. A
future Agent Loop phase owns:

```
Observation → Continue | Re-plan | Stop | Block
```

That phase will need bounded-stop outcomes, iteration limits, and
retry/re-plan policy (OQ-9 — now contract-defined in
`2026-09-19-bounded-agent-loop-v1.md`). This tool's obligation is only
to PROVIDE the structured observation (§6/§12b.6) that the future
decision layer consumes.

### 12b.10 Future product requirement — user-visible Agent Activity / Execution Trace

Recorded NOW so the roadmap preserves it; **frontend is NOT part of the
matching phase.** A future Agent Loop phase must expose a user-safe
execution activity view, rendered from **structured runtime/telemetry
events** (extension of the existing `AgentTelemetryEvent` contract,
ARCHITECTURE_V2 §9.1) — never parsed backend log text. Per-iteration
surface: goal · current plan / completed steps · tool invoked · tool
result summary · decision summary · Continue / Re-plan / Stop / Block ·
iteration number · relevant evidence/artifact references. Hidden
chain-of-thought is never exposed — decision summaries are user-safe
structured summaries, not raw model reasoning.

---

## 12c. Matching-Analysis Phase — Implementation Record
**(added 2026-09-19; the §12b contract is implemented — nothing here
re-decides it, this records what was built and the concrete choices made
within the contract's stated freedoms)**

### Implementation status: DONE

The `cross_account_trade_analysis` domain tool exists and is wired into
the runtime end-to-end (planner → contract check → executor → retrieval
→ deterministic matching → composer → artifact). Deferred items (§12b.9/
§12b.10, MCP, calibration) remain untouched.

### Files / capabilities added

- `backend/app/domain_tools/cross_account_trade_analysis.py` — the tool:
  scope-only input (`case_id` injected, optional `finding_id` anchor,
  `analysis_type` locked to `cross_account_opposite_trade`); consumes
  `retrieve_trade_evidence` exclusively (no direct adapter calls);
  deterministic engine (`find_candidates`) with rank/candidate-id
  assignment; §6 payload/status contract; top-K emission cap.
- `backend/app/config.py` — matching keys added:
  `TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS=30`,
  `TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE=0.05`,
  `TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR=1.0`,
  `TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE=5`.
- `backend/app/trade_evidence.py` — `resolve_account_scope` extracted
  (behavior-preserving) so the tool and the retrieval loop share one
  authoritative scope resolution.
- `backend/app/skills.py` — step `analyze_cross_account_trades` bound to
  the tool with the `analysis_type` lock; parameterization registered
  (P19); `trade_investigation` updated (allowed_tools + planning_steps,
  analysis first).
- `backend/app/planner_v2.py` — `_CROSS_ACCOUNT_TRADE_RE` routing hook
  evaluated BEFORE the self-opposite hook; prompt guidance; deeper-ask
  vocabulary extended.
- `backend/app/executor_v2.py` — provider registration + runtime
  injection of `case_id`/`finding_id` for the analysis step.
- `backend/app/domain_tools/finding_drilldown.py` — canonical
  `view="self_opposite_trades"` bounded-unsupported branch;
  `SELF_OPPOSITE_TRADES_NOT_SUPPORTED`; legacy `view="opposite_trades"`
  accepted as alias (same bounded answer).
- `backend/app/adapters/risk_platform.py` — capability
  `opposite_trades` → `self_opposite_trades` in `derive_capabilities`.
- `backend/app/investigation_service.py` — composer branches for
  `MATCH_FOUND` / `NO_MATCH` / `INSUFFICIENT_DATA` (candidate
  terminology; intent-free wording) + capability-phrase rename.
- `backend/app/domain_tools/artifact_bundle.py` — "Candidate
  Cross-Account Relationships" section (candidates, criteria, IDs,
  limitations, caveat) with contributing-call provenance.
- `backend/app/context_resolution.py` — capability-guidance text
  updated for the cross-account capability.
- Eval: E16 (scripted cross-account analysis, Layer A, 16 scenarios),
  P11 (cross-account routing, Layer B), `cata_patches` fixture seams,
  runner generalized to an ExitStack.
- Tests: `backend/tests/test_cross_account_trade_analysis_v1.py` — the
  §10 17-case matrix + tool-boundary, routing, composer, and artifact
  coverage with exact-value assertions. All rename surfaces updated
  across ~14 existing test files.

### Implementation decisions within §12b's stated freedoms

1. **OQ-11 (registry-time)**: the cross-account step joins
   `trade_investigation` (gated on `self_opposite_trades`); the tool
   itself adds no finding-capability gate (case-scoped analysis; RP
   enforces account scope at the retrieval layer). Consequence: the
   skill's PRIMARY step is now the executable analysis tool, so
   `trade_investigation` became planner-eligible for opposite-trade
   findings — previously it was entirely non-executable (P19). The
   self-opposite VIEW remains registered-but-unimplemented (bounded
   unsupported; E15 semantics preserved).
2. **Counterparty constraint**: a candidate record has exactly
   `account_a`/`account_b` (§6), so aggregation legs are restricted to
   ONE counterparty account — the earliest opposite leg's account
   (deterministic).
3. **Rank/ID assignment** lives in the engine (all candidates, ranked
   order); the tool applies the top-K cap afterwards, so
   `candidates_found` vs `candidates_emitted` is directly observable.
4. **Anchor iteration** is global timestamp order over all records
   (symbol-filtered, side-paired per anchor); consumed transactions
   never anchor again and never join another candidate.
5. `INSUFFICIENT_DATA` carries `next_data_needed` strings distinguishing
   empty scope vs truncated retrieval; the tool never emits `EMPTY`.

### Validation results

- New suite: 37 tests green (17-case matrix, boundary inclusivity,
  leg-cap, no-reuse, ranking, top-K, statuses, provenance, compact
  observation, routing precedence, composer wording, artifact section).
- Layer A: **16/16** scripted scenarios green (incl. new E16).
- Layer B (real LLM, 3 runs/scenario): **P11 cross-account routing
  3/3 PASS** (routes to `analyze_cross_account_trades`, never the self
  step); **P08 same-account routing 3/3 PASS** post-rename (no
  regression). Overall 30/33 = 90.9% run-level, 10/11 scenarios
  perfectly stable; P09 remains the documented compound-request planner
  limitation (0/3, unchanged).
- Full backend suite: **869 passed**, 43 failed — the failure set is
  byte-identical to the pre-phase baseline (legacy pre-V2 suites +
  telemetry/MCP tests requiring a live RP at :8000). Zero new failures,
  zero regressions.
- Rename regression-gated: P19 tests, skill-shape tests, product-
  boundary tests, E15, and planner routing tests updated to the new
  canonical contract and green.

### Deviations

- None from the §12b frozen contract. The only behavioral delta outside
  the tool itself is the §12c-1 gating consequence (trade skill now
  plannable), which the OQ-11 decision authorized.
- Deferred doc-sync: `SKILL_MODEL_V1.md` / `AGENT_CLIENT_ARCHITECTURE_V2.md`
  still carry the pre-rename `opposite_trades` vocabulary in their
  frozen Week-1 tables; the canonical naming lives in this baseline and
  the code. Syncing those documents is follow-up documentation work, not
  a runtime concern.

---

## 12d. Self-Opposite View Completion Record
**(added 2026-09-19; E15's capability is now COMPLETE — the canonical
`finding_drilldown(view="self_opposite_trades")` is implemented and
executable)**

### Implementation status: COMPLETE

The same-account opposite-side inspection capability is executable. It
composes/presents the Risk Platform's AUTHORITATIVE aggregate semantics —
it does NOT duplicate or replace them, and it is NOT the cross-account
analysis: no pairing, no time proximity, no value matching, no second
`opposite_trade_ratio` definition.

### Capability surface

- `finding_drilldown(view="self_opposite_trades")` (`ALLOWED_VIEWS` now
  includes the view). Payload (`outcome="success"`):
  `semantics="same_account_opposite_side"`, `account_id` (the SAME
  account), `opposite_trade_ratio` (RP `feature_evidence` value echoed
  verbatim, `ratio_source="risk_platform_feature_evidence"`),
  `rule` (backing `rule_evidence` — name/description/trigger/threshold/
  contribution — echoed via the existing strict-anchoring rule normalizer,
  `None` when no rule backs the finding), `records`/`record_count`/
  `complete: true` (the account's complete transaction record set via the
  existing evidence normalizer), `requested_view_alias` when the legacy
  alias was used. Provenance: per-record transaction `evidence_refs` +
  the finding's `policy_refs`.
- Capability gate: view-scoped — the self view requires the
  `self_opposite_trades` finding capability; without it the result is
  bounded `unsupported` (`CAPABILITY_NOT_SUPPORTED`, capability detail
  `self_opposite_trades`), exactly the E15 contract. Other views keep the
  timeline gate.
- Status semantics (existing conventions only): success (ratio present
  and/or records exist; `ratio == 0` is a SUCCESS answer: "no opposite-
  side activity observed"); `empty` (no aggregate AND no records — no
  relevant opposite-side activity data); `unsupported` (capability gate);
  `integration_error`/`validation_error` unchanged. No new statuses.
- Registry: `(("view", "self_opposite_trades"),)` added to
  finding_drilldown's supported parameterizations — the step is now an
  executable P19 path (trade_investigation's primary path remains the
  cross-account analysis tool).
- Planner: same-account phrasings extended ("check this account's
  opposite trades", "show self-opposite trading", "does this account
  trade both sides?") → `inspect_self_opposite_trades`; cross-account
  routing stays ahead of the self hook (ordering rule unchanged, tested).
- Composer: same-account wording ("opposite-side trading activity by this
  SAME account", ratio %, rule echo, record count, explicit "implies no
  cross-account relationships and nothing about intent" caveat); zero-
  ratio and empty states worded distinctly; no candidate/coordinated
  terminology.
- Artifact: "Self-Opposite (Same-Account) Trade Activity" section (ratio,
  rule, record table, same-account caveat) with contributing-call
  provenance.
- Compatibility alias RETAINED: `view="opposite_trades"` normalizes to
  the canonical view (same bounded, capability-gated behavior) and is
  recorded on the payload as `requested_view_alias`. The retired
  `SELF_OPPOSITE_TRADES_NOT_SUPPORTED` error code is superseded by the
  capability gate (`CAPABILITY_NOT_SUPPORTED` with capability detail) —
  bounded unsupported either way, now with the accurate reason.

### Tests / results

- New focused suite `tests/test_self_opposite_trades_view_v1.py`: 17
  tests — executable path, canonical success with verbatim RP aggregates,
  no-second-ratio assertion, alias, capability gate, EMPTY vs zero-ratio
  distinction, provenance, routing precedence (incl. the both-regexes
  case), composer/artifact wording rules (same-account terms only).
- Layer A: **17/17** scripted scenarios — E15 (non-capable finding →
  bounded rejection) preserved; new E17 (capable finding → success with
  same-account wording).
- Full backend suite: **887 passed**, 43 failed — failure set identical
  to the standing pre-existing baseline. Zero new failures.
- Updated contracts: capability-matrix/product-boundary/P19 tests now
  assert the implemented+executable state.

### Deviations

- None from documented semantics. The one wording-level change: the
  bounded rejection reason for non-capable findings is now the accurate
  capability gate (previously a not-implemented bound); external E15
  behavior (outcome, composer phrase) is unchanged.

---

## 13. Summary

**Already supported by the existing code (no change needed):**

- Domain-tool boundary and `ToolResult` contract with the five outcomes,
  success-with-`evidence_missing`, warnings, and evidence refs
  (`models.py`, `domain_tools/__init__.py`).
- Bounded retrieval pattern with complete-record opt-in and truncation
  honesty (`finding_drilldown`, `RiskPlatformAdapter.fetch_case_evidence`).
- **The OQ-4 retrieval capability (§12a)**: account-attributed bounded
  transaction retrieval end-to-end — RP endpoint with server-side scope
  enforcement/filtering/completeness, client adapter surface, logical
  contract (`trade_evidence.py`), system retrieval budgets, and
  boundary tests on both sides.
- **The matching-phase contract (§12b)**: frozen parameters, aggregation
  boundary, ranking requirements, output contract, status mapping, and
  evidence-interpretation rules — implementation follows the contract,
  it does not re-decide it.
- Skill/step registry, parameter locks, contract checker, and the
  executable-path rule (`skills.py`) — the new step attaches by registry
  entry, no architecture change.
- Planner constrained vocabularies + deterministic intent hooks
  (`planner_v2.py`) — routing pattern for the new intent exists.
- Executor runtime argument injection for `case_id`/`finding_id`
  (`executor_v2.py`) — matches the scope-only input contract.
- Deterministic response composition and artifact provenance closure
  (`investigation_service.py`, `artifact_bundle.py`).
- Three-layer evaluation harness with deterministic checkers
  (`backend/eval/`).
- Turn-based agent loop with EMPTY-is-a-valid-answer and no-retry
  semantics (`investigation_service.py`, `executor_v2.py`).

**Matching phase (per §12b): IMPLEMENTED — see §12c. Self-opposite view
(per §8): COMPLETE — see §12d.** Remaining in their wake:
architecture-doc naming sync (deferred, §12c), live Layer C scenario
pending a seeded multi-account demo fixture, and tolerance calibration
(values stay provisional).

**Explicitly deferred (not in the matching phase):**

- Agent Loop behavior — autonomous continue/re-plan/stop/block,
  bounded-stop outcomes, retry policy (OQ-9; §12b.9). The tool provides
  the structured observation; the loop phase decides over it.
- **Agent Activity / Execution Trace UI** — user-safe execution activity
  from structured telemetry events (§12b.10); future product/runtime
  requirement, now recorded.
- MCP exposure of the new capability; rich candidate-scoring models;
  tolerance calibration from production data (values stay provisional).

**Open decisions:** none remaining in this baseline's own scope —
OQ-9 (loop scope) is now contract-defined in the separate Agent Loop
baseline (`2026-09-19-bounded-agent-loop-v1.md`, implementation
pending); OQ-2/5/6/7 values are DECIDED-but-provisional with
calibration OPEN; OQ-10 is an implementation decision within frozen
content rules. OQ-1/3/4/8/11 are resolved (§12a / §12b / §12c).
