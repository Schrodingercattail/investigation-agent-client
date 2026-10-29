"""WP-CG-1 — Compound Extraction Protocol tests.

L1 coverage (implementation plan §7): scripted-LLM payloads against the
REAL `understand_goal` — the SAME single goal-understanding call now
carries compound multi-deliverable proposals to the system, without a
second semantic call, without changing the single-goal contract, and
with flag-OFF byte-identical prompts.

Verified HERE (the CG-1 boundary): the structured proposal ARRIVES —
resolution COMPOUND, goal_type None, proposal attached in request
order. Contract acceptance hardening (≥2 rule, canonical dedup,
all-or-nothing on field validity) is WP-CG-2 and deliberately not
asserted here.
"""

import json

import pytest

from app.config import settings
from app.goal_resolution import (
    GoalRepresentation, GoalResolutionStatus, understand_goal)
from app.models import InvestigationContext


class _ScriptLLM:
    """One scripted payload per call; counts calls; captures prompts."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.prompts = []

    def generate(self, messages=None, max_tokens=0, temperature=0.0):
        self.calls += 1
        self.prompts.append(messages[0]["content"] if messages else "")
        return self.payload


class _ExplodingLLM:
    def generate(self, messages=None, max_tokens=0, temperature=0.0):
        raise RuntimeError("provider returned no text")


def _out(claim, kind="retrieval", subject="U00010", scope="case_level",
         reqs=("case_summary",), description=None):
    out = {"claim": claim, "claim_kind": kind, "subject_ref": subject,
           "scope": scope, "evidence_requirements": list(reqs)}
    if description:
        out["claim_description"] = description
    return out


def _canonical_network_claim():
    """The system-canonical related-accounts claim (goal_resolution)."""
    from app.goal_resolution import _CLAIM_RELATED_ACCOUNTS
    return _CLAIM_RELATED_ACCOUNTS


def _payload(resolution="INFERRED", goal_type="policy_check",
             ambiguous=(), outputs=None, reason="r"):
    return json.dumps({
        "goal_resolution": resolution,
        "goal_type": goal_type,
        "ambiguous_goal_types": list(ambiguous),
        "reason": reason,
        "required_outputs": list(outputs or []),
    })


COMPOUND_OUTPUTS = [
    _out("hedge_presence_assessment", kind="existence_check",
         reqs=["opposite_trade_relations"]),
    _out("related_accounts_list", kind="retrieval",
         reqs=["network_relations"]),
]


@pytest.fixture()
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)


@pytest.fixture()
def flag_off(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", False)


class TestSingleGoalBackwardCompat:
    def test_inferred_single_goal_with_proposal_unchanged(self, flag_on):
        llm = _ScriptLLM(_payload(outputs=[_out(
            "risk_level_report", reqs=["case_summary",
                                        "rule_explanation"])]))
        rep = understand_goal("这个账号的风险情况", resolved_finding_id=None,
                              case_id="U00010", llm=llm)
        assert llm.calls == 1                       # SAME single call
        assert rep.goal_resolution == "INFERRED"
        assert rep.goal_type == "policy_check"
        assert rep.contract_proposal is not None
        outs = rep.contract_proposal["required_outputs"]
        assert len(outs) == 1
        assert len(outs[0]["evidence_requirements"]) == 2   # not over-split

    def test_explicit_payload_preexisting_bounded_fallback(self, flag_on):
        """Pre-existing behavior (unchanged by CG-1): the extractor has no
        EXPLICIT branch — an EXPLICIT payload falls into the bounded
        heuristic fallback. CG-1 neither fixed nor worsened it."""
        llm = _ScriptLLM(json.dumps({
            "goal_resolution": "EXPLICIT", "goal_type": "timeline",
            "ambiguous_goal_types": [], "reason": "r"}))
        rep = understand_goal("给出时间线", resolved_finding_id="F3",
                              case_id="U00010", llm=llm)
        assert rep.goal_resolution == "INFERRED"   # heuristic fallback
        assert rep.source == "heuristic"
        assert rep.contract_proposal is None


class TestCompoundExtraction:
    def test_compound_two_outputs_arrive_structured(self, flag_on):
        # Deterministic-first note: requests matching a SUPPORTED pattern
        # skip the LLM entirely (their enumeration is system-owned), so
        # this test uses a compound request with NO deterministic
        # enumeration to pin the LLM-proposal consumption path.
        llm = _ScriptLLM(_payload(
            resolution="COMPOUND", goal_type=None,
            outputs=COMPOUND_OUTPUTS,
            reason="two distinct deliverables"))
        req = ("这个账号有没有对冲交易？另外把它的关联账号也列出来。")
        rep = understand_goal(
            req, resolved_finding_id=None, case_id="U00010", llm=llm)
        assert llm.calls == 1                       # no second LLM call
        assert rep.goal_resolution == "COMPOUND"
        assert rep.goal_type is None                # AD-1
        assert rep.ambiguous_goal_types == []
        proposal = rep.contract_proposal
        assert proposal is not None
        outs = proposal["required_outputs"]
        # B-fix: the network deliverable signature-matches the candidate's
        # canonical dict, which wins (system-owned wording/facets); the
        # hedge entry has no candidate counterpart and stays as emitted.
        assert [o["claim"] for o in outs] == [
            "hedge_presence_assessment", _canonical_network_claim()]
        assert [o["evidence_requirements"] for o in outs] == [
            ["opposite_trade_relations"], ["network_relations"]]

    def test_compound_three_outputs_in_request_order(self, flag_on):
        outs = COMPOUND_OUTPUTS + [
            _out("policy_requirements", kind="retrieval",
                 reqs=["policy_references"])]
        llm = _ScriptLLM(_payload(resolution="COMPOUND", goal_type=None,
                                  outputs=outs))
        rep = understand_goal("这个账号有没有对冲交易？把关联账号列出来，"
                              "并说明适用的政策要求。",
                              resolved_finding_id=None,
                              case_id="U00010", llm=llm)
        assert rep.goal_resolution == "COMPOUND"
        outs = rep.contract_proposal["required_outputs"]
        assert [o["claim"] for o in outs] == [
            "hedge_presence_assessment", _canonical_network_claim(),
            "policy_requirements"]

    def test_swapped_order_preserved(self, flag_on):
        llm = _ScriptLLM(_payload(
            resolution="COMPOUND", goal_type=None,
            outputs=list(reversed(COMPOUND_OUTPUTS))))
        rep = understand_goal("先列出所有关联账号，再看有没有对冲交易",
                              resolved_finding_id=None,
                              case_id="U00010", llm=llm)
        outs = rep.contract_proposal["required_outputs"]
        assert [o["claim"] for o in outs] == [_canonical_network_claim(),
                                              "hedge_presence_assessment"]

    def test_enumerated_and_compound_verb_shapes(self, flag_on):
        # the protocol is wording-agnostic at the payload boundary: the
        # enumerated and compound-verb request shapes use the SAME
        # COMPOUND payload structure
        for _ in (1, 2):
            llm = _ScriptLLM(_payload(resolution="COMPOUND", goal_type=None,
                                      outputs=COMPOUND_OUTPUTS))
            rep = understand_goal("请分别回答：这个账号有没有对冲交易？"
                                  "它的关联账号有哪些？",
                                  resolved_finding_id=None,
                                  case_id="U00010", llm=llm)
            assert rep.goal_resolution == "COMPOUND"
            assert len(rep.contract_proposal["required_outputs"]) == 2

    def test_compound_projects_user_safely(self, flag_on):
        llm = _ScriptLLM(_payload(resolution="COMPOUND", goal_type=None,
                                  outputs=COMPOUND_OUTPUTS))
        rep = understand_goal("这个账号有没有对冲交易？把关联账号也列出"
                              "来。", resolved_finding_id=None,
                              case_id="U00010", llm=llm)
        projected = rep.project()
        assert projected["goal_resolution"] == "COMPOUND"
        assert "contract_proposal" not in projected   # never projected


class TestCompoundBoundaries:
    def test_genuine_ambiguous_is_not_compound(self, flag_on):
        llm = _ScriptLLM(json.dumps({
            "goal_resolution": "AMBIGUOUS", "goal_type": None,
            "ambiguous_goal_types": ["cross_account_check",
                                     "identify_related_accounts"],
            "reason": "which one?"}))
        rep = understand_goal("查一下这个", resolved_finding_id=None,
                              case_id="U00010", llm=llm)
        assert rep.goal_resolution == "AMBIGUOUS"
        assert rep.contract_proposal is None        # never an output set

    def test_compound_requires_goal_type_none(self, flag_on):
        llm = _ScriptLLM(_payload(resolution="COMPOUND",
                                  goal_type="cross_account_check",
                                  outputs=COMPOUND_OUTPUTS))
        rep = understand_goal("复合请求", resolved_finding_id=None,
                              case_id="U00010", llm=llm)
        # AD-1 violation → bounded heuristic fallback, never repaired
        assert rep.goal_resolution == "INFERRED" or \
            rep.contract_proposal is None

    def test_compound_single_output_rejected_structurally(self, flag_on):
        llm = _ScriptLLM(_payload(resolution="COMPOUND", goal_type=None,
                                  outputs=[COMPOUND_OUTPUTS[0]]))
        rep = understand_goal("复合请求", resolved_finding_id=None,
                              case_id="U00010", llm=llm)
        assert rep.goal_resolution != "COMPOUND"
        assert rep.contract_proposal is None

    def test_llm_empty_falls_back_bounded(self, flag_on):
        diagnostics = {}
        rep = understand_goal("复合请求", resolved_finding_id=None,
                              case_id="U00010", use_llm=True,
                              llm=_ExplodingLLM(), diagnostics=diagnostics)
        assert diagnostics["llm_attempted"] is True
        assert "provider returned no text" in diagnostics["llm_error"]
        assert rep.source == "heuristic"
        assert rep.contract_proposal is None        # never a proposal

    def test_flag_off_compound_payload_bounded_fallback(self, flag_off):
        llm = _ScriptLLM(_payload(resolution="COMPOUND", goal_type=None,
                                  outputs=COMPOUND_OUTPUTS))
        rep = understand_goal(
            "这个账号有没有对冲交易？另外把它的关联账号也列出来。",
            resolved_finding_id=None, case_id="U00010", llm=llm)
        # OFF never produces COMPOUND; the deterministic heuristic route
        # applies (bounded single-goal classification, no proposal)
        assert rep.goal_resolution != "COMPOUND"
        assert rep.contract_proposal is None


class TestPromptContract:
    def test_flag_on_prompt_carries_compound_rule(self, flag_on):
        llm = _ScriptLLM(_payload(outputs=COMPOUND_OUTPUTS))
        understand_goal("任意请求", resolved_finding_id=None,
                        case_id="U00010", llm=llm)
        prompt = llm.prompts[0]
        assert "required_outputs" in prompt
        assert "COMPOUND" in prompt
        assert "one required_outputs entry per" in prompt
        assert "WHICH single" in prompt             # AMBIGUOUS narrowing

    def test_flag_off_prompt_unchanged(self, flag_off):
        llm = _ScriptLLM(_payload(outputs=[_out("x")]))
        understand_goal("任意请求", resolved_finding_id=None,
                        case_id="U00010", llm=llm)
        prompt = llm.prompts[0]
        assert "required_outputs" not in prompt
        assert "COMPOUND" not in prompt             # byte-identical base

    def test_flag_on_single_call_for_compound(self, flag_on):
        llm = _ScriptLLM(_payload(resolution="COMPOUND", goal_type=None,
                                  outputs=COMPOUND_OUTPUTS))
        understand_goal("复合请求", resolved_finding_id=None,
                        case_id="U00010", llm=llm)
        assert llm.calls == 1                       # no second call, ever


class TestCheckpointIntegration:
    """Integration evidence (plan §7): a COMPOUND representation produced
    by the REAL extraction reaches the loop's goal checkpoint and — via
    the existing resolution-agnostic builder — lands as a structured
    multi-output contract in state (acceptance hardening is WP-CG-2)."""

    def test_compound_proposal_reaches_state_as_contract(
            self, monkeypatch, tmp_path):
        import tempfile
        from tests.test_al351_target_validity_guard_v1 import (
            _build_runner, _resolved_outcome, _CustomResolver)
        from tests.test_api_investigations_v1 import make_findings, rp_patches

        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        rep = GoalRepresentation(
            case_id="U00299", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="case", subject_ref="U00299",
            ambiguous_goal_types=[], reason="two deliverables",
            source="llm", request_text="req",
            contract_proposal={"required_outputs": [
                {"claim": "hedge_presence_assessment",
                 "claim_kind": "existence_check", "subject_ref": "U00299",
                 "scope": "account_level",
                 "evidence_requirements": ["opposite_trade_relations"]},
                {"claim": "related_accounts_list", "claim_kind": "retrieval",
                 "subject_ref": "U00299", "scope": "case_level",
                 "evidence_requirements": ["network_relations"]}]})

        from app.task_store_v2 import TaskStoreV2
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg1_int.db")
        findings = make_findings()
        runner = _build_runner(
            store,
            resolver=_CustomResolver(
                _resolved_outcome(evidence_kind="concept_grounding")),
            planner_scripts=(
                '{"skill_id": "case_intake", "goal": "g", "steps": '
                '[{"type": "fetch_case", "reason": "r"}]}'),
            decisions=("STOP",))
        runner.goal_resolver = lambda req, fid, case: rep
        p1, p2 = rp_patches()
        with p1, p2:
            result = runner.run("复合请求",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-CG1-INT",
                                task_id="TASK-CG1-INT",
                                findings_provider=lambda: findings)
        s = result.state
        assert s.goal_contract is not None
        assert s.goal_contract.goal_type is None        # AD-1
        assert [o.claim for o in s.goal_contract.outputs] == [
            "hedge_presence_assessment", "related_accounts_list"]
        assert s.goal_contract_decision["outcome"] == "contract_accepted"
        assert (s.goal_contract_decision["understanding"]["contract"]
                ["decision"] == "accepted")
