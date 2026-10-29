# QA Triage Report V1 — U00010 Cross-Language Acceptance Failure Cluster (F1–F7)

**Status**: ACTIVE — diagnosis-first triage of the 2026-10-21 manual
browser-acceptance session `INV-517e25d2e182` (case U00010, 9 turns).
**No production code was modified for this report.** All evidence is from
the persisted store (`backend/data/investigation_agent_v2.db`), the live
Risk Platform (:8000), targeted non-destructive probes, and the run
records of that session.

---

## 1. Executive summary

Seven user-visible failures were investigated. They decompose into
**five independent root-cause groups and one shared-infrastructure
group**:

- **Group S (shared infrastructure)** — F1(O1/O2), F3, and the failed
  re-plan steps in F1 share one mechanism: finding-level steps planned
  WITHOUT a finding anchor (compound/multi-target requests defeat the
  exactly-one title-anchoring rule, and re-plan steps are never
  anchored). Same family as the U00005 fix; F4's artifact-binding
  defect is the B14 sibling of the same composition problem.
- **Independent defects**: F1(O3) multi-stream retrieval evaluator
  conflict; F2/F3 understanding over-fusion + missing policy retrieval
  leg; F5 GSP policy projection/evaluator contract mismatch; F6 zh-face
  preview/sample labeling gap; F7 contract mis-modeling of a timeline
  ask.

The single most impactful repair is a **per-output finding anchoring
pass** (planner + re-plan), which unblocks F1(O1/O2), F3, and the F1
re-plan steps; F4 additionally needs the B14 rule-2 implementation
(re-plan must revise artifact `source_step_ids`).

---

## 2. Failure matrix

| # | Turn (store task) | Symptom (exact) | First divergence point | Shared? |
|---|---|---|---|---|
| F1 | TASK-4fb64b8cc217 | Both rule criteria "No determinable result"; O3 file "insufficient"; 2 artifacts created | Two: (a) re-plan steps S5 explain_signal args={} → INVALID_ARGUMENT; (b) evaluator `conflicts[record_count, streams]` on dual-stream delivery | (a) = Group S; (b) independent; artifact-split = B14 family |
| F2 | TASK-7033855d5187 | Leads with "未发现…共享 IP 记录", then 18-account attribution; no citation original text; outcome `complete` | Goal understanding fused 4 evidence types into ONE output; planner ran only `analyze_related_accounts`; evaluator ANY-of semantics marked it satisfied | Shares the "policy leg missing" with F3; satisfaction-semantics issue is its own |
| F3 | TASK-4ead29130c1e | Policy subgoal "No determinable result"; network attribution returned | `policy_lookup` ×2 INVALID_ARGUMENT `finding_id is required` (no anchor, no focus) | Yes — Group S (no-anchor family) + understanding ambiguity |
| F4 | TASK-339efd7f7403 | 6 candidate pairs in chat; artifact = plain 64-record transaction table | Stream plan authored `generate_artifact source_step_ids=["S1"]`; coverage-inserted analysis steps never re-bound it | B14 recurrence, different composition; independent of F1's evaluator issue |
| F5 | TASK-d5600133d387 (F6 focused) | retrieve_policy succeeded; O1 unresolved; "当前证据不足以确定该项" | GSP POLICY_REFERENCES branch reads `summary.matches`; the projected policy observation carries only `{match_count, associated_count, associated_citations, finding_policy_status}` | No |
| F6 | TASK-9a7e8f96946d (F6 focused) | "19 个账号" cluster vs "共 5 个" related accounts, no scope explanation | Three DIFFERENT RP populations conflated in presentation: cluster member_count=19; network attribution count=10–18; explain payload preview list=5. EN face labels the preview as SAMPLE; zh face does not | Presentation only |
| F7 | TASK-94512ce99f91 (F6 focused) | Case-wide 8-finding summary instead of F6 timeline; event "New account with high activity" | Contract mis-modeled the timeline ask: O1 ev=[case_summary, transaction_records] (no timeline type); timeline step unassociated (for: []); coverage inserted case-summary retrieval | No |

---

## 3. Evidence-backed root causes

### F1 — U00010 compound dual-criteria + dual-file ask

**Request**: 「高频提现和新账号高频活动的判定条件分别是什么？给我生成提现和交易明细文件」

**Contract** (correct): O1 高频提现判定条件 [rule_explanation]; O2
新账号高频活动判定条件 [rule_explanation]; O3 提现与交易明细文件
[withdrawal_records + transaction_records]. All subject=U00010.

**Observed**: O1/O2 pending → "No determinable result"; O3 unresolved →
"当前证据不足以确定该项"; two artifacts persisted (ART-de6d67251d
withdrawals-only 1,097 chars; ART-07e60a8abb transactions 6,659 chars);
stop = completed/bounded_data (iteration budget).

**(a) Criteria leg — first divergence**: the re-planned step
`S5 explain_signal args={}` executed → INVALID_ARGUMENT "Missing
required argument(s): finding_id" ×2 → O1/O2 pending. The planner's
title-anchoring pass declined to anchor because the request names TWO
findings (exactly-one guard) and the zh text carries no EN title tokens
— correct per its contract, but no per-output anchoring exists to
replace it. O1/O2 subject_ref=U00010 (case-level) means the evaluator
would NOT require finding binding — the rule evidence only needed a
finding_id to execute.

**(b) File leg**: O3 ev=[withdrawal_records + transaction_records];
BOTH record sets were completely delivered (7 withdrawals + 64
transactions, both `complete: true`, both for_outputs=['O3']) —
targeted probe: evaluator returns SATISFIED for each stream alone but
**UNRESOLVED + conflicts=['record_count','streams'] for both
together** — the postcondition evaluator cannot adjudicate a
multi-stream retrieval output. The two artifacts (withdrawals file +
transactions file) were actually created, so delivery happened; the
completion semantics failed.

**(c) Why completed/bounded_data**: iteration budget reached before the
re-planned S4 artifact + S5 explain steps could run; bounded completion
is honest but the plan shape was composed in an order that spent the
budget on already-covered records (S1 withdrawals ran twice).

### F2 — 「把与关联账号相关的引文原文发给我」

**Understanding**: O1 "关联账号相关引文原文" with evidence_requirements
fused to [network_relations, shared_device_evidence, shared_ip_evidence,
policy_references] — the LLM fused every plausibly-related evidence
type into one retrieval output.

**Observed**: only `analyze_related_accounts` ran; the answer leads
with the shared-IP negative statement, then the 18-account
attribution; O1 marked **satisfied**; outcome `complete`.

**Root cause** (two-layer):
1. The planner planned only the network-analysis leg — no
   `policy_lookup` step for the policy_references requirement (no
   coverage-driven insertion despite the requirement being present in
   the accepted output).
2. The postcondition ANY-of semantics: one satisfied evidence type
   (network_relations) satisfies the whole fused output — the
   citation-original-text ask is masked. ("引文" is ambiguous zh; the
   follow-up F3 disambiguates to POLICY citations — and U00010 data
   DOES carry finding-level policy citations, e.g. F6 "Policy
   citations: [3]" in the intake bundle, so the content existed.)

### F3 — 「我要的是与关联账号相关的政策引文，把该条政策原文发我」

**Contract**: O1 关联账号相关政策原文 [policy_references] pending; O2
network attribution satisfied. **Observed**: `policy_lookup` ×2
INVALID_ARGUMENT `finding_id is required` — the ask is population-
scoped ("与关联账号相关的政策"), the tool contract requires a
finding_id, and no anchor exists (no focus; zh population phrasing has
no EN title token). Same no-anchor family as F1(a). **Data check**:
U00010 findings DO carry finding-level policy citations (F6 → [3];
intake bundle "Policy References" section exists), so this is NOT a
data-availability limitation — it is an anchoring/tool-contract gap
(plus a genuine product question: which finding's policy set applies
to a population-scoped ask).

### F4 — 「查询关联账号中是否存在对冲交易，如果有，发交易明细给我，并生成一份明细文件」

**Plan** (revisions=1, no re-plan): S1 inspect_transactions(F1) →
S2 generate_artifact **source_step_ids=["S1"]** → S3
analyze_related_account_set_trades (6 candidates, budget-bounded) →
S4 analyze_related_accounts. Persisted artifact = S1's plain
64-record transaction table.

**Root cause**: composition starvation. The ARTIFACT-intent stream
plan authored S1+S2(binding=[S1]); the set-internal analyses were
coverage-inserted at adoption, but S2's binding was never revised —
the documented-but-unimplemented B14 contract rule 2 ("any re-plan
that inserts steps producing evidence required by an artifact's goal
must revise that artifact step's source_step_ids"). Additionally the
vocabulary gap recurred: 「关联账号中是否存在对冲交易」 — the marker
list has 关联账户中 but NOT the 账号 variant, and `_SET_EXISTENCE_RE`
requires 是否有对冲 (the text has 是否存在对冲) — so the set-internal
branch of `_artifact_stream_plan` did not fire; the stream plan did.

### F5 — F6 suggested next step 「这个发现适用哪些政策要求？」

F6 WAS focused (focused_finding_id=F6). `retrieve_policy` succeeded
(`policy_matches`); projected summary = {match_count,
associated_count, associated_citations, finding_policy_status}; the
GSP POLICY_REFERENCES branch requires `summary.matches` — **the
projection and the GSP composer/evaluator disagree on the payload
shape** → O1 unresolved → "当前证据不足以确定该项".

**Data check**: the intake bundle shows F6 "Policy citations: [3]" —
finding-level policy content EXISTS for F6. This is an execution/
projection-path defect, not missing data.

### F6 — 「这个发现为什么被标记？解释它的检测信号。」

Answer: graph cluster Cluster_device_sharing_1 (device_sharing; **19
accounts**, risk 80.0) + shared device DEV8Z1O4A35QU + 「同一集群中的
关联账号（共 5 个）: U00337, U00325, U00349, U00004, U00343」.

**Source fields (RP-verified for U00010)**: network_evidence.member_count
= **19** (graph cluster members); network_evidence.related_accounts_count
= **10** with related_accounts = first **5** (server-side preview);
related_account_analysis (separate tool) reported 18 device-linked
related accounts from the risk-summary feature. So "19 vs 5" conflates
three legitimately different populations (cluster members; preview
sample; device-linkage count), and the zh answer states 「共 5 个」
with no preview/sample/total labeling — the EN face carries the
SAMPLE/total/alternative-tool labeling, the zh face does not
(language-asymmetric presentation). Not a data/binding error.

### F7 — F6 suggested next step 「展示这个发现的时间线。」

**Contract mis-model**: O1 "发现 F6 时间线" with
evidence_requirements=[case_summary, transaction_records] — a timeline
ask has no timeline evidence type in the frozen vocabulary, so the
understanding layer substituted case semantics. The plan ran
inspect_timeline(view=timeline) twice, but neither observation was
associated to O1 (for_outputs=[]) — the coverage gate then inserted a
case-summary retrieval (risk_case_fetch), which satisfied O1's
declared case_summary requirement → goal_satisfied with a CASE-WIDE
summary. The timeline event shown ("New account with high activity")
is the focused-finding anchor's timeline row — F6's graph finding
carries device-linked events, and U00010's timeline row for the
new-account rule is what RP returned; whether that event belongs to
F6's population is a data-semantics question registered as UNKNOWN
(§11). The user-visible failure is the scope substitution: a timeline
ask answered by a case summary.

---

## 4. Shared-root-cause groups vs independent defects

**Group S — unanchored finding-level steps (F1a, F3; also blocks F1's
re-plan steps)**: compound or population-scoped asks defeat the
exactly-one title-anchoring rule; re-plan steps are never anchored.
Smallest repair: per-output subject anchoring in the planner (each
contract output's claim/subject is matched against finding titles/
signal refs deterministically; multi-output asks anchor PER OUTPUT,
not per request), plus the same anchoring call in the re-plan path
(already proven for the U00005 fix).

**Group B14-family — artifact binding (F4; F1's split artifacts)**:
stream-plan artifact binding + coverage-inserted steps; re-plan/
adoption never revises bindings. Repair: implement the documented B14
rule 2 — on adoption and re-plan, revise artifact `source_step_ids`
to cover the steps producing the artifact-output's required evidence
(or plan a second artifact per stream, which the current run
effectively produced as two partial files).

**Independent defects**:
- F1(b): postcondition evaluator cannot adjudicate multi-stream
  retrieval outputs (conflicts on record_count/streams).
- F2/F3: policy leg — planner omits policy retrieval for fused
  population asks; tool contract requires finding_id (F3); evaluator
  ANY-of fusion masks the miss (F2).
- F5: GSP policy projection/evaluator shape mismatch (matches vs
  match_count/associated_*).
- F6: zh-face preview/sample/total labeling asymmetry (three
  populations conflated in presentation).
- F7: timeline-ask contract mis-modeling (no timeline evidence type;
  coverage substitutes case summary).

## 5. Existing tests that failed to catch each issue

| Failure | Existing coverage | Why it missed |
|---|---|---|
| F1(a) | U00005 anchoring tests (single EN title); set-internal intent tests (zh phrasings, single-population) | Multi-TITLE zh compound asks + per-output anchoring never tested; re-plan steps never anchored in tests |
| F1(b) | Facet/records tests use SINGLE-stream delivery | No test feeds TWO record streams into one retrieval output |
| F1(c) artifacts | r2a stream-artifact tests pin per-stream binding | No test composes stream plan + coverage-inserted analyses (the adoption composition) |
| F2/F3 | policy_lookup tool tests; cg_gs policy QA | No test for population-scoped citation asks; no test that the planner plans a policy leg for a policy_references output |
| F4 | B14 documented; U00029 tests pin the set-internal branch (analyses+artifact) | No test for the STREAM-plan + coverage-insert composition (artifact bound to S1 while analyses inserted) |
| F5 | cg_gs policy tests use raw `matches` payloads | GSP-path projection shape (match_count/associated_*) never fed to the GSP evaluator/composer |
| F6 | EN-face preview labeling tested | zh-face preview/sample labeling never asserted |
| F7 | timeline tool tests; suggested-next-step chip tests | No test that a timeline ASK produces a timeline-scoped CONTRACT (evidence type gap) and a timeline-scoped answer |

## 6. Minimal repair plan (order by dependency/impact — NOT implemented)

1. **R1 — per-output anchoring (Group S)**: planner post-pass anchors
   each contract output's finding-level steps by matching the OUTPUT's
   claim/subject against finding titles/signal refs (per output, not
   per request); re-plan path applies the same pass. Unblocks F1(a),
   F3; fixes the F1 re-plan INVALID_ARGUMENTs.
2. **R2 — B14 rule 2 implementation**: on plan adoption and re-plan,
   revise artifact `source_step_ids` to include every step producing
   the artifact output's required evidence (or emit one artifact per
   required stream). Fixes F4, F1's split/incorrect files.
3. **R3 — GSP policy projection contract**: align the projected policy
   observation and the GSP POLICY_REFERENCES evaluator/composer on ONE
   shape (include matches/associated refs in the projection, or teach
   both to read the projected keys). Fixes F5, and the policy leg of
   F2/F3 once R1 plans the policy step.
4. **R4 — multi-stream retrieval evaluator**: when a retrieval output
   requires N record streams and N complete single-stream deliveries
   exist, satisfy from their union (label per-stream scope) instead of
   conflicts. Fixes F1(b).
5. **R5 — zh preview/sample labeling parity**: port the EN
   SAMPLE/total/alternative-tool labeling to the zh graph face.
   Fixes F6's presentation gap.
6. **R6 — timeline evidence semantics**: add a timeline evidence
   requirement/association so a timeline ask produces a
   timeline-scoped contract and timeline-associated answer. Fixes F7.
7. **R7 — unsolicited-artifact authorization (separate registered
   TODO GJ-REG-U00029-NOART)**: gate generate_artifact in the
   INVESTIGATION-intent set-internal branch on explicit artifact
   intent. Interacts with F4: R2 must land compatibly (authorized
   file asks keep the artifact; investigation-only asks drop it).

## 7. Regression requirements (EN/ZH per journey)

- F1: zh compound dual-criteria + dual-file request → BOTH rule
  outputs answered from rule evidence; files carry withdrawals AND
  transactions respectively (or one combined file); no "No
  determinable result". EN mirror request.
- F2/F3: zh+EN citation-original-text asks → policy citation
  document/section/original text retrieved and cited (or an explicit
  no-finding-level-policy statement grounded in finding_policy_status);
  must NOT be answered by network attribution alone.
- F4: zh+EN hedge + details + file → conversation lists candidates
  AND the persisted artifact contains the candidate table with pairs/
  transaction ids (not the plain transaction table).
- F5: zh+EN "which policy requirements apply to F6" (chip and typed) →
  associated citation document/section rendered from the projected
  policy observation.
- F6: zh+EN explain asks → answer labels preview vs total (样例/总数)
  and cluster vs device-linkage populations in BOTH faces.
- F7: zh+EN timeline asks → timeline events scoped to the focused
  finding (or an explicit bounded statement), never a case-wide
  findings summary.

## 8. Acceptance requirements

- API E2E (real LLM + real RP, no synthetic fixture): per journey,
  assert contract outputs, executed tools, per-output satisfaction,
  answer content, and persisted artifact content/source refs via
  GET /tasks/{id}(/artifacts).
- Browser E2E (real input box): per journey, assert rendered answer
  sections, suggested-next-step outcomes for focused findings, artifact
  panel content (tab visitor), and refresh/reopen persistence.

## 9. Artifact content/source-binding checks (per case)

- F1: both artifacts' sections and record streams must match their
  authorizing outputs (withdrawals file ↔ withdrawal stream; the
  transactions/evidence file ↔ transaction stream + rule evidence).
- F4: artifact must contain the candidate table (pairs + transaction
  ids + criteria) sourced from the set-internal analysis observation —
  never the plain transaction table.
- F5: answer must cite the associated policy document/section for F6
  (citation [3]) when finding_policy_status=associated.

## 10. Project-level QA closure stop condition

QA closure for this cluster is reached when: R1–R6 are implemented
with the §7 regressions green in BOTH languages; two consecutive real
API E2E runs per journey (EN + ZH) pass all per-deliverable checks;
one browser E2E per journey passes; and the F1 budget/ordering shape
(re-plan producing the full multi-deliverable plan) is demonstrated at
least once without iteration-budget exhaustion. Any new failure is
triaged under the same groups before being declared fixed.

## 11. Unknowns requiring further reproduction

1. F7: whether RP's timeline for F6 (graph finding) legitimately
   contains the "New account with high activity" event, or the
   drilldown ignored the F6 anchor (requires RP timeline inspection
   for the F6 finding id).
2. F1: whether the iteration budget (not step order) alone caused the
   S4/S5 non-execution, or the loop's re-plan admission also
   deprioritized them (requires a scripted re-plan replay).
3. F2: the product intent of 「引文」 in this phrasing — evidence
   citations vs policy citations vs shared-IP records (requires
   product decision; F3 suggests policy).
4. Whether glm-5.3 output variance (LLM_OUTPUT_INVALID family) also
   contributed to any F1–F7 run (none of the seven failed at the
   planner boundary; all produced plans).
5. The exact RP semantics of related_accounts (device-linkage set) vs
   cluster members (graph traversal) — required before R5 wording is
   finalized.

---

**Verification of this triage**: store queries against
`backend/data/investigation_agent_v2.db` (tasks_v2, agent_states,
investigation_sessions for INV-517e25d2e182); live RP GETs for U00010
evidence/explain; one targeted non-destructive evaluator probe
(`evaluate_output` with single vs dual stream observations); no
production code modified; nothing staged/committed/pushed.
