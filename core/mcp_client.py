"""MCP client abstraction and offline mock implementation."""

from __future__ import annotations

from typing import Any

from core.mcp_errors import MCP_EXECUTION_FAILED, MCP_SERVER_DISABLED, MCP_TOOL_NOT_FOUND, mcp_error
from core.mcp_types import MCPServerConfig, MCPToolResult


class BaseMCPClient:
    """Protocol adapter boundary for future real MCP SDK clients."""

    def list_tools(self, server: MCPServerConfig) -> list[dict[str, Any]]:
        raise NotImplementedError

    def call_tool(self, server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        raise NotImplementedError


class MockMCPClient(BaseMCPClient):
    """Offline MCP client for tests."""

    def __init__(
        self,
        tools: dict[str, list[dict[str, Any]]] | None = None,
        responses: dict[tuple[str, str], Any] | None = None,
    ) -> None:
        self.tools = tools or {}
        self.responses = responses or {}

    def list_tools(self, server: MCPServerConfig) -> list[dict[str, Any]]:
        if not server.enabled:
            return []
        return [dict(item) for item in self.tools.get(server.name, [])]

    def call_tool(self, server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        qualified_name = f"mcp.{server.name}.{tool_name}"
        if not server.enabled:
            return mcp_error(
                MCP_SERVER_DISABLED,
                f"MCP server is disabled: {server.name}",
                server_name=server.name,
                tool_name=tool_name,
                qualified_name=qualified_name,
            )
        known_tools = {str(item.get("name", "")) for item in self.tools.get(server.name, [])}
        if tool_name not in known_tools:
            return mcp_error(
                MCP_TOOL_NOT_FOUND,
                f"MCP tool not found: {tool_name}",
                server_name=server.name,
                tool_name=tool_name,
                qualified_name=qualified_name,
            )
        try:
            response = self.responses.get((server.name, tool_name), {"arguments": arguments})
            if isinstance(response, Exception):
                raise response
            if isinstance(response, MCPToolResult):
                return response
            return MCPToolResult(
                success=True,
                data=response,
                server_name=server.name,
                tool_name=tool_name,
                qualified_name=qualified_name,
            )
        except Exception as exc:  # noqa: BLE001 - normalize mock failures like real client failures.
            return mcp_error(
                MCP_EXECUTION_FAILED,
                str(exc),
                server_name=server.name,
                tool_name=tool_name,
                qualified_name=qualified_name,
            )
