"""AL-2.5 — REAL RP API E2E over the real running stack. NO MOCKS.

Drives the REAL uvicorn backend (:8001) over real HTTP; the backend's
real Agent Loop runtime (real PlannerV2/ExecutorV2/registry/TaskStoreV2)
calls the REAL Risk Platform (:8000) over real HTTP. This module imports
NO repo test fixtures and applies NO patches — see the anti-mocking
contract in docs/qa/2026-09-28-al2-5-real-rp-browser-e2e-v1.md.

The test independently fetches the RP payloads ITSELF purely as the
assertion ORACLE (ground truth to compare against) — it is never fed to
the agent under test, which only sees what a real user's request would
fetch through the real tool path.

The test SKIPs (rather than fails) when the real stack is not running,
so the offline suite stays green; the AL-2.5 gate requires this test to
RUN and PASS against the live stack.
"""

import json
import re
import time

import httpx
import pytest

BACKEND = "http://localhost:8001"
RP = "http://localhost:8000"
CASE = "U00299"          # stable seeded case in the local RP instance
# The bundle request is the deterministic golden path: ARTIFACT_CASE
# intent → deterministic intake plan [fetch_case, generate_artifact] with
# artifact_produced completion criteria. It exercises the full real chain
# (loop → planner → governed step types → executor → real RP HTTP →
# artifact persistence) with NO dependency on external LLM availability.
GOAL = "Generate the investigation bundle for U00299"
POLL_TIMEOUT_S = 300

pytestmark = pytest.mark.al25_real_rp


def _stack_available() -> bool:
    try:
        if httpx.get(f"{BACKEND}/health", timeout=3).status_code != 200:
            return False
        r = httpx.get(f"{RP}/api/risk/cases/{CASE}/evidence", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _rp_oracle() -> dict:
    """Ground truth fetched by the TEST directly from the real RP."""
    evidence = httpx.get(f"{RP}/api/risk/cases/{CASE}/evidence",
                         timeout=10).json()
    explanation = httpx.post(f"{RP}/api/risk/explain",
                             json={"user_id": CASE}, timeout=10).json()
    return {"evidence": evidence, "explanation": explanation}


def _required_real_values(oracle: dict) -> list[str]:
    """Values that exist only in the LIVE RP data (nothing hardcoded from
    repo fixtures). The agent's persisted artifact must contain them."""
    required = [CASE]
    for kf in (oracle["explanation"].get("key_findings") or []):
        body = kf.split(". ", 1)[1] if ". " in kf[:6] else kf
        name = body.split("\n")[0].split(" — ")[0].strip()
        name = re.sub(r"\s*\[\d+\]\s*$", "", name).strip()
        if name and len(name) > 3:
            required.append(name)
    return required


def test_real_rp_goal_loop_intake_bundle():
    if not _stack_available():
        pytest.skip("real stack not running (backend :8001 / RP :8000)")
    oracle = _rp_oracle()
    required = _required_real_values(oracle)

    with httpx.Client(timeout=30) as c:
        # 1) open a real investigation through the real API
        inv = c.post(f"{BACKEND}/api/v2/investigations",
                     json={"case_id": CASE})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]

        # 2) start the real bounded Agent Loop over real HTTP
        run = c.post(f"{BACKEND}/api/v2/investigations/{inv_id}/goal",
                     json={"goal": GOAL})
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]

        # 3) poll the real activity projection to terminal
        deadline = time.time() + POLL_TIMEOUT_S
        activity = None
        while time.time() < deadline:
            activity = c.get(
                f"{BACKEND}/api/v2/tasks/{task_id}/activity").json()
            if activity.get("is_terminal"):
                break
            time.sleep(2)
        assert activity and activity.get("is_terminal"), \
            f"loop did not terminate within {POLL_TIMEOUT_S}s: {activity!r}"
        assert activity["agent_status"] == "completed", \
            f"agent_status={activity['agent_status']} " \
            f"stop={activity['stop_reason']}"
        assert activity["stop_reason"] == "goal_satisfied"

        # 4) the plan ran REAL tools (governed mapping → real executor)
        step_status = {p["type"]: p["status"] for p in activity["plan_steps"]}
        assert step_status.get("fetch_case") == "success"
        completed_tools = [e.get("tool_name") for e in activity["activity"]
                           if e.get("event_type") == "tool.completed"]
        assert "risk_case_fetch" in completed_tools
        assert activity["iteration"] >= 2

        # 5) the persisted artifact is served through the read model and
        #    corresponds to the LIVE RP data (dynamic oracle comparison)
        arts = c.get(
            f"{BACKEND}/api/v2/tasks/{task_id}/artifacts").json()["artifacts"]
        assert len(arts) >= 1, "no artifact persisted for the loop run"
        bundle = json.dumps(arts[0], ensure_ascii=False)
        missing = [v for v in required if v not in bundle]
        assert not missing, f"artifact missing live RP values: {missing}"

        # 6) reopen surface (task detail) serves the same artifact
        detail = c.get(f"{BACKEND}/api/v2/tasks/{task_id}").json()
        assert any(a["artifact_id"] == arts[0]["artifact_id"]
                   for a in detail["artifacts"])
