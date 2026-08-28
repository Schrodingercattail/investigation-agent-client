"""
Custom exceptions for the Investigation Agent system.

Provides specific exception types for different failure modes
to enable proper error handling and user messaging.
"""

from typing import Any


class InvestigationAgentError(Exception):
    """Base exception for all investigation agent errors."""

    def __init__(self, message: str, context: dict[str, Any] | None = None):
        self.message = message
        self.context = context or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dict for API responses."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "context": self.context,
        }


class LLMError(InvestigationAgentError):
    """Error in LLM communication or decision making."""

    pass


class LLMConfigurationError(LLMError):
    """LLM is misconfigured (missing API key, invalid model, etc.)."""

    pass


class LLMTimeoutError(LLMError):
    """LLM request timed out."""

    pass


class LLMRateLimitError(LLMError):
    """LLM rate limit exceeded."""

    pass


class ToolExecutionError(InvestigationAgentError):
    """Error in tool execution."""

    def __init__(self, message: str, tool_name: str, args: dict[str, Any] | None = None):
        self.tool_name = tool_name
        self.tool_args = args or {}  # Use tool_args to avoid conflict with Exception.args
        super().__init__(message, {"tool_name": tool_name, "args": self.tool_args})


class ToolNotFoundError(ToolExecutionError):
    """Requested tool does not exist."""

    pass


class ToolArgumentError(ToolExecutionError):
    """Invalid arguments provided to tool."""

    pass


class RiskPlatformError(InvestigationAgentError):
    """Error communicating with Risk Platform API."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(
            message,
            {"status_code": status_code, "response_body": response_body},
        )


class RiskPlatformUnavailableError(RiskPlatformError):
    """Risk Platform is temporarily unavailable."""

    pass


class RiskPlatformAuthenticationError(RiskPlatformError):
    """Authentication failed with Risk Platform."""

    pass


class AgentExecutionError(InvestigationAgentError):
    """Error during agent loop execution."""

    def __init__(
        self,
        message: str,
        step_number: int | None = None,
        agent_state: dict[str, Any] | None = None,
    ):
        self.step_number = step_number
        self.agent_state = agent_state or {}
        super().__init__(
            message,
            {"step_number": step_number, "agent_state": agent_state},
        )


class MaxStepsExceededError(AgentExecutionError):
    """Agent did not complete within maximum allowed steps."""

    pass


class TaskValidationError(InvestigationAgentError):
    """Error in task validation."""

    pass


class StoreError(InvestigationAgentError):
    """Error in task store operations."""

    pass
