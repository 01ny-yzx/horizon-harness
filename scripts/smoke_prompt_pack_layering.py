"""Offline smoke checks for stage-specific prompt packs."""

from __future__ import annotations

import sys
import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true"}
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.execution_boundary import evaluate_tool_execution_boundary
from core.finalization_context_snapshot import build_finalization_context_snapshot
from core.state import PlanStep, TaskState
from core.structured_intent_access import structured_tool_plan
from core.task_profile import TaskProfile
from core.runtime_lane import resolve_runtime_lane
from core.simple_fast_path import should_use_simple_fast_path
from core.tool_call_schema import build_structured_tool_call_envelope
from core.tool_execution_authorization import authorize_tool_execution
from core.unified_intent_capability import apply_unified_intent_capability_metadata
from core.prompt_pack import (
    build_final_answer_pack,
    build_tool_call_pack,
    terminal_final_answer_action_commitment_reason,
)
from prompts.system_prompt import SYSTEM_PROMPT
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _tool_schema(name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }


def _text(pack: object) -> str:
    messages = getattr(pack, "messages")
    return "\n".join(str(message.get("content") or "") for message in messages)


def _task_state(**metadata: object) -> SimpleNamespace:
    return SimpleNamespace(metadata=dict(metadata))


def _final_snapshot(
    user_input: str,
    observations: list[dict[str, object]],
    **metadata: object,
):
    state = SimpleNamespace(
        task_id="prompt-pack-smoke",
        user_goal=user_input,
        metadata={
            **dict(metadata),
            "completion_observations": observations,
            "structured_tool_call_ids": [
                str(item.get("call_id") or "")
                for item in observations
                if item.get("call_id")
            ],
        },
    )
    return build_finalization_context_snapshot(
        user_request=user_input,
        task_state=state,
        finalization_mode=str(metadata.get("finalization_mode") or ""),
    )


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


def test_tool_call_pack_contains_only_current_command_tool() -> None:
    pack = build_tool_call_pack(
        user_input="执行命令：echo ok",
        task_state=_task_state(runtime_lane="build"),
        tools=[_tool_schema("sandbox_exec")],
        memory_messages=[{"role": "user", "content": "执行命令：echo ok"}],
    )
    text = _text(pack)

    assert pack.stage == "agent_continuation"
    assert "ToolPlan" not in text
    assert "RuntimePlan" not in text
    assert "current plan step" not in text.lower()
    assert pack.metadata["tool_names"] == ["sandbox_exec"]
    assert "read_file" not in text
    assert "write_file" not in text
    assert "git_status" not in text
    assert "browser" not in text
    assert "database" not in text
    assert "final-answer stage" not in text


def test_tool_call_pack_contains_only_current_write_tool() -> None:
    pack = build_tool_call_pack(
        user_input="写一个桌面文件",
        task_state=_task_state(runtime_lane="build"),
        tools=[_tool_schema("write_file")],
        memory_messages=[{"role": "user", "content": "写一个桌面文件"}],
    )
    text = _text(pack)

    assert "write_file" not in text
    assert pack.metadata["tool_names"] == ["write_file"]


def test_final_answer_pack_has_evidence_without_tool_schema() -> None:
    observation = {
        "call_id": "read-main",
        "tool": "read_file",
        "success": True,
        "status": "success",
        "data": {"path": "main.py", "content": "print(1)"},
    }
    pack = build_final_answer_pack(
        snapshot=_final_snapshot("读取 main.py 并总结", [observation]),
    )
    text = _text(pack)

    assert pack.stage == "final_answer"
    assert "读取 main.py 并总结" in text
    assert "print(1)" in text
    assert '"parameters"' not in text
    assert "intent_schema_v1" not in text
    assert "Call exactly one available tool" not in text


def test_final_answer_pack_filters_old_memory_and_protocol() -> None:
    observation = {
        "call_id": "read-visible",
        "tool": "read_file",
        "success": True,
        "status": "success",
        "data": {"content": "visible"},
    }
    pack = build_final_answer_pack(
        snapshot=_final_snapshot(
            "总结文件",
            [observation],
            finalization_mode="satisfied_explore_file_read",
        ),
    )
    text = _text(pack)

    assert "总结文件" in text
    assert "visible" in text
    assert "Context Summary" not in text
    assert "让我尝试获取完整内容" not in text
    assert "planner internal message" not in text
    assert "tool_calls" not in text
    assert '"parameters"' not in text


def test_final_answer_pack_terminal_truncated_evidence_rules() -> None:
    pack = build_final_answer_pack(
        snapshot=_final_snapshot(
            "总结文件",
            [
                {
                    "call_id": "read-truncated",
                    "tool": "read_file",
                    "success": True,
                    "status": "success",
                    "truncated": True,
                    "data": {"content": "visible", "truncated": True},
                }
            ],
            finalization_mode="satisfied_explore_file_read",
        ),
    )
    text = _text(pack).lower()

    assert "answer only from the supplied current-request finalization context" in text
    assert "do not request, promise, describe, or emit another toolcall" in text
    assert '"truncated":true' in text
    assert terminal_final_answer_action_commitment_reason("文件内容被截断了，让我尝试获取完整内容。")


def test_minimal_integration_packs_keep_required_tools() -> None:
    command_pack = build_tool_call_pack(
        user_input="执行命令：echo ok",
        task_state=_task_state(),
        tools=[_tool_schema("sandbox_exec")],
        memory_messages=[],
    )
    read_pack = build_tool_call_pack(
        user_input="读取 main.py",
        task_state=_task_state(),
        tools=[_tool_schema("read_file")],
        memory_messages=[],
    )
    write_pack = build_tool_call_pack(
        user_input="写文件",
        task_state=_task_state(),
        tools=[_tool_schema("write_file")],
        memory_messages=[],
    )

    assert command_pack.metadata["tool_names"] == ["sandbox_exec"]
    assert read_pack.metadata["tool_names"] == ["read_file"]
    assert write_pack.metadata["tool_names"] == ["write_file"]


def test_access_mode_contract_is_present() -> None:
    read_pack = build_tool_call_pack(
        user_input="分析",
        task_state=_task_state(),
        tools=[_tool_schema("read_file")],
        memory_messages=[],
        access_mode="read_only",
    )
    full_pack = build_tool_call_pack(
        user_input="实施",
        task_state=_task_state(),
        tools=[_tool_schema("write_file")],
        memory_messages=[],
        access_mode="full_access",
    )
    assert "Current access mode is read_only" in _text(read_pack)
    assert "without performing changes" in _text(read_pack)
    assert read_pack.metadata["access_mode"] == "read_only"
    assert "Current access mode is full_access" in _text(full_pack)
    assert "does not bypass Runtime authorization" in _text(full_pack)
    assert full_pack.metadata["access_mode"] == "full_access"


def test_file_write_pack_preserves_tool_plan_authorization() -> None:
    profile = _profile(
        needs_file_output=True,
        requested_output_path="/Users/yzx/Desktop/prompt_pack_test.md",
        raw_requested_output_path="/Users/yzx/Desktop/prompt_pack_test.md",
        output_format="markdown",
        output_target="file",
        side_effect_required=True,
        tool_required=True,
        execution_mode="normal",
        structured_intent_type="file_write",
        structured_task_type="simple",
        structured_workflow_kind="file_output_only",
    )
    state = TaskState.create(
        user_goal="在桌面写一个文件 /Users/yzx/Desktop/prompt_pack_test.md，内容是：prompt pack ok。",
        task_type="simple",
        task_profile=profile,
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    state.metadata.update(
        {
            "required_capabilities": ["file_write"],
            "primary_capability": "",
            "primary_tool": "write_file",
            "tool_required": True,
            "side_effect_required": True,
            "tool_plan": {
                "primary_tool": "write_file",
                "tool_priority": ["write_file"],
            },
            "capability_routing": {
                "required_capabilities": ["file_write"],
                "primary_capability": "",
                "primary_tool": "write_file",
                "tool_plan": {
                    "primary_tool": "write_file",
                    "tool_priority": ["write_file"],
                },
            },
        }
    )
    decision = apply_unified_intent_capability_metadata(state)
    plan = structured_tool_plan(state)

    assert decision.requires_file_write is True
    assert state.metadata["primary_capability"] == "file_write"
    assert state.metadata["primary_tool"] == "write_file"
    assert "file_write" in state.metadata["required_capabilities"]
    assert plan["primary_capability"] == "file_write"
    assert plan["primary_tool"] == "write_file"
    assert "write_file" in plan["tool_priority"]
    assert "file_write" in plan["supporting_capabilities"]
    assert state.metadata["capability_routing"]["primary_capability"] == "file_write"
    assert state.metadata["capability_routing"]["primary_tool"] == "write_file"
    assert state.metadata["capability_routing"]["tool_plan"]["primary_capability"] == "file_write"

    auth = authorize_tool_execution(task_state=state, tool_name="write_file")
    assert auth.allowed is True
    assert "tool_execution_not_authorized" != auth.code

    sandbox = authorize_tool_execution(task_state=state, tool_name="sandbox_exec")
    assert sandbox.allowed is True


def test_file_write_arguments_are_not_runtime_projected() -> None:
    profile = _profile(
        side_effect_required=True,
        tool_required=True,
        execution_mode="normal",
        structured_intent_type="file_write",
        structured_task_type="simple",
        structured_workflow_kind="",
    )
    state = TaskState.create(
        user_goal="write structured file output",
        task_type="simple",
        task_profile=profile,
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    state.metadata.update(
        {
            "required_capabilities": ["file_write"],
            "primary_capability": "file_write",
            "primary_tool": "write_file",
            "tool_required": True,
            "side_effect_required": True,
            "tool_plan": {"primary_capability": "file_write", "primary_tool": "write_file", "tool_priority": ["write_file"]},
            "capability_routing": {"required_capabilities": ["file_write"], "tool_plan": {}},
        }
    )
    envelope = build_structured_tool_call_envelope(
        SimpleNamespace(
            id="call_write",
            function=SimpleNamespace(
                name="write_file",
                arguments='{"path":"/Users/yzx/Desktop/prompt_pack_authorization_smoke.md","content":"ok"}',
            ),
        )
    )
    before_profile = deepcopy(state.task_profile)
    before_plan = deepcopy(state.metadata["tool_plan"])
    boundary = evaluate_tool_execution_boundary(
        task_state=state,
        tool_name=envelope.executable_name or envelope.tool_name,
        arguments=envelope.parsed_arguments,
        raw_arguments=envelope.raw_arguments,
    )

    assert boundary.allowed is True
    assert boundary.sanitized_arguments == envelope.parsed_arguments
    assert state.task_profile == before_profile
    assert state.metadata["tool_plan"] == before_plan

    sandbox = evaluate_tool_execution_boundary(
        task_state=state,
        tool_name="sandbox_exec",
        arguments={"command": "echo no"},
        raw_arguments='{"command":"echo no"}',
    )
    assert sandbox.allowed is True


def test_unplanned_file_write_boundary_does_not_repair_tool_plan() -> None:
    state = TaskState.create(
        user_goal="write output",
        task_type="simple",
        task_profile=_profile(
            needs_file_output=True,
            requested_output_path="output.txt",
            raw_requested_output_path="output.txt",
            output_filename="output.txt",
            tool_required=True,
            side_effect_required=True,
            execution_mode="normal",
        ),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    state.metadata.update(
        {
            "required_capabilities": ["file_read"],
            "primary_capability": "file_read",
            "primary_tool": "read_file",
            "tool_required": True,
            "side_effect_required": True,
            "tool_plan": {
                "primary_capability": "file_read",
                "primary_tool": "read_file",
                "tool_priority": ["read_file"],
            },
            "capability_routing": {
                "required_capabilities": ["file_read"],
                "primary_capability": "file_read",
                "primary_tool": "read_file",
            },
        }
    )
    before_profile = deepcopy(state.task_profile)
    before_metadata = deepcopy(state.metadata)

    boundary = evaluate_tool_execution_boundary(
        task_state=state,
        tool_name="write_file",
        arguments={"path": "output.txt", "content": "hello"},
        raw_arguments='{"path":"output.txt","content":"hello"}',
    )

    assert boundary.allowed is True
    assert boundary.sanitized_arguments == {"path": "output.txt", "content": "hello"}
    assert state.task_profile == before_profile
    assert state.metadata["tool_plan"] == before_metadata["tool_plan"]
    assert (
        state.metadata["capability_routing"]
        == before_metadata["capability_routing"]
    )
    assert (
        state.metadata["required_capabilities"]
        == before_metadata["required_capabilities"]
    )
    assert not any(
        key.startswith("toolplan_runtime" + "_recovery_")
        for key in state.metadata
    )


def test_file_read_side_effect_required_cleanup() -> None:
    read_state = TaskState.create(
        user_goal="读取文件",
        task_type="simple",
        task_profile=_profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    read_state.metadata.update(
        {
            "required_capabilities": ["file_read"],
            "primary_capability": "file_read",
            "primary_tool": "read_file",
            "tool_required": True,
            "side_effect_required": True,
        }
    )
    apply_unified_intent_capability_metadata(read_state)
    assert read_state.metadata["tool_required"] is True
    assert read_state.metadata["side_effect_required"] is False

    write_state = TaskState.create(
        user_goal="写文件",
        task_type="simple",
        task_profile=_profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    write_state.metadata.update({"required_capabilities": ["file_write"], "primary_tool": "write_file", "side_effect_required": True})
    apply_unified_intent_capability_metadata(write_state)
    assert write_state.metadata["side_effect_required"] is True

    command_state = TaskState.create(
        user_goal="执行命令",
        task_type="simple",
        task_profile=_profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    command_state.metadata.update({"required_capabilities": ["command_exec"], "primary_tool": "sandbox_exec", "side_effect_required": True})
    apply_unified_intent_capability_metadata(command_state)
    assert command_state.metadata["side_effect_required"] is True


def test_file_read_keeps_side_effect_false_across_runtime_metadata() -> None:
    read_state = TaskState.create(
        user_goal="读取 core/prompt_pack.py，并总结这个文件主要负责什么。",
        task_type="simple",
        task_profile=_profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    read_state.metadata.update(
        {
            "required_capabilities": ["file_read"],
            "primary_capability": "file_read",
            "primary_tool": "read_file",
            "tool_required": True,
            "side_effect_required": True,
            "capability_routing": {"required_capabilities": ["file_read"], "tool_plan": {}},
            "tool_plan": {
                "primary_capability": "file_read",
                "primary_tool": "read_file",
                "tool_priority": ["read_file"],
                "supporting_capabilities": ["file_read"],
            },
        }
    )
    apply_unified_intent_capability_metadata(read_state)
    runtime_lane = resolve_runtime_lane(read_state, tool_plan=structured_tool_plan(read_state))
    simple_fast_path = should_use_simple_fast_path(read_state, runtime_lane, structured_tool_plan(read_state))

    assert read_state.metadata["tool_required"] is True
    assert read_state.metadata["side_effect_required"] is False
    assert read_state.metadata["unified_intent_capability"]["metadata"]["side_effect_required"] is False
    assert read_state.metadata["capability_routing"]["side_effect_required"] is False
    assert read_state.task_profile.tool_required is True
    assert read_state.task_profile.side_effect_required is False
    assert runtime_lane.metadata["tool_required"] is True
    assert runtime_lane.metadata["side_effect_required"] is False
    assert simple_fast_path.metadata["tool_required"] is True
    assert simple_fast_path.metadata["side_effect_required"] is False

    write_state = TaskState.create(
        user_goal="写文件",
        task_type="simple",
        task_profile=_profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    write_state.metadata.update(
        {
            "required_capabilities": ["file_write"],
            "primary_capability": "file_write",
            "primary_tool": "write_file",
            "tool_required": True,
            "side_effect_required": True,
            "tool_plan": {"primary_capability": "file_write", "primary_tool": "write_file", "tool_priority": ["write_file"]},
        }
    )
    apply_unified_intent_capability_metadata(write_state)
    assert write_state.metadata["side_effect_required"] is True
    assert write_state.task_profile.side_effect_required is True

    command_state = TaskState.create(
        user_goal="执行命令",
        task_type="simple",
        task_profile=_profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    command_state.metadata.update(
        {
            "required_capabilities": ["command_exec"],
            "primary_capability": "command_exec",
            "primary_tool": "sandbox_exec",
            "tool_required": True,
            "side_effect_required": True,
            "tool_plan": {
                "primary_capability": "command_exec",
                "primary_tool": "sandbox_exec",
                "tool_priority": ["sandbox_exec"],
            },
        }
    )
    apply_unified_intent_capability_metadata(command_state)
    assert command_state.metadata["side_effect_required"] is True
    assert command_state.task_profile.side_effect_required is True


def main() -> None:
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspaces").get_context("smoke", "prompt-pack"))
            test_tool_call_pack_contains_only_current_command_tool()
            test_tool_call_pack_contains_only_current_write_tool()
            test_final_answer_pack_has_evidence_without_tool_schema()
            test_final_answer_pack_filters_old_memory_and_protocol()
            test_final_answer_pack_terminal_truncated_evidence_rules()
            test_minimal_integration_packs_keep_required_tools()
            test_access_mode_contract_is_present()
            test_file_write_pack_preserves_tool_plan_authorization()
            test_file_write_arguments_are_not_runtime_projected()
            test_unplanned_file_write_boundary_does_not_repair_tool_plan()
            test_file_read_side_effect_required_cleanup()
            test_file_read_keeps_side_effect_false_across_runtime_metadata()
            for marker in (
                "ToolPlan",
                "RuntimePlan",
                "primary tool",
                "primary capability",
                "current plan step",
                "current phase",
            ):
                assert marker.lower() not in SYSTEM_PROMPT.lower(), marker
    finally:
        os.chdir(original_cwd)
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_prompt_pack_layering ok")


if __name__ == "__main__":
    main()
