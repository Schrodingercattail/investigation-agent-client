# AL-2.11 — Remove Legacy Finding Lexicon; Unified Finding Semantic Resolution — QA v1

**Date**: 2026-10-02
**Status**: **PASS** (canonical runtime)
**Scope**: architecture convergence — `finding_lexicon` deleted;
`SemanticNormalizer → M2 Concept Registry → Candidate Metadata →
deterministic grounding` is the ONLY conversational finding-semantic
entry. No new agent capability. Safe-Select / M2 contract /
ClarificationLedger / RP / case-level capability routing: untouched.

---

## 1. Objective

Remove the last parallel semantic authority. Final flow:

```
User NL → SemanticNormalizer → M2 Concept Registry → Candidate Metadata
→ deterministic grounding → UNIQUE/AMBIGUOUS/UNKNOWN → Finding Target
→ Intent → Planner → Tool
```

## 2. Migration Decision

**YES WITH CONDITIONS** (migration audit, prior work item): the
lexicon's two entries were grounding hints whose knowledge the governed
concepts already carry (proven live before deletion); its matching
mechanism was an implementation workaround; its deterministic-exact
tier created a two-tier semantic inconsistency (same finding class
resolved by LLM or not, depending on phrasing). Conditions accepted:
CJK finding references now REQUIRE the LLM semantic path; no-LLM
finding references degrade honestly (§8); no language_variants schema
was added (§15 Open Issue).

## 3. Legacy Lexicon Inventory (deleted)

`backend/app/finding_lexicon.py` — untracked (never committed; created
in the pre-AL-2.8 session as the CJK stopgap), 2 entries:
「共享设备」→ title tokens {shared, device} (F5);
「高频提现」→ {high, withdrawal} (F3).
Runtime consumer: exactly one (`context_resolution._alias_findings`).
Classification: entries = genuine user vocabulary already carried by M2
concepts (class A+D); mechanism = pure workaround (B).

## 4. Target M2 Architecture (unchanged from AL-2.8/2.9)

LLM proposes ONE governed concept + verbatim fragment → system validates
registry conformance + fragment → system grounds against CURRENT-case
candidate `core_concepts` (DEPRECATED excluded; status = provenance,
never a gate) → UNIQUE/AMBIGUOUS/UNKNOWN. LLM never selects a finding.

One prompt refinement (minimal, shared normalizer system prompt):
"prefer the concept the finding is CENTRALLY about — not a distinguishing
signal, not a derivation mechanism". Rationale: live validation caught
the real model proposing F5's DISTINGUISHING concept (`SHARED_DEVICE`)
for 「共享设备」→ core-only grounding correctly missed → bounded
ambiguity via the assist. The predicate is untouched (still
core-only, for resolution AND Safe-Select); only proposal quality
improved. WI-4 suite green.

## 5. Finding Resolution Flow (post-removal)

deterministic tiers: explicit focus → case-level capability routes
(trade / related-accounts / intake — capability routing, NOT semantic
authority; untouched) → **English title-token matching (kept; matches
the finding's OWN authoritative title only — never extended, not a
semantic authority)** → **M2 semantic stage (the single NL finding
entry)** → Stage-1 bounded assist → unresolved.

## 6. F5 / F3 / F7 — SAME-PATH PROOF (real LLM, canonical :8001)

Every turn below recorded `target_resolution.semantic_grounding =
{stage: semantic_normalizer, status: OK, concept, fragment, grounded}`:

| Scenario | Fragment | Concept | → Finding | Plan |
|---|---|---|---|---|
| 为什么共享设备是风险项 | 共享设备 | SHARED_DEVICE_RELATIONSHIP | **F5** | explain_signal + retrieve_policy |
| 高频提现有哪些证据 | 高频提现 | HIGH_WITHDRAWAL_FREQUENCY | **F3** | inspect_withdrawals |
| 为什么说高频交易是风险项，给我输出高频交易的具体交易明细 | 高频交易 | HIGH_TRADING_FREQUENCY | **F7** | explain_signal + inspect_transactions (64 real transaction records) |

F3≠F7 on the unified path: disjoint cores asserted in unit tests AND
exercised live (B→F3, C→F7; never crossed). Deterministic scripted
variants: `test_al211_unified_semantic_entry_v1.py` parametrized
same-path proof with `script.calls == 1` per resolution.

## 7. Lexicon Removal (verified)

- `backend/app/finding_lexicon.py` **deleted**.
- `context_resolution`: import, `_alias_findings`, and the alias tier
  removed; the English title-token matcher stays with an explicit
  non-authority boundary comment.
- Static/production checks pinned by tests
  (`test_al211_unified_semantic_entry_v1.py`): module absent; zero
  `finding_lexicon` / `_alias_findings` / `alias_matches` references in
  `app/**.py`; no-key finding request → honest unavailable with NO
  phrase-table fallback; normalizer proposing nothing leaves even the
  historical phrases unresolved (proving resolution came only from the
  governed proposal).

## 8. LLM Unavailable Behavior

- Finding NL request with unavailable provider →
  `SEMANTIC_RESOLUTION_UNAVAILABLE` + honest user message + zero tool
  calls (browser scenario 4, no-key stack; screenshot al211-04).
- Deterministic CASE-LEVEL capability routes keep working without an
  LLM — same browser session: related-accounts answered from real RP
  attribution. Capability routing ≠ finding semantic fallback.
- Taxonomy unchanged from AL-2.10: only genuine unavailability produces
  the outage state; no-text/malformed/no-concept are resolution
  failures (fall-through), never outages.

## 9. UNKNOWN / AMBIGUOUS boundaries

Held: no governed concept → fall-through → bounded assist/unresolved;
multi-candidate core hit → AMBIGUOUS clarification (context unmutated);
DEPRECATED excluded; INCOMPLETE/UNVERIFIED candidates remain
conversationally reachable and Safe-Select-ineligible (F3/F7 UNVERIFIED
ground live).

## 10. API E2E

- `test_al211_unified_semantic_entry_v1.py` (8): deleted-path checks +
  parametrized F3/F5/F7 same-path proof (provenance + single proposal
  consumption) + F3≠F7 + no-fallback + no-hidden-mapping.
- Migrated suites all green: al28 foundation (precedence test inverted
  to single-entry proof), al28 finding-level NL (identification tests
  rewritten onto the M2 path), al29 (A scenario supplied the normalizer
  script — no lexicon tier anymore), al210 (differential A premise
  inverted: scenario A now REQUIRES and records the semantic stage).

## 11. Real LLM Validation

Canonical :8001 (identity re-verified post-restart), three fresh
conversations: A/B/C above — all grounded by the real normalizer with
recorded provenance; responses natural-language on real RP data
(64-record evidence in C). No scripted resolver, no pre-resolved
finding, no test-only ids.

## 12. Browser E2E (canonical :3001 → :8001)

Scenarios 1/2/3 **PASS** (screenshots al211-01..03; reload/reopen
persistence verified per scenario; F7 never F3 in scenario 3).
Scenario 4 (temporary no-key stack :3011→:8012, torn down after):
**PASS** — honest unavailable card, zero fabrication, and the
deterministic capability route answered in the same session
(screenshot al211-04).

## 13. Deleted-Path Verification

See §7 — enforced by tests, not by assertion in this document.

## 14. Regression

Batch after the removal (21 suites: AL-2.8/2.9/2.10 + case-level
routing + related-account + cross-account + Safe-Select WI-5/6 +
M2 WI-2/3/4 + Clarification + conversation persistence + no-key
startup + API/investigation E2E): **251 passed, 0 failed.**
(Migration-test rewrites are contract inversions required by the
removal, listed in §10.)

## 15. Known Limitations / Open Issues

1. **Open Issue — language_variants**: not implemented (contract
   OPTIONAL; no schema added). Finding NL understanding now always
   requires the LLM; if deterministic CJK exact phrases are ever needed
   again, implement the OPTIONAL hints then. NOT a synonym dictionary.
2. No-LLM finding references degrade to the honest unavailable state
   (accepted product decision; explicit selection and case-level
   capabilities remain fully available).
3. One extra LLM round-trip per NL finding reference (latency/cost;
   provider weather can degrade individual turns honestly).
4. Normalizer prompt now steers toward core concepts; a degenerate
   proposal can still fall through to the bounded assist (by design).

## 16. Definition of Done

All boxes satisfied: lexicon deleted with zero production references;
SemanticNormalizer the single finding-semantic entry; M2 registry/
metadata the semantic authority/grounding source; F5/F3/F7 real-LLM
paths with recorded provenance; F3≠F7 tested on the unified path;
UNKNOWN/AMBIGUOUS held; Safe-Select/SupportEvaluator/SafeSelectGate/
ClarificationLedger/case-level routing untouched (regression batch
green); unavailable behavior honest and non-fallback; API+Browser+real
LLM+real RP+canonical runtime+reload/reopen all verified.

## 17. Final Status

**PASS** — "F5 / F3 / F7 conversational finding resolution now all flow
through the same M2-grounded SemanticNormalizer path", with recorded
provenance per resolution and no hidden deterministic finding mapping.
