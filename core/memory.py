"""Conversation memory with compact session and task context management."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from core.unicode_safety import sanitize_unicode
from prompts.system_prompt import SYSTEM_PROMPT


MAX_TEXT_CHARS = 4_000
MAX_STDIO_CHARS = 2_000
MAX_SEARCH_MATCHES = 10
MAX_WEB_RESULTS = 5
MAX_FETCH_TEXT_CHARS = 2_000
MAX_RAG_CONTEXT_CHARS = 4_000
TASK_SCOPED_NOTE_TYPES = {
    "web_mode",
    "research_router",
    "guard",
    "research_recovery",
    "reflection",
    "auto_rag",
    "task_state",
    "sources",
    "chunks",
    "vectors",
    "rag_context",
    "fused_context",
    "rag_diagnostics",
    "file_output",
    "mcp_runtime",
    "explicit_tool",
    "tool_schema_scope",
    "runtime_state",
}


@dataclass(frozen=True)
class AgentTurnSessionContext:
    """Provider-ready session history plus compact context accounting."""

    messages: list[dict[str, Any]]
    history_message_count: int
    history_chars: int
    compacted: bool
    current_user_count: int

    def trace_summary(self) -> dict[str, Any]:
        return sanitize_unicode(
            {
                "agent_turn_history_message_count": self.history_message_count,
                "agent_turn_history_chars": self.history_chars,
                "agent_turn_compacted": self.compacted,
                "agent_turn_current_user_count": self.current_user_count,
            }
        )


class Memory:
    """Stores chat-completion messages for a single Agent session."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.current_task_id: str = ""
        self.last_task_intent_summary: dict[str, Any] | None = None

    def begin_task(self, task_id: str, user_input: str = "", task_profile: Any | None = None) -> None:
        """Start a task without destroying prior Session history."""

        self.current_task_id = task_id
        cleaned: list[dict[str, Any]] = []
        for message in self.messages:
            metadata = message.get("metadata", {})
            note_type = metadata.get("note_type")
            if note_type in TASK_SCOPED_NOTE_TYPES:
                continue
            cleaned.append(sanitize_unicode(message))
        self.messages = cleaned

    def add_user_message(self, content: str, task_id: str | None = None) -> None:
        """Save one user request."""

        self.messages.append(
            sanitize_unicode({"role": "user", "content": content, "metadata": self._task_metadata(task_id)})
        )

    def add_assistant_message(
        self,
        content: str | None,
        tool_calls: Any | None = None,
        task_id: str | None = None,
        provider_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save an assistant message, including requested tool calls."""

        message: dict[str, Any] = {"role": "assistant", "content": content, "metadata": self._task_metadata(task_id)}
        if tool_calls:
            message["tool_calls"] = [
                self._to_plain_tool_call(tool_call) for tool_call in tool_calls
            ]
        if provider_metadata:
            message["provider_metadata"] = dict(provider_metadata)
        self.messages.append(sanitize_unicode(message))

    def ensure_task_final_assistant_message(self, task_id: str, content: str) -> None:
        """Persist one user-visible final assistant message for Session continuity."""

        effective_task_id = str(task_id or "").strip()
        final_content = str(content or "")
        for message in reversed(self.messages):
            metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            if str(metadata.get("task_id") or "") != effective_task_id:
                continue
            if message.get("role") == "assistant" and not message.get("tool_calls"):
                if str(message.get("content") or "") == final_content:
                    return
                break
        self.add_assistant_message(content=final_content, tool_calls=None, task_id=effective_task_id)

    def add_tool_observation(
        self,
        tool_call_id: str,
        tool_name: str,
        observation_json: str,
        task_id: str | None = None,
    ) -> None:
        """Save a compacted tool result so the model can reason from it."""

        compacted = sanitize_unicode(self.compact_observation(observation_json))
        self.messages.append(
            sanitize_unicode(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": compacted,
                    "metadata": self._task_metadata(task_id),
                }
            )
        )

    def add_system_note(self, content: str, note_type: str = "general", task_id: str | None = None) -> None:
        """Add a short system note for workflow, state, or guard context."""

        if note_type in TASK_SCOPED_NOTE_TYPES:
            self.messages = [
                message
                for message in self.messages
                if message.get("metadata", {}).get("note_type") != note_type
            ]
        self.messages.append(
            sanitize_unicode(
                {
                    "role": "system",
                    "content": content,
                    "metadata": {"note_type": note_type, **self._task_metadata(task_id)},
                }
            )
        )

    def clear_task_scoped_notes(self) -> None:
        """Remove workflow notes from the previous task while keeping durable context."""

        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") not in TASK_SCOPED_NOTE_TYPES
        ]

    def save_task_intent_summary(self, task_state: Any) -> None:
        """Store a safe high-level summary for follow-up intent resolution."""

        profile = getattr(task_state, "task_profile", None)
        if not profile:
            self.last_task_intent_summary = None
            return
        last_modified_files = _safe_path_list(getattr(task_state, "modified_files", []) or [])
        self.last_task_intent_summary = sanitize_unicode({
            "workflow_kind": getattr(profile, "workflow_kind", getattr(task_state, "task_type", "simple")),
            "topic": getattr(profile, "topic", ""),
            "task_type": getattr(task_state, "task_type", ""),
            "needs_code_edit": bool(getattr(profile, "needs_code_edit", False)),
            "coding_action": getattr(profile, "coding_action", ""),
            "coding_target_area": getattr(profile, "coding_target_area", ""),
            "coding_likely_files": _safe_path_list(getattr(profile, "coding_likely_files", []) or []),
            "coding_required_checks": [
                str(item)[:160] for item in getattr(profile, "coding_required_checks", []) or [] if str(item).strip()
            ],
            "last_modified_files": last_modified_files,
            "needs_file_output": bool(getattr(profile, "needs_file_output", False)),
            "output_format": getattr(profile, "output_format", "unknown"),
            "output_target": getattr(profile, "output_target", "unknown"),
            "output_filename": getattr(profile, "output_filename", ""),
            "requested_output_path": getattr(profile, "requested_output_path", ""),
            "filename_pattern": getattr(profile, "filename_pattern", ""),
            "needs_web_search": bool(getattr(profile, "needs_web_search", False)),
        })

    def add_task_state(self, content: str) -> None:
        """Replace older TaskState notes with the latest state note."""

        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") != "task_state"
        ]
        self.add_system_note(content, note_type="task_state")

    def add_document_note(self, content: str) -> None:
        """Replace older document notes with the latest DocumentStore index."""

        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") != "documents"
        ]
        self.add_system_note(content, note_type="documents")

    def add_chunk_note(self, content: str) -> None:
        """Replace older chunk retrieval notes with the latest selected chunks."""

        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") != "chunks"
        ]
        self.add_system_note(content, note_type="chunks")

    def add_vector_note(self, content: str) -> None:
        """Replace older semantic retrieval notes without storing vectors."""

        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") != "vectors"
        ]
        self.add_system_note(content, note_type="vectors")

    def add_rag_context_note(self, content: str) -> None:
        """Replace older RAG context notes with the latest retrieved context."""

        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") != "rag_context"
        ]
        safe_content = self._truncate_text(content, MAX_RAG_CONTEXT_CHARS)
        self.add_system_note(f"RAG Context:\n{safe_content}", note_type="rag_context")

    def add_fused_context_note(self, content: str) -> None:
        """Replace older fused context notes without duplicating content."""

        safe_content = self._truncate_text(content, 6_000)
        existing = next(
            (
                message
                for message in self.messages
                if message.get("metadata", {}).get("note_type") == "fused_context"
            ),
            None,
        )
        if existing and existing.get("content") == safe_content:
            return
        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") != "fused_context"
        ]
        self.add_system_note(safe_content, note_type="fused_context")

    def add_long_term_memory_note(self, content: str) -> None:
        """Replace older long-term memory notes with the latest note."""

        self.messages = [
            message
            for message in self.messages
            if message.get("metadata", {}).get("note_type") != "long_term_memory"
        ]
        self.add_system_note(content, note_type="long_term_memory")

    def _task_metadata(self, task_id: str | None = None) -> dict[str, str]:
        effective_task_id = task_id or self.current_task_id
        if not effective_task_id:
            return {}
        return {"task_id": effective_task_id, "scope": "task"}

    def get_messages(self) -> list[dict[str, Any]]:
        """Return a provider-ready copy without mutating stored history."""

        return sanitize_unicode([self._strip_internal_fields(message) for message in self.messages])

    def get_task_messages(self, task_id: str) -> list[dict[str, Any]]:
        """Return provider-ready messages scoped to one task plus global system context."""

        effective_task_id = str(task_id or self.current_task_id or "").strip()
        if not effective_task_id:
            return sanitize_unicode([self._strip_internal_fields(message) for message in self.messages])
        selected: list[dict[str, Any]] = []
        for message in self.messages:
            metadata = message.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            message_task_id = str(metadata.get("task_id") or "").strip()
            if message_task_id == effective_task_id:
                selected.append(message)
                continue
            if message.get("role") == "system" and not message_task_id:
                selected.append(message)
        return sanitize_unicode([self._strip_internal_fields(message) for message in selected])

    def get_agent_turn_context(
        self,
        task_id: str,
        current_user_input: str,
        *,
        include_previous_dialogue: bool = True,
    ) -> AgentTurnSessionContext:
        """Return one protocol-safe Session context for any tools-enabled agent turn."""

        compacted_now = False
        effective_task_id = str(task_id or "").strip()
        selected: list[dict[str, Any]] = []
        current_user_seen = False
        compacted_history = compacted_now

        for message in self.messages:
            if not isinstance(message, dict):
                continue
            metadata = message.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            message_task_id = str(metadata.get("task_id") or "").strip()
            note_type = str(metadata.get("note_type") or "")
            role = str(message.get("role") or "")

            if note_type == "summary":
                compacted_history = True
            if role == "system":
                if (
                    not include_previous_dialogue
                    and message_task_id
                    and message_task_id != effective_task_id
                ):
                    continue
                if note_type in TASK_SCOPED_NOTE_TYPES and message_task_id != effective_task_id:
                    continue
                selected.append(self._agent_turn_system_message(message, note_type=note_type))
                continue

            if effective_task_id and message_task_id == effective_task_id:
                if role == "user":
                    if current_user_seen:
                        continue
                    current_user_seen = True
                selected.append(message)
                continue

            if not include_previous_dialogue:
                continue

            # Previous tasks contribute normal dialogue, not executable tool protocol.
            if role == "user":
                selected.append(message)
            elif role == "assistant" and not message.get("tool_calls"):
                selected.append(message)

        protocol_safe = self._protocol_safe_messages(selected)
        current_task_user_messages = [
            message
            for message in protocol_safe
            if message.get("role") == "user"
            and str((message.get("metadata") or {}).get("task_id") or "") == effective_task_id
            and bool(effective_task_id)
        ]
        history_messages = [message for message in protocol_safe if message not in current_task_user_messages]
        history_count = len(history_messages)
        history_chars = sum(len(str(message.get("content") or "")) for message in history_messages)
        if not current_user_seen:
            protocol_safe.append(
                {
                    "role": "user",
                    "content": str(current_user_input or ""),
                    "metadata": self._task_metadata(effective_task_id or None),
                }
            )
            current_user_seen = True

        return AgentTurnSessionContext(
            messages=sanitize_unicode([self._strip_internal_fields(message) for message in protocol_safe]),
            history_message_count=history_count,
            history_chars=history_chars,
            compacted=compacted_history,
            current_user_count=1 if current_user_seen else 0,
        )

    def summarize_if_needed(self) -> bool:
        """Compatibility no-op; request-level budgeting owns compaction."""

        return False

    def compact_observation(self, observation_json: str) -> str:
        """Return a compact JSON string for LLM context."""

        try:
            observation = json.loads(observation_json)
        except json.JSONDecodeError:
            return sanitize_unicode(self._truncate_text(observation_json, MAX_TEXT_CHARS))

        compacted = self._compact_value(observation)
        return sanitize_unicode(json.dumps(sanitize_unicode(compacted), ensure_ascii=False))

    def _compact_value(self, value: Any) -> Any:
        """Recursively compact large observation values."""

        if isinstance(value, str):
            return self._truncate_text(value, MAX_TEXT_CHARS)

        if isinstance(value, list):
            if len(value) > MAX_SEARCH_MATCHES:
                return [self._compact_value(item) for item in value[:MAX_SEARCH_MATCHES]] + [
                    {"truncated": f"{len(value) - MAX_SEARCH_MATCHES} more items omitted"}
                ]
            return [self._compact_value(item) for item in value]

        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in {"stdout", "stderr"} and isinstance(item, str):
                    result[key] = self._truncate_text(item, MAX_STDIO_CHARS)
                elif key == "text" and isinstance(item, str):
                    result[key] = self._truncate_text(item, MAX_FETCH_TEXT_CHARS)
                elif key == "results" and isinstance(item, list):
                    result[key] = [self._compact_value(entry) for entry in item[:MAX_WEB_RESULTS]]
                elif key == "matches" and isinstance(item, list):
                    result[key] = self._compact_value(item)
                else:
                    result[key] = self._compact_value(item)
            return result

        return value

    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str:
        """Create a rule-based summary of older context."""

        tool_count = sum(1 for m in messages if m.get("role") == "tool")
        user_count = sum(1 for m in messages if m.get("role") == "user")
        assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
        return (
            "Context Summary:\n"
            f"- Compacted older messages: users={user_count}, assistants={assistant_count}, tools={tool_count}.\n"
            "- Older detailed tool observations were omitted. Use current TaskState, recent observations, "
            "and tools again if exact details are needed."
        )

    @staticmethod
    def _drop_leading_orphan_tool_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Avoid sending tool messages without their matching assistant call."""

        while messages and messages[0].get("role") == "tool":
            messages = messages[1:]
        return messages

    @staticmethod
    def _protocol_safe_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep complete assistant ToolCall groups and drop orphan protocol fragments."""

        result: list[dict[str, Any]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            role = message.get("role")
            if role == "tool":
                index += 1
                continue
            tool_calls = message.get("tool_calls") if role == "assistant" else None
            if not tool_calls:
                result.append(message)
                index += 1
                continue
            expected_ids = [str(call.get("id") or "") for call in tool_calls if isinstance(call, dict)]
            following = messages[index + 1 : index + 1 + len(expected_ids)]
            observed_ids = [str(item.get("tool_call_id") or "") for item in following if item.get("role") == "tool"]
            if expected_ids and len(following) == len(expected_ids) and set(observed_ids) == set(expected_ids):
                result.extend([message, *following])
                index += 1 + len(expected_ids)
                continue
            index += 1
        return result

    @staticmethod
    def _agent_turn_system_message(message: dict[str, Any], *, note_type: str) -> dict[str, Any]:
        """Remove the duplicate user request from current TaskState prompt notes."""

        if note_type != "task_state":
            return message
        content = str(message.get("content") or "")
        filtered = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("- user_goal:")
        )
        return {**message, "content": filtered}

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        """Truncate long text for model context."""

        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return f"{text[:limit]}\n... [truncated {omitted} chars]"

    @staticmethod
    def _to_plain_tool_call(tool_call: Any) -> dict[str, Any]:
        """Convert SDK tool-call objects into plain dictionaries."""

        if hasattr(tool_call, "model_dump"):
            return sanitize_unicode(tool_call.model_dump())
        if isinstance(tool_call, dict):
            return sanitize_unicode(tool_call)
        return sanitize_unicode(
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
        )

    @staticmethod
    def _strip_internal_fields(message: dict[str, Any]) -> dict[str, Any]:
        """Remove memory-only fields while preserving provider adapter metadata."""

        return {key: value for key, value in message.items() if key != "metadata"}


def _safe_path_list(paths: Any) -> list[str]:
    """Return compact path-only values for previous task follow-up context."""

    if not isinstance(paths, list):
        return []
    result: list[str] = []
    cwd = os.getcwd()
    for item in paths:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            normalized = os.path.normpath(text)
            if os.path.isabs(normalized):
                try:
                    normalized = os.path.relpath(normalized, cwd)
                except ValueError:
                    pass
            normalized = normalized.replace("\\", "/")
        except (OSError, ValueError):
            normalized = text.replace("\\", "/")
        if normalized and normalized not in result:
            result.append(normalized[:240])
    return result[:10]
