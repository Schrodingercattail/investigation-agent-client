"""Bounded clarification lifecycle state (WP-M2-1 WI-6).

Persistence + classification for the clarification control state:
when the runtime cannot autonomously Safe-Select, the clarification
becomes a resumable, hash-keyed condition — not just a text response.

Ledger key = (investigation_id, request_fingerprint, candidate_set_hash,
metadata_status_hash). Outcomes:

- CLARIFIED                   — first time this key is clarified (open)
- NON_SELECTABLE_TERMINAL     — the same key repeated with no meaningful
                                state change: bounded informational
                                terminal (never a third clarification)
- RESOLVED_BY_DESIGNATION     — the user explicitly designated a
                                candidate (existing focus path); intent
                                evidence ONLY — metadata status is never
                                modified by designation
- RESUMED_BY_REEVALUATION     — a new request fingerprint (meaningful
                                user clarification) re-entered evaluation

UNKNOWN discipline: metadata_status_hash covers the governed statuses of
the retained candidate set; any change (e.g. re-verification) changes
the key and resets loop protection. No chain-of-thought, no prompts, no
LLM reasoning is stored here.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from app.telemetry import emit_event

OUTCOME_CLARIFIED = "clarified"
OUTCOME_NON_SELECTABLE_TERMINAL = "non_selectable_terminal"
OUTCOME_RESOLVED_BY_DESIGNATION = "resolved_by_designation"
OUTCOME_RESUMED_BY_REEVALUATION = "resumed_by_reevaluation"

TERMINAL_OUTCOME = OUTCOME_NON_SELECTABLE_TERMINAL

TERMINAL_RESPONSE = (
    "I can't autonomously determine which finding you mean, and "
    "clarifying hasn't provided new information. You can select a "
    "finding from the Findings panel yourself, or rephrase with more "
    "specific details."
)

CLARIFY_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS clarify_ledger (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id      TEXT NOT NULL,
    request_fingerprint   TEXT NOT NULL,
    candidate_set_hash    TEXT NOT NULL,
    metadata_status_hash  TEXT NOT NULL,
    outcome               TEXT NOT NULL,
    reason_class          TEXT,
    detail                TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
)
"""

CLARIFY_LEDGER_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_clarify_ledger_inv_key "
    "ON clarify_ledger(investigation_id, request_fingerprint, "
    "candidate_set_hash, metadata_status_hash)"
)


def ensure_clarification_state_schema(conn) -> None:
    """Create the clarify ledger if absent. Idempotent; called from
    TaskStoreV2._init_schema() (same additive migration convention)."""
    conn.execute(CLARIFY_LEDGER_DDL)
    conn.execute(CLARIFY_LEDGER_INDEX_DDL)
    conn.commit()


def request_fingerprint(request_text: str) -> str:
    return hashlib.sha256(
        (request_text or "").strip().encode("utf-8")).hexdigest()


def candidate_set_hash(candidate_ids) -> str:
    ids = sorted(set(candidate_ids or []))
    return hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()


def metadata_status_hash(status_by_id: dict) -> str:
    pairs = sorted((cid, (status_by_id or {}).get(cid, "missing"))
                   for cid in (status_by_id or {}))
    return hashlib.sha256(
        "|".join(f"{cid}:{status}" for cid, status in
                 pairs).encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ClarificationSignal:
    """What the runtime should do at the AMBIGUOUS/clarification
    boundary, plus the key components for the user-safe control state."""
    terminal: bool                       # True -> bounded terminal turn
    ledger_id: int | None
    repeated: bool                       # same key seen before
    request_fingerprint: str | None = None
    candidate_set_hash: str | None = None
    metadata_status_hash: str | None = None


class ClarificationLedger:
    """Loop-boundary protection over the real store. Borrow-only: uses
    TaskStoreV2.metadata_write() for connection/lock; owns no
    connection."""

    def __init__(self, store):
        self._store = store

    def _write(self):
        return self._store.metadata_write()

    def status_by_id(self, candidate_ids) -> dict:
        """Governed statuses for the retained candidate set (missing
        record -> 'missing'), via the WI-1 catalog."""
        from app.candidate_metadata import TaskStoreCatalogProvider
        provider = TaskStoreCatalogProvider(self._store)
        out = {}
        for cid in (candidate_ids or []):
            record = provider.get_candidate(cid)
            out[cid] = record.status if record is not None else "missing"
        return out

    def record_clarification(self, investigation_id: str, request_text: str,
                             candidate_ids, reason_class: str | None = None) \
            -> ClarificationSignal:
        """Record a clarification boundary. Hashes are computed from the
        GOVERNED catalog statuses (not from the request-side objects). If
        an identical open key already exists, flip it to the bounded
        terminal outcome (no third clarification is created)."""
        fp = request_fingerprint(request_text)
        csh = candidate_set_hash(candidate_ids)
        status_by_id = self.status_by_id(candidate_ids)
        msh = metadata_status_hash(status_by_id)
        now = _now_iso()
        with self._write() as conn:
            row = conn.execute(
                """SELECT id, outcome FROM clarify_ledger
                   WHERE investigation_id = ?
                     AND request_fingerprint = ?
                     AND candidate_set_hash = ?
                     AND metadata_status_hash = ?
                   ORDER BY id DESC LIMIT 1""",
                (investigation_id, fp, csh, msh)).fetchone()
            if row is not None:
                conn.execute(
                    """UPDATE clarify_ledger SET outcome = ?,
                           updated_at = ? WHERE id = ?""",
                    (TERMINAL_OUTCOME, now, row["id"]))
                emit_event("clarification.terminal",
                           investigation_id=investigation_id,
                           request_fingerprint=fp,
                           candidate_set_hash=csh,
                           metadata_status_hash=msh,
                           reason_class="non_selectable_terminal",
                           outcome=TERMINAL_OUTCOME)
                return ClarificationSignal(
                    terminal=True, ledger_id=row["id"], repeated=True,
                    request_fingerprint=fp, candidate_set_hash=csh,
                    metadata_status_hash=msh)
            cur = conn.execute(
                """INSERT INTO clarify_ledger
                   (investigation_id, request_fingerprint,
                    candidate_set_hash, metadata_status_hash, outcome,
                    reason_class, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (investigation_id, fp, csh, msh, OUTCOME_CLARIFIED,
                 reason_class, now, now))
            emit_event("clarification.opened",
                       investigation_id=investigation_id,
                       request_fingerprint=fp,
                       candidate_set_hash=csh,
                       metadata_status_hash=msh,
                       reason_class=reason_class,
                       outcome=OUTCOME_CLARIFIED)
            return ClarificationSignal(
                terminal=False, ledger_id=cur.lastrowid, repeated=False,
                request_fingerprint=fp, candidate_set_hash=csh,
                metadata_status_hash=msh)

    def close_by_designation(self, investigation_id: str,
                             candidate_id: str) -> int | None:
        """Close the latest open clarification for this investigation as
        resolved by explicit user designation. Returns the ledger id or
        None when nothing was open. NEVER touches candidate metadata."""
        now = _now_iso()
        with self._write() as conn:
            row = conn.execute(
                """SELECT id FROM clarify_ledger
                   WHERE investigation_id = ? AND outcome = ?
                   ORDER BY id DESC LIMIT 1""",
                (investigation_id, OUTCOME_CLARIFIED)).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE clarify_ledger SET outcome = ?,
                       reason_class = ?, updated_at = ? WHERE id = ?""",
                (OUTCOME_RESOLVED_BY_DESIGNATION,
                 f"designated:{candidate_id}", now, row["id"]))
            emit_event("clarification.resolved",
                       investigation_id=investigation_id,
                       candidate_id=candidate_id,
                       reason_class=OUTCOME_RESOLVED_BY_DESIGNATION,
                       outcome=OUTCOME_RESOLVED_BY_DESIGNATION)
            emit_event("user.designated_candidate",
                       investigation_id=investigation_id,
                       candidate_id=candidate_id,
                       reason_class="explicit_focus",
                       outcome="focus_transition")
            return row["id"]

    def close_by_reevaluation(self, investigation_id: str) -> int | None:
        """Close the latest open clarification: a new request fingerprint
        re-entered evaluation (meaningful user clarification)."""
        now = _now_iso()
        with self._write() as conn:
            row = conn.execute(
                """SELECT id FROM clarify_ledger
                   WHERE investigation_id = ? AND outcome = ?
                   ORDER BY id DESC LIMIT 1""",
                (investigation_id, OUTCOME_CLARIFIED)).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE clarify_ledger SET outcome = ?, updated_at = ?
                   WHERE id = ?""",
                (OUTCOME_RESUMED_BY_REEVALUATION, now, row["id"]))
            emit_event("clarification.resolved",
                       investigation_id=investigation_id,
                       reason_class=OUTCOME_RESUMED_BY_REEVALUATION,
                       outcome=OUTCOME_RESUMED_BY_REEVALUATION)
            return row["id"]

    def entries(self, investigation_id: str) -> list[dict]:
        with self._write() as conn:
            rows = conn.execute(
                """SELECT id, request_fingerprint, candidate_set_hash,
                          metadata_status_hash, outcome, reason_class,
                          created_at, updated_at
                   FROM clarify_ledger WHERE investigation_id = ?
                   ORDER BY id""",
                (investigation_id,)).fetchall()
        return [dict(r) for r in rows]
