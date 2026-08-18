"""Permission policy for MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.mcp_types import MCPToolSpec


MCP_PERMISSION_LEVELS = ("read_only", "write", "network", "shell", "dangerous")


@dataclass(frozen=True)
class MCPPermissionPolicy:
    """Infer and enforce MCP tool permissions."""

    default_permission: str = "read_only"
    disabled_permissions: tuple[str, ...] = ("dangerous",)
    require_confirmation_permissions: tuple[str, ...] = ("write", "shell", "dangerous")
    protected_paths: tuple[str, ...] = (".env", ".git/", "stores/", "node_modules/", ".venv/", "venv/", "dist/", "build/")

    def infer_permission_level(self, tool_name: str, description: str, input_schema: dict[str, Any]) -> str:
        text = f"{tool_name} {description} {' '.join(_schema_keys(input_schema))}".lower()
        dangerous_words = (
            "dangerous",
            "format",
            "reset",
            "sudo",
            "rm -rf",
            "delete",
            "remove",
            "merge",
            "transfer",
            "secret",
            "admin",
            "workflow_dispatch",
            "deploy",
            "release",
            "publish",
        )
        write_words = (
            "write",
            "create",
            "update",
            "edit",
            "save",
            "add",
            "comment",
            "assign",
            "unassign",
            "label",
            "unlabel",
            "lock",
            "unlock",
            "reopen",
            "close",
            "request",
            "review",
            "approve",
            "dismiss",
            "submit",
            "fork",
            "branch",
            "commit",
            "push",
            "upload",
            "dispatch",
            "rerun",
            "cancel",
            "edit_file",
            "move_file",
            "create_directory",
        )
        read_words = ("read", "list", "get", "search", "find", "view", "status", "check")
        first_verb = _first_tool_verb(tool_name)
        if first_verb in dangerous_words:
            return "dangerous"
        if first_verb in write_words:
            return "write"
        if first_verb in read_words:
            return "read_only"
        if any(word in text for word in dangerous_words):
            return "dangerous"
        if any(word in text for word in ("list_allowed_directories", "directory_tree", "get_file_info")):
            return "read_only"
        if any(word in text for word in ("shell", "execute", "run_command", "run shell", "terminal")):
            return "shell"
        if any(word in text for word in ("http", "url", "fetch", "download", "network", "browser")):
            return "network"
        if any(word in text for word in write_words):
            return "write"
        if any(word in text for word in read_words):
            return "read_only"
        return self.normalize_permission(self.default_permission)

    def is_allowed(self, tool_spec: MCPToolSpec) -> bool:
        return self.normalize_permission(tool_spec.permission_level) not in self.disabled_permissions

    def denial_reason(self, tool_spec: MCPToolSpec) -> str:
        permission = self.normalize_permission(tool_spec.permission_level)
        if permission in self.disabled_permissions:
            return f"MCP permission denied: {permission} tools are disabled."
        return ""

    def normalize_permission(self, value: str) -> str:
        normalized = (value or self.default_permission).strip().lower()
        if normalized not in MCP_PERMISSION_LEVELS:
            return self.default_permission
        return normalized


def _schema_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.append(str(key))
            keys.extend(_schema_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_schema_keys(item))
    return keys


def _first_tool_verb(tool_name: str) -> str:
    parts = [part for part in str(tool_name or "").lower().replace("-", "_").split("_") if part]
    return parts[0] if parts else ""
