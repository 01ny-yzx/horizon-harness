"""Projection handlers for durable Horizon Session events."""

from __future__ import annotations

import sqlite3
from types import MappingProxyType
from typing import Any

from core.session_event import DurableSessionEvent
from core.session_message_updater import (
    COMPACTION_ENDED,
    PROMPTED,
    SESSION_MESSAGE_EVENT_TYPES,
    project_session_message_event,
)
from core.session_input import (
    PROMPT_ADMITTED,
    project_prompt_admitted_event,
    project_session_input_prompted,
)


SESSION_CREATED = "SessionCreated"
COMPACTION_STARTED = "session.next.compaction.started"


class SessionAlreadyProjected(RuntimeError):
    """Raised when concurrent creation has already established the Session."""


def project_session_event(
    connection: sqlite3.Connection,
    event: DurableSessionEvent,
) -> None:
    """Apply the supported Session event to the read-side projection."""

    if event.type != SESSION_CREATED:
        raise ValueError(f"Unsupported Session event type: {event.type}")
    if str(event.data.get("id") or "") != event.aggregate_id:
        raise ValueError("SessionCreated aggregate_id must equal the Session id")
    _project_session_created(connection, event.data)


def _project_session_created(
    connection: sqlite3.Connection,
    data: dict[str, Any],
) -> None:
    required = (
        "id",
        "user_id",
        "project_id",
        "workspace_id",
        "directory",
        "title",
        "time_created",
        "time_updated",
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"SessionCreated payload missing fields: {', '.join(missing)}")
    existing = connection.execute(
        "SELECT id FROM session WHERE id = ?",
        (str(data["id"]),),
    ).fetchone()
    if existing is not None:
        raise SessionAlreadyProjected(str(data["id"]))
    connection.execute(
        """
        INSERT INTO session (
            id, user_id, project_id, workspace_id, directory, title,
            time_created, time_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(data["id"]),
            str(data["user_id"]),
            str(data["project_id"]),
            str(data["workspace_id"]),
            str(data["directory"]),
            str(data["title"]),
            int(data["time_created"]),
            int(data["time_updated"]),
        ),
    )


def project_prompted_event(
    connection: sqlite3.Connection,
    event: DurableSessionEvent,
) -> None:
    """Promote durable input before creating its canonical User message."""

    project_session_input_prompted(connection, event)
    project_session_message_event(connection, event)


def project_compaction_started_event(
    _connection: sqlite3.Connection,
    event: DurableSessionEvent,
) -> None:
    """Record an attempt without advancing canonical history."""

    if str(event.data.get("session_id") or "") != event.aggregate_id:
        raise ValueError("CompactionStarted aggregate_id must equal session_id")
    if str(event.data.get("reason") or "") not in {"auto", "manual"}:
        raise ValueError("CompactionStarted reason must be auto or manual")


SESSION_EVENT_PROJECTORS = MappingProxyType(
    {
        SESSION_CREATED: project_session_event,
        **{
            event_type: project_session_message_event
            for event_type in SESSION_MESSAGE_EVENT_TYPES
            if event_type != PROMPTED
        },
        PROMPT_ADMITTED: project_prompt_admitted_event,
        PROMPTED: project_prompted_event,
        COMPACTION_STARTED: project_compaction_started_event,
    }
)


__all__ = [
    "SESSION_CREATED",
    "COMPACTION_STARTED",
    "SESSION_EVENT_PROJECTORS",
    "SessionAlreadyProjected",
    "project_prompted_event",
    "project_session_event",
]
