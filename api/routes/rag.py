"""RAG routes scoped by workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, RagQueryRequest, WorkspaceRequest
from core.rate_limit import RateLimiter
from tools.rag_tools import get_rag_status, rag_query


router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/query", response_model=ApiResponse)
def rag_query_route(request: RagQueryRequest) -> ApiResponse:
    try:
        context = run_in_workspace(request.user_id, request.project_id)
        quota = RateLimiter().check_and_increment(context.user_id, "rag")
        if not quota.get("allowed"):
            raise HTTPException(status_code=429, detail={"error": "Daily RAG limit exceeded.", "rate_limit": quota})
        result = rag_query(query=request.query, mode=request.mode, top_k=request.top_k)
        data = result.get("data")
        if isinstance(data, dict):
            data.pop("embedding", None)
            data["rate_limit"] = quota
        return ApiResponse(success=bool(result.get("success")), data=data, error=result.get("error"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/status", response_model=ApiResponse)
def rag_status(request: WorkspaceRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = get_rag_status()
        return ApiResponse(success=bool(result.get("success")), data=result.get("data"), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))
