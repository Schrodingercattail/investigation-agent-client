"""Case-summary presence must not satisfy a finding-explanation output.

Real failure (read-only forensic trace): investigation INV-727c5a264c85 /
task TASK-740e83a1b5f5 (U00010) — the user asked WHY related accounts are
a risk item + the policy original text. The plan fetched the case bundle
(+ policy) only; the CASE_SUMMARY postcondition treated `finding_count`
presence as SATISFIED for the explanation output, so a generic F1–F8
listing was delivered and the whole goal was judged COMPLETE.

Fix semantics (this file pins it):
- A case bundle still satisfies outputs whose deliverable IS the case
  context (reqs = [case_summary]) — general case-summary asks unchanged.
- When the SAME output also demands explanation-type evidence
  (rule_explanation / ml_signal_explanation / graph_cluster_facts), the
  bundle is context only: no SATISFIED from summary presence; the deeper
  requirement decides, or the existing indeterminate fallback keeps the
  output honestly unresolved (so the loop's existing corrective
  RE_PLAN coercion stays reachable — a STOP proposal over unsatisfied
  outputs is deterministically coerced, agent_loop decision rule 2).
- Real explanation evidence (any source producing the existing
  rule/signal/cluster fact shapes) still satisfies — no fixed tool.
- A compound goal with one unresolved explanation output is PARTIAL,
  never COMPLETE because the policy half succeeded.
"""

import pytest

from app.goal_postconditions import (
    GoalOutcome,
    R_UNDETERMINED,
    apply_postcondition,
    derive_goal_outcome,
    evaluate_contract_outputs,
    evaluate_output,
)
from app.goal_resolution import GoalContract
from app.goal_resolution import RequiredOutput
from app.skills import EvidenceType


def _case_bundle_obs(for_outputs=("O1",)):
    return {"tool_name": "risk_case_fetch", "outcome": "success",
            "step_type": "fetch_case", "for_outputs": list(for_outputs),
            "actual_evidence_types": ["case_summary"],
            "summary": {"finding_count": 8, "risk_level": "CRITICAL",
                        "risk_score": 86,
                        "findings": [
                            {"id": "F5",
                             "title": "Shared Device Relationships",
                             "summary": "One shared device was used."},
                            {"id": "F6", "title": "Linked Account Network",
                             "summary": "18 connected accounts."}]}}


def _signal_explain_obs(finding_id="F6", for_outputs=("O1",)):
    return {"tool_name": "signal_explain", "outcome": "success",
            "step_type": "explain_signal",
            "for_outputs": list(for_outputs),
            "actual_evidence_types": ["rule_explanation"],
            "summary": {"finding_id": finding_id,
                        "rule_name": "Linked Account Network",
                        "threshold": "linked_account_count > 10",
                        "trigger_values": {"linked_account_count": 18}}}


def _policy_obs(for_outputs=("O2",)):
    return {"tool_name": "policy_lookup", "outcome": "success",
            "step_type": "retrieve_policy", "for_outputs": list(for_outputs),
            "actual_evidence_types": ["policy_references"],
            "summary": {"matches": [{"citation_id": 3,
                                     "document": "AML.md",
                                     "section": "5.1",
                                     "snippet": "Accounts linked to "
                                                "known risky clusters…"}],
                        "finding_policy_status": "associated"}}


def _explanation_output(oid="O1", subject="U00010",
                        reqs=(EvidenceType.RULE_EXPLANATION,
                              EvidenceType.CASE_SUMMARY)):
    return RequiredOutput(
        output_id=oid, claim="解释关联账号为何是风险项",
        claim_kind="explanation", subject_ref=subject,
        scope="case_level", evidence_requirements=list(reqs))


def _policy_output(oid="O2"):
    return RequiredOutput(
        output_id=oid, claim="提供政策依据与原文", claim_kind="retrieval",
        subject_ref="U00010", scope="case_level",
        evidence_requirements=[EvidenceType.POLICY_REFERENCES])


# --- A. general case-summary requests are unchanged ------------------------

class TestBoundaryAPlainCaseSummary:
    @pytest.mark.parametrize("kind", ["retrieval", "explanation"])
    def test_summary_only_output_still_satisfied(self, kind):
        out = RequiredOutput(
            output_id="O1", claim="案件整体风险情况", claim_kind=kind,
            subject_ref="U00010", scope="case_level",
            evidence_requirements=[EvidenceType.CASE_SUMMARY])
        res = evaluate_output(out, "U00010", [_case_bundle_obs()])
        assert res.satisfaction.value == "satisfied"
        assert res.polarity == "positive"
        sat, pol, reason = apply_postcondition(res)
        assert sat.value == "satisfied"


# --- B. explanation + only a case bundle: never SATISFIED ------------------

class TestBoundaryBNoFalseSatisfaction:
    def test_case_bundle_alone_leaves_explanation_unresolved(self):
        out = _explanation_output()
        res = evaluate_output(out, "U00010", [_case_bundle_obs()])
        assert res.satisfaction.value == "unresolved"
        assert res.satisfaction.value != "satisfied"
        assert res.reason_code == R_UNDETERMINED
        assert res.conflicts == []
        sat, pol, reason = apply_postcondition(res)
        assert sat.value == "unresolved"

    def test_evidence_req_order_does_not_matter(self):
        out = _explanation_output(reqs=(EvidenceType.CASE_SUMMARY,
                                        EvidenceType.RULE_EXPLANATION))
        res = evaluate_output(out, "U00010", [_case_bundle_obs()])
        assert res.satisfaction.value == "unresolved"

    def test_enum_member_reqs_behave_like_string_reqs(self):
        """Contract reqs arrive as plain strings at runtime; the guard
        must behave identically for enum members."""
        out = _explanation_output(reqs=["rule_explanation", "case_summary"])
        res = evaluate_output(out, "U00010", [_case_bundle_obs()])
        assert res.satisfaction.value == "unresolved"


# --- C. real explanation evidence satisfies (no fixed tool) ----------------

class TestBoundaryCRealExplanationEvidence:
    def test_rule_facts_satisfy_the_same_output(self):
        out = _explanation_output(subject="F6")
        out.evidence_requirements = ["rule_explanation", "case_summary"]
        res = evaluate_output(
            out, "U00010",
            [_case_bundle_obs(), _signal_explain_obs(finding_id="F6")])
        assert res.satisfaction.value == "satisfied"
        sat, pol, reason = apply_postcondition(res)
        assert sat.value == "satisfied"

    def test_signal_facts_via_existing_branch(self):
        """ml_score-bearing explanation payload — the other existing
        explanation fact shape; proves the branch is source-agnostic."""
        out = _explanation_output(subject="F1")
        out.evidence_requirements = ["ml_signal_explanation",
                                     "case_summary"]
        obs = _signal_explain_obs(finding_id="F1")
        obs["summary"] = {"finding_id": "F1", "ml_score": 99.4,
                          "detection_method": "LightGBM"}
        obs["actual_evidence_types"] = ["ml_signal_explanation"]
        res = evaluate_output(out, "U00010",
                              [_case_bundle_obs(), obs])
        assert res.satisfaction.value == "satisfied"


# --- D/E. compound honesty: policy success cannot offset explanation -------

def _compound_contract():
    return GoalContract(
        contract_id="GC-CSEX1", subject_ref="U00010",
        intent_source="llm",
        outputs=[_explanation_output(), _policy_output()])


class TestCompoundHonesty:
    def _contract(self):
        return _compound_contract()

    def test_outcome_is_partial_not_complete(self):
        contract = self._contract()
        results = evaluate_contract_outputs(
            contract, [_case_bundle_obs(), _policy_obs()], "U00010")
        contract.outputs[0].satisfaction = results[0].satisfaction.value
        contract.outputs[1].satisfaction = results[1].satisfaction.value
        assert results[0].satisfaction.value == "unresolved"
        assert results[1].satisfaction.value == "satisfied"
        outcome = derive_goal_outcome(
            [r.satisfaction for r in results], "completed", "llm")
        assert outcome is GoalOutcome.PARTIAL
        assert outcome is not GoalOutcome.COMPLETE

    def test_user_visible_answer_names_the_gap(self):
        from app.agent_loop import AgentLoopState, _compose_gsp_response
        contract = self._contract()
        results = evaluate_contract_outputs(
            contract, [_case_bundle_obs(), _policy_obs()], "U00010")
        contract.outputs[0].satisfaction = results[0].satisfaction.value
        contract.outputs[1].satisfaction = results[1].satisfaction.value
        state = AgentLoopState(
            task_id="TASK-CSEX", investigation_id="INV-CSEX",
            case_id="U00010",
            goal="为什么关联账号是风险项？有什么政策依据，把相关政策原文发我",
            criteria_kind="question_answered", goal_contract=contract,
            observations=[_case_bundle_obs(), _policy_obs()],
            agent_status="completed", stop_reason="bounded_data")
        answer = _compose_gsp_response(state)
        assert "当前证据不足以确定该项。" in answer
        assert "本次调查目标已完整回答" not in answer
        assert "本次调查目标已部分回答" in answer
        # the policy deliverable is still rendered in full
        assert "原文摘录" in answer
        # no fabricated detection logic appears for the unresolved part
        assert "连通分量" not in answer and "社区发现" not in answer


# --- corrective RE_PLAN reachability (the real failure's decision point) ----

class TestCorrectiveReplanReachable:
    def test_stop_over_unsatisfied_outputs_is_coerced_to_re_plan(self):
        """The real failure's decision shape: fetch_case + policy done,
        the explanation output UNRESOLVED (post the satisfaction-semantics
        fix), plan exhausted, and the proposer proposes STOP. The loop's
        deterministic rule must coerce STOP → RE_PLAN so the follow-up
        plan can pursue the explanation deliverable (existing mechanism —
        no new routing)."""
        from app.agent_loop import (
            AgentLoopState, DecisionProposal, LoopDecision, LoopPolicy)
        contract = _compound_contract()
        contract.outputs[0].satisfaction = "unresolved"
        contract.outputs[1].satisfaction = "satisfied"
        state = AgentLoopState(
            task_id="TASK-CSEX2", investigation_id="INV-CSEX",
            case_id="U00010",
            goal="为什么关联账号是风险项？有什么政策依据，把相关政策原文发我",
            criteria_kind="question_answered", goal_contract=contract,
            observations=[_case_bundle_obs(), _policy_obs()],
            plan_steps=[
                {"step_id": "S1", "type": "fetch_case", "status": "success"},
                {"step_id": "S2", "type": "retrieve_policy",
                 "status": "success"}],
            iteration=2)
        enforced = LoopPolicy().enforce(
            DecisionProposal(decision="STOP", reason="case context and "
                             "policy citations are sufficient",
                             source="llm"),
            state)
        assert enforced.decision == LoopDecision.RE_PLAN
        assert enforced.coerced_from == "STOP"


# ===========================================================================
# Explanation DELIVERY semantics (round 2 — real stack TASK-268228ad0559):
# listing related objects is not explaining why the relationship is a risk
# item, and a policy citation existing is not a fact↔policy link. The
# composer must deliver the explanation from RP's own attribution facts
# when they exist, and must name the confirmed facts + the missing basis
# when they do not — while the policy original text is always preserved
# and a bare relation list can no longer complete the explanation goal.
# ===========================================================================

def _related_accounts_summary(with_cluster=True):
    s = {
        "related_accounts_count": 18,
        "related_accounts": [f"U0000{i}" for i in range(1, 10)],
        "shared_devices": ["DEV8Z1O4A35QU"],
        "shared_ip_attributions": {"U00004": []},
    }
    if with_cluster:
        s.update({
            "cluster_name": "Cluster_device_sharing_1",
            "detection_type": "device_sharing",
            "member_count": 19,
            "cluster_risk_score": 80.0,
            "role_in_cluster": "spoke",
        })
    return s


def _related_obs(with_cluster=True, for_outputs=("O1",)):
    return {"tool_name": "related_account_analysis",
            "step_type": "analyze_related_accounts", "outcome": "success",
            "for_outputs": list(for_outputs),
            "actual_evidence_types": ["network_relations",
                                      "shared_device_evidence"],
            "summary": _related_accounts_summary(with_cluster)}


def _explanation_network_output():
    return RequiredOutput(
        output_id="O1", claim="解释关联账号为何是风险项",
        claim_kind="explanation", subject_ref="U00010",
        scope="case_level",
        evidence_requirements=["rule_explanation", "network_relations"])


_CITATION = {"citation_id": 3, "document": "AML_Suspicious_Indicators.md",
             "section": "AML Suspicious Activity Indicators (Demo "
                        "Template) / 5. Network / Relationship Signals / "
                        "5.1 Links to Known Risky Clusters",
             "snippet": "Accounts linked to known risky clusters "
                        "(shared devices, shared withdrawal destinations, "
                        "repeated counterparties) should be prioritized "
                        "for review."}


class TestExplanationDeliveryWithClusterFacts:
    def test_cluster_attribution_satisfies_and_renders_narrative(self):
        out = _explanation_network_output()
        res = evaluate_output(out, "U00010", [_related_obs(True)])
        assert res.satisfaction.value == "satisfied"
        from app.agent_loop import _gsp_render_output_facts
        lines = _gsp_render_output_facts(
            out, res and {"related_accounts_count": 18,
                          "related_accounts": ["U00004"],
                          "shared_devices": ["DEV8Z1O4A35QU"],
                          "cluster_name": "Cluster_device_sharing_1",
                          "detection_type": "device_sharing",
                          "member_count": 19,
                          "cluster_risk_score": 80.0,
                          "role_in_cluster": "spoke"},
            run_policy=([_CITATION], True, True))
        text = "\n".join(lines)
        # the RP attribution facts are stated, not just the member list
        assert "Cluster_device_sharing_1" in text
        assert "device_sharing" in text
        assert "spoke" in text
        # the bounded risk-signal judgment, never a violation claim
        assert "风险信号判断" in text
        assert "不构成" in text and "认定" in text
        # the grounded fact↔policy correspondence (RP-attached)
        assert "政策对应" in text and "finding 级依据" in text
        assert "5.1 Links to Known Risky Clusters" in text

    def test_no_correspondence_without_finding_attachment(self):
        """Policy exists but RP did NOT attach it to the finding: the
        answer must NOT claim the fact↔policy link — case-level
        reference wording only."""
        from app.agent_loop import _gsp_render_output_facts
        out = _explanation_network_output()
        lines = _gsp_render_output_facts(
            out, _related_accounts_summary(True),
            run_policy=([_CITATION], False, True))
        text = "\n".join(lines)
        assert "政策对应" not in text
        assert "案件级参考" in text
        assert "不据此宣称直接适用" in text

    def test_judgment_only_without_run_policy(self):
        from app.agent_loop import _gsp_render_output_facts
        out = _explanation_network_output()
        lines = _gsp_render_output_facts(
            out, _related_accounts_summary(True), run_policy=None)
        text = "\n".join(lines)
        assert "风险信号判断" in text
        assert "政策对应" not in text


class TestExplanationInsufficientBareList:
    def test_bare_relation_list_never_satisfies_explanation(self):
        out = _explanation_network_output()
        res = evaluate_output(out, "U00010", [_related_obs(False)])
        assert res.satisfaction.value == "unresolved"
        sat, pol, reason = apply_postcondition(res)
        assert sat.value == "unresolved"

    def test_unresolved_names_confirmed_facts_and_missing_basis(self):
        from app.agent_loop import (
            AgentLoopState, _compose_gsp_response)
        contract = GoalContract(
            contract_id="GC-DELIV1", subject_ref="U00010",
            intent_source="llm",
            outputs=[_explanation_network_output(), _policy_output()])
        results = evaluate_contract_outputs(
            contract, [_related_obs(False), _policy_obs()], "U00010")
        contract.outputs[0].satisfaction = results[0].satisfaction.value
        contract.outputs[1].satisfaction = results[1].satisfaction.value
        state = AgentLoopState(
            task_id="TASK-DELIV1", investigation_id="INV-DELIV1",
            case_id="U00010",
            goal="为什么关联账号是风险项？有什么政策依据，把相关政策原文发我",
            criteria_kind="question_answered", goal_contract=contract,
            observations=[_related_obs(False), _policy_obs()],
            agent_status="completed", stop_reason="bounded_data")
        answer = _compose_gsp_response(state)
        # confirmed facts are named
        assert "已确认的关系事实" in answer
        assert "关联账号 18 个" in answer
        assert "DEV8Z1O4A35QU" in answer
        # the missing basis is named, with the anti-overclaim boundary
        assert "尚缺的解释依据" in answer
        assert "不能确定" in answer
        # policy original text preserved
        assert "Accounts linked to known risky clusters" in answer
        # no false completion
        assert "本次调查目标已完整回答" not in answer
        assert "本次调查目标已部分回答" in answer

    def test_policy_success_cannot_complete_compound(self):
        contract = GoalContract(
            contract_id="GC-DELIV2", subject_ref="U00010",
            intent_source="llm",
            outputs=[_explanation_network_output(), _policy_output()])
        results = evaluate_contract_outputs(
            contract, [_related_obs(False), _policy_obs()], "U00010")
        outcome = derive_goal_outcome(
            [r.satisfaction for r in results], "completed", "llm")
        assert outcome is GoalOutcome.PARTIAL


class TestPlainRelationListingUnchanged:
    def test_retrieval_kind_relation_output_still_satisfied(self):
        """A pure 'list the related accounts' deliverable (no explanation
        requirement) keeps its existing satisfaction — the guard keys on
        the contract's explanation demand, never on a fixed tool."""
        out = RequiredOutput(
            output_id="O1", claim="列出关联账号", claim_kind="retrieval",
            subject_ref="U00010", scope="case_level",
            evidence_requirements=["network_relations"])
        res = evaluate_output(out, "U00010", [_related_obs(False)])
        assert res.satisfaction.value == "satisfied"

    def test_explanation_req_with_any_fact_source_satisfies(self):
        """Explanation facts via a non-signal_explain producer still
        satisfy — shape-based, not tool-based."""
        out = _explanation_network_output()
        obs = _related_obs(False)
        obs["summary"]["cluster_name"] = "Cluster_X"
        res = evaluate_output(out, "U00010", [obs])
        assert res.satisfaction.value == "satisfied"


# ===========================================================================
# PARTIALLY_SUPPORTED file-delivery claim (real executions TASK-88c0e445cce9
# + 3 more claimed "其余记录的完整集合见已生成文件" with ZERO artifacts in
# the run). The claim now follows the L1 file-export backing convention:
# this output's record families must be covered by artifact_backed_families
# — an unrelated artifact never backs it; without backing the honest
# partial wording applies.
# ===========================================================================

def _records_partial_output():
    return RequiredOutput(
        output_id="O1", claim="交易明细清单", claim_kind="retrieval",
        subject_ref="U00033", scope="case_level",
        evidence_requirements=["transaction_records"])


def _records_partial_summary():
    return {"record_count": 50,
            "record_rows": [{"record_id": f"T0000{i}",
                             "timestamp": "2026-08-19T10:00:00Z",
                             "summary": "SELL trade"} for i in range(1, 13)]}


class TestPartialFileClaimBacking:
    def _render(self, backed_families):
        from app.agent_loop import _gsp_render_output_section
        out = _records_partial_output()
        out.satisfaction = "partially_supported"
        return "\n".join(_gsp_render_output_section(
            out, _records_partial_summary(),
            artifact_backed_families=backed_families))

    def test_backed_record_families_keep_the_file_pointer(self):
        text = self._render({"transaction_records"})
        assert "其余记录的完整集合见已生成文件" in text
        assert "已获取部分证据，但覆盖范围不完整" not in text

    def test_no_artifact_restores_honest_partial_wording(self):
        text = self._render(set())
        assert "已生成文件" not in text
        assert "已获取部分证据，但覆盖范围不完整" in text

    def test_unrelated_artifact_never_backs_the_claim(self):
        text = self._render({"withdrawal_records"})
        assert "已生成文件" not in text
        assert "已获取部分证据，但覆盖范围不完整" in text

    def test_candidates_partial_without_record_families_is_honest(self):
        """No record families in reqs (e.g. a capped candidate listing):
        the records-flavored file claim never renders."""
        from app.agent_loop import _gsp_render_output_section
        out = RequiredOutput(
            output_id="O1", claim="对冲候选", claim_kind="retrieval",
            subject_ref="U00033", scope="case_level",
            evidence_requirements=["opposite_trade_relations"])
        out.satisfaction = "partially_supported"
        summary = {"candidates": [
            {"candidate_id": "CATA-001", "account_a": "A", "account_b": "B",
             "side_a": "BUY", "side_b": "SELL", "symbol": "BTC",
             "max_time_gap_seconds": 0.5, "value_a": 1.0, "value_b": 0.9,
             "value_difference": 0.1, "value_difference_relative": 0.1,
             "transaction_ids_a": ["T1"], "transaction_ids_b": ["T2"]}],
            "candidates_found": 8}
        text = "\n".join(_gsp_render_output_section(
            out, summary, artifact_backed_families=set()))
        assert "已生成文件" not in text
        assert "已获取部分证据，但覆盖范围不完整" in text
