from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import types

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.get = lambda *args, **kwargs: None
    requests_stub.post = lambda *args, **kwargs: None
    requests_stub.Session = lambda *args, **kwargs: None
    requests_stub.exceptions = SimpleNamespace(RequestException=Exception, Timeout=TimeoutError)
    sys.modules["requests"] = requests_stub

from core.capability_surface import (
    build_capability_surface_summary,
    is_mixed_capability,
)
from core.prompt_pack import build_tool_call_pack
from core.runtime_lane import RuntimeLane, resolve_runtime_lane
from core.runtime_metrics import RuntimeMetrics


def _schema(name: str) -> dict[str, object]:
    return {"type": "function", "function": {"name": name, "description": f"{name} tool", "parameters": {"type": "object"}}}


def _state(**metadata: object) -> SimpleNamespace:
    return SimpleNamespace(task_type="", workflow_name="", workflow_kind="", metadata=dict(metadata))


def test_single_file_read_surface() -> None:
    summary = build_capability_surface_summary(
        runtime_lane="single_file_read",
        lane_profile="single_file_read",
        primary_capability="file_read",
        primary_tool="read_file",
        required_capabilities=["file_read"],
        scoped_tool_names=["read_file"],
    )
    assert summary["capability_surface"] == "read"
    assert summary["effective_capabilities"] == ["file_read"]
    assert summary["effective_tools"] == ["read_file"]
    assert "write_file" not in summary["effective_tools"]
    assert "sandbox_exec" not in summary["effective_tools"]


def test_file_output_surface() -> None:
    summary = build_capability_surface_summary(
        runtime_lane="file_output",
        lane_profile="file_output",
        primary_capability="file_write",
        primary_tool="write_file",
        required_capabilities=["file_write"],
        scoped_tool_names=["write_file"],
    )
    assert summary["capability_surface"] == "write"
    assert summary["effective_capabilities"] == ["file_write"]
    assert summary["effective_tools"] == ["write_file"]
    assert "read_file" not in summary["effective_tools"]
    assert "sandbox_exec" not in summary["effective_tools"]


def test_command_exec_surface() -> None:
    summary = build_capability_surface_summary(
        runtime_lane="command_exec",
        lane_profile="command_exec",
        primary_capability="command_exec",
        primary_tool="sandbox_exec",
        required_capabilities=["command_exec"],
        scoped_tool_names=["sandbox_exec"],
    )
    assert summary["capability_surface"] == "bash"
    assert summary["effective_capabilities"] == ["command_exec"]
    assert summary["effective_tools"] == ["sandbox_exec"]
    assert "read_file" not in summary["effective_tools"]
    assert "write_file" not in summary["effective_tools"]


def test_code_edit_and_build_not_over_narrowed() -> None:
    code_summary = build_capability_surface_summary(
        runtime_lane="code_edit",
        lane_profile="code_edit",
        primary_capability="code_edit",
        primary_tool="replace_in_file",
        required_capabilities=["code_edit"],
        scoped_tool_names=["read_file", "replace_in_file", "write_file", "sandbox_exec"],
    )
    assert code_summary["capability_surface"] == "edit"
    assert "read_file" in code_summary["effective_tools"]
    assert "replace_in_file" in code_summary["effective_tools"]
    assert "sandbox_exec" in code_summary["effective_tools"]

    build_summary = build_capability_surface_summary(
        runtime_lane="build",
        lane_profile="build",
        primary_capability="",
        primary_tool="",
        required_capabilities=["file_write", "command_exec"],
        scoped_tool_names=["write_file", "sandbox_exec"],
    )
    assert build_summary["capability_surface"] == "build"
    assert build_summary["effective_capabilities"] == ["file_write", "command_exec"]
    assert build_summary["effective_tools"] == ["write_file", "sandbox_exec"]


def test_build_surface_projects_write_file_from_file_write_capability() -> None:
    summary = build_capability_surface_summary(
        runtime_lane="build",
        lane_profile="build",
        primary_capability="file_read",
        primary_tool="read_file",
        required_capabilities=["file_read", "file_write"],
        scoped_tool_names=["read_file"],
    )
    assert summary["runtime_lane"] == "build"
    assert summary["capability_surface"] == "build"
    assert summary["effective_capabilities"] == ["file_read", "file_write"]
    assert summary["effective_tools"] == ["read_file", "write_file"]


def test_mixed_capability_runtime_lane_build_fallback() -> None:
    cases = (
        (["file_read", "command_exec"], "read_file", ["read_file", "sandbox_exec"]),
        (["file_read", "file_write"], "read_file", ["read_file", "write_file"]),
        (["file_write", "command_exec"], "write_file", ["write_file", "sandbox_exec"]),
    )
    for required, primary_tool, tools in cases:
        state = _state(
            required_capabilities=required,
            runtime_lane_hint="build",
            primary_capability=required[0],
            primary_tool=primary_tool,
            tool_required=True,
            side_effect_required=True,
            tool_plan={
                "primary_capability": required[0],
                "primary_tool": primary_tool,
                "tool_priority": tools,
            },
        )
        lane = resolve_runtime_lane(state, tool_plan=state.metadata["tool_plan"])
        assert lane.lane is RuntimeLane.BUILD
        assert lane.reason == "mixed_capability_build_fallback"


def test_mixed_capability_surface_build_fallback_even_with_simple_lane() -> None:
    assert is_mixed_capability(["command_exec", "file_read"]) is True
    assert is_mixed_capability(["command_exec", "command_exec", ""]) is False
    summary = build_capability_surface_summary(
        runtime_lane="command_exec",
        lane_profile="command_exec",
        primary_capability="file_read",
        primary_tool="read_file",
        required_capabilities=["command_exec", "file_read"],
        scoped_tool_names=["sandbox_exec"],
    )
    assert summary["runtime_lane"] == "build"
    assert summary["capability_surface"] == "build"
    assert summary["source"] == "mixed_capability"
    assert summary["effective_capabilities"] == ["command_exec", "file_read"]
    assert "sandbox_exec" in summary["effective_tools"]
    assert "read_file" in summary["effective_tools"]


def test_tool_call_pack_uses_capability_surface_not_legacy_hint() -> None:
    state = _state(
        runtime_lane="command_exec",
        runtime_lane_hint="build",
        required_capabilities=["command_exec", "file_write"],
        primary_capability="mcp_tool",
        primary_tool="get_browser_status",
        tool_arguments={"command": "echo ok"},
    )
    pack = build_tool_call_pack(
        user_input="执行命令：echo ok",
        task_state=state,
        tools=[_schema("sandbox_exec")],
        memory_messages=[],
    )
    rendered = "\n".join(str(item.get("content") or "") for item in pack.messages)
    assert "capability_surface_summary" not in rendered
    assert "runtime_lane_hint" not in rendered
    assert "get_browser_status" not in rendered
    assert "fast_lane" not in rendered
    assert pack.metadata["tool_names"] == ["sandbox_exec"]


def test_runtime_metrics_records_capability_surface() -> None:
    metrics = RuntimeMetrics()
    summary = build_capability_surface_summary(
        runtime_lane="file_output",
        scoped_tool_names=["write_file"],
        required_capabilities=["file_write"],
    )
    metrics.record_capability_surface(summary)
    decoded = metrics.summary()
    assert decoded["capability_surface"] == "write"
    assert decoded["effective_capabilities"] == ["file_write"]
    assert decoded["effective_tools"] == ["write_file"]
    assert decoded["capability_surface_records"][0]["source"] == "runtime_lane"


def main() -> None:
    test_single_file_read_surface()
    test_file_output_surface()
    test_command_exec_surface()
    test_code_edit_and_build_not_over_narrowed()
    test_build_surface_projects_write_file_from_file_write_capability()
    test_mixed_capability_runtime_lane_build_fallback()
    test_mixed_capability_surface_build_fallback_even_with_simple_lane()
    test_tool_call_pack_uses_capability_surface_not_legacy_hint()
    test_runtime_metrics_records_capability_surface()
    print("smoke_capability_cleanup ok")


if __name__ == "__main__":
    main()
