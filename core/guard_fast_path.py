"""Trace and metrics helpers for deterministic tool guard fast path."""

from __future__ import annotations

import json
from typing import Any

from core.tool_execution_authorization import base_tool_name


def guard_fast_path_summary(decision: Any, *, elapsed_ms: int, tool_name: str = "") -> dict[str, Any]:
    """Return a trace-friendly summary for one execution boundary decision."""

    allowed = bool(getattr(decision, "allowed", False))
    resolved_tool = str(getattr(decision, "tool_name", "") or tool_name or "")
    return {
        "enabled": True,
        "allowed": allowed,
        "status": str(getattr(decision, "status", "") or ("allowed" if allowed else "blocked")),
        "policy_code": str(getattr(decision, "code", "") or ""),
        "reason": str(getattr(decision, "reason", "") or ""),
        "boundary": str(getattr(decision, "boundary", "") or ""),
        "tool_name": resolved_tool,
        "base_tool": base_tool_name(resolved_tool),
        "access_mode": str(getattr(decision, "access_mode", "") or ""),
        "duration_ms": int(elapsed_ms or 0),
        "sanitized_arguments_present": getattr(decision, "sanitized_arguments", None) is not None,
    }


def record_guard_fast_path(
    *,
    decision: Any,
    elapsed_ms: int,
    metrics: Any | None = None,
    trace: Any | None = None,
    step: int = 0,
    tool_name: str = "",
) -> dict[str, Any]:
    """Record metrics and trace for a pre-dispatch execution boundary decision."""

    if metrics is not None:
        metrics.record_tool_guard_fast_path(decision, elapsed_ms=elapsed_ms)
    summary = guard_fast_path_summary(decision, elapsed_ms=elapsed_ms, tool_name=tool_name)
    if trace is not None:
        trace.add_event(
            step,
            "guard_fast_path",
            json.dumps(summary, ensure_ascii=False),
            tool_name=str(summary.get("tool_name") or ""),
            success=bool(summary.get("allowed")),
        )
    return summary
