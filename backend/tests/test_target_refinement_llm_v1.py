"""SP-M2-1 — Layer B stability evaluation: real LLM refinement stability.

Repeats the ambiguous-candidate refinement scenarios against the REAL
configured LLM and the REAL Risk Platform candidate set (U00010) to
measure decision stability (SP-M2-1 success criterion B).
"""

import json

import httpx
import pytest

from app.adapters.risk_platform import RiskPlatformAdapter
from app.goal_resolution import understand_goal
from app.target_refinement import refine_target

CASE = "U00010"


def real_candidates():
    """Build refinement candidates from the REAL Risk Platform findings
    via the canonical domain tool boundary (sync)."""
    from app.domain_tools import risk_case_fetch
    result = risk_case_fetch(case_id=CASE)
    candidates = []
    for i, f in enumerate(result.data.get("findings") or [], 1):
        candidates.append({"id": f.finding_id, "title": f.title,
                           "description": (f.summary or "")[:200]})
    return candidates


def numbered(candidates):
    return [{**c, "id": f"CAND-{i}"} for i, c in enumerate(candidates, 1)]


SCENARIOS = [
    ("E3-shared-devices", "有哪些账户和这个账户共享了设备",
     "identify_related_accounts"),
    ("E5-hedging", "我想查看下这个账户和其他账户之间有没有关联对冲交易",
     "cross_account_check"),
    ("network-structure", "这个账户的 Linked Account Network 具体是什么？",
     "inspect_network_structure"),
]


@pytest.mark.parametrize("name,text,expected_goal", SCENARIOS)
def test_refinement_stability(name, text, expected_goal):
    candidates = numbered(real_candidates())
    llm = __import__("app.llm_provider", fromlist=["ClaudeProvider"]) \
        .ClaudeProvider()
    goal = understand_goal(text, case_id=CASE, llm=llm, use_llm=True)
    assert goal.goal_type, f"[{name}] goal must be classified"
    decisions = []
    for _ in range(3):
        r = refine_target(text, candidates, goal.project(), llm,
                          min_confidence=0.7)
        decisions.append(r.decision)
        if r.decision == "SELECT":
            assert r.target_id in {c["id"] for c in candidates}
    # stability: all three runs agree on the decision
    assert len(set(decisions)) == 1, decisions
