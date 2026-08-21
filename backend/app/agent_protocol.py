from typing import Any

from pydantic import BaseModel, Field


class ToolRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    action: str
    tool_request: ToolRequest | None = None
    reasoning: str | None = None

import json


def parse_agent_decision(text: str) -> AgentDecision:
    """
    Parse and validate the LLM's JSON decision.
    The LLM is never allowed to directly execute a tool.
    """

    # Store original text for error reporting (sanitize if needed)
    raw_response = text.strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # Include the raw response in the error for debugging
        # Show first 200 chars to help diagnose without exposing secrets
        preview = raw_response[:200] if len(raw_response) <= 200 else raw_response[:200] + "..."
        raise ValueError(
            f"LLM returned invalid JSON: {exc}. "
            f"Raw response (first 200 chars): {preview}"
        ) from exc

    decision = AgentDecision.model_validate(payload)

    if decision.action == "tool_call":
        if decision.tool_request is None:
            raise ValueError(
                "tool_call action requires tool_request"
            )

    elif decision.action == "final":
        if decision.tool_request is not None:
            raise ValueError(
                "final action must not contain tool_request"
            )

    else:
        raise ValueError(
            f"Unsupported agent action: {decision.action}"
        )

    return decision