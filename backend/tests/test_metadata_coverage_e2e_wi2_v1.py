"""WP-M2-1 WI-2 — REAL HTTP E2E for Coverage Check (scenarios A–E).

Real application stack: real FastAPI router, real InvestigationAPI /
InvestigationService / ContextResolver / TaskStoreV2, real /turn HTTP
path — with the Risk Platform boundary controlled via the established
provider-injection seam and the planner LLM scripted (repo E2E
convention). The metadata catalog is the REAL TaskStoreV2-backed store.

Fixture universe = make_findings() (F1 ML Pattern Detection / F2
Coordinated Trading Pattern / F3 High Withdrawal Frequency) — labeled
CONTROLLED FIXTURE. The dynamic E2E seed derives concepts for these
fixtures from their own titles; this is fixture metadata, not real-RP
semantic evidence.
"""

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
    compute_content_hash,
)
from app.config import settings
from app.investigation_service import InvestigationService
from app.models import Finding, FindingCapability
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import PLAN_TIMELINE, ScriptedLLM, \
    make_findings, rp_patches

F3_REQUEST = "具体说明 withdrawal frequency 的证据"


def fixture_universe():
    return make_findings()


def f3_request_changed_universe(universe):
    changed = [f.model_copy(deep=True) for f in universe]
    for f in changed:
        if f.finding_id == "F3":
            f.summary = ("12 withdrawals were recorded in 24 hours, "
                         "exceeding the elevated threshold.")
    return changed


def build_real_http(flag_on: bool, monkeypatch):
    """Real app + real /turn; RP boundary controlled, planner scripted,
    real temp store pre-seeded with the fixture-universe metadata
    (F3 verified -> COMPLETE, F1/F2 INCOMPLETE)."""
    monkeypatch.setattr(settings, "ENABLE_CANDIDATE_METADATA_COVERAGE",
                        flag_on)
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi2_e2e.db")
    universe = fixture_universe()
    provider = TaskStoreCatalogProvider(store)
    core_by_id = {"F1": "ML_PATTERN_DETECTION",
                  "F2": "COORDINATED_TRADING_PATTERN",
                  "F3": "HIGH_WITHDRAWAL_FREQUENCY"}
    for concept in set(core_by_id.values()):
        provider.upsert_concept(ConceptRecord(
            concept_id=concept, definition=f"Fixture concept {concept}.",
            created_by="wi2-e2e"))
    for f in universe:
        core = core_by_id[f.finding_id]
        verified = f.finding_id == "F3"
        provider.upsert_candidate(CandidateMetadataRecord(
            candidate_id=f.finding_id,
            candidate_type="finding",
            case_scope="U00299",
            title_snapshot=f.title,
            description_snapshot=f.summary,
            content_hash=compute_content_hash(f.title, f.summary),
            core_concepts=[core],
            distinguishing_concepts=[core],
            potential_confusions=[],
            status="COMPLETE" if verified else "INCOMPLETE",
            metadata_version=1 if verified else 0,
            owner="fixture",
            authored_by="fixture",
            verified_by="fixture-review" if verified else None,
            provenance={"fixture": True}))

    holder = {"findings": [f.model_copy(deep=True) for f in universe]}

    def controlled_provider(case_id):
        return [f.model_copy(deep=True) for f in holder["findings"]]

    service = InvestigationService(
        planner=PlannerV2(ScriptedLLM([PLAN_TIMELINE])),
        executor=None, task_store=store)
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=controlled_provider)
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    return client, store, holder


def run_turn(client, message=F3_REQUEST):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00299"}).json()
    inv_id = inv["investigation"]["investigation_id"]
    with rp_patches()[0], rp_patches()[1]:
        return client.post(f"/api/v2/investigations/{inv_id}/turn",
                           json={"message": message})


def statuses(store):
    return {c.candidate_id: c.status
            for c in TaskStoreCatalogProvider(store).list_candidates()}


def test_scenario_a_f12_auto_registered_via_real_http(monkeypatch):
    client, store, holder = build_real_http(True, monkeypatch)
    new_finding = make_findings()[0].model_copy(deep=True)
    new_finding.finding_id = "F12"
    new_finding.title = "Unusual Login Pattern."
    new_finding.summary = "Login attempts from three countries within " \
                          "one hour."
    holder["findings"].append(new_finding)
    resp = run_turn(client)
    assert resp.status_code == 200
    f12 = TaskStoreCatalogProvider(store).get_candidate("F12")
    assert f12 is not None and f12.status == "INCOMPLETE"
    assert f12.core_concepts == []
    assert f12.provenance["auto_provisioned"] is True


def test_scenario_b_semantic_change_invalidates_via_real_http(monkeypatch):
    client, store, holder = build_real_http(True, monkeypatch)
    holder["findings"] = f3_request_changed_universe(holder["findings"])
    resp = run_turn(client)
    assert resp.status_code == 200
    f3 = TaskStoreCatalogProvider(store).get_candidate("F3")
    assert f3.status == "UNVERIFIED"
    assert f3.metadata_version == 2   # seed v1 + one invalidation
    assert f3.core_concepts == ["HIGH_WITHDRAWAL_FREQUENCY"]  # preserved


def test_scenario_c_candidate_gone_not_deprecated_via_real_http(monkeypatch):
    client, store, holder = build_real_http(True, monkeypatch)
    holder["findings"] = [f for f in holder["findings"]
                          if f.finding_id != "F3"]
    resp = run_turn(client)
    assert resp.status_code == 200
    f3 = TaskStoreCatalogProvider(store).get_candidate("F3")
    assert f3.provenance["candidate_gone"]["detected_at"]
    assert f3.status == "COMPLETE"        # never auto-DEPRECATED


def test_scenario_d_coverage_failure_does_not_block_turn(monkeypatch, caplog):
    client, store, holder = build_real_http(True, monkeypatch)
    with patch("app.api.investigations.reconcile_coverage",
               side_effect=RuntimeError("db down")):
        with caplog.at_level(logging.WARNING,
                             logger="app.api.investigations"):
            resp = run_turn(client)
    assert resp.status_code == 200        # turn completed, not blocked
    assert any("candidate metadata coverage failed" in r.message
               for r in caplog.records)


def test_scenario_e_flag_off_no_metadata_side_effect(monkeypatch):
    client, store, holder = build_real_http(False, monkeypatch)
    new_finding = make_findings()[0].model_copy(deep=True)
    new_finding.finding_id = "F12"
    new_finding.title = "Unusual Login Pattern."
    new_finding.summary = "New finding."
    holder["findings"].append(new_finding)
    before = statuses(store)
    assert "F12" not in before
    resp = run_turn(client)
    assert resp.status_code == 200
    assert TaskStoreCatalogProvider(store).get_candidate("F12") is None
    assert statuses(store) == before
