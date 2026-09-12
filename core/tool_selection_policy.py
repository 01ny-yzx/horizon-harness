"""Deterministic Tool Selection Policy v29.2.

This module defines the cross-family selection matrix used by capability
routing. It only consumes structured routing data and tool schemas; it does not
call LLMs, execute tools, access the network, or read/write workspace files.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.tool_spec import ToolKind, ToolProvider


TOOL_SELECTION_POLICY_VERSION = "29.2"


@dataclass(frozen=True)
class ToolSelectionFamily:
    capability: str
    family: str
    priority: int
    candidate_tools: tuple[str, ...]
    fallback_families: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()


TOOL_SELECTION_MATRIX: dict[str, ToolSelectionFamily] = {
    "file_read": ToolSelectionFamily(
        "file_read",
        "File Tools",
        34,
        ("read_file", "read_document", "list_files", "get_project_tree", "find_files", "search_text"),
    ),
    "file_write": ToolSelectionFamily("file_write", "File Tools", 40, ("write_file",)),
    "document_load": ToolSelectionFamily(
        "document_load",
        "Document Import",
        35,
        ("load_document", "load_documents_from_directory"),
    ),
    "document_search": ToolSelectionFamily(
        "document_search",
        "Document/RAG",
        30,
        ("search_document_chunks", "hybrid_search_chunks", "semantic_search_chunks"),
        ("document_load", "file_read"),
    ),
    "rag": ToolSelectionFamily(
        "rag",
        "Document/RAG",
        25,
        ("rag_query", "search_document_chunks", "hybrid_search_chunks", "semantic_search_chunks"),
        ("document_search", "document_load"),
    ),
    "browser_extract": ToolSelectionFamily("browser_extract", "Browser/Web Fetch", 48, ("browser_extract_text",), ("web_fetch",)),
    "browser_links": ToolSelectionFamily("browser_links", "Browser/Web Fetch", 49, ("browser_list_links",), ("web_fetch",)),
    "browser_screenshot": ToolSelectionFamily("browser_screenshot", "Browser/Web Fetch", 49, ("browser_screenshot",), ("web_fetch",)),
    "browser": ToolSelectionFamily(
        "browser",
        "Browser/Web Fetch",
        60,
        ("browser_extract_text", "browser_list_links", "browser_screenshot"),
        ("web_fetch",),
    ),
    "web_fetch": ToolSelectionFamily("web_fetch", "Browser/Web Fetch", 50, ("fetch_url",), ("browser_extract", "web_search")),
    "web_search": ToolSelectionFamily(
        "web_search",
        "Research/Search",
        55,
        ("web_search",),
        ("fetch_url", "browser_extract"),
    ),
    "database_read": ToolSelectionFamily(
        "database_read",
        "Database MCP",
        28,
        ("database_get_connection_config", "database_test_connection", "database_list_tables"),
    ),
    "database_schema": ToolSelectionFamily(
        "database_schema",
        "Database MCP",
        27,
        ("database_list_tables", "database_describe_table"),
        ("database_read",),
    ),
    "database_query": ToolSelectionFamily(
        "database_query",
        "Database MCP",
        26,
        ("database_select_query", "database_get_sample_rows"),
        ("database_schema", "database_read"),
        ("read_only_sql_only",),
    ),
    "mcp_tool": ToolSelectionFamily("mcp_tool", "MCP/Plugin/Marketplace", 70, ()),
    "coding": ToolSelectionFamily(
        "coding",
        "Coding",
        10,
        ("read_file", "search_text", "find_files", "replace_in_file", "write_file"),
        ("validation", "git"),
    ),
    "validation": ToolSelectionFamily(
        "validation",
        "Coding",
        15,
        ("sandbox_exec",),
    ),
    "git": ToolSelectionFamily("git", "Coding", 20, ("git_status", "git_diff", "git_log")),
    "python_sandbox": ToolSelectionFamily("python_sandbox", "Coding", 75, ("sandbox_exec",)),
    "shell_sandbox": ToolSelectionFamily("shell_sandbox", "Coding", 80, ("sandbox_exec",)),
    "memory_read": ToolSelectionFamily(
        "memory_read",
        "Memory",
        90,
        ("search_memory_references", "read_memory_reference"),
    ),
    "memory_write": ToolSelectionFamily(
        "memory_write",
        "Memory",
        85,
        (
            "remember_user_preference",
            "remember_stable_fact",
            "remember_project_summary",
            "remember_project_instruction",
            "update_stable_fact",
            "update_project_instruction",
            "delete_memory_reference",
            "delete_user_preference",
            "delete_project_instruction",
            "clear_memory_type",
        ),
    ),
    "workspace": ToolSelectionFamily("workspace", "Workspace", 95, ()),
    "cache": ToolSelectionFamily("cache", "Cache", 100, ()),
    "usage": ToolSelectionFamily("usage", "Usage", 105, ()),
}

CAPABILITY_FAMILIES = frozenset(TOOL_SELECTION_MATRIX)
CAPABILITY_PRIORITY = {capability: item.priority for capability, item in TOOL_SELECTION_MATRIX.items()}
CAPABILITY_TOOL_CANDIDATES = {capability: item.candidate_tools for capability, item in TOOL_SELECTION_MATRIX.items()}
CAPABILITY_FALLBACKS = {capability: item.fallback_families for capability, item in TOOL_SELECTION_MATRIX.items()}

BLOCKED_CAPABILITY_TOOLS = {
    "database_write": ("database_execute_write", "database_delete_rows", "database_update_rows", "database_drop_table"),
    "file_write": ("write_file", "replace_in_file"),
    "browser": ("browser_extract_text", "browser_list_links", "browser_screenshot"),
    "browser_extract": ("browser_extract_text",),
    "browser_links": ("browser_list_links",),
    "browser_screenshot": ("browser_screenshot",),
    "web_fetch": ("fetch_url",),
}


def ordered_capabilities(
    routes: list[Any],
    required_capabilities: list[str],
    optional_capabilities: list[str],
    route_source: str,
) -> list[str]:
    required = set(required_capabilities)
    optional = set(optional_capabilities)

    def sort_key(route: Any) -> tuple[int, int, int, int, int, int, str]:
        specificity = int(getattr(route, "request_specificity", 0) or 0)
        source = str(getattr(route, "source", "") or "")
        capability = str(getattr(route, "capability", "") or "")
        exact_tool_rank = 0 if specificity >= 2 else 1
        explicit_rank = 0 if specificity >= 1 else 1
        source_rank = 0 if route_source == "llm_intent_primary" and source == "intent" else 1
        required_rank = 0 if capability in required else 1
        priority = CAPABILITY_PRIORITY.get(capability, 999)
        confidence_rank = int((1.0 - float(getattr(route, "confidence", 0.0) or 0.0)) * 1000)
        optional_rank = 0 if capability in optional else 1
        return (exact_tool_rank, source_rank, required_rank, priority, explicit_rank, confidence_rank + optional_rank, capability)

    ordered = [str(getattr(route, "capability", "") or "") for route in sorted(routes, key=sort_key)]
    return _stable_unique([capability for capability in ordered if capability in required or capability in optional])


def candidate_tools_for_capability(
    capability: str,
    *,
    route: Any | None = None,
    tool_schemas: list[dict[str, object]] | None = None,
    tool_specs: Mapping[str, Any] | None = None,
) -> list[str]:
    candidates = list(CAPABILITY_TOOL_CANDIDATES.get(capability, ()))
    if capability == "mcp_tool":
        candidates = _mcp_tools_from_specs(tool_specs, tool_schemas)
    return _stable_unique(candidates)


def fallback_capabilities_for(primary_capability: str, blocked: set[str]) -> list[str]:
    fallbacks = [capability for capability in CAPABILITY_FALLBACKS.get(primary_capability, ()) if capability not in blocked]
    return _stable_unique(fallbacks)


def blocked_tools_for_capabilities(blocked_capabilities: list[str]) -> list[str]:
    tools: list[str] = []
    for capability in blocked_capabilities:
        tools.extend(BLOCKED_CAPABILITY_TOOLS.get(capability, ()))
    return _stable_unique(tools)


def build_selection_metadata(
    *,
    routes: list[Any],
    ordered_families: list[str],
    primary_capability: str,
    primary_tool: str,
    primary_tools: list[str],
    fallback_capabilities: list[str],
    fallback_tools: list[str],
    blocked_capabilities: list[str],
    unavailable_tools: list[str],
    warnings: list[str],
    route_source: str,
    fallback_reason: str,
    tool_schemas: list[dict[str, object]] | None = None,
    tool_specs: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    route_by_capability = {str(getattr(route, "capability", "") or ""): route for route in routes}
    candidate_tools = _stable_unique([*primary_tools, *fallback_tools, *unavailable_tools])
    rejected: list[str] = []
    rejection_reasons: dict[str, str] = {}
    for capability in ordered_families:
        if capability == primary_capability:
            continue
        rejected.append(capability)
        if capability in blocked_capabilities:
            rejection_reasons[capability] = "blocked_by_safety_boundary"
        elif not candidate_tools_for_capability(
            capability,
            route=route_by_capability.get(capability),
            tool_schemas=tool_schemas,
            tool_specs=tool_specs,
        ):
            rejection_reasons[capability] = "no_candidate_tool_defined"
        else:
            rejection_reasons[capability] = "lower_priority_than_selected_family"
    for capability in blocked_capabilities:
        if capability not in rejected:
            rejected.append(capability)
            rejection_reasons[capability] = "blocked_by_safety_boundary"
    route = route_by_capability.get(primary_capability)
    return {
        "tool_selection_policy_version": TOOL_SELECTION_POLICY_VERSION,
        "tool_selection_selected_family": primary_capability,
        "tool_selection_selected_capability": primary_capability,
        "tool_selection_primary_tool": primary_tool,
        "tool_selection_candidate_families": list(ordered_families),
        "tool_selection_candidate_tools": candidate_tools,
        "tool_selection_rejected_families": rejected,
        "tool_selection_rejection_reasons": rejection_reasons,
        "tool_selection_fallback_families": list(fallback_capabilities),
        "tool_selection_fallback_tools": list(fallback_tools),
        "tool_selection_reason": _selection_reason(route, fallback_reason),
        "tool_selection_source": str(getattr(route, "source", "") or route_source or "tool_plan"),
        "tool_selection_confidence": float(getattr(route, "confidence", 0.0) or 0.0) if route is not None else 0.0,
        "tool_selection_safety_notes": _stable_unique([*warnings, *unavailable_tools]),
    }


def _selection_reason(route: Any | None, fallback_reason: str) -> str:
    if route is None:
        return fallback_reason or "No executable tool family selected."
    reason = str(getattr(route, "reason", "") or "").strip()
    return reason or "Structured intent selected this tool family."


def _mcp_tools_from_specs(
    tool_specs: Mapping[str, Any] | None,
    tool_schemas: list[dict[str, object]] | None,
) -> list[str]:
    if not tool_specs:
        return []
    if tool_schemas is None:
        names = list(tool_specs)
    else:
        names = []
        for schema in tool_schemas:
            function = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if name:
                names.append(name)
    tools: list[str] = []
    for name in names:
        spec = tool_specs.get(name)
        if spec is None:
            continue
        provider = str(getattr(spec, "provider", "") or "")
        kind = str(getattr(spec, "kind", "") or "")
        if provider == ToolProvider.MCP or kind == ToolKind.MCP:
            tools.append(name)
    return _stable_unique(tools)


def _stable_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
