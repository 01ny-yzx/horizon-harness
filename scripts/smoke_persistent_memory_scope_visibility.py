"""Focused smoke for model-visible persistent-memory workspace scope."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import patch


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
from core.memory_reference_guidance import resolve_memory_reference_guidance
from core.persistent_memory import PersistentMemory
from core.workspace import WorkspaceContext, WorkspaceManager
from core.workspace_runtime import set_current_workspace
from providers.mock import MockProvider, assistant_message


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def _new_loop(
    manager: WorkspaceManager,
    responses: list[Any],
    *,
    memory: Memory | None = None,
) -> tuple[AgentLoop, MockProvider]:
    llm = MockProvider(responses=responses)
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(llm, memory or Memory(), max_steps=3)
    loop.workspace_manager = manager

    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        return answer

    loop._finish_with_trace = MethodType(finish, loop)

    def switch_workspace(user_id: str, project_id: str) -> dict[str, Any]:
        context = manager.get_context(user_id, project_id)
        set_current_workspace(context)
        return {
            "success": True,
            "status": "success",
            "data": {
                "user_id": context.user_id,
                "project_id": context.project_id,
                "workspace_id": context.workspace_id,
            },
        }

    loop.tools["switch_workspace"] = switch_workspace
    return loop, llm


def _persistent(context: WorkspaceContext) -> PersistentMemory:
    return PersistentMemory(
        database_path=context.database_path,
        user_id=context.user_id,
        project_id=context.project_id,
    )


def _rendered_call(llm: MockProvider, index: int) -> str:
    return "\n".join(
        str(message.get("content") or "")
        for message in llm.calls[index]["messages"]
    )


def _seed_scopes(manager: WorkspaceManager) -> None:
    project_a = manager.get_context("user_a", "project_a")
    project_b = manager.get_context("user_a", "project_b")
    user_b = manager.get_context("user_b", "project_b")
    assert project_a.database_path == project_b.database_path == user_b.database_path
    memory_a = _persistent(project_a)
    assert memory_a.add_user_preference("shared", "USER_SHARED").get("success")
    assert memory_a.add_user_preference("user_a", "USER_A_ONLY").get("success")
    assert memory_a.add_project_instruction("PROJECT_A_ONLY").get("success")

    assert _persistent(project_b).add_project_instruction("PROJECT_B_ONLY").get("success")

    assert _persistent(user_b).add_user_preference("user_b", "USER_B_ONLY").get("success")

    source = manager.get_context("user_empty", "project_source")
    assert _persistent(source).add_project_instruction("PROJECT_SOURCE_ONLY").get("success")
    _persistent(manager.get_context("user_empty", "project_blank"))


def test_mid_task_project_switch(manager: WorkspaceManager) -> None:
    loop, llm = _new_loop(
        manager,
        [
            assistant_message(
                "",
                [_call("switch-project", "switch_workspace", {"user_id": "user_a", "project_id": "project_b"})],
            ),
            assistant_message("project switched"),
        ],
    )
    assert loop.run("switch project", user_id="user_a", project_id="project_a") == "project switched"
    first = _rendered_call(llm, 0)
    continuation = _rendered_call(llm, 1)
    assert "USER_SHARED" in first and "PROJECT_A_ONLY" in first
    assert "PROJECT_B_ONLY" not in first
    assert "USER_SHARED" in continuation and "PROJECT_B_ONLY" in continuation
    assert "PROJECT_A_ONLY" not in continuation


def test_mid_task_empty_scope(manager: WorkspaceManager) -> None:
    loop, llm = _new_loop(
        manager,
        [
            assistant_message(
                "",
                [_call("switch-empty", "switch_workspace", {"user_id": "user_empty", "project_id": "project_blank"})],
            ),
            assistant_message("empty project switched"),
        ],
    )
    assert loop.run("switch to blank", user_id="user_empty", project_id="project_source") == "empty project switched"
    assert "PROJECT_SOURCE_ONLY" in _rendered_call(llm, 0)
    assert "PROJECT_SOURCE_ONLY" not in _rendered_call(llm, 1)


def test_mid_task_user_switch(manager: WorkspaceManager) -> None:
    loop, llm = _new_loop(
        manager,
        [
            assistant_message(
                "",
                [_call("switch-user", "switch_workspace", {"user_id": "user_b", "project_id": "project_b"})],
            ),
            assistant_message("user switched"),
        ],
    )
    assert loop.run("switch user", user_id="user_a", project_id="project_a") == "user switched"
    continuation = _rendered_call(llm, 1)
    assert "USER_B_ONLY" in continuation
    assert "USER_A_ONLY" not in continuation
    assert "USER_SHARED" not in continuation


def test_new_task_project_changes(manager: WorkspaceManager) -> None:
    loop, llm = _new_loop(
        manager,
        [assistant_message("task a"), assistant_message("task b")],
    )
    assert loop.run("task a", user_id="user_a", project_id="project_a") == "task a"
    assert loop.run("task b", user_id="user_a", project_id="project_b") == "task b"
    second = _rendered_call(llm, 1)
    assert "USER_SHARED" in second and "PROJECT_B_ONLY" in second
    assert "PROJECT_A_ONLY" not in second


def test_new_task_empty_scope(manager: WorkspaceManager) -> None:
    loop, llm = _new_loop(
        manager,
        [assistant_message("source task"), assistant_message("blank task")],
    )
    assert loop.run("source", user_id="user_empty", project_id="project_source") == "source task"
    assert loop.run("blank", user_id="user_empty", project_id="project_blank") == "blank task"
    assert "PROJECT_SOURCE_ONLY" not in _rendered_call(llm, 1)


def test_no_switch_continuation(manager: WorkspaceManager) -> None:
    loop, llm = _new_loop(
        manager,
        [
            assistant_message("", [_call("read", "read_file", {"path": str(ROOT / "core" / "memory.py")})]),
            assistant_message("read complete"),
        ],
    )
    loop.tools["read_file"] = lambda path: {
        "success": True,
        "status": "success",
        "data": {"path": path, "content": "memory source"},
        "metadata": {
            "path": path,
            "instruction_discovery_path": path,
            "resource_type": "file",
        },
    }
    assert loop.run("read", user_id="user_a", project_id="project_a") == "read complete"
    assert "USER_SHARED" in _rendered_call(llm, 0)
    assert "PROJECT_A_ONLY" in _rendered_call(llm, 0)
    assert "USER_SHARED" in _rendered_call(llm, 1)
    assert "PROJECT_A_ONLY" in _rendered_call(llm, 1)


def test_reference_guidance_is_not_history(manager: WorkspaceManager) -> None:
    memory = Memory()
    memory.add_user_message("ordinary user", task_id="task")
    memory.add_assistant_message("ordinary assistant", task_id="task")
    memory.add_tool_observation("call", "read_file", '{"success":true}', task_id="task")
    before = list(memory.messages)
    persistent = _persistent(manager.get_context("user_a", "project_a"))
    assert persistent.add_stable_fact(
        "REFERENCE_BACKGROUND",
        "Scope visibility background",
    ).get("success") is True
    guidance = resolve_memory_reference_guidance(
        persistent_memory=persistent,
    )
    assert guidance.available is True
    assert memory.messages == before
    assert not any(
        message.get("metadata", {}).get("note_type") == "long_term_memory"
        for message in memory.messages
    )


def main() -> None:
    try:
        with TemporaryDirectory() as directory:
            manager = WorkspaceManager(
                Path(directory) / "workspaces",
                database_path=Path(directory) / "horizon.db",
            )
            _seed_scopes(manager)
            test_mid_task_project_switch(manager)
            test_mid_task_empty_scope(manager)
            test_mid_task_user_switch(manager)
            test_new_task_project_changes(manager)
            test_new_task_empty_scope(manager)
            test_no_switch_continuation(manager)
            test_reference_guidance_is_not_history(manager)
    finally:
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_persistent_memory_scope_visibility ok")


if __name__ == "__main__":
    main()
