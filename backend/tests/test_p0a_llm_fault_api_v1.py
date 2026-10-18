"""P0/A-fix — API-path E2E with controlled LLM fault injection (U00047).

The REAL API surface (FastAPI -> InvestigationService -> ContextResolver ->
PlannerV2 -> executor) with the LLM boundary scripted at
ClaudeProvider._generate_once — so the REAL ClaudeProvider.generate()
bounded-retry loop runs inside the API path. This is CONTROLLED FAULT
INJECTION at the LLM call boundary, NOT a real model-recovery test.

Scenarios:
  A. empty-then-valid: the planner's first LLM call is injected empty,
     the retry succeeds -> the valid plan is parsed/validated/executed
     normally; the turn completes with real tool evidence (exactly 2
     application-layer calls).
  B. consecutive empty: both attempts injected empty -> bounded failure;
     the truthful LLM_EMPTY_RESPONSE class (never LLM_UNAVAILABLE),
     exactly 2 application-layer calls, no executed steps, honest
     user message, no completion claim.
  C. classification control: a provider outage (LLMError) keeps its own
     LLM_UNAVAILABLE message — the classes stay distinct at the API
     surface.
"""

import tempfile

from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.investigation_service import InvestigationService
from app.context_resolution import ContextResolver
from app.llm_provider import (
    ClaudeProvider,
    LLMEmptyResponseError,
    LLMError,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al210_capability_routing_v1 import (
    _findings as _al210_findings,
)
from tests.test_al28_semantic_foundation_v1 import (
    _extended_rp,
    seeded_catalog,
)
from tests.test_api_investigations_v1 import ScriptedLLM, rp_patches
import tests.test_risk_platform_adapter_v2 as fx

# investigation intent (no capability/artifact vocabulary), no explain
# domains, no evidence-stream or case-level-capability vocabulary —
# the deterministic planning branches decline and the LLM planner runs.
REQUEST = "评估一下这个发现的风险等级和处置优先级"
PLAN_JSON = ('{"skill_id": "timeline_investigation", "goal": "assess", '
             '"steps": [{"type": "explain_signal", "reason": "assess the '
             'finding"}]}')


def _build_client(planner_provider):
    store = TaskStoreV2(tempfile.mkdtemp() + "/p0a_fault_api.db")
    catalog, _ = seeded_catalog(store)
    # the resolver's own LLM is never scripted-to-fail: explicit focus
    # resolves deterministically (al210 harness pattern)
    resolver = ContextResolver(llm_provider=ScriptedLLM([]),
                               llm_assist=False, semantic_catalog=catalog)
    planner = PlannerV2(planner_provider)
    service = InvestigationService(
        resolver=resolver,
        planner=planner,
        executor=None, task_store=store)
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda cid:
                           _al210_findings())
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _run_two_turns(client):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00010"}).json()
    iid = inv["investigation"]["investigation_id"]
    r1 = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": "Investigate U00010"}).json()
    r2 = client.post(
        f"/api/v2/investigations/{iid}/turn",
        json={"message": REQUEST,
              "context_action": {"type": "focus_finding",
                                 "finding_id": "F3"}}).json()
    return r1, r2


class _FaultedProvider(ClaudeProvider):
    """Real ClaudeProvider; _generate_once (below the REAL generate()
    bounded-retry loop) is scripted per scenario."""

    def __init__(self):
        super().__init__(api_key="test-key", model="test-model")
        self.once_calls = 0
        self._behaviors = ["EMPTY"]

    def script(self, *behaviors):
        self._behaviors = list(behaviors)

    def _generate_once(self, messages, max_tokens, temperature):
        self.once_calls += 1
        behavior = self._behaviors[min(self.once_calls - 1,
                                       len(self._behaviors) - 1)]
        if behavior == "EMPTY":
            raise LLMEmptyResponseError(
                "LLM response contained no text block (injected)",
                context={"injected": True})
        if behavior == "OUTAGE":
            raise LLMError("gateway 502", context={"status_code": 502})
        return behavior


def test_empty_then_valid_recovers_through_api_path():
    """A: injected empty -> provider retry -> valid plan -> normal
    parse/validation/execution; turn completes with tool evidence."""
    provider = _FaultedProvider()
    provider.script("EMPTY", PLAN_JSON)
    client = _build_client(provider)
    with rp_patches()[0], _extended_rp(_al210_findings()):
        r1, r2 = _run_two_turns(client)

    assert r1["status"] == "completed"
    # the REAL retry loop consumed exactly one extra application-layer call
    assert provider.once_calls == 2
    assert r2["status"] == "completed"
    plan = r2.get("plan") or {}
    assert plan.get("steps"), "valid plan must be adopted"
    tools = (r2.get("execution") or {}).get("tool_calls") or []
    assert tools and tools[0]["tool_name"] == "signal_explain"
    assert tools[0]["outcome"] == "success"


def test_consecutive_empty_fails_bounded_with_true_class():
    """B: both attempts injected empty -> the planner raises the
    post-retry LLMEmptyResponseError; the classification is truthful
    (NOT LLM_UNAVAILABLE), the failure is bounded, and nothing executes."""
    provider = _FaultedProvider()
    provider.script("EMPTY")            # every attempt empty
    client = _build_client(provider)
    with rp_patches()[0], _extended_rp(_al210_findings()):
        r1, r2 = _run_two_turns(client)

    assert r1["status"] == "completed"
    # bounded: first call + ONE retry, then stop — never more
    assert provider.once_calls == 2
    assert r2["status"] == "failed"
    # truthful classification at the user surface: the empty-response
    # wording, never the provider-unavailable wording
    assert "empty responses" in r2["response"]
    assert "temporarily unavailable" not in r2["response"]
    assert not (r2.get("plan") or {}).get("steps")
    tools = (r2.get("execution") or {}).get("tool_calls") or []
    assert tools == []                  # no step executed on a planning failure
    # no false completion claim: the honest message names the failure
    assert "could not be planned" in r2["response"]


def test_provider_outage_keeps_llm_unavailable_class():
    """C: a genuine LLMError outage stays in its own class at the API
    surface — distinct from the empty-response class."""
    provider = _FaultedProvider()
    provider.script("OUTAGE")
    client = _build_client(provider)
    with rp_patches()[0], _extended_rp(_al210_findings()):
        _, r2 = _run_two_turns(client)

    assert r2["status"] == "failed"
    assert "temporarily unavailable" in r2["response"]
    assert "empty responses" not in r2["response"]


# --- goal/loop surface (the U00047 defect shape: loop task, planner LLM) ---


class _TurnScriptStub:
    """The turn path is never invoked by the goal endpoint (gsp7 harness
    pattern): a bounded placeholder keeps the service constructible."""

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        return ""


def _build_goal_client(tmp_path, monkeypatch, planner_provider):
    """The REAL goal endpoint with the PRODUCTION loop runner; the
    scripted-session seam pins goal understanding + decision proposer
    (otherwise they would call the real network), while the explicitly
    supplied planner — the seam never overrides an explicit one — is the
    real PlannerV2 over the faulted provider, so the PLANNER's LLM call
    is the faulted/retried surface."""
    import json as _json

    from app.agent_loop import AgentLoopRunner
    from app.candidate_metadata import TaskStoreCatalogProvider

    session = {
        "plans": [],                      # unused: the planner is explicit
        "decisions": [],                  # proposer: unusable → fallback STOP
        "goal": {                         # scripted goal understanding
            "case_id": "U00010",
            "goal_type": "explain_finding",
            "goal_resolution": "INFERRED",
            "subject_type": "finding",
            "subject_ref": "F3",
            "ambiguous_goal_types": [],
            "reason": "scripted session",
            "source": "llm",
            "request_text": REQUEST,
        },
    }
    path = tmp_path / "p0a_goal_session.json"
    path.write_text(_json.dumps(session, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE", str(path))

    store = TaskStoreV2(str(tmp_path / "p0a_goal_fault.db"))
    catalog, _ = seeded_catalog(store)

    def _factory(loop_store):
        return AgentLoopRunner(
            store=loop_store,
            resolver=ContextResolver(
                semantic_catalog=TaskStoreCatalogProvider(loop_store)),
            planner=PlannerV2(planner_provider))

    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(_TurnScriptStub()),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: _al210_findings(),
        loop_runner_factory=_factory)
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def test_goal_run_planner_empty_then_valid_completes_with_task_outcome(
        tmp_path, monkeypatch):
    """The U00047 defect shape on the REAL loop surface: inside a real
    Agent API goal run, the PLANNER's first LLM call is injected empty,
    the REAL ClaudeProvider bounded retry recovers, the second (valid)
    response is parsed/validated/adopted, the step executes, and the
    loop Task reaches an honest completed terminal (no failure, no
    unavailable wording, no fabricated completeness verdict)."""
    import time as _time

    provider = _FaultedProvider()
    provider.script("EMPTY", PLAN_JSON)
    client = _build_goal_client(tmp_path, monkeypatch, provider)

    with rp_patches()[0], _extended_rp(_al210_findings()):
        inv = client.post("/api/v2/investigations",
                          json={"case_id": "U00010"})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = client.post(f"/api/v2/investigations/{inv_id}/goal",
                          json={"goal": REQUEST,
                                "context_action": {
                                    "type": "focus_finding",
                                    "finding_id": "F3"}})
        assert run.status_code == 200, run.text
        assert run.json()["agent_status"] == "running"
        task_id = run.json()["task_id"]

        deadline = _time.time() + 30
        activity = None
        while _time.time() < deadline:
            activity = client.get(
                f"/api/v2/tasks/{task_id}/activity").json()
            if activity["is_terminal"]:
                break
            _time.sleep(0.02)
        assert activity and activity["is_terminal"], "loop did not terminate"

    detail = client.get(f"/api/v2/tasks/{task_id}").json()

    # the planner retried exactly once at the REAL provider boundary
    assert provider.once_calls == 2
    # honest completed terminal
    assert activity["agent_status"] == "completed"
    assert activity.get("stop_reason") == "goal_satisfied"
    steps = activity.get("plan_steps") or []
    assert any(s.get("type") == "explain_signal"
               and s.get("status") == "success" for s in steps)
    # contract-free run: no fabricated completeness verdict (GSP off)
    assert activity.get("goal_outcome") is None
    # the user-facing answer is substantive, never an outage claim
    response_text = detail.get("task_response_text") or ""
    assert "temporarily unavailable" not in response_text
    assert "could not be planned" not in response_text
    assert detail["task"]["status"] == "completed"
