"""Smoke checks for complete ToolCall batches across ordinary runtime lanes."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ["AGENT_ACCESS_MODE"] = "full_access"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_tool_surface import resolve_permission_tool_schemas
from core.runtime_lane import RuntimeLane, resolve_runtime_lane
from core.state import PlanStep, TaskState
from scripts.smoke_tool_error_continuation import (
    _call,
    _message,
    _run,
)
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs
import tools.registry as registry_module


def _permission_names() -> list[str]:
    schemas = get_unified_tool_schemas()
    allowed = resolve_permission_tool_schemas(
        get_unified_tool_specs(),
        schemas,
        access_mode="full_access",
    )
    return [str(item.get("function", {}).get("name") or "") for item in allowed]


def _call_tool_names(call: dict[str, object]) -> list[str]:
    return [
        str(item.get("function", {}).get("name") or "")
        for item in call["tools"]  # type: ignore[index]
    ]


def test_research_full_agent_loop() -> None:
    url = "https://8.8.8.8/"
    original = registry_module.get_web_search_provider_status
    registry_module.get_web_search_provider_status = lambda: SimpleNamespace(
        search_available=True
    )
    try:
        expected_names = _permission_names()
        answer, llm, captured, executed = _run(
            [
                _message(
                    "",
                    [_call("search", "web_search", {"query": "example evidence"})],
                ),
                _message(
                    "",
                    [_call("fetch", "fetch_url", {"url": url})],
                ),
                _message(f"已读取 {url}：Example evidence。"),
            ],
            tools={
                "web_search": lambda query, **_: {
                    "success": True,
                    "status": "success",
                    "data": {
                        "query": query,
                        "results": [
                            {
                                "title": "Example",
                                "url": url,
                                "snippet": "Example evidence",
                            }
                        ],
                    },
                },
                "fetch_url": lambda url, **_: {
                    "success": True,
                    "status": "success",
                    "data": {
                        "url": url,
                        "title": "Example",
                        "content": "Example evidence",
                    },
                },
            },
            request="搜索并读取示例页面。",
            max_steps=6,
        )
    finally:
        registry_module.get_web_search_provider_status = original
    assert answer == f"已读取 {url}：Example evidence。"
    assert executed == ["web_search", "fetch_url"]
    assert len(llm.calls) == 3
    assert _call_tool_names(llm.calls[0]) == expected_names
    assert _call_tool_names(llm.calls[1]) == expected_names
    assert _call_tool_names(llm.calls[2]) == expected_names
    events = captured["trace"].events
    assert not any(
        event.event_type == "tool_call_not_in_scoped_surface"
        for event in events
    )


def test_command_then_read_agent_loop() -> None:
    with TemporaryDirectory() as temp_dir:
        target = Path(temp_dir) / "command-output.txt"
        target.write_text("COMMAND_OK", encoding="utf-8")
        answer, llm, captured, executed = _run(
            [
                _message(
                    "",
                    [_call("command", "sandbox_exec", {"command": "printf COMMAND_OK"})],
                ),
                _message(
                    "",
                    [_call("read", "read_file", {"path": str(target)})],
                ),
                _message("命令输出为 COMMAND_OK。"),
            ],
            tools={
                "sandbox_exec": lambda command, **_: {
                    "success": True,
                    "status": "success",
                    "data": {
                        "command": command,
                        "exit_code": 0,
                        "stdout": "COMMAND_OK",
                        "stderr": "",
                    },
                },
                "read_file": lambda path, **_: {
                    "success": True,
                    "status": "success",
                    "data": {
                        "path": path,
                        "content": Path(path).read_text(encoding="utf-8"),
                    },
                },
            },
            request="执行命令后读取结果文件。",
        )
        assert answer == "命令输出为 COMMAND_OK。"
        assert executed == ["sandbox_exec", "read_file"]
        expected_names = _permission_names()
        assert _call_tool_names(llm.calls[0]) == expected_names
        assert _call_tool_names(llm.calls[1]) == expected_names
        assert _call_tool_names(llm.calls[2]) == expected_names
        assert not any(
            event.event_type == "tool_call_not_in_scoped_surface"
            for event in captured["trace"].events
        )


def test_execution_batch_does_not_select_narrow_task_lane() -> None:
    cases = (
        ("file_read", "read_file", "explore", RuntimeLane.EXPLORE),
        ("file_write", "write_file", "build", RuntimeLane.BUILD),
        ("command_exec", "sandbox_exec", "build", RuntimeLane.BUILD),
        ("code_edit", "replace_in_file", "build", RuntimeLane.BUILD),
    )
    for capability, tool_name, hint, expected in cases:
        state = TaskState.create(
            f"{hint} initial batch",
            "simple",
            [PlanStep(1, "initial batch", "continue")],
        )
        state.metadata.update(
            {
                "required_capabilities": [capability],
                "primary_capability": capability,
                "primary_tool": tool_name,
                "runtime_lane_hint": hint,
                "tool_plan": {
                    "primary_capability": capability,
                    "primary_tool": tool_name,
                    "tool_priority": [tool_name],
                    "capability_authority": "execution_batch",
                },
            }
        )
        decision = resolve_runtime_lane(
            state,
            tool_plan=state.metadata["tool_plan"],
            access_mode="full_access",
        )
        assert decision.lane is expected
        assert decision.lane not in {
            RuntimeLane.SINGLE_FILE_READ,
            RuntimeLane.FILE_OUTPUT,
            RuntimeLane.COMMAND_EXEC,
            RuntimeLane.CODE_EDIT,
        }


def main() -> None:
    test_research_full_agent_loop()
    test_command_then_read_agent_loop()
    test_execution_batch_does_not_select_narrow_task_lane()
    print("smoke_initial_batch_lane_continuity ok")


if __name__ == "__main__":
    main()
