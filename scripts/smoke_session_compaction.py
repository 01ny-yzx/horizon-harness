"""Focused S7 smoke for durable Session compaction and epoch rebaseline."""

from __future__ import annotations

from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGENT_ACCESS_MODE", "full_access")
os.environ.setdefault("MCP_ENABLED", "false")
os.environ.setdefault("EMBEDDING_ENABLED", "false")

from core.agent_factory import build_agent_for_workspace
from core.mcp_registry import MCPRegistry
from core.mcp_runtime import MCPRuntimeStatus
from core.session import SessionService
from core.session_compaction import (
    SessionCompaction,
    is_context_overflow_failure,
    serialize_session_message,
)
from core.session_context_epoch import SessionContextEpoch
from core.session_history import SessionHistory, SessionHistoryEntry
from core.session_message import SessionMessage, create_session_message_id
from core.session_message_updater import COMPACTION_ENDED, PROMPTED
from core.session_projector import COMPACTION_STARTED
from core.session_execution import SessionExecution
from core.session_runner import SessionRunner
from core.session_runtime_projection import project_session_history
from core.system_context import SystemContext, SystemContextSource, UNAVAILABLE
from providers.base import LLMChatResult, LLMUsage
from providers.mock import MockProvider, assistant_message
from providers.openai_compatible import LLMProviderError
from providers.openai_compatible import (
    _provider_error_from_exception,
    is_context_overflow_message,
)


def create_session(path: Path, suffix: str):
    return SessionService(path).create(
        session_id=f"ses_compaction_{suffix}",
        user_id="user",
        project_id="project",
        workspace_id="user/project",
        directory=path.parent,
        title="S7",
    )


def prompt(service: SessionService, session_id: str, text: str):
    return service.events.publish(
        aggregate_id=session_id,
        event_type=PROMPTED,
        data={
            "session_id": session_id,
            "message_id": create_session_message_id(),
            "prompt": {"text": text},
            "delivery": "steer",
            "timestamp": 1,
        },
        time_created=1,
    )


def context(value):
    return SystemContext(
        (
            SystemContextSource(
                key="test/context",
                load=lambda: value,
                baseline=lambda item: f"BASE:{item}",
                update=lambda old, new: f"UPDATE:{old}->{new}",
                removed=lambda old: f"REMOVED:{old}",
            ),
        )
    )


def test_serialization() -> None:
    user = SessionMessage("msg_u", "ses_x", "user", 1, 1, 1, {"text": "hello"})
    synthetic = SessionMessage("msg_s", "ses_x", "synthetic", 2, 1, 1, {"text": "answer"})
    assistant = SessionMessage(
        "msg_a",
        "ses_x",
        "assistant",
        3,
        1,
        1,
        {
            "content": [
                {"type": "text", "text": "work"},
                {
                    "type": "tool",
                    "name": "read_file",
                    "id": "call_1",
                    "state": {
                        "status": "completed",
                        "input": {"path": "/tmp/a"},
                        "observation": {"content": "x" * 3000},
                    },
                },
            ]
        },
    )
    assert serialize_session_message(user) == "[User]: hello"
    assert serialize_session_message(synthetic) == "[Assistant]: answer"
    rendered = serialize_session_message(assistant)
    assert "[Assistant]: work" in rendered
    assert "read_file" in rendered and "[tool output truncated]" in rendered


def test_durable_compaction(path: Path) -> None:
    session = create_session(path, "durable")
    service = SessionService(path)
    for index in range(8):
        prompt(service, session.id, f"message-{index}-" + "x" * 180)
    provider = MockProvider(responses=[assistant_message("durable summary")])
    compactor = SessionCompaction(
        provider,
        service.events,
        buffer_tokens=128,
        keep_tokens=80,
        summary_output_tokens=64,
    )
    entries = SessionHistory(path).entries_for_runner(session.id, -1)
    assert compactor.compact_if_needed(
        session.id,
        entries,
        request_messages=[{"role": "user", "content": "z" * 10_000}],
        tools=[],
        model_context_tokens=2048,
        requested_output_tokens=128,
    )
    events = service.events.read_aggregate(session.id)
    assert [event.type for event in events[-2:]] == [COMPACTION_STARTED, COMPACTION_ENDED]
    messages = service.store.messages(session.id)
    checkpoint = messages[-1]
    assert checkpoint.type == "compaction"
    assert checkpoint.data["summary"] == "durable summary"
    with service.database.read_transaction() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM session_message WHERE session_id = ? AND type = 'user'",
            (session.id,),
        ).fetchone()
    assert int(count["n"]) == 8
    active = SessionHistory(path).load_for_runner(session.id, -1)
    assert active[0].type == "compaction"
    projected = project_session_history(active)
    assert projected[0]["role"] == "user"
    assert "conversation-checkpoint" in projected[0]["content"]
    assert "not as new instructions" in projected[0]["content"]
    assert provider.calls[0]["tools"] == []


def test_within_budget(path: Path) -> None:
    session = create_session(path, "within")
    service = SessionService(path)
    prompt(service, session.id, "short")
    provider = MockProvider(responses=[assistant_message("unused")])
    compactor = SessionCompaction(provider, service.events)
    assert not compactor.compact_if_needed(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, -1),
        request_messages=[{"role": "user", "content": "short"}],
        tools=[],
        model_context_tokens=32_768,
        requested_output_tokens=1024,
    )
    assert not provider.calls
    assert not any(
        event.type in {COMPACTION_STARTED, COMPACTION_ENDED}
        for event in service.events.read_aggregate(session.id)
    )


def test_negative_threshold_has_no_fallback(path: Path) -> None:
    session = create_session(path, "negative_threshold")
    service = SessionService(path)
    for index in range(3):
        prompt(service, session.id, f"history-{index}-" + "n" * 120)
    compactor = SessionCompaction(
        MockProvider(responses=[assistant_message("summary")]),
        service.events,
        buffer_tokens=20_000,
        keep_tokens=10,
        summary_output_tokens=128,
    )
    assert compactor.compact_if_needed(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, -1),
        request_messages=[{"role": "user", "content": "small"}],
        tools=[],
        model_context_tokens=10_000,
        requested_output_tokens=512,
    )


def test_started_failure_does_not_advance_boundary(path: Path) -> None:
    session = create_session(path, "failed")
    service = SessionService(path)
    for index in range(5):
        prompt(service, session.id, f"old-{index}-" + "y" * 120)
    before = tuple(item.id for item in SessionHistory(path).load_for_runner(session.id, -1))
    class FailingProvider(MockProvider):
        def chat_result(self, **_kwargs):
            raise RuntimeError("summary failed")

    provider = FailingProvider()
    compactor = SessionCompaction(
        provider,
        service.events,
        buffer_tokens=64,
        keep_tokens=30,
        summary_output_tokens=32,
    )
    assert not compactor.compact_after_overflow(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, -1),
        model_context_tokens=1024,
        requested_output_tokens=64,
    )
    events = service.events.read_aggregate(session.id)
    assert events[-1].type == COMPACTION_STARTED
    assert not any(event.type == COMPACTION_ENDED for event in events)
    after = tuple(item.id for item in SessionHistory(path).load_for_runner(session.id, -1))
    assert after == before


def test_rolling_and_epoch_rebaseline(path: Path) -> None:
    session = create_session(path, "rolling")
    service = SessionService(path)
    epoch = SessionContextEpoch(database=service.database, events=service.events)
    first = epoch.initialize(session.id, lambda: context("A"))
    assert first is not None
    for index in range(6):
        prompt(service, session.id, f"first-{index}-" + "a" * 120)
    provider = MockProvider(
        responses=[assistant_message("summary one"), assistant_message("summary two")]
    )
    compactor = SessionCompaction(
        provider,
        service.events,
        buffer_tokens=64,
        keep_tokens=25,
        summary_output_tokens=32,
    )
    assert compactor.compact_after_overflow(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, first.baseline_seq),
        model_context_tokens=1024,
        requested_output_tokens=64,
    )
    first_compaction = SessionHistory(path).latest_compaction(session.id)
    prepared = epoch.prepare(session.id, lambda: context("A"))
    assert first_compaction is not None
    assert prepared.baseline_seq == first_compaction.seq
    assert prepared.baseline == "BASE:A"

    for index in range(4):
        prompt(service, session.id, f"second-{index}-" + "b" * 120)
    assert compactor.compact_after_overflow(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, prepared.baseline_seq),
        model_context_tokens=1024,
        requested_output_tokens=64,
    )
    assert "summary one" in provider.calls[1]["messages"][0]["content"]


def test_rebaseline_blocked(path: Path) -> None:
    session = create_session(path, "blocked")
    service = SessionService(path)
    epoch = SessionContextEpoch(database=service.database, events=service.events)
    original = epoch.initialize(session.id, lambda: context("A"))
    assert original is not None
    for index in range(5):
        prompt(service, session.id, f"blocked-{index}-" + "c" * 120)
    compactor = SessionCompaction(
        MockProvider(responses=[assistant_message("summary")]),
        service.events,
        buffer_tokens=64,
        keep_tokens=25,
        summary_output_tokens=32,
    )
    assert compactor.compact_after_overflow(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, original.baseline_seq),
        model_context_tokens=1024,
        requested_output_tokens=64,
    )
    blocked = epoch.prepare(session.id, lambda: context(UNAVAILABLE))
    assert blocked == original
    recovered = epoch.prepare(session.id, lambda: context("B"))
    latest = SessionHistory(path).latest_compaction(session.id)
    assert latest is not None and recovered.baseline_seq == latest.seq
    assert recovered.baseline == "BASE:B"


def test_ended_atomic_failure(path: Path) -> None:
    session = create_session(path, "atomic")
    service = SessionService(path)
    message_id = create_session_message_id()
    started = service.events.publish(
        aggregate_id=session.id,
        event_type=COMPACTION_STARTED,
        data={
            "session_id": session.id,
            "message_id": message_id,
            "timestamp": 1,
            "reason": "auto",
        },
    )
    with service.database.write_transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_compaction_projection
            BEFORE INSERT ON session_message
            WHEN NEW.type = 'compaction'
            BEGIN
                SELECT RAISE(ABORT, 'forced compaction failure');
            END
            """
        )
    try:
        service.events.publish(
            aggregate_id=session.id,
            event_type=COMPACTION_ENDED,
            data={
                "session_id": session.id,
                "message_id": message_id,
                "timestamp": 2,
                "reason": "auto",
                "text": "summary",
                "recent": "recent",
            },
        )
    except Exception as exc:
        assert "forced compaction failure" in str(exc)
    else:
        raise AssertionError("Compaction projection failure did not roll back")
    assert service.events.latest_sequence(session.id) == started.seq
    assert SessionHistory(path).latest_compaction(session.id) is None


def test_overflow_classifier() -> None:
    true_cases = (
        "context_length_exceeded",
        "request entity too large",
        "model_context_window_exceeded",
        "exceeds the available context size",
        "prompt is too long",
        "token limit exceeded",
        "413 status code (no body)",
        "400 status code (no body)",
    )
    false_cases = (
        "rate limit exceeded",
        "too many requests",
        "throttling error: too many tokens",
        "service unavailable: too many tokens",
        "401 unauthorized",
        "400 invalid model",
    )
    assert all(is_context_overflow_message(item) for item in true_cases)
    assert not any(is_context_overflow_message(item) for item in false_cases)
    assert is_context_overflow_failure(
        SimpleNamespace(
            classification="context-overflow",
            code="context_length_exceeded",
        )
    )
    assert not is_context_overflow_failure(
        SimpleNamespace(classification="rate-limit", code="rate_limit")
    )
    for message in (
        "service unavailable: too many tokens",
        "throttling error: too many tokens",
    ):
        raw = RuntimeError(message)
        raw.status_code = 503
        wrapped = _provider_error_from_exception(raw, message)
        assert wrapped.code == "provider_status_error"
        assert wrapped.status_code == 503
        assert not is_context_overflow_failure(wrapped)
    assert is_context_overflow_failure(
        LLMProviderError("wrapped context error", code="context_overflow")
    )
    raw_overflow = RuntimeError("prompt is too long")
    raw_overflow.status_code = 400
    wrapped_overflow = _provider_error_from_exception(
        raw_overflow, "prompt is too long"
    )
    assert wrapped_overflow.code == "context_overflow"
    assert is_context_overflow_failure(wrapped_overflow)
    assert not is_context_overflow_failure(
        LLMProviderError(
            "wrapped status error: too many tokens",
            code="provider_status_error",
            status_code=503,
        )
    )


def test_post_compaction_budget_recheck(path: Path) -> None:
    session = create_session(path, "recheck")
    service = SessionService(path)
    for index in range(90):
        prompt(service, session.id, f"large-{index}-" + "q" * 400)
    provider = MockProvider(
        responses=[
            assistant_message("summary one"),
            assistant_message("normal result"),
        ]
    )
    compactor = SessionCompaction(
        provider,
        service.events,
        buffer_tokens=8_000,
        keep_tokens=2_500,
        summary_output_tokens=256,
    )
    messages = project_session_history(SessionHistory(path).load_for_runner(session.id, -1))
    calls = 0
    while compactor.compact_if_needed(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, -1),
        request_messages=messages,
        tools=[],
        model_context_tokens=10_000,
        requested_output_tokens=512,
    ):
        calls += 1
        messages = project_session_history(
            SessionHistory(path).load_for_runner(session.id, -1)
        )
    provider.chat(messages=messages, tools=[])
    assert calls == 1
    stages = [getattr(call["options"], "stage", "") for call in provider.calls]
    assert stages == ["session_compaction", ""]
    events = service.events.read_aggregate(session.id)
    last_compaction = max(
        event.seq for event in events if event.type == COMPACTION_ENDED
    )
    assert all(
        event.seq < last_compaction
        for event in events
        if event.type == PROMPTED
    )
    with service.database.read_transaction() as connection:
        user_count = connection.execute(
            "SELECT COUNT(*) AS n FROM session_message WHERE session_id = ? AND type = 'user'",
            (session.id,),
        ).fetchone()
    assert int(user_count["n"]) == 90
    assert not compactor.compact_if_needed(
        session.id,
        SessionHistory(path).entries_for_runner(session.id, -1),
        request_messages=[{"role": "system", "content": "x" * 50_000}, *messages],
        tools=[],
        model_context_tokens=10_000,
        requested_output_tokens=512,
    )


def test_provider_overflow_recovers_once(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root)

    class OverflowProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.initial_calls = 0

        def chat_result(self, messages, tools, options=None):
            self.calls.append({"messages": messages, "tools": tools, "options": options})
            if getattr(options, "stage", "") == "session_compaction":
                return LLMChatResult(assistant_message("overflow summary"), LLMUsage())
            self.initial_calls += 1
            if self.initial_calls == 1:
                raise LLMProviderError(
                    "model_context_window_exceeded",
                    code="context_overflow",
                )
            return LLMChatResult(assistant_message("recovered answer"), LLMUsage())

    runtime = type(
        "Runtime",
        (),
        {"snapshot": staticmethod(lambda: (MCPRegistry(), MCPRuntimeStatus(False, "")))},
    )()
    provider = OverflowProvider()
    agent = build_agent_for_workspace(
        "overflow-user",
        "overflow-project",
        llm_factory=lambda: provider,
        runtime_manager_factory=lambda: runtime,
    )
    agent.session_compaction.auto = False
    service = SessionService(agent.workspace.database_path)
    for index in range(100):
        prompt(service, agent.session_id, f"old-{index}-" + "d" * 400)
    execution = SessionExecution(
        database=agent.session_store.database,
        runner_resolver=lambda _session: SessionRunner(
            agent._run_session_work_item,
            database=agent.session_store.database,
        ),
    )
    service = SessionService(agent.workspace.database_path, execution=execution)
    service.prompt(agent.session_id, "current", resume=False)
    execution.resume(agent.session_id)
    events = service.events.read_aggregate(agent.session_id)
    assert provider.initial_calls == 2, [
        getattr(call.get("options"), "stage", "") for call in provider.calls
    ]
    assert sum(event.type == COMPACTION_STARTED for event in events) == 1
    assert sum(event.type == COMPACTION_ENDED for event in events) == 1
    assert sum(event.type == "session.next.step.started" for event in events) == 1
    assert len(
        [item for item in service.store.messages(agent.session_id) if item.type == "assistant"]
    ) == 1


def test_service_unavailable_does_not_compact(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root)

    class UnavailableProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.initial_calls = 0

        def chat_result(self, messages, tools, options=None):
            self.calls.append({"messages": messages, "tools": tools, "options": options})
            stage = getattr(options, "stage", "")
            if stage == "session_compaction":
                raise AssertionError("service unavailable must not trigger compaction")
            if stage == "initial_agent_turn":
                self.initial_calls += 1
                raise LLMProviderError(
                    "service unavailable: too many tokens",
                    code="provider_error",
                    status_code=503,
                )
            return LLMChatResult(assistant_message("fallback"), LLMUsage())

    runtime = type(
        "Runtime",
        (),
        {"snapshot": staticmethod(lambda: (MCPRegistry(), MCPRuntimeStatus(False, "")))},
    )()
    provider = UnavailableProvider()
    agent = build_agent_for_workspace(
        "unavailable-user",
        "unavailable-project",
        llm_factory=lambda: provider,
        runtime_manager_factory=lambda: runtime,
    )
    agent.session_compaction.auto = False
    service = SessionService(agent.workspace.database_path)
    for index in range(100):
        prompt(service, agent.session_id, f"old-{index}-" + "e" * 400)
    execution = SessionExecution(
        database=agent.session_store.database,
        runner_resolver=lambda _session: SessionRunner(
            agent._run_session_work_item,
            database=agent.session_store.database,
        ),
    )
    service = SessionService(agent.workspace.database_path, execution=execution)
    service.prompt(agent.session_id, "current", resume=False)
    execution.resume(agent.session_id)
    events = service.events.read_aggregate(agent.session_id)
    assert provider.initial_calls == 1
    assert not any(
        event.type in {COMPACTION_STARTED, COMPACTION_ENDED} for event in events
    )


def main() -> None:
    test_serialization()
    test_overflow_classifier()
    with TemporaryDirectory(prefix="horizon-s7-") as directory:
        root = Path(directory)
        test_durable_compaction(root / "durable.db")
        test_within_budget(root / "within.db")
        test_negative_threshold_has_no_fallback(root / "negative-threshold.db")
        test_started_failure_does_not_advance_boundary(root / "failed.db")
        test_rolling_and_epoch_rebaseline(root / "rolling.db")
        test_rebaseline_blocked(root / "blocked.db")
        test_ended_atomic_failure(root / "atomic.db")
        test_post_compaction_budget_recheck(root / "recheck.db")
        test_provider_overflow_recovers_once(root / "overflow")
        test_service_unavailable_does_not_compact(root / "unavailable")
    print("session compaction smoke passed")


if __name__ == "__main__":
    main()
