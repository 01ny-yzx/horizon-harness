"""Filesystem MCP identification and safety checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.mcp_errors import MCP_PERMISSION_DENIED, mcp_error
from core.mcp_types import MCPServerConfig, MCPToolResult

READ_ONLY_FILESYSTEM_TOOLS = {
    "read_file",
    "read_multiple_files",
    "list_directory",
    "directory_tree",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
}
WRITE_FILESYSTEM_TOOLS = {"write_file", "edit_file", "create_directory", "move_file"}
PATH_ARGUMENT_KEYS = {"path", "paths", "source", "destination", "file_path", "directory_path"}


def is_filesystem_server(server: MCPServerConfig) -> bool:
    text = " ".join([server.name, server.command, *server.args]).lower()
    return (
        server.name.lower() == "filesystem"
        or "server-filesystem" in text
        or str(server.metadata.get("type", "")).lower() == "filesystem"
    )


def normalize_filesystem_tool_name(name: str) -> str:
    return name.strip()


def infer_filesystem_permission(tool_name: str, description: str) -> str:
    text = f"{tool_name} {description}".lower()
    name = normalize_filesystem_tool_name(tool_name).lower()
    if name in READ_ONLY_FILESYSTEM_TOOLS:
        return "read_only"
    if name in WRITE_FILESYSTEM_TOOLS:
        return "write"
    if "delete" in text or "remove" in text:
        return "dangerous"
    if any(word in text for word in ("execute", "shell", "run")):
        return "shell"
    return "read_only"


def is_filesystem_path_allowed(path: str, allowed_roots: list[str], protected_paths: tuple[str, ...]) -> bool:
    if not str(path).strip():
        return True
    candidate = _resolve_path(path)
    if _is_protected(candidate, protected_paths):
        return False
    roots = allowed_roots or ["."]
    for root in roots:
        root_path = _resolve_path(root)
        try:
            candidate.relative_to(root_path)
            return True
        except ValueError:
            continue
    return False


def validate_filesystem_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    server: MCPServerConfig,
    protected_paths: tuple[str, ...],
) -> MCPToolResult | None:
    permission = infer_filesystem_permission(tool_name, "")
    if permission == "dangerous":
        return _denied(server, tool_name, "Dangerous filesystem MCP tools are denied by default.")
    if permission == "shell":
        return _denied(server, tool_name, "Shell filesystem MCP tools are denied by default.")
    if permission == "write" and "write" not in {item.lower() for item in server.permissions}:
        return _denied(server, tool_name, "Filesystem MCP write tools require explicit write permission.")

    allowed_roots = _allowed_roots(server)
    for value in _iter_path_values(arguments):
        if not is_filesystem_path_allowed(str(value), allowed_roots, protected_paths):
            return _denied(server, tool_name, f"Filesystem MCP path is outside allowed roots or protected: {value}")
    return None


def _allowed_roots(server: MCPServerConfig) -> list[str]:
    roots = server.metadata.get("allowed_roots", ["."])
    if isinstance(roots, list):
        return [str(item) for item in roots]
    return ["."]


def _iter_path_values(arguments: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key, value in arguments.items():
        if key not in PATH_ARGUMENT_KEYS:
            continue
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def _resolve_path(path: str) -> Path:
    raw = str(path).replace("\\", "/")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve(strict=False)


def _is_protected(path: Path, protected_paths: tuple[str, ...]) -> bool:
    parts = {part.lower() for part in path.parts}
    for protected in protected_paths:
        normalized = protected.replace("\\", "/").strip("/").lower()
        if not normalized:
            continue
        if "/" not in normalized and normalized in parts:
            return True
        protected_path = _resolve_path(normalized)
        try:
            path.relative_to(protected_path)
            return True
        except ValueError:
            continue
    return False


def _denied(server: MCPServerConfig, tool_name: str, message: str) -> MCPToolResult:
    return mcp_error(
        MCP_PERMISSION_DENIED,
        message,
        server_name=server.name,
        tool_name=tool_name,
        qualified_name=f"mcp.{server.name}.{tool_name}",
    )
