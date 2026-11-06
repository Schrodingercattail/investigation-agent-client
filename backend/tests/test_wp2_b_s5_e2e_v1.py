"""WP-2 Version B S5 — REAL-API E2E verification (Blocks A-H).

Every core scenario goes through the real HTTP API face (TestClient ->
FastAPI -> InvestigationAPI -> service / AgentLoopRunner -> shared
admission service -> ExecutorV2 -> artifact_bundle -> persistence).
The model boundary is injected (documented deterministic-API
infrastructure); the admission service, loop, executor, artifact tool
and persistence chain are the REAL production paths.

Stage boundaries: Browser E2E = S6 (NOT IN SCOPE); archival = S7. This
file does not constitute Version B overall acceptance.

Feature flags: ENABLE_GOAL_SEMANTIC_CONTRACT pinned explicitly in every
scenario (D4 discipline).
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.agent_loop import (
    AgentLoopRunner,
    AgentLoopState,
    AgentStatus,
    HeuristicDecisionProposer,
    PlanStepStatus,
    StopReason,
)
from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.config import settings
from app.context_resolution import ContextResolver
from app.delivery_admission import canonical_records_digest
from app.investigation_service import InvestigationService
from app.llm_provider import ClaudeProvider, LLMError
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al210_capability_routing_v1 import (
    _findings as _al210_findings,
)
from tests.test_al28_semantic_foundation_v1 import seeded_catalog

CASE = "U00010"
SET_REF = "CS-ab12cd34-S9"
ASK_REF = "导出 CS-ab12cd34-S9 的候选交易明细文件"
ASK_PLAIN = "导出这个账户的全部交易记录文件"
ASK_TWO_REFS = "导出 CS-aaa11111-S1 和 CS-bbb22222-S2 的候选交易明细文件"
ASK_ANALYZE_EXPORT = "分析 CS-ab12cd34-S9 集合内的对冲候选并导出明细文件"
INJECTED_GU_ERROR = ("peer closed connection without sending complete "
                     "message body (injected WP2-S5)")


def _leg(tid, side):
    return {"transaction_id": tid, "account_id": "ACC-A", "side": side,
            "quantity": 1.0, "value": 100.0, "symbol": "BTC",
            "timestamp": "2026-08-19T10:00:00+00:00"}


def mk_candidate(cid="CATA-001", t1="TX-1", t2="TX-2"):
    return {"candidate_id": cid, "rank": 1, "account_a": "ACC-A",
            "account_b": "ACC-B", "symbol": "BTC",
            "supporting": [_leg(t1, "BUY"), _leg(t2, "SELL")]}


def mk_entry(set_ref=SET_REF, task="TASK-1", candidates=None):
    return {"set_ref": set_ref, "source_task_id": task,
            "source_step_id": "S1", "created_at": "2026-10-31T00:00:00Z",
            "bounded_data": False, "origin": "analysis",
            "candidates": candidates if candidates is not None
            else [mk_candidate()]}


class ScriptedNever:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def _build_client(tmp_path, monkeypatch, *, scripted_session=None,
                  loop_owns_planner=False):
    """loop_owns_planner=True keeps the runner's planner=None so the
    scripted-session plans/decisions seam activates (Block E); the
    default builds an explicit deterministic planner/proposer."""
    store = TaskStoreV2(str(tmp_path / "wp2_s5.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
    import tests.test_risk_platform_adapter_v2 as fx

    def _fetch_case(self, uid):
        return (fx.rp_evidence_payload(CASE),
                fx.rp_explanation_payload())

    monkeypatch.setattr(_rcf.RiskPlatformAdapter, "fetch_case", _fetch_case)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    monkeypatch.setattr(
        ClaudeProvider, "generate",
        lambda self, messages, max_tokens=0, temperature=0.1, **kw:
            (_ for _ in ()).throw(LLMError(INJECTED_GU_ERROR)))
    if scripted_session is not None:
        path = tmp_path / "wp2_s5_session.json"
        path.write_text(json.dumps(scripted_session, ensure_ascii=False),
                        encoding="utf-8")
        monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE", str(path))
    else:
        monkeypatch.delenv("AGENT_LOOP_SCRIPTED_SESSION_FILE",
                           raising=False)

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=(None if loop_owns_planner else PlannerV2(_NeverLLM())),
            proposer=(None if loop_owns_planner
                      else HeuristicDecisionProposer()),
        )

    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(_NeverLLM()),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: _al210_findings(),
        loop_runner_factory=_factory,
    )
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._wp2_store = store
    return client


def _create_and_anchor(client, ask="Investigate U00010"):
    """Create an investigation and complete its first submission — the
    resulting task is the provenance anchor for seeded sets."""
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": ask}, timeout=180).json()
    tid = r.get("task_id") or (r.get("task") or {}).get("task_id")
    _wait(client, tid)
    return iid, tid


def _seed(client, iid, entries):
    doc = client._wp2_store.get_session(iid) or {}
    doc.setdefault("candidate_sets", []).extend(entries)
    client._wp2_store.save_session(iid, doc)


def _wait(client, tid):
    import time as _time
    deadline = _time.time() + 120
    while _time.time() < deadline:
        detail = client.get(f"/api/v2/tasks/{tid}").json()
        st = (detail.get("task") or {}).get("status")
        if st in ("completed", "failed"):
            return detail
        _time.sleep(0.3)
    return client.get(f"/api/v2/tasks/{tid}").json()


def _submit(client, iid, message):
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": message}, timeout=180).json()
    tid = r.get("task_id") or (r.get("task") or {}).get("task_id")
    detail = _wait(client, tid)
    doc = client._wp2_store.get_agent_state(tid)
    return tid, detail, doc


def _assert_rejected(client, tid, doc, reason):
    """The frozen H1 explicit-assertion set for one rejected target."""
    assert doc.agent_status == "blocked"
    assert doc.stop_reason == "clarification_needed"
    assert doc.goal_outcome == "partial"
    # the admission rejection is EXPLICITLY recorded, not inferred
    recs = [d for d in (doc.decisions or [])
            if d.get("decision") == "ADMISSION_INVALID_TARGET"]
    assert recs, "ADMISSION_INVALID_TARGET decision record missing"
    assert any(reason in str(d.get("reason")) for d in recs), \
        f"reason {reason} not in {[str(d.get('reason')) for d in recs]}"
    # the reason surfaces in the served response
    assert f"（准入原因：{reason}）" in (doc.response or "")
    # zero execution, zero artifact, zero export observation
    assert doc.observations == []
    assert doc.artifact_ids == []
    # persisted: no candidate_set_export anywhere (nothing to mark
    # delivered from) and the task container carries no artifact
    detail = client.get(f"/api/v2/tasks/{tid}").json()
    assert (detail.get("task") or {}).get("artifact_ids") == []
    for o in doc.observations:
        assert o.get("summary", {}).get("candidate_set_export") is None
    # the rejected delivery step is stripped from the persisted plan
    assert all(s.get("type") != "generate_artifact"
               for s in doc.plan_steps)


# --- Block B/C/H1: HSG admission matrix (real /submit → loop) ------------

def _matrix_scenarios(anchor_tid):
    """Build each scenario's entries INSIDE the run: the ownership-passing
    entries carry the REAL anchor task id (a static task id would be
    rejected as cross_investigation before the scenario's own failure
    mode fires). Scenarios that TEST ownership use a foreign task."""
    ok = mk_entry(task=anchor_tid)
    # genuinely service-malformed: a NON-DICT supporting item (the
    # wellformed gate rejects it before any injection)
    malformed = mk_entry(task=anchor_tid)
    malformed["candidates"][0]["supporting"] = [
        {"transaction_id": "TX-1", "account_id": "ACC-A", "side": "BUY",
         "quantity": 1.0, "value": 100.0, "symbol": "BTC",
         "timestamp": "2026-08-19T10:00:00+00:00"}, "junk"]
    nan_c = mk_candidate()
    nan_c["supporting"][0]["value"] = float("nan")
    return [
        ("no_ref", [mk_entry(task=anchor_tid)], ASK_PLAIN,
         "no_explicit_ref"),
        ("ref_not_in_session", [ok],
         "导出 CS-missing000-S1 的候选交易明细文件", "ref_not_in_session"),
        ("duplicate_set_ref", [ok, mk_entry(task=anchor_tid)], ASK_REF,
         "duplicate_set_ref"),
        ("cross_investigation", [mk_entry(task="TASK-FOREIGN")], ASK_REF,
         "cross_investigation"),
        ("empty_set",
         [mk_entry(task=anchor_tid, set_ref="CS-emptyset0-S1",
                   candidates=[])],
         "导出 CS-emptyset0-S1 的候选交易明细文件", "empty_set"),
        ("malformed_leg", [malformed], ASK_REF, "malformed_entry"),
        ("non_finite", [mk_entry(task=anchor_tid, candidates=[nan_c])],
         ASK_REF, "non_finite_record_values"),
        ("ambiguous_two_refs",
         [mk_entry(task=anchor_tid, set_ref="CS-aaa11111-S1"),
          mk_entry(task=anchor_tid, set_ref="CS-bbb22222-S2")],
         ASK_TWO_REFS, "ambiguous_mapping"),
    ]


S5_MATRIX = [
    ("no_ref", ASK_PLAIN, "no_explicit_ref"),
    ("ref_not_in_session", "导出 CS-missing000-S1 的候选交易明细文件",
     "ref_not_in_session"),
    ("duplicate_set_ref", ASK_REF, "duplicate_set_ref"),
    ("cross_investigation", ASK_REF, "cross_investigation"),
    ("empty_set", "导出 CS-emptyset0-S1 的候选交易明细文件", "empty_set"),
    ("malformed_leg", ASK_REF, "malformed_entry"),
    ("non_finite", ASK_REF, "non_finite_record_values"),
    ("ambiguous_two_refs", ASK_TWO_REFS, "ambiguous_mapping"),
]


@pytest.mark.parametrize("name,ask,reason", S5_MATRIX,
                         ids=[m[0] for m in S5_MATRIX])
def test_s5_hsg_matrix_invalid_classes(name, ask, reason,
                                       tmp_path, monkeypatch):
    """Blocks B/C/H1: real /submit → loop → shared admission service.
    Frozen fail-closed per class with the EXPLICIT assertion set
    (decision record, reason in response, zero execution, zero artifact,
    zero export observation, stripped delivery step)."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    scenarios = {s[0]: s for s in _matrix_scenarios(tid1)}
    _, entries, scenario_ask, _ = scenarios[name]
    assert scenario_ask == ask
    _seed(client, iid, entries)
    tid, detail, doc = _submit(client, iid, ask)
    _assert_rejected(client, tid, doc, reason)


def test_s5_hsg_no_autobind_with_single_seeded_set(tmp_path, monkeypatch):
    """The no-auto-bind pin at the API level: exactly one persisted set
    in the session and NO explicit ref — fail closed (never inferred)."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    _seed(client, iid, [mk_entry(task=tid1)])
    tid, detail, doc = _submit(client, iid, ASK_PLAIN)
    _assert_rejected(client, tid, doc, "no_explicit_ref")


def test_s5_hsg_valid_binding_delivers_in_boundary(tmp_path, monkeypatch):
    """Blocks B/A(pass direction)/G: HSG + explicit ref + persisted set
    → delivers ONLY the seeded candidates, dual verification echoes
    pass, normal completion, no clarify framing."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    seeded = [mk_entry(task=tid1),
              mk_entry(task=tid1, set_ref="CS-other99999-S3")]
    _seed(client, iid, seeded)
    tid, detail, doc = _submit(client, iid, ASK_REF)
    assert doc.agent_status == "completed"
    assert doc.artifact_ids, "valid binding must deliver"
    arts = client.get(f"/api/v2/tasks/{tid}/artifacts").json()["artifacts"]
    assert arts[0]["scope"] == f"candidate_set:{SET_REF}"
    content = arts[0]["content"]
    assert f"候选集: {SET_REF}" in content
    assert "TX-1" in content and "TX-2" in content
    # explicit selection disambiguates: the OTHER set's marker absent
    assert "CS-other99999-S3" not in content
    # dual verification echo on the persisted observation
    verifications = [o["summary"]["candidate_set_export"]
                     for o in (doc.observations or [])
                     if isinstance(o.get("summary"), dict)
                     and isinstance(
                         o["summary"].get("candidate_set_export"), dict)]
    assert verifications
    v0 = verifications[-1]
    assert v0["verification"]["passed"]
    assert v0["records_digest_verification"]["passed"]
    assert v0["records_digest_verification"]["expected"] == \
        v0["records_digest_verification"]["actual"]
    assert v0["provenance_verification"]["passed"]
    resp = detail.get("task_response_text") or ""
    assert "未通过确定性准入" not in resp
    # Block G: zero delivered-marking assertions do not apply here (a
    # DELIVERED target legitimately carries its verification payload);
    # the persisted observation IS the delivery evidence.


# --- Block F: /turn anti-bypass (re-verified in S5) ------------------------

def test_s5_turn_no_ref_refused(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    _seed(client, iid, [mk_entry(task=tid1)])
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": ASK_PLAIN},
                     timeout=180).json()
    assert (tr.get("artifacts") or []) == []
    assert "未通过确定性准入" in (tr.get("response") or "")


def test_s5_turn_invalid_ref_refused(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    _seed(client, iid, [mk_entry(task=tid1)])
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": "导出 CS-missing000-S1 的候选交易明细文件"},
                     timeout=180).json()
    assert (tr.get("artifacts") or []) == []
    assert "未通过确定性准入" in (tr.get("response") or "")


def test_s5_turn_valid_ref_delivers(tmp_path, monkeypatch):
    """Block F: the S3 core scenarios RE-VERIFIED in the S5 run (not
    cited from S3). Uses the EN deterministic ask — the zh R2a
    records-stream ask fails PLAN-TIME validation on the legacy /turn
    environment (pre-existing, recorded)."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    _seed(client, iid, [mk_entry(task=tid1)])
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": "export the candidates in "
                          "CS-ab12cd34-S9 as an artifact"},
                     timeout=180).json()
    artifacts = tr.get("artifacts") or []
    assert artifacts
    assert artifacts[0]["scope"] == f"candidate_set:{SET_REF}"
    assert "TX-1" in artifacts[0]["content"]
    assert "未通过确定性准入" not in (tr.get("response") or "")


# --- Block H2: non-ARTIFACT /turn passthrough ------------------------------

def test_s5_h2_turn_question_passthrough(tmp_path, monkeypatch):
    """Block H2: a non-ARTIFACT /turn request never enters candidate_set
    admission — no spurious rejection/clarification, original response
    behavior preserved."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    _seed(client, iid, [mk_entry()])
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": "这个案件的整体风险水平如何？"},
                     timeout=180).json()
    resp = tr.get("response") or ""
    assert resp, "non-ARTIFACT turn must still answer"
    assert "未通过确定性准入" not in resp
    assert "ADMISSION_INVALID_TARGET" not in json.dumps(tr)
    assert (tr.get("artifacts") or []) == []


# --- Block H3: /turn Case Intake exemption (Option A) -----------------------

def test_s5_h3_turn_intake_exempt_option_a(tmp_path, monkeypatch):
    """Block H3: a /turn Case Intake request is EXEMPT from
    candidate_set admission (recognized, no binding demanded, no
    rejection) — recorded on the /turn surface together with its
    PRE-EXISTING legacy behavior (an /turn intake has never run the
    U00004 loop strip; the resulting bundle on this legacy surface is
    pre-existing, unchanged by S3, and flagged for user adjudication)."""
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": "Investigate U00010"},
                     timeout=180).json()
    resp = tr.get("response") or ""
    # exempt: no candidate_set admission rejection / binding demand
    assert "未通过确定性准入" not in resp
    assert "ADMISSION_INVALID_TARGET" not in json.dumps(tr)
    # Option A loop-strip is a LOOP-surface gate; the /turn legacy
    # surface retains its pre-existing behavior (recorded honestly —
    # flagged for user adjudication, NOT a B regression)
    artifacts = tr.get("artifacts") or []
    print("H3-RECORD /turn intake artifacts on the legacy surface:",
          [a.get("artifact_id") for a in artifacts])


# --- Block D: resume via the real HTTP resume endpoint ----------------------

def _seed_midstate(store, iid, tid, entries):
    """Construct the resume precondition honestly: the persisted
    non-terminal document a killed run would have left — post-admission,
    post-injection, AFTER the read-only fetch succeeded and BEFORE the
    admitted artifact step executed."""
    entry = entries[0]
    cands = entry["candidates"]
    injected = {
        "source_kind": "candidate_set", "set_ref": SET_REF,
        "candidate_records": cands, "bounded_data": False,
        "source_task_id": entry["source_task_id"],
        "expected_records_digest": canonical_records_digest(cands),
        "expected_binding": {
            "kind": "candidate_set_export", "status": "bound_ready",
            "set_ref": SET_REF, "source_task_id": entry["source_task_id"],
            "source_step_id": "S1", "delivery_target_id": "DT-S2-1",
            "case_id": CASE},
        "delivery_target_id": "DT-S2-1", "source_step_id": "S1",
    }
    state = AgentLoopState(
        task_id=tid, investigation_id=iid, case_id=CASE, goal=ASK_REF,
        criteria_kind="artifact_produced",
        response_language="zh",
        goal_contract=None,
        goal_contract_decision={"outcome": "heuristic_single_goal",
                                "reason": "seeded mid-state"},
        goal_representation={"goal_type": "artifact_export",
                             "goal_resolution": "INFERRED",
                             "source": "heuristic",
                             "request_text": ASK_REF},
        plan_steps=[
            {"step_id": "S1", "type": "fetch_case", "status": "success",
             "tool_name": "risk_case_fetch", "arguments": {}},
            {"step_id": "S2", "type": "generate_artifact",
             "status": "pending", "tool_name": "artifact_bundle",
             "arguments": dict(injected)}],
        agent_status="running",
    )
    store.save_agent_state(state)
    return injected


def test_s5_d_resume_delivers_within_boundary_no_bypass_change(
        tmp_path, monkeypatch):
    """Block D: the resume path RE-RUNS admission on the adopted plan —
    the injected target re-verifies against the persisted session and
    delivers within the SAME boundary; expected digest/provenance match
    the original admission (idempotent re-injection)."""
    import time as _time
    client = _build_client(tmp_path, monkeypatch)
    iid, tid = _create_and_anchor(client)
    # the turn-1 background thread persists its post-terminal session
    # bookkeeping AFTER the task flips terminal — let it drain before
    # seeding so the admission reads the intended persisted state
    _time.sleep(1.5)
    entries = [mk_entry(task=tid)]
    _seed(client, iid, entries)
    _seed_midstate(client._wp2_store, iid, tid, entries)
    r = client.post(f"/api/v2/tasks/{tid}/resume", timeout=180)
    assert r.status_code == 200, r.text
    detail = _wait(client, tid)
    doc = client._wp2_store.get_agent_state(tid)
    assert (doc.goal_contract_decision or {}).get("outcome") == \
        "heuristic_single_goal"
    assert doc.agent_status == "completed"
    assert doc.artifact_ids, "resume must deliver the admitted target"
    arts = client.get(f"/api/v2/tasks/{tid}/artifacts").json()["artifacts"]
    assert arts[0]["scope"] == f"candidate_set:{SET_REF}"
    content = arts[0]["content"]
    assert "TX-1" in content and "TX-2" in content


def test_s5_d_resume_no_bypass_without_binding(tmp_path, monkeypatch):
    """Block D fail-closed direction: a resume whose plan carries an
    UNINJECTED HSG artifact step and whose session has no set → the
    re-run admission strips and clarifies (resume never bypasses)."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid = _create_and_anchor(client)
    state = AgentLoopState(
        task_id=tid, investigation_id=iid, case_id=CASE, goal=ASK_PLAIN,
        criteria_kind="artifact_produced", response_language="zh",
        goal_contract=None,
        goal_contract_decision={"outcome": "heuristic_single_goal",
                                "reason": "seeded mid-state"},
        goal_representation={"goal_type": "artifact_export",
                             "goal_resolution": "INFERRED",
                             "source": "heuristic",
                             "request_text": ASK_PLAIN},
        plan_steps=[
            {"step_id": "S1", "type": "fetch_case", "status": "pending",
             "arguments": {}},
            {"step_id": "S2", "type": "generate_artifact",
             "status": "pending", "arguments": {}}],
        agent_status="running",
    )
    client._wp2_store.save_agent_state(state)
    r = client.post(f"/api/v2/tasks/{tid}/resume", timeout=180)
    assert r.status_code == 200
    detail = _wait(client, tid)
    doc = client._wp2_store.get_agent_state(tid)
    _assert_rejected(client, tid, doc, "no_explicit_ref")


# --- Block E: same-run unpersisted set fail-closed --------------------------

def test_s5_e_same_run_set_fail_closed(tmp_path, monkeypatch):
    """Block E (V1 restriction): a NON-FIRST submission whose scripted
    plan would create a set (analysis) and then export it — the export's
    explicit ref cannot resolve against the PERSISTED session (the set
    is only persisted AFTER the run by the API layer), so admission
    fails closed BEFORE any execution: the analysis never runs either,
    zero artifacts, zero delivered."""
    p1 = {"skill_id": "case_intake", "goal": "eval",
          "steps": [{"type": "fetch_case", "reason": "eval"}]}
    p2 = {"skill_id": "case_trade_analysis", "goal": "eval",
          "steps": [
              {"type": "analyze_related_account_set_trades",
               "reason": "eval"},
              {"type": "generate_artifact", "reason": "eval"}]}
    client = _build_client(
        tmp_path, monkeypatch,
        scripted_session={
            "plans": [json.dumps(p1, ensure_ascii=False),
                      json.dumps(p2, ensure_ascii=False)],
            "decisions": ["CONTINUE", "CONTINUE", "STOP"]},
        loop_owns_planner=True)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    # turn 1 (anchor): consumes P1 — the plain fetch plan
    r1 = client.post(f"/api/v2/investigations/{iid}/submit",
                     json={"message": "Investigate U00010"},
                     timeout=180).json()
    tid1 = r1.get("task_id") or (r1.get("task") or {}).get("task_id")
    _wait(client, tid1)
    # turn 2: consumes P2 (analysis + export) — the export's ref cannot
    # resolve against the persisted session (nothing created it), so
    # admission fails closed BEFORE the analysis runs
    tid, detail, doc = _submit(client, iid, ASK_ANALYZE_EXPORT)
    _assert_rejected(client, tid, doc, "ref_not_in_session")
    sess = client._wp2_store.get_session(iid) or {}
    assert sess.get("candidate_sets") in (None, [])
