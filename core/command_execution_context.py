"""Task-scoped host command execution context."""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from core.agent_access_policy import get_agent_access_mode
from core.path_grounding import build_path_context


def _resolved_path(value: str | Path) -> Path:
    expanded = os.path.expandvars(
        str(value).replace("%USERPROFILE%", os.getenv("USERPROFILE") or str(Path.home()))
    )
    return Path(expanded).expanduser().resolve(strict=False)


@dataclass(frozen=True)
class HostCommandExecutionContext:
    project_root: Path
    session_directory: Path
    sandbox_dir: Path
    access_mode: str
    runtime_lane: str
    task_id: str
    source: str


_HOST_COMMAND_CONTEXT: ContextVar[HostCommandExecutionContext | None] = ContextVar(
    "horizon_host_command_context",
    default=None,
)


def get_host_command_context() -> HostCommandExecutionContext:
    current = _HOST_COMMAND_CONTEXT.get()
    if current is not None:
        return current
    path_context = build_path_context(runtime_lane="command_exec")
    return HostCommandExecutionContext(
        project_root=_resolved_path(path_context.project_root),
        session_directory=_resolved_path(path_context.project_root),
        sandbox_dir=_resolved_path(path_context.sandbox_dir),
        access_mode=get_agent_access_mode(),
        runtime_lane=str(path_context.runtime_lane or "command_exec"),
        task_id="",
        source="build_path_context",
    )


def bind_host_command_context(
    *,
    project_root: str | Path,
    session_directory: str | Path | None = None,
    sandbox_dir: str | Path,
    access_mode: str,
    runtime_lane: str = "",
    task_id: str = "",
    source: str = "agent_loop",
) -> Token[HostCommandExecutionContext | None]:
    project = _resolved_path(project_root)
    context = HostCommandExecutionContext(
        project_root=project,
        session_directory=_resolved_path(session_directory or project),
        sandbox_dir=_resolved_path(sandbox_dir),
        access_mode=get_agent_access_mode(access_mode),
        runtime_lane=str(runtime_lane or ""),
        task_id=str(task_id or ""),
        source=str(source or "agent_loop"),
    )
    return _HOST_COMMAND_CONTEXT.set(context)


@contextmanager
def host_command_context(**kwargs: object) -> Iterator[HostCommandExecutionContext]:
    token = bind_host_command_context(**kwargs)  # type: ignore[arg-type]
    try:
        yield get_host_command_context()
    finally:
        _HOST_COMMAND_CONTEXT.reset(token)


__all__ = [
    "HostCommandExecutionContext",
    "bind_host_command_context",
    "get_host_command_context",
    "host_command_context",
]
