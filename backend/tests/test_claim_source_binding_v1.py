"""Cross-domain consistency — claim/source binding integration tests.

For F1 (ML score), F5 (shared devices), F6 (linked accounts): the
Finding summary claim and the Agent's expanded-detail surface derive
from the SAME authoritative RP field. These tests bind the two surfaces
over the real adapter/tool path (source-backed fixture), so any drift
between the summary metric and the detail fails loudly. Classification
(see the consistency QA record): structurally guaranteed by the
single-authoritative-field pass-through — these tests pin that
guarantee; they are not the guarantee itself.
"""

import sys

import pytest

sys.path.insert(0, ".")

from app.adapters.risk_platform import RiskPlatformAdapter
from app.domain_tools import related_account_analysis, signal_explain
from tests.test_risk_platform_adapter_v2 import rp_evidence_payload

CASE = "U00010"


@pytest.fixture()
def live_payload():
    """The source-backed RP evidence payload (same fixture lineage the
    server seed uses)."""
    return RiskPlatformAdapter().fetch_case_evidence(
        CASE, expose_complete_records=True)


def test_f1_ml_score_summary_source_bound_to_signal_detail(live_payload):
    """F1: the summary's 99.41/100 claim and the signal-explanation
    detail draw on the SAME ml_score field — bound here so a source
    drift between them cannot ship silently."""
    claimed = (live_payload.get("risk_summary") or {}).get("ml_score")
    assert claimed is not None                      # the summary's source
    r = signal_explain(finding_id="F1", case_id=CASE)
    explanation = (r.data or {}).get("explanation") or {}
    assert explanation.get("ml_score") == claimed
    # the F1 summary wording "99.41/100" is exactly this value
    assert f"{claimed:.2f}/100" == "99.41/100"


def test_f5_shared_device_count_source_bound_to_detail(live_payload):
    """F5: '1 shared device' — the summary's count and the analysis
    detail share one authoritative source field."""
    claimed = (live_payload.get("feature_evidence") or {}).get(
        "shared_device_count")
    assert claimed is not None
    r = related_account_analysis.related_account_analysis(case_id=CASE)
    devices = (r.data or {}).get("shared_devices") or []
    assert len(devices) == claimed
    # identity binding: the device id itself matches the network record
    net = (live_payload.get("network_evidence") or {}).get(
        "shared_devices") or []
    assert devices == net


def test_f6_linked_account_count_source_bound_to_detail(live_payload):
    """F6: '18 connected accounts' — the summary count and the analysis
    endpoint count draw on the same cluster computation; the analysis
    list length equals the claimed count (no silent truncation)."""
    feats = live_payload.get("feature_evidence") or {}
    claimed = feats.get("linked_account_count")
    assert claimed is not None
    r = related_account_analysis.related_account_analysis(case_id=CASE)
    d = r.data or {}
    accounts = d.get("related_accounts") or []
    assert d.get("related_accounts_count") == claimed
    assert len(accounts) == claimed
    # distinct-entity semantics: the detail records are distinct ids
    # (related_accounts entries are plain account-id strings)
    assert len(set(accounts)) == claimed
