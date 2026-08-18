"""Backend process manager foundation for the desktop client."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop.ports import (
    DEFAULT_PREFERRED_PORT,
    DEFAULT_PORT_RANGE_END,
    DEFAULT_PORT_RANGE_START,
    PortAllocationRequest,
    allocate_port,
    port_allocation_to_dict,
)
from desktop.runtime import DesktopRuntimeState


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|password|secret|credential)\b\s*([:=])\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(authorization)\b\s*:\s*bearer\s+([^\s,;]+)"),
]


@dataclass(frozen=True)
class BackendProcessSpec:
    host: str
    port: int | None
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    startup_timeout_seconds: float = 10.0
    shutdown_timeout_seconds: float = 5.0
    port_allocation: dict[str, object] | None = None


@dataclass(frozen=True)
class BackendProcessStatus:
    running: bool
    pid: int | None = None
    returncode: int | None = None
    status: str = "stopped"
    error: str | None = None


def build_backend_command(
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    python_executable: str | None = None,
    module: str = "scripts.run_api",
) -> list[str]:
    del host, port
    return [python_executable or sys.executable, "-m", module]


def build_backend_env(
    *,
    host: str,
    port: int | None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env["API_HOST"] = host
    if port is not None:
        env["API_PORT"] = str(port)
    return env


def build_backend_process_spec(
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    preferred_port: int = DEFAULT_PREFERRED_PORT,
    port_range: tuple[int, int] = (DEFAULT_PORT_RANGE_START, DEFAULT_PORT_RANGE_END),
    allocate_dynamic_port: bool = False,
    port_checker: Any | None = None,
    cwd: str | None = None,
    python_executable: str | None = None,
    base_env: dict[str, str] | None = None,
) -> BackendProcessSpec:
    selected_port = port
    allocation_data: dict[str, object] | None = None
    if allocate_dynamic_port:
        allocation = allocate_port(
            PortAllocationRequest(
                host=host,
                requested_port=port,
                preferred_port=preferred_port,
                range_start=port_range[0],
                range_end=port_range[1],
            ),
            port_checker=port_checker,
        )
        allocation_data = port_allocation_to_dict(allocation)
        selected_port = allocation.selected_port if allocation.ok else None
    return BackendProcessSpec(
        host=host,
        port=selected_port,
        command=build_backend_command(host=host, port=selected_port, python_executable=python_executable),
        cwd=cwd or str(PROJECT_ROOT),
        env=build_backend_env(host=host, port=selected_port, base_env=base_env),
        port_allocation=allocation_data,
    )


class BackendProcessManager:
    def __init__(
        self,
        spec: BackendProcessSpec,
        *,
        popen_factory: Any | None = None,
    ) -> None:
        self.spec = spec
        self._popen_factory = popen_factory or subprocess.Popen
        self._process: Any | None = None
        self._last_error: str | None = None

    def is_running(self) -> bool:
        return self._process is not None and self._poll() is None

    def status(self) -> BackendProcessStatus:
        if self._last_error:
            return BackendProcessStatus(
                running=False,
                pid=self._pid(),
                returncode=self._poll(),
                status="error",
                error=self._last_error,
            )
        if self.is_running():
            return BackendProcessStatus(running=True, pid=self._pid(), returncode=None, status="running")
        returncode = self._poll()
        if self._process is not None and returncode is not None:
            return BackendProcessStatus(running=False, pid=self._pid(), returncode=returncode, status="exited")
        return BackendProcessStatus(running=False, pid=None, returncode=None, status="stopped")

    def start(self) -> BackendProcessStatus:
        if self.is_running():
            return self.status()
        if self.spec.port is None and self.spec.port_allocation and self.spec.port_allocation.get("ok") is False:
            reason = self.spec.port_allocation.get("reason")
            self._last_error = f"port allocation failed:{reason}" if reason else "port allocation failed"
            return self.status()
        if not self.spec.command:
            self._last_error = "empty backend command"
            return self.status()
        try:
            self._process = self._popen_factory(
                list(self.spec.command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.spec.cwd,
                env=self.spec.env,
                shell=False,
            )
            self._last_error = None
            return self.status()
        except Exception as exc:
            self._process = None
            self._last_error = sanitize_process_error(exc)
            return self.status()

    def stop(self) -> BackendProcessStatus:
        if self._process is None:
            self._last_error = None
            return BackendProcessStatus(running=False, status="stopped")
        if self._poll() is not None:
            pid = self._pid()
            returncode = self._poll()
            self._process = None
            self._last_error = None
            return BackendProcessStatus(running=False, pid=pid, returncode=returncode, status="stopped")
        try:
            process = self._process
            pid = self._pid()
            self._process.terminate()
            try:
                self._process.wait(timeout=self.spec.shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                self._process.kill()
                try:
                    self._process.wait(timeout=self.spec.shutdown_timeout_seconds)
                except subprocess.TimeoutExpired:
                    pass
            returncode = process.poll() if process is not None else None
            self._process = None
            self._last_error = None
            return BackendProcessStatus(running=False, pid=pid, returncode=returncode, status="stopped")
        except Exception as exc:
            self._last_error = sanitize_process_error(exc)
            return self.status()

    def to_runtime_state(self) -> DesktopRuntimeState:
        status = self.status()
        return DesktopRuntimeState(
            backend_running=status.running,
            backend_pid=status.pid,
            backend_url=f"http://{self.spec.host}:{self.spec.port}" if self.spec.port is not None else None,
            selected_port=self.spec.port,
            status=status.status,
            errors=[status.error] if status.error else [],
        )

    def _poll(self) -> int | None:
        if self._process is None:
            return None
        try:
            return self._process.poll()
        except Exception:
            return None

    def _pid(self) -> int | None:
        if self._process is None:
            return None
        pid = getattr(self._process, "pid", None)
        return pid if isinstance(pid, int) else None


def sanitize_process_error(value: object) -> str:
    text = " ".join(str(value).split())
    for pattern in SECRET_PATTERNS[:2]:
        text = pattern.sub("[REDACTED_KEY]", text)
    text = SECRET_PATTERNS[2].sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = SECRET_PATTERNS[3].sub(lambda match: f"{match.group(1)}: Bearer [REDACTED]", text)
    return text[:300]
