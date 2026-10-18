"""A-fix — bounded LLM retry + accurate error classification (U00047).

Covers, at the REAL production boundaries:

1. Provider (ClaudeProvider.generate): an HTTP-200 response with NO text
   block is retried ONCE at the shared LLM call boundary (the Anthropic
   SDK's max_retries does not cover 200-empty-content); a valid second
   response is used; two empty responses raise LLMEmptyResponseError with
   attempts=2; a valid first response causes exactly ONE create call.
2. Planner (PlannerV2._plan_inner): provider exceptions map to DISTINCT
   PlanningFailure codes — never flattened into LLM_UNAVAILABLE — and an
   empty-then-valid sequence plans successfully.
3. Transport/timeout/rate-limit remain the SDK's responsibility: no
   app-layer loop is added for them.
"""

import pytest

from unittest.mock import MagicMock

import app.llm_provider as llm_provider_mod
from app.llm_provider import (
    ClaudeProvider,
    LLMConfigurationError,
    LLMEmptyResponseError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.planner_v2 import PlannerV2
from app.skills import eligible_skills_for_finding


def _thinking_block():
    b = MagicMock()
    b.type = "thinking"
    return b


def _text_block(text):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _provider_with(responses, calls):
    """ClaudeProvider whose Anthropic client returns the scripted
    responses in order (each entry = one HTTP 200 response's content)."""
    p = ClaudeProvider(api_key="test-key", model="test-model")

    def create(**kwargs):
        calls.append(1)
        content = responses.pop(0) if responses else [_thinking_block()]
        response = MagicMock()
        response.content = content
        response.stop_reason = "end_turn"
        return response

    p.client.messages.create = create
    return p


PLAN_JSON = ('{{"skill_id": "timeline_investigation", "goal": "explain", '
             '"steps": [{{"type": "explain_signal", "reason": "why"}}]}}')


class TestProviderBoundedRetry:
    def test_empty_then_valid_second_attempt_used(self):
        calls = []
        p = _provider_with([[_thinking_block()], [_text_block("plan")]],
                           calls)
        text = p.generate([{"role": "user", "content": "plan"}])
        assert text == "plan"
        assert len(calls) == 2              # application-layer attempts

    def test_double_empty_raises_after_two_attempts(self):
        calls = []
        p = _provider_with([[_thinking_block()], [_thinking_block()]],
                           calls)
        with pytest.raises(LLMEmptyResponseError) as ei:
            p.generate([{"role": "user", "content": "plan"}])
        assert len(calls) == 2
        assert ei.value.context.get("attempts") == 2

    def test_valid_first_response_single_call(self):
        calls = []
        p = _provider_with([[_text_block("ok")]], calls)
        assert p.generate([{"role": "user", "content": "x"}]) == "ok"
        assert len(calls) == 1              # no extra calls for valid text

    def test_rate_limit_is_not_app_layer_retried(self):
        """429 keeps its retry_after semantics and is NOT retried by the
        application layer — the SDK's own max_retries owns it inside
        create(); one app-level call per generate()."""
        import httpx
        from anthropic import RateLimitError as AnthropicRateLimitError

        calls = []
        p = _provider_with([], calls)
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(429, request=req,
                              headers={"retry-after": "7"})

        def create(**kwargs):
            calls.append(1)
            raise AnthropicRateLimitError("rate limited",
                                          response=resp, body=None)

        p.client.messages.create = create
        with pytest.raises(LLMRateLimitError) as ei:
            p.generate([{"role": "user", "content": "x"}])
        assert len(calls) == 1              # exactly one app-layer call
        assert ei.value.context.get("retry_after") == "7"

    def test_connection_error_is_not_app_layer_retried(self):
        """Transport errors stay the SDK's responsibility: the provider
        maps them to LLMError but never loops at the application layer."""
        import httpx
        from anthropic import APIConnectionError

        calls = []
        p = _provider_with([], calls)
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

        def create(**kwargs):
            calls.append(1)
            raise APIConnectionError(request=req)

        p.client.messages.create = create
        with pytest.raises(LLMError):
            p.generate([{"role": "user", "content": "x"}])
        assert len(calls) == 1              # exactly one app-layer call

    def test_auth_error_is_not_app_layer_retried(self):
        """401 → LLMConfigurationError immediately; no application-layer
        retry for authentication/configuration failures."""
        import httpx
        from anthropic import APIStatusError

        calls = []
        p = _provider_with([], calls)
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(401, request=req)

        def create(**kwargs):
            calls.append(1)
            raise APIStatusError("invalid api key",
                                 response=resp, body=None)

        p.client.messages.create = create
        with pytest.raises(LLMConfigurationError):
            p.generate([{"role": "user", "content": "x"}])
        assert len(calls) == 1              # no retry on auth errors


class TestPlannerExceptionClassification:
    def _plan_with(self, provider):
        from app.domain_tools import risk_case_fetch
        findings = list((risk_case_fetch(
            case_id="U90047").data or {}).get("findings") or [])
        planner = PlannerV2(llm_provider=provider)
        from app.models import InvestigationContext
        return planner.plan(
            "共享设备为什么是风险项，是否有判定阈值",
            InvestigationContext(case_id="U90047"),
            [s.skill_id for s in __import__(
                "app.skills",
                fromlist=["eligible_skills_for_finding"])
                .eligible_skills_for_finding(None)],
            finding_capabilities=None, goal_contract=None,
            case_findings=findings)

    def test_empty_response_maps_to_distinct_code(self):
        class _EmptyProvider:
            def generate(self, **kw):
                raise LLMEmptyResponseError(
                    "no text block", context={"attempts": 2})

        failure = self._plan_with(_EmptyProvider())
        assert failure.code == "LLM_EMPTY_RESPONSE"
        assert failure.code != "LLM_UNAVAILABLE"

    def test_timeout_maps_to_distinct_code(self):
        class _TimeoutProvider:
            def generate(self, **kw):
                raise LLMTimeoutError("timed out", context={})

        failure = self._plan_with(_TimeoutProvider())
        assert failure.code == "LLM_TIMEOUT"

    def test_rate_limit_maps_to_distinct_code(self):
        class _RateLimitedProvider:
            def generate(self, **kw):
                raise LLMRateLimitError("rate limited", context={})

        failure = self._plan_with(_RateLimitedProvider())
        assert failure.code == "LLM_RATE_LIMITED"

    def test_configuration_error_maps_to_distinct_code(self):
        class _ConfigProvider:
            def generate(self, **kw):
                raise LLMConfigurationError("no api key", context={})

        failure = self._plan_with(_ConfigProvider())
        assert failure.code == "LLM_CONFIGURATION_ERROR"

    def test_generic_provider_error_maps_to_llm_unavailable(self):
        """LLMError = the provider-unreachable family (connection/5xx)
        — LLM_UNAVAILABLE is the ACCURATE code for it; the distinct
        classes (empty/timeout/rate-limit/config) have their own codes."""
        class _GenericProvider:
            def generate(self, **kw):
                raise LLMError("gateway 502", context={})

        failure = self._plan_with(_GenericProvider())
        assert failure.code == "LLM_UNAVAILABLE"

    def test_unexpected_exception_is_not_masked_as_llm_unavailable(self):
        class _BrokenProvider:
            def generate(self, **kw):
                raise ValueError("unexpected internal error")

        failure = self._plan_with(_BrokenProvider())
        assert failure.code == "PLANNING_UNEXPECTED_ERROR"
        assert failure.code != "LLM_UNAVAILABLE"
