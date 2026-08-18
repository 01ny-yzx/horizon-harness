from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trace import AgentTrace, MAX_SUMMARY_CHARS


def test_task_and_latest_trace_files() -> None:
    trace = AgentTrace("task/unsafe id", "trace smoke", "build")
    structured = {
        "total_ms": 1234,
        "first_call_ms": 15,
        "planner_ms": 20,
        "tool_call_ms": 30,
        "tool_execution_ms": 40,
        "final_answer_ms": 50,
        "stages": {"final_answer": {"prompt_chars": 1000, "tool_schema_chars": 0}},
        "llm_profile": {"max_tokens": 512, "timeout": 45, "disable_reasoning": True},
        "long_value": "x" * 1600,
    }
    trace.add_event(
        1,
        "runtime_metrics",
        json.dumps(structured, ensure_ascii=False),
        success=True,
        data=structured,
    )
    trace.add_event(
        1,
        "agent_turn_context",
        json.dumps(
            {
                "agent_turn_history_message_count": 2,
                "agent_turn_history_chars": 24,
                "agent_turn_compacted": False,
                "agent_turn_current_user_count": 1,
            },
            ensure_ascii=False,
        ),
        success=True,
    )
    trace.add_event(
        1,
        "final_observation_context",
        json.dumps({"observation_count": 2, "call_ids": ["call_a", "call_b"]}, ensure_ascii=False),
        success=True,
    )

    with TemporaryDirectory() as temporary_directory:
        trace_dir = Path(temporary_directory)
        saved = trace.save_task_files(trace_dir, final_answer="final answer")
        assert saved.trace_id == "task_unsafe_id"
        assert saved.trace_path.name == "agent_trace_task_unsafe_id.json"
        assert saved.latest_trace_path.name == "agent_trace_latest.json"
        assert saved.trace_path.read_bytes() == saved.latest_trace_path.read_bytes()

        payload = json.loads(saved.trace_path.read_text(encoding="utf-8"))
        assert payload["task_id"] == "task/unsafe id"
        assert payload["final_answer"] == "final answer"
        assert payload["final_answer_chars"] == len("final answer")
        assert payload["created_at"]
        assert payload["completed_at"]
        assert payload["trace_path"] == str(saved.trace_path)
        assert payload["latest_trace_path"] == str(saved.latest_trace_path)

        event = payload["events"][0]
        assert len(event["summary"]) <= MAX_SUMMARY_CHARS
        assert event["data"]["long_value"] == structured["long_value"]
        assert event["data"]["llm_profile"]["disable_reasoning"] is True
        context_event = next(item for item in payload["events"] if item["event_type"] == "agent_turn_context")
        assert context_event["data"]["agent_turn_current_user_count"] == 1
        final_context_event = next(
            item for item in payload["events"] if item["event_type"] == "final_observation_context"
        )
        assert final_context_event["data"]["call_ids"] == ["call_a", "call_b"]
        assert not list(trace_dir.glob("*.tmp"))
        assert not list(trace_dir.glob(".*.tmp"))


def test_runtime_and_api_wiring() -> None:
    loop_source = (ROOT / "core" / "loop.py").read_text(encoding="utf-8")
    api_source = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "metrics_data = metrics.summary()" in loop_source
    assert "data=metrics_data" in loop_source
    assert "trace.complete(final_answer)" in loop_source
    assert "trace.save_task_files" in loop_source
    assert "last_trace_save_error" in loop_source
    assert "Trace Save Warning" in loop_source
    assert "file=sys.stderr" in loop_source
    assert "agent.last_trace_id" in api_source
    assert 'trace_id = "agent_trace_latest"' not in api_source


def main() -> None:
    test_task_and_latest_trace_files()
    test_runtime_and_api_wiring()
    print("smoke_trace_task_files ok")


if __name__ == "__main__":
    main()
