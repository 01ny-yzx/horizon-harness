"""Minimal stdio MCP client adapter without SDK dependencies."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from typing import Any

from core.mcp_client import BaseMCPClient
from core.mcp_dependency_diagnostics import (
    command_override_key,
    expand_args_placeholders,
    resolve_command,
    suggested_fix_for_command,
)
from core.mcp_errors import (
    MCP_EXECUTION_FAILED,
    MCP_INVALID_RESPONSE,
    MCP_SERVER_DISABLED,
    MCP_SERVER_NOT_FOUND,
    MCP_TIMEOUT,
    mcp_error,
)
from core.mcp_types import MCPServerConfig, MCPToolResult


ENV_PLACEHOLDER_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class StdioMCPClient(BaseMCPClient):
    """Short-lived JSON-RPC stdio MCP client."""

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
        env = os.environ.copy()
        expanded_server_env, env_diagnostics = _expand_server_env(server.env, os.environ)
        env.update(expanded_server_env)
        resolved = resolve_command(server.command, command_override_key(server.command) or None)
        expanded_args, args_diagnostics = expand_args_placeholders(server.args, env)
        diagnostics = _base_diagnostics(server, method, resolved, args_diagnostics)
        diagnostics["env"] = env_diagnostics
        self.last_diagnostics = diagnostics
        if not server.command:
            return self._record_error(mcp_error(MCP_SERVER_NOT_FOUND, "MCP server command is empty.", server_name=server.name))
        if not resolved["command_found"]:
            return self._record_error(
                mcp_error(
                    MCP_SERVER_NOT_FOUND,
                    f"MCP server command not found: {server.command}",
                    server_name=server.name,
                )
            )

        process: subprocess.Popen[str] | None = None
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        stdout_queue: queue.Queue[str] = queue.Queue()
        try:
            process = subprocess.Popen(
                [str(resolved["resolved_command"]), *expanded_args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            _start_reader(process.stdout, stdout_lines, stdout_queue)
            _start_reader(process.stderr, stderr_lines, None)

            self._send(process, _initialize_request())
            initialize_response = self._read_response(stdout_queue, stdout_lines, 1, server.timeout_seconds, process, stderr_lines)
            diagnostics["initialize_response_preview"] = _preview(json.dumps(initialize_response, ensure_ascii=False))
            if initialize_response.get("error"):
                return self._record_error(_response_error(server, initialize_response))

            self._send(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            self._send(process, request)
            target_response = self._read_response(
                stdout_queue,
                stdout_lines,
                int(request["id"]),
                server.timeout_seconds,
                process,
                stderr_lines,
            )
            diagnostics["target_response_preview"] = _preview(json.dumps(target_response, ensure_ascii=False))
            if target_response.get("error"):
                return self._record_error(_response_error(server, target_response))
            if "result" not in target_response or not isinstance(target_response["result"], dict):
                return self._record_error(mcp_error(MCP_INVALID_RESPONSE, "MCP response missing object result.", server_name=server.name))
            result = MCPToolResult(success=True, data=target_response["result"], server_name=server.name)
            diagnostics.update({"error_code": "", "error": "", "invalid_response": False, "timed_out": False})
            return result
        except FileNotFoundError:
            return self._record_error(
                mcp_error(MCP_SERVER_NOT_FOUND, f"MCP server command not found: {server.command}", server_name=server.name)
            )
        except TimeoutError as exc:
            diagnostics["timed_out"] = True
            return self._record_error(mcp_error(MCP_TIMEOUT, str(exc), server_name=server.name))
        except json.JSONDecodeError as exc:
            diagnostics["invalid_response"] = True
            return self._record_error(mcp_error(MCP_INVALID_RESPONSE, str(exc), server_name=server.name))
        except Exception as exc:  # noqa: BLE001 - normalize process failures for caller boundary.
            return self._record_error(mcp_error(MCP_EXECUTION_FAILED, str(exc), server_name=server.name))
        finally:
            if process is not None:
                diagnostics["returncode"] = process.poll()
                diagnostics["stdout_preview"] = _preview("".join(stdout_lines))
                diagnostics["stderr_preview"] = _preview("".join(stderr_lines))
                _terminate(process)
                diagnostics["returncode"] = process.poll()

    def _send(self, process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("MCP process stdin is unavailable.")
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_response(
        self,
        stdout_queue: queue.Queue[str],
        stdout_lines: list[str],
        expected_id: int,
        timeout_seconds: float,
        process: subprocess.Popen[str] | None = None,
        stderr_lines: list[str] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_json: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            try:
                line = stdout_queue.get(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                if process is not None and process.poll() is not None:
                    self.last_diagnostics["returncode"] = process.returncode
                    self.last_diagnostics["target_response_preview"] = _preview(json.dumps(last_json or {}, ensure_ascii=False))
                    self.last_diagnostics["stdout_preview"] = _preview("".join(stdout_lines))
                    if stderr_lines is not None:
                        self.last_diagnostics["stderr_preview"] = _preview("".join(stderr_lines))
                    raise RuntimeError(f"MCP process exited before response id {expected_id}: returncode {process.returncode}")
                continue
            response = json.loads(line)
            last_json = response
            if response.get("id") == expected_id:
                return response
        self.last_diagnostics["target_response_preview"] = _preview(json.dumps(last_json or {}, ensure_ascii=False))
        self.last_diagnostics["stdout_preview"] = _preview("".join(stdout_lines))
        raise TimeoutError(f"MCP response id not found before timeout: {expected_id}")

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


def _initialize_request() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "agent", "version": "0.1.0"},
        },
    }


def _response_error(server: MCPServerConfig, response: dict[str, Any]) -> MCPToolResult:
    error = response.get("error")
    if isinstance(error, dict):
        message = str(error.get("message", "MCP execution failed."))
    else:
        message = "MCP execution failed."
    return mcp_error(MCP_EXECUTION_FAILED, message, server_name=server.name)


def _start_reader(stream: Any, lines: list[str], line_queue: queue.Queue[str] | None) -> threading.Thread | None:
    if stream is None:
        return None

    def run() -> None:
        try:
            for line in stream:
                lines.append(line)
                if line_queue is not None:
                    line_queue.put(line)
        except (UnicodeDecodeError, ValueError, OSError) as exc:
            lines.append(f"[stream_read_error] {type(exc).__name__}: {_sanitize_reader_error(exc)}\n")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _base_diagnostics(
    server: MCPServerConfig,
    request_method: str,
    resolved: dict[str, Any] | None = None,
    args_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = resolved or resolve_command(server.command, command_override_key(server.command) or None)
    args_diagnostics = args_diagnostics or {
        "args_expanded_count": 0,
        "args_unresolved_placeholders": [],
        "args_placeholders": [],
        "args_sanitized": list(server.args),
    }
    missing_dependencies = [] if resolved.get("command_found") else [server.command]
    return {
        "server_name": server.name,
        "command": server.command,
        "args": list(server.args),
        "original_command": resolved.get("original_command", server.command),
        "resolved_command": resolved.get("resolved_command", server.command),
        "command_found": bool(resolved.get("command_found")),
        "command_source": resolved.get("command_source", "not_found"),
        "dependency_status": {
            "command": server.command,
            "found": bool(resolved.get("command_found")),
            "resolved_command": resolved.get("resolved_command", server.command) if resolved.get("command_found") else "",
            "command_source": resolved.get("command_source", "not_found"),
        },
        "missing_dependencies": missing_dependencies,
        "suggested_fix": "" if resolved.get("command_found") else suggested_fix_for_command(server.command),
        **args_diagnostics,
        "request_method": request_method,
        "returncode": None,
        "stderr_preview": "",
        "stdout_preview": "",
        "initialize_response_preview": "",
        "target_response_preview": "",
        "error_code": "",
        "error": "",
        "timed_out": False,
        "invalid_response": False,
    }


def _preview(value: str, limit: int = 2000) -> str:
    return value[:limit]


def _expand_server_env(server_env: dict[str, str], host_env: dict[str, str]) -> tuple[dict[str, str], dict[str, dict[str, bool]]]:
    expanded: dict[str, str] = {}
    diagnostics: dict[str, dict[str, bool]] = {}
    for key, value in server_env.items():
        text_key = str(key)
        text_value = str(value)
        placeholder = ENV_PLACEHOLDER_PATTERN.fullmatch(text_value)
        if placeholder:
            host_key = placeholder.group(1)
            host_value = host_env.get(host_key, "")
            diagnostics[text_key] = {"placeholder": True, "configured": bool(host_value)}
            if host_value:
                expanded[text_key] = host_value
            continue
        diagnostics[text_key] = {"placeholder": False, "configured": bool(text_value)}
        expanded[text_key] = text_value
    return expanded, diagnostics


def _sanitize_reader_error(exc: Exception) -> str:
    text = str(exc)
    for marker in ("token", "api_key", "apikey", "secret", "password"):
        text = text.replace(marker, "[redacted]")
        text = text.replace(marker.upper(), "[REDACTED]")
    return text[:500]


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
