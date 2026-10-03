"""AL-3 evaluation result model — structured, persisted, comparable."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class ScenarioStatus(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


@dataclass
class CheckResult:
    check_id: str
    expected: str
    observed: str
    passed: bool
    detail: str = ""


@dataclass
class JudgeScore:
    dimension: str
    score: int              # 0, 1, or 2
    rationale: str = ""
    confidence: float = 1.0


@dataclass
class TraceSummary:
    tool_calls: list[dict] = field(default_factory=list)
    plan_steps: list[str] = field(default_factory=list)
    iteration_count: int = 0
    stop_reason: str | None = None
    agent_status: str | None = None
    response_text: str = ""
    investigation_id: str | None = None
    task_id: str | None = None
    focused_finding_id: str | None = None
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class ScenarioResult:
    scenario_id: str
    status: ScenarioStatus
    hard_gate_results: list[CheckResult] = field(default_factory=list)
    llm_judge_scores: list[JudgeScore] = field(default_factory=list)
    judge_available: bool = True
    diagnostics: dict = field(default_factory=dict)
    trace: TraceSummary = field(default_factory=TraceSummary)
    failure_taxonomy: str | None = None
    failure_detail: str | None = None

    @property
    def all_gates_passed(self) -> bool:
        return all(c.passed for c in self.hard_gate_results)

    @property
    def avg_judge_score(self) -> float | None:
        if not self.llm_judge_scores:
            return None
        return sum(j.score for j in self.llm_judge_scores) / len(self.llm_judge_scores)

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "status": self.status.value,
            "hard_gates": [
                {"check_id": c.check_id, "expected": c.expected,
                 "observed": c.observed, "passed": c.passed, "detail": c.detail}
                for c in self.hard_gate_results
            ],
            "llm_judge": [
                {"dimension": j.dimension, "score": j.score,
                 "rationale": j.rationale}
                for j in self.llm_judge_scores
            ],
            "diagnostics": self.diagnostics,
            "trace": {
                "tool_calls": self.trace.tool_calls,
                "plan_steps": self.trace.plan_steps,
                "iteration_count": self.trace.iteration_count,
                "stop_reason": self.trace.stop_reason,
                "agent_status": self.trace.agent_status,
                "investigation_id": self.trace.investigation_id,
                "task_id": self.trace.task_id,
                "focused_finding_id": self.trace.focused_finding_id,
                "artifacts": self.trace.artifacts,
                "duration_ms": self.trace.duration_ms,
            },
            "failure_taxonomy": self.failure_taxonomy,
            "failure_detail": self.failure_detail,
        }


@dataclass
class EvaluationRun:
    run_id: str
    timestamp: str
    git_commit: str
    model_name: str
    scenario_version: str
    rp_data_hash: str | None
    baseline_ref: str | None
    results: dict[str, ScenarioResult] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "git_commit": self.git_commit,
            "model_name": self.model_name,
            "scenario_version": self.scenario_version,
            "rp_data_hash": self.rp_data_hash,
            "baseline_ref": self.baseline_ref,
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "summary": self.summary(),
        }

    def summary(self) -> dict:
        total = len(self.results)
        by_status: dict[str, list[str]] = {}
        for sid, r in self.results.items():
            by_status.setdefault(r.status.value, []).append(sid)
        axis_agg: dict[str, dict] = {}
        taxonomy_agg: dict[str, int] = {}
        for r in self.results.values():
            if r.failure_taxonomy:
                taxonomy_agg[r.failure_taxonomy] = \
                    taxonomy_agg.get(r.failure_taxonomy, 0) + 1
        return {
            "total_scenarios": total,
            "by_status": {k: len(v) for k, v in by_status.items()},
            "scenario_ids_by_status": by_status,
            "failure_taxonomy": taxonomy_agg,
        }
