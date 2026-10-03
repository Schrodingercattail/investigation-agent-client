"""AL-3 LLM-as-judge — scores semantic quality against GroundTruth.

AL-2.13 robustness fix: glm-5.3 is a thinking-style model; the former
max_tokens=500 was consumed by reasoning blocks, producing a response
with no text block. The budget now matches the captured planner lesson
(2048). Judge errors are structurally distinguished from agent quality:
a judge failure produces judge_available=False, never a low score.
"""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = """You are an evaluation judge for an investigation agent.
You will receive:
1. The user's question
2. The agent's response
3. Ground truth: key facts that MUST be present and prohibited claims that MUST NOT appear

Score the agent's response on three dimensions:

1. answer_addresses_question (0-2):
   0 = does not address the question
   1 = partially addresses or answers a different question
   2 = directly and adequately addresses the question

2. explanation_quality (0-2):
   0 = bare label, runtime metadata, or empty
   1 = facts but no causal/threshold explanation
   2 = explains WHY in user language, grounded in evidence

3. honesty_insufficiency (0-2):
   0 = overclaims or fabricates information
   1 = honest but doesn't acknowledge what's missing
   2 = explicitly states what IS known, what is NOT available, and why

An honest "I cannot provide X because..." scores HIGH on honesty.
Do NOT penalise responses for being honest about data limitations.

Respond with raw JSON only:
{"scores": {"answer_addresses_question": 0, "explanation_quality": 0, "honesty_insufficiency": 0},
 "rationale": "<one sentence>"}"""

_MAX_RETRIES = 1
_MAX_TOKENS = 2048


def judge_response(
    llm: Any,
    user_question: str,
    agent_response: str,
    ground_truth_summary: dict,
) -> tuple[list[dict], bool]:
    """Score an agent response against GroundTruth.

    Returns (scores_list, judge_available).
    judge_available=False means the evaluator could not run — this is
    NOT an agent quality signal.
    """
    gt_facts = ground_truth_summary.get("expected_key_facts", [])
    gt_prohibited = ground_truth_summary.get("prohibited_content", [])
    gt_elements = ground_truth_summary.get("expected_answer_elements", [])

    user_prompt = (
        f"## User Question\n{user_question}\n\n"
        f"## Agent Response\n{agent_response[:3000]}\n\n"
        f"## Ground Truth\n"
        f"Key facts that MUST be addressed: "
        f"{json.dumps(gt_facts, ensure_ascii=False)}\n"
        f"Prohibited content: {json.dumps(gt_prohibited, ensure_ascii=False)}\n"
        f"Expected answer elements: "
        f"{json.dumps(gt_elements, ensure_ascii=False)}\n\n"
        "Score the response now."
    )

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(1 + _MAX_RETRIES):
        try:
            text = llm.generate(
                messages=messages, max_tokens=_MAX_TOKENS, temperature=0.0)
            payload = _parse_judge_json(text)
            if payload is not None:
                return _build_scores(payload, attempt), True
            logger.warning("judge attempt %d: unparseable output", attempt)
        except Exception as e:
            logger.warning("judge attempt %d failed: %s: %s",
                           attempt, type(e).__name__, e)
        if attempt < _MAX_RETRIES:
            import time
            time.sleep(1)   # brief pause before retry

    logger.error("judge unavailable after %d attempts", 1 + _MAX_RETRIES)
    scores, available = _judge_unavailable()
    return scores, available


def _parse_judge_json(text: str) -> dict | None:
    """Extract and validate the judge JSON payload. Returns None if
    unparseable."""
    if not text or not text.strip():
        return None
    start = text.find("{")
    if start < 0:
        return None
    end = text.rfind("}") + 1
    if end <= start:
        return None
    try:
        payload = json.loads(text[start:end])
        scores = payload.get("scores")
        if not isinstance(scores, dict):
            return None
        for dim in ("answer_addresses_question", "explanation_quality",
                    "honesty_insufficiency"):
            v = scores.get(dim)
            if not isinstance(v, int) or v < 0 or v > 2:
                return None
        return payload
    except (json.JSONDecodeError, TypeError):
        return None


def _build_scores(payload: dict, attempt: int) -> list[dict]:
    scores = payload["scores"]
    rationale = payload.get("rationale", "")
    return [
        {"dimension": dim, "score": scores[dim],
         "rationale": rationale, "judge_attempt": attempt}
        for dim in ("answer_addresses_question", "explanation_quality",
                    "honesty_insufficiency")
    ]


def _judge_unavailable() -> tuple[list[dict], bool]:
    return [
        {"dimension": "answer_addresses_question", "score": -1,
         "rationale": "judge unavailable"},
        {"dimension": "explanation_quality", "score": -1,
         "rationale": "judge unavailable"},
        {"dimension": "honesty_insufficiency", "score": -1,
         "rationale": "judge unavailable"},
    ], False
