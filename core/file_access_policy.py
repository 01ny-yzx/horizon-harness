"""Unified local file access safety policy."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from config.settings import PROJECT_ROOT
from core.agent_access_policy import evaluate_agent_file_access
from core.path_grounding import build_path_context, ground_read_path, ground_write_path, path_resolution_to_dict


FileOperation = Literal["read", "write"]

DESKTOP_ALIASES = {"desktop", "桌面"}
DOWNLOAD_ALIASES = {"downloads", "download", "下载"}
DOCUMENT_ALIASES = {"documents", "document", "文档"}
KNOWN_FOLDER_ALIASES = DESKTOP_ALIASES | DOWNLOAD_ALIASES | DOCUMENT_ALIASES

@dataclass
class FileAccessDecision:
    allowed: bool
    operation: str
    scope: str
    requested_path: str
    resolved_path: str | None = None
    reason: str = ""
    code: str = "ok"
    suggestion: str = ""
    allowed_roots: list[str] | None = None
    path_grounding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FileAccessScope:
    FULL_ACCESS = "full_access"
    DENIED_AGENT_ACCESS = "denied_agent_access"
    AUTHORIZED_ROOT = "authorized_root"
    INVALID_PATH = "invalid_path"


class FileAccessPolicy:
    """Classify and gate local file reads/writes."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (project_root or PROJECT_ROOT).resolve()

    def evaluate(
        self,
        path: str | Path,
        *,
        operation: FileOperation,
        raw_requested_path: str | None = None,
        allowed_roots: list[Path] | None = None,
        workspace_root: Path | None = None,
        artifact_root: Path | None = None,
        allow_project_readonly: bool = True,
        default_bare_filename_to_output_dir: bool = False,
    ) -> FileAccessDecision:
        requested = str(path or "").strip()
        path_grounding = self._ground_path(
            requested,
            operation=operation,
            default_bare_filename_to_output_dir=default_bare_filename_to_output_dir,
        )
        agent_decision = evaluate_agent_file_access(
            requested,
            operation=operation,
            raw_requested_path=raw_requested_path if raw_requested_path is not None else requested,
            project_root=self.project_root,
        )
        if not agent_decision.allowed:
            return self._deny(
                operation,
                FileAccessScope.DENIED_AGENT_ACCESS,
                requested,
                agent_decision.resolved_path,
                agent_decision.code,
                agent_decision.reason,
                path_grounding=path_grounding,
            )
        if not requested:
            return self._deny(operation, FileAccessScope.INVALID_PATH, requested, None, "path_empty", "目标路径不能为空。", path_grounding=path_grounding)
        roots = [
            Path(root).expanduser().resolve(strict=False)
            for root in (allowed_roots or [])
        ]
        return FileAccessDecision(
            True,
            operation,
            FileAccessScope.FULL_ACCESS if operation == "write" else FileAccessScope.AUTHORIZED_ROOT,
            requested,
            agent_decision.resolved_path,
            allowed_roots=[str(item) for item in roots],
            path_grounding=path_grounding,
        )

    def preflight_raw_path(
        self,
        raw_path: str | Path | None,
        *,
        operation: FileOperation,
        requested_path: str | None = None,
    ) -> FileAccessDecision:
        requested = str(requested_path if requested_path is not None else raw_path or "").strip()
        if not requested:
            return self._deny(operation, FileAccessScope.INVALID_PATH, requested, None, "path_empty", "目标路径不能为空。")
        return FileAccessDecision(True, operation, "raw_path_ok", requested)

    def output_roots(self, *, workspace_root: Path | None = None, artifact_root: Path | None = None) -> list[Path]:
        context = build_path_context(project_root=self.project_root)
        roots = [
            context.default_output_dir,
            (self.project_root / "exports").resolve(),
            (workspace_root or context.workspace_root).resolve() / "exports",
        ]
        if artifact_root is not None:
            roots.append(artifact_root.resolve())
        return [root.resolve(strict=False) for root in roots]

    @staticmethod
    def _deny(
        operation: str,
        scope: str,
        requested: str,
        resolved: str | None,
        code: str,
        reason: str,
        roots: list[Path] | None = None,
        path_grounding: dict[str, Any] | None = None,
    ) -> FileAccessDecision:
        suggestion = "请改用 exports/ 或已授权目录。"
        return FileAccessDecision(
            False,
            operation,
            scope,
            requested,
            resolved,
            reason,
            code,
            suggestion,
            [str(root) for root in roots] if roots else [],
            path_grounding,
        )

    def _ground_path(
        self,
        requested: str,
        *,
        operation: FileOperation,
        default_bare_filename_to_output_dir: bool,
    ) -> dict[str, Any]:
        context = build_path_context(project_root=self.project_root)
        if operation == "read":
            return path_resolution_to_dict(ground_read_path(requested, context=context))
        return path_resolution_to_dict(
            ground_write_path(
                requested,
                context=context,
                default_bare_filename_to_output_dir=default_bare_filename_to_output_dir,
            )
        )


def expand_requested_path(requested: str, allowed_roots: list[Path], project_root: Path) -> Path:
    raw_parts = raw_path_parts(requested)
    first = raw_parts[0].lower() if raw_parts else ""
    remainder = raw_parts[1:]
    if first in DESKTOP_ALIASES:
        return _known_folder("Desktop", allowed_roots).joinpath(*remainder)
    if first in DOWNLOAD_ALIASES:
        return _known_folder("Downloads", allowed_roots).joinpath(*remainder)
    if first in DOCUMENT_ALIASES:
        return _known_folder("Documents", allowed_roots).joinpath(*remainder)
    text = str(requested)
    user_home = os.getenv("USERPROFILE") or str(Path.home())
    if text == "~" or text.startswith("~/") or text.startswith("~\\"):
        text = user_home + text[1:]
    expanded = os.path.expandvars(text.replace("%USERPROFILE%", user_home))
    expanded = os.path.expanduser(expanded)
    path = Path(expanded)
    return path if path.is_absolute() else project_root / path


def raw_path_parts(path: str) -> list[str]:
    normalized = _decoded_path(path).replace("\\", "/").strip()
    return [part for part in normalized.split("/") if part and part not in {".", "~"} and not part.endswith(":")]


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _known_folder(name: str, allowed_roots: list[Path]) -> Path:
    if os.getenv("USERPROFILE"):
        return (Path(os.getenv("USERPROFILE", "")) / name).resolve()
    for root in allowed_roots:
        if root.name.lower() == name.lower():
            return root
    return (Path(os.getenv("USERPROFILE") or str(Path.home())) / name).resolve()


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in result:
            result.append(resolved)
    return result


def _decoded_path(path: str) -> str:
    decoded = str(path or "")
    for _ in range(3):
        new_value = unquote(decoded)
        if new_value == decoded:
            break
        decoded = new_value
    return decoded
