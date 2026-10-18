"""AL-2.8 — the M2 semantic foundation as conversational finding resolution.

The architecture decision under test (docs/qa/
2026-09-30-al2-8-finding-level-semantic-resolution-v1.md):

    User NL → SemanticNormalizer (LLM proposal, registry-conformed,
    verbatim-grounded) → deterministic grounding over governed candidate
    metadata of the CURRENT case → UNIQUE / AMBIGUOUS / UNKNOWN → target

Boundaries encoded here:

- Resolution ≠ authorization: grounding works regardless of metadata
  status (UNVERIFIED/INCOMPLETE stay conversationally reachable);
  only DEPRECATED is excluded (Metadata Contract §24). Safe-Select
  eligibility is a different decision (WI-5 gate) and is NOT touched.
- Provider outage after deterministic stages failed =
  SEMANTIC_RESOLUTION_UNAVAILABLE — an honest degraded state, distinct
  from unresolved_reference, never replaced by a keyword agent.
- No governed concept → honest no-match fall-through (legacy chain,
  zero tool execution for unknown findings).

Boundary map per test:
  LEVEL 1 (resolver unit): real repo seed data + scripted normalizer LLM.
  LEVEL 2 (API E2E): real HTTP /turn + real TaskStoreV2 + repo seed +
    scripted normalizer/planner LLMs + real tools with the RP boundary
    controlled (repo E2E convention).
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.candidate_metadata import TaskStoreCatalogProvider
from app.config import settings
from app.context_resolution import (
    SEMANTIC_RESOLUTION_UNAVAILABLE_MESSAGE,
    ContextResolver,
    ResolutionStatus,
)
from app.investigation_service import InvestigationService
from app.metadata_seeding import DEFAULT_SEED_FILE, seed_from_document
from app.models import Finding, FindingCapability, InvestigationContext
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

import tests.test_risk_platform_adapter_v2 as fx
from tests.test_api_investigations_v1 import ScriptedLLM, rp_patches

REPO_SEED = Path(__file__).resolve().parents[1] / DEFAULT_SEED_FILE

_CAPS = FindingCapability.model_validate(["timeline", "signal_explain"])

TITLES = {
    "F3": "High withdrawal frequency.",
    "F4": "First withdrawal to new address.",
    "F5": "Shared Device Relationships.",
    "F6": "Linked Account Network.",
    "F7": "High Trading Frequency.",
}


def _findings(ids=("F3", "F4", "F5", "F6")):
    return [
        Finding(finding_id=fid, case_id="U00010", type="rule_signal",
                title=TITLES[fid], severity="high",
                summary=f"summary of {TITLES[fid]}",
                evidence_refs=[], signal_refs=[], policy_refs=[],
                capabilities=_CAPS.model_copy(deep=True))
        for fid in ids
    ]


def seeded_catalog(store=None):
    """The REAL governed catalog: repo seed v2 (F3/F5/F6/F8 authored
    UNVERIFIED) loaded into a real TaskStoreV2-backed store."""
    store = store or TaskStoreV2(tempfile.mkdtemp() + "/al28_sem.db")
    seed_from_document(store, json.loads(REPO_SEED.read_text("utf-8")))
    return TaskStoreCatalogProvider(store), store


def _norm_script(concept_id, fragment, confidence=0.9):
    return json.dumps({"concept_id": concept_id,
                       "matched_fragment": fragment,
                       "confidence": confidence})


def _resolver(script=None, catalog=None, findings_ids=("F3", "F4", "F5", "F6")):
    return ContextResolver(
        llm_provider=script, llm_assist=False, semantic_catalog=catalog
    ), _findings(findings_ids)


def _resolve(script=None, catalog=None, text="shared device",
             focused=None, findings_ids=("F3", "F4", "F5", "F6")):
    resolver, findings = _resolver(script, catalog, findings_ids)
    ctx = InvestigationContext(case_id="U00010",
                               focused_finding_id=focused,
                               focus_source=None)
    return resolver.resolve(text, ctx, findings=findings)


# --- LEVEL 1: semantic grounding unit tests -------------------------------------

def test_shared_device_paraphrase_grounds_f5_via_m2():
    """Paraphrase OUTSIDE the alias lexicon: NL → governed concept → F5.
    Resolution provenance names concept, fragment, and metadata status."""
    catalog, _ = seeded_catalog()
    out = _resolve(ScriptedLLM([_norm_script(
        "SHARED_DEVICE_RELATIONSHIP", "设备共享")]),
        catalog, "为什么会标记出设备共享的关系")
    assert out.status == ResolutionStatus.RESOLVED
    assert out.updated_context.focused_finding_id == "F5"
    assert out.focus_source.value == "agent_resolved"
    assert out.resolution_kind == "inferred"
    ev = out.semantic_evidence
    assert ev["concept_id"] == "SHARED_DEVICE_RELATIONSHIP"
    assert ev["matched_fragment"] == "设备共享"
    assert ev["grounded_candidate_ids"] == ["F5"]
    assert "UNVERIFIED" in out.resolution_reason    # status = provenance


def test_unverified_status_does_not_block_reachability_f3():
    """THE boundary: F3 metadata is UNVERIFIED — conversationally
    reachable through the M2 foundation; Safe-Select stays closed."""
    catalog, _ = seeded_catalog()
    out = _resolve(ScriptedLLM([_norm_script(
        "HIGH_WITHDRAWAL_FREQUENCY", "提现频繁")]),
        catalog, "给我看一下提现频繁的具体证据")
    assert out.status == ResolutionStatus.RESOLVED
    assert out.updated_context.focused_finding_id == "F3"
    rec = catalog.get_candidate("F3")
    assert rec.status == "UNVERIFIED"               # unchanged governance


def test_mechanism_concept_does_not_ground_the_competitor():
    """F6 records SHARED_DEVICE_RELATIONSHIP as a MECHANISM, not core —
    the device concept grounds F5 alone (the F5/F6 separability)."""
    catalog, _ = seeded_catalog()
    out = _resolve(ScriptedLLM([_norm_script(
        "SHARED_DEVICE_RELATIONSHIP", "设备共享")]),
        catalog, "设备共享的关系怎么样")
    assert out.status == ResolutionStatus.RESOLVED
    assert out.updated_context.focused_finding_id == "F5"
    assert out.semantic_evidence["grounded_candidate_ids"] == ["F5"]


def test_shared_core_concept_is_ambiguous_not_guessed():
    catalog, _ = seeded_catalog()
    provider = TaskStoreCatalogProvider(TaskStoreV2(
        tempfile.mkdtemp() + "/al28_amb.db"))
    from app.candidate_metadata import CandidateMetadataRecord, ConceptRecord
    provider.upsert_concept(ConceptRecord(
        concept_id="ACCOUNT_LINK", definition="Any account linkage.",
        created_by="al28-test"))
    for cid in ("F3", "F5"):
        provider.upsert_candidate(CandidateMetadataRecord(
            candidate_id=cid, candidate_type="finding",
            # candidate semantics are CASE-SCOPED: these belong to the
            # same case the resolution context runs in (single-case
            # isolation invariant B)
            case_scope="U00010",
            core_concepts=["ACCOUNT_LINK"],
            distinguishing_concepts=["ACCOUNT_LINK"],
            potential_confusions=[], status="UNVERIFIED",
            metadata_version=1))
    out = _resolve(ScriptedLLM([_norm_script(
        "ACCOUNT_LINK", "账户纽带")]), provider, "给我讲讲这种账户纽带的情况")
    assert out.status == ResolutionStatus.AMBIGUOUS
    assert {c.id for c in out.candidates} == {"F3", "F5"}
    assert out.updated_context.focused_finding_id is None   # no mutation


def test_deprecated_record_is_excluded_from_grounding():
    catalog, _ = seeded_catalog()
    catalog.update_status("F5", "DEPRECATED")
    out = _resolve(ScriptedLLM([_norm_script(
        "SHARED_DEVICE_RELATIONSHIP", "设备共享")]),
        catalog, "为什么会标记出设备共享的关系")
    assert out.status == ResolutionStatus.UNRESOLVED   # honest no-match


def test_llm_unavailable_degrades_to_semantic_unavailable():
    """The mandated honest outage: provider down + no deterministic
    match → SEMANTIC_RESOLUTION_UNAVAILABLE with the governed user
    message — NOT unresolved_reference, NOT a keyword fallback."""
    catalog, _ = seeded_catalog()

    class DownLLM:
        def generate(self, **kw):
            raise RuntimeError("provider down")

    out = _resolve(DownLLM(), catalog, "为什么会标记出设备共享的关系")
    assert out.status == ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE
    assert SEMANTIC_RESOLUTION_UNAVAILABLE_MESSAGE in \
        out.clarification_message
    assert out.semantic_evidence["reason_class"] == "llm_unavailable"
    assert out.updated_context.focused_finding_id is None


def test_malformed_provider_output_is_not_an_outage():
    """AL-2.10 taxonomy: a provider that ANSWERED with unusable output is
    a resolution failure, not an outage — the legacy chain continues
    (here: bounded unresolved), never semantic_resolution_unavailable."""
    catalog, _ = seeded_catalog()
    out = _resolve(ScriptedLLM(["I cannot answer that."]),
                   catalog, "为什么会标记出设备共享的关系")
    assert out.status == ResolutionStatus.UNRESOLVED
    assert out.status != ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE


def test_no_governed_concept_is_an_honest_no_match():
    """Negative scenario A: a finding the case does not have. The
    normalizer proposes nothing → legacy fall-through → unresolved; zero
    tools (no plan exists at resolver level and no target is set)."""
    catalog, _ = seeded_catalog()
    out = _resolve(ScriptedLLM([_norm_script(None, "")]),
                   catalog, "谈谈虚拟币洗钱这个风险项")
    assert out.status == ResolutionStatus.UNRESOLVED
    assert out.updated_context.focused_finding_id is None


def test_invented_concept_is_rejected_and_falls_through():
    catalog, _ = seeded_catalog()
    out = _resolve(ScriptedLLM([_norm_script(
        "MONEY_LAUNDERING_PATTERN", "洗钱")]),
        catalog, "谈谈虚拟币洗钱这个风险项")
    assert out.status == ResolutionStatus.UNRESOLVED


def test_grounding_restricted_to_the_current_case():
    catalog, _ = seeded_catalog()
    out = _resolve(ScriptedLLM([_norm_script(
        "SHARED_DEVICE_RELATIONSHIP", "设备共享")]),
        catalog, "为什么会标记出设备共享的关系",
        findings_ids=("F3", "F4"))              # F5 not in this case
    assert out.status == ResolutionStatus.UNRESOLVED


def test_canonical_phrase_resolves_through_the_single_semantic_entry():
    """AL-2.11: the lexicon is gone — even the canonical phrase flows
    through the ONE finding-semantic entry (SemanticNormalizer → M2
    grounding), with provenance on the outcome."""
    catalog, _ = seeded_catalog()
    script = ScriptedLLM([_norm_script("SHARED_DEVICE_RELATIONSHIP",
                                       "共享设备")])
    out = _resolve(script, catalog, "共享设备为什么是风险项")
    assert out.status == ResolutionStatus.RESOLVED
    assert out.updated_context.focused_finding_id == "F5"
    assert out.used_llm is True
    assert script.calls == 1                    # the single entry consumed
    assert out.semantic_evidence["concept_id"] == \
        "SHARED_DEVICE_RELATIONSHIP"


def test_flag_off_restores_legacy_chain():
    catalog, _ = seeded_catalog()
    script = ScriptedLLM([_norm_script("SHARED_DEVICE_RELATIONSHIP",
                                       "共享设备")])
    from app.config import settings as s
    original = s.ENABLE_SEMANTIC_FINDING_RESOLUTION
    s.ENABLE_SEMANTIC_FINDING_RESOLUTION = False
    try:
        out = _resolve(script, catalog, "为什么会标记出设备共享的关系")
    finally:
        s.ENABLE_SEMANTIC_FINDING_RESOLUTION = original
    assert out.status == ResolutionStatus.UNRESOLVED
    assert script.calls == 0


def test_no_catalog_keeps_legacy_behavior():
    out = _resolve(None, None, "为什么会标记出设备共享的关系")
    assert out.status == ResolutionStatus.UNRESOLVED


# --- LEVEL 2: real HTTP /turn E2E -------------------------------------------------

def _build_client(normalizer_scripts, findings=None):
    store = TaskStoreV2(tempfile.mkdtemp() + "/al28_api.db")
    catalog, _ = seeded_catalog(store)
    findings = findings or _findings()
    resolver = ContextResolver(llm_provider=ScriptedLLM(normalizer_scripts),
                               llm_assist=False,
                               semantic_catalog=catalog)
    service = InvestigationService(
        resolver=resolver,
        planner=PlannerV2(ScriptedLLM(["{}"])),   # deterministic branches
        executor=None, task_store=store)          # plan without the LLM
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda cid: [
                               f.model_copy(deep=True) for f in findings])
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), store


def _turn(client, message):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00010"}).json()
    inv_id = inv["investigation"]["investigation_id"]
    with rp_patches()[0], rp_patches()[1]:
        return client.post(f"/api/v2/investigations/{inv_id}/turn",
                           json={"message": message}).json(), inv_id


def _extended_rp(extra_findings):
    """Extend the CONTROLLED RP universe so the domain tools' internal
    case fetch sees the same candidate universe the resolver grounded
    against (fixture-level consistency; the real-RP path is validated by
    the browser E2E). Compose with rp_patches()."""
    from contextlib import contextmanager
    import importlib
    _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
    _real_fetch = _rcf.risk_case_fetch        # captured BEFORE patching

    @contextmanager
    def _patched():
        with patch("app.domain_tools.risk_case_fetch.risk_case_fetch",
                   new=lambda *a, **kw: _extend(_real_fetch(*a, **kw))):
            yield

    def _extend(result):
        if result.outcome.value == "success" \
                and isinstance(result.data, dict):
            have = {f.finding_id
                    for f in (result.data.get("findings") or [])}
            result.data["findings"] = list(
                result.data.get("findings") or []) + [
                f for f in extra_findings if f.finding_id not in have]
        return result

    return _patched()


def test_api_scenario_a_semantic_explain_finding_full_chain():
    """Raw NL (paraphrase, no lexicon hit) → governed concept → F5 →
    explain_finding → governed deterministic plan (explain_signal +
    retrieve_policy) → REAL tools (RP boundary controlled) → response."""
    client, store = _build_client([
        _norm_script("SHARED_DEVICE_RELATIONSHIP", "设备共享")])
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00010"}).json()
    inv_id = inv["investigation"]["investigation_id"]
    with rp_patches()[0], rp_patches()[1], \
            _extended_rp(_findings(("F5", "F6"))):
        body = client.post(f"/api/v2/investigations/{inv_id}/turn",
                           json={"message": "为什么会标记出设备共享的关系"}
                           ).json()
    assert body["status"] == "completed"
    # semantic grounding evidence is on the turn record
    sem = body["target_resolution"]["semantic_grounding"]
    assert sem["concept_id"] == "SHARED_DEVICE_RELATIONSHIP"
    assert sem["grounded_candidate_ids"] == ["F5"]
    # governed plan: the deterministic explain branch, contract-checked
    plan = body["plan"]
    assert [s["type"] for s in plan["steps"]] == \
        ["explain_signal", "retrieve_policy"]
    assert plan["steps"][0]["tool_name"] == "signal_explain"
    # real tools ran through the executor (outcome-level summary on the
    # turn; normalized views live behind /tasks/{id}/results)
    calls = body["execution"]["tool_calls"]
    assert [c["tool_name"] for c in calls] == ["signal_explain",
                                               "policy_lookup"]
    assert all(c["outcome"] == "success" for c in calls)
    assert body["task"]["status"] == "completed"
    # the response is user-facing language, not a runtime dump
    assert "signal_explain:" not in body["response"]
    # the focused context actually moved to F5
    assert body["context"]["focused_finding_id"] == "F5"


def test_api_scenario_b_semantic_evidence_request_full_chain():
    """Raw NL → HIGH_WITHDRAWAL_FREQUENCY → F3 (UNVERIFIED, reachable) →
    evidence request → inspect_withdrawals → real withdrawal records →
    natural-language response carrying the records."""
    client, store = _build_client([
        _norm_script("HIGH_WITHDRAWAL_FREQUENCY", "提现频繁")])
    body, inv_id = _turn(client, "给我输出一下提现频繁的具体证据")
    assert body["status"] == "completed"
    sem = body["target_resolution"]["semantic_grounding"]
    assert sem["concept_id"] == "HIGH_WITHDRAWAL_FREQUENCY"
    assert sem["grounded_candidate_ids"] == ["F3"]
    plan = body["plan"]
    assert [s["type"] for s in plan["steps"]] == ["inspect_withdrawals"]
    assert plan["steps"][0]["arguments"] == {"view": "evidence",
                                             "stream": "withdrawals"}
    calls = body["execution"]["tool_calls"]
    assert calls and calls[0]["tool_name"] == "finding_drilldown"
    assert calls[0]["outcome"] == "success"
    # the normalized evidence view (task results boundary) carries the
    # actual withdrawal records
    task_id = body["task"]["task_id"]
    results = client.get(f"/api/v2/tasks/{task_id}/results").json()
    views = [r for r in results["results"]
             if r.get("tool_name") == "finding_drilldown"
             and r.get("evidence_records")]
    assert views, "normalized evidence view must exist"
    recs = views[0]["evidence_records"]["records"]
    assert recs and views[0]["evidence_records"]["streams"].get(
        "withdrawals_included") is True
    # the user-facing answer names the records, not the runtime —
    # in the REQUEST's language (zh request → zh response; per-turn
    # response language selection)
    assert ("withdrawal record" in body["response"]
            or "提现记录" in body["response"])
    assert "finding_drilldown:" not in body["response"]
    assert body["context"]["focused_finding_id"] == "F3"


def test_api_llm_unavailable_is_named_as_an_outage():
    """Provider down after deterministic stages failed: the turn ends
    with the honest degraded outcome — no plan, no tool call, no fake
    resolution, distinct task error code."""
    class DownLLM:
        def generate(self, **kw):
            raise RuntimeError("provider down")

    store = TaskStoreV2(tempfile.mkdtemp() + "/al28_down.db")
    catalog, _ = seeded_catalog(store)
    resolver = ContextResolver(llm_provider=DownLLM(), llm_assist=False,
                               semantic_catalog=catalog)
    service = InvestigationService(resolver=resolver,
                                   planner=PlannerV2(ScriptedLLM(["{}"])),
                                   executor=None, task_store=store)
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda cid: _findings())
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    body, _ = _turn(client, "为什么会标记出设备共享的关系")
    assert body["target_resolution"]["kind"] == \
        "semantic_resolution_unavailable"
    assert "语义解析服务当前不可用" in body["response"]
    assert body["plan"] is None
    assert body["execution"] is None            # zero tool execution
    assert body["task"]["error"] == "semantic_resolution_unavailable"


def test_api_nonexistent_finding_stays_unresolved_with_zero_tools():
    client, store = _build_client([_norm_script(None, "")])
    body, _ = _turn(client, "谈谈虚拟币洗钱这个风险项")
    assert body["target_resolution"]["kind"] == "unresolved"
    assert body["plan"] is None
    assert body["execution"] is None
    assert body["task"]["error"] == "unresolved_reference"


# --- LEVEL 2: Agent Loop boundary ---------------------------------------------------

def test_agent_loop_blocks_on_semantic_unavailable():
    """The loop path terminates BLOCKED with its own stop reason — never
    a planning attempt, never completion."""
    from app.agent_loop import AgentLoopRunner, HeuristicDecisionProposer

    class DownLLM:
        def generate(self, **kw):
            raise RuntimeError("provider down")

    store = TaskStoreV2(tempfile.mkdtemp() + "/al28_loop.db")
    catalog, _ = seeded_catalog(store)
    resolver = ContextResolver(llm_provider=DownLLM(), llm_assist=False,
                               semantic_catalog=catalog)
    runner = AgentLoopRunner(store=store, resolver=resolver,
                             proposer=HeuristicDecisionProposer(),
                             findings_provider=lambda: _findings())
    result = runner.run(
        "为什么会标记出设备共享的关系",
        InvestigationContext(case_id="U00010"),
        _findings())
    assert result.state.agent_status == "blocked"
    assert result.state.stop_reason == "semantic_resolution_unavailable"
    assert result.state.plan_steps == []        # never planned
    assert "语义解析服务当前不可用" in result.response


def test_reauthor_semantics_completes_auto_provisioned_placeholder():
    """Seed v2 over a store where Coverage Check had auto-provisioned an
    empty INCOMPLETE F3: the authored entry completes onboarding Step 3;
    a manual INCOMPLETE record with concepts is left untouched."""
    from app.candidate_metadata import CandidateMetadataRecord
    store = TaskStoreV2(tempfile.mkdtemp() + "/al28_reauth.db")
    provider = TaskStoreCatalogProvider(store)
    provider.upsert_candidate(CandidateMetadataRecord(
        candidate_id="F3", candidate_type="finding",
        title_snapshot="High withdrawal frequency.",
        description_snapshot="auto", status="INCOMPLETE",
        metadata_version=0,
        provenance={"auto_provisioned": True}))
    summary = seed_from_document(store, json.loads(
        REPO_SEED.read_text("utf-8")))
    rec = provider.get_candidate("F3")
    assert rec.core_concepts == ["HIGH_WITHDRAWAL_FREQUENCY"]
    assert rec.status == "UNVERIFIED"
    assert rec.metadata_version == 1
    assert rec.provenance["reauthored_from"] == "auto_provisioned_placeholder"
    assert "F3" in summary.candidates_seeded


def test_reauthor_semantics_completes_older_seed_placeholder():
    """A seed-v1 INCOMPLETE declaration (insufficiency_reason, no
    concepts) is completed by the newer seed; an equal-or-newer
    seed-sourced record is never touched."""
    from app.candidate_metadata import CandidateMetadataRecord
    provider = TaskStoreCatalogProvider(TaskStoreV2(
        tempfile.mkdtemp() + "/al28_reauth2.db"))
    provider.upsert_candidate(CandidateMetadataRecord(
        candidate_id="F3", candidate_type="finding",
        title_snapshot="High withdrawal frequency.",
        status="INCOMPLETE", metadata_version=0,
        provenance={"seed_version": 1,
                    "insufficiency_reason": "requires authoring"}))
    seed_from_document(provider._store, json.loads(
        REPO_SEED.read_text("utf-8")))
    rec = provider.get_candidate("F3")
    assert rec.core_concepts == ["HIGH_WITHDRAWAL_FREQUENCY"]
    assert rec.status == "UNVERIFIED"

    # same-version seed-sourced INCOMPLETE record: protected
    provider2 = TaskStoreCatalogProvider(TaskStoreV2(
        tempfile.mkdtemp() + "/al28_reauth3.db"))
    provider2.upsert_candidate(CandidateMetadataRecord(
        candidate_id="F3", candidate_type="finding",
        status="INCOMPLETE", metadata_version=0,
        provenance={"seed_version": 3}))
    seed_from_document(provider2._store, json.loads(
        REPO_SEED.read_text("utf-8")))
    assert provider2.get_candidate("F3").core_concepts == []
