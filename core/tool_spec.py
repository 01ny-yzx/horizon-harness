"""ToolSpec contracts for registry-backed tool metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ToolProvider:
    LOCAL = "local"
    MCP = "mcp"


class ToolKind:
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    EXECUTION = "execution"
    BROWSER_READ = "browser_read"
    BROWSER_WRITE = "browser_write"
    WEB_READ = "web_read"
    DATABASE_READ = "database_read"
    DATABASE_WRITE = "database_write"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"
    RAG = "rag"
    MEMORY = "memory"
    CACHE = "cache"
    CONTEXT = "context"
    PROJECT = "project"
    STATUS = "status"
    MCP = "mcp"
    UNKNOWN = "unknown"


class ToolRisk:
    READ_ONLY = "read_only"
    FILE_WRITE = "file_write"
    CODE_EXECUTION = "code_execution"
    SHELL_EXECUTION = "shell_execution"
    NETWORK = "network"
    BROWSER_SIDE_EFFECT = "browser_side_effect"
    DATABASE_READ = "database_read"
    DATABASE_WRITE = "database_write"
    GIT_MUTATION = "git_mutation"
    MCP_EXTERNAL = "mcp_external"
    INTERNAL_STATE = "internal_state"
    UNKNOWN = "unknown"


class ToolPathPolicy:
    NONE = "none"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    PROJECT_PATH = "project_path"
    EXTERNAL_OUTPUT = "external_output"
    URL = "url"
    COMMAND = "command"


_RUNTIME_CAPABILITY_BY_KIND = {
    ToolKind.FILE_READ: "file_read",
    ToolKind.FILE_WRITE: "file_write",
    ToolKind.EXECUTION: "command_exec",
    ToolKind.WEB_READ: "network",
    ToolKind.BROWSER_READ: "browser",
    ToolKind.BROWSER_WRITE: "browser",
    ToolKind.DATABASE_READ: "database",
    ToolKind.DATABASE_WRITE: "database",
    ToolKind.GIT_READ: "git",
    ToolKind.GIT_WRITE: "git",
    ToolKind.MEMORY: "memory",
    ToolKind.MCP: "mcp",
}

_RUNTIME_CAPABILITY_ALIASES = {
    "coding": "code_edit",
    "execution": "command_exec",
    "shell_sandbox": "command_exec",
    "python_sandbox": "command_exec",
    "web_search": "network",
    "web_fetch": "network",
    "browser_url": "network",
    "git_read": "git",
    "git_write": "git",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    provider: str
    kind: str
    capabilities: tuple[str, ...] = ()
    input_schema: dict[str, Any] | None = None
    description: str = ""
    side_effect: bool = False
    mutates_files: bool = False
    executes_code: bool = False
    reads_files: bool = False
    uses_network: bool = False
    uses_browser: bool = False
    uses_database: bool = False
    requires_path_guard: bool = False
    path_policy: str = ToolPathPolicy.NONE
    risk: str = ToolRisk.UNKNOWN
    aliases: tuple[str, ...] = ()
    canonical_name: str | None = None


def structured_runtime_capabilities_for_tool_spec(spec: Any | None) -> frozenset[str]:
    """Return one shared runtime-capability view of ToolSpec metadata."""

    if spec is None:
        return frozenset()
    result: set[str] = set()
    for value in getattr(spec, "capabilities", ()) or ():
        capability = str(value or "").strip().lower()
        if not capability:
            continue
        result.add(capability)
        alias = _RUNTIME_CAPABILITY_ALIASES.get(capability)
        if alias:
            result.add(alias)

    kind = str(getattr(spec, "kind", "") or "").strip()
    kind_capability = _RUNTIME_CAPABILITY_BY_KIND.get(kind, "")
    if kind_capability and not (
        kind_capability == "file_read" and "document_load" in result
    ):
        result.add(kind_capability)
    if str(getattr(spec, "provider", "") or "") == ToolProvider.MCP:
        result.add("mcp")
    if kind == ToolKind.FILE_WRITE:
        result.add("artifact_output")
    if kind == ToolKind.EXECUTION:
        result.add("validation")
    return frozenset(result)


def tool_spec_supports_runtime_capability(
    spec: Any | None,
    capability: str,
) -> bool:
    """Return whether a ToolSpec can satisfy one structured runtime capability."""

    normalized = str(capability or "").strip().lower()
    return bool(
        normalized
        and normalized in structured_runtime_capabilities_for_tool_spec(spec)
    )
