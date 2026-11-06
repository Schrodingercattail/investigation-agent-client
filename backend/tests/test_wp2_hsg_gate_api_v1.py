"""WP-2 S1 / D2 — GU-fault-injection REAL-API E2E for the HSG clarify
path, per the approved S1 Kickoff (docs/plans/
WP2_VERSION_B_IMPLEMENTATION_KICKOFF_S1.md, PRE-02 / T4).

EVIDENCE SCOPE (kickoff §4 PRE-02, S1/S5 separation): every assertion
here verifies the CURRENT production (A-gate) behavior — the injected
heuristic_single_goal fallback drives today's planning-time
clarification. Nothing in this file is Version B acceptance evidence;
when Version B lands, S5 REWRITES these scenarios to assert the B
admission outcomes. Reuse across stages is limited to the injection
mechanism, the real-API face, and the evidence conventions.

Scenarios:
  (i)  zh HSG injected — current A-gate clarify outcome, zh turn;
  (ii) en HSG injected — the mirror, en turn;
  (iii) GU-success control (contract_accepted) — deterministic via the
       documented scripted-session goal seam (WP-CG-4-class test
       infrastructure); proves the harness discriminates the two GU
       outcomes and that CURRENT production delivers a
       contract_accepted artifact ask gate-free. Limited proof scope:
       says NOTHING about Version B admission behavior or the A-gate
       retirement.

Real chain under test (mirror of the accepted Phase 1 pattern
test_delivery_gate_g7_api_v1.py): FastAPI TestClient (real HTTP API
face -> InvestigationAPI.submit -> GU understanding with the injected
transport failure -> deterministic heuristic fallback ->
heuristic_single_goal decision -> PlannerV2 deterministic artifact
branch A-gate -> planning-time clarification terminal -> persist).
The RP adapter is patched offline; the resolver LLM is deterministic;
the class-level ClaudeProvider.generate injection reproduces the
audited "peer closed connection" transport failure.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner, HeuristicDecisionProposer
from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.config import settings
from app.context_resolution import ContextResolver
from app.investigation_service import InvestigationService
from app.llm_provider import ClaudeProvider, LLMError
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al210_capability_routing_v1 import (
    _findings as _al210_findings,
)
from tests.test_al28_semantic_foundation_v1 import seeded_catalog

CASE = "U00010"
ASK_EN = "generate all trade records of this account as an artifact"
ASK_ZH = "导出这个账户的全部交易记录文件"
INJECTED_GU_ERROR = ("peer closed connection without sending complete "
                     "message body (injected WP2-S1-D2)")

LANG_SCENARIOS = [
    ("en", ASK_EN, "Please clarify the delivery scope",
     "请补充说明交付的具体范围"),
    ("zh", ASK_ZH, "请补充说明交付的具体范围",
     "delivery-generation (artifact) goal"),
]


class ScriptedNever:
    """Resolver LLM stub: target resolution stays deterministic; any
    resolver LLM use is a test failure."""

    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    """Planner LLM stub: the deterministic artifact branch must never
    reach the planner LLM (the A-gate refuses before any LLM call)."""

    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


class _RaisingSubmitGU:
    """Submit-level GU stub that RAISES (understand_goal itself never
    raises on LLM errors — it bounded-falls-back to a heuristic
    representation, which api.submit then hands to the runner and which
    would override the loop's scripted-session goal seam). Raising here
    takes api.submit's designed exception path (representation=None →
    the loop classifies the request itself → the goal seam applies)."""

    def __call__(self, *args, **kwargs):
        raise LLMError(INJECTED_GU_ERROR)


def _goal_session():
    """Deterministic accepted-contract template for the control
    scenario (scripted-session goal seam; documented test
    infrastructure). delivery=file_export keeps the U00004 strip gate
    from removing the artifact step, so the control exercises a real
    gate-free contract_accepted delivery."""
    claim = (f"查询案例 {CASE} 的整体情况：已识别的风险发现与"
             "总体风险水平")
    return {"goal": {
        "case_id": CASE,
        "goal_type": "summarize",
        "goal_resolution": "INFERRED",
        "subject_type": "case",
        "subject_ref": CASE,
        "ambiguous_goal_types": [],
        "reason": "scripted accepted contract (WP2-S1-D2 control)",
        "source": "llm",
        "proposal_source": "llm",
        "request_text": ASK_EN,
        "contract_proposal": {"required_outputs": [{
            "claim": claim,
            "claim_description": claim,
            "claim_kind": "retrieval",
            "subject_ref": CASE,
            "scope": "case_level",
            "evidence_requirements": ["case_summary"],
            "delivery": "file_export",
        }]},
    }}


def _build_client(tmp_path, monkeypatch, *, goal_session=None,
                  raising_submit_gu=False):
    store = TaskStoreV2(str(tmp_path / "wp2_api.db"))
    catalog, _ = seeded_catalog(store)

    _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
    import tests.test_risk_platform_adapter_v2 as fx

    def _fetch_case(self, uid):
        return (fx.rp_evidence_payload(CASE),
                fx.rp_explanation_payload())

    monkeypatch.setattr(_rcf.RiskPlatformAdapter, "fetch_case",
                        _fetch_case)

    if goal_session is not None:
        path = tmp_path / "wp2_api_session.json"
        path.write_text(json.dumps(goal_session, ensure_ascii=False),
                        encoding="utf-8")
        monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE",
                           str(path))
    else:
        monkeypatch.delenv("AGENT_LOOP_SCRIPTED_SESSION_FILE",
                           raising=False)

    def _factory(store):
        return AgentLoopRunner(
            store=store,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(_NeverLLM()),
            proposer=HeuristicDecisionProposer(),
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
    if raising_submit_gu:
        api.goal_resolver = _RaisingSubmitGU()
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._wp2_store = store
    return client


def _submit_and_wait(client, iid, message, tag):
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": message},
                    timeout=180).json()
    tid = r.get("task_id") or ((r.get("task") or {}).get("task_id"))
    import time as _time
    deadline = _time.time() + 120
    while _time.time() < deadline:
        detail = client.get(f"/api/v2/tasks/{tid}").json()
        st = (detail.get("task") or {}).get("status")
        if st in ("completed", "failed"):
            break
        _time.sleep(0.3)
    return tid


def _hsg_scenario(lang, ask, marker, anti_marker,
                  tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    # the audited transport failure, injected at the model boundary —
    # every GU LLM attempt fails -> the REAL deterministic heuristic
    # fallback records heuristic_single_goal (the HSG-path proof).
    monkeypatch.setattr(
        ClaudeProvider, "generate",
        lambda self, messages, max_tokens=0, temperature=0.1, **kw:
            (_ for _ in ()).throw(LLMError(INJECTED_GU_ERROR)))

    client = _build_client(tmp_path, monkeypatch)

    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    # Turn 1 (first submission): with the injected GU failure the
    # intake exception substitutes the deterministic case-intake
    # representation — NOT the gate under test.
    _submit_and_wait(client, iid, "Investigate U00010", f"{lang}-t1")
    # Turn 2: the artifact ask on a NON-first submission — the heuristic
    # fallback preserves the HSG decision, and the CURRENT production
    # A-gate refuses the delivery plan at planning time.
    tid = _submit_and_wait(client, iid, ask, f"{lang}-t2")

    doc = client._wp2_store.get_agent_state(tid)
    assert doc is not None, f"agent state missing for {tid}"
    # HSG-path proof: the persisted decision record, not a narrative.
    dec = (doc.goal_contract_decision or {}).get("outcome")
    assert dec == "heuristic_single_goal", f"decision={dec}"
    # Current A-gate terminal:
    assert doc.agent_status == "blocked"
    assert doc.stop_reason == "clarification_needed"
    assert doc.goal_outcome == "partial"
    # planning-time clarification: NOTHING executes
    assert doc.observations == []
    assert doc.artifact_ids == []
    served = doc.response or ""
    assert marker in served, f"[{lang}] marker missing: {served[:200]}"
    assert anti_marker not in served
    assert "未生成 artifact" in served or "no artifact was generated" in served
    return tid


def test_hsg_en_injected_current_gate_clarifies(tmp_path, monkeypatch):
    _hsg_scenario(*LANG_SCENARIOS[0], tmp_path=tmp_path,
                  monkeypatch=monkeypatch)


def test_hsg_zh_injected_current_gate_clarifies(tmp_path, monkeypatch):
    _hsg_scenario(*LANG_SCENARIOS[1], tmp_path=tmp_path,
                  monkeypatch=monkeypatch)


def test_control_contract_accepted_delivers_gate_free(tmp_path,
                                                      monkeypatch):
    """LIMITED PROOF SCOPE (kickoff PRE-02): proves (a) the harness
    discriminates the two GU outcomes and (b) CURRENT production
    delivers a contract_accepted artifact ask gate-free. Says NOTHING
    about Version B admission behavior or the A-gate retirement."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    # submit-level GU fails deterministically (injected); the loop's
    # scripted-session goal seam provides the accepted contract — the
    # documented deterministic-API-run infrastructure.
    monkeypatch.setattr(
        ClaudeProvider, "generate",
        lambda self, messages, max_tokens=0, temperature=0.1, **kw:
            (_ for _ in ()).throw(LLMError(INJECTED_GU_ERROR)))

    client = _build_client(tmp_path, monkeypatch,
                           goal_session=_goal_session(),
                           raising_submit_gu=True)

    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    # Turn 1: escapes the FIRST-submission intake substitution (the
    # turn-1 result itself is not under test — with the seam active it
    # also runs under the accepted contract).
    _submit_and_wait(client, iid, "Investigate U00010", "control-t1")
    # Turn 2: submit-level GU fails (injected) -> representation None ->
    # loop-internal understanding = the scripted accepted contract
    # (file_export) -> CURRENT production delivers gate-free.
    tid = _submit_and_wait(client, iid, ASK_EN, "control-t2")

    doc = client._wp2_store.get_agent_state(tid)
    assert doc is not None
    dec = (doc.goal_contract_decision or {}).get("outcome")
    assert dec == "contract_accepted", f"decision={dec}"
    assert doc.agent_status == "completed"
    # CURRENT production: a contract_accepted artifact ask delivers
    # gate-free (the delivery exception all frozen gates carve out).
    assert doc.artifact_ids, "control must deliver an artifact"
    served = doc.response or ""
    assert "请补充说明交付的具体范围" not in served
    assert "delivery-generation (artifact) goal" not in served
