"""Trace and metrics gates for Horizon Runtime performance invariants."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.unicode_safety import sanitize_unicode


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actual: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(
            {
                "name": self.name,
                "passed": self.passed,
                "failures": list(self.failures),
                "warnings": list(self.warnings),
                "actual": dict(self.actual),
            }
        )


def evaluate_performance_gate(
    scenario: str,
    *,
    events: Sequence[dict[str, Any]] | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> GateResult:
    """Evaluate one named performance gate from trace events and/or metrics."""

    name = str(scenario or "").strip()
    normalized_events = list(events or [])
    normalized_metrics = dict(metrics or {})
    if name == "single_file_read":
        return _evaluate_single_file_read(normalized_events, normalized_metrics)
    if name == "command_exec":
        return _evaluate_command_exec(normalized_events, normalized_metrics)
    if name == "file_output":
        return _evaluate_file_output(normalized_events, normalized_metrics)
    if name == "mixed_build":
        return _evaluate_mixed_build(normalized_events, normalized_metrics)
    if name == "blocked_guard":
        return _evaluate_blocked_guard(normalized_events, normalized_metrics)
    if name == "build_step_contract":
        return _evaluate_build_step_contract(normalized_events, normalized_metrics)
    if name == "initial_direct_answer":
        return _evaluate_initial_direct_answer(normalized_events, normalized_metrics)
    if name == "initial_single_tool":
        return _evaluate_initial_single_tool(normalized_events, normalized_metrics)
    if name == "initial_policy_block":
        return _evaluate_initial_policy_block(normalized_events, normalized_metrics)
    if name == "initial_retry_success":
        return _evaluate_initial_retry_success(normalized_events, normalized_metrics)
    if name == "initial_terminal_failure":
        return _evaluate_initial_terminal_failure(normalized_events, normalized_metrics)
    return GateResult(
        name=name or "unknown",
        passed=False,
        failures=[f"unknown performance gate scenario: {name or 'unknown'}"],
        actual={"scenario": name},
    )


def normalize_event_summary(summary: Any) -> dict[str, Any]:
    """Normalize trace event summary to a dictionary when possible."""

    if isinstance(summary, Mapping):
        return sanitize_unicode(dict(summary))
    if isinstance(summary, str):
        text = summary.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        if isinstance(payload, Mapping):
            return sanitize_unicode(dict(payload))
        return {"value": sanitize_unicode(payload)}
    return {}


def events_by_type(events: Sequence[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if str(event.get("event_type") or "") == event_type]


def last_event(events: Sequence[dict[str, Any]], event_type: str) -> dict[str, Any]:
    matching = events_by_type(events, event_type)
    return dict(matching[-1]) if matching else {}


def performance_stages(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_event_payload(event) for event in events_by_type(events, "performance_stage")]


def count_stage(events: Sequence[dict[str, Any]], stage_name: str) -> int:
    return sum(1 for stage in performance_stages(events) if str(stage.get("stage") or "") == stage_name)


def count_completed_llm_stage(events: Sequence[dict[str, Any]], stage_name: str) -> int:
    return sum(
        1
        for stage in performance_stages(events)
        if str(stage.get("stage") or "") == stage_name and "duration_ms" in stage
    )


def count_completed_tool_call_stages(events: Sequence[dict[str, Any]]) -> int:
    return sum(
        1
        for stage in performance_stages(events)
        if str(stage.get("stage") or "") == "tool_call" and "duration_ms" in stage
    )


def _evaluate_single_file_read(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _actual_summary("single_file_read", events, metrics)
    failures: list[str] = []
    initial_path = _uses_initial_agent_turn(actual)
    if not initial_path:
        _expect_surface(
            actual,
            failures,
            lane="single_file_read",
            surface="read",
            tools=["read_file", "read_document"],
        )
    else:
        surface = actual["capability_surface"]
        if str(surface.get("runtime_lane") or "") == "single_file_read":
            failures.append(
                "expected non-authoritative Initial Tool Batch not to select single_file_read"
            )
        tools = _list(surface.get("effective_tools"))
        if not {"read_file", "read_document"} <= set(tools):
            failures.append(
                "expected stable Initial Tool Surface to include read_file and "
                f"read_document, got {tools}"
            )
    _expect_guard(actual, failures, allowed=True, tool_name="read_file")
    _expect_observation(
        actual,
        failures,
        reason=(
            "successful_observation_requires_agent_decision"
            if initial_path
            else "single_file_read_observation_satisfied"
        ),
    )
    expected_single_file_continuation = 0 if initial_path else 1
    if actual["single_file_read_agent_continuation_count"] != expected_single_file_continuation:
        failures.append(
            "expected single_file_read_agent_continuation stage count "
            f"{expected_single_file_continuation}, "
            f"got {actual['single_file_read_agent_continuation_count']}"
        )
    if initial_path and actual["agent_continuation_count"] != 1:
        failures.append(
            "expected one agent-owned continuation stage, "
            f"got {actual['agent_continuation_count']}"
        )
    if actual["generic_final_answer_count"] != 0:
        failures.append(f"expected no generic final_answer stage, got {actual['generic_final_answer_count']}")
    if not actual["direct_agent_prose_adopted"]:
        failures.append("expected direct_agent_prose_adopted continuation result")
    if initial_path and actual["total_llm_call_count"] != 2:
        failures.append(f"expected exactly two LLM calls, got {actual['total_llm_call_count']}")
    expected_tool_call_llm_count = 0 if initial_path else 1
    if actual["tool_call_count"] != expected_tool_call_llm_count:
        failures.append(
            f"expected completed tool_call stage count {expected_tool_call_llm_count}, "
            f"got {actual['tool_call_count']}"
        )
    if initial_path and actual["tool_execution_count"] != 1:
        failures.append(f"expected one read_file execution, got {actual['tool_execution_count']}")
    return _result("single_file_read", failures, [], actual)


def _evaluate_command_exec(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _actual_summary("command_exec", events, metrics)
    failures: list[str] = []
    initial_path = _uses_initial_agent_turn(actual)
    if not initial_path:
        _expect_surface(actual, failures, lane="command_exec", surface="bash", tools=["sandbox_exec"])
    else:
        surface = actual["capability_surface"]
        if str(surface.get("runtime_lane") or "") == "command_exec":
            failures.append(
                "expected non-authoritative Initial Tool Batch not to select command_exec"
            )
        tools = _list(surface.get("effective_tools"))
        if "sandbox_exec" not in tools:
            failures.append(
                f"expected stable Initial Tool Surface to include sandbox_exec, got {tools}"
            )
    _expect_guard(actual, failures, allowed=True, tool_name="sandbox_exec")
    _expect_observation(
        actual,
        failures,
        reason=(
            "successful_observation_requires_agent_decision"
            if initial_path
            else "command_exec_observation_complete"
        ),
    )
    expected_tool_call_llm_count = 0 if initial_path else 1
    if actual["tool_call_count"] != expected_tool_call_llm_count:
        failures.append(
            f"expected completed tool_call stage count {expected_tool_call_llm_count}, "
            f"got {actual['tool_call_count']}"
        )
    if initial_path and actual["tool_execution_count"] != 1:
        failures.append(f"expected one sandbox_exec execution, got {actual['tool_execution_count']}")
    return _result("command_exec", failures, [], actual)


def _evaluate_file_output(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _actual_summary("file_output", events, metrics)
    failures: list[str] = []
    warnings: list[str] = []
    initial_path = _uses_initial_agent_turn(actual)
    if not initial_path:
        _expect_surface(actual, failures, lane="file_output", surface="write", tools=["write_file"])
    else:
        surface = actual["capability_surface"]
        if str(surface.get("runtime_lane") or "") == "file_output":
            failures.append(
                "expected non-authoritative Initial Tool Batch not to select file_output"
            )
        tools = _list(surface.get("effective_tools"))
        if "write_file" not in tools:
            failures.append(
                f"expected stable Initial Tool Surface to include write_file, got {tools}"
            )
    _expect_guard(actual, failures, allowed=True, tool_name="write_file")
    _expect_observation(
        actual,
        failures,
        reason=(
            "successful_observation_requires_agent_decision"
            if initial_path
            else "file_output_observation_complete"
        ),
    )
    expected_tool_call_llm_count = 0 if initial_path else 1
    if actual["tool_call_count"] != expected_tool_call_llm_count:
        failures.append(
            f"expected completed tool_call stage count {expected_tool_call_llm_count}, "
            f"got {actual['tool_call_count']}"
        )
    if initial_path and actual["tool_execution_count"] != 1:
        failures.append(f"expected one write_file execution, got {actual['tool_execution_count']}")
    if actual["generic_final_answer_count"] > 0:
        warnings.append(f"generic final_answer stage present: {actual['generic_final_answer_count']}")
    return _result("file_output", failures, warnings, actual)


def _evaluate_mixed_build(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _actual_summary("mixed_build", events, metrics)
    failures: list[str] = []
    initial_path = _uses_initial_agent_turn(actual)
    if initial_path:
        mode = str(actual["initial_agent_turn"].get("mode") or "")
        if mode != "tool_calls":
            failures.append(f"expected structured initial mixed/build mode, got {mode!r}")
        if not actual["build_step_contract"]:
            failures.append("expected BuildStepContract on initial mixed/build path")
    surface = actual["capability_surface"]
    if str(surface.get("runtime_lane") or "") != "build":
        failures.append(f"expected capability_surface.runtime_lane build, got {surface.get('runtime_lane')!r}")
    if str(surface.get("capability_surface") or "") != "build":
        failures.append(f"expected capability_surface build, got {surface.get('capability_surface')!r}")
    tools = _list(surface.get("effective_tools"))
    if tools == ["sandbox_exec"]:
        failures.append("expected mixed effective_tools not to be single-tool ['sandbox_exec']")
    return _result("mixed_build", failures, [], actual)


def _evaluate_blocked_guard(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _actual_summary("blocked_guard", events, metrics)
    failures: list[str] = []
    guard = actual["guard_fast_path"]
    if bool(guard.get("allowed")):
        failures.append("expected guard_fast_path.allowed false")
    if str(guard.get("status") or "") != "blocked":
        failures.append(f"expected guard_fast_path.status blocked, got {guard.get('status')!r}")
    if not str(guard.get("policy_code") or ""):
        failures.append("expected blocked guard policy_code")
    return _result("blocked_guard", failures, [], actual)


def _evaluate_build_step_contract(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _actual_summary("build_step_contract", events, metrics)
    failures: list[str] = []
    contract = actual["build_step_contract"]
    if not contract:
        failures.append("expected build_step_contract event")
    if str(contract.get("runtime_lane") or "") != "build":
        failures.append(f"expected build_step_contract.runtime_lane build, got {contract.get('runtime_lane')!r}")
    steps = contract.get("steps") if isinstance(contract.get("steps"), list) else []
    if len(steps) < 2:
        failures.append(f"expected at least 2 build contract steps, got {len(steps)}")
    step_tools = [str(step.get("tool_name") or "") for step in steps if isinstance(step, dict)]
    surface = actual["capability_surface"]
    if str(surface.get("capability_surface") or "") != "build":
        failures.append(f"expected capability_surface build, got {surface.get('capability_surface')!r}")
    tools = _list(surface.get("effective_tools"))
    if {"read_file", "sandbox_exec"} <= set(tools):
        if not {"read_file", "sandbox_exec"} <= set(step_tools):
            failures.append(f"expected contract steps to contain read_file and sandbox_exec, got {step_tools}")
    elif {"file_read", "command_exec"} <= set(_list(surface.get("effective_capabilities"))):
        failures.append(f"expected effective_tools to include read_file and sandbox_exec, got {tools}")
    progress = actual["build_step_progress"]
    if not progress:
        failures.append("expected build_step_progress event")
    else:
        if int(progress.get("completed_count") or 0) < 1:
            failures.append(f"expected build_step_progress completed_count >= 1, got {progress.get('completed_count')!r}")
        progress_resolved = bool(progress.get("all_steps_completed") or progress.get("all_steps_resolved"))
        expected_index = 0 if progress_resolved else 2
        if int(progress.get("current_step_index") or 0) != expected_index:
            failures.append(
                f"expected build_step_progress current_step_index {expected_index}, got {progress.get('current_step_index')!r}"
            )
        expected_next = "" if progress_resolved else "sandbox_exec"
        if str(progress.get("next_tool") or "") != expected_next:
            failures.append(f"expected build_step_progress next_tool sandbox_exec, got {progress.get('next_tool')!r}")
    if actual["post_completion_contract_loop_count"] > 0:
        failures.append(
            f"expected no build_step_contract events after resolved contract, got {actual['post_completion_contract_loop_count']}"
        )
    return _result("build_step_contract", failures, [], actual)


def _evaluate_initial_direct_answer(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _initial_path_summary("initial_direct_answer", events, metrics)
    failures: list[str] = []
    _expect_stage_count(actual, failures, "initial_agent_turn", 1)
    _expect_stage_count(actual, failures, "tool_call", 0)
    _expect_stage_count(actual, failures, "final_answer", 0)
    if actual["tool_execution_count"] != 0:
        failures.append(f"expected no tool execution, got {actual['tool_execution_count']}")
    if str(actual["initial_agent_turn"].get("mode") or "") != "direct_answer":
        failures.append("expected initial_agent_turn mode direct_answer")
    if str(actual["final_answer_path"].get("path") or "") != "direct_answer":
        failures.append("expected final_answer_path direct_answer")
    _expect_agent_turn_context(actual, failures)
    return _result("initial_direct_answer", failures, [], actual)


def _evaluate_initial_single_tool(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _initial_path_summary("initial_single_tool", events, metrics)
    failures: list[str] = []
    _expect_stage_count(actual, failures, "initial_agent_turn", 1)
    _expect_stage_count(actual, failures, "tool_call", 0)
    if actual["stage_counts"].get("final_answer", 0) > 1:
        failures.append("expected at most one final_answer LLM call")
    if actual["tool_execution_count"] != 1:
        failures.append(f"expected one tool execution, got {actual['tool_execution_count']}")
    if not bool(metrics.get("initial_agent_turn_direct_tool_execution", False)):
        failures.append("expected direct initial tool execution")
    if "initial_tool_call_count" in metrics and int(metrics.get("initial_tool_call_count") or 0) != 1:
        failures.append("expected exactly one initial structured ToolCall")
    _expect_agent_turn_context(actual, failures)
    return _result("initial_single_tool", failures, [], actual)


def _evaluate_initial_policy_block(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _initial_path_summary("initial_policy_block", events, metrics)
    failures: list[str] = []
    _expect_stage_count(actual, failures, "initial_agent_turn", 1)
    _expect_stage_count(actual, failures, "tool_call", 0)
    _expect_stage_count(actual, failures, "final_answer", 0)
    if str(actual["final_answer_path"].get("path") or "") != "policy_blocked":
        failures.append("expected policy_blocked final path")
    if actual["total_llm_calls"] != 1:
        failures.append(f"expected one total LLM call, got {actual['total_llm_calls']}")
    return _result("initial_policy_block", failures, [], actual)


def _evaluate_initial_retry_success(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _initial_path_summary("initial_retry_success", events, metrics)
    failures: list[str] = []
    initial = actual["initial_agent_turn"]
    if int(initial.get("retry_count") or 0) != 1:
        failures.append("expected one initial-agent retry")
    attempts = actual["initial_attempts"]
    if len(attempts) != 2:
        failures.append("expected two initial-agent attempt records")
    elif [int(item.get("attempt_index") or 0) for item in attempts] != [1, 2]:
        failures.append("expected initial-agent attempts 1 then 2")
    elif not bool(attempts[1].get("runtime_accepted")):
        failures.append("expected second initial-agent attempt to be runtime accepted")
    if actual["stage_counts"].get("initial_agent_turn", 0) != 2:
        failures.append("expected two initial_agent_turn calls")
    if any(name in actual["stage_counts"] for name in ("first_call", "planner")):
        failures.append("legacy first_call/planner metrics must be absent")
    _validate_initial_attempt_consistency(actual, failures)
    if any(int(item.get("retry_delay_ms") or 0) for item in attempts):
        if not bool(attempts[0].get("retry_wait_applied")):
            failures.append("provider retry delay must record an applied wait")
    return _result("initial_retry_success", failures, [], actual)


def _evaluate_initial_terminal_failure(events: list[dict[str, Any]], metrics: dict[str, Any]) -> GateResult:
    actual = _initial_path_summary("initial_terminal_failure", events, metrics)
    failures: list[str] = []
    if str(actual["final_answer_path"].get("path") or "") != "terminal_failure":
        failures.append("expected terminal_failure final path")
    if not _summary_for(events, "initial_agent_turn_failure"):
        failures.append("expected initial_agent_turn_failure event")
    attempts = actual["initial_attempts"]
    if len(attempts) != int(actual["stage_counts"].get("initial_agent_turn", 0) or 0):
        failures.append("initial attempt records must match initial_agent_turn call count")
    failure = _summary_for(events, "initial_agent_turn_failure")
    error_code = str(failure.get("error_code") or "")
    if error_code in {"timeout", "connection_error", "rate_limit", "provider_status_error"}:
        if any(bool(item.get("provider_success")) for item in attempts):
            failures.append("provider retry failure must not be marked as model output")
    if attempts and actual["stage_counts"].get("final_answer", 0):
        failures.append("terminal initial failure must not call final_answer LLM")
    _validate_initial_attempt_consistency(actual, failures)
    if str(actual["retry_skipped_reason"] or "") == "provider_retry_delay_exceeds_inline_limit":
        if len(attempts) != 1:
            failures.append("long provider retry delay must not create a second attempt")
    failure_status_code = failure.get("status_code")
    metric_status_code = actual.get("failure_status_code")
    if isinstance(failure_status_code, int) and failure_status_code != metric_status_code:
        failures.append("terminal failure status code must match metrics")
    return _result("initial_terminal_failure", failures, [], actual)


def _initial_path_summary(scenario: str, events: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    counts = {str(key): int(value or 0) for key, value in dict(metrics.get("llm_call_count_by_stage") or {}).items()}
    if not counts:
        counts = {
            name: count_stage(events, name)
            for name in (
                "initial_agent_turn",
                "tool_call",
                "agent_continuation",
                "single_file_read_agent_continuation",
                "final_answer",
            )
        }
    return sanitize_unicode(
        {
            "scenario": scenario,
            "stage_counts": counts,
            "total_llm_calls": int(metrics.get("llm_call_count") or sum(counts.values())),
            "tool_execution_count": int(metrics.get("tool_call_count") or count_stage(events, "tool_execution")),
            "initial_agent_turn": _summary_for(events, "initial_agent_turn"),
            "initial_attempts": [
                stage
                for stage in performance_stages(events)
                if str(stage.get("stage") or "") == "initial_agent_turn"
            ],
            "initial_agent_turn_attempt_count": int(
                metrics.get("initial_agent_turn_attempt_count") or 0
            ),
            "initial_agent_turn_attempt_consistent": bool(
                metrics.get("initial_agent_turn_attempt_consistent", True)
            ),
            "retry_skipped_reason": str(
                metrics.get("initial_agent_turn_retry_skipped_reason") or ""
            ),
            "retry_reason": str(
                metrics.get("initial_agent_turn_retry_reason") or ""
            ),
            "retry_wait_applied": bool(
                metrics.get("initial_agent_turn_retry_wait_applied", False)
            ),
            "failure_status_code": metrics.get(
                "initial_agent_turn_failure_status_code"
            ),
            "final_answer_path": _summary_for(events, "final_answer_path"),
            "build_step_contract": _summary_for(events, "build_step_contract"),
            "agent_turn_context": _summary_for(events, "agent_turn_context") or _agent_context_from_metrics(metrics),
            "final_observation_context": _summary_for(events, "final_observation_context"),
        }
    )


def _validate_initial_attempt_consistency(
    actual: dict[str, Any],
    failures: list[str],
) -> None:
    attempts = actual["initial_attempts"]
    stage_count = int(actual["stage_counts"].get("initial_agent_turn", 0) or 0)
    metric_count = int(actual["initial_agent_turn_attempt_count"] or 0)
    if len(attempts) != stage_count:
        failures.append("initial attempt records must match stage count")
    if metric_count and metric_count != len(attempts):
        failures.append("initial attempt records must match metrics attempt count")
    if not bool(actual["initial_agent_turn_attempt_consistent"]):
        failures.append("initial-agent attempt consistency flag is false")
    initial = actual.get("initial_agent_turn") or {}
    retry_count = int(initial.get("retry_count") or 0)
    retry_reason = str(actual.get("retry_reason") or "")
    retry_skipped_reason = str(actual.get("retry_skipped_reason") or "")
    retry_wait_applied = bool(actual.get("retry_wait_applied"))
    if retry_count == 0 and retry_reason:
        failures.append("zero retries must not retain a retry reason")
    if retry_skipped_reason and retry_count != 0:
        failures.append("a skipped retry must not count as an executed retry")
    if retry_wait_applied and (retry_count != 1 or len(attempts) != 2):
        failures.append("an applied retry wait must precede a second attempt")
    for attempt in attempts:
        if bool(attempt.get("provider_success")) and bool(attempt.get("usage_available")):
            values = [
                int(attempt.get(name) or 0)
                for name in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "reasoning_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                )
            ]
            if any(value < 0 for value in values):
                failures.append("provider usage tokens must be nonnegative")
            if values[2] < values[0] or values[2] < values[1]:
                failures.append("total_tokens must cover input and output tokens")


def _expect_stage_count(actual: dict[str, Any], failures: list[str], stage: str, expected: int) -> None:
    count = int((actual.get("stage_counts") or {}).get(stage, 0) or 0)
    if count != expected:
        failures.append(f"expected {stage} count {expected}, got {count}")


def _expect_agent_turn_context(actual: dict[str, Any], failures: list[str]) -> None:
    context = actual.get("agent_turn_context") if isinstance(actual.get("agent_turn_context"), dict) else {}
    if context and int(context.get("agent_turn_current_user_count") or 0) != 1:
        failures.append("expected current user request exactly once in agent turn context")


def _agent_context_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    if "agent_turn_current_user_count" not in metrics:
        return {}
    return {
        "agent_turn_history_message_count": metrics.get("agent_turn_history_message_count", 0),
        "agent_turn_history_chars": metrics.get("agent_turn_history_chars", 0),
        "agent_turn_compacted": metrics.get("agent_turn_compacted", False),
        "agent_turn_current_user_count": metrics.get("agent_turn_current_user_count", 0),
    }


def _actual_summary(scenario: str, events: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    surface = _summary_for(events, "capability_surface") or _surface_from_metrics(metrics)
    guard = _summary_for(events, "guard_fast_path") or _guard_from_metrics(metrics)
    observation = _summary_for(events, "observation")
    contract = _summary_for(events, "build_step_contract") or _build_contract_from_metrics(metrics)
    progress = _summary_for(events, "build_step_progress") or _build_progress_from_metrics(metrics)
    stage_counts = {
        str(key): int(value or 0)
        for key, value in dict(metrics.get("llm_call_count_by_stage") or {}).items()
    }
    if not stage_counts:
        stage_counts = {
            name: (
                max(count_completed_llm_stage(events, name), len(events_by_type(events, "initial_agent_turn")))
                if name == "initial_agent_turn"
                else count_completed_llm_stage(events, name)
            )
            for name in (
                "initial_agent_turn",
                "tool_call",
                "agent_continuation",
                "single_file_read_agent_continuation",
                "final_answer",
            )
        }
    else:
        stage_counts["initial_agent_turn"] = max(
            int(stage_counts.get("initial_agent_turn", 0) or 0),
            len(events_by_type(events, "initial_agent_turn")),
        )
    return sanitize_unicode(
        {
            "scenario": scenario,
            "capability_surface": surface,
            "guard_fast_path": guard,
            "observation": observation,
            "build_step_contract": contract,
            "build_step_progress": progress,
            "initial_agent_turn": _summary_for(events, "initial_agent_turn"),
            "stage_counts": stage_counts,
            "tool_call_count": count_completed_tool_call_stages(events),
            "tool_execution_count": int(metrics.get("tool_call_count") or count_stage(events, "tool_execution")),
            "single_file_read_final_answer_count": count_stage(events, "single_file_read_final_answer"),
            "single_file_read_agent_continuation_count": int(
                stage_counts.get("single_file_read_agent_continuation", 0)
            ),
            "agent_continuation_count": int(stage_counts.get("agent_continuation", 0)),
            "generic_final_answer_count": count_stage(events, "final_answer"),
            "direct_agent_prose_adopted": any(
                bool(_event_payload(event).get("direct_agent_prose_adopted"))
                for event in events_by_type(events, "agent_continuation_result")
            ),
            "total_llm_call_count": max(
                int(metrics.get("llm_call_count") or 0),
                sum(int(value or 0) for value in stage_counts.values()),
            ),
            "post_completion_contract_loop_count": _post_completion_contract_loop_count(events),
        }
    )


def _uses_initial_agent_turn(actual: dict[str, Any]) -> bool:
    initial = actual.get("initial_agent_turn") if isinstance(actual.get("initial_agent_turn"), dict) else {}
    mode = str(initial.get("mode") or "")
    return mode in {"direct_answer", "tool_calls"}


def _post_completion_contract_loop_count(events: list[dict[str, Any]]) -> int:
    resolved_seen = False
    repeats = 0
    for event in events:
        if str(event.get("event_type") or "") != "build_step_contract":
            continue
        summary = normalize_event_summary(event.get("summary"))
        if resolved_seen:
            repeats += 1
        if summary.get("all_steps_completed") or summary.get("all_steps_resolved"):
            resolved_seen = True
    return repeats


def _summary_for(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    event = last_event(events, event_type)
    return _event_payload(event) if event else {}


def _event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    if isinstance(data, Mapping) and data:
        return sanitize_unicode(dict(data))
    return normalize_event_summary(event.get("summary"))


def _surface_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    records = metrics.get("capability_surface_records")
    if isinstance(records, list) and records:
        return dict(records[-1])
    if not metrics.get("capability_surface"):
        return {}
    return {
        "capability_surface": metrics.get("capability_surface"),
        "effective_capabilities": metrics.get("effective_capabilities") or [],
        "effective_tools": metrics.get("effective_tools") or [],
    }


def _guard_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    records = metrics.get("tool_guard_fast_path_records")
    return dict(records[-1]) if isinstance(records, list) and records else {}


def _build_contract_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    records = metrics.get("build_step_contract_records")
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict) and record.get("steps"):
                return dict(record)
    return {}


def _build_progress_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    records = metrics.get("build_step_contract_records")
    if isinstance(records, list):
        for record in reversed(records):
            if isinstance(record, dict) and record.get("completed_steps") is not None:
                return dict(record)
    return {}


def _expect_surface(
    actual: dict[str, Any],
    failures: list[str],
    *,
    lane: str,
    surface: str,
    tools: list[str],
) -> None:
    summary = actual["capability_surface"]
    if str(summary.get("runtime_lane") or "") != lane:
        failures.append(f"expected capability_surface.runtime_lane {lane}, got {summary.get('runtime_lane')!r}")
    if str(summary.get("capability_surface") or "") != surface:
        failures.append(f"expected capability_surface {surface}, got {summary.get('capability_surface')!r}")
    actual_tools = _list(summary.get("effective_tools"))
    if actual_tools != tools:
        failures.append(f"expected effective_tools {tools}, got {actual_tools}")


def _expect_guard(actual: dict[str, Any], failures: list[str], *, allowed: bool, tool_name: str) -> None:
    guard = actual["guard_fast_path"]
    if bool(guard.get("allowed")) is not allowed:
        failures.append(f"expected guard_fast_path.allowed {allowed}, got {guard.get('allowed')!r}")
    if str(guard.get("tool_name") or "") != tool_name:
        failures.append(f"expected guard tool_name {tool_name}, got {guard.get('tool_name')!r}")


def _expect_observation(actual: dict[str, Any], failures: list[str], *, reason: str) -> None:
    observation = actual["observation"]
    if str(observation.get("reason") or "") != reason:
        failures.append(f"expected observation reason {reason}, got {observation.get('reason')!r}")
    if _requires_more_tools(observation):
        failures.append("expected observation.requires_more_tools false")


def _requires_more_tools(observation: dict[str, Any]) -> bool:
    if "requires_more_tools" in observation:
        return bool(observation.get("requires_more_tools"))
    if "skip_next_tool_call" in observation:
        return not bool(observation.get("skip_next_tool_call"))
    return False


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "")]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item or "")]
    return []


def _result(name: str, failures: list[str], warnings: list[str], actual: dict[str, Any]) -> GateResult:
    return GateResult(name=name, passed=not failures, failures=failures, warnings=warnings, actual=actual)
