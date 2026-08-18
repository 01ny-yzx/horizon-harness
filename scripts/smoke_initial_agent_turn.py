"""Offline parsing checks for the initial agent turn."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_agent_turn import capability_for_tool_spec, parse_initial_agent_turn
from core.tool_spec import ToolKind, ToolProvider, ToolRisk, ToolSpec
from tools.registry import get_unified_tool_specs


SURFACE = ("read_file", "read_document", "sandbox_exec", "fetch_url")


def _call(call_id: str, name: str, arguments: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(content: str = "", calls: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=calls or [])


def main() -> None:
    direct = parse_initial_agent_turn(_message("这是普通回答。"), surface_tool_names=SURFACE)
    assert direct.mode == "direct_answer"

    shell_call = _call("call_shell", "sandbox_exec", {"command": "echo 1"})
    shell = parse_initial_agent_turn(_message(calls=[shell_call]), surface_tool_names=SURFACE)
    assert shell.mode == "tool_calls"
    assert shell.tool_calls[0].id == "call_shell"

    fetch = parse_initial_agent_turn(
        _message(calls=[_call("call_fetch", "fetch_url", {"url": "ftp://example.com/file.txt"})]),
        surface_tool_names=SURFACE,
    )
    assert fetch.mode == "tool_calls"

    document = parse_initial_agent_turn(
        _message(calls=[_call("call_document", "read_document", {"path": "sample.xlsx"})]),
        surface_tool_names=SURFACE,
    )
    assert document.mode == "tool_calls"
    assert document.tool_calls[0].function.name == "read_document"

    assert parse_initial_agent_turn(_message(), surface_tool_names=SURFACE).mode == "terminal_failure"
    assert parse_initial_agent_turn(
        _message('<tool_call name="sandbox_exec">echo 1</tool_call>'),
        surface_tool_names=SURFACE,
    ).mode == "terminal_failure"
    assert parse_initial_agent_turn(
        _message(calls=[_call("call_unknown", "write_file", {"path": "x", "content": "y"})]),
        surface_tool_names=SURFACE,
    ).failure_reason == "tool_not_in_initial_surface"

    search_tool = ToolSpec(
        "web_search",
        ToolProvider.LOCAL,
        ToolKind.WEB_READ,
        capabilities=("web_search",),
        risk=ToolRisk.NETWORK,
    )
    workspace_status = ToolSpec(
        "get_workspace_status",
        ToolProvider.LOCAL,
        ToolKind.STATUS,
        risk=ToolRisk.READ_ONLY,
    )
    assert capability_for_tool_spec(search_tool, tool_name=search_tool.name) == "network"
    assert capability_for_tool_spec(workspace_status, tool_name=workspace_status.name) == "memory"
    specs = get_unified_tool_specs()
    print("smoke_initial_agent_turn ok")


if __name__ == "__main__":
    main()
