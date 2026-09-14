"""Focused S3 smoke for durable Session input admission and promotion."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

from core.agent_factory import build_agent_for_workspace
from core.database import DatabaseService
from core.mcp_registry import MCPRegistry
from core.mcp_runtime import MCPRuntimeStatus
from core.memory import Memory
from core.session import SessionService
from core.session_execution import SessionExecution
import core.session_event as session_event_module
from core.session_input import (
    PROMPT_ADMITTED,
    SessionInputService,
    SessionPromptConflictError,
    project_prompt_admitted_event,
)
from core.session_message_updater import PROMPTED, STEP_STARTED, SYNTHETIC
from core.session_store import SessionStore
from core.session_runner import SessionRunner
from providers.mock import MockProvider, assistant_message


def _session(database_path: Path, session_id: str):
    return SessionService(database_path).create(
        session_id=session_id,
        user_id="user-a",
        project_id="project-a",
        workspace_id="user-a/project-a",
        directory=database_path.parent,
        title="S3 smoke",
    )


def _publish(service: SessionService, session_id: str, kind: str, **data):
    return service.events.publish(
        aggregate_id=session_id,
        event_type=kind,
        data={"session_id": session_id, **data},
    )


def _count(database: DatabaseService, sql: str, parameters: tuple = ()) -> int:
    with database.read_transaction() as connection:
        return int(connection.execute(sql, parameters).fetchone()[0])


def test_admission_identity_conflicts_and_memory_boundary(database_path: Path) -> None:
    first = _session(database_path, "ses_s3_admission_a")
    second = _session(database_path, "ses_s3_admission_b")
    service = SessionInputService(database_path)
    database = service.database
    message_id = "msg_s3_admission"
    memory = Memory()
    before_memory = list(memory.messages)

    admitted = service.admit(
        first.id,
        {"text": "hello"},
        delivery="steer",
        message_id=message_id,
    )
    assert admitted.id == message_id
    assert admitted.prompt == {"text": "hello"}
    assert admitted.delivery == "steer"
    assert admitted.promoted_seq is None
    assert SessionStore(database_path).messages(first.id) == []
    assert memory.messages == before_memory
    events = SessionService(database_path).events.read_aggregate(first.id)
    assert events[-1].type == PROMPT_ADMITTED
    assert events[-1].data["message_id"] == admitted.id
    assert admitted.admitted_seq == events[-1].seq
    assert service.has_pending(first.id, "steer") is True
    assert service.has_pending(first.id, "queue") is False
    assert service.has_pending(second.id, "steer") is False

    retried = service.admit(
        first.id,
        {"text": "hello"},
        delivery="steer",
        message_id=message_id,
    )
    assert retried == admitted
    assert _count(database, "SELECT COUNT(*) FROM session_input WHERE id = ?", (message_id,)) == 1
    assert _count(
        database,
        "SELECT COUNT(*) FROM event WHERE type = ? AND json_extract(data, '$.message_id') = ?",
        (PROMPT_ADMITTED, message_id),
    ) == 1

    for conflict in (
        lambda: service.admit(first.id, {"text": "delete database"}, "steer", message_id),
        lambda: service.admit(first.id, {"text": "hello"}, "queue", message_id),
        lambda: service.admit(second.id, {"text": "hello"}, "steer", message_id),
    ):
        try:
            conflict()
        except SessionPromptConflictError:
            pass
        else:
            raise AssertionError("message_id semantic reuse did not raise PromptConflict")
    assert service.find(message_id) == admitted

    visible_id = "msg_s3_visible_history"
    _publish(
        SessionService(database_path),
        first.id,
        SYNTHETIC,
        message_id=visible_id,
        text="already visible",
    )
    try:
        service.admit(first.id, "new prompt", message_id=visible_id)
    except SessionPromptConflictError:
        pass
    else:
        raise AssertionError("visible Session message ID was reused for admission")
    assert service.find(visible_id) is None

    generated_a = service.admit(first.id, "hello")
    generated_b = service.admit(first.id, "hello")
    assert generated_a.id.startswith("msg_")
    assert generated_b.id.startswith("msg_")
    assert generated_a.id != generated_b.id


def test_promotion_identity_time_legacy_and_rollback(database_path: Path) -> None:
    session = _session(database_path, "ses_s3_promotion")
    sessions = SessionService(database_path)
    inputs = SessionInputService(database_path)
    message_id = "msg_s3_promotion"
    admitted_event = sessions.events.publish(
        aggregate_id=session.id,
        event_type=PROMPT_ADMITTED,
        data={
            "session_id": session.id,
            "message_id": message_id,
            "prompt": {"text": "promote me"},
            "delivery": "steer",
            "timestamp": 1_000,
        },
        time_created=1_000,
    )
    assert inputs.promote_steers(session.id, admitted_event.seq) == 1
    stored = inputs.find(message_id)
    assert stored is not None
    messages = SessionStore(database_path).messages(session.id)
    user = next(item for item in messages if item.id == message_id)
    events = sessions.events.read_aggregate(session.id)
    prompted = next(
        event
        for event in events
        if event.type == PROMPTED and event.data.get("message_id") == message_id
    )
    assert [event.type for event in events[-2:]] == [PROMPT_ADMITTED, PROMPTED]
    assert admitted_event.data["message_id"] == stored.id == prompted.data["message_id"] == user.id
    assert stored.admitted_seq == admitted_event.seq
    assert stored.promoted_seq == prompted.seq
    assert user.type == "user" and user.seq == prompted.seq
    assert stored.time_created == user.time_created == user.data["time"]["created"] == 1_000
    assert prompted.time_created >= 1_000
    assert prompted.data["timestamp"] == 1_000

    legacy_id = "msg_s3_legacy_direct"
    legacy = _publish(
        sessions,
        session.id,
        PROMPTED,
        message_id=legacy_id,
        text="legacy S2 prompt",
        timestamp=2_000,
    )
    legacy_input = inputs.find(legacy_id)
    legacy_user = next(
        item for item in SessionStore(database_path).messages(session.id) if item.id == legacy_id
    )
    assert legacy_input is not None
    assert legacy_input.admitted_seq == legacy_input.promoted_seq == legacy.seq
    assert legacy_input.prompt == {"text": "legacy S2 prompt"}
    assert legacy_input.delivery == "steer"
    assert legacy_user.seq == legacy.seq and legacy_user.time_created == 2_000

    rollback_id = "msg_s3_promotion_rollback"
    pending = inputs.admit(session.id, "must stay pending", message_id=rollback_id)
    latest = sessions.events.latest_sequence(session.id)
    with inputs.database.write_transaction() as connection:
        connection.execute(
            f"""
            CREATE TRIGGER fail_s3_user_insert
            BEFORE INSERT ON session_message
            WHEN NEW.id = '{rollback_id}'
            BEGIN
                SELECT RAISE(ABORT, 'forced user insert failure');
            END
            """
        )
    try:
        inputs.promote_steers(session.id, pending.admitted_seq)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Prompted projection failure did not roll back")
    assert inputs.find(rollback_id).promoted_seq is None
    assert sessions.events.latest_sequence(session.id) == latest
    assert not any(
        event.type == PROMPTED and event.data.get("message_id") == rollback_id
        for event in sessions.events.read_aggregate(session.id)
    )
    assert all(item.id != rollback_id for item in SessionStore(database_path).messages(session.id))


def test_ordering_cutoff_fifo_and_delivery_isolation(database_path: Path) -> None:
    session = _session(database_path, "ses_s3_ordering")
    inputs = SessionInputService(database_path)
    steer_a = inputs.admit(session.id, "steer A", "steer", "msg_s3_steer_a")
    steer_b = inputs.admit(session.id, "steer B", "steer", "msg_s3_steer_b")
    steer_c = inputs.admit(session.id, "steer C", "steer", "msg_s3_steer_c")
    queue_a = inputs.admit(session.id, "queue A", "queue", "msg_s3_queue_a")
    queue_b = inputs.admit(session.id, "queue B", "queue", "msg_s3_queue_b")
    queue_c = inputs.admit(session.id, "queue C", "queue", "msg_s3_queue_c")

    assert inputs.promote_steers(session.id, steer_b.admitted_seq) == 2
    assert inputs.find(steer_a.id).promoted_seq is not None
    assert inputs.find(steer_b.id).promoted_seq is not None
    assert inputs.find(steer_c.id).promoted_seq is None
    assert inputs.find(queue_a.id).promoted_seq is None
    first_users = [
        item.id for item in SessionStore(database_path).messages(session.id) if item.type == "user"
    ]
    assert first_users == [steer_a.id, steer_b.id]

    assert inputs.promote_next_queued(session.id) is True
    assert inputs.find(queue_a.id).promoted_seq is not None
    assert inputs.find(queue_b.id).promoted_seq is None
    assert inputs.find(queue_c.id).promoted_seq is None
    assert inputs.promote_next_queued(session.id) is True
    assert inputs.find(queue_b.id).promoted_seq is not None
    assert inputs.find(queue_c.id).promoted_seq is None
    assert inputs.find(steer_c.id).promoted_seq is None
    user_ids = [
        item.id for item in SessionStore(database_path).messages(session.id) if item.type == "user"
    ]
    assert user_ids == [steer_a.id, steer_b.id, queue_a.id, queue_b.id]


def test_concurrent_retry_and_promotion(database_path: Path) -> None:
    session = _session(database_path, "ses_s3_concurrent")
    message_id = "msg_s3_concurrent_admit"
    workers = 2
    barrier = threading.Barrier(workers)

    def admit_once(_: int):
        service = SessionInputService(database_path)
        barrier.wait(timeout=10)
        return service.admit(session.id, "same", "steer", message_id)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        admissions = list(executor.map(admit_once, range(workers)))
    assert admissions[0] == admissions[1]
    database = DatabaseService(database_path)
    database.ensure_ready()
    assert _count(database, "SELECT COUNT(*) FROM session_input WHERE id = ?", (message_id,)) == 1
    assert _count(
        database,
        "SELECT COUNT(*) FROM event WHERE type = ? AND json_extract(data, '$.message_id') = ?",
        (PROMPT_ADMITTED, message_id),
    ) == 1

    pending = admissions[0]
    promotion_barrier = threading.Barrier(workers)

    def promote_once(_: int):
        service = SessionInputService(database_path)
        promotion_barrier.wait(timeout=10)
        return service.promote_steers(session.id, pending.admitted_seq)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(promote_once, range(workers)))
    promoted = SessionInputService(database_path).find(message_id)
    assert promoted is not None and promoted.promoted_seq is not None
    assert _count(
        database,
        "SELECT COUNT(*) FROM event WHERE type = ? AND json_extract(data, '$.message_id') = ?",
        (PROMPTED, message_id),
    ) == 1
    assert _count(database, "SELECT COUNT(*) FROM session_message WHERE id = ?", (message_id,)) == 1


def test_prompt_admitted_transaction_rollback(database_path: Path) -> None:
    session = _session(database_path, "ses_s3_admission_rollback")
    inputs = SessionInputService(database_path)
    latest = inputs.events.latest_sequence(session.id)

    def failing_projector(connection: sqlite3.Connection, event) -> None:
        project_prompt_admitted_event(connection, event)
        raise RuntimeError("forced admission projection failure")

    with patch.object(
        session_event_module,
        "_projector_for_event",
        return_value=failing_projector,
    ):
        try:
            inputs.admit(session.id, "rollback", message_id="msg_s3_admit_rollback")
        except RuntimeError as exc:
            assert str(exc) == "forced admission projection failure"
        else:
            raise AssertionError("PromptAdmitted projection failure did not escape")
    assert inputs.find("msg_s3_admit_rollback") is None
    assert inputs.events.latest_sequence(session.id) == latest
    assert not any(
        event.data.get("message_id") == "msg_s3_admit_rollback"
        for event in inputs.events.read_aggregate(session.id)
    )


def test_agent_loop_admits_and_promotes_before_provider(root: Path) -> None:
    previous = os.environ.get("HORIZON_USER_DATA_ROOT")
    data_root = root / "agent-data"
    os.environ["HORIZON_USER_DATA_ROOT"] = str(data_root)
    runtime = SimpleNamespace(
        snapshot=lambda: (MCPRegistry(), MCPRuntimeStatus(enabled=False, config_path=""))
    )

    class BoundaryProvider(MockProvider):
        def __init__(self) -> None:
            super().__init__(responses=[assistant_message("boundary ok")])
            self.session_id = ""
            self.boundary_snapshots: list[list[str]] = []

        def chat(self, messages, tools, options=None):
            service = SessionService(data_root / "horizon.db")
            events = service.events.read_aggregate(self.session_id)
            relevant = [
                event.type
                for event in events
                if event.type in {PROMPT_ADMITTED, PROMPTED, STEP_STARTED}
            ]
            self.boundary_snapshots.append(relevant)
            assert relevant[:2] == [PROMPT_ADMITTED, PROMPTED], relevant
            assert STEP_STARTED not in relevant, relevant
            admitted_event = next(event for event in events if event.type == PROMPT_ADMITTED)
            prompted_event = next(event for event in events if event.type == PROMPTED)
            input_row = SessionInputService(data_root / "horizon.db").find(
                str(admitted_event.data["message_id"])
            )
            assert input_row is not None and input_row.promoted_seq == prompted_event.seq
            assert any(
                item.id == input_row.id
                for item in SessionStore(data_root / "horizon.db").messages(self.session_id)
            )
            return super().chat(messages, tools, options=options)

    provider = BoundaryProvider()
    try:
        agent = build_agent_for_workspace(
            "loop-user",
            "loop-project",
            llm_factory=lambda: provider,
            runtime_manager_factory=lambda: runtime,
        )
        provider.session_id = agent.session_id
        execution = SessionExecution(
            database=agent.session_store.database,
            runner_resolver=lambda _: SessionRunner(
                agent._run_session_work_item,
                database=agent.session_store.database,
            ),
        )
        sessions = SessionService(
            data_root / "horizon.db",
            execution=execution,
        )
        admitted = sessions.prompt(
            agent.session_id,
            "durable first",
            resume=False,
        )
        assert admitted.promoted_seq is None
        assert execution.resume(agent.session_id) is None
        assert provider.boundary_snapshots
        events = SessionService(data_root / "horizon.db").events.read_aggregate(agent.session_id)
        relevant = [
            event.type
            for event in events
            if event.type in {PROMPT_ADMITTED, PROMPTED, STEP_STARTED}
        ]
        assert relevant[:3] == [PROMPT_ADMITTED, PROMPTED, STEP_STARTED]
        admitted_id = next(
            event.data["message_id"] for event in events if event.type == PROMPT_ADMITTED
        )
        prompted_id = next(
            event.data["message_id"] for event in events if event.type == PROMPTED
        )
        assert admitted_id == prompted_id
    finally:
        if previous is None:
            os.environ.pop("HORIZON_USER_DATA_ROOT", None)
        else:
            os.environ["HORIZON_USER_DATA_ROOT"] = previous


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        test_admission_identity_conflicts_and_memory_boundary(root / "admission.db")
        test_promotion_identity_time_legacy_and_rollback(root / "promotion.db")
        test_ordering_cutoff_fifo_and_delivery_isolation(root / "ordering.db")
        test_concurrent_retry_and_promotion(root / "concurrent.db")
        test_prompt_admitted_transaction_rollback(root / "admission-rollback.db")
        test_agent_loop_admits_and_promotes_before_provider(root)
    print("smoke_session_input ok")


if __name__ == "__main__":
    main()
