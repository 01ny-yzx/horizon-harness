"""Smoke checks for unified intent capability routing."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_lane import RuntimeLane, resolve_runtime_lane
from core.simple_fast_path import should_use_simple_fast_path
from core.state import PlanStep, TaskState
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


def _state(goal: str, profile: TaskProfile | None = None, metadata: dict[str, object] | None = None) -> TaskState:
    state = TaskState.create(
        user_goal=goal,
        task_type=(profile.task_type if profile else "simple"),
        task_profile=profile or _profile(),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    state.metadata.update(metadata or {})
    return state


def main() -> None:
    state = _state("你好")
    decision = apply_unified_intent_capability_metadata(state)
    lane = resolve_runtime_lane(state)
    fast = should_use_simple_fast_path(state, lane, {})
    assert decision.planner_would_skip is True
    assert state.metadata["planner_skipped"] is True
    assert lane.lane is RuntimeLane.CHAT
    assert fast.enabled is True

    long_text = "这是一段普通聊天背景。" * 1000 + "只回答是否合理。"
    state = _state(long_text)
    decision = apply_unified_intent_capability_metadata(state)
    assert decision.planner_would_skip is True
    assert state.metadata["runtime_lane_hint"] == "chat"

    state = _state(
        "file-read-smoke",
        _profile(task_type="simple", tool_required=True, execution_mode="normal"),
        {"required_capabilities": ["file_read"]},
    )
    decision = apply_unified_intent_capability_metadata(state)
    lane = resolve_runtime_lane(state)
    assert decision.planner_would_skip is False
    assert decision.requires_file_read is True
    assert lane.lane is RuntimeLane.SINGLE_FILE_READ

    state = _state(
        "file-write-smoke",
        _profile(task_type="simple", tool_required=True, side_effect_required=True, execution_mode="normal"),
        {"required_capabilities": ["artifact_output", "file_write"]},
    )
    decision = apply_unified_intent_capability_metadata(state)
    assert decision.planner_would_skip is False
    assert decision.requires_file_write is True
    assert decision.requires_artifact_output is True

    state = _state(
        "command-smoke",
        _profile(task_type="simple", tool_required=True, side_effect_required=True, execution_mode="normal"),
        {"required_capabilities": ["command_exec"]},
    )
    decision = apply_unified_intent_capability_metadata(state)
    assert decision.planner_would_skip is False
    assert decision.requires_command_exec is True

    state = _state("invalid-smoke", metadata={"required_capabilities": ["unknown"]})
    decision = apply_unified_intent_capability_metadata(state)
    lane = resolve_runtime_lane(state)
    fast = should_use_simple_fast_path(state, lane, {})
    assert decision.planner_would_skip is False
    assert decision.fallback_reason == "invalid_capability_list"
    assert fast.enabled is False

    loop_text = (PROJECT_ROOT / "core" / "loop.py").read_text(encoding="utf-8")
    assert "decide_capability_pre_route" not in loop_text
    assert "capability_router_timeout" not in loop_text

    print("smoke_unified_intent_capability ok")


if __name__ == "__main__":
    main()
