"""Timeline emission completeness + policy applicability rendering.

Pins the targeted fixes verified in the 2026-10-26 acceptance round:

A1  the GSP timeline section renders the COMPLETE projected event set —
    a fixed render cap silently dropped events 9+ while the run stayed
    fully answered; an explicit top_n subset is disclosed with actual
    rendered/total counts.
A2  a compound timeline+records output renders BOTH sections — the
    records branch no longer swallows the timeline delivery.
A3  the TIMELINE_EVENTS postcondition judges emission/total parity from
    the uncapped event_summaries vs the tool's total_events (never the
    _MAX_IDS-bounded event_ids) — a shortfall is PARTIAL, never a
    silent SATISFIED.
B1  the policy overview wording follows finding_policy_status in BOTH
    languages (EN no longer mislabels finding-level citations as
    case-level).
B2  the no-finding-level-basis note is REACHABLE: case-level-only
    answers state the evidence boundary and render the case-level
    contextual citations in the answer itself.
B3  no dangling "see the policy section" pointer — citations are
    rendered in the body; the zero-citation contract line stays.

All fixtures are GENERIC (no real case/finding data).
"""
import pytest

from app.agent_loop import _gsp_render_output_section
from app.goal_postconditions import evaluate_output
from app.goal_resolution import (
    EvidenceType,
    OutputSatisfaction,
    RequiredOutput,
)


# --- generic fixtures ------------------------------------------------------

def _events(n, finding="F9"):
    return [{"event_id": f"{finding}-E{i:03d}",
             "timestamp": f"2026-01-01T00:{i:02d}:00Z",
             "summary": f"event {i} — generic activity"}
            for i in range(1, n + 1)]


def _timeline_output(subject="F9", extra_reqs=()):
    return RequiredOutput(
        output_id="O1", claim="show the finding timeline",
        claim_kind="retrieval", subject_ref=subject,
        scope="finding_level",
        evidence_requirements=[EvidenceType.TIMELINE_EVENTS,
                               *extra_reqs],
        satisfaction="satisfied")


def _timeline_summary(n, finding="F9", truncated=False, total=None):
    ev = _events(n, finding)
    return {
        "finding_id": finding,
        "total_events": total if total is not None else n,
        "event_ids": [e["event_id"] for e in ev],
        "event_summaries": ev,
        "truncated": truncated,
    }


def _event_lines(lines):
    import re
    return [l for l in lines if re.match(r"^- [FU]\d+-E\d+ \|", l)]


def _timeline_obs(finding="F9", n=11, total=None, truncated=False):
    ev = _events(n, finding)
    return {
        "step_type": "inspect_timeline", "tool_name": "finding_drilldown",
        "outcome": "success", "semantic_status": "timeline",
        "for_outputs": ["O1"],
        "actual_evidence_types": [EvidenceType.TIMELINE_EVENTS],
        "summary": {
            "finding_id": finding,
            "total_events": total if total is not None else n,
            "event_ids": [e["event_id"] for e in ev],
            "event_summaries": ev,
            "truncated": truncated,
        },
    }


# --- A1: timeline section renders the complete set -------------------------

@pytest.mark.parametrize("n", [4, 8, 11, 12])
def test_a1_complete_timeline_renders_every_event(n):
    out = _timeline_output()
    lines = _gsp_render_output_section(
        out, _timeline_summary(n), lang="zh")
    rendered = _event_lines(lines)
    assert len(rendered) == n
    assert [l.split(" | ")[0].lstrip("- ") for l in rendered] == \
        [f"F9-E{i:03d}" for i in range(1, n + 1)]
    # no false disclosure when everything is rendered
    assert not any("显示前" in l or "showing the first" in l
                   for l in lines)


def test_a1_explicit_subset_disclosed_with_actual_counts():
    out = _timeline_output()
    # user asked for the first 3 of 11 — event_summaries holds the subset
    s = _timeline_summary(11)
    s["event_summaries"] = s["event_summaries"][:3]
    s["event_ids"] = s["event_ids"][:3]
    s["truncated"] = True
    lines = _gsp_render_output_section(out, s, lang="zh")
    assert len(_event_lines(lines)) == 3
    disc = [l for l in lines if "显示前" in l or "showing the first" in l]
    assert disc, lines
    assert "3" in disc[0] and "11" in disc[0]
    en = _gsp_render_output_section(out, s, lang="en")
    en_disc = [l for l in en if "showing the first" in l]
    assert en_disc and "3" in en_disc[0] and "11" in en_disc[0]


def test_a1_unrelated_events_never_invented_or_mixed():
    """The section renders exactly the projected (subject-filtered) set —
    nothing from other findings is added by the composer."""
    out = _timeline_output(subject="F9")
    s = _timeline_summary(5, finding="F9")
    lines = _gsp_render_output_section(out, s, lang="en")
    rendered = _event_lines(lines)
    assert all(l.startswith("- F9-E") for l in rendered)
    assert len(rendered) == 5


# --- A2: compound timeline + records output --------------------------------

def _records_summary(n_records=10):
    return {
        "record_count": n_records,
        "record_rows": [{"record_id": f"R{i:03d}",
                         "timestamp": f"2026-02-01T00:{i:02d}:00Z",
                         "summary": "generic record",
                         "address": None}
                        for i in range(1, n_records + 1)],
        "view": "evidence",
    }


def _compound_obs():
    return [
        _timeline_obs(),
        {"step_type": "inspect_withdrawals",
         "tool_name": "finding_drilldown", "outcome": "success",
         "for_outputs": ["O1"],
         "actual_evidence_types": [EvidenceType.WITHDRAWAL_RECORDS],
         "summary": _records_summary()},
    ]


def test_a2_compound_output_renders_timeline_and_records():
    out = _timeline_output(extra_reqs=(EvidenceType.WITHDRAWAL_RECORDS,))
    from app.goal_postconditions import _combine_summaries
    combined, _ = _combine_summaries(_compound_obs())
    # the real composition path always sets the actual-evidence grounding
    combined["_actual_evidence_types"] = [EvidenceType.WITHDRAWAL_RECORDS]
    combined["_outcome"] = "success"
    lines = _gsp_render_output_section(out, combined, lang="en")
    # the timeline section must NOT vanish behind the records branch
    assert len(_event_lines(lines)) == 11
    # and the records section still renders (L1 behavior unchanged)
    assert any("Complete withdrawal records" in l for l in lines), lines
    assert sum(1 for l in lines if l.startswith("- R")) == 10


def test_a2_records_only_output_unchanged():
    out = RequiredOutput(
        output_id="O1", claim="list withdrawals", claim_kind="retrieval",
        subject_ref="U1", scope="case_level",
        evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
        satisfaction="satisfied")
    from app.goal_postconditions import _combine_summaries
    combined, _ = _combine_summaries(
        [{"step_type": "inspect_withdrawals", "outcome": "success",
          "summary": _records_summary(3)}])
    combined["_outcome"] = "success"
    lines = _gsp_render_output_section(out, combined, lang="en")
    assert not _event_lines(lines)          # no timeline invention
    assert sum(1 for l in lines if l.startswith("- R")) == 3


# --- A3: completion state consistent with the emitted set -----------------

def test_a3_full_set_satisfied():
    out = _timeline_output()
    r = evaluate_output(out, "case-x", [_timeline_obs(n=11)])
    assert r.satisfaction == OutputSatisfaction.SATISFIED


def test_a3_total_shortfall_is_partial_never_silent_satisfied():
    """11 events projected against a declared total of 12 → non-complete
    (the _MAX_IDS-bounded event_ids list must not mask this)."""
    obs = _timeline_obs(n=11, total=12)
    obs["summary"]["event_ids"] = [e["event_id"]
                                   for e in obs["summary"]["event_summaries"]]
    out = _timeline_output()
    r = evaluate_output(out, "case-x", [obs])
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


def test_a3_max_ids_cap_does_not_break_completeness():
    """event_ids capped at 10 (_MAX_IDS) while 11 events are fully
    projected with total_events=11 → still SATISFIED (the metadata cap
    never downgrades a complete delivery)."""
    obs = _timeline_obs(n=11, total=11)
    obs["summary"]["event_ids"] = obs["summary"]["event_ids"][:10]
    out = _timeline_output()
    r = evaluate_output(out, "case-x", [obs])
    assert r.satisfaction == OutputSatisfaction.SATISFIED


def test_a3_explicit_subset_stays_partial():
    obs = _timeline_obs(n=3, total=11, truncated=True)
    out = _timeline_output()
    r = evaluate_output(out, "case-x", [obs])
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


# --- B: policy applicability rendering -------------------------------------

_CITE = {"citation_id": 2,
         "document": "AML_Policy.md",
         "section": "2.1 Velocity",
         "snippet": "A sudden spike in transfers may indicate mule activity."}


def _policy_output():
    return RequiredOutput(
        output_id="O1", claim="policy citations for the finding",
        claim_kind="retrieval", subject_ref="F9", scope="finding_level",
        evidence_requirements=[EvidenceType.POLICY_REFERENCES],
        satisfaction="satisfied")


def test_b1_finding_level_en_says_directly_apply():
    s = {"finding_id": "F9", "match_count": 1, "associated_count": 1,
         "associated_citations": [_CITE],
         "associated_policy_refs": [{"citation_id": 2}],
         "matches": [_CITE], "finding_policy_status": "associated"}
    en = _gsp_render_output_section(_policy_output(), s, lang="en")
    joined = "\n".join(en)
    assert "directly" in joined and "finding-level" in joined
    assert "case-level policy citations" not in joined
    assert "Excerpt [2]:" in joined


def test_b1_finding_level_zh_face_unchanged():
    s = {"finding_id": "F9", "match_count": 1, "associated_count": 1,
         "associated_citations": [_CITE],
         "associated_policy_refs": [{"citation_id": 2}],
         "matches": [_CITE], "finding_policy_status": "associated"}
    zh = _gsp_render_output_section(_policy_output(), s, lang="zh")
    joined = "\n".join(zh)
    assert "该发现直接适用 1 条政策引用" in joined
    assert "原文摘录 [2]:" in joined
    assert "Excerpt" not in joined


def test_b2_no_basis_note_reachable_and_citations_visible_en():
    """assoc empty + case-level matches → the note renders AND the
    case-level citations are visible (previously a bare pointer)."""
    s = {"finding_id": "F9", "match_count": 4, "associated_count": 0,
         "associated_citations": [],
         "associated_policy_refs": [],
         "matches": [dict(_CITE), dict(_CITE, citation_id=3),
                     dict(_CITE, citation_id=1), dict(_CITE, citation_id=4)],
         "finding_policy_status": "no_finding_level_basis"}
    en = _gsp_render_output_section(_policy_output(), s, lang="en")
    joined = "\n".join(en)
    assert "insufficient to confirm" in joined
    assert "case-level context" in joined
    assert "not finding-level requirements" in joined
    assert joined.count("Excerpt [") == 3          # bounded rendering
    assert "(4 case-level citations were retrieved in total" in joined
    assert "directly apply" not in joined.split("insufficient")[0]
    assert "see the policy section" not in joined  # no dangling pointer


def test_b2_no_basis_note_reachable_and_citations_visible_zh():
    s = {"finding_id": "F9", "match_count": 4, "associated_count": 0,
         "associated_citations": [],
         "associated_policy_refs": [],
         "matches": [dict(_CITE), dict(_CITE, citation_id=3),
                     dict(_CITE, citation_id=1)],
         "finding_policy_status": "no_finding_level_basis"}
    zh = _gsp_render_output_section(_policy_output(), s, lang="zh")
    joined = "\n".join(zh)
    assert "不足以确认任何引用直接适用" in joined
    assert "案件级背景参考" in joined
    assert "不代表该发现的特定要求" in joined
    assert joined.count("原文摘录 [") == 3
    assert "该发现直接适用" not in joined


def test_b3_zero_citations_contract_line_unchanged():
    s = {"finding_id": "F9", "match_count": 0, "matches": [],
         "associated_citations": [], "associated_policy_refs": [],
         "finding_policy_status": "no_match"}
    zh = _gsp_render_output_section(_policy_output(), s, lang="zh")
    assert any("未检索到适用于该发现的政策引用" in l for l in zh)
    en = _gsp_render_output_section(_policy_output(), s, lang="en")
    assert any("No directly applicable policy references" in l
               for l in en)


def test_b4_zero_relevance_candidates_stay_case_level():
    """A zero-relevance match in the case set renders as case-level
    context only — the composer never marks it finding-level."""
    s = {"finding_id": "F9", "match_count": 1, "associated_count": 0,
         "associated_citations": [],
         "associated_policy_refs": [],
         "matches": [dict(_CITE, citation_id=9)],
         "finding_policy_status": "no_finding_level_basis"}
    for lang in ("zh", "en"):
        out = _gsp_render_output_section(_policy_output(), s, lang)
        joined = "\n".join(out)
        assert "case-level" in joined or "案件级" in joined
        assert "该发现直接适用" not in joined
        assert "directly apply to this finding" not in joined
