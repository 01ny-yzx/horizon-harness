"""MCP tool discovery, normalization, and invocation registry."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from config.settings import settings
from core.mcp_client import BaseMCPClient, MockMCPClient
from core.mcp_config import load_mcp_server_configs
from core.mcp_errors import (
    MCP_CLIENT_NOT_CONFIGURED,
    MCP_PERMISSION_DENIED,
    MCP_SCHEMA_INVALID,
    MCP_SERVER_DISABLED,
    MCP_SERVER_NOT_FOUND,
    MCP_TOOL_NOT_FOUND,
    mcp_error,
)
from core.mcp_filesystem import (
    infer_filesystem_permission,
    is_filesystem_server,
    validate_filesystem_arguments,
)
from core.mcp_permissions import MCPPermissionPolicy
from core.mcp_permissions import MCP_PERMISSION_LEVELS
from core.mcp_types import MCPServerConfig, MCPToolResult, MCPToolSpec


class MCPSchemaError(ValueError):
    """Raised when an MCP tool schema cannot be normalized."""


class MCPRegistry:
    """Registry for namespaced MCP tools."""

    def __init__(
        self,
        client: BaseMCPClient | None = None,
        permission_policy: MCPPermissionPolicy | None = None,
    ) -> None:
        self.servers: dict[str, MCPServerConfig] = {}
        self.client = client or MockMCPClient()
        self.permission_policy = permission_policy or MCPPermissionPolicy()
        self.tools: dict[str, MCPToolSpec] = {}
        self.schema_name_to_qualified_name: dict[str, str] = {}
        self.clients_by_server: dict[str, BaseMCPClient] = {}

    def load_servers(self, configs: list[MCPServerConfig]) -> None:
        self.servers = {config.name: config for config in configs if config.name}

    def discover_tools(self) -> list[MCPToolSpec]:
        self.tools = {}
        self.schema_name_to_qualified_name = {}
        for server in self.servers.values():
            if not server.enabled:
                continue
            self.clients_by_server[server.name] = self.client
            raw_tools = self.client.list_tools(server)
            for raw_tool in raw_tools:
                spec = self.normalize_tool_schema(server.name, raw_tool)
                if is_filesystem_server(server):
                    spec = _replace_tool_spec(
                        spec,
                        permission_level=infer_filesystem_permission(spec.name, spec.description),
                    )
                if not _server_allows_permission(server, spec.permission_level):
                    spec = _replace_tool_spec(
                        spec,
                        enabled=False,
                        metadata={
                            **spec.metadata,
                            "disabled_reason": f"MCP server permissions do not allow {spec.permission_level} tools.",
                        },
                    )
                if not self.permission_policy.is_allowed(spec):
                    spec = _replace_tool_spec(
                        spec,
                        enabled=False,
                        metadata={
                            **spec.metadata,
                            "disabled_reason": self.permission_policy.denial_reason(spec),
                        },
                    )
                spec = self._with_unique_schema_name(spec)
                self.tools[spec.qualified_name] = spec
                self.schema_name_to_qualified_name[spec.schema_name()] = spec.qualified_name
        return list(self.tools.values())

    def normalize_tool_schema(self, server_name: str, raw_tool: dict[str, Any]) -> MCPToolSpec:
        name = str(raw_tool.get("name", "")).strip()
        if not name:
            raise MCPSchemaError(MCP_SCHEMA_INVALID)
        description = str(raw_tool.get("description", "")).strip()
        input_schema = (
            raw_tool.get("inputSchema")
            or raw_tool.get("input_schema")
            or raw_tool.get("parameters")
            or {"type": "object", "properties": {}}
        )
        if not isinstance(input_schema, dict):
            raise MCPSchemaError(MCP_SCHEMA_INVALID)
        _assert_json_serializable(input_schema)
        qualified_name = f"mcp.{server_name}.{name}"
        permission_level = _explicit_permission_level(raw_tool) or self.permission_policy.infer_permission_level(name, description, input_schema)
        return MCPToolSpec(
            name=name,
            qualified_name=qualified_name,
            server_name=server_name,
            description=description,
            input_schema=input_schema,
            permission_level=permission_level,
            metadata={"raw_schema_keys": sorted(str(key) for key in raw_tool.keys())},
        )

    def get_tool(self, qualified_name: str) -> MCPToolSpec | None:
        return self.tools.get(self.resolve_tool_name(qualified_name))

    def resolve_tool_name(self, name: str) -> str:
        """Resolve schema-safe tool names back to internal qualified names."""

        if name in self.tools:
            return name
        return self.schema_name_to_qualified_name.get(name, name)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_tool_schema() for tool in self.tools.values() if tool.enabled]

    def call_tool(self, qualified_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        requested_name = qualified_name
        qualified_name = self.resolve_tool_name(qualified_name)
        tool = self.get_tool(qualified_name)
        if not tool:
            return mcp_error(MCP_TOOL_NOT_FOUND, f"MCP tool not found: {requested_name}", qualified_name=requested_name)
        server = self.servers.get(tool.server_name)
        if not server:
            return mcp_error(MCP_SERVER_NOT_FOUND, f"MCP server not found: {tool.server_name}", qualified_name=qualified_name)
        if not server.enabled:
            return mcp_error(MCP_SERVER_DISABLED, f"MCP server is disabled: {server.name}", qualified_name=qualified_name)
        if not tool.enabled or not self.permission_policy.is_allowed(tool):
            return mcp_error(
                MCP_PERMISSION_DENIED,
                self.permission_policy.denial_reason(tool) or "MCP tool is disabled.",
                server_name=tool.server_name,
                tool_name=tool.name,
                qualified_name=qualified_name,
            )
        if is_filesystem_server(server):
            preflight = validate_filesystem_arguments(
                tool.name,
                arguments,
                server,
                self.permission_policy.protected_paths,
            )
            if preflight is not None:
                return preflight
        if not self.client:
            return mcp_error(MCP_CLIENT_NOT_CONFIGURED, "MCP client is not configured.", qualified_name=qualified_name)
        try:
            client = self.clients_by_server.get(server.name, self.client)
            result = client.call_tool(server, tool.name, arguments)
        except Exception as exc:  # noqa: BLE001 - keep AgentLoop boundary normalized.
            return mcp_error(
                "mcp_execution_failed",
                str(exc),
                server_name=tool.server_name,
                tool_name=tool.name,
                qualified_name=qualified_name,
            )
        if result.qualified_name:
            return result
        return MCPToolResult(
            success=result.success,
            data=result.data,
            error=result.error,
            error_code=result.error_code,
            server_name=tool.server_name,
            tool_name=tool.name,
            qualified_name=qualified_name,
            metadata=result.metadata,
        )

    def _with_unique_schema_name(self, spec: MCPToolSpec) -> MCPToolSpec:
        schema_name = spec.schema_name()
        existing = self.schema_name_to_qualified_name.get(schema_name)
        if existing is None or existing == spec.qualified_name:
            return spec
        suffix = hashlib.sha1(spec.qualified_name.encode("utf-8")).hexdigest()[:8]
        base = schema_name[:55].rstrip("_-") or "mcp_tool"
        unique_name = f"{base}_{suffix}"
        counter = 2
        while unique_name in self.schema_name_to_qualified_name:
            unique_name = f"{base[:52]}_{counter}_{suffix[:6]}"
            counter += 1
        return _replace_tool_spec(
            spec,
            metadata={**spec.metadata, "schema_name_override": unique_name, "schema_name_base": schema_name},
        )


def _assert_json_serializable(value: Any) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise MCPSchemaError(MCP_SCHEMA_INVALID) from exc


def _replace_tool_spec(spec: MCPToolSpec, **changes: Any) -> MCPToolSpec:
    data = spec.to_dict()
    data.update(changes)
    return MCPToolSpec(**data)


def _server_allows_permission(server: MCPServerConfig, permission: str) -> bool:
    permissions = {item.strip().lower() for item in server.permissions}
    if not permissions:
        return True
    normalized = MCPPermissionPolicy().normalize_permission(permission)
    if normalized == "read_only":
        return "read_only" in permissions
    return normalized in permissions


def _explicit_permission_level(raw_tool: dict[str, Any]) -> str:
    for key in ("permission_level", "x_permission_level"):
        value = str(raw_tool.get(key, "")).strip().lower()
        if value in MCP_PERMISSION_LEVELS:
            return value
    return ""


def build_mcp_registry_from_settings(client: BaseMCPClient | None = None) -> MCPRegistry:
    """Build an optional MCP registry from runtime settings."""

    from core.mcp_stdio_client import StdioMCPClient

    registry = MCPRegistry(client=client)
    if not settings.mcp_enabled:
        return registry
    try:
        configs = load_mcp_server_configs(settings.mcp_config_path)
        if client is None:
            registry.client = StdioMCPClient()
        registry.load_servers(configs)
        registry.discover_tools()
    except Exception:  # noqa: BLE001 - MCP must not break the main agent path.
        return MCPRegistry(client=client)
    return registry
