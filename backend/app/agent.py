import json
import logging
import re
from typing import Any

from app.agent_protocol import parse_agent_decision
from app.exceptions import (
    AgentExecutionError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    MaxStepsExceededError,
    ToolArgumentError,
    ToolExecutionError,
    ToolNotFoundError,
)
from app.llm_provider import ClaudeProvider
from app.tools import TOOL_REGISTRY, execute_tool


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are an investigation agent.

Your job is to investigate a risk case by using the available tools.

Available tools:

1. risk_case_fetch (PRIMARY - Authoritative Investigation Context)
   Use this to retrieve the complete authoritative investigation context from Risk Platform.
   This provides:
   - Authoritative findings (with canonical names from Risk Platform)
   - Validated citations (with semantic support, not just policy ID checking)
   - Risk summary (scores, detection methods, recommended actions)
   - Explanation metadata

   You MUST provide these arguments:
   {
     "case_id": string  // The case ID to investigate (e.g., "U00299", "U00010")
   }

   The returned findings and citations are authoritative from Risk Platform.
   DO NOT attempt to recreate findings, validate citations, or apply Risk Platform logic.
   Trust the Risk Platform as the source of truth for risk domain information.

2. policy_search (Optional - Broader Policy Context)
   Use this to retrieve additional policy context beyond what Risk Platform provides.
   You MUST provide these arguments:
   {
     "query": string,   // Your semantic search query
     "top_k": integer  // Number of results (usually 3-5)
   }

Legacy tools (transitional - will be removed in Phase 2):
3. evidence_fetch - Superseded by risk_case_fetch
4. compose_structured_result - Superseded by risk_case_fetch
5. citation_validate - Superseded by risk_case_fetch

Rules:

- You may only use the available tools.
- Never invent evidence or findings.
- ALWAYS output compact, valid JSON.
- NEVER use Markdown code fences like ```json. Output raw JSON only.
- When investigating a case, START with risk_case_fetch to get authoritative context.
- After receiving tool results, decide what to do next based on the investigation needs.
- You may call multiple tools in sequence as needed.
- The Risk Platform provides authoritative domain capabilities (findings, citations).
- Your role is orchestration: decide which tools to call and when.

For a tool call, output exactly this structure:

{"action":"tool_call","tool_request":{"tool":"<tool_name>","args":{}}}

When the investigation is complete, output exactly:

{"action":"final"}
"""


class InvestigationAgent:
    def __init__(self):
        self.llm = ClaudeProvider()

    def decide(
        self,
        messages: list[dict[str, Any]],
    ):
        response = self.llm.generate(messages)
        return parse_agent_decision(response)

    def run_once(
        self,
        user_intent: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        context = context or {}

        messages = [
            {
                "role": "user",
                "content": (
                    f"{SYSTEM_PROMPT}\n\n"
                    f"Investigation task:\n"
                    f"{user_intent}\n\n"
                    f"Current context:\n"
                    f"{context}"
                ),
            }
        ]

        decision = self.decide(messages)

        if decision.action == "final":
            return {
                "type": "final",
                "decision": decision,
            }

        tool_request = decision.tool_request

        if tool_request is None:
            raise ValueError(
                "Agent returned tool_call without tool_request"
            )

        tool_name = tool_request.tool
        llm_args = tool_request.args or {}

        # Normalize and inject runtime arguments for consistency
        normalized_args = self._normalize_tool_args(
            tool_name,
            llm_args,
            runtime_context=context,  # Use provided context for dependencies
            user_intent=user_intent,
        )

        result = execute_tool(
            tool_name,
            normalized_args,
        )

        return {
            "type": "tool_result",
            "decision": decision,
            "tool_name": tool_name,
            "tool_args": normalized_args,  # Return normalized args
            "result": result,
        }

    def _extract_case_id(self, user_intent: str) -> str | None:
        """
        Extract case_id from user intent using deterministic regex.
        Supports: "Investigate case U00299", "case U00299", etc.
        """
        match = re.search(r"\bcase\s+([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b", user_intent, re.IGNORECASE)
        if not match:
            return None

        raw_case_id = match.group(1)
        # Normalize to canonical U-prefixed format for Risk Platform
        # Risk Platform uses U-prefix as canonical identifier (e.g., U00010)
        # User may enter "00010" -> normalize to "U00010"
        # User may enter "U00010" -> already canonical, return as-is
        if not raw_case_id.startswith('U'):
            return f"U{raw_case_id}"
        return raw_case_id

    def _normalize_tool_args(
        self,
        tool_name: str,
        llm_args: dict[str, Any],
        runtime_context: dict[str, Any],
        user_intent: str,
    ) -> dict[str, Any]:
        """
        Normalize and inject runtime context into tool arguments.

        This implements the architecture where:
        - LLM provides semantic arguments (e.g., policy_search query/top_k)
        - Runtime injects structural arguments from previous tool results

        Args:
            tool_name: The tool being called
            llm_args: Arguments provided by the LLM (may be empty {})
            runtime_context: Accumulated results from previous tool calls by tool name
                           OR generic context dict (for run_once compatibility)
            user_intent: Original user intent (for case_id extraction)

        Returns:
            Normalized arguments dict ready for execute_tool()
        """
        # Validate tool name exists in registry
        if tool_name not in TOOL_REGISTRY:
            raise ValueError(f"Unknown tool: {tool_name}")

        if tool_name == "evidence_fetch":
            # Inject case_id from user intent
            case_id = self._extract_case_id(user_intent)
            if not case_id:
                raise ValueError(
                    f"Cannot call evidence_fetch: no case_id found in user_intent: '{user_intent}'"
                )
            return {"case_id": case_id}

        elif tool_name == "compose_structured_result":
            # Inject evidence and policies from runtime context
            # Support both by-tool-name format (run()) and legacy format (run_once)
            evidence = runtime_context.get("evidence_fetch") or runtime_context.get("evidence")
            policies = runtime_context.get("policy_search") or runtime_context.get("policies")

            if not evidence:
                raise ValueError(
                    "Cannot call compose_structured_result: evidence not found in context. "
                    "Call evidence_fetch first or provide 'evidence' in context."
                )
            if not policies:
                raise ValueError(
                    "Cannot call compose_structured_result: policies not found in context. "
                    "Call policy_search first or provide 'policies' in context."
                )
            return {
                "evidence": evidence,
                "policies": policies,
                "user_intent": user_intent,
            }

        elif tool_name == "citation_validate":
            # Inject claims and policies from runtime context
            # Support both by-tool-name format and legacy format
            structured_result = runtime_context.get("compose_structured_result") or runtime_context.get("structured_result")
            policies = runtime_context.get("policy_search") or runtime_context.get("policies")

            if not structured_result:
                raise ValueError(
                    "Cannot call citation_validate: structured_result not found in context. "
                    "Call compose_structured_result first or provide 'structured_result' in context."
                )
            if not policies:
                raise ValueError(
                    "Cannot call citation_validate: policies not found in context. "
                    "Call policy_search first or provide 'policies' in context."
                )

            claims = structured_result.get("findings", [])
            if not isinstance(claims, list):
                # Wrong type - this is an error
                raise ValueError(
                    f"Cannot call citation_validate: 'findings' must be a list. "
                    f"Got: {type(claims).__name__}"
                )
            # Empty claims array is valid for low-risk cases - proceed anyway
            return {
                "claims": claims,
                "policies": policies,
            }

        elif tool_name == "risk_case_fetch":
            # Inject case_id from user intent (like evidence_fetch)
            case_id = self._extract_case_id(user_intent)
            if not case_id:
                raise ValueError(
                    f"Cannot call risk_case_fetch: no case_id found in user_intent: '{user_intent}'"
                )
            return {"case_id": case_id}

        elif tool_name == "policy_search":
            # LLM must provide query and top_k for semantic search
            if "query" not in llm_args:
                raise ValueError(
                    "policy_search requires 'query' argument from LLM"
                )
            # Use provided args, with default top_k if not specified
            return {
                "query": llm_args["query"],
                "top_k": llm_args.get("top_k", 3),
            }

        else:
            # Unknown tool - should have been caught by registry check above
            raise ValueError(f"Unknown tool: {tool_name}")

    def run(
        self,
        user_intent: str,
        max_steps: int = 8,
    ) -> dict[str, Any]:
        """
        Run the full agent loop until completion or max_steps.

        This is the main agent loop that:
        1. Initializes messages with system prompt and user intent
        2. Calls decide() to get LLM decision
        3. If action == "tool_call": normalize args, execute tool, append result to messages, continue
        4. If action == "final": stop and return completed status
        5. Stops if max_steps is reached

        The runtime maintains a context dict that stores tool results by tool name,
        enabling automatic argument injection for tools that depend on previous results.

        Returns:
        {
            "status": "completed" | "max_steps_exceeded" | "error",
            "steps": [...],
            "final_decision": ...,
            "error": ...,
            "error_details": {...}
        }
        """
        messages = [
            {
                "role": "user",
                "content": (
                    f"{SYSTEM_PROMPT}\n\n"
                    f"Investigation task:\n"
                    f"{user_intent}"
                ),
            }
        ]

        steps = []
        step_number = 0

        # Runtime context: stores tool results by tool name for argument injection
        runtime_context: dict[str, Any] = {}

        try:
            while step_number < max_steps:
                step_number += 1

                # Get LLM decision with error handling
                try:
                    decision = self.decide(messages)
                    logger.debug(f"Step {step_number}: LLM decision = {decision.action}")
                except Exception as e:
                    error_msg = f"LLM decision failed at step {step_number}: {str(e)}"
                    logger.error(error_msg, exc_info=True)

                    # Try to provide more specific error information
                    error_type = "unknown"
                    if "timeout" in str(e).lower():
                        error_type = "timeout"
                        raise LLMTimeoutError(
                            f"LLM request timed out at step {step_number}",
                            context={"step": step_number}
                        ) from e
                    elif "rate" in str(e).lower() or "quota" in str(e).lower():
                        error_type = "rate_limit"
                        raise LLMRateLimitError(
                            f"LLM rate limit exceeded at step {step_number}",
                            context={"step": step_number}
                        ) from e

                    raise LLMError(
                        error_msg,
                        context={"step": step_number, "original_error": str(e)}
                    ) from e

                # Check if LLM wants to finish
                if decision.action == "final":
                    logger.info(f"Agent completed successfully at step {step_number}")
                    return {
                        "status": "completed",
                        "steps": steps,
                        "final_decision": decision.model_dump(),
                    }

                # Validate tool_call action
                if decision.action != "tool_call":
                    error_msg = (
                        f"Invalid action at step {step_number}: "
                        f"expected 'tool_call' or 'final', got '{decision.action}'"
                    )
                    logger.error(error_msg)
                    raise AgentExecutionError(
                        error_msg,
                        step_number=step_number,
                        agent_state={"last_action": decision.action},
                    )

                # Get tool request
                tool_request = decision.tool_request
                if tool_request is None:
                    error_msg = (
                        f"Agent returned 'tool_call' without tool_request "
                        f"at step {step_number}"
                    )
                    logger.error(error_msg)
                    raise AgentExecutionError(
                        error_msg,
                        step_number=step_number,
                        agent_state={"decision": decision.model_dump()},
                    )

                tool_name = tool_request.tool
                llm_args = tool_request.args or {}

                logger.debug(f"Step {step_number}: Executing tool '{tool_name}'")

                # Normalize and inject runtime arguments
                try:
                    normalized_args = self._normalize_tool_args(
                        tool_name,
                        llm_args,
                        runtime_context,
                        user_intent,
                    )
                except ValueError as e:
                    # Argument normalization failed - this is a tool argument error
                    error_msg = (
                        f"Tool argument normalization failed at step {step_number}: "
                        f"tool={tool_name}, error={str(e)}"
                    )
                    logger.error(error_msg)
                    raise ToolArgumentError(
                        error_msg,
                        tool_name=tool_name,
                        args={"llm_args": llm_args, "available_context": list(runtime_context.keys())},
                    ) from e
                except Exception as e:
                    # Unexpected error during normalization
                    error_msg = (
                        f"Unexpected error during argument normalization at step {step_number}: "
                        f"tool={tool_name}, error={str(e)}"
                    )
                    logger.error(error_msg, exc_info=True)
                    raise AgentExecutionError(
                        error_msg,
                        step_number=step_number,
                        agent_state={"tool_name": tool_name, "llm_args": llm_args},
                    ) from e

                # Execute the tool with normalized arguments
                try:
                    tool_result = execute_tool(tool_name, normalized_args)
                    logger.debug(f"Step {step_number}: Tool '{tool_name}' completed successfully")
                except ValueError as e:
                    # Tool execution failed with a known error
                    error_msg = (
                        f"Tool execution failed at step {step_number}: "
                        f"tool={tool_name}, error={str(e)}"
                    )
                    logger.error(error_msg)
                    raise ToolExecutionError(
                        error_msg,
                        tool_name=tool_name,
                        args=normalized_args,
                    ) from e
                except Exception as e:
                    # Unexpected error during tool execution
                    error_msg = (
                        f"Unexpected tool execution error at step {step_number}: "
                        f"tool={tool_name}, error={str(e)}"
                    )
                    logger.error(error_msg, exc_info=True)
                    raise ToolExecutionError(
                        error_msg,
                        tool_name=tool_name,
                        args=normalized_args,
                    ) from e

                # Store result in runtime context for future argument injection
                runtime_context[tool_name] = tool_result

                # Record this step
                step_record = {
                    "step": step_number,
                    "tool_name": tool_name,
                    "tool_args": normalized_args,  # Record the actual args used
                    "result": tool_result,
                }
                steps.append(step_record)

                # Append assistant's tool decision to messages
                messages.append({
                    "role": "assistant",
                    "content": decision.model_dump_json(),
                })

                # Append tool result to messages for next LLM call
                messages.append({
                    "role": "user",
                    "content": (
                        f"Tool execution result:\n"
                        f"{json.dumps(tool_result, indent=2)}\n\n"
                        f"Decide what to do next."
                    ),
                })

            # Max steps reached
            error_msg = f"Agent did not complete within {max_steps} steps"
            logger.warning(error_msg)
            raise MaxStepsExceededError(
                error_msg,
                step_number=max_steps,
                agent_state={"steps_taken": len(steps), "max_steps": max_steps},
            )

        except (LLMError, ToolExecutionError, AgentExecutionError, MaxStepsExceededError) as e:
            # Known error types - convert to proper result format
            return {
                "status": "error" if not isinstance(e, MaxStepsExceededError) else "max_steps_exceeded",
                "steps": steps,
                "error": e.message,
                "error_details": e.to_dict() if hasattr(e, "to_dict") else {"type": type(e).__name__},
            }
        except Exception as e:
            # Unexpected error - log and return error status
            error_msg = f"Unexpected error in agent loop: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "steps": steps,
                "error": error_msg,
                "error_details": {"type": "UnexpectedError", "original_error": str(e)},
            }