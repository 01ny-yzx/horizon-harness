"""Tools for safe JSON-backed long-term memory."""

from __future__ import annotations

from typing import Any

from core.persistent_memory import PersistentMemory
from core.workspace_runtime import get_memory_dir


def _memory() -> PersistentMemory:
    return PersistentMemory(memory_dir=get_memory_dir())


def remember_user_preference(key: str, value: str) -> dict[str, Any]:
    """Save a durable user preference."""

    memory = _memory()
    return memory.add_user_preference(key=key, value=value, source="tool")


def remember_stable_fact(content: str) -> dict[str, Any]:
    """Save a durable stable fact."""

    memory = _memory()
    return memory.add_stable_fact(content=content, source="tool")


def remember_project_summary(
    project_path: str,
    summary: str,
    tech_stack: list[str] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Save a durable project summary."""

    memory = _memory()
    return memory.add_project_summary(
        project_path=project_path,
        summary=summary,
        tech_stack=tech_stack,
        status=status,
        source="tool",
    )


def list_memories(memory_type: str = "all") -> dict[str, Any]:
    """List current long-term memories."""

    memory = _memory()
    memory.load_all()
    normalized = (memory_type or "all").lower()
    data: dict[str, Any] = {}
    if normalized in {"all", "user"}:
        data["user"] = memory.get_user_memory()
    if normalized in {"all", "project"}:
        data["project"] = memory.get_project_memory()
    if normalized in {"all", "tasks"}:
        data["tasks"] = memory.get_recent_tasks(limit=20)
    if not data:
        return {"success": False, "error": "memory_type must be one of: all, user, project, tasks."}
    return {"success": True, "data": data}


def forget_memory(memory_type: str, keyword: str) -> dict[str, Any]:
    """Forget matching memory entries by keyword."""

    normalized_keyword = str(keyword or "").strip()
    if not normalized_keyword:
        return {"success": False, "error": "keyword is required."}
    memory = _memory()
    return memory.forget_memory(
        memory_type=memory_type,
        keyword=normalized_keyword,
    )


def clear_memory_type(memory_type: str) -> dict[str, Any]:
    """Clear one allowed long-term memory category."""

    memory = _memory()
    return memory.clear_memory_type(memory_type)


MEMORY_TOOLS = {
    "remember_user_preference": remember_user_preference,
    "remember_stable_fact": remember_stable_fact,
    "remember_project_summary": remember_project_summary,
    "list_memories": list_memories,
    "forget_memory": forget_memory,
    "clear_memory_type": clear_memory_type,
}


MEMORY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "remember_user_preference",
            "description": "Save a durable, non-sensitive user preference explicitly requested by the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short preference name, such as answer_language or answer_style."},
                    "value": {"type": "string", "description": "Preference value to remember."},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_stable_fact",
            "description": "Save a durable, non-sensitive stable fact or background provided by the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Stable fact to remember. Do not include secrets or temporary details."}
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_project_summary",
            "description": "Save a concise non-sensitive summary for a local project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Project path or identifier."},
                    "summary": {"type": "string", "description": "Concise project summary."},
                    "tech_stack": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional technology stack list.",
                    },
                    "status": {"type": "string", "description": "Optional project status."},
                },
                "required": ["project_path", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "List long-term memories. Use when the user asks what is remembered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": ["all", "user", "project", "tasks"],
                        "description": "Memory category to list.",
                        "default": "all",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": "Delete long-term memories matching a keyword. Use when the user asks to forget something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": ["all", "user", "user_preference", "stable_fact", "project", "project_summary", "tasks", "task"],
                        "description": "Memory category to delete from.",
                    },
                    "keyword": {"type": "string", "description": "Keyword used to match memory entries for deletion."},
                },
                "required": ["memory_type", "keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_memory_type",
            "description": "Clear one allowed long-term memory category without deleting memory_store.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": ["user_preferences", "stable_facts", "task_history", "project_memory"],
                        "description": "Memory category to clear.",
                    }
                },
                "required": ["memory_type"],
            },
        },
    },
]
