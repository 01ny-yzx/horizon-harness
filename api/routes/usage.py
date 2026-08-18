"""Usage status API routes."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, WorkspaceRequest
from tools.usage_tools import get_usage_status


router = APIRouter(prefix="/usage", tags=["usage"])


@router.post("/status", response_model=ApiResponse)
def usage_status(request: WorkspaceRequest) -> ApiResponse:
    try:
        context = run_in_workspace(request.user_id, request.project_id)
        result = get_usage_status(user_id=context.user_id)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))
