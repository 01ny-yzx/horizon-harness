"""Offline full-loop closure for model-visible Agent prompt authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

_ENV = {
    "AGENT_ACCESS_MODE": "full_access",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "MCP_ENABLED": "false",
    "EMBEDDING_ENABLED": "false",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
}
_OLD_ENV = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.initial_agent_turn import parse_initial_agent_turn
from core.initial_tool_surface import build_initial_tool_surface
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from scripts.smoke_tool_result_llm_loop_reset import (
    _call,
    _current_permission_names,
    _failed,
    _message,
    _run,
    _schema_names,
)
from tools.file_tools import read_file as real_read_file
from tools.registry import get_local_tool_schemas, get_local_tool_specs
import tools.registry as registry_module


FORBIDDEN_AUTHORITY = (
    "ToolPlan",
    "RuntimePlan",
    "TaskState",
    "TaskProfile",
    "current_phase",
    "primary tool",
    "primary capability",
    "current plan step",
    "candidate_tool_names",
)


def _render_call(call: dict[str, Any]) -> str:
    return json.dumps(call["messages"], ensure_ascii=False)


def _assert_prompt_closed(call: dict[str, Any]) -> None:
    rendered = _render_call(call).lower()
    for marker in FORBIDDEN_AUTHORITY:
        assert marker.lower() not in rendered, marker


def test_initial_context_and_keyword_neutrality() -> None:
    prose = "Capability routing is part of the Runtime design, and this is how the tool flow works."
    answer, llm, captured, executed = _run(
        [_message(prose)],
        tools={},
        request="解释这段设计",
    )
    assert answer == prose
    assert not executed
    assert len(llm.calls) == 1
    assert _schema_names(llm.calls[0]["tools"]) == _current_permission_names()
    _assert_prompt_closed(llm.calls[0])
    assert not any(
        event.event_type == "initial_agent_turn_retry"
        for event in captured["trace"].events
    )


def test_successful_read_continuation_has_observation_without_finish_note(root: Path) -> None:
    target = root / "read.txt"
    target.write_text("visible read result", encoding="utf-8")
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("read-success", "read_file", {"path": str(target)})]),
            _message("已根据读取结果回答。"),
        ],
        tools={"read_file": real_read_file},
        request="读取文件并说明",
    )
    assert answer == "已根据读取结果回答。"
    assert [name for name, _ in executed] == ["read_file"]
    assert len(llm.calls) == 2
    continuation = llm.calls[1]
    _assert_prompt_closed(continuation)
    rendered = _render_call(continuation)
    assert "read-success" in rendered
    assert "visible read result" in rendered
    for marker in (
        "provide the final summary",
        "do not call more tools",
        "the requested file has already been successfully read",
        "tools are disabled for this pass",
    ):
        assert marker not in rendered.lower(), marker
    assert continuation["tools"]
    assert not any(
        event.event_type in {"isolated_finalization_pack", "normal_finalization"}
        for event in captured["trace"].events
    )


def test_recoverable_failure_is_fact_only() -> None:
    answer, llm, _, executed = _run(
        [
            _message("", [_call("recoverable", "read_file", {"path": "missing.txt"})]),
            _message("读取失败，我会如实说明。"),
        ],
        tools={"read_file": lambda **_: _failed("temporary_failure", recoverable=True)},
        request="读取文件",
    )
    assert answer == "读取失败，我会如实说明。"
    assert [name for name, _ in executed] == ["read_file"]
    continuation = llm.calls[1]
    rendered = _render_call(continuation).lower()
    assert "temporary_failure" in rendered
    _assert_prompt_closed(continuation)
    for marker in (
        "recovery candidate",
        "fallback tool",
        "use fetch_url as",
        "use web_search as",
        "recovery phase",
    ):
        assert marker not in rendered, marker


def test_unavailable_search_changes_schema_without_fallback_prompt() -> None:
    original = registry_module.get_web_search_provider_status
    registry_module.get_web_search_provider_status = lambda: SimpleNamespace(search_available=False)
    try:
        answer, llm, _, _ = _run(
            [_message("当前搜索工具不可用。")],
            tools={},
            request="搜索当前信息",
        )
    finally:
        registry_module.get_web_search_provider_status = original
    assert answer
    assert "web_search" not in _schema_names(llm.calls[0]["tools"])
    assert "fetch_url" in _schema_names(llm.calls[0]["tools"])
    rendered = _render_call(llm.calls[0]).lower()
    assert "use fetch_url as" not in rendered
    assert "fallback to fetch_url" not in rendered


def test_raw_tool_markup_remains_protocol_failure() -> None:
    schemas = get_local_tool_schemas()
    specs = get_local_tool_specs()
    surface = build_initial_tool_surface(specs, schemas, access_mode="full_access")
    result = parse_initial_agent_turn(
        _message('<tool_call>{"name":"read_file","arguments":{"path":"x"}}</tool_call>'),
        surface_tool_names=surface.tool_names,
    )
    assert result.mode == "terminal_failure"
    assert result.failure_reason == "raw_tool_markup_without_structured_calls"
    assert not result.tool_calls

    answer, llm, _, executed = _run(
        [
            _message('<tool_call>{"name":"read_file","arguments":{"path":"x"}}</tool_call>'),
            _message("这只是文本中的伪工具标记，不会被执行。"),
        ],
        tools={"read_file": lambda **_: {"success": True, "data": {"content": "wrong"}}},
        request="解释这段伪工具标记",
    )
    assert answer == "这只是文本中的伪工具标记，不会被执行。"
    assert not executed
    assert len(llm.calls) == 2


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(
                WorkspaceManager(root).get_context("smoke", "agent-loop-prompt-authority")
            )
            test_initial_context_and_keyword_neutrality()
            test_successful_read_continuation_has_observation_without_finish_note(root)
            test_recoverable_failure_is_fact_only()
            test_unavailable_search_changes_schema_without_fallback_prompt()
            test_raw_tool_markup_remains_protocol_failure()
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_agent_loop_prompt_authority_closure ok")


if __name__ == "__main__":
    main()
