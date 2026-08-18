"""Tools for inspecting and resetting local usage counters."""

from __future__ import annotations

from typing import Any

from config.settings import settings
from core.usage import UsageTracker
from core.workspace_runtime import get_current_workspace


def get_usage_status(user_id: str | None = None) -> dict[str, Any]:
    """Return today's usage for the current or provided user."""

    context = get_current_workspace()
    safe_user = user_id or context.user_id
    usage = UsageTracker().get_usage(safe_user)
    return {
        "success": True,
        "data": {
            "user_id": safe_user,
            "project_id": context.project_id,
            "usage": usage,
            "limits": {
                "chat": settings.daily_chat_limit,
                "rag": settings.daily_rag_limit,
                "web_search": settings.daily_web_search_limit,
                "embedding": settings.daily_embedding_limit,
                "document_load": settings.daily_document_load_limit,
            },
        },
    }


def clear_usage(date: str | None = None) -> dict[str, Any]:
    """Clear usage counters for one day. Intended for local development."""

    return UsageTracker().reset_day(date=date)


USAGE_TOOLS = {
    "get_usage_status": get_usage_status,
    "clear_usage": clear_usage,
}


USAGE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_usage_status",
            "description": "Return today's local usage counters and limits for the current user.",
            "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_usage",
            "description": "Clear local usage counters for a day. Local development only.",
            "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": []},
        },
    },
]
