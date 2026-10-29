"""F3 EN rule-delivery gap fix — EN domain elements + explain-intent gate.

Root cause (audited): `_EXPLAIN_DOMAINS`' withdrawal_new_address domain
had NO English request element, so EN criteria asks deterministically
derived the F2 explain while the F3 explain depended on goal-
understanding LLM variance (2/3 live runs missed it; when the LLM did
author it, its case-level subject weakened the finding binding).

Fix: EN domain elements ("first withdrawal", "withdrawal to new
address") + an explain-intent gate — pure policy asks naming the
finding do NOT derive an F3 explain output (U00020 fix B semantics
preserved for EN); genuine criteria/why asks derive F2+F3.

Generic fixtures; the audited EN request is the regression anchor.
"""
from types import SimpleNamespace

from app import investigation_concepts as ic
from app.goal_resolution import deterministic_contract_proposal
from app.models import InvestigationContext

EN_CRITERIA = ("show me the criterias of High Withdrawal Frequency and "
               "First Withdrawal to New Address")
EN_POLICY = ("Which policy requirements apply to finding F3 "
             "(First Withdrawal to New Address)?")
ZH_COMPOUND_WHY = "为什么高频提现和提现到新地址是风险项"


def _findings():
    return [
        SimpleNamespace(finding_id="F1", title="ML Pattern Detection"),
        SimpleNamespace(finding_id="F2",
                        title="High Withdrawal Frequency"),
        SimpleNamespace(finding_id="F3",
                        title="First Withdrawal to New Address"),
        SimpleNamespace(finding_id="F5", title="Linked Account Network"),
    ]


# --- A1/A2: EN domain identification -------------------------------------

def test_en_criteria_ask_recognizes_both_domains():
    pairs = ic.findings_for_explain_domains(
        EN_CRITERIA, _findings(), drop_comparison_context=True)
    ids = sorted(f.finding_id for _d, f in pairs)
    assert ids == ["F2", "F3"]


def test_en_first_withdrawal_phrase_matches_domain():
    pairs = ic.findings_for_explain_domains(
        "What are the criteria for the first withdrawal to a new "
        "address?", _findings(), drop_comparison_context=True)
    assert any(f.finding_id == "F3" for _d, f in pairs)


def test_en_case_level_subject_not_used_for_domain_finding():
    """The domain resolves the REAL finding F3 — never a case-level
    subject (the audited O2 subject=U00008 gap)."""
    proposal = deterministic_contract_proposal(
        EN_CRITERIA, None, "U00008", case_findings=_findings())
    explains = [o for o in proposal["required_outputs"]
                if o.get("evidence_requirements") == ["rule_explanation"]]
    f3_outs = [o for o in explains if o["subject_ref"] == "F3"]
    assert f3_outs, explains
    for o in f3_outs:
        assert o["subject_ref"] == "F3"
        # the finding is named by TITLE in the finding-scoped claim
        assert "First Withdrawal to New Address" in o["claim"]
        assert o["scope"] == "finding_level"


# --- A3: mentions predicate consistency for the gap expressions ---------

def test_mentions_predicate_consistent_with_domain_on_en():
    """For the EN expressions that carry this domain, the records-delivery
    predicate and the explain-domain agree (both fire)."""
    for text in (EN_CRITERIA,
                 "What are the criteria for the first withdrawal to a "
                 "new address?"):
        assert ic.mentions_withdrawal_new_address(text) is True, text
        pairs = ic.findings_for_explain_domains(text, _findings())
        # the domain table fires for the new-address concept whenever the
        # phrase is present (gating is a caller concern, not vocabulary)
        assert any(d == "withdrawal_new_address" for d, _f in pairs), text


def test_domain_table_has_en_elements():
    en_elements = [e for _d, e, _t in ic._EXPLAIN_DOMAINS
                   if _d == "withdrawal_new_address"]
    assert any("first withdrawal" in e for e in en_elements)
    assert any("withdrawal to new address" in e for e in en_elements)


# --- B2: pure EN policy ask derives NO F3 explain ------------------------

def test_en_policy_ask_derives_no_f3_explain():
    proposal = deterministic_contract_proposal(
        EN_POLICY, None, "U00008", case_findings=_findings())
    outputs = (proposal or {}).get("required_outputs", [])
    explains = [o for o in outputs
                if o.get("evidence_requirements") == ["rule_explanation"]]
    assert explains == [], explains


def test_en_policy_ask_gate_recorded_in_fired_aspects():
    fired: list = []
    deterministic_contract_proposal(
        EN_POLICY, None, "U00020", case_findings=_findings(),
        fired_aspects=fired)
    assert any(a.startswith(
        "explain_compound:skipped_policy_intent:") for a in fired), fired


# --- B3: compound why keeps both explains --------------------------------

def test_zh_compound_why_keeps_both_explains():
    proposal = deterministic_contract_proposal(
        ZH_COMPOUND_WHY, None, "U00020",
        case_findings=risk_case_fetch_u00020())
    explains = sorted(o["subject_ref"] for o in proposal["required_outputs"]
                      if o.get("evidence_requirements")
                      == ["rule_explanation"])
    assert explains == ["F2", "F3"]


def risk_case_fetch_u00020():
    # tiny local fixture matching U00020's title vocabulary (no network)
    return [
        SimpleNamespace(finding_id="F1", title="ML Pattern Detection"),
        SimpleNamespace(finding_id="F2",
                        title="High Withdrawal Frequency"),
        SimpleNamespace(finding_id="F3",
                        title="First Withdrawal to New Address"),
        SimpleNamespace(finding_id="F5", title="Linked Account Network"),
    ]


# --- B4: single-finding explain unaffected -------------------------------

def test_single_finding_criteria_still_derives_one_explain():
    proposal = deterministic_contract_proposal(
        "为什么高频提现是风险项？", None, "U00008",
        case_findings=_findings())
    explains = [o for o in proposal["required_outputs"]
                if o.get("evidence_requirements") == ["rule_explanation"]]
    assert [o["subject_ref"] for o in explains] == ["F2"]


# --- comparison-context gating still applies with EN elements ------------

def test_comparison_context_gate_unchanged():
    q = ("提现到新地址是相同地址还是不同地址？")
    pairs = ic.findings_for_explain_domains(
        q, _findings(), drop_comparison_context=True)
    assert pairs == []
