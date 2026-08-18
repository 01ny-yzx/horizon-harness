"""Deterministic capability surface summaries for scoped runtime prompts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.unicode_safety import sanitize_unicode


_LANE_DEFAULTS: dict[str, tuple[str, list[str], list[str], str, str]] = {
    "single_file_read": ("read", ["file_read"], ["read_file", "read_document"], "file_read", ""),
    "file_output": ("write", ["file_write"], ["write_file"], "file_write", "write_file"),
    "command_exec": ("bash", ["command_exec"], ["sandbox_exec"], "command_exec", "sandbox_exec"),
    "chat": ("none", [], [], "", ""),
}

_CAPABILITY_TO_TOOLS = {
    "file_read": ("read_file", "read_document"),
    "document_load": ("load_document", "load_documents_from_directory"),
    "file_write": ("write_file",),
    "artifact_output": ("write_file",),
    "command_exec": ("sandbox_exec",),
    "code_edit": ("replace_in_file",),
}


def is_mixed_capability(required_capabilities: Sequence[str] | None) -> bool:
    """Return true when structured required capabilities contain more than one distinct value."""

    families = _dedupe([_capability_family(value) for value in (required_capabilities or [])])
    return len(families) > 1


def tool_priority_for_mixed_capabilities(
    required_capabilities: Sequence[str] | None,
    existing_tool_names: Sequence[str] | None = None,
) -> list[str]:
    """Return current tools plus required tools implied by mixed structured capabilities."""

    tools = _dedupe(list(existing_tool_names or []))
    for capability in required_capabilities or []:
        candidates = _CAPABILITY_TO_TOOLS.get(str(capability or "").strip(), ())
        if str(capability or "").strip() == "file_read":
            scoped_candidates = [tool for tool in candidates if tool in tools]
            tools.extend(scoped_candidates or candidates)
        else:
            tools.extend(candidates)
    return _dedupe(tools)


def build_capability_surface_summary(
    *,
    runtime_lane: str,
    lane_profile: str | None = None,
    primary_capability: str | None = None,
    primary_tool: str | None = None,
    required_capabilities: list[str] | tuple[str, ...] | None = None,
    scoped_tool_names: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build the effective capability/tool surface from lane and scoped tools.

    This is intentionally deterministic. It does not classify user text; it only
    projects the already resolved runtime lane and final scoped tool names.
    """

    lane = str(runtime_lane or "").strip().lower()
    profile = str(lane_profile or lane or "").strip().lower()
    scoped_tools_provided = scoped_tool_names is not None
    scoped_tools = _dedupe(scoped_tool_names or [])
    required = _dedupe(required_capabilities or [])
    primary_cap = str(primary_capability or "").strip()
    primary = str(primary_tool or "").strip()

    if is_mixed_capability(required) and lane not in {"code_edit", "build", "research"}:
        effective_tools = _mixed_effective_tools(required, scoped_tools)
        return _summary(
            runtime_lane="build",
            lane_profile="build",
            capability_surface="build",
            effective_capabilities=required,
            effective_tools=effective_tools,
            primary_capability=primary_cap,
            primary_tool=primary or (effective_tools[0] if effective_tools else ""),
            source="mixed_capability",
        )

    if lane in _LANE_DEFAULTS:
        surface, caps, tools, default_cap, default_tool = _LANE_DEFAULTS[lane]
        effective_tools = _filter_default_tools(tools, scoped_tools, scoped_tools_provided=scoped_tools_provided)
        return _summary(
            runtime_lane=lane,
            lane_profile=profile,
            capability_surface=surface,
            effective_capabilities=caps,
            effective_tools=effective_tools,
            primary_capability=primary_cap or default_cap,
            primary_tool=primary or default_tool,
        )

    if lane == "code_edit":
        caps = _code_edit_capabilities(required, scoped_tools)
        return _summary(
            runtime_lane=lane,
            lane_profile=profile,
            capability_surface="edit",
            effective_capabilities=caps,
            effective_tools=scoped_tools,
            primary_capability=primary_cap or "code_edit",
            primary_tool=primary or (scoped_tools[0] if scoped_tools else ""),
        )

    if lane == "build":
        effective_tools = _mixed_effective_tools(required, scoped_tools)
        return _summary(
            runtime_lane=lane,
            lane_profile=profile,
            capability_surface="build",
            effective_capabilities=required,
            effective_tools=effective_tools,
            primary_capability=primary_cap,
            primary_tool=primary or (effective_tools[0] if effective_tools else ""),
        )

    if lane == "research":
        return _summary(
            runtime_lane=lane,
            lane_profile=profile,
            capability_surface="research",
            effective_capabilities=required,
            effective_tools=scoped_tools,
            primary_capability=primary_cap,
            primary_tool=primary or (scoped_tools[0] if scoped_tools else ""),
        )

    return _summary(
        runtime_lane=lane,
        lane_profile=profile,
        capability_surface=lane or "unknown",
        effective_capabilities=required,
        effective_tools=scoped_tools,
        primary_capability=primary_cap,
        primary_tool=primary or (scoped_tools[0] if scoped_tools else ""),
    )


def _summary(
    *,
    runtime_lane: str,
    lane_profile: str,
    capability_surface: str,
    effective_capabilities: list[str],
    effective_tools: list[str],
    primary_capability: str,
    primary_tool: str,
    source: str = "runtime_lane",
) -> dict[str, Any]:
    return sanitize_unicode(
        {
            "runtime_lane": runtime_lane,
            "lane_profile": lane_profile,
            "capability_surface": capability_surface,
            "effective_capabilities": _dedupe(effective_capabilities),
            "effective_tools": _dedupe(effective_tools),
            "source": source,
            "primary_capability": primary_capability,
            "primary_tool": primary_tool,
        }
    )


def _filter_default_tools(default_tools: list[str], scoped_tools: list[str], *, scoped_tools_provided: bool) -> list[str]:
    if not scoped_tools_provided:
        return list(default_tools)
    allowed = set(default_tools)
    return [name for name in scoped_tools if name in allowed]


def _code_edit_capabilities(required: list[str], scoped_tools: list[str]) -> list[str]:
    caps = [cap for cap in required if cap]
    if not caps:
        caps = ["file_read", "code_edit"]
    if any(name in {"write_file", "replace_in_file", "edit_file"} for name in scoped_tools):
        caps.append("file_write")
    if "sandbox_exec" in scoped_tools:
        caps.append("command_exec")
    return _dedupe(caps)


def _mixed_effective_tools(required: list[str], scoped_tools: list[str]) -> list[str]:
    return tool_priority_for_mixed_capabilities(required, scoped_tools)


def _capability_family(value: str) -> str:
    capability = str(value or "").strip()
    if capability == "artifact_output":
        return "file_write"
    if capability in {"shell_sandbox", "python_sandbox"}:
        return "command_exec"
    return capability


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
