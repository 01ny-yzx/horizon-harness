"""Shell-related tools backed by sandbox policy checks."""

from __future__ import annotations

from typing import Any

from core.command_execution_context import get_host_command_context
from core.sandbox import current_sandbox_manager


def run_shell_in_sandbox(command: str, timeout: int = 30) -> dict[str, Any]:
    """Legacy/internal shell helper; Agent-visible execution uses sandbox_exec."""

    return current_sandbox_manager().run_local_command(command, timeout=timeout)


def sandbox_exec(command: str, cwd: str | None = None, timeout: int = 30) -> dict[str, Any]:
    """Canonical sandbox execution tool for shell commands."""

    result = current_sandbox_manager().run_local_command(command, cwd=cwd, timeout=timeout)
    return _sandbox_exec_result(result, command=command, requested_cwd=cwd)


def get_sandbox_status() -> dict[str, Any]:
    """Return local-host execution configuration and the temporary directory."""

    return current_sandbox_manager().status()


def cleanup_sandbox() -> dict[str, Any]:
    """Clear the current workspace sandbox directory."""

    return current_sandbox_manager().cleanup_sandbox()


def run_command(cmd: str) -> dict[str, Any]:
    """Legacy/internal shell helper; not exposed in Agent-visible schemas."""

    return run_shell_in_sandbox(command=cmd, timeout=30)


def get_current_path() -> dict[str, Any]:
    """Return process, project, and temporary paths."""

    import os

    context = get_host_command_context()
    return {
        "success": True,
        "data": {
            "process_cwd": os.getcwd(),
            "project_root": str(context.project_root),
            "sandbox_dir": str(context.sandbox_dir),
            "mode": "local_host",
        },
        "error": None,
    }


def run_python_code(code: str) -> dict[str, Any]:
    """Legacy/internal Python helper; not exposed in Agent-visible schemas."""

    return current_sandbox_manager().run_python_code(code, timeout=30)


def _sandbox_exec_result(result: dict[str, Any], *, command: str, requested_cwd: str | None) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    normalized = dict(data)
    exit_code = normalized.get("exit_code", normalized.get("returncode"))
    normalized["exit_code"] = exit_code
    normalized.setdefault("returncode", exit_code)
    normalized["stdout"] = str(normalized.get("stdout") or "")
    normalized["stderr"] = str(normalized.get("stderr") or "")
    normalized["command"] = command
    normalized["cwd"] = str(normalized.get("cwd") or requested_cwd or normalized.get("project_root") or "")
    normalized["mode"] = "local_host"

    success = bool(result.get("success"))
    error = result.get("error")
    error_payload = error if isinstance(error, dict) else {}
    error_code = str(error_payload.get("code") or normalized.get("code") or "")
    status = "success" if success else "failed"
    policy_code = ""
    blocked_by = ""
    if success:
        message = "Command completed successfully."
    elif exit_code is not None:
        message = f"Command exited with code {exit_code}."
        error = None
    elif error_code in {"timeout", "execution_error", "cwd_not_found", "cwd_not_directory"}:
        status = "error"
        message = str(error_payload.get("message") or error or "Command could not be executed.")
    elif error_code:
        status = "blocked"
        policy_code = error_code
        blocked_by = "execution_boundary"
        message = str(error_payload.get("message") or error or "Command blocked by sandbox policy.")
    else:
        message = str(error or "Command could not be executed.")
    normalized["status"] = status
    normalized["message"] = message
    if error_code:
        normalized["error_code"] = error_code
        normalized["code"] = error_code
    if policy_code:
        normalized["policy_code"] = policy_code
        normalized["blocked_by"] = blocked_by
    recoverable = bool(not success and status == "failed" and exit_code is not None)
    recovery_reason = (
        "command_execution_can_be_revised"
        if recoverable
        else ""
    )
    if recoverable:
        normalized["recoverable"] = True
        normalized["recovery_reason"] = recovery_reason

    return {
        "success": success,
        "status": status,
        "stdout": normalized["stdout"],
        "stderr": normalized["stderr"],
        "exit_code": exit_code,
        "command": command,
        "cwd": normalized["cwd"],
        "message": message,
        "error": None if status == "failed" and exit_code is not None else (message if not success else None),
        "error_code": error_code,
        "policy_code": policy_code,
        "blocked_by": blocked_by,
        "recoverable": recoverable,
        "recovery_reason": recovery_reason,
        "data": normalized,
    }


SHELL_TOOLS = {
    "sandbox_exec": sandbox_exec,
    "get_current_path": get_current_path,
    "get_sandbox_status": get_sandbox_status,
    "cleanup_sandbox": cleanup_sandbox,
}


SHELL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "sandbox_exec",
            "description": "Run a shell command on the current host computer in the project environment. When cwd is omitted the current project directory is used; relative cwd values resolve from the project root and absolute cwd values use the host path directly. Access is governed by AGENT_ACCESS_MODE, task authorization, and operating-system permissions. sandbox_dir is only for temporary files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to execute on the host computer."},
                    "cwd": {"type": "string", "description": "Optional host working directory. Relative paths resolve from the current project root. Absolute paths use the host operating-system path directly."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds, default 30."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sandbox_status",
            "description": "Show local-host execution mode, project_root, temporary sandbox_dir, timeout, and output limits without running user code.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cleanup_sandbox",
            "description": "Clear temporary files in the current workspace sandbox directory.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_path",
            "description": "Return current process cwd, project_root, temporary sandbox_dir, and local-host mode.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
