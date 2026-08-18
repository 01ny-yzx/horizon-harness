"""Deterministic user-facing formatting for memory operation results."""

from __future__ import annotations

from typing import Any


def format_memory_delete_result(result: dict[str, Any]) -> str:
    """Format a memory deletion result without exposing raw tool payloads."""

    if not result.get("success"):
        error = str(result.get("error") or "未知错误").strip()
        return f"删除失败，原因是：{error[:160]}"
    data = result.get("data", {})
    if not isinstance(data, dict):
        return "删除失败，原因是：工具返回格式不完整。"
    deleted = int(data.get("deleted") or 0)
    if deleted <= 0:
        return "我没有找到匹配的长期记忆，因此没有删除任何内容。你可以先让我查看当前长期记忆。"

    parts = ["已删除匹配的长期记忆。"]
    details: list[str] = []
    preferences = data.get("deleted_preferences")
    if isinstance(preferences, list) and preferences:
        details.append("用户偏好：" + ", ".join(str(item) for item in preferences[:8]))
    stable_facts = int(data.get("deleted_stable_facts") or 0)
    projects = int(data.get("deleted_projects") or 0)
    tasks = int(data.get("deleted_tasks") or 0)
    if stable_facts:
        details.append(f"稳定事实：{stable_facts} 条")
    if projects:
        details.append(f"项目记忆：{projects} 条")
    if tasks:
        details.append(f"相关任务历史：{tasks} 条")
    if details:
        parts.append("删除内容：" + "；".join(details))
    return "\n".join(parts)


__all__ = ["format_memory_delete_result"]
