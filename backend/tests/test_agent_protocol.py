import pytest

from app.agent_protocol import parse_agent_decision


def test_parse_tool_call():
    text = """
    {
      "action": "tool_call",
      "tool_request": {
        "tool": "policy_search",
        "args": {
          "query": "AML suspicious indicators",
          "top_k": 3
        }
      }
    }
    """

    decision = parse_agent_decision(text)

    assert decision.action == "tool_call"
    assert decision.tool_request is not None
    assert decision.tool_request.tool == "policy_search"
    assert decision.tool_request.args["top_k"] == 3


def test_parse_final():
    text = """
    {
      "action": "final"
    }
    """

    decision = parse_agent_decision(text)

    assert decision.action == "final"
    assert decision.tool_request is None


def test_invalid_json():
    with pytest.raises(ValueError):
        parse_agent_decision("not valid json")


def test_tool_call_requires_request():
    with pytest.raises(ValueError):
        parse_agent_decision(
            '{"action": "tool_call"}'
        )


def test_unknown_action():
    with pytest.raises(ValueError):
        parse_agent_decision(
            '{"action": "something_else"}'
        )