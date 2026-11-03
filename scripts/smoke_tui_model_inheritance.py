"""A configured UI model must work for subsequent fresh sessions too."""
import unittest

from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp


class ModelInheritance(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_inherits_provider_and_model_before_prompt(self):
        class Client(FakeClient):
            serial = 0
            async def create(self):
                self.serial += 1
                self.model_choices[f'ses_{self.serial}'] = dict(self.default_model)
                return {'id': f'ses_{self.serial}', 'title': 'test'}
            async def prompt(self, session_id, text, **kwargs):
                self.asserted_choice = self.model_choices.get(session_id)
                return await super().prompt(session_id, text, **kwargs)
        client = Client()
        app = HorizonApp(client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._apply_model({'connection_id': 'custom-provider', 'model': 'chosen-model'})
            await app.action_new_session()
            self.assertEqual(app._selected_model, 'chosen-model')
            await app._send('hello')
            self.assertEqual(client.asserted_choice,
                             {'connection_id': 'custom-provider', 'model': 'chosen-model'})

    async def test_fresh_start_uses_global_model_without_env_default(self):
        class Client(FakeClient):
            async def status(self):
                raise RuntimeError('No environment model configured')
            async def models(self):
                row = {'id': 'saved:model', 'connection_id': 'saved', 'model': 'model', 'configured': True}
                return {'current': dict(self.default_model),
                        'models': [row], 'recent': [row]}
        client = Client()
        client.default_model = {'connection_id': 'saved', 'model': 'model'}
        app = HorizonApp(client)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(app._selected_model, 'model')
            await app._send('hello')
            self.assertEqual(client.model_choices[app.session_id],
                             {'connection_id': 'saved', 'model': 'model'})

    async def test_changing_global_in_old_chat_does_not_change_its_model(self):
        client = FakeClient()
        client.model_choices['ses_old'] = {'connection_id': 'test', 'model': 'offline-test'}
        app = HorizonApp(client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._open('ses_old')
            await app._apply_model({'connection_id': 'test', 'model': 'other-model'})
            self.assertEqual(client.default_model['model'], 'other-model')
            self.assertEqual(client.model_choices['ses_old']['model'], 'offline-test')
            self.assertEqual(app._selected_model, 'offline-test')
            await app.action_new_session()
            self.assertEqual(app._selected_model, 'other-model')
            await app._send('new question')
            self.assertEqual(client.model_choices['ses_ui']['model'], 'other-model')


if __name__ == '__main__':
    unittest.main()
