"""Restart layout restoration without model calls, real credentials or real data."""
from pathlib import Path
import asyncio
import sys
from tempfile import TemporaryDirectory
import unittest
import subprocess
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tui.app import HorizonApp
from tui.i18n import Preferences
from scripts.smoke_tui_session_tabs import TabClient


class TabPersistenceContracts(unittest.IsolatedAsyncioTestCase):
    async def test_typing_during_slow_startup_prevents_active_tab_stealing_focus(self):
        from tui.tab_layout import TabLayoutStore
        class SlowClient(TabClient):
            def __init__(self):
                super().__init__()
                self.started, self.release = asyncio.Event(), asyncio.Event()
            async def sessions(self):
                self.started.set()
                await self.release.wait()
                return await super().sessions()
        with TemporaryDirectory() as root:
            path = Path(root) / 'preferences.json'
            TabLayoutStore(path.with_name('tabs.json')).save(['ses_b', 'ses_a'], 'ses_b')
            client = SlowClient()
            app = HorizonApp(client, preferences=Preferences(path))
            async with app.run_test(size=(100, 30)) as pilot:
                await client.started.wait()
                app._prompt_widget.load_text('new draft')
                client.release.set()
                await pilot.pause(.15)
                self.assertEqual(list(app._session_tabs), ['ses_b', 'ses_a'])
                self.assertIsNone(app.session_id)
                self.assertEqual(app._prompt_widget.text, 'new draft')

    async def test_order_active_selection_and_closed_tabs_survive_restart(self):
        with TemporaryDirectory() as root:
            path = Path(root) / 'preferences.json'
            first = HorizonApp(TabClient(), preferences=Preferences(path))
            async with first.run_test(size=(100, 30)) as pilot:
                await first._refresh_tabs()
                await first._open('ses_b')
                await first._close_session_tab('ses_a')
                await first._open('ses_a')
                self.assertEqual(list(first._session_tabs), ['ses_b', 'ses_a'])
            client = TabClient()
            client.rows.reverse()
            second = HorizonApp(client, preferences=Preferences(path))
            async with second.run_test(size=(100, 30)) as pilot:
                await pilot.pause(.15)
                self.assertEqual(list(second._session_tabs), ['ses_b', 'ses_a'])
                self.assertEqual(second.session_id, 'ses_a')
                self.assertEqual(client.sent, [])
                await second._close_session_tab('ses_b')
            third = HorizonApp(TabClient(), preferences=Preferences(path))
            async with third.run_test(size=(100, 30)) as pilot:
                await pilot.pause(.15)
                self.assertEqual(list(third._session_tabs), ['ses_a'])

    async def test_closed_all_is_an_empty_layout_not_first_launch(self):
        with TemporaryDirectory() as root:
            path = Path(root) / 'preferences.json'
            first = HorizonApp(TabClient(), preferences=Preferences(path))
            async with first.run_test(size=(100, 30)) as pilot:
                await first._refresh_tabs()
                for sid in list(first._session_tabs):
                    await first._close_session_tab(sid)
            second = HorizonApp(TabClient(), preferences=Preferences(path))
            async with second.run_test(size=(100, 30)) as pilot:
                await pilot.pause(.15)
                self.assertEqual(list(second._session_tabs), [])
                self.assertIsNone(second.session_id)

    async def test_welcome_defers_active_restore_and_missing_history_is_skipped(self):
        from tui.tab_layout import TabLayoutStore
        from tui.welcome import WelcomeScreen
        with TemporaryDirectory() as root:
            path = Path(root) / 'preferences.json'
            store = TabLayoutStore(path.with_name('tabs.json'))
            store.save(['ses_missing', 'ses_b', 'ses_a'], 'ses_b')
            client = TabClient()
            app = HorizonApp(client, welcome=True, preferences=Preferences(path))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause(.15)
                self.assertIsInstance(app.screen, WelcomeScreen)
                self.assertEqual(list(app._session_tabs), ['ses_b', 'ses_a'])
                self.assertIsNone(app.session_id)
                await pilot.press('enter')
                await pilot.pause(.15)
                self.assertEqual(app.session_id, 'ses_b')
                self.assertEqual(client.sent, [])


class LayoutStoreContracts(unittest.TestCase):
    def test_corrupt_file_is_preserved_and_replaced_with_writable_layout(self):
        from tui.tab_layout import TabLayoutStore
        with TemporaryDirectory() as root:
            path = Path(root) / 'tabs.json'
            path.write_text('{broken')
            store = TabLayoutStore(path)
            store.save(['ses_a'], 'ses_a')
            self.assertEqual(store.load()['sessions'], ['ses_a'])
            backups = list(path.parent.glob('tabs.corrupt-*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), '{broken')

    def test_two_processes_do_not_overwrite_each_others_scope(self):
        from tui.tab_layout import TabLayoutStore
        code = '''
import sys,time
from pathlib import Path
from tui.tab_layout import TabLayoutStore
path, scope = Path(sys.argv[1]), sys.argv[2]
class SlowStore(TabLayoutStore):
    def _read(self):
        result = super()._read()
        path.with_name(scope + '.ready').touch()
        until = time.monotonic() + .2
        while len(list(path.parent.glob('*.ready'))) < 2 and time.monotonic() < until:
            time.sleep(.005)
        return result
SlowStore(path,scope=scope).save(['ses_' + scope], 'ses_' + scope)
'''
        with TemporaryDirectory() as root:
            path = Path(root) / 'tabs.json'
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            processes = [subprocess.Popen([sys.executable, '-c', code, str(path), scope],
                                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                         for scope in ('a', 'b')]
            for process in processes:
                _, error = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, error.decode())
            for scope in ('a', 'b'):
                self.assertEqual(TabLayoutStore(path, scope=scope).load()['sessions'], ['ses_' + scope])

    def test_scopes_and_empty_layout_are_persisted_separately(self):
        from tui.tab_layout import TabLayoutStore
        with TemporaryDirectory() as root:
            path = Path(root) / 'tabs.json'
            a, b = TabLayoutStore(path), TabLayoutStore(path, scope='other')
            self.assertIsNone(a.load())
            a.save(['ses_b', 'ses_a'], 'ses_a')
            b.save([], None)
            self.assertEqual(a.load(), {'sessions': ['ses_b', 'ses_a'], 'active': 'ses_a'})
            self.assertEqual(b.load(), {'sessions': [], 'active': None})
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
