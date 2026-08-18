"""Boundary helpers for rejecting raw tool-call text in model content."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from core.tool_schema_scope import schema_tool_name


FALLBACK_TOOL_LIKE_NAMES = frozenset(
    {
        "fetch_url",
        "list_files",
        "read_file",
        "web_search",
        "write_file",
    }
)
GENERIC_TOOL_TAGS = frozenset({"tool_call", "function"})
CONTEXTUAL_PAYLOAD_TAGS = frozenset({"path", "content"})

FENCED_BLOCK_RE = re.compile(r"```[A-Za-z0-9_+-]*\n(?P<body>.*?)```", re.DOTALL)
TAG_RE = re.compile(
    r"<\s*/?\s*(?P<name>[A-Za-z_][A-Za-z0-9_.:-]*)\b[^>]*>",
    re.IGNORECASE,
)
FUNCTION_CALL_RE = re.compile(
    r"(?m)^\s*(?:await\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_.]*)\s*\([^()\n]{0,500}\)\s*$"
)
JSON_TOOL_KEY_RE = re.compile(r'"(?:tool|tool_name|name)"\s*:\s*"(?P<name>[A-Za-z_][A-Za-z0-9_.:-]*)"')


@dataclass(frozen=True)
class ToolBoundaryResult:
    has_raw_tool_text: bool
    matched_tool_names: tuple[str, ...] = ()
    matched_patterns: tuple[str, ...] = ()
    reason: str = ""


def collect_registered_tool_names(tool_schemas: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
    """Collect currently known local and schema-visible tool names."""

    names: set[str] = set(FALLBACK_TOOL_LIKE_NAMES)
    try:
        from tools.registry import get_local_tool_registry, get_local_tool_schemas

        names.update(str(name).strip() for name in get_local_tool_registry() if str(name).strip())
        schemas = list(tool_schemas or get_local_tool_schemas())
    except Exception:
        schemas = list(tool_schemas or [])

    for schema in schemas:
        name = schema_tool_name(schema)
        if name:
            names.add(name)
            names.add(name.split(".")[-1])
    return tuple(sorted(names))


def detect_raw_tool_text(text: str | None, *, tool_names: tuple[str, ...] | None = None) -> ToolBoundaryResult:
    """Detect pseudo tool calls emitted as ordinary assistant text."""

    if not text:
        return ToolBoundaryResult(False)

    body = str(text)
    registered = set(tool_names or collect_registered_tool_names())
    normalized_registered = {_normalize_tool_name(name) for name in registered if name}
    matched_names: set[str] = set()
    matched_patterns: set[str] = set()

    _detect_tags(body, normalized_registered, matched_names, matched_patterns)
    _detect_function_calls(body, normalized_registered, matched_names, matched_patterns)
    _detect_json_tool_calls(body, normalized_registered, matched_names, matched_patterns)

    if matched_patterns:
        return ToolBoundaryResult(
            True,
            tuple(sorted(matched_names)),
            tuple(sorted(matched_patterns)),
            "assistant_content_contains_raw_tool_call_text",
        )
    return ToolBoundaryResult(False)


def _detect_tags(
    text: str,
    registered: set[str],
    matched_names: set[str],
    matched_patterns: set[str],
) -> None:
    tag_names = [_normalize_tool_name(match.group("name")) for match in TAG_RE.finditer(text)]
    tag_set = set(tag_names)
    tool_context = bool(tag_set.intersection(GENERIC_TOOL_TAGS)) or any(
        _is_registered_tool_name(name, registered) or _is_mcp_like_tool_tag(name) for name in tag_set
    )
    for name in tag_names:
        if not name:
            continue
        if name in GENERIC_TOOL_TAGS:
            matched_names.add(name)
            matched_patterns.add(f"tag:{name}")
            continue
        if _is_registered_tool_name(name, registered) or _is_mcp_like_tool_tag(name):
            matched_names.add(name)
            matched_patterns.add(f"tag:{name}")
            continue
        if tool_context and name in CONTEXTUAL_PAYLOAD_TAGS:
            matched_names.add(name)
            matched_patterns.add(f"context_tag:{name}")


def _detect_function_calls(
    text: str,
    registered: set[str],
    matched_names: set[str],
    matched_patterns: set[str],
) -> None:
    candidates = [text]
    candidates.extend(match.group("body") for match in FENCED_BLOCK_RE.finditer(text))
    for candidate in candidates:
        for match in FUNCTION_CALL_RE.finditer(candidate):
            name = _normalize_tool_name(match.group("name"))
            if _is_registered_tool_name(name, registered) or _is_mcp_like_tool_tag(name):
                matched_names.add(name)
                matched_patterns.add(f"function_call:{name}")


def _detect_json_tool_calls(
    text: str,
    registered: set[str],
    matched_names: set[str],
    matched_patterns: set[str],
) -> None:
    for match in JSON_TOOL_KEY_RE.finditer(text):
        name = _normalize_tool_name(match.group("name"))
        if _is_registered_tool_name(name, registered) or _is_mcp_like_tool_tag(name):
            matched_names.add(name)
            matched_patterns.add(f"json_tool:{name}")

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped[0] not in "{[":
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        _detect_json_value(parsed, registered, matched_names, matched_patterns)


def _detect_json_value(
    value: Any,
    registered: set[str],
    matched_names: set[str],
    matched_patterns: set[str],
) -> None:
    if isinstance(value, dict):
        for key in ("tool", "tool_name", "name"):
            raw_name = value.get(key)
            if isinstance(raw_name, str):
                name = _normalize_tool_name(raw_name)
                if _is_registered_tool_name(name, registered) or _is_mcp_like_tool_tag(name):
                    matched_names.add(name)
                    matched_patterns.add(f"json_tool:{name}")
        if "arguments" in value and any(key in value for key in ("tool", "tool_name", "name")):
            matched_patterns.add("json_arguments")
        for child in value.values():
            _detect_json_value(child, registered, matched_names, matched_patterns)
    elif isinstance(value, list):
        for item in value:
            _detect_json_value(item, registered, matched_names, matched_patterns)


def _normalize_tool_name(name: str) -> str:
    return str(name or "").strip().lower().replace("-", "_").replace(":", "_").split(".")[-1]


def _is_registered_tool_name(name: str, registered: set[str]) -> bool:
    return name in registered or name.split(".")[-1] in registered


def _is_mcp_like_tool_tag(name: str) -> bool:
    if name.startswith("mcp_"):
        return True
    if not re.match(r"^[a-z][a-z0-9]*_[a-z0-9_]+$", name):
        return False
    action_markers = {
        "add",
        "archive",
        "commit",
        "create",
        "delete",
        "edit",
        "fill",
        "forward",
        "move",
        "post",
        "push",
        "replace",
        "send",
        "submit",
        "update",
        "upload",
        "write",
    }
    return bool(action_markers.intersection(name.split("_")))


__all__ = [
    "ToolBoundaryResult",
    "collect_registered_tool_names",
    "detect_raw_tool_text",
]
