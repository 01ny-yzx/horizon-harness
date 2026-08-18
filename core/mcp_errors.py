"""MCP error codes and result helpers."""

from __future__ import annotations

from typing import Any

from core.mcp_types import MCPToolResult


MCP_SERVER_NOT_FOUND = "mcp_server_not_found"
MCP_SERVER_DISABLED = "mcp_server_disabled"
MCP_TOOL_NOT_FOUND = "mcp_tool_not_found"
MCP_SCHEMA_INVALID = "mcp_schema_invalid"
MCP_PERMISSION_DENIED = "mcp_permission_denied"
MCP_TIMEOUT = "mcp_timeout"
MCP_EXECUTION_FAILED = "mcp_execution_failed"
MCP_INVALID_RESPONSE = "mcp_invalid_response"
MCP_CLIENT_NOT_CONFIGURED = "mcp_client_not_configured"


def mcp_error(error_code: str, message: str, **metadata: Any) -> MCPToolResult:
    """Return a standardized MCP failure result."""

    return MCPToolResult(
        success=False,
        error=message,
        error_code=error_code,
        source="mcp",
        server_name=str(metadata.pop("server_name", "")),
        tool_name=str(metadata.pop("tool_name", "")),
        qualified_name=str(metadata.pop("qualified_name", "")),
        metadata=metadata,
    )
