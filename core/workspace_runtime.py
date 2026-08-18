"""Process-local current workspace provider for tools."""

from __future__ import annotations

from pathlib import Path

from core.workspace import WorkspaceContext, WorkspaceManager


_CURRENT_WORKSPACE: WorkspaceContext | None = None


def set_current_workspace(context: WorkspaceContext) -> None:
    """Set the current process workspace used by tools."""

    global _CURRENT_WORKSPACE
    WorkspaceManager().ensure_workspace(context)
    _CURRENT_WORKSPACE = context


def get_current_workspace() -> WorkspaceContext:
    """Return the current workspace, falling back to default workspace."""

    global _CURRENT_WORKSPACE
    if _CURRENT_WORKSPACE is None:
        _CURRENT_WORKSPACE = WorkspaceManager().get_context()
    return _CURRENT_WORKSPACE


def get_memory_dir() -> Path:
    return get_current_workspace().memory_dir


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
