"""U00010 F2/F3 (2026-10-21 phase-1) — citation ORIGINAL-TEXT ask.

The real E2E run (docs/evaluations/u00010-triage-e2e-20261021T122055Z.json,
F2_1) answered 「把与关联账号相关的引文原文发给我」 with the shared-IP
negative line + the 18-account network attribution: the accepted contract
carried ONLY a network_relations output, so no policy leg could ever be
planned. The journey contract (triage §7 F2/F3): citation asks → policy
citation document/section/original text (or a grounded bounded statement);
must NOT be answered by network attribution alone.

Repairs pinned here:
- deterministic candidate: a citation-original-text ask emits the
  policy_citation_original_text output (policy_references);
- guard: in a citation ask the network-attribution retrieval output is
  dropped even though 关联账号 appears (the population phrase scopes the
  citations, it never demands the list);
- anchoring vocabulary: the population phrase resolves to THIS case's
  linked-account finding (title-element governance).
"""
from app.goal_resolution import (
    _apply_output_guards,
    deterministic_contract_proposal,
)
from app.investigation_concepts import (
    is_policy_citation_original_text_request,
)

F2_ASK = "把与关联账号相关的引文原文发给我"
F3_ASK = "我要的是与关联账号相关的政策引文，把该条政策原文发我"
EN_ASK = ("send me the original policy citations related to the linked "
          "accounts")


def test_citation_ask_recognized_zh_and_en():
    assert is_policy_citation_original_text_request(F2_ASK)
    assert is_policy_citation_original_text_request(F3_ASK)
    assert is_policy_citation_original_text_request(EN_ASK)
    # non-citation asks stay unrecognized
    assert not is_policy_citation_original_text_request(
        "列出所有关联账号")
    assert not is_policy_citation_original_text_request(
        "查询关联账号中是否存在对冲交易")


def test_candidate_emits_policy_citation_not_network():
    for ask in (F2_ASK, F3_ASK):
        proposal = deterministic_contract_proposal(ask, None, "U00010")
        assert proposal is not None, ask
        outs = proposal["required_outputs"]
        evidence = [e for o in outs for e in o["evidence_requirements"]]
        assert "policy_references" in evidence, (ask, evidence)
        # the candidate is a PRIOR — the network output it also carries is
        # dropped by the deterministic guards on the final set: the
        # guaranteed contract for a citation ask is citations, not the list
        guarded = _apply_output_guards(ask, outs)
        g_evidence = [e for o in guarded
                      for e in o["evidence_requirements"]]
        assert "policy_references" in g_evidence, (ask, g_evidence)
        assert "network_relations" not in g_evidence, (ask, g_evidence)


def test_guard_drops_network_output_in_citation_ask():
    # the LLM over-fused the ask into a network-attribution deliverable
    # (the live F2 failure): the guard must drop it for a citation ask
    llm_outputs = [{
        "claim": "关联账号网络归因明细",
        "claim_kind": "retrieval",
        "subject_ref": "U00010",
        "scope": "case_level",
        "evidence_requirements": ["network_relations"],
    }]
    kept = _apply_output_guards(F2_ASK, llm_outputs)
    assert kept == [], kept
    # the same output survives when the request is a genuine list ask
    kept2 = _apply_output_guards("列出所有关联账号", llm_outputs)
    assert len(kept2) == 1, kept2


def test_guard_reapplies_after_candidate_merge():
    """THE live F2 failure (TASK-cc857241f124): the guards ran BEFORE the
    candidate merge, so the merge re-admitted the candidate's network
    output and the contract carried BOTH the citation AND the 18-account
    network leg. Guards must re-apply to the FINAL merged set."""
    from app.goal_resolution import _merge_with_candidate
    candidate = {"required_outputs": [
        {"claim": "政策引文及原文摘录", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["policy_references"]},
        {"claim": "案件的关联账号网络归因明细（Risk Platform 网络归因）",
         "claim_kind": "retrieval", "subject_ref": "U00010",
         "scope": "case_level",
         "evidence_requirements": ["network_relations"]},
    ]}
    # the LLM reviewed and kept only the citation deliverable
    llm_outputs = [candidate["required_outputs"][0]]
    merged = _merge_with_candidate(llm_outputs, candidate)
    assert len(merged) == 2, merged          # network re-admitted by merge
    final = _apply_output_guards(F2_ASK, merged)
    evidence = [e for o in final
                for e in o["evidence_requirements"]]
    assert "policy_references" in evidence, evidence
    assert "network_relations" not in evidence, evidence


def test_population_phrase_anchors_linked_account_finding():
    from types import SimpleNamespace
    from app.planner_v2 import _output_target_finding

    class _F:
        finding_id = "F6"
        title = "Linked Account Network"

    findings = [_F(), SimpleNamespace(finding_id="F3",
                                      title="High withdrawal frequency")]
    output = SimpleNamespace(
        claim="账户 U00010 中与关联账号相关的政策引文，含每条引文的"
              "文件/章节及其原文摘录",
        subject_ref="U00010",
        evidence_requirements=[])
    fnd = _output_target_finding(output, F2_ASK, findings)
    assert fnd is not None and fnd.finding_id == "F6", fnd
