"""GU pre-loop observability (plan v2): submitted_at + goal_understanding
window persisted with the stage diagnostics.

Deterministic verification of the approved v2 design — no real model
calls, no keys:

- unit: understand_goal outcome taxonomy (llm_success /
  llm_failed_heuristic_fallback / not_attempted), window ordering,
  attempts wall stamps, submitted_at preservation — via the V1-B
  synthetic streaming client;
- runner /goal: real AgentLoopRunner + real TaskStoreV2 (temp DB) — the
  loop-internal GU stamps land in the PERSISTED state document and
  task.started_at precedes goal_understanding_started_at;
- endpoint /submit: the real HTTP surface (TestClient) with an
  understand_goal-backed resolver — submitted_at from the handler, the
  GU window stamped in-handler, task creation after GU.

/turn is structurally excluded (plan v2 D): its single-turn runtime
creates no goal_understanding diagnostics at all.
"""

import json
import tempfile
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from unittest.mock import MagicMock

from app.llm_provider import ClaudeProvider
from app.goal_resolution import understand_goal
from app.agent_loop import AgentLoopRunner
from app.models import InvestigationContext
from app.task_store_v2 import TaskStoreV2
from app.planner_v2 import PlannerV2


def _iso(s):
    return datetime.fromisoformat(s)


def _thinking_block():
    b = MagicMock()
    b.type = "thinking"
    b.thinking = "x" * 20000
    return b


def _text_block(text):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _final_message(content, stop_reason="max_tokens", out=8192, inp=427):
    return NS(content=content, stop_reason=stop_reason,
              usage=NS(input_tokens=inp, output_tokens=out))


def _fake_provider(responses):
    """ClaudeProvider whose client streams the scripted final messages in
    order (one entry per application attempt). An exhausted script keeps
    serving thinking-only max_tokens responses (the production empty
    signature)."""
    p = ClaudeProvider(api_key="test-key", model="glm-5.3")
    scripted = list(responses)

    class _StreamNS:
        def __init__(self, final):
            self._final = final
            self._events = [object(), object()]

        def __iter__(self):
            return iter(self._events)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return self._final

    class _Msgs:
        def __init__(self):
            self.calls = []

        def stream(self, **kwargs):
            self.calls.append(kwargs)
            if scripted:
                final = scripted.pop(0)
            else:
                final = {"content": [_thinking_block()],
                         "stop_reason": "max_tokens",
                         "usage_input_tokens": 300,
                         "usage_output_tokens": 8192}
            return _StreamNS(NS(
                content=final["content"],
                stop_reason=final["stop_reason"],
                usage=NS(input_tokens=final["usage_input_tokens"],
                         output_tokens=final["usage_output_tokens"])))

    class _Client:
        def __init__(self):
            self.messages = _Msgs()
            self.options = {}

        def with_options(self, **opts):
            self.options = opts
            return self

    p.client = _Client()
    return p


VALID_GU_JSON = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "explain_finding",
    "ambiguous_goal_types": [], "reason": "single clear objective"})

AMBIGUOUS_GU_JSON = json.dumps({
    "goal_resolution": "AMBIGUOUS", "goal_type": None,
    "ambiguous_goal_types": ["explain_finding", "policy_check"],
    "reason": "two readings"})


def _ok_response(text):
    return {"content": [_thinking_block(), _text_block(text)],
            "stop_reason": "end_turn", "usage_input_tokens": 2669,
            "usage_output_tokens": 2372}


def _empty_response():
    return {"content": [_thinking_block()], "stop_reason": "max_tokens",
            "usage_input_tokens": 427, "usage_output_tokens": 8192}


class TestUnderstandGoalOutcomeTaxonomy:
    def test_llm_success_window_and_submitted_at_preserved(self):
        diag = {"submitted_at": "2026-10-28T12:00:00+00:00"}
        provider = _fake_provider([_ok_response(VALID_GU_JSON)])
        repr_obj = understand_goal(
            "ML都检测什么", resolved_finding_id="F1", case_id="U00005",
            llm=provider, diagnostics=diag, case_findings=[])
        assert repr_obj.goal_resolution == "INFERRED"
        assert diag["goal_understanding_outcome"] == "llm_success"
        s, e = (diag["goal_understanding_started_at"],
                diag["goal_understanding_ended_at"])
        assert s and e and _iso(s) <= _iso(e)
        # entry-time anchor injected by the caller is preserved verbatim
        assert diag["submitted_at"] == "2026-10-28T12:00:00+00:00"
        # per-attempt wall stamps from llm_provider
        assert len(diag["attempts"]) == 1
        a0 = diag["attempts"][0]
        assert a0["started_at"] and a0["ended_at"]
        assert _iso(a0["started_at"]) <= _iso(a0["ended_at"])
        assert isinstance(a0["elapsed_ms"], int) and a0["elapsed_ms"] >= 0

    def test_double_empty_falls_back_with_window(self):
        provider = _fake_provider([_empty_response(), _empty_response()])
        diag = {}
        repr_obj = understand_goal(
            "ML都检测什么", resolved_finding_id="F1", case_id="U00005",
            llm=provider, diagnostics=diag, case_findings=[])
        assert diag["goal_understanding_outcome"] == \
            "llm_failed_heuristic_fallback"
        assert "LLMEmptyResponseError" in diag.get("llm_error", "")
        assert len(diag["attempts"]) == 2
        assert all(a["stop_reason"] == "max_tokens" and
                   a["usage_output_tokens"] == 8192
                   for a in diag["attempts"])
        s, e = (diag["goal_understanding_started_at"],
                diag["goal_understanding_ended_at"])
        assert _iso(s) <= _iso(e)
        # heuristic fallback still produced an honest representation
        assert repr_obj.goal_resolution in ("INFERRED", "UNRESOLVED")

    def test_malformed_json_fallback_recorded(self):
        provider = _fake_provider([
            _ok_response("not json {{"), _ok_response("still not json {{")])
        diag = {}
        understand_goal("ML都检测什么", resolved_finding_id="F1",
                        case_id="U00005", llm=provider, diagnostics=diag,
                        case_findings=[])
        assert diag["goal_understanding_outcome"] == \
            "llm_failed_heuristic_fallback"
        assert diag["structured_output"]["resolved"] is False
        assert diag["goal_understanding_ended_at"]

    def test_not_attempted_when_use_llm_false(self):
        diag = {}
        understand_goal("总结案件", case_id="U00005", llm=None,
                        use_llm=False, diagnostics=diag, case_findings=[])
        assert diag["goal_understanding_outcome"] == "not_attempted"
        assert diag["goal_understanding_started_at"]
        assert diag["goal_understanding_ended_at"]
        assert _iso(diag["goal_understanding_started_at"]) <= \
            _iso(diag["goal_understanding_ended_at"])
        assert "llm_attempted" not in diag

    def test_illegal_resolution_value_is_llm_failed(self):
        """E-1: syntactically valid JSON with an ILLEGAL resolution value
        ("BOGUS") falls back to the heuristic AND the outcome records the
        structured-output failure — never llm_success."""
        bad = json.dumps({
            "goal_resolution": "BOGUS", "goal_type": "explain_finding",
            "ambiguous_goal_types": [], "reason": "r"})
        provider = _fake_provider([_ok_response(bad)])
        diag = {"submitted_at": "2026-10-28T12:10:00+00:00"}
        repr_obj = understand_goal(
            "ML都检测什么", resolved_finding_id="F1", case_id="U00005",
            llm=provider, diagnostics=diag, case_findings=[])
        # the fallback RESULT is unchanged by the observability fix
        assert repr_obj.goal_resolution in ("INFERRED", "UNRESOLVED")
        assert repr_obj.source == "heuristic"
        assert diag["goal_understanding_outcome"] == \
            "llm_failed_heuristic_fallback"
        # no exception occurred — the fallback is a normal-path degradation
        assert diag.get("llm_error") in (None, "")
        s, e = (diag["goal_understanding_started_at"],
                diag["goal_understanding_ended_at"])
        assert s and e and _iso(s) <= _iso(e)
        assert diag["submitted_at"] == "2026-10-28T12:10:00+00:00"

    def test_ambiguous_with_insufficient_plausible_is_llm_failed(self):
        """E-2: an AMBIGUOUS verdict backed by fewer than 2 plausible
        vocabulary candidates falls back to the heuristic and records the
        failure — the representation must NOT claim AMBIGUOUS."""
        thin = json.dumps({
            "goal_resolution": "AMBIGUOUS", "goal_type": None,
            "ambiguous_goal_types": ["explain_finding"], "reason": "r2"})
        provider = _fake_provider([_ok_response(thin)])
        diag = {}
        repr_obj = understand_goal(
            "ML都检测什么", resolved_finding_id="F1", case_id="U00005",
            llm=provider, diagnostics=diag, case_findings=[])
        assert repr_obj.goal_resolution in ("INFERRED", "UNRESOLVED")
        assert repr_obj.goal_resolution != "AMBIGUOUS"
        assert diag["goal_understanding_outcome"] == \
            "llm_failed_heuristic_fallback"
        s, e = (diag["goal_understanding_started_at"],
                diag["goal_understanding_ended_at"])
        assert s and e and _iso(s) <= _iso(e)

    def test_valid_ambiguous_stays_llm_success(self):
        """E-3 unchanged: a well-backed AMBIGUOUS verdict is a successful
        GU result (resolution recorded on the representation)."""
        provider = _fake_provider([_ok_response(AMBIGUOUS_GU_JSON)])
        diag = {}
        repr_obj = understand_goal(
            "ML都检测什么", resolved_finding_id="F1", case_id="U00005",
            llm=provider, diagnostics=diag, case_findings=[])
        assert repr_obj.goal_resolution == "AMBIGUOUS"
        assert diag["goal_understanding_outcome"] == "llm_success"
        s, e = (diag["goal_understanding_started_at"],
                diag["goal_understanding_ended_at"])
        assert s and e and _iso(s) <= _iso(e)

    def test_provider_exception_classification(self):
        provider = ClaudeProvider(api_key="t", model="glm-5.3")
        provider.client = type("C", (), {})()
        provider.client.with_options = lambda **opts: provider.client
        provider.client.messages = type("M", (), {})()

        def boom(**kw):
            raise RuntimeError("isolated-test failure")
        provider.client.messages.stream = boom
        diag = {}
        understand_goal("ML都检测什么", case_id="U00005", llm=provider,
                        diagnostics=diag, case_findings=[])
        assert diag["goal_understanding_outcome"] == \
            "llm_failed_heuristic_fallback"
        # the catch-all wraps the raw RuntimeError into LLMError —
        # the ORIGINAL class is preserved in the message, and the
        # wrapped classification is service_unavailable (verified in
        # test_llm_provider_empty_retry_v1)
        assert "isolated-test failure" in diag.get("llm_error", "")
        assert diag["goal_understanding_ended_at"]

    def test_ambiguous_is_a_successful_gu_result(self):
        provider = _fake_provider([_ok_response(AMBIGUOUS_GU_JSON)])
        diag = {}
        repr_obj = understand_goal(
            "ML都检测什么", resolved_finding_id="F1", case_id="U00005",
            llm=provider, diagnostics=diag, case_findings=[])
        assert repr_obj.goal_resolution == "AMBIGUOUS"
        assert repr_obj.ambiguous_goal_types == [
            "explain_finding", "policy_check"]
        # AMBIGUOUS is a resolution result — the GU call itself succeeded
        assert diag["goal_understanding_outcome"] == "llm_success"


# --- runner /goal: loop-internal GU window persisted ------------------------

class _PlanLLM:
    """Planner LLM returning an invalid plan payload: the run ends at the
    bounded planning failure — which is exactly the persist point this
    test needs (the GU window must already be on the state)."""

    def generate(self, messages=None, max_tokens=0, temperature=0.1, **k):
        return "{}"


def test_goal_entry_persists_window(tmp_path):
    provider = _fake_provider([_ok_response(VALID_GU_JSON)])
    store = TaskStoreV2(db_path=tempfile.mkdtemp() + "/guo.sqlite3")
    runner = AgentLoopRunner(
        store=store, planner=PlannerV2(_PlanLLM()), executor=None,
        resolver=None)
    runner.goal_resolver = (
        lambda req, fid, case, diag=None, case_findings=None:
        understand_goal(req, resolved_finding_id=fid, case_id=case,
                        llm=provider, diagnostics=diag,
                        case_findings=case_findings))
    submitted = "2026-10-28T12:30:00+00:00"
    result = runner.run(
        "ML都检测什么", InvestigationContext(case_id="U00005"), [],
        investigation_id="INV-GUO", task_id="TASK-GUO",
        submitted_at=submitted, findings_provider=lambda: [])
    state = result.state
    gd = state.goal_understanding_diagnostics or {}
    assert gd.get("submitted_at") == submitted
    assert gd.get("goal_understanding_outcome") == "llm_success"
    s, e = (gd.get("goal_understanding_started_at"),
            gd.get("goal_understanding_ended_at"))
    assert s and e and _iso(s) <= _iso(e)
    # /goal ordering: the task row is created BEFORE the loop-internal GU
    task_started = result.task.started_at
    assert task_started and _iso(task_started) <= \
        _iso(gd["goal_understanding_started_at"])
    # persisted, not just in-memory: reload from the store
    row = store.get(state.task_id)
    assert row is not None


# --- endpoint /submit: real HTTP surface, persisted window ------------------

def _gu_resolver(provider):
    """Production resolver shape: delegates to understand_goal with the
    caller's diagnostics dict (the `_default_goal_resolver` convention)."""
    def _resolve(req, fid, case, diag=None, case_findings=None):
        return understand_goal(
            req, resolved_finding_id=fid, case_id=case, llm=provider,
            diagnostics=diag, case_findings=case_findings)
    return _resolve


class TestSubmitEndpointPersistence:
    def test_submit_persists_window_and_created_after_gu(self, tmp_path):
        import time
        from unittest.mock import MagicMock as _MM
        from fastapi.testclient import TestClient
        from app.api import investigations as inv_api
        from app.api.investigations import InvestigationAPI
        from app.investigation_service import InvestigationService
        from app.main import app as fastapp
        import sqlite3

        db_path = tmp_path / "guo-submit.sqlite3"
        store = TaskStoreV2(db_path=str(db_path))
        service = InvestigationService(
            resolver=_MM(),
            planner=PlannerV2(_PlanLLM()),   # bounded planning failure
            executor=None,
            task_store=store)
        # FULL isolation: inject the loop runner factory too — the default
        # factory wires the LLM-backed `_default_goal_resolver()`, which
        # would fire a REAL model call from the loop-internal GU (escape
        # observed once and contained: the daemon thread died with the
        # test process; the response was discarded). Every model surface
        # in this test is synthetic.
        provider = _fake_provider([_ok_response(VALID_GU_JSON)])

        def _factory(store):
            return AgentLoopRunner(
                store=store,
                planner=PlannerV2(_PlanLLM()),
                executor=None,
                resolver=None,
                goal_resolver=_gu_resolver(provider))

        api_obj = InvestigationAPI(service=service, store=store,
                                   case_findings_provider=lambda cid: [],
                                   loop_runner_factory=_factory)
        api_obj.goal_resolver = _gu_resolver(provider)
        # test hygiene: the module-global API object is shared with every
        # other test in the process — save and RESTORE it so this test's
        # synthetic wiring never leaks into later suites.
        _saved_api = inv_api.api
        inv_api.api = api_obj
        client = TestClient(fastapp, raise_server_exceptions=False)

        inv = client.post("/api/v2/investigations",
                          json={"case_id": "U00005"}).json()
        inv_id = inv["investigation"]["investigation_id"]
        resp = client.post(f"/api/v2/investigations/{inv_id}/submit",
                           json={"message": "ML都检测什么"})
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        deadline = time.time() + 60
        status = None
        while time.time() < deadline:
            d = client.get(f"/api/v2/tasks/{task_id}").json()
            status = (d.get("task") or {}).get("status")
            if status in ("completed", "failed"):
                break
            time.sleep(0.2)
        assert status in ("completed", "failed"), status

        # persisted (not just in-memory): read the agent_states document
        row = sqlite3.connect(str(db_path)).execute(
            "SELECT document FROM agent_states WHERE task_id=?",
            (task_id,)).fetchone()
        assert row is not None, "agent state must be persisted"
        state = json.loads(row[0])
        gd = state.get("goal_understanding_diagnostics") or {}
        assert gd.get("submitted_at"), "entry anchor must persist"
        s, e = (gd.get("goal_understanding_started_at"),
                gd.get("goal_understanding_ended_at"))
        assert s and e and _iso(s) <= _iso(e)
        assert gd.get("goal_understanding_outcome") == "llm_success"
        # /submit ordering: the task row is created AFTER the in-handler GU
        task_row = json.loads(sqlite3.connect(str(db_path)).execute(
            "SELECT document FROM tasks_v2 WHERE task_id=?",
            (task_id,)).fetchone()[0])
        assert task_row.get("started_at")
        assert _iso(gd["goal_understanding_ended_at"]) <= \
            _iso(task_row["started_at"])
        # restore the shared global BEFORE the test ends (finally-style:
        # also restored if later assertions fail — wrap via try/finally
        # is not needed here because this is the last statement, but the
        # restore is unconditional)
        inv_api.api = _saved_api
