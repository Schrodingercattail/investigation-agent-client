"""WP-GS-D — destination-identity facet evaluation, real HTTP API E2E.

In-process TestClient against the real FastAPI app: real goal
understanding (model text seam), real contract acceptance, real
planner parsing, real ExecutorV2 → finding_drilldown → Risk Platform
adapter → GS-B fact block → real observation projection, real GSP-3
association, real GSP-4 facet evaluation, real GSP-5 derivation, real
composer and Activity projection. ONLY the model boundary (goal LLM
text / plans / decisions) is scripted via the approved session seam;
where the Risk Platform payload itself must be fixture-controlled
(partial-identity shape), the ADAPTER boundary is patched — the tool,
block computation, projection, evaluation, derivation, composer and
Activity stay the production path.

Flag hygiene (GS-D harness rule): every test sets
ENABLE_GOAL_SEMANTIC_CONTRACT through monkeypatch — set AND restored
per test; no test relies on another test's global state.
"""

import json
import os
import sys
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
PLAN_EVIDENCE = ('{"skill_id": "timeline_investigation", "goal": "g", '
                 '"steps": [{"type": "inspect_evidence", "reason": "r"}]}')

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

PLAIN_CONTRACT = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "withdrawal_records_list", "claim_kind": "retrieval",
         "subject_ref": "F3", "scope": "finding_level",
         "evidence_requirements": ["withdrawal_records"]}]})

COMPARISON_DONE = "个不同目的地"
ALL_SAME = "笔提现指向同一目的地"
PARTIAL_MARK = "笔记录缺少目的地标识"
ABSENT_MARK = "未获得完成目的地比较所需的地址事实"
WHOLE_CLAIM = "本次调查目标已完整回答"
# retrieval-shaped goal text (detector near-negative) for the
# plain-contract control — the comparison question would correctly
# trigger the GS-C coverage disclosure on a facet-less contract
RETRIEVAL_GOAL = "列出这个账号的全部提现记录。"


def _client(tmp_path):
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "cgsd_api.db"))
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
    """A Risk Platform evidence payload whose withdrawal stream is the
    given (withdrawal_id, address) rows — everything else mirrors the
    standard fixture. The GS-B block is computed by the REAL tool from
    these records."""
    payload = rp_evidence_payload(case_id=CASE)
    payload["withdrawal_evidence"] = [
        {"withdrawal_id": wid, "asset": "BTC", "amount": 0.5,
         "address": addr, "is_new_address": False,
         "timestamp": f"2026-08-19T10:{i:02d}:00Z",
         "risk_reason": "fixture"}
        for i, (wid, addr) in enumerate(rows)]
    return payload


PARTIAL_ROWS = (
    # 9 withdrawals: 6 with addresses (two groups: 4 + 2), 3 without
    [(f"W00016{i}", f"0xshared{i:02x}") for i in range(4)]
    + [(f"W00017{i}", "0xother") for i in range(2)]
    + [(f"W00018{i}", "  ") for i in range(3)]     # blank → no identity
)


def _journey(client, tmp_path, monkeypatch, *, goal_llm_text, plans,
             decisions, goal=GOAL, focus="F3", fixture_payload=None):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    session = {"plans": list(plans), "decisions": list(decisions),
               "goal_llm": [goal_llm_text]}
    session_path = str(tmp_path / "cgsd_session.json")
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
            json={"goal": goal,
                  "context_action": {"type": "focus_finding",
                                     "finding_id": focus}})
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
                    f"contract={(json.dumps(a.get('goal_contract'), ensure_ascii=False) or '')[:400]} "
                    f"resp={detail.get('task_response_text', '')[:300]!r}]")

        return a, (detail.get("task_response_text") or ""), _diag
    finally:
        if adapter_patch is not None:
            adapter_patch.stop()
        os.environ.pop("AGENT_LOOP_SCRIPTED_SESSION_FILE", None)


class TestFullFacts:
    def test_live_full_comparison_satisfied_positive(self, monkeypatch,
                                                     tmp_path):
        """Live Risk Platform (no fixture): focused F3 carries seven
        withdrawals with seven distinct recorded destinations → the
        facet output is SATISFIED (positive), GoalOutcome complete,
        and the response renders the comparison conclusion + group
        provenance + the 目的地 evidence table — never 'records
        retrieved' as the answer, and no coverage-incomplete mark (the
        facet covers the detected comparison intent)."""
        client = _client(tmp_path)
        # the live population is 7 — the premise-consistent question
        # asserts 7 (a 9-premised question over this case now yields the
        # population-shortfall blocked shape, covered in the consistency
        # suite)
        activity, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            goal=("7笔提现都提向同一个地址，"
                  "还是每笔提向一个新地址？"))
        contract = activity["goal_contract"]
        assert contract is not None, diag()
        (out,) = contract["outputs"]
        assert out["satisfaction"] == "satisfied", diag()
        assert out["polarity"] == "positive"
        assert out["postcondition_facets"] == [FACET]
        assert "个不同目的地" in out["detail"], diag()
        assert activity["goal_outcome"] == "complete", diag()
        assert COMPARISON_DONE in resp, diag()
        assert "目的地标识分组" in resp, diag()
        assert "| 记录 ID | 时间 | 目的地 |" in resp, diag()
        assert "未能建立完整的目标分解" not in resp
        decision = activity["goal_contract_decision"]
        assert decision.get("coverage") != "comparison_intent_uncovered"


class TestPartialIdentity:
    def test_partial_identities_partially_supported(self, monkeypatch,
                                                     tmp_path):
        """Fixture-controlled identities (design §14 fixture basis):
        9 withdrawals, 3 without a recorded destination →
        PARTIALLY_SUPPORTED, the 3/9 M/N boundary in the section, the
        comparable-subset groups rendered, overall PARTIAL, and no
        whole-request completion claim."""
        client = _client(tmp_path)
        activity, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            fixture_payload=_withdrawal_fixture(PARTIAL_ROWS))
        contract = activity["goal_contract"]
        (out,) = contract["outputs"]
        assert out["satisfaction"] == "partially_supported", diag()
        assert out["polarity"] is None
        assert "3/9 笔记录缺少目的地标识" in (out["detail"] or ""), diag()
        assert activity["goal_outcome"] == "partial", diag()
        assert PARTIAL_MARK in resp, diag()
        assert "整体的比较尚未建立" in resp, diag()
        assert "已部分回答" in resp, diag()
        assert WHOLE_CLAIM not in resp
        # comparable-subset groups are traceable to their records
        assert "dest-1" in resp and "W000160" in resp, diag()


class TestInsufficientFacts:
    def test_all_identities_missing_partial_never_satisfied(
            self, monkeypatch, tmp_path):
        """Tool succeeds, nine withdrawals retrieved — but NONE carries
        a recorded destination: comparison facts are insufficient. The
        facet maps to PARTIALLY_SUPPORTED with the 9/9 boundary; the
        comparable subset is empty and no same/different conclusion may
        appear; overall PARTIAL with no whole-request claim."""
        client = _client(tmp_path)
        rows = [(f"W00018{i}", "   ") for i in range(9)]
        activity, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            fixture_payload=_withdrawal_fixture(rows))
        contract = activity["goal_contract"]
        (out,) = contract["outputs"]
        assert out["satisfaction"] == "partially_supported", diag()
        assert "9/9 笔记录缺少目的地标识" in (out["detail"] or ""), diag()
        assert activity["goal_outcome"] == "partial", diag()
        assert PARTIAL_MARK in resp, diag()
        assert "整体的比较尚未建立" in resp, diag()
        assert WHOLE_CLAIM not in resp

    def test_no_withdrawal_records_is_determinate_negative(
            self, monkeypatch, tmp_path):
        """Row 4: the withdrawal stream authoritatively holds zero
        records (complete scope) → the inherited empty-complete
        negative IS the answer to the comparison request; the facet
        renderer says so explicitly and the contract-relative
        completion claim is legitimate FOR THAT CLAIM."""
        client = _client(tmp_path)
        activity, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            fixture_payload=_withdrawal_fixture([]))
        contract = activity["goal_contract"]
        (out,) = contract["outputs"]
        assert out["satisfaction"] == "satisfied", diag()
        assert out["polarity"] == "negative"
        assert activity["goal_outcome"] == "complete", diag()
        assert "不存在可比较的目的地标识" in resp, diag()

    def test_replan_identical_repeat_never_upgrades(self, monkeypatch,
                                                    tmp_path):
        """§14-16: the re-plan repeats the same withdrawal retrieval;
        the identical repeat carries no new facts — the no-progress
        convergence stops the loop, the facet stays
        PARTIALLY_SUPPORTED (9/9 identities missing), and the loop
        completes with an honest non-complete outcome — never a
        whole-request completion claim."""
        client = _client(tmp_path)
        rows = [(f"W00018{i}", "   ") for i in range(9)]
        activity, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=faceted_contract(),
            plans=[PLAN_WITHDRAWALS] * 3,
            decisions=["CONTINUE"] * 3,
            fixture_payload=_withdrawal_fixture(rows))
        contract = activity["goal_contract"]
        (out,) = contract["outputs"]
        assert out["satisfaction"] == "partially_supported", diag()
        assert activity["agent_status"] == "completed", diag()
        assert activity["goal_outcome"] == "partial", diag()
        assert PARTIAL_MARK in resp, diag()
        assert WHOLE_CLAIM not in resp


class TestRetrievalOnlyIntact:
    def test_plain_withdrawal_output_keeps_retrieval_semantics(
            self, monkeypatch, tmp_path):
        """A contract without the facet keeps the existing
        satisfied-by-retrieval semantics and rendering — the facet
        branch must not touch non-faceted outputs."""
        client = _client(tmp_path)
        activity, resp, diag = _journey(
            client, tmp_path, monkeypatch,
            goal_llm_text=PLAIN_CONTRACT,
            plans=[PLAN_WITHDRAWALS], decisions=["STOP"],
            goal=RETRIEVAL_GOAL)
        contract = activity["goal_contract"]
        (out,) = contract["outputs"]
        assert out["satisfaction"] == "satisfied", diag()
        assert out["postcondition_facets"] == []
        assert "records_retrieved" not in (out.get("detail") or "")
        assert activity["goal_outcome"] == "complete", diag()
        assert WHOLE_CLAIM in resp, diag()
        assert ABSENT_MARK not in resp
        assert PARTIAL_MARK not in resp
