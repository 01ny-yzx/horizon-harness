"""Offline checks for bounded Provider retry timing."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_tool_surface import build_initial_tool_surface
from core.initial_agent_turn import task_state_from_initial_failure
from core.loop import _execute_initial_agent_turn_with_retry
from core.provider_retry_delay import compute_provider_retry_delay
from core.runtime_metrics import RuntimeMetrics
from providers.openai_compatible import LLMProviderError
from tools.registry import get_local_tool_schemas, get_local_tool_specs


class FakeLLM:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.capabilities = SimpleNamespace(supports_tools=True)

    def chat(self, **_kwargs):
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _run(responses, sleep_calls):
    specs = get_local_tool_specs()
    schemas = get_local_tool_schemas()
    llm = FakeLLM(responses)
    execution = _execute_initial_agent_turn_with_retry(
        llm=llm,
        messages=[{"role": "user", "content": "read x"}],
        surface=build_initial_tool_surface(specs, schemas, access_mode="full_access"),
        options=None,
        metrics=RuntimeMetrics(),
        model="mock",
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
        now_fn=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    return execution, llm


def main() -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    assert compute_provider_retry_delay({"retry-after-ms": "1500"}).applied_delay_ms == 1500
    assert compute_provider_retry_delay({"retry-after": "3"}).applied_delay_ms == 3000
    assert compute_provider_retry_delay({"retry-after-ms": "0.9"}).requested_delay_ms == 1
    assert compute_provider_retry_delay({"retry-after": "0.0009"}).requested_delay_ms == 1
    assert compute_provider_retry_delay({"retry-after-ms": "1500.1"}).requested_delay_ms == 1501
    future = (now + timedelta(seconds=4)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert compute_provider_retry_delay({"retry-after": future}, now=now).applied_delay_ms == 4000
    assert compute_provider_retry_delay({}).applied_delay_ms == 2000
    assert compute_provider_retry_delay({"retry-after": "NaN"}).applied_delay_ms == 2000
    assert compute_provider_retry_delay({"retry-after-ms": "-1"}).applied_delay_ms == 2000
    long = compute_provider_retry_delay({"retry-after": "60"})
    assert long.should_retry_inline is False and long.applied_delay_ms == 0

    sleep_calls: list[float] = []
    short, short_llm = _run([
        LLMProviderError("rate", code="rate_limit", retryable=True, response_headers={"retry-after-ms": "1500"}),
        SimpleNamespace(content="完成。", tool_calls=[]),
    ], sleep_calls)
    assert short.attempt_count == 2 and sleep_calls == [1.5]
    assert short.retry_count == 1 and short.retry_reason == "provider_exception"
    assert short_llm.calls == 2
    assert short.retry_wait_applied is True and short.retry_delay_ms == 1500
    assert short.attempt_records[0]["retry_delay_ms"] == 1500
    assert short.attempt_records[0]["retry_wait_applied"] is True

    sleep_calls.clear()
    unavailable, unavailable_llm = _run([
        LLMProviderError(
            "unavailable",
            code="provider_status_error",
            retryable=True,
            status_code=503,
            response_headers={"retry-after": "60"},
        ),
    ], sleep_calls)
    assert unavailable.attempt_count == 1 and sleep_calls == []
    assert unavailable.retry_count == 0 and unavailable.retry_reason == ""
    assert unavailable_llm.calls == 1
    assert unavailable.result.failure_reason == "provider_exception"
    assert unavailable.result.error_code == "provider_status_error"
    assert unavailable.result.status_code == 503
    assert unavailable.retry_skipped_reason == "provider_retry_delay_exceeds_inline_limit"
    assert unavailable.attempt_records[0]["retry_skipped_reason"] == "provider_retry_delay_exceeds_inline_limit"
    assert unavailable.result.user_message == "模型服务暂时不可用，请稍后重新执行任务。"
    terminal_state = task_state_from_initial_failure(unavailable.result, "x")
    assert terminal_state.metadata["initial_agent_turn_retry_delay_ms"] == 60000
    assert terminal_state.metadata["initial_agent_turn_retry_wait_applied"] is False
    assert terminal_state.metadata["initial_agent_turn_retry_skipped_reason"] == "provider_retry_delay_exceeds_inline_limit"
    assert terminal_state.metadata["initial_agent_turn_failure_status_code"] == 503

    limited, limited_llm = _run([
        LLMProviderError(
            "limited",
            code="rate_limit",
            retryable=True,
            status_code=429,
            response_headers={"retry-after": "60"},
        ),
    ], sleep_calls)
    assert limited_llm.calls == 1
    assert limited.retry_count == 0 and limited.retry_reason == ""
    assert limited.result.status_code == 429
    assert limited.result.failure_reason == "provider_exception"
    assert limited.result.user_message == "模型服务当前请求受限，请稍后重新执行任务。"

    sleep_calls.clear()
    repaired, repaired_llm = _run([
        SimpleNamespace(content="<tool_call>read_file</tool_call>", tool_calls=[]),
        SimpleNamespace(content="完成。", tool_calls=[]),
    ], sleep_calls)
    assert repaired.attempt_count == 2 and sleep_calls == []
    assert repaired.retry_reason == "raw_tool_markup_without_structured_calls"
    assert repaired_llm.calls == 2
    print("smoke_provider_retry_delay ok")


if __name__ == "__main__":
    main()
