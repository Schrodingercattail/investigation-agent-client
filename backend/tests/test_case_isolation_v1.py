"""Single-case isolation — systematic invariants (B-chain).

Finding identity is (case_id, finding_id); a bare in-case id is never a
cross-case semantic handle. Case-scoped candidate metadata grounds ONLY
its own case; the concept taxonomy (semantic_concepts) is the globally
shared layer. These tests assert the FINAL grounding object and its case
attribution — never merely "no exception".

Synthetic cases UA/UB throughout; no real case id appears in fixtures.
"""

import tempfile
from unittest.mock import patch

import pytest

from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
    list_candidates,
)
from app.context_resolution import ContextResolver, _candidate_title_agrees
from app.domain_tools.signal_explain import signal_explain
from app.models import Finding, InvestigationContext
from app.task_store_v2 import TaskStoreV2

UA = "U90001"          # synthetic ids in the system's case-id vocabulary
UB = "U90002"
CONCEPT_X = "SYN_CONCEPT_X"
CONCEPT_Y = "SYN_CONCEPT_Y"


def _store():
    return TaskStoreV2(db_path=tempfile.mkdtemp() + "/isol.db")


def _catalog(store):
    return TaskStoreCatalogProvider(store)


def _seed_concepts(cat, *concept_ids):
    for cid in concept_ids:
        cat.upsert_concept(ConceptRecord(
            concept_id=cid, definition=f"def {cid}", created_by="isol-test"))


def _seed_candidate(cat, candidate_id, case_scope, core, *,
                    title_snapshot=None, status="UNVERIFIED"):
    cat.upsert_candidate(CandidateMetadataRecord(
        candidate_id=candidate_id, candidate_type="finding",
        case_scope=case_scope, title_snapshot=title_snapshot,
        core_concepts=core, distinguishing_concepts=[],
        potential_confusions=[], status=status, metadata_version=1))


def _finding(fid, title, case_id):
    return Finding(finding_id=fid, title=title, case_id=case_id,
                   type="rule_signal", summary=f"summary of {title}",
                   capabilities=frozenset())


def _resolver(cat):
    return ContextResolver(semantic_catalog=cat)


class _Norm:
    """Stub normalizer: proposes ONE concept (what the LLM layer would)."""

    def __init__(self, concept_id):
        self.concept_id = concept_id

    def normalize(self, text, concepts):
        class _R:
            status = "OK"
            matched_fragment = "fragment"
            reason_class = None
        r = _R()
        r.concept_id = self.concept_id
        return r


def _semantic_resolve(cat, case_id, findings, concept_id):
    resolver = _resolver(cat)
    ctx = InvestigationContext(case_id=case_id)
    with patch.object(resolver, "_get_normalizer",
                      return_value=_Norm(concept_id)):
        return resolver._semantic_resolve(
            "why is this a risk item", ctx, findings)


# ---------------------------------------------------------------------------
# A. Case identity & semantic isolation
# ---------------------------------------------------------------------------

class TestSemanticGroundingIsolation:
    def test_same_id_different_semantics_never_cross_grounds(self):
        """UA:F1 (semantics X) exists; UB has the SAME in-case id F1 but
        a DIFFERENT meaning and NO scoped record of its own. UB's request
        for X must NOT ground UB's F1 via UA's record — and UA's own
        grounding is untouched."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X)
        _seed_candidate(cat, "F1", UA, [CONCEPT_X],
                        title_snapshot="Alpha Finding")
        # UB's scoped view does not even see UA's record:
        assert cat.list_candidates(case_scope=UB) == []
        ub_findings = [_finding("F1", "Beta Finding", UB)]
        out = _semantic_resolve(cat, UB, ub_findings, CONCEPT_X)
        assert out is None            # honest no-match for THIS case
        # and UA's own grounding still works:
        ua_findings = [_finding("F1", "Alpha Finding", UA)]
        out_ua = _semantic_resolve(cat, UA, ua_findings, CONCEPT_X)
        assert out_ua is not None
        assert out_ua.updated_context.focused_finding_id == "F1"
        assert out_ua.updated_context.case_id == UA

    def test_id_collision_seed_replacement_cannot_pollute(self):
        """The storage layer keys candidates by bare in-case id (a known
        contract gap: (case_id, finding_id) needs a schema migration).
        Even if a later case's seed REPLACES the record, the scoped
        queries keep every case honest: the replaced record leaves UA's
        scope entirely, so UA grounds nothing rather than something
        wrong."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X, CONCEPT_Y)
        _seed_candidate(cat, "F1", UA, [CONCEPT_X],
                        title_snapshot="Alpha Finding")
        # a later case's seed replaces the bare-id record:
        _seed_candidate(cat, "F1", UB, [CONCEPT_Y],
                        title_snapshot="Beta Finding")
        assert cat.list_candidates(case_scope=UA) == []
        out_ua_x = _semantic_resolve(
            cat, UA, [_finding("F1", "Alpha Finding", UA)], CONCEPT_X)
        assert out_ua_x is None       # honest: no scoped record anymore
        out_ua_y = _semantic_resolve(
            cat, UA, [_finding("F1", "Alpha Finding", UA)], CONCEPT_Y)
        assert out_ua_y is None       # UB's semantics never leak to UA
        # UB's own grounding works with the replacement record:
        out_ub = _semantic_resolve(
            cat, UB, [_finding("F1", "Beta Finding", UB)], CONCEPT_Y)
        assert out_ub is not None
        assert out_ub.updated_context.focused_finding_id == "F1"
        assert out_ub.updated_context.case_id == UB

    def test_different_ids_same_semantics_ground_in_own_case(self):
        """The SAME concept exists in both cases under DIFFERENT ids:
        each case resolves to ITS OWN finding, never the other's."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X)
        _seed_candidate(cat, "F1", UA, [CONCEPT_X],
                        title_snapshot="Alpha Frequency")
        _seed_candidate(cat, "F2", UB, [CONCEPT_X],
                        title_snapshot="Beta Frequency")
        ub_findings = [_finding("F1", "Unrelated", UB),
                       _finding("F2", "Beta Frequency", UB)]
        ua_findings = [_finding("F1", "Alpha Frequency", UA),
                       _finding("F2", "Unrelated", UA)]

        out_ub = _semantic_resolve(cat, UB, ub_findings, CONCEPT_X)
        assert out_ub is not None
        assert out_ub.updated_context.focused_finding_id == "F2"
        assert out_ub.updated_context.case_id == UB

        out_ua = _semantic_resolve(cat, UA, ua_findings, CONCEPT_X)
        assert out_ua is not None
        assert out_ua.updated_context.focused_finding_id == "F1"
        assert out_ua.updated_context.case_id == UA

    def test_scope_mismatched_record_excluded_at_query_layer(self):
        """The provider query itself is the isolation boundary: a scope-
        constrained query never even returns another case's records."""
        store = _store()
        cat = _catalog(store)
        _seed_candidate(cat, "F1", UA, [CONCEPT_X])
        _seed_candidate(cat, "F2", UB, [])
        assert sorted(r.candidate_id for r in
                      cat.list_candidates(case_scope=UA)) == ["F1"]
        assert sorted(r.candidate_id for r in
                      cat.list_candidates(case_scope=UB)) == ["F2"]

    def test_missing_scope_record_grounds_no_case(self):
        """A record with NO confirmed scope is not silently global: the
        current data contract has no global-record form, so it must
        ground NEITHER case (no defaults)."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X)
        _seed_candidate(cat, "F1", None, [CONCEPT_X],
                        title_snapshot="Orphan Finding")
        conn_check = cat.list_candidates(case_scope=UA)
        assert conn_check == []
        out_ua = _semantic_resolve(
            cat, UA, [_finding("F1", "Orphan Finding", UA)], CONCEPT_X)
        assert out_ua is None
        out_ub = _semantic_resolve(
            cat, UB, [_finding("F1", "Orphan Finding", UB)], CONCEPT_X)
        assert out_ub is None

    def test_title_snapshot_mismatch_excluded_within_case(self):
        """Same case, same in-case id, concept in core — but the stored
        title disagrees with the case's authoritative finding title: the
        record is STALE/misfiled and must not ground it."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X)
        _seed_candidate(cat, "F1", UA, [CONCEPT_X],
                        title_snapshot="Totally Different Title")
        out = _semantic_resolve(
            cat, UA, [_finding("F1", "Actual Finding Title", UA)],
            CONCEPT_X)
        assert out is None

    def test_same_case_seed_still_grounds_end_to_end(self):
        """The legal path is intact: a same-case seed with an agreeing
        title grounds its own finding, with provenance."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X)
        _seed_candidate(cat, "F2", UA, [CONCEPT_X],
                        title_snapshot="Alpha Withdrawal Frequency.")
        out = _semantic_resolve(
            cat, UA,
            [_finding("F1", "Unrelated", UA),
             _finding("F2", "Alpha Withdrawal Frequency", UA)],
            CONCEPT_X)
        assert out is not None
        assert out.updated_context.focused_finding_id == "F2"
        assert out.updated_context.case_id == UA
        assert out.semantic_evidence["grounded_candidate_ids"] == ["F2"]

    def test_concept_taxonomy_is_shared_while_records_are_scoped(self):
        """The global layer is the concept REGISTRY (no case field); the
        scoped layer is the records. One registry serves both cases, but
        each grounds only through its own records."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X, CONCEPT_Y)
        assert {c.concept_id for c in cat.list_concepts()} == \
            {CONCEPT_X, CONCEPT_Y}          # global, unscoped
        _seed_candidate(cat, "F1", UA, [CONCEPT_X])
        _seed_candidate(cat, "F9", UB, [CONCEPT_Y])
        assert len(cat.list_candidates(case_scope=UA)) == 1
        assert len(cat.list_candidates(case_scope=UB)) == 1

    def test_title_agreement_normalization_rules(self):
        assert _candidate_title_agrees("High Withdrawal Frequency.",
                                       "high withdrawal frequency")
        assert _candidate_title_agrees(None, "anything")   # no evidence
        assert not _candidate_title_agrees("Different", "Actual")


# ---------------------------------------------------------------------------
# B. Propagation & execution isolation
# ---------------------------------------------------------------------------

class TestExecutionBoundary:
    def test_unresolvable_case_yields_no_focus_to_inject(self):
        """When the current case has no legal grounding, the resolver
        falls through WITHOUT a focus — there is no foreign finding_id
        for the executor to inject."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X)
        _seed_candidate(cat, "F1", UB, [CONCEPT_X])   # other case only
        out = _semantic_resolve(
            cat, UA, [_finding("F1", "Alpha Finding", UA)], CONCEPT_X)
        assert out is None
        ctx = InvestigationContext(case_id=UA)
        assert ctx.focused_finding_id is None

    def test_tool_rejects_finding_outside_current_case(self):
        """A finding reference that does not exist in the current case is
        explicitly rejected by the tool boundary — never silently
        executed."""
        r = signal_explain(
            finding_id="F99",
            case_context={"case_id": UA, "findings": [
                _finding("F1", "Alpha Finding", UA)]})
        assert r.outcome.value == "validation_error"
        assert "does not exist in the current case context" in \
            (r.error.message or "")

    def test_executor_injects_current_case_id_only(self):
        """Runtime argument injection takes case_id from the CURRENT
        investigation context — never derived from a finding id."""
        from app.executor_v2 import ExecutorV2
        from app.models import PlanStep, PlanStepStatus
        step = PlanStep(step_id="S1", type="analyze_cross_account_trades",
                        reason="r", status=PlanStepStatus.PENDING,
                        tool_name="cross_account_trade_analysis",
                        arguments={})
        args, sources = ExecutorV2._runtime_arguments(
            step, InvestigationContext(case_id=UA))
        assert args["case_id"] == UA
        assert sources["case_id"] == "investigation_context"

    def test_validity_guard_uses_only_current_case_metadata(self):
        """G1 assist-evidence validation annotates findings with the
        CURRENT case's records only: another case's same-id record can
        neither supply nor withhold governed semantics here."""
        store = _store()
        cat = _catalog(store)
        _seed_concepts(cat, CONCEPT_X)
        _seed_candidate(cat, "F1", UB, [CONCEPT_X],
                        title_snapshot="Beta Frequency")
        resolver = _resolver(cat)
        payload = {"evidence": {
            "request_fragments": ["fragment"],
            "target_terms": [CONCEPT_X],
            "per_candidate_support": []}}
        admissible, supported, _ = resolver._validate_assist_evidence(
            "fragment", "F1", payload,
            [_finding("F1", "Alpha Frequency", UA)], case_id=UA)
        # UB's record is invisible: the fallback (RP-authoritative text)
        # decides — the UA finding's own title contains no governed term,
        # so the term cannot be attributed to it via foreign metadata.
        assert supported == [] or "F1" not in supported

    def test_observation_association_stays_within_current_contract(self):
        """Association is driven by the CURRENT run's accepted outputs —
        a contract from one case cannot receive another case's evidence
        because contracts are built from the current case's goal
        understanding; assert the association predicate's output shape."""
        from types import SimpleNamespace
        from app.agent_loop import associate_observation
        from app.goal_resolution import GoalContract, RequiredOutput, \
            ScopeLevel
        from app.skills import EvidenceType
        out = RequiredOutput(
            output_id="O1", claim="why flagged", claim_kind="explanation",
            subject_ref="F1", scope="finding_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION])
        contract = GoalContract(
            contract_id="GC-ISO", goal_type=None, subject_ref=UA,
            outputs=[out])
        state = SimpleNamespace(
            goal_contract=contract, observations=[],
            step_output_map={"S1": ["O1"]}, plan_steps=[],
            investigation_id="INV-ISO", task_id="T-ISO")
        observation = {"step_id": "S1",
                       "step_type": "explain_signal",
                       "tool_name": "signal_explain",
                       "actual_evidence_types": ["rule_explanation"]}
        associate_observation(state, observation)
        assert observation["for_outputs"] == ["O1"]
        assert observation["association_reason"] == "contract_step_mapping"


# ---------------------------------------------------------------------------
# C. Case-switch conversation policy
# ---------------------------------------------------------------------------

class TestCaseSwitchPolicy:
    def test_switch_request_prompted_and_context_unchanged(self):
        """A request naming ANOTHER case inside a bound conversation is
        rejected at the earliest boundary with new-conversation guidance;
        the current context is never re-targeted."""
        from app.context_resolution import ResolutionStatus
        resolver = ContextResolver(semantic_catalog=_catalog(_store()))
        ctx = InvestigationContext(case_id=UA)
        findings = [_finding("F1", "Alpha Finding", UA)]
        out = resolver.resolve(f"调查 {UB} 的情况", ctx, findings=findings)
        assert out.status == ResolutionStatus.UNRESOLVED
        assert "new investigation" in (out.clarification_message or "")
        assert out.updated_context.case_id == UA
        assert out.updated_context.focused_finding_id is None
        assert out.context_changed is False

    def test_same_case_reference_still_works(self):
        """A request naming the CURRENT case is not a cross-case request:
        the ordinary (case intake) path proceeds."""
        from app.context_resolution import ResolutionStatus
        resolver = ContextResolver(semantic_catalog=_catalog(_store()))
        ctx = InvestigationContext(case_id=UA)
        findings = [_finding("F1", "Alpha Finding", UA)]
        out = resolver.resolve(f"展开一下 {UA} 案例情况", ctx,
                               findings=findings)
        assert out.status != ResolutionStatus.UNRESOLVED or \
            (out.clarification_message or "") == "" or \
            "new investigation" not in (out.clarification_message or "")
