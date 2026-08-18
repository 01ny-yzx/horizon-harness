"""Same-call duplicate protection keyed by structured ToolCall identity."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from core.tool_call_grants import stable_tool_arguments_fingerprint


TOOL_CALL_EXECUTION_CACHE_KEY = "tool_call_execution_cache"


@dataclass(frozen=True)
class ToolCallReplayDecision:
    replay: bool
    call_id: str
    cached_observation: dict[str, Any] | None = None
    reason: str = ""


def find_completed_tool_call(task_state: Any, envelope: Any) -> ToolCallReplayDecision:
    """Return a prior result only for the exact same structured call identity."""

    call_id = _call_id(envelope)
    if not call_id:
        return ToolCallReplayDecision(False, "")
    cached = _cache(task_state).get(call_id)
    if not isinstance(cached, dict):
        return ToolCallReplayDecision(False, call_id)
    if not _identity_matches(cached, envelope):
        return ToolCallReplayDecision(False, call_id, reason="tool_call_identity_conflict")
    observation = cached.get("observation")
    if not isinstance(observation, dict):
        return ToolCallReplayDecision(False, call_id)
    return ToolCallReplayDecision(
        True,
        call_id,
        copy.deepcopy(observation),
        "same_tool_call_identity_completed",
    )


def record_completed_tool_call(
    task_state: Any,
    envelope: Any,
    observation: dict[str, Any],
) -> bool:
    """Persist one completed real dispatch for same-call duplicate delivery."""

    call_id = _call_id(envelope)
    if not call_id or not bool(getattr(envelope, "side_effect", False)):
        return False
    cache = _cache(task_state)
    if call_id in cache:
        return False
    cache[call_id] = {
        "call_id": call_id,
        "canonical_name": str(getattr(envelope, "canonical_name", "") or ""),
        "executable_name": str(getattr(envelope, "executable_name", "") or ""),
        "arguments_fingerprint": stable_tool_arguments_fingerprint(
            getattr(envelope, "parsed_arguments", {}) or {}
        ),
        "observation": copy.deepcopy(observation),
    }
    return True


def _identity_matches(cached: dict[str, Any], envelope: Any) -> bool:
    return bool(
        str(cached.get("call_id") or "") == _call_id(envelope)
        and str(cached.get("canonical_name") or "")
        == str(getattr(envelope, "canonical_name", "") or "")
        and str(cached.get("executable_name") or "")
        == str(getattr(envelope, "executable_name", "") or "")
        and str(cached.get("arguments_fingerprint") or "")
        == stable_tool_arguments_fingerprint(getattr(envelope, "parsed_arguments", {}) or {})
    )


def _call_id(envelope: Any) -> str:
    return str(
        getattr(envelope, "provider_call_id", "")
        or getattr(envelope, "call_id", "")
        or ""
    ).strip()


def _cache(task_state: Any) -> dict[str, dict[str, Any]]:
    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        setattr(task_state, "metadata", metadata)
    cache = metadata.get(TOOL_CALL_EXECUTION_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
        metadata[TOOL_CALL_EXECUTION_CACHE_KEY] = cache
    return cache


__all__ = [
    "TOOL_CALL_EXECUTION_CACHE_KEY",
    "ToolCallReplayDecision",
    "find_completed_tool_call",
    "record_completed_tool_call",
]
