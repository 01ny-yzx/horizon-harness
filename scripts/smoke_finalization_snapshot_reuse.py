"""Offline smoke checks for one-build finalization snapshot reuse."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.loop as loop_module
from core.loop import AgentLoop
from core.memory import Memory
from core.state import TaskState
from core.tool_outcome_resolution import ToolOutcomeResolution
from core.trace import AgentTrace
from providers.base import LLMCapabilities, LLMConfig
from providers.mock import MockProvider, assistant_message


def _state(*, incomplete: bool = False, large: bool = False) -> TaskState:
    state = TaskState.create(
        user_goal="总结终局结果",
        task_type="tool_use",
        plan=[],
        task_profile=None,
    )
    observation = {
        "call_id": "call-present",
        "provider_call_id": "provider-present",
        "observation_id": "observation-present",
        "tool": "sandbox_exec",
        "success": False,
        "status": "failed",
        "error": "command failed",
        "error_code": "command_failed",
        "data": {
            "exit_code": 2,
            "stderr_preview": "x" * (12000 if large else 20),
        },
    }
    state.metadata["completion_observations"] = [observation]
    state.metadata["structured_tool_call_ids"] = [
        "call-present",
        *(["call-missing"] if incomplete else []),
    ]
    return state


def _outcome(kind: str = "terminal_failure") -> ToolOutcomeResolution:
    observation = {
        "call_id": "call-present",
        "tool": "sandbox_exec",
        "success": False,
        "status": "failed",
        "error": "command failed",
        "error_code": "command_failed",
        "data": {"exit_code": 2, "stderr_preview": "failed"},
    }
    return ToolOutcomeResolution(
        kind,
        "command_failed",
        tool="sandbox_exec",
        status="failed",
        failure_disposition="ordinary_failure",
        metadata={"observation": observation},
    )


class RaisingProvider(MockProvider):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], options: Any = None) -> Any:
        self.calls.append({"messages": messages, "tools": tools, "options": options})
        raise RuntimeError("provider unavailable")


def _loop(responses: list[Any], *, llm: MockProvider | None = None) -> tuple[AgentLoop, MockProvider]:
    provider = llm or MockProvider(responses=responses)
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = provider
    loop.memory = Memory()
    loop._runtime_metrics = None
    return loop, provider


def _finalize(
    responses: list[Any],
    *,
    state: TaskState | None = None,
    outcome: ToolOutcomeResolution | None = None,
    llm: MockProvider | None = None,
) -> tuple[str, MockProvider, AgentTrace, int]:
    active_state = state or _state()
    active_outcome = outcome or _outcome()
    loop, provider = _loop(responses, llm=llm)
    trace = AgentTrace(
        active_state.task_id,
        active_state.user_goal,
        active_state.task_type,
    )
    original = loop_module.build_finalization_context_snapshot
    calls = 0

    def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    loop_module.build_finalization_context_snapshot = counted
    try:
        answer = loop._build_final_answer_from_terminal_outcome(
            active_state,
            active_outcome,
            trace,
            1,
        )
    finally:
        loop_module.build_finalization_context_snapshot = original
    return answer, provider, trace, calls


def _events(trace: AgentTrace, event_type: str) -> list[Any]:
    return [event for event in trace.events if event.event_type == event_type]


def test_responder_adoption_builds_once() -> None:
    answer, llm, trace, count = _finalize(
        [assistant_message("命令执行失败，退出码为 2。")]
    )
    assert answer == "命令执行失败，退出码为 2。"
    assert count == 1 and len(llm.calls) == 1
    assert not _events(trace, "emergency_finalization_fallback")
    snapshot_hash = _events(trace, "finalization_context_snapshot")[0].data[
        "snapshot_hash"
    ]
    responder = _events(trace, "terminal_responder_llm")[0].data
    assert responder["snapshot_hash"] == snapshot_hash


def test_rejected_responses_reuse_snapshot() -> None:
    tool_call = SimpleNamespace(
        id="terminal-tool",
        function=SimpleNamespace(name="read_file", arguments="{}"),
    )
    for response, expected in (
        (assistant_message(""), "empty_content"),
        (
            assistant_message("", tool_calls=[tool_call]),
            "assistant_requested_tool_call",
        ),
    ):
        answer, _, trace, count = _finalize([response])
        assert answer and count == 1
        reused = _events(trace, "finalization_snapshot_reused")
        assert reused and reused[-1].data["rebuilt"] is False
        assert reused[-1].data["original_consumer"] == "terminal_responder"
        assert reused[-1].data["next_consumer"] == "emergency_finalization"
        emergency = _events(trace, "emergency_finalization_fallback")[-1]
        assert emergency.data["fallback_reason"] == expected
        assert emergency.data["snapshot_hash"] == reused[-1].data["snapshot_hash"]


def test_provider_exception_reuses_without_memory() -> None:
    provider = RaisingProvider(responses=[])
    answer, llm, trace, count = _finalize([], llm=provider)
    assert answer and count == 1 and len(llm.calls) == 1
    assert _events(trace, "finalization_snapshot_reused")
    assert _events(trace, "emergency_finalization_fallback")


def test_direct_emergency_outlet_builds_once() -> None:
    nonterminal = ToolOutcomeResolution(
        "allow_continue",
        "non_terminal_outcome",
        status="failed",
    )
    answer, llm, trace, count = _finalize([], outcome=nonterminal)
    assert answer and count == 1 and len(llm.calls) == 0
    assert _events(trace, "emergency_finalization_fallback")
    assert not _events(trace, "terminal_responder_llm")


def test_incomplete_coverage_skips_llm() -> None:
    answer, llm, trace, count = _finalize([], state=_state(incomplete=True))
    assert count == 1 and len(llm.calls) == 0
    assert "结果不完整" in answer
    assert "call-missing" not in answer
    started = _events(trace, "emergency_finalization_started")[-1].data
    assert started["llm_skipped"] is True
    assert started["skip_reason"] == "finalization_context_incomplete"


def test_unsatisfied_budget_skips_llm_and_keeps_results() -> None:
    capabilities = LLMCapabilities(
        supports_tools=True,
        max_context_tokens=160,
        max_input_tokens=160,
        max_output_tokens=32,
    )
    provider = MockProvider(
        config=LLMConfig(
            provider="mock",
            model="tiny",
            capabilities=capabilities,
        ),
        responses=[assistant_message("must not run")],
    )
    state = _state(large=True)
    observations = list(state.metadata["completion_observations"])
    for index in range(1, 8):
        observation = dict(observations[0])
        observation["call_id"] = f"call-present-{index}"
        observation["provider_call_id"] = f"provider-present-{index}"
        observation["observation_id"] = f"observation-present-{index}"
        observations.append(observation)
        state.metadata["structured_tool_call_ids"].append(
            observation["call_id"]
        )
    state.metadata["completion_observations"] = observations
    answer, llm, trace, count = _finalize(
        [],
        state=state,
        llm=provider,
    )
    assert answer and count == 1 and len(llm.calls) == 0
    budget = _events(trace, "finalization_context_budget")[-1].data
    assert budget["budget_satisfied"] is False
    assert budget["dropped_result_count"] == 0
    assert budget["result_count"] == 8
    started = _events(trace, "emergency_finalization_started")[-1].data
    assert started["skip_reason"] == "finalization_context_budget_unsatisfied"


def main() -> None:
    test_responder_adoption_builds_once()
    test_rejected_responses_reuse_snapshot()
    test_provider_exception_reuses_without_memory()
    test_direct_emergency_outlet_builds_once()
    test_incomplete_coverage_skips_llm()
    test_unsatisfied_budget_skips_llm_and_keeps_results()
    print("smoke_finalization_snapshot_reuse ok")


if __name__ == "__main__":
    main()
