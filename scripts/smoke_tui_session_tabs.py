"""Session tab navigation and state isolation with an offline fake service."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp
from tui.terminal_colors import TerminalBackground, message_background


class TabClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.rows = [{'id': 'ses_a', 'title': 'Apple warranty', 'time_updated': 2},
                     {'id': 'ses_b', 'title': 'Project analysis', 'time_updated': 1}]
        self.running = set()
        self.queues = {row['id']: asyncio.Queue() for row in self.rows}

    async def sessions(self):
        return list(self.rows)

    async def context(self, session_id):
        return [{'id': session_id, 'type': 'user', 'text': session_id + '\n' + '\n'.join(str(i) for i in range(50))}]

    async def active(self):
        return list(self.running)

    async def events(self, session_id, after=0):
        while True:
            yield await self.queues.setdefault(session_id, asyncio.Queue()).get()


class SessionTabsContracts(unittest.IsolatedAsyncioTestCase):
    async def test_scroll_after_tab_switch_is_not_overridden_by_delayed_restore(self):
        app = HorizonApp(TabClient())
        async with app.run_test(size=(100, 30)) as pilot:
            await app._open('ses_a')
            await pilot.pause()
            view = app.query_one('#transcript')
            await app._open('ses_b')
            view.scroll_to(y=7, animate=False, force=True, immediate=True)
            await pilot.pause()
            self.assertEqual(view.scroll_y, 7)

    async def test_stream_prefix_is_preserved_and_missed_deltas_are_replayed(self):
        class ReplayClient(TabClient):
            def __init__(self):
                super().__init__()
                self.history = {sid: [] for sid in self.queues}

            def publish(self, sid, text):
                event = {'id': len(self.history[sid]) + 1, 'type': 'llm.delta',
                         'data': {'call_id': 'answer', 'text': text}}
                self.history[sid].append(event)
                self.queues[sid].put_nowait(event)

            async def events(self, sid, after=0):
                cursor = len(self.history[sid]) if after is None else after
                if after is None:
                    yield {'id': cursor, 'type': 'session.snapshot',
                           'data': {'active': sid in self.running, 'messages': await self.context(sid)}}
                for event in self.history[sid]:
                    if event['id'] > cursor:
                        cursor = event['id']
                        yield event
                while True:
                    event = await self.queues[sid].get()
                    if event['id'] > cursor:
                        cursor = event['id']
                        yield event
        client = ReplayClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            await app._open('ses_a')
            await pilot.pause()
            client.running.add('ses_a')
            client.publish('ses_a', 'prefix ')
            await pilot.pause()
            self.assertEqual(app._live, {'answer': 'prefix '})
            await app._open('ses_b')
            client.publish('ses_a', 'middle ')
            await app._open('ses_a')
            await pilot.pause()
            client.publish('ses_a', 'suffix')
            await pilot.pause()
            self.assertEqual(app._live, {'answer': 'prefix middle suffix'})

    async def test_click_close_shortcuts_and_narrow_overflow(self):
        client = TabClient()
        client.rows[0]['title'] = 'A very long conversation title that needs truncation'
        app = HorizonApp(client)
        async with app.run_test(size=(60, 24)) as pilot:
            await app._refresh_tabs()
            await app._open('ses_b')
            await pilot.pause()
            active = app.query_one('.active-tab')
            self.assertLessEqual(active.size.width, 26)
            self.assertGreaterEqual(active.region.x, app._tab_bar.region.x)
            self.assertLessEqual(active.region.right, app._tab_bar.region.right)
            await pilot.hover(active.query_one('.tab-label'))
            await pilot.pause()
            self.assertTrue(active.query_one('.tab-close').visible)
            await pilot.click(active.query_one('.tab-close'))
            await pilot.pause()
            self.assertEqual(app.session_id, 'ses_a')
            self.assertNotIn('ses_b', app._session_tabs)
            await pilot.press('ctrl+w')
            self.assertIsNone(app.session_id)
            self.assertTrue(app.has_class('home'))
            self.assertEqual(len(await client.sessions()), 2)

    async def test_drafts_scroll_and_close_preserve_history(self):
        client = TabClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            await app._refresh_tabs()
            self.assertIn('ses_a', app._session_tabs)
            await app._open('ses_a')
            app._prompt_widget.load_text('draft A')
            view = app.query_one('#transcript')
            view.scroll_to(y=5, animate=False, force=True)
            await pilot.pause()
            await app._open('ses_b')
            self.assertEqual(app._prompt_widget.text, '')
            app._prompt_widget.load_text('draft B')
            await app._open('ses_a')
            await pilot.pause()
            self.assertEqual(app._prompt_widget.text, 'draft A')
            self.assertEqual(view.scroll_y, 5)
            await app._close_session_tab('ses_b')
            self.assertEqual([r['id'] for r in await client.sessions()], ['ses_a', 'ses_b'])
            self.assertNotIn('ses_b', app._session_tabs)
            await app._refresh_tabs()
            self.assertNotIn('ses_b', app._session_tabs)
            await app._open('ses_b')
            self.assertEqual(app._prompt_widget.text, 'draft B')

    async def test_switch_away_from_running_session_and_terminal_background(self):
        client = TabClient()
        with patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            app = HorizonApp(client)
        async with app.run_test(size=(80, 24)) as pilot:
            await app._open('ses_a')
            client.running.add('ses_a')
            app._set_phase('正在回答', busy=True)
            await app._open('ses_b')
            self.assertEqual(app.session_id, 'ses_b')
            self.assertFalse(app.busy)
            self.assertFalse(client.interrupted)
            self.assertIn('ses_a', app._active_tab_sessions)
            await app._refresh_tabs()
            client.rows[0]['title'] = 'Updated title'
            await app._refresh_tabs()
            self.assertEqual(app._session_tabs['ses_a'].title, 'Updated title')
            for rgb in ((255, 255, 255), (24, 24, 24)):
                app.post_message(TerminalBackground(rgb))
                await pilot.pause()
                expected = message_background(rgb)
                tab = app.query_one('.session-tab')
                self.assertTrue(tab.rich_style.bgcolor.is_default)
                self.assertEqual(app.query_one('.user-message').styles.background.hex.lower(), expected)
            await app._close_session_tab('ses_a')
            self.assertIn('ses_a', client.running)
            self.assertFalse(client.interrupted)


if __name__ == '__main__':
    unittest.main()
