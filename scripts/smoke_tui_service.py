"""Isolated local-service and event-bridge verification, without model/network calls."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routes import session as routes
from core.session import SessionService
from core.session_execution import SessionExecution


class ServiceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'horizon.db'
        self.service = SessionService(self.path, execution=SessionExecution(self.path))
        self.session = self.service.create(user_id='alice', project_id='project',
                                           workspace_id='alice/project', directory=self.tmp.name)
        self.patch = patch.object(routes, '_service', return_value=(self.service, 'alice', 'project'))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_create_session_endpoint_and_ownership(self):
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(routes, 'create_session_for_workspace', create=True,
                          return_value=(NS(), self.session)):
            with TestClient(app) as client:
                response = client.post('/session', json={'user_id': 'alice', 'project_id': 'project'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['data']['id'], self.session.id)
                with patch.object(routes, '_service', return_value=(self.service, 'bob', 'project')):
                    forbidden = client.get(f'/session/{self.session.id}/events')
                    self.assertEqual(forbidden.status_code, 403)

    def test_execution_stream_is_inside_the_runner_thread_and_emits_failure(self):
        from core.session_live import session_live
        from providers.streaming import stream_context
        def resolve(_):
            def run(session_id, force, cancelled):
                context = stream_context('final_answer')
                self.assertIsNotNone(context)
                context.emit('llm.delta', {'text': 'first', 'call_id': 'live'})
                raise ValueError('private detail must not reach UI')
            return NS(run=run)
        execution = SessionExecution(self.path, runner_resolver=resolve)
        with self.assertLogs('core.session_execution', level='ERROR'), self.assertRaises(ValueError):
            execution.resume(self.session.id)
        events = session_live.read(self.session.id).events
        self.assertIn('llm.delta', [e.type for e in events])
        terminal = [e for e in events if e.type == 'run.ended'][-1]
        self.assertEqual(terminal.data['status'], 'failed')
        self.assertNotIn('private detail', repr(events))
        self.assertNotIn(self.session.id, execution.active())

    def test_context_loading_failure_has_safe_actionable_message(self):
        from core.session_live import session_live
        from core.system_context import SystemContextInitializationBlocked
        def resolve(_):
            raise SystemContextInitializationBlocked(['horizon/persistent-instructions'])
        execution = SessionExecution(self.path, runner_resolver=resolve)
        with self.assertLogs('core.session_execution', level='ERROR'), self.assertRaises(SystemContextInitializationBlocked):
            execution.resume(self.session.id)
        ended = [e for e in session_live.read(self.session.id).events if e.type == 'run.ended'][-1]
        self.assertEqual(ended.data['error']['code'], 'system_context_unavailable')
        self.assertIn('本地记忆', ended.data['error']['message'])

    def test_committed_events_and_sse_resume(self):
        from core.session_live import session_live
        async def check():
            session_live.publish(self.session.id, 'llm.delta', {'text': '你好'})
            request = NS(is_disconnected=lambda: asyncio.sleep(0, result=False))
            response = await routes.session_events(self.session.id, request, after=0,
                                                   user_id='alice', project_id='project')
            iterator = response.body_iterator
            first = await anext(iterator)
            self.assertIn('ready', first)
            second = await anext(iterator)
            # The creation event is committed before the manually published delta.
            if 'llm.delta' not in second:
                second = await anext(iterator)
            self.assertIn('你好', second)
            self.assertIn('id:', second)
            await iterator.aclose()
        asyncio.run(check())
        # Persistence remains authoritative: token delivery cannot invent messages.
        self.assertEqual(self.service.context(self.session.id), [])

    def test_handled_model_failure_is_not_reported_as_completed(self):
        from core.session_live import session_live
        from providers.streaming import stream_context
        def resolve(_):
            def run(*_):
                stream_context('final_answer').emit('llm.failed', {'call_id': 'failed'})
            return NS(run=run)
        execution = SessionExecution(self.path, runner_resolver=resolve)
        execution.resume(self.session.id)
        ended = [e for e in session_live.read(self.session.id).events if e.type == 'run.ended'][-1]
        self.assertEqual(ended.data['status'], 'failed')

    def test_open_history_starts_with_snapshot_instead_of_old_running_events(self):
        from core.session_live import session_live
        session_live.publish(self.session.id, 'run.started', {})
        session_live.publish(self.session.id, 'llm.delta', {'text': 'old text'})
        async def check():
            request = NS(is_disconnected=lambda: asyncio.sleep(0, result=False))
            response = await routes.session_events(self.session.id, request, after=0, tail=True,
                                                   user_id='alice', project_id='project')
            iterator = response.body_iterator
            await anext(iterator)
            first = await anext(iterator)
            self.assertIn('session.snapshot', first)
            self.assertIn('"active": false', first)
            self.assertNotIn('old text', first)
            await iterator.aclose()
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
