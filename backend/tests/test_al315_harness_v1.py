"""AL-3.2 harness self-tests — GroundTruth, evaluator, scenario registry."""
import pytest

from eval.ground_truth import GroundTruth, GT_S03, GT_S05, GT_S11
from eval.scenarios_al3 import (
    get_all, get_quality_scenarios, get_regression_scenarios,
    get_by_layer, get_scenarios,
)
from eval.evaluator import evaluate_hard_gates, classify_failure
from eval.result_model import TraceSummary, ScenarioStatus
from eval.llm_judge import judge_response


class TestScenarioRegistry:
    def test_18_scenarios(self):
        assert len(get_all()) == 18

    def test_16_quality_2_regression(self):
        """AL-3 v2: 18 total, 16 quality, 2 regression (S02 + S16)."""
        assert len(get_quality_scenarios()) == 16
        assert len(get_regression_scenarios()) == 2

    def test_all_have_ground_truth(self):
        for sid, s in get_all().items():
            assert isinstance(s.ground_truth, GroundTruth), \
                f"{sid} has no GroundTruth"

    def test_all_have_quality_axes(self):
        for sid, s in get_all().items():
            assert len(s.quality_axes) > 0, f"{sid} has no quality axes"

    def test_layer_filter(self):
        l1 = get_by_layer("L1")
        assert len(l1) > 0
        l3 = get_by_layer("L3")
        assert len(l3) > 0
        assert set(l3.keys()) <= set(s for s in get_all())

    def test_key_scenarios_present(self):
        all_s = get_all()
        for sid in ("S01", "S03", "S05", "S08", "S09", "S10", "S11", "S13"):
            assert sid in all_s, f"{sid} missing from registry"


class TestGroundTruth:
    def test_s03_has_graph_facts(self):
        gt = GT_S03
        assert gt.expected_finding_id == "F5"
        assert "Cluster_device_sharing_1" in gt.expected_key_facts
        assert "DEV8Z1O4A35QU" in gt.expected_key_facts
        assert "rule-based detection" in gt.prohibited_content

    def test_s05_has_withdrawal_count(self):
        gt = GT_S05
        assert gt.expected_evidence_count_exact == 7
        assert gt.expected_evidence_type == "withdrawals"
        assert "例如" in gt.prohibited_content

    def test_s11_is_unresolved(self):
        gt = GT_S11
        assert gt.expected_finding_id is None  # must NOT resolve
        assert gt.expected_stop_reason == "context_unresolved"

    def test_schema_validation(self):
        gt = GroundTruth()  # empty should be valid
        assert gt.expected_case_id == "U00010"  # default


class TestDeterministicEvaluator:
    def test_all_gates_pass_correct_trace(self):
        gt = GT_S03
        trace = TraceSummary(
            tool_calls=[{"name": "signal_explain", "outcome": "success"}],
            response_text=("Cluster_device_sharing_1. DEV8Z1O4A35QU. 80.0."),
            stop_reason="goal_satisfied",
            iteration_count=1, focused_finding_id="F5")
        checks = evaluate_hard_gates(gt, trace)
        assert all(c.passed for c in checks)

    def test_wrong_target_fails(self):
        trace = TraceSummary(
            tool_calls=[], response_text="",
            stop_reason="goal_satisfied",
            iteration_count=1, focused_finding_id="F3")
        checks = evaluate_hard_gates(GT_S03, trace)
        assert not all(c.passed for c in checks)
        assert any("expected_finding" in c.check_id for c in checks
                   if not c.passed)

    def test_missing_key_fact_fails(self):
        trace = TraceSummary(
            tool_calls=[{"name": "signal_explain", "outcome": "success"}],
            response_text="Cluster_device_sharing_1.",  # missing DEV
            stop_reason="goal_satisfied",
            iteration_count=1, focused_finding_id="F5")
        checks = evaluate_hard_gates(GT_S03, trace)
        assert not all(c.passed for c in checks)

    def test_prohibited_content_fails(self):
        trace = TraceSummary(
            tool_calls=[{"name": "signal_explain", "outcome": "success"}],
            response_text=("Cluster_device_sharing_1. DEV8Z1O4A35QU. "
                           "80.0. 例如"),
            stop_reason="goal_satisfied",
            iteration_count=1, focused_finding_id="F5")
        checks = evaluate_hard_gates(GT_S03, trace)
        assert any("prohibited" in c.check_id for c in checks
                   if not c.passed)

    def test_classification_on_failure(self):
        trace = TraceSummary(
            tool_calls=[], response_text="",
            stop_reason="goal_satisfied",
            iteration_count=1, focused_finding_id="F3")
        checks = evaluate_hard_gates(GT_S03, trace)
        taxonomy = classify_failure(checks)
        assert taxonomy is not None


class TestLLMJudge:
    def test_judge_returns_scores(self):
        class FakeLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return json.dumps({
                    "scores": {"answer_addresses_question": 2,
                               "explanation_quality": 1,
                               "honesty_insufficiency": 2},
                    "rationale": "test"})

        results, available = judge_response(
            FakeLLM(), "test question", "test answer",
            {"expected_key_facts": ["fact1"]})
        assert available is True
        assert len(results) == 3
        assert results[0]["score"] == 2

    def test_judge_handles_malformed_response(self):
        class BadLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return "not json at all"

        results, available = judge_response(
            BadLLM(), "test", "test answer", {"expected_key_facts": []})
        assert available is False
        assert all(r["score"] == -1 for r in results)

    def test_judge_handles_exception(self):
        class ExplodingLLM:
            def generate(self, **kw):
                raise RuntimeError("boom")

        results, available = judge_response(
            ExplodingLLM(), "test", "test", {"expected_key_facts": []})
        assert available is False
        assert all(r["score"] == -1 for r in results)


import json
