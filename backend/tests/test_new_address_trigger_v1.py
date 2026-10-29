"""withdrawal_new_address_status trigger discipline.

Pins the root-cause fix for the F3 policy-ask case (2026-10-26 audit):
a finding TITLE whose own words contain the aspect keywords ("First
Withdrawal to New Address") is a REFERENCE, never a per-record
retrieval demand. mentions_withdrawal_new_address masks known finding
titles before matching — genuine per-record asks and explicit compound
asks still trigger; the turn-path identify_aspects contract is
unchanged (findings are not threaded there in this round).

Generic fixtures — no real case data is required.
"""
import pytest

from app.investigation_concepts import (
    mentions_withdrawal_new_address,
    strip_finding_title_mentions,
)


class _F:
    def __init__(self, finding_id, title):
        self.finding_id = finding_id
        self.title = title


FINDINGS = [
    _F("F1", "ML Pattern Detection Signal"),
    _F("F2", "High Withdrawal Frequency"),
    _F("F3", "First Withdrawal to New Address"),
    _F("F4", "Shared Device Relationships"),
]

# (text, expected) — mentions_withdrawal_new_address WITH findings
TRIGGER_CASES = [
    # --- policy asks naming the finding: NEVER the per-record check ----
    ("发现 F3（First Withdrawal to New Address）适用哪些政策要求？", False),
    ("发现 F3 适用哪些政策要求？", False),
    ("Which policy requirements apply to finding F3 "
     "(First Withdrawal to New Address)?", False),
    # unbracketed title mention, natural zh/en word order
    ("First Withdrawal to New Address 这个发现适用哪些政策要求", False),
    ("which policy requirements apply to the finding "
     "First Withdrawal to New Address", False),
    # case / separator variations of the title
    ("发现 F3（first withdrawal to new address）适用哪些政策要求？", False),
    ("发现 F3（FIRST  WITHDRAWAL   TO  NEW  ADDRESS）适用哪些政策要求？",
     False),
    ("发现 F3（First-Withdrawal-to-New-Address）适用哪些政策要求？", False),
    # policy ask with a DIFFERENT title mentioned
    ("High Withdrawal Frequency 与政策要求的关系是什么？", False),
    # --- genuine per-record asks: STILL trigger -------------------------
    ("哪些提现被标记为新地址？", True),
    ("逐笔列出哪些提现是新地址。", True),
    ("哪些提现是新地址？", True),
    ("把被标记为新地址的提现列出来。", True),
    ("List the withdrawals flagged as new addresses.", True),
    ("Which withdrawals were flagged as new addresses?", True),
    ("which withdrawals went to a newly encountered address", True),
    # --- explicit compound asks: STILL trigger --------------------------
    ("发现 F3（First Withdrawal to New Address）适用哪些政策要求，"
     "并逐笔列出哪些提现是新地址。", True),
    ("For finding F3 (First Withdrawal to New Address): which policy "
     "requirements apply, and list the withdrawals flagged as new "
     "addresses.", True),
]


@pytest.mark.parametrize("text,expected", TRIGGER_CASES)
def test_trigger_discipline(text, expected):
    got = mentions_withdrawal_new_address(text, FINDINGS)
    assert got is expected, (text, got, expected)


def test_default_arg_keeps_legacy_behavior():
    """Callers that do not supply findings (turn path) keep the
    historical predicate behavior byte-for-byte."""
    text = "发现 F3（First Withdrawal to New Address）适用哪些政策要求？"
    assert mentions_withdrawal_new_address(text) is True


def test_mask_helper_only_touches_title_spans():
    masked = strip_finding_title_mentions(
        "发现 F3（First Withdrawal to New Address）适用哪些政策要求，"
        "并逐笔列出哪些提现是新地址。", FINDINGS)
    # the quoted title is gone…
    assert "New Address）" not in masked
    # …but the user's own demand words survive
    assert "提现" in masked and "新地址" in masked
    # titles shorter than the distinctiveness bound are untouched
    assert strip_finding_title_mentions("F3 提现新地址", FINDINGS) == \
        "F3 提现新地址"


# --- candidate-stage integration (goal path) ---------------------------

def _proposal(text):
    from app.goal_resolution import deterministic_contract_proposal
    fired: list = []
    proposal = deterministic_contract_proposal(
        text, "F3", "C1", case_findings=FINDINGS, fired_aspects=fired)
    return proposal, fired


def test_policy_ask_mounts_no_new_address_candidate():
    for text in ("发现 F3（First Withdrawal to New Address）适用哪些政策"
                 "要求？",
                 "Which policy requirements apply to finding F3 "
                 "(First Withdrawal to New Address)?"):
        proposal, fired = _proposal(text)
        assert "withdrawal_new_address_status" not in fired, text
        if proposal is not None:
            facets = {f for o in proposal["required_outputs"]
                      for f in o["postcondition_facets"]}
            assert "withdrawal_new_address_status" not in facets, text


def test_genuine_and_compound_asks_still_mount_the_aspect():
    for text in ("哪些提现被标记为新地址？",
                 "List the withdrawals flagged as new addresses.",
                 "发现 F3（First Withdrawal to New Address）适用哪些政策"
                 "要求，并逐笔列出哪些提现是新地址。"):
        proposal, fired = _proposal(text)
        assert "withdrawal_new_address_status" in fired, text
        assert proposal is not None
        facets = {f for o in proposal["required_outputs"]
                  for f in o["postcondition_facets"]}
        assert "withdrawal_new_address_status" in facets, text
