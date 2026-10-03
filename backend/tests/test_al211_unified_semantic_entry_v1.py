"""AL-2.11 — the lexicon is gone; ONE finding-semantic entry.

Proves the migration end state required by the work item:

1. DELETED PATH: finding_lexicon.py does not exist; no production import;
   no lexicon branch in the resolver.
2. SAME-PATH PROOF: F5 / F3 / F7 each resolve through
   SemanticNormalizer → governed concept → candidate grounding, with
   invocation provenance (script consumption + semantic evidence).
3. NO HIDDEN MAPPING: with the normalizer unavailable or proposing
   nothing, CJK finding phrases do NOT resolve — there is no phrase
   table behind the scenes.
4. F3 ≠ F7: the structural distinction holds ON the unified path.
"""

import json
from pathlib import Path

import pytest

from app.context_resolution import ContextResolver, ResolutionStatus
from app.models import InvestigationContext

from tests.test_al28_semantic_foundation_v1 import (
    _findings,
    _norm_script,
    seeded_catalog,
)
from tests.test_api_investigations_v1 import ScriptedLLM

APP_DIR = Path(__file__).resolve().parents[1] / "app"


class _DownLLM:
    """Genuine provider unavailability."""

    def generate(self, **kw):
        raise RuntimeError("provider unreachable")


def _resolve(script, text, findings_ids=("F3", "F4", "F5", "F6", "F7")):
    catalog, _ = seeded_catalog()
    resolver = ContextResolver(llm_provider=script, llm_assist=False,
                               semantic_catalog=catalog)
    return resolver.resolve(
        text, InvestigationContext(case_id="U00010"),
        findings=_findings(findings_ids))


# --- 1. the deleted path is really gone ------------------------------------------

def test_lexicon_module_is_deleted():
    assert not (APP_DIR / "finding_lexicon.py").exists()


def test_no_production_import_or_branch_remains():
    for py in APP_DIR.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "finding_lexicon" not in src, f"{py.name} still references it"
        assert "_alias_findings" not in src, f"{py.name} still has the branch"
        assert "alias_matches" not in src, f"{py.name} still matches aliases"


# --- 2. same-path proof for F5 / F3 / F7 -----------------------------------------

@pytest.mark.parametrize("phrase,concept,candidate", [
    ("共享设备为什么是风险项", "SHARED_DEVICE_RELATIONSHIP", "F5"),
    ("给我输出一下高频提现的具体证据", "HIGH_WITHDRAWAL_FREQUENCY", "F3"),
    ("为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
     "HIGH_TRADING_FREQUENCY", "F7"),
])
def test_f3_f5_f7_all_flow_through_the_single_semantic_entry(
        phrase, concept, candidate):
    script = ScriptedLLM([_norm_script(concept, phrase[:4])])
    out = _resolve(script, phrase)
    assert out.status == ResolutionStatus.RESOLVED
    assert out.updated_context.focused_finding_id == candidate
    # SAME-PATH PROOF: every finding reference carries normalizer
    # provenance — none of them resolved through any other mechanism.
    assert out.used_llm is True
    assert out.semantic_evidence["stage"] == "semantic_normalizer"
    assert out.semantic_evidence["status"] == "OK"
    assert out.semantic_evidence["concept_id"] == concept
    assert out.semantic_evidence["grounded_candidate_ids"] == [candidate]
    assert script.calls == 1                    # exactly one proposal
    assert "UNVERIFIED" in out.resolution_reason   # status = provenance only


def test_f3_f7_distinction_holds_on_the_unified_path():
    withdrawal = _resolve(ScriptedLLM(
        [_norm_script("HIGH_WITHDRAWAL_FREQUENCY", "高频提现")]),
        "为什么高频提现是风险项")
    trading = _resolve(ScriptedLLM(
        [_norm_script("HIGH_TRADING_FREQUENCY", "高频交易")]),
        "为什么高频交易是风险项")
    assert withdrawal.updated_context.focused_finding_id == "F3"
    assert trading.updated_context.focused_finding_id == "F7"
    catalog, _ = seeded_catalog()
    f3 = catalog.get_candidate("F3")
    f7 = catalog.get_candidate("F7")
    assert not set(f3.core_concepts) & set(f7.core_concepts)


# --- 3. no hidden mapping behind the scenes ---------------------------------------

def test_no_key_finding_request_never_falls_back_to_a_phrase_table():
    out = _resolve(_DownLLM(), "共享设备为什么是风险项")
    assert out.status == ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE
    assert out.updated_context.focused_finding_id is None
    assert out.semantic_evidence["reason_class"] == "llm_unavailable"


def test_semantic_no_proposal_means_no_resolution_even_for_known_phrases():
    """The normalizer proposing NOTHING leaves even the historical lexicon
    phrases unresolved — resolution came only from the governed proposal,
    never from a phrase table."""
    out = _resolve(ScriptedLLM([_norm_script(None, "")]),
                   "共享设备为什么是风险项")
    assert out.status == ResolutionStatus.UNRESOLVED
    assert out.updated_context.focused_finding_id is None
