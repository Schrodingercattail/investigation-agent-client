# SP-M2-1C — Candidate Separability / Safe Select Boundary

**Status**: **DISCOVERY COMPLETE — Outcome A candidate** (experiments
executed 2026-09-24; boundary definition produced with evidence; PMO
decision required before any contract design; NO production code was
written or changed in this spike)
**Date**: 2026-09-24
**Type**: Discovery Spike (M2 architecture-validation track)
**Predecessor**: SP-M2-1 — **Discovery Complete / Current Contract
Rejected** (`2026-09-21-sp-m2-1-target-goal-refinement-spike-v1.md`,
Final Discovery Status)
**Evidence base**:
`docs/qa/2026-09-23-sp-m2-1-independent-qa-rerun-v1.md` ·
`docs/qa/2026-09-24-sp-m2-1r-developer-validation-v1.md` ·
`docs/qa/2026-09-24-sp-m2-1-final-independent-qa-v1.md`
**Risk register**: `2026-09-21-target-goal-refinement-and-m2-rescope-v1.md`
§5 **R7** (this spike is R7's research owner and the gate for WP-M2-1)

---

## 1. Why this spike exists (one paragraph)

SP-M2-1 proved Goal-aware Target Refinement has real value on
semantically distinguishable multi-candidate requests, and its bounded
mechanics (wiring, fast-path isolation, candidate-ID enforcement,
single attempt, safe fallback) all work. It also proved the current
Safe Select Policy is unsafe: on a genuinely ambiguous request the
model's slight preference (0.72–0.78) crossed the 0.7 threshold and
became a definitive SELECT in 2 of 5 runs in the final QA sample —
while supported SELECTs ranged 0.75–0.90. Confidence alone therefore
does not discriminate safe from unsafe selection. Before any WP-M2-1
implementation, the system needs a definition of **when SELECT is
admissible at all**. That definition is this spike's research object.

## 2. The single research question

> When multiple plausible candidate targets exist, what evidence and
> system conditions are sufficient to admit a Refinement SELECT —
> and in which situations must the system CLARIFY?

Explicitly NOT researched here: new capabilities, new tools, planner or
Agent-Loop redesign, UI, MCP, memory, multi-agent, new network tools,
large-scale prompt engineering.

## 3. Research model — a bounded decision boundary

```
User Request
  + Goal Representation (existing, frozen semantics)
  + Candidate Set (authoritative, from initial Target Resolution)
        ↓
Candidate Separability Assessment     ← the object of study
        ↓
┌─────────────────────────────────────────────┐
│ sufficiently separable → SELECT admissible  │
└─────────────────────────────────────────────┘
        otherwise
┌─────────────────────────────────────────────┐
│ not sufficiently separable → CLARIFY        │
└─────────────────────────────────────────────┘
```

Boundary conditions on the model itself:

- The assessment is a **bounded decision boundary**, not a new
  free-form generative agent. It observes; it does not explore.
- It may only ever select from the authoritative candidate set
  (unchanged from SP-M2-1's frozen boundaries: candidate-ID
  enforcement, single attempt, no tools, no recursion, system-enforced
  outcomes).
- Whether the assessment is implemented as LLM judgment, system rules,
  or a hybrid is an OUTPUT of this spike, not an input assumption.

## 4. Concepts that must stay separate (naming discipline)

| Concept | Definition | Failure if confused |
|---|---|---|
| **Candidate relevance** | the candidate is related to the request at all | relevance ≠ uniqueness; relevant candidates can still be inseparable |
| **Candidate preference** | the model rates one candidate higher | a preference is not a justification; AMB r2/r3 showed preference becoming "identification" |
| **Candidate separability** | the user's request provides enough information that one candidate is **clearly** better than the others | this — and only this — admits SELECT |

Formal distinctions carried from R7:

- **Candidate preference ≠ Candidate separability**
- **Confidence ≠ Candidate separability**
- **Candidate count > 1 ≠ User intent ambiguity**

Working definition this spike will refine (not finalize): a candidate
set is *sufficiently separable* when the request, under its expressed
goal, contains a discriminating signal that maps to exactly one
candidate and against the others — and that mapping survives
restatement (paraphrase, CJK variant) and run repetition.

## 5. Discovery matrix — variables under study

| Var | Name | Question | Notes |
|---|---|---|---|
| A | Goal specificity | does the request carry goal information that distinguishes candidates ("共享设备" vs "关联情况")? | primary candidate for the boundary signal |
| B | Candidate semantic overlap | do candidate titles/descriptions overlap heavily? | measured on live RP descriptions, not vibes |
| C | Relative evidence | does one candidate hold discriminating evidence the others lack (e.g. F6 enumerates 18 accounts; F5 names the mechanism)? | content-analytic, per candidate pair |
| D | Confidence | how confident is the LLM? | **an input variable only — must NOT be pre-defined as the decision**; H2 exists precisely because SP-M2-1 treated it as the decision |
| E | Confidence margin | is top-vs-second confidence gap informative? | study as variable; do not presuppose a threshold |
| F | User-language ambiguity | is the request itself inherently unable to separate the candidates, regardless of candidates? | separates "user was vague" from "candidates are close" |
| G | Upstream target-path stability | is the candidate set itself stable across runs? | mandatory control: upstream instability (SP-M2-1 final QA E5: Stage-1 self-resolved 2/5) must be separable from refinement instability before any refinement-stability claim |

## 6. Candidate Separability Taxonomy (experimental framework)

A labeling framework for experiments — explicitly NOT a final product
contract. Classes are assigned per (request, candidate set) pair by
annotation, before observing model behavior:

| Class | Definition | Expected behavior |
|---|---|---|
| **S1** | single candidate / deterministic fast path | resolve without refinement |
| **S2** | multi-candidate + strong distinguishing goal signal in the request | SELECT (the discriminated one) |
| **S3** | multi-candidate + weak distinguishing signal | likely CLARIFY |
| **S4** | multi-candidate + request genuinely cannot distinguish | CLARIFY |
| **S5** | high model confidence but insufficient goal evidence | the unsafe-selection risk zone — boundary must catch these |
| **S6** | LLM output unreliable / malformed | safe fallback (CLARIFY), separately tallied from semantic CLARIFY |

S5 is the class that killed the SP-M2-1 contract: high confidence and
insufficient evidence co-occurring. S3 vs S4 distinction (weak signal
vs no signal) is deliberately included to test whether one boundary can
serve both or whether S3 needs its own treatment. Classes may gain a
subdivision only if experiments force it — no taxonomy sprawl.

## 7. Experimental questions per class

- **S2**: can goal-aware refinement select the correct target
  stably (target identity AND decision across runs)?
- **S3**: does a weak signal justify SELECT, or must the boundary
  default to CLARIFY? Where is the weak/strong line?
- **S4**: can the system avoid forced SELECT even when confidence is
  high? (the AMB case)
- **S5**: how to recognize *model confidence* as distinct from
  *user-evidence-supported separability*? What observable features
  differ between an S2 SELECT and an S5 SELECT?
- **S2 vs S4** (the core contrast): is there a reliable discriminating-
  evidence boundary that separates these two classes — using only
  variables a system can actually observe?
- **S6**: can fallback-CLARIFY be distinguished from semantic-CLARIFY
  in telemetry (it already can via `target_resolution.reason` — keep
  it that way in all measurements)?

## 8. Hypotheses (to be tested, not assumed)

- **H1**: goal specificity (Var A) predicts candidate separability.
- **H2**: top-candidate confidence threshold alone cannot safely
  separate SELECT from CLARIFY (already strongly indicated: supported
  SELECTs 0.75–0.90 vs unsafe SELECTs 0.72–0.78 overlap the threshold).
- **H3**: separability assessment requires relative, candidate-set-level
  evidence (margin, discriminating content), not single-candidate
  confidence.
- **H4**: genuine ambiguity (S4) can be made to CLARIFY *stably*
  (the SP-M2-1 contract failed exactly here: 2 unsafe SELECTs in the
  final-QA sample of 5 runs).
- **H5**: refinement stability is only assessable when the upstream
  candidate set is stable (Var G control; SP-M2-1's E5 evidence).

## 9. Discovery method (no production code)

1. **Case matrix** — real U00010 candidate semantics first (F5/F6/F7/F8
   pairs and triples as actually served); a small number of clearly
   labeled controlled fixtures ONLY for boundary shapes real data
   cannot present (e.g. two candidates with near-identical
   descriptions). Fixture cases are labeled `FIXTURE` in every artifact;
   never mixed into real-RP evidence.
2. **Annotation** — for every case, BEFORE observing the model:
   correct target if uniquely supported · genuinely ambiguous? ·
   expected SELECT/CLARIFY · discriminating evidence · competing
   candidate · why the candidates can / cannot be separated · taxonomy
   class (S1–S6). Annotation is the ground truth layer.
3. **LLM observation** — observe the FROZEN refinement behavior only
   (the shipped `refine_target` via the wired runtime or direct
   module-level calls recorded as such). No prompt/threshold/algorithm
   modification. Repeats: ≥5 per case (sample-level observation only —
   never reported as a system error rate).
4. **Decision analysis** — correlate observed decisions against
   annotation: model confidence · confidence margin · goal specificity
   · candidate semantic overlap · labeled separability. Output: which
   variables actually track safe SELECT, which do not, and where a
   system-enforceable boundary would have to sit.

## 10. Data boundary (carried forward, non-negotiable)

- **REAL RP DATA** → real system behavior evidence (live U00010 via the
  real runtime; titles/descriptions re-verified at experiment time, not
  from memory).
- **CONTROLLED FIXTURE** → architecture boundary shapes only, in
  unit/contract-style experiments, explicitly labeled.
- No fixture may ever be recorded as real-RP evidence (the error the
  SP-M2-1 record previously made with its ambiguous pair).

## 11. Deliverables

1. Candidate Separability taxonomy (validated/adjusted by data)
2. Experimental case matrix (annotated)
3. Labeled examples per class, including the S2/S4/S5 boundary cases
4. Known variables with measured association to safe SELECT
5. Hypotheses with verdicts (supported / refuted / unresolvable)
6. Measurement method (repeatable; sample-level statistics labeled as
   such)
7. Expected decision boundary (candidate definition of "sufficiently
   separable")
8. Known unknowns (explicitly listed, not absorbed)
9. Exit criteria assessment (§12)

Experiment scripts, annotation files, and analysis outputs live outside
production code (QA/spike tooling locations), clearly separated from
`backend/app/`.

## 12. Exit criteria

The spike succeeds if it can ANSWER — with evidence — the following,
regardless of which implementation direction later wins:

1. What does "sufficiently separable" mean, observably?
2. What is "genuine ambiguity", observably?
3. Why is confidence alone insufficient? (demonstrate, not assert)
4. Which variables most plausibly constitute the Safe Select Boundary?
5. Which variables must be enforced by the SYSTEM rather than delegated
   to the LLM's own judgment?
6. What remains unanswerable with current means?

…and can thereby support exactly one next-step decision:

| Outcome | Meaning |
|---|---|
| **A** | an implementable Safe Select Boundary can be defined → proceed to WP-M2-1 contract design |
| **B** | more experiments needed → extend THIS spike's matrix (not a new spike) with a stated gap |
| **C** | evidence insufficient → refinement implementation paused; boundary research continues or waits |
| **D** | refinement architecture not worth continuing → record honestly with evidence |

The outcome is NOT prejudged. Any of A–D is a successful spike if the
evidence supports it.

## 13. Spike discipline (anti-proliferation rule)

If a "deeper question" appears during SP-M2-1C, it must first be
classified: (a) inside this spike's research question → absorb into the
matrix/variables; (b) an existing Unknown → record under it; (c) truly
blocking this spike → handle inside this spike; (d) an independent
architecture question → only then may a new spike be proposed, with a
written justification. No SP-M2-1C.1/.2/.3 chains.

## 14. Hard prohibitions during SP-M2-1C

No changes to: production refinement (algorithm/prompt/threshold),
target resolution, GoalRepresentation, planner, Agent Loop, frontend,
runtime contract; no new tools/regexes; no MCP/memory/multi-agent; no
architecture-doc sync; no claims of M2 completion or of H1 being
finally settled. If answering a research question appears to require a
production-contract change → STOP, record it as an architecture
dependency for the post-spike decision, do not implement it inside the
spike.

---

## 15. PMO Summary (one page)

### SP-M2-1
**Status**: Discovery Complete / Current Contract Rejected
**Why**: the current contract cannot safely handle genuine ambiguity —
"confidence ≥ threshold → SELECT" admitted unsafe forced selections
(observed 2/5 in the final-QA sample on a genuinely ambiguous request)
while the threshold overlaps supported selections (0.75–0.90).
**Value retained**: goal-aware refinement has evidence-backed value on
semantically clear multi-candidate requests (E3 5/5 correct & stable;
E5 0.90), and the runtime wiring + bounded mechanics are proven.

### SP-M2-1C (this spike)
**Question**: Candidate Separability / Safe Select Boundary — what
evidence and system conditions make SELECT admissible, what requires
CLARIFY?
**Purpose**: define the safe boundary as a research conclusion, with a
taxonomy, annotated case matrix, and frozen-behavior observation —
before any new implementation.

### M2
**Current status**: Architecture Validation / Discovery Gate.
**Blocked downstream**: Capability Metadata, Capability Suitability,
Capability Gap, Composition — Target Refinement is M2's upstream
architecture gate; its safe boundary is not yet established.
**Not started / not affected**: WP-M2-1 implementation (gated on this
spike's outcome A–D).

### PMO next action
Run SP-M2-1C discovery (case matrix → annotation → frozen-behavior
observation → decision analysis) and bring the evidence to an A/B/C/D
decision. No production code before that decision.

---

# Discovery Results (executed 2026-09-24)

Experiment artifacts: `/tmp/spm21c/cases.json` (pre-registered
annotations, written before any model run), `results_discovery.json`
(12 cases × 5 frozen runs + margin probes), `results_upstream.json`
(9 real-runtime turns), `analyze.py`. All numbers below are
**observations in this experiment sample** — not population rates.

## 1. Experiment Setup

- Module level (primary, controlled): the frozen `refine_target`
  called directly, real LLM, real RP-derived candidate sets (F5/F6/F8
  live descriptions re-verified same day) or labeled controlled
  fixtures; goal computed ONCE per case and held fixed across runs
  (Variable-G control); 5 runs per case; threshold untouched (0.7);
  no prompt or algorithm change.
- Runtime level (Variable G): 3 requests × 3 turns through the real
  `/turn` path (flag ON, fresh investigation per turn, runtime
  identity verified).
- Offline-only margin probe (labeled `OFFLINE-EXPERIMENTAL`): because
  the frozen `RefinementDecision` exposes a single confidence with no
  second-best score or ranking, **confidence margin is not observable
  under the production contract**. The probe used a separate
  experimental scorer prompt (per-candidate support 0–1 + a
  unique-support judgment) that never feeds any product path.

## 2. Ground Truth Annotation

All 12 cases were annotated BEFORE any model run, per the spike's
annotation rules (`cases.json` `_meta`): expected action, expected
target, taxonomy class, goal specificity A∈{0,1,2}, candidate overlap
B∈{0,1,2}, discriminating evidence C∈{0,1,2}, plus a `contested` flag
where a competing annotation reading exists. The frozen model's output
played no part in the annotations. One case (R-A) is pre-registered as
**contested**: the brief's topic-core rule yields SELECT F5, while an
answer-shape reading (哪些账户 = "which accounts" → enumeration)
yields F6. R-A results are classified per the pre-registered ground
truth and analyzed separately as contested.

## 3. Case Matrix (12 cases; A/B/C = specificity/overlap/evidence)

| ID | Layer | Request (abridged) | Cands | Tax | A | B | C | Expected |
|---|---|---|---|---|---|---|---|---|
| R-A | REAL | 哪些账户…共享了设备 | F5,F6 | S2 | 2 | 2 | 2 | SELECT F5 (contested: F6) |
| R-B | REAL | …有哪些关联情况？ | F5,F6 | S4 | 0 | 2 | 0 | CLARIFY |
| R-C | REAL | Linked Account Network 是什么 | F5,F6 | S2 | 2 | 2 | 2 | SELECT F6 |
| R-D | REAL | …有没有关联对冲交易 | F5,F6,F8 | S2 | 2 | 1 | 2 | SELECT F8 |
| R-E | REAL | …有哪些相关账户？ | F5,F6 | S3 | 1 | 2 | 1 | CLARIFY |
| R-F | REAL | …用了同一台设备吗 | F5,F6 | S2 | 2 | 2 | 2 | SELECT F5 |
| R-G | REAL | …交易行为有什么异常 | F5,F6,F8 | S3 | 1 | 1 | 1 | CLARIFY |
| R-H | REAL | 关联账户网络规模有多大 | F5,F6 | S2 | 2 | 2 | 2 | SELECT F6 |
| FX-1 | FIXTURE | 共用同一台设备吗 | FIX-A/B disjoint | S2 | 2 | 0 | 2 | SELECT FIX-A |
| FX-2 | FIXTURE | 有什么需要关注的吗？ | FIX-A/B | S4 | 0 | 0 | 0 | CLARIFY |
| FX-3 | FIXTURE | = R-A request, device mention removed from network candidate | FIX-C/D | S2 | 2 | 1 | 2 | SELECT FIX-D |
| FX-4 | FIXTURE | 有什么特别之处？ | FIX-E/G generic | S4 | 0 | 2 | 0 | CLARIFY |

## 4. Observations (60 frozen runs, classified per run)

| Case | Expected | Observed outcome (runs of 5) | Stable? | Margin probe (offline) |
|---|---|---|---|---|
| R-A | SELECT F5 | **5/5 INCORRECT_SELECT F6** conf 0.78–0.88 | yes (stable-wrong) | margin **0.1**, unique_support **False** |
| R-B | CLARIFY | 3 SAFE_CLARIFY (reasoned) + **2 UNSAFE_SELECT** F6 0.75–0.78 | **no** | probe failed (endpoint) |
| R-C | SELECT F6 | 5/5 CORRECT_SELECT conf 0.95–0.98 | yes | margin 0.63, unique True |
| R-D | SELECT F8 | 2 CORRECT_SELECT 0.75–0.85 + 3 OVER_CAUTIOUS_MISSED (reasoned CLARIFY) | **no** | probe failed (endpoint) |
| R-E | CLARIFY | **5/5 UNSAFE_SELECT** F6 conf 0.72–0.85 | yes (unsafe-stable) | probe failed (endpoint) |
| R-F | SELECT F5 | 3 CORRECT_SELECT 0.72–0.82 + 1 MISSED + 1 SAFE_FALLBACK | **no** | probe failed (endpoint) |
| R-G | CLARIFY | **5/5 UNSAFE_SELECT** F8 conf 0.72–0.88 | yes (unsafe-stable) | probe failed (endpoint) |
| R-H | SELECT F6 | 5/5 CORRECT_SELECT conf 0.93–0.95 | yes | margin 0.5, unique True |
| FX-1 | SELECT FIX-A | 5/5 CORRECT_SELECT conf 0.97–0.98 | yes | margin 0.95, unique True |
| FX-2 | CLARIFY | 5/5 SAFE_CLARIFY (reasoned) | yes | margin 0.0, unique False |
| FX-3 | SELECT FIX-D | 5/5 CORRECT_SELECT conf 0.93–0.95 | yes | margin 0.6, unique True |
| FX-4 | CLARIFY | 5/5 SAFE_CLARIFY (reasoned) | yes | margin 0.0, unique False |

Totals (sample): 25 CORRECT_SELECT · 13 SAFE_CLARIFY · 12
UNSAFE_SELECT · 5 INCORRECT_SELECT (contested annotation) · 4
OVER_CAUTIOUS_MISSED_SELECT · 1 SAFE_FALLBACK. Endpoint produced 1
unusable refinement output + 5 margin-probe failures in this sample.

## 5. Variables (what was actually measured)

Per §5 of the baseline: A (goal specificity), B (overlap), C
(discriminating evidence) pre-annotated; D (confidence) observed from
the frozen output; E (margin) observed ONLY via the labeled offline
probe — production output does not contain it; G (upstream stability)
controlled at module level, observed at runtime level.

## 6. Counterexamples (all four target classes found)

- **A — high confidence + expected CLARIFY → SELECT**: R-E runs at
  0.82/0.82/0.85; R-G runs at 0.80/0.88/0.82 (all UNSAFE_SELECT).
- **B — strong evidence (C=2) → CLARIFY**: R-D missed 3/5 and R-F
  missed 2/5 on pre-registered SELECT cases (reasoned CLARIFYs, not
  fallbacks).
- **C — high confidence + strong evidence → wrong target**: R-A
  5/5 SELECT F6 at 0.78–0.88 against the pre-registered F5
  (contested annotation) — while the offline scorer rated support
  non-unique (margin 0.1).
- **D — same confidence band, opposite safety**: R-F's CORRECT
  SELECTs (0.72–0.82) vs R-G's UNSAFE SELECTs (0.72–0.88) — fully
  overlapping bands.

## 7. Stability Results

- Decision-stable cases: R-A (stable-wrong), R-C, R-E (stable-unsafe),
  R-G (stable-unsafe), R-H, FX-1..4. Decision-unstable: R-B, R-D,
  R-F. **Stability tracked the offline unique-support judgment, not
  confidence**: every unique=True case was 5/5 stable-correct; the
  unique=False non-ambiguous cases (FX-2/FX-4) were 5/5 stable-CLARIFY;
  the unstable cases were exactly those whose probes failed (and whose
  annotations sat near the boundary).
- Variable G (upstream): in this runtime sample, all 9/9 turns
  retained candidates and invoked refinement (candidate counts stable
  where recorded: R-B=2, R-D=3) — but the final-QA E5 series observed
  Stage-1 self-resolution 2/5 on another session. Upstream stability
  therefore varies across sessions; H5 is supported: refinement
  stability may only be judged relative to a measured-stable candidate
  set. (Also noted, NOT fixed per spike constraints: the runtime
  `target_resolution` evidence records `candidates_considered` only on
  the clarify path, not the select path — a small evidence asymmetry
  for a future wiring revision.)

## 8. Variable → Outcome Analysis

| Variable | Observation (this sample) | Correct SELECT | Safe CLARIFY | Unsafe SELECT | Stability | Interpretation |
|---|---|---:|---:|---:|---|---|
| Goal specificity (A) | A=2 → 25 correct/5 runs-cases but also R-A stable-wrong + 4 missed; A=1 → R-E+R-G **10/10 unsafe**; A=0 → mostly safe CLARIFY (R-B 2 unsafe) | necessary, not sufficient | — | **mid-band A=1 is the most dangerous** | no | vague requests are usually refused; "just enough" requests invite over-selection |
| Candidate overlap (B) | R-A (B=2) stable-wrong vs FX-3 (B=1, same request, overlap reduced) stable-correct at +0.15 conf | high B depresses correctness | — | associated | high B associated with stable-wrong | overlap is a risk amplifier (Pair 2) |
| Discriminating evidence (C) | all stable-correct cases have verifiable one-candidate-only signals; weak-C cases (R-E/R-G) went unsafe 10/10 | **primary driver** | — | **primary risk driver** | associated | the request↔candidate signal, not the score, carries safety |
| Confidence (D) | CORRECT 0.72–0.98 (n=25); UNSAFE 0.72–0.88 (n=12); INCORRECT 0.78–0.88 (n=5) | overlapping | overlapping | overlapping | no | **cannot discriminate safe from unsafe** (H2 supported) |
| Confidence margin (E) | offline only: margin ≥0.5 & unique=True ↔ 4/4 stable-correct; margin 0.1 & unique=False ↔ stable-wrong; 0.0 ↔ stable-CLARIFY | **separates in-sample** | separates | separates | associated | strongest boundary signal measured — but production-unobservable, probe fragile (5 endpoint failures), small n |
| Candidate-set stability (G) | module-level (fixed sets) = clean attribution; runtime sample 9/9 invoked/stable here vs final-QA 2/5 self-resolved elsewhere | precondition | precondition | precondition | precondition | **H5 supported: must be measured before any refinement-stability claim** |

Association language throughout — no causal claims; all from one
sample.

## 9. Candidate Separability Findings

1. **Confidence is not a safety signal** (Q1/H2): its distribution is
   identical across correct, unsafe, and wrong-target selections in
   this sample.
2. **The unsafe zone is the middle-specificity band** (Q2): requests
   with *some* goal content but no decisive signal (R-E/R-G) produced
   unsafe selection in 10/10 runs, while fully vague requests were
   mostly refused and highly specific requests mostly resolved.
3. **Semantic overlap amplifies** (Q3): on the same request, reducing
   the competitor's overlap (FX-3) converted a stable-wrong selection
   (R-A) into a stable-correct one.
4. **Unique-support + margin is the sharpest observed boundary signal**
   (Q4/H3): it aligned with annotated safety in all 7 cases where the
   probe succeeded, and it exposed R-A (model confident; support
   non-unique) that confidence alone cannot see. This is direct,
   quantified evidence for *candidate preference ≠ candidate
   separability*.
5. **Discriminating evidence beats confidence as an explanation of
   safe SELECT** (Q4): pre-annotated C and observed unique-support
   track outcomes; confidence does not.
6. **Upstream stability is a necessary measurement precondition**
   (Q5/H5), and it varies across sessions.
7. **Over-caution is real but secondary**: 4/60 runs missed a
   pre-registered SELECT via reasoned CLARIFY (plus 1 fallback) — the
   failure mass sits on unsafe selection, not on refusal (relevant for
   any future boundary: tightening SELECT costs little in missed
   selections *in this sample*).

## 10. Safe Select Boundary — Candidate Model (research output; NOT implemented)

> **SELECT is admissible only if ALL of:**
> 1. the candidate set is measured-stable (upstream control);
> 2. a separability assessment finds **unique support** — exactly one
>    candidate clearly supported by the request+goal against the
>    others (the offline probe's margin/unique judgment is a working
>    in-sample operationalization);
> 3. the request contains discriminating evidence mapping to that
>    candidate's core and not equivalently to competitors;
> 4. model confidence is acceptable — a NECESSARY input, never the
>    decision.
>
> Otherwise: **CLARIFY.**

Explicitly undecided (future architecture decision, not this spike):
the assessment's implementation form (system rule vs second judgment
vs scorer), thresholds, whether margin/unique-support requires a
refinement-contract change (it is production-unobservable today —
recorded as an **architecture dependency**), and validation scale.

## 11. Remaining Unknowns

- Margin/unique-support probe reliability (5 endpoint failures this
  sample) and behavior at larger n.
- Whether the mid-band unsafe pattern (A=1) replicates beyond these
  cases.
- The contested-annotation class (R-A): a boundary will need a policy
  for requests whose two readings disagree — currently a human-judgment
  gap, surfaced honestly by the annotation.
- Runtime evidence asymmetry (`candidates_considered` absent on the
  select path).

## 12. Architecture Decision (per exit criteria)

**Outcome A — an implementable Safe Select Boundary can be defined.**
All eight exit questions are answerable from this sample: confidence's
role is (at most) a necessary filter; it is insufficient alone
(counterexample D); goal specificity has explanatory power but its
middle band is the risk zone; overlap is a risk amplifier;
discriminating evidence / unique-support is the core variable; margin
is production-unobservable today and requires a contract decision;
candidate-set stability is a mandatory precondition; and the boundary
above states the necessary conditions. The gated next step is a
**contract-design decision** (PMO): whether to introduce a
separability assessment (and its contract change) into WP-M2-1 — with
validation expansion (more cases, larger n, fixture + real split
maintained) as part of that work package's validation, not as an open-
ended spike extension.

## 13. PMO Next Action

- SP-M2-1C → **DISCOVERY COMPLETE, Outcome A candidate** (this doc).
- Decision requested: approve WP-M2-1 contract design on the §10
  boundary, including its architecture dependency (separability
  assessment in the refinement contract). Until that decision: no
  production code, no WP-M2-1 start, no downstream M2 WPs.
- No SP-M2-1C.x sub-spikes created; remaining unknowns (§11) are
  recorded for WP-M2-1's validation scope, not as new spikes.
