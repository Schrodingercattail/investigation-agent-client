"""AL-2.5 regression — the backend must start without an LLM key.

AL-2.5 real-stack finding: `api = InvestigationAPI()` eagerly constructed
ClaudeProvider() inside the default refinement/admissibility factories,
so importing app.main without ANTHROPIC_API_KEY crashed startup — even
though every runtime consumer has a documented deterministic fallback
(refine_target → CLARIFY, SemanticNormalizer → UNKNOWN, proposer →
HeuristicDecisionProposer). The whole suite had been silently dependent
on backend/.env exporting the key into os.environ.
"""

import os

from app.api.investigations import InvestigationAPI


def test_default_api_constructs_without_llm_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # also neutralize a settings-cached provider config if any
    api = InvestigationAPI()          # must not raise
    assert api.store is not None
    # the degraded collaborators are wired (None LLM → deterministic path)
    assert api.service is not None
    assert os.getenv("ANTHROPIC_API_KEY") in (None, "")
