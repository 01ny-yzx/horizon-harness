from __future__ import annotations

from pathlib import Path
import json
import os
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV_OVERRIDES = {
    "TOOL_RESULT_EXTERNALIZE_CHARS": "12000",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "AGENT_ACCESS_MODE": "full_access",
}
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}
os.environ.update(_ENV_OVERRIDES)

from core.observation_compaction import (
    compact_observation_for_model,
    compact_tool_result_for_model,
    observation_compaction_summary,
)
from core.runtime_metrics import RuntimeMetrics
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_to_model_message_json
from core.tool_result_store import ToolResultStore
from core.trace import AgentTrace
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


def _read_envelope(call_id: str, path: Path) -> ToolCallEnvelope:
    arguments = {"path": str(path)}
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name="read_file",
        tool_name="read_file",
        canonical_name="read_file",
        executable_name="read_file",
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "observation-compaction"},
    )


def test_read_file_large_content_compacts(workspace: Path) -> None:
    content = "A" * 10_000
    source = workspace / "large-read.txt"
    source.write_text(content, encoding="utf-8")
    observation = normalize_tool_result(
        _read_envelope("large-read", source),
        {"success": True, "status": "success", "metadata": {"path": str(source)}, "data": content},
    )
    compacted = compact_observation_for_model(observation)
    summary = compacted["model_visible_summary"]
    encoded = compacted["model_observation_json"]
    assert summary["tool_name"] == "read_file"
    assert summary["source_chars"] == 10_000
    assert "preview" in summary
    assert summary["source_ref"] == str(source.resolve()) and not summary["content_ref"]
    assert "A" * 5_000 not in encoded
    assert compacted["original_chars"] > compacted["compacted_chars"]
    assert compacted["saved_chars"] > 0


def test_read_file_small_content_keeps_preview_shape(workspace: Path) -> None:
    source = workspace / "small-read.txt"
    source.write_text("small content", encoding="utf-8")
    observation = normalize_tool_result(
        _read_envelope("small-read", source),
        {"success": True, "status": "success", "metadata": {"path": str(source)}, "data": "small content"},
    )
    compacted = compact_observation_for_model(observation)
    summary = compacted["model_visible_summary"]
    assert summary["preview"] == "small content"
    assert summary["tool_name"] == "read_file"
    assert isinstance(summary, dict)


def test_sandbox_exec_large_stdio_compacts(workspace: Path) -> None:
    store = ToolResultStore()
    stdout_ref = Path(store.store_text("O" * 10_000, task_id="task", call_id="stdout", kind="stdout").content_ref)
    stderr_ref = Path(store.store_text("E" * 5_000, task_id="task", call_id="stderr", kind="stderr").content_ref)
    data = {
            "command": "python noisy.py",
            "cwd": "/sandbox",
            "exit_code": 1,
            "stdout": "O" * 10_000,
            "stderr": "E" * 5_000,
            "stdout_ref": str(stdout_ref),
            "stderr_ref": str(stderr_ref),
    }
    compacted = compact_tool_result_for_model("sandbox_exec", data, success=False)
    summary = compacted["model_visible_summary"]
    encoded = compacted["model_observation_json"]
    assert summary["exit_code"] == 1
    assert summary["command"] == "python noisy.py"
    assert len(summary["stdout_preview"]) < 700
    assert len(summary["stderr_tail"]) <= 500
    assert "O" * 5_000 not in encoded
    assert "E" * 3_000 not in encoded


def test_write_file_omits_content(workspace: Path) -> None:
    output = workspace / "out.txt"
    output.write_text("SECRET" * 1000)
    data = {
            "path": str(output),
            "bytes": 20,
            "content": "SECRET" * 1000,
            "path_grounding": {"operation": "write", "logical_root": "default_output_dir", "path_kind": "default_output", "resolved_path": str(output), "code": "ok"},
    }
    arguments = {"path": str(output), "content": data["content"]}
    envelope = ToolCallEnvelope(
        call_id="write-output",
        provider_call_id="write-output",
        source=ToolCallSource.STRUCTURED,
        raw_name="write_file",
        tool_name="write_file",
        canonical_name="write_file",
        executable_name="write_file",
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments=arguments,
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True, "task_id": "observation-compaction"},
    )
    compacted = compact_observation_for_model(normalize_tool_result(envelope, {"success": True, "data": data}))
    summary = compacted["model_visible_summary"]
    encoded = compacted["model_observation_json"]
    assert summary["output_path"].endswith("out.txt")
    assert "SECRET" * 100 not in encoded
    assert summary["path_grounding"]["logical_root"] == "default_output_dir"


def test_list_files_large_directory_compacts(workspace: Path) -> None:
    items = [{"name": f"file_{index}.txt", "path": f"/tmp/file_{index}.txt"} for index in range(200)]
    result_ref = Path(ToolResultStore().store_json(items, task_id="task", call_id="listing", kind="listing").content_ref)
    compacted = compact_tool_result_for_model(
        "list_files",
        items,
        payload={"success": True, "tool": "list_files", "data": {"result": items, "content_ref": str(result_ref)}},
    )
    summary = compacted["model_visible_summary"]
    assert summary["item_count"] == 200
    assert len(summary["items"]) == 20
    assert summary["omitted_count"] == 180


def test_failed_observation_keeps_error_without_large_payload(workspace: Path) -> None:
        root = workspace
        missing = root / "missing.txt"
        blocked = root / "blocked.txt"
        blocked.write_text("REAL-BODY" * 1000, encoding="utf-8")
        cases = (
            ("failed", missing, "file_not_found"),
            ("blocked", blocked, "sensitive_file_blocked"),
        )
        for status, path, code in cases:
            compacted = compact_tool_result_for_model(
                "read_file",
                {"path": str(path), "content": "X" * 5000},
                success=False,
                error="read failed" + ("!" * 5000),
                payload={
                    "success": False,
                    "status": status,
                    "tool": "read_file",
                    "error_code": code,
                    "error": "read failed" + ("!" * 5000),
                    "data": {"path": str(path), "content": "X" * 5000},
                },
            )
            summary = compacted["model_visible_summary"]
            assert summary["status"] == status
            assert summary["error_code"] == code
            assert summary["requested_path"] == str(path)
            assert not summary.get("content_ref")
            assert "X" * 1000 not in compacted["model_observation_json"]
            assert "REAL-BODY" not in compacted["model_observation_json"]


def test_path_grounding_short_summary(workspace: Path) -> None:
    source = workspace / "grounded.txt"
    source.write_text("grounded", encoding="utf-8")
    data = {
        "path": str(source),
        "path_grounding": {
            "operation": "read",
            "logical_root": "project_root",
            "path_kind": "project_relative",
            "resolved_path": "/tmp/project/a.txt",
            "code": "ok",
            "huge_unused": "Z" * 5000,
        },
    }
    compacted = compact_tool_result_for_model("read_file", data, payload={"success": True, "tool": "read_file", "data": data})
    grounding = compacted["model_visible_summary"]["path_grounding"]
    assert grounding["logical_root"] == "project_root"
    assert grounding["path_kind"] == "project_relative"
    assert "huge_unused" not in grounding


def test_model_message_uses_compact_observation(workspace: Path) -> None:
    source = workspace / "big.txt"
    source.write_text("B" * 14_000, encoding="utf-8")
    envelope = ToolCallEnvelope(
        call_id="call_1",
        raw_name="read_file",
        tool_name="read_file",
        canonical_name="read_file",
        executable_name="read_file",
        source=ToolCallSource.STRUCTURED,
        provider_call_id="call_1",
        raw_arguments=json.dumps({"path": str(source)}),
        parsed_arguments={"path": str(source)},
        sanitized_arguments={"path": str(source)},
        parse_error="",
        status=ToolCallStatus.EXECUTABLE,
        metadata={"tool_spec_found": True},
    )
    observation = normalize_tool_result(envelope, {"success": True, "data": {"path": str(source), "content": "B" * 14_000}})
    model_json = observation_to_model_message_json(observation, runtime_lane="build")
    assert "B" * 5_000 not in model_json
    payload = json.loads(model_json)
    assert payload["tool_name"] == "read_file"
    assert payload["source_ref"] and not payload.get("content_ref")


def test_build_lane_previous_read_for_next_step_compact(workspace: Path) -> None:
    content = "C" * 10_000
    source = workspace / "previous-read.txt"
    source.write_text(content, encoding="utf-8")
    observation = normalize_tool_result(
        _read_envelope("previous-read", source),
        {"success": True, "status": "success", "metadata": {"path": str(source)}, "data": content},
    )
    compacted = compact_observation_for_model(
        observation,
        runtime_lane="build",
        current_step={"index": 2, "tool_name": "sandbox_exec", "status": "pending"},
    )
    encoded = compacted["model_observation_json"]
    assert "C" * 5_000 not in encoded
    payload = json.loads(encoded)
    assert payload["current_step"]["tool_name"] == "sandbox_exec"
    assert payload["tool_name"] == "read_file"


def test_execution_facts_survive_projection_for_every_lane() -> None:
    success_payload = {
        "observation_id": "obs-memory-success",
        "call_id": "call-memory-success",
        "provider_call_id": "provider-memory-success",
        "tool": "remember_user_preference",
        "success": True,
        "status": "success",
        "data": {"key": "answer_first_line", "value": "PREF6:"},
    }
    failure_payload = {
        "observation_id": "obs-memory-failure",
        "call_id": "call-memory-failure",
        "provider_call_id": "provider-memory-failure",
        "tool": "remember_user_preference",
        "success": False,
        "status": "failed",
        "error": "preference rejected",
        "error_code": "invalid_preference",
        "recoverable": True,
        "data": {},
    }
    forbidden_markers = (
        "chat_lane_observation_placeholder",
        "Tool observation omitted from chat model context",
    )
    for lane in ("chat", "explore", "build", "research"):
        successful = compact_observation_for_model(success_payload, runtime_lane=lane)
        successful_json = successful["model_observation_json"]
        successful_summary = json.loads(successful_json)
        assert successful_summary["success"] is True
        assert successful_summary["status"] == "success"
        assert successful_summary["tool_name"] == "remember_user_preference"
        assert successful_summary["observation_id"] == "obs-memory-success"
        assert successful_summary["call_id"] == "call-memory-success"
        assert successful_summary["provider_call_id"] == "provider-memory-success"
        assert not any(marker in successful_json for marker in forbidden_markers)

        failed = compact_observation_for_model(failure_payload, runtime_lane=lane)
        failed_summary = json.loads(failed["model_observation_json"])
        assert failed_summary["success"] is False
        assert failed_summary["status"] == "failed"
        assert failed_summary["error_code"] == "invalid_preference"
        assert failed_summary["error"] == "preference rejected"
        assert failed_summary["recoverable"] is True
        assert failed_summary["call_id"] == "call-memory-failure"


def test_trace_and_metrics_summary(workspace: Path) -> None:
    stdout_ref = Path(ToolResultStore().store_text("D" * 3000, task_id="task", call_id="trace", kind="stdout").content_ref)
    compacted = compact_tool_result_for_model("sandbox_exec", {"stdout": "D" * 3000, "stderr": "", "exit_code": 0, "stdout_ref": str(stdout_ref)})
    summary = observation_compaction_summary(compacted)
    metrics = RuntimeMetrics()
    metrics.record_observation_compaction(summary, step=2)
    decoded = metrics.summary()
    assert decoded["observation_compaction_count"] == 1
    assert decoded["observation_saved_chars_total"] > 0
    assert decoded["observation_compaction_by_tool"]["sandbox_exec"]["count"] == 1

    trace = AgentTrace("task", "goal", "test")
    trace.add_event(2, "observation_compaction", json.dumps(summary), tool_name="sandbox_exec", success=True)
    event = trace.to_dict()["events"][0]
    assert event["event_type"] == "observation_compaction"
    assert "D" * 1000 not in event["summary"]


def main() -> None:
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            os.chdir(directory)
            context = WorkspaceManager(Path(directory)).get_context("smoke", "observation-compaction")
            set_current_workspace(context)
            workspace = context.workspace_dir
            test_read_file_large_content_compacts(workspace)
            test_read_file_small_content_keeps_preview_shape(workspace)
            test_sandbox_exec_large_stdio_compacts(workspace)
            test_write_file_omits_content(workspace)
            test_list_files_large_directory_compacts(workspace)
            test_failed_observation_keeps_error_without_large_payload(workspace)
            test_path_grounding_short_summary(workspace)
            test_model_message_uses_compact_observation(workspace)
            test_build_lane_previous_read_for_next_step_compact(workspace)
            test_execution_facts_survive_projection_for_every_lane()
            test_trace_and_metrics_summary(workspace)
    finally:
        os.chdir(original_cwd)
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_observation_compaction ok")


if __name__ == "__main__":
    main()
