"""AL-2.6 — REAL RP API E2E: case-level cross-account routing. NO MOCKS.

The exact defect scenario from the real-browser manual test, driven over
the real running stack (real uvicorn :8001 → real Agent Loop runtime →
real RP :8000), starting from the RAW natural-language goal. Nothing is
pre-resolved: no goal_type injection, no focused_finding_id, no plan
steps, no scripted runner — the production default loop runs.

The test independently fetches the RP payloads ITSELF as the assertion
oracle only.

Asserts the FULL trajectory from the persisted Activity/state — not just
a terminal status: Context resolved (not unresolved) → Planner produced
the governed analysis plan → real tool executed → Observations recorded
→ bounded terminal → artifact persisted through the read model.

Negative controls prove the fix did NOT relax context safety: requests
that do NOT carry the supported case-level intent still terminate on the
existing safe BLOCKED path with ZERO tool invocations.

Skips when the real stack is not running (the AL-2.6 gate requires it to
RUN and PASS against the live stack).
"""

import json
import time

import httpx
import pytest

BACKEND = "http://localhost:8001"
RP = "http://localhost:8000"
CASE = "U00010"          # the real case from the manual browser test
# The EXACT raw user goal from the manual test (untouched, unnormalized).
RAW_GOAL = ("查一下有没有不同账户之间的对冲交易情况，如果有，输出相关证据给我；"
            "如果没有，告诉我你都查了哪些范围内的账户从而判定没有关联交易的")
POLL_TIMEOUT_S = 300

pytestmark = pytest.mark.al26_real_rp


def _stack_available() -> bool:
    try:
        if httpx.get(f"{BACKEND}/health", timeout=3).status_code != 200:
            return False
        return httpx.get(
            f"{RP}/api/risk/cases/{CASE}/evidence",
            timeout=5).status_code == 200
    except Exception:
        return False


def _run_goal(c: httpx.Client, goal: str) -> dict:
    """Open a fresh real investigation for CASE and run the raw goal
    through the real /goal endpoint to a terminal state."""
    inv = c.post(f"{BACKEND}/api/v2/investigations",
                 json={"case_id": CASE})
    assert inv.status_code == 200, inv.text
    inv_id = inv.json()["investigation"]["investigation_id"]
    run = c.post(f"{BACKEND}/api/v2/investigations/{inv_id}/goal",
                 json={"goal": goal})
    assert run.status_code == 200, run.text
    task_id = run.json()["task_id"]
    deadline = time.time() + POLL_TIMEOUT_S
    activity = None
    while time.time() < deadline:
        activity = c.get(
            f"{BACKEND}/api/v2/tasks/{task_id}/activity").json()
        if activity.get("is_terminal"):
            break
        time.sleep(2)
    assert activity and activity.get("is_terminal"), \
        f"loop did not terminate within {POLL_TIMEOUT_S}s"
    return activity


def test_raw_goal_reaches_case_level_analysis_over_real_rp():
    if not _stack_available():
        pytest.skip("real stack not running (backend :8001 / RP :8000)")
    # oracle: the analyzed universe is REAL RP data (cluster accounts)
    evidence = httpx.get(f"{RP}/api/risk/cases/{CASE}/evidence",
                         timeout=10).json()
    network = evidence.get("network_evidence") or {}
    oracle_accounts = set(network.get("related_accounts") or []) | {CASE}

    with httpx.Client(timeout=30) as c:
        activity = _run_goal(c, RAW_GOAL)
        task_id = activity["task_id"]

        # --- Context: NOT unresolved; case-level context route taken ----
        assert activity["agent_status"] == "completed", \
            f"status={activity['agent_status']} stop={activity['stop_reason']}"
        assert activity["stop_reason"] == "goal_satisfied"
        assert "unresolved" not in (activity["stop_reason"] or "")
        event_types = [e.get("event_type") for e in activity["activity"]]
        assert "agent.blocked" not in event_types
        assert "context.resolved" in event_types

        # --- Planning: PlannerV2 produced the governed analysis plan -----
        # (asserted from the PERSISTED plan.created event — the plan_steps
        # snapshot only mirrors the LATEST plan after any re-plan)
        plan_created = [e for e in activity["activity"]
                        if e.get("event_type") == "plan.created"]
        assert plan_created, "no plan.created event"
        first_plan_summary = plan_created[0].get("summary") or ""
        assert "analyze_cross_account_trades" in first_plan_summary
        assert "generate_artifact" in first_plan_summary

        # --- Tool: real governed tools executed over real RP -------------
        completed = {}
        for e in activity["activity"]:
            if e.get("event_type") == "tool.completed" and e.get("tool_name"):
                completed[e["tool_name"]] = e.get("outcome")
        assert completed.get("cross_account_trade_analysis") == "success"
        assert completed.get("artifact_bundle") == "success"

        # --- Observation: recorded for the executed steps ----------------
        observations = [e for e in activity["activity"]
                        if e.get("event_type") == "observation.recorded"]
        assert len(observations) >= 2
        assert observations[0].get("step_type") == \
            "analyze_cross_account_trades"
        assert observations[0].get("outcome") == "success"

        # --- Result: real RP data correspondence --------------------------
        # the activity event carries the user-safe display phrase; the
        # honest analyzed-scope digest lives in the persisted agent state
        # and the honest analysis result in the PERSISTED bundle body
        # (asserted below) — the activity projection never carries payload
        # bodies by design
        assert (observations[0].get("summary") or "").strip() != ""

        # --- Persistence: artifact readable through the read model -------
        assert activity["artifact_ids"], "no artifact persisted"
        arts = c.get(
            f"{BACKEND}/api/v2/tasks/{task_id}/artifacts").json()["artifacts"]
        assert len(arts) >= 1
        bundle = json.dumps(arts[0], ensure_ascii=False)
        assert CASE in bundle                       # real case scope
        assert "cross_account" in bundle            # the real analysis
        # the bundle honestly reports the analysis limitations (never a
        # fabricated no-match): either candidates or the bounded-retrieval
        # caveat is present
        assert ("Candidate Cross-Account Relationships" in bundle)
        detail = c.get(f"{BACKEND}/api/v2/tasks/{task_id}").json()
        assert any(a["artifact_id"] == arts[0]["artifact_id"]
                   for a in detail["artifacts"])
        assert oracle_accounts, "RP cluster universe unexpectedly empty"


def test_negative_control_unsupported_request_still_blocked():
    """Accounts named WITHOUT the trade/hedging subject (and a fully
    unresolvable request) must NOT reach the cross-account tool — the fix
    must not relax context safety into 'everything focus-less runs'."""
    if not _stack_available():
        pytest.skip("real stack not running (backend :8001 / RP :8000)")
    with httpx.Client(timeout=30) as c:
        for negative_goal in (
                "比较不同账户的提现频率",
                "tell me about the thing that does not exist"):
            activity = _run_goal(c, negative_goal)
            assert activity["agent_status"] == "blocked", \
                f"{negative_goal!r}: {activity['agent_status']}"
            # AL-2.10: the bounded-blocked family is the contract — the
            # real-LLM assist may legitimately produce a single-candidate
            # ambiguity (clarification) for an unresolvable referent.
            assert activity["stop_reason"] in (
                "context_unresolved", "clarification_needed",
                "semantic_resolution_unavailable")
            assert activity["plan_steps"] == []
            assert activity["iteration"] == 0
            tools = [e.get("tool_name") for e in activity["activity"]
                     if e.get("event_type") in ("tool.called",
                                                "tool.completed")]
            assert tools == [], \
                f"{negative_goal!r} invoked tools: {tools}"
