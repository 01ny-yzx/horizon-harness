"""Offline checks that initial-turn metrics follow real provider attempts."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_agent_turn import execute_initial_agent_turn
from core.initial_tool_surface import build_initial_tool_surface
from core.loop import _execute_initial_agent_turn_with_retry
from core.runtime_metrics import RuntimeMetrics
from providers.base import LLMChatResult, LLMUsage
from providers.openai_compatible import LLMProviderError
from tools.registry import get_local_tool_schemas, get_local_tool_specs


class FakeLLM:
    def __init__(self, responses, *, supports_tools: bool = True) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.capabilities = SimpleNamespace(supports_tools=supports_tools)

    def chat(self, **_kwargs):
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _call(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name=name, arguments=json.dumps({"path": "x.txt"})),
    )


def _run(responses, *, supports_tools: bool = True):
    specs = get_local_tool_specs()
    schemas = get_local_tool_schemas()
    surface = build_initial_tool_surface(specs, schemas, access_mode="full_access")
    metrics = RuntimeMetrics()
    llm = FakeLLM(responses, supports_tools=supports_tools)
    execution = _execute_initial_agent_turn_with_retry(
        llm=llm,
        messages=[{"role": "user", "content": "read x"}],
        surface=surface,
        options=None,
        metrics=metrics,
        model="mock",
    )
    return execution, metrics, llm, surface, specs, schemas


def _assert_attempts(metrics: RuntimeMetrics, count: int) -> list[dict]:
    records = metrics.summary()["llm_calls_by_stage"].get("initial_agent_turn", [])
    assert len(records) == count
    assert metrics.summary()["llm_call_count_by_stage"].get("initial_agent_turn", 0) == count
    assert all(record["status"] != "running" for record in records)
    return records


def main() -> None:
    direct, metrics, llm, *_ = _run([
        LLMChatResult(
            message=SimpleNamespace(content="已完成。", tool_calls=[]),
            usage=LLMUsage(input_tokens=11, output_tokens=3, total_tokens=14, available=True),
            finish_reason="stop",
        )
    ])
    assert direct.result.mode == "direct_answer" and llm.calls == 1
    record = _assert_attempts(metrics, 1)[0]
    assert record["runtime_accepted"] is True
    assert record["usage_available"] is True and record["total_tokens"] == 14
    assert record["finish_reason"] == "stop"

    tools, metrics, llm, *_ = _run([SimpleNamespace(content="", tool_calls=[_call("read_file")])])
    assert tools.result.mode == "tool_calls" and llm.calls == 1
    assert _assert_attempts(metrics, 1)[0]["runtime_accepted"] is True

    hallucination, metrics, llm, *_ = _run([
        SimpleNamespace(
            content="",
            tool_calls=[SimpleNamespace(
                id="plan-1",
                function=SimpleNamespace(
                    name="unavailable_tool",
                    arguments="{}",
                ),
            )],
        ),
        SimpleNamespace(content="已改用正常回答。", tool_calls=[]),
    ])
    records = _assert_attempts(metrics, 2)
    assert hallucination.result.mode == "direct_answer" and llm.calls == 2
    assert records[0]["runtime_accepted"] is False
    assert records[1]["runtime_accepted"] is True

    repaired, metrics, llm, *_ = _run([
        LLMChatResult(
            message=SimpleNamespace(content="<tool_call>read_file</tool_call>", tool_calls=[]),
            usage=LLMUsage(input_tokens=5, output_tokens=2, total_tokens=7, available=True),
            finish_reason="stop",
        ),
        LLMChatResult(
            message=SimpleNamespace(content="已修复。", tool_calls=[]),
            usage=LLMUsage(input_tokens=7, output_tokens=3, total_tokens=10, available=True),
            finish_reason="stop",
        ),
    ])
    records = _assert_attempts(metrics, 2)
    assert repaired.result.mode == "direct_answer" and llm.calls == 2
    assert records[0]["provider_success"] is True and records[0]["runtime_accepted"] is False
    assert records[1]["provider_success"] is True and records[1]["runtime_accepted"] is True
    assert metrics.summary()["llm_total_tokens"] == 17

    timeout, metrics, llm, *_ = _run([
        LLMProviderError("timeout", code="timeout", retryable=True),
        SimpleNamespace(content="已恢复。", tool_calls=[]),
    ])
    records = _assert_attempts(metrics, 2)
    assert timeout.result.mode == "direct_answer" and llm.calls == 2
    assert records[0]["provider_success"] is False and records[0]["error_code"] == "timeout"

    _, metrics, llm, surface, specs, schemas = _run([], supports_tools=False)
    assert llm.calls == 0 and _assert_attempts(metrics, 0) == []
    disabled = execute_initial_agent_turn(
        llm=FakeLLM([]), messages=[{"role": "user", "content": "x"}],
        surface=replace(surface, enabled=False),
        metrics=RuntimeMetrics(), model="mock",
    )
    assert disabled.attempt_count == 0
    assert not hasattr(disabled, "provider_" + "call_attempted")
    print("smoke_initial_agent_turn_attempt_metrics ok")


if __name__ == "__main__":
    main()
