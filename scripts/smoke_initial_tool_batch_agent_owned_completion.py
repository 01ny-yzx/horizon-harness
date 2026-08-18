"""Full-loop smoke for agent-owned completion after Initial ToolCall batches."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

os.environ["AGENT_ACCESS_MODE"] = "full_access"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.loop as loop_module
from core.build_step_contract import (
    build_step_contract_can_finalize,
    build_step_contract_from_initial_tool_calls,
)
from core.initial_tool_surface import (
    build_initial_tool_surface,
    resolve_permission_tool_schemas,
)
from scripts.smoke_tool_error_continuation import _call, _message, _run
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs


def _schema_names(call: dict[str, Any]) -> list[str]:
    return [
        str(schema["function"]["name"])
        for schema in call.get("tools") or []
    ]


def _events(captured: dict[str, Any], event_type: str) -> list[Any]:
    return [
        event
        for event in captured["trace"].events
        if event.event_type == event_type
    ]


def _success(path: str, content: str) -> dict[str, Any]:
    return {
        "success": True,
        "status": "success",
        "data": {
            "path": path,
            "filename": Path(path).name,
            "content": content,
        },
    }


def _write_success(path: str, content: str) -> dict[str, Any]:
    return {
        "success": True,
        "status": "success",
        "data": {
            "path": path,
            "content": content,
            "bytes": len(content.encode("utf-8")),
            "content_hash": hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()[:16],
            "target_type": "local_path",
        },
    }


def test_single_initial_call_contract() -> None:
    contract = build_step_contract_from_initial_tool_calls(
        [
            {
                "call_id": "read-1",
                "tool_name": "read_file",
                "capability": "file_read",
                "arguments": {"path": "/tmp/source.txt"},
            }
        ]
    )
    assert contract["reason"] == "initial_tool_calls"
    assert contract["contract_role"] == "execution_batch"
    assert len(contract["steps"]) == 1
    assert build_step_contract_can_finalize(contract) is False


def test_full_access_read_write_prose(root: Path) -> None:
    source = root / "source.txt"
    target = root / "full_access_output.txt"
    content = "alpha\nbeta\n原始内容"
    source.write_text(content, encoding="utf-8")
    final_prose = f"已将原始内容完整写入 `{target}`。"

    def read_file(path: str, **_: Any) -> dict[str, Any]:
        return _success(path, Path(path).read_text(encoding="utf-8"))

    def write_file(path: str, content: str, **_: Any) -> dict[str, Any]:
        Path(path).write_text(content, encoding="utf-8")
        return _write_success(path, content)

    answer, llm, captured, executed = _run(
        [
            _message("", [_call("read-source", "read_file", {"path": str(source)})]),
            _message(
                "",
                [
                    _call(
                        "write-target",
                        "write_file",
                        {"path": str(target), "content": content},
                    )
                ],
            ),
            _message(final_prose),
        ],
        tools={"read_file": read_file, "write_file": write_file},
        request="读取 source.txt，并将原始内容完整写入 full_access_output.txt。",
    )

    assert answer == final_prose
    assert executed == ["read_file", "write_file"]
    assert target.read_text(encoding="utf-8") == content
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    assert "write_file" in _schema_names(llm.calls[1])
    surfaces = _events(captured, "continuation_tool_surface_resolved")
    assert surfaces
    assert all(
        event.data["source"] == "registry_availability_permission"
        for event in surfaces
    )
    assert captured["state"].metadata["normal_completion_owner"] == "agent_continuation"
    assert captured["state"].metadata["task_contract_authoritative"] is False
    assert captured["state"].metadata["initial_tool_batch"] is True
    assert captured["state"].metadata["capability_authority"] == "execution_batch"
    assert captured["state"].metadata["observed_initial_capabilities"] == [
        "file_read"
    ]
    assert captured["state"].metadata["observed_initial_tool_names"] == [
        "read_file"
    ]
    assert captured["state"].metadata["observed_initial_tool_call_ids"] == [
        "read-source"
    ]
    specs = get_unified_tool_specs()
    schemas = get_unified_tool_schemas()
    initial_surface = build_initial_tool_surface(
        specs,
        schemas,
        access_mode="full_access",
    )
    permission_surface = resolve_permission_tool_schemas(
        specs,
        schemas,
        access_mode="full_access",
    )
    expected_names = list(initial_surface.tool_names)
    assert _schema_names({"tools": permission_surface}) == expected_names
    assert all(_schema_names(call) == expected_names for call in llm.calls)
    assert captured["state"].metadata.get("finalization_tools_disabled") is not True
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True
    metadata = captured["state"].metadata
    assert metadata["satisfied_capabilities"] == ["file_read"]
    assert metadata["observed_capabilities"] == ["file_read", "file_write"]
    assert metadata["satisfying_tool"] == "read_file"
    assert metadata["satisfying_tool_call_id"] == "read-source"
    assert metadata["satisfying_target"] == str(source)
    assert metadata["local_file_read_path"] == str(source)
    assert metadata["explore_file_read_path"] == str(source)
    assert metadata["file_write_satisfied"] is True
    assert metadata["file_write_tool"] == "write_file"
    assert metadata["file_write_tool_call_id"] == "write-target"
    assert metadata["file_write_path"] == str(target)
    assert metadata["capability_witnesses"]["file_read"]["tool_name"] == "read_file"
    assert metadata["capability_witnesses"]["file_read"]["target"] == str(source)
    assert metadata["capability_witnesses"]["file_write"]["tool_name"] == "write_file"
    assert metadata["capability_witnesses"]["file_write"]["target"] == str(target)
    witness_events = _events(captured, "observation_capability_witness")
    assert len(witness_events) == 2
    assert witness_events[0].data["primary_capability_matched"] is True
    assert witness_events[1].data["observed_capability"] == "file_write"
    assert witness_events[1].data["primary_capability_matched"] is False
    assert witness_events[1].data["primary_witness_updated"] is False
    capability_events = _events(captured, "capability_observation_satisfied")
    assert not any(
        event.data.get("satisfying_tool") == "write_file"
        and event.data.get("required_capability") == "file_read"
        for event in capability_events
    )
    write_runtime_state = next(
        event
        for event in _events(captured, "runtime_state")
        if event.tool_name == "write_file"
    )
    assert write_runtime_state.data["local_file_read_path"] == str(source)
    assert write_runtime_state.data["explore_file_read_path"] == str(source)
    assert write_runtime_state.data["satisfying_tool"] == "read_file"
    assert write_runtime_state.data["satisfying_target"] == str(source)
    assert write_runtime_state.data["file_write_tool"] == "write_file"
    assert write_runtime_state.data["file_write_path"] == str(target)
    assert not _events(captured, "max_steps_reached")
    assert not any(
        str(call["options"].stage) == "final_answer" for call in llm.calls
    )
    assert not captured["state"].metadata.get("emergency_finalization_fallback")
    metrics = captured["metrics"].summary()
    assert metrics["tool_call_count"] == 2
    assert metrics["real_tool_execution_count"] == 2
    assert metrics["exact_tool_loop_stop_count"] == 0


def test_pure_read_and_command_keep_two_llm_calls(root: Path) -> None:
    source = root / "read-only-target.txt"
    source.write_text("only read", encoding="utf-8")
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("read-only", "read_file", {"path": str(source)})]),
            _message("文件内容是 only read。"),
        ],
        tools={
            "read_file": lambda path, **_: _success(
                path,
                Path(path).read_text(encoding="utf-8"),
            )
        },
        request="读取这个文件并告诉我内容。",
    )
    assert answer == "文件内容是 only read。"
    assert executed == ["read_file"]
    assert len(llm.calls) == 2
    assert "write_file" in _schema_names(llm.calls[1])
    assert _schema_names(llm.calls[0]) == _schema_names(llm.calls[1])
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True

    command_answer, command_llm, command_captured, command_executed = _run(
        [
            _message(
                "",
                [_call("command", "sandbox_exec", {"command": "printf COMMAND_OK"})],
            ),
            _message("命令执行成功，输出为 COMMAND_OK。"),
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
            }
        },
        request="执行命令并告诉我输出。",
    )
    assert command_answer == "命令执行成功，输出为 COMMAND_OK。"
    assert command_executed == ["sandbox_exec"]
    assert len(command_llm.calls) == 2
    assert _schema_names(command_llm.calls[0]) == _schema_names(command_llm.calls[1])


def test_read_only_surface_does_not_gain_side_effect_tools(root: Path) -> None:
    source = root / "read-only-source.txt"
    target = root / "must-not-exist.txt"
    source.write_text("read only", encoding="utf-8")
    previous_settings = loop_module.settings
    loop_module.settings = replace(previous_settings, agent_access_mode="read_only")
    try:
        answer, llm, captured, executed = _run(
            [
                _message("", [_call("read", "read_file", {"path": str(source)})]),
                _message("只读模式下已读取内容，但未修改本地状态。"),
            ],
            tools={
                "read_file": lambda path, **_: _success(
                    path,
                    Path(path).read_text(encoding="utf-8"),
                )
            },
            request="读取源文件并尝试写入目标文件。",
        )
    finally:
        loop_module.settings = previous_settings

    assert answer and executed == ["read_file"]
    names = set(_schema_names(llm.calls[1]))
    assert not {
        "write_file",
        "replace_in_file",
        "sandbox_exec",
        "load_document",
        "load_documents_from_directory",
    } & names
    assert not target.exists()
    assert captured["state"].metadata.get("finalization_tools_disabled") is not True


def test_multiple_initial_calls_are_one_non_authoritative_batch(root: Path) -> None:
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    answer, llm, captured, executed = _run(
        [
            _message(
                "",
                [
                    _call("first", "read_file", {"path": str(first)}),
                    _call("second", "read_file", {"path": str(second)}),
                ],
            ),
            _message("两个文件都已读取。"),
        ],
        tools={
            "read_file": lambda path, **_: _success(
                path,
                Path(path).read_text(encoding="utf-8"),
            )
        },
        request="读取两个文件并总结。",
    )
    assert answer == "两个文件都已读取。"
    assert executed == ["read_file", "read_file"]
    assert len(llm.calls) == 2
    contract = captured["state"].metadata["build_step_contract"]
    assert contract["contract_role"] == "execution_batch"
    assert contract["all_steps_completed"] is True
    assert captured["state"].metadata.get("finalization_tools_disabled") is not True
    assert _events(captured, "initial_tool_batch_complete")
    assert _schema_names(llm.calls[0]) == _schema_names(llm.calls[1])


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            test_single_initial_call_contract()
            test_full_access_read_write_prose(root)
            test_pure_read_and_command_keep_two_llm_calls(root)
            test_read_only_surface_does_not_gain_side_effect_tools(root)
            test_multiple_initial_calls_are_one_non_authoritative_batch(root)
    finally:
        os.chdir(old_cwd)
    print("Initial Tool Batch agent-owned completion smoke passed.")


if __name__ == "__main__":
    main()
