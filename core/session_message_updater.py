"""Project durable Session events into canonical Session messages."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from core.session_event import DurableSessionEvent
from core.unicode_safety import sanitize_unicode


PROMPTED = "session.next.prompted"
CONTEXT_UPDATED = "session.next.context.updated"
COMPACTION_ENDED = "session.next.compaction.ended"
SYNTHETIC = "session.next.synthetic"
STEP_STARTED = "session.next.step.started"
STEP_ENDED = "session.next.step.ended"
STEP_FAILED = "session.next.step.failed"
TEXT_STARTED = "session.next.text.started"
TEXT_ENDED = "session.next.text.ended"
TOOL_INPUT_STARTED = "session.next.tool.input.started"
TOOL_INPUT_ENDED = "session.next.tool.input.ended"
TOOL_CALLED = "session.next.tool.called"
TOOL_SUCCESS = "session.next.tool.success"
TOOL_FAILED = "session.next.tool.failed"

SESSION_MESSAGE_EVENT_TYPES = frozenset(
    {
        PROMPTED,
        CONTEXT_UPDATED,
        COMPACTION_ENDED,
        SYNTHETIC,
        STEP_STARTED,
        STEP_ENDED,
        STEP_FAILED,
        TEXT_STARTED,
        TEXT_ENDED,
        TOOL_INPUT_STARTED,
        TOOL_INPUT_ENDED,
        TOOL_CALLED,
        TOOL_SUCCESS,
        TOOL_FAILED,
    }
)


class SessionMessageProjectionError(RuntimeError):
    """Raised when an event cannot be applied to canonical Session history."""


class SessionMessageUpdater:
    """Apply one durable event without changing an existing message sequence."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def apply(self, event: DurableSessionEvent) -> None:
        if event.type not in SESSION_MESSAGE_EVENT_TYPES:
            raise SessionMessageProjectionError(
                f"Unsupported Session message event type: {event.type}"
            )
        session_id = _required(event.data, "session_id")
        if session_id != event.aggregate_id:
            raise SessionMessageProjectionError(
                "Session message event aggregate_id must equal session_id"
            )
        handlers = {
            PROMPTED: self._prompted,
            CONTEXT_UPDATED: self._context_updated,
            COMPACTION_ENDED: self._compaction_ended,
            SYNTHETIC: self._synthetic,
            STEP_STARTED: self._step_started,
            STEP_ENDED: self._step_ended,
            STEP_FAILED: self._step_failed,
            TEXT_STARTED: self._text_started,
            TEXT_ENDED: self._text_ended,
            TOOL_INPUT_STARTED: self._tool_input_started,
            TOOL_INPUT_ENDED: self._tool_input_ended,
            TOOL_CALLED: self._tool_called,
            TOOL_SUCCESS: self._tool_success,
            TOOL_FAILED: self._tool_failed,
        }
        handlers[event.type](event)

    def _prompted(self, event: DurableSessionEvent) -> None:
        from core.session_input import prompt_from_event

        created = _timestamp(event)
        prompt = prompt_from_event(event)
        data = {
            "text": str(sanitize_unicode(prompt["text"])),
            "time": {"created": created},
        }
        self._insert(event, _required(event.data, "message_id"), "user", data)

    def _context_updated(self, event: DurableSessionEvent) -> None:
        created = _timestamp(event)
        data = {
            "text": str(sanitize_unicode(event.data.get("text") or "")),
            "time": {"created": created},
        }
        self._insert(event, _required(event.data, "message_id"), "system", data)

    def _compaction_ended(self, event: DurableSessionEvent) -> None:
        created = _timestamp(event)
        reason = str(event.data.get("reason") or "")
        if reason not in {"auto", "manual"}:
            raise SessionMessageProjectionError("Compaction reason must be auto or manual")
        data = {
            "reason": reason,
            "summary": str(sanitize_unicode(event.data.get("text") or "")),
            "recent": str(sanitize_unicode(event.data.get("recent") or "")),
            "time": {"created": created},
        }
        if not data["summary"].strip():
            raise SessionMessageProjectionError("Compaction summary must not be empty")
        self._insert(
            event,
            _required(event.data, "message_id"),
            "compaction",
            data,
        )

    def _synthetic(self, event: DurableSessionEvent) -> None:
        created = _timestamp(event)
        data = {
            "text": str(sanitize_unicode(event.data.get("text") or "")),
            "time": {"created": created},
        }
        self._insert(
            event,
            _required(event.data, "message_id"),
            "synthetic",
            data,
        )

    def _step_started(self, event: DurableSessionEvent) -> None:
        created = _timestamp(event)
        current = self.get_current_assistant(event.aggregate_id)
        if current is not None:
            current_id, current_data = current
            current_data.setdefault("time", {})["completed"] = created
            self._update(event, current_id, current_data)
        data: dict[str, Any] = {
            "content": [],
            "time": {"created": created},
        }
        model = event.data.get("model")
        if model:
            data["model"] = sanitize_unicode(model)
        provider = event.data.get("provider")
        if provider:
            data["provider"] = str(sanitize_unicode(provider))
        self._insert(
            event,
            _required(event.data, "assistant_message_id"),
            "assistant",
            data,
        )

    def get_current_assistant(
        self,
        session_id: str,
    ) -> tuple[str, dict[str, Any]] | None:
        """Return only the latest incomplete Assistant in one Session."""

        row = self.connection.execute(
            """
            SELECT id, data FROM session_message
            WHERE session_id = ? AND type = 'assistant'
            ORDER BY seq DESC
            LIMIT 1
            """,
            (str(session_id),),
        ).fetchone()
        if row is None:
            return None
        data = json.loads(str(row["data"]))
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise SessionMessageProjectionError("Assistant message data is invalid")
        time_data = data.get("time")
        if not isinstance(time_data, dict):
            raise SessionMessageProjectionError("Assistant message time is invalid")
        if time_data.get("completed") is not None:
            return None
        return str(row["id"]), data

    def _step_ended(self, event: DurableSessionEvent) -> None:
        message_id, data = self._assistant(event)
        data["finish"] = str(sanitize_unicode(event.data.get("finish") or "stop"))
        data.setdefault("time", {})["completed"] = _timestamp(event)
        provider_metadata = event.data.get("provider_metadata")
        if isinstance(provider_metadata, dict) and provider_metadata:
            data["provider_metadata"] = sanitize_unicode(provider_metadata)
        self._update(event, message_id, data)

    def _step_failed(self, event: DurableSessionEvent) -> None:
        message_id, data = self._assistant(event)
        data["finish"] = "error"
        data["error"] = {
            "message": str(sanitize_unicode(event.data.get("error") or "Provider turn failed.")),
            "error_code": str(sanitize_unicode(event.data.get("error_code") or "provider_error")),
        }
        data.setdefault("time", {})["completed"] = _timestamp(event)
        self._update(event, message_id, data)

    def _text_started(self, event: DurableSessionEvent) -> None:
        message_id, data = self._assistant(event)
        text_id = _required(event.data, "text_id")
        if _content_item(data, "text", text_id) is not None:
            raise SessionMessageProjectionError(f"Duplicate text item: {text_id}")
        data["content"].append({"type": "text", "id": text_id, "text": ""})
        self._update(event, message_id, data)

    def _text_ended(self, event: DurableSessionEvent) -> None:
        message_id, data = self._assistant(event)
        text_id = _required(event.data, "text_id")
        item = _content_item(data, "text", text_id)
        if item is None:
            raise SessionMessageProjectionError(f"Text item not found: {text_id}")
        item["text"] = str(sanitize_unicode(event.data.get("text") or ""))
        self._update(event, message_id, data)

    def _tool_input_started(self, event: DurableSessionEvent) -> None:
        message_id, data = self._assistant(event)
        call_id = _required(event.data, "call_id")
        if _content_item(data, "tool", call_id) is not None:
            raise SessionMessageProjectionError(f"Duplicate tool call: {call_id}")
        data["content"].append(
            {
                "type": "tool",
                "id": call_id,
                "name": _required(event.data, "name"),
                "state": {"status": "pending", "input": ""},
                "time": {"created": _timestamp(event)},
            }
        )
        self._update(event, message_id, data)

    def _tool_input_ended(self, event: DurableSessionEvent) -> None:
        message_id, data, tool = self._tool(event)
        if str(tool.get("state", {}).get("status") or "") != "pending":
            raise SessionMessageProjectionError("Tool input can end only while pending")
        tool["state"]["input"] = str(sanitize_unicode(event.data.get("text") or ""))
        self._update(event, message_id, data)

    def _tool_called(self, event: DurableSessionEvent) -> None:
        message_id, data, tool = self._tool(event)
        if str(tool.get("state", {}).get("status") or "") != "pending":
            raise SessionMessageProjectionError("Tool can run only from pending")
        tool["name"] = _required(event.data, "name")
        tool["state"] = {
            "status": "running",
            "input": sanitize_unicode(dict(event.data.get("input") or {})),
        }
        tool["time"]["ran"] = _timestamp(event)
        self._update(event, message_id, data)

    def _tool_success(self, event: DurableSessionEvent) -> None:
        message_id, data, tool = self._tool(event)
        if str(tool.get("state", {}).get("status") or "") != "running":
            raise SessionMessageProjectionError("Tool success requires running state")
        observation = _observation(event)
        tool["state"] = {
            "status": "completed",
            "input": sanitize_unicode(dict(tool["state"].get("input") or {})),
            "observation": observation,
        }
        tool["time"]["completed"] = _timestamp(event)
        self._update(event, message_id, data)

    def _tool_failed(self, event: DurableSessionEvent) -> None:
        message_id, data, tool = self._tool(event)
        prior = str(tool.get("state", {}).get("status") or "")
        if prior not in {"pending", "running"}:
            raise SessionMessageProjectionError(
                "Tool failure requires pending or running state"
            )
        observation = _observation(event)
        previous_input = tool.get("state", {}).get("input")
        metadata = (
            observation.get("metadata")
            if isinstance(observation.get("metadata"), dict)
            else {}
        )
        observed_arguments = metadata.get("tool_arguments")
        structured_input = (
            previous_input
            if isinstance(previous_input, dict)
            else observed_arguments
            if isinstance(observed_arguments, dict)
            else {}
        )
        next_state = {
            "status": "error",
            "input": sanitize_unicode(structured_input),
            "raw_input": (
                str(sanitize_unicode(previous_input))
                if isinstance(previous_input, str)
                else ""
            ),
            "observation_status": str(observation.get("status") or "failed"),
            "error": str(observation.get("error") or ""),
            "error_code": str(observation.get("error_code") or ""),
            "observation": observation,
        }
        for flag in ("real_execution", "tool_executed"):
            value = _execution_flag_value(observation, flag)
            if value is not None:
                next_state[flag] = value
        tool["state"] = next_state
        tool["time"]["completed"] = _timestamp(event)
        self._update(event, message_id, data)

    def _assistant(self, event: DurableSessionEvent) -> tuple[str, dict[str, Any]]:
        message_id = _required(event.data, "assistant_message_id")
        row = self.connection.execute(
            """
            SELECT type, data FROM session_message
            WHERE id = ? AND session_id = ?
            """,
            (message_id, event.aggregate_id),
        ).fetchone()
        if row is None or str(row["type"]) != "assistant":
            raise SessionMessageProjectionError(
                f"Assistant message not found in Session: {message_id}"
            )
        data = json.loads(str(row["data"]))
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise SessionMessageProjectionError("Assistant message data is invalid")
        return message_id, data

    def _tool(
        self,
        event: DurableSessionEvent,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        message_id, data = self._assistant(event)
        call_id = _required(event.data, "call_id")
        item = _content_item(data, "tool", call_id)
        if item is None:
            raise SessionMessageProjectionError(f"Tool call not found: {call_id}")
        return message_id, data, item

    def _insert(
        self,
        event: DurableSessionEvent,
        message_id: str,
        message_type: str,
        data: dict[str, Any],
    ) -> None:
        if not message_id.startswith("msg_"):
            raise SessionMessageProjectionError("Session message id must use msg_ prefix")
        created = _timestamp(event)
        self.connection.execute(
            """
            INSERT INTO session_message (
                id, session_id, type, seq, time_created, time_updated, data
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                event.aggregate_id,
                message_type,
                event.seq,
                created,
                created,
                _json(data),
            ),
        )

    def _update(
        self,
        event: DurableSessionEvent,
        message_id: str,
        data: dict[str, Any],
    ) -> None:
        cursor = self.connection.execute(
            """
            UPDATE session_message
            SET data = ?, time_updated = ?
            WHERE id = ? AND session_id = ?
            """,
            (_json(data), _timestamp(event), message_id, event.aggregate_id),
        )
        if cursor.rowcount != 1:
            raise SessionMessageProjectionError("Session message update lost its scope")


def project_session_message_event(
    connection: sqlite3.Connection,
    event: DurableSessionEvent,
) -> None:
    SessionMessageUpdater(connection).apply(event)


def _required(data: dict[str, Any], key: str) -> str:
    value = str(sanitize_unicode(data.get(key) or "")).strip()
    if not value:
        raise SessionMessageProjectionError(f"Session event missing field: {key}")
    return value


def _timestamp(event: DurableSessionEvent) -> int:
    timestamp = event.data.get("timestamp")
    return int(event.time_created if timestamp is None else timestamp)


def _content_item(
    data: dict[str, Any],
    item_type: str,
    item_id: str,
) -> dict[str, Any] | None:
    for item in reversed(data.get("content") or []):
        if (
            isinstance(item, dict)
            and item.get("type") == item_type
            and str(item.get("id") or "") == item_id
        ):
            return item
    return None


def _observation(event: DurableSessionEvent) -> dict[str, Any]:
    value = event.data.get("observation")
    if not isinstance(value, dict):
        raise SessionMessageProjectionError("Tool settlement requires observation")
    return dict(sanitize_unicode(value))


def _execution_flag_value(
    observation: dict[str, Any],
    name: str,
) -> bool | None:
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    metadata = (
        observation.get("metadata")
        if isinstance(observation.get("metadata"), dict)
        else {}
    )
    if name in data:
        return bool(data[name])
    if name in metadata:
        return bool(metadata[name])
    return None


def _json(data: dict[str, Any]) -> str:
    return json.dumps(
        sanitize_unicode(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "COMPACTION_ENDED",
    "CONTEXT_UPDATED",
    "PROMPTED",
    "SESSION_MESSAGE_EVENT_TYPES",
    "STEP_ENDED",
    "STEP_FAILED",
    "STEP_STARTED",
    "SessionMessageProjectionError",
    "SessionMessageUpdater",
    "SYNTHETIC",
    "TEXT_ENDED",
    "TEXT_STARTED",
    "TOOL_CALLED",
    "TOOL_FAILED",
    "TOOL_INPUT_ENDED",
    "TOOL_INPUT_STARTED",
    "TOOL_SUCCESS",
    "project_session_message_event",
]
