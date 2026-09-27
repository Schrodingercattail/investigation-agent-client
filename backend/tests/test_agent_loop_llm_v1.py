"""Agent Loop Layer B — real-LLM decision proposal quality.

Runs the REAL configured LLM through the loop's bounded decision proposer
(same convention as the planner wrapper: real LLM, deterministic state,
no network beyond the LLM, no tool execution). Validates that the model's
PROPOSALS distinguish task completion from step completion and choose
sensible next directions — the LoopPolicy enforcement itself is already
deterministically tested in Layer A.
"""

import pytest

from app.agent_loop import (
    AgentLoopState,
    LLMDecisionProposer,
)


def state_with(observation, *, pending=(), goal="analyze cross-account "
               "trades behind this finding", criteria="question_answered",
               iteration=1):
    return AgentLoopState(
        task_id="T-B", investigation_id="CASE:U00299", case_id="U00299",
        goal=goal, criteria_kind=criteria,
        plan_steps=[{"step_id": f"S{i}", "type": t, "status": "pending",
                     "tool_name": "finding_drilldown"}
                    for i, t in enumerate(pending, 2)],
        observations=[observation], iteration=iteration)


MATCH_FOUND = {
    "tool_name": "cross_account_trade_analysis",
    "step_type": "analyze_cross_account_trades",
    "outcome": "success", "semantic_status": "MATCH_FOUND",
    "summary": {"candidates_found": 1, "candidates_emitted": 1,
                "candidates": [{"candidate_id": "CATA-001",
                                "match_type": "1_to_1",
                                "account_a": "U00299",
                                "account_b": "U00123"}],
                "totals": {"transactions_retrieved": 12,
                           "total_matched": 12},
                "retrieval": {"status": "complete", "truncated": False,
                              "truncated_reasons": []},
                "data_quality": {"records_input": 12,
                                 "records_usable": 12,
                                 "records_excluded_untimestamped": 0,
                                 "transactions_matched": 2}},
    "error": None,
}

INSUFFICIENT = {
    "tool_name": "signal_explain", "step_type": "explain_signal",
    "outcome": "success", "semantic_status": None,
    "summary": {"evidence_missing": True},
    "error": None, "next_data_needed": ["ML model score for this case"],
}

NO_MATCH = {
    "tool_name": "cross_account_trade_analysis",
    "step_type": "analyze_cross_account_trades",
    "outcome": "success", "semantic_status": "NO_MATCH",
    "summary": {"candidates_found": 0, "candidates_emitted": 0,
                "totals": {"transactions_retrieved": 8,
                           "total_matched": 8},
                "retrieval": {"status": "truncated", "truncated": True,
                              "truncated_reasons":
                                  ["account_scope_listing_bounded"]},
                "data_quality": {}},
    "error": None,
}


class TestLLMDecisionProposals:
    def test_completion_vs_step_completion(self):
        """A satisfied goal (answer in hand) should propose STOP or RE_PLAN
        (model variance on verify-then-stop — both policy-valid; the
        deterministic LoopPolicy enforces STOP either way, proven in Layer
        A). It must never propose BLOCK: nothing is blocked. The same
        result under a goal needing more scope must not propose STOP."""
        proposer = LLMDecisionProposer()
        satisfied = proposer.propose(
            state_with(MATCH_FOUND, pending=()))
        assert satisfied.decision in ("STOP", "RE_PLAN"), satisfied
        assert satisfied.decision != "BLOCK"

        more_scope = proposer.propose(
            state_with(MATCH_FOUND, pending=("explain_signal",),
                       goal="check every related account for cross-account "
                            "opposite trades"))
        assert more_scope.decision in ("CONTINUE", "RE_PLAN"), more_scope
        assert more_scope.decision != "BLOCK"

    def test_insufficient_data_never_completes_or_blocks_with_a_path(self):
        proposer = LLMDecisionProposer()
        proposal = proposer.propose(
            state_with(INSUFFICIENT, pending=("inspect_timeline",)))
        assert proposal.decision in ("CONTINUE", "RE_PLAN"), proposal

    def test_no_match_on_unanswered_goal_proposes_replan(self):
        proposer = LLMDecisionProposer()
        proposal = proposer.propose(
            state_with(NO_MATCH, pending=(),
                       goal="find which related accounts were involved in "
                            "cross-account opposite trading"))
        assert proposal.decision == "RE_PLAN", proposal
        assert proposal.reason.strip(), "user-safe reason required"
