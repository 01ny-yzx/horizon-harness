"""Shared AgentLoop construction for CLI and API entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.llm import build_default_llm
from core.loop import AgentLoop
from core.mcp_runtime_manager import get_mcp_runtime_manager
from core.memory import Memory
from core.path_grounding import build_path_context
from core.session import SessionInfo, SessionService
from core.workspace import WorkspaceContext, WorkspaceManager
from core.workspace_runtime import set_current_workspace


def get_workspace_context(user_id: str | None = None, project_id: str | None = None) -> WorkspaceContext:
    """Resolve and create a sanitized workspace context."""

    manager = WorkspaceManager()
    context = manager.get_context(user_id=user_id, project_id=project_id)
    manager.ensure_workspace(context)
    return context


def run_in_workspace(user_id: str | None = None, project_id: str | None = None) -> WorkspaceContext:
    """Set the execution-thread current workspace and return it."""

    context = get_workspace_context(user_id=user_id, project_id=project_id)
    set_current_workspace(context)
    return context


def build_agent_for_workspace(
    user_id: str | None = None,
    project_id: str | None = None,
    *,
    session_id: str | None = None,
    llm_factory: Callable[[], Any] = build_default_llm,
    runtime_manager_factory: Callable[[], Any] = get_mcp_runtime_manager,
) -> AgentLoop:
    """Build an AgentLoop with one durable Session identity."""

    _, session = create_session_for_workspace(
        user_id,
        project_id,
        session_id=session_id,
    )
    return build_agent_for_session(
        session,
        llm_factory=llm_factory,
        runtime_manager_factory=runtime_manager_factory,
    )


def create_session_for_workspace(
    user_id: str | None = None,
    project_id: str | None = None,
    *,
    session_id: str | None = None,
) -> tuple[WorkspaceContext, SessionInfo]:
    """Create or adopt one durable Session without constructing an Agent runtime."""

    context = run_in_workspace(user_id=user_id, project_id=project_id)
    path_context = build_path_context(
        user_id=context.user_id,
        project_id=context.project_id,
    )
    session = SessionService(database_path=context.database_path).create(
        session_id=session_id,
        user_id=context.user_id,
        project_id=context.project_id,
        workspace_id=context.workspace_id,
        directory=path_context.project_root,
    )
    return context, session


def build_agent_for_session(
    session: SessionInfo,
    *,
    llm_factory: Callable[[], Any] = build_default_llm,
    runtime_manager_factory: Callable[[], Any] = get_mcp_runtime_manager,
) -> AgentLoop:
    """Resolve one ephemeral Agent runtime from durable Session identity."""

    context = run_in_workspace(session.user_id, session.project_id)
    if context.workspace_id != session.workspace_id:
        raise ValueError("Resolved workspace does not match durable Session identity")
    mcp_registry, mcp_runtime_status = runtime_manager_factory().snapshot()
    return AgentLoop(
        llm=llm_factory(),
        memory=Memory(),
        session_id=session.id,
        user_id=session.user_id,
        project_id=session.project_id,
        mcp_registry=mcp_registry,
        mcp_runtime_status=mcp_runtime_status,
    )


def build_session_runner(
    session: SessionInfo,
    *,
    llm_factory: Callable[[], Any] = build_default_llm,
    runtime_manager_factory: Callable[[], Any] = get_mcp_runtime_manager,
):
    """Build a fresh runner from durable Session identity for one drain."""

    from core.session_runner import SessionRunner

    agent = build_agent_for_session(
        session,
        llm_factory=llm_factory,
        runtime_manager_factory=runtime_manager_factory,
    )
    if agent.session_store is None:
        raise RuntimeError("Session runtime did not bind durable Session storage")
    return SessionRunner(
        agent._run_session_work_item,
        database=agent.session_store.database,
    )
