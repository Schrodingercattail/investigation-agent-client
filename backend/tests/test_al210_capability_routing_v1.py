"""AL-2.10 — capability routing, error classification, answer fidelity.

The audit this file pins (QA doc §2/§3):

1. `semantic_resolution_unavailable` was produced by a provider that was
   REACHABLE — SemanticNormalizer's max_tokens=400 budget let a
   thinking-style model exhaust the budget with reasoning blocks, the
   response carried no text block, and a broad except mislabeled it
   llm_unavailable. The taxonomy now separates:
       genuine unavailability  → SEMANTIC_RESOLUTION_UNAVAILABLE
       answered-but-unusable   → fall-through (a resolution failure)
   The Error Classification Control tests make the four failure classes
   distinguishable — they may never collapse into one label again.
2. 「是否有其他账号和这个账号之间存在对冲交易…」 is a SUPPORTED case-level
   cross-account capability request; it routes deterministically to
   cross_account_trade_analysis and NEVER enters finding resolution.
3. 「哪些账号是这个账号的关联账号」 routes to the second case-level
   capability (related_account_analysis — an authoritative RP echo).

Boundary map:
  - Classification control: resolver unit, scripted LLMs (deterministic).
  - Differential A/B: API two-turn, real planner contract gate, real
    tools with the RP boundary controlled; normalizer script consumed =
    LLM-invoked (asserted ZERO for both capability routes).
  - Persistence: store ordering + loop-task start stamps.
"""

import json
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.config import settings
from app.context_resolution import ContextResolver, ResolutionStatus
from app.investigation_service import InvestigationService
from app.models import Finding, FindingCapability, InvestigationContext
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al28_semantic_foundation_v1 import (
    _extended_rp,
    _findings as _al28_findings,
    _norm_script,
    seeded_catalog,
)
from tests.test_api_investigations_v1 import ScriptedLLM, rp_patches
import tests.test_risk_platform_adapter_v2 as fx

CAPS = FindingCapability.model_validate(["timeline", "signal_explain"])

REQUEST_A = "高频提现有哪些证据"
REQUEST_B = ("是否有其他账号和这个账号之间存在对冲交易，"
             "如果有，输出相关证据给我")
REQUEST_R = "能告诉我哪些账号是这个账号的关联账号吗"


def _findings(ids=("F3", "F5", "F7")):
    return [
        Finding(finding_id=fid, case_id="U00010", type="rule_signal",
                title={"F3": "High withdrawal frequency.",
                       "F5": "Shared Device Relationships.",
                       "F7": "High Trading Frequency."}[fid],
                severity="high", summary=f"summary of {fid}",
                evidence_refs=[], signal_refs=[], policy_refs=[],
                capabilities=CAPS.model_copy(deep=True))
        for fid in ids
    ]


def _resolver(llm, catalog):
    return ContextResolver(llm_provider=llm, llm_assist=False,
                           semantic_catalog=catalog)


def _resolve(llm, text):
    catalog, _ = seeded_catalog()
    resolver = _resolver(llm, catalog)
    return resolver.resolve(
        text, InvestigationContext(case_id="U00010"), findings=_findings())


# --- Error Classification Control (the four classes never collapse) -----

class _GenuinelyDown:
    """Provider unreachable / config failure — the ONLY outage class."""

    def generate(self, **kw):
        from app.exceptions import LLMConfigurationError
        raise LLMConfigurationError("no api key", context={})


class _NoTextBlock:
    """Provider REACHABLE; response carries no text block
    (thinking model + small budget). NOT an outage."""

    def generate(self, **kw):
        from app.exceptions import LLMEmptyResponseError
        raise LLMEmptyResponseError(
            "LLM response contained no text block", context={})


def test_class_a_genuine_unavailable_is_the_only_outage():
    out = _resolve(_GenuinelyDown(), "为什么会标记出设备共享的关系")
    assert out.status == ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE
    assert out.semantic_evidence["reason_class"] == "llm_unavailable"


def test_class_b_no_text_block_is_never_reported_as_outage():
    out = _resolve(_NoTextBlock(), "为什么会标记出设备共享的关系")
    assert out.status != ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE
    # bounded fall-through: deterministic reason preserved (assist off)
    assert out.status == ResolutionStatus.UNRESOLVED


def test_class_c_malformed_proposal_is_never_reported_as_outage():
    out = _resolve(ScriptedLLM(["garbage, no json"]),
                   "为什么会标记出设备共享的关系")
    assert out.status != ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE
    assert out.status == ResolutionStatus.UNRESOLVED


def test_class_d_no_governed_concept_is_never_reported_as_outage():
    out = _resolve(ScriptedLLM([_norm_script(None, "")]),
                   "谈谈虚拟币洗钱这个风险项")
    assert out.status != ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE
    assert out.status == ResolutionStatus.UNRESOLVED


def test_normalizer_budget_alignment():
    """The root cause of the AL-2.10 misclassification: a 400-token
    budget is consumed by thinking-model reasoning blocks. The budget now
    matches the captured planner lesson (2048)."""
    from app.semantic_resolution import SemanticNormalizer
    assert SemanticNormalizer(lambda **kw: "")._max_tokens == 2048


# --- Capability-level routing (B and R never enter finding resolution) --

def test_hedge_request_routes_case_level_trade_capability():
    """B is a supported CASE-LEVEL cross-account request: deterministic
    plan for cross_account_trade_analysis — no finding target, no
    semantic grounding, no normalizer invocation."""
    from app.models import InvestigationContext
    from app.skills import eligible_skills_for_finding
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    plan = PlannerV2().plan(REQUEST_B,
                            InvestigationContext(case_id="U00010"),
                            eligible, finding_capabilities=None)
    assert [s.type for s in plan.steps] == \
        ["analyze_cross_account_trades", "generate_artifact"]
    assert plan.steps[0].tool_name == "cross_account_trade_analysis"


def test_related_account_request_routes_case_level_capability():
    from app.models import InvestigationContext
    from app.skills import eligible_skills_for_finding
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    plan = PlannerV2().plan(REQUEST_R,
                            InvestigationContext(case_id="U00010"),
                            eligible, finding_capabilities=None)
    assert [s.type for s in plan.steps] == ["analyze_related_accounts"]
    assert plan.steps[0].tool_name == "related_account_analysis"


def test_trade_intent_wins_over_related_account_vocabulary():
    from app import investigation_concepts as ic
    mixed = "看看哪些账号和这个账号之间有对冲交易"
    assert ic.is_cross_account_trade_analysis_request(mixed) is True
    assert ic.is_related_account_analysis_request(mixed) is False


# --- Differential E2E (A vs B on the same stack) -------------------------

def _build_client(normalizer_scripts, evidence_overrides=None):
    store = TaskStoreV2(tempfile.mkdtemp() + "/al210_api.db")
    catalog, _ = seeded_catalog(store)
    resolver = _resolver(ScriptedLLM(normalizer_scripts), catalog)
    service = InvestigationService(
        resolver=resolver,
        planner=PlannerV2(ScriptedLLM(["{}"])),
        executor=None, task_store=store)
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda cid: _findings())
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _two_turn(client, first, second):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00010"}).json()
    iid = inv["investigation"]["investigation_id"]
    evidence = fx.rp_evidence_payload()
    evidence["network_evidence"] = {
        "cluster_id": 1012, "cluster_name": "Cluster_device_sharing_1",
        "detection_type": "device_sharing", "member_count": 19,
        "cluster_risk_score": 80.0, "role_in_cluster": "spoke",
        "related_accounts_count": 10,
        "related_accounts": ["U00337", "U00325", "U00349"],
        "shared_devices": ["DEV8Z1O4A35QU"],
    }
    network_patch = patch(
        "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
        new=lambda self, uid, expose_complete_records=False: evidence)
    with rp_patches()[0], network_patch, _extended_rp(_findings()):
        r1 = client.post(f"/api/v2/investigations/{iid}/turn",
                         json={"message": first}).json()
        r2 = client.post(f"/api/v2/investigations/{iid}/turn",
                         json={"message": second}).json()
    return r1, r2


def test_differential_a_resolves_through_the_semantic_layer():
    """AL-2.11: with the lexicon removed, scenario A flows through the
    SAME M2 semantic entry as every other finding reference — the
    normalizer IS consulted (script consumed) and grounding is provenance
    recorded. Capability requests (scenario B) remain the zero-LLM tier."""
    script = ScriptedLLM([_norm_script("HIGH_WITHDRAWAL_FREQUENCY",
                                       "高频提现")])
    client = _build_client(script.scripts)
    r1, r2 = _two_turn(client, "展开一下00010的具体情况", REQUEST_A)
    assert r2["status"] == "completed"
    assert r2["context"]["focused_finding_id"] == "F3"
    # the semantic-grounding evidence IS the invocation provenance: the
    # normalizer ran (status OK) and grounding recorded the concept —
    # the lexicon's zero-LLM tier no longer exists for finding references
    sem = r2["target_resolution"]["semantic_grounding"]
    assert sem["stage"] == "semantic_normalizer"
    assert sem["status"] == "OK"
    assert sem["concept_id"] == "HIGH_WITHDRAWAL_FREQUENCY"


def test_differential_b_hedge_request_is_a_case_level_capability():
    """Scenario B (the reported failure): routed to the governed
    cross-account capability — semantic_grounding absent, normalizer
    never invoked, real tool executed, honest candidate answer."""
    script = ScriptedLLM([])
    client = _build_client(script)
    r1, r2 = _two_turn(client, "展开一下00010的具体情况", REQUEST_B)
    assert r2["status"] == "completed"
    tr = r2.get("target_resolution") or {}
    assert tr.get("kind") == "resolved"
    assert "semantic_grounding" not in tr          # never finding-resolved
    assert script.calls == 0                       # never normalizer-called
    plan = r2["plan"]
    assert plan["steps"][0]["type"] == "analyze_cross_account_trades"
    tools = r2["execution"]["tool_calls"]
    assert tools[0]["tool_name"] == "cross_account_trade_analysis"
    assert tools[0]["outcome"] == "success"
    # the answer is the honest analytical output (candidates or a bounded
    # no-match), never a resolution failure
    # in the REQUEST's language (per-turn response language: zh → zh face)
    assert ("候选跨账户对冲交易关系" in r2["response"]
            or "没有匹配的候选跨账户" in r2["response"]
            or "candidate cross-account" in r2["response"]
            or "No candidate cross-account" in r2["response"])


def test_related_account_capability_answers_from_rp_attribution():
    client = _build_client([])
    r1, r2 = _two_turn(client, "展开一下00010的具体情况", REQUEST_R)
    assert r2["status"] == "completed"
    assert r2["plan"]["steps"][0]["type"] == "analyze_related_accounts"
    tools = r2["execution"]["tool_calls"]
    assert tools[0]["tool_name"] == "related_account_analysis"
    assert tools[0]["outcome"] == "success"
    assert "U00337" in r2["response"]              # real RP attribution
    assert "Risk Platform network attribution" in r2["response"]


# --- Persistence ordering -------------------------------------------------

def test_loop_task_carries_started_at_for_chronology():
    """AL-2.10: the loop turn is part of the conversation chronology —
    its TaskV2 carries a start stamp (NULL reordered restores)."""
    from app.agent_loop import AgentLoopRunner, HeuristicDecisionProposer

    store = TaskStoreV2(tempfile.mkdtemp() + "/al210_loop.db")
    runner = AgentLoopRunner(store=store,
                             resolver=ContextResolver(llm_assist=False),
                             proposer=HeuristicDecisionProposer(),
                             findings_provider=lambda: [])

    def _boom():
        raise RuntimeError("rp down")

    runner.findings_provider = _boom
    result = runner.run(" Investigate U00010",
                        InvestigationContext(case_id="U00010"), [])
    listed = store.list_by_investigation(result.task.investigation_id)
    assert listed and listed[0].task_id == result.task.task_id
    assert listed[0].started_at is not None


def test_store_ordering_puts_unstamped_tasks_last():
    """Historic rows without a start stamp (pre-AL-2.10 loop runs) sort
    AFTER stamped turns — never jump to the top of a reopened
    conversation."""
    store = TaskStoreV2(tempfile.mkdtemp() + "/al210_order.db")
    from app.models import TaskStatusV2, TaskV2
    loop_old = TaskV2(task_id="TASK-aaa000000001",
                      investigation_id="CASE:U00010", type="agent_loop",
                      user_request="loop", status=TaskStatusV2.COMPLETED)
    turn1 = TaskV2(task_id="TASK-bbb000000002",
                   investigation_id="CASE:U00010", type="investigation_turn",
                   user_request="turn 1", status=TaskStatusV2.COMPLETED,
                   started_at="2026-10-01T10:00:00+00:00")
    turn2 = TaskV2(task_id="TASK-ccc000000003",
                   investigation_id="CASE:U00010", type="investigation_turn",
                   user_request="turn 2", status=TaskStatusV2.COMPLETED,
                   started_at="2026-10-01T10:01:00+00:00")
    for t in (loop_old, turn1, turn2):
        store.update(t)
    listed = [t.task_id for t in store.list_by_investigation("CASE:U00010")]
    assert listed == [turn1.task_id, turn2.task_id, loop_old.task_id]
