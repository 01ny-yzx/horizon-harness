"""Legacy/internal Python execution helpers.

These wrappers are not Agent-visible; command execution is exposed through
sandbox_exec.
"""

from __future__ import annotations

from typing import Any

from core.sandbox import current_sandbox_manager


def run_python_in_sandbox(code: str, timeout: int = 30) -> dict[str, Any]:
    """Run Python code in the current workspace sandbox."""

    return current_sandbox_manager().run_python_code(code, timeout=timeout)


def run_python_file_in_sandbox(path: str, timeout: int = 30) -> dict[str, Any]:
    """Copy a Python file into the sandbox and run it there."""

    return current_sandbox_manager().run_python_file(path, timeout=timeout)


def run_python_file(path: str) -> dict[str, Any]:
    """Legacy Python file tool; internally runs in the sandbox."""

    return run_python_file_in_sandbox(path, timeout=30)


PYTHON_TOOLS: dict[str, Any] = {}


PYTHON_TOOL_SCHEMAS: list[dict[str, Any]] = []
