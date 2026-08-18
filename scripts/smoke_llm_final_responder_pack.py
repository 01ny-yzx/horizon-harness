from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.final_observation_context import build_final_observation_context
from core.final_responder import build_final_responder_pack_from_snapshot
from core.finalization_context_snapshot import build_finalization_context_snapshot
from core.tool_outcome_resolution import ToolOutcomeResolution


class DummyTaskState:
    task_id = "task-current"
    task_type = "research"
    user_goal = "调用 get_usage_status"
    metadata = {"runtime_lane": "research"}
    tool_failures: list[dict[str, object]] = []
    file_output_completed = False


STATUS_DATA = {"status": "available", "remaining_quota": 42}


def _outcome() -> ToolOutcomeResolution:
    return ToolOutcomeResolution(
        "terminal_success",
        "status_tool_success",
        tool="get_usage_status",
        status="success",
        metadata={
            "observation": {
                "success": True,
                "status": "success",
                "kind": "internal",
                "tool": "get_usage_status",
                "data": dict(STATUS_DATA),
            }
        },
    )


def test_final_observation_context_preserves_status_result() -> None:
    context = build_final_observation_context(DummyTaskState(), _outcome())
    assert len(context) == 1
    assert context[0]["schema_version"] == "final_observation_context_v1"
    assert context[0]["tool"] == "get_usage_status"
    assert context[0]["success"] is True
    assert context[0]["status"] == "success"


def test_llm_final_answer_pack_disables_tools_and_contains_context() -> None:
    snapshot = build_finalization_context_snapshot(
        user_request="调用 get_usage_status",
        task_state=DummyTaskState(),
        outcome=_outcome(),
    )
    pack = build_final_responder_pack_from_snapshot(snapshot)
    assert pack.stage == "final_answer"
    assert pack.pack_name == "terminal_responder_isolated"
    assert pack.message_count == 2
    assert pack.tool_schema_chars == 0
    assert pack.metadata["observation_count"] == 1
    assert pack.metadata["coverage_complete"] is True
    assert pack.metadata["context_schema"] == "finalization_context_snapshot_v1"
    assert pack.metadata["tools_disabled"] is True
    assert pack.metadata["response_contract"]
    assert pack.metadata["finalization_mode"] == "terminal_responder"
    assert pack.metadata["memory_message_count"] == 0
    assert pack.metadata["snapshot_hash"] == snapshot.snapshot_hash
    rendered = json.dumps(pack.messages, ensure_ascii=False)
    assert "results" in rendered
    assert "conversation_summary" not in rendered
    assert "result_hints" not in rendered
    assert "get_usage_status" in rendered
    assert "success" in rendered
    assert "tools are disabled" in rendered.lower()
    assert "same language as the current user request" in rendered


def main() -> None:
    test_final_observation_context_preserves_status_result()
    test_llm_final_answer_pack_disables_tools_and_contains_context()
    print("smoke_llm_final_responder_pack ok")


if __name__ == "__main__":
    main()
