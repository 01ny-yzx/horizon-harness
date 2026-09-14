"""Focused S6 smoke for durable Session Context Epoch integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading

os.environ.setdefault("AGENT_ACCESS_MODE", "full_access")
os.environ.setdefault("MCP_ENABLED", "false")
os.environ.setdefault("EMBEDDING_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "openai_compatible")
os.environ.setdefault("LLM_MODEL", "deepseek-chat")
os.environ.setdefault("LLM_BASE_URL", "https://api.deepseek.com")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_factory import build_agent_for_workspace
from core.horizon_system_context import build_horizon_system_context_registry
from core.mcp_registry import MCPRegistry
from core.mcp_runtime import MCPRuntimeStatus
from core.session import SessionService
from core.session_context_epoch import SessionContextEpoch
from core.session_execution import SessionExecution
from core.session_history import SessionHistory
from core.session_input import SessionInputService
from core.session_message_updater import CONTEXT_UPDATED, PROMPTED, STEP_STARTED, SYNTHETIC
from core.session_runner import SessionRunner
from core.system_context import SystemContext, SystemContextInitializationBlocked, SystemContextSource, UNAVAILABLE
from core.system_context_registry import SystemContextRegistry, SystemContextRegistryEntry
from providers.mock import MockProvider, assistant_message


class _Memory:
    def __init__(self, instruction: str = "") -> None:
        self.success = True
        self.instruction = instruction
        self.counts = {"stable_fact": 0, "project_summary": 0, "task_history": 0}

    def load_all(self):
        return {"success": self.success}

    def format_instruction_context(self, max_chars=None):
        return self.instruction

    def get_memory_reference_counts(self):
        return dict(self.counts)

    def format_reference_guidance(self, max_chars=None):
        return "" if not any(self.counts.values()) else "reference guidance"


def registry(state: list[object]) -> SystemContextRegistry:
    result = SystemContextRegistry()
    result.register(
        SystemContextRegistryEntry(
            "test/entry",
            lambda: SystemContext(
                (
                    SystemContextSource(
                        key="test/source",
                        load=lambda: state[0],
                        baseline=lambda value: f"BASELINE:{value}",
                        update=lambda old, new: f"UPDATE:{old}->{new}",
                        removed=lambda old: f"REMOVED:{old}",
                    ),
                )
            ),
        )
    )
    return result


def runtime():
    class Runtime:
        @staticmethod
        def snapshot():
            return MCPRegistry(), MCPRuntimeStatus(enabled=False, config_path="")
    return Runtime()


def execution_for(agent) -> SessionExecution:
    return SessionExecution(
        database=agent.session_store.database,
        runner_resolver=lambda _session: SessionRunner(
            agent._run_session_work_item,
            database=agent.session_store.database,
        ),
    )


def test_epoch_and_provider(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "provider")
    provider = MockProvider(
        responses=[
            assistant_message("first-result"),
            assistant_message("second-result"),
            assistant_message("third-result"),
        ]
    )
    agent = build_agent_for_workspace(
        "s6-user", "s6-project", session_id="ses_s6_provider",
        llm_factory=lambda: provider, runtime_manager_factory=runtime,
    )
    state: list[object] = ["A"]
    agent.system_context_registry = registry(state)
    execution = execution_for(agent)
    sessions = SessionService(agent.workspace.database_path, execution=execution)

    first = sessions.prompt(agent.session_id, "first", resume=False)
    execution.resume(agent.session_id)
    epoch = agent.session_context_epoch.find(agent.session_id)
    assert epoch is not None and epoch.baseline == "BASELINE:A"
    events = sessions.events.read_aggregate(agent.session_id)
    prompted = next(event for event in events if event.type == PROMPTED and event.data["message_id"] == first.id)
    assert epoch.baseline_seq < prompted.seq
    assert "BASELINE:A" in str(provider.calls[0]["messages"])

    before_updates = sum(event.type == CONTEXT_UPDATED for event in events)
    second = sessions.prompt(agent.session_id, "second", resume=False)
    state[0] = "B"
    execution.resume(agent.session_id)
    events = sessions.events.read_aggregate(agent.session_id)
    assert sum(event.type == CONTEXT_UPDATED for event in events) == before_updates + 1
    prompted2 = next(event for event in events if event.type == PROMPTED and event.data["message_id"] == second.id)
    update = next(event for event in events if event.type == CONTEXT_UPDATED)
    step2 = [event for event in events if event.type == STEP_STARTED][-1]
    assert prompted2.seq < update.seq < step2.seq
    rendered = str(provider.calls[1]["messages"])
    assert "BASELINE:A" in rendered and "UPDATE:A->B" in rendered
    restarted = SessionContextEpoch(database=agent.session_store.database)
    assert restarted.find(agent.session_id).baseline == "BASELINE:A"
    restart_update_count = sum(event.type == CONTEXT_UPDATED for event in events)
    assert restarted.prepare(agent.session_id, agent.system_context_registry.load).baseline == "BASELINE:A"
    assert sum(
        event.type == CONTEXT_UPDATED
        for event in sessions.events.read_aggregate(agent.session_id)
    ) == restart_update_count
    agent._last_request_guidance_fingerprint = "must-not-be-session-authority"
    third = sessions.prompt(agent.session_id, "third", resume=False)
    execution.resume(agent.session_id)
    assert agent.session_inputs.find(third.id).promoted_seq is not None
    final_events = sessions.events.read_aggregate(agent.session_id)
    assert sum(event.type == CONTEXT_UPDATED for event in final_events) == before_updates + 1


def test_initial_unavailable(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "unavailable")
    provider = MockProvider(responses=[assistant_message("unused")])
    agent = build_agent_for_workspace(
        "s6-user", "s6-unavailable", session_id="ses_s6_unavailable",
        llm_factory=lambda: provider, runtime_manager_factory=runtime,
    )
    agent.system_context_registry = registry([UNAVAILABLE])
    execution = execution_for(agent)
    admitted = SessionService(agent.workspace.database_path, execution=execution).prompt(
        agent.session_id, "pending", resume=False
    )
    try:
        execution.resume(agent.session_id)
    except SystemContextInitializationBlocked:
        pass
    else:
        raise AssertionError("initial unavailable context did not block")
    assert agent.session_inputs.find(admitted.id).promoted_seq is None
    assert agent.session_context_epoch.find(agent.session_id) is None
    assert provider.calls == []


def test_atomic_context_update(root: Path) -> None:
    database = root / "atomic.db"
    sessions = SessionService(database)
    session = sessions.create(
        session_id="ses_s6_atomic", user_id="u", project_id="p",
        workspace_id="u/p", directory=root,
    )
    epochs = SessionContextEpoch(database)
    epochs.initialize(session.id, lambda: registry(["A"]).load())
    latest = sessions.events.latest_sequence(session.id)

    def fail_commit(_connection, _event):
        raise RuntimeError("snapshot advance failed")

    try:
        sessions.events.publish(
            aggregate_id=session.id,
            event_type=CONTEXT_UPDATED,
            data={
                "session_id": session.id,
                "message_id": "msg_s6_atomic",
                "timestamp": 1,
                "text": "should rollback",
            },
            commit=fail_commit,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("commit failure was swallowed")
    assert sessions.events.latest_sequence(session.id) == latest
    assert all(message.id != "msg_s6_atomic" for message in SessionHistory(database).load(session.id))

    state: list[object] = ["A"]
    reg = registry(state)
    # Replace the earlier test baseline with a source that can produce an update.
    with sessions.database.write_transaction() as connection:
        connection.execute("DELETE FROM session_context_epoch WHERE session_id = ?", (session.id,))
    epochs.initialize(session.id, reg.load)
    state[0] = "B"
    before = sessions.events.latest_sequence(session.id)
    with sessions.database.write_transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_epoch_snapshot_advance
            BEFORE UPDATE ON session_context_epoch
            BEGIN SELECT RAISE(ABORT, 'snapshot advance failed'); END
            """
        )
    try:
        epochs.prepare(session.id, reg.load)
    except Exception as exc:
        assert "snapshot advance failed" in str(exc)
    else:
        raise AssertionError("epoch snapshot failure was swallowed")
    assert sessions.events.latest_sequence(session.id) == before
    stored = epochs.find(session.id)
    assert stored.snapshot.sources["test/source"]["value"] == "A"
    assert not any(
        message.type == "system" and message.data.get("text") == "UPDATE:A->B"
        for message in SessionHistory(database).load(session.id)
    )


def test_unavailable_preserves_and_removal(root: Path) -> None:
    database = root / "preserve.db"
    sessions = SessionService(database)
    session = sessions.create(
        session_id="ses_s6_preserve", user_id="u", project_id="p",
        workspace_id="u/p", directory=root,
    )
    state: list[object] = ["A"]
    reg = registry(state)
    epoch = SessionContextEpoch(database)
    epoch.initialize(session.id, reg.load)
    state[0] = UNAVAILABLE
    prepared = epoch.prepare(session.id, reg.load)
    assert prepared.snapshot.sources["test/source"]["value"] == "A"
    assert not any(event.type == CONTEXT_UPDATED for event in sessions.events.read_aggregate(session.id))
    empty = SystemContextRegistry()
    epoch.prepare(session.id, empty.load)
    events = sessions.events.read_aggregate(session.id)
    assert [event.data["text"] for event in events if event.type == CONTEXT_UPDATED] == ["REMOVED:A"]


def _corrupt_instruction_snapshot(sessions: SessionService, session_id: str) -> None:
    with sessions.database.write_transaction() as connection:
        row = connection.execute(
            "SELECT snapshot FROM session_context_epoch WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        snapshot = json.loads(str(row["snapshot"]))
        snapshot["horizon/instructions"]["value"] = "not-a-list"
        connection.execute(
            "UPDATE session_context_epoch SET snapshot = ? WHERE session_id = ?",
            (
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                session_id,
            ),
        )


def test_production_replacement_ready(root: Path) -> None:
    directory = root / "replacement-ready"
    directory.mkdir()
    horizon = directory / "HORIZON.md"
    horizon.write_text("A", encoding="utf-8")
    database = root / "replacement-ready.db"
    sessions = SessionService(database)
    session = sessions.create(
        session_id="ses_s6_replacement_ready", user_id="u", project_id="p",
        workspace_id="u/p", directory=directory,
    )
    context = build_horizon_system_context_registry(session, _Memory())
    epoch = SessionContextEpoch(database)
    initial = epoch.initialize(session.id, context.load)
    assert initial is not None
    sessions.events.publish(
        aggregate_id=session.id,
        event_type=SYNTHETIC,
        data={
            "session_id": session.id,
            "message_id": "msg_s6_replacement_boundary",
            "timestamp": 2,
            "text": "boundary",
        },
    )
    expected_seq = sessions.events.latest_sequence(session.id)
    horizon.write_text("B", encoding="utf-8")
    _corrupt_instruction_snapshot(sessions, session.id)
    before_updates = sum(
        event.type == CONTEXT_UPDATED for event in sessions.events.read_aggregate(session.id)
    )
    before_system = sum(
        message.type == "system" for message in SessionHistory(database).load(session.id)
    )
    prepared = epoch.prepare(session.id, context.load)
    instruction = prepared.snapshot.sources["horizon/instructions"]["value"]
    assert instruction == [{"path": str(horizon.resolve()), "content": "B"}]
    assert prepared.baseline_seq == expected_seq
    assert prepared.baseline != initial.baseline
    assert f"Instructions from: {horizon.resolve()}\nB" in prepared.baseline
    assert sum(
        event.type == CONTEXT_UPDATED for event in sessions.events.read_aggregate(session.id)
    ) == before_updates
    assert sum(
        message.type == "system" for message in SessionHistory(database).load(session.id)
    ) == before_system


def test_production_replacement_blocked(root: Path) -> None:
    directory = root / "replacement-blocked"
    directory.mkdir()
    (directory / "HORIZON.md").write_text("B", encoding="utf-8")
    database = root / "replacement-blocked.db"
    sessions = SessionService(database)
    session = sessions.create(
        session_id="ses_s6_replacement_blocked", user_id="u", project_id="p",
        workspace_id="u/p", directory=directory,
    )
    memory = _Memory("Persistent instructions:\n- retained")
    context = build_horizon_system_context_registry(session, memory)
    epoch = SessionContextEpoch(database)
    initial = epoch.initialize(session.id, context.load)
    assert initial is not None
    _corrupt_instruction_snapshot(sessions, session.id)
    stored = epoch.find(session.id)
    assert stored is not None
    memory.success = False
    before_updates = sum(
        event.type == CONTEXT_UPDATED for event in sessions.events.read_aggregate(session.id)
    )
    before_system = sum(
        message.type == "system" for message in SessionHistory(database).load(session.id)
    )
    prepared = epoch.prepare(session.id, context.load)
    assert prepared.baseline == stored.baseline
    assert prepared.snapshot.to_dict() == stored.snapshot.to_dict()
    assert prepared.baseline_seq == stored.baseline_seq
    assert sum(
        event.type == CONTEXT_UPDATED for event in sessions.events.read_aggregate(session.id)
    ) == before_updates
    assert sum(
        message.type == "system" for message in SessionHistory(database).load(session.id)
    ) == before_system


def test_production_instruction_update(root: Path) -> None:
    directory = root / "normal-update"
    directory.mkdir()
    horizon = directory / "HORIZON.md"
    horizon.write_text("A", encoding="utf-8")
    database = root / "normal-update.db"
    sessions = SessionService(database)
    session = sessions.create(
        session_id="ses_s6_normal_update", user_id="u", project_id="p",
        workspace_id="u/p", directory=directory,
    )
    context = build_horizon_system_context_registry(session, _Memory())
    epoch = SessionContextEpoch(database)
    initial = epoch.initialize(session.id, context.load)
    assert initial is not None
    horizon.write_text("B", encoding="utf-8")
    prepared = epoch.prepare(session.id, context.load)
    assert prepared.baseline == initial.baseline
    assert prepared.baseline_seq == initial.baseline_seq
    assert prepared.snapshot.sources["horizon/instructions"]["value"][0]["content"] == "B"
    assert sum(
        event.type == CONTEXT_UPDATED for event in sessions.events.read_aggregate(session.id)
    ) == 1


def main() -> None:
    with TemporaryDirectory() as raw:
        root = Path(raw)
        test_epoch_and_provider(root)
        test_initial_unavailable(root)
        test_atomic_context_update(root)
        test_unavailable_preserves_and_removal(root)
        test_production_replacement_ready(root)
        test_production_replacement_blocked(root)
        test_production_instruction_update(root)
    print("smoke_session_system_context ok")


if __name__ == "__main__":
    main()
