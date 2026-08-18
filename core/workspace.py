"""Workspace isolation for user/project scoped local stores."""

from __future__ import annotations

import re
import shutil
import os
from dataclasses import dataclass
from pathlib import Path

from config.settings import settings
from core.path_grounding import build_path_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class WorkspaceContext:
    """Resolved local directories for one user/project workspace."""

    user_id: str
    project_id: str
    root_dir: Path
    workspace_dir: Path
    memory_dir: Path
    document_dir: Path
    vector_dir: Path
    trace_dir: Path
    logs_dir: Path

    @property
    def workspace_id(self) -> str:
        return f"{self.user_id}/{self.project_id}"


class WorkspaceManager:
    """Create, inspect, and safely remove isolated workspaces."""

    def __init__(self, root_dir: Path | str | None = None) -> None:
        configured_root = root_dir if root_dir is not None else _configured_workspace_root()
        self.root_dir = _resolve_workspace_root(configured_root)

    def get_context(self, user_id: str | None = None, project_id: str | None = None) -> WorkspaceContext:
        """Return a sanitized workspace context."""

        if not settings.enable_workspace_isolation:
            return WorkspaceContext(
                user_id=self.sanitize_id(user_id, settings.default_user_id),
                project_id=self.sanitize_id(project_id, settings.default_project_id),
                root_dir=PROJECT_ROOT,
                workspace_dir=PROJECT_ROOT,
                memory_dir=PROJECT_ROOT / "memory_store",
                document_dir=PROJECT_ROOT / "document_store",
                vector_dir=PROJECT_ROOT / "vector_store",
                trace_dir=PROJECT_ROOT / "logs",
                logs_dir=PROJECT_ROOT / "logs",
            )

        safe_user = self.sanitize_id(user_id, settings.default_user_id)
        safe_project = self.sanitize_id(project_id, settings.default_project_id)
        workspace_dir = self.root_dir / safe_user / safe_project
        context = WorkspaceContext(
            user_id=safe_user,
            project_id=safe_project,
            root_dir=self.root_dir,
            workspace_dir=workspace_dir,
            memory_dir=workspace_dir / "memory_store",
            document_dir=workspace_dir / "document_store",
            vector_dir=workspace_dir / "vector_store",
            trace_dir=workspace_dir / "traces",
            logs_dir=workspace_dir / "logs",
        )
        self.ensure_workspace(context)
        return context

    def ensure_workspace(self, context: WorkspaceContext) -> WorkspaceContext:
        """Create all directories required by a workspace."""

        for path in (
            context.root_dir,
            context.workspace_dir,
            context.memory_dir,
            context.document_dir,
            context.vector_dir,
            context.trace_dir,
            context.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return context

    def sanitize_id(self, value: str | None, default: str | None = None) -> str:
        """Validate a user_id/project_id segment against path traversal."""

        candidate = str(value or default or settings.default_user_id).strip()
        if not candidate:
            candidate = str(default or settings.default_user_id)
        if "/" in candidate or "\\" in candidate or ".." in candidate:
            raise ValueError("workspace id must not contain path separators or '..'")
        if Path(candidate).is_absolute():
            raise ValueError("workspace id must not be an absolute path")
        if not SAFE_ID_RE.fullmatch(candidate):
            raise ValueError("workspace id may only contain letters, digits, underscore, and hyphen, max length 64")
        return candidate

    def list_workspaces(self) -> list[dict[str, str]]:
        """List user/project workspaces under workspace_store."""

        if not self.root_dir.exists():
            return []
        results: list[dict[str, str]] = []
        for user_dir in sorted(path for path in self.root_dir.iterdir() if path.is_dir()):
            try:
                safe_user = self.sanitize_id(user_dir.name, settings.default_user_id)
            except ValueError:
                continue
            for project_dir in sorted(path for path in user_dir.iterdir() if path.is_dir()):
                try:
                    safe_project = self.sanitize_id(project_dir.name, settings.default_project_id)
                except ValueError:
                    continue
                results.append(
                    {
                        "user_id": safe_user,
                        "project_id": safe_project,
                        "workspace_id": f"{safe_user}/{safe_project}",
                    }
                )
        return results

    def delete_project_workspace(self, user_id: str, project_id: str) -> dict[str, object]:
        """Delete one sanitized project workspace, never the workspace root."""

        safe_user = self.sanitize_id(user_id, settings.default_user_id)
        safe_project = self.sanitize_id(project_id, settings.default_project_id)
        target = (self.root_dir / safe_user / safe_project).resolve()
        root = self.root_dir.resolve()
        if target == root or root not in target.parents:
            raise ValueError("refusing to delete outside workspace root")
        if not target.exists():
            return {"success": True, "deleted": False, "workspace_id": f"{safe_user}/{safe_project}"}
        shutil.rmtree(target)
        return {"success": True, "deleted": True, "workspace_id": f"{safe_user}/{safe_project}"}


def _resolve_project_path(path: Path | str) -> Path:
    raw = Path(path)
    return raw if raw.is_absolute() else PROJECT_ROOT / raw


def _resolve_workspace_root(path: Path | str | None) -> Path:
    if path is None:
        return build_path_context().workspace_root
    raw = Path(path)
    return raw.resolve() if raw.is_absolute() else (build_path_context().user_data_root / raw).resolve()


def _configured_workspace_root() -> str | None:
    env_value = os.getenv("WORKSPACE_ROOT", "").strip()
    if env_value:
        return env_value
    configured = str(settings.workspace_root or "").strip()
    return configured if configured and configured != "workspace_store" else None
