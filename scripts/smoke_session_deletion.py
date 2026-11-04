"""Session deletion: isolated storage, ownership, busy guard, and terminal confirmation."""
import asyncio
from pathlib import Path
import sys
import sqlite3
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.session import SessionService
from core.session_execution import SessionExecution
from core.session_run_coordinator import SessionBusyError
from core.session_store import SessionNotFoundError
from core.session_live import session_live


class DeletionStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.execution = SessionExecution(self.root / 'test.db')
        self.service = SessionService(database=self.execution.store.database, execution=self.execution)
        self.first = self.create('ses_delete')
        self.other = self.create('ses_keep')

    def create(self, sid):
        return self.service.create(session_id=sid, user_id='u', project_id='p', workspace_id='u/p', directory=self.root)

    def test_deletion_cascades_only_this_session_and_survives_reopen(self):
        sid = self.first.id
        prompt = self.service.prompt(sid, 'test', resume=False)
        self.service.inputs.promote_steers(sid, prompt.admitted_seq)
        self.service.prompt(sid, 'queued', delivery='queue', resume=False)
        with self.service.database.write_transaction() as db:
            db.execute('INSERT INTO session_context_epoch VALUES (?,?,?,?)', (sid, 'baseline', '{}', 0))
            db.execute('INSERT INTO memory_user_preference VALUES (?,?,?,?,?,?)', ('u','language','zh','test',1,1))
        protected = self.root / 'project.txt'
        protected.write_text('project content')
        credentials = self.root / 'providers.json'
        credentials.write_text('private credentials')
        self.service.delete(sid)
        reopened = SessionService(database=self.service.database, execution=self.execution)
        self.assertEqual([row.id for row in reopened.list()], [self.other.id])
        with self.assertRaises(SessionNotFoundError):
            reopened.get(sid)
        with self.service.database.read_transaction() as db:
            for table in ('session_message','session_input','session_context_epoch'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE session_id=?', (sid,)).fetchone()[0], 0)
            for table in ('event','event_sequence'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE aggregate_id=?', (sid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM session_input_control').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT value FROM memory_user_preference').fetchone()[0], 'zh')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(session_live.read(sid).events, ())
        self.assertEqual(protected.read_text(), 'project content')
        self.assertEqual(credentials.read_text(), 'private credentials')

    def test_missing_working_directory_does_not_prevent_deletion(self):
        missing = self.root / 'gone'
        missing.mkdir()
        session = self.service.create(user_id='u', project_id='p', workspace_id='u/p', directory=missing)
        missing.rmdir()
        self.service.delete(session.id)
        with self.assertRaises(SessionNotFoundError):
            self.service.get(session.id)

    def test_busy_session_is_not_deleted(self):
        started, finish = threading.Event(), threading.Event()
        def run(*args):
            started.set()
            finish.wait(3)
        self.execution._runner_resolver = lambda _: NS(run=run)
        self.service.prompt(self.first.id, 'busy', resume=False)
        self.execution.wake(self.first.id)
        self.assertTrue(started.wait(2))
        try:
            with self.assertRaises(SessionBusyError):
                self.service.delete(self.first.id)
            self.assertEqual(self.service.get(self.first.id).id, self.first.id)
        finally:
            finish.set()
            self.execution.interrupt(self.first.id)

    def test_atomic_rollback_keeps_history_if_delete_fails(self):
        sid = self.first.id
        with self.service.database.write_transaction() as db:
            db.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON event_sequence BEGIN SELECT RAISE(ABORT, 'blocked'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.delete(sid)
        self.assertEqual(self.service.get(sid).id, sid)
        self.assertGreaterEqual(len(self.service.history(sid).events), 1)

    def test_api_checks_ownership_and_missing_session(self):
        import httpx
        from fastapi import FastAPI
        from api.routes import session as routes
        app = FastAPI()
        app.include_router(routes.router)
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                with patch.object(routes, '_service', return_value=(self.service, 'other-user', 'p')):
                    denied = await client.delete(f'/session/{self.first.id}')
                    self.assertEqual(denied.status_code, 403)
                with patch.object(routes, '_service', return_value=(self.service, 'u', 'p')):
                    deleted = await client.delete(f'/session/{self.first.id}')
                    self.assertEqual(deleted.status_code, 200)
                    self.assertTrue(deleted.json()['data']['deleted'])
                    missing = await client.delete(f'/session/{self.first.id}')
                    self.assertEqual(missing.status_code, 404)
        asyncio.run(check())


class DeletionTerminal(unittest.IsolatedAsyncioTestCase):
    async def test_default_cancel_and_confirm_current_session_delete(self):
        from scripts.smoke_tui_session_tabs import TabClient
        from tui.app import HorizonApp
        from tui.session_history import SessionDeleteConfirm, SessionHistoryPicker
        class Client(TabClient):
            def __init__(self):
                super().__init__()
                self.deleted = []
            async def delete_session(self, sid):
                self.deleted.append(sid)
                self.rows = [row for row in self.rows if row['id'] != sid]
                return {'id': sid, 'deleted': True}
        client = Client()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 32)) as pilot:
            await app._open('ses_a')
            await pilot.pause()
            await app.action_history()
            await pilot.press('delete')
            await pilot.pause()
            self.assertIsInstance(app.screen, SessionDeleteConfirm)
            await pilot.press('enter')
            await pilot.pause()
            self.assertEqual(client.deleted, [])
            self.assertIsInstance(app.screen, SessionHistoryPicker)
            await pilot.press('backspace')
            await pilot.pause()
            self.assertIsInstance(app.screen, SessionDeleteConfirm)
            await pilot.press('escape')
            await pilot.pause()
            self.assertEqual(client.deleted, [])
            await pilot.press('delete')
            await pilot.press('right', 'enter')
            await pilot.pause(.2)
            self.assertEqual(client.deleted, ['ses_a'])
            self.assertIsNone(app.session_id)
            self.assertNotIn('ses_a', app._session_tabs)
            self.assertNotIn('ses_a', app._closed_tab_views)
            self.assertIsInstance(app.screen, SessionHistoryPicker)
            self.assertEqual([row['id'] for row in app.screen.rows], ['ses_b'])
            await pilot.press('escape')
            await pilot.pause()
            self.assertIs(app.focused, app._prompt_widget)

    async def test_backend_failure_keeps_history_and_tabs(self):
        from scripts.smoke_tui_session_tabs import TabClient
        from tui.app import HorizonApp
        class Client(TabClient):
            async def delete_session(self, sid):
                raise RuntimeError('server unavailable')
        app = HorizonApp(Client())
        async with app.run_test(size=(100, 32)) as pilot:
            await app._open('ses_a')
            await pilot.pause()
            await app._delete_session('ses_a')
            await pilot.pause()
            self.assertEqual(app.session_id, 'ses_a')
            self.assertIn('ses_a', app._session_tabs)
            self.assertEqual(len(app.client.rows), 2)


    async def test_background_delete_and_restart_layout(self):
        from scripts.smoke_tui_session_tabs import TabClient
        from tui.app import HorizonApp
        from tui.i18n import Preferences
        from tui.session_history import SessionHistoryPicker
        class Client(TabClient):
            async def delete_session(self, sid):
                self.rows = [row for row in self.rows if row['id'] != sid]
                return {'id': sid, 'deleted': True}
        with TemporaryDirectory() as root:
            prefs = Preferences(Path(root) / 'preferences.json')
            client = Client()
            app = HorizonApp(client, preferences=prefs)
            async with app.run_test(size=(100, 32)) as pilot:
                await app._refresh_tabs()
                await app._open('ses_a')
                await pilot.pause()
                await app.action_history()
                await pilot.pause()
                await pilot.press('down')
                await pilot.pause()
                self.assertIsInstance(app.screen, SessionHistoryPicker)
                self.assertEqual(app.screen.query_one('#picker-options').highlighted, 1)
                await pilot.press('delete')
                await pilot.pause()
                await pilot.click('#session-delete-yes')
                await pilot.pause(.15)
                self.assertEqual(app.session_id, 'ses_a')
                self.assertNotIn('ses_b', app._session_tabs)
                self.assertEqual([row['id'] for row in client.rows], ['ses_a'])
            reopened = HorizonApp(client, preferences=prefs)
            async with reopened.run_test(size=(100, 32)) as pilot:
                await pilot.pause(.15)
                self.assertEqual(list(reopened._session_tabs), ['ses_a'])
                self.assertEqual(reopened.session_id, 'ses_a')
                await reopened.action_history()
                await pilot.press('delete', 'right', 'enter')
                await pilot.pause(.15)
                self.assertEqual(reopened._session_tabs, {})
                self.assertIsNone(reopened.session_id)
                self.assertEqual(reopened.screen.rows, [])
                self.assertEqual(len(reopened.screen.query('#history-open, #history-delete')), 0)
                await pilot.press('escape')
                self.assertTrue(reopened.has_class('home'))

    async def test_stale_tab_refresh_cannot_restore_deleted_session(self):
        from scripts.smoke_tui_session_tabs import TabClient
        from tui.app import HorizonApp
        class Client(TabClient):
            def __init__(self):
                super().__init__()
                self.capture = False
                self.started, self.release = asyncio.Event(), asyncio.Event()
            async def sessions(self):
                snapshot = list(self.rows)
                if self.capture:
                    self.capture = False
                    self.started.set()
                    await self.release.wait()
                return snapshot
            async def delete_session(self, sid):
                self.rows = [row for row in self.rows if row['id'] != sid]
                return {'id': sid, 'deleted': True}
        client = Client()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 32)) as pilot:
            await app._refresh_tabs()
            await app._open('ses_a')
            await pilot.pause()
            client.capture = True
            refreshing = asyncio.create_task(app._refresh_tabs())
            await client.started.wait()
            try:
                await app._delete_session('ses_b')
            finally:
                client.release.set()
                await refreshing
            await pilot.pause()
            self.assertNotIn('ses_b', app._session_tabs)
            self.assertNotIn('ses_b', app._closed_tab_views)
            self.assertEqual(app.session_id, 'ses_a')

    async def test_cannot_submit_during_slow_delete_and_deleted_drafts_are_forgotten(self):
        from scripts.smoke_tui_session_tabs import TabClient
        from tui.app import HorizonApp, PromptInput
        class Client(TabClient):
            def __init__(self):
                super().__init__()
                self.started, self.release = asyncio.Event(), asyncio.Event()
            async def delete_session(self, sid):
                self.started.set()
                await self.release.wait()
                self.rows = [row for row in self.rows if row['id'] != sid]
                return {'id': sid, 'deleted': True}
        client = Client()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 32)) as pilot:
            await app._open('ses_a')
            await pilot.pause()
            app._saved_drafts['ses_a'] = [('old private draft', 'read_only')]
            app._queue_retry = (('ses_a', 'queued', 'read_only'), 'msg_retry')
            deleting = asyncio.create_task(app._delete_session('ses_a'))
            await client.started.wait()
            try:
                from textual.screen import ModalScreen
                app.action_help()
                await app.action_language()
                self.assertNotIsInstance(app.screen, ModalScreen)
                await app.on_prompt_input_submitted(PromptInput.Submitted('new message'))
                await pilot.pause(.05)
                self.assertEqual(client.sent, [])
            finally:
                client.release.set()
                await deleting
            await pilot.pause()
            self.assertIsNone(app.session_id)
            self.assertFalse(app.busy)
            self.assertFalse(app._prompt_widget.disabled)
            self.assertNotIn('ses_a', app._saved_drafts)
            self.assertIsNone(app._queue_retry)

    async def test_confirmation_centered_without_history_action_buttons(self):
        from scripts.smoke_tui_session_tabs import TabClient
        from tui.app import HorizonApp
        for size in ((120, 30), (80, 24)):
            with self.subTest(size=size):
                app = HorizonApp(TabClient())
                app.language = 'zh' if size[0] == 120 else 'en'
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    await app.action_history()
                    await pilot.pause()
                    self.assertEqual(len(app.screen.query('#history-open, #history-delete')), 0)
                    await pilot.press('delete')
                    await pilot.pause()
                    panel = app.screen.query_one('#session-delete-confirm')
                    self.assertLessEqual(abs(panel.region.x * 2 + panel.region.width - size[0]), 1)
                    self.assertLessEqual(abs(panel.region.y * 2 + panel.region.height - size[1]), 1)
                    self.assertEqual(app.screen.focused.id, 'session-delete-cancel')
                    self.assertLessEqual(panel.region.width, 54)
                    self.assertEqual(len(panel.query('#session-delete-title, Label, .picker-hint')), 0)
                    warning = panel.query_one('#session-delete-warning')
                    self.assertEqual(warning.styles.text_align, 'center')
                    self.assertEqual(warning.styles.content_align_horizontal, 'center')
                    for line_index in range(warning.size.height):
                        line = warning.render_line(line_index).text
                        if line.strip():
                            leading = len(line) - len(line.lstrip())
                            trailing = len(line) - len(line.rstrip())
                            self.assertLessEqual(abs(leading - trailing), 1)
                    self.assertEqual(warning.styles.color.hex.lower(), '#f2a35c')
                    app.add_class('terminal-light')
                    await pilot.pause()
                    self.assertEqual(warning.styles.color.hex.lower(), '#b45309')
                    cancel = app.screen.query_one('#session-delete-cancel').region
                    delete = app.screen.query_one('#session-delete-yes').region
                    self.assertLessEqual(abs(cancel.x + delete.right - (panel.region.x * 2 + panel.region.width)), 1)

    async def test_mouse_click_still_opens_a_history_session(self):
        from scripts.smoke_tui_session_tabs import TabClient
        from tui.app import HorizonApp
        from tui.session_history import SessionHistoryPicker
        app = HorizonApp(TabClient())
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await app.action_history()
            await pilot.pause()
            await pilot.click('#picker-options', offset=(4, 1))
            await pilot.pause()
            self.assertNotIsInstance(app.screen, SessionHistoryPicker)
            self.assertEqual(app.session_id, 'ses_b')

    async def test_client_delete_identity_and_busy_error_is_safe(self):
        import httpx
        from tui.client import HorizonClient
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(409, json={'detail': 'private-provider-key'})
        async with HorizonClient('http://test', user_id='u', project_id='p',
                transport=httpx.MockTransport(respond), language='en') as client:
            with self.assertRaises(RuntimeError) as error:
                await client.delete_session('ses_target')
        self.assertIn('Interrupt', str(error.exception))
        self.assertNotIn('private-provider-key', str(error.exception))
        self.assertEqual(requests[0].method, 'DELETE')
        self.assertEqual(requests[0].url.path, '/session/ses_target')
        self.assertEqual(dict(requests[0].url.params), {'user_id': 'u', 'project_id': 'p'})


if __name__ == '__main__':
    unittest.main()
