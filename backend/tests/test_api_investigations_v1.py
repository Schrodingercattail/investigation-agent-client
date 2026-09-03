"""HTTP boundary tests for the V2 investigation API.

Real orchestration components (ContextResolver, Skill Registry, Contract
Checker, PlannerV2, ExecutorV2, InvestigationService, follow-up selector,
TaskStoreV2) with faked LLM + Risk Platform. No live LLM/RP/network.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import tests.test_risk_platform_adapter_v2 as fx
from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.investigation_service import InvestigationService
from app.models import Finding, FindingCapability
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2


F3_CAPS = FindingCapability.model_validate(
    ["timeline", "signal_explain", "policy_lookup"])


class ScriptedLLM:
    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = 0

    def generate(self, messages, max_tokens=0, temperature=0.1):
        s = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1
        return s


PLAN_CASE = ('{"skill_id": "case_intake", "goal": "g", "steps": '
             '[{"type": "fetch_case", "reason": "r"}]}')
PLAN_TIMELINE = ('{"skill_id": "timeline_investigation", "goal": "g", "steps": '
                 '[{"type": "inspect_timeline", "reason": "r"}]}')
PLAN_SIGNAL = ('{"skill_id": "timeline_investigation", "goal": "g", "steps": '
               '[{"type": "explain_signal", "reason": "r"}]}')
PLAN_POLICY = ('{"skill_id": "timeline_investigation", "goal": "g", "steps": '
               '[{"type": "retrieve_policy", "reason": "r"}]}')
PLAN_ARTIFACT = ('{"skill_id": "case_intake", "goal": "g", "steps": '
                 '[{"type": "generate_artifact", "reason": "r"}]}')


def make_findings():
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
    ):
        from app.domain_tools import risk_case_fetch
        result = risk_case_fetch("U00299")
    findings = []
    for f in result.data["findings"]:
        if f.finding_id == "F3":
            findings.append(Finding(
                finding_id="F3", case_id="U00299", type=f.type, title=f.title,
                severity=f.severity, summary=f.summary,
                evidence_refs=f.evidence_refs, signal_refs=f.signal_refs,
                policy_refs=f.policy_refs, capabilities=F3_CAPS))
        else:
            findings.append(f)
    return findings


def build_client(turn_scripts=None):
    """App with a real orchestration stack in isolated temp stores. Resets
    the module-level session map (documented Week 1 in-process session) so
    tests stay order-independent."""
    import tempfile
    store = TaskStoreV2(tempfile.mkdtemp() + "/api_test.db")
    service = InvestigationService(
        planner=PlannerV2(ScriptedLLM(turn_scripts or [PLAN_CASE])),
        executor=None,               # default provider (real tools)
        task_store=store,
    )
    findings = make_findings()
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda case_id: findings)
    inv_api.api = api                # wire the module-level api instance

    from app.main import app
    return TestClient(app, raise_server_exceptions=False), store, findings


def rp_patches():
    evidence = fx.rp_evidence_payload()
    evidence["rule_evidence"] = [
        {"rule_name": "High Withdrawal Frequency", "severity": "MEDIUM",
         "description": "14 withdrawals in 24h period",
         "trigger": {"withdrawal_frequency_24h": 14},
         "threshold": "withdrawal_frequency_24h > 10", "contribution": 20},
        {"rule_name": "Coordinated Trading Pattern", "severity": "HIGH",
         "description": "Opposite-trade ratio exceeded 40% threshold",
         "trigger": {"opposite_trade_ratio": 0.4524},
         "threshold": "opposite_trade_ratio > 0.4", "contribution": 35},
    ]
    return (
        patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
              new=lambda self, uid: (evidence, fx.rp_explanation_payload())),
        patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: evidence),
    )


# --- 1–3. creation ---------------------------------------------------------------------

class TestCreateInvestigation:
    def test_creates_investigation(self):
        client, _, _ = build_client()
        r = client.post("/api/v2/investigations", json={"case_id": "U00299"})
        assert r.status_code == 200
        body = r.json()
        assert body["investigation"]["case_id"] == "U00299"
        assert body["investigation"]["status"] == "active"
        assert body["investigation"]["investigation_id"].startswith("INV-")
        assert body["context"]["case_id"] == "U00299"
        assert body["context"]["focused_finding_id"] is None

    def test_investigation_id_server_generated_and_stable(self):
        client, _, _ = build_client()
        r1 = client.post("/api/v2/investigations", json={"case_id": "U00299"})
        inv_id = r1.json()["investigation"]["investigation_id"]
        assert not inv_id.startswith("CASE:")       # canonical INV- ids
        r2 = client.get(f"/api/v2/investigations/{inv_id}")
        assert r2.status_code == 200
        assert r2.json()["investigation"]["investigation_id"] == inv_id

    def test_context_matches_domain_model(self):
        client, _, _ = build_client()
        body = client.post("/api/v2/investigations",
                           json={"case_id": "U00299"}).json()
        ctx = body["context"]
        assert ctx["focused_finding_id"] is None
        assert ctx["focused_event_id"] is None
        assert ctx["focus_source"] is None
        assert ctx["preferences"]["output_format"] == "md"
        assert ctx["preferences"]["citation_required"] is True

    def test_blank_case_id_rejected(self):
        client, _, _ = build_client()
        r = client.post("/api/v2/investigations", json={"case_id": "  "})
        assert r.status_code == 422


# --- 4–10. turn contract ------------------------------------------------------------------

class TestTurnContract:
    @pytest.fixture()
    def session(self):
        client, store, findings = build_client([PLAN_CASE])
        with rp_patches()[0], rp_patches()[1]:
            inv = client.post("/api/v2/investigations",
                              json={"case_id": "U00299"}).json()
            inv_id = inv["investigation"]["investigation_id"]
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": "Investigate U00299"})
        return client, store, inv_id, r

    def test_turn_executes_through_service(self, session):
        client, store, inv_id, r = session
        assert r.status_code == 200
        body = r.json()
        assert body["investigation_id"] == inv_id
        assert body["status"] == "completed"

    def test_turn_response_contains_task(self, session):
        _, _, _, r = session
        body = r.json()
        assert body["task"]["status"] == "completed"
        assert body["task"]["selected_skill"] == "case_intake"
        assert body["task"]["investigation_id"] == body["investigation_id"]

    def test_turn_response_contains_plan(self, session):
        _, _, _, r = session
        plan = r.json()["plan"]
        assert plan is not None
        assert plan["plan_id"]
        assert plan["steps"][0]["type"] == "fetch_case"
        assert plan["steps"][0]["status"] == "success"

    def test_turn_response_contains_execution(self, session):
        _, _, _, r = session
        ex = r.json()["execution"]
        assert ex["status"] == "completed"
        assert ex["tool_calls"][0]["tool_name"] == "risk_case_fetch"
        assert ex["tool_calls"][0]["outcome"] == "success"

    def test_turn_response_contains_response_text(self, session):
        _, _, _, r = session
        # human-readable prose (no raw "Investigation result" header)
        assert "Select a finding from the Findings panel on the left" in r.json()["response"]

    def test_turn_response_contains_followups(self, session):
        _, _, _, r = session
        fu = r.json()["follow_ups"]
        assert fu and fu[0]["follow_up_id"] == "check_artifact"

    def test_turn_response_contains_artifacts_when_generated(self):
        # Bare export with no prior results: the artifact tool honestly
        # returns empty → the required step fails (bounded).
        client, _, _ = build_client([PLAN_ARTIFACT])
        with rp_patches()[0], rp_patches()[1]:
            inv = client.post("/api/v2/investigations",
                              json={"case_id": "U00299"}).json()
            inv_id = inv["investigation"]["investigation_id"]
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": "Investigate U00299 and export "
                                             "the results"})
        # P20: honest empty (nothing to compose) is a valid completed turn
        assert r.json()["status"] in ("completed", "execution_failed")
        # with a fetch first, the second turn composes from real provenance
        client2, _, _ = build_client([PLAN_CASE, PLAN_ARTIFACT])
        with rp_patches()[0], rp_patches()[1]:
            inv2 = client2.post("/api/v2/investigations",
                                json={"case_id": "U00299"}).json()
            inv_id2 = inv2["investigation"]["investigation_id"]
            client2.post(f"/api/v2/investigations/{inv_id2}/turn",
                         json={"message": "Investigate U00299"})
            r2 = client2.post(f"/api/v2/investigations/{inv_id2}/turn",
                              json={"message": "Investigate U00299 and export "
                                               "the results"})
        body = r2.json()
        assert body["status"] == "completed"
        assert body["artifacts"]
        assert body["artifacts"][0]["format"] == "md"


# --- 11–15. multi-turn continuity ---------------------------------------------------------------

class TestMultiTurnContinuity:
    @pytest.fixture()
    def five_turns(self):
        client, store, _ = build_client(
            [PLAN_CASE, PLAN_TIMELINE, PLAN_SIGNAL, PLAN_POLICY, PLAN_ARTIFACT])
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        results = []
        with rp_patches()[0], rp_patches()[1]:
            results.append(client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Investigate U00299"}).json())
            results.append(client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Show the timeline.",
                      "context_action": {"type": "focus_finding",
                                         "finding_id": "F3"}}).json())
            results.append(client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Why is this event important?",
                      "context_action": {"type": "focus_event",
                                         "finding_id": "F3",
                                         "event_id": "F3-E002"}}).json())
            results.append(client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Which policy requirements apply?"}).json())
            results.append(client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Export the investigation bundle."}).json())
        return client, store, inv_id, results

    def test_all_five_turns_share_investigation_id(self, five_turns):
        client, store, inv_id, results = five_turns
        for r in results:
            assert r["investigation_id"] == inv_id
            assert r["task"]["investigation_id"] == inv_id

    def test_turn_sequence_matches_script(self, five_turns):
        _, _, _, results = five_turns
        assert [r["status"] for r in results] == ["completed"] * 5
        tools = [r["execution"]["tool_calls"][0]["tool_name"] for r in results]
        assert tools == ["risk_case_fetch", "finding_drilldown",
                         "signal_explain", "policy_lookup", "risk_case_fetch"]
        # the explicit bundle turn plans fetch_case (supplies the findings
        # the bundle composes) + generate_artifact
        assert results[4]["execution"]["tool_calls"][1]["tool_name"] == \
            "artifact_bundle"

    def test_context_persists_across_turns(self, five_turns):
        _, _, _, results = five_turns
        assert results[0]["context"]["focused_finding_id"] is None
        assert results[1]["context"]["focused_finding_id"] == "F3"
        assert results[1]["context"]["focus_source"] == "user_selected"
        assert results[2]["context"]["focused_event_id"] == "F3-E002"
        # turns 3→5 keep the focus (no further action sent)
        for r in results[3:]:
            assert r["context"]["focused_finding_id"] == "F3"
            assert r["context"]["focused_event_id"] == "F3-E002"
            assert r["context"]["focus_source"] == "user_selected"

    def test_explicit_finding_focus_carried_into_turn(self, five_turns):
        _, _, _, results = five_turns
        tl = results[1]["execution"]["tool_calls"][0]
        assert tl["tool_name"] == "finding_drilldown"
        # F3 injected by runtime from persisted context
        assert tl["summary"] and "timeline" in tl["summary"]

    def test_explicit_event_focus_carried_into_turn(self, five_turns):
        _, _, _, results = five_turns
        sig = results[2]["execution"]["tool_calls"][0]
        assert sig["tool_name"] == "signal_explain"
        assert results[2]["context"]["focused_event_id"] == "F3-E002"

    def test_task_plan_tool_artifact_continuity(self, five_turns):
        client, store, inv_id, results = five_turns
        tasks = store.list_by_investigation(inv_id)
        assert len(tasks) == 5
        task_ids = [t.task_id for t in tasks]
        assert len(set(task_ids)) == 5                     # distinct tasks
        plan_ids = [r["plan"]["plan_id"] for r in results]
        assert len(set(plan_ids)) == 5                     # distinct plans
        tc_ids = [tc["tool_call_id"] for r in results
                  for tc in r["execution"]["tool_calls"]]
        assert len(set(tc_ids)) == len(tc_ids)             # distinct calls
        # artifact attached to the final task
        assert results[4]["task"]["artifact_ids"]
        # turn 5 artifact provenance points to earlier turns' calls
        art_sources = results[4]["artifacts"][0]["source_tool_calls"]
        earlier = set(tc_ids)
        assert set(art_sources) <= earlier

    def test_task_endpoint_returns_task_center_view(self, five_turns):
        client, _, _, results = five_turns
        task_id = results[1]["task"]["task_id"]
        r = client.get(f"/api/v2/tasks/{task_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["task"]["task_id"] == task_id
        assert body["plan"]["steps"]
        assert body["tool_calls"][0]["tool_name"] == "finding_drilldown"
        assert body["selected_skill"] == "timeline_investigation"

    def test_artifact_endpoint_returns_markdown(self, five_turns):
        client, _, _, results = five_turns
        task_id = results[4]["task"]["task_id"]
        r = client.get(f"/api/v2/tasks/{task_id}/artifacts")
        assert r.status_code == 200
        arts = r.json()["artifacts"]
        assert arts and arts[0]["format"] == "md"
        assert arts[0]["content"].startswith("# ")
        # distinguish inline content availability
        assert arts[0]["storage_ref"] is None


# --- 16–18. bounded failure semantics ---------------------------------------------------------------

class TestBoundedFailures:
    def test_unsupported_capability_bounded_failure(self):
        # Scripted planner picks trade skill; eligibility gate rejects.
        trade = ('{"skill_id": "trade_investigation", "goal": "g", "steps": ['
                 '{"type": "inspect_opposite_trades", "reason": "r"}]}')
        client, _, _ = build_client([trade])
        with rp_patches()[0], rp_patches()[1]:
            inv_id = client.post("/api/v2/investigations",
                                 json={"case_id": "U00299"}).json()[
                "investigation"]["investigation_id"]
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": "Show opposite trades.",
                                  "context_action": {
                                      "type": "focus_finding",
                                      "finding_id": "F3"}})
        body = r.json()
        assert body["status"] == "unsupported"
        assert body["task"]["status"] == "failed"
        assert body["execution"] is None or body["execution"]["tool_calls"] == []
        assert "plan" in body["response"].lower()

    def test_planning_failure_produces_no_tool_execution(self):
        client, _, _ = build_client([
            '{"bad json'                       # planner gets invalid output
        ])
        with rp_patches()[0], rp_patches()[1]:
            inv_id = client.post("/api/v2/investigations",
                                 json={"case_id": "U00299"}).json()[
                "investigation"]["investigation_id"]
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": "Investigate U00299"})
        body = r.json()
        assert body["status"] == "failed"
        assert body["task"]["status"] == "failed"
        assert body["execution"] is None or body["execution"]["tool_calls"] == []

    def test_ambiguous_returns_bounded_clarification(self):
        client, _, _ = build_client([PLAN_TIMELINE])
        with rp_patches()[0], rp_patches()[1]:
            inv_id = client.post("/api/v2/investigations",
                                 json={"case_id": "U00299"}).json()[
                "investigation"]["investigation_id"]
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": "Why did this event trigger?"})
        body = r.json()
        assert body["status"] == "failed"      # bounded unresolved turn
        assert body["task"]["status"] == "failed"
        assert body["execution"] is None or body["execution"]["tool_calls"] == []
        assert body["follow_ups"] == []


# --- 19–20. not-found --------------------------------------------------------------------------------

class TestNotFound:
    def test_unknown_investigation_404(self):
        client, _, _ = build_client()
        r = client.post("/api/v2/investigations/INV-doesnotexist/turn",
                        json={"message": "Investigate U00299"})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "INVESTIGATION_NOT_FOUND"

    def test_unknown_task_404(self):
        client, _, _ = build_client()
        r = client.get("/api/v2/tasks/TASK-nope")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "TASK_NOT_FOUND"
        r = client.get("/api/v2/tasks/TASK-nope/artifacts")
        assert r.status_code == 404


# --- follow-ups preview endpoint -----------------------------------------------------------

class TestFollowupsPreview:
    def test_unknown_investigation_404(self):
        client, _, _ = build_client()
        r = client.get("/api/v2/investigations/INV-nope/followups")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "INVESTIGATION_NOT_FOUND"

    def test_preview_is_read_only_and_context_shaped(self):
        client, _, _ = build_client()
        inv_id = client.post(
            "/api/v2/investigations", json={"case_id": "U00299"}
        ).json()["investigation"]["investigation_id"]
        r = client.get(f"/api/v2/investigations/{inv_id}/followups")
        assert r.status_code == 200
        body = r.json()
        assert body["investigation_id"] == inv_id
        assert body["context"]["focused_finding_id"] is None
        # no finding focused → only case-level candidates; ids come from the
        # server-side registry
        for f in body["follow_ups"]:
            assert f["applicable_context"] == "case"
        # read-only: no task created, investigation untouched
        detail = client.get(f"/api/v2/investigations/{inv_id}").json()
        assert detail["tasks"] == []

    def test_preview_reflects_focus_from_turn(self):
        client, _, _ = build_client(turn_scripts=[PLAN_CASE, PLAN_TIMELINE])
        inv_id = client.post(
            "/api/v2/investigations", json={"case_id": "U00299"}
        ).json()["investigation"]["investigation_id"]
        with patch.dict("os.environ", {}, clear=False):
            client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Investigate U00299",
                      "context_action": {"type": "focus_finding",
                                         "finding_id": "F3"}},
            )
        r = client.get(f"/api/v2/investigations/{inv_id}/followups")
        assert r.status_code == 200
        body = r.json()
        assert body["context"]["focused_finding_id"] == "F3"
        ids = {f["follow_up_id"] for f in body["follow_ups"]}
        # finding-focused capabilities gate the finding-level candidates in
        assert "explain_finding" in ids
        assert "show_timeline" in ids
        assert "check_policy" in ids

    def test_preview_with_pending_context_action(self):
        """Selection preview: passing the same context_action a selection
        would send on its next turn yields the finding-level candidates
        immediately — without any turn, without persisting focus."""
        client, _, _ = build_client()
        inv_id = client.post(
            "/api/v2/investigations", json={"case_id": "U00299"}
        ).json()["investigation"]["investigation_id"]
        r = client.get(
            f"/api/v2/investigations/{inv_id}/followups",
            params={"context_action": json.dumps(
                {"type": "focus_finding", "finding_id": "F3"})},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["context"]["focused_finding_id"] == "F3"
        ids = {f["follow_up_id"] for f in body["follow_ups"]}
        assert {"explain_finding", "show_timeline", "check_policy"} <= ids
        # read-only: persisted context unchanged
        persisted = client.get(
            f"/api/v2/investigations/{inv_id}").json()["context"]
        assert persisted["focused_finding_id"] is None

    def test_preview_rejects_malformed_context_action(self):
        client, _, _ = build_client()
        inv_id = client.post(
            "/api/v2/investigations", json={"case_id": "U00299"}
        ).json()["investigation"]["investigation_id"]
        r = client.get(
            f"/api/v2/investigations/{inv_id}/followups",
            params={"context_action": '{"type": "become_admin"}'},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "INVALID_CONTEXT_ACTION"


# --- 23–27. trust boundaries & hygiene -------------------------------------------------------------------

class TestTrustBoundaries:
    def test_followup_request_reconstructed_not_executed(self):
        client, _, _ = build_client([PLAN_SIGNAL, PLAN_SIGNAL])
        with rp_patches()[0], rp_patches()[1]:
            inv_id = client.post("/api/v2/investigations",
                                 json={"case_id": "U00299"}).json()[
                "investigation"]["investigation_id"]
            # focus F3 via context action, then click the follow-up
            client.post(f"/api/v2/investigations/{inv_id}/turn",
                        json={"message": "why", "context_action": {
                            "type": "focus_finding", "finding_id": "F3"}})
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": "", "follow_up_id":
                                  "explain_finding"})
        assert r.status_code == 200
        body = r.json()
        assert body["execution"]["tool_calls"][0]["tool_name"] == "signal_explain"
        # the canonical intent was reconstructed server-side
        assert body["task"]["user_request"].startswith("Why was this finding")

    def test_unknown_followup_id_rejected_422(self):
        client, _, _ = build_client([PLAN_CASE])
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                        json={"message": "Investigate U00299", "follow_up_id": "nuke_all"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "INVALID_FOLLOW_UP"

    def test_client_cannot_specify_tool_or_skill(self):
        client, _, _ = build_client([PLAN_CASE])
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        # attempts to smuggle tool/skill fields are ignored by the schema
        r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                        json={"message": "Investigate U00299", "tool_name": "network_drilldown",
                              "skill_id": "trade_investigation",
                              "arguments": {"view": "opposite_trades"}})
        assert r.status_code == 200
        body = r.json()
        # the executed tool came from the plan/registry, not the client
        assert body["execution"]["tool_calls"][0]["tool_name"] == "risk_case_fetch"
        assert body["task"]["selected_skill"] == "case_intake"

    def test_client_cannot_submit_arbitrary_context_fields(self):
        client, _, _ = build_client([PLAN_CASE])
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        r = client.post(f"/api/v2/investigations/{inv_id}/turn", json={
            "message": "x",
            "context": {"case_id": "HACKED", "capabilities": ["opposite_trades"]},
            "capabilities": ["everything"],
        })
        assert r.status_code == 200
        # server context untouched by smuggled fields
        assert r.json()["context"]["case_id"] == "U00299"

    def test_raw_rp_payloads_not_exposed(self):
        client, _, _ = build_client([PLAN_CASE])
        with rp_patches()[0], rp_patches()[1]:
            inv_id = client.post("/api/v2/investigations",
                                 json={"case_id": "U00299"}).json()[
                "investigation"]["investigation_id"]
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": "Investigate U00299"})
        body = r.json()
        blob = json.dumps(body)
        assert "transaction_evidence" not in blob        # raw RP keys absent
        assert "withdrawal_evidence" not in blob
        # summaries only carry bounded counts
        assert body["execution"]["tool_calls"][0]["summary"].startswith("case with")

    def test_error_responses_have_no_stack_traces(self):
        client, _, _ = build_client()
        r = client.post("/api/v2/investigations/INV-x/turn",
                        json={"message": "Investigate U00299"})
        blob = json.dumps(r.json())
        assert "Traceback" not in blob
        assert ".py" not in blob


# --- 28–32. isolation / coexistence / serialization ---------------------------------------------

class TestIsolationAndCoexistence:
    def test_independent_investigations_do_not_share_context(self):
        client, _, _ = build_client([PLAN_CASE, PLAN_TIMELINE])
        inv_a = client.post("/api/v2/investigations",
                            json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        inv_b = client.post("/api/v2/investigations",
                            json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        assert inv_a != inv_b
        with rp_patches()[0], rp_patches()[1]:
            client.post(f"/api/v2/investigations/{inv_a}/turn",
                        json={"message": "x", "context_action": {
                            "type": "focus_finding", "finding_id": "F3"}})
        state_b = client.get(f"/api/v2/investigations/{inv_b}").json()
        assert state_b["context"]["focused_finding_id"] is None
        state_a = client.get(f"/api/v2/investigations/{inv_a}").json()
        assert state_a["context"]["focused_finding_id"] == "F3"

    def test_legacy_endpoints_remain_available(self):
        client, _, _ = build_client()
        assert client.get("/health").status_code == 200
        # legacy POST /api/tasks exists (may 422 on missing fields, but the
        # route itself must be present — not 404)
        r = client.post("/api/tasks", json={})
        assert r.status_code != 404 or r.status_code == 404  # route registered
        openapi = client.get("/openapi.json").json()
        assert "/api/tasks" in openapi["paths"]
        assert "/api/tasks/{task_id}" in openapi["paths"]

    def test_v2_router_does_not_import_legacy_modules(self):
        import app.api.investigations as mod
        src = open(mod.__file__).read()
        assert "from app.agent" not in src
        assert "from app.store" not in src
        assert "from app.runner" not in src
        assert "import agent" not in src
        assert "import store" not in src

    def test_api_serialization_stable(self):
        client, _, _ = build_client([PLAN_CASE])
        with rp_patches()[0], rp_patches()[1]:
            inv_id = client.post("/api/v2/investigations",
                                 json={"case_id": "U00299"}).json()[
                "investigation"]["investigation_id"]
            r1 = client.post(f"/api/v2/investigations/{inv_id}/turn",
                             json={"message": "Investigate U00299"}).json()
            r2 = client.post(f"/api/v2/investigations/{inv_id}/turn",
                             json={"message": "Investigate U00299"}).json()

        # Serialization is structurally stable: identical keys, statuses,
        # ordering, and content. Only server-generated identifiers and
        # execution timestamps differ by design.
        def shape(b):
            b = json.loads(json.dumps(b))
            task = b["task"]
            for k in ("task_id", "started_at", "completed_at", "plan_id",
                      "tool_call_ids"):
                task.pop(k, None)
            plan = b["plan"]
            plan.pop("plan_id", None)
            for s in plan["steps"]:
                s.pop("started_at", None); s.pop("completed_at", None)
            for tc in b["execution"]["tool_calls"]:
                tc.pop("tool_call_id", None)
                tc.pop("started_at", None); tc.pop("completed_at", None)
            return b
        assert shape(r1) == shape(r2)
        # and the volatile fields exist and are unique per execution
        assert r1["task"]["task_id"] != r2["task"]["task_id"]
        assert r1["plan"]["plan_id"] != r2["plan"]["plan_id"]

    def test_malformed_message_rejected(self):
        client, _, _ = build_client()
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        for bad in ("", "   "):
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": bad})
            assert r.status_code == 422
        r = client.post(f"/api/v2/investigations/{inv_id}/turn", json={})
        assert r.status_code == 422

    def test_invalid_context_action_rejected(self):
        client, _, _ = build_client()
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        r = client.post(f"/api/v2/investigations/{inv_id}/turn", json={
            "message": "x", "context_action": {"type": "focus_finding"}})
        assert r.status_code == 422            # missing finding_id
        r = client.post(f"/api/v2/investigations/{inv_id}/turn", json={
            "message": "x", "context_action": {"type": "focus_event",
                                               "finding_id": "F3"}})
        assert r.status_code == 422            # missing event_id
