"""Bounded process-local transport, never the source of durable Session history."""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass
import threading
from typing import Any

from core.unicode_safety import sanitize_unicode


@dataclass(frozen=True)
class LiveEvent:
    seq: int
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class LiveBatch:
    events: tuple[LiveEvent, ...]
    cursor: int
    reset: bool = False


class LiveJournal:
    """Thread-safe per-session replay with explicit detection of expired cursors."""

    def __init__(self, capacity: int = 2048, sessions: int = 128) -> None:
        self.capacity = capacity
        self.sessions = sessions
        self._channels: OrderedDict[str, tuple[int, deque[LiveEvent]]] = OrderedDict()
        self._lock = threading.Lock()

    def publish(self, session_id: str, event_type: str, data: dict[str, Any]) -> LiveEvent:
        with self._lock:
            seq, events = self._channels.pop(session_id, (0, deque(maxlen=self.capacity)))
            event = LiveEvent(seq + 1, event_type, sanitize_unicode(deepcopy(data)))
            events.append(event)
            self._channels[session_id] = (event.seq, events)
            while len(self._channels) > self.sessions:
                self._channels.popitem(last=False)
            return event

    def read(self, session_id: str, after: int = 0) -> LiveBatch:
        with self._lock:
            seq, events = self._channels.get(session_id, (0, ()))
            reset = after > seq or bool(events and after < events[0].seq - 1)
            return LiveBatch(tuple(deepcopy(e) for e in events if reset or e.seq > after), seq, reset)


session_live = LiveJournal()
