"""Durable System Context epoch for one Session."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import time
from typing import Callable

from core.database import DatabaseService, get_database_service
from core.session_event import DurableSessionEvent, SessionEventPublisher
from core.session_message import create_session_message_id
from core.session_message_updater import CONTEXT_UPDATED
from core.session_history import SessionHistory
from core.system_context import (
    ReplacementBlocked,
    ReplacementReady,
    SystemContext,
    SystemContextSnapshot,
    Unchanged,
    Updated,
)


@dataclass(frozen=True)
class SessionContextEpochState:
    session_id: str
    baseline: str
    snapshot: SystemContextSnapshot
    baseline_seq: int


class SessionContextEpoch:
    def __init__(
        self,
        database_path: str | None = None,
        *,
        database: DatabaseService | None = None,
        events: SessionEventPublisher | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database.ensure_ready()
        self.events = events or SessionEventPublisher(database=self.database)

    def find(self, session_id: str) -> SessionContextEpochState | None:
        with self.database.read_transaction() as connection:
            row = connection.execute(
                "SELECT session_id, baseline, snapshot, baseline_seq "
                "FROM session_context_epoch WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        if row is None:
            return None
        return _decode(row)

    def initialize(
        self,
        session_id: str,
        load_context: Callable[[], SystemContext],
    ) -> SessionContextEpochState | None:
        """Create the first exact baseline; do not observe if it already exists."""

        if self.find(session_id) is not None:
            return None
        generation = load_context().initialize()
        baseline_seq = self.events.latest_sequence(session_id)
        encoded = _encode_snapshot(generation.snapshot)
        try:
            with self.database.write_transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO session_context_epoch
                        (session_id, baseline, snapshot, baseline_seq)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(session_id), generation.baseline, encoded, baseline_seq),
                )
        except sqlite3.IntegrityError:
            return None
        return SessionContextEpochState(
            session_id=str(session_id),
            baseline=generation.baseline,
            snapshot=generation.snapshot,
            baseline_seq=baseline_seq,
        )

    def prepare(
        self,
        session_id: str,
        load_context: Callable[[], SystemContext],
    ) -> SessionContextEpochState:
        stored = self.find(session_id)
        if stored is None:
            initialized = self.initialize(session_id, load_context)
            return initialized or self._required(session_id)
        context = load_context()
        compaction = SessionHistory(database=self.database).latest_compaction(session_id)
        replacement_seq = (
            compaction.seq
            if compaction is not None and compaction.seq > stored.baseline_seq
            else None
        )
        result = (
            context.replace(stored.snapshot)
            if replacement_seq is not None
            else context.reconcile(stored.snapshot)
        )
        if isinstance(result, (Unchanged, ReplacementBlocked)):
            return stored
        if isinstance(result, ReplacementReady):
            generation = result.generation
            baseline_seq = (
                replacement_seq
                if replacement_seq is not None
                else self.events.latest_sequence(session_id)
            )
            with self.database.write_transaction() as connection:
                connection.execute(
                    """
                    UPDATE session_context_epoch
                    SET baseline = ?, snapshot = ?, baseline_seq = ?
                    WHERE session_id = ?
                    """,
                    (
                        generation.baseline,
                        _encode_snapshot(generation.snapshot),
                        baseline_seq,
                        str(session_id),
                    ),
                )
            return SessionContextEpochState(
                str(session_id), generation.baseline, generation.snapshot, baseline_seq
            )
        assert isinstance(result, Updated)
        timestamp = int(time.time() * 1000)

        def advance(connection: sqlite3.Connection, _event: DurableSessionEvent) -> None:
            cursor = connection.execute(
                "UPDATE session_context_epoch SET snapshot = ? WHERE session_id = ?",
                (_encode_snapshot(result.snapshot), str(session_id)),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Session Context Epoch disappeared during update")

        self.events.publish(
            aggregate_id=str(session_id),
            event_type=CONTEXT_UPDATED,
            data={
                "session_id": str(session_id),
                "message_id": create_session_message_id(),
                "timestamp": timestamp,
                "text": result.text,
            },
            time_created=timestamp,
            commit=advance,
        )
        return SessionContextEpochState(
            stored.session_id,
            stored.baseline,
            result.snapshot,
            stored.baseline_seq,
        )

    def _required(self, session_id: str) -> SessionContextEpochState:
        row = self.find(session_id)
        if row is None:
            raise RuntimeError(f"Session Context Epoch not found: {session_id}")
        return row


def _encode_snapshot(snapshot: SystemContextSnapshot) -> str:
    return json.dumps(
        snapshot.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _decode(row: sqlite3.Row) -> SessionContextEpochState:
    raw = json.loads(str(row["snapshot"]))
    return SessionContextEpochState(
        session_id=str(row["session_id"]),
        baseline=str(row["baseline"]),
        snapshot=SystemContextSnapshot.from_dict(raw),
        baseline_seq=int(row["baseline_seq"]),
    )


__all__ = ["SessionContextEpoch", "SessionContextEpochState"]
