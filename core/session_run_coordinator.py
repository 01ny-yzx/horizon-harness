"""Process-local same-Session execution serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable


Drain = Callable[[str, bool, threading.Event], None]


class SessionInactiveError(RuntimeError):
    """Steering requires an active, non-stopping execution."""


class SessionBusyError(RuntimeError):
    """A configuration change cannot race a running session."""


@dataclass
class _Entry:
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    pending_wake: bool = False
    restart_on_failure: bool = False
    stopping: bool = False
    error: BaseException | None = None


class SessionRunCoordinator:
    """Serialize one active drain per Session while allowing distinct Sessions."""

    def __init__(self, drain: Drain) -> None:
        self._drain = drain
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}

    def active(self) -> set[str]:
        with self._lock:
            return set(self._entries)

    def configure_idle(self, session_id: str, update: Callable):
        with self._lock:
            if str(session_id) in self._entries:
                raise SessionBusyError('任务正在执行，请先中断再切换模型')
            return update()

    def configure_active(self, session_id: str, update: Callable):
        with self._lock:
            entry = self._entries.get(str(session_id))
            if entry is None or entry.stopping or entry.error is not None or entry.done.is_set():
                raise SessionInactiveError('当前没有正在执行的任务，消息将继续排队')
            return update()

    def wake(self, session_id: str, *, restart_on_failure: bool = True) -> None:
        key = str(session_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.pending_wake = True
                entry.restart_on_failure |= restart_on_failure
                return
            entry = _Entry()
            self._entries[key] = entry
            self._start(key, entry, force=False)

    def run(self, session_id: str) -> None:
        key = str(session_id)
        while True:
            with self._lock:
                entry = self._entries.get(key)
                if entry is None:
                    entry = _Entry()
                    self._entries[key] = entry
                    self._start(key, entry, force=True)
                    retry_after_stop = False
                else:
                    retry_after_stop = entry.stopping
            entry.done.wait()
            if retry_after_stop:
                continue
            if entry.error is not None:
                raise entry.error
            return None

    def interrupt(self, session_id: str) -> None:
        key = str(session_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.stopping = True
            entry.pending_wake = False
            entry.cancelled.set()
        entry.done.wait()

    def _start(self, key: str, entry: _Entry, *, force: bool) -> None:
        thread = threading.Thread(
            target=self._own,
            args=(key, entry, force),
            name=f"horizon-session-{key}",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            if self._entries.get(key) is entry:
                self._entries.pop(key, None)
            entry.done.set()
            raise

    def _own(self, key: str, entry: _Entry, force: bool) -> None:
        current_force = force
        while True:
            try:
                self._drain(key, current_force, entry.cancelled)
            except BaseException as exc:
                entry.error = exc
            if not self._settle(key, entry):
                return
            current_force = False

    def _settle(self, key: str, entry: _Entry) -> bool:
        """Atomically settle one drain or retain/transfer its pending wake."""

        with self._lock:
            if entry.error is None and not entry.stopping and entry.pending_wake:
                entry.pending_wake = False
                entry.restart_on_failure = False
                return True

            restart = entry.pending_wake and (
                entry.restart_on_failure or (entry.error is None and not entry.stopping)
            )
            successor = _Entry() if restart else None
            if self._entries.get(key) is entry:
                if successor is None:
                    self._entries.pop(key, None)
                else:
                    self._entries[key] = successor
                    try:
                        self._start(key, successor, force=False)
                    except BaseException:
                        entry.done.set()
                        return False
            entry.done.set()
            return False

__all__ = ["Drain", "SessionRunCoordinator"]
