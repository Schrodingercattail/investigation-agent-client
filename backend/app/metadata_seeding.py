"""Candidate Semantic Metadata seeding + verification utility (WP-M2-1 WI-3).

Data-driven bootstrap for the Candidate Semantic Metadata Contract:
- loads a governed seed document (JSON; concepts + candidates + evidence),
- provisions the Concept Registry and candidate records (create-if-absent,
  idempotent; never overwrites governed state),
- performs the deterministic UNVERIFIED -> COMPLETE verification
  transition for candidates whose authored concepts conform to the
  registry and whose evidence provenance is present.

Design constraints (rev. 3 Implementation Baseline, WI-3):
- deterministic; NO LLM anywhere in this module;
- not triggered by chat; not part of the Safe-Select runtime;
- data-driven: candidate specifics live in the seed document, so future
  candidates never require code changes here;
- verification requires evidence provenance; candidates seeded as
  INCOMPLETE (insufficient evidence) are refused.

CLI:
    python -m app.metadata_seeding seed   --db PATH [--seed-file PATH]
    python -m app.metadata_seeding verify --db PATH --candidate ID --verified-by NAME
"""

import argparse
import json
from dataclasses import dataclass, field

from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
    compute_content_hash,
    validate_candidate_status,
)
from app.task_store_v2 import TaskStoreV2

DEFAULT_SEED_FILE = "seed/candidate_metadata_seed.json"

REQUIRED_CONCEPT_FIELDS = ("concept_id", "definition", "status")
REQUIRED_CANDIDATE_FIELDS = (
    "candidate_id", "candidate_type", "core_concepts",
    "distinguishing_concepts", "potential_confusions", "target_status",
)


class SeedConformanceError(Exception):
    """Raised when a seed document or a verification request fails
    registry/evidence conformance. `errors` lists every violation."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass
class SeedSummary:
    concepts_seeded: list[str] = field(default_factory=list)
    concepts_already_present: list[str] = field(default_factory=list)
    candidates_seeded: dict[str, str] = field(default_factory=dict)
    candidates_already_present: list[str] = field(default_factory=list)
    inventory_count: int = 0

    @property
    def coverage_complete(self) -> bool:
        """Scope A (lifecycle coverage): every seeded candidate is
        represented. Eligibility (scope B) is intentionally separate."""
        return (self.inventory_count ==
                len(self.candidates_seeded) +
                len(self.candidates_already_present))


# --- seed document validation -------------------------------------------------

def validate_seed_document(doc: dict) -> list[str]:
    errors: list[str] = []
    for key in ("concept_registry", "candidates"):
        if not isinstance(doc.get(key), list):
            errors.append(f"missing/invalid seed key: {key}")
    for c in doc.get("concept_registry", []):
        for f in REQUIRED_CONCEPT_FIELDS:
            if not c.get(f):
                errors.append(f"concept {c.get('concept_id', '?')!r}: "
                              f"missing required field {f!r}")
    for c in doc.get("candidates", []):
        for f in REQUIRED_CANDIDATE_FIELDS:
            if f not in c:
                errors.append(f"candidate {c.get('candidate_id', '?')!r}: "
                              f"missing required field {f!r}")
        ts = c.get("target_status")
        try:
            validate_candidate_status(ts)
        except ValueError as e:
            errors.append(f"candidate {c.get('candidate_id', '?')!r}: {e}")
        if ts == "UNVERIFIED" and not c.get("evidence", {}).get("sources"):
            errors.append(f"candidate {c.get('candidate_id', '?')!r}: "
                          "UNVERIFIED target requires evidence.sources")
        if ts == "INCOMPLETE" and not c.get("insufficiency_reason"):
            errors.append(f"candidate {c.get('candidate_id', '?')!r}: "
                          "INCOMPLETE target requires insufficiency_reason")
    return errors


# --- seeding (create-if-absent; never overwrites governed state) ---------------

def seed_from_document(store: TaskStoreV2, doc: dict) -> SeedSummary:
    """Provision the Concept Registry and candidate records.

    Create-if-absent: existing records are NEVER overwritten (governed
    state is protected); re-running the seed is idempotent.
    """
    errors = validate_seed_document(doc)
    if errors:
        raise SeedConformanceError(errors)
    provider = TaskStoreCatalogProvider(store)
    summary = SeedSummary(inventory_count=len(doc["candidates"]))

    registry_ids = set()
    for c in doc["concept_registry"]:
        cid = c["concept_id"]
        if provider.get_concept(cid) is not None:
            summary.concepts_already_present.append(cid)
            registry_ids.add(cid)
            continue
        provider.upsert_concept(ConceptRecord(
            concept_id=cid, definition=c["definition"],
            status=c.get("status", "ACTIVE"),
            version=c.get("version", 1), created_by=c.get("created_by")))
        registry_ids.add(cid)
        summary.concepts_seeded.append(cid)

    for c in doc["candidates"]:
        cid = c["candidate_id"]
        authored = bool(c.get("core_concepts"))
        existing = provider.get_candidate(cid)
        if existing is not None:
            # Re-author semantics (Metadata Contract §16 Step 2 -> Step 3):
            # an empty INCOMPLETE PLACEHOLDER is not governed semantic
            # state yet — a seed entry carrying authored concepts
            # completes the onboarding authoring step. Placeholders are
            # either Coverage-Check auto-provisions or earlier-seed
            # INCOMPLETE declarations (provenance.seed_version present);
            # a seed-sourced placeholder is only completed by a NEWER
            # seed version. Anything else (authored records,
            # INCOMPLETE-with-concepts, DEPRECATED, manual records) is
            # protected governed state and is NEVER overwritten.
            prov = existing.provenance or {}
            from_seed = prov.get("seed_version") is not None
            newer_seed = (not from_seed) or (
                (doc.get("seed_version") or 0) > prov["seed_version"])
            placeholder = (
                existing.status == "INCOMPLETE"
                and not existing.core_concepts
                and (bool(prov.get("auto_provisioned")) or from_seed)
                and newer_seed
            )
            if not (authored and placeholder):
                summary.candidates_already_present.append(cid)
                continue
            concepts = (c.get("core_concepts", []) +
                        c.get("distinguishing_concepts", []) +
                        c.get("mechanism_concepts", []))
            unknown = sorted(set(concepts) - registry_ids)
            if unknown:
                raise SeedConformanceError(
                    [f"candidate {cid!r}: concepts not in registry: "
                     f"{unknown}"])
            ev = c.get("evidence", {})
            record = CandidateMetadataRecord(
                candidate_id=cid,
                candidate_type=existing.candidate_type,
                case_scope=existing.case_scope or doc.get("source_case"),
                title_snapshot=existing.title_snapshot,
                description_snapshot=existing.description_snapshot,
                content_hash=existing.content_hash,
                core_concepts=c.get("core_concepts", []),
                distinguishing_concepts=c.get("distinguishing_concepts", []),
                potential_confusions=(existing.potential_confusions
                                      or c.get("potential_confusions", [])),
                mechanism_concepts=c.get("mechanism_concepts", []),
                suggested_core_concepts=[],
                status=c["target_status"],
                metadata_version=1,
                owner=c.get("owner"),
                authored_by=c.get("authored_by", "seed"),
                created_at=existing.created_at,
                provenance={
                    "auto_provisioned": True,       # history preserved
                    "seed_version": doc.get("seed_version"),
                    "evidence_sources": ev.get("sources", []),
                    "evidence_rationale": ev.get("rationale"),
                    "reauthored_from": "auto_provisioned_placeholder",
                },
            )
            provider.upsert_candidate(record)
            summary.candidates_seeded[cid] = record.status
            continue
        concepts = (c.get("core_concepts", []) +
                    c.get("distinguishing_concepts", []) +
                    c.get("mechanism_concepts", []))
        unknown = sorted(set(concepts) - registry_ids)
        if unknown:
            raise SeedConformanceError(
                [f"candidate {cid!r}: concepts not in registry: {unknown}"])
        ev = c.get("evidence", {})
        record = CandidateMetadataRecord(
            candidate_id=cid,
            candidate_type=c["candidate_type"],
            case_scope=doc.get("source_case"),
            title_snapshot=c.get("title_snapshot"),
            description_snapshot=c.get("description_snapshot"),
            content_hash=c.get("content_hash") or compute_content_hash(
                c.get("title_snapshot"),
                c.get("description_snapshot")),
            core_concepts=c.get("core_concepts", []),
            distinguishing_concepts=c.get("distinguishing_concepts", []),
            potential_confusions=c.get("potential_confusions", []),
            mechanism_concepts=c.get("mechanism_concepts", []),
            suggested_core_concepts=[],
            status=c["target_status"],
            metadata_version=1 if c["target_status"] != "INCOMPLETE" else 0,
            owner=c.get("owner"),
            authored_by=c.get("authored_by", "seed-wi3"),
            provenance={
                "seed_version": doc.get("seed_version"),
                "evidence_sources": ev.get("sources", []),
                "evidence_rationale": ev.get("rationale"),
                "insufficiency_reason": c.get("insufficiency_reason"),
            },
        )
        provider.upsert_candidate(record)
        summary.candidates_seeded[cid] = record.status
    return summary


# --- deterministic verification transition (UNVERIFIED -> COMPLETE) ------------

def verify_candidate(store: TaskStoreV2, candidate_id: str,
                     verified_by: str) -> CandidateMetadataRecord:
    """Deterministic governance transition. Refuses (SeedConformanceError)
    unless: the record exists, is UNVERIFIED, has non-empty core and
    distinguishing concepts, every referenced concept exists in the
    registry, and evidence provenance is present. No LLM involvement."""
    provider = TaskStoreCatalogProvider(store)
    record = provider.get_candidate(candidate_id)
    if record is None:
        raise SeedConformanceError(
            [f"candidate {candidate_id!r} is not registered"])
    errors: list[str] = []
    if record.status != "UNVERIFIED":
        errors.append(f"candidate {candidate_id!r} status is "
                      f"{record.status!r}, expected UNVERIFIED")
    if not record.core_concepts:
        errors.append("no core concepts authored")
    if not record.distinguishing_concepts:
        errors.append("no distinguishing concepts authored")
    registry = {c.concept_id for c in provider.list_concepts()
                if c.status == "ACTIVE"}
    for concept in (record.core_concepts +
                    record.distinguishing_concepts +
                    record.mechanism_concepts):
        if concept not in registry:
            errors.append(f"concept {concept!r} is not in the registry")
    sources = (record.provenance or {}).get("evidence_sources") or []
    if not sources:
        errors.append("evidence provenance missing (evidence_sources)")
    if not verified_by:
        errors.append("verified_by is required")
    if errors:
        raise SeedConformanceError(errors)
    record.status = "COMPLETE"
    record.verified_by = verified_by
    record.last_verified_at = _now_iso()
    # metadata_version tracks SEMANTIC changes only (contract §11); a
    # verification transition is lifecycle state, not a semantic change.
    record.provenance = {**(record.provenance or {}),
                         "verification": {"verified_by": verified_by,
                                          "at": record.last_verified_at}}
    provider.upsert_candidate(record)
    return provider.get_candidate(candidate_id)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# --- CLI (dev-facing governance utility) ---------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        "app.metadata_seeding", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    seed_p = sub.add_parser("seed", help="provision registry + candidates")
    seed_p.add_argument("--db", required=True)
    seed_p.add_argument("--seed-file", default=DEFAULT_SEED_FILE)
    verify_p = sub.add_parser("verify", help="UNVERIFIED -> COMPLETE")
    verify_p.add_argument("--db", required=True)
    verify_p.add_argument("--candidate", required=True)
    verify_p.add_argument("--verified-by", required=True)
    args = parser.parse_args(argv)
    store = TaskStoreV2(args.db)
    if args.command == "seed":
        with open(args.seed_file, encoding="utf-8") as f:
            doc = json.load(f)
        summary = seed_from_document(store, doc)
        print(json.dumps({
            "concepts_seeded": summary.concepts_seeded,
            "concepts_already_present": summary.concepts_already_present,
            "candidates_seeded": summary.candidates_seeded,
            "candidates_already_present": summary.candidates_already_present,
            "inventory_count": summary.inventory_count,
            "lifecycle_coverage_complete": summary.coverage_complete,
        }, indent=2))
        return 0
    try:
        record = verify_candidate(store, args.candidate, args.verified_by)
    except SeedConformanceError as e:
        print(json.dumps({"verified": False, "errors": e.errors},
                         indent=2))
        return 1
    print(json.dumps({"verified": True, "candidate": record.candidate_id,
                      "status": record.status,
                      "verified_by": record.verified_by}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
