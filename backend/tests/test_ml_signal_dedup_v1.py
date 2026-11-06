"""U00033 (INV-12d412c79044) — ML signal-explanation block dedup.

Composer-level regression tests over `_compose_gsp_response` (pure
rendering; no state mutation, no retrieval, no business rules).

Grounded in the REAL defect: one signal_explain observation legally
serving multiple Goal Outputs rendered its ML explanation facts in
EVERY serving section — the explanation output via the requirement-
keyed compact form, the other outputs via the generic long form
(two shapes, same data). Contract: the shared explanation renders
completely exactly ONCE — in the first-served output's section —
later servings drop the derived block and gain an explicit reference;
independent observations and single-output answers are untouched.
GU outcome, Planner, tool calls, evidence acquisition, goal-
satisfaction and candidate-scope export are not exercised here and
must not change.
"""

from app.agent_loop import AgentLoopState, _compose_gsp_response
from app.goal_resolution import GoalContract, RequiredOutput


CASE = "U00033"


def _output(oid, claim, kind, reqs, sat="satisfied", polarity="positive"):
    return RequiredOutput(
        output_id=oid, claim=claim, claim_kind=kind, subject_ref=CASE,
        scope="case_level", evidence_requirements=reqs,
        postcondition_facets=[], satisfaction=sat,
        polarity=polarity, detail=None)


def _ml_obs(for_outputs, score=85, step_id="S1"):
    return {
        "tool_name": "signal_explain", "step_type": "explain_signal",
        "outcome": "success", "semantic_status": "ML",
        "step_id": step_id, "iteration": 1, "for_outputs": for_outputs,
        "summary": {
            "signal_type": "ML",
            "detection_method": "unsupervised pattern model",
            "ml_score": score,
            "score_interpretation":
                "system signal, not a calibrated probability",
            "available_feature_values": {
                "tx_count_24h": "31", "withdrawal_count_24h": "9"},
            "attribution_available": False,
        },
        "evidence_refs": [],
    }


def _state(outputs, observations, lang=None):
    kw = {} if lang is None else {"response_language": lang}
    return AgentLoopState(
        task_id="TASK-MLD", investigation_id="INV-MLD", case_id=CASE,
        goal="这个账户为什么被标记为高风险？解释一下 ML 分数",
        criteria_kind="question_answered",
        goal_contract=GoalContract(
            contract_id="GC-MLD", subject_ref=CASE, intent_source="llm",
            outputs=outputs),
        observations=observations, **kw)


def test_shared_ml_block_renders_once_with_reference():
    """REAL defect shape: one ML signal obs serves the explanation
    output AND a second output — score/features/boundary render exactly
    once, the long-form duplicate is suppressed, later section carries
    the reference line."""
    state = _state(
        [_output("O1", "为什么被标记", "explanation",
                 ["ml_signal_explanation"]),
         _output("O2", "是否存在对冲候选", "existence_check",
                 ["opposite_trade_relations"])],
        [_ml_obs(["O1", "O2"])])
    text = _compose_gsp_response(state)
    # complete facts exactly once (owner = first-served section)
    assert text.count("ML 信号分数 85/100") == 1
    assert text.count("模型上下文特征") == 1
    assert text.count("边界: Risk Platform 未提供逐特征贡献归因") == 1
    assert "tx_count_24h" in text
    # long-form duplicate suppressed
    assert "本账号的 ML 信号分数为" not in text
    assert "ML Pattern Detection 的含义" not in text
    assert "归因边界" not in text
    # explicit reference in the later section
    assert "ML 信号解释完整版见" in text
    assert "本项结论不受影响" in text
    # both section headers still present (no output dropped)
    assert text.count("关于「") == 2


def test_independent_ml_observations_both_rendered():
    """Two outputs with their OWN signal observations (different
    scores): both render fully; no reference lines, no stripping."""
    state = _state(
        [_output("O1", "为什么被标记", "explanation",
                 ["ml_signal_explanation"]),
         _output("O2", "另一个账户的信号", "explanation",
                 ["ml_signal_explanation"])],
        [_ml_obs(["O1"], score=85, step_id="S1"),
         _ml_obs(["O2"], score=77, step_id="S2")])
    text = _compose_gsp_response(state)
    assert "ML 信号分数 85/100" in text
    assert "ML 信号分数 77/100" in text
    assert "ML 信号解释完整版见" not in text


def test_shared_block_keeps_output_unique_information():
    """A serving output whose section also renders its OWN evidence
    (graph cluster facts) keeps those unique lines — only shared-derived
    signal lines are ever stripped."""
    cluster_obs = {
        "tool_name": "signal_explain", "step_type": "explain_signal",
        "outcome": "success", "semantic_status": "Graph",
        "step_id": "S2", "iteration": 1, "for_outputs": ["O2"],
        "summary": {
            "signal_type": "Graph",
            "cluster_name": "cluster-A1", "detection_type": "cluster",
            "member_count": 4, "cluster_risk_score": 71,
            "shared_devices": ["dev-9"],
        },
        "evidence_refs": [],
    }
    state = _state(
        [_output("O1", "为什么被标记", "explanation",
                 ["ml_signal_explanation"]),
         _output("O2", "是否存在共享设备关联", "existence_check",
                 ["shared_device_evidence"])],
        [_ml_obs(["O1", "O2"]), cluster_obs])
    text = _compose_gsp_response(state)
    # unique graph evidence of O2 survives intact
    assert "cluster-A1" in text
    assert "dev-9" in text
    # shared ML facts still exactly once (owner section)
    assert text.count("ML 信号分数 85/100") == 1


def test_single_output_unaffected():
    """Single-output answer: complete block, unchanged behavior — no
    reference line, nothing stripped."""
    state = _state(
        [_output("O1", "为什么被标记", "explanation",
                 ["ml_signal_explanation"])],
        [_ml_obs(["O1"])])
    text = _compose_gsp_response(state)
    assert text.count("ML 信号分数 85/100") == 1
    assert text.count("模型上下文特征") == 1
    assert text.count("边界: Risk Platform 未提供逐特征贡献归因") == 1
    assert "ML 信号解释完整版见" not in text


def test_three_serving_outputs_block_once():
    """Three outputs share one ML obs: facts exactly once, each later
    section gets the reference, no duplicated blocks."""
    state = _state(
        [_output("O1", "为什么被标记", "explanation",
                 ["ml_signal_explanation"]),
         _output("O2", "是否存在对冲候选", "existence_check",
                 ["opposite_trade_relations"]),
         _output("O3", "是否有共享设备", "existence_check",
                 ["shared_ip_evidence"])],
        [_ml_obs(["O1", "O2", "O3"])])
    text = _compose_gsp_response(state)
    assert text.count("ML 信号分数 85/100") == 1
    assert text.count("模型上下文特征") == 1
    assert text.count("ML 信号解释完整版见") == 2
    assert "本账号的 ML 信号分数为" not in text
    assert "ML Pattern Detection 的含义" not in text


def test_shared_block_en_reference():
    """English face: compact facts once, English reference wording,
    long form suppressed."""
    state = _state(
        [_output("O1", "Why flagged", "explanation",
                 ["ml_signal_explanation"]),
         _output("O2", "Hedging candidates", "existence_check",
                 ["opposite_trade_relations"])],
        [_ml_obs(["O1", "O2"])], lang="en")
    text = _compose_gsp_response(state)
    assert text.count("ML signal score 85/100") == 1
    assert text.count("Model context features") == 1
    assert "The full ML signal explanation is in the" in text
    assert "ML Pattern Detection means" not in text
    assert "This account's ML signal score is" not in text
