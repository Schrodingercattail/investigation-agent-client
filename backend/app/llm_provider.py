import logging
import os
from datetime import datetime, timezone

from anthropic import Anthropic
from anthropic import APITimeoutError
from anthropic import RateLimitError as AnthropicRateLimitError
from anthropic import APIConnectionError, APIStatusError
from dotenv import load_dotenv

from app.exceptions import (
    LLMConfigurationError,
    LLMEmptyResponseError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)


load_dotenv()


logger = logging.getLogger(__name__)


class ClaudeProvider:
    """Provider for Claude LLM with robust error handling."""

    DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_TIMEOUT = 60.0
    # Slow-request diagnosis (GU empty attempts, 21 persisted samples):
    # thinking-only responses grow to EXACTLY the output budget
    # (usage_out=4096 era → 52-92s empties; usage_out=8192 era → 163-189s
    # empties) — forced thinking is unbounded, so an empty response is
    # budget exhaustion, and repeating the SAME budget just repeats the
    # truncation. When the bounded retry fires on a max_tokens stop, the
    # retry therefore runs with a DOUBLED budget (single variable, one
    # escalation, never compounded): the same request gets the thinking
    # room it truncated at, and a still-empty escalated retry fails the
    # same way the old same-budget retry did. Success attempts observed
    # at 2.4k-7.6k output tokens fit comfortably.
    EMPTY_RETRY_BUDGET_FACTOR = 2

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        """
        Initialize Claude provider.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            base_url: Optional base URL override (defaults to ANTHROPIC_BASE_URL env var)
            model: Model name (defaults to ANTHROPIC_MODEL env var or DEFAULT_MODEL)

        Raises:
            LLMConfigurationError: If configuration is invalid
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL")
        self.model = model or os.getenv("ANTHROPIC_MODEL") or self.DEFAULT_MODEL

        if not self.api_key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not configured",
                context={"env_var": "ANTHROPIC_API_KEY"},
            )

        if not self.model:
            raise LLMConfigurationError(
                "ANTHROPIC_MODEL is not configured",
                context={"env_var": "ANTHROPIC_MODEL"},
            )

        try:
            self.client = Anthropic(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.DEFAULT_TIMEOUT,
            )
            logger.info(f"ClaudeProvider initialized with model: {self.model}")
        except Exception as e:
            raise LLMConfigurationError(
                f"Failed to initialize Anthropic client: {str(e)}",
                context={"base_url": self.base_url},
            ) from e

    def generate(
        self,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.1,
        timeout: float | None = None,
        sdk_max_retries: int | None = None,
        stream: bool = False,
    ) -> str:
        """
        Generate a response from the LLM.

        Bounded retry: an HTTP-200 response that carries NO text block
        (LLMEmptyResponseError — observed with thinking-style models) is
        retried ONCE at this shared call boundary; the Anthropic SDK's own
        max_retries does not cover it (a 200 is transport-successful).
        Transport/timeout/429/5xx retries remain the SDK's responsibility
        (default max_retries=2) — no app-layer loop is added for them, so
        the combined request budget stays bounded.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0 to 1.0)

        Returns:
            The generated text response

        Raises:
            LLMEmptyResponseError: If both attempts return no text block
            LLMTimeoutError: If the request times out
            LLMRateLimitError: If rate limit is exceeded
            LLMError: For other LLM-related errors
            LLMConfigurationError: If configuration is invalid
        """
        last_empty: LLMEmptyResponseError | None = None
        attempt_max_tokens = max_tokens
        # A-fix observability: per-application-attempt diagnostics for THIS
        # call. Consumed by structured callers (goal understanding) and
        # persisted with their stage diagnostics. Safe fields only — no
        # prompts, no response bodies. Application-layer attempts are
        # counted here; SDK/transport retries below this boundary are NOT
        # observable and are never conflated with these counts.
        attempts: list[dict] = []
        self.last_attempts = attempts
        for attempt in (1, 2):
            _t0 = __import__("time").monotonic()
            try:
                text = self._generate_once(messages, attempt_max_tokens,
                                           temperature, timeout,
                                           sdk_max_retries, stream)
                attempts.append({
                    "attempt_index": attempt,
                    "max_application_attempts": 2,
                    "outcome": "success",
                    "error_class": None,
                    "elapsed_ms": int((__import__("time").monotonic()
                                       - _t0) * 1000),
                    "model": self.model,
                    "max_tokens": attempt_max_tokens,
                    "timeout_seconds": timeout,
                    "sdk_max_retries": sdk_max_retries,
                    "retry_triggered": attempt > 1,
                    "budget_escalated": False,   # only an empty attempt
                    "accepted": True,            # can trigger escalation
                    **getattr(self, "last_response_meta", {}),
                    **getattr(self, "last_attempt_window", {}),
                })
                return text
            except LLMEmptyResponseError as e:
                _ctx = getattr(e, "context", {}) or {}
                attempts.append({
                    "attempt_index": attempt,
                    "max_application_attempts": 2,
                    "outcome": "empty_response",
                    "error_class": "LLMEmptyResponseError",
                    "empty_detail": _ctx.get("empty_detail", "unknown"),
                    "stop_reason": _ctx.get("stop_reason"),
                    "elapsed_ms": int((__import__("time").monotonic()
                                       - _t0) * 1000),
                    "model": self.model,
                    "max_tokens": attempt_max_tokens,
                    "timeout_seconds": timeout,
                    "sdk_max_retries": sdk_max_retries,
                    "retry_triggered": attempt == 1,
                    # budget escalation: the ONE targeted response to the
                    # verified budget-exhaustion signature — only for a
                    # max_tokens stop, only once (attempt 2 is final).
                    "budget_escalated": (
                        attempt == 1 and _ctx.get("stop_reason") == "max_tokens"),
                    "accepted": False,
                    **getattr(self, "last_response_meta", {}),
                    **getattr(self, "last_attempt_window", {}),
                })
                last_empty = e
                if (attempt == 1
                        and _ctx.get("stop_reason") == "max_tokens"):
                    attempt_max_tokens = (
                        max_tokens * type(self).EMPTY_RETRY_BUDGET_FACTOR)
                logger.warning(
                    "LLM attempt %d/2 returned no text block "
                    "(stop_reason=%s)%s",
                    attempt,
                    _ctx.get("stop_reason"),
                    "; retrying once" if attempt == 1 else "; giving up")
            except (APITimeoutError, LLMTimeoutError) as e:
                self._record_and_reraise("timeout", "LLMTimeoutError", e, _t0,
                                          max_tokens=max_tokens,
                                          timeout_seconds=timeout,
                                          sdk_max_retries=sdk_max_retries)
            except AnthropicRateLimitError as e:
                self._record_and_reraise("rate_limit", "AnthropicRateLimitError", e, _t0,
                                          max_tokens=max_tokens,
                                          timeout_seconds=timeout,
                                          sdk_max_retries=sdk_max_retries)
            except LLMRateLimitError as e:
                self._record_and_reraise("rate_limit", "LLMRateLimitError", e, _t0,
                                          max_tokens=max_tokens,
                                          timeout_seconds=timeout,
                                          sdk_max_retries=sdk_max_retries)
            except LLMConfigurationError as e:
                self._record_and_reraise("configuration_error", type(e).__name__, e, _t0,
                                          max_tokens=max_tokens,
                                          timeout_seconds=timeout,
                                          sdk_max_retries=sdk_max_retries)
            except (APIConnectionError, LLMError) as e:
                self._record_and_reraise("service_unavailable", type(e).__name__, e, _t0,
                                          max_tokens=max_tokens,
                                          timeout_seconds=timeout,
                                          sdk_max_retries=sdk_max_retries)
            except APIStatusError as e:
                outcome = ("configuration_error" if getattr(e, "status_code", None) in (401, 400)
                           else "service_unavailable")
                self._record_and_reraise(outcome, type(e).__name__, e, _t0)
        context = dict(last_empty.context or {}) if last_empty else {}
        context["attempts"] = 2
        raise LLMEmptyResponseError(
            "LLM response contained no text block (2 attempts)",
            context=context,
        ) from last_empty

    @staticmethod
    def _system_for_stream(messages: list[dict]) -> str | None:
        """Anthropic protocol: system travels as a top-level parameter."""
        for m in messages:
            if m.get("role") == "system":
                return m.get("content")
        return None

    def _record_and_reraise(self, outcome, error_class, exc, _t0,
                            max_tokens=None, timeout_seconds=None,
                            sdk_max_retries=None):
        """A-fix observability: record a NON-empty-response attempt (the
        mapped classification), then re-raise unchanged — no retry."""
        self.last_attempts.append({
            "attempt_index": len(self.last_attempts) + 1,
            "max_application_attempts": 2,
            "outcome": outcome,
            "error_class": type(exc).__name__,
            "detail": str(exc)[:120],
            "elapsed_ms": int((__import__("time").monotonic() - _t0) * 1000),
            "model": self.model,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "sdk_max_retries": sdk_max_retries,
            "retry_triggered": False,
            "accepted": False,
            **getattr(self, "last_attempt_window", {}),
        })
        raise exc

    # TEST INSTRUMENTATION (bounded): injects HTTP-200 no-text-block
    # responses at the shared LLM call boundary so the retry and error
    # classification can be verified through the REAL API path with a
    # controlled gateway scenario. Default 0 = never active in production.
    FAULT_EMPTY_FIRST = int(os.environ.get("LLM_FAULT_EMPTY_FIRST", "0"))
    # LLM_FAULT_SKIP_FIRST=M delays the fault window until after M healthy
    # calls (lets a test target a specific subsystem, e.g. the planner,
    # without faulting setup calls). Default 0 = no skip.
    FAULT_SKIP_FIRST = int(os.environ.get("LLM_FAULT_SKIP_FIRST", "0"))
    # LLM_FAULT_MALFORMED_JSON_FIRST=N serves the first N in-window calls
    # as NON-EMPTY but structurally broken JSON text (the R0 failure
    # class: the provider answers, the consumer's JSON parse fails).
    # Same window semantics as the empty-response fault; default 0.
    FAULT_MALFORMED_JSON_FIRST = int(
        os.environ.get("LLM_FAULT_MALFORMED_JSON_FIRST", "0"))
    # seeded from the env var at import time so a backend started with
    # LLM_FAULT_EMPTY_FIRST=N serves the first N calls as injected
    # no-text-block responses (test instrumentation only; default 0).
    _fault_remaining: list[int] = (
        [FAULT_EMPTY_FIRST] if FAULT_EMPTY_FIRST > 0 else [])
    _malformed_remaining: list[int] = (
        [FAULT_MALFORMED_JSON_FIRST] if FAULT_MALFORMED_JSON_FIRST > 0
        else [])
    _calls_so_far: list[int] = [0]

    def _generate_once(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        timeout: float | None = None,
        sdk_max_retries: int | None = None,
        stream: bool = False,
    ) -> str:
        """Wall-clock window stamping wrapper (slow-request diagnosis):
        every application attempt records its UTC start/end alongside the
        monotonic elapsed_ms — the elapsed value stays the in-process
        latency source; the wall stamps exist for cross-stage audit. The
        finally covers success AND every exception path without altering
        propagation."""
        _started_at = datetime.now(timezone.utc).isoformat()
        try:
            return self._generate_once_inner(
                messages, max_tokens, temperature, timeout,
                sdk_max_retries, stream)
        finally:
            self.last_attempt_window = {
                "started_at": _started_at,
                "ended_at": datetime.now(timezone.utc).isoformat(),
            }

    def _generate_once_inner(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        timeout: float | None = None,
        sdk_max_retries: int | None = None,
        stream: bool = False,
    ) -> str:
        type(self)._calls_so_far[0] += 1
        _in_fault_window = (type(self)._calls_so_far[0]
                            > type(self).FAULT_SKIP_FIRST)
        if type(self).FAULT_SKIP_FIRST > 0 or type(self)._fault_remaining \
                or type(self)._malformed_remaining:
            logger.info(
                "LLM call #%s (fault_window=%s, empty_left=%s, "
                "malformed_left=%s)",
                type(self)._calls_so_far[0], _in_fault_window,
                (type(self)._fault_remaining[0]
                 if type(self)._fault_remaining else 0),
                (type(self)._malformed_remaining[0]
                 if type(self)._malformed_remaining else 0))
        if _in_fault_window and type(self)._fault_remaining \
                and type(self)._fault_remaining[0] > 0:
            type(self)._fault_remaining[0] -= 1
            logger.warning(
                "LLM FAULT INJECTION (test instrumentation): serving a "
                "no-text-block response; call=%s skip=%s remaining=%s",
                type(self)._calls_so_far[0],
                type(self).FAULT_SKIP_FIRST,
                type(self)._fault_remaining[0])
            raise LLMEmptyResponseError(
                "LLM response contained no text block (injected)",
                context={"injected": True, "attempts_remaining":
                         type(self)._fault_remaining[0]},
            )
        if _in_fault_window and type(self)._malformed_remaining \
                and type(self)._malformed_remaining[0] > 0:
            type(self)._malformed_remaining[0] -= 1
            logger.warning(
                "LLM FAULT INJECTION (test instrumentation): serving a "
                "non-empty MALFORMED-JSON response; call=%s skip=%s "
                "remaining=%s",
                type(self)._calls_so_far[0],
                type(self).FAULT_SKIP_FIRST,
                type(self)._malformed_remaining[0])
            # mirrors the live U00008 failure: text present, JSON broken
            # mid-string — a JSONDecodeError at the consumer, NOT an
            # empty response (the A-fix provider retry must NOT fire).
            return ('{"goal_resolution": "INFERRED", "goal_type": '
                    '"policy_check", "ambiguous_goal_types": [], '
                    '"reason": "unterminat')
        try:
            logger.debug(f"Sending {len(messages)} messages to LLM (model: {self.model})")
            # Q2: GU-specific request-scoped timeout / SDK retry policy via
            # the SDK's official with_options — the shared client (and its
            # 60s/2-retry defaults for other stages) is never mutated.
            _client = self.client
            if timeout is not None or sdk_max_retries is not None:
                _opts: dict = {}
                if timeout is not None:
                    _opts["timeout"] = timeout
                if sdk_max_retries is not None:
                    _opts["max_retries"] = sdk_max_retries
                _client = self.client.with_options(**_opts)
            if stream:
                # Q2: GU uses the SDK's official streaming API — keeps the
                # connection alive during long thinking, accumulates the
                # FINAL message so downstream parsing is unchanged.
                with _client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=self._system_for_stream(messages),
                    messages=messages,
                ) as _stream:
                    _first_event_ms = None
                    _t_stream = __import__("time").monotonic()
                    _event_count = 0
                    for _event in _stream:
                        _event_count += 1
                        if _first_event_ms is None:
                            _first_event_ms = int(
                                (__import__("time").monotonic()
                                 - _t_stream) * 1000)
                    response = _stream.get_final_message()
                _final_usage = getattr(response, "usage", None)
                self.last_response_meta = {
                    "stop_reason": getattr(response, "stop_reason", None),
                    "content_kinds": [getattr(b, "type", "?")
                                      for b in response.content],
                    "text_chars": sum(
                        len(getattr(b, "text", "") or "")
                        for b in response.content
                        if getattr(b, "type", None) == "text"),
                    "usage_input_tokens": (
                        getattr(_final_usage, "input_tokens", None)
                        if _final_usage is not None else None),
                    "usage_output_tokens": (
                        getattr(_final_usage, "output_tokens", None)
                        if _final_usage is not None else None),
                    "max_tokens": max_tokens,
                    "stream_completed": True,
                    "first_event_ms": _first_event_ms,
                    "event_count": _event_count,
                }
            else:
                response = _client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages,
                )

            # Per-attempt response metadata (safe fields only): usage,
            # stop reason, content-block structure, text length. No prompt
            # or response bodies are retained here.
            _usage = getattr(response, "usage", None)
            _text_chars = 0
            _prev_meta = getattr(self, "last_response_meta", {}) or {}
            self.last_response_meta = {
                "stop_reason": getattr(response, "stop_reason", None),
                "content_kinds": [getattr(b, "type", "?")
                                  for b in response.content],
                # U00008 planner diagnostics: per-block type + size only —
                # never the block content (no prompt/reasoning leakage).
                "content_blocks": [
                    {"type": getattr(b, "type", "?"),
                     "chars": len(getattr(b, "text", "")
                                  or getattr(b, "thinking", "") or "")}
                    for b in response.content],
                "stream": bool(stream),
                "text_chars": sum(
                    len(getattr(b, "text", "") or "")
                    for b in response.content
                    if getattr(b, "type", None) == "text"),
                "usage_input_tokens": (
                    getattr(_usage, "input_tokens", None)
                    if _usage is not None else None),
                "usage_output_tokens": (
                    getattr(_usage, "output_tokens", None)
                    if _usage is not None else None),
                "max_tokens": max_tokens,
                # streaming observability survives the shared rebuild
                **{k: _prev_meta[k] for k in
                   ("stream_completed", "first_event_ms", "event_count")
                   if k in _prev_meta},
            }
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    logger.debug(f"Received LLM response: {len(block.text)} chars")
                    return block.text

            # AL-2.10: the provider ANSWERED — the response simply carries
            # no usable text block (e.g. a thinking-style model consumed a
            # small max_tokens budget with reasoning blocks). A distinct
            # class keeps the failure taxonomy truthful: this is NOT an
            # unavailable provider.
            _meta = getattr(self, "last_response_meta", {}) or {}
            _kinds = _meta.get("content_kinds") or [
                getattr(b, "type", "?") for b in response.content]
            _detail = ("no_content_blocks" if not _kinds
                       else "non_text_blocks_only")
            raise LLMEmptyResponseError(
                "LLM response contained no text block",
                context={"stop_reason":
                         getattr(response, "stop_reason", None),
                         "content_kinds": _kinds,
                         "empty_detail": _detail,
                    "response_type": str(type(response.content)),
                    "stop_reason": getattr(response, "stop_reason", None),
                },
            )

        except APITimeoutError as e:
            logger.error(f"LLM request timed out: {str(e)}")
            raise LLMTimeoutError(
                f"LLM request timed out after {self.DEFAULT_TIMEOUT}s",
                context={"timeout": self.DEFAULT_TIMEOUT},
            ) from e

        except AnthropicRateLimitError as e:
            logger.error(f"LLM rate limit exceeded: {str(e)}")
            # SDK >=0.1x carries the hint in response headers, not as an
            # attribute — extract from the real location so the 429's
            # retry_after semantics actually survive classification.
            retry_after = getattr(e, "retry_after", None)
            if retry_after is None:
                _resp = getattr(e, "response", None)
                retry_after = (_resp.headers.get("retry-after")
                               if _resp is not None else None)
            raise LLMRateLimitError(
                "LLM rate limit exceeded - please retry after a delay",
                context={"retry_after": retry_after},
            ) from e

        except APIStatusError as e:
            status_code = e.status_code
            logger.error(f"LLM API returned error status {status_code}: {str(e)}")

            if status_code == 401:
                raise LLMConfigurationError(
                    "LLM authentication failed - check API key",
                    context={"status_code": status_code},
                ) from e
            elif status_code == 400:
                raise LLMConfigurationError(
                    f"LLM request validation failed: {str(e)}",
                    context={"status_code": status_code, "error": str(e)},
                ) from e
            else:
                raise LLMError(
                    f"LLM API error: {str(e)}",
                    context={"status_code": status_code},
                ) from e

        except APIConnectionError as e:
            logger.error(f"LLM connection error: {str(e)}")
            raise LLMError(
                f"Failed to connect to LLM API: {str(e)}",
                context={"base_url": self.base_url},
            ) from e

        except LLMEmptyResponseError:
            raise                  # already truthfully classified (AL-2.10)

        except Exception as e:
            logger.error(f"Unexpected LLM error: {str(e)}", exc_info=True)
            raise LLMError(
                f"Unexpected LLM error: {str(e)}",
                context={"error_type": type(e).__name__},
            ) from e


# Fault-injection arming guard (test instrumentation, default OFF): both
# switches are env-opt-in and inert unless a process is EXPLICITLY started
# with them. If armed, announce it loudly once so no test-only process can
# be mistaken for a production one in the logs.
if (ClaudeProvider.FAULT_EMPTY_FIRST > 0
        or ClaudeProvider.FAULT_SKIP_FIRST > 0
        or ClaudeProvider.FAULT_MALFORMED_JSON_FIRST > 0):
    logger.warning(
        "LLM FAULT INJECTION ARMED (TEST INSTRUMENTATION — never enable "
        "outside a controlled test process): LLM_FAULT_EMPTY_FIRST=%s, "
        "LLM_FAULT_MALFORMED_JSON_FIRST=%s, LLM_FAULT_SKIP_FIRST=%s; "
        "LLM calls after the first %s will be served injected "
        "no-text-block or malformed-JSON responses until the fault "
        "budgets are exhausted.",
        ClaudeProvider.FAULT_EMPTY_FIRST,
        ClaudeProvider.FAULT_MALFORMED_JSON_FIRST,
        ClaudeProvider.FAULT_SKIP_FIRST,
        ClaudeProvider.FAULT_SKIP_FIRST)