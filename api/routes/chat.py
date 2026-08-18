"""Chat route for running the Agent in a request workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import build_agent_for_workspace, run_in_workspace, safe_error
from api.schemas import ChatRequest, ChatResponse
from core.rate_limit import RateLimiter


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Run one Agent turn for a user/project workspace."""

    try:
        context = run_in_workspace(request.user_id, request.project_id)
        quota = RateLimiter().check_and_increment(context.user_id, "chat")
        if not quota.get("allowed"):
            raise HTTPException(status_code=429, detail={"error": "Daily chat limit exceeded.", "rate_limit": quota})
        agent = build_agent_for_workspace(context.user_id, context.project_id)
        answer = agent.run(request.message, user_id=context.user_id, project_id=context.project_id)
        trace_id = None
        if request.debug:
            trace_id = agent.last_trace_id or None
        return ChatResponse(
            success=True,
            answer=answer,
            user_id=context.user_id,
            project_id=context.project_id,
            trace_id=trace_id,
            remaining=int(quota.get("remaining", 0)),
            rate_limit=quota,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return ChatResponse(
            success=False,
            answer="",
            user_id=request.user_id,
            project_id=request.project_id,
            trace_id=None,
            error=safe_error(exc),
        )
