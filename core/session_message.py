"""Canonical durable Session message projection types."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
import uuid

from core.unicode_safety import sanitize_unicode


SESSION_MESSAGE_TYPES = frozenset(
    {"user", "assistant", "synthetic", "system", "compaction"}
)


@dataclass(frozen=True)
class SessionMessage:
    """One canonical Session history row ordered by durable event sequence."""

    id: str
    session_id: str
    type: str
    seq: int
    time_created: int
    time_updated: int
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "type": self.type,
            "seq": self.seq,
            "time_created": self.time_created,
            "time_updated": self.time_updated,
            **sanitize_unicode(dict(self.data)),
        }


def create_session_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def create_text_id() -> str:
    return f"text_{uuid.uuid4().hex}"


def decode_session_message_row(row: Any) -> SessionMessage:
    message_type = str(row["type"])
    if message_type not in SESSION_MESSAGE_TYPES:
        raise ValueError(f"Unsupported Session message type: {message_type}")
    data = json.loads(str(row["data"]))
    if not isinstance(data, dict):
        raise ValueError("Session message data must decode to an object")
    return SessionMessage(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        type=message_type,
        seq=int(row["seq"]),
        time_created=int(row["time_created"]),
        time_updated=int(row["time_updated"]),
        data=dict(data),
    )


__all__ = [
    "SESSION_MESSAGE_TYPES",
    "SessionMessage",
    "create_session_message_id",
    "create_text_id",
    "decode_session_message_row",
]
