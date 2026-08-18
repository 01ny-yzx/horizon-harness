"""Cache inspection API routes."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import safe_error
from api.schemas import ApiResponse
from tools.cache_tools import clear_cache, get_cache_status


router = APIRouter(prefix="/cache", tags=["cache"])


@router.post("/status", response_model=ApiResponse)
def cache_status() -> ApiResponse:
    try:
        result = get_cache_status()
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/clear", response_model=ApiResponse)
def cache_clear(cache_name: str = "all") -> ApiResponse:
    try:
        result = clear_cache(cache_name=cache_name)
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))
