"""Memory routes scoped by user/project workspace."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, ForgetMemoryRequest, MemoryListRequest, RememberPreferenceRequest
from tools.memory_tools import forget_memory, list_memories, remember_user_preference


router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/list", response_model=ApiResponse)
def memory_list(request: MemoryListRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = list_memories(memory_type=request.memory_type)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/forget", response_model=ApiResponse)
def memory_forget(request: ForgetMemoryRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = forget_memory(memory_type=request.memory_type, keyword=request.keyword)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/remember/preference", response_model=ApiResponse)
def memory_remember_preference(request: RememberPreferenceRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = remember_user_preference(key=request.key, value=request.value)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))
