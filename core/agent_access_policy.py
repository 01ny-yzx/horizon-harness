"""Agent-wide access mode boundary for local side effects."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


AGENT_ACCESS_MODES = {"read_only", "full_access"}
FileAccessOperation = Literal["read", "write"]


@dataclass(frozen=True)
class AgentAccessDecision:
    allowed: bool
    access_mode: str
    operation: str
    requested_path: str = ""
    resolved_path: str | None = None
    code: str = "ok"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentToolAccessDecision:
    allowed: bool
    access_mode: str
    code: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentPermissionDecision:
    allowed: bool
    permission: str
    pattern: str
    action: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_agent_access_mode(value: str | None = None) -> str:
    raw = os.getenv("AGENT_ACCESS_MODE", "read_only") if value is None else value
    normalized = str(raw or "read_only").strip().lower()
    return normalized if normalized in AGENT_ACCESS_MODES else "read_only"


def is_read_only_access(value: str | None = None) -> bool:
    return get_agent_access_mode(value) == "read_only"


def is_full_access(value: str | None = None) -> bool:
    return get_agent_access_mode(value) == "full_access"


def evaluate_agent_tool_access(
    tool_spec: Any | None,
    *,
    access_mode: str | None = None,
) -> AgentToolAccessDecision:
    """Evaluate only the agent-wide access boundary for one registered tool."""

    mode = get_agent_access_mode(access_mode)
    if tool_spec is None:
        return AgentToolAccessDecision(
            False,
            mode,
            "agent_tool_spec_missing",
            "ToolSpec is required before a tool can enter the Agent tool surface.",
        )
    if mode == "read_only" and bool(getattr(tool_spec, "side_effect", False)):
        return AgentToolAccessDecision(
            False,
            mode,
            "agent_access_mode_read_only",
            "agent_access_mode_read_only",
        )
    return AgentToolAccessDecision(
        True,
        mode,
        "ok",
        "The access-mode layer allows this tool to enter later capability and safety filters.",
    )


def evaluate_agent_permission(
    task_state: Any,
    *,
    permission: str,
    pattern: str,
) -> AgentPermissionDecision:
    """Evaluate one explicit runtime permission without choosing a tool.

    Horizon currently has no interactive permission prompt. Callers may provide
    deterministic per-task decisions in ``metadata["permission_decisions"]``;
    an absent decision fails closed.
    """

    metadata = getattr(task_state, "metadata", None)
    decisions = metadata.get("permission_decisions") if isinstance(metadata, dict) else None
    configured: Any = decisions.get(permission) if isinstance(decisions, dict) else None
    if isinstance(configured, dict):
        action = str(configured.get(pattern) or configured.get("*") or "deny").strip().lower()
    else:
        action = str(configured or "deny").strip().lower()
    if action not in {"allow", "deny"}:
        action = "deny"
    return AgentPermissionDecision(
        allowed=action == "allow",
        permission=str(permission or ""),
        pattern=str(pattern or ""),
        action=action,
        reason=(
            "explicit_permission_allow"
            if action == "allow"
            else "permission_denied_or_interactive_ask_unavailable"
        ),
    )


def evaluate_agent_file_access(
    path: str | Path,
    *,
    operation: FileAccessOperation,
    raw_requested_path: str | None = None,
    project_root: Path | None = None,
) -> AgentAccessDecision:
    """Evaluate only the agent-wide access boundary for a local file path."""

    requested = str(path or "").strip()
    mode = get_agent_access_mode()
    if operation == "write" and mode == "read_only":
        return AgentAccessDecision(
            False,
            mode,
            operation,
            requested,
            code="agent_access_mode_read_only",
            reason="当前 Agent 权限模式为 read_only，不允许写入、修改或删除文件。",
        )

    if not requested:
        return AgentAccessDecision(False, mode, operation, requested, code="path_empty", reason="目标路径不能为空。")
    from config.settings import PROJECT_ROOT
    from core.path_grounding import resolve_host_path

    root = (project_root or PROJECT_ROOT).expanduser().resolve(strict=False)
    resolved = resolve_host_path(requested, project_root=root)
    return AgentAccessDecision(True, mode, operation, requested, resolved_path=resolved)
