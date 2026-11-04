"""Durable Session reopen and process-local execution controls."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from api.deps import create_session_for_workspace, run_in_workspace, safe_error
from api.schemas import ApiResponse, WorkspaceRequest
from config.settings import settings
from core.session import SessionInfo, SessionService
from core.session_store import SessionNotFoundError


router = APIRouter(prefix="/session", tags=["session"])


class ModelSelectionRequest(WorkspaceRequest):
    connection_id: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=300)


@router.get('/{session_id}/model', response_model=ApiResponse)
def get_session_model(session_id: str, user_id: str = settings.default_user_id,
                      project_id: str = settings.default_project_id):
    from core.session_model import SessionModelSelection
    try:
        service, user, project = _service(user_id, project_id)
        _owned(service, session_id, user, project)
        return ApiResponse(success=True, data=SessionModelSelection(service.database).get(session_id))
    except Exception as exc:
        _raise_api_error(exc)


@router.put('/{session_id}/model', response_model=ApiResponse)
def select_session_model(session_id: str, request: ModelSelectionRequest):
    from core.session_model import SessionModelSelection
    from core.session_run_coordinator import SessionBusyError
    try:
        service, user, project = _service(request.user_id, request.project_id)
        _owned(service, session_id, user, project)
        selected = service._execution().configure_idle(session_id, lambda: SessionModelSelection(service.database).select(
            session_id, request.connection_id, request.model))
        return ApiResponse(success=True, data=selected)
    except SessionBusyError:
        raise HTTPException(409, '任务正在执行，请先中断再切换模型') from None
    except Exception as exc:
        _raise_api_error(exc)


@router.post("", response_model=ApiResponse)
def create_session(request: WorkspaceRequest) -> ApiResponse:
    """Create an empty conversation without invoking a model."""
    try:
        _, session = create_session_for_workspace(request.user_id, request.project_id)
        return ApiResponse(success=True, data=session.to_event_data())
    except Exception as exc:
        _raise_api_error(exc)


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


@router.delete("/{session_id}", response_model=ApiResponse)
def delete_session(session_id: str, user_id: str = settings.default_user_id,
                   project_id: str = settings.default_project_id) -> ApiResponse:
    from core.session_run_coordinator import SessionBusyError
    try:
        service, safe_user, safe_project = _service(user_id, project_id)
        _owned(service, session_id, safe_user, safe_project)
        service.delete(session_id)
        return ApiResponse(success=True, data={"id": session_id, "deleted": True})
    except SessionBusyError:
        raise HTTPException(409, "请先中断该会话的任务，再删除会话") from None
    except Exception as exc:
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


@router.get("/{session_id}/transcript", response_model=ApiResponse)
def session_transcript(
    session_id: str,
    user_id: str = settings.default_user_id,
    project_id: str = settings.default_project_id,
) -> ApiResponse:
    try:
        service, safe_user, safe_project = _service(user_id, project_id)
        _owned(service, session_id, safe_user, safe_project)
        return ApiResponse(
            success=True,
            data=[message.to_dict() for message in service.transcript(session_id)],
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


@router.get("/{session_id}/events")
async def session_events(
    session_id: str,
    request: Request,
    after: int = 0,
    tail: bool = False,
    user_id: str = settings.default_user_id,
    project_id: str = settings.default_project_id,
) -> StreamingResponse:
    """SSE transport for live output; reconnecting never resumes execution."""
    from core.session_live import session_live

    try:
        service, safe_user, safe_project = await asyncio.to_thread(_service, user_id, project_id)
        await asyncio.to_thread(_owned, service, session_id, safe_user, safe_project)
        if after < 0:
            raise HTTPException(status_code=422, detail="after must be non-negative")
    except Exception as exc:
        _raise_api_error(exc)

    def frame(kind, data, seq=None):
        prefix = "" if seq is None else f"id: {seq}\n"
        return f"{prefix}event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        cursor = session_live.read(session_id).cursor if tail else after
        heartbeat = time.monotonic()
        yield frame("ready", {"session_id": session_id})
        if tail:
            messages = await asyncio.to_thread(service.transcript, session_id)
            yield frame("session.snapshot", {
                "messages": [message.to_dict() for message in messages],
                "active": session_id in service.active(),
            }, cursor)
        while not await request.is_disconnected():
            batch = session_live.read(session_id, cursor)
            if batch.reset:
                # The journal is bounded. Replace provisional output with durable
                # context instead of presenting an incomplete replay as complete.
                messages = await asyncio.to_thread(service.transcript, session_id)
                yield frame("session.snapshot", {
                    "messages": [message.to_dict() for message in messages],
                    "active": session_id in service.active(),
                }, batch.cursor)
                cursor = batch.cursor
            else:
                for event in batch.events:
                    yield frame(event.type, event.data, event.seq)
                    cursor = event.seq
            if time.monotonic() - heartbeat > 15:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(.04)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


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


def _pending_payload(inputs, item):
    return {
        'message_id': item.id, 'prompt': item.prompt, 'delivery': item.delivery,
        'admitted_seq': item.admitted_seq, 'time_created': item.time_created,
        'access_mode': inputs.access_mode(item.id),
    }


@router.get('/{session_id}/pending', response_model=ApiResponse)
def pending_session_inputs(session_id: str, user_id: str = settings.default_user_id,
                           project_id: str = settings.default_project_id):
    try:
        service, user, project = _service(user_id, project_id)
        _owned(service, session_id, user, project)
        items = service.inputs.pending_all(session_id, unclaimed=True)
        return ApiResponse(success=True, data={'session_id': session_id,
            'pending': [_pending_payload(service.inputs, item) for item in items]})
    except Exception as exc:
        _raise_queue_error(exc)


@router.post('/{session_id}/pending/{message_id}/steer', response_model=ApiResponse)
def steer_session_input(session_id: str, message_id: str, request: WorkspaceRequest):
    try:
        service, user, project = _service(request.user_id, request.project_id)
        _owned(service, session_id, user, project)
        service._execution().configure_active(session_id,
            lambda: service.inputs.steer(session_id, message_id))
        return ApiResponse(success=True, data={'session_id': session_id,
            'message_id': message_id, 'delivery': 'steer', 'steered': True})
    except Exception as exc:
        _raise_queue_error(exc)


@router.delete('/{session_id}/pending/{message_id}', response_model=ApiResponse)
def recall_session_input(session_id: str, message_id: str,
                         user_id: str = settings.default_user_id,
                         project_id: str = settings.default_project_id):
    try:
        service, user, project = _service(user_id, project_id)
        _owned(service, session_id, user, project)
        item = service.inputs.recall(session_id, message_id)
        return ApiResponse(success=True, data={'session_id': session_id,
            'message_id': message_id, 'prompt': item.prompt,
            'access_mode': service.inputs.access_mode(item.id), 'removed': True})
    except Exception as exc:
        _raise_queue_error(exc)


def _raise_queue_error(exc):
    from core.session_input import SessionInputLifecycleConflict
    from core.session_run_coordinator import SessionInactiveError
    if isinstance(exc, SessionInactiveError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, SessionInputLifecycleConflict):
        raise HTTPException(status_code=409, detail='Queue item is no longer pending') from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail='Pending input not found') from exc
    _raise_api_error(exc)
