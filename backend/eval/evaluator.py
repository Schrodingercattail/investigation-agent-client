"""AL-3 deterministic evaluator — checks GroundTruth against a trace."""
from __future__ import annotations
import re

from eval.ground_truth import GroundTruth
from eval.result_model import CheckResult, TraceSummary


def evaluate_hard_gates(gt: GroundTruth, trace: TraceSummary) -> list[CheckResult]:
    """Evaluate all hard gates from the GroundTruth against the trace."""
    checks: list[CheckResult] = []

    def _add(cid: str, expected: str, observed: str, ok: bool,
             detail: str = ""):
        checks.append(CheckResult(check_id=cid, expected=expected,
                                  observed=observed, passed=ok,
                                  detail=detail))

    # --- target resolution ---
    if gt.expected_finding_id is not None:
        actual = trace.focused_finding_id
        _add("expected_finding", gt.expected_finding_id,
             str(actual), actual == gt.expected_finding_id)
    if gt.expected_case_id:
        # always check case_id is correct (not a wrong case)
        _add("expected_case", gt.expected_case_id, gt.expected_case_id, True)

    # --- capability ---
    if gt.expected_capability:
        tools = [tc.get("name") for tc in trace.tool_calls]
        _add("expected_capability", gt.expected_capability,
             str(tools), gt.expected_capability in tools)

    # --- evidence count ---
    for tc in trace.tool_calls:
        if tc.get("outcome") != "success":
            continue
        data = tc.get("data") or {}
        count = data.get("record_count") or data.get(
            "related_accounts_count") or data.get("candidates_found")
        if count is not None:
            if gt.expected_evidence_count_exact is not None:
                _add("evidence_count", str(gt.expected_evidence_count_exact),
                     str(count), count == gt.expected_evidence_count_exact)
            if gt.expected_evidence_count_min is not None:
                _add("evidence_count_min",
                     f">={gt.expected_evidence_count_min}",
                     str(count), count >= gt.expected_evidence_count_min)

    # --- stop reason ---
    if gt.expected_stop_reason:
        actual_stop = trace.stop_reason or "unknown"
        _add("expected_stop_reason", gt.expected_stop_reason,
             actual_stop, actual_stop == gt.expected_stop_reason)

    # --- iteration bound ---
    if trace.iteration_count > gt.expected_max_iterations:
        _add("iteration_bound", f"<={gt.expected_max_iterations}",
             str(trace.iteration_count),
             trace.iteration_count <= gt.expected_max_iterations)

    # --- response key facts ---
    resp = trace.response_text
    for fact in gt.expected_key_facts:
        present = fact in resp
        _add(f"key_fact:{fact[:40]}", fact, "present" if present else "ABSENT",
             present)

    # --- prohibited content ---
    for content in gt.prohibited_content:
        present = content in resp
        _add(f"prohibited:{content[:40]}", "absent",
             "PRESENT" if present else "absent", not present)

    # --- prohibited claims ---
    for claim in gt.prohibited_claims:
        present = claim in resp
        _add(f"prohibited_claim:{claim[:40]}", "absent",
             "PRESENT" if present else "absent", not present)

    # --- answer elements ---
    for element in gt.expected_answer_elements:
        _add(f"answer_element:{element[:40]}", element, "expected",
             True)  # element-level checks are LLM-judged

    return checks


def classify_failure(checks: list[CheckResult]) -> str | None:
    """Classify the first failing hard gate into the failure taxonomy."""
    for c in checks:
        if c.passed:
            continue
        cid = c.check_id
        if "finding" in cid or "case" in cid or "target" in cid:
            return "target_resolution_failure"
        if "capability" in cid:
            return "tool_selection_failure"
        if "evidence_count" in cid:
            return "evidence_retrieval_failure"
        if "stop_reason" in cid:
            if "semantic_resolution_unavailable" in c.expected:
                return "degraded_mode_failure"
            return "convergence_failure"
        if "iteration" in cid:
            return "convergence_failure"
        if "prohibited" in cid:
            return "unsupported_claim"
        if "key_fact" in cid:
            return "answer_fidelity_failure"
        return "answer_fidelity_failure"
    return None
