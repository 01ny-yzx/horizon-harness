"""Minimal protocol validation for ordinary Agent prose."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.tool_boundary import detect_raw_tool_text


@dataclass(frozen=True)
class AgentProseValidation:
    accepted: bool
    content: str
    reject_reason: str = ""
    content_chars: int = 0
    raw_tool_text: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def validate_agent_prose_candidate(content: str) -> AgentProseValidation:
    """Accept any non-empty prose that does not encode a raw tool protocol."""

    candidate = str(content or "")
    stripped = candidate.strip()
    if not stripped:
        return AgentProseValidation(
            accepted=False,
            content="",
            reject_reason="empty_content",
            content_chars=0,
        )
    raw_tool_text = detect_raw_tool_text(candidate)
    if raw_tool_text.has_raw_tool_text:
        return AgentProseValidation(
            accepted=False,
            content="",
            reject_reason="raw_tool_text_in_agent_prose",
            content_chars=len(candidate),
            raw_tool_text=True,
            metadata={
                "matched_tool_names": list(raw_tool_text.matched_tool_names),
                "matched_patterns": list(raw_tool_text.matched_patterns),
            },
        )
    return AgentProseValidation(
        accepted=True,
        content=candidate,
        content_chars=len(candidate),
    )


__all__ = ["AgentProseValidation", "validate_agent_prose_candidate"]
