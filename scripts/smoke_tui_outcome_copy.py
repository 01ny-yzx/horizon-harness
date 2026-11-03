"""Internal diagnostics stay in durable data, not duplicated in the chat UI."""
import unittest

from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp


class OutcomeCopy(unittest.IsolatedAsyncioTestCase):
    async def test_failure_and_interrupt_have_only_one_short_status(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 30)) as pilot:
            app._show_chat()
            for status in ('failed', 'interrupted'):
                messages = [
                    {'type': 'user', 'text': '继续'},
                    {'type': 'assistant', 'content': [], 'error': {'message': 'Provider failed'}},
                    {'type': 'synthetic', 'text': '模型服务连续调用失败，任务未执行。'},
                    {'type': 'run_status', 'status': status, 'error': {'message': '模型调用失败，请检查模型配置、连接和服务状态'}},
                ]
                await app._render_context(messages)
                await pilot.pause()
                self.assertEqual(len(app.query('#messages .answer')), 0)
                self.assertEqual(len(app.query('#messages .run-outcome')), 1)
                expected = app.tr('已中断' if status == 'interrupted' else '执行失败')
                self.assertEqual(str(app.query_one('.run-outcome').render()), expected)
                self.assertIn('模型服务', messages[2]['text'])

    async def test_real_answers_and_user_text_are_not_filtered(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 30)) as pilot:
            app._show_chat()
            await app._render_context([
                {'type': 'user', 'text': '解释“模型服务连续调用失败，任务未执行。”'},
                {'type': 'assistant', 'content': [{'type': 'text', 'text': '模型服务连续调用失败，任务未执行。'}]},
                {'type': 'synthetic', 'text': '修改已保存，两个测试未通过。'},
            ])
            await pilot.pause()
            self.assertEqual(len(app.query('#messages .answer')), 2)
            self.assertIn('模型服务', str(app.query_one('.user-message').render()))

    async def test_legacy_diagnostic_without_outcome_keeps_short_failure(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 30)) as pilot:
            app._show_chat()
            await app._render_context([{'type': 'user', 'text': '继续'},
                {'type': 'synthetic', 'text': '模型服务连续调用失败，任务未执行。'}])
            await pilot.pause()
            self.assertEqual(len(app.query('#messages .answer')), 0)
            self.assertEqual(str(app.query_one('.run-outcome').render()), app.tr('执行失败'))


if __name__ == '__main__':
    unittest.main()
