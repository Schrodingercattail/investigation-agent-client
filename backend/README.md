# Investigation Agent Client Backend

Week 1 implementation of the Investigation Agent: an orchestration layer over
the Risk Platform (findings / evidence / policy) with LLM-constrained
planning, deterministic tool execution, and provenance-traceable artifacts.

Principles: `docs/architecture/AGENT_RESPONSE_EVIDENCE_PRINCIPLES_V1.md`
Architecture: `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md`
Contracts: `DOMAIN_MODELS_V1.md`, `SKILL_MODEL_V1.md`, `FOLLOW_UP_MODEL_V1.md`, `API_CONTRACT_V1.md`

## Port Configuration

- **Risk Platform**: `localhost:8000` (external service)
- **Investigation Agent Client**: `localhost:8001` (this service)
- **Frontend Dev Server**: `localhost:3001` (Vite dev server with proxy to 8001)

### Environment Variables

See `.env.example` for required environment variables:

```bash
# Anthropic-compatible LLM Configuration
ANTHROPIC_API_KEY=your_api_key_here
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_MODEL=glm-5.3

# Risk Platform Integration (points to external Risk Platform on port 8000)
RISK_PLATFORM_BASE_URL=http://localhost:8000
```

## Starting the Backend

```bash
# From the backend directory
./start.sh
# or
uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level info
```

### Development

```bash
pip install -r requirements.txt
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

## API (V2 — investigations)

- `POST /api/v2/investigations` — create an investigation (canonical case_id or raw reference, resolved server-side)
- `GET /api/v2/investigations/{id}` — investigation + context + tasks
- `GET /api/v2/investigations` — history list
- `DELETE /api/v2/investigations/{id}` — delete
- `POST /api/v2/investigations/{id}/turn` — run one investigation turn
- `GET /api/v2/investigations/{id}/followups` — read-only Suggested Follow-ups preview
- `GET /api/v2/tasks/{task_id}` — task detail (+ composed response / follow-ups for conversation restore)
- `GET /api/v2/tasks/{task_id}/artifacts` — task artifacts
- `GET /api/v2/tasks/{task_id}/results` — read-only normalized tool results

## Skills and Tools

**Skill = investigation workflow.** A skill defines the closed planning
vocabulary (steps) the LLM may choose for a request and the capability gate a
finding must satisfy for the skill to be eligible.

**Tool = reusable capability.** A tool is a concrete, registered execution
capability (`STEP_TOOL_MAP` binding) and may be bound to multiple skills —
e.g. `finding_drilldown` serves both `inspect_timeline` (view=timeline) and
`inspect_evidence` (view=evidence) steps of `timeline_investigation`.

Week 1 skills: `case_intake`, `timeline_investigation` (the only
finding-level investigation skill), `trade_investigation` (registered; not
executable — see matrix below).

## Week 1 Investigation Coverage

Week 1 capability availability is runtime-driven. The following matrix
defines the currently implemented investigation surface. Per the
Capability Transparency principle
(`AGENT_RESPONSE_EVIDENCE_PRINCIPLES_V1.md` §P19), the Agent never offers
or implies an action outside this surface.

A **finding exists** ≠ a **finding supports every investigation capability**.
Each finding carries a `FindingCapability` set derived at normalization time
from what the Risk Platform actually returned (`derive_capabilities` in
`app/adapters/risk_platform.py`). A capability is offered to the user only
when this chain holds end-to-end:

```
FindingCapability → skill eligibility → planning step → STEP_TOOL_MAP
                  → registered provider tool → executable path
```

### Supported capabilities

| Capability | What it means to a user | User action (example) | Runtime step | Tool | Availability condition |
|---|---|---|---|---|---|
| Case intake | Loads the authoritative finding set for a case and establishes the investigation baseline | "Investigate U00010" / create investigation | `fetch_case` | `risk_case_fetch` | Always (case-level; no finding focus or capability required) |
| Artifact generation | Produces the persistent, provenance-traceable Markdown bundle of what was investigated | "Generate a Markdown investigation bundle" | `generate_artifact` | `artifact_bundle` | Always at case level; finding scope when a finding is focused |
| Timeline | Shows the chronological sequence of a finding's events | "Show the timeline of this finding" | `inspect_timeline` (view=timeline) | `finding_drilldown` | Finding declares `timeline` (case has >1 timestamped evidence record) |
| Complete concrete evidence | Lists every authoritative record behind the finding (complete set, never a subset) | "Show all withdrawals supporting this finding" | `inspect_evidence` (view=evidence) | `finding_drilldown` | Finding declares `timeline` (same timestamped-evidence condition; view fetches the COMPLETE record set) |
| Signal explanation | Explains why the finding was flagged (rule/ML/graph detection basis) | "Why is this finding flagged?" | `explain_signal` | `signal_explain` | Finding declares `signal_explain` (a triggered rule or detector-named finding exists) |
| Policy lookup | Shows the policy requirements that apply to this specific finding | "Which policy requirements apply?" | `retrieve_policy` | `policy_lookup` | Finding declares `policy_lookup` (the Risk Platform attached a citation to the finding) |

All timeline/evidence/signal/policy capabilities run under the
`timeline_investigation` skill (required capability: `timeline`).

### Capability derivation summary (runtime truth: `derive_capabilities`)

| FindingCapability | Granted when |
|---|---|
| `timeline` | the case carries more than one timestamped transaction/withdrawal record |
| `signal_explain` | a triggered rule backs the finding, OR the finding name is in the Risk Platform's detector vocabulary (ML pattern detection, shared devices, linked accounts, trading frequency, withdrawal) |
| `policy_lookup` | the Risk Platform actually attached a citation marker to the finding (`[n]` in its authoritative text) |
| `opposite_trades` | the finding is a coordinated-trading/opposite-trade finding AND opposite-trade-ratio data exists |

Capability sets therefore differ between findings of the same case. Example:
a case-intake finding named "High trading frequency" derives
`{signal_explain, timeline, policy_lookup}` (a citation was attached and
timestamped trades exist), while an un-cited feature observation of the same
case derives only `{signal_explain, timeline}` — and it supports neither
policy lookup nor any opposite-trades path. Which chips appear in the
conversation follows from exactly these runtime sets.

### Not implemented in Week 1

| Path | Status |
|---|---|
| Opposite-trades drilldown (`inspect_opposite_trades`, view=opposite_trades) | Skill/step/binding registered, **view not implemented** in `finding_drilldown` — not executable |
| `trade_investigation` skill | Registered but its core step (`inspect_opposite_trades`) binds to the unimplemented opposite_trades view — **excluded from the planner's candidate set** (not executable → never offered) |
| Dedicated network / relationship drilldown | No registered step or tool — not implemented |
| `show_evidence` / `next_actions` / `verify_next` follow-up templates | Deliberately absent from the follow-up registry (no distinct executable tool path — no dead buttons) |

Unsupported capability on a focused finding surfaces as a bounded
`unsupported`/`unsupported_request` outcome — never as empty data.

### Product boundaries

- **One investigation = one case.** An investigation session represents
  exactly one case. A later message naming a *different* case is not
  executed against either case — the Agent states which case is under
  investigation and points the user to start a new investigation for the
  other case. Messages naming the current case remain valid. Switching
  cases happens only through the explicit **New Investigation** action.
- **Out-of-scope requests fail readably.** Requests that cannot be mapped
  to a supported investigation intent (in any language) receive a
  human-readable response naming what the Agent *can* investigate — never
  planner/schema/LLM internals. Transient planning-service unavailability
  is reported as retryable and is semantically distinct from out-of-scope.
- **Week 1 is intentionally bounded** to the matrix above; anything not in
  it is not offered, implied, or partially executed.

## Project Structure

- `app/api/investigations.py` - V2 HTTP boundary (investigations/turns/tasks/results)
- `app/investigation_service.py` - turn orchestration (resolve → plan → execute → compose)
- `app/planner_v2.py` - LLM planner over the closed skill vocabulary
- `app/executor_v2.py` - deterministic plan execution + runtime contract checks
- `app/skills.py` - Skill Registry, `STEP_TOOL_MAP`, contract checker
- `app/context_resolution.py` - ContextResolver (explicit selection → context → match)
- `app/followups.py` - Suggested Follow-ups selection pipeline
- `app/domain_tools/` - the five Week 1 tools (risk_case_fetch, finding_drilldown, signal_explain, policy_lookup, artifact_bundle)
- `app/adapters/risk_platform.py` - the single Risk Platform boundary (normalization + capability derivation)
- `app/models.py` - domain models (Finding, FindingCapability, ToolResult, Artifact, …)
- `app/task_store_v2.py` - persistence (investigations, tasks, sessions)
- `app/agent.py` / `app/tools.py` / `app/store.py` - superseded-era modules kept for transitional imports (not the V2 path)
- `tests/` - test suite
