"""Step 6.2 closure Smoke for ToolCall execution and safety boundaries."""

from __future__ import annotations

import json
import os
import shlex
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_boundary import evaluate_tool_execution_boundary
from core.exact_tool_call_loop_guard import ExactToolCallLoopState
from core.loop import AgentLoop
from core.memory import Memory
from core.state import TaskState
from core.task_profile import TaskProfile
from core.tool_call_grants import GRANT_COMPLETED, mark_tool_call_grant_state, register_tool_call_grant
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, build_structured_tool_call_envelope
from core.trace import AgentTrace
from providers.mock import MockProvider, assistant_message
from tools.file_tools import write_file as real_write_file
from tools.shell_tools import sandbox_exec as real_sandbox_exec


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _state(*, requested_output_path: str = "") -> TaskState:
    profile = TaskProfile(
        task_type="simple",
        needs_web=False,
        has_url=False,
        has_search_engine_url=False,
        needs_code_edit=False,
        needs_validation=False,
        needs_git=False,
        needs_file_output=bool(requested_output_path),
        requested_output_path=requested_output_path,
        raw_requested_output_path=requested_output_path,
        user_intent_summary="execution boundary smoke",
    )
    return TaskState.create("execution boundary smoke", "simple", [], profile)


def _envelope(call_id: str, name: str, arguments: dict[str, Any]) -> ToolCallEnvelope:
    envelope = build_structured_tool_call_envelope(_call(call_id, name, arguments))
    envelope.metadata["execution_grant_required"] = True
    envelope.metadata["grant_registered_arguments"] = dict(arguments)
    return envelope


def _register(state: TaskState, envelope: ToolCallEnvelope) -> None:
    decision = register_tool_call_grant(
        state,
        call_id=envelope.call_id,
        provider_call_id=envelope.provider_call_id,
        canonical_name=envelope.canonical_name,
        executable_name=envelope.executable_name,
        arguments=envelope.parsed_arguments,
        source=envelope.source,
        raw_arguments=envelope.raw_arguments,
    )
    assert decision.allowed, decision


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


def test_distinct_identical_calls_in_one_batch() -> None:
    arguments = {"command": "printf same"}
    llm = MockProvider(
        responses=[
            assistant_message("", [_call("call-1", "sandbox_exec", arguments), _call("call-2", "sandbox_exec", arguments)]),
            assistant_message("done"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=3)
    dispatched: list[dict[str, Any]] = []

    def sandbox_exec(**kwargs: Any) -> dict[str, Any]:
        dispatched.append(dict(kwargs))
        return {"success": True, "data": {"exit_code": 0, **kwargs}}

    loop.tools["sandbox_exec"] = sandbox_exec
    with _access_mode("full_access"):
        assert loop.run("execute both structured calls") == "done"
    assert dispatched == [arguments, arguments]


def test_same_call_duplicate_delivery() -> None:
    loop = AgentLoop(MockProvider([]), Memory())
    state = _state()
    envelope = _envelope("duplicate-call", "sandbox_exec", {"command": "printf once"})
    _register(state, envelope)
    response_state = ExactToolCallLoopState()
    trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
    dispatched = 0

    def sandbox_exec(**_: Any) -> dict[str, Any]:
        nonlocal dispatched
        dispatched += 1
        return {"success": True, "data": {"exit_code": 0}}

    loop.tools["sandbox_exec"] = sandbox_exec
    with _access_mode("full_access"):
        first = loop._execute_tool_envelope(
            state,
            envelope,
            exact_tool_call_loop_state=response_state,
            trace=trace,
        )
        mark_tool_call_grant_state(state, envelope.call_id, GRANT_COMPLETED)
        second = loop._execute_tool_envelope(
            state,
            envelope,
            exact_tool_call_loop_state=response_state,
            trace=trace,
        )
        third = loop._execute_tool_envelope(
            state,
            envelope,
            exact_tool_call_loop_state=response_state,
            trace=trace,
        )
    assert first.success and second.success and third.success
    assert dispatched == 1
    assert second.data["idempotent_replay"] is True
    assert third.data["idempotent_replay"] is True
    assert second.data["identity"] == "structured_tool_call_id"
    assert response_state.consecutive_count == 1
    assert not any("doom_loop" in event.event_type for event in trace.events)


def _run_repeated_permission(action: str) -> tuple[list[Any], int, AgentTrace]:
    loop = AgentLoop(MockProvider([]), Memory())
    state = _state()
    state.metadata["permission_decisions"] = {"doom_loop": {"sandbox_exec": action}}
    trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
    response_state = ExactToolCallLoopState()
    dispatched = 0
    observations = []

    def sandbox_exec(**_: Any) -> dict[str, Any]:
        nonlocal dispatched
        dispatched += 1
        return {"success": True, "data": {"exit_code": 0}}

    loop.tools["sandbox_exec"] = sandbox_exec
    with _access_mode("full_access"):
        for index in range(1, 4):
            envelope = _envelope(f"repeat-{index}", "sandbox_exec", {"command": "printf repeat"})
            _register(state, envelope)
            observation = loop._execute_tool_envelope(
                state,
                envelope,
                exact_tool_call_loop_state=response_state,
                trace=trace,
                step=index,
            )
            observations.append(observation)
            mark_tool_call_grant_state(state, envelope.call_id, GRANT_COMPLETED if observation.success else "blocked")
    return observations, dispatched, trace


def test_repeated_call_permission_boundary() -> None:
    allowed, allowed_dispatches, allowed_trace = _run_repeated_permission("allow")
    assert [item.success for item in allowed] == [True, True, True]
    assert allowed_dispatches == 3
    denied, denied_dispatches, denied_trace = _run_repeated_permission("deny")
    assert [item.success for item in denied] == [True, True, False]
    assert denied_dispatches == 2
    assert denied[-1].status == "rejected"
    assert denied[-1].error_code == "permission_rejected"
    assert denied[-1].blocked_by == "permission"
    for trace in (allowed_trace, denied_trace):
        event_types = [event.event_type for event in trace.events]
        assert "doom_loop_permission_asked" in event_types
        assert "doom_loop_permission_decision" in event_types
        assert "exact_tool_call_loop_stop" not in event_types


def test_boundary_blocked_calls_still_reach_doom_loop() -> None:
    loop = AgentLoop(MockProvider([]), Memory())
    state = _state()
    state.metadata["permission_decisions"] = {"doom_loop": {"write_file": "allow"}}
    response_state = ExactToolCallLoopState()
    trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
    target = ROOT / "core" / "step62-doom-loop-blocked.txt"
    arguments = {"path": "core/step62-doom-loop-blocked.txt", "content": "must not be written"}
    dispatched: list[dict[str, Any]] = []
    observations = []
    counts = []

    def write_file(**kwargs: Any) -> dict[str, Any]:
        dispatched.append(dict(kwargs))
        return {"success": True, "data": {"path": kwargs["path"]}}

    loop.tools["write_file"] = write_file
    target.unlink(missing_ok=True)
    try:
        with _access_mode("full_access"):
            for index in range(1, 4):
                envelope = _envelope(f"blocked-write-{index}", "write_file", arguments)
                _register(state, envelope)
                observation = loop._execute_tool_envelope(
                    state,
                    envelope,
                    exact_tool_call_loop_state=response_state,
                    trace=trace,
                    step=index,
                )
                observations.append(observation)
                counts.append(response_state.consecutive_count)
        assert counts == [1, 2, 3]
        assert all(item.status == "blocked" for item in observations)
        assert all(item.error_code == "agent_self_protected_path_blocked" for item in observations)
        assert dispatched == []
        assert not target.exists()
        permission = next(
            event.data
            for event in trace.events
            if event.event_type == "doom_loop_permission_decision"
        )
        assert permission["consecutive_count"] == 3
        assert permission["action"] == "allow"
    finally:
        target.unlink(missing_ok=True)


def test_cross_continuation_repetition_does_not_accumulate() -> None:
    llm = MockProvider(
        responses=[
            assistant_message("", [_call(f"response-{index}", "sandbox_exec", {"command": "printf repeat"})])
            for index in range(1, 4)
        ]
        + [assistant_message("done")]
    )
    loop = AgentLoop(llm, Memory(), max_steps=5)
    dispatched = 0
    captured: dict[str, Any] = {}

    def sandbox_exec(**_: Any) -> dict[str, Any]:
        nonlocal dispatched
        dispatched += 1
        return {"success": True, "data": {"exit_code": 0}}

    def finish(self: AgentLoop, trace: AgentTrace, state: TaskState, answer: str) -> str:
        captured.update(trace=trace, state=state)
        return answer

    loop.tools["sandbox_exec"] = sandbox_exec
    loop._finish_with_trace = MethodType(finish, loop)
    with _access_mode("full_access"):
        assert loop.run("repeat across separate assistant responses") == "done"
    assert dispatched == 3
    assert len(llm.calls) == 4
    assert not any(
        event.event_type in {"doom_loop_permission_asked", "doom_loop_permission_decision"}
        for event in captured["trace"].events
    )


def test_write_content_and_path_integrity() -> None:
    loop = AgentLoop(MockProvider([]), Memory())
    state = _state(requested_output_path="outputs/legacy-B.txt")
    content = 'hello\n"metadata"\ntool_plan\nruntime\nstatus\nworld\n'
    arguments = {"path": "outputs/step62-model-A.txt", "content": content, "overwrite": True}
    envelope = _envelope("write-integrity", "write_file", arguments)
    _register(state, envelope)
    dispatched: list[dict[str, Any]] = []

    def write_file(**kwargs: Any) -> dict[str, Any]:
        dispatched.append(dict(kwargs))
        target = ROOT / kwargs["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(kwargs["content"], encoding="utf-8")
        return {"success": True, "data": {"path": str(target), "bytes_written": len(kwargs["content"])}}

    loop.tools["write_file"] = write_file
    target = ROOT / arguments["path"]
    try:
        with _access_mode("full_access"):
            observation = loop._execute_tool_envelope(state, envelope)
        assert observation.success
        assert dispatched == [arguments]
        assert dispatched[0]["content"] == content
        assert dispatched[0]["path"] == "outputs/step62-model-A.txt"
        assert target.read_text(encoding="utf-8") == content
        assert state.task_profile is not None
        assert state.task_profile.requested_output_path == "outputs/legacy-B.txt"
    finally:
        target.unlink(missing_ok=True)


def _mirror_entries() -> set[Path]:
    root = ROOT / ".horizon_runtime_validation"
    return set(root.rglob("*")) if root.exists() else set()


def test_agent_loop_sandbox_command_and_cwd_integrity() -> None:
    before_mirror_entries = _mirror_entries()
    with TemporaryDirectory(prefix="horizon-step62-") as directory:
        cwd = Path(directory)
        target = cwd / "model-target.py"
        content = 'print("MODEL_TARGET_EXECUTED")\n'
        command = f"python3 {shlex.quote(str(target))}"
        write_arguments = {"path": str(target), "content": content, "overwrite": True}
        exec_arguments = {"command": command, "cwd": str(cwd)}
        llm = MockProvider(
            responses=[
                assistant_message(
                    "",
                    [
                        _call("write-real-target", "write_file", write_arguments),
                        _call("execute-real-target", "sandbox_exec", exec_arguments),
                    ],
                ),
                assistant_message("done"),
            ]
        )
        loop = AgentLoop(llm, Memory(), max_steps=3)
        write_dispatches: list[dict[str, Any]] = []
        exec_dispatches: list[dict[str, Any]] = []
        exec_results: list[dict[str, Any]] = []

        def write_file(**kwargs: Any) -> dict[str, Any]:
            write_dispatches.append(dict(kwargs))
            return real_write_file(**kwargs)

        def sandbox_exec(**kwargs: Any) -> dict[str, Any]:
            exec_dispatches.append(dict(kwargs))
            result = real_sandbox_exec(**kwargs)
            exec_results.append(result)
            return result

        loop.tools["write_file"] = write_file
        loop.tools["sandbox_exec"] = sandbox_exec
        with _access_mode("full_access"):
            assert loop.run("create and validate the requested output") == "done"

        assert write_dispatches == [write_arguments]
        assert exec_dispatches == [exec_arguments]
        assert exec_dispatches[0]["command"] == command
        assert exec_dispatches[0]["cwd"] == str(cwd)
        assert str(target) in exec_dispatches[0]["command"]
        assert ".horizon_runtime_validation" not in json.dumps(exec_dispatches, ensure_ascii=False)
        assert target.read_text(encoding="utf-8") == content
        assert exec_results[0]["success"] is True
        assert exec_results[0]["data"]["stdout"].strip() == "MODEL_TARGET_EXECUTED"
    assert _mirror_entries() == before_mirror_entries


def test_rejected_actual_sandbox_call_is_not_rewritten() -> None:
    loop = AgentLoop(MockProvider([]), Memory())
    state = _state(requested_output_path="/tmp/legacy-validation-target.py")
    arguments = {"command": "python3 /tmp/model-target.py", "cwd": "/tmp"}
    envelope = _envelope("blocked-real-target", "sandbox_exec", arguments)
    _register(state, envelope)
    dispatched: list[dict[str, Any]] = []
    before_mirror_entries = _mirror_entries()

    def sandbox_exec(**kwargs: Any) -> dict[str, Any]:
        dispatched.append(dict(kwargs))
        return {"success": True, "data": {"exit_code": 0}}

    loop.tools["sandbox_exec"] = sandbox_exec
    with _access_mode("read_only"):
        observation = loop._execute_tool_envelope(state, envelope)

    assert observation.success is False
    assert observation.status == "blocked"
    assert observation.error_code == "agent_access_mode_read_only"
    assert dispatched == []
    assert envelope.parsed_arguments == arguments
    assert ".horizon_runtime_validation" not in json.dumps(envelope.parsed_arguments, ensure_ascii=False)
    assert _mirror_entries() == before_mirror_entries


def test_normal_safety_preserved() -> None:
    loop = AgentLoop(MockProvider([]), Memory())
    state = _state()
    unstructured = _envelope("bad-source", "sandbox_exec", {"command": "printf bad"})
    unstructured.source = ToolCallSource.USER_TEXT
    with _access_mode("full_access"):
        assert loop._execute_tool_envelope(state, unstructured).error_code == "tool_call_source_not_executable"

    unavailable = build_structured_tool_call_envelope(_call("missing", "not_registered", {}))
    with _access_mode("full_access"):
        assert loop._execute_tool_envelope(state, unavailable).error_code == "unknown_or_unexecutable_tool_call"

    with _access_mode("full_access"):
        invalid_command = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="sandbox_exec",
            arguments={"command": ""},
            tool_registry=loop.tools,
        )
        invalid_cwd = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="sandbox_exec",
            arguments={"command": "printf ok", "cwd": 7},
            tool_registry=loop.tools,
        )
        invalid_timeout = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="sandbox_exec",
            arguments={"command": "printf ok", "timeout": 301},
            tool_registry=loop.tools,
        )
        unsafe_path = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="write_file",
            arguments={"path": "core/loop.py", "content": "blocked"},
            raw_arguments=json.dumps({"path": "core/loop.py", "content": "blocked"}),
            tool_registry=loop.tools,
        )
    assert invalid_command.code == "invalid_command" and not invalid_command.allowed
    assert invalid_cwd.code == "invalid_cwd" and not invalid_cwd.allowed
    assert invalid_timeout.code == "invalid_timeout" and not invalid_timeout.allowed
    assert unsafe_path.code == "agent_self_protected_path_blocked" and not unsafe_path.allowed

    with _access_mode("read_only"):
        read_only = evaluate_tool_execution_boundary(
            task_state=state,
            tool_name="sandbox_exec",
            arguments={"command": "printf blocked"},
            tool_registry=loop.tools,
        )
    assert read_only.code == "agent_access_mode_read_only" and not read_only.allowed


def main() -> None:
    test_distinct_identical_calls_in_one_batch()
    test_same_call_duplicate_delivery()
    test_repeated_call_permission_boundary()
    test_boundary_blocked_calls_still_reach_doom_loop()
    test_cross_continuation_repetition_does_not_accumulate()
    test_write_content_and_path_integrity()
    test_agent_loop_sandbox_command_and_cwd_integrity()
    test_rejected_actual_sandbox_call_is_not_rewritten()
    test_normal_safety_preserved()
    print("smoke_execution_boundary_safety_alignment ok")


if __name__ == "__main__":
    main()
