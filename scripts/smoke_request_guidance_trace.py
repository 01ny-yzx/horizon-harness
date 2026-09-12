"""Focused smoke for content-free request-guidance trace evidence."""

from __future__ import annotations

import hashlib
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
    request_guidance_trace_payload,
    resolve_request_guidance,
)
from core.runtime_metrics import RuntimeMetrics
from core.trace import AgentTrace
from core.workspace import WorkspaceManager
from providers.mock import MockProvider, assistant_message


def _fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _guidance_content(messages: list[dict[str, Any]], kind: str) -> str:
    for message in messages:
        metadata = message.get("metadata")
        if isinstance(metadata, dict) and metadata.get("guidance_kind") == kind:
            return str(message.get("content") or "")
    return ""


def _guidance_events(trace: AgentTrace) -> list[dict[str, Any]]:
    return [
        event
        for event in trace.to_dict()["events"]
        if event["event_type"] == "request_guidance_resolved"
    ]


def _capture_trace(loop: AgentLoop, target: dict[str, Any]) -> None:
    def finish(
        self: AgentLoop,
        trace: AgentTrace,
        state: Any,
        answer: str,
    ) -> str:
        del self, state
        target["trace"] = trace
        return answer

    loop._finish_with_trace = MethodType(finish, loop)


def _call(call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="read_file",
            arguments=json.dumps({"path": str(ROOT / "core" / "memory.py")}),
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


def test_resolver_metadata(root: Path, database_path: Path) -> None:
    horizon = root / "HORIZON.md"
    horizon.write_text("RULE_A", encoding="utf-8")
    memory = PersistentMemory(
        database_path=database_path,
        user_id="trace-user",
        project_id="trace-project",
    )
    assert memory.add_user_preference("style", "PERSISTENT_RULE")["success"]

    first = resolve_request_guidance(
        project_root=root,
        persistent_memory=memory,
    )
    repeated = resolve_request_guidance(
        project_root=root,
        persistent_memory=memory,
    )
    assert first.root_instruction_fingerprint
    assert first.root_instruction_fingerprint == repeated.root_instruction_fingerprint
    first_root_text = _guidance_content(
        list(first.system_messages),
        "root_instruction",
    )
    assert "RULE_A" in first_root_text
    assert first.root_instruction_chars == len(first_root_text)
    assert first.persistent_guidance_included
    assert first.persistent_guidance_fingerprint
    assert first.persistent_guidance_chars > 0

    horizon.write_text("RULE_B", encoding="utf-8")
    second = resolve_request_guidance(
        project_root=root,
        persistent_memory=memory,
    )
    second_root_text = _guidance_content(
        list(second.system_messages),
        "root_instruction",
    )
    assert second.root_instruction_fingerprint != first.root_instruction_fingerprint
    assert "RULE_B" in second_root_text and "RULE_A" not in second_root_text
    assert second.root_instruction_chars == len(second_root_text)

    payload = request_guidance_trace_payload(second, stage="agent_continuation")
    trace = AgentTrace("trace", "goal", "smoke")
    trace.add_event(
        1,
        "request_guidance_resolved",
        "Request guidance resolved.",
        success=True,
        data=payload,
    )
    serialized_event = json.dumps(_guidance_events(trace)[0], ensure_ascii=False)
    assert "RULE_A" not in serialized_event
    assert "RULE_B" not in serialized_event
    assert "PERSISTENT_RULE" not in serialized_event
    assert payload["instruction_paths"] == [str(horizon.resolve())]
    assert payload["persistent_load_success"] is True


def test_initial_and_continuation_evidence(
    root: Path,
    database_path: Path,
) -> None:
    horizon = root / "HORIZON.md"
    manager = WorkspaceManager(root / "workspaces", database_path=database_path)
    path_context = build_path_context(project_root=root)

    horizon.write_text("INITIAL_RULE_A", encoding="utf-8")
    initial_provider = MutatingProvider(
        [assistant_message("initial done")],
        [lambda: horizon.write_text("INITIAL_RULE_B", encoding="utf-8")],
    )
    initial_capture: dict[str, Any] = {}
    with (
        patch("core.loop.WorkspaceManager", return_value=manager),
        patch("core.loop.build_path_context", return_value=path_context),
    ):
        initial_loop = AgentLoop(initial_provider, Memory(), max_steps=2)
        _capture_trace(initial_loop, initial_capture)
        assert initial_loop.run(
            "initial trace",
            user_id="trace-initial",
            project_id="project",
        ) == "initial done"
    initial_event = _guidance_events(initial_capture["trace"])[0]
    actual_initial = _guidance_content(
        initial_provider.calls[0]["messages"],
        "root_instruction",
    )
    assert "INITIAL_RULE_A" in actual_initial
    assert "INITIAL_RULE_B" not in actual_initial
    assert horizon.read_text(encoding="utf-8") == "INITIAL_RULE_B"
    assert initial_event["data"]["stage"] == "initial_agent_turn"
    assert initial_event["data"]["root_instruction_fingerprint"] == _fingerprint(
        actual_initial
    )

    horizon.write_text("CONTINUATION_RULE_A", encoding="utf-8")
    continuation_provider = MutatingProvider(
        [
            assistant_message("", [_call("read-a")]),
            assistant_message("continuation done"),
        ],
        [lambda: horizon.write_text("CONTINUATION_RULE_B", encoding="utf-8")],
    )
    continuation_capture: dict[str, Any] = {}
    with (
        patch("core.loop.WorkspaceManager", return_value=manager),
        patch("core.loop.build_path_context", return_value=path_context),
    ):
        continuation_loop = AgentLoop(continuation_provider, Memory(), max_steps=4)
        continuation_loop.tools["read_file"] = lambda path: {
            "success": True,
            "status": "success",
            "data": {"path": path, "content": "read"},
            "metadata": {
                "path": path,
                "instruction_discovery_path": path,
                "resource_type": "file",
            },
        }
        _capture_trace(continuation_loop, continuation_capture)
        assert continuation_loop.run(
            "continuation trace",
            user_id="trace-continuation",
            project_id="project",
        ) == "continuation done"
    events = _guidance_events(continuation_capture["trace"])
    reused = next(
        event
        for event in events
        if event["data"].get("resolution_mode") == "reused_initial"
    )
    fresh = next(
        event
        for event in events
        if event["data"].get("stage") == "agent_continuation"
        and event["data"].get("resolution_mode") == "fresh"
    )
    actual_continuation = _guidance_content(
        continuation_provider.calls[1]["messages"],
        "root_instruction",
    )
    actual_first = _guidance_content(
        continuation_provider.calls[0]["messages"],
        "root_instruction",
    )
    assert "CONTINUATION_RULE_A" in actual_first
    assert reused["data"]["root_instruction_fingerprint"] == _fingerprint(
        actual_first
    )
    assert "CONTINUATION_RULE_B" in actual_continuation
    assert "CONTINUATION_RULE_A" not in actual_continuation
    assert fresh["data"]["root_instruction_fingerprint"] == _fingerprint(
        actual_continuation
    )


def test_simple_fast_path_evidence(root: Path, database_path: Path) -> None:
    horizon = root / "HORIZON.md"
    horizon.write_text("SIMPLE_RULE", encoding="utf-8")
    manager = WorkspaceManager(root / "simple-workspaces", database_path=database_path)
    provider = MockProvider(responses=[assistant_message("simple done")])
    capture: dict[str, Any] = {}
    with patch("core.loop.WorkspaceManager", return_value=manager):
        loop = AgentLoop(provider, Memory(), max_steps=2)
    _capture_trace(loop, capture)
    state = task_state_from_initial_direct_answer("simple trace")
    trace = AgentTrace(state.task_id, state.user_goal, state.task_type)
    answer = loop._run_simple_fast_path(
        "simple trace",
        state,
        trace,
        RuntimeMetrics(),
        project_root=root,
    )
    assert answer == "simple done"
    event = _guidance_events(capture["trace"])[0]
    actual = _guidance_content(provider.calls[0]["messages"], "root_instruction")
    assert "SIMPLE_RULE" in actual
    assert event["data"]["stage"] == "simple_fast_path"
    assert event["data"]["root_instruction_fingerprint"] == _fingerprint(actual)


def main() -> None:
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            database_path = Path(directory) / "horizon.db"
            test_resolver_metadata(root, database_path)
            test_initial_and_continuation_evidence(root, database_path)
            test_simple_fast_path_evidence(root, database_path)
    finally:
        for key, value in _PREVIOUS_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_request_guidance_trace ok")


if __name__ == "__main__":
    main()
