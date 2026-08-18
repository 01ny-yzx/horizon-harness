"""Unified execution boundary for tool calls.

This module only evaluates whether a tool call may be dispatched. It delegates
actual safety rules to existing policy/guard modules and never executes tools.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.agent_access_policy import get_agent_access_mode
from core.browser_policy import BrowserPolicy
from core.coding_intent_resolution import EDIT_ACTIONS
from core.coding_safety import CodingSafetyPolicy
from core.file_access_policy import FileAccessPolicy
from core.file_output_grounding import canonical_output_identity, file_output_path_matches
from core.file_output_policy import resolve_existing_write_target, resolve_output_target
from core.path_zone_policy import PROJECT_PROTECTED_RELATIVE_ROOTS, PROJECT_PROTECTED_ROOT_FILES, evaluate_file_output_path_zone
from core.tool_execution_authorization import base_tool_name, tool_execution_authorization_guard
from core.tool_call_grants import get_tool_call_grant
from core.execution_payload_risk import ExecutionPayloadRisk, detect_execution_payload_risk as detect_shared_execution_payload_risk
from core.tool_risk_registry import (
    DATABASE_WRITE,
    ToolRiskMetadata,
    get_tool_risk_metadata,
    mcp_permission_level_to_risk_metadata,
    tool_risk_metadata_to_dict,
)
from core.mcp_filesystem import is_filesystem_server
from mcp_servers.database import validate_read_only_sql
from tools.registry import (
    is_browser_tool,
    is_database_read_tool,
    is_execution_tool,
    is_file_read_tool,
    is_file_write_tool,
    is_side_effect_tool,
)


BOUNDARY_METADATA_KEYS = (
    "execution_boundary_checked",
    "execution_boundary_status",
    "execution_boundary_allowed",
    "execution_boundary_code",
    "execution_boundary_reason",
    "execution_boundary_boundary",
    "execution_boundary_tool_name",
    "execution_boundary_access_mode",
    "tool_risk_registry_checked",
    "tool_risk_family",
    "tool_risk_operation",
    "tool_risk_side_effect",
    "tool_risk_source",
    "execution_payload_risk_checked",
    "execution_payload_risk",
    "execution_payload_risk_source",
    "embedded_tool_risk_family",
    "embedded_tool_risk_operation",
    "argument_snapshot_checked",
    "original_arguments_preserved",
    "raw_arguments_preserved",
    "normalized_arguments_preserved",
    "dispatch_arguments_tracked",
    "argument_security_used_original_values",
)

DATABASE_WRITE_MARKERS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "truncate",
    "merge",
    "write",
    "execute",
}
ARGUMENT_SNAPSHOT_PASSTHROUGH_METADATA_KEYS = {
    "argument_snapshot_checked",
    "original_arguments_preserved",
    "raw_arguments_preserved",
    "parsed_arguments_preserved",
    "normalized_arguments_preserved",
    "sanitized_arguments_tracked",
    "dispatch_arguments_tracked",
    "dispatch_arguments_derived_from_sanitized",
    "argument_security_used_original_values",
    "argument_security_relevant_fields",
    "argument_snapshot_redacted",
    "argument_snapshot_hash",
    "argument_snapshot_raw_fields",
    "argument_snapshot_parsed_fields",
    "argument_snapshot_task_profile_raw_fields",
    "blocked_original_argument_field",
    "blocked_original_argument_value_preview",
    "blocked_normalized_argument_field",
    "blocked_reason_source",
    "normalization_would_have_hidden_risk",
}
PATH_ARGUMENT_KEYS = (
    "path",
    "output_path",
    "target_path",
    "file_path",
    "filename",
    "requested_path",
    "requested_output_path",
    "raw_requested_path",
    "raw_requested_output_path",
    "original_requested_path",
    "coding_likely_files",
)
URL_ARGUMENT_KEYS = ("url", "href", "target_url")
SQL_ARGUMENT_KEYS = ("sql", "query", "statement")
INVALID_ARGUMENT_GUARD_CODES = {
    "path_empty",
    "empty_url",
    "missing_hostname",
    "invalid_local_file_path",
    "database_sql_invalid",
}
ORDINARY_FAILURE_GUARD_CODES = {
    "dns_resolution_failed",
    "blocked_dns_resolution_failed",
}
OPAQUE_URI_SCHEMES = {"data", "javascript", "file", "about", "chrome", "edge"}
@dataclass
class ExecutionBoundaryDecision:
    allowed: bool
    status: str
    code: str
    reason: str
    boundary: str
    tool_name: str
    access_mode: str
    sanitized_arguments: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.sanitized_arguments is not None:
            payload["sanitized_arguments"] = _safe_argument_summary(self.sanitized_arguments)
        payload["metadata"] = _safe_metadata(self.metadata)
        return payload

    def metadata_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "allowed": self.allowed,
            "code": self.code,
            "reason": _safe_text(self.reason, 240),
            "boundary": self.boundary,
            "tool_name": self.tool_name,
            "access_mode": self.access_mode,
            **_safe_metadata(self.metadata),
        }


@dataclass(frozen=True)
class ToolArgumentSnapshot:
    tool_name: str
    base_tool: str
    raw_arguments_text: str
    parsed_arguments: Mapping[str, Any]
    raw_argument_values: Mapping[str, Any]
    normalized_arguments: Mapping[str, Any]
    sanitized_arguments: Mapping[str, Any] | None
    task_profile_raw_values: Mapping[str, Any]
    security_relevant_values: Mapping[str, Any]

    def with_sanitized_arguments(self, sanitized_arguments: Mapping[str, Any] | None) -> "ToolArgumentSnapshot":
        return ToolArgumentSnapshot(
            tool_name=self.tool_name,
            base_tool=self.base_tool,
            raw_arguments_text=self.raw_arguments_text,
            parsed_arguments=self.parsed_arguments,
            raw_argument_values=self.raw_argument_values,
            normalized_arguments=self.normalized_arguments,
            sanitized_arguments=dict(sanitized_arguments) if sanitized_arguments is not None else None,
            task_profile_raw_values=self.task_profile_raw_values,
            security_relevant_values=self.security_relevant_values,
        )

    def metadata(self, *, dispatch_arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        dispatch_args = dict(dispatch_arguments or {})
        return {
            "argument_snapshot_checked": True,
            "original_arguments_preserved": True,
            "raw_arguments_preserved": True,
            "parsed_arguments_preserved": True,
            "normalized_arguments_preserved": True,
            "sanitized_arguments_tracked": self.sanitized_arguments is not None,
            "dispatch_arguments_tracked": dispatch_arguments is not None,
            "dispatch_arguments_derived_from_sanitized": bool(
                self.sanitized_arguments is not None and dict(self.sanitized_arguments) != dict(self.parsed_arguments)
            ),
            "argument_security_used_original_values": bool(self.raw_argument_values or self.task_profile_raw_values),
            "argument_security_relevant_fields": sorted(self.security_relevant_values),
            "argument_snapshot_redacted": True,
            "argument_snapshot_hash": _argument_snapshot_hash(self, dispatch_args),
            "argument_snapshot_raw_fields": sorted(str(key) for key in self.raw_argument_values),
            "argument_snapshot_parsed_fields": sorted(str(key) for key in self.parsed_arguments),
            "argument_snapshot_task_profile_raw_fields": sorted(str(key) for key in self.task_profile_raw_values),
        }


def evaluate_tool_execution_boundary(
    *,
    task_state: Any,
    tool_name: str,
    arguments: dict[str, Any],
    tool_call_envelope: Any | None = None,
    raw_arguments: str | None = None,
    browser_policy: BrowserPolicy | None = None,
    tool_registry: dict[str, Any] | None = None,
    mcp_registry: Any | None = None,
    now: Any = None,
) -> ExecutionBoundaryDecision:
    """Evaluate all pre-dispatch execution boundaries for one tool call."""

    del now
    access_mode = get_agent_access_mode()
    base_tool = base_tool_name(tool_name)
    argument_snapshot = build_tool_argument_snapshot(
        task_state=task_state,
        tool_name=tool_name,
        base_tool=base_tool,
        arguments=arguments,
        raw_arguments=raw_arguments,
    )
    sanitized = dict(arguments)
    if is_file_write_tool(base_tool):
        _sanitize_write_file_arguments(task_state, sanitized)
        argument_snapshot = argument_snapshot.with_sanitized_arguments(sanitized)
    snapshot_metadata = argument_snapshot.metadata(dispatch_arguments=sanitized)
    if tool_registry is not None and tool_name not in tool_registry and base_tool not in tool_registry:
        decision = _decision(
            False,
            "unsupported",
            "tool_not_registered",
            f"Unknown tool: {tool_name}",
            "generic_tool",
            tool_name,
            access_mode,
            metadata=snapshot_metadata,
        )
        _record_decision(task_state, decision)
        return decision

    risk_metadata = resolve_tool_risk_metadata_for_boundary(tool_name, mcp_registry=mcp_registry)
    if risk_metadata is None:
        decision = _decision(
            False,
            "blocked",
            "tool_risk_metadata_missing",
            "Tool risk metadata is missing; dispatch is blocked by universal pre-dispatch boundary.",
            "tool_risk_registry",
            tool_name,
            access_mode,
            metadata={
                "tool_risk_registry_checked": True,
                "risk_metadata_missing": True,
                "base_tool": base_tool,
                "blocked_tool": tool_name,
            },
        )
        _attach_argument_snapshot_metadata(decision, snapshot_metadata)
        _record_decision(task_state, decision)
        return decision
    risk_metadata_payload = _risk_metadata_summary(risk_metadata)

    payload_risk = detect_execution_payload_risk(
        tool_name,
        arguments,
        raw_arguments,
        risk_metadata=risk_metadata,
        argument_snapshot=argument_snapshot,
    )
    if payload_risk is not None:
        decision = _decision(
            False,
            "blocked",
            "execution_payload_git_mutation_not_authorized",
            "Execution payload contains git mutation commands that are not authorized for generic execution dispatch.",
            "tool_risk_registry",
            tool_name,
            access_mode,
            metadata=payload_risk.to_metadata(),
        )
        _attach_tool_risk_metadata(decision, risk_metadata_payload)
        _attach_argument_snapshot_metadata(
            decision,
            snapshot_metadata,
            extra=_normalization_hidden_risk_metadata(argument_snapshot, payload_risk.payload_fields[0] if payload_risk.payload_fields else ""),
        )
        _record_decision(task_state, decision)
        return decision

    snapshot_guard = _argument_snapshot_security_guard(
        task_state=task_state,
        tool_name=tool_name,
        argument_snapshot=argument_snapshot,
        browser_policy=browser_policy or BrowserPolicy(),
    )
    if snapshot_guard is not None:
        decision = _decision_from_guard(snapshot_guard, tool_name, access_mode)
        _attach_tool_risk_metadata(decision, risk_metadata_payload)
        _attach_argument_snapshot_metadata(decision, snapshot_metadata)
        _record_decision(task_state, decision)
        return decision

    registry_guard = _tool_risk_registry_guard(
        task_state=task_state,
        tool_name=tool_name,
        base_tool=base_tool,
        risk_metadata=risk_metadata,
        access_mode=access_mode,
    )
    if registry_guard is not None:
        decision = _decision_from_guard(registry_guard, tool_name, access_mode)
        _attach_tool_risk_metadata(decision, risk_metadata_payload)
        _attach_argument_snapshot_metadata(decision, snapshot_metadata)
        _record_decision(task_state, decision)
        return decision

    pre_authorization_guards = (
        _structured_agent_self_path_preflight(task_state, tool_name, sanitized),
        _file_write_deny_only_preflight(task_state, tool_name, sanitized, raw_arguments or ""),
        _side_effect_path_zone_preflight(task_state, tool_name, sanitized),
    )
    for guard in pre_authorization_guards:
        if guard is not None:
            decision = _decision_from_guard(guard, tool_name, access_mode)
            _attach_tool_risk_metadata(decision, risk_metadata_payload)
            _attach_argument_snapshot_metadata(decision, snapshot_metadata)
            _record_decision(task_state, decision)
            return decision

    auth_guard = tool_execution_authorization_guard(
        task_state=task_state,
        tool_name=tool_name,
        tool_call_envelope=tool_call_envelope,
        sanitized_arguments=sanitized,
    )
    if auth_guard is not None:
        decision = _decision_from_guard(auth_guard, tool_name, access_mode)
        _attach_tool_risk_metadata(decision, risk_metadata_payload)
        _attach_argument_snapshot_metadata(decision, snapshot_metadata)
        _record_decision(task_state, decision)
        return decision

    guards = (
        local_file_path_argument_guard(tool_name, sanitized),
        _research_python_guard(task_state, base_tool),
        _file_tool_raw_path_guard(task_state, tool_name, sanitized, raw_arguments or ""),
        _file_read_access_guard(tool_name, sanitized),
        _sandbox_guard(tool_name, sanitized),
        _browser_guard(browser_policy or BrowserPolicy(), tool_name, sanitized),
        _database_guard(tool_name, sanitized),
    )
    for guard in guards:
        if guard is not None:
            decision = _decision_from_guard(guard, tool_name, access_mode)
            _attach_tool_risk_metadata(decision, risk_metadata_payload)
            _attach_argument_snapshot_metadata(decision, snapshot_metadata)
            _record_decision(task_state, decision)
            return decision

    if is_file_write_tool(base_tool):
        if _allow_agent_internal_write(task_state, str(sanitized.get("path") or sanitized.get("output_path") or "")):
            sanitized["allow_agent_internal"] = True
            argument_snapshot = argument_snapshot.with_sanitized_arguments(sanitized)
            snapshot_metadata = argument_snapshot.metadata(dispatch_arguments=sanitized)

    post_sanitize_guards = (_file_write_policy_guard(task_state, tool_name, sanitized),)
    for guard in post_sanitize_guards:
        if guard is not None:
            decision = _decision_from_guard(guard, tool_name, access_mode)
            _attach_tool_risk_metadata(decision, risk_metadata_payload)
            _attach_argument_snapshot_metadata(decision, snapshot_metadata)
            _record_decision(task_state, decision)
            return decision

    final_snapshot_metadata = argument_snapshot.metadata(dispatch_arguments=sanitized)
    call_id = str(
        getattr(tool_call_envelope, "provider_call_id", "")
        or getattr(tool_call_envelope, "call_id", "")
        or ""
    ).strip()
    grant = get_tool_call_grant(task_state, call_id) if call_id else None
    grant_metadata = (
        {
            "tool_call_grant_checked": True,
            "tool_call_grant_call_id": call_id,
            "tool_call_grant_status": str(grant.get("status") or ""),
            "tool_call_grant_step_index": int(grant.get("step_index") or 0),
            "tool_call_grant_fingerprint_match": True,
        }
        if isinstance(grant, dict)
        else {}
    )
    decision = _decision(
        True,
        "allowed",
        "ok",
        "Execution boundary allowed tool dispatch.",
        _boundary_for_risk_metadata(risk_metadata, base_tool),
        tool_name,
        access_mode,
        sanitized_arguments=sanitized,
        metadata={**risk_metadata_payload, **final_snapshot_metadata, **grant_metadata},
    )
    _record_decision(task_state, decision)
    return decision


def blocked_observation(decision: ExecutionBoundaryDecision) -> dict[str, Any]:
    """Return the unified observation shape for blocked boundary decisions."""

    data = {
        "status": "blocked",
        "blocked_by": "execution_boundary",
        "code": decision.code,
        "reason": _safe_text(decision.reason, 500),
        "tool_name": decision.tool_name,
        "boundary": decision.boundary,
        "execution_boundary": decision.metadata_summary(),
    }
    data.update(_safe_metadata(decision.metadata))
    if "blocked_tool" not in data:
        data["blocked_tool"] = decision.tool_name
    return {
        "success": False,
        "error": _safe_text(decision.reason, 500) or "Tool call blocked by execution boundary.",
        "data": data,
    }


def _decision(
    allowed: bool,
    status: str,
    code: str,
    reason: str,
    boundary: str,
    tool_name: str,
    access_mode: str,
    *,
    sanitized_arguments: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExecutionBoundaryDecision:
    return ExecutionBoundaryDecision(
        allowed=allowed,
        status=status,
        code=code,
        reason=reason,
        boundary=boundary,
        tool_name=tool_name,
        access_mode=access_mode,
        sanitized_arguments=sanitized_arguments,
        metadata=dict(metadata or {}),
    )


def build_tool_argument_snapshot(
    *,
    task_state: Any,
    tool_name: str,
    base_tool: str,
    arguments: Mapping[str, Any],
    raw_arguments: str | None,
) -> ToolArgumentSnapshot:
    raw_text = str(raw_arguments or "")
    raw_values = _parse_raw_argument_values(raw_text)
    parsed_values = dict(arguments)
    profile_values: dict[str, Any] = {}
    security_values = _security_relevant_values(parsed_values, raw_values, profile_values)
    return ToolArgumentSnapshot(
        tool_name=tool_name,
        base_tool=base_tool,
        raw_arguments_text=raw_text,
        parsed_arguments=parsed_values,
        raw_argument_values=raw_values,
        normalized_arguments=dict(parsed_values),
        sanitized_arguments=None,
        task_profile_raw_values=profile_values,
        security_relevant_values=security_values,
    )


def _parse_raw_argument_values(raw_arguments: str) -> dict[str, Any]:
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _security_relevant_values(
    parsed_values: Mapping[str, Any],
    raw_values: Mapping[str, Any],
    profile_values: Mapping[str, Any],
) -> dict[str, Any]:
    keys = set(PATH_ARGUMENT_KEYS) | set(URL_ARGUMENT_KEYS) | set(SQL_ARGUMENT_KEYS) | {
        "command",
        "cmd",
        "shell_command",
        "code",
        "python_code",
        "script",
        "args",
        "argv",
    }
    result: dict[str, Any] = {}
    for source, values in (
        ("parsed", parsed_values),
        ("raw_arguments", raw_values),
    ):
        for key, value in values.items():
            if key in keys or key in {"provided_urls", "browser_url"}:
                result[f"{source}.{key}"] = _safe_value_preview(value)
    return result


def _argument_snapshot_hash(snapshot: ToolArgumentSnapshot, dispatch_arguments: Mapping[str, Any]) -> str:
    payload = {
        "tool_name": snapshot.tool_name,
        "base_tool": snapshot.base_tool,
        "raw_arguments_text": snapshot.raw_arguments_text,
        "parsed_arguments": _safe_argument_summary(dict(snapshot.parsed_arguments)),
        "raw_argument_values": _safe_argument_summary(dict(snapshot.raw_argument_values)),
        "normalized_arguments": _safe_argument_summary(dict(snapshot.normalized_arguments)),
        "sanitized_arguments": _safe_argument_summary(dict(snapshot.sanitized_arguments or {})),
        "dispatch_arguments": _safe_argument_summary(dict(dispatch_arguments)),
        "task_profile_raw_values": _safe_argument_summary(dict(snapshot.task_profile_raw_values)),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_value_preview(value: Any, limit: int = 80) -> str:
    if isinstance(value, (list, tuple)):
        return _safe_text(",".join(str(item) for item in value[:5]), limit)
    if isinstance(value, dict):
        return f"[dict:{len(value)}]"
    return _safe_text(str(value), limit)


def _attach_argument_snapshot_metadata(
    decision: ExecutionBoundaryDecision,
    snapshot_metadata: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    decision.metadata.update(snapshot_metadata)
    if extra:
        decision.metadata.update(extra)


def _normalization_hidden_risk_metadata(snapshot: ToolArgumentSnapshot, field: str) -> dict[str, Any]:
    return {
        "blocked_original_argument_field": field,
        "blocked_original_argument_value_preview": _blocked_value_preview(snapshot, field),
        "blocked_normalized_argument_field": field if field in snapshot.parsed_arguments else "",
        "blocked_reason_source": _argument_field_source(snapshot, field),
        "normalization_would_have_hidden_risk": True,
    }


def _blocked_value_preview(snapshot: ToolArgumentSnapshot, field: str) -> str:
    for values in (snapshot.raw_argument_values, snapshot.parsed_arguments):
        value = values.get(field) if isinstance(values, Mapping) else None
        if value not in (None, ""):
            return _safe_value_preview(value)
    return ""


def _argument_field_source(snapshot: ToolArgumentSnapshot, field: str) -> str:
    if field in snapshot.raw_argument_values:
        return "raw_arguments"
    if field in snapshot.parsed_arguments:
        return "parsed_arguments"
    return "raw_arguments"


def _argument_snapshot_security_guard(
    *,
    task_state: Any,
    tool_name: str,
    argument_snapshot: ToolArgumentSnapshot,
    browser_policy: BrowserPolicy,
) -> dict[str, Any] | None:
    path_guard = _argument_snapshot_path_guard(task_state, tool_name, argument_snapshot)
    if path_guard is not None:
        return path_guard
    url_guard = _argument_snapshot_url_guard(tool_name, argument_snapshot, browser_policy)
    if url_guard is not None:
        return url_guard
    sql_guard = _argument_snapshot_sql_guard(tool_name, argument_snapshot)
    if sql_guard is not None:
        return sql_guard
    return None


def _argument_snapshot_path_guard(task_state: Any, tool_name: str, snapshot: ToolArgumentSnapshot) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    if not (is_file_read_tool(base_tool) or is_file_write_tool(base_tool)):
        return None
    operation = "write" if is_file_write_tool(base_tool) else "read"
    final_write_path = _snapshot_final_write_path(snapshot) if operation == "write" else ""
    for field, path, source in _snapshot_path_candidates(snapshot):
        if operation == "write" and source != "parsed_arguments":
            if _same_safe_output_identity(final_write_path, path) or _same_safe_output_leaf(final_write_path, path):
                continue
        if operation == "write":
            decision = evaluate_file_output_path_zone(
                requested_path=path,
                raw_requested_path=path,
                allow_agent_internal=_allow_agent_internal_write(task_state, path),
                default_relative_to_output_root=not _snapshot_path_requires_project_root(source, field, path),
            )
            if decision.allowed:
                continue
            data = decision.to_dict()
            data["path_zone_code"] = decision.code
            data["code"] = _compat_path_zone_code(decision.code)
            data["message"] = _file_preflight_message(data["code"], decision.requested_path or decision.raw_path)
        else:
            continue
        data.update(
            {
                "operation": operation,
                "blocked_original_argument_field": field,
                "blocked_original_argument_value_preview": _safe_value_preview(path),
                "blocked_normalized_argument_field": field if field in snapshot.parsed_arguments else "",
                "blocked_reason_source": source,
                "normalization_would_have_hidden_risk": source != "parsed_arguments"
                or _normalization_would_hide_field(snapshot, field, path),
            }
        )
        return {"success": False, "error": data["message"], "data": data}
    return None


def _snapshot_final_write_path(snapshot: ToolArgumentSnapshot) -> str:
    if snapshot.sanitized_arguments is not None:
        sanitized_path = _current_write_tool_path(snapshot.sanitized_arguments)
        if sanitized_path:
            return sanitized_path
    return _current_write_tool_path(snapshot.parsed_arguments)


def _snapshot_path_candidates(snapshot: ToolArgumentSnapshot) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    for source, values in (
        ("raw_arguments", snapshot.raw_argument_values),
        ("parsed_arguments", snapshot.parsed_arguments),
    ):
        for key in PATH_ARGUMENT_KEYS:
            value = values.get(key) if isinstance(values, Mapping) else None
            if isinstance(value, str) and value.strip() and not _is_url_like(value):
                candidates.append((key, value.strip(), source))
            elif isinstance(value, list):
                candidates.extend((key, item.strip(), source) for item in value if isinstance(item, str) and item.strip() and not _is_url_like(item))
    return _unique_snapshot_candidates(candidates)


def _path_has_traversal(path: str) -> bool:
    return any(part == ".." for part in str(path or "").replace("\\", "/").split("/"))


def _snapshot_path_requires_project_root(source: str, field: str, path: str) -> bool:
    if source == "parsed_arguments":
        return False
    normalized = str(path or "").strip().replace("\\", "/").lstrip("./")
    root = normalized.split("/", 1)[0]
    return (
        _path_has_traversal(path)
        or root in PROJECT_PROTECTED_RELATIVE_ROOTS
        or root in PROJECT_PROTECTED_ROOT_FILES
        or _is_absolute_like_for_boundary(path)
    )


def _unique_snapshot_candidates(candidates: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field, value, source in candidates:
        key = (field, value)
        if key in seen:
            continue
        seen.add(key)
        result.append((field, value, source))
    return result


def _normalization_would_hide_field(snapshot: ToolArgumentSnapshot, field: str, value: str) -> bool:
    parsed_value = snapshot.parsed_arguments.get(field) if isinstance(snapshot.parsed_arguments, Mapping) else None
    if parsed_value is None:
        parsed_value = snapshot.parsed_arguments.get("path") if isinstance(snapshot.parsed_arguments, Mapping) else None
    return bool(str(parsed_value or "") and str(parsed_value) != str(value))


def _argument_snapshot_url_guard(tool_name: str, snapshot: ToolArgumentSnapshot, browser_policy: BrowserPolicy) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    if not is_browser_tool(base_tool):
        return None
    for field, url, source in _snapshot_url_candidates(snapshot):
        if not str(url).lower().startswith(("http://", "https://")):
            continue
        decision = browser_policy.check_url(url)
        if decision.get("allowed"):
            continue
        data = {
            "code": str(decision.get("code") or "browser_url_blocked"),
            "reason": str(decision.get("reason") or "unsafe_url"),
            "blocked_tool": tool_name,
            "blocked_url": _safe_value_preview(url),
            "blocked_original_argument_field": field,
            "blocked_original_argument_value_preview": _safe_value_preview(url),
            "blocked_normalized_argument_field": field if field in snapshot.parsed_arguments else "",
            "blocked_reason_source": source,
            "normalization_would_have_hidden_risk": source != "parsed_arguments"
            or _normalization_would_hide_field(snapshot, field, url),
        }
        return {"success": False, "error": "当前 URL 参数包含不允许访问的本地或私有地址。", "data": data}
    return None


def _snapshot_url_candidates(snapshot: ToolArgumentSnapshot) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    for source, values in (
        ("raw_arguments", snapshot.raw_argument_values),
        ("parsed_arguments", snapshot.parsed_arguments),
    ):
        for key in URL_ARGUMENT_KEYS + ("browser_url", "provided_urls"):
            value = values.get(key) if isinstance(values, Mapping) else None
            if isinstance(value, str) and value.strip():
                candidates.append((key, value.strip(), source))
            elif isinstance(value, list):
                candidates.extend((key, str(item).strip(), source) for item in value if str(item or "").strip())
        for key in ("command", "cmd", "code", "python_code", "script"):
            value = values.get(key) if isinstance(values, Mapping) else None
            if isinstance(value, str):
                candidates.extend((key, item, source) for item in re.findall(r"\bhttps?://[^\s<>\]\)\"']+", value))
    return _unique_snapshot_candidates(candidates)


def _argument_snapshot_sql_guard(tool_name: str, snapshot: ToolArgumentSnapshot) -> dict[str, Any] | None:
    if base_tool_name(tool_name) != "database_select_query":
        return None
    for field, sql, source in _snapshot_sql_candidates(snapshot):
        validation = validate_read_only_sql(sql)
        if validation.success:
            continue
        error = validation.error if isinstance(validation.error, dict) else {}
        code = str(error.get("error_code") or error.get("code") or "database_query_not_read_only")
        data = {
            "code": code,
            "reason": "database_write",
            "blocked_tool": tool_name,
            "blocked_original_argument_field": field,
            "blocked_original_argument_value_preview": _safe_value_preview(sql),
            "blocked_normalized_argument_field": field if field in snapshot.parsed_arguments else "",
            "blocked_reason_source": source,
            "normalization_would_have_hidden_risk": source != "parsed_arguments"
            or _normalization_would_hide_field(snapshot, field, sql),
        }
        return {"success": False, "error": "This database tool only allows read-only queries.", "data": data}
    return None


def _snapshot_sql_candidates(snapshot: ToolArgumentSnapshot) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    for source, values in (
        ("raw_arguments", snapshot.raw_argument_values),
        ("parsed_arguments", snapshot.parsed_arguments),
    ):
        for key in SQL_ARGUMENT_KEYS:
            value = values.get(key) if isinstance(values, Mapping) else None
            if isinstance(value, str) and value.strip():
                candidates.append((key, value.strip(), source))
    return _unique_snapshot_candidates(candidates)


def detect_execution_payload_risk(
    tool_name: str,
    arguments: Mapping[str, Any],
    raw_arguments: Any | None = None,
    *,
    risk_metadata: ToolRiskMetadata | None = None,
    argument_snapshot: ToolArgumentSnapshot | None = None,
) -> ExecutionPayloadRisk | None:
    """Detect high-risk embedded commands inside generic execution payloads."""

    metadata = risk_metadata or get_tool_risk_metadata(tool_name) or get_tool_risk_metadata(base_tool_name(tool_name))
    if not _requires_execution_payload_check(tool_name, metadata):
        return None
    if argument_snapshot is not None:
        return detect_shared_execution_payload_risk(
            arguments=arguments,
            raw_argument_values=argument_snapshot.raw_argument_values,
            raw_arguments_text=argument_snapshot.raw_arguments_text,
            check_raw_text=bool(argument_snapshot.raw_arguments_text and not argument_snapshot.raw_argument_values),
        )
    if isinstance(raw_arguments, Mapping):
        return detect_shared_execution_payload_risk(arguments=arguments, raw_argument_values=raw_arguments)
    if isinstance(raw_arguments, str):
        return detect_shared_execution_payload_risk(arguments=arguments, raw_arguments_text=raw_arguments, check_raw_text=True)
    return detect_shared_execution_payload_risk(arguments=arguments)


def _requires_execution_payload_check(tool_name: str, risk_metadata: ToolRiskMetadata | None) -> bool:
    base_tool = base_tool_name(tool_name)
    if base_tool in {"sandbox_exec", "run_command", "run_shell_in_sandbox"}:
        return False
    if is_execution_tool(base_tool):
        return True
    if risk_metadata is None:
        return False
    categories = {str(item) for item in risk_metadata.boundary_categories}
    return (
        risk_metadata.operation == "execute"
        or bool(categories.intersection({"execution", "shell", "python", "sandbox"}))
        or risk_metadata.family in {"shell", "python"}
    )


def resolve_tool_risk_metadata_for_boundary(
    tool_name: str,
    *,
    mcp_registry: Any | None = None,
) -> ToolRiskMetadata | None:
    """Resolve deterministic tool risk metadata before dispatch."""

    exact = get_tool_risk_metadata(tool_name)
    if exact is not None:
        return exact

    mcp_metadata = _resolve_mcp_tool_risk_metadata(tool_name, mcp_registry)
    if mcp_metadata is not None:
        return mcp_metadata

    base_tool = base_tool_name(tool_name)
    if base_tool != tool_name:
        return get_tool_risk_metadata(base_tool)
    return None


def _resolve_mcp_tool_risk_metadata(tool_name: str, mcp_registry: Any | None) -> ToolRiskMetadata | None:
    if mcp_registry is None:
        return None
    get_tool = getattr(mcp_registry, "get_tool", None)
    if not callable(get_tool):
        return None
    spec = get_tool(tool_name)
    if spec is None:
        return None
    server = getattr(mcp_registry, "servers", {}).get(getattr(spec, "server_name", ""))
    if server is not None and is_filesystem_server(server):
        filesystem_metadata = get_tool_risk_metadata(f"mcp_filesystem.{getattr(spec, 'name', '')}")
        if filesystem_metadata is not None:
            return filesystem_metadata
    return mcp_permission_level_to_risk_metadata(
        tool_name=str(getattr(spec, "name", tool_name) or tool_name),
        canonical_name=str(getattr(spec, "qualified_name", tool_name) or tool_name),
        permission_level=str(getattr(spec, "permission_level", "read_only") or "read_only"),
        server_name=str(getattr(spec, "server_name", "") or ""),
    )


def _tool_risk_registry_guard(
    *,
    task_state: Any,
    tool_name: str,
    base_tool: str,
    risk_metadata: ToolRiskMetadata,
    access_mode: str,
) -> dict[str, Any] | None:
    if risk_metadata.operation == DATABASE_WRITE or risk_metadata.canonical_name == "database_write_contract":
        return {
            "success": False,
            "error": "Database write tools are blocked by the universal pre-dispatch boundary.",
            "data": {
                "code": "database_write_tool_not_allowed",
                "reason": "database_write",
                "blocked_tool": tool_name,
                "base_tool": base_tool,
            },
        }

    if risk_metadata.source not in {"mcp", "mcp_filesystem"} or not risk_metadata.side_effect:
        return None

    if access_mode == "read_only":
        return {
            "success": False,
            "error": "当前 Agent 权限模式为 read_only，不允许执行高风险 MCP 工具。",
            "data": {
                "code": "mcp_tool_blocked_by_access_mode",
                "reason": "agent_access_mode_read_only",
                "blocked_tool": tool_name,
                "base_tool": base_tool,
            },
        }

    return None


def _risk_metadata_summary(risk_metadata: ToolRiskMetadata) -> dict[str, Any]:
    payload = tool_risk_metadata_to_dict(risk_metadata)
    return {
        "tool_risk_registry_checked": True,
        "tool_risk_family": risk_metadata.family,
        "tool_risk_operation": risk_metadata.operation,
        "tool_risk_side_effect": risk_metadata.side_effect,
        "tool_risk_source": risk_metadata.source,
        "tool_risk_metadata": payload,
    }


def _attach_tool_risk_metadata(decision: ExecutionBoundaryDecision, risk_metadata_payload: dict[str, Any]) -> None:
    decision.metadata.update(risk_metadata_payload)


def _decision_from_guard(guard: dict[str, Any], tool_name: str, access_mode: str) -> ExecutionBoundaryDecision:
    data = guard.get("data", {})
    data = data if isinstance(data, dict) else {}
    code = str(data.get("code") or data.get("error_code") or "execution_boundary_blocked")
    reason = str(guard.get("error") or data.get("message") or data.get("reason") or code)
    boundary = _boundary_from_guard(code, tool_name, data)
    status = "needs_authorization" if boundary == "tool_authorization" else "blocked"
    if code in INVALID_ARGUMENT_GUARD_CODES:
        status = "invalid_arguments"
    elif code in ORDINARY_FAILURE_GUARD_CODES:
        status = "failed"
    metadata = dict(data)
    metadata["original_observation"] = {
        "success": False,
        "error": _safe_text(str(guard.get("error") or ""), 500),
    }
    return _decision(False, status, code, reason, boundary, tool_name, access_mode, metadata=metadata)


def _record_decision(task_state: Any, decision: ExecutionBoundaryDecision) -> None:
    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        return
    summary = decision.metadata_summary()
    metadata.update(
        {
            "execution_boundary_checked": True,
            "execution_boundary_status": decision.status,
            "execution_boundary_allowed": decision.allowed,
            "execution_boundary_code": decision.code,
            "execution_boundary_reason": summary["reason"],
            "execution_boundary_boundary": decision.boundary,
            "execution_boundary_tool_name": decision.tool_name,
            "execution_boundary_access_mode": decision.access_mode,
        }
    )
    for key in (
        "tool_risk_registry_checked",
        "tool_risk_family",
        "tool_risk_operation",
        "tool_risk_side_effect",
        "tool_risk_source",
        "tool_risk_metadata",
        "risk_metadata_missing",
        "execution_payload_risk_checked",
        "execution_payload_risk",
        "execution_payload_risk_source",
        "embedded_tool_risk_family",
        "embedded_tool_risk_operation",
        "embedded_git_subcommands",
        "generic_execution_cannot_bypass_git_mutation",
        "argument_snapshot_checked",
        "original_arguments_preserved",
        "raw_arguments_preserved",
        "parsed_arguments_preserved",
        "normalized_arguments_preserved",
        "sanitized_arguments_tracked",
        "dispatch_arguments_tracked",
        "dispatch_arguments_derived_from_sanitized",
        "argument_security_used_original_values",
        "argument_security_relevant_fields",
        "argument_snapshot_redacted",
        "argument_snapshot_hash",
        "argument_snapshot_raw_fields",
        "argument_snapshot_parsed_fields",
        "argument_snapshot_task_profile_raw_fields",
        "blocked_original_argument_field",
        "blocked_original_argument_value_preview",
        "blocked_normalized_argument_field",
        "blocked_reason_source",
        "normalization_would_have_hidden_risk",
    ):
        if key in summary:
            metadata[key] = summary[key]
    decisions = metadata.setdefault("execution_boundary_decisions", [])
    if isinstance(decisions, list):
        decisions.append(summary)
        del decisions[:-20]


def _file_write_deny_only_preflight(
    task_state: Any,
    tool_name: str,
    arguments: dict[str, Any],
    raw_arguments: str,
) -> dict[str, Any] | None:
    if not is_file_write_tool(base_tool_name(tool_name)):
        return None
    if get_agent_access_mode() == "read_only":
        return {
            "success": False,
            "error": "当前 Agent 权限模式为 read_only，不允许写入、修改或删除文件。",
            "data": {
                "code": "tool_execution_not_authorized",
                "reason": "agent_access_mode_read_only",
                "blocked_tool": tool_name,
                "required_capabilities": [],
            },
        }

    zone_guard = _file_write_path_zone_guard(task_state, tool_name, arguments, raw_arguments)
    if zone_guard is not None:
        return zone_guard
    return None


STRUCTURED_AGENT_SELF_PATH_ARGUMENT_KEYS = (
    "raw_requested_path",
    "original_requested_path",
    "raw_requested_output_path",
    "requested_output_path",
    "path",
    "output_path",
    "target_path",
    "file_path",
)


def _structured_agent_self_path_preflight(task_state: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    if not is_side_effect_tool(base_tool):
        return None
    for candidate in _collect_structured_agent_self_path_candidates(task_state, arguments, include_coding_likely_files=not is_file_write_tool(base_tool)):
        decision = evaluate_file_output_path_zone(
            requested_path=candidate,
            raw_requested_path=candidate,
            allow_agent_internal=True,
            default_relative_to_output_root=False,
        )
        if decision.code != "agent_self_protected_path_blocked":
            continue
        data = decision.to_dict()
        data["path_zone_code"] = decision.code
        data["code"] = decision.code
        data["message"] = _file_preflight_message(decision.code, decision.requested_path or decision.raw_path)
        data["operation"] = "write"
        data["structured_agent_self_path_preflight"] = True
        return {"success": False, "error": data["message"], "data": data}
    return None


def _collect_structured_agent_self_path_candidates(
    task_state: Any,
    arguments: dict[str, Any],
    *,
    include_coding_likely_files: bool,
) -> list[str]:
    candidates: list[str] = []
    del task_state, include_coding_likely_files
    for key in STRUCTURED_AGENT_SELF_PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
        elif isinstance(value, list):
            candidates.extend(item.strip() for item in value if isinstance(item, str) and item.strip())

    result: list[str] = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return result


def _side_effect_path_zone_preflight(
    task_state: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    if not is_side_effect_tool(base_tool):
        return None
    if is_file_write_tool(base_tool):
        return None
    candidates = _collect_side_effect_file_write_candidates(task_state, arguments)
    if not candidates:
        return None

    raw_context = _command_side_effect_context(arguments)
    for candidate in candidates:
        decision = evaluate_file_output_path_zone(
            requested_path=candidate,
            raw_requested_path=candidate,
            raw_context=raw_context,
            allow_agent_internal=_allow_agent_internal_write(task_state, candidate),
        )
        if decision.allowed:
            continue
        if decision.code not in _PROTECTED_PATH_ZONE_CODES:
            continue
        data = decision.to_dict()
        data["path_zone_code"] = decision.code
        data["code"] = _compat_path_zone_code(decision.code)
        data["message"] = _file_preflight_message(data["code"], decision.requested_path or decision.raw_path)
        data["operation"] = "write"
        data["side_effect_path_zone_preflight"] = True
        return {"success": False, "error": data["message"], "data": data}
    return None


def _file_write_path_zone_guard(
    task_state: Any,
    tool_name: str,
    arguments: dict[str, Any],
    raw_arguments: str,
) -> dict[str, Any] | None:
    if not is_file_write_tool(base_tool_name(tool_name)):
        return None
    path = _current_write_tool_path(arguments)
    raw_path = _raw_write_requested_path(task_state, arguments)
    raw_context = _file_write_path_raw_context(task_state, arguments)
    candidates = _collect_file_write_path_candidates(arguments, raw_arguments, task_state)
    if path and path not in candidates:
        candidates.insert(0, path)
    if raw_path and raw_path not in candidates:
        candidates.append(raw_path)
    for candidate in candidates or [path]:
        if candidate != path and _same_safe_output_identity(path, candidate):
            continue
        if candidate != path and not _raw_file_write_candidate_is_dangerous(candidate):
            continue
        decision = evaluate_file_output_path_zone(
            requested_path=candidate,
            raw_requested_path=candidate,
            filename=str(arguments.get("filename") or "") or None,
            allow_agent_internal=_allow_agent_internal_write(task_state, candidate),
            raw_context=raw_context,
        )
        if not decision.allowed:
            data = decision.to_dict()
            data["path_zone_code"] = decision.code
            data["code"] = _compat_path_zone_code(decision.code)
            data["message"] = _file_preflight_message(data["code"], decision.requested_path or decision.raw_path)
            data["operation"] = "write"
            return {"success": False, "error": data["message"], "data": data}
    return None


def _file_write_path_raw_context(task_state: Any, arguments: dict[str, Any]) -> str:
    """Return path-only context for write_file path-zone checks."""

    values: list[str] = []
    for key in FILE_WRITE_PATH_CANDIDATE_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, list):
            values.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    del task_state
    return "\n".join(dict.fromkeys(values))


def _file_read_access_guard(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    if not is_file_read_tool(base_tool_name(tool_name)):
        return None
    path = str(arguments.get("path") or ".")
    raw_path = str(arguments.get("raw_requested_path") or path)
    decision = FileAccessPolicy().evaluate(path, operation="read", raw_requested_path=raw_path)
    if decision.allowed:
        return None
    if decision.code != "path_empty":
        return None
    data = decision.to_dict()
    data["message"] = _file_preflight_message(decision.code, decision.requested_path)
    return {"success": False, "error": data["message"], "data": data}


def _is_unambiguous_uri_path(path: str) -> bool:
    """Return True only for URI forms that cannot be mistaken for local paths."""

    value = str(path or "").strip()
    if not value:
        return False
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if not scheme:
        return False
    if len(scheme) == 1 and len(value) > 1 and value[1:2] == ":":
        return False
    if value[len(scheme) :].startswith("://"):
        return True
    return scheme in OPAQUE_URI_SCHEMES


def local_file_path_argument_guard(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Reject URI arguments before local file path grounding."""

    if not is_file_read_tool(base_tool_name(tool_name)):
        return None
    path = str(arguments.get("path") or "").strip()
    if not path:
        return None
    if not _is_unambiguous_uri_path(path):
        return None
    parsed = urlsplit(path)
    return {
        "success": False,
        "error": "read_file only accepts local filesystem paths.",
        "data": {
            "code": "invalid_local_file_path",
            "error_code": "invalid_local_file_path",
            "path": path,
            "scheme": parsed.scheme.lower(),
            "reroute_allowed": False,
        },
    }


def _file_tool_raw_path_guard(
    task_state: Any,
    tool_name: str,
    arguments: dict[str, Any],
    raw_arguments: str,
) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    if not (is_file_read_tool(base_tool) or is_file_write_tool(base_tool)):
        return None
    operation = "read" if is_file_read_tool(base_tool) else "write"
    if operation == "write":
        return _file_write_path_zone_guard(task_state, tool_name, arguments, raw_arguments)
    raw_text = "\n".join(
        item
        for item in (
            getattr(task_state, "user_goal", ""),
            raw_arguments,
            str(arguments.get("raw_requested_path") or ""),
            str(arguments.get("requested_path") or ""),
            str(arguments.get("path") or ""),
        )
        if item
    )
    decision = FileAccessPolicy().preflight_raw_path(raw_text, operation=operation, requested_path=str(arguments.get("path") or ""))
    if decision.allowed:
        return None
    data = decision.to_dict()
    data["message"] = "当前安全策略不允许访问包含路径跳转的路径。"
    return {"success": False, "error": data["message"], "data": data}


def _sanitize_write_file_arguments(task_state: Any, arguments: dict[str, Any]) -> None:
    """Preserve the model's write arguments; safety guards inspect them later."""

    del task_state, arguments


def _sanitize_write_file_content(content: str) -> str:
    return content


def _file_write_policy_guard(task_state: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    if base_tool == "write_file":
        target = resolve_output_target(
            str(arguments.get("path") or ""),
            filename=arguments.get("filename") if isinstance(arguments.get("filename"), str) else None,
            raw_requested_path=_raw_write_requested_path(task_state, arguments),
            allow_agent_internal=_allow_agent_internal_write(task_state, str(arguments.get("path") or "")),
        )
    elif base_tool == "replace_in_file":
        target = resolve_existing_write_target(
            str(arguments.get("path") or ""),
            raw_requested_path=_raw_write_requested_path(task_state, arguments),
            allow_agent_internal=_allow_agent_internal_write(task_state, str(arguments.get("path") or "")),
        )
    else:
        return None
    if target.target_type != "denied" and target.safe_path:
        return None
    data = {
        "code": target.code,
        "reason": target.reason,
        "requested_path": target.requested_path,
        "operation": "write",
        "scope": "denied_agent_access" if target.code == "agent_access_mode_read_only" else "denied_outside_roots",
    }
    data["message"] = _file_preflight_message(target.code, target.requested_path)
    return {"success": False, "error": data["message"], "data": data}


def _allow_agent_internal_write(task_state: Any, path: str | None = None) -> bool:
    profile = getattr(task_state, "task_profile", None)
    if profile is None:
        return False
    if getattr(profile, "needs_file_output", False) or getattr(profile, "is_file_output_only", False):
        return False
    if not _llm_intent_profile_source(task_state, profile):
        return False
    if not _structured_coding_modify_profile(profile):
        return False
    if not _tool_plan_allows_coding_internal_write(task_state):
        return False
    if path and not _coding_safety_allows_path(path):
        return False
    return True


def _llm_intent_profile_source(task_state: Any, profile: Any) -> bool:
    metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    source = str(getattr(profile, "profile_source", "") or metadata.get("intent_profile_source") or "")
    return source in {"llm_intent_primary", "llm_intent_recovery"}


def _structured_coding_modify_profile(profile: Any) -> bool:
    action = str(getattr(profile, "coding_action", "") or "").strip().lower()
    if action not in EDIT_ACTIONS:
        return False
    if not bool(getattr(profile, "is_coding_task", False) and getattr(profile, "needs_code_edit", False)):
        return False
    if str(getattr(profile, "structured_intent_type", "") or "") not in {"coding", ""}:
        return False
    if str(getattr(profile, "structured_workflow_kind", "") or "") not in {"coding", ""}:
        return False
    return True


def _tool_plan_allows_coding_internal_write(task_state: Any) -> bool:
    metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    plan = metadata.get("tool_plan")
    if not isinstance(plan, dict):
        routing = metadata.get("capability_routing")
        if isinstance(routing, dict):
            raw_plan = routing.get("tool_plan")
            plan = raw_plan if isinstance(raw_plan, dict) else {}
        else:
            plan = {}
    routing = metadata.get("capability_routing")
    routing = routing if isinstance(routing, dict) else {}
    capabilities = {
        str(plan.get("primary_capability") or ""),
        *_string_set(plan.get("supporting_capabilities")),
        *_string_set(plan.get("fallback_capabilities")),
        *_string_set(routing.get("required_capabilities")),
        *_string_set(routing.get("optional_capabilities")),
    }
    tools = {
        str(plan.get("primary_tool") or ""),
        *_string_set(plan.get("tool_priority")),
        *_string_set(plan.get("supporting_tool_priority")),
        *_string_set(plan.get("fallback_tool_priority")),
    }
    blocked_capabilities = {
        *_string_set(plan.get("blocked_capabilities")),
        *_string_set(routing.get("blocked_capabilities")),
    }
    blocked_tools = {base_tool_name(tool) for tool in _string_set(plan.get("blocked_tools"))}
    return (
        "coding" in capabilities
        and not blocked_capabilities.intersection({"coding", "file_write"})
        and any(is_file_write_tool(base_tool_name(tool)) for tool in tools)
        and not any(is_file_write_tool(tool) for tool in blocked_tools)
    )


def _coding_safety_allows_path(path: str) -> bool:
    return not CodingSafetyPolicy().is_protected_path(path)


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item or "").strip()}


def _research_python_guard(task_state: Any, base_tool: str) -> dict[str, Any] | None:
    """Defensive legacy guard only; old Python tools are not Agent-visible."""

    if getattr(task_state, "task_type", "") == "research" and base_tool in {"run_python_code", "run_python_in_sandbox"}:
        return {
            "success": False,
            "error": "Research tasks must use web_search or fetch_url for external information; Python execution cannot replace Web Tools.",
            "data": {"code": "research_python_not_allowed"},
        }
    return None


def _sandbox_guard(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Validate the structured shape of host command arguments."""
    base_tool = base_tool_name(tool_name)
    if base_tool in {"sandbox_exec", "run_command", "run_shell_in_sandbox"}:
        command = arguments.get("command", arguments.get("cmd"))
        if not isinstance(command, str) or not command.strip():
            return {
                "success": False,
                "data": {
                    "code": "invalid_command",
                    "boundary": "command_arguments",
                },
                "error": "command must be a non-empty string.",
            }
        cwd = arguments.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            return {
                "success": False,
                "data": {"code": "invalid_cwd", "boundary": "command_arguments"},
                "error": "cwd must be a string.",
            }
        timeout = arguments.get("timeout")
        if timeout is not None:
            try:
                value = int(timeout)
            except (TypeError, ValueError):
                value = 0
            if value < 1 or value > 300:
                return {
                    "success": False,
                    "data": {"code": "invalid_timeout", "boundary": "command_arguments"},
                    "error": "timeout must be an integer from 1 to 300.",
                }
    if base_tool in {"run_python_code", "run_python_in_sandbox"}:
        code = str(arguments.get("code") or "")
        if not code.strip():
            return {"success": False, "data": {"code": "invalid_python_code"}, "error": "code must be a non-empty string."}
    return None


def _browser_guard(policy: BrowserPolicy, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    if not is_browser_tool(base_tool):
        return None
    decision = policy.check_url(str(arguments.get("url", "")))
    if not decision.get("allowed"):
        return {"success": False, "data": {"policy": decision, "code": decision.get("code", "browser_url_blocked")}, "error": decision.get("reason", "Browser URL rejected.")}
    if base_tool == "browser_screenshot":
        screenshot = policy.check_screenshot()
        if not screenshot.get("allowed"):
            return {"success": False, "data": {"policy": screenshot, "code": screenshot.get("code", "browser_screenshot_blocked")}, "error": screenshot.get("reason", "Screenshot rejected.")}
    if base_tool == "browser_fill_form":
        fields = arguments.get("fields", {})
        if not isinstance(fields, dict):
            return {"success": False, "data": {"code": "invalid_arguments"}, "error": "fields must be a JSON object."}
        form = policy.check_form_fields(fields, arguments.get("submit_selector"))
        if not form.get("allowed"):
            return {"success": False, "data": {"policy": form, "code": form.get("code", "browser_form_blocked")}, "error": form.get("reason", "Form rejected.")}
    return None


def _database_guard(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    base_tool = base_tool_name(tool_name)
    lowered = base_tool.lower()
    if not (lowered.startswith("database_") or lowered == "get_database_status"):
        return None
    if not is_database_read_tool(base_tool) or any(marker in lowered for marker in DATABASE_WRITE_MARKERS):
        return _database_blocked(base_tool, "database_write_blocked", "Database write tools are blocked by the execution boundary.")
    if base_tool == "database_select_query":
        validation = validate_read_only_sql(str(arguments.get("sql") or ""))
        if not validation.success:
            error = validation.error if isinstance(validation.error, dict) else {}
            return _database_blocked(base_tool, str(error.get("error_code") or error.get("code") or "database_query_not_read_only"), "This database tool only allows read-only queries.")
    return None


def _database_blocked(tool_name: str, code: str, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": reason,
        "data": {
            "code": code,
            "reason": "database_write",
            "blocked_tool": tool_name,
        },
    }


FILE_WRITE_PATH_CANDIDATE_KEYS = (
    "path",
    "output_path",
    "target_path",
    "file_path",
    "filename",
    "requested_path",
    "requested_output_path",
    "raw_requested_path",
    "raw_requested_output_path",
    "original_requested_path",
)
STRUCTURED_SIDE_EFFECT_PATH_CANDIDATE_KEYS = (
    "raw_requested_path",
    "original_requested_path",
    "raw_requested_output_path",
    "requested_output_path",
    "path",
    "output_path",
    "target_path",
    "file_path",
)
COMMAND_SIDE_EFFECT_ARGUMENT_KEYS = ("command", "cmd", "code")
_PROTECTED_PATH_ZONE_CODES = {
    "agent_self_protected_path_blocked",
    "agent_internal_protected_path_blocked",
}


def _collect_side_effect_file_write_candidates(task_state: Any, arguments: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_collect_structured_path_candidates(task_state, arguments))
    candidates.extend(_collect_command_side_effect_path_candidates(arguments))
    return _unique_path_candidates(candidates)


def _collect_structured_path_candidates(task_state: Any, arguments: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    del task_state

    for key in STRUCTURED_SIDE_EFFECT_PATH_CANDIDATE_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
        elif isinstance(value, list):
            candidates.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    return candidates


def _collect_command_side_effect_path_candidates(arguments: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    command = str(arguments.get("command") or arguments.get("cmd") or "")
    if command:
        candidates.extend(_extract_shell_side_effect_paths(command))
        candidates.extend(_extract_python_side_effect_paths(command))
    code = str(arguments.get("code") or "")
    if code:
        candidates.extend(_extract_python_side_effect_paths(code))
    return candidates


def _command_side_effect_context(arguments: dict[str, Any]) -> str:
    return "\n".join(
        str(arguments.get(key) or "")
        for key in COMMAND_SIDE_EFFECT_ARGUMENT_KEYS
        if isinstance(arguments.get(key), str) and str(arguments.get(key) or "").strip()
    )


def _extract_shell_side_effect_paths(command: str) -> list[str]:
    candidates: list[str] = []
    for pattern in (
        r"(?:^|[\s;&|])(?:\d?>?>|&>)\s*(?P<path>\"[^\"]+\"|'[^']+'|[^\s;&|]+)",
        r"(?:^|[\s;&|])tee(?:\s+-[A-Za-z]+)*\s+(?P<path>\"[^\"]+\"|'[^']+'|[^\s;&|]+)",
    ):
        for match in re.finditer(pattern, command):
            candidate = _clean_candidate_path(match.group("path"))
            if candidate:
                candidates.append(candidate)

    for tokens in _split_shell_commands(command):
        if not tokens:
            continue
        executable = tokens[0].rsplit("/", 1)[-1]
        if executable == "touch":
            candidates.extend(_shell_path_operands(tokens[1:]))
        elif executable == "sed" and any(token == "-i" or token.startswith("-i") for token in tokens[1:]):
            path_operands = _shell_path_operands(tokens[1:])
            if path_operands:
                candidates.append(path_operands[-1])
        elif executable == "rm":
            candidates.extend(_shell_path_operands(tokens[1:]))
        elif executable in {"mv", "cp"}:
            path_operands = _shell_path_operands(tokens[1:])
            if path_operands:
                candidates.append(path_operands[-1])
    return candidates


def _split_shell_commands(command: str) -> list[list[str]]:
    try:
        import shlex

        tokens = shlex.split(command)
    except ValueError:
        return []

    commands: list[list[str]] = []
    current: list[str] = []
    separators = {";", "&&", "||", "|"}
    for token in tokens:
        if token in separators:
            if current:
                commands.append(current)
                current = []
            continue
        current.append(token)
    if current:
        commands.append(current)
    return commands


def _shell_path_operands(tokens: list[str]) -> list[str]:
    operands: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == "--":
            continue
        if token in {">", ">>", "1>", "1>>", "2>", "2>>", "&>"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        cleaned = _clean_candidate_path(token)
        if cleaned:
            operands.append(cleaned)
    return operands


def _extract_python_side_effect_paths(code: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(
        r"\bopen\(\s*[\"'](?P<path>[^\"']+)[\"']\s*,\s*[\"'][^\"']*[wax+][^\"']*[\"']",
        code,
    ):
        candidates.append(match.group("path"))

    for match in re.finditer(
        r"(?:Path|pathlib\.Path)\(\s*[\"'](?P<path>[^\"']+)[\"']\s*\)\s*\.\s*(?:write_text|write_bytes|unlink|rename|replace)\(",
        code,
    ):
        candidates.append(match.group("path"))

    for match in re.finditer(r"\bos\.(?:remove|unlink)\(\s*[\"'](?P<path>[^\"']+)[\"']", code):
        candidates.append(match.group("path"))

    for match in re.finditer(
        r"\bshutil\.(?:copy|copy2|copyfile|move)\(\s*[\"'][^\"']+[\"']\s*,\s*[\"'](?P<path>[^\"']+)[\"']",
        code,
    ):
        candidates.append(match.group("path"))
    return candidates


def _unique_path_candidates(candidates: list[str]) -> list[str]:
    result: list[str] = []
    for candidate in candidates:
        candidate = _clean_candidate_path(candidate)
        if _is_url_like(candidate):
            continue
        if candidate not in result:
            result.append(candidate)
    return result


def _clean_candidate_path(value: str) -> str:
    text = str(value or "").strip().strip("。；;，,")
    text = text.strip("\"'")
    return text.rstrip(").]}")


def _is_url_like(value: str) -> bool:
    lowered = str(value or "").lower()
    return lowered.startswith(("http://", "https://", "file://")) or lowered.startswith("//")


def _collect_file_write_path_candidates(arguments: dict[str, Any], raw_arguments: str, task_state: Any) -> list[str]:
    del raw_arguments, task_state
    candidates: list[str] = []
    for key in FILE_WRITE_PATH_CANDIDATE_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    result: list[str] = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return result


def _current_write_tool_path(arguments: Mapping[str, Any]) -> str:
    for key in ("path", "output_path", "target_path", "file_path"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = arguments.get("filename")
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _same_safe_output_identity(current_path: str, candidate: str) -> bool:
    if not current_path or not candidate:
        return False
    if _raw_file_write_candidate_is_dangerous(candidate):
        return False
    current_identity = canonical_output_identity(current_path)
    candidate_identity = canonical_output_identity(candidate)
    return bool(current_identity and candidate_identity and current_identity == candidate_identity)


def _same_safe_output_leaf(current_path: str, candidate: str) -> bool:
    if not current_path or not candidate:
        return False
    if _raw_file_write_candidate_is_dangerous(candidate):
        return False
    current = str(current_path or "").strip().replace("\\", "/").strip("/")
    raw = str(candidate or "").strip().replace("\\", "/").strip("/")
    if not raw or "/" in raw or raw.startswith(("~", ".")):
        return False
    if _is_absolute_like_for_boundary(raw):
        return False
    current_parts = [part for part in current.split("/") if part]
    if not current_parts or current_parts[-1] != raw:
        return False
    return current_parts[0].lower() in {"desktop", "downloads", "exports"} or canonical_output_identity(current_path) != ""


def _raw_file_write_candidate_is_dangerous(candidate: str) -> bool:
    text = str(candidate or "").strip()
    if not text:
        return False
    decision = evaluate_file_output_path_zone(
        requested_path=text,
        raw_requested_path=text,
        allow_agent_internal=False,
        default_relative_to_output_root=False,
    )
    if decision.code == "agent_self_protected_path_blocked":
        return True
    if decision.code == "agent_internal_protected_path_blocked":
        return _looks_like_agent_internal_relative_request_for_boundary(text) or _is_absolute_like_for_boundary(text)
    return False


def _looks_like_agent_internal_relative_request_for_boundary(value: str) -> bool:
    if _is_absolute_like_for_boundary(value) or _path_has_traversal(value):
        return False
    parts = [part for part in str(value or "").strip().replace("\\", "/").lstrip("./").split("/") if part]
    if not parts:
        return False
    return parts[0].lower() in PROJECT_PROTECTED_RELATIVE_ROOTS or parts[0] in PROJECT_PROTECTED_ROOT_FILES


def _is_absolute_like_for_boundary(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith(("/", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", text))


def _raw_write_requested_path(task_state: Any, arguments: dict[str, Any]) -> str:
    parsed_path = _current_write_tool_path(arguments)
    del task_state
    for value in (
        arguments.get("raw_requested_path"),
        arguments.get("original_requested_path"),
    ):
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = value.strip()
        if _same_safe_output_identity(parsed_path, candidate):
            continue
        if _raw_file_write_candidate_is_dangerous(candidate):
            return candidate
    return parsed_path


def _file_preflight_message(code: str, requested_path: str = "") -> str:
    if code == "agent_access_mode_read_only":
        return "当前 Agent 权限模式为 read_only，不允许写入、修改或删除文件。"
    if code == "agent_self_protected_path_blocked":
        return "运行时 Agent 不允许修改自身系统项目内部文件。"
    if code == "agent_internal_protected_path_blocked":
        return "普通文件输出不能写入 Agent 项目内部非输出区。"
    return "当前宿主路径无效或无法访问。"


def _compat_path_zone_code(code: str) -> str:
    return code


def _boundary_for_tool(base_tool: str) -> str:
    if is_file_write_tool(base_tool):
        return "file_write"
    if is_file_read_tool(base_tool):
        return "file_read"
    if is_browser_tool(base_tool):
        return "browser_url"
    if is_execution_tool(base_tool):
        return "sandbox"
    if is_database_read_tool(base_tool) or base_tool.startswith("database_"):
        return "database"
    return "generic_tool"


def _boundary_for_risk_metadata(risk_metadata: ToolRiskMetadata, base_tool: str) -> str:
    categories = tuple(risk_metadata.boundary_categories)
    if "tool_authorization" in categories and risk_metadata.source in {"mcp", "mcp_filesystem"}:
        return "tool_risk_registry"
    if "file_write" in categories:
        return "file_write"
    if "file_read" in categories:
        return "file_read"
    if "browser_url" in categories:
        return "browser_url"
    if "sandbox" in categories:
        return "sandbox"
    if "database" in categories:
        return "database"
    if "mcp" in categories or "mcp_filesystem" in categories:
        return "tool_risk_registry"
    return _boundary_for_tool(base_tool)


def _boundary_from_guard(code: str, tool_name: str, data: dict[str, Any]) -> str:
    base_tool = base_tool_name(tool_name)
    if code in {
        "tool_risk_metadata_missing",
        "mcp_tool_blocked_by_access_mode",
        "mcp_tool_risk_not_authorized",
        "execution_payload_git_mutation_not_authorized",
    }:
        return "tool_risk_registry"
    if code == "tool_execution_not_authorized":
        return "tool_authorization"
    if code.startswith("database_") or base_tool.startswith("database_"):
        return "database"
    if code in {
        "url_tool_policy_blocked",
        "blocked_localhost",
        "blocked_private_ip",
        "blocked_dns_private_ip",
        "blocked_dns_resolution_failed",
        "blocked_scheme",
        "unsupported_scheme",
        "empty_url",
        "missing_hostname",
        "dns_resolution_failed",
    }:
        return "browser_url"
    if is_browser_tool(base_tool) or "policy" in data and isinstance(data.get("policy"), dict) and str(data["policy"].get("code", "")).startswith("browser"):
        return "browser_url"
    if code in {
        "path_empty",
        "agent_self_protected_path_blocked",
        "agent_internal_protected_path_blocked",
    }:
        return "file_read" if is_file_read_tool(base_tool) else "file_write"
    if code in {"file_output_requires_write_file", "dangerous_python", "dangerous_python_subprocess", "research_python_not_allowed"} or is_execution_tool(base_tool):
        return "sandbox"
    if is_file_write_tool(base_tool):
        return "file_write"
    if is_file_read_tool(base_tool):
        return "file_read"
    return _boundary_for_tool(base_tool)


def _safe_argument_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in ("content", "secret", "token", "authorization", "password", "api_key", "apikey", "credential")):
            summary[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = _safe_text(str(value), 160) if isinstance(value, str) else value
        else:
            summary[key] = f"[{type(value).__name__}]"
    return summary


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = str(key).lower()
        if key in ARGUMENT_SNAPSHOT_PASSTHROUGH_METADATA_KEYS:
            if isinstance(value, list):
                safe[key] = [_safe_text(str(item), 160) for item in value[:20]]
            elif isinstance(value, str):
                safe[key] = _safe_text(value, 240)
            else:
                safe[key] = value
        elif lowered in {"original_observation"}:
            safe[key] = value
        elif any(marker in lowered for marker in ("raw", "content", "secret", "token", "authorization", "password", "api_key", "apikey", "credential", "sql")):
            safe[key] = "[redacted]"
        elif isinstance(value, dict):
            safe[key] = _safe_metadata(value)
        elif isinstance(value, list):
            safe[key] = [_safe_metadata(item) if isinstance(item, dict) else _safe_text(str(item), 160) for item in value[:20]]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = _safe_text(value, 240) if isinstance(value, str) else value
        else:
            safe[key] = _safe_text(str(value), 160)
    return safe


def _safe_text(value: str, limit: int) -> str:
    text = str(value or "")
    for marker in ("Authorization:", "Bearer ", "SECRET=", "TOKEN=", "API_KEY=", "PASSWORD="):
        if marker in text:
            text = text.replace(marker, "[redacted]")
    return text[:limit]
