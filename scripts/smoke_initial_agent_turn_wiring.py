"""Minimal wiring smoke for one-call initial agent turns."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.finalization_path import FinalAnswerPathDecision, record_final_answer_path_once
from core.initial_agent_turn import execute_initial_agent_turn
from core.initial_tool_surface import build_initial_tool_surface, initial_agent_turn_tools
from core.loop import consume_pending_initial_agent_message
from core.prompt_pack import build_initial_agent_turn_pack
from core.tool_call_schema import ToolCallSource, build_structured_tool_call_envelope
from core.tool_spec import ToolKind, ToolProvider, ToolRisk, ToolSpec
from core.trace import AgentTrace


def _schema(name: str, required: list[str] | None = None) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}, "required": required or []},
        },
    }


def _call(call_id: str, name: str, arguments: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class FakeLLM:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def chat(self, *, messages: list[dict[str, object]], tools: list[dict[str, object]], options: object) -> object:
        self.calls.append({"messages": messages, "tools": tools, "options": options})
        return self.response


def main() -> None:
    specs = {
        "read_file": ToolSpec("read_file", ToolProvider.LOCAL, ToolKind.FILE_READ, risk=ToolRisk.READ_ONLY),
        "replace_in_file": ToolSpec(
            "replace_in_file",
            ToolProvider.LOCAL,
            ToolKind.FILE_WRITE,
            capabilities=("coding", "file_write"),
            side_effect=True,
            risk=ToolRisk.FILE_WRITE,
        ),
        "sandbox_exec": ToolSpec(
            "sandbox_exec", ToolProvider.LOCAL, ToolKind.EXECUTION, side_effect=True, risk=ToolRisk.SHELL_EXECUTION
        ),
        "database_query": ToolSpec(
            "database_query", ToolProvider.LOCAL, ToolKind.DATABASE_READ, risk=ToolRisk.READ_ONLY
        ),
        "load_document": ToolSpec(
            "load_document",
            ToolProvider.LOCAL,
            ToolKind.FILE_READ,
            capabilities=("document_load", "state_mutation"),
            side_effect=True,
            risk=ToolRisk.INTERNAL_STATE,
        ),
    }
    schemas = [
        _schema("read_file", ["path"]),
        _schema("replace_in_file"),
        _schema("sandbox_exec", ["command"]),
        _schema("database_query", ["query"]),
        _schema("load_document", ["path"]),
    ]
    surface = build_initial_tool_surface(specs, schemas, access_mode="full_access")
    tools = initial_agent_turn_tools(surface)
    assert [item["function"]["name"] for item in tools] == list(surface.tool_names)
    pack = build_initial_agent_turn_pack(user_input="structured request", tools=tools)

    shell_message = SimpleNamespace(content="", tool_calls=[_call("call_1", "sandbox_exec", {"command": "echo 1"})])
    shell_llm = FakeLLM(shell_message)
    shell_result = execute_initial_agent_turn(
        llm=shell_llm,
        messages=pack.messages,
        surface=surface,
    )
    pending, remaining, reused = consume_pending_initial_agent_message(shell_result.assistant_message)
    assert len(shell_llm.calls) == 1
    assert reused is True and remaining is None and pending is shell_message
    envelope = build_structured_tool_call_envelope(pending.tool_calls[0])
    assert envelope.source == ToolCallSource.STRUCTURED
    assert envelope.call_id == "call_1"

    direct_llm = FakeLLM(SimpleNamespace(content="直接回答。", tool_calls=[]))
    direct = execute_initial_agent_turn(
        llm=direct_llm,
        messages=pack.messages,
        surface=surface,
    )
    assert direct.mode == "direct_answer" and len(direct_llm.calls) == 1
    trace = AgentTrace("task-direct", "goal", "simple")
    record_final_answer_path_once(
        trace,
        0,
        FinalAnswerPathDecision(path="direct_answer", trigger="initial_agent_turn_direct_answer"),
    )
    assert trace.events[-1].data["path"] == "direct_answer"

    print("smoke_initial_agent_turn_wiring ok")


if __name__ == "__main__":
    main()
