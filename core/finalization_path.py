"""Structured finalization bridge and final-answer path tracing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.tool_observation import ToolObservation, observation_to_legacy_dict
from core.tool_outcome_resolution import ToolOutcomeResolution


FinalAnswerPath = Literal[
    "direct_answer",
    "llm_final",
    "policy_blocked",
    "terminal_failure",
    "emergency_fallback",
]
TERMINAL_OUTCOME_KINDS = frozenset({"terminal_success", "terminal_failure", "terminal_policy_blocked"})


@dataclass(frozen=True)
class FinalAnswerPathDecision:
    path: FinalAnswerPath
    trigger: str
    outcome_kind: str = ""
    tool: str = ""
    tools_disabled: bool = True
    policy_code: str = ""
    fallback_reason: str = ""
    snapshot_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "trigger": self.trigger,
            "outcome_kind": self.outcome_kind,
            "tool": self.tool,
            "tools_disabled": self.tools_disabled,
            "policy_code": self.policy_code,
            "fallback_reason": self.fallback_reason,
            "snapshot_hash": self.snapshot_hash,
        }


def promote_completed_observation_to_terminal_outcome(
    *,
    outcome: ToolOutcomeResolution,
    observation: ToolObservation | dict[str, Any],
    observation_completion: Any,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> ToolOutcomeResolution:
    """Promote a completed structured observation into a terminal outcome."""

    if outcome.kind in TERMINAL_OUTCOME_KINDS:
        return outcome
    if not bool(getattr(observation_completion, "can_finish", False)):
        return outcome
    if bool(getattr(observation_completion, "requires_more_tools", True)):
        return outcome
    observed = observation_to_legacy_dict(observation) if isinstance(observation, ToolObservation) else dict(observation or {})
    if not _is_valid_observation(observed):
        return outcome

    status = str(observed.get("status") or "").strip().lower()
    policy_code = _policy_code(observed, outcome)
    metadata = {
        **dict(outcome.metadata or {}),
        "arguments": dict(arguments or {}),
        "observation": observed,
        "observation_completion_gate": _completion_dict(observation_completion),
        "promotion_source": "observation_completion",
    }
    resolved_tool = str(observed.get("tool") or tool_name or outcome.tool or "")
    if status == "blocked" or policy_code:
        return ToolOutcomeResolution(
            "terminal_policy_blocked",
            "policy_blocked",
            tool=resolved_tool,
            status="blocked",
            policy_code=policy_code or str(observed.get("error_code") or "tool_call_blocked"),
            user_message=outcome.user_message,
            metadata=metadata,
        )
    if observed.get("success") is True:
        return ToolOutcomeResolution(
            "terminal_success",
            "observation_completion_success",
            tool=resolved_tool,
            status="success",
            user_message=outcome.user_message,
            metadata=metadata,
        )
    return ToolOutcomeResolution(
        "terminal_failure",
        "observation_completion_failure",
        tool=resolved_tool,
        status="failed",
        user_message=outcome.user_message,
        metadata=metadata,
    )


def record_final_answer_path_once(trace: Any, step: int, decision: FinalAnswerPathDecision) -> bool:
    """Record exactly one final_answer_path event on a trace."""

    events = getattr(trace, "events", ()) or ()
    if any(str(getattr(event, "event_type", "") or "") == "final_answer_path" for event in events):
        return False
    import json

    trace.add_event(
        step,
        "final_answer_path",
        json.dumps(decision.to_dict(), ensure_ascii=False),
        tool_name=decision.tool,
        success=decision.path != "emergency_fallback" or bool(decision.fallback_reason),
    )
    return True


def _is_valid_observation(observation: dict[str, Any]) -> bool:
    return bool(observation and (observation.get("tool") or "success" in observation or observation.get("status")))


def _policy_code(observation: dict[str, Any], outcome: ToolOutcomeResolution) -> str:
    explicit = str(observation.get("policy_code") or getattr(outcome, "policy_code", "") or "").strip()
    if explicit:
        return explicit
    if str(observation.get("status") or "").strip().lower() != "blocked":
        return ""
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    return str(observation.get("error_code") or data.get("policy_code") or data.get("code") or "").strip()


def _completion_dict(completion: Any) -> dict[str, Any]:
    to_dict = getattr(completion, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        return dict(value) if isinstance(value, dict) else {}
    return {
        "status": str(getattr(completion, "status", "") or ""),
        "can_finish": bool(getattr(completion, "can_finish", False)),
        "requires_more_tools": bool(getattr(completion, "requires_more_tools", False)),
        "reason": str(getattr(completion, "reason", "") or ""),
        "finalization_mode": str(getattr(completion, "finalization_mode", "") or ""),
    }


__all__ = [
    "FinalAnswerPathDecision",
    "promote_completed_observation_to_terminal_outcome",
    "record_final_answer_path_once",
]
