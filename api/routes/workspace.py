"""Workspace routes for Service API v1."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, WorkspaceRequest, WorkspaceStatusResponse
from tools.workspace_tools import get_workspace_status, list_workspaces


router = APIRouter(tags=["workspace"])


@router.get("/workspaces", response_model=ApiResponse)
def workspaces() -> ApiResponse:
    """List available workspaces."""

    result = list_workspaces()
    return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))


@router.post("/workspace/status", response_model=WorkspaceStatusResponse)
def workspace_status(request: WorkspaceRequest) -> WorkspaceStatusResponse:
    """Return compact status for one workspace."""

    try:
        context = run_in_workspace(request.user_id, request.project_id)
        result = get_workspace_status()
        data = result.get("data", {}) if result.get("success") else {}
        return WorkspaceStatusResponse(
            success=bool(result.get("success")),
            user_id=context.user_id,
            project_id=context.project_id,
            workspace_id=context.workspace_id,
            memory_counts=data.get("memory_counts", {}) if isinstance(data, dict) else {},
            documents_count=int(data.get("documents_count", 0) or 0) if isinstance(data, dict) else 0,
            chunks_count=int(data.get("chunks_count", 0) or 0) if isinstance(data, dict) else 0,
            vectors_count=int(data.get("vectors_count", 0) or 0) if isinstance(data, dict) else 0,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=safe_error(exc)) from exc


@router.post("/workspace/switch", response_model=ApiResponse)
def workspace_switch(request: WorkspaceRequest) -> ApiResponse:
    """Confirm a workspace is ready.

    The API is stateless: each request must include user_id/project_id.
    """

    try:
        context = run_in_workspace(request.user_id, request.project_id)
        return ApiResponse(
            success=True,
            data={
                "user_id": context.user_id,
                "project_id": context.project_id,
                "workspace_id": context.workspace_id,
                "workspace_ready": True,
                "note": "Service API requests are stateless; pass user_id/project_id on every request.",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))
