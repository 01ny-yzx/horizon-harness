"""Offline checks for authoritative initial-turn runtime metrics."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_agent_turn import InitialAgentTurnResult
from core.runtime_metrics import RuntimeMetrics
from providers.base import LLMUsage


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "读取文件"}]


def main() -> None:
    metrics = RuntimeMetrics()
    first = metrics.start_llm_attempt(
        messages=_messages(), tools=[], stage="initial_agent_turn",
        model="mock", options=None, attempt_index=1,
    )
    assert metrics.llm_call_count_by_stage["initial_agent_turn"] == 1
    assert metrics.llm_calls_by_stage["initial_agent_turn"][0]["status"] == "running"
    metrics.finish_llm_attempt(
        first, provider_success=True, runtime_accepted=False,
        error_code="invalid_direct_answer", retryable=True,
        usage=LLMUsage(input_tokens=4, output_tokens=2, total_tokens=6, available=True),
        finish_reason="stop",
    )
    second = metrics.start_llm_attempt(
        messages=_messages(), tools=[], stage="initial_agent_turn",
        model="mock", options=None, attempt_index=2,
    )
    metrics.finish_llm_attempt(
        second, provider_success=True, runtime_accepted=True,
        usage=LLMUsage(input_tokens=5, output_tokens=3, total_tokens=8, available=True),
        finish_reason="length",
    )
    metrics.record_initial_agent_turn(
        SimpleNamespace(
            result=InitialAgentTurnResult(
                mode="tool_calls", attempt_count=2, retry_count=1,
            ),
            elapsed_ms=3,
            attempt_count=2,
            retry_count=1,
            retry_reason="invalid_structured_tool_call",
            attempt_records=tuple(metrics.llm_calls_by_stage["initial_agent_turn"]),
        ),
        schema_chars=12, direct_tool_execution=True,
    )
    summary = metrics.summary()
    assert summary["initial_agent_turn_attempt_count"] == 2
    assert summary["initial_agent_turn_retry_count"] == 1
    assert summary["initial_agent_turn_retry_reason"] == "invalid_structured_tool_call"
    assert summary["initial_agent_turn_terminal_failure"] is False
    assert summary["llm_call_count_by_stage"]["initial_agent_turn"] == 2
    attempts = summary["llm_calls_by_stage"]["initial_agent_turn"]
    assert [record["attempt_index"] for record in attempts] == [1, 2]
    assert all(record["status"] != "running" for record in attempts)
    assert all(isinstance(record["duration_ms"], int) for record in attempts)
    assert attempts[0]["provider_success"] is True
    assert attempts[0]["runtime_accepted"] is False
    assert attempts[1]["runtime_accepted"] is True
    assert summary["llm_total_tokens"] == 14
    assert summary["llm_usage_available_count"] == 2
    assert summary["llm_usage_unavailable_count"] == 0
    assert summary["finish_reason_counts_by_stage"]["initial_agent_turn"] == {
        "stop": 1,
        "length": 1,
    }
    assert all("legacy" not in key for key in summary)

    metrics.record_initial_agent_turn(
        SimpleNamespace(
            result=InitialAgentTurnResult(
                mode="terminal_failure", failure_category="provider",
                failure_reason="provider_exception", error_code="authentication_error",
                status_code=503,
                attempt_count=2,
            ),
            elapsed_ms=1,
            attempt_count=2,
            retry_count=1,
            retry_reason="",
            attempt_records=tuple(metrics.llm_calls_by_stage["initial_agent_turn"]),
        ),
        schema_chars=0,
    )
    failed = metrics.summary()
    assert failed["initial_agent_turn_terminal_failure"] is True
    assert failed["initial_agent_turn_failure_category"] == "provider"
    assert failed["initial_agent_turn_failure_reason"] == "provider_exception"
    assert failed["initial_agent_turn_failure_status_code"] == 503
    print("smoke_runtime_metrics_split ok")


if __name__ == "__main__":
    main()
