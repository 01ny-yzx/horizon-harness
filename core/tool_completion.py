"""Record ToolObservation execution facts and capability witnesses."""

from __future__ import annotations

from typing import Any

from core.tool_observation import (
    ToolObservation,
    observation_to_legacy_dict,
    observation_to_trace_dict,
)
from core.tool_spec import tool_spec_supports_runtime_capability


def tool_observation_satisfies_capability(
    *,
    required_capability: str,
    tool_spec: Any,
    observation: dict[str, Any] | ToolObservation,
) -> bool:
    """Return whether a successful observation witnesses one ToolSpec capability."""

    capability = str(required_capability or "").strip()
    if not capability or tool_spec is None:
        return False
    observed = _observation_summary(
        observation
        if isinstance(observation, ToolObservation)
        else {"observation": observation}
    )
    if observed.get("success") is not True:
        return False
    if not tool_spec_supports_runtime_capability(tool_spec, capability):
        return False
    if capability != "document_load":
        return True
    data = _raw_observation_data(observation)
    if isinstance(data.get("documents"), list):
        documents = data.get("documents") or []
        failures = data.get("failures") if isinstance(data.get("failures"), list) else []
        return bool(
            str(data.get("status") or "").lower() == "success"
            and not failures
            and all(
                isinstance(item, dict)
                and bool(str(item.get("document_id") or "").strip())
                and item.get("chunks_stored") is True
                and (
                    item.get("document_stored") is True
                    or bool(item.get("store_status"))
                )
                for item in documents
            )
        )
    return bool(
        str(data.get("status") or "").lower() == "success"
        and str(data.get("document_id") or "").strip()
        and data.get("chunks_stored") is True
        and (
            data.get("document_stored") is True
            or bool(data.get("store_status"))
        )
    )


def record_completion_observation(
    task_state: Any,
    tool_name: str,
    observation: dict[str, Any] | ToolObservation,
) -> None:
    """Store a compact execution-fact record in TaskState metadata."""

    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        return
    observations = metadata.setdefault("completion_observations", [])
    if not isinstance(observations, list):
        observations = []
        metadata["completion_observations"] = observations
    if isinstance(observation, ToolObservation):
        record = observation_to_legacy_dict(observation)
    else:
        nested = observation.get("observation") if isinstance(observation, dict) else None
        record = dict(nested) if isinstance(nested, dict) else dict(observation or {})
    record.setdefault(
        "tool",
        str(tool_name or record.get("tool") or record.get("tool_name") or ""),
    )
    observations.append(record)


def _observation_summary(item: dict[str, Any] | ToolObservation) -> dict[str, Any]:
    if isinstance(item, ToolObservation):
        item = {"tool": item.tool_name, "observation": observation_to_trace_dict(item)}
    observation = item.get("observation")
    observation = observation if isinstance(observation, dict) else item
    data = observation.get("data", {}) if isinstance(observation, dict) else {}
    data = data if isinstance(data, dict) else {}
    status = str(observation.get("status") or data.get("status") or "")
    return {
        "success": observation.get("success") is True
        and status not in {"blocked", "skipped", "error"},
    }


def _raw_observation_data(
    observation: dict[str, Any] | ToolObservation,
) -> dict[str, Any]:
    if isinstance(observation, ToolObservation):
        return dict(observation.data or {}) if isinstance(observation.data, dict) else {}
    if not isinstance(observation, dict):
        return {}
    data = observation.get("data")
    if isinstance(data, dict):
        return data
    nested = observation.get("observation")
    if isinstance(nested, dict) and isinstance(nested.get("data"), dict):
        return nested["data"]
    return {}


__all__ = [
    "record_completion_observation",
    "tool_observation_satisfies_capability",
]
