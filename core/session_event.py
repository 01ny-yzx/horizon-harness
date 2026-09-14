"""Durable, aggregate-local event publication for Horizon sessions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import time
from typing import Any, Callable
import uuid

from core.database import DatabaseService, get_database_service
from core.unicode_safety import sanitize_unicode


@dataclass(frozen=True)
class DurableSessionEvent:
    """One persisted event in a Session aggregate."""

    id: str
    aggregate_id: str
    seq: int
    type: str
    data: dict[str, Any]
    time_created: int


SessionProjector = Callable[[sqlite3.Connection, DurableSessionEvent], None]
SessionCommitHook = Callable[[sqlite3.Connection, DurableSessionEvent], None]


class SessionEventPublisher:
    """Publish Session events and their projections in one SQLite transaction."""

    def __init__(
        self,
        database_path: str | None = None,
        *,
        database: DatabaseService | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database.ensure_ready()

    def latest_sequence(self, aggregate_id: str) -> int:
        """Return the latest durable sequence for one aggregate, or -1."""

        with self.database.read_transaction() as connection:
            row = connection.execute(
                "SELECT seq FROM event_sequence WHERE aggregate_id = ?",
                (str(aggregate_id),),
            ).fetchone()
        return int(row["seq"]) if row is not None else -1

    def publish(
        self,
        *,
        aggregate_id: str,
        event_type: str,
        data: dict[str, Any],
        event_id: str | None = None,
        time_created: int | None = None,
        commit: SessionCommitHook | None = None,
    ) -> DurableSessionEvent:
        """Atomically project and append one event with its next aggregate seq."""

        aggregate = str(aggregate_id or "").strip()
        kind = str(event_type or "").strip()
        if not aggregate:
            raise ValueError("aggregate_id is required")
        if not kind:
            raise ValueError("event_type is required")
        projector = _projector_for_event(kind)
        safe_data = sanitize_unicode(dict(data or {}))
        created = int(time_created if time_created is not None else time.time() * 1000)
        identifier = str(event_id or f"evt_{uuid.uuid4().hex}")

        with self.database.write_transaction() as connection:
            row = connection.execute(
                "SELECT seq FROM event_sequence WHERE aggregate_id = ?",
                (aggregate,),
            ).fetchone()
            latest = int(row["seq"]) if row is not None else -1
            event = DurableSessionEvent(
                id=identifier,
                aggregate_id=aggregate,
                seq=latest + 1,
                type=kind,
                data=safe_data,
                time_created=created,
            )
            projector(connection, event)
            if commit is not None:
                commit(connection, event)
            connection.execute(
                """
                INSERT INTO event_sequence (aggregate_id, seq)
                VALUES (?, ?)
                ON CONFLICT(aggregate_id) DO UPDATE SET seq = excluded.seq
                """,
                (event.aggregate_id, event.seq),
            )
            connection.execute(
                """
                INSERT INTO event (id, aggregate_id, seq, type, data, time_created)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.aggregate_id,
                    event.seq,
                    event.type,
                    json.dumps(
                        event.data,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    event.time_created,
                ),
            )
        return event

    def read_aggregate(
        self,
        aggregate_id: str,
        *,
        after: int = -1,
        limit: int | None = None,
    ) -> list[DurableSessionEvent]:
        """Read one Session aggregate in durable sequence order."""

        if limit is not None and int(limit) < 1:
            raise ValueError("limit must be positive")
        limit_clause = " LIMIT ?" if limit is not None else ""
        parameters: tuple[Any, ...] = (str(aggregate_id), int(after))
        if limit is not None:
            parameters = (*parameters, int(limit))
        with self.database.read_transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, aggregate_id, seq, type, data, time_created
                FROM event
                WHERE aggregate_id = ? AND seq > ?
                ORDER BY seq ASC
                """
                + limit_clause,
                parameters,
            ).fetchall()
        return [
            DurableSessionEvent(
                id=str(row["id"]),
                aggregate_id=str(row["aggregate_id"]),
                seq=int(row["seq"]),
                type=str(row["type"]),
                data=dict(json.loads(str(row["data"]))),
                time_created=int(row["time_created"]),
            )
            for row in rows
        ]


class UnregisteredSessionEventError(LookupError):
    """Raised when a durable Session event has no fixed projector binding."""


def _projector_for_event(event_type: str) -> SessionProjector:
    from core.session_projector import SESSION_EVENT_PROJECTORS

    projector = SESSION_EVENT_PROJECTORS.get(event_type)
    if projector is None:
        raise UnregisteredSessionEventError(
            f"No Session projector registered for event type: {event_type}"
        )
    return projector


__all__ = [
    "DurableSessionEvent",
    "SessionEventPublisher",
    "SessionCommitHook",
    "SessionProjector",
    "UnregisteredSessionEventError",
]
