"""Offline smoke checks for the neutral terminal and emergency finalization boundary."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.emergency_finalization import (
    build_emergency_finalization,
)
from core.finalization_context_snapshot import build_finalization_context_snapshot
from core.finalization_outlet import resolve_finalization_outlet
from core.loop import AgentLoop
from core.memory import Memory
from core.state import TaskState
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.trace import AgentTrace
from providers.mock import MockProvider, assistant_message


def _loop(responses: list[Any]) -> tuple[AgentLoop, MockProvider]:
    llm = MockProvider(responses=responses)
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = llm
    loop.memory = Memory()
    loop._runtime_metrics = None
    return loop, llm


def _state() -> TaskState:
    return TaskState.create(
        user_goal="检查结构化工具结果",
        task_type="tool_use",
        plan=[],
        task_profile=None,
    )


def _trace(state: TaskState) -> AgentTrace:
    return AgentTrace(state.task_id, state.user_goal, state.task_type)


def _status_success() -> ToolOutcomeResolution:
    observation = {
        "call_id": "status-call",
        "tool": "get_usage_status",
        "success": True,
        "status": "success",
        "data": {
            "status": "available",
        },
    }
    return ToolOutcomeResolution(
        "terminal_success",
        "status_tool_success",
        tool="get_usage_status",
        status="success",
        user_message="当前搜索提供商为 tavily。",
        metadata={"observation": observation},
    )


def _execution_failure() -> ToolOutcomeResolution:
    observation = {
        "call_id": "exec-call",
        "tool": "sandbox_exec",
        "success": False,
        "status": "failed",
        "error": "command not found",
        "error_code": "command_not_found",
        "data": {
            "exit_code": 127,
            "stderr": "command not found",
        },
    }
    return ToolOutcomeResolution(
        "terminal_failure",
        "execution_failed_nonzero_exit",
        tool="sandbox_exec",
        status="failed",
        metadata={"observation": observation},
    )


def _events(trace: AgentTrace, event_type: str) -> list[dict[str, Any]]:
    return [
        event.to_dict()
        for event in trace.events
        if event.event_type == event_type
    ]


def _finalize(
    responses: list[Any],
    outcome: ToolOutcomeResolution,
    *,
    state: TaskState | None = None,
) -> tuple[str, MockProvider, AgentTrace]:
    active_state = state or _state()
    loop, llm = _loop(responses)
    trace = _trace(active_state)
    answer = loop._build_final_answer_from_terminal_outcome(
        active_state,
        outcome,
        trace,
        1,
    )
    return answer, llm, trace


def test_terminal_responder_valid_prose_skips_emergency() -> None:
    answer, llm, trace = _finalize(
        [assistant_message("当前搜索提供商为 Tavily。")],
        _status_success(),
    )
    assert answer == "当前搜索提供商为 Tavily。"
    assert len(llm.calls) == 1 and llm.calls[0]["tools"] == []
    assert _events(trace, "terminal_responder_validation")[-1]["success"] is True
    assert not _events(trace, "emergency_finalization_fallback")


def test_invalid_terminal_responses_use_emergency() -> None:
    tool_call = SimpleNamespace(
        id="final-tool",
        function=SimpleNamespace(name="fetch_url", arguments="{}"),
    )
    cases = (
        (assistant_message(""), "empty_content"),
        (
            assistant_message("", tool_calls=[tool_call]),
            "assistant_requested_tool_call",
        ),
        (
            assistant_message('<tool_call name="fetch_url">{}</tool_call>'),
            "raw_tool_text_in_final_responder",
        ),
        (
            assistant_message("我会再执行命令。"),
            "assistant_committed_future_tool_action",
        ),
    )
    for response, expected_reason in cases:
        answer, _, trace = _finalize([response], _status_success())
        assert "tavily" in answer.lower()
        emergency = _events(trace, "emergency_finalization_fallback")
        assert emergency
        assert emergency[-1]["data"]["fallback_reason"] == expected_reason
        assert (
            _events(trace, "final_answer_path")[-1]["data"]["path"]
            == "emergency_fallback"
        )


def test_terminal_failure_prefers_responder_then_falls_back() -> None:
    accepted, _, accepted_trace = _finalize(
        [assistant_message("命令执行失败，退出码为 127：command not found。")],
        _execution_failure(),
    )
    assert "127" in accepted and "失败" in accepted
    assert not _events(accepted_trace, "emergency_finalization_fallback")

    fallback, _, fallback_trace = _finalize(
        [assistant_message("")],
        _execution_failure(),
    )
    assert "command_not_found" in fallback
    assert "127" in fallback
    assert "失败" in fallback
    assert (
        _events(fallback_trace, "emergency_finalization_fallback")[-1]["data"][
            "fallback_reason"
        ]
        == "empty_content"
    )


def test_partial_success_and_blocked_preserve_both_without_ids() -> None:
    success = {
        "call_id": "read-call",
        "provider_call_id": "provider-read",
        "observation_id": "observation-read",
        "tool": "read_file",
        "success": True,
        "status": "success",
        "data": {"path": "a.txt", "content": "VISIBLE_RESULT"},
    }
    blocked = {
        "call_id": "blocked-call",
        "provider_call_id": "provider-blocked",
        "observation_id": "observation-blocked",
        "tool": "sandbox_exec",
        "success": False,
        "status": "blocked",
        "error": "dangerous command blocked",
        "error_code": "dangerous_command",
        "policy_code": "dangerous_command",
    }
    state = _state()
    state.metadata["completion_observations"] = [success, blocked]
    outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="sandbox_exec",
        status="blocked",
        policy_code="dangerous_command",
        metadata={"observation": blocked},
    )
    snapshot = build_finalization_context_snapshot(
        user_request=state.user_goal,
        task_state=state,
        outcome=outcome,
    )
    result = build_emergency_finalization(
        snapshot,
        reason="test_boundary",
        fallback_reason="empty_content",
    )
    assert "部分完成" in result.content
    assert "VISIBLE_RESULT" in result.content
    assert "dangerous_command" in result.content
    for hidden in (
        "read-call",
        "provider-read",
        "observation-read",
        "blocked-call",
        "provider-blocked",
        "observation-blocked",
    ):
        assert hidden not in result.content


def test_outlet_and_emergency_do_not_route_on_user_text() -> None:
    outlet_signature = inspect.signature(resolve_finalization_outlet)
    emergency_signature = inspect.signature(
        build_emergency_finalization
    )
    assert "user_input" not in outlet_signature.parameters
    assert "user_goal" not in outlet_signature.parameters
    assert "user_input" not in emergency_signature.parameters
    assert "user_goal" not in emergency_signature.parameters
    outlet_source = inspect.getsource(resolve_finalization_outlet)
    assert "user_input" not in outlet_source
    assert "user_goal" not in outlet_source
    assert "re.search" not in outlet_source


def main() -> None:
    test_terminal_responder_valid_prose_skips_emergency()
    test_invalid_terminal_responses_use_emergency()
    test_terminal_failure_prefers_responder_then_falls_back()
    test_partial_success_and_blocked_preserve_both_without_ids()
    test_outlet_and_emergency_do_not_route_on_user_text()
    print("smoke_emergency_finalization_boundary ok")


if __name__ == "__main__":
    main()
