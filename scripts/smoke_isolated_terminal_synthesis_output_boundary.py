"""Offline checks that normal Agent flow does not enter terminal synthesis."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
from types import MethodType
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
os.environ.update(_ENV)

import core.loop as loop_module
from core.loop import AgentLoop
from core.memory import Memory
from providers.mock import MockProvider
from scripts.smoke_tool_error_continuation import (
    _call,
    _failed,
    _message,
)


READ_PATH = str(ROOT / "core" / "loop.py")


class RaiseOnSecondCallProvider(MockProvider):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: Any = None,
    ) -> Any:
        if len(self.calls) == 1:
            self.calls.append(
                {"messages": messages, "tools": tools, "options": options}
            )
            raise RuntimeError("isolated provider unavailable")
        return super().chat(messages, tools, options)


def _run_case(
    responses: list[Any],
    *,
    provider: MockProvider | None = None,
    read_success: bool = False,
    max_steps: int = 7,
) -> dict[str, Any]:
    llm = provider or MockProvider(responses=responses)
    memory = Memory()
    loop = AgentLoop(llm, memory, max_steps=max_steps)
    captured: dict[str, Any] = {
        "builder_count": 0,
        "budget_count": 0,
        "executed": [],
        "before": {},
        "snapshot_hashes": [],
    }

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        captured.update(
            trace=trace,
            state=state,
            answer=answer,
            metrics=self._runtime_metrics,
        )
        return answer

    loop._finish_with_trace = MethodType(finish, loop)

    def read_file(path: str, **_: Any) -> dict[str, Any]:
        captured["executed"].append("read_file")
        if read_success:
            return {
                "success": True,
                "status": "success",
                "data": {"path": path, "content": "visible"},
            }
        return _failed("file_not_found", path=path)

    def write_file(path: str, content: str, **_: Any) -> dict[str, Any]:
        captured["executed"].append("write_file")
        return {
            "success": True,
            "status": "success",
            "data": {"path": path, "content": content},
        }

    loop.tools["read_file"] = read_file
    loop.tools["write_file"] = write_file

    original_builder = loop_module.build_finalization_context_snapshot
    original_budget = loop_module.apply_finalization_context_budget

    def counted_builder(*args: Any, **kwargs: Any) -> Any:
        captured["builder_count"] += 1
        task_state = kwargs["task_state"]
        if not captured["before"]:
            captured["before"] = _boundary_state(task_state, memory)
        snapshot = original_builder(*args, **kwargs)
        captured["snapshot_hashes"].append(snapshot.snapshot_hash)
        return snapshot

    def counted_budget(*args: Any, **kwargs: Any) -> Any:
        captured["budget_count"] += 1
        return original_budget(*args, **kwargs)

    loop_module.build_finalization_context_snapshot = counted_builder
    loop_module.apply_finalization_context_budget = counted_budget
    error: Exception | None = None
    answer = ""
    try:
        answer = loop.run("读取指定文件")
    except Exception as exc:  # normal non-terminal provider error is asserted below
        error = exc
    finally:
        loop_module.build_finalization_context_snapshot = original_builder
        loop_module.apply_finalization_context_budget = original_budget
    captured.update(
        answer=answer,
        error=error,
        llm=llm,
        memory=memory,
    )
    return captured


def _boundary_state(state: Any, memory: Memory) -> dict[str, Any]:
    metadata = state.metadata
    return copy.deepcopy(
        {
            "structured_tool_call_ids": metadata.get("structured_tool_call_ids"),
            "structured_tool_call_count": metadata.get(
                "structured_tool_call_count"
            ),
            "pending_tool_call_count": metadata.get("pending_tool_call_count"),
            "completion_observations": metadata.get("completion_observations"),
            "build_step_contract": metadata.get("build_step_contract"),
            "tool_call_grant_ledger": metadata.get("tool_call_grant_ledger"),
            "tool_results": getattr(state, "tool_results", None),
            "memory_messages": memory.messages,
        }
    )


def _events(case: dict[str, Any], event_type: str) -> list[Any]:
    trace = case.get("trace")
    return [
        event
        for event in (getattr(trace, "events", ()) or ())
        if event.event_type == event_type
    ]


def _stages(case: dict[str, Any]) -> list[str]:
    return [
        str(call["options"].stage)
        for call in case["llm"].calls
        if call.get("options") is not None
    ]


def test_valid_prose_is_adopted() -> None:
    expected = "无法读取指定文件。"
    case = _run_case(
        [
            _message(
                "",
                [_call("read-valid", "read_file", {"path": READ_PATH})],
            ),
            _message(expected),
        ]
    )
    assert case["error"] is None
    assert case["answer"] == expected
    assert case["executed"] == ["read_file"]
    assert _stages(case) == ["initial_agent_turn", "agent_continuation"]
    assert case["llm"].calls[-1]["tools"]
    assert case["builder_count"] == 0
    assert not _events(case, "isolated_terminal_synthesis_output")
    assert not _events(case, "terminal_responder_llm")
    assert not _events(case, "emergency_finalization_fallback")
    summary = case["metrics"].summary()
    assert summary["isolated_terminal_synthesis_output_reject_count"] == 0
    assert summary["isolated_terminal_synthesis_fallback_count"] == 0


def test_normal_agent_paths_are_unchanged() -> None:
    tool_case = _run_case(
        [
            _message(
                "",
                [_call("read-normal-tool", "read_file", {"path": READ_PATH})],
            ),
            _message(
                "",
                [
                    _call(
                        "read-normal-tool-2",
                        "read_file",
                        {"path": str(ROOT / "core" / "state.py")},
                    )
                ],
            ),
            _message("两次读取均已完成。"),
        ],
        read_success=True,
    )
    assert tool_case["error"] is None
    assert tool_case["executed"] == ["read_file", "read_file"]
    assert tool_case["builder_count"] == 0
    assert not _events(tool_case, "isolated_terminal_synthesis_output")

    repair_case = _run_case(
        [
            _message(
                "",
                [_call("read-normal-repair", "read_file", {"path": READ_PATH})],
            ),
            _message(""),
            _message("读取完成。"),
        ],
        read_success=True,
    )
    assert repair_case["error"] is None
    assert repair_case["state"].metadata["agent_prose_rejection_count"] == 1
    assert repair_case["state"].metadata["repair_attempt"] is True
    assert _stages(repair_case) == [
        "initial_agent_turn",
        "agent_continuation",
        "agent_continuation",
    ]
    assert not _events(repair_case, "isolated_terminal_synthesis_output")

    provider = RaiseOnSecondCallProvider(
        responses=[
            _message(
                "",
                [
                    _call(
                        "read-normal-provider",
                        "read_file",
                        {"path": READ_PATH},
                    )
                ],
            )
        ]
    )
    provider_case = _run_case([], provider=provider, read_success=True)
    assert isinstance(provider_case["error"], RuntimeError)
    assert not provider_case.get("trace")


def main() -> None:
    test_valid_prose_is_adopted()
    test_normal_agent_paths_are_unchanged()
    print("smoke_isolated_terminal_synthesis_output_boundary ok")


if __name__ == "__main__":
    main()
