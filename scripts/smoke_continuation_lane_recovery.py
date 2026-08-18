"""Real AgentLoop smoke for continuation recovery across the initial runtime lane."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from dataclasses import replace
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
from core.state import TaskState
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(content: str = "", calls: list[Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=calls or [])


def _capture(loop: AgentLoop) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    return captured


def _continuation_schema_names(llm: MockProvider, call_index: int) -> list[str]:
    return [schema["function"]["name"] for schema in llm.calls[call_index]["tools"]]


def _assert_schema_and_boundary_match(captured: dict[str, Any], schema_names: list[str], tool_name: str) -> None:
    continuation_events = [
        event
        for event in captured["trace"].events
        if event.event_type == "agent_continuation_result"
        and int(event.data.get("parsed_tool_call_count") or 0) > 0
    ]
    assert continuation_events
    assert any(event.data["scoped_tool_names"] == schema_names for event in continuation_events)
    assert tool_name in schema_names
    assert not any(
        "tool_call_not_in_scoped_surface" in event.summary
        for event in captured["trace"].events
    )


def _read_file_then_load_document(root: Path) -> None:
    source = root / "load-source.txt"
    source.write_text("document body", encoding="utf-8")
    llm = MockProvider(
        responses=[
            _message("", [_call("read-before-load", "read_file", {"path": str(source)})]),
            _message(
                "",
                [_call("load-after-read", "load_document", {"path": str(source), "create_chunks": False})],
            ),
            _message("文档已经成功导入。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=5)
    captured = _capture(loop)
    executed: list[str] = []
    original_read = loop.tools["read_file"]

    def tracked_read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        executed.append("read_file")
        return original_read(*args, **kwargs)

    def tracked_load(path: str, create_chunks: bool = False) -> dict[str, Any]:
        del create_chunks
        executed.append("load_document")
        return {
            "success": True,
            "status": "success",
            "data": {
                "status": "success",
                "document_id": "doc-lane-recovery",
                "source_path": path,
                "document_stored": True,
                "store_status": {"added": True},
                "chunks_stored": True,
                "chunk_count": 0,
            },
        }

    loop.tools["read_file"] = tracked_read
    loop.tools["load_document"] = tracked_load
    original_execute = loop._execute_tool_envelope

    def execute_with_document_load_requirement(
        self: AgentLoop, state: TaskState, envelope: Any, **kwargs: Any
    ) -> Any:
        state.metadata["required_capabilities"] = ["file_read", "document_load"]
        state.metadata["runtime_lane"] = "build"
        state.metadata["runtime_lane_hint"] = "build"
        state.metadata["runtime_lane_profile"] = "build"
        if state.task_profile is not None:
            state.task_profile = replace(
                state.task_profile,
                needs_document_load=True,
                side_effect_required=True,
                workflow_kind="build",
            )
        return original_execute(state, envelope, **kwargs)

    loop._execute_tool_envelope = MethodType(
        execute_with_document_load_requirement, loop
    )
    assert loop.run("导入该文档") == "文档已经成功导入。"
    assert executed == ["read_file", "load_document"], (
        executed,
        [str(call["options"].stage) for call in llm.calls],
        [_continuation_schema_names(llm, index) for index in range(1, len(llm.calls))],
    )
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    continuation_names = _continuation_schema_names(llm, 1)
    assert {"load_document", "load_documents_from_directory"} <= set(continuation_names)
    _assert_schema_and_boundary_match(captured, continuation_names, "load_document")
    assert captured["state"].document_loaded is True
    assert captured["state"].metadata.get("finalization_tools_disabled") is not True
    assert captured["state"].metadata.get("finalization_mode") not in {
        "document_load_observation_complete",
        "capability_observation_complete",
    }
    assert "write_file" in _continuation_schema_names(llm, 2)
    assert "load-after-read" in captured["state"].metadata["tool_call_grant_ledger"]


def _read_file_then_write_file(root: Path) -> None:
    source = root / "write-source.txt"
    target = root / "created-by-continuation.txt"
    source.write_text("source text", encoding="utf-8")
    llm = MockProvider(
        responses=[
            _message("", [_call("read-before-write", "read_file", {"path": str(source)})]),
            _message(
                "",
                [
                    _call(
                        "write-after-read",
                        "write_file",
                        {"path": str(target), "content": "created", "overwrite": True},
                    )
                ],
            ),
            _message("输出文件已经创建。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=5)
    captured = _capture(loop)
    executed: list[str] = []
    original_read = loop.tools["read_file"]
    original_write = loop.tools["write_file"]

    def tracked_read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        executed.append("read_file")
        return original_read(*args, **kwargs)

    def tracked_write(*args: Any, **kwargs: Any) -> dict[str, Any]:
        executed.append("write_file")
        return original_write(*args, **kwargs)

    loop.tools["read_file"] = tracked_read
    loop.tools["write_file"] = tracked_write
    original_execute = loop._execute_tool_envelope

    def execute_with_file_write_requirement(
        self: AgentLoop, state: TaskState, envelope: Any, **kwargs: Any
    ) -> Any:
        state.metadata["required_capabilities"] = ["file_read", "file_write"]
        state.metadata["runtime_lane"] = "build"
        state.metadata["runtime_lane_hint"] = "build"
        state.metadata["runtime_lane_profile"] = "build"
        if state.task_profile is not None:
            state.task_profile = replace(
                state.task_profile,
                side_effect_required=True,
                workflow_kind="build",
            )
        return original_execute(state, envelope, **kwargs)

    loop._execute_tool_envelope = MethodType(
        execute_with_file_write_requirement, loop
    )
    assert loop.run("读取源文件并生成输出文件") == "输出文件已经创建。"
    assert executed == ["read_file", "write_file"]
    assert target.read_text(encoding="utf-8") == "created"
    continuation_names = _continuation_schema_names(llm, 1)
    _assert_schema_and_boundary_match(captured, continuation_names, "write_file")
    assert "write-after-read" in captured["state"].metadata["tool_call_grant_ledger"]


def _single_file_summary_trace_and_metrics(root: Path) -> None:
    source = root / "summary.txt"
    source.write_text("summary body", encoding="utf-8")
    llm = MockProvider(
        responses=[
            _message("", [_call("read-summary", "read_file", {"path": str(source)})]),
            _message("文件内容是 summary body。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=4)
    captured = _capture(loop)
    assert loop.run("读取并总结文件") == "文件内容是 summary body。"
    assert len(llm.calls) == 2
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
    ]
    assert _continuation_schema_names(llm, 0) == _continuation_schema_names(llm, 1)
    assert not any(
        str(call["options"].stage) == "final_answer" for call in llm.calls
    )
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "lane-recovery"))
            _read_file_then_load_document(root)
            _read_file_then_write_file(root)
            _single_file_summary_trace_and_metrics(root)
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_continuation_lane_recovery: PASS")


if __name__ == "__main__":
    main()
