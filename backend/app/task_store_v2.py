"""V2 Task Store — persistence for Week 1 investigations/tasks.

Deliberately separate from the legacy TaskStore (app/store.py): the legacy
schema encodes superseded-era fields (execution_mode, acceptance_status)
whose semantics conflict with the V2 contract, and it stores nested
Step/Artifact rows. V2 persists serializable TaskV2 documents keyed by task
id — minimal schema (task_id, investigation_id, status, timestamps) plus a
full JSON document column, so records stay reloadable/inspectable as the
model evolves without repeated migrations.

Supported operations: create / update / get / list_by_investigation.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import Investigation, TaskV2

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "investigation_agent_v2.db"


class TaskStoreV2:
    """SQLite-backed store for TaskV2 audit containers and Investigation
    sessions (the smallest compatible extension of the V2 persistence
    boundary — same DB file, no second persistence mechanism)."""

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = str(db_path or _DEFAULT_DB)
        if db_path is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    # --- connection ----------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks_v2 (
                    task_id          TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL,
                    case_id          TEXT NOT NULL,
                    selected_skill   TEXT,
                    status           TEXT NOT NULL,
                    started_at       TEXT,
                    completed_at     TEXT,
                    document         TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_v2_inv "
                "ON tasks_v2(investigation_id)"
            )
            # Investigation sessions (same store, same DB file — no second
            # persistence mechanism; document column keeps the model reloadable)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS investigations (
                    investigation_id TEXT PRIMARY KEY,
                    case_id          TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    created_at       TEXT,
                    updated_at       TEXT,
                    document         TEXT NOT NULL
                )
                """
            )
            # Session state per investigation: evolved InvestigationContext,
            # per-task plans, executed ToolCall records. Document-per-record
            # (same pattern as tasks_v2/investigations) so the shape can grow
            # without migrations. This replaces the Week 1 process-local
            # _SESSIONS map — true persistence across refresh AND restart.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS investigation_sessions (
                    investigation_id TEXT PRIMARY KEY,
                    updated_at       TEXT,
                    document         TEXT NOT NULL
                )
                """
            )
            conn.commit()

    # --- CRUD ----------------------------------------------------------------

    @staticmethod
    def _row_values(task: TaskV2) -> tuple:
        case_id = ""
        # investigation_id currently carries CASE:<case_id> until full
        # Investigation sessions are created by the task layer.
        if task.investigation_id.startswith("CASE:"):
            case_id = task.investigation_id[len("CASE:"):]
        return (
            task.task_id,
            task.investigation_id,
            case_id,
            task.selected_skill,
            task.status.value,
            task.started_at,
            task.completed_at,
            task.model_dump_json(),
        )

    def create(self, task: TaskV2) -> TaskV2:
        with self._lock:
            conn = self._connection()
            conn.execute(
                """INSERT OR REPLACE INTO tasks_v2
                   (task_id, investigation_id, case_id, selected_skill, status,
                    started_at, completed_at, document)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                self._row_values(task),
            )
            conn.commit()
        return task

    def update(self, task: TaskV2) -> TaskV2:
        """Full-document upsert; same semantics as create for simplicity."""
        return self.create(task)

    def get(self, task_id: str) -> TaskV2 | None:
        with self._lock:
            row = self._connection().execute(
                "SELECT document FROM tasks_v2 WHERE task_id = ?", (task_id,),
            ).fetchone()
        if row is None:
            return None
        return TaskV2.model_validate_json(row["document"])

    def list_by_investigation(self, investigation_id: str) -> list[TaskV2]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT document FROM tasks_v2 WHERE investigation_id = ? "
                "ORDER BY started_at ASC, task_id ASC",
                (investigation_id,),
            ).fetchall()
        return [TaskV2.model_validate_json(r["document"]) for r in rows]

    # --- Investigation session persistence ---------------------------------

    def save_investigation(self, investigation: Investigation) -> Investigation:
        with self._lock:
            conn = self._connection()
            conn.execute(
                """INSERT OR REPLACE INTO investigations
                   (investigation_id, case_id, status, created_at, updated_at,
                    document)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    investigation.investigation_id,
                    investigation.case_id,
                    investigation.status.value,
                    investigation.created_at,
                    investigation.updated_at,
                    investigation.model_dump_json(),
                ),
            )
            conn.commit()
        return investigation

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        with self._lock:
            row = self._connection().execute(
                "SELECT document FROM investigations WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if row is None:
            return None
        return Investigation.model_validate_json(row["document"])

    # --- Investigation session state ----------------------------------------
    #
    # The session document is the minimal read-model needed to reconstruct a
    # reopened workspace: the evolved InvestigationContext, per-task plans,
    # and the executed ToolCall records (normalized results included) plus
    # produced artifacts. Tasks/Artifacts also live in their own tables; the
    # session document references them by id and stores the rendering payloads
    # so a reload never needs to re-execute anything.

    def save_session(self, investigation_id: str, document: dict) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(
                """INSERT OR REPLACE INTO investigation_sessions
                   (investigation_id, updated_at, document)
                   VALUES (?, ?, ?)""",
                (
                    investigation_id,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(document),
                ),
            )
            conn.commit()

    def get_session(self, investigation_id: str) -> dict | None:
        with self._lock:
            row = self._connection().execute(
                "SELECT document FROM investigation_sessions "
                "WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["document"])

    def delete_session(self, investigation_id: str) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(
                "DELETE FROM investigation_sessions WHERE investigation_id = ?",
                (investigation_id,),
            )
            conn.execute(
                "DELETE FROM tasks_v2 WHERE investigation_id = ?",
                (investigation_id,),
            )
            conn.execute(
                "DELETE FROM investigations WHERE investigation_id = ?",
                (investigation_id,),
            )
            conn.commit()

    def list_investigations(self) -> list[Investigation]:
        """All investigations, deterministic by updated_at descending (the
        history list)."""
        with self._lock:
            rows = self._connection().execute(
                "SELECT document FROM investigations "
                "ORDER BY COALESCE(updated_at, created_at) DESC, "
                "investigation_id ASC",
            ).fetchall()
        return [Investigation.model_validate_json(r["document"]) for r in rows]
