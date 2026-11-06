"""F1 candidate-merge delivery-channel isolation + F2 GU channel-split
rule (2026-11-05 U00043 dual-delivery fix).

Pins (approved design B = F1+F2):
- same delivery + same signature → existing dedup/normalize unchanged;
- different delivery + same signature → P2 never replaces, P3 never
  covers — both channels survive as two outputs;
- detail_list + file_export → two independent outputs;
- missing delivery ≡ explicit detail_list;
- subject isolation and claim guards unchanged;
- AD-3: same content + different delivery → two outputs; same delivery →
  one;
- GU zh/en dual-delivery requests → detail_list + file_export;
- GU file-only → single file_export; GU detail-only → single
  detail_list.
"""
import json

from app.goal_resolution import (
    GoalContract,
    GoalRepresentation,
    GoalResolutionStatus,
    RequiredOutput,
    ScopeLevel,
    _dedup_canonical_outputs,
    _merge_with_candidate,
    build_goal_contract,
    understand_goal,
)

DETAIL = {"claim": "withdrawal details for this account (with addresses)",
          "claim_kind": "retrieval", "subject_ref": "U00043",
          "scope": "case_level",
          "evidence_requirements": ["withdrawal_records"]}
FILE = {"claim": "withdrawal records file for this account (with addresses)",
        "claim_kind": "retrieval", "subject_ref": "U00043",
        "scope": "case_level",
        "evidence_requirements": ["withdrawal_records"],
        "delivery": "file_export"}


# --- 1. same delivery, same signature: existing dedup unchanged -----------
def test_same_delivery_same_signature_dedup_unchanged():
    llm = [dict(DETAIL)]
    cand = {"required_outputs": [dict(DETAIL)]}
    merged = _merge_with_candidate(llm, cand)
    assert len(merged) == 1
    assert merged[0]["claim"] == DETAIL["claim"]


# --- 2-4. different delivery: never replaced, never covered ---------------
def test_different_delivery_p2_never_replaces():
    llm = [dict(DETAIL)]                                   # detail_list
    cand = {"required_outputs": [dict(FILE)]}              # file_export
    merged = _merge_with_candidate(llm, cand)
    deliveries = sorted(o.get("delivery") or "detail_list"
                        for o in merged)
    assert deliveries == ["detail_list", "file_export"]
    assert any(o["claim"] == DETAIL["claim"] for o in merged), \
        "the detail_list dict must survive verbatim"
    assert any(o["claim"] == FILE["claim"] for o in merged), \
        "the file_export dict must survive verbatim"


def test_different_delivery_p3_never_covers():
    # P3 coverage: the covering (LLM) file_export output must NOT absorb
    # a detail_list candidate — both channels are distinct deliverables.
    llm = [dict(FILE)]                                     # file_export
    cand = {"required_outputs": [dict(DETAIL)]}            # detail_list
    merged = _merge_with_candidate(llm, cand)
    assert len(merged) == 2
    assert any((o.get("delivery") or "detail_list") == "detail_list"
               for o in merged)
    assert any(o.get("delivery") == "file_export" for o in merged)


def test_detail_and_file_both_retained():
    llm = [dict(DETAIL), dict(FILE)]
    cand = {"required_outputs": [dict(FILE)]}
    merged = _merge_with_candidate(llm, cand)
    assert len(merged) == 2
    assert sum(1 for o in merged
               if (o.get("delivery") or "detail_list") == "detail_list") == 1
    assert sum(1 for o in merged
               if o.get("delivery") == "file_export") == 1


# --- 5. missing delivery ≡ explicit detail_list ---------------------------
def test_missing_delivery_equals_detail_list():
    no_key = {k: v for k, v in DETAIL.items() if k != "delivery"}
    explicit = dict(DETAIL)                                # no delivery key
    explicit["delivery"] = "detail_list"
    llm = [dict(explicit)]
    cand = {"required_outputs": [dict(no_key)]}
    merged = _merge_with_candidate(llm, cand)
    assert len(merged) == 1, "missing delivery must equal detail_list"
    assert merged[0]["delivery"] == "detail_list"


# --- 6. subject isolation unchanged ---------------------------------------
def test_different_subjects_stay_isolated():
    other = dict(DETAIL, subject_ref="U00044")
    llm = [dict(DETAIL)]
    cand = {"required_outputs": [other]}
    merged = _merge_with_candidate(llm, cand)
    assert len(merged) == 2
    subjects = {o["subject_ref"] for o in merged}
    assert subjects == {"U00043", "U00044"}


# --- 7. claim guard unchanged (build_goal_contract overreach strip) -------
def test_claim_overreach_guard_unchanged():
    claim = "send the withdrawal details file"
    repr_obj = GoalRepresentation(
        case_id="U00043", goal_type="expand_evidence",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref="U00043", reason="r", source="llm",
        request_text="generate an artifact of all withdrawal records",
        contract_proposal={"required_outputs": [{
            "claim": claim, "claim_kind": "retrieval",
            "subject_ref": "U00043", "scope": "case_level",
            "evidence_requirements": ["withdrawal_records"],
            "delivery": "file_export"}]})
    contract, reason = build_goal_contract(repr_obj, "U00043")
    assert reason is None, reason
    built_claim = contract.outputs[0].claim
    assert "send" not in built_claim.lower(), \
        "delivery-overreach strip must keep working with delivery present"
    assert contract.outputs[0].delivery == "file_export"


# --- 8-9. AD-3 canonical dedup with the delivery dimension ----------------
def _req_out(delivery, claim="all withdrawal records"):
    return RequiredOutput(
        output_id="O1", claim=claim, claim_kind="retrieval",
        subject_ref="U00043", scope="case_level",
        evidence_requirements=["withdrawal_records"],
        delivery=delivery)


def test_ad3_same_content_different_delivery_keeps_two():
    kept = _dedup_canonical_outputs([_req_out("detail_list"),
                                     _req_out("file_export")])
    assert len(kept) == 2
    assert {o.delivery for o in kept} == {"detail_list", "file_export"}


def test_ad3_same_content_same_delivery_dedups():
    kept = _dedup_canonical_outputs([_req_out("detail_list"),
                                     _req_out("detail_list")])
    assert len(kept) == 1


# --- 10-13. GU channel-split rule through the real pipeline ---------------
def _gu_payload(required_outputs, compound):
    """Payload shapes mirror the REAL GU classifications: a multi-
    deliverable request → COMPOUND with goal_type null and ≥2 outputs;
    a single-deliverable request → INFERRED + expand_evidence (the
    U00043 real-run shape). Returns the DICT — _ScriptedGU.generate
    performs the single json.dumps."""
    return {
        "goal_type": None if compound else "expand_evidence",
        "goal_resolution": "COMPOUND" if compound else "INFERRED",
        "reason": "request",
        "required_outputs": required_outputs,
    }


class _ScriptedGU:
    def __init__(self, payload):
        self.payload = payload

    def generate(self, *a, **k):
        return json.dumps(self.payload)


def _contract_deliveries(text, required_outputs, compound):
    from app.config import settings
    llm = _ScriptedGU(_gu_payload(required_outputs, compound))
    previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
    settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
    try:
        rep = understand_goal(text, case_id="U00043", llm=llm,
                              use_llm=True)
        contract, reason = build_goal_contract(rep, "U00043")
    finally:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
    assert reason is None, reason
    assert rep.proposal_source == "llm", rep.proposal_source
    return [(o.delivery, o.subject_ref,
             tuple(sorted(str(e) for e in o.evidence_requirements)))
            for o in contract.outputs]


ZH_DUAL = "请把此账号的带有提现地址的提现明细发我，并生成一份文件"
EN_DUAL = ("what's the threshold of High Withdrawal Frequency? "
           "send me the withdrawal details with address. generate an "
           "artifact of all withdrawal records")


def test_gu_zh_dual_delivery_two_outputs():
    llm_outs = [
        {"claim": "此账号的提现明细（含地址）", "claim_kind": "retrieval",
         "claim_description": "列出该账号的全部提现明细",
         "subject_ref": "U00043", "scope": "case_level",
         "evidence_requirements": ["withdrawal_records"],
         "delivery": "detail_list"},
        {"claim": "此账号的提现记录文件（含地址）", "claim_kind": "retrieval",
         "claim_description": "生成全部提现记录文件",
         "subject_ref": "U00043", "scope": "case_level",
         "evidence_requirements": ["withdrawal_records"],
         "delivery": "file_export"},
    ]
    got = _contract_deliveries(ZH_DUAL, llm_outs, compound=True)
    deliveries = [g[0] for g in got]
    assert deliveries == ["detail_list", "file_export"], got
    assert all(g[1] == "U00043" for g in got)


def test_gu_en_dual_delivery_two_outputs():
    llm_outs = [
        {"claim": "withdrawal details for this account", "claim_kind":
         "retrieval", "claim_description": "list the withdrawal details",
         "subject_ref": "U00043", "scope": "case_level",
         "evidence_requirements": ["withdrawal_records"],
         "delivery": "detail_list"},
        {"claim": "withdrawal records file", "claim_kind": "retrieval",
         "claim_description": "generate the withdrawal records file",
         "subject_ref": "U00043", "scope": "case_level",
         "evidence_requirements": ["withdrawal_records"],
         "delivery": "file_export"},
    ]
    got = _contract_deliveries(EN_DUAL, llm_outs, compound=True)
    assert [g[0] for g in got] == ["detail_list", "file_export"]


def test_gu_file_only_single_file_export():
    llm_outs = [
        {"claim": "withdrawal records file for this account",
         "claim_kind": "retrieval",
         "claim_description": "generate the withdrawal records file",
         "subject_ref": "U00043", "scope": "case_level",
         "evidence_requirements": ["withdrawal_records"],
         "delivery": "file_export"},
    ]
    got = _contract_deliveries(
        "请把此账号的提现记录生成一份文件", llm_outs,
        compound=False)
    assert len(got) == 1 and got[0][0] == "file_export"


def test_gu_detail_only_single_detail_list():
    llm_outs = [
        {"claim": "withdrawal details for this account", "claim_kind":
         "retrieval", "claim_description": "list the withdrawal details",
         "subject_ref": "U00043", "scope": "case_level",
         "evidence_requirements": ["withdrawal_records"]},
    ]
    got = _contract_deliveries(
        "请把此账号的提现明细发我", llm_outs,
        compound=False)
    assert len(got) == 1 and got[0][0] == "detail_list"
