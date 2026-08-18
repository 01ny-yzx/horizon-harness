"""HTTP JSON-RPC MCP client adapter without SDK dependencies."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

from core.mcp_client import BaseMCPClient
from core.mcp_errors import (
    MCP_EXECUTION_FAILED,
    MCP_INVALID_RESPONSE,
    MCP_SERVER_DISABLED,
    MCP_SERVER_NOT_FOUND,
    MCP_TIMEOUT,
    mcp_error,
)
from core.mcp_install_manager import SENSITIVE_KEY_PATTERN
from core.mcp_types import MCPServerConfig, MCPToolResult


PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class _InvalidMCPResponse(ValueError):
    """Raised when the remote server returns non-JSON or invalid JSON-RPC."""


class RemoteMCPClient(BaseMCPClient):
    """Short-lived HTTP JSON-RPC MCP client."""

    def __init__(self) -> None:
        self.last_diagnostics: dict[str, Any] = {}

    def list_tools(self, server: MCPServerConfig) -> list[dict[str, Any]]:
        if not server.enabled:
            self.last_diagnostics = _base_diagnostics(server, "tools/list")
            self.last_diagnostics.update({"error_code": MCP_SERVER_DISABLED, "error": "MCP server is disabled."})
            return []
        result = self._run_request(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        if not result.success:
            return []
        tools = result.data.get("tools") if isinstance(result.data, dict) else None
        return [tool for tool in tools if isinstance(tool, dict)] if isinstance(tools, list) else []

    def call_tool(self, server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        qualified_name = f"mcp.{server.name}.{tool_name}"
        if not server.enabled:
            self.last_diagnostics = _base_diagnostics(server, "tools/call")
            self.last_diagnostics.update({"error_code": MCP_SERVER_DISABLED, "error": "MCP server is disabled."})
            return mcp_error(
                MCP_SERVER_DISABLED,
                f"MCP server is disabled: {server.name}",
                server_name=server.name,
                tool_name=tool_name,
                qualified_name=qualified_name,
            )
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        result = self._run_request(server, request)
        return MCPToolResult(
            success=result.success,
            data=result.data,
            error=result.error,
            error_code=result.error_code,
            server_name=server.name,
            tool_name=tool_name,
            qualified_name=qualified_name,
            metadata=result.metadata,
        )

    def _run_request(self, server: MCPServerConfig, request: dict[str, Any]) -> MCPToolResult:
        method = str(request.get("method", ""))
        diagnostics = _base_diagnostics(server, method)
        headers, header_diagnostics = expand_header_placeholders(server.headers, os.environ)
        diagnostics["headers"] = header_diagnostics
        self.last_diagnostics = diagnostics
        url_error = _validate_url(server)
        if url_error is not None:
            diagnostics.update({"error_code": url_error.error_code, "error": url_error.error})
            return url_error
        headers = {"Content-Type": "application/json", **headers}
        try:
            initialize_response = self._post(server, _initialize_request(), headers)
            diagnostics["initialize_response_preview"] = _preview_response(initialize_response)
            if initialize_response.get("error"):
                return self._record_error(_response_error(server, initialize_response))
            target_response = self._post(server, request, headers)
            diagnostics["target_response_preview"] = _preview_response(target_response)
            if target_response.get("error"):
                return self._record_error(_response_error(server, target_response))
            result = target_response.get("result")
            if not isinstance(result, dict):
                return self._record_error(mcp_error(MCP_INVALID_RESPONSE, "MCP response missing object result.", server_name=server.name))
            diagnostics.update({"error_code": "", "error": "", "invalid_response": False, "timed_out": False})
            return MCPToolResult(success=True, data=result, server_name=server.name)
        except (_InvalidMCPResponse, json.JSONDecodeError) as exc:
            diagnostics["invalid_response"] = True
            return self._record_error(mcp_error(MCP_INVALID_RESPONSE, _sanitize_text(str(exc)), server_name=server.name))
        except requests.Timeout as exc:
            diagnostics["timed_out"] = True
            return self._record_error(mcp_error(MCP_TIMEOUT, _sanitize_text(str(exc) or "Remote MCP request timed out."), server_name=server.name))
        except requests.RequestException as exc:
            return self._record_error(
                mcp_error(MCP_EXECUTION_FAILED, _sanitize_text(str(exc) or "Remote MCP request failed."), server_name=server.name)
            )
        except Exception as exc:  # noqa: BLE001 - normalize remote client failures.
            return self._record_error(mcp_error(MCP_EXECUTION_FAILED, _sanitize_text(str(exc)), server_name=server.name))

    def _post(self, server: MCPServerConfig, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        response = requests.post(server.url, headers=headers, json=payload, timeout=server.timeout_seconds)
        self.last_diagnostics["status_code"] = response.status_code
        if response.status_code < 200 or response.status_code >= 300:
            raise requests.RequestException(f"Remote MCP HTTP status {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise _InvalidMCPResponse(str(exc) or "MCP response is not valid JSON.") from exc
        _validate_jsonrpc_response(data)
        return data

    def _record_error(self, result: MCPToolResult) -> MCPToolResult:
        self.last_diagnostics.update(
            {
                "error_code": result.error_code,
                "error": result.error,
                "timed_out": result.error_code == MCP_TIMEOUT,
                "invalid_response": result.error_code == MCP_INVALID_RESPONSE,
            }
        )
        return result


def expand_header_placeholders(headers: dict[str, str], environ: Mapping[str, str]) -> tuple[dict[str, str], dict[str, dict[str, bool]]]:
    expanded: dict[str, str] = {}
    diagnostics: dict[str, dict[str, bool]] = {}
    for key, value in headers.items():
        text_key = str(key)
        text_value = str(value)
        placeholders = PLACEHOLDER_PATTERN.findall(text_value)
        configured = True
        output = text_value
        for name in placeholders:
            env_value = environ.get(name, "")
            configured = configured and bool(env_value)
            output = output.replace(f"${{{name}}}", env_value)
        if placeholders and not all(environ.get(name, "") for name in placeholders):
            output = ""
        diagnostics[text_key] = {"configured": bool(output) if placeholders else bool(text_value), "sensitive": _is_sensitive_header(text_key)}
        if output:
            expanded[text_key] = output
    return expanded, diagnostics


def _initialize_request() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agent-remote-mcp-client", "version": "0.1.0"},
        },
    }


def _validate_url(server: MCPServerConfig) -> MCPToolResult | None:
    if not str(server.url or "").strip():
        return mcp_error("remote_mcp_url_missing", "Remote MCP URL is required.", server_name=server.name)
    parsed = urlparse(server.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return mcp_error(MCP_SERVER_NOT_FOUND, "Remote MCP URL must use http or https.", server_name=server.name)
    return None


def _validate_jsonrpc_response(data: object) -> None:
    if not isinstance(data, dict):
        raise _InvalidMCPResponse("JSON-RPC response must be an object.")
    if data.get("jsonrpc") != "2.0":
        raise _InvalidMCPResponse("JSON-RPC response missing version 2.0.")
    if "id" not in data:
        raise _InvalidMCPResponse("JSON-RPC response missing id.")

    has_result = "result" in data
    has_error = "error" in data
    if has_result == has_error:
        raise _InvalidMCPResponse("JSON-RPC response must include exactly one of result or error.")
    if has_error:
        error = data.get("error")
        if not isinstance(error, dict):
            raise _InvalidMCPResponse("JSON-RPC error must be an object.")
        if not isinstance(error.get("code"), int) or not isinstance(error.get("message"), str):
            raise _InvalidMCPResponse("JSON-RPC error must include integer code and string message.")


def _response_error(server: MCPServerConfig, response: dict[str, Any]) -> MCPToolResult:
    error = response.get("error")
    message = str(error.get("message", "MCP execution failed.")) if isinstance(error, dict) else "MCP execution failed."
    return mcp_error(MCP_EXECUTION_FAILED, _sanitize_text(message), server_name=server.name)


def _base_diagnostics(server: MCPServerConfig, request_method: str) -> dict[str, Any]:
    return {
        "transport": "http",
        "server_name": server.name,
        "url_preview": _url_preview(server.url),
        "method": request_method,
        "timeout_seconds": server.timeout_seconds,
        "headers": {},
        "status_code": None,
        "error_code": "",
        "error": "",
        "initialize_response_preview": "",
        "target_response_preview": "",
        "timed_out": False,
        "invalid_response": False,
    }


def _url_preview(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _preview_response(value: dict[str, Any], limit: int = 2000) -> str:
    return _sanitize_text(json.dumps(value, ensure_ascii=False, default=str))[:limit]


def _sanitize_text(text: str) -> str:
    value = str(text)
    for marker in ("token", "api_key", "apikey", "secret", "password", "authorization"):
        value = value.replace(marker, "[redacted]")
        value = value.replace(marker.upper(), "[REDACTED]")
        value = value.replace(marker.title(), "[Redacted]")
    return value[:2000]


def _is_sensitive_header(key: str) -> bool:
    return str(key).lower() == "authorization" or bool(SENSITIVE_KEY_PATTERN.search(str(key)))
