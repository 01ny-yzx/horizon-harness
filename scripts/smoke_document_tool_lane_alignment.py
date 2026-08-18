"""Contract smoke for preserving explicit read_document tool selection."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from types import SimpleNamespace
from core.initial_agent_turn import InitialAgentTurnResult, task_state_from_initial_tool_calls
from core.build_step_contract import build_step_contract_from_initial_tool_calls
from core.capability_surface import build_capability_surface_summary, tool_priority_for_mixed_capabilities
from core.runtime_lane import resolve_runtime_lane
from core.state import PlanStep, TaskState
from core.structured_intent_access import structured_tool_plan
from core.task_profile import TaskProfile
from core.unified_intent_capability import apply_unified_intent_capability_metadata, normalize_primary_tool
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs


def _initial_tool_result(primary_tool: str, path: str = "sample.xlsx") -> InitialAgentTurnResult:
    call = SimpleNamespace(
        id="read-document",
        function=SimpleNamespace(name=primary_tool, arguments=json.dumps({"path": path})),
    )
    return InitialAgentTurnResult(mode="tool_calls", tool_calls=(call,), surface_tool_names=(primary_tool,))


def _capability_only_state() -> TaskState:
    profile = TaskProfile(
        task_type="simple",
        needs_web=False,
        has_url=False,
        has_search_engine_url=False,
        needs_code_edit=False,
        needs_validation=False,
        needs_git=False,
        needs_file_output=False,
        user_intent_summary="structured capability smoke",
        tool_required=True,
        side_effect_required=False,
        execution_mode="normal",
        structured_intent_type="file_read",
        structured_task_type="simple",
        structured_workflow_kind="simple",
    )
    state = TaskState.create("smoke", "simple", [PlanStep(index=1, name="read", instruction="read")], profile)
    state.metadata.update(
        {
            "required_capabilities": ["file_read"],
            "primary_capability": "file_read",
            "tool_required": True,
            "side_effect_required": False,
            "tool_plan": {"primary_capability": "file_read", "tool_priority": []},
        }
    )
    return state


def main() -> None:
    specs = get_unified_tool_specs()
    schemas = get_unified_tool_schemas()
    schema_names = [schema["function"]["name"] for schema in schemas]
    assert {"read_file", "read_document"} <= set(schema_names)
    assert tool_priority_for_mixed_capabilities(["file_read"], []) == ["read_file", "read_document"]

    assert normalize_primary_tool("file_read", "") == ""
    assert normalize_primary_tool("file_read", "read_file") == "read_file"
    assert normalize_primary_tool("file_read", "read_document") == "read_document"
    generic = _capability_only_state()
    generic_decision = apply_unified_intent_capability_metadata(generic)
    assert generic_decision.primary_tool == ""
    plan = structured_tool_plan(generic)
    assert plan.get("primary_tool", "") == ""
    lane = resolve_runtime_lane(generic, tool_plan=plan)
    assert lane.lane.value == "single_file_read" and lane.metadata["primary_tool"] == ""
    surface = build_capability_surface_summary(
        runtime_lane=lane.lane.value,
        primary_capability="file_read",
        primary_tool="",
        required_capabilities=["file_read"],
        scoped_tool_names=["read_file", "read_document"],
    )
    assert surface["effective_tools"] == ["read_file", "read_document"]
    assert surface["primary_tool"] == ""

    first = _initial_tool_result("read_document")
    state = task_state_from_initial_tool_calls(first, "parse document", tool_specs=specs)
    unified = apply_unified_intent_capability_metadata(state)
    assert unified.primary_tool == "read_document"
    plan = structured_tool_plan(state)
    assert plan["primary_tool"] == "read_document"
    lane = resolve_runtime_lane(state, tool_plan=plan)
    assert lane.lane.value == "explore"
    state.metadata["runtime_lane"] = "single_file_read"
    state.metadata["normal_completion_owner"] = "agent_continuation"
    state.metadata["task_contract_authoritative"] = False
    state.metadata["initial_tool_batch"] = True
    state.metadata["build_step_contract"] = (
        build_step_contract_from_initial_tool_calls(
            [
                {
                    "call_id": "read-document",
                    "tool_name": "read_document",
                    "capability": "file_read",
                    "arguments": {"path": "sample.xlsx"},
                }
            ],
        )
    )
    text_first = _initial_tool_result("read_file", "sample.txt")
    text_state = task_state_from_initial_tool_calls(text_first, "read text", tool_specs=specs)
    text_unified = apply_unified_intent_capability_metadata(text_state)
    text_lane = resolve_runtime_lane(text_state, tool_plan=structured_tool_plan(text_state))

    import core.capability_surface as capability_surface_module
    import core.runtime_lane as runtime_lane_module
    import core.unified_intent_capability as unified_module

    for module in (capability_surface_module, runtime_lane_module, unified_module):
        source = inspect.getsource(module)
        assert "re.search" not in source
        assert "user_input" not in source
        assert "user_goal" not in source
        assert "endswith(\".xlsx\")" not in source
        assert "endswith(\".pdf\")" not in source
    print("smoke_document_tool_lane_alignment ok")


if __name__ == "__main__":
    main()
