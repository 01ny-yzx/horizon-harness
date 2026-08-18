"""Offline smoke checks for guard fast path trace and metrics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.execution_boundary import ExecutionBoundaryDecision, blocked_observation
from core.guard_fast_path import record_guard_fast_path
from core.runtime_metrics import RuntimeMetrics
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import make_boundary_blocked_observation
from core.trace import AgentTrace


def _envelope(tool_name: str, arguments: dict[str, object] | None = None) -> ToolCallEnvelope:
    args = dict(arguments or {})
    return ToolCallEnvelope(
        call_id="call",
        provider_call_id="call",
        source=ToolCallSource.STRUCTURED,
        raw_name=tool_name,
        tool_name=tool_name,
        canonical_name=tool_name,
        executable_name=tool_name,
        raw_arguments=json.dumps(args, ensure_ascii=False),
        parsed_arguments=args,
        sanitized_arguments={},
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True},
    )


def _record(decision: ExecutionBoundaryDecision, envelope: ToolCallEnvelope) -> tuple[RuntimeMetrics, AgentTrace]:
    metrics = RuntimeMetrics()
    trace = AgentTrace("task", "smoke", "simple")
    record_guard_fast_path(
        decision=decision,
        elapsed_ms=1,
        metrics=metrics,
        trace=trace,
        step=1,
        tool_name=envelope.executable_name or envelope.tool_name,
    )
    return metrics, trace


def _event(trace: AgentTrace) -> dict[str, object]:
    payload = trace.to_dict()["events"][0]
    summary = json.loads(payload["summary"])
    return {**payload, "summary_payload": summary}


def test_allowed_read_file_records_guard_fast_path() -> None:
    decision = ExecutionBoundaryDecision(
        allowed=True,
        status="allowed",
        code="ok",
        reason="Execution boundary allowed tool dispatch.",
        boundary="file_read",
        tool_name="read_file",
        access_mode="full_access",
        sanitized_arguments={"path": "core/loop.py"},
    )
    metrics, trace = _record(decision, _envelope("read_file", {"path": "core/loop.py"}))
    event = _event(trace)

    assert event["event_type"] == "guard_fast_path"
    assert event["summary_payload"]["allowed"] is True
    assert event["summary_payload"]["policy_code"] == "ok"
    assert event["summary_payload"]["tool_name"] == "read_file"
    summary = metrics.summary()
    assert summary["tool_guard_fast_path_count"] == 1
    assert summary["tool_guard_fast_path_allowed_count"] == 1
    assert summary["tool_guard_fast_path_blocked_count"] == 0


def test_invalid_sandbox_exec_records_guard_fast_path_without_dispatch() -> None:
    decision = ExecutionBoundaryDecision(
        allowed=False,
        status="blocked",
        code="invalid_timeout",
        reason="timeout must be an integer from 1 to 300.",
        boundary="command_arguments",
        tool_name="sandbox_exec",
        access_mode="full_access",
        sanitized_arguments=None,
    )
    envelope = _envelope("sandbox_exec", {"command": "echo ok", "timeout": 0})
    metrics, trace = _record(decision, envelope)
    observation = make_boundary_blocked_observation(envelope, decision, blocked_observation(decision))
    event = _event(trace)

    assert event["event_type"] == "guard_fast_path"
    assert event["summary_payload"]["allowed"] is False
    assert event["summary_payload"]["status"] == "blocked"
    assert event["summary_payload"]["policy_code"] == "invalid_timeout"
    assert event["summary_payload"]["boundary"] == "command_arguments"
    assert observation.status == "blocked"
    assert observation.success is False
    summary = metrics.summary()
    assert summary["tool_guard_fast_path_count"] == 1
    assert summary["tool_guard_fast_path_blocked_count"] == 1


def test_structured_tool_call_still_records_guard() -> None:
    decision = ExecutionBoundaryDecision(
        allowed=True,
        status="allowed",
        code="ok",
        reason="Execution boundary allowed tool dispatch.",
        boundary="sandbox",
        tool_name="sandbox_exec",
        access_mode="full_access",
        sanitized_arguments={"command": "echo ok"},
    )
    metrics, trace = _record(decision, _envelope("sandbox_exec", {"command": "echo ok"}))
    event = _event(trace)

    assert event["event_type"] == "guard_fast_path"
    assert event["summary_payload"]["tool_name"] == "sandbox_exec"
    assert metrics.summary()["tool_guard_fast_path_allowed_count"] == 1


def main() -> None:
    test_allowed_read_file_records_guard_fast_path()
    test_invalid_sandbox_exec_records_guard_fast_path_without_dispatch()
    test_structured_tool_call_still_records_guard()
    print("smoke_guard_fast_path ok")


if __name__ == "__main__":
    main()
