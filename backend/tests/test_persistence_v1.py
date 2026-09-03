"""Persistent investigation sessions — history, delete, restart regression.

Verifies true SQLite persistence (survives store re-instantiation), the
history list, explicit deletion, and isolation between investigations.
No LLM, no RP, no network.
"""

import tempfile
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


PLAN_CASE = ('{"skill_id": "case_intake", "goal": "g", "steps": '
             '[{"type": "fetch_case", "reason": "r"}]}')
PLAN_TIMELINE = ('{"skill_id": "timeline_investigation", "goal": "g", "steps": '
                 '[{"type": "inspect_timeline", "reason": "r"}]}')


class ScriptedLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        user_text = messages[-1]["content"]
        if "timeline" in user_text.lower():
            return PLAN_TIMELINE
        return PLAN_CASE


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "persist.db"


@pytest.fixture()
def findings():
    return [
        Finding(finding_id="F3", case_id="U00299", type="rule_signal",
                title="High Withdrawal Frequency", summary="14 withdrawals",
                capabilities=FindingCapability.model_validate(
                    ["timeline", "signal_explain", "policy_lookup"])),
        Finding(finding_id="F1", case_id="U00010", type="rule_signal",
                title="New account with high activity", summary="5 days old",
                capabilities=FindingCapability.model_validate(
                    ["timeline", "signal_explain", "policy_lookup"])),
    ]


def make_api(db_path, findings):
    store = TaskStoreV2(db_path)
    service = InvestigationService(
        planner=PlannerV2(ScriptedLLM()), task_store=store)
    by_case = {f.case_id: [f] for f in findings}
    api = inv_api.InvestigationAPI(
        service=service, store=store,
        case_findings_provider=lambda case_id: by_case.get(case_id, []))
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), api


def rp():
    """Hermetic: patch both RP touch points (case fetch + evidence fetch).
    Returns a single context manager."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            fx.rp_evidence_payload(uid), fx.rp_explanation_payload())))
    stack.enter_context(patch(
        "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
        new=lambda self, uid, expose_complete_records=False: fx.rp_evidence_payload(uid)))
    return stack


def run_case_turn(client, inv_id, message):
    return client.post(f"/api/v2/investigations/{inv_id}/turn",
                       json={"message": message}).json()


# --- 1–4. creation + multi-turn persistence -----------------------------------

class TestPersistence:
    def test_create_persists_investigation(self, db_path, findings):
        client, _ = make_api(db_path, findings)
        r = client.post("/api/v2/investigations", json={"case_id": "U00299"})
        assert r.status_code == 200
        inv_id = r.json()["investigation"]["investigation_id"]
        got = client.get(f"/api/v2/investigations/{inv_id}")
        assert got.status_code == 200
        assert got.json()["investigation"]["investigation_id"] == inv_id

    def test_context_persists_after_turns(self, db_path, findings):
        client, _ = make_api(db_path, findings)
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        with rp():
            run_case_turn(client, inv_id, "Investigate U00299")
            r2 = client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Show the timeline.",
                      "context_action": {"type": "focus_finding",
                                         "finding_id": "F3"}}).json()
        assert r2["status"] == "completed"
        # focus persisted via context action
        assert r2["context"]["focused_finding_id"] == "F3"
        # GET reconstructs the same context
        got = client.get(f"/api/v2/investigations/{inv_id}").json()
        assert got["context"]["focused_finding_id"] == "F3"
        assert got["context"]["case_id"] == "U00299"

    def test_multiple_turns_persisted_with_history(self, db_path, findings):
        client, _ = make_api(db_path, findings)
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        with rp():
            run_case_turn(client, inv_id, "Investigate U00299")
            run_case_turn(client, inv_id, "Show the timeline.")
        got = client.get(f"/api/v2/investigations/{inv_id}").json()
        assert len(got["tasks"]) == 2
        # conversation is reconstructable: task + plan + tool calls per turn
        for task in got["tasks"]:
            assert task["user_request"]
        assert got["tasks"][0]["user_request"] != got["tasks"][1]["user_request"]


# --- 5–10. history list + delete ------------------------------------------------

class TestHistoryAndDelete:
    def _create_two(self, db_path, findings):
        client, _ = make_api(db_path, findings)
        inv_a = client.post("/api/v2/investigations",
                            json={"case_id": "U00299"}).json()
        inv_b = client.post("/api/v2/investigations",
                            json={"case_id": "U00010"}).json()
        return client, inv_a["investigation"], inv_b["investigation"]

    def test_list_returns_stored_investigations(self, db_path, findings):
        client, a, b = self._create_two(db_path, findings)
        listing = client.get("/api/v2/investigations").json()
        ids = {i["investigation_id"] for i in listing["investigations"]}
        assert {a["investigation_id"], b["investigation_id"]} <= ids

    def test_list_ordering_deterministic(self, db_path, findings):
        import time
        client, a, b = self._create_two(db_path, findings)
        first = client.get("/api/v2/investigations").json()
        time.sleep(0.05)
        # touch b so its updated_at is newest
        run_case_turn(client, b["investigation_id"], "Investigate U00010")
        second = client.get("/api/v2/investigations").json()
        first_ids = [i["investigation_id"] for i in first["investigations"]]
        second_ids = [i["investigation_id"] for i in second["investigations"]]
        assert second_ids.index(b["investigation_id"]) <= \
            first_ids.index(b["investigation_id"])
        # repeated calls stable
        assert second_ids == [i["investigation_id"] for i in
                              client.get("/api/v2/investigations")
                              .json()["investigations"]]

    def test_delete_one_preserves_other(self, db_path, findings):
        client, a, b = self._create_two(db_path, findings)
        with rp():
            run_case_turn(client, a["investigation_id"], "Investigate U00299")
        r = client.delete(f"/api/v2/investigations/{a['investigation_id']}")
        assert r.status_code == 200
        # deleted gone from history
        ids = {i["investigation_id"] for i in client.get(
            "/api/v2/investigations").json()["investigations"]}
        assert a["investigation_id"] not in ids
        assert b["investigation_id"] in ids
        # deleted cannot be reopened
        assert client.get(
            f"/api/v2/investigations/{a['investigation_id']}").status_code == 404
        # other investigation fully intact
        got_b = client.get(
            f"/api/v2/investigations/{b['investigation_id']}").json()
        assert got_b["investigation"]["case_id"] == "U00010"

    def test_deleted_tasks_not_leaked_to_other(self, db_path, findings):
        client, a, b = self._create_two(db_path, findings)
        with rp():
            run_case_turn(client, a["investigation_id"], "Investigate U00299")
        client.delete(f"/api/v2/investigations/{a['investigation_id']}")
        got_b = client.get(
            f"/api/v2/investigations/{b['investigation_id']}").json()
        assert got_b["tasks"] == []

    def test_delete_unknown_404(self, db_path, findings):
        client, _ = make_api(db_path, findings)
        assert client.delete(
            "/api/v2/investigations/INV-nope").status_code == 404

    def test_isolated_contexts(self, db_path, findings):
        client, a, b = self._create_two(db_path, findings)
        # focus a finding in A only
        client.post(f"/api/v2/investigations/{a['investigation_id']}/turn",
                    json={"message": "Investigate U00299",
                          "context_action": {"type": "focus_finding",
                                             "finding_id": "F3"}})
        ctx_a = client.get(
            f"/api/v2/investigations/{a['investigation_id']}").json()["context"]
        ctx_b = client.get(
            f"/api/v2/investigations/{b['investigation_id']}").json()["context"]
        assert ctx_a["focused_finding_id"] == "F3"
        assert ctx_b["focused_finding_id"] is None
        assert ctx_b["case_id"] == "U00010"


# --- 14–17. restart persistence ---------------------------------------------------

class TestRestartPersistence:
    def test_data_survives_store_reinstantiation(self, db_path, findings):
        client, api = make_api(db_path, findings)
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        with rp():
            run_case_turn(client, inv_id, "Investigate U00299")
            client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Show the timeline.",
                      "context_action": {"type": "focus_finding",
                                         "finding_id": "F3"}})

        # "restart": fresh TaskStoreV2 + fresh API over the same DB file
        client2, _ = make_api(db_path, findings)
        got = client2.get(f"/api/v2/investigations/{inv_id}").json()
        assert got["investigation"]["case_id"] == "U00299"
        assert len(got["tasks"]) == 2
        # context fully restored
        assert got["context"]["focused_finding_id"] == "F3"
        assert got["context"]["preferences"]["citation_required"] is True
        # conversation reconstruction data (plans + tool calls) restored
        for task in got["tasks"]:
            detail = client2.get(f"/api/v2/tasks/{task['task_id']}").json()
            assert detail["plan"] is not None
            assert detail["tool_calls"]
            assert detail["plan"]["steps"]

    def test_continued_turn_uses_restored_context(self, db_path, findings):
        client, _ = make_api(db_path, findings)
        inv_id = client.post("/api/v2/investigations",
                             json={"case_id": "U00299"}).json()[
            "investigation"]["investigation_id"]
        with rp():
            run_case_turn(client, inv_id, "Investigate U00299")
            client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Show the timeline.",
                      "context_action": {"type": "focus_finding",
                                         "finding_id": "F3"}})

        # restart, then continue with another turn — must use restored
        # context (F3 focus persisted before restart), not a fresh default
        client2, _ = make_api(db_path, findings)
        with rp():
            r3 = run_case_turn(client2, inv_id, "Show the timeline.")
        assert r3["status"] == "completed"
        assert r3["context"]["focused_finding_id"] == "F3"
        # task history continues to accumulate in the same investigation
        got = client2.get(f"/api/v2/investigations/{inv_id}").json()
        assert len(got["tasks"]) == 3

    def test_session_persists_across_reinstantiation_directly(self, db_path):
        store1 = TaskStoreV2(db_path)
        store1.save_investigation(
            __import__("app.models", fromlist=["Investigation"]).Investigation(
                investigation_id="INV-1", case_id="U00299",
                created_at="2026-08-29T00:00:00Z",
                updated_at="2026-08-29T00:00:00Z"))
        store1.save_session("INV-1", {"context": {"case_id": "U00299"},
                                      "tool_calls": []})
        store2 = TaskStoreV2(db_path)
        assert store2.get_session("INV-1") == {"context": {"case_id": "U00299"},
                                               "tool_calls": []}

    def test_single_database_file(self, tmp_path, findings):
        # V2 persistence uses exactly one DB file (no second store).
        client, api = make_api(tmp_path / "one.db", findings)
        client.post("/api/v2/investigations", json={"case_id": "U00299"})
        import os
        leftovers = [f for f in os.listdir(tmp_path)
                     if f.endswith(".db") and f != "one.db"]
        assert leftovers == []
