"""Offline smoke checks for task-scoped per-call execution grants."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.execution_boundary import evaluate_tool_execution_boundary
from core.initial_agent_turn import InitialAgentTurnResult, task_state_from_initial_tool_calls
from core.state import PlanStep, TaskState
from core.task_profile import TaskProfile
from core.tool_call_grants import (
    GRANT_COMPLETED,
    GRANT_FAILED,
    get_tool_call_grant,
    mark_tool_call_grant_state,
    register_tool_call_grant,
)
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_completion import record_completion_observation
from core.tool_execution_authorization import authorize_tool_execution
from tools.registry import get_tool_spec, get_unified_tool_specs


def _profile(task_type: str = "simple") -> TaskProfile:
    return TaskProfile(
        task_type=task_type,
        needs_web=False,
        has_url=False,
        has_search_engine_url=False,
        needs_code_edit=False,
        needs_validation=False,
        needs_git=False,
        user_intent_summary="smoke",
    )


def _call(call_id: str, name: str, arguments: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _initial_state(*calls: SimpleNamespace) -> TaskState:
    result = InitialAgentTurnResult(
        mode="tool_calls",
        tool_calls=tuple(calls),
        surface_tool_names=tuple(call.function.name for call in calls),
    )
    return task_state_from_initial_tool_calls(result, "structured smoke", tool_specs=get_unified_tool_specs())


def _envelope(call_id: str, name: str, arguments: dict[str, object], *, required: bool = True) -> ToolCallEnvelope:
    spec = get_tool_spec(name)
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name=name,
        tool_name=name,
        canonical_name=str(getattr(spec, "canonical_name", "") or name),
        executable_name=name,
        raw_arguments=json.dumps(arguments, ensure_ascii=False),
        parsed_arguments=dict(arguments),
        sanitized_arguments={},
        status=ToolCallStatus.EXECUTABLE,
        metadata={
            "tool_spec_found": True,
            "execution_grant_required": required,
            "grant_registered_arguments": dict(arguments),
        },
    )


def _register(state: TaskState, envelope: ToolCallEnvelope, *, step_index: int = 0):
    return register_tool_call_grant(
        state,
        call_id=envelope.call_id,
        provider_call_id=envelope.provider_call_id,
        canonical_name=envelope.canonical_name,
        executable_name=envelope.executable_name,
        arguments=envelope.parsed_arguments,
        source=envelope.source,
        step_index=step_index,
        raw_arguments=envelope.raw_arguments,
    )


@contextmanager
def _access_mode(value: str):
    previous = os.environ.get("AGENT_ACCESS_MODE")
    os.environ["AGENT_ACCESS_MODE"] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous


def test_initial_multi_tool_grants_are_independent() -> None:
    state = _initial_state(
        _call("call_read", "read_file", {"path": "core/runtime_metrics.py"}),
        _call("call_exec", "sandbox_exec", {"command": "python -c \"print('STEP5_MIXED_OK')\""}),
        _call("call_git", "git_diff", {}),
    )
    assert set(state.metadata["tool_call_grant_ledger"]) == {"call_read", "call_exec", "call_git"}
    assert state.metadata["tool_plan"]["primary_tool"] == "read_file"
    with _access_mode("full_access"):
        decision = authorize_tool_execution(
            task_state=state,
            tool_name="sandbox_exec",
            tool_call_envelope=_envelope("call_exec", "sandbox_exec", {"command": "python -c \"print('STEP5_MIXED_OK')\""}),
            sanitized_arguments={"command": "python -c \"print('STEP5_MIXED_OK')\""},
        )
    assert decision.allowed is True
    assert decision.reason == "authorized_by_task_scoped_tool_call_grant"
    assert decision.call_id == "call_exec"


def test_same_tool_calls_and_identity_mismatches() -> None:
    state = _initial_state(
        _call("call_a", "sandbox_exec", {"command": "echo 1"}),
        _call("call_b", "sandbox_exec", {"command": "echo 2"}),
    )
    with _access_mode("full_access"):
        for call_id, command in (("call_a", "echo 1"), ("call_b", "echo 2")):
            decision = authorize_tool_execution(
                task_state=state,
                tool_name="sandbox_exec",
                tool_call_envelope=_envelope(call_id, "sandbox_exec", {"command": command}),
                sanitized_arguments={"command": command},
            )
            assert decision.allowed is True

        missing = authorize_tool_execution(
            task_state=state,
            tool_name="sandbox_exec",
            tool_call_envelope=_envelope("call_missing", "sandbox_exec", {"command": "echo 1"}),
            sanitized_arguments={"command": "echo 1"},
        )
        wrong_tool = authorize_tool_execution(
            task_state=state,
            tool_name="write_file",
            tool_call_envelope=_envelope("call_a", "write_file", {"path": "x", "content": "x"}),
            sanitized_arguments={"path": "x", "content": "x"},
        )
        wrong_args = authorize_tool_execution(
            task_state=state,
            tool_name="sandbox_exec",
            tool_call_envelope=_envelope("call_a", "sandbox_exec", {"command": "echo changed"}),
            sanitized_arguments={"command": "echo changed"},
        )
    assert missing.allowed is False and missing.reason == "tool_call_grant_missing"
    assert wrong_tool.allowed is False and "name_mismatch" in wrong_tool.reason
    assert wrong_args.allowed is False and wrong_args.reason == "tool_call_grant_arguments_mismatch"


def test_task_isolation_access_and_safety_boundaries() -> None:
    first = _initial_state(_call("call_exec", "sandbox_exec", {"command": "echo ok"}))
    second = _initial_state(_call("call_other", "sandbox_exec", {"command": "echo ok"}))
    assert get_tool_call_grant(second, "call_exec") is None

    envelope = _envelope("call_exec", "sandbox_exec", {"command": "echo ok"})
    with _access_mode("read_only"):
        read_only = authorize_tool_execution(
            task_state=first,
            tool_name="sandbox_exec",
            tool_call_envelope=envelope,
            sanitized_arguments=envelope.parsed_arguments,
        )
    assert read_only.allowed is False and read_only.reason == "agent_access_mode_read_only"

    host_command_state = _initial_state(_call("call_host", "sandbox_exec", {"command": "rm -rf /"}))
    host_command_envelope = _envelope("call_host", "sandbox_exec", {"command": "rm -rf /"})
    with _access_mode("full_access"):
        host_command = evaluate_tool_execution_boundary(
            task_state=host_command_state,
            tool_name="sandbox_exec",
            arguments=host_command_envelope.parsed_arguments,
            tool_call_envelope=host_command_envelope,
            raw_arguments=host_command_envelope.raw_arguments,
        )
    assert host_command.allowed is True
    assert host_command.reason in {
        "authorized_by_task_scoped_tool_call_grant",
        "Execution boundary allowed tool dispatch.",
    }

    self_write_state = _initial_state(
        _call("call_write", "write_file", {"path": "core/loop.py", "content": "x"})
    )
    self_write_envelope = _envelope(
        "call_write",
        "write_file",
        {"path": "core/loop.py", "content": "x"},
    )
    with _access_mode("full_access"):
        self_write = evaluate_tool_execution_boundary(
            task_state=self_write_state,
            tool_name="write_file",
            arguments=self_write_envelope.parsed_arguments,
            tool_call_envelope=self_write_envelope,
            raw_arguments=self_write_envelope.raw_arguments,
        )
    assert self_write.allowed is False
    assert self_write.code == "agent_self_protected_path_blocked"


def test_grant_lifecycle_and_partial_observations() -> None:
    state = _initial_state(
        _call("call_read", "read_file", {"path": "core/runtime_metrics.py"}),
        _call("call_exec", "sandbox_exec", {"command": "missing-command"}),
    )
    update = mark_tool_call_grant_state(state, "call_read", GRANT_COMPLETED)
    assert update.allowed is True
    assert get_tool_call_grant(state, "call_read")["status"] == GRANT_COMPLETED
    stopped = mark_tool_call_grant_state(state, "call_exec", GRANT_FAILED)
    assert stopped.allowed is True
    assert get_tool_call_grant(state, "call_exec")["status"] == GRANT_FAILED
    record_completion_observation(
        state,
        "read_file",
        {"call_id": "call_read", "tool": "read_file", "success": True, "status": "success", "data": {"path": "core/runtime_metrics.py"}},
    )
    record_completion_observation(
        state,
        "sandbox_exec",
        {"call_id": "call_exec", "tool": "sandbox_exec", "success": False, "status": "failed", "error_code": "command_not_found", "data": {"exit_code": 127}},
    )
    observations = state.metadata["completion_observations"]
    assert [item["call_id"] for item in observations] == ["call_read", "call_exec"]
    assert observations[0]["success"] is True and observations[1]["success"] is False


def test_grants_are_structural_only() -> None:
    source = (PROJECT_ROOT / "core" / "tool_call_grants.py").read_text(encoding="utf-8")
    assert "user_input" not in source
    assert "user_goal" not in source
    assert "re.search" not in source
    state = _initial_state(_call("call", "read_file", {"path": "core/loop.py"}))
    unstructured = register_tool_call_grant(
        state,
        call_id="raw",
        provider_call_id="raw",
        canonical_name="read_file",
        executable_name="read_file",
        arguments={"path": "core/loop.py"},
        source="assistant_text",
    )
    assert unstructured.allowed is False
    assert unstructured.reason == "grant_requires_structured_tool_call"


def main() -> None:
    test_initial_multi_tool_grants_are_independent()
    test_same_tool_calls_and_identity_mismatches()
    test_task_isolation_access_and_safety_boundaries()
    test_grant_lifecycle_and_partial_observations()
    test_grants_are_structural_only()
    print("smoke_task_scoped_tool_call_grants ok")


if __name__ == "__main__":
    main()
