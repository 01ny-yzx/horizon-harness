from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.build_step_contract import (
    build_step_contract_can_finalize,
    build_step_contract_from_initial_tool_calls,
    build_step_contract_from_task_state,
    current_build_step,
    resolve_build_contract_completion,
    update_build_step_contract_with_observation,
)
from core.prompt_pack import build_agent_continuation_pack


def _state(**metadata: object) -> SimpleNamespace:
    return SimpleNamespace(metadata=dict(metadata))


def _observation(
    tool_name: str,
    *,
    success: bool = True,
    status: str = "success",
    arguments: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_name=tool_name,
        status=status,
        success=success,
        arguments=arguments or {},
        error_code="",
        policy_code="",
        exit_code=0 if tool_name == "sandbox_exec" and success else None,
        output_path="",
    )


def test_file_read_command_exec_contract() -> None:
    state = _state(
        effective_required_capabilities=["file_read", "command_exec"],
        tool_arguments={
            "file_read": {"path": "core/runtime_metrics.py"},
            "command_exec": {"command": "python -c \"print('ok')\""},
        },
        capability_surface={
            "runtime_lane": "build",
            "capability_surface": "build",
            "effective_tools": ["read_file", "sandbox_exec"],
        },
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    assert [step["tool_name"] for step in contract["steps"]] == ["read_file", "sandbox_exec"]
    assert contract["steps"][0]["arguments"]["path"] == "core/runtime_metrics.py"
    assert contract["steps"][1]["arguments"]["command"] == "python -c \"print('ok')\""
    assert contract["current_step_index"] == 1
    assert contract["pending_count"] == 2
    assert contract["all_steps_completed"] is False
    assert contract["all_steps_resolved"] is False


def test_progress_after_read_file_success() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={"path": "core/runtime_metrics.py", "command": "python -c \"print('ok')\""},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    updated = update_build_step_contract_with_observation(contract, tool_name="read_file", observation=_observation("read_file"))
    assert updated["steps"][0]["status"] == "completed"
    assert updated["current_step_index"] == 2
    assert current_build_step(updated)["tool_name"] == "sandbox_exec"
    assert updated["completed_count"] == 1
    assert updated["pending_count"] == 1
    assert updated["all_steps_completed"] is False
    assert updated["all_steps_resolved"] is False


def test_all_steps_completed() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={"path": "core/runtime_metrics.py", "command": "python -c \"print('ok')\""},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    contract = update_build_step_contract_with_observation(contract, tool_name="read_file", observation=_observation("read_file"))
    contract = update_build_step_contract_with_observation(contract, tool_name="sandbox_exec", observation=_observation("sandbox_exec"))
    assert contract["all_steps_completed"] is True
    assert contract["all_steps_resolved"] is True
    assert contract["completed_count"] == 2
    assert contract["pending_count"] == 0
    assert contract["current_step_index"] == 0

    state.metadata["runtime_lane"] = "build"
    state.metadata["build_step_contract"] = contract
    state.metadata["completion_observations"] = [
        {"tool": "read_file", "success": True, "status": "success"},
        {"tool": "sandbox_exec", "success": True, "status": "success", "data": {"exit_code": 0}},
    ]
    completion = resolve_build_contract_completion(state)
    assert completion["all_steps_completed"] is True
    assert completion["has_pending_steps"] is False
    assert completion["required_capabilities_satisfied"] is True
    assert completion["can_finalize"] is True
    assert completion["reason"] == "build_steps_complete"


def test_completed_step_arguments_are_backfilled() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="read_file",
        observation=_observation("read_file"),
        execution_arguments={"path": "core/runtime_metrics.py"},
    )
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="sandbox_exec",
        observation=_observation("sandbox_exec"),
        execution_arguments={"command": "python -c \"print('mixed step12 ok')\""},
    )
    assert contract["steps"][0]["arguments"] == {"path": "core/runtime_metrics.py"}
    assert contract["steps"][0]["missing_arguments"] == []
    assert contract["steps"][1]["arguments"] == {"command": "python -c \"print('mixed step12 ok')\""}
    assert contract["steps"][1]["missing_arguments"] == []


def test_failed_step_blocks_remaining_steps_and_resolves() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={"path": "core/runtime_metrics.py", "command": "python -c \"print('ok')\""},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="read_file",
        observation=_observation("read_file", success=False, status="failed"),
    )
    assert contract["steps"][0]["status"] == "failed"
    assert contract["steps"][1]["status"] == "blocked"
    assert contract["steps"][1]["observation_summary"]["error_code"] == "blocked_by_previous_failure"
    assert contract["completed_count"] == 0
    assert contract["failed_count"] == 1
    assert contract["blocked_count"] == 1
    assert contract["all_steps_completed"] is False
    assert contract["all_steps_resolved"] is True
    assert contract["current_step_index"] == 0


def test_blocked_step_does_not_count_completed() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={"path": "core/runtime_metrics.py", "command": "python -c \"print('ok')\""},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="read_file",
        observation=_observation("read_file", success=False, status="blocked"),
    )
    assert contract["steps"][0]["status"] == "blocked"
    assert contract["steps"][1]["status"] == "blocked"
    assert contract["completed_count"] == 0
    assert contract["blocked_count"] == 2
    assert contract["all_steps_completed"] is False
    assert contract["all_steps_resolved"] is True


def test_file_read_file_write_contract_requires_write_file_available() -> None:
    state = _state(
        required_capabilities=["file_read", "file_write"],
        tool_arguments={"path": "notes.md", "content": "ok"},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file"]},
    )
    assert build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"]) == {}

    state.metadata["capability_surface"]["effective_tools"] = ["read_file", "write_file"]
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    assert [step["tool_name"] for step in contract["steps"]] == ["read_file", "write_file"]


def test_repeat_completed_tool_warning() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={"path": "core/runtime_metrics.py", "command": "python -c \"print('ok')\""},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    contract = update_build_step_contract_with_observation(contract, tool_name="read_file", observation=_observation("read_file"))
    contract = update_build_step_contract_with_observation(contract, tool_name="read_file", observation=_observation("read_file"))
    assert contract["last_progress"]["repeat_tool_warning"] is True
    assert contract["last_progress"]["repeat_tool_name"] == "read_file"
    assert contract["current_step_index"] == 2
    assert current_build_step(contract)["tool_name"] == "sandbox_exec"


def test_unexpected_tool_warning_without_progress() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={"path": "core/runtime_metrics.py", "command": "python -c \"print('ok')\""},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    contract = update_build_step_contract_with_observation(contract, tool_name="sandbox_exec", observation=_observation("sandbox_exec"))
    assert contract["last_progress"]["unexpected_tool_warning"] is True
    assert contract["last_progress"]["expected_tool"] == "read_file"
    assert contract["last_progress"]["actual_tool"] == "sandbox_exec"
    assert contract["steps"][0]["status"] == "pending"
    assert contract["current_step_index"] == 1


def test_missing_arguments_still_generates_contract() -> None:
    state = _state(
        required_capabilities=["file_read", "command_exec"],
        tool_arguments={"path": "core/runtime_metrics.py"},
        capability_surface={"runtime_lane": "build", "effective_tools": ["read_file", "sandbox_exec"]},
    )
    contract = build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"])
    assert [step["tool_name"] for step in contract["steps"]] == ["read_file", "sandbox_exec"]
    assert contract["steps"][1]["missing_arguments"] == ["command"]


def test_simple_lane_no_contract() -> None:
    for lane, tools, capabilities in (
        ("single_file_read", ["read_file"], ["file_read"]),
        ("file_output", ["write_file"], ["file_write"]),
        ("command_exec", ["sandbox_exec"], ["command_exec"]),
    ):
        state = _state(
            required_capabilities=capabilities,
            capability_surface={"runtime_lane": lane, "effective_tools": tools},
        )
        assert build_step_contract_from_task_state(state, capability_surface=state.metadata["capability_surface"]) == {}


def test_initial_turn_contract_sources() -> None:
    single = build_step_contract_from_initial_tool_calls(
        [
            {
                "call_id": "call_single_read",
                "tool_name": "read_file",
                "capability": "file_read",
                "arguments": {"path": "single.txt"},
            }
        ]
    )
    assert single["reason"] == "initial_tool_calls"
    assert single["contract_role"] == "execution_batch"
    assert len(single["steps"]) == 1
    assert build_step_contract_can_finalize(single) is False

    calls = [
        {"call_id": "call_read", "tool_name": "read_file", "capability": "file_read", "arguments": {"path": "a.py"}},
        {"call_id": "call_exec", "tool_name": "sandbox_exec", "capability": "command_exec", "arguments": {"command": "true"}},
    ]
    contract = build_step_contract_from_initial_tool_calls(calls)
    assert contract["reason"] == "initial_tool_calls"
    assert contract["contract_role"] == "execution_batch"
    assert build_step_contract_can_finalize(contract) is False
    assert [step["call_id"] for step in contract["steps"]] == ["call_read", "call_exec"]

def test_initial_turn_repeated_tool_calls_keep_distinct_call_ids() -> None:
    calls = [
        {"call_id": "call_status_1", "tool_name": "get_workspace_status", "capability": "memory"},
        {"call_id": "call_status_2", "tool_name": "get_workspace_status", "capability": "memory"},
    ]
    contract = build_step_contract_from_initial_tool_calls(calls)
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="get_workspace_status",
        observation=_observation("get_workspace_status"),
    )
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="get_workspace_status",
        observation=_observation("get_workspace_status"),
    )
    assert [step["call_id"] for step in contract["steps"]] == ["call_status_1", "call_status_2"]
    assert contract["all_steps_completed"] is True

    state = _state(
        build_step_contract=contract,
        completion_observations=[
            {"call_id": "call_status_1", "tool": "get_workspace_status", "status": "success", "success": True},
            {"call_id": "call_status_2", "tool": "get_workspace_status", "status": "success", "success": True},
        ],
    )
    completion = resolve_build_contract_completion(state)
    assert completion["contract_role"] == "execution_batch"
    assert completion["initial_tool_batch_complete"] is True
    assert completion["completed_count"] == 2
    assert completion["can_finalize"] is False
    assert completion["required_capabilities_satisfied"] is False
    assert completion["reason"] == "initial_tool_batch_complete"


def test_initial_tool_calls_failure_does_not_block_remaining_step() -> None:
    calls = [
        {"call_id": "call_failed", "tool_name": "read_file", "capability": "file_read"},
        {"call_id": "call_completed", "tool_name": "sandbox_exec", "capability": "command_exec"},
    ]
    contract = build_step_contract_from_initial_tool_calls(calls)
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="read_file",
        observation=_observation("read_file", success=False, status="failed"),
    )
    assert [step["status"] for step in contract["steps"]] == ["failed", "pending"]
    assert contract["blocked_count"] == 0
    assert contract["current_step_index"] == 2

    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="sandbox_exec",
        observation=_observation("sandbox_exec"),
    )
    assert [step["status"] for step in contract["steps"]] == ["failed", "completed"]
    assert contract["failed_count"] == 1
    assert contract["completed_count"] == 1
    assert contract["pending_count"] == 0
    assert contract["all_steps_resolved"] is True

    state = _state(
        build_step_contract=contract,
        completion_observations=[
            {"call_id": "call_failed", "tool": "read_file", "status": "failed", "success": False},
            {"call_id": "call_completed", "tool": "sandbox_exec", "status": "success", "success": True},
        ],
    )
    completion = resolve_build_contract_completion(state)
    assert completion["contract_role"] == "execution_batch"
    assert completion["initial_tool_batch_complete"] is True
    assert completion["failed_count"] == 1
    assert completion["can_finalize"] is False
    assert completion["required_capabilities_satisfied"] is False
    assert completion["reason"] == "initial_tool_batch_complete"


def test_initial_tool_batch_pending_cannot_finalize() -> None:
    contract = build_step_contract_from_initial_tool_calls(
        [
            {"call_id": "call_read", "tool_name": "read_file", "capability": "file_read"},
            {"call_id": "call_status", "tool_name": "get_context_status", "capability": "memory"},
        ]
    )
    contract = update_build_step_contract_with_observation(
        contract,
        tool_name="read_file",
        observation=_observation("read_file"),
    )
    state = _state(
        build_step_contract=contract,
        completion_observations=[
            {"call_id": "call_read", "tool": "read_file", "status": "success", "success": True},
        ],
    )
    completion = resolve_build_contract_completion(state)
    assert completion["initial_tool_batch_complete"] is False
    assert completion["pending_count"] == 1
    assert completion["can_finalize"] is False
    assert completion["reason"] == "initial_tool_batch_pending"


def test_initial_tool_batch_is_not_prompt_visible_as_task_contract() -> None:
    contract = build_step_contract_from_initial_tool_calls(
        [
            {"call_id": "call_read", "tool_name": "read_document", "capability": "file_read"},
            {"call_id": "call_status", "tool_name": "get_context_status", "capability": "memory"},
        ]
    )
    state = SimpleNamespace(
        task_type="document",
        workflow_name="",
        workflow_kind="",
        metadata={
            "runtime_lane": "explore",
            "build_step_contract_enabled": True,
            "build_step_contract": contract,
        },
    )
    pack = build_agent_continuation_pack(
        user_input="import the document",
        task_state=state,
        tools=[],
        memory_messages=[],
    )
    assert pack.metadata["build_step_contract_enabled"] is False
    rendered = "\n".join(str(message.get("content") or "") for message in pack.messages)
    assert '"reason":"initial_tool_calls"' not in rendered


def main() -> None:
    test_file_read_command_exec_contract()
    test_progress_after_read_file_success()
    test_all_steps_completed()
    test_completed_step_arguments_are_backfilled()
    test_failed_step_blocks_remaining_steps_and_resolves()
    test_blocked_step_does_not_count_completed()
    test_file_read_file_write_contract_requires_write_file_available()
    test_repeat_completed_tool_warning()
    test_unexpected_tool_warning_without_progress()
    test_missing_arguments_still_generates_contract()
    test_simple_lane_no_contract()
    test_initial_turn_contract_sources()
    test_initial_turn_repeated_tool_calls_keep_distinct_call_ids()
    test_initial_tool_calls_failure_does_not_block_remaining_step()
    test_initial_tool_batch_pending_cannot_finalize()
    test_initial_tool_batch_is_not_prompt_visible_as_task_contract()
    print("smoke_build_step_contract ok")


if __name__ == "__main__":
    main()
