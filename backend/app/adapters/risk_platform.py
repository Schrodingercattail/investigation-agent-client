"""Risk Platform Adapter.

Single boundary between the Agent Client domain and the Risk Platform HTTP
API (see docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md §4.5). The RP
remains the source of truth for detection/evidence; this adapter only:

- calls existing Risk Platform endpoints,
- normalizes responses into Agent domain structures (Finding etc.),
- isolates RP-specific field names/shapes from the Agent domain,
- distinguishes valid-empty data, upstream integration failure, and
  invalid input — and never converts integration failures into empty data,
- never fabricates missing evidence (refs mirror what RP actually returned).

Endpoints used (verified against the Risk Platform codebase):
- GET  /api/risk/cases/{user_id}/evidence   → RiskEvidenceResponse
- POST /api/risk/explain                    → ExplanationResponse
  (authoritative canonical findings + validated citations)

RP "empty case" semantics: GET evidence returns risk_summary with
risk_level="UNKNOWN", risk_score=0, detected_at=None and empty evidence
arrays for a user with no risk event. That is a VALID response describing a
genuinely empty case — normalized to ToolResult.outcome="empty".
"""

import asyncio
import concurrent.futures
import logging
from typing import Any

import httpx

from app.config import settings
from app.models import (
    EvidenceRef,
    Finding,
    FindingCapability,
    PolicyRef,
    SignalRef,
)

logger = logging.getLogger(__name__)


class RiskPlatformError(Exception):
    """Fatal adapter-level failure (transport/auth/malformed payload).

    Raised so callers can map it to ToolResult outcome=integration_error or
    validation_error as appropriate; never silently swallowed.
    """

    def __init__(self, kind: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.kind = kind          # "unavailable" | "auth" | "malformed" | "http"
        self.status_code = status_code
        self.message = message


class RiskPlatformAdapter:
    """HTTP adapter over the existing Risk Platform APIs."""

    DEFAULT_TIMEOUT = 30.0

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (
            base_url
            or getattr(settings, "RISK_PLATFORM_BASE_URL", "http://localhost:8000")
        ).rstrip("/")
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

    # --- low-level request helper ------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method, url, json=json_body,
                )
        except httpx.TimeoutException as e:
            raise RiskPlatformError(
                "unavailable", f"Risk Platform timed out after {self.timeout}s",
            ) from e
        except httpx.NetworkError as e:
            raise RiskPlatformError(
                "unavailable", f"Risk Platform network error: {e}",
            ) from e

        if response.status_code in (401, 403):
            raise RiskPlatformError(
                "auth",
                f"Risk Platform authentication failed (status {response.status_code})",
                status_code=response.status_code,
            )
        if response.status_code >= 500 or response.status_code == 404:
            # 404 on these read APIs means upstream has no record/route — an
            # upstream problem for a well-formed request, not caller's fault.
            raise RiskPlatformError(
                "unavailable" if response.status_code >= 500 else "http",
                f"Risk Platform returned {response.status_code} for {path}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise RiskPlatformError(
                "http",
                f"Risk Platform rejected request ({response.status_code})",
                status_code=response.status_code,
            )

        try:
            return response.json()
        except ValueError as e:
            raise RiskPlatformError(
                "malformed",
                f"Risk Platform returned invalid JSON for {path}",
            ) from e

    @staticmethod
    def _run(coro: Any) -> Any:
        """Run a coroutine whether or not an event loop is already running
        (mirrors the established backend pattern using a worker thread)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()

    # --- public API -----------------------------------------------------------

    async def get_case_evidence_raw(
        self, user_id: str, expose_complete_records: bool = False,
    ) -> dict[str, Any]:
        """GET /api/risk/cases/{user_id}/evidence → raw dict.

        `expose_complete_records` opts into ALL stored transaction/withdrawal
        records instead of RP's default top-5 representative subset — only
        for concrete-evidence-level investigation, never as the default."""
        return await self._request(
            "GET",
            f"/api/risk/cases/{user_id}/evidence"
            + ("?expose_complete_records=true" if expose_complete_records else ""),
        )

    async def get_case_explanation_raw(self, user_id: str) -> dict[str, Any]:
        """POST /api/risk/explain → raw dict."""
        return await self._request(
            "POST", "/api/risk/explain", json_body={"user_id": user_id},
        )

    def fetch_case(self, user_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch both authoritative RP payloads for one case (sync wrapper).

        Returns (evidence_response, explanation_response). Raises
        RiskPlatformError on transport/auth/malformed failure.
        """
        evidence = self._run(self.get_case_evidence_raw(user_id))
        explanation = self._run(self.get_case_explanation_raw(user_id))
        return evidence, explanation

    def fetch_case_evidence(
        self, user_id: str, expose_complete_records: bool = False,
    ) -> dict[str, Any]:
        """Sync wrapper: GET /api/risk/cases/{user_id}/evidence only."""
        return self._run(self.get_case_evidence_raw(
            user_id, expose_complete_records=expose_complete_records,
        ))


# ---------------------------------------------------------------------------
# Normalization: RP shapes → Agent domain objects
# ---------------------------------------------------------------------------

# Detection source used by the RP inside explanation text is not structured;
# we derive SignalRef presence from structured rule_evidence / feature data
# below. Nothing here invents attribution that RP did not provide.

_FEATURE_TO_SIGNAL = {
    # feature_evidence key → signal name mirroring RP's supporting_feature
    "shared_device_count": "shared_device_count",
    "linked_account_count": "linked_account_count",
    "trade_frequency_24h": "trade_frequency_24h",
    "withdrawal_risk_score": "withdrawal_risk_score",
}


def derive_capabilities(
    *,
    finding_name: str,
    transaction_evidence_count: int = 0,
    withdrawal_evidence_count: int = 0,
    opposite_trade_ratio: float | None = None,
    has_rule_trigger: bool,
) -> FindingCapability:
    """Deterministic, testable capability derivation from ACTUAL available
    evidence. Never type-based blanket grants.

    Rules (Week 1):
    - timeline         : more than one timestamped evidence item exists for the
                         case (timeline needs multiple ordered events).
    - opposite_trades  : actual trade-composition data exists — i.e. the case
                         carries trade evidence AND this finding is about the
                         opposite-trade composition (rule trigger or contextual
                         ratio finding).
    - signal_explain   : there is a real detection signal behind the finding
                         (triggered rule), OR ML detector signal exists.

    Deliberately NOT derived here: policy_lookup. Policy retrieval is a
    case-wide capability (RP exposes its single validated-citation surface
    to every finding); whether a finding carries an authoritative
    FINDING-LEVEL policy basis is a DATA question, answered by
    finding.policy_refs presence — never conflated with retrieval support.
    """
    caps: set[str] = set()

    multiple_timestamped = (
        transaction_evidence_count + withdrawal_evidence_count > 1
    )

    if multiple_timestamped:
        caps.add("timeline")

    is_opposite_trade_finding = (
        "coordinated trading" in finding_name.lower()
        or "opposite trade" in finding_name.lower()
    )
    if is_opposite_trade_finding and opposite_trade_ratio is not None:
        caps.add("opposite_trades")

    # signal_explain: a real detection signal backs the finding — either an
    # explicit rule trigger, or the finding belongs to RP's detector-produced
    # naming vocabulary (ML detector signal, graph/device findings, observed
    # behavioral features). RP only emits findings that its canonical
    # pipeline detected, so these keywords mirror actual RP finding names.
    name_l = finding_name.lower()
    detector_named = any(kw in name_l for kw in (
        "ml pattern detection", "shared device", "linked account",
        "network", "trading frequency", "withdrawal",
    ))
    if has_rule_trigger or detector_named:
        caps.add("signal_explain")

    return FindingCapability.model_validate(sorted(caps))


def normalize_case(
    *,
    case_id: str,
    evidence: dict[str, Any],
    explanation: dict[str, Any],
) -> dict[str, Any]:
    """Normalize RP responses into the canonical case payload.

    Output shape:
      {
        case_id,
        findings: [Finding...],           # conforming to app.models.Finding
        evidence_refs: [...],
        signal_refs: [...],
        policy_refs: [...],
      }

    Ordering source for deterministic IDs: `explanation.key_findings` — the
    Risk Platform's authoritative ordered canonical-findings list produced by
    its own get_canonical_evidence pipeline. Findings are numbered F1..Fn in
    that order. A Finding the explanation does not name is NOT fabricated;
    structured rule_evidence items not present among key_findings are appended
    after them in RP's rule_evidence order.
    """
    user_id = evidence.get("user_id") or case_id

    risk_summary = evidence.get("risk_summary") or {}
    transactions = evidence.get("transaction_evidence") or []
    withdrawals = evidence.get("withdrawal_evidence") or []
    features = evidence.get("feature_evidence") or {}
    rules = evidence.get("rule_evidence") or []
    network = evidence.get("network_evidence") or None

    # -- case-level reference lists (mirror only what RP returned) ----------
    evidence_refs: list[EvidenceRef] = []
    for tx in transactions:
        evidence_refs.append(EvidenceRef(kind="transaction", id=tx.get("transaction_id", "")))
    for wd in withdrawals:
        evidence_refs.append(EvidenceRef(kind="withdrawal", id=wd.get("withdrawal_id", "")))

    signal_refs: list[SignalRef] = []
    ml_score = risk_summary.get("ml_score")
    if ml_score is not None:
        signal_refs.append(SignalRef(signal_type="ML", name="ml_score"))
    graph_score = risk_summary.get("graph_score")
    if graph_score:
        signal_refs.append(SignalRef(signal_type="Graph", name="graph_score"))
    for rule in rules:
        signal_refs.append(SignalRef(signal_type="Rule", name=rule.get("rule_name", "")))
    for feat_key in _FEATURE_TO_SIGNAL:
        if features.get(feat_key):
            signal_refs.append(SignalRef(signal_type="Feature", name=feat_key))

    policy_refs = [
        PolicyRef(
            citation_id=c.get("id"),
            chunk_id=c.get("chunk_id"),
            doc=c.get("doc"),
            section=c.get("section"),
        )
        for c in (explanation.get("citations") or [])
    ]

    # -- structural checks before trusting the payload ----------------------
    required_sections = ("user_id", "risk_summary")
    missing = [k for k in required_sections if k not in evidence]
    if missing:
        raise RiskPlatformError(
            "malformed",
            f"Evidence response missing required sections: {missing}",
        )

    # -- findings -------------------------------------------------------------
    key_findings: list[str] = explanation.get("key_findings") or []
    rule_names = {r.get("rule_name", "") for r in rules}

    transaction_count = len(transactions)
    withdrawal_count = len(withdrawals)
    opp_ratio = features.get("opposite_trade_ratio")
    ml_present = ml_score is not None

    findings: list[Finding] = []

    def build_finding(
        name: str, summary: str, *, finding_id: str,
        severity_hint: str = "unknown",
        rule_triggered: bool, finding_policy_refs: list[PolicyRef],
    ) -> Finding:
        finding_specific_refs = [
            r for r in signal_refs
            if _finding_uses_signal(name, r.name, evidence, network)
        ]
        related_evidence = [
            r for r in evidence_refs
            if _evidence_relates_to_finding(name, r.kind, evidence)
        ]
        capabilities = derive_capabilities(
            finding_name=name,
            transaction_evidence_count=transaction_count,
            withdrawal_evidence_count=withdrawal_count,
            opposite_trade_ratio=opp_ratio if related_evidence else None,
            has_rule_trigger=rule_triggered or bool(finding_specific_refs),
        )
        return Finding(
            finding_id=finding_id,
            case_id=user_id,
            type=_classify_finding_type(name, summary),
            title=name,
            severity=severity_hint,
            summary=summary,
            evidence_refs=related_evidence,
            signal_refs=finding_specific_refs,
            policy_refs=finding_policy_refs,
            capabilities=capabilities,
            ext={},
        )

    # 1) Ordered canonical findings from explanation.key_findings (F1..Fn).
    #    The text embeds optional [n] markers; names are the leading phrase.
    #    Citation association is MARKER-ONLY: a finding's policy_refs mirror
    #    exactly the citations its own authoritative text marks with [n].
    #    Case-level citations that appear elsewhere in the explanation are
    #    exposed via the case-level policy_refs list — never copied onto
    #    unmarked findings (no heuristic text matching; RP marker ids only).
    #
    #    Cross-section identity: rule_evidence names are matched against
    #    explanation names through _normalize_finding_name so the SAME
    #    authoritative finding is never appended twice merely because RP
    #    renders the name with/without sentence punctuation.
    n_tx = len(transactions)
    n_wd = len(withdrawals)
    index = 1
    seen_names: set[str] = set()
    for kf_text in key_findings:
        name, summary = _split_key_finding(kf_text)
        name_l = name.lower()
        seen_names.add(_normalize_finding_name(name))
        marked_ids = list(dict.fromkeys(
            m.group(1) for m in _CITATION_MARKER_RE.finditer(kf_text)
        ))
        by_id = {
            str(p.citation_id): p for p in policy_refs
            if p.citation_id is not None
        }
        policy_for_finding = [
            by_id[mid] for mid in marked_ids if mid in by_id
        ]

        rule_triggered = any(rn and rn.lower() in name_l for rn in rule_names)
        severity = _severity_from_rules(rules, name)
        f = build_finding(
            name, summary, finding_id=f"F{index}", severity_hint=severity,
            rule_triggered=rule_triggered,
            finding_policy_refs=policy_for_finding,
        )
        findings.append(f)
        index += 1

    # 2) Structured rule findings the explanation did not mention (appended,
    #    preserving RP rule_evidence order) — still never invented content.
    #    Identity uses the normalized name: a rule already covered by the
    #    explanation is NOT a separate finding (no duplicate F10–F12).
    for rule in rules:
        rname = rule.get("rule_name", "")
        if rname and _normalize_finding_name(rname) not in seen_names:
            f = build_finding(
                rname, rule.get("description", ""),
                finding_id=f"F{index}",
                severity_hint=(rule.get("severity") or "unknown").lower(),
                rule_triggered=True,
                finding_policy_refs=[],
            )
            findings.append(f)
            seen_names.add(_normalize_finding_name(rname))
            index += 1

    return {
        "case_id": user_id,
        "findings": findings,
        "evidence_refs": evidence_refs,
        "signal_refs": signal_refs,
        "policy_refs": policy_refs,
        "ext": {
            "risk_level": risk_summary.get("risk_level"),
            "risk_score": risk_summary.get("risk_score"),
            "ml_score": ml_score,
            "rule_score": risk_summary.get("rule_score"),
            "graph_score": graph_score,
            "recommended_action": risk_summary.get("recommended_action"),
            "missing_info": explanation.get("missing_info") or [],
            "explanation_source": explanation.get("explanation_source"),
        },
    }


_CITATION_MARKER_RE = __import__("re").compile(r"\[(\d+)\]")


def _normalize_finding_name(name: str) -> str:
    """Canonical form for cross-section finding-name identity.

    RP's explanation text renders finding names with sentence punctuation
    ('High withdrawal frequency.') while rule_evidence.rule_name carries the
    same canonical name without it ('High withdrawal frequency'). Comparing
    raw strings would treat the same authoritative finding as two distinct
    ones and duplicate it. Normalization is punctuation/case-folding only —
    it never merges genuinely different RP findings.
    """
    return " ".join(name.replace(".", " ").replace("—", " ").split()).lower().strip()


def _split_key_finding(text: str) -> tuple[str, str]:
    """Split 'N. Name [k]' / 'N. Name — detail' / 'N. Name\\ndetail'
    into (name, summary). RP formats seen in the wild:
      '2. Coordinated Trading Pattern [1]'
      '1. ML Pattern Detection — 96.24/100; a system signal'
      '9. Opposite trade ratio\\nAn opposite-trade ratio of ...'"""
    import re as _re
    t = text.strip()
    body = t.split(". ", 1)[1] if ". " in t[:6] else t
    first_line, nl_detail = (body.split("\n", 1) + [""])[:2]
    em_dash_parts = first_line.split(" — ", 1)
    if len(em_dash_parts) == 2 and not _CITATION_MARKER_RE.search(first_line):
        name = em_dash_parts[0].strip()
        detail = em_dash_parts[1].strip()
    else:
        name = first_line.strip()
        detail = nl_detail.strip()
    name = _re.sub(r"\s*\[\d+\]\s*$", "", name).strip()
    summary = detail or name
    return name, summary


def _classify_finding_type(name: str, summary: str) -> str:
    """Classify for provenance display only — based on RP-provided naming; no
    threshold/scoring logic is reimplemented here."""
    l = (name + " " + summary).lower()
    if "ml pattern detection" in l:
        return "detector_signal"
    if any(w in l for w in ("device", "network", "linked account")):
        return "graph_signal"
    if any(w in l for w in ("rule", "threshold", "exceeded", "triggering")):
        return "rule_signal"
    return "feature_observation"


def _severity_from_rules(rules: list[dict[str, Any]], name: str) -> str:
    lname = name.lower()
    for rule in rules:
        rn = (rule.get("rule_name") or "").lower()
        if rn and (rn in lname or lname in rn):
            return (rule.get("severity") or "unknown").lower()
    return "unknown"


def _finding_uses_signal(
    finding_name: str, signal_name: str, evidence: dict[str, Any], network: Any,
) -> bool:
    """Whether this finding plausibly draws on that signal, per RP structure:
    detector signal belongs to the ML-detector finding; Rule signals belong to
    the rule-named finding; Feature signals belong to the finding whose
    supporting_feature matches."""
    fl = finding_name.lower()
    sl = signal_name.lower()
    if sl == "ml_score":
        return fl.startswith("ml pattern detection")
    if sl.startswith(("shared_device", "linked_account")):
        return any(w in fl for w in ("device", "linked account", "network"))
    if sl == "trade_frequency_24h":
        return "trading frequency" in fl or "trading" in fl
    if sl == "withdrawal_risk_score":
        return "withdrawal" in fl
    return False


def _evidence_relates_to_finding(
    finding_name: str, kind: str, evidence: dict[str, Any],
) -> bool:
    fl = finding_name.lower()
    if kind == "withdrawal":
        return "withdrawal" in fl
    if kind == "transaction":
        return ("trading" in fl or "trade" in fl or "pattern detection" in fl)
    return False


# ---------------------------------------------------------------------------
# Timeline normalization (finding_drilldown view="timeline")
#
# Confirmed RP timestamp sources (schemas.py, database.py):
#   - TransactionEvidence.timestamp      (optional str)
#   - WithdrawalEvidence.timestamp       (optional str)
#   - RiskSummary.detected_at            (optional str; detection event)
# Network/device evidence carries NO timestamps in the current RP API and is
# therefore never given a synthetic one.
# ---------------------------------------------------------------------------

def normalize_timeline_events(
    *,
    case_id: str,
    evidence: dict[str, Any],
    finding: Any,
) -> list[dict[str, Any]]:
    """Build deterministic TimelineEvent dicts for one finding from RP
    evidence already fetched via GET /api/risk/cases/{user_id}/evidence.

    Returns a list of dicts (validated into TimelineEvent by the tool layer).
    Events use ONLY timestamps present in RP data; sources without timestamps
    contribute no events. Each event carries ≥1 real evidence_ref.
    """
    finding_lower = finding.title.lower()

    # Structural integrity: a broken/incomplete payload is malformed, not empty.
    required_sections = ("user_id", "risk_summary")
    missing = [k for k in required_sections if k not in evidence]
    if missing:
        raise RiskPlatformError(
            "malformed",
            f"Evidence response missing required sections: {missing}",
        )

    # Which evidence streams are relevant to THIS finding (conservative,
    # name-keyword based, consistent with the case normalizer's rules).
    wants_transactions = any(
        kw in finding_lower for kw in ("trade", "trading", "transaction", "pattern detection")
    )
    wants_withdrawals = "withdrawal" in finding_lower
    wants_detection = True   # detection event anchors most findings' start

    raw_events: list[dict[str, Any]] = []

    transactions = evidence.get("transaction_evidence") or []
    withdrawals = evidence.get("withdrawal_evidence") or []
    risk_summary = evidence.get("risk_summary") or {}
    rules = evidence.get("rule_evidence") or []
    citations = []   # policy refs are attached by the tool layer from case context

    # --- transaction events ------------------------------------------------
    if wants_transactions:
        for idx, tx in enumerate(transactions):
            ts = tx.get("timestamp")
            if not ts:
                continue
            raw_events.append({
                "_source": "transaction",
                "_order": idx,
                "timestamp": ts,
                "event_type": "transaction",
                "summary": (
                    f"{tx.get('side', 'Trade').upper()} trade of "
                    f"{tx.get('quantity', 0)} {tx.get('symbol', '')} "
                    f"({tx.get('value', 0):,.2f} value)"
                ),
                "evidence_refs": [{"kind": "transaction",
                                   "id": tx.get("transaction_id", "")}],
                "signal_refs": _finding_signal_refs(finding),
                "policy_refs": [],
                "importance": _event_importance(risk_reason=tx.get("risk_reason"),
                                                rule=(_matching_rule(finding, rules))),
            })

    # --- withdrawal events ---------------------------------------------------
    if wants_withdrawals:
        for idx, wd in enumerate(withdrawals):
            ts = wd.get("timestamp")
            if not ts:
                continue
            new_addr = wd.get("is_new_address")
            suffix = " to a newly encountered address" if new_addr else ""
            raw_events.append({
                "_source": "withdrawal",
                "_order": idx,
                "timestamp": ts,
                "event_type": "withdrawal",
                "summary": (
                    f"Withdrawal of {wd.get('amount', 0)} {wd.get('asset', '')}"
                    f"{suffix}"
                ),
                "evidence_refs": [{"kind": "withdrawal",
                                   "id": wd.get("withdrawal_id", "")}],
                "signal_refs": _finding_signal_refs(finding),
                "policy_refs": [],
                "importance": _event_importance(
                    risk_reason=wd.get("risk_reason"),
                    rule=_matching_rule(finding, rules),
                    new_address=new_addr,
                ),
            })

    # --- detection event (risk_summary.detected_at) ----------------------------
    detected_at = risk_summary.get("detected_at")
    if wants_detection and detected_at:
        rule = _matching_rule(finding, rules)
        parts = []
        if rule:
            parts.append(f"rule {rule.get('rule_name')!r} triggered")
        if risk_summary.get("ml_score") is not None and "pattern detection" in finding_lower:
            parts.append(f"ML pattern score {risk_summary['ml_score']}/100")
        summary = (
            (f"Case detected: " + "; ".join(parts)) if parts
            else "Case flagged by the detection system"
        )
        raw_events.append({
            "_source": "detection",
            "_order": 0,
            "timestamp": detected_at,
            "event_type": "detection",
            "summary": summary,
            "evidence_refs": [{"kind": "risk_event", "id": case_id}],
            "signal_refs": _finding_signal_refs(finding),
            "policy_refs": [],
            "importance": (
                _severity_to_importance(rule.get("severity"))
                if rule else "high" if (risk_summary.get("risk_level") or "").lower()
                in ("critical", "high") else "medium"
            ),
        })

    # --- deterministic ordering: timestamp, then stable secondary key --------
    raw_events.sort(key=lambda e: (
        e["timestamp"],
        e["_source"],
        e["_order"],
        e["summary"],
    ))

    # --- deterministic IDs (post-ordering, so F{n}/E{n} are stable) ----------
    events: list[dict[str, Any]] = []
    for i, e in enumerate(raw_events, start=1):
        events.append({
            "event_id": f"{finding.finding_id}-E{i:03d}",
            "finding_id": finding.finding_id,
            "timestamp": e["timestamp"],
            "event_type": e["event_type"],
            "summary": e["summary"],
            "evidence_refs": e["evidence_refs"],
            "signal_refs": e["signal_refs"],
            "policy_refs": [],
            "importance": e["importance"],
        })
    return events


def _matching_rule(finding: Any, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    fl = finding.title.lower()
    for rule in rules:
        rn = (rule.get("rule_name") or "").lower()
        if rn and (rn in fl or fl in rn
                   or any(w in fl for w in rn.split() if len(w) > 3)):
            return rule
    return None


# ---------------------------------------------------------------------------
# Concrete evidence normalization (finding_drilldown view="evidence")
#
# Purpose: the COMPLETE authoritative record set (all RP-stored withdrawals /
# transactions for the case), fetched via RP's opt-in expose_complete_records
# surface — never the default top-5 representative subset. Records are
# mirrored verbatim; nothing is filtered to a preview and nothing is
# fabricated when a stream is empty.
# ---------------------------------------------------------------------------

def normalize_evidence_records(
    *,
    case_id: str,
    evidence: dict[str, Any],
    finding: Any,
    stream: str | None = None,
) -> dict[str, Any]:
    """Build the complete concrete-evidence payload for one finding.

    `stream` ("withdrawals" | "transactions" | None): an EXPLICIT
    evidence-stream scope supplied by the caller (derived from the user's
    request). When set it GOVERNS stream selection and overrides the
    finding-title heuristic — a withdrawal request can never silently
    return transaction records. None keeps the conservative title-keyword
    rule for unscoped evidence requests.

    Returns {records: [...], streams} where each record mirrors an actual RP
    withdrawal/transaction row (id, core fields, timestamp, risk_reason).
    Nothing is truncated here — completeness is the contract of this view.
    """
    finding_lower = finding.title.lower()

    # Stream selection: an explicit stream scope governs; otherwise the
    # conservative title-keyword rule applies (unscoped evidence requests).
    if stream == "withdrawals":
        wants_transactions, wants_withdrawals = False, True
    elif stream == "transactions":
        wants_transactions, wants_withdrawals = True, False
    else:
        # Same conservative stream selection as the timeline normalizer.
        wants_transactions = any(
            kw in finding_lower for kw in ("trade", "trading", "transaction", "pattern detection")
        )
        wants_withdrawals = "withdrawal" in finding_lower

    withdrawals = evidence.get("withdrawal_evidence") or []
    transactions = evidence.get("transaction_evidence") or []

    records: list[dict[str, Any]] = []

    if wants_withdrawals:
        for wd in withdrawals:
            new_addr = wd.get("is_new_address")
            records.append({
                "record_kind": "withdrawal",
                "record_id": wd.get("withdrawal_id", ""),
                "summary": (
                    f"Withdrawal of {wd.get('amount', 0)} {wd.get('asset', '')}"
                    + (" to a newly encountered address" if new_addr else "")
                ),
                "amount": wd.get("amount"),
                "asset": wd.get("asset"),
                "address": wd.get("address"),
                "is_new_address": new_addr,
                "timestamp": wd.get("timestamp"),
                "risk_reason": wd.get("risk_reason"),
            })

    if wants_transactions:
        for tx in transactions:
            records.append({
                "record_kind": "transaction",
                "record_id": tx.get("transaction_id", ""),
                "summary": (
                    f"{tx.get('side', 'Trade').upper()} trade of "
                    f"{tx.get('quantity', 0)} {tx.get('symbol', '')} "
                    f"({tx.get('value', 0):,.2f} value)"
                ),
                "symbol": tx.get("symbol"),
                "side": tx.get("side"),
                "quantity": tx.get("quantity"),
                "value": tx.get("value"),
                "timestamp": tx.get("timestamp"),
                "risk_reason": tx.get("risk_reason"),
            })

    # Deterministic order: timestamp, then id — stable across calls.
    records.sort(key=lambda r: (r.get("timestamp") or "", r.get("record_id", "")))

    # Feature-level facts are NOT records — they are carried separately so
    # aggregate counts ("7 withdrawals in 24h") are never conflated with the
    # concrete record list.
    features = evidence.get("feature_evidence") or {}
    relevant_features = {
        k: features[k] for k in (
            "withdrawal_frequency_24h", "withdrawal_volume_24h",
            "withdrawal_risk_score", "trade_frequency_24h",
            "trade_frequency_7d", "trade_volume_24h",
            "opposite_trade_ratio", "shared_device_count",
            "linked_account_count", "account_age_days",
        ) if features.get(k) is not None
    }

    return {
        "records": records,
        "record_count": len(records),
        "risk_features": relevant_features,
        "streams": {
            "withdrawals_included": wants_withdrawals,
            "transactions_included": wants_transactions,
        },
    }


def _finding_signal_refs(finding: Any) -> list[dict[str, Any]]:
    """Mirror the finding's own signal_refs (already RP-derived at case fetch
    time) — no new attribution is invented here."""
    return [s.model_dump() for s in finding.signal_refs]


def _severity_to_importance(severity: str | None) -> str:
    mapping = {"CRITICAL": "high", "HIGH": "high",
               "MEDIUM": "medium", "LOW": "low"}
    return mapping.get((severity or "").upper(), "medium")


def _event_importance(
    *,
    risk_reason: str | None,
    rule: dict[str, Any] | None,
    new_address: bool | None = None,
) -> str:
    """Deterministic importance rule:
    1. rule severity (HIGH/CRITICAL → high, LOW → low) when a rule backs the
       finding,
    2. else explicit risk markers (new-address withdrawals → high),
    3. else medium as the conservative default (EVENT-level default; never
       fabricated as 'critical' without support)."""
    if rule:
        return _severity_to_importance(rule.get("severity"))
    if new_address:
        return "high"
    if risk_reason and any(w in risk_reason.lower()
                           for w in ("large", "sudden", "new address", "burst")):
        return "high"
    return "medium"


# ---------------------------------------------------------------------------
# Signal explanation normalization (signal_explain tool)
#
# Sources (all confirmed in RP source, no new endpoints):
#   Rule  : rule_evidence[] {rule_name, severity, description, trigger{},
#           threshold, contribution}
#   ML    : risk_summary.ml_score + feature_evidence{} (values only — RP
#           exposes NO per-transaction/feature attribution anywhere)
#   Graph : network_evidence{cluster_*, related_accounts[], shared_devices[]}
#           (no relationship paths exposed)
# ---------------------------------------------------------------------------

def _require_evidence_shape(evidence: dict[str, Any]) -> None:
    """Structural integrity shared by the signal normalizers: a payload
    without user_id/risk_summary is malformed upstream data, not empty."""
    missing = [k for k in ("user_id", "risk_summary") if k not in evidence]
    if missing:
        raise RiskPlatformError(
            "malformed",
            f"Evidence response missing required sections: {missing}",
        )


def normalize_rule_signal(
    *, finding: Any, evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """Rule-signal explanation payload for one finding, from matching
    rule_evidence. Returns None when no rule actually backs the finding."""
    _require_evidence_shape(evidence)
    rules = evidence.get("rule_evidence") or []
    fl = finding.title.lower()
    for rule in rules:
        rn = (rule.get("rule_name") or "").lower()
        # Strict anchoring only: full rule name inside the finding title or
        # vice versa (both ≥4 chars). Loose word-overlap matching is unsafe —
        # e.g. "pattern" would wrongly attach the coordinated-trading rule to
        # the ML Pattern Detection finding.
        if rn and len(rn) >= 4 and len(fl) >= 4 and (rn in fl or fl in rn):
            trigger = rule.get("trigger") or {}
            return {
                "signal_type": "Rule",
                "rule": {
                    "name": rule.get("rule_name"),
                    "severity": rule.get("severity"),
                    "description": rule.get("description"),
                    # actual observed values and threshold exactly as RP
                    # reports them — never reconstructed or re-derived here
                    "trigger_values": dict(trigger),
                    "threshold": rule.get("threshold"),
                    "contribution": rule.get("contribution"),
                },
                "evidence_refs": [
                    {"kind": "risk_event",
                     "id": evidence.get("user_id", "")},
                ],
            }
    return None


def normalize_ml_signal(
    *, finding: Any, evidence: dict[str, Any],
) -> dict[str, Any]:
    """ML-signal explanation payload. Score + available feature values only.
    Attribution data does not exist in the RP API — that absence is reported
    via evidence_missing by the tool layer, never invented here."""
    _require_evidence_shape(evidence)
    risk_summary = evidence.get("risk_summary") or {}
    features = evidence.get("feature_evidence") or {}
    ml_score = risk_summary.get("ml_score")
    available_features = {k: v for k, v in features.items() if v is not None}

    explanation: dict[str, Any] = {}
    evidence_missing = False
    next_data_needed: list[str] = []

    if ml_score is not None:
        explanation["ml_score"] = ml_score
        explanation["score_interpretation"] = (
            "system signal, not a calibrated probability of fraud"
        )
    else:
        evidence_missing = True
        next_data_needed.append("ML model score for this case")

    if available_features:
        explanation["available_feature_values"] = available_features
    else:
        evidence_missing = True
        next_data_needed.append("feature values for this case")

    if ml_score is not None and not evidence_missing:
        # Score and features exist, but per-feature attribution does not.
        evidence_missing = True
        next_data_needed.append("transaction-level feature attribution")

    explanation["attribution_available"] = False

    return {
        "signal_type": "ML",
        "explanation": explanation,
        "evidence_refs": [
            {"kind": "risk_event", "id": evidence.get("user_id", "")},
        ],
        "evidence_missing": evidence_missing,
        "next_data_needed": next_data_needed,
    }


def normalize_graph_signal(
    *, finding: Any, evidence: dict[str, Any],
) -> dict[str, Any]:
    """Graph-signal explanation payload from network_evidence only.
    Relationship paths do not exist in the RP API and are never synthesized."""
    _require_evidence_shape(evidence)
    network = evidence.get("network_evidence") or None
    fl = finding.title.lower()
    graph_relevant = any(w in fl for w in ("device", "linked account", "network"))

    explanation: dict[str, Any] = {}
    evidence_missing = False
    next_data_needed: list[str] = []

    if network:
        explanation["cluster"] = {
            "cluster_id": network.get("cluster_id"),
            "cluster_name": network.get("cluster_name"),
            "detection_type": network.get("detection_type"),
            "member_count": network.get("member_count"),
            "cluster_risk_score": network.get("cluster_risk_score"),
        }
        if network.get("related_accounts"):
            explanation["related_accounts"] = list(network["related_accounts"])
        if network.get("shared_devices"):
            explanation["shared_devices"] = list(network["shared_devices"])
    else:
        evidence_missing = True
        next_data_needed.append("network/cluster evidence for this case")

    if not graph_relevant and not evidence_missing:
        # Graph data exists case-wide but this finding is not a graph finding;
        # the data cannot explain it.
        evidence_missing = True
        next_data_needed.append("graph signal associated with this finding")

    explanation["relationship_paths_available"] = False

    return {
        "signal_type": "Graph",
        "explanation": explanation,
        "evidence_refs": (
            [{"kind": "risk_event", "id": evidence.get("user_id", "")}]
            if network else []
        ),
        "evidence_missing": evidence_missing,
        "next_data_needed": next_data_needed,
    }


# ---------------------------------------------------------------------------
# Policy lookup normalization (policy_lookup tool)
#
# RP exposes NO dedicated policy-search HTTP endpoint; its only HTTP policy
# surface is the validated citations[] returned by POST /api/risk/explain
# (the same payload risk_case_fetch already consumes — finding-associated,
# semantically validated, metadata-filtered by RP's own pipeline).
#
# policy_lookup therefore: fetches the case's authoritative citations and
# deterministically ranks them against the investigator's topic (keyword
# overlap scoring over quote/section/doc text — presentation-level filtering
# of RP's own results, NOT a new retrieval engine).
# ---------------------------------------------------------------------------

def normalize_policy_matches(
    *, citations: list[dict[str, Any]], topic: str,
) -> list[dict[str, Any]]:
    """Rank RP citations against the topic by deterministic keyword overlap.

    Scoring counts topic keywords appearing in the citation's quote/section/
    doc text (case-insensitive). Citations keep RP's own citation ids; no
    scores/ids are invented. Zero-overlap citations still match (score 0) so
    the caller can distinguish 'cited but off-topic' from 'nothing cited'.
    """
    import re as _re

    def _tokens(text: str) -> set[str]:
        return {t for t in _re.split(r"[\s\W_]+", (text or "").lower()) if len(t) > 2}

    topic_tokens = _tokens(topic)
    matches: list[dict[str, Any]] = []
    for c in citations:
        quote = c.get("quote") or ""
        section = c.get("section") or ""
        doc = c.get("doc") or ""
        blob = _tokens(f"{quote} {section} {doc}")
        relevance = len(topic_tokens & blob) if topic_tokens else 0
        matches.append({
            "citation_id": c.get("id"),
            "chunk_id": c.get("chunk_id"),
            "document": doc,
            "section": section,
            "snippet": quote,
            # RP exposes no relevance score; this is a deterministic
            # topic-overlap count computed at the boundary (never an RP value)
            "relevance": relevance,
        })
    matches.sort(key=lambda m: (-m["relevance"], str(m["citation_id"])))
    return matches


def split_policy_refs(
    *, finding: Any, matches: list[dict[str, Any]],
) -> tuple[list[PolicyRef], list[PolicyRef]]:
    """Partition normalized matches into (already-associated, newly-retrieved)
    relative to the finding's authoritative policy_refs."""
    known = {
        (p.citation_id, p.chunk_id) for p in finding.policy_refs
    }
    associated, new = [], []
    for m in matches:
        ref = PolicyRef(
            citation_id=m.get("citation_id"),
            chunk_id=m.get("chunk_id"),
            doc=m.get("document"),
            section=m.get("section"),
        )
        key = (ref.citation_id, ref.chunk_id)
        (associated if key in known else new).append(ref)
    return associated, new
