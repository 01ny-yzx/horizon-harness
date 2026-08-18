"""Offline contract checks for unified initial-agent-turn failures and retry."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_agent_turn import execute_initial_agent_turn
from core.initial_tool_surface import build_initial_tool_surface
from core.loop import _execute_initial_agent_turn_with_retry
from core.loop import AgentLoop
from core.memory import Memory
from core.runtime_metrics import RuntimeMetrics
from providers.openai_compatible import LLMProviderError
from tools.registry import get_local_tool_schemas, get_local_tool_specs


def _call(name: str) -> SimpleNamespace:
    return SimpleNamespace(id="call-1", function=SimpleNamespace(name=name, arguments=json.dumps({"path": "x.txt"})))


class FakeLLM:
    def __init__(self, responses: list[object], supports_tools: bool = True) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.capabilities = SimpleNamespace(
            supports_tools=supports_tools,
            max_context_tokens=8192,
            max_input_tokens=7000,
            max_output_tokens=1024,
        )
        self.config = SimpleNamespace(
            capabilities=SimpleNamespace(
                supports_tools=supports_tools,
                max_context_tokens=8192,
                max_input_tokens=7000,
                max_output_tokens=1024,
            ),
            model="mock",
        )

    def chat(self, *, messages, tools, options):
        self.calls.append({"messages": messages, "tools": tools, "options": options})
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _surface():
    return build_initial_tool_surface(get_local_tool_specs(), get_local_tool_schemas(), access_mode="full_access")


def main() -> None:
    surface = _surface()
    specs = get_local_tool_specs()
    schemas = get_local_tool_schemas()
    messages = [{"role": "user", "content": "read x"}]

    timeout = FakeLLM([LLMProviderError("timeout", code="timeout", retryable=True), SimpleNamespace(content="", tool_calls=[_call("read_file")])])
    metrics = RuntimeMetrics()
    retried = _execute_initial_agent_turn_with_retry(
        llm=timeout, messages=messages, surface=surface, options=None, metrics=metrics, model="mock",
        sleep_fn=lambda _seconds: None,
    )
    assert retried.result.mode == "tool_calls" and retried.attempt_count == 2
    assert len(timeout.calls) == 2 and timeout.calls[0]["messages"] == timeout.calls[1]["messages"]

    invalid = FakeLLM([SimpleNamespace(content="<tool_call>read_file</tool_call>", tool_calls=[]), SimpleNamespace(content="", tool_calls=[_call("read_file")])])
    repaired = _execute_initial_agent_turn_with_retry(
        llm=invalid, messages=messages, surface=surface, options=None, metrics=RuntimeMetrics(), model="mock",
        sleep_fn=lambda _seconds: None,
    )
    assert repaired.result.mode == "tool_calls" and "previous initial agent turn was rejected" in invalid.calls[1]["messages"][-1]["content"]
    assert invalid.calls[0]["tools"] == invalid.calls[1]["tools"]

    exhausted = FakeLLM([SimpleNamespace(content="<tool_call>read_file</tool_call>", tool_calls=[])] * 2)
    terminal = _execute_initial_agent_turn_with_retry(
        llm=exhausted, messages=messages, surface=surface, options=None, metrics=RuntimeMetrics(), model="mock",
        sleep_fn=lambda _seconds: None,
    ).result
    assert terminal.mode == "terminal_failure" and terminal.failure_reason == "initial_agent_turn_retry_exhausted"
    assert "结构化结果" in terminal.user_message

    timeout_exhausted = _execute_initial_agent_turn_with_retry(
        llm=FakeLLM([LLMProviderError("timeout", code="timeout", retryable=True)] * 2),
        messages=messages, surface=surface, options=None, metrics=RuntimeMetrics(), model="mock",
        sleep_fn=lambda _seconds: None,
    ).result
    assert timeout_exhausted.failure_category == "provider"
    assert timeout_exhausted.error_code == "timeout"
    assert "模型服务连续调用超时" in timeout_exhausted.user_message
    assert "结构化结果" not in timeout_exhausted.user_message

    auth = execute_initial_agent_turn(
        llm=FakeLLM([LLMProviderError("auth", code="authentication_error")]), messages=messages, surface=surface,
    )
    assert auth.mode == "terminal_failure" and auth.retryable is False and auth.failure_category == "provider"
    unsupported = execute_initial_agent_turn(
        llm=FakeLLM([], supports_tools=False), messages=messages, surface=surface,
    )
    assert unsupported.mode == "terminal_failure" and unsupported.attempt_count == 0

    loop_llm = FakeLLM([LLMProviderError("auth", code="authentication_error")])
    loop = AgentLoop(loop_llm, Memory())
    captured = {}

    def _capture(trace, state, answer):
        captured["trace"] = trace
        captured["state"] = state
        return answer

    loop._finish_with_trace = _capture
    answer = loop.run("read x")
    assert answer == "模型服务认证失败，请检查 Provider 配置。"
    assert len(loop_llm.calls) == 1
    state = captured["state"]
    assert state.current_phase == "initial_agent_turn_failure"
    assert state.failed_steps == ["initial_agent_turn_failure"]
    assert state.plan[0].status == "failed"
    assert state.metadata["initial_agent_turn_error_code"] == "authentication_error"
    assert not {"planner_skipped", "planner_would_skip", "tool_plan", "build_step_contract"} & set(state.metadata)
    assert not any(event.event_type == "unified_intent_capability" for event in captured["trace"].events)

    unavailable_llm = FakeLLM([
        LLMProviderError(
            "unavailable",
            code="provider_status_error",
            retryable=True,
            status_code=503,
            response_headers={"retry-after": "60"},
        )
    ])
    unavailable_loop = AgentLoop(unavailable_llm, Memory())
    unavailable_captured = {}
    unavailable_loop._finish_with_trace = lambda trace, state, answer: (
        unavailable_captured.update(trace=trace, state=state) or answer
    )
    unavailable_answer = unavailable_loop.run("read x")
    assert unavailable_answer == "模型服务暂时不可用，请稍后重新执行任务。"
    assert len(unavailable_llm.calls) == 1
    unavailable_state = unavailable_captured["state"]
    assert unavailable_state.metadata["initial_agent_turn_failure_status_code"] == 503
    assert unavailable_state.metadata["initial_agent_turn_retry_count"] == 0
    assert unavailable_state.metadata["initial_agent_turn_retry_skipped_reason"] == (
        "provider_retry_delay_exceeds_inline_limit"
    )
    failure_event = next(
        event
        for event in unavailable_captured["trace"].events
        if event.event_type == "initial_agent_turn_failure"
    )
    failure_payload = json.loads(failure_event.summary)
    assert failure_payload["status_code"] == 503
    assert failure_payload["retry_skipped_reason"] == (
        "provider_retry_delay_exceeds_inline_limit"
    )
    print("smoke_initial_agent_turn_unified_failure ok")


if __name__ == "__main__":
    main()
