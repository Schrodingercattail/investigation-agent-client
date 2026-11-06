"""Policy-ask yield — 「把关联账号相关的政策原文发我」 must deliver the
policy citations WITHOUT expanding the related-account list
(2026-11-05 acceptance TASK-b0019ac3e0e1: the candidate's
related_accounts_network aspect counted the bare policy-delivery verb
发我 as account-list intent, and _merge_with_candidate re-admitted the
network output into the GU's policy-only set — 18 accounts + shared
device expanded in a policy-only answer).

Three-intent boundary pinned at the deterministic candidate:
- policy-only ask → policy citations, NO network output;
- account-list ask → network output (unchanged);
- policy + explicit account-list ask → BOTH outputs.
"""
from app.goal_resolution import deterministic_contract_proposal

F5 = SimpleNamespace = None  # placeholder replaced below


def _outputs(text, findings=None):
    from types import SimpleNamespace
    f5 = SimpleNamespace(finding_id="F5", title="Linked Account Network")
    rep = deterministic_contract_proposal(text, None, "U00004",
                                          case_findings=findings or [f5])
    return (rep or {}).get("required_outputs") or []


def test_policy_only_yields_to_policy_ask():
    outs = _outputs("把关联账号相关的政策原文发我")
    assert outs, "policy ask must still produce the policy output"
    claims = " | ".join(str(o.get("claim")) for o in outs)
    # policy citations present (finding-scoped per U00020 fix A)
    assert "政策依据" in claims
    # NO network-attribution deliverable
    assert "网络归因明细" not in claims
    assert not any(o.get("evidence_requirements") == ["network_relations"]
                   for o in outs)


def test_account_list_keeps_network_output():
    outs = _outputs("列出这个案件的关联账号名单")
    assert any(o.get("evidence_requirements") == ["network_relations"]
               for o in outs), "explicit account-list ask keeps the network deliverable"
    assert any("关联账号" in str(o.get("claim")) for o in outs)


def test_policy_plus_explicit_list_keeps_both():
    outs = _outputs("把关联账号相关的政策原文发我，并列出关联账号明细")
    claims = " | ".join(str(o.get("claim")) for o in outs)
    assert "政策依据" in claims
    assert "网络归因明细" in claims
    # two distinct retrieval outputs, no merging loss
    assert len(outs) == 2


def test_en_related_policy_yields_too():
    outs = _outputs("send me the related policy or citation for the "
                    "linked accounts")
    claims = " | ".join(str(o.get("claim")) for o in outs)
    assert "网络归因明细" not in claims
