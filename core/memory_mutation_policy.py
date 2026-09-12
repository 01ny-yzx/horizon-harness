"""Deterministic provenance validation for model-requested memory mutations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


PROVENANCE_USER_EXPLICIT = "user_explicit"
PROVENANCE_VERIFIED_OBSERVATION = "verified_observation"
PROVENANCE_KINDS = frozenset({
    PROVENANCE_USER_EXPLICIT,
    PROVENANCE_VERIFIED_OBSERVATION,
})

MEMORY_READ_TOOLS = frozenset({"search_memory_references", "read_memory_reference"})
MEMORY_MUTATION_TOOLS = frozenset({
    "remember_user_preference",
    "remember_stable_fact",
    "remember_project_summary",
    "remember_project_instruction",
    "update_stable_fact",
    "update_project_instruction",
    "delete_memory_reference",
    "delete_user_preference",
    "delete_project_instruction",
    "clear_memory_type",
})
MEMORY_TOOLS = MEMORY_READ_TOOLS | MEMORY_MUTATION_TOOLS

_CURRENT_TASK_STATE: ContextVar[Any | None] = ContextVar(
    "horizon_memory_mutation_task_state",
    default=None,
)


@dataclass(frozen=True)
class MemoryProvenanceDecision:
    allowed: bool
    source: str = ""
    source_reference: str = ""
    error: str = ""


@contextmanager
def memory_mutation_context(task_state: Any) -> Iterator[None]:
    """Expose only the currently executing task to synchronous Memory tools."""

    token = _CURRENT_TASK_STATE.set(task_state)
    try:
        yield
    finally:
        _CURRENT_TASK_STATE.reset(token)


def validate_memory_provenance(
    provenance_kind: str,
    source_reference: str = "",
) -> MemoryProvenanceDecision:
    """Validate explicit or observation-backed provenance without intent guessing."""

    kind = str(provenance_kind or "").strip().lower()
    reference = str(source_reference or "").strip()
    if kind not in PROVENANCE_KINDS:
        return MemoryProvenanceDecision(
            False,
            error="provenance_kind must be user_explicit or verified_observation.",
        )
    if kind == PROVENANCE_USER_EXPLICIT:
        return MemoryProvenanceDecision(True, source=kind, source_reference=reference)
    if not reference:
        return MemoryProvenanceDecision(
            False,
            error="source_reference is required for verified_observation provenance.",
        )

    task_state = _CURRENT_TASK_STATE.get()
    metadata = getattr(task_state, "metadata", None)
    observations = metadata.get("completion_observations") if isinstance(metadata, dict) else None
    for item in observations if isinstance(observations, list) else []:
        observation = _observation_payload(item)
        if not _successful_observation(observation):
            continue
        tool_name = _tool_name(observation)
        if tool_name in MEMORY_TOOLS:
            continue
        identities = {
            str(observation.get(key) or "").strip()
            for key in ("call_id", "provider_call_id", "observation_id")
        }
        if reference in identities:
            return MemoryProvenanceDecision(
                True,
                source=kind,
                source_reference=reference,
            )
    return MemoryProvenanceDecision(
        False,
        error="source_reference must identify a successful non-Memory ToolObservation in the current task.",
    )


def _observation_payload(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    nested = item.get("observation")
    return dict(nested) if isinstance(nested, dict) else dict(item)


def _successful_observation(observation: dict[str, Any]) -> bool:
    status = str(observation.get("status") or "").strip().lower()
    return observation.get("success") is True and status in {"success", "completed"}


def _tool_name(observation: dict[str, Any]) -> str:
    return str(
        observation.get("tool")
        or observation.get("tool_name")
        or observation.get("canonical_name")
        or ""
    ).rsplit(".", 1)[-1]


__all__ = [
    "MEMORY_MUTATION_TOOLS",
    "MEMORY_READ_TOOLS",
    "MEMORY_TOOLS",
    "MemoryProvenanceDecision",
    "memory_mutation_context",
    "validate_memory_provenance",
]
