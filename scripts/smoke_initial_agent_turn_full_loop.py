"""Minimal full-loop smoke for Session context and structured ToolCall batches."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from typing import Any
from tempfile import TemporaryDirectory

_ENV_OVERRIDES = {
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "MCP_ENABLED": "false",
    "EMBEDDING_ENABLED": "false",
    "AGENT_ACCESS_MODE": "full_access",
}
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}
os.environ.update(_ENV_OVERRIDES)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop
from core.memory import Memory
from providers.mock import MockProvider, assistant_message
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace


_FIXTURE_ROOT: Path | None = None


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    effective = dict(arguments)
    if name == "read_file" and _FIXTURE_ROOT is not None and effective.get("path") and not Path(str(effective["path"])).is_absolute():
        effective["path"] = str((_FIXTURE_ROOT / str(effective["path"])).resolve())
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(effective, ensure_ascii=False)),
    )


def _loop(responses: list[Any], memory: Memory | None = None) -> tuple[AgentLoop, MockProvider, dict[str, Any]]:
    llm = MockProvider(responses=responses)
    loop = AgentLoop(llm, memory or Memory(), max_steps=4)
    captured: dict[str, Any] = {}

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    return loop, llm, captured


def _stages(llm: MockProvider) -> list[str]:
    return [str(getattr(call.get("options"), "stage", "") or "") for call in llm.calls]


def test_session_history() -> None:
    memory = Memory()
    memory.add_user_message("我喜欢草莓味。", task_id="old_task")
    memory.add_assistant_message("记住了。", task_id="old_task")
    loop, llm, captured = _loop([assistant_message("你喜欢草莓味。")], memory)
    assert loop.run("我刚才说我喜欢什么味？") == "你喜欢草莓味。"
    messages = llm.calls[0]["messages"]
    assert any(message.get("content") == "我喜欢草莓味。" for message in messages)
    assert any(message.get("content") == "记住了。" for message in messages)
    assert sum(message.get("content") == "我刚才说我喜欢什么味？" for message in messages) == 1
    assert any("Runtime model identity:" in str(message.get("content") or "") for message in messages)
    assert _stages(llm) == ["initial_agent_turn"]
    assert not any(event.event_type == "initial_agent_turn_failure" for event in captured["trace"].events)


def test_two_read_file_calls_use_generic_final() -> None:
    calls = [
        _call("call_a", "read_file", {"path": "core/trace.py"}),
        _call("call_b", "read_file", {"path": "core/memory.py"}),
    ]
    loop, llm, captured = _loop(
        [assistant_message("", calls), assistant_message("两个文件的结果已统一总结。")]
    )
    executed: list[str] = []

    def read_file(path: str) -> dict[str, Any]:
        executed.append(path)
        return {"success": True, "status": "success", "data": {"path": path, "content": f"content:{path}"}}

    loop.tools["read_file"] = read_file
    assert loop.run("读取两个文件并总结") == "两个文件的结果已统一总结。"
    assert [Path(path).name for path in executed] == ["trace.py", "memory.py"]
    assert _stages(llm) == ["initial_agent_turn", "agent_continuation"], _stages(llm)
    observations = captured["state"].metadata["completion_observations"]
    assert [item["call_id"] for item in observations] == ["call_a", "call_b"]
    contract = captured["state"].metadata["build_step_contract"]
    assert contract["all_steps_completed"] is True and contract["completed_count"] == 2
    assert contract["contract_role"] == "execution_batch"
    metadata = captured["state"].metadata
    assert metadata["initial_tool_batch_complete"] is True
    assert metadata["initial_tool_batch_completed_count"] == 2
    assert metadata["initial_tool_batch_failed_count"] == 0
    assert metadata["initial_tool_batch_blocked_count"] == 0
    assert metadata["agent_continuation_ready"] is True
    assert "write_file" in {
        schema["function"]["name"] for schema in llm.calls[1]["tools"]
    }
    events = captured["trace"].events
    initial_surface = next(
        event for event in events if event.event_type == "initial_tool_surface"
    )
    assert initial_surface.data["access_mode"] == "full_access"
    assert initial_surface.data["source"] == "registry_availability_permission"
    schema_scopes = [
        event for event in events if event.event_type == "tool_schema_scope"
    ]
    assert schema_scopes
    assert all(event.data["access_mode"] == "full_access" for event in schema_scopes)
    batch_events = [event for event in events if event.event_type == "initial_tool_batch_complete"]
    assert len(batch_events) == 1
    assert batch_events[0].data["call_ids"] == ["call_a", "call_b"]
    assert batch_events[0].data["tool_names"] == ["read_file", "read_file"]
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True
    assert not any(event.event_type == "terminal_responder_llm" for event in events)


def test_read_file_and_sandbox_exec_use_independent_grants() -> None:
    calls = [
        _call("call_read", "read_file", {"path": "core/runtime_metrics.py"}),
        _call("call_exec", "sandbox_exec", {"command": "python -c \"print('STEP5_MIXED_OK')\""}),
    ]
    loop, llm, captured = _loop([assistant_message("", calls), assistant_message("两个结果已总结。")])
    executed: list[str] = []
    loop.tools["read_file"] = lambda path: (
        executed.append("read_file")
        or {"success": True, "status": "success", "data": {"path": path, "content": "metrics"}}
    )
    loop.tools["sandbox_exec"] = lambda command, **_: (
        executed.append("sandbox_exec")
        or {"success": True, "status": "success", "data": {"command": command, "exit_code": 0, "stdout": "STEP5_MIXED_OK\n"}}
    )
    previous = os.environ.get("AGENT_ACCESS_MODE")
    os.environ["AGENT_ACCESS_MODE"] = "full_access"
    try:
        assert loop.run("读取指标文件并执行验证命令") == "两个结果已总结。"
    finally:
        if previous is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous
    assert executed == ["read_file", "sandbox_exec"]
    assert _stages(llm) == ["initial_agent_turn", "agent_continuation"]
    observations = captured["state"].metadata["completion_observations"]
    assert [item["call_id"] for item in observations] == ["call_read", "call_exec"]
    assert all(item.get("error_code") != "tool_execution_not_authorized" for item in observations)
    grants = captured["state"].metadata["tool_call_grant_ledger"]
    assert grants["call_read"]["status"] == "completed"
    assert grants["call_exec"]["status"] == "completed"
    events = captured["trace"].events
    assert sum(event.event_type == "tool_call_grant_registered" for event in events) == 2
    assert any(
        event.event_type == "tool_call_grant_checked"
        and event.data.get("call_id") == "call_exec"
        and event.data.get("allowed") is True
        for event in events
    )


def test_one_read_file_uses_agent_owned_continuation() -> None:
    loop, llm, captured = _loop(
        [
            assistant_message("", [_call("call_single", "read_file", {"path": "core/trace.py"})]),
            assistant_message("单文件摘要。"),
        ]
    )
    loop.tools["read_file"] = lambda path: {
        "success": True,
        "status": "success",
        "data": {"path": path, "content": "trace content"},
    }
    assert loop.run("读取单个文件并总结") == "单文件摘要。"
    assert _stages(llm) == ["initial_agent_turn", "agent_continuation"]
    assert "write_file" in {
        schema["function"]["name"] for schema in llm.calls[-1]["tools"]
    }
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True


def test_one_read_document_uses_generic_final() -> None:
    loop, llm, captured = _loop(
        [
            assistant_message("", [_call("call_document", "read_document", {"path": "sample.xlsx"})]),
            assistant_message("文档已解析并总结。"),
        ]
    )
    executed: list[str] = []
    loop.tools["read_document"] = lambda path, **_: (
        executed.append(path)
        or {
            "success": True,
            "status": "success",
            "metadata": {"path": str((_FIXTURE_ROOT / "sample.xlsx").resolve()) if _FIXTURE_ROOT else path},
            "data": {
                "path": path,
                "text": "document content",
                "tables": [{"name": "Sheet1", "preview_rows": [["A", "B"]]}],
            },
        }
    )
    assert loop.run("解析 sample.xlsx 并总结") == "文档已解析并总结。"
    assert executed == ["sample.xlsx"]
    assert _stages(llm) == ["initial_agent_turn", "agent_continuation"]
    observations = captured["state"].metadata["completion_observations"]
    assert len(observations) == 1 and observations[0]["call_id"] == "call_document"
    assert observations[0]["tool"] == "read_document"
    events = captured["trace"].events
    assert "write_file" in {
        schema["function"]["name"] for schema in llm.calls[-1]["tools"]
    }
    assert not any(event.event_type == "final_answer_path" and event.data.get("path") == "emergency_fallback" for event in events)


def main() -> None:
    global _FIXTURE_ROOT
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _FIXTURE_ROOT = root
            os.chdir(root)
            context = WorkspaceManager(root).get_context("smoke", "initial-full-loop")
            set_current_workspace(context)
            core_dir = root / "core"
            core_dir.mkdir()
            for name in ("trace.py", "memory.py", "runtime_metrics.py"):
                (core_dir / name).write_text(f"# isolated {name}\n", encoding="utf-8")
            (root / "sample.xlsx").write_bytes(b"document-smoke-fixture")
            test_session_history()
            test_two_read_file_calls_use_generic_final()
            test_read_file_and_sandbox_exec_use_independent_grants()
            test_one_read_file_uses_agent_owned_continuation()
            test_one_read_document_uses_generic_final()
    finally:
        _FIXTURE_ROOT = None
        os.chdir(original_cwd)
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_initial_agent_turn_full_loop ok")


if __name__ == "__main__":
    main()
