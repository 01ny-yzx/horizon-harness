"""Browser Tool routes scoped by workspace."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, BrowserClickRequest, BrowserScreenshotRequest, BrowserUrlRequest, WorkspaceRequest
from core.browser import BrowserManager
from core.rate_limit import RateLimiter


router = APIRouter(prefix="/browser", tags=["browser"])


@router.post("/status", response_model=ApiResponse)
def browser_status(request: WorkspaceRequest) -> ApiResponse:
    try:
        context = run_in_workspace(request.user_id, request.project_id)
        quota = RateLimiter().check_and_increment(context.user_id, "browser")
        if not quota.get("allowed"):
            raise HTTPException(status_code=429, detail={"error": "Daily browser limit exceeded.", "rate_limit": quota})
        result = BrowserManager().get_status()
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        data["rate_limit"] = quota
        return ApiResponse(success=bool(result.get("success")), data=data, error=result.get("error"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))


@router.post("/extract-text", response_model=ApiResponse)
def browser_extract_text(request: BrowserUrlRequest) -> ApiResponse:
    return _run_browser_action(request, lambda manager: manager.extract_text(request.url))


@router.post("/screenshot", response_model=ApiResponse)
def browser_screenshot(request: BrowserScreenshotRequest) -> ApiResponse:
    return _run_browser_action(request, lambda manager: manager.screenshot(request.url, full_page=request.full_page))


@router.post("/list-links", response_model=ApiResponse)
def browser_list_links(request: BrowserUrlRequest) -> ApiResponse:
    return _run_browser_action(request, lambda manager: manager.list_links(request.url))


@router.post("/click-and-extract", response_model=ApiResponse)
def browser_click_and_extract(request: BrowserClickRequest) -> ApiResponse:
    return _run_browser_action(
        request,
        lambda manager: manager.click_and_extract(request.url, selector=request.selector, text=request.text),
    )


def _run_browser_action(request: WorkspaceRequest, action: Callable[[BrowserManager], dict]) -> ApiResponse:
    try:
        context = run_in_workspace(request.user_id, request.project_id)
        quota = RateLimiter().check_and_increment(context.user_id, "browser")
        if not quota.get("allowed"):
            raise HTTPException(status_code=429, detail={"error": "Daily browser limit exceeded.", "rate_limit": quota})
        result = action(BrowserManager())
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        data["rate_limit"] = quota
        return ApiResponse(success=bool(result.get("success")), data=data, error=result.get("error"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, error=safe_error(exc))
