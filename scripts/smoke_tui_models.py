"""Isolated connection, selection, credential-boundary and runtime checks."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import Settings
from core.model_connections import ModelConnections
from core.session import SessionService
from core.session_execution import SessionExecution
from core.session_model import SessionModelSelection
from core.session_run_coordinator import SessionBusyError, SessionRunCoordinator


class Models(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = Settings(llm_base_url='http://localhost:9999/v1', llm_api_key='environment-key',
                                 llm_model='original', llm_capabilities_json={'max_context_tokens': 128000})
        self.connections = ModelConnections(self.root, self.settings)
        self.connections.save('custom', name='Local', base_url='http://localhost:8888/v1',
                              api_key='private-test-key', model='selected', context_tokens=128000)
        model_connections = patch('core.model_connections.ModelConnections', return_value=self.connections)
        model_connections.start()
        self.addCleanup(model_connections.stop)
        self.service = SessionService(self.root / 'horizon.db', execution=SessionExecution(self.root / 'horizon.db'))
        self.session = self.service.create(user_id='alice', project_id='project', workspace_id='alice/project', directory=str(self.root))

    def test_private_permissions_public_redaction_and_key_reuse(self):
        if os.name != 'nt':
            self.assertEqual(os.stat(self.connections.path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(self.connections.path.parent).st_mode & 0o777, 0o700)
        public = self.connections.public('custom')
        self.assertTrue(public['configured'])
        self.assertNotIn('api_key', public)
        self.assertNotIn('private-test-key', json.dumps(self.connections.catalog()))
        self.connections.save('custom', name='Local', base_url='http://localhost:8888/v1', api_key='')
        self.assertEqual(self.connections.runtime_settings('custom', 'selected').llm_api_key, 'private-test-key')
        with self.assertRaises(ValueError):
            self.connections.save('custom', name='changed', base_url='https://elsewhere.invalid/v1', api_key='')
        with self.assertRaises(ValueError):
            self.connections.save('custom', name='bad', base_url='https://user:secret@example.com/v1', api_key='new-key')
        self.connections.save('local.test', name='Local', base_url='http://localhost:9000/v1',
                              api_key='local-key', model='small', context_tokens=4096)
        self.connections.save('local.test', name='Local', base_url='http://localhost:9000/v1',
                              api_key='', model='large', context_tokens=128000)
        self.assertEqual(self.connections.runtime_settings('local.test', 'small').llm_capabilities_json['max_context_tokens'], 4096)
        self.assertEqual(self.connections.runtime_settings('local.test', 'large').llm_capabilities_json['max_context_tokens'], 128000)
        environment = ModelConnections(self.root, replace(self.settings,
            llm_base_url='https://user:environment-secret@example.invalid/v1?api_key=query-secret'))
        public_url = environment.public(environment.default_id)['base_url']
        self.assertEqual(public_url, 'https://example.invalid/v1')

    def test_multilingual_connection_name_is_utf8_on_every_platform(self):
        title = '中文 Café 日本語 🌅'
        self.connections.save('unicode', name=title, base_url='http://localhost:8888/v1',
                              api_key='test-key', model='custom', context_tokens=128000)
        saved = json.loads(self.connections.path.read_bytes().decode('utf-8'))
        self.assertIn(title, json.dumps(saved, ensure_ascii=False))
        self.assertEqual(ModelConnections(self.root, self.settings).public('unicode')['name'], title)

    def test_session_choice_is_durable_isolated_and_has_no_secret(self):
        selector = SessionModelSelection(self.service.database, self.connections)
        selected = selector.select(self.session.id, 'custom', 'selected')
        self.assertEqual(selected['model'], 'selected')
        reopened = SessionModelSelection(SessionService(self.root / 'horizon.db').database,
                                         ModelConnections(self.root, self.settings))
        self.assertEqual(reopened.get(self.session.id), selected)
        other = self.service.create(user_id='alice', project_id='project', workspace_id='alice/project', directory=str(self.root))
        self.assertEqual(reopened.get(other.id)['model'], 'original')
        self.assertEqual(self.service.context(self.session.id), [])
        self.assertNotIn('private-test-key', repr(self.service.events.read_aggregate(self.session.id)))

    def test_runtime_construction_uses_choice_without_global_mutation(self):
        from core import agent_factory
        SessionModelSelection(self.service.database, self.connections).select(self.session.id, 'custom', 'selected')
        workspace = NS(workspace_id=self.session.workspace_id, database_path=self.root / 'horizon.db')
        with patch.object(agent_factory, 'run_in_workspace', return_value=workspace), \
             patch.object(agent_factory, 'AgentLoop', side_effect=lambda **kwargs: NS(**kwargs)), \
             patch('core.model_connections.ModelConnections', return_value=self.connections):
            agent = agent_factory.build_agent_for_session(self.session, runtime_manager_factory=lambda: NS(snapshot=lambda: (None, None)))
        self.assertEqual(agent.llm.config.model, 'selected')
        self.assertEqual(agent.llm.config.api_key, 'private-test-key')
        self.assertEqual(self.settings.llm_model, 'original')

    def test_used_concrete_models_survive_reopen_and_only_configured_are_returned(self):
        from core.model_history import saved_models
        self.connections.save('deepseek', name='DeepSeek', base_url='https://api.deepseek.com',
                              api_key='private-test-key', model='deepseek-flash')
        SessionModelSelection(self.service.database, self.connections).select(self.session.id, 'deepseek', 'deepseek-v4-pro')
        reopened = ModelConnections(self.root, self.settings)
        recent = saved_models(self.service.database, reopened, reopened.catalog(), user_id='alice', project_id='project')
        self.assertIn('deepseek:deepseek-v4-pro', [row['id'] for row in recent])
        other_user = saved_models(self.service.database, reopened, reopened.catalog(), user_id='bob', project_id='project')
        self.assertNotIn('deepseek:deepseek-v4-pro', [row['id'] for row in other_user])
        self.assertNotIn('private-test-key', json.dumps(recent))
        unavailable = [{**row, 'configured': False} for row in reopened.catalog()]
        self.assertEqual(saved_models(self.service.database, reopened, unavailable, user_id='alice', project_id='project'), [])

    def test_active_session_rejects_selection_atomically(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        def run(*args):
            entered.set()
            release.wait(2)
            done.set()
        coordinator = SessionRunCoordinator(run)
        coordinator.wake(self.session.id)
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(SessionBusyError):
                coordinator.configure_idle(self.session.id, lambda: self.fail('must not mutate'))
        finally:
            release.set()
            done.wait(2)

    def test_api_returns_no_key_and_enforces_session_ownership(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routes import models, session
        app = FastAPI()
        app.include_router(models.router)
        app.include_router(session.router)
        with patch.object(models, 'ModelConnections', return_value=self.connections), \
             patch.object(session, '_service', return_value=(self.service, 'alice', 'project')), \
             patch('core.session_model.ModelConnections', return_value=self.connections), TestClient(app) as client:
            response = client.get('/models')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('private-test-key', response.text)
            configured = client.put('/models/connections/custom', json={
                'name': 'Local', 'base_url': 'http://localhost:8888/v1', 'api_key': '',
                'context_tokens': 128000, 'model': 'selected'})
            self.assertEqual(configured.status_code, 200)
            self.assertNotIn('private-test-key', configured.text)
            chosen = client.put(f'/session/{self.session.id}/model', json={
                'connection_id': 'custom', 'model': 'selected', 'user_id': 'alice', 'project_id': 'project'})
            self.assertEqual(chosen.status_code, 200)
            self.assertEqual(client.get(f'/session/{self.session.id}/model').json()['data']['model'], 'selected')
            with patch.object(session, '_service', return_value=(self.service, 'bob', 'project')):
                self.assertEqual(client.get(f'/session/{self.session.id}/model').status_code, 403)
                self.assertEqual(client.get(f'/session/{self.session.id}/transcript').status_code, 403)


if __name__ == '__main__':
    unittest.main()
