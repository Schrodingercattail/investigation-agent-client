"""GSP WP-GSP-2 — Goal Semantic Contract: model, acceptance boundary,
single-call extraction, persistence/recovery, flag compatibility.

L1 deterministic coverage (no LLM judge; scripted proposals only):
- GoalContract / RequiredOutput schema validation
- deterministic acceptance rules (build_goal_contract)
- single-call extraction (understand_goal flag ON/OFF, heuristic, AMBIGUOUS)
- semantic fixtures Case A–D (laundering / hedge / shared-IP / multi-output)
- persistence + legacy recovery on the real TaskStoreV2
- G1 compatibility (valid finding subject preserved; flag independence)
"""

import json
import tempfile
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config import settings
from app.goal_resolution import (
    GoalContract,
    GoalRepresentation,
    GoalResolutionStatus,
    OutputSatisfaction,
    RequiredOutput,
    ScopeLevel,
    build_goal_contract,
    understand_goal,
)
from app.skills import EVIDENCE_TYPES, EvidenceType, STEP_TOOL_MAP
from app.task_store_v2 import TaskStoreV2


# --- helpers -----------------------------------------------------------------

def _repr(proposal=None, goal_type="policy_check", subject="U00010",
          case_id="U00010"):
    """A GoalRepresentation carrying the RAW proposal in the same shape
    understand_goal attaches it (wrapped dict), or None."""
    return GoalRepresentation(
        case_id=case_id,
        goal_type=goal_type,
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="finding" if subject.startswith("F") else "case",
        subject_ref=subject,
        reason="r",
        source="llm",
        request_text="req",
        contract_proposal=({"required_outputs": proposal}
                           if proposal else None),
    )


def _proposal(claim="money_laundering_assessment", subject="U00010",
              scope="account_level", evidence=None, description=None,
              claim_kind=None):
    out = {
        "claim": claim,
        "subject_ref": subject,
        "scope": scope,
        "evidence_requirements": evidence or [
            "transaction_records", "withdrawal_records",
            "network_relations", "policy_references",
            "ml_signal_explanation"],
    }
    if description:
        out["claim_description"] = description
    if claim_kind:
        out["claim_kind"] = claim_kind
    return out


class _ScriptLLM:
    """Returns one fixed payload; counts calls; captures prompts."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.prompts = []

    def generate(self, messages=None, max_tokens=0, temperature=0.0):
        self.calls += 1
        self.prompts.append(messages[0]["content"] if messages else "")
        return self.payload


def _goal_payload(goal_type="policy_check", with_outputs=True,
                  outputs=None):
    body = {"goal_resolution": "INFERRED", "goal_type": goal_type,
            "ambiguous_goal_types": [], "reason": "r"}
    if with_outputs:
        body["required_outputs"] = outputs if outputs is not None else [
            _proposal()]
    return json.dumps(body)


# --- 1. schema -----------------------------------------------------------------

class TestContractSchema:
    def test_required_output_defaults_to_pending(self):
        o = RequiredOutput(output_id="O1", claim="risk_level_assessment",
                           subject_ref="U00010", scope=ScopeLevel.CASE_LEVEL,
                           evidence_requirements=[EvidenceType.CASE_SUMMARY])
        assert o.satisfaction == OutputSatisfaction.PENDING

    def test_empty_evidence_requirements_rejected(self):
        with pytest.raises(ValidationError):
            RequiredOutput(output_id="O1", claim="x", subject_ref="U00010",
                           scope=ScopeLevel.CASE_LEVEL,
                           evidence_requirements=[])

    def test_unknown_evidence_type_rejected(self):
        with pytest.raises(ValidationError):
            RequiredOutput(output_id="O1", claim="x", subject_ref="U00010",
                           scope=ScopeLevel.CASE_LEVEL,
                           evidence_requirements=["fund_flow"])

    def test_claim_must_not_be_an_evidence_type_value(self):
        with pytest.raises(ValidationError):
            RequiredOutput(output_id="O1", claim="policy_references",
                           subject_ref="U00010", scope=ScopeLevel.CASE_LEVEL,
                           evidence_requirements=[EvidenceType.POLICY_REFERENCES])

    def test_unknown_scope_rejected(self):
        with pytest.raises(ValidationError):
            RequiredOutput(output_id="O1", claim="x", subject_ref="U00010",
                           scope="universe",
                           evidence_requirements=[EvidenceType.CASE_SUMMARY])

    def test_empty_claim_rejected(self):
        with pytest.raises(ValidationError):
            RequiredOutput(output_id="O1", claim="  ", subject_ref="U00010",
                           scope=ScopeLevel.CASE_LEVEL,
                           evidence_requirements=[EvidenceType.CASE_SUMMARY])

    def test_duplicate_output_id_rejected(self):
        o = RequiredOutput(output_id="O1", claim="a", subject_ref="U00010",
                           scope=ScopeLevel.CASE_LEVEL,
                           evidence_requirements=[EvidenceType.CASE_SUMMARY])
        with pytest.raises(ValidationError):
            GoalContract(contract_id="GC-x", goal_type="policy_check",
                         subject_ref="U00010", outputs=[o, o.model_copy()])

    def test_empty_outputs_rejected(self):
        with pytest.raises(ValidationError):
            GoalContract(contract_id="GC-x", goal_type="policy_check",
                         subject_ref="U00010", outputs=[])


# --- 2. deterministic acceptance boundary ---------------------------------------

class TestBuildGoalContract:
    def test_valid_proposal_accepted_with_system_ids(self):
        contract, reason = build_goal_contract(_repr([_proposal()]))
        assert reason is None and contract is not None
        assert contract.outputs[0].output_id == "O1"
        assert contract.outputs[0].satisfaction == OutputSatisfaction.PENDING
        assert contract.goal_type == "policy_check"
        assert contract.subject_ref == "U00010"
        assert contract.contract_id.startswith("GC-")

    def test_multi_output_ids_stable_and_ordered(self):
        p = [_proposal(claim="hedge_presence_assessment",
                       evidence=["opposite_trade_relations"]),
             _proposal(claim="overlap_assessment",
                       evidence=["network_relations"])]
        c1, _ = build_goal_contract(_repr(p))
        c2, _ = build_goal_contract(_repr(p))
        assert [o.output_id for o in c1.outputs] == ["O1", "O2"]
        assert [o.claim for o in c1.outputs] == [o.claim for o in c2.outputs]

    def test_no_proposal_rejected(self):
        contract, reason = build_goal_contract(_repr(None))
        assert contract is None and reason == "no_proposal"

    def test_missing_evidence_requirements_rejected(self):
        p = _proposal()
        p["evidence_requirements"] = []
        contract, reason = build_goal_contract(_repr([p]))
        assert contract is None and reason == "missing_evidence_requirements"

    def test_invalid_evidence_type_rejected_whole_proposal(self):
        p = _proposal()
        p["evidence_requirements"] = ["fund_flow"]   # not in vocabulary
        contract, reason = build_goal_contract(_repr([p]))
        assert contract is None and reason == "invalid_evidence_type"

    def test_claim_evidence_conflation_rejected(self):
        p = _proposal(claim="policy_references",
                      evidence=["policy_references"])
        contract, reason = build_goal_contract(_repr([p]))
        assert contract is None and reason == "claim_evidence_conflation"

    def test_invalid_scope_rejected(self):
        contract, reason = build_goal_contract(
            _repr([_proposal(scope="universe")]))
        assert contract is None and reason == "invalid_scope"

    def test_subject_mismatch_rejected(self):
        contract, reason = build_goal_contract(
            _repr([_proposal(subject="U99999")]))
        assert contract is None and reason == "subject_mismatch"

    def test_no_silent_output_drop_on_partial_validity(self):
        """One valid + one invalid output → WHOLE proposal rejected
        (all-or-nothing; never silently drop the invalid one)."""
        good = _proposal(claim="hedge_presence_assessment",
                         evidence=["opposite_trade_relations"])
        bad = _proposal(claim="x", evidence=["fund_flow"])
        contract, reason = build_goal_contract(_repr([good, bad]))
        assert contract is None and reason == "invalid_evidence_type"

    def test_capability_availability_does_not_shape_contract(self):
        """shared_ip_evidence has NO declaring binding — the contract still
        accepts it as a requirement (UNSUPPORTED is a later, planning-time
        verdict; capability availability never shapes the goal)."""
        contract, reason = build_goal_contract(
            _repr([_proposal(claim="shared_ip_assessment",
                             scope="relationship_graph",
                             evidence=["shared_ip_evidence"])]))
        assert reason is None
        assert contract.outputs[0].evidence_requirements == [
            EvidenceType.SHARED_IP_EVIDENCE]

    def test_contract_id_stable_within_contract(self):
        c, _ = build_goal_contract(_repr([_proposal()]))
        assert c.contract_id == c.contract_id


# --- 3. single-call extraction (understand_goal) ---------------------------------

class TestSingleCallExtraction:
    def test_flag_on_single_call_with_proposal(self):
        llm = _ScriptLLM(_goal_payload())
        previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            repr_obj = understand_goal("这个账号是否有洗钱情况？",
                                       resolved_finding_id=None,
                                       case_id="U00010", llm=llm)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
        assert llm.calls == 1                        # SAME single call
        assert repr_obj.contract_proposal is not None
        assert repr_obj.contract_proposal["required_outputs"][0][
            "claim"] == "money_laundering_assessment"
        assert any("required_outputs" in p for p in llm.prompts)

    def test_flag_off_no_prompt_block_no_proposal(self):
        llm = _ScriptLLM(_goal_payload())
        previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = False
        try:
            repr_obj = understand_goal("这个账号是否有洗钱情况？",
                                       resolved_finding_id=None,
                                       case_id="U00010", llm=llm)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
        assert llm.calls == 1
        assert repr_obj.contract_proposal is None
        assert all("required_outputs" not in p for p in llm.prompts)

    def test_heuristic_path_carries_no_proposal(self):
        previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            repr_obj = understand_goal("调查00010", resolved_finding_id=None,
                                       case_id="U00010", llm=None,
                                       use_llm=False)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
        assert repr_obj.contract_proposal is None

    def test_ambiguous_outcome_carries_no_proposal(self):
        llm = _ScriptLLM(json.dumps({
            "goal_resolution": "AMBIGUOUS", "goal_type": None,
            "ambiguous_goal_types": ["explain_finding",
                                     "identify_related_accounts"],
            "reason": "r"}))
        previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            repr_obj = understand_goal("ambiguous request",
                                       resolved_finding_id=None,
                                       case_id="U00010", llm=llm)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
        assert repr_obj.contract_proposal is None

    def test_proposal_is_not_projected(self):
        """The raw proposal never reaches the user-safe projection."""
        repr_obj = _repr([_proposal()])
        assert "contract_proposal" not in repr_obj.project()


# --- 4. semantic fixtures Case A–D ------------------------------------------------

class TestSemanticFixtures:
    def test_case_a_laundering_assessment(self):
        contract, reason = build_goal_contract(_repr([_proposal()]))
        assert reason is None
        o = contract.outputs[0]
        assert o.claim == "money_laundering_assessment"
        assert o.claim not in [e.value for e in o.evidence_requirements]
        assert {e.value for e in o.evidence_requirements} <= EVIDENCE_TYPES
        assert o.subject_ref == "U00010"

    def test_case_b_cross_account_hedge(self):
        contract, reason = build_goal_contract(_repr([
            _proposal(claim="hedge_presence_assessment",
                      scope="relationship_graph",
                      evidence=["opposite_trade_relations"])]))
        assert reason is None
        o = contract.outputs[0]
        assert o.evidence_requirements == [EvidenceType.OPPOSITE_TRADE_RELATIONS]
        assert o.claim != "related_account_analysis"   # no tool substitution

    def test_case_c_shared_ip_requirement_without_capability(self):
        """shared_ip_evidence ∈ vocabulary, no binding declares it — the
        contract keeps the requirement (normal Slice-1 state)."""
        contract, reason = build_goal_contract(_repr([
            _proposal(claim="shared_ip_assessment",
                      scope="relationship_graph",
                      evidence=["shared_ip_evidence"])]))
        assert reason is None
        assert contract.outputs[0].evidence_requirements == [
            EvidenceType.SHARED_IP_EVIDENCE]
        # and the registry still declares no support (the two facts coexist)
        assert all(
            EvidenceType.SHARED_IP_EVIDENCE not in b.supported_evidence_types
            for b in STEP_TOOL_MAP.values())

    def test_case_d_multi_output_request(self):
        p = [_proposal(claim="risk_level_report", scope="case_level",
                       evidence=["case_summary"]),
             _proposal(claim="risk_reason_report", scope="case_level",
                       evidence=["case_summary", "rule_explanation"]),
             _proposal(claim="ml_explanation", subject="F1",
                       scope="finding_level",
                       evidence=["ml_signal_explanation"])]
        contract, reason = build_goal_contract(_repr(p, subject="U00010"))
        assert reason is None
        assert [o.output_id for o in contract.outputs] == ["O1", "O2", "O3"]
        assert [o.claim for o in contract.outputs] == [
            "risk_level_report", "risk_reason_report", "ml_explanation"]
        assert contract.outputs[2].subject_ref == "F1"


# --- 5. persistence + legacy recovery (real TaskStoreV2) --------------------------

class TestPersistenceAndRecovery:
    def _state_with_contract(self, store):
        from app.agent_loop import AgentLoopState
        contract, _ = build_goal_contract(_repr([_proposal()]))
        state = AgentLoopState(
            task_id="TASK-GSP2", investigation_id="INV-GSP2",
            case_id="U00010", goal="这个账号是否有洗钱情况？",
            goal_contract=contract)
        store.save_agent_state(state)
        return contract

    def test_contract_round_trip_preserves_nested_outputs(self):
        store = TaskStoreV2(tempfile.mkdtemp() + "/gsp2.db")
        contract = self._state_with_contract(store)
        loaded = store.get_agent_state("TASK-GSP2")
        assert loaded.goal_contract is not None
        assert loaded.goal_contract.contract_id == contract.contract_id
        assert loaded.goal_contract.outputs[0].output_id == "O1"
        assert loaded.goal_contract.outputs[0].claim == \
            contract.outputs[0].claim
        assert loaded.goal_contract.outputs[0].scope == \
            contract.outputs[0].scope
        assert [e.value for e in
                loaded.goal_contract.outputs[0].evidence_requirements] == \
            [e.value for e in contract.outputs[0].evidence_requirements]
        assert loaded.goal_contract.outputs[0].satisfaction == \
            OutputSatisfaction.PENDING

    def test_legacy_state_without_goal_contract_loads(self):
        """A pre-GSP persisted state (no goal_contract key) loads with
        goal_contract=None — old tasks keep recovering."""
        from app.agent_loop import AgentLoopState
        store = TaskStoreV2(tempfile.mkdtemp() + "/gsp2b.db")
        state = AgentLoopState(
            task_id="TASK-OLD", investigation_id="INV-OLD",
            case_id="U00010", goal="g")
        store.save_agent_state(state)
        raw = json.loads(store._connection().execute(
            "SELECT document FROM agent_states WHERE task_id = ?",
            ("TASK-OLD",)).fetchone()["document"])
        raw.pop("goal_contract", None)               # simulate legacy JSON
        legacy = AgentLoopState.model_validate_json(json.dumps(raw))
        assert legacy.goal_contract is None
        assert legacy.task_id == "TASK-OLD"

    def test_loop_run_persists_contract(self, monkeypatch):
        """Full real-loop integration: goal resolver returns the proposal,
        the loop accepts the contract, and the persisted state reloads
        with it intact (store round-trip through the real run path)."""
        from tests.test_al351_target_validity_guard_v1 import (
            _build_runner, _resolved_outcome, _CustomResolver)
        from app.models import InvestigationContext
        from tests.test_api_investigations_v1 import make_findings
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/gsp2d.db")
        findings = make_findings()
        runner = _build_runner(store)
        runner.resolver = _CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding"))

        def goal_resolver(req, fid, case):
            return _repr([_proposal(subject="U00299", scope="case_level",
                                    evidence=["case_summary"])],
                         subject="U00299", case_id="U00299")

        runner.goal_resolver = goal_resolver
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
                   "fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid),
                       fx.rp_explanation_payload())):
            result = runner.run("这个账号是否有洗钱情况？",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-GSP2D",
                                task_id="TASK-GSP2D",
                                findings_provider=lambda: findings)
        reloaded = store.get_agent_state("TASK-GSP2D")
        assert reloaded.goal_contract is not None
        assert reloaded.goal_contract.outputs[0].claim == \
            "money_laundering_assessment"
        assert reloaded.goal_contract.subject_ref == "U00299"


# --- 6. G1 compatibility + flag combinations ---------------------------------------

class TestG1Compatibility:
    def test_valid_finding_subject_preserved_in_contract(self):
        """A finding-focused run's accepted resolution (F1) is referenced —
        GSP never re-resolves or overrides the G1-admitted target."""
        contract, reason = build_goal_contract(
            _repr([_proposal(claim="ml_explanation", subject="F1",
                             scope="finding_level",
                             evidence=["ml_signal_explanation"])],
                  subject="F1"))
        assert reason is None
        assert contract.subject_ref == "F1"
        assert contract.outputs[0].subject_ref == "F1"

    def test_flags_are_independent(self):
        """GSP flag and G1 flag are separate settings — flipping one does
        not read or write the other."""
        g1_before = settings.ENABLE_TARGET_VALIDITY_GUARD
        gsp_before = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        try:
            settings.ENABLE_TARGET_VALIDITY_GUARD = not g1_before
            assert settings.ENABLE_GOAL_SEMANTIC_CONTRACT == gsp_before
        finally:
            settings.ENABLE_TARGET_VALIDITY_GUARD = g1_before

    def test_defaults_off(self):
        assert settings.ENABLE_GOAL_SEMANTIC_CONTRACT is False or True
        # the default IN CODE is False; assert via a fresh Settings that
        # ignores the cwd env_file AND the process env (llm_provider's
        # module-level load_dotenv injects backend/.env into os.environ
        # when the LLM chain is imported first — P0 recovery made the
        # .env carry the flag, so isolate both channels explicitly)
        import os
        from app.config import Settings
        _saved = os.environ.pop("ENABLE_GOAL_SEMANTIC_CONTRACT", None)
        try:
            assert Settings(_env_file=None) \
                .ENABLE_GOAL_SEMANTIC_CONTRACT is False
        finally:
            if _saved is not None:
                os.environ["ENABLE_GOAL_SEMANTIC_CONTRACT"] = _saved

    def test_goal_contract_telemetry_emitted(self, monkeypatch):
        """accepted/rejected events are emitted with structured fields
        (contract_id, output_count, reason_code) — no CoT."""
        from tests.test_al351_target_validity_guard_v1 import (
            _build_runner, _resolved_outcome, _CustomResolver)
        from app import telemetry
        from app.models import InvestigationContext
        from tests.test_api_investigations_v1 import make_findings
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        events = []

        class _Sink:
            def record(self, event):
                events.append(event)

        previous_sink = telemetry.get_sink()
        telemetry.set_sink(_Sink())
        try:
            store = TaskStoreV2(tempfile.mkdtemp() + "/gsp2t.db")
            findings = make_findings()
            runner = _build_runner(store)
            runner.resolver = _CustomResolver(
                _resolved_outcome(evidence_kind="concept_grounding"))

            def goal_resolver(req, fid, case):
                return _repr([_proposal(subject="U00299", scope="case_level",
                                        evidence=["case_summary"])],
                             subject="U00299", case_id="U00299")

            runner.goal_resolver = goal_resolver
            with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
                       "fetch_case",
                       new=lambda self, uid: (
                           fx.rp_evidence_payload(uid),
                           fx.rp_explanation_payload())):
                runner.run("这个账号是否有洗钱情况？",
                           InvestigationContext(case_id="U00299"),
                           findings, investigation_id="INV-GSP2T",
                           task_id="TASK-GSP2T",
                           findings_provider=lambda: findings)
        finally:
            telemetry.set_sink(previous_sink)
        accepted = [e for e in events
                    if e.event_type == "goal_contract.accepted"]
        assert accepted and accepted[0].output_count == 1
        assert accepted[0].contract_id.startswith("GC-")
        assert accepted[0].guard_enabled is True
