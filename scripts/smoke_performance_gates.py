"""Offline performance-gate checks for the unified initial agent turn."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.performance_gates import evaluate_performance_gate


def main() -> None:
    report = evaluate_performance_gate(
        "initial_retry_success",
        events=[
            {"event_type": "initial_agent_turn", "data": {"attempt_count": 2, "retry_count": 1}},
            {"event_type": "performance_stage", "data": {"stage": "initial_agent_turn", "attempt_index": 1, "provider_success": True, "runtime_accepted": False}},
            {"event_type": "performance_stage", "data": {"stage": "initial_agent_turn", "attempt_index": 2, "provider_success": True, "runtime_accepted": True}},
        ],
        metrics={"llm_call_count_by_stage": {"initial_agent_turn": 2}},
    )
    assert report.passed, report
    mismatch = evaluate_performance_gate(
        "initial_retry_success",
        events=[
            {"event_type": "initial_agent_turn", "data": {"attempt_count": 2, "retry_count": 1}},
            {"event_type": "performance_stage", "data": {"stage": "initial_agent_turn", "attempt_index": 1, "provider_success": True, "runtime_accepted": False}},
        ],
        metrics={
            "llm_call_count_by_stage": {"initial_agent_turn": 2},
            "initial_agent_turn_attempt_count": 2,
            "initial_agent_turn_attempt_consistent": False,
        },
    )
    assert mismatch.passed is False
    long_retry = evaluate_performance_gate(
        "initial_terminal_failure",
        events=[
            {"event_type": "initial_agent_turn_failure", "data": {"error_code": "rate_limit"}},
            {"event_type": "final_answer_path", "data": {"path": "terminal_failure"}},
            {"event_type": "performance_stage", "data": {"stage": "initial_agent_turn", "attempt_index": 1, "provider_success": False, "runtime_accepted": False}},
            {"event_type": "performance_stage", "data": {"stage": "initial_agent_turn", "attempt_index": 2, "provider_success": False, "runtime_accepted": False}},
        ],
        metrics={
            "llm_call_count_by_stage": {"initial_agent_turn": 2},
            "initial_agent_turn_attempt_count": 2,
            "initial_agent_turn_retry_skipped_reason": "provider_retry_delay_exceeds_inline_limit",
        },
    )
    assert long_retry.passed is False
    retry_reason_without_turn = evaluate_performance_gate(
        "initial_terminal_failure",
        events=[
            {"event_type": "initial_agent_turn", "data": {"retry_count": 0}},
            {"event_type": "initial_agent_turn_failure", "data": {"error_code": "timeout"}},
            {"event_type": "final_answer_path", "data": {"path": "terminal_failure"}},
            {"event_type": "performance_stage", "data": {"stage": "initial_agent_turn", "attempt_index": 1, "provider_success": False, "runtime_accepted": False}},
        ],
        metrics={
            "llm_call_count_by_stage": {"initial_agent_turn": 1},
            "initial_agent_turn_attempt_count": 1,
            "initial_agent_turn_retry_reason": "provider_exception",
        },
    )
    assert retry_reason_without_turn.passed is False
    status_mismatch = evaluate_performance_gate(
        "initial_terminal_failure",
        events=[
            {"event_type": "initial_agent_turn_failure", "data": {"error_code": "provider_status_error", "status_code": 503}},
            {"event_type": "final_answer_path", "data": {"path": "terminal_failure"}},
            {"event_type": "performance_stage", "data": {"stage": "initial_agent_turn", "attempt_index": 1, "provider_success": False, "runtime_accepted": False}},
        ],
        metrics={
            "llm_call_count_by_stage": {"initial_agent_turn": 1},
            "initial_agent_turn_attempt_count": 1,
            "initial_agent_turn_failure_status_code": 429,
        },
    )
    assert status_mismatch.passed is False
    print("smoke_performance_gates ok")


if __name__ == "__main__":
    main()
