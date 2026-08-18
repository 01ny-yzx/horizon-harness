"""Offline smoke checks for partial-success policy-blocked finalization."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.final_observation_context import build_final_observation_context
from core.finalization_outlet import resolve_finalization_outlet
from core.loop import AgentLoop
from core.memory import Memory
from core.state import TaskState
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.trace import AgentTrace
from providers.mock import MockProvider, assistant_message


def _success(call_id: str, tool: str, value: str) -> dict[str, Any]:
    return {
        "observation_id": f"obs_{call_id}",
        "call_id": call_id,
        "provider_call_id": call_id,
        "tool": tool,
        "kind": "file_read" if tool == "read_file" else "execution",
        "status": "success",
        "success": True,
        "data": {
            "path": value if tool == "read_file" else "",
            "content": f"result:{value}",
            "stdout": f"result:{value}",
            "exit_code": 0,
        },
    }


def _blocked(call_id: str = "call_blocked") -> dict[str, Any]:
    observation = {
        "observation_id": f"obs_{call_id}" if call_id else "",
        "call_id": call_id,
        "provider_call_id": call_id,
        "tool": "sandbox_exec",
        "kind": "execution",
        "status": "blocked",
        "success": False,
        "error": "dangerous command blocked",
        "error_code": "dangerous_command",
        "policy_code": "dangerous_command",
        "data": {"code": "dangerous_command", "status": "blocked"},
    }
    return {key: value for key, value in observation.items() if value != ""}


def _state(observations: list[dict[str, Any]]) -> TaskState:
    state = TaskState.create(user_goal="读取结果并执行验证", task_type="build", plan=[], task_profile=None)
    state.metadata["completion_observations"] = [dict(item) for item in observations]
    return state


def _outcome(blocked: dict[str, Any]) -> ToolOutcomeResolution:
    return ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="sandbox_exec",
        status="blocked",
        policy_code="dangerous_command",
        metadata={"observation": dict(blocked)},
    )


def _loop(responses: list[Any]) -> tuple[AgentLoop, MockProvider]:
    llm = MockProvider(responses=responses)
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = llm
    loop.memory = Memory()
    loop._runtime_metrics = None
    return loop, llm


def _trace(state: TaskState) -> AgentTrace:
    return AgentTrace(state.task_id, state.user_goal, state.task_type)


def test_success_then_blocked_uses_terminal_responder() -> None:
    success = _success("call_read", "read_file", "core/runtime_metrics.py")
    blocked = _blocked()
    state = _state([success, blocked])
    outcome = _outcome(blocked)
    outlet = resolve_finalization_outlet(state, outcome)
    assert outlet.kind == "terminal_responder"
    assert outlet.reason == "partial_outcome_with_policy_block"
    assert outlet.policy_code == "dangerous_command"

    loop, llm = _loop([assistant_message("已读取 runtime_metrics.py；验证命令因安全限制未执行，任务仅部分完成。")])
    trace = _trace(state)
    answer = loop._build_final_answer_from_terminal_outcome(state, outcome, trace, 1)
    assert "已读取" in answer and "未执行" in answer and "部分完成" in answer
    assert len(llm.calls) == 1 and llm.calls[0]["tools"] == []
    events = [event.to_dict() for event in trace.events]
    assert not any(event["event_type"] == "emergency_finalization_fallback" for event in events)
    context_event = next(event for event in events if event["event_type"] == "final_observation_context")
    assert context_event["data"]["observation_count"] == 2
    assert set(context_event["data"]["call_ids"]) == {"call_read", "call_blocked"}
    path = next(event["data"] for event in events if event["event_type"] == "final_answer_path")
    assert path["path"] == "llm_final"
    assert path["trigger"] == "partial_outcome_policy_blocked"
    assert path["outcome_kind"] == "terminal_policy_blocked"
    assert path["policy_code"] == "dangerous_command"


def test_two_successes_and_blocked_all_reach_context() -> None:
    blocked = _blocked()
    state = _state(
        [
            _success("call_a", "read_file", "a.py"),
            _success("call_b", "read_file", "b.py"),
            blocked,
        ]
    )
    context = build_final_observation_context(state, _outcome(blocked))
    assert len(context) == 3
    assert {item.get("call_id") for item in context} == {"call_a", "call_b", "call_blocked"}
    assert sum(item.get("success") is True for item in context) == 2
    assert sum(item.get("status") == "blocked" for item in context) == 1


def test_pure_blocked_uses_terminal_responder() -> None:
    blocked = _blocked()
    state = _state([blocked])
    outcome = _outcome(blocked)
    outlet = resolve_finalization_outlet(state, outcome)
    assert outlet.kind == "terminal_responder"
    loop, llm = _loop([assistant_message("命令被当前授权边界阻止，因此没有执行。")])
    trace = _trace(state)
    answer = loop._build_final_answer_from_terminal_outcome(state, outcome, trace, 1)
    assert "授权边界阻止" in answer
    assert len(llm.calls) == 1
    assert not any(event.event_type == "emergency_finalization_fallback" for event in trace.events)


def test_duplicate_blocked_and_identity_rules() -> None:
    blocked = _blocked()
    duplicate_state = _state([dict(blocked), dict(blocked)])
    assert resolve_finalization_outlet(duplicate_state, _outcome(blocked)).kind == "terminal_responder"
    duplicate_context = build_final_observation_context(duplicate_state, _outcome(blocked))
    assert len(duplicate_context) == 1

    partial_state = _state([_success("call_other", "read_file", "other.py"), blocked])
    assert resolve_finalization_outlet(partial_state, _outcome(blocked)).kind == "terminal_responder"

    no_id_blocked = _blocked("")
    no_id_state = _state([dict(no_id_blocked)])
    assert resolve_finalization_outlet(no_id_state, _outcome(no_id_blocked)).kind == "terminal_responder"

    ambiguous_success = _success("call_ignored", "sandbox_exec", "same tool")
    ambiguous_success.pop("call_id", None)
    ambiguous_success.pop("provider_call_id", None)
    ambiguous_success.pop("observation_id", None)
    ambiguous_state = _state([ambiguous_success, no_id_blocked])
    assert resolve_finalization_outlet(ambiguous_state, _outcome(no_id_blocked)).kind == "terminal_responder"


def test_final_tool_call_rejected_and_fallback_preserves_all_facts() -> None:
    success = _success("call_read", "read_file", "UNIQUE_READ_RESULT")
    blocked = _blocked()
    state = _state([success, blocked])
    tool_call = SimpleNamespace(id="final_tool", function=SimpleNamespace(name="read_file", arguments="{}"))
    loop, llm = _loop([assistant_message("", tool_calls=[tool_call])])
    trace = _trace(state)
    answer = loop._build_final_answer_from_terminal_outcome(state, _outcome(blocked), trace, 1)
    assert len(llm.calls) == 1 and llm.calls[0]["tools"] == []
    assert "任务部分完成" in answer
    assert "UNIQUE_READ_RESULT" in answer
    assert "dangerous_command" in answer
    for internal_field in ("call_id", "provider_call_id", "observation_id", "task_id"):
        assert internal_field not in answer
    events = [event.to_dict() for event in trace.events]
    assert any(
        event["event_type"] == "terminal_responder_validation"
        and event["data"].get("reject_reason") == "assistant_requested_tool_call"
        for event in events
    )
    assert any(event["event_type"] == "emergency_finalization_fallback" for event in events)


def test_multi_success_fallback_redacts_internal_ids() -> None:
    blocked = _blocked()
    state = _state(
        [
            _success("call_a", "read_file", "FIRST_RESULT"),
            _success("call_b", "read_file", "SECOND_RESULT"),
            blocked,
        ]
    )
    tool_call = SimpleNamespace(id="final_tool", function=SimpleNamespace(name="read_file", arguments="{}"))
    loop, _ = _loop([assistant_message("", tool_calls=[tool_call])])
    trace = _trace(state)
    answer = loop._build_final_answer_from_terminal_outcome(state, _outcome(blocked), trace, 1)
    assert "FIRST_RESULT" in answer
    assert "SECOND_RESULT" in answer
    assert "dangerous_command" in answer
    for internal_field in ("call_id", "provider_call_id", "observation_id", "task_id"):
        assert internal_field not in answer

    context_event = next(event.to_dict() for event in trace.events if event.event_type == "final_observation_context")
    assert set(context_event["data"]["call_ids"]) == {"call_a", "call_b", "call_blocked"}


def test_outlet_has_no_user_text_routing() -> None:
    source = inspect.getsource(resolve_finalization_outlet)
    assert "user_goal" not in source
    assert "user_input" not in source
    assert "re.search" not in source


def main() -> None:
    test_success_then_blocked_uses_terminal_responder()
    test_two_successes_and_blocked_all_reach_context()
    test_pure_blocked_uses_terminal_responder()
    test_duplicate_blocked_and_identity_rules()
    test_final_tool_call_rejected_and_fallback_preserves_all_facts()
    test_multi_success_fallback_redacts_internal_ids()
    test_outlet_has_no_user_text_routing()
    print("smoke_partial_outcome_finalization ok")


if __name__ == "__main__":
    main()
