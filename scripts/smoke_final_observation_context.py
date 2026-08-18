from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.final_observation_context as observation_context
from core.final_observation_context import (
    SCHEMA_VERSION,
    build_final_observation_context,
    final_observation_context_trace_summary,
)
from core.memory import Memory
from core.tool_result_store import ToolResultStore
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.workspace import WorkspaceManager
from core.workspace_runtime import get_current_workspace, set_current_workspace


class DummyTaskState:
    task_type = "research"
    user_goal = "ignored by observation context"
    tool_failures: list[dict[str, object]] = []
    file_output_completed = False

    def __init__(self, task_id: str = "task-current") -> None:
        self.task_id = task_id
        self.metadata: dict[str, object] = {"runtime_lane": "research"}


def _outcome(kind: str, tool: str, observation: dict[str, object], policy_code: str = "") -> ToolOutcomeResolution:
    return ToolOutcomeResolution(
        kind,  # type: ignore[arg-type]
        "status_tool_success" if kind == "terminal_success" else "policy_blocked",
        tool=tool,
        status=str(observation.get("status") or ""),
        policy_code=policy_code,
        metadata={"observation": observation},
    )


def test_fetch_url_execution_fields() -> None:
    context = build_final_observation_context(
        DummyTaskState(),
        _outcome(
            "allow_continue",
            "fetch_url",
            {
                "tool": "fetch_url",
                "success": False,
                "status": "failed",
                "error": "Unable to fetch https://example.com/file.txt",
                "error_code": "http_error",
                "data": {
                    "url": "https://example.com/file.txt",
                    "content_type": "text/plain",
                    "format": "markdown",
                    "http_status": 403,
                    "needs_browser_fallback": False,
                    "fallback_reason": "legacy_hint",
                },
            },
        ),
    )
    item = context[0]
    assert item["tool"] == "fetch_url"
    assert item["base_tool"] == "fetch_url"
    assert item["error"] == "Unable to fetch https://example.com/file.txt"
    assert item["error_code"] == "http_error"
    assert "policy_code" not in item
    assert item["data_summary"] == {
        "url": "https://example.com/file.txt",
        "content_type": "text/plain",
        "format": "markdown",
        "error": "Unable to fetch https://example.com/file.txt",
        "error_code": "http_error",
        "http_status": 403,
    }
    assert "needs_browser_fallback" not in item["data_summary"]
    assert "fallback_reason" not in item["data_summary"]


def test_read_file_long_content_compacted() -> None:
    content = "abc123\n" * 500
    path = str((ROOT / "core" / "runtime_metrics.py").resolve())
    context = build_final_observation_context(
        DummyTaskState(),
        _outcome(
            "terminal_success",
            "read_file",
            {"tool": "read_file", "success": True, "status": "success", "data": {"path": path, "content": content}},
        ),
    )
    item = context[0]
    rendered = json.dumps(item, ensure_ascii=False)
    assert item.get("preview") or item["data_summary"].get("preview")
    assert item["truncated"] is True
    assert not item.get("refs") and not item["data_summary"].get("source_ref")
    assert content not in rendered


def test_sandbox_exec_stdio_compacted() -> None:
    stdout = "out\n" * 800
    stderr = "err\n" * 800
    context = build_final_observation_context(
        DummyTaskState(),
        _outcome(
            "terminal_failure",
            "sandbox_exec",
            {
                "tool": "sandbox_exec",
                "success": False,
                "status": "failed",
                "data": {
                    "command": "python script.py",
                    "cwd": "/tmp/project",
                    "exit_code": 1,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            },
        ),
    )
    summary = context[0]["data_summary"]
    rendered = json.dumps(context[0], ensure_ascii=False)
    assert summary["command"] == "python script.py"
    assert summary["exit_code"] == 1
    assert "stdout_preview" in summary
    assert "stderr_preview" in summary
    assert stdout not in rendered
    assert stderr not in rendered


def test_multiple_observations_budget_and_dedupe() -> None:
    task_state = DummyTaskState()
    duplicate = {"tool": "fetch_url", "success": True, "status": "success", "data": {"url": "https://example.com", "content": "same"}}
    task_state.metadata["completion_observations"] = [duplicate]
    outcome = _outcome("terminal_success", "fetch_url", duplicate)
    context = build_final_observation_context(task_state, outcome)
    assert context[0]["source"] == "task_state"
    summary = final_observation_context_trace_summary(context)
    assert summary["raw_observation_count"] == 2
    assert summary["observation_count"] == 1
    assert summary["duplicates_removed"] == 1

    task_state.metadata["completion_observations"] = [
        {"tool": "read_file", "success": True, "status": "success", "data": {"path": "/tmp/a.txt", "content": "visible"}},
        {"tool": "sandbox_exec", "success": True, "status": "success", "data": {"command": "echo ok", "exit_code": 0, "stdout": "ok"}},
    ]
    mixed = build_final_observation_context(
        task_state,
        _outcome("terminal_success", "sandbox_exec", task_state.metadata["completion_observations"][-1]),
    )
    assert {item["base_tool"] for item in mixed} == {"read_file", "sandbox_exec"}

    reads = [
        {
            "call_id": "call_read_a",
            "tool": "read_file",
            "success": True,
            "status": "success",
            "data": {"path": "a.txt", "content": "A"},
        },
        {
            "call_id": "call_read_b",
            "tool": "read_file",
            "success": True,
            "status": "success",
            "data": {"path": "b.txt", "content": "B"},
        },
    ]
    task_state.metadata["completion_observations"] = reads
    two_reads = build_final_observation_context(
        task_state,
        _outcome("terminal_success", "read_file", reads[-1]),
    )
    two_read_summary = final_observation_context_trace_summary(two_reads)
    assert len(two_reads) == 2
    assert set(two_read_summary["call_ids"]) == {"call_read_a", "call_read_b"}
    assert two_read_summary["expected_call_ids"] == ["call_read_a", "call_read_b"]
    assert two_read_summary["represented_call_ids"] == ["call_read_a", "call_read_b"]
    assert two_read_summary["missing_call_ids"] == []
    assert two_read_summary["coverage_complete"] is True

    sparse = {"observation_id": "obs-shared", "tool": "sandbox_exec", "success": True, "status": "success", "data": {"command": "echo ok", "exit_code": 0}}
    richer = {"observation_id": "obs-shared", "tool": "sandbox_exec", "success": True, "status": "success", "data": {"command": "echo ok", "exit_code": 0, "stdout": "ok\n"}}
    task_state.metadata["completion_observations"] = [richer]
    preferred = build_final_observation_context(task_state, _outcome("terminal_success", "sandbox_exec", sparse))
    assert len(preferred) == 1
    assert preferred[0]["source"] == "task_state"
    assert "ok" in preferred[0]["data_summary"]["stdout_preview"]


def test_task_scoped_memory_and_call_identity() -> None:
    memory = Memory()
    memory.messages.extend(
        [
            {
                "role": "tool",
                "tool_call_id": "call-old",
                "name": "sandbox_exec",
                "content": json.dumps({"tool": "sandbox_exec", "success": True, "status": "success", "data": {"stdout": "old"}}),
                "metadata": {"task_id": "task-old", "scope": "task"},
            },
            {
                "role": "user",
                "content": "current request",
                "metadata": {"task_id": "task-current", "scope": "task"},
            },
            {
                "role": "tool",
                "tool_call_id": "call-123",
                "name": "sandbox_exec",
                "content": json.dumps(
                    {
                        "success": True,
                        "status": "success",
                        "data": {"command": "echo ok", "exit_code": 0},
                    }
                ),
                "metadata": {"task_id": "task-current", "scope": "task"},
            },
        ]
    )
    current_messages = memory.get_task_messages("task-current")
    rendered_messages = json.dumps(current_messages, ensure_ascii=False)
    assert "call-old" not in rendered_messages
    assert "call-123" in rendered_messages
    assert all("metadata" not in message for message in current_messages)

    task_state = DummyTaskState()
    terminal = {
        "call_id": "call-123",
        "observation_id": "obs-1",
        "tool": "sandbox_exec",
        "success": True,
        "status": "success",
        "data": {"command": "echo ok", "exit_code": 0},
    }
    richer = {
        **terminal,
        "data": {"command": "echo ok", "exit_code": 0, "stdout": "ok\n"},
    }
    task_state.metadata["completion_observations"] = [richer]
    context = build_final_observation_context(
        task_state,
        _outcome("terminal_success", "sandbox_exec", terminal),
    )
    summary = final_observation_context_trace_summary(context)
    assert len(context) == 1
    assert context[0]["call_id"] == "call-123"
    assert context[0]["source"] == "task_state"
    assert "ok" in context[0]["data_summary"]["stdout_preview"]
    assert summary["raw_observation_count"] == 2
    assert summary["observation_count"] == 1
    assert summary["duplicates_removed"] == 1
    assert summary["call_ids"] == ["call-123"]

    second = {**richer, "call_id": "call-456", "observation_id": "obs-2"}
    task_state.metadata["completion_observations"] = [richer, second]
    distinct = build_final_observation_context(
        task_state,
        _outcome("terminal_success", "sandbox_exec", terminal),
    )
    distinct_summary = final_observation_context_trace_summary(distinct)
    assert len(distinct) == 2
    assert set(distinct_summary["call_ids"]) == {"call-123", "call-456"}


def test_partial_outcome_keeps_success_and_block_without_policy_pollution() -> None:
    task_state = DummyTaskState()
    success = {
        "call_id": "call-read",
        "tool": "read_file",
        "success": True,
        "status": "success",
        "data": {"path": "a.txt", "content": "READ_RESULT"},
    }
    blocked = {
        "call_id": "call-command",
        "tool": "sandbox_exec",
        "success": False,
        "status": "blocked",
        "error": "dangerous command blocked",
        "error_code": "dangerous_command",
    }
    task_state.metadata["completion_observations"] = [success, blocked]
    context = build_final_observation_context(
        task_state,
        _outcome("terminal_policy_blocked", "sandbox_exec", blocked, policy_code="dangerous_command"),
    )
    summary = final_observation_context_trace_summary(context)
    by_call = {item["call_id"]: item for item in context}
    assert len(context) == 2
    assert set(summary["call_ids"]) == {"call-read", "call-command"}
    assert summary["duplicates_removed"] == 1
    assert by_call["call-read"].get("policy_code", "") == ""
    assert by_call["call-command"]["policy_code"] == "dangerous_command"


def test_read_document_keeps_canonical_table_summary() -> None:
    previous_workspace = get_current_workspace()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            set_current_workspace(WorkspaceManager(root).get_context("smoke", "final-document-table"))
            source = root / "source.xlsx"
            source.write_bytes(b"PK\x03\x04source")
            stored = ToolResultStore().store_canonical_json(
                {"result": {"tables": [["reader result"]]}},
                task_id="final-document-table",
                call_id="read-document",
                kind="content",
            )
            table = {
                "name": "Sheet1",
                "source_row_count": 100,
                "source_column_count": 30,
                "parsed_row_count": 50,
                "parsed_column_count": 20,
                "visible_row_count": 8,
                "visible_column_count": 12,
                "row_count": 100,
                "column_count": 30,
                "headers": [],
                "preview_rows": [[f"S1-R{row}-C{column}" for column in range(1, 13)] for row in range(1, 9)],
                "reader_omitted_rows": 50,
                "reader_omitted_columns": 10,
                "projection_omitted_rows": 42,
                "projection_omitted_columns": 8,
                "omitted_rows": 92,
                "omitted_columns": 18,
                "reader_truncated": True,
                "truncated": True,
            }
            canonical = {
                "excerpt": "parsed workbook",
                "table_count": 7,
                "total_sheet_count": 7,
                "parsed_sheet_count": 5,
                "visible_table_count": 1,
                "tables": [table],
                "reader_omitted_tables": 2,
                "projection_omitted_tables": 4,
                "omitted_tables": 6,
                "reader_truncated": True,
                "truncated": True,
                "compacted": True,
            }
            observation = {
                "call_id": "read-document",
                "tool": "read_document",
                "success": True,
                "status": "success",
                "data": {
                    "result": canonical,
                    "source_ref": str(source),
                    "source_sha256": "source-sha",
                    "content_ref": stored.content_ref,
                    "content_chars": stored.chars,
                    "content_bytes": stored.bytes,
                    "content_sha256": stored.sha256,
                    "content_externalized": True,
                },
            }
            context = build_final_observation_context(
                DummyTaskState(),
                _outcome("terminal_success", "read_document", observation),
            )
            summary = context[0]["data_summary"]
            assert summary["table_count"] == 7 and summary["total_sheet_count"] == 7
            assert summary["parsed_sheet_count"] == 5 and summary["visible_table_count"] == 1
            assert summary["reader_omitted_tables"] == 2 and summary["projection_omitted_tables"] == 4
            first = summary["tables"][0]
            assert first["source_row_count"] == 100 and first["source_column_count"] == 30
            assert first["parsed_row_count"] == 50 and first["parsed_column_count"] == 20
            assert first["visible_row_count"] == 8 and first["visible_column_count"] == 12
            assert first["reader_omitted_rows"] == 50 and first["projection_omitted_rows"] == 42
            assert first["omitted_rows"] == 92 and first["omitted_columns"] == 18
            rendered = json.dumps(context, ensure_ascii=False)
            assert "S1-R1-C1" in rendered and "S1-R100-C30" not in rendered
            assert summary["source_ref"] == str(source)
            assert summary["content_ref"] == stored.content_ref
            source.unlink()
            assert "S1-R1-C1" in json.dumps(context, ensure_ascii=False)
    finally:
        set_current_workspace(previous_workspace)


def test_legacy_read_document_rows_become_preview_rows() -> None:
    observation = {
        "call_id": "legacy-document-rows",
        "tool": "read_document",
        "success": True,
        "status": "success",
        "data": {
            "result": {
                "tables": [{"name": "Sheet1", "rows": [["A", "B"], ["C", "D"]]}],
                "compacted": True,
            }
        },
    }
    context = build_final_observation_context(
        DummyTaskState(),
        _outcome("terminal_success", "read_document", observation),
    )
    table = context[0]["data_summary"]["tables"][0]
    assert table["preview_rows"] == [["A", "B"], ["C", "D"]]
    assert table["visible_row_count"] == 2 and table["visible_column_count"] == 2
    assert table["omitted_rows"] == 0 and table["omitted_columns"] == 0
    assert not any(key in table for key in ("rows", "data", "values"))


def test_no_user_text_keyword_routing() -> None:
    source = inspect.getsource(observation_context)
    assert "re.search" not in source
    assert "user_input" not in source
    assert "user_goal" not in source
    assert "状态" not in source
    assert "调用" not in source
    assert "错误" not in source


def main() -> None:
    test_fetch_url_execution_fields()
    test_read_file_long_content_compacted()
    test_sandbox_exec_stdio_compacted()
    test_multiple_observations_budget_and_dedupe()
    test_task_scoped_memory_and_call_identity()
    test_partial_outcome_keeps_success_and_block_without_policy_pollution()
    test_read_document_keeps_canonical_table_summary()
    test_legacy_read_document_rows_become_preview_rows()
    test_no_user_text_keyword_routing()
    print("smoke_final_observation_context ok")


if __name__ == "__main__":
    main()
