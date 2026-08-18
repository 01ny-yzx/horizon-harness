"""Offline smoke checks for explicit runtime lane profiles."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_lane import resolve_runtime_lane
from core.state import PlanStep, TaskState
from core.structured_intent_access import structured_tool_plan
from core.task_profile import TaskProfile
from core.unified_intent_capability import apply_unified_intent_capability_metadata


def _profile(**overrides: object) -> TaskProfile:
    values = {
        "task_type": "simple",
        "needs_web": False,
        "has_url": False,
        "has_search_engine_url": False,
        "needs_code_edit": False,
        "needs_validation": False,
        "needs_git": False,
        "needs_file_output": False,
        "user_intent_summary": "smoke",
        "side_effect_required": False,
        "tool_required": False,
        "execution_mode": "text_only",
        "structured_intent_type": "simple_answer",
        "structured_task_type": "simple",
        "structured_workflow_kind": "simple",
    }
    values.update(overrides)
    return TaskProfile(**values)


def _state(*, capabilities: list[str], primary_capability: str, primary_tool: str, side_effect_required: bool) -> TaskState:
    state = TaskState.create(
        user_goal="runtime lane smoke",
        task_type="simple",
        task_profile=_profile(tool_required=bool(capabilities), side_effect_required=side_effect_required, execution_mode="normal"),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    state.metadata.update(
        {
            "required_capabilities": list(capabilities),
            "primary_capability": primary_capability,
            "primary_tool": primary_tool,
            "tool_required": bool(capabilities),
            "side_effect_required": side_effect_required,
            "tool_plan": {
                "primary_capability": primary_capability,
                "primary_tool": primary_tool,
                "tool_priority": [primary_tool] if primary_tool else [],
                "supporting_capabilities": list(capabilities),
            },
        }
    )
    apply_unified_intent_capability_metadata(state)
    return state


def test_chat_lane() -> None:
    state = _state(capabilities=[], primary_capability="", primary_tool="", side_effect_required=False)
    lane = resolve_runtime_lane(state, tool_plan=structured_tool_plan(state))
    assert lane.lane.value == "chat"
    assert lane.metadata["lane_profile"] == "chat"
    assert lane.metadata["tool_required"] is False
    assert lane.metadata["side_effect_required"] is False
    assert lane.allows_read is False
    assert lane.allows_write is False
    assert lane.allows_exec is False


def test_single_file_read_lane() -> None:
    state = _state(capabilities=["file_read"], primary_capability="file_read", primary_tool="read_file", side_effect_required=False)
    lane = resolve_runtime_lane(state, tool_plan=structured_tool_plan(state))
    assert lane.lane.value == "single_file_read"
    assert lane.metadata["budget_profile"] == "single_file_read_budget"
    assert lane.metadata["tool_required"] is True
    assert lane.metadata["side_effect_required"] is False
    assert lane.allows_read is True
    assert lane.allows_write is False
    assert lane.allows_exec is False

    document_state = _state(capabilities=["file_read"], primary_capability="file_read", primary_tool="read_document", side_effect_required=False)
    document_lane = resolve_runtime_lane(document_state, tool_plan=structured_tool_plan(document_state))
    assert document_lane.lane.value == "single_file_read"
    assert document_lane.metadata["primary_tool"] == "read_document"

    capability_only = _state(capabilities=["file_read"], primary_capability="file_read", primary_tool="", side_effect_required=False)
    capability_only_lane = resolve_runtime_lane(capability_only, tool_plan=structured_tool_plan(capability_only))
    assert capability_only_lane.lane.value == "single_file_read"
    assert capability_only_lane.metadata["primary_tool"] == ""


def test_file_output_lane() -> None:
    state = _state(capabilities=["file_write"], primary_capability="file_write", primary_tool="write_file", side_effect_required=True)
    lane = resolve_runtime_lane(state, tool_plan=structured_tool_plan(state))
    assert lane.lane.value == "file_output"
    assert lane.metadata["budget_profile"] == "file_output_budget"
    assert lane.metadata["tool_required"] is True
    assert lane.metadata["side_effect_required"] is True
    assert lane.allows_read is False
    assert lane.allows_write is True
    assert lane.allows_exec is False


def test_command_exec_lane() -> None:
    state = _state(capabilities=["command_exec"], primary_capability="command_exec", primary_tool="sandbox_exec", side_effect_required=True)
    lane = resolve_runtime_lane(state, tool_plan=structured_tool_plan(state))
    assert lane.lane.value == "command_exec"
    assert lane.metadata["budget_profile"] == "command_exec_budget"
    assert lane.metadata["tool_required"] is True
    assert lane.metadata["side_effect_required"] is True
    assert lane.allows_read is False
    assert lane.allows_write is False
    assert lane.allows_exec is True


def test_code_edit_lane() -> None:
    state = _state(capabilities=["code_edit"], primary_capability="code_edit", primary_tool="replace_in_file", side_effect_required=True)
    state.metadata["tool_plan"]["tool_priority"] = ["read_file", "write_file", "replace_in_file", "sandbox_exec"]
    apply_unified_intent_capability_metadata(state)
    lane = resolve_runtime_lane(state, tool_plan=structured_tool_plan(state))
    assert lane.lane.value == "code_edit"
    assert lane.metadata["budget_profile"] == "code_edit_budget"
    assert state.metadata["primary_capabilities"] == ["code_edit"]
    assert set(state.metadata["supporting_capabilities"]) >= {"file_read", "file_write", "command_exec"}
    assert lane.allows_read is True
    assert lane.allows_write is True
    assert lane.allows_exec is True


def test_mixed_side_effect_falls_back_to_build() -> None:
    state = _state(capabilities=["file_write", "command_exec"], primary_capability="file_write", primary_tool="write_file", side_effect_required=True)
    state.metadata["tool_plan"]["tool_priority"] = ["write_file", "sandbox_exec"]
    apply_unified_intent_capability_metadata(state)
    lane = resolve_runtime_lane(state, tool_plan=structured_tool_plan(state))
    assert lane.lane.value == "build"
    assert lane.metadata["lane_profile"] == "build"


def main() -> None:
    test_chat_lane()
    test_single_file_read_lane()
    test_file_output_lane()
    test_command_exec_lane()
    test_code_edit_lane()
    test_mixed_side_effect_falls_back_to_build()
    print("smoke_runtime_lane_profile ok")


if __name__ == "__main__":
    main()
