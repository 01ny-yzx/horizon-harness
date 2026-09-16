"""ToolObservation v1 for normalized tool execution results."""

from __future__ import annotations

from collections.abc import Mapping
import json
import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from config.settings import settings
from core.document_result_compaction import compact_document_result
from core.observation_compaction import bounded_read_file_page, compact_observation_for_model
from core.source_reference import resolve_source_file_metadata
from core.tool_result_store import ToolResultStore, resolve_tool_artifact_metadata
from core.tool_call_schema import ToolCallEnvelope
from core.unicode_safety import sanitize_unicode


MAX_TEXT_CHARS = 4000
MAX_MODEL_TEXT_CHARS = 1200


class ToolObservationStatus:
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    ERROR = "error"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    DENIED = "denied"
    CANCELLED = "cancelled"


NON_SUCCESS_OBSERVATION_STATUSES = frozenset({
    ToolObservationStatus.FAILED,
    ToolObservationStatus.BLOCKED,
    ToolObservationStatus.ERROR,
    ToolObservationStatus.REJECTED,
    ToolObservationStatus.DENIED,
    ToolObservationStatus.CANCELLED,
})
RECOVERABLE_OBSERVATION_ERROR_CODES = frozenset({"tool_resource_incompatible"})


class ToolObservationKind:
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    EXECUTION = "execution"
    WEB_READ = "web_read"
    BROWSER = "browser"
    DATABASE = "database"
    MCP = "mcp"
    MEMORY = "memory"
    RAG = "rag"
    GIT = "git"
    INTERNAL = "internal"
    GENERIC = "generic"
    UNKNOWN = "unknown"


@dataclass
class ToolObservation:
    observation_id: str
    call_id: str
    provider_call_id: str
    tool_name: str
    canonical_name: str
    executable_name: str
    status: str
    kind: str
    success: bool
    error: str = ""
    error_code: str = ""
    recoverable: bool = False
    recovery_reason: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    output_text: str = ""
    output_path: str = ""
    url: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    blocked_by: str = ""
    policy_code: str = ""
    source_ref: str = ""
    source_chars: int | None = None
    source_bytes: int = 0
    source_sha256: str = ""
    source_kind: str = ""
    source_mime: str = ""
    source_encoding: str | None = None
    source_is_text: bool = False
    content_ref: str = ""
    content_chars: int = 0
    content_bytes: int = 0
    content_sha256: str = ""
    content_externalized: bool = False
    preview_chars: int = 0
    output_text_chars: int = 0
    compacted: bool = False
    timestamp: str = ""


def normalize_tool_result(envelope: ToolCallEnvelope, raw_result: Any) -> ToolObservation:
    if _is_observation_payload(raw_result):
        payload = dict(raw_result)
        raw_result_value = payload.get("data")
    else:
        payload = {"success": True, "data": raw_result}
        raw_result_value = raw_result
    normalized_result = sanitize_unicode(raw_result_value)
    raw_data = {"result": normalized_result}
    if isinstance(normalized_result, dict):
        raw_data.update(normalized_result)
    tool = str(envelope.executable_name or envelope.tool_name or envelope.raw_name or "").split(".")[-1]
    tool_arguments = _arguments_dict(envelope)
    if tool in {"read_file", "read_document"}:
        _partition_legacy_read_metadata(
            tool,
            payload,
            raw_data,
            argument_path=str(tool_arguments.get("path") or ""),
        )
    preliminary_success = is_successful_observation(
        {**payload, "status": payload.get("status") or raw_data.get("status") or ""}
    )
    if tool in {"read_file", "read_document"} and not preliminary_success:
        raw_data = _failed_read_data(envelope, payload, raw_data)
    external = _externalize_tool_result(envelope, payload, raw_data)
    reference_error_code = str(
        external.get("source_error_code")
        or external.get("artifact_error_code")
        or raw_data.get("source_error_code")
        or raw_data.get("artifact_error_code")
        or ""
    )
    if reference_error_code:
        return make_error_observation(
            envelope,
            "Referenced tool result failed integrity validation.",
            data={"code": reference_error_code},
        )
    data = finalize_model_visible_tool_data(
        tool,
        sanitize_unicode(raw_data),
        max_chars=MAX_MODEL_TEXT_CHARS,
        successful=preliminary_success,
    )
    metadata = (
        _safe_failed_read_metadata(tool, payload.get("metadata"))
        if tool in {"read_file", "read_document"} and not preliminary_success
        else _json_dict(payload.get("metadata", {}))
    )
    if tool_arguments:
        metadata.setdefault("tool_arguments", tool_arguments)
    failed_read = tool in {"read_file", "read_document"} and not preliminary_success
    success = False if failed_read else payload.get("success") is True
    error = str(data.get("error") or "") if failed_read else str(payload.get("error") or "")
    error_code = (
        str(data.get("error_code") or "")
        if failed_read
        else str(payload.get("error_code") or data.get("code") or data.get("error_code") or "")
    )
    recoverable = bool(data.get("recoverable") is True or payload.get("recoverable") is True)
    recovery_reason = str(data.get("recovery_reason") or payload.get("recovery_reason") or "")
    exit_code = _int_or_none(data.get("exit_code", data.get("returncode")))
    if exit_code is not None and exit_code != 0 and success:
        success = False
    explicit_status = str(data.get("status") if failed_read else (payload.get("status") or data.get("status") or "")).strip().lower()
    if explicit_status in {
        ToolObservationStatus.SUCCESS,
        ToolObservationStatus.FAILED,
        ToolObservationStatus.BLOCKED,
        ToolObservationStatus.ERROR,
        ToolObservationStatus.SKIPPED,
        ToolObservationStatus.REJECTED,
        ToolObservationStatus.DENIED,
        ToolObservationStatus.CANCELLED,
    }:
        status = explicit_status
        success = status == ToolObservationStatus.SUCCESS
    else:
        status = ToolObservationStatus.SUCCESS if success else ToolObservationStatus.FAILED
    bounded_read_page = bounded_read_file_page(data) if tool == "read_file" else None
    output_text = (
        ""
        if tool in {"read_file", "read_document"} and not preliminary_success
        else str(bounded_read_page.get("content") or "")
        if bounded_read_page is not None
        else build_observation_output_text(data, metadata, max_chars=MAX_MODEL_TEXT_CHARS)
    )
    return _observation(
        envelope,
        status=status,
        success=success,
        error=error,
        error_code=error_code,
        recoverable=recoverable,
        recovery_reason=recovery_reason,
        message=str(payload.get("message") or data.get("message") or ""),
        data=data,
        metadata=metadata,
        output_text=output_text,
        output_path=str(data.get("path") or data.get("output_path") or data.get("download_url") or tool_arguments.get("path") or ""),
        url=str(data.get("url") or tool_arguments.get("url") or ""),
        exit_code=exit_code,
        stdout=str(data.get("stdout") or ""),
        stderr=str(data.get("stderr") or ""),
        blocked_by=str(payload.get("blocked_by") or data.get("blocked_by") or ""),
        policy_code=str(payload.get("policy_code") or data.get("policy_code") or (data.get("code") if status == ToolObservationStatus.BLOCKED else "") or ""),
        source_ref=str(external.get("source_ref") or data.get("source_ref") or ""),
        source_chars=_optional_int(external["source_chars"] if "source_chars" in external else data.get("source_chars")),
        source_bytes=int(external.get("source_bytes") or data.get("source_bytes") or 0),
        source_sha256=str(external.get("source_sha256") or data.get("source_sha256") or ""),
        source_kind=str(external.get("source_kind") or data.get("source_kind") or ""),
        source_mime=str(external.get("source_mime") or data.get("source_mime") or ""),
        source_encoding=(external["source_encoding"] if "source_encoding" in external else data.get("source_encoding")),
        source_is_text=bool(external.get("source_is_text") if "source_is_text" in external else data.get("source_is_text")),
        content_ref=str(external.get("content_ref") or data.get("content_ref") or ""),
        content_chars=int(external.get("content_chars") or data.get("content_chars") or 0),
        content_bytes=int(external.get("content_bytes") or data.get("content_bytes") or 0),
        content_sha256=str(external.get("content_sha256") or data.get("content_sha256") or ""),
        content_externalized=bool(external.get("content_externalized") or data.get("content_externalized")),
        preview_chars=len(output_text),
        output_text_chars=len(output_text),
        compacted=bool(data.get("compacted")),
    )


def is_successful_observation(observation: ToolObservation | dict[str, Any]) -> bool:
    """Return true only for a genuinely completed successful observation."""

    if isinstance(observation, ToolObservation):
        return observation.success is True and str(observation.status or "").strip().lower() == ToolObservationStatus.SUCCESS
    if not isinstance(observation, dict):
        return False
    status = str(observation.get("status") or "").strip().lower()
    if status in NON_SUCCESS_OBSERVATION_STATUSES:
        return False
    if status and status not in {ToolObservationStatus.SUCCESS, "completed", "ok"}:
        return False
    return observation.get("success") is True


def is_recoverable_observation(observation: ToolObservation | dict[str, Any]) -> bool:
    """Return true only for explicitly declared non-policy recovery facts."""

    if isinstance(observation, ToolObservation):
        success = observation.success
        recoverable = observation.recoverable
        error_code = observation.error_code
        status = observation.status
        policy_code = observation.policy_code
    elif isinstance(observation, dict):
        data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        success = observation.get("success") is True
        recoverable = bool(
            observation.get("recoverable") is True
            or data.get("recoverable") is True
            or result.get("recoverable") is True
        )
        error_code = str(
            observation.get("error_code")
            or data.get("error_code")
            or result.get("error_code")
            or ""
        )
        status = str(observation.get("status") or data.get("status") or result.get("status") or "")
        policy_code = str(
            observation.get("policy_code")
            or data.get("policy_code")
            or result.get("policy_code")
            or ""
        )
    else:
        return False
    return bool(
        not success
        and recoverable
        and str(status or "").strip().lower()
        not in {
            ToolObservationStatus.BLOCKED,
            ToolObservationStatus.DENIED,
            ToolObservationStatus.REJECTED,
            ToolObservationStatus.CANCELLED,
        }
        and not str(policy_code or "").strip()
    )


def observation_result(observation: ToolObservation | dict[str, Any]) -> Any:
    data = observation.data if isinstance(observation, ToolObservation) else observation.get("data")
    if isinstance(data, dict) and "result" in data:
        return data.get("result")
    return data


def observation_result_mapping(observation: ToolObservation | dict[str, Any]) -> dict[str, Any]:
    value = observation_result(observation)
    return value if isinstance(value, dict) else {}


def observation_result_sequence(observation: ToolObservation | dict[str, Any]) -> list[Any]:
    value = observation_result(observation)
    return value if isinstance(value, list) else []


def _is_observation_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if "success" in value:
        return True
    envelope_keys = {"status", "error", "error_code", "metadata", "policy_code", "blocked_by", "message"}
    return "data" in value and bool(envelope_keys.intersection(value))


def make_blocked_observation(
    envelope: ToolCallEnvelope,
    reason: str,
    error: str,
    data: dict[str, Any] | None = None,
) -> ToolObservation:
    payload = _json_dict(data or {})
    policy_code = str(payload.get("code") or reason)
    return _observation(
        envelope,
        status=ToolObservationStatus.BLOCKED,
        success=False,
        error=error,
        error_code=policy_code,
        message=str(payload.get("message") or error),
        data={**payload, "code": policy_code, "reason": str(payload.get("reason") or reason)},
        blocked_by=str(payload.get("blocked_by") or "runtime"),
        policy_code=policy_code,
    )


def make_boundary_blocked_observation(envelope: ToolCallEnvelope, boundary_decision: Any, legacy_block: dict[str, Any]) -> ToolObservation:
    data = _json_dict(legacy_block.get("data", {}))
    data.setdefault("blocked_by", "execution_boundary")
    code = str(getattr(boundary_decision, "code", "") or data.get("code") or "execution_boundary_blocked")
    data.setdefault("code", code)
    return make_blocked_observation(
        envelope,
        reason=code,
        error=str(legacy_block.get("error") or getattr(boundary_decision, "reason", "") or "Tool call blocked by execution boundary."),
        data=data,
    )


def make_error_observation(
    envelope: ToolCallEnvelope,
    error: str,
    data: dict[str, Any] | None = None,
) -> ToolObservation:
    payload = _json_dict(data or {})
    return _observation(
        envelope,
        status=ToolObservationStatus.ERROR,
        success=False,
        error=error,
        error_code=str(payload.get("code") or "tool_runtime_error"),
        message=error,
        data=payload,
        policy_code=str(payload.get("code") or ""),
    )


def make_permission_rejected_observation(
    envelope: ToolCallEnvelope,
    *,
    permission: str,
    pattern: str,
    reason: str,
    data: dict[str, Any] | None = None,
) -> ToolObservation:
    payload = {
        **_json_dict(data or {}),
        "code": "permission_rejected",
        "permission": permission,
        "patterns": [pattern],
        "permission_decision": "deny",
        "reason": reason,
        "tool_executed": False,
        "real_execution": False,
    }
    return _observation(
        envelope,
        status=ToolObservationStatus.REJECTED,
        success=False,
        error=f"Permission rejected: {permission} ({pattern}).",
        error_code="permission_rejected",
        message=reason,
        data=payload,
        blocked_by="permission",
        policy_code="permission_rejected",
    )


def make_skipped_observation(tool_call_or_envelope: Any, reason: str, terminal_kind: str) -> ToolObservation:
    envelope = tool_call_or_envelope if isinstance(tool_call_or_envelope, ToolCallEnvelope) else _envelope_from_provider_tool_call(tool_call_or_envelope)
    return _observation(
        envelope,
        status=ToolObservationStatus.SKIPPED,
        success=False,
        error="Tool call skipped because a previous tool completed the task.",
        error_code="tool_call_skipped_after_terminal_outcome",
        message=reason,
        data={
            "code": "tool_call_skipped_after_terminal_outcome",
            "reason": reason,
            "terminal_kind": terminal_kind,
            "skipped_tool": envelope.executable_name or envelope.tool_name or envelope.raw_name,
        },
    )


def observation_to_legacy_dict(observation: ToolObservation) -> dict[str, Any]:
    data = sanitize_unicode(dict(observation.data or {}))
    metadata = _trim_mapping(observation.metadata, MAX_TEXT_CHARS)
    args = metadata.get("tool_arguments") if isinstance(metadata.get("tool_arguments"), dict) else {}
    base_tool = str(observation.executable_name or observation.canonical_name or observation.tool_name or "").split(".")[-1]
    failed_read = base_tool in {"read_file", "read_document"} and not observation.success
    data.setdefault("status", observation.status)
    if not failed_read:
        data.setdefault("kind", observation.kind)
    if observation.output_path and not failed_read:
        data.setdefault("path", observation.output_path)
    if not failed_read and not data.get("path") and args.get("path"):
        data["path"] = args["path"]
    if observation.url:
        data.setdefault("url", observation.url)
    if not data.get("url") and args.get("url"):
        data["url"] = args["url"]
    if not data.get("command") and args.get("command"):
        data["command"] = args["command"]
    if not data.get("cwd") and args.get("cwd"):
        data["cwd"] = args["cwd"]
    if observation.output_text:
        for key in ("content", "text"):
            existing = data.get(key)
            if not isinstance(existing, str) or len(existing) > len(observation.output_text):
                data[key] = observation.output_text
    if observation.exit_code is not None:
        data.setdefault("exit_code", observation.exit_code)
        data.setdefault("returncode", observation.exit_code)
    if observation.stdout:
        data.setdefault("stdout", _truncate(observation.stdout, MAX_TEXT_CHARS))
    if observation.stderr:
        data.setdefault("stderr", _truncate(observation.stderr, MAX_TEXT_CHARS))
    if observation.policy_code:
        data.setdefault("code", observation.policy_code)
    if observation.recoverable:
        data.setdefault("recoverable", True)
    if observation.recovery_reason:
        data.setdefault("recovery_reason", observation.recovery_reason)
    if observation.source_ref:
        data.setdefault("source_ref", observation.source_ref)
        data.setdefault("source_chars", observation.source_chars)
        data.setdefault("source_bytes", observation.source_bytes)
        data.setdefault("source_sha256", observation.source_sha256)
        data.setdefault("source_kind", observation.source_kind)
        data.setdefault("source_mime", observation.source_mime)
        data.setdefault("source_encoding", observation.source_encoding)
        data.setdefault("source_is_text", observation.source_is_text)
    if not failed_read:
        data.setdefault("preview_chars", observation.preview_chars)
        data.setdefault("output_text_chars", observation.output_text_chars)
    if observation.content_ref:
        data.setdefault("content_ref", observation.content_ref)
    if observation.content_chars:
        data.setdefault("content_chars", observation.content_chars)
    if observation.content_bytes:
        data.setdefault("content_bytes", observation.content_bytes)
    if observation.content_sha256:
        data.setdefault("content_sha256", observation.content_sha256)
    if observation.content_externalized:
        data.setdefault("content_externalized", True)
    result = {
        "observation_id": observation.observation_id,
        "call_id": observation.call_id,
        "provider_call_id": observation.provider_call_id,
        "success": observation.success,
        "status": observation.status,
        "kind": observation.kind,
        "tool": observation.tool_name,
        "error": observation.error,
        "error_code": observation.error_code,
        "recoverable": observation.recoverable,
        "recovery_reason": observation.recovery_reason,
        "data": data,
        "metadata": metadata,
    }
    if observation.message:
        result["message"] = observation.message
    if observation.policy_code:
        result["policy_code"] = observation.policy_code
    return result


def observation_to_cache_snapshot(observation: ToolObservation) -> dict[str, Any]:
    """Return an untrimmed normalized snapshot for task-scoped idempotent replay."""

    return asdict(observation)


def observation_from_cache_snapshot(
    envelope: ToolCallEnvelope,
    snapshot: dict[str, Any],
    *,
    replay_metadata: dict[str, Any] | None = None,
) -> ToolObservation:
    """Restore an observation without re-running externalization."""

    failure = _validate_replay_artifacts(snapshot)
    if failure:
        return make_error_observation(envelope, failure[1], data={"code": failure[0]})
    values = {field_name: snapshot.get(field_name) for field_name in ToolObservation.__dataclass_fields__}
    for field_name, default in (
        ("source_ref", ""),
        ("source_chars", None),
        ("source_bytes", 0),
        ("source_sha256", ""),
        ("source_kind", ""),
        ("source_mime", ""),
        ("source_encoding", None),
        ("source_is_text", False),
        ("preview_chars", 0),
        ("output_text_chars", 0),
        ("recoverable", False),
        ("recovery_reason", ""),
    ):
        if values.get(field_name) is None:
            values[field_name] = default
    previous_call_id = str(values.get("call_id") or "")
    values.update(
        {
            "observation_id": f"obs-{uuid.uuid4().hex}",
            "call_id": envelope.call_id,
            "provider_call_id": envelope.provider_call_id,
            "tool_name": envelope.tool_name or envelope.raw_name,
            "canonical_name": envelope.canonical_name,
            "executable_name": envelope.executable_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    data = dict(values.get("data") or {})
    data.update(sanitize_unicode(dict(replay_metadata or {})))
    data.update({
        "idempotent_replay": True,
        "replayed_from_call_id": previous_call_id,
        "content_ref_reused": bool(values.get("content_ref") or data.get("stdout_ref") or data.get("stderr_ref")),
    })
    values["data"] = data
    metadata = dict(values.get("metadata") or {})
    metadata.update(sanitize_unicode(dict(replay_metadata or {})))
    metadata.update({"idempotent_replay": True, "replayed_from_call_id": previous_call_id})
    values["metadata"] = metadata
    return ToolObservation(**values)


def _validate_replay_artifacts(snapshot: dict[str, Any]) -> tuple[str, str] | None:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    source_ref = snapshot.get("source_ref") or data.get("source_ref")
    if source_ref:
        source = resolve_source_file_metadata(
            str(source_ref),
            operation="replay_read",
            expected_sha256=str(snapshot.get("source_sha256") or data.get("source_sha256") or ""),
            expected_chars=_optional_int(snapshot["source_chars"] if "source_chars" in snapshot else data.get("source_chars")),
            expected_bytes=_optional_int(snapshot["source_bytes"] if "source_bytes" in snapshot else data.get("source_bytes")),
        )
        if not source.ok:
            return source.error_code, "Cached source reference failed integrity validation."
        snapshot.update({
            "source_ref": source.ref,
            "source_chars": source.chars,
            "source_bytes": source.bytes,
            "source_sha256": source.sha256,
            "source_mime": source.mime_type,
            "source_encoding": source.encoding,
            "source_is_text": source.is_text,
        })
        data.update({
            "source_ref": source.ref,
            "source_chars": source.chars,
            "source_bytes": source.bytes,
            "source_sha256": source.sha256,
            "source_mime": source.mime_type,
            "source_encoding": source.encoding,
            "source_is_text": source.is_text,
        })
    entries = [
        (
            "content",
            snapshot.get("content_ref") or data.get("content_ref"),
            snapshot.get("content_sha256") or data.get("content_sha256"),
            snapshot["content_chars"] if "content_chars" in snapshot else data.get("content_chars"),
            snapshot["content_bytes"] if "content_bytes" in snapshot else data.get("content_bytes"),
        ),
        ("stdout", data.get("stdout_ref"), data.get("stdout_sha256"), data.get("stdout_chars"), data.get("stdout_bytes")),
        ("stderr", data.get("stderr_ref"), data.get("stderr_sha256"), data.get("stderr_chars"), data.get("stderr_bytes")),
    ]
    for name, raw_ref, raw_sha, raw_chars, raw_bytes in entries:
        if not raw_ref:
            continue
        validated = resolve_tool_artifact_metadata(
            str(raw_ref),
            str(raw_sha or ""),
            _optional_int(raw_chars),
            _optional_int(raw_bytes),
        )
        if not validated.valid:
            return validated.error_code, "Cached tool result artifact failed integrity validation."
        if name == "content":
            snapshot.update({
                "content_ref": validated.ref,
                "content_chars": validated.chars,
                "content_bytes": validated.bytes,
                "content_sha256": validated.sha256,
            })
        else:
            data.update({
                f"{name}_ref": validated.ref,
                f"{name}_chars": validated.chars,
                f"{name}_bytes": validated.bytes,
                f"{name}_sha256": validated.sha256,
            })
    snapshot["data"] = data
    return None


def observation_to_trace_dict(observation: ToolObservation) -> dict[str, Any]:
    data = observation.data if isinstance(observation.data, dict) else {}
    return {
        "observation_id": observation.observation_id,
        "call_id": observation.call_id,
        "provider_call_id": observation.provider_call_id,
        "tool": observation.tool_name,
        "canonical_name": observation.canonical_name,
        "executable_name": observation.executable_name,
        "status": observation.status,
        "kind": observation.kind,
        "success": observation.success,
        "error": _truncate(observation.error, MAX_MODEL_TEXT_CHARS),
        "error_code": observation.error_code,
        "recoverable": observation.recoverable,
        "recovery_reason": observation.recovery_reason,
        "message": _truncate(observation.message, MAX_MODEL_TEXT_CHARS),
        "path": observation.output_path,
        "url": observation.url,
        "exit_code": observation.exit_code,
        "command": _truncate(str(data.get("command") or ""), 500),
        "cwd": _truncate(str(data.get("cwd") or ""), 500),
        "has_stdout": bool(observation.stdout),
        "has_stderr": bool(observation.stderr),
        "stdout_preview": _truncate(observation.stdout, 500) if observation.stdout else "",
        "stderr_preview": _truncate(observation.stderr, 500) if observation.stderr else "",
        "blocked_by": observation.blocked_by,
        "policy_code": observation.policy_code,
        "source_ref": observation.source_ref,
        "source_chars": observation.source_chars,
        "source_bytes": observation.source_bytes,
        "source_sha256": observation.source_sha256,
        "source_kind": observation.source_kind,
        "source_mime": observation.source_mime,
        "source_encoding": observation.source_encoding,
        "source_is_text": observation.source_is_text,
        "content_ref": observation.content_ref,
        "content_chars": observation.content_chars,
        "content_bytes": observation.content_bytes,
        "content_sha256": observation.content_sha256,
        "content_externalized": observation.content_externalized,
        "preview_chars": observation.preview_chars,
        "output_text_chars": observation.output_text_chars,
        "compacted": observation.compacted,
        "timestamp": observation.timestamp,
    }


def observation_to_model_message_json(
    observation: ToolObservation,
    *,
    runtime_lane: str = "",
    current_step: dict[str, Any] | None = None,
) -> str:
    compacted = compact_observation_for_model(observation, runtime_lane=runtime_lane, current_step=current_step)
    return str(compacted.get("model_observation_json") or json.dumps(compacted.get("model_visible_summary") or {}, ensure_ascii=False, separators=(",", ":")))


def observation_summary(observation: ToolObservation | dict[str, Any]) -> str:
    if isinstance(observation, ToolObservation):
        trace = observation_to_trace_dict(observation)
    else:
        trace = observation if isinstance(observation, dict) else {}
        if "observation" in trace and isinstance(trace["observation"], dict):
            trace = trace["observation"]
    data = trace.get("data", {}) if isinstance(trace.get("data"), dict) else {}
    parts = [
        f"status={trace.get('status') or ('success' if trace.get('success') is True else 'failed')}",
        f"kind={trace.get('kind') or data.get('kind') or ''}",
        f"tool={trace.get('tool') or ''}",
    ]
    path = trace.get("path") or data.get("path") or data.get("output_path") or data.get("download_url")
    url = trace.get("url") or data.get("url")
    exit_code = trace.get("exit_code", data.get("exit_code", data.get("returncode")))
    policy_code = trace.get("policy_code") or data.get("code")
    if path:
        parts.append(f"path={path}")
    if url:
        parts.append(f"url={url}")
    if exit_code is not None:
        parts.append(f"exit_code={exit_code}")
    if policy_code:
        parts.append(f"policy_code={policy_code}")
    error_code = trace.get("error_code") or data.get("error_code")
    if error_code:
        parts.append(f"error_code={error_code}")
    if trace.get("recoverable") is True or data.get("recoverable") is True:
        parts.append("recoverable=true")
    recovery_reason = trace.get("recovery_reason") or data.get("recovery_reason")
    if recovery_reason:
        parts.append(f"recovery_reason={recovery_reason}")
    error = trace.get("error") or ""
    if error:
        parts.append(f"error={_truncate(str(error), 240)}")
    return " ".join(str(part) for part in parts if part and not str(part).endswith("="))


def _observation(
    envelope: ToolCallEnvelope,
    *,
    status: str,
    success: bool,
    error: str = "",
    error_code: str = "",
    recoverable: bool = False,
    recovery_reason: str = "",
    message: str = "",
    data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    output_text: str = "",
    output_path: str = "",
    url: str = "",
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    blocked_by: str = "",
    policy_code: str = "",
    source_ref: str = "",
    source_chars: int | None = None,
    source_bytes: int = 0,
    source_sha256: str = "",
    source_kind: str = "",
    source_mime: str = "",
    source_encoding: str | None = None,
    source_is_text: bool = False,
    content_ref: str = "",
    content_chars: int = 0,
    content_bytes: int = 0,
    content_sha256: str = "",
    content_externalized: bool = False,
    preview_chars: int = 0,
    output_text_chars: int = 0,
    compacted: bool = False,
) -> ToolObservation:
    return ToolObservation(
        observation_id=str(uuid.uuid4()),
        call_id=envelope.call_id,
        provider_call_id=envelope.provider_call_id,
        tool_name=envelope.executable_name or envelope.tool_name or envelope.raw_name,
        canonical_name=envelope.canonical_name,
        executable_name=envelope.executable_name,
        status=status,
        kind=_kind_from_envelope(envelope),
        success=success,
        error=_truncate(error, MAX_TEXT_CHARS),
        error_code=error_code,
        recoverable=recoverable,
        recovery_reason=recovery_reason,
        message=_truncate(message, MAX_TEXT_CHARS),
        data=sanitize_unicode(data or {}),
        metadata=_trim_mapping(metadata or {}, MAX_TEXT_CHARS),
        output_text=(
            output_text
            if bounded_read_file_page(data or {}) is not None
            else _truncate(output_text, MAX_TEXT_CHARS)
        ),
        output_path=output_path,
        url=url,
        exit_code=exit_code,
        stdout=_truncate(stdout, MAX_TEXT_CHARS),
        stderr=_truncate(stderr, MAX_TEXT_CHARS),
        blocked_by=blocked_by,
        policy_code=policy_code,
        source_ref=source_ref,
        source_chars=source_chars,
        source_bytes=source_bytes,
        source_sha256=source_sha256,
        source_kind=source_kind,
        source_mime=source_mime,
        source_encoding=source_encoding,
        source_is_text=source_is_text,
        content_ref=content_ref,
        content_chars=content_chars,
        content_bytes=content_bytes,
        content_sha256=content_sha256,
        content_externalized=content_externalized,
        preview_chars=preview_chars,
        output_text_chars=output_text_chars,
        compacted=compacted,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _kind_from_envelope(envelope: ToolCallEnvelope) -> str:
    if envelope.spec_provider == "mcp" or "." in (envelope.raw_name or ""):
        return ToolObservationKind.MCP
    kind = envelope.spec_kind
    if kind in {"file_read"}:
        return ToolObservationKind.FILE_READ
    if kind == "file_write":
        return ToolObservationKind.FILE_WRITE
    if kind == "execution":
        return ToolObservationKind.EXECUTION
    if kind == "web_read":
        return ToolObservationKind.WEB_READ
    if kind in {"browser_read", "browser_write"}:
        return ToolObservationKind.BROWSER
    if kind in {"database_read", "database_write"}:
        return ToolObservationKind.DATABASE
    if kind == "git_write" or kind == "git_read":
        return ToolObservationKind.GIT
    if kind in {"memory", "cache", "context", "project"}:
        return ToolObservationKind.INTERNAL
    if kind == "status":
        return ToolObservationKind.INTERNAL
    if kind == "rag":
        return ToolObservationKind.RAG
    return ToolObservationKind.UNKNOWN if not kind else ToolObservationKind.GENERIC


def _externalize_tool_result(
    envelope: ToolCallEnvelope,
    payload: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    tool = str(envelope.executable_name or envelope.tool_name or envelope.raw_name or "").split(".")[-1]
    if tool in {"read_file", "read_document"} and not is_successful_observation(payload):
        return {}
    task_id = str(envelope.metadata.get("task_id") or "task")
    call_id = str(envelope.provider_call_id or envelope.call_id or uuid.uuid4().hex)
    store = ToolResultStore()
    result: dict[str, Any] = {}
    threshold = max(1, int(settings.tool_result_externalize_chars or 12000))

    if data.get("source_ref"):
        source = resolve_source_file_metadata(
            str(data.get("source_ref") or ""),
            operation="write_result" if tool in {"write_file", "replace_in_file"} else "read",
            expected_sha256=str(data.get("source_sha256") or ""),
            expected_chars=_optional_int(data.get("source_chars")),
            expected_bytes=_optional_int(data.get("source_bytes")),
        )
        if not source.ok:
            return {"source_error_code": source.error_code}
        source_values = {
            "source_ref": source.ref,
            "source_chars": source.chars,
            "source_bytes": source.bytes,
            "source_sha256": source.sha256,
            "source_kind": str(data.get("source_kind") or ("document" if tool == "read_document" else "file")),
            "source_mime": source.mime_type,
            "source_encoding": source.encoding,
            "source_is_text": source.is_text,
        }
        data.update(source_values)
        result.update(source_values)

    preexisting_content_ref = bool(data.get("content_ref"))
    if preexisting_content_ref:
        validated = resolve_tool_artifact_metadata(
            str(data.get("content_ref") or ""),
            str(data.get("content_sha256") or ""),
            _optional_int(data.get("content_chars")),
            _optional_int(data.get("content_bytes")),
        )
        if not validated.valid:
            return {"artifact_error_code": validated.error_code}
        data.update({
            "content_ref": validated.ref,
            "content_chars": validated.chars,
            "content_bytes": validated.bytes,
            "content_sha256": validated.sha256,
        })
        data["content_ref_reused"] = True
        result.update({
            "content_ref": validated.ref,
            "content_chars": validated.chars,
            "content_bytes": validated.bytes,
            "content_sha256": validated.sha256,
            "content_externalized": bool(data.get("content_externalized")),
            "content_ref_reused": True,
        })

    if tool == "sandbox_exec":
        full_stdout = str(data.pop("_full_stdout", data.get("stdout") or ""))
        full_stderr = str(data.pop("_full_stderr", data.get("stderr") or ""))
        refs = []
        preexisting_ref = bool(data.get("stdout_ref") or data.get("stderr_ref"))
        if data.get("stdout_ref"):
            stream_error = _complete_stream_artifact(data, "stdout")
            if stream_error:
                return {"artifact_error_code": stream_error}
        elif len(full_stdout) > threshold:
            stored = store.store_text(full_stdout, task_id=task_id, call_id=call_id, kind="stdout")
            data.update({
                "stdout_ref": stored.content_ref,
                "stdout_chars": stored.chars,
                "stdout_bytes": stored.bytes,
                "stdout_sha256": stored.sha256,
                "stdout_ref_reused": stored.reused_existing,
            })
            refs.append(stored)
        if data.get("stderr_ref"):
            stream_error = _complete_stream_artifact(data, "stderr")
            if stream_error:
                return {"artifact_error_code": stream_error}
        elif len(full_stderr) > threshold:
            stored = store.store_text(full_stderr, task_id=task_id, call_id=call_id, kind="stderr")
            data.update({
                "stderr_ref": stored.content_ref,
                "stderr_chars": stored.chars,
                "stderr_bytes": stored.bytes,
                "stderr_sha256": stored.sha256,
                "stderr_ref_reused": stored.reused_existing,
            })
            refs.append(stored)
        if refs:
            data["content_ref_reused"] = any(item.reused_existing for item in refs)
        elif preexisting_ref:
            data["content_ref_reused"] = True
        data.update(_stream_metadata("stdout", full_stdout, data))
        data.update(_stream_metadata("stderr", full_stderr, data))
        result["content_externalized"] = bool(data.get("stdout_ref") or data.get("stderr_ref"))
    elif tool in {"read_file", "read_document", "write_file", "replace_in_file"}:
        arguments = _arguments_dict(envelope)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        raw_path = str(metadata.get("path") or data.get("path") or data.get("output_path") or data.get("file_path") or arguments.get("path") or "")
        bounded_read_page = bounded_read_file_page(data) if tool == "read_file" else None
        if raw_path and not data.get("source_ref") and bounded_read_page is None:
            source = resolve_source_file_metadata(
                raw_path,
                operation="write_result" if tool in {"write_file", "replace_in_file"} else "read",
            )
            if not source.ok:
                return {"source_error_code": source.error_code}
            source_kind = "document" if tool == "read_document" else "file"
            source_values = {
                "source_ref": source.ref,
                "source_chars": source.chars,
                "source_bytes": source.bytes,
                "source_sha256": source.sha256,
                "source_kind": source_kind,
                "source_mime": source.mime_type,
                "source_encoding": source.encoding,
                "source_is_text": source.is_text,
            }
            data.update(source_values)
            result.update(source_values)
        if tool == "read_document":
            full_result = sanitize_unicode(data.get("result"))
            document = compact_document_result(
                full_result,
                max_preview_chars=MAX_MODEL_TEXT_CHARS,
                max_table_rows=8,
                max_table_columns=12,
                max_tables=5,
            )
            if document.full_result_required and not preexisting_content_ref:
                stored = store.store_canonical_json(
                    {"result": full_result},
                    task_id=task_id,
                    call_id=call_id,
                    kind="document_result",
                )
                data["content_ref_reused"] = stored.reused_existing
                data.update({
                    "content_ref": stored.content_ref,
                    "content_chars": stored.chars,
                    "content_bytes": stored.bytes,
                    "content_sha256": stored.sha256,
                    "content_externalized": True,
                    "content_ref_reused": stored.reused_existing,
                })
                result.update({
                    "content_ref": stored.content_ref,
                    "content_chars": stored.chars,
                    "content_bytes": stored.bytes,
                    "content_sha256": stored.sha256,
                    "content_externalized": True,
                    "content_ref_reused": stored.reused_existing,
                })
            data["result"] = document.compacted_result
            data["document_result_compacted"] = document.was_compacted
            data["document_omitted_fields"] = list(document.omitted_fields)
            data["document_original_chars"] = document.original_serialized_chars
            data["document_visible_chars"] = document.visible_serialized_chars
            data["compacted"] = document.was_compacted
    elif preexisting_content_ref:
        pass
    else:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        if len(encoded) > threshold:
            stored = store.store_json(payload, task_id=task_id, call_id=call_id)
            data["content_ref_reused"] = stored.reused_existing
            data.update({
                "content_ref": stored.content_ref,
                "content_chars": stored.chars,
                "content_bytes": stored.bytes,
                "content_sha256": stored.sha256,
                "content_externalized": True,
                "content_ref_reused": stored.reused_existing,
            })
            result = {
                "content_ref": stored.content_ref,
                "content_chars": stored.chars,
                "content_bytes": stored.bytes,
                "content_sha256": stored.sha256,
                "content_externalized": True,
                "content_ref_reused": stored.reused_existing,
            }
    return result


def _partition_legacy_read_metadata(
    tool_name: str,
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    argument_path: str = "",
) -> None:
    """Move historical read execution grounding out of business data."""

    tool = str(tool_name or "").split(".")[-1]
    if tool not in {"read_file", "read_document"}:
        return

    existing = payload.get("metadata")
    metadata = dict(existing) if isinstance(existing, Mapping) else {}
    result = data.get("result")
    result_copy = dict(result) if isinstance(result, Mapping) else None
    legacy_nested_grounding = None
    if result_copy is not None:
        legacy_nested_grounding = result_copy.pop("path_grounding", None)
        data["result"] = result_copy
    legacy_flat_grounding = data.pop("path_grounding", None)

    if not isinstance(metadata.get("path_grounding"), Mapping):
        if isinstance(legacy_nested_grounding, Mapping):
            metadata["path_grounding"] = legacy_nested_grounding
        elif isinstance(legacy_flat_grounding, Mapping):
            metadata["path_grounding"] = legacy_flat_grounding

    if not str(metadata.get("path") or "").strip():
        candidates = (
            result_copy.get("resolved_path") if result_copy is not None else "",
            result_copy.get("path") if result_copy is not None else "",
            data.get("resolved_path"),
            data.get("path"),
            data.get("file_path"),
            argument_path,
        )
        resolved_path = next((str(value).strip() for value in candidates if str(value or "").strip()), "")
        if resolved_path:
            metadata["path"] = resolved_path
    payload["metadata"] = sanitize_unicode(metadata)


def _partition_legacy_read_document_metadata(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    argument_path: str = "",
) -> None:
    """Compatibility wrapper for historical document-only callers."""

    _partition_legacy_read_metadata(
        "read_document",
        payload,
        data,
        argument_path=argument_path,
    )


def _stream_metadata(name: str, text: str, data: dict[str, Any]) -> dict[str, Any]:
    if data.get(f"{name}_ref"):
        return {
            f"{name}_chars": int(data[f"{name}_chars"]),
            f"{name}_bytes": int(data[f"{name}_bytes"]),
            f"{name}_sha256": str(data.get(f"{name}_sha256") or ""),
            f"{name}_externalized": True,
        }
    encoded = text.encode("utf-8")
    return {
        f"{name}_chars": len(text),
        f"{name}_bytes": len(encoded),
        f"{name}_sha256": hashlib.sha256(encoded).hexdigest(),
        f"{name}_externalized": bool(data.get(f"{name}_ref")),
    }


def _complete_stream_artifact(data: dict[str, Any], name: str) -> str:
    validated = resolve_tool_artifact_metadata(
        str(data.get(f"{name}_ref") or ""),
        str(data.get(f"{name}_sha256") or ""),
        _optional_int(data.get(f"{name}_chars")),
        _optional_int(data.get(f"{name}_bytes")),
    )
    if not validated.valid:
        return validated.error_code
    data.update({
        f"{name}_ref": validated.ref,
        f"{name}_chars": validated.chars,
        f"{name}_bytes": validated.bytes,
        f"{name}_sha256": validated.sha256,
        f"{name}_externalized": True,
        f"{name}_ref_reused": True,
    })
    return ""


def finalize_model_visible_tool_data(
    tool_name: str,
    data: dict[str, Any],
    *,
    max_chars: int,
    successful: bool,
) -> dict[str, Any]:
    """Return the single canonical model-visible data payload for an observation."""

    tool = str(tool_name or "").split(".")[-1]
    canonical = sanitize_unicode(dict(data or {}))
    if tool in {"read_file", "read_document"} and not successful:
        allowed = {
            "requested_path", "status", "error", "error_code", "code",
            "policy_code", "blocked_by", "reason", "recoverable", "recovery_reason",
        }
        result = canonical.get("result")
        safe_result = (
            {key: sanitize_unicode(value) for key, value in result.items() if key in allowed}
            if isinstance(result, dict)
            else {}
        )
        pure = {key: sanitize_unicode(value) for key, value in canonical.items() if key in allowed}
        if safe_result:
            pure["result"] = safe_result
        return pure
    if tool == "read_file":
        bounded_page = bounded_read_file_page(canonical)
        if bounded_page is not None:
            canonical["result"] = bounded_page
            for key, value in bounded_page.items():
                canonical[key] = value
            canonical["content"] = str(bounded_page.get("content") or "")
            canonical["text"] = canonical["content"]
            canonical["preview_chars"] = len(canonical["content"])
            canonical["compacted"] = False
            return sanitize_unicode(canonical)
        result = canonical.get("result")
        result_mapping = dict(result) if isinstance(result, dict) else None
        body = ""
        if isinstance(result, str):
            body = result
        elif result_mapping is not None:
            body = next(
                (str(result_mapping.get(key)) for key in ("content", "text", "body", "markdown", "preview") if isinstance(result_mapping.get(key), str)),
                "",
            )
        if not body:
            body = next(
                (str(canonical.get(key)) for key in ("content", "text", "body", "markdown", "preview") if isinstance(canonical.get(key), str)),
                "",
            )
        visible = _bounded_preview(body, max_chars)
        if result_mapping is None:
            canonical["result"] = visible
        else:
            for key in ("content", "text", "body", "markdown", "preview"):
                if key in result_mapping:
                    result_mapping[key] = visible
            canonical["result"] = result_mapping
        canonical["content"] = visible
        canonical["text"] = visible
        canonical.pop("body", None)
        canonical.pop("markdown", None)
        canonical["preview_chars"] = len(visible)
        canonical["compacted"] = bool(len(body) > len(visible))
        return sanitize_unicode(canonical)
    if tool == "read_document":
        result = canonical.get("result")
        keep = {
            "result", "path", "file_path", "output_path", "filename", "title", "pages", "page_count",
            "status", "error", "error_code", "document_result_compacted", "document_omitted_fields",
            "document_original_chars", "document_visible_chars", "compacted", "content_ref_reused",
        }
        projected = {
            key: value
            for key, value in canonical.items()
            if key in keep or key.startswith("source_") or key.startswith("content_")
        }
        projected["result"] = result
        if isinstance(result, dict):
            projected["preview_chars"] = len(str(result.get("excerpt") or result.get("preview") or ""))
        else:
            projected["preview_chars"] = len(str(result or ""))
        return sanitize_unicode(projected)
    return sanitize_unicode(canonical)


def _bounded_preview(value: str, limit: int) -> str:
    text = str(value or "")
    maximum = max(1, int(limit or MAX_MODEL_TEXT_CHARS))
    if len(text) <= maximum:
        return text
    marker = f"...[truncated {len(text) - maximum} chars]"
    if len(marker) >= maximum:
        return text[:maximum]
    return text[: maximum - len(marker)] + marker


def _failed_read_data(envelope: ToolCallEnvelope, payload: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    arguments = _arguments_dict(envelope)
    result_mapping = data.get("result") if isinstance(data.get("result"), Mapping) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    requested_path = next(
        (
            str(value).strip()
            for value in (
                result_mapping.get("requested_path"),
                data.get("requested_path"),
                payload.get("requested_path"),
                arguments.get("path"),
                metadata.get("path"),
            )
            if isinstance(value, str) and value.strip()
        ),
        "",
    )
    error, error_code, status = _extract_tool_error_facts(payload, data)
    result = {
        "requested_path": requested_path,
        "status": status,
        "error": error,
        "error_code": error_code,
        "recoverable": bool(
            payload.get("recoverable") is True
            or data.get("recoverable") is True
            or result_mapping.get("recoverable") is True
        ),
        "recovery_reason": str(
            payload.get("recovery_reason")
            or data.get("recovery_reason")
            or result_mapping.get("recovery_reason")
            or ""
        ),
    }
    for key in ("code", "policy_code", "blocked_by", "reason"):
        value = next(
            (
                source.get(key)
                for source in (payload, data, result_mapping)
                if isinstance(source, Mapping) and source.get(key) not in (None, "", [], {})
            ),
            None,
        )
        if isinstance(value, (str, bool, int, float)):
            result[key] = sanitize_unicode(value)
    return {"result": result, **result}


def _extract_tool_error_facts(
    payload: Mapping[str, Any],
    data: Mapping[str, Any],
) -> tuple[str, str, str]:
    result = data.get("result") if isinstance(data.get("result"), Mapping) else {}

    def message_from(value: Any) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, Mapping):
            message = value.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return ""

    error_message = next(
        (
            message
            for message in (
                message_from(payload.get("error")),
                message_from(payload.get("message")),
                message_from(data.get("error")),
                message_from(data.get("message")),
                message_from(result.get("error")),
                message_from(result.get("message")),
            )
            if message
        ),
        "工具执行失败。",
    )

    def code_from(value: Any) -> str:
        return value.strip() if isinstance(value, str) and value.strip() else ""

    payload_error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    data_error = data.get("error") if isinstance(data.get("error"), Mapping) else {}
    result_error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
    error_code = next(
        (
            code
            for code in (
                code_from(payload.get("error_code")),
                code_from(payload.get("code")),
                code_from(payload_error.get("code")),
                code_from(data.get("error_code")),
                code_from(data.get("code")),
                code_from(data_error.get("code")),
                code_from(result.get("error_code")),
                code_from(result.get("code")),
                code_from(result_error.get("code")),
            )
            if code
        ),
        "tool_execution_failed",
    )
    raw_status = next(
        (
            value.strip().lower()
            for value in (payload.get("status"), data.get("status"), result.get("status"))
            if isinstance(value, str) and value.strip()
        ),
        "",
    )
    failure_statuses = {"failed", "error", "blocked", "denied", "rejected", "cancelled"}
    status = raw_status if raw_status in failure_statuses else "failed"
    return error_message, error_code, status


def _safe_failed_read_metadata(tool_name: str, value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    safe = {
        key: sanitize_unicode(source[key])
        for key in ("task_id", "provider", "policy_code", "blocked_by")
        if key in source
    }
    tool = str(tool_name or "").split(".")[-1]
    if tool in {"read_file", "read_document"}:
        path = source.get("path")
        if isinstance(path, str) and path.strip():
            safe["path"] = sanitize_unicode(path.strip())
        grounding = source.get("path_grounding")
        if isinstance(grounding, Mapping):
            safe["path_grounding"] = _safe_path_grounding(grounding)
    return safe


def _safe_path_grounding(value: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {
        "content", "text", "body", "markdown", "preview", "tables", "rows", "data", "values",
        "source_ref", "content_ref", "raw_result", "full_result",
    }

    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): clean(nested)
                for key, nested in item.items()
                if str(key) not in forbidden
            }
        if isinstance(item, (list, tuple)):
            return [clean(nested) for nested in item]
        if item is None or isinstance(item, (str, bool, int, float)):
            return sanitize_unicode(item)
        return str(item)

    return sanitize_unicode(clean(value))


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_observation_output_text(
    normalized_data: dict[str, Any],
    metadata: dict[str, Any],
    *,
    max_chars: int,
) -> str:
    """Build model-visible text exclusively from the normalized observation."""

    del metadata
    result = normalized_data.get("result")
    candidates: list[Any] = []
    if isinstance(result, dict):
        candidates.extend(result.get(key) for key in ("output", "content", "text", "preview", "excerpt", "markdown"))
    else:
        candidates.append(result)
    candidates.extend(normalized_data.get(key) for key in ("output", "content", "text", "preview", "stdout"))
    for value in candidates:
        if isinstance(value, str) and value:
            return _truncate(value, max_chars)
    if result not in (None, "", {}, []):
        try:
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            encoded = str(result)
        return _truncate(encoded, max_chars)
    return ""


def _arguments_dict(envelope: ToolCallEnvelope) -> dict[str, Any]:
    for value in (envelope.sanitized_arguments, envelope.parsed_arguments):
        if isinstance(value, dict) and value:
            return _trim_mapping(value, MAX_TEXT_CHARS)
    try:
        parsed = json.loads(envelope.raw_arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return _trim_mapping(parsed, MAX_TEXT_CHARS) if isinstance(parsed, dict) else {}


def _json_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _trim_mapping(value, MAX_TEXT_CHARS)


def _trim_mapping(value: dict[str, Any], limit: int) -> dict[str, Any]:
    return {str(key): _trim_value(item, limit) for key, item in value.items()}


def _trim_value(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return _truncate(value, limit)
    if isinstance(value, dict):
        return _trim_mapping(value, limit)
    if isinstance(value, list):
        return [_trim_value(item, limit) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _truncate(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _envelope_from_provider_tool_call(tool_call: Any) -> ToolCallEnvelope:
    function = getattr(tool_call, "function", None)
    name = str(getattr(function, "name", "") or "")
    call_id = str(getattr(tool_call, "id", "") or "")
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source="structured",
        raw_name=name,
        tool_name=name.split(".")[-1],
        canonical_name=name,
        executable_name=name,
        raw_arguments="{}",
        parsed_arguments={},
        sanitized_arguments={},
    )


__all__ = [
    "ToolObservation",
    "ToolObservationKind",
    "ToolObservationStatus",
    "make_blocked_observation",
    "make_boundary_blocked_observation",
    "make_error_observation",
    "make_permission_rejected_observation",
    "make_skipped_observation",
    "is_successful_observation",
    "is_recoverable_observation",
    "finalize_model_visible_tool_data",
    "normalize_tool_result",
    "observation_summary",
    "observation_to_legacy_dict",
    "observation_to_cache_snapshot",
    "observation_from_cache_snapshot",
    "observation_result",
    "observation_result_mapping",
    "observation_result_sequence",
    "observation_to_model_message_json",
    "observation_to_trace_dict",
]
