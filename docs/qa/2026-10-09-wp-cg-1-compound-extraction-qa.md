# WP-CG-1 — Compound Extraction Protocol — QA Record

**Date**: 2026-10-09
**Status**: IMPLEMENTED + L1-VALIDATED (16/16) + goal/GSP regression
green (250 passed). Not committed / not pushed.
**Approved basis**: design v1 Revision 2 §10-E1/E2, implementation
plan v1 §7. Scope guard: this WP lands the extraction protocol ONLY —
acceptance hardening (≥2 rule, canonical dedup, all-or-nothing on
field validity) is WP-CG-2 and was not implemented.

## Objective

The core acceptance question: **can the model's multiple user goals
reach the system in structured form, instead of being lost to the
single goal_type and the AMBIGUOUS branch?** Answer, verified by L1:
yes — the SAME single goal-understanding call now returns
`goal_resolution: COMPOUND` with an N-entry `required_outputs`
proposal that is attached to the representation and reaches the loop's
contract builder.

## Changed files

| File | Change |
|---|---|
| `app/goal_resolution.py` | `GoalResolutionStatus.COMPOUND` (documented: distinct from AMBIGUOUS); `GoalRepresentation` validator COMPOUND branch (structural only: `goal_type=None` per AD-1, no ambiguous types, `contract_proposal` with ≥2 raw entries — field-level validation stays in `build_goal_contract`); `_build_goal_prompt` flag-gated resolution rule (multi-part → COMPOUND + N entries in request order; AMBIGUOUS narrowed to genuine which-one uncertainty); `understand_goal` COMPOUND branch (flag ON → proposal attached; violation → bounded heuristic fallback) |
| `app/agent_loop.py`, `app/api/investigations.py`, `app/api/schemas.py`, `tests/test_agent_activity_api_v1.py` | (from the approved PMO follow-up, landed first this round) `_call_goal_resolver` signature-bind dispatcher replacing the 4-arg/TypeError fallback; checkpoint decision record preserves the real failure category (`goal resolution failed: TypeError: …`); projection/schema additive fields |
| `tests/test_cg3_contractless_safety_v1.py` | +`TestResolverCompat` (3 tests) |
| `tests/test_cg1_compound_extraction_v1.py` | NEW — 16 tests (15 extraction/compat + 1 checkpoint integration) |

## WP-CG-1 semantics implemented

- COMPOUND ≠ AMBIGUOUS: COMPOUND is a RESOLVED multi-deliverable
  outcome (goal_type null, N ordered entries); AMBIGUOUS stays genuine
  which-one uncertainty (handled by the WP-CG-3 bounded stop).
- Same single LLM call (asserted: `llm.calls == 1` for COMPOUND).
- LLM proposes / system disposes: the model cannot self-declare — a
  COMPOUND payload violating AD-1 (`goal_type` set) or the ≥2
  structural rule falls into the bounded heuristic fallback (never
  repaired, never narrowed).
- Flag OFF: the base prompt is byte-identical (asserted: no
  `required_outputs`, no `COMPOUND`); a COMPOUND payload under OFF
  falls into the deterministic heuristic route (bounded, no proposal).
- Projection safety: `project()` serves `goal_resolution=COMPOUND`
  and never the proposal.

## L1 results — PASS (16/16)

`tests/test_cg1_compound_extraction_v1.py`:
- Single-goal backward compat: INFERRED+1-output proposal unchanged
  (one call; multi-evidence output NOT over-split); EXPLICIT-payload
  behavior documented as pre-existing bounded fallback (the extractor
  never had an EXPLICIT branch — pre-existing, unchanged).
- COMPOUND: 2 and 3 outputs arrive structured, in request order;
  swapped-order preserved; enumerated/compound-verb payload shapes
  identical; projection excludes the proposal.
- Boundaries: genuine AMBIGUOUS never misread as COMPOUND (no
  proposal); COMPOUND with goal_type set → bounded fallback;
  1-entry COMPOUND → structurally rejected → bounded fallback;
  LLM-empty → heuristic + `diagnostics` provenance; flag OFF → no
  COMPOUND, no proposal.
- Prompt contract: flag-ON prompt carries the COMPOUND rule +
  AMBIGUOUS narrowing; flag-OFF prompt byte-identical base; single
  call for COMPOUND.
- **Checkpoint integration evidence**: a COMPOUND representation
  produced by the real extraction reaches the loop checkpoint and —
  via the resolution-agnostic builder — lands in state as a 2-output
  contract (`goal_type=None`, claims in order,
  `goal_contract_decision=contract_accepted`) on a real run
  (`test_compound_proposal_reaches_state_as_contract`).

## Resolver-compat fix (prerequisite, landed first this round)

Completed the interrupted PMO follow-up: `_call_goal_resolver`
signature-bind dispatcher (decide-before-call), checkpoint single call
site, real failure category preserved in the decision reason.
`TestResolverCompat` 3/3 (single-call assertions for 3-arg and 4-arg
resolvers; internal TypeError → one call, category preserved in
`goal_contract_decision.reason`, no leak into the user response).
Details recorded in the WP-CG-3 QA record
("Resolver-compat follow-up").

## Regression results — PASS

goal_resolution + goal_resolution_api + gsp1–gsp7 (incl. API E2E) +
al351 + cg3 (L1 + API E2E) + activity_api suites: **250 passed,
0 failed**. Single-goal extraction, GSP-2 acceptance tests, GSP-6/7
flows, the CG-3 safety paths and the projection whitelists all green
like-for-like.

## Not done / deferred

- WP-CG-2: contract-acceptance hardening (COMPOUND ≥2 acceptance rule,
  canonical semantic dedup, explicit all-or-nothing on field validity).
  Today a well-formed COMPOUND proposal is accepted by the existing
  resolution-agnostic builder — the CG-2 rules tighten this path.
- WP-CG-4: real-HTTP integration scenarios (incl. the `goal_llm` seam
  extension).
- WP-CG-5: browser journeys and the Session-01 exploratory regression.
- Real-model paraphrase validation of the reconciled prompt:
  exploratory-tier evidence, deliberately deferred to WP-CG-5 per the
  plan.

Not committed / not pushed.
