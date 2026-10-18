"""WP-GS-E — Implementation Plan §10.1 acceptance-matrix gap scenarios.

Scenarios the GS-A..GS-D suites did not already cover at the API layer
on the CURRENT worktree: S1 (exact 9-distinct fixture), S2 (all
identical), S3 (mixed repeats), S5 (ambiguous/unclassified address
shapes — normalization annotated, comparison over recorded identifiers,
no on-chain claim), S11 (invalid facet member → whole-proposal
rejection → guarded path), and S20 (per-output/overall/closure parity
assertions).

Same production chain and seam discipline as the GS-D suite; flag set
AND restored per test (monkeypatch); adapter-boundary fixtures for the
deterministic identity shapes (marked data-injection boundary, real
tool/block/projection/evaluation/composer downstream).
"""

import json
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import rp_patches
from tests.test_risk_platform_adapter_v2 import rp_evidence_payload

CASE = "U00010"
FACET = "destination_address_identity"
GOAL = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"
PLAN_WITHDRAWALS = ('{"skill_id": "timeline_investigation", "goal": "g", '
                    '"steps": [{"type": "inspect_withdrawals", '
                    '"reason": "r"}]}')
WHOLE_CLAIM = "本次调查目标已完整回答"


def faceted_contract(subject="F3"):
    return json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "withdrawal_destination_comparison",
             "claim_kind": "retrieval", "subject_ref": subject,
             "scope": "finding_level",
             "evidence_requirements": ["withdrawal_records"],
             "postcondition_facets": [FACET]}]})


def invalid_facet_contract(subject="F3"):
    payload = json.loads(faceted_contract(subject))
    payload["required_outputs"][0]["postcondition_facets"] = [
        "chain_level_destination_identity"]
    return json.dumps(payload)


def rows_with(ids_and_addresses):
    return [(f"W{i:05d}", a) for i, a in enumerate(ids_and_addresses, 160)]


NINE_DISTINCT = rows_with([f"0xaddr{i:02x}" for i in range(9)])
NINE_IDENTICAL = rows_with(["0xsame"] * 9)
MIXED = rows_with(["0xa"] * 4 + ["0xb"] * 3 + ["0xc"] * 2)
UNCLASSIFIED = rows_with([
    "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",   # bech32 family
    "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",           # base58 charset
    "0XDEADBEEF",                                    # 0x prefix, UPPER hex
    "not-an-address-shape",                          # exact_unclassified
    "0xdeadbeef",                                    # case-variant DISTINCT
] + ["0xfiller"] * 4)


def _client(tmp_path):
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "gse_matrix.db"))
    p1, p2 = rp_patches()
    with p1, p2:
        from app.domain_tools import risk_case_fetch
        findings = list(risk_case_fetch(CASE).data["findings"])

    class _TurnScript:
        def generate(self, messages=None, max_tokens=0, temperature=0.1):
            return ""

    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(_TurnScript()), executor=None,
            task_store=store),
        store=store,
        case_findings_provider=lambda case_id: findings)
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _withdrawal_fixture(rows):
    payload = rp_evidence_payload(case_id=CASE)
    payload["withdrawal_evidence"] = [
        {"withdrawal_id": wid, "asset": "BTC", "amount": 0.5,
         "address": addr, "is_new_address": False,
         "timestamp": f"2026-08-19T10:{i:02d}:00Z",
         "risk_reason": "fixture"}
        for i, (wid, addr) in enumerate(rows)]
    return payload


def _journey(client, tmp_path, monkeypatch, *, goal_llm_text,
             fixture_payload=None):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    session = {"plans": [PLAN_WITHDRAWALS], "decisions": ["STOP"],
               "goal_llm": [goal_llm_text]}
    session_path = str(tmp_path / "gse_session.json")
    with open(session_path, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False)
    os.environ["AGENT_LOOP_SCRIPTED_SESSION_FILE"] = session_path
    adapter_patch = (
        patch("app.adapters.risk_platform.RiskPlatformAdapter."
              "fetch_case_evidence",
              new=lambda self, uid, **kw: fixture_payload)
        if fixture_payload is not None else None)
    try:
        if adapter_patch is not None:
            adapter_patch.start()
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = client.post(
            f"/api/v2/investigations/{inv_id}/goal",
            json={"goal": GOAL,
                  "context_action": {"type": "focus_finding",
                                     "finding_id": "F3"}})
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]
        deadline = time.time() + 90
        while time.time() < deadline:
            a = client.get(f"/api/v2/tasks/{task_id}/activity").json()
            if a["is_terminal"]:
                break
            time.sleep(0.1)
        assert a["is_terminal"], "loop did not terminate"
        detail = client.get(f"/api/v2/tasks/{task_id}").json()

        def _diag():
            return (f" [status={a.get('agent_status')!r} "
                    f"outcome={a.get('goal_outcome')!r} "
                    f"resp={detail.get('task_response_text', '')[:280]!r}]")

        return a, (detail.get("task_response_text") or ""), _diag
    finally:
        if adapter_patch is not None:
            adapter_patch.stop()
        os.environ.pop("AGENT_LOOP_SCRIPTED_SESSION_FILE", None)


class TestMatrixScenarios:
    def test_s1_nine_distinct_destinations(self, monkeypatch, tmp_path):
        """§10.1-1: 9 withdrawals / 9 distinct recorded destinations →
        SATISFIED positive; contract-relative COMPLETE; conclusion +
        provenance + 目的地 column."""
        client = _client(tmp_path)
        a, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            fixture_payload=_withdrawal_fixture(NINE_DISTINCT))
        out = a["goal_contract"]["outputs"][0]
        assert out["satisfaction"] == "satisfied", diag()
        assert out["polarity"] == "positive"
        assert "9 笔提现共指向 9 个不同目的地" in resp, diag()
        assert "目的地标识分组" in resp and "W00160" in resp, diag()
        assert a["goal_outcome"] == "complete", diag()

    def test_s2_nine_identical_destinations(self, monkeypatch, tmp_path):
        """§10.1-2: all nine share one recorded destination → SATISFIED
        negative polarity; 全部 9 笔…同一目的地."""
        client = _client(tmp_path)
        a, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            fixture_payload=_withdrawal_fixture(NINE_IDENTICAL))
        out = a["goal_contract"]["outputs"][0]
        assert out["satisfaction"] == "satisfied", diag()
        assert out["polarity"] == "negative"
        assert "全部 9 笔提现指向同一目的地" in resp, diag()
        assert "dest-1" in resp and "0xsame" in resp, diag()
        assert a["goal_outcome"] == "complete", diag()

    def test_s3_mixed_repeats_match_partition(self, monkeypatch, tmp_path):
        """§10.1-3: mixed repeats (4+3+2) → conclusion matches the
        actual 3-group partition."""
        client = _client(tmp_path)
        a, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            fixture_payload=_withdrawal_fixture(MIXED))
        out = a["goal_contract"]["outputs"][0]
        assert out["satisfaction"] == "satisfied", diag()
        assert "9 笔提现共指向 3 个不同目的地" in resp, diag()
        for ref in ("dest-1", "dest-2", "dest-3"):
            assert ref in resp, diag()

    def test_s5_unclassified_shapes_still_compared(self, monkeypatch,
                                                   tmp_path):
        """§10.1-5: ambiguous/unclassified address values → the
        comparison runs over RECORDED identifiers (exact, case-
        sensitive: '0XDEADBEEF' ≠ '0xdeadbeef' are DISTINCT groups);
        descriptive shapes never decide equality; no on-chain claim."""
        client = _client(tmp_path)
        a, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            fixture_payload=_withdrawal_fixture(UNCLASSIFIED))
        out = a["goal_contract"]["outputs"][0]
        assert out["satisfaction"] == "satisfied", diag()
        # 9 records: 0XDEADBEEF and 0xdeadbeef are distinct recorded
        # identifiers (case-sensitive), 0xfiller×4 identical → 6 groups
        assert "9 笔提现共指向 6 个不同目的地" in resp, diag()
        assert "0XDEADBEEF" in resp and "0xdeadbeef" in resp, diag()

    def test_s11_invalid_facet_rejected_guarded(self, monkeypatch,
                                                tmp_path):
        """§10.1-11: an unknown facet member → whole-proposal rejection
        (`invalid_postcondition_facet`) → contract-less guarded path:
        disclosure rendered, no contract, no completion claim."""
        client = _client(tmp_path)
        a, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=invalid_facet_contract())
        assert a["goal_contract"] is None, diag()
        decision = a["goal_contract_decision"] or {}
        assert decision.get("outcome") == \
            "invalid_proposal:invalid_postcondition_facet", diag()
        assert "未能建立完整的目标分解" in resp, diag()
        assert WHOLE_CLAIM not in resp
        assert a["goal_outcome"] is None, diag()


class TestS20Parity:
    def test_partial_parity_across_surfaces(self, monkeypatch, tmp_path):
        """§10.1-20 (scenario-4 shape): per-output satisfaction, overall
        outcome, closure wording, Activity decision and served response
        carry the SAME semantic state."""
        client = _client(tmp_path)
        rows = rows_with(["0xa"] * 4 + ["0xb"] * 2 + ["  "] * 3)
        a, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            fixture_payload=_withdrawal_fixture(rows))
        out = a["goal_contract"]["outputs"][0]
        decision = a["goal_contract_decision"] or {}
        # per-output ↔ overall
        assert out["satisfaction"] == "partially_supported", diag()
        assert a["goal_outcome"] == "partial", diag()
        # closure ↔ coverage decision pairing
        assert "已部分回答" in resp and "3/9 笔记录缺少目的地标识" in resp, diag()
        assert "本次调查目标已完整回答" not in resp
        assert decision.get("coverage") != "comparison_intent_uncovered"
        # Activity semantic fields ↔ response
        assert out["detail"] and "3/9" in out["detail"]
