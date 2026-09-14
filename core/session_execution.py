"""Process-global Session execution routed from durable Session identity."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

from core.database import DatabaseService, get_database_service
from core.session import SessionInfo
from core.session_run_coordinator import SessionRunCoordinator
from core.session_runner import SessionRunner
from core.session_store import SessionStore


RunnerResolver = Callable[[SessionInfo], SessionRunner]


class SessionExecution:
    """Resolve a fresh Session runtime for each process-local drain."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        database: DatabaseService | None = None,
        runner_resolver: RunnerResolver | None = None,
    ) -> None:
        selected_database = database or get_database_service(database_path)
        selected_database.ensure_ready()
        self.store = SessionStore(database=selected_database)
        self._runner_resolver = runner_resolver or self._default_runner_resolver
        self.coordinator = SessionRunCoordinator(self._drain)

    def active(self) -> set[str]:
        return self.coordinator.active()

    def resume(self, session_id: str) -> None:
        self.coordinator.run(session_id)

    def wake(self, session_id: str) -> None:
        self.coordinator.wake(session_id)

    def interrupt(self, session_id: str) -> None:
        self.coordinator.interrupt(session_id)

    def _drain(
        self,
        session_id: str,
        force: bool,
        cancelled: threading.Event,
    ) -> None:
        session = self.store.get(session_id)
        runner = self._runner_resolver(session)
        runner.run(session.id, force, cancelled)

    @staticmethod
    def _default_runner_resolver(session: SessionInfo) -> SessionRunner:
        from core.agent_factory import build_session_runner

        return build_session_runner(session)


_PROCESS_EXECUTION: SessionExecution | None = None
_PROCESS_EXECUTION_LOCK = threading.Lock()


def get_session_execution() -> SessionExecution:
    global _PROCESS_EXECUTION
    with _PROCESS_EXECUTION_LOCK:
        if _PROCESS_EXECUTION is None:
            _PROCESS_EXECUTION = SessionExecution()
        return _PROCESS_EXECUTION


__all__ = [
    "RunnerResolver",
    "SessionExecution",
    "get_session_execution",
]
