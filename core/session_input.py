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


INPUT_CONTROLLED = "session.next.input.controlled"
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
        access_mode: str | None = None,
    ) -> AdmittedSessionInput:
        from core.agent_access_policy import AGENT_ACCESS_MODES, get_agent_access_mode
        if access_mode is not None and access_mode not in AGENT_ACCESS_MODES:
            raise ValueError('Invalid access mode')
        selected_mode = get_agent_access_mode() if access_mode is None else access_mode
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
            self._check_access_mode(identifier, selected_mode)
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
                    "access_mode": selected_mode,
                },
                time_created=created,
            )
        except Exception as exc:
            recorded = self.find(identifier)
            if recorded is not None:
                self._check_access_mode(identifier, selected_mode)
                return _equivalent_or_conflict(recorded, expected)
            if isinstance(exc, SessionInputLifecycleConflict):
                raise SessionPromptConflictError(
                    f"PromptConflict for message_id: {identifier}"
                ) from exc
            raise
        recorded = self.find(identifier)
        if recorded is None:
            raise RuntimeError("PromptAdmitted committed without a session_input projection")
        self._check_access_mode(identifier, selected_mode)
        return _equivalent_or_conflict(recorded, expected)

    def access_mode(self, message_id: str) -> str | None:
        with self.database.read_transaction() as connection:
            row = connection.execute('''
                SELECT e.data FROM session_input i JOIN event e
                ON e.aggregate_id = i.session_id AND e.seq = i.admitted_seq
                WHERE i.id = ?
            ''', (message_id,)).fetchone()
        mode = json.loads(row['data']).get('access_mode') if row else None
        return mode if mode in {'read_only', 'full_access'} else None

    def _check_access_mode(self, message_id: str, mode: str):
        stored = self.access_mode(message_id)
        if stored is not None and stored != mode:
            raise SessionPromptConflictError(f'PromptConflict for message_id: {message_id}')

    def pending_all(self, session_id: str, *, unclaimed: bool = False) -> list[SessionInput]:
        """Read all effective deliveries in one consistent display snapshot."""
        with self.database.read_transaction() as connection:
            rows = connection.execute(_PENDING_SELECT + """
                WHERE i.session_id = ? AND i.promoted_seq IS NULL
                  AND COALESCE(c.action, '') != 'recall'
                  AND (? = 0 OR COALESCE(c.claimed, 0) = 0)
                ORDER BY i.admitted_seq
            """, (str(session_id), int(unclaimed))).fetchall()
        return [_from_row(row) for row in rows]

    def pending(self, session_id: str, delivery: str, *, unclaimed: bool = False) -> list[SessionInput]:
        selected = canonical_delivery(delivery)
        return [item for item in self.pending_all(session_id, unclaimed=unclaimed)
                if item.delivery == selected]

    def has_pending(self, session_id: str, delivery: str) -> bool:
        return bool(self.pending(session_id, delivery))

    def recall(self, session_id: str, message_id: str) -> SessionInput:
        return self._control(session_id, message_id, 'recall')

    def steer(self, session_id: str, message_id: str) -> SessionInput:
        return self._control(session_id, message_id, 'steer')

    def _control(self, session_id: str, message_id: str, action: str) -> SessionInput:
        stored = self.find(message_id)
        if stored is None or stored.session_id != session_id:
            raise KeyError(message_id)
        try:
            self.events.publish(aggregate_id=session_id, event_type=INPUT_CONTROLLED,
                data={'session_id': session_id, 'message_id': message_id, 'action': action})
        except _AlreadyControlled:
            pass
        return stored

    def claim_next_queued(self, session_id: str) -> SessionInput | None:
        # Claim before choosing text/access mode. Controls and promotion serialize
        # against the same SQLite writer, so no stale recalled text can execute.
        with self.database.write_transaction() as connection:
            row = connection.execute(_PENDING_SELECT + """
                WHERE i.session_id = ? AND i.promoted_seq IS NULL
                  AND COALESCE(c.action, '') NOT IN ('recall', 'steer')
                  AND i.delivery = 'queue' ORDER BY i.admitted_seq LIMIT 1
            """, (session_id,)).fetchone()
            if row is None:
                return None
            connection.execute("""INSERT INTO session_input_control(message_id, claimed)
                VALUES (?, 1) ON CONFLICT(message_id) DO UPDATE SET claimed = 1""", (row['id'],))
            return _from_row(row)

    def claim_input(self, session_id: str, message_id: str) -> bool:
        with self.database.write_transaction() as connection:
            row = connection.execute("""SELECT i.id FROM session_input i
                LEFT JOIN session_input_control c ON c.message_id = i.id
                WHERE i.session_id = ? AND i.id = ? AND i.promoted_seq IS NULL
                  AND COALESCE(c.action, '') != 'recall'""", (session_id, message_id)).fetchone()
            if row is None:
                return False
            connection.execute("""INSERT INTO session_input_control(message_id, claimed)
                VALUES (?, 1) ON CONFLICT(message_id) DO UPDATE SET claimed = 1""", (message_id,))
            return True

    def release_claim(self, message_id: str) -> None:
        with self.database.write_transaction() as connection:
            connection.execute('UPDATE session_input_control SET claimed = 0 WHERE message_id = ?', (message_id,))

    def promote_steers(self, session_id: str, cutoff: int) -> int:
        return sum(self._publish_prompted(item) for item in self.pending(session_id, 'steer')
                   if item.admitted_seq <= int(cutoff))

    def promote_next_queued(self, session_id: str) -> bool:
        # Retry if a control wins between selecting and publishing.
        while True:
            queued = self.pending(session_id, 'queue')
            if not queued:
                return False
            if self._publish_prompted(queued[0]):
                return True

    def _publish_prompted(self, admitted: SessionInput) -> bool:
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
                and stored.session_id == admitted.session_id
                and stored.prompt == admitted.prompt
                and stored.time_created == admitted.time_created
            ):
                return False
            if stored is not None and stored.promoted_seq is None:
                with self.database.read_transaction() as connection:
                    control = connection.execute(
                        'SELECT action FROM session_input_control WHERE message_id = ?',
                        (admitted.id,),
                    ).fetchone()
                if control is not None and (
                    control['action'] == 'recall'
                    or (control['action'] == 'steer' and admitted.delivery == 'queue')
                ):
                    return False
            raise exc
        return True


_PENDING_SELECT = """
    SELECT i.id, i.session_id, i.prompt,
           CASE WHEN c.action = 'steer' THEN 'steer' ELSE i.delivery END AS delivery,
           i.admitted_seq, i.promoted_seq, i.time_created
    FROM session_input i LEFT JOIN session_input_control c ON c.message_id = i.id
"""


class _AlreadyControlled(RuntimeError):
    pass


def project_input_controlled(connection: sqlite3.Connection, event: DurableSessionEvent) -> None:
    session_id, message_id = _event_identity(event)
    action = event.data.get('action')
    if action not in {'steer', 'recall'}:
        raise ValueError('Invalid queue control')
    row = _find_row(connection, message_id)
    control = connection.execute('SELECT * FROM session_input_control WHERE message_id = ?', (message_id,)).fetchone()
    if row is None or row['session_id'] != session_id:
        raise KeyError(message_id)
    if row['promoted_seq'] is not None or (control and control['claimed']):
        raise SessionInputLifecycleConflict(message_id)
    if control and control['action'] == action:
        raise _AlreadyControlled(message_id)
    if row['delivery'] != 'queue' or (control and control['action'] in {'recall', 'steer'}):
        raise SessionInputLifecycleConflict(message_id)
    connection.execute("""INSERT INTO session_input_control(message_id, action, control_seq)
        VALUES (?, ?, ?) ON CONFLICT(message_id) DO UPDATE SET
        action = excluded.action, control_seq = excluded.control_seq""", (message_id, action, event.seq))


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
    control = connection.execute('SELECT action FROM session_input_control WHERE message_id = ?', (message_id,)).fetchone()
    if control is not None and control['action'] == 'recall':
        raise SessionInputLifecycleConflict(message_id)
    original = _find_row(connection, message_id)
    effective_delivery = 'steer' if control is not None and control['action'] == 'steer' else (original['delivery'] if original else expected_delivery)
    if effective_delivery != expected_delivery:
        raise SessionInputLifecycleConflict(message_id)
    # Keep immutable admission delivery; validate Prompted against effective delivery.
    projection_delivery = original['delivery'] if original else expected_delivery
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
            delivery=projection_delivery,
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
                delivery=projection_delivery,
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
    "INPUT_CONTROLLED",
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
    "project_input_controlled",
    "project_session_input_prompted",
    "prompt_from_event",
    "timestamp_from_event",
]
