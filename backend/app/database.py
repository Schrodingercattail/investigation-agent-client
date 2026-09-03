"""
SQLite database management for Investigation Agent.

Provides schema initialization and connection management.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

# Lock for thread-safe database operations
_db_lock = threading.Lock()
_db_connection: Optional[sqlite3.Connection] = None


def get_database_path() -> Path:
    """Get the path to the SQLite database file."""
    backend_dir = Path(__file__).parent.parent
    data_dir = backend_dir / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "investigation_agent.db"


def get_connection() -> sqlite3.Connection:
    """Get a thread-local database connection, creating it if necessary."""
    global _db_connection

    with _db_lock:
        if _db_connection is None:
            _db_connection = sqlite3.connect(
                get_database_path(),
                check_same_thread=False,
            )
            _db_connection.row_factory = sqlite3.Row
            initialize_schema(_db_connection)
        return _db_connection


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Initialize the database schema."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_intent TEXT NOT NULL,
            case_id TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            acceptance_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS steps (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error TEXT,
            execution_order INTEGER,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id TEXT PRIMARY KEY,
            step_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args TEXT,
            status TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            latency_ms INTEGER,
            output_summary TEXT,
            output TEXT,
            error TEXT,
            FOREIGN KEY (step_id) REFERENCES steps (id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            data TEXT,
            created_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )
    """)

    # Create indexes for better query performance
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_steps_task_id
        ON steps (task_id)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tool_calls_step_id
        ON tool_calls (step_id)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_artifacts_task_id
        ON artifacts (task_id)
    """)

    conn.commit()


def close_connection() -> None:
    """Close the database connection."""
    global _db_connection

    with _db_lock:
        if _db_connection is not None:
            _db_connection.close()
            _db_connection = None
