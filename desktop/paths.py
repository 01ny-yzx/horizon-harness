"""Desktop client local path planning."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DesktopPaths:
    app_name: str
    base_dir: Path
    config_dir: Path
    data_dir: Path
    logs_dir: Path
    workspace_dir: Path
    cache_dir: Path
    plugins_dir: Path
    runtime_dir: Path


def default_desktop_base_dir(app_name: str = "agent") -> Path:
    safe_name = app_name.strip() or "agent"
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / safe_name
        return Path.home() / "AppData" / "Local" / safe_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / safe_name
    return Path.home() / ".local" / "share" / safe_name


def build_desktop_paths(base_dir: str | Path | None = None, app_name: str = "agent") -> DesktopPaths:
    base = Path(base_dir).expanduser() if base_dir is not None else default_desktop_base_dir(app_name)
    return DesktopPaths(
        app_name=app_name.strip() or "agent",
        base_dir=base,
        config_dir=base / "config",
        data_dir=base / "data",
        logs_dir=base / "logs",
        workspace_dir=base / "workspace",
        cache_dir=base / "cache",
        plugins_dir=base / "plugins",
        runtime_dir=base / "runtime",
    )


def ensure_desktop_dirs(paths: DesktopPaths) -> DesktopPaths:
    for path in iter_desktop_dirs(paths):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def iter_desktop_dirs(paths: DesktopPaths) -> tuple[Path, ...]:
    return (
        paths.base_dir,
        paths.config_dir,
        paths.data_dir,
        paths.logs_dir,
        paths.workspace_dir,
        paths.cache_dir,
        paths.plugins_dir,
        paths.runtime_dir,
    )


def desktop_paths_to_dict(paths: DesktopPaths) -> dict[str, str]:
    return {
        "app_name": paths.app_name,
        "base_dir": str(paths.base_dir),
        "config_dir": str(paths.config_dir),
        "data_dir": str(paths.data_dir),
        "logs_dir": str(paths.logs_dir),
        "workspace_dir": str(paths.workspace_dir),
        "cache_dir": str(paths.cache_dir),
        "plugins_dir": str(paths.plugins_dir),
        "runtime_dir": str(paths.runtime_dir),
    }
