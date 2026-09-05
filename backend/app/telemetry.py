"""Telemetry extension point — a thin, observational, non-authoritative
capture layer for the Investigation Agent.

Design contract (Week 1):

- Telemetry is OBSERVATIONAL, never authoritative. It must never become
  part of finding truth, risk score, evidence truth, policy truth,
  capability truth, or any execution decision.
- Only structured plan OUTPUT and execution metadata are captured. No
  chain-of-thought, no hidden reasoning, no raw prompts/responses.
- Recording is best-effort and isolated at the telemetry boundary: a sink
  failure can never break investigation execution. Core application logic
  is never wrapped in broad exception handling to hide bugs.
- The default sink is no-op — production behavior is unchanged. Tests (and
  future backends) can attach an in-memory or external sink.

Event types: planner.started/completed/failed,
execution.started/completed/failed, semantic.evaluated.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --- version metadata -------------------------------------------------------
# Stable constants so future telemetry can answer: "which Agent / planner /
# prompt / model version produced this behavior?"

AGENT_VERSION = "1.0.0-week1"
PLANNER_VERSION = "planner_v2-1.0.0"
PROMPT_VERSION = "prompt-2026-09-04"   # INTENT → STEP GUIDANCE revision date
DEFAULT_MODEL = "glm-5.3"              # mirrors config default; overridden
#                                        per-event when settings are loaded


# --- event model ------------------------------------------------------------

class AgentTelemetryEvent(BaseModel):
    """One structured telemetry event. Common fields identify the run;
    planner/execution/semantic field groups are optional — never force
    every event to carry every field."""

    event_type: str                      # planner.started/completed/failed,
                                         # execution.started/completed/failed,
                                         # semantic.evaluated
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request_id: str | None = None        # user request text (bounded)
    investigation_id: str | None = None
    case_id: str | None = None
    focused_finding_id: str | None = None
    agent_version: str = AGENT_VERSION
    planner_version: str | None = PLANNER_VERSION
    prompt_version: str | None = PROMPT_VERSION
    model: str | None = DEFAULT_MODEL

    # planner group
    planner_status: str | None = None    # completed | failed
    plan_step_types: list[str] | None = None
    plan_arguments: list[dict] | None = None
    planner_error: str | None = None     # bounded failure code/detail
    planner_latency_ms: float | None = None

    # execution group
    execution_status: str | None = None  # completed | failed
    executed_step_types: list[str] | None = None
    tool_names: list[str] | None = None
    execution_latency_ms: float | None = None

    # semantic-evaluation group
    semantic_success: bool | None = None
    failure_category: str | None = None
    scenario_id: str | None = None       # eval runs only


# --- sink interface ----------------------------------------------------------

class TelemetrySink:
    """Sink protocol: implement record(event). Sinks must be fast and must
    never raise into the caller — the module-level emit() isolates them."""

    def record(self, event: AgentTelemetryEvent) -> None:   # pragma: no cover
        raise NotImplementedError


class NoopTelemetrySink(TelemetrySink):
    """Default sink: discards events. Production behavior is unchanged."""

    def record(self, event: AgentTelemetryEvent) -> None:
        return None


class InMemoryTelemetrySink(TelemetrySink):
    """Minimal capture sink for tests and evaluation runs."""

    def __init__(self):
        self.events: list[AgentTelemetryEvent] = []

    def record(self, event: AgentTelemetryEvent) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()


# --- process-wide sink + emission -------------------------------------------

_sink: TelemetrySink = NoopTelemetrySink()


def set_sink(sink: TelemetrySink) -> None:
    """Attach a sink. Safe to call at startup (or in tests)."""
    global _sink
    _sink = sink


def get_sink() -> TelemetrySink:
    return _sink


def emit(event: AgentTelemetryEvent) -> None:
    """Best-effort emission: telemetry failures are logged and swallowed so
    they can never break investigation execution. Only the telemetry
    boundary is isolated — core application logic is untouched."""
    try:
        _sink.record(event)
    except Exception:                     # noqa: BLE001 — boundary isolation
        logger.warning("telemetry sink failed to record %s event",
                       event.event_type, exc_info=True)


def now_ms() -> float:
    """Monotonic clock in milliseconds (for latency start markers)."""
    return time.monotonic() * 1000.0


def elapsed_ms(start_ms: float) -> float:
    """Milliseconds elapsed since a now_ms() marker."""
    return round(time.monotonic() * 1000.0 - start_ms, 2)
