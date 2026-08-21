import json
import re
from typing import Any

from app.agent_protocol import parse_agent_decision
from app.llm_provider import ClaudeProvider
from app.tools import TOOL_REGISTRY, execute_tool


SYSTEM_PROMPT = """
You are an investigation agent.

Your job is to investigate a risk case by using the available tools.

Available tools:

1. policy_search
   Use this to retrieve relevant policy snippets.
   You MUST provide these arguments:
   {
     "query": string,   // Your semantic search query
     "top_k": integer  // Number of results (usually 3-5)
   }

2. evidence_fetch
   Use this to retrieve canonical evidence and unified findings.
   The runtime will automatically provide the case_id from the user intent.
   Output: {"action":"tool_call","tool_request":{"tool":"evidence_fetch","args":{}}}

3. compose_structured_result
   Use this to generate structured findings and actions.
   The runtime will automatically provide evidence and policies from previous tool results.
   You do NOT need to reproduce those objects.
   Output: {"action":"tool_call","tool_request":{"tool":"compose_structured_result","args":{}}}

4. citation_validate
   Use this to validate claim-level citations.
   The runtime will automatically provide claims and policies from previous results.
   You do NOT need to reproduce those objects.
   Output: {"action":"tool_call","tool_request":{"tool":"citation_validate","args":{}}}

Rules:

- You may only use the available tools.
- Never invent evidence.
- ALWAYS output compact, valid JSON.
- NEVER use Markdown code fences like ```json. Output raw JSON only.
- When you need information, call the appropriate tool.
- After receiving a tool result, decide what to do next based on the result.
- You may call multiple tools in sequence as needed.
- For tools that use previous results (compose_structured_result, citation_validate),
  use empty args {}. The runtime will inject the required context.
- When the investigation is complete, return a final action.

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
        return match.group(1) if match else None

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
            if isinstance(claims, list) and len(claims) == 0:
                # Fallback for legacy format
                claims = structured_result
            return {
                "claims": claims,
                "policies": policies,
            }

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
            "error": ...
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

        while step_number < max_steps:
            step_number += 1

            try:
                decision = self.decide(messages)
            except Exception as e:
                return {
                    "status": "error",
                    "steps": steps,
                    "error": f"LLM decision failed at step {step_number}: {e}",
                }

            # Check if LLM wants to finish
            if decision.action == "final":
                return {
                    "status": "completed",
                    "steps": steps,
                    "final_decision": decision.model_dump(),
                }

            # Validate tool_call action
            if decision.action != "tool_call":
                return {
                    "status": "error",
                    "steps": steps,
                    "error": (
                        f"Invalid action at step {step_number}: "
                        f"expected 'tool_call' or 'final', got '{decision.action}'"
                    ),
                }

            # Get tool request
            tool_request = decision.tool_request
            if tool_request is None:
                return {
                    "status": "error",
                    "steps": steps,
                    "error": (
                        f"Agent returned 'tool_call' without tool_request "
                        f"at step {step_number}"
                    ),
                }

            tool_name = tool_request.tool
            llm_args = tool_request.args or {}

            # Normalize and inject runtime arguments
            try:
                normalized_args = self._normalize_tool_args(
                    tool_name,
                    llm_args,
                    runtime_context,
                    user_intent,
                )
            except Exception as e:
                return {
                    "status": "error",
                    "steps": steps,
                    "error": (
                        f"Argument normalization failed at step {step_number}: "
                        f"tool={tool_name}, error={e}"
                    ),
                }

            # Execute the tool with normalized arguments
            try:
                tool_result = execute_tool(tool_name, normalized_args)
            except Exception as e:
                return {
                    "status": "error",
                    "steps": steps,
                    "error": (
                        f"Tool execution failed at step {step_number}: "
                        f"tool={tool_name}, error={e}"
                    ),
                }

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
        return {
            "status": "max_steps_exceeded",
            "steps": steps,
            "error": f"Agent did not complete within {max_steps} steps",
        }