"""AL-3 scenario runner — executes scenarios against the canonical runtime."""
from __future__ import annotations
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx

from eval.ground_truth import GroundTruth
from eval.result_model import (
    CheckResult, JudgeScore, ScenarioResult, ScenarioStatus,
    TraceSummary, EvaluationRun,
)
from eval.evaluator import evaluate_hard_gates, classify_failure
from eval.llm_judge import judge_response

logger = logging.getLogger(__name__)


class AL3Runner:
    """Executes AL-3 scenarios against the canonical backend (:8001)."""

    def __init__(self, backend_url: str = "http://localhost:8001",
                 llm: Any = None):
        self.backend = backend_url
        self.client = httpx.Client(timeout=120)
        self._llm = llm

    def _create_investigation(self, case_id: str) -> str:
        r = self.client.post(f"{self.backend}/api/v2/investigations",
                             json={"case_id": case_id})
        return r.json()["investigation"]["investigation_id"]

    def _turn(self, iid: str, message: str) -> dict:
        r = self.client.post(f"{self.backend}/api/v2/investigations/{iid}/turn",
                             json={"message": message})
        return r.json()

    def _goal(self, iid: str, goal: str) -> tuple[str, dict]:
        r = self.client.post(
            f"{self.backend}/api/v2/investigations/{iid}/goal",
            json={"goal": goal})
        tid = r.json()["task_id"]
        for _ in range(120):
            try:
                a = self.client.get(
                    f"{self.backend}/api/v2/tasks/{tid}/activity", timeout=15).json()
                if a.get("is_terminal"):
                    break
            except Exception:
                pass
            time.sleep(3)
        d = self.client.get(f"{self.backend}/api/v2/tasks/{tid}", timeout=15).json()
        return tid, {
            "agent_status": a.get("agent_status"),
            "stop_reason": a.get("stop_reason"),
            "iteration": a.get("iteration"),
            "plan": [(p.get("type"), p.get("status"))
                     for p in (a.get("plan") or [])],
            "activity_tail": [
                {"event": it.get("event_type"),
                 "tool": it.get("tool_name"),
                 "decision": it.get("decision"),
                 "summary": (it.get("summary") or "")[:120]}
                for it in (a.get("activity") or [])[-10:]
            ],
            "response": d.get("task_response_text") or "",
        }

    def execute(self, scenario) -> TraceSummary:
        """Execute one scenario and capture the trace."""
        start = time.time()
        ctx = scenario.initial_context
        case_id = ctx.get("case_id", "U00010")
        iid = self._create_investigation(case_id)
        prompts = (scenario.user_prompt if isinstance(scenario.user_prompt, list)
                   else [scenario.user_prompt])

        if scenario.execution_mode == "turn":
            # single-turn: execute via /turn, capture response directly
            r = self._turn(iid, prompts[-1])
            resp = r.get("response") or ""
            focus = (r.get("context") or {}).get("focused_finding_id")
            tool_calls = []
            plan_steps = []
            if r.get("plan"):
                plan_steps = [s["type"] for s in r["plan"].get("steps", [])]
            if r.get("execution"):
                for tc in (r["execution"].get("tool_calls") or []):
                    tool_calls.append({
                        "name": tc.get("tool_name"),
                        "outcome": tc.get("outcome"),
                        "data": (tc.get("result") or {}).get("data", {})
                        if tc.get("result") else {},
                    })
            duration = int((time.time() - start) * 1000)
            return TraceSummary(
                tool_calls=tool_calls, plan_steps=plan_steps,
                iteration_count=1, stop_reason="goal_satisfied",
                agent_status=r.get("task", {}).get("status"),
                response_text=resp, investigation_id=iid,
                task_id=r.get("task", {}).get("task_id"),
                focused_finding_id=focus, duration_ms=duration,
            )

        # loop mode: multi-turn intake + /goal
        for i, prompt in enumerate(prompts[:-1]):
            self._turn(iid, prompt)
        last_prompt = prompts[-1]
        tid, loop_info = self._goal(iid, last_prompt)

        resp = loop_info.get("response") or ""
        focus = None
        # try to get focus from context
        try:
            inv_data = self.client.get(
                f"{self.backend}/api/v2/investigations/{iid}", timeout=15).json()
            ctx_data = inv_data.get("context") or {}
            focus = ctx_data.get("focused_finding_id")
        except Exception:
            pass

        tool_calls = []
        # extract tool calls from activity tail
        for a_item in loop_info.get("activity_tail", []):
            if a_item.get("tool"):
                tool_calls.append({"name": a_item["tool"],
                                   "outcome": "success",
                                   "data": {}})

        duration = int((time.time() - start) * 1000)
        return TraceSummary(
            tool_calls=tool_calls,
            plan_steps=[s[0] for s in loop_info.get("plan", []) if s[1] == "completed"],
            iteration_count=loop_info.get("iteration") or 0,
            stop_reason=loop_info.get("stop_reason"),
            agent_status=loop_info.get("agent_status"),
            response_text=resp,
            investigation_id=iid,
            task_id=tid,
            focused_finding_id=focus,
            duration_ms=duration,
        )

    def evaluate(self, scenario, trace: TraceSummary,
                 use_llm_judge: bool = True) -> ScenarioResult:
        """Evaluate a trace against the scenario's GroundTruth.

        Status logic (AL-2.13 robustness):
        - Hard gates determine PASS/FAIL (deterministic, always available)
        - LLM judge adds quality scores when available
        - Judge unavailable → status from gates only, judge_scores=[],
          judge_available=False (evaluator error ≠ Agent quality PARTIAL)
        """
        gt = scenario.ground_truth
        gates = evaluate_hard_gates(gt, trace)
        judge_scores: list[JudgeScore] = []
        judge_available = False

        if use_llm_judge and gt.expected_key_facts:
            try:
                if self._llm is None:
                    from app.llm_provider import ClaudeProvider
                    self._llm = ClaudeProvider()
                gt_summary = {
                    "expected_key_facts": gt.expected_key_facts,
                    "prohibited_content": gt.prohibited_content,
                    "expected_answer_elements": gt.expected_answer_elements,
                }
                raw, judge_available = judge_response(
                    self._llm,
                    scenario.user_prompt if isinstance(scenario.user_prompt, str)
                    else scenario.user_prompt[-1],
                    trace.response_text[:3000],
                    gt_summary)
                judge_scores = [
                    JudgeScore(dimension=r["dimension"], score=r["score"],
                               rationale=r.get("rationale", ""))
                    for r in raw
                ]
            except Exception as e:
                logger.warning("LLM judge error for %s: %s",
                               scenario.scenario_id, e)
                judge_available = False

        # Status: deterministic gates are the primary signal
        all_gates_pass = all(c.passed for c in gates)
        taxonomy = classify_failure(gates) if not all_gates_pass else None

        if not all_gates_pass:
            status = ScenarioStatus.FAIL
        elif judge_available and judge_scores:
            avg = sum(j.score for j in judge_scores) / len(judge_scores)
            status = (ScenarioStatus.PASS if avg >= 1.5
                      else ScenarioStatus.PARTIAL)
        elif judge_available is False and use_llm_judge and gt.expected_key_facts:
            # Judge couldn't run but deterministic gates passed:
            # status is determined by gates alone; judge unavailability
            # is recorded separately (NOT an Agent quality PARTIAL)
            status = ScenarioStatus.PASS
        else:
            status = ScenarioStatus.PASS

        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            status=status,
            hard_gate_results=gates,
            llm_judge_scores=judge_scores,
            judge_available=judge_available,
            trace=trace,
            failure_taxonomy=taxonomy,
        )

    def run_scenario(self, scenario, use_llm_judge: bool = True) -> ScenarioResult:
        trace = self.execute(scenario)
        return self.evaluate(scenario, trace, use_llm_judge)

    def run_all(self, scenarios: list, use_llm_judge: bool = True,
                max_scenarios: int | None = None) -> dict[str, ScenarioResult]:
        results = {}
        targets = scenarios[:max_scenarios] if max_scenarios else scenarios
        for s in targets:
            logger.info("Running scenario %s: %s", s.scenario_id, s.description)
            try:
                results[s.scenario_id] = self.run_scenario(s, use_llm_judge)
                logger.info("  %s → %s", s.scenario_id,
                            results[s.scenario_id].status.value)
            except Exception as e:
                logger.exception("Scenario %s execution error", s.scenario_id)
                results[s.scenario_id] = ScenarioResult(
                    scenario_id=s.scenario_id,
                    status=ScenarioStatus.ERROR,
                    failure_detail=str(e),
                )
        return results
