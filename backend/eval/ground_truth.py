"""AL-3 Ground Truth — evaluator-side expected facts, claims, and boundedness.

GroundTruth is authored by the developer (who knows the RP data) and is
NEVER exposed to the running Agent. The LLM judge compares the Agent's
response against this oracle to verify correctness — not just
plausibility.

Expectation kinds:
  exact      — must match exactly (e.g. finding_id)
  min        — must be >= this value (e.g. evidence count)
  required   — must be present in the response (e.g. key facts)
  prohibited — must NOT appear in the response
  allowed    — one of these values is acceptable
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class GroundTruth:
    # Resolution
    expected_finding_id: str | None = None
    expected_case_id: str = "U00010"
    expected_capability: str | None = None

    # Evidence
    expected_evidence_type: str | None = None
    expected_evidence_count_exact: int | None = None
    expected_evidence_count_min: int | None = None
    expected_evidence_ids: list[str] = field(default_factory=list)
    expected_key_facts: list[str] = field(default_factory=list)

    # Answer properties
    expected_answer_elements: list[str] = field(default_factory=list)

    # Prohibited
    prohibited_claims: list[str] = field(default_factory=list)
    prohibited_content: list[str] = field(default_factory=list)

    # Boundedness
    expected_stop_reason: str | None = None
    expected_max_iterations: int = 8
    expected_insufficiency: str | None = None

    # Layer targets
    deterministic_layers: list[str] = field(default_factory=lambda: ["L1"])
    real_agent_layer: bool = True
    browser_layer: bool = False


# ---------------------------------------------------------------------------
# Per-scenario Ground Truth instances (U00010 live data)
# ---------------------------------------------------------------------------

GT_S01 = GroundTruth(
    expected_case_id="U00010",
    expected_capability="risk_case_fetch",
    expected_key_facts=["findings", "U00010"],
    expected_answer_elements=["case overview"],
    prohibited_content=["wrong case", "U00299"],
)

GT_S02 = GroundTruth(
    expected_case_id="U00010",
    expected_capability="case_intake",
    expected_key_facts=["8 findings", "U00010"],
    prohibited_content=["wrong case"],
    expected_stop_reason="goal_satisfied",
)

GT_S03 = GroundTruth(
    expected_finding_id="F5",
    expected_case_id="U00010",
    expected_capability="signal_explain",
    expected_evidence_type="graph",
    expected_key_facts=[
        "Cluster_device_sharing_1",
        "DEV8Z1O4A35QU",
        "80.0",
    ],
    expected_answer_elements=["why explanation", "graph facts"],
    prohibited_content=[
        "rule-based detection",
        "例如",
    ],
    expected_stop_reason="goal_satisfied",
)

GT_S04 = GroundTruth(
    expected_finding_id="F1",
    expected_case_id="U00010",
    expected_capability="signal_explain",
    expected_evidence_type="ML",
    expected_evidence_count_exact=99,       # ml_score is 99.41
    expected_key_facts=[
        "99.41",
        "LightGBM",
        "机器学习",
        "不是校准过的欺诈概率",
        "不能准确断言某一个具体特征贡献了多少分",
        "ML Factors",
    ],
    expected_answer_elements=[
        "ML definition",
        "score semantics",
        "feature context",
        "attribution boundary",
    ],
    prohibited_claims=[
        "因为共享设备所以99.41",
        "因为高频交易所以99.41",
    ],
    prohibited_content=[
        "Investigation_and_Action_SOP",
        "Links to Known Risky Clusters",
    ],
    expected_stop_reason="goal_satisfied",
)

GT_S05 = GroundTruth(
    expected_finding_id="F3",
    expected_case_id="U00010",
    expected_capability="finding_drilldown",
    expected_evidence_type="withdrawals",
    expected_evidence_count_exact=7,
    expected_key_facts=[
        "7",
        "W000087",
        "W000089",
        "withdrawal",
    ],
    expected_answer_elements=["evidence table", "all records"],
    prohibited_content=[
        "例如",
        "top 5",
        "证据视图",
        "保存在",
    ],
    prohibited_claims=["F7", "High Trading Frequency"],
    expected_stop_reason="goal_satisfied",
)

GT_S06 = GroundTruth(
    expected_finding_id="F7",
    expected_case_id="U00010",
    expected_capability="signal_explain",
    expected_evidence_type="feature+transactions",
    expected_evidence_count_min=50,         # 64 transactions
    expected_key_facts=[
        "Trade frequency (24h) = 54",
        "T00",
        "transaction record",
    ],
    expected_answer_elements=["why explanation", "evidence table"],
    prohibited_content=[
        "withdrawal record",
        "W000087",
    ],
    prohibited_claims=["F3", "High Withdrawal Frequency"],
    expected_stop_reason="goal_satisfied",
)

GT_S07 = GroundTruth(
    expected_finding_id="F5",
    expected_case_id="U00010",
    expected_key_facts=[
        "Cluster_device_sharing_1",
        "DEV8Z1O4A35QU",
    ],
    expected_answer_elements=["why explanation"],
    prohibited_content=["wrong finding"],
    expected_stop_reason="goal_satisfied",
)

GT_S08 = GroundTruth(
    expected_case_id="U00010",
    expected_capability="related_account_analysis",
    expected_evidence_type="accounts",
    expected_evidence_count_exact=18,
    expected_key_facts=[
        "18",
        "U00004",
        "U00337",
        "DEV8Z1O4A35QU",
        "不能证明这些账号之间存在违规或恶意意图",
    ],
    expected_answer_elements=[
        "why explanation",
        "account list",
        "relationship boundary",
    ],
    prohibited_content=[
        "10 个关联账号",
        "只包含其中 5 个",
        "showing 5 of 10",
    ],
    expected_stop_reason="goal_satisfied",
)

GT_S09 = GroundTruth(
    expected_case_id="U00010",
    expected_capability="cross_account_trade_analysis",
    expected_evidence_type="candidates",
    expected_evidence_count_min=5,
    expected_key_facts=[
        "CATA-001",
        "预算限制",
        "不构成对操纵或交易意图的结论",
    ],
    expected_answer_elements=[
        "conclusion",
        "criteria",
        "boundedness",
        "candidate table",
    ],
    prohibited_content=["artifact_bundle: success"],
    expected_stop_reason="goal_satisfied",
)

GT_S10 = GroundTruth(
    expected_finding_id="F5",
    expected_case_id="U00010",
    expected_evidence_type="graph+empty_records",
    expected_key_facts=[
        "数据边界",
        "不能把现有摘要",
        "全部明细",
    ],
    expected_answer_elements=["graph facts", "data boundary"],
    prohibited_content=[
        "conclusive empty",
        "全部明细已提供",
    ],
    expected_stop_reason="bounded_data",
    expected_max_iterations=4,
)

GT_S11 = GroundTruth(
    expected_finding_id=None,          # must NOT resolve
    expected_case_id="U00010",
    expected_key_facts=[],
    expected_answer_elements=["honest unresolved message"],
    prohibited_content=[
        "finding_drilldown",
        "signal_explain",
        "artifact_bundle",
    ],
    expected_stop_reason="context_unresolved",
)

GT_S12 = GroundTruth(
    expected_case_id="U00010",
    expected_capability="related_account_analysis",
    expected_evidence_type="accounts",
    expected_evidence_count_exact=18,
    expected_key_facts=[
        "18",
        "为什么是风险信号",
        "不能证明这些账号之间存在违规或恶意意图",
    ],
    expected_answer_elements=["why explanation", "account list"],
    prohibited_content=["10 个关联账号"],
    expected_stop_reason="goal_satisfied",
)

GT_S13 = GroundTruth(
    expected_case_id="U00010",
    expected_key_facts=[
        "语义解析服务当前不可用",
    ],
    expected_answer_elements=["honest degraded message"],
    prohibited_content=[
        "graph-based risk detection",
        "DEV8Z1O4A35QU",
    ],
    expected_stop_reason="semantic_resolution_unavailable",
)

GT_S14 = GroundTruth(
    expected_finding_id="F3",
    expected_case_id="U00010",
    expected_evidence_type="withdrawals",
    expected_evidence_count_exact=7,
    expected_key_facts=["7", "W000087", "withdrawal"],
    expected_answer_elements=["evidence table"],
    prohibited_content=["例如"],
    expected_stop_reason="goal_satisfied",
)

GT_S15 = GroundTruth(
    expected_key_facts=[],
    expected_max_iterations=4,
    prohibited_content=["iteration_bound_reached"],
)

GT_S16 = GroundTruth(
    expected_stop_reason="bounded_data",
    prohibited_content=["conclusive empty"],
)

GT_S17 = GroundTruth(
    expected_finding_id="F3",
    expected_case_id="U00010",
    expected_key_facts=[
        "Withdrawal frequency (24h) = 7",
        "withdrawal",
    ],
    prohibited_claims=[
        "graph-based risk detection",
    ],
    prohibited_content=[
        "Trade frequency (24h)",
        "F7",
    ],
)

GT_S18 = GroundTruth(
    expected_key_facts=[],
    prohibited_content=[],
)
