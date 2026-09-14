"""Read-side access to persisted Horizon Session projections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.database import DatabaseService, get_database_service
from core.session import SessionInfo
from core.session_history import SessionHistory
from core.session_message import SessionMessage


class SessionNotFoundError(LookupError):
    """Raised when a Session projection does not exist."""


class SessionStore:
    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        database: DatabaseService | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database.ensure_ready()

    def get(self, session_id: str) -> SessionInfo:
        """Return one persisted Session or raise an explicit not-found error."""

        with self.database.read_transaction() as connection:
            row = connection.execute(
                """
                SELECT id, user_id, project_id, workspace_id, directory, title,
                       time_created, time_updated
                FROM session
                WHERE id = ?
                """,
                (str(session_id),),
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(str(session_id))
        return _session_from_row(row)

    def list(
        self,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> list[SessionInfo]:
        """List Session projections in deterministic creation order."""

        if project_id is not None and user_id is None:
            raise ValueError("project-scoped Session listing requires user_id")
        conditions: list[str] = []
        parameters: list[str] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            parameters.append(str(user_id))
        if project_id is not None:
            conditions.append("project_id = ?")
            parameters.append(str(project_id))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.database.read_transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, project_id, workspace_id, directory, title,
                       time_created, time_updated
                FROM session
                """
                + where
                + " ORDER BY time_created ASC, id ASC",
                tuple(parameters),
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def messages(self, session_id: str) -> list[SessionMessage]:
        """Return the public canonical Session history."""

        return self.context(session_id)

    def context(self, session_id: str) -> list[SessionMessage]:
        """Return the complete durable chronology for one Session."""

        self.get(session_id)
        return SessionHistory(database=self.database).load(session_id)


def _session_from_row(row: Any) -> SessionInfo:
    return SessionInfo(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        project_id=str(row["project_id"]),
        workspace_id=str(row["workspace_id"]),
        directory=str(row["directory"]),
        title=str(row["title"]),
        time_created=int(row["time_created"]),
        time_updated=int(row["time_updated"]),
    )


__all__ = ["SessionNotFoundError", "SessionStore"]
