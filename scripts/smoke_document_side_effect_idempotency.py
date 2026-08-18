"""Smoke for per-call identity protection on document side effects."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.state import TaskState
from core.tool_call_idempotency import find_completed_tool_call, record_completed_tool_call
from core.tool_call_schema import ToolCallEnvelope, ToolCallSource, ToolCallStatus
from core.tool_observation import normalize_tool_result, observation_to_cache_snapshot


def _envelope(call_id: str) -> ToolCallEnvelope:
    arguments = {"path": "same.txt", "create_chunks": True}
    return ToolCallEnvelope(
        call_id=call_id,
        provider_call_id=call_id,
        source=ToolCallSource.STRUCTURED,
        raw_name="load_document",
        tool_name="load_document",
        canonical_name="load_document",
        executable_name="load_document",
        raw_arguments=json.dumps(arguments),
        parsed_arguments=arguments,
        sanitized_arguments={},
        status=ToolCallStatus.EXECUTABLE,
        side_effect=True,
        metadata={"tool_spec_found": True},
    )


def main() -> None:
    state = TaskState.create("document identity smoke", "simple", [])
    first = _envelope("document-call-1")
    duplicate = _envelope("document-call-1")
    distinct = _envelope("document-call-2")
    observation = normalize_tool_result(
        first,
        {"success": True, "data": {"document_id": "doc-1", "document_stored": True}},
    )
    assert record_completed_tool_call(state, first, observation_to_cache_snapshot(observation))
    assert find_completed_tool_call(state, duplicate).replay is True
    assert find_completed_tool_call(state, distinct).replay is False
    print("smoke_document_side_effect_idempotency ok")


if __name__ == "__main__":
    main()
