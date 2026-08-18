"""ToolCallEnvelope v1 for structured provider tool calls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from tools.registry import get_executable_tool_name, get_tool_spec, normalize_tool_name


class ToolCallSource:
    STRUCTURED = "structured"
    USER_TEXT = "user_text"
    ASSISTANT_TEXT = "assistant_text"
    CANDIDATE_TEXT = "candidate_text"
    RAW_MARKUP = "raw_markup"
    REPAIR = "repair"
    UNKNOWN = "unknown"


class ToolCallStatus:
    RECEIVED = "received"
    PARSED = "parsed"
    NORMALIZED = "normalized"
    BLOCKED = "blocked"
    EXECUTABLE = "executable"


@dataclass
class ToolCallEnvelope:
    call_id: str
    provider_call_id: str
    source: str
    raw_name: str
    tool_name: str
    canonical_name: str
    executable_name: str
    raw_arguments: str
    parsed_arguments: dict[str, Any]
    sanitized_arguments: dict[str, Any]
    parse_error: str = ""
    status: str = ToolCallStatus.RECEIVED
    spec_kind: str = ""
    spec_risk: str = ""
    spec_provider: str = ""
    side_effect: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_tool_arguments(raw_arguments: str) -> tuple[dict[str, Any], str]:
    raw = raw_arguments or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"_raw": raw}, f"Tool argument JSON parse error: {exc.msg}"
    if not isinstance(parsed, dict):
        return {}, "Tool arguments must be a JSON object."
    return parsed, ""


def build_structured_tool_call_envelope(provider_tool_call: Any, *, mcp_registry: Any | None = None) -> ToolCallEnvelope:
    function = getattr(provider_tool_call, "function", None)
    raw_name = str(getattr(function, "name", "") or "")
    raw_arguments = str(getattr(function, "arguments", "") or "{}")
    call_id = str(getattr(provider_tool_call, "id", "") or "")
    parsed, parse_error = parse_tool_arguments(raw_arguments)
    envelope = ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name=raw_name,
        tool_name=normalize_tool_name(raw_name),
        canonical_name="",
        executable_name="",
        raw_arguments=raw_arguments,
        parsed_arguments=parsed,
        sanitized_arguments={},
        parse_error=parse_error,
        status=ToolCallStatus.PARSED if not parse_error else ToolCallStatus.BLOCKED,
    )
    return normalize_tool_call_envelope(envelope, mcp_registry=mcp_registry)


def normalize_tool_call_envelope(envelope: ToolCallEnvelope, *, mcp_registry: Any | None = None) -> ToolCallEnvelope:
    spec = get_tool_spec(envelope.raw_name or envelope.tool_name, mcp_registry)
    canonical_name = str(getattr(spec, "canonical_name", "") or envelope.tool_name)
    executable_name = get_executable_tool_name(envelope.raw_name or envelope.tool_name, mcp_registry)
    status = envelope.status
    if not envelope.parse_error:
        status = ToolCallStatus.EXECUTABLE if envelope.source == ToolCallSource.STRUCTURED and spec is not None else ToolCallStatus.NORMALIZED
    envelope.tool_name = normalize_tool_name(envelope.raw_name or envelope.tool_name)
    envelope.canonical_name = canonical_name
    envelope.executable_name = executable_name
    envelope.status = status
    envelope.spec_kind = str(getattr(spec, "kind", "") or "")
    envelope.spec_risk = str(getattr(spec, "risk", "") or "")
    envelope.spec_provider = str(getattr(spec, "provider", "") or "")
    envelope.side_effect = bool(getattr(spec, "side_effect", False))
    envelope.metadata = {**dict(envelope.metadata), "tool_spec_found": spec is not None}
    return envelope


def is_executable_tool_call(envelope: ToolCallEnvelope) -> bool:
    return bool(
        envelope.source == ToolCallSource.STRUCTURED
        and not envelope.parse_error
        and envelope.executable_name
        and envelope.metadata.get("tool_spec_found") is True
    )


def blocked_tool_call_observation(envelope: ToolCallEnvelope, reason: str, error: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": error,
        "data": {
            "code": reason,
            "reason": reason,
            "tool_call_source": envelope.source,
            "tool": envelope.tool_name,
            "raw_name": envelope.raw_name,
            "canonical_name": envelope.canonical_name,
            "executable_name": envelope.executable_name,
            "parse_error": envelope.parse_error,
        },
    }


def tool_call_to_trace_dict(envelope: ToolCallEnvelope) -> dict[str, Any]:
    return {
        "tool": envelope.executable_name or envelope.tool_name or envelope.raw_name,
        "raw_name": envelope.raw_name,
        "canonical_name": envelope.canonical_name,
        "source": envelope.source,
        "status": envelope.status,
        "arguments": envelope.parsed_arguments,
        "spec": {
            "kind": envelope.spec_kind,
            "risk": envelope.spec_risk,
            "provider": envelope.spec_provider,
            "side_effect": envelope.side_effect,
        },
    }


__all__ = [
    "ToolCallEnvelope",
    "ToolCallSource",
    "ToolCallStatus",
    "blocked_tool_call_observation",
    "build_structured_tool_call_envelope",
    "is_executable_tool_call",
    "normalize_tool_call_envelope",
    "parse_tool_arguments",
    "tool_call_to_trace_dict",
]
