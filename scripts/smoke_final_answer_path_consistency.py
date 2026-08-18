from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.emergency_finalization as emergency_finalization
from core.finalization_path import (
    FinalAnswerPathDecision,
    record_final_answer_path_once,
)
from core.loop import AgentLoop
from core.trace import AgentTrace


def test_final_answer_path_is_recorded_once() -> None:
    trace = AgentTrace("task-smoke", "structured smoke", "simple")
    decision = FinalAnswerPathDecision(
        path="llm_final",
        trigger="observation_completion",
        outcome_kind="terminal_success",
        tool="sample_tool",
        tools_disabled=True,
        snapshot_hash="snapshot-smoke",
    )
    assert record_final_answer_path_once(trace, 1, decision) is True
    assert record_final_answer_path_once(trace, 1, decision) is False
    assert sum(event.event_type == "final_answer_path" for event in trace.events) == 1
    assert trace.events[-1].data["snapshot_hash"] == "snapshot-smoke"


def test_finalization_wiring_rules() -> None:
    finish_source = inspect.getsource(AgentLoop._finish_with_trace)
    assert "record_final_answer_path_once" not in finish_source
    assert "completion_observations" not in finish_source

    run_source = inspect.getsource(AgentLoop.run)
    direct_adoption = run_source.index('task_state.metadata["direct_agent_prose_adopted"] = True')
    direct_finish = run_source.index(
        "return self._finish_with_trace(trace, task_state, candidate_final_answer)",
        direct_adoption,
    )
    assert "_build_final_answer_from_task_state_timed" not in run_source[direct_adoption:direct_finish]
    assert "_build_single_file_read_final_answer_fast_timed" not in run_source[direct_adoption:direct_finish]
    normal_exit = run_source[direct_adoption:direct_finish]
    assert "terminal_responder" not in normal_exit
    assert "isolated_terminal_synthesis" not in normal_exit

    terminal_source = inspect.getsource(AgentLoop._build_final_answer_from_terminal_outcome)
    assert 'outlet.reason == "partial_outcome_with_policy_block"' in terminal_source
    assert '"partial_outcome_policy_blocked"' in terminal_source
    assert "policy_code=outlet.policy_code" in terminal_source

    emergency_source = inspect.getsource(
        emergency_finalization.build_emergency_finalization
    )
    assert "user_input" not in emergency_source
    assert "user_goal" not in emergency_source
    assert "task_state" not in emergency_source
    assert "FinalizationContextSnapshot" in emergency_source


def main() -> None:
    test_final_answer_path_is_recorded_once()
    test_finalization_wiring_rules()
    print("smoke_final_answer_path_consistency ok")


if __name__ == "__main__":
    main()
