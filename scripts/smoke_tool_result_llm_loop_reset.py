"""Offline full-loop coverage for the ToolResult -> LLM authority reset."""

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

import tools.registry as registry_module
from core.initial_tool_surface import (
    build_initial_tool_surface,
    initial_agent_turn_tools,
    permission_tool_names,
    resolve_permission_tool_schemas,
)
from core.loop import AgentLoop
from core.memory import Memory
from core.prompt_pack import build_initial_agent_turn_pack
from core.workspace import WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider, assistant_message
from tools.file_tools import read_file as real_read_file
from tools.registry import get_local_tool_schemas, get_local_tool_specs


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
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


def _schema_names(schemas: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[str]:
    return [
        str(schema.get("function", {}).get("name") or "")
        for schema in schemas
    ]


def _failed(error_code: str, *, recoverable: bool = False, **data: Any) -> dict[str, Any]:
    result = {
        "success": False,
        "status": "failed",
        "error": error_code,
        "error_code": error_code,
        "data": {"error_code": error_code, **data},
    }
    if recoverable:
        result.update(
            recoverable=True,
            recovery_reason="model_may_choose_another_action",
        )
        result["data"].update(
            recoverable=True,
            recovery_reason="model_may_choose_another_action",
        )
    return result


def _success(**data: Any) -> dict[str, Any]:
    return {"success": True, "status": "success", "data": data}


def _run(
    responses: list[Any],
    *,
    tools: dict[str, Any],
    request: str,
    max_steps: int = 6,
    state_projection: Any | None = None,
    provider: MockProvider | None = None,
) -> tuple[str, MockProvider, dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    llm = provider or MockProvider(responses=responses)
    loop = AgentLoop(llm, Memory(), max_steps=max_steps)
    captured: dict[str, Any] = {}
    executed: list[tuple[str, dict[str, Any]]] = []

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(trace=trace, state=state, answer=answer, metrics=self._runtime_metrics)
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    for name, implementation in tools.items():
        def counted(
            *args: Any,
            __name: str = name,
            __impl: Any = implementation,
            **kwargs: Any,
        ) -> Any:
            executed.append((__name, dict(kwargs)))
            return __impl(*args, **kwargs)

        loop.tools[name] = counted

    if state_projection is not None:
        original_execute = loop._execute_tool_envelope

        def execute_with_projection(
            self: AgentLoop,
            state: Any,
            envelope: Any,
            **kwargs: Any,
        ) -> Any:
            state_projection(state)
            return original_execute(state, envelope, **kwargs)

        loop._execute_tool_envelope = MethodType(execute_with_projection, loop)

    answer = loop.run(request)
    return answer, llm, captured, executed


def _current_permission_names(access_mode: str = "full_access") -> list[str]:
    schemas = get_local_tool_schemas()
    allowed = resolve_permission_tool_schemas(
        get_local_tool_specs(),
        schemas,
        access_mode=access_mode,
    )
    return list(permission_tool_names(allowed))


def _assert_normal_two_turn_exit(
    answer: str,
    llm: MockProvider,
    captured: dict[str, Any],
    expected_answer: str,
) -> None:
    assert answer == expected_answer
    assert len(llm.calls) == 2, [str(call["options"].stage) for call in llm.calls]
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "agent_continuation",
    ]
    assert _schema_names(llm.calls[0]["tools"]) == _current_permission_names()
    assert _schema_names(llm.calls[1]["tools"]) == _current_permission_names()
    for call in llm.calls:
        system_text = "\n".join(
            str(message.get("content") or "")
            for message in call["messages"]
            if message.get("role") == "system"
        )
        for marker in (
            "isolated terminal synthesis",
            "tools are disabled",
            "Tool Completion Gate:",
            "ToolPlan",
            "TaskState",
            "TaskProfile",
            "current phase",
            "current plan step",
            "primary tool",
            "primary capability",
        ):
            assert marker.lower() not in system_text.lower(), marker
    event_types = {event.event_type for event in captured["trace"].events}
    assert "terminal_responder_llm" not in event_types
    assert "normal_finalization" not in event_types


def test_registry_permission_surface() -> None:
    specs = get_local_tool_specs()
    schemas = get_local_tool_schemas()
    for access_mode in ("read_only", "full_access"):
        expected = resolve_permission_tool_schemas(
            specs,
            schemas,
            access_mode=access_mode,
        )
        surface = build_initial_tool_surface(
            specs,
            schemas,
            access_mode=access_mode,
        )
        assert _schema_names(surface.schemas) == _schema_names(expected)
        assert _schema_names(initial_agent_turn_tools(surface)) == _schema_names(expected)

    pack = build_initial_agent_turn_pack(
        user_input="inspect the project",
        tools=initial_agent_turn_tools(
            build_initial_tool_surface(specs, schemas, access_mode="read_only")
        ),
        access_mode="read_only",
    )
    prompt = "\n".join(str(message.get("content") or "") for message in pack.messages)
    assert "Tool Completion Gate" not in prompt
    assert "current step" not in prompt.lower()


def test_initial_prose_semantic_keywords_do_not_retry() -> None:
    prose = "Capability routing is part of the Runtime design, and this tool flow is intentional."
    answer, llm, captured, executed = _run(
        [_message(prose)],
        tools={},
        request="解释 Runtime 的 capability routing",
    )
    assert answer == prose
    assert not executed
    assert len(llm.calls) == 1
    assert not any(
        event.event_type == "initial_agent_turn_retry"
        for event in captured["trace"].events
    )


def test_three_call_batch_has_one_followup_llm(root: Path) -> None:
    for index in range(1, 4):
        (root / f"{index}.txt").write_text(f"content:{index}", encoding="utf-8")
    calls = [
        _call(f"read-{index}", "read_file", {"path": str(root / f"{index}.txt")})
        for index in range(1, 4)
    ]
    answer, llm, captured, executed = _run(
        [_message("", calls), _message("三个文件结果均已收到。")],
        tools={"read_file": real_read_file},
        request="读取三个文件并总结",
    )
    _assert_normal_two_turn_exit(answer, llm, captured, "三个文件结果均已收到。")
    assert [name for name, _ in executed] == ["read_file", "read_file", "read_file"]
    messages = llm.calls[1]["messages"]
    assistant_index = next(
        index
        for index, message in enumerate(messages)
        if message.get("role") == "assistant"
        and [call.get("id") for call in message.get("tool_calls") or []]
        == ["read-1", "read-2", "read-3"]
    )
    tool_messages = [
        message
        for message in messages[assistant_index + 1 :]
        if message.get("role") == "tool"
    ]
    assert [message.get("tool_call_id") for message in tool_messages[:3]] == [
        "read-1",
        "read-2",
        "read-3",
    ]


def test_failure_batches_are_collected_before_followup(root: Path) -> None:
    for recoverable, code in ((True, "temporary_read_failure"), (False, "file_not_found")):
        first = root / f"{code}.txt"
        second = root / f"{code}-ok.txt"
        second.write_text("ok", encoding="utf-8")

        def read_file(path: str, **_: Any) -> dict[str, Any]:
            if path == str(first):
                return _failed(code, recoverable=recoverable, path=path)
            return real_read_file(path=path)

        answer, llm, captured, executed = _run(
            [
                _message(
                    "",
                    [
                        _call(f"{code}-fail", "read_file", {"path": str(first)}),
                        _call(f"{code}-ok", "read_file", {"path": str(second)}),
                    ],
                ),
                _message(f"已处理 {code}。"),
            ],
            tools={"read_file": read_file},
            request="执行完整读取批次",
        )
        _assert_normal_two_turn_exit(answer, llm, captured, f"已处理 {code}。")
        assert [name for name, _ in executed] == ["read_file", "read_file"]
        assert "recoverable_failure_agent_choice_pending" not in captured["state"].metadata
        tool_messages = [
            message
            for message in llm.calls[1]["messages"]
            if message.get("role") == "tool"
            and message.get("tool_call_id") in {f"{code}-fail", f"{code}-ok"}
        ]
        assert [message.get("tool_call_id") for message in tool_messages] == [
            f"{code}-fail",
            f"{code}-ok",
        ]


def test_success_then_prose_ignores_legacy_completion_authority(root: Path) -> None:
    target = root / "result.txt"
    target.write_text("done", encoding="utf-8")

    def project_missing_legacy_step(state: Any) -> None:
        state.metadata["tool_plan"] = {
            "primary_tool": "write_file",
            "primary_capability": "file_write",
            "tool_priority": ["write_file"],
        }
        state.metadata["build_step_contract"] = {
            "steps": [
                {
                    "index": 1,
                    "tool_name": "write_file",
                    "capability": "file_write",
                    "status": "pending",
                }
            ],
            "pending_count": 1,
            "all_steps_completed": False,
            "all_steps_resolved": False,
        }

    answer, llm, captured, executed = _run(
        [
            _message("", [_call("success", "read_file", {"path": str(target)})]),
            _message("读取完成，直接给出结果。"),
        ],
        tools={"read_file": real_read_file},
        request="读取现有结果后回答",
        state_projection=project_missing_legacy_step,
    )
    _assert_normal_two_turn_exit(answer, llm, captured, "读取完成，直接给出结果。")
    assert [name for name, _ in executed] == ["read_file"]
    assert captured["state"].metadata.get("direct_agent_prose_adopted") is True


def test_tool_calls_override_provider_stop(root: Path) -> None:
    target = root / "finish-reason.txt"
    target.write_text("ok", encoding="utf-8")
    answer, llm, captured, executed = _run(
        [
            _message(
                "",
                [_call("stop-with-tool", "read_file", {"path": str(target)})],
                finish_reason="stop",
            ),
            _message("工具已经执行。"),
        ],
        tools={"read_file": real_read_file},
        request="读取文件",
    )
    _assert_normal_two_turn_exit(answer, llm, captured, "工具已经执行。")
    assert [name for name, _ in executed] == ["read_file"]


class _AvailabilitySwitchingProvider(MockProvider):
    def __init__(self, state: dict[str, bool], responses: list[Any]) -> None:
        super().__init__(responses=responses)
        self._state = state

    def chat(self, messages: Any, tools: Any, options: Any = None) -> Any:
        result = super().chat(messages, tools, options)
        if len(self.calls) == 1:
            self._state["search_available"] = False
        return result


def test_web_availability_is_re_resolved() -> None:
    state = {"search_available": True}
    original_status = registry_module.get_web_search_provider_status
    registry_module.get_web_search_provider_status = lambda: SimpleNamespace(
        search_available=state["search_available"]
    )
    try:
        provider = _AvailabilitySwitchingProvider(
            state,
            [
                _message(
                    "",
                    [_call("fetch", "fetch_url", {"url": "https://example.test/"})],
                ),
                _message("availability transition complete"),
            ],
        )
        answer, llm, captured, executed = _run(
            [],
            tools={"fetch_url": lambda **_: _success(url="https://example.test/", output="ok")},
            request="fetch then continue",
            provider=provider,
        )
    finally:
        registry_module.get_web_search_provider_status = original_status
    assert answer == "availability transition complete"
    assert "web_search" in _schema_names(llm.calls[0]["tools"])
    assert "fetch_url" in _schema_names(llm.calls[0]["tools"])
    assert "web_search" not in _schema_names(llm.calls[1]["tools"])
    assert "fetch_url" in _schema_names(llm.calls[1]["tools"])
    assert [name for name, _ in executed] == ["fetch_url"]
    assert captured["state"].is_finished is True


def test_exact_three_repeat_uses_doom_loop_permission(root: Path) -> None:
    target = root / "repeat.txt"
    target.write_text("same", encoding="utf-8")
    responses = [
        _message(
            "",
            [
                _call(f"repeat-{index}", "read_file", {"path": str(target)})
                for index in range(1, 4)
            ],
        ),
        _message("重复调用已被安全停止。"),
    ]
    answer, llm, captured, executed = _run(
        responses,
        tools={"read_file": real_read_file},
        request="重复读取同一文件",
    )
    assert answer
    assert [name for name, _ in executed] == ["read_file", "read_file"]
    permission_event = next(
        event for event in captured["trace"].events
        if event.event_type == "doom_loop_permission_decision"
    )
    assert permission_event.data["consecutive_count"] == 3
    assert permission_event.data["permission"] == "doom_loop"
    assert permission_event.data["action"] == "deny"
    assert not any(event.event_type == "exact_tool_call_loop_stop" for event in captured["trace"].events)
    assert all(name == "read_file" for name, _ in executed)
    assert [str(call["options"].stage) for call in llm.calls] == [
        "initial_agent_turn",
        "final_answer",
    ]


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            set_current_workspace(
                WorkspaceManager(root).get_context("smoke", "tool-result-llm-loop-reset")
            )
            test_registry_permission_surface()
            test_initial_prose_semantic_keywords_do_not_retry()
            test_three_call_batch_has_one_followup_llm(root)
            test_failure_batches_are_collected_before_followup(root)
            test_success_then_prose_ignores_legacy_completion_authority(root)
            test_tool_calls_override_provider_stop(root)
            test_web_availability_is_re_resolved()
            test_exact_three_repeat_uses_doom_loop_permission(root)
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_tool_result_llm_loop_reset ok")


if __name__ == "__main__":
    main()
