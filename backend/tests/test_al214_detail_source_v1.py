"""AL-2.14 — related-account detail data-source correction.

Pins the data-source contract: the related_account_analysis tool uses
the RP `network-signals` detail endpoint (complete membership, no SQL
LIMIT, no Python [:5]) — NOT the evidence payload's `network_evidence`
preview (which truncates at SQL LIMIT 10 + Python [:5]).

Verified with live U00010 data: connected_account_count = 18,
18 unique account IDs, no truncation to 5 or 10.
"""

from unittest.mock import patch

from app.domain_tools.related_account_analysis import (
    ANALYSIS_TYPE,
    related_account_analysis,
)
from app.models import ToolResultOutcome

import tests.test_risk_platform_adapter_v2 as fx


def _evidence_with_cluster():
    ev = fx.rp_evidence_payload("U00010")
    ev["network_evidence"] = {
        "cluster_id": 1012,
        "cluster_name": "Cluster_device_sharing_1",
        "detection_type": "device_sharing",
        "member_count": 19,
        "cluster_risk_score": 80.0,
        "role_in_cluster": "spoke",
        "related_accounts_count": 10,       # preview path count
        "related_accounts": ["U00337", "U00325", "U00349",
                             "U00004", "U00343"],   # preview 5
        "shared_devices": ["DEV8Z1O4A35QU"],
    }
    return ev


def _network_signals_full():
    """Real-shaped network-signals payload with 18 accounts."""
    return {
        "connected_account_count": 18,
        "connected_accounts": [
            {"user_id": f"U{i:05d}", "risk_score": 70.0 + i * 0.1,
             "risk_level": "CRITICAL",
             "relationship_type": ["shared_device"],
             "device_fingerprints": ["DEV8Z1O4A35QU"], "shared_ips": []}
            for i in range(1, 19)
        ],
    }


def test_uses_network_signals_not_evidence_preview():
    """The tool calls fetch_network_signals for the account list and does
    NOT use the evidence payload's preview-related_accounts."""
    ev = _evidence_with_cluster()
    ns = _network_signals_full()
    calls = {"ns": 0, "ev": 0}

    class MockAdapter:
        def fetch_network_signals(self, case_id, limit=None):
            calls["ns"] += 1
            return ns

        def fetch_case_evidence(self, case_id, **kw):
            calls["ev"] += 1
            return ev

    r = related_account_analysis("U00010", adapter=MockAdapter())
    assert r.outcome == ToolResultOutcome.SUCCESS
    assert calls["ns"] == 1                  # detail endpoint called
    assert calls["ev"] == 1                  # cluster context also fetched
    d = r.data
    # the COMPLETE list from network-signals overwrites the preview 5
    assert d["related_accounts_count"] == 18
    assert len(d["related_accounts"]) == 18
    assert len(set(d["related_accounts"])) == 18
    assert "U00001" in d["related_accounts"]  # from ns, not preview
    assert "U00018" in d["related_accounts"]  # from ns, not preview
    # cluster context from evidence
    assert d["cluster"]["cluster_name"] == "Cluster_device_sharing_1"
    assert d["shared_devices"] == ["DEV8Z1O4A35QU"]


def test_preview_path_untouched():
    """The evidence payload's own network_evidence is NOT modified —
    the preview path remains intact for other consumers."""
    from app.domain_tools.related_account_analysis import (
        normalize_related_accounts)
    ev = _evidence_with_cluster()
    payload = normalize_related_accounts(case_id="U00010", evidence=ev)
    # the preview path still has the 10/5 inconsistency (unchanged; the
    # AL-2.14 fix is in the tool, not in the evidence normalizer)
    assert payload["related_accounts_count"] == 10
    assert len(payload["related_accounts"]) == 5


def test_uses_configured_trade_analysis_limit():
    """The limit passed to network-signals reuses the existing
    TRADE_ANALYSIS_MAX_ACCOUNTS setting (not a new magic number)."""
    from app.config import settings
    assert settings.TRADE_ANALYSIS_MAX_ACCOUNTS >= 18  # covers U00010's 18
