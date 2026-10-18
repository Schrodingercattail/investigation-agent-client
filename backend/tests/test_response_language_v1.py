"""Per-turn response language selection — bounded regression locks.

- Detection priority: explicit request > body language > None (default).
- The decision uses the CURRENT request only (never history/tools).
- zh/None keep the existing default face; en renders English variants.
- The matcher/business logic is untouched by language.
"""

from app.response_language import detect_response_language


class TestDetection:
    def test_zh_body(self):
        assert detect_response_language("为什么高频提现是风险项？") == "zh"
        assert detect_response_language(
            "把关联账户的明细发我，并且查询关联账户中是否有对冲交易") == "zh"

    def test_en_body(self):
        assert detect_response_language(
            "Explain what risks the ML Pattern Detection detected") == "en"
        assert detect_response_language(
            "Show me the withdrawal records") == "en"

    def test_explicit_overrides_body(self):
        assert detect_response_language(
            "用英文回答：为什么高频提现是风险项？") == "en"
        assert detect_response_language(
            "为什么高频提现是风险项？请用英文回答") == "en"
        assert detect_response_language(
            "Explain the ML finding 用中文回答") == "zh"

    def test_technical_terms_do_not_flip(self):
        # a mostly-EN request containing tool names / ids stays en
        assert detect_response_language(
            "explain signal_explain for finding F1") == "en"

    def test_undeterminable_returns_none(self):
        assert detect_response_language("") is None
        assert detect_response_language("   ") is None
        assert detect_response_language("12345 + 6789") is None

    def test_mixed_with_cjk_majority_is_zh(self):
        assert detect_response_language(
            "解释一下 ML Pattern Detection 检测到了什么风险") == "zh"


from app.agent_loop import _gsp_render_output_facts
from app.goal_resolution import RequiredOutput, ScopeLevel
from app.skills import EvidenceType


def _out(reqs):
    return RequiredOutput(
        output_id="O1", claim="c", claim_kind="retrieval",
        subject_ref="UC", scope="case_level", evidence_requirements=reqs)


class TestKnownGSPBranchesEn:
    def test_shared_ip_branch_en_and_zh(self):
        summary = {"shared_ip_attributions": {"UA": ["ip1"], "UB": ["ip2"]}}
        en = _gsp_render_output_facts(
            _out([EvidenceType.SHARED_IP_EVIDENCE]), summary, lang="en")
        assert any("Shared IP records" in l for l in en)
        zh = _gsp_render_output_facts(
            _out([EvidenceType.SHARED_IP_EVIDENCE]), summary, lang="zh")
        assert any("共享 IP 记录" in l for l in zh)

    def test_policy_branch_en_and_zh(self):
        # the REAL observation projection keys (associated_citations/
        # match_count) — the composer's old `matches` key never exists
        summary = {"associated_citations": [{"citation_id": 2}],
                   "match_count": 1, "finding_policy_status": "associated"}
        en = _gsp_render_output_facts(
            _out([EvidenceType.POLICY_REFERENCES]), summary, lang="en")
        assert any("policy references were retrieved" in l.lower()
                   for l in en)
        zh = _gsp_render_output_facts(
            _out([EvidenceType.POLICY_REFERENCES]), summary, lang="zh")
        assert any("已检索相关政策引用" in l for l in zh)

    def test_case_summary_branch_en_and_zh(self):
        summary = {"finding_count": 7}
        en = _gsp_render_output_facts(
            _out([EvidenceType.CASE_SUMMARY]), summary, lang="en")
        assert any("7 identified risk findings" in l for l in en)
        zh = _gsp_render_output_facts(
            _out([EvidenceType.CASE_SUMMARY]), summary, lang="zh")
        assert any("案例共识别 7 个风险发现" in l for l in zh)


from app.planner_v2 import PlannerV2
from app.models import InvestigationContext
from app.domain_tools import risk_case_fetch


class TestMetricQuestionProposalGuard:
    """P2: a metric question whose predicate mentions 关联账户 ("是关联
    账户的对冲交易比例吗") is a question ABOUT the metric — the related-
    account deliverable is only extracted when the request itself asks
    for the accounts (delivery intent)."""

    def test_metric_question_suppresses_network_aspect(self):
        from app.goal_resolution import deterministic_contract_proposal
        Q = ("opposite-trade ratio of 34.29%是关联账户的对冲交易比例吗，"
             "这是怎么计算的？")
        p = deterministic_contract_proposal(Q, None, "U90008",
                                            case_findings=[])
        claims = [o["claim"] for o in p["required_outputs"]]
        assert len(claims) == 1
        assert "反向交易率" in claims[0]
        assert not any("关联" in c for c in claims)

    def test_genuine_combo_still_delivers_both(self):
        from app.goal_resolution import deterministic_contract_proposal
        Q = "反向交易率是怎么计算的，把关联账户明细也发我"
        p = deterministic_contract_proposal(Q, None, "U90008",
                                            case_findings=[])
        claims = [o["claim"] for o in p["required_outputs"]]
        assert any("反向交易率" in c for c in claims)
        assert any("关联" in c for c in claims)


class TestPlannerContractPrecedence:
    def test_planner_skips_related_accounts_when_contract_lacks_it(self):
        """Contract [metric] + request mentioning 关联账户 → the planner
        must not plan the related-account analysis (contract precedence)."""
        from app.goal_resolution import GoalContract, ScopeLevel
        findings = list((risk_case_fetch(
            case_id="U90008").data or {}).get("findings") or [])
        outputs = [RequiredOutput(
            output_id="O1", claim="metric", claim_kind="explanation",
            subject_ref="U90008", scope="account_level",
            evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS])]
        gc = GoalContract(contract_id="GC-P", goal_type=None,
                          subject_ref="U90008", outputs=outputs)
        ctx = InvestigationContext(case_id="U90008")
        Q = "opposite-trade ratio of 34.29%是关联账户的对冲交易比例吗"
        p = PlannerV2().plan(
            Q, ctx, [s.skill_id for s in __import__(
                "app.skills", fromlist=["eligible_skills_for_finding"])
                .eligible_skills_for_finding(None)],
            finding_capabilities=None, goal_contract=gc,
            case_findings=findings)
        types = [s.type for s in p.steps]
        assert "analyze_related_accounts" not in types

    def test_planner_plans_related_accounts_when_contract_wants_it(self):
        from app.goal_resolution import GoalContract, ScopeLevel
        findings = list((risk_case_fetch(
            case_id="U90008").data or {}).get("findings") or [])
        outputs = [RequiredOutput(
            output_id="O2", claim="related", claim_kind="retrieval",
            subject_ref="U90008", scope="case_level",
            evidence_requirements=[EvidenceType.NETWORK_RELATIONS])]
        gc = GoalContract(contract_id="GC-P2", goal_type=None,
                          subject_ref="U90008", outputs=outputs)
        ctx = InvestigationContext(case_id="U90008")
        Q = "列出这个案例的关联账号"
        p = PlannerV2().plan(
            Q, ctx, [s.skill_id for s in __import__(
                "app.skills", fromlist=["eligible_skills_for_finding"])
                .eligible_skills_for_finding(None)],
            finding_capabilities=None, goal_contract=gc,
            case_findings=findings)
        assert any(s.type == "analyze_related_accounts" for s in p.steps)
