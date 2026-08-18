"""Shared API dependencies and workspace helpers."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, Request

from config.settings import settings
from core.auth import validate_api_key
from core.agent_factory import build_agent_for_workspace as _build_agent_for_workspace
from core.agent_factory import get_workspace_context, run_in_workspace
from core.llm import build_default_llm
from core.loop import AgentLoop
from core.mcp_runtime_manager import get_mcp_runtime_manager
from core.workspace import WorkspaceContext


def build_agent_for_workspace(user_id: str | None = None, project_id: str | None = None) -> AgentLoop:
    """Build an AgentLoop bound to one workspace."""

    return _build_agent_for_workspace(
        user_id=user_id,
        project_id=project_id,
        llm_factory=build_default_llm,
        runtime_manager_factory=get_mcp_runtime_manager,
    )


def safe_error(exc: Exception) -> str:
    """Return a compact error string without secrets."""

    text = str(exc)
    for marker in (
        "DEEPSEEK" + "_API" + "_KEY=",
        "LLM" + "_API" + "_KEY=",
        "TAVILY" + "_API" + "_KEY=",
        "EMBEDDING" + "_API" + "_KEY=",
        "API" + "_KEY=",
    ):
        text = re.sub(re.escape(marker) + r"[^&\s,;]+", marker + "[REDACTED]", text)
    return text[:1000]


def require_api_key(request: Request) -> None:
    """FastAPI dependency enforcing lightweight API key auth."""

    api_key = request.headers.get(settings.api_key_header)
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid API key.")
