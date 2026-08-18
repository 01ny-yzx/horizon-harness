"""Structured finalization outlet selection for terminal tool outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FinalizationOutletKind = Literal["terminal_responder", "emergency_fallback"]


@dataclass(frozen=True)
class FinalizationOutlet:
    kind: FinalizationOutletKind
    reason: str
    policy_code: str = ""
    observation: dict[str, Any] = field(default_factory=dict)


def resolve_finalization_outlet(task_state: Any, outcome: Any) -> FinalizationOutlet:
    """Resolve finalization from structured runtime outcome only."""

    kind = str(getattr(outcome, "kind", "") or "").strip()
    observation = _outcome_observation(outcome)
    if kind == "terminal_policy_blocked":
        policy_code = _first_text(
            getattr(outcome, "policy_code", ""),
            observation.get("error_code"),
            _data_value(observation, "error_code"),
            _data_value(observation, "code"),
            _data_value(observation, "block_reason"),
        )
        return FinalizationOutlet(
            kind="terminal_responder",
            reason=(
                "partial_outcome_with_policy_block"
                if _has_independent_successful_completion_observation(task_state, observation)
                else "terminal_policy_blocked"
            ),
            policy_code=policy_code,
            observation=observation,
        )
    if kind in {"terminal_success", "terminal_failure"}:
        return FinalizationOutlet(
            kind="terminal_responder",
            reason=str(getattr(outcome, "reason", "") or "").strip() or "terminal_tool_result",
            observation=observation,
        )
    return FinalizationOutlet(
        kind="emergency_fallback",
        reason=kind or "non_terminal_tool_outcome",
        observation=observation,
    )


def _outcome_observation(outcome: Any) -> dict[str, Any]:
    metadata = getattr(outcome, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    observation = metadata.get("observation")
    return dict(observation) if isinstance(observation, dict) else {}


def _data_value(observation: dict[str, Any], key: str) -> Any:
    data = observation.get("data")
    data = data if isinstance(data, dict) else {}
    return data.get(key)


def _has_independent_successful_completion_observation(
    task_state: Any,
    blocked_observation: dict[str, Any],
) -> bool:
    metadata = getattr(task_state, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    observations = metadata.get("completion_observations")
    observations = observations if isinstance(observations, list) else []
    blocked_identities = set(_observation_identities(blocked_observation))
    for candidate in observations:
        if not isinstance(candidate, dict) or not _observation_is_success(candidate):
            continue
        if _observations_are_independent_calls(candidate, blocked_observation, blocked_identities):
            return True
    return False


def _observation_is_success(observation: dict[str, Any]) -> bool:
    status = str(observation.get("status") or "").strip().lower()
    data = observation.get("data")
    data = data if isinstance(data, dict) else {}
    data_status = str(data.get("status") or "").strip().lower()
    policy_code = _first_text(observation.get("policy_code"), data.get("policy_code"))
    return bool(
        not policy_code
        and status != "blocked"
        and data_status != "blocked"
        and (observation.get("success") is True or status in {"success", "completed"} or data_status in {"success", "completed"})
    )


def _observation_identities(observation: dict[str, Any]) -> list[str]:
    identities: list[str] = []
    for key, prefix in (
        ("call_id", "call"),
        ("tool_call_id", "call"),
        ("provider_call_id", "provider"),
        ("observation_id", "observation"),
    ):
        value = str(observation.get(key) or "").strip()
        identity = f"{prefix}:{value}" if value else ""
        if identity and identity not in identities:
            identities.append(identity)
    return identities


def _observations_are_independent_calls(
    candidate: dict[str, Any],
    blocked_observation: dict[str, Any],
    blocked_identities: set[str],
) -> bool:
    candidate_identities = set(_observation_identities(candidate))
    if blocked_identities and candidate_identities:
        return blocked_identities.isdisjoint(candidate_identities)
    candidate_tool = str(candidate.get("tool") or candidate.get("tool_name") or "").strip()
    blocked_tool = str(blocked_observation.get("tool") or blocked_observation.get("tool_name") or "").strip()
    return bool(candidate_tool and blocked_tool and candidate_tool != blocked_tool)


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


__all__ = [
    "FinalizationOutlet",
    "FinalizationOutletKind",
    "resolve_finalization_outlet",
]
