"""Streaming-branch empty-response handling in ClaudeProvider (phase V1-B).

The production GU and Planner paths both use stream=True
(goal_resolution.py GU call, planner_v2.py planner call) — the
`messages.stream()` branch in llm_provider._generate_once. The existing
tests/test_llm_retry_and_classification_v1.py covers the retry loop and
error classification over the NON-streaming `messages.create` shape;
this file exercises the REAL streaming code branch with a synthetic
client and pins the per-attempt diagnostic fields for the observed
production failure signature (INV-8cc8eb8121ee / TASK-6fb77701c28c /
TASK-bbeb522c488a: stop_reason=max_tokens, usage_output_tokens=8192,
content_kinds=["thinking"] — thinking-only, no text block).

Scope: client logic ONLY (parsing, empty classification, bounded retry
count, attempts diagnostics). Mock results say nothing about real model
latency or real empty-response rates.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.llm_provider import (
    ClaudeProvider,
    LLMEmptyResponseError,
)


def _thinking_block(chars=36000):
    b = MagicMock()
    b.type = "thinking"
    b.thinking = "x" * chars
    return b


def _text_block(text):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _response(content, stop_reason="max_tokens", out_tokens=8192,
              in_tokens=427):
    usage = SimpleNamespace(input_tokens=in_tokens,
                            output_tokens=out_tokens)
    return SimpleNamespace(content=content, stop_reason=stop_reason,
                           usage=usage)


class _FakeStream:
    """Context-manager stream whose final message is scripted."""

    def __init__(self, final_message):
        self._final = final_message
        self.events = [_SimpleEvent() for _ in range(3)]

    def __iter__(self):
        return iter(self.events)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._final


class _SimpleEvent:
    pass


class _FakeMessages:
    def __init__(self, final_messages):
        self._final_messages = list(final_messages)
        self.stream_calls = []

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return _FakeStream(self._final_messages.pop(0))


class _FakeClient:
    """Mimics the Anthropic client surface the streaming branch uses:
    with_options() (GU passes request-scoped timeout/retries) and
    messages.stream()."""

    def __init__(self, final_messages):
        self.messages = _FakeMessages(final_messages)
        self.options = {}

    def with_options(self, **opts):
        self.options = opts
        return self


def _provider(final_messages):
    p = ClaudeProvider(api_key="test-key", model="glm-5.3")
    p.client = _FakeClient(final_messages)
    return p


GU_KW = dict(messages=[{"role": "user", "content": "GU prompt"}],
             max_tokens=8192, temperature=0.0, timeout=240.0,
             sdk_max_retries=0, stream=True)


class TestStreamingEmptyResponse:
    def test_thinking_only_max_tokens_is_classified_empty(self):
        """The exact production signature (TASK-6fb77701c28c attempt 1):
        thinking-only content, stop_reason=max_tokens, output_tokens at
        the 8192 ceiling → LLMEmptyResponseError with the real context
        fields, and the per-attempt diagnostics recorded."""
        p = _provider([_response([_thinking_block(36523)],
                                 stop_reason="max_tokens",
                                 out_tokens=8192, in_tokens=427),
                       _response([_thinking_block(8981),
                                  _text_block("{}")],
                                 stop_reason="end_turn",
                                 out_tokens=2000, in_tokens=300)])
        text = p.generate(**GU_KW)          # attempt 2 succeeds
        assert text == "{}"
        ctx = {"stop_reason": "max_tokens"}   # from the recorded attempt
        attempt = p.last_attempts[0]
        assert attempt["outcome"] == "empty_response"
        assert attempt["stop_reason"] == "max_tokens"
        assert attempt["usage_output_tokens"] == 8192
        assert attempt["usage_input_tokens"] == 427
        assert attempt["content_kinds"] == ["thinking"]
        assert attempt["empty_detail"] == "non_text_blocks_only"
        assert attempt["max_tokens"] == 8192
        assert attempt["stream"] is True
        assert isinstance(attempt["elapsed_ms"], int)
        assert attempt["elapsed_ms"] >= 0
        # the request carried NO thinking parameter (client never sends it)
        sent = p.client.messages.stream_calls[0]
        assert "thinking" not in sent

    def test_thinking_then_text_extracts_text_and_marks_success(self):
        p = _provider([_response([_thinking_block(8981),
                                  _text_block('{"goal_resolution": '
                                              '"INFERRED"}')],
                                 stop_reason="end_turn",
                                 out_tokens=2372, in_tokens=2669)])
        text = p.generate(**GU_KW)
        assert text == '{"goal_resolution": "INFERRED"}'
        attempt = p.last_attempts[0]
        assert attempt["outcome"] == "success"
        assert attempt["accepted"] is True
        assert attempt["stop_reason"] == "end_turn"
        assert attempt["content_kinds"] == ["thinking", "text"]
        assert attempt["text_chars"] == len(
            '{"goal_resolution": "INFERRED"}')

    def test_double_empty_exactly_two_application_attempts(self):
        """TASK-6fb77701c28c: both attempts empty → give up after exactly
        TWO application attempts (bounded retry); both recorded with
        correct retry flags and the raise carries attempts=2."""
        p = _provider([_response([_thinking_block(36523)]),
                       _response([_thinking_block(35489)])])
        with pytest.raises(LLMEmptyResponseError) as ei:
            p.generate(**GU_KW)
        attempts = p.last_attempts
        assert len(attempts) == 2
        assert all(a["outcome"] == "empty_response" for a in attempts)
        assert attempts[0]["retry_triggered"] is True
        assert attempts[1]["retry_triggered"] is False
        assert all(a["usage_output_tokens"] == 8192 for a in attempts)
        assert ei.value.context.get("attempts") == 2
        assert "2 attempts" in str(ei.value)


class TestMalformedResponseActualBehavior:
    """Approved scope: pin the ACTUAL classification for malformed
    shapes; if it differs from expectations, report — do not change
    product code. (All three behaviors below were discovered by running
    these tests and are pinned as-found.)"""

    def test_content_none_classified_service_unavailable(self):
        """ACTUAL: content=None crashes the streaming meta construction
        (TypeError at the content_kinds comprehension) and is caught by
        the catch-all handler → re-raised as LLMError('Unexpected LLM
        error…') classified service_unavailable — NOT empty_response, no
        application attempt recorded, and the retry loop does not fire."""
        from app.llm_provider import LLMError
        p = _provider([_response(content=None)])
        with pytest.raises(LLMError) as ei:
            p.generate(**GU_KW)
        assert "Unexpected LLM error" in str(ei.value)
        assert "'NoneType' object is not iterable" in str(ei.value)
        # the catch-all records the attempt as service_unavailable
        assert p.last_attempts[0]["outcome"] == "service_unavailable"
        assert p.last_attempts[0]["error_class"] == "LLMError"

    def test_text_block_with_none_text_crashes_at_logging(self):
        """ACTUAL: a text-typed block with text=None passes the
        None-coalescing char counting, but the unconditional debug
        f-string evaluates len(block.text) → raw TypeError propagates
        (not in the taxonomy, not retried, not recorded)."""
        p = _provider([_response([_text_block(None)],
                                 stop_reason="end_turn",
                                 out_tokens=10, in_tokens=5)])
        from app.llm_provider import LLMError
        with pytest.raises(LLMError) as ei:
            p.generate(**GU_KW)
        # the raw TypeError is caught by the catch-all and re-raised as
        # LLMError service_unavailable (recorded, not retried)
        assert "has no len()" in str(ei.value)
        assert p.last_attempts[0]["outcome"] == "service_unavailable"

    def test_no_content_blocks_is_no_content_blocks(self):
        """content=[] → LLMEmptyResponseError with empty_detail=
        'no_content_blocks' (the other documented empty shape); like the
        thinking-only shape it consumes both application attempts."""
        p = _provider([_response([], stop_reason="end_turn",
                                 out_tokens=5, in_tokens=5),
                       _response([], stop_reason="end_turn",
                                 out_tokens=5, in_tokens=5)])
        with pytest.raises(LLMEmptyResponseError) as ei:
            p.generate(**GU_KW)
        attempts = p.last_attempts
        assert len(attempts) == 2
        assert all(a["empty_detail"] == "no_content_blocks"
                   for a in attempts)
        assert ei.value.context.get("attempts") == 2


class TestEmptyRetryBudgetEscalation:
    def test_max_tokens_empty_escalates_retry_budget(self):
        p = _provider([
            _response([_thinking_block()], stop_reason="max_tokens",
                      out_tokens=8192, in_tokens=427),
            _response([_thinking_block(), _text_block("ok")],
                      stop_reason="end_turn", out_tokens=2372,
                      in_tokens=2669)])
        text = p.generate([{"role": "user", "content": "GU"}],
                          max_tokens=8192, stream=True)
        assert text == "ok"
        a1, a2 = p.last_attempts
        assert a1["outcome"] == "empty_response"
        assert a1["max_tokens"] == 8192
        assert a1["budget_escalated"] is True
        assert a2["outcome"] == "success"
        assert a2["max_tokens"] == 16384      # escalated ×2
        assert a2.get("budget_escalated") is False  # flag only on the
        # empty attempt that TRIGGERED the escalation

    def test_non_max_tokens_empty_retries_at_original_budget(self):
        """no_content_blocks (stop_reason=end_turn) is NOT the budget-
        exhaustion signature — the retry stays at the original budget."""
        plain_empty = _response([], stop_reason="end_turn",
                                out_tokens=0, in_tokens=5)
        p = _provider([plain_empty, plain_empty])
        with pytest.raises(LLMEmptyResponseError):
            p.generate([{"role": "user", "content": "GU"}],
                       max_tokens=8192, stream=True)
        a1, a2 = p.last_attempts
        assert a1["stop_reason"] == "end_turn"
        assert a1["max_tokens"] == 8192
        assert a1.get("budget_escalated") is False
        assert a2["max_tokens"] == 8192

    def test_first_try_success_never_escalates(self):
        p = _provider([_response([_text_block("ok")],
                                 stop_reason="end_turn",
                                 out_tokens=10, in_tokens=5)])
        assert p.generate([{"role": "user", "content": "GU"}],
                          max_tokens=8192, stream=True) == "ok"
        assert p.last_attempts[0]["max_tokens"] == 8192
        assert p.last_attempts[0].get("budget_escalated") is False

    def test_escalated_double_empty_still_bounded_at_two_attempts(self):
        p = _provider([
            _response([_thinking_block()], stop_reason="max_tokens",
                      out_tokens=8192, in_tokens=427),
            _response([_thinking_block()], stop_reason="max_tokens",
                      out_tokens=8192, in_tokens=427)])
        with pytest.raises(LLMEmptyResponseError) as ei:
            p.generate([{"role": "user", "content": "GU"}],
                       max_tokens=8192, stream=True)
        a1, a2 = p.last_attempts
        assert a1["max_tokens"] == 8192
        assert a2["max_tokens"] == 16384      # escalated exactly once
        assert ei.value.context.get("attempts") == 2
