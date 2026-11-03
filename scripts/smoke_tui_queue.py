"""Keyboard queue/steer/recall flows against a deterministic offline service."""
import unittest
from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp


class QueueClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.waiting = []
        self.steered = []
    async def pending(self, session_id):
        return {'session_id': session_id, 'pending': list(self.waiting)}
    async def prompt(self, session_id, text, *, message_id=None, access_mode=None, delivery='steer'):
        if delivery == 'queue':
            self.waiting.append({'message_id': message_id, 'prompt': {'text': text},
                                 'delivery': 'queue', 'access_mode': access_mode})
            return {'message_id': message_id}
        return await super().prompt(session_id, text, message_id=message_id, access_mode=access_mode)
    async def steer_pending(self, session_id, message_id):
        row = next(r for r in self.waiting if r['message_id'] == message_id)
        self.waiting.remove(row)
        self.steered.append(row)
        return {'steered': True}
    async def recall_pending(self, session_id, message_id):
        row = next(r for r in self.waiting if r['message_id'] == message_id)
        self.waiting.remove(row)
        return {'removed': True, **row}


class QueueUI(unittest.IsolatedAsyncioTestCase):
    async def test_enter_queues_then_separate_enter_confirms_steering(self):
        client = QueueClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            app.session_id = 'ses_ui'
            app._show_chat()
            app._set_phase('正在思考', busy=True)
            app._prompt_widget.load_text('Next task')
            await pilot.press('enter')
            await pilot.pause(.1)
            self.assertEqual(len(client.waiting), 1)
            self.assertEqual(app._prompt_widget.text, '')
            self.assertEqual(len(client.steered), 0)
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertEqual(type(app.screen).__name__, 'QueueConfirm')
            self.assertEqual(len(client.steered), 0)
            await pilot.press('escape')
            await pilot.pause(.05)
            self.assertEqual(len(client.waiting), 1)
            await pilot.press('enter')
            await pilot.pause(.05)
            await pilot.press('enter')
            await pilot.pause(.1)
            self.assertEqual(len(client.waiting), 0)
            self.assertEqual(len(client.steered), 1)
            self.assertFalse(client.interrupted)

    async def test_recall_preserves_draft_and_admission_permission(self):
        client = QueueClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            app.session_id = 'ses_ui'
            app._show_chat()
            app._set_phase('正在思考', busy=True)
            app._access_mode = 'read_only'
            app._prompt_widget.load_text('Queued task')
            await pilot.press('enter')
            await pilot.pause(.1)
            self.assertEqual(client.waiting[0]['access_mode'], 'read_only')
            app._access_mode = 'full_access'
            app._prompt_widget.load_text('Unsent draft')
            await pilot.press('ctrl+r')
            await pilot.pause(.1)
            self.assertEqual(client.waiting, [])
            self.assertEqual(app._prompt_widget.text, 'Queued task')
            self.assertEqual(app._access_mode, 'read_only')
            await pilot.press('ctrl+g')
            self.assertEqual(app._prompt_widget.text, 'Unsent draft')

    async def test_pending_queue_stays_above_composer_not_in_transcript(self):
        client = QueueClient()
        client.waiting = [{'message_id': 'q', 'prompt': {'text': 'Waiting task'}, 'delivery': 'queue'}]
        app = HorizonApp(client)
        async with app.run_test(size=(70, 24)) as pilot:
            app.session_id = 'ses_ui'
            app._show_chat()
            await app._refresh_pending()
            await app._render_context([{'type': 'user', 'id': 'q', 'text': 'Waiting task',
                                       'delivery_state': 'pending', 'delivery': 'queue'}])
            await pilot.pause()
            self.assertEqual(len(app.query('.user-message')), 0)
            self.assertLessEqual(app.query_one('#pending-queue').region.bottom,
                                 app.query_one('#composer').region.y)


    async def test_failed_admission_preserves_input_and_retry_identifier(self):
        class Ambiguous(QueueClient):
            def __init__(self):
                super().__init__()
                self.ids = []
            async def prompt(self, sid, text, **kwargs):
                self.ids.append(kwargs['message_id'])
                if len(self.ids) == 1:
                    raise ConnectionError('offline')
                return await super().prompt(sid, text, **kwargs)
        client = Ambiguous()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            app.session_id = 'ses_ui'
            app._show_chat()
            app._set_phase('正在思考', busy=True)
            app._prompt_widget.load_text('Keep this draft')
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertEqual(app._prompt_widget.text, 'Keep this draft')
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertEqual(client.ids[0], client.ids[1])
            self.assertEqual(len(client.waiting), 1)

    async def test_multiple_recalls_preserve_all_drafts_and_modes(self):
        client = QueueClient()
        client.waiting = [{'message_id': str(i), 'delivery': 'queue', 'prompt': {'text': text},
                           'access_mode': 'read_only'} for i, text in enumerate(['First', 'Second'])]
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            app.session_id = 'ses_ui'
            app._show_chat()
            await app._refresh_pending()
            app._access_mode = 'full_access'
            app._prompt_widget.load_text('Original')
            await pilot.press('ctrl+r')
            await pilot.pause(.05)
            await pilot.press('ctrl+r')
            await pilot.pause(.05)
            self.assertEqual(app._prompt_widget.text, 'First')
            await pilot.press('ctrl+g')
            self.assertEqual(app._prompt_widget.text, 'Second')
            self.assertEqual(app._access_mode, 'read_only')
            await pilot.press('ctrl+g')
            self.assertEqual(app._prompt_widget.text, 'Original')
            self.assertEqual(app._access_mode, 'full_access')

    async def test_previous_session_queue_cannot_control_current_session(self):
        client = QueueClient()
        client.waiting = [{'message_id': 'q', 'delivery': 'queue', 'prompt': {'text': 'Private draft'}}]
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            app.session_id = 'ses_ui'
            await app._refresh_pending()
            app.session_id = 'ses_other'
            await pilot.press('ctrl+r', 'enter')
            await pilot.pause(.05)
            self.assertEqual(len(client.waiting), 1)
            self.assertNotEqual(type(app.screen).__name__, 'QueueConfirm')


if __name__ == '__main__':
    unittest.main()
