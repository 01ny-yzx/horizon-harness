"""Per-input permissions and direct reuse of saved models; no paid providers."""
import os
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from scripts.smoke_tui_ui import FakeClient


class AccessRuntime(unittest.TestCase):
    def test_context_is_thread_local_and_both_file_and_tool_guards_follow_it(self):
        from core.agent_access_policy import access_mode_scope, evaluate_agent_file_access, evaluate_agent_tool_access, get_agent_access_mode
        barrier = threading.Barrier(2)
        results = {}
        def check(mode):
            with access_mode_scope(mode):
                barrier.wait(timeout=2)
                results[mode] = (get_agent_access_mode(), evaluate_agent_tool_access(NS(side_effect=True)).allowed,
                                 evaluate_agent_file_access('/tmp/test', operation='write').allowed)
        with patch.dict(os.environ, AGENT_ACCESS_MODE='read_only'):
            threads = [threading.Thread(target=check, args=(mode,)) for mode in ('read_only', 'full_access')]
            for t in threads: t.start()
            for t in threads: t.join(3)
            self.assertEqual(results['read_only'], ('read_only', False, False))
            self.assertEqual(results['full_access'], ('full_access', True, True))
            self.assertEqual(get_agent_access_mode(), 'read_only')

    def test_bound_mode_blocks_actual_file_write_and_full_access_allows_it(self):
        from core.agent_access_policy import access_mode_scope
        from tools.file_tools import write_file
        with TemporaryDirectory() as tmp, patch.dict(os.environ, AGENT_ACCESS_MODE='full_access'):
            target = Path(tmp)/'write.txt'
            with access_mode_scope('read_only'):
                self.assertFalse(write_file(str(target), 'denied', overwrite=True)['success'])
                self.assertFalse(target.exists())
            with access_mode_scope('full_access'):
                self.assertTrue(write_file(str(target), 'allowed', overwrite=True)['success'])
                self.assertEqual(target.read_text(), 'allowed')

    def test_admitted_permission_survives_reopen_and_runner_binds_it(self):
        from core.session import SessionService
        from core.session_runner import SessionRunner
        from core.agent_access_policy import get_agent_access_mode
        from core.session_input import SessionPromptConflictError
        with TemporaryDirectory() as tmp:
            service = SessionService(Path(tmp)/'test.db')
            session = service.create(user_id='u', project_id='p', workspace_id='u/p', directory=tmp)
            admitted = service.prompt(session.id, 'write', access_mode='full_access', resume=False)
            with self.assertRaises(SessionPromptConflictError):
                service.prompt(session.id, 'write', message_id=admitted.id, access_mode='read_only', resume=False)
            seen = []
            def work(text, promotion, cancelled):
                seen.append(get_agent_access_mode())
                service.inputs.promote_steers(session.id, 1000)
            with patch.dict(os.environ, AGENT_ACCESS_MODE='read_only'):
                SessionRunner(work, database=service.database).run(session.id, False, threading.Event())
            self.assertEqual(seen, ['full_access'])
            self.assertEqual(service.inputs.access_mode(admitted.id), 'full_access')


class AccessUI(unittest.IsolatedAsyncioTestCase):
    async def test_tab_changes_only_icon_preserves_draft_and_modal_fields_keep_tab(self):
        from tui.app import HorizonApp, PromptInput
        from tui.pickers import ConnectionForm
        from tui.i18n import Preferences
        from textual.widgets import Input
        with TemporaryDirectory() as tmp:
            prefs = Preferences(Path(tmp)/'ui.json')
            client = FakeClient()
            app = HorizonApp(client, preferences=prefs)
            async with app.run_test(size=(80,24)) as pilot:
                await pilot.pause(.05)
                field = app.query_one(PromptInput)
                field.load_text('my draft')
                self.assertIn('◆', str(app.query_one('#composer-hint').render()))
                await pilot.press('tab')
                self.assertIn('◆', str(app.query_one('#composer-hint').render()))
                self.assertNotIn('full_access', str(app.query_one('#composer-hint').render()))
                self.assertEqual(field.text, 'my draft')
                self.assertTrue(field.has_focus)
                self.assertEqual(Preferences(prefs.path).access_mode, 'full_access')
                await pilot.press('enter')
                await pilot.pause(.05)
                self.assertEqual(client.prompt_modes[-1], 'full_access')
                app._set_phase('正在思考', busy=True)
                await pilot.press('tab')
                self.assertEqual(client.prompt_modes[-1], 'full_access')
                self.assertTrue(app.busy)
                app.busy = False
                await app._model_picked({'id':'__custom__','configured':False})
                await pilot.pause(.05)
                self.assertIsInstance(app.screen, ConnectionForm)
                mode = app._access_mode
                before = app.screen.focused
                await pilot.press('tab')
                self.assertIsInstance(app.screen.focused, Input)
                self.assertIsNot(app.screen.focused, before)
                self.assertEqual(app._access_mode, mode)

    async def test_tab_during_admission_does_not_change_already_submitted_mode(self):
        from tui.app import HorizonApp, PromptInput
        class DelayedClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.entered = asyncio.Event()
                self.release = asyncio.Event()
            async def create(self):
                self.entered.set()
                await self.release.wait()
                return await super().create()
        client = DelayedClient()
        app = HorizonApp(client)
        async with app.run_test(size=(80,24)) as pilot:
            app.query_one(PromptInput).load_text('already sent')
            await pilot.press('enter')
            await asyncio.wait_for(client.entered.wait(), 1)
            await pilot.press('tab')
            self.assertEqual(app._access_mode, 'full_access')
            client.release.set()
            await pilot.pause(.05)
            self.assertEqual(client.prompt_modes, ['read_only'])

    async def test_recent_models_switch_directly_and_add_opens_providers(self):
        from tui.app import HorizonApp
        from textual.widgets import Input
        class RecentClient(FakeClient):
            async def models(self):
                data = await super().models()
                return {**data, 'recent': data['models']}
            async def connection(self, key):
                raise AssertionError('saved model should reuse credentials')
        client = RecentClient()
        app = HorizonApp(client)
        async with app.run_test(size=(80,24)) as pilot:
            await app.action_models()
            await pilot.pause(.05)
            self.assertEqual(len(app.screen.rows), 3)
            self.assertEqual(app.screen.rows[-1]['id'], '__add_model__')
            self.assertEqual(len(app.screen.query(Input)), 0)
            await pilot.press('down','enter')
            await pilot.pause(.05)
            self.assertEqual(client.default_model['model'], 'other-model')
            self.assertEqual(client.configured, [])
            await app.action_models()
            await pilot.pause(.05)
            await pilot.press('end','enter')
            await pilot.pause(.05)
            self.assertEqual(app.screen.title_text, 'Select provider')
            await pilot.press('escape')
            await pilot.pause(.05)
            self.assertEqual(app.screen.rows[-1]['id'], '__add_model__')


if __name__ == '__main__': unittest.main()
