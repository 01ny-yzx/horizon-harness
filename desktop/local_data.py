"""Local desktop data directory management."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from desktop.config import DesktopRuntimeConfig
from desktop.paths import DesktopPaths, build_desktop_paths


DESKTOP_DIRECTORY_KEYS = (
    "base",
    "config",
    "data",
    "logs",
    "workspace",
    "cache",
    "plugins",
    "runtime",
)


@dataclass(frozen=True)
class LocalDirectoryStatus:
    key: str
    path: str
    exists: bool
    is_dir: bool
    writable: bool
    created: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LocalDataDirectoryStatus:
    ok: bool
    base_dir: str
    directories: dict[str, LocalDirectoryStatus]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def desktop_paths_to_directory_map(paths: DesktopPaths) -> dict[str, Path]:
    return {
        "base": paths.base_dir,
        "config": paths.config_dir,
        "data": paths.data_dir,
        "logs": paths.logs_dir,
        "workspace": paths.workspace_dir,
        "cache": paths.cache_dir,
        "plugins": paths.plugins_dir,
        "runtime": paths.runtime_dir,
    }


def ensure_local_data_dirs(paths: DesktopPaths) -> LocalDataDirectoryStatus:
    statuses: dict[str, LocalDirectoryStatus] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for key, path in desktop_paths_to_directory_map(paths).items():
        existed_before = path.exists()
        error: str | None = None
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            error = _safe_error(exc)
        status = _directory_status(key, path, created=not existed_before and path.exists(), error=error)
        statuses[key] = status
        if status.error:
            errors.append(f"{key}:{status.error}")
        errors.extend(f"{key}:{warning}" for warning in status.warnings)
    return LocalDataDirectoryStatus(
        ok=not errors and all(_status_ok(status) for status in statuses.values()),
        base_dir=str(paths.base_dir),
        directories=statuses,
        errors=errors,
        warnings=warnings,
    )


def validate_local_data_dirs(paths: DesktopPaths, *, check_writable: bool = True) -> LocalDataDirectoryStatus:
    statuses: dict[str, LocalDirectoryStatus] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for key, path in desktop_paths_to_directory_map(paths).items():
        status = _directory_status(key, path, created=False, check_writable=check_writable)
        statuses[key] = status
        if status.error:
            errors.append(f"{key}:{status.error}")
        errors.extend(f"{key}:{warning}" for warning in status.warnings)
    return LocalDataDirectoryStatus(
        ok=not errors and all(_status_ok(status, require_writable=check_writable) for status in statuses.values()),
        base_dir=str(paths.base_dir),
        directories=statuses,
        errors=errors,
        warnings=warnings,
    )


def is_directory_writable(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    probe = path / ".desktop_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        return True
    except OSError:
        return False
    finally:
        try:
            if probe.exists():
                probe.unlink()
        except OSError:
            pass


def local_directory_status_to_dict(status: LocalDirectoryStatus) -> dict[str, object]:
    return asdict(status)


def local_data_directory_status_to_dict(status: LocalDataDirectoryStatus) -> dict[str, object]:
    return {
        "ok": status.ok,
        "base_dir": status.base_dir,
        "directories": {key: local_directory_status_to_dict(value) for key, value in status.directories.items()},
        "errors": list(status.errors),
        "warnings": list(status.warnings),
    }


def build_desktop_paths_from_config(
    config: DesktopRuntimeConfig,
    *,
    base_dir: str | Path | None = None,
    app_name: str = "agent",
) -> DesktopPaths:
    selected_base = base_dir if base_dir is not None else config.data_dir
    paths = build_desktop_paths(selected_base, app_name=app_name)
    if config.workspace_dir:
        return DesktopPaths(
            app_name=paths.app_name,
            base_dir=paths.base_dir,
            config_dir=paths.config_dir,
            data_dir=paths.data_dir,
            logs_dir=paths.logs_dir,
            workspace_dir=Path(config.workspace_dir).expanduser(),
            cache_dir=paths.cache_dir,
            plugins_dir=paths.plugins_dir,
            runtime_dir=paths.runtime_dir,
        )
    return paths


def _directory_status(
    key: str,
    path: Path,
    *,
    created: bool = False,
    error: str | None = None,
    check_writable: bool = True,
) -> LocalDirectoryStatus:
    exists = path.exists()
    is_dir = path.is_dir() if exists else False
    writable = is_directory_writable(path) if check_writable else False
    warnings: list[str] = []
    if error is None:
        if not exists:
            error = "missing"
        elif not is_dir:
            error = "not_directory"
        elif check_writable and not writable:
            error = "not_writable"
    if exists and not is_dir:
        warnings.append("path_is_file")
    return LocalDirectoryStatus(
        key=key,
        path=str(path),
        exists=exists,
        is_dir=is_dir,
        writable=writable,
        created=created,
        error=error,
        warnings=warnings,
    )


def _status_ok(status: LocalDirectoryStatus, *, require_writable: bool = True) -> bool:
    return status.error is None and status.exists and status.is_dir and (status.writable if require_writable else True)


def _safe_error(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:200] or exc.__class__.__name__
