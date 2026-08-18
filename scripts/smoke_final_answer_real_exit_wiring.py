from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop
from core.memory import Memory
from core.state import TaskState
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.trace import AgentTrace
from providers.mock import MockProvider, assistant_message


def _loop(llm: MockProvider) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = llm
    loop.memory = Memory()
    loop._runtime_metrics = None
    return loop


def _task_state() -> TaskState:
    return TaskState.create(
        user_goal="调用 get_usage_status",
        task_type="research",
        plan=[],
        task_profile=None,
    )


def _trace(task_state: TaskState) -> AgentTrace:
    return AgentTrace(task_state.task_id, task_state.user_goal, task_state.task_type)


def test_policy_blocked_uses_tools_disabled_final_llm() -> None:
    llm = MockProvider(responses=[assistant_message("该地址未通过当前访问策略，因此没有执行网络读取。")])
    loop = _loop(llm)
    task_state = _task_state()
    trace = _trace(task_state)
    outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="fetch_url",
        status="blocked",
        policy_code="unsupported_scheme",
        metadata={
            "observation": {
                "success": False,
                "status": "blocked",
                "kind": "internal",
                "tool": "fetch_url",
                "error": "only http and https URLs are allowed.",
                "error_code": "unsupported_scheme",
            }
        },
    )

    answer = loop._build_final_answer_from_terminal_outcome(task_state, outcome, trace, 1)

    assert "未通过当前访问策略" in answer
    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"] == []
    events = [event.to_dict() for event in trace.events]
    assert any(event["event_type"] == "finalization_outlet" and '"kind": "terminal_responder"' in event["summary"] for event in events)
    assert any(event["event_type"] == "terminal_responder_llm" and event["success"] is True for event in events)


def test_policy_blocked_complete_gate_uses_llm_final_outlet() -> None:
    llm = MockProvider(responses=[assistant_message("该 URL 使用了不受支持的协议，因此读取被阻止。")])
    loop = _loop(llm)
    task_state = _task_state()
    trace = _trace(task_state)
    outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="fetch_url",
        status="blocked",
        policy_code="unsupported_scheme",
        metadata={
            "observation": {
                "success": False,
                "status": "blocked",
                "kind": "internal",
                "tool": "fetch_url",
                "error": "only http and https URLs are allowed.",
                "error_code": "unsupported_scheme",
            }
        },
    )
    answer = loop._build_final_answer_from_terminal_outcome(task_state, outcome, trace, 1)

    assert "不受支持的协议" in answer
    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"] == []
    events = [event.to_dict() for event in trace.events]
    assert any(event["event_type"] == "finalization_outlet" and '"kind": "terminal_responder"' in event["summary"] for event in events)
    assert any(event["event_type"] == "terminal_responder_llm" and event["success"] is True for event in events)


def test_partial_policy_block_uses_tools_disabled_final_llm() -> None:
    llm = MockProvider(responses=[assistant_message("文件读取结果为 READ_OK；后续命令被安全边界阻断，因此任务仅部分完成。")])
    loop = _loop(llm)
    task_state = _task_state()
    trace = _trace(task_state)
    success = {
        "call_id": "call-read",
        "tool": "read_file",
        "success": True,
        "status": "success",
        "data": {"path": "a.txt", "content": "READ_OK"},
    }
    blocked = {
        "call_id": "call-command",
        "tool": "sandbox_exec",
        "success": False,
        "status": "blocked",
        "error_code": "dangerous_command",
    }
    task_state.metadata["completion_observations"] = [success, blocked]
    outcome = ToolOutcomeResolution(
        "terminal_policy_blocked",
        "policy_blocked",
        tool="sandbox_exec",
        status="blocked",
        policy_code="dangerous_command",
        metadata={"observation": blocked},
    )

    answer = loop._build_final_answer_from_terminal_outcome(task_state, outcome, trace, 1)

    assert "READ_OK" in answer
    assert "部分完成" in answer
    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"] == []
    events = [event.to_dict() for event in trace.events]
    assert any(event["event_type"] == "finalization_outlet" and "partial_outcome_with_policy_block" in event["summary"] for event in events)
    assert any(event["event_type"] == "final_answer_path" and '"trigger": "partial_outcome_policy_blocked"' in event["summary"] for event in events)
    assert not any(event["event_type"] == "emergency_finalization_fallback" for event in events)


def main() -> None:
    test_policy_blocked_uses_tools_disabled_final_llm()
    test_policy_blocked_complete_gate_uses_llm_final_outlet()
    test_partial_policy_block_uses_tools_disabled_final_llm()
    print("smoke_final_answer_real_exit_wiring ok")


if __name__ == "__main__":
    main()
