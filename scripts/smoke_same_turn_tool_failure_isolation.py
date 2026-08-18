"""Smoke checks for failure isolation within one structured ToolCall batch."""

from __future__ import annotations

import json
import os
from dataclasses import replace
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

from core.loop import AgentLoop  # noqa: E402
from core.memory import Memory  # noqa: E402
from providers.mock import MockProvider, assistant_message  # noqa: E402
from core.workspace import WorkspaceManager  # noqa: E402
from core.workspace_runtime import set_current_workspace  # noqa: E402


_FIXTURE_ROOT: Path | None = None


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    effective = dict(arguments)
    if name == "read_file" and _FIXTURE_ROOT is not None and effective.get("path") and not Path(str(effective["path"])).is_absolute():
        effective["path"] = str((_FIXTURE_ROOT / str(effective["path"])).resolve())
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(effective, ensure_ascii=False)),
    )


def _loop(calls: list[Any], *, max_failures: int = 3) -> tuple[AgentLoop, MockProvider, dict[str, Any], list[str]]:
    llm = MockProvider(responses=[assistant_message("", calls), assistant_message("已汇总本轮成功与失败结果。")])
    loop = AgentLoop(llm, Memory(), max_steps=4, max_consecutive_failures=max_failures)
    captured: dict[str, Any] = {}
    executed: list[str] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer)
        return answer

    def read_file(path: str) -> dict[str, Any]:
        executed.append(path)
        if "missing" in path:
            return {
                "success": False,
                "status": "failed",
                "error": f"file not found: {path}",
                "error_code": "file_not_found",
                "data": {"path": path},
            }
        return {"success": True, "status": "success", "data": {"path": path, "content": f"CONTENT:{path}"}}

    def sandbox_exec(command: str, **_: Any) -> dict[str, Any]:
        executed.append(command)
        if "fail" in command:
            return {
                "success": False,
                "status": "failed",
                "error": "command failed",
                "error_code": "command_failed",
                "data": {"command": command, "exit_code": 1, "stderr": "FAILED"},
            }
        return {
            "success": True,
            "status": "success",
            "data": {"command": command, "exit_code": 0, "stdout": "OK\n"},
        }

    def fetch_url(url: str, **_: Any) -> dict[str, Any]:
        executed.append(f"fetch_url:{url}")
        return {
            "success": False,
            "status": "failed",
            "error": f"Unable to fetch {url}",
            "error_code": "invalid_arguments",
            "data": {"url": url, "error_code": "invalid_arguments"},
        }

    loop._finish_with_trace = MethodType(finish, loop)
    loop.tools["read_file"] = read_file
    loop.tools["read_document"] = lambda path, **_: (
        executed.append(f"read_document:{path}")
        or {
            "success": True,
            "status": "success",
            "data": {"path": path, "text": "recovered"},
        }
    )
    loop.tools["sandbox_exec"] = sandbox_exec
    loop.tools["fetch_url"] = fetch_url
    loop.tools["get_context_status"] = lambda: (
        executed.append("get_context_status")
        or {"success": True, "status": "success", "data": {"available": True}}
    )
    return loop, llm, captured, executed


def _run(calls: list[Any], *, max_failures: int = 3) -> tuple[MockProvider, dict[str, Any], list[str]]:
    loop, llm, captured, executed = _loop(calls, max_failures=max_failures)
    previous = os.environ.get("AGENT_ACCESS_MODE")
    os.environ["AGENT_ACCESS_MODE"] = "full_access"
    try:
        answer = loop.run("执行本轮结构化工具调用并汇总结果")
    finally:
        if previous is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous
    if max_failures > 1:
        assert answer == "已汇总本轮成功与失败结果。"
    else:
        assert answer
    return llm, captured, executed


def _assert_batch(calls: list[Any], expected_statuses: list[str], *, max_failures: int = 3) -> dict[str, Any]:
    llm, captured, executed = _run(calls, max_failures=max_failures)
    state = captured["state"]
    observations = state.metadata["completion_observations"]
    call_ids = [call.id for call in calls]
    assert [item["call_id"] for item in observations] == call_ids
    assert [step["status"] for step in state.metadata["build_step_contract"]["steps"]] == expected_statuses
    assert state.metadata["build_step_contract"]["all_steps_resolved"] is True
    assert all(item.get("status") != "skipped" for item in observations)
    assert len(executed) == len(calls)
    stages = [str(call["options"].stage) for call in llm.calls]
    assert stages == [
        "initial_agent_turn",
        "agent_continuation",
    ], stages
    rendered_final_messages = json.dumps(llm.calls[-1]["messages"], ensure_ascii=False)
    for call_id in call_ids:
        assert call_id in rendered_final_messages
    assert state.metadata["initial_tool_batch_complete"] is True
    assert state.metadata.get("finalization_mode") != "build_step_contract_resolved"
    assert state.metadata.get("finalization_tools_disabled") is not True
    assert llm.calls[-1]["tools"]
    assert sum(
        event.event_type == "initial_tool_batch_complete"
        for event in captured["trace"].events
    ) == 1
    assert captured["answer"] and not any(name in captured["answer"] for name in ("call_id", "observation_id"))
    return captured


def test_failure_positions_and_repeated_tools() -> None:
    front = [
        _call("call-read-fail", "read_file", {"path": "missing-a.py"}),
        _call("call-shell-ok", "sandbox_exec", {"command": "echo ok"}),
    ]
    captured = _assert_batch(front, ["failed", "completed"])
    disposition = next(
        event.data
        for event in captured["trace"].events
        if event.event_type == "tool_failure_disposition"
    )
    assert disposition["disposition"] == "ordinary_failure"

    back = [
        _call("call-read-ok", "read_file", {"path": "a.py"}),
        _call("call-shell-fail", "sandbox_exec", {"command": "fail"}),
    ]
    _assert_batch(back, ["completed", "failed"])

    middle = [
        _call("call-a", "read_file", {"path": "a.py"}),
        _call("call-b", "read_file", {"path": "missing-b.py"}),
        _call("call-c", "sandbox_exec", {"command": "echo c"}),
    ]
    _assert_batch(middle, ["completed", "failed", "completed"])

    repeated = [
        _call("call-read-1", "read_file", {"path": "one.py"}),
        _call("call-read-2", "read_file", {"path": "missing-two.py"}),
        _call("call-read-3", "read_file", {"path": "three.py"}),
    ]
    _assert_batch(repeated, ["completed", "failed", "completed"])


def test_consecutive_failure_limit_waits_for_batch_end() -> None:
    calls = [
        _call("call-fail-1", "read_file", {"path": "missing-1.py"}),
        _call("call-fail-2", "read_file", {"path": "missing-2.py"}),
        _call("call-fail-3", "sandbox_exec", {"command": "fail but still runs"}),
    ]
    _assert_batch(calls, ["failed", "failed", "failed"], max_failures=1)


def test_ordinary_failure_returns_control_after_batch() -> None:
    missing = (_FIXTURE_ROOT / "authoritative-missing.txt").resolve()
    calls = [
        _call("call-ordinary-missing", "read_file", {"path": str(missing)}),
        _call("call-unrelated-status", "get_context_status", {}),
    ]
    loop, llm, captured, executed = _loop(calls)
    original_execute = loop._execute_tool_envelope

    def execute_with_authoritative_profile(
        self: AgentLoop,
        task_state: Any,
        envelope: Any,
        **kwargs: Any,
    ) -> Any:
        task_state.task_profile = replace(
            task_state.task_profile,
            document_paths=[str(missing)],
        )
        return original_execute(task_state, envelope, **kwargs)

    loop._execute_tool_envelope = MethodType(execute_with_authoritative_profile, loop)
    answer = loop.run("执行结构化批次并总结明确目标失败")
    assert answer == "已汇总本轮成功与失败结果。"
    assert executed == [str(missing), "get_context_status"]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
    ]
    observations = captured["state"].metadata["completion_observations"]
    assert [item["call_id"] for item in observations] == [
        "call-ordinary-missing",
        "call-unrelated-status",
    ]
    rendered_final_messages = json.dumps(llm.calls[-1]["messages"], ensure_ascii=False)
    assert "call-ordinary-missing" in rendered_final_messages
    assert "call-unrelated-status" in rendered_final_messages
    events = captured["trace"].events
    dispositions = [
        event.data
        for event in events
        if event.event_type == "tool_failure_disposition"
    ]
    assert dispositions[0]["disposition"] == "ordinary_failure"
    assert dispositions[0]["outcome_kind"] == "allow_continue"
    assert not any(
        event.event_type.startswith("definitive_tool_failure_")
        for event in events
    )


def test_single_failure_returns_to_agent() -> None:
    loop, llm, captured, _ = _loop([_call("call-only", "read_file", {"path": "missing-only.py"})])
    previous = os.environ.get("AGENT_ACCESS_MODE")
    os.environ["AGENT_ACCESS_MODE"] = "full_access"
    try:
        loop.run("执行单个失败调用")
    finally:
        if previous is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous
    stages = [str(call["options"].stage) for call in llm.calls]
    assert stages[0] == "initial_agent_turn"
    assert stages == ["initial_agent_turn", "agent_continuation"]


def test_fetch_argument_failure_remains_batch_local() -> None:
    calls = [
        _call("call-block", "fetch_url", {"url": "ftp://example.com/file.txt"}),
        _call("call-never", "sandbox_exec", {"command": "echo must-not-run"}),
    ]
    captured = _assert_batch(calls, ["failed", "completed"])
    assert captured["answer"] == "已汇总本轮成功与失败结果。"
    assert not any(event.event_type == "terminal_policy_blocked" for event in captured["trace"].events)


def main() -> None:
    global _FIXTURE_ROOT
    original_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _FIXTURE_ROOT = root
            os.chdir(root)
            context = WorkspaceManager(root).get_context("smoke", "failure-isolation")
            set_current_workspace(context)
            for name in ("a.py", "one.py", "three.py"):
                (root / name).write_text(f"# isolated {name}\n", encoding="utf-8")
            test_failure_positions_and_repeated_tools()
            test_consecutive_failure_limit_waits_for_batch_end()
            test_ordinary_failure_returns_control_after_batch()
            test_single_failure_returns_to_agent()
            test_fetch_argument_failure_remains_batch_local()
    finally:
        _FIXTURE_ROOT = None
        os.chdir(original_cwd)
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_same_turn_tool_failure_isolation ok")


if __name__ == "__main__":
    main()
