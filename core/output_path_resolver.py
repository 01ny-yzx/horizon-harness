"""Deterministic output path normalization from structured intent fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from core.file_access_policy import DESKTOP_ALIASES, DOWNLOAD_ALIASES


TARGET_ALIASES: dict[str, str] = {
    **{alias.lower(): "desktop" for alias in DESKTOP_ALIASES},
    **{alias.lower(): "downloads" for alias in DOWNLOAD_ALIASES},
    "download": "downloads",
    "workspace": "workspace",
    "exports": "workspace",
    "workspace_store": "workspace",
    "artifact": "artifact",
    "unknown": "unknown",
}
STANDARD_OUTPUT_TARGETS = {"desktop", "downloads", "workspace", "artifact", "unknown"}
PROTECTED_PROJECT_ROOTS = {
    "api",
    "core",
    "config",
    "frontend",
    "prompts",
    "providers",
    "scripts",
    "tests",
    "tools",
    "workflows",
}
PROTECTED_PROJECT_FILES = {"main.py", "README.md", "pyproject.toml", "requirements.txt"}


@dataclass(frozen=True)
class OutputPathResolution:
    complete: bool
    requested_output_path: str
    output_target: str
    output_filename: str
    output_format: str
    error: str = ""


def resolve_structured_output_path(
    *,
    output_target: str | None,
    output_filename: str | None,
    requested_output_path: str | None,
    output_format: str | None = None,
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> OutputPathResolution:
    """Return a stable logical request path from structured file-output fields."""

    home = home_dir or Path.home()
    project = project_root or Path.cwd()
    target = normalize_output_target(output_target)
    filename = _clean_text(output_filename)
    requested = _clean_text(requested_output_path)
    fmt = _clean_format(output_format)

    path_target = ""
    path_filename = ""
    path_is_directory = False
    if requested:
        path_target, path_filename, path_is_directory = _classify_requested_path(
            requested,
            home_dir=home,
            project_root=project,
        )

    if path_target and target in {"unknown", ""}:
        target = path_target
    if path_filename and not filename:
        filename = path_filename
    if not fmt:
        fmt = _format_from_filename(filename or path_filename)

    if not filename:
        return OutputPathResolution(
            complete=False,
            requested_output_path="",
            output_target=target or "unknown",
            output_filename="",
            output_format=fmt or "unknown",
            error="incomplete_file_output_parameters",
        )

    if target in {"", "unknown"} and path_target:
        target = path_target
    if target in {"", "unknown"}:
        target = path_target or "unknown"

    logical_path = ""
    if target in {"desktop", "downloads"}:
        logical_path = f"{target}/{filename}"
    elif target in {"", "unknown"} and requested and _looks_like_protected_project_path(requested):
        logical_path = _normalize_separators(requested)
    elif target in {"", "unknown"} and _looks_like_plain_filename(requested or filename):
        logical_path = f"exports/{filename}"
    elif requested and not path_is_directory and path_filename:
        logical_path = _normalize_separators(requested)
    elif target in {"workspace", "artifact"} and _looks_like_protected_project_path(filename):
        logical_path = filename
    elif target in {"workspace", "artifact"}:
        logical_path = filename if target == "artifact" else f"exports/{filename}"
    else:
        logical_path = _normalize_separators(requested) if requested else filename

    return OutputPathResolution(
        complete=True,
        requested_output_path=logical_path,
        output_target=target or "unknown",
        output_filename=filename,
        output_format=fmt or _format_from_filename(filename) or "unknown",
    )


def normalize_output_target(value: str | None) -> str:
    text = _clean_text(value).replace("\\", "/").strip("/").lower()
    if not text:
        return "unknown"
    first = text.split("/", 1)[0]
    return TARGET_ALIASES.get(first, TARGET_ALIASES.get(text, text if text in STANDARD_OUTPUT_TARGETS else "unknown"))


def path_has_filename(value: str | None) -> bool:
    target, filename, is_directory = _classify_requested_path(value or "", home_dir=Path.home(), project_root=Path.cwd())
    return bool(filename and not is_directory)


def _classify_requested_path(
    value: str,
    *,
    home_dir: Path,
    project_root: Path,
) -> tuple[str, str, bool]:
    text = _normalize_separators(value)
    stripped = text.strip("/")
    if not stripped:
        return "", "", True

    if _is_directory_request(text):
        return normalize_output_target(stripped), "", True

    parts = [part for part in stripped.split("/") if part and part != "."]
    first = parts[0].lower() if parts else ""
    if first in TARGET_ALIASES:
        target = normalize_output_target(first)
        filename = parts[-1] if len(parts) > 1 else ""
        return target, filename, not filename

    home_logical = _logical_known_folder_path(text, home_dir=home_dir)
    if home_logical:
        target, filename, is_directory = _classify_requested_path(home_logical, home_dir=home_dir, project_root=project_root)
        return target, filename, is_directory

    project_logical = _logical_project_output_path(text, project_root=project_root)
    if project_logical:
        return _classify_requested_path(project_logical, home_dir=home_dir, project_root=project_root)

    return "", parts[-1] if parts else "", False


def _logical_known_folder_path(value: str, *, home_dir: Path) -> str:
    text = _normalize_separators(value)
    if text.startswith("~/"):
        return _known_folder_suffix(text[2:])

    home_posix = _normalize_separators(str(home_dir)).rstrip("/")
    if home_posix and text.lower().startswith(home_posix.lower() + "/"):
        return _known_folder_suffix(text[len(home_posix) + 1 :])

    windows_home = PureWindowsPath(str(home_dir)).as_posix().rstrip("/")
    win_text = PureWindowsPath(value).as_posix() if _looks_like_windows_path(value) else text
    if windows_home and win_text.lower().startswith(windows_home.lower() + "/"):
        return _known_folder_suffix(win_text[len(windows_home) + 1 :])
    generic_user_home = re.match(r"^/(?:users|home)/[^/]+/(desktop|downloads|download)(?:/(.*))?$", text, re.IGNORECASE)
    if generic_user_home:
        suffix = generic_user_home.group(2) or ""
        return "/".join(part for part in (generic_user_home.group(1), suffix) if part)
    generic_windows_home = re.match(r"^[A-Za-z]:/users/[^/]+/(desktop|downloads|download)(?:/(.*))?$", win_text, re.IGNORECASE)
    if generic_windows_home:
        suffix = generic_windows_home.group(2) or ""
        return "/".join(part for part in (generic_windows_home.group(1), suffix) if part)
    return ""


def _logical_project_output_path(value: str, *, project_root: Path) -> str:
    text = _normalize_separators(value)
    project_posix = _normalize_separators(str(project_root)).rstrip("/")
    if project_posix and text.lower().startswith(project_posix.lower() + "/"):
        rel = text[len(project_posix) + 1 :]
        if rel.lower().startswith("exports/"):
            return rel
    return ""


def _known_folder_suffix(value: str) -> str:
    parts = [part for part in _normalize_separators(value).strip("/").split("/") if part]
    if not parts:
        return ""
    first = parts[0].lower()
    if first in {"desktop", "桌面"}:
        return "/".join(["desktop", *parts[1:]])
    if first in {"downloads", "download", "下载"}:
        return "/".join(["downloads", *parts[1:]])
    return ""


def _looks_like_protected_project_path(value: str) -> bool:
    text = _normalize_separators(value).strip("/")
    if not text:
        return False
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts:
        return False
    return parts[0].lower() in PROTECTED_PROJECT_ROOTS or parts[0] in PROTECTED_PROJECT_FILES


def _looks_like_plain_filename(value: str) -> bool:
    text = _normalize_separators(value).strip()
    if not text or text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", text):
        return False
    return "/" not in text


def _is_directory_request(value: str) -> bool:
    text = _normalize_separators(value).strip()
    stripped = text.strip("/").lower()
    return text.endswith("/") or stripped in TARGET_ALIASES


def _format_from_filename(filename: str) -> str:
    name = _clean_text(filename)
    if "." not in name or name.endswith("."):
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _clean_format(value: str | None) -> str:
    text = _clean_text(value).lower().lstrip(".")
    return "" if text in {"", "unknown", "none"} else text


def _clean_text(value: str | None) -> str:
    return str(value or "").strip().strip("\"'`")


def _normalize_separators(value: str) -> str:
    if _looks_like_windows_path(value):
        text = PureWindowsPath(value).as_posix()
    else:
        text = str(value or "").replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    return text.strip().strip("\"'`")


def _looks_like_windows_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", str(value or "")))
