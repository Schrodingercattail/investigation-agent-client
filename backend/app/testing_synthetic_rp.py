"""TEST INFRASTRUCTURE — synthetic Risk-Platform fixture adapter.

NOT a production data path. This module exists solely so that controlled
acceptance runs can exercise the REAL downstream pipeline (tool →
executor → loop → postconditions → composers → API → frontend) with
bounded, fully synthetic candidate data — without touching the Risk
Platform, its seed, or any real case.

Activation is EXPLICIT and process-wide: the serving process must be
started with AGENT_SYNTHETIC_RP_FIXTURE=<path-to-fixture-json>. Without
that environment variable this module is never imported.

Fixture shape (JSON):
{
  "case_id": "U99001",                      # synthetic case id
  "connected_accounts": ["U99011", ...],    # authoritative related set
  "transactions": [                          # raw RP transaction rows
      {"transaction_id": "...", "account_id": "...", "symbol": "BTC",
       "side": "SELL", "price": 100.0, "quantity": 1.0, "value": 100.0,
       "timestamp": "2026-07-20T10:00:00+00:00"}, ...],
  "feature_evidence": {"opposite_trade_ratio": 0.42},
  "cluster": {"cluster_name": "SYNTHETIC_CLUSTER", "member_count": 13,
              "detection_type": "device_sharing", "cluster_risk_score": 80.0},
  "shared_devices": ["DEV-SYNTH-1"],
  "policy_refs": [...],                     # optional passthrough
  "synthetic": true                          # MUST be true (refused otherwise)
}

Every response carries "synthetic": true so downstream payloads and
artifacts made from this data are identifiable as synthetic runs.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

ENV_VAR = "AGENT_SYNTHETIC_RP_FIXTURE"


def load_fixture(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        fixture = json.load(fh)
    if not fixture.get("synthetic"):
        raise ValueError(
            f"synthetic fixture {path!r} must set \"synthetic\": true")
    return fixture


class SyntheticRPAdapter:
    """Duck-types the RiskPlatformAdapter read surfaces the trade-analysis
    and signal-explanation paths use. Serves ONLY fixture rows."""

    def __init__(self, fixture: dict[str, Any]):
        self.fixture = fixture
        self.case_id = fixture.get("case_id") or "U99001"

    def _rows(self, account_ids: list[str] | None) -> list[dict[str, Any]]:
        accounts = set(account_ids or [self.case_id])
        return [r for r in self.fixture.get("transactions", [])
                if r.get("account_id") in accounts]

    def fetch_network_signals(self, user_id: str, limit: int | None = None
                              ) -> dict[str, Any]:
        connected = self.fixture.get("connected_accounts", [])
        if limit is not None:
            connected = connected[:limit]
        return {
            "synthetic": True,
            "connected_account_count": len(
                self.fixture.get("connected_accounts", [])),
            "connected_accounts": [
                {"user_id": a} for a in connected],
        }

    def fetch_account_transactions(self, user_id: str,
                                   account_ids: list[str] | None = None,
                                   start_time: str | None = None,
                                   end_time: str | None = None,
                                   symbols: list[str] | None = None,
                                   limit: int | None = None,
                                   offset: int | None = None) -> dict[str, Any]:
        rows = self._rows(account_ids)
        if start_time:
            rows = [r for r in rows if (r.get("timestamp") or "") >= start_time]
        if end_time:
            rows = [r for r in rows if (r.get("timestamp") or "") <= end_time]
        if symbols:
            rows = [r for r in rows if r.get("symbol") in set(symbols)]
        rows = sorted(rows, key=lambda r: (r.get("timestamp") or "",
                                           r.get("transaction_id") or ""))
        lim = limit if limit is not None else 50
        off = offset or 0
        page = rows[off:off + lim]
        has_more = off + len(page) < len(rows)
        return {
            "synthetic": True,
            "case_user_id": user_id,
            "requested_accounts": list(account_ids or [user_id]),
            "transactions": page,
            "total_matched": len(rows),
            "returned_count": len(page),
            "offset": off, "limit": lim,
            "truncated": has_more, "has_more": has_more,
            "next_offset": off + len(page) if has_more else None,
        }

    def fetch_case_evidence(self, user_id: str,
                            expose_complete_records: bool = False
                            ) -> dict[str, Any]:
        cluster = self.fixture.get("cluster") or {}
        return {
            "synthetic": True,
            "user_id": user_id,
            "risk_summary": {"ml_score": None},
            "feature_evidence": self.fixture.get("feature_evidence") or {},
            "network_evidence": {
                "cluster_id": 0,
                "cluster_name": cluster.get("cluster_name"),
                "detection_type": cluster.get("detection_type"),
                "member_count": cluster.get("member_count"),
                "cluster_risk_score": cluster.get("cluster_risk_score"),
                "role_in_cluster": "core",
                # server-side preview semantics mirrored on purpose: the
                # list-completeness renderers must label it as a sample
                "related_accounts_count": len(
                    self.fixture.get("connected_accounts", [])),
                "related_accounts": (
                    self.fixture.get("connected_accounts", [])[:5]),
                "shared_devices": self.fixture.get("shared_devices", []),
            },
            "transaction_evidence": [],
            "withdrawal_evidence": [],
            "rule_evidence": [],
            "policy_refs": self.fixture.get("policy_refs") or [],
        }

    def fetch_case(self, user_id: str) -> tuple[dict[str, Any],
                                                dict[str, Any]]:
        return (self.fetch_case_evidence(user_id),
                self.get_case_explanation_raw(user_id))

    def get_case_explanation_raw(self, user_id: str) -> dict[str, Any]:
        cluster = self.fixture.get("cluster") or {}
        return {
            "synthetic": True,
            "user_id": user_id,
            "cluster": cluster,
            "related_accounts": (
                self.fixture.get("connected_accounts", [])[:5]),
            "shared_devices": self.fixture.get("shared_devices", []),
            "relationship_paths_available": False,
        }


def install(adapter: Any, fixture_path: str) -> None:
    """Bind the synthetic fetch surfaces onto an adapter instance
    (instance attributes shadow the class methods)."""
    fixture = load_fixture(fixture_path)
    synth = SyntheticRPAdapter(fixture)
    adapter.fetch_network_signals = synth.fetch_network_signals
    adapter.fetch_account_transactions = synth.fetch_account_transactions
    adapter.fetch_case_evidence = synth.fetch_case_evidence
    adapter.fetch_case_explanation_raw = synth.get_case_explanation_raw
    adapter.fetch_case = synth.fetch_case
    logger.warning(
        "SYNTHETIC RP FIXTURE ACTIVE (%s) — this process serves synthetic "
        "test data, NOT Risk Platform evidence", fixture_path)
