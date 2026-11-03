"""Real headless scroll/layout and incremental Markdown checks; offline service."""
import asyncio
import unittest

from textual.widgets import Markdown
from textual import events
from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp


def history():
    return [{'type': 'user', 'text': f'Previous message {i}'} for i in range(20)]


class TranscriptScroll(unittest.IsolatedAsyncioTestCase):
    async def test_steer_short_conversation_stays_top_aligned(self):
        from scripts.smoke_tui_queue import QueueClient
        class SteerClient(QueueClient):
            async def steer_pending(self, sid, mid):
                result = await super().steer_pending(sid, mid)
                self.messages.append({'id': mid, 'type': 'user', 'text': 'Why is it taking so long?',
                                      'delivery_state': 'pending', 'delivery': 'steer'})
                return result
        client = SteerClient()
        client.messages = [{'id': 'first', 'type': 'user', 'text': 'Look at my project'}]
        client.waiting = [{'message_id': 'later', 'delivery': 'queue', 'prompt': {'text': 'Why is it taking so long?'}}]
        app = HorizonApp(client)
        async with app.run_test(size=(127, 34)) as pilot:
            app.session_id = 'ses_ui'
            app._show_chat()
            app._set_phase('正在执行', busy=True)
            await app._refresh_context()
            await app._refresh_pending()
            await pilot.pause(.1)
            await app._control_pending('ses_ui', 'later', 'steer')
            await app.handle_event({'type': 'session.next.input.controlled', 'data': {}})
            await pilot.pause(.2)
            view = app.query_one('#transcript')
            first = list(app.query('.user-message'))[0]
            self.assertGreaterEqual(view.scroll_y, 0)
            self.assertLessEqual(first.region.y, view.region.y + 1)
            await pilot.resize_terminal(100, 40)
            await pilot.pause(.1)
            self.assertGreaterEqual(view.scroll_y, 0)
            self.assertLessEqual(first.region.y, view.region.y + 1)

    async def test_tail_follows_later_layout_growth_and_resize(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(90, 30)) as pilot:
            app._show_chat()
            await app._render_context(history())
            await pilot.pause()
            view = app.query_one('#transcript')
            view.scroll_end(animate=False)
            await pilot.pause()
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            self.assertEqual(view.scroll_y, view.max_scroll_y)
            app._update_live_output('\n\n'.join('New paragraph ' + str(i) for i in range(20)))
            await pilot.pause(.15)
            self.assertEqual(view.scroll_y, view.max_scroll_y)

    async def test_reading_history_survives_stream_and_snapshot_rebuild(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            await app._render_context(history())
            await pilot.pause()
            view = app.query_one('#transcript')
            view.scroll_to(y=12, animate=False, force=True)
            await pilot.pause()
            app._update_live_output('**Live response**\n\n' + 'paragraph\n\n' * 20)
            await pilot.pause(.15)
            self.assertEqual(view.scroll_y, 12)
            await app._render_context(history())
            await pilot.pause()
            self.assertEqual(view.scroll_y, 12)
            view.scroll_end(animate=False)
            await pilot.pause()
            app._update_live_output('**Live response**\n\n' + 'paragraph\n\n' * 40)
            await pilot.pause(.15)
            self.assertEqual(view.scroll_y, view.max_scroll_y)

    async def test_stream_formats_before_finish_and_keeps_final_chunk(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            view = app.query_one('#live-output')
            for chunk in ['## Result\n\n', '**bold** text\n\n', '```python\n', 'print(1)\n', '```\n', '\nLast token']:
                await app.handle_event({'type': 'llm.delta', 'data': {'call_id': 'x', 'text': chunk}})
            await pilot.pause(.15)
            self.assertIsInstance(view, Markdown)
            self.assertEqual(len(view.query('MarkdownH2')), 1)
            self.assertEqual(len(view.query('MarkdownFence')), 1)
            self.assertTrue(view.source.endswith('Last token'))
            identity = id(view)
            heading = view.query_one('MarkdownH2')
            await app.handle_event({'type': 'llm.delta', 'data': {'call_id': 'x', 'text': ' preserved'}})
            await pilot.pause(.1)
            self.assertEqual(id(app.query_one('#live-output')), identity)
            self.assertIs(view.query_one('MarkdownH2'), heading)
            self.assertTrue(view.source.endswith('Last token preserved'))

    async def test_wheel_up_pauses_follow_even_during_final_snapshot(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            await app._render_context(history())
            app._update_live_output('Old output\n\n' * 30)
            await pilot.pause(.15)
            view = app.query_one('#transcript')
            self.assertEqual(view.scroll_y, view.max_scroll_y)
            view.post_message(events.MouseScrollUp(view, 2, 2, 0, -1, 0, False, False, False))
            await pilot.pause()
            reading = view.scroll_y
            self.assertLess(reading, view.max_scroll_y)
            app._update_live_output('Old output\n\n' * 30 + 'New output\n\n' * 20)
            await pilot.pause(.15)
            self.assertEqual(view.scroll_y, reading)
            await app._render_context(history())
            await pilot.pause()
            self.assertEqual(view.scroll_y, reading)

    async def test_source_reset_does_not_append_previous_tab_or_turn(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            app._update_live_output('## Previous\n\n**old**')
            await pilot.pause(.1)
            app._update_live_output('')
            app._update_live_output('## New\n\nCurrent reply')
            await pilot.pause(.1)
            live = app.query_one('#live-output', Markdown)
            self.assertEqual(live.source, '## New\n\nCurrent reply')
            self.assertEqual(len(live.query('MarkdownH2')), 1)

    async def test_one_line_up_is_reading_history_not_tail_follow(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            await app._render_context(history())
            await pilot.pause()
            view = app.query_one('#transcript')
            view.scroll_to(y=view.max_scroll_y - 1, animate=False, force=True)
            await pilot.pause()
            reading = view.scroll_y
            await app.handle_event({'type': 'llm.delta', 'data': {'call_id': 'x', 'text': 'A new answer'}})
            await pilot.pause(.15)
            self.assertEqual(view.scroll_y, reading)

            await app._render_context(history())
            await pilot.pause()
            self.assertEqual(view.scroll_y, reading)

    async def test_wheel_during_snapshot_cancels_old_follow_intent(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            await app._render_context(history())
            await pilot.pause()
            view = app.query_one('#transcript')
            entered, release = asyncio.Event(), asyncio.Event()
            populate = app._populate_context

            async def delayed_populate(messages):
                await populate(messages)
                entered.set()
                await release.wait()

            app._populate_context = delayed_populate
            task = asyncio.create_task(app._render_context(history()))
            try:
                await entered.wait()
                view.post_message(events.MouseScrollUp(view, 2, 2, 0, -1, 0, False, False, False))
                await asyncio.sleep(.03)
                reading = view.scroll_y
                self.assertLess(reading, view.max_scroll_y)
            finally:
                release.set()
                await task
            await pilot.pause()
            self.assertEqual(view.scroll_y, reading)
            await app._render_context(history())
            await pilot.pause()
            self.assertEqual(view.scroll_y, reading)


if __name__ == '__main__':
    unittest.main()
