"""Smoke checks for compact, authority-neutral Agent prompt packs."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_tool_surface import build_initial_tool_surface, initial_agent_turn_tools
from core.prompt_pack import build_agent_continuation_pack, build_initial_agent_turn_pack
from core.state import PlanStep, TaskState
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs


def _rendered(pack: object) -> str:
    return "\n".join(
        str(message.get("content") or "")
        for message in getattr(pack, "messages")
    )


def test_initial_pack_uses_real_tools() -> None:
    request = "读取文件并总结"
    surface = build_initial_tool_surface(
        get_unified_tool_specs(),
        get_unified_tool_schemas(),
        access_mode="full_access",
    )
    pack = build_initial_agent_turn_pack(
        user_input=request,
        tools=initial_agent_turn_tools(surface),
        memory_messages=[{"role": "user", "content": request}],
        current_user_included=True,
        access_mode="full_access",
    )
    text = _rendered(pack)
    assert text.count(request) == 1
    assert "structured tools" in text
    assert "actual observations" in text
    assert "current_step" not in text
    assert pack.metadata["tool_surface_mode"] == "registry_availability_permission"


def test_continuation_pack_replays_observation_once_without_step_projection() -> None:
    state = TaskState.create(
        "读取后回答",
        "simple",
        [PlanStep(1, "read", "read")],
    )
    memory_messages = [
        {"role": "user", "content": state.user_goal},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "read-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"/tmp/source.txt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read-1",
            "name": "read_file",
            "content": '{"success":true,"content":"OBSERVATION_ONCE"}',
        },
    ]
    surface = build_initial_tool_surface(
        get_unified_tool_specs(),
        get_unified_tool_schemas(),
        access_mode="full_access",
    )
    pack = build_agent_continuation_pack(
        user_input=state.user_goal,
        task_state=state,
        tools=list(surface.schemas),
        memory_messages=memory_messages,
        current_user_included=True,
        access_mode="full_access",
    )
    rendered = _rendered(pack)
    assert rendered.count("OBSERVATION_ONCE") == 1
    assert "Current step context:" not in rendered
    assert "Tool Completion Gate:" not in rendered
    assert "recoverable_failure_stable_agent_surface" not in rendered
    assert pack.metadata["context_projection_chars"] == 0
    assert pack.metadata["tool_surface_mode"] == "registry_availability_permission"


def main() -> None:
    test_initial_pack_uses_real_tools()
    test_continuation_pack_replays_observation_once_without_step_projection()
    print("smoke_prompt_pack_compaction ok")


if __name__ == "__main__":
    main()
