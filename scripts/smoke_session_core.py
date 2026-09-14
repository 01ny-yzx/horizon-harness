"""Focused S1 smoke for durable Session identity and event projection."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from inspect import signature
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import DatabaseService
from core.agent_factory import build_agent_for_workspace
from core.mcp_registry import MCPRegistry
from core.mcp_runtime import MCPRuntimeStatus
from core.session import (
    SessionIdentityConflict,
    SessionService,
    SessionWorkspaceMismatchError,
)
import core.session_event as session_event_module
from core.session_event import SessionEventPublisher, UnregisteredSessionEventError
from core.session_projector import (
    SESSION_CREATED,
    SESSION_EVENT_PROJECTORS,
    project_session_event,
)
from core.session_store import SessionNotFoundError, SessionStore
from core.state import TaskState
from core.workspace_runtime import get_current_workspace
from providers.mock import MockProvider


def _create(
    service: SessionService,
    *,
    session_id: str | None = None,
    user_id: str = "user-a",
    project_id: str = "project-a",
    directory: Path,
):
    return service.create(
        session_id=session_id,
        user_id=user_id,
        project_id=project_id,
        workspace_id=f"{user_id}/{project_id}",
        directory=directory,
        title="S1 smoke",
    )


def _count(database: DatabaseService, sql: str, parameters: tuple = ()) -> int:
    with database.read_transaction() as connection:
        return int(connection.execute(sql, parameters).fetchone()[0])


def test_create_and_adopt(database_path: Path, root: Path) -> None:
    assert SESSION_EVENT_PROJECTORS[SESSION_CREATED] is project_session_event
    database = DatabaseService(database_path)
    service = SessionService(database=database)
    session = _create(service, directory=root)
    assert session.id.startswith("ses_")
    assert _count(database, "SELECT COUNT(*) FROM session WHERE id = ?", (session.id,)) == 1
    assert _count(database, "SELECT COUNT(*) FROM event WHERE aggregate_id = ?", (session.id,)) == 1
    assert service.events.latest_sequence(session.id) == 0
    event = service.events.read_aggregate(session.id)[0]
    assert event.seq == 0 and event.type == SESSION_CREATED

    adopted = _create(service, session_id=session.id, directory=root)
    assert adopted == session
    assert _count(database, "SELECT COUNT(*) FROM event WHERE aggregate_id = ?", (session.id,)) == 1
    assert service.events.latest_sequence(session.id) == 0

    try:
        _create(
            service,
            session_id=session.id,
            project_id="different-project",
            directory=root,
        )
    except SessionIdentityConflict:
        pass
    else:
        raise AssertionError("conflicting Session identity was silently reinterpreted")


def test_independent_sessions_and_store(database_path: Path, root: Path) -> None:
    service = SessionService(database_path)
    first = _create(service, directory=root)
    second = _create(service, directory=root)
    other_project = _create(
        service,
        user_id="user-a",
        project_id="project-b",
        directory=root,
    )
    other_user = _create(
        service,
        user_id="user-b",
        project_id="project-a",
        directory=root,
    )
    assert first.id != second.id
    assert service.events.latest_sequence(first.id) == 0
    assert service.events.latest_sequence(second.id) == 0

    reopened = SessionStore(database=DatabaseService(database_path))
    assert reopened.get(first.id) == first
    project_sessions = reopened.list(user_id="user-a", project_id="project-a")
    assert {item.id for item in project_sessions} == {first.id, second.id}
    assert [(item.time_created, item.id) for item in project_sessions] == sorted(
        (item.time_created, item.id) for item in project_sessions
    )
    assert {item.id for item in reopened.list(user_id="user-a")} == {
        first.id,
        second.id,
        other_project.id,
    }
    assert other_user.id not in {item.id for item in project_sessions}
    try:
        reopened.list(project_id="project-a")
    except ValueError as exc:
        assert "requires user_id" in str(exc)
    else:
        raise AssertionError("project-only Session listing crossed user scope")
    try:
        reopened.get("ses_missing")
    except SessionNotFoundError:
        pass
    else:
        raise AssertionError("missing Session did not report not found")


def test_projector_failure_rolls_back(database_path: Path, root: Path) -> None:
    database = DatabaseService(database_path)
    publisher = SessionEventPublisher(database=database)
    session_id = "ses_projector_failure"
    payload = {
        "id": session_id,
        "user_id": "user-a",
        "project_id": "project-a",
        "workspace_id": "user-a/project-a",
        "directory": str(root.resolve()),
        "title": "must rollback",
        "time_created": 1,
        "time_updated": 1,
    }

    def failing_projector(connection: sqlite3.Connection, event) -> None:
        project_session_event(connection, event)
        raise RuntimeError("forced projector failure")

    assert "projector" not in signature(publisher.publish).parameters
    with patch.object(
        session_event_module,
        "_projector_for_event",
        return_value=failing_projector,
    ):
        try:
            publisher.publish(
                aggregate_id=session_id,
                event_type=SESSION_CREATED,
                data=payload,
            )
        except RuntimeError as exc:
            assert str(exc) == "forced projector failure"
        else:
            raise AssertionError("projector failure did not escape")
    assert _count(database, "SELECT COUNT(*) FROM session WHERE id = ?", (session_id,)) == 0
    assert _count(database, "SELECT COUNT(*) FROM event WHERE aggregate_id = ?", (session_id,)) == 0
    assert _count(database, "SELECT COUNT(*) FROM event_sequence WHERE aggregate_id = ?", (session_id,)) == 0

    unknown_id = "ses_unregistered_event"
    try:
        publisher.publish(
            aggregate_id=unknown_id,
            event_type="UnregisteredSessionEvent",
            data={"id": unknown_id},
        )
    except UnregisteredSessionEventError:
        pass
    else:
        raise AssertionError("unregistered durable event was committed")
    assert _count(database, "SELECT COUNT(*) FROM event WHERE aggregate_id = ?", (unknown_id,)) == 0
    assert _count(database, "SELECT COUNT(*) FROM event_sequence WHERE aggregate_id = ?", (unknown_id,)) == 0


def test_concurrent_explicit_identity(database_path: Path, root: Path) -> None:
    session_id = "ses_concurrent_identity"
    workers = 8
    barrier = threading.Barrier(workers)

    def create_once(_: int):
        service = SessionService(database_path)
        barrier.wait(timeout=10)
        return _create(service, session_id=session_id, directory=root)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        sessions = list(executor.map(create_once, range(workers)))
    assert {item.id for item in sessions} == {session_id}
    database = DatabaseService(database_path)
    database.ensure_ready()
    assert _count(database, "SELECT COUNT(*) FROM session WHERE id = ?", (session_id,)) == 1
    assert _count(database, "SELECT COUNT(*) FROM event WHERE aggregate_id = ?", (session_id,)) == 1
    assert _count(database, "SELECT seq FROM event_sequence WHERE aggregate_id = ?", (session_id,)) == 0


def test_agent_factory_binds_durable_session(root: Path) -> None:
    previous = os.environ.get("HORIZON_USER_DATA_ROOT")
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "agent-data")
    runtime = SimpleNamespace(
        snapshot=lambda: (
            MCPRegistry(),
            MCPRuntimeStatus(enabled=False, config_path=""),
        )
    )
    try:
        agent = build_agent_for_workspace(
            "factory-user",
            "factory-project",
            llm_factory=MockProvider,
            runtime_manager_factory=lambda: runtime,
        )
        assert agent.session_id.startswith("ses_")
        stored = SessionStore(root / "agent-data" / "horizon.db").get(agent.session_id)
        assert stored.user_id == "factory-user"
        assert stored.project_id == "factory-project"
        assert stored.workspace_id == "factory-user/factory-project"
        assert agent.workspace.workspace_id == stored.workspace_id
        assert agent.persistent_memory.user_id == stored.user_id
        assert agent.persistent_memory.project_id == stored.project_id

        agent._assert_session_workspace_request(
            user_id="factory-user",
            project_id="factory-project",
        )

        original_workspace = agent.workspace
        original_memory = agent.persistent_memory
        for different_user, different_project in (
            ("factory-user", "other-project"),
            ("other-user", "factory-project"),
        ):
            try:
                agent.run(
                    "cross scope",
                    user_id=different_user,
                    project_id=different_project,
                )
            except SessionWorkspaceMismatchError:
                pass
            else:
                raise AssertionError("Session-bound run changed workspace identity")
            assert agent.workspace is original_workspace
            assert agent.persistent_memory is original_memory
            assert agent.persistent_memory.user_id == stored.user_id
            assert agent.persistent_memory.project_id == stored.project_id
            assert get_current_workspace().workspace_id == "factory-user/factory-project"
            assert SessionStore(root / "agent-data" / "horizon.db").get(agent.session_id) == stored

        task_state = TaskState.create("workspace binding", "simple", [])
        task_state.user_id = stored.user_id
        task_state.project_id = stored.project_id
        task_state.workspace_id = stored.workspace_id
        dispatched: list[dict[str, str]] = []

        def switch_workspace(user_id: str, project_id: str) -> dict[str, object]:
            dispatched.append({"user_id": user_id, "project_id": project_id})
            return {"success": True, "data": {"workspace_id": f"{user_id}/{project_id}"}}

        agent.tools["switch_workspace"] = switch_workspace
        for target in (
            {"user_id": "factory-user", "project_id": "other-project"},
            {"user_id": "other-user", "project_id": "factory-project"},
        ):
            blocked = agent._execute_tool(
                task_state,
                "switch_workspace",
                json.dumps(target),
            )
            assert blocked["success"] is False
            assert blocked["status"] == "blocked"
            assert blocked["error_code"] == "session_workspace_mismatch"
            assert blocked["data"]["real_execution"] is False
            assert blocked["data"]["tool_executed"] is False
            assert dispatched == []
            assert agent.workspace is original_workspace
            assert get_current_workspace().workspace_id == stored.workspace_id
            assert task_state.workspace_id == stored.workspace_id
            assert SessionStore(root / "agent-data" / "horizon.db").get(agent.session_id) == stored

        allowed = agent._execute_tool(
            task_state,
            "switch_workspace",
            json.dumps(
                {"user_id": "factory-user", "project_id": "factory-project"}
            ),
        )
        assert allowed["success"] is True
        assert dispatched == [
            {"user_id": "factory-user", "project_id": "factory-project"}
        ]
        assert agent.workspace.workspace_id == stored.workspace_id
        assert task_state.workspace_id == stored.workspace_id
    finally:
        if previous is None:
            os.environ.pop("HORIZON_USER_DATA_ROOT", None)
        else:
            os.environ["HORIZON_USER_DATA_ROOT"] = previous


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        test_create_and_adopt(root / "create.db", root)
        test_independent_sessions_and_store(root / "store.db", root)
        test_projector_failure_rolls_back(root / "rollback.db", root)
        test_concurrent_explicit_identity(root / "concurrent.db", root)
        test_agent_factory_binds_durable_session(root)
    print("smoke_session_core ok")


if __name__ == "__main__":
    main()
