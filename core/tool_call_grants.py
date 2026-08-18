"""Task-scoped execution grants for structured provider ToolCalls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping


LEDGER_METADATA_KEY = "tool_call_grant_ledger"
GRANT_PENDING = "pending"
GRANT_EXECUTING = "executing"
GRANT_COMPLETED = "completed"
GRANT_FAILED = "failed"
GRANT_BLOCKED = "blocked"
TERMINAL_GRANT_STATES = frozenset({GRANT_COMPLETED, GRANT_BLOCKED})


@dataclass(frozen=True)
class ToolCallExecutionGrant:
    task_id: str
    call_id: str
    provider_call_id: str
    canonical_name: str
    executable_name: str
    arguments_fingerprint: str
    registered_arguments_fingerprint: str
    source: str
    step_index: int
    status: str = GRANT_PENDING
    attempts: int = 0
    raw_arguments: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCallGrantDecision:
    allowed: bool
    reason: str
    task_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    step_index: int = 0
    status: str = ""
    fingerprint_match: bool = False
    grant: dict[str, Any] | None = None

    def trace_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "step_index": self.step_index,
            "grant_status": self.status,
            "allowed": self.allowed,
            "reason": self.reason,
            "fingerprint_match": self.fingerprint_match,
        }


def stable_tool_arguments_fingerprint(arguments: Mapping[str, Any] | None) -> str:
    """Return a stable digest for sanitized JSON-compatible arguments."""

    payload = json.dumps(
        _json_safe(arguments or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def register_tool_call_grant(
    task_state: Any,
    *,
    call_id: str,
    provider_call_id: str,
    canonical_name: str,
    executable_name: str,
    arguments: Mapping[str, Any],
    source: str,
    step_index: int = 0,
    raw_arguments: str = "",
) -> ToolCallGrantDecision:
    """Register one trusted structured call in the current task ledger."""

    task_id = str(getattr(task_state, "task_id", "") or "").strip()
    call_key = str(call_id or provider_call_id or "").strip()
    provider_key = str(provider_call_id or call_key).strip()
    canonical = str(canonical_name or executable_name or "").strip()
    executable = str(executable_name or canonical).strip()
    call_source = str(source or "").strip()
    fingerprint = stable_tool_arguments_fingerprint(arguments)
    if not task_id or not call_key or not canonical or not executable:
        return _decision(False, "grant_identity_incomplete", task_id, call_key, executable, step_index)
    if call_source != "structured":
        return _decision(False, "grant_requires_structured_tool_call", task_id, call_key, executable, step_index)
    ledger = _ledger(task_state)
    existing = ledger.get(call_key)
    if isinstance(existing, dict):
        identity_matches = (
            str(existing.get("task_id") or "") == task_id
            and str(existing.get("canonical_name") or "") == canonical
            and str(existing.get("executable_name") or "") == executable
            and str(existing.get("registered_arguments_fingerprint") or "") == fingerprint
        )
        return _decision(
            identity_matches,
            "grant_already_registered" if identity_matches else "grant_identity_conflict",
            task_id,
            call_key,
            executable,
            int(existing.get("step_index") or step_index or 0),
            status=str(existing.get("status") or ""),
            fingerprint_match=identity_matches,
            grant=existing,
        )

    grant = ToolCallExecutionGrant(
        task_id=task_id,
        call_id=call_key,
        provider_call_id=provider_key,
        canonical_name=canonical,
        executable_name=executable,
        arguments_fingerprint=fingerprint,
        registered_arguments_fingerprint=fingerprint,
        source=call_source,
        step_index=int(step_index or 0),
        raw_arguments=str(raw_arguments or ""),
    ).to_dict()
    ledger[call_key] = grant
    return _decision(
        True,
        "grant_registered",
        task_id,
        call_key,
        executable,
        int(step_index or 0),
        status=GRANT_PENDING,
        fingerprint_match=True,
        grant=grant,
    )


def check_tool_call_grant(
    task_state: Any,
    *,
    call_id: str,
    canonical_name: str,
    executable_name: str,
    parsed_arguments: Mapping[str, Any],
    sanitized_arguments: Mapping[str, Any],
) -> ToolCallGrantDecision:
    """Validate and activate a concrete call using its final sanitized arguments."""

    task_id = str(getattr(task_state, "task_id", "") or "").strip()
    call_key = str(call_id or "").strip()
    executable = str(executable_name or canonical_name or "").strip()
    grant = _ledger(task_state).get(call_key)
    if not isinstance(grant, dict):
        return _decision(False, "tool_call_grant_missing", task_id, call_key, executable, 0)
    if str(grant.get("task_id") or "") != task_id:
        return _decision(False, "tool_call_grant_task_mismatch", task_id, call_key, executable, int(grant.get("step_index") or 0), grant=grant)
    if str(grant.get("canonical_name") or "") != str(canonical_name or ""):
        return _decision(False, "tool_call_grant_canonical_name_mismatch", task_id, call_key, executable, int(grant.get("step_index") or 0), grant=grant)
    if str(grant.get("executable_name") or "") != executable:
        return _decision(False, "tool_call_grant_executable_name_mismatch", task_id, call_key, executable, int(grant.get("step_index") or 0), grant=grant)

    parsed_fingerprint = stable_tool_arguments_fingerprint(parsed_arguments)
    sanitized_fingerprint = stable_tool_arguments_fingerprint(sanitized_arguments)
    registered_fingerprint = str(grant.get("registered_arguments_fingerprint") or "")
    if parsed_fingerprint != registered_fingerprint:
        return _decision(
            False,
            "tool_call_grant_arguments_mismatch",
            task_id,
            call_key,
            executable,
            int(grant.get("step_index") or 0),
            status=str(grant.get("status") or ""),
            fingerprint_match=False,
            grant=grant,
        )
    status = str(grant.get("status") or GRANT_PENDING)
    if status in TERMINAL_GRANT_STATES:
        return _decision(False, "tool_call_grant_already_consumed", task_id, call_key, executable, int(grant.get("step_index") or 0), status=status, fingerprint_match=True, grant=grant)

    grant["arguments_fingerprint"] = sanitized_fingerprint
    grant["status"] = GRANT_EXECUTING
    grant["attempts"] = int(grant.get("attempts") or 0) + 1
    return _decision(
        True,
        "tool_call_grant_allowed",
        task_id,
        call_key,
        executable,
        int(grant.get("step_index") or 0),
        status=GRANT_EXECUTING,
        fingerprint_match=True,
        grant=grant,
    )


def mark_tool_call_grant_state(task_state: Any, call_id: str, status: str) -> ToolCallGrantDecision:
    """Move an existing grant to an execution result state."""

    task_id = str(getattr(task_state, "task_id", "") or "").strip()
    call_key = str(call_id or "").strip()
    grant = _ledger(task_state).get(call_key)
    if not isinstance(grant, dict):
        return _decision(False, "tool_call_grant_missing", task_id, call_key, "", 0)
    normalized = str(status or "").strip().lower()
    if normalized not in {GRANT_PENDING, GRANT_EXECUTING, GRANT_COMPLETED, GRANT_FAILED, GRANT_BLOCKED}:
        return _decision(False, "tool_call_grant_status_invalid", task_id, call_key, str(grant.get("executable_name") or ""), int(grant.get("step_index") or 0), grant=grant)
    grant["status"] = normalized
    return _decision(
        True,
        "tool_call_grant_state_updated",
        task_id,
        call_key,
        str(grant.get("executable_name") or ""),
        int(grant.get("step_index") or 0),
        status=normalized,
        fingerprint_match=True,
        grant=grant,
    )


def refresh_executing_tool_call_grant_arguments(
    task_state: Any,
    *,
    call_id: str,
    previous_arguments: Mapping[str, Any],
    final_arguments: Mapping[str, Any],
) -> ToolCallGrantDecision:
    """Record a deterministic runtime projection in an executing grant."""

    task_id = str(getattr(task_state, "task_id", "") or "").strip()
    call_key = str(call_id or "").strip()
    grant = _ledger(task_state).get(call_key)
    if not isinstance(grant, dict):
        return _decision(False, "tool_call_grant_missing", task_id, call_key, "", 0)
    tool_name = str(grant.get("executable_name") or "")
    step_index = int(grant.get("step_index") or 0)
    if str(grant.get("status") or "") != GRANT_EXECUTING:
        return _decision(False, "tool_call_grant_not_executing", task_id, call_key, tool_name, step_index, grant=grant)
    previous_fingerprint = stable_tool_arguments_fingerprint(previous_arguments)
    if str(grant.get("arguments_fingerprint") or "") != previous_fingerprint:
        return _decision(False, "tool_call_grant_runtime_projection_mismatch", task_id, call_key, tool_name, step_index, grant=grant)
    grant["arguments_fingerprint"] = stable_tool_arguments_fingerprint(final_arguments)
    return _decision(
        True,
        "tool_call_grant_runtime_arguments_refreshed",
        task_id,
        call_key,
        tool_name,
        step_index,
        status=GRANT_EXECUTING,
        fingerprint_match=True,
        grant=grant,
    )
def get_tool_call_grant(task_state: Any, call_id: str) -> dict[str, Any] | None:
    grant = _ledger(task_state).get(str(call_id or "").strip())
    return dict(grant) if isinstance(grant, dict) else None


def _ledger(task_state: Any) -> dict[str, dict[str, Any]]:
    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        setattr(task_state, "metadata", metadata)
    ledger = metadata.get(LEDGER_METADATA_KEY)
    if not isinstance(ledger, dict):
        ledger = {}
        metadata[LEDGER_METADATA_KEY] = ledger
    return ledger


def _decision(
    allowed: bool,
    reason: str,
    task_id: str,
    call_id: str,
    tool_name: str,
    step_index: int,
    *,
    status: str = "",
    fingerprint_match: bool = False,
    grant: dict[str, Any] | None = None,
) -> ToolCallGrantDecision:
    return ToolCallGrantDecision(
        allowed=allowed,
        reason=reason,
        task_id=task_id,
        call_id=call_id,
        tool_name=tool_name,
        step_index=step_index,
        status=status,
        fingerprint_match=fingerprint_match,
        grant=dict(grant) if isinstance(grant, dict) else None,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


__all__ = [
    "GRANT_BLOCKED",
    "GRANT_COMPLETED",
    "GRANT_EXECUTING",
    "GRANT_FAILED",
    "GRANT_PENDING",
    "LEDGER_METADATA_KEY",
    "ToolCallExecutionGrant",
    "ToolCallGrantDecision",
    "check_tool_call_grant",
    "get_tool_call_grant",
    "mark_tool_call_grant_state",
    "refresh_executing_tool_call_grant_arguments",
    "register_tool_call_grant",
    "stable_tool_arguments_fingerprint",
]
