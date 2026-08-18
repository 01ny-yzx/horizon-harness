"""Offline consistency checks for initial-turn attempt accounting."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_agent_turn import task_state_from_initial_failure
from core.initial_tool_surface import build_initial_tool_surface
from core.loop import _execute_initial_agent_turn_with_retry
from core.runtime_metrics import RuntimeMetrics
from providers.openai_compatible import LLMProviderError
from tools.registry import get_local_tool_schemas, get_local_tool_specs


class FakeLLM:
    def __init__(self, responses, *, supports_tools=True):
        self.responses = list(responses)
        self.capabilities = SimpleNamespace(supports_tools=supports_tools)

    def chat(self, **_kwargs):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _run(llm, surface):
    metrics = RuntimeMetrics()
    execution = _execute_initial_agent_turn_with_retry(
        llm=llm,
        messages=[{"role": "user", "content": "x"}],
        surface=surface,
        options=None,
        metrics=metrics,
        model="mock",
        sleep_fn=lambda _seconds: None,
    )
    metrics.record_initial_agent_turn(
        execution,
        schema_chars=surface.schema_chars,
        direct_tool_execution=False,
    )
    return execution, metrics


def _assert_consistent(execution, metrics, expected):
    summary = metrics.summary()
    assert len(execution.attempt_records) == expected
    assert execution.attempt_count == expected
    assert execution.result.attempt_count == expected
    assert summary["initial_agent_turn_attempt_count"] == expected
    assert summary["llm_call_count_by_stage"].get("initial_agent_turn", 0) == expected
    assert summary["initial_agent_turn_attempt_consistent"] is True


def main() -> None:
    specs = get_local_tool_specs()
    schemas = get_local_tool_schemas()
    surface = build_initial_tool_surface(specs, schemas, access_mode="full_access")

    unavailable, metrics = _run(FakeLLM([]), replace(surface, enabled=False))
    _assert_consistent(unavailable, metrics, 0)
    state = task_state_from_initial_failure(unavailable.result, "x")
    assert state.metadata["initial_agent_turn_attempt_count"] == 0

    unsupported, metrics = _run(FakeLLM([], supports_tools=False), surface)
    _assert_consistent(unsupported, metrics, 0)
    assert task_state_from_initial_failure(unsupported.result, "x").metadata[
        "initial_agent_turn_attempt_count"
    ] == 0

    success, metrics = _run(
        FakeLLM([SimpleNamespace(content="完成。", tool_calls=[])]), surface
    )
    _assert_consistent(success, metrics, 1)

    repaired, metrics = _run(
        FakeLLM([
            SimpleNamespace(content="<tool_call>read_file</tool_call>", tool_calls=[]),
            SimpleNamespace(content="完成。", tool_calls=[]),
        ]),
        surface,
    )
    _assert_consistent(repaired, metrics, 2)

    retried, metrics = _run(
        FakeLLM([
            LLMProviderError("timeout", code="timeout", retryable=True),
            SimpleNamespace(content="完成。", tool_calls=[]),
        ]),
        surface,
    )
    _assert_consistent(retried, metrics, 2)
    retried_summary = metrics.summary()
    assert retried.retry_count == 1
    assert retried.retry_reason == "provider_exception"
    assert retried_summary["initial_agent_turn_retry_count"] == 1
    assert retried_summary["initial_agent_turn_retry_reason"] == "provider_exception"

    skipped, metrics = _run(
        FakeLLM([
            LLMProviderError(
                "unavailable",
                code="provider_status_error",
                retryable=True,
                status_code=503,
                response_headers={"retry-after": "60"},
            ),
        ]),
        surface,
    )
    _assert_consistent(skipped, metrics, 1)
    skipped_summary = metrics.summary()
    assert skipped.retry_count == 0
    assert skipped.retry_reason == ""
    assert skipped.retry_skipped_reason == "provider_retry_delay_exceeds_inline_limit"
    assert skipped_summary["initial_agent_turn_retry_count"] == 0
    assert skipped_summary["initial_agent_turn_retry_reason"] == ""
    assert skipped_summary["initial_agent_turn_retry_skipped_reason"] == (
        "provider_retry_delay_exceeds_inline_limit"
    )
    assert skipped_summary["initial_agent_turn_failure_status_code"] == 503
    print("smoke_initial_attempt_summary_consistency ok")


if __name__ == "__main__":
    main()
