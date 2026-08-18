"""Desktop runtime state types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from desktop.paths import DesktopPaths


@dataclass(frozen=True)
class DesktopRuntimeState:
    backend_running: bool = False
    backend_pid: int | None = None
    backend_url: str | None = None
    selected_port: int | None = None
    data_dir: str | None = None
    logs_dir: str | None = None
    workspace_dir: str | None = None
    status: str = "stopped"
    errors: list[str] = field(default_factory=list)


def desktop_runtime_state_to_dict(state: DesktopRuntimeState) -> dict[str, object]:
    return asdict(state)


def build_runtime_state_from_desktop_paths(
    paths: DesktopPaths,
    *,
    backend_running: bool = False,
    backend_pid: int | None = None,
    backend_url: str | None = None,
    selected_port: int | None = None,
    status: str = "stopped",
    errors: list[str] | None = None,
) -> DesktopRuntimeState:
    return DesktopRuntimeState(
        backend_running=backend_running,
        backend_pid=backend_pid,
        backend_url=backend_url,
        selected_port=selected_port,
        data_dir=str(paths.data_dir),
        logs_dir=str(paths.logs_dir),
        workspace_dir=str(paths.workspace_dir),
        status=status,
        errors=list(errors or []),
    )
