"""Bounded command execution on the current host operating system."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import settings
from core.command_execution_context import get_host_command_context
from core.path_grounding import (
    PathContext,
    build_path_context,
    ground_exec_cwd,
    path_resolution_to_dict,
)
from core.workspace import WorkspaceManager
from core.workspace_runtime import get_current_workspace


def _decode_text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


@dataclass(frozen=True)
class SandboxContext:
    sandbox_id: str
    user_id: str
    project_id: str
    sandbox_dir: str
    created_at: str
    expires_at: str
    mode: str
    timeout_seconds: int
    max_output_chars: int


class SandboxManager:
    """Manage temporary files and run bounded host subprocesses."""

    def __init__(self) -> None:
        self.max_output_chars = max(1000, int(settings.sandbox_max_output_chars or 12000))

    def create_sandbox(self, user_id: str | None = None, project_id: str | None = None) -> SandboxContext:
        workspace = (
            get_current_workspace()
            if user_id is None and project_id is None
            else WorkspaceManager().get_context(user_id, project_id)
        )
        sandbox_dir = workspace.workspace_dir / "sandbox"
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        return SandboxContext(
            sandbox_id=f"sandbox-{uuid.uuid4().hex[:12]}",
            user_id=workspace.user_id,
            project_id=workspace.project_id,
            sandbox_dir=str(sandbox_dir.resolve(strict=False)),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=24)).isoformat(),
            mode="local_host",
            timeout_seconds=max(1, int(settings.sandbox_timeout_seconds or 30)),
            max_output_chars=self.max_output_chars,
        )

    def get_sandbox_dir(self, user_id: str | None = None, project_id: str | None = None) -> Path:
        return Path(self.create_sandbox(user_id, project_id).sandbox_dir)

    def cleanup_sandbox(self, user_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        sandbox_dir = self.get_sandbox_dir(user_id, project_id)
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        return self._ok({"deleted": True, "sandbox_dir": str(sandbox_dir), "mode": "local_host"})

    def status(self, user_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        context = self.create_sandbox(user_id, project_id)
        host = get_host_command_context()
        return self._ok(
            {
                **asdict(context),
                "enabled": settings.sandbox_enabled,
                "project_root": str(host.project_root),
                "session_directory": str(host.session_directory),
                "temporary_directory": context.sandbox_dir,
                "mode": "local_host",
            }
        )

    def run_local_command(self, command: str, cwd: str | Path | None = None, timeout: int = 30) -> dict[str, Any]:
        if not settings.sandbox_enabled:
            return self._err("Sandbox execution is disabled.", "sandbox_disabled")
        if not isinstance(command, str) or not command.strip():
            return self._err("Command is empty.", "empty_command")

        host = get_host_command_context()
        sandbox_dir = host.sandbox_dir
        grounding = ground_exec_cwd(
            str(cwd) if cwd not in (None, "") else None,
            context=self._path_context(host.project_root, sandbox_dir, host.runtime_lane),
        )
        run_cwd = Path(grounding.resolved_path)
        base_data = {
            "command": command,
            "cwd": str(run_cwd),
            "host_resolved_cwd": str(run_cwd),
            "project_root": str(host.project_root),
            "sandbox_dir": str(sandbox_dir),
            "logical_root": grounding.logical_root,
            "path_kind": grounding.path_kind,
            "path_grounding": path_resolution_to_dict(grounding),
            "mode": "local_host",
        }
        access_error = self._execution_access_guard(operation="host_command", base_data=base_data)
        if access_error is not None:
            return access_error
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        if not run_cwd.exists():
            return self._err("Working directory does not exist.", "cwd_not_found", base_data)
        if not run_cwd.is_dir():
            return self._err("Working directory is not a directory.", "cwd_not_directory", base_data)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_cwd),
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._timeout(timeout),
                encoding="utf-8",
                errors="replace",
                env=self._host_env(sandbox_dir, host.project_root),
            )
            return self._ok(
                {
                    **base_data,
                    **self._stdio_payload(completed.stdout, completed.stderr),
                    "returncode": completed.returncode,
                },
                success=completed.returncode == 0,
            )
        except subprocess.TimeoutExpired as exc:
            return self._err(
                f"Command timed out after {self._timeout(timeout)} seconds.",
                "timeout",
                {
                    **base_data,
                    **self._stdio_payload(exc.stdout or "", exc.stderr or ""),
                    "returncode": None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), "execution_error", base_data)

    def run_python_code(self, code: str, timeout: int = 30) -> dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            return self._err("Python code is empty.", "empty_python")
        host = get_host_command_context()
        access_error = self._execution_access_guard(
            operation="python_code",
            base_data={"project_root": str(host.project_root), "sandbox_dir": str(host.sandbox_dir)},
        )
        if access_error is not None:
            return access_error
        host.sandbox_dir.mkdir(parents=True, exist_ok=True)
        script = host.sandbox_dir / f"snippet_{uuid.uuid4().hex}.py"
        script.write_text(code, encoding="utf-8")
        try:
            return self._run_argv([sys.executable, str(script)], timeout=timeout)
        finally:
            script.unlink(missing_ok=True)

    def run_python_file(self, path: str, timeout: int = 30) -> dict[str, Any]:
        host = get_host_command_context()
        access_error = self._execution_access_guard(
            operation="python_file",
            base_data={"path": str(path or ""), "project_root": str(host.project_root), "sandbox_dir": str(host.sandbox_dir)},
        )
        if access_error is not None:
            return access_error
        source = Path(os.path.expandvars(path)).expanduser()
        if not source.is_absolute():
            source = host.project_root / source
        source = source.resolve(strict=False)
        if not source.exists() or not source.is_file():
            return self._err(f"Python file does not exist: {path}", "file_not_found")
        if source.suffix.lower() != ".py":
            return self._err("Only .py files can be run.", "unsupported_file")
        return self._run_argv([sys.executable, str(source)], timeout=timeout)

    def _run_argv(self, argv: list[str], *, timeout: int) -> dict[str, Any]:
        host = get_host_command_context()
        access_error = self._execution_access_guard(
            operation="host_argv",
            base_data={"project_root": str(host.project_root), "sandbox_dir": str(host.sandbox_dir)},
        )
        if access_error is not None:
            return access_error
        sandbox_dir = host.sandbox_dir
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                argv,
                cwd=str(host.project_root),
                capture_output=True,
                text=True,
                timeout=self._timeout(timeout),
                encoding="utf-8",
                errors="replace",
                env=self._host_env(sandbox_dir, host.project_root),
            )
            return self._ok(
                {
                    **self._stdio_payload(completed.stdout, completed.stderr),
                    "returncode": completed.returncode,
                    "cwd": str(host.project_root),
                    "project_root": str(host.project_root),
                    "sandbox_dir": str(sandbox_dir),
                    "mode": "local_host",
                },
                success=completed.returncode == 0,
            )
        except subprocess.TimeoutExpired as exc:
            return self._err(
                f"Python execution timed out after {self._timeout(timeout)} seconds.",
                "timeout",
                {
                    **self._stdio_payload(exc.stdout or "", exc.stderr or ""),
                    "returncode": None,
                    "cwd": str(host.project_root),
                    "project_root": str(host.project_root),
                    "sandbox_dir": str(sandbox_dir),
                    "mode": "local_host",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), "execution_error")

    @staticmethod
    def _path_context(project_root: Path, sandbox_dir: Path, runtime_lane: str) -> PathContext:
        base = build_path_context(runtime_lane=runtime_lane, project_root=project_root)
        return PathContext(
            **{
                **asdict(base),
                "project_root": project_root,
                "sandbox_dir": sandbox_dir,
            }
        )

    def _execution_access_guard(
        self,
        *,
        operation: str,
        base_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        context = get_host_command_context()
        if context.access_mode != "read_only":
            return None
        data = dict(base_data or {})
        data.update(
            {
                "access_mode": "read_only",
                "mode": "local_host",
                "operation": str(operation or "host_execution"),
            }
        )
        return self._err(
            "当前 Agent 权限模式为 read_only，禁止宿主命令和代码执行。",
            "agent_access_mode_read_only",
            data,
        )

    def _timeout(self, timeout: int | None) -> int:
        return max(1, min(int(timeout or settings.sandbox_timeout_seconds or 30), 300))

    def _limit(self, value: str | bytes | None) -> str:
        text = self._redact(_decode_text(value))
        if len(text) <= self.max_output_chars:
            return text
        return text[: self.max_output_chars] + "\n[output truncated by sandbox]"

    def _stdio_payload(self, stdout: str | bytes | None, stderr: str | bytes | None) -> dict[str, str]:
        full_stdout = self._redact(_decode_text(stdout))
        full_stderr = self._redact(_decode_text(stderr))
        return {
            "stdout": self._limit(full_stdout),
            "stderr": self._limit(full_stderr),
            "_full_stdout": full_stdout,
            "_full_stderr": full_stderr,
        }

    @staticmethod
    def _redact(text: str) -> str:
        result = text
        for key, value in os.environ.items():
            if ("KEY" in key or "TOKEN" in key or "SECRET" in key) and value and len(value) >= 8:
                result = result.replace(value, "[REDACTED]")
        return result

    @staticmethod
    def _host_env(sandbox_dir: Path, project_root: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "TEMP": str(sandbox_dir),
                "TMP": str(sandbox_dir),
                "TMPDIR": str(sandbox_dir),
                "SANDBOX_ROOT": str(sandbox_dir),
                "HORIZON_PROJECT_ROOT": str(project_root),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
        )
        return env

    @staticmethod
    def _ok(data: dict[str, Any], success: bool = True) -> dict[str, Any]:
        return {
            "success": success,
            "data": data,
            "error": None if success else data.get("stderr") or "Execution failed.",
        }

    @staticmethod
    def _err(message: str, code: str = "sandbox_error", data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(data or {})
        payload.setdefault("error_code", code)
        payload.setdefault("code", code)
        return {
            "success": False,
            "data": payload,
            "error": {"code": code, "message": message},
            "error_code": code,
        }


def current_sandbox_manager() -> SandboxManager:
    get_current_workspace()
    return SandboxManager()
