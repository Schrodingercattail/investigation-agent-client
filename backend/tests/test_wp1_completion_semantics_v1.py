"""R2.2.3 WP-1 — G6 completion semantics + contract=None PARTIAL.

Component tests over `_compose_gsp_response` (real composer path):

- reviewed coverage (proposal_source="llm") + all outputs SATISFIED →
  goal_outcome capped at PARTIAL with the "已识别目标完成+完整性未证"
  disclosure — COMPLETE is unreachable while request completeness is
  UNKNOWN (frozen R2.2.3 matrix; explicit design limitation);
- candidate coverage keeps its existing heuristic-origin wording
  (regression);
- the outcome derivation itself is untouched (derive_goal_outcome is
  not modified; the cap is applied at the agent_loop call site).

GU retry, G7'' gate, criteria backstop, and artifact logic are not
exercised here (Phase 1 / Phase 2 already verified).
"""

import pytest

from app.agent_loop import AgentLoopState, _compose_gsp_response
from app.goal_resolution import GoalContract, RequiredOutput

CASE = "U00047"


def _output(oid, claim, kind, reqs, sat="satisfied", polarity="positive"):
    return RequiredOutput(
        output_id=oid, claim=claim, claim_kind=kind, subject_ref=CASE,
        scope="case_level", evidence_requirements=reqs,
        postcondition_facets=[], satisfaction=sat,
        polarity=polarity, detail=None)


def _state(outputs, *, intent_source, proposal_source, lang=None):
    from app.agent_loop import AgentLoopState
    kw = {"response_language": lang} if lang else {}
    # mirror build_goal_contract: contract.intent_source = proposal_source
    # (heuristic candidate → "heuristic"; LLM-authored → "llm")
    intent_source = proposal_source
    repr_obj = {"goal_type": "artifact_export",
                "goal_resolution": "INFERRED", "source": "llm",
                "request_text": "compound ask", "proposal_source":
                    proposal_source}
    return AgentLoopState(
        task_id="TASK-WP1", investigation_id="INV-WP1", case_id=CASE,
        goal="compound delivery ask",
        criteria_kind="artifact_produced",
        goal_contract=GoalContract(
            contract_id="GC-WP1", subject_ref=CASE,
            intent_source=intent_source, outputs=outputs),
        goal_contract_decision={"outcome": "contract_accepted"},
        goal_representation=repr_obj, **kw)


def _records_output(oid="O1", sat="satisfied"):
    return _output(oid, "trade records artifact", "retrieval",
                   ["transaction_records"], sat=sat)


def test_reviewed_all_satisfied_capped_partial_with_disclosure():
    """G6: reviewed coverage + all outputs SATISFIED → goal_outcome
    PARTIAL (ceiling) and the disclosure wording — never a whole-request
    completion claim."""
    st = _state([_records_output()], intent_source="llm",
                proposal_source="llm")
    text = _compose_gsp_response(st)
    assert st.goal_outcome == "partial"
    assert ("已完成所有已识别目标" in text
            and "请求完整性未经独立验证" in text)
    assert "已完整回答" not in text
    assert "本次调查目标已完整回答" not in text


def test_reviewed_en_capped_partial_with_disclosure():
    st = _state([_records_output()], intent_source="llm",
                proposal_source="llm", lang="en")
    text = _compose_gsp_response(st)
    assert st.goal_outcome == "partial"
    assert ("all identified goals were completed" in text
            and "not been independently verified" in text)
    assert "fully answered" not in text.lower()


def test_candidate_keeps_heuristic_origin_wording():
    """candidate (proposal_source=heuristic → intent_source=heuristic)
    keeps the EXISTING 情形-A wording — no regression, same PARTIAL
    ceiling."""
    st = _state([_records_output()], intent_source="heuristic",
                proposal_source="heuristic")
    text = _compose_gsp_response(st)
    assert st.goal_outcome == "partial"
    assert ("已识别的交付项均已回答" in text
            or "未能由模型完成验证" in text)
    assert st.goal_outcome != "complete"


def test_partially_supported_output_stays_partial():
    """A genuinely partially-supported output keeps PARTIAL semantics —
    unchanged by WP-1."""
    st = _state([_output("O1", "existence check", "existence_check",
                         ["opposite_trade_relations"],
                         sat="partially_supported")],
                intent_source="llm", proposal_source="llm")
    text = _compose_gsp_response(st)
    assert st.goal_outcome == "partial"


def test_g7_blocked_terminal_keeps_partial():
    """G7''-B blocked runs keep goal_outcome=PARTIAL through the legacy
    composer (no overwrite)."""
    from app.agent_loop import AgentLoopState
    state = AgentLoopState(
        task_id="TASK-WP1B", investigation_id="INV-WP1B", case_id=CASE,
        goal="compound ask", criteria_kind="artifact_produced",
        goal_contract=None,
        goal_contract_decision={"outcome": "heuristic_single_goal"},
        goal_representation={"goal_type": "artifact_export",
                             "goal_resolution": "INFERRED",
                             "source": "heuristic",
                             "proposal_source": None},
        agent_status="blocked",
        stop_reason="clarification_needed",
        goal_outcome="partial",
        observations=[],
        plan_steps=[{"step_id": "S1", "type": "fetch_case",
                     "status": "success"}],
    )
    from app.agent_loop import compose_loop_response
    text = compose_loop_response(state)
    assert state.goal_outcome == "partial"
    # R2.2.3 WP-2: the HSG planning-time clarify message asks the user to
    # supplement/restate (请补充说明/请…提出) — the clarification REQUEST
    # semantics, calibrated from the phase-1 loop-gate wording
    assert ("请补充" in text or "请确认" in text
            or "clarification" in text.lower() or "澄清" in text)
