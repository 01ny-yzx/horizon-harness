"""Drain eligible durable Session input through an existing Agent runtime."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

from core.database import DatabaseService, get_database_service
from core.session_input import SessionInput, SessionInputService
from core.session_message_updater import TOOL_FAILED
from core.session_store import SessionStore


RunWorkItem = Callable[[str, str | None, threading.Event], None]


class SessionRunner:
    """Orchestrate durable steer/queue work without making model decisions."""

    def __init__(
        self,
        run_work_item: RunWorkItem,
        database_path: Path | str | None = None,
        *,
        database: DatabaseService | None = None,
    ) -> None:
        self.database = database or get_database_service(database_path)
        self.database.ensure_ready()
        self.inputs = SessionInputService(database=self.database)
        self.store = SessionStore(database=self.database)
        self.run_work_item = run_work_item

    def run(
        self,
        session_id: str,
        force: bool,
        cancelled: threading.Event,
    ) -> None:
        has_steer = self.inputs.has_pending(session_id, "steer")
        has_queue = False if has_steer else self.inputs.has_pending(session_id, "queue")
        if not force and not has_steer and not has_queue:
            return
        self.fail_interrupted_tools(session_id)
        should_force = force
        while not cancelled.is_set():
            steers = self.inputs.pending(session_id, "steer")
            queues = [] if steers else self.inputs.pending(session_id, "queue")
            if steers:
                primary = steers[-1]
                promotion: str | None = "steer"
            elif queues:
                primary = queues[0]
                promotion = "queue"
            elif should_force:
                primary = self._latest_visible_user(session_id)
                promotion = None
            else:
                break
            self.run_work_item(
                primary.prompt["text"] if primary is not None else "",
                promotion,
                cancelled,
            )
            should_force = False

    def fail_interrupted_tools(self, session_id: str) -> int:
        settled = 0
        for message in self.store.context(session_id):
            if message.type != "assistant":
                continue
            content = message.data.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool":
                    continue
                state = item.get("state")
                if not isinstance(state, dict) or state.get("status") not in {"pending", "running"}:
                    continue
                self.inputs.events.publish(
                    aggregate_id=session_id,
                    event_type=TOOL_FAILED,
                    data={
                        "session_id": session_id,
                        "assistant_message_id": message.id,
                        "call_id": str(item.get("id") or ""),
                        "observation": {
                            "success": False,
                            "status": "failed",
                            "error": "Tool execution interrupted",
                            "error_code": "tool_execution_interrupted",
                            "data": {
                                "execution_state_unknown": True,
                                "replayed": False,
                            },
                        },
                    },
                )
                settled += 1
        return settled

    def _latest_visible_user(self, session_id: str) -> SessionInput | None:
        for message in reversed(self.store.context(session_id)):
            if message.type != "user":
                continue
            return SessionInput(
                id=message.id,
                session_id=message.session_id,
                prompt={"text": str(message.data.get("text") or "")},
                delivery="steer",
                admitted_seq=message.seq,
                promoted_seq=message.seq,
                time_created=message.time_created,
            )
        return None


__all__ = ["RunWorkItem", "SessionRunner"]
