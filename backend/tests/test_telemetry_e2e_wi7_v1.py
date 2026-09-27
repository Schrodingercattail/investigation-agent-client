"""WP-M2-1 WI-7 — REAL HTTP E2E: telemetry reconstruction.

Full investigation sequences through the real HTTP path with an
in-memory telemetry sink attached. After the turns, the sink events
ALONE must reconstruct:

- Rejection flow: support evidence -> admissibility REJECT
  (metadata_incomplete) -> clarification opened -> (no unsafe SELECT)
- Admission flow: support evidence -> admissibility ADMIT -> focus
- Designation flow: clarification resolved by designation -> focus
- Failure isolation: a raising sink leaves decisions/state unchanged
"""

import json
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.clarification_state import ClarificationLedger
from app.config import settings
from app.telemetry import (
    InMemoryTelemetrySink,
    NoopTelemetrySink,
    set_sink,
)

from tests.test_safe_select_clarification_e2e_wi6_v1 import (
    AMBIGUOUS_REQUEST,
    build_client,
    open_inv,
    turn,
)


@pytest.fixture()
def rejection_flow(monkeypatch):
    client, store, catalog, ledger = build_client(
        monkeypatch, f3_status="INCOMPLETE")
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    inv_id = open_inv(client)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    events = [e.model_dump() for e in sink.events]
    set_sink(NoopTelemetrySink())
    return inv_id, body, events


def no_op(*a, **k):
    return None


@pytest.fixture()
def admission_flow(monkeypatch):
    client, store, catalog, ledger = build_client(
        monkeypatch, f3_status="COMPLETE")
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    inv_id = open_inv(client)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    events = [e.model_dump() for e in sink.events]
    set_sink(NoopTelemetrySink())
    return inv_id, body, events


@pytest.fixture()
def designation_flow(monkeypatch):
    client, store, catalog, ledger = build_client(
        monkeypatch, f3_status="INCOMPLETE")
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    inv_id = open_inv(client)
    turn(client, inv_id, AMBIGUOUS_REQUEST)
    body = turn(client, inv_id, "就是 F3。",
                context_action={"type": "focus_finding",
                                "finding_id": "F3"})
    events = [e.model_dump() for e in sink.events]
    set_sink(NoopTelemetrySink())
    return inv_id, body, events


@pytest.fixture()
def terminal_flow(monkeypatch):
    client, store, catalog, ledger = build_client(
        monkeypatch, f3_status="INCOMPLETE")
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    inv_id = open_inv(client)
    turn(client, inv_id, AMBIGUOUS_REQUEST)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    events = [e.model_dump() for e in sink.events]
    set_sink(NoopTelemetrySink())
    return inv_id, body, events


def _kinds(events):
    return [e["event_type"] for e in events]


def test_reconstruction_rejection_flow(rejection_flow):
    inv_id, body, events = rejection_flow
    # turn outcome: bounded clarification, no autonomous SELECT
    assert body["status"] == "failed"
    assert body["task"]["error"] == "clarification_needed"
    assert body["context"]["focused_finding_id"] is None
    # reconstruct from telemetry alone:
    decided = [e for e in events if e["event_type"] ==
               "admissibility.decided"]
    assert decided, "admissibility decision must be observable"
    assert decided[0]["decision"] == "REJECT"
    assert decided[0]["reason_class"] == "metadata_incomplete"
    support = [e for e in events if e["event_type"] == "support.evaluated"]
    # eligibility short-circuits before support evaluation on this path
    assert support == []
    opened = [e for e in events if e["event_type"] ==
              "clarification.opened"]
    assert opened, "clarification boundary must be observable"
    assert opened[0]["request_fingerprint"]
    # no ADMIT anywhere in the rejection flow
    assert not any(e.get("decision") == "ADMIT" for e in events
                   if e["event_type"] == "admissibility.decided")


def test_reconstruction_designation_flow(designation_flow):
    inv_id, body, events = designation_flow
    # explicit designation resolves focus; metadata untouched
    assert body["context"]["focused_finding_id"] == "F3"
    assert body["context"]["focus_source"] == "user_selected"
    kinds = _kinds(events)
    assert "clarification.opened" in kinds
    assert "clarification.resolved" in kinds
    assert "user.designated_candidate" in kinds
    designated = next(e for e in events
                      if e["event_type"] == "user.designated_candidate")
    assert designated["candidate_id"] == "F3"
    assert designated["reason_class"] == "explicit_focus"
    raw = json.dumps(designated)
    assert "verified_by" not in raw               # no verification grant
    resolved = next(e for e in events
                    if e["event_type"] == "clarification.resolved")
    assert resolved["reason_class"] == "resolved_by_designation"


def test_reconstruction_terminal_flow(terminal_flow):
    inv_id, body, events = terminal_flow
    # bounded terminal on the repeated identical condition
    assert body["status"] == "completed"
    tr = body["target_resolution"]
    assert tr["kind"] == "non_selectable_terminal"
    terminal = [e for e in events
                if e["event_type"] == "clarification.terminal"]
    assert terminal, "terminal transition must be observable"
    opened = [e for e in events if e["event_type"] ==
              "clarification.opened"]
    assert len(opened) == 1                        # no third clarification


def test_reconstruction_admission_flow(admission_flow):
    inv_id, body, events = admission_flow
    assert body["status"] == "completed"
    assert body["context"]["focused_finding_id"] == "F2"
    decided = [e for e in events if e["event_type"] ==
               "admissibility.decided"]
    assert decided and decided[0]["decision"] == "ADMIT"
    assert decided[0]["reason_class"] == "admitted"
    support = [e for e in events if e["event_type"] == "support.evaluated"]
    assert support, "support evaluation must be observable on admission"
    assert json.loads(support[0]["summary"])["unique"] is True
    assert json.loads(support[0]["summary"])["unique_target"] == "F2"


def test_reconstruction_no_cot_or_prompts_in_events(rejection_flow):
    flattened = json.dumps([e for e in rejection_flow[2]],
                           ensure_ascii=False)
    for prohibited in ("You refine", "You normalize", "You resolve",
                       "chain_of_thought", "USER_REQUEST_START"):
        assert prohibited not in flattened, prohibited


# --- real-HTTP E2E: failure injection + identity coverage -------------------------

def test_e2e_9_telemetry_failure_injection(monkeypatch):
    """A raising telemetry sink leaves decisions, state, and the turn
    outcome unchanged (failure isolation at runtime)."""
    client, store, catalog, ledger = build_client(monkeypatch)

    class RaisingSink:
        def record(self, event):
            raise RuntimeError("sink down")

    set_sink(RaisingSink())
    try:
        inv_id = open_inv(client)
        body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    finally:
        set_sink(NoopTelemetrySink())
    assert body["status"] == "failed"
    assert body["task"]["error"] == "clarification_needed"
    assert body["context"]["focused_finding_id"] is None


def test_e2e_10_identity_coverage_summary_matches_catalog(monkeypatch):
    client, store, catalog, ledger = build_client(monkeypatch)
    identity = client.get("/api/v2/runtime/identity").json()
    coverage = identity["metadata_coverage"]
    records = catalog.list_candidates()
    assert coverage["total"] == len(records) == 3
    assert coverage["complete"] == 2               # F1, F2
    assert coverage["incomplete"] == 1             # F3 INCOMPLETE
    assert coverage["unverified"] == 0
    assert coverage["deprecated"] == 0
    expected_ratio = round(2 / 3, 3)
    assert coverage["coverage_ratio"] == expected_ratio
