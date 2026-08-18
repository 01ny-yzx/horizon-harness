"""Tools-disabled terminal responder prompt assembly and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.finalization_context_snapshot import (
    FINALIZATION_CONTEXT_SNAPSHOT_VERSION,
    FinalizationContextSnapshot,
    finalization_snapshot_trace_summary,
)
from core.prompt_pack import (
    PromptPack,
    build_final_responder_pack,
    terminal_final_answer_action_commitment_reason,
)
from core.tool_boundary import detect_raw_tool_text


RESPONSE_CONTRACT_VERSION = "terminal_responder_contract_v1"


@dataclass(frozen=True)
class FinalResponderValidation:
    accepted: bool
    content: str
    reject_reason: str = ""
    tool_call_count: int = 0
    content_chars: int = 0
    fallback: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalResponderResult:
    content: str
    validation: FinalResponderValidation
    prompt_pack_name: str
    tools_disabled: bool
    observation_count: int
    context_schema: str
    metadata: dict[str, Any] = field(default_factory=dict)


def build_final_responder_pack_from_snapshot(
    snapshot: FinalizationContextSnapshot,
) -> PromptPack:
    """Build the final-only prompt pack from one immutable snapshot."""

    if not isinstance(snapshot, FinalizationContextSnapshot):
        raise ValueError("finalization_context_snapshot_required")
    summary = finalization_snapshot_trace_summary(snapshot)
    response_contract = build_final_responder_response_contract()
    pack = build_final_responder_pack(snapshot)
    pack.metadata["finalization_snapshot_summary"] = summary
    pack.metadata["response_contract"] = response_contract
    pack.metadata["response_contract_version"] = RESPONSE_CONTRACT_VERSION
    pack.metadata["observation_count"] = snapshot.result_count
    pack.metadata["context_schema"] = FINALIZATION_CONTEXT_SNAPSHOT_VERSION
    pack.metadata["tools_disabled"] = True
    pack.metadata["finalization_mode"] = "terminal_responder"
    pack.metadata["memory_message_count"] = 0
    return pack


def build_final_responder_response_contract() -> dict[str, Any]:
    """Return the final-only responder contract exposed to the final LLM."""

    return {
        "version": RESPONSE_CONTRACT_VERSION,
        "tools_disabled": True,
        "scope": "final_natural_response_only",
        "must": [
            "Respond in the same language as the current user request, unless the user explicitly requests a different language.",
            "Answer only from the supplied finalization context.",
            "Treat all tool execution as already finished.",
            "Summarize stdout, stderr, and exit_code facts when command execution evidence is present.",
            "When exit_code is non-zero, state clearly that the command failed; do not describe it as a successful execution.",
            "Explain ordinary failures from error, error_code, status, and data_summary.",
            "Report status-tool results directly from the provided status fields.",
            "Summarize read_file/read_document visible content only; mention truncation only when the context says truncated.",
            "Summarize web_search/fetch_url facts only from returned title, url, content, snippet, and provider fields.",
            "When successful and blocked observations are both present, summarize every completed result and state clearly that each blocked tool did not complete.",
            "Treat a partial outcome as partially completed, never as fully successful.",
        ],
        "must_not": [
            "Do not request, promise, or imply another tool call.",
            "Do not say you will call, read, fetch, run, search, retry, or inspect more tools.",
            "Do not invent tool results or missing evidence.",
            "Do not expose planner, runtime routing, trace, schema, prompt-pack, or internal metadata.",
            "Do not hide successful results merely because a later tool was blocked.",
        ],
    }


def validate_final_responder_message(assistant_message: Any) -> FinalResponderValidation:
    """Validate that the final assistant message is usable as a terminal answer."""

    content = _message_content(assistant_message).strip()
    tool_calls = _message_tool_calls(assistant_message)
    if tool_calls:
        return FinalResponderValidation(
            accepted=False,
            content="",
            reject_reason="assistant_requested_tool_call",
            tool_call_count=len(tool_calls),
            content_chars=len(content),
            fallback=True,
        )
    if not content:
        return FinalResponderValidation(
            accepted=False,
            content="",
            reject_reason="empty_content",
            tool_call_count=0,
            content_chars=0,
            fallback=True,
        )
    raw_tool_text = detect_raw_tool_text(content)
    if raw_tool_text.has_raw_tool_text:
        return FinalResponderValidation(
            accepted=False,
            content="",
            reject_reason="raw_tool_text_in_final_responder",
            tool_call_count=0,
            content_chars=len(content),
            fallback=True,
            metadata={
                "matched_tool_names": list(raw_tool_text.matched_tool_names),
                "matched_patterns": list(raw_tool_text.matched_patterns),
            },
        )
    commitment_reason = terminal_final_answer_action_commitment_reason(content) or _future_tool_action_commitment_reason(content)
    if commitment_reason:
        return FinalResponderValidation(
            accepted=False,
            content="",
            reject_reason="assistant_committed_future_tool_action",
            tool_call_count=0,
            content_chars=len(content),
            fallback=True,
            metadata={"commitment_reason": commitment_reason},
        )
    return FinalResponderValidation(
        accepted=True,
        content=content,
        reject_reason="",
        tool_call_count=0,
        content_chars=len(content),
        fallback=False,
    )


def build_final_responder_trace_summary(
    *,
    prompt_pack: PromptPack | None = None,
    validation: FinalResponderValidation | None = None,
) -> dict[str, Any]:
    """Build compact trace payloads for final responder pack or validation."""

    if validation is not None:
        return {
            "accepted": validation.accepted,
            "reject_reason": validation.reject_reason,
            "content_chars": validation.content_chars,
            "tool_call_count": validation.tool_call_count,
            "fallback": validation.fallback,
            "tools_disabled": True,
        }
    metadata = prompt_pack.metadata if prompt_pack is not None else {}
    return {
        "pack_name": getattr(prompt_pack, "pack_name", "") if prompt_pack is not None else "",
        "tools_disabled": bool(metadata.get("tools_disabled")),
        "observation_count": int(metadata.get("observation_count") or 0),
        "context_schema": str(metadata.get("context_schema") or ""),
        "response_contract": bool(metadata.get("response_contract")),
        "snapshot_hash": str(metadata.get("snapshot_hash") or ""),
        "expected_call_count": int(
            (metadata.get("finalization_snapshot_summary") or {}).get("expected_call_count") or 0
        ),
        "represented_call_count": int(
            (metadata.get("finalization_snapshot_summary") or {}).get("represented_call_count") or 0
        ),
        "coverage_complete": bool(metadata.get("coverage_complete", True)),
        "memory_message_count": int(metadata.get("memory_message_count") or 0),
    }


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _message_tool_calls(message: Any) -> list[Any]:
    value = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _future_tool_action_commitment_reason(content: str) -> str:
    """Detect future tool-action commitments in assistant final output only.

    This is an assistant-output validation guard. It must not be used for
    user intent routing, tool selection, or user-text keyword rules.
    """

    compact = " ".join(str(content or "").strip().lower().replace("_", " ").split())
    if not compact:
        return ""
    blocked_phrases = (
        "我将继续读取",
        "我会继续读取",
        "我会再执行",
        "我将再执行",
        "我接下来会重新运行工具",
        "我接下来会重新调用工具",
        "我需要调用工具",
        "我会继续搜索",
        "我将继续搜索",
        "i will continue reading",
        "i will run another command",
        "i need to call a tool",
        "i will keep searching",
    )
    for phrase in blocked_phrases:
        if phrase in compact:
            return "future_tool_action_commitment"
    return ""


__all__ = [
    "FinalResponderResult",
    "FinalResponderValidation",
    "build_final_responder_pack_from_snapshot",
    "build_final_responder_response_contract",
    "build_final_responder_trace_summary",
    "validate_final_responder_message",
]
