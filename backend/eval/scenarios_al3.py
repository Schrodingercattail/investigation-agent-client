"""AL-3 scenario registry — 18 scenarios with GroundTruth and rubric."""
from __future__ import annotations
from dataclasses import dataclass

from eval.ground_truth import (
    GroundTruth, GT_S01, GT_S02, GT_S03, GT_S04, GT_S05, GT_S06,
    GT_S07, GT_S08, GT_S09, GT_S10, GT_S11, GT_S12, GT_S13, GT_S14,
    GT_S15, GT_S16, GT_S17, GT_S18,
)

QUALITY = "al3_quality"
REGRESSION = "al2_regression"

# AL-3.2: execution_mode is a formal execution contract, not metadata.
# The runner MUST dispatch based on this field; invalid values must
# produce a clear error, never a silent fallback to loop.
VALID_EXECUTION_MODES = {"turn", "loop", "no_key_loop", "deterministic"}


@dataclass
class AL3Scenario:
    scenario_id: str
    scenario_version: str
    category: str
    description: str
    user_prompt: str | list[str]
    initial_context: dict
    execution_mode: str
    ground_truth: GroundTruth
    quality_axes: list[str]
    layers: list[str]
    scenario_class: str = QUALITY
    human_review: bool = False
    browser_required: bool = False


REGISTRY: dict[str, AL3Scenario] = {}


def _reg(**kw) -> AL3Scenario:
    s = AL3Scenario(**kw)
    REGISTRY[s.scenario_id] = s
    return s


_reg(scenario_id="S01", scenario_version="1.0",
     category="case-intake", description="Case intake via CJK prompt",
     user_prompt="给我输出一下00010案例情况",
     initial_context={"case_id": "U00010"},
     execution_mode="turn", ground_truth=GT_S01,
     quality_axes=["Q1", "Q6", "Q9"],
     layers=["L1", "L2", "L3"], human_review=True, browser_required=True)

_reg(scenario_id="S02", scenario_version="1.0",
     category="case-intake-CJK", description="Case intake CJK alias",
     user_prompt="调查00010",
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S02,
     quality_axes=["Q1"], layers=["L1"], scenario_class=REGRESSION)

_reg(scenario_id="S03", scenario_version="1.0",
     category="finding-explain", description="Shared device explain",
     user_prompt="为什么共享设备是风险项",
     initial_context={"case_id": "U00010"},
     execution_mode="turn", ground_truth=GT_S03,
     quality_axes=["Q1", "Q6"],
     layers=["L1", "L2", "L3"], human_review=True, browser_required=True)

_reg(scenario_id="S04", scenario_version="1.0",
     category="finding-explain-ML", description="ML explain",
     user_prompt="ML Pattern Detection什么意思，为什么是风险",
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S04,
     quality_axes=["Q1", "Q6", "Q7"],
     layers=["L2", "L3"], human_review=True, browser_required=True)

_reg(scenario_id="S05", scenario_version="1.0",
     category="finding-evidence", description="F3 withdrawal evidence",
     user_prompt="高频提现为什么是风险项，把所有高频提现证据发给我",
     initial_context={"case_id": "U00010"},
     execution_mode="turn", ground_truth=GT_S05,
     quality_axes=["Q1", "Q5", "Q6"],
     layers=["L1", "L2", "L3"], browser_required=True)

_reg(scenario_id="S06", scenario_version="1.0",
     category="finding-detail-combined",
     description="F7 combined why + transaction detail",
     user_prompt="为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S06,
     quality_axes=["Q1", "Q2", "Q5", "Q6"],
     layers=["L2", "L3"], browser_required=True)

_reg(scenario_id="S07", scenario_version="1.0",
     category="follow-up-finding", description="Follow-up resolution",
     user_prompt=["给我输出一下00010案例情况", "为什么说共享设备是风险项"],
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S07,
     quality_axes=["Q1", "Q8"], layers=["L2", "L3"], browser_required=True)

_reg(scenario_id="S08", scenario_version="1.0",
     category="related-accounts", description="Related accounts list",
     user_prompt=["给我输出一下00010案例情况",
                  "能告诉我哪些账号是这个账号的关联账号吗"],
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S08,
     quality_axes=["Q1", "Q5", "Q9"],
     layers=["L1", "L2", "L3"], human_review=True, browser_required=True)

_reg(scenario_id="S09", scenario_version="1.0",
     category="cross-account", description="Cross-account analysis (CONTROL)",
     user_prompt=["Investigate U00010",
                  "这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我"],
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S09,
     quality_axes=["Q1", "Q3", "Q5", "Q6", "Q7"],
     layers=["L1", "L2", "L3"], browser_required=True)

_reg(scenario_id="S10", scenario_version="1.0",
     category="insufficient-data", description="F5 graph: no record data",
     user_prompt="共享设备为什么是风险项，给我提供一下所有与这个账号共享设备的明细",
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S10,
     quality_axes=["Q4", "Q6", "Q7"], layers=["L1", "L2"])

_reg(scenario_id="S11", scenario_version="1.0",
     category="unresolved", description="Nonexistent finding",
     user_prompt="谈谈虚拟币洗钱这个风险项",
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S11,
     quality_axes=["Q1", "Q7"], layers=["L1"])

_reg(scenario_id="S12", scenario_version="1.0",
     category="multi-intent", description="Related account why + list",
     user_prompt="关联账户关系为什么是风险项，这个账户有哪些关联账户",
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S12,
     quality_axes=["Q2", "Q6"],
     layers=["L1", "L2", "L3"], browser_required=True)

_reg(scenario_id="S13", scenario_version="1.0",
     category="degraded-mode", description="LLM unavailable honesty",
     user_prompt="为什么共享设备是风险项",
     initial_context={"case_id": "U00010"},
     execution_mode="no_key_loop", ground_truth=GT_S13,
     quality_axes=["Q7"], layers=["L1", "L2"])

_reg(scenario_id="S14", scenario_version="1.0",
     category="persistence", description="F3 evidence persistence",
     user_prompt=["给我输出一下00010案例情况",
                  "高频提现为什么是风险项，把所有高频提现证据发给我"],
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S14,
     quality_axes=["Q8"], layers=["L3"], browser_required=True)

_reg(scenario_id="S15", scenario_version="1.0",
     category="boundedness", description="Convergence stress (det.)",
     user_prompt="repeated same query stress",
     initial_context={"case_id": "U00299"},
     execution_mode="deterministic", ground_truth=GT_S15,
     quality_axes=["Q4"], layers=["L1"])

_reg(scenario_id="S16", scenario_version="1.0",
     category="evidence-empty", description="EMPTY ≠ SATISFIED (regression)",
     user_prompt="show me all the transactions",
     initial_context={"case_id": "U00299"},
     execution_mode="deterministic", ground_truth=GT_S16,
     quality_axes=["Q5", "Q7"], layers=["L1"],
     scenario_class=REGRESSION)

_reg(scenario_id="S17", scenario_version="1.0",
     category="evidence-vs-answer",
     description="Evidence correct but answer wrong (F3 not F7)",
     user_prompt="这个发现的风险信号是什么？为什么被标记？",
     initial_context={"case_id": "U00010"},
     execution_mode="loop", ground_truth=GT_S17,
     quality_axes=["Q5", "Q6"], layers=["L2"])

_reg(scenario_id="S18", scenario_version="1.0",
     category="premature-stopping",
     description="Convergence guard specificity (det.)",
     user_prompt="paginated query stress",
     initial_context={"case_id": "U00299"},
     execution_mode="deterministic", ground_truth=GT_S18,
     quality_axes=["Q4"], layers=["L1"])


def get_all() -> dict[str, AL3Scenario]:
    return dict(REGISTRY)


def get_quality_scenarios() -> dict[str, AL3Scenario]:
    return {k: v for k, v in REGISTRY.items() if v.scenario_class == QUALITY}


def get_regression_scenarios() -> dict[str, AL3Scenario]:
    return {k: v for k, v in REGISTRY.items() if v.scenario_class == REGRESSION}


def get_by_layer(layer: str) -> dict[str, AL3Scenario]:
    return {k: v for k, v in REGISTRY.items() if layer in v.layers}


def get_scenarios(layers: list[str] | None = None,
                  scenario_class: str | None = None) -> dict[str, AL3Scenario]:
    result = dict(REGISTRY)
    if layers:
        result = {k: v for k, v in result.items()
                  if any(l in v.layers for l in layers)}
    if scenario_class:
        result = {k: v for k, v in result.items()
                  if v.scenario_class == scenario_class}
    return result
