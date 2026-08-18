"""Offline smoke checks for isolated finalization snapshots and prompts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.finalization_context_budget import apply_finalization_context_budget
from core.finalization_context_snapshot import build_finalization_context_snapshot
from core.loop import AgentLoop
from core.memory import Memory
from core.prompt_pack import (
    build_agent_terminal_synthesis_pack,
    build_agent_continuation_pack,
)
from core.state import TaskState
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.trace import AgentTrace
from providers.mock import MockProvider, assistant_message
from scripts.smoke_tool_error_continuation import (
    _call,
    _failed,
    _message,
    _run,
)


POISON_MARKERS = (
    "POISON_PREVIOUS_DIALOGUE",
    "POISON_CONTEXT_SUMMARY",
    "POISON_TOOL_SCOPE",
    "POISON_PLANNER",
    "POISON_REFLECTION",
    "POISON_RECOVERY",
    "POISON_CANDIDATE",
)


def _observation(
    call_id: str,
    tool: str,
    *,
    success: bool,
    status: str,
    data: dict[str, Any] | None = None,
    error_code: str = "",
    policy_code: str = "",
) -> dict[str, Any]:
    return {
        "task_id": "hidden-task",
        "call_id": call_id,
        "provider_call_id": f"provider-{call_id}",
        "observation_id": f"observation-{call_id}",
        "source": "hidden-source",
        "schema_version": "hidden-schema",
        "tool": tool,
        "success": success,
        "status": status,
        "error": error_code,
        "error_code": error_code,
        "policy_code": policy_code,
        "data": dict(data or {}),
    }


def _state(
    observations: list[dict[str, Any]],
    *,
    expected: list[str] | None = None,
) -> TaskState:
    state = TaskState.create(
        user_goal="汇总当前任务的结构化结果",
        task_type="tool_use",
        plan=[],
        task_profile=None,
    )
    state.metadata["completion_observations"] = [dict(item) for item in observations]
    state.metadata["structured_tool_call_ids"] = list(
        expected
        if expected is not None
        else [str(item.get("call_id") or "") for item in observations]
    )
    return state


def _outcome(
    kind: str,
    observation: dict[str, Any] | None = None,
    *,
    failure_disposition: str = "none",
) -> ToolOutcomeResolution:
    return ToolOutcomeResolution(
        kind,
        "snapshot_smoke",
        tool=str((observation or {}).get("tool") or ""),
        status=str((observation or {}).get("status") or "failed"),
        failure_disposition=failure_disposition,
        policy_code=str((observation or {}).get("policy_code") or ""),
        metadata={"observation": observation} if observation else {},
    )


def test_normal_agent_continuation_is_unchanged(root: Path) -> None:
    source = root / "normal.txt"
    source.write_text("normal result", encoding="utf-8")
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("read-normal", "read_file", {"path": str(source)})]),
            _message("读取结果是 normal result。"),
        ],
        tools={
            "read_file": lambda path, **_: {
                "success": True,
                "status": "success",
                "data": {"path": path, "content": "normal result"},
            }
        },
        request="读取这个文件",
    )
    assert answer and executed == ["read_file"]
    assert len(llm.calls) == 2
    assert llm.calls[1]["tools"]
    metrics = captured["metrics"].summary()
    assert metrics["finalization_snapshot_count"] == 0
    assert metrics["isolated_finalization_pack_count"] == 0
    assert not any(
        event.event_type == "finalization_context_snapshot"
        for event in captured["trace"].events
    )
    assert build_agent_continuation_pack


def test_ordinary_failure_stays_in_normal_agent_context(root: Path) -> None:
    poison_memory = Memory()
    poison_memory.messages.extend(
        [
            {"role": "user", "content": POISON_MARKERS[0]},
            {"role": "system", "content": "Context Summary: " + POISON_MARKERS[1]},
            {"role": "system", "content": " ".join(POISON_MARKERS[2:])},
            {"role": "assistant", "content": POISON_MARKERS[-1]},
        ]
    )
    missing = root / "missing.txt"
    final_prose = "当前读取失败，无法提供文件内容。"
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("read-missing", "read_file", {"path": str(missing)})]),
            _message(final_prose),
        ],
        tools={
            "read_file": lambda path, **_: _failed(
                "file_not_found",
                path=path,
            )
        },
        request="读取指定文件",
        memory=poison_memory,
    )
    assert answer == final_prose and executed == ["read_file"]
    continuation_call = llm.calls[1]
    rendered = json.dumps(continuation_call["messages"], ensure_ascii=False)
    assert continuation_call["tools"]
    assert "read-missing" in rendered
    assert "file_not_found" in rendered
    assert not any(
        event.event_type == "isolated_finalization_pack"
        for event in captured["trace"].events
    )
    metrics = captured["metrics"].summary()
    assert metrics["finalization_snapshot_count"] == 0
    assert metrics["isolated_finalization_pack_count"] == 0
    assert metrics["isolated_terminal_synthesis_output_reject_count"] == 0
    assert metrics["isolated_terminal_synthesis_provider_error_count"] == 0
    assert metrics["isolated_terminal_synthesis_fallback_count"] == 0
    assert not any(
        event.event_type == "isolated_terminal_synthesis_output"
        for event in captured["trace"].events
    )


def test_snapshot_projection_coverage_and_statuses() -> None:
    read = _observation(
        "read-call",
        "read_file",
        success=True,
        status="success",
        data={
            "path": "/tmp/source.txt",
            "preview": "visible",
            "content_ref": "/internal/content-ref",
            "source_ref": "/internal/source-ref",
            "source_sha256": "hidden-sha256",
        },
    )
    read["metadata"] = {
        "tool_arguments": {"path": "/internal/raw-argument-marker"}
    }
    command = _observation(
        "exec-call",
        "sandbox_exec",
        success=False,
        status="failed",
        data={"exit_code": 2, "stderr_preview": "failed"},
        error_code="command_failed",
    )
    blocked = _observation(
        "write-call",
        "write_file",
        success=False,
        status="blocked",
        data={"path": "/tmp/output.txt"},
        error_code="write_blocked",
        policy_code="write_blocked",
    )
    state = _state([read, command, blocked])
    snapshot = build_finalization_context_snapshot(
        user_request=state.user_goal,
        task_state=state,
        outcome=_outcome("terminal_policy_blocked", blocked),
        finalization_mode="policy_blocked",
        finalization_reason="write_blocked",
    )
    assert snapshot.result_count == 3
    assert snapshot.model_context["final_status"] == "partially_completed"
    assert [item["tool"] for item in snapshot.model_context["results"]] == [
        "read_file",
        "sandbox_exec",
        "write_file",
    ]
    rendered_model = json.dumps(snapshot.model_context, ensure_ascii=False)
    rendered_trace = json.dumps(snapshot.trace_context, ensure_ascii=False)
    for hidden in (
        "hidden-task",
        "provider-read-call",
        "observation-read-call",
        "hidden-source",
        "hidden-schema",
        "/internal/content-ref",
        "/internal/source-ref",
        "hidden-sha256",
        "/internal/raw-argument-marker",
    ):
        assert hidden not in rendered_model
    assert "read-call" in rendered_trace
    assert snapshot.snapshot_hash not in rendered_model
    pack = build_agent_terminal_synthesis_pack(snapshot)
    assert pack.message_count == 2 and pack.tool_schema_chars == 0
    assert snapshot.snapshot_hash not in str(pack.messages[1]["content"])
    exposed_model = snapshot.model_context
    exposed_model["results"].clear()
    exposed_trace = snapshot.trace_context
    exposed_trace["missing_call_ids"] = ["mutated"]
    assert snapshot.result_count == 3
    assert len(snapshot.model_context["results"]) == 3
    assert snapshot.trace_context["missing_call_ids"] == []

    repeated = [
        _observation(
            f"shell-{index}",
            "sandbox_exec",
            success=False,
            status="failed",
            data={"exit_code": 1, "stderr_preview": f"failure-{index}"},
            error_code="command_failed",
        )
        for index in range(3)
    ]
    for item in repeated:
        item["provider_call_id"] = "shared-provider-id"
    distinct = build_finalization_context_snapshot(
        user_request="run",
        task_state=_state(repeated),
        outcome=_outcome("terminal_failure", repeated[-1]),
    )
    assert distinct.result_count == 3

    stopped = _observation(
        "stop-call",
        "sandbox_exec",
        success=False,
        status="failed",
        data={
            "stopped_before_execution": True,
            "arguments_fingerprint": "hidden-fingerprint",
        },
        error_code="exact_tool_call_loop_detected",
    )
    stopped_snapshot = build_finalization_context_snapshot(
        user_request="run",
        task_state=_state([stopped]),
        outcome=_outcome(
            "terminal_failure",
            stopped,
            failure_disposition="no_progress",
        ),
    )
    assert stopped_snapshot.model_context["final_status"] == "stopped"
    stopped_rendered = json.dumps(stopped_snapshot.model_context, ensure_ascii=False)
    assert "stopped_before_execution" in stopped_rendered
    assert "hidden-fingerprint" not in stopped_rendered


def test_memory_does_not_fill_missing_result() -> None:
    present = _observation(
        "present-call",
        "read_file",
        success=True,
        status="success",
        data={"path": "/tmp/present.txt", "preview": "present"},
    )
    state = _state([present], expected=["present-call", "missing-call"])
    snapshot = build_finalization_context_snapshot(
        user_request=state.user_goal,
        task_state=state,
        outcome=_outcome("terminal_failure"),
    )
    assert snapshot.coverage_complete is False
    assert snapshot.model_context["final_status"] == "incomplete_evidence"
    assert "missing-call" not in json.dumps(snapshot.model_context)
    assert snapshot.trace_context["missing_call_ids"] == ["missing-call"]

    llm = MockProvider(responses=[assistant_message("must not run")])
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = llm
    loop.memory = Memory()
    loop.memory.add_tool_observation(
        tool_call_id="missing-call",
        tool_name="sandbox_exec",
        observation_json=json.dumps(
            _observation(
                "missing-call",
                "sandbox_exec",
                success=True,
                status="success",
            )
        ),
        task_id=state.task_id,
    )
    loop._runtime_metrics = None
    trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
    answer = loop._build_final_answer_from_terminal_outcome(
        state,
        _outcome("terminal_failure"),
        trace,
        1,
    )
    assert len(llm.calls) == 0
    assert "结果不完整" in answer
    assert "missing-call" not in answer


def test_twenty_five_results_are_compacted_not_dropped() -> None:
    observations = [
        _observation(
            f"call-{index}",
            "read_file",
            success=True,
            status="success",
            data={
                "path": f"/tmp/source-{index}.txt",
                "preview": "x" * 1800,
            },
        )
        for index in range(25)
    ]
    state = _state(observations)
    snapshot = build_finalization_context_snapshot(
        user_request=state.user_goal,
        task_state=state,
        outcome=_outcome("terminal_success", observations[-1]),
    )
    budgeted, decision = apply_finalization_context_budget(
        snapshot,
        model_context_tokens=4096,
        model_input_tokens=4096,
        model_output_tokens=512,
        reserved_output_tokens=512,
    )
    assert budgeted.result_count == 25
    assert len(budgeted.model_context["results"]) == 25
    assert decision.dropped_result_count == 0
    assert decision.truncated_result_count > 0
    assert decision.final_tokens <= decision.original_tokens
    assert decision.action == "compact"
    assert budgeted.snapshot_hash != snapshot.snapshot_hash
    for key in (
        "expected_call_ids",
        "represented_call_ids",
        "missing_call_ids",
        "coverage_complete",
    ):
        assert budgeted.trace_context[key] == snapshot.trace_context[key]


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        test_normal_agent_continuation_is_unchanged(root)
        test_ordinary_failure_stays_in_normal_agent_context(root)
    test_snapshot_projection_coverage_and_statuses()
    test_memory_does_not_fill_missing_result()
    test_twenty_five_results_are_compacted_not_dropped()
    print("smoke_finalization_context_isolation ok")


if __name__ == "__main__":
    main()
