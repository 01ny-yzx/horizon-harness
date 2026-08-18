"""Full-loop smoke coverage for tool-error continuation semantics."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

from core.loop import AgentLoop
from core.memory import Memory
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider, assistant_message
from tools.file_tools import read_document as real_read_document
from tools.file_tools import read_file as real_read_file


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(
    content: str = "",
    calls: list[Any] | None = None,
    *,
    finish_reason: str = "",
) -> SimpleNamespace:
    message = assistant_message(content, calls)
    if finish_reason:
        message.finish_reason = finish_reason
    return message


def _run(
    responses: list[Any],
    *,
    tools: dict[str, Any],
    request: str,
    max_steps: int = 6,
    tool_plan: dict[str, Any] | None = None,
    memory: Memory | None = None,
    state_projection: Any | None = None,
) -> tuple[str, MockProvider, dict[str, Any], list[str]]:
    llm = MockProvider(responses=responses)
    loop = AgentLoop(llm, memory or Memory(), max_steps=max_steps)
    captured: dict[str, Any] = {}
    executed: list[str] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(
            trace=trace,
            state=state,
            answer=answer,
            metrics=self._runtime_metrics,
        )
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    for name, implementation in tools.items():
        def counted(*args: Any, __name: str = name, __impl: Any = implementation, **kwargs: Any) -> Any:
            executed.append(__name)
            return __impl(*args, **kwargs)

        loop.tools[name] = counted
    if tool_plan or state_projection is not None:
        original_execute = loop._execute_tool_envelope

        def execute_with_plan(self: AgentLoop, state: Any, envelope: Any, **kwargs: Any) -> Any:
            if tool_plan:
                state.metadata["tool_plan"] = dict(tool_plan)
                context = getattr(state, "intent_runtime_context", None)
                if context is not None and isinstance(getattr(context, "tool_plan", None), dict):
                    context.tool_plan.clear()
                    context.tool_plan.update(tool_plan)
            if state_projection is not None:
                state_projection(state)
            return original_execute(state, envelope, **kwargs)

        loop._execute_tool_envelope = MethodType(execute_with_plan, loop)
    answer = loop.run(request)
    return answer, llm, captured, executed


def _failed(error_code: str, **data: Any) -> dict[str, Any]:
    return {
        "success": False,
        "status": "failed",
        "error": error_code,
        "error_code": error_code,
        "data": {"error_code": error_code, **data},
    }


def _assert_natural_failure(
    *,
    answer: str,
    llm: MockProvider,
    captured: dict[str, Any],
    executed: list[str],
    tool: str,
    expected_answer: str,
) -> None:
    assert executed == [tool]
    assert answer == expected_answer
    stages = [str(call["options"].stage) for call in llm.calls]
    assert stages == [
        "initial_agent_turn",
        "agent_continuation",
    ], stages
    events = captured["trace"].events
    disposition = next(
        event.data for event in events if event.event_type == "tool_failure_disposition"
    )
    assert disposition["disposition"] == "ordinary_failure"
    assert disposition["outcome_kind"] == "allow_continue"
    assert not any(str(call["options"].stage) == "final_answer" for call in llm.calls)
    continuation_names = {
        schema["function"]["name"] for schema in llm.calls[1]["tools"]
    }
    assert {"read_file", "read_document", "write_file"} <= continuation_names
    assert len(llm.calls) == 2
    assert len(executed) == 1
    assert any(
        message.get("role") == "tool"
        and message.get("name") == tool
        for message in llm.calls[1]["messages"]
    )
    event_types = {event.event_type for event in events}
    deprecated_event = "failure_observation_" + "fact_mismatch"
    assert deprecated_event not in event_types
    assert "max_steps_reached" not in event_types


def test_ordinary_file_and_document_failures(root: Path) -> None:
    missing = root / "missing.txt"
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("missing", "read_file", {"path": str(missing)})]),
            _message(
                f"无法读取 `{missing}`：工具返回 `file_not_found`。"
            ),
        ],
        tools={"read_file": lambda path, **_: _failed("file_not_found", path=path)},
        request="读取指定文件",
    )
    _assert_natural_failure(
        answer=answer,
        llm=llm,
        captured=captured,
        executed=executed,
        tool="read_file",
        expected_answer=f"无法读取 `{missing}`：工具返回 `file_not_found`。",
    )
    assert not {"list_files", "find_files", "sandbox_exec"} & set(executed)

    for suffix, code, prose in (
        (".xlsx", "document_xlsx_invalid_or_corrupt", "该 XLSX 已损坏，无法读取。"),
        (".xls", "document_legacy_format_unsupported", "暂不支持 .xls，请转换为 .xlsx。"),
    ):
        path = root / f"document{suffix}"
        path.write_bytes(b"broken")
        answer, llm, captured, executed = _run(
            [
                _message("", [_call(f"doc-{suffix}", "read_document", {"path": str(path)})]),
                _message(f"无法读取 `{path}`（{suffix}）：{prose}"),
            ],
            tools={"read_document": lambda path, _code=code, **_: _failed(_code, path=path)},
            request="读取指定文档",
        )
        _assert_natural_failure(
            answer=answer,
            llm=llm,
            captured=captured,
            executed=executed,
            tool="read_document",
            expected_answer=f"无法读取 `{path}`（{suffix}）：{prose}",
        )
        if suffix == ".xls":
            assert ".xlsx" in answer and ".excelx" not in answer


def test_recovery_choices(root: Path) -> None:
    binary = root / "table.xlsx"
    binary.write_bytes(b"\xff\xfe")
    text = root / "table.txt"
    text.write_text("recovered", encoding="utf-8")
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("read-text", "read_file", {"path": str(binary)})]),
            _message("", [_call("read-doc", "read_document", {"path": str(text)})]),
            _message("已改用文档读取工具完成。"),
        ],
        tools={"read_file": real_read_file, "read_document": real_read_document},
        request="读取表格资源",
    )
    assert answer == "已改用文档读取工具完成。"
    assert executed == ["read_file", "read_document"]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    continuation_names = {
        schema["function"]["name"] for schema in llm.calls[1]["tools"]
    }
    assert {"read_file", "read_document"} <= continuation_names
    assert "write_file" in {
        schema["function"]["name"] for schema in llm.calls[2]["tools"]
    }

    commands: list[str] = []

    def sandbox_exec(command: str, **_: Any) -> dict[str, Any]:
        commands.append(command)
        if command == "bad":
            return {
                **_failed("command_failed", command=command, exit_code=1),
                "recoverable": True,
                "recovery_reason": "command_execution_can_be_revised",
                "data": {
                    "command": command,
                    "exit_code": 1,
                    "recoverable": True,
                    "recovery_reason": "command_execution_can_be_revised",
                },
            }
        return {"success": True, "status": "success", "data": {"command": command, "exit_code": 0}}

    answer, command_llm, _, executed = _run(
        [
            _message("", [_call("cmd-1", "sandbox_exec", {"command": "bad"})]),
            _message("", [_call("cmd-2", "sandbox_exec", {"command": "fixed"})]),
            _message("修正命令后成功。"),
        ],
        tools={"sandbox_exec": sandbox_exec},
        request="执行并修正命令",
    )
    assert answer == "修正命令后成功。"
    assert executed == ["sandbox_exec", "sandbox_exec"]
    assert commands == ["bad", "fixed"]
    assert "write_file" in {
        schema["function"]["name"] for schema in command_llm.calls[-1]["tools"]
    }


def test_fallback_is_advisory(root: Path) -> None:
    path = root / "broken.xlsx"
    answer, llm, captured, executed = _run(
        [
            _message("", [_call("primary", "read_document", {"path": str(path)})]),
            _message("文档读取失败，当前没有可靠恢复方式。"),
        ],
        tools={
            "read_document": lambda path, **_: _failed(
                "document_read_failed", path=path
            ),
            "read_file": lambda path, **_: {"success": True, "data": {"path": path}},
            "sandbox_exec": lambda command, **_: {"success": True, "data": {"command": command}},
        },
        request="读取指定文档",
        tool_plan={
            "primary_tool": "read_document",
            "primary_capability": "file_read",
            "fallback_tool_priority": ["sandbox_exec", "read_file"],
        },
    )
    assert executed == ["read_document"]
    assert answer == "文档读取失败，当前没有可靠恢复方式。"
    assert [str(call["options"].stage) for call in llm.calls][-1] == "agent_continuation"


def test_same_turn_failure_batch(root: Path) -> None:
    missing = root / "same-turn-missing.txt"
    answer, llm, captured, executed = _run(
        [
            _message(
                "",
                [
                    _call("batch-fail", "read_file", {"path": str(missing)}),
                    _call("batch-status", "get_context_status", {}),
                ],
            ),
            _message("文件读取失败；上下文状态已获取。"),
        ],
        tools={
            "read_file": lambda path, **_: _failed("file_not_found", path=path),
            "get_context_status": lambda: {
                "success": True,
                "status": "success",
                "data": {"available": True},
            },
        },
        request="执行同轮工具批次",
    )
    assert answer
    assert executed == ["read_file", "get_context_status"]
    observations = captured["state"].metadata["completion_observations"]
    assert [item["call_id"] for item in observations] == ["batch-fail", "batch-status"]
    finish_stages = [str(call["options"].stage) for call in llm.calls]
    assert finish_stages == [
        "initial_agent_turn",
        "agent_continuation",
    ], finish_stages
    assert not any(
        event.event_type.startswith("definitive_tool_failure_")
        for event in captured["trace"].events
    )


def test_doom_loop_permission_boundary() -> None:
    calls = [
        _message(
            "",
            [
                _call(f"repeat-{index}", "sandbox_exec", {"command": "same"})
                for index in range(1, 4)
            ],
        ),
        _message("重复动作没有推进任务，已停止。"),
    ]
    answer, llm, captured, executed = _run(
        calls,
        tools={
            "sandbox_exec": lambda command, **_: _failed(
                "command_failed", command=command, exit_code=1
            )
        },
        request="重复执行同一命令",
    )
    assert answer
    assert executed == ["sandbox_exec", "sandbox_exec"]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "final_answer",
    ]
    permission = next(
        event.data
        for event in captured["trace"].events
        if event.event_type == "doom_loop_permission_decision"
    )
    assert permission["consecutive_count"] == 3
    assert permission["permission"] == "doom_loop"
    assert permission["action"] == "deny"
    assert not any(event.event_type == "exact_tool_call_loop_stop" for event in captured["trace"].events)
    assert not any(
        event.event_type == "max_steps_reached"
        for event in captured["trace"].events
    )


def test_finish_reason_is_not_authoritative(root: Path) -> None:
    answer, llm, _, executed = _run(
        [
            _message(
                "",
                [_call("finish-tool", "sandbox_exec", {"command": "true"})],
                finish_reason="stop",
            ),
            _message("命令已执行。", finish_reason="unknown"),
        ],
        tools={
            "sandbox_exec": lambda command, **_: {
                "success": True,
                "status": "success",
                "data": {"command": command, "exit_code": 0},
            }
        },
        request="执行命令",
    )
    assert answer == "命令已执行。"
    assert executed == ["sandbox_exec"]
    finish_reason_stages = [str(call["options"].stage) for call in llm.calls]
    assert finish_reason_stages == [
        "initial_agent_turn",
        "agent_continuation",
    ], finish_reason_stages


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(
                WorkspaceManager(root).get_context("smoke", "tool-error-continuation")
            )
            test_ordinary_file_and_document_failures(root)
            test_recovery_choices(root)
            test_fallback_is_advisory(root)
            test_same_turn_failure_batch(root)
            test_doom_loop_permission_boundary()
            test_finish_reason_is_not_authoritative(root)
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_tool_error_continuation ok")


if __name__ == "__main__":
    main()
