"""Focused S5 smoke for explicit Session reopen and crash boundaries."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("AGENT_ACCESS_MODE", "full_access")
os.environ.setdefault("MCP_ENABLED", "false")
os.environ.setdefault("EMBEDDING_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import DatabaseService
from core.session import SessionService
from core.session_execution import SessionExecution
from core.session_message import create_session_message_id, create_text_id
from core.session_message_updater import (
    PROMPTED,
    STEP_ENDED,
    STEP_STARTED,
    TEXT_ENDED,
    TEXT_STARTED,
    TOOL_CALLED,
    TOOL_FAILED,
    TOOL_INPUT_ENDED,
    TOOL_INPUT_STARTED,
    TOOL_SUCCESS,
)
from core.session_runner import SessionRunner
from core.session_store import SessionStore


def _service(path: Path, *, execution=None) -> SessionService:
    return SessionService(database=DatabaseService(path), execution=execution)


def _create(service: SessionService, session_id: str):
    return service.create(
        session_id=session_id,
        user_id="restore-user",
        project_id="restore-project",
        workspace_id="restore-user/restore-project",
        directory=ROOT,
        title="S5 restoration smoke",
    )


def _publish(service: SessionService, session_id: str, kind: str, **data):
    return service.events.publish(
        aggregate_id=session_id,
        event_type=kind,
        data={"session_id": session_id, **data},
    )


def _complete_assistant(service: SessionService, session_id: str, text: str) -> str:
    assistant_id = create_session_message_id()
    _publish(service, session_id, STEP_STARTED, assistant_message_id=assistant_id)
    text_id = create_text_id()
    _publish(
        service,
        session_id,
        TEXT_STARTED,
        assistant_message_id=assistant_id,
        text_id=text_id,
    )
    _publish(
        service,
        session_id,
        TEXT_ENDED,
        assistant_message_id=assistant_id,
        text_id=text_id,
        text=text,
    )
    _publish(service, session_id, STEP_ENDED, assistant_message_id=assistant_id)
    return assistant_id


def _runner_execution(path: Path, work) -> SessionExecution:
    database = DatabaseService(path)
    return SessionExecution(
        database=database,
        runner_resolver=lambda _: SessionRunner(work, database=database),
    )


def _wait_idle(execution: SessionExecution, session_id: str) -> None:
    deadline = time.time() + 3
    while session_id in execution.active():
        assert time.time() < deadline
        time.sleep(0.005)


def test_reopen_and_finite_history(path: Path) -> None:
    process_a = _service(path)
    session = _create(process_a, "ses_s5_reopen")
    admitted = process_a.prompt(
        session.id,
        "persist me",
        message_id="msg_s5_reopen_user",
        resume=False,
    )
    process_a.inputs.promote_steers(session.id, admitted.admitted_seq)
    _complete_assistant(process_a, session.id, "durable answer")
    expected_context = [item.to_dict() for item in process_a.context(session.id)]
    expected_event_count = len(process_a.events.read_aggregate(session.id))

    resolutions: list[str] = []
    process_b_execution = SessionExecution(
        database=DatabaseService(path),
        runner_resolver=lambda stored: resolutions.append(stored.id),
    )
    process_b = SessionService(
        database=DatabaseService(path),
        execution=process_b_execution,
    )
    assert process_b_execution.active() == set()
    assert process_b.get(session.id).id == session.id
    assert session.id in {item.id for item in process_b.list()}
    assert [item.to_dict() for item in process_b.context(session.id)] == expected_context
    assert resolutions == []
    assert len(process_b.events.read_aggregate(session.id)) == expected_event_count

    seen = []
    after = None
    while True:
        page = process_b.history(session.id, after=after, limit=2)
        seen.extend(page.events)
        if not page.has_more:
            break
        after = page.events[-1].seq
    assert [event.seq for event in seen] == list(range(expected_event_count))
    assert len({event.seq for event in seen}) == expected_event_count
    try:
        process_b.history(session.id, limit=101)
    except ValueError as exc:
        assert "100" in str(exc)
    else:
        raise AssertionError("history accepted a page larger than 100")
    assert len(process_b.events.read_aggregate(session.id)) == expected_event_count


def test_pending_input_requires_explicit_resume(path: Path) -> None:
    process_a = _service(path)
    session = _create(process_a, "ses_s5_pending")
    admitted = process_a.prompt(
        session.id,
        "resume pending",
        message_id="msg_s5_pending",
        resume=False,
    )
    calls: list[str] = []
    process_b: SessionService

    def work(text: str, promotion: str | None, cancelled: threading.Event) -> None:
        assert not cancelled.is_set()
        if promotion == "queue":
            process_b.inputs.promote_next_queued(session.id)
        cutoff = process_b.events.latest_sequence(session.id)
        process_b.inputs.promote_steers(session.id, cutoff)
        calls.append(text)
        _complete_assistant(process_b, session.id, "resumed answer")

    execution = _runner_execution(path, work)
    process_b = SessionService(database=DatabaseService(path), execution=execution)
    assert execution.active() == set()
    assert process_b.get(session.id).id == session.id
    assert process_b.context(session.id) == []
    assert calls == []
    assert process_b.resume(session.id) is None
    assert calls == ["resume pending"]
    assert process_b.inputs.find(admitted.id).promoted_seq is not None
    assert [item.type for item in process_b.context(session.id)] == ["user", "assistant"]


def test_promoted_wake_and_partial_assistant_boundary(path: Path) -> None:
    process_a = _service(path)
    session = _create(process_a, "ses_s5_partial")
    admitted = process_a.prompt(
        session.id,
        "already promoted",
        message_id="msg_s5_promoted",
        resume=False,
    )
    process_a.inputs.promote_steers(session.id, admitted.admitted_seq)
    old_assistant = create_session_message_id()
    _publish(process_a, session.id, STEP_STARTED, assistant_message_id=old_assistant)
    old_text = create_text_id()
    _publish(
        process_a,
        session.id,
        TEXT_STARTED,
        assistant_message_id=old_assistant,
        text_id=old_text,
    )
    _publish(
        process_a,
        session.id,
        TEXT_ENDED,
        assistant_message_id=old_assistant,
        text_id=old_text,
        text="partial output",
    )
    before = len(process_a.events.read_aggregate(session.id))
    provider_calls: list[str] = []
    new_assistants: list[str] = []
    process_b: SessionService

    def work(text: str, promotion: str | None, cancelled: threading.Event) -> None:
        provider_calls.append(text)
        new_assistants.append(_complete_assistant(process_b, session.id, "new turn"))

    execution = _runner_execution(path, work)
    process_b = SessionService(database=DatabaseService(path), execution=execution)
    assert len(process_b.context(session.id)) == 2
    execution.wake(session.id)
    _wait_idle(execution, session.id)
    assert provider_calls == []
    assert len(process_b.events.read_aggregate(session.id)) == before
    assert process_b.resume(session.id) is None
    assert provider_calls == ["already promoted"], provider_calls
    assert len(new_assistants) == 1 and new_assistants[0] != old_assistant
    messages = {item.id: item for item in process_b.context(session.id)}
    assert messages[old_assistant].data["time"].get("completed") is not None
    assert messages[new_assistants[0]].data["content"][0]["text"] == "new turn"
    assert sum(
        event.type == PROMPTED and event.data.get("message_id") == admitted.id
        for event in process_b.events.read_aggregate(session.id)
    ) == 1


def test_interrupted_tools_are_settled_once_without_replay(path: Path) -> None:
    process_a = _service(path)
    session = _create(process_a, "ses_s5_tools")
    admitted = process_a.prompt(
        session.id,
        "tool crash",
        message_id="msg_s5_tool_user",
        resume=False,
    )
    process_a.inputs.promote_steers(session.id, admitted.admitted_seq)
    assistant_id = create_session_message_id()
    _publish(process_a, session.id, STEP_STARTED, assistant_message_id=assistant_id)
    for call_id in ("call_running", "call_completed"):
        _publish(
            process_a,
            session.id,
            TOOL_INPUT_STARTED,
            assistant_message_id=assistant_id,
            call_id=call_id,
            name="write_file",
        )
        _publish(
            process_a,
            session.id,
            TOOL_INPUT_ENDED,
            assistant_message_id=assistant_id,
            call_id=call_id,
            text='{"path":"out.txt"}',
        )
        _publish(
            process_a,
            session.id,
            TOOL_CALLED,
            assistant_message_id=assistant_id,
            call_id=call_id,
            name="write_file",
            input={"path": "out.txt"},
        )
    _publish(
        process_a,
        session.id,
        TOOL_SUCCESS,
        assistant_message_id=assistant_id,
        call_id="call_completed",
        observation={"success": True, "status": "success", "data": {}},
    )
    old_executor_calls = 0
    provider_calls = 0
    process_b: SessionService

    def work(text: str, promotion: str | None, cancelled: threading.Event) -> None:
        nonlocal provider_calls
        provider_calls += 1
        assistant = next(item for item in process_b.context(session.id) if item.id == assistant_id)
        tools = {item["id"]: item for item in assistant.data["content"]}
        assert tools["call_running"]["state"]["status"] == "error"
        assert tools["call_completed"]["state"]["status"] == "completed"
        _complete_assistant(process_b, session.id, f"continued {provider_calls}")

    execution = _runner_execution(path, work)
    process_b = SessionService(database=DatabaseService(path), execution=execution)
    failed_before = sum(
        event.type == TOOL_FAILED for event in process_b.events.read_aggregate(session.id)
    )
    process_b.context(session.id)
    execution.wake(session.id)
    _wait_idle(execution, session.id)
    assert old_executor_calls == provider_calls == 0
    assert sum(
        event.type == TOOL_FAILED for event in process_b.events.read_aggregate(session.id)
    ) == failed_before

    process_b.resume(session.id)
    assert old_executor_calls == 0 and provider_calls == 1
    failed_once = sum(
        event.type == TOOL_FAILED for event in process_b.events.read_aggregate(session.id)
    )
    assert failed_once == failed_before + 1
    running = next(item for item in process_b.context(session.id) if item.id == assistant_id)
    running_tool = next(item for item in running.data["content"] if item["id"] == "call_running")
    assert running_tool["state"]["error"] == "Tool execution interrupted"
    assert running_tool["state"]["observation"]["data"] == {
        "execution_state_unknown": True,
        "replayed": False,
    }

    process_b.resume(session.id)
    assert old_executor_calls == 0 and provider_calls == 2
    assert sum(
        event.type == TOOL_FAILED for event in process_b.events.read_aggregate(session.id)
    ) == failed_once


def test_session_api_reopen_surface(path: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(path.parent)
    service = _service(path)
    session = _create(service, "ses_s5_api")
    service.prompt(
        session.id,
        "api reopen",
        message_id="msg_s5_api",
        resume=False,
    )

    calls = {"resume": 0, "interrupt": 0}

    class FakeExecution:
        def active(self):
            return {session.id}

        def resume(self, session_id: str) -> None:
            assert session_id == session.id
            calls["resume"] += 1

        def wake(self, session_id: str) -> None:
            raise AssertionError("read API unexpectedly woke execution")

        def interrupt(self, session_id: str) -> None:
            assert session_id == session.id
            calls["interrupt"] += 1

    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.routes import session as session_routes

    with patch("core.session_execution.get_session_execution", return_value=FakeExecution()):
        first_app = create_app()
        second_app = create_app()
        assert first_app is not second_app
        client = TestClient(second_app)
        auth = {"X-API-Key": "dev-local-key"}
        query = "user_id=restore-user&project_id=restore-project"
        assert client.get(f"/session?{query}", headers=auth).status_code == 200
        assert client.get(f"/session/active?{query}", headers=auth).json()["data"] == [session.id]
        assert client.get(f"/session/{session.id}?{query}", headers=auth).status_code == 200
        assert client.get(f"/session/{session.id}/context?{query}", headers=auth).status_code == 200
        history_calls: list[int | None] = []
        original_history = session_routes.SessionService.history

        def observed_history(service, session_id, *, after=None, limit=50):
            history_calls.append(after)
            return original_history(service, session_id, after=after, limit=limit)

        with patch.object(
            session_routes.SessionService,
            "history",
            new=observed_history,
        ):
            history = client.get(
                f"/session/{session.id}/history?{query}&limit=1",
                headers=auth,
            )
            assert history.status_code == 200
            assert history.json()["data"]["events"][0]["seq"] == 0
            assert history.json()["data"]["has_more"] is True

            after_zero = client.get(
                f"/session/{session.id}/history?{query}&after=0&limit=100",
                headers=auth,
            )
            assert after_zero.status_code == 200
            assert all(
                event["seq"] > 0
                for event in after_zero.json()["data"]["events"]
            )
            after_positive = client.get(
                f"/session/{session.id}/history?{query}&after=1&limit=100",
                headers=auth,
            )
            assert after_positive.status_code == 200
            assert all(
                event["seq"] > 1
                for event in after_positive.json()["data"]["events"]
            )
            calls_before_invalid = len(history_calls)
            assert client.get(
                f"/session/{session.id}/history?{query}&after=-1",
                headers=auth,
            ).status_code == 422
            assert client.get(
                f"/session/{session.id}/history?{query}&after=-100",
                headers=auth,
            ).status_code == 422
            assert client.get(
                f"/session/{session.id}/history?{query}&limit=101",
                headers=auth,
            ).status_code == 422
            assert len(history_calls) == calls_before_invalid
            assert history_calls == [None, 0, 1]
        body = {"user_id": "restore-user", "project_id": "restore-project"}
        assert client.post(f"/session/{session.id}/resume", json=body, headers=auth).json()["data"]["resumed"] is True
        assert client.post(f"/session/{session.id}/interrupt", json=body, headers=auth).json()["data"]["interrupted"] is True
        assert calls == {"resume": 1, "interrupt": 1}
        forbidden = client.get(
            f"/session/{session.id}?user_id=restore-user&project_id=other-project",
            headers=auth,
        )
        assert forbidden.status_code == 403


def main() -> None:
    previous = os.environ.get("HORIZON_USER_DATA_ROOT")
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.environ["HORIZON_USER_DATA_ROOT"] = str(root)
            test_reopen_and_finite_history(root / "reopen.db")
            test_pending_input_requires_explicit_resume(root / "pending.db")
            test_promoted_wake_and_partial_assistant_boundary(root / "partial.db")
            test_interrupted_tools_are_settled_once_without_replay(root / "tools.db")
            test_session_api_reopen_surface(root / "horizon.db")
    finally:
        if previous is None:
            os.environ.pop("HORIZON_USER_DATA_ROOT", None)
        else:
            os.environ["HORIZON_USER_DATA_ROOT"] = previous
    print("smoke_session_restoration ok")


if __name__ == "__main__":
    main()
