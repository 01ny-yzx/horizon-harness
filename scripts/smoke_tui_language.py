"""UI language preferences and compaction activity, without real credentials."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp, PromptInput


class LanguageUI(unittest.IsolatedAsyncioTestCase):
    async def test_first_use_english_language_command_and_persisted_selection(self):
        from tui.i18n import Preferences
        with TemporaryDirectory() as tmp:
            prefs = Preferences(Path(tmp) / 'ui.json')
            app = HorizonApp(FakeClient(), preferences=prefs)
            async with app.run_test(size=(80, 24)) as pilot:
                self.assertEqual(app.language, 'en')
                self.assertEqual(str(app.query_one('#history').label), 'Sessions')
                await pilot.press('/', 'l')
                await pilot.pause(.05)
                self.assertEqual([row['id'] for row in app._command_rows], ['language'])
                await pilot.press('enter')
                await pilot.pause(.05)
                await pilot.press('down', 'enter')
                await pilot.pause(.05)
                self.assertEqual(app.language, 'zh')
                self.assertEqual(str(app.query_one('#history').label), '会话')
                self.assertEqual(Preferences(prefs.path).language, 'zh')
                await app.action_language()
                await pilot.pause(.05)
                await pilot.press('down', 'enter')
                await pilot.pause(.05)
                self.assertEqual(app.language, 'ja')
                self.assertEqual(str(app.query_one('#history').label), '履歴')
                self.assertEqual(app.query_one(PromptInput).text, '')
                self.assertEqual(app.client.sent, [])
            restarted = HorizonApp(FakeClient(), preferences=Preferences(prefs.path))
            self.assertEqual(restarted.language, 'ja')

    async def test_translated_picker_form_help_and_user_text_are_separate(self):
        from tui.pickers import ConnectionForm
        from tui.i18n import help_text
        from textual.widgets import Label, Static
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(60, 20)) as pilot:
            await app.set_language('ja')
            await app.action_models()
            await pilot.pause(.05)
            self.assertEqual(app.screen.title_text, 'サービスを選択')
            self.assertIn('設定', str(app.screen.query_one('.picker-hint', Static).render()))
            await pilot.press('escape')
            app.push_screen(ConnectionForm(app.client,
                {'id': 'test:model', 'connection_id': 'test', 'model': 'model'},
                {'name': 'Test', 'base_url': 'https://example.invalid', 'configured': True}))
            await pilot.pause(.05)
            self.assertIn('保存済み', str(list(app.screen.query(Label))[-1].render()))
            self.assertLessEqual(app.screen.query_one('#connection-form').region.bottom, 20)
            await pilot.press('escape')
            app._show_chat()
            await app._render_context([{'id': 'u', 'type': 'user', 'text': '你好 hello こんにちは'}])
            self.assertIn('你好 hello こんにちは', str(app.query_one('.user-message').render()))
            self.assertIn('/language', help_text('ja'))
            self.assertIn('言語', help_text('ja'))

    async def test_compaction_activity_follows_goal_and_changes_with_language(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            await app._render_context([{'id': 'u', 'type': 'user', 'text': 'Goal'}])
            await app.handle_event({'type': 'session.next.compaction.started', 'data': {}})
            await pilot.pause(.1)
            activity = app.query_one('#activity')
            self.assertTrue(app.busy)
            self.assertTrue(activity.display)
            self.assertIn('Compacting context', str(activity.render()))
            self.assertGreaterEqual(activity.region.y, app.query_one('.user-message').region.bottom)
            await app.set_language('zh')
            self.assertIn('正在压缩上下文', str(activity.render()))
            await app.set_language('ja')
            self.assertIn('コンテキストを圧縮中', str(activity.render()))
            await app.handle_event({'type': 'session.compaction.failed', 'data': {}})
            self.assertNotEqual(app.phase, '正在压缩上下文')
            self.assertTrue(app.busy)
            await app.handle_event({'type': 'session.next.compaction.started', 'data': {}})
            await app.handle_event({'type': 'session.next.compaction.ended', 'data': {}})
            self.assertNotEqual(app.phase, '正在压缩上下文')
            await app.handle_event({'type': 'run.ended', 'data': {'status': 'interrupted'}})
            self.assertFalse(app.busy)


class CompactionSignals(unittest.TestCase):
    def test_failed_and_empty_summary_emit_terminal_activity_without_summary_text(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from core.session_compaction import SessionCompaction, SessionCompactionSelection
        from core.session_live import LiveJournal
        for response in (RuntimeError('private-error'), SimpleNamespace(message={'content': ''})):
            with self.subTest(response=type(response).__name__):
                journal = LiveJournal()
                events = SimpleNamespace(publish=lambda **kwargs: journal.publish(
                    kwargs['aggregate_id'], kwargs['event_type'], {}))
                compactor = SessionCompaction(object(), events)
                with patch.object(compactor, 'select', return_value=SessionCompactionSelection(('old message',), ())), \
                     patch('core.session_compaction.call_llm_chat_result', **(
                         {'side_effect': response} if isinstance(response, Exception) else {'return_value': response})), \
                     patch('core.session_live.session_live', journal):
                    result = compactor._compact('test', [], reason='auto', limits=SimpleNamespace(
                        requested_output_tokens=1024, effective_input_tokens=100000))
                self.assertFalse(result)
                self.assertEqual([event.type for event in journal.read('test').events],
                                 ['session.next.compaction.started', 'session.compaction.failed'])
                self.assertNotIn('private-error', repr(journal.read('test')))


if __name__ == '__main__':
    unittest.main()
