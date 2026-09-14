"""Persistent Session identity and creation service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any, Protocol
import uuid

from core.database import DatabaseService, get_database_service
from core.session_event import SessionEventPublisher
from core.session_projector import (
    SESSION_CREATED,
    SessionAlreadyProjected,
)
from core.unicode_safety import sanitize_unicode

if TYPE_CHECKING:
    from core.session_event import DurableSessionEvent
    from core.session_input import AdmittedSessionInput
    from core.session_message import SessionMessage


SESSION_ID_RE = re.compile(r"^ses_[A-Za-z0-9_-]{1,128}$")


class SessionIdentityConflict(ValueError):
    """Raised when an existing Session ID is reused with different identity."""


class SessionWorkspaceMismatchError(ValueError):
    """Raised when a Session-bound runtime requests a different workspace."""


class SessionExecutionPort(Protocol):
    def active(self) -> set[str]: ...
    def resume(self, session_id: str) -> None: ...
    def wake(self, session_id: str) -> None: ...
    def interrupt(self, session_id: str) -> None: ...


@dataclass(frozen=True)
class SessionInfo:
    id: str
    user_id: str
    project_id: str
    workspace_id: str
    directory: str
    title: str
    time_created: int
    time_updated: int

    def to_event_data(self) -> dict[str, Any]:
        return dict(sanitize_unicode(asdict(self)))


@dataclass(frozen=True)
class SessionHistoryPage:
    events: tuple[DurableSessionEvent, ...]
    has_more: bool


class SessionService:
    """Create durable Session identities through SessionCreated events."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        database: DatabaseService | None = None,
        execution: SessionExecutionPort | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database.ensure_ready()
        self.events = SessionEventPublisher(database=self.database)
        self.execution = execution
        from core.session_store import SessionStore
        from core.session_input import SessionInputService

        self.store = SessionStore(database=self.database)
        self.inputs = SessionInputService(database=self.database)

    def get(self, session_id: str) -> SessionInfo:
        """Reopen one existing durable Session without creating it."""

        return self.store.get(session_id)

    def list(
        self,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> list[SessionInfo]:
        """List existing durable Sessions, optionally scoped to a workspace."""

        return self.store.list(user_id=user_id, project_id=project_id)

    def context(self, session_id: str) -> list[SessionMessage]:
        """Read canonical durable Session messages without executing work."""

        self.store.get(session_id)
        return self.store.context(session_id)

    def history(
        self,
        session_id: str,
        *,
        after: int | None = None,
        limit: int = 50,
    ) -> SessionHistoryPage:
        """Read one finite page of durable Session events in ascending sequence."""

        self.store.get(session_id)
        requested_limit = int(limit)
        if requested_limit < 1 or requested_limit > 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self.events.read_aggregate(
            session_id,
            after=-1 if after is None else int(after),
            limit=requested_limit + 1,
        )
        return SessionHistoryPage(
            events=tuple(rows[:requested_limit]),
            has_more=len(rows) > requested_limit,
        )

    def active(self) -> set[str]:
        """Return only process-local active Session execution identities."""

        return self._execution().active()

    def resume(self, session_id: str) -> None:
        """Explicitly continue one existing Session from durable history."""

        self.store.get(session_id)
        self._execution().resume(session_id)

    def interrupt(self, session_id: str) -> None:
        """Interrupt only the current process-local execution for a Session."""

        self.store.get(session_id)
        self._execution().interrupt(session_id)

    def create(
        self,
        *,
        user_id: str,
        project_id: str,
        workspace_id: str,
        directory: Path | str,
        title: str | None = None,
        session_id: str | None = None,
    ) -> SessionInfo:
        identifier = _session_id(session_id)
        resolved_directory = str(Path(directory).expanduser().resolve())
        requested_identity = {
            "user_id": _required_text(user_id, "user_id"),
            "project_id": _required_text(project_id, "project_id"),
            "workspace_id": _required_text(workspace_id, "workspace_id"),
            "directory": resolved_directory,
        }
        existing = _stored_session(self.store, identifier)
        if existing is not None:
            return _adopt_existing(existing, requested_identity)

        now = int(time.time() * 1000)
        session = SessionInfo(
            id=identifier,
            **requested_identity,
            title=_clean_title(title, now),
            time_created=now,
            time_updated=now,
        )
        try:
            self.events.publish(
                aggregate_id=session.id,
                event_type=SESSION_CREATED,
                data=session.to_event_data(),
                time_created=now,
            )
        except SessionAlreadyProjected:
            recorded = _stored_session(self.store, identifier)
            if recorded is None:
                raise
            return _adopt_existing(recorded, requested_identity)
        return self.store.get(identifier)

    def prompt(
        self,
        session_id: str,
        prompt: dict[str, Any] | str,
        *,
        delivery: str = "steer",
        message_id: str | None = None,
        resume: bool = True,
    ) -> AdmittedSessionInput:
        """Durably admit a prompt, optionally wake execution, and return it."""

        from core.session_message import create_session_message_id

        self.store.get(session_id)
        admitted = self.inputs.admit(
            session_id,
            prompt,
            delivery=delivery,
            message_id=message_id or create_session_message_id(),
        )
        if resume:
            self._execution().wake(admitted.session_id)
        return admitted

    def _execution(self):
        execution = self.execution
        if execution is None:
            from core.session_execution import get_session_execution

            execution = get_session_execution()
        return execution


def create_session_id() -> str:
    return f"ses_{uuid.uuid4().hex}"


def _session_id(value: str | None) -> str:
    identifier = str(value or create_session_id()).strip()
    if not SESSION_ID_RE.fullmatch(identifier):
        raise ValueError("session_id must use the ses_ prefix and contain only safe ID characters")
    return identifier


def _required_text(value: Any, name: str) -> str:
    text = str(sanitize_unicode(value or "")).strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _clean_title(value: str | None, created: int) -> str:
    title = str(sanitize_unicode(value or "")).strip()
    if title:
        return title[:500]
    stamp = datetime.fromtimestamp(created / 1000, tz=UTC).isoformat()
    return f"New session - {stamp}"


def _adopt_existing(
    session: SessionInfo,
    requested: dict[str, str],
) -> SessionInfo:
    conflicts = [
        name
        for name, value in requested.items()
        if str(getattr(session, name)) != str(value)
    ]
    if conflicts:
        raise SessionIdentityConflict(
            f"Existing Session identity conflicts on: {', '.join(conflicts)}"
        )
    return session


def _stored_session(store: Any, session_id: str) -> SessionInfo | None:
    from core.session_store import SessionNotFoundError

    try:
        return store.get(session_id)
    except SessionNotFoundError:
        return None


__all__ = [
    "SESSION_ID_RE",
    "SessionIdentityConflict",
    "SessionHistoryPage",
    "SessionInfo",
    "SessionService",
    "SessionWorkspaceMismatchError",
    "create_session_id",
]
