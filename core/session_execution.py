"""Process-global Session execution routed from durable Session identity."""

from __future__ import annotations

from collections.abc import Callable
import logging
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

    def configure_idle(self, session_id: str, update: Callable):
        return self.coordinator.configure_idle(session_id, update)

    def configure_active(self, session_id: str, update: Callable):
        return self.coordinator.configure_active(session_id, update)

    def resume(self, session_id: str) -> None:
        self.coordinator.run(session_id)

    def wake(self, session_id: str, *, restart_on_failure: bool = True) -> None:
        self.coordinator.wake(session_id, restart_on_failure=restart_on_failure)

    def interrupt(self, session_id: str) -> None:
        self.coordinator.interrupt(session_id)

    def _drain(
        self,
        session_id: str,
        force: bool,
        cancelled: threading.Event,
    ) -> None:
        from core.session_live import session_live
        from providers.streaming import live_stream

        model_failed = False

        def emit(kind, data):
            nonlocal model_failed
            if kind == "llm.failed":
                model_failed = True
            elif kind == "llm.ended":
                model_failed = False
            session_live.publish(session_id, kind, data)

        status = "completed"
        error: dict[str, str] | None = None
        emit("run.started", {})
        try:
            with live_stream(emit, cancelled):
                session = self.store.get(session_id)
                runner = self._runner_resolver(session)
                runner.run(session.id, force, cancelled)
        except BaseException as exc:
            status = "interrupted" if cancelled.is_set() else "failed"
            if status == "failed":
                from core.system_context import SystemContextInitializationBlocked

                error = (
                    {"code": "system_context_unavailable",
                     "message": "无法加载本地记忆或项目指令，请检查本地数据与文件权限"}
                    if isinstance(exc, SystemContextInitializationBlocked)
                    else {"code": "execution_failed", "message": "运行器执行出错，请检查本地配置与日志"}
                )
                if isinstance(exc, ValueError) and str(exc) == '请先配置这个服务的 API Key':
                    error = {'code': 'model_unconfigured', 'message': '请先配置这个服务的 API Key'}
                logging.getLogger(__name__).exception("Session execution failed")
            raise
        finally:
            if cancelled.is_set():
                status = "interrupted"
            elif model_failed:
                status = "failed"
                error = {"code": "provider_failed", "message": "模型调用失败，请检查模型配置、连接和服务状态"}
            outcome = {"status": status, **({"error": error} if error else {})}
            try:
                if status in {'failed', 'interrupted'}:
                    from core.session_event import SessionEventPublisher
                    from core.session_run_status import RUN_FINISHED
                    SessionEventPublisher(database=self.store.database).publish(
                        aggregate_id=session_id, event_type=RUN_FINISHED,
                        data={'session_id': session_id, **outcome})
            finally:
                emit("run.ended", outcome)

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
