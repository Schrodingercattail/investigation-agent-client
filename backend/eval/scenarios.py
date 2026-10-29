"""Week 1 Agent evaluation scenarios — Python data, no YAML/JSON.

Each scenario: id, category, mode (scripted|live), setup (case, findings,
focus, scripted planner responses), turns (user messages in order), and
expected semantic outcomes as (checker, args) lists applied per turn.

Scripted scenarios run the real resolver/executor/tools/composer with a
scripted planner LLM and stubbed RP — fully deterministic. Live scenarios
run the same runtime with the real configured RP + LLM.
"""

from dataclasses import dataclass, field

from eval.fixtures import (
    FULL_CAPS,
    build_context,
    build_service,
    default_findings,
    make_finding,
    plan_json,
    rp_patches,
)


@dataclass
class Scenario:
    scenario_id: str
    category: str
    mode: str                      # "scripted" | "live"
    case_id: str
    turns: list                    # user messages, in order
    focus: str | None = None       # initial focused finding
    planner_scripts: list = field(default_factory=list)
    checks: list = field(default_factory=list)   # [(checker_name, kwargs)]
    check_turn: int = -1           # turn the checkers apply to
    notes: str = ""

    def scripts_for(self):
        return self.planner_scripts


def _C(name, *args, **kw):
    return (name, args, kw)


# --- scripted planner responses (deterministic planning targets) --------
P_INTAKE = plan_json("case_intake", ["fetch_case", "generate_artifact"])
P_SIGNAL_ML = plan_json("timeline_investigation",
                        ["explain_signal", "retrieve_policy"])
P_SIGNAL_RULE = plan_json("timeline_investigation",
                         ["explain_signal", "retrieve_policy"])
P_POLICY = plan_json("case_intake", ["retrieve_policy"])
P_WITHDRAWALS = plan_json("timeline_investigation", ["inspect_withdrawals"])
P_TRANSACTIONS = plan_json("timeline_investigation", ["inspect_transactions"])
P_EVIDENCE = plan_json("timeline_investigation", ["inspect_evidence"])
P_TIMELINE = plan_json("timeline_investigation", ["inspect_timeline"])


def build_scenarios():
    scenarios = []
    add = scenarios.append

    # E01 — case intake
    add(Scenario(
        scenario_id="E01", category="intake", mode="scripted",
        case_id="U00299", turns=["Investigate U00299"],
        planner_scripts=[P_INTAKE],
        checks=[
            _C("expected_skill", skill="case_intake"),
            _C("expected_step", "fetch_case", "generate_artifact"),
            _C("tool_called", tool_name="risk_case_fetch"),
            _C("expected_outcome", "risk_case_fetch", "success",),
            _C("expected_focus", finding_id=None),
            _C("response_contains",
               "findings were identified", "has been created"),
            _C("artifact_has_scope", scope="case:U00299"),
        ],
        notes="Case intake: fetch + case-scoped bundle (accepted behavior).",
    ))

    # E02 — focused ML finding signal question: detector identity from refs
    add(Scenario(
        scenario_id="E02", category="signal", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["Why was this finding flagged?"],
        planner_scripts=[P_SIGNAL_ML],
        checks=[
            _C("expected_skill", skill="timeline_investigation"),
            # AL-2.8: explain intent deterministically plans the
            # policy continuation alongside the signal explanation.
            _C("expected_step", "explain_signal", "retrieve_policy"),
            _C("tool_called", tool_name="signal_explain"),
            _C("tool_argument", tool_name="signal_explain", key="finding_id",
               value="F1"),
            _C("detector_type", tool_name="signal_explain", expected="ML"),
            _C("response_contains", "ML Pattern Detection"),
            _C("response_not_contains", "rule-based detection"),
        ],
        notes="Detector identity derived from F1.signal_refs (ML), not Rule.",
    ))

    # E03 — policy request on a finding with finding-level basis
    add(Scenario(
        scenario_id="E03", category="policy", mode="scripted",
        case_id="U00299", focus="F2",
        turns=["Which policy requirements apply to this finding?"],
        planner_scripts=[P_POLICY],
        checks=[
            # policy retrieval rides both skills; the semantic contract is
            # the retrieve_policy STEP + policy_lookup result, not the skill
            _C("expected_skill", skill="timeline_investigation"),
            _C("expected_step", "retrieve_policy"),
            _C("tool_called", tool_name="policy_lookup"),
            _C("expected_outcome", "policy_lookup", "success",),
            _C("response_contains", "applies directly"),
            # AL-2.10: the case-level supplement block no longer exists in
            # the finding-explain answer (answer fidelity).
            _C("response_not_contains", "Additionally,"),
        ],
        notes="Finding-associated policy contract (AL-2.10: no case dump).",
    ))

    # E04 — F1 -> F2 focus switch
    add(Scenario(
        scenario_id="E04", category="focus", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["thanks",
               ("Show the timeline of the Coordinated Trading Pattern "
                "finding.",
                {"type": "focus_finding", "finding_id": "F2"})],
        planner_scripts=[P_SIGNAL_ML, P_TIMELINE],
        checks=[
            _C("expected_focus", finding_id="F2"),
            _C("no_context_leak", forbidden_finding_id="F1"),
            _C("tool_argument", tool_name="finding_drilldown",
               key="finding_id", value="F2"),
        ],
        notes="Focus switch F1->F2: current focus F2, no F1 targeting.",
    ))

    # E05 — explicit cross-case request while F1 focused
    add(Scenario(
        scenario_id="E05", category="cross-case", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["调查90001这个case"],
        planner_scripts=[],   # never reached: guard rejects pre-planning
        checks=[
            _C("expected_focus", finding_id="F1"),
            _C("context_unchanged", previous_focused_finding_id="F1"),
            _C("tool_not_called", tool_name="risk_case_fetch"),
            _C("tool_not_called", tool_name="artifact_bundle"),
            # per-turn response language: the zh request gets the zh
            # guidance face (the en face "start a new investigation" is
            # the undeterminable-language default)
            _C("response_contains", "U90001", "新建一个调查"),
        ],
        notes="Cross-case invariant: bounded guidance, zero execution.",
    ))

    # E06 — pure finding reference / focus transition
    add(Scenario(
        scenario_id="E06", category="focus", mode="scripted",
        case_id="U00299", focus=None,
        turns=["Coordinated Trading Pattern"],
        planner_scripts=[P_SIGNAL_RULE],
        checks=[
            _C("expected_focus", finding_id="F2"),
        ],
        notes="Bare finding-name reference resolves focus deterministically.",
    ))

    # E07 — withdrawals on a finding WITHOUT a withdrawal stream
    add(Scenario(
        scenario_id="E07", category="evidence", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["show me all the withdrawals"],
        planner_scripts=[P_WITHDRAWALS],
        checks=[
            _C("expected_step", "inspect_withdrawals"),
            _C("tool_argument", tool_name="finding_drilldown",
               key="stream", value="withdrawals"),
            _C("response_not_contains", "transaction record"),
            _C("response_semantic_success", turn=0),
        ],
        check_turn=0,
        notes="F1 has no withdrawal stream: bounded, NO transaction "
              "substitution. Task may fail — semantic success is the "
              "bounded response.",
    ))

    # E08 — withdrawals on a finding WITH withdrawal evidence
    add(Scenario(
        scenario_id="E08", category="evidence", mode="scripted",
        case_id="U00299", focus="F3",
        turns=["show me all the withdrawals"],
        planner_scripts=[P_WITHDRAWALS],
        checks=[
            _C("expected_step", "inspect_withdrawals"),
            _C("tool_argument", tool_name="finding_drilldown",
               key="stream", value="withdrawals"),
            _C("expected_outcome", "finding_drilldown", "success",),
            _C("evidence_stream", tool_name="finding_drilldown",
               stream="withdrawals"),
            _C("response_matches_stream", stream="withdrawals"),
        ],
        notes="Withdrawal-backed finding returns exactly withdrawal records.",
    ))

    # E09 — transactions on a finding with transaction evidence
    add(Scenario(
        scenario_id="E09", category="evidence", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["show me all the transactions"],
        planner_scripts=[P_TRANSACTIONS],
        checks=[
            _C("expected_step", "inspect_transactions"),
            _C("tool_argument", tool_name="finding_drilldown",
               key="stream", value="transactions"),
            _C("expected_outcome", "finding_drilldown", "success",),
            _C("evidence_stream", tool_name="finding_drilldown",
               stream="transactions"),
        ],
        notes="Transaction-backed finding returns exactly transaction records.",
    ))

    # E10 — withdrawals -> transactions across two turns (no contamination)
    add(Scenario(
        scenario_id="E10", category="evidence", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["show me all the withdrawals",     # F1: bounded unsupported
               ("show me all the transactions",
                {"type": "focus_finding", "finding_id": "F1"})],
        planner_scripts=[P_WITHDRAWALS, P_TRANSACTIONS],
        checks=[
            # turn 0: bounded unsupported — no transaction substitution
            _C("response_not_contains", "transaction record", turn=0),
            _C("response_semantic_success", turn=0),
            # turn 1: explicit transactions on the same finding succeeds —
            # the failed withdrawal turn did not contaminate it
            _C("expected_focus", finding_id="F1", turn=1),
            _C("expected_outcome", "finding_drilldown", "success", turn=1),
            _C("evidence_stream", tool_name="finding_drilldown",
               stream="transactions", turn=1),
            _C("response_matches_stream", stream="transactions", turn=1),
        ],
        notes="Stream switching + no substitution: unsupported withdrawal "
              "does not contaminate a subsequent valid transaction request.",
    ))

    # E11 — associated policy refs: count = associated only
    add(Scenario(
        scenario_id="E11", category="policy", mode="scripted",
        case_id="U00299", focus="F2",
        turns=["Which policy requirements apply to this finding?"],
        planner_scripts=[P_POLICY],
        checks=[
            _C("response_contains",
               "For this finding, 1 policy reference applies directly:"),
            _C("response_not_contains",
               "2 policy references apply to this finding"),
            _C("response_not_contains", "Additionally,"),
        ],
        notes="Case-level refs are NOT counted as finding-level and are "
              "no longer listed (AL-2.10).",
    ))

    # E12 — no finding-level policy basis
    add(Scenario(
        scenario_id="E12", category="policy", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["Which policy requirements apply to this finding?"],
        planner_scripts=[P_POLICY],
        checks=[
            _C("response_contains",
               "The Risk Platform has attached no finding-level policy "
               "basis to this finding"),
            _C("response_not_contains", "case-level"),
        ],
        notes="No-basis: explicit statement; case-wide citations omitted "
              "(AL-2.10 answer fidelity).",
    ))

    # E13 — finding artifact after signal + timeline + policy
    add(Scenario(
        scenario_id="E13", category="artifact", mode="scripted",
        case_id="U00299", focus="F3",
        turns=["Why was this finding flagged?",
               "Show the timeline of this finding.",
               "Which policy requirements apply to this finding?",
               "Generate a Markdown investigation bundle for this finding."],
        planner_scripts=[P_SIGNAL_RULE, P_TIMELINE, P_POLICY,
                         plan_json("case_intake", ["generate_artifact"])],
        checks=[
            _C("artifact_has_scope", scope="finding:F3"),
            _C("artifact_contains", "High Withdrawal Frequency"),
            _C("artifact_contains", "## Timeline"),
            _C("artifact_contains", "withdrawal"),
            _C("artifact_not_contains", "## Evidence Gaps",
               "Next data needed"),
            _C("artifact_not_contains",
               "ML Pattern Detection", "Coordinated Trading Pattern"),
        ],
        check_turn=3,
        notes="Finding-scoped artifact: correct finding/timeline/signal, "
              "no Evidence Gaps, no other-finding leakage.",
    ))

    # E14 — finding switch + artifact (F1 investigated, then F2 artifact)
    add(Scenario(
        scenario_id="E14", category="artifact", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["Why was this finding flagged?",
               "Which policy requirements apply to this finding?",
               ("Generate a Markdown investigation bundle for this finding.",
                {"type": "focus_finding", "finding_id": "F2"})],
        planner_scripts=[P_SIGNAL_ML, P_POLICY,
                         plan_json("timeline_investigation",
                                   ["generate_artifact"])],
        checks=[
            _C("expected_focus", finding_id="F2"),
            _C("artifact_has_scope", scope="finding:F2"),
            _C("artifact_contains", "F2 — Coordinated Trading Pattern"),
            _C("artifact_not_contains", "ML Pattern Detection",
               "rule-based detection"),
        ],
        check_turn=2,
        notes="Focus F1 investigated, focus switches to F2: the F2 artifact "
              "contains only F2 content and only contributing provenance.",
    ))

    # E15 — unsupported same-account (self) opposite-trade request
    add(Scenario(
        scenario_id="E15", category="unsupported", mode="scripted",
        case_id="U00299", focus="F1",
        turns=["show me the opposite trade"],
        # the scripted plan mirrors the live contract: the request
        # specializes to inspect_self_opposite_trades (distinct semantic
        # request; same-account drilldown, registered-but-unimplemented)
        planner_scripts=[plan_json("timeline_investigation",
                                   ["inspect_self_opposite_trades"])],
        checks=[
            _C("expected_skill", skill="timeline_investigation"),
            _C("expected_step", "inspect_self_opposite_trades"),
            _C("tool_argument", tool_name="finding_drilldown",
               key="view", value="self_opposite_trades"),
            _C("expected_outcome", "finding_drilldown", "unsupported"),
            _C("response_contains",
               "self-opposite-trade investigation is not available"),
            _C("response_not_contains", "transaction record",
               "opposite-trade ratio"),
        ],
        notes="Self-opposite (same-account) drilldown unsupported for F1: "
              "bounded response, NO ordinary transaction fallback.",
    ))

    # E16 — cross-account opposite-trade analysis (trade investigation)
    # The cross-account step is the trade skill's primary executable path;
    # the deterministic planner hook routes cross-account wording here (it
    # must never degrade into the same-account self step). The RP adapter
    # seams are patched (fixture below) so the analysis runs on controlled
    # multi-account evidence.
    add(Scenario(
        scenario_id="E16", category="trade-analysis", mode="scripted",
        case_id="U00299", focus="F2",
        turns=["Find opposite trades across accounts for this finding."],
        planner_scripts=[plan_json("trade_investigation",
                                   ["analyze_cross_account_trades"])],
        checks=[
            _C("expected_skill", skill="trade_investigation"),
            # AL-2.6: the deterministic plan appends the bounded bundle
            # step after the governed analysis step.
            _C("expected_step", "analyze_cross_account_trades",
               "generate_artifact"),
            _C("tool_called", tool_name="cross_account_trade_analysis"),
            _C("tool_argument", tool_name="cross_account_trade_analysis",
               key="analysis_type", value="cross_account_opposite_trade"),
            _C("expected_outcome", "cross_account_trade_analysis",
               "success"),
            _C("response_contains",
               "candidate cross-account opposite-trade relationship"),
            _C("response_contains", "U00299", "U00123"),
            _C("response_not_contains",
               "confirmed coordinated trading", "confirmed collusion"),
        ],
        notes="Cross-account wording routes to the analysis tool (never "
              "the self step); MATCH_FOUND renders candidate terminology "
              "with account pair + metrics.",
    ))

    # E17 — self-opposite (same-account) view executes on a capable finding
    # The completion-phase contract: the canonical view is IMPLEMENTED and
    # capability-gated. F2 declares self_opposite_trades → the RP aggregate
    # ratio + backing rule render with same-account wording; the same
    # request on F1 stays bounded-unsupported (E15).
    add(Scenario(
        scenario_id="E17", category="trade-analysis", mode="scripted",
        case_id="U00299", focus="F2",
        turns=["show me the opposite trade"],
        planner_scripts=[plan_json("timeline_investigation",
                                   ["inspect_self_opposite_trades"])],
        checks=[
            _C("expected_skill", skill="timeline_investigation"),
            _C("expected_step", "inspect_self_opposite_trades"),
            _C("tool_argument", tool_name="finding_drilldown",
               key="view", value="self_opposite_trades"),
            _C("expected_outcome", "finding_drilldown", "success"),
            _C("response_contains",
               "opposite-side trading activity by this SAME account"),
            _C("response_contains", "45.24%",
               "Coordinated Trading Pattern"),
            _C("response_not_contains",
               "candidate cross-account", "confirmed coordinated"),
        ],
        notes="Self-opposite view executes for the capable finding: RP "
              "aggregate ratio + rule echoed, same-account wording only; "
              "no cross-account candidate terminology.",
    ))

    return scenarios


# --- live scenarios (run against real configured RP + LLM) ----------------

def build_live_scenarios():
    scenarios = []
    add = scenarios.append

    add(Scenario(
        scenario_id="L01", category="intake", mode="live",
        case_id="U00299", turns=["Investigate U00299"],
        checks=[
            _C("expected_skill", skill="case_intake"),
            _C("tool_called", tool_name="risk_case_fetch"),
            _C("expected_outcome", "risk_case_fetch", "success",),
            _C("response_contains", "findings were identified"),
        ],
        notes="Warm case intake.",
    ))
    add(Scenario(
        scenario_id="L02", category="signal", mode="live",
        case_id="U00299", focus="F1",
        turns=["Why was this finding flagged?"],
        checks=[
            _C("expected_skill", skill="timeline_investigation"),
            _C("tool_called", tool_name="signal_explain"),
            _C("response_contains", "ML Pattern Detection"),
            _C("response_not_contains", "rule-based detection"),
            _C("response_not_contains", "exceeding the system's configured "
                                        "threshold"),
        ],
        notes="ML mechanism explicit; no fabricated attribution/thresholds.",
    ))
    add(Scenario(
        scenario_id="L03", category="evidence", mode="live",
        case_id="U00299", focus="F3",
        turns=["show me all the withdrawals"],
        checks=[
            _C("expected_outcome", "finding_drilldown", "success",),
            _C("evidence_stream", tool_name="finding_drilldown",
               stream="withdrawals"),
            _C("response_matches_stream", stream="withdrawals"),
        ],
        notes="Supported stream returns exactly that stream.",
    ))
    add(Scenario(
        scenario_id="L04", category="evidence", mode="live",
        case_id="U00299", focus="F1",
        turns=["show me all the withdrawals"],
        checks=[
            _C("response_not_contains", "transaction record"),
            _C("response_semantic_success", turn=0),
        ],
        notes="Unsupported stream: bounded response, no substitution.",
    ))
    add(Scenario(
        scenario_id="L05", category="cold-start", mode="live",
        case_id="U00002", turns=["Investigate U00002"],
        checks=[
            _C("expected_skill", skill="case_intake"),
            _C("expected_outcome", "risk_case_fetch", "success",),
            _C("response_contains", "findings were identified"),
        ],
        notes="Cold case: RP generates+persists the explanation on demand; "
              "Agent reuses it. explanation_source reported separately by "
              "the runner.",
    ))
    return scenarios


def all_scenarios():
    return build_scenarios() + build_live_scenarios()


def scripted_scenarios():
    return build_scenarios()
