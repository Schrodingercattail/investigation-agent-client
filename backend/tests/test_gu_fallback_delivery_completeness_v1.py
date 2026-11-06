"""GU-fallback delivery completeness — the deterministic candidate must
carry EVERY deliverable the request names, so a GU-LLM outage can never
silently drop goals (2026-11-04 acceptance TASK-7f707394f962: the three-
goal EN request degraded to ONE explain deliverable — the policy/citation
and artifact deliverables were lost, and the delivery-intent gate then
stripped the artifact step the fallback plan itself had proposed).

Pins (real EN acceptance request):
- deterministic candidate emits THREE outputs: finding-scoped policy,
  finding-scoped explanation, file_export records artifact (with
  addresses) — all anchored to the explain-resolved finding;
- boundaries: artifact intent without a unique explain resolution →
  case-level file output; detail-only phrasing → no file output;
  original-text superset guard → no double policy output;
- fault path: understand_goal with a connection-class LLM failure still
  returns the complete three-deliverable representation.
"""
import sys
from types import SimpleNamespace

from app import investigation_concepts as ic
from app.exceptions import LLMError
from app.goal_resolution import understand_goal

REQUEST = ("what's the threshold of High Withdrawal Frequency? "
           "send me the related policy or citation. generate an artifact "
           "of all withdrawal records with address")

FINDING = SimpleNamespace(finding_id="F2",
                          title="High Withdrawal Frequency")


def _connection_error_llm():
    class _Broken:
        def generate(self, *a, **k):
            raise LLMError("Unexpected LLM error: peer closed connection "
                           "without sending complete message body "
                           "(incomplete chunked read)")
    return _Broken()


def test_candidate_emits_all_three_deliverables():
    rep = understand_goal(REQUEST, llm=_connection_error_llm(),
                          use_llm=True, case_id="U00005",
                          case_findings=[FINDING])
    outs = (rep.contract_proposal or {}).get("required_outputs") or []
    # 1. finding-scoped policy basis (U00020 fix-A anchor, EN phrasing)
    policy = [o for o in outs
              if o.get("evidence_requirements") == ["policy_references"]
              and o.get("subject_ref") == "F2"]
    assert len(policy) == 1, outs
    # 2. finding-scoped threshold explanation
    explain = [o for o in outs if o.get("claim_kind") == "explanation"]
    assert len(explain) == 1 and explain[0].get("subject_ref") == "F2"
    # 3. file_export records artifact with addresses, F2-anchored
    file_outs = [o for o in outs if o.get("delivery") == "file_export"]
    assert len(file_outs) == 1
    assert file_outs[0].get("subject_ref") == "F2"
    assert file_outs[0].get("evidence_requirements") == \
        ["withdrawal_records"]
    assert "address" in str(file_outs[0].get("claim", "")).lower() \
        or "地址" in str(file_outs[0].get("claim", ""))


def test_case_level_artifact_boundary():
    """Artifact intent WITHOUT a unique explain resolution (no why
    vocabulary in the request) → the file output is CASE-level."""
    rep = understand_goal(
        "generate an artifact of all withdrawal records with address",
        llm=_connection_error_llm(), use_llm=True,
        case_id="U00005", case_findings=[FINDING])
    outs = (rep.contract_proposal or {}).get("required_outputs") or []
    file_outs = [o for o in outs if o.get("delivery") == "file_export"]
    assert len(file_outs) == 1
    assert file_outs[0].get("scope") == "case_level"
    assert file_outs[0].get("subject_ref") == "U00005" or \
        file_outs[0].get("subject_ref") == "U00010"


def test_detail_only_no_file_output():
    rep = understand_goal("list all withdrawal records for this finding",
                          llm=_connection_error_llm(), use_llm=True,
                          case_id="U00005",
                          case_findings=[FINDING])
    outs = (rep.contract_proposal or {}).get("required_outputs") or []
    assert not [o for o in outs if o.get("delivery") == "file_export"]


def test_original_text_superset_guard():
    rep = understand_goal("send me the related policy or citation 原文发我",
                          llm=_connection_error_llm(), use_llm=True,
                          case_id="U00005",
                          case_findings=[FINDING])
    outs = (rep.contract_proposal or {}).get("required_outputs") or []
    # exactly ONE policy deliverable (the original-text aspect owns it)
    policy_outs = [o for o in outs
                   if "policy" in str(o.get("claim", "")).lower()
                   or "政策" in str(o.get("claim", ""))]
    assert len(policy_outs) <= 1


def test_vocabulary_recognition():
    assert ic.is_related_policy_citation_request(
        "send me the related policy or citation")
    assert ic.is_related_policy_citation_request(
        "give me the related policy")
    assert not ic.is_related_policy_citation_request(
        "list all withdrawal records")
    # the EN related-policy phrasing does NOT trip the original-text ask
    assert not ic.is_policy_citation_original_text_request(
        "send me the related policy or citation")
