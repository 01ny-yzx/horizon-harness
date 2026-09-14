"""Chat route for running the Agent in a request workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import create_session_for_workspace, run_in_workspace, safe_error
from api.schemas import ChatRequest, ChatResponse
from core.rate_limit import RateLimiter
from core.session import SessionService


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Durably admit one Session prompt and return its admission identity."""

    try:
        context = run_in_workspace(request.user_id, request.project_id)
        quota = RateLimiter().check_and_increment(context.user_id, "chat")
        if not quota.get("allowed"):
            raise HTTPException(status_code=429, detail={"error": "Daily chat limit exceeded.", "rate_limit": quota})
        workspace, session = create_session_for_workspace(
            context.user_id,
            context.project_id,
            session_id=request.session_id,
        )
        admitted = SessionService(database_path=workspace.database_path).prompt(
            session.id,
            request.message,
            delivery=request.delivery,
            message_id=request.message_id,
            resume=request.resume,
        )
        return ChatResponse(
            success=True,
            answer=None,
            user_id=context.user_id,
            project_id=context.project_id,
            session_id=admitted.session_id,
            message_id=admitted.id,
            admitted_seq=admitted.admitted_seq,
            delivery=admitted.delivery,
            trace_id=None,
            remaining=int(quota.get("remaining", 0)),
            rate_limit=quota,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return ChatResponse(
            success=False,
            answer=None,
            user_id=request.user_id,
            project_id=request.project_id,
            trace_id=None,
            error=safe_error(exc),
        )
