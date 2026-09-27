"""Candidate Semantic Metadata storage (WP-M2-1 WI-1).

Persistence layer for the Candidate Semantic Metadata Contract
(docs/baselines/2026-09-24-candidate-semantic-metadata-contract-v1.md)
and the implementation baseline
(docs/plans/2026-09-24-candidate-semantic-metadata-implementation-plan-v1.md,
rev. 3).

WI-1 scope ONLY: schema + repository primitives + the CatalogProvider
boundary. Deliberately absent from this module: coverage reconcile,
semantic normalization, support evaluation, admissibility, clarify
ledger, telemetry — later work items.

Safety invariants encoded here:

- candidate metadata status is a closed enum. INCOMPLETE / UNVERIFIED
  mean "support is UNKNOWN" downstream; NOTHING in this module maps
  them to FALSE, and no later consumer may either (Metadata Contract
  §13).
- ``suggested_core_concepts`` is PROPOSAL-ONLY storage. No method in
  this module derives status, core_concepts, or eligibility from it —
  there is deliberately no implicit path from a suggestion to a
  COMPLETE/eligible candidate (Metadata Contract §25).

The store (TaskStoreV2) owns the SQLite connection lifecycle and its
lock; this module's repository only borrows the connection through
``TaskStoreV2.metadata_write()`` and never creates connections itself.
"""

import hashlib
import json
from dataclasses import dataclass, field, replace

from app.telemetry import emit_event
from typing import Any, Protocol

# --- status enums (closed; Metadata Contract §11) ---------------------------

CANDIDATE_METADATA_STATUSES = (
    "INCOMPLETE",    # auto-registered / not yet authored: support UNKNOWN
    "UNVERIFIED",    # authored, pending independent verification: UNKNOWN
    "COMPLETE",      # authored + verified: eligible for Safe-Select evaluation
    "DEPRECATED",    # retired: excluded from new candidate sets
)

CONCEPT_STATUSES = ("ACTIVE", "DEPRECATED")


def validate_candidate_status(status: str) -> str:
    if status not in CANDIDATE_METADATA_STATUSES:
        raise ValueError(
            f"invalid candidate metadata status {status!r}; "
            f"allowed: {CANDIDATE_METADATA_STATUSES}")
    return status


def validate_concept_status(status: str) -> str:
    if status not in CONCEPT_STATUSES:
        raise ValueError(
            f"invalid concept status {status!r}; "
            f"allowed: {CONCEPT_STATUSES}")
    return status


# --- records -----------------------------------------------------------------

@dataclass
class CandidateMetadataRecord:
    """One governed metadata record for one candidate (finding).

    JSON-typed fields (core/distinguishing/mechanism/suggested concept
    lists, potential confusions, provenance) are serialized by this
    module; callers always see plain Python values.
    """

    candidate_id: str
    candidate_type: str
    case_scope: str | None = None
    title_snapshot: str | None = None
    description_snapshot: str | None = None
    content_hash: str | None = None
    core_concepts: list[str] = field(default_factory=list)
    distinguishing_concepts: list[str] = field(default_factory=list)
    potential_confusions: list[str] = field(default_factory=list)
    mechanism_concepts: list[str] = field(default_factory=list)
    suggested_core_concepts: list[str] = field(default_factory=list)
    status: str = "INCOMPLETE"
    metadata_version: int = 0
    owner: str | None = None
    authored_by: str | None = None
    verified_by: str | None = None
    last_verified_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConceptRecord:
    concept_id: str
    definition: str
    status: str = "ACTIVE"
    version: int = 1
    created_at: str | None = None
    created_by: str | None = None


def _dump_list(values: list[str]) -> str:
    return json.dumps(list(values or []), ensure_ascii=False)


def _load_list(raw: Any) -> list[str]:
    return list(json.loads(raw)) if raw else []


def _dump_obj(obj: dict[str, Any]) -> str | None:
    return json.dumps(obj, ensure_ascii=False) if obj else None


def _load_obj(raw: Any) -> dict[str, Any]:
    return dict(json.loads(raw)) if raw else {}


# --- schema (owner: this module; created via TaskStoreV2._init_schema) -------

CANDIDATE_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS candidate_metadata (
    candidate_id            TEXT PRIMARY KEY,
    candidate_type          TEXT NOT NULL,
    case_scope              TEXT,
    title_snapshot          TEXT,
    description_snapshot    TEXT,
    content_hash            TEXT,
    core_concepts           TEXT NOT NULL DEFAULT '[]',
    distinguishing_concepts TEXT NOT NULL DEFAULT '[]',
    potential_confusions    TEXT NOT NULL DEFAULT '[]',
    mechanism_concepts      TEXT NOT NULL DEFAULT '[]',
    suggested_core_concepts TEXT NOT NULL DEFAULT '[]',
    status                  TEXT NOT NULL
                            CHECK (status IN ('INCOMPLETE', 'UNVERIFIED',
                                              'COMPLETE', 'DEPRECATED')),
    metadata_version        INTEGER NOT NULL DEFAULT 0,
    owner                   TEXT,
    authored_by             TEXT,
    verified_by             TEXT,
    last_verified_at        TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    provenance              TEXT
)
"""

SEMANTIC_CONCEPTS_DDL = """
CREATE TABLE IF NOT EXISTS semantic_concepts (
    concept_id  TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'DEPRECATED')),
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    created_by  TEXT
)
"""


def ensure_candidate_metadata_schema(conn) -> None:
    """Create the WI-1 tables if absent. Idempotent; called from
    TaskStoreV2._init_schema() so every store open re-asserts the schema
    (the project's additive migration convention)."""
    conn.execute(CANDIDATE_METADATA_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_metadata_status "
        "ON candidate_metadata(status)")
    conn.execute(SEMANTIC_CONCEPTS_DDL)
    conn.commit()


# --- row mapping ---------------------------------------------------------------

_CANDIDATE_COLUMNS = (
    "candidate_id, candidate_type, case_scope, title_snapshot, "
    "description_snapshot, content_hash, core_concepts, "
    "distinguishing_concepts, potential_confusions, mechanism_concepts, "
    "suggested_core_concepts, status, metadata_version, owner, "
    "authored_by, verified_by, last_verified_at, created_at, updated_at, "
    "provenance"
)


def _record_values(r: CandidateMetadataRecord) -> tuple:
    validate_candidate_status(r.status)
    return (
        r.candidate_id,
        r.candidate_type,
        r.case_scope,
        r.title_snapshot,
        r.description_snapshot,
        r.content_hash,
        _dump_list(r.core_concepts),
        _dump_list(r.distinguishing_concepts),
        _dump_list(r.potential_confusions),
        _dump_list(r.mechanism_concepts),
        _dump_list(r.suggested_core_concepts),
        r.status,
        r.metadata_version,
        r.owner,
        r.authored_by,
        r.verified_by,
        r.last_verified_at,
        r.created_at,
        r.updated_at,
        _dump_obj(r.provenance),
    )


def _record_from_row(row) -> CandidateMetadataRecord:
    return CandidateMetadataRecord(
        candidate_id=row["candidate_id"],
        candidate_type=row["candidate_type"],
        case_scope=row["case_scope"],
        title_snapshot=row["title_snapshot"],
        description_snapshot=row["description_snapshot"],
        content_hash=row["content_hash"],
        core_concepts=_load_list(row["core_concepts"]),
        distinguishing_concepts=_load_list(
            row["distinguishing_concepts"]),
        potential_confusions=_load_list(row["potential_confusions"]),
        mechanism_concepts=_load_list(row["mechanism_concepts"]),
        suggested_core_concepts=_load_list(row["suggested_core_concepts"]),
        status=row["status"],
        metadata_version=row["metadata_version"],
        owner=row["owner"],
        authored_by=row["authored_by"],
        verified_by=row["verified_by"],
        last_verified_at=row["last_verified_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        provenance=_load_obj(row["provenance"]),
    )


# --- repository primitives (borrow the store connection; no owns) --------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def upsert_candidate(conn, record: CandidateMetadataRecord) \
        -> CandidateMetadataRecord:
    """Insert-or-replace one metadata record. Status is validated here;
    transition-legality policy lives in later WIs — this primitive is
    deliberately permissive storage. Missing timestamps are defaulted
    (provided values are preserved for round-trips)."""
    validate_candidate_status(record.status)
    now = _now_iso()
    if not record.created_at:
        record.created_at = now
    if not record.updated_at:
        record.updated_at = now
    conn.execute(
        f"""INSERT OR REPLACE INTO candidate_metadata
            ({_CANDIDATE_COLUMNS})
            VALUES ({','.join('?' * 20)})""",
        _record_values(record),
    )
    return record


def get_candidate(conn, candidate_id: str) -> CandidateMetadataRecord | None:
    row = conn.execute(
        f"SELECT {_CANDIDATE_COLUMNS} FROM candidate_metadata "
        "WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return _record_from_row(row) if row is not None else None


def list_candidates(conn, status: str | None = None) \
        -> list[CandidateMetadataRecord]:
    if status is not None:
        validate_candidate_status(status)
        rows = conn.execute(
            f"SELECT {_CANDIDATE_COLUMNS} FROM candidate_metadata "
            "WHERE status = ? ORDER BY candidate_id",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_CANDIDATE_COLUMNS} FROM candidate_metadata "
            "ORDER BY candidate_id",
        ).fetchall()
    return [_record_from_row(r) for r in rows]


def update_status(conn, candidate_id: str, status: str, *,
                  verified_by: str | None = None,
                  last_verified_at: str | None = None,
                  metadata_version: int | None = None,
                  provenance_update: dict[str, Any] | None = None) \
        -> CandidateMetadataRecord:
    """Status-only primitive (validation/eligibility transitions belong
    to later WIs). Updates provenance fields when provided; never
    touches concepts. Raises ValueError on invalid status and
    LookupError on unknown candidate."""
    validate_candidate_status(status)
    previous = get_candidate(conn, candidate_id)
    cur = conn.execute(
        """UPDATE candidate_metadata
           SET status = ?,
               verified_by = COALESCE(?, verified_by),
               last_verified_at = COALESCE(?, last_verified_at),
               metadata_version = COALESCE(?, metadata_version),
               provenance = COALESCE(?, provenance)
           WHERE candidate_id = ?""",
        (status, verified_by, last_verified_at, metadata_version,
         _dump_obj(provenance_update) if provenance_update else None,
         candidate_id),
    )
    if cur.rowcount == 0:
        raise LookupError(f"unknown candidate: {candidate_id}")
    emit_event("candidate.metadata.status_changed",
               candidate_id=candidate_id,
               reason_class=status,
               summary=(f"{previous.status if previous else 'unknown'} "
                        f"-> {status}"))
    return get_candidate(conn, candidate_id)


def upsert_concept(conn, concept: ConceptRecord) -> ConceptRecord:
    validate_concept_status(concept.status)
    if not concept.created_at:
        concept.created_at = _now_iso()
    conn.execute(
        """INSERT OR REPLACE INTO semantic_concepts
           (concept_id, definition, status, version, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (concept.concept_id, concept.definition, concept.status,
         concept.version, concept.created_at, concept.created_by),
    )
    return concept


def get_concept(conn, concept_id: str) -> ConceptRecord | None:
    row = conn.execute(
        "SELECT concept_id, definition, status, version, created_at, "
        "created_by FROM semantic_concepts WHERE concept_id = ?",
        (concept_id,),
    ).fetchone()
    if row is None:
        return None
    return ConceptRecord(
        concept_id=row["concept_id"], definition=row["definition"],
        status=row["status"], version=row["version"],
        created_at=row["created_at"], created_by=row["created_by"])


def list_concepts(conn, status: str | None = None) -> list[ConceptRecord]:
    if status is not None:
        validate_concept_status(status)
        rows = conn.execute(
            "SELECT concept_id, definition, status, version, created_at, "
            "created_by FROM semantic_concepts WHERE status = ? "
            "ORDER BY concept_id", (status,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT concept_id, definition, status, version, created_at, "
            "created_by FROM semantic_concepts ORDER BY concept_id",
        ).fetchall()
    return [ConceptRecord(
        concept_id=r["concept_id"], definition=r["definition"],
        status=r["status"], version=r["version"],
        created_at=r["created_at"], created_by=r["created_by"])
        for r in rows]


# --- CatalogProvider boundary (contract §15) -----------------------------------

class CatalogProvider(Protocol):
    """Storage boundary the later WIs (coverage check, support
    evaluation, admissibility gate) program against. Implementations
    must keep UNKNOWN discipline: nothing here translates
    INCOMPLETE/UNVERIFIED into a negative support fact."""

    def upsert_candidate(self, record: CandidateMetadataRecord) \
        -> CandidateMetadataRecord: ...
    def get_candidate(self, candidate_id: str) \
        -> CandidateMetadataRecord | None: ...
    def list_candidates(self, status: str | None = None) \
        -> list[CandidateMetadataRecord]: ...
    def update_status(self, candidate_id: str, status: str, **kw) \
        -> CandidateMetadataRecord: ...
    def upsert_concept(self, concept: ConceptRecord) -> ConceptRecord: ...
    def get_concept(self, concept_id: str) -> ConceptRecord | None: ...
    def list_concepts(self, status: str | None = None) \
        -> list[ConceptRecord]: ...


class TaskStoreCatalogProvider:
    """CatalogProvider over the TaskStoreV2 store instance (same SQLite
    connection/lock — no second database, no second persistence
    mechanism)."""

    def __init__(self, store):
        self._store = store

    def upsert_candidate(self, record):
        with self._store.metadata_write() as conn:
            return upsert_candidate(conn, record)

    def get_candidate(self, candidate_id):
        with self._store.metadata_write() as conn:
            return get_candidate(conn, candidate_id)

    def list_candidates(self, status=None):
        with self._store.metadata_write() as conn:
            return list_candidates(conn, status)

    def update_status(self, candidate_id, status, **kw):
        with self._store.metadata_write() as conn:
            return update_status(conn, candidate_id, status, **kw)

    def upsert_concept(self, concept):
        with self._store.metadata_write() as conn:
            return upsert_concept(conn, concept)

    def get_concept(self, concept_id):
        with self._store.metadata_write() as conn:
            return get_concept(conn, concept_id)

    def list_concepts(self, status=None):
        with self._store.metadata_write() as conn:
            return list_concepts(conn, status)


# --- coverage reconcile (WP-M2-1 WI-2) -----------------------------------------
# Deterministic, data-driven, generic over candidates. NO LLM, NO
# candidate-specific branches, NO Safe-Select logic. Governance data is
# never overwritten by source snapshots: reconcile only provisions new
# candidates, refreshes RP mirror fields on real content change, and
# invalidates governed metadata (COMPLETE/UNVERIFIED -> UNVERIFIED).

def compute_content_hash(title: str | None, description: str | None) -> str:
    return hashlib.sha256(
        f"{title or ''}\n{description or ''}".encode("utf-8")).hexdigest()


@dataclass
class CoverageReport:
    candidates_seen: int = 0
    provisioned: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    invalidated: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    gone_known: list[str] = field(default_factory=list)
    reinstated: list[str] = field(default_factory=list)
    skipped_deprecated: list[str] = field(default_factory=list)
    skipped_empty_fetch: bool = False

    def summary(self) -> str:
        return (f"seen={self.candidates_seen} "
                f"provisioned={self.provisioned} "
                f"unchanged={self.unchanged} "
                f"invalidated={self.invalidated} "
                f"refreshed={self.refreshed} "
                f"gone={self.gone} gone_known={self.gone_known} "
                f"reinstated={self.reinstated} "
                f"deprecated={self.skipped_deprecated} "
                f"empty_fetch={self.skipped_empty_fetch}")


def reconcile_coverage(provider, findings, *, now: str | None = None) \
        -> CoverageReport:
    """Reconcile the live RP candidate universe against the metadata
    catalog.

    - new candidate  -> auto-provision INCOMPLETE (never concepts, never
      COMPLETE, never eligibility);
    - content change -> governed candidates invalidated to UNVERIFIED
      (version++, invalidation provenance; authored metadata preserved);
      INCOMPLETE candidates get snapshot refresh only (no version bump);
    - gone           -> detected once, marked in provenance; DEPRECATED
      requires explicit governed action and is never automatic;
    - empty fetch    -> skipped: a failed provider call must not be read
      as every-candidate-gone.
    """
    now = now or _now_iso()
    report = CoverageReport(candidates_seen=len(findings))
    if not findings:
        report.skipped_empty_fetch = True
        return report
    catalog = {r.candidate_id: r for r in provider.list_candidates()}
    present: set[str] = set()
    for f in findings:
        cid = f.finding_id
        present.add(cid)
        content_hash = compute_content_hash(f.title, f.summary)
        existing = catalog.get(cid)
        if existing is not None and existing.status == "DEPRECATED":
            report.skipped_deprecated.append(cid)
            continue
        if existing is None:
            provider.upsert_candidate(CandidateMetadataRecord(
                candidate_id=cid,
                candidate_type="finding",
                case_scope=getattr(f, "case_id", None),
                title_snapshot=f.title,
                description_snapshot=f.summary,
                content_hash=content_hash,
                status="INCOMPLETE",
                metadata_version=0,
                provenance={"auto_provisioned": True,
                            "provisioned_at": now}))
            report.provisioned.append(cid)
            emit_event("candidate.metadata.provisioned",
                       candidate_id=cid, candidate_type="finding",
                       metadata_version=0, status="INCOMPLETE",
                       content_hash=content_hash,
                       reason_class="auto_provisioned")
            continue
        if existing.content_hash == content_hash:
            if (existing.provenance or {}).get("candidate_gone"):
                # candidate returned after a gone detection
                provenance = {**(existing.provenance or {}),
                              "candidate_gone_reinstated_at": now}
                provider.upsert_candidate(
                    replace(existing, provenance=provenance))
                report.reinstated.append(cid)
            else:
                report.unchanged.append(cid)
            continue
        provenance = dict(existing.provenance or {})
        reinstated = None
        if provenance.get("candidate_gone"):
            provenance["candidate_gone_reinstated_at"] = now
            reinstated = cid
        if existing.status == "INCOMPLETE":
            # scope-A bookkeeping: refresh the RP mirror; governed fields
            # untouched; still INCOMPLETE; no version bump.
            provider.upsert_candidate(replace(
                existing,
                title_snapshot=f.title,
                description_snapshot=f.summary,
                content_hash=content_hash,
                provenance={**provenance,
                            "last_content_change": now}))
            report.refreshed.append(cid)
        else:
            # governed metadata (COMPLETE / UNVERIFIED): invalidate.
            # Authored concepts and provenance are PRESERVED.
            provider.upsert_candidate(replace(
                existing,
                status="UNVERIFIED",
                title_snapshot=f.title,
                description_snapshot=f.summary,
                content_hash=content_hash,
                metadata_version=existing.metadata_version + 1,
                provenance={**provenance,
                            "invalidation": {"at": now,
                                             "old_hash": existing.content_hash,
                                             "new_hash": content_hash}}))
            report.invalidated.append(cid)
            emit_event("candidate.metadata.invalidated",
                       candidate_id=cid,
                       content_hash=content_hash,
                       reason_class="semantic_source_changed",
                       summary=(f"status {existing.status} -> UNVERIFIED "
                                f"(semantic source changed)"))
        if reinstated:
            report.reinstated.append(reinstated)
    for cid, existing in catalog.items():
        if cid in present or existing.status == "DEPRECATED":
            continue
        provenance = dict(existing.provenance or {})
        if (existing.provenance or {}).get("candidate_gone", {}).get(
                "detected_at"):
            report.gone_known.append(cid)
            continue
        provider.upsert_candidate(replace(
            existing,
            provenance={**provenance,
                        "candidate_gone": {"detected_at": now}}))
        report.gone.append(cid)
    emit_event("candidate.metadata.coverage",
               coverage_counts={
                   "candidates_seen": report.candidates_seen,
                   "provisioned": len(report.provisioned),
                   "unchanged": len(report.unchanged),
                   "invalidated": len(report.invalidated),
                   "refreshed": len(report.refreshed),
                   "gone": len(report.gone),
                   "gone_known": len(report.gone_known),
                   "reinstated": len(report.reinstated),
                   "skipped_deprecated": len(report.skipped_deprecated),
                   "skipped_empty_fetch": report.skipped_empty_fetch,
               })
    return report
