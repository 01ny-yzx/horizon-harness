"""Offline closure smoke for Step 6.1 legacy authority removal."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

_ENV = {
    "AGENT_ACCESS_MODE": "full_access",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "MCP_ENABLED": "false",
    "EMBEDDING_ENABLED": "false",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
}
_OLD_ENV = {key: os.environ.get(key) for key in (*_ENV, "WORKSPACE_ROOT")}
os.environ.update(_ENV)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_tool_surface import resolve_permission_tool_schemas
from core.prompt_pack import build_agent_continuation_pack, build_tool_call_pack
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from scripts.smoke_tool_result_llm_loop_reset import (
    _call,
    _current_permission_names,
    _message,
    _run,
    _schema_names,
)
from tools.registry import get_local_tool_schemas, get_local_tool_specs


REMOVED_FILES = (
    "core/url_tool_execution_guard.py",
    "core/continuation_tool_surface.py",
    "core/guards.py",
    "core/tool_plan_attempt_guard.py",
    "core/workflow_runtime.py",
    "workflows/coding_workflow.py",
    "workflows/rag_workflow.py",
    "core/intent_runtime.py",
    "core/task_profile_bridge.py",
    "core/intent_failure_recovery.py",
    "core/capability_routing.py",
    "core/intent_classifier.py",
    "core/intent_policy.py",
    "core/follow_up_gate.py",
    "core/intent_projection.py",
    "core/intent_debug_trace.py",
)

REMOVED_PRODUCTION_MARKERS = (
    "resolve_continuation_tool_surface",
    "resolve_agent_continuation_tool_schemas",
    "validation_guard",
    "tool_plan_primary_attempt_guard",
    "evaluate_observation_completion",
    "evaluate_tool_completion",
    "scoped_tool_schemas_for_tool_plan",
    "apply_runtime_lane_tool_scope_budget",
    "resolve_intent_runtime_context",
    "intent_runtime_context",
)


def test_removed_files_and_production_markers() -> None:
    assert all(not (ROOT / relative).exists() for relative in REMOVED_FILES)
    for root_name in ("core", "tools", "workflows", "prompts"):
        for path in (ROOT / root_name).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in REMOVED_PRODUCTION_MARKERS:
                assert marker not in source, f"{path}: {marker}"


def test_prompt_pack_compatibility_parameters_are_gone() -> None:
    forbidden = {
        "tool_surface_decision",
        "runtime_lane",
        "tool_plan",
        "capability_surface_summary",
    }
    for function in (build_tool_call_pack, build_agent_continuation_pack):
        assert forbidden.isdisjoint(inspect.signature(function).parameters)


def test_current_surface_and_tool_result_loop() -> None:
    schemas = get_local_tool_schemas()
    current = resolve_permission_tool_schemas(
        get_local_tool_specs(),
        schemas,
        access_mode="full_access",
    )
    expected = _schema_names(current)
    assert expected == _current_permission_names()

    answer, llm, captured, executed = _run(
        [
            _message("", [_call("cwd-1", "get_current_path", {})]),
            _message("已根据真实工具结果完成。"),
        ],
        tools={
            "get_current_path": lambda **_: {
                "success": True,
                "status": "success",
                "data": {"path": "/offline/workspace"},
            }
        },
        request="查看当前路径后回答",
    )
    assert answer == "已根据真实工具结果完成。"
    assert [name for name, _ in executed] == ["get_current_path"]
    assert len(llm.calls) == 2
    assert _schema_names(llm.calls[0]["tools"]) == expected
    assert _schema_names(llm.calls[1]["tools"]) == expected
    assert any(
        message.get("role") == "tool" and message.get("tool_call_id") == "cwd-1"
        for message in llm.calls[1]["messages"]
    )
    event_types = {event.event_type for event in captured["trace"].events}
    assert "normal_finalization" not in event_types
    assert "terminal_responder_llm" not in event_types

    prose, prose_llm, _, prose_executed = _run(
        [_message("普通正文直接结束。")],
        tools={},
        request="直接回答",
    )
    assert prose == "普通正文直接结束。"
    assert len(prose_llm.calls) == 1
    assert prose_executed == []


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            os.environ["WORKSPACE_ROOT"] = str(root / "workspace_store")
            set_current_workspace(
                WorkspaceManager(root).get_context("smoke", "legacy-authority-removal")
            )
            test_removed_files_and_production_markers()
            test_prompt_pack_compatibility_parameters_are_gone()
            test_current_surface_and_tool_result_loop()
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_legacy_authority_dead_path_removal ok")


if __name__ == "__main__":
    main()
