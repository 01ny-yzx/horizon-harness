"""Global choice survives restart; each conversation freezes its creation choice."""
import json
import unittest
from unittest.mock import patch

from scripts import smoke_tui_models as fixtures
from core.session_model import SessionModelSelection


class GlobalModelContracts(unittest.TestCase):
    setUp = fixtures.Models.setUp

    def test_global_default_survives_restart_and_invalid_update_keeps_it(self):
        from core.model_default import GlobalModelSelection
        from core.model_connections import ModelConnections
        selector = GlobalModelSelection(self.connections)
        credentials = self.connections.path.read_bytes()
        chosen = selector.select('custom', 'selected')
        reopened = GlobalModelSelection(ModelConnections(self.root, self.settings))
        self.assertEqual(reopened.get(), chosen)
        self.assertEqual(self.connections.path.read_bytes(), credentials)
        with patch('core.model_default.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                reopened.select(self.connections.default_id, 'original')
        self.assertEqual(reopened.get(), chosen)
        self.assertEqual(list(selector.path.parent.glob('.default-model-*')), [])
        self.assertNotIn('private-test-key', selector.path.read_text())
        with self.assertRaises(ValueError):
            reopened.select('not-configured', 'no-model')
        self.assertEqual(reopened.get(), chosen)

    def test_new_sessions_pin_global_choice_and_old_sessions_never_follow_changes(self):
        from core.model_default import GlobalModelSelection
        selector = GlobalModelSelection(self.connections)
        def create():
            with patch('core.model_connections.ModelConnections', return_value=self.connections):
                return self.service.create(user_id='alice', project_id='project',
                                           workspace_id='alice/project', directory=self.root)
        old = create()
        old_choice = SessionModelSelection(self.service.database, self.connections).get(old.id)
        selector.select('custom', 'selected')
        new = create()
        selections = SessionModelSelection(self.service.database, self.connections)
        self.assertEqual(selections.get(new.id), {'connection_id': 'custom', 'model': 'selected'})
        self.assertEqual(selections.get(old.id), old_choice)
        selector.select(self.connections.default_id, 'original')
        self.assertEqual(selections.get(new.id), {'connection_id': 'custom', 'model': 'selected'})
        self.assertEqual(selections.get(old.id), old_choice)

    def test_api_default_roundtrip_does_not_mutate_sessions_or_expose_keys(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routes import models
        app = FastAPI()
        app.include_router(models.router)
        with patch.object(models, 'ModelConnections', return_value=self.connections), \
             patch.object(models, 'get_database_service', return_value=self.service.database), TestClient(app) as client:
            response = client.put('/models/default', json={'connection_id': 'custom', 'model': 'selected'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('private-test-key', response.text)
            self.assertEqual(client.get('/models').json()['data']['current'], response.json()['data'])
            self.assertEqual(client.put('/models/default', json={'connection_id': 'missing', 'model': 'no'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
