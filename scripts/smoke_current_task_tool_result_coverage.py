"""Smoke checks for complete current-task tool-result coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.final_responder import build_final_responder_pack_from_snapshot
from core.finalization_context_snapshot import build_finalization_context_snapshot
from core.final_observation_context import (
    build_final_observation_context,
    final_observation_context_trace_summary,
)
from core.loop import AgentLoop
from core.memory import Memory
from core.prompt_pack import build_final_answer_pack
from core.runtime_metrics import RuntimeMetrics
from core.state import TaskState
from core.tool_completion import record_completion_observation
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.trace import AgentTrace
from providers.mock import MockProvider, assistant_message


def _observation(call_id: str, tool: str = "sandbox_exec", *, value: str = "ok", status: str = "success") -> dict[str, object]:
    success = status in {"success", "completed"}
    data: dict[str, object]
    if tool == "read_file":
        data = {"content": value}
    elif tool == "git_status":
        data = {"items": [f"{value}-{index}-" + ("x" * 1600) for index in range(8)]}
    else:
        data = {"command": f"echo {call_id}", "exit_code": 0 if success else 1, "stdout": value}
    return {
        "call_id": call_id,
        "provider_call_id": call_id,
        "observation_id": f"obs-{call_id}",
        "tool": tool,
        "status": status,
        "success": success,
        "error_code": "blocked_by_policy" if status == "blocked" else "",
        "data": data,
    }


def _state(observations: list[dict[str, object]]) -> TaskState:
    state = TaskState.create(user_goal="coverage smoke", task_type="build", plan=[], task_profile=None)
    state.metadata["completion_observations"] = [dict(item) for item in observations]
    return state


def _outcome(observation: dict[str, object] | None = None) -> ToolOutcomeResolution:
    observation = dict(observation or {})
    return ToolOutcomeResolution(
        "terminal_success",
        "coverage_smoke",
        tool=str(observation.get("tool") or "sandbox_exec"),
        status=str(observation.get("status") or "success"),
        metadata={"observation": observation} if observation else {},
    )


def test_eight_terminal_calls_are_all_represented() -> None:
    observations = [_observation(f"call-{index}", value=f"result-{index}") for index in range(8)]
    context = build_final_observation_context(_state(observations), _outcome(observations[-1]))
    summary = final_observation_context_trace_summary(context)
    expected = [f"call-{index}" for index in range(8)]
    assert len(context) == 8
    assert summary["expected_call_ids"] == expected
    assert summary["represented_call_ids"] == expected
    assert summary["missing_call_ids"] == []
    assert summary["expected_call_count"] == 8
    assert summary["represented_call_count"] == 8
    assert summary["coverage_complete"] is True


def test_twenty_five_completion_observations_are_retained() -> None:
    state = _state([])
    for index in range(25):
        record_completion_observation(state, "sandbox_exec", _observation(f"call-{index}", value=f"value-{index}"))
    observations = state.metadata["completion_observations"]
    assert len(observations) == 25
    assert observations[0]["call_id"] == "call-0"
    context = build_final_observation_context(state, _outcome(observations[-1]))
    assert len(context) == 25
    assert [item["call_id"] for item in context] == [f"call-{index}" for index in range(25)]


def test_large_mixed_result_does_not_displace_other_calls() -> None:
    observations = [
        _observation("call-read-a", "read_file", value="FILE_A"),
        _observation("call-read-b", "read_file", value="FILE_B"),
        _observation("call-command", value="COMMAND_OK"),
        _observation("call-git", "git_status", value="GIT_STATUS"),
    ]
    context = build_final_observation_context(_state(observations), _outcome(observations[-1]))
    summary = final_observation_context_trace_summary(context)
    assert [item["call_id"] for item in context] == [
        "call-read-a",
        "call-read-b",
        "call-command",
        "call-git",
    ]
    assert summary["total_chars"] > 8000
    assert summary["coverage_complete"] is True


def test_same_tool_different_call_ids_stay_distinct() -> None:
    observations = [_observation(f"call-shell-{index}", value=f"OUTPUT_{index}") for index in range(3)]
    context = build_final_observation_context(_state(observations), _outcome(observations[-1]))
    assert len(context) == 3
    assert [item["call_id"] for item in context] == [f"call-shell-{index}" for index in range(3)]
    rendered = json.dumps(context, ensure_ascii=False)
    for index in range(3):
        assert f"OUTPUT_{index}" in rendered


def test_duplicate_sources_merge_at_task_state_position() -> None:
    sparse = _observation("call-shared", value="")
    state = _state([sparse])
    terminal = _observation("call-shared", value="terminal")
    memory_messages = [
        {
            "role": "tool",
            "name": "sandbox_exec",
            "tool_call_id": "call-shared",
            "content": json.dumps(_observation("call-shared", value="MEMORY_RICHEST")),
        }
    ]
    context = build_final_observation_context(state, _outcome(terminal))
    summary = final_observation_context_trace_summary(context)
    assert len(context) == 1
    assert context[0]["source"] == "task_state"
    assert "MEMORY_RICHEST" not in json.dumps(context[0], ensure_ascii=False)
    assert "terminal" in json.dumps(context[0], ensure_ascii=False)
    assert summary["raw_observation_count"] == 2
    assert summary["duplicates_removed"] == 1


def test_partial_outcome_has_complete_coverage_and_full_pack() -> None:
    observations = [
        _observation("call-success-a", "read_file", value="SUCCESS_A"),
        _observation("call-success-b", value="SUCCESS_B"),
        _observation("call-blocked", value="BLOCKED", status="blocked"),
    ]
    state = _state(observations)
    blocked_outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="sandbox_exec",
        status="blocked",
        policy_code="blocked_by_policy",
        metadata={"observation": observations[-1]},
    )
    snapshot = build_finalization_context_snapshot(
        user_request="coverage smoke",
        task_state=state,
        outcome=blocked_outcome,
    )
    pack = build_final_responder_pack_from_snapshot(snapshot)
    assert snapshot.result_count == 3
    assert snapshot.coverage_complete is True
    rendered = json.dumps(pack.messages, ensure_ascii=False)
    assert "SUCCESS_A" in rendered
    assert "SUCCESS_B" in rendered
    assert "blocked_by_policy" in rendered


def test_missing_terminal_result_blocks_final_llm() -> None:
    state = _state([])
    state.metadata["build_step_contract"] = {
        "steps": [{"index": 1, "status": "completed", "call_id": "call-missing", "tool_name": "read_file"}],
    }
    context = build_final_observation_context(state, _outcome())
    summary = final_observation_context_trace_summary(context)
    assert summary["missing_call_ids"] == ["call-missing"]
    assert summary["coverage_complete"] is False

    llm = MockProvider(responses=[assistant_message("must not be called")])
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = llm
    loop.memory = Memory()
    loop._runtime_metrics = None
    trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
    answer = loop._build_final_answer_from_terminal_outcome(state, _outcome(), trace, 1)
    assert len(llm.calls) == 0
    assert "结果不完整" in answer
    context_event = next(event.to_dict() for event in trace.events if event.event_type == "final_observation_context")
    assert context_event["success"] is False
    assert context_event["data"]["missing_call_ids"] == ["call-missing"]
    assert any(event.event_type == "emergency_finalization_fallback" for event in trace.events)


def test_pending_step_is_not_expected() -> None:
    completed = _observation("call-completed", value="DONE")
    state = _state([completed])
    state.metadata["build_step_contract"] = {
        "steps": [
            {"index": 1, "status": "completed", "call_id": "call-completed"},
            {"index": 2, "status": "pending", "call_id": "call-pending"},
        ]
    }
    summary = final_observation_context_trace_summary(
        build_final_observation_context(state, _outcome(completed))
    )
    assert summary["expected_call_ids"] == ["call-completed"]
    assert summary["missing_call_ids"] == []
    assert summary["coverage_complete"] is True


def test_prompt_pack_requires_snapshot_and_keeps_all_results() -> None:
    try:
        build_final_answer_pack()
    except ValueError as exc:
        assert str(exc) == "finalization_context_snapshot_required"
    else:
        raise AssertionError("final answer pack accepted missing snapshot")
    observations = [
        _observation(f"call-{index}", value=f"value-{index}")
        for index in range(8)
    ]
    state = _state(observations)
    snapshot = build_finalization_context_snapshot(
        user_request="coverage smoke",
        task_state=state,
        outcome=_outcome(observations[-1]),
    )
    pack = build_final_answer_pack(snapshot=snapshot)
    payload = json.loads(str(pack.messages[1]["content"]))
    evidence = payload["results"]
    assert len(evidence) == 8
    assert all(item["tool"] == "sandbox_exec" for item in evidence)


def test_runtime_metrics_keep_coverage_fields() -> None:
    metrics = RuntimeMetrics()
    metrics.record_final_observation_context(
        {
            "observation_count": 2,
            "call_ids": ["call-a", "call-b"],
            "expected_call_ids": ["call-a", "call-b", "call-c"],
            "represented_call_ids": ["call-a", "call-b"],
            "missing_call_ids": ["call-c"],
            "coverage_complete": False,
        }
    )
    summary = metrics.summary()
    assert summary["final_observation_count"] == 2
    assert summary["final_observation_call_ids"] == ["call-a", "call-b"]
    assert summary["expected_call_ids"] == ["call-a", "call-b", "call-c"]
    assert summary["represented_call_ids"] == ["call-a", "call-b"]
    assert summary["missing_call_ids"] == ["call-c"]
    assert summary["coverage_complete"] is False


def main() -> None:
    test_eight_terminal_calls_are_all_represented()
    test_twenty_five_completion_observations_are_retained()
    test_large_mixed_result_does_not_displace_other_calls()
    test_same_tool_different_call_ids_stay_distinct()
    test_duplicate_sources_merge_at_task_state_position()
    test_partial_outcome_has_complete_coverage_and_full_pack()
    test_missing_terminal_result_blocks_final_llm()
    test_pending_step_is_not_expected()
    test_prompt_pack_requires_snapshot_and_keeps_all_results()
    test_runtime_metrics_keep_coverage_fields()
    print("smoke_current_task_tool_result_coverage ok")


if __name__ == "__main__":
    main()
