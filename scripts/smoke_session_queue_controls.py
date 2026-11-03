"""Offline durable queue controls, selection races, and ownership smoke."""
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.session import SessionService
from core.session_input import SessionInputService, SessionInputLifecycleConflict
from core.session_runner import SessionRunner
from core.session_store import SessionStore


def fixture(root, name):
    sessions = SessionService(root / f'{name}.db')
    session = sessions.create(user_id='owner', project_id='project', workspace_id='owner/project', directory=root)
    return sessions, session, sessions.inputs


def test_recall_durable_and_no_resurrection(root):
    sessions, session, inputs = fixture(root, 'recall')
    item = inputs.admit(session.id, 'original', 'queue', 'msg_recall', access_mode='read_only')
    assert inputs.recall(session.id, item.id).prompt == {'text': 'original'}
    reopened = SessionInputService(database=inputs.database)
    assert reopened.pending(session.id, 'queue') == []
    assert reopened.admit(session.id, 'original', 'queue', item.id, access_mode='read_only') == item
    assert not reopened.promote_next_queued(session.id)
    assert SessionStore(database=inputs.database).messages(session.id) == []
    assert inputs.find(item.id) == item


def test_steer_same_identity_mode_and_retry(root):
    sessions, session, inputs = fixture(root, 'steer')
    item = inputs.admit(session.id, 'guide active task', 'queue', 'msg_steer', access_mode='read_only')
    inputs.steer(session.id, item.id)
    inputs.steer(session.id, item.id)
    assert inputs.pending(session.id, 'queue') == []
    try:
        inputs.recall(session.id, item.id)
    except SessionInputLifecycleConflict:
        pass
    else:
        raise AssertionError('stale queue recall cancelled a steered message')
    assert [x.id for x in inputs.pending(session.id, 'steer')] == [item.id]
    assert inputs.access_mode(item.id) == 'read_only'
    assert inputs.admit(session.id, item.prompt, 'queue', item.id, access_mode='read_only') == item
    assert inputs.promote_steers(session.id, inputs.events.latest_sequence(session.id)) == 1
    assert [x.id for x in SessionStore(database=inputs.database).messages(session.id)] == [item.id]
    try:
        inputs.recall(session.id, item.id)
    except SessionInputLifecycleConflict:
        pass
    else:
        raise AssertionError('promoted item recalled')


def test_runner_claim_rejects_mutation(root):
    sessions, session, inputs = fixture(root, 'claim')
    item = inputs.admit(session.id, 'chosen', 'queue', 'msg_claim')
    def work(text, promotion, cancelled):
        assert text == 'chosen'
        for action in (inputs.recall, inputs.steer):
            try:
                action(session.id, item.id)
            except SessionInputLifecycleConflict:
                pass
            else:
                raise AssertionError('runner-selected item mutated')
        assert inputs.promote_next_queued(session.id)
    SessionRunner(work, database=inputs.database).run(session.id, False, threading.Event())


def test_steer_claim_blocks_recall_and_steer_races(root):
    sessions, session, inputs = fixture(root, 'steer_claim')
    item = inputs.admit(session.id, 'guidance', 'queue', 'msg_steer_claim')
    inputs.steer(session.id, item.id)
    def work(text, promotion, cancelled):
        assert text == 'guidance' and promotion == 'steer'
        try:
            inputs.recall(session.id, item.id)
        except SessionInputLifecycleConflict:
            pass
        else:
            raise AssertionError('claimed steer recalled')
        inputs.promote_steers(session.id, inputs.events.latest_sequence(session.id))
    SessionRunner(work, database=inputs.database).run(session.id, False, threading.Event())
    for index in range(8):
        _, session, inputs = fixture(root, f'steer_race{index}')
        item = inputs.admit(session.id, 'guide', 'queue', f'msg_steer_race{index}')
        barrier = threading.Barrier(2)
        def steer():
            barrier.wait()
            try:
                inputs.steer(session.id, item.id)
                return True
            except SessionInputLifecycleConflict:
                return False
        def promote():
            barrier.wait()
            return inputs.promote_next_queued(session.id)
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(steer), pool.submit(promote)
            steered, promoted = a.result(), b.result()
        assert steered != promoted
        if steered:
            assert [x.id for x in inputs.pending(session.id, 'steer')] == [item.id]
            inputs.promote_steers(session.id, inputs.events.latest_sequence(session.id))
        assert len(SessionStore(database=inputs.database).messages(session.id)) == 1


def test_recall_and_promotion_race(root):
    for index in range(12):
        sessions, session, inputs = fixture(root, f'race{index}')
        item = inputs.admit(session.id, 'race', 'queue', f'msg_race{index}')
        barrier = threading.Barrier(2)
        def recall():
            barrier.wait()
            try:
                inputs.recall(session.id, item.id)
                return True
            except SessionInputLifecycleConflict:
                return False
        def promote():
            barrier.wait()
            return inputs.promote_next_queued(session.id)
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(recall), pool.submit(promote)
            recalled, promoted = a.result(), b.result()
        assert recalled != promoted
        assert len(SessionStore(database=inputs.database).messages(session.id)) == int(promoted)


def test_failed_claim_and_restart_recovery(root):
    sessions, session, inputs = fixture(root, 'recovery')
    item = inputs.admit(session.id, 'recover', 'queue', 'msg_recover')
    def fail(*args):
        raise RuntimeError('before provider')
    try:
        SessionRunner(fail, database=inputs.database).run(session.id, False, threading.Event())
    except RuntimeError:
        pass
    assert [x.id for x in inputs.pending(session.id, 'queue', unclaimed=True)] == [item.id]
    # A crash after claiming leaves a durable reservation; explicit resume can
    # recover it without an automatic execution triggered by GET/control APIs.
    assert inputs.claim_next_queued(session.id).id == item.id
    assert inputs.pending(session.id, 'queue', unclaimed=True) == []
    assert sessions.transcript(session.id)[0].data['delivery_state'] == 'claimed'
    def work(text, promotion, cancelled):
        assert text == 'recover'
        assert inputs.promote_next_queued(session.id)
    SessionRunner(work, database=inputs.database).run(session.id, False, threading.Event())
    assert [x.id for x in sessions.transcript(session.id) if x.type == 'user'] == [item.id]


def test_api_ownership_and_pending_visibility(root):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.routes.session as routes
    sessions, session, inputs = fixture(root, 'ownership')
    item = inputs.admit(session.id, 'private', 'queue', 'msg_private', access_mode='read_only')
    app = FastAPI()
    app.include_router(routes.router)
    with patch.object(routes, '_service', side_effect=lambda user, project: (sessions, user, project)):
        client = TestClient(app)
        base = f'/session/{session.id}/pending'
        identity = {'user_id': 'owner', 'project_id': 'project'}
        for method, url in [('GET', base), ('POST', f'{base}/{item.id}/steer'), ('DELETE', f'{base}/{item.id}')]:
            kwargs = {'json': {'user_id': 'intruder', 'project_id': 'project'}} if method == 'POST' else {'params': {'user_id': 'intruder', 'project_id': 'project'}}
            assert client.request(method, url, **kwargs).status_code == 403
        assert client.get(base, params=identity).json()['data']['pending'][0]['access_mode'] == 'read_only'
        assert client.post(f'{base}/{item.id}/steer', json=identity).status_code == 409
        assert inputs.pending(session.id, 'queue')[0].id == item.id
        from core.session_execution import SessionExecution
        started, release = threading.Event(), threading.Event()
        class HoldingRunner:
            def run(self, *args):
                started.set()
                assert release.wait(3)
        execution = SessionExecution(database=inputs.database, runner_resolver=lambda _: HoldingRunner())
        sessions.execution = execution
        execution.wake(session.id)
        assert started.wait(2)
        try:
            assert client.post(f'{base}/{item.id}/steer', json=identity).status_code == 200
        finally:
            release.set()
            execution.interrupt(session.id)
        visible = sessions.transcript(session.id)
        assert visible[0].data['delivery'] == 'steer'
        assert visible[0].data['delivery_state'] == 'pending'
        assert client.delete(f'{base}/{item.id}', params=identity).status_code == 409
        assert client.get(base, params=identity).json()['data']['pending'][0]['delivery'] == 'steer'
        recalled = inputs.admit(session.id, 'recall me', 'queue', 'msg_api_recall', access_mode='read_only')
        response = client.delete(f'{base}/{recalled.id}', params=identity)
        assert response.status_code == 200
        assert response.json()['data']['access_mode'] == 'read_only'
        assert client.delete(f'{base}/{recalled.id}', params=identity).status_code == 200
        assert [x.id for x in inputs.pending_all(session.id)] == [item.id]
        assert [message.id for message in sessions.transcript(session.id) if message.type == 'user'] == [item.id]
        assert inputs.find(item.id).promoted_seq is None


def test_queue_failure_does_not_restart_and_success_still_drains(root):
    from core.session_execution import SessionExecution
    for fail in (True, False):
        sessions, session, inputs = fixture(root, f'queue_failure_{fail}')
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        class Runner:
            def run(self, sid, force, cancelled):
                calls.append(force)
                if len(calls) == 1:
                    started.set()
                    assert release.wait(3)
                    if fail:
                        raise RuntimeError('first failed')
                finished.set()
        execution = SessionExecution(database=inputs.database, runner_resolver=lambda _: Runner())
        sessions.execution = execution
        with ThreadPoolExecutor(1) as pool:
            owner = pool.submit(execution.resume, session.id)
            assert started.wait(2)
            sessions.prompt(session.id, 'later', delivery='queue', message_id=f'msg_failure_{fail}')
            release.set()
            if fail:
                try:
                    owner.result(3)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('failure swallowed')
                assert not finished.wait(.1)
                assert calls == [True]
                assert len(inputs.pending(session.id, 'queue')) == 1
                assert execution.active() == set()
            else:
                owner.result(3)
                assert finished.wait(2)
                assert calls == [True, False]


def test_pending_all_effective_delivery_and_claim_filter(root):
    sessions, session, inputs = fixture(root, 'pending_all')
    a = inputs.admit(session.id, 'A', 'queue', 'msg_all_a')
    b = inputs.admit(session.id, 'B', 'steer', 'msg_all_b')
    c = inputs.admit(session.id, 'C', 'queue', 'msg_all_c')
    inputs.steer(session.id, a.id)
    inputs.recall(session.id, c.id)
    assert [(x.id, x.delivery) for x in inputs.pending_all(session.id)] == [
        (a.id, 'steer'), (b.id, 'steer')]
    assert inputs.claim_input(session.id, a.id)
    assert [x.id for x in inputs.pending_all(session.id, unclaimed=True)] == [b.id]


def test_model_terminal_failure_stops_queue_until_explicit_resume(root):
    import os
    from types import SimpleNamespace
    from core.agent_factory import build_agent_for_workspace
    from core.mcp_registry import MCPRegistry
    from core.mcp_runtime import MCPRuntimeStatus
    from core.session_execution import SessionExecution
    from providers.mock import MockProvider, assistant_message
    provider = MockProvider(responses=[assistant_message('')])
    runtime = SimpleNamespace(snapshot=lambda: (MCPRegistry(), MCPRuntimeStatus(enabled=False, config_path='')))
    with patch.dict(os.environ, {'HORIZON_USER_DATA_ROOT': str(root / 'soft-failure')}):
        agent = build_agent_for_workspace('queue-user', 'queue-project', llm_factory=lambda: provider,
                                          runtime_manager_factory=lambda: runtime)
        execution = SessionExecution(database=agent.session_store.database,
            runner_resolver=lambda _: SessionRunner(agent._run_session_work_item, database=agent.session_store.database))
        sessions = SessionService(database=agent.session_store.database, execution=execution)
        first = sessions.prompt(agent.session_id, 'first task', resume=False)
        later = sessions.prompt(agent.session_id, 'later task', delivery='queue', resume=False)
        try:
            execution.resume(agent.session_id)
        except RuntimeError:
            pass
        assert sessions.inputs.find(first.id).promoted_seq is not None
        assert sessions.inputs.find(later.id).promoted_seq is None
        transcript = sessions.transcript(agent.session_id)
        assert any(x.type == 'run_status' and x.data['status'] == 'failed' for x in transcript)
        assert any(x.type == 'synthetic' for x in transcript)
        assert provider.index == 2
        provider.responses = [assistant_message('recovered')]
        execution.resume(agent.session_id)
        assert sessions.inputs.find(later.id).promoted_seq is not None
        assert provider.index == 3
        assert len([x for x in sessions.events.read_aggregate(agent.session_id)
                    if x.type == 'session.run.finished']) == 1


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        test_recall_durable_and_no_resurrection(root)
        test_steer_same_identity_mode_and_retry(root)
        test_runner_claim_rejects_mutation(root)
        test_steer_claim_blocks_recall_and_steer_races(root)
        test_recall_and_promotion_race(root)
        test_failed_claim_and_restart_recovery(root)
        test_api_ownership_and_pending_visibility(root)
        test_queue_failure_does_not_restart_and_success_still_drains(root)
        test_pending_all_effective_delivery_and_claim_filter(root)
        test_model_terminal_failure_stops_queue_until_explicit_resume(root)
    print('smoke_session_queue_controls ok')

if __name__ == '__main__':
    main()
