"""Local status-query tool policy."""

from __future__ import annotations

from typing import Any


STATUS_QUERY_TOOLS = frozenset(
    {
        "get_rag_status",
        "get_sandbox_status",
        "get_workspace_status",
        "get_usage_status",
        "get_cache_status",
        "get_context_status",
        "get_browser_status",
        "get_embedding_status",
    }
)


def is_status_query_tool(tool_name: str) -> bool:
    """Return True for known local read-only status tools."""

    return _base_tool_name(tool_name) in STATUS_QUERY_TOOLS


def format_status_tool_success_message(tool_name: str, observation: dict[str, Any]) -> str:
    """Format a short user-facing status-tool success summary."""

    base = _base_tool_name(tool_name)
    return f"已完成状态查询：{base}。"


def _base_tool_name(tool_name: str) -> str:
    return str(tool_name or "").rsplit(".", 1)[-1]
