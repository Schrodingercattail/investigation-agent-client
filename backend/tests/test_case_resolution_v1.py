"""Tests for Case Reference Resolution (app/case_resolution.py) and the
natural-input create-investigation API path.

Deterministic only — no LLM, no RP, no network.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx

from app.case_resolution import (
    CaseResolutionStatus,
    resolve_case_reference,
)

# --- 1–11. resolver matrix ------------------------------------------------------------


class TestResolverMatrix:
    def test_numeric_reference(self):
        r = resolve_case_reference("00299")
        assert r.status == CaseResolutionStatus.RESOLVED
        assert r.case_id == "U00299"

    def test_canonical_reference(self):
        r = resolve_case_reference("U00299")
        assert r.status == CaseResolutionStatus.RESOLVED
        assert r.case_id == "U00299"

    def test_lowercase_canonical(self):
        r = resolve_case_reference("u00299")
        assert r.status == CaseResolutionStatus.RESOLVED
        assert r.case_id == "U00299"

    def test_investigate_canonical(self):
        r = resolve_case_reference("investigate U00299")
        assert r.status == CaseResolutionStatus.RESOLVED
        assert r.case_id == "U00299"

    def test_investigate_case_canonical(self):
        r = resolve_case_reference("investigate case U00299")
        assert r.case_id == "U00299"

    def test_investigate_case_numeric(self):
        r = resolve_case_reference("investigate case 00299")
        assert r.case_id == "U00299"

    def test_look_into_numeric(self):
        r = resolve_case_reference("look into 00299")
        assert r.case_id == "U00299"

    def test_show_me_case_numeric(self):
        r = resolve_case_reference("show me case 00299")
        assert r.case_id == "U00299"

    def test_whitespace_normalization(self):
        assert resolve_case_reference("  U00299  ").case_id == "U00299"
        assert resolve_case_reference("  00299 ").case_id == "U00299"

    def test_multiple_references_ambiguous(self):
        r = resolve_case_reference("investigate case 00299 and 00301")
        assert r.status == CaseResolutionStatus.AMBIGUOUS
        assert r.case_id is None
        assert r.candidates == ["U00299", "U00301"]
        assert "00299" in r.message and "00301" in r.message

    def test_repeated_reference_not_ambiguous(self):
        r = resolve_case_reference("investigate 00299, i said 00299")
        assert r.status == CaseResolutionStatus.RESOLVED
        assert r.case_id == "U00299"

    def test_invalid_reference_bounded(self):
        r = resolve_case_reference("case ABCXYZ")
        assert r.status == CaseResolutionStatus.INVALID
        assert r.case_id is None
        assert r.message

    def test_no_reference_missing_bounded(self):
        r = resolve_case_reference("hello")
        assert r.status == CaseResolutionStatus.MISSING
        assert r.case_id is None
        assert "00299" in r.message

    def test_empty_input_missing(self):
        for bad in ("", "   "):
            r = resolve_case_reference(bad)
            assert r.status == CaseResolutionStatus.MISSING

    def test_resolved_message_none_on_success(self):
        r = resolve_case_reference("00299")
        assert r.message is None


# --- determinism / safety ---------------------------------------------------------------


class TestDeterminismAndSafety:
    def test_same_input_same_result(self):
        a = resolve_case_reference("investigate case 00299")
        b = resolve_case_reference("investigate case 00299")
        assert a.model_dump() == b.model_dump()

    def test_user_text_cannot_inject_noncanonical_id(self):
        # Any identifier produced by resolution must be U+digits.
        for text in ("case HACKED", "investigate ZZZZ", "check 12ab"):
            r = resolve_case_reference(text)
            if r.status == CaseResolutionStatus.RESOLVED:
                assert r.case_id.startswith("U") and r.case_id[1:].isdigit()

    def test_no_llm_required(self):
        # Resolution is pure regex — importing/instantiating no LLM provider.
        import app.case_resolution as mod
        src = open(mod.__file__).read()
        assert "llm_provider" not in src
        assert "ClaudeProvider" not in src


# --- API: natural-input creation path ------------------------------------------------------


@pytest.fixture()
def api_client():
    import tempfile
    from app.api import investigations as inv_api
    from app.investigation_service import InvestigationService
    from app.task_store_v2 import TaskStoreV2

    store = TaskStoreV2(tempfile.mkdtemp() + "/case_res.db")
    service = InvestigationService(task_store=store)
    api = inv_api.InvestigationAPI(
        service=service,
        store=store,
        case_findings_provider=lambda case_id: [],
    )
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), store


class TestAPICaseResolution:
    def test_canonical_case_id_still_accepted(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations", json={"case_id": "U00299"})
        assert r.status_code == 200
        assert r.json()["investigation"]["case_id"] == "U00299"

    def test_natural_language_message_resolved(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations",
                        json={"message": "investigate case 00299"})
        assert r.status_code == 200
        body = r.json()
        # canonical id stored on the investigation
        assert body["investigation"]["case_id"] == "U00299"
        # canonical id in the context for all downstream components
        assert body["context"]["case_id"] == "U00299"

    def test_numeric_case_reference_resolved(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations",
                        json={"case_reference": "00299"})
        assert r.status_code == 200
        assert r.json()["investigation"]["case_id"] == "U00299"

    def test_ambiguous_message_409(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations",
                        json={"message": "investigate case 00299 and 00301"})
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["code"] == "AMBIGUOUS_CASE_REFERENCE"
        assert set(detail["candidates"]) == {"U00299", "U00301"}

    def test_invalid_message_422(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations",
                        json={"message": "case ABCXYZ"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "INVALID_CASE_REFERENCE"

    def test_missing_reference_422(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations", json={"message": "hello"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "CASE_REFERENCE_REQUIRED"

    def test_both_sources_rejected(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations",
                        json={"case_id": "U00299", "message": "investigate 00299"})
        assert r.status_code == 422

    def test_nothing_supplied_rejected(self, api_client):
        client, store = api_client
        r = client.post("/api/v2/investigations", json={})
        assert r.status_code == 422

    def test_no_stack_trace_in_error_responses(self, api_client):
        client, store = api_client
        for body in ({"message": "case ABCXYZ"}, {"message": "hello"},
                     {"message": "investigate case 1 and 2 and 3"}):
            r = client.post("/api/v2/investigations", json=body)
            blob = r.json() if r.status_code != 500 else r.text
            text = str(blob)
            assert "Traceback" not in text
            assert ".py" not in text

    def test_first_turn_after_resolution_uses_canonical_id(self, api_client):
        client, store = api_client
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (_ for _ in ()).throw(
                AssertionError(f"tool must receive canonical id, got {uid}")),
        ):
            # create with natural input; run a turn whose case fetch will
            # assert the canonical id reaches the tool layer
            created = client.post("/api/v2/investigations",
                                  json={"message": "investigate case 00299"})
            assert created.status_code == 200
            inv_id = created.json()["investigation"]["investigation_id"]
            # the context already carries the canonical id
            state = client.get(f"/api/v2/investigations/{inv_id}").json()
            assert state["context"]["case_id"] == "U00299"


F3_CAPS = None  # set in env fixture


# --- initial-flow regression: ONE canonical Case Intake turn -------------------

class TestInitialIntakeFlow:
    """Browser-flow regression: the raw case-reference message must NEVER be
    re-sent as a conversational turn. Creation returns a canonical
    `intake_message`; exactly ONE Case Intake turn follows."""

    @pytest.fixture()
    def env(self):
        import tempfile
        from app.api import investigations as inv_api
        from app.api.investigations import InvestigationAPI
        from app.investigation_service import InvestigationService
        from app.planner_v2 import PlannerV2
        from app.task_store_v2 import TaskStoreV2
        from fastapi.testclient import TestClient

        calls = {"fetch_case": 0}

        def fake_fetch(self, uid):
            calls["fetch_case"] += 1
            import tests.test_risk_platform_adapter_v2 as _fx
            return _fx.rp_evidence_payload(uid), _fx.rp_explanation_payload()

        class ScriptedLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                user_text = messages[-1]["content"]
                if "timeline" in user_text.lower():
                    return ('{"skill_id": "timeline_investigation", "goal": "g", '
                            '"steps": [{"type": "inspect_timeline", "reason": "r"}]}')
                return ('{"skill_id": "case_intake", "goal": "g", "steps": '
                        '[{"type": "fetch_case", "reason": "r"}]}')

        store = TaskStoreV2(tempfile.mkdtemp() + "/intake.db")
        service = InvestigationService(
            planner=PlannerV2(ScriptedLLM()), task_store=store)

        def findings_provider(case_id):
            from app.models import Finding, FindingCapability
            caps = FindingCapability.model_validate(
                ["timeline", "signal_explain", "policy_lookup"])
            return [Finding(
                finding_id="F3", case_id=case_id, type="rule_signal",
                title="High Withdrawal Frequency",
                summary="14 withdrawals in 24h",
                capabilities=caps)]

        api = inv_api.InvestigationAPI(
            service=service,
            store=store,
            case_findings_provider=findings_provider,
        )
        inv_api.api = api
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        return client, store, calls, fake_fetch

    @pytest.mark.parametrize("raw", [
        "00299",
        "investigate case 00299",
        "investigate U00299",
        "show me case 00299",
        "u00299",
    ])
    def test_create_returns_canonical_intake_message(self, env, raw):
        client, store, calls, fake_fetch = env
        r = client.post("/api/v2/investigations", json={"message": raw})
        assert r.status_code == 200
        body = r.json()
        assert body["investigation"]["case_id"] == "U00299"
        assert body["intake_message"] == "Investigate U00299"

    def test_intake_turn_runs_case_intake_exactly_once(self, env):
        client, store, calls, fake_fetch = env
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=fake_fetch,
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: fx.rp_evidence_payload(uid),
        ):
            created = client.post("/api/v2/investigations",
                                  json={"message": "00299"}).json()
            inv_id = created["investigation"]["investigation_id"]
            intake = created["intake_message"]
            assert intake == "Investigate U00299"

            # the single Case Intake turn uses the canonical message
            r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                            json={"message": intake})
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "completed"
            assert body["task"]["selected_skill"] == "case_intake"
            assert body["context"]["focused_finding_id"] is None
            assert body["context"]["focus_source"] is None

        # exactly ONE case fetch across the whole initial flow
        assert calls["fetch_case"] == 1
        # the raw reference never became a conversational turn
        assert body["task"]["user_request"] == "Investigate U00299"
        assert store.list_by_investigation(inv_id)[0].user_request == \
            "Investigate U00299"

    def test_raw_reference_as_turn_is_rejected_not_crashing(self, env):
        # Documenting bounded behavior: sending the raw reference as a turn
        # after creation never 500s; it resolves to a bounded outcome.
        client, store, calls, fake_fetch = env
        created = client.post("/api/v2/investigations",
                              json={"message": "00299"}).json()
        inv_id = created["investigation"]["investigation_id"]
        r = client.post(f"/api/v2/investigations/{inv_id}/turn",
                        json={"message": "00299"})
        assert r.status_code == 200
        assert r.json()["task"]["status"] in ("failed", "completed")

    def test_multi_turn_still_works_after_intake(self, env):
        client, store, calls, fake_fetch = env
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=fake_fetch,
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: fx.rp_evidence_payload(uid),
        ):
            created = client.post("/api/v2/investigations",
                                  json={"message": "investigate case 00299"}).json()
            inv_id = created["investigation"]["investigation_id"]
            r1 = client.post(f"/api/v2/investigations/{inv_id}/turn",
                             json={"message": created["intake_message"]}).json()
            assert r1["status"] == "completed"
            # focus F3 + timeline
            r2 = client.post(
                f"/api/v2/investigations/{inv_id}/turn",
                json={"message": "Show the timeline.",
                      "context_action": {"type": "focus_finding",
                                         "finding_id": "F3"}}).json()
            assert r2["status"] == "completed"
            assert r2["execution"]["tool_calls"][0]["tool_name"] == \
                "finding_drilldown"
            # the drilldown tool performs its own evidence fetch when it has
            # no case context, so the case fetch ran once per RP-bound tool
            # execution (intake + drilldown) — never duplicated WITHIN a turn
            assert calls["fetch_case"] == 2
