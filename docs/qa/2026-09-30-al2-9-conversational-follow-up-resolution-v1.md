# AL-2.9 — Conversational Follow-up Resolution — QA v1

**Date**: 2026-09-30
**Status**: **PASS** (with documented limitations, §17)
**Scope**: the SECOND turn of a real conversation — finding follow-ups
resolve against the established conversation context; 高频交易 grounds to
High Trading Frequency (never High Withdrawal Frequency); dual-intent
governed planning; honest negative boundaries. M2 foundation, Safe-Select
separation, and all AL-2.8 invariants unchanged.

---

## 1. Objective

Before AL-3: fix and prove that a natural-language finding follow-up in a
CONTINUOUS conversation resolves to the correct finding and produces a
real natural-language answer — not `context_unresolved`, not the wrong
finding, not runtime metadata.

## 2. Confirmed User Scenarios (real case U00010)

Both findings exist live and are different findings:

- **High Trading Frequency** — "54 trades were recorded in 24 hours, an
  elevated level of trading activity for a single-day window." (RP risk
  feature `trade_frequency_24h = 54`; RP has **no** trading-frequency
  rule.)
- **High Withdrawal Frequency** — backed by RP rule "High withdrawal
  frequency".

| Scenario | Turn 1 | Turn 2 | Required |
|---|---|---|---|
| A | 展开一下00010的具体情况 | 为什么说共享设备是风险项 | F5 explain, graph answer |
| B | 展开一下00010的具体情况 | 为什么说高频交易是风险项，给我输出高频交易的具体交易明细 | **F7** High Trading Frequency + explain + transaction records (never F3, never unresolved) |
| CMP | 调查00010 | same as B | identical behavior — context, not phrasing |
| N | 展开一下00010的具体情况 | 谈谈虚拟币洗钱这个风险项 | bounded honest, zero tools |

## 3. Runtime Audit (done BEFORE any code change)

Live 13-layer traces against the CURRENT code (QA backend :8010, real RP,
real LLM), per scenario, covering: investigation state, case_id, previous
turn info, request, resolver context, candidates, normalizer input/output,
metadata consulted, grounding, intent, planner, outcome:

| # | Layer | A.turn2 | B.turn2 |
|---|---|---|---|
| 1–4 | state / case / prev / request | case=U00010, focus=None (case-level turn1), request as above | same |
| 5 | resolver context | case_id + full candidate list **passed** (verified both /turn and /goal) | same |
| 6 | candidates | 8 live RP findings | 8 live RP findings |
| 7–8 | normalizer | not reached — deterministic lexicon exact tier hits 「共享设备」first (documented precedence) | (pre-fix run) registry had NO trading concept → fell to the generic Stage-1 assist, which picked F7 by title/description similarity — NOT the M2 foundation; (post-fix) `HIGH_TRADING_FREQUENCY` ← 「高频交易」→ grounded [F7] |
| 9 | metadata | F5 record (UNVERIFIED, does not block) | F7 record (UNVERIFIED, does not block) |
| 10 | grounding | F5 unique | F7 unique (F3 core disjoint) |
| 11 | intent | explain_finding | explain_finding **+** evidence(明细) |
| 12 | planner | (pre-fix) explain+policy — evidence half dropped by the elif; (post-fix) explain_signal + inspect_transactions | |
| 13 | outcome | completed, real graph answer | completed, real feature answer + 64 transaction records |

**Findings:**

1. On the CURRENT code the follow-up resolution chain was already wired
   for turn 2 through BOTH the /turn and /goal paths (case_id +
   `case_findings` are passed by `InvestigationService.run_turn` and
   `AgentLoopRunner._run_loop` respectively — verified live, loop run
   completed `goal_satisfied` with the correct F5 answer).
2. The user-reported failures reproduce on the **standing dev stack**:
   `GET /api/v2/runtime/identity` on :8001 reports
   `git_commit: cc56544` — the checkpoint BEFORE the uncommitted AL-2.8
   work. That runtime has no finding_lexicon, no semantic stage, and no
   semantic-composer: a CJK follow-up there can only die as
   unresolved → loop BLOCKED `context_unresolved`, exactly as reported.
3. What the current code DID get wrong (and AL-2.9 fixes):
   a. Scenario B resolved via the **generic assist**, not the M2
      foundation (F7 had no authored concepts) — §7.
   b. Scenario B's plan was explain-only: the intent branches were
      `if/elif`, so the 明细 evidence half was dropped — §6.
   c. F7's explain answered "flagged by rule-based detection … evidence
      not available" — a false mechanism claim plus a dead end (RP has no
      trading-frequency rule; the finding's "why" lives in
      `feature_evidence.trade_frequency_24h`) — §9.

## 4. Conversation Context Contract

Made explicit and pinned by tests:

```
Conversation State (case_id, findings of the current case, focus)
        +
Current User Utterance
        ↓
ContextResolver (deterministic tiers → M2 semantic stage → assist)
```

The user never needs to repeat the case id. Both turn-1 phrasings
("展开一下00010的具体情况", "调查00010") leave the conversation bound to
U00010 and both follow-ups resolve identically (comparison test + CMP
browser scenario). The pre-existing focus-priority invariant is NOT
changed (§17.4).

## 5. Finding Resolution

Tier order unchanged; the M2 tier is the semantic authority:

```
explicit focus → case-level routes → title tokens
  → finding_lexicon (deterministic exact tier — canonical phrases only)
  → M2 SemanticNormalizer → governed grounding (UNIQUE/AMBIGUOUS/UNKNOWN)
  → Stage-1 generic assist (bounded) → unresolved
```

## 6. Intent Resolution

Finding and intent remain separate decisions; ONE request may now carry
BOTH intents. Planner branch 1a-2 composes them into one contract-checked
plan (still deterministic, still no LLM round-trip):

- explain only → `explain_signal, retrieve_policy` (unchanged)
- evidence only → stream step (unchanged)
- **both → `explain_signal` + stream step** (Scenario B:
  `explain_signal, inspect_transactions` with
  `{view: evidence, stream: transactions}` locked)

Shared intent vocabulary: `is_evidence_request` now also recognizes
「明细/明細」 — the user's actual Scenario B wording ("交易明细").
Deliberately narrow: English "details" is NOT added (vague explanatory
language must keep planning the explanation level). The goal layer's
heuristic recognition mirrors the same word (display-only). Limitation
recorded: a THIRD simultaneous intent still waits for the LLM planner
path; no planning framework was built.

## 7. M2 Semantic Foundation Reuse

- Seed v3 (`seed_version: 3`): **F7 minimal authoring** — core
  `HIGH_TRADING_FREQUENCY`, distinguishing `TRADING_FREQUENCY`,
  confusions `[F8, F2]` (already recorded), status **UNVERIFIED**,
  provenance from the live RP description + `trade_frequency_24h=54`.
  Same governance shape as AL-2.8's F3. M2 contract: not modified.
- **Disambiguation is structural**: `HIGH_TRADING_FREQUENCY` ∩
  `HIGH_WITHDRAWAL_FREQUENCY` = ∅ at the core-concept level, so 「高频交易」
  can only ground F7 and 「高频提现」 only F3 — decided by canonical
  concepts and grounding, never by title similarity. Control tests:
  API `test_withdrawal_wording_goes_to_f3_never_f7` +
  `test_f7_and_f3_core_concepts_are_disjoint`; live B/C runs on real RP.
- Resolution ≠ authorization: F7 grounds while UNVERIFIED; Safe-Select
  untouched (regression suite green).

## 8. Scenario A — result

Turn 1 → real intake (8 findings, bundle). Turn 2 「为什么说共享设备是风险项」
→ lexicon exact tier → F5 → `explain_signal, retrieve_policy` → real
tools → real graph answer (device_sharing cluster, 19 accounts, score 80,
DEV8Z1O4A35QU, related accounts, policy citations). Browser: live reply,
persisted across reload/reopen. (`为什么说**说**共享设备…` still contains
the lexicon phrase — the deterministic exact tier legitimately resolves
it; the semantic tier was separately proven for the lexicon-missing
paraphrase in AL-2.8 A′ and remains green.)

## 9. Scenario B — result

Turn 2 「为什么说高频交易是风险项，给我输出高频交易的具体交易明细」 →
`HIGH_TRADING_FREQUENCY` ← 「高频交易」→ F7 → plan
`explain_signal, inspect_transactions` → real tools:

- `signal_explain` → **Feature answer** (new bounded fallback): "This
  finding was flagged by behavioral risk-feature detection. The finding
  is based on the following Risk Platform risk features: Trade frequency
  (24h) = 54." — the honest "why", from RP `feature_evidence`, no rule
  fabricated. The fallback fires ONLY when no RP rule backs the finding
  (F3's richer rule answer is regression-tested to be unchanged) and
  reports only features that actually exist in the evidence.
- `finding_drilldown` → **64 real transaction records**
  (`transactions_included: true`).
- The wrong-finding guard is asserted everywhere: never
  `Withdrawal frequency (24h)…`, never the withdrawal stream.

## 10. Negative / Ambiguous Cases

- Unknown finding after case context (「谈谈虚拟币洗钱这个风险项」):
  bounded unresolved / clarification, **zero tools** (API + browser N).
- Shared-core concept → AMBIGUOUS with candidates, no mutation (AL-2.8
  suite, still green).
- No safety boundary was relaxed for the positive scenarios.

## 11. API E2E (`backend/tests/test_al29_conversational_followup_v1.py`, 10 tests)

Real HTTP `/turn` × two turns + real store + real repo seed v3 + real
planner contract gate + real tools with the RP boundary controlled;
normalizer scripted (deterministic; real-LLM validation is the browser
journey's job). Covers: A two-turn, B two-turn via M2 (dual intent, F7,
feature fact), CMP ("调查00010"), withdrawal→F3 control, disjoint-core
unit, negative zero-tools, 明细 intent unit, planner composition units,
Feature-fallback tool units. **All PASS.**

## 12. Browser E2E (`qa/journey_al29_conversational_followup.py`)

Real headless Chrome → :3011 → :8010 (current code) → real RP → real
LLM. Two-turn conversations in the real UI, one scenario per Chrome:

| Leg | Sequence | Oracle | Result |
|---|---|---|---|
| a | 展开一下00010… → 为什么说共享设备是风险项 | live F5 graph answer | **PASS** |
| b | 展开一下00010… → 高频交易+交易明细 | live answer: Trade frequency (24h) = 54 + real transaction records [T00…]; never withdrawal | **PASS** |
| cmp | 调查00010 → same follow-up | identical resolution | **PASS** |
| n | 展开一下00010… → 谈谈虚拟币洗钱… | bounded honest, no fabrication | **PASS** |
| persistence | reload + reopen (a, b) | responses retained | **PASS** |

## 13. Conversation Response

Both follow-up answers are natural language grounded in real RP data
(§8/§9) — never `signal_explain: success` / `Steps completed` as the
answer; finding identity is explicit (F5 graph data / F7 feature fact +
the requested stream).

## 14. Persistence

Reload + reopen verified for A and B in the browser; responses served
from the persisted investigation read model.

## 15. Previous Test Coverage Gap (the precise answer)

AL-2.8's browser E2E validated `raw NL → semantic resolver → finding` as
**independent one-turn scenarios on fresh investigations**. The boundary
it never crossed was:

```
previous conversation state (case established in turn 1)
        + raw NL follow-up (turn 2)
        → resolver → finding
```

executed against a REAL running stack. When the user then exercised the
two-turn journey, they did it on the standing dev stack, which runs
`cc56544` — a runtime where this entire layer does not exist yet; the
follow-up therefore died as `context_unresolved`. On current code the
same journey passes (API and browser, both /turn and /goal). AL-2.9 pins
the missing boundary with two-turn API tests and two-turn browser
journeys so it can no longer silently diverge. Environment note: the
standing stack (:3001→:8001) must be restarted onto the current working
tree to serve these scenarios (not done automatically — it is the
user's standing process; the isolated QA stack :3011→:8010 runs current
code).

**Correction (found during canonical validation)**: an earlier draft of
this section suggested :8001 used a different store than :8010. That was
wrong — `lsof` shows both processes open the SAME database
(`backend/data/investigation_agent_v2.db`; `_DEFAULT_DB` is derived from
`__file__` and is cwd-independent). Stale CODE was the only problem with
:8001, never the data.

## 16. Regression

- Batch (AL-2.8 + AL-2.9 + seed/coverage/semantic-resolution/signal/
  API/e2e/Safe-Select WI-5/6 + nokey + AL-1/2.6/2.7 + goal): **253
  PASS** after all AL-2.9 changes.
- Seed-shape tests updated to v3 (10 concepts; F7 UNVERIFIED;
  INCOMPLETE=3) — expectation updates for governed authoring, not
  behavior changes.
- Pre-existing inherited failures unchanged (test_error_handling ×4
  legacy, agent_loop re-plan ×2, tool_orchestration_final ×4 — documented
  in the AL-2.8 QA §17).

## 17. Known Limitations

1. A THIRD simultaneous intent still goes through the LLM planner path;
   only the explain+evidence composition is deterministic.
2. 「明细/明細」 is intentionally CJK-only in the evidence-intent
   vocabulary (English "details" stays explanatory to protect the P5
   wording contract).
3. F7 remains UNVERIFIED — Safe-Select ineligible by design pending
   independent verification.
4. Focus-priority invariant unchanged: an established focus still wins
   over a new NL reference inside one conversation (out of scope per the
   AL-2.9 brief §9; recorded as an open issue).
5. The standing dev stack must be restarted to run current code (§15).
6. Scenario B's evidence answer is bounded by the existing drilldown
   contract (complete record set, streams-labeled); no new evidence
   semantics were introduced.

## 18. Definition of Done

| Item | Status |
|---|---|
| case-level conversation context consumed by next-turn resolution | **DONE** (§3/§4, both paths) |
| 共享设备 follow-up → F5 | **DONE** |
| 高频交易 follow-up → High Trading Frequency | **DONE** (M2 grounding) |
| never High Withdrawal Frequency | **DONE** (disjoint cores + controls) |
| finding/intent separation; dual intent governed plan | **DONE** |
| correct governed tool path; real RP evidence | **DONE** |
| natural-language response; live; reload/reopen | **DONE** |
| unknown finding safe; ambiguity not guessed | **DONE** |
| Safe-Select unchanged; M2 contract unchanged | **DONE** |
| regression accounted | **DONE** (§16) |
| API E2E + Browser E2E + two-turn journey + negative + comparison | **DONE** |

## 19. Final Status

**PASS** — confirmed on the canonical runtime (addendum below).

---

## Addendum — Canonical Runtime Validation (2026-09-30)

With user authorization, the canonical stack (:3001 → :8001) was
restarted onto the current working tree (pre-restart identity:
`git_commit: cc56544` — see the AL-2.8 addendum for the identity/
fingerprint caveats) and the acceptance was re-run there:

- **Canonical API smoke** (:8001, real LLM + real RP): A two-turn
  follow-up → F5 graph answer ✓; B two-turn follow-up →
  `HIGH_TRADING_FREQUENCY` → F7 → `explain_signal + inspect_transactions`
  → "Trade frequency (24h) = 54" + real transaction records ✓;
  negative → bounded honest unresolved, zero tools ✓ (one provider
  hiccup during validation produced the honest
  `semantic_resolution_unavailable`, which succeeded on retry — the
  designed behavior, exercised on the canonical stack too).
- **Canonical browser journeys** (`FRONTEND_BASE=http://localhost:3001/`):
  al29 a / b / cmp / n — **4/4 PASS** (two-turn conversations in the
  real UI, reload/reopen persistence for a/b); al28 a / a2 / b / n —
  **4/4 PASS**.
- Journey scripts parameterized via `FRONTEND_BASE` (QA-only change);
  the isolated QA stack (:8010/:8011/:3011) was torn down after the
  canonical run. The canonical-runtime rule is codified in CLAUDE.md
  ("Canonical runtime").
