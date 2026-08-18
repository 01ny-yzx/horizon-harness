"""Simple chat fast path for tool-free runtime lane tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.runtime_lane import RuntimeLane, RuntimeLaneDecision
from core.structured_intent_access import structured_task_profile
from core.unicode_safety import sanitize_unicode


SIMPLE_CHAT_SYSTEM_PROMPT = (
    "You are Horizon Runtime in simple chat mode.\n"
    "Answer the user directly and concisely.\n"
    "No tools are available in this mode.\n"
    "Do not claim that you read files, executed commands, browsed the web, or used tools.\n"
    "If the request requires tools, say that it requires the full agent path."
)


@dataclass(frozen=True)
class SimpleFastPathDecision:
    enabled: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(
            {
                "enabled": self.enabled,
                "reason": self.reason,
                "metadata": self.metadata,
            }
        )


def should_use_simple_fast_path(
    task_state: Any,
    runtime_lane: RuntimeLaneDecision,
    tool_plan: dict[str, Any] | None,
) -> SimpleFastPathDecision:
    """Return whether a task can use the simple chat path."""

    plan = dict(tool_plan) if isinstance(tool_plan, dict) else {}
    profile = structured_task_profile(task_state)
    metadata = _decision_metadata(task_state, runtime_lane, plan)
    if runtime_lane.lane is not RuntimeLane.CHAT:
        return SimpleFastPathDecision(False, "runtime_lane_not_chat", metadata)
    state_metadata = getattr(task_state, "metadata", {}) if task_state is not None else {}
    if isinstance(state_metadata, dict) and state_metadata.get("pre_router_fallback_reason") == "invalid_capability_list":
        return SimpleFastPathDecision(False, "invalid_capability_list", metadata)
    if profile is None:
        return SimpleFastPathDecision(False, "missing_task_profile", metadata)
    if bool(getattr(profile, "tool_required", False)):
        return SimpleFastPathDecision(False, "tool_required", metadata)
    if bool(getattr(profile, "side_effect_required", False)):
        return SimpleFastPathDecision(False, "side_effect_required", metadata)
    if _plan_has_tools_or_capabilities(plan):
        return SimpleFastPathDecision(False, "tool_plan_not_empty", metadata)
    if _profile_requires_full_agent(profile):
        return SimpleFastPathDecision(False, "profile_requires_full_agent", metadata)
    return SimpleFastPathDecision(True, "runtime_lane_chat_without_tools", metadata)


def build_simple_chat_messages(memory: Any, user_input: str, *, identity_note: str = "") -> list[dict[str, Any]]:
    """Build lightweight chat messages without the full runtime system prompt."""

    messages: list[dict[str, Any]] = [{"role": "system", "content": SIMPLE_CHAT_SYSTEM_PROMPT}]
    if identity_note:
        messages.append({"role": "system", "content": identity_note})
    for message in getattr(memory, "messages", []) or []:
        filtered = _simple_history_message(message)
        if filtered is not None:
            messages.append(filtered)
    messages.append({"role": "user", "content": str(user_input or "")})
    return sanitize_unicode(messages)


def _profile_requires_full_agent(profile: Any) -> bool:
    return any(
        bool(getattr(profile, name, False))
        for name in (
            "needs_web",
            "needs_research",
            "needs_web_search",
            "needs_fetch_url",
            "needs_browser",
            "needs_rag",
            "needs_validation",
            "needs_file_output",
            "needs_code_edit",
        )
    )


def _plan_has_tools_or_capabilities(plan: dict[str, Any]) -> bool:
    if str(plan.get("primary_tool") or "").strip():
        return True
    if str(plan.get("primary_capability") or "").strip():
        return True
    for key in (
        "tool_priority",
        "supporting_tool_priority",
        "fallback_tool_priority",
        "capabilities",
        "tool_capabilities",
        "supporting_capabilities",
        "fallback_capabilities",
    ):
        if _non_empty_list(plan.get(key)):
            return True
    return False


def _non_empty_list(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(str(item or "").strip() for item in value)
    return False


def _simple_history_message(message: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if role == "tool":
        return None
    if role == "assistant" and message.get("tool_calls"):
        return None
    if role not in {"user", "assistant"}:
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return {"role": role, "content": content}


def _decision_metadata(task_state: Any, runtime_lane: RuntimeLaneDecision, plan: dict[str, Any]) -> dict[str, Any]:
    profile = structured_task_profile(task_state)
    return sanitize_unicode(
        {
            "runtime_lane": runtime_lane.lane.value,
            "tool_plan_primary_tool": str(plan.get("primary_tool") or ""),
            "tool_plan_primary_capability": str(plan.get("primary_capability") or ""),
            "tool_required": bool(getattr(profile, "tool_required", False)) if profile is not None else None,
            "side_effect_required": bool(getattr(profile, "side_effect_required", False)) if profile is not None else None,
        }
    )
