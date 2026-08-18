"""Provider visibility policy for local, MCP, and future cloud tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


ToolProvider = Literal["local", "mcp", "cloud"]
ToolVisibility = Literal["default", "explicit_only", "hidden"]
ToolVisibilityMode = Literal["default", "explicit"]


@dataclass(frozen=True)
class ToolProviderDecision:
    tool_name: str
    provider: ToolProvider
    capability: str
    visibility: ToolVisibility
    reason: str
    default_provider: ToolProvider

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


DEFAULT_PROVIDER_BY_CAPABILITY: dict[str, ToolProvider] = {
    "browser_extract": "local",
    "browser_links": "local",
    "browser_screenshot": "local",
    "file_read": "local",
    "file_write": "local",
    "web_fetch": "local",
    "web_search": "local",
    "database_query": "mcp",
    "database_schema": "mcp",
    "database_read": "mcp",
}


def infer_tool_capability(tool_name: str) -> str:
    """Return the capability family represented by a tool name."""

    base = _base_tool_name(tool_name)
    if base in {"browser_extract_text", "browser_click_and_extract"}:
        return "browser_extract"
    if base == "browser_list_links":
        return "browser_links"
    if base == "browser_screenshot":
        return "browser_screenshot"
    if base in {"read_file", "read_document"}:
        return "file_read"
    if base in {"write_file", "replace_in_file"}:
        return "file_write"
    if base == "sandbox_exec":
        return "shell_sandbox"
    if base == "fetch_url":
        return "web_fetch"
    if base == "web_search":
        return "web_search"
    if base in {"database_select_query", "database_get_sample_rows"}:
        return "database_query"
    if base in {"database_list_tables", "database_describe_table"}:
        return "database_schema"
    if base in {"database_get_connection_config", "database_test_connection", "get_database_status"}:
        return "database_read"
    if base in {"rag_query", "search_document_chunks", "hybrid_search_chunks", "semantic_search_chunks"}:
        return "rag"
    return ""


def build_provider_capability_map(tools: list[tuple[str, ToolProvider, str | None]]) -> dict[str, set[ToolProvider]]:
    """Build capability -> providers from (name, provider, capability_override)."""

    providers: dict[str, set[ToolProvider]] = {}
    for tool_name, provider, capability_override in tools:
        capability = capability_override or infer_tool_capability(tool_name)
        if not capability:
            continue
        providers.setdefault(capability, set()).add(provider)
    return providers


def decide_tool_visibility(
    *,
    tool_name: str,
    provider: ToolProvider,
    provider_capabilities: dict[str, set[ToolProvider]],
    capability: str = "",
    mode: ToolVisibilityMode = "default",
) -> ToolProviderDecision:
    """Decide whether one tool should be shown in the ordinary Agent schema list."""

    capability = capability or infer_tool_capability(tool_name)
    providers = provider_capabilities.get(capability, {provider}) if capability else {provider}
    default_provider = _default_provider(capability, providers)
    if mode == "explicit":
        return ToolProviderDecision(tool_name, provider, capability, "default", "explicit_provider_mode", default_provider)
    if not capability or len(providers) <= 1:
        return ToolProviderDecision(tool_name, provider, capability, "default", "single_provider_capability", default_provider)
    if provider == default_provider:
        return ToolProviderDecision(tool_name, provider, capability, "default", "default_provider_for_capability", default_provider)
    return ToolProviderDecision(tool_name, provider, capability, "explicit_only", "non_default_provider_for_capability", default_provider)


def schema_name(schema: dict[str, object]) -> str:
    function = schema.get("function")
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "")


def _default_provider(capability: str, providers: set[ToolProvider]) -> ToolProvider:
    configured = DEFAULT_PROVIDER_BY_CAPABILITY.get(capability)
    if configured and configured in providers:
        return configured
    for provider in ("local", "mcp", "cloud"):
        if provider in providers:
            return provider  # type: ignore[return-value]
    return "local"


def _base_tool_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name.startswith("mcp."):
        parts = name.split(".")
        if len(parts) >= 3:
            return parts[-1]
    if name.startswith("cloud."):
        parts = name.split(".")
        if len(parts) >= 3:
            return parts[-1]
    for prefix in (
        "mcp_browser_",
        "mcp_database_",
        "mcp_filesystem_",
        "mcp_files_",
        "cloud_mcp_",
        "remote_mcp_",
    ):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name
