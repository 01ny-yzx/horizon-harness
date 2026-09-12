"""Focused smoke for request-time root and persistent guidance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from typing import Any, Callable
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

import core.loop as loop_module
from core.context_budget import apply_context_budget
from core.context_fusion import ContextFusionEngine
from core.loop import AgentLoop
from core.memory import Memory
from core.memory_reference_guidance import resolve_memory_reference_guidance
from core.path_grounding import build_path_context
from core.persistent_memory import PersistentMemory
from core.prompt_pack import build_agent_continuation_pack, build_initial_agent_turn_pack
from core.request_guidance import resolve_request_guidance
from core.simple_fast_path import build_simple_chat_messages
from core.workspace import WorkspaceManager
from providers.mock import MockProvider, assistant_message


def _render(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def _call(call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="read_file",
            arguments=json.dumps({"path": str(ROOT / "core" / "memory.py")}),
        ),
    )


class MutatingProvider(MockProvider):
    def __init__(self, responses: list[Any], mutations: list[Callable[[], None]]) -> None:
        super().__init__(responses=responses)
        self.mutations = mutations

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], options: Any = None) -> Any:
        response = super().chat(messages, tools, options=options)
        index = len(self.calls) - 1
        if index < len(self.mutations):
            self.mutations[index]()
        return response


def _finish_without_persistence(loop: AgentLoop) -> None:
    def finish(self: AgentLoop, trace: Any, state: Any, answer: str) -> str:
        return answer

    loop._finish_with_trace = MethodType(finish, loop)


def test_request_boundary(root: Path, database_path: Path) -> None:
    horizon = root / "HORIZON.md"
    horizon.write_text("ROOT_V1\n" + "R" * 1400, encoding="utf-8")
    memory = PersistentMemory(database_path=database_path, user_id="user-a", project_id="project-a")
    assert memory.add_user_preference("style", "USER_PREF_V1 " + "U" * 780).get("success")
    assert memory.add_user_preference("detail_a", "DETAIL_A " + "A" * 780).get("success")
    assert memory.add_user_preference("detail_b", "DETAIL_B " + "B" * 780).get("success")
    assert memory.add_project_instruction("PROJECT_INSTRUCTION_V1").get("success")
    assert memory.add_stable_fact(
        "STABLE_FACT_BACKGROUND",
        "Stable background fixture",
    ).get("success")
    assert memory.add_project_summary("/project-a", "PROJECT_SUMMARY_BACKGROUND", [], "active").get("success")
    assert memory.add_task_summary({"task_id": "history-background", "summary": "TASK_HISTORY_BACKGROUND"}).get("success")

    guidance = resolve_request_guidance(project_root=root, persistent_memory=memory)
    rendered = _render(list(guidance.system_messages))
    assert all(marker in rendered for marker in ("ROOT_V1", "USER_PREF_V1", "PROJECT_INSTRUCTION_V1"))
    assert all(marker not in rendered for marker in ("STABLE_FACT_BACKGROUND", "PROJECT_SUMMARY_BACKGROUND", "TASK_HISTORY_BACKGROUND"))
    assert guidance.root_instruction_included and guidance.persistent_guidance_included
    assert len(memory.format_instruction_context(max_chars=None)) > 1600

    conversation = Memory()
    initial = build_initial_agent_turn_pack(
        user_input="initial",
        tools=[],
        memory_messages=conversation.messages,
        request_guidance_messages=list(guidance.system_messages),
    )
    continuation = build_agent_continuation_pack(
        user_input="continuation",
        task_state=SimpleNamespace(),
        tools=[],
        memory_messages=conversation.messages,
        request_guidance_messages=list(guidance.system_messages),
    )
    simple = build_simple_chat_messages(
        conversation,
        "simple",
        request_guidance_messages=list(guidance.system_messages),
    )
    for messages in (initial.messages, continuation.messages, simple):
        text = _render(messages)
        assert "ROOT_V1" in text and "USER_PREF_V1" in text and "PROJECT_INSTRUCTION_V1" in text
    assert not any(
        message.get("metadata", {}).get("note_type") == "request_guidance"
        for message in conversation.messages
    )

    reference_guidance = resolve_memory_reference_guidance(persistent_memory=memory)
    reference_note = _render(list(reference_guidance.system_messages))
    assert "<name>stable_fact</name>" in reference_note
    assert "STABLE_FACT_BACKGROUND" not in reference_note
    assert "USER_PREF_V1" not in reference_note and "PROJECT_INSTRUCTION_V1" not in reference_note
    fused = ContextFusionEngine().build_fused_context(
        "question", SimpleNamespace(task_profile=None, task_type="chat", rag_used=False, rag_enough_evidence=False, retrieved_chunks=[]), memory
    )
    fused_text = str(fused.get("data", {}).get("context_text") or "")
    assert "USER_PREF_V1" not in fused_text and "PROJECT_INSTRUCTION_V1" not in fused_text

    pressured_messages = [
        *initial.messages[:-1],
        {"role": "user", "content": "old " + "X" * 30000},
        {"role": "assistant", "content": "old answer " + "Y" * 12000},
        initial.messages[-1],
    ]
    compacted, decision = apply_context_budget(
        pressured_messages,
        tools=[],
        runtime_lane="agent_continuation",
        task_state=SimpleNamespace(metadata={}),
        model_context_tokens=4096,
        reserved_output_tokens=1024,
    )
    compacted_text = _render(compacted)
    assert "ROOT_V1" in compacted_text
    assert "R" * 1400 in compacted_text
    assert "USER_PREF_V1" in compacted_text and "U" * 780 in compacted_text
    assert "DETAIL_A" in compacted_text and "A" * 780 in compacted_text
    assert decision.pressure_detected


def test_live_refresh_and_task_boundary(root: Path, database_path: Path) -> None:
    horizon = root / "HORIZON.md"
    horizon.write_text("ROOT_V1", encoding="utf-8")
    manager = WorkspaceManager(root / "workspaces", database_path=database_path)
    persistent = PersistentMemory(database_path=database_path, user_id="user-a", project_id="project-a")
    persistent.clear_memory_type("user_preferences")
    persistent.clear_memory_type("project_instructions")
    assert persistent.add_user_preference("style", "USER_PREF_V1").get("success")
    assert persistent.add_project_instruction("PROJECT_INSTRUCTION_V1").get("success")

    def update_guidance() -> None:
        horizon.write_text("ROOT_V2", encoding="utf-8")
        assert persistent.add_user_preference("style", "USER_PREF_V2").get("success")
        assert persistent.clear_memory_type("project_instructions").get("success")
        assert persistent.add_project_instruction("PROJECT_INSTRUCTION_V2").get("success")

    def delete_guidance() -> None:
        horizon.unlink()
        assert persistent.clear_memory_type("user_preferences").get("success")
        assert persistent.clear_memory_type("project_instructions").get("success")

    provider = MutatingProvider(
        [
            assistant_message("", [_call("read-v1")]),
            assistant_message("", [_call("read-v2")]),
            assistant_message("done"),
        ],
        [update_guidance, delete_guidance],
    )
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=4)
    loop.workspace_manager = manager
    loop.tools["read_file"] = lambda path: {
        "success": True,
        "status": "success",
        "data": {"path": path, "content": "read"},
        "metadata": {"path": path, "instruction_discovery_path": path, "resource_type": "file"},
    }
    _finish_without_persistence(loop)
    assert loop.run("refresh", user_id="user-a", project_id="project-a") == "done"
    first, second, third = (_render(call["messages"]) for call in provider.calls)
    assert all(marker in first for marker in ("ROOT_V1", "USER_PREF_V1", "PROJECT_INSTRUCTION_V1"))
    assert all(marker in second for marker in ("ROOT_V2", "USER_PREF_V2", "PROJECT_INSTRUCTION_V2"))
    assert all(marker not in second for marker in ("ROOT_V1", "USER_PREF_V1", "PROJECT_INSTRUCTION_V1"))
    assert all(marker not in third for marker in ("ROOT_V1", "ROOT_V2", "USER_PREF_V1", "USER_PREF_V2", "PROJECT_INSTRUCTION_V1", "PROJECT_INSTRUCTION_V2"))

    horizon.write_text("ROOT_TASK_BOUNDARY", encoding="utf-8")
    provider2 = MockProvider(responses=[assistant_message("task one"), assistant_message("task two")])
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop2 = AgentLoop(provider2, Memory(), max_steps=2)
    loop2.workspace_manager = manager
    _finish_without_persistence(loop2)
    assert loop2.run("one", user_id="user-a", project_id="project-a") == "task one"
    assert loop2.run("two", user_id="user-a", project_id="project-a") == "task two"
    assert "ROOT_TASK_BOUNDARY" in _render(provider2.calls[0]["messages"])
    assert "ROOT_TASK_BOUNDARY" in _render(provider2.calls[1]["messages"])


def test_scope_switch(root: Path, database_path: Path) -> None:
    memory_a = PersistentMemory(database_path=database_path, user_id="scope-a", project_id="project-a")
    memory_b = PersistentMemory(database_path=database_path, user_id="scope-b", project_id="project-b")
    assert memory_a.add_user_preference("scope", "SCOPE_A_ONLY").get("success")
    assert memory_a.add_project_instruction("PROJECT_A_ONLY").get("success")
    assert memory_b.add_user_preference("scope", "SCOPE_B_ONLY").get("success")
    assert memory_b.add_project_instruction("PROJECT_B_ONLY").get("success")
    first = _render(list(resolve_request_guidance(project_root=root, persistent_memory=memory_a).system_messages))
    second = _render(list(resolve_request_guidance(project_root=root, persistent_memory=memory_b).system_messages))
    assert "SCOPE_A_ONLY" in first and "PROJECT_A_ONLY" in first
    assert "SCOPE_A_ONLY" not in second and "PROJECT_A_ONLY" not in second
    assert "SCOPE_B_ONLY" in second and "PROJECT_B_ONLY" in second


def main() -> None:
    original_build_path_context = loop_module.build_path_context
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            database_path = Path(directory) / "horizon.db"
            path_context = build_path_context(project_root=root)
            loop_module.build_path_context = lambda *_, **__: path_context
            test_request_boundary(root, database_path)
            test_live_refresh_and_task_boundary(root, database_path)
            test_scope_switch(root, database_path)
    finally:
        loop_module.build_path_context = original_build_path_context
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_request_guidance_lifecycle ok")


if __name__ == "__main__":
    main()
