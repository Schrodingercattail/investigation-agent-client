"""Repro harness (pre-fix evidence): language leakage in the GSP composer.

Case A — English ML explain request composes a Chinese answer body.
Case B — English withdrawal-comparison request composes a Chinese About
         header (deterministic claim_description).
"""
import os
os.environ["ENABLE_GOAL_SEMANTIC_CONTRACT"] = "true"

from app.agent_loop import AgentLoopState, compose_loop_response
from app.goal_resolution import (
    GoalRepresentation, GoalResolutionStatus, build_goal_contract,
    deterministic_contract_proposal,
)


def cjk_ratio(text: str) -> float:
    t = "".join(text.split())
    if not t:
        return 0.0
    cjk = sum(1 for c in t if "一" <= c <= "鿿")
    return cjk / len(t)


print("=== Case A: English ML signal explain ===")
state = AgentLoopState(
    task_id="T", investigation_id="I", case_id="U00047",
    goal="please explain what ML detected in details",
    response_language="en",
    focused_finding_id="F1",
    focused_finding_title="ML Pattern Detection.",
)
state.observations = [{
    "tool_name": "signal_explain", "outcome": "success",
    "semantic_status": "ML",
    "for_outputs": ["O1"],
    "summary": {
        "ml_score": 99.6, "detection_method": "LightGBM",
        "attribution_available": False,
        "available_feature_values": {"ml_score": 99.6,
                                     "trade_frequency_7d": 69},
    },
}]
from app.goal_resolution import RequiredOutput, EvidenceType, ScopeLevel
contract = type(state).model_fields  # noqa (placeholder)
from app.goal_resolution import GoalContract
state.goal_contract = GoalContract(
    contract_id="GC-test", subject_ref="F1",
    outputs=[RequiredOutput(
        output_id="O1", claim="what ML detected",
        claim_kind="explanation", claim_description="ML detection basis",
        subject_ref="F1", scope="finding_level",
        evidence_requirements=[EvidenceType.ML_SIGNAL_EXPLANATION],
        satisfaction="satisfied")],
)
out = compose_loop_response(state)
print(out[:600])
print(f"--> CJK share of body: {cjk_ratio(out):.0%}")

print()
print("=== Case B: English withdrawal destination comparison ===")
req = ("Are the withdrawal addresses all the same or different address? "
       "send all withdrawal records to me.")
proposal = deterministic_contract_proposal(req, None, "U00047")
rep = GoalRepresentation(
    case_id="U00047",
    goal_resolution=GoalResolutionStatus.INFERRED.value,
    goal_type="identify_supporting_records",
    subject_type="case", subject_ref="U00047", source="heuristic",
    request_text=req, contract_proposal=proposal)
contract_b, err = build_goal_contract(rep, case_id="U00047")
print("accept err:", err)
if contract_b:
    for o in contract_b.outputs:
        print(f"  O: claim_description={o.claim_description!r}")
