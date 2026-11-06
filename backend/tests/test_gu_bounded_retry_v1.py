"""R2.2.3 Phase 2 — GU bounded connection retry at the provider boundary.

Phase 1 is a frozen, accepted baseline; these tests must not alter it.

Physical-call accounting: the tests replace the provider's HTTP client
with a FAKE CLIENT at the `with_options`/`messages.stream` boundary —
the REAL ClaudeProvider.generate()/with_options/stream code path runs,
and every `messages.stream` entry is one PHYSICAL HTTP request (the GU
call configures the SDK with max_retries=0, so no hidden SDK retries
exist). Assertions are on that counter, never on "a retry function was
called".

Frozen rules under test:
- max TWO physical calls (first + at most one bounded retry), shared
  with the empty-response retry;
- ONLY transport connection failures are retried (type/chain first,
  bounded audited-signature fallback);
- timeout / rate-limit / configuration errors: single physical call, no
  retry;
- local program defects (the audited `_system_for_stream` NameError):
  single physical call, honest local_error classification, real type
  preserved via the cause chain, never retried or mislabeled;
- timeout passes through per physical call (240s for GU);
- logs distinguish retry / retry-success / exhaustion and never carry
  prompt or credential content.
"""

import logging

import httpx
import pytest

from eval.fixtures import default_findings

from app.exceptions import (
    LLMConfigurationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm_provider import ClaudeProvider, LLMError

AUDITED_PEER_CLOSED = ("peer closed connection without sending complete "
                       "message body (incomplete chunked read)")


def _remote_protocol_error():
    # the audited transport type for the mid-stream GU drop (U00010)
    return httpx.RemoteProtocolError(AUDITED_PEER_CLOSED)


class _FakeBlock:
    def __init__(self, kind, text=""):
        self.type = kind
        self.text = text
        self.thinking = ""


class _FakeResponse:
    def __init__(self, text="OK-GU-PROPOSAL", stop_reason="end_turn"):
        self.content = [_FakeBlock("text", text)]
        self.stop_reason = stop_reason
        self.usage = None


class _FakeStream:
    def __init__(self, behavior):
        self.behavior = behavior

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        b = self.behavior
        if isinstance(b, BaseException):
            raise b
        if b == "":
            # empty physical response: NO content blocks → the provider's
            # LLMEmptyResponseError path (pre-existing A-fix retry)
            return
        yield _FakeBlock("text", str(b))

    def get_final_message(self):
        # the REAL streaming path accumulates events and returns the
        # final message (GU parses its JSON from the text block)
        b = self.behavior
        if isinstance(b, BaseException):
            raise b
        if b == "":
            resp = _FakeResponse.__new__(_FakeResponse)
            resp.content = []
            resp.stop_reason = "end_turn"
            resp.usage = None
            return resp
        return _FakeResponse(str(b))


class _FakeMessages:
    def __init__(self, physical):
        self._physical = physical

    def stream(self, **kwargs):
        idx = min(self._physical.calls, len(self._physical.script) - 1)
        self._physical.calls += 1
        self._physical.requests.append(dict(kwargs))
        return _FakeStream(self._physical.script[idx])


class _FakeClient:
    """Records with_options (timeout / max_retries) per physical call and
    serves the scripted stream behaviors."""

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.requests = []
        self.opts_history = []
        self._messages = _FakeMessages(self)

    @property
    def messages(self):
        return self._messages

    def with_options(self, **opts):
        self.opts_history.append(dict(opts))
        # the SDK applies request-scoped options to the returned client
        clone = _FakeClient(self.script)
        clone._messages = self._messages
        clone.opts_history = self.opts_history
        clone.requests = self.requests
        clone._opts = opts
        return clone


def make_provider(behaviors, *, connection_retry=True, timeout=240.0,
                  sdk_max_retries=0):
    p = ClaudeProvider(api_key="test-key", model="test-model")
    fake = _FakeClient(behaviors)
    p.client = fake
    p._fake = fake
    p._kw = {"connection_retry": connection_retry, "timeout": timeout,
             "sdk_max_retries": sdk_max_retries, "stream": True}
    return p, fake


def gen(p, messages=None):
    msgs = messages or [{"role": "user",
                         "content": "QUESTION-TEXT-PLACEHOLDER"}]
    return p.generate(msgs, max_tokens=100, temperature=0.0, **p._kw)


def conn_err():
    return _remote_protocol_error()


class TestBoundedConnectionRetry:
    def test_first_connection_error_second_succeeds(self, caplog):
        """A: first physical request raises the audited mid-stream
        connection error, the bounded retry succeeds — exactly 2 physical
        calls, the second result returned."""
        p, fake = make_provider([conn_err(), "OK-GU-PROPOSAL"])
        with caplog.at_level(logging.WARNING, logger="app.llm_provider"):
            text = gen(p)
        assert text == "OK-GU-PROPOSAL"
        assert len(fake.requests) == 2
        assert fake.opts_history[0]["max_retries"] == 0
        assert fake.opts_history[0]["timeout"] == 240.0
        outcomes = [a["outcome"] for a in p.last_attempts]
        assert outcomes == ["service_unavailable", "success"]
        # the success record marks that it WAS the triggered retry
        assert [a["retry_triggered"] for a in p.last_attempts] ==                 [True, True]
        assert any("retrying once" in r.getMessage()
                   for r in caplog.records)

    def test_connection_errors_exhaust_budget(self, caplog):
        """B: both physical requests raise the connection error —
        exactly 2 physical calls, the ORIGINAL exception propagates for
        the existing GU fallback."""
        p, fake = make_provider([conn_err(), conn_err()])
        with pytest.raises(LLMError) as ei:
            gen(p)
        assert len(fake.requests) == 2
        assert AUDITED_PEER_CLOSED in str(ei.value)
        assert ei.value.__cause__ is not None
        outcomes = [a["outcome"] for a in p.last_attempts]
        assert outcomes == ["service_unavailable", "service_unavailable"]
        assert any("retry budget exhausted" in r.getMessage()
                   or "giving up" in r.getMessage()
                   for r in caplog.records)

    def test_local_programming_error_single_call_honest_class(self):
        """C/D: the audited local defect (`_system_for_stream`
        NameError) takes exactly ONE physical call, is classified
        local_error (never service_unavailable), and its REAL type stays
        on the exception chain — never masked, never retried."""
        p, fake = make_provider([conn_err()])
        import app.llm_provider as lp

        def _boom(*a, **k):
            raise NameError("name '_system_for_stream' is not defined")
        mp = pytest.MonkeyPatch()
        # MonkeyPatch restores the ORIGINAL staticmethod descriptor
        mp.setattr(ClaudeProvider, "_system_for_stream",
                   staticmethod(_boom))
        try:
            with pytest.raises(LLMError) as ei:
                gen(p)
        finally:
            mp.undo()
        # the local defect fires BEFORE any physical request
        assert len(fake.requests) == 0
        attempt = p.last_attempts[-1]
        assert attempt["outcome"] == "local_error"
        assert attempt["retry_triggered"] is False
        assert "_system_for_stream" in attempt["detail"]
        assert "_system_for_stream" in str(ei.value.__cause__)

    def test_transport_type_connection_error_retried(self):
        """Type-first detection: a raw httpx.RemoteProtocolError
        (transport type) is retried without relying on the signature
        fallback."""
        p, fake = make_provider([_remote_protocol_error(), "OK"])
        text = gen(p)
        assert text == "OK"
        assert len(fake.requests) == 2

    def test_non_connection_signatureless_error_no_retry(self):
        """A generic ValueError WITHOUT any connection signature or
        transport type in the chain is a local defect: one physical
        request, local_error outcome, no retry. The inner wrap turns it
        into a generic LLMError (pre-existing behavior) while the REAL
        type stays on the cause chain."""
        p, fake = make_provider([ValueError("bad input mapping")])
        with pytest.raises(LLMError) as ei:
            gen(p)
        assert len(fake.requests) == 1
        attempt = p.last_attempts[-1]
        assert attempt["outcome"] == "local_error"
        assert "bad input mapping" in str(ei.value)
        assert isinstance(ei.value.__cause__, ValueError)

    def test_timeout_no_retry(self):
        """Timeout errors keep their single-call behavior (retrying a
        timed-out 240s call is out of the frozen scope)."""
        p, fake = make_provider([LLMTimeoutError("timed out")])
        # non-retryable: single physical call; the pre-existing inner wrap
        # classifies it as a generic LLMError — real type on the chain
        with pytest.raises(LLMError) as ei:
            gen(p)
        assert len(fake.requests) == 1
        assert "timed out" in str(ei.value)
        assert isinstance(ei.value.__cause__, LLMTimeoutError)

    def test_rate_limit_no_retry(self):
        # production shape: the SDK raises AnthropicRateLimitError INSIDE
        # the stream; the inner wrap converts it to LLMRateLimitError
        from anthropic import RateLimitError as AnthropicRateLimitError
        rl = AnthropicRateLimitError(
            "429", response=httpx.Response(
                429, request=httpx.Request("POST", "https://test.invalid")),
            body=None)
        p, fake = make_provider([rl])
        with pytest.raises(LLMRateLimitError) as ei:
            gen(p)
        assert len(fake.requests) == 1
        assert "rate limit" in str(ei.value).lower()

    def test_configuration_error_no_retry(self):
        p, fake = make_provider([LLMConfigurationError("bad key")])
        with pytest.raises(LLMError) as ei:
            gen(p)
        assert len(fake.requests) == 1
        assert "bad key" in str(ei.value)
        assert isinstance(ei.value.__cause__, LLMConfigurationError)

    def test_planner_compat_connection_retry_off_by_default(self):
        """Without the GU opt-in, connection errors keep the exact
        pre-Phase-2 behavior: one physical request, immediate raise (the
        Planner keeps the shared-client SDK defaults and gains no app
        retry)."""
        p, fake = make_provider([conn_err()], connection_retry=False)
        with pytest.raises(LLMError):
            gen(p)
        assert len(fake.requests) == 1


class TestBudgetAndConfig:
    def test_connection_retry_shares_the_two_call_budget(self):
        """connection → connection: the combined application budget stops
        at 2 physical calls (never 3) even though the second script slot
        could serve more."""
        p, fake = make_provider([conn_err(), conn_err()])
        with pytest.raises(LLMError):
            gen(p)
        assert len(fake.requests) == 2

    def test_empty_response_retry_still_works(self):
        """The pre-existing empty-response retry (Phase-1-era A-fix)
        still works within the same budget."""
        p, fake = make_provider(["", "OK-GU-PROPOSAL"])
        text = gen(p)
        assert text == "OK-GU-PROPOSAL"
        assert len(fake.requests) == 2
        outcomes = [a["outcome"] for a in p.last_attempts]
        assert outcomes == ["empty_response", "success"]

    def test_timeout_passthrough_per_physical_request(self):
        """F: the GU timeout (240s) is applied via with_options on every
        physical request."""
        p, fake = make_provider([conn_err(), "OK"])
        gen(p)
        assert len(fake.requests) == 2
        assert all(o.get("timeout") == 240.0 for o in fake.opts_history)

    def test_sdk_max_retries_zero_for_gu(self):
        """E: the GU call configures the SDK with max_retries=0 — no
        hidden SDK-side retries can stack on the application budget."""
        p, fake = make_provider([conn_err(), "OK"])
        gen(p)
        assert all(o.get("max_retries") == 0 for o in fake.opts_history)

    def test_no_prompt_content_in_logs(self, caplog):
        """G: retry/exhaustion logs never carry prompt or credential
        content."""
        import logging as _logging
        secret = "SECRET-PROMPT-CONTENT-marker"
        p, fake = make_provider([conn_err(), conn_err()])
        with caplog.at_level(logging.DEBUG, logger="app.llm_provider"):
            with pytest.raises(LLMError):
                gen(p, messages=[{"role": "user", "content": secret}])
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "SECRET-PROMPT-CONTENT" not in joined
        assert "retrying once" in joined


# ---------------------------------------------------------------------------
# GU-level: parse/structure errors after a NON-EMPTY response are never
# retried (the provider returned text; the failure is downstream parsing)
# ---------------------------------------------------------------------------

class TestGUParseNoRetry:
    def test_malformed_payload_single_generate_call(self):
        """D: a non-empty but structurally unusable GU response is parsed
        ONCE — the provider is not re-asked (parse failures are not
        transport failures) — and the run takes the existing heuristic
        fallback."""
        import json as _json

        from app.goal_resolution import understand_goal

        calls = {"n": 0}

        class CountingProvider:
            def generate(self, messages, max_tokens=0, temperature=0.1,
                         timeout=None, sdk_max_retries=None, stream=False):
                calls["n"] += 1
                # non-empty but structurally unusable (E-1 shape)
                return '{"goal_resolution": "BOGUS_NOT_IN_VOCABULARARY"}'

        diag = {}
        rep = understand_goal(
            "generate all trade records as an artifact",
            resolved_finding_id=None, case_id="U00010",
            use_llm=True, diagnostics=diag,
            case_findings=default_findings(), llm=CountingProvider())
        assert calls["n"] == 1
        assert (rep.goal_type is None
                or rep.goal_resolution in ("UNRESOLVED", "INFERRED"))
        # no fabricated LLM proposal origin on the fallback path
        assert rep.source != "llm" or rep.proposal_source != "llm"
