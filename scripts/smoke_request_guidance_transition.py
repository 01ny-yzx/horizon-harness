"""Smoke checks for one-request guidance transition notes."""

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
    "LLM_PROVIDER": "mock",
    "LLM_MODEL": "mock",
    "LLM_API_KEY": "",
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

from core.initial_agent_turn import task_state_from_initial_direct_answer
from core.loop import AgentLoop
from core.memory import Memory
from core.path_grounding import build_path_context
from core.persistent_memory import PersistentMemory
from core.request_guidance import (
    REQUEST_GUIDANCE_TRANSITION_TEXT,
    resolve_request_guidance,
    resolve_request_guidance_transition,
)
from core.runtime_metrics import RuntimeMetrics
from core.trace import AgentTrace
from core.workspace import WorkspaceManager
from providers.mock import MockProvider, assistant_message


def _has_transition(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(message.get("metadata"), dict)
        and message["metadata"].get("note_type")
        == "request_guidance_transition"
        and message.get("content") == REQUEST_GUIDANCE_TRANSITION_TEXT
        for message in messages
    )


def _guidance_events(trace: AgentTrace) -> list[dict[str, Any]]:
    return [
        event
        for event in trace.to_dict()["events"]
        if event["event_type"] == "request_guidance_resolved"
    ]


def _capture_traces(loop: AgentLoop, target: list[AgentTrace]) -> None:
    def finish(
        self: AgentLoop,
        trace: AgentTrace,
        state: Any,
        answer: str,
    ) -> str:
        del self, state
        target.append(trace)
        return answer

    loop._finish_with_trace = MethodType(finish, loop)


def _call(call_id: str, filename: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="read_file",
            arguments=json.dumps({"path": str(ROOT / "core" / filename)}),
        ),
    )


class MutatingProvider(MockProvider):
    def __init__(
        self,
        responses: list[Any],
        mutations: list[Callable[[], None]],
    ) -> None:
        super().__init__(responses=responses)
        self.mutations = mutations

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: Any = None,
    ) -> Any:
        response = super().chat(messages, tools, options=options)
        index = len(self.calls) - 1
        if index < len(self.mutations):
            self.mutations[index]()
        return response


def test_effective_fingerprint_and_initial_tasks(
    root: Path,
    database_path: Path,
) -> None:
    horizon = root / "HORIZON.md"
    horizon.write_text("GUIDANCE_A", encoding="utf-8")
    memory = PersistentMemory(
        database_path=database_path,
        user_id="unit-user",
        project_id="unit-project",
    )
    guidance_a = resolve_request_guidance(
        project_root=root,
        persistent_memory=memory,
    )
    first = resolve_request_guidance_transition(
        guidance_a,
        previous_effective_fingerprint="",
    )
    same = resolve_request_guidance_transition(
        guidance_a,
        previous_effective_fingerprint=guidance_a.effective_guidance_fingerprint,
    )
    assert not first.transition_note_included
    assert not same.transition_note_included
    assert all(
        message.get("content") != REQUEST_GUIDANCE_TRANSITION_TEXT
        for message in guidance_a.system_messages
    )

    manager = WorkspaceManager(root / "workspaces", database_path=database_path)
    provider = MockProvider(
        responses=[
            assistant_message("[OLD_RULE] one"),
            assistant_message("different assistant history"),
            assistant_message("three"),
            assistant_message("four"),
        ]
    )
    traces: list[AgentTrace] = []
    path_context = build_path_context(project_root=root)
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=2)
    _capture_traces(loop, traces)
    with patch("core.loop.build_path_context", return_value=path_context):
        loop.run("one", user_id="initial-user", project_id="project")
        loop.run("two", user_id="initial-user", project_id="project")
        horizon.write_text("GUIDANCE_B", encoding="utf-8")
        loop.run("three", user_id="initial-user", project_id="project")
        loop.run("four", user_id="initial-user", project_id="project")

    assert [_has_transition(call["messages"]) for call in provider.calls] == [
        False,
        False,
        True,
        False,
    ]
    payloads = [_guidance_events(trace)[0]["data"] for trace in traces]
    assert payloads[0]["effective_guidance_fingerprint"] == payloads[1][
        "effective_guidance_fingerprint"
    ]
    assert payloads[2]["effective_guidance_fingerprint"] != payloads[1][
        "effective_guidance_fingerprint"
    ]
    assert payloads[2]["effective_guidance_fingerprint"] == payloads[3][
        "effective_guidance_fingerprint"
    ]
    assert [item["transition_note_included"] for item in payloads] == [
        False,
        False,
        True,
        False,
    ]


def test_persistent_guidance_change(root: Path, database_path: Path) -> None:
    (root / "HORIZON.md").write_text("STATIC_ROOT", encoding="utf-8")
    manager = WorkspaceManager(root / "persistent-workspaces", database_path=database_path)
    stored = PersistentMemory(
        database_path=database_path,
        user_id="persistent-user",
        project_id="project",
    )
    assert stored.add_user_preference("style", "PREFERENCE_A")["success"]
    provider = MockProvider(
        responses=[assistant_message("one"), assistant_message("two"), assistant_message("three")]
    )
    traces: list[AgentTrace] = []
    path_context = build_path_context(project_root=root)
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=2)
    _capture_traces(loop, traces)
    with patch("core.loop.build_path_context", return_value=path_context):
        loop.run("one", user_id="persistent-user", project_id="project")
        assert stored.add_user_preference("style", "PREFERENCE_B")["success"]
        loop.run("two", user_id="persistent-user", project_id="project")
        loop.run("three", user_id="persistent-user", project_id="project")
    assert [_has_transition(call["messages"]) for call in provider.calls] == [
        False,
        True,
        False,
    ]
    payloads = [_guidance_events(trace)[0]["data"] for trace in traces]
    assert payloads[1]["guidance_changed_since_previous_request"] is True
    assert payloads[2]["guidance_changed_since_previous_request"] is False


def test_continuation_transition(root: Path, database_path: Path) -> None:
    horizon = root / "HORIZON.md"
    horizon.write_text("CONTINUATION_A", encoding="utf-8")
    manager = WorkspaceManager(root / "continuation-workspaces", database_path=database_path)
    provider = MutatingProvider(
        responses=[
            assistant_message("", [_call("read-a", "memory.py")]),
            assistant_message("", [_call("read-b", "trace.py")]),
            assistant_message("done"),
        ],
        mutations=[lambda: horizon.write_text("CONTINUATION_B", encoding="utf-8")],
    )
    traces: list[AgentTrace] = []
    path_context = build_path_context(project_root=root)
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=5)
    loop.tools["read_file"] = lambda path: {
        "success": True,
        "status": "success",
        "data": {"path": path, "content": "read"},
        "metadata": {
            "path": path,
            "instruction_discovery_path": path,
            "resource_type": "file",
        },
    }
    _capture_traces(loop, traces)
    with patch("core.loop.build_path_context", return_value=path_context):
        assert loop.run(
            "continuation",
            user_id="continuation-user",
            project_id="project",
        ) == "done"
    assert [_has_transition(call["messages"]) for call in provider.calls] == [
        False,
        True,
        False,
    ]
    continuation_events = [
        event["data"]
        for event in _guidance_events(traces[0])
        if event["data"]["stage"] == "agent_continuation"
    ]
    reused = next(
        item for item in continuation_events if item["resolution_mode"] == "reused_initial"
    )
    fresh = [
        item for item in continuation_events if item["resolution_mode"] == "fresh"
    ]
    assert reused["transition_note_included"] is False
    assert [item["transition_note_included"] for item in fresh] == [True, False]


def test_simple_fast_path_transition(root: Path, database_path: Path) -> None:
    horizon = root / "HORIZON.md"
    horizon.write_text("SIMPLE_A", encoding="utf-8")
    manager = WorkspaceManager(root / "simple-workspaces", database_path=database_path)
    provider = MockProvider(
        responses=[assistant_message("one"), assistant_message("two"), assistant_message("three")]
    )
    traces: list[AgentTrace] = []
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=2)
    _capture_traces(loop, traces)
    for index in range(3):
        if index == 1:
            horizon.write_text("SIMPLE_B", encoding="utf-8")
        state = task_state_from_initial_direct_answer(f"simple-{index}")
        trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
        loop._run_simple_fast_path(
            state.user_goal,
            state,
            trace,
            RuntimeMetrics(),
            project_root=root,
        )
    assert [_has_transition(call["messages"]) for call in provider.calls] == [
        False,
        True,
        False,
    ]
    payloads = [_guidance_events(trace)[0]["data"] for trace in traces]
    assert [item["transition_note_included"] for item in payloads] == [
        False,
        True,
        False,
    ]


def main() -> None:
    try:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            for name, test in (
                ("initial", test_effective_fingerprint_and_initial_tasks),
                ("persistent", test_persistent_guidance_change),
                ("continuation", test_continuation_transition),
                ("simple", test_simple_fast_path_transition),
            ):
                root = base / name
                root.mkdir()
                test(root, base / f"{name}.db")
    finally:
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_request_guidance_transition ok")


if __name__ == "__main__":
    main()
