"""Assistant-response-scoped exact ToolCall loop boundary.

This guard tracks consecutive model-requested actions within one structured
assistant response. It is intentionally independent from task state and from
observation-driven no-progress detection.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from dataclasses import dataclass, field
from typing import Any, Literal

EXACT_TOOL_CALL_LOOP_THRESHOLD = 3

ExactToolCallLoopAction = Literal["allow", "permission"]


@dataclass
class ExactToolCallLoopState:
    """Mutable repetition state owned by one Assistant/model response."""

    tool_identity: str = ""
    input_json: str = ""
    action_fingerprint: str = ""
    consecutive_count: int = 0


@dataclass(frozen=True)
class ExactToolCallLoopDecision:
    action: ExactToolCallLoopAction
    canonical_tool: str
    arguments_fingerprint: str
    execution_scope: str
    consecutive_count: int
    threshold: int = EXACT_TOOL_CALL_LOOP_THRESHOLD
    permission: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def resolve_exact_tool_call_loop(
    *,
    response_state: ExactToolCallLoopState,
    canonical_tool: str,
    arguments: dict[str, Any],
    execution_scope: str,
) -> ExactToolCallLoopDecision:
    """Record one action in the current response and gate its third repeat."""

    tool = str(canonical_tool or "").strip()
    scope = str(execution_scope or "").strip()
    model_input_json = _ordered_json(arguments)
    model_arguments_fingerprint = _arguments_fingerprint(model_input_json)
    action_fingerprint = _action_fingerprint(
        canonical_tool=tool,
        model_input_json=model_input_json,
    )
    arguments_fingerprint = model_arguments_fingerprint
    previous_fingerprint = response_state.action_fingerprint
    previous_count = response_state.consecutive_count
    consecutive_count = (
        previous_count + 1
        if response_state.tool_identity == tool and response_state.input_json == model_input_json
        else 1
    )
    requires_permission = consecutive_count >= EXACT_TOOL_CALL_LOOP_THRESHOLD
    response_state.tool_identity = tool
    response_state.input_json = model_input_json
    response_state.action_fingerprint = action_fingerprint
    response_state.consecutive_count = consecutive_count
    return ExactToolCallLoopDecision(
        action="permission" if requires_permission else "allow",
        canonical_tool=tool,
        arguments_fingerprint=arguments_fingerprint,
        execution_scope=scope,
        consecutive_count=consecutive_count,
        permission="doom_loop" if requires_permission else "",
        metadata={
            "action_fingerprint": action_fingerprint,
            "model_arguments_fingerprint": model_arguments_fingerprint,
            "previous_action_fingerprint": previous_fingerprint,
            "state_lifecycle": "assistant_response",
        },
    )


def exact_tool_call_loop_trace_payload(
    decision: ExactToolCallLoopDecision,
) -> dict[str, Any]:
    """Return a compact structured Trace payload."""

    return {
        "canonical_tool": decision.canonical_tool,
        "arguments_fingerprint": decision.arguments_fingerprint,
        "execution_scope": decision.execution_scope,
        "consecutive_count": decision.consecutive_count,
        "threshold": decision.threshold,
        "permission_required": decision.action == "permission",
        "permission": decision.permission,
    }


def _arguments_fingerprint(model_input_json: str) -> str:
    return hashlib.sha256(model_input_json.encode("utf-8")).hexdigest()


def _action_fingerprint(
    *,
    canonical_tool: str,
    model_input_json: str,
) -> str:
    payload = [canonical_tool, model_input_json]
    return hashlib.sha256(_ordered_json(payload).encode("utf-8")).hexdigest()


def _ordered_json(value: Any) -> str:
    """Match JSON.stringify equality for parsed structured ToolCall input."""

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _json_stringify_string(value)
    if isinstance(value, (int, float)):
        return _json_stringify_number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_ordered_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            _json_stringify_string(key) + ":" + _ordered_json(item)
            for key, item in _json_stringify_object_items(value)
        ) + "}"
    raise TypeError(f"Unsupported parsed ToolCall input type: {type(value).__name__}")


def _json_stringify_object_items(value: dict[Any, Any]) -> list[tuple[str, Any]]:
    """Apply JavaScript ordinary-object property enumeration order."""

    array_indexes: list[tuple[int, str, Any]] = []
    string_properties: list[tuple[str, Any]] = []
    for raw_key, item in value.items():
        key = str(raw_key)
        index = _javascript_array_index(key)
        if index is None:
            string_properties.append((key, item))
        else:
            array_indexes.append((index, key, item))
    array_indexes.sort(key=lambda entry: entry[0])
    return [
        *((key, item) for _, key, item in array_indexes),
        *string_properties,
    ]


def _javascript_array_index(key: str) -> int | None:
    """Return the index only for canonical Uint32 keys below 2**32 - 1."""

    if key == "0":
        return 0
    if len(key) > 10:
        return None
    if not key or key[0] == "0" or not all("0" <= char <= "9" for char in key):
        return None
    index = int(key)
    return index if index < 4294967295 else None


def _json_stringify_number(value: int | float) -> str:
    """Serialize a JSON number with JavaScript Number-style equality."""

    try:
        number = float(value)
    except OverflowError:
        return "null"
    if not math.isfinite(number):
        return "null"
    if number == 0:
        return "0"

    absolute = abs(number)
    representation = repr(number).lower()
    if number.is_integer() and absolute < 1e21:
        return str(int(number))
    if "e" not in representation:
        return representation.removesuffix(".0")

    mantissa, exponent_text = representation.split("e", 1)
    exponent = int(exponent_text)
    if 1e-6 <= absolute < 1e21:
        return format(Decimal(representation), "f").rstrip("0").rstrip(".")
    mantissa = mantissa.removesuffix(".0")
    exponent_sign = "+" if exponent >= 0 else ""
    return f"{mantissa}e{exponent_sign}{exponent}"


def _json_stringify_string(value: str) -> str:
    """Quote a string while emitting lone surrogates as JSON escapes."""

    quoted = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "".join(
        f"\\u{ord(character):04x}"
        if 0xD800 <= ord(character) <= 0xDFFF
        else character
        for character in quoted
    )


__all__ = [
    "EXACT_TOOL_CALL_LOOP_THRESHOLD",
    "ExactToolCallLoopAction",
    "ExactToolCallLoopDecision",
    "ExactToolCallLoopState",
    "exact_tool_call_loop_trace_payload",
    "resolve_exact_tool_call_loop",
]
