"""Lightweight run trace for debugging and regression analysis."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.unicode_safety import sanitize_unicode


TraceEventType = Literal[
    "task_start",
    "request_guidance_resolved",
    "initial_tool_surface",
    "initial_agent_turn",
    "initial_agent_turn_failure",
    "initial_tool_batch_complete",
    "agent_turn_context",
    "continuation_tool_surface_resolved",
    "prompt_pack_compaction",
    "unified_intent_capability",
    "llm_response",
    "tool_call",
    "tool_schema_scope",
    "observation",
    "path_grounding",
    "observation_compaction",
    "observation_capability_witness",
    "capability_observation_satisfied",
    "host_command_context",
    "guard_fast_path",
    "final",
    "runtime_lane",
    "capability_surface",
    "build_step_contract",
    "build_step_progress",
    "build_step_contract_repeat_tool_warning",
    "build_step_contract_unexpected_tool_warning",
    "simple_fast_path",
    "tool_scope_budget",
    "runtime_state",
    "terminal_finalization_adoption",
    "terminal_finalization_recovery",
    "terminal_success_applied",
    "tool_call_skipped_after_terminal_outcome",
    "context_budget",
    "auto_compact",
    "performance_stage",
    "runtime_metrics",
    "finalization_outlet",
    "terminal_responder_llm",
    "terminal_responder_pack",
    "terminal_responder_validation",
    "final_observation_context",
    "finalization_context_snapshot",
    "finalization_context_budget",
    "isolated_finalization_pack",
    "finalization_snapshot_reused",
    "isolated_terminal_synthesis_output",
    "final_answer_path",
    "emergency_finalization_started",
    "emergency_finalization_fallback",
    "tool_call_grant_registered",
    "tool_call_grant_checked",
    "tool_call_grant_state",
    "tool_result_externalized",
    "doom_loop_permission_asked",
    "doom_loop_permission_decision",
    "same_tool_call_result_replayed",
    "agent_continuation_result",
    "candidate_rejected",
    "recoverable_tool_observation",
    "tool_failure_disposition",
    "tool_outcome",
    "research_trace",
]
MAX_SUMMARY_CHARS = 800
STRUCTURED_EVENT_TYPES = frozenset(
    {
        "performance_stage",
        "runtime_metrics",
        "initial_tool_surface",
        "request_guidance_resolved",
        "initial_agent_turn",
        "initial_agent_turn_failure",
        "initial_tool_batch_complete",
        "agent_turn_context",
        "continuation_tool_surface_resolved",
        "prompt_pack_compaction",
        "tool_schema_scope",
        "tool_call_grant_registered",
        "tool_call_grant_checked",
        "tool_call_grant_state",
        "tool_result_externalized",
        "doom_loop_permission_asked",
        "doom_loop_permission_decision",
        "same_tool_call_result_replayed",
        "agent_continuation_result",
        "candidate_rejected",
        "recoverable_tool_observation",
        "observation_capability_witness",
        "capability_observation_satisfied",
        "tool_failure_disposition",
        "tool_outcome",
        "research_trace",
        "terminal_success_applied",
        "tool_call_skipped_after_terminal_outcome",
        "emergency_finalization_started",
        "emergency_finalization_fallback",
        "context_budget",
        "auto_compact",
        "final_observation_context",
        "finalization_context_snapshot",
        "finalization_context_budget",
        "isolated_finalization_pack",
        "finalization_snapshot_reused",
        "isolated_terminal_synthesis_output",
        "terminal_responder_llm",
        "terminal_responder_validation",
        "final_answer_path",
    }
)


@dataclass
class TraceEvent:
    """One sanitized event in an Agent run."""

    step: int
    event_type: TraceEventType
    tool_name: str
    success: bool | None
    summary: str
    timestamp: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return sanitize_unicode(asdict(self))


@dataclass(frozen=True)
class TraceSaveResult:
    """Paths and stable ID for one persisted task trace."""

    trace_id: str
    trace_path: Path
    latest_trace_path: Path


class AgentTrace:
    """Collects a compact sanitized trace for one Agent run."""

    def __init__(self, task_id: str, user_goal: str, task_type: str) -> None:
        self.task_id = _sanitize(task_id)
        self.user_goal = _sanitize(user_goal)
        self.task_type = _sanitize(task_type)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.completed_at = ""
        self.final_answer = ""
        self.trace_path = ""
        self.latest_trace_path = ""
        self.events: list[TraceEvent] = []

    def add_event(
        self,
        step: int,
        event_type: TraceEventType,
        summary: str,
        tool_name: str = "",
        success: bool | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Add one sanitized event."""

        structured_data = data
        if structured_data is None and event_type in STRUCTURED_EVENT_TYPES:
            structured_data = _structured_data_from_summary(summary)
        self.events.append(
            TraceEvent(
                step=step,
                event_type=event_type,
                tool_name=_sanitize(tool_name),
                success=success,
                summary=_sanitize(summary)[:MAX_SUMMARY_CHARS],
                timestamp=datetime.now(timezone.utc).isoformat(),
                data=_sanitize_data(structured_data or {}),
            )
        )

    def complete(self, final_answer: str) -> None:
        """Attach final response metadata before persistence."""

        self.final_answer = _redact_text(final_answer)
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def task_file_paths(self, trace_dir: str | Path) -> TraceSaveResult:
        """Resolve safe task-specific and latest trace paths."""

        directory = Path(trace_dir).resolve()
        trace_id = _safe_task_id(self.task_id)
        return TraceSaveResult(
            trace_id=trace_id,
            trace_path=directory / f"agent_trace_{trace_id}.json",
            latest_trace_path=directory / "agent_trace_latest.json",
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the full trace as a JSON-friendly dictionary."""

        return _sanitize_data(
            {
                "task_id": self.task_id,
                "user_goal": self.user_goal,
                "task_type": self.task_type,
                "final_answer": self.final_answer,
                "final_answer_chars": len(self.final_answer),
                "created_at": self.created_at,
                "completed_at": self.completed_at,
                "trace_path": self.trace_path,
                "latest_trace_path": self.latest_trace_path,
                "events": [event.to_dict() for event in self.events],
            }
        )

    def save_task_files(self, trace_dir: str | Path, *, final_answer: str | None = None) -> TraceSaveResult:
        """Atomically save identical task-specific and latest trace files."""

        paths = self.task_file_paths(trace_dir)
        paths.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self.trace_path = str(paths.trace_path)
        self.latest_trace_path = str(paths.latest_trace_path)
        if final_answer is not None:
            self.complete(final_answer)
        elif not self.completed_at:
            self.completed_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        _atomic_write_text(paths.trace_path, payload)
        _atomic_write_text(paths.latest_trace_path, payload)
        return paths

    def save_json(self, path: str | Path) -> None:
        """Save trace JSON to disk."""

        output_path = Path(path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        _atomic_write_text(output_path, text)

    def format_summary(self) -> str:
        """Return a short terminal-friendly summary."""

        lines = [f"Trace task_id={self.task_id} task_type={self.task_type} events={len(self.events)}"]
        for event in self.events[-12:]:
            status = "" if event.success is None else f" success={event.success}"
            tool = "" if not event.tool_name else f" tool={event.tool_name}"
            lines.append(f"- step={event.step} {event.event_type}{tool}{status}: {event.summary}")
        return "\n".join(lines)


def summarize_observation(observation: dict[str, Any]) -> str:
    """Create a compact observation summary without long page bodies."""

    try:
        from core.tool_observation import observation_summary

        if isinstance(observation, dict) and ("status" in observation or "kind" in observation or "tool_observation" in observation):
            return observation_summary(observation)
    except Exception:
        pass

    success = observation.get("success")
    error = observation.get("error", "")
    data = observation.get("data", {})
    if not isinstance(data, dict):
        return f"success={success} error={error}"

    keys = sorted(key for key in data.keys() if key not in {"text", "stdout", "stderr", "diff"})
    extras = []
    if "results" in data and isinstance(data["results"], list):
        extras.append(f"results={len(data['results'])}")
    if "url" in data:
        extras.append(f"url={data.get('url')}")
    return " ".join(part for part in [f"success={success}", f"keys={keys}", *extras, f"error={error}" if error else ""] if part)


def _sanitize(text: str) -> str:
    """Remove likely secrets and shrink whitespace."""

    return sanitize_unicode(" ".join(_redact_text(text).split()))


def _redact_text(text: Any) -> str:
    value = re.sub(r"(sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,})", "[REDACTED_KEY]", str(text or ""))
    value = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED_KEY]", value)
    return sanitize_unicode(value)


def _sanitize_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_data(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_data(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(value)


def _structured_data_from_summary(summary: Any) -> dict[str, Any]:
    if isinstance(summary, dict):
        return summary
    try:
        payload = json.loads(str(summary or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_task_id(task_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(task_id or "").strip()).strip("_")
    return (value or "task")[:128]


def _atomic_write_text(path: Path, text: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            errors="replace",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
