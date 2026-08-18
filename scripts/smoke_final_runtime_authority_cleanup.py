"""Verify final Runtime authority cleanup without external side effects."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PREVIOUS_ACCESS_MODE = os.environ.get("AGENT_ACCESS_MODE")
os.environ["AGENT_ACCESS_MODE"] = "full_access"

from core.execution_boundary import evaluate_tool_execution_boundary
from core.loop import AgentLoop
from core.state import PlanStep, TaskState
from core.task_profile import TaskProfile
import tools.memory_tools as memory_tools


class FakePersistentMemory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def add_task_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("add_task_summary", (summary,), {}))
        return {"success": True}

    def forget_memory(self, memory_type: str, keyword: str) -> dict[str, Any]:
        self.calls.append(("forget_memory", (), {
            "memory_type": memory_type,
            "keyword": keyword,
        }))
        return {"success": True}

    def add_user_preference(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("add_user_preference", (), kwargs))
        return {"success": True}

    def add_stable_fact(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("add_stable_fact", (), kwargs))
        return {"success": True}

    def add_project_summary(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("add_project_summary", (), kwargs))
        return {"success": True}


class FakeMemory:
    def __init__(self) -> None:
        self.notes: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def add_system_note(self, *args: Any, **kwargs: Any) -> None:
        self.notes.append((args, kwargs))


def _profile(**overrides: Any) -> TaskProfile:
    values = {
        "task_type": "simple",
        "needs_web": False,
        "has_url": False,
        "has_search_engine_url": False,
        "needs_code_edit": False,
        "needs_validation": False,
        "needs_git": False,
        "needs_file_output": False,
        "user_intent_summary": "smoke",
        "tool_required": False,
        "side_effect_required": False,
        "execution_mode": "normal",
    }
    values.update(overrides)
    return TaskProfile(**values)


def _state(user_goal: str, *, profile: TaskProfile | None = None) -> TaskState:
    return TaskState.create(
        user_goal=user_goal,
        task_type="simple",
        task_profile=profile or _profile(),
        plan=[PlanStep(index=1, name="smoke", instruction="smoke")],
    )


def _history_loop(memory: FakePersistentMemory) -> AgentLoop:
    loop = object.__new__(AgentLoop)
    loop.persistent_memory = memory
    loop._build_task_summary = lambda state, answer: {
        "task": state.user_goal,
        "answer": answer,
    }
    return loop


def test_text_does_not_change_long_term_memory() -> None:
    for user_goal in (
        "删除这个文件",
        "清空日志文件",
        "删除 users 表中的测试数据",
        "记住这个临时文件名，然后继续处理",
    ):
        memory = FakePersistentMemory()
        state = _state(user_goal)
        _history_loop(memory)._save_task_history_after_task(state, "done")
        assert [item[0] for item in memory.calls] == ["add_task_summary"]
        assert state.saved_memory_types == ["task_summary"]


def test_memory_tool_attempt_skips_task_summary() -> None:
    for tool_name in (
        "remember_user_preference",
        "forget_memory",
        "list_memories",
    ):
        memory = FakePersistentMemory()
        state = _state("ordinary request")
        state.metadata["memory_tool_attempted"] = True
        _history_loop(memory)._save_task_history_after_task(state, tool_name)
        assert memory.calls == []


def test_forget_memory_keeps_explicit_keyword() -> None:
    fake_memory = FakePersistentMemory()
    original_memory = memory_tools._memory
    memory_tools._memory = lambda: fake_memory
    try:
        assert memory_tools.forget_memory("all", "回答风格偏好")["success"] is True
        assert fake_memory.calls[-1][2] == {
            "memory_type": "all",
            "keyword": "回答风格偏好",
        }
        explicit = "忘记我之前的回答风格偏好"
        assert memory_tools.forget_memory("all", explicit)["success"] is True
        assert fake_memory.calls[-1][2]["keyword"] == explicit
        empty = memory_tools.forget_memory("all", "  ")
        assert empty == {"success": False, "error": "keyword is required."}
    finally:
        memory_tools._memory = original_memory


def _file_write_state(*, authorized: bool) -> TaskState:
    state = _state(
        "write output",
        profile=_profile(
            needs_file_output=True,
            requested_output_path="output.txt",
            raw_requested_output_path="output.txt",
            output_filename="output.txt",
            tool_required=True,
            side_effect_required=True,
        ),
    )
    capability = "file_write" if authorized else "file_read"
    tool = "write_file" if authorized else "read_file"
    state.metadata.update(
        {
            "required_capabilities": [capability],
            "primary_capability": capability,
            "primary_tool": tool,
            "tool_required": True,
            "side_effect_required": True,
            "tool_plan": {
                "primary_capability": capability,
                "primary_tool": tool,
                "tool_priority": [tool],
            },
            "capability_routing": {
                "required_capabilities": [capability],
                "primary_capability": capability,
                "primary_tool": tool,
                "tool_plan": {
                    "primary_capability": capability,
                    "primary_tool": tool,
                    "tool_priority": [tool],
                },
            },
        }
    )
    state.intent_runtime_context = SimpleNamespace(
        tool_plan=deepcopy(state.metadata["tool_plan"])
    )
    return state


def test_file_write_execution_ignores_legacy_plan_authority() -> None:
    state = _file_write_state(authorized=False)
    before_profile = deepcopy(state.task_profile)
    before_metadata = deepcopy(state.metadata)
    before_context_plan = deepcopy(state.intent_runtime_context.tool_plan)
    decision = evaluate_tool_execution_boundary(
        task_state=state,
        tool_name="write_file",
        arguments={"path": "output.txt", "content": "hello"},
        raw_arguments='{"path":"output.txt","content":"hello"}',
    )
    assert decision.allowed is True
    assert state.task_profile == before_profile
    assert state.metadata["tool_plan"] == before_metadata["tool_plan"]
    assert (
        state.metadata["capability_routing"]
        == before_metadata["capability_routing"]
    )
    assert (
        state.metadata["required_capabilities"]
        == before_metadata["required_capabilities"]
    )
    assert state.metadata["primary_capability"] == "file_read"
    assert state.metadata["primary_tool"] == "read_file"
    assert state.intent_runtime_context.tool_plan == before_context_plan
    assert not any(
        key.startswith("toolplan_runtime" + "_recovery_")
        for key in state.metadata
    )

    authorized = _file_write_state(authorized=True)
    authorized_profile = deepcopy(authorized.task_profile)
    authorized_metadata = deepcopy(authorized.metadata)
    allowed = evaluate_tool_execution_boundary(
        task_state=authorized,
        tool_name="write_file",
        arguments={"path": "output.txt", "content": "hello"},
        raw_arguments='{"path":"output.txt","content":"hello"}',
    )
    assert allowed.allowed is True
    assert allowed.sanitized_arguments == {"path": "output.txt", "content": "hello"}
    assert authorized.task_profile == authorized_profile
    assert authorized.metadata["tool_plan"] == authorized_metadata["tool_plan"]


def test_fetch_failure_records_observation_without_router_note() -> None:
    loop = object.__new__(AgentLoop)
    loop.memory = FakeMemory()
    loop._should_record_validation = lambda *_: False
    state = _state("read URL")
    observation = {
        "success": False,
        "status": "failed",
        "error": "request failed",
        "error_code": "http_403",
        "data": {"http_status": 403},
    }
    loop._update_task_state(
        state,
        "fetch_url",
        {"url": "https://example.com"},
        observation,
    )
    assert state.metadata["completion_observations"][0]["tool"] == "fetch_url"
    assert state.tool_failures[0]["error_code"] == "http_403"
    assert loop.memory.notes == []


def main() -> None:
    try:
        test_text_does_not_change_long_term_memory()
        test_memory_tool_attempt_skips_task_summary()
        test_forget_memory_keeps_explicit_keyword()
        test_file_write_execution_ignores_legacy_plan_authority()
        test_fetch_failure_records_observation_without_router_note()
        print("smoke_final_runtime_authority_cleanup ok")
    finally:
        if _PREVIOUS_ACCESS_MODE is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = _PREVIOUS_ACCESS_MODE


if __name__ == "__main__":
    main()
