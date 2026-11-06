"""GET /tasks/{id}/results — contract-owned timeline delivery signal.

2026-11-03 real incident (TASK-66d942582954, U00033 "what contributed to
ML Pattern detection score?"): the conversation card attaches its
structured Timeline section from this payload. The payload must carry
whether the user's ACCEPTED GOAL actually required timeline delivery —
same contract truth the response composer uses — so a re-plan's
unrelated timeline probe (69 trade events from finding_drilldown) can
never again be rendered under an unrelated score-explanation answer.

Pins:
- a contract whose output requires timeline_events → signal True;
- an ML-score-explanation contract (ml_signal_explanation only) → False;
- no contract (turn-local / legacy) → False;
- the results evidence entries are returned unchanged (provenance and
  the events themselves are never dropped by the signal).
"""

import json

from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopState
from app.api.investigations import InvestigationAPI
from app.goal_resolution import (ClaimKind, GoalContract,
                                 RequiredOutput, ScopeLevel)
from app.skills import EvidenceType
from app.task_store_v2 import TaskStoreV2


def _state(task_id, case, outputs):
    contract = None
    if outputs is not None:
        contract = GoalContract(
            contract_id="GC-test0001", subject_ref=case, outputs=outputs)
    return AgentLoopState(
        task_id=task_id, investigation_id=f"INV-{task_id}",
        case_id=case, goal="test goal", goal_contract=contract,
        observations=[])


def _timeline_output():
    return RequiredOutput(
        output_id="O1", claim="the transaction timeline for this case",
        claim_kind=ClaimKind.RETRIEVAL, subject_ref="U00033",
        scope=ScopeLevel.CASE_LEVEL,
        evidence_requirements=[EvidenceType.TIMELINE_EVENTS])


def _ml_output():
    return RequiredOutput(
        output_id="O1", claim="ML Pattern detection score contributors",
        claim_kind=ClaimKind.EXPLANATION, subject_ref="U00033",
        scope=ScopeLevel.CASE_LEVEL,
        evidence_requirements=[EvidenceType.ML_SIGNAL_EXPLANATION])


def _results(tmp_path, state):
    """Persist the state + task and call the read-model directly (the
    unit under test is the payload builder, not HTTP routing)."""
    from app.models import Investigation, TaskStatusV2, TaskV2
    store = TaskStoreV2(db_path=tmp_path / "signal.db")
    store.save_investigation(Investigation(
        investigation_id=state.investigation_id, case_id=state.case_id))
    store.create(TaskV2(task_id=state.task_id,
                        investigation_id=state.investigation_id,
                        case_id=state.case_id, user_request="test goal",
                        status=TaskStatusV2.COMPLETED))
    store.save_agent_state(state)
    return InvestigationAPI(store=store).get_task_results(state.task_id)


def test_timeline_contract_signals_required(tmp_path):
    payload = _results(tmp_path, _state("TASK-tl1", "U00033",
                                        [_timeline_output()]))
    assert payload["timeline_delivery_required"] is True


def test_ml_score_contract_does_not_signal(tmp_path):
    payload = _results(tmp_path, _state("TASK-ml1", "U00033",
                                        [_ml_output()]))
    assert payload["timeline_delivery_required"] is False


def test_no_contract_defaults_false(tmp_path):
    payload = _results(tmp_path, _state("TASK-nc1", "U00033", None))
    assert payload["timeline_delivery_required"] is False


def test_results_evidence_untouched_by_signal(tmp_path):
    # the signal never drops evidence: timeline events still ride results
    from app.models import Investigation, TaskStatusV2, TaskV2
    store = TaskStoreV2(db_path=tmp_path / "sig2.db")
    state = _state("TASK-ev1", "U00033", [_timeline_output()])
    store.save_investigation(Investigation(
        investigation_id=state.investigation_id, case_id=state.case_id))
    store.create(TaskV2(task_id=state.task_id,
                        investigation_id=state.investigation_id,
                        case_id=state.case_id, user_request="test goal",
                        status=TaskStatusV2.COMPLETED))
    store.save_agent_state(state)
    # persist a tool call + session so result_view can project the events
    from app.models import ToolCallV2, ToolCallStatusV2, ToolResult, \
        ToolResultOutcome
    call = ToolCallV2(
        tool_call_id="TC-S1-x", investigation_id=state.investigation_id,
        task_id=state.task_id,
        tool_name="finding_drilldown",
        status=ToolCallStatusV2.SUCCESS,
        arguments={"view": "timeline", "finding_id": "F1"},
        result=ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": "F1", "total_events": 1,
                  "events": [{"event_id": "F1-E001",
                              "summary": "BUY trade of 1 BTC"}]},
            evidence_refs=[]))
    session = {"tool_calls": [call.model_dump()]}
    store.save_session(state.investigation_id, session)
    payload = InvestigationAPI(store=store).get_task_results(
        state.task_id)
    assert payload["timeline_delivery_required"] is True
    tl = [r for r in payload["results"] if r.get("timeline_events")]
    assert tl and tl[0]["timeline_events"], "evidence must stay in results"
    assert json.dumps(tl[0]["timeline_events"]).find("BUY trade") >= 0
