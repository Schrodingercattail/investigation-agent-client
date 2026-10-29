"""U00008 truncation fix — bounded finding summaries + step.completed.

Fix A: project_observation's finding-summary bound now cuts at a SAFE
sentence boundary and appends an explicit bilingual omission note —
never a silent mid-sentence slice ("…The account is" rendered as if a
section stopped). The real U00008 shape (RP embeds "Account Age
(contextual)" inside F7's summary) is the regression anchor; fixtures
are generic.

Fix B: the identical-re-plan collapse emits the LAST step's
step.completed event exactly once — the activity block's
started+completed count now matches the executed steps.
"""
import re
from types import SimpleNamespace

import pytest

from app.agent_loop import (
    _OMIT_NOTE_EN,
    _OMIT_NOTE_ZH,
    _bounded_finding_summary,
    project_observation,
)
from app.models import ToolResult, ToolResultOutcome

# The audited U00008 F7 text (462 chars): "Account Age (contextual)"
# embedded mid-summary, with the old [:280] cut landing after
# "The account is".
F7_TEXT = (
    "An opposite-trade ratio of 34.29% was observed, which is below the "
    "40% threshold for the coordinated trading rule. This is a single "
    "aggregate statistic and does not by itself indicate coordination or "
    "offsetting of another party's positions.\n"
    "Account Age (contextual)\n"
    "The account is 19 days old. This is contextual evidence only; the "
    "system's new-account rule requires both a very young account and "
    "high trading activity, and no such rule trigger is recorded here.")


# --- Fix A: bounded summary boundary logic ------------------------------

def test_a_within_limit_unchanged():
    text = "Short summary. Second sentence."
    assert _bounded_finding_summary(text, limit=280) == text
    assert _bounded_finding_summary(text) == text


def test_a_sentence_boundary_cut_with_note_en():
    out = _bounded_finding_summary(F7_TEXT, limit=280, lang="en")
    assert out.endswith(_OMIT_NOTE_EN)
    # the cut kept the last COMPLETE sentence inside the (note-adjusted)
    # window — the first sentence ends "…trading rule."
    assert "trading rule." in out
    # never ends mid-sentence: the note is the ending
    assert not re.search(r"The account is$", out)
    # the omitted continuation is not silently present as a fragment
    assert "The account is\n" not in out
    # bound respected: content + note within the limit
    assert len(out) <= 280


def test_a_sentence_boundary_cut_with_note_zh():
    out = _bounded_finding_summary(F7_TEXT, limit=280, lang=None)
    assert out.endswith(_OMIT_NOTE_ZH)
    assert "positions." in out
    assert _OMIT_NOTE_EN not in out


def test_a_paragraph_boundary_when_no_sentence_enders():
    # window has a newline but no sentence ender before the budget: the
    # paragraph boundary (not a word cut) is the fallback
    text = "A" * 100 + "\n" + "B" * 100 + "\n" + "C" * 200
    out = _bounded_finding_summary(text, limit=280, lang="en")
    assert out.endswith(_OMIT_NOTE_EN)
    assert "AAAA" in out
    assert "BBBB" in out          # kept up to the last paragraph boundary
    assert "CCCC" not in out      # beyond the boundary → disclosed cut


def test_a_no_boundary_fallback_still_disclosed():
    # no punctuation, no spaces: deterministic hard cut + note (never
    # a silent arbitrary slice)
    text = "字" * 400
    out = _bounded_finding_summary(text, limit=280, lang="zh")
    assert out.endswith(_OMIT_NOTE_ZH)
    assert out.count("字") >= 80


@pytest.mark.parametrize("lang,note", [("en", _OMIT_NOTE_EN),
                                       ("zh", _OMIT_NOTE_ZH),
                                       (None, _OMIT_NOTE_ZH)])
def test_a_note_language_selection(lang, note):
    out = _bounded_finding_summary("word " * 100, limit=100, lang=lang)
    assert out.endswith(note)


def test_a_projection_integrates_bound_and_lang():
    findings = [SimpleNamespace(finding_id="F7", title="Opposite Trade",
                                summary=F7_TEXT)]
    result = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
        "findings": findings, "finding_count": 1})
    obs = project_observation("risk_case_fetch", "fetch_case", result,
                              lang="en")
    s = obs["summary"]["findings"][0]["summary"]
    assert s.endswith(_OMIT_NOTE_EN)
    assert "trading rule." in s
    assert len(s) <= 280
    obs_zh = project_observation("risk_case_fetch", "fetch_case", result,
                                 lang="zh")
    assert obs_zh["summary"]["findings"][0]["summary"].endswith(
        _OMIT_NOTE_ZH)
    # short summaries pass through untouched
    short = [SimpleNamespace(finding_id="F1", title="T", summary="Fine.")]
    obs_short = project_observation(
        "risk_case_fetch", "fetch_case",
        ToolResult(outcome=ToolResultOutcome.SUCCESS,
                   data={"findings": short}), lang="en")
    assert obs_short["summary"]["findings"][0]["summary"] == "Fine."


def test_a_real_u00008_shape_never_renders_the_account_is_alone():
    out = _bounded_finding_summary(F7_TEXT, limit=280, lang="en")
    # the audited artifact text stays available; the conversation body
    # ends at a sentence boundary + note instead of "The account is"
    assert "The account is 19 days old" in F7_TEXT      # upstream intact
    assert not out.rstrip().endswith("The account is")
    zh = _bounded_finding_summary(F7_TEXT, limit=280, lang="zh")
    # the zh note is shorter → its window keeps the second sentence too
    assert "positions." in zh and zh.endswith(_OMIT_NOTE_ZH)


# --- Fix B: identical-re-plan collapse emits the final step.completed ---

def _step_event_counts(state):
    started, completed = {}, {}
    for e in state.activity:
        if e.get("event_type") == "step.started":
            started[e.get("step_type")] = started.get(
                e.get("step_type"), 0) + 1
        if e.get("event_type") == "step.completed":
            completed[e.get("step_type")] = completed.get(
                e.get("step_type"), 0) + 1
    return started, completed


def test_b_collapse_emits_final_step_completed_once(tmp_path):
    from app.agent_loop import AgentLoopRunner
    from app.executor_v2 import ExecutorV2
    from app.planner_v2 import PlannerV2
    from app.task_store_v2 import TaskStoreV2
    from tests.test_agent_loop_v1 import (
        ScriptedLLM, ScriptedToolProvider, build_context, plan_json)

    # SUCCESS outcomes that still carry evidence_missing → the task-level
    # criteria stay unsatisfied, so the proposer's RE_PLAN is consulted
    # and the identical re-plan collapses (the audited U00008 shape).
    case_fetch = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
        "findings": [SimpleNamespace(
            finding_id="F1", title="T", summary="Fine.")],
        "finding_count": 1, "evidence_missing": True,
        "next_data_needed": ["policy context"]})
    policy = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
        "matches": [], "evidence_missing": True,
        "next_data_needed": ["policy context"]})
    store = TaskStoreV2(db_path=tmp_path / "u00008b.sqlite3")
    plan = plan_json("case_intake", ["fetch_case", "retrieve_policy"])
    planner = PlannerV2(ScriptedLLM([plan, plan]))     # identical re-plan
    runner = AgentLoopRunner(
        planner=planner, executor=ExecutorV2(ScriptedToolProvider(
            [case_fetch, policy])),
        store=store, proposer=__import__(
            "tests.test_agent_loop_v1", fromlist=["ScriptedProposer"])
        .ScriptedProposer(["RE_PLAN"]))
    result = runner.run("case overview", build_context(None, "U00299"),
                        [])
    state = result.state
    assert state.agent_status == "completed"
    assert state.stop_reason == "bounded_data"          # collapse semantics
    assert state.plan_revisions == 2
    executed = [o["step_type"] for o in state.observations]
    started, completed = _step_event_counts(state)
    # the LAST executed step (the terminal plan's final step) has its
    # completion event exactly once — the pre-fix gap
    assert completed.get(executed[-1]) == 1, (executed, completed)
    # no step is completed twice (no duplicate emits from the fix)
    assert all(v == 1 for v in completed.values()), completed
    # event count matches the successful plan steps (2/2)
    success_steps = [s for s in state.plan_steps
                     if s.get("status") == "success"]
    assert sum(completed.values()) == len(success_steps) == 2
    # a started event exists for the terminal step too (paired stream)
    assert started.get(executed[-1]) == 1
