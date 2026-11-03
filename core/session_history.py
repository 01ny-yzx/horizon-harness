"""Read canonical durable Session history in aggregate sequence order."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import json

from core.database import DatabaseService, get_database_service
from core.session_message import SessionMessage, decode_session_message_row


@dataclass(frozen=True)
class SessionHistoryEntry:
    seq: int
    message: SessionMessage


class SessionHistory:
    """Load only the projected conversation for one durable Session."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        database: DatabaseService | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database.ensure_ready()

    def load(self, session_id: str) -> list[SessionMessage]:
        with self.database.read_transaction() as connection:
            row = connection.execute(
                "SELECT baseline_seq FROM session_context_epoch WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            baseline_seq = int(row["baseline_seq"]) if row is not None else -1
            compaction_seq = _latest_compaction_seq(connection, session_id)
            rows = _load_rows(connection, session_id, baseline_seq, compaction_seq)
        return [decode_session_message_row(item) for item in rows]

    def load_transcript(self, session_id: str) -> list[SessionMessage]:
        """One atomic display snapshot; admission does not promote runner inputs."""
        from core.session_run_status import RUN_FINISHED
        with self.database.read_transaction() as connection:
            # UI history retains past turns even after the runner compacts its context.
            rows = connection.execute(
                'SELECT id, session_id, type, seq, time_created, time_updated, data FROM session_message WHERE session_id = ? ORDER BY seq',
                (session_id,)).fetchall()
            messages = [decode_session_message_row(row) for row in rows]
            inputs = connection.execute('''
                SELECT i.id, i.prompt, i.admitted_seq, i.time_created,
                       CASE WHEN c.action = 'steer' THEN 'steer' ELSE i.delivery END AS delivery,
                       COALESCE(c.claimed, 0) AS claimed
                FROM session_input i LEFT JOIN session_input_control c ON c.message_id = i.id
                WHERE i.session_id = ? AND COALESCE(c.action, '') != 'recall'
                ORDER BY i.admitted_seq
            ''', (session_id,)).fetchall()
            admission_order = {row['id']: row['admitted_seq'] for row in inputs}
            messages = [replace(message, seq=admission_order[message.id])
                        if message.type == 'user' and message.id in admission_order else message
                        for message in messages]
            existing = {message.id for message in messages}
            for row in inputs:
                if row['id'] in existing:
                    continue
                messages.append(SessionMessage(row['id'], session_id, 'user', row['admitted_seq'],
                    row['time_created'], row['time_created'], {'text': json.loads(row['prompt'])['text'],
                        'time': {'created': row['time_created']}, 'delivery_state': 'claimed' if row['claimed'] else 'pending',
                        'delivery': row['delivery']}))
            outcomes = connection.execute('SELECT id, seq, time_created, data FROM event WHERE aggregate_id = ? AND type = ? ORDER BY seq',
                                          (session_id, RUN_FINISHED)).fetchall()
            for row in outcomes:
                data = json.loads(row['data'])
                if data['status'] != 'completed':
                    messages.append(SessionMessage(row['id'], session_id, 'run_status', row['seq'],
                        row['time_created'], row['time_created'], data))
        return sorted(messages, key=lambda message: message.seq)

    def load_for_runner(
        self,
        session_id: str,
        baseline_seq: int,
    ) -> list[SessionMessage]:
        """Load chronology while excluding System updates absorbed by the baseline."""

        return [entry.message for entry in self.entries_for_runner(session_id, baseline_seq)]

    def entries_for_runner(
        self,
        session_id: str,
        baseline_seq: int,
    ) -> list[SessionHistoryEntry]:
        with self.database.read_transaction() as connection:
            compaction_seq = _latest_compaction_seq(connection, session_id)
            rows = _load_rows(connection, session_id, baseline_seq, compaction_seq)
        return [
            SessionHistoryEntry(seq=int(row["seq"]), message=decode_session_message_row(row))
            for row in rows
        ]

    def latest_compaction(self, session_id: str) -> SessionHistoryEntry | None:
        with self.database.read_transaction() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, type, seq, time_created, time_updated, data
                FROM session_message
                WHERE session_id = ? AND type = 'compaction'
                ORDER BY seq DESC
                LIMIT 1
                """,
                (str(session_id),),
            ).fetchone()
        if row is None:
            return None
        return SessionHistoryEntry(int(row["seq"]), decode_session_message_row(row))


def _latest_compaction_seq(connection, session_id: str) -> int | None:
    row = connection.execute(
        """
        SELECT seq FROM session_message
        WHERE session_id = ? AND type = 'compaction'
        ORDER BY seq DESC LIMIT 1
        """,
        (str(session_id),),
    ).fetchone()
    return None if row is None else int(row["seq"])


def _load_rows(
    connection,
    session_id: str,
    baseline_seq: int,
    compaction_seq: int | None,
):
    if compaction_seq is None:
        boundary = "(type != 'system' OR seq > ?)"
        parameters = (str(session_id), int(baseline_seq))
    else:
        boundary = "((type != 'system' AND seq >= ?) OR (type = 'system' AND seq > ?))"
        parameters = (str(session_id), int(compaction_seq), int(baseline_seq))
    return connection.execute(
        f"""
        SELECT id, session_id, type, seq, time_created, time_updated, data
        FROM session_message
        WHERE session_id = ? AND {boundary}
        ORDER BY seq ASC
        """,
        parameters,
    ).fetchall()


__all__ = ["SessionHistory", "SessionHistoryEntry"]
