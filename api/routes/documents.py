"""Document and chunk routes scoped by workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, ChunkSearchRequest, DirectoryLoadRequest, DocumentLoadRequest, WorkspaceRequest
from core.rate_limit import RateLimiter
from tools.chunk_tools import search_document_chunks
from tools.document_tools import list_documents, load_document, load_documents_from_directory


router = APIRouter(tags=["documents"])


@router.post("/documents/load", response_model=ApiResponse)
def documents_load(request: DocumentLoadRequest) -> ApiResponse:
    try:
        context = run_in_workspace(request.user_id, request.project_id)
        quota = RateLimiter().check_and_increment(context.user_id, "document_load")
        if not quota.get("allowed"):
            raise HTTPException(status_code=429, detail={"error": "Daily document load limit exceeded.", "rate_limit": quota})
        result = load_document(path=request.path, create_chunks=request.create_chunks)
        data = _trim_data(result.get("data"))
        if isinstance(data, dict):
            data["rate_limit"] = quota
        return ApiResponse(success=bool(result.get("success")), data=data, error=result.get("error"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/documents/load-directory", response_model=ApiResponse)
def documents_load_directory(request: DirectoryLoadRequest) -> ApiResponse:
    try:
        context = run_in_workspace(request.user_id, request.project_id)
        quota = RateLimiter().check_and_increment(context.user_id, "document_load")
        if not quota.get("allowed"):
            raise HTTPException(status_code=429, detail={"error": "Daily document load limit exceeded.", "rate_limit": quota})
        result = load_documents_from_directory(
            path=request.path,
            recursive=request.recursive,
            max_files=request.max_files,
            create_chunks=request.create_chunks,
        )
        data = _trim_data(result.get("data"))
        if isinstance(data, dict):
            data["rate_limit"] = quota
        return ApiResponse(success=bool(result.get("success")), data=data, error=result.get("error"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/documents/list", response_model=ApiResponse)
def documents_list(request: WorkspaceRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = list_documents()
        return ApiResponse(success=bool(result.get("success")), data=_trim_data(result.get("data")), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/chunks/search", response_model=ApiResponse)
def chunks_search(request: ChunkSearchRequest) -> ApiResponse:
    try:
        run_in_workspace(request.user_id, request.project_id)
        result = search_document_chunks(keyword=request.keyword, document_id=request.document_id, limit=request.limit)
        return ApiResponse(success=bool(result.get("success")), data=_trim_data(result.get("data")), error=result.get("error"))
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


def _trim_data(data: object) -> object:
    """Keep API document responses bounded."""

    if not isinstance(data, dict):
        return data
    trimmed = dict(data)
    if "text_preview" in trimmed:
        trimmed["text_preview"] = str(trimmed.get("text_preview", ""))[:1200]
    if isinstance(trimmed.get("documents"), list):
        trimmed["documents"] = trimmed["documents"][:50]
    if isinstance(trimmed.get("chunks"), list):
        trimmed["chunks"] = trimmed["chunks"][:20]
    return trimmed
