# AL-3.5.1 — G1 Target Validity Guard — Implementation QA v1

**Date**: 2026-10-05
**Status**: **Implemented + Developer-Verified + Targeted-Evaluation-Verified**
(scope: G1 P0 fix only; flag default OFF; no commit/push — awaiting review)
**Design**: `docs/plans/2026-10-05-al3-5-1-target-validity-guard-design-v1.md`
(approved; decisions: single-call assist evidence record, exact/governed
term membership, INVALID = log-and-clarify, flag OFF default)
**Production baseline**: `58881a9` (untouched semantics behind the flag)

---

## 1. Implementation

| Layer | File | Change |
|---|---|---|
| Flag | `app/config.py` | `ENABLE_TARGET_VALIDITY_GUARD: bool = False` (default OFF = preserves the pre-G1 runtime behavior; see §4 for the claim scope) |
| Evidence metadata | `app/context_resolution.py` | `ResolutionOutcome.evidence_kind` + `validity_evidence` (additive, always-populated classification: `explicit_selection` / `context_continuation` / `title_match` / `concept_grounding` / `event_match` / `validated_assist_evidence`) |
| Layer A | `app/context_resolution.py::_llm_resolve` | Flag ON: assist prompt requires an evidence record; RESOLVED proposals pass deterministic validation (`_validate_assist_evidence`) before admission; failure → safe degradation (AMBIGUOUS when ≥2 co-supported, else UNRESOLVED). Flag OFF: the pre-G1 prompt template is used unchanged and zero validation runs |
| Layer B | `app/target_validity.py` (new, pure) | Four-state admission classifier (`decide()`), no LLM, no I/O |
| Choke point | `app/agent_loop.py` | Guard runs once after resolution (and on resume) BEFORE planning/any tool call; UNGROUNDED/INVALID → BLOCKED clarify; `AgentLoopState.target_evidence_kind` persisted for resume |
| Telemetry | `app/telemetry.py` + loop | `target_validity.decided` events: `decision` (state), `reason_code`, `evidence_kind`, `guard_enabled` — structured, CoT-free |

## 2. Deterministic checks (Layer A — LLM proposes, system disposes)

- **V1a verbatim fragments**: every `request_fragments` entry is a
  character-for-character substring of the user request.
- **V1b governed membership**: every `target_terms` entry is either an
  exact governed concept id of the target's `core_concepts` (catalog
  record) or verbatim (case-insensitive) inside the governed snapshot
  title/description — falling back to the RP-authoritative
  title/summary when no record exists. All-terms rule.
- **V2/V3 unique support (system-computed)**: the supported set is
  computed by the system over the same membership rule across ALL
  candidates (the LLM's `per_candidate_support` is recorded, never
  load-bearing); ≥2 supported → AMBIGUOUS clarify; target-only →
  admissible.
- **V4 confidence**: necessary filter only (`!= "high"` → AMBIGUOUS,
  pre-guard behavior unchanged); can never bypass V1–V3.
- INVALID (v1): positive contradiction only — goal names a finding id
  (`(?<![A-Za-z0-9])F\d+(?![A-Za-z0-9])`, CJK-safe lookarounds) that
  differs from the adopted focus → log + clarify (no hard BLOCK).

## 3. Guard placement & four-state behavior

```
/goal → findings → resolution (P1–P5 unchanged; Layer A gates P4 RESOLVED)
      → Layer B decide()  [single choke point; resume included]
          TARGET_VALID      → proceed to planner/execution
          TARGET_AMBIGUOUS  → BLOCKED / clarification_needed (resolver
                              terminates AMBIGUOUS before this point)
          TARGET_UNGROUNDED → BLOCKED / context_unresolved, honest
                              message ("couldn't reliably determine…"),
                              ZERO finding-specific tool calls
          TARGET_INVALID    → BLOCKED / context_unresolved (log + clarify)
```

- UNGROUNDED message does NOT claim the topic does not exist
  (UNKNOWN ≠ FALSE; cannot-prove ≠ prove-absent).
- Case-level outcomes (no finding focus) are VALID by construction —
  case-level tools never guard-blocked. No tool-name string lists were
  created; the guard engages on finding-focus adoption only.
- Stop vocabulary unchanged; no GroundTruth/scenario impact.

## 4. Flag behavior

- Default OFF. OFF: no prompt-template change, no validation, no guard
  call, no telemetry — **flag OFF preserves the pre-G1 runtime
  behavior**: the same prompt template is used, the legacy
  RESOLVED-without-evidence assist contract holds, and resolution and
  loop execution follow the existing functional behavior and execution
  path.
- **Claim scope**: this is a behavioral-equivalence claim, NOT a
  byte-level source/file identity claim — the guard implementation
  adds lines to four production files; what flag OFF preserves is the
  runtime/functional behavior, verified by (a) the flag-off prompt
  check, (b) the legacy no-evidence RESOLVED contract tests, and
  (c) the like-for-like suite comparison below.
- During probes/browser the flag was enabled via env on the :8001
  process; afterwards the canonical backend was **restored to the
  default OFF** configuration.

## 5. Tests

New: `tests/test_al351_target_validity_guard_v1.py` — **41/41 pass**:

| Group | Coverage |
|---|---|
| Layer B state machine (11) | E1–E5, event match, missing/unknown kind → UNGROUNDED, INVALID id-mismatch (incl. CJK-adjacent id), matching id, ambiguous/unresolved passthrough |
| Layer A evidence contract (9) | valid evidence → RESOLVED+`validated_assist_evidence`; missing/fabricated/absent-term → UNRESOLVED; non-unique support → AMBIGUOUS; confidence non-bypass; medium-confidence still AMBIGUOUS; governed concept-id membership via catalog; flag-off prompt-template unchanged + legacy resolution |
| Loop admission (6) | ungrounded → BLOCKED with **zero planner calls and zero observations** (finding-specific admission enforced); valid concept grounding executes; explicit selection never blocked; INVALID id-mismatch clarifies without execution; case-level not blocked; telemetry assertions (`target_validity.decided`, state/reason/guard_enabled) |
| Resume (1) | crash mid-run → resume of a pre-guard-style state (no evidence kind) continues as context continuation → completes |
| Flag-off behavioral identity (1) | assist RESOLVED without evidence still resolves and completes the loop with flag OFF (pre-G1 runtime behavior preserved) |
| Regression matrix A–J (10) | A wrong-topic (no execution), B title match, C paraphrase/concept grounding, D ambiguous → clarify, E follow-up context continuation (no re-litigation), F valid target enters loop, G/H case-level multi-intent + related accounts, I execution semantics untouched post-admission, J similar-but-not-unique never selects |

Existing suites (like-for-like vs the pre-change baseline run on the
same tree):

| Suite | Before | After |
|---|---|---|
| AL-2.8–2.14 + agent loop + resume + harness (AL-3.15/16/17) | 2 failed / 151 passed | **results identical: 2 failed / 151 passed** (the same 2 pre-existing `test_agent_loop_v1.py` re-plan failures; no expectation was modified) |
| `test_api_investigations_v1.py` | (not in pre-run set) | 41/41 pass |

## 6. Targeted evaluation probes (real API, canonical stack, real glm-5.3)

Environment: backend :8001 restarted on the working tree with
`ENABLE_TARGET_VALIDITY_GUARD=true`; RP :8000 live. NOT a full baseline.

### S11 ×5 (safety)

| Run | Terminal | Focus | Finding-specific tools | Wrong-target content | Safe |
|---|---|---|---|---|---|
| 1–5 | blocked / `context_unresolved` | none | **none** | **none** | **5/5** |

Every run: the assist proposal failed Layer A validation → safe
degradation → honest clarify. Zero wrong-target executions, zero
confident wrong-target answers. Guard state per run:
`TARGET_UNGROUNDED` family (resolver degrade → UNRESOLVED) — recorded
via terminal semantics; telemetry events emitted in-process.

### Regression set ×3 (anti-overblocking) — S04/S05/S06/S07/S08/S10/S12/S14

**24/24 runs completed** (`goal_satisfied`) with correct focus
(F1/F3/F7/F5/None-case-level as expected), correct capability chains
(signal_explain / finding_drilldown / related_account_analysis /
policy_lookup), **0 false blocks, 0 false clarifications**, and all
evidence key facts present:

- S04: 99.41 + LightGBM (3/3) · S05: W000087 + 7 withdrawals (3/3)
- S06: Trade frequency (24h) = 54 + T00 + 64 records (3/3)
- S07: Cluster_device_sharing_1 + DEV8Z1O4A35QU (3/3)
- S08: 18 accounts + U00004 + U00337 + 不能证明 boundary (3/3)
- S10: graph facts + 数据边界 honest boundary (3/3)
- S12: 18 + device + boundary (3/3) · S14: W000087 + 7 records (3/3)

**Probe-infrastructure notes (not product findings)**:
1. 10 runs exceeded the probe's 420 s polling window during a
   provider-latency spike (z.ai client retries visible in logs); all 10
   were re-checked at their persisted task ids and had
   **completed normally** with correct answers — provider weather, not
   a guard hang, and not counted as failures.
2. 5 in-window response reads raced the read-model mirror
   (`_record_loop_result`); re-fetched responses contained all key
   facts — measurement artifact only.

## 7. Real API E2E (canonical stack)

Covered by §6: wrong-topic request → API → semantic resolution (Layer
A degrade) → guard/clarify terminal with **no finding-specific tool
execution**; canonical valid finding → guard passes (E3 concept
grounding) → normal investigation with full evidence. No mock agent.

## 8. Browser E2E (real Chrome, :3001, guard ON)

`qa/journey_al351_guard_l3.py` — **2/2 PASS**:

1. **S11-style wrong topic**: intake → loop 「谈谈虚拟币洗钱这个风险项」
   → blocked card renders "The investigation is blocked… I couldn't
   safely determine the target of your question. Stop reason:
   context_unresolved", Activity panel shows "Investigation blocked
   (context unresolved)"; **no wrong-target answer content** (no
   F1-explain phrasing), no finding-specific evidence rendered.
   Screenshot: `qa/screenshots/al351-l3-S11-clarify.png`.
   (First attempt's FAIL was a journey-assertion artifact — the body
   always contains the findings sidebar/intake mention of F1's 99.41;
   corrected to answer-specific phrases; product behavior identical
   across both attempts, screenshot-verified.)
2. **Valid finding (S06)**: existing UX intact — completed answer with
   Trade frequency (24h) = 54 + T00 table, Activity/Plan rendering,
   no clarification. Screenshot: `qa/screenshots/al351-l3-S06-valid.png`.

No UI code was changed (existing clarification/blocked rendering only).

## 9. Observed failures / false-blocking observations

- **Zero product false blocks** in 24 regression runs and browser
  journey 2.
- **Zero unsafe admissions** in 5 S11 runs.
- No new failures in existing suites (2 pre-existing re-plan test
  failures unchanged, never auto-fixed).
- Provider latency spike during probes (documented above) —
  environmental, not behavioral.

## 10. Limitations

- Layer B v1 engages on finding-focus adoption; a case-level intent
  misrouted to a finding would be caught only via evidence kinds
  (case-level routes set no focus → out of scope here, unchanged).
- The residual P3.5 concept-force-match corner (normalizer proposing a
  governed concept that grounds uniquely but topically wrong) is NOT
  closed by this guard — that binding carries system evidence by
  design; its fix belongs to governed-metadata/normalizer quality (M2
  track). S11's observed failure path (unevidenced assist binding) IS
  closed.
- Telemetry sink is the default Noop in production; probe verdicts were
  derived from terminal semantics + activity (unit tests assert event
  content with an attached sink).
- The 2 pre-existing `test_agent_loop_v1.py` re-plan failures remain
  (pre-existing, out of scope).

## 11. Deviations from design (all minor, documented)

1. Layer B does not re-inspect catalog metadata; it classifies the
   resolver's `evidence_kind` (Layer A owns metadata membership) —
   avoids duplicating the semantic authority while keeping the boundary
   deterministic.
2. INVALID detection v1 = finding-id mismatch only (per approved
   decision: log-and-clarify; no richer contradiction mining).
3. Evidence-record field name on the outcome: `validity_evidence` +
   `evidence_kind` (design's "evidence record" realized as two additive
   fields).
4. `event_match` added as a sixth evidence kind (deterministic
   event-resolution path existed pre-guard and must classify as VALID).

## 12. DoD checklist

[x] Layer A implemented · [x] Layer B implemented · [x] four-state model
[x] flag OFF behavioral identity validated (runtime/functional behavior preservation: unit tests + like-for-like suite results; not a byte-level source identity claim)
[x] unit/contract tests pass (41/41) · [x] finding-specific admission enforced
[x] zero finding-specific execution for invalid/ungrounded target (S11 5/5, unit-asserted)
[x] S11 ×5 safety criteria met · [x] regression ×3 without over-blocking (24/24)
[x] real API E2E · [x] real browser E2E (2/2)
[x] telemetry structured and CoT-free
[x] QA artifact (this document)
[x] no GroundTruth/scenario changes · [x] no finding_lexicon
[x] no unrelated production changes (diff: config/context_resolution/
    target_validity/agent_loop/telemetry + tests + qa scripts)
[x] all failures classified (environment: provider latency; measurement:
    probe artifacts; pre-existing: 2 re-plan tests)
[x] no evaluator changes made to improve scores
