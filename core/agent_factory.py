"""Shared AgentLoop construction for CLI and API entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.llm import build_default_llm
from core.loop import AgentLoop
from core.mcp_runtime_manager import get_mcp_runtime_manager
from core.memory import Memory
from core.workspace import WorkspaceContext, WorkspaceManager
from core.workspace_runtime import set_current_workspace


def get_workspace_context(user_id: str | None = None, project_id: str | None = None) -> WorkspaceContext:
    """Resolve and create a sanitized workspace context."""

    manager = WorkspaceManager()
    context = manager.get_context(user_id=user_id, project_id=project_id)
    manager.ensure_workspace(context)
    return context


def run_in_workspace(user_id: str | None = None, project_id: str | None = None) -> WorkspaceContext:
    """Set the process-local current workspace and return it."""

    context = get_workspace_context(user_id=user_id, project_id=project_id)
    set_current_workspace(context)
    return context


def build_agent_for_workspace(
    user_id: str | None = None,
    project_id: str | None = None,
    *,
    llm_factory: Callable[[], Any] = build_default_llm,
    runtime_manager_factory: Callable[[], Any] = get_mcp_runtime_manager,
) -> AgentLoop:
    """Build an AgentLoop bound to one workspace."""

    context = run_in_workspace(user_id=user_id, project_id=project_id)
    mcp_registry, mcp_runtime_status = runtime_manager_factory().snapshot()
    return AgentLoop(
        llm=llm_factory(),
        memory=Memory(),
        user_id=context.user_id,
        project_id=context.project_id,
        mcp_registry=mcp_registry,
        mcp_runtime_status=mcp_runtime_status,
    )
