"""Central SQLite authority for Horizon persistent data."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from typing import Iterator

from core.path_grounding import build_path_context


def get_default_database_path() -> Path:
    """Return the single production authority for Horizon's database path."""

    return (build_path_context().user_data_root / "horizon.db").resolve()


DEFAULT_DATABASE_PATH = get_default_database_path()


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS event_sequence (
        aggregate_id TEXT PRIMARY KEY,
        seq INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event (
        id TEXT PRIMARY KEY,
        aggregate_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        type TEXT NOT NULL,
        data TEXT NOT NULL,
        time_created INTEGER NOT NULL,
        UNIQUE (aggregate_id, seq),
        FOREIGN KEY (aggregate_id) REFERENCES event_sequence (aggregate_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS event_aggregate_seq_idx ON event (aggregate_id, seq)",
    "CREATE INDEX IF NOT EXISTS event_aggregate_type_seq_idx ON event (aggregate_id, type, seq)",
    """
    CREATE TABLE IF NOT EXISTS session (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        directory TEXT NOT NULL,
        title TEXT NOT NULL,
        time_created INTEGER NOT NULL,
        time_updated INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS session_user_time_idx ON session (user_id, time_created, id)",
    "CREATE INDEX IF NOT EXISTS session_project_time_idx ON session (user_id, project_id, time_created, id)",
    """
    CREATE TABLE IF NOT EXISTS session_context_epoch (
        session_id TEXT PRIMARY KEY,
        baseline TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        baseline_seq INTEGER NOT NULL,
        FOREIGN KEY (session_id) REFERENCES session (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_input (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        prompt TEXT NOT NULL,
        delivery TEXT NOT NULL,
        admitted_seq INTEGER NOT NULL,
        promoted_seq INTEGER,
        time_created INTEGER NOT NULL,
        FOREIGN KEY (session_id) REFERENCES session (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS session_input_session_pending_delivery_seq_idx
    ON session_input (session_id, promoted_seq, delivery, admitted_seq)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS session_input_session_admitted_seq_idx
    ON session_input (session_id, admitted_seq)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS session_input_session_promoted_seq_idx
    ON session_input (session_id, promoted_seq)
    """,
    """
    CREATE TABLE IF NOT EXISTS session_message (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        type TEXT NOT NULL,
        seq INTEGER NOT NULL,
        time_created INTEGER NOT NULL,
        time_updated INTEGER NOT NULL,
        data TEXT NOT NULL,
        UNIQUE (session_id, seq),
        FOREIGN KEY (session_id) REFERENCES session (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS session_message_session_type_seq_idx ON session_message (session_id, type, seq)",
    "CREATE INDEX IF NOT EXISTS session_message_session_time_created_id_idx ON session_message (session_id, time_created, id)",
    """
    CREATE TABLE IF NOT EXISTS memory_user_preference (
        user_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        source TEXT NOT NULL,
        time_created INTEGER NOT NULL,
        time_updated INTEGER NOT NULL,
        PRIMARY KEY (user_id, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_stable_fact (
        id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        description TEXT NOT NULL,
        source TEXT NOT NULL,
        source_reference TEXT NOT NULL,
        time_created INTEGER NOT NULL,
        time_updated INTEGER NOT NULL,
        UNIQUE (user_id, content)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_project_summary (
        id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        project_path TEXT NOT NULL,
        summary TEXT NOT NULL,
        tech_stack TEXT,
        status TEXT NOT NULL,
        source TEXT NOT NULL,
        source_reference TEXT NOT NULL,
        time_created INTEGER NOT NULL,
        time_updated INTEGER NOT NULL,
        UNIQUE (user_id, project_id, project_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_project_instruction (
        id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        content TEXT NOT NULL,
        source TEXT NOT NULL,
        time_created INTEGER NOT NULL,
        time_updated INTEGER NOT NULL,
        UNIQUE (user_id, project_id, content)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_task_history (
        id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        task_id TEXT,
        task_type TEXT NOT NULL,
        goal TEXT NOT NULL,
        result TEXT NOT NULL,
        summary TEXT NOT NULL,
        modified_files TEXT,
        hidden_from_memory_prompt INTEGER NOT NULL,
        source TEXT NOT NULL,
        time_created INTEGER NOT NULL,
        time_updated INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS memory_stable_fact_user_idx ON memory_stable_fact (user_id, time_created, id)",
    "CREATE INDEX IF NOT EXISTS memory_project_summary_scope_idx ON memory_project_summary (user_id, project_id, time_created, id)",
    "CREATE INDEX IF NOT EXISTS memory_project_instruction_scope_idx ON memory_project_instruction (user_id, project_id, time_created, id)",
    "CREATE INDEX IF NOT EXISTS memory_task_history_scope_time_idx ON memory_task_history (user_id, project_id, time_created, id)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS memory_task_history_scope_task_id_unique
    ON memory_task_history (user_id, project_id, task_id)
    WHERE task_id IS NOT NULL
    """,
)


class DatabaseService:
    """Open configured SQLite connections and own the current schema."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.path = (
            Path(database_path).expanduser().resolve()
            if database_path is not None
            else get_default_database_path()
        )
        self._ready = False
        self._initialization_lock = threading.Lock()

    @property
    def is_ready(self) -> bool:
        """Whether the current schema completed initialization successfully."""

        return self._ready

    def initialize(self) -> None:
        """Initialize the schema once; failed attempts remain retryable."""

        with self._initialization_lock:
            if self._ready:
                return
            try:
                with self.write_transaction() as connection:
                    for statement in SCHEMA_STATEMENTS:
                        connection.execute(statement)
                connection = self.connect()
                try:
                    connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                finally:
                    connection.close()
            except Exception:
                self._ready = False
                raise
            self._ready = True

    def ensure_ready(self) -> None:
        """Make the service ready without repeating successful initialization."""

        if not self._ready:
            self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Return one independently configured connection."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA cache_size = -64000")
            connection.execute("PRAGMA foreign_keys = ON")
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a consistent read transaction."""

        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read_only_transaction(self) -> Iterator[sqlite3.Connection]:
        """Read an existing database without creating or modifying it."""

        connection = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one serialized write transaction with rollback on failure."""

        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


_DATABASE_SERVICES: dict[Path, DatabaseService] = {}
_DATABASE_SERVICES_LOCK = threading.Lock()


def get_database_service(database_path: Path | str | None = None) -> DatabaseService:
    """Return the process-local database authority for one resolved path."""

    path = (
        Path(database_path).expanduser().resolve()
        if database_path is not None
        else get_default_database_path()
    )
    with _DATABASE_SERVICES_LOCK:
        service = _DATABASE_SERVICES.get(path)
        if service is None:
            service = DatabaseService(path)
            _DATABASE_SERVICES[path] = service
        return service


__all__ = [
    "DEFAULT_DATABASE_PATH",
    "DatabaseService",
    "SCHEMA_STATEMENTS",
    "get_database_service",
    "get_default_database_path",
]
