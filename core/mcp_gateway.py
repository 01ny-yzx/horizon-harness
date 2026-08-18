"""Minimal HTTP JSON-RPC MCP gateway for the current MCP runtime."""

from __future__ import annotations

import json
from typing import Any

from config.settings import settings
from core.mcp_registry import MCPRegistry
from core.mcp_runtime_manager import MCPRuntimeManager
from core.mcp_types import MCPToolResult, MCPToolSpec


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agent-mcp-gateway"
SERVER_VERSION = "0.1.0"
MAX_TEXT_CHARS = 12000


def handle_jsonrpc_request(
    payload: dict[str, Any],
    runtime_manager: MCPRuntimeManager,
    *,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Handle one JSON-RPC request without API response wrapping."""

    request_id = payload.get("id") if isinstance(payload, dict) else None
    if enabled is None:
        enabled = _gateway_enabled()
    if not enabled:
        return _error_response(request_id, -32000, "MCP Gateway is disabled.")
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or "method" not in payload:
        return _error_response(request_id, -32600, "Invalid JSON-RPC request.")
    method = str(payload.get("method", ""))
    params = payload.get("params") or {}
    if params is not None and not isinstance(params, dict):
        return _error_response(request_id, -32602, "Invalid params.")
    try:
        if method == "initialize":
            return _success_response(request_id, build_initialize_result())
        if method == "notifications/initialized":
            return _success_response(request_id, {})
        if method == "tools/list":
            registry, _status = runtime_manager.snapshot()
            return _success_response(request_id, build_tools_list_result(registry))
        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return _error_response(request_id, -32602, "Invalid params.")
            raw_arguments = params.get("arguments", {})
            if raw_arguments is None:
                raw_arguments = {}
            if not isinstance(raw_arguments, dict):
                return _error_response(request_id, -32602, "Invalid params.")
            arguments = raw_arguments
            registry, _status = runtime_manager.snapshot()
            result = call_gateway_tool(registry, params["name"], arguments)
            return _success_response(request_id, _tool_result_payload(result))
        return _error_response(request_id, -32601, "Method not found.")
    except Exception:  # noqa: BLE001 - do not leak traceback through JSON-RPC.
        return _error_response(request_id, -32603, "Internal error.")


def build_initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def build_tools_list_result(registry: MCPRegistry) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for tool in registry.tools.values():
        if not tool.enabled or _is_gateway_self_loop(registry, tool):
            continue
        schema_name = tool.schema_name()
        if schema_name in used_names:
            continue
        used_names.add(schema_name)
        tools.append(_gateway_tool_schema(tool))
    return {"tools": tools}


def call_gateway_tool(registry: MCPRegistry, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
    resolved_name = registry.resolve_tool_name(tool_name)
    tool = registry.get_tool(resolved_name)
    if not tool or not tool.enabled or _is_gateway_self_loop(registry, tool):
        return MCPToolResult(
            success=False,
            error="MCP Gateway tool not found.",
            error_code="gateway_tool_not_found",
            qualified_name=resolved_name,
            metadata={"jsonrpc_code": -32001},
        )
    return registry.call_tool(resolved_name, arguments)


def gateway_status(runtime_manager: MCPRuntimeManager, *, enabled: bool | None = None) -> dict[str, Any]:
    if enabled is None:
        enabled = _gateway_enabled()
    runtime_loaded = False
    tools_total = 0
    tools_enabled = 0
    try:
        registry, status = runtime_manager.snapshot()
        runtime_loaded = True
        tools_total = len(build_tools_list_result(registry)["tools"])
        tools_enabled = tools_total
        if hasattr(status, "tools_enabled"):
            tools_enabled = min(tools_total, int(getattr(status, "tools_enabled", tools_total) or tools_total))
    except Exception:  # noqa: BLE001 - status endpoint must stay safe.
        runtime_loaded = False
    return {
        "enabled": bool(enabled),
        "endpoint": "/mcp/gateway",
        "protocol": "http-jsonrpc",
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "runtime_loaded": runtime_loaded,
        "tools_total": tools_total,
        "tools_enabled": tools_enabled,
        "transports_supported": ["http"],
        "auth": {"uses_api_key_header": True, "header": "X-API-Key"},
    }


def _gateway_tool_schema(tool: MCPToolSpec) -> dict[str, Any]:
    permission = str(tool.permission_level or "read_only")
    return {
        "name": tool.schema_name(),
        "description": tool.description,
        "inputSchema": tool.input_schema or {"type": "object", "properties": {}},
        "permission_level": permission,
        "x_permission_level": permission,
        "x_gateway_qualified_name": tool.qualified_name,
        "x_gateway_server_name": tool.server_name,
        "x_gateway_tool_name": tool.name,
    }


def _tool_result_payload(result: MCPToolResult) -> dict[str, Any]:
    if result.success:
        structured = _sanitize_mapping(result.data)
        return {
            "content": [{"type": "text", "text": _bounded_json_text(structured)}],
            "structuredContent": structured,
            "isError": False,
        }
    structured = {
        "success": False,
        "error_code": _sanitize_text(result.error_code or "mcp_execution_failed"),
        "error": _sanitize_text(result.error or "MCP tool execution failed."),
    }
    return {
        "content": [{"type": "text", "text": _bounded_json_text(structured)}],
        "structuredContent": structured,
        "isError": True,
    }


def _success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": _sanitize_text(message)}}


def _bounded_json_text(value: Any) -> str:
    limit = max(int(getattr(settings, "mcp_max_result_chars", MAX_TEXT_CHARS) or MAX_TEXT_CHARS), 100)
    text = json.dumps(_sanitize_mapping(value), ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit]


def _sanitize_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("authorization", "token", "api_key", "apikey", "secret", "password", "headers", "env")):
                safe["[redacted]"] = "[redacted]"
            else:
                safe[key] = _sanitize_mapping(item)
        return safe
    if isinstance(value, list):
        return [_sanitize_mapping(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(text: str) -> str:
    value = str(text)
    for marker in ("authorization", "token", "api_key", "apikey", "secret", "password"):
        value = value.replace(marker, "[redacted]")
        value = value.replace(marker.upper(), "[REDACTED]")
        value = value.replace(marker.title(), "[Redacted]")
    return value[:2000]


def _is_gateway_self_loop(registry: MCPRegistry, tool: MCPToolSpec) -> bool:
    server = registry.servers.get(tool.server_name)
    metadata = server.metadata if server and isinstance(server.metadata, dict) else {}
    return bool(metadata.get("gateway_self"))


def _gateway_enabled() -> bool:
    return bool(getattr(settings, "mcp_gateway_enabled", False) or getattr(settings, "mcp_enabled", False))
