"""Focused S2 smoke for durable canonical Session history."""

from __future__ import annotations

from pathlib import Path
import json
import os
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import DatabaseService
from core.agent_factory import build_agent_for_workspace
from core.mcp_registry import MCPRegistry
from core.mcp_runtime import MCPRuntimeStatus
from core.memory import Memory
from core.session import SessionService
from core.session_execution import SessionExecution
from core.session_message import create_session_message_id
from core.session_message_updater import (
    PROMPTED,
    STEP_ENDED,
    STEP_FAILED,
    STEP_STARTED,
    TEXT_ENDED,
    TEXT_STARTED,
    TOOL_CALLED,
    TOOL_FAILED,
    TOOL_INPUT_ENDED,
    TOOL_INPUT_STARTED,
    TOOL_SUCCESS,
    SessionMessageProjectionError,
)
from core.session_store import SessionStore
from core.session_runner import SessionRunner
from providers.mock import MockProvider, assistant_message


def _session(path: Path, session_id: str):
    return SessionService(path).create(
        session_id=session_id,
        user_id="user-a",
        project_id="project-a",
        workspace_id="user-a/project-a",
        directory=path.parent,
        title="S2 smoke",
    )


def _publish(service: SessionService, session_id: str, kind: str, **data):
    return service.events.publish(
        aggregate_id=session_id,
        event_type=kind,
        data={"session_id": session_id, **data},
    )


def _assistant(store: SessionStore, session_id: str, message_id: str):
    return next(item for item in store.messages(session_id) if item.id == message_id)


def test_text_and_stable_sequence(path: Path) -> None:
    session = _session(path, "ses_s2_text")
    service = SessionService(path)
    user_id = create_session_message_id()
    assistant_id = create_session_message_id()
    prompted = _publish(service, session.id, PROMPTED, message_id=user_id, text="hello")
    started = _publish(
        service,
        session.id,
        STEP_STARTED,
        assistant_message_id=assistant_id,
        provider="openai_compatible",
        model="deepseek-flash",
    )
    _publish(service, session.id, TEXT_STARTED, assistant_message_id=assistant_id, text_id="text_a")
    _publish(
        service,
        session.id,
        TEXT_ENDED,
        assistant_message_id=assistant_id,
        text_id="text_a",
        text="complete answer",
    )
    ended = _publish(
        service,
        session.id,
        STEP_ENDED,
        assistant_message_id=assistant_id,
        finish="stop",
    )
    messages = SessionStore(path).messages(session.id)
    assert [item.type for item in messages] == ["user", "assistant"]
    assert messages[0].seq == prompted.seq
    assert messages[0].data["text"] == "hello"
    assert messages[1].seq == started.seq
    assert messages[1].seq != ended.seq
    assert messages[1].data["content"] == [
        {"id": "text_a", "text": "complete answer", "type": "text"}
    ]
    assert messages[1].data["finish"] == "stop"


def test_tool_lifecycle_and_isolation(path: Path) -> None:
    first = _session(path, "ses_s2_tools_a")
    second = _session(path, "ses_s2_tools_b")
    service = SessionService(path)
    assistant_id = create_session_message_id()
    _publish(service, first.id, STEP_STARTED, assistant_message_id=assistant_id)
    for index in range(1, 5):
        call_id = f"call_{index}"
        _publish(
            service,
            first.id,
            TOOL_INPUT_STARTED,
            assistant_message_id=assistant_id,
            call_id=call_id,
            name="read_file" if index != 2 else "search_text",
        )
        _publish(
            service,
            first.id,
            TOOL_INPUT_ENDED,
            assistant_message_id=assistant_id,
            call_id=call_id,
            text=f'{{"path":"file-{index}"}}',
        )
        if index != 4:
            _publish(
                service,
                first.id,
                TOOL_CALLED,
                assistant_message_id=assistant_id,
                call_id=call_id,
                name="read_file" if index != 2 else "search_text",
                input={"path": f"file-{index}"},
            )
    success_observation = {
        "success": True,
        "status": "success",
        "error_code": "",
        "data": {
            "path": "file-2",
            "content_ref": "tool_results/ref.txt",
            "real_execution": True,
            "tool_executed": True,
        },
    }
    _publish(
        service,
        first.id,
        TOOL_SUCCESS,
        assistant_message_id=assistant_id,
        call_id="call_2",
        observation=success_observation,
    )
    failed_observation = {
        "success": False,
        "status": "failed",
        "error": "missing",
        "error_code": "file_not_found",
        "data": {"real_execution": True, "tool_executed": True},
    }
    _publish(
        service,
        first.id,
        TOOL_FAILED,
        assistant_message_id=assistant_id,
        call_id="call_1",
        observation=failed_observation,
    )
    blocked_observation = {
        "success": False,
        "status": "blocked",
        "error": "session mismatch",
        "error_code": "session_workspace_mismatch",
        "data": {"real_execution": False, "tool_executed": False},
    }
    _publish(
        service,
        first.id,
        TOOL_FAILED,
        assistant_message_id=assistant_id,
        call_id="call_3",
        observation=blocked_observation,
    )
    skipped_observation = {
        "success": False,
        "status": "skipped",
        "error": "previous tool completed task",
        "error_code": "previous_tool_completed_task",
        "data": {"real_execution": False, "tool_executed": False},
    }
    _publish(
        service,
        first.id,
        TOOL_FAILED,
        assistant_message_id=assistant_id,
        call_id="call_4",
        observation=skipped_observation,
    )
    assistant = _assistant(SessionStore(path), first.id, assistant_id)
    tools = {item["id"]: item for item in assistant.data["content"]}
    assert len(tools) == 4
    assert tools["call_2"]["state"]["status"] == "completed"
    assert tools["call_2"]["state"]["observation"] == success_observation
    assert tools["call_1"]["state"]["status"] == "error"
    assert tools["call_1"]["state"]["error_code"] == "file_not_found"
    assert tools["call_1"]["state"]["real_execution"] is True
    assert tools["call_3"]["state"]["status"] == "error"
    assert tools["call_3"]["state"]["observation_status"] == "blocked"
    assert tools["call_3"]["state"]["real_execution"] is False
    assert tools["call_3"]["state"]["tool_executed"] is False
    assert tools["call_4"]["state"]["status"] == "error"
    assert tools["call_4"]["state"]["observation_status"] == "skipped"
    assert [item.type for item in SessionStore(path).messages(first.id)] == ["assistant"]

    latest = service.events.latest_sequence(second.id)
    try:
        _publish(
            service,
            second.id,
            TOOL_FAILED,
            assistant_message_id=assistant_id,
            call_id="call_1",
            observation=failed_observation,
        )
    except SessionMessageProjectionError:
        pass
    else:
        raise AssertionError("cross-Session Assistant update was accepted")
    assert service.events.latest_sequence(second.id) == latest
    assert SessionStore(path).messages(second.id) == []


def test_projection_rollback_and_runtime_note_exclusion(path: Path) -> None:
    session = _session(path, "ses_s2_rollback")
    service = SessionService(path)
    assistant_id = create_session_message_id()
    started = _publish(
        service,
        session.id,
        STEP_STARTED,
        assistant_message_id=assistant_id,
    )
    try:
        _publish(
            service,
            session.id,
            TEXT_ENDED,
            assistant_message_id=assistant_id,
            text_id="missing_text",
            text="must rollback",
        )
    except SessionMessageProjectionError:
        pass
    else:
        raise AssertionError("invalid projector update was committed")
    assert service.events.latest_sequence(session.id) == started.seq
    assert len(service.events.read_aggregate(session.id)) == 2
    assert _assistant(SessionStore(path), session.id, assistant_id).data["content"] == []

    failed_assistant_id = create_session_message_id()
    _publish(
        service,
        session.id,
        STEP_STARTED,
        assistant_message_id=failed_assistant_id,
    )
    _publish(
        service,
        session.id,
        STEP_FAILED,
        assistant_message_id=failed_assistant_id,
        error="provider unavailable",
        error_code="provider_error",
    )
    failed_assistant = _assistant(
        SessionStore(path), session.id, failed_assistant_id
    )
    assert failed_assistant.data["finish"] == "error"
    assert failed_assistant.data["error"]["error_code"] == "provider_error"

    memory = Memory()
    memory.add_user_message("runtime user")
    memory.add_assistant_message("runtime assistant")
    memory.add_tool_observation("runtime-call", "read_file", '{"success":true}')
    memory.add_system_note("runtime-only", note_type="runtime_state")
    assert [item["role"] for item in memory.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "system",
    ]
    assert len(SessionStore(path).messages(session.id)) == 2


def test_step_started_supersedes_latest_incomplete_assistant(path: Path) -> None:
    service = SessionService(path)
    store = SessionStore(path)

    completed_session = _session(path, "ses_s2_completed_supersession")
    completed_a = create_session_message_id()
    completed_b = create_session_message_id()
    completed_started = _publish(
        service,
        completed_session.id,
        STEP_STARTED,
        assistant_message_id=completed_a,
        timestamp=1_000,
    )
    _publish(
        service,
        completed_session.id,
        TEXT_STARTED,
        assistant_message_id=completed_a,
        text_id="text_completed",
        timestamp=1_050,
    )
    _publish(
        service,
        completed_session.id,
        TEXT_ENDED,
        assistant_message_id=completed_a,
        text_id="text_completed",
        text="preserved completed text",
        timestamp=1_075,
    )
    _publish(
        service,
        completed_session.id,
        STEP_ENDED,
        assistant_message_id=completed_a,
        finish="stop",
        timestamp=1_100,
    )
    _publish(
        service,
        completed_session.id,
        STEP_STARTED,
        assistant_message_id=completed_b,
        timestamp=1_200,
    )
    old_completed = _assistant(store, completed_session.id, completed_a)
    new_after_completed = _assistant(store, completed_session.id, completed_b)
    assert old_completed.seq == completed_started.seq
    assert old_completed.data["time"] == {"created": 1_000, "completed": 1_100}
    assert old_completed.data["finish"] == "stop"
    assert old_completed.data["content"][0]["text"] == "preserved completed text"
    assert new_after_completed.data["time"] == {"created": 1_200}

    stale_session = _session(path, "ses_s2_stale_supersession")
    stale_a = create_session_message_id()
    stale_b = create_session_message_id()
    stale_started = _publish(
        service,
        stale_session.id,
        STEP_STARTED,
        assistant_message_id=stale_a,
        provider="provider-a",
        model="model-a",
        timestamp=2_000,
    )
    _publish(
        service,
        stale_session.id,
        TEXT_STARTED,
        assistant_message_id=stale_a,
        text_id="text_stale",
        timestamp=2_050,
    )
    _publish(
        service,
        stale_session.id,
        TEXT_ENDED,
        assistant_message_id=stale_a,
        text_id="text_stale",
        text="preserve stale content",
        timestamp=2_100,
    )
    next_started = _publish(
        service,
        stale_session.id,
        STEP_STARTED,
        assistant_message_id=stale_b,
        timestamp=2_200,
    )
    closed_stale = _assistant(store, stale_session.id, stale_a)
    current = _assistant(store, stale_session.id, stale_b)
    assert closed_stale.id == stale_a
    assert closed_stale.seq == stale_started.seq
    assert closed_stale.data == {
        "content": [{"id": "text_stale", "text": "preserve stale content", "type": "text"}],
        "model": "model-a",
        "provider": "provider-a",
        "time": {"created": 2_000, "completed": 2_200},
    }
    assert current.seq == next_started.seq
    assert current.data == {"content": [], "time": {"created": 2_200}}

    other_session = _session(path, "ses_s2_cross_session_supersession")
    other_assistant = create_session_message_id()
    _publish(
        service,
        other_session.id,
        STEP_STARTED,
        assistant_message_id=other_assistant,
        timestamp=3_000,
    )
    assert _assistant(store, stale_session.id, stale_b).data["time"].get("completed") is None
    assert _assistant(store, other_session.id, other_assistant).data["time"].get("completed") is None

    latest_session = _session(path, "ses_s2_latest_only_supersession")
    latest_a = create_session_message_id()
    latest_b = create_session_message_id()
    latest_c = create_session_message_id()
    _publish(
        service,
        latest_session.id,
        STEP_STARTED,
        assistant_message_id=latest_a,
        timestamp=4_000,
    )
    _publish(
        service,
        latest_session.id,
        STEP_STARTED,
        assistant_message_id=latest_b,
        timestamp=4_100,
    )
    with service.events.database.write_transaction() as connection:
        row = connection.execute(
            "SELECT data FROM session_message WHERE id = ? AND session_id = ?",
            (latest_a, latest_session.id),
        ).fetchone()
        abnormal = json.loads(str(row["data"]))
        abnormal["time"].pop("completed", None)
        connection.execute(
            "UPDATE session_message SET data = ? WHERE id = ? AND session_id = ?",
            (
                json.dumps(abnormal, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                latest_a,
                latest_session.id,
            ),
        )
    _publish(
        service,
        latest_session.id,
        STEP_STARTED,
        assistant_message_id=latest_c,
        timestamp=4_200,
    )
    assert _assistant(store, latest_session.id, latest_a).data["time"].get("completed") is None
    assert _assistant(store, latest_session.id, latest_b).data["time"]["completed"] == 4_200
    assert _assistant(store, latest_session.id, latest_c).data["time"].get("completed") is None

    rollback_session = _session(path, "ses_s2_supersession_rollback")
    rollback_a = create_session_message_id()
    rollback_started = _publish(
        service,
        rollback_session.id,
        STEP_STARTED,
        assistant_message_id=rollback_a,
        timestamp=5_000,
    )
    events_before = len(service.events.read_aggregate(rollback_session.id))
    try:
        _publish(
            service,
            rollback_session.id,
            STEP_STARTED,
            assistant_message_id=rollback_a,
            timestamp=5_100,
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate Assistant insert did not fail")
    rolled_back = _assistant(store, rollback_session.id, rollback_a)
    assert rolled_back.data["time"] == {"created": 5_000}
    assert service.events.latest_sequence(rollback_session.id) == rollback_started.seq
    assert len(service.events.read_aggregate(rollback_session.id)) == events_before


def test_agent_loop_projects_provider_and_tool_history(root: Path) -> None:
    previous = os.environ.get("HORIZON_USER_DATA_ROOT")
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "agent-data")
    runtime = SimpleNamespace(
        snapshot=lambda: (
            MCPRegistry(),
            MCPRuntimeStatus(enabled=False, config_path=""),
        )
    )
    tool_call = SimpleNamespace(
        id="call_workspace_status",
        function=SimpleNamespace(name="get_workspace_status", arguments="{}"),
    )
    provider = MockProvider(
        responses=[
            assistant_message("", [tool_call]),
            assistant_message("workspace inspected"),
        ]
    )
    try:
        agent = build_agent_for_workspace(
            "history-user",
            "history-project",
            llm_factory=lambda: provider,
            runtime_manager_factory=lambda: runtime,
        )
        execution = SessionExecution(
            database=agent.session_store.database,
            runner_resolver=lambda _: SessionRunner(
                agent._run_session_work_item,
                database=agent.session_store.database,
            ),
        )
        sessions = SessionService(
            root / "agent-data" / "horizon.db",
            execution=execution,
        )
        sessions.prompt(agent.session_id, "inspect workspace", resume=False)
        assert execution.resume(agent.session_id) is None
        messages = SessionStore(root / "agent-data" / "horizon.db").messages(
            agent.session_id
        )
        assert [item.type for item in messages] == ["user", "assistant", "assistant"]
        first_assistant = messages[1]
        tool = next(
            item
            for item in first_assistant.data["content"]
            if item.get("type") == "tool"
        )
        assert tool["id"] == "call_workspace_status"
        assert tool["state"]["status"] == "completed"
        assert tool["state"]["observation"]["success"] is True
        assert messages[2].data["content"][0]["text"] == "workspace inspected"
        assert not any(item.type == "tool" for item in messages)
        assert [item.seq for item in messages] == sorted(item.seq for item in messages)

        blocked_provider = MockProvider(
            responses=[
                assistant_message(
                    "",
                    [
                        SimpleNamespace(
                            id="call_cross_workspace",
                            function=SimpleNamespace(
                                name="switch_workspace",
                                arguments=(
                                    '{"user_id":"history-user",'
                                    '"project_id":"other-project"}'
                                ),
                            ),
                        )
                    ],
                ),
                assistant_message("switch blocked"),
            ]
        )
        blocked_agent = build_agent_for_workspace(
            "history-user",
            "history-project",
            llm_factory=lambda: blocked_provider,
            runtime_manager_factory=lambda: runtime,
        )
        blocked_execution = SessionExecution(
            database=blocked_agent.session_store.database,
            runner_resolver=lambda _: SessionRunner(
                blocked_agent._run_session_work_item,
                database=blocked_agent.session_store.database,
            ),
        )
        blocked_sessions = SessionService(
            root / "agent-data" / "horizon.db",
            execution=blocked_execution,
        )
        blocked_sessions.prompt(
            blocked_agent.session_id,
            "switch away",
            resume=False,
        )
        assert blocked_execution.resume(blocked_agent.session_id) is None
        blocked_messages = SessionStore(
            root / "agent-data" / "horizon.db"
        ).messages(blocked_agent.session_id)
        blocked_tool = next(
            item
            for item in blocked_messages[1].data["content"]
            if item.get("type") == "tool"
        )
        assert blocked_tool["state"]["status"] == "error"
        assert blocked_tool["state"]["observation_status"] == "blocked"
        assert blocked_tool["state"]["error_code"] == "session_workspace_mismatch"
        assert blocked_tool["state"]["real_execution"] is False
        assert blocked_tool["state"]["tool_executed"] is False
    finally:
        if previous is None:
            os.environ.pop("HORIZON_USER_DATA_ROOT", None)
        else:
            os.environ["HORIZON_USER_DATA_ROOT"] = previous


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        test_text_and_stable_sequence(root / "text.db")
        test_tool_lifecycle_and_isolation(root / "tools.db")
        test_projection_rollback_and_runtime_note_exclusion(root / "rollback.db")
        test_step_started_supersedes_latest_incomplete_assistant(
            root / "supersession.db"
        )
        test_agent_loop_projects_provider_and_tool_history(root)
    print("smoke_session_history ok")


if __name__ == "__main__":
    main()
