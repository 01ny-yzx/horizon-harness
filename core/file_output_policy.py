"""Policy-gated file output targets for local file tools."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT
from core.agent_access_policy import get_agent_access_mode
from core.file_access_policy import (
    DESKTOP_ALIASES,
    DOCUMENT_ALIASES,
    DOWNLOAD_ALIASES,
    FileAccessPolicy,
    expand_requested_path,
    is_relative_to,
)
from core.file_output_ux import default_filename_for_content, safe_filename
from core.path_grounding import build_path_context, ground_write_path, path_resolution_to_dict
from core.path_zone_policy import default_output_root as path_zone_default_output_root
from core.path_zone_policy import evaluate_file_output_path_zone


@dataclass
class OutputTarget:
    target_type: str
    safe_path: str | None
    requested_path: str
    artifact_id: str | None = None
    download_url: str | None = None
    reason: str = ""
    code: str = "ok"
    path_grounding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FileOutputPolicy:
    """Resolve requested output paths into local paths under the agent access boundary."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (project_root or PROJECT_ROOT).resolve()

    def resolve_output_target(
        self,
        user_path: str,
        *,
        mode: str | None = None,
        filename: str | None = None,
        workspace_root: Path | None = None,
        existing: bool = False,
        raw_requested_path: str | None = None,
        default_bare_filename_to_output_dir: bool = True,
        allow_agent_internal: bool = False,
    ) -> OutputTarget:
        del mode, workspace_root, existing
        requested = str(user_path or "").strip()
        raw_path = raw_requested_path if raw_requested_path is not None else requested

        if get_agent_access_mode() == "read_only":
            grounding = self._ground_write(
                requested,
                filename=filename,
                default_bare_filename_to_output_dir=default_bare_filename_to_output_dir,
                allow_agent_internal=allow_agent_internal,
            )
            return self._denied(
                requested,
                "agent_access_mode_read_only",
                "当前 Agent 权限模式为 read_only，不允许写入、修改或删除文件。",
                grounding,
            )

        grounding = self._ground_write(
            requested,
            filename=filename,
            default_bare_filename_to_output_dir=default_bare_filename_to_output_dir,
            allow_agent_internal=allow_agent_internal,
        )
        zone = evaluate_file_output_path_zone(
            requested_path=requested,
            raw_requested_path=raw_path,
            filename=filename,
            project_root=self.project_root,
            allow_agent_internal=allow_agent_internal,
            default_relative_to_output_root=default_bare_filename_to_output_dir,
        )
        if not zone.allowed:
            return self._denied(requested, zone.code, zone.reason, grounding)

        target = Path(zone.resolved_path)
        decision = FileAccessPolicy(self.project_root).evaluate(
            target,
            operation="write",
            raw_requested_path=str(target),
            default_bare_filename_to_output_dir=default_bare_filename_to_output_dir,
        )
        if not decision.allowed or not decision.resolved_path:
            return self._denied(requested, decision.code, decision.reason, decision.path_grounding or grounding)
        return OutputTarget("local_path", decision.resolved_path, requested, path_grounding=grounding)

    def resolve_existing_write_target(
        self,
        path: str,
        *,
        raw_requested_path: str | None = None,
        allow_agent_internal: bool = False,
    ) -> OutputTarget:
        return self.resolve_output_target(
            path,
            raw_requested_path=raw_requested_path,
            default_bare_filename_to_output_dir=False,
            allow_agent_internal=allow_agent_internal,
        )

    def artifact_path_for_id(self, artifact_id: str) -> Path | None:
        artifact_id = str(artifact_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,160}", artifact_id):
            return None
        root = default_output_root(self.project_root)
        path = (root / artifact_id).resolve()
        if not is_relative_to(path, root):
            return None
        decision = FileAccessPolicy(self.project_root).evaluate(path, operation="read")
        if not decision.allowed:
            return None
        return path

    def _target_path(
        self,
        requested: str,
        filename: str | None,
        *,
        default_bare_filename_to_output_dir: bool = True,
    ) -> Path:
        leaf_name = safe_filename(filename or Path(requested).name or default_filename_for_content(""))
        if not requested:
            return (default_output_root(self.project_root) / leaf_name).resolve()
        if default_bare_filename_to_output_dir and _is_bare_filename_request(requested):
            return (default_output_root(self.project_root) / leaf_name).resolve()
        target = expand_requested_path(requested, [], self.project_root).resolve()
        if _looks_like_directory_request(requested):
            target = (target / safe_filename(filename or default_filename_for_content(""))).resolve()
        return target

    def _ground_write(
        self,
        requested: str,
        *,
        filename: str | None,
        default_bare_filename_to_output_dir: bool,
        allow_agent_internal: bool,
    ) -> dict[str, Any]:
        context = build_path_context(project_root=self.project_root)
        return path_resolution_to_dict(
            ground_write_path(
                requested,
                context=context,
                filename=filename,
                default_bare_filename_to_output_dir=default_bare_filename_to_output_dir,
                allow_agent_internal=allow_agent_internal,
            )
        )

    @staticmethod
    def _denied(requested: str, code: str, reason: str, path_grounding: dict[str, Any] | None = None) -> OutputTarget:
        return OutputTarget("denied", None, requested, reason=reason, code=code, path_grounding=path_grounding)


def resolve_output_target(
    user_path: str,
    *,
    mode: str | None = None,
    filename: str | None = None,
    workspace_root: Path | None = None,
    existing: bool = False,
    raw_requested_path: str | None = None,
    default_bare_filename_to_output_dir: bool = True,
    allow_agent_internal: bool = False,
) -> OutputTarget:
    return FileOutputPolicy().resolve_output_target(
        user_path,
        mode=mode,
        filename=filename,
        workspace_root=workspace_root,
        existing=existing,
        raw_requested_path=raw_requested_path,
        default_bare_filename_to_output_dir=default_bare_filename_to_output_dir,
        allow_agent_internal=allow_agent_internal,
    )


def resolve_existing_write_target(
    path: str,
    *,
    raw_requested_path: str | None = None,
    allow_agent_internal: bool = False,
) -> OutputTarget:
    return FileOutputPolicy().resolve_existing_write_target(
        path,
        raw_requested_path=raw_requested_path,
        allow_agent_internal=allow_agent_internal,
    )


def default_output_root(project_root: Path | None = None) -> Path:
    return path_zone_default_output_root(project_root or PROJECT_ROOT)


def _looks_like_directory_request(path: str) -> bool:
    stripped = path.strip().replace("\\", "/")
    return stripped.lower() in DESKTOP_ALIASES | DOWNLOAD_ALIASES | DOCUMENT_ALIASES or stripped.endswith("/")


def _is_bare_filename_request(path: str) -> bool:
    stripped = path.strip()
    if not stripped or _looks_like_directory_request(stripped):
        return False
    if stripped.startswith("~") or Path(stripped).expanduser().is_absolute():
        return False
    if "/" in stripped or "\\" in stripped:
        return False
    return True
