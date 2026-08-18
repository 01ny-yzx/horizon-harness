"""Immutable, task-local context for tools-disabled finalization."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core.final_observation_context import (
    FinalObservationContext,
    build_final_observation_context,
    final_observation_context_trace_summary,
)
from core.unicode_safety import sanitize_unicode


FINALIZATION_CONTEXT_SNAPSHOT_VERSION = "finalization_context_snapshot_v1"

_MODEL_DATA_FORBIDDEN_KEYS = frozenset(
    {
        "arguments",
        "arguments_fingerprint",
        "call_id",
        "content_ref",
        "content_sha256",
        "metadata",
        "observation_id",
        "path_grounding",
        "provider_call_id",
        "schema_version",
        "sha256",
        "snapshot_hash",
        "source_ref",
        "source_sha256",
        "task_id",
        "tool_call_id",
    }
)


@dataclass(frozen=True, init=False)
class FinalizationContextSnapshot:
    """An immutable finalization view with separate model and trace projections."""

    _model_context: dict[str, Any] = field(repr=False)
    _trace_context: dict[str, Any] = field(repr=False)
    snapshot_hash: str
    coverage_complete: bool
    result_count: int
    model_context_chars: int
    trace_context_chars: int

    def __init__(
        self,
        *,
        model_context: dict[str, Any],
        trace_context: dict[str, Any],
        snapshot_hash: str,
        coverage_complete: bool,
        result_count: int,
        model_context_chars: int,
        trace_context_chars: int,
    ) -> None:
        object.__setattr__(
            self,
            "_model_context",
            copy.deepcopy(sanitize_unicode(dict(model_context or {}))),
        )
        object.__setattr__(
            self,
            "_trace_context",
            copy.deepcopy(sanitize_unicode(dict(trace_context or {}))),
        )
        object.__setattr__(self, "snapshot_hash", str(snapshot_hash or ""))
        object.__setattr__(self, "coverage_complete", bool(coverage_complete))
        object.__setattr__(self, "result_count", int(result_count or 0))
        object.__setattr__(self, "model_context_chars", int(model_context_chars or 0))
        object.__setattr__(self, "trace_context_chars", int(trace_context_chars or 0))

    @property
    def model_context(self) -> dict[str, Any]:
        return copy.deepcopy(self._model_context)

    @property
    def trace_context(self) -> dict[str, Any]:
        return copy.deepcopy(self._trace_context)


def build_finalization_context_snapshot(
    *,
    user_request: str,
    task_state: Any,
    outcome: Any | None = None,
    finalization_mode: str = "",
    finalization_reason: str = "",
) -> FinalizationContextSnapshot:
    """Build one current-task snapshot without reading Memory or dialogue."""

    context = build_final_observation_context(task_state, outcome)
    context_summary = final_observation_context_trace_summary(context)
    results = [_model_result(item) for item in context]
    if not results:
        synthetic = _structured_outcome_result(outcome)
        if synthetic:
            results.append(synthetic)
    coverage_complete = bool(getattr(context, "coverage_complete", True))
    model_context = sanitize_unicode(
        {
            "user_request": str(
                user_request or getattr(task_state, "user_goal", "") or ""
            ).strip(),
            "final_status": _final_status(
                results,
                coverage_complete=coverage_complete,
                outcome=outcome,
            ),
            "result_count": len(results),
            "results": results,
            "coverage": {
                "complete": coverage_complete,
                "result_count": len(results),
                "missing_result_count": len(
                    getattr(context, "missing_call_ids", []) or []
                ),
            },
        }
    )
    metadata = getattr(task_state, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    trace_context = {
        "task_id": str(getattr(task_state, "task_id", "") or ""),
        "finalization_mode": str(
            finalization_mode or metadata.get("finalization_mode") or ""
        ),
        "finalization_reason": str(
            finalization_reason
            or metadata.get("finalization_reason")
            or finalization_mode
            or ""
        ),
        "outcome_kind": str(getattr(outcome, "kind", "") or ""),
        "outcome_reason": str(getattr(outcome, "reason", "") or ""),
        "outcome_status": str(getattr(outcome, "status", "") or ""),
        "failure_disposition": str(
            getattr(outcome, "failure_disposition", "") or ""
        ),
        "policy_code": str(getattr(outcome, "policy_code", "") or ""),
        "raw_observation_count": int(
            getattr(context, "raw_observation_count", len(context)) or 0
        ),
        "observation_count": len(context),
        "duplicates_removed": int(
            getattr(context, "duplicates_removed", 0) or 0
        ),
        "expected_call_ids": list(getattr(context, "expected_call_ids", []) or []),
        "represented_call_ids": list(
            getattr(context, "represented_call_ids", []) or []
        ),
        "missing_call_ids": list(getattr(context, "missing_call_ids", []) or []),
        "coverage_complete": coverage_complete,
        "source_counts": dict(context_summary.get("source_counts") or {}),
        "snapshot_version": FINALIZATION_CONTEXT_SNAPSHOT_VERSION,
    }
    return _snapshot_from_projections(model_context, trace_context)


def rebuild_finalization_context_snapshot(
    snapshot: FinalizationContextSnapshot,
    *,
    model_context: dict[str, Any],
) -> FinalizationContextSnapshot:
    """Create a budgeted snapshot while preserving trace identity and coverage."""

    trace_context = snapshot.trace_context
    for key in (
        "snapshot_hash",
        "model_context_chars",
        "trace_context_chars",
    ):
        trace_context.pop(key, None)
    return _snapshot_from_projections(model_context, trace_context)


def finalization_snapshot_trace_summary(
    snapshot: FinalizationContextSnapshot,
) -> dict[str, Any]:
    """Return trace-safe snapshot metadata without model-context content."""

    trace_context = snapshot.trace_context
    return sanitize_unicode(
        {
            "snapshot_hash": snapshot.snapshot_hash,
            "result_count": snapshot.result_count,
            "coverage_complete": snapshot.coverage_complete,
            "expected_call_count": len(trace_context.get("expected_call_ids") or []),
            "represented_call_count": len(
                trace_context.get("represented_call_ids") or []
            ),
            "missing_call_count": len(trace_context.get("missing_call_ids") or []),
            "model_context_chars": snapshot.model_context_chars,
            "trace_context_chars": snapshot.trace_context_chars,
            "source_counts": dict(trace_context.get("source_counts") or {}),
        }
    )


def _snapshot_from_projections(
    model_context: dict[str, Any],
    trace_context: dict[str, Any],
) -> FinalizationContextSnapshot:
    safe_model = sanitize_unicode(copy.deepcopy(dict(model_context or {})))
    safe_trace = sanitize_unicode(copy.deepcopy(dict(trace_context or {})))
    expected = list(safe_trace.get("expected_call_ids") or [])
    represented = list(safe_trace.get("represented_call_ids") or [])
    missing = list(safe_trace.get("missing_call_ids") or [])
    coverage_complete = bool(safe_trace.get("coverage_complete", not missing))
    hash_payload = {
        "model_context": safe_model,
        "trace_identity": {
            "expected_call_ids": expected,
            "represented_call_ids": represented,
            "missing_call_ids": missing,
            "coverage_complete": coverage_complete,
        },
    }
    snapshot_hash = hashlib.sha256(_stable_json(hash_payload).encode("utf-8")).hexdigest()
    model_chars = len(_stable_json(safe_model))
    safe_trace["snapshot_hash"] = snapshot_hash
    safe_trace["model_context_chars"] = model_chars
    trace_chars = 0
    for _ in range(4):
        safe_trace["trace_context_chars"] = trace_chars
        next_chars = len(_stable_json(safe_trace))
        if next_chars == trace_chars:
            break
        trace_chars = next_chars
    safe_trace["trace_context_chars"] = trace_chars
    return FinalizationContextSnapshot(
        model_context=safe_model,
        trace_context=safe_trace,
        snapshot_hash=snapshot_hash,
        coverage_complete=coverage_complete,
        result_count=int(safe_model.get("result_count") or 0),
        model_context_chars=model_chars,
        trace_context_chars=trace_chars,
    )


def _model_result(item: dict[str, Any]) -> dict[str, Any]:
    result = {
        "tool": str(item.get("base_tool") or item.get("tool") or ""),
        "status": str(item.get("status") or ""),
        "success": item.get("success") is True,
        "summary": str(item.get("summary") or ""),
        "error": str(item.get("error") or ""),
        "error_code": str(item.get("error_code") or ""),
        "policy_code": str(item.get("policy_code") or ""),
        "data_summary": _model_safe_data(item.get("data_summary")),
        "preview": str(item.get("preview") or ""),
        "truncated": bool(item.get("truncated")),
    }
    return sanitize_unicode(
        {
            key: value
            for key, value in result.items()
            if value not in ("", None, {}, [])
            or key in {"success", "truncated"}
        }
    )


def _structured_outcome_result(outcome: Any | None) -> dict[str, Any]:
    if outcome is None:
        return {}
    kind = str(getattr(outcome, "kind", "") or "")
    reason = str(getattr(outcome, "reason", "") or "")
    user_message = str(getattr(outcome, "user_message", "") or "")
    policy_code = str(getattr(outcome, "policy_code", "") or "")
    metadata = getattr(outcome, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    error_code = str(metadata.get("error_code") or "")
    if not any((kind, reason, user_message, policy_code, error_code)):
        return {}
    success = kind == "terminal_success"
    status = str(getattr(outcome, "status", "") or "")
    if not status:
        status = (
            "success"
            if success
            else "blocked"
            if kind == "terminal_policy_blocked"
            else "failed"
        )
    result = {
        "tool": str(getattr(outcome, "tool", "") or ""),
        "status": status,
        "success": success,
        "summary": user_message or reason,
        "error": "" if success else user_message or reason,
        "error_code": error_code,
        "policy_code": policy_code,
        "truncated": False,
    }
    return {
        key: value
        for key, value in sanitize_unicode(result).items()
        if value not in ("", None, {}, []) or key in {"success", "truncated"}
    }


def _model_safe_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _model_safe_data(item)
            for key, item in value.items()
            if str(key) not in _MODEL_DATA_FORBIDDEN_KEYS
            and str(key) not in {"command", "cwd"}
        }
    if isinstance(value, list):
        return [_model_safe_data(item) for item in value]
    return copy.deepcopy(value)


def _final_status(
    results: list[dict[str, Any]],
    *,
    coverage_complete: bool,
    outcome: Any | None,
) -> str:
    if not coverage_complete:
        return "incomplete_evidence"
    has_success = any(item.get("success") is True for item in results)
    has_blocked = any(
        str(item.get("status") or "").lower() == "blocked"
        or bool(item.get("policy_code"))
        for item in results
    )
    has_stopped = (
        str(getattr(outcome, "failure_disposition", "") or "") == "no_progress"
        or any(
            bool((item.get("data_summary") or {}).get("stopped_before_execution"))
            for item in results
            if isinstance(item.get("data_summary"), dict)
        )
    )
    has_failed = any(
        item.get("success") is not True
        and str(item.get("status") or "").lower()
        in {"failed", "error", "partial", "stopped"}
        for item in results
    )
    if has_success and (has_failed or has_blocked or has_stopped):
        return "partially_completed"
    if has_success:
        return "completed"
    if has_blocked:
        return "blocked"
    if has_stopped:
        return "stopped"
    return "failed"


def _stable_json(value: Any) -> str:
    return json.dumps(
        sanitize_unicode(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


__all__ = [
    "FINALIZATION_CONTEXT_SNAPSHOT_VERSION",
    "FinalizationContextSnapshot",
    "build_finalization_context_snapshot",
    "finalization_snapshot_trace_summary",
    "rebuild_finalization_context_snapshot",
]
