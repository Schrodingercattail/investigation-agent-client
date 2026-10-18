"""AL-1 resume re-validation of a persisted pre-fix focus (B-chain).

A focus persisted by the OLD resolver (cross-case seed pollution:
U00005's "why is high withdrawal frequency a risk item" focused F3 =
First Withdrawal to New Address) must not silently survive task
recovery just because the id still exists in the case. Recovery
re-resolves the SAME goal through the CURRENT resolver: an updated
RESOLVED target replaces the stale focus; user-selected focus is never
re-interpreted; no re-resolution result keeps the persisted focus
(pre-existing resume contract).

Runs the REAL recovery path: real ContextResolver + real Executor over
the real tool boundary (live RP), scripted decision proposer, isolated
temp store. No production data is touched.
"""

import json
import tempfile

import pytest

from app.agent_loop import AgentLoopRunner, AgentLoopState
from app.candidate_metadata import TaskStoreCatalogProvider
from app.config import settings
from app.context_resolution import ContextResolver
from app.executor_v2 import ExecutorV2, default_tool_provider
from app.task_store_v2 import TaskStoreV2


class _ScriptLLM:
    """LLM assist stub: proposes F2 among the CURRENT case's findings
    (the payload shape the assist layer validates: verbatim fragment +
    a target term occurring in the target's authoritative text)."""

    def __init__(self, selected_id="F2"):
        self.selected_id = selected_id

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        return json.dumps({
            "resolution": "RESOLVED",
            "finding_id": self.selected_id,
            "confidence": "high",
            "reason": "high withdrawal frequency",
            "evidence": {
                "request_fragments": ["高频提现"],
                "target_terms": ["high withdrawal frequency"],
                "per_candidate_support": [],
            },
        })


class _ScriptProposer:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        if self.decisions:
            from app.agent_loop import DecisionProposal
            return DecisionProposal(decision=self.decisions.pop(0),
                                    reason="scripted")
        from app.agent_loop import DecisionProposal
        return DecisionProposal(decision="STOP", reason="scripted")


def _u00005_findings():
    from app.domain_tools import risk_case_fetch
    res = risk_case_fetch(case_id="U00005")
    return list((res.data or {}).get("findings") or [])


def _persist_polluted_state(store):
    """A pre-fix run, mid-flight: case U00005, the polluted focus (F3,
    agent_resolved), an adopted plan whose explain step has NO explicit
    target (historical shape — the executor injects the focus)."""
    task_id = "TASK-RESUME-ISO"
    from app.models import TaskV2, TaskStatusV2
    task = TaskV2(task_id=task_id, investigation_id="INV-RESUME-ISO",
                  user_request="为什么高频提现是风险项？", type="agent_loop",
                  status=TaskStatusV2.EXECUTING)
    store.create(task)
    state = AgentLoopState(
        task_id=task_id, investigation_id="INV-RESUME-ISO",
        case_id="U00005", goal="为什么高频提现是风险项？",
        criteria_kind="question_answered",
        focused_finding_id="F3",                 # the POLLUTED focus
        focus_source="agent_resolved",
        target_evidence_kind=None,               # pre-guard legacy shape
    )
    state.plan_steps = [
        {"step_id": "S1", "type": "explain_signal", "status": "pending",
         "tool_name": "signal_explain", "arguments": {}},
    ]
    state.iteration = 1
    store.save_agent_state(state)
    return task_id


def _runner(store, resolver, decisions):
    runner = AgentLoopRunner(
        store=store,
        executor=ExecutorV2(default_tool_provider()),
        proposer=_ScriptProposer(decisions),
        resolver=resolver,
    )
    return runner


@pytest.fixture()
def guard_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)


def _observed_explain_target(store, task_id):
    state = store.get_agent_state(task_id)
    explains = [o for o in state.observations
                if o.get("step_type") == "explain_signal"]
    assert explains, "explain_signal never ran"
    return state, explains[-1]


def test_resume_replaces_stale_polluted_focus(guard_on):
    """THE historical scenario: recovery re-resolves and the explain step
    executes against U00005:F2 with F2's OWN rule — never F3's
    first-withdrawal semantics."""
    store = TaskStoreV2(db_path=tempfile.mkdtemp() + "/resume_iso.db")
    task_id = _persist_polluted_state(store)
    resolver = ContextResolver(
        llm_provider=_ScriptLLM("F2"), llm_assist=True,
        semantic_catalog=TaskStoreCatalogProvider(store))
    runner = _runner(store, resolver, ["CONTINUE", "STOP"])

    result = runner.resume(task_id, findings_provider=_u00005_findings)

    state, obs = _observed_explain_target(store, task_id)
    # the focus was re-validated to the correct target BEFORE execution
    assert state.focused_finding_id == "F2"
    # the executed tool call targeted F2 with F2's own rule semantics
    assert obs.get("summary", {}).get("rule_name") == \
        "High withdrawal frequency"
    assert "first_withdrawal" not in json.dumps(
        obs.get("summary") or {}).lower()
    assert result.state.agent_status == "completed"


def test_resume_keeps_user_selected_focus(guard_on):
    """Explicit user selection is authoritative: recovery does not
    re-interpret it (contract preserved)."""
    store = TaskStoreV2(db_path=tempfile.mkdtemp() + "/resume_iso2.db")
    task_id = _persist_polluted_state(store)
    state = store.get_agent_state(task_id)
    state.focus_source = "user_selected"       # explicit selection of F3
    store.save_agent_state(state)
    resolver = ContextResolver(
        llm_provider=_ScriptLLM("F2"), llm_assist=True,
        semantic_catalog=TaskStoreCatalogProvider(store))
    runner = _runner(store, resolver, ["CONTINUE", "STOP"])

    runner.resume(task_id, findings_provider=_u00005_findings)

    state, obs = _observed_explain_target(store, task_id)
    assert state.focused_finding_id == "F3"    # never re-interpreted


def test_resume_without_reresolution_keeps_persisted_focus(guard_on):
    """No resolver (or no usable resolution) keeps the persisted focus —
    the pre-existing resume contract, unchanged."""
    store = TaskStoreV2(db_path=tempfile.mkdtemp() + "/resume_iso3.db")
    task_id = _persist_polluted_state(store)
    runner = _runner(store, None, ["CONTINUE", "STOP"])   # no resolver

    runner.resume(task_id, findings_provider=_u00005_findings)

    state, obs = _observed_explain_target(store, task_id)
    assert state.focused_finding_id == "F3"
    assert obs.get("summary", {}).get("rule_name") == \
        "First withdrawal to new address"      # honest bounded outcome
