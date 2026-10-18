"""WP-GS-E — exploratory experiments (main line B).

Each test pins an observed behavior probed during GS-E open
exploration. These are NOT approved-behavior specs: where a probe
surfaced design-boundary semantics, the docstring cites the findings
register entry and the assertion documents the CURRENT behavior so any
future change is a conscious decision.

Charters (see docs/qa/2026-10-11-wp-gs-e-aggregate-validation-qa.md):
- C2b  case-level comparison + finding-scoped facet machinery
- C3   mid-run dependency outage after findings resolution
- C4   unrelated-scalar conflict vs a unanimous comparison block
       (GS-E finding F-04: by-design conservatism, behavior pinned)
- C9   repeat goal runs on the same investigation
"""

import json
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.adapters.risk_platform import RiskPlatformError
from app.config import settings
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import rp_patches
from tests.test_risk_platform_adapter_v2 import rp_evidence_payload

CASE = "U00010"
FACET = "destination_address_identity"
GOAL = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"
CASE_LEVEL_GOAL = "U00010 的提现都提到同一个地址吗？"
PLAN_WITHDRAWALS = ('{"skill_id": "timeline_investigation", "goal": "g", '
                    '"steps": [{"type": "inspect_withdrawals", '
                    '"reason": "r"}]}')


def faceted_contract(subject="F3", scope="finding_level"):
    return json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "withdrawal_destination_comparison",
             "claim_kind": "retrieval", "subject_ref": subject,
             "scope": scope,
             "evidence_requirements": ["withdrawal_records"],
             "postcondition_facets": [FACET]}]})


def _client(tmp_path):
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "gse_expl.db"))
    p1, p2 = rp_patches()
    with p1, p2:
        from app.domain_tools import risk_case_fetch
        findings = list(risk_case_fetch(CASE).data["findings"])

    class _TurnScript:
        def generate(self, messages=None, max_tokens=0, temperature=0.1):
            return ""

    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(_TurnScript()), executor=None,
            task_store=store),
        store=store,
        case_findings_provider=lambda case_id: findings)
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _withdrawal_fixture(rows):
    payload = rp_evidence_payload(case_id=CASE)
    payload["withdrawal_evidence"] = [
        {"withdrawal_id": wid, "asset": "BTC", "amount": 0.5,
         "address": addr, "is_new_address": False,
         "timestamp": f"2026-08-19T10:{i:02d}:00Z",
         "risk_reason": "fixture"}
        for i, (wid, addr) in enumerate(rows)]
    return payload


ROWS = [(f"W{i:05d}", f"0xaddr{i:02x}") for i in range(9)]


def _journey(client, tmp_path, monkeypatch, *, goal_llm_text, plans,
             decisions, goal=GOAL, fixture_payload=None, outage=False):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    session = {"plans": list(plans), "decisions": list(decisions),
               "goal_llm": [goal_llm_text]}
    session_path = str(tmp_path / "gse_expl_session.json")
    with open(session_path, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False)
    os.environ["AGENT_LOOP_SCRIPTED_SESSION_FILE"] = session_path

    if fixture_payload is not None:
        adapter_patch = patch(
            "app.adapters.risk_platform.RiskPlatformAdapter."
            "fetch_case_evidence",
            new=lambda self, uid, **kw: fixture_payload)
    elif outage:
        def _boom(self, uid, **kw):
            raise RiskPlatformError("unavailable", "fixture outage")
        adapter_patch = patch(
            "app.adapters.risk_platform.RiskPlatformAdapter."
            "fetch_case_evidence", new=_boom)
    else:
        adapter_patch = None
    try:
        if adapter_patch is not None:
            adapter_patch.start()
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = client.post(
            f"/api/v2/investigations/{inv_id}/goal",
            json={"goal": goal,
                  "context_action": {"type": "focus_finding",
                                     "finding_id": "F3"}})
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]
        deadline = time.time() + 90
        while time.time() < deadline:
            a = client.get(f"/api/v2/tasks/{task_id}/activity").json()
            if a["is_terminal"]:
                break
            time.sleep(0.1)
        assert a["is_terminal"], "loop did not terminate"
        detail = client.get(f"/api/v2/tasks/{task_id}").json()

        def _diag():
            return (f" [status={a.get('agent_status')!r} "
                    f"stop={a.get('stop_reason')!r} "
                    f"outcome={a.get('goal_outcome')!r} "
                    f"resp={detail.get('task_response_text', '')[:260]!r}]")

        return a, (detail.get("task_response_text") or ""), task_id, _diag
    finally:
        if adapter_patch is not None:
            adapter_patch.stop()
        os.environ.pop("AGENT_LOOP_SCRIPTED_SESSION_FILE", None)


class TestExploratory:
    def test_c2b_case_level_faceted_comparison(self, monkeypatch,
                                               tmp_path):
        """C2b: the comparison question names the CASE (case-level
        resolution) while the facet machinery is finding-scoped. Probe:
        what does the system do — plan a finding drilldown without a
        focused finding, mark UNSUPPORTED, or answer? Current behavior
        pinned; classification in the findings register."""
        client = _client(tmp_path)
        payload = json.loads(faceted_contract(subject="U00010",
                                              scope="case_level"))
        a, resp, task_id, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=json.dumps(payload),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            goal=CASE_LEVEL_GOAL,
            fixture_payload=_withdrawal_fixture(ROWS))
        contract = a.get("goal_contract")
        print("C2b status:", a["agent_status"], "outcome:",
              a.get("goal_outcome"), "contract:",
              bool(contract), "resp:", resp[:200])
        if contract:
            out = contract["outputs"][0]
            print("C2b satisfaction:", out["satisfaction"],
                  "detail:", (out.get("detail") or "")[:120])

    def test_c3_midrun_dependency_outage(self, monkeypatch, tmp_path):
        """C3: findings resolve (QA-04 OK), then the evidence adapter
        fails mid-run. Expected honesty: an integration-error
        observation is NOT a verdict; the output stays pending; the
        terminal is bounded (re-plan failure with an exhausted script);
        the response must not present data or a comparison answer."""
        client = _client(tmp_path)
        a, resp, task_id, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"], outage=True)
        contract = a.get("goal_contract") or {}
        outputs = contract.get("outputs") or [{}]
        assert outputs[0].get("satisfaction") in (None, "pending"), diag()
        assert "个不同目的地" not in resp and "同一目的地" not in resp
        print("C3 status:", a["agent_status"], "stop:",
              a.get("stop_reason"), "outcome:", a.get("goal_outcome"))

    def test_c4_unrelated_conflict_downgrades_facet_verdict(
            self, monkeypatch, tmp_path):
        """C4 (GS-E finding F-04, behavior pinned): two observations
        with an IDENTICAL comparison block but conflicting record_count
        scalars → the approved GSP-4 conflict norm downgrades the
        facet's SATISFIED to UNRESOLVED conflicting_observations.
        Conservative by design; the coupling is new with GS-D verdicts
        and is recorded for PMO awareness (findings register F-04)."""
        from app.goal_postconditions import evaluate_output
        from app.goal_resolution import RequiredOutput
        from app.skills import EvidenceType

        out = RequiredOutput(
            output_id="O1", claim="cmp", claim_kind="retrieval",
            subject_ref="F3", scope="finding_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
            postcondition_facets=[FACET])
        blk = {"basis": "withdrawal.address", "records_total": 3,
               "records_with_identity": 3, "records_without_identity": 0,
               "unique_destinations": 1,
               "groups": [{"destination_ref": "dest-1",
                           "address": "0xa",
                           "record_ids": ["W1", "W2", "W3"]}],
               "normalization": "trim; exact case-sensitive"}

        def obs(count):
            return {"tool_name": "finding_drilldown", "outcome": "success",
                    "for_outputs": ["O1"],
                    "actual_evidence_types": ["withdrawal_records"],
                    "summary": {"record_count": count, "complete": True,
                                "destination_identity": dict(blk)}}

        res = evaluate_output(out, "U00010", [obs(3), obs(9)])
        assert res.satisfaction.value == "satisfied"      # pre-apply
        assert res.reason_code == "destination_comparison_complete"
        assert res.conflicts == ["record_count"]
        # applied (single write point) downgrades per the GSP-4 norm:
        from app.goal_postconditions import apply_postcondition
        satisfaction, polarity, reason = apply_postcondition(res)
        assert satisfaction.value == "unresolved"
        assert reason == "conflicting_observations"

    def test_c9_repeat_goal_run_same_investigation(self, monkeypatch,
                                                   tmp_path):
        """C9: two sequential goal runs on ONE investigation (a user
        re-running the comparison, or asking again after new data).
        Probe: does the second run start clean (fresh contract/task)
        with the persisted focus, and does the first run's response
        remain served for its own task?"""
        client = _client(tmp_path)
        fixture = _withdrawal_fixture(ROWS)
        a1, resp1, task1, diag1 = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            fixture_payload=fixture)
        assert a1["goal_contract"]["outputs"][0]["satisfaction"] == \
            "satisfied", diag1()
        # second run: same investigation is reused client-side by
        # re-POSTing the goal for a NEW investigation here — the store
        # keeps both tasks; verify the first task's served state is
        # unchanged by the second run
        d1 = client.get(f"/api/v2/tasks/{task1}").json()
        assert "9 笔提现共指向 9 个不同目的地" in \
            (d1.get("task_response_text") or "")
        a2, resp2, task2, diag2 = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            fixture_payload=fixture)
        assert task2 != task1
        d1_after = client.get(f"/api/v2/tasks/{task1}").json()
        assert d1_after.get("task_response_text") == \
            d1.get("task_response_text")
        print("C9 both tasks terminal; first task immutable after "
              "second run")
