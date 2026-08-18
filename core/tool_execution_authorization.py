"""Tool execution authorization for side-effect tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Iterable
from typing import Any

from core.agent_access_policy import evaluate_agent_tool_access
from core.tool_call_grants import ToolCallGrantDecision, check_tool_call_grant, get_tool_call_grant
from tools.registry import get_tool_spec


PLAN_CAPABILITY_NAMES = frozenset(
    {
        "file_write",
        "coding",
        "shell_sandbox",
        "python_sandbox",
        "validation",
        "git",
        "browser",
    }
)
EXECUTION_CAPABILITY_ALIASES = {
    "command_exec": "shell_sandbox",
    "execution": "shell_sandbox",
    "sandbox": "shell_sandbox",
}


@dataclass(frozen=True)
class ToolExecutionAuthorizationDecision:
    allowed: bool
    code: str
    reason: str
    tool_name: str
    required_capabilities: tuple[str, ...] = ()
    grant_checked: bool = False
    grant_reason: str = ""
    call_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def authorize_tool_execution(
    *,
    task_state: Any,
    tool_name: str,
    tool_call_envelope: Any | None = None,
    sanitized_arguments: dict[str, Any] | None = None,
) -> ToolExecutionAuthorizationDecision:
    """Authorize one proposed tool call before any tool dispatch."""

    base_tool = base_tool_name(tool_name)
    tool_access = evaluate_agent_tool_access(get_tool_spec(base_tool))
    if not tool_access.allowed:
        return ToolExecutionAuthorizationDecision(
            False,
            tool_access.code,
            tool_access.reason,
            tool_name,
            _required_capabilities(base_tool),
        )

    grant_decision = _per_call_grant_decision(
        task_state=task_state,
        tool_name=tool_name,
        tool_call_envelope=tool_call_envelope,
        sanitized_arguments=sanitized_arguments,
    )
    grant_required = bool(getattr(tool_call_envelope, "metadata", {}).get("execution_grant_required"))
    if grant_decision is not None:
        if grant_decision.allowed:
            return ToolExecutionAuthorizationDecision(
                True,
                "ok",
                "authorized_by_task_scoped_tool_call_grant",
                tool_name,
                grant_checked=True,
                grant_reason=grant_decision.reason,
                call_id=grant_decision.call_id,
            )
        if grant_required or get_tool_call_grant(task_state, grant_decision.call_id) is not None:
            return ToolExecutionAuthorizationDecision(
                False,
                "tool_execution_not_authorized",
                grant_decision.reason,
                tool_name,
                grant_checked=True,
                grant_reason=grant_decision.reason,
                call_id=grant_decision.call_id,
            )
    elif grant_required:
        call_id = str(getattr(tool_call_envelope, "provider_call_id", "") or getattr(tool_call_envelope, "call_id", "") or "")
        return ToolExecutionAuthorizationDecision(
            False,
            "tool_execution_not_authorized",
            "tool_call_grant_missing",
            tool_name,
            grant_checked=True,
            grant_reason="tool_call_grant_missing",
            call_id=call_id,
        )

    return ToolExecutionAuthorizationDecision(
        True,
        "ok",
        "authorized_by_deterministic_execution_safety",
        tool_name,
        _required_capabilities(base_tool),
    )


def tool_execution_authorization_guard(
    *,
    task_state: Any,
    tool_name: str,
    tool_call_envelope: Any | None = None,
    sanitized_arguments: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    decision = authorize_tool_execution(
        task_state=task_state,
        tool_name=tool_name,
        tool_call_envelope=tool_call_envelope,
        sanitized_arguments=sanitized_arguments,
    )
    if decision.allowed:
        return None
    return {
        "success": False,
        "error": "当前工具未通过结构化 ToolCall grant 或执行安全授权。",
        "data": {
            "code": decision.code,
            "reason": decision.reason,
            "blocked_tool": tool_name,
            "required_capabilities": list(decision.required_capabilities),
            "grant_checked": decision.grant_checked,
            "grant_reason": decision.grant_reason,
            "call_id": decision.call_id,
        },
    }


def base_tool_name(tool_name: str) -> str:
    return str(tool_name or "").strip().split(".")[-1]


def _required_capabilities(tool_name: str) -> tuple[str, ...]:
    spec = get_tool_spec(tool_name)
    if spec is None:
        return ()
    return tuple(
        _dedupe(
            normalize_execution_capability(capability)
            for capability in spec.capabilities
            if normalize_execution_capability(capability) in PLAN_CAPABILITY_NAMES
        )
    )


def normalize_execution_capability(capability: str) -> str:
    """Normalize execution capability aliases at the authorization boundary."""

    value = str(capability or "").strip()
    return EXECUTION_CAPABILITY_ALIASES.get(value, value)


def _per_call_grant_decision(
    *,
    task_state: Any,
    tool_name: str,
    tool_call_envelope: Any | None,
    sanitized_arguments: dict[str, Any] | None,
) -> ToolCallGrantDecision | None:
    if tool_call_envelope is None:
        return None
    call_id = str(
        getattr(tool_call_envelope, "provider_call_id", "")
        or getattr(tool_call_envelope, "call_id", "")
        or ""
    ).strip()
    if not call_id:
        return None
    canonical = str(getattr(tool_call_envelope, "canonical_name", "") or tool_name).strip()
    executable = str(getattr(tool_call_envelope, "executable_name", "") or tool_name).strip()
    return check_tool_call_grant(
        task_state,
        call_id=call_id,
        canonical_name=canonical,
        executable_name=executable,
        parsed_arguments=dict(
            getattr(tool_call_envelope, "metadata", {}).get("grant_registered_arguments")
            or getattr(tool_call_envelope, "parsed_arguments", {})
            or {}
        ),
        sanitized_arguments=dict(sanitized_arguments or getattr(tool_call_envelope, "parsed_arguments", {}) or {}),
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
