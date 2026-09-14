"""Durable Session reopen and process-local execution controls."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.deps import run_in_workspace, safe_error
from api.schemas import ApiResponse, WorkspaceRequest
from config.settings import settings
from core.session import SessionInfo, SessionService
from core.session_store import SessionNotFoundError


router = APIRouter(prefix="/session", tags=["session"])


def _service(user_id: str, project_id: str) -> tuple[SessionService, str, str]:
    workspace = run_in_workspace(user_id, project_id)
    return (
        SessionService(database_path=workspace.database_path),
        workspace.user_id,
        workspace.project_id,
    )


def _owned(
    service: SessionService,
    session_id: str,
    user_id: str,
    project_id: str,
) -> SessionInfo:
    session = service.get(session_id)
    if session.user_id != user_id or session.project_id != project_id:
        raise HTTPException(status_code=403, detail="Session workspace identity mismatch")
    return session


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, SessionNotFoundError):
        raise HTTPException(status_code=404, detail="Session not found") from exc
    raise HTTPException(status_code=400, detail=safe_error(exc)) from exc


@router.get("", response_model=ApiResponse)
def list_sessions(
    user_id: str = settings.default_user_id,
    project_id: str = settings.default_project_id,
) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(user_id, project_id)
        sessions = service.list(user_id=safe_user, project_id=safe_project)
        return ApiResponse(success=True, data=[item.to_event_data() for item in sessions])
    except Exception as exc:  # noqa: BLE001
        _raise_api_error(exc)


@router.get("/active", response_model=ApiResponse)
def active_sessions(
    user_id: str = settings.default_user_id,
    project_id: str = settings.default_project_id,
) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(user_id, project_id)
        owned = {
            item.id
            for item in service.list(user_id=safe_user, project_id=safe_project)
        }
        return ApiResponse(success=True, data=sorted(service.active() & owned))
    except Exception as exc:  # noqa: BLE001
        _raise_api_error(exc)


@router.get("/{session_id}", response_model=ApiResponse)
def get_session(
    session_id: str,
    user_id: str = settings.default_user_id,
    project_id: str = settings.default_project_id,
) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(user_id, project_id)
        session = _owned(service, session_id, safe_user, safe_project)
        return ApiResponse(success=True, data=session.to_event_data())
    except Exception as exc:  # noqa: BLE001
        _raise_api_error(exc)


@router.get("/{session_id}/context", response_model=ApiResponse)
def session_context(
    session_id: str,
    user_id: str = settings.default_user_id,
    project_id: str = settings.default_project_id,
) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(user_id, project_id)
        _owned(service, session_id, safe_user, safe_project)
        return ApiResponse(
            success=True,
            data=[message.to_dict() for message in service.context(session_id)],
        )
    except Exception as exc:  # noqa: BLE001
        _raise_api_error(exc)


@router.get("/{session_id}/history", response_model=ApiResponse)
def session_history(
    session_id: str,
    user_id: str = settings.default_user_id,
    project_id: str = settings.default_project_id,
    after: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(user_id, project_id)
        _owned(service, session_id, safe_user, safe_project)
        page = service.history(session_id, after=after, limit=limit)
        return ApiResponse(
            success=True,
            data={
                "events": [
                    {
                        "id": event.id,
                        "aggregate_id": event.aggregate_id,
                        "seq": event.seq,
                        "type": event.type,
                        "data": event.data,
                        "time_created": event.time_created,
                    }
                    for event in page.events
                ],
                "has_more": page.has_more,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _raise_api_error(exc)


@router.post("/{session_id}/resume", response_model=ApiResponse)
def resume_session(session_id: str, request: WorkspaceRequest) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(request.user_id, request.project_id)
        _owned(service, session_id, safe_user, safe_project)
        service.resume(session_id)
        return ApiResponse(success=True, data={"session_id": session_id, "resumed": True})
    except Exception as exc:  # noqa: BLE001
        _raise_api_error(exc)


@router.post("/{session_id}/interrupt", response_model=ApiResponse)
def interrupt_session(session_id: str, request: WorkspaceRequest) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(request.user_id, request.project_id)
        _owned(service, session_id, safe_user, safe_project)
        service.interrupt(session_id)
        return ApiResponse(success=True, data={"session_id": session_id, "interrupted": True})
    except Exception as exc:  # noqa: BLE001
        _raise_api_error(exc)


__all__ = ["router"]
