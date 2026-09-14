"""Process-local current workspace provider for tools."""

from __future__ import annotations

from pathlib import Path
import threading

from core.workspace import WorkspaceContext, WorkspaceManager


_WORKSPACE_LOCAL = threading.local()


def set_current_workspace(context: WorkspaceContext) -> None:
    """Set the current execution-thread workspace used by tools."""

    WorkspaceManager().ensure_workspace(context)
    _WORKSPACE_LOCAL.current = context


def get_current_workspace() -> WorkspaceContext:
    """Return this execution thread's workspace, or its default workspace."""

    current = getattr(_WORKSPACE_LOCAL, "current", None)
    if current is None:
        current = WorkspaceManager().get_context()
        _WORKSPACE_LOCAL.current = current
    return current


def get_database_path() -> Path:
    return get_current_workspace().database_path


def get_document_dir() -> Path:
    return get_current_workspace().document_dir


def get_vector_dir() -> Path:
    return get_current_workspace().vector_dir


def get_trace_dir() -> Path:
    return get_current_workspace().trace_dir


def get_tool_result_dir() -> Path:
    path = get_current_workspace().workspace_dir / "tool_results"
    path.mkdir(parents=True, exist_ok=True)
    return path
