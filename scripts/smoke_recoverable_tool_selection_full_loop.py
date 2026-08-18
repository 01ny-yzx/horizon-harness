"""Full-loop smoke for LLM-owned choices after recoverable ToolObservations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
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
_PREVIOUS = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop
from core.memory import Memory
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider, assistant_message
from tools.file_tools import read_document as real_read_document
from tools.file_tools import read_file as real_read_file


def _call(
    call_id: str,
    name: str,
    arguments: dict[str, Any],
) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def _schema_names(call: dict[str, Any]) -> list[str]:
    return [
        str(schema.get("function", {}).get("name") or "")
        for schema in call.get("tools") or []
    ]


def _tool_message_ids(call: dict[str, Any]) -> list[str]:
    return [
        str(message.get("tool_call_id") or "")
        for message in call.get("messages") or []
        if message.get("role") == "tool"
    ]


def _assert_normal_continuation_surfaces(llm: MockProvider) -> None:
    surfaces = [_schema_names(call) for call in llm.calls[1:]]
    assert surfaces
    assert all(surface == surfaces[0] for surface in surfaces)
    assert {"read_file", "read_document", "write_file"} <= set(surfaces[0])
    assert all(call["tools"] for call in llm.calls[1:])


def test_recoverable_file_failure_returns_to_llm(
    binary: Path,
    valid: Path,
) -> None:
    request = "RECOVERABLE_CURRENT_REQUEST_ONCE"
    llm = MockProvider(
        responses=[
            assistant_message(
                "",
                [_call("call-read-file", "read_file", {"path": str(binary)})],
            ),
            assistant_message(
                "",
                [
                    _call(
                        "call-read-document",
                        "read_document",
                        {"path": str(valid)},
                    )
                ],
            ),
            assistant_message("已根据新的结构化工具结果完成总结。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=4)
    captured: dict[str, Any] = {}
    executed: list[tuple[str, str]] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    def counted_read_file(path: str, **kwargs: Any) -> dict[str, Any]:
        executed.append(("read_file", path))
        return real_read_file(path, **kwargs)

    def counted_read_document(path: str, **kwargs: Any) -> dict[str, Any]:
        executed.append(("read_document", path))
        return real_read_document(path, **kwargs)

    loop._finish_with_trace = MethodType(finish, loop)
    loop.tools["read_file"] = counted_read_file
    loop.tools["read_document"] = counted_read_document
    answer = loop.run(request)

    assert answer == "已根据新的结构化工具结果完成总结。"
    assert [name for name, _ in executed] == ["read_file", "read_document"]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    _assert_normal_continuation_surfaces(llm)
    assert _tool_message_ids(llm.calls[1]) == ["call-read-file"]
    assert _tool_message_ids(llm.calls[2]) == [
        "call-read-file",
        "call-read-document",
    ]
    first_observation = json.loads(
        next(
            message["content"]
            for message in llm.calls[1]["messages"]
            if message.get("tool_call_id") == "call-read-file"
        )
    )
    assert first_observation["success"] is False
    assert first_observation["recoverable"] is True
    assert first_observation["error_code"] == "tool_resource_incompatible"
    metadata = captured["state"].metadata
    assert metadata["last_recoverable_tool_failure"]["error_code"] == (
        "tool_resource_incompatible"
    )
    events = captured["trace"].events
    assert any(event.event_type == "recoverable_tool_observation" for event in events)
    assert not any(event.event_type == "forced_tool_call" for event in events)
    assert not any(
        event.event_type in {
            "emergency_finalization_fallback",
            "emergency_finalization_started",
            "terminal_responder_llm",
        }
        for event in events
    )


def test_recoverable_failure_does_not_interrupt_batch(
    binary: Path,
    valid: Path,
) -> None:
    llm = MockProvider(
        responses=[
            assistant_message(
                "",
                [
                    _call("batch-read-file", "read_file", {"path": str(binary)}),
                    _call(
                        "batch-read-document",
                        "read_document",
                        {"path": str(valid)},
                    ),
                ],
            ),
            assistant_message("同一批次的失败与成功结果均已收到。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=3)
    captured: dict[str, Any] = {}
    executed: list[str] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    def counted_read_file(path: str, **kwargs: Any) -> dict[str, Any]:
        executed.append("read_file")
        return real_read_file(path, **kwargs)

    def counted_read_document(path: str, **kwargs: Any) -> dict[str, Any]:
        executed.append("read_document")
        return real_read_document(path, **kwargs)

    loop._finish_with_trace = MethodType(finish, loop)
    loop.tools["read_file"] = counted_read_file
    loop.tools["read_document"] = counted_read_document
    answer = loop.run("执行同一轮工具批次并总结")

    assert answer == "同一批次的失败与成功结果均已收到。"
    assert executed == ["read_file", "read_document"]
    assert len(llm.calls) == 2
    assert _tool_message_ids(llm.calls[1]) == [
        "batch-read-file",
        "batch-read-document",
    ]
    _assert_normal_continuation_surfaces(llm)


def test_llm_revises_recoverable_command() -> None:
    llm = MockProvider(
        responses=[
            assistant_message(
                "",
                [_call("call-command-failed", "sandbox_exec", {"command": "exit 1"})],
            ),
            assistant_message(
                "",
                [
                    _call(
                        "call-command-fixed",
                        "sandbox_exec",
                        {"command": "printf RECOVERED"},
                    )
                ],
            ),
            assistant_message("命令修正后执行成功。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=4)
    captured: dict[str, Any] = {}
    executed: list[str] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    def sandbox_exec(command: str, **_: Any) -> dict[str, Any]:
        executed.append(command)
        if command == "exit 1":
            return {
                "success": False,
                "status": "failed",
                "error": "command exited non-zero",
                "error_code": "command_failed",
                "recoverable": True,
                "recovery_reason": "command_execution_can_be_revised",
                "data": {
                    "command": command,
                    "exit_code": 1,
                    "recoverable": True,
                    "recovery_reason": "command_execution_can_be_revised",
                },
            }
        return {
            "success": True,
            "status": "success",
            "data": {"command": command, "exit_code": 0, "stdout": "RECOVERED"},
        }

    loop._finish_with_trace = MethodType(finish, loop)
    loop.tools["sandbox_exec"] = sandbox_exec
    answer = loop.run("执行命令并在失败时修正参数")

    assert answer == "命令修正后执行成功。"
    assert executed == ["exit 1", "printf RECOVERED"]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    assert _tool_message_ids(llm.calls[1]) == ["call-command-failed"]
    assert _tool_message_ids(llm.calls[2]) == [
        "call-command-failed",
        "call-command-fixed",
    ]
    _assert_normal_continuation_surfaces(llm)
    dispositions = [
        event.data
        for event in captured["trace"].events
        if event.event_type == "tool_failure_disposition"
    ]
    assert dispositions[0]["disposition"] == "recoverable"
    assert dispositions[0]["outcome_kind"] == "allow_continue"


def main() -> None:
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(
                WorkspaceManager(root).get_context(
                    "smoke",
                    "recoverable-full-loop",
                )
            )
            binary = root / "resource"
            binary.write_bytes(b"\xff\xfe\xfa")
            valid = root / "valid.txt"
            valid.write_text("RECOVERED_DOCUMENT_CONTENT", encoding="utf-8")
            test_recoverable_file_failure_returns_to_llm(binary, valid)
            test_recoverable_failure_does_not_interrupt_batch(binary, valid)
            test_llm_revises_recoverable_command()
    finally:
        os.chdir(original_cwd)
        for key, value in _PREVIOUS.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_recoverable_tool_selection_full_loop ok")


if __name__ == "__main__":
    main()
