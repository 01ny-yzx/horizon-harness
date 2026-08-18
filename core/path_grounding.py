"""Path grounding runtime for project, output, and sandbox paths."""

from __future__ import annotations

import os
import platform
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from config.settings import PROJECT_ROOT, settings


DESKTOP_ALIASES = {"desktop", "桌面"}
DOCUMENT_ALIASES = {"documents", "document", "文档"}
DOWNLOAD_ALIASES = {"downloads", "download", "下载"}
PROJECT_INTERNAL_ROOTS = {
    "api",
    "core",
    "config",
    "frontend",
    "mcp_servers",
    "prompts",
    "providers",
    "scripts",
    "tests",
    "tools",
    "workflows",
    "workspace_store",
}
PROJECT_INTERNAL_FILES = {"README.md", "main.py", "pyproject.toml", "requirements.txt"}


@dataclass(frozen=True)
class PathContext:
    project_root: Path
    app_root: Path
    user_data_root: Path
    workspace_root: Path
    workspace_dir: Path
    sandbox_dir: Path
    default_output_dir: Path
    process_cwd: Path
    user_home: Path
    access_mode: str
    runtime_lane: str
    user_id: str
    project_id: str
    platform_name: str
    source: str


@dataclass(frozen=True)
class PathResolution:
    operation: str
    raw_path: str
    normalized_path: str
    resolved_path: str
    logical_root: str
    path_kind: str
    cwd_used: str
    default_output_dir: str
    project_root: str
    app_root: str
    user_data_root: str
    workspace_root: str
    sandbox_dir: str
    is_user_explicit: bool
    used_default_output_dir: bool
    allowed: bool
    code: str
    reason: str


def build_path_context(
    runtime_lane: str = "",
    user_id: str = "",
    project_id: str = "",
    project_root: Path | None = None,
    app_root: Path | None = None,
    user_data_root: Path | None = None,
) -> PathContext:
    user = _safe_id(user_id or settings.default_user_id)
    project = _safe_id(project_id or settings.default_project_id)
    app = (app_root or PROJECT_ROOT).expanduser().resolve()
    opened_project = (project_root or PROJECT_ROOT).expanduser().resolve()
    data_root = (user_data_root or _default_user_data_root()).expanduser().resolve()
    workspace_root = (data_root / "workspace_store").resolve()
    workspace_dir = (workspace_root / user / project).resolve()
    sandbox_dir = (workspace_dir / "sandbox").resolve()
    default_output_dir = _default_output_dir(data_root)
    return PathContext(
        project_root=opened_project,
        app_root=app,
        user_data_root=data_root,
        workspace_root=workspace_root,
        workspace_dir=workspace_dir,
        sandbox_dir=sandbox_dir,
        default_output_dir=default_output_dir,
        process_cwd=Path.cwd().resolve(),
        user_home=Path.home().resolve(),
        access_mode=str(os.getenv("AGENT_ACCESS_MODE", settings.agent_access_mode) or ""),
        runtime_lane=str(runtime_lane or ""),
        user_id=user,
        project_id=project,
        platform_name=platform.system().lower() or os.name,
        source="path_grounding",
    )


def ground_read_path(path: str, context: PathContext | None = None) -> PathResolution:
    ctx = context or build_path_context()
    raw = _clean(path)
    normalized = _normalize(raw)
    resolved, logical_root, path_kind = _resolve_project_read(normalized, ctx)
    return _resolution(
        ctx,
        operation="read",
        raw_path=raw,
        normalized_path=normalized,
        resolved_path=resolved,
        logical_root=logical_root,
        path_kind=path_kind,
        cwd_used=ctx.project_root,
        is_user_explicit=bool(raw),
        used_default_output_dir=False,
        allowed=bool(raw),
        code="path_empty" if not raw else "ok",
        reason="Read path grounded.",
    )


def ground_write_path(
    path: str,
    context: PathContext | None = None,
    filename: str | None = None,
    default_bare_filename_to_output_dir: bool = True,
    allow_agent_internal: bool = False,
) -> PathResolution:
    del allow_agent_internal
    ctx = context or build_path_context()
    raw = _clean(path)
    normalized = _normalize(raw or filename or "")
    resolved, logical_root, path_kind, used_default = _resolve_write(normalized, ctx, filename, default_bare_filename_to_output_dir)
    return _resolution(
        ctx,
        operation="write",
        raw_path=raw,
        normalized_path=normalized,
        resolved_path=resolved,
        logical_root=logical_root,
        path_kind=path_kind,
        cwd_used=ctx.default_output_dir if used_default else ctx.project_root,
        is_user_explicit=bool(raw),
        used_default_output_dir=used_default,
        allowed=bool(normalized),
        code="path_empty" if not normalized else "ok",
        reason="Write path grounded.",
    )


def ground_exec_cwd(cwd: str | None, context: PathContext | None = None) -> PathResolution:
    ctx = context or build_path_context(runtime_lane="command_exec")
    raw = _clean(cwd or "")
    normalized = _normalize_host_value(raw)
    if not normalized:
        resolved: str | Path = ctx.project_root
        logical_root = "project_root"
        path_kind = "project_default_cwd"
    elif is_host_absolute_path(normalized):
        resolved = resolve_host_path(normalized, project_root=ctx.project_root)
        logical_root = _logical_root(resolved, ctx)
        path_kind = "host_absolute_cwd"
    else:
        resolved = (ctx.project_root / normalized).resolve(strict=False)
        logical_root = _logical_root(resolved, ctx)
        path_kind = "project_relative_cwd"
    return _resolution(
        ctx,
        operation="exec_cwd",
        raw_path=raw,
        normalized_path=normalized,
        resolved_path=resolved,
        logical_root=logical_root,
        path_kind=path_kind,
        cwd_used=resolved,
        is_user_explicit=bool(raw),
        used_default_output_dir=False,
        allowed=True,
        code="ok",
        reason="Host cwd grounded.",
    )


def path_resolution_to_dict(resolution: PathResolution) -> dict[str, Any]:
    return {key: str(value) if isinstance(value, Path) else value for key, value in asdict(resolution).items()}


def compact_path_grounding(value: Any) -> dict[str, Any]:
    data = path_resolution_to_dict(value) if isinstance(value, PathResolution) else dict(value or {}) if isinstance(value, dict) else {}
    keys = (
        "operation",
        "raw_path",
        "normalized_path",
        "resolved_path",
        "logical_root",
        "path_kind",
        "cwd_used",
        "project_root",
        "allowed",
        "code",
        "reason",
        "sandbox_dir",
        "default_output_dir",
    )
    return {key: data.get(key) for key in keys if data.get(key) not in (None, "")}


def _resolve_project_read(path: str, ctx: PathContext) -> tuple[str | Path, str, str]:
    special = _known_user_folder_path(path, ctx)
    if special is not None:
        return special, "user_home", "known_user_folder"
    if is_host_absolute_path(path):
        return resolve_host_path(path, project_root=ctx.project_root), "external_path", "host_absolute"
    if path.lower().replace("\\", "/").startswith("exports/"):
        return (ctx.project_root / path).resolve(), "project_root", "project_output_explicit"
    return (ctx.project_root / path).resolve(), "project_root", "project_relative"


def _resolve_write(path: str, ctx: PathContext, filename: str | None, default_to_output: bool) -> tuple[str | Path, str, str, bool]:
    value = path or filename or "output.txt"
    if _looks_like_directory(value):
        value = f"{value.rstrip('/').rstrip(chr(92))}/{filename or 'output.txt'}"
    special = _known_user_folder_path(value, ctx)
    if special is not None:
        return special, "user_home", "known_user_folder", False
    if is_host_absolute_path(value):
        return resolve_host_path(value, project_root=ctx.project_root), "external_path", "host_absolute", False
    normalized = value.replace("\\", "/")
    if normalized.lower().startswith("exports/"):
        return (ctx.project_root / normalized).resolve(), "project_root", "project_output_explicit", False
    first = normalized.split("/", 1)[0]
    if first in PROJECT_INTERNAL_ROOTS or first in PROJECT_INTERNAL_FILES:
        return (ctx.project_root / normalized).resolve(), "project_root", "agent_self_candidate", False
    if default_to_output:
        return (ctx.default_output_dir / normalized).resolve(), "default_output_dir", "default_output", True
    return (ctx.project_root / normalized).resolve(), "project_root", "project_relative", False


def _resolution(
    ctx: PathContext,
    *,
    operation: str,
    raw_path: str,
    normalized_path: str,
    resolved_path: str | Path,
    logical_root: str,
    path_kind: str,
    cwd_used: str | Path,
    is_user_explicit: bool,
    used_default_output_dir: bool,
    allowed: bool,
    code: str,
    reason: str,
) -> PathResolution:
    return PathResolution(
        operation=operation,
        raw_path=raw_path,
        normalized_path=normalized_path,
        resolved_path=str(resolved_path),
        logical_root=logical_root,
        path_kind=path_kind,
        cwd_used=str(cwd_used),
        default_output_dir=str(ctx.default_output_dir),
        project_root=str(ctx.project_root),
        app_root=str(ctx.app_root),
        user_data_root=str(ctx.user_data_root),
        workspace_root=str(ctx.workspace_root),
        sandbox_dir=str(ctx.sandbox_dir),
        is_user_explicit=is_user_explicit,
        used_default_output_dir=used_default_output_dir,
        allowed=allowed,
        code=code,
        reason=reason,
    )


def _default_user_data_root() -> Path:
    configured = os.getenv("HORIZON_USER_DATA_ROOT") or os.getenv("AGENT_USER_DATA_ROOT")
    if configured:
        return Path(configured)
    system = platform.system().lower()
    home = Path.home()
    if system == "darwin":
        return home / "Library" / "Application Support" / "Horizon Runtime"
    if system == "windows":
        return Path(os.getenv("APPDATA") or home / "AppData" / "Roaming") / "Horizon Runtime"
    return Path(os.getenv("XDG_DATA_HOME") or home / ".local" / "share") / "horizon-runtime"


def _default_output_dir(user_data_root: Path) -> Path:
    configured = os.getenv("AGENT_DEFAULT_OUTPUT_DIR", settings.agent_default_output_dir).strip()
    if configured:
        candidate = Path(os.path.expanduser(os.path.expandvars(configured)))
        return candidate.resolve() if candidate.is_absolute() else (user_data_root / candidate).resolve()
    return (user_data_root / "exports").resolve()


def _known_user_folder_path(path: str, ctx: PathContext) -> Path | None:
    parts = [part for part in path.replace("\\", "/").split("/") if part and part != "."]
    if not parts:
        return None
    first = parts[0].lower()
    mapping = {
        **{alias: "Desktop" for alias in DESKTOP_ALIASES},
        **{alias: "Documents" for alias in DOCUMENT_ALIASES},
        **{alias: "Downloads" for alias in DOWNLOAD_ALIASES},
    }
    folder = mapping.get(first)
    if folder:
        return (ctx.user_home / folder).joinpath(*parts[1:]).resolve()
    if path == "~" or path.startswith("~/") or path.startswith("~\\"):
        return _expanded_path(path).resolve()
    return None


def _expanded_path(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path.replace("%USERPROFILE%", os.getenv("USERPROFILE") or str(Path.home())))))


_WINDOWS_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_UNC_RE = re.compile(r"^(?:\\\\|//)[^\\/]+[\\/][^\\/]+")


def is_host_absolute_path(path: str | Path) -> bool:
    value = _clean(str(path))
    if not value:
        return False
    expanded = _expand_host_text(value)
    return bool(
        Path(expanded).is_absolute()
        or _WINDOWS_DRIVE_ABSOLUTE_RE.match(expanded)
        or _WINDOWS_UNC_RE.match(expanded)
    )


def resolve_host_path(path: str | Path, *, project_root: Path) -> str:
    value = _expand_host_text(_clean(str(path)))
    if (_WINDOWS_DRIVE_ABSOLUTE_RE.match(value) or _WINDOWS_UNC_RE.match(value)) and os.name != "nt":
        return str(PureWindowsPath(value))
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return str(candidate.resolve(strict=False))


def _expand_host_text(value: str) -> str:
    user_home = os.getenv("USERPROFILE") or str(Path.home())
    return os.path.expanduser(
        os.path.expandvars(value.replace("%USERPROFILE%", user_home))
    )


def _normalize_host_value(value: str) -> str:
    return _clean(value)


def _logical_root(path: str | Path, ctx: PathContext) -> str:
    text = str(path)
    if (_WINDOWS_DRIVE_ABSOLUTE_RE.match(text) or _WINDOWS_UNC_RE.match(text)) and os.name != "nt":
        return "external_directory"
    target = Path(text).resolve(strict=False)
    if _is_relative_to(target, ctx.project_root):
        return "project_root"
    if _is_relative_to(target, ctx.sandbox_dir):
        return "sandbox_dir"
    return "external_directory"


def _clean(value: str | None) -> str:
    return str(value or "").strip().strip("\"'`")


def _normalize(value: str) -> str:
    return _clean(value).replace("\\", "/")


def _has_traversal(value: str) -> bool:
    return any(part == ".." for part in value.replace("\\", "/").split("/") if part)


def _looks_like_directory(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower().strip("/\\")
    return stripped.endswith(("/", "\\")) or lowered in DESKTOP_ALIASES | DOCUMENT_ALIASES | DOWNLOAD_ALIASES


def _safe_id(value: str) -> str:
    result = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"_", "-"})
    return result or "default"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
