"""Durable Session compaction built from canonical Session history."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Iterable, Sequence

from core.context_budget import estimate_request_tokens, estimate_text_tokens
from core.session_event import SessionEventPublisher
from core.session_history import SessionHistoryEntry
from core.session_message import SessionMessage, create_session_message_id
from core.session_message_updater import COMPACTION_ENDED
from core.session_projector import COMPACTION_STARTED
from core.unicode_safety import sanitize_unicode
from providers.base import LLMCallOptions, call_llm_chat_result
from providers.base import LLMCapabilities
from providers.capabilities import resolve_effective_model_limits
from providers.openai_compatible import is_context_overflow_failure as _provider_overflow


DEFAULT_BUFFER_TOKENS = 20_000
DEFAULT_KEEP_TOKENS = 8_000
TOOL_OUTPUT_MAX_CHARS = 2_000
SUMMARY_OUTPUT_TOKENS = 4_096


@dataclass(frozen=True)
class SessionCompactionSelection:
    head: tuple[str, ...]
    recent: tuple[str, ...]
    previous_summary: str = ""
    previous_recent: str = ""


class SessionCompaction:
    """Create model-visible durable checkpoints without deleting source history."""

    def __init__(
        self,
        llm: Any,
        events: SessionEventPublisher,
        *,
        auto: bool = True,
        buffer_tokens: int = DEFAULT_BUFFER_TOKENS,
        keep_tokens: int = DEFAULT_KEEP_TOKENS,
        summary_output_tokens: int = SUMMARY_OUTPUT_TOKENS,
    ) -> None:
        self.llm = llm
        self.events = events
        self.auto = bool(auto)
        self.buffer_tokens = _positive(buffer_tokens, "buffer_tokens")
        self.keep_tokens = _positive(keep_tokens, "keep_tokens")
        self.summary_output_tokens = _positive(
            summary_output_tokens, "summary_output_tokens"
        )

    def compact_if_needed(
        self,
        session_id: str,
        entries: Sequence[SessionHistoryEntry],
        *,
        request_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_context_tokens: int | None,
        model_input_tokens: int | None = None,
        model_output_tokens: int | None = None,
        requested_output_tokens: int | None = None,
    ) -> bool:
        if not self.auto:
            return False
        limits = _limits(
            model_context_tokens,
            model_input_tokens,
            model_output_tokens,
            requested_output_tokens,
        )
        threshold = limits.effective_input_tokens - max(
            limits.requested_output_tokens,
            self.buffer_tokens,
        )
        if estimate_request_tokens(request_messages, tools) <= threshold:
            return False
        return self._compact(session_id, entries, reason="auto", limits=limits)

    def compact_after_overflow(
        self,
        session_id: str,
        entries: Sequence[SessionHistoryEntry],
        *,
        model_context_tokens: int | None,
        model_input_tokens: int | None = None,
        model_output_tokens: int | None = None,
        requested_output_tokens: int | None = None,
    ) -> bool:
        limits = _limits(
            model_context_tokens,
            model_input_tokens,
            model_output_tokens,
            requested_output_tokens,
        )
        return self._compact(session_id, entries, reason="auto", limits=limits)

    def select(
        self, entries: Sequence[SessionHistoryEntry]
    ) -> SessionCompactionSelection | None:
        previous: SessionMessage | None = None
        serialized: list[str] = []
        for entry in entries:
            if entry.message.type == "compaction":
                previous = entry.message
                continue
            rendered = serialize_session_message(entry.message)
            if rendered:
                serialized.append(rendered)

        recent_start = len(serialized)
        recent_tokens = 0
        for index in range(len(serialized) - 1, -1, -1):
            tokens = estimate_text_tokens(serialized[index])
            if recent_tokens + tokens > self.keep_tokens:
                break
            recent_start = index
            recent_tokens += tokens
        head = tuple(serialized[:recent_start])
        recent = tuple(serialized[recent_start:])
        previous_summary = ""
        previous_recent = ""
        if previous is not None:
            previous_summary = str(previous.data.get("summary") or "")
            previous_recent = str(previous.data.get("recent") or "")
        if not serialized:
            return None
        if not head and not previous_summary:
            return None
        return SessionCompactionSelection(
            head=head,
            recent=recent,
            previous_summary=previous_summary,
            previous_recent=previous_recent,
        )

    def _compact(self, session_id: str, entries: Sequence[SessionHistoryEntry], *, reason: str, limits: Any) -> bool:
        selection = self.select(entries)
        if selection is None:
            return False
        prompt = _summary_prompt(selection)
        summary_output = min(
            self.summary_output_tokens,
            int(limits.requested_output_tokens or self.summary_output_tokens),
        )
        if estimate_request_tokens([{"role": "user", "content": prompt}], []) > (
            limits.effective_input_tokens - summary_output
        ):
            return False

        timestamp = int(time.time() * 1000)
        message_id = create_session_message_id()
        self.events.publish(
            aggregate_id=session_id,
            event_type=COMPACTION_STARTED,
            data={
                "session_id": session_id,
                "message_id": message_id,
                "reason": reason,
                "timestamp": timestamp,
            },
            time_created=timestamp,
        )
        try:
            result = call_llm_chat_result(
                self.llm,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                options=LLMCallOptions(
                    stage="session_compaction",
                    max_tokens=summary_output,
                ),
            )
            summary = _message_text(result.message).strip()
        except BaseException:
            return False
        if not summary:
            return False

        recent = "\n\n".join(selection.recent).strip()
        ended_at = int(time.time() * 1000)
        self.events.publish(
            aggregate_id=session_id,
            event_type=COMPACTION_ENDED,
            data={
                "session_id": session_id,
                "message_id": message_id,
                "reason": reason,
                "timestamp": ended_at,
                "text": summary,
                "recent": recent,
            },
            time_created=ended_at,
        )
        return True


def serialize_session_message(message: SessionMessage) -> str:
    """Serialize one complete canonical message for the summary model."""

    if message.type == "user":
        return f"[User]: {message.data.get('text') or ''}"
    if message.type == "synthetic":
        return f"[Assistant]: {message.data.get('text') or ''}"
    if message.type == "system":
        return f"[System update]: {message.data.get('text') or ''}"
    if message.type != "assistant":
        return ""

    sections: list[str] = []
    content = message.data.get("content")
    if not isinstance(content, list):
        return ""
    text_parts = [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    if any(text_parts):
        sections.append("[Assistant]: " + "".join(text_parts))
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool":
            continue
        name = str(item.get("name") or "tool")
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        call = json.dumps(
            sanitize_unicode(state.get("input") if isinstance(state.get("input"), dict) else {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        status = str(state.get("status") or "pending")
        rendered = f"[Assistant tool call]: {name}({call})"
        if status in {"completed", "error"}:
            observation = state.get("observation")
            if not isinstance(observation, dict):
                observation = {
                    key: value
                    for key, value in state.items()
                    if key not in {"input", "raw_input"}
                }
            output = json.dumps(
                sanitize_unicode(observation),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if len(output) > TOOL_OUTPUT_MAX_CHARS:
                output = output[:TOOL_OUTPUT_MAX_CHARS] + "\n[tool output truncated]"
            rendered += f"\n[Tool result - {status}]: {output}"
        sections.append(rendered)
    return "\n".join(sections).strip()


def is_context_overflow_failure(value: Any) -> bool:
    return _provider_overflow(value)


def _summary_prompt(selection: SessionCompactionSelection) -> str:
    context = [selection.previous_recent, *selection.head]
    conversation = "\n\n".join(item for item in context if item)
    prior = (
        "\n\nThe <prior-summary> summarizes everything that happened before the "
        "<conversation>. Combine both; the newer conversation wins on conflict.\n"
        f"<prior-summary>\n{selection.previous_summary}\n</prior-summary>"
        if selection.previous_summary
        else ""
    )
    return f"""Here is the conversation so far:

<conversation>
{conversation}
</conversation>{prior}

Output exactly this Markdown structure and keep every section:

## Objective
- [one or two brief sentences describing the user's goal]

## Important Details
- [constraints, decisions, exact facts, identifiers, or (none)]

## Work State
### Completed
- [finished work or (none)]

### Active
- [current work or (none)]

### Blocked
- [blockers, exact errors, or (none)]

## Next Move
1. [immediate action or (none)]
2. [next action or (none)]

## Relevant Files
- [exact path and why it matters, or (none)]

Use terse bullets. Preserve exact paths, symbols, commands, errors, URLs, and IDs.
Do not mention the summary process or that context was compacted."""


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _limits(context: int | None, input_tokens: int | None, output: int | None, requested: int | None):
    return resolve_effective_model_limits(
        LLMCapabilities(
            max_context_tokens=context,
            max_input_tokens=input_tokens,
            max_output_tokens=output,
        ),
        requested,
        default_output_tokens=SUMMARY_OUTPUT_TOKENS,
    )


def _positive(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


__all__ = [
    "DEFAULT_BUFFER_TOKENS",
    "DEFAULT_KEEP_TOKENS",
    "SUMMARY_OUTPUT_TOKENS",
    "TOOL_OUTPUT_MAX_CHARS",
    "SessionCompaction",
    "SessionCompactionSelection",
    "is_context_overflow_failure",
    "serialize_session_message",
]
