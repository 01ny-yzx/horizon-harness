"""Deterministic resolution for tool observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.structured_intent_access import structured_file_output_validation_required
from core.status_tool_policy import format_status_tool_success_message, is_status_query_tool
from core.tool_execution_authorization import base_tool_name
from core.tool_observation import is_recoverable_observation


ToolOutcomeKind = Literal[
    "allow_continue",
    "terminal_policy_blocked",
    "terminal_failure",
    "terminal_success",
]
FailureDisposition = Literal[
    "none",
    "recoverable",
    "ordinary_failure",
    "policy_blocked",
    "no_progress",
]

POLICY_BLOCK_CODES = {
    "blocked_dns_private_ip",
    "blocked_localhost",
    "blocked_private_ip",
    "blocked_scheme",
    "browser_mcp_write_not_allowed",
    "browser_blocked_file_url",
    "browser_blocked_localhost",
    "browser_blocked_private_ip",
    "database_query_not_read_only",
    "database_readonly_blocked",
    "database_write_blocked",
    "database_write_tool_not_allowed",
    "destructive_action_blocked",
    "execution_payload_git_mutation_not_authorized",
    "file_access_denied",
    "file_url_blocked",
    "file_write_blocked",
    "agent_self_protected_path_blocked",
    "home_sensitive_path_blocked",
    "mcp_tool_blocked_by_access_mode",
    "mcp_tool_risk_not_authorized",
    "path_not_allowed",
    "path_outside_allowed_roots",
    "path_traversal_blocked",
    "permission_bypass_blocked",
    "raw_user_tool_text_not_executable",
    "sensitive_file_blocked",
    "sensitive_file_write_blocked",
    "system_path_blocked",
    "system_protected_path_blocked",
    "tool_execution_not_authorized",
    "tool_risk_metadata_missing",
    "unsupported_scheme",
    "url_tool_policy_blocked",
}
POLICY_BLOCK_REASONS = {
    "blocked_scheme",
    "database_write",
    "dangerous_sql",
    "file_url",
    "localhost_or_private_url",
    "private_or_local_ip",
    "unsupported_scheme",
}
EXECUTION_TOOL_NAMES = {
    # Legacy names are historical observation compatibility only, not Agent-visible tools.
    "sandbox_exec",
    "run_command",
    "run_shell_in_sandbox",
    "run_python_code",
    "run_python_file",
    "run_python_in_sandbox",
    "run_python_file_in_sandbox",
}
@dataclass(frozen=True)
class FailureDispositionDecision:
    disposition: FailureDisposition
    error_code: str = ""
    policy_code: str = ""
    authoritative_target: bool = False
    target_path: str = ""
    target_key: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolOutcomeResolution:
    kind: ToolOutcomeKind
    reason: str
    tool: str = ""
    status: str = ""
    policy_code: str = ""
    failure_disposition: FailureDisposition = "none"
    user_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_failure_disposition(
    *,
    task_state: Any,
    tool_name: str,
    arguments: dict[str, Any] | None,
    observation: dict[str, Any],
) -> FailureDispositionDecision:
    """Classify one failed observation using structured runtime facts only."""

    arguments = arguments if isinstance(arguments, dict) else {}
    observation = observation if isinstance(observation, dict) else {}
    status = str(observation.get("status") or "").strip().lower()
    error_code = _observation_error_code(observation)
    policy_code = _policy_block_code(task_state, observation)
    target_path = _normalized_failure_target_path(arguments, observation)
    target_key = f"local_path:{target_path}" if target_path else ""
    authoritative_target = _is_authoritative_document_target(task_state, target_path)

    if status == "blocked" or policy_code:
        return FailureDispositionDecision(
            "policy_blocked",
            error_code=error_code,
            policy_code=policy_code or error_code or "tool_call_blocked",
            authoritative_target=authoritative_target,
            target_path=target_path,
            target_key=target_key,
            reason="structured_policy_block",
        )
    if is_recoverable_observation(observation):
        return FailureDispositionDecision(
            "recoverable",
            error_code=error_code,
            authoritative_target=authoritative_target,
            target_path=target_path,
            target_key=target_key,
            reason="structured_recoverable_observation",
        )
    failed = (
        status in {"failed", "error"}
        or observation.get("success") is False
    )
    return FailureDispositionDecision(
        "ordinary_failure" if failed else "none",
        error_code=error_code,
        authoritative_target=authoritative_target,
        target_path=target_path,
        target_key=target_key,
        reason="ordinary_tool_error" if failed else "observation_not_failed",
    )


def resolve_tool_outcome(
    *,
    task_state: Any,
    tool_name: str,
    arguments: dict[str, Any] | None,
    observation: dict[str, Any],
) -> ToolOutcomeResolution:
    """Resolve one tool result into a deterministic next action."""

    arguments = arguments if isinstance(arguments, dict) else {}
    status = str(observation.get("status") or "").strip().lower()
    if status == "skipped":
        return ToolOutcomeResolution("allow_continue", "tool_call_skipped", tool=tool_name, status=status)
    if status == "blocked":
        if _premature_validation_tool_before_file_output(task_state, tool_name):
            return ToolOutcomeResolution(
                "allow_continue",
                "premature_validation_tool_before_file_output",
                tool=tool_name,
                status=status,
                metadata={
                    "arguments": dict(arguments),
                    "observation": observation,
                    "file_output_completed": bool(getattr(task_state, "file_output_completed", False)),
                },
            )
        disposition = resolve_failure_disposition(
            task_state=task_state,
            tool_name=tool_name,
            arguments=arguments,
            observation=observation,
        )
        policy_code = disposition.policy_code or "tool_call_blocked"
        return ToolOutcomeResolution(
            "terminal_policy_blocked",
            "policy_blocked",
            tool=tool_name,
            status=status,
            policy_code=policy_code,
            failure_disposition="policy_blocked",
            metadata={
                "attempted_tools": sorted(_attempted_tools(task_state)),
                "arguments": dict(arguments),
                "observation": observation,
                **_policy_block_metadata(observation),
                "failure_disposition_decision": disposition.to_dict(),
            },
        )
    if observation.get("success") is True:
        if _file_output_success_validation_pending(task_state, tool_name):
            return ToolOutcomeResolution(
                "allow_continue",
                "file_output_success_validation_pending",
                tool=tool_name,
                status=status or "success",
                metadata={"arguments": dict(arguments), "observation": observation},
            )
        if is_status_query_tool(tool_name):
            return ToolOutcomeResolution(
                "terminal_success",
                "status_tool_success",
                tool=tool_name,
                status=status or "success",
                user_message=format_status_tool_success_message(tool_name, observation),
                metadata={"arguments": dict(arguments), "observation": observation},
            )
        if _is_task_complete_after_tool_success(task_state, tool_name, observation):
            return ToolOutcomeResolution(
                "terminal_success",
                "task_complete_after_tool_success",
                tool=tool_name,
                status=status or "success",
                metadata={"arguments": dict(arguments), "observation": observation},
            )
        if _has_success_evidence(task_state, tool_name, observation):
            return ToolOutcomeResolution(
                "allow_continue",
                "tool_success_requires_llm_summary",
                tool=tool_name,
                status=status or "success",
                metadata={"arguments": dict(arguments), "observation": observation},
            )
        return ToolOutcomeResolution(
            "allow_continue",
            "tool_success_without_terminal_evidence",
            tool=tool_name,
            status=status or "success",
            metadata={"arguments": dict(arguments), "observation": observation},
        )

    disposition = resolve_failure_disposition(
        task_state=task_state,
        tool_name=tool_name,
        arguments=arguments,
        observation=observation,
    )
    if disposition.disposition == "policy_blocked":
        return ToolOutcomeResolution(
            "terminal_policy_blocked",
            "policy_blocked",
            tool=tool_name,
            status=status or "blocked",
            policy_code=disposition.policy_code,
            failure_disposition="policy_blocked",
            metadata={
                "attempted_tools": sorted(_attempted_tools(task_state)),
                "arguments": dict(arguments),
                "observation": observation,
                **_policy_block_metadata(observation),
                "failure_disposition_decision": disposition.to_dict(),
            },
        )

    if disposition.disposition == "recoverable":
        data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
        return ToolOutcomeResolution(
            "allow_continue",
            "recoverable_tool_observation_returns_control_to_assistant",
            tool=tool_name,
            status=status or "failed",
            failure_disposition="recoverable",
            metadata={
                "error_code": str(observation.get("error_code") or data.get("error_code") or ""),
                "recovery_reason": str(
                    observation.get("recovery_reason") or data.get("recovery_reason") or ""
                ),
                "arguments": dict(arguments),
                "observation": observation,
                "failure_disposition_decision": disposition.to_dict(),
            },
        )

    return ToolOutcomeResolution(
        "allow_continue",
        "ordinary_tool_error_returns_control_to_assistant",
        tool=tool_name,
        status=status or "failed",
        failure_disposition="ordinary_failure",
        metadata={
            "arguments": dict(arguments),
            "observation": observation,
            "failure_disposition_decision": disposition.to_dict(),
        },
    )


def _has_success_evidence(task_state: Any, tool_name: str, observation: dict[str, Any]) -> bool:
    base_tool = base_tool_name(tool_name)
    if base_tool in {"write_file", "replace_in_file"}:
        return True
    if base_tool in {
        "read_file",
        "read_document",
        "load_document",
        "search_document_chunks",
        "rag_query",
        "database_select_query",
        "database_list_tables",
        "database_describe_table",
        "fetch_url",
        "browser_extract_text",
        "browser_list_links",
        "browser_screenshot",
    }:
        return True
    return False


def _is_task_complete_after_tool_success(task_state: Any, tool_name: str, observation: dict[str, Any]) -> bool:
    if _coding_completion_ready(task_state):
        return True
    base_tool = base_tool_name(tool_name)
    if base_tool not in {"write_file", "replace_in_file"}:
        return False
    profile = getattr(task_state, "task_profile", None)
    if not profile:
        return False
    if not bool(getattr(profile, "needs_file_output", False)):
        return False
    if bool(getattr(profile, "is_coding_task", False)):
        return False
    if not bool(getattr(task_state, "file_output_completed", False)):
        return False
    result = getattr(task_state, "file_output_result", None)
    if not isinstance(result, dict):
        return False
    return bool(result.get("path") or result.get("artifact_id") or result.get("download_url"))


def _file_output_success_validation_pending(task_state: Any, tool_name: str) -> bool:
    base_tool = base_tool_name(tool_name)
    if base_tool not in {"write_file", "replace_in_file"}:
        return False
    if not structured_file_output_validation_required(task_state):
        return False
    if getattr(task_state, "validation_results", None):
        return False
    return not _latest_validation_passed(task_state)


def _premature_validation_tool_before_file_output(task_state: Any, tool_name: str) -> bool:
    if base_tool_name(tool_name) not in EXECUTION_TOOL_NAMES:
        return False
    return bool(structured_file_output_validation_required(task_state) and not getattr(task_state, "file_output_completed", False))


def _coding_completion_ready(task_state: Any) -> bool:
    ready = getattr(task_state, "coding_completion_ready", None)
    if callable(ready):
        return bool(ready())
    if str(getattr(task_state, "task_type", "") or "") != "coding":
        return False
    if not getattr(task_state, "modified_files", None):
        return False
    if not _latest_validation_passed(task_state):
        return False
    if str(getattr(task_state, "git_repo_root", "") or "") and not bool(getattr(task_state, "reviewed_diff", False)):
        return False
    return True


def _latest_validation_passed(task_state: Any) -> bool:
    latest = getattr(task_state, "latest_validation_passed", None)
    if callable(latest):
        return bool(latest())
    results = getattr(task_state, "validation_results", []) or []
    if not results:
        return False
    latest_result = results[-1]
    return isinstance(latest_result, dict) and latest_result.get("success") is True


def _policy_block_code(task_state: Any, observation: dict[str, Any]) -> str:
    data = observation.get("data", {})
    data = data if isinstance(data, dict) else {}
    explicit_policy_code = str(observation.get("policy_code") or data.get("policy_code") or "").strip()
    code = str(data.get("code") or data.get("error_code") or observation.get("error_code") or "").strip()
    reason = str(data.get("reason") or data.get("block_reason") or "")
    if explicit_policy_code:
        return explicit_policy_code
    if code in POLICY_BLOCK_CODES or reason in POLICY_BLOCK_REASONS:
        return code or reason
    return ""


def _observation_error_code(observation: dict[str, Any]) -> str:
    data = observation.get("data")
    data = data if isinstance(data, dict) else {}
    error = observation.get("error")
    error = error if isinstance(error, dict) else {}
    return str(
        observation.get("error_code")
        or data.get("error_code")
        or data.get("code")
        or error.get("code")
        or ""
    ).strip()


def _normalized_failure_target_path(
    arguments: dict[str, Any],
    observation: dict[str, Any],
) -> str:
    data = observation.get("data")
    data = data if isinstance(data, dict) else {}
    metadata = observation.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    grounding = metadata.get("path_grounding")
    grounding = grounding if isinstance(grounding, dict) else {}
    data_grounding = data.get("path_grounding")
    data_grounding = data_grounding if isinstance(data_grounding, dict) else {}
    candidates = (
        grounding.get("resolved_path"),
        data_grounding.get("resolved_path"),
        metadata.get("resolved_path"),
        metadata.get("path"),
        data.get("resolved_path"),
        data.get("path"),
        observation.get("resolved_path"),
        observation.get("path"),
        arguments.get("resolved_path"),
        arguments.get("path"),
        arguments.get("file_path"),
    )
    raw = next((str(item).strip() for item in candidates if str(item or "").strip()), "")
    return _normalize_local_path(raw)


def _normalize_local_path(value: str) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return ""


def _is_authoritative_document_target(task_state: Any, target_path: str) -> bool:
    if not target_path:
        return False
    profile = getattr(task_state, "task_profile", None)
    document_paths = getattr(profile, "document_paths", None) if profile is not None else None
    if not isinstance(document_paths, (list, tuple)):
        return False
    normalized_targets = {
        normalized
        for item in document_paths
        if (normalized := _normalize_local_path(str(item or "").strip()))
    }
    return target_path in normalized_targets


def _policy_block_metadata(observation: dict[str, Any]) -> dict[str, Any]:
    data = observation.get("data", {})
    data = data if isinstance(data, dict) else {}
    result: dict[str, Any] = {}
    for source, target in (
        ("blocked_tool", "blocked_tool"),
        ("reason", "authorization_reason"),
        ("planned_capabilities", "planned_capabilities"),
        ("required_capabilities", "required_capabilities"),
        ("planned_tools", "planned_tools"),
    ):
        value = data.get(source)
        if value:
            result[target] = value
    return result


def _attempted_tools(task_state: Any) -> set[str]:
    attempted: set[str] = set()
    metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    attempted.update(str(tool) for tool in metadata.get("successful_tools") or [] if tool)
    attempted.update(str(tool) for tool in metadata.get("failed_tools") or [] if tool)
    for failure in getattr(task_state, "tool_failures", []) or []:
        if isinstance(failure, dict):
            attempted.update(str(failure.get(key)) for key in ("tool", "blocked_tool") if failure.get(key))
    return {item for item in attempted if item}
