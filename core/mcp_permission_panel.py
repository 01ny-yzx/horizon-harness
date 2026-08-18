"""Safe MCP permission snapshot helpers for the API and frontend."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from core.mcp_install_manager import MCPInstallManager, SERVER_NAME_PATTERN
from core.mcp_marketplace_catalog import MCPMarketplaceCatalog
from core.mcp_permissions import MCP_PERMISSION_LEVELS
from core.mcp_registry import MCPRegistry


SENSITIVE_KEY_PATTERN = re.compile(r"(token|api[_-]?key|apikey|secret|password)", re.IGNORECASE)


def build_mcp_permission_snapshot(
    runtime_manager: Any,
    install_manager: MCPInstallManager | None = None,
    catalog: MCPMarketplaceCatalog | None = None,
) -> dict[str, Any]:
    registry, _status = runtime_manager.snapshot()
    install_manager = install_manager or MCPInstallManager()
    catalog = catalog or MCPMarketplaceCatalog()
    installed = {item.get("server_name"): item for item in install_manager.list_installed_mcp() if item.get("server_name")}
    servers = [_server_detail(name, registry, installed.get(name), catalog) for name in sorted(_server_names(registry, installed))]
    summary = _summary(servers)
    return sanitize_permission_snapshot(
        {
            "servers": servers,
            "summary": summary,
            "policy": {
                "levels": list(MCP_PERMISSION_LEVELS),
                "dangerous_disabled": True,
                "requires_reload_after_change": True,
            },
        }
    )


def build_server_permission_detail(
    server_name: str,
    runtime_manager: Any,
    install_manager: MCPInstallManager | None = None,
    catalog: MCPMarketplaceCatalog | None = None,
) -> dict[str, Any]:
    if not SERVER_NAME_PATTERN.fullmatch(str(server_name or "")):
        return {"success": False, "error_code": "invalid_server_name", "error": "Invalid server name."}
    snapshot = build_mcp_permission_snapshot(runtime_manager, install_manager=install_manager, catalog=catalog)
    for server in snapshot["servers"]:
        if server.get("server_name") == server_name:
            return {"success": True, "server": server}
    return {"success": False, "error_code": "mcp_server_not_found", "error": "MCP server not found."}


def summarize_tool_permissions(registry: MCPRegistry) -> dict[str, Any]:
    counts = {level: 0 for level in MCP_PERMISSION_LEVELS}
    for tool in registry.tools.values():
        level = tool.permission_level if tool.permission_level in counts else "read_only"
        counts[level] += 1
    return counts


def sanitize_permission_snapshot(data: Any) -> Any:
    if isinstance(data, dict):
        safe: dict[str, Any] = {}
        for key, value in data.items():
            if str(key) in {"env_keys", "sensitive_env_keys"} and isinstance(value, list):
                safe[key] = [str(item)[:200] for item in value]
            elif SENSITIVE_KEY_PATTERN.search(str(key)):
                safe[key] = "[redacted]"
            else:
                safe[key] = sanitize_permission_snapshot(value)
        return safe
    if isinstance(data, list):
        return [sanitize_permission_snapshot(item) for item in data]
    if isinstance(data, str):
        return _sanitize_text(data)
    return data


def _server_names(registry: MCPRegistry, installed: dict[str, dict[str, Any]]) -> set[str]:
    names = set(installed)
    names.update(registry.servers)
    names.update(tool.server_name for tool in registry.tools.values())
    return {name for name in names if name}


def _server_detail(
    server_name: str,
    registry: MCPRegistry,
    installed: dict[str, Any] | None,
    catalog: MCPMarketplaceCatalog,
) -> dict[str, Any]:
    tools = [tool for tool in registry.tools.values() if tool.server_name == server_name]
    server_config = registry.servers.get(server_name)
    if installed:
        permissions = list(installed.get("permissions") or [])
    elif server_config:
        permissions = list(server_config.permissions)
    else:
        permissions = []
    catalog_item = catalog.get_item_by_server_name(server_name)
    item = catalog_item.get("item") if catalog_item.get("success") else {}
    available_permissions = _available_permissions(item, permissions)
    env_keys = sorted(set((installed or {}).get("env_keys") or list((server_config.env if server_config else {}).keys())))
    sensitive_env_keys = sorted(key for key in env_keys if SENSITIVE_KEY_PATTERN.search(key))
    header_keys = sorted(set((installed or {}).get("header_keys") or list((server_config.headers if server_config else {}).keys())))
    sensitive_header_keys = sorted(set((installed or {}).get("sensitive_header_keys") or [key for key in header_keys if _is_sensitive_header(key)]))
    tool_items = [_tool_detail(tool) for tool in sorted(tools, key=lambda item: item.qualified_name)]
    disabled_reasons = sorted({tool.get("disabled_reason", "") for tool in tool_items if tool.get("disabled_reason")})
    permission_summary = {level: 0 for level in MCP_PERMISSION_LEVELS}
    for tool in tool_items:
        level = str(tool.get("permission_level") or "read_only")
        permission_summary[level if level in permission_summary else "read_only"] += 1
    return {
        "server_name": server_name,
        "installed": installed is not None,
        "enabled": bool((installed or {}).get("enabled", server_config.enabled if server_config else False)),
        "transport": str((installed or {}).get("transport", server_config.transport if server_config else "")),
        "url_preview": str((installed or {}).get("url_preview") or _url_preview(server_config.url if server_config else "")),
        "permissions": permissions or ["read_only"],
        "available_permissions": available_permissions,
        "permission_summary": permission_summary,
        "tools_total": len(tool_items),
        "tools_enabled": len([tool for tool in tool_items if tool.get("enabled")]),
        "tools_disabled": len([tool for tool in tool_items if not tool.get("enabled")]),
        "disabled_reasons": disabled_reasons,
        "env_keys": env_keys,
        "sensitive_env_keys": sensitive_env_keys,
        "header_keys": header_keys,
        "sensitive_header_keys": sensitive_header_keys,
        "resource_scope": _resource_scope(server_name, server_config, item),
        "tools": tool_items,
    }


def _tool_detail(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "qualified_name": tool.qualified_name,
        "description": tool.description,
        "permission_level": tool.permission_level,
        "enabled": bool(tool.enabled),
        "disabled_reason": str(tool.metadata.get("disabled_reason", "")),
        "schema_name": tool.schema_name(),
    }


def _available_permissions(item: dict[str, Any], current: list[str]) -> list[str]:
    permissions = item.get("permissions") if isinstance(item, dict) else {}
    available = permissions.get("available") if isinstance(permissions, dict) else None
    values = [str(value) for value in (available or current or ["read_only"]) if str(value) in MCP_PERMISSION_LEVELS]
    values = [value for value in values if value != "dangerous"]
    return sorted(set(["read_only", *values]), key=lambda item: MCP_PERMISSION_LEVELS.index(item))


def _resource_scope(server_name: str, server_config: Any, catalog_item: dict[str, Any]) -> dict[str, str]:
    catalog_scope = catalog_item.get("resource_scope") if isinstance(catalog_item, dict) else None
    if isinstance(catalog_scope, dict) and catalog_scope.get("type"):
        return {str(key): str(value) for key, value in catalog_scope.items() if value is not None}
    provider = str((catalog_item.get("runtime") or {}).get("type") or (catalog_item.get("category") or server_name)).lower()
    if server_name == "filesystem" or "filesystem" in server_name:
        preview = ""
        if server_config:
            args = [str(arg) for arg in server_config.args]
            preview = _path_preview(args[-1]) if args else ""
        return {"type": "filesystem", "description": "Filesystem access is limited to the configured root.", "path_preview": preview}
    return {"type": provider or "mcp", "description": "MCP server access is limited by its local configuration."}


def _summary(servers: list[dict[str, Any]]) -> dict[str, Any]:
    permission_counts = {level: 0 for level in MCP_PERMISSION_LEVELS}
    for server in servers:
        for level, count in (server.get("permission_summary") or {}).items():
            if level in permission_counts:
                permission_counts[level] += int(count)
    return {
        "servers_total": len(servers),
        "tools_total": sum(int(server.get("tools_total", 0)) for server in servers),
        "tools_enabled": sum(int(server.get("tools_enabled", 0)) for server in servers),
        "tools_disabled": sum(int(server.get("tools_disabled", 0)) for server in servers),
        "permission_counts": permission_counts,
    }


def _path_preview(value: str) -> str:
    if not value or SENSITIVE_KEY_PATTERN.search(value):
        return ""
    return Path(value).name or value[:80]


def _sanitize_text(text: str) -> str:
    for marker in ("token", "api_key", "apikey", "secret", "password"):
        text = text.replace(marker, "[redacted]")
        text = text.replace(marker.upper(), "[REDACTED]")
    return text[:1000]


def _is_sensitive_header(key: str) -> bool:
    return str(key).lower() == "authorization" or bool(SENSITIVE_KEY_PATTERN.search(str(key)))


def _url_preview(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
