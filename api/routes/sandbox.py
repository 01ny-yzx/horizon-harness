"""Sandbox routes scoped by workspace."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, WorkspaceRequest
from core.sandbox import SandboxManager


router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.post("/status", response_model=ApiResponse)
def sandbox_status(request: WorkspaceRequest) -> ApiResponse:
    try:
        context = run_in_workspace(request.user_id, request.project_id)
        result = SandboxManager().status(context.user_id, context.project_id)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))
