"""Coverage supplement — claim-kind-aware capability preference.

2026-11-03 final-acceptance incident (TASK-bf7fdbf887a1, U00033 「新账号
高频活动的判定阈值是怎样的？把该账号所有交易明细生成一份文件」): the
planner proposed only the transactions stream + file; the coverage
supplement then picked the registry-first RECORDS driller
(inspect_evidence) for the MIXED-requirement explanation output
([rule_explanation, transaction_records], subject F2) — structurally
EMPTY for a rule-based finding ("only aggregate feature values exist")
— while explain_signal (which declares rule_explanation and delivers
the exact threshold answer "account_age_days < 7 AND
trade_frequency_24h > 50") ranked later. The threshold question rendered
"当前证据不足以确定该项".

Pins:
- MIXED requirements + explanation claim → supplement picks the
  explanation-capable step (explain_signal), pinned to the finding
  subject, serving THAT output — the two requirements never substitute
  for each other (the records half keeps its own serving steps);
- mixed requirements + RETRIEVAL claim → records driller still preferred
  (no over-preference);
- single-requirement behaviors are byte-for-byte unchanged (pure
  rule_explanation → explain_signal; pure transaction_records → the
  stream-specialized records step per the existing U00010 B1 rule).
"""

from app.goal_resolution import (ClaimKind, GoalContract, RequiredOutput,
                                 ScopeLevel)
from app.skills import EvidenceType
from app.planning_coverage import (_ordered_eligible,
                                   coverage_supplement_steps)


def _out(output_id, kind, reqs, subject="F2"):
    return RequiredOutput(
        output_id=output_id, claim="test claim", claim_kind=ClaimKind(kind),
        subject_ref=subject, scope=ScopeLevel.FINDING_LEVEL,
        evidence_requirements=[EvidenceType(r) for r in reqs])


class _Step:
    def __init__(self, step_id, step_type, args=None):
        self.step_id = step_id
        self.type = step_type
        self.arguments = args or {}


def _contract(outputs):
    return GoalContract(contract_id="GC-t", subject_ref="U00033",
                        outputs=list(outputs))


def test_mixed_explanation_output_prefers_rule_capable_step():
    o1 = _out("O1", "explanation",
              ["rule_explanation", "transaction_records"])
    assert _ordered_eligible(o1)[0] == "explain_signal"
    # planner proposed only the F1 transactions stream + file (the real
    # incident shape): the supplement must append explain_signal pinned
    # to the output's OWN finding subject (F2), serving O1 — the F1
    # stream step never substitutes for the F2 rule explanation.
    plan = [_Step("S1", "inspect_transactions",
                  {"finding_id": "F1", "stream": "transactions"}),
            _Step("S2", "generate_artifact", {})]
    specs = coverage_supplement_steps(_contract([o1]), plan)
    assert [(s.step_type, s.finding_target, s.output_ids)
            for s in specs] == [("explain_signal", "F2", ["O1"])]


def test_mixed_retrieval_output_keeps_records_preference():
    # same mixed requirement set but a RETRIEVAL claim: no over-preference
    # — the records driller stays first (delivery-oriented claim)
    o = _out("O1", "retrieval",
             ["rule_explanation", "transaction_records"])
    ordered = _ordered_eligible(o)
    assert ordered[0] in ("inspect_evidence", "inspect_transactions")


def test_single_requirement_rule_output_unchanged():
    o = _out("O1", "explanation", ["rule_explanation"])
    assert _ordered_eligible(o) == ["explain_signal"]
    plan = [_Step("S1", "inspect_transactions",
                  {"finding_id": "F1", "stream": "transactions"})]
    specs = coverage_supplement_steps(_contract([o]), plan)
    assert [(s.step_type, s.finding_target) for s in specs] == \
        [("explain_signal", "F2")]


def test_single_requirement_records_output_unchanged():
    # pre-existing semantics, unchanged by the explanation preference:
    # the U00010 B1 stream specialization promotes inspect_transactions
    # only for CASE/ACCOUNT subjects; a FINDING-subject pure-records
    # output keeps registry order (inspect_evidence first) — exactly as
    # before this fix
    o_case = _out("O1", "retrieval", ["transaction_records"],
                  subject="U00033")
    assert _ordered_eligible(o_case)[0] == "inspect_transactions"
    o_finding = _out("O1", "retrieval", ["transaction_records"],
                     subject="F2")
    assert _ordered_eligible(o_finding)[0] == "inspect_evidence"


def test_requirements_do_not_substitute_in_served_check():
    # the F1-anchored transactions step covers a CASE-subject transactions
    # output (access path) but never the F2-subject mixed output — the
    # rule_explanation requirement is not "substituted" by the records
    # match when the subject conflicts
    from app.planning_coverage import step_covers_output
    o_mixed = _out("O1", "explanation",
                   ["rule_explanation", "transaction_records"])
    s1 = _Step("S1", "inspect_transactions",
               {"finding_id": "F1", "stream": "transactions"})
    assert step_covers_output(s1, o_mixed) is False
    o_case = _out("O2", "retrieval", ["transaction_records"], subject="U00033")
    assert step_covers_output(s1, o_case) is True
