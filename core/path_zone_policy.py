"""Path zone policy for file-output write targets.

This module classifies write targets by resolved path zone before any file
output grounding or path rewriting can make a protected request look safe.
It does not execute tools, write files, call LLMs, or access the network.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT
from core.file_access_policy import (
    DESKTOP_ALIASES,
    DOCUMENT_ALIASES,
    DOWNLOAD_ALIASES,
    expand_requested_path,
    is_relative_to,
    raw_path_parts,
)
from core.path_grounding import build_path_context, ground_write_path


AGENT_OUTPUT_AREA = "agent_output_area"
AGENT_SELF_PROTECTED_AREA = "agent_self_protected_area"
AGENT_INTERNAL_PROTECTED_AREA = "agent_internal_protected_area"
USER_EXTERNAL_AREA = "user_external_area"
TRAVERSAL_TO_ALLOWED_AREA = "traversal_to_allowed_area"
RELATIVE_OUTPUT_AREA = "relative_output_area"
PROJECT_OUTPUT_PREFIXES = ("exports", "workspace_store/exports")
PROJECT_PROTECTED_RELATIVE_ROOTS = {
    "api",
    "core",
    "config",
    "docs",
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
PROJECT_PROTECTED_ROOT_FILES = {
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "main.py",
}


@dataclass(frozen=True)
class PathZoneDecision:
    allowed: bool
    zone: str
    code: str
    reason: str
    raw_path: str
    requested_path: str
    resolved_path: str
    should_ground_to_output_root: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_file_output_path_zone(
    *,
    requested_path: str,
    raw_requested_path: str | None = None,
    filename: str | None = None,
    project_root: Path | None = None,
    output_root: Path | None = None,
    allow_agent_internal: bool = False,
    default_relative_to_output_root: bool = True,
    raw_context: str | None = None,
) -> PathZoneDecision:
    """Return the file-output write decision for a requested path."""

    project = (project_root or PROJECT_ROOT).resolve()
    output = (output_root or default_output_root(project)).resolve()
    requested = _clean_path(requested_path)
    raw = _clean_path(raw_requested_path) if raw_requested_path is not None else requested

    return _evaluate_single_path(
        raw or requested,
        requested_path=requested,
        filename=filename,
        project_root=project,
        output_root=output,
        allow_agent_internal=allow_agent_internal,
        default_relative_to_output_root=default_relative_to_output_root,
        raw_path=raw or requested,
    )


def default_output_root(project_root: Path | None = None) -> Path:
    del project_root
    return build_path_context().default_output_dir.resolve()


def output_roots(project_root: Path, output_root: Path | None = None) -> list[Path]:
    roots = [
        (output_root or default_output_root(project_root)).resolve(),
        (project_root / "exports").resolve(),
        (project_root / "workspace_store" / "exports").resolve(),
    ]
    return _dedupe_paths(roots)


def is_agent_output_path(path: Path, project_root: Path, output_root: Path | None = None) -> bool:
    target = path.resolve()
    return any(is_relative_to(target, root) for root in output_roots(project_root.resolve(), output_root))


def _evaluate_single_path(
    value: str,
    *,
    requested_path: str,
    filename: str | None,
    project_root: Path,
    output_root: Path,
    allow_agent_internal: bool,
    default_relative_to_output_root: bool,
    raw_path: str,
) -> PathZoneDecision:
    text = _clean_path(value)
    if not text and filename:
        text = _safe_leaf(filename)
    if not text:
        return _decision(False, USER_EXTERNAL_AREA, "path_empty", "目标路径不能为空。", raw_path, requested_path, "")

    target = _resolve_file_output_path(
        text,
        filename=filename,
        project_root=project_root,
        output_root=output_root,
        default_relative_to_output_root=default_relative_to_output_root,
    )
    zone = _classify_zone(text, target, project_root=project_root, output_root=output_root)
    if zone == AGENT_SELF_PROTECTED_AREA:
        return _decision(
            False,
            zone,
            "agent_self_protected_path_blocked",
            "运行时 Agent 不允许修改自身系统项目内部文件。",
            raw_path,
            requested_path,
            str(target),
        )
    if zone == AGENT_INTERNAL_PROTECTED_AREA and not allow_agent_internal:
        return _decision(False, zone, "agent_internal_protected_path_blocked", "普通文件输出不能写入 Agent 项目内部非输出区。", raw_path, requested_path, str(target))

    should_ground = _is_relative_output_request(text) and not _has_traversal(text)
    return _decision(True, zone, "ok", "File output path zone allowed.", raw_path, requested_path, str(target), should_ground)


def _resolve_file_output_path(
    text: str,
    *,
    filename: str | None,
    project_root: Path,
    output_root: Path,
    default_relative_to_output_root: bool,
) -> Path:
    normalized = _clean_path(text)
    leaf = _safe_leaf(filename) if filename else ""
    if _has_traversal(normalized):
        target = (project_root / normalized).resolve()
    else:
        context = build_path_context(project_root=project_root)
        if output_root != context.default_output_dir:
            context = dataclass_replace(context, default_output_dir=output_root.resolve())
        grounded = ground_write_path(
            normalized,
            context=context,
            filename=filename,
            default_bare_filename_to_output_dir=default_relative_to_output_root,
        )
        target = Path(grounded.resolved_path)
    if _looks_like_directory_request(normalized):
        target = (target / (leaf or "output.txt")).resolve()
    return target


def _classify_zone(text: str, target: Path, *, project_root: Path, output_root: Path) -> str:
    if _is_agent_self_protected_write_target(text, target, project_root=project_root):
        return AGENT_SELF_PROTECTED_AREA
    if _is_known_folder_request(text) or text.startswith("~") or _is_known_user_output_area(target):
        return USER_EXTERNAL_AREA
    if _looks_like_agent_internal_relative_request(text):
        return AGENT_INTERNAL_PROTECTED_AREA
    if is_agent_output_path(target, project_root, output_root):
        return AGENT_OUTPUT_AREA if _starts_with_output_prefix(text) or _is_absolute_like(text) else RELATIVE_OUTPUT_AREA
    if is_relative_to(target, project_root):
        return USER_EXTERNAL_AREA
    if _has_traversal(text):
        return TRAVERSAL_TO_ALLOWED_AREA
    return USER_EXTERNAL_AREA if _is_absolute_like(text) else RELATIVE_OUTPUT_AREA


def _is_known_folder_request(value: str) -> bool:
    parts = raw_path_parts(value)
    return bool(parts and parts[0].lower() in DESKTOP_ALIASES | DOWNLOAD_ALIASES | DOCUMENT_ALIASES)


def _starts_with_output_prefix(value: str) -> bool:
    lowered = _clean_path(value).lower().lstrip("/")
    return lowered.startswith(PROJECT_OUTPUT_PREFIXES)


def _is_relative_output_request(value: str) -> bool:
    return not _is_absolute_like(value) and not _is_known_folder_request(value) and not _starts_with_output_prefix(value)


def _looks_like_agent_internal_relative_request(value: str) -> bool:
    if _is_absolute_like(value) or _has_traversal(value) or _starts_with_output_prefix(value) or _is_known_folder_request(value):
        return False
    parts = [part for part in _clean_path(value).replace("\\", "/").split("/") if part and part != "."]
    if not parts:
        return False
    return parts[0].lower() in PROJECT_PROTECTED_RELATIVE_ROOTS or parts[0] in PROJECT_PROTECTED_ROOT_FILES


def _is_agent_self_protected_write_target(text: str, target: Path, *, project_root: Path) -> bool:
    self_root = PROJECT_ROOT.resolve()
    cleaned = _clean_path(text)
    if not cleaned:
        return False

    if project_root.resolve() == self_root and _looks_like_agent_internal_relative_request(cleaned):
        return True
    if _is_agent_output_under_self_root(target, self_root):
        return False

    try:
        relative_parts = target.resolve().relative_to(self_root).parts
    except ValueError:
        return False
    if not relative_parts:
        return False
    first = relative_parts[0].lower()
    if first in {prefix.split("/", 1)[0] for prefix in PROJECT_OUTPUT_PREFIXES}:
        return False
    return first in PROJECT_PROTECTED_RELATIVE_ROOTS or relative_parts[0] in PROJECT_PROTECTED_ROOT_FILES


def _is_agent_output_under_self_root(path: Path, self_root: Path) -> bool:
    target = path.resolve()
    output_roots_for_self = output_roots(self_root)
    return any(is_relative_to(target, root) for root in output_roots_for_self)


def _looks_like_directory_request(value: str) -> bool:
    stripped = _clean_path(value)
    lowered = stripped.lower().strip("/")
    return stripped.endswith("/") or lowered in DESKTOP_ALIASES | DOWNLOAD_ALIASES | DOCUMENT_ALIASES


def _has_traversal(value: str) -> bool:
    return any(part == ".." for part in raw_path_parts(value))


def _is_absolute_like(value: str) -> bool:
    text = _clean_path(value)
    if text.startswith("~"):
        return True
    if _looks_like_windows_path(text):
        return True
    return Path(os.path.expandvars(text)).expanduser().is_absolute()


def _looks_like_windows_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", str(value or "")))


def _is_known_user_output_area(path: Path) -> bool:
    home = Path(os.getenv("USERPROFILE") or str(Path.home())).resolve()
    target = path.resolve()
    return any(is_relative_to(target, home / name) for name in ("Desktop", "Documents", "Downloads"))


def _safe_leaf(value: str | None) -> str:
    leaf = Path(str(value or "").replace("\\", "/")).name.strip()
    return leaf or "output.txt"


def _clean_path(value: str | None) -> str:
    return str(value or "").strip().strip("\"'`")


def _decision(
    allowed: bool,
    zone: str,
    code: str,
    reason: str,
    raw_path: str,
    requested_path: str,
    resolved_path: str,
    should_ground_to_output_root: bool = False,
) -> PathZoneDecision:
    return PathZoneDecision(
        allowed=allowed,
        zone=zone,
        code=code,
        reason=reason,
        raw_path=raw_path,
        requested_path=requested_path,
        resolved_path=resolved_path,
        should_ground_to_output_root=should_ground_to_output_root,
    )


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in result:
            result.append(resolved)
    return result
