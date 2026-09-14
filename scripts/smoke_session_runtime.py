"""Focused S4 smoke for strict OpenCode-style Session execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import MethodType, SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("AGENT_ACCESS_MODE", "full_access")
os.environ.setdefault("MCP_ENABLED", "false")
os.environ.setdefault("EMBEDDING_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "openai_compatible")
os.environ.setdefault("LLM_MODEL", "deepseek-chat")
os.environ.setdefault("LLM_BASE_URL", "https://api.deepseek.com")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_factory import (
    build_agent_for_workspace,
    create_session_for_workspace,
    run_in_workspace,
)
from core.loop import AgentLoop
from core.mcp_registry import MCPRegistry
from core.mcp_runtime import MCPRuntimeStatus
from core.memory import Memory
from core.message_validator import validate_openai_tool_messages
from core.session import SessionService
from core.session_execution import SessionExecution, get_session_execution
from core.session_input import (
    PROMPT_ADMITTED,
    SessionInputService,
    SessionPromptConflictError,
)
from core.session_message_updater import PROMPTED, STEP_STARTED
from core.session_run_coordinator import SessionRunCoordinator
from core.session_runner import SessionRunner
from core.session_runtime_projection import project_session_history
from core.session_store import SessionStore
from core.workspace_runtime import get_current_workspace
from providers.mock import MockProvider, assistant_message


class _ControlledProvider(MockProvider):
    def __init__(
        self,
        steps: list[tuple[threading.Event, threading.Event | None, object]],
    ) -> None:
        super().__init__(responses=[assistant_message("unused")])
        self.steps = steps
        self._step_lock = threading.Lock()

    def chat(self, messages, tools, options=None):
        with self._step_lock:
            index = self.index
            self.index += 1
            self.calls.append({"messages": messages, "tools": tools, "options": options})
        started, release, outcome = self.steps[index]
        started.set()
        if release is not None:
            assert release.wait(3)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _runtime():
    return SimpleNamespace(
        snapshot=lambda: (MCPRegistry(), MCPRuntimeStatus(enabled=False, config_path=""))
    )


def _execution_for_agent(agent: AgentLoop) -> SessionExecution:
    assert agent.session_store is not None
    return SessionExecution(
        database=agent.session_store.database,
        runner_resolver=lambda _session: SessionRunner(
            agent._run_session_work_item,
            database=agent.session_store.database,
        ),
    )


def _sessions(agent: AgentLoop, execution: SessionExecution) -> SessionService:
    return SessionService(
        database_path=agent.workspace.database_path,
        execution=execution,
    )


def _wait_idle(execution: SessionExecution, session_id: str) -> None:
    deadline = time.time() + 5
    while session_id in execution.active():
        assert time.time() < deadline
        time.sleep(0.005)


def _visible_text(agent: AgentLoop) -> list[str]:
    return [
        str(message.get("content") or "")
        for message in project_session_history(
            SessionStore(agent.workspace.database_path).context(agent.session_id)
        )
        if message.get("role") in {"user", "assistant"}
        and not message.get("tool_calls")
    ]


def _call(
    call_id: str,
    name: str = "get_workspace_status",
    arguments: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments or {}, ensure_ascii=False),
        ),
    )


def test_prompt_returns_after_admission(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "prompt-return")
    started = threading.Event()
    release = threading.Event()
    provider = _ControlledProvider(
        [(started, release, assistant_message("A-result"))]
    )
    agent = build_agent_for_workspace(
        "runtime-user",
        "runtime-project",
        session_id="ses_s4_prompt_return",
        llm_factory=lambda: provider,
        runtime_manager_factory=_runtime,
    )
    execution = _execution_for_agent(agent)
    admitted = _sessions(agent, execution).prompt(agent.session_id, "A")
    assert admitted.prompt == {"text": "A"}
    assert admitted.promoted_seq is None
    assert started.wait(2) and not release.is_set()
    events = SessionService(agent.workspace.database_path).events.read_aggregate(
        agent.session_id
    )
    admitted_event = next(
        event
        for event in events
        if event.type == PROMPT_ADMITTED and event.data.get("message_id") == admitted.id
    )
    release.set()
    _wait_idle(execution, agent.session_id)
    stored = agent.session_inputs.find(admitted.id)
    assert stored is not None and stored.promoted_seq is not None
    events = SessionService(agent.workspace.database_path).events.read_aggregate(
        agent.session_id
    )
    prompted = next(
        event
        for event in events
        if event.type == PROMPTED and event.data.get("message_id") == admitted.id
    )
    step = next(event for event in events if event.type == STEP_STARTED)
    assert admitted_event.seq < prompted.seq < step.seq
    assert _visible_text(agent)[-2:] == ["A", "A-result"]


def test_resume_false_and_durable_output(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "resume-false")
    provider = MockProvider(responses=[assistant_message("RESUMED-result")])
    agent = build_agent_for_workspace(
        "runtime-user",
        "runtime-project",
        session_id="ses_s4_resume_false",
        llm_factory=lambda: provider,
        runtime_manager_factory=_runtime,
    )
    execution = _execution_for_agent(agent)
    admitted = _sessions(agent, execution).prompt(
        agent.session_id,
        "RESUME-ME",
        resume=False,
    )
    assert agent.session_inputs.find(admitted.id).promoted_seq is None
    assert provider.calls == [] and execution.active() == set()
    assert execution.resume(agent.session_id) is None
    assert len(provider.calls) == 1
    assert _visible_text(agent)[-2:] == ["RESUME-ME", "RESUMED-result"]


def test_prompt_exact_retry_and_wake_failure(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "prompt-retry")
    provider = MockProvider(responses=[assistant_message("RETRY-result")])
    agent = build_agent_for_workspace(
        "runtime-user",
        "runtime-project",
        session_id="ses_s4_prompt_retry",
        llm_factory=lambda: provider,
        runtime_manager_factory=_runtime,
    )
    execution = _execution_for_agent(agent)
    message_id = "msg_s4_exact_retry"
    sessions = _sessions(agent, execution)
    first = sessions.prompt(
        agent.session_id,
        "RETRY",
        message_id=message_id,
        resume=False,
    )
    second = sessions.prompt(
        agent.session_id,
        "RETRY",
        message_id=message_id,
        resume=False,
    )
    assert first == second
    events = sessions.events.read_aggregate(agent.session_id)
    assert sum(
        event.type == PROMPT_ADMITTED and event.data.get("message_id") == message_id
        for event in events
    ) == 1
    try:
        sessions.prompt(
            agent.session_id,
            "CONFLICT",
            message_id=message_id,
            resume=False,
        )
    except SessionPromptConflictError:
        pass
    else:
        raise AssertionError("conflicting prompt reuse was accepted")

    class BrokenExecution:
        def wake(self, session_id: str) -> None:
            raise RuntimeError("wake failed")

    failed_id = "msg_s4_wake_failure"
    try:
        SessionService(
            database_path=agent.workspace.database_path,
            execution=BrokenExecution(),
        ).prompt(agent.session_id, "WAKE-FAIL", message_id=failed_id)
    except RuntimeError as exc:
        assert str(exc) == "wake failed"
    else:
        raise AssertionError("wake infrastructure failure was swallowed")
    pending = agent.session_inputs.find(failed_id)
    assert pending is not None and pending.promoted_seq is None
    assert sessions.prompt(
        agent.session_id,
        "WAKE-FAIL",
        message_id=failed_id,
        resume=False,
    ) == pending
    assert execution.resume(agent.session_id) is None
    assert agent.session_inputs.find(failed_id).promoted_seq is not None


def test_runtime_projection_and_tool_continuation(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "tool-continuation")
    provider = MockProvider(
        responses=[
            assistant_message("", [_call("call_reload")]),
            assistant_message("RELOADED"),
        ]
    )
    agent = build_agent_for_workspace(
        "runtime-user",
        "runtime-project",
        session_id="ses_s4_tool_reload",
        llm_factory=lambda: provider,
        runtime_manager_factory=_runtime,
    )
    execution = _execution_for_agent(agent)
    executed: list[str] = []

    def tool_success():
        executed.append("call_reload")
        agent.session_inputs.admit(
            agent.session_id,
            "STEER-DURING-TOOL",
            message_id="msg_s4_during_tool",
        )
        return {"success": True, "data": {"source": "durable-tool-result"}}

    agent.tools["get_workspace_status"] = tool_success
    _sessions(agent, execution).prompt(agent.session_id, "RUN-TOOL", resume=False)
    assert execution.resume(agent.session_id) is None
    assert executed == ["call_reload"] and len(provider.calls) == 2
    second = provider.calls[1]["messages"]
    validate_openai_tool_messages(second)
    rendered = json.dumps(second, ensure_ascii=False)
    assert all(
        value in rendered
        for value in (
            "RUN-TOOL",
            "call_reload",
            "durable-tool-result",
            "STEER-DURING-TOOL",
        )
    )


def test_pre_promotion_failure_stays_pending(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "pre-promotion")
    provider = MockProvider(responses=[assistant_message("unused")])
    agent = build_agent_for_workspace(
        "runtime-user",
        "runtime-project",
        session_id="ses_s4_pre_promotion",
        llm_factory=lambda: provider,
        runtime_manager_factory=_runtime,
    )
    execution = _execution_for_agent(agent)
    admitted = _sessions(agent, execution).prompt(
        agent.session_id,
        "PENDING-A",
        resume=False,
    )
    attempts = 0

    def fail_before_promotion(self, user_input, user_id=None, project_id=None):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("pre-promotion failure")

    agent._run_request = MethodType(fail_before_promotion, agent)
    try:
        execution.resume(agent.session_id)
    except RuntimeError as exc:
        assert str(exc) == "pre-promotion failure"
    else:
        raise AssertionError("pre-promotion failure was swallowed")
    time.sleep(0.05)
    assert attempts == 1
    assert agent.session_inputs.find(admitted.id).promoted_seq is None
    assert provider.calls == [] and execution.active() == set()


def test_cutoff_queue_and_void_runner(root: Path) -> None:
    database_path = root / "cutoff.db"
    service = SessionService(database_path)
    session = service.create(
        session_id="ses_s4_cutoff",
        user_id="runtime-user",
        project_id="runtime-project",
        workspace_id="runtime-user/runtime-project",
        directory=root,
    )
    inputs = SessionInputService(database_path)
    a = inputs.admit(session.id, "A", message_id="msg_s4_cutoff_a")
    b = inputs.admit(session.id, "B", message_id="msg_s4_cutoff_b")
    cutoff = b.admitted_seq
    c = inputs.admit(session.id, "C", message_id="msg_s4_cutoff_c")
    assert inputs.promote_steers(session.id, cutoff) == 2
    assert inputs.find(a.id).promoted_seq is not None
    assert inputs.find(b.id).promoted_seq is not None
    assert inputs.find(c.id).promoted_seq is None
    users = [
        item["content"]
        for item in project_session_history(SessionStore(database_path).context(session.id))
        if item["role"] == "user"
    ]
    assert users == ["A", "B"]

    queued = service.create(
        session_id="ses_s4_queue",
        user_id="runtime-user",
        project_id="runtime-project",
        workspace_id="runtime-user/runtime-project",
        directory=root,
    )
    for name in ("A", "B", "C"):
        inputs.admit(
            queued.id,
            f"QUEUE-{name}",
            delivery="queue",
            message_id=f"msg_s4_queue_{name.lower()}",
        )
    order: list[str] = []

    def queue_work(text: str, promotion: str | None, cancelled: threading.Event) -> None:
        assert promotion == "queue"
        before = {item.id for item in inputs.pending(queued.id, "queue")}
        assert inputs.promote_next_queued(queued.id) is True
        after = {item.id for item in inputs.pending(queued.id, "queue")}
        order.extend(before - after)

    runner = SessionRunner(queue_work, database_path)
    assert runner.run(queued.id, False, threading.Event()) is None
    assert order == [
        "msg_s4_queue_a",
        "msg_s4_queue_b",
        "msg_s4_queue_c",
    ]


def test_coordinator_void_and_settlement() -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[tuple[str, bool]] = []

    def blocked(key: str, force: bool, cancelled: threading.Event) -> None:
        calls.append((key, force))
        started.set()
        release.wait(2)

    coordinator = SessionRunCoordinator(blocked)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(coordinator.run, "same")
        assert started.wait(1)
        second = pool.submit(coordinator.run, "same")
        release.set()
        assert first.result(timeout=2) is None
        assert second.result(timeout=2) is None
    assert calls == [("same", True)]

    follow_started = threading.Event()
    follow_release = threading.Event()
    follow_calls: list[bool] = []

    def followup(key: str, force: bool, cancelled: threading.Event) -> None:
        follow_calls.append(force)
        if len(follow_calls) == 1:
            follow_started.set()
            follow_release.wait(2)

    coordinator = SessionRunCoordinator(followup)

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(coordinator.run, "follow")
        assert follow_started.wait(1)
        coordinator.wake("follow")
        coordinator.wake("follow")
        follow_release.set()
        assert owner.result(timeout=2) is None
    assert follow_calls == [True, False]

    failure_started = threading.Event()
    allow_failure = threading.Event()
    successor_done = threading.Event()
    failure_calls: list[bool] = []

    def failure(key: str, force: bool, cancelled: threading.Event) -> None:
        failure_calls.append(force)
        if len(failure_calls) == 1:
            failure_started.set()
            allow_failure.wait(2)
            raise RuntimeError("first failed")
        successor_done.set()

    coordinator = SessionRunCoordinator(failure)

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(coordinator.run, "failure")
        assert failure_started.wait(1)
        coordinator.wake("failure")
        allow_failure.set()
        try:
            owner.result(timeout=2)
        except RuntimeError as exc:
            assert str(exc) == "first failed"
        else:
            raise AssertionError("original execution failure was swallowed")
    assert successor_done.wait(2)
    deadline = time.time() + 2
    while coordinator.active():
        assert time.time() < deadline
        time.sleep(0.005)
    assert failure_calls == [True, False]


def test_coordinator_interrupt_and_start_failure() -> None:
    started = threading.Event()

    def interruptible(key: str, force: bool, cancelled: threading.Event) -> None:
        started.set()
        cancelled.wait(2)

    coordinator = SessionRunCoordinator(interruptible)
    coordinator.wake("interrupt")
    assert started.wait(1)
    coordinator.interrupt("interrupt")
    assert coordinator.active() == set()

    with patch.object(threading.Thread, "start", side_effect=RuntimeError("start failed")):
        try:
            coordinator.wake("start-failure")
        except RuntimeError as exc:
            assert str(exc) == "start failed"
        else:
            raise AssertionError("thread start failure was swallowed")
    assert coordinator.active() == set()
    assert coordinator.run("start-failure") is None


def test_interrupt_join_snapshot_and_stopping_retry() -> None:
    started = threading.Event()
    calls: list[bool] = []

    def joined_then_interrupted(
        key: str,
        force: bool,
        cancelled: threading.Event,
    ) -> None:
        calls.append(force)
        started.set()
        cancelled.wait(2)

    coordinator = SessionRunCoordinator(joined_then_interrupted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        joined = pool.submit(coordinator.run, "joined")
        assert started.wait(1)
        interrupted = pool.submit(coordinator.interrupt, "joined")
        assert joined.result(timeout=2) is None
        assert interrupted.result(timeout=2) is None
    assert calls == [True]
    assert coordinator.active() == set()

    first_started = threading.Event()
    cancellation_seen = threading.Event()
    release_cleanup = threading.Event()
    restarted = threading.Event()
    retry_calls: list[bool] = []

    def stopping_then_explicit_run(
        key: str,
        force: bool,
        cancelled: threading.Event,
    ) -> None:
        retry_calls.append(force)
        if len(retry_calls) == 1:
            first_started.set()
            cancelled.wait(2)
            cancellation_seen.set()
            release_cleanup.wait(2)
            return
        restarted.set()

    coordinator = SessionRunCoordinator(stopping_then_explicit_run)
    coordinator.wake("stopping")
    assert first_started.wait(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        interrupted = pool.submit(coordinator.interrupt, "stopping")
        assert cancellation_seen.wait(1)
        resumed = pool.submit(coordinator.run, "stopping")
        assert not restarted.wait(0.05)
        release_cleanup.set()
        assert interrupted.result(timeout=2) is None
        assert resumed.result(timeout=2) is None
    assert restarted.is_set()
    assert retry_calls == [False, True]


def test_interrupt_clears_pending_wake() -> None:
    started = threading.Event()
    calls: list[bool] = []

    def drain(key: str, force: bool, cancelled: threading.Event) -> None:
        calls.append(force)
        started.set()
        cancelled.wait(2)

    coordinator = SessionRunCoordinator(drain)
    coordinator.wake("pending-interrupt")
    assert started.wait(1)
    coordinator.wake("pending-interrupt")
    coordinator.interrupt("pending-interrupt")
    assert calls == [False]
    assert coordinator.active() == set()


def test_execution_resolves_each_drain_from_session(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "execution-resolver")
    workspace, session = create_session_for_workspace(
        "resolver-user",
        "resolver-project",
        session_id="ses_s4_execution_resolver",
    )
    inputs = SessionInputService(workspace.database_path)
    first_started = threading.Event()
    release_first = threading.Event()
    resolved: list[tuple[str, str, int]] = []
    runner_count = 0

    def resolver(stored):
        nonlocal runner_count
        runner_count += 1
        runner_identity = runner_count
        resolved.append((stored.id, stored.workspace_id, runner_identity))

        def work(text: str, promotion: str | None, cancelled: threading.Event) -> None:
            cutoff = SessionService(workspace.database_path).events.latest_sequence(
                stored.id
            )
            if runner_identity == 1:
                first_started.set()
                release_first.wait(2)
            if promotion == "steer":
                inputs.promote_steers(stored.id, cutoff)

        return SessionRunner(work, workspace.database_path)

    execution = SessionExecution(
        workspace.database_path,
        runner_resolver=resolver,
    )
    request_a = SessionService(workspace.database_path, execution=execution)
    request_b = SessionService(workspace.database_path, execution=execution)
    admitted_a = request_a.prompt(
        session.id,
        "A",
        message_id="msg_s4_resolved_a",
    )
    assert first_started.wait(1)
    admitted_b = request_b.prompt(
        session.id,
        "B",
        message_id="msg_s4_resolved_b",
    )
    release_first.set()
    _wait_idle(execution, session.id)
    assert inputs.find(admitted_a.id).promoted_seq is not None
    assert inputs.find(admitted_b.id).promoted_seq is not None
    assert resolved == [
        (session.id, session.workspace_id, 1),
        (session.id, session.workspace_id, 2),
    ]


def test_workspace_runtime_is_thread_local(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "workspace-isolation")
    workspace_a, session_a = create_session_for_workspace(
        "workspace-user-a",
        "project-a",
        session_id="ses_s4_workspace_a",
    )
    workspace_b, session_b = create_session_for_workspace(
        "workspace-user-b",
        "project-b",
        session_id="ses_s4_workspace_b",
    )
    barrier = threading.Barrier(2)
    observed: dict[str, str] = {}
    observed_lock = threading.Lock()

    def resolver(stored):
        run_in_workspace(stored.user_id, stored.project_id)

        def work(text: str, promotion: str | None, cancelled: threading.Event) -> None:
            barrier.wait(timeout=2)
            with observed_lock:
                observed[stored.id] = get_current_workspace().workspace_id

        return SessionRunner(work, workspace_a.database_path)

    execution = SessionExecution(
        workspace_a.database_path,
        runner_resolver=resolver,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        run_a = pool.submit(execution.resume, session_a.id)
        run_b = pool.submit(execution.resume, session_b.id)
        assert run_a.result(timeout=3) is None
        assert run_b.result(timeout=3) is None
    assert observed == {
        session_a.id: workspace_a.workspace_id,
        session_b.id: workspace_b.workspace_id,
    }


def test_session_agent_run_is_not_public_prompt(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "agent-run-disabled")
    agent = build_agent_for_workspace(
        "runtime-user",
        "runtime-project",
        session_id="ses_s4_agent_run_disabled",
        llm_factory=lambda: MockProvider(responses=[assistant_message("unused")]),
        runtime_manager_factory=_runtime,
    )
    assert not hasattr(agent, "session_execution")
    assert not hasattr(agent, "session_runner")
    assert get_session_execution() is get_session_execution()
    try:
        agent.run("must not execute")
    except RuntimeError as exc:
        assert "SessionService.prompt" in str(exc)
    else:
        raise AssertionError("Session AgentLoop.run remained a public prompt path")
    assert agent.session_inputs.pending(agent.session_id, "steer") == []


def test_non_session_legacy_path(root: Path) -> None:
    os.environ["HORIZON_USER_DATA_ROOT"] = str(root / "legacy")
    workspace = run_in_workspace("legacy-user", "legacy-project")
    memory = Memory()
    memory.add_user_message("LEGACY-OLD")
    memory.add_assistant_message("LEGACY-ANSWER")
    provider = MockProvider(responses=[assistant_message("LEGACY-DONE")])
    agent = AgentLoop(
        provider,
        memory,
        user_id=workspace.user_id,
        project_id=workspace.project_id,
        mcp_registry=MCPRegistry(),
        mcp_runtime_status=MCPRuntimeStatus(enabled=False, config_path=""),
    )
    assert agent.run("LEGACY-CURRENT") == "LEGACY-DONE"


def main() -> None:
    previous = os.environ.get("HORIZON_USER_DATA_ROOT")
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            test_prompt_returns_after_admission(root)
            test_resume_false_and_durable_output(root)
            test_prompt_exact_retry_and_wake_failure(root)
            test_runtime_projection_and_tool_continuation(root)
            test_pre_promotion_failure_stays_pending(root)
            test_cutoff_queue_and_void_runner(root)
            test_coordinator_void_and_settlement()
            test_coordinator_interrupt_and_start_failure()
            test_interrupt_join_snapshot_and_stopping_retry()
            test_interrupt_clears_pending_wake()
            test_execution_resolves_each_drain_from_session(root)
            test_workspace_runtime_is_thread_local(root)
            test_session_agent_run_is_not_public_prompt(root)
            test_non_session_legacy_path(root)
    finally:
        if previous is None:
            os.environ.pop("HORIZON_USER_DATA_ROOT", None)
        else:
            os.environ["HORIZON_USER_DATA_ROOT"] = previous
    print("smoke_session_runtime ok")


if __name__ == "__main__":
    main()
