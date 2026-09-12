"""Static tool risk registry for boundary contract audits.

The registry is a declarative contract only. It does not dispatch tools,
authorize calls, inspect user intent, or change runtime behavior.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ToolRiskMetadata:
    tool_name: str
    canonical_name: str
    family: str
    operation: str
    side_effect: bool
    mutates_state: bool
    external_io: bool
    requires_tool_plan: bool
    requires_access_mode: bool
    required_capabilities: tuple[str, ...]
    boundary_categories: tuple[str, ...]
    idempotency_policy: str
    source: str
    notes: tuple[str, ...] = ()


READ = "read"
WRITE = "write"
EXECUTE = "execute"
NETWORK_READ = "network_read"
BROWSER_READ = "browser_read"
BROWSER_WRITE = "browser_write"
DATABASE_READ = "database_read"
DATABASE_WRITE = "database_write"
GIT_READ = "git_read"
GIT_MUTATION = "git_mutation"
STATE_MUTATION = "state_mutation"


def _meta(
    tool_name: str,
    *,
    family: str,
    operation: str,
    side_effect: bool = False,
    mutates_state: bool | None = None,
    external_io: bool = False,
    requires_tool_plan: bool | None = None,
    requires_access_mode: bool | None = None,
    required_capabilities: tuple[str, ...] = (),
    boundary_categories: tuple[str, ...] = (),
    idempotency_policy: str = "none",
    source: str = "local",
    canonical_name: str | None = None,
    notes: tuple[str, ...] = (),
) -> ToolRiskMetadata:
    mutates = side_effect if mutates_state is None else mutates_state
    return ToolRiskMetadata(
        tool_name=tool_name,
        canonical_name=canonical_name or tool_name,
        family=family,
        operation=operation,
        side_effect=side_effect,
        mutates_state=mutates,
        external_io=external_io,
        requires_tool_plan=side_effect if requires_tool_plan is None else requires_tool_plan,
        requires_access_mode=side_effect if requires_access_mode is None else requires_access_mode,
        required_capabilities=tuple(required_capabilities),
        boundary_categories=tuple(boundary_categories),
        idempotency_policy=idempotency_policy,
        source=source,
        notes=tuple(notes),
    )


_LOCAL_TOOL_RISK_METADATA: dict[str, ToolRiskMetadata] = {
    "browser_click_and_extract": _meta(
        "browser_click_and_extract",
        family="browser",
        operation=BROWSER_WRITE,
        side_effect=True,
        external_io=True,
        boundary_categories=("browser_url", "tool_authorization"),
        required_capabilities=("browser",),
        idempotency_policy="task_exact",
    ),
    "browser_extract_text": _meta("browser_extract_text", family="browser", operation=BROWSER_READ, external_io=True, boundary_categories=("browser_url",)),
    "browser_fill_form": _meta(
        "browser_fill_form",
        family="browser",
        operation=BROWSER_WRITE,
        side_effect=True,
        external_io=True,
        required_capabilities=("browser",),
        boundary_categories=("browser_url", "tool_authorization"),
        idempotency_policy="task_exact",
    ),
    "browser_list_links": _meta("browser_list_links", family="browser", operation=BROWSER_READ, external_io=True, boundary_categories=("browser_url",)),
    "browser_screenshot": _meta("browser_screenshot", family="browser", operation=BROWSER_READ, external_io=True, boundary_categories=("browser_url",)),
    "cleanup_cache": _meta("cleanup_cache", family="cache", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "cleanup_sandbox": _meta("cleanup_sandbox", family="shell", operation=STATE_MUTATION, side_effect=True, boundary_categories=("sandbox",)),
    "clear_cache": _meta("clear_cache", family="cache", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "clear_chunks": _meta("clear_chunks", family="document", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "clear_documents": _meta("clear_documents", family="document", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "clear_memory_type": _meta("clear_memory_type", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "clear_rag_context": _meta("clear_rag_context", family="rag", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "clear_usage": _meta("clear_usage", family="cache", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "clear_vectors": _meta("clear_vectors", family="vector", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "embed_all_chunks": _meta("embed_all_chunks", family="vector", operation=STATE_MUTATION, side_effect=True, external_io=True, boundary_categories=("state_mutation",)),
    "embed_chunk": _meta("embed_chunk", family="vector", operation=STATE_MUTATION, side_effect=True, external_io=True, boundary_categories=("state_mutation",)),
    "embed_document_chunks": _meta("embed_document_chunks", family="vector", operation=STATE_MUTATION, side_effect=True, external_io=True, boundary_categories=("state_mutation",)),
    "fetch_url": _meta("fetch_url", family="web", operation=NETWORK_READ, external_io=True),
    "find_documents": _meta("find_documents", family="document", operation=READ, boundary_categories=("document_read",)),
    "find_files": _meta("find_files", family="project", operation=READ, boundary_categories=("file_read",)),
    "delete_memory_reference": _meta("delete_memory_reference", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "delete_project_instruction": _meta("delete_project_instruction", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "delete_user_preference": _meta("delete_user_preference", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "get_browser_status": _meta("get_browser_status", family="browser", operation=READ, boundary_categories=("browser_status",)),
    "get_cache_status": _meta("get_cache_status", family="cache", operation=READ),
    "get_chunk": _meta("get_chunk", family="document", operation=READ, boundary_categories=("document_read",)),
    "get_context_status": _meta("get_context_status", family="context", operation=READ),
    "get_current_path": _meta("get_current_path", family="shell", operation=READ, boundary_categories=("sandbox",)),
    "get_embedding_status": _meta("get_embedding_status", family="vector", operation=READ, external_io=True),
    "get_project_tree": _meta("get_project_tree", family="project", operation=READ, boundary_categories=("file_read",)),
    "get_rag_status": _meta("get_rag_status", family="rag", operation=READ),
    "get_sandbox_status": _meta("get_sandbox_status", family="shell", operation=READ, boundary_categories=("sandbox",)),
    "get_usage_status": _meta("get_usage_status", family="cache", operation=READ),
    "get_workspace_status": _meta("get_workspace_status", family="workspace", operation=READ),
    "git_add": _meta("git_add", family="git", operation=GIT_MUTATION, side_effect=True, required_capabilities=("git", "coding"), boundary_categories=("tool_authorization",), idempotency_policy="batch_exact"),
    "git_commit": _meta("git_commit", family="git", operation=GIT_MUTATION, side_effect=True, required_capabilities=("git", "coding"), boundary_categories=("tool_authorization",), idempotency_policy="task_exact"),
    "git_diff": _meta("git_diff", family="git", operation=GIT_READ, boundary_categories=("git_read",)),
    "git_diff_summary": _meta("git_diff_summary", family="git", operation=GIT_READ, boundary_categories=("git_read",)),
    "git_is_repo": _meta("git_is_repo", family="git", operation=GIT_READ, boundary_categories=("git_read",)),
    "git_log": _meta("git_log", family="git", operation=GIT_READ, boundary_categories=("git_read",)),
    "git_restore_file": _meta("git_restore_file", family="git", operation=GIT_MUTATION, side_effect=True, required_capabilities=("git", "coding"), boundary_categories=("tool_authorization",), idempotency_policy="batch_exact"),
    "git_status": _meta("git_status", family="git", operation=GIT_READ, boundary_categories=("git_read",)),
    "git_suggest_commit_message": _meta(
        "git_suggest_commit_message",
        family="git",
        operation=GIT_READ,
        boundary_categories=("git_read",),
        notes=("suggests text from git status only; not a git mutation contract",),
    ),
    "hybrid_search_chunks": _meta("hybrid_search_chunks", family="vector", operation=READ, external_io=True),
    "inspect_context_summary": _meta("inspect_context_summary", family="context", operation=READ),
    "list_chunks": _meta("list_chunks", family="document", operation=READ, boundary_categories=("document_read",)),
    "list_documents": _meta("list_documents", family="document", operation=READ, boundary_categories=("document_read",)),
    "list_files": _meta("list_files", family="file", operation=READ, boundary_categories=("file_read",)),
    "list_vectors": _meta("list_vectors", family="vector", operation=READ),
    "list_workspaces": _meta("list_workspaces", family="workspace", operation=READ),
    "load_document": _meta("load_document", family="document", operation=STATE_MUTATION, side_effect=True, boundary_categories=("file_read", "state_mutation"), idempotency_policy="task_resource_exact"),
    "load_documents_from_directory": _meta("load_documents_from_directory", family="document", operation=STATE_MUTATION, side_effect=True, boundary_categories=("file_read", "state_mutation"), idempotency_policy="task_resource_exact"),
    "rag_query": _meta("rag_query", family="rag", operation=READ, external_io=True),
    "read_document": _meta("read_document", family="file", operation=READ, boundary_categories=("file_read",)),
    "read_file": _meta("read_file", family="file", operation=READ, boundary_categories=("file_read",)),
    "read_memory_reference": _meta("read_memory_reference", family="memory", operation=READ),
    "rebuild_chunks_for_document": _meta("rebuild_chunks_for_document", family="document", operation=STATE_MUTATION, side_effect=True, boundary_categories=("file_read", "state_mutation"), idempotency_policy="task_resource_exact"),
    "remember_project_summary": _meta("remember_project_summary", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "remember_project_instruction": _meta("remember_project_instruction", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "remember_stable_fact": _meta("remember_stable_fact", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "remember_user_preference": _meta("remember_user_preference", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "update_project_instruction": _meta("update_project_instruction", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "update_stable_fact": _meta("update_stable_fact", family="memory", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "remove_document": _meta("remove_document", family="document", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "replace_in_file": _meta("replace_in_file", family="file", operation=WRITE, side_effect=True, required_capabilities=("coding", "file_write"), boundary_categories=("file_write", "tool_authorization"), idempotency_policy="task_exact"),
    "sandbox_exec": _meta("sandbox_exec", family="shell", operation=EXECUTE, side_effect=True, required_capabilities=("shell_sandbox", "validation"), boundary_categories=("sandbox", "tool_authorization"), idempotency_policy="batch_exact"),
    "search_memory_references": _meta("search_memory_references", family="memory", operation=READ),
    "search_document_chunks": _meta("search_document_chunks", family="document", operation=READ, boundary_categories=("document_read",)),
    "search_text": _meta("search_text", family="project", operation=READ, boundary_categories=("file_read",)),
    "semantic_search_chunks": _meta("semantic_search_chunks", family="vector", operation=READ, external_io=True),
    "switch_workspace": _meta("switch_workspace", family="workspace", operation=STATE_MUTATION, side_effect=True, boundary_categories=("state_mutation",)),
    "web_search": _meta("web_search", family="web", operation=NETWORK_READ, external_io=True, boundary_categories=("browser_url",)),
    "write_file": _meta("write_file", family="file", operation=WRITE, side_effect=True, required_capabilities=("file_write",), boundary_categories=("file_write", "tool_authorization"), idempotency_policy="task_exact"),
}


_MCP_FILESYSTEM_CONTRACT_METADATA: dict[str, ToolRiskMetadata] = {
    "mcp_filesystem.read_file": _meta("read_file", canonical_name="mcp_filesystem.read_file", family="mcp_filesystem", operation=READ, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_read")),
    "mcp_filesystem.read_multiple_files": _meta("read_multiple_files", canonical_name="mcp_filesystem.read_multiple_files", family="mcp_filesystem", operation=READ, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_read")),
    "mcp_filesystem.list_directory": _meta("list_directory", canonical_name="mcp_filesystem.list_directory", family="mcp_filesystem", operation=READ, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_read")),
    "mcp_filesystem.directory_tree": _meta("directory_tree", canonical_name="mcp_filesystem.directory_tree", family="mcp_filesystem", operation=READ, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_read")),
    "mcp_filesystem.search_files": _meta("search_files", canonical_name="mcp_filesystem.search_files", family="mcp_filesystem", operation=READ, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_read")),
    "mcp_filesystem.get_file_info": _meta("get_file_info", canonical_name="mcp_filesystem.get_file_info", family="mcp_filesystem", operation=READ, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_read")),
    "mcp_filesystem.list_allowed_directories": _meta("list_allowed_directories", canonical_name="mcp_filesystem.list_allowed_directories", family="mcp_filesystem", operation=READ, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_read")),
    "mcp_filesystem.write_file": _meta("write_file", canonical_name="mcp_filesystem.write_file", family="mcp_filesystem", operation=WRITE, side_effect=True, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_write"), idempotency_policy="task_exact", notes=("provider-specific write_file contract; local write_file remains file/write",)),
    "mcp_filesystem.edit_file": _meta("edit_file", canonical_name="mcp_filesystem.edit_file", family="mcp_filesystem", operation=WRITE, side_effect=True, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_write")),
    "mcp_filesystem.create_directory": _meta("create_directory", canonical_name="mcp_filesystem.create_directory", family="mcp_filesystem", operation=WRITE, side_effect=True, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_write")),
    "mcp_filesystem.move_file": _meta("move_file", canonical_name="mcp_filesystem.move_file", family="mcp_filesystem", operation=WRITE, side_effect=True, source="mcp_filesystem", boundary_categories=("mcp_filesystem", "file_write")),
}


_DATABASE_WRITE_CONTRACT_METADATA: dict[str, ToolRiskMetadata] = {
    "get_database_status": _meta("get_database_status", family="database", operation=DATABASE_READ, source="contract", boundary_categories=("database",)),
    "database_test_connection": _meta("database_test_connection", family="database", operation=DATABASE_READ, source="contract", boundary_categories=("database",)),
    "database_get_connection_config": _meta("database_get_connection_config", family="database", operation=DATABASE_READ, source="contract", boundary_categories=("database",)),
    "database_list_tables": _meta("database_list_tables", family="database", operation=DATABASE_READ, source="contract", boundary_categories=("database",)),
    "database_describe_table": _meta("database_describe_table", family="database", operation=DATABASE_READ, source="contract", boundary_categories=("database",)),
    "database_select_query": _meta("database_select_query", family="database", operation=DATABASE_READ, source="contract", boundary_categories=("database",)),
    "database_get_sample_rows": _meta("database_get_sample_rows", family="database", operation=DATABASE_READ, source="contract", boundary_categories=("database",)),
    "database_write_contract": _meta(
        "database_*_write",
        canonical_name="database_write_contract",
        family="database",
        operation=DATABASE_WRITE,
        side_effect=True,
        required_capabilities=("database_write",),
        boundary_categories=("database", "fail_closed_candidate"),
        source="contract",
        notes=("unknown database write or mutation tools are fail-closed boundary candidates",),
    ),
    "database_delete_rows": _meta("database_delete_rows", family="database", operation=DATABASE_WRITE, side_effect=True, required_capabilities=("database_write",), boundary_categories=("database", "fail_closed_candidate"), source="contract"),
    "database_update_rows": _meta("database_update_rows", family="database", operation=DATABASE_WRITE, side_effect=True, required_capabilities=("database_write",), boundary_categories=("database", "fail_closed_candidate"), source="contract"),
    "database_write_rows": _meta("database_write_rows", family="database", operation=DATABASE_WRITE, side_effect=True, required_capabilities=("database_write",), boundary_categories=("database", "fail_closed_candidate"), source="contract"),
    "database_drop_table": _meta("database_drop_table", family="database", operation=DATABASE_WRITE, side_effect=True, required_capabilities=("database_write",), boundary_categories=("database", "fail_closed_candidate"), source="contract"),
    "database_execute_write": _meta("database_execute_write", family="database", operation=DATABASE_WRITE, side_effect=True, required_capabilities=("database_write",), boundary_categories=("database", "fail_closed_candidate"), source="contract"),
}


_TOOL_RISK_METADATA: dict[str, ToolRiskMetadata] = {
    **_LOCAL_TOOL_RISK_METADATA,
    **_MCP_FILESYSTEM_CONTRACT_METADATA,
    **_DATABASE_WRITE_CONTRACT_METADATA,
}


def get_tool_risk_metadata(tool_name: str) -> ToolRiskMetadata | None:
    return _TOOL_RISK_METADATA.get(tool_name)


def require_tool_risk_metadata(tool_name: str) -> ToolRiskMetadata:
    metadata = get_tool_risk_metadata(tool_name)
    if metadata is None:
        raise KeyError(f"Tool risk metadata is not registered: {tool_name}")
    return metadata


def all_tool_risk_metadata() -> dict[str, ToolRiskMetadata]:
    return dict(_TOOL_RISK_METADATA)


def known_tool_names() -> set[str]:
    return set(_TOOL_RISK_METADATA)


def side_effect_tool_names() -> set[str]:
    return {name for name, metadata in _TOOL_RISK_METADATA.items() if metadata.side_effect}


def tools_requiring_tool_plan() -> set[str]:
    return {name for name, metadata in _TOOL_RISK_METADATA.items() if metadata.requires_tool_plan}


def tools_by_family(family: str) -> set[str]:
    return {name for name, metadata in _TOOL_RISK_METADATA.items() if metadata.family == family}


def tools_by_operation(operation: str) -> set[str]:
    return {name for name, metadata in _TOOL_RISK_METADATA.items() if metadata.operation == operation}


def tool_risk_metadata_to_dict(metadata: ToolRiskMetadata) -> dict[str, Any]:
    payload = asdict(metadata)
    payload["required_capabilities"] = list(metadata.required_capabilities)
    payload["boundary_categories"] = list(metadata.boundary_categories)
    payload["notes"] = list(metadata.notes)
    return payload


def mcp_permission_level_to_risk_metadata(
    *,
    tool_name: str,
    canonical_name: str,
    permission_level: str,
    server_name: str,
) -> ToolRiskMetadata:
    level = str(permission_level or "read_only").strip().lower()
    if level == "network":
        return _meta(
            tool_name,
            canonical_name=canonical_name,
            family="unknown",
            operation=NETWORK_READ,
            external_io=True,
            source="mcp",
            boundary_categories=("mcp", "network"),
            notes=(f"dynamic MCP tool from server {server_name}",),
        )
    if level == "write":
        return _meta(
            tool_name,
            canonical_name=canonical_name,
            family="unknown",
            operation=WRITE,
            side_effect=True,
            source="mcp",
            boundary_categories=("mcp", "tool_authorization"),
            notes=(f"dynamic MCP write tool from server {server_name}",),
        )
    if level == "shell":
        return _meta(
            tool_name,
            canonical_name=canonical_name,
            family="unknown",
            operation=EXECUTE,
            side_effect=True,
            source="mcp",
            boundary_categories=("mcp", "tool_authorization", "sandbox"),
            notes=(f"dynamic MCP shell tool from server {server_name}",),
        )
    if level == "dangerous":
        return _meta(
            tool_name,
            canonical_name=canonical_name,
            family="unknown",
            operation=STATE_MUTATION,
            side_effect=True,
            source="mcp",
            boundary_categories=("mcp", "tool_authorization", "fail_closed_candidate"),
            notes=(f"dynamic MCP dangerous tool from server {server_name}",),
        )
    return _meta(
        tool_name,
        canonical_name=canonical_name,
        family="unknown",
        operation=READ,
        source="mcp",
        boundary_categories=("mcp", "read_only"),
        notes=(f"dynamic MCP read-only tool from server {server_name}",),
    )
