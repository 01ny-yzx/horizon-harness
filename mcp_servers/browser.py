"""Browser MCP server over newline-delimited JSON-RPC stdio."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from core.browser_policy import BrowserPolicy
from core.browser_errors import BrowserErrorCode, format_browser_error, redact_browser_error, safe_exception_summary, safe_traceback
from tools.browser_tools import BROWSER_TOOL_SCHEMAS, BROWSER_TOOLS


MAX_TEXT_CHARS = 12000


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = _handle_request(request)
            if response is not None:
                _write(response)
        except Exception as exc:  # noqa: BLE001 - MCP server boundary must stay alive.
            _log_error("browser MCP request failed", exc)
            request_id = _safe_request_id(line)
            if request_id is not None:
                _write(_error(request_id, "internal_error", "Browser MCP request failed."))


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = str(request.get("method", ""))
    request_id = request.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "browser", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [_to_mcp_tool(schema) for schema in BROWSER_TOOL_SCHEMAS]}}
    if method == "tools/call":
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        name = str(params.get("name", ""))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        return {"jsonrpc": "2.0", "id": request_id, "result": _call_tool(name, arguments)}
    return _error(request_id, "method_not_found", f"Unsupported MCP method: {method}")


def _to_mcp_tool(openai_schema: dict[str, Any]) -> dict[str, Any]:
    function = openai_schema.get("function") if isinstance(openai_schema.get("function"), dict) else {}
    return {
        "name": str(function.get("name", "")),
        "description": str(function.get("description", "")),
        "inputSchema": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object", "properties": {}},
    }


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = BROWSER_TOOLS.get(name)
    if tool is None:
        result = format_browser_error(
            BrowserErrorCode.TOOL_NOT_FOUND,
            details={"tool": name},
        )
        return _tool_result(result, is_error=True)
    boundary = evaluate_browser_mcp_server_boundary(name, arguments)
    if boundary is not None:
        return _tool_result(boundary, is_error=True)
    try:
        result = tool(**arguments)
        is_error = isinstance(result, dict) and result.get("success") is False
        return _tool_result(result, is_error=is_error)
    except Exception as exc:  # noqa: BLE001 - return structured MCP tool error.
        _log_error(f"browser tool failed: {name}", exc)
        result = format_browser_error(BrowserErrorCode.TOOL_FAILED)
        return _tool_result(result, is_error=True)


def evaluate_browser_mcp_server_boundary(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    if name not in BROWSER_TOOLS:
        return _browser_boundary_error("browser_mcp_tool_not_found", f"Unknown browser tool: {name}")

    policy = BrowserPolicy()
    if name in {"browser_extract_text", "browser_screenshot", "browser_list_links", "browser_click_and_extract", "browser_fill_form"}:
        decision = policy.check_url(str(arguments.get("url") or ""))
        if not decision.get("allowed"):
            return _browser_boundary_error(
                str(decision.get("code") or "browser_url_blocked"),
                str(decision.get("reason") or "Browser URL rejected."),
                extra={"category": "policy"},
            )
    if name == "browser_screenshot":
        decision = policy.check_screenshot()
        if not decision.get("allowed"):
            return _browser_boundary_error(
                str(decision.get("code") or "browser_screenshot_blocked"),
                str(decision.get("reason") or "Screenshot rejected."),
                extra={"category": "policy"},
            )
    if name == "browser_fill_form":
        if os.getenv("BROWSER_MCP_ALLOW_WRITE", "").strip().lower() not in {"1", "true", "yes"}:
            return _browser_boundary_error(
                "browser_mcp_write_not_allowed",
                "Browser MCP write tools are not allowed without AgentLoop ToolPlan context.",
                extra={"category": "policy"},
            )
        fields = arguments.get("fields", {})
        if not isinstance(fields, dict):
            return _browser_boundary_error("invalid_arguments", "fields must be a JSON object.", extra={"category": "policy"})
        decision = policy.check_form_fields(fields, arguments.get("submit_selector"))
        if not decision.get("allowed"):
            return _browser_boundary_error(
                str(decision.get("code") or "browser_form_blocked"),
                str(decision.get("reason") or "Form rejected."),
                extra={"category": "policy"},
            )
    return None


def _browser_boundary_error(error_code: str, message: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": error_code,
        "code": error_code,
        "error": message,
        "message": message,
        "retryable": False,
        **(extra or {}),
    }


def _tool_result(result: Any, *, is_error: bool) -> dict[str, Any]:
    safe_result = redact_browser_error(result)
    text = _bounded_json(safe_result)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": safe_result,
        "isError": bool(is_error),
    }


def _bounded_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS] + "...[truncated]"


def _safe_error(exc: Exception) -> str:
    return safe_exception_summary(exc, limit=1000)


def _log_error(prefix: str, exc: Exception) -> None:
    if os.getenv("BROWSER_MCP_DEBUG", "").strip().lower() in {"1", "true", "yes"}:
        print(f"{prefix}: {safe_traceback(exc)}", file=sys.stderr)
    else:
        print(f"{prefix}: {_safe_error(exc)}", file=sys.stderr)


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _safe_request_id(raw_line: str) -> Any:
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    return payload.get("id") if isinstance(payload, dict) else None


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
