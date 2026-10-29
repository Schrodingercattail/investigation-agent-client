"""U00020 policy-scope fix — finding-anchored policy intent + explain
intent gating.

Fix A: a policy-basis ask tied to exactly ONE explain-intent finding
inherits that finding's anchor (subject_ref = F-id, finding-scoped
claim) — the audited case anchored F5 (population wording → linked-
account domain → Linked Account Network) instead of the user's F2,
whose own RP citation [2] (High-Velocity Transfers) was never
retrieved. Multi-finding / no-finding shapes keep the population
canonical byte-for-byte.

Fix B: explain-compound decomposition is INTENT-AWARE — a risk domain
named inside a comparison/status clause (“提现到新地址是相同地址还是
提向不同地址”) does not derive a rule-explanation output; genuine
compound why-asks keep every explain output. fired_aspects records the
derivation (fired + dropped) for auditability.

Generic fixtures; the audited U00020 phrasing is the regression anchor.
"""
from types import SimpleNamespace

import pytest

from app import investigation_concepts as ic
from app.goal_resolution import (
    _canonicalize_llm_outputs,
    _policy_output_for_request,
    deterministic_contract_proposal,
)

AUDIT_Q = ("高频提现的判定条件是什么？有什么政策依据？提现到新地址"
           "是相同地址还是提向不同地址？把提现明细生成一份文件")
COMPOUND_WHY = "为什么高频提现和提现到新地址是风险项"


def _findings():
    return [
        SimpleNamespace(finding_id="F1", title="ML Pattern Detection"),
        SimpleNamespace(finding_id="F2",
                        title="High Withdrawal Frequency"),
        SimpleNamespace(finding_id="F3",
                        title="First Withdrawal to New Address"),
        SimpleNamespace(finding_id="F5", title="Linked Account Network"),
    ]


# --- Fix B: intent-aware explain decomposition --------------------------

def test_b_audit_request_keeps_f2_explain_drops_f3():
    pairs = ic.findings_for_explain_domains(
        AUDIT_Q, _findings(), drop_comparison_context=True)
    ids = [f.finding_id for _d, f in pairs]
    assert ids == ["F2"]            # no F3 from the comparison clause


def test_b_compound_why_keeps_both_explains():
    pairs = ic.findings_for_explain_domains(
        COMPOUND_WHY, _findings(), drop_comparison_context=True)
    ids = sorted(f.finding_id for _d, f in pairs)
    assert ids == ["F2", "F3"]


def test_b_pure_comparison_ask_derives_no_explain():
    q = "提现到新地址的目的地是相同还是不同？"
    pairs = ic.findings_for_explain_domains(
        q, _findings(), drop_comparison_context=True)
    assert pairs == []


def test_b_ungated_default_unchanged():
    # anchoring callers keep the historical (ungated) behavior
    pairs = ic.findings_for_explain_domains(AUDIT_Q, _findings())
    ids = [f.finding_id for _d, f in pairs]
    assert ids == ["F3", "F2"]      # pre-fix order preserved


def test_b_proposal_original_request_has_no_f3_explain():
    proposal = deterministic_contract_proposal(
        AUDIT_Q, None, "U00020", case_findings=_findings())
    assert proposal is not None
    explains = [o for o in proposal["required_outputs"]
                if o.get("evidence_requirements") == ["rule_explanation"]]
    assert [o["subject_ref"] for o in explains] == ["F2"]   # B2, B1 part
    # the explicit records deliveries survive (B1)
    facets = {f for o in proposal["required_outputs"]
              for f in (o.get("postcondition_facets") or [])}
    assert "destination_address_identity" in facets
    assert "withdrawal_new_address_status" in facets


def test_b_proposal_compound_why_keeps_both():
    proposal = deterministic_contract_proposal(
        COMPOUND_WHY, None, "U00020", case_findings=_findings())
    assert proposal is not None
    explains = [o for o in proposal["required_outputs"]
                if o.get("evidence_requirements") == ["rule_explanation"]]
    assert sorted(o["subject_ref"] for o in explains) == ["F2", "F3"]


def test_b_fired_aspects_record_derivation_and_drops():
    fired: list = []
    deterministic_contract_proposal(
        AUDIT_Q, None, "U00020", case_findings=_findings(),
        fired_aspects=fired)
    assert any(a.startswith("explain_compound:withdrawal_frequency:F2")
               for a in fired)                       # fired derivation
    assert any(a.startswith(
        "explain_compound:dropped_comparison_context:"
        "withdrawal_new_address:F3") for a in fired)  # why it dropped
    fired2: list = []
    deterministic_contract_proposal(
        COMPOUND_WHY, None, "U00020", case_findings=_findings(),
        fired_aspects=fired2)
    assert any(a == "explain_compound:withdrawal_new_address:F3"
               for a in fired2)                      # fired, not dropped


# --- Fix A: policy intent inheritance ------------------------------------

def test_a_audit_request_policy_inherits_f2():
    proposal = deterministic_contract_proposal(
        AUDIT_Q, None, "U00020", case_findings=_findings())
    pol = [o for o in proposal["required_outputs"]
           if o.get("evidence_requirements") == ["policy_references"]]
    # the deterministic vocabulary does not fire for “政策依据” alone;
    # when it does fire (explicit citation asks) it must be F2-scoped
    for o in pol:
        assert o["subject_ref"] == "F2"
        assert "F2" in o["claim"]


def test_a_canonicalize_llm_tagged_policy_inherits_f2():
    # the audited run's actual path: the LLM tagged its policy output
    tagged = [{"aspect": "policy_citation_original_text",
               "claim": "policy citations"}]
    out = _canonicalize_llm_outputs(tagged, "U00020", None,
                                    request_text=AUDIT_Q,
                                    case_findings=_findings())
    o = out[0]
    assert o["subject_ref"] == "F2"
    assert o["scope"] == "finding_level"
    assert "High Withdrawal Frequency" in o["claim"]


def test_a_multi_finding_why_keeps_population_canonical():
    tagged = [{"aspect": "policy_citation_original_text",
               "claim": "policy citations"}]
    out = _canonicalize_llm_outputs(tagged, "U00020", None,
                                    request_text=(
                                        "为什么高频提现和提现到新地址是"
                                        "风险项？有什么政策依据？"),
                                    case_findings=_findings())
    # two explain findings → no single anchor → population canonical
    assert out[0]["subject_ref"] == "U00020"
    assert "关联账号" in out[0]["claim"]


def test_a_no_request_text_keeps_population_canonical():
    tagged = [{"aspect": "policy_citation_original_text",
               "claim": "policy citations"}]
    out = _canonicalize_llm_outputs(tagged, "U00020", None)
    assert out[0]["subject_ref"] == "U00020"
    assert "关联账号" in out[0]["claim"]


def test_a_population_journey_vocabulary_untouched():
    # the explicit population ask still resolves via the linked-account
    # domain (U00010 journey) — the vocabulary entry is preserved
    q = "与关联账号相关的政策引文是什么？"
    assert ic.is_policy_citation_original_text_request(q)
    pairs = ic.findings_for_explain_domains(
        q, _findings(), drop_comparison_context=True)
    assert any(f.finding_id == "F5" for _d, f in pairs)


def test_a_coverage_supplement_anchors_f2_not_f5():
    """The policy output's F-id subject anchors the supplement spec
    directly — never the linked-account domain match that produced F5."""
    from app.goal_resolution import (
        GoalRepresentation, GoalResolutionStatus, build_goal_contract)
    from app.planning_coverage import coverage_supplement_steps
    proposal = deterministic_contract_proposal(
        AUDIT_Q, None, "U00020", case_findings=_findings())
    # the REAL flow: the LLM's policy output arrives tagged and is
    # canonicalized with the request text (intent-aware) — reproduce it
    tagged = [{"aspect": "policy_citation_original_text",
               "claim": "policy citations"}]
    outputs = list(proposal["required_outputs"]) + _canonicalize_llm_outputs(
        tagged, "U00020", None, request_text=AUDIT_Q,
        case_findings=_findings())
    rep = GoalRepresentation(
        case_id="U00020", goal_type=None,
        goal_resolution=GoalResolutionStatus.COMPOUND.value,
        subject_type="case", subject_ref="U00020",
        source="llm", request_text=AUDIT_Q[:400],
        contract_proposal={"required_outputs": outputs})
    contract, reason = build_goal_contract(rep, "U00020")
    assert contract is not None, reason
    specs = coverage_supplement_steps(contract, [])
    pol_specs = [s for s in specs if s.step_type == "retrieve_policy"]
    assert pol_specs, specs
    assert all(s.finding_target == "F2" for s in pol_specs), pol_specs
    assert all(s.finding_target != "F5" for s in specs)
