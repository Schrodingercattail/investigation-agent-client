"""Cross-scope scalar isolation in _combine_summaries (U00033).

The same observation fact (e.g. candidates_found, totals, retrieval)
re-delivered under DIFFERENT declared analysis_scope values
(target_account vs related_account_set) is a complementary scope-shaped
delivery, NOT a contradiction. _combine_summaries partitions scalar
agreement by the payload's own declared analysis_scope:

- same-partition disagreement (or any disagreement involving an
  unscoped/None partition) → CONFLICTED, key omitted from combined,
  apply_postcondition downgrade unchanged (real conflicts never hidden);
- each declared scope internally unanimous but scopes differ → NOT a
  conflict, NOT merged into a top-level scalar; recorded per scope under
  combined[SCOPE_FACTS_KEY];
- at most one partition present → byte-identical to the historical
  unanimity semantics.

Promise boundary: this is cross-scope ISOLATED comparison only — no
field-name whitelist, no facet exemption, no general fact-level conflict
identification, no U00033-specific branch. The prior unapproved
_ANALYSIS_SHAPE_CONFLICT_KEYS whitelist is removed: a same-scope
disagreement of ANY key now surfaces (the whitelist wrongly suppressed
those).
"""

import pytest

from app.goal_postconditions import (
    SCOPE_FACTS_KEY,
    _combine_summaries,
    apply_postcondition,
    evaluate_output,
)
from app.goal_resolution import GoalContract, PostconditionFacet
from app.goal_resolution import RequiredOutput
from app.skills import EvidenceType


def _obs(summary, for_outputs=("O1",), outcome="success",
         tool="cross_account_trade_analysis"):
    return {"tool_name": tool, "outcome": outcome,
            "for_outputs": list(for_outputs), "summary": summary}


def _trade_summary(scope, candidates_found, candidates=(),
                   ratio=0.12, retrieval=None, totals=None):
    """A cross_account_trade_analysis observation summary (the real
    shape projected at agent_loop observation-normalization)."""
    return {
        "candidates_found": candidates_found,
        "candidates_emitted": len(candidates),
        "candidates": list(candidates),
        "totals": totals if totals is not None
        else {"transactions_retrieved": 10},
        "retrieval": retrieval if retrieval is not None
        else {"truncated": False},
        "accounts_analyzed": ["UA", "UB"],
        "analysis_scope": scope,
        "target_account": "UA",
        "target_account_metrics": {
            "opposite_trade_ratio": ratio,
            "source": "risk_platform.feature_evidence"},
    }


METRIC_FACET = PostconditionFacet.REVERSE_TRADE_METRIC_SEMANTICS.value


def _metric_output():
    return RequiredOutput(
        output_id="O1", claim="指标语义", claim_kind="retrieval",
        subject_ref="F3", scope="finding_level",
        evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS],
        postcondition_facets=[METRIC_FACET])


# ---------------------------------------------------------------------------
# 1-2, 10 — cross-scope isolation vs same-scope conflicts
# ---------------------------------------------------------------------------

class TestScopePartitioning:
    def test_cross_scope_difference_is_not_a_conflict(self):
        """Two declared scopes, each internally unanimous, differing
        values: NOT conflicted, NOT merged into a top-level scalar;
        recorded per scope under SCOPE_FACTS_KEY; lists still union;
        unanimous scalars stay top-level."""
        t = _obs(_trade_summary(
            "target_account", 2,
            totals={"transactions_retrieved": 10}))
        s = _obs(_trade_summary(
            "related_account_set", 8,
            totals={"transactions_retrieved": 40}))
        combined, conflicts = _combine_summaries([t, s])
        assert conflicts == []
        assert "candidates_found" not in combined
        assert SCOPE_FACTS_KEY in combined
        sf = combined[SCOPE_FACTS_KEY]
        assert sf["candidates_found"] == {
            "target_account": 2, "related_account_set": 8}
        assert sf["totals"] == {
            "target_account": {"transactions_retrieved": 10},
            "related_account_set": {"transactions_retrieved": 40}}
        # the scope discriminator itself is a per-scope fact, not a conflict
        assert sf["analysis_scope"] == {
            "target_account": "target_account",
            "related_account_set": "related_account_set"}
        # list-valued facts still union
        assert combined["accounts_analyzed"] == ["UA", "UB"]
        # the authoritative metric echo is identical in both scopes →
        # unanimous → stays a top-level scalar (the facet reads it here)
        assert combined["target_account_metrics"][
            "opposite_trade_ratio"] == 0.12

    def test_same_scope_disagreement_is_still_a_conflict(self):
        a = _obs(_trade_summary("related_account_set", 8))
        b = _obs(_trade_summary("related_account_set", 12))
        combined, conflicts = _combine_summaries([a, b])
        assert conflicts == ["candidates_found"]
        assert "candidates_found" not in combined
        assert SCOPE_FACTS_KEY not in combined

    def test_permutation_invariance(self):
        """Observation ORDER never decides the combined facts."""
        t = _obs(_trade_summary(
            "target_account", 2,
            totals={"transactions_retrieved": 10}))
        s = _obs(_trade_summary(
            "related_account_set", 8,
            totals={"transactions_retrieved": 40}))
        c1, k1 = _combine_summaries([t, s])
        c2, k2 = _combine_summaries([s, t])
        assert c1 == c2 and k1 == k2


# ---------------------------------------------------------------------------
# 3-4, 6 — conservative unscoped rules and legacy equivalence
# ---------------------------------------------------------------------------

class TestUnscopedConservative:
    def test_unscoped_vs_scoped_disagreement_stays_a_conflict(self):
        """An observation that declares NO scope cannot claim cross-scope
        complementarity — its disagreement with a scoped one is a real
        conflict."""
        a = _obs(_trade_summary(None, 5))
        b = _obs(_trade_summary("target_account", 2))
        combined, conflicts = _combine_summaries([a, b])
        assert conflicts == ["candidates_found"]
        assert "candidates_found" not in combined
        assert SCOPE_FACTS_KEY not in combined

    def test_unscoped_agreement_is_unanimous_top_level(self):
        a = _obs(_trade_summary(None, 2))
        b = _obs(_trade_summary("target_account", 2))
        combined, conflicts = _combine_summaries([a, b])
        assert conflicts == []
        assert combined["candidates_found"] == 2
        assert SCOPE_FACTS_KEY not in combined

    def test_non_string_scope_partitions_as_unscoped(self):
        a = _obs(_trade_summary(123, 5))          # non-string → unscoped
        b = _obs(_trade_summary("target_account", 2))
        combined, conflicts = _combine_summaries([a, b])
        # conservative: the int scope value is itself an unscoped scalar
        # fact, so it too conflicts with the declared scope string
        assert conflicts == ["analysis_scope", "candidates_found"]

    def test_all_unscoped_keeps_legacy_semantics(self):
        """With no declared scope anywhere the historical unanimity /
        union / conflict behavior is byte-identical."""
        s1 = {"record_count": 9, "complete": True,
              "rows": ["a", "b"], "artifact_id": "A1", "absent": None}
        s2 = {"record_count": 9, "complete": False,
              "rows": ["b", "c"], "artifact_id": "A1"}
        combined, conflicts = _combine_summaries(
            [_obs(s1, tool="finding_drilldown"),
             _obs(s2, tool="finding_drilldown")])
        assert combined == {"record_count": 9, "artifact_id": "A1",
                            "rows": ["a", "b", "c"]}
        assert conflicts == ["complete"]
        assert SCOPE_FACTS_KEY not in combined

    def test_single_scope_unanimous_multi_obs_has_no_scope_facts(self):
        a = _obs(_trade_summary("related_account_set", 8))
        b = _obs(_trade_summary("related_account_set", 8))
        combined, conflicts = _combine_summaries([a, b])
        assert conflicts == []
        assert combined["candidates_found"] == 8
        assert SCOPE_FACTS_KEY not in combined


# ---------------------------------------------------------------------------
# 7-9, 12 — evaluator + single write point
# ---------------------------------------------------------------------------

class TestEvaluatorAndApply:
    def test_two_scope_compound_keeps_metric_verdict(self):
        """The U00033 compound shape: both scopes' observations serve the
        metric output; the authoritative metric echo is unanimous so the
        facet verdict stands and NO conflict downgrade fires (previously
        the unapproved whitelist hid this; now there is simply no false
        conflict)."""
        t = _obs(_trade_summary(
            "target_account", 2,
            retrieval={"truncated": False, "target": {},
                       "counterparty": {}}))
        s = _obs(_trade_summary(
            "related_account_set", 8,
            retrieval={"truncated": False, "account_set": {}}))
        res = evaluate_output(_metric_output(), "U00033", [t, s])
        assert res.satisfaction.value == "satisfied"
        assert res.reason_code == "reverse_trade_metric_semantics_complete"
        assert res.conflicts == []
        satisfaction, polarity, reason = apply_postcondition(res)
        assert satisfaction.value == "satisfied"
        assert reason == "reverse_trade_metric_semantics_complete"

    def test_same_scope_real_conflict_still_downgrades(self):
        a = _obs(_trade_summary("related_account_set", 8))
        b = _obs(_trade_summary("related_account_set", 12))
        res = evaluate_output(_metric_output(), "U00033", [a, b])
        assert res.satisfaction.value == "satisfied"     # pre-apply
        assert res.conflicts == ["candidates_found"]
        satisfaction, polarity, reason = apply_postcondition(res)
        assert satisfaction.value == "unresolved"
        assert reason == "conflicting_observations"

    def test_whitelist_removed_same_scope_retrieval_conflict_surfaces(self):
        """The removed _ANALYSIS_SHAPE_CONFLICT_KEYS whitelist used to
        suppress same-scope retrieval/totals disagreements on the metric
        facet. No whitelist remains: such a real conflict is reported and
        downgrades at the single write point."""
        import app.goal_postconditions as gp
        assert not hasattr(gp, "_ANALYSIS_SHAPE_CONFLICT_KEYS")
        a = _obs(_trade_summary(None, 3, retrieval={"truncated": False}))
        b = _obs(_trade_summary(None, 3, retrieval={"truncated": True}))
        res = evaluate_output(_metric_output(), "U00033", [a, b])
        assert "retrieval" in res.conflicts
        satisfaction, polarity, reason = apply_postcondition(res)
        assert satisfaction.value == "unresolved"
        assert reason == "conflicting_observations"

    def test_missing_metric_evidence_is_never_satisfied(self):
        """No target_account_metrics echo in either scope → the facet
        yields nothing → honest UNRESOLVED comparison_facts_absent; the
        cross-scope isolation never manufactures the missing value."""
        t = _obs(_trade_summary("target_account", 2))
        s = _obs(_trade_summary("related_account_set", 8))
        for o in (t, s):
            o["summary"].pop("target_account_metrics")
        res = evaluate_output(_metric_output(), "U00033", [t, s])
        assert res.satisfaction.value == "unresolved"
        assert res.reason_code == "comparison_facts_absent"


# ---------------------------------------------------------------------------
# 11 — composer renders both scopes' deliveries from the union
# ---------------------------------------------------------------------------

class TestComposerUnion:
    def _candidate(self, cid, acc_a, acc_b):
        return {"candidate_id": cid, "match_type": "1_to_1",
                "account_a": acc_a, "side_a": "BUY",
                "account_b": acc_b, "side_b": "SELL", "symbol": "BTC",
                "max_time_gap_seconds": 0.5,
                "value_a": 100.0, "value_b": 99.0,
                "value_difference": 1.0, "value_difference_relative": 0.01,
                "transaction_ids_a": [f"{cid}-A"],
                "transaction_ids_b": [f"{cid}-B"]}

    def test_two_scope_section_renders_both_deliveries(self):
        from app.agent_loop import AgentLoopState, _compose_gsp_response
        contract = GoalContract(
            contract_id="GC-SP1", subject_ref="UA", intent_source="llm",
            outputs=[RequiredOutput(
                output_id="O1", claim="对冲候选", claim_kind="existence_check",
                subject_ref="UA", scope="case_level",
                evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS],
                satisfaction="satisfied", polarity="positive",
                detail="发现候选对冲交易关系。")])
        target_obs = _obs(_trade_summary(
            "target_account", 1,
            candidates=[self._candidate("CID-T1", "UA", "UB")]),
            for_outputs=["O1"])
        set_obs = _obs(_trade_summary(
            "related_account_set", 2,
            candidates=[self._candidate("CID-S1", "UA", "UC"),
                        self._candidate("CID-S2", "UB", "UD")]),
            for_outputs=["O1"])
        state = AgentLoopState(
            task_id="TASK-SP1", investigation_id="INV-SP1", case_id="UA",
            goal="该账户是否存在对冲交易", criteria_kind="question_answered",
            goal_contract=contract, observations=[target_obs, set_obs])
        answer = _compose_gsp_response(state)
        # the combined union carries BOTH scopes' candidate deliveries
        assert "CID-T1" in answer
        assert "CID-S1" in answer
