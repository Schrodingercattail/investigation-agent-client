"""U00047 F3 — case-level detection event must not enter finding timelines.

Regression for the audited defect (real incident: ask "Show the
chronological timeline of events for finding F3" on U00047, persisted
TASK-7941917d920b): `normalize_timeline_events` unconditionally
synthesized a "Case detected: rule '…' triggered" event from the
CASE-level `risk_summary.detected_at` (evidence ref
`risk_event:<case_id>`) into EVERY finding timeline.

Contract after the fix: a finding timeline carries ONLY
finding-associated evidence; the case-level detection event stays on
case-level surfaces (raw evidence payload / case summary). No keyword
blacklists; no case-membership ownership inference. Transaction/
withdrawal stream gating and timeline capability derivation are
unchanged.
"""

from unittest.mock import patch

from app.adapters.risk_platform import (
    derive_capabilities,
    normalize_timeline_events,
)
from app.domain_tools import risk_case_fetch
from app.models import Finding, FindingCapability

CASE = "U00047"

# The REAL persisted U00047 F3 timeline input (TASK-7941917d920b).
DETECTED_AT = "2026-09-01T07:22:43.730680+00:00"
RULE = {"rule_name": "New account with high activity",
        "severity": "HIGH",
        "description": "New account with unusually high activity"}

REAL_WITHDRAWALS = [
    {"withdrawal_id": "W0001", "asset": "BTC", "amount": 2.432614,
     "is_new_address": True, "timestamp": "2026-07-21T01:50:49.901218+00:00"},
    {"withdrawal_id": "W0002", "asset": "BTC", "amount": 2.320267,
     "is_new_address": True, "timestamp": "2026-07-21T02:06:49.901218+00:00"},
    {"withdrawal_id": "W0003", "asset": "USDC", "amount": 2.004239,
     "is_new_address": True, "timestamp": "2026-07-21T04:07:49.901218+00:00"},
    {"withdrawal_id": "W0004", "asset": "USDT", "amount": 2.796471,
     "is_new_address": True, "timestamp": "2026-07-21T05:06:49.901218+00:00"},
    {"withdrawal_id": "W0005", "asset": "USDC", "amount": 1.576777,
     "is_new_address": True, "timestamp": "2026-07-21T05:59:49.901218+00:00"},
]


def _f3_finding():
    return Finding(
        finding_id="F3", case_id=CASE, type="rule_derived",
        title="First withdrawal to new address.",
        summary="First withdrawal to a newly encountered address.",
        capabilities=FindingCapability(root=frozenset({"timeline"})))


def _f3_evidence(**overrides):
    payload = {
        "user_id": CASE,
        "risk_summary": {
            "risk_level": "HIGH", "risk_score": 78.0,
            "primary_reason": "New account with high activity",
            "detection_methods": ["Rule"],
            "detected_at": DETECTED_AT,
            "ml_score": None, "rule_score": 78.0, "graph_score": None,
        },
        "transaction_evidence": [
            {"transaction_id": "TX0001", "symbol": "BTC", "side": "buy",
             "quantity": 0.4, "timestamp": "2026-07-20T09:00:00+00:00",
             "value": 17000.0, "risk_reason": "burst pattern"},
        ],
        "withdrawal_evidence": [dict(w) for w in REAL_WITHDRAWALS],
        "rule_evidence": [dict(RULE)],
    }
    payload.update(overrides)
    return payload


def test_f3_timeline_has_no_case_detection_event():
    """The audited real shape: 5 withdrawals stay; the case-level
    detection event (risk_event:<case_id>, 'Case detected: …') is gone."""
    evidence = _f3_evidence()
    events = normalize_timeline_events(
        case_id=CASE, evidence=evidence, finding=_f3_finding())
    assert len(events) == 5
    assert all(e["event_type"] == "withdrawal" for e in events)
    assert all(e["summary"].startswith("Withdrawal of") for e in events)
    # chronological, real timestamps preserved
    stamps = [e["timestamp"] for e in events]
    assert stamps == sorted(stamps)
    assert stamps[0] == "2026-07-21T01:50:49.901218+00:00"
    # no case-level detection event in any shape
    assert not any(e["event_type"] == "detection" for e in events)
    assert not any("Case detected" in e["summary"] for e in events)
    assert not any(r.get("kind") == "risk_event"
                   for e in events for r in e["evidence_refs"])


def test_evidence_payload_left_unmutated():
    """The timeline normalizer must not consume/alter the shared case
    evidence: risk_summary.detected_at stays intact for every other
    consumer (case summary, empty-case detection)."""
    evidence = _f3_evidence()
    normalize_timeline_events(
        case_id=CASE, evidence=evidence, finding=_f3_finding())
    assert evidence["risk_summary"]["detected_at"] == DETECTED_AT
    assert evidence["rule_evidence"] == [dict(RULE)]
    assert len(evidence["withdrawal_evidence"]) == 5


def test_transaction_finding_timeline_unchanged():
    """A transaction-titled finding still gets its transaction events —
    stream gating is untouched by the detection fix."""
    finding = Finding(
        finding_id="F1", case_id=CASE, type="ml_derived",
        title="ML pattern detection in trading behavior",
        summary="ML-detected trading pattern.",
        capabilities=FindingCapability(root=frozenset({"timeline"})))
    events = normalize_timeline_events(
        case_id=CASE, evidence=_f3_evidence(), finding=finding)
    tx_events = [e for e in events if e["event_type"] == "transaction"]
    assert len(tx_events) == 1
    assert tx_events[0]["timestamp"] == "2026-07-20T09:00:00+00:00"
    assert not any(e["event_type"] == "detection" for e in events)


def test_timeline_capability_derivation_ignores_detection():
    """Capability gating never depended on detection events: it counts
    transaction+withdrawal evidence only — unchanged signatures and
    behavior."""
    caps = derive_capabilities(
        finding_name="First withdrawal to new address.",
        transaction_evidence_count=0, withdrawal_evidence_count=5,
        has_rule_trigger=True)
    assert caps.supports("timeline")
    caps_min = derive_capabilities(
        finding_name="First withdrawal to new address.",
        transaction_evidence_count=0, withdrawal_evidence_count=2,
        has_rule_trigger=True)
    assert caps_min.supports("timeline")
    caps_one = derive_capabilities(
        finding_name="First withdrawal to new address.",
        transaction_evidence_count=0, withdrawal_evidence_count=1,
        has_rule_trigger=True)
    assert not caps_one.supports("timeline")


def test_case_summary_path_unaffected():
    """The case fetch path (risk_case_fetch) is untouched: its
    normalized case payload still carries the evidence-derived risk
    fields, and the raw evidence (with detected_at) is the adapter's
    unchanged return."""
    evidence = _f3_evidence()
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (evidence, {"citations": []}),
    ):
        result = risk_case_fetch(CASE)
    assert result.outcome.value == "success"
    ext = (result.data or {}).get("ext") or {}
    assert ext.get("risk_level") == "HIGH"
    assert ext.get("risk_score") == 78.0
    # raw evidence keeps detected_at for every legitimate consumer
    assert evidence["risk_summary"]["detected_at"] == DETECTED_AT
