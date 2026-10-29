"""Bounded bilingual defect fixes R7 / R11 / R14 — focused tests.

R7: "Steps completed:" / "Artifacts produced:" metadata lines follow the
    response language in BOTH composers (GSP contract path and legacy
    flag-off path) — exercised through the real compose_loop_response.
R11: the loop's persistent_error message follows the response language
     (exercised through the real bounded exception branch).
R14: legacy /turn coverage disclosure lines follow the response language
     (coverage_lines face mapping + the run_turn call-site wiring).
"""

import tempfile

import pytest

from app.agent_loop import AgentLoopRunner, AgentLoopState, \
    compose_loop_response
from app.config import settings
from app.models import InvestigationContext, PlanStepStatus
from app.request_coverage import coverage_lines
from app.task_store_v2 import TaskStoreV2


def _state(lang, *, contract=None):
    st = AgentLoopState(
        task_id="T", investigation_id="INV", case_id="UA", goal="g",
        criteria_kind="question_answered",
        response_language=lang,
        goal_contract=contract)
    st.plan_steps = [{"step_id": "S1", "type": "explain_signal",
                      "status": PlanStepStatus.SUCCESS.value}]
    st.artifact_ids = ["ART-1"]
    st.agent_status = "completed"
    st.observations = []
    return st


def _monkey_flag(on):
    import contextlib
    @contextlib.contextmanager
    def cm():
        old = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = on
        try:
            yield
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = old
    return cm()


# ---------------- R7: metadata lines follow the response language -------

def test_r7_legacy_composer_zh_metadata(monkeypatch):
    st = _state("zh")
    with _monkey_flag(False):
        text = compose_loop_response(st)
    assert "已完成步骤: 1 (explain_signal)。" in text
    assert "已产出文件: ART-1。" in text
    assert "Steps completed" not in text
    assert "Artifacts produced" not in text


def test_r7_legacy_composer_en_metadata(monkeypatch):
    st = _state("en")
    with _monkey_flag(False):
        text = compose_loop_response(st)
    assert "Steps completed: 1 (explain_signal)." in text
    assert "Artifacts produced: ART-1." in text
    assert "已完成步骤" not in text
    assert "已产出文件" not in text


def test_r7_gsp_composer_zh_and_en_metadata(monkeypatch):
    """The GSP contract-path composer through the real
    compose_loop_response entry (mirrors the three_constraints GSP state
    construction)."""
    from app.goal_resolution import (
        GoalRepresentation, GoalResolutionStatus,
        deterministic_contract_proposal)

    proposal = deterministic_contract_proposal(
        "34.12%的反向交易率是账户间的对冲交易还是账户自己的对冲交易？"
        "为什么反向交易是风险项？", "F7", "U00010")
    rep = GoalRepresentation(
        case_id="U00010", goal_type="cross_account_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="finding", subject_ref="F7",
        source="heuristic",
        request_text="34.12%的反向交易率是账户间的对冲交易还是账户自己"
                     "的对冲交易？为什么反向交易是风险项？",
        contract_proposal=proposal)
    contract, reason = __import__("app.goal_resolution",
                                  fromlist=["build_goal_contract"]) \
        .build_goal_contract(rep, "U00010")
    assert contract is not None, reason
    for o in contract.outputs:
        o.satisfaction = "satisfied"

    for lang, expect_step, forbid in (
            ("zh", "已完成步骤: 1 (explain_signal)。", "Steps completed"),
            ("en", "Steps completed: 1 (explain_signal).", "已完成步骤")):
        st = _state(lang, contract=contract)
        st.goal_contract_decision = {"outcome": "contract_accepted"}
        with _monkey_flag(True):
            text = compose_loop_response(st)
        assert expect_step in text, (lang, text[-400:])
        assert forbid not in text, (lang, text[-400:])
        if lang == "zh":
            assert "已产出文件: ART-1。" in text
        else:
            assert "Artifacts produced: ART-1." in text


# ---------------- R11: persistent_error follows the language -----------

def _failing_runner(tmp_path, monkeypatch):
    store = TaskStoreV2(tempfile.mkdtemp() + "/r11.db")
    runner = AgentLoopRunner(store=store)
    monkeypatch.setattr(runner, "findings_provider", lambda: [],
                        raising=False)

    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(runner, "_run_loop", boom)
    return runner


def test_r11_persistent_error_zh(monkeypatch, tmp_path):
    runner = _failing_runner(tmp_path, monkeypatch)
    result = runner.run(
        "任意目标", InvestigationContext(case_id="U00010"), [],
        investigation_id="INV-R11", task_id="TASK-R11-ZH",
        response_language="zh",
        findings_provider=lambda: [])
    assert result.state.stop_reason == "persistent_error"
    assert result.state.response == \
        "调查因意外内部错误而停止。已保留部分结果。"


def test_r11_persistent_error_en(monkeypatch, tmp_path):
    runner = _failing_runner(tmp_path, monkeypatch)
    result = runner.run(
        "任意目标", InvestigationContext(case_id="U00010"), [],
        investigation_id="INV-R11E", task_id="TASK-R11-EN",
        response_language="en",
        findings_provider=lambda: [])
    assert result.state.stop_reason == "persistent_error"
    assert result.state.response == (
        "The investigation stopped because of an unexpected internal "
        "error. Partial results are preserved.")


# ---------------- R14: coverage_lines language face ---------------------

def _coverage(outcome="partial"):
    return {
        "outcome": outcome,
        "aspects": [
            {"satisfaction": "uncovered", "claim": "对冲候选存在性",
             "detail": "本次运行没有执行跨账户交易分析。"},
            {"satisfaction": "unresolved", "claim": "为什么是风险项",
             "detail": None},
        ],
    }


def test_r14_coverage_lines_zh_default_unchanged():
    lines = coverage_lines(_coverage())
    text = "\n".join(lines)
    assert lines[0] == "请求覆盖情况："
    assert "整体状态：请求已部分回答" in text
    assert "尚未覆盖" in text and "证据不足，无法确定" in text
    assert "Request coverage:" not in text and "not yet covered" not in text


def test_r14_coverage_lines_none_keeps_zh():
    assert coverage_lines(_coverage(), lang=None) == \
        coverage_lines(_coverage())


def test_r14_coverage_lines_en():
    lines = coverage_lines(_coverage(), lang="en")
    text = "\n".join(lines)
    assert lines[0] == "Request coverage:"
    assert "not yet covered" in text and "insufficient evidence" in text
    assert ("Overall status: the request was partially answered") in text
    assert "请求覆盖情况" not in text and "整体状态" not in text
    assert "尚未覆盖" not in text


def test_r14_unanswerable_en_and_zh():
    en = "\n".join(coverage_lines(
        _coverage(outcome="unanswerable"), lang="en"))
    zh = "\n".join(coverage_lines(_coverage(outcome="unanswerable")))
    assert "could not answer the identified aspects" in en
    assert "未能回答所识别的请求方面" in zh


def test_r14_run_turn_call_site_passes_language():
    """Structural: the legacy /turn path forwards response_language into
    coverage_lines (no silent zh default at the call site)."""
    import inspect
    from app import investigation_service
    src = inspect.getsource(investigation_service)
    assert "coverage_lines(coverage,\n                                         " \
        "lang=response_language)" in src or \
        "coverage_lines(coverage, lang=response_language)" in src
