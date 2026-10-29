"""Policy Applicability End-to-End Closure — anchoring invariants.

Audited failure (TASK-becfe61f4fbf, U00029, focus=F2, en): the anaphoric
ask "Which policy requirements apply to this finding?" lost the finding
identity (focus lives in context, not in the request text), the policy
output fell back to the population canonical whose claim re-anchored the
retrieval to F5 via the linked-account domain, and F5's clusters citation
was delivered as finding-level applicable — fully answered.

Invariants pinned here:
I1 anaphoric inheritance — a deixis policy ask inherits the UNIQUE focus.
I2 no silent target substitution — a finding-anchored output pins the
   supplement spec directly (F-id subject), so domain re-inference over
   the canonical claim cannot re-anchor it.
I3 explicit ambiguity — population asks (no deixis, no focus) stay
   population; multi-finding with no focus is never silently anchored.
I4 applicability — a FINDING-subject policy output is satisfied only
   with an RP-attached (associated) basis for THAT finding; case-level
   matches alone yield a disclosed partial.
I5 completion integrity — the partial state flows to the outcome, so a
   wrong/unsupported policy target can never read "fully answered".
"""
import json
from types import SimpleNamespace

import pytest

from app import investigation_concepts as ic
from app.goal_resolution import (
    OutputSatisfaction,
    _canonicalize_llm_outputs,
    _policy_output_for_request,
    deterministic_contract_proposal,
)
from app.goal_postconditions import evaluate_output
from app.goal_resolution import GoalResolutionStatus, build_goal_contract
from app.models import InvestigationContext
from app.skills import EvidenceType
from app.planning_coverage import coverage_supplement_steps


def _findings():
    return [
        SimpleNamespace(finding_id="F1", title="ML Pattern Detection"),
        SimpleNamespace(finding_id="F2",
                        title="High Withdrawal Frequency"),
        SimpleNamespace(finding_id="F3",
                        title="First Withdrawal to New Address"),
        SimpleNamespace(finding_id="F5", title="Linked Account Network"),
    ]


FINDINGS = _findings()
ANAPHORIC_EN = "Which policy requirements apply to this finding?"
ANAPHORIC_ZH = "该发现适用哪些政策要求？"
AUDIT_Q = ("高频提现的判定条件是什么？有什么政策依据？提现到新地址"
           "是相同地址还是提向不同地址？把提现明细生成一份文件")


def _proposal(text, focus=None, findings=FINDINGS):
    return deterministic_contract_proposal(
        text, focus, "U00029", case_findings=findings)


def _policy_outputs(proposal):
    return [o for o in (proposal or {}).get("required_outputs", [])
            if o.get("evidence_requirements") == ["policy_references"]]


# --- I1: anaphoric inheritance -------------------------------------------

def test_i1_anaphoric_en_inherits_focus_f2():
    proposal = _proposal(ANAPHORIC_EN, focus="F2")
    pol = _policy_outputs(proposal)
    assert len(pol) == 1
    assert pol[0]["subject_ref"] == "F2"
    assert "High Withdrawal Frequency" in pol[0]["claim"]
    # population wording must not return for the anaphoric ask
    assert "关联账号" not in pol[0]["claim"]


def test_i1_anaphoric_zh_inherits_focus_f2():
    proposal = _proposal(ANAPHORIC_ZH, focus="F2")
    pol = _policy_outputs(proposal)
    assert len(pol) == 1
    assert pol[0]["subject_ref"] == "F2"


def test_i1_focus_that_does_not_resolve_stays_population():
    proposal = _proposal(ANAPHORIC_EN, focus="F99")
    pol = _policy_outputs(proposal)
    assert pol[0]["subject_ref"] == "U00029"      # population fallback


# --- I2: no silent target substitution -----------------------------------

def test_i2_f2_anchored_output_pins_supplement_spec_to_f2():
    from app.goal_resolution import GoalRepresentation
    proposal = _proposal(ANAPHORIC_EN, focus="F2")
    rep = GoalRepresentation(
        case_id="U00029", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.EXPLICIT.value,
        subject_type="case", subject_ref="U00029",
        source="llm", request_text=ANAPHORIC_EN[:400],
        contract_proposal=proposal)
    contract, reason = build_goal_contract(rep, "U00029")
    assert contract is not None, reason
    specs = coverage_supplement_steps(contract, [])
    pol_specs = [s for s in specs if s.step_type == "retrieve_policy"]
    assert pol_specs, specs
    # the F-id subject pins the anchor DIRECTLY — the linked-account
    # domain match that produced F5 can never re-anchor this output
    assert all(s.finding_target == "F2" for s in pol_specs), pol_specs
    assert all(s.finding_target != "F5" for s in specs)


# --- I3: population and ambiguity semantics ------------------------------

def test_i3_population_ask_without_focus_stays_population():
    """No deixis + no focus: the deterministic proposal produces NO
    finding-anchored policy output (the ask is not deterministically
    boundable) — the population semantic is owned by the GU LLM +
    canonicalize (covered by test_canonicalize_no_text_population_
    unchanged). The binding assertion here: no finding subject leaks."""
    proposal = _proposal("Which policy requirements apply?", focus=None)
    pol = _policy_outputs(proposal)
    assert all(o["subject_ref"] == "U00029" for o in pol)

def test_i3_multi_finding_no_focus_stays_population():
    proposal = _proposal(AUDIT_Q, focus=None)
    pol = _policy_outputs(proposal)
    if pol:                                # policy block fired
        assert pol[0]["subject_ref"] == "U00029"


def test_i3_anaphoric_with_focus_is_the_explicit_user_selection():
    """Anaphoric + focus = the user's own selection (not a guess): the
    focus anchor is used even when the text also names other domains."""
    proposal = _proposal(
        AUDIT_Q + " Which policy requirements apply to this finding?",
        focus="F2")
    pol = _policy_outputs(proposal)
    assert pol and pol[0]["subject_ref"] == "F2"


# --- I4: applicability validation ----------------------------------------

def _policy_eval(subject, summary):
    from app.goal_resolution import RequiredOutput
    out = RequiredOutput(
        output_id="O1", claim="policy basis for the finding",
        claim_kind="retrieval", subject_ref=subject, scope="finding_level",
        evidence_requirements=[EvidenceType.POLICY_REFERENCES],
        satisfaction="satisfied")
    return evaluate_output(out, "U00029", [{
        "step_type": "retrieve_policy", "tool_name": "policy_lookup",
        "outcome": "success", "for_outputs": ["O1"],
        "summary": summary}])


def test_i4_associated_basis_satisfies_finding_policy():
    r = _policy_eval("F2", {
        "matches": [{"citation_id": 2}],
        "associated_citations": [{"citation_id": 2}],
        "finding_policy_status": "associated",
        "finding_id": "F2"})
    assert r.satisfaction == OutputSatisfaction.SATISFIED


def test_i4_no_associated_basis_is_disclosed_partial_never_satisfied():
    """The audited wrong-delivery shape: retrieval succeeded (matches
    present) but NOTHING is associated with THIS finding — the clusters
    citation retrieved for another finding can never satisfy it."""
    r = _policy_eval("F2", {
        "matches": [{"citation_id": 3}, {"citation_id": 4}],
        "associated_citations": [],
        "finding_policy_status": "no_finding_level_basis",
        "finding_id": "F2"})
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


def test_i4_no_basis_status_partial_even_with_matches():
    r = _policy_eval("F2", {
        "matches": [{"citation_id": 3}],
        "associated_citations": [],
        "finding_policy_status": "no_finding_level_basis",
        "match_count": 4,
        "finding_id": "F2"})
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


def test_i4_population_subject_keeps_retrieval_rule():
    r = _policy_eval("U00029", {
        "matches": [{"citation_id": 3}],
        "finding_policy_status": "associated"})
    assert r.satisfaction == OutputSatisfaction.SATISFIED


# --- I5: completion integrity ---------------------------------------------

def test_i5_partial_policy_keeps_outcome_partial():
    from app.goal_postconditions import derive_goal_outcome
    from app.goal_resolution import OutputSatisfaction as OS
    outcome = derive_goal_outcome([OS.PARTIALLY_SUPPORTED], "completed",
                                  intent_source="llm")
    assert outcome.value == "partial"


def test_i5_canonicalize_anaphoric_tagged_policy_inherits_focus():
    """The audited run's exact path: the GU LLM tagged its policy output;
    canonicalization must inherit the focus (previously it fell back to
    the population canonical because the request text has no domains)."""
    tagged = [{"aspect": "policy_citation_original_text",
               "claim": "policy citations"}]
    out = _canonicalize_llm_outputs(
        tagged, "U00029", "F2", request_text=ANAPHORIC_EN,
        case_findings=_findings())
    assert out[0]["subject_ref"] == "F2"
    assert "High Withdrawal Frequency" in out[0]["claim"]


# --- canonicalize without text: population unchanged ----------------------

def test_canonicalize_no_text_population_unchanged():
    tagged = [{"aspect": "policy_citation_original_text",
               "claim": "policy citations"}]
    out = _canonicalize_llm_outputs(tagged, "U00029", "F2")
    assert out[0]["subject_ref"] == "U00029"


def test_i3_explicit_population_wording_beats_single_domain_match():
    """R2 (I3): an explicit population wording must keep the population
    canonical even when the linked-account domain matches exactly one
    finding (“the linked accounts population” ≠ the F5 finding)."""
    tagged = [{"aspect": "policy_citation_original_text",
               "claim": "policy citations"}]
    out = _canonicalize_llm_outputs(
        tagged, "U00029", None,
        request_text=("Which policy requirements apply to the linked "
                      "accounts population?"),
        case_findings=_findings())
    assert out[0]["subject_ref"] == "U00029"
    assert "关联账号" in out[0]["claim"]
