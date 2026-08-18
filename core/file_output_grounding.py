"""Current-turn file output path grounding helpers."""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any

from core.output_path_resolver import resolve_structured_output_path


if TYPE_CHECKING:  # pragma: no cover
    from core.state import TaskState


def file_output_path_grounding_error(task_state: "TaskState", tool_path: str) -> dict[str, Any] | None:
    """Return a structured mismatch error when a write path is not current-turn grounded."""

    tool_path = str(tool_path or "").strip()
    if not tool_path:
        return None
    expected_paths = current_turn_output_paths(task_state)
    profile = task_state.task_profile
    if not expected_paths and profile and getattr(profile, "needs_file_output", False) and not getattr(profile, "is_coding_task", False):
        return {
            "success": False,
            "error": "当前文件输出请求缺少明确的文件名或输出路径。",
            "data": {
                "code": "file_output_parameters_incomplete",
                "requested_output_path": "",
                "tool_path": tool_path,
                "suggestion": "请先取得明确的文件名和输出位置，再调用 write_file。",
            },
        }
    if not expected_paths:
        return None
    if any(file_output_path_matches(tool_path, expected) for expected in expected_paths):
        return None
    requested = display_requested_output_path(task_state, expected_paths)
    return {
        "success": False,
        "error": "当前工具调用的输出路径不属于本轮用户请求。",
        "data": {
            "code": "file_output_path_mismatch",
            "requested_output_path": requested,
            "tool_path": tool_path,
            "suggestion": "请重新调用 write_file，并使用本轮请求对应的输出路径。",
        },
    }


def current_turn_output_paths(task_state: "TaskState") -> list[str]:
    """Return concrete current-turn output path candidates from structured profile fields."""

    profile = task_state.task_profile
    values: list[str] = []
    if profile:
        raw_requested = str(getattr(profile, "raw_requested_output_path", "") or "").strip()
        if raw_requested:
            values.append(raw_requested)
        requested = str(getattr(profile, "requested_output_path", "") or "").strip()
        if requested:
            values.append(requested)
        target = str(getattr(profile, "output_target", "") or "").strip()
        filename = str(getattr(profile, "output_filename", "") or "").strip()
        resolution = resolve_structured_output_path(
            output_target=target,
            output_filename=filename,
            requested_output_path=requested,
            output_format=str(getattr(profile, "output_format", "") or ""),
        )
        if resolution.complete:
            values.append(resolution.requested_output_path)
    return list(dict.fromkeys(value for value in values if value))


def display_requested_output_path(task_state: "TaskState", expected_paths: list[str]) -> str:
    profile = task_state.task_profile
    if profile:
        raw_requested = str(getattr(profile, "raw_requested_output_path", "") or "").strip()
        if raw_requested:
            return raw_requested
        requested = str(getattr(profile, "requested_output_path", "") or "").strip()
        if requested:
            return requested
    return expected_paths[0] if expected_paths else ""


def file_output_path_matches(tool_path: str, expected_path: str) -> bool:
    tool_identity = canonical_output_identity(tool_path)
    expected_identity = canonical_output_identity(expected_path)
    if tool_identity and expected_identity:
        return tool_identity == expected_identity

    tool_norm = normalize_output_path_for_match(tool_path)
    expected_norm = normalize_output_path_for_match(expected_path)
    if not tool_norm or not expected_norm:
        return False
    if tool_norm == expected_norm:
        return True
    if resolved_match(tool_path, expected_path):
        return True
    return False


def canonical_output_identity(
    path: str,
    *,
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> str | None:
    text = str(path or "").strip()
    if not text:
        return None
    resolution = resolve_structured_output_path(
        output_target=None,
        output_filename=None,
        requested_output_path=text,
        output_format=None,
        home_dir=home_dir,
        project_root=project_root,
    )
    if resolution.complete:
        return normalize_output_path_for_match(resolution.requested_output_path)
    if looks_like_windows_path(text):
        return PureWindowsPath(text).as_posix().lower().rstrip("/")
    try:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = (project_root or Path.cwd()) / candidate
        return str(candidate.resolve(strict=False)).replace("\\", "/").lower().rstrip("/")
    except (OSError, RuntimeError, ValueError):
        return None


def normalize_output_path_for_match(value: str) -> str:
    text = str(value or "").strip().strip("\"'`")
    text = text.replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    if len(text) >= 2 and text[1] == ":":
        text = text[0].lower() + text[1:]
    return text.rstrip("/").lower()


def resolved_match(tool_path: str, expected_path: str) -> bool:
    if looks_like_windows_path(tool_path) or looks_like_windows_path(expected_path):
        return PureWindowsPath(tool_path).as_posix().lower() == PureWindowsPath(expected_path).as_posix().lower()
    try:
        tool_resolved = Path(tool_path).expanduser()
        expected_resolved = Path(expected_path).expanduser()
        if not tool_resolved.is_absolute():
            tool_resolved = Path.cwd() / tool_resolved
        if not expected_resolved.is_absolute():
            expected_resolved = Path.cwd() / expected_resolved
        return tool_resolved.resolve(strict=False) == expected_resolved.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False


def looks_like_windows_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", str(value or "")))


def path_basename(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
