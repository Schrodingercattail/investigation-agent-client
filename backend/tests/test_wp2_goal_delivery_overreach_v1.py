"""WP-2 goal-overreach fix — unit/component regression for the
deterministic delivery-semantics sanitizer in build_goal_contract.

Root cause (real incident): the GU LLM coined the claim_description
"Export all trade records of the investigated account under case U00010
as a downloadable artifact." for the ask "generate all trade records of
this account as an artifact" — introducing "Export"/"downloadable"
delivery semantics the user never expressed and the system does not
have. Fix under test: (1) LLM-coined claim texts are stripped of
delivery-capability words the user never used; (2) modes the USER
explicitly requested are preserved verbatim in the claim and flagged
unsupported in a bounded detail note (never silently dropped, never
reported done); (3) legitimate context completion (case/account) is
untouched.

Frozen requirements: real cases A-E (kickoff task spec §五).
"""

import pytest

from app.goal_resolution import (
    GoalResolutionStatus,
    GoalRepresentation,
    build_goal_contract,
    _delivery_overreach_words,
    _strip_delivery_overreach,
)

CASE = "U00010"


def _rep(request_text, proposal):
    return GoalRepresentation(
        case_id=CASE,
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        goal_type="artifact_export",
        subject_type="case", subject_ref=CASE, source="llm",
        request_text=request_text,
        contract_proposal={"required_outputs": proposal})


def _proposal(claim_desc, claim=None):
    return [{
        "claim": claim or "Generate an artifact containing all trade "
                          "records of the investigated account",
        "claim_description": claim_desc,
        "claim_kind": "retrieval",
        "subject_ref": CASE,
        "scope": "case_level",
        "evidence_requirements": ["transaction_records"],
    }]


USER_ASK = "generate all trade records of this account as an artifact"


# --- Case A: the real incident (over-claim sanitized) -----------------------

class TestCaseARealIncident:
    def test_downloadable_stripped_from_claim_description(self):
        rep = _rep(USER_ASK, _proposal(
            "Export all trade records of the investigated account under "
            "case U00010 as a downloadable artifact."))
        contract, err = build_goal_contract(rep, CASE)
        assert err is None and contract is not None
        out = contract.outputs[0]
        assert "downloadable" not in out.claim_description.lower()
        assert "export" not in out.claim_description.lower()

    def test_generate_artifact_intent_preserved(self):
        rep = _rep(USER_ASK, _proposal(
            "Export all trade records of the investigated account under "
            "case U00010 as a downloadable artifact."))
        contract, _ = build_goal_contract(rep, CASE)
        out = contract.outputs[0]
        # the GENERATE intent and the record scope survive sanitization
        assert "trade records" in out.claim_description.lower()
        assert "u00010" in out.claim_description.lower()
        assert out.delivery in ("detail_list", "file_export")


# --- Case B: explicit download request (intent preserved + unsupported
#     note) ------------------------------------------------------------------

class TestCaseBExplicitDownload:
    def test_download_intent_kept_and_flagged_unsupported(self):
        ask = "generate an artifact and let me download it"
        proposal = _proposal(
            "Generate an artifact with all trade records and let the "
            "user download it.")
        rep = _rep(ask, proposal)
        contract, err = build_goal_contract(rep, CASE)
        assert err is None and contract is not None
        out = contract.outputs[0]
        # the USER used "download" → their intent is NOT silently deleted
        assert "download" in out.claim_description.lower()
        # the deterministic unsupported note is present
        assert out.detail and "not supported" in out.detail
        assert "download" in out.detail.lower()


# --- Case C: explicit email request ------------------------------------------

class TestCaseCExplicitEmail:
    def test_email_intent_kept_and_flagged_unsupported(self):
        ask = "generate an artifact and email it to me"
        proposal = _proposal(
            "Generate an artifact with all trade records and email it "
            "to the user.")
        rep = _rep(ask, proposal)
        contract, err = build_goal_contract(rep, CASE)
        assert err is None and contract is not None
        out = contract.outputs[0]
        assert out.detail and "not supported" in out.detail
        assert "email" in out.detail.lower()
        # the LLM must not have claimed the email was performed
        rejected = None
        assert "email it to the user" in out.claim_description.lower() or \
            "email" in out.claim_description.lower()


# --- Case D: clean generation ask (no-op) ------------------------------------

class TestCaseDCleanGeneration:
    def test_no_delivery_words_no_change(self):
        ask = "create an artifact containing all transactions"
        proposal = _proposal(
            "Create an artifact containing all transactions of the "
            "account.")
        rep = _rep(ask, proposal)
        contract, err = build_goal_contract(rep, CASE)
        assert err is None and contract is not None
        out = contract.outputs[0]
        # nothing to sanitize: the detail unsupported note is absent and
        # the claim words survive untouched
        assert not (out.detail and "not supported" in out.detail)
        assert "artifact containing all transactions" in \
            out.claim_description.lower()


# --- Case E: legitimate context completion survives --------------------------

class TestCaseEContextCompletion:
    def test_case_context_completion_preserved(self):
        rep = _rep(USER_ASK, _proposal(
            "Generate an artifact containing all trade records of the "
            "investigated account under case U00010."))
        contract, err = build_goal_contract(rep, CASE)
        assert err is None and contract is not None
        out = contract.outputs[0]
        # case/account context completion is legal and untouched
        assert "u00010" in out.claim_description.lower()
        assert "trade records" in out.claim_description.lower()
        assert "downloadable" not in out.claim_description.lower()


# --- helper-level pins --------------------------------------------------------

class TestHelperPins:
    def test_overreach_detection_requires_absence_in_user_text(self):
        assert _delivery_overreach_words(
            "export the downloadable artifact", "create an artifact") == \
            ["export", "downloadable"]
        # words the USER used are never over-reach
        assert _delivery_overreach_words(
            "downloadable artifact", "give me a downloadable artifact") \
            == []

    def test_strip_removes_all_inflections(self):
        text, changed = _strip_delivery_overreach(
            "Export a downloadable artifact and email it", [
                "export", "downloadable", "email"])
        assert changed
        assert "export" not in text.lower()
        assert "downloadable" not in text.lower()
        assert "email" not in text.lower()
