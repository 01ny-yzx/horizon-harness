"""Unified Agent Continuation and document_load contract smoke."""

from __future__ import annotations

import json
import os
from dataclasses import replace
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

from core.final_observation_context import build_final_observation_context
from core.agent_prose_validation import validate_agent_prose_candidate
from core.initial_agent_turn import capability_for_tool_spec
from core.initial_tool_surface import build_initial_tool_surface
from core.loop import AgentLoop
from core.memory import Memory
from core.observation_compaction import compact_tool_result_for_model
from core.prompt_pack import build_agent_continuation_pack
from core.runtime_lane import RuntimeLane, resolve_runtime_lane
from core.state import PlanStep, TaskState
from core.tool_completion import record_completion_observation
from core.tool_outcome_resolution import ToolOutcomeResolution, _attempted_tools
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider
from tools import document_tools
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(content: str = "", calls: list[Any] | None = None, finish_reason: str = "") -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=calls or [], finish_reason=finish_reason)


def _state(primary_capability: str = "document_load", primary_tool: str = "") -> TaskState:
    state = TaskState.create("load document", "document", [PlanStep(1, "load", "load")])
    state.metadata.update(
        {
            "runtime_lane": "explore",
            "primary_capability": primary_capability,
            "tool_plan": {
                "primary_capability": primary_capability,
                "primary_tool": primary_tool,
                "required_capabilities": [primary_capability],
            },
        }
    )
    return state


def _run_continuation_flow(root: Path) -> None:
    llm = MockProvider(
        responses=[
            _message("", [_call("tree", "get_project_tree", {})], finish_reason="stop"),
            _message("项目结构已经检查完成。", finish_reason="tool_calls"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=4)
    captured: dict[str, Any] = {}
    executed: list[str] = []
    loop.tools["get_project_tree"] = lambda **_: (
        executed.append("get_project_tree")
        or {"success": True, "status": "success", "data": {"tree": ["a.py"]}}
    )

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(
            trace=trace,
            state=state,
            answer=answer,
            metrics=self._runtime_metrics,
        )
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    answer = loop.run("检查项目结构后直接告诉我结果")
    assert answer == "项目结构已经检查完成。"
    assert executed == ["get_project_tree"]
    stages = [str(getattr(call["options"], "stage", "")) for call in llm.calls]
    assert stages == ["initial_agent_turn", "agent_continuation"]
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True
    assert not any(event.event_type == "terminal_responder_llm" for event in captured["trace"].events)
    result_events = [event.data for event in captured["trace"].events if event.event_type == "agent_continuation_result"]
    assert any(item.get("parsed_tool_call_count") == 1 for item in result_events), result_events
    assert any(item.get("agent_continuation_output_type") == "prose" for item in result_events)
    assert all(text not in answer for text in ("操作未成功完成", "未能真实执行所需工具调用", "已完成"))


def _run_raw_repair_flow() -> None:
    llm = MockProvider(
        responses=[
            _message("", [_call("tree-raw", "get_project_tree", {})]),
            _message('<tool_call name="get_project_tree">{}</tool_call>'),
            _message("已根据真实观察完成说明。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=4)
    captured: dict[str, Any] = {}
    loop.tools["get_project_tree"] = lambda **_: {
        "success": True,
        "status": "success",
        "data": {"tree": ["a.py"]},
    }

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    assert loop.run("检查项目") == "已根据真实观察完成说明。"
    metadata = captured["state"].metadata
    assert metadata["raw_tool_rejection_count"] == 1
    assert metadata["output_violation_recovered"] is True
    assert metadata.get("active_output_violation") is not True
    state = _state()
    raw = validate_agent_prose_candidate(
        '<tool_call name="read_file">{"path":"a.txt"}</tool_call>'
    )
    assert raw.accepted is False and raw.raw_tool_text is True
    assert loop._handle_invalid_agent_prose_candidate(state, validation=raw) is False
    assert loop._handle_invalid_agent_prose_candidate(state, validation=raw) is True
    assert state.metadata["raw_tool_rejection_count"] == 2


def _run_document_load_recovery(root: Path) -> None:
    fixture = root / "recovery.xlsx"
    fixture.write_bytes(b"fixture")
    llm = MockProvider(
        responses=[
            _message("", [_call("read-wrong", "read_document", {"path": str(fixture)})]),
            _message("", [_call("load-right", "load_document", {"path": str(fixture), "create_chunks": False})]),
            _message("文档已经成功导入，可以继续使用其存储记录。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=5)
    captured: dict[str, Any] = {}
    executed: list[str] = []
    loop.tools["read_document"] = lambda path: (
        executed.append("read_document")
        or {"success": True, "status": "success", "data": {"path": path, "text": "preview"}}
    )
    loop.tools["load_document"] = lambda path, create_chunks=False: (
        executed.append("load_document")
        or {
            "success": True,
            "status": "success",
            "data": {
                "status": "success",
                "path": path,
                "source_path": path,
                "document_id": "doc-recovery",
                "store_status": {"added": True},
                "document_stored": True,
                "chunks_stored": True,
                "chunk_count": 0,
            },
        }
    )
    original_execute = loop._execute_tool_envelope

    def execute_with_document_load_requirement(
        self: AgentLoop, state: TaskState, envelope: Any, **kwargs: Any
    ) -> Any:
        state.metadata["required_capabilities"] = ["document_load"]
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

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    answer = loop.run("导入指定文档")
    assert answer == "文档已经成功导入，可以继续使用其存储记录。"
    assert executed == ["read_document", "load_document"]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    continuation_names = {
        schema["function"]["name"] for schema in llm.calls[1]["tools"]
    }
    assert {"load_document", "load_documents_from_directory"} <= continuation_names
    assert not any(
        "tool_call_not_in_scoped_surface" in event.summary
        for event in captured["trace"].events
    )
    assert captured["state"].document_loaded is True
    assert _attempted_tools(captured["state"]) == {"read_document", "load_document"}


def _run_initial_tool_batch_then_document_load(root: Path) -> None:
    fixture = root / "initial-batch.xlsx"
    fixture.write_bytes(b"fixture")
    llm = MockProvider(
        responses=[
            _message(
                "",
                [
                    _call("batch-read", "read_document", {"path": str(fixture)}),
                    _call("batch-status", "get_context_status", {}),
                ],
            ),
            _message(
                "",
                [_call("continuation-load", "load_document", {"path": str(fixture), "create_chunks": False})],
            ),
            _message("XLSX 已成功导入 Horizon 文档库。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=5)
    captured: dict[str, Any] = {}
    executed: list[str] = []
    loop.tools["read_document"] = lambda path, **_: (
        executed.append("read_document")
        or {"success": True, "status": "success", "data": {"path": path, "text": "preview"}}
    )
    loop.tools["get_context_status"] = lambda: (
        executed.append("get_context_status")
        or {"success": True, "status": "success", "data": {"available": True}}
    )
    loop.tools["load_document"] = lambda path, create_chunks=False: (
        executed.append("load_document")
        or {
            "success": True,
            "status": "success",
            "data": {
                "status": "success",
                "path": path,
                "source_path": path,
                "document_id": "doc-initial-batch",
                "store_status": {"added": True},
                "document_stored": True,
                "chunks_stored": True,
                "chunk_count": 0,
            },
        }
    )
    original_execute = loop._execute_tool_envelope

    def execute_with_document_load_requirement(
        self: AgentLoop, state: TaskState, envelope: Any, **kwargs: Any
    ) -> Any:
        state.metadata["required_capabilities"] = ["document_load"]
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

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(
            trace=trace,
            state=state,
            answer=answer,
            metrics=self._runtime_metrics,
        )
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    answer = loop.run("将这个 XLSX 导入 Horizon 文档库")
    assert answer == "XLSX 已成功导入 Horizon 文档库。"
    assert executed == ["read_document", "get_context_status", "load_document"], (
        executed,
        [
            [schema["function"]["name"] for schema in call["tools"]]
            for call in llm.calls
        ],
    )
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    continuation_names = {schema["function"]["name"] for schema in llm.calls[1]["tools"]}
    assert "load_document" in continuation_names
    state = captured["state"]
    observations = state.metadata["completion_observations"]
    assert [item["call_id"] for item in observations[:2]] == ["batch-read", "batch-status"]
    assert state.metadata["initial_tool_batch_complete"] is True
    assert state.metadata.get("finalization_tools_disabled") is not True
    assert state.metadata.get("finalization_mode") != "build_step_contract_resolved"
    assert "write_file" in {
        schema["function"]["name"] for schema in llm.calls[-1]["tools"]
    }
    assert state.document_loaded is True
    events = captured["trace"].events
    assert sum(event.event_type == "initial_tool_batch_complete" for event in events) == 1
    assert not any(
        event.event_type in {
            "build_step_contract_repeat_tool_warning",
            "build_step_contract_unexpected_tool_warning",
        }
        and event.tool_name == "load_document"
        for event in events
    )
    assert not any("tool_call_not_in_scoped_surface" in event.summary for event in events)
    assert not any(
        event.event_type in {"emergency_finalization_fallback", "emergency_finalization_started"}
        for event in events
    )
    assert state.metadata["direct_agent_prose_adopted"] is True
    metrics = captured["metrics"].summary()
    assert metrics["tool_call_count"] == 3
    assert metrics["real_tool_execution_count"] == 3
    assert metrics["exact_tool_loop_stop_count"] == 0


def _run_single_file_continuation_contract(root: Path) -> None:
    fixture = root / "single.txt"
    fixture.write_text("plain text", encoding="utf-8")
    llm = MockProvider(
        responses=[
            _message("", [_call("read-text", "read_file", {"path": str(fixture)})]),
            _message("已根据结构化读取结果给出说明。"),
        ]
    )
    loop = AgentLoop(llm, Memory(), max_steps=5)
    captured: dict[str, Any] = {}
    executed: list[str] = []
    loop.tools["read_file"] = lambda path: (
        executed.append("read_file")
        or {"success": True, "status": "success", "data": "plain text", "metadata": {"path": path}}
    )
    loop.tools["read_document"] = lambda path: (
        executed.append("read_document")
        or {"success": True, "status": "success", "data": {"path": path, "text": "plain text"}}
    )

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    assert loop.run("读取并说明文件") == "已根据结构化读取结果给出说明。"
    assert executed == ["read_file"]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
    ]
    assert "write_file" in {
        schema["function"]["name"] for schema in llm.calls[1]["tools"]
    }
    summary_llm = MockProvider(
        responses=[
            _message("", [_call("read-summary", "read_file", {"path": str(fixture)})]),
            _message("文件内容是 plain text。"),
        ]
    )
    summary_loop = AgentLoop(summary_llm, Memory(), max_steps=4)
    summary_captured: dict[str, Any] = {}
    summary_loop.tools["read_file"] = lambda path: {
        "success": True,
        "status": "success",
        "data": "plain text",
        "metadata": {"path": path},
    }

    def summary_finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        summary_captured.update(trace=trace, state=state, answer=answer)
        return answer

    summary_loop._finish_with_trace = MethodType(summary_finish, summary_loop)
    assert summary_loop.run("读取并总结文件") == "文件内容是 plain text。"
    assert len(summary_llm.calls) == 2
    assert [
        schema["function"]["name"] for schema in summary_llm.calls[0]["tools"]
    ] == [
        schema["function"]["name"] for schema in summary_llm.calls[1]["tools"]
    ]
    assert not any(
        str(call["options"].stage) == "final_answer"
        for call in summary_llm.calls
    )


def _lane_and_attempted_tool_contract() -> None:
    state = _state("document_load", "")
    state.metadata["required_capabilities"] = ["document_load"]
    state.metadata["tool_plan"]["capabilities"] = ["document_load"]
    assert resolve_runtime_lane(state).lane is RuntimeLane.BUILD
    state.document_loaded = True
    state.metadata["successful_tools"] = ["load_document"]
    assert _attempted_tools(state) == {"load_document"}


def _final_responder_raw_output_contract() -> None:
    llm = MockProvider(
        responses=[_message('<tool_call name="read_file">{"path":"x"}</tool_call>')]
    )
    loop = AgentLoop(llm, Memory(), max_steps=2)
    state = _state("file_read", "read_file")
    observation = {
        "tool": "read_file",
        "success": False,
        "status": "failed",
        "error": "file missing",
        "error_code": "file_not_found",
        "data": {"status": "failed", "error": "file missing", "error_code": "file_not_found"},
    }
    record_completion_observation(state, "read_file", observation)
    trace = SimpleNamespace(events=[])

    def add_event(
        step: int,
        event_type: str,
        summary: str,
        tool_name: str = "",
        success: bool = True,
        data: dict[str, Any] | None = None,
    ) -> None:
        trace.events.append(
            SimpleNamespace(
                step=step,
                event_type=event_type,
                summary=summary,
                tool_name=tool_name,
                success=success,
                data=data or {},
            )
        )

    trace.add_event = add_event
    outcome = ToolOutcomeResolution(
        "terminal_failure",
        "tool_failed_no_fallback",
        tool="read_file",
        status="failed",
        metadata={"observation": observation},
    )
    answer = loop._build_final_answer_from_terminal_outcome(
        state,
        outcome,
        trace,
        1,
    )
    assert "<tool_call" not in answer
    assert len(llm.calls) == 1
    assert any(event.event_type == "emergency_finalization_started" for event in trace.events)
    assert any(
        event.event_type == "terminal_responder_validation"
        and "raw_tool_text_in_final_responder" in event.summary
        for event in trace.events
    )


class _FakeStore:
    def __init__(self, *, fail_chunks: bool = False, fail_document_at: int = 0) -> None:
        self.fail_chunks = fail_chunks
        self.fail_document_at = fail_document_at
        self.document_calls = 0

    def add_document(self, record: Any) -> dict[str, Any]:
        self.document_calls += 1
        if self.fail_document_at == self.document_calls:
            return {"success": False, "error": "store failed"}
        return {
            "success": True,
            "data": {
                "document": {"document_id": record.document_id},
                "added": True,
                "updated": False,
                "reason": "stored",
            },
        }

    def add_chunks(self, document_id: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        if self.fail_chunks:
            return {"success": False, "error": "chunks failed"}
        return {"success": True, "data": {"document_id": document_id, "count": len(chunks)}}


def _document_contract(root: Path) -> None:
    specs = get_unified_tool_specs()
    schemas = get_unified_tool_schemas()
    full = build_initial_tool_surface(specs, schemas, access_mode="full_access")
    read_only = build_initial_tool_surface(specs, schemas, access_mode="read_only")
    assert {"load_document", "load_documents_from_directory"} <= set(full.tool_names)
    assert {"load_document", "load_documents_from_directory"}.isdisjoint(read_only.tool_names)
    assert capability_for_tool_spec(specs["load_document"], tool_name="load_document") == "document_load"
    for name in ("load_document", "load_documents_from_directory", "rebuild_chunks_for_document"):
        spec = specs[name]
        assert spec.reads_files is True
        assert spec.side_effect is True
        assert spec.risk == "internal_state"
        assert {"document_load", "state_mutation"} <= set(spec.capabilities)
        assert "file_read" not in spec.capabilities
    assert specs["read_document"].side_effect is False

    complete_observation = {
        "tool": "load_document",
        "success": True,
        "status": "success",
        "data": {
            "status": "success",
            "document_id": "doc-1",
            "source_path": str(root / "doc.txt"),
            "store_status": {"added": True},
            "document_stored": True,
            "chunks_stored": True,
            "chunk_count": 0,
        },
    }
    original_store = document_tools._store
    try:
        source = root / "partial.txt"
        source.write_text("partial document", encoding="utf-8")
        document_tools._store = lambda: _FakeStore(fail_chunks=True)
        partial = document_tools.load_document(str(source))
        assert partial["success"] is False and partial["status"] == "partial"
        assert partial["data"]["document_stored"] is True
        assert partial["data"]["chunks_stored"] is False
        assert "chunk_count" in partial["data"]
        partial_state = _state("document_load", "load_document")
        partial_state.record_document_load_result("load_document", partial["data"], success=False)
        assert partial_state.document_loaded is False
        assert partial_state.document_load_status == "partial"

        batch_dir = root / "batch"
        batch_dir.mkdir()
        (batch_dir / "a.txt").write_text("a", encoding="utf-8")
        (batch_dir / "b.txt").write_text("b", encoding="utf-8")
        document_tools._store = lambda: _FakeStore(fail_document_at=2)
        batch = document_tools.load_documents_from_directory(str(batch_dir))
        assert batch["status"] == "partial"
        assert batch["data"]["loaded_count"] == 1
        assert batch["data"]["failed_count"] == 1
        assert len(batch["data"]["failures"]) == 1
        batch_state = _state("document_load", "load_documents_from_directory")
        batch_state.record_document_load_result(
            "load_documents_from_directory", batch["data"], success=False
        )
        assert batch_state.document_loaded is False
        assert batch_state.loaded_count == 1 and batch_state.failed_count == 1
    finally:
        document_tools._store = original_store

    state = _state("document_load", "load_document")
    record_completion_observation(state, "load_document", complete_observation)
    state.record_document_load_result("load_document", complete_observation["data"], success=True)
    assert state.document_loaded is True and state.document_id == "doc-1"
    assert state.chunk_count == 0
    compacted = compact_tool_result_for_model(
        "load_document",
        complete_observation["data"],
        success=True,
        payload=complete_observation,
    )
    visible = compacted["model_visible_summary"]
    for key in ("document_id", "store_status", "document_stored", "chunks_stored", "chunk_count"):
        assert key in visible
    final_context = build_final_observation_context(state, None)
    load_context = next(
        item for item in final_context if item.get("base_tool") == "load_document"
    )
    assert load_context["data_summary"]["document_id"] == "doc-1"
    assert load_context["data_summary"]["store_status"] == {"added": True}
    assert load_context["data_summary"]["document_stored"] is True
    assert load_context["data_summary"]["chunks_stored"] is True
    assert load_context["data_summary"]["chunk_count"] == 0


def _prompt_contract() -> None:
    pack = build_agent_continuation_pack(
        user_input="continue",
        task_state=_state(),
        tools=[],
        memory_messages=[],
    )
    text = "\n".join(str(message.get("content") or "") for message in pack.messages)
    assert "ToolObservations" in text and "Return normal user-facing prose" in text
    for forbidden in ("Do not write final prose", "If no tool is needed, return no tool call"):
        assert forbidden not in text


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(WorkspaceManager(root / "workspace").get_context("smoke", "continuation"))
            _prompt_contract()
            _run_continuation_flow(root)
            _run_raw_repair_flow()
            _run_document_load_recovery(root)
            _run_initial_tool_batch_then_document_load(root)
            _run_single_file_continuation_contract(root)
            _lane_and_attempted_tool_contract()
            _final_responder_raw_output_contract()
            _document_contract(root)
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_unified_agent_continuation_document_load: PASS")


if __name__ == "__main__":
    main()
