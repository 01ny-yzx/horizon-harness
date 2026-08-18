"""Safe MCP runtime dependency diagnostics and command resolution."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any


SENSITIVE_NAME_PATTERN = re.compile(r"(token|secret|password|api[_-]?key|apikey)", re.IGNORECASE)
PLACEHOLDER_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
WINDOWS_DOCKER_BIN = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"


def check_command(command: str, version_args: list[str] | None = None) -> dict[str, Any]:
    """Check whether a command is executable and optionally collect version text."""

    resolved = resolve_command(command)
    result = {
        "name": Path(command).stem if command else "",
        "command": command,
        "found": bool(resolved["command_found"]),
        "resolved_path": resolved["resolved_command"] if resolved["command_found"] else "",
        "version": "",
        "error_code": "",
        "error": "",
        "suggested_fix": "",
    }
    if not result["found"]:
        result.update({"error_code": "mcp_command_not_found", "error": f"Command not found: {command}"})
        return result
    if version_args is None:
        return result
    try:
        completed = subprocess.run(
            [str(resolved["resolved_command"]), *version_args],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics should not crash the API.
        result.update({"error_code": "mcp_dependency_check_failed", "error": _sanitize_text(str(exc))})
        return result
    output = (completed.stdout or completed.stderr or "").strip()
    result["version"] = _sanitize_text(output.splitlines()[0] if output else "")
    if completed.returncode != 0:
        result.update({"error_code": "mcp_dependency_check_failed", "error": _sanitize_text((completed.stderr or completed.stdout)[:500])})
    return result


def check_docker() -> dict[str, Any]:
    resolved = resolve_command("docker", "MCP_DOCKER_BIN", common_paths=[WINDOWS_DOCKER_BIN])
    result = _dependency_result("docker", "docker", resolved)
    result["suggested_fix"] = (
        "Install and start Docker Desktop, ensure the backend process PATH includes Docker bin, "
        "or set MCP_DOCKER_BIN=C:/Program Files/Docker/Docker/resources/bin/docker.exe."
    )
    if not result["found"]:
        result["error_code"] = "mcp_dependency_missing"
        result["error"] = "Docker command is not available to the backend process."
        return result
    _fill_version(result, [str(resolved["resolved_command"]), "--version"])
    try:
        completed = subprocess.run(
            [str(resolved["resolved_command"]), "ps"],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        result["daemon_available"] = completed.returncode == 0
        if completed.returncode != 0:
            result["error_code"] = "mcp_dependency_check_failed"
            result["error"] = _sanitize_text((completed.stderr or completed.stdout)[:500])
    except Exception as exc:  # noqa: BLE001
        result["daemon_available"] = False
        result["error_code"] = "mcp_dependency_check_failed"
        result["error"] = _sanitize_text(str(exc))
    return result


def check_node() -> dict[str, Any]:
    result = _dependency_result("node", "node", resolve_command("node", "MCP_NODE_BIN"))
    result["suggested_fix"] = "Install Node.js LTS, reopen the terminal so PATH updates, or set MCP_NODE_BIN."
    if not result["found"]:
        result["error_code"] = "mcp_dependency_missing"
        result["error"] = "node is not available to the backend process."
        return result
    _fill_version(result, [result["resolved_path"], "-v"])
    return result


def check_npm() -> dict[str, Any]:
    result = _dependency_result("npm", "npm", resolve_command("npm", "MCP_NPM_BIN"))
    result["suggested_fix"] = "Install Node.js LTS, reopen the terminal so PATH updates, or set MCP_NPM_BIN."
    if not result["found"]:
        result["error_code"] = "mcp_dependency_missing"
        result["error"] = "npm is not available to the backend process."
        return result
    _fill_version(result, [result["resolved_path"], "-v"])
    return result


def check_npx() -> dict[str, Any]:
    result = _dependency_result("npx", "npx", resolve_command("npx", "MCP_NPX_BIN"))
    result["suggested_fix"] = "Install Node.js LTS, reopen the terminal so PATH updates, or set MCP_NPX_BIN."
    if not result["found"]:
        result["error_code"] = "mcp_dependency_missing"
        result["error"] = "npx is not available to the backend process."
        return result
    _fill_version(result, [result["resolved_path"], "-v"])
    return result


def resolve_command(
    command: str,
    env_override_key: str | None = None,
    *,
    common_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve a command for execution without mutating config."""

    override = os.getenv(env_override_key or "", "").strip() if env_override_key else ""
    if override:
        return {
            "original_command": command,
            "resolved_command": override,
            "command_found": _command_exists(override),
            "command_source": "env_override",
            "env_override_key": env_override_key,
        }
    found = shutil.which(command)
    if found:
        return {
            "original_command": command,
            "resolved_command": found,
            "command_found": True,
            "command_source": "path",
            "env_override_key": env_override_key or "",
        }
    if _command_exists(command):
        return {
            "original_command": command,
            "resolved_command": command,
            "command_found": True,
            "command_source": "direct",
            "env_override_key": env_override_key or "",
        }
    for candidate in common_paths or []:
        if _command_exists(candidate):
            return {
                "original_command": command,
                "resolved_command": candidate,
                "command_found": True,
                "command_source": "common_path",
                "env_override_key": env_override_key or "",
            }
    return {
        "original_command": command,
        "resolved_command": command,
        "command_found": False,
        "command_source": "not_found",
        "env_override_key": env_override_key or "",
    }


def build_dependency_report() -> dict[str, Any]:
    return {
        "docker": check_docker(),
        "node": check_node(),
        "npm": check_npm(),
        "npx": check_npx(),
        "env": {
            key: _env_status(key)
            for key in ("MCP_DOCKER_BIN", "MCP_NPX_BIN", "MCP_NODE_BIN", "MCP_NPM_BIN", "MCP_FILESYSTEM_ROOT")
        },
    }


def expand_args_placeholders(args: list[str], env: Mapping[str, str] | None = None) -> tuple[list[str], dict[str, Any]]:
    """Expand full-string ${ENV_NAME} args and return safe diagnostics."""

    env_map = env or os.environ
    expanded: list[str] = []
    placeholders: list[dict[str, Any]] = []
    unresolved: list[str] = []
    sanitized: list[str] = []
    expanded_count = 0
    for arg in args:
        text = str(arg)
        match = PLACEHOLDER_PATTERN.fullmatch(text)
        if not match:
            expanded.append(text)
            sanitized.append(text)
            continue
        name = match.group(1)
        sensitive = _is_sensitive(name)
        configured = name in env_map and str(env_map.get(name, "")) != ""
        value = str(env_map.get(name, ""))
        if configured:
            expanded.append(value)
            expanded_count += 1
        else:
            expanded.append(text)
            unresolved.append(name)
        placeholders.append(
            {
                "name": name,
                "configured": configured,
                "sensitive": sensitive,
                "exists": Path(value).exists() if configured and not sensitive else None,
                "value_preview": "" if sensitive or not configured else _sanitize_path(value),
            }
        )
        sanitized.append("[configured]" if configured and sensitive else (_sanitize_path(value) if configured else text))
    return expanded, {
        "args_expanded_count": expanded_count,
        "args_unresolved_placeholders": unresolved,
        "args_placeholders": placeholders,
        "args_sanitized": sanitized,
    }


def command_override_key(command: str) -> str:
    normalized = Path(str(command)).name.lower()
    if normalized in {"docker", "docker.exe"}:
        return "MCP_DOCKER_BIN"
    if normalized in {"npx", "npx.cmd", "npx.exe"}:
        return "MCP_NPX_BIN"
    if normalized in {"node", "node.exe"}:
        return "MCP_NODE_BIN"
    if normalized in {"npm", "npm.cmd", "npm.exe"}:
        return "MCP_NPM_BIN"
    return ""


def suggested_fix_for_command(command: str) -> str:
    normalized = Path(str(command)).name.lower()
    if normalized in {"docker", "docker.exe"}:
        return (
            "Install and start Docker Desktop, ensure the backend process PATH includes Docker bin, "
            "or set MCP_DOCKER_BIN=C:/Program Files/Docker/Docker/resources/bin/docker.exe."
        )
    if normalized in {"npx", "npx.cmd", "npx.exe"}:
        return "Install Node.js LTS, reopen the terminal so PATH updates, or set MCP_NPX_BIN."
    return "Ensure the command is installed and visible in the backend process PATH."


def _dependency_result(name: str, command: str, resolved: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "command": command,
        "found": bool(resolved["command_found"]),
        "resolved_path": resolved["resolved_command"] if resolved["command_found"] else "",
        "command_source": resolved["command_source"],
        "version": "",
        "error_code": "",
        "error": "",
        "suggested_fix": "",
    }


def _fill_version(result: dict[str, Any], command: list[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        result["error_code"] = "mcp_dependency_check_failed"
        result["error"] = _sanitize_text(str(exc))
        return
    output = (completed.stdout or completed.stderr or "").strip()
    result["version"] = _sanitize_text(output.splitlines()[0] if output else "")
    if completed.returncode != 0:
        result["error_code"] = "mcp_dependency_check_failed"
        result["error"] = _sanitize_text((completed.stderr or completed.stdout)[:500])


def _env_status(key: str) -> dict[str, Any]:
    value = os.getenv(key, "")
    status: dict[str, Any] = {"configured": bool(value)}
    if value and not _is_sensitive(key):
        status["exists"] = Path(value).exists()
        status["value_preview"] = _sanitize_path(value)
    return status


def _command_exists(command: str) -> bool:
    return bool(command) and Path(command).exists()


def _is_sensitive(name: str) -> bool:
    return bool(SENSITIVE_NAME_PATTERN.search(name))


def _sanitize_path(value: str) -> str:
    path = Path(value)
    return str(path.name or value) if path.name else value[:80]


def _sanitize_text(text: str) -> str:
    for marker in ("token", "api_key", "apikey", "secret", "password"):
        text = text.replace(marker, "[redacted]")
        text = text.replace(marker.upper(), "[REDACTED]")
    return text[:1000]
