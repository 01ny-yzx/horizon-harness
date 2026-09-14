"""Durable admission and promotion lifecycle for Session user input."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from core.database import DatabaseService, get_database_service
from core.session_event import DurableSessionEvent, SessionEventPublisher
from core.session_message import create_session_message_id
from core.session_message_updater import PROMPTED
from core.unicode_safety import sanitize_unicode


PROMPT_ADMITTED = "session.next.prompt.admitted"
SESSION_INPUT_DELIVERIES = frozenset({"steer", "queue"})


class SessionInputLifecycleConflict(RuntimeError):
    """Raised when one message ID cannot follow the requested input lifecycle."""


class SessionPromptConflictError(ValueError):
    """Raised when a caller reuses one message ID with different semantics."""


PromptConflictError = SessionPromptConflictError


@dataclass(frozen=True)
class SessionInput:
    id: str
    session_id: str
    prompt: dict[str, str]
    delivery: str
    admitted_seq: int
    promoted_seq: int | None
    time_created: int


AdmittedSessionInput = SessionInput


class SessionInputService:
    """Record durable input first, then promote it through Prompted events."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        database: DatabaseService | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database.ensure_ready()
        self.events = SessionEventPublisher(database=self.database)

    def find(self, message_id: str) -> SessionInput | None:
        with self.database.read_transaction() as connection:
            row = _find_row(connection, message_id)
        return None if row is None else _from_row(row)

    def admit(
        self,
        session_id: str,
        prompt: dict[str, Any] | str,
        delivery: str = "steer",
        message_id: str | None = None,
    ) -> AdmittedSessionInput:
        identifier = _message_id(message_id)
        expected_prompt = canonical_prompt(prompt)
        expected_delivery = canonical_delivery(delivery)
        expected = {
            "session_id": _required_text(session_id, "session_id"),
            "prompt": expected_prompt,
            "delivery": expected_delivery,
        }
        existing = self.find(identifier)
        if existing is not None:
            return _equivalent_or_conflict(existing, expected)

        created = int(time.time() * 1000)
        try:
            self.events.publish(
                aggregate_id=expected["session_id"],
                event_type=PROMPT_ADMITTED,
                data={
                    "session_id": expected["session_id"],
                    "message_id": identifier,
                    "prompt": expected_prompt,
                    "delivery": expected_delivery,
                    "timestamp": created,
                },
                time_created=created,
            )
        except Exception as exc:
            recorded = self.find(identifier)
            if recorded is not None:
                return _equivalent_or_conflict(recorded, expected)
            if isinstance(exc, SessionInputLifecycleConflict):
                raise SessionPromptConflictError(
                    f"PromptConflict for message_id: {identifier}"
                ) from exc
            raise
        recorded = self.find(identifier)
        if recorded is None:
            raise RuntimeError("PromptAdmitted committed without a session_input projection")
        return _equivalent_or_conflict(recorded, expected)

    def has_pending(self, session_id: str, delivery: str) -> bool:
        selected_delivery = canonical_delivery(delivery)
        with self.database.read_transaction() as connection:
            row = connection.execute(
                """
                SELECT id FROM session_input
                WHERE session_id = ? AND promoted_seq IS NULL AND delivery = ?
                LIMIT 1
                """,
                (str(session_id), selected_delivery),
            ).fetchone()
        return row is not None

    def pending(self, session_id: str, delivery: str) -> list[SessionInput]:
        selected_delivery = canonical_delivery(delivery)
        with self.database.read_transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, prompt, delivery, admitted_seq,
                       promoted_seq, time_created
                FROM session_input
                WHERE session_id = ? AND promoted_seq IS NULL AND delivery = ?
                ORDER BY admitted_seq ASC
                """,
                (str(session_id), selected_delivery),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def promote_steers(self, session_id: str, cutoff: int) -> int:
        with self.database.read_transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, prompt, delivery, admitted_seq,
                       promoted_seq, time_created
                FROM session_input
                WHERE session_id = ?
                  AND promoted_seq IS NULL
                  AND delivery = 'steer'
                  AND admitted_seq <= ?
                ORDER BY admitted_seq ASC
                """,
                (str(session_id), int(cutoff)),
            ).fetchall()
        inputs = [_from_row(row) for row in rows]
        for admitted in inputs:
            self._publish_prompted(admitted)
        return len(inputs)

    def promote_next_queued(self, session_id: str) -> bool:
        with self.database.read_transaction() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, prompt, delivery, admitted_seq,
                       promoted_seq, time_created
                FROM session_input
                WHERE session_id = ?
                  AND promoted_seq IS NULL
                  AND delivery = 'queue'
                ORDER BY admitted_seq ASC
                LIMIT 1
                """,
                (str(session_id),),
            ).fetchone()
        if row is None:
            return False
        admitted = _from_row(row)
        self._publish_prompted(admitted)
        return True

    def _publish_prompted(self, admitted: SessionInput) -> None:
        try:
            self.events.publish(
                aggregate_id=admitted.session_id,
                event_type=PROMPTED,
                data={
                    "session_id": admitted.session_id,
                    "message_id": admitted.id,
                    "prompt": admitted.prompt,
                    "delivery": admitted.delivery,
                    "timestamp": admitted.time_created,
                },
            )
        except SessionInputLifecycleConflict as exc:
            stored = self.find(admitted.id)
            if (
                stored is not None
                and stored.promoted_seq is not None
                and projection_equivalent(stored, admitted)
            ):
                return
            raise exc


def canonical_prompt(prompt: dict[str, Any] | str) -> dict[str, str]:
    if isinstance(prompt, str):
        value: dict[str, Any] = {"text": prompt}
    elif isinstance(prompt, dict):
        value = dict(prompt)
    else:
        raise ValueError("prompt must be text or an object containing text")
    unsupported = sorted(str(key) for key in value if key != "text")
    if unsupported:
        raise ValueError(f"Unsupported prompt fields: {', '.join(unsupported)}")
    return {"text": str(sanitize_unicode(value.get("text") or ""))}


def canonical_delivery(delivery: str | None) -> str:
    value = str(delivery or "steer").strip()
    if value not in SESSION_INPUT_DELIVERIES:
        raise ValueError("delivery must be 'steer' or 'queue'")
    return value


def prompt_from_event(event: DurableSessionEvent) -> dict[str, str]:
    value = event.data.get("prompt")
    if value is None:
        value = {"text": event.data.get("text") or ""}
    return canonical_prompt(value)


def delivery_from_event(event: DurableSessionEvent) -> str:
    return canonical_delivery(event.data.get("delivery"))


def timestamp_from_event(event: DurableSessionEvent) -> int:
    timestamp = event.data.get("timestamp")
    return int(event.time_created if timestamp is None else timestamp)


def equivalent(
    admitted: SessionInput,
    *,
    session_id: str,
    prompt: dict[str, Any] | str,
    delivery: str,
) -> bool:
    return (
        admitted.session_id == str(session_id)
        and admitted.delivery == canonical_delivery(delivery)
        and _prompt_json(admitted.prompt) == _prompt_json(canonical_prompt(prompt))
    )


def projection_equivalent(actual: SessionInput, expected: SessionInput) -> bool:
    return (
        equivalent(
            actual,
            session_id=expected.session_id,
            prompt=expected.prompt,
            delivery=expected.delivery,
        )
        and actual.time_created == expected.time_created
    )


def project_prompt_admitted_event(
    connection: sqlite3.Connection,
    event: DurableSessionEvent,
) -> None:
    session_id, message_id = _event_identity(event)
    occupied = connection.execute(
        "SELECT id FROM session_message WHERE id = ?",
        (message_id,),
    ).fetchone()
    if occupied is not None:
        raise SessionInputLifecycleConflict(message_id)
    try:
        connection.execute(
            """
            INSERT INTO session_input (
                id, session_id, prompt, delivery, admitted_seq,
                promoted_seq, time_created
            ) VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                message_id,
                session_id,
                _prompt_json(prompt_from_event(event)),
                delivery_from_event(event),
                event.seq,
                timestamp_from_event(event),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise SessionInputLifecycleConflict(message_id) from exc


def project_session_input_prompted(
    connection: sqlite3.Connection,
    event: DurableSessionEvent,
) -> None:
    session_id, message_id = _event_identity(event)
    expected_prompt = prompt_from_event(event)
    expected_delivery = delivery_from_event(event)
    expected_time = timestamp_from_event(event)
    cursor = connection.execute(
        """
        UPDATE session_input
        SET promoted_seq = ?
        WHERE id = ? AND session_id = ? AND promoted_seq IS NULL
        """,
        (event.seq, message_id, session_id),
    )
    if cursor.rowcount == 1:
        stored = _from_row(_find_row(connection, message_id))
        if not _matches_projection(
            stored,
            session_id=session_id,
            prompt=expected_prompt,
            delivery=expected_delivery,
            time_created=expected_time,
        ):
            raise SessionInputLifecycleConflict(message_id)
        return

    row = _find_row(connection, message_id)
    if row is not None:
        stored = _from_row(row)
        if (
            stored.promoted_seq == event.seq
            and _matches_projection(
                stored,
                session_id=session_id,
                prompt=expected_prompt,
                delivery=expected_delivery,
                time_created=expected_time,
            )
        ):
            return
        raise SessionInputLifecycleConflict(message_id)

    occupied = connection.execute(
        "SELECT id FROM session_message WHERE id = ?",
        (message_id,),
    ).fetchone()
    if occupied is not None:
        raise SessionInputLifecycleConflict(message_id)
    try:
        connection.execute(
            """
            INSERT INTO session_input (
                id, session_id, prompt, delivery, admitted_seq,
                promoted_seq, time_created
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                _prompt_json(expected_prompt),
                expected_delivery,
                event.seq,
                event.seq,
                expected_time,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise SessionInputLifecycleConflict(message_id) from exc


def _event_identity(event: DurableSessionEvent) -> tuple[str, str]:
    session_id = _required_text(event.data.get("session_id"), "session_id")
    message_id = _message_id(event.data.get("message_id"))
    if session_id != event.aggregate_id:
        raise SessionInputLifecycleConflict(message_id)
    return session_id, message_id


def _matches_projection(
    admitted: SessionInput,
    *,
    session_id: str,
    prompt: dict[str, Any] | str,
    delivery: str,
    time_created: int,
) -> bool:
    return equivalent(
        admitted,
        session_id=session_id,
        prompt=prompt,
        delivery=delivery,
    ) and admitted.time_created == int(time_created)


def _equivalent_or_conflict(
    admitted: SessionInput,
    expected: dict[str, Any],
) -> SessionInput:
    if equivalent(
        admitted,
        session_id=str(expected["session_id"]),
        prompt=expected["prompt"],
        delivery=str(expected["delivery"]),
    ):
        return admitted
    raise SessionPromptConflictError(f"PromptConflict for message_id: {admitted.id}")


def _find_row(connection: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, session_id, prompt, delivery, admitted_seq,
               promoted_seq, time_created
        FROM session_input
        WHERE id = ?
        """,
        (str(message_id),),
    ).fetchone()


def _from_row(row: Any) -> SessionInput:
    prompt = json.loads(str(row["prompt"]))
    return SessionInput(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        prompt=canonical_prompt(prompt),
        delivery=canonical_delivery(str(row["delivery"])),
        admitted_seq=int(row["admitted_seq"]),
        promoted_seq=(
            None if row["promoted_seq"] is None else int(row["promoted_seq"])
        ),
        time_created=int(row["time_created"]),
    )


def _message_id(value: Any | None) -> str:
    identifier = str(value or create_session_message_id()).strip()
    if not identifier.startswith("msg_") or len(identifier) <= 4:
        raise ValueError("message_id must use the msg_ prefix")
    return identifier


def _required_text(value: Any, name: str) -> str:
    text = str(sanitize_unicode(value or "")).strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _prompt_json(prompt: dict[str, Any]) -> str:
    return json.dumps(
        canonical_prompt(prompt),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "AdmittedSessionInput",
    "PROMPT_ADMITTED",
    "PromptConflictError",
    "SESSION_INPUT_DELIVERIES",
    "SessionInput",
    "SessionInputLifecycleConflict",
    "SessionInputService",
    "SessionPromptConflictError",
    "canonical_delivery",
    "canonical_prompt",
    "delivery_from_event",
    "equivalent",
    "project_prompt_admitted_event",
    "project_session_input_prompted",
    "prompt_from_event",
    "timestamp_from_event",
]
