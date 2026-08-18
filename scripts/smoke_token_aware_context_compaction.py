"""Offline smoke checks for model-aware context compaction."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.context_budget import ContextBudgetConfig, apply_context_budget
from core.message_validator import validate_openai_tool_messages
from core.memory import Memory


def _tool_schema(name: str, description: str = "tool") -> dict[str, object]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object"}}}


def _chain(call_id: str, body: str, *, task_id: str = "old") -> list[dict[str, object]]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
            "metadata": {"task_id": task_id},
        },
        {
            "role": "tool",
            "name": "read_file",
            "tool_call_id": call_id,
            "content": json.dumps({"call_id": call_id, "tool": "read_file", "success": True, "status": "success", "data": {"path": "a.txt", "content": body}}),
            "metadata": {"task_id": task_id},
        },
    ]


def _state() -> SimpleNamespace:
    return SimpleNamespace(metadata={})


def test_keep_without_pressure() -> None:
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}]
    tools = [_tool_schema("read_file")]
    result, decision = apply_context_budget(messages, tools=tools, runtime_lane="chat", task_state=_state(), model_context_tokens=8192, reserved_output_tokens=1024)
    assert result == messages
    assert decision.action == "keep"
    assert decision.reason == "within_token_budget"
    assert decision.pressure_detected is False
    assert not any("Context Compact Summary" in str(message.get("content")) for message in result)


def test_model_capacity_and_output_reserve() -> None:
    messages = [{"role": "system", "content": "system"}]
    for index in range(6):
        messages.extend([{"role": "user", "content": f"old-{index}-" + "x" * 4500}, {"role": "assistant", "content": "answer-" + "y" * 1200}])
    messages.append({"role": "user", "content": "current"})
    small, small_decision = apply_context_budget(messages, tools=[], runtime_lane="chat", task_state=_state(), model_context_tokens=8192, reserved_output_tokens=1024)
    large, large_decision = apply_context_budget(messages, tools=[], runtime_lane="chat", task_state=_state(), model_context_tokens=32768, reserved_output_tokens=1024)
    assert small_decision.pressure_detected is True and small_decision.action == "compact"
    assert large == messages and large_decision.pressure_detected is False
    assert any("Context Compact Summary" in str(message.get("content")) for message in small)

    _, reserve_small = apply_context_budget(messages[:9], tools=[], runtime_lane="chat", task_state=_state(), model_context_tokens=8192, reserved_output_tokens=1024)
    _, reserve_large = apply_context_budget(messages[:9], tools=[], runtime_lane="chat", task_state=_state(), model_context_tokens=8192, reserved_output_tokens=4096)
    assert reserve_large.usable_input_tokens < reserve_small.usable_input_tokens
    assert reserve_large.pressure_detected or not reserve_small.pressure_detected


def test_tools_and_protocol_are_counted_and_protected() -> None:
    base = [{"role": "system", "content": "system"}, {"role": "user", "content": "current"}]
    tools = [_tool_schema("large_tool", "z" * 18000)]
    _, with_tools = apply_context_budget(base, tools=tools, runtime_lane="build", task_state=_state(), model_context_tokens=4096, reserved_output_tokens=1024)
    _, without_tools = apply_context_budget(base, tools=[], runtime_lane="build", task_state=_state(), model_context_tokens=4096, reserved_output_tokens=1024)
    assert with_tools.original_estimated_tokens > without_tools.original_estimated_tokens
    assert with_tools.pressure_detected is True

    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "current"}, *_chain("call-success", "S" * 12000, task_id="current"), *_chain("call-failed", "F" * 12000, task_id="current")]
    messages[-1]["content"] = json.dumps({"call_id": "call-failed", "tool": "read_file", "success": False, "status": "failed", "error_code": "file_not_found", "data": {"content": "F" * 12000}})
    compacted, decision = apply_context_budget(messages, tools=[], runtime_lane="build", task_state=_state(), model_context_tokens=4096, reserved_output_tokens=1024)
    validate_openai_tool_messages([{key: value for key, value in message.items() if key != "metadata"} for message in compacted])
    rendered = json.dumps(compacted)
    assert "call-success" in rendered and "call-failed" in rendered
    assert decision.protected_current_task_tokens > 0


def test_old_tools_compact_before_old_turns_and_recent_turns_survive() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "very old goal"},
        *_chain("call-old", "O" * 20000),
        {"role": "assistant", "content": "very old conclusion"},
        {"role": "user", "content": "recent one"},
        {"role": "assistant", "content": "recent answer one"},
        {"role": "user", "content": "recent two"},
        {"role": "assistant", "content": "recent answer two"},
        {"role": "user", "content": "current"},
    ]
    compacted, decision = apply_context_budget(messages, tools=[], runtime_lane="build", task_state=_state(), model_context_tokens=4096, reserved_output_tokens=1024)
    rendered = json.dumps(compacted, ensure_ascii=False)
    assert "recent one" in rendered and "recent two" in rendered and "current" in rendered
    assert "call-old" in decision.metadata["compacted_tool_call_ids"]
    validate_openai_tool_messages([{key: value for key, value in message.items() if key != "metadata"} for message in compacted])


def test_missing_model_limits_fail_closed() -> None:
    messages = [{"role": "user", "content": "short"}]
    try:
        apply_context_budget(messages, tools=[], runtime_lane="chat", task_state=_state(), model_context_tokens=None, reserved_output_tokens=1024)
    except RuntimeError as exc:
        assert "Unable to resolve model token limits" in str(exc)
    else:
        raise AssertionError("missing model limits must fail before an LLM request")


def test_memory_history_is_not_destructively_compacted() -> None:
    memory = Memory()
    memory.begin_task("old")
    memory.add_user_message("old request", task_id="old")
    memory.add_assistant_message(
        "",
        tool_calls=[{"id": "call-old", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        task_id="old",
    )
    memory.add_tool_observation("call-old", "read_file", '{"success":true,"status":"success"}', task_id="old")
    for index in range(35):
        memory.add_assistant_message(f"history-{index}", task_id="old")
    before = len(memory.messages)
    memory.begin_task("current")
    assert len(memory.messages) == before
    assert any(message.get("role") == "tool" for message in memory.messages)
    assert memory.summarize_if_needed() is False
    assert len(memory.messages) == before
    agent_context = memory.get_agent_turn_context("current", "current request")
    assert not any(message.get("role") == "tool" for message in agent_context.messages)


def main() -> None:
    test_keep_without_pressure()
    test_model_capacity_and_output_reserve()
    test_tools_and_protocol_are_counted_and_protected()
    test_old_tools_compact_before_old_turns_and_recent_turns_survive()
    test_missing_model_limits_fail_closed()
    test_memory_history_is_not_destructively_compacted()
    print("smoke_token_aware_context_compaction ok")


if __name__ == "__main__":
    main()
