"""Smoke checks for Observation capability witnesses and neutral file-write evidence."""

from __future__ import annotations

import hashlib
import os

_PREVIOUS_ACCESS_MODE = os.environ.get("AGENT_ACCESS_MODE")
os.environ["AGENT_ACCESS_MODE"] = "full_access"

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop, update_primary_capability_satisfied
from core.state import PlanStep, TaskState
from core.task_profile import TaskProfile
from core.tool_observation import ToolObservation
from scripts.smoke_tool_error_continuation import (
    _call,
    _message,
    _run,
)
from tools.registry import get_unified_tool_specs


SPECS = get_unified_tool_specs()


def _profile(
    *,
    needs_file_output: bool = False,
    task_type: str = "simple",
    is_coding_task: bool = False,
) -> TaskProfile:
    return TaskProfile(
        task_type=task_type,
        needs_web=False,
        has_url=False,
        has_search_engine_url=False,
        needs_code_edit=is_coding_task,
        needs_validation=False,
        needs_git=False,
        user_intent_summary="capability witness smoke",
        needs_file_output=needs_file_output,
        is_coding_task=is_coding_task,
        tool_required=True,
        side_effect_required=True,
        execution_mode="normal",
        structured_intent_type="tool",
        structured_task_type=task_type,
        structured_workflow_kind="build" if is_coding_task else "simple",
    )


def _state(
    *,
    primary_capability: str,
    required_capabilities: list[str],
    primary_tool: str,
    needs_file_output: bool = False,
    task_type: str = "simple",
    is_coding_task: bool = False,
) -> TaskState:
    state = TaskState.create(
        "capability witness smoke",
        task_type,
        [PlanStep(1, "smoke", "smoke")],
        _profile(
            needs_file_output=needs_file_output,
            task_type=task_type,
            is_coding_task=is_coding_task,
        ),
    )
    state.metadata.update(
        {
            "primary_capability": primary_capability,
            "required_capabilities": required_capabilities,
            "tool_plan": {
                "primary_capability": primary_capability,
                "primary_tool": primary_tool,
                "tool_priority": [primary_tool],
            },
        }
    )
    return state


def _observation(
    tool_name: str,
    call_id: str,
    *,
    success: bool,
    path: str = "",
    data: dict[str, Any] | None = None,
) -> ToolObservation:
    payload = dict(data or {})
    if path:
        payload.setdefault("path", path)
    return ToolObservation(
        observation_id=f"obs-{call_id}",
        call_id=call_id,
        provider_call_id=call_id,
        tool_name=tool_name,
        canonical_name=tool_name,
        executable_name=tool_name,
        status="success" if success else "failed",
        kind=str(SPECS[tool_name].kind),
        success=success,
        error="" if success else "tool failed",
        error_code="" if success else "tool_failed",
        data=payload,
        output_path=path,
        stdout=str(payload.get("stdout") or ""),
        exit_code=payload.get("exit_code"),
    )


def _update(
    state: TaskState,
    tool_name: str,
    call_id: str,
    observation: ToolObservation,
) -> dict[str, Any]:
    return update_primary_capability_satisfied(
        state,
        tool_name=tool_name,
        tool_spec=SPECS[tool_name],
        observation=observation,
        tool_call_id=call_id,
    )


def _write_data(path: Path, content: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "filename": path.name,
        "bytes": len(content.encode("utf-8")),
        "content_hash": hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()[:16],
        "target_type": "local_path",
        "content": content,
    }


def _events(captured: dict[str, Any], event_type: str) -> list[Any]:
    return [
        event
        for event in captured["trace"].events
        if event.event_type == event_type
    ]


def test_read_write_full_loop_preserves_read_witness(root: Path) -> None:
    source = root / "source.txt"
    target = root / "full_access_output.txt"
    content = "source witness\n原始内容"
    source.write_text(content, encoding="utf-8")
    final_prose = "已按原内容完成写入。"

    def read_file(path: str, **_: Any) -> dict[str, Any]:
        return {
            "success": True,
            "status": "success",
            "data": {
                "path": path,
                "content": Path(path).read_text(encoding="utf-8"),
            },
        }

    def write_file(path: str, content: str, **_: Any) -> dict[str, Any]:
        Path(path).write_text(content, encoding="utf-8")
        return {
            "success": True,
            "status": "success",
            "data": _write_data(Path(path), content),
        }

    def project_build_lane(state: Any) -> None:
        state.metadata["runtime_lane"] = "build"
        state.metadata["runtime_lane_hint"] = "build"
        state.metadata["runtime_lane_profile"] = "build"

    answer, _, captured, executed = _run(
        [
            _message(
                "",
                [_call("read-source", "read_file", {"path": str(source)})],
            ),
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
        request="读取源文件并把原始内容写入目标文件。",
        state_projection=project_build_lane,
    )

    assert answer == final_prose
    assert executed == ["read_file", "write_file"]
    assert target.read_text(encoding="utf-8") == content
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
    assert metadata["capability_witnesses"]["file_read"] == {
        "tool_name": "read_file",
        "tool_call_id": "read-source",
        "target": str(source),
        "success": True,
        "result_available": True,
        "observed_capability": "file_read",
    }
    assert metadata["capability_witnesses"]["file_write"]["tool_name"] == (
        "write_file"
    )
    assert metadata["capability_witnesses"]["file_write"]["target"] == str(
        target
    )
    successful_writes = metadata["successful_file_write_observations"]
    assert successful_writes == [
        {
            "tool_name": "write_file",
            "tool_call_id": "write-target",
            "path": str(target),
            "bytes": len(content.encode("utf-8")),
            "content_hash": hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()[:16],
            "target_type": "local_path",
        }
    ]
    assert captured["state"].file_output_completed is False
    assert captured["state"].file_output_result is None
    assert captured["state"].output_files == []
    assert captured["state"].created_outputs == []

    witness_events = _events(captured, "observation_capability_witness")
    assert len(witness_events) == 2
    assert witness_events[0].data["observed_capability"] == "file_read"
    assert witness_events[0].data["primary_capability_matched"] is True
    assert witness_events[1].data["observed_capability"] == "file_write"
    assert witness_events[1].data["matched_required_capabilities"] == []
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
    assert write_runtime_state.data["file_write_satisfied"] is True
    assert write_runtime_state.data["file_write_tool"] == "write_file"
    assert write_runtime_state.data["file_write_path"] == str(target)


def test_primary_file_write_and_output_contract(root: Path) -> None:
    target = root / "primary-write.txt"
    content = "primary file write"
    data = _write_data(target, content)
    observation = _observation(
        "write_file",
        "write-primary",
        success=True,
        path=str(target),
        data=data,
    )
    state = _state(
        primary_capability="file_write",
        required_capabilities=["file_write"],
        primary_tool="write_file",
        needs_file_output=True,
    )
    update = _update(state, "write_file", "write-primary", observation)
    assert update["observed_capability"] == "file_write"
    assert update["matched_required_capabilities"] == ["file_write"]
    assert update["primary_capability_matched"] is True
    assert update["primary_witness_updated"] is True
    assert state.metadata["satisfied_capabilities"] == ["file_write"]
    assert state.metadata["satisfying_tool"] == "write_file"
    assert state.metadata["satisfying_target"] == str(target)
    assert state.metadata["capability_witnesses"]["file_write"]["tool_name"] == (
        "write_file"
    )

    legacy_observation = {
        "success": True,
        "status": "success",
        "data": data,
    }
    loop = AgentLoop.__new__(AgentLoop)
    loop._record_side_effects(
        state,
        "write_file",
        {"path": str(target), "content": content},
        legacy_observation,
    )
    assert state.file_output_completed is True
    assert state.file_output_result
    assert state.file_output_result["path"] == str(target)
    assert state.output_files == [str(target)]
    assert state.created_outputs[0]["path"] == str(target)

    dynamic_state = _state(
        primary_capability="file_read",
        required_capabilities=["file_read"],
        primary_tool="read_file",
        needs_file_output=False,
    )
    _update(
        dynamic_state,
        "write_file",
        "dynamic-write",
        observation,
    )
    loop._record_side_effects(
        dynamic_state,
        "write_file",
        {"path": str(target), "content": content},
        legacy_observation,
    )
    assert dynamic_state.metadata["file_write_satisfied"] is True
    assert dynamic_state.file_output_completed is False
    assert dynamic_state.file_output_result is None
    assert dynamic_state.output_files == []
    assert dynamic_state.created_outputs == []

    edit_target = root / "edited.py"
    edit_observation = _observation(
        "replace_in_file",
        "edit-call",
        success=True,
        path=str(edit_target),
        data=_write_data(edit_target, "print('edited')\n"),
    )
    edit_state = _state(
        primary_capability="code_edit",
        required_capabilities=["code_edit"],
        primary_tool="replace_in_file",
        task_type="coding",
        is_coding_task=True,
    )
    edit_update = _update(
        edit_state,
        "replace_in_file",
        "edit-call",
        edit_observation,
    )
    assert edit_update["observed_capability"] == "code_edit"
    assert edit_update["matched_required_capabilities"] == ["code_edit"]
    assert edit_state.metadata["capability_witnesses"]["code_edit"][
        "tool_name"
    ] == "replace_in_file"
    assert edit_state.metadata["file_write_satisfied"] is True
    assert edit_state.metadata["file_write_tool"] == "replace_in_file"


def test_later_command_does_not_replace_read_witness(root: Path) -> None:
    source = root / "command-source.txt"
    state = _state(
        primary_capability="file_read",
        required_capabilities=["file_read"],
        primary_tool="read_file",
    )
    _update(
        state,
        "read_file",
        "read-before-command",
        _observation(
            "read_file",
            "read-before-command",
            success=True,
            path=str(source),
            data={"path": str(source), "content": "source"},
        ),
    )
    command_update = _update(
        state,
        "sandbox_exec",
        "command-after-read",
        _observation(
            "sandbox_exec",
            "command-after-read",
            success=True,
            data={"command": "printf ok", "exit_code": 0, "stdout": "ok"},
        ),
    )
    assert command_update["observed_capability"] == "command_exec"
    assert command_update["matched_required_capabilities"] == []
    assert command_update["primary_capability_matched"] is False
    assert state.metadata["satisfying_tool"] == "read_file"
    assert state.metadata["satisfying_target"] == str(source)
    assert state.metadata["local_file_read_path"] == str(source)
    assert state.metadata["observed_capabilities"] == [
        "file_read",
        "command_exec",
    ]
    assert state.metadata["capability_witnesses"]["command_exec"][
        "tool_name"
    ] == "sandbox_exec"


def test_read_document_can_witness_file_read(root: Path) -> None:
    document = root / "table.xlsx"
    state = _state(
        primary_capability="file_read",
        required_capabilities=["file_read"],
        primary_tool="read_file",
    )
    update = _update(
        state,
        "read_document",
        "read-document",
        _observation(
            "read_document",
            "read-document",
            success=True,
            path=str(document),
            data={
                "path": str(document),
                "sheets": [{"name": "Sheet1", "rows": [["value"]]}],
            },
        ),
    )
    assert update["observed_capability"] == "file_read"
    assert update["matched_required_capabilities"] == ["file_read"]
    assert update["primary_capability_matched"] is True
    assert state.metadata["satisfying_tool"] == "read_document"
    assert state.metadata["satisfying_target"] == str(document)
    assert state.metadata["local_file_read_path"] == str(document)
    assert state.metadata["explore_file_read_path"] == str(document)
    assert state.metadata["capability_witnesses"]["file_read"]["tool_name"] == (
        "read_document"
    )
    assert state.metadata["capability_witnesses"]["file_read"][
        "tool_name"
    ] == "read_document"
    assert state.metadata["observed_capabilities"] == ["file_read"]


def test_failed_write_records_no_success_witness(root: Path) -> None:
    source = root / "failure-source.txt"
    target = root / "failure-target.txt"
    state = _state(
        primary_capability="file_read",
        required_capabilities=["file_read"],
        primary_tool="read_file",
    )
    _update(
        state,
        "read_file",
        "read-success",
        _observation(
            "read_file",
            "read-success",
            success=True,
            path=str(source),
            data={"path": str(source), "content": "source"},
        ),
    )
    failed = _update(
        state,
        "write_file",
        "write-failed",
        _observation(
            "write_file",
            "write-failed",
            success=False,
            path=str(target),
            data={"path": str(target), "error_code": "write_failed"},
        ),
    )
    assert failed["observation_success"] is False
    assert failed["matched_required_capabilities"] == []
    assert failed["primary_witness_updated"] is False
    assert state.metadata["satisfying_tool"] == "read_file"
    assert state.metadata["local_file_read_path"] == str(source)
    assert "file_write" not in state.metadata["observed_capabilities"]
    assert "file_write" not in state.metadata["capability_witnesses"]
    assert not state.metadata.get("file_write_satisfied")
    assert not state.metadata.get("successful_file_write_observations")


def test_runtime_state_has_no_file_read_hardcode() -> None:
    answer, _, captured, executed = _run(
        [
            _message(
                "",
                [
                    _call(
                        "command-only",
                        "sandbox_exec",
                        {"command": "printf COMMAND_ONLY"},
                    )
                ],
            ),
            _message("命令已完成。"),
        ],
        tools={
            "sandbox_exec": lambda command, **_: {
                "success": True,
                "status": "success",
                "data": {
                    "command": command,
                    "exit_code": 0,
                    "stdout": "COMMAND_ONLY",
                    "stderr": "",
                },
            }
        },
        request="执行命令并报告结果。",
    )
    assert answer == "命令已完成。"
    assert executed == ["sandbox_exec"]
    runtime_state = _events(captured, "runtime_state")[-1]
    assert runtime_state.data["observed_capability"] == "command_exec"
    assert runtime_state.data["explore_file_read_satisfied"] is False
    assert runtime_state.data["local_file_read_satisfied"] is False
    assert runtime_state.data["file_write_satisfied"] is False


def test_idempotent_document_replay_keeps_current_witness_identity() -> None:
    state = _state(
        primary_capability="document_load",
        required_capabilities=["document_load"],
        primary_tool="load_document",
    )
    observation = _observation(
        "load_document",
        "load-replay-current",
        success=True,
        path="/tmp/source.txt",
        data={
            "status": "success",
            "document_id": "doc-replay",
            "document_stored": True,
            "chunks_stored": True,
            "chunk_count": 1,
            "store_status": {"added": True},
            "idempotent_replay": True,
            "replayed_from_call_id": "load-original",
            "real_execution": False,
            "tool_executed": False,
        },
    )
    result = _update(
        state,
        "load_document",
        "load-replay-current",
        observation,
    )
    assert result["observed_capability"] == "document_load"
    assert result["primary_capability_matched"] is True
    witness = state.metadata["capability_witnesses"]["document_load"]
    assert witness["tool_name"] == "load_document"
    assert witness["tool_call_id"] == "load-replay-current"
    assert witness["target"] == "/tmp/source.txt"


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            test_read_write_full_loop_preserves_read_witness(root)
            test_primary_file_write_and_output_contract(root)
            test_later_command_does_not_replace_read_witness(root)
            test_read_document_can_witness_file_read(root)
            test_failed_write_records_no_success_witness(root)
            test_runtime_state_has_no_file_read_hardcode()
            test_idempotent_document_replay_keeps_current_witness_identity()
    finally:
        os.chdir(old_cwd)
        if _PREVIOUS_ACCESS_MODE is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = _PREVIOUS_ACCESS_MODE
    print("Observation capability witness consistency smoke passed.")


if __name__ == "__main__":
    main()
