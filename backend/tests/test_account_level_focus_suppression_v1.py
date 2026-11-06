"""Account-level records retrieval — stream-anchor focus suppression
predicate (2026-11-05 U00043-A fix).

Pins the approved boundary: an AGENT_RESOLVED focus from
ContextResolver._stream_anchor is suppressed ONLY for account-level
records contracts (no F-id subject, record-stream requirements, no
finding id named in the request). User-selected and explicitly named
findings, finding-anchored contracts, and non-records contracts are
never suppressed.
"""
from types import SimpleNamespace

from app.agent_loop import _suppress_agent_stream_anchor_focus
from app.goal_resolution import ClaimKind, GoalContract, RequiredOutput, \
    ScopeLevel


def _contract(delivery="detail_list", subject="U00043", reqs=("withdrawal_records",)):
    fields = dict(output_id="O1", claim="claim", claim_kind="retrieval",
                  claim_description="d", subject_ref=subject,
                  scope=ScopeLevel.CASE_LEVEL,
                  evidence_requirements=list(reqs))
    if delivery is not None:
        fields["delivery"] = delivery
    return GoalContract(contract_id="GC-t", subject_ref=subject, outputs=[
        RequiredOutput(**fields)])


def test_agent_resolved_account_records_suppressed():
    assert _suppress_agent_stream_anchor_focus(
        "请把此账号的带有提现地址的提现明细发我",
        _contract(), "agent_resolved") is True


def test_user_selected_f2_never_suppressed():
    assert _suppress_agent_stream_anchor_focus(
        "请把此账号的带有提现地址的提现明细发我",
        _contract(), "user_selected") is False


def test_explicitly_named_finding_never_suppressed():
    assert _suppress_agent_stream_anchor_focus(
        "谈谈F2这个发现的提现明细", _contract(), "agent_resolved") is False


def test_finding_anchored_contract_never_suppressed():
    contract = _contract(subject="F2")
    assert _suppress_agent_stream_anchor_focus(
        "请把此账号的带有提现地址的提现明细发我",
        contract, "agent_resolved") is False


def test_non_records_contract_never_suppressed():
    policy = _contract(reqs=("policy_references",))
    explain = _contract(reqs=("rule_explanation",))
    assert _suppress_agent_stream_anchor_focus(
        "send me the related policy or citation", policy,
        "agent_resolved") is False
    assert _suppress_agent_stream_anchor_focus(
        "why is this finding flagged", explain,
        "agent_resolved") is False


def test_missing_or_none_focus_source_never_suppressed():
    assert _suppress_agent_stream_anchor_focus(
        "请把此账号的带有提现地址的提现明细发我", _contract(), None) is False
    assert _suppress_agent_stream_anchor_focus(
        "请把此账号的带有提现地址的提现明细发我", _contract(), "") is False


def test_no_contract_never_suppressed():
    assert _suppress_agent_stream_anchor_focus(
        "请把此账号的带有提现地址的提现明细发我", None,
        "agent_resolved") is False
