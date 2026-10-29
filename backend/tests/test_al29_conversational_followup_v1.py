"""AL-2.9 — conversational follow-up finding resolution.

The boundary AL-2.8's browser E2E never crossed: the SECOND turn of a
real conversation, where the resolution input is

    previous conversation state (case established) + current utterance

not a standalone request. Every scenario here starts from a real
case-level turn ("展开一下00010的具体情况" / "调查00010") and then issues
the follow-up against that conversation state.

Layer map per test (runtime audit, QA doc §3):
  investigation state → case_id → candidates → SemanticNormalizer
  (scripted for determinism; real-LLM validation lives in the browser
  journey) → governed grounding → intent(s) → governed plan → real
  tools (RP boundary controlled) → NL response.

Boundaries kept:
  - Resolution ≠ authorization (F7 grounds while UNVERIFIED).
  - 高频交易 → F7 High Trading Frequency, NEVER F3 High Withdrawal
    Frequency (disjoint core concepts; control test).
  - Negative requests stay bounded with zero tools.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.candidate_metadata import TaskStoreCatalogProvider
from app.context_resolution import ContextResolver
from app.investigation_service import InvestigationService
from app.metadata_seeding import DEFAULT_SEED_FILE, seed_from_document
from app.models import Finding, FindingCapability
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al28_semantic_foundation_v1 import (
    REPO_SEED,
    _extended_rp,
    _norm_script,
    seeded_catalog,
    _findings as _al28_findings,
)
from tests.test_api_investigations_v1 import ScriptedLLM, rp_patches

_CAPS = FindingCapability.model_validate(["timeline", "signal_explain"])

TITLES = {
    "F3": "High withdrawal frequency.",
    "F4": "First withdrawal to new address.",
    "F5": "Shared Device Relationships.",
    "F7": "High Trading Frequency.",
}


def _findings(ids=("F3", "F4", "F5", "F7")):
    # signal_refs mirror the REAL RP shapes: F3/F7 carry named Feature
    # refs (the Feature explain fallback keys on them); the others are
    # detectorless in this fixture.
    refs = {
        "F3": [{"signal_type": "Feature", "name": "withdrawal_risk_score"}],
        "F7": [{"signal_type": "Feature", "name": "trade_frequency_24h"}],
    }
    return [
        Finding(finding_id=fid, case_id="U00010", type="rule_signal",
                title=TITLES[fid], severity="high",
                summary=f"summary of {TITLES[fid]}",
                evidence_refs=[], signal_refs=refs.get(fid, []),
                policy_refs=[],
                capabilities=_CAPS.model_copy(deep=True))
        for fid in ids
    ]


def _build_client(normalizer_scripts):
    store = TaskStoreV2(tempfile.mkdtemp() + "/al29_api.db")
    catalog, _ = seeded_catalog(store)
    resolver = ContextResolver(llm_provider=ScriptedLLM(normalizer_scripts),
                               llm_assist=False,
                               semantic_catalog=catalog)
    service = InvestigationService(
        resolver=resolver,
        planner=PlannerV2(ScriptedLLM(["{}"])),   # deterministic branches
        executor=None, task_store=store)
    findings = _findings()
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda cid: [
                               f.model_copy(deep=True) for f in findings])
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), findings


def _two_turn(client, first, second, findings):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00010"}).json()
    iid = inv["investigation"]["investigation_id"]
    with rp_patches()[0], rp_patches()[1], \
            _extended_rp(findings):
        r1 = client.post(f"/api/v2/investigations/{iid}/turn",
                         json={"message": first}).json()
        r2 = client.post(f"/api/v2/investigations/{iid}/turn",
                         json={"message": second}).json()
    return r1, r2


# --- conversation context contract ------------------------------------------------

def test_scenario_a_two_turn_shared_device_followup():
    """Turn 1 establishes case-level context; turn 2's finding reference
    resolves through the M2 semantic entry (AL-2.11: no lexicon) → F5
    explain plan → tools."""
    client, findings = _build_client([
        _norm_script("SHARED_DEVICE_RELATIONSHIP", "共享设备")])
    r1, r2 = _two_turn(client, "展开一下00010的具体情况",
                       "为什么说共享设备是风险项", findings)
    assert r1["status"] == "completed"
    assert r1["context"]["case_id"] == "U00010"
    assert r1["context"]["focused_finding_id"] is None   # case-level
    assert r2["status"] == "completed"
    assert r2["context"]["focused_finding_id"] == "F5"
    plan = r2["plan"]
    assert [s["type"] for s in plan["steps"]] == \
        ["explain_signal", "retrieve_policy"]
    calls = r2["execution"]["tool_calls"]
    assert [c["tool_name"] for c in calls] == ["signal_explain",
                                               "policy_lookup"]


def test_scenario_b_two_turn_trading_followup_via_m2():
    """「高频交易」 grounds through the M2 foundation to F7 High Trading
    Frequency, and BOTH intents compose into one governed plan:
    explain + the transactions evidence stream."""
    client, findings = _build_client([
        _norm_script("HIGH_TRADING_FREQUENCY", "高频交易")])
    r1, r2 = _two_turn(
        client, "展开一下00010的具体情况",
        "为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
        findings)
    assert r1["status"] == "completed"
    assert r2["status"] == "completed"
    sem = r2["target_resolution"]["semantic_grounding"]
    assert sem["concept_id"] == "HIGH_TRADING_FREQUENCY"
    assert sem["grounded_candidate_ids"] == ["F7"]
    assert r2["context"]["focused_finding_id"] == "F7"
    # dual intent, one contract-checked plan; stream locked to transactions
    plan = r2["plan"]
    assert [s["type"] for s in plan["steps"]] == \
        ["explain_signal", "inspect_transactions"]
    assert plan["steps"][1]["arguments"] == {"view": "evidence",
                                             "stream": "transactions"}
    tools = r2["execution"]["tool_calls"]
    assert [t["tool_name"] for t in tools] == ["signal_explain",
                                               "finding_drilldown"]
    assert all(t["outcome"] == "success" for t in tools)
    # the answer names the feature fact (fixture trade_frequency_24h=28)
    # — in the request's language (zh request → zh feature face)
    assert ("behavioral risk-feature detection" in r2["response"]
            or "行为特征观察记录" in r2["response"])
    assert "Trade frequency (24h) = 28" in r2["response"]


def test_comparison_different_case_intake_then_followup():
    """Control (§12): a DIFFERENT case-establishing phrasing
    ("调查00010") followed by the same follow-up must behave the same —
    resolution rides the conversation context, not one magic phrase."""
    client, findings = _build_client([
        _norm_script("HIGH_TRADING_FREQUENCY", "高频交易")])
    r1, r2 = _two_turn(
        client, "调查00010",
        "为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
        findings)
    assert r1["status"] == "completed"
    assert r2["status"] == "completed"
    assert r2["context"]["focused_finding_id"] == "F7"
    assert [s["type"] for s in r2["plan"]["steps"]] == \
        ["explain_signal", "inspect_transactions"]


# --- 高频交易 vs 高频提现 disambiguation -------------------------------------------

def test_withdrawal_wording_goes_to_f3_never_f7():
    """Control: the parallel withdrawal wording must resolve F3 with the
    withdrawals stream — the twoHIGH-frequency findings never cross."""
    client, findings = _build_client([
        _norm_script("HIGH_WITHDRAWAL_FREQUENCY", "高频提现")])
    r1, r2 = _two_turn(
        client, "展开一下00010的具体情况",
        "为什么说高频提现是风险项，给我输出高频提现的具体证据",
        findings)
    assert r2["status"] == "completed"
    assert r2["context"]["focused_finding_id"] == "F3"
    # 高频提现 is a lexicon exact entry: the deterministic tier resolves
    # it before the semantic stage (documented precedence) — used_llm False
    assert r2["target_resolution"]["kind"] == "resolved"
    plan = r2["plan"]
    assert plan["steps"][1]["arguments"] == {"view": "evidence",
                                             "stream": "withdrawals"}


def test_f7_and_f3_core_concepts_are_disjoint():
    """The structural ground for the disambiguation: no shared core
    concept between the two HIGH-frequency findings."""
    catalog, _ = seeded_catalog()
    f3 = catalog.get_candidate("F3")
    f7 = catalog.get_candidate("F7")
    assert f3.core_concepts == ["HIGH_WITHDRAWAL_FREQUENCY"]
    assert f7.core_concepts == ["HIGH_TRADING_FREQUENCY"]
    assert not set(f3.core_concepts) & set(f7.core_concepts)
    assert f7.status == "UNVERIFIED"          # reachable, not eligible


# --- negative safety ----------------------------------------------------------------

def test_followup_unknown_finding_stays_bounded_zero_tools():
    client, findings = _build_client([_norm_script(None, "")])
    r1, r2 = _two_turn(client, "展开一下00010的具体情况",
                       "谈谈虚拟币洗钱这个风险项", findings)
    assert r1["status"] == "completed"
    assert r2["target_resolution"]["kind"] == "unresolved"
    assert r2["plan"] is None and r2["execution"] is None
    assert r2["task"]["error"] == "unresolved_reference"


# --- intent vocabulary + planner composition units ----------------------------------

def test_evidence_intent_recognizes_mingxi():
    from app import investigation_concepts as ic
    assert ic.is_evidence_request("输出高频交易的具体交易明细")
    assert ic.is_evidence_request("给我输出一下高频提现的具体证据")
    assert not ic.is_evidence_request("共享设备为什么是风险项")
    # vague English "details" deliberately NOT an evidence request
    assert not ic.is_evidence_request("tell me more details about this")


def test_planner_dual_intent_composition():
    from app.models import InvestigationContext
    from app.skills import eligible_skills_for_finding
    caps = _CAPS
    eligible = [s.skill_id for s in eligible_skills_for_finding(caps)]
    ctx = InvestigationContext(case_id="U00010",
                               focused_finding_id="F7")
    plan = PlannerV2().plan(
        "为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
        ctx, eligible, finding_capabilities=caps)
    assert [s.type for s in plan.steps] == \
        ["explain_signal", "inspect_transactions"]
    # explain-only keeps the policy continuation
    plan = PlannerV2().plan("共享设备为什么是风险项", ctx, eligible,
                            finding_capabilities=caps)
    assert [s.type for s in plan.steps] == \
        ["explain_signal", "retrieve_policy"]
    # evidence-only unchanged
    plan = PlannerV2().plan("给我输出一下高频提现的具体证据", ctx, eligible,
                            finding_capabilities=caps)
    assert [s.type for s in plan.steps] == ["inspect_withdrawals"]


# --- signal_explain Feature fallback --------------------------------------------------

def test_signal_explain_feature_fallback_reports_rp_features():
    """A finding with no RP rule but named Feature signal_refs gets its
    real feature values as the honest 'why' (signal_type=Feature)."""
    from app.domain_tools.signal_explain import signal_explain
    from app.models import ToolResultOutcome
    import tests.test_risk_platform_adapter_v2 as fx
    from unittest.mock import patch
    findings = _findings(("F7",))
    evidence = fx.rp_evidence_payload()          # feature_evidence present
    case_context = {"case_id": "U00010",
                    "findings": [f.model_copy(deep=True)
                                 for f in findings],
                    "_raw_evidence": evidence}
    with patch("app.adapters.risk_platform"
               ".RiskPlatformAdapter.fetch_case_evidence",
               new=lambda self, uid, expose_complete_records=False:
               evidence):
        r = signal_explain(finding_id="F7", case_context=case_context)
    assert r.outcome == ToolResultOutcome.SUCCESS
    assert r.data["signal_type"] == "Feature"
    feats = r.data["explanation"]["features"]
    assert {"name": "trade_frequency_24h", "value": 28} in feats
    assert r.data["evidence_missing"] is False


def test_signal_explain_rule_finding_keeps_rule_path():
    """A finding the RP rules DO back keeps the richer rule explanation
    (the Feature fallback only fires when no rule exists)."""
    from app.domain_tools.signal_explain import signal_explain
    from app.models import ToolResultOutcome
    import tests.test_risk_platform_adapter_v2 as fx
    evidence = fx.rp_evidence_payload()
    evidence["rule_evidence"] = evidence["rule_evidence"] + [{
        "rule_name": "High Withdrawal Frequency",
        "severity": "MEDIUM",
        "description": "14 withdrawals in 24h period",
        "trigger": {"withdrawal_frequency_24h": 14},
        "threshold": "withdrawal_frequency_24h > 10",
        "contribution": 20,
    }]
    f3 = _findings(("F3",))[0]
    assert f3.signal_refs              # Feature refs present but rule wins
    case_context = {"case_id": "U00010",
                    "findings": [f3], "_raw_evidence": evidence}
    with patch("app.adapters.risk_platform"
               ".RiskPlatformAdapter.fetch_case_evidence",
               new=lambda self, uid, expose_complete_records=False:
               evidence):
        r = signal_explain(finding_id="F3", case_context=case_context)
    assert r.outcome == ToolResultOutcome.SUCCESS
    assert r.data["signal_type"] == "Rule"
    assert r.data["rule"]["name"] == "High Withdrawal Frequency"
