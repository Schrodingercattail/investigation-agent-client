"""Evaluation fixtures — the single canonical collection of setups, RP
payload stubs, scripted LLMs, and SUT construction for the evaluation set.

Reuse-first: these wrap the same seams the regression suites use
(InvestigationService.run_turn, PlannerV2 scripted LLM, adapter patches,
ToolCallV2 construction) — no duplication of production behavior.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.test_risk_platform_adapter_v2 as fx  # shared RP-shaped payloads

from app.investigation_service import InvestigationService
from app.models import (
    Finding,
    FindingCapability,
    FocusSource,
    InvestigationContext,
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2

FULL_CAPS = ["timeline", "signal_explain", "policy_lookup"]


def make_finding(fid, title, caps=FULL_CAPS, case_id="U00299"):
    return Finding(finding_id=fid, case_id=case_id, type="rule_signal",
                   title=title, summary="s",
                   capabilities=FindingCapability.model_validate(caps))


def default_findings():
    """U00299-shaped findings with controlled capabilities:
    F1 = ML (signal_refs [ML], no withdrawals), F3 = withdrawal-backed."""
    f1 = make_finding("F1", "ML Pattern Detection")
    f1 = f1.model_copy(update={
        "signal_refs": [{"signal_type": "ML", "name": "ml_score"}]})
    f2 = make_finding("F2", "Coordinated Trading Pattern",
                      caps=FULL_CAPS + ["self_opposite_trades"])
    f3 = make_finding("F3", "High Withdrawal Frequency")
    f3 = f3.model_copy(update={
        "signal_refs": [{"signal_type": "Feature",
                         "name": "withdrawal_risk_score"}]})
    return [f1, f2, f3]


def rp_patches(case_id="U00299"):
    """Patch both RP touch points (evidence + explanation) with real-shape
    payloads. The fixture evidence includes structured trigger fields for
    the withdrawal and coordinated-trading rules."""
    evidence = fx.rp_evidence_payload(case_id)
    evidence["rule_evidence"] = [
        {"rule_name": "High withdrawal frequency", "severity": "MEDIUM",
         "description": "10 withdrawals in 24h exceeds the normal pattern",
         "trigger": {"withdrawal_frequency_24h": 10},
         "threshold": "withdrawal_frequency_24h > 5", "contribution": 20},
        {"rule_name": "Coordinated Trading Pattern", "severity": "HIGH",
         "description": "An opposite-trade ratio of 45.24% exceeded the "
                        "40% threshold.",
         "trigger": {"opposite_trade_ratio": 0.4524},
         "threshold": "opposite_trade_ratio > 0.4", "contribution": 35},
    ]
    return (
        patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
              new=lambda self, uid: (evidence, fx.rp_explanation_payload())),
        patch("app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
              new=lambda self, uid, expose_complete_records=False: evidence),
    )


# Related account used by the cross-account analysis fixture (U00123-style
# id, connected to the case account through a device-sharing cluster).
CATA_RELATED_ACCOUNT = "U00123"


def cata_patches(case_id="U00299"):
    """Patch the account-scoped retrieval seams consumed by
    cross_account_trade_analysis (network signals + bounded transaction
    pages). Controlled multi-account evidence: one 1_to_1 candidate
    (same symbol, opposite sides, 15s gap, equal values). Scenarios that
    never plan the analysis tool are unaffected."""
    signals = {
        "connected_account_count": 1,
        "connected_accounts": [{
            "user_id": CATA_RELATED_ACCOUNT,
            "relationship_type": ["shared_device"],
            "device_fingerprints": ["dev-1"],
            "shared_ips": [],
            "risk_level": "HIGH",
            "risk_score": 77.0,
        }],
    }
    transactions = {
        "case_user_id": case_id,
        "requested_accounts": [case_id, CATA_RELATED_ACCOUNT],
        "transactions": [
            {"transaction_id": "CATX-00001", "account_id": case_id,
             "symbol": "BTC", "side": "BUY", "price": 40000.0,
             "quantity": 0.5, "value": 20000.0,
             "timestamp": "2026-08-19T10:00:00+00:00"},
            {"transaction_id": "CATX-00002",
             "account_id": CATA_RELATED_ACCOUNT,
             "symbol": "BTC", "side": "SELL", "price": 40000.0,
             "quantity": 0.5, "value": 20000.0,
             "timestamp": "2026-08-19T10:00:15+00:00"},
        ],
        "total_matched": 2,
        "returned_count": 2,
        "offset": 0,
        "limit": 100,
        "truncated": False,
        "has_more": False,
        "next_offset": None,
    }
    return (
        patch("app.adapters.risk_platform.RiskPlatformAdapter.fetch_network_signals",
              new=lambda self, uid, limit=None: signals),
        patch("app.adapters.risk_platform.RiskPlatformAdapter.fetch_account_transactions",
              new=lambda self, uid, **kw: transactions),
    )


class ScriptedLLM:
    """FIFO scripted planner responses (one JSON plan per call)."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0

    def generate(self, messages, max_tokens=0, temperature=0.1):
        script = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1
        return script


def plan_json(skill_id, steps):
    import json
    return json.dumps({
        "skill_id": skill_id, "goal": "eval",
        "steps": [{"type": t, "reason": "eval"} for t in steps],
    })


def build_service(scripts, findings=None, store=None):
    """InvestigationService with a scripted planner LLM and real tools."""
    findings = findings if findings is not None else default_findings()
    svc = InvestigationService(planner=PlannerV2(ScriptedLLM(scripts)))
    return svc, findings


def build_context(focused_finding_id=None, case_id="U00299"):
    return InvestigationContext(
        case_id=case_id, focused_finding_id=focused_finding_id,
        focus_source=FocusSource.USER_SELECTED
        if focused_finding_id else None)


def run_turns(svc, findings, context, turns):
    """Run a sequence of turns; returns InvestigationTurnResult list and
    the final context (later turns carry the evolved context forward,
    mirroring real conversation state)."""
    results = []
    ctx = context
    for message in turns:
        r = svc.run_turn(message, ctx, findings)
        results.append(r)
        ctx = r.context
    return results, ctx


def tool_result(tool_call):
    return tool_call.result


def make_tool_call(tool_call_id, tool_name, result):
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:EVAL", task_id="T",
        tool_name=tool_name, arguments={}, status=ToolCallStatusV2.SUCCESS,
        result=result, started_at="t", completed_at="t")
