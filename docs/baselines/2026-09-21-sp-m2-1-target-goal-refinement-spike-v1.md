# SP-M2-1 Spike Result: Goal-Aware Target Refinement

**Status**: ~~IMPLEMENTED · VALIDATED (stability + browser) · decision:
H1 partially supported → proceed to WP-M2-1 with contract revisions~~
**SUPERSEDED 2026-09-24 — see Final Discovery Status below.**
**Date**: 2026-09-21
**Parent**: `2026-09-21-target-goal-refinement-and-m2-rescope-v1.md` (§10 SP-M2-1)
**Boundary doc**: `2026-09-19-agent-qa-boundaries-v1.md` (B8/B9 context)
**Closing evidence**:
`docs/qa/2026-09-23-sp-m2-1-independent-qa-rerun-v1.md` (D1–D5) ·
`docs/qa/2026-09-24-sp-m2-1r-developer-validation-v1.md` (runtime wiring) ·
`docs/qa/2026-09-24-sp-m2-1-final-independent-qa-v1.md` (final acceptance QA)

---

## Final Discovery Status (2026-09-24)

> ### Discovery Complete — Current Contract Rejected
>
> **"Rejected" means**: the current refinement contract / Safe Select
> Policy ("confidence ≥ threshold → SELECT") is insufficient as the
> implementation baseline for WP-M2-1.
>
> **It does NOT mean**: the Goal-aware Refinement direction is
> disproven · target↔goal refinement is not worth studying · M2 failed ·
> the existing implementation must be deleted.

### A. Proven to have value

- Semantically clear multi-candidate requests are correctly resolved:
  E3 → F6 5/5 SELECT with consistent goal-grounded rationale; E1/E2/E4
  → F6 supported; E5 → F8 at 0.90 (final QA, live RP, real browser).
- Goal information provides real disambiguation information gain over
  candidate-count-only clarification.
- Runtime integration is real and proven: flag-controlled `/turn`
  execution with runtime-identity evidence (SP-M2-1R).
- The bounded mechanism set works: deterministic fast path untouched,
  candidate-ID enforcement, single-attempt bound, malformed-output
  safe fallback (exercised live), no CoT/prompt exposure.

### B. Proven to be problems

1. The confidence threshold alone is NOT a safe SELECT condition:
   unsafe SELECTs were accepted at 0.72–0.78 while safe SELECTs ranged
   0.75–0.90 — the threshold does not separate the two classes.
2. Genuine ambiguity produces unsafe forced SELECT: on the
   deliberately broad request ("这个账户有哪些关联情况？"), 2 unsafe
   SELECTs were observed in this test sample's 5 repeated runs
   (observed unsafe-selection rate in this sample = 2/5; a sample-level
   observation, not a system error rate).
3. Upstream target-resolution path variability exists (E5: Stage-1
   sometimes self-resolves, sometimes retains candidates, once returned
   UNRESOLVED) — refinement stability cannot be attributed to
   refinement alone.
4. "confidence ≥ 0.7" is not demonstrated to mean "the user's goal
   uniquely supports this target" — the model's preference was accepted
   as identification without user-evidence-backed separability.

### C. Not yet proven (open questions → SP-M2-1C)

- Which conditions reliably define candidate separability.
- How to distinguish "model preference" from "user-evidence support".
- Whether a candidate confidence/margin variable is informative.
- Whether evidence-of-discrimination or a second independent judgment
  is needed.
- Whether a system rule should block threshold-marginal SELECTs.
- How to make genuine ambiguity STABLY resolve to CLARIFY.

Successor spike: `2026-09-24-sp-m2-1c-candidate-separability-v1.md`.
The original §10 decision below is retained as history; its
"proceed to WP-M2-1" clause is superseded by this status.

---

## 1. Hypothesis (H1)

When initial target resolution produces multiple plausible candidate
findings, goal understanding can use the user's complete request plus
those candidates to uniquely resolve the target with high confidence.

## 2. Experimental design

- Feature flag `ENABLE_TARGET_GOAL_REFINEMENT` (default **False** —
  current behavior preserved unless explicitly enabled).
- Bounded refinement module `app/target_refinement.py`:
  structured contract (`SELECT` | `CLARIFY` + `target_id` + `confidence`
  0.0–1.0 + user-safe `reason`), ONE attempt, candidate-set membership
  enforced, fabricated ids rejected, system-enforced confidence
  threshold (`TARGET_REFINEMENT_MIN_CONFIDENCE = 0.7`).
- Service integration: in `run_turn`, an AMBIGUOUS resolution with
  retained candidates and the flag on runs ONE refinement attempt; a
  high-confidence SELECT upgrades the resolution to RESOLVED-INFERRED;
  otherwise the existing clarification path fires unchanged.
- The refinement receives: full user request, retained candidates
  (id/title/description from authoritative case context), and the
  Stage 2.1 GoalRepresentation projection. No unrestricted case data.

## 3. Implementation boundary

- `app/target_refinement.py` (new, spike) — bounded contract + LLM call.
- `app/config.py` — flag + threshold (system-controlled).
- `app/investigation_service.py` — refinement hook on the AMBIGUOUS
  branch (flag-gated, injectable `refinement_resolver`).
- NOT changed: Agent Loop, PlannerV2, domain tools, frontend,
  docs/architecture, existing routing guards.

## 4. Test matrix and results

### LEVEL 1 — contract unit tests (8/8 pass)

- SELECT valid candidate → correct id/confidence
- Fabricated target id → CLARIFY (never a fabricated resolution)
- Confidence below threshold → CLARIFY
- Unknown decision / malformed output → CLARIFY
- Reason bounded ≤200 chars
- Prompt carries candidates + goal + request fencing; system prompt
  says "Never invent ids"

### Stability (Layer B, real LLM + real RP, 3 runs per scenario)

| Scenario | Runs | SELECT | CLARIFY | Stable? |
|---|---|---|---|---|
| Ambiguous two-candidate set (Trading Ratio vs Opposite Trade Ratio) | 3 | 2 | 1 | **NO — model variance confirmed** |
| E3 "共享了设备" (single clear target F5) | 3 | 3 | 0 | yes |

The ambiguous-set instability is the H1 risk made concrete: the model
sometimes SELECTs one of two equally plausible candidates. The system
enforced boundary (CLARIFY when evidence is insufficient) held in every
run — no fabricated or forced selection.

### Browser acceptance (real UI, RP up, real LLM)

| Scenario | Result |
|---|---|
| Primary: "Opposite Trade Ratio展开细说一下具体情况" (no selection, case 00010) | **PASS** — no unresolved_reference; focus = F8 (the Opposite Trade Ratio finding); bounded evidence view rendered |
| Traditional CJK: "具體展開一下Opposite Trade Ratio的證據" | **PASS** — same behavior |
| E3: "有哪些账户和这个账户共享了设备" | bounded clarification (F5/F6 listed) — correct: the goal layer did not uniquely disambiguate, and the system did not force a pick |

## 5. Repeated-run stability results

| Scenario | SELECT | CLARIFY | Stable? |
|---|---|---|---|
| E3 shared-devices (Layer B) | 3/3 | 0 | ✓ |
| E5 hedging (Layer B) | 3/3 | 0 | ✓ |
| Ambiguous two-candidate set (Layer B) | 2/3 | 1/3 | **✗ — documented instability** |

## 6. Correct resolutions

E3 (shared-devices → F5), E5 (hedging → F8/Opposite Trade Ratio), and
E6 (exact title → F6 via the unchanged deterministic fast path) all
resolved correctly through the refinement pipeline where the LLM was
able to use the goal + request semantics.

## 7. Safe clarifications

All low-confidence, ambiguous, and unmatched scenarios degraded to
CLARIFY — never an arbitrary selection. The confidence threshold was
enforced by the system, not the LLM.

## 8. Incorrect resolutions

One observed instability: the ambiguous two-candidate set produced
SELECT in some runs and CLARIFY in others. The system enforced boundary
(CLARIFY when the threshold is not met by a unique candidate) held in
every run — no fabricated or forced selection.

## 9. Root causes of instability

The model treats shared-noun candidates (Trading Ratio vs Opposite
Trade Ratio) as sometimes-distinguishable and sometimes-not, depending
on subtle prompt interpretation. This is real-LLM variance; the
system's threshold boundary prevented it from becoming an incorrect
resolution.

## 10. Decision

> **2026-09-24: superseded by Final Discovery Status above.**
> The revision this section called for was confirmed necessary by two
> independent QA rounds; the contract revision was never made, and the
> original contract is now formally rejected as a WP-M2-1 baseline.

**B. H1 partially supported → revise the refinement contract before
proceeding to formalize WP-M2-1.**

What worked: candidate-set restriction, system-enforced confidence,
goal-aware disambiguation on clear cases, bounded CLARIFY fallback.
What needs revision before WP-M2-1: the contract for genuinely
ambiguous candidate sets (the model must be steered toward CLARIFY when
candidates are equally plausible; the current single-call design cannot
guarantee this from the LLM alone). This is a bounded prompt/contract
revision inside the existing spike boundary — not a redesign.
