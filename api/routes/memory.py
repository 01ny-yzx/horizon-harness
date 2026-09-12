"""Memory routes scoped by user/project workspace."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import run_in_workspace, safe_error
from api.schemas import (
    ApiResponse,
    ClearMemoryTypeRequest,
    DeleteMemoryReferenceRequest,
    DeleteProjectInstructionRequest,
    DeleteUserPreferenceRequest,
    MemoryListRequest,
    RememberPreferenceRequest,
)
from tools.memory_tools import (
    clear_memory_type,
    delete_memory_reference,
    delete_project_instruction,
    delete_user_preference,
    list_memories,
    remember_user_preference,
)


router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/list", response_model=ApiResponse)
def memory_list(request: MemoryListRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = list_memories(memory_type=request.memory_type)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/reference/delete", response_model=ApiResponse)
def memory_reference_delete(request: DeleteMemoryReferenceRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = delete_memory_reference(reference_id=request.reference_id)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/preference/delete", response_model=ApiResponse)
def memory_preference_delete(request: DeleteUserPreferenceRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = delete_user_preference(key=request.key)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/project-instruction/delete", response_model=ApiResponse)
def memory_project_instruction_delete(request: DeleteProjectInstructionRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = delete_project_instruction(content=request.content)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/type/clear", response_model=ApiResponse)
def memory_type_clear(request: ClearMemoryTypeRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = clear_memory_type(memory_type=request.memory_type)
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
