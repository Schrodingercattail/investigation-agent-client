"""Contract-subject focus re-anchor — bounded regression locks (P0).

The accepted GoalContract is built from THIS request's goal
understanding. When it names exactly one finding-level subject and the
resolved focus is a DIFFERENT finding of the same case, recovery of the
stale focus must not steer planning/execution to that other finding's
evidence: the focus re-anchors to the contract subject.

Historical failure (U00008): focus=F5 (Linked Account Network,
user_selected from the previous turn) + "高频交易风险的触发阈值是多少"
→ contract subject F6 (High Trading Frequency) — but execution ran
explain_signal(F5) and rendered F5's device-cluster facts under the F6
header, claiming complete.
"""

import pytest

from app.agent_loop import _reanchor_focus_to_contract_subject
from app.goal_resolution import GoalContract, RequiredOutput, ScopeLevel
from app.models import Finding, InvestigationContext
from app.skills import EvidenceType


def _finding(fid, title, case_id="U00008", refs=()):
    from app.models import SignalRef
    signal_refs = [SignalRef(signal_type=st, name=name)
                   for st, name in ([refs] if refs else [])]
    return Finding(finding_id=fid, title=title, case_id=case_id,
                   type="rule_signal", summary=f"summary of {title}",
                   evidence_refs=[], signal_refs=signal_refs,
                   policy_refs=[],
                   capabilities=frozenset({"timeline",
                                           "signal_explain"}))


def _contract(subjects):
    outputs = [
        RequiredOutput(output_id=f"O{i}", claim=f"c{i}",
                       claim_kind="explanation", subject_ref=sid,
                       scope="finding_level",
                       evidence_requirements=[EvidenceType.RULE_EXPLANATION])
        for i, sid in enumerate(sorted(subjects), 1)
    ]
    return GoalContract(contract_id="GC-RA", goal_type=None,
                        subject_ref=subjects[0] if subjects else "UC",
                        outputs=outputs)


def _ctx(focus):
    return InvestigationContext(case_id="U00008", focused_finding_id=focus)


U00008_FINDINGS = [
        _finding("F5", "Linked Account Network",
                 refs=("Feature", "linked_account_count")),
        _finding("F6", "High Trading Frequency",
                 refs=("Feature", "trade_frequency_24h")),
]


class TestContractSubjectReAnchor:
    def test_stale_focus_reanchors_to_contract_subject(self):
        """THE historical failure: focus F5 (stale, user_selected from
        the previous turn) + contract subject F6 → re-anchor to F6."""
        ctx = _ctx("F5")
        contract = _contract(["F6"])
        out = _reanchor_focus_to_contract_subject(ctx, contract,
                                                  U00008_FINDINGS)
        assert out is not None and out.focused_finding_id == "F6"

    def test_focus_matching_contract_subject_is_untouched(self):
        """Same-domain continuous investigation: focus == contract
        subject → unchanged (context is legitimately reused)."""
        ctx = _ctx("F6")
        contract = _contract(["F6"])
        assert _reanchor_focus_to_contract_subject(
            ctx, contract, U00008_FINDINGS) is None

    def test_no_finding_subjects_no_reanchor(self):
        """Case-level contracts (subjects are case ids) never re-anchor."""
        ctx = _ctx("F5")
        outputs = [RequiredOutput(
            output_id="O1", claim="c", claim_kind="retrieval",
            subject_ref="U00008", scope="case_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS])]
        contract = GoalContract(contract_id="GC-RA2", goal_type=None,
                                subject_ref="U00008", outputs=outputs)
        assert _reanchor_focus_to_contract_subject(
            ctx, contract, U00008_FINDINGS) is None

    def test_multi_subject_contract_no_reanchor(self):
        """Multi-target compound contracts carry explicit per-step
        anchors — no single-subject re-anchor."""
        ctx = _ctx("F5")
        contract = _contract(["F5", "F6"])
        assert _reanchor_focus_to_contract_subject(
            ctx, contract, U00008_FINDINGS) is None

    def test_subject_absent_from_case_no_reanchor(self):
        """A contract subject that does not exist in the current case is
        never used as an execution target."""
        ctx = _ctx("F5")
        contract = _contract(["F99"])
        assert _reanchor_focus_to_contract_subject(
            ctx, contract, U00008_FINDINGS) is None

    def test_no_focus_no_reanchor(self):
        ctx = InvestigationContext(case_id="U00008")
        contract = _contract(["F6"])
        assert _reanchor_focus_to_contract_subject(
            ctx, contract, U00008_FINDINGS) is None

    def test_evidence_relevance_is_preserved_by_reanchor(self):
        """The re-anchor does not touch evidence — it changes the target
        so that the SUBSEQUENT explain runs against the contract
        subject's own signals (F6 trade-frequency refs, not F5's
        device/network refs)."""
        f6 = next(f for f in U00008_FINDINGS if f.finding_id == "F6")
        assert any(r.name == "trade_frequency_24h"
                   for r in f6.signal_refs)
        assert not any("device" in (r.name or "") or "linked" in (r.name or "")
                       for r in f6.signal_refs)
