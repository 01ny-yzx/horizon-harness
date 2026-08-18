"""Smoke checks for capability metadata without ToolPlan execution authority."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

_ENV = {"AGENT_ACCESS_MODE": "full_access", "ENABLE_WORKSPACE_ISOLATION": "true"}
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_boundary import evaluate_tool_execution_boundary
from core.runtime_lane import RuntimeLane, resolve_runtime_lane
from core.state import PlanStep, TaskState
from core.task_profile import TaskProfile
from core.tool_execution_authorization import authorize_tool_execution
from core.tool_call_grants import register_tool_call_grant
from core.tool_call_schema import build_structured_tool_call_envelope
from core.unified_intent_capability import apply_unified_intent_capability_metadata
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


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


def _state(profile: TaskProfile, metadata: dict[str, object]) -> TaskState:
    state = TaskState.create(
        user_goal="authoritative contract smoke",
        task_type=profile.task_type,
        task_profile=profile,
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )
    state.metadata.update(metadata)
    return state


def test_command_alias_and_authorization() -> None:
    state = _state(
        _profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        {
            "required_capabilities": ["command_exec"],
            "primary_capability": "command_exec",
            "primary_tool": "shell",
            "tool_plan": {
                "primary_capability": "command_exec",
                "primary_tool": "shell",
                "tool_priority": ["shell"],
            },
        },
    )
    decision = apply_unified_intent_capability_metadata(state)
    assert decision.primary_tool == "sandbox_exec"
    assert state.metadata["tool_plan"]["primary_tool"] == "sandbox_exec"
    assert authorize_tool_execution(task_state=state, tool_name="sandbox_exec").allowed is True
    assert evaluate_tool_execution_boundary(
        task_state=state,
        tool_name="sandbox_exec",
        arguments={"command": "echo ok"},
        raw_arguments='{"command":"echo ok"}',
    ).allowed is True


def test_formal_plan_cannot_block_actual_structured_tool_call() -> None:
    state = _state(
        _profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        {
            "required_capabilities": ["mcp"],
            "primary_capability": "mcp",
            "primary_tool": "mcp.fixture.lookup",
            "tool_plan": {
                "primary_capability": "mcp",
                "primary_tool": "mcp.fixture.lookup",
                "tool_priority": ["mcp.fixture.lookup"],
            },
            "first_call_required_capabilities": ["command_exec"],
            "first_call_primary_capability": "command_exec",
            "first_call_primary_tool": "sandbox_exec",
        },
    )
    decision = apply_unified_intent_capability_metadata(state)
    assert decision.primary_capability == "mcp"
    assert decision.primary_tool == "lookup"
    assert decision.requires_command_exec is False
    arguments = {"command": "echo actual-call"}
    envelope = build_structured_tool_call_envelope(
        SimpleNamespace(
            id="actual-sandbox-call",
            function=SimpleNamespace(
                name="sandbox_exec",
                arguments=json.dumps(arguments),
            ),
        )
    )
    envelope.metadata["execution_grant_required"] = True
    envelope.metadata["grant_registered_arguments"] = dict(arguments)
    grant = register_tool_call_grant(
        state,
        call_id=envelope.call_id,
        provider_call_id=envelope.provider_call_id,
        canonical_name=envelope.canonical_name,
        executable_name=envelope.executable_name,
        arguments=envelope.parsed_arguments,
        source=envelope.source,
        raw_arguments=envelope.raw_arguments,
    )
    assert grant.allowed is True
    assert authorize_tool_execution(
        task_state=state,
        tool_name="sandbox_exec",
        tool_call_envelope=envelope,
        sanitized_arguments=arguments,
    ).allowed is True
    assert evaluate_tool_execution_boundary(
        task_state=state,
        tool_name="sandbox_exec",
        arguments=arguments,
        tool_call_envelope=envelope,
        raw_arguments=envelope.raw_arguments,
    ).allowed is True


def test_formal_mixed_contract_and_file_contracts() -> None:
    mixed = _state(
        _profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        {
            "required_capabilities": ["code_edit", "command_exec"],
            "primary_capability": "code_edit",
            "primary_tool": "replace_in_file",
            "tool_plan": {
                "primary_capability": "code_edit",
                "primary_tool": "replace_in_file",
                "tool_priority": ["replace_in_file", "sandbox_exec"],
                "supporting_capabilities": ["command_exec"],
            },
        },
    )
    mixed_decision = apply_unified_intent_capability_metadata(mixed)
    assert {"code_edit", "command_exec"} <= set(mixed_decision.required_capabilities)
    assert "sandbox_exec" in mixed.metadata["tool_plan"]["tool_priority"]
    assert resolve_runtime_lane(mixed).lane is RuntimeLane.CODE_EDIT

    read = _state(_profile(tool_required=True, execution_mode="normal"), {"required_capabilities": ["file_read"]})
    assert apply_unified_intent_capability_metadata(read).requires_file_read is True
    assert resolve_runtime_lane(read).lane is RuntimeLane.SINGLE_FILE_READ

    write = _state(
        _profile(tool_required=True, side_effect_required=True, execution_mode="normal"),
        {"required_capabilities": ["file_write"], "primary_tool": "write_file"},
    )
    assert apply_unified_intent_capability_metadata(write).requires_file_write is True
    assert resolve_runtime_lane(write).lane is RuntimeLane.FILE_OUTPUT


def main() -> None:
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspaces").get_context("smoke", "authoritative-contract"))
            test_command_alias_and_authorization()
            test_formal_plan_cannot_block_actual_structured_tool_call()
            test_formal_mixed_contract_and_file_contracts()
    finally:
        os.chdir(original_cwd)
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_authoritative_capability_contract ok")


if __name__ == "__main__":
    main()
