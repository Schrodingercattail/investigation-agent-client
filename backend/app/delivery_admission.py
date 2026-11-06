"""WP-2 Version B — shared deterministic delivery admission service.

Sole authority: docs/architecture/R2_GOAL_COVERAGE_AND_DELIVERY_SAFETY_V1.md,
Phase 3 "Version B · DESIGN FROZEN" (Final Rulings 1-4 + the P3
verification pipeline). This module is the SINGLE implementation of the
admission semantics; the loop (S3) and /turn call it — they never
re-implement the rules.

PURE MODULE: no store access, no LLM, no I/O. Callers (S3 wiring) fetch
session state and pass it in. Deterministic: same inputs -> same
verdicts, so resume/retry re-verification is exact.

V1 frozen constraints honored here:
- one delivery target per generate_artifact step (DT-{step_id}-1);
  multi-target steps fail closed (ruling 1) — the ordinal structure is
  a reserved extension, NOT a supported capability;
- NO auto-bind from a unique candidate set (user ruling): a target is
  bound only via an explicit `CS-…` reference in the CURRENT request
  text, exact-matched against the session's persisted candidate_sets;
- per-target independence: one target's verdict never authorizes or
  cancels another (ruling 3);
- same-run unpersisted sets are invisible (V1 fail closed, ruling 7) —
  this module only sees what the caller read from the session store;
- Case Intake exemption: requests whose classified intent is not
  ARTIFACT_* pass through untouched (single-source intent classifier).
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

# --- canonical records digest (Final Ruling 2, frozen formula) -----------


def canonical_records_digest(records) -> str:
    """Frozen canonicalization (design: Final Ruling 2) — the ONE shared
    implementation for the injection side (loop) and the verification
    side (tool). sort_keys recursive; ensure_ascii fixed; compact
    separators; allow_nan=False makes non-finite values raise ValueError
    (callers fail closed); null vs missing and int vs float are
    naturally distinct; list order is content; unknown fields are
    included."""
    payload = json.dumps(records, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- explicit CS-ref extraction ------------------------------------------

_SET_REF_RE = re.compile(r"\bCS-[A-Za-z0-9_-]+")


def extract_explicit_set_refs(request_text: str) -> list[str]:
    """Explicit user intent evidence: every `CS-…` token in the CURRENT
    request text, first-occurrence order, deduplicated. (The contract-
    path helper uses re.search — first match only; admission needs the
    full multiset for the ref/target pairing rules.)"""
    seen: set[str] = set()
    refs: list[str] = []
    for m in _SET_REF_RE.finditer(request_text or ""):
        ref = m.group(0)
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


# --- delivery target identity (Final Ruling 1) ---------------------------


@dataclass
class DeliveryTarget:
    delivery_target_id: str
    step_id: str
    set_ref: str | None = None
    invalid_reason: str | None = None
    binding: dict | None = field(default=None)


def _target_invalid(target: DeliveryTarget, reason: str) -> DeliveryTarget:
    target.invalid_reason = reason
    return target


def enumerate_delivery_targets(plan_steps: list[dict],
                               explicit_refs: list[str]):
    """Enumerate the delivery targets of the ADOPTED plan (V1: exactly
    one implicit target per generate_artifact step) and pair the
    explicit refs. Fail-closed pairing (ruling 1 / frozen pairing
    rules): ref count != target count, or more than one ref, or a
    multi-target step => every affected target invalid
    (ambiguous_mapping); never inferred, never order-paired."""
    artifact_steps = [s for s in (plan_steps or [])
                      if s.get("type") == "generate_artifact"]
    targets: list[DeliveryTarget] = []
    for step in artifact_steps:
        step_id = step.get("step_id") or ""
        predeclared = (step.get("arguments") or {}).get(
            "delivery_targets") or []
        if len(predeclared) > 1:
            # reserved extension — NOT supported in V1 (ruling 1)
            targets.append(_target_invalid(
                DeliveryTarget(f"DT-{step_id}-0", step_id),
                "multi_target_step_unsupported"))
            continue
        target = DeliveryTarget(f"DT-{step_id}-1", step_id)
        if len(explicit_refs) == 1 and len(artifact_steps) == 1:
            target.set_ref = explicit_refs[0]
        elif not explicit_refs:
            target.set_ref = None      # no auto-bind (user ruling)
        else:
            _target_invalid(target, "ambiguous_mapping")
        targets.append(target)
    return targets


# --- binding resolution (frozen lifecycle) -------------------------------

_REQUIRED_LEG_FIELDS = ("transaction_id", "account_id", "side",
                        "quantity", "value", "symbol", "timestamp")


def _entry_wellformed(entry: dict) -> str | None:
    """Structural validation of one persisted session set entry.
    Returns a fail reason or None."""
    if not isinstance(entry, dict):
        return "malformed_entry"
    if not entry.get("set_ref"):
        return "malformed_entry"
    if not isinstance(entry.get("source_task_id"), str) \
            or not entry.get("source_task_id"):
        return "malformed_entry"
    candidates = entry.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return "empty_set"
    seen_ids = set()
    for c in candidates:
        if not isinstance(c, dict):
            return "malformed_entry"
        cid = c.get("candidate_id")
        if not cid or not isinstance(cid, str):
            return "malformed_entry"
        # frozen Final Ruling 2 test matrix: duplicate candidate_id is a
        # malformed entry (fail closed at admission — the single gate
        # before any injection).
        if cid in seen_ids:
            return "duplicate_candidate_id"
        seen_ids.add(cid)
        sup = c.get("supporting")
        if not isinstance(sup, list) or not sup:
            return "malformed_entry"
        for leg in sup:
            if not isinstance(leg, dict):
                return "malformed_entry"
            if any(leg.get(k) is None for k in _REQUIRED_LEG_FIELDS):
                return "malformed_entry"
    return None


def resolve_binding(target: DeliveryTarget, session_sets: list,
                    investigation_task_ids: set[str],
                    case_id: str | None = None) -> DeliveryTarget:
    """Resolve ONE target against the session's persisted candidate_sets.
    Fail-closed for: missing ref, no explicit ref (never auto-bind),
    duplicate set_ref, empty set, malformed entry, cross-investigation
    provenance. Exact string match only."""
    if target.invalid_reason:
        return target
    if not target.set_ref:
        return _target_invalid(target, "no_explicit_ref")
    matches = [e for e in (session_sets or [])
               if isinstance(e, dict)
               and e.get("set_ref") == target.set_ref]
    if not matches:
        return _target_invalid(target, "ref_not_in_session")
    if len(matches) > 1:
        return _target_invalid(target, "duplicate_set_ref")
    entry = matches[0]
    source_task_id = entry.get("source_task_id")
    if source_task_id not in (investigation_task_ids or set()):
        return _target_invalid(target, "cross_investigation")
    reason = _entry_wellformed(entry)
    if reason:
        return _target_invalid(target, reason)
    target.binding = {
        "kind": "candidate_set_export",
        "status": "bound_ready",
        "set_ref": target.set_ref,
        "source_task_id": source_task_id,
        "source_step_id": entry.get("source_step_id"),
        "delivery_target_id": target.delivery_target_id,
        "case_id": case_id,
    }
    return target


# --- admission (frozen per-target pipeline) ------------------------------

_HSG = "heuristic_single_goal"


def admit_delivery_targets(*, goal_contract, decision_outcome: str | None,
                           request_text: str, plan_steps: list[dict],
                           session_sets: list,
                           investigation_task_ids: set[str],
                           case_id: str,
                           hsg_only: bool = True) -> dict:
    """The frozen admission pipeline (design §5 pseudocode). Returns a
    deterministic verdict structure; the CALLER (S3) applies the strip/
    injection to the plan and renders the clarification.

    PASS_THROUGH classes (no admission): an accepted contract exists;
    the decision is not HSG; the plan has no generate_artifact step;
    the request intent is not ARTIFACT_* (Case Intake / read-only —
    single-source classifier, Case Intake exemption per user ruling).

    hsg_only: the loop path (a GU decision record exists) gates only the
    HSG class (default True). /turn has NO decision record — it calls
    with hsg_only=False so EVERY no-contract artifact delivery is gated
    (frozen /turn anti-bypass requirement). A recorded non-HSG decision
    still passes through on either path.

    Per-target: independent verdicts; valid targets carry the full
    injection payload (records verbatim from the bound entry + expected
    digest + expected binding envelope); invalid targets carry a
    fail-closed reason. No global pass, no cross-target effects."""
    from app.context_resolution import _classify_request_intent
    from app.models import RequestIntent

    if goal_contract is not None:
        return {"mode": "passthrough", "reason": "contract_path"}
    if hsg_only:
        if decision_outcome != _HSG:
            return {"mode": "passthrough", "reason": "not_hsg"}
    elif decision_outcome not in (None, _HSG):
        return {"mode": "passthrough", "reason": "not_hsg"}
    artifact_steps = [s for s in (plan_steps or [])
                      if s.get("type") == "generate_artifact"]
    if not artifact_steps:
        return {"mode": "passthrough", "reason": "no_delivery_target"}
    if _classify_request_intent(request_text or "") not in (
            RequestIntent.ARTIFACT_CASE, RequestIntent.ARTIFACT_FINDING):
        return {"mode": "passthrough", "reason": "intent_not_artifact"}

    explicit_refs = extract_explicit_set_refs(request_text)
    targets = enumerate_delivery_targets(plan_steps, explicit_refs)
    valid, invalid = [], []
    for target in targets:
        resolve_binding(target, session_sets, investigation_task_ids,
                        case_id=case_id)
        if target.invalid_reason:
            invalid.append({
                "delivery_target_id": target.delivery_target_id,
                "step_id": target.step_id,
                "reason": target.invalid_reason,
            })
            continue
        entry = next(e for e in (session_sets or [])
                     if e.get("set_ref") == target.set_ref)
        candidates = entry.get("candidates") or []
        try:
            expected_digest = canonical_records_digest(candidates)
        except (ValueError, TypeError):
            invalid.append({
                "delivery_target_id": target.delivery_target_id,
                "step_id": target.step_id,
                "reason": "non_finite_record_values",
            })
            continue
        inject = {
            "delivery_target_id": target.delivery_target_id,
            "step_id": target.step_id,
            "source_kind": "candidate_set",
            "set_ref": target.set_ref,
            "candidate_records": candidates,     # verbatim, this set only
            "bounded_data": bool(entry.get("bounded_data")),
            "source_task_id": entry.get("source_task_id"),
            "expected_records_digest": expected_digest,
            "expected_binding": target.binding,
        }
        valid.append(inject)
    return {
        "mode": "admitted",
        "targets": [t.delivery_target_id for t in targets],
        "valid": valid,
        "invalid": invalid,
        "case_id": case_id,
    }
