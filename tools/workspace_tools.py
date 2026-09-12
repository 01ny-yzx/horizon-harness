"""Tools for inspecting and switching process-local workspaces."""

from __future__ import annotations

from typing import Any

from core.document_store import DocumentStore
from core.persistent_memory import PersistentMemory
from core.vector_store import VectorStore
from core.workspace import WorkspaceManager
from core.workspace_runtime import get_current_workspace, set_current_workspace


def get_workspace_status() -> dict[str, Any]:
    """Return safe counts and paths for the current workspace."""

    workspace = get_current_workspace()
    memory = PersistentMemory(
        database_path=workspace.database_path,
        user_id=workspace.user_id,
        project_id=workspace.project_id,
    )
    memory_health = memory.load_all()
    document_store = DocumentStore(document_dir=workspace.document_dir)
    documents = document_store.list_documents().get("data", {})
    chunks = document_store.list_chunks(limit=1).get("data", {})
    vectors = VectorStore(vector_dir=workspace.vector_dir).get_status().get("data", {})
    return {
        "success": True,
        "data": {
            "user_id": workspace.user_id,
            "project_id": workspace.project_id,
            "workspace_id": workspace.workspace_id,
            "workspace_dir": str(workspace.workspace_dir),
            "database_path": str(workspace.database_path),
            "document_dir_exists": workspace.document_dir.exists(),
            "vector_dir_exists": workspace.vector_dir.exists(),
            "documents_count": int(documents.get("documents_count", 0) or 0) if isinstance(documents, dict) else 0,
            "chunks_count": int(chunks.get("chunks_count", 0) or 0) if isinstance(chunks, dict) else 0,
            "vectors_count": int(vectors.get("vectors_count", 0) or 0) if isinstance(vectors, dict) else 0,
            "memory_counts": memory.get_counts() if memory_health.get("success") else {},
            "persistent_memory_healthy": memory_health.get("success") is True,
        },
    }


def list_workspaces() -> dict[str, Any]:
    """List available user/project workspaces."""

    manager = WorkspaceManager()
    workspaces = manager.list_workspaces()
    return {"success": True, "data": {"count": len(workspaces), "workspaces": workspaces}}


def switch_workspace(user_id: str, project_id: str) -> dict[str, Any]:
    """Switch the current process to another user/project workspace."""

    manager = WorkspaceManager()
    context = manager.get_context(user_id=user_id, project_id=project_id)
    manager.ensure_workspace(context)
    set_current_workspace(context)
    status = get_workspace_status()
    if status.get("success") and isinstance(status.get("data"), dict):
        status["data"]["note"] = "switch_workspace only affects the current process."
    return status


WORKSPACE_TOOLS = {
    "get_workspace_status": get_workspace_status,
    "list_workspaces": list_workspaces,
    "switch_workspace": switch_workspace,
}


WORKSPACE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_workspace_status",
            "description": "Inspect the current user/project workspace and safe store counts.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspaces",
            "description": "List user/project workspaces under workspace_store.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_workspace",
            "description": "Switch the current process to a sanitized user/project workspace. Does not delete data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Safe user id: letters, digits, underscore, hyphen."},
                    "project_id": {"type": "string", "description": "Safe project id: letters, digits, underscore, hyphen."},
                },
                "required": ["user_id", "project_id"],
            },
        },
    },
]
